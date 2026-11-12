
#!/usr/bin/env python3
"""Audit PFR-to-GSIS player IDs for NFL LOCKBOX 2026."""

import csv
import json
import sqlite3
import tempfile
import urllib.request
from collections import defaultdict, Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE / "NFL_LOCKBOX_2026"
DB = ROOT / "nfl_lockbox_2026.sqlite"
OUTPUT = ROOT / "player_identity_audit.csv"
SUMMARY = ROOT / "player_identity_summary.json"

SOURCE_URL = (
    "https://github.com/nflverse/nflverse-players/"
    "releases/download/players_components/players_full.rds"
)

try:
    import pyreadr
except ImportError as exc:
    raise SystemExit(
        "Missing dependency. Install with: pip install pyreadr"
    ) from exc


def clean(value):
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("", "nan", "none", "na") else text


if not DB.exists():
    raise FileNotFoundError(f"Database not found: {DB}")

ROOT.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory() as folder:
    source = Path(folder) / "players_full.rds"
    urllib.request.urlretrieve(SOURCE_URL, source)
    frames = pyreadr.read_r(str(source))

    if len(frames) != 1:
        raise ValueError("Unexpected player dataset structure")

    players = next(iter(frames.values()))

required = {"pfr_id", "gsis_id"}
missing = required - set(players.columns)
if missing:
    raise ValueError(f"Missing mapping columns: {sorted(missing)}")

mapping = defaultdict(set)

for pfr, gsis in zip(players["pfr_id"], players["gsis_id"]):
    pfr_id = clean(pfr)
    gsis_id = clean(gsis)
    if pfr_id and gsis_id:
        mapping[pfr_id].add(gsis_id)

with sqlite3.connect(DB) as conn:
    snaps = conn.execute("""
        SELECT game_id, team, player, pfr_player_id,
               offense_snaps
        FROM snap_counts
        ORDER BY game_id, team, player
    """).fetchall()

    usage = {
        (game, team, player_id)
        for game, team, player_id in conn.execute("""
            SELECT DISTINCT game_id, team, player_id
            FROM player_game_usage
            WHERE player_id IS NOT NULL
        """)
    }

if not snaps:
    raise ValueError("No snap-count records found")

results = []
counts = Counter()

for game, team, name, pfr, offense_snaps in snaps:
    pfr_id = clean(pfr)
    candidates = mapping.get(pfr_id, set())

    if not pfr_id:
        status = "MISSING_PFR_ID"
        gsis_id = ""
    elif not candidates:
        status = "NO_ID_MAPPING"
        gsis_id = ""
    elif len(candidates) > 1:
        status = "CONFLICTING_MAPPING"
        gsis_id = ""
    else:
        gsis_id = next(iter(candidates))
        if (game, team, gsis_id) in usage:
            status = "VERIFIED_GAME_TEAM_USAGE"
        else:
            status = "MAPPED_NO_GAME_TEAM_USAGE"

    counts[status] += 1

    results.append({
        "game_id": game,
        "team": team,
        "player": name,
        "pfr_player_id": pfr_id,
        "gsis_id": gsis_id,
        "offense_snaps": offense_snaps,
        "status": status,
    })

fields = [
    "game_id", "team", "player", "pfr_player_id",
    "gsis_id", "offense_snaps", "status"
]

with OUTPUT.open("w", newline="", encoding="utf-8") as file:
    writer = csv.DictWriter(file, fieldnames=fields)
    writer.writeheader()
    writer.writerows(results)

summary = {
    "source": SOURCE_URL,
    "snap_records": len(snaps),
    "status_counts": dict(sorted(counts.items())),
    "note": (
        "VERIFIED_GAME_TEAM_USAGE confirms an ID mapping and "
        "matching game/team usage record, not full snap or "
        "play-by-play completeness. No existing tables modified."
    ),
}

SUMMARY.write_text(json.dumps(summary, indent=2))

print(json.dumps(summary, indent=2))
print(f"Audit written to {OUTPUT}")
print("Player matching audit completed.")
