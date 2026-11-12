
#!/usr/bin/env python3
"""NFL LOCKBOX — Phase 7: Injury & Lineup Intelligence.

Research only. Injury reports and depth charts are evidence,
not confirmation of game-day availability or starting roles.

Uses exact GSIS ID + team matching. Preserves Phase 6 rankings.
"""

import argparse
import csv
import gzip
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path


SEASON = 2026


def read_csv(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def get_header(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as f:
        return csv.DictReader(f).fieldnames or []


def column(headers, *names):
    lookup = {h.lower(): h for h in headers}
    for name in names:
        if name.lower() in lookup:
            return lookup[name.lower()]
    return None


def field(row, name):
    if row is None or name is None:
        return ""
    return str(row.get(name, "") or "").strip()


def number(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_date(value):
    text = str(value or "").strip()
    if not text:
        return None

    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass

    for fmt in ("%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    return None


def source_spec(headers, kind):
    spec = {
        "id": column(
            headers, "gsis_id", "player_gsis_id", "gsis"
        ),
        "team": column(
            headers, "team", "club_code", "team_abbr"
        ),
        "week": column(headers, "week", "game_week"),
        "season": column(
            headers, "season", "season_year"
        ),
        "date": column(
            headers, "dt", "date",
            "report_date", "game_date"
        ),
        "name": column(
            headers, "full_name", "player_name", "player"
        ),
    }

    if kind == "injuries":
        spec.update({
            "report_status": column(
                headers, "report_status",
                "game_status", "injury_status"
            ),
            "practice_status": column(
                headers, "practice_status",
                "practice_participation"
            ),
            "primary_injury": column(
                headers, "report_primary_injury",
                "primary_injury", "injury_type"
            ),
            "secondary_injury": column(
                headers, "report_secondary_injury",
                "secondary_injury"
            ),
            "practice_injury": column(
                headers, "practice_primary_injury"
            ),
        })
    else:
        spec.update({
            "position": column(
                headers, "pos_abb",
                "position", "pos_name", "pos"
            ),
            "position_group": column(
                headers, "pos_grp", "position_group"
            ),
            "depth": column(
                headers, "pos_rank", "depth_team",
                "depth_chart_order", "depth_rank"
            ),
            "slot": column(
                headers, "pos_slot", "depth_slot"
            ),
        })

    return spec


def load_source(path, spec, relevant, label, audit_counts):
    records = defaultdict(list)

    for row in read_csv(path):
        audit_counts[label + "_source_rows"] += 1

        season = number(field(row, spec["season"]))
        if season is not None and season != SEASON:
            continue

        team = field(row, spec["team"]).upper()
        pid = field(row, spec["id"])
        key = (team, pid)

        if key not in relevant:
            continue

        report_date = parse_date(field(row, spec["date"]))
        week = number(field(row, spec["week"]))

        records[key].append({
            "row": row,
            "date": report_date,
            "week": week,
        })

        audit_counts[label + "_matched_source_rows"] += 1

    for items in records.values():
        items.sort(
            key=lambda x: (
                x["date"] or date.min,
                x["week"] if x["week"] is not None else -1,
            ),
            reverse=True,
        )

    return records


def choose_injury(records, target_week, game_date):
    """Prefer current-week evidence; otherwise historical only."""

    eligible = []

    for item in records:
        week = item["week"]
        report_date = item["date"]

        if (
            target_week is not None
            and week is not None
            and week > target_week
        ):
            continue

        if (
            report_date is not None
            and game_date is not None
            and report_date > game_date
        ):
            continue

        eligible.append(item)

    current = [
        item for item in eligible
        if (
            target_week is not None
            and item["week"] == target_week
        )
    ]

    if current:
        current.sort(
            key=lambda x: x["date"] or date.min,
            reverse=True,
        )
        return current[0], "CURRENT_GAME_WEEK"

    historical = [
        item for item in eligible
        if (
            target_week is not None
            and item["week"] is not None
            and item["week"] < target_week
        )
    ]

    if historical:
        historical.sort(
            key=lambda x: (
                x["week"],
                x["date"] or date.min,
            ),
            reverse=True,
        )
        return historical[0], "HISTORICAL"

    return None, "UNKNOWN"


def choose_depth(records, game_date):
    """Use latest dated snapshot not after the game."""

    eligible = []

    for item in records:
        snapshot = item["date"]

        if snapshot is None:
            continue

        if game_date is None or snapshot > game_date:
            continue

        eligible.append(item)

    if not eligible:
        return None, "UNKNOWN"

    latest_date = max(x["date"] for x in eligible)
    latest = [
        x for x in eligible if x["date"] == latest_date
    ]

    # Preserve an actual depth entry, without asserting starter.
    latest.sort(
        key=lambda x: (
            number(
                field(x["row"], DEPTH_SPEC["depth"])
            )
            if number(
                field(x["row"], DEPTH_SPEC["depth"])
            ) is not None
            else 999,
            field(x["row"], DEPTH_SPEC["position"]),
        )
    )

    return latest[0], "DATED_SNAPSHOT"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", default="NFL_LOCKBOX_2026"
    )
    args = parser.parse_args()

    root = Path(args.root)
    source = root / "source_data"
    output_dir = root / "advanced_analytics"

    ranking_path = (
        output_dir / "upcoming_research_rankings_2026.csv"
    )
    injury_path = source / "injuries_2026.csv.gz"
    depth_path = source / "depth_charts_2026.csv.gz"

    for path in (ranking_path, injury_path, depth_path):
        if not path.is_file():
            raise SystemExit(f"Missing input: {path}")

    rankings = list(read_csv(ranking_path))

    if not rankings:
        raise SystemExit("Phase 6 rankings are empty")

    required = {
        "game_id", "game_date", "week", "offense",
        "player_id", "player_name", "kind",
        "research_priority_index",
    }

    missing = required - set(rankings[0])

    if missing:
        raise SystemExit(
            f"Missing ranking columns: {sorted(missing)}"
        )

    injury_headers = get_header(injury_path)
    depth_headers = get_header(depth_path)

    injury_spec = source_spec(
        injury_headers, "injuries"
    )
    depth_spec = source_spec(
        depth_headers, "depth_charts"
    )

    global DEPTH_SPEC
    DEPTH_SPEC = depth_spec

    for label, spec in (
        ("injuries", injury_spec),
        ("depth_charts", depth_spec),
    ):
        if not spec["id"] or not spec["team"]:
            raise SystemExit(
                f"{label}: exact GSIS/team join unavailable"
            )

    if not injury_spec["week"]:
        raise SystemExit(
            "Injury data lacks a week column"
        )

    if not depth_spec["date"]:
        raise SystemExit(
            "Depth charts lack a snapshot date column"
        )

    relevant = {
        (
            row["offense"].strip().upper(),
            row["player_id"].strip(),
        )
        for row in rankings
    }

    counts = Counter()

    injuries = load_source(
        injury_path, injury_spec, relevant,
        "injuries", counts
    )
    depths = load_source(
        depth_path, depth_spec, relevant,
        "depth_charts", counts
    )

    results = []
    flags_count = Counter()
    injury_freshness = Counter()
    depth_freshness = Counter()
    designations = Counter()
    unique_injury_players = set()
    unique_depth_players = set()

    for ranking in rankings:
        entry = dict(ranking)

        key = (
            ranking["offense"].strip().upper(),
            ranking["player_id"].strip(),
        )

        game_date = parse_date(ranking["game_date"])
        game_week = number(ranking["week"])

        injury, injury_class = choose_injury(
            injuries.get(key, []),
            game_week,
            game_date,
        )

        depth, depth_class = choose_depth(
            depths.get(key, []),
            game_date,
        )

        injury_row = injury["row"] if injury else None
        depth_row = depth["row"] if depth else None

        injury_freshness[injury_class] += 1
        depth_freshness[depth_class] += 1

        if injury:
            unique_injury_players.add(key)

        if depth:
            unique_depth_players.add(key)

        report_status = field(
            injury_row, injury_spec["report_status"]
        )
        practice_status = field(
            injury_row, injury_spec["practice_status"]
        )

        flags = []

        if injury_class == "UNKNOWN":
            flags.append("NO_ELIGIBLE_INJURY_REPORT")
        elif injury_class == "HISTORICAL":
            flags.append("HISTORICAL_INJURY_ONLY")

        if depth_class == "UNKNOWN":
            flags.append("NO_ELIGIBLE_DATED_DEPTH_CHART")

        if injury_class == "CURRENT_GAME_WEEK":
            status = report_status.lower()

            if status in (
                "out", "doubtful", "questionable"
            ):
                flags.append(
                    "CURRENT_WEEK_REPORTED_"
                    + status.upper()
                )

            elif report_status:
                flags.append(
                    "CURRENT_WEEK_DESIGNATION_REVIEW"
                )

            practice_lower = practice_status.lower()

            if any(
                term in practice_lower
                for term in (
                    "did not", "dnp", "limited"
                )
            ):
                flags.append(
                    "CURRENT_WEEK_PRACTICE_CONCERN"
                )

            designations[
                report_status or "NOT_SPECIFIED"
            ] += 1

        flags.extend([
            "GAME_DAY_ACTIVE_STATUS_UNVERIFIED",
            "STARTER_STATUS_UNVERIFIED",
        ])

        for flag in flags:
            flags_count[flag] += 1

        entry.update({
            "injury_report_matched": int(bool(injury)),
            "injury_evidence_freshness": injury_class,
            "injury_report_week": (
                injury["week"] if injury else ""
            ),
            "injury_report_date": (
                injury["date"].isoformat()
                if injury and injury["date"] else ""
            ),
            "reported_game_status": report_status,
            "reported_practice_status": practice_status,
            "reported_primary_injury": field(
                injury_row,
                injury_spec["primary_injury"]
            ),
            "reported_secondary_injury": field(
                injury_row,
                injury_spec["secondary_injury"]
            ),
            "reported_practice_injury": field(
                injury_row,
                injury_spec["practice_injury"]
            ),
            "depth_chart_matched": int(bool(depth)),
            "depth_evidence_freshness": depth_class,
            "depth_chart_date": (
                depth["date"].isoformat()
                if depth and depth["date"] else ""
            ),
            "depth_chart_position": field(
                depth_row, depth_spec["position"]
            ),
            "depth_chart_position_group": field(
                depth_row,
                depth_spec["position_group"]
            ),
            "depth_chart_order_as_reported": field(
                depth_row, depth_spec["depth"]
            ),
            "depth_chart_slot_as_reported": field(
                depth_row, depth_spec["slot"]
            ),
            "availability_status": (
                "UNVERIFIED_NO_OFFICIAL_GAME_DAY_INACTIVES"
            ),
            "lineup_status": (
                "UNVERIFIED_NO_CONFIRMED_STARTERS"
            ),
            "lineup_research_flags": "|".join(flags),
        })

        results.append(entry)

    if len(results) != len(rankings):
        raise SystemExit(
            "Phase 7 row count differs from Phase 6"
        )

    output_dir.mkdir(
        parents=True, exist_ok=True
    )

    csv_path = (
        output_dir / "injury_lineup_research_2026.csv"
    )

    with csv_path.open(
        "w", newline="", encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(
            f, fieldnames=list(results[0])
        )
        writer.writeheader()
        writer.writerows(results)

    audit = {
        "phase": 7,
        "version": "schema_repair_v2",
        "season": SEASON,
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "status": "OK",
        "phase6_input_rows": len(rankings),
        "phase7_output_rows": len(results),
        "row_count_preserved": (
            len(rankings) == len(results)
        ),
        "distinct_candidate_team_player_pairs": len(
            relevant
        ),
        "source_headers": {
            "injuries": injury_headers,
            "depth_charts": depth_headers,
        },
        "detected_columns": {
            "injuries": injury_spec,
            "depth_charts": depth_spec,
        },
        "source_counts": dict(counts),
        "injury_freshness_candidate_rows": dict(
            injury_freshness
        ),
        "depth_freshness_candidate_rows": dict(
            depth_freshness
        ),
        "current_week_report_designations": dict(
            designations
        ),
        "matched_unique_injury_team_player_pairs": len(
            unique_injury_players
        ),
        "matched_unique_depth_team_player_pairs": len(
            unique_depth_players
        ),
        "research_flags": dict(flags_count),
        "limitations": [
            "Current-week means matching game week, "
            "not confirmed current-day availability.",
            "Injury source lacks a report date when "
            "the source schema provides none.",
            "Historical injury statuses must not "
            "be interpreted as current statuses.",
            "A missing report does not establish health.",
            "Depth charts are dated snapshots, "
            "not confirmed starters or snap shares.",
            "Depth-chart snapshots after the game "
            "are excluded.",
            "Exact team and GSIS ID matching only.",
            "No official inactive or starter feed "
            "is integrated in this phase.",
            "No odds, wagers or betting tickets.",
        ],
    }

    audit_path = (
        output_dir / "injury_lineup_audit_2026.json"
    )

    audit_path.write_text(
        json.dumps(audit, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    print(json.dumps({
        "status": "OK",
        "phase": 7,
        "version": "schema_repair_v2",
        "candidate_rows": len(results),
        "injury_freshness": dict(injury_freshness),
        "depth_freshness": dict(depth_freshness),
        "flags": dict(flags_count),
    }, indent=2))


if __name__ == "__main__":
    main()
