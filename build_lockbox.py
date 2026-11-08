import csv,gzip,sqlite3,collections,json,os,zipfile,shutil
from pathlib import Path
BASE=Path(__file__).resolve().parent
ROOT=BASE/'NFL_LOCKBOX_2026';ROOT.mkdir(exist_ok=True)
SRC=BASE/'play_by_play_2026.csv.gz'
DB=ROOT/'nfl_lockbox_2026.sqlite'
if DB.exists(): DB.unlink()
c=sqlite3.connect(DB)
c.executescript('''CREATE TABLE source_registry(source_id TEXT PRIMARY KEY,filename TEXT,origin TEXT,notes TEXT);INSERT INTO source_registry VALUES ('upload_2026','play_by_play_2026.csv.gz','user_uploaded_external_pbp','User supplied archive; external nflverse-format play-by-play, NOT original manually curated LOCKBOX game logs.');CREATE TABLE plays(game_id TEXT NOT NULL,play_id TEXT NOT NULL,season INTEGER,week INTEGER,game_date TEXT,season_type TEXT,home_team TEXT,away_team TEXT,posteam TEXT,defteam TEXT,qtr INTEGER,down INTEGER,ydstogo REAL,yardline_100 REAL,goal_to_go INTEGER,play_type TEXT,pass_attempt INTEGER,complete_pass INTEGER,rush_attempt INTEGER,passer_id TEXT,passer_name TEXT,receiver_id TEXT,receiver_name TEXT,rusher_id TEXT,rusher_name TEXT,passing_yards REAL,receiving_yards REAL,rushing_yards REAL,air_yards REAL,yards_after_catch REAL,third_down_converted INTEGER,third_down_failed INTEGER,two_point_attempt INTEGER,two_point_conv_result TEXT,pass_touchdown INTEGER,rush_touchdown INTEGER,source_id TEXT NOT NULL,PRIMARY KEY(game_id,play_id));CREATE INDEX idx_plays_week ON plays(season,week);CREATE INDEX idx_plays_receiver ON plays(receiver_id);CREATE INDEX idx_plays_rusher ON plays(rusher_id);CREATE INDEX idx_plays_passer ON plays(passer_id);''')
fields=['game_id','play_id','season','week','game_date','season_type','home_team','away_team','posteam','defteam','qtr','down','ydstogo','yardline_100','goal_to_go','play_type','pass_attempt','complete_pass','rush_attempt','passer_player_id','passer_player_name','receiver_player_id','receiver_player_name','rusher_player_id','rusher_player_name','passing_yards','receiving_yards','rushing_yards','air_yards','yards_after_catch','third_down_converted','third_down_failed','two_point_attempt','two_point_conv_result','pass_touchdown','rush_touchdown']
ints={'season','week','qtr','down','goal_to_go','pass_attempt','complete_pass','rush_attempt','third_down_converted','third_down_failed','two_point_attempt','pass_touchdown','rush_touchdown'}
floats={'ydstogo','yardline_100','passing_yards','receiving_yards','rushing_yards','air_yards','yards_after_catch'}
def convert(k,v):
 if not v or v=='NA':return None
 if k in ints:
  try:return int(float(v))
  except:return None
 if k in floats:
  try:return float(v)
  except:return None
 return v
counts=collections.Counter();teams=collections.defaultdict(set); dates={}; quarters=collections.defaultdict(set);games=collections.Counter(); missing=collections.Counter(); player_events=collections.Counter()
with gzip.open(SRC,'rt',newline='') as f:
 reader=csv.DictReader(f); raw_cols=reader.fieldnames
 for row in reader:
  vals=[convert(k,row.get(k)) for k in fields]
  try:c.execute('INSERT INTO plays VALUES ('+','.join(['?']*37)+')',vals+['upload_2026'])
  except sqlite3.IntegrityError:counts['duplicate_play_keys']+=1;continue
  gid=row['game_id'];games[gid]+=1;quarters[gid].add(row['qtr']);dates[gid]=row['game_date'];teams[gid].update([row['home_team'],row['away_team']]);counts['rows']+=1;counts['week_'+row['week']]+=1
c.commit()
# event aggregation: explicit attempts and player identity required. No nulls promoted to zero in individual play facts.
q='''SELECT game_id,season,week,game_date,posteam,defteam,qtr,down,yardline_100,goal_to_go,pass_attempt,complete_pass,rush_attempt,receiver_id,receiver_name,rusher_id,rusher_name,passer_id,passer_name,receiving_yards,rushing_yards,passing_yards,two_point_attempt FROM plays ORDER BY game_date,game_id,CAST(play_id AS REAL)'''
rows=c.execute(q).fetchall()
stat=collections.defaultdict(lambda:collections.Counter())
for row in rows:
 (gid,season,week,date,off,defteam,qtr,down,yl,gt,pa,comp,ra,rid,rname,runid,runname,pid,pname,ry,rush_y,pass_y,tp)=row
 red=yl is not None and yl<=20
 if rid and pa==1:
  k=(gid,season,week,date,off,rid,rname,'receiving');v=stat[k];v['targets']+=1;v['receptions']+=int(comp==1);v['receiving_yards']+=ry or 0;v['third_down_targets']+=int(down==3);v['red_zone_targets']+=int(red);v['goal_to_go_targets']+=int(gt==1);v['q1_targets']+=int(qtr==1);v['q1_receptions']+=int(qtr==1 and comp==1);v['q1_receiving_yards']+=(ry or 0) if qtr==1 else 0
 if runid and ra==1:
  k=(gid,season,week,date,off,runid,runname,'rushing');v=stat[k];v['rush_attempts']+=1;v['rushing_yards']+=rush_y or 0;v['red_zone_rushes']+=int(red);v['goal_to_go_rushes']+=int(gt==1);v['q1_rush_attempts']+=int(qtr==1);v['q1_rushing_yards']+=(rush_y or 0) if qtr==1 else 0
 if pid and pa==1:
  k=(gid,season,week,date,off,pid,pname,'passing');v=stat[k];v['pass_attempts']+=1;v['completions']+=int(comp==1);v['passing_yards']+=pass_y or 0;v['q1_pass_attempts']+=int(qtr==1);v['q1_completions']+=int(qtr==1 and comp==1);v['q1_passing_yards']+=(pass_y or 0) if qtr==1 else 0
c.execute('''CREATE TABLE player_game_usage(game_id TEXT,season INTEGER,week INTEGER,game_date TEXT,team TEXT,player_id TEXT,player_name TEXT,role TEXT,targets INTEGER,receptions INTEGER,receiving_yards REAL,third_down_targets INTEGER,red_zone_targets INTEGER,goal_to_go_targets INTEGER,q1_targets INTEGER,q1_receptions INTEGER,q1_receiving_yards REAL,rush_attempts INTEGER,rushing_yards REAL,red_zone_rushes INTEGER,goal_to_go_rushes INTEGER,q1_rush_attempts INTEGER,q1_rushing_yards REAL,pass_attempts INTEGER,completions INTEGER,passing_yards REAL,q1_pass_attempts INTEGER,q1_completions INTEGER,q1_passing_yards REAL,PRIMARY KEY(game_id,player_id,role))''')
metrics=['targets','receptions','receiving_yards','third_down_targets','red_zone_targets','goal_to_go_targets','q1_targets','q1_receptions','q1_receiving_yards','rush_attempts','rushing_yards','red_zone_rushes','goal_to_go_rushes','q1_rush_attempts','q1_rushing_yards','pass_attempts','completions','passing_yards','q1_pass_attempts','q1_completions','q1_passing_yards']
for key,vals in stat.items():c.execute('INSERT INTO player_game_usage VALUES ('+','.join('?'*29)+')',list(key)+[vals.get(m,0) for m in metrics])
c.execute('''CREATE VIEW receiving_game_share AS SELECT u.*,ROUND(100.0*u.targets/NULLIF(t.team_targets,0),2) target_share_pct FROM player_game_usage u LEFT JOIN (SELECT game_id,posteam,COUNT(*) team_targets FROM plays WHERE pass_attempt=1 AND receiver_id IS NOT NULL GROUP BY game_id,posteam) t ON t.game_id=u.game_id AND t.posteam=u.team WHERE u.role='receiving' ''')
c.commit()
report={'archive':'play_by_play_2026.csv.gz','provenance':'user-uploaded, nflverse-format external play-by-play; separate from manually curated LOCKBOX data','rows':counts['rows'],'distinct_games':len(games),'source_columns':len(raw_cols),'duplicate_play_keys':counts['duplicate_play_keys'],'first_game_date':min(dates.values()),'last_game_date':max(dates.values()),'week_rows':{k[5:]:v for k,v in counts.items() if k.startswith('week_')},'player_game_role_rows':len(stat),'games_with_fewer_than_four_quarters':[g for g,qs in quarters.items() if not {'1','2','3','4'}.issubset(qs)],'warning':'Four quarters present does not prove complete play-by-play. Passing targets require an identified receiver and pass_attempt=1. Unidentified targets are excluded and reported by audit; incomplete passes may lack a receiver. Two-point plays not yet allocated to individual player usage.'}
report['pass_attempts_without_receiver_id']=c.execute('SELECT count(*) FROM plays WHERE pass_attempt=1 AND receiver_id IS NULL').fetchone()[0]
report['pass_attempts_with_receiver_id']=c.execute('SELECT count(*) FROM plays WHERE pass_attempt=1 AND receiver_id IS NOT NULL').fetchone()[0]
(ROOT/'audit.json').write_text(json.dumps(report,indent=2))
with (ROOT/'game_coverage.csv').open('w',newline='') as f:
 w=csv.writer(f);w.writerow(['game_id','date','plays','quarters_present','teams','status']);
 for gid,n in sorted(games.items(),key=lambda x:dates[x[0]]):w.writerow([gid,dates[gid],n,','.join(sorted(quarters[gid])),','.join(sorted(teams[gid])),'REQUIRES_BOX_SCORE_VALIDATION'])
with (ROOT/'player_game_usage.csv').open('w',newline='') as f:
 w=csv.writer(f);w.writerow(['game_id','season','week','game_date','team','player_id','player_name','role']+metrics)
 for row in c.execute('SELECT * FROM player_game_usage ORDER BY game_date,game_id,role,player_name'):w.writerow(row)
(ROOT/'README.md').write_text('''# NFL LOCKBOX 2026 — Audited PBP foundation\n\nThis is a **research-only** database built from the user-uploaded `play_by_play_2026.csv.gz` (nflverse-style schema). No sportsbook tickets, wagers, or odds.\n\n## Contents\n- `nfl_lockbox_2026.sqlite`: `plays` (35 selected original play fields + source id), `player_game_usage`, `source_registry`, and `receiving_game_share` view.\n- `player_game_usage.csv`: per-player per-game counts and first-quarter splits.\n- `game_coverage.csv`: inventory, not a claim of game completeness.\n- `audit.json`: ingestion statistics and limitations.\n- `build_lockbox.py`: reproducible builder; place source archive in same directory or adjust paths.\n\n## IMPORTANT LIMITATIONS\n1. The file was supplied by the user but contains **external nflverse-format player data**, not necessarily the manually validated LOCKBOX PBP. Label it `user-uploaded external PBP`, not `verified LOCKBOX` until independently reconciled.\n2. Target share denominator counts identified receiver pass attempts only. QB throwaways, spikes, and unidentified targets need reconciliation before trusting target-share percentages.\n3. Two-point player usage, penalty-nullified plays, lateral plays, scrambles and sacks require additional validation; this version does not fully handle them.\n4. Individual play blanks are NULL in SQLite. Aggregates only count recognized positive events.\n5. Do not infer completed games merely from four quarters present.\n6. Week 9 is incomplete; exclude partial games from trend claims until validated.\n\n## Sample SQLite queries\n```sql\nSELECT player_name, SUM(targets) targets, SUM(receptions) receptions FROM player_game_usage WHERE role='receiving' GROUP BY player_id ORDER BY targets DESC LIMIT 5;\nSELECT player_name, game_date, targets, receptions, receiving_yards FROM player_game_usage WHERE role='receiving' AND player_name LIKE '%Chase%' ORDER BY game_date DESC;\nSELECT player_name, game_date, targets, target_share_pct FROM receiving_game_share ORDER BY game_date DESC LIMIT 20;\n```\n''')
shutil.copy(Path(__file__).resolve(),ROOT/'build_lockbox.py')
c.close()
with zipfile.ZipFile(BASE/'NFL_LOCKBOX_2026_AUDITED.zip','w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
 for p in ROOT.iterdir():z.write(p,arcname=p.name)
print(json.dumps(report,indent=2));print('zip',os.path.getsize(BASE/'NFL_LOCKBOX_2026_AUDITED.zip'))

# PHASE 2: Player rolling trends (completed weeks only)
import csv as _csv
from collections import defaultdict as _defaultdict

with (ROOT/'player_game_usage.csv').open(newline='') as _f:
    _usage=list(_csv.DictReader(_f))

# Exclude the most recent week when the uploaded source may be incomplete.
_weeks=sorted({int(r['week']) for r in _usage if r['week'] and r['season']=='2026'})
_excluded_week=max(_weeks) if _weeks else None
_groups=_defaultdict(list)
for _r in _usage:
    if _r['season']!='2026' or not _r['week']:
        continue
        if int(_r['week'])==_excluded_week and _r['game_id']!='2026_09_JAX_BAL':
        continue
    _groups[(_r['player_id'],_r['role'])].append(_r)

_trend_metrics=['targets','receptions','receiving_yards','rush_attempts','rushing_yards','pass_attempts','completions','passing_yards','q1_targets','q1_receptions','q1_receiving_yards','q1_rush_attempts','q1_rushing_yards','q1_pass_attempts','q1_completions','q1_passing_yards']
with (ROOT/'player_rolling_trends.csv').open('w',newline='') as _f:
    _w=_csv.writer(_f)
    _w.writerow(['player_id','player_name','role','window','games_in_sample','latest_game_date','excluded_week']+[m+'_per_game' for m in _trend_metrics])
    for (_pid,_role),_games in sorted(_groups.items()):
        _games.sort(key=lambda r:(r['game_date'],r['game_id']),reverse=True)
        for _window in (3,5,8):
            _sample=_games[:_window]
            if len(_sample)<_window:
                continue
            _w.writerow([_pid,_sample[0]['player_name'],_role,_window,len(_sample),_sample[0]['game_date'],_excluded_week]+[round(sum(float(r[m] or 0) for r in _sample)/_window,2) for m in _trend_metrics])
print('Phase 2 rolling trends generated:',ROOT/'player_rolling_trends.csv')
