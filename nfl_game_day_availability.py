
#!/usr/bin/env python3
"""
NFL LOCKBOX — Phase 8A
Weekly Roster Evidence & Conservative Availability

Research only. No odds, bets, or ticket tracking.

Inputs:
  advanced_analytics/injury_lineup_research_2026.csv
  source_data/weekly_rosters_2026.csv.gz

Outputs:
  advanced_analytics/game_day_availability_2026.csv
  advanced_analytics/game_day_availability_audit_2026.json

IMPORTANT:
Weekly roster status is NOT official game-day
active/inactive status. Phase 8A never confirms
game-day availability.
"""

import argparse
import csv
import gzip
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SEASON = 2026
VERSION = "phase8a_roster_evidence_v1"

INPUT_NAME = "injury_lineup_research_2026.csv"
ROSTER_NAME = "weekly_rosters_2026.csv.gz"
OUTPUT_NAME = "game_day_availability_2026.csv"
AUDIT_NAME = "game_day_availability_audit_2026.json"

KEY_COLUMNS = (
    "game_id",
    "offense",
    "player_id",
    "kind",
)

REQUIRED_COLUMNS = {
    "game_id",
    "game_date",
    "week",
    "offense",
    "player_id",
    "player_name",
    "kind",
    "injury_evidence_freshness",
    "depth_chart_selection_type",
}


def read_csv(path):
    opener = (
        gzip.open
        if str(path).endswith(".gz")
        else open
    )

    with opener(
        path, "rt", encoding="utf-8-sig", newline=""
    ) as file:
        yield from csv.DictReader(file)


def csv_columns(path):
    opener = (
        gzip.open
        if str(path).endswith(".gz")
        else open
    )

    with opener(
        path, "rt", encoding="utf-8-sig", newline=""
    ) as file:
        return csv.DictReader(file).fieldnames or []


def find_column(columns, *candidates):
    lookup = {
        name.lower(): name for name in columns
    }

    for candidate in candidates:
        found = lookup.get(candidate.lower())
        if found:
            return found

    return None


def value(row, column):
    if row is None or column is None:
        return ""

    return str(
        row.get(column, "") or ""
    ).strip()


def as_int(raw):
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return None


def identity(row):
    return tuple(
        value(row, column)
        for column in KEY_COLUMNS
    )


def roster_schema(columns):
    return {
        "id": find_column(
            columns,
            "gsis_id",
            "player_gsis_id",
            "gsis"
        ),
        "team": find_column(
            columns,
            "team",
            "team_abbr",
            "club_code",
            "recent_team"
        ),
        "season": find_column(
            columns,
            "season",
            "season_year"
        ),
        "week": find_column(
            columns,
            "week",
            "game_week"
        ),
        "status": find_column(
            columns,
            "status",
            "roster_status"
        ),
        "position": find_column(
            columns,
            "position",
            "position_group"
        ),
        "name": find_column(
            columns,
            "full_name",
            "player_name",
            "player_display_name"
        ),
    }


def normalize_status(raw):
    status = str(raw or "").strip().upper()

    aliases = {
        "ACTIVE": "ACT",
        "INACTIVE": "INA",
        "INJURED RESERVE": "RES",
        "INJURED RESERVE LIST": "RES",
        "PRACTICE SQUAD": "PRC",
    }

    return aliases.get(status, status)


def roster_evidence_label(status):
    """
    Describe roster evidence, never game-day
    availability.
    """

    if not status:
        return "ROSTER_STATUS_UNSPECIFIED"

    if status == "ACT":
        return "ACTIVE_ROSTER_RECORD"

    if status == "INA":
        return "ROSTER_INACTIVE_CODE_UNVERIFIED"

    if status in {
        "RES",
        "IR",
        "PUP",
        "NFI",
        "SUS",
        "EXE",
    }:
        return "NON_ACTIVE_ROSTER_DESIGNATION"

    if status in {
        "PRC",
        "PS",
    }:
        return "PRACTICE_SQUAD_RECORD"

    return "OTHER_ROSTER_STATUS"


def load_rosters(path, relevant):
    """
    Index exact team, GSIS ID, season, week.

    Duplicate source records are retained for
    ambiguity detection rather than silently
    selecting one.
    """

    indexed = defaultdict(list)
    counts = Counter()

    if not path.is_file():
        return indexed, counts, {}, "SOURCE_MISSING"

    columns = csv_columns(path)
    schema = roster_schema(columns)

    for required in (
        "id",
        "team",
        "season",
        "week",
        "status",
    ):
        if not schema[required]:
            raise SystemExit(
                "Weekly roster source missing "
                f"required column: {required}"
            )

    for row in read_csv(path):
        counts["source_rows"] += 1

        season = as_int(
            value(row, schema["season"])
        )
        week = as_int(
            value(row, schema["week"])
        )

        if season != SEASON or week is None:
            continue

        team = value(
            row, schema["team"]
        ).upper()

        player_id = value(
            row, schema["id"]
        )

        if (team, player_id) not in relevant:
            continue

        key = (
            team,
            player_id,
            season,
            week,
        )

        indexed[key].append(row)
        counts["matched_source_rows"] += 1

    return indexed, counts, schema, "SOURCE_LOADED"


def classify_roster(records, schema):
    if not records:
        return {
            "match": "NO_MATCH",
            "status": "",
            "evidence": "NO_WEEKLY_ROSTER_RECORD",
            "position": "",
            "name": "",
            "duplicate_rows": 0,
            "ambiguous": False,
        }

    statuses = {
        normalize_status(
            value(row, schema["status"])
        )
        for row in records
    }

    positions = {
        value(row, schema["position"])
        for row in records
        if value(row, schema["position"])
    }

    names = {
        value(row, schema["name"])
        for row in records
        if value(row, schema["name"])
    }

    ambiguous = len(statuses) > 1

    if ambiguous:
        status = ""
        evidence = "CONFLICTING_ROSTER_RECORDS"
        match = "AMBIGUOUS"
    else:
        status = next(iter(statuses))
        evidence = roster_evidence_label(status)
        match = "MATCHED"

    return {
        "match": match,
        "status": status,
        "evidence": evidence,
        "position": (
            sorted(positions)[0]
            if len(positions) == 1 else ""
        ),
        "name": (
            sorted(names)[0]
            if len(names) == 1 else ""
        ),
        "duplicate_rows": max(
            len(records) - 1, 0
        ),
        "ambiguous": ambiguous,
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--root",
        default="NFL_LOCKBOX_2026",
    )

    parser.add_argument(
        "--require-rosters",
        action="store_true",
        help=(
            "Fail if weekly roster source is missing. "
            "Use after the downloader is integrated."
        ),
    )

    args = parser.parse_args()

    root = Path(args.root)
    analytics = root / "advanced_analytics"

    phase7_file = analytics / INPUT_NAME
    roster_file = (
        root / "source_data" / ROSTER_NAME
    )

    output_file = analytics / OUTPUT_NAME
    audit_file = analytics / AUDIT_NAME

    if not phase7_file.is_file():
        raise SystemExit(
            f"Missing Phase 7.1 input: {phase7_file}"
        )

    phase7 = list(read_csv(phase7_file))

    if not phase7:
        raise SystemExit(
            "Phase 7.1 input contains no rows"
        )

    missing_columns = (
        REQUIRED_COLUMNS - set(phase7[0])
    )

    if missing_columns:
        raise SystemExit(
            "Missing Phase 7.1 columns: "
            f"{sorted(missing_columns)}"
        )

    input_ids = [
        identity(row) for row in phase7
    ]

    if len(input_ids) != len(set(input_ids)):
        raise SystemExit(
            "Duplicate Phase 7.1 candidate identities"
        )

    relevant = {
        (
            value(row, "offense").upper(),
            value(row, "player_id"),
        )
        for row in phase7
    }

    rosters, source_counts, schema, source_state = (
        load_rosters(roster_file, relevant)
    )

    if (
        args.require_rosters
        and source_state != "SOURCE_LOADED"
    ):
        raise SystemExit(
            "Weekly roster source is required "
            "but missing"
        )

    output = []
    evidence_counts = Counter()
    flag_counts = Counter()
    week_counts = Counter()

    for row in phase7:
        entry = dict(row)

        season = SEASON
        week = as_int(value(row, "week"))

        if week is None or week < 1 or week > 25:
            raise SystemExit(
                "Invalid candidate week for "
                f"{value(row, 'game_id')}"
            )

        team = value(
            row, "offense"
        ).upper()

        player_id = value(
            row, "player_id"
        )

        key = (
            team,
            player_id,
            season,
            week,
        )

        records = rosters.get(key, [])

        evidence = classify_roster(
            records, schema
        )

        flags = []

        if source_state == "SOURCE_MISSING":
            flags.append(
                "WEEKLY_ROSTER_SOURCE_NOT_LOADED"
            )

        if evidence["match"] == "NO_MATCH":
            flags.append(
                "NO_EXACT_WEEKLY_ROSTER_MATCH"
            )

        if evidence["ambiguous"]:
            flags.append(
                "CONFLICTING_WEEKLY_ROSTER_STATUSES"
            )

        if evidence["evidence"] in {
            "NON_ACTIVE_ROSTER_DESIGNATION",
            "PRACTICE_SQUAD_RECORD",
            "ROSTER_INACTIVE_CODE_UNVERIFIED",
        }:
            flags.append(
                "ROSTER_ELIGIBILITY_REVIEW"
            )

        if (
            value(
                row, "injury_evidence_freshness"
            ) != "CURRENT_GAME_WEEK"
        ):
            flags.append(
                "NO_MATCHING_WEEK_INJURY_EVIDENCE"
            )

        if value(
            row, "depth_chart_stale"
        ) == "1":
            flags.append(
                "STALE_DEPTH_CHART_EVIDENCE"
            )

        # Never infer a player's game-day
        # availability from a roster record.
        flags.append(
            "OFFICIAL_GAME_DAY_STATUS_UNVERIFIED"
        )

        for flag in flags:
            flag_counts[flag] += 1

        evidence_counts[
            evidence["evidence"]
        ] += 1

        week_counts[str(week)] += 1

        entry.update({
            "weekly_roster_source": (
                "nflverse_weekly_rosters_2026"
                if source_state == "SOURCE_LOADED"
                else ""
            ),
            "weekly_roster_source_state": (
                source_state
            ),
            "weekly_roster_season": (
                season
                if evidence["match"] != "NO_MATCH"
                else ""
            ),
            "weekly_roster_week": (
                week
                if evidence["match"] != "NO_MATCH"
                else ""
            ),
            "weekly_roster_match": (
                evidence["match"]
            ),
            "weekly_roster_status_raw_normalized": (
                evidence["status"]
            ),
            "weekly_roster_evidence": (
                evidence["evidence"]
            ),
            "weekly_roster_position": (
                evidence["position"]
            ),
            "weekly_roster_player_name": (
                evidence["name"]
            ),
            "weekly_roster_duplicate_source_rows": (
                evidence["duplicate_rows"]
            ),
            "game_day_availability": (
                "UNVERIFIED"
            ),
            "game_day_availability_source": "",
            "game_day_availability_verified_at_utc": "",
            "game_day_availability_flags": (
                "|".join(flags)
            ),
        })

        output.append(entry)

    # Strict integrity checks.
    output_ids = [
        identity(row) for row in output
    ]

    if output_ids != input_ids:
        raise SystemExit(
            "Candidate identities or ordering changed"
        )

    if len(output) != len(phase7):
        raise SystemExit(
            "Phase 8A row count mismatch"
        )

    if sum(evidence_counts.values()) != len(output):
        raise SystemExit(
            "Roster evidence totals mismatch"
        )

    if sum(week_counts.values()) != len(output):
        raise SystemExit(
            "Candidate week totals mismatch"
        )

    if any(
        row["game_day_availability"]
        != "UNVERIFIED"
        for row in output
    ):
        raise SystemExit(
            "Unsafe game-day availability inference"
        )

    # Verify every original Phase 7.1 field
    # survives unchanged.
    original_columns = list(phase7[0])

    for before, after in zip(phase7, output):
        for column in original_columns:
            if before[column] != after[column]:
                raise SystemExit(
                    "Phase 7.1 field changed: "
                    f"{column}"
                )

    analytics.mkdir(
        parents=True,
        exist_ok=True
    )

    with output_file.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=list(output[0]),
        )

        writer.writeheader()
        writer.writerows(output)

    audit = {
        "phase": "8A",
        "version": VERSION,
        "season": SEASON,
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "status": (
            "PASS"
            if source_state == "SOURCE_LOADED"
            else "PASS_WITH_MISSING_SOURCE"
        ),
        "phase7_input_rows": len(phase7),
        "phase8_output_rows": len(output),
        "candidate_rows_preserved": True,
        "candidate_identities_preserved": True,
        "phase7_fields_preserved": True,
        "source_state": source_state,
        "source_file": str(roster_file),
        "source_columns": schema,
        "source_counts": dict(source_counts),
        "roster_evidence_counts": dict(
            evidence_counts
        ),
        "candidate_week_counts": dict(
            week_counts
        ),
        "research_flags": dict(flag_counts),
        "availability_counts": {
            "UNVERIFIED": len(output),
            "CONFIRMED_ACTIVE": 0,
            "CONFIRMED_INACTIVE": 0,
        },
        "validation": {
            "row_count": "PASS",
            "candidate_identity": "PASS",
            "original_fields": "PASS",
            "evidence_totals": "PASS",
            "week_totals": "PASS",
            "no_unsafe_availability_inference": "PASS",
        },
        "limitations": [
            "Weekly roster data is not an "
            "official game-day inactive feed.",
            "ACT is a roster code, not proof "
            "of game-day participation.",
            "INA is not interpreted as an "
            "official game-day inactive.",
            "Roster snapshots may be updated "
            "after a game; source timing must "
            "be verified before pregame use.",
            "Missing roster evidence does not "
            "establish that a player is inactive.",
            "Official inactive and starter "
            "verification remain Phase 8B.",
            "No betting odds or tickets.",
        ],
    }

    audit_file.write_text(
        json.dumps(audit, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps({
        "version": VERSION,
        "status": audit["status"],
        "input_rows": len(phase7),
        "output_rows": len(output),
        "roster_source": source_state,
        "roster_evidence": dict(
            evidence_counts
        ),
        "validation": audit["validation"],
    }, indent=2))


if __name__ == "__main__":
    main()
