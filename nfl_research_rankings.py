#!/usr/bin/env python3
"""NFL LOCKBOX Phase 6: transparent, odds-blind research-priority rankings.

Consumes Phase 5 actual matchups. Rankings are descriptive screening priorities,
NOT forecasts, prop probabilities, active-roster confirmations, or betting picks.
No external player statistics or odds are introduced.
"""
import argparse
import csv
import json
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path

INPUT = 'upcoming_actual_matchups_2026.csv'
OUTPUT = 'upcoming_research_rankings_2026.csv'
SUMMARY = 'game_research_shortlists_2026.csv'
AUDIT = 'research_rankings_audit_2026.json'
METRICS = ('offense_opportunities_per_team_game', 'offense_yards_per_team_game',
           'defense_position_opportunities_allowed_per_game',
           'defense_position_yards_allowed_per_game',
           'defense_position_q1_opportunities_per_game',
           'defense_position_red_zone_opportunities_per_game')


def number(value):
    try:
        if value is None or str(value).strip() == '':
            return None
        return float(value)
    except (ValueError, TypeError):
        return None


def percentile(value, values):
    """Within-kind empirical rank, 0..1; no probability interpretation."""
    if value is None:
        return None
    good = sorted(v for v in values if v is not None)
    if len(good) < 2:
        return 0.5 if good else None
    below = sum(v < value for v in good)
    equal = sum(v == value for v in good)
    return round((below + 0.5 * equal) / len(good), 6)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='NFL_LOCKBOX_2026')
    parser.add_argument('--top', type=int, default=5, help='Top candidates per game and kind')
    args = parser.parse_args()
    if not 1 <= args.top <= 30:
        parser.error('--top must be 1..30')
    directory = Path(args.root) / 'advanced_analytics'
    path = directory / INPUT
    if not path.is_file():
        raise SystemExit(f'Missing Phase 5 file: {path}')
    with path.open(newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        required = {'game_id', 'game_date', 'offense', 'defense', 'player_id',
                    'player_name', 'position', 'kind', 'window', 'offense_team_games',
                    'defense_games', 'offense_sample_complete', 'defense_sample_complete',
                    'player_games_status_unknown', *METRICS}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f'Missing columns: {sorted(missing)}')
        source = list(reader)
    if not source:
        raise SystemExit('Phase 5 file is empty: no rankings produced')
    unique = {}
    duplicates = 0
    for row in source:
        key = (row['game_id'], row['offense'], row['defense'], row['player_id'], row['kind'], row['window'])
        if key in unique:
            duplicates += 1
            if unique[key] != row:
                raise SystemExit(f'Conflicting matchup rows for {key}')
        else:
            unique[key] = row
    if duplicates:
        raise SystemExit(f'Duplicate matchup keys: {duplicates}; repair Phase 5 input')

    by_player = defaultdict(dict)
    for row in source:
        if row['window'] not in ('L3', 'L5', 'L8'):
            continue
        key = (row['game_id'], row['offense'], row['defense'], row['player_id'], row['kind'])
        by_player[key][row['window']] = row
    candidates = []
    for key, windows in by_player.items():
        # Use L3 when present, then L5, then L8; never fill missing windows with invented data.
        primary_window = next((w for w in ('L3', 'L5', 'L8') if w in windows), None)
        if not primary_window:
            continue
        row = windows[primary_window]
        available = [w for w in ('L3', 'L5', 'L8') if w in windows]
        opp = [number(windows[w]['offense_opportunities_per_team_game']) for w in available]
        opp = [x for x in opp if x is not None]
        consistency = (min(opp) / max(opp)) if len(opp) >= 2 and max(opp) > 0 else None
        incomplete = any(windows[w].get('offense_sample_complete') != '1' or
                         windows[w].get('defense_sample_complete') != '1' for w in available)
        player_unknown = any(windows[w].get('player_games_status_unknown') == '1' for w in available)
        explosive = number(row.get('player_explosive_positive_yard_share'))
        flags = []
        if incomplete:
            flags.append('INCOMPLETE_TEAM_OR_DEFENSE_WINDOW')
        if player_unknown:
            flags.append('PLAYER_OPPORTUNITY_GAMES_UNKNOWN')
        if len(available) < 3:
            flags.append('MISSING_SOME_L3_L5_L8_WINDOWS')
        if explosive is not None and explosive >= 0.5:
            flags.append('HIGH_EXPLOSIVE_YARD_DEPENDENCE')
        if consistency is not None and consistency < 0.5:
            flags.append('VARIABLE_OPPORTUNITY_RATE_ACROSS_WINDOWS')
        flags.append('PLAYER_AVAILABILITY_NOT_VERIFIED')
        entry = {k: row.get(k, '') for k in ('game_id', 'game_date', 'game_time_et', 'week',
                  'home_team', 'away_team', 'offense', 'defense', 'venue', 'player_id',
                  'player_name', 'position', 'kind')}
        entry.update({'primary_window': primary_window, 'windows_available': '|'.join(available),
                      'opportunity_rate_consistency_ratio': round(consistency, 3) if consistency is not None else '',
                      'player_explosive_positive_yard_share': row.get('player_explosive_positive_yard_share', ''),
                      'sample_complete_all_available_windows': int(not incomplete),
                      'player_opportunity_games_unknown': int(player_unknown),
                      'research_flags': '|'.join(flags), 'research_status': 'DESCRIPTIVE_ONLY_NOT_PROJECTION'})
        for metric in METRICS:
            entry[metric] = row.get(metric, '')
        candidates.append(entry)

    # Score each kind separately; cross-kind raw opportunities are not comparable.
    groups = defaultdict(list)
    for c in candidates:
        groups[c['kind']].append(c)
    weights = {
        'offense_opportunities_per_team_game': 0.38,
        'offense_yards_per_team_game': 0.10,
        'defense_position_opportunities_allowed_per_game': 0.22,
        'defense_position_yards_allowed_per_game': 0.15,
        'defense_position_q1_opportunities_per_game': 0.08,
        'defense_position_red_zone_opportunities_per_game': 0.07,
    }
    for kind, items in groups.items():
        # Defensive allowance metrics are identical for all players in the same
        # defense-position-window; ranking only provides a transparent heuristic.
        pools = {m: [number(c[m]) for c in items] for m in METRICS}
        for c in items:
            components = [(weights[m], percentile(number(c[m]), pools[m])) for m in METRICS]
            valid = [(w, v) for w, v in components if v is not None]
            score = sum(w * v for w, v in valid) / sum(w for w, _ in valid) if valid else 0
            if c['sample_complete_all_available_windows'] != 1:
                score *= 0.85
            if c['player_opportunity_games_unknown'] == 1:
                score *= 0.90
            c['research_priority_index'] = round(score * 100, 2)
            c['research_priority_interpretation'] = 'RELATIVE_REVIEW_ORDER_NOT_HIT_PROBABILITY'

    by_game_kind = defaultdict(list)
    for c in candidates:
        by_game_kind[(c['game_id'], c['kind'])].append(c)
    ranked = []
    shortlist = []
    for (game, kind), items in sorted(by_game_kind.items()):
        items.sort(key=lambda x: (-x['research_priority_index'], x['offense'], x['player_id']))
        for rank, c in enumerate(items, 1):
            c['game_kind_rank'] = rank
            c['shortlisted'] = int(rank <= args.top)
            ranked.append(c)
            if rank <= args.top:
                shortlist.append(dict(c))
    directory.mkdir(parents=True, exist_ok=True)
    columns = list(ranked[0]) if ranked else []
    if not columns:
        raise SystemExit('No eligible player windows found')
    for name, data in ((OUTPUT, ranked), (SUMMARY, shortlist)):
        with (directory / name).open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(data)
    audit = {
        'phase': 6, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'source': str(path), 'source_rows': len(source), 'distinct_upcoming_games': len({r['game_id'] for r in source}),
        'distinct_player_game_kind_candidates': len(candidates),
        'ranked_rows': len(ranked), 'shortlist_rows': len(shortlist),
        'top_per_game_and_kind': args.top,
        'kind_counts': dict(Counter(c['kind'] for c in candidates)),
        'flag_counts': dict(Counter(flag for c in candidates for flag in c['research_flags'].split('|'))),
        'scoring_weights': weights,
        'status': 'OK',
        'limitations': [
            'Priority index is a relative, descriptive review heuristic, not a probability, forecast, or bet recommendation',
            'Comparisons are ranked separately for receiving and rushing; no passing ranking in Phase 5 input',
            'L3 is the primary sample when present; L5/L8 availability and opportunity-rate variation are disclosed',
            'First-quarter and red-zone defense measures are descriptive, not player-specific projections',
            'Players are not confirmed active or on current depth charts; injury and lineup integration is Phase 7',
            'Missing receiver IDs, unknown player opportunity games, and positional classification limitations carry over',
            'Explosive dependency is flagged; no estimated hit rates or sportsbook lines are generated',
        ]
    }
    (directory / AUDIT).write_text(json.dumps(audit, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(audit, indent=2))


if __name__ == '__main__':
    main()
