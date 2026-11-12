
#!/usr/bin/env python3
"""Import 2026 snap counts without guessing player-ID matches."""

import csv
import gzip
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
SOURCE = BASE / "NFL_LOCKBOX_2026/source_data/snap_counts_2026.csv.gz"
DATABASE = BASE / "NFL_LOCKBOX_2026/nfl_lockbox_2026.sqlite"

REQUIRED = {
    "game_id", "season", "week", "player",
    "pfr_player_id", "position", "team", "opponent",
    "offense_snaps", "offense_pct",
    "defense_snaps", "defense_pct",
    "st_snaps", "st_pct",
}

if not SOURCE.exists():
    raise FileNotFoundError(f"Missing snap-count source: {SOURCE}")

if not DATABASE.exists():
    raise FileNotFoundError(f"Missing LOCKBOX database: {DATABASE}")

with gzip.open(SOURCE, "rt", encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)
    missing = REQUIRED - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    rows = list(reader)

if not rows:
    raise ValueError("Snap-count file is empty")

if any(row["season"] != "2026" for row in rows):
    raise ValueError("Unexpected season in snap-count data")

if any(not row["game_id"].startswith("2026_") for row in rows):
    raise ValueError("Unexpected game ID")

def integer(value):
    return int(value) if value else None

def decimal(value):
    return float(value) if value else None

records = [
    (
        row["game_id"],
        integer(row["season"]),
        integer(row["week"]),
        row["player"],
        row["pfr_player_id"],
        row["position"],
        row["team"],
        row["opponent"],
        integer(row["offense_snaps"]),
        decimal(row["offense_pct"]),
        integer(row["defense_snaps"]),
        decimal(row["defense_pct"]),
        integer(row["st_snaps"]),
        decimal(row["st_pct"]),
    )
    for row in rows
]

with sqlite3.connect(DATABASE) as conn:
    conn.execute("BEGIN")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS snap_counts (
            game_id TEXT NOT NULL,
            season INTEGER,
            week INTEGER,
            player TEXT,
            pfr_player_id TEXT NOT NULL,
            position TEXT,
            team TEXT NOT NULL,
            opponent TEXT,
            offense_snaps INTEGER,
            offense_pct REAL,
            defense_snaps INTEGER,
            defense_pct REAL,
            st_snaps INTEGER,
            st_pct REAL,
            PRIMARY KEY (game_id, team, pfr_player_id)
        )
    """)

    conn.execute("DELETE FROM snap_counts")

    conn.executemany("""
        INSERT INTO snap_counts VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
    """, records)

    count = conn.execute(
        "SELECT COUNT(*) FROM snap_counts"
    ).fetchone()[0]

    if count != len(records):
        raise ValueError("Imported row count does not match source")

print(f"Imported {count} snap-count records.")
print("Player-ID matching has NOT been performed.")
