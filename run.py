"""Cross-platform entry point; run from the extracted package root."""
import argparse
import os
from src.main import run, load_yaml, COMPANIES_FILE
from src.runtime import selected, source_seconds

def main():
    parser=argparse.ArgumentParser(description='Munich job scraper. Full coverage is the default.')
    parser.add_argument('--tier',choices=['all','daily','2','scheduled'])
    parser.add_argument('--companies',nargs='+',help='Exact company names, quoted when containing spaces')
    parser.add_argument('--check-config',action='store_true',help='Validate configuration without network requests')
    args=parser.parse_args()
    if args.tier:os.environ['SCRAPE_TIER']=args.tier
    if args.companies:os.environ['SCRAPE_COMPANIES']=','.join(args.companies)
    if args.check_config:
        config=load_yaml(COMPANIES_FILE);companies=config.get('companies',[])
        if not companies:parser.error('No companies configured')
        for company in companies:
            selected(company,config.get('scrape_schedule'))
            source_seconds(company,config.get('scrape_schedule'))
            if not company.get('careers_url'):parser.error('Missing careers URL: '+company['name'])
        print(f'Configuration OK: {len(companies)} companies; mode {os.getenv("SCRAPE_TIER","all")}')
        return
    run()

if __name__=='__main__':main()
