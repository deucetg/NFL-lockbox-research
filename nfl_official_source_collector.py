
#!/usr/bin/env python3
"""
NFL LOCKBOX — Phase 8B Step 5A
Official NFL Source Collector v1

Downloads and archives a specifically supplied
NFL.com inactive report.

This is source capture, NOT authentication.
No parsing, GSIS matching, staging updates,
or game-day availability changes occur.

Usage:
python nfl_official_source_collector.py \
  --url https://www.nfl.com/news/EXAMPLE-REPORT

Without --url, creates a safe NOT_CONFIGURED audit.
"""

import argparse
import hashlib
import json
import re
import ssl
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import (
    Request,
    HTTPRedirectHandler,
    build_opener,
    HTTPSHandler,
)

VERSION = "phase8b_official_collector_v1"
MAX_BYTES = 3_000_000
TIMEOUT = 25

DEFAULT_ROOT = "NFL_LOCKBOX_2026"


def now():
    return datetime.now(timezone.utc).isoformat()


def allowed_url(raw):
    try:
        parsed = urlparse(raw)
    except ValueError:
        return False

    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()

    return (
        parsed.scheme == "https"
        and host in {"www.nfl.com", "nfl.com"}
        and path.startswith("/news/")
        and "inactive" in path
        and not parsed.username
        and not parsed.password
        and not parsed.port
        and not parsed.query
        and not parsed.fragment
    )


class SafeRedirects(HTTPRedirectHandler):
    def redirect_request(
        self, req, fp, code, msg, headers, newurl
    ):
        if not allowed_url(newurl):
            raise ValueError(
                "Redirect outside approved NFL report URLs"
            )
        return super().redirect_request(
            req, fp, code, msg, headers, newurl
        )


def fetch(url):
    if not allowed_url(url):
        raise ValueError(
            "URL must be an approved NFL.com "
            "inactive news report"
        )

    opener = build_opener(
        SafeRedirects(),
        HTTPSHandler(
            context=ssl.create_default_context()
        ),
    )

    request = Request(
        url,
        headers={
            "User-Agent": "NFL-LOCKBOX-Research/1.0",
            "Accept": "text/html",
        },
    )

    with opener.open(
        request, timeout=TIMEOUT
    ) as response:
        final_url = response.geturl()

        if not allowed_url(final_url):
            raise ValueError(
                "Final URL failed official-domain check"
            )

        content_type = (
            response.headers.get(
                "Content-Type", ""
            ).lower()
        )

        if "text/html" not in content_type:
            raise ValueError(
                "Unexpected content type: "
                + content_type
            )

        payload = response.read(MAX_BYTES + 1)

        if len(payload) > MAX_BYTES:
            raise ValueError(
                "Response exceeds safe size limit"
            )

        if len(payload) < 500:
            raise ValueError(
                "Response unexpectedly small"
            )

        return payload, final_url, content_type


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", default=DEFAULT_ROOT
    )
    parser.add_argument(
        "--url", default=""
    )
    args = parser.parse_args()

    root = Path(args.root)
    directory = (
        root / "source_data"
        / "official_inactive_evidence"
    )
    directory.mkdir(
        parents=True, exist_ok=True
    )

    audit_path = (
        directory / "collection_audit_2026.json"
    )

    audit = {
        "phase": "8B",
        "version": VERSION,
        "season": 2026,
        "generated_at_utc": now(),
        "requested_url": args.url,
        "status": "NOT_CONFIGURED",
        "source_domain_validated": False,
        "content_authenticated": False,
        "inactive_records_extracted": 0,
        "gsis_ids_resolved": 0,
        "staging_rows_modified": 0,
        "availability_rows_modified": 0,
        "snapshots": [],
        "errors": [],
    }

    if not args.url:
        audit["errors"].append(
            "No official report URL supplied"
        )

    else:
        try:
            if not allowed_url(args.url):
                raise ValueError(
                    "URL failed strict NFL.com allowlist"
                )

            payload, final_url, content_type = (
                fetch(args.url)
            )

            digest = hashlib.sha256(
                payload
            ).hexdigest()

            stamp = datetime.now(
                timezone.utc
            ).strftime("%Y%m%dT%H%M%SZ")

            # Content-addressed filename prevents
            # confusing two different page versions.
            filename = (
                f"nfl_inactives_{stamp}_"
                f"{digest[:16]}.html"
            )

            destination = directory / filename
            destination.write_bytes(payload)

            # These checks are diagnostic only.
            # They do not authenticate a report.
            html = payload.decode(
                "utf-8", errors="replace"
            )

            title_match = re.search(
                r"<title[^>]*>(.*?)</title>",
                html,
                flags=re.I | re.S,
            )

            title = (
                re.sub(
                    r"\s+", " ",
                    title_match.group(1)
                ).strip()
                if title_match else ""
            )

            audit.update({
                "status": "CAPTURED_UNAUTHENTICATED",
                "source_domain_validated": True,
                "final_url": final_url,
                "retrieved_at_utc": now(),
                "content_type": content_type,
                "bytes": len(payload),
                "sha256": digest,
                "html_title": title,
                "snapshots": [str(destination)],
            })

        except Exception as error:
            audit["status"] = "FETCH_FAILED"
            audit["errors"].append(str(error))

    audit["limitations"] = [
        "NFL.com domain validation is not article authentication.",
        "HTML may contain incomplete or dynamically loaded content.",
        "No publication timestamp is authenticated.",
        "No game, team, or week is inferred.",
        "No player names or GSIS IDs are extracted.",
        "No official inactive status is confirmed.",
        "Existing research and staging remain unchanged.",
    ]

    audit_path.write_text(
        json.dumps(audit, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(audit, indent=2))

    if audit["status"] == "FETCH_FAILED":
        raise SystemExit(
            "Official NFL source collection failed"
        )


if __name__ == "__main__":
    main()
