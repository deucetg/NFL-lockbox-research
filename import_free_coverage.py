#!/usr/bin/env python3
"""NFL LOCKBOX: validate free, source-attributed Sharp/PFF coverage research.

No scraping or paid API. Input CSVs are manually curated from publicly accessible
source material. Percentages are never converted into invented snap counts.
"""
import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

TEAM_FIELDS = ['season','team','man_rate','zone_rate','source_name','source_url','source_date','notes']
PLAYER_FIELDS = ['season','player','team','coverage_type','metric','value','unit','sample_size','source_name','source_url','source_date','notes']
PROVIDERS = {'SHARP': 'sharpfootballanalysis.com', 'PFF': 'pff.com'}

def parse_date(value):
    return datetime.strptime(value, '%Y-%m-%d').date().isoformat()

def provider(row):
    name = row['source_name'].strip().upper()
    if name not in PROVIDERS:
        raise ValueError('source_name must be SHARP or PFF')
    url = row['source_url'].strip()
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    domain = PROVIDERS[name]
    if parsed.scheme != 'https' or not (host == domain or host.endswith('.' + domain)):
        raise ValueError(f'{name} requires an HTTPS URL on {domain}')
    return name, url

def percent(value, name):
    v = float(value)
    if not math.isfinite(v) or not 0 <= v <= 100:
        raise ValueError(f'{name} must be between 0 and 100')
    return v

def read_rows(path, required, kind, errors):
    if not path.exists():
        return []
    result = []
    with path.open(newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        missing = set(required) - set(reader.fieldnames or [])
        if missing:
            errors.append(f'{path}: missing columns {sorted(missing)}')
            return []
        for line, row in enumerate(reader, 2):
            try:
                if any(not (row.get(field) or '').strip() for field in required if field != 'notes'):
                    raise ValueError('required field blank')
                source_name, url = provider(row)
                season = int(row['season'])
                if season != 2026:
                    raise ValueError('season must be 2026')
                clean = {key: (row.get(key) or '').strip() for key in required}
                clean.update(source_name=source_name, source_url=url, season=season,
                             source_date=parse_date(row['source_date']))
                if kind == 'team':
                    clean['team'] = clean['team'].upper()
                    if not (2 <= len(clean['team']) <= 3 and clean['team'].isalpha()):
                        raise ValueError('team must be a 2–3 letter abbreviation')
                    clean['man_rate'] = percent(row['man_rate'], 'man_rate')
                    clean['zone_rate'] = percent(row['zone_rate'], 'zone_rate')
                    if clean['man_rate'] + clean['zone_rate'] > 100.1:
                        raise ValueError('man_rate + zone_rate exceeds 100%')
                else:
                    clean['team'] = clean['team'].upper()
                    if clean['coverage_type'].upper() not in ('MAN', 'ZONE'):
                        raise ValueError('coverage_type must be MAN or ZONE')
                    clean['coverage_type'] = clean['coverage_type'].upper()
                    clean['value'] = float(clean['value'])
                    if not math.isfinite(clean['value']):
                        raise ValueError('value must be finite')
                    clean['sample_size'] = int(clean['sample_size'])
                    if clean['sample_size'] < 0:
                        raise ValueError('sample_size cannot be negative')
                clean['verification_status'] = 'SOURCE_RECORDED_NOT_INDEPENDENTLY_VERIFIED'
                result.append(clean)
            except (ValueError, TypeError, OverflowError) as exc:
                errors.append(f'{path}:{line}: {exc}')
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--team-input', type=Path, default=Path('coverage_data/free_team_coverage_2026.csv'))
    parser.add_argument('--player-input', type=Path, default=Path('coverage_data/free_player_coverage_2026.csv'))
    parser.add_argument('--output-dir', type=Path, default=Path('coverage_data'))
    args = parser.parse_args()
    errors = []
    teams = read_rows(args.team_input, TEAM_FIELDS, 'team', errors)
    players = read_rows(args.player_input, PLAYER_FIELDS, 'player', errors)
    seen = set()
    for row in teams:
        key = (row['season'], row['team'], row['source_name'], row['source_date'])
        if key in seen:
            errors.append(f'duplicate team/provider/date: {key}')
        seen.add(key)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    audit = {
        'engine': 'NFL LOCKBOX Free Coverage Intelligence V2',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'status': 'FAILED' if errors else ('PASSED' if teams or players else 'NO_DATA'),
        'team_rows': len(teams), 'player_rows': len(players),
        'source_verified': False,
        'note': 'Schema and source domains validated; individual published figures are NOT independently verified. No scraping performed.',
        'errors': errors,
    }
    (args.output_dir / 'free_coverage_audit_2026.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    if not errors:
        (args.output_dir / 'free_coverage_research_2026.json').write_text(
            json.dumps({'team_coverage': teams, 'player_coverage': players}, indent=2), encoding='utf-8')
    print(json.dumps(audit, indent=2))
    return 1 if errors else 0

if __name__ == '__main__':
    raise SystemExit(main())
