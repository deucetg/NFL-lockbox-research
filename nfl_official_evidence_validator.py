
#!/usr/bin/env python3
"""
NFL LOCKBOX — Phase 8B Step 5G
Official Inactive Evidence Validator v1

Read-only validation of archived NFL.com evidence
and extracted inactive preview.

This verifies internal consistency, NOT independent
NFL publication authenticity or player availability.

No GSIS, staging, or availability writes.

Usage:
python nfl_official_evidence_validator.py
"""

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from nfl_official_inactive_extractor import (
    FIELDS,
    find_articles,
    parse_body,
    validate_source_url,
)

VERSION = "phase8b_evidence_validator_v1"

DEFAULT_DIR = (
    "NFL_LOCKBOX_2026/source_data/"
    "official_inactive_evidence"
)

OUTPUT_NAME = "evidence_validation_audit_2026.json"


def now():
    return datetime.now(timezone.utc).isoformat()


def parse_time(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
        if result.tzinfo is None:
            return None
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def read_json(path):
    with path.open(encoding="utf-8") as handle:
        result = json.load(handle)
    if not isinstance(result, dict):
        raise ValueError(
            f"Expected JSON object: {path.name}"
        )
    return result


def read_csv(path):
    with path.open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        rows = list(reader)
    return fields, rows


def canonical_url(value):
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlparse(value)
        if not validate_source_url(value):
            return ""
        return parsed._replace(
            query="", fragment=""
        ).geturl()
    except (ValueError, TypeError):
        return ""


def metadata_url(article):
    page = article.get("mainEntityOfPage")
    if isinstance(page, dict):
        return page.get("@id", "")
    if isinstance(page, str):
        return page
    return ""


def check(condition, code, problems):
    if not condition:
        problems.append(code)
    return bool(condition)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--evidence-dir", default=DEFAULT_DIR
    )
    args = parser.parse_args()

    directory = Path(args.evidence_dir)

    collection_path = (
        directory / "collection_audit_2026.json"
    )
    extraction_path = (
        directory / "extraction_audit_2026.json"
    )
    preview_path = (
        directory
        / "extracted_inactives_preview_2026.csv"
    )
    output_path = directory / OUTPUT_NAME

    problems = []
    warnings = []

    audit = {
        "phase": "8B",
        "version": VERSION,
        "generated_at_utc": now(),
        "status": "NOT_VALIDATED",
        "internal_evidence_consistent": False,
        "independently_authenticated": False,
        "official_player_status_confirmed": 0,
        "gsis_ids_resolved": 0,
        "staging_rows_modified": 0,
        "availability_rows_modified": 0,
        "checks": {},
        "errors": problems,
        "warnings": warnings,
    }

    def record(name, condition, code):
        audit["checks"][name] = check(
            condition, code, problems
        )

    try:
        for path in (
            collection_path,
            extraction_path,
            preview_path,
        ):
            if not path.is_file():
                raise ValueError(
                    f"Required input missing: {path.name}"
                )

        collection = read_json(collection_path)
        extraction = read_json(extraction_path)
        fields, saved_rows = read_csv(preview_path)

        record(
            "collector_status",
            collection.get("status")
            == "CAPTURED_UNAUTHENTICATED",
            "COLLECTOR_NOT_CAPTURED",
        )

        record(
            "extractor_status",
            extraction.get("status")
            in {
                "EXTRACTED_UNVERIFIED",
                "EXTRACTED_WITH_REJECTIONS",
            },
            "EXTRACTION_NOT_SUCCESSFUL",
        )

        snapshots = collection.get("snapshots")
        if not isinstance(snapshots, list):
            snapshots = []

        if len(snapshots) != 1:
            raise ValueError(
                "Expected exactly one archived snapshot"
            )

        filename = Path(snapshots[0]).name

        if not re.fullmatch(
            r"nfl_inactives_\d{8}T\d{6}Z_"
            r"[0-9a-f]{16}\.html",
            filename,
        ):
            raise ValueError(
                "Unexpected snapshot filename"
            )

        snapshot = directory / filename

        if not snapshot.is_file():
            raise ValueError(
                "Archived HTML snapshot missing"
            )

        payload = snapshot.read_bytes()
        digest = hashlib.sha256(
            payload
        ).hexdigest()

        record(
            "snapshot_size",
            500 <= len(payload) <= 3000000
            and collection.get("bytes") == len(payload),
            "SNAPSHOT_SIZE_MISMATCH",
        )

        record(
            "sha256_chain",
            digest == collection.get("sha256")
            == extraction.get("source_sha256")
            and digest[:16] in filename,
            "SOURCE_HASH_CHAIN_MISMATCH",
        )

        record(
            "snapshot_identity",
            Path(
                extraction.get(
                    "source_snapshot", ""
                )
            ).name == filename,
            "SNAPSHOT_IDENTITY_MISMATCH",
        )

        source_url = collection.get(
            "final_url", ""
        )

        record(
            "source_url_chain",
            bool(canonical_url(source_url))
            and source_url
            == extraction.get("source_url")
            == collection.get("requested_url"),
            "SOURCE_URL_CHAIN_MISMATCH",
        )

        retrieved = parse_time(
            collection.get("retrieved_at_utc")
        )

        record(
            "retrieval_timestamp",
            retrieved is not None,
            "INVALID_RETRIEVAL_TIME",
        )

        html_text = payload.decode(
            "utf-8", errors="strict"
        )
        articles = find_articles(html_text)

        if len(articles) != 1:
            raise ValueError(
                "Expected exactly one NewsArticle "
                "containing articleBody"
            )

        article = articles[0]

        record(
            "article_url",
            canonical_url(article.get("url", ""))
            == canonical_url(source_url)
            and canonical_url(
                metadata_url(article)
            ) == canonical_url(source_url),
            "ARTICLE_URL_MISMATCH",
        )

        publisher = article.get("publisher")
        publisher_name = (
            publisher.get("name", "")
            if isinstance(publisher, dict)
            else ""
        )

        record(
            "publisher_declaration",
            publisher_name == "NFL",
            "NFL_PUBLISHER_NOT_DECLARED",
        )

        headline = article.get("headline", "")
        description = article.get(
            "description", ""
        )

        record(
            "headline_context",
            isinstance(headline, str)
            and "inactive" in headline.lower()
            and "2026" in headline
            and "week" in headline.lower(),
            "HEADLINE_CONTEXT_MISMATCH",
        )

        record(
            "description_context",
            isinstance(description, str)
            and "inactive" in description.lower()
            and "2026" in description,
            "DESCRIPTION_CONTEXT_MISMATCH",
        )

        published = parse_time(
            article.get("datePublished")
        )
        modified = parse_time(
            article.get("dateModified")
        )

        record(
            "publication_timestamp",
            published is not None
            and published.year == 2026,
            "INVALID_PUBLICATION_TIME",
        )

        record(
            "modification_timestamp",
            modified is not None
            and published is not None
            and modified >= published,
            "INVALID_MODIFICATION_TIME",
        )

        record(
            "publication_before_capture",
            published is not None
            and retrieved is not None
            and published <= retrieved,
            "PUBLICATION_AFTER_CAPTURE",
        )

        record(
            "modification_before_capture",
            modified is not None
            and retrieved is not None
            and modified <= retrieved,
            "MODIFICATION_AFTER_CAPTURE",
        )

        body = article["articleBody"]

        replay_rows, replay_rejected, teams = (
            parse_body(body, source_url, digest)
        )

        record(
            "preview_schema",
            fields == FIELDS,
            "PREVIEW_SCHEMA_MISMATCH",
        )

        record(
            "exact_extraction_replay",
            fields == FIELDS
            and saved_rows == [
                {
                    key: str(row[key])
                    for key in FIELDS
                }
                for row in replay_rows
            ],
            "EXTRACTION_REPLAY_MISMATCH",
        )

        record(
            "extraction_counts",
            extraction.get("records_extracted")
            == len(replay_rows)
            and extraction.get("records_rejected")
            == len(replay_rejected)
            and extraction.get("team_count")
            == len(teams),
            "EXTRACTION_COUNT_MISMATCH",
        )

        record(
            "team_set",
            extraction.get("teams_found")
            == teams,
            "TEAM_SET_MISMATCH",
        )

        team_counts = dict(sorted(
            Counter(
                row["team_heading"]
                for row in replay_rows
            ).items()
        ))

        record(
            "team_counts",
            extraction.get("records_by_team")
            == team_counts,
            "TEAM_COUNT_MISMATCH",
        )

        record(
            "rejection_details",
            extraction.get("rejected_details")
            == replay_rejected,
            "REJECTION_DETAILS_MISMATCH",
        )

        # Structural plausibility only:
        # NFL game-day inactive lists are commonly
        # 5-7 players, but special cases exist.
        unusual = {
            team: count
            for team, count in team_counts.items()
            if not 1 <= count <= 7
        }

        record(
            "nonempty_team_sections",
            len(team_counts) == len(teams)
            and all(
                count > 0
                for count in team_counts.values()
            ),
            "EMPTY_TEAM_SECTION",
        )

        if unusual:
            warnings.append({
                "code": "UNUSUAL_TEAM_COUNTS",
                "teams": unusual,
            })

        if len(teams) % 2 != 0:
            warnings.append({
                "code": "ODD_NUMBER_OF_TEAMS",
                "team_count": len(teams),
            })

        match = re.search(
            r"(\d+)\s+games?",
            headline,
            flags=re.I,
        )

        declared_games = (
            int(match.group(1))
            if match else None
        )

        if declared_games is not None:
            record(
                "declared_team_count",
                len(teams) == declared_games * 2,
                "DECLARED_GAME_COVERAGE_MISMATCH",
            )
        else:
            warnings.append({
                "code": "GAME_COUNT_NOT_DECLARED",
            })

        emergency = sum(
            str(row["emergency_third_qb"])
            == "True"
            for row in replay_rows
        )

        audit.update({
            "source_url": source_url,
            "source_sha256": digest,
            "snapshot": filename,
            "article_headline": headline,
            "article_published_unverified": (
                article.get("datePublished", "")
            ),
            "article_modified_unverified": (
                article.get("dateModified", "")
            ),
            "publisher_claim": publisher_name,
            "extracted_rows": len(replay_rows),
            "rejected_rows": len(replay_rejected),
            "teams_found": teams,
            "team_count": len(teams),
            "records_by_team": team_counts,
            "declared_game_count": declared_games,
            "emergency_third_qb_annotations": emergency,
            "unusual_team_counts": unusual,
        })

        audit["internal_evidence_consistent"] = (
            not problems
        )

        audit["status"] = (
            "EVIDENCE_CONSISTENT_NOT_AUTHENTICATED"
            if not problems
            else "EVIDENCE_REVIEW_REQUIRED"
        )

    except Exception as error:
        problems.append(
            "VALIDATION_EXCEPTION: " + str(error)
        )
        audit["status"] = "VALIDATION_FAILED"

    audit["limitations"] = [
        "SHA-256 verifies archive integrity, not NFL authorship.",
        "Article metadata is self-declared by the captured page.",
        "No independent live NFL source comparison was performed.",
        "Extraction replay uses the existing extractor parser.",
        "Team-count checks are structural, not schedule verification.",
        "Emergency third QB annotations require separate review.",
        "Player names and identities are not independently verified.",
        "No GSIS IDs or game IDs are assigned.",
        "No player is inferred active or inactive in research.",
        "Official staging and availability are unchanged.",
    ]

    directory.mkdir(
        parents=True, exist_ok=True
    )

    output_path.write_text(
        json.dumps(
            audit,
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )

    print(json.dumps({
        "version": VERSION,
        "status": audit["status"],
        "checks_passed": sum(
            audit["checks"].values()
        ),
        "checks_total": len(
            audit["checks"]
        ),
        "extracted_rows": audit.get(
            "extracted_rows", 0
        ),
        "team_count": audit.get(
            "team_count", 0
        ),
        "warnings": warnings,
        "errors": problems,
        "audit": str(output_path),
    }, indent=2))

    if problems:
        raise SystemExit(
            "Official evidence validation "
            "requires review"
        )


if __name__ == "__main__":
    main()
