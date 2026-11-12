
#!/usr/bin/env python3
"""NFL LOCKBOX Phase 7: conservative injury/depth-chart evidence integration.

No player is called ACTIVE, INACTIVE or STARTING from a depth chart or an
injury report alone. Research-only; does not modify Phase 6 rankings.
"""
import argparse
import csv
import gzip
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def read_csv(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        yield from reader


def header(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", newline="", encoding="utf-8-sig") as f:
        return csv.DictReader(f).fieldnames or []


def pick(columns, *choices):
    lookup = {c.lower(): c for c in columns}
    return next(
        (lookup[x.lower()] for x in choices if x.lower() in lookup),
        None,
    )


def value(row, col):
    return str(row.get(col, "") or "").strip() if col else ""


def week_number(raw):
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="NFL_LOCKBOX_2026")
    args = parser.parse_args()

    root = Path(args.root)
    source = root / "source_data"
    adv = root / "advanced_analytics"

    ranking_file = adv / "upcoming_research_rankings_2026.csv"
    injury_file = source / "injuries_2026.csv.gz"
    depth_file = source / "depth_charts_2026.csv.gz"

    required = [ranking_file, injury_file, depth_file]
    missing = [str(p) for p in required if not p.is_file()]

    if missing:
        raise SystemExit(
            "Missing required files: " + ", ".join(missing)
        )

    rankings = list(read_csv(ranking_file))

    if not rankings:
        raise SystemExit("Empty Phase 6 rankings")

    required_rank = {
        "game_id",
        "game_date",
        "week",
        "offense",
        "player_id",
        "player_name",
        "kind",
        "research_priority_index",
    }

    if required_rank - set(rankings[0]):
        raise SystemExit(
            "Phase 6 ranking schema mismatch: "
            + str(sorted(required_rank - set(rankings[0])))
        )

    icols = header(injury_file)
    dcols = header(depth_file)

    specs = {}

    for label, cols in [
        ("injuries", icols),
        ("depth_charts", dcols),
    ]:
        specs[label] = {
            "id": pick(
                cols,
                "gsis_id",
                "player_id",
                "gsis",
                "player_gsis_id",
            ),
            "team": pick(
                cols,
                "team",
                "club_code",
                "recent_team",
                "team_abbr",
            ),
            "week": pick(cols, "week", "game_week"),
            "season": pick(cols, "season", "season_year"),
            "date": pick(
                cols,
                "date",
                "report_date",
                "game_date",
            ),
            "name": pick(
                cols,
                "full_name",
                "player_name",
                "player",
                "name",
            ),
        }

    specs["injuries"].update({
        "report_status": pick(
            icols,
            "report_status",
            "game_status",
            "injury_status",
            "status",
        ),
        "practice_status": pick(
            icols,
            "practice_status",
            "practice_participation",
            "participation",
        ),
        "primary_injury": pick(
            icols,
            "primary_injury",
            "injury",
            "injury_type",
        ),
    })

    specs["depth_charts"].update({
        "position": pick(
            dcols,
            "position",
            "position_group",
            "pos",
        ),
        "depth": pick(
            dcols,
            "depth_team",
            "depth_chart_order",
            "depth",
            "rank",
            "depth_rank",
        ),
    })

    # Never join by name alone.
    for label in specs:
        if not specs[label]["id"] or not specs[label]["team"]:
            raise SystemExit(
                f"{label} lacks a reliable ID/team join: "
                f"{specs[label]}"
            )

    relevant = {
        (r["offense"].upper(), r["player_id"])
        for r in rankings
    }

    latest = {
        "injuries": {},
        "depth_charts": {},
    }

    counts = Counter()

    for label, path in [
        ("injuries", injury_file),
        ("depth_charts", depth_file),
    ]:
        s = specs[label]

        for row in read_csv(path):
            counts[label + "_source_rows"] += 1

            if s["season"] and value(
                row, s["season"]
            ) not in ("", "2026"):
                continue

            team = value(row, s["team"]).upper()
            pid = value(row, s["id"])
            key = (team, pid)

            if key not in relevant:
                continue

            week = week_number(value(row, s["week"]))
            date = value(row, s["date"])

            order = (
                week if week is not None else -1,
                date,
            )

            latest[label].setdefault(
                key, []
            ).append((order, row))

            counts[label + "_matched_rows"] += 1

    for label in latest:
        for key, records in latest[label].items():
            records.sort(
                key=lambda pair: pair[0],
                reverse=True,
            )

    out = []

    for r in rankings:
        key = (
            r["offense"].upper(),
            r["player_id"],
        )

        target_week = week_number(r["week"])
        game_date = r["game_date"]
        evidence = {}

        for label in ("injuries", "depth_charts"):
            s = specs[label]
            chosen = None

            for (_, date), item in latest[label].get(
                key, []
            ):
                w = week_number(
                    value(item, s["week"])
                )

                # Do not use future-week reports.
                if (
                    target_week is not None
                    and w is not None
                    and w > target_week
                ):
                    continue

                # Do not use reports dated after kickoff.
                if (
                    s["date"]
                    and date
                    and len(date) >= 10
                    and date[:10] > game_date
                ):
                    continue

                chosen = item
                break

            evidence[label] = chosen

        injury = evidence["injuries"]
        depth = evidence["depth_charts"]

        i = specs["injuries"]
        d = specs["depth_charts"]

        report = (
            value(injury, i["report_status"])
            if injury else ""
        )

        practice = (
            value(injury, i["practice_status"])
            if injury else ""
        )

        injury_week = (
            value(injury, i["week"])
            if injury else ""
        )

        depth_week = (
            value(depth, d["week"])
            if depth else ""
        )

        flags = []

        if not injury:
            flags.append("NO_MATCHED_INJURY_REPORT")
        elif (
            target_week is not None
            and week_number(injury_week) != target_week
        ):
            flags.append(
                "INJURY_REPORT_NOT_CURRENT_GAME_WEEK"
            )

        if not depth:
            flags.append("NO_MATCHED_DEPTH_CHART")
        elif (
            target_week is not None
            and week_number(depth_week) != target_week
        ):
            flags.append(
                "DEPTH_CHART_NOT_CURRENT_GAME_WEEK"
            )

        normalized = report.lower().strip()

        if normalized in (
            "out",
            "doubtful",
            "questionable",
        ):
            flags.append(
                "REPORTED_" + normalized.upper()
            )
        elif normalized:
            flags.append(
                "REPORTED_DESIGNATION_REVIEW"
            )

        if practice and any(
            x in practice.lower()
            for x in (
                "did not",
                "dnp",
                "limited",
            )
        ):
            flags.append(
                "PRACTICE_PARTICIPATION_CONCERN"
            )

        flags.append(
            "GAME_DAY_ACTIVE_STATUS_UNVERIFIED"
        )
        flags.append(
            "STARTER_STATUS_UNVERIFIED"
        )

        entry = dict(r)

        entry.update({
            "injury_report_matched": int(bool(injury)),
            "injury_report_week": injury_week,
            "injury_report_date": (
                value(injury, i["date"])
                if injury else ""
            ),
            "reported_game_status": report,
            "reported_practice_status": practice,
            "reported_primary_injury": (
                value(injury, i["primary_injury"])
                if injury else ""
            ),
            "depth_chart_matched": int(bool(depth)),
            "depth_chart_week": depth_week,
            "depth_chart_position": (
                value(depth, d["position"])
                if depth else ""
            ),
            "depth_chart_order_as_reported": (
                value(depth, d["depth"])
                if depth else ""
            ),
            "availability_status": (
                "UNVERIFIED_NO_OFFICIAL_GAME_DAY_INACTIVES"
            ),
            "lineup_status": (
                "UNVERIFIED_NO_CONFIRMED_STARTERS"
            ),
            "lineup_research_flags": "|".join(flags),
        })

        out.append(entry)

    adv.mkdir(parents=True, exist_ok=True)

    outfile = adv / "injury_lineup_research_2026.csv"

    with outfile.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=list(out[0]),
        )
        writer.writeheader()
        writer.writerows(out)

    audit = {
        "phase": 7,
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "source_headers": {
            "injuries": icols,
            "depth_charts": dcols,
        },
        "detected_columns": specs,
        "source_and_match_counts": dict(counts),
        "rankings_rows": len(rankings),
        "output_rows": len(out),
        "distinct_players": len(relevant),
        "flags": dict(
            Counter(
                flag
                for r in out
                for flag in r[
                    "lineup_research_flags"
                ].split("|")
            )
        ),
        "limitations": [
            "Historical injury reports and depth charts "
            "are evidence, not official game-day inactives",
            "Missing injury report is not evidence "
            "of health or availability",
            "Depth-chart presence and ordering do "
            "not confirm a starting role",
            "Joins require exact team and GSIS ID; "
            "unmatched records remain unknown",
            "Report freshness is flagged; "
            "future-week reports are excluded",
            "No odds, betting advice, or "
            "estimated injury impacts",
        ],
        "status": "OK",
    }

    audit_file = adv / "injury_lineup_audit_2026.json"

    audit_file.write_text(
        json.dumps(audit, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "status": "OK",
                "rows": len(out),
                "flags": audit["flags"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
