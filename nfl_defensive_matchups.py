#!/usr/bin/env python3
"""NFL LOCKBOX Phase 3: verified defensive positional allowances.

Reads the existing LOCKBOX SQLite database and identity audit. No outside stats.
Outputs defensive game, rolling-window and classification-audit reports.
"""
import argparse
import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

FIELDS = ('opportunities', 'completions', 'yards', 'explosive_plays', 'explosive_yards',
          'q1_opportunities', 'q1_yards', 'third_down_opportunities',
          'red_zone_opportunities', 'goal_to_go_opportunities', 'touchdowns')
POSITIONS = {'WR', 'TE', 'RB', 'QB', 'FB'}


def as_int(value):
    return int(value or 0)


def as_float(value):
    return float(value or 0)


def read_positions(db, identity_file):
    """Only join same-game, same-team GSIS identities to snap-count positions."""
    snaps = defaultdict(set)
    for game, team, pfr, position in db.execute(
        'SELECT game_id, team, pfr_player_id, position FROM snap_counts'
    ):
        if game and team and pfr and position:
            snaps[(game, team, pfr)].add(position.upper().strip())
    mapped = defaultdict(set)
    with identity_file.open(newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            gsis = row.get('gsis_id', '').strip()
            if not gsis:
                continue
            key = (row['game_id'], row['team'], row['pfr_player_id'])
            for pos in snaps.get(key, ()):
                mapped[(row['game_id'], row['team'], gsis)].add(pos)
    result = {}
    for key, values in mapped.items():
        # Multiple positions or unknown position => unclassified, not guessed.
        if len(values) == 1:
            value = next(iter(values))
            if value in POSITIONS:
                result[key] = value
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--database', default='NFL_LOCKBOX_2026/nfl_lockbox_2026.sqlite')
    ap.add_argument('--identity', default='NFL_LOCKBOX_2026/player_identity_audit.csv')
    ap.add_argument('--output', default='NFL_LOCKBOX_2026/advanced_analytics')
    args = ap.parse_args()
    database, identity, out = Path(args.database), Path(args.identity), Path(args.output)
    if not database.exists():
        ap.error(f'Missing database: {database}')
    if not identity.exists():
        ap.error(f'Missing identity audit: {identity}')
    out.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(database))
    db.row_factory = sqlite3.Row
    positions = read_positions(db, identity)
    # Key: game, date, week, defense, opposing offense, position, kind, period.
    aggregates = defaultdict(lambda: defaultdict(float))
    coverage = defaultdict(lambda: defaultdict(float))
    games = {}
    sql = '''SELECT game_id, game_date, week, posteam, defteam, qtr, pass_attempt,
                    complete_pass, receiver_id, receiving_yards, pass_touchdown,
                    rush_attempt, rusher_id, rushing_yards, rush_touchdown,
                    down, yardline_100, goal_to_go
             FROM plays WHERE season=2026 AND defteam IS NOT NULL
             ORDER BY game_date, game_id, play_id'''
    for p in db.execute(sql):
        game, date, week, offense, defense = (p[k] for k in ('game_id','game_date','week','posteam','defteam'))
        if not game or not date or not offense or not defense:
            continue
        games[(game, defense)] = (date, week)
        period = 'regulation' if as_int(p['qtr']) <= 4 else 'overtime'
        for kind, flag, player_field, yards_field, td_field in (
            ('receiving', 'pass_attempt', 'receiver_id', 'receiving_yards', 'pass_touchdown'),
            ('rushing', 'rush_attempt', 'rusher_id', 'rushing_yards', 'rush_touchdown')
        ):
            if not as_int(p[flag]):
                continue
            player = p[player_field]
            pos = positions.get((game, offense, player), 'UNCLASSIFIED') if player else 'UNCLASSIFIED'
            y = as_float(p[yards_field])
            completed = as_int(p['complete_pass']) if kind == 'receiving' else 0
            # A receiving attempt with no receiver ID is not a confirmed target.
            # Keep it in coverage only, not in position-based opportunity counts.
            if kind == 'receiving' and not player:
                coverage[(kind, period)]['unattributed_attempts'] += 1
                continue
            coverage[(kind, period)]['opportunities'] += 1
            if pos == 'UNCLASSIFIED':
                coverage[(kind, period)]['unclassified_opportunities'] += 1
                coverage[(kind, period)]['unclassified_yards'] += y
            else:
                coverage[(kind, period)]['classified_opportunities'] += 1
                coverage[(kind, period)]['classified_yards'] += y
            key = (game, date, week, defense, offense, pos, kind, period)
            a = aggregates[key]
            a['opportunities'] += 1
            a['completions'] += completed
            a['yards'] += y
            threshold = 15 if kind == 'receiving' else 10
            if y >= threshold:
                a['explosive_plays'] += 1
                a['explosive_yards'] += y
            if as_int(p['qtr']) == 1:
                a['q1_opportunities'] += 1
                a['q1_yards'] += y
            if as_int(p['down']) == 3:
                a['third_down_opportunities'] += 1
            if p['yardline_100'] is not None and as_float(p['yardline_100']) <= 20:
                a['red_zone_opportunities'] += 1
            if as_int(p['goal_to_go']):
                a['goal_to_go_opportunities'] += 1
            a['touchdowns'] += as_int(p[td_field])

    game_rows = []
    for key, a in sorted(aggregates.items()):
        row = dict(zip(('game_id','game_date','week','defense','offense','position','kind','period'), key))
        row.update({f: round(a[f], 3) for f in FIELDS})
        game_rows.append(row)
    game_cols = ['game_id','game_date','week','defense','offense','position','kind','period',*FIELDS]
    write_csv(out/'defensive_position_game_2026.csv', game_cols, game_rows)

    # L3/L5/L8 = last N *observed defensive team games*, including games with zero
    # opportunities for a position. Do not mistake them for N qualifying games.
    team_games = defaultdict(list)
    for (game, defense), (date, week) in games.items():
        team_games[defense].append((date, week, game))
    by_game = {(r['game_id'],r['defense'],r['position'],r['kind'],r['period']):r for r in game_rows}
    window_rows = []
    for defense, played in sorted(team_games.items()):
        played.sort()
        for n in (3,5,8):
            recent = played[-n:]
            for period in ('regulation','overtime'):
                for kind in ('receiving','rushing'):
                    for pos in ('WR','TE','RB','QB','FB','UNCLASSIFIED'):
                        items = [by_game.get((game,defense,pos,kind,period)) for _,_,game in recent]
                        sums = {f:round(sum(float(x[f]) for x in items if x),3) for f in FIELDS}
                        row = {'defense':defense,'window':f'L{n}', 'games_available':len(recent),
                               'games_with_position_opportunities':sum(bool(x) for x in items),
                               'position':pos,'kind':kind,'period':period,
                               'last_game_date':recent[-1][0]}
                        row.update(sums)
                        row['yards_per_game'] = round(sums['yards']/len(recent),3)
                        row['yards_per_opportunity'] = round(sums['yards']/sums['opportunities'],3) if sums['opportunities'] else None
                        window_rows.append(row)
    window_cols = ['defense','window','games_available','games_with_position_opportunities',
                   'position','kind','period','last_game_date',*FIELDS,'yards_per_game','yards_per_opportunity']
    write_csv(out/'defensive_position_windows_2026.csv',window_cols,window_rows)
    audit = {'season':2026,'games':len(set(g for g,_ in games)),
             'defensive_team_games':len(games),'identity_keys_with_unique_supported_position':len(positions),
             'coverage':{},'definitions':{
                'receiving_opportunity':'pass attempt with a nonempty receiver ID (not all pass attempts)',
                'rushing_opportunity':'rush attempt with rusher ID, or unclassified if missing',
                'red_zone':'yardline_100 <= 20; source data may omit yardline',
                'explosive_receiving_yards':15,'explosive_rushing_yards':10,
                'rolling_windows':'last 3, 5 or 8 observed defensive team games',
                'position':'unique same-game same-team GSIS-to-PFR-to-snap position; ambiguous or missing = UNCLASSIFIED',
                'quality':'Unclassified yards/opportunities must not be interpreted as zero allowances to known positions.'}}
    for (kind,period), counts in sorted(coverage.items()):
        d = dict(counts)
        total = d.get('opportunities',0)
        d['classified_opportunity_rate'] = round(d.get('classified_opportunities',0)/total,4) if total else None
        audit['coverage'][f'{kind}_{period}'] = d
    (out/'defensive_position_audit_2026.json').write_text(json.dumps(audit,indent=2)+'\n')
    print(f'Wrote {len(game_rows)} game rows, {len(window_rows)} rolling-window rows to {out}')
    print(json.dumps(audit['coverage'],indent=2))
    db.close()


def write_csv(path, columns, rows):
    with path.open('w',newline='',encoding='utf-8') as f:
        w = csv.DictWriter(f,fieldnames=columns)
        w.writeheader()
        w.writerows(rows)


if __name__ == '__main__':
    main()
