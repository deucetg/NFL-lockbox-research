
#!/usr/bin/env python3
"""NFL LOCKBOX 9I.1: scheduled offense-concept versus defense-vulnerability research.

FTN Data via nflverse, CC BY-SA 4.0. Descriptive, not projections.
"""
import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

WINDOWS = {'SEASON': None, 'L3': 3, 'L5': 5, 'L8': 8}
CONCEPTS = {
    'blitz_charted': ('n_blitzers', 'pass'),
    'motion': ('is_motion', 'all'),
    'play_action': ('is_play_action', 'pass'),
    'rpo': ('is_rpo', 'all'),
    'screen_pass': ('is_screen_pass', 'pass'),
}
COLUMNS = ['game_id','game_date','week','offense','defense','venue','window',
           'concept','scope','offense_games','defense_games',
           'offense_eligible_plays','offense_concept_observed',
           'offense_concept_plays','offense_concept_rate_pct',
           'defense_concept_plays','defense_concept_rate_faced_pct',
           'defense_epa_observed','defense_epa_per_play_allowed',
           'defense_offensive_success_rate_allowed_pct',
           'offense_sample_status','defense_sample_status','research_flag']


def number(value):
    try:
        n = float(value)
        return n if n == n and abs(n) != float('inf') else None
    except (TypeError, ValueError):
        return None


def flag(value):
    n = number(value)
    return int(n) if n in (0, 1) else None


def pct(n, d):
    return round(100 * n / d, 2) if d else None


def load(path, required):
    with path.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f'{path}: missing columns {sorted(missing)}')
        return list(reader)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', default='NFL_LOCKBOX_2026')
    p.add_argument('--min-concept-plays', type=int, default=20)
    p.add_argument('--min-observation-coverage', type=float, default=0.95)
    args = p.parse_args()
    if args.min_concept_plays < 1 or not 0 < args.min_observation_coverage <= 1:
        p.error('Invalid minimum sample or observation coverage')
    root = Path(args.root)
    adv = root / 'advanced_analytics'
    ftn_path = adv / 'ftn_charting_joined_2026.csv'
    vuln_path = adv / 'ftn_defensive_vulnerability_2026.csv'
    schedule_path = adv / 'upcoming_actual_matchups_2026.csv'
    ftn_audit = adv / 'ftn_charting_audit_2026.json'
    vuln_audit = adv / 'ftn_defensive_vulnerability_audit_2026.json'
    schedule_audit = adv / 'schedule_matchup_audit_2026.json'
    for path in (ftn_path, vuln_path, schedule_path, ftn_audit, vuln_audit, schedule_audit):
        if not path.is_file():
            raise SystemExit(f'Missing input: {path}')
    for path in (ftn_audit, vuln_audit):
        if json.loads(path.read_text()).get('status') != 'PASS':
            raise SystemExit(f'Source audit not PASS: {path}')
    charted = load(ftn_path, {'game_id','play_id','game_date','offense','defense',
                             'qtr','pass_attempt','rush_attempt'} |
                   {name for name, _ in CONCEPTS.values()})
    vulnerability = load(vuln_path, {'defense','window','concept','games_in_sample',
                                    'concept_true_plays','concept_rate_pct',
                                    'epa_observed_plays','epa_per_play_allowed',
                                    'offensive_success_rate_allowed_pct','data_status'})
    scheduled = load(schedule_path, {'game_id','game_date','week','offense',
                                    'defense','venue'})
    quality = Counter()
    grouped = defaultdict(list)
    seen = set()
    for row in charted:
        quality['charted_rows'] += 1
        key = (row['game_id'], row['play_id'])
        if key in seen:
            raise SystemExit(f'Duplicate FTN play: {key}')
        seen.add(key)
        if not row['game_id'].startswith('2026_') or number(row['qtr']) not in (1,2,3,4):
            continue
        passing, rushing = flag(row['pass_attempt']), flag(row['rush_attempt'])
        if (passing, rushing) not in ((1,0),(0,1)):
            continue
        if not row['offense'] or not row['defense'] or row['offense'] == row['defense']:
            continue
        row['_pass'] = passing == 1
        grouped[(row['offense'], row['game_id'])].append(row)
        quality['offense_eligible_plays'] += 1
    by_team = defaultdict(list)
    for (team, game_id), plays in grouped.items():
        dates = {r['game_date'] for r in plays}
        if len(dates) != 1:
            raise SystemExit(f'Conflicting game dates: {game_id}')
        by_team[team].append((next(iter(dates)), game_id, plays))
    tendencies = {}
    for team, games in by_team.items():
        games.sort(key=lambda x: (x[0], x[1]))
        for window, count in WINDOWS.items():
            chosen = games[-count:] if count else games
            plays = [r for _, _, group in chosen for r in group]
            for concept, (field, scope) in CONCEPTS.items():
                eligible = [r for r in plays if scope == 'all' or r['_pass']]
                observed = []
                for r in eligible:
                    if field == 'n_blitzers':
                        n = number(r.get(field))
                        value = int(n > 0) if n is not None and n >= 0 else None
                    else:
                        value = flag(r.get(field))
                    if value is not None:
                        observed.append(value)
                # Blitzers are defensive behavior faced by an offense, not an offensive play call.
                tendencies[(team, window, concept)] = {
                    'games': len(chosen), 'eligible': len(eligible),
                    'observed': len(observed), 'true': sum(observed),
                    'rate': pct(sum(observed), len(observed)),
                    'coverage': len(observed)/len(eligible) if eligible else 0,
                }
    def_index = {}
    for r in vulnerability:
        key = (r['defense'],r['window'],r['concept'])
        if key in def_index:
            raise SystemExit(f'Duplicate defensive vulnerability: {key}')
        def_index[key] = r
    games = {}
    for r in scheduled:
        key = (r['game_id'],r['offense'],r['defense'])
        if key in games:
            if games[key] != r['game_date']:
                raise SystemExit(f'Conflicting scheduled matchup: {key}')
        games[key] = r['game_date']
    output = []
    for (game_id, offense, defense), game_date in sorted(games.items()):
        sample = next(r for r in scheduled if (r['game_id'],r['offense'],r['defense']) == (game_id,offense,defense))
        for window in WINDOWS:
            for concept, (_, scope) in CONCEPTS.items():
                off = tendencies.get((offense,window,concept))
                d = def_index.get((defense,window,concept))
                if not off or not d:
                    quality['missing_pairs'] += 1
                    continue
                count = number(d['concept_true_plays']) or 0
                epa_n = number(d['epa_observed_plays']) or 0
                off_ok = off['eligible'] > 0 and off['coverage'] >= args.min_observation_coverage
                def_ok = d['data_status'] == 'COMPLETE' and count >= args.min_concept_plays and epa_n >= args.min_concept_plays
                flag_label = 'DESCRIPTIVE_COMPARISON'
                if concept == 'blitz_charted':
                    flag_label = 'DEFENSIVE_BLITZ_FACED_NOT_OFFENSIVE_PLAY_CALL'
                if not off_ok or not def_ok:
                    flag_label = 'SMALL_SAMPLE_OR_MISSING_COVERAGE'
                output.append({
                    'game_id':game_id,'game_date':game_date,'week':sample['week'],
                    'offense':offense,'defense':defense,'venue':sample['venue'],
                    'window':window,'concept':concept,'scope':scope,
                    'offense_games':off['games'],'defense_games':d['games_in_sample'],
                    'offense_eligible_plays':off['eligible'],
                    'offense_concept_observed':off['observed'],
                    'offense_concept_plays':off['true'],
                    'offense_concept_rate_pct':off['rate'],
                    'defense_concept_plays':d['concept_true_plays'],
                    'defense_concept_rate_faced_pct':d['concept_rate_pct'],
                    'defense_epa_observed':d['epa_observed_plays'],
                    'defense_epa_per_play_allowed':d['epa_per_play_allowed'],
                    'defense_offensive_success_rate_allowed_pct':d['offensive_success_rate_allowed_pct'],
                    'offense_sample_status':'COMPLETE' if off_ok else 'LIMITED',
                    'defense_sample_status':'COMPLETE' if def_ok else 'LIMITED',
                    'research_flag':flag_label,
                })
    output_path = adv / 'ftn_scheduled_concept_matchups_2026.csv'
    with output_path.open('w',newline='',encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(output)
    audit = {
        'version':'9i1','created_utc':datetime.now(timezone.utc).isoformat(),
        'status':'PASS' if games and output and not quality['missing_pairs'] else 'REVIEW',
        'scheduled_offense_defense_pairs':len(games),'rows':len(output),
        'offenses_with_ftn_data':len(by_team),
        'small_sample_or_missing_coverage_rows':sum(r['research_flag']=='SMALL_SAMPLE_OR_MISSING_COVERAGE' for r in output),
        'quality':dict(quality),
        'source':'FTN Data via nflverse, CC BY-SA 4.0; existing LOCKBOX schedule and defensive vulnerability outputs',
        'limitations':['Offensive concept frequency is descriptive, not a projection',
                       'Charted blitzers are defense behavior faced by the offense, not an offensive tendency',
                       'EPA and success are outcomes allowed, not proof of causation',
                       'No opponent adjustment, man-zone inference, player availability, odds or prop predictions',
                       'L3/L5/L8 are independent team game windows and not necessarily same calendar games'],
    }
    (adv / 'ftn_scheduled_concept_matchups_audit_2026.json').write_text(json.dumps(audit,indent=2)+'\n')
    print(json.dumps(audit,indent=2))
    if audit['status'] != 'PASS':
        raise SystemExit('Scheduled concept matchup audit requires review')


if __name__ == '__main__':
    main()
