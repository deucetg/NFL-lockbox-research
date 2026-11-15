#!/usr/bin/env python3
"""Read-only LOCKBOX participation reconciliation. No inferred inactive statuses."""
import argparse, json
from pathlib import Path
import pandas as pd

def run(source: Path, output: Path):
    output.mkdir(parents=True,exist_ok=True)
    v=pd.read_csv(source/'player_game_validation_2026.csv',dtype={'player_id':str})
    i=pd.read_csv(source/'player_identity_audit.csv',dtype={'gsis_id':str})
    s=i.dropna(subset=['gsis_id'])[['game_id','team','gsis_id','offense_snaps','status']]
    # Duplicate mappings are not silently treated as definitive.
    counts=s.groupby(['game_id','team','gsis_id'],dropna=False).size().rename('mapping_rows').reset_index()
    s=s.sort_values('offense_snaps',ascending=False).drop_duplicates(['game_id','team','gsis_id'])
    s=s.merge(counts,on=['game_id','team','gsis_id'])
    d=v.merge(s,left_on=['game_id','team','player_id'],right_on=['game_id','team','gsis_id'],how='left',validate='many_to_one')
    def classify(r):
        if int(r['offensive_event_evidence'])==1:return 'OBSERVED_OFFENSIVE_EVENT'
        if pd.isna(r['offense_snaps']):return 'UNRESOLVED_NO_SNAP_MATCH'
        if r['mapping_rows']>1:return 'REVIEW_DUPLICATE_IDENTITY_MAPPING'
        if r['offense_snaps']>0:return 'CONFIRMED_OFFENSIVE_SNAPS_NO_IDENTIFIED_EVENT'
        return 'MATCHED_ZERO_OFFENSIVE_SNAPS_NOT_INACTIVE_PROOF'
    d['reconciled_status']=d.apply(classify,axis=1)
    d['zero_target_certainty']='NOT_PROVEN_RECEIVER_ID_GAPS' # Missing receiver IDs prevent absolute zero proof
    d['source_note']='snap count and PBP joined by game_id, team, GSIS player ID'
    d.to_csv(output/'player_game_participation_reconciled.csv',index=False)
    counts=d['reconciled_status'].value_counts().to_dict()
    report={'source_files':['player_game_validation_2026.csv','player_identity_audit.csv'],'total_player_game_rows':len(d),'classification_counts':counts,'limitations':['No official inactive evidence integrated','Positive offensive snaps prove participation but not zero targets','Duplicate identity mappings flagged for review','No original research files modified']}
    (output/'participation_reconciliation_summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--source',default='NFL_LOCKBOX_2026');a.add_argument('--output',default='participation_audit');x=a.parse_args();run(Path(x.source),Path(x.output))
