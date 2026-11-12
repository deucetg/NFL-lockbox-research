
#!/usr/bin/env python3
"""NFL LOCKBOX Step 9D.1: source-verified defensive EPA and success rate.

Read-only join of nflverse PBP EPA to existing LOCKBOX play IDs.
Does not modify SQLite or assert that charted man/zone coverage
is available.
"""

import argparse
import csv
import gzip
import hashlib
import json
import math
import sqlite3

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


WINDOWS = {
    "SEASON": None,
    "L3": 3,
    "L5": 5,
    "L8": 8,
}

FIELDS = [
    "defense",
    "window",
    "category",
    "games_in_sample",
    "last_game_date",
    "eligible_plays",
    "epa_observed_plays",
    "epa_coverage_pct",
    "epa_total_allowed",
    "epa_per_play_allowed",
    "epa_rank",
    "success_observed_plays",
    "offensive_success_rate_allowed",
    "success_rate_rank",
    "data_status",
    "source",
    "methodology",
]


def finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def flag(value):
    if value is None:
        return None

    if str(value).strip().lower() in (
        "",
        "na",
        "nan",
        "null",
    ):
        return None

    number = finite(value)
    return int(number) if number in (0.0, 1.0) else None


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--pbp",
        default="play_by_play_2026.csv.gz",
    )

    parser.add_argument(
        "--database",
        default="NFL_LOCKBOX_2026/nfl_lockbox_2026.sqlite",
    )

    parser.add_argument(
        "--output",
        default="NFL_LOCKBOX_2026/advanced_analytics",
    )

    parser.add_argument(
        "--min-coverage",
        type=float,
        default=0.95,
    )

    args = parser.parse_args()

    source = Path(args.pbp)
    database_file = Path(args.database)
    output = Path(args.output)

    if not source.is_file() or not database_file.is_file():
        parser.error(
            "Both the refreshed nflverse PBP and "
            "LOCKBOX SQLite must exist"
        )

    if not 0 < args.min_coverage <= 1:
        parser.error(
            "--min-coverage must be within (0,1]"
        )

    output.mkdir(parents=True, exist_ok=True)

    db = sqlite3.connect(
        f"file:{database_file.resolve()}?mode=ro",
        uri=True,
    )

    db.row_factory = sqlite3.Row

    known = {}
    game_meta = {}

    query = """
        SELECT
            game_id,
            play_id,
            game_date,
            week,
            defteam,
            posteam,
            season_type,
            qtr,
            pass_attempt,
            rush_attempt
        FROM plays
        WHERE season = 2026
          AND season_type = 'REG'
          AND qtr BETWEEN 1 AND 4
    """

    for row in db.execute(query):
        game_id = str(row["game_id"])
        play_id = str(row["play_id"])

        defense = row["defteam"]
        offense = row["posteam"]

        if (
            not game_id
            or not play_id
            or not defense
            or not offense
            or defense == offense
        ):
            continue

        key = (game_id, play_id)

        if key in known:
            raise SystemExit(
                f"Duplicate LOCKBOX play key: {key}"
            )

        pass_attempt = row["pass_attempt"] == 1
        rush_attempt = row["rush_attempt"] == 1

        if pass_attempt and not rush_attempt:
            category = "pass"
        elif rush_attempt and not pass_attempt:
            category = "rush"
        else:
            category = None

        known[key] = (
            defense,
            offense,
            category,
        )

        game_key = (defense, game_id)

        metadata = (
            str(row["game_date"] or ""),
            int(row["week"] or 0),
        )

        if (
            game_key in game_meta
            and game_meta[game_key] != metadata
        ):
            raise SystemExit(
                f"Conflicting game metadata: {game_key}"
            )

        game_meta[game_key] = metadata

    if not known:
        raise SystemExit(
            "No 2026 regular-season LOCKBOX plays"
        )

    data = defaultdict(
        lambda: {
            "eligible": 0,
            "epa_count": 0,
            "epa_sum": 0.0,
            "success_count": 0,
            "success_sum": 0,
        }
    )

    quality = defaultdict(int)

    matched = set()
    raw_seen = set()

    opener = (
        gzip.open
        if source.suffix == ".gz"
        else open
    )

    with opener(
        source,
        "rt",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        reader = csv.DictReader(file)

        required = {
            "game_id",
            "play_id",
            "season",
            "defteam",
            "posteam",
            "epa",
            "success",
            "pass_attempt",
            "rush_attempt",
        }

        missing = required - set(
            reader.fieldnames or []
        )

        if missing:
            raise SystemExit(
                "Source missing required columns: "
                + ", ".join(sorted(missing))
            )

        for row in reader:

            if str(row.get("season")) != "2026":
                continue

            key = (
                str(row.get("game_id") or ""),
                str(row.get("play_id") or ""),
            )

            if key in raw_seen:
                quality["duplicate_source_keys"] += 1
                continue

            raw_seen.add(key)

            if key not in known:
                continue

            defense, offense, category = known[key]

            if (
                row.get("defteam") != defense
                or row.get("posteam") != offense
            ):
                quality["team_mismatch"] += 1
                continue

            matched.add(key)

            if category is None:
                continue

            if (
                flag(row.get("pass_attempt"))
                != int(category == "pass")
                or flag(row.get("rush_attempt"))
                != int(category == "rush")
            ):
                quality["attempt_type_mismatch"] += 1
                continue

            epa = finite(row.get("epa"))
            success = flag(row.get("success"))

            record = data[
                (defense, key[0], category)
            ]

            record["eligible"] += 1

            if epa is not None:
                record["epa_count"] += 1
                record["epa_sum"] += epa

            if success is not None:
                record["success_count"] += 1
                record["success_sum"] += success

    # Corrected: subtract matched keys from known keys,
    # rather than attempting dictionary-set subtraction.
    quality["lockbox_plays_unmatched"] = len(
        known.keys() - matched
    )

    game_lists = defaultdict(list)

    for (defense, game_id), (
        date,
        week,
    ) in game_meta.items():

        game_lists[defense].append(
            (date, week, game_id)
        )

    for games in game_lists.values():
        games.sort()

    rows = []

    for defense, games in sorted(
        game_lists.items()
    ):

        for window, count in WINDOWS.items():

            selected = (
                games
                if count is None
                else games[-count:]
            )

            for category in (
                "pass",
                "rush",
                "combined",
            ):

                sums = {
                    key: 0
                    for key in (
                        "eligible",
                        "epa_count",
                        "epa_sum",
                        "success_count",
                        "success_sum",
                    )
                }

                for _, _, game_id in selected:

                    parts = (
                        ("pass", "rush")
                        if category == "combined"
                        else (category,)
                    )

                    for part in parts:

                        record = data[
                            (defense, game_id, part)
                        ]

                        for key in sums:
                            sums[key] += record[key]

                eligible = sums["eligible"]
                epa_count = sums["epa_count"]
                success_count = sums["success_count"]

                epa_coverage = (
                    epa_count / eligible
                    if eligible
                    else 0
                )

                success_coverage = (
                    success_count / eligible
                    if eligible
                    else 0
                )

                valid_epa = (
                    eligible > 0
                    and epa_coverage
                    >= args.min_coverage
                )

                valid_success = (
                    eligible > 0
                    and success_coverage
                    >= args.min_coverage
                )

                rows.append(
                    {
                        "defense": defense,
                        "window": window,
                        "category": category,
                        "games_in_sample": len(
                            selected
                        ),
                        "last_game_date": selected[-1][0],
                        "eligible_plays": eligible,
                        "epa_observed_plays": epa_count,
                        "epa_coverage_pct": round(
                            100 * epa_coverage,
                            2,
                        ),
                        "epa_total_allowed": (
                            round(
                                sums["epa_sum"],
                                4,
                            )
                            if valid_epa
                            else None
                        ),
                        "epa_per_play_allowed": (
                            round(
                                sums["epa_sum"]
                                / epa_count,
                                5,
                            )
                            if valid_epa
                            else None
                        ),
                        "epa_rank": None,
                        "success_observed_plays": (
                            success_count
                        ),
                        "offensive_success_rate_allowed": (
                            round(
                                sums["success_sum"]
                                / success_count,
                                5,
                            )
                            if valid_success
                            else None
                        ),
                        "success_rate_rank": None,
                        "data_status": (
                            "COMPLETE"
                            if valid_epa and valid_success
                            else "INSUFFICIENT_COVERAGE"
                        ),
                        "source": (
                            "nflverse_pbp_2026_joined_to_"
                            "LOCKBOX_play_ids"
                        ),
                        "methodology": (
                            "Lower offensive EPA and "
                            "offensive success rate allowed "
                            "is better; pass_attempt/"
                            "rush_attempt plays only; "
                            "regulation"
                        ),
                    }
                )

    for window in WINDOWS:

        for category in (
            "pass",
            "rush",
            "combined",
        ):

            subset = [
                row
                for row in rows
                if (
                    row["window"] == window
                    and row["category"] == category
                )
            ]

            ranking_metrics = (
                (
                    "epa_per_play_allowed",
                    "epa_rank",
                ),
                (
                    "offensive_success_rate_allowed",
                    "success_rate_rank",
                ),
            )

            for metric, rank_field in ranking_metrics:

                ordered = sorted(
                    (
                        row
                        for row in subset
                        if row[metric] is not None
                    ),
                    key=lambda row: (
                        row[metric],
                        row["defense"],
                    ),
                )

                previous = None
                position = 0

                for row in ordered:

                    if (
                        previous is None
                        or row[metric] != previous
                    ):
                        position += 1

                    row[rank_field] = position
                    previous = row[metric]

    destination = (
        output / "defensive_epa_2026.csv"
    )

    with destination.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=FIELDS,
        )

        writer.writeheader()
        writer.writerows(rows)

    sha = hashlib.sha256()

    with source.open("rb") as file:

        for chunk in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            sha.update(chunk)

    audit = {
        "version": "step9d_defensive_epa_v1",
        "created_utc": (
            datetime.now(timezone.utc).isoformat()
        ),
        "source_file": str(source),
        "source_sha256": sha.hexdigest(),
        "database": str(database_file),
        "teams": len(game_lists),
        "team_games": len(game_meta),
        "rows": len(rows),
        "min_coverage": args.min_coverage,
        "quality": dict(quality),
        "incomplete_rows": sum(
            row["data_status"] != "COMPLETE"
            for row in rows
        ),
        "status": (
            "PASS"
            if (
                len(game_lists) == 32
                and not quality["duplicate_source_keys"]
                and not quality["team_mismatch"]
                and not quality["attempt_type_mismatch"]
            )
            else "REVIEW"
        ),
        "limitations": [
            "Not opponent-adjusted",
            (
                "EPA is nflverse offensive EPA; "
                "defensive value is EPA allowed"
            ),
            (
                "Success is nflverse offensive "
                "success indicator"
            ),
            (
                "Sacks are excluded unless source "
                "marks them as pass_attempt"
            ),
            "No man/zone charting",
            (
                "Coverage threshold suppresses "
                "incomplete metrics"
            ),
            (
                "Only PBP plays matching existing "
                "LOCKBOX game_id and play_id are used"
            ),
        ],
    }

    audit_path = (
        output / "defensive_epa_audit_2026.json"
    )

    audit_path.write_text(
        json.dumps(audit, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "status": audit["status"],
                "teams": audit["teams"],
                "rows": len(rows),
                "incomplete_rows": (
                    audit["incomplete_rows"]
                ),
                "quality": dict(quality),
            },
            indent=2,
        )
    )

    db.close()

    if audit["status"] != "PASS":
        raise SystemExit(
            "Defensive EPA source reconciliation "
            "needs review"
        )


if __name__ == "__main__":
    main()
