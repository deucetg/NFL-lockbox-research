
import sys
import unittest
from pathlib import Path

# Allow this test to run directly from the repository tests/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_opportunity_acceleration import build

class AccelerationTests(unittest.TestCase):
    def setup_data(self):
        coverage=[dict(game_id=f'g{i}',date=f'2026-10-{i:02d}',teams='AAA,BBB',status='REQUIRES_BOX_SCORE_VALIDATION') for i in range(1,11)]
        usage=[dict(game_id=f'g{i}',team='AAA',player_id='p1',player_name='Player One',role='receiving',targets=str(i),receptions='1',receiving_yards='10',q1_targets='1',red_zone_targets='0',third_down_targets='0') for i in range(1,11)]
        return usage,coverage

    def test_non_overlapping_l3(self):
        u,c=self.setup_data()
        r=next(x for x in build(u,c) if x['metric']=='targets' and x['window']=='L3_vs_previous_3')
        self.assertEqual(r['current_per_team_game'],9.0)
        self.assertEqual(r['previous_per_team_game'],6.0)
        self.assertEqual(r['percent_change'],50.0)
        self.assertEqual(r['trend_status'],'INCREASING')
        self.assertEqual(len(set(r['current_game_ids'].split('|'))&set(r['previous_game_ids'].split('|'))),0)

    def test_missing_game_not_zero(self):
        u,c=self.setup_data()
        u=[r for r in u if r['game_id']!='g9']
        r=next(x for x in build(u,c) if x['metric']=='targets' and x['window']=='L3_vs_previous_3')
        self.assertEqual(r['trend_status'],'INCOMPLETE_PLAYER_GAME_COVERAGE')
        self.assertEqual(r['percent_change'],'')
        self.assertEqual(r['missing_current_game_ids'],'g9')

    def test_zero_baseline(self):
        u,c=self.setup_data()
        for r in u:
            if int(r['game_id'][1:])<=7:
                r['targets']='0'
        r=next(x for x in build(u,c) if x['metric']=='targets' and x['window']=='L3_vs_previous_3')
        self.assertEqual(r['trend_status'],'ZERO_PREVIOUS_BASELINE')
        self.assertEqual(r['percent_change'],'')

    def test_duplicate_rejected(self):
        u,c=self.setup_data()
        with self.assertRaises(ValueError):
            build(u+[dict(u[0])],c)

    def test_l5_window(self):
        u,c=self.setup_data()
        r=next(x for x in build(u,c) if x['metric']=='targets' and x['window']=='L5_vs_previous_5')
        self.assertEqual(r['current_per_team_game'],8.0)
        self.assertEqual(r['previous_per_team_game'],3.0)

if __name__=='__main__':
    unittest.main()
