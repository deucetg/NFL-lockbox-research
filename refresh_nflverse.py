
#!/usr/bin/env python3
"""Download and validate 2026 nflverse datasets for NFL LOCKBOX."""

import csv
import gzip
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

YEAR = "2026"
BASE = "https://github.com/nflverse/nflverse-data/releases/download"
OUT = Path("NFL_LOCKBOX_2026/source_data")
OUT.mkdir(parents=True, exist_ok=True)

SOURCES = {
    "pbp": ("pbp", "play_by_play_2026.csv.gz"),
    "snap_counts": ("snap_counts", "snap_counts_2026.csv.gz"),
    "injuries": ("injuries", "injuries_2026.csv.gz"),
    "depth_charts": ("depth_charts", "depth_charts_2026.csv.gz"),
    "ftn_charting": ("ftn_charting", "ftn_charting_2026.csv"),
}

REQUIRED_PBP = {
    "game_id",
    "play_id",
    "season",
    "week",
    "game_date",
    "home_team",
    "away_team",
    "posteam",
    "pass_attempt",
    "rush_attempt",
}

manifest = {
    "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
    "season": YEAR,
    "sources": {},
}

for key, (release, filename) in SOURCES.items():
    url = f"{BASE}/{release}/{filename}"
    print(f"Downloading {key}: {url}", flush=True)

    try:
        request = Request(
            url,
            headers={"User-Agent": "NFL-LOCKBOX/1.0"},
        )

        with urlopen(request, timeout=180) as response:
            payload = response.read()

        if len(payload) < 100:
            raise ValueError("File unexpectedly small")

        raw = (
            gzip.decompress(payload)
            if filename.endswith(".gz")
            else payload
        )

        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        columns = set(reader.fieldnames or [])

        if not columns:
            raise ValueError("CSV header missing")

        if key == "pbp":
            missing = REQUIRED_PBP - columns
            if missing:
                raise ValueError(
                    f"Missing PBP columns: {missing}"
                )

        rows = 0
        games = set()
        dates = set()

        for row in reader:
            rows += 1

            if row.get("game_id"):
                games.add(row["game_id"])

            if row.get("game_date"):
                dates.add(row["game_date"])

        if rows == 0:
            raise ValueError("No data rows")

        if key == "pbp":
            if not any(
                game.startswith("2026_")
                for game in games
            ):
                raise ValueError("No 2026 game IDs found")

            destination = Path(filename)
        else:
            destination = OUT / filename

        destination.write_bytes(payload)

        manifest["sources"][key] = {
            "status": "verified",
            "url": url,
            "file": str(destination),
            "bytes": len(payload),
            "rows": rows,
            "games": len(games),
            "latest_game_date": (
                max(dates) if dates else None
            ),
        }

        print(
            f"Verified {key}: {rows} rows",
            flush=True,
        )

    except Exception as error:
        manifest["sources"][key] = {
            "status": "failed",
            "url": url,
            "error": str(error),
        }

        print(
            f"FAILED {key}: {error}",
            flush=True,
        )

        if key == "pbp":
            (OUT / "refresh_manifest.json").write_text(
                json.dumps(manifest, indent=2)
            )
            raise

(OUT / "refresh_manifest.json").write_text(
    json.dumps(manifest, indent=2)
)

print("NFL LOCKBOX source refresh complete.")
