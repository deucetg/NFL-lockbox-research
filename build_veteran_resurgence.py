
#!/usr/bin/env python3
"""NFL LOCKBOX Step 3A: cautious workload-recovery / veteran-resurgence scanner.

Research only. Requires fresh main-build usage/coverage CSVs and a matching
fresh-acceleration provenance manifest. Uses three consecutive, non-overlapping
3-TEAM-GAME windows (oldest -> trough -> latest), not last-observed games.

The script never assumes missing player-games mean zero or that a player is a
veteran. Verified veteran tenure requires a separate, sourced registry.
Game completeness and player participation remain independently unverified.
"""

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SEASON = 2026
WINDOW = 3
ROLE_METRICS = {
    'receiving': {'volume': 'targets', 'q1': 'q1_targets', 'red_zone': 'red_zone_targets', 'third_down': 'third_down_targets', 'min_volume': 2.0, 'min_change': 0.5},
    'rushing': {'volume': 'rush_attempts', 'q1': 'q1_rush_attempts', 'red_zone': 'red_zone_rushes', 'third_down': None, 'min_volume': 4.0, 'min_change': 1.0},
    'passing': {'volume': 'pass_attempts', 'q1': 'q1_pass_attempts', 'red_zone': None, 'third_down': None, 'min_volume': 15.0, 'min_change': 3.0},
}
FIELDS = [
    'season', 'team', 'player_id', 'player_name', 'role', 'volume_metric',
    'older_game_ids', 'trough_game_ids', 'latest_game_ids',
    'older_observed_games', 'trough_observed_games', 'latest_observed_games',
    'missing_game_ids', 'older_per_team_game', 'trough_per_team_game',
    'latest_per_team_game', 'decline_pct', 'rebound_pct',
    'latest_q1_per_team_game', 'trough_q1_per_team_game',
    'latest_red_zone_per_team_game', 'trough_red_zone_per_team_game',
    'latest_third_down_per_team_game', 'trough_third_down_per_team_game',
    'opportunity_pattern', 'veteran_eligibility', 'first_nfl_season',
    'veteran_source', 'analysis_status', 'coverage_statuses',
    'verified_research_ready', 'explanation',
]


def load_csv(path):
    with Path(path).open(newline='', encoding='utf-8-sig') as stream:
        return list(csv.DictReader(stream))


def numeric(raw):
    if raw is None or str(raw).strip() == '':
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'Invalid numeric field: {raw!r}') from exc
    if not math.isfinite(value):
        raise ValueError(f'Non-finite numeric field: {raw!r}')
    return value


def percent_change(new, old):
    return None if old is None or old == 0 else 100 * (new - old) / abs(old)


def fmt(value, decimals=4):
    return '' if value is None else round(value, decimals)


def load_registry(path):
    """Optional sourced first-NFL-season lookup; no roster tenure is inferred."""
    if not path:
        return {}
    registry = {}
    for row in load_csv(path):
        player_id = row.get('player_id', '').strip()
        if not player_id or player_id in registry:
            raise ValueError(f'Missing or duplicated veteran player_id: {player_id!r}')
        registry[player_id] = row
    return registry


def veteran_status(player_id, registry):
    row = registry.get(player_id)
    if row is None:
        return 'TENURE_NOT_VERIFIED', '', ''
    first_raw = row.get('first_nfl_season', '').strip()
    source = row.get('source_url', '').strip()
    verified = row.get('verification_status', '').strip().upper() == 'VERIFIED'
    if not first_raw or not verified or not source:
        return 'TENURE_NOT_VERIFIED', first_raw, source
    try:
        first = int(first_raw)
    except ValueError as exc:
        raise ValueError(f'Invalid first NFL season for {player_id}: {first_raw}') from exc
    if not 1900 <= first <= SEASON:
        raise ValueError(f'Out-of-range first NFL season for {player_id}: {first}')
    return ('VERIFIED_5_PLUS_SEASONS' if SEASON - first + 1 >= 5 else 'BELOW_FIVE_SEASONS', first, source)


def build(usage, coverage, registry=None):
    registry = registry or {}
    schedule = defaultdict(dict)
    coverage_status = {}
    for game in coverage:
        gid = game['game_id']
        date = game['date']
        if not date.startswith(f'{SEASON}-'):
            raise ValueError(f'Unexpected coverage season: {gid} {date}')
        if gid in coverage_status:
            raise ValueError(f'Duplicated coverage game: {gid}')
        teams = [t.strip() for t in game.get('teams', '').split(',') if t.strip()]
        if len(teams) != 2 or len(teams) != len(set(teams)):
            raise ValueError(f'Invalid teams in game coverage: {gid}: {teams}')
        coverage_status[gid] = game.get('status', 'UNKNOWN')
        for team in teams:
            schedule[team][gid] = date

    observed = defaultdict(dict)
    names = {}
    for entry in usage:
        role = entry.get('role', '')
        if role not in ROLE_METRICS:
            continue
        if str(entry.get('season', '')) != str(SEASON):
            raise ValueError(f'Unexpected player-game season: {entry.get("game_id")}')
        team, pid, gid = entry['team'], entry['player_id'], entry['game_id']
        if not pid or gid not in schedule.get(team, {}):
            raise ValueError(f'Player-game missing from team calendar: {team}/{pid}/{gid}')
        key = (team, pid, role)
        if gid in observed[key]:
            raise ValueError(f'Duplicate player-game-role: {key}/{gid}')
        observed[key][gid] = entry
        names[key] = entry.get('player_name', '')

    result = []
    for key, samples in sorted(observed.items()):
        team, pid, role = key
        spec = ROLE_METRICS[role]
        full_schedule = sorted(schedule[team], key=lambda g: (schedule[team][g], g), reverse=True)
        games = full_schedule[:3*WINDOW]
        older = games[2*WINDOW:3*WINDOW]
        trough = games[WINDOW:2*WINDOW]
        latest = games[:WINDOW]
        missing = [g for g in games if g not in samples]
        vet, first, source = veteran_status(pid, registry)
        row = dict.fromkeys(FIELDS, '')
        row.update({
            'season': SEASON, 'team': team, 'player_id': pid, 'player_name': names[key],
            'role': role, 'volume_metric': spec['volume'],
            'older_game_ids': '|'.join(older), 'trough_game_ids': '|'.join(trough),
            'latest_game_ids': '|'.join(latest), 'older_observed_games': sum(g in samples for g in older),
            'trough_observed_games': sum(g in samples for g in trough),
            'latest_observed_games': sum(g in samples for g in latest),
            'missing_game_ids': '|'.join(missing), 'opportunity_pattern': 'NOT_EVALUATED',
            'veteran_eligibility': vet, 'first_nfl_season': first,
            'veteran_source': source, 'verified_research_ready': 'NO',
            'coverage_statuses': '|'.join(sorted({coverage_status[g] for g in games})),
        })
        if len(games) < 3*WINDOW:
            row.update(analysis_status='INSUFFICIENT_TEAM_GAMES', explanation='At least nine chronological team games are required')
            result.append(row)
            continue
        if missing:
            row.update(analysis_status='INCOMPLETE_PLAYER_GAME_COVERAGE', explanation='Missing player-game observations are unknown, not zero')
            result.append(row)
            continue

        def average(segment, metric):
            if metric is None:
                return None
            values = [numeric(samples[g].get(metric)) for g in segment]
            if any(value is None for value in values):
                return None
            if metric not in ('receiving_yards', 'rushing_yards', 'passing_yards') and any(value < 0 for value in values):
                raise ValueError(f'Negative opportunity count for {key}/{metric}')
            return sum(values) / WINDOW

        o, t, l = (average(segment, spec['volume']) for segment in (older, trough, latest))
        if None in (o, t, l):
            row.update(analysis_status='MISSING_METRIC_VALUE', explanation='Blank workload value must not be treated as zero')
            result.append(row)
            continue
        row.update({
            'older_per_team_game': fmt(o), 'trough_per_team_game': fmt(t),
            'latest_per_team_game': fmt(l), 'decline_pct': fmt(percent_change(t, o), 2),
            'rebound_pct': fmt(percent_change(l, t), 2),
            'latest_q1_per_team_game': fmt(average(latest, spec['q1'])),
            'trough_q1_per_team_game': fmt(average(trough, spec['q1'])),
            'latest_red_zone_per_team_game': fmt(average(latest, spec['red_zone'])),
            'trough_red_zone_per_team_game': fmt(average(trough, spec['red_zone'])),
            'latest_third_down_per_team_game': fmt(average(latest, spec['third_down'])),
            'trough_third_down_per_team_game': fmt(average(trough, spec['third_down'])),
        })
        decline = (o >= spec['min_volume'] and o - t >= spec['min_change'] and t <= o * 0.80)
        recovery = (l >= spec['min_volume'] and l - t >= spec['min_change'] and
                    (t == 0 or l >= t * 1.20) and l >= o * 0.80)
        pattern = 'WORKLOAD_RECOVERY_PATTERN' if decline and recovery else 'NO_RECOVERY_PATTERN'
        status = 'PROVISIONAL_GAME_COVERAGE'
        if pattern == 'WORKLOAD_RECOVERY_PATTERN' and vet != 'VERIFIED_5_PLUS_SEASONS':
            status = 'PROVISIONAL_TENURE_AND_GAME_COVERAGE'
        row.update({
            'opportunity_pattern': pattern, 'analysis_status': status,
            'explanation': ('Material decline followed by rebound in workload; not proof of veteran status, participation or full-game data'
                            if pattern == 'WORKLOAD_RECOVERY_PATTERN' else
                            'Three-window decline and rebound thresholds not both satisfied'),
        })
        result.append(row)
    return result


def validate_source_metadata(manifest, usage, coverage):
    """Fail closed on row-count/date mismatches (not a cryptographic hash)."""
    for key, actual in (('usage_rows', len(usage)), ('coverage_games', len(coverage))):
        try:
            expected = int(manifest[key])
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f'Missing or invalid manifest source count: {key}') from exc
        if expected != actual:
            raise ValueError(f'Manifest and source rows disagree: {key}: {expected} != {actual}')
    for key, actual in (
        ('latest_usage_game_date', max((r.get('game_date', '') for r in usage), default='')),
        ('latest_coverage_game_date', max((r.get('date', '') for r in coverage), default='')),
    ):
        if manifest.get(key) != actual:
            raise ValueError(f'Manifest source date mismatch: {key}: {manifest.get(key)} != {actual}')
    sources = [Path(p).name for p in manifest.get('source_files', [])]
    if sorted(sources) != sorted(['player_game_usage.csv', 'game_coverage.csv']):
        raise ValueError('Fresh manifest must identify the two expected upstream CSVs')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--usage', required=True, help='Fresh upstream player_game_usage.csv')
    ap.add_argument('--coverage', required=True, help='Fresh upstream game_coverage.csv')
    ap.add_argument('--manifest', required=True, help='Fresh acceleration provenance manifest')
    ap.add_argument('--veteran-registry', default='', help='Optional sourced CSV: player_id,first_nfl_season,verification_status,source_url')
    ap.add_argument('--output-dir', default='NFL_LOCKBOX_2026/advanced_analytics')
    args = ap.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    if manifest.get('source_type') != 'successful_main_build_artifact' or not manifest.get('upstream_run_id') or not manifest.get('upstream_head_sha'):
        raise SystemExit('A valid fresh successful-main-build provenance manifest is required')
    for col in ('latest_usage_game_date', 'latest_coverage_game_date'):
        if not str(manifest.get(col, '')).startswith(f'{SEASON}-'):
            raise SystemExit(f'Manifest has an unexpected season: {col}')
    usage = load_csv(args.usage)
    coverage = load_csv(args.coverage)
    validate_source_metadata(manifest, usage, coverage)
    rows = build(usage, coverage, load_registry(args.veteran_registry))
    if not rows:
        raise SystemExit('No player-game-role source records')
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    filename = out / 'veteran_resurgence_2026.csv'
    with filename.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    audit = {
        'created_utc': datetime.now(timezone.utc).isoformat(), 'season': SEASON,
        'upstream_run_id': manifest['upstream_run_id'], 'upstream_head_sha': manifest['upstream_head_sha'],
        'latest_source_date': manifest['latest_usage_game_date'],
        'source_coverage_status_counts': manifest.get('coverage_status_counts', {}),
        'player_role_team_records': len(rows),
        'opportunity_patterns': dict(Counter(r['opportunity_pattern'] for r in rows)),
        'analysis_status_counts': dict(Counter(r['analysis_status'] for r in rows)),
        'veteran_eligibility_counts': dict(Counter(r['veteran_eligibility'] for r in rows)),
        'verified_recommendations': 0,
        'limitations': [
            'No missing player-games imputed as zeros',
            'Windows are consecutive non-overlapping team games, not observed-game subsets',
            'Veteran tenure requires independent sourced registry',
            'Observed player actions do not validate participation or complete game coverage',
            'No route participation or snap counts inferred from play-by-play',
            'No researched patterns are treated as predictive before walk-forward testing',
            'Source metadata is compared by record count/date, not cryptographic checksums',
        ],
    }
    (out / 'veteran_resurgence_audit_2026.json').write_text(json.dumps(audit, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'csv': str(filename), **audit}, indent=2))


if __name__ == '__main__':
    main()
