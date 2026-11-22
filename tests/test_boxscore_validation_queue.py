
"""Tests for Step 5A game validation queue and provenance restrictions."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_boxscore_validation_queue import build, validate_inputs


def fixtures():
    games = []
    quality = []
    participation = []
    for k in range(1, 9):
        gid = f'g{k}'
        games.append({
            'game_id': gid,
            'date': f'2026-09-{k:02d}',
            'teams': 'AAA,BBB',
            'plays': '101',
            'status': 'REQUIRES_BOX_SCORE_VALIDATION'
        })
        quality.append({
            'game_id': gid,
            'date': f'2026-09-{k:02d}',
            'home_team': 'AAA',
            'away_team': 'BBB',
            'pbp_rows': '101',
            'end_game_markers': '1',
            'duplicate_play_keys': '0',
            'other_pass_attempts_without_receiver_id': '2'
        })
        participation.append({
            'game_id': gid,
            'team': 'AAA',
            'player_id': 'p1',
            'reconciled_status': (
                'UNRESOLVED_NO_SNAP_MATCH'
                if k == 1 else 'OBSERVED_OFFENSIVE_EVENT'
            )
        })

    def replay(past, heldout, status='PROVISIONAL_UNVALIDATED_GAMES'):
        return {
            'team': 'AAA',
            'player_id': 'p1',
            'role': 'receiving',
            'asof_game': past[-1],
            'heldout_game': heldout,
            'previous_three_games': '|'.join(past[:3]),
            'recent_three_games': '|'.join(past[3:]),
            'backtest_status': status,
            'research_eligible': 'NO'
        }

    reps = [
        replay([f'g{k}' for k in range(1, 7)], 'g7'),
        replay([f'g{k}' for k in range(2, 8)], 'g8')
    ]
    return games, quality, participation, reps


class QueueTests(unittest.TestCase):

    def test_dependency_ranking_and_auditable_counts(self):
        g, q, p, r = fixtures()
        rows = build(g, q, p, r)
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0]['game_id'], 'g2')
        self.assertEqual(
            rows[0]['complete_replays_depending_on_game'], 2
        )
        self.assertEqual(
            rows[-1]['complete_replays_depending_on_game'], 1
        )
        self.assertEqual(
            sum(x['complete_replays_depending_on_game'] for x in rows),
            14
        )

    def test_no_game_is_auto_validated(self):
        g, q, p, r = fixtures()
        rows = build(g, q, p, r)
        self.assertTrue(all(
            x['external_box_score_evidence'] == 'NOT_PROVIDED'
            for x in rows
        ))
        self.assertTrue(all(
            x['validation_readiness'] == 'AWAITING_INDEPENDENT_BOX_SCORE'
            for x in rows
        ))

    def test_source_claim_of_validation_is_not_proof(self):
        g, q, p, r = fixtures()
        g[0]['status'] = 'BOX_SCORE_VALIDATED'
        row = next(
            x for x in build(g, q, p, r)
            if x['game_id'] == 'g1'
        )
        self.assertIn(
            'SOURCE_MARKED_VALIDATED_PROOF_NOT_SUPPLIED',
            row['quality_warnings']
        )
        self.assertEqual(
            row['external_box_score_evidence'], 'NOT_PROVIDED'
        )

    def test_missing_quality_is_reported_not_imputed(self):
        g, q, p, r = fixtures()
        row = next(
            x for x in build(g, q[:-1], p, r)
            if x['game_id'] == 'g8'
        )
        self.assertIn(
            'QUALITY_REPORT_MISSING', row['quality_warnings']
        )
        self.assertEqual(row['pbp_rows'], '')

    def test_receiver_id_gap_and_participation_are_flags(self):
        g, q, p, r = fixtures()
        row = next(
            x for x in build(g, q, p, r)
            if x['game_id'] == 'g1'
        )
        self.assertIn(
            'RECEIVER_ID_GAPS', row['quality_warnings']
        )
        self.assertIn(
            'PARTICIPATION_UNRESOLVED', row['quality_warnings']
        )
        self.assertEqual(
            row['unresolved_participation_rows'], 1
        )

    def test_duplicate_source_game_rejected(self):
        g, q, p, r = fixtures()
        with self.assertRaises(ValueError):
            build(g + [dict(g[0])], q, p, r)

    def test_duplicate_participation_identity_rejected(self):
        g, q, p, r = fixtures()
        with self.assertRaises(ValueError):
            build(g, q, p + [dict(p[0])], r)

    def test_duplicate_replay_rejected(self):
        g, q, p, r = fixtures()
        with self.assertRaises(ValueError):
            build(g, q, p, r + [dict(r[0])])

    def test_malformed_replay_rejected(self):
        g, q, p, r = fixtures()
        r[0]['recent_three_games'] = 'g4|g4|g6'
        with self.assertRaises(ValueError):
            build(g, q, p, r)

    def test_missing_walkforward_provenance_rejected(self):
        manifest = {
            'source_type': 'successful_main_build_artifact',
            'upstream_run_id': '123',
            'upstream_head_sha': 'abc',
            'usage_rows': 0,
            'coverage_games': 0,
            'source_files': [
                'upstream/player_game_usage.csv',
                'upstream/game_coverage.csv'
            ],
            'latest_usage_game_date': '',
            'latest_coverage_game_date': ''
        }
        audit = {
            'upstream_run_id': '124',
            'upstream_head_sha': 'abc',
            'historical_replay_attempts': 2
        }
        with self.assertRaises(ValueError):
            validate_inputs(manifest, audit, [], [], [{}, {}])

    def test_incomplete_replay_is_not_validation_unblock_credit(self):
        g, q, p, r = fixtures()
        r[0]['backtest_status'] = 'INCOMPLETE_PLAYER_GAME_COVERAGE'
        rows = build(g, q, p, r)
        self.assertEqual(
            sum(x['complete_replays_depending_on_game'] for x in rows),
            7
        )
        self.assertEqual(
            sum(x['incomplete_replays_touching_game'] for x in rows),
            7
        )


if __name__ == '__main__':
    unittest.main()
