"""Test an employer and optionally promote only after quality acceptance."""
import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
import yaml
from src.scrapers import discover_real_careers_url, platform_from_url, scrape_company
from src.diagnostics import source_budget
from src.source_quality import quality, valid_url

ROOT = Path(__file__).resolve().parent.parent

def propose(name, url, priority=2, sectors=None, seconds=90, configured=None):
    if not str(name).strip() or not valid_url(url):
        raise ValueError('A company name and an HTTP(S) careers URL are required')
    if priority not in (1, 2, 3): raise ValueError('priority must be 1, 2 or 3')
    entry = dict(name=name.strip(), city='Munich', careers_url=url, ats='auto', board_token='',
                 enabled=True, priority=priority, sector=sectors or ['general'],
                 expected_locations=['Munich', 'Muenchen', 'München'], max_pages=20,
                 detail_fetch_mode='matched_only', verification_status='pending')
    if configured:
        entry={**configured,**entry,'ats':configured.get('ats','auto'),'board_token':configured.get('board_token',''),'max_pages':configured.get('max_pages',20),'expected_locations':configured.get('expected_locations',entry['expected_locations'])}
        entry.pop('verified_on',None)
    with source_budget(seconds) as budget:
        def discover():
            direct = platform_from_url(url)
            return direct if direct[0] else discover_real_careers_url(url)
        detected = budget.call(discover, default=(None, None, None), phase='discovery')
        final, platform, token = detected or (None, None, None)
        entry.update(careers_url=final or url, ats=platform or entry['ats'], board_token=token or entry['board_token'])
        jobs = budget.call(scrape_company, entry, default=None, phase='listing')
        if jobs is None: jobs = budget.candidates
        result = quality(jobs, expected_locations=entry['expected_locations'])
        if budget.expired:
            result['passes'] = False
            result['reasons'].append('source_budget_exhausted')
    if result['passes']:
        entry['verified_on'] = datetime.now(timezone.utc).date().isoformat()
        entry['verification_status'] = 'passed'
    return entry, result

def promote(entry, result, config):
    if not result.get('passes'): raise ValueError('Cannot promote a source that failed quality checks')
    path = Path(config)
    data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    companies = data.setdefault('companies', [])
    key = lambda value: re.sub(r'[^\w]', '', value.casefold())
    for index, company in enumerate(companies):
        if key(company['name']) == key(entry['name']):
            companies[index] = {**company, **entry}
            break
    else: companies.append(entry)
    backup = path.with_suffix(path.suffix + '.bak')
    backup.write_bytes(path.read_bytes())
    temporary = path.with_suffix('.tmp')
    temporary.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding='utf-8')
    temporary.replace(path)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('name'); ap.add_argument('url')
    ap.add_argument('--priority', type=int, choices=[1, 2, 3], default=2)
    ap.add_argument('--sector', action='append'); ap.add_argument('--seconds', type=int, default=90)
    ap.add_argument('--promote', action='store_true')
    ap.add_argument('--config', type=Path, default=ROOT/'config/companies.yaml')
    args = ap.parse_args()
    if not 1 <= args.seconds <= 600: ap.error('--seconds must be between 1 and 600')
    entry, result = propose(args.name, args.url, args.priority, args.sector, args.seconds)
    print(yaml.safe_dump({'company': entry, 'quality': result}, sort_keys=False, allow_unicode=True))
    if args.promote and result['passes']: promote(entry, result, args.config)
    if not result['passes']: raise SystemExit(2)

if __name__ == '__main__': main()
