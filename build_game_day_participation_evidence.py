#!/usr/bin/env python3
"""Read-only evidence audit for player-games without observed offensive events.
Never infer ACTIVE/INACTIVE or zero opportunities from missing snap rows.
"""
import csv
import json
import sqlite3
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE / 'NFL_LOCKBOX_2026'
PART = ROOT / 'participation_audit/player_game_participation_reconciled.csv'
STAGING = ROOT / 'source_data/official_inactives_staging_2026.csv'
DB = ROOT / 'nfl_lockbox_2026.sqlite'
OUT = ROOT / 'advanced_analytics'
OUT.mkdir(parents=True, exist_ok=True)

def read_csv(path):
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open(newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))

participation = read_csv(PART)
staging = read_csv(STAGING)
if not DB.exists():
    raise FileNotFoundError(DB)
with sqlite3.connect(DB) as conn:
    snap_rows = conn.execute('SELECT game_id, team, pfr_player_id, offense_snaps FROM snap_counts').fetchall()
    try:
        identity_rows = conn.execute('SELECT game_id, team, gsis_id, pfr_player_id FROM player_identity_matches').fetchall()
    except sqlite3.OperationalError:
        identity_rows = []

snap_index = {(str(g),str(t),str(p)): n for g,t,p,n in snap_rows}
# Staging can only be used as a pointer for manual review: no authenticated
# official inactive evidence is inferred from its presence or absence.
staging_games = {(r.get('game_id',''),r.get('team','')) for r in staging}
result = []
for r in participation:
    game,team,pid = r['game_id'],r['team'],r['player_id']
    original = r.get('reconciled_status','')
    snap = r.get('offense_snaps','')
    has_event = r.get('offensive_event_evidence','') in ('1','1.0','True','true')
    if has_event:
        evidence = 'OFFENSIVE_EVENT_OBSERVED'
    elif snap not in ('',None):
        try:
            n = float(snap)
            evidence = 'POSITIVE_OFFENSIVE_SNAPS' if n > 0 else 'ZERO_OFFENSIVE_SNAPS_NOT_INACTIVE_PROOF'
        except ValueError:
            evidence = 'INVALID_SNAP_VALUE_REVIEW'
    else:
        evidence = 'NO_MATCHED_OFFENSIVE_SNAP_EVIDENCE'
    result.append({
        'game_id':game,'team':team,'player_id':pid,'player_name':r.get('player_name',''),
        'original_reconciled_status':original,'offense_snaps':snap,
        'participation_evidence':evidence,
        'official_inactive_verified':'0',
        'official_active_verified':'0',
        'official_staging_team_game_present':str((game,team) in staging_games).lower(),
        'availability_conclusion':'UNVERIFIED_OFFICIAL_GAME_DAY_STATUS',
        'zero_targets_confirmed':'0',
    })

fields=list(result[0]) if result else []
output=OUT/'game_day_participation_evidence_2026.csv'
with output.open('w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(result)
counts=dict(Counter(r['participation_evidence'] for r in result))
audit={
    'status':'PASS' if len(result)==len(participation) else 'FAIL',
    'source_player_game_rows':len(participation),'output_rows':len(result),
    'snap_source_rows':len(snap_rows),'official_staging_rows':len(staging),
    'evidence_counts':counts,
    'limitations':[
        'Official game-day inactive status has not been authenticated by this script.',
        'Weekly roster status is not proof of game-day active status.',
        'Missing snap-count match is not proof of inactivity.',
        'Zero offensive snaps is not proof of inactivity.',
        'No offensive targets or other opportunities are inferred as zero.',
        'No research rankings or source records are modified.'
    ]
}
(OUT/'game_day_participation_evidence_audit_2026.json').write_text(json.dumps(audit,indent=2)+'\n')
print(json.dumps(audit,indent=2))
if audit['status']!='PASS':
    raise SystemExit(1)
