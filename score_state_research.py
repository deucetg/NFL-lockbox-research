#!/usr/bin/env python3
"""NFL LOCKBOX 2026: leading/tied/trailing QB, receiver and team splits.
Uses ONLY the user-uploaded play_by_play_2026.csv.gz in this folder.
Run after build_lockbox.py: python score_state_research.py
"""
import csv
import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'play_by_play_2026.csv.gz'
OUT = ROOT / 'NFL_LOCKBOX_2026'
OUT.mkdir(exist_ok=True)


def flag(row, key):
    return row.get(key) == '1'


def number(row, key):
    try:
        return float(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def score_state(diff):
    if diff == 0:
        return ('TIED',)
    if diff < 0:
        return ('TRAIL', 'TRAIL_1_8' if diff >= -8 else 'TRAIL_9_PLUS')
    return ('LEAD', 'LEAD_1_8' if diff <= 8 else 'LEAD_9_PLUS')


def write_csv(filename, header, data):
    with (OUT / filename).open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(data)


if not SOURCE.exists():
    raise FileNotFoundError('Missing play_by_play_2026.csv.gz beside this script')
with gzip.open(SOURCE, 'rt', newline='') as handle:
    plays = list(csv.DictReader(handle))
if not plays or not {'score_differential', 'game_id', 'pass_attempt', 'posteam'}.issubset(plays[0]):
    raise ValueError('Source play-by-play is empty or missing required columns')

# An END GAME marker confirms the feed reached game end, not that every play is accurate.
finished = {r['game_id'] for r in plays if (r.get('desc') or '').strip().upper() == 'END GAME'}
team_games = defaultdict(dict)
for r in plays:
    gid = r.get('game_id')
    if gid not in finished or r.get('season') != '2026' or r.get('season_type') != 'REG':
        continue
    for team in (r.get('home_team'), r.get('away_team')):
        if team and r.get('game_date'):
            team_games[team][gid] = r['game_date']
recent = {}
for team, games in team_games.items():
    ordered = sorted(games, key=lambda game: (games[game], game), reverse=True)
    for n in (3, 5, 8):
        recent[(team, n)] = set(ordered[:n])

teams, qbs, receivers = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)
team_seen, qb_seen, receiver_seen = defaultdict(set), defaultdict(set), defaultdict(set)
qb_names, receiver_names, audit = {}, {}, Counter()
for r in plays:
    gid, team = r.get('game_id'), r.get('posteam')
    if gid not in finished or r.get('season') != '2026' or r.get('season_type') != 'REG' or not team:
        continue
    try:
        diff = int(float(r['score_differential']))
    except (ValueError, TypeError, KeyError):
        audit['plays_missing_preplay_score'] += 1
        continue
    if flag(r, 'two_point_attempt'):
        audit['two_point_plays_excluded'] += 1
        continue
    attempt, rush, drop = (flag(r, k) for k in ('pass_attempt', 'rush_attempt', 'qb_dropback'))
    if not (attempt or rush or drop):
        continue
    completed = flag(r, 'complete_pass')
    passer, receiver = r.get('passer_player_id') or '', r.get('receiver_player_id') or ''
    windows = ['SEASON'] + [f'L{n}' for n in (3, 5, 8) if gid in recent.get((team, n), set())]
    periods = ['ALL'] + (['Q1'] if r.get('qtr') == '1' else []) + (['Q4'] if r.get('qtr') == '4' else [])
    if attempt:
        audit['pass_attempts'] += 1
        if not receiver:
            audit['pass_attempts_without_receiver_id'] += 1
    for window in windows:
        for period in periods:
            for state in score_state(diff):
                team_key = (team, window, period, state)
                t = teams[team_key]
                team_seen[team_key].add(gid)
                t['attempts'] += int(attempt)
                t['completions'] += int(attempt and completed)
                t['pass_yards'] += number(r, 'passing_yards') if attempt else 0
                t['dropbacks'] += int(drop)
                t['designed_rushes'] += int(rush and not drop and not flag(r, 'qb_kneel'))
                t['identified_targets'] += int(attempt and bool(receiver))
                if attempt and passer:
                    key = (team, passer, window, period, state)
                    p = qbs[key]
                    qb_seen[key].add(gid)
                    qb_names[(team, passer)] = r.get('passer_player_name') or ''
                    p['attempts'] += 1
                    p['completions'] += int(completed)
                    p['yards'] += number(r, 'passing_yards')
                    p['touchdowns'] += int(flag(r, 'pass_touchdown'))
                    p['third_down_att'] += int(r.get('down') == '3')
                if attempt and receiver:
                    key = (team, receiver, window, period, state)
                    p = receivers[key]
                    receiver_seen[key].add(gid)
                    receiver_names[(team, receiver)] = r.get('receiver_player_name') or ''
                    p['targets'] += 1
                    p['catches'] += int(completed)
                    p['yards'] += number(r, 'receiving_yards')
                    p['touchdowns'] += int(completed and flag(r, 'pass_touchdown'))
                    p['third_down_targets'] += int(r.get('down') == '3')
                    p['redzone_targets'] += int(bool(r.get('yardline_100')) and number(r, 'yardline_100') <= 20)
                    p['goal_to_go_targets'] += int(flag(r, 'goal_to_go'))


def pct(n, d):
    return round(100 * n / d, 1) if d else ''


def per(n, d):
    return round(n / d, 2) if d else ''


team_rows = []
for (team, window, period, state), v in sorted(teams.items()):
    games = len(team_seen[(team, window, period, state)])
    team_rows.append([team, window, period, state, games, v['attempts'], v['completions'],
                      v['pass_yards'], v['dropbacks'], v['designed_rushes'], v['identified_targets'],
                      pct(v['dropbacks'], v['dropbacks'] + v['designed_rushes']),
                      per(v['attempts'], games), per(v['pass_yards'], v['attempts'])])
write_csv('team_score_state_2026.csv',
          ['team', 'window', 'period', 'score_state', 'games_exposed', 'pass_attempts',
           'completions', 'passing_yards', 'dropbacks', 'designed_rushes', 'identified_targets',
           'dropback_rate_pct', 'pass_attempts_per_exposed_game', 'yards_per_attempt'], team_rows)

qb_rows = []
for (team, pid, window, period, state), v in sorted(qbs.items()):
    games = len(qb_seen[(team, pid, window, period, state)])
    qb_rows.append([team, pid, qb_names[(team, pid)], window, period, state, games,
                    v['attempts'], v['completions'], v['yards'], v['touchdowns'],
                    v['third_down_att'], pct(v['completions'], v['attempts']),
                    per(v['yards'], v['attempts']), per(v['attempts'], games), per(v['yards'], games)])
write_csv('qb_score_state_2026.csv',
          ['team', 'player_id', 'player_name', 'window', 'period', 'score_state',
           'games_with_attempts', 'pass_attempts', 'completions', 'passing_yards',
           'passing_tds', 'third_down_attempts', 'completion_pct', 'yards_per_attempt',
           'attempts_per_appearance_game', 'yards_per_appearance_game'], qb_rows)

receiver_rows = []
for (team, pid, window, period, state), v in sorted(receivers.items()):
    games = len(receiver_seen[(team, pid, window, period, state)])
    t = teams[(team, window, period, state)]
    receiver_rows.append([team, pid, receiver_names[(team, pid)], window, period, state, games,
                          t['attempts'], t['identified_targets'], v['targets'], v['catches'],
                          v['yards'], v['touchdowns'], v['third_down_targets'],
                          v['redzone_targets'], v['goal_to_go_targets'],
                          pct(v['targets'], t['attempts']), pct(v['targets'], t['identified_targets']),
                          pct(v['catches'], v['targets']), per(v['yards'], v['targets']),
                          per(v['targets'], games), per(v['yards'], games)])
write_csv('receiver_score_state_2026.csv',
          ['team', 'player_id', 'player_name', 'window', 'period', 'score_state',
           'games_with_targets', 'team_pass_attempts', 'team_identified_targets',
           'targets', 'receptions', 'receiving_yards', 'receiving_tds', 'third_down_targets',
           'redzone_targets', 'goal_to_go_targets', 'targets_per_team_pass_attempt_pct',
           'share_of_identified_targets_pct', 'catch_pct', 'yards_per_target',
           'targets_per_targeted_game', 'yards_per_targeted_game'], receiver_rows)

report = {
    'source': 'User-uploaded nflverse-format 2026 play-by-play; not independently box-score verified',
    'completed_game_markers': len(finished),
    'window_meaning': 'Last 3/5/8 completed team games present in uploaded source',
    'score_meaning': 'Offense pre-play score differential; tied=0; +/-1-8; +/-9 or more',
    'periods': ['ALL', 'Q1', 'Q4'],
    'note': 'End marker does not prove complete PBP. Player games with zero recognized targets may be absent.',
    'double_count_warning': 'LEAD/TRAIL are totals; do not add to their narrower 1-8/9+ subsets.',
    'target_warning': 'Unidentified receiver attempts make identified-target share potentially biased.',
    'audit_counts': dict(audit),
    'rows': {'team': len(team_rows), 'qb': len(qb_rows), 'receiver': len(receiver_rows)}
}
(OUT / 'score_state_audit_2026.json').write_text(json.dumps(report, indent=2))
print('Game-script research completed:', report['rows'])
