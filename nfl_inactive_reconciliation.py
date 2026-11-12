
#!/usr/bin/env python3
"""
NFL LOCKBOX Phase 8B — Reconciliation v2.

Validates game/team/week and GSIS identity evidence.
Preserves multiple legitimate research rows per player.
Does not authenticate NFL announcements or modify
game-day availability.
"""

import argparse
import csv
import gzip
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from nfl_official_inactives import (
    FIELDS,
    validate as validate_staging,
)

VERSION = "phase8b_reconciliation_v2"
SEASON = 2026

RESULT_FIELDS = [
    "game_id", "week", "team", "gsis_id",
    "player_name", "schedule_status",
    "identity_status", "candidate_status",
    "reconciliation_status", "official_status",
    "source_url",
]

SCHEDULE_REQUIRED = {
    "game_id", "season", "week", "game_type",
    "home_team", "away_team",
}

CANDIDATE_REQUIRED = {
    "game_id", "offense", "player_id",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def value(row, key):
    return str(row.get(key, "") or "").strip()


def integer(raw):
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        return None


def read_csv(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(
        path, "rt", encoding="utf-8-sig", newline=""
    ) as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path, fields, rows):
    with path.open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def find_column(fields, choices):
    lookup = {x.lower(): x for x in fields}
    for choice in choices:
        if choice.lower() in lookup:
            return lookup[choice.lower()]
    return None


def load_schedule(path):
    fields, rows = read_csv(path)
    missing = SCHEDULE_REQUIRED - set(fields)
    if missing:
        raise SystemExit(
            f"Schedule missing columns: {sorted(missing)}"
        )

    games = {}
    conflicts = set()

    for row in rows:
        if integer(value(row, "season")) != SEASON:
            continue
        if value(row, "game_type") != "REG":
            continue

        game = value(row, "game_id")
        record = (
            integer(value(row, "week")),
            value(row, "home_team").upper(),
            value(row, "away_team").upper(),
        )

        if (
            not game
            or record[0] is None
            or not 1 <= record[0] <= 25
            or not record[1]
            or not record[2]
            or record[1] == record[2]
        ):
            conflicts.add(game)
            continue

        if game in games and games[game] != record:
            conflicts.add(game)

        games[game] = record

    return games, conflicts


def load_candidates(path):
    fields, rows = read_csv(path)
    missing = CANDIDATE_REQUIRED - set(fields)

    if missing:
        raise SystemExit(
            f"Candidate columns missing: {sorted(missing)}"
        )

    identities = set()
    counts = Counter()
    player_teams = defaultdict(set)

    for row in rows:
        game = value(row, "game_id")
        team = value(row, "offense").upper()
        player = value(row, "player_id")

        if not game or not team or not player:
            raise SystemExit(
                "Candidate contains an empty identity"
            )

        key = (game, team, player)
        identities.add(key)
        counts[key] += 1
        player_teams[(game, player)].add(team)

    # Repeated rows for different kinds/windows are
    # legitimate. A player assigned to opposing teams
    # in the same game requires identity review.
    conflicts = {
        (game, player)
        for (game, player), teams in player_teams.items()
        if len(teams) > 1
    }

    repeated = sum(
        count - 1
        for count in counts.values()
        if count > 1
    )

    return (
        identities,
        conflicts,
        len(rows),
        repeated,
        len(identities),
    )


def load_rosters(path):
    if not path.is_file():
        return set(), set(), set(), "SOURCE_MISSING"

    fields, rows = read_csv(path)

    schema = {
        "id": find_column(
            fields, ["gsis_id", "player_gsis_id", "gsis"]
        ),
        "team": find_column(
            fields, [
                "team", "team_abbr",
                "club_code", "recent_team"
            ]
        ),
        "week": find_column(
            fields, ["week", "game_week"]
        ),
        "season": find_column(
            fields, ["season", "season_year"]
        ),
        "name": find_column(
            fields, [
                "full_name", "player_name",
                "player_display_name"
            ]
        ),
    }

    if not all(
        schema[k] for k in ("id", "team", "week", "season")
    ):
        raise SystemExit(
            "Weekly roster identity columns missing"
        )

    identities = set()
    team_weeks = set()
    names = defaultdict(set)

    for row in rows:
        if integer(value(row, schema["season"])) != SEASON:
            continue

        week = integer(value(row, schema["week"]))
        team = value(row, schema["team"]).upper()
        player = value(row, schema["id"])

        if week is None or not 1 <= week <= 25:
            continue
        if not team:
            continue

        team_weeks.add((team, week))

        if not player:
            continue

        key = (team, player, week)
        identities.add(key)

        if schema["name"]:
            name = value(row, schema["name"])
            if name:
                names[key].add(name)

    conflicts = {
        key for key, found in names.items()
        if len(found) > 1
    }

    return (
        identities, team_weeks,
        conflicts, "SOURCE_LOADED"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", default="NFL_LOCKBOX_2026"
    )
    args = parser.parse_args()

    root = Path(args.root)
    source = root / "source_data"
    analytics = root / "advanced_analytics"
    analytics.mkdir(parents=True, exist_ok=True)

    schedule_file = source / "schedule_2026.csv"
    staging_file = (
        source / "official_inactives_staging_2026.csv"
    )
    roster_file = (
        source / "weekly_rosters_2026.csv.gz"
    )
    candidate_file = (
        analytics / "game_day_availability_2026.csv"
    )

    output_file = (
        analytics / "inactive_reconciliation_2026.csv"
    )
    audit_file = (
        analytics
        / "inactive_reconciliation_audit_2026.json"
    )

    for path in (
        schedule_file, staging_file, candidate_file
    ):
        if not path.is_file():
            raise SystemExit(
                f"Required input missing: {path}"
            )

    games, schedule_conflicts = load_schedule(
        schedule_file
    )

    (
        candidates,
        candidate_conflicts,
        candidate_count,
        repeated_rows,
        unique_candidates,
    ) = load_candidates(candidate_file)

    (
        roster_ids,
        roster_team_weeks,
        roster_conflicts,
        roster_state,
    ) = load_rosters(roster_file)

    fields, staged = read_csv(staging_file)
    if fields != FIELDS:
        raise SystemExit(
            "Official inactive staging schema mismatch"
        )

    output = []
    counts = Counter()
    errors = []
    seen = set()

    for line, row in enumerate(staged, start=2):
        game = value(row, "game_id")
        team = value(row, "team").upper()
        player = value(row, "gsis_id")
        week = integer(value(row, "week"))

        staged_key = (game, team, player)
        roster_key = (team, player, week)

        # Handle malformed staging records without
        # crashing before an audit can be written.
        normalized = {
            field: value(row, field)
            for field in FIELDS
        }
        row_errors = validate_staging(normalized)

        if staged_key in seen:
            row_errors.append(
                "DUPLICATE_STAGED_IDENTITY"
            )
        seen.add(staged_key)

        schedule_status = "GAME_NOT_FOUND"

        if game in schedule_conflicts:
            schedule_status = "SCHEDULE_CONFLICT"
        elif game in games:
            scheduled_week, home, away = games[game]

            if scheduled_week != week:
                schedule_status = "WEEK_MISMATCH"
            elif team not in (home, away):
                schedule_status = "TEAM_NOT_IN_GAME"
            else:
                schedule_status = "EXACT_GAME_TEAM_WEEK"

        candidate_status = "NOT_IN_CANDIDATE_SET"

        if (game, player) in candidate_conflicts:
            candidate_status = (
                "CANDIDATE_IDENTITY_CONFLICT"
            )
        elif staged_key in candidates:
            candidate_status = "EXACT_CANDIDATE_MATCH"

        if roster_key in roster_conflicts:
            identity_status = "ROSTER_IDENTITY_CONFLICT"
        elif roster_key in roster_ids:
            identity_status = "EXACT_GSIS_TEAM_WEEK"
        elif roster_state != "SOURCE_LOADED":
            identity_status = "ROSTER_SOURCE_UNAVAILABLE"
        elif (team, week) not in roster_team_weeks:
            identity_status = "TEAM_WEEK_NOT_COVERED"
        else:
            identity_status = "GSIS_NOT_IN_TEAM_WEEK"

        if row_errors:
            status = "STAGING_REJECTED"
        elif schedule_status != "EXACT_GAME_TEAM_WEEK":
            status = "SCHEDULE_REVIEW"
        elif identity_status != "EXACT_GSIS_TEAM_WEEK":
            status = "IDENTITY_REVIEW"
        elif candidate_status == (
            "CANDIDATE_IDENTITY_CONFLICT"
        ):
            status = "CANDIDATE_REVIEW"
        else:
            status = "RECONCILED_NOT_AUTHENTICATED"

        counts[status] += 1

        if row_errors:
            errors.append({
                "line": line,
                "errors": row_errors,
            })

        output.append({
            "game_id": game,
            "week": value(row, "week"),
            "team": team,
            "gsis_id": player,
            "player_name": value(row, "player_name"),
            "schedule_status": schedule_status,
            "identity_status": identity_status,
            "candidate_status": candidate_status,
            "reconciliation_status": status,
            "official_status": value(
                row, "official_status"
            ),
            "source_url": value(row, "source_url"),
        })

    write_csv(output_file, RESULT_FIELDS, output)

    safe = (
        not schedule_conflicts
        and not candidate_conflicts
        and not errors
        and all(
            row["reconciliation_status"]
            == "RECONCILED_NOT_AUTHENTICATED"
            for row in output
        )
    )

    audit = {
        "phase": "8B",
        "version": VERSION,
        "generated_at_utc": now(),
        "status": (
            "PASS" if safe else "REVIEW_REQUIRED"
        ),
        "schedule_games": len(games),
        "schedule_conflicts": len(schedule_conflicts),
        "candidate_rows_preserved": candidate_count,
        "unique_candidate_player_game_ids": (
            unique_candidates
        ),
        "repeated_research_rows": repeated_rows,
        "candidate_identity_conflicts": len(
            candidate_conflicts
        ),
        "roster_source": roster_state,
        "roster_identity_conflicts": len(
            roster_conflicts
        ),
        "staging_rows": len(staged),
        "reconciled_rows": len(output),
        "reconciliation_counts": dict(counts),
        "staging_errors": errors,
        "official_inactives_authenticated": 0,
        "availability_rows_modified": 0,
        "official_active_inference_enabled": False,
        "limitations": [
            "Reconciliation is not source authentication.",
            "Official publication times are not independently verified.",
            "Weekly roster evidence does not establish game-day availability.",
            "Repeated research rows are not identity conflicts.",
            "Player names are not used for automatic identity matching.",
            "Players outside the candidate shortlist may be valid.",
            "No game-day availability status is changed.",
        ],
    }

    audit_file.write_text(
        json.dumps(audit, indent=2) + "\n",
        encoding="utf-8"
    )

    print(json.dumps(audit, indent=2))

    if not safe:
        raise SystemExit(
            "Inactive reconciliation requires review"
        )


if __name__ == "__main__":
    main()
