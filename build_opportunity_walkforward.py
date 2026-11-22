
#!/usr/bin/env python3
"""NFL LOCKBOX Step 4A: leakage-resistant, provenance-gated walk-forward backtest.

Evaluates three pregame models for NEXT-GAME yards: trailing L3 average,
trailing L6 average, and workload x blended historical yards/opportunity.
Every historical as-of is generated from exactly six PREVIOUS team games;
the seventh team game is held out as the outcome. Only independent game
coverage marked BOX_SCORE_VALIDATED counts toward reliability metrics.

All other outputs are exploratory diagnostics, NOT validated hit rates,
probabilities, odds, prop selections, or evidence that a model is better.
Missing player-games never become zero. A missing participation record is
unknown even if a full game exists in the team calendar.
"""

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from build_veteran_resurgence import validate_source_metadata

SEASON = 2026
VALIDATED_GAME_STATUS = 'BOX_SCORE_VALIDATED'
PAIRINGS = {
    'receiving': ('targets', 'receiving_yards', 2.0, 0.5, 0.15),
    'rushing': ('rush_attempts', 'rushing_yards', 4.0, 1.0, 0.15),
    'passing': ('pass_attempts', 'passing_yards', 15.0, 3.0, 0.10),
}
FIELDS = [
    'season', 'team', 'player_id', 'player_name', 'role', 'asof_game',
    'asof_date', 'heldout_game', 'heldout_date', 'previous_three_games',
    'recent_three_games', 'source_statuses', 'missing_game_ids',
    'opportunity_metric', 'outcome_metric', 'previous_l3_opportunities',
    'recent_l3_opportunities', 'pregame_opportunity_signal',
    'actual_heldout_yards', 'pred_recent_l3_yards', 'pred_trailing_l6_yards',
    'pred_workload_adjusted_yards', 'abs_error_l3', 'abs_error_l6',
    'abs_error_workload_adjusted', 'backtest_status', 'research_eligible',
    'explanation',
]


def load_csv(path):
    with Path(path).open(newline='', encoding='utf-8-sig') as stream:
        return list(csv.DictReader(stream))


def number(value):
    if value is None or str(value).strip() == '':
        return None
    try:
        x = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f'Invalid numeric value {value!r}') from exc
    if not math.isfinite(x):
        raise ValueError(f'Non-finite numeric value {value!r}')
    return x


def fmt(value, ndigits=4):
    return '' if value is None else round(value, ndigits)


def build(usage, coverage):
    """Return all historical replay attempts; incomplete or unverified rows stay flagged."""
    calendar = defaultdict(dict)
    coverage_status = {}
    for game in coverage:
        gid, date = game['game_id'], game['date']
        if not gid or not date.startswith(f'{SEASON}-'):
            raise ValueError(f'Invalid coverage game ID/season: {gid}/{date}')
        if gid in coverage_status:
            raise ValueError(f'Duplicate coverage game ID: {gid}')
        status = game.get('status', 'UNKNOWN') or 'UNKNOWN'
        teams = [t.strip() for t in game.get('teams', '').split(',') if t.strip()]
        if len(teams) != 2 or len(set(teams)) != 2:
            raise ValueError(f'Invalid coverage teams for {gid}: {teams}')
        coverage_status[gid] = status
        for team in teams:
            if gid in calendar[team]:
                raise ValueError(f'Duplicate team game: {team}/{gid}')
            calendar[team][gid] = date

    players = defaultdict(dict)
    names = {}
    for sample in usage:
        role = sample.get('role', '')
        if role not in PAIRINGS:
            continue
        if str(sample.get('season', '')) != str(SEASON):
            raise ValueError(f'Unexpected player-game season: {sample.get("game_id")}')
        team, pid, gid = sample['team'], sample['player_id'], sample['game_id']
        if not pid or gid not in calendar.get(team, {}):
            raise ValueError(f'Unknown player-team game: {team}/{pid}/{gid}')
        if sample.get('game_date', '') != calendar[team][gid]:
            raise ValueError(f'Usage/coverage date disagreement: {team}/{pid}/{gid}')
        key = (team, pid, role)
        if gid in players[key]:
            raise ValueError(f'Duplicate player-game-role: {key}/{gid}')
        players[key][gid] = sample
        names[key] = sample.get('player_name', '')

    results = []
    for (team, pid, role), observed in sorted(players.items()):
        volume, outcome, min_vol, min_delta, pct = PAIRINGS[role]
        team_games = sorted(calendar[team], key=lambda gid: (calendar[team][gid], gid))
        # Each as-of has six earlier *team games*. The outcome is next team game.
        for i in range(5, len(team_games) - 1):
            past = team_games[i-5:i+1]
            future = team_games[i+1]
            older, recent = past[:3], past[3:]
            assert len(set(past + [future])) == 7
            assert calendar[team][future] >= calendar[team][past[-1]]
            all_games = past + [future]
            missing = [gid for gid in all_games if gid not in observed]
            statuses = sorted({coverage_status[gid] for gid in all_games})
            row = dict.fromkeys(FIELDS, '')
            row.update({
                'season': SEASON, 'team': team, 'player_id': pid,
                'player_name': names[team, pid, role], 'role': role,
                'asof_game': past[-1], 'asof_date': calendar[team][past[-1]],
                'heldout_game': future, 'heldout_date': calendar[team][future],
                'previous_three_games': '|'.join(older),
                'recent_three_games': '|'.join(recent),
                'source_statuses': '|'.join(statuses),
                'missing_game_ids': '|'.join(missing),
                'opportunity_metric': volume, 'outcome_metric': outcome,
                'pregame_opportunity_signal': 'NOT_EVALUATED',
                'backtest_status': 'NOT_EVALUATED', 'research_eligible': 'NO',
            })
            if missing:
                row.update(backtest_status='INCOMPLETE_PLAYER_GAME_COVERAGE',
                           explanation='Missing player-game rows are unknown, including the held-out outcome')
                results.append(row)
                continue
            vols = [number(observed[g].get(volume)) for g in all_games]
            yards = [number(observed[g].get(outcome)) for g in all_games]
            if any(v is None for v in vols + yards):
                row.update(backtest_status='MISSING_METRIC_VALUE',
                           explanation='Blank metric values are not zero')
                results.append(row)
                continue
            if any(v < 0 for v in vols):
                raise ValueError(f'Negative workload: {team}/{pid}/{role}/{past[-1]}')
            older_vol = sum(vols[:3]) / 3
            recent_vol = sum(vols[3:6]) / 3
            rising = (older_vol > 0 and older_vol >= min_vol and
                      recent_vol - older_vol >= min_delta and
                      recent_vol >= older_vol * (1 + pct))
            falling = (older_vol > 0 and older_vol >= min_vol and
                       older_vol - recent_vol >= min_delta and
                       recent_vol <= older_vol * (1 - pct))
            signal = 'OPPORTUNITY_UP' if rising else ('OPPORTUNITY_DOWN' if falling else 'NO_MATERIAL_CHANGE')
            forecast_l3 = sum(yards[3:6]) / 3
            forecast_l6 = sum(yards[:6]) / 6
            # All coefficients and aggregates are from games known at as-of.
            vol_recent = sum(vols[3:6])
            vol_six = sum(vols[:6])
            workload_forecast = None
            if vol_recent > 0 and vol_six > 0:
                recent_eff = sum(yards[3:6]) / vol_recent
                six_eff = sum(yards[:6]) / vol_six
                workload_forecast = recent_vol * (0.5 * recent_eff + 0.5 * six_eff)
            heldout = yards[6]
            fully_verified = all(coverage_status[g] == VALIDATED_GAME_STATUS for g in all_games)
            state = 'ELIGIBLE_VALIDATED_REPLAY' if fully_verified else 'PROVISIONAL_UNVALIDATED_GAMES'
            row.update({
                'previous_l3_opportunities': fmt(older_vol),
                'recent_l3_opportunities': fmt(recent_vol),
                'pregame_opportunity_signal': signal,
                'actual_heldout_yards': fmt(heldout),
                'pred_recent_l3_yards': fmt(forecast_l3),
                'pred_trailing_l6_yards': fmt(forecast_l6),
                'pred_workload_adjusted_yards': fmt(workload_forecast),
                'abs_error_l3': fmt(abs(heldout - forecast_l3)),
                'abs_error_l6': fmt(abs(heldout - forecast_l6)),
                'abs_error_workload_adjusted': fmt(abs(heldout - workload_forecast)) if workload_forecast is not None else '',
                'backtest_status': state,
                'research_eligible': 'YES' if fully_verified else 'NO',
                'explanation': ('All seven games independently box-score validated; still review player participation'
                                if fully_verified else
                                'Seven observed player-games, but game-level box-score validation is incomplete'),
            })
            results.append(row)
    return results


def summarize_verified(rows):
    """Aggregate only eligibility-gated held-out errors; absent evidence -> null."""
    eligible = [r for r in rows if r['research_eligible'] == 'YES']
    by_role = {}
    for role in PAIRINGS:
        subset = [r for r in eligible if r['role'] == role]
        entries = {}
        for field, label in (
            ('abs_error_l3', 'l3_mae_yards'),
            ('abs_error_l6', 'l6_mae_yards'),
            ('abs_error_workload_adjusted', 'adjusted_mae_yards'),
        ):
            nums = [number(r[field]) for r in subset if r[field] != '']
            entries[label] = round(sum(nums)/len(nums), 4) if nums else None
            entries[label.replace('mae_yards', 'sample_count')] = len(nums)
        l3, adjusted = entries['l3_mae_yards'], entries['adjusted_mae_yards']
        entries['adjusted_improvement_vs_l3_pct'] = (
            round(100*(l3-adjusted)/l3, 2)
            if l3 is not None and adjusted is not None and l3 != 0 and
            entries['l3_sample_count'] == entries['adjusted_sample_count']
            else None
        )
        by_role[role] = entries
    return by_role


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--usage', required=True)
    parser.add_argument('--coverage', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output-dir', default='NFL_LOCKBOX_2026/advanced_analytics')
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    if manifest.get('source_type') != 'successful_main_build_artifact':
        raise SystemExit('Only successful fresh main-build artifacts may be used')
    if not manifest.get('upstream_run_id') or not manifest.get('upstream_head_sha'):
        raise SystemExit('Missing upstream run provenance')
    usage, coverage = load_csv(args.usage), load_csv(args.coverage)
    validate_source_metadata(manifest, usage, coverage)
    rows = build(usage, coverage)
    if not rows:
        raise SystemExit('No historical replay windows available')
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    csv_out = output / 'opportunity_walkforward_2026.csv'
    with csv_out.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    counts = Counter(r['backtest_status'] for r in rows)
    audit = {
        'generated_utc': datetime.now(timezone.utc).isoformat(),
        'season': SEASON,
        'upstream_run_id': manifest['upstream_run_id'],
        'upstream_head_sha': manifest['upstream_head_sha'],
        'source_latest_date': manifest['latest_usage_game_date'],
        'verified_status_required': VALIDATED_GAME_STATUS,
        'source_coverage_status_counts': manifest['coverage_status_counts'],
        'historical_replay_attempts': len(rows),
        'replay_status_counts': dict(counts),
        'validated_heldout_samples': counts.get('ELIGIBLE_VALIDATED_REPLAY', 0),
        'benchmark_results_validated_only': summarize_verified(rows),
        'has_validated_replays': counts.get('ELIGIBLE_VALIDATED_REPLAY', 0) > 0,
        'can_claim_predictive_improvement': False,
        'limitations': [
            'Exactly six pregame team games; held-out game never contributes features',
            'Missing player-game observations are unknown, not zeros',
            'Only independently BOX_SCORE_VALIDATED games enter accuracy estimates',
            'Availability and participation evidence remain separate from play-by-play',
            'One season; multiple overlapping windows per player cause dependence',
            'No odds, prop thresholds, wagering outcomes, or confidence calibration',
            'One or a handful of validated replays is not sufficient proof of improvement',
            'Source record-count/date matching is not a cryptographic checksum',
        ],
    }
    (output / 'opportunity_walkforward_audit_2026.json').write_text(
        json.dumps(audit, indent=2) + '\n', encoding='utf-8'
    )
    print(json.dumps({'csv': str(csv_out), **audit}, indent=2))


if __name__ == '__main__':
    main()
