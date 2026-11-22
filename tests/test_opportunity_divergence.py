
"""Unit tests for NFL LOCKBOX Step 2A divergence research builder."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_opportunity_divergence import build, validate_manifest


def sample(metric, previous, current, *, missing=False):
    return {
        "team": "AAA", "player_id": "p1", "player_name": "Player One",
        "role": "receiving", "window": "L3_vs_previous_3", "metric": metric,
        "current_game_ids": "g6|g5|g4", "previous_game_ids": "g3|g2|g1",
        "current_observed_games": "2" if missing else "3",
        "previous_observed_games": "3",
        "missing_current_game_ids": "g5" if missing else "",
        "missing_previous_game_ids": "",
        "current_per_team_game": "" if missing else str(current),
        "previous_per_team_game": str(previous),
        "trend_status": "INCOMPLETE_PLAYER_GAME_COVERAGE" if missing else "INCREASING",
        "source_game_statuses": "REQUIRES_BOX_SCORE_VALIDATION",
        "verified_game_completeness": "NOT_VERIFIED",
    }


class DivergenceTests(unittest.TestCase):
    def test_opportunity_up_production_lag(self):
        row = build([sample("targets", 3, 4), sample("receiving_yards", 50, 45)])[0]
        self.assertEqual(row["divergence_signal"], "OPPORTUNITY_UP_PRODUCTION_LAG")
        self.assertEqual(row["analysis_status"], "PROVISIONAL_SOURCE_VALIDATION")
        self.assertEqual(row["verified_research_ready"], "NO")

    def test_opportunity_down_production_up(self):
        row = build([sample("targets", 4, 3), sample("receiving_yards", 30, 50)])[0]
        self.assertEqual(row["divergence_signal"], "OPPORTUNITY_DOWN_PRODUCTION_UP")

    def test_missing_player_game_not_zero(self):
        row = build([sample("targets", 3, 4, missing=True), sample("receiving_yards", 50, 45, missing=True)])[0]
        self.assertEqual(row["analysis_status"], "INCOMPLETE_PLAYER_GAME_COVERAGE")
        self.assertEqual(row["divergence_signal"], "NOT_EVALUATED")
        self.assertEqual(row["opportunity_delta"], "")

    def test_zero_volume_baseline(self):
        row = build([sample("targets", 0, 3), sample("receiving_yards", 0, 50)])[0]
        self.assertEqual(row["analysis_status"], "ZERO_OPPORTUNITY_BASELINE")
        self.assertEqual(row["opportunity_percent_change"], "")

    def test_crossed_window_rejected(self):
        v = sample("targets", 3, 4)
        p = sample("receiving_yards", 50, 45)
        p["previous_game_ids"] = "g3|g2|g4"
        with self.assertRaises(ValueError):
            build([v, p])

    def test_duplicate_metric_rejected(self):
        v = sample("targets", 3, 4)
        with self.assertRaises(ValueError):
            build([v, dict(v)])

    def test_manifest_cannot_use_root_snapshots(self):
        manifest = {
            "source_type": "committed_repository_snapshot",
            "upstream_run_id": "42", "upstream_head_sha": "abc",
            "latest_usage_game_date": "2026-11-19",
            "latest_coverage_game_date": "2026-11-19",
            "coverage_status_counts": {"REQUIRES_BOX_SCORE_VALIDATION": 151},
        }
        with self.assertRaises(ValueError):
            validate_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
