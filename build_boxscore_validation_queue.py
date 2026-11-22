
#!/usr/bin/env python3
"""NFL LOCKBOX Step 5A: provenance-gated game validation work queue.

Ranks games by how many complete-observation, still-unvalidated walk-forward
replays depend on them. Joins existing PBP quality and player participation
reconciliation as investigation aids. These are NOT independent box scores:
no game is certified or given BOX_SCORE_VALIDATED by this script.
"""

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from build_veteran_resurgence import validate_source_metadata

EXPECTED_SEASON = '2026'
VALIDATED = 'BOX_SCORE_VALIDATED'
COLUMNS = [
    'rank', 'game_id', 'date', 'teams', 'source_coverage_status',
    'external_box_score_evidence', 'validation_readiness',
    'complete_replays_depending_on_game', 'incomplete_replays_touching_game',
    'pbp_rows', 'end_game_markers', 'duplicate_play_keys',
    'unidentified_receiver_pass_attempts', 'reconciled_player_rows',
    'unresolved_participation_rows', 'positive_snaps_no_identified_event',
    'reconciliation_zero_snaps_not_inactive_proof', 'data_consistency',
    'quality_warnings', 'next_action',
]


def load_csv(path):
    with Path(path).open(newline='', encoding='utf-8-sig') as handle:
        return list(csv.DictReader(handle))


def integer(value, label):
    try:
        text = str(value).strip()
        if not text or not text.lstrip('-').isdigit():
            raise ValueError()
        number = int(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'Invalid integer {label}: {value!r}') from exc
    if number < 0:
        raise ValueError(f'Negative integer {label}: {value!r}')
    return number


def game_ids(text):
    ids = [item.strip() for item in str(text or '').split('|') if item.strip()]
    if len(ids) != len(set(ids)):
        raise ValueError(f'Duplicate game ID in replay window: {text!r}')
    return ids


def build(coverage, quality, participation, replay):
    games = {}
    for row in coverage:
        game = row['game_id']
        if not game or game in games:
            raise ValueError(f'Missing/duplicate game in coverage: {game!r}')
        if not row['date'].startswith(EXPECTED_SEASON + '-'):
            raise ValueError(f'Invalid game season: {game}')
        teams = [x.strip() for x in row['teams'].split(',') if x.strip()]
        if len(teams) != 2 or len(set(teams)) != 2:
            raise ValueError(f'Invalid teams for {game}: {teams}')
        games[game] = row

    quality_by_game = {}
    for row in quality:
        gid = row['game_id']
        if gid not in games or gid in quality_by_game:
            raise ValueError(f'Unknown/duplicate quality game: {gid}')
        quality_by_game[gid] = row
    if not games:
        raise ValueError('No source games available')

    counts = defaultdict(Counter)
    seen_players = set()
    for row in participation:
        gid, team, player_id = (
            row.get(k, '') for k in ('game_id', 'team', 'player_id')
        )
        if gid not in games or team not in [
            x.strip() for x in games[gid]['teams'].split(',')
        ]:
            raise ValueError(
                f'Participation references unknown game/team: {gid}/{team}'
            )
        key = gid, team, player_id
        if not player_id or key in seen_players:
            raise ValueError(f'Missing/duplicate participation identity: {key}')
        seen_players.add(key)
        counts[gid]['total'] += 1
        status = row.get('reconciled_status', '')
        if status == 'UNRESOLVED_NO_SNAP_MATCH':
            counts[gid]['unresolved'] += 1
        elif status == 'CONFIRMED_OFFENSIVE_SNAPS_NO_IDENTIFIED_EVENT':
            counts[gid]['positive_snap_no_event'] += 1
        elif status == 'MATCHED_ZERO_OFFENSIVE_SNAPS_NOT_INACTIVE_PROOF':
            counts[gid]['zero_snaps_not_inactive'] += 1
        elif status != 'OBSERVED_OFFENSIVE_EVENT':
            raise ValueError(f'Unexpected participation state: {status!r}')

    replay_dependency = Counter()
    incomplete_dependency = Counter()
    seen_replays = set()
    for row in replay:
        replay_id = tuple(row.get(x, '') for x in (
            'team', 'player_id', 'role', 'asof_game', 'heldout_game'
        ))
        if replay_id in seen_replays or any(not x for x in replay_id):
            raise ValueError(f'Duplicate/incomplete replay key: {replay_id}')
        seen_replays.add(replay_id)
        history = (
            game_ids(row.get('previous_three_games')) +
            game_ids(row.get('recent_three_games'))
        )
        heldout = row.get('heldout_game', '')
        if len(history) != 6 or len(set(history + [heldout])) != 7:
            raise ValueError(
                f'Invalid six-pregame/one-heldout window: {replay_id}'
            )
        involved = history + [heldout]
        if any(g not in games for g in involved):
            raise ValueError(
                f'Replay references unknown source game: {replay_id}'
            )
        status = row.get('backtest_status', '')
        if status == 'PROVISIONAL_UNVALIDATED_GAMES':
            if row.get('research_eligible') != 'NO':
                raise ValueError(
                    f'Provisional replay wrongly marked eligible: {replay_id}'
                )
            replay_dependency.update(involved)
        elif status == 'INCOMPLETE_PLAYER_GAME_COVERAGE':
            incomplete_dependency.update(involved)
        elif status in ('ELIGIBLE_VALIDATED_REPLAY', 'MISSING_METRIC_VALUE'):
            pass
        else:
            raise ValueError(
                f'Unexpected walk-forward replay status: {status}'
            )

    output = []
    for gid, game in games.items():
        q = quality_by_game.get(gid)
        warnings = []
        if q is None:
            warnings.append('QUALITY_REPORT_MISSING')
        else:
            if q['date'] != game['date']:
                warnings.append('DATE_DISAGREEMENT')
            if {q['home_team'], q['away_team']} != set(
                game['teams'].split(',')
            ):
                warnings.append('TEAM_DISAGREEMENT')
            if integer(q.get('pbp_rows'), 'pbp_rows') != integer(
                game.get('plays'), 'coverage plays'
            ):
                warnings.append('PBP_ROW_DISAGREEMENT')
            if integer(q.get('end_game_markers'), 'end_game_markers') == 0:
                warnings.append('END_MARKER_MISSING')
            if integer(q.get('duplicate_play_keys'), 'duplicate_play_keys') > 0:
                warnings.append('DUPLICATED_PBP_PLAYS')
            if integer(
                q.get('other_pass_attempts_without_receiver_id'),
                'other_pass_attempts_without_receiver_id'
            ) > 0:
                warnings.append('RECEIVER_ID_GAPS')

        n = counts[gid]
        if n['unresolved']:
            warnings.append('PARTICIPATION_UNRESOLVED')
        if n['positive_snap_no_event']:
            warnings.append('POSITIVE_SNAPS_WITHOUT_IDENTIFIED_EVENT')
        if game.get('status') == VALIDATED:
            warnings.append('SOURCE_MARKED_VALIDATED_PROOF_NOT_SUPPLIED')

        row = {
            'rank': 0,
            'game_id': gid,
            'date': game['date'],
            'teams': game['teams'],
            'source_coverage_status': game.get('status', 'UNKNOWN'),
            'external_box_score_evidence': 'NOT_PROVIDED',
            'validation_readiness': 'AWAITING_INDEPENDENT_BOX_SCORE',
            'complete_replays_depending_on_game': replay_dependency[gid],
            'incomplete_replays_touching_game': incomplete_dependency[gid],
            'pbp_rows': q.get('pbp_rows', '') if q else '',
            'end_game_markers': q.get('end_game_markers', '') if q else '',
            'duplicate_play_keys': q.get('duplicate_play_keys', '') if q else '',
            'unidentified_receiver_pass_attempts': (
                q.get('other_pass_attempts_without_receiver_id', '')
                if q else ''
            ),
            'reconciled_player_rows': n['total'],
            'unresolved_participation_rows': n['unresolved'],
            'positive_snaps_no_identified_event': n['positive_snap_no_event'],
            'reconciliation_zero_snaps_not_inactive_proof': (
                n['zero_snaps_not_inactive']
            ),
            'data_consistency': (
                'REVIEW_SOURCE_WARNINGS' if warnings
                else 'NO_FLAGS_FROM_INTERNAL_AUDITS'
            ),
            'quality_warnings': '|'.join(warnings),
            'next_action': (
                'Compare play-by-play totals with independent official box '
                'score and resolve participation/receiver-ID gaps before '
                'marking game validated'
            ),
        }
        output.append(row)

    output.sort(key=lambda r: (
        -r['complete_replays_depending_on_game'],
        -r['unresolved_participation_rows'],
        -integer(
            r['unidentified_receiver_pass_attempts'], 'receiver-ID gaps'
        ) if r['unidentified_receiver_pass_attempts'] != '' else 0,
        r['game_id']
    ))
    for rank, row in enumerate(output, start=1):
        row['rank'] = rank
    return output


def validate_inputs(manifest, walk_audit, usage, coverage, replay):
    if manifest.get('source_type') != 'successful_main_build_artifact':
        raise ValueError(
            'Expected provenance from successful upstream main-build artifact'
        )
    if not manifest.get('upstream_run_id') or not manifest.get(
        'upstream_head_sha'
    ):
        raise ValueError('Missing upstream provenance ID/SHA')

    validate_source_metadata(manifest, usage, coverage)

    for k in ('upstream_run_id', 'upstream_head_sha'):
        if str(walk_audit.get(k)) != str(manifest[k]):
            raise ValueError(
                f'Walk-forward audit and upstream manifest disagree: {k}'
            )
    if integer(
        walk_audit.get('historical_replay_attempts'),
        'historical_replay_attempts'
    ) != len(replay):
        raise ValueError(
            'Walk-forward audit and replay CSV disagree on row count'
        )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for arg in (
        'usage', 'coverage', 'quality', 'participation',
        'walkforward', 'walkforward-audit', 'manifest'
    ):
        p.add_argument('--' + arg, required=True)
    p.add_argument(
        '--output-dir',
        default='NFL_LOCKBOX_2026/advanced_analytics'
    )
    args = p.parse_args()

    usage = load_csv(args.usage)
    coverage = load_csv(args.coverage)
    quality = load_csv(args.quality)
    participation = load_csv(args.participation)
    replay = load_csv(args.walkforward)
    manifest = json.loads(
        Path(args.manifest).read_text(encoding='utf-8')
    )
    walk_audit = json.loads(
        Path(args.walkforward_audit).read_text(encoding='utf-8')
    )

    validate_inputs(manifest, walk_audit, usage, coverage, replay)
    queue = build(coverage, quality, participation, replay)

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    result = out / 'boxscore_validation_queue_2026.csv'

    with result.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(queue)

    audit = {
        'generated_utc': datetime.now(timezone.utc).isoformat(),
        'season': EXPECTED_SEASON,
        'upstream_run_id': manifest['upstream_run_id'],
        'upstream_head_sha': manifest['upstream_head_sha'],
        'source_games': len(queue),
        'complete_unvalidated_replay_dependencies': sum(
            int(x['complete_replays_depending_on_game'])
            for x in queue
        ) // 7,
        'total_dependency_memberships': sum(
            int(x['complete_replays_depending_on_game'])
            for x in queue
        ),
        'game_validation_certifications_issued': 0,
        'source_coverage_status_counts': dict(
            Counter(r['source_coverage_status'] for r in queue)
        ),
        'game_warning_counts': dict(
            Counter(
                w for r in queue
                for w in r['quality_warnings'].split('|') if w
            )
        ),
        'top_10_priority_game_ids': [
            r['game_id'] for r in queue[:10]
        ],
        'warnings': [
            'A rank is replay-dependency priority, not an independent proof of game completeness',
            'Source status BOX_SCORE_VALIDATED is not sufficient without independently traceable evidence',
            'Reconciled snap counts do not prove zero targets or inactive status',
            'Overlapping player-game replay memberships are not independent observations',
            'Receiver-ID gaps and missing player-games remain unresolved until independently investigated',
        ],
    }
    (
        out / 'boxscore_validation_queue_audit_2026.json'
    ).write_text(
        json.dumps(audit, indent=2) + '\n',
        encoding='utf-8'
    )
    print(json.dumps({'csv': str(result), **audit}, indent=2))


if __name__ == '__main__':
    main()
