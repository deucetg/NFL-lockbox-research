
#!/usr/bin/env python3
"""NFL LOCKBOX Step 9G.1 — FTN defensive tendencies (research only).

Input: verified FTN play-level join. No coverage, pressure or EPA inferred.
Attribution: FTN Data via nflverse (CC BY-SA 4.0).
"""
import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

WINDOWS = {'SEASON': None, 'L3': 3, 'L5': 5, 'L8': 8}
TEAMS = set('ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX KC LA LAC LV MIA MIN NE NO NYG NYJ PHI PIT SEA SF TB TEN WAS'.split())
FLAGS = ('is_motion', 'is_play_action', 'is_rpo', 'is_screen_pass', 'is_no_huddle')
COUNTS = ('n_blitzers', 'n_pass_rushers', 'n_defense_box')
REQUIRED = {'game_id', 'play_id', 'game_date', 'week', 'offense', 'defense', 'qtr', 'pass_attempt', 'rush_attempt', *FLAGS, *COUNTS}


def number(value):
    s = str(value or '').strip()
    if not s or s.lower() in ('nan', 'na', 'none', 'null'):
        return None
    try:
        n = float(s)
        return n if n >= 0 and n == n else None
    except ValueError:
        return None


def flag(value):
    n = number(value)
    return int(n) if n in (0, 1) else None


def pct(a, b):
    return round(100 * a / b, 2) if b else None


def mean(values):
    return round(sum(values) / len(values), 3) if values else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', default='NFL_LOCKBOX_2026/advanced_analytics/ftn_charting_joined_2026.csv')
    parser.add_argument('--source-audit', default='NFL_LOCKBOX_2026/advanced_analytics/ftn_charting_audit_2026.json')
    parser.add_argument('--output', default='NFL_LOCKBOX_2026/advanced_analytics')
    args = parser.parse_args()
    src, audit_src, out = Path(args.input), Path(args.source_audit), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    report = {'version': '9g1', 'created_utc': datetime.now(timezone.utc).isoformat(), 'source': str(src), 'attribution': 'FTN Data via nflverse; CC BY-SA 4.0', 'methodology': 'Regulation, regular-season pass_attempt or rush_attempt; defense team-game windows. Blitz is n_blitzers > 0 on pass attempts; no pressure or man-zone inferred.'}
    audit_dest = out / 'ftn_defensive_intelligence_audit_2026.json'
    if not src.exists() or not audit_src.exists():
        report['status'] = 'SOURCE_UNAVAILABLE'
        audit_dest.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
        return
    source_audit = json.loads(audit_src.read_text())
    if source_audit.get('status') != 'PASS':
        raise SystemExit('FTN importer audit must PASS before computing defensive intelligence')
    games = defaultdict(list)
    seen = set()
    quality = Counter()
    with src.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        missing = REQUIRED - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f'Missing FTN join columns: {sorted(missing)}')
        for r in reader:
            quality['all_joined_rows'] += 1
            key = (r['game_id'], r['play_id'])
            if key in seen:
                raise SystemExit(f'Duplicate play key: {key}')
            seen.add(key)
            if not r['game_id'].startswith('2026_') or not r['game_date'].startswith('2026-'):
                quality['excluded_non2026'] += 1
                continue
            defense, offense = r['defense'].strip(), r['offense'].strip()
            if defense not in TEAMS or offense not in TEAMS or defense == offense:
                quality['missing_team_labels' if not defense or not offense else 'invalid_team_labels'] += 1
                continue
            qtr = number(r['qtr'])
            if qtr not in (1, 2, 3, 4):
                quality['excluded_overtime_or_unknown_quarter'] += 1
                continue
            passing, rushing = flag(r['pass_attempt']), flag(r['rush_attempt'])
            if passing != 1 and rushing != 1:
                quality['excluded_non_offensive_plays'] += 1
                continue
            if passing == 1 and rushing == 1:
                quality['ambiguous_pass_rush'] += 1
                continue
            r['_pass'] = passing == 1
            r['_flags'] = {name: flag(r[name]) for name in FLAGS}
            r['_counts'] = {name: number(r[name]) for name in COUNTS}
            games[(defense, r['game_id'])].append(r)
            quality['eligible_plays'] += 1
    by_team = defaultdict(list)
    for (team, game_id), plays in games.items():
        dates = {p['game_date'] for p in plays}
        if len(dates) != 1:
            raise SystemExit(f'Inconsistent game dates: {game_id}')
        by_team[team].append((next(iter(dates)), game_id, plays))
    if not by_team:
        raise SystemExit('No valid defensive team games')
    output_rows = []
    for team in sorted(TEAMS):
        ordered = sorted(by_team.get(team, []), key=lambda x: (x[0], x[1]))
        for window, length in WINDOWS.items():
            selected = ordered[-length:] if length else ordered
            plays = [p for _, _, ps in selected for p in ps]
            pass_plays = [p for p in plays if p['_pass']]
            row = {'defense': team, 'window': window, 'games_in_sample': len(selected), 'last_game_date': selected[-1][0] if selected else '', 'eligible_plays': len(plays), 'pass_plays': len(pass_plays), 'rush_plays': len(plays)-len(pass_plays)}
            for field in FLAGS:
                for label, subset in (('all', plays), ('pass', pass_plays)):
                    values = [p['_flags'][field] for p in subset if p['_flags'][field] is not None]
                    yes = sum(values)
                    prefix = f'{field}_{label}'
                    row[prefix+'_observed'] = len(values)
                    row[prefix+'_true'] = yes
                    row[prefix+'_rate_pct'] = pct(yes, len(values))
            for field in COUNTS:
                subset = pass_plays if field in ('n_blitzers', 'n_pass_rushers') else plays
                values = [p['_counts'][field] for p in subset if p['_counts'][field] is not None]
                row[field+'_observed'] = len(values)
                row[field+'_mean'] = mean(values)
                if field == 'n_blitzers':
                    row['pass_plays_with_blitzers'] = sum(v > 0 for v in values)
                    row['blitz_rate_pct_of_observed_pass_plays'] = pct(row['pass_plays_with_blitzers'], len(values))
            row['data_status'] = 'COMPLETE' if len(selected) and len(pass_plays) and all(row[f+'_observed'] >= .95 * len(pass_plays) for f in ('n_blitzers','n_pass_rushers')) else 'LIMITED'
            output_rows.append(row)
    dest = out / 'ftn_defensive_intelligence_2026.csv'
    with dest.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(output_rows[0]))
        writer.writeheader()
        writer.writerows(output_rows)
    report.update(status='PASS' if not quality['invalid_team_labels'] and not quality['ambiguous_pass_rush'] and len(by_team) == 32 else 'REVIEW', defenses_with_data=len(by_team), defensive_team_games=len(games), output_rows=len(output_rows), quality=dict(quality), source_importer_status=source_audit['status'], output=str(dest), caveats=['These are charted play tendencies, not opponent-adjusted rankings', 'n_blitzers > 0 is not a verified pressure event', 'Motion and play action are offensive actions faced by the defense', 'Missing observations excluded from metric denominators', 'No EPA or success rate calculated in this version'])
    audit_dest.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'team_games': len(games), 'rows': len(output_rows), 'quality': dict(quality)}, indent=2))
    if report['status'] != 'PASS':
        raise SystemExit('FTN defensive intelligence requires audit review')


if __name__ == '__main__':
    main()
