import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from build_cross_season_matchups import build, METRICS


class CrossSeasonTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.history=self.base/'history'
        self.output=self.base/'output'
        for year in (2023,2024,2025):
            d=self.history/str(year)
            d.mkdir(parents=True,exist_ok=True)
            c=sqlite3.connect(d/f'nfl_historical_{year}.sqlite')
            c.execute('CREATE TABLE source_registry(season INTEGER,source_url TEXT,source_sha256 TEXT,provenance TEXT)')
            c.execute('INSERT INTO source_registry VALUES(?,?,?,?)',
                      (year,'fixture','test','EXTERNAL_HISTORICAL_NFLVERSE_PBP_NOT_USER_VERIFIED'))
            c.execute('''CREATE TABLE games(season INTEGER,game_id TEXT PRIMARY KEY,week INTEGER,
                game_date TEXT,home_team TEXT,away_team TEXT,season_type TEXT,
                play_rows INTEGER,quarters_present TEXT,end_game_marker_present INTEGER,
                coverage_status TEXT)''')
            columns='game_id TEXT,season INTEGER,week INTEGER,game_date TEXT,team TEXT,player_id TEXT,player_name TEXT,role TEXT,'
            columns+=','.join(m+(' REAL' if 'yards' in m else ' INTEGER') for m in METRICS)
            c.execute('CREATE TABLE player_game_usage('+columns+')')
            if year == 2023:
                c.execute('INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (year,f'{year}_01_MIA_NYJ',1,f'{year}-09-01','NYJ','MIA','REG',100,'1,2,3,4',1,'END_MARKER_PRESENT_UNVALIDATED'))
            if year in (2024,2025):
                gid=f'{year}_12_NYG_WAS'
                c.execute('INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (year,gid,12,f'{year}-11-25','WAS','NYG','REG',100,'1,2,3,4',1,'END_MARKER_PRESENT_UNVALIDATED'))
                for name in ('Name A' if year == 2024 else 'Name B',):
                    v=[gid,year,12,f'{year}-11-25','NYG','player123',name,'receiving']
                    values={m:0 for m in METRICS}
                    values.update(targets=5,receptions=3,receiving_yards=40,
                                  q1_targets=1,q1_receptions=1,q1_receiving_yards=11)
                    c.execute('INSERT INTO player_game_usage VALUES ('+','.join('?'*(8+len(METRICS)))+')',
                              v+[values[m] for m in METRICS])
            c.commit()
            c.close()
        self.schedule=self.base/'upcoming.csv'
        with self.schedule.open('w',newline='') as f:
            w=csv.writer(f);w.writerow(['game_id','game_date','week','home_team','away_team'])
            w.writerow(['2026_11_NYG_WAS','2026-11-15',11,'WAS','NYG'])
            w.writerow(['2026_11_NYG_WAS','2026-11-15',11,'WAS','NYG'])  # repeated from matchup matrix
            w.writerow(['2026_11_SEA_LV','2026-11-15',11,'LV','SEA'])
        self.current=self.base/'player_game_usage.csv'
        with self.current.open('w',newline='') as f:
            w=csv.writer(f);w.writerow(['season','team','player_id'])
            w.writerow(['2026','NYG','player123'])
    def tearDown(self):
        self.temp.cleanup()
    def test_output_season_isolation_and_prior_games(self):
        result=build(self.history,self.output,self.schedule,'2026-11-12',8,self.current)
        self.assertEqual(result['future_2026_matchups'],2)
        self.assertEqual(result['historical_head_to_head_games'],2)
        self.assertEqual(result['historical_player_game_role_rows'],2)
        with (self.output/'historical_player_matchup_summaries.csv').open() as f:
            records=list(csv.DictReader(f))
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['recorded_h2h_player_game_rows'],'2')
        self.assertEqual(records[0]['historical_seasons_observed'],'2024,2025')
        self.assertEqual(records[0]['observed_on_matching_team_in_2026_usage_snapshot'],'yes')
        self.assertEqual(records[0]['avg_receiving_yards_per_recorded_row'],'40.0')
        self.assertEqual(records[0]['player_name'],'Name B')
        with (self.output/'head_to_head_2023_2025_for_2026.csv').open() as f:
            h2h=list(csv.DictReader(f))
        self.assertEqual(len(h2h),3)  # 2 historical games + 1 no-H2H placeholder
        self.assertTrue(any(r['coverage_status']=='NO_H2H_2023_2025' for r in h2h))
        self.assertFalse((self.history/'2026').exists())
        self.assertFalse((self.base/'NFL_LOCKBOX_2026').exists())
    def test_missing_history_is_error(self):
        (self.history/'2023'/'nfl_historical_2023.sqlite').unlink()
        with self.assertRaises(FileNotFoundError):
            build(self.history,self.output,self.schedule,'2026-11-12',8)
    def test_conflicting_schedule_is_error(self):
        with self.schedule.open('a',newline='') as f:
            csv.writer(f).writerow(['2026_11_NYG_WAS','2026-11-15',11,'NYG','WAS'])
        with self.assertRaisesRegex(ValueError,'Conflicting schedule'):
            build(self.history,self.output,self.schedule,'2026-11-12',8)
    def test_current_snapshot_not_implied_active(self):
        result=build(self.history,self.output,self.schedule,'2026-11-12',8)
        self.assertIsNone(result['current_usage_snapshot'])
        with (self.output/'historical_player_matchup_summaries.csv').open() as f:
            records=list(csv.DictReader(f))
        self.assertEqual(records[0]['observed_on_matching_team_in_2026_usage_snapshot'],'not_checked')

if __name__=='__main__':
    unittest.main()
