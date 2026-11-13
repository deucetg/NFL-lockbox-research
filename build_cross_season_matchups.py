#!/usr/bin/env python3
"""NFL LOCKBOX: separate 2023-25 head-to-head context for upcoming 2026 matchups.

Research-only. Historical nflverse observations are external, NOT reconciled
LOCKBOX 2026 player stats. This never modifies the current 2026 database.
Python standard library only. Use --help for invocation options.
"""
import argparse
import collections
import csv
import hashlib
import io
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

YEARS = (2023, 2024, 2025)
SCHEDULE_SOURCE = 'https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv'
METRICS = (
    'targets', 'receptions', 'receiving_yards', 'third_down_targets',
    'red_zone_targets', 'goal_to_go_targets', 'q1_targets', 'q1_receptions',
    'q1_receiving_yards', 'rush_attempts', 'rushing_yards', 'red_zone_rushes',
    'goal_to_go_rushes', 'q1_rush_attempts', 'q1_rushing_yards', 'pass_attempts',
    'completions', 'passing_yards', 'q1_pass_attempts', 'q1_completions',
    'q1_passing_yards',
)
HISTORY_COLUMNS = (
    'upcoming_game_id', 'upcoming_date', 'upcoming_week', 'home_team',
    'away_team', 'historical_meetings_total', 'history_season', 'history_game_id',
    'history_date', 'history_week', 'history_home', 'history_away',
    'history_season_type', 'coverage_status',
)
PLAYER_COLUMNS = (
    'upcoming_game_id','upcoming_date','history_season','history_game_id',
    'history_date','historical_team','player_id','player_name','role',*METRICS,
)
SUMMARY_COLUMNS = (
    'upcoming_game_id','home_team','away_team','player_id','historical_team',
    'player_name','role','recorded_h2h_player_game_rows','historical_seasons_observed',
    'latest_history_date','observed_on_matching_team_in_2026_usage_snapshot',
    *('avg_'+m+'_per_recorded_row' for m in METRICS),
)
DEFENSE_COLUMNS = (
    'upcoming_game_id','history_season','history_game_id','historical_defense',
    'charted_pass_plays','pass_epa_allowed_per_play','charted_rush_plays',
    'rush_epa_allowed_per_play',
)


def write_csv(path, columns, rows):
    with Path(path).open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def load_schedule(path, as_of, days):
    """Read nflverse schedules or a LOCKBOX upcoming_actual_matchups snapshot."""
    if path is None:
        req = Request(SCHEDULE_SOURCE, headers={'User-Agent':'NFL-LOCKBOX-History/1.0'})
        with urlopen(req, timeout=90) as response:
            payload = response.read()
        source = SCHEDULE_SOURCE
    else:
        payload = Path(path).read_bytes()
        source = str(Path(path))
    rows = csv.DictReader(io.StringIO(payload.decode('utf-8-sig')))
    columns = set(rows.fieldnames or ())
    if not {'game_id','home_team','away_team'}.issubset(columns):
        raise ValueError('Schedule missing game_id/home_team/away_team')
    if not ({'game_date','gameday'} & columns):
        raise ValueError('Schedule missing game_date/gameday')
    until = as_of + timedelta(days=days)
    chosen = {}
    for row in rows:
        gid = (row.get('game_id') or '').strip()
        if not gid.startswith('2026_'):
            continue
        if row.get('season') and str(row['season']).strip() != '2026':
            raise ValueError(f'Season conflict for 2026 ID {gid}')
        if row.get('game_type') and row['game_type'].strip() != 'REG':
            continue
        day_text = (row.get('game_date') or row.get('gameday') or '').strip()
        try:
            gameday = date.fromisoformat(day_text)
        except ValueError:
            continue
        if not (as_of <= gameday <= until):
            continue
        if row.get('home_score', '').strip() or row.get('away_score', '').strip():
            continue
        home, away = (row.get('home_team') or '').strip(), (row.get('away_team') or '').strip()
        if not home or not away or home == away:
            raise ValueError(f'Invalid home/away for game {gid}')
        entry = {'game_id':gid,'game_date':day_text,'week':str(row.get('week') or ''),
                 'home_team':home,'away_team':away}
        if gid in chosen and chosen[gid] != entry:
            raise ValueError(f'Conflicting schedule entries for {gid}')
        chosen[gid] = entry
    return sorted(chosen.values(),key=lambda x:(x['game_date'],x['game_id'])), source, hashlib.sha256(payload).hexdigest()


def current_players_snapshot(path):
    """Observed 2026 player/team IDs; DOES NOT establish active status."""
    if not path:
        return set(), None
    src = Path(path)
    with src.open(newline='',encoding='utf-8-sig') as f:
        reader=csv.DictReader(f)
        if not {'season','team','player_id'}.issubset(set(reader.fieldnames or [])):
            raise ValueError('2026 usage snapshot missing season/team/player_id')
        observed=set()
        for row in reader:
            if str(row.get('season') or '').strip() == '2026':
                pid=(row.get('player_id') or '').strip()
                team=(row.get('team') or '').strip()
                if pid and team:
                    observed.add((team,pid))
    return observed, hashlib.sha256(src.read_bytes()).hexdigest()


def read_history(root, year):
    db = Path(root)/str(year)/f'nfl_historical_{year}.sqlite'
    if not db.is_file():
        raise FileNotFoundError(f'Missing historical SQLite for {year}: {db}')
    con = sqlite3.connect(f'file:{db.resolve()}?mode=ro',uri=True)
    con.row_factory=sqlite3.Row
    try:
        if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError(f'Historical SQLite integrity failed: {year}')
        seasons = {int(r[0]) for r in con.execute('SELECT DISTINCT season FROM games')}
        if seasons != {year}:
            raise ValueError(f'Historical database season mismatch {year}: {seasons}')
        sources = [dict(r) for r in con.execute('SELECT * FROM source_registry')]
        if len(sources) != 1 or int(sources[0]['season']) != year:
            raise ValueError(f'Historical provenance registry mismatch: {year}')
        games = [dict(r) for r in con.execute('SELECT * FROM games')]
        if any(not g['game_id'].startswith(f'{year}_') for g in games):
            raise ValueError(f'Mixed or mislabeled game IDs in historical {year} file')
        return con, db, games, sources[0]
    except Exception:
        con.close()
        raise


def build(history_root, out_root, schedule_file=None, as_of=None, days=8, current_usage_file=None):
    if as_of is None:
        as_of=datetime.now(ZoneInfo('America/New_York')).date()
    elif isinstance(as_of,str):
        as_of=date.fromisoformat(as_of)
    if not 0 <= days <= 31:
        raise ValueError('days must be between 0 and 31')
    scheduled, source, schedule_sha = load_schedule(schedule_file,as_of,days)
    if not scheduled:
        raise ValueError('No future 2026 regular-season matchups in selected window')
    observed_2026, current_sha = current_players_snapshot(current_usage_file)
    out=Path(out_root)
    out.mkdir(parents=True,exist_ok=True)
    by_pair=collections.defaultdict(list)
    conns=[]
    audit_years={}
    game_ids=set()
    try:
        for year in YEARS:
            con, db, games, registry = read_history(history_root,year)
            conns.append(con)
            for game in games:
                if game['game_id'] in game_ids:
                    raise ValueError('Duplicate game ID across history seasons')
                game_ids.add(game['game_id'])
                by_pair[frozenset((game['home_team'],game['away_team']))].append((year,game,con))
            audit_years[str(year)] = {
                'games':len(games),'database':str(db),
                'provenance':registry.get('provenance'),
                'source_sha256':registry.get('source_sha256'),
            }
        h2h=[]
        player_rows=[]
        defense_rows=[]
        match_summaries=[]
        for fixture in scheduled:
            gid=fixture['game_id']
            previous=sorted(by_pair.get(frozenset((fixture['home_team'],fixture['away_team'])),()),
                key=lambda x:(x[1]['game_date'],x[1]['game_id']),reverse=True)
            match_summaries.append({'upcoming_game_id':gid,'upcoming_date':fixture['game_date'],
                'home_team':fixture['home_team'],'away_team':fixture['away_team'],
                'historical_meetings_2023_2025':len(previous),
                'historical_years':','.join(sorted({str(y) for y,_,_ in previous})),
                'data_status':'EXTERNAL_HISTORICAL_CONTEXT_NOT_CURRENT_PROJECTION'})
            if not previous:
                h2h.append({'upcoming_game_id':gid,'upcoming_date':fixture['game_date'],
                            'upcoming_week':fixture['week'],'home_team':fixture['home_team'],
                            'away_team':fixture['away_team'],'historical_meetings_total':0,
                            'coverage_status':'NO_H2H_2023_2025'})
                continue
            for year,old,con in previous:
                old_gid=old['game_id']
                h2h.append({'upcoming_game_id':gid,'upcoming_date':fixture['game_date'],
                    'upcoming_week':fixture['week'],'home_team':fixture['home_team'],
                    'away_team':fixture['away_team'],'historical_meetings_total':len(previous),
                    'history_season':year,'history_game_id':old_gid,
                    'history_date':old['game_date'],'history_week':old['week'],
                    'history_home':old['home_team'],'history_away':old['away_team'],
                    'history_season_type':old['season_type'],
                    'coverage_status':old['coverage_status']})
                usage=con.execute('SELECT * FROM player_game_usage WHERE game_id=?',(old_gid,))
                for p in usage:
                    p=dict(p)
                    player_rows.append({'upcoming_game_id':gid,'upcoming_date':fixture['game_date'],
                        'history_season':year,'history_game_id':old_gid,'history_date':old['game_date'],
                        'historical_team':p['team'],'player_id':p['player_id'],
                        'player_name':p['player_name'],'role':p['role'],
                        **{m:p.get(m) for m in METRICS}})
                eps=Path(history_root)/str(year)/f'defensive_epa_by_game_{year}.csv'
                if eps.exists():
                    # This file is optional and source-labeled; no defense EPA is invented.
                    with eps.open(newline='',encoding='utf-8-sig') as f:
                        for ep in csv.DictReader(f):
                            if ep.get('game_id')==old_gid:
                                defense_rows.append({'upcoming_game_id':gid,'history_season':year,
                                    'history_game_id':old_gid,'historical_defense':ep.get('defense'),
                                    'charted_pass_plays':ep.get('chartable_pass_plays'),
                                    'pass_epa_allowed_per_play':ep.get('pass_epa_allowed_per_play'),
                                    'charted_rush_plays':ep.get('chartable_rush_plays'),
                                    'rush_epa_allowed_per_play':ep.get('rush_epa_allowed_per_play')})
        # One summary per historical player ID/role/team, not by display name.
        grouped=collections.defaultdict(list)
        fixture_index={g['game_id']:g for g in scheduled}
        for row in player_rows:
            key=(row['upcoming_game_id'],row['historical_team'],row['player_id'],row['role'])
            grouped[key].append(row)
        summaries=[]
        for (gid,team,pid,role),group in sorted(grouped.items()):
            latest=max(group,key=lambda x:(x['history_date'],x['history_game_id']))
            summary={'upcoming_game_id':gid,'home_team':fixture_index[gid]['home_team'],
                'away_team':fixture_index[gid]['away_team'],
                'player_id':pid,'historical_team':team,
                'player_name':latest['player_name'],'role':role,
                'recorded_h2h_player_game_rows':len(group),
                'historical_seasons_observed':','.join(map(str,sorted({r['history_season'] for r in group}))),
                'latest_history_date':latest['history_date'],
                'observed_on_matching_team_in_2026_usage_snapshot':
                    ('yes' if (team,pid) in observed_2026 else
                     'no' if current_usage_file else 'not_checked')}
            for m in METRICS:
                values=[float(r[m]) for r in group if r.get(m) is not None]
                summary['avg_'+m+'_per_recorded_row'] = (round(sum(values)/len(values),2)
                                                            if values else '')
            summaries.append(summary)
        write_csv(out/'upcoming_historical_matchups_2026.csv',
                  ['upcoming_game_id','upcoming_date','home_team','away_team',
                   'historical_meetings_2023_2025','historical_years','data_status'],match_summaries)
        write_csv(out/'head_to_head_2023_2025_for_2026.csv',HISTORY_COLUMNS,h2h)
        write_csv(out/'historical_player_game_usage_by_matchup.csv',PLAYER_COLUMNS,player_rows)
        write_csv(out/'historical_player_matchup_summaries.csv',SUMMARY_COLUMNS,summaries)
        write_csv(out/'historical_defensive_epa_by_matchup.csv',DEFENSE_COLUMNS,defense_rows)
        audit={'as_of_eastern_date':as_of.isoformat(),
            'horizon_days':days,'future_2026_matchups':len(scheduled),
            'historical_head_to_head_games':sum(row['historical_meetings_2023_2025'] for row in match_summaries),
            'historical_player_game_role_rows':len(player_rows),
            'historical_player_summaries':len(summaries),
            'schedule_source':source,'schedule_source_sha256':schedule_sha,
            'current_usage_snapshot':str(current_usage_file) if current_usage_file else None,
            'current_usage_snapshot_sha256':current_sha,
            'historical_years':audit_years,
            'provenance':'EXTERNAL_HISTORICAL_NFLVERSE_PBP_NOT_VERIFIED_LOCKBOX_CURRENT_STATS',
            'notes':[
                'No new player statistics are added to the current 2026 LOCKBOX database.',
                'Historical games are prior head-to-head meetings, not projections.',
                'Historical player records are only observed play-by-play events; absence of a row is not zero playing time.',
                '2026 player ID snapshot flags only observation in supplied sample, not active/starting status.',
                'Current roster, injury and expected playing time must be verified separately.',
                'Historical player aliases are consolidated by stable player IDs within season.',
                'Season and game IDs are preserved on every historical row.',
                'EPA reflects raw historical charted plays, not adjusted team quality.',
                'No sportsbook lines, prices, stakes, parlays or wager results included.',
            ],
            'created_at_utc':datetime.now(timezone.utc).isoformat()}
        (out/'cross_season_audit_2026.json').write_text(json.dumps(audit,indent=2)+'\n',encoding='utf-8')
        return audit
    finally:
        for con in conns:
            con.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--history-root',default='NFL_LOCKBOX_2026/historical')
    p.add_argument('--out-root',default='NFL_LOCKBOX_2026/historical_intelligence')
    p.add_argument('--schedule-file',help='Optional 2026 upcoming matchups CSV or nflverse games.csv')
    p.add_argument('--current-usage-file',help='Optional 2026 player_game_usage.csv for observed-ID matching only')
    p.add_argument('--as-of',help='Eastern date YYYY-MM-DD; default today')
    p.add_argument('--days',type=int,default=8)
    args=p.parse_args()
    print(json.dumps(build(args.history_root,args.out_root,args.schedule_file,
                           args.as_of,args.days,args.current_usage_file),indent=2))


if __name__=='__main__':
    main()
