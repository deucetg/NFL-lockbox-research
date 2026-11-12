
#!/usr/bin/env python3
"""
NFL LOCKBOX — Phase 8B Step 1
Official Inactive Evidence Staging

Research only. No odds or wagers.

This module creates and validates an evidence
staging file. It does NOT scrape NFL websites,
infer active players, or change Phase 8A.1.

Only explicit, game-specific official inactive
records are accepted for staging. Staged records
are NOT automatically confirmed in LOCKBOX.
"""

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

VERSION = "phase8b_official_staging_v1"
SEASON = 2026

FIELDS = [
    "season",
    "week",
    "game_id",
    "team",
    "player_name",
    "gsis_id",
    "official_status",
    "source_url",
    "source_published_at_utc",
    "retrieved_at_utc",
    "verification_method",
    "evidence_note",
]

TEAM_CODES = set(
    "ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN "
    "DET GB HOU IND JAX KC LAC LA LV MIA "
    "MIN NE NO NYG NYJ PHI PIT SEA SF TB "
    "TEN WAS".split()
)

VALID_METHODS = {
    "MANUAL_OFFICIAL_REVIEW",
    "VERIFIED_OFFICIAL_EXTRACTOR",
}


def timestamp():
    return datetime.now(
        timezone.utc
    ).isoformat()


def parse_utc(raw):
    if not raw:
        return None

    try:
        parsed = datetime.fromisoformat(
            raw.replace("Z", "+00:00")
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return None

    if parsed.utcoffset().total_seconds() != 0:
        return None

    return parsed


def official_url(raw):
    try:
        parsed = urlparse(raw)
    except ValueError:
        return False

    host = (parsed.hostname or "").lower()

    return (
        parsed.scheme == "https"
        and (
            host == "nfl.com"
            or host.endswith(".nfl.com")
        )
        and bool(parsed.path.strip("/"))
        and not parsed.username
        and not parsed.password
    )


def validate(row):
    errors = []

    try:
        season = int(row["season"])
        week = int(row["week"])
    except (ValueError, TypeError):
        return ["INVALID_SEASON_OR_WEEK"]

    if season != SEASON:
        errors.append("WRONG_SEASON")

    if not 1 <= week <= 25:
        errors.append("INVALID_WEEK")

    game_id = row["game_id"].strip()
    team = row["team"].strip().upper()
    name = row["player_name"].strip()
    gsis = row["gsis_id"].strip()

    if not game_id.startswith(
        f"{SEASON}_{week:02d}_"
    ):
        errors.append("GAME_ID_WEEK_MISMATCH")

    if team not in TEAM_CODES:
        errors.append("UNKNOWN_TEAM")

    if not name:
        errors.append("PLAYER_NAME_MISSING")

    if not (
        gsis.startswith("00-")
        and len(gsis) == 10
        and gsis[3:].isdigit()
    ):
        errors.append("GSIS_ID_NOT_VERIFIED")

    if row["official_status"].strip().upper() != (
        "INACTIVE"
    ):
        errors.append("NOT_EXPLICITLY_INACTIVE")

    if not official_url(row["source_url"].strip()):
        errors.append("SOURCE_NOT_NFL_OFFICIAL")

    published = parse_utc(
        row["source_published_at_utc"].strip()
    )
    retrieved = parse_utc(
        row["retrieved_at_utc"].strip()
    )

    if published is None:
        errors.append("PUBLICATION_TIME_UNVERIFIED")

    if retrieved is None:
        errors.append("RETRIEVAL_TIME_INVALID")

    if published and retrieved and published > retrieved:
        errors.append("PUBLICATION_AFTER_RETRIEVAL")

    if (
        row["verification_method"].strip()
        not in VALID_METHODS
    ):
        errors.append("METHOD_NOT_VERIFIED")

    if not row["evidence_note"].strip():
        errors.append("EVIDENCE_NOTE_MISSING")

    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        default="NFL_LOCKBOX_2026",
    )
    args = parser.parse_args()

    root = Path(args.root)
    directory = root / "source_data"
    directory.mkdir(
        parents=True, exist_ok=True
    )

    source = (
        directory
        / "official_inactives_staging_2026.csv"
    )

    audit_path = (
        directory
        / "official_inactives_staging_audit_2026.json"
    )

    if not source.exists():
        with source.open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.DictWriter(
                handle, fieldnames=FIELDS
            )
            writer.writeheader()

    with source.open(
        "r", newline="", encoding="utf-8-sig"
    ) as handle:
        reader = csv.DictReader(handle)

        if reader.fieldnames != FIELDS:
            raise SystemExit(
                "Staging CSV schema mismatch"
            )

        rows = list(reader)

    accepted = []
    rejected = []
    reasons = Counter()
    seen = set()

    for index, row in enumerate(rows, start=2):
        errors = validate(row)

        identity = (
            row["game_id"].strip(),
            row["team"].strip().upper(),
            row["gsis_id"].strip(),
        )

        if identity in seen:
            errors.append("DUPLICATE_PLAYER_GAME")

        seen.add(identity)

        if errors:
            rejected.append({
                "csv_line": index,
                "errors": errors,
            })
            reasons.update(errors)
        else:
            accepted.append(row)

    audit = {
        "phase": "8B",
        "version": VERSION,
        "season": SEASON,
        "generated_utc": timestamp(),
        "status": (
            "PASS" if not rejected
            else "REJECTED_RECORDS_PRESENT"
        ),
        "staging_rows": len(rows),
        "schema_valid_rows": len(accepted),
        "rejected_rows": len(rejected),
        "rejection_reasons": dict(reasons),
        "rejected_details": rejected,
        "official_status_confirmed_in_lockbox": 0,
        "phase8a_modified": False,
        "game_day_status_inference_enabled": False,
        "source_file": str(source),
        "limitations": [
            "Staging is not live official verification.",
            "URL syntax checks do not prove content.",
            "GSIS format checks do not prove identity.",
            "Source publication time is supplied, "
            "not independently verified.",
            "Game and team identity require later "
            "schedule reconciliation.",
            "No active status can be inferred from "
            "absence on an inactive list.",
            "No records are merged into Phase 8A.1.",
        ],
    }

    audit_path.write_text(
        json.dumps(audit, indent=2) + "\n",
        encoding="utf-8"
    )

    print(json.dumps({
        "version": VERSION,
        "status": audit["status"],
        "staging_rows": len(rows),
        "accepted_for_staging": len(accepted),
        "rejected": len(rejected),
        "confirmed_in_lockbox": 0,
        "audit": str(audit_path),
    }, indent=2))

    if rejected:
        raise SystemExit(
            "Official inactive staging validation failed"
        )


if __name__ == "__main__":
    main()
