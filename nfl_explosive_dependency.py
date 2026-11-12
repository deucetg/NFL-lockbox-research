#!/usr/bin/env python3
"""NFL LOCKBOX Phase 2: explosive play dependency (2026 PBP, research only).

Uses only the existing SQLite plays table. Standard library only.
Outputs CSVs and a JSON audit under NFL_LOCKBOX_2026/advanced_analytics.
"""
import argparse
import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

SEASON = 2026
THRESHOLDS = {'receiving': 15.0, 'rushing': 10.0}


def safe_float(value):
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


def metrics(plays, kind):
    """One player, one game or a rolling set of games, one play kind."""
    values = [p['yards'] for p in plays]
    positive = sum(max(0, y) for y in values)
    net = sum(values)
    largest = max(values, default=0.0)
    threshold = THRESHOLDS[kind]
    explosive = [y for y in values if y >= threshold]
    explosive_yards = sum(explosive)
    # Positive-yard denominator is deliberate: negative rushes otherwise produce
    # nonsensical >100% explosive shares. Net yards are separately preserved.
    return {
        'opportunities': len(plays),
        'net_yards': round(net, 2),
        'positive_yards': round(positive, 2),
        'explosive_plays': len(explosive),
        'explosive_yards': round(explosive_yards, 2),
        'explosive_play_rate': round(len(explosive) / len(plays), 4) if plays else None,
        'explosive_positive_yard_share': round(explosive_yards / positive, 4) if positive else None,
        'largest_play_yards': round(largest, 2),
        'largest_play_positive_yard_share': round(max(largest, 0) / positive, 4) if positive else None,
        'net_yards_without_largest_play': round(net - largest, 2) if plays else None,
        'games_with_opportunities': len({p['game_id'] for p in plays}),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='NFL_LOCKBOX_2026/nfl_lockbox_2026.sqlite')
    ap.add_argument('--output', default='NFL_LOCKBOX_2026/advanced_analytics')
    args = ap.parse_args()
    db, output = Path(args.db), Path(args.output)
    if not db.is_file():
        raise SystemExit(f'Missing database: {db}')
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    required = {'game_id', 'play_id', 'season', 'week', 'game_date', 'posteam', 'qtr', 'pass_attempt', 'complete_pass', 'rush_attempt', 'receiver_id', 'receiver_name', 'rusher_id', 'rusher_name', 'receiving_yards', 'rushing_yards'}
    cols = {r['name'] for r in conn.execute('PRAGMA table_info(plays)')}
    if not required.issubset(cols):
        raise SystemExit('Missing plays columns: ' + ', '.join(sorted(required-cols)))
    data = conn.execute('''SELECT game_id,play_id,season,week,game_date,posteam,qtr,
      pass_attempt,complete_pass,rush_attempt,receiver_id,receiver_name,
      rusher_id,rusher_name,receiving_yards,rushing_yards
      FROM plays WHERE season=? ORDER BY game_date,week,game_id,play_id''', (SEASON,)).fetchall()
    conn.close()
    grouped = defaultdict(list)
    game_info = {}
    seen = set()
    issues = defaultdict(int)
    for r in data:
        game_id = str(r['game_id'])
        key = (game_id, str(r['play_id']))
        if key in seen:
            raise SystemExit(f'Duplicate play key: {key}')
        seen.add(key)
        qtr = r['qtr']
        if qtr is None or qtr < 1:
            issues['invalid_quarter_rows'] += 1
            continue
        period = 'regulation' if qtr <= 4 else 'overtime'
        game_info[game_id] = {'game_date': r['game_date'], 'week': r['week']}
        if r['pass_attempt'] == 1:
            if not r['receiver_id']:
                issues['pass_attempts_without_receiver_id'] += 1
            elif r['complete_pass'] == 1:
                grouped[(game_id, str(r['posteam'] or ''), str(r['receiver_id']), str(r['receiver_name'] or ''), 'receiving', period)].append({
                    'game_id': game_id, 'yards': safe_float(r['receiving_yards'])})
            else:
                # Missed targets count toward targets, but NOT completed-reception
                # explosive rates. We calculate receiving metrics per reception.
                issues['identified_incomplete_passes_excluded_from_reception_rates'] += 1
        if r['rush_attempt'] == 1:
            if not r['rusher_id']:
                issues['rush_attempts_without_rusher_id'] += 1
            else:
                grouped[(game_id, str(r['posteam'] or ''), str(r['rusher_id']), str(r['rusher_name'] or ''), 'rushing', period)].append({
                    'game_id': game_id, 'yards': safe_float(r['rushing_yards'])})
    # Emit per-game records for players with at least one completed reception or rush.
    game_rows = []
    player_games = defaultdict(list)
    for (game_id, team, pid, name, kind, period), plays in grouped.items():
        rec = {'game_id': game_id, 'game_date': game_info[game_id]['game_date'],
               'week': game_info[game_id]['week'], 'team': team,
               'player_id': pid, 'player_name': name, 'kind': kind,
               'period': period, **metrics(plays, kind)}
        game_rows.append(rec)
        player_games[(team, pid, name, kind, period)].append((game_id, plays))
    game_rows.sort(key=lambda x: (str(x['game_date']), x['game_id'], x['team'], x['player_id'], x['kind'], x['period']))
    # Rolling windows: last N games with recorded opportunities, not last N team games.
    # This avoids silently treating missing appearances or unobserved opportunities as zero.
    window_rows = []
    for (team, pid, name, kind, period), game_list in player_games.items():
        game_list.sort(key=lambda item: (str(game_info[item[0]]['game_date']), game_info[item[0]]['week'] or 0, item[0]))
        for size in (3, 5, 8):
            selected = game_list[-size:]
            combined = [play for _, plays in selected for play in plays]
            window_rows.append({
                'team': team, 'player_id': pid, 'player_name': name,
                'kind': kind, 'period': period, 'window': f'L{size}',
                'observed_games': len(selected), 'full_window': int(len(selected) == size),
                'first_game_date': game_info[selected[0][0]]['game_date'],
                'last_game_date': game_info[selected[-1][0]]['game_date'],
                **metrics(combined, kind),
            })
    window_rows.sort(key=lambda x: (x['team'], x['player_id'], x['kind'], x['period'], int(x['window'][1:])))
    metric_fields = list(metrics([], 'rushing').keys())
    write_csv(output/'player_explosive_game_2026.csv', game_rows,
              ['game_id','game_date','week','team','player_id','player_name','kind','period'] + metric_fields)
    write_csv(output/'player_explosive_windows_2026.csv', window_rows,
              ['team','player_id','player_name','kind','period','window','observed_games','full_window','first_game_date','last_game_date'] + metric_fields)
    audit = {
        'season': SEASON,
        'source': str(db),
        'source_plays': len(data),
        'source_games': len({str(r['game_id']) for r in data}),
        'player_game_rows': len(game_rows),
        'player_window_rows': len(window_rows),
        'explosive_thresholds_yards': THRESHOLDS,
        'issues': dict(issues),
        'limitations': [
            'Receiving opportunity means completed reception, not target; missed passes are excluded',
            'Rolling windows are last N observed games with opportunities, not last N team games',
            'No zero-opportunity player games are inferred',
            'Receiving and rushing plays are counted separately; no combined yardage metric',
            'Explosive share denominator is positive yards; net yards are separately reported',
            'Regulation and overtime are separate; period-level rows do not imply complete games',
            'No claim of complete schedule or player appearance coverage',
            'Player IDs and play classifications are inherited from source PBP'
        ]
    }
    output.mkdir(parents=True, exist_ok=True)
    (output/'explosive_dependency_audit_2026.json').write_text(json.dumps(audit, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(audit, indent=2))


if __name__ == '__main__':
    main()
