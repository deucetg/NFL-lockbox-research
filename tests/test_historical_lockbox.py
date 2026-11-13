import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from build_historical_lockbox import build

FIELDS=['game_id','play_id','season','week','game_date','home_team','away_team',
        'season_type','posteam','defteam','qtr','down','pass_attempt',
        'complete_pass','rush_attempt','receiver_player_id','receiver_player_name',
        'passer_player_id','passer_player_name','rusher_player_id','rusher_player_name',
        'receiving_yards','passing_yards','rushing_yards','yardline_100',
        'goal_to_go','two_point_attempt','epa','desc']


class HistoricalImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()
    def write(self,rows):
        source=self.path/'fixture.csv'
        with source.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        return source
    def test_separate_season_and_lookup(self):
        base=dict(season='2025',week='10',game_id='2025_10_NYG_WAS',
                  game_date='2025-11-09',home_team='WAS',away_team='NYG',
                  season_type='REG',yardline_100='15',goal_to_go='0',two_point_attempt='0')
        source=self.write([
            dict(base,play_id='1',posteam='NYG',defteam='WAS',qtr='1',down='3',
                 pass_attempt='1',complete_pass='1',rush_attempt='0',
                 receiver_player_id='rec1',receiver_player_name='Test Receiver',
                 passer_player_id='qb1',passer_player_name='Test QB',
                 receiving_yards='15',passing_yards='15',epa='0.5'),
            dict(base,play_id='2',posteam='NYG',defteam='WAS',qtr='4',down='1',
                 pass_attempt='0',rush_attempt='1',rusher_player_id='rb1',
                 rusher_player_name='Test RB',rushing_yards='6',epa='0.2',desc='END GAME'),
        ])
        audit=build(2025,source,self.path/'history')
        self.assertEqual(audit['game_count'],1)
        self.assertEqual(audit['play_rows'],2)
        self.assertEqual(audit['games_with_end_marker'],1)
        db=self.path/'history'/'2025'/'nfl_historical_2025.sqlite'
        with sqlite3.connect(db) as con:
            self.assertEqual(con.execute('select count(*) from plays').fetchone()[0],2)
            rec=con.execute("SELECT targets,receptions,third_down_targets,red_zone_targets,q1_receptions FROM player_game_usage WHERE role='receiving'").fetchone()
            self.assertEqual(rec,(1,1,1,1,1))
            self.assertEqual(con.execute('SELECT COUNT(*) FROM games').fetchone()[0],1)
        self.assertFalse((self.path/'history'/'2026').exists())
        h2h=(self.path/'history'/'2025'/'head_to_head_2025.csv').read_text()
        self.assertIn('NYG,WAS',h2h)
    def test_mixed_seasons_rejected(self):
        src=self.write([dict(game_id='2025_01_NYG_WAS',play_id='1',season='2026',
            week='1',game_date='2025-09-01',home_team='WAS',away_team='NYG',
            posteam='NYG',defteam='WAS',qtr='1',pass_attempt='1',rush_attempt='0')])
        with self.assertRaisesRegex(ValueError,'season other than 2025'):
            build(2025,src,self.path/'history')
    def test_duplicate_game_play_rejected(self):
        r=dict(game_id='2025_01_NYG_WAS',play_id='1',season='2025',week='1',
            game_date='2025-09-01',home_team='WAS',away_team='NYG',
            posteam='NYG',defteam='WAS',qtr='1',pass_attempt='1',rush_attempt='0')
        src=self.write([r,r])
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            build(2025,src,self.path/'history')


if __name__=='__main__':
    unittest.main()
