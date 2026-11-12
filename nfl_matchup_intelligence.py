#!/usr/bin/env python3
"""NFL LOCKBOX Phase 4: odds-blind offensive-vs-defensive research matrix.

All pairings are hypothetical, NOT a game schedule. Uses only existing LOCKBOX
reports and player identity records. Does not estimate hit probability or odds.
"""
import argparse
import csv
import json
from collections import defaultdict, Counter
from pathlib import Path

WINDOWS = ('L3', 'L5', 'L8')
KINDS = ('receiving', 'rushing')
POSITION_MAP = {'WR':'WR','RB':'RB','HB':'RB','FB':'RB','TE':'TE','QB':'QB'}


def rows(path):
    with path.open(newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def num(v):
    try: return float(v or 0)
    except (ValueError, TypeError): return 0.0


def integer(v):
    return int(num(v))


def fmt(v):
    return round(v, 3)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', default='NFL_LOCKBOX_2026')
    args = p.parse_args()
    root = Path(args.root)
    adv = root / 'advanced_analytics'
    required = [root/'player_identity_audit.csv', root/'nfl_lockbox_2026.sqlite', adv/'player_regulation_windows_2026.csv', adv/'player_explosive_windows_2026.csv', adv/'defensive_position_windows_2026.csv', adv/'player_quarter_usage_2026.csv']
    missing = [str(f) for f in required if not f.is_file()]
    if missing: raise SystemExit('Missing required files: '+', '.join(missing))

    import sqlite3
    conn = sqlite3.connect(str(root/'nfl_lockbox_2026.sqlite'))
    positions = defaultdict(set)
    # Identity file supplies the validated GSIS-to-PFR mapping; snap counts supply positions.
    identities = rows(root/'player_identity_audit.csv')
    snap_position = defaultdict(set)
    for game, team, pfr, position in conn.execute('SELECT game_id, team, pfr_player_id, position FROM snap_counts'):
        if pfr and position: snap_position[(game,team,pfr)].add(position.upper())
    for r in identities:
        gsis = r.get('gsis_id')
        if not gsis: continue
        for pos in snap_position.get((r['game_id'],r['team'],r['pfr_player_id']), ()):
            positions[(r['team'],gsis)].add(POSITION_MAP.get(pos,'OTHER'))
    player_pos = {k: next(iter(v)) for k,v in positions.items() if len(v)==1}
    ambiguous = sum(len(v)>1 for v in positions.values())

    offense = rows(adv/'player_regulation_windows_2026.csv')
    defense = rows(adv/'defensive_position_windows_2026.csv')
    explosive = rows(adv/'player_explosive_windows_2026.csv')
    quarter = rows(adv/'player_quarter_usage_2026.csv')
    def_idx = {(r['defense'],r['window'],r['position'],r['kind']):r for r in defense if r['period']=='regulation'}
    exp_idx = {(r['team'],r['player_id'],r['window'],r['kind']):r for r in explosive if r['period']=='regulation'}
    q1 = defaultdict(lambda: {'targets':0.,'rush_attempts':0.,'receiving_yards':0.,'rushing_yards':0.,'games':set()})
    for r in quarter:
        if r['period_type']!='REG' or integer(r['quarter'])!=1: continue
        key=(r['team'],r['player_id']); q1[key]['games'].add(r['game_id'])
        for col in ('targets','rush_attempts','receiving_yards','rushing_yards'):
            q1[key][col]+=num(r[col])

    defenses = sorted({r['defense'] for r in defense})
    out=[]; omitted=Counter(); candidates=0
    for r in offense:
        team,pid,window=r['team'],r['player_id'],r['window']
        if window not in WINDOWS: continue
        pos=player_pos.get((team,pid))
        if not pos or pos=='OTHER': omitted['unmapped_or_other_position']+=1; continue
        team_games=integer(r['team_games_available'])
        player_games=integer(r['player_games_with_identified_opportunities'])
        if not team_games: omitted['no_team_games']+=1; continue
        for kind in KINDS:
            opp=num(r['regulation_targets' if kind=='receiving' else 'regulation_rush_attempts'])
            yards=num(r['regulation_receiving_yards' if kind=='receiving' else 'regulation_rushing_yards'])
            if opp<3: omitted['fewer_than_3_opportunities']+=1; continue
            candidates+=1
            exp=exp_idx.get((team,pid,window,kind),{})
            early=q1[(team,pid)]
            for d in defenses:
                if d==team: continue
                dr=def_idx.get((d,window,pos,kind))
                if not dr: omitted['missing_defensive_position_window']+=1; continue
                dg=integer(dr['games_available'])
                if not dg: continue
                def_opp=num(dr['opportunities'])
                # Descriptive opportunity and efficiency contrasts, NOT projections.
                result={
                    'offense':team,'defense':d,'scheduled_matchup':'NO_HYPOTHETICAL_PAIRING',
                    'player_id':pid,'player_name':r['player_name'],'position':pos,'kind':kind,'window':window,
                    'offense_team_games':team_games,'offense_player_opportunity_games':player_games,
                    'offense_opportunities':fmt(opp),'offense_opportunities_per_team_game':fmt(opp/team_games),
                    'offense_yards':fmt(yards),'offense_yards_per_team_game':fmt(yards/team_games),
                    'offense_yards_per_opportunity':fmt(yards/opp),
                    'defense_games':dg,'defense_position_opportunity_games':integer(dr['games_with_position_opportunities']),
                    'defense_position_opportunities_allowed':fmt(def_opp),
                    'defense_position_opportunities_allowed_per_game':fmt(def_opp/dg),
                    'defense_position_yards_allowed_per_game':fmt(num(dr['yards'])/dg),
                    'defense_position_yards_per_opportunity':fmt(num(dr['yards'])/def_opp) if def_opp else '',
                    'defense_position_q1_opportunities_per_game':fmt(num(dr['q1_opportunities'])/dg),
                    'defense_position_red_zone_opportunities_per_game':fmt(num(dr['red_zone_opportunities'])/dg),
                    'defense_position_third_down_opportunities_per_game':fmt(num(dr['third_down_opportunities'])/dg),
                    'player_explosive_positive_yard_share':exp.get('explosive_positive_yard_share',''),
                    'player_largest_play_positive_yard_share':exp.get('largest_play_positive_yard_share',''),
                    'explosive_window_observed_games':exp.get('observed_games',''),
                    'q1_player_opportunities_all_observed_games':fmt(early['targets' if kind=='receiving' else 'rush_attempts']),
                    'q1_player_observed_games_all_season':len(early['games']),
                    'offense_sample_complete':int(team_games==int(window[1:])),
                    'defense_sample_complete':int(dg==int(window[1:])),
                    'player_games_status_unknown':int(player_games<team_games),
                    'defense_unclassified_or_unattributed_excluded':1,
                }
                out.append(result)
    adv.mkdir(parents=True,exist_ok=True)
    path=adv/'player_defense_matchup_matrix_2026.csv'
    if not out: raise SystemExit('No matchup rows produced; verify source report schema')
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(out[0]));w.writeheader();w.writerows(out)
    audit={'season':2026,'pairings':'HYPOTHETICAL_ALL_DEFENSES_NOT_ACTUAL_SCHEDULE',
           'matrix_rows':len(out),'offensive_player_kind_windows_eligible':candidates,
           'distinct_players':len({(r['offense'],r['player_id']) for r in out}),
           'defenses':len(defenses),'identity_keys_unique_supported_position':len(player_pos),
           'identity_keys_ambiguous_position':ambiguous,'omitted':dict(omitted),
           'limitations':['No upcoming schedule or injury information is included',
                          'Offensive player games without recorded opportunities are unknown, not zeros',
                          'Defensive position stats omit unidentified receiver attempts and unclassified players',
                          'Explosive rolling windows count observed player opportunity games and may differ from team windows',
                          'Q1 player fields cover all observed games, not the L3/L5/L8 window',
                          'Metrics are descriptive and not prop projections or betting recommendations']}
    (adv/'matchup_intelligence_audit_2026.json').write_text(json.dumps(audit,indent=2)+'\n')
    print(json.dumps(audit,indent=2))

if __name__=='__main__': main()
