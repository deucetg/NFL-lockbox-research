# NFL LOCKBOX 2026 — Audited PBP foundation

This is a **research-only** database built from the user-uploaded `play_by_play_2026.csv.gz` (nflverse-style schema). 

## Contents
- `nfl_lockbox_2026.sqlite`: `plays` (35 selected original play fields + source id), `player_game_usage`, `source_registry`, and `receiving_game_share` view.
- `player_game_usage.csv`: per-player per-game counts and first-quarter splits.
- `game_coverage.csv`: inventory, not a claim of game completeness.
- `audit.json`: ingestion statistics and limitations.
- `build_lockbox.py`: reproducible builder; place source archive in same directory or adjust paths.

## IMPORTANT LIMITATIONS
1. The file was supplied by the user but contains **external nflverse-format player data**, not necessarily the manually validated LOCKBOX PBP. Label it `user-uploaded external PBP`, not `verified LOCKBOX` until independently reconciled.
2. Target share denominator counts identified receiver pass attempts only. QB throwaways, spikes, and unidentified targets need reconciliation before trusting target-share percentages.
3. Two-point player usage, penalty-nullified plays, lateral plays, scrambles and sacks require additional validation; this version does not fully handle them.
4. Individual play blanks are NULL in SQLite. Aggregates only count recognized positive events.
5. Do not infer completed games merely from four quarters present.
6. Week 9 is incomplete; exclude partial games from trend claims until validated.

## Sample SQLite queries
```sql
SELECT player_name, SUM(targets) targets, SUM(receptions) receptions FROM player_game_usage WHERE role='receiving' GROUP BY player_id ORDER BY targets DESC LIMIT 5;
SELECT player_name, game_date, targets, receptions, receiving_yards FROM player_game_usage WHERE role='receiving' AND player_name LIKE '%Chase%' ORDER BY game_date DESC;
SELECT player_name, game_date, targets, target_share_pct FROM receiving_game_share ORDER BY game_date DESC LIMIT 20;
```
