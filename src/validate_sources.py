"""Live extraction-quality checks; no configuration mutation by default."""
import argparse
import json
from pathlib import Path
import yaml
from src.onboard_company import propose, promote, ROOT

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--company', action='append', help='Exact company name; repeat to select several')
    ap.add_argument('--pending', action='store_true')
    ap.add_argument('--expansion', action='store_true', help='Only the 19 newly added candidate employers')
    ap.add_argument('--promote', action='store_true')
    ap.add_argument('--seconds', type=int, default=90)
    ap.add_argument('--config', type=Path, default=ROOT/'config/companies.yaml')
    ap.add_argument('--output', type=Path, default=ROOT/'docs/source_validation.json')
    args = ap.parse_args()
    if not 1 <= args.seconds <= 600: ap.error('--seconds must be between 1 and 600')
    companies = (yaml.safe_load(args.config.read_text(encoding='utf-8')) or {}).get('companies', [])
    names = {n.casefold() for n in args.company or []}
    selected = [c for c in companies if (not names or c['name'].casefold() in names) and (not args.pending or c.get('verification_status') != 'passed') and (not args.expansion or c.get('source_origin')=='expansion_candidate')]
    if not selected: ap.error('No companies match the selection')
    results = []
    for company in selected:
        try:
            entry, result = propose(company['name'], company['careers_url'], company.get('priority', 2), company.get('sector'), args.seconds, configured=company)
            if args.promote and result['passes']: promote(entry, result, args.config)
            results.append({'name': company['name'], 'company': entry, 'quality': result})
        except Exception as exc:
            results.append({'name': company['name'], 'quality': {'passes': False, 'reasons': [str(exc)]}})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'Validated {len(results)} sources; passed {sum(r["quality"]["passes"] for r in results)}; report: {args.output}')
    if not all(r['quality']['passes'] for r in results): raise SystemExit(2)

if __name__ == '__main__': main()
