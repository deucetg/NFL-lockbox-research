#!/usr/bin/env python3
"""NFL LOCKBOX advanced analytics, phase 1: regulation-aware player opportunities.
Read-only SQLite input. Produces audited L3/L5/L8 receiving/rushing windows and
quarter breakdowns. No snap/route or injury assumptions.
"""
import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'NFL_LOCKBOX_2026'
DB = ROOT / 'nfl_lockbox_2026.sqlite'
OUT = ROOT / 'advanced_analytics'
WINDOWS = (3, 5, 8)

def number(x):
    return float(x or 0)

def write_csv(path, columns, rows):
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)

def main():
    if not DB.exists():
        raise FileNotFoundError(f'Expected existing database at {DB}')
    OUT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    required = {'game_id','play_id','game_date','season','week','posteam','qtr',
                'pass_attempt','receiver_id','receiver_name','complete_pass',
                'receiving_yards','rush_attempt','rusher_id','rusher_name','rushing_yards'}
    actual = {r['name'] for r in conn.execute('PRAGMA table_info(plays)')}
    if required - actual:
        raise ValueError(f'Missing plays columns: {sorted(required-actual)}')
    games = defaultdict(dict)
    for r in conn.execute('''SELECT DISTINCT game_id,game_date,week,posteam FROM plays
                             WHERE season=2026 AND posteam IS NOT NULL AND posteam!='' '''):
        games[r['posteam']][r['game_id']] = (r['game_date'] or '', int(r['week'] or 0))
    team_windows = {}
    for team, g in games.items():
        ordered = sorted(g, key=lambda x: (g[x][0],g[x][1],x), reverse=True)
        team_windows[team] = {w:set(ordered[:w]) for w in WINDOWS}
    player = defaultdict(lambda: defaultdict(lambda: {'targets':0,'receptions':0,'receiving_yards':0.,'rush_attempts':0,'rushing_yards':0.}))
    names = {}
    quarter_rows = defaultdict(lambda: {'targets':0,'receptions':0,'receiving_yards':0.,'rush_attempts':0,'rushing_yards':0.})
    unmatched_passes = 0
    total_passes = 0
    for r in conn.execute('''SELECT game_id,posteam,qtr,pass_attempt,receiver_id,receiver_name,
                                    complete_pass,receiving_yards,rush_attempt,rusher_id,rusher_name,rushing_yards
                             FROM plays WHERE season=2026'''):
        game, team = r['game_id'],r['posteam']
        if not team or team not in team_windows:
            continue
        q = int(r['qtr'] or 0)
        if r['pass_attempt']==1:
            total_passes += 1
            if not r['receiver_id']:
                unmatched_passes += 1
        if r['pass_attempt']==1 and r['receiver_id']:
            pid = str(r['receiver_id'])
            names[(team,pid)] = r['receiver_name'] or names.get((team,pid),pid)
            p = player[(team,pid)][game]
            s = quarter_rows[(team,pid,game,q)]
            for item in (p,s):
                item['targets']+=1
                item['receptions']+=int(r['complete_pass'] or 0)
                item['receiving_yards']+=number(r['receiving_yards'])
        if r['rush_attempt']==1 and r['rusher_id']:
            pid = str(r['rusher_id'])
            names[(team,pid)] = r['rusher_name'] or names.get((team,pid),pid)
            p = player[(team,pid)][game]
            s = quarter_rows[(team,pid,game,q)]
            for item in (p,s):
                item['rush_attempts']+=1
                item['rushing_yards']+=number(r['rushing_yards'])
    metrics = ['targets','receptions','receiving_yards','rush_attempts','rushing_yards']
    window_rows = []
    for (team,pid), pgames in sorted(player.items()):
        for w in WINDOWS:
            sample = team_windows[team][w]
            reg = {m:0 for m in metrics}
            ot = {m:0 for m in metrics}
            observed = set(pgames)&sample
            for (t,p,g,q),vals in quarter_rows.items():
                if t!=team or p!=pid or g not in sample:
                    continue
                bucket = reg if 1<=q<=4 else ot if q>=5 else None
                if bucket is not None:
                    for m in metrics:
                        bucket[m]+=vals[m]
            row={'team':team,'player_id':pid,'player_name':names[(team,pid)],'window':f'L{w}',
                 'team_games_available':len(sample),'player_games_with_identified_opportunities':len(observed),
                 'missing_player_game_status':'UNKNOWN_NOT_ZERO' if len(observed)<len(sample) else 'ALL_OBSERVED'}
            for m in metrics:
                row[f'regulation_{m}']=round(reg[m],2)
                row[f'overtime_{m}']=round(ot[m],2)
                row[f'full_game_{m}']=round(reg[m]+ot[m],2)
            window_rows.append(row)
    qrows=[]
    for (team,pid,game,q),vals in sorted(quarter_rows.items()):
        qrows.append({'team':team,'player_id':pid,'player_name':names[(team,pid)],
                      'game_id':game,'quarter':q,'period_type':'OT' if q>=5 else 'REG' if q>=1 else 'UNKNOWN',
                      **{k:round(v,2) for k,v in vals.items()}})
    if not window_rows or not qrows:
        raise ValueError('No player opportunities found; refusing empty exports')
    write_csv(OUT/'player_regulation_windows_2026.csv',list(window_rows[0]),window_rows)
    write_csv(OUT/'player_quarter_usage_2026.csv',list(qrows[0]),qrows)
    audit={'season':2026,'source_database':str(DB),'player_window_rows':len(window_rows),
           'player_quarter_rows':len(qrows),'pass_attempts':total_passes,
           'pass_attempts_without_receiver_id':unmatched_passes,
           'limitations':['No inferred zero opportunities for missing player-game rows',
                          'Unidentified pass targets are excluded from player totals',
                          'Quarter >=5 treated as overtime; quarter 0 excluded',
                          'Team rolling windows include games with recorded offensive plays; not a completeness guarantee',
                          'Player games with opportunities are not equivalent to player appearances']}
    (OUT/'advanced_phase1_audit.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    print(json.dumps(audit,indent=2))
    conn.close()

if __name__=='__main__':
    main()
