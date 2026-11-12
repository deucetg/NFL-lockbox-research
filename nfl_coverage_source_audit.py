
#!/usr/bin/env python3
"""NFL LOCKBOX Step 9E.1: Coverage source discovery.

Inspects the 2026 nflverse PBP source for explicitly charted
man/zone coverage information.

Never infers coverage from play type, formation, EPA, or result.
Does not modify the LOCKBOX database.
"""

import argparse
import csv
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path


COVERAGE_FIELDS = {
    "defense_man_zone_type",
    "defense_coverage_type",
    "coverage_type",
    "coverage",
    "coverage_scheme",
    "coverage_shell",
    "coverage_family",
    "man_zone",
    "man_zone_type",
    "coverage_name",
}

CONTEXT_FIELDS = {
    "defenders_in_box",
    "number_of_pass_rushers",
    "time_to_throw",
    "qb_hit",
    "sack",
    "shotgun",
    "no_huddle",
    "pass_attempt",
    "complete_pass",
    "air_yards",
    "epa",
    "success",
}

IDENTITY_FIELDS = {
    "game_id",
    "play_id",
    "season",
    "week",
    "posteam",
    "defteam",
}


def open_source(path):
    if path.suffix.lower() == ".gz":
        return gzip.open(
            path,
            "rt",
            encoding="utf-8-sig",
            newline="",
        )

    return path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    )


def normalize(value):
    return str(value or "").strip().lower()


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--pbp",
        default="play_by_play_2026.csv.gz",
    )

    parser.add_argument(
        "--output",
        default="NFL_LOCKBOX_2026/"
                "advanced_analytics/"
                "coverage_source_audit_2026.json",
    )

    args = parser.parse_args()

    source = Path(args.pbp)
    output = Path(args.output)

    if not source.is_file():
        parser.error(
            f"Missing PBP source: {source}"
        )

    with open_source(source) as file:
        reader = csv.DictReader(file)

        headers = reader.fieldnames or []

        if not headers:
            raise SystemExit(
                "PBP file has no readable headers"
            )

        header_lookup = {
            normalize(name): name
            for name in headers
        }

        available_coverage = sorted(
            COVERAGE_FIELDS
            & set(header_lookup)
        )

        available_context = sorted(
            CONTEXT_FIELDS
            & set(header_lookup)
        )

        available_identity = sorted(
            IDENTITY_FIELDS
            & set(header_lookup)
        )

        missing_identity = sorted(
            IDENTITY_FIELDS
            - set(header_lookup)
        )

        coverage_counts = {
            name: {
                "nonempty_rows": 0,
                "values": {},
            }
            for name in available_coverage
        }

        total_rows = 0
        season_rows = 0
        defenses = set()
        games = set()

        for row in reader:
            total_rows += 1

            if normalize(row.get("season")) != "2026":
                continue

            season_rows += 1

            defense = row.get("defteam")
            game_id = row.get("game_id")

            if defense:
                defenses.add(defense)

            if game_id:
                games.add(game_id)

            for name in available_coverage:
                actual_name = header_lookup[name]
                value = normalize(
                    row.get(actual_name)
                )

                if not value or value in (
                    "na",
                    "nan",
                    "null",
                    "none",
                ):
                    continue

                entry = coverage_counts[name]
                entry["nonempty_rows"] += 1

                values = entry["values"]
                values[value] = (
                    values.get(value, 0) + 1
                )

    usable_fields = [
        name
        for name in available_coverage
        if coverage_counts[name]["nonempty_rows"] > 0
    ]

    if missing_identity:
        status = "REVIEW"
    elif usable_fields:
        status = "COVERAGE_FIELDS_FOUND"
    else:
        status = "EXTERNAL_COVERAGE_SOURCE_REQUIRED"

    audit = {
        "version": "step9e1_coverage_source_audit_v1",
        "created_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "source": str(source),
        "season": 2026,
        "status": status,
        "total_source_rows": total_rows,
        "season_rows": season_rows,
        "teams_with_defensive_plays": len(defenses),
        "games": len(games),
        "available_identity_fields": available_identity,
        "missing_identity_fields": missing_identity,
        "available_coverage_fields": available_coverage,
        "coverage_field_observations": coverage_counts,
        "available_context_fields": available_context,
        "limitations": [
            "Only explicitly named coverage fields are detected",
            "Context fields do not establish man or zone",
            "Nonempty coverage fields still require validation",
            "Coverage values are not normalized or inferred",
            "No man/zone percentages are calculated",
            "No player matchup statistics are invented",
            "External charted coverage may require licensing",
        ],
        "next_step": (
            "Validate field definitions and play-level "
            "coverage labels before calculating man/zone "
            "rates or coverage-specific player production"
        ),
    }

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output.write_text(
        json.dumps(
            audit,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "status": status,
                "season_rows": season_rows,
                "defenses": len(defenses),
                "games": len(games),
                "coverage_fields": usable_fields,
                "output": str(output),
            },
            indent=2,
        )
    )

    if missing_identity:
        raise SystemExit(
            "Required source identity fields missing"
        )


if __name__ == "__main__":
    main()
