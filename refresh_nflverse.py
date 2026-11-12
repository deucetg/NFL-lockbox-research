
#!/usr/bin/env python3
"""
NFL LOCKBOX — Automated nflverse Source Refresh

Phase 8A upgrade:
- Preserve existing research downloads
- Add 2026 weekly roster evidence
- Validate downloaded CSVs
- Record source status and download timestamps
- Never interpret roster status as game-day status
"""

import csv
import gzip
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

YEAR = "2026"

BASE = (
    "https://github.com/nflverse/"
    "nflverse-data/releases/download"
)

OUT = Path("NFL_LOCKBOX_2026/source_data")
OUT.mkdir(parents=True, exist_ok=True)

SOURCES = {
    "pbp": (
        "pbp",
        "play_by_play_2026.csv.gz",
    ),
    "snap_counts": (
        "snap_counts",
        "snap_counts_2026.csv.gz",
    ),
    "injuries": (
        "injuries",
        "injuries_2026.csv.gz",
    ),
    "depth_charts": (
        "depth_charts",
        "depth_charts_2026.csv.gz",
    ),
    "ftn_charting": (
        "ftn_charting",
        "ftn_charting_2026.csv",
    ),
    "weekly_rosters": (
        "weekly_rosters",
        "weekly_rosters_2026.csv.gz",
    ),
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

REQUIRED_ROSTER_GROUPS = {
    "player_id": {
        "gsis_id",
        "player_gsis_id",
        "gsis",
    },
    "team": {
        "team",
        "team_abbr",
        "club_code",
        "recent_team",
    },
    "season": {"season", "season_year"},
    "week": {"week", "game_week"},
    "status": {"status", "roster_status"},
}


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def validate_roster_columns(columns):
    for label, choices in REQUIRED_ROSTER_GROUPS.items():
        if not (columns & choices):
            raise ValueError(
                "Weekly rosters missing required "
                f"{label} column; found: "
                f"{sorted(columns)}"
            )


def download_source(key, release, filename):
    url = f"{BASE}/{release}/{filename}"

    print(
        f"Downloading {key}: {url}",
        flush=True,
    )

    request = Request(
        url,
        headers={
            "User-Agent": "NFL-LOCKBOX/1.0",
            "Accept": "application/octet-stream",
        },
    )

    with urlopen(request, timeout=180) as response:
        payload = response.read()

    if len(payload) < 100:
        raise ValueError(
            "Downloaded file unexpectedly small"
        )

    raw = (
        gzip.decompress(payload)
        if filename.endswith(".gz")
        else payload
    )

    decoded = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(decoded))

    columns = set(reader.fieldnames or [])

    if not columns:
        raise ValueError("CSV header missing")

    if key == "pbp":
        missing = REQUIRED_PBP - columns
        if missing:
            raise ValueError(
                f"PBP missing columns: {sorted(missing)}"
            )

    if key == "weekly_rosters":
        validate_roster_columns(columns)

    rows = 0
    games = set()
    dates = set()
    seasons = set()
    weeks = set()

    for row in reader:
        rows += 1

        game_id = row.get("game_id")
        game_date = row.get("game_date")

        if game_id:
            games.add(game_id)

        if game_date:
            dates.add(game_date)

        season = row.get(
            "season",
            row.get("season_year", ""),
        )

        week = row.get(
            "week",
            row.get("game_week", ""),
        )

        if season:
            seasons.add(str(season).strip())

        if week:
            weeks.add(str(week).strip())

    if rows == 0:
        raise ValueError("No data rows")

    if key == "pbp":
        if not any(
            game.startswith("2026_")
            for game in games
        ):
            raise ValueError(
                "No 2026 game IDs found"
            )

    if key == "weekly_rosters":
        if YEAR not in seasons:
            raise ValueError(
                "Weekly rosters contain no "
                "2026 season records"
            )

    destination = (
        Path(filename)
        if key == "pbp"
        else OUT / filename
    )

    # Write only after validation succeeds.
    destination.write_bytes(payload)

    return {
        "status": "verified",
        "url": url,
        "file": str(destination),
        "downloaded_at_utc": utc_now(),
        "bytes": len(payload),
        "sha256": hashlib.sha256(
            payload
        ).hexdigest(),
        "rows": rows,
        "games": len(games),
        "latest_game_date": (
            max(dates) if dates else None
        ),
        "seasons": sorted(seasons),
        "weeks": sorted(
            weeks,
            key=lambda x: (
                int(x) if x.isdigit() else 999
            ),
        ),
        "columns": sorted(columns),
    }


def main():
    manifest = {
        "season": YEAR,
        "refresh_version": "phase8a_weekly_rosters_v1",
        "started_at_utc": utc_now(),
        "sources": {},
    }

    for key, (release, filename) in SOURCES.items():
        try:
            result = download_source(
                key,
                release,
                filename,
            )

            manifest["sources"][key] = result

            print(
                f"Verified {key}: "
                f"{result['rows']} rows",
                flush=True,
            )

        except Exception as error:
            destination = (
                Path(filename)
                if key == "pbp"
                else OUT / filename
            )

            # Prevent an old local file from being
            # mistaken for a fresh successful refresh.
            if destination.exists():
                destination.unlink()

            manifest["sources"][key] = {
                "status": "failed",
                "url": (
                    f"{BASE}/{release}/{filename}"
                ),
                "checked_at_utc": utc_now(),
                "error": str(error),
            }

            print(
                f"FAILED {key}: {error}",
                flush=True,
            )

            if key == "pbp":
                manifest["completed_at_utc"] = utc_now()

                (
                    OUT / "refresh_manifest.json"
                ).write_text(
                    json.dumps(
                        manifest,
                        indent=2,
                    ) + "\n",
                    encoding="utf-8",
                )

                raise

    manifest["completed_at_utc"] = utc_now()

    manifest["weekly_roster_ready"] = (
        manifest["sources"]
        .get("weekly_rosters", {})
        .get("status") == "verified"
    )

    (
        OUT / "refresh_manifest.json"
    ).write_text(
        json.dumps(
            manifest,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    print(
        "NFL LOCKBOX source refresh complete.",
        flush=True,
    )

    print(
        "Weekly roster source ready:",
        manifest["weekly_roster_ready"],
        flush=True,
    )


if __name__ == "__main__":
    main()
