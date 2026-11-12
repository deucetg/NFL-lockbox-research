
#!/usr/bin/env python3
"""NFL LOCKBOX Step 9F.1: read-only FTN charting import and reconciliation.

Source: FTN Data via nflverse (CC BY-SA 4.0; attribute FTN Data).
No coverage labels, pressure events, or player routes are inferred.
"""
import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

FIELDS = ["game_id", "play_id", "game_date", "week", "offense", "defense", "qtr", "down", "play_type", "pass_attempt", "rush_attempt", "ftn_game_id", "ftn_play_id", "date_pulled", "starting_hash", "qb_location", "n_offense_backfield", "n_defense_box", "is_no_huddle", "is_motion", "is_play_action", "is_screen_pass", "is_rpo", "is_trick_play", "is_qb_out_of_pocket", "is_interception_worthy", "is_throw_away", "read_thrown", "is_catchable_ball", "is_contested_ball", "is_created_reception", "is_drop", "is_qb_sneak", "n_blitzers", "n_pass_rushers", "is_qb_fault_sack"]
BOOLS = [x for x in FIELDS if x.startswith("is_")]
NUMBERS = ["n_offense_backfield", "n_defense_box", "n_blitzers", "n_pass_rushers"]
CHARTED = [x for x in FIELDS if x not in {"game_id", "play_id", "game_date", "week", "offense", "defense", "qtr", "down", "play_type", "pass_attempt", "rush_attempt"}]
REQUIRED = {"nflverse_game_id", "nflverse_play_id", "season", "week"}


def clean(v):
    return str(v or "").strip()


def integer(v):
    s = clean(v)
    if not s or s.lower() in ("na", "nan", "null", "none"):
        return None
    try:
        x = float(s)
        return int(x) if x.is_integer() and x >= 0 else None
    except ValueError:
        return None


def boolean(v):
    s = clean(v).lower()
    if s in ("true", "t", "1", "yes"):
        return 1
    if s in ("false", "f", "0", "no"):
        return 0
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", default="NFL_LOCKBOX_2026/source_data/ftn_charting_2026.csv")
    p.add_argument("--database", default="NFL_LOCKBOX_2026/nfl_lockbox_2026.sqlite")
    p.add_argument("--output", default="NFL_LOCKBOX_2026/advanced_analytics")
    a = p.parse_args()
    source, database, output = Path(a.source), Path(a.database), Path(a.output)
    if not database.is_file():
        p.error(f"Missing database: {database}")
    output.mkdir(parents=True, exist_ok=True)
    audit_path = output / "ftn_charting_audit_2026.json"
    audit = {"version": "9f1", "created_utc": datetime.now(timezone.utc).isoformat(), "season": 2026, "source": str(source), "database": str(database), "attribution": "FTN Data via nflverse; CC BY-SA 4.0", "limitations": ["Charted FTN fields are not man/zone coverage labels", "Number of blitzers is not a verified pressure event", "Only joined 2026 regular-season plays are exported", "Missing FTN values remain missing, never zero-filled"]}
    if not source.is_file():
        audit.update(status="SOURCE_UNAVAILABLE", message="FTN refresh file missing; importer did not fabricate data")
        audit_path.write_text(json.dumps(audit, indent=2) + "\n")
        print(json.dumps(audit, indent=2))
        return
    audit["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    db = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    plays = {}
    for r in db.execute("SELECT game_id, play_id, game_date, week, posteam, defteam, qtr, down, play_type, pass_attempt, rush_attempt FROM plays WHERE season=2026 AND season_type='REG'"):
        key = (clean(r["game_id"]), clean(r["play_id"]))
        if key in plays:
            raise SystemExit(f"Duplicate LOCKBOX key: {key}")
        plays[key] = dict(r)
    counts = Counter()
    observed = Counter()
    joined_games = set()
    rows = []
    seen = set()
    with source.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        columns = set(reader.fieldnames or [])
        missing = REQUIRED - columns
        if missing:
            raise SystemExit(f"FTN source missing required fields: {sorted(missing)}")
        audit["available_charting_fields"] = sorted(columns.intersection(CHARTED))
        audit["missing_charting_fields"] = sorted(set(CHARTED) - columns)
        for raw in reader:
            if clean(raw.get("season")) != "2026":
                continue
            counts["source_2026_rows"] += 1
            key = (clean(raw.get("nflverse_game_id")), clean(raw.get("nflverse_play_id")))
            if key in seen:
                counts["duplicate_source_keys"] += 1
                continue
            seen.add(key)
            base = plays.get(key)
            if base is None:
                counts["unmatched_source_rows"] += 1
                continue
            if integer(raw.get("week")) != integer(base["week"]):
                counts["week_mismatch"] += 1
                continue
            row = {"game_id": key[0], "play_id": key[1], "game_date": base["game_date"], "week": base["week"], "offense": base["posteam"], "defense": base["defteam"], "qtr": base["qtr"], "down": base["down"], "play_type": base["play_type"], "pass_attempt": base["pass_attempt"], "rush_attempt": base["rush_attempt"]}
            for name in CHARTED:
                value = raw.get(name)
                if name in BOOLS:
                    value = boolean(value)
                elif name in NUMBERS or name in ("ftn_game_id", "ftn_play_id"):
                    value = integer(value)
                else:
                    value = clean(value) or None
                row[name] = value
                if value is not None:
                    observed[name] += 1
            rows.append(row)
            joined_games.add(key[0])
            counts["matched_rows"] += 1
    dest = output / "ftn_charting_joined_2026.csv"
    with dest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    audit.update(status="PASS" if rows and not counts["duplicate_source_keys"] and not counts["week_mismatch"] else "REVIEW", source_rows=counts["source_2026_rows"], matched_rows=len(rows), matched_games=len(joined_games), unmatched_source_rows=counts["unmatched_source_rows"], duplicate_source_keys=counts["duplicate_source_keys"], week_mismatch=counts["week_mismatch"], observed_field_counts=dict(observed), output=str(dest))
    audit_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": audit["status"], "matched_rows": len(rows), "matched_games": len(joined_games), "unmatched_source_rows": audit["unmatched_source_rows"]}, indent=2))
    db.close()
    if audit["status"] != "PASS":
        raise SystemExit("FTN charting reconciliation needs review")


if __name__ == "__main__":
    main()
