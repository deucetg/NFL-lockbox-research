
#!/usr/bin/env python3
"""
NFL LOCKBOX — Phase 8B Step 4A
Schedule and GSIS Reconciliation

Research-only verification.

Reads:
  source_data/schedule_2026.csv
  source_data/official_inactives_staging_2026.csv
  source_data/weekly_rosters_2026.csv.gz
  advanced_analytics/game_day_availability_2026.csv

Writes:
  advanced_analytics/inactive_reconciliation_2026.csv
  advanced_analytics/inactive_reconciliation_audit_2026.json

Never modifies earlier research or confirms game-day
availability. A roster match is identity evidence,
not official active/inactive evidence.
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

VERSION = "phase8b_reconciliation_v1"
SEASON = 2026

RESULT_FIELDS = [
    "game_id",
    "week",
    "team",
    "gsis_id",
    "player_name",
    "schedule_status",
    "identity_status",
    "candidate_status",
    "reconciliation_status",
    "official_status",
    "source_url",
]

REQUIRED_SCHEDULE = {
    "game_id", "season", "week",
    "home_team", "away_team", "game_type",
}

REQUIRED_CANDIDATES = {
    "game_id", "offense", "player_id",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def read_csv(path):
    opener = (
        gzip.open
        if str(path).endswith(".gz")
        else open
    )
    with opener(
        path, "rt", encoding="utf-8-sig",
        newline=""
    ) as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path, fields, rows):
    with path.open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields
        )
        writer.writeheader()
        writer.writerows(rows)


def number(value):
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


def value(row, field):
    return str(row.get(field, "") or "").strip()


def find_column(fields, choices):
    lookup = {x.lower(): x for x in fields}
    for choice in choices:
        if choice.lower() in lookup:
            return lookup[choice.lower()]
    return None


def roster_schema(fields):
    return {
        "id": find_column(
            fields,
            ["gsis_id", "player_gsis_id", "gsis"]
        ),
        "team": find_column(
            fields,
            ["team", "team_abbr",
             "club_code", "recent_team"]
        ),
        "week": find_column(
            fields, ["week", "game_week"]
        ),
        "season": find_column(
            fields, ["season", "season_year"]
        ),
        "name": find_column(
            fields,
            ["full_name", "player_name",
             "player_display_name"]
        ),
    }


def load_schedule(path):
    fields, rows = read_csv(path)
    missing = REQUIRED_SCHEDULE - set(fields)

    if missing:
        raise SystemExit(
            f"Schedule columns missing: {sorted(missing)}"
        )

    games = {}
    conflicts = set()

    for row in rows:
        if number(value(row, "season")) != SEASON:
            continue

        if value(row, "game_type") != "REG":
            continue

        game_id = value(row, "game_id")
        record = (
            number(value(row, "week")),
            value(row, "home_team").upper(),
            value(row, "away_team").upper(),
        )

        if (
            not game_id
            or record[0] is None
            or not record[1]
            or not record[2]
            or record[1] == record[2]
        ):
            conflicts.add(game_id)
            continue

        if game_id in games and games[game_id] != record:
            conflicts.add(game_id)

        games[game_id] = record

    return games, conflicts


def load_candidates(path):
    fields, rows = read_csv(path)
    missing = REQUIRED_CANDIDATES - set(fields)

    if missing:
        raise SystemExit(
            f"Candidate columns missing: {sorted(missing)}"
        )

    identities = set()
    conflicts = set()

    for row in rows:
        key = (
            value(row, "game_id"),
            value(row, "offense").upper(),
            value(row, "player_id"),
        )

        if key in identities:
            conflicts.add(key)

        identities.add(key)

    return identities, conflicts, len(rows)


def load_rosters(path):
    if not path.is_file():
        return set(), set(), set(), "SOURCE_MISSING"

    fields, rows = read_csv(path)
    schema = roster_schema(fields)

    if not all(
        schema[x]
        for x in ("id", "team", "week", "season")
    ):
        raise SystemExit(
            "Weekly roster identity columns missing"
        )

    identities = set()
    team_weeks = set()
    conflicts = set()
    names_by_key = defaultdict(set)

    for row in rows:
        if number(value(
            row, schema["season"]
        )) != SEASON:
            continue

        week = number(value(row, schema["week"]))
        team = value(row, schema["team"]).upper()
        player = value(row, schema["id"])

        if week is None or not team:
            continue

        team_weeks.add((team, week))

        if not player:
            continue

        key = (team, player, week)
        identities.add(key)

        if schema["name"]:
            name = value(row, schema["name"])
            if name:
                names_by_key[key].add(name)

    for key, names in names_by_key.items():
        if len(names) > 1:
            conflicts.add(key)

    return (
        identities,
        team_weeks,
        conflicts,
        "SOURCE_LOADED",
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

    schedule_path = source / "schedule_2026.csv"
    staging_path = (
        source / "official_inactives_staging_2026.csv"
    )
    roster_path = (
        source / "weekly_rosters_2026.csv.gz"
    )
    candidate_path = (
        analytics / "game_day_availability_2026.csv"
    )

    output_path = (
        analytics / "inactive_reconciliation_2026.csv"
    )
    audit_path = (
        analytics
        / "inactive_reconciliation_audit_2026.json"
    )

    required_files = [
        schedule_path,
        staging_path,
        candidate_path,
    ]

    missing_files = [
        str(path)
        for path in required_files
        if not path.is_file()
    ]

    if missing_files:
        raise SystemExit(
            f"Required inputs missing: {missing_files}"
        )

    games, game_conflicts = load_schedule(
        schedule_path
    )

    candidates, candidate_conflicts, candidate_count = (
        load_candidates(candidate_path)
    )

    (
        roster_ids,
        roster_team_weeks,
        roster_conflicts,
        roster_state,
    ) = load_rosters(roster_path)

    fields, staging = read_csv(staging_path)

    if fields != FIELDS:
        raise SystemExit(
            "Phase 8B staging schema mismatch"
        )

    output = []
    status_counts = Counter()
    seen = set()
    errors = []

    for line, row in enumerate(staging, start=2):
        game_id = value(row, "game_id")
        team = value(row, "team").upper()
        player = value(row, "gsis_id")
        week = number(value(row, "week"))

        key = (game_id, team, player)
        roster_key = (team, player, week)

        row_errors = validate_staging(row)

        if key in seen:
            row_errors.append("DUPLICATE_STAGED_IDENTITY")

        seen.add(key)

        schedule_status = "GAME_NOT_FOUND"
        identity_status = "UNVERIFIED"
        candidate_status = "NOT_IN_CANDIDATE_SET"

        if game_id in game_conflicts:
            schedule_status = "SCHEDULE_CONFLICT"

        elif game_id in games:
            scheduled_week, home, away = games[game_id]

            if scheduled_week != week:
                schedule_status = "WEEK_MISMATCH"

            elif team not in {home, away}:
                schedule_status = "TEAM_NOT_IN_GAME"

            else:
                schedule_status = "EXACT_GAME_TEAM_WEEK"

        if key in candidate_conflicts:
            candidate_status = "CANDIDATE_IDENTITY_CONFLICT"
        elif key in candidates:
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

        # A player may be officially inactive but
        # absent from the weekly roster source.
        # Do not treat roster absence as proof that
        # the official announcement is incorrect.
        #
        # Only exact, non-conflicting identity
        # evidence qualifies for reconciliation.
        if row_errors:
            status = "STAGING_REJECTED"

        elif schedule_status != "EXACT_GAME_TEAM_WEEK":
            status = "SCHEDULE_REVIEW"

        elif identity_status != "EXACT_GSIS_TEAM_WEEK":
            status = "IDENTITY_REVIEW"

        elif candidate_status == "CANDIDATE_IDENTITY_CONFLICT":
            status = "CANDIDATE_REVIEW"

        else:
            status = "RECONCILED_NOT_AUTHENTICATED"

        status_counts[status] += 1

        if row_errors:
            errors.append({
                "line": line,
                "errors": row_errors,
            })

        output.append({
            "game_id": game_id,
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

    write_csv(output_path, RESULT_FIELDS, output)

    safe = (
        not errors
        and not game_conflicts
        and not candidate_conflicts
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
        "schedule_conflicts": len(game_conflicts),
        "candidate_rows_preserved": candidate_count,
        "candidate_identity_conflicts": len(
            candidate_conflicts
        ),
        "roster_source": roster_state,
        "roster_identity_conflicts": len(
            roster_conflicts
        ),
        "staging_rows": len(staging),
        "reconciled_rows": len(output),
        "reconciliation_counts": dict(status_counts),
        "staging_errors": errors,
        "official_inactives_authenticated": 0,
        "availability_rows_modified": 0,
        "official_active_inference_enabled": False,
        "limitations": [
            "Reconciliation is not source authentication.",
            "Staged publication times are not independently verified.",
            "Weekly roster membership is not official game-day availability.",
            "Roster absence may reflect stale coverage or roster changes.",
            "Names are not used for automatic identity matching.",
            "Candidates outside the research shortlist may be valid NFL players.",
            "No inactive or active status is written into Phase 8A.1.",
        ],
    }

    audit_path.write_text(
        json.dumps(audit, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(audit, indent=2))

    if not safe:
        raise SystemExit(
            "Inactive reconciliation requires review"
        )


if __name__ == "__main__":
    main()
