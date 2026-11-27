"""Run the production extraction pipeline without changing tracker files."""
import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse
from unittest.mock import patch

from src import main
from src.scrapers import reset_headless_budget


def split_sources(companies, count):
    groups = {}
    for c in companies:
        host = urlparse(c['careers_url']).hostname
        groups.setdefault(host, []).append(c)
    shards = [[] for _ in range(count)]
    for _, group in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0] or '')):
        min(shards, key=len).extend(group)
    return shards


def run_shard(companies, profile, schedule, output):
    output.mkdir(parents=True, exist_ok=True)
    reset_headless_budget()
    enabled = [c for c in companies if not main.excluded(c) and c.get('enabled', True) is True]
    with TemporaryDirectory(prefix='job-board-audit-') as tmp, patch.object(main, 'ROOT', Path(tmp)), patch.object(main, 'HEALTH_FILE', output.resolve()/'scraper_health.json'):
        _, _, rows = main.scrape_all_any_city(enabled, profile, schedule)
    seen = {r['company'] for r in rows}
    for c in companies:
        if c['name'] not in seen:
            enabled_here = c in enabled
            rows.append(dict(company=c['name'], url=c['careers_url'], status='not_run', diagnosis='UNEXPECTEDLY_SKIPPED' if enabled_here else 'DISABLED_OR_OUT_OF_SCOPE'))
    (output/'audit-rows.json').write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding='utf-8')


def merge(companies, reports, output):
    indexed = {}
    for file in sorted(reports.rglob('audit-rows.json')):
        for row in json.loads(file.read_text(encoding='utf-8')):
            if row['company'] in indexed:
                raise ValueError(f"Duplicate audit result for {row['company']}")
            indexed[row['company']] = row
    output.mkdir(parents=True, exist_ok=True)
    rows = [indexed.get(c['name'], dict(company=c['name'],url=c['careers_url'],status='not_run',diagnosis='SHARD_RESULT_MISSING')) for c in companies]
    fields = ['company','url','diagnosis','status','raw_candidates','eligible','unknown_dates','unscored','missing_both','detail_fetches','seconds','pagination_completion','recommended_action']
    with (output/'company-audit.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer=csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            flat={**r,'pagination_completion':(r.get('pagination') or {}).get('completion','')}
            vals={k:flat.get(k,'') for k in fields}
            writer.writerow({k:("'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v) for k,v in vals.items()})
    missing = sum(r['diagnosis']=='SHARD_RESULT_MISSING' for r in rows)
    summary=dict(configured=len(companies), reported=len(rows)-missing, missing=missing, diagnoses=dict(Counter(r['diagnosis'] for r in rows)))
    (output/'summary.json').write_text(json.dumps(summary,indent=2), encoding='utf-8')
    (output/'audit-rows.json').write_text(json.dumps(rows,indent=2,ensure_ascii=False), encoding='utf-8')
    print(json.dumps(summary,indent=2))
    return missing


def cli():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shards',type=int,default=8)
    parser.add_argument('--shard',type=int,default=0)
    parser.add_argument('--merge',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    cfg=main.load_yaml(main.COMPANIES_FILE)
    companies=cfg['companies']
    if args.merge is not None:
        return 2 if merge(companies,args.merge,args.output) else 0
    if not 1<=args.shards<=16 or not 0<=args.shard<args.shards:
        parser.error('Invalid shard selection')
    profile=main.load_yaml(main.CV_PROFILE_FILE)
    if not profile: parser.error('Missing CV profile')
    run_shard(split_sources(companies,args.shards)[args.shard],profile,cfg.get('scrape_schedule'),args.output)
    return 0


if __name__=='__main__':
    raise SystemExit(cli())
