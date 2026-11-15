#!/usr/bin/env python3
"""Read-only Phase 8B pipeline readiness audit; no player status inference."""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('NFL_LOCKBOX_2026')
SOURCE = ROOT / 'source_data'
EVIDENCE = SOURCE / 'official_inactive_evidence'
OUT = ROOT / 'advanced_analytics'
OUT.mkdir(parents=True, exist_ok=True)

def load_json(path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return {'status': 'UNREADABLE'}

def count_csv(path):
    if not path.is_file():
        return None
    with path.open(newline='', encoding='utf-8-sig') as f:
        return sum(1 for _ in csv.DictReader(f))

collection = load_json(EVIDENCE / 'collection_audit_2026.json')
extraction = load_json(EVIDENCE / 'extraction_audit_2026.json')
validation = load_json(EVIDENCE / 'evidence_validation_audit_2026.json')
staging = load_json(SOURCE / 'official_inactives_staging_audit_2026.json')
reconciliation = load_json(OUT / 'inactive_reconciliation_audit_2026.json')
checks = {
    'collector_audit_present': collection is not None,
    'source_captured': bool(collection and collection.get('status') == 'CAPTURED_UNAUTHENTICATED'),
    'extractor_audit_present': extraction is not None,
    'candidate_extraction_success': bool(extraction and extraction.get('status') in ('EXTRACTED_UNVERIFIED', 'EXTRACTED_WITH_REJECTIONS')),
    'validator_audit_present': validation is not None,
    'internal_evidence_consistent': bool(validation and validation.get('internal_evidence_consistent') is True),
    'staging_audit_present': staging is not None,
    'reconciliation_audit_present': reconciliation is not None,
}
report = {
    'generated_utc': datetime.now(timezone.utc).isoformat(),
    'status': 'SOURCE_EVIDENCE_REVIEW_REQUIRED' if not all(checks.values()) else 'INTERNAL_PIPELINE_PRESENT_NOT_AUTHENTICATED',
    'checks': checks,
    'collector_status': (collection or {}).get('status', 'MISSING'),
    'extractor_status': (extraction or {}).get('status', 'MISSING'),
    'validator_status': (validation or {}).get('status', 'MISSING'),
    'staging_rows': count_csv(SOURCE / 'official_inactives_staging_2026.csv'),
    'candidate_rows': count_csv(EVIDENCE / 'extracted_inactives_preview_2026.csv'),
    'limitations': [
        'No automatic discovery of current NFL inactive report URLs.',
        'A captured NFL.com page and consistent extraction do not independently authenticate player status.',
        'No player-game classifications or research rankings are modified.',
        'Missing official evidence must remain unknown.'
    ]
}
path = OUT / 'official_inactive_pipeline_readiness_2026.json'
path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, indent=2))
