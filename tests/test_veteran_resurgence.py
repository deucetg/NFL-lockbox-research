
"""Unit tests for NFL LOCKBOX Step 3A workload recovery research."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_veteran_resurgence import build, veteran_status, validate_source_metadata


def fixtures():
    coverage = [
        {'game_id': f'g{i}', 'date': f'2026-09-{i:02d}',
         'teams': 'AAA,BBB', 'status': 'REQUIRES_BOX_SCORE_VALIDATION'}
        for i in range(1, 10)
    ]
    usage = []
    for i in range(1, 10):
        target = 5 if i <= 3 or i >= 7 else 2
        usage.append({
            'game_id': f'g{i}', 'season': '2026', 'team': 'AAA',
            'player_id': 'p1', 'player_name': 'Sample Receiver',
            'role': 'receiving', 'targets': str(target),
            'q1_targets': str(2 if i <= 3 or i >= 7 else 1),
            'red_zone_targets': str(1 if i >= 7 else 0),
            'third_down_targets': '1',
        })
    return usage, coverage


class ResurgenceTests(unittest.TestCase):
    def test_three_distinct_team_game_windows_and_recovery(self):
        u, c = fixtures()
        r = build(u, c)[0]
        self.assertEqual(r['older_game_ids'], 'g3|g2|g1')
        self.assertEqual(r['trough_game_ids'], 'g6|g5|g4')
        self.assertEqual(r['latest_game_ids'], 'g9|g8|g7')
        self.assertEqual(r['older_per_team_game'], 5.0)
        self.assertEqual(r['trough_per_team_game'], 2.0)
        self.assertEqual(r['latest_per_team_game'], 5.0)
        self.assertEqual(r['opportunity_pattern'], 'WORKLOAD_RECOVERY_PATTERN')
        self.assertEqual(r['veteran_eligibility'], 'TENURE_NOT_VERIFIED')
        self.assertEqual(r['verified_research_ready'], 'NO')

    def test_no_decline_means_no_recovery_pattern(self):
        u, c = fixtures()
        for p in u:
            p['targets'] = '5'
        r = build(u, c)[0]
        self.assertEqual(r['opportunity_pattern'], 'NO_RECOVERY_PATTERN')

    def test_missing_observation_is_not_a_zero(self):
        u, c = fixtures()
        u = [p for p in u if p['game_id'] != 'g8']
        r = build(u, c)[0]
        self.assertEqual(r['analysis_status'], 'INCOMPLETE_PLAYER_GAME_COVERAGE')
        self.assertEqual(r['missing_game_ids'], 'g8')
        self.assertEqual(r['latest_per_team_game'], '')
        self.assertEqual(r['opportunity_pattern'], 'NOT_EVALUATED')

    def test_duplicate_role_game_is_rejected(self):
        u, c = fixtures()
        with self.assertRaises(ValueError):
            build(u+[dict(u[0])], c)

    def test_eight_team_games_are_not_enough(self):
        u, c = fixtures()
        r = build(u[:-1], c[:-1])[0]
        self.assertEqual(r['analysis_status'], 'INSUFFICIENT_TEAM_GAMES')
        self.assertEqual(r['opportunity_pattern'], 'NOT_EVALUATED')

    def test_mixed_seasons_rejected(self):
        u, c = fixtures()
        u[0]['season'] = '2025'
        with self.assertRaises(ValueError):
            build(u, c)

    def test_veteran_proof_requires_source_and_verified_status(self):
        self.assertEqual(veteran_status('p1', {})[0], 'TENURE_NOT_VERIFIED')
        registry = {
            'p1': {
                'first_nfl_season': '2020',
                'verification_status': 'VERIFIED',
                'source_url': 'https://example.org/roster'
            }
        }
        self.assertEqual(
            veteran_status('p1', registry)[0],
            'VERIFIED_5_PLUS_SEASONS'
        )
        u, c = fixtures()
        r = build(u, c, registry)[0]
        self.assertEqual(r['veteran_eligibility'], 'VERIFIED_5_PLUS_SEASONS')
        self.assertEqual(r['verified_research_ready'], 'NO')
        registry['p1']['source_url'] = ''
        self.assertEqual(
            veteran_status('p1', registry)[0],
            'TENURE_NOT_VERIFIED'
        )

    def test_metadata_mismatch_rejected(self):
        u, c = fixtures()
        manifest = {
            'usage_rows': 8,
            'coverage_games': 9,
            'latest_usage_game_date': '2026-09-09',
            'latest_coverage_game_date': '2026-09-09',
            'source_files': [
                'upstream/player_game_usage.csv',
                'upstream/game_coverage.csv'
            ],
        }
        with self.assertRaises(ValueError):
            validate_source_metadata(manifest, u, c)

    def test_stale_source_date_rejected(self):
        u, c = fixtures()
        manifest = {
            'usage_rows': 9,
            'coverage_games': 9,
            'latest_usage_game_date': '2026-09-08',
            'latest_coverage_game_date': '2026-09-09',
            'source_files': [
                'upstream/player_game_usage.csv',
                'upstream/game_coverage.csv'
            ],
        }
        for row in u:
            row['game_date'] = (
                f"2026-09-{int(row['game_id'][1:]):02d}"
            )
        with self.assertRaises(ValueError):
            validate_source_metadata(manifest, u, c)

    def test_empty_numeric_is_unknown_not_zero(self):
        u, c = fixtures()
        u[-1]['targets'] = ''
        r = build(u, c)[0]
        self.assertEqual(r['analysis_status'], 'MISSING_METRIC_VALUE')
        self.assertEqual(r['opportunity_pattern'], 'NOT_EVALUATED')


if __name__ == '__main__':
    unittest.main()
