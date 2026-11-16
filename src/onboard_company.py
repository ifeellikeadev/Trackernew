from __future__ import annotations
import argparse,re,sys
from pathlib import Path
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parent))
from scrapers import discover_real_careers_url, platform_from_url, scrape_company

def propose(name,url,priority=2,sectors=None):
    discovered,platform,token=discover_real_careers_url(url)
    final=discovered or url
    if not platform:
        final,platform,token=platform_from_url(final)
    entry=dict(name=name,city='Munich',careers_url=final,ats=platform or 'auto',board_token=token or '',enabled=True,priority=priority,sector=sectors or ['general'],expected_locations=['Munich','Muenchen','München'],max_pages=20,detail_fetch_mode='matched_only')
    jobs=scrape_company(entry)
    usable=sum(bool(j.get('title') and j.get('url')) for j in jobs)
    located=sum(bool(j.get('location')) for j in jobs)
    return entry,dict(raw=len(jobs),usable=usable,with_location=located,passes=bool(jobs and usable/len(jobs)>=.95))

def main():
    ap=argparse.ArgumentParser(description='Detect ATS, test extraction and print a production-ready YAML proposal.')
    ap.add_argument('name');ap.add_argument('url');ap.add_argument('--priority',type=int,default=2);ap.add_argument('--sector',action='append')
    a=ap.parse_args();entry,quality=propose(a.name,a.url,a.priority,a.sector)
    print(yaml.safe_dump({'company':entry,'quality':quality},sort_keys=False,allow_unicode=True))
    if not quality['passes']:raise SystemExit(2)
if __name__=='__main__':main()
