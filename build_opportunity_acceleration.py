
#!/usr/bin/env python3
"""NFL LOCKBOX: auditable, non-overlapping opportunity acceleration (research only).

Uses existing player_game_usage.csv and game_coverage.csv. Does not infer that an
unobserved player-game is zero, that a game is complete, or that a player was active.
Does not modify any existing file; exports separate CSV and JSON outputs.
"""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

WINDOWS = (3, 5)
MARKETS = {
    'receiving': ('targets', 'receptions', 'receiving_yards', 'q1_targets', 'red_zone_targets', 'third_down_targets'),
    'rushing': ('rush_attempts', 'rushing_yards', 'q1_rush_attempts', 'red_zone_rushes', 'goal_to_go_rushes'),
    'passing': ('pass_attempts', 'completions', 'passing_yards', 'q1_pass_attempts'),
}
VOLUME = {'receiving': 'targets', 'rushing': 'rush_attempts', 'passing': 'pass_attempts'}


def read_csv(path):
    with path.open(newline='', encoding='utf-8-sig') as stream:
        return list(csv.DictReader(stream))


def as_number(value):
    return float(value or 0)


def build(usage, coverage):
    schedule = defaultdict(dict)
    coverage_status = {}
    for g in coverage:
        gid = g['game_id']
        coverage_status[gid] = g.get('status', 'UNKNOWN')
        for team in g.get('teams', '').split(','):
            team = team.strip()
            if team:
                schedule[team][gid] = g['date']
    observed = defaultdict(dict)
    names = {}
    for p in usage:
        if p['role'] not in MARKETS:
            continue
        key = (p['team'], p['player_id'], p['role'])
        gid = p['game_id']
        if gid in observed[key]:
            raise ValueError(f'Duplicate player-game-role: {key}, {gid}')
        if gid not in schedule[p['team']]:
            raise ValueError(f'Usage game not in coverage calendar: {p["team"]}, {gid}')
        observed[key][gid] = p
        names[key] = p['player_name']
    output = []
    for key, games in sorted(observed.items()):
        team, pid, role = key
        ordered = sorted(schedule[team], key=lambda gid: (schedule[team][gid], gid), reverse=True)
        for n in WINDOWS:
            current = ordered[:n]
            previous = ordered[n:2*n]
            if len(current) < n or len(previous) < n:
                continue
            missing_current = [gid for gid in current if gid not in games]
            missing_previous = [gid for gid in previous if gid not in games]
            complete = not missing_current and not missing_previous
            for metric in MARKETS[role]:
                cur_total = sum(as_number(games[gid][metric]) for gid in current if gid in games)
                prev_total = sum(as_number(games[gid][metric]) for gid in previous if gid in games)
                cur_avg = cur_total/n if not missing_current else None
                prev_avg = prev_total/n if not missing_previous else None
                delta = cur_avg-prev_avg if complete else None
                pct = 100*delta/prev_avg if complete and prev_avg != 0 else None
                status = ('INCOMPLETE_PLAYER_GAME_COVERAGE' if not complete else
                          'ZERO_PREVIOUS_BASELINE' if prev_avg == 0 else
                          'INCREASING' if delta > 0 else 'DECREASING' if delta < 0 else 'STABLE')
                output.append({
                    'team': team, 'player_id': pid, 'player_name': names[key], 'role': role,
                    'metric': metric, 'window': f'L{n}_vs_previous_{n}',
                    'current_game_ids': '|'.join(current), 'previous_game_ids': '|'.join(previous),
                    'current_observed_games': n-len(missing_current),
                    'previous_observed_games': n-len(missing_previous),
                    'missing_current_game_ids': '|'.join(missing_current),
                    'missing_previous_game_ids': '|'.join(missing_previous),
                    'current_per_team_game': round(cur_avg, 4) if cur_avg is not None else '',
                    'previous_per_team_game': round(prev_avg, 4) if prev_avg is not None else '',
                    'absolute_change': round(delta, 4) if delta is not None else '',
                    'percent_change': round(pct, 2) if pct is not None else '',
                    'trend_status': status,
                    'source_game_statuses': '|'.join(sorted(set(coverage_status.get(g, 'UNKNOWN') for g in current+previous))),
                    'verified_game_completeness': 'NOT_VERIFIED',
                })
    return output


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--usage', default='player_game_usage.csv')
    ap.add_argument('--coverage', default='game_coverage.csv')
    ap.add_argument('--output-dir', default='NFL_LOCKBOX_2026/advanced_analytics')
    args = ap.parse_args()
    rows = build(read_csv(Path(args.usage)), read_csv(Path(args.coverage)))
    if not rows:
        raise SystemExit('No comparable windows found; no output written')
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out/'opportunity_acceleration_2026.csv'
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    from collections import Counter
    audit = {
        'rows': len(rows), 'trend_counts': dict(Counter(r['trend_status'] for r in rows)),
        'metrics': MARKETS, 'comparison': 'Non-overlapping team-game windows; latest N vs preceding N',
        'limitations': [
            'No missing player-game is interpreted as zero',
            'Observed usage is not proof of full-game coverage or participation',
            'All completeness statuses require independent validation',
            'Percentage change is undefined when previous average is zero',
            'No sportsbook probabilities or prop recommendations are inferred',
        ],
    }
    (out/'opportunity_acceleration_audit_2026.json').write_text(json.dumps(audit, indent=2)+'\n')
    print(json.dumps({'csv': str(path), **audit}, indent=2))

if __name__ == '__main__':
    main()
