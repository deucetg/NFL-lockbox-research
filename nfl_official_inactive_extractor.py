
#!/usr/bin/env python3
"""
NFL LOCKBOX — Phase 8B Step 5D
Official Inactive Evidence Extractor v1

Reads archived NFL.com NewsArticle JSON-LD.
Produces unverified extraction candidates only.

NO source authentication.
NO GSIS matching.
NO official staging writes.
NO game-day availability changes.

Usage:
python nfl_official_inactive_extractor.py

Or use an extracted artifact directory:
python nfl_official_inactive_extractor.py \
  --evidence-dir /path/to/official_inactive_evidence
"""

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

VERSION = "phase8b_inactive_extractor_v1"

DEFAULT_DIR = (
    "NFL_LOCKBOX_2026/source_data/"
    "official_inactive_evidence"
)

FIELDS = [
    "source_url",
    "source_sha256",
    "team_heading",
    "position",
    "player_name",
    "emergency_third_qb",
    "source_line",
    "raw_line",
    "extraction_status",
]

TEAM_HEADINGS = {
    "CARDINALS", "FALCONS", "RAVENS",
    "BILLS", "PANTHERS", "BEARS",
    "BENGALS", "BROWNS", "COWBOYS",
    "BRONCOS", "LIONS", "PACKERS",
    "TEXANS", "COLTS", "JAGUARS",
    "CHIEFS", "RAIDERS", "CHARGERS",
    "RAMS", "DOLPHINS", "VIKINGS",
    "PATRIOTS", "SAINTS", "GIANTS",
    "JETS", "EAGLES", "STEELERS",
    "49ERS", "SEAHAWKS", "BUCCANEERS",
    "TITANS", "COMMANDERS",
}

SECTIONS = {
    "EARLY GAMES",
    "LATE GAMES",
    "SUNDAY NIGHT FOOTBALL",
    "MONDAY NIGHT FOOTBALL",
    "THURSDAY NIGHT FOOTBALL",
    "SATURDAY GAMES",
}

POSITIONS = {
    "QB", "RB", "FB", "WR", "TE",
    "LT", "RT", "LG", "RG", "C",
    "G", "T", "OT", "OL",
    "DE", "DT", "DL", "NT",
    "EDGE", "LB", "OLB", "ILB",
    "CB", "DB", "S", "FS", "SS",
    "K", "P", "LS",
}

PLAYER_PATTERN = re.compile(
    r"^([A-Za-z]+)\s+(.+?)"
    r"(?:\s+\(emergency third QB\))?$",
    flags=re.I,
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class JSONLDScripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.collecting = False
        self.parts = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "script":
            return

        attributes = dict(attrs)
        content_type = (
            attributes.get("type") or ""
        ).lower().strip()

        self.collecting = (
            content_type == "application/ld+json"
        )
        self.parts = []

    def handle_data(self, data):
        if self.collecting:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script":
            if self.collecting:
                self.scripts.append(
                    "".join(self.parts)
                )
            self.collecting = False
            self.parts = []


def find_articles(html_text):
    parser = JSONLDScripts()
    parser.feed(html_text)

    articles = []

    def visit(obj):
        if isinstance(obj, list):
            for item in obj:
                visit(item)
            return

        if not isinstance(obj, dict):
            return

        types = obj.get("@type", [])
        if isinstance(types, str):
            types = [types]

        if "NewsArticle" in types:
            if isinstance(
                obj.get("articleBody"), str
            ):
                articles.append(obj)

        if "@graph" in obj:
            visit(obj["@graph"])

    for script in parser.scripts:
        try:
            visit(json.loads(script))
        except (ValueError, TypeError):
            continue

    return articles


def validate_source_url(url):
    parsed = urlparse(url)

    return (
        parsed.scheme == "https"
        and parsed.hostname in {
            "www.nfl.com", "nfl.com"
        }
        and parsed.path.startswith("/news/")
        and "inactive" in parsed.path.lower()
        and not parsed.username
        and not parsed.password
        and not parsed.port
    )


def parse_body(body, url, digest):
    records = []
    rejected = []
    teams = set()
    seen = set()
    current_team = None

    for line_number, original in enumerate(
        body.splitlines(), start=1
    ):
        line = " ".join(original.split())

        if not line:
            continue

        upper = line.upper()

        if upper in TEAM_HEADINGS:
            current_team = upper
            teams.add(upper)
            continue

        if upper in SECTIONS:
            current_team = None
            continue

        # Introductory prose is not player data.
        if current_team is None:
            continue

        match = PLAYER_PATTERN.fullmatch(line)

        if not match:
            rejected.append({
                "line": line_number,
                "team": current_team,
                "raw": line,
                "reason": "UNRECOGNIZED_LINE",
            })
            continue

        position = match.group(1).upper()
        name = match.group(2).strip()

        if position not in POSITIONS:
            rejected.append({
                "line": line_number,
                "team": current_team,
                "raw": line,
                "reason": "UNKNOWN_POSITION",
            })
            continue

        if not (
            2 <= len(name) <= 90
            and any(c.isalpha() for c in name)
            and not any(c.isdigit() for c in name)
        ):
            rejected.append({
                "line": line_number,
                "team": current_team,
                "raw": line,
                "reason": "INVALID_PLAYER_NAME",
            })
            continue

        key = (current_team, name.casefold())

        if key in seen:
            rejected.append({
                "line": line_number,
                "team": current_team,
                "raw": line,
                "reason": "DUPLICATE_NAME_TEAM",
            })
            continue

        seen.add(key)

        emergency = bool(
            re.search(
                r"\(emergency third QB\)$",
                line,
                flags=re.I,
            )
        )

        records.append({
            "source_url": url,
            "source_sha256": digest,
            "team_heading": current_team,
            "position": position,
            "player_name": name,
            "emergency_third_qb": emergency,
            "source_line": line_number,
            "raw_line": line,
            "extraction_status": (
                "EXTRACTED_UNVERIFIED"
            ),
        })

    return records, rejected, sorted(teams)


def write_csv(path, records):
    with path.open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=FIELDS
        )
        writer.writeheader()
        writer.writerows(records)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--evidence-dir",
        default=DEFAULT_DIR,
    )
    args = parser.parse_args()

    directory = Path(args.evidence_dir)
    audit_path = (
        directory / "collection_audit_2026.json"
    )

    output_csv = (
        directory
        / "extracted_inactives_preview_2026.csv"
    )
    output_audit = (
        directory
        / "extraction_audit_2026.json"
    )

    audit = {
        "phase": "8B",
        "version": VERSION,
        "generated_at_utc": utc_now(),
        "status": "NOT_PROCESSED",
        "source_hash_verified": False,
        "content_authenticated": False,
        "records_extracted": 0,
        "records_rejected": 0,
        "gsis_ids_resolved": 0,
        "staging_rows_modified": 0,
        "availability_rows_modified": 0,
        "errors": [],
    }

    records = []
    rejected = []
    teams = []

    try:
        if not audit_path.is_file():
            raise ValueError(
                "Collection audit not found"
            )

        collection = json.loads(
            audit_path.read_text(
                encoding="utf-8"
            )
        )

        if collection.get("status") != (
            "CAPTURED_UNAUTHENTICATED"
        ):
            raise ValueError(
                "Source collection not successful"
            )

        snapshots = collection.get(
            "snapshots", []
        )

        if len(snapshots) != 1:
            raise ValueError(
                "Expected exactly one snapshot"
            )

        # Use the archived filename, never
        # an arbitrary path from audit JSON.
        filename = Path(snapshots[0]).name

        if not re.fullmatch(
            r"nfl_inactives_"
            r"\d{8}T\d{6}Z_"
            r"[0-9a-f]{16}\.html",
            filename,
        ):
            raise ValueError(
                "Unexpected snapshot filename"
            )

        snapshot = directory / filename

        if not snapshot.is_file():
            raise ValueError(
                "Archived HTML not found"
            )

        payload = snapshot.read_bytes()

        if not 500 <= len(payload) <= 3000000:
            raise ValueError(
                "Invalid snapshot size"
            )

        digest = hashlib.sha256(
            payload
        ).hexdigest()

        if digest != collection.get("sha256"):
            raise ValueError(
                "SHA-256 verification failed"
            )

        if digest[:16] not in filename:
            raise ValueError(
                "Snapshot filename hash mismatch"
            )

        audit["source_hash_verified"] = True
        audit["source_sha256"] = digest

        url = collection.get("final_url", "")

        if not validate_source_url(url):
            raise ValueError(
                "Unapproved source URL"
            )

        html_text = payload.decode(
            "utf-8", errors="strict"
        )

        articles = find_articles(html_text)

        if len(articles) != 1:
            raise ValueError(
                "Expected one NewsArticle "
                "with articleBody; found "
                + str(len(articles))
            )

        article = articles[0]

        article_url = article.get("url", "")

        if article_url != url:
            raise ValueError(
                "Article URL differs "
                "from captured source"
            )

        body = article["articleBody"]

        if "inactive" not in (
            article.get("headline", "")
        ).lower():
            raise ValueError(
                "Article headline not about inactives"
            )

        records, rejected, teams = parse_body(
            body, url, digest
        )

        if not records:
            raise ValueError(
                "No player entries extracted"
            )

        audit["status"] = (
            "EXTRACTED_WITH_REJECTIONS"
            if rejected
            else "EXTRACTED_UNVERIFIED"
        )

        audit["article_headline"] = (
            article.get("headline", "")
        )
        audit["article_date_published_unverified"] = (
            article.get("datePublished", "")
        )
        audit["source_url"] = url
        audit["source_snapshot"] = filename

    except Exception as error:
        audit["status"] = "EXTRACTION_FAILED"
        audit["errors"].append(str(error))

    audit["records_extracted"] = len(records)
    audit["records_rejected"] = len(rejected)
    audit["teams_found"] = teams
    audit["team_count"] = len(teams)
    audit["records_by_team"] = dict(
        sorted(
            Counter(
                r["team_heading"]
                for r in records
            ).items()
        )
    )
    audit["rejected_details"] = rejected

    audit["limitations"] = [
        "Extracted names are unverified candidates.",
        "NFL.com page content is not independently authenticated.",
        "Article publication timestamp is not authenticated.",
        "Team headings are not schedule-reconciled.",
        "No game IDs or weeks are assigned.",
        "No GSIS IDs are matched.",
        "Emergency third QB notation is preserved.",
        "No player is inferred active.",
        "No official staging file is modified.",
        "No research availability status is changed.",
    ]

    directory.mkdir(
        parents=True, exist_ok=True
    )

    write_csv(output_csv, records)

    output_audit.write_text(
        json.dumps(
            audit, indent=2, ensure_ascii=False
        ) + "\n",
        encoding="utf-8",
    )

    print(json.dumps({
        "version": VERSION,
        "status": audit["status"],
        "records_extracted": len(records),
        "records_rejected": len(rejected),
        "teams_found": len(teams),
        "source_hash_verified": (
            audit["source_hash_verified"]
        ),
        "csv": str(output_csv),
        "audit": str(output_audit),
    }, indent=2))

    if audit["status"] == "EXTRACTION_FAILED":
        raise SystemExit(
            "Official inactive extraction failed"
        )


if __name__ == "__main__":
    main()
