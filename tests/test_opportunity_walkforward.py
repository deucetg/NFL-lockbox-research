
"""Tests for NFL LOCKBOX Step 4A time-safe held-out replay research."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_opportunity_walkforward import build, summarize_verified


def sample_inputs(*, validated=False):
    coverage = []
    usage = []
    for i in range(1, 9):
        gid = f'g{i}'
        coverage.append({
            'game_id': gid, 'date': f'2026-09-{i:02d}', 'teams': 'AAA,BBB',
            'status': 'BOX_SCORE_VALIDATED' if validated else 'REQUIRES_BOX_SCORE_VALIDATION',
        })
        targets = 2 if i <= 3 else (4 if i <= 6 else 6)
        usage.append({
            'game_id': gid, 'game_date': f'2026-09-{i:02d}',
            'season': '2026', 'team': 'AAA', 'player_id': 'p1',
            'player_name': 'Receiver', 'role': 'receiving',
            'targets': str(targets), 'receiving_yards': str(targets * 10),
        })
    return usage, coverage


class WalkForwardTests(unittest.TestCase):
    def test_six_past_and_one_heldout_no_overlap(self):
        usage, cov = sample_inputs()
        rows = build(usage, cov)
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first['previous_three_games'], 'g1|g2|g3')
        self.assertEqual(first['recent_three_games'], 'g4|g5|g6')
        self.assertEqual(first['asof_game'], 'g6')
        self.assertEqual(first['heldout_game'], 'g7')
        self.assertEqual(first['pregame_opportunity_signal'], 'OPPORTUNITY_UP')
        self.assertEqual(first['pred_recent_l3_yards'], 40)
        self.assertEqual(first['pred_trailing_l6_yards'], 30)
        self.assertEqual(first['pred_workload_adjusted_yards'], 40)
        self.assertEqual(first['actual_heldout_yards'], 60)

    def test_heldout_outcome_does_not_change_predictions(self):
        usage, cov = sample_inputs()
        old = build(usage, cov)[0]
        changed = copy.deepcopy(usage)
        changed[6]['receiving_yards'] = '999'
        new = build(changed, cov)[0]
        for metric in (
            'pred_recent_l3_yards',
            'pred_trailing_l6_yards',
            'pred_workload_adjusted_yards',
            'pregame_opportunity_signal',
        ):
            self.assertEqual(new[metric], old[metric])
        self.assertNotEqual(
            new['actual_heldout_yards'],
            old['actual_heldout_yards']
        )

    def test_unvalidated_games_excluded_from_reliability(self):
        usage, cov = sample_inputs()
        rows = build(usage, cov)
        self.assertEqual(
            rows[0]['backtest_status'],
            'PROVISIONAL_UNVALIDATED_GAMES'
        )
        self.assertEqual(rows[0]['research_eligible'], 'NO')
        self.assertIsNone(
            summarize_verified(rows)['receiving']['l3_mae_yards']
        )

    def test_explicit_validation_gate(self):
        usage, cov = sample_inputs(validated=True)
        rows = build(usage, cov)
        self.assertEqual(
            rows[0]['backtest_status'],
            'ELIGIBLE_VALIDATED_REPLAY'
        )
        self.assertEqual(rows[0]['research_eligible'], 'YES')
        stats = summarize_verified(rows)
        self.assertEqual(stats['receiving']['l3_sample_count'], 2)
        self.assertIsNotNone(stats['receiving']['l3_mae_yards'])

    def test_one_unvalidated_game_blocks_metric(self):
        usage, cov = sample_inputs(validated=True)
        cov[4]['status'] = 'REQUIRES_BOX_SCORE_VALIDATION'
        rows = build(usage, cov)
        self.assertTrue(
            all(r['research_eligible'] == 'NO' for r in rows)
        )

    def test_missing_heldout_record_is_unknown_not_zero(self):
        usage, cov = sample_inputs(validated=True)
        usage = [r for r in usage if r['game_id'] != 'g7']
        row = build(usage, cov)[0]
        self.assertEqual(
            row['backtest_status'],
            'INCOMPLETE_PLAYER_GAME_COVERAGE'
        )
        self.assertEqual(row['missing_game_ids'], 'g7')
        self.assertEqual(row['actual_heldout_yards'], '')
        self.assertEqual(row['research_eligible'], 'NO')

    def test_blank_prior_value_is_unknown_not_zero(self):
        usage, cov = sample_inputs()
        usage[3]['targets'] = ''
        self.assertEqual(
            build(usage, cov)[0]['backtest_status'],
            'MISSING_METRIC_VALUE'
        )

    def test_duplicate_observation_rejected(self):
        usage, cov = sample_inputs()
        with self.assertRaises(ValueError):
            build(usage + [dict(usage[0])], cov)

    def test_mixed_season_rejected(self):
        usage, cov = sample_inputs()
        usage[0]['season'] = '2025'
        with self.assertRaises(ValueError):
            build(usage, cov)

    def test_date_mismatch_rejected(self):
        usage, cov = sample_inputs()
        usage[0]['game_date'] = '2026-08-31'
        with self.assertRaises(ValueError):
            build(usage, cov)

    def test_negative_workload_rejected(self):
        usage, cov = sample_inputs()
        usage[1]['targets'] = '-1'
        with self.assertRaises(ValueError):
            build(usage, cov)

    def test_short_team_calendar_has_no_replays(self):
        usage, cov = sample_inputs()
        self.assertEqual(build(usage[:6], cov[:6]), [])


if __name__ == '__main__':
    unittest.main()
