#!/usr/bin/env python3
"""NFL LOCKBOX Step 9B.1 — audited defensive allowance rankings.

Reads existing LOCKBOX defensive game CSV. No external data, odds, or injury inputs.
Does not claim coverage rates, EPA, success rate, or WR1/WR2/WR3 rankings.
"""
import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

VERSION = 'step9b_defensive_ratings_v1'
METRICS = ('opportunities', 'completions', 'yards', 'explosive_plays',
           'explosive_yards', 'q1_opportunities', 'q1_yards',
           'third_down_opportunities', 'red_zone_opportunities',
           'goal_to_go_opportunities', 'touchdowns')
GROUPS = (('WR', 'receiving'), ('TE', 'receiving'),
          ('RB', 'receiving'), ('FB', 'receiving'),
          ('WR', 'rushing'), ('RB', 'rushing'),
          ('QB', 'rushing'), ('FB', 'rushing'))
WINDOWS = ('SEASON', 'L3', 'L5', 'L8')
FIELDS = ('defense', 'window', 'position', 'kind', 'period',
          'games_available', 'sample_complete', 'last_game_date',
          *METRICS, 'opportunities_per_game', 'yards_per_game',
          'yards_per_opportunity', 'explosive_plays_per_game',
          'q1_yards_per_game', 'third_down_opportunities_per_game',
          'red_zone_opportunities_per_game', 'touchdowns_per_game',
          'yards_allowed_rank', 'opportunities_allowed_rank',
          'explosive_plays_allowed_rank', 'ranked_teams',
          'classification_caution', 'source')


def read_rows(path):
    with path.open(newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        required = {'game_id', 'game_date', 'defense', 'position',
                    'kind', 'period', *METRICS}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError('Missing columns: ' +
                             ', '.join(sorted(required - set(reader.fieldnames or []))))
        return list(reader)


def number(value):
    try:
        return float(value or 0)
    except (ValueError, TypeError):
        raise ValueError(f'Invalid numeric value: {value!r}')


def round3(value):
    return round(value, 3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='NFL_LOCKBOX_2026')
    args = parser.parse_args()
    root = Path(args.root)
    source = root / 'advanced_analytics' / 'defensive_position_game_2026.csv'
    if not source.is_file():
        parser.error(f'Missing source: {source}')
    data = read_rows(source)
    if not data:
        parser.error('Defensive game CSV is empty')

    # Derive observed games from all position rows, including unclassified rows.
    observed = defaultdict(dict)
    indexed = defaultdict(lambda: defaultdict(float))
    seen_keys = set()
    for record in data:
        team = record['defense'].strip()
        game = record['game_id'].strip()
        date = record['game_date'].strip()
        position = record['position'].strip()
        kind = record['kind'].strip()
        period = record['period'].strip()
        if not team or not game or not date:
            raise ValueError('Missing defense, game, or date')
        if period not in ('regulation', 'overtime'):
            raise ValueError(f'Unexpected period: {period}')
        key = (game, team, position, kind, period)
        if key in seen_keys:
            raise ValueError(f'Duplicate defensive game row: {key}')
        seen_keys.add(key)
        if game in observed[team] and observed[team][game] != date:
            raise ValueError(f'Conflicting game dates: {team} {game}')
        observed[team][game] = date
        for metric in METRICS:
            value = number(record[metric])
            if value < 0 and metric != 'yards' and metric != 'q1_yards' and metric != 'explosive_yards':
                raise ValueError(f'Negative count: {key} {metric}')
            indexed[key][metric] = value

    # Missing position/game combination means zero *recorded* opportunities,
    # not proof of complete position attribution.
    output = []
    for team, games in sorted(observed.items()):
        ordered = sorted(games, key=lambda game: (games[game], game))
        for window in WINDOWS:
            n = len(ordered) if window == 'SEASON' else int(window[1:])
            selected = ordered[-n:]
            count = len(selected)
            for position, kind in GROUPS:
                sums = {metric: 0.0 for metric in METRICS}
                for game in selected:
                    values = indexed.get((game, team, position, kind, 'regulation'), {})
                    for metric in METRICS:
                        sums[metric] += values.get(metric, 0.0)
                row = dict(defense=team, window=window,
                           position=position, kind=kind, period='regulation',
                           games_available=count,
                           sample_complete=(window == 'SEASON' or count == n),
                           last_game_date=games[selected[-1]],
                           classification_caution='POSITION_CLASSIFIED_ONLY',
                           source='LOCKBOX_2026_DEFENSIVE_POSITION_GAME')
                row.update({metric: round3(value) for metric, value in sums.items()})
                row['opportunities_per_game'] = round3(sums['opportunities'] / count)
                row['yards_per_game'] = round3(sums['yards'] / count)
                row['yards_per_opportunity'] = (round3(sums['yards'] / sums['opportunities'])
                                                if sums['opportunities'] else None)
                for name, metric in (
                    ('explosive_plays_per_game', 'explosive_plays'),
                    ('q1_yards_per_game', 'q1_yards'),
                    ('third_down_opportunities_per_game', 'third_down_opportunities'),
                    ('red_zone_opportunities_per_game', 'red_zone_opportunities'),
                    ('touchdowns_per_game', 'touchdowns'),
                ):
                    row[name] = round3(sums[metric] / count)
                output.append(row)

    # Rank lower allowances as stronger defense. Ties share competition rank.
    buckets = defaultdict(list)
    for row in output:
        buckets[(row['window'], row['position'], row['kind'])].append(row)
    for bucket in buckets.values():
        for rank_col, metric in (
            ('yards_allowed_rank', 'yards_per_game'),
            ('opportunities_allowed_rank', 'opportunities_per_game'),
            ('explosive_plays_allowed_rank', 'explosive_plays_per_game'),
        ):
            values = sorted(r[metric] for r in bucket)
            for row in bucket:
                row[rank_col] = values.index(row[metric]) + 1
                row['ranked_teams'] = len(bucket)

    output.sort(key=lambda row: (row['window'], row['kind'],
                                 row['position'], row['yards_allowed_rank'],
                                 row['defense']))
    target = root / 'advanced_analytics'
    target.mkdir(parents=True, exist_ok=True)
    csv_path = target / 'defensive_ratings_2026.csv'
    with csv_path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(output)

    audit = {
        'version': VERSION,
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'PASS',
        'source': str(source),
        'rows': len(output),
        'teams': len(observed),
        'observed_games': len({game for games in observed.values() for game in games}),
        'defensive_team_games': sum(len(games) for games in observed.values()),
        'rows_by_window': dict(sorted(Counter(r['window'] for r in output).items())),
        'incomplete_window_rows': sum(not r['sample_complete'] for r in output),
        'rank_definition': '1 = fewest classified yards/opportunities/explosive plays allowed per observed defensive team game; tied teams share rank',
        'sample_definition': 'L3/L5/L8 = last N observed games; season = all observed games',
        'limitations': [
            'Ranks cover classified player positions only; unclassified receiving/rushing opportunities are excluded.',
            'Missing receiver IDs are not attributed to a position.',
            'No WR1/WR2/WR3 role classification.',
            'No man/zone, EPA, success rate, pressure, routes or snaps claimed.',
            'No strength-of-schedule or opponent adjustment.',
            'Ranks are descriptive and not betting probabilities.',
            'Negative yards are valid; ranks may reflect negative-yard plays.',
        ],
    }
    audit_path = target / 'defensive_ratings_audit_2026.json'
    audit_path.write_text(json.dumps(audit, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': audit['status'], 'rows': len(output),
                      'teams': len(observed), 'output': str(csv_path),
                      'audit': str(audit_path)}, indent=2))


if __name__ == '__main__':
    main()
