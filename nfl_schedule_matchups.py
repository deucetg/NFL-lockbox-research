#!/usr/bin/env python3
"""NFL LOCKBOX Phase 5: verified upcoming schedule -> actual matchup research.

Research only. No odds, picks, projections, or assumed player availability.
Requires Phase 4 matrix. Downloads nflverse schedules/games.csv (not player stats).
Use --schedule-file to test with a local schedule or --as-of YYYY-MM-DD for audits.
"""
import argparse
import csv
import io
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

SOURCE = 'https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv'
REQUIRED_SCHEDULE = {'game_id', 'season', 'game_type', 'week', 'gameday', 'away_team', 'home_team'}
REQUIRED_MATRIX = {'offense', 'defense', 'player_id', 'player_name', 'position', 'kind', 'window', 'offense_team_games', 'defense_games'}
OUTPUT_COLUMNS = ['game_id', 'game_date', 'game_time_et', 'week', 'home_team', 'away_team', 'offense', 'defense', 'venue', 'player_id', 'player_name', 'position', 'kind', 'window', 'data_status', 'offense_team_games', 'defense_games', 'offense_opportunities_per_team_game', 'offense_yards_per_team_game', 'defense_position_opportunities_allowed_per_game', 'defense_position_yards_allowed_per_game', 'defense_position_q1_opportunities_per_game', 'defense_position_red_zone_opportunities_per_game', 'player_explosive_positive_yard_share', 'player_largest_play_positive_yard_share', 'offense_sample_complete', 'defense_sample_complete', 'player_games_status_unknown']


def read_csv_file(path):
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        return csv.DictReader(f).fieldnames, list(csv.DictReader(f))


def parse_date(value):
    return date.fromisoformat(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='NFL_LOCKBOX_2026')
    parser.add_argument('--season', type=int, default=2026)
    parser.add_argument('--as-of', help='YYYY-MM-DD in US Eastern calendar date; defaults to current Eastern date')
    parser.add_argument('--days', type=int, default=8, help='Days ahead, inclusive (default 8)')
    parser.add_argument('--schedule-file', help='Optional local schedule CSV for reproducible tests')
    args = parser.parse_args()
    if not 1 <= args.days <= 31:
        parser.error('--days must be 1..31')
    if args.as_of:
        today = parse_date(args.as_of)
    else:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo('America/New_York')).date()
    cutoff = today + timedelta(days=args.days)
    root = Path(args.root)
    adv = root / 'advanced_analytics'
    matrix_file = adv / 'player_defense_matchup_matrix_2026.csv'
    if not matrix_file.is_file():
        raise SystemExit(f'Missing Phase 4 matrix: {matrix_file}')
    with matrix_file.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        missing = REQUIRED_MATRIX - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f'Missing matrix columns: {sorted(missing)}')
        matrix = list(reader)
    if args.schedule_file:
        schedule_bytes = Path(args.schedule_file).read_bytes()
        source = str(Path(args.schedule_file))
    else:
        req = Request(SOURCE, headers={'User-Agent': 'NFL-LOCKBOX/1.0'})
        with urlopen(req, timeout=90) as response:
            schedule_bytes = response.read()
        source = SOURCE
    schedule_text = schedule_bytes.decode('utf-8-sig')
    schedule_reader = csv.DictReader(io.StringIO(schedule_text))
    missing = REQUIRED_SCHEDULE - set(schedule_reader.fieldnames or [])
    if missing:
        raise SystemExit(f'Missing schedule columns: {sorted(missing)}')
    schedule = list(schedule_reader)
    if not schedule:
        raise SystemExit('Schedule source has no rows')
    chosen = {}
    rejected = Counter()
    for row in schedule:
        if row['season'] != str(args.season) or row['game_type'] != 'REG':
            continue
        try:
            game_date = parse_date(row['gameday'])
        except (ValueError, TypeError):
            rejected['invalid_date'] += 1
            continue
        if not today <= game_date <= cutoff:
            continue
        game_id = row['game_id'].strip()
        home, away = row['home_team'].strip(), row['away_team'].strip()
        if not game_id or not home or not away or home == away:
            rejected['invalid_teams_or_id'] += 1
            continue
        if row.get('home_score', '').strip() or row.get('away_score', '').strip():
            rejected['already_has_score'] += 1
            continue
        if game_id in chosen:
            if chosen[game_id] != row:
                raise SystemExit(f'Conflicting schedule rows for {game_id}')
            rejected['duplicate_identical_schedule_row'] += 1
            continue
        chosen[game_id] = row
    index = {}
    for r in matrix:
        key = (r['offense'], r['defense'])
        index.setdefault(key, []).append(r)
    result = []
    missing_pairings = []
    for game_id, game in sorted(chosen.items(), key=lambda kv: (kv[1]['gameday'], kv[1].get('gametime', ''), kv[0])):
        home, away = game['home_team'], game['away_team']
        for offense, defense, venue in ((home, away, 'home'), (away, home, 'away')):
            pairs = index.get((offense, defense), [])
            if not pairs:
                missing_pairings.append({'game_id': game_id, 'offense': offense, 'defense': defense})
            for r in pairs:
                out = {col: r.get(col, '') for col in OUTPUT_COLUMNS}
                out.update({'game_id': game_id, 'game_date': game['gameday'],
                            'game_time_et': game.get('gametime', ''), 'week': game['week'],
                            'home_team': home, 'away_team': away, 'offense': offense,
                            'defense': defense, 'venue': venue,
                            'data_status': 'DESCRIPTIVE_ONLY_NOT_PLAYER_AVAILABILITY_VERIFIED'})
                result.append(out)
    adv.mkdir(parents=True, exist_ok=True)
    source_dir = root / 'source_data'
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / 'schedule_2026.csv').write_bytes(schedule_bytes)
    outfile = adv / 'upcoming_actual_matchups_2026.csv'
    with outfile.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(result)
    audit = {'season': args.season, 'as_of_eastern_date': today.isoformat(),
             'through_eastern_date_inclusive': cutoff.isoformat(),
             'schedule_source': source, 'downloaded_at_utc': datetime.now(timezone.utc).isoformat(),
             'schedule_rows_total': len(schedule), 'eligible_upcoming_games': len(chosen),
             'actual_matchup_rows': len(result), 'missing_offense_defense_pairings': missing_pairings,
             'rejected': dict(rejected), 'status': 'OK' if chosen and result and not missing_pairings else 'REVIEW',
             'limitations': ['Only regular season games with no posted scores in the selected date range',
                             'No player active/inactive or starting lineup confirmation',
                             'Rows describe prior player and defense data, not projections or betting recommendations',
                             'Missing/unknown receiving targets and positional identity limitations carry over from Phase 4',
                             'Schedule time is source-reported ET; confirm schedule changes before use']}
    (adv / 'schedule_matchup_audit_2026.json').write_text(json.dumps(audit, indent=2) + '\n')
    print(json.dumps(audit, indent=2))
    if not chosen:
        raise SystemExit('No upcoming games found in schedule window; inspect audit or change --days')
    if not result or missing_pairings:
        raise SystemExit('Incomplete scheduled matchup coverage; inspect audit')


if __name__ == '__main__':
    main()
