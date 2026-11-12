#!/usr/bin/env python3
"""NFL LOCKBOX Step 9D.1: source-verified defensive EPA and success rate.

Read-only join of nflverse PBP EPA to existing LOCKBOX play IDs. Does not
modify SQLite or assert that charted man/zone coverage is available.
"""
import argparse
import csv
import gzip
import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

WINDOWS = {'SEASON': None, 'L3': 3, 'L5': 5, 'L8': 8}
FIELDS = ['defense','window','category','games_in_sample','last_game_date',
          'eligible_plays','epa_observed_plays','epa_coverage_pct','epa_total_allowed',
          'epa_per_play_allowed','epa_rank','success_observed_plays',
          'offensive_success_rate_allowed','success_rate_rank','data_status',
          'source','methodology']

def finite(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (ValueError,TypeError):
        return None

def flag(v):
    if v is None or str(v).strip().lower() in ('','na','nan','null'):
        return None
    x=finite(v)
    return int(x) if x in (0.0,1.0) else None

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--pbp',default='play_by_play_2026.csv.gz')
    p.add_argument('--database',default='NFL_LOCKBOX_2026/nfl_lockbox_2026.sqlite')
    p.add_argument('--output',default='NFL_LOCKBOX_2026/advanced_analytics')
    p.add_argument('--min-coverage',type=float,default=0.95)
    a=p.parse_args()
    src=Path(a.pbp); dbfile=Path(a.database); out=Path(a.output)
    if not src.is_file() or not dbfile.is_file():
        p.error('Both the refreshed nflverse PBP and LOCKBOX SQLite must exist')
    if not 0<a.min_coverage<=1: p.error('--min-coverage must be within (0,1]')
    out.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(f'file:{dbfile.resolve()}?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    known={}
    game_meta={}
    for r in db.execute('''SELECT game_id,play_id,game_date,week,defteam,posteam,
                          season_type,qtr,pass_attempt,rush_attempt FROM plays
                          WHERE season=2026 AND season_type='REG' AND qtr BETWEEN 1 AND 4'''):
        gid=str(r['game_id']); pid=str(r['play_id']); d=r['defteam']; off=r['posteam']
        if not gid or not pid or not d or not off or d==off: continue
        k=(gid,pid)
        if k in known: raise SystemExit('Duplicate LOCKBOX play key: '+str(k))
        kind='pass' if r['pass_attempt']==1 and r['rush_attempt']!=1 else ('rush' if r['rush_attempt']==1 and r['pass_attempt']!=1 else None)
        known[k]=(d,off,kind)
        gm=(d,gid)
        meta=(str(r['game_date'] or ''),int(r['week'] or 0))
        if gm in game_meta and game_meta[gm]!=meta: raise SystemExit('Conflicting game metadata: '+str(gm))
        game_meta[gm]=meta
    if not known: raise SystemExit('No 2026 regular-season LOCKBOX plays')
    data=defaultdict(lambda:{'eligible':0,'epa_count':0,'epa_sum':0.,'success_count':0,'success_sum':0})
    quality=defaultdict(int)
    matched=set(); raw_seen=set()
    opener=gzip.open if src.suffix=='.gz' else open
    with opener(src,'rt',encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        needed={'game_id','play_id','season','defteam','posteam','epa','success','pass_attempt','rush_attempt'}
        missing=needed-set(reader.fieldnames or [])
        if missing: raise SystemExit('Source missing required columns: '+', '.join(sorted(missing)))
        for r in reader:
            if str(r.get('season'))!='2026': continue
            k=(str(r.get('game_id') or ''),str(r.get('play_id') or ''))
            if k in raw_seen:
                quality['duplicate_source_keys']+=1
                continue
            raw_seen.add(k)
            if k not in known: continue
            d,off,kind=known[k]
            if r.get('defteam')!=d or r.get('posteam')!=off:
                quality['team_mismatch']+=1
                continue
            matched.add(k)
            if kind is None: continue
            if flag(r.get('pass_attempt'))!=int(kind=='pass') or flag(r.get('rush_attempt'))!=int(kind=='rush'):
                quality['attempt_type_mismatch']+=1
                continue
            ep=finite(r.get('epa')); su=flag(r.get('success'))
            t=data[(d,k[0],kind)]
            t['eligible']+=1
            if ep is not None: t['epa_count']+=1; t['epa_sum']+=ep
            if su is not None: t['success_count']+=1; t['success_sum']+=su
    quality['lockbox_plays_unmatched']=len(known-matched)
    game_lists=defaultdict(list)
    for (d,g),(date,week) in game_meta.items(): game_lists[d].append((date,week,g))
    for games in game_lists.values(): games.sort()
    rows=[]
    for d,games in sorted(game_lists.items()):
        for win,n in WINDOWS.items():
            selected=games if n is None else games[-n:]
            for kind in ('pass','rush','combined'):
                sums={key:0 for key in ('eligible','epa_count','epa_sum','success_count','success_sum')}
                for _,_,gid in selected:
                    for part in (('pass','rush') if kind=='combined' else (kind,)):
                        t=data[(d,gid,part)]
                        for key in sums: sums[key]+=t[key]
                eligible=sums['eligible']; ec=sums['epa_count']; sc=sums['success_count']
                ecover=ec/eligible if eligible else 0
                scover=sc/eligible if eligible else 0
                valid_e=eligible>0 and ecover>=a.min_coverage
                valid_s=eligible>0 and scover>=a.min_coverage
                rows.append(dict(defense=d,window=win,category=kind,
                    games_in_sample=len(selected),last_game_date=selected[-1][0],
                    eligible_plays=eligible,epa_observed_plays=ec,
                    epa_coverage_pct=round(100*ecover,2),
                    epa_total_allowed=round(sums['epa_sum'],4) if valid_e else None,
                    epa_per_play_allowed=round(sums['epa_sum']/ec,5) if valid_e else None,
                    epa_rank=None,success_observed_plays=sc,
                    offensive_success_rate_allowed=round(sums['success_sum']/sc,5) if valid_s else None,
                    success_rate_rank=None,
                    data_status='COMPLETE' if valid_e and valid_s else 'INSUFFICIENT_COVERAGE',
                    source='nflverse_pbp_2026_joined_to_LOCKBOX_play_ids',
                    methodology='Lower offensive EPA and offensive success rate allowed is better; pass_attempt/rush_attempt plays only; regulation'))
    for win in WINDOWS:
        for kind in ('pass','rush','combined'):
            subset=[r for r in rows if r['window']==win and r['category']==kind]
            for metric,rank in (('epa_per_play_allowed','epa_rank'),('offensive_success_rate_allowed','success_rate_rank')):
                ordered=sorted((r for r in subset if r[metric] is not None),key=lambda r:(r[metric],r['defense']))
                last=None; pos=0
                for r in ordered:
                    if last is None or r[metric]!=last: pos+=1
                    r[rank]=pos; last=r[metric]
    dest=out/'defensive_epa_2026.csv'
    with dest.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
    sha=hashlib.sha256()
    with src.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): sha.update(chunk)
    audit={'version':'step9d_defensive_epa_v1','created_utc':datetime.now(timezone.utc).isoformat(),
           'source_file':str(src),'source_sha256':sha.hexdigest(),
           'database':str(dbfile),'teams':len(game_lists),'team_games':len(game_meta),
           'rows':len(rows),'min_coverage':a.min_coverage,'quality':dict(quality),
           'incomplete_rows':sum(r['data_status']!='COMPLETE' for r in rows),
           'status':'PASS' if len(game_lists)==32 and not quality['duplicate_source_keys'] and not quality['team_mismatch'] and not quality['attempt_type_mismatch'] else 'REVIEW',
           'limitations':['Not opponent-adjusted','EPA is nflverse offensive EPA; defensive value is EPA allowed','Success is nflverse offensive success indicator','Sacks are excluded unless source marks them as pass_attempt','No man/zone charting','Coverage threshold suppresses incomplete metrics','Only PBP plays matching existing LOCKBOX game_id and play_id are used']}
    (out/'defensive_epa_audit_2026.json').write_text(json.dumps(audit,indent=2)+'\n')
    print(json.dumps({'status':audit['status'],'teams':audit['teams'],'rows':len(rows),'incomplete_rows':audit['incomplete_rows'],'quality':dict(quality)},indent=2))
    db.close()
    if audit['status']!='PASS': raise SystemExit('Defensive EPA source reconciliation needs review')

if __name__=='__main__': main()
