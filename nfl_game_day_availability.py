
#!/usr/bin/env python3
"""
NFL LOCKBOX — Phase 8A.1
Roster Freshness Intelligence

Research-only roster evidence.
No betting odds, wagers, or tickets.

Preserves Phase 7.1 candidate rows and fields.
Separates unpublished weeks from missing player
records within published team-weeks.

Weekly roster status NEVER establishes official
game-day active or inactive status.
"""

import argparse
import csv
import gzip
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SEASON = 2026
VERSION = "phase8a1_roster_freshness_v1"

INPUT = "injury_lineup_research_2026.csv"
ROSTERS = "weekly_rosters_2026.csv.gz"
OUTPUT = "game_day_availability_2026.csv"
AUDIT = "game_day_availability_audit_2026.json"

KEY_FIELDS = (
    "game_id",
    "offense",
    "player_id",
    "kind",
)

REQUIRED = {
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


def now():
    return datetime.now(timezone.utc).isoformat()


def read_csv(path):
    opener = (
        gzip.open if str(path).endswith(".gz")
        else open
    )
    with opener(
        path, "rt", encoding="utf-8-sig", newline=""
    ) as handle:
        yield from csv.DictReader(handle)


def headers(path):
    opener = (
        gzip.open if str(path).endswith(".gz")
        else open
    )
    with opener(
        path, "rt", encoding="utf-8-sig", newline=""
    ) as handle:
        return csv.DictReader(handle).fieldnames or []


def column(names, *choices):
    lookup = {x.lower(): x for x in names}
    for choice in choices:
        if choice.lower() in lookup:
            return lookup[choice.lower()]
    return None


def val(row, name):
    if not row or not name:
        return ""
    return str(row.get(name, "") or "").strip()


def number(raw):
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def identity(row):
    return tuple(val(row, x) for x in KEY_FIELDS)


def schema_for(names):
    return {
        "id": column(
            names, "gsis_id", "player_gsis_id", "gsis"
        ),
        "team": column(
            names, "team", "team_abbr",
            "club_code", "recent_team"
        ),
        "season": column(
            names, "season", "season_year"
        ),
        "week": column(
            names, "week", "game_week"
        ),
        "status": column(
            names, "status", "roster_status"
        ),
        "position": column(
            names, "position", "position_group"
        ),
        "name": column(
            names, "full_name", "player_name",
            "player_display_name"
        ),
    }


def normalize_status(raw):
    status = raw.strip().upper()
    return {
        "ACTIVE": "ACT",
        "INACTIVE": "INA",
        "INJURED RESERVE": "RES",
        "INJURED RESERVE LIST": "RES",
        "PRACTICE SQUAD": "PRC",
    }.get(status, status)


def evidence_label(status):
    if not status:
        return "ROSTER_STATUS_UNSPECIFIED"
    if status == "ACT":
        return "ACTIVE_ROSTER_RECORD"
    if status == "INA":
        return "ROSTER_INACTIVE_CODE_UNVERIFIED"
    if status in {
        "RES", "IR", "PUP", "NFI", "SUS", "EXE"
    }:
        return "NON_ACTIVE_ROSTER_DESIGNATION"
    if status in {"PRC", "PS"}:
        return "PRACTICE_SQUAD_RECORD"
    return "OTHER_ROSTER_STATUS"


def load_manifest(path):
    if not path.is_file():
        return {}, "MANIFEST_MISSING"
    try:
        data = json.loads(path.read_text("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Manifest is not an object")
        return data, "MANIFEST_LOADED"
    except (ValueError, OSError) as error:
        return {"error": str(error)}, "MANIFEST_INVALID"


def load_rosters(path, relevant):
    indexed = defaultdict(list)
    weeks = set()
    team_weeks = set()
    counts = Counter()

    if not path.is_file():
        return (
            indexed, weeks, team_weeks,
            counts, {}, "SOURCE_MISSING"
        )

    spec = schema_for(headers(path))

    for required in (
        "id", "team", "season", "week", "status"
    ):
        if not spec[required]:
            raise SystemExit(
                "Weekly roster source missing "
                f"column: {required}"
            )

    for row in read_csv(path):
        counts["source_rows"] += 1

        season = number(val(row, spec["season"]))
        week = number(val(row, spec["week"]))

        if season != SEASON or week is None:
            continue

        if not 1 <= week <= 25:
            continue

        team = val(row, spec["team"]).upper()
        player = val(row, spec["id"])

        counts["season_rows"] += 1
        weeks.add(week)

        if team:
            team_weeks.add((team, week))

        if (team, player) not in relevant:
            continue

        indexed[(team, player, week)].append(row)
        counts["matched_source_rows"] += 1

    return (
        indexed, weeks, team_weeks,
        counts, spec, "SOURCE_LOADED"
    )


def classify(records, spec):
    if not records:
        return {
            "match": "NO_MATCH",
            "status": "",
            "evidence": "NO_WEEKLY_ROSTER_RECORD",
            "position": "",
            "name": "",
            "duplicates": 0,
            "ambiguous": False,
        }

    statuses = {
        normalize_status(val(row, spec["status"]))
        for row in records
    }

    positions = {
        val(row, spec["position"])
        for row in records
        if val(row, spec["position"])
    }

    names = {
        val(row, spec["name"])
        for row in records
        if val(row, spec["name"])
    }

    ambiguous = len(statuses) > 1

    status = (
        "" if ambiguous else next(iter(statuses))
    )

    return {
        "match": (
            "AMBIGUOUS" if ambiguous else "MATCHED"
        ),
        "status": status,
        "evidence": (
            "CONFLICTING_ROSTER_RECORDS"
            if ambiguous else evidence_label(status)
        ),
        "position": (
            next(iter(positions))
            if len(positions) == 1 else ""
        ),
        "name": (
            next(iter(names))
            if len(names) == 1 else ""
        ),
        "duplicates": max(len(records) - 1, 0),
        "ambiguous": ambiguous,
    }


def coverage_for(week, team, weeks, team_weeks,
                 source_state):
    if source_state != "SOURCE_LOADED":
        return "SOURCE_UNAVAILABLE"

    if not weeks:
        return "NO_SEASON_COVERAGE"

    latest = max(weeks)

    if week > latest:
        return "WEEK_NOT_YET_IN_SOURCE"

    if week not in weeks:
        return "WEEK_MISSING_FROM_SOURCE"

    if (team, week) not in team_weeks:
        return "TEAM_WEEK_NOT_IN_SOURCE"

    return "TEAM_WEEK_COVERED"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", default="NFL_LOCKBOX_2026"
    )
    parser.add_argument(
        "--require-rosters", action="store_true"
    )
    args = parser.parse_args()

    root = Path(args.root)
    analytics = root / "advanced_analytics"
    source = root / "source_data"

    input_file = analytics / INPUT
    roster_file = source / ROSTERS
    manifest_file = source / "refresh_manifest.json"

    if not input_file.is_file():
        raise SystemExit(
            f"Missing Phase 7.1 input: {input_file}"
        )

    phase7 = list(read_csv(input_file))
    if not phase7:
        raise SystemExit("Phase 7.1 input is empty")

    missing = REQUIRED - set(phase7[0])
    if missing:
        raise SystemExit(
            f"Missing Phase 7.1 fields: {sorted(missing)}"
        )

    original_fields = list(phase7[0])
    input_ids = [identity(x) for x in phase7]

    if len(input_ids) != len(set(input_ids)):
        raise SystemExit(
            "Duplicate Phase 7.1 candidate identities"
        )

    relevant = {
        (
            val(row, "offense").upper(),
            val(row, "player_id")
        )
        for row in phase7
    }

    (
        indexed, published_weeks, team_weeks,
        source_counts, spec, source_state
    ) = load_rosters(roster_file, relevant)

    if (
        args.require_rosters
        and source_state != "SOURCE_LOADED"
    ):
        raise SystemExit(
            "Required weekly roster source missing"
        )

    manifest, manifest_state = load_manifest(
        manifest_file
    )

    roster_manifest = (
        manifest.get("sources", {})
        .get("weekly_rosters", {})
    )

    downloaded_at = (
        roster_manifest.get("downloaded_at_utc", "")
        if isinstance(roster_manifest, dict) else ""
    )

    source_url = (
        roster_manifest.get("url", "")
        if isinstance(roster_manifest, dict) else ""
    )

    manifest_verified = (
        manifest_state == "MANIFEST_LOADED"
        and roster_manifest.get("status") == "verified"
        and source_state == "SOURCE_LOADED"
    )

    latest_week = (
        max(published_weeks)
        if published_weeks else None
    )

    output = []
    evidence_counts = Counter()
    coverage_counts = Counter()
    match_counts = Counter()
    flags_count = Counter()
    candidate_weeks = Counter()

    for row in phase7:
        entry = dict(row)

        week = number(val(row, "week"))
        if week is None or not 1 <= week <= 25:
            raise SystemExit(
                "Invalid candidate week: "
                f"{val(row, 'game_id')}"
            )

        team = val(row, "offense").upper()
        player = val(row, "player_id")

        coverage = coverage_for(
            week, team, published_weeks,
            team_weeks, source_state
        )

        records = indexed.get(
            (team, player, week), []
        )

        evidence = classify(records, spec)

        # Coverage and exact matching are separate
        # dimensions. Never call an unpublished week
        # a player-ID mismatch.
        if coverage == "TEAM_WEEK_COVERED":
            if evidence["match"] == "MATCHED":
                match_reason = "EXACT_MATCH"
            elif evidence["match"] == "AMBIGUOUS":
                match_reason = "CONFLICTING_RECORDS"
            else:
                match_reason = (
                    "PLAYER_RECORD_MISSING_IN_COVERED_TEAM_WEEK"
                )
        else:
            if evidence["match"] != "NO_MATCH":
                raise SystemExit(
                    "Roster record exists outside "
                    "its declared team-week coverage"
                )
            match_reason = coverage

        flags = []

        if coverage == "SOURCE_UNAVAILABLE":
            flags.append(
                "WEEKLY_ROSTER_SOURCE_NOT_LOADED"
            )
        elif coverage == "NO_SEASON_COVERAGE":
            flags.append(
                "NO_2026_ROSTER_COVERAGE"
            )
        elif coverage == "WEEK_NOT_YET_IN_SOURCE":
            flags.append(
                "ROSTER_WEEK_NOT_YET_PUBLISHED"
            )
        elif coverage == "WEEK_MISSING_FROM_SOURCE":
            flags.append(
                "ROSTER_WEEK_MISSING_IN_SOURCE"
            )
        elif coverage == "TEAM_WEEK_NOT_IN_SOURCE":
            flags.append(
                "ROSTER_TEAM_WEEK_NOT_COVERED"
            )

        if match_reason == (
            "PLAYER_RECORD_MISSING_IN_COVERED_TEAM_WEEK"
        ):
            flags.append(
                "EXACT_PLAYER_TEAM_WEEK_RECORD_MISSING"
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
            flags.append("ROSTER_ELIGIBILITY_REVIEW")

        if val(
            row, "injury_evidence_freshness"
        ) != "CURRENT_GAME_WEEK":
            flags.append(
                "NO_MATCHING_WEEK_INJURY_EVIDENCE"
            )

        if val(row, "depth_chart_stale") == "1":
            flags.append(
                "STALE_DEPTH_CHART_EVIDENCE"
            )

        if not manifest_verified:
            flags.append(
                "ROSTER_REFRESH_MANIFEST_UNVERIFIED"
            )

        flags.append(
            "OFFICIAL_GAME_DAY_STATUS_UNVERIFIED"
        )

        for flag in flags:
            flags_count[flag] += 1

        evidence_counts[evidence["evidence"]] += 1
        coverage_counts[coverage] += 1
        match_counts[match_reason] += 1
        candidate_weeks[str(week)] += 1

        entry.update({
            "weekly_roster_source": (
                "nflverse_weekly_rosters_2026"
                if source_state == "SOURCE_LOADED"
                else ""
            ),
            "weekly_roster_source_state": source_state,
            "weekly_roster_season": (
                SEASON if records else ""
            ),
            "weekly_roster_week": (
                week if records else ""
            ),
            "weekly_roster_match": evidence["match"],
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
                evidence["duplicates"]
            ),
            "game_day_availability": "UNVERIFIED",
            "game_day_availability_source": "",
            "game_day_availability_verified_at_utc": "",
            "game_day_availability_flags": (
                "|".join(flags)
            ),
            "weekly_roster_coverage": coverage,
            "weekly_roster_match_reason": match_reason,
            "weekly_roster_latest_source_week": (
                latest_week if latest_week is not None
                else ""
            ),
            "weekly_roster_downloaded_at_utc": (
                downloaded_at
            ),
            "weekly_roster_manifest_verified": (
                "1" if manifest_verified else "0"
            ),
        })

        output.append(entry)

    # Integrity checks.
    if len(output) != len(phase7):
        raise SystemExit("Candidate row count changed")

    if [identity(x) for x in output] != input_ids:
        raise SystemExit(
            "Candidate identities/order changed"
        )

    for before, after in zip(phase7, output):
        for field in original_fields:
            if before[field] != after[field]:
                raise SystemExit(
                    f"Phase 7.1 field changed: {field}"
                )

    if sum(coverage_counts.values()) != len(output):
        raise SystemExit("Coverage count mismatch")

    if sum(match_counts.values()) != len(output):
        raise SystemExit("Match reason count mismatch")

    if sum(evidence_counts.values()) != len(output):
        raise SystemExit("Evidence count mismatch")

    if sum(candidate_weeks.values()) != len(output):
        raise SystemExit("Week count mismatch")

    if any(
        x["game_day_availability"] != "UNVERIFIED"
        for x in output
    ):
        raise SystemExit(
            "Unsafe game-day availability inference"
        )

    if any(
        x["weekly_roster_match_reason"]
        == "PLAYER_RECORD_MISSING_IN_COVERED_TEAM_WEEK"
        and x["weekly_roster_coverage"]
        != "TEAM_WEEK_COVERED"
        for x in output
    ):
        raise SystemExit(
            "Uncovered week mislabeled as player mismatch"
        )

    analytics.mkdir(parents=True, exist_ok=True)

    with (analytics / OUTPUT).open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(output[0])
        )
        writer.writeheader()
        writer.writerows(output)

    audit = {
        "phase": "8A.1",
        "version": VERSION,
        "season": SEASON,
        "generated_utc": now(),
        "status": (
            "PASS" if manifest_verified
            else "PASS_WITH_SOURCE_WARNING"
        ),
        "phase7_input_rows": len(phase7),
        "phase8_output_rows": len(output),
        "candidate_rows_preserved": True,
        "candidate_identities_preserved": True,
        "phase7_fields_preserved": True,
        "source_state": source_state,
        "source_file": str(roster_file),
        "source_url": source_url,
        "source_downloaded_at_utc": downloaded_at,
        "source_publication_time_verified": False,
        "manifest_state": manifest_state,
        "manifest_verified": manifest_verified,
        "source_columns": spec,
        "source_counts": dict(source_counts),
        "published_roster_weeks": sorted(
            published_weeks
        ),
        "latest_published_roster_week": latest_week,
        "covered_team_weeks": len(team_weeks),
        "candidate_week_counts": dict(candidate_weeks),
        "roster_coverage_counts": dict(coverage_counts),
        "roster_match_reason_counts": dict(match_counts),
        "roster_evidence_counts": dict(evidence_counts),
        "research_flags": dict(flags_count),
        "availability_counts": {
            "UNVERIFIED": len(output),
            "CONFIRMED_ACTIVE": 0,
            "CONFIRMED_INACTIVE": 0,
        },
        "validation": {
            "row_count": "PASS",
            "candidate_identity": "PASS",
            "original_fields": "PASS",
            "coverage_totals": "PASS",
            "match_reason_totals": "PASS",
            "evidence_totals": "PASS",
            "candidate_week_totals": "PASS",
            "unpublished_week_classification": "PASS",
            "no_unsafe_availability_inference": "PASS",
        },
        "limitations": [
            "Latest published week is inferred from "
            "weeks present in the source file.",
            "A missing team-week may indicate "
            "incomplete source coverage.",
            "A missing player record within a "
            "covered team-week is not proof of "
            "an incorrect GSIS ID.",
            "Download timestamp is not the "
            "original publication timestamp.",
            "Weekly roster ACT/INA codes are "
            "not official game-day availability.",
            "Official inactive verification "
            "remains Phase 8B.",
            "No betting data is stored.",
        ],
    }

    (analytics / AUDIT).write_text(
        json.dumps(audit, indent=2) + "\n",
        encoding="utf-8"
    )

    print(json.dumps({
        "version": VERSION,
        "status": audit["status"],
        "candidate_rows": len(output),
        "latest_roster_week": latest_week,
        "coverage": dict(coverage_counts),
        "match_reasons": dict(match_counts),
        "validation": audit["validation"],
    }, indent=2))


if __name__ == "__main__":
    main()
