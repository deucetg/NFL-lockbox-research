#!/usr/bin/env python3
"""Build a SEPARATE nflverse-derived NFL historical research database.

Research provenance: external historical nflverse PBP; not user-verified LOCKBOX
player statistics. Never append these games to current-season player hit rates.
Python standard library only. Run `--season 2025` first.
"""
import argparse
import collections
import csv
import gzip
import hashlib
import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

PBP_URL = 'https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.csv.gz'
REQUIRED = {'game_id', 'play_id', 'season', 'week', 'game_date', 'home_team',
            'away_team', 'posteam', 'defteam', 'qtr', 'pass_attempt', 'rush_attempt'}
METRICS = [
    'targets','receptions','receiving_yards','third_down_targets','red_zone_targets',
    'goal_to_go_targets','q1_targets','q1_receptions','q1_receiving_yards',
    'rush_attempts','rushing_yards','red_zone_rushes','goal_to_go_rushes',
    'q1_rush_attempts','q1_rushing_yards',
    'pass_attempts','completions','passing_yards',
    'q1_pass_attempts','q1_completions','q1_passing_yards',
]


def integer(value):
    try:
        return int(float(value)) if str(value).strip() not in ('', 'NA', 'None') else 0
    except (ValueError, TypeError):
        return 0


def number(value):
    try:
        return float(value) if str(value).strip() not in ('', 'NA', 'None') else 0.0
    except (ValueError, TypeError):
        return 0.0


def write_csv(path, headings, rows):
    with path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(headings)
        writer.writerows(rows)


def download_season(season, dest):
    url = PBP_URL.format(season=season)
    req = Request(url, headers={'User-Agent': 'NFL-LOCKBOX-Historical/1.0'})
    with urlopen(req, timeout=180) as response, dest.open('wb') as f:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    if dest.stat().st_size < 1024:
        raise ValueError('Historical PBP download unexpectedly small')
    return url


def build(season, source_file, root, source_url=None):
    out = Path(root) / str(season)
    out.mkdir(parents=True, exist_ok=True)
    src = Path(source_file)
    digest = hashlib.sha256()
    with src.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)

    db_path = out / f'nfl_historical_{season}.sqlite'
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.execute('PRAGMA journal_mode=OFF')
    con.execute('PRAGMA synchronous=OFF')
    con.executescript('''
        CREATE TABLE source_registry(season INTEGER PRIMARY KEY, source_url TEXT,
            source_sha256 TEXT, provenance TEXT);
        CREATE TABLE plays(
            season INTEGER NOT NULL, game_id TEXT NOT NULL, play_id TEXT NOT NULL,
            week INTEGER, game_date TEXT, home_team TEXT, away_team TEXT,
            posteam TEXT, defteam TEXT, qtr INTEGER, down INTEGER,
            yardline_100 REAL, goal_to_go INTEGER, pass_attempt INTEGER,
            complete_pass INTEGER, rush_attempt INTEGER,
            passer_id TEXT, receiver_id TEXT, rusher_id TEXT,
            passing_yards REAL, receiving_yards REAL, rushing_yards REAL,
            epa REAL, PRIMARY KEY(game_id, play_id));
        CREATE INDEX idx_hist_games ON plays(season,game_id);
        CREATE INDEX idx_hist_receiver ON plays(receiver_id);
        CREATE TABLE games(season INTEGER,game_id TEXT PRIMARY KEY,week INTEGER,
            game_date TEXT,home_team TEXT,away_team TEXT,season_type TEXT,
            play_rows INTEGER,quarters_present TEXT,end_game_marker_present INTEGER,
            coverage_status TEXT);
        CREATE TABLE player_game_usage(game_id TEXT,season INTEGER,week INTEGER,
            game_date TEXT,team TEXT,player_id TEXT,player_name TEXT,role TEXT,
            targets INTEGER,receptions INTEGER,receiving_yards REAL,
            third_down_targets INTEGER,red_zone_targets INTEGER,goal_to_go_targets INTEGER,
            q1_targets INTEGER,q1_receptions INTEGER,q1_receiving_yards REAL,
            rush_attempts INTEGER,rushing_yards REAL,red_zone_rushes INTEGER,
            goal_to_go_rushes INTEGER,q1_rush_attempts INTEGER,q1_rushing_yards REAL,
            pass_attempts INTEGER,completions INTEGER,passing_yards REAL,
            q1_pass_attempts INTEGER,q1_completions INTEGER,q1_passing_yards REAL,
            PRIMARY KEY(game_id,player_id,role));
    ''')
    con.execute('INSERT INTO source_registry VALUES (?,?,?,?)',
        (season, source_url or str(src), digest.hexdigest(),
         'EXTERNAL_HISTORICAL_NFLVERSE_PBP_NOT_USER_VERIFIED'))
    games = {}
    # Aggregate by stable player ID, NOT display name: nflverse names can vary
    # across plays for the same player in the same game.
    stats = collections.defaultdict(collections.Counter)
    player_names = collections.defaultdict(collections.Counter)
    player_game_teams = {}

    def usage_key(gid, week, date, team, player_id, name, role):
        slot = (gid, player_id, role)
        prior = player_game_teams.setdefault(slot, team)
        if prior != team:
            raise ValueError(
                f'Conflicting teams for player ID {player_id} in {gid} ({role}): ' 
                f'{prior} versus {team}; investigate source rather than merge'
            )
        key = (gid, season, week, date, team, player_id, role)
        display = (name or '').strip()
        if display:
            player_names[key][display] += 1
        return key

    defense = collections.defaultdict(collections.Counter)
    duplicate = 0
    missing_receiver = 0
    discarded_two_point = 0
    total = 0
    open_func = gzip.open if src.suffix == '.gz' else open
    try:
        with open_func(src, 'rt', encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            missing = REQUIRED - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f'Missing required nflverse fields: {sorted(missing)}')
            for r in reader:
                if integer(r.get('season')) != season:
                    raise ValueError(f'Source contains season other than {season}: {r.get("season")}')
                gid = (r.get('game_id') or '').strip()
                pid = (r.get('play_id') or '').strip()
                if not gid or not pid:
                    raise ValueError('Missing game_id/play_id; refusing silent drop')
                week, qtr, down = integer(r.get('week')), integer(r.get('qtr')), integer(r.get('down'))
                home, away = (r.get('home_team') or '').strip(), (r.get('away_team') or '').strip()
                off, deft = (r.get('posteam') or '').strip(), (r.get('defteam') or '').strip()
                pa, ra, cp = integer(r.get('pass_attempt')), integer(r.get('rush_attempt')), integer(r.get('complete_pass'))
                ppid, rpid, runid = [(r.get(k) or '').strip() for k in
                    ('passer_player_id', 'receiver_player_id', 'rusher_player_id')]
                pass_y, recv_y, rush_y = [number(r.get(k)) for k in
                    ('passing_yards', 'receiving_yards', 'rushing_yards')]
                yl = r.get('yardline_100')
                yardline = None if yl in (None, '', 'NA') else number(yl)
                red = yardline is not None and yardline <= 20
                gt = integer(r.get('goal_to_go'))
                epa_text = r.get('epa')
                epa = None if epa_text in (None, '', 'NA') else number(epa_text)
                try:
                    con.execute('INSERT INTO plays VALUES (' + ','.join(['?']*23) + ')',
                        (season,gid,pid,week,r.get('game_date'),home,away,off,deft,qtr,down,
                         yardline,gt,pa,cp,ra,ppid,rpid,runid,pass_y,recv_y,rush_y,epa))
                except sqlite3.IntegrityError:
                    duplicate += 1
                    continue
                total += 1
                g = games.setdefault(gid, {'week':week,'date':r.get('game_date') or '',
                    'home':home,'away':away,'type':r.get('season_type') or '',
                    'count':0,'quarters':set(),'end':False})
                if (g['home'],g['away']) != (home,away):
                    raise ValueError(f'Conflicting teams in game {gid}')
                g['count'] += 1
                if qtr:
                    g['quarters'].add(qtr)
                if (r.get('desc') or '').strip().upper() in ('END GAME','END OF GAME'):
                    g['end'] = True
                if pa and not rpid:
                    missing_receiver += 1
                # PBP two-point plays are excluded from ordinary stat markets.
                if integer(r.get('two_point_attempt')):
                    discarded_two_point += 1
                    continue
                if epa is not None and deft:
                    if pa:
                        defense[(gid,deft)]['pass_count'] += 1
                        defense[(gid,deft)]['pass_epa_sum'] += epa
                    if ra:
                        defense[(gid,deft)]['rush_count'] += 1
                        defense[(gid,deft)]['rush_epa_sum'] += epa
                if pa and rpid and off:
                    key=usage_key(gid,week,g['date'],off,rpid,r.get('receiver_player_name'),'receiving')
                    x=stats[key]; x['targets']+=1; x['receptions']+=int(cp == 1)
                    x['receiving_yards']+=recv_y
                    x['third_down_targets']+=int(down==3)
                    x['red_zone_targets']+=int(red)
                    x['goal_to_go_targets']+=int(gt==1)
                    x['q1_targets']+=int(qtr==1)
                    x['q1_receptions']+=int(qtr==1 and cp==1)
                    x['q1_receiving_yards']+=recv_y if qtr==1 else 0
                if ra and runid and off:
                    key=usage_key(gid,week,g['date'],off,runid,r.get('rusher_player_name'),'rushing')
                    x=stats[key];x['rush_attempts']+=1;x['rushing_yards']+=rush_y
                    x['red_zone_rushes']+=int(red);x['goal_to_go_rushes']+=int(gt==1)
                    x['q1_rush_attempts']+=int(qtr==1)
                    x['q1_rushing_yards']+=rush_y if qtr==1 else 0
                if pa and ppid and off:
                    key=usage_key(gid,week,g['date'],off,ppid,r.get('passer_player_name'),'passing')
                    x=stats[key];x['pass_attempts']+=1;x['completions']+=int(cp==1)
                    x['passing_yards']+=pass_y
                    x['q1_pass_attempts']+=int(qtr==1)
                    x['q1_completions']+=int(qtr==1 and cp==1)
                    x['q1_passing_yards']+=pass_y if qtr==1 else 0
        if not games:
            raise ValueError('No historical games found')
        if duplicate:
            raise ValueError(f'Duplicate (game_id,play_id) keys detected: {duplicate}; reject source')
        games_rows=[]
        h2h_rows=[]
        for gid,g in sorted(games.items(),key=lambda t:(t[1]['date'],t[0])):
            coverage='END_MARKER_PRESENT_UNVALIDATED' if g['end'] else 'COMPLETENESS_UNKNOWN'
            games_rows.append((season,gid,g['week'],g['date'],g['home'],g['away'],g['type'],
                g['count'],','.join(map(str,sorted(g['quarters']))),int(g['end']),coverage))
            if g['home'] and g['away']:
                a,b=sorted((g['home'],g['away']))
                h2h_rows.append((season,a,b,gid,g['date'],g['week'],g['home'],g['away'],coverage))
        con.executemany('INSERT INTO games VALUES ('+','.join(['?']*11)+')', games_rows)
        usage_rows=[]
        for key,vals in sorted(stats.items(),key=lambda x:(x[0][3],x[0][0],x[0][6],x[0][5])):
            # Use the most frequent nonblank display name; ties sort alphabetically.
            names = player_names[key]
            canonical_name = (sorted(names, key=lambda n: (-names[n], n))[0]
                              if names else key[5])
            usage_rows.append((*key[:6], canonical_name, key[6],
                               *(vals.get(m,0) for m in METRICS)))
        con.executemany('INSERT INTO player_game_usage VALUES ('+','.join(['?']*29)+')',usage_rows)
        con.commit()
        write_csv(out / f'historical_games_{season}.csv',
            ['season','game_id','week','game_date','home_team','away_team','season_type',
             'play_rows','quarters_present','end_game_marker_present','coverage_status'],games_rows)
        write_csv(out / f'head_to_head_{season}.csv',
            ['season','team_a','team_b','game_id','game_date','week','home_team','away_team','coverage_status'],h2h_rows)
        write_csv(out / f'player_game_usage_{season}.csv',
            ['game_id','season','week','game_date','team','player_id','player_name','role',*METRICS],usage_rows)
        def_rows=[]
        for (gid,team),cnt in sorted(defense.items()):
            pa,ra=cnt['pass_count'],cnt['rush_count']
            def_rows.append((season,gid,team,pa,round(cnt['pass_epa_sum']/pa,5) if pa else '',
                ra,round(cnt['rush_epa_sum']/ra,5) if ra else ''))
        write_csv(out / f'defensive_epa_by_game_{season}.csv',
            ['season','game_id','defense','chartable_pass_plays','pass_epa_allowed_per_play',
             'chartable_rush_plays','rush_epa_allowed_per_play'],def_rows)
        audit={'season':season,'source_url':source_url or str(src),
            'source_sha256':digest.hexdigest(), 'source_bytes':src.stat().st_size,
            'provenance':'EXTERNAL_HISTORICAL_NFLVERSE_PBP_NOT_USER_VERIFIED',
            'game_count':len(games),'play_rows':total,'duplicate_play_keys':duplicate,
            'identified_player_game_role_rows':len(stats),
            'player_game_roles_with_name_variants':sum(1 for c in player_names.values() if len(c)>1),
            'player_name_variant_examples':[
                {'game_id':key[0], 'player_id':key[5], 'role':key[6],
                 'names':sorted(names)}
                for key,names in sorted(player_names.items()) if len(names)>1
            ][:20],
            'pass_attempts_missing_receiver_id':missing_receiver,
            'excluded_two_point_plays_from_usage':discarded_two_point,
            'games_with_end_marker':sum(g['end'] for g in games.values()),
            'created_at_utc':datetime.now(timezone.utc).isoformat(),
            'limitations':['Not integrated into 2026 player hit rates or existing 2026 SQLite database.',
                           'Missing player-game records are not automatic zero-production games.',
                           'Incomplete or unidentified receiver targets distort target shares.',
                           'End marker is not independent proof of complete PBP.',
                           'Season labels must always be shown when comparing head-to-head games.',
                           'EPA is descriptive, not opponent-adjusted or causal.',
                           'No sportsbook prices or bets stored.']}
        (out/f'historical_audit_{season}.json').write_text(json.dumps(audit,indent=2)+'\n')
        return audit
    finally:
        con.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--season',type=int,choices=(2023,2024,2025),default=2025)
    parser.add_argument('--source-file',help='Optional local .csv.gz or .csv (for offline testing)')
    parser.add_argument('--root',default='NFL_LOCKBOX_2026/historical')
    args=parser.parse_args()
    if args.source_file:
        report=build(args.season,args.source_file,args.root)
    else:
        with tempfile.TemporaryDirectory(prefix='nfl-history-') as tmp:
            source=Path(tmp)/f'play_by_play_{args.season}.csv.gz'
            url=download_season(args.season,source)
            report=build(args.season,source,args.root,source_url=url)
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
