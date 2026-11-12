
#!/usr/bin/env python3
"""NFL LOCKBOX 9J.1: Audited scheduled concept research rankings.

Ranks descriptive matchups within concept and window.
No odds, player projections, causal claims, or inferred coverage.
FTN Data via nflverse, CC BY-SA 4.0.
"""

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

WINDOWS = ("SEASON", "L3", "L5", "L8")
CONCEPTS = (
    "motion",
    "play_action",
    "rpo",
    "screen_pass",
    "blitz_charted",
)

FIELDS = [
    "game_id",
    "game_date",
    "week",
    "offense",
    "defense",
    "venue",
    "window",
    "concept",
    "research_category",
    "offense_games",
    "defense_games",
    "offense_concept_plays",
    "offense_concept_rate_pct",
    "defense_concept_plays",
    "defense_epa_observed",
    "defense_epa_per_play_allowed",
    "defense_offensive_success_rate_allowed_pct",
    "offense_usage_percentile",
    "defense_epa_percentile",
    "research_score",
    "rank_within_concept_window",
    "sample_grade",
    "research_status",
    "warning",
]


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def integer(value):
    n = number(value)
    return int(n) if n is not None and n.is_integer() else None


def percentile(value, population):
    """Percentile among observed comparison values; ties share rank."""
    if value is None or not population:
        return None
    below = sum(x < value for x in population)
    equal = sum(x == value for x in population)
    return round(100 * (below + 0.5 * equal) / len(population), 2)


def read_csv(path, required):
    with path.open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(
                f"Missing columns in {path}: {sorted(missing)}"
            )
        return list(reader)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", default="NFL_LOCKBOX_2026"
    )
    parser.add_argument(
        "--min-defense-plays", type=int, default=20
    )
    parser.add_argument(
        "--min-offense-plays", type=int, default=10
    )
    parser.add_argument(
        "--min-games", type=int, default=3
    )
    args = parser.parse_args()

    if (
        args.min_defense_plays < 1
        or args.min_offense_plays < 1
        or args.min_games < 1
    ):
        parser.error("Minimum samples must be positive")

    root = Path(args.root)
    adv = root / "advanced_analytics"
    source = adv / "ftn_scheduled_concept_matchups_2026.csv"
    source_audit = (
        adv / "ftn_scheduled_concept_matchups_audit_2026.json"
    )

    output_file = adv / "ftn_matchup_research_rankings_2026.csv"
    audit_file = adv / "ftn_matchup_research_rankings_audit_2026.json"

    if not source.is_file() or not source_audit.is_file():
        raise SystemExit(
            "Missing Phase 9I matchup CSV or audit"
        )

    prior = json.loads(source_audit.read_text())
    if prior.get("status") != "PASS":
        raise SystemExit(
            "Phase 9I source audit must PASS"
        )

    required = {
        "game_id", "game_date", "week",
        "offense", "defense", "venue",
        "window", "concept",
        "offense_games", "defense_games",
        "offense_concept_plays",
        "offense_concept_rate_pct",
        "defense_concept_plays",
        "defense_epa_observed",
        "defense_epa_per_play_allowed",
        "defense_offensive_success_rate_allowed_pct",
        "offense_sample_status",
        "defense_sample_status",
        "research_flag",
    }

    rows = read_csv(source, required)
    if not rows:
        raise SystemExit("No matchup rows available")

    seen = set()
    groups = defaultdict(list)
    quality = Counter()

    for raw in rows:
        key = (
            raw["game_id"],
            raw["offense"],
            raw["defense"],
            raw["window"],
            raw["concept"],
        )

        if key in seen:
            raise SystemExit(
                f"Duplicate scheduled concept matchup: {key}"
            )
        seen.add(key)

        if (
            raw["window"] not in WINDOWS
            or raw["concept"] not in CONCEPTS
        ):
            quality["unsupported_concept_or_window"] += 1
            continue

        if (
            not raw["game_id"]
            or not raw["offense"]
            or not raw["defense"]
            or raw["offense"] == raw["defense"]
        ):
            quality["invalid_matchup"] += 1
            continue

        groups[
            (raw["concept"], raw["window"])
        ].append(raw)

    if quality["invalid_matchup"] or quality[
        "unsupported_concept_or_window"
    ]:
        raise SystemExit(
            f"Invalid source rows: {dict(quality)}"
        )

    results = []

    for (concept, window), members in sorted(
        groups.items()
    ):
        is_blitz = concept == "blitz_charted"

        # Deduplicate the same defense's historical EPA
        # appearing in multiple scheduled matchup directions.
        defense_values = {}
        for r in members:
            defense = r["defense"]
            value = number(
                r["defense_epa_per_play_allowed"]
            )
            if value is None:
                continue

            if (
                defense in defense_values
                and defense_values[defense] != value
            ):
                raise SystemExit(
                    f"Conflicting defensive EPA: "
                    f"{defense}/{concept}/{window}"
                )
            defense_values[defense] = value

        epa_population = list(
            defense_values.values()
        )

        # One observation per offense in each concept/window.
        offense_values = {}
        if not is_blitz:
            for r in members:
                offense = r["offense"]
                value = number(
                    r["offense_concept_rate_pct"]
                )
                if value is None:
                    continue

                if (
                    offense in offense_values
                    and offense_values[offense] != value
                ):
                    raise SystemExit(
                        f"Conflicting offensive usage: "
                        f"{offense}/{concept}/{window}"
                    )
                offense_values[offense] = value

        usage_population = list(
            offense_values.values()
        )

        ranked = []

        for r in members:
            offense_games = integer(
                r["offense_games"]
            ) or 0
            defense_games = integer(
                r["defense_games"]
            ) or 0

            offense_plays = integer(
                r["offense_concept_plays"]
            ) or 0
            defense_plays = integer(
                r["defense_concept_plays"]
            ) or 0
            epa_plays = integer(
                r["defense_epa_observed"]
            ) or 0

            offense_rate = number(
                r["offense_concept_rate_pct"]
            )
            epa = number(
                r["defense_epa_per_play_allowed"]
            )

            source_ok = (
                r["offense_sample_status"] == "COMPLETE"
                and r["defense_sample_status"] == "COMPLETE"
            )

            defense_sample_ok = (
                defense_games >= args.min_games
                and defense_plays >= args.min_defense_plays
                and epa_plays >= args.min_defense_plays
                and epa is not None
            )

            offense_sample_ok = (
                offense_games >= args.min_games
                and offense_plays >= args.min_offense_plays
                and offense_rate is not None
            )

            # Blitz frequency faced is NOT an offensive
            # play-calling preference, so never score it
            # as offensive concept usage.
            if is_blitz:
                offense_sample_ok = (
                    offense_games >= args.min_games
                )

            warnings = []

            if not source_ok:
                warnings.append("SOURCE_SAMPLE_LIMITED")

            if not defense_sample_ok:
                warnings.append("DEFENSE_SAMPLE_LIMITED")

            if not offense_sample_ok:
                warnings.append(
                    "OFFENSE_SAMPLE_LIMITED"
                    if not is_blitz
                    else "OFFENSE_GAMES_LIMITED"
                )

            if epa is not None and epa < 0:
                warnings.append(
                    "NEGATIVE_EPA_ALLOWED"
                )

            if is_blitz:
                warnings.append(
                    "BLITZ_EXPOSURE_NOT_OFFENSIVE_PLAY_CALL"
                )

            usage_pct = (
                percentile(
                    offense_rate,
                    usage_population
                )
                if not is_blitz
                else None
            )

            epa_pct = percentile(
                epa, epa_population
            )

            eligible = (
                source_ok
                and defense_sample_ok
                and offense_sample_ok
            )

            score = None

            if eligible and epa_pct is not None:
                if is_blitz:
                    # Context ranking only.
                    score = round(epa_pct, 2)
                elif usage_pct is not None:
                    # Relative research-priority score.
                    # Not a predicted EPA, win rate,
                    # prop hit rate, or betting edge.
                    score = round(
                        0.5 * usage_pct
                        + 0.5 * epa_pct,
                        2
                    )

            if not eligible:
                status = "REVIEW_SAMPLE"
                grade = "LIMITED"
            elif is_blitz:
                status = "BLITZ_CONTEXT_ONLY"
                grade = "CONTEXT"
            else:
                status = "RESEARCH_ELIGIBLE"
                grade = (
                    "A" if min(
                        offense_games,
                        defense_games
                    ) >= 5
                    and offense_plays >= 20
                    and defense_plays >= 40
                    else "B"
                )

            record = {
                "game_id": r["game_id"],
                "game_date": r["game_date"],
                "week": r["week"],
                "offense": r["offense"],
                "defense": r["defense"],
                "venue": r["venue"],
                "window": window,
                "concept": concept,
                "research_category": (
                    "DEFENSIVE_BLITZ_CONTEXT"
                    if is_blitz
                    else "OFFENSIVE_CONCEPT_VS_DEFENSE"
                ),
                "offense_games": offense_games,
                "defense_games": defense_games,
                "offense_concept_plays": offense_plays,
                "offense_concept_rate_pct": (
                    offense_rate
                    if offense_rate is not None
                    else ""
                ),
                "defense_concept_plays": defense_plays,
                "defense_epa_observed": epa_plays,
                "defense_epa_per_play_allowed": (
                    epa if epa is not None else ""
                ),
                "defense_offensive_success_rate_allowed_pct":
                    r["defense_offensive_success_rate_allowed_pct"],
                "offense_usage_percentile": (
                    usage_pct if usage_pct is not None else ""
                ),
                "defense_epa_percentile": (
                    epa_pct if epa_pct is not None else ""
                ),
                "research_score": (
                    score if score is not None else ""
                ),
                "rank_within_concept_window": "",
                "sample_grade": grade,
                "research_status": status,
                "warning": "|".join(warnings),
            }

            ranked.append(record)

        eligible_ranked = sorted(
            (
                r for r in ranked
                if isinstance(
                    r["research_score"], (int, float)
                )
            ),
            key=lambda r: (
                -r["research_score"],
                r["game_id"],
                r["offense"],
            )
        )

        last_score = None
        last_rank = 0

        for position, r in enumerate(
            eligible_ranked, 1
        ):
            if r["research_score"] != last_score:
                last_rank = position
                last_score = r["research_score"]

            r["rank_within_concept_window"] = last_rank

        results.extend(ranked)

    if not results:
        raise SystemExit("No research rankings produced")

    results.sort(
        key=lambda r: (
            WINDOWS.index(r["window"]),
            CONCEPTS.index(r["concept"]),
            (
                r["rank_within_concept_window"]
                if r["rank_within_concept_window"] != ""
                else 999999
            ),
            r["game_id"],
            r["offense"],
        )
    )

    adv.mkdir(parents=True, exist_ok=True)

    with output_file.open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=FIELDS
        )
        writer.writeheader()
        writer.writerows(results)

    status_counts = Counter(
        r["research_status"] for r in results
    )

    audit = {
        "version": "9j1",
        "created_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "status": "PASS",
        "source_audit_status": prior["status"],
        "source_rows": len(rows),
        "output_rows": len(results),
        "scheduled_games": len({
            r["game_id"] for r in results
        }),
        "scheduled_team_matchups": len({
            (
                r["game_id"],
                r["offense"],
                r["defense"],
            )
            for r in results
        }),
        "concept_windows": len(groups),
        "status_counts": dict(status_counts),
        "quality": dict(quality),
        "thresholds": {
            "min_defense_plays":
                args.min_defense_plays,
            "min_offense_plays":
                args.min_offense_plays,
            "min_games": args.min_games,
        },
        "source": (
            "FTN Data via nflverse, CC BY-SA 4.0; "
            "LOCKBOX Phase 9I matchup report"
        ),
        "limitations": [
            "Scores rank research priority, not predicted outcomes.",
            "Ranks are only comparable within the same concept and window.",
            "Four windows are overlapping historical samples, not independent observations.",
            "Blitz exposure is not offensive play-calling.",
            "EPA is descriptive, not opponent-adjusted or causal.",
            "Source completeness does not guarantee a large sample.",
            "No man-zone coverage or QB pressure inferred.",
            "No odds, wagers, or player prop projections.",
            "Player availability is not verified.",
        ],
        "output": str(output_file),
    }

    audit_file.write_text(
        json.dumps(audit, indent=2) + "\n",
        encoding="utf-8"
    )

    print(json.dumps({
        "status": audit["status"],
        "output_rows": len(results),
        "scheduled_games": audit["scheduled_games"],
        "status_counts": dict(status_counts),
    }, indent=2))


if __name__ == "__main__":
    main()
