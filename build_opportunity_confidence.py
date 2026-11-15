#!/usr/bin/env python3
"""Additive participation-confidence audit for NFL LOCKBOX rolling windows.

Does not overwrite opportunity totals, change rankings, or infer zero targets/inactives.
"""
import argparse
import csv
import json
from collections import Counter
from pathlib import Path


def read_csv(path):
    with path.open(newline='', encoding='utf-8-sig') as stream:
        return list(csv.DictReader(stream))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='NFL_LOCKBOX_2026')
    args = parser.parse_args()
    root = Path(args.root)
    audit = root / 'participation_audit' / 'player_game_participation_reconciled.csv'
    windows = root / 'player_opportunity_windows_2026.csv'
    if not audit.is_file() or not windows.is_file():
        parser.error(f'Missing inputs: {audit} or {windows}')

    participation = {}
    for row in read_csv(audit):
        key = (row['team'], row['player_id'], row['game_id'])
        if key in participation:
            raise SystemExit(f'Duplicate participation key: {key}')
        participation[key] = row['reconciled_status']

    output = []
    statuses = Counter()
    for row in read_csv(windows):
        ids = [x for x in row.get('unknown_game_ids', '').split(',') if x]
        counts = Counter()
        for game in ids:
            status = participation.get((row['team'], row['player_id'], game), 'NO_PARTICIPATION_AUDIT_ROW')
            counts[status] += 1
        if len(ids) != int(row['unobserved_unknown_games']):
            raise SystemExit(f'Unknown-game count mismatch: {row["team"]}, {row["player_id"]}, {row["window_type"]}')
        unresolved = counts['UNRESOLVED_NO_SNAP_MATCH'] + counts['NO_PARTICIPATION_AUDIT_ROW']
        zero_snaps = counts['MATCHED_ZERO_OFFENSIVE_SNAPS_NOT_INACTIVE_PROOF']
        positive = counts['CONFIRMED_OFFENSIVE_SNAPS_NO_IDENTIFIED_EVENT']
        duplicates = counts['REVIEW_DUPLICATE_IDENTITY_MAPPING']
        status = ('REVIEW_IDENTITY' if duplicates else
                  'NO_UNOBSERVED_GAMES' if not ids else
                  'PARTICIPATION_EVIDENCE_ALL_UNKNOWN_GAMES' if positive == len(ids) else
                  'PARTIAL_PARTICIPATION_EVIDENCE' if positive else
                  'UNKNOWN_PARTICIPATION_OR_ZERO_SNAPS')
        statuses[status] += 1
        output.append({
            'team': row['team'], 'player_id': row['player_id'],
            'player_name': row['player_name'], 'window_type': row['window_type'],
            'team_games_available': row['team_games_available'],
            'original_unobserved_unknown_games': len(ids),
            'confirmed_offensive_snap_games_without_event': positive,
            'matched_zero_offensive_snap_games_not_inactive_proof': zero_snaps,
            'unresolved_no_snap_evidence_games': unresolved,
            'duplicate_identity_review_games': duplicates,
            'participation_confidence_status': status,
            'original_unknown_flag_preserved': int(bool(ids)),
            'unknown_game_ids': row.get('unknown_game_ids', ''),
        })
    outdir = root / 'advanced_analytics'
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / 'player_opportunity_confidence_2026.csv'
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(output[0]) if output else [])
        if output:
            writer.writeheader()
            writer.writerows(output)
    summary = {'window_rows': len(output), 'confidence_status_counts': dict(statuses),
               'method': 'Additive participation evidence for previously unknown game IDs',
               'limitations': ['Does not prove zero targets', 'Does not prove inactive status',
                               'Does not change rolling opportunity totals or ranking scores']}
    (outdir / 'player_opportunity_confidence_audit_2026.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
