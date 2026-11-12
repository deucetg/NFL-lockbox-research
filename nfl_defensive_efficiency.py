#!/usr/bin/env python3
"""NFL LOCKBOX Step 9C.1 — audited PBP defensive efficiency (no EPA claims).

Reads existing 2026 SQLite plays, writes standalone advanced_analytics reports.
No source downloads, betting, injury, staging, or database mutations.
"""
import argparse
import csv
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

VERSION = 'step9c_defensive_efficiency_v1'
REQUIRED = {'game_id','play_id','season','season_type','game_date','week','posteam',
            'defteam','qtr','down','yardline_100','pass_attempt','rush_attempt',
            'complete_pass','passing_yards','rushing_yards','pass_touchdown',
            'rush_touchdown','third_down_converted','third_down_failed'}
CATEGORIES = ('pass','rush','combined')
WINDOWS = ('SEASON','L3','L5','L8')


def n(value):
    return float(value or 0)


def ratio(a,b):
    return round(a/b,4) if b else None


def write_csv(path, records):
    with path.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(records[0]))
        writer.writeheader(); writer.writerows(records)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--database',default='NFL_LOCKBOX_2026/nfl_lockbox_2026.sqlite')
    ap.add_argument('--output',default='NFL_LOCKBOX_2026/advanced_analytics')
    args=ap.parse_args()
    db_path=Path(args.database); out=Path(args.output)
    if not db_path.is_file(): ap.error('Database not found: '+str(db_path))
    out.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(f'file:{db_path.resolve()}?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    cols={r['name'] for r in db.execute('PRAGMA table_info(plays)')}
    missing=sorted(REQUIRED-cols)
    if missing: raise SystemExit('Missing plays columns: '+', '.join(missing))

    # One observation per defensive game. Keep zero-play categories in denominators.
    games={}
    totals=defaultdict(lambda:defaultdict(float))
    quality=defaultdict(int)
    seen=set()
    sql='''SELECT * FROM plays WHERE season=2026 AND season_type='REG'
             AND qtr BETWEEN 1 AND 4 AND defteam IS NOT NULL
             ORDER BY game_date,game_id,play_id'''
    for r in db.execute(sql):
        gid=r['game_id']; defense=r['defteam']; offense=r['posteam']
        if not gid or not defense or not offense or defense==offense:
            quality['missing_or_invalid_team_game']+=1; continue
        identity=(gid,str(r['play_id']))
        if identity in seen:
            quality['duplicate_play_ids']+=1; continue
        seen.add(identity)
        date=str(r['game_date'] or '')
        game=(defense,gid)
        if game in games and games[game] != (date,int(r['week'] or 0)):
            raise SystemExit('Conflicting game metadata for '+str(game))
        games[game]=(date,int(r['week'] or 0))
        pa=int(r['pass_attempt'] or 0); ra=int(r['rush_attempt'] or 0)
        if pa and ra:
            quality['both_pass_and_rush']+=1; continue
        if not pa and not ra: continue
        category='pass' if pa else 'rush'
        t=totals[(defense,gid,category)]
        t['attempts']+=1
        yards=r['passing_yards'] if pa else r['rushing_yards']
        # Incomplete passes legitimately have NULL passing_yards in this database.
        incomplete=bool(pa and not int(r['complete_pass'] or 0))
        valid_yards=(yards is not None) or incomplete
        if valid_yards:
            y=n(yards)
            t['yard_known_attempts']+=1
            t['yards']+=y
            if y >= (15 if pa else 10): t['explosive_plays']+=1
        else:
            quality['attempts_missing_yards']+=1
        if pa and int(r['complete_pass'] or 0): t['completions']+=1
        if int(r['down'] or 0)==3:
            converted=int(r['third_down_converted'] or 0)
            failed=int(r['third_down_failed'] or 0)
            if converted+failed==1:
                t['third_down_known']+=1
                t['third_down_converted']+=converted
            else:
                quality['third_down_result_unknown_or_ambiguous']+=1
        if r['yardline_100'] is not None and n(r['yardline_100'])<=20:
            t['red_zone_attempts']+=1
            t['red_zone_touchdowns']+=int(r['pass_touchdown'] or 0) if pa else int(r['rush_touchdown'] or 0)
        if int(r['qtr'])==1:
            t['q1_attempts']+=1
            if valid_yards: t['q1_yards']+=n(yards)

    teams=sorted({d for d,_ in games})
    game_lists=defaultdict(list)
    for (d,g),(date,week) in games.items(): game_lists[d].append((date,week,g))
    for d in teams: game_lists[d].sort()
    rows=[]
    metrics=('attempts','yard_known_attempts','yards','explosive_plays',
             'completions','third_down_known','third_down_converted',
             'red_zone_attempts','red_zone_touchdowns','q1_attempts','q1_yards')
    for d in teams:
        played=game_lists[d]
        for window in WINDOWS:
            sample=played if window=='SEASON' else played[-int(window[1:]):]
            for kind in CATEGORIES:
                agg={k:0.0 for k in metrics}
                for _,_,g in sample:
                    for part in (('pass','rush') if kind=='combined' else (kind,)):
                        source=totals[(d,g,part)]
                        for k in metrics: agg[k]+=source[k]
                attempts=int(agg['attempts']); known=int(agg['yard_known_attempts'])
                complete=bool(attempts and known==attempts)
                row={'defense':d,'window':window,'category':kind,
                     'games_available':len(sample),'last_game_date':sample[-1][0],
                     'attempts_allowed':attempts,'yards_known_attempts':known,
                     'yards_data_complete':complete,
                     'yards_allowed':round(agg['yards'],2) if complete else None,
                     'yards_per_attempt_allowed':ratio(agg['yards'],attempts) if complete else None,
                     'yards_per_game_allowed':round(agg['yards']/len(sample),3) if complete else None,
                     'explosive_plays_allowed':int(agg['explosive_plays']) if complete else None,
                     'explosive_rate_allowed':ratio(agg['explosive_plays'],attempts) if complete else None,
                     'completions_allowed':int(agg['completions']) if kind=='pass' else None,
                     'completion_rate_allowed':ratio(agg['completions'],attempts) if kind=='pass' else None,
                     'third_down_known':int(agg['third_down_known']),
                     'third_down_conversions_allowed':int(agg['third_down_converted']),
                     'third_down_conversion_rate_allowed':ratio(agg['third_down_converted'],agg['third_down_known']),
                     'red_zone_attempts':int(agg['red_zone_attempts']),
                     'red_zone_tds_allowed':int(agg['red_zone_touchdowns']),
                     'q1_attempts_allowed':int(agg['q1_attempts']),
                     'q1_yards_allowed':round(agg['q1_yards'],2) if complete else None,
                     'yards_per_attempt_rank':None,'explosive_rate_rank':None,
                     'epa_per_play':None,'success_rate':None,
                     'coverage_man_pct':None,'coverage_zone_pct':None,
                     'source':'LOCKBOX_2026_PBP',
                     'limitations':'No sacks-adjusted dropback metrics; no EPA or charted coverage'}
                rows.append(row)
    for window in WINDOWS:
        for kind in CATEGORIES:
            subset=[r for r in rows if r['window']==window and r['category']==kind]
            for metric,rank_field in (('yards_per_attempt_allowed','yards_per_attempt_rank'),
                                      ('explosive_rate_allowed','explosive_rate_rank')):
                eligible=sorted((r for r in subset if r[metric] is not None),
                                key=lambda r:(r[metric],r['defense']))
                # Dense ranks; ties share the same rank.
                last=None; rank=0
                for r in eligible:
                    if last is None or r[metric]!=last: rank+=1
                    r[rank_field]=rank; last=r[metric]
    output=out/'defensive_efficiency_2026.csv'
    write_csv(output,rows)
    audit={'version':VERSION,'status':'PASS' if len(teams)==32 and not quality['duplicate_play_ids'] else 'REVIEW',
           'generated_at_utc':datetime.now(timezone.utc).isoformat(),
           'teams':len(teams),'defensive_team_games':len(games),'ranking_rows':len(rows),
           'windows':list(WINDOWS),'categories':list(CATEGORIES),
           'incomplete_yards_rows':sum(not r['yards_data_complete'] for r in rows),
           'quality_flags':dict(quality),
           'epa_available':False,'coverage_charting_available':False,
           'methodology':{
               'yards':'NULL on incomplete passes is interpreted as zero; other missing yards fail completeness',
               'pass':'PBP pass_attempt; sacks and scrambles are not assumed included',
               'rush':'PBP rush_attempt; QB scrambles may be included by source',
               'explosive':'15+ passing yards or 10+ rushing yards on an attempt',
               'third_down':'Only third-down attempts with exactly one converted/failed indicator',
               'red_zone':'yardline_100 <= 20 on pass/rush attempts; touchdowns are play outcomes, not drive conversion',
               'rank':'Lower allowed rates are ranked better; only complete-yardage rows ranked',
               'windows':'Most recent observed defensive team-games, regulation only',
               'excluded':'EPA, success rate, man/zone, sacks-adjusted dropbacks, WR depth rankings'},
           'database_read_only':True,'staging_modified':False,'availability_modified':False}
    audit_path=out/'defensive_efficiency_audit_2026.json'
    audit_path.write_text(json.dumps(audit,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':audit['status'],'teams':len(teams),'games':len(games),
                      'rows':len(rows),'incomplete_yards_rows':audit['incomplete_yards_rows'],
                      'output':str(output),'audit':str(audit_path)},indent=2))
    db.close()
    if audit['status']!='PASS': raise SystemExit('Defensive efficiency audit requires review')

if __name__=='__main__': main()
