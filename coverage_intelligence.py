
#!/usr/bin/env python3
"""NFL LOCKBOX Defensive Coverage Intelligence — V1.

Research only. No odds, wagers, or betting tickets.
Uses sourced CSV inputs; never estimates missing coverage data.
"""

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

INPUT = Path("coverage_data/defensive_coverage_2026.csv")
OUTPUT = Path("coverage_data/coverage_audit_2026.json")

FIELDS = [
    "season",
    "team",
    "games_charted",
    "coverage_snaps",
    "man_snaps",
    "zone_snaps",
    "blitz_rate",
    "pressure_rate",
    "source_name",
    "source_url",
    "source_date",
]

def number(value, field, errors, row_number):
    if value is None or str(value).strip() == "":
        return None
    try:
        result = float(value)
        if not (0 <= result <= 100000):
            raise ValueError()
        return result
    except ValueError:
        errors.append(
            f"Row {row_number}: invalid {field}"
        )
        return None

def validate_row(row, row_number):
    errors = []
    clean = dict(row)

    required = [
        "season", "team", "games_charted",
        "coverage_snaps", "man_snaps",
        "zone_snaps", "source_name",
        "source_url", "source_date"
    ]

    for field in required:
        if not str(row.get(field, "")).strip():
            errors.append(
                f"Row {row_number}: missing {field}"
            )

    if str(row.get("season", "")).strip() != "2026":
        errors.append(
            f"Row {row_number}: expected 2026 season"
        )

    counts = {}
    for field in [
        "games_charted", "coverage_snaps",
        "man_snaps", "zone_snaps"
    ]:
        value = number(
            row.get(field), field, errors, row_number
        )
        if value is not None and not value.is_integer():
            errors.append(
                f"Row {row_number}: {field} must be integer"
            )
        counts[field] = value

    total = counts["coverage_snaps"]
    man = counts["man_snaps"]
    zone = counts["zone_snaps"]

    if None not in (total, man, zone):
        if man + zone > total:
            errors.append(
                f"Row {row_number}: coverage counts "
                "exceed total snaps"
            )

        if total > 0:
            clean["man_rate"] = round(
                man / total * 100, 2
            )
            clean["zone_rate"] = round(
                zone / total * 100, 2
            )
        else:
            clean["man_rate"] = None
            clean["zone_rate"] = None

    for field in ["blitz_rate", "pressure_rate"]:
        value = number(
            row.get(field), field, errors, row_number
        )
        if value is not None and value > 100:
            errors.append(
                f"Row {row_number}: {field} exceeds 100%"
            )

    url = str(row.get("source_url", "")).strip()
    if url and not url.startswith(("https://", "http://")):
        errors.append(
            f"Row {row_number}: invalid source URL"
        )

    date = str(row.get("source_date", "")).strip()
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            errors.append(
                f"Row {row_number}: invalid source date"
            )

    clean["verification_status"] = (
        "STRUCTURALLY_VALID" if not errors
        else "INVALID"
    )

    return clean, errors

def run(input_path, output_path):
    audit = {
        "engine": "NFL LOCKBOX Coverage Intelligence",
        "version": "1.0",
        "season": 2026,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "input_file": str(input_path),
        "status": "NO_DATA",
        "rows_checked": 0,
        "valid_rows": 0,
        "errors": [],
        "teams": [],
        "source_verified": False,
        "note": (
            "Structural validation does not independently "
            "verify source accuracy or charting methodology."
        ),
    }

    if not input_path.exists():
        audit["note"] = (
            "No coverage CSV supplied. "
            "No coverage statistics were generated."
        )
    else:
        with input_path.open(
            newline="", encoding="utf-8-sig"
        ) as handle:
            reader = csv.DictReader(handle)

            missing = [
                field for field in FIELDS
                if field not in (reader.fieldnames or [])
            ]

            if missing:
                audit["errors"].append(
                    "Missing columns: " + ", ".join(missing)
                )
            else:
                seen = set()

                for row_number, row in enumerate(
                    reader, start=2
                ):
                    audit["rows_checked"] += 1

                    clean, errors = validate_row(
                        row, row_number
                    )

                    team = str(
                        row.get("team", "")
                    ).strip().upper()

                    if team in seen:
                        errors.append(
                            f"Row {row_number}: duplicate team"
                        )

                    seen.add(team)
                    audit["errors"].extend(errors)

                    if not errors:
                        audit["valid_rows"] += 1
                        audit["teams"].append({
                            "team": team,
                            "man_rate": clean.get("man_rate"),
                            "zone_rate": clean.get("zone_rate"),
                            "source": row["source_name"],
                            "status": "STRUCTURALLY_VALID",
                        })

                if audit["errors"]:
                    audit["status"] = "FAILED"
                elif audit["valid_rows"]:
                    audit["status"] = "PASSED"

    output_path.parent.mkdir(
        parents=True, exist_ok=True
    )

    output_path.write_text(
        json.dumps(audit, indent=2),
        encoding="utf-8"
    )

    print(json.dumps(audit, indent=2))

    return 1 if audit["status"] == "FAILED" else 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input", type=Path, default=INPUT
    )
    parser.add_argument(
        "--output", type=Path, default=OUTPUT
    )
    args = parser.parse_args()

    raise SystemExit(
        run(args.input, args.output)
    )
