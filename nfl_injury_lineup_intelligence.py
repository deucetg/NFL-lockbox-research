
#!/usr/bin/env python3
"""NFL LOCKBOX Phase 7.1 — Injury and Offensive Lineup Intelligence.

Research-only pipeline. Uses exact team + GSIS ID joins.
Depth charts do not confirm starters, snaps, or active status.
Injury reports do not confirm game-day availability.
"""

import argparse
import csv
import gzip
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

SEASON = 2026
STALE_DAYS = 14

OFFENSE = {"QB", "RB", "WR", "TE", "FB", "HB"}
SPECIAL = {"KR", "PR", "K", "P", "LS", "RS"}

POSITION_ALIASES = {
    "HB": "RB",
    "FB": "RB",
    "HALFBACK": "RB",
    "FULLBACK": "RB",
    "RUNNING BACK": "RB",
    "WIDE RECEIVER": "WR",
    "QUARTERBACK": "QB",
    "TIGHT END": "TE",
}

EXPECTED_BY_KIND = {
    "receiving": {"WR", "TE", "RB"},
    "rushing": {"RB", "QB", "WR"},
}


def read_csv(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f)


def headers(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as f:
        return csv.DictReader(f).fieldnames or []


def detect(columns, *names):
    lookup = {c.lower(): c for c in columns}
    for name in names:
        if name.lower() in lookup:
            return lookup[name.lower()]
    return None


def val(row, col):
    if row is None or col is None:
        return ""
    return str(row.get(col, "") or "").strip()


def integer(raw):
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return None


def date_value(raw):
    text = str(raw or "").strip()
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


def normalized_position(raw):
    position = str(raw or "").strip().upper()
    return POSITION_ALIASES.get(position, position)


def source_schema(columns, kind):
    spec = {
        "id": detect(columns, "gsis_id", "player_gsis_id"),
        "team": detect(columns, "team", "club_code"),
        "week": detect(columns, "week", "game_week"),
        "season": detect(columns, "season", "season_year"),
        "date": detect(
            columns, "dt", "report_date", "date", "game_date"
        ),
    }

    if kind == "injuries":
        spec.update({
            "report_status": detect(
                columns, "report_status", "game_status"
            ),
            "practice_status": detect(
                columns, "practice_status",
                "practice_participation"
            ),
            "primary_injury": detect(
                columns, "report_primary_injury",
                "primary_injury"
            ),
            "secondary_injury": detect(
                columns, "report_secondary_injury",
                "secondary_injury"
            ),
            "practice_injury": detect(
                columns, "practice_primary_injury"
            ),
        })
    else:
        spec.update({
            "position": detect(
                columns, "pos_abb", "position", "pos"
            ),
            "group": detect(
                columns, "pos_grp", "position_group"
            ),
            "rank": detect(
                columns, "pos_rank", "depth_rank",
                "depth_team"
            ),
            "slot": detect(
                columns, "pos_slot", "depth_slot"
            ),
        })

    return spec


def load_records(path, spec, relevant, label, counts):
    result = defaultdict(list)

    for row in read_csv(path):
        counts[label + "_source_rows"] += 1

        season = integer(val(row, spec["season"]))
        if season is not None and season != SEASON:
            continue

        key = (
            val(row, spec["team"]).upper(),
            val(row, spec["id"]),
        )

        if key not in relevant:
            continue

        result[key].append({
            "row": row,
            "week": integer(val(row, spec["week"])),
            "date": date_value(val(row, spec["date"])),
        })

        counts[label + "_matched_source_rows"] += 1

    return result


def injury_selection(records, game_week, game_date):
    eligible = []

    for item in records:
        week = item["week"]
        observed = item["date"]

        if (
            game_week is not None
            and week is not None
            and week > game_week
        ):
            continue

        if (
            game_date is not None
            and observed is not None
            and observed > game_date
        ):
            continue

        eligible.append(item)

    current = [
        x for x in eligible
        if game_week is not None and x["week"] == game_week
    ]

    if current:
        current.sort(
            key=lambda x: x["date"] or date.min,
            reverse=True,
        )
        return current[0], "CURRENT_GAME_WEEK"

    historical = [
        x for x in eligible
        if (
            game_week is not None
            and x["week"] is not None
            and x["week"] < game_week
        )
    ]

    if historical:
        historical.sort(
            key=lambda x: (
                x["week"], x["date"] or date.min
            ),
            reverse=True,
        )
        return historical[0], "HISTORICAL"

    return None, "UNKNOWN"


def depth_selection(records, game_date, spec, player_position):
    eligible = [
        x for x in records
        if (
            game_date is not None
            and x["date"] is not None
            and x["date"] <= game_date
        )
    ]

    if not eligible:
        return None, "UNKNOWN", None

    # A newer snapshot supersedes older snapshots.
    latest_date = max(x["date"] for x in eligible)
    latest = [
        x for x in eligible if x["date"] == latest_date
    ]

    offensive = []

    for item in latest:
        row = item["row"]
        pos = normalized_position(val(row, spec["position"]))
        group = normalized_position(val(row, spec["group"]))

        if pos in OFFENSE or group in OFFENSE:
            offensive.append(item)

    def priority(item):
        row = item["row"]
        pos = normalized_position(val(row, spec["position"]))
        group = normalized_position(val(row, spec["group"]))
        rank = integer(val(row, spec["rank"]))

        return (
            0 if pos == player_position else 1,
            0 if pos in OFFENSE else 1,
            0 if group == player_position else 1,
            rank if rank is not None else 999,
            pos,
            val(row, spec["slot"]),
        )

    if offensive:
        offensive.sort(key=priority)
        chosen = offensive[0]
        category = "OFFENSIVE_POSITION"
    else:
        latest.sort(key=priority)
        chosen = latest[0]
        category = "NO_OFFENSIVE_POSITION"

    age = (game_date - latest_date).days
    return chosen, category, age


def candidate_identity(row):
    return (
        row["game_id"],
        row["offense"],
        row["player_id"],
        row["kind"],
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", default="NFL_LOCKBOX_2026"
    )
    args = parser.parse_args()

    root = Path(args.root)
    source = root / "source_data"
    outdir = root / "advanced_analytics"

    ranking_file = (
        outdir / "upcoming_research_rankings_2026.csv"
    )
    injury_file = source / "injuries_2026.csv.gz"
    depth_file = source / "depth_charts_2026.csv.gz"

    for path in (ranking_file, injury_file, depth_file):
        if not path.is_file():
            raise SystemExit(f"Missing input file: {path}")

    rankings = list(read_csv(ranking_file))

    if not rankings:
        raise SystemExit("Phase 6 rankings are empty")

    required = {
        "game_id", "game_date", "week", "offense",
        "player_id", "player_name", "position", "kind",
        "research_priority_index",
    }

    missing = required - set(rankings[0])
    if missing:
        raise SystemExit(
            f"Missing Phase 6 columns: {sorted(missing)}"
        )

    source_ids = [
        candidate_identity(row) for row in rankings
    ]

    if len(source_ids) != len(set(source_ids)):
        raise SystemExit(
            "Duplicate game/team/player/kind identities "
            "in Phase 6 rankings"
        )

    injury_columns = headers(injury_file)
    depth_columns = headers(depth_file)

    ispec = source_schema(injury_columns, "injuries")
    dspec = source_schema(depth_columns, "depth_charts")

    for label, spec in (
        ("injuries", ispec),
        ("depth_charts", dspec),
    ):
        if not spec["id"] or not spec["team"]:
            raise SystemExit(
                f"{label} missing exact ID/team join fields"
            )

    if not ispec["week"]:
        raise SystemExit("Injury report week missing")

    if not dspec["date"] or not dspec["position"]:
        raise SystemExit(
            "Depth-chart date or position column missing"
        )

    relevant = {
        (
            row["offense"].strip().upper(),
            row["player_id"].strip(),
        )
        for row in rankings
    }

    counts = Counter()

    injuries = load_records(
        injury_file, ispec, relevant, "injuries", counts
    )
    depths = load_records(
        depth_file, dspec, relevant, "depth_charts", counts
    )

    output = []
    flags_count = Counter()
    injury_freshness = Counter()
    depth_types = Counter()
    depth_age_bands = Counter()
    designations = Counter()
    unique_injury = set()
    unique_depth = set()

    for row in rankings:
        entry = dict(row)

        key = (
            row["offense"].strip().upper(),
            row["player_id"].strip(),
        )

        game_date = date_value(row["game_date"])
        game_week = integer(row["week"])

        if game_date is None or game_week is None:
            raise SystemExit(
                f"Invalid game date/week: {row['game_id']}"
            )

        player_position = normalized_position(
            row["position"]
        )

        injury, injury_type = injury_selection(
            injuries.get(key, []), game_week, game_date
        )

        depth, depth_type, age = depth_selection(
            depths.get(key, []),
            game_date,
            dspec,
            player_position,
        )

        ir = injury["row"] if injury else None
        dr = depth["row"] if depth else None

        injury_freshness[injury_type] += 1
        depth_types[depth_type] += 1

        if injury:
            unique_injury.add(key)

        if depth:
            unique_depth.add(key)

        flags = []

        if injury_type == "UNKNOWN":
            flags.append("NO_ELIGIBLE_INJURY_REPORT")
        elif injury_type == "HISTORICAL":
            flags.append("HISTORICAL_INJURY_ONLY")

        status = val(ir, ispec["report_status"])
        practice = val(ir, ispec["practice_status"])

        if injury_type == "CURRENT_GAME_WEEK":
            normalized_status = status.lower()

            if normalized_status in (
                "out", "doubtful", "questionable"
            ):
                flags.append(
                    "CURRENT_WEEK_REPORTED_"
                    + normalized_status.upper()
                )
            elif status:
                flags.append(
                    "CURRENT_WEEK_DESIGNATION_REVIEW"
                )

            if any(
                x in practice.lower()
                for x in ("did not", "dnp", "limited")
            ):
                flags.append(
                    "CURRENT_WEEK_PRACTICE_CONCERN"
                )

            designations[
                status or "NOT_SPECIFIED"
            ] += 1

        if depth is None:
            flags.append("NO_ELIGIBLE_DATED_DEPTH_CHART")
            depth_age_bands["UNKNOWN"] += 1
        else:
            if depth_type == "NO_OFFENSIVE_POSITION":
                flags.append(
                    "NO_OFFENSIVE_DEPTH_POSITION"
                )

            if age > STALE_DAYS:
                flags.append(
                    "STALE_DEPTH_SNAPSHOT_GT_14_DAYS"
                )
                depth_age_bands["OVER_14_DAYS"] += 1
            elif age <= 7:
                depth_age_bands["0_TO_7_DAYS"] += 1
            else:
                depth_age_bands["8_TO_14_DAYS"] += 1

        depth_position = normalized_position(
            val(dr, dspec["position"])
        )

        if (
            depth_type == "OFFENSIVE_POSITION"
            and player_position in OFFENSE
            and depth_position in OFFENSE
            and depth_position != player_position
        ):
            flags.append(
                "DEPTH_POSITION_DIFFERS_FROM_RANKING"
            )

        flags.extend([
            "GAME_DAY_ACTIVE_STATUS_UNVERIFIED",
            "STARTER_STATUS_UNVERIFIED",
        ])

        for flag in flags:
            flags_count[flag] += 1

        entry.update({
            "injury_report_matched": int(bool(injury)),
            "injury_evidence_freshness": injury_type,
            "injury_report_week": (
                injury["week"] if injury else ""
            ),
            "injury_report_date": (
                injury["date"].isoformat()
                if injury and injury["date"] else ""
            ),
            "reported_game_status": status,
            "reported_practice_status": practice,
            "reported_primary_injury": val(
                ir, ispec["primary_injury"]
            ),
            "reported_secondary_injury": val(
                ir, ispec["secondary_injury"]
            ),
            "reported_practice_injury": val(
                ir, ispec["practice_injury"]
            ),
            "depth_chart_matched": int(bool(depth)),
            "depth_evidence_freshness": (
                "DATED_SNAPSHOT" if depth else "UNKNOWN"
            ),
            "depth_chart_selection_type": depth_type,
            "depth_chart_date": (
                depth["date"].isoformat()
                if depth else ""
            ),
            "depth_chart_age_days": (
                age if age is not None else ""
            ),
            "depth_chart_stale": (
                int(age > STALE_DAYS)
                if age is not None else ""
            ),
            "depth_chart_position": val(
                dr, dspec["position"]
            ),
            "depth_chart_position_group": val(
                dr, dspec["group"]
            ),
            "depth_chart_order_as_reported": val(
                dr, dspec["rank"]
            ),
            "depth_chart_slot_as_reported": val(
                dr, dspec["slot"]
            ),
            "availability_status": (
                "UNVERIFIED_NO_OFFICIAL_GAME_DAY_INACTIVES"
            ),
            "lineup_status": (
                "UNVERIFIED_NO_CONFIRMED_STARTERS"
            ),
            "lineup_research_flags": "|".join(flags),
        })

        output.append(entry)

    # Fail the build if row identity or ordering changes.
    output_ids = [
        candidate_identity(row) for row in output
    ]

    if output_ids != source_ids:
        raise SystemExit(
            "Phase 7.1 changed Phase 6 candidate identities"
        )

    if sum(injury_freshness.values()) != len(output):
        raise SystemExit("Injury freshness count mismatch")

    if sum(depth_types.values()) != len(output):
        raise SystemExit("Depth selection count mismatch")

    if sum(depth_age_bands.values()) != len(output):
        raise SystemExit("Depth age count mismatch")

    for row in output:
        if (
            row["depth_chart_selection_type"]
            == "OFFENSIVE_POSITION"
            and normalized_position(
                row["depth_chart_position"]
            ) not in OFFENSE
            and normalized_position(
                row["depth_chart_position_group"]
            ) not in OFFENSE
        ):
            raise SystemExit(
                "Non-offensive entry incorrectly classified"
            )

    outdir.mkdir(parents=True, exist_ok=True)

    output_file = (
        outdir / "injury_lineup_research_2026.csv"
    )

    with output_file.open(
        "w", newline="", encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(
            f, fieldnames=list(output[0])
        )
        writer.writeheader()
        writer.writerows(output)

    audit = {
        "phase": 7,
        "subphase": "7.1",
        "version": "offensive_depth_v3",
        "season": SEASON,
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "status": "OK",
        "phase6_input_rows": len(rankings),
        "phase7_output_rows": len(output),
        "row_count_preserved": True,
        "candidate_identity_preserved": True,
        "distinct_candidate_team_player_pairs": len(
            relevant
        ),
        "source_headers": {
            "injuries": injury_columns,
            "depth_charts": depth_columns,
        },
        "detected_columns": {
            "injuries": ispec,
            "depth_charts": dspec,
        },
        "source_counts": dict(counts),
        "injury_freshness_candidate_rows": dict(
            injury_freshness
        ),
        "depth_selection_candidate_rows": dict(
            depth_types
        ),
        "depth_age_candidate_rows": dict(
            depth_age_bands
        ),
        "current_week_report_designations": dict(
            designations
        ),
        "matched_unique_injury_team_player_pairs": len(
            unique_injury
        ),
        "matched_unique_depth_team_player_pairs": len(
            unique_depth
        ),
        "research_flags": dict(flags_count),
        "validation": {
            "row_count": "PASS",
            "candidate_identity": "PASS",
            "injury_count_totals": "PASS",
            "depth_selection_totals": "PASS",
            "depth_age_totals": "PASS",
            "offensive_position_classification": "PASS",
        },
        "limitations": [
            "Depth charts are snapshots, not confirmed "
            "starters, routes, snaps, or active status.",
            "Offensive position is prioritized within "
            "the latest eligible dated snapshot.",
            "Special-teams-only depth matches are "
            "flagged, not used as offensive evidence.",
            "Snapshots older than 14 days are flagged.",
            "Current-week injury evidence is not "
            "confirmed game-day availability.",
            "The injury source may lack report dates.",
            "Historical injury statuses are not "
            "treated as current statuses.",
            "Missing reports do not prove health.",
            "Exact GSIS ID and team joins only.",
            "No betting odds, wagers, or tickets.",
        ],
    }

    audit_file = (
        outdir / "injury_lineup_audit_2026.json"
    )

    audit_file.write_text(
        json.dumps(audit, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps({
        "status": "OK",
        "version": "offensive_depth_v3",
        "candidate_rows": len(output),
        "injury_freshness": dict(injury_freshness),
        "depth_selection": dict(depth_types),
        "depth_age": dict(depth_age_bands),
        "flags": dict(flags_count),
        "validation": audit["validation"],
    }, indent=2))


if __name__ == "__main__":
    main()
