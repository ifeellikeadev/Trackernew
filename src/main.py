"""Munich-region scrape with per-company diagnostics and structured enrichment."""
import logging,re,sys,time
from pathlib import Path
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from src.ats_scrapers import scrape_company,fetch_job_details,reset_headless_budget
from src.matcher import filter_by_title_only,resolve_city_for_job,extract_location_snippet,score_jobs
from src.job_recency import filter_recent_jobs,parse_posted
from src.tracker import update_tracker
from src.generate_html import generate as generate_html
from src.scraper_health import observe,new_row,finish,save_report,append_to_page
logging.basicConfig(level=logging.INFO,format='%(asctime)s [%(levelname)s] %(message)s')
logger=logging.getLogger('job_scraper.main')
ROOT=Path(__file__).resolve().parent.parent
COMPANIES_FILE=ROOT/'config/companies.yaml'
JOB_BOARDS_FILE=ROOT/'config/job_boards.yaml'
CV_PROFILE_FILE=ROOT/'config/cv_profile.yaml'
TRACKER_FILE=ROOT/'data/job_tracker.xlsx'
HEALTH_FILE=ROOT/'docs/scraper_health.json'

def load_yaml(path):
    if not path.exists():return {}
    return yaml.safe_load(path.read_text(encoding='utf-8')) or {}

def excluded(company):
    name=re.sub('[^a-z0-9]','',company.get('name','').lower())
    url=company.get('careers_url','').lower()
    return any(v in name or v in url for v in ('randstad','michaelpage','michalpage')) or company.get('city','Munich')!='Munich'

def scrape_all_any_city(companies,cv_profile):
    jobs=[];reports=[];counts={'ok':0,'empty':0,'errored':0,'total':0,'title_matched':0,'location_confirmed':0}
    detail_cache={}
    for company in companies:
        if excluded(company):continue
        counts['total']+=1;row=new_row(company);start=time.monotonic();error=None
        try:
            with observe(row):
                raw=scrape_company(company);row['raw_candidates']=len(raw)
                row['methods']=sorted({j.get('_extraction','existing_ats_adapter') for j in raw})
                # Reject known old dates before title filtering; undated stay.
                recent=filter_recent_jobs(raw);row['date_rejected']=len(raw)-len(recent)
                titled=filter_by_title_only(recent,cv_profile);row['title_rejected']=len(recent)-len(titled)
                counts['title_matched']+=len(titled)
                confirmed=[]
                for job in titled:
                    text=None
                    # Fetch once per URL only for survivors missing useful fields.
                    if job.get('url') and (not job.get('location') or not job.get('description') or not job.get('posted_date')):
                        url=job['url']
                        if url not in detail_cache:
                            detail_cache[url]=fetch_job_details(url);row['detail_fetches']+=1
                        details,text=detail_cache[url]
                        for field in ('location','description','posted_date'):
                            if not job.get(field) and details.get(field):job[field]=details[field]
                    # Structured detail may supply a previously unknown old date.
                    dated=filter_recent_jobs([job])
                    if not dated:row['date_rejected']+=1;continue
                    job=dated[0]
                    matched=resolve_city_for_job(job,search_text=text if not job.get('location') else None)
                    if matched!='Munich':row['location_rejected']+=1;continue
                    if not job.get('location') and text:job['location']=extract_location_snippet(text,matched)
                    if not job.get('description') and text:job['description']=text
                    job['company']=company['name'];job['city']='Munich';job.pop('matched_city',None)
                    confirmed.append(job)
                counts['location_confirmed']+=len(confirmed)
                score_jobs(confirmed,cv_profile)
                floor=cv_profile.get('main_min_score',0)
                eligible=[j for j in confirmed if j.get('relevance_score',0)>=floor]
                row['score_rejected']=len(confirmed)-len(eligible);row['eligible']=len(eligible)
                row['unknown_dates']=sum(parse_posted(j.get('posted_date')) is None for j in eligible)
                jobs.extend(eligible)
                counts['ok' if raw else 'empty']+=1
        except Exception as exc:
            error=exc;counts['errored']+=1;logger.exception('FAILED %s',company['name'])
        row['seconds']=round(time.monotonic()-start,2);finish(row,error);reports.append(row)
        logger.info('HEALTH %s | %s | raw=%d date=%d title=%d location=%d score=%d eligible=%d',company['name'],row['status'],row['raw_candidates'],row['date_rejected'],row['title_rejected'],row['location_rejected'],row['score_rejected'],row['eligible'])
        time.sleep(.3)
    save_report(reports,HEALTH_FILE)
    return jobs,counts,reports

def run():
    profile=load_yaml(CV_PROFILE_FILE);reset_headless_budget()
    companies=load_yaml(COMPANIES_FILE).get('companies',[])+load_yaml(JOB_BOARDS_FILE).get('companies',[])
    jobs,counts,reports=scrape_all_any_city(companies,profile)
    summary=update_tracker(TRACKER_FILE,jobs,min_score=profile.get('main_min_score',0))
    generate_html();append_to_page(reports,ROOT/'docs/index.html')
    logger.info('Run counts: %s | Tracker: %s',counts,summary)
if __name__=='__main__':run()
