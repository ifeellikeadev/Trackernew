"""Consolidated main implementation. See INSTALL.txt for migration details."""
from __future__ import annotations

# ========================================================================
# main: consolidated implementation
# ========================================================================
"""Synchronous Munich-region pipeline with auditable filtering and confirmed-location results only."""
import logging,re,sys,time
from collections import Counter
from pathlib import Path
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from src.scrapers import scrape_company, fetch_job_details, reset_headless_budget
from src.diagnostics import source_budget
from src.matching import title_decision, location_decision, score_jobs, related_role_candidate, role_decision
from src.matching import filter_recent_jobs, parse_posted, date_decision
from src.extraction import usable_title, dedupe, is_vacancy_url
from src.tracker import update_tracker
from src.website import generate as generate_html
from src.diagnostics import observe, new_row, finish, save_report, append_to_page
logging.basicConfig(level=logging.INFO,format='%(asctime)s [%(levelname)s] %(message)s')
logger=logging.getLogger('job_scraper.main')
ROOT=Path(__file__).resolve().parent.parent
COMPANIES_FILE=ROOT/'config/companies.yaml'
JOB_BOARDS_FILE=ROOT/'config/job_boards.yaml'
CV_PROFILE_FILE=ROOT/'config/cv_profile.yaml'
TRACKER_FILE=ROOT/'data/job_tracker.xlsx'
HEALTH_FILE=ROOT/'docs/scraper_health.json'
DATE_FIELDS=('_date_raw','_date_field','_date_kind','_date_source','_date_observed_on')

def load_yaml(path):
    if not path.exists():return {}
    return yaml.safe_load(path.read_text(encoding='utf-8')) or {}

def excluded(company):
    name=re.sub('[^a-z0-9]','',company.get('name','').lower());url=company.get('careers_url','').lower()
    return any(v in name or v in url for v in ('randstad','michaelpage','michalpage')) or company.get('city','Munich')!='Munich'

def enrich(job,details):
    for field in ('location','description'):
        if details.get(field) and (not job.get(field) or (field=='location' and location_decision(job)['status']=='unconfirmed')):
            job[field]=details[field]
            if field=='location':job['_location_source']=details.get('_location_source','detail_explicit_location')
    if details.get('posted_date') and (not job.get('posted_date') or job.get('_date_kind')=='creation_unverified'):
        job['posted_date']=details['posted_date']
        for field in DATE_FIELDS:
            job.pop(field,None)
            if field in details:job[field]=details[field]

def audit_date(row,job,stage):
    decision=date_decision(job)
    row['date_decisions'][decision['reason']]=row['date_decisions'].get(decision['reason'],0)+1
    example={'title':job.get('title',''),'url':job.get('url',''),'stage':stage,**decision}
    target='date_rejected_samples' if not decision['keep'] else 'date_kept_samples'
    limit=20 if not decision['keep'] else 8
    # Kept-date samples are stratified by reason so missing/uncertain dates
    # cannot be hidden behind the first eight ordinary publication dates.
    used=len(row[target]) if not decision['keep'] else sum(x['reason']==decision['reason'] for x in row[target])
    if used<limit:row[target].append(example)
    return decision

def scrape_all_any_city(companies,cv_profile):
    jobs=[];reports=[];counts={'ok':0,'empty':0,'errored':0,'total':0,'title_matched':0,'location_confirmed':0}
    detail_cache={}
    def cached_details(url,row):
        if not url:return {},''
        if url not in detail_cache:
            if budget.remaining()<=0:
                budget.expired=True;budget.detail_skipped+=1
                return {},''
            row['detail_fetches']+=1
            try:detail_cache[url]=budget.call(fetch_job_details,url,default=({},''),phase='job_detail')
            except Exception as exc:
                row['issues'].append('Detail extraction failed: '+str(url)+' | '+str(exc)[:180]);detail_cache[url]=({},'')
        return detail_cache[url]
    for company in companies:
        if excluded(company):continue
        counts['total']+=1;row=new_row(company);start=time.monotonic();error=None
        row.update(location_unconfirmed=0,non_job_links_removed=0,duplicate_candidates_removed=0,date_decisions={},date_rejected_samples=[],date_kept_samples=[],location_rejected_samples=[],title_rejection_details=[])
        try:
            with source_budget() as budget, observe(row):
                extracted=budget.call(scrape_company,company,default=None,phase='listing')
                if extracted is None:extracted=budget.candidates
                budget.remember(extracted)
                valid=[j for j in extracted if is_vacancy_url(j.get('url',''))]
                row['non_job_links_removed']=len(extracted)-len(valid)
                raw=dedupe(valid);row['duplicate_candidates_removed']=len(valid)-len(raw);row['raw_candidates']=len(raw)
                row['pagination']=next((j['_pagination'] for j in raw if j.get('_pagination')), {})
                row['title_recovered']=0;row['title_unresolved']=0;recovery_budget=60
                for candidate in raw:
                    if usable_title(candidate.get('title','')) and not candidate.get('_needs_title'):continue
                    if recovery_budget<=0:row['title_unresolved']+=1;continue
                    recovery_budget-=1
                    details,_=cached_details(candidate.get('_detail_api_url') or candidate.get('url',''),row)
                    if usable_title(details.get('title','')):
                        candidate['title']=details['title'];enrich(candidate,details);candidate.pop('_needs_title',None);row['title_recovered']+=1
                    else:row['title_unresolved']+=1
                row['unresolved_samples']=[{'url':j.get('url',''),'label':j.get('title','')} for j in raw if j.get('_needs_title') or not usable_title(j.get('title',''))][:8]
                row['sample_titles']=[str(j.get('title',''))[:160] for j in raw[:8]]
                row['unverified_candidates']=sum(str(j.get('_extraction','')).endswith('_unverified') for j in raw)
                row['methods']=sorted({j.get('_extraction','existing_ats_adapter') for j in raw})
                recent=[]
                for job in raw:
                    if audit_date(row,job,'list')['keep']:recent.extend(filter_recent_jobs([job]))
                    else:row['date_rejected']+=1
                readable=[j for j in recent if usable_title(j.get('title','')) and not j.get('_needs_title')]
                row['extraction_unresolved']=len(recent)-len(readable)
                titled=[]
                for job in readable:
                    if related_role_candidate(job['title'],cv_profile) and location_decision(job)['status'] not in ('outside','excluded') and not job.get('description'):
                        details,body=cached_details(job.get('_detail_api_url') or job.get('url',''),row)
                        enrich(job,details)
                        if not job.get('description') and body:job['description']=body
                        row['related_detail_fetch_attempts']=row.get('related_detail_fetch_attempts',0)+1
                    decision=role_decision(job,cv_profile)
                    if decision['keep']:
                        job['match_type']=decision['match_type'];job['match_reason']=decision['match_reason']
                        if decision['match_type']=='Related role':row['related_roles_qualified']=row.get('related_roles_qualified',0)+1
                    if decision['keep']:titled.append(job)
                    else:
                        row['title_rejected']+=1
                        if len(row['title_rejection_details'])<20:row['title_rejection_details'].append({'title':job['title'],'url':job['url'],**decision})
                row['title_rejected_samples']=[j['title'] for j in row['title_rejection_details'][:8]]
                counts['title_matched']+=len(titled);confirmed=[]
                for job in titled:
                    loc_before=location_decision(job)
                    # Known outside/excluded locations do not need expensive details.
                    if loc_before['status'] not in ('outside','excluded') and job.get('url') and (loc_before['status']=='unconfirmed' or not job.get('description') or not job.get('posted_date')):
                        details,_=cached_details(job.get('_detail_api_url') or job['url'],row);enrich(job,details)
                    decision=audit_date(row,job,'after_detail')
                    if not decision['keep']:row['date_rejected']+=1;continue
                    job=filter_recent_jobs([job])[0]
                    loc=location_decision(job)
                    if loc['status'] in ('outside','excluded'):
                        row['location_rejected']+=1
                        if len(row['location_rejected_samples'])<20:row['location_rejected_samples'].append({'title':job['title'],'url':job['url'],**loc})
                        continue
                    if loc['status']=='unconfirmed':
                        row['location_unconfirmed']+=1
                        continue
                    job['company']=company['name'];job['city']='Munich';job.pop('matched_city',None);confirmed.append(job)
                counts['location_confirmed']+=len(confirmed);score_jobs(confirmed,cv_profile)
                floor=cv_profile.get('main_min_score',0);eligible=[j for j in confirmed if j.get('relevance_score',0)>=floor]
                row['score_rejected']=len(confirmed)-len(eligible);row['eligible']=len(eligible)
                row['score_rejected_samples']=[{'title':j['title'],'url':j['url'],'score':j.get('relevance_score',0),'floor':floor} for j in confirmed if j not in eligible][:20]
                row['unknown_dates']=sum(parse_posted(j.get('posted_date')) is None for j in eligible)
                jobs.extend(eligible);counts['ok' if raw else 'empty']+=1
        except Exception as exc:
            error=exc;counts['errored']+=1;logger.exception('FAILED %s',company['name'])
        row['runtime_budget']=budget.diagnostics()
        if budget.expired:row['issues'].append('Source runtime budget exhausted; coverage incomplete; partial candidates retained and remaining details skipped.')
        if not budget.enforced:row['issues'].append('Source deadline enforcement unavailable in this runtime; cooperative checks only.')
        row['seconds']=round(time.monotonic()-start,2);finish(row,error);reports.append(row)
        row['funnel_accounted']=sum(row.get(k,0) for k in ('date_rejected','extraction_unresolved','title_rejected','location_rejected','location_unconfirmed','score_rejected','eligible'))
        row['funnel_balanced']=row['funnel_accounted']==row['raw_candidates']
        logger.info('HEALTH %s | %s | raw=%d date=%d unresolved=%d title=%d outside=%d location_review=%d score=%d eligible=%d',company['name'],row['status'],row['raw_candidates'],row['date_rejected'],row['extraction_unresolved'],row['title_rejected'],row['location_rejected'],row['location_unconfirmed'],row['score_rejected'],row['eligible'])
        time.sleep(.3)
    counts['source_statuses']=dict(Counter(row['status'] for row in reports))
    save_report(reports,HEALTH_FILE)
    return jobs,counts,reports

def run():
    profile=load_yaml(CV_PROFILE_FILE);reset_headless_budget()
    companies=load_yaml(COMPANIES_FILE).get('companies',[])+load_yaml(JOB_BOARDS_FILE).get('companies',[])
    jobs,counts,reports=scrape_all_any_city(companies,profile)
    if not reports or not any(row.get('raw_candidates',0)>row.get('extraction_unresolved',0) for row in reports) or all(row['status'] in ('ERROR','FAILED_OR_BLOCKED','EMPTY_UNVERIFIED','EXTRACTION_INCOMPLETE') for row in reports):
        raise RuntimeError('No usable source results; existing tracker and webpage retained.')
    summary=update_tracker(TRACKER_FILE,jobs,min_score=profile.get('main_min_score',0),related_roles_enabled=(profile.get('related_roles') or {}).get('enabled') is True)
    generate_html();append_to_page(reports,ROOT/'docs/index.html')
    logger.info('Run counts: %s | Tracker: %s',counts,summary)
if __name__=='__main__':run()
