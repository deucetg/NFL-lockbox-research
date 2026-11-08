#!/usr/bin/env python3
"""NFL LOCKBOX 2026: auditable offensive player-game and opening-drive analysis.

Research only. All player counts come from the user-supplied play_by_play_2026.csv.gz.
This script does NOT invent snaps, routes, starting status, injuries, or sportsbook odds.

Run in repository root: python opportunity_audit.py
Outputs are written to NFL_LOCKBOX_2026/ alongside existing research outputs.
"""
import csv
import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent
SOURCE = BASE / "play_by_play_2026.csv.gz"
OUTPUT = BASE / "NFL_LOCKBOX_2026"
OUTPUT.mkdir(exist_ok=True)


def flag(row, name):
    return row.get(name) == "1"


def number(row, name):
    try:
        return float(row.get(name) or 0)
    except (ValueError, TypeError):
        return 0.0


def play_order(row):
    try:
        return float(row.get("play_id") or 0)
    except (ValueError, TypeError):
        return 0.0


def write_csv(filename, columns, items):
    with (OUTPUT / filename).open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        writer.writerows(items)


with gzip.open(SOURCE, "rt", encoding="utf-8", newline="") as fh:
    reader = csv.DictReader(fh)
    required = {"game_id", "play_id", "season", "season_type", "game_date", "week",
                "home_team", "away_team", "posteam", "drive", "qtr", "desc",
                "pass_attempt", "complete_pass", "rush_attempt", "qb_dropback",
                "receiver_player_id", "rusher_player_id", "passer_player_id",
                "receiving_yards", "rushing_yards", "passing_yards"}
    absent = required.difference(reader.fieldnames or [])
    if absent:
        raise ValueError("Missing required PBP fields: " + ", ".join(sorted(absent)))
    source_groups = defaultdict(list)
    for play in reader:
        if play.get("season") == "2026" and play.get("season_type") == "REG":
            source_groups[play["game_id"]].append(play)

# A published END GAME row demonstrates an end marker, not full box-score reconciliation.
game_rows = {}
team_schedule = defaultdict(list)
game_info = {}
game_quality = []
quality_flags = Counter()
for gid, original in source_groups.items():
    original.sort(key=play_order)
    markers = sum((r.get("desc") or "").strip().upper() == "END GAME" for r in original)
    seen_keys = [r.get("play_id") for r in original]
    duplicates = len(seen_keys) - len(set(seen_keys))
    quarters = sorted({r.get("qtr") for r in original if r.get("qtr") in {"1", "2", "3", "4", "5"}})
    home, away = original[0].get("home_team"), original[0].get("away_team")
    date, week = original[0].get("game_date"), original[0].get("week")
    scrimmage = [r for r in original if flag(r, "pass_attempt") or flag(r, "rush_attempt")
                 or flag(r, "qb_dropback") or flag(r, "sack")]
    unknown_targets = sum(flag(r, "pass_attempt") and not r.get("receiver_player_id")
                          and not flag(r, "two_point_attempt") for r in original)
    spikes = sum(flag(r, "pass_attempt") and not r.get("receiver_player_id")
                 and r.get("play_type") == "qb_spike" for r in original)
    status = "END_MARKER_PRESENT_REQUIRES_BOX_VALIDATION" if markers else "NO_END_MARKER_EXCLUDE"
    if markers and duplicates == 0:
        game_rows[gid] = original
        game_info[gid] = (date, week, home, away)
        for team, opp in ((home, away), (away, home)):
            if team:
                team_schedule[team].append((date, week, gid, opp))
    quality_flags["games_all"] += 1
    quality_flags["games_end_marker"] += bool(markers)
    quality_flags["games_no_end_marker"] += not bool(markers)
    quality_flags["games_duplicate_play_keys"] += bool(duplicates)
    quality_flags["unidentified_receiver_attempts"] += unknown_targets
    game_quality.append([gid, date, week, home, away, len(original), len(scrimmage),
                         ",".join(quarters), markers, duplicates, unknown_targets,
                         spikes, unknown_targets - spikes, status])
for team in team_schedule:
    team_schedule[team].sort(key=lambda r: (r[0], r[2]))

write_csv("game_quality_2026.csv", [
    "game_id", "date", "week", "home_team", "away_team", "pbp_rows", "scrimmage_plays",
    "quarters_present", "end_game_markers", "duplicate_play_keys",
    "pass_attempts_without_receiver_id", "qb_spikes_without_receiver_id",
    "other_pass_attempts_without_receiver_id", "coverage_status"
], sorted(game_quality, key=lambda v: (v[1], v[0])))

# Player identity comes only from an actual offensive event, never a roster inference.
player_names = {}
players_by_team = defaultdict(set)
observed = defaultdict(set)
stat = defaultdict(Counter)
unknown_targets_by_team_game = Counter()
first_drive_team = {}
first_drive_player = defaultdict(Counter)
first10_player = defaultdict(Counter)
first_drive_columns = []


def identity(team, gid, r, id_field, name_field):
    pid = (r.get(id_field) or "").strip()
    if pid:
        players_by_team[team].add(pid)
        observed[(team, gid)].add(pid)
        if r.get(name_field):
            player_names[(team, pid)] = r[name_field]
    return pid


def record_event(c, kind, r):
    if kind == "pass":
        c["pass_attempts"] += 1
        c["completions"] += int(flag(r, "complete_pass"))
        c["passing_yards"] += number(r, "passing_yards")
        if r.get("qtr") == "1":
            c["q1_pass_attempts"] += 1
            c["q1_passing_yards"] += number(r, "passing_yards")
    elif kind == "receive":
        c["targets"] += 1
        c["receptions"] += int(flag(r, "complete_pass"))
        c["receiving_yards"] += number(r, "receiving_yards")
        if r.get("qtr") == "1":
            c["q1_targets"] += 1
            c["q1_receptions"] += int(flag(r, "complete_pass"))
            c["q1_receiving_yards"] += number(r, "receiving_yards")
    elif kind == "rush":
        c["rush_attempts"] += 1
        c["rushing_yards"] += number(r, "rushing_yards")
        if r.get("qtr") == "1":
            c["q1_rush_attempts"] += 1
            c["q1_rushing_yards"] += number(r, "rushing_yards")


for gid, plays in game_rows.items():
    date, week, home, away = game_info[gid]
    by_team = defaultdict(list)
    for r in plays:
        off = r.get("posteam")
        if off in (home, away):
            if flag(r, "pass_attempt") or flag(r, "rush_attempt") or flag(r, "qb_dropback") or flag(r, "sack"):
                by_team[off].append(r)
            if flag(r, "two_point_attempt"):
                continue  # Two-point conversions are kept separate, not counted as normal props.
            if flag(r, "pass_attempt") and not r.get("receiver_player_id"):
                unknown_targets_by_team_game[(off, gid)] += 1
            if flag(r, "pass_attempt"):
                pid = identity(off, gid, r, "passer_player_id", "passer_player_name")
                if pid:
                    record_event(stat[(off, gid, pid)], "pass", r)
                pid = identity(off, gid, r, "receiver_player_id", "receiver_player_name")
                if pid:
                    record_event(stat[(off, gid, pid)], "receive", r)
            if flag(r, "rush_attempt"):
                pid = identity(off, gid, r, "rusher_player_id", "rusher_player_name")
                if pid:
                    record_event(stat[(off, gid, pid)], "rush", r)

    for team, opp in ((home, away), (away, home)):
        s = by_team.get(team, [])
        s.sort(key=play_order)
        if not s:
            first_drive_team[(team, gid)] = ("", [], [], "NO_RECOGNIZED_OFFENSIVE_SNAP")
            continue
        first = next((r for r in s if r.get("drive")), None)
        if first is None:
            first_drive_team[(team, gid)] = ("", [], s[:10], "NO_DRIVE_ID")
            continue
        drive_id = first["drive"]
        drive_plays = [r for r in s if r.get("drive") == drive_id]
        first10 = s[:10]  # Explicitly distinct from the opening possession.
        first_drive_team[(team, gid)] = (drive_id, drive_plays, first10, "DRIVE_ID_PRESENT")
        for collection, target in ((drive_plays, first_drive_player), (first10, first10_player)):
            for r in collection:
                if flag(r, "two_point_attempt"):
                    continue
                if flag(r, "pass_attempt"):
                    for role, pidfield in (("pass", "passer_player_id"), ("receive", "receiver_player_id")):
                        pid = r.get(pidfield) or ""
                        if pid:
                            record_event(target[(team, gid, pid)], role, r)
                if flag(r, "rush_attempt"):
                    pid = r.get("rusher_player_id") or ""
                    if pid:
                        record_event(target[(team, gid, pid)], "rush", r)

# Opening possessions are determined from the first SCRIMMAGE play with a drive ID.
opening_team_rows = []
for team, schedule in sorted(team_schedule.items()):
    for date, week, gid, opponent in schedule:
        drive_id, dplays, f10, status = first_drive_team[(team, gid)]
        all_scrimmage = [r for r in game_rows[gid] if r.get("posteam") == team
                         and (flag(r, "pass_attempt") or flag(r, "rush_attempt") or flag(r, "qb_dropback") or flag(r, "sack"))]
        opening_team_rows.append([
            gid, date, week, team, opponent, drive_id,
            dplays[0].get("play_id") if dplays else "",
            dplays[0].get("qtr") if dplays else "",
            dplays[-1].get("qtr") if dplays else "",
            len(dplays), sum(flag(r, "pass_attempt") for r in dplays),
            sum(flag(r, "rush_attempt") for r in dplays),
            sum(flag(r, "pass_attempt") and not r.get("receiver_player_id") for r in dplays),
            len(f10), sum(flag(r, "pass_attempt") for r in f10),
            sum(flag(r, "rush_attempt") for r in f10),
            len(all_scrimmage), status,
        ])
write_csv("opening_drive_team_2026.csv", [
    "game_id", "date", "week", "team", "opponent", "first_drive_id",
    "first_scrimmage_play_id", "start_quarter", "end_quarter", "drive_scrimmage_plays",
    "drive_pass_attempts", "drive_rush_attempts", "drive_unknown_receiver_attempts",
    "first10_offensive_snaps_available", "first10_pass_attempts", "first10_rush_attempts",
    "game_scrimmage_plays", "drive_extraction_status"
], opening_team_rows)

# Use the SAME team-game inventory for zero-opportunity validation and recent windows.
metric_names = ["pass_attempts", "completions", "passing_yards", "targets", "receptions",
                "receiving_yards", "rush_attempts", "rushing_yards", "q1_pass_attempts",
                "q1_passing_yards", "q1_targets", "q1_receptions", "q1_receiving_yards",
                "q1_rush_attempts", "q1_rushing_yards"]
opening_names = ["pass_attempts", "passing_yards", "targets", "receptions", "receiving_yards",
                 "rush_attempts", "rushing_yards"]
name_map = {"pass_attempts": "pass_attempts", "passing_yards": "passing_yards", "targets": "targets",
            "receptions": "receptions", "receiving_yards": "receiving_yards",
            "rush_attempts": "rush_attempts", "rushing_yards": "rushing_yards"}
exposure_rows = []
opening_player_rows = []
window_rows = []
unknown_total = 0
observed_total = 0

for team, pids in sorted(players_by_team.items()):
    schedule = team_schedule.get(team, [])
    for pid in sorted(pids):
        player_games = [g for g in schedule if pid in observed[(team, g[2])]]
        if not player_games:
            continue
        first_date = min(g[0] for g in player_games)
        last_date = max(g[0] for g in player_games)
        name = player_names.get((team, pid), "")
        for date, week, gid, opp in schedule:
            confirmed = pid in observed[(team, gid)]
            if confirmed:
                status = "OBSERVED_OFFENSIVE_EVENT"
                observed_total += 1
            elif date < first_date:
                status = "UNOBSERVED_BEFORE_FIRST_EVIDENCE"
                unknown_total += 1
            elif date > last_date:
                status = "UNOBSERVED_AFTER_LAST_EVIDENCE"
                unknown_total += 1
            else:
                status = "UNOBSERVED_WITHIN_EVIDENCE_SPAN"
                unknown_total += 1
            game_stats = stat.get((team, gid, pid), Counter())
            missing_targets = unknown_targets_by_team_game[(team, gid)]
            exposure_rows.append([gid, date, week, team, opp, pid, name, status,
                                  int(confirmed), first_date, last_date, missing_targets] +
                                 [round(game_stats[m], 2) if confirmed else "" for m in metric_names])
            if confirmed:
                d = first_drive_player.get((team, gid, pid), Counter())
                t = first10_player.get((team, gid, pid), Counter())
                drive_id, dplays, f10, extraction_status = first_drive_team[(team, gid)]
                drive_passes = sum(flag(r, "pass_attempt") for r in dplays)
                first10_passes = sum(flag(r, "pass_attempt") for r in f10)
                opening_player_rows.append([
                    gid, date, week, team, opp, pid, name, "OBSERVED_OFFENSIVE_EVENT_IN_GAME",
                    drive_id, extraction_status, missing_targets,
                    drive_passes, first10_passes,
                    round(100 * d["targets"] / drive_passes, 1) if drive_passes else "",
                    round(100 * t["targets"] / first10_passes, 1) if first10_passes else "",
                    *[round(d[m], 2) for m in opening_names],
                    *[round(t[m], 2) for m in opening_names],
                    *[round(game_stats[m], 2) for m in
                      ("q1_targets", "q1_receptions", "q1_receiving_yards",
                       "q1_rush_attempts", "q1_rushing_yards", "q1_pass_attempts", "q1_passing_yards")],
                ])
        for n in (3, 5, 8):
            games = schedule[-n:]
            known = [g for g in games if pid in observed[(team, g[2])]]
            missing = [g for g in games if pid not in observed[(team, g[2])]]
            d = Counter()
            f10 = Counter()
            allstats = Counter()
            games_with_drive_target = 0
            games_with_drive_rush = 0
            games_with_q1_receiving_10 = 0
            games_with_q1_rush_10 = 0
            for _, _, gid, _ in known:
                allstats.update(stat[(team, gid, pid)])
                d.update(first_drive_player.get((team, gid, pid), Counter()))
                f10.update(first10_player.get((team, gid, pid), Counter()))
                games_with_drive_target += int(first_drive_player.get((team, gid, pid), Counter())["targets"] > 0)
                games_with_drive_rush += int(first_drive_player.get((team, gid, pid), Counter())["rush_attempts"] > 0)
                games_with_q1_receiving_10 += int(stat[(team, gid, pid)]["q1_receiving_yards"] >= 10)
                games_with_q1_rush_10 += int(stat[(team, gid, pid)]["q1_rushing_yards"] >= 10)
            known_drive_passes = sum(sum(flag(r, "pass_attempt") for r in first_drive_team[(team, g[2])][1]) for g in known)
            known_first10_passes = sum(sum(flag(r, "pass_attempt") for r in first_drive_team[(team, g[2])][2]) for g in known)
            window_rows.append([
                team, pid, name, f"L{n}_TEAM_GAMES", n, len(games), len(known), len(missing),
                ",".join(g[2] for g in missing),
                int(len(games) == n and len(missing) == 0),
                sum(unknown_targets_by_team_game[(team, g[2])] for g in games),
                games_with_drive_target, games_with_drive_rush,
                games_with_q1_receiving_10, games_with_q1_rush_10,
                round(100 * d["targets"] / known_drive_passes, 1) if known_drive_passes else "",
                round(100 * f10["targets"] / known_first10_passes, 1) if known_first10_passes else "",
                *[round(d[m], 2) for m in opening_names],
                *[round(f10[m], 2) for m in opening_names],
                *[round(allstats[m], 2) for m in metric_names],
            ])

write_csv("player_game_validation_2026.csv", [
    "game_id", "date", "week", "team", "opponent", "player_id", "player_name",
    "participation_status", "offensive_event_evidence", "first_offensive_evidence_date",
    "last_offensive_evidence_date", "team_pass_attempts_without_receiver_id"
] + metric_names, exposure_rows)
write_csv("opening_drive_player_2026.csv", [
    "game_id", "date", "week", "team", "opponent", "player_id", "player_name",
    "participation_basis", "opening_drive_id", "drive_extraction_status",
    "team_pass_attempts_without_receiver_id", "team_opening_drive_pass_attempts",
    "team_first10_pass_attempts", "opening_drive_target_share_pct_of_pass_attempts",
    "first10_target_share_pct_of_pass_attempts"
] + ["opening_drive_" + s for s in opening_names]
  + ["first10_offensive_snaps_" + s for s in opening_names]
  + ["q1_" + s for s in ("targets", "receptions", "receiving_yards", "rush_attempts",
                          "rushing_yards", "pass_attempts", "passing_yards")], opening_player_rows)
write_csv("player_opportunity_windows_2026.csv", [
    "team", "player_id", "player_name", "window_type", "requested_team_games",
    "team_games_available", "offensive_event_games", "unobserved_unknown_games",
    "unknown_game_ids", "all_games_have_offensive_event_evidence",
    "team_pass_attempts_without_receiver_id", "games_with_opening_drive_target",
    "games_with_opening_drive_rush", "games_with_q1_10plus_receiving_yards",
    "games_with_q1_10plus_rushing_yards", "opening_drive_target_share_pct_of_pass_attempts",
    "first10_target_share_pct_of_pass_attempts"
] + ["opening_drive_" + s for s in opening_names]
  + ["first10_offensive_snaps_" + s for s in opening_names]
  + ["total_" + s for s in metric_names], window_rows)

quality_summary = {
    "source": "User-uploaded play_by_play_2026.csv.gz; nflverse-format external PBP",
    "season": 2026,
    "scope": "REG season games with END GAME marker and no duplicate play ID",
    "source_completeness": "END GAME is only a source marker, not verified box-score completeness",
    "game_counts": dict(quality_flags),
    "completed_games_analyzed": len(game_rows),
    "offensive_event_observed_player_game_rows": observed_total,
    "unobserved_player_game_rows_must_not_be_counted_as_zero": unknown_total,
    "opening_drive_team_rows": len(opening_team_rows),
    "opening_drive_player_rows": len(opening_player_rows),
    "rolling_team_window_rows": len(window_rows),
    "notes": [
        "A player with a recorded pass/rush/identified target participated in at least one offense event.",
        "No play or target does not establish inactive status or prove zero routes/snaps.",
        "Unobserved players have blank metrics, not zero. This tool cannot verify active roster.",
        "Even observed-game zero target counts can be understated if receiver IDs are missing.",
        "Opening drives are the first offensive drive ID with a recognized scrimmage play per team.",
        "First ten offensive snaps are a separate metric and can extend beyond the opening drive.",
        "Opening-drive targets are events, not routes or snaps played. Game-based observation is not drive participation.",
        "L3/L5/L8 use last 3/5/8 completed TEAM games; each row gives known and unknown player exposure.",
        "No injury, schedule, bookmaker price, or outside player metrics are mixed into these results."
    ]
}
(OUTPUT / "opportunity_audit_2026.json").write_text(json.dumps(quality_summary, indent=2))
print(json.dumps({k: v for k, v in quality_summary.items() if k not in ("notes", "source")}, indent=2))
