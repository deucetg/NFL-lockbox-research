
#!/usr/bin/env python3
"""NFL LOCKBOX Step 2A: auditable opportunity/production divergence research.

Consumes the *fresh* Opportunity Acceleration CSV and provenance manifest. All
comparisons reuse the builder's non-overlapping team-game windows. Does not
impute absent player-games, infer participation, or assert validated games.
Research signals are hypotheses, not forecasts or recommendations.
"""

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SEASON = 2026
PAIRINGS = {
    "receiving": ("targets", "receiving_yards", 2.0, 0.5, 0.15),
    "rushing": ("rush_attempts", "rushing_yards", 4.0, 1.0, 0.15),
    "passing": ("pass_attempts", "passing_yards", 15.0, 3.0, 0.10),
}
WINDOW_SIZES = {"L3_vs_previous_3": 3, "L5_vs_previous_5": 5}
FIELDS = [
    "season", "team", "player_id", "player_name", "role", "window",
    "opportunity_metric", "production_metric", "current_game_ids",
    "previous_game_ids", "current_observed_games", "previous_observed_games",
    "missing_current_game_ids", "missing_previous_game_ids",
    "current_opportunities_per_team_game", "previous_opportunities_per_team_game",
    "opportunity_delta", "opportunity_percent_change",
    "current_production_per_team_game", "previous_production_per_team_game",
    "production_delta", "production_percent_change",
    "current_yards_per_opportunity", "previous_yards_per_opportunity",
    "yards_per_opportunity_percent_change", "divergence_signal",
    "analysis_status", "source_game_statuses", "verified_game_completeness",
    "verified_research_ready", "explanation",
]


def read_csv(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def number(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid numeric value: {value!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"Nonfinite numeric value: {value!r}")
    return result


def rounded(value, digits=4):
    return "" if value is None else round(value, digits)


def percent(current, previous):
    if previous is None or current is None or previous == 0:
        return None
    return 100.0 * (current - previous) / abs(previous)


def parse_game_ids(value):
    result = [game for game in (value or "").split("|") if game]
    if len(result) != len(set(result)):
        raise ValueError(f"Duplicated game IDs in acceleration source: {value!r}")
    return result


def validate_manifest(manifest):
    if manifest.get("source_type") != "successful_main_build_artifact":
        raise ValueError("Requires manifest from successful main build artifact, not committed root snapshots")
    if not manifest.get("upstream_run_id") or not manifest.get("upstream_head_sha"):
        raise ValueError("Missing upstream run ID or commit SHA in source manifest")
    for field in ("latest_usage_game_date", "latest_coverage_game_date"):
        value = str(manifest.get(field) or "")
        if not value.startswith(f"{SEASON}-"):
            raise ValueError(f"Unexpected season in {field}: {value!r}")
    if not manifest.get("coverage_status_counts"):
        raise ValueError("Missing coverage status audit in source manifest")


def build(acceleration_rows):
    """Return one audited role/window research row per player, including exclusions."""
    groups = defaultdict(dict)
    for row in acceleration_rows:
        role = row.get("role", "")
        window = row.get("window", "")
        if role not in PAIRINGS or window not in WINDOW_SIZES:
            continue
        key = (row.get("team", ""), row.get("player_id", ""), role, window)
        if not key[0] or not key[1]:
            raise ValueError(f"Missing player identity: {key}")
        metric = row.get("metric", "")
        if metric in groups[key]:
            raise ValueError(f"Duplicate acceleration metric: {key}, {metric}")
        groups[key][metric] = row

    output = []
    for (team, player_id, role, window), metrics in sorted(groups.items()):
        volume_metric, production_metric, min_baseline, min_delta, min_pct = PAIRINGS[role]
        vol, prod = metrics.get(volume_metric), metrics.get(production_metric)
        basis = vol or prod or next(iter(metrics.values()))
        result = dict.fromkeys(FIELDS, "")
        result.update({
            "season": SEASON, "team": team, "player_id": player_id,
            "player_name": basis.get("player_name", ""), "role": role,
            "window": window, "opportunity_metric": volume_metric,
            "production_metric": production_metric,
            "divergence_signal": "NOT_EVALUATED", "verified_research_ready": "NO",
            "verified_game_completeness": basis.get("verified_game_completeness", "NOT_VERIFIED"),
            "source_game_statuses": basis.get("source_game_statuses", "UNKNOWN"),
        })

        if not vol or not prod:
            result.update(analysis_status="MISSING_REQUIRED_METRIC", explanation="Volume or production metric absent from input")
            output.append(result)
            continue

        structural_fields = (
            "current_game_ids", "previous_game_ids", "current_observed_games",
            "previous_observed_games", "missing_current_game_ids",
            "missing_previous_game_ids", "source_game_statuses",
            "verified_game_completeness",
        )
        if any(vol.get(field, "") != prod.get(field, "") for field in structural_fields):
            raise ValueError(f"Inconsistent volume/production windows: {team}/{player_id}/{role}/{window}")
        current_ids = parse_game_ids(vol.get("current_game_ids"))
        previous_ids = parse_game_ids(vol.get("previous_game_ids"))
        n = WINDOW_SIZES[window]
        if len(current_ids) != n or len(previous_ids) != n or set(current_ids) & set(previous_ids):
            raise ValueError(f"Overlapping or invalid windows: {team}/{player_id}/{window}")
        for field in structural_fields[:6]:
            result[field] = vol.get(field, "")

        observed_current = number(vol.get("current_observed_games"))
        observed_previous = number(vol.get("previous_observed_games"))
        complete = (
            observed_current == n and observed_previous == n
            and not vol.get("missing_current_game_ids")
            and not vol.get("missing_previous_game_ids")
            and vol.get("trend_status") != "INCOMPLETE_PLAYER_GAME_COVERAGE"
            and prod.get("trend_status") != "INCOMPLETE_PLAYER_GAME_COVERAGE"
        )
        if not complete:
            result.update(analysis_status="INCOMPLETE_PLAYER_GAME_COVERAGE", explanation="A missing player-game observation must not be interpreted as zero")
            output.append(result)
            continue

        vc = number(vol.get("current_per_team_game"))
        vp = number(vol.get("previous_per_team_game"))
        pc = number(prod.get("current_per_team_game"))
        pp = number(prod.get("previous_per_team_game"))
        if None in (vc, vp, pc, pp):
            result.update(analysis_status="MISSING_NUMERIC_VALUE", explanation="A full observed window lacks an input average")
            output.append(result)
            continue
        if min(vc, vp) < 0:
            raise ValueError(f"Negative opportunity count: {team}/{player_id}/{role}/{window}")

        result.update({
            "current_opportunities_per_team_game": rounded(vc),
            "previous_opportunities_per_team_game": rounded(vp),
            "opportunity_delta": rounded(vc - vp),
            "opportunity_percent_change": rounded(percent(vc, vp), 2),
            "current_production_per_team_game": rounded(pc),
            "previous_production_per_team_game": rounded(pp),
            "production_delta": rounded(pc - pp),
            "production_percent_change": rounded(percent(pc, pp), 2),
        })
        if vp <= 0 or vc <= 0:
            result.update(analysis_status="ZERO_OPPORTUNITY_BASELINE", explanation="Zero workload baseline cannot establish reliable relative momentum")
            output.append(result)
            continue
        if min(vp, vc) < min_baseline:
            result.update(analysis_status="LOW_VOLUME_SAMPLE", explanation=f"Both window averages must reach {min_baseline:g} opportunities per team game")
            output.append(result)
            continue

        rate_c, rate_p = pc / vc, pp / vp
        result.update({
            "current_yards_per_opportunity": rounded(rate_c),
            "previous_yards_per_opportunity": rounded(rate_p),
            "yards_per_opportunity_percent_change": rounded(percent(rate_c, rate_p), 2),
        })
        vol_change = percent(vc, vp) / 100.0
        prod_change = percent(pc, pp)
        rate_change = percent(rate_c, rate_p)
        rising = vol_change >= min_pct and vc - vp >= min_delta
        falling = vol_change <= -min_pct and vp - vc >= min_delta
        # When the old production average is zero, absolute yards can still be
        # compared; do not invent a percentage for the production baseline.
        production_lags = pc - pp <= max(0.0, abs(pp) * 0.05)
        production_rises = pc - pp >= max(1.0, abs(pp) * 0.10)
        signal = "NO_QUALIFYING_DIVERGENCE"
        detail = "No divergence crossed the prespecified volume and production thresholds"
        if rising and production_lags:
            signal = "OPPORTUNITY_UP_PRODUCTION_LAG"
            detail = "Workload increased materially while yards per team game were flat or declined"
        elif falling and production_rises:
            signal = "OPPORTUNITY_DOWN_PRODUCTION_UP"
            detail = "Production improved despite fewer opportunities; review efficiency sustainability"
        elif rising and rate_change is not None and rate_change <= -15:
            signal = "OPPORTUNITY_UP_EFFICIENCY_DOWN"
            detail = "Workload increased while yards gained per opportunity fell at least 15%"
        elif not rising and not falling and prod_change is not None and rate_change is not None and prod_change >= 25 and rate_change >= 20:
            signal = "PRODUCTION_UP_WITHOUT_VOLUME_GROWTH"
            detail = "Yards and efficiency surged without a comparably material workload rise"
        result.update({
            "divergence_signal": signal,
            "analysis_status": "PROVISIONAL_SOURCE_VALIDATION" if result["verified_game_completeness"] != "VERIFIED" else "COMPUTED_REVIEW_REQUIRED",
            "explanation": detail,
        })
        # Downstream betting research must not auto-promote a signal based solely
        # on this numerical classification, even if later inputs are validated.
        output.append(result)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    base = "NFL_LOCKBOX_2026/advanced_analytics"
    parser.add_argument("--acceleration", default=f"{base}/opportunity_acceleration_2026.csv")
    parser.add_argument("--manifest", default=f"{base}/opportunity_acceleration_source_manifest_2026.json")
    parser.add_argument("--output-dir", default=base)
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    validate_manifest(manifest)
    rows = build(read_csv(args.acceleration))
    if not rows:
        raise SystemExit("No comparable player-window research rows; no output generated")
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / "opportunity_production_divergence_2026.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    audit = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "season": SEASON,
        "input_acceleration": args.acceleration,
        "input_manifest": args.manifest,
        "upstream_run_id": manifest["upstream_run_id"],
        "upstream_head_sha": manifest["upstream_head_sha"],
        "latest_source_game_date": manifest["latest_usage_game_date"],
        "source_coverage_status_counts": manifest["coverage_status_counts"],
        "player_role_window_rows": len(rows),
        "signal_counts": dict(Counter(r["divergence_signal"] for r in rows)),
        "analysis_status_counts": dict(Counter(r["analysis_status"] for r in rows)),
        "confirmed_prop_recommendations": 0,
        "limitations": [
            "Uses fresh upstream artifact, never committed root CSV snapshots",
            "Non-overlapping team-game windows inherited from acceleration source",
            "Missing player-games remain unknown; zeros are never imputed",
            "Game completeness and participation require independent validation",
            "No touchdown or route/snaps divergence inferred from unavailable fields",
            "Signals are research hypotheses, not bets or probability forecasts",
        ],
    }
    (out / "opportunity_production_divergence_audit_2026.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"csv": str(csv_path), **audit}, indent=2))


if __name__ == "__main__":
    main()
