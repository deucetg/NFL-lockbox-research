#!/usr/bin/env python3
"""Query previous head-to-head games from a season-specific historical SQLite file."""
import argparse
import json
import sqlite3
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--season',type=int,choices=(2023,2024,2025),default=2025)
    p.add_argument('--team-a',required=True,help='NFL team abbreviation, e.g. WAS')
    p.add_argument('--team-b',required=True,help='NFL team abbreviation, e.g. NYG')
    p.add_argument('--root',default='NFL_LOCKBOX_2026/historical')
    args=p.parse_args()
    ta,tb=args.team_a.strip().upper(),args.team_b.strip().upper()
    if not ta or not tb or ta==tb:
        p.error('Supply two different NFL team abbreviations')
    db=Path(args.root)/str(args.season)/f'nfl_historical_{args.season}.sqlite'
    if not db.exists():
        p.error(f'Historical database not found: {db}')
    with sqlite3.connect(f'file:{db.resolve()}?mode=ro',uri=True) as con:
        con.row_factory=sqlite3.Row
        games=[dict(x) for x in con.execute('''
          SELECT season,game_id,week,game_date,home_team,away_team,season_type,
                 play_rows,coverage_status
          FROM games
          WHERE (home_team=? AND away_team=?) OR (home_team=? AND away_team=?)
          ORDER BY game_date DESC,game_id''',(ta,tb,tb,ta))]
        for g in games:
            rows=con.execute('''
              SELECT player_name,team,role,targets,receptions,receiving_yards,
                     rush_attempts,rushing_yards,pass_attempts,passing_yards,
                     third_down_targets,red_zone_targets,goal_to_go_targets,
                     q1_targets,q1_receptions,q1_receiving_yards,
                     q1_rush_attempts,q1_rushing_yards,q1_pass_attempts,q1_passing_yards
              FROM player_game_usage WHERE game_id=?
              ORDER BY team,role,player_name''',(g['game_id'],))
            g['observed_player_roles']=[dict(row) for row in rows]
    print(json.dumps({'season':args.season,'teams':[ta,tb],
        'historical_games_found':len(games),
        'note':'External historical nflverse observations, not current-season forecasts or verified current LOCKBOX player logs.',
        'games':games},indent=2))

if __name__=='__main__':
    main()
