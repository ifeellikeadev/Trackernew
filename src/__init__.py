"""Bundled tracker modules with focused coverage fixes."""
import importlib.abc as _abc
import importlib.util as _util
import sys as _sys
from pathlib import Path as _Path
_SOURCES = {}


# Module: src/diagnostics.py
_SOURCES['diagnostics'] = r'''"""Consolidated diagnostics implementation. See INSTALL.txt for migration details."""
from __future__ import annotations

# ========================================================================
# source_budget: consolidated implementation
# ========================================================================
"""One synchronous deadline per employer, shared by listing and detail calls.
SIGALRM enforcement targets the Linux main-thread GitHub runner. Other runtimes
use cooperative checks and explicitly report that enforcement is unavailable.
No worker threads, login automation or background delivery.
"""
import contextvars, logging, signal, threading, time
from contextlib import contextmanager
_CURRENT = contextvars.ContextVar('source_budget', default=None)
logger = logging.getLogger('job_scraper.ats')
DEFAULT_SOURCE_SECONDS = 180.0
class SourceDeadline(BaseException):
    """Not swallowed by generic scraper except Exception handlers."""
class SourceBudget:
    def __init__(self, seconds=DEFAULT_SOURCE_SECONDS):
        self.seconds=float(seconds);self.started=time.monotonic();self.deadline=self.started+self.seconds
        self.candidates=[];self.expired=False;self.detail_skipped=0;self.phase=''
        self.enforced=hasattr(signal,'setitimer') and threading.current_thread() is threading.main_thread()
    def remaining(self):return max(0.0,self.deadline-time.monotonic())
    def remember(self,jobs):
        from src.extraction import dedupe
        self.candidates=dedupe(self.candidates+list(jobs))
        return jobs
    def call(self,fn,*args,default=None,phase='operation',**kwargs):
        self.phase=phase
        if self.remaining()<=0:
            self.expired=True
            return default
        if not self.enforced:return fn(*args,**kwargs)
        # Preserve any enclosing alarm; never replace an existing watchdog.
        old_timer=signal.getitimer(signal.ITIMER_REAL)
        if old_timer[0]>0:
            logger.warning('Source deadline alarm unavailable: enclosing timer active')
            self.enforced=False
            return fn(*args,**kwargs)
        old_handler=signal.getsignal(signal.SIGALRM)
        def timeout(signum,frame):
            self.expired=True
            # Bound a stuck finally/browser-close during exception unwinding too.
            signal.setitimer(signal.ITIMER_REAL,2.0)
            raise SourceDeadline()
        signal.signal(signal.SIGALRM,timeout)
        signal.setitimer(signal.ITIMER_REAL,max(.001,self.remaining()))
        try:return fn(*args,**kwargs)
        except SourceDeadline:
            logger.warning('Source runtime budget exhausted during %s; retaining %s checkpointed candidates',phase,len(self.candidates))
            return default
        finally:
            signal.setitimer(signal.ITIMER_REAL,0)
            signal.signal(signal.SIGALRM,old_handler)
    def diagnostics(self):
        return dict(limit_seconds=self.seconds,elapsed_seconds=round(time.monotonic()-self.started,2),
                    expired=self.expired,alarm_enforced=self.enforced,detail_requests_skipped=self.detail_skipped,
                    checkpointed_candidates=len(self.candidates),last_phase=self.phase)
@contextmanager
def source_budget(seconds=DEFAULT_SOURCE_SECONDS):
    budget=SourceBudget(seconds);token=_CURRENT.set(budget)
    try:yield budget
    finally:_CURRENT.reset(token)
def current_budget():return _CURRENT.get()
def checkpoint(jobs):
    budget=current_budget()
    if budget:budget.remember(jobs)
    return jobs
def request_timeout(default=20):
    budget=current_budget()
    if budget and budget.remaining()<=0:raise SourceDeadline()
    return max(.001,min(float(default),budget.remaining())) if budget else default


# ========================================================================
# scraper_health: consolidated implementation
# ========================================================================
"""Per-source diagnostics for the synchronous scrape pipeline."""
import html,json,logging,time
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
import requests

@contextmanager
def observe(row):
    original_get,original_post=requests.get,requests.post
    def wrapper(fn):
        def call(url,*args,**kwargs):
            try:
                response=fn(url,*args,**kwargs)
                row['requests']+=1
                if response.status_code>=400:
                    row['issues'].append('HTTP '+str(response.status_code)+' '+str(url))
                return response
            except requests.RequestException as exc:
                row['requests']+=1
                row['issues'].append(type(exc).__name__+': '+str(exc)[:200])
                raise
        return call
    class Capture(logging.Handler):
        def emit(self,record):
            if record.levelno>=logging.WARNING:
                row['issues'].append(record.getMessage()[:300])
    logger=logging.getLogger('job_scraper.ats');handler=Capture();logger.addHandler(handler)
    try:
        with patch.object(requests,'get',wrapper(original_get)),patch.object(requests,'post',wrapper(original_post)):
            yield
    finally:logger.removeHandler(handler)

def new_row(company):
    return dict(company=company['name'],url=company.get('careers_url',''),status='',requests=0,raw_candidates=0,date_rejected=0,extraction_unresolved=0,title_rejected=0,location_rejected=0,score_rejected=0,eligible=0,unknown_dates=0,detail_fetches=0,methods=[],issues=[],seconds=0)

def finish(row,error=None):
    row['issues']=list(dict.fromkeys(row['issues']))
    if error:row['status']='ERROR';row['issues'].append(str(error)[:300])
    elif row['raw_candidates'] and row.get('title_unresolved',0)>=row['raw_candidates']:row['status']='EXTRACTION_INCOMPLETE'
    elif row['issues']:row['status']='PARTIAL_OR_WARNING' if row['raw_candidates'] else 'FAILED_OR_BLOCKED'
    elif not row['raw_candidates']:row['status']='EMPTY_UNVERIFIED'
    elif row.get('title_unresolved',0) or row.get('location_unconfirmed',0):row['status']='PARTIAL_OR_WARNING'
    elif row['eligible']:row['status']='MATCHES_FOUND'
    else:row['status']='NO_MATCHES_IN_EXTRACTED_RESULTS'
    text=' '.join(row['issues']).lower()
    if row.get('runtime_budget',{}).get('expired'):category,action='SOURCE_TIME_BUDGET_EXHAUSTED','Source deadline reached. Partial candidates retained; remaining details skipped. Coverage is incomplete.'
    elif row.get('title_unresolved',0):category,action='UNRESOLVED_JOB_TITLES','Some job titles could not be extracted. These are extraction gaps, not rejected job preferences. Review unresolved URL examples.'
    elif row.get('location_unconfirmed',0):category,action='LOCATION_NEEDS_REVIEW','Title/date survivors have unconfirmed locations. Unconfirmed locations are excluded from the board. Counts remain available for diagnostics.'
    elif '429' in text:category,action='RATE_LIMITED','Reduce repeated requests; respect rate limits. Do not bypass blocks.'
    elif 'certificate' in text or 'sslerror' in text:category,action='TLS_ERROR','Verify official URL/certificate. Keep TLS verification enabled.'
    elif 'name_not_resolved' in text or 'nameresolutionerror' in text:category,action='DNS_ERROR','Verify domain; a single run cannot prove a permanent outage.'
    elif '404' in text:category,action='URL_OR_BOARD_NOT_FOUND','Verify careers URL or board token against the employer site.'
    elif '403' in text or '405' in text or '406' in text:category,action='HTTP_ACCESS_ERROR','Check supported public careers source; do not assume no vacancies.'
    elif 'pagination' in text:category,action='INCOMPLETE_PAGINATION','Some pages may be missing. Review pagination response and totals.'
    elif 'coverage unverified' in text:category,action='COVERAGE_UNVERIFIED','Candidates may be partial. Review page counts and stop reasons; empty is not proof of no vacancies.'
    elif 'budget exhausted' in text:category,action='BROWSER_BUDGET_EXHAUSTED','Browser fallback was skipped; extraction coverage unverified.'
    elif 'headless' in text or 'browser' in text:category,action='BROWSER_ERROR','Review browser navigation/rendering warning.'
    elif error:category,action='EXTRACTION_ERROR','Inspect exception and source response.'
    elif not row['raw_candidates']:category,action='EMPTY_UNVERIFIED','No extracted vacancies; source coverage needs verification.'
    elif row.get('unverified_candidates',0):category,action='LINK_EXTRACTION_UNVERIFIED','Candidates include HTML links; extraction is not certified complete.'
    elif row['eligible']:category,action='MATCHES_FOUND','Matches extracted; completeness still unverified.'
    else:category,action='FILTERED_RESULTS','Review rejected-title samples and date/location/score counts.'
    row['diagnosis']=category;row['recommended_action']=action
    return row

def save_report(rows,path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'generated_at':datetime.now(timezone.utc).isoformat(),'scope':'This reports extracted candidates, not proof that every vacancy was retrieved. Empty is not a certified no-vacancy result. Eligible is before tracker deduplication.','companies':rows},indent=2),encoding='utf-8')

def append_to_page(rows,path):
    path=Path(path)
    if not path.exists():return
    cols=['company','status','diagnosis','title_recovered','title_unresolved','raw_candidates','date_rejected','extraction_unresolved','title_rejected','location_rejected','location_unconfirmed','score_rejected','eligible','unknown_dates','detail_fetches','requests','seconds']
    headers=''.join('<th>'+html.escape(c.replace('_',' ').title())+'</th>' for c in cols)
    body=[]
    for row in rows:
        cells=''.join('<td>'+html.escape(str(row.get(c,'')))+'</td>' for c in cols)
        details=html.escape((row.get('recommended_action','')+' | '+('; '.join(row['issues']) or 'No captured HTTP/browser warnings')))
        if row.get('runtime_budget'):
            details += '<br>Runtime budget: ' + html.escape(json.dumps(row['runtime_budget']))
        if row.get('pagination'):
            details += '<br>Pagination: ' + html.escape(json.dumps(row['pagination']))
        if row.get('unresolved_samples'):
            details += '<br>Unresolved examples: ' + html.escape(json.dumps(row['unresolved_samples'],ensure_ascii=False))
        for key in ('date_decisions','date_rejected_samples','date_kept_samples','location_rejected_samples','title_rejection_details','score_rejected_samples'):
            if row.get(key):details += '<br>'+html.escape(key.replace('_',' ').title())+': '+html.escape(json.dumps(row[key],ensure_ascii=False))
        samples=html.escape('; '.join(row.get('title_rejected_samples',[])))
        details += '<br>Rejected-title examples: '+samples if samples else ''
        body.append('<tr>'+cells+'<td><details><summary>View diagnostics</summary><div>'+details+'</div></details></td></tr>')
    section='<h2>Scraper health</h2><p>Empty does not prove no vacancies. Eligible counts precede tracker deduplication. Link candidates may be unverified. Unresolved titles are extraction gaps, not title-filter rejections. Warnings include detail-page requests.</p><div class="scroll-wrap"><table><thead><tr>'+headers+'<th>Issues</th></tr></thead><tbody>'+''.join(body)+'</tbody></table></div>'
    from collections import Counter
    statuses=Counter(row.get('status','UNKNOWN') for row in rows)
    badges=''.join('<span>'+html.escape(status.replace('_',' ').title())+': '+str(count)+'</span>' for status,count in sorted(statuses.items()))
    section='<details id="scraper-health" class="health-panel"><summary>Source health &amp; diagnostics <span>'+str(len(rows))+' sources checked · expand for coverage details</span></summary><div class="health-content"><div class="health-stats">'+badges+'</div>'+section+'</div></details>'
    text=path.read_text(encoding='utf-8')
    # Replace an existing panel rather than duplicate it on repeated appends.
    start='<!-- TRACKER_HEALTH_START -->';end='<!-- TRACKER_HEALTH_END -->'
    if start in text and end in text:
        a=text.index(start);b=text.index(end,a)+len(end);text=text[:a]+text[b:]
    path.write_text(text.replace('</body>',start+section+end+'</body>'),encoding='utf-8')
'''

# Module: src/extraction.py
_SOURCES['extraction'] = r'''"""Consolidated extraction implementation. See INSTALL.txt for migration details."""
from __future__ import annotations

# ========================================================================
# vacancy_extract: consolidated implementation
# ========================================================================
"""Evidence-based vacancy extraction; unknown dates and locations stay unknown."""
import json,re
from urllib.parse import urljoin,urlparse,parse_qs,urlencode,urlunparse
from bs4 import BeautifulSoup

def plain(value):
    return BeautifulSoup(str(value or ''),'html.parser').get_text(' ',strip=True)

def walk(value):
    if isinstance(value,dict):
        kind=value.get('@type',[])
        if kind=='JobPosting' or isinstance(kind,list) and 'JobPosting' in kind:yield value
        for child in value.values():yield from walk(child)
    elif isinstance(value,list):
        for child in value:yield from walk(child)

def location(value):
    if isinstance(value,list):return '; '.join(filter(None,(location(v) for v in value)))
    if isinstance(value,str):return value
    if not isinstance(value,dict):return ''
    address=value.get('address',value)
    if isinstance(address,str):return address
    if not isinstance(address,dict):return ''
    return ', '.join(str(address[k]) for k in ('addressLocality','addressRegion','addressCountry') if isinstance(address.get(k),str))

NAV = re.compile(r'^(?:zum hauptinhalt springen|zum inhalt springen|skip to (?:main )?content|contact(?: us)?|kontakt|faqs?(?:.*)?|support|help(?:.*)?|review accommodations.*|privacy.*|datenschutz.*|impressum|legal(?: notice| information| disclaimer)?|cookie.*|sign in|log in|register|search(?: jobs)?|all jobs|view all jobs|careers|karriere|jobs|stellenangebote|open positions|job alerts?|talent community|benefits|our culture|about us|locations|saved jobs|actions|to the vacancies|apply unsolicited now|bewerberlogin|candidate login|my applications)$',re.I)
APPLY = re.compile(r'^(?:apply(?: now| here| for this job)?|bewerben|jetzt bewerben|direkt bewerben|mehr erfahren|learn more|read more|details|view job|view details|job details|view|hier|here|to the complete job advertisement|zur stellenanzeige|zur stellenausschreibung|zur bewerbung|zur online bewerbung)$',re.I)
ROLE = re.compile(r'\b(?:manager|management|engineer|engineering|consultant|specialist|analyst|coordinator|director|head|lead|developer|architect|officer|assistant|associate|buyer|intern|technician|scientist|designer|administrator|controller|accountant|nurse|sales|recruiter|werkstudent|praktikant|leiter|leitung|referent|berater|entwickler|projekt|mitarbeiter|sachbearbeiter|kaufmann|kauffrau|fachkraft|monteur|techniker|ingenieur)\w*\b',re.I)
DETAIL = re.compile(r'/(?:jobs?|positions?|postings?|vacancies|stellenangebote|job-detail|o)/[^/?#]+',re.I)
BAD_PATH = re.compile(r'(?:^|/)(?:zum hauptinhalt springen|zum inhalt springen|skip to (?:main )?content|contact|kontakt|faq|support|privacy|legal|accommodations|benefits|culture|alerts?|search|saved|saved-jobs|login|register|hvhapply|apply|application|bewerbung|bewerben)(?:/|$)',re.I)

def is_vacancy_url(url):
    parsed=urlparse(str(url or ''));path=parsed.path.lower();query=parse_qs(parsed.query)
    if parsed.scheme not in ('http','https') or BAD_PATH.search(path):return False
    if path.endswith(('.pdf','.png','.jpg','.css','.js')):return False
    if re.search(r'/(?:jobs|careers|vacancies)/(?:page/\d+|results|search|saved)/?$',path):return False
    if re.search(r'/(?:jobs|careers|vacancies)/?$',path) and any(k.lower() in ('page','start','offset') for k in query):return False
    if re.search(r'/(?:jobs|vacancies)/(?:all|results|search)/?$|/applications/?$',path):return False
    if any(k.lower() in ('action','mode') and any(str(v).lower() in ('login','register','apply','application') for v in values) for k,values in query.items()):return False
    return True

def canonical_job_url(url):
    parsed=urlparse(str(url or ''))
    query=[(k,v) for k,values in parse_qs(parsed.query,keep_blank_values=True).items() for v in values if not k.lower().startswith(('utm_','gh_src')) and k.lower() not in ('source','ref','referral','trackingid','jsessionid')]
    path=re.sub(r';jsessionid=[^/;?]+','',parsed.path,flags=re.I)
    return urlunparse(parsed._replace(path=path,params='',query=urlencode(sorted(query)),fragment=''))

def clean_label(value):
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    return re.sub(r'(?:\s*(?:new\s*tab(?:\s*icon)?|opens? in (?:a )?new (?:tab|window)|wird (?:auf|in) einer neuen registerkarte geöffnet|external link(?: icon)?))+$', '', text, flags=re.I).strip()

def anchor_label(anchor):
    clone = BeautifulSoup(str(anchor), 'html.parser')
    for tag in clone.select('svg,img,[aria-hidden="true"],.icon,.sr-only,.visually-hidden'):
        tag.decompose()
    return clean_label(clone.get_text(' ', strip=True))

def usable_title(title):
    title = clean_label(title)
    return bool(3<=len(title)<=200 and not NAV.fullmatch(title) and not APPLY.fullmatch(title))

def resolve_job_url(base, target):
    """Resolve observed links, including Google's app-root-relative results links."""
    if not isinstance(target, str) or not target.strip():return ''
    target=target.strip()
    parsed=urlparse(base)
    if parsed.hostname in ('google.com','www.google.com','careers.google.com') and target.startswith('jobs/results/') and '/jobs/results' in parsed.path:
        root=parsed.path.split('/jobs/results',1)[0]
        return urljoin(base,root+'/'+target)
    return urljoin(base,target)

def document_base(soup,url):
    tag=soup.find('base',href=True)
    if tag:
        target=urljoin(url,tag['href'])
        if urlparse(target).scheme in ('http','https') and urlparse(target).hostname==urlparse(url).hostname:return target
    return url

def structured_jobs(html,url):
    soup=BeautifulSoup(html,'html.parser');jobs=[]
    for script in soup.find_all('script',type='application/ld+json'):
        try:items=list(walk(json.loads(script.string or script.get_text())))
        except (ValueError,TypeError):continue
        for item in items:
            title=plain(item.get('title'));target=item.get('url') or (url if len(items)==1 else '')
            if not usable_title(title) or not isinstance(target,str) or not target:continue
            target=resolve_job_url(document_base(soup,url),target)
            if urlparse(target).scheme not in ('http','https'):continue
            jobs.append(dict(title=title,location=location(item.get('jobLocation')),url=target,description=plain(item.get('description')),posted_date=item.get('datePosted') or '',_date_raw=item.get('datePosted') or '',_date_field='datePosted',_date_kind='publication',_date_source=url,_extraction='jsonld'))
    return dedupe(jobs)

def dedupe(jobs):
    """Keep stable order and enrich duplicate URLs instead of losing richer cards."""
    result=[];seen={}
    for original in jobs:
        key=('id',urlparse(str(original.get('url',''))).hostname,str(original['_job_id'])) if original.get('_job_id') else canonical_job_url(original.get('url',''))
        if not key:continue
        if key not in seen:
            item=dict(original);seen[key]=item;result.append(item);continue
        item=seen[key]
        if usable_title(original.get('title','')) and (not usable_title(item.get('title','')) or item.get('_needs_title')):
            item['title']=original['title'];item.pop('_needs_title',None)
        for field in ('location','description','posted_date','_detail_api_url','_pagination','_date_raw','_date_field','_date_kind','_date_source','_date_observed_on'):
            if not item.get(field) and original.get(field):item[field]=original[field]
        if original.get('_extraction') in ('jsonld','public_embedded_jobs'):
            item['_extraction']=original['_extraction']
    return result

def card_title(anchor):
    # Recover a nearby title only from a small card, not the whole job board.
    for parent in list(anchor.parents)[:3]:
        if parent.name in ('body','html','nav','footer','header'):break
        headings=parent.select('h2,h3,h4,h5,h6,[itemprop="title"],.job-title,.position-title,[data-ph-at-id="job-link"],.jobTitle')
        valid=list(dict.fromkeys(clean_label(h.get_text(' ',strip=True)) for h in headings if usable_title(h.get_text(' ',strip=True))))
        if len(valid)==1:return valid[0],parent
        if len(valid)>1:break
    return '',None

def html_jobs(html,url):
    structured=structured_jobs(html,url);soup=BeautifulSoup(html,'html.parser');jobs=[];base=document_base(soup,url)
    for a in soup.find_all('a',href=True):
        if a.find_parent(['nav','footer','header']):continue
        target=resolve_job_url(base,a['href']);parsed=urlparse(target);title=anchor_label(a)
        if not usable_title(title):
            for attr in ('title','aria-label'):
                label=clean_label(a.get(attr,''))
                if usable_title(label) and ROLE.search(label):title=label;break
        if parsed.scheme not in ('http','https') or parsed.path.lower().endswith(('.pdf','.png','.jpg','.css','.js')) or BAD_PATH.search(parsed.path):continue
        if NAV.fullmatch(title):continue
        if not is_vacancy_url(target):continue
        query=parse_qs(parsed.query)
        detail=bool((parsed.hostname=='join.com' and re.search(r'/companies/[^/]+/\d+[-/]',parsed.path)) or DETAIL.search(parsed.path) or re.search(r'/(?:stellen|stelle|jobangebot|stellenanzeige|job-posting)/[^/]+|[-/]j\d+\.html$',parsed.path,re.I) or any(k.lower() in ('jobid','job_id','postingid','positionid','jobofferid','career_job_req_id') for k in query))
        if not detail:continue
        card=None;method='html_link_unverified'
        if APPLY.fullmatch(title) or not title:
            title,card=card_title(a);method='html_card_unverified'
            if not title:
                jobs.append(dict(title='',location='',url=target,description='',posted_date='',_extraction='detail_link_unverified',_needs_title=True));continue
        if not usable_title(title):continue
        # A detail-looking path alone is insufficient for a generic label.
        if not ROLE.search(title) and not re.search(r'\d|[0-9a-f]{8}-',parsed.path,re.I) and card is None:continue
        loc=''
        if card is None:
            for parent in list(a.parents)[:3]:
                if parent.name in ('body','html','main'):break
                links={link.get('href') for link in parent.select('a[href]') if DETAIL.search(urlparse(resolve_job_url(base,link.get('href',''))).path)}
                if len(links)>1:break
                if parent.select_one('[itemprop="jobLocation"],.job-location,.position-location,[data-testid="job-location"],[data-ph-at-id="job-location"]'):
                    card=parent;break
        if card is not None:
            tag=card.select_one('[itemprop="jobLocation"],.job-location,.position-location,[data-testid="job-location"],[data-ph-at-id="job-location"]')
            if tag:loc=tag.get_text(' ',strip=True)
        jobs.append(dict(title=title,location=loc,url=target,description='',posted_date='',_extraction=method))
    return dedupe(structured+jobs)

def detail_title(html):
    soup=BeautifulSoup(html,'html.parser')
    for tag in soup(['nav','footer','header','script','style']):tag.decompose()
    heading=soup.select_one('h1,[itemprop="title"]')
    title=heading.get_text(' ',strip=True) if heading else ''
    # H1 fallback requires role evidence; generic page headings are not titles.
    return title if usable_title(title) and ROLE.search(title) else ''

def ashby_jobs(data):
    if not isinstance(data,dict) or not isinstance(data.get('jobs'),list):raise ValueError('Ashby jobs response shape invalid')
    jobs=[]
    for j in data['jobs']:
        if j.get('isListed') is False or not j.get('title') or not j.get('jobUrl'):continue
        loc=[j.get('location','')]+[x.get('location','') for x in j.get('secondaryLocations',[]) if isinstance(x,dict)]
        jobs.append(dict(title=j['title'],location='; '.join(filter(None,loc)),url=j['jobUrl'],description=j.get('descriptionPlain') or plain(j.get('descriptionHtml')),posted_date=j.get('publishedAt') or '',_date_raw=j.get('publishedAt') or '',_date_field='publishedAt',_date_kind='publication',_date_source=j['jobUrl'],_extraction='ashby_api'))
    return dedupe(jobs)
'''

# Module: src/main.py
_SOURCES['main'] = r'''"""Consolidated main implementation. See INSTALL.txt for migration details."""
from __future__ import annotations

# ========================================================================
# main: consolidated implementation
# ========================================================================
"""Synchronous Munich-region pipeline with auditable filtering and confirmed-location results only."""
import logging,re,sys,time,os
from datetime import datetime
from src.runtime import selected, source_seconds
from src.review import review_hint, review_candidate, save_review
from src.source_quality import quality
from collections import Counter
from pathlib import Path
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from src.scrapers import scrape_company, fetch_job_details, reset_headless_budget
from src.diagnostics import source_budget
from src.matching import title_decision, location_decision, score_jobs, related_role_candidate, role_decision, background_decision
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

def scrape_all_any_city(companies,cv_profile,schedule=None):
    review_rows=[]
    jobs=[];reports=[];counts={'ok':0,'empty':0,'errored':0,'total':0,'title_matched':0,'location_confirmed':0}
    detail_cache={}
    def cached_details(url,row):
        if not url or company.get('detail_fetch_mode','matched_only')=='none':return {},''
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
        if excluded(company) or not selected(company,schedule):continue
        counts['total']+=1;row=new_row(company);start=time.monotonic();error=None
        row.update(location_unconfirmed=0,non_job_links_removed=0,duplicate_candidates_removed=0,date_decisions={},date_rejected_samples=[],date_kept_samples=[],location_rejected_samples=[],title_rejection_details=[])
        try:
            with source_budget(source_seconds(company,schedule)) as budget, observe(row):
                extracted=budget.call(scrape_company,company,default=None,phase='listing')
                if extracted is None:extracted=budget.candidates
                budget.remember(extracted)
                row['source_quality']=quality(extracted, expected_locations=company.get('expected_locations'))
                row['verification_status']=company.get('verification_status','unknown')
                valid=[j for j in extracted if is_vacancy_url(j.get('url',''))]
                row['non_job_links_removed']=len(extracted)-len(valid)
                raw=dedupe(valid);row['duplicate_candidates_removed']=len(valid)-len(raw);row['raw_candidates']=len(raw)
                row['pagination']=getattr(extracted,'pagination',None) or next((j['_pagination'] for j in raw if j.get('_pagination')), {})
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
                if company.get('detail_fetch_mode')=='all':
                    for item in raw:
                        if location_decision(item)['status'] not in ('outside','excluded'):
                            details,body=cached_details(item.get('_detail_api_url') or item.get('url',''),row)
                            enrich(item,details)
                            if not item.get('description') and body:item['description']=body
                recent=[]
                for job in raw:
                    if audit_date(row,job,'list')['keep']:recent.extend(filter_recent_jobs([job]))
                    else:row['date_rejected']+=1
                readable=[j for j in recent if usable_title(j.get('title','')) and not j.get('_needs_title')]
                row['extraction_unresolved']=len(recent)-len(readable)
                titled=[]
                for job in readable:
                    if (related_role_candidate(job['title'],cv_profile) or review_hint(job,cv_profile)) and location_decision(job)['status'] not in ('outside','excluded') and not job.get('description'):
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
                        review=review_candidate(job,cv_profile,company)
                        if review:
                            review_rows.append(review)
                            row['review_candidates']=row.get('review_candidates',0)+1
                        row['title_rejected']+=1
                        if len(row['title_rejection_details'])<20:row['title_rejection_details'].append({'title':job['title'],'url':job['url'],**decision})
                row['title_rejected_samples']=[j['title'] for j in row['title_rejection_details'][:8]]
                counts['title_matched']+=len(titled);confirmed=[]
                for job in titled:
                    loc_before=location_decision(job)
                    # Known outside/excluded locations do not need expensive details.
                    if loc_before['status'] not in ('outside','excluded') and job.get('url') and (loc_before['status']=='unconfirmed' or not job.get('description') or not job.get('posted_date')):
                        details,body=cached_details(job.get('_detail_api_url') or job['url'],row);enrich(job,details)
                        if not job.get('description') and body:job['description']=body
                    mismatch=background_decision(job,cv_profile)
                    if not mismatch['keep']:
                        row['title_rejected']+=1
                        row['title_rejection_details'].append({'title':job['title'],'url':job['url'],**mismatch})
                        continue
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
                        review_rows.append(dict(company=company['name'],title=job['title'],location=job.get('location',''),url=job['url'],relevance_score='',review_reason='Matching title; explicit workplace could not be confirmed',location_status='unconfirmed'))
                        continue
                    job['company']=company['name'];job['city']='Munich';job.pop('matched_city',None);confirmed.append(job)
                counts['location_confirmed']+=len(confirmed)
                eligible=confirmed
                row['score_rejected']=0;row['eligible']=len(eligible)
                row['score_rejected_samples']=[]
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
    save_review(review_rows,ROOT/'data/review_candidates.csv')
    return jobs,counts,reports

def run():
    profile=load_yaml(CV_PROFILE_FILE);reset_headless_budget()
    configuration=load_yaml(COMPANIES_FILE)
    companies=configuration.get('companies',[])+load_yaml(JOB_BOARDS_FILE).get('companies',[])
    names={n.strip().casefold() for n in os.getenv('SCRAPE_COMPANIES','').split(',') if n.strip()}
    if names:companies=[c for c in companies if c['name'].casefold() in names]
    logger.info('Run mode: %s; configured companies: %d',os.getenv('SCRAPE_TIER','all'),len(companies))
    jobs,counts,reports=scrape_all_any_city(companies,profile,configuration.get('scrape_schedule'))
    if not reports or not any(row.get('raw_candidates',0)>row.get('extraction_unresolved',0) for row in reports) or all(row['status'] in ('ERROR','FAILED_OR_BLOCKED','EMPTY_UNVERIFIED','EXTRACTION_INCOMPLETE') for row in reports):
        raise RuntimeError('No usable source results; existing tracker and webpage retained.')
    summary=update_tracker(TRACKER_FILE,jobs,min_score=0,related_roles_enabled=(profile.get('related_roles') or {}).get('enabled') is True)
    generate_html();append_to_page(reports,ROOT/'docs/index.html')
    logger.info('Run counts: %s | Tracker: %s',counts,summary)
if __name__=='__main__':run()
'''

# Module: src/matching.py
_SOURCES['matching'] = r'''"""Consolidated matching implementation. See INSTALL.txt for migration details."""
from __future__ import annotations

# ========================================================================
# location_priority: consolidated implementation
# ========================================================================
"""Location priority uses explicit vacancy location text, never company HQ."""
import re,unicodedata

def normalize(value):
    text=str(value or '').lower().replace('ä','ae').replace('ö','oe').replace('ü','ue').replace('ß','ss')
    return ''.join(c for c in unicodedata.normalize('NFKD',text) if not unicodedata.combining(c))

def contains(text,word):
    return re.search(r'(?<!\w)'+re.escape(normalize(word))+r'(?!\w)',normalize(text)) is not None

PREFERRED={
 'Taufkirchen':['taufkirchen'],
 'Unterhaching':['unterhaching'],
 'Ottobrunn':['ottobrunn'],
 'Unterfoehring':['unterfoehring','unterföhring'],
 'Bogenhausen':['bogenhausen'],
 'Werksviertel':['werksviertel'],
 'Lehel':['lehel'],
 'Downtown':['altstadt','innenstadt','city centre','city center','downtown','marienplatz','odeonsplatz','karlsplatz','sendlinger tor'],
}
MUNICH_LABELS=['munich','münchen','muenchen','munich metropolitan region','metropolregion münchen']
REGIONAL_LABELS=['augsburg','ingolstadt','kaufbeuren','landshut','altötting','bad tölz','wolfratshausen','dachau','dillingen an der donau','dingolfing','landau an der isar','donau-ries','ebersberg','freising','fürstenfeldbruck','garmisch-partenkirchen','kelheim','landsberg am lech','miesbach','mühldorf am inn','ostallgäu','pfaffenhofen an der ilm','starnberg']

def area_priority(location):
    for label,terms in PREFERRED.items():
        if any(contains(location,t) for t in terms):
            # Other towns share the name Taufkirchen: do not prioritise
            # an explicit contradictory district as the Hachinger Tal site.
            if label=='Taufkirchen' and any(contains(location,t) for t in ['vils','erding','mühldorf']):continue
            return 0,label
    if any(contains(location,t) for t in ['munich','münchen','muenchen']):return 1,'Munich (area unspecified)'
    return 2,'Wider region / area unspecified'

def sort_key(location,score):
    rank,_=area_priority(location)
    return (rank,)


# ========================================================================
# job_scope: consolidated implementation
# ========================================================================
"""Shared Munich-area scope and excluded recruitment-source checks."""
import re
from urllib.parse import urlparse
SCOPE_EXCLUDED_LOCATION = re.compile(r"\b(?:zurich|zürich|zuerich|switzerland|schweiz|suisse|svizzera|basel|basle|bern|berne|geneva|genève|geneve|lausanne|lucerne|luzern|zug|winterthur|kloten|wallisellen|dübendorf|duebendorf|opfikon|adliswil|horgen|dietikon|uster|regensdorf|schlieren|volketswil|wetzikon|thalwil)\b", re.I)
def excluded_agency(name='', url=''):
    compact = re.sub(r'[^a-z0-9]', '', str(name).lower())
    host = urlparse(str(url)).netloc.lower()
    return any(x in compact for x in ('randstad','michaelpage','michalpage')) or any(x in host for x in ('randstad','michaelpage'))
def excluded_source(entry):
    return excluded_agency(entry.get('name',''), entry.get('careers_url','')) or bool(SCOPE_EXCLUDED_LOCATION.search(' '.join(str(entry.get(k,'')) for k in ('city','country','name'))))
def allowed_job(job):
    return job.get('city') == 'Munich' and not excluded_agency(job.get('company',''),job.get('url','')) and not SCOPE_EXCLUDED_LOCATION.search(str(job.get('location','')))


# ========================================================================
# job_recency: consolidated implementation
# ========================================================================
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import re
MAX_JOB_AGE_DAYS = 21

def today_local():
    return datetime.now(ZoneInfo("Europe/Berlin")).date()

def parse_posted(value, *, today=None, allow_relative=False):
    today = today or today_local()
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo("Europe/Berlin")).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone(ZoneInfo("Europe/Berlin")).date() if parsed.tzinfo else parsed.date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", text):
        try:
            return datetime.strptime(text, "%d.%m.%Y").date()
        except ValueError:
            return None
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:return datetime.strptime(text,fmt).date()
        except ValueError:pass
    if not allow_relative:
        return None
    t = re.sub(r"^(?:posted|published|veröffentlicht)\s+", "", text.lower()).strip()
    t = re.sub(r"(?<=\d)\+", "", t)
    if t in ("today", "heute", "just posted"):
        return today
    if t in ("yesterday", "gestern"):
        return today - timedelta(days=1)
    m = re.fullmatch(r"(\d+)\s+(day|days|week|weeks|hour|hours)\s+ago", t)
    if m:
        n = int(m.group(1)); unit = m.group(2)
        days = n * 7 if unit.startswith("week") else n if unit.startswith("day") else n // 24
        return today - timedelta(days=days)
    m = re.fullmatch(r"vor\s+(\d+)\s+(tag|tagen|tage|woche|wochen)", t)
    if m:
        n = int(m.group(1))
        return today - timedelta(days=n * 7 if m.group(2).startswith("woch") else n)
    return None

def keep_posted(value, *, today=None, allow_relative=False):
    today = today or today_local()
    posted = parse_posted(value, today=today, allow_relative=allow_relative)
    return posted is None or posted > today or (today - posted).days <= MAX_JOB_AGE_DAYS

def date_decision(job, *, today=None):
    today=today or today_local()
    raw=job.get('_date_raw',job.get('posted_date',''))
    field=job.get('_date_field','posted_date_unspecified')
    kind=job.get('_date_kind','publication_unspecified')
    observed=job.get('_date_observed_on',today.isoformat())
    if kind in ('creation_unverified','update','first_seen'):
        return dict(keep=True,reason='not_a_verified_publication_date',raw=str(raw or ''),field=field,kind=kind,parsed='',age_days=None,observed_on=observed,source=job.get('_date_source',job.get('url','')))
    # Prefer an absolute date already anchored at retrieval. Relative text is
    # parsed against this run only; it is never refreshed in saved workbook rows.
    value=job.get('posted_date','')
    posted=parse_posted(value,today=today,allow_relative=True)
    if posted is None:reason='missing_date' if not str(value or '').strip() else 'unparseable_date'
    elif posted>today:reason='future_date_needs_review'
    elif (today-posted).days>MAX_JOB_AGE_DAYS:reason='older_than_21_days'
    else:reason='within_21_days'
    return dict(keep=reason!='older_than_21_days',reason=reason,raw=str(raw or ''),field=field,kind=kind,parsed=posted.isoformat() if posted else '',age_days=(today-posted).days if posted else None,observed_on=observed,source=job.get('_date_source',job.get('url','')))

def filter_recent_jobs(jobs, *, today=None):
    today=today or today_local();kept=[]
    for job in jobs:
        decision=date_decision(job,today=today)
        if not decision['keep']:continue
        item=dict(job);item['_date_audit']=decision
        if decision['reason']=='not_a_verified_publication_date':item['posted_date']=''
        elif decision['parsed'] and decision['reason']!='future_date_needs_review':item['posted_date']=decision['parsed']
        kept.append(item)
    return kept

def prune_old_rows(ws, columns, *, today=None):
    today = today or today_local()
    if "Job Posted" not in columns:
        return 0
    col = columns.index("Job Posted") + 1
    removed = 0
    for row in range(ws.max_row, 1, -1):
        if not keep_posted(ws.cell(row=row, column=col).value, today=today):
            ws.delete_rows(row)
            removed += 1
    return removed


# ========================================================================
# matcher: consolidated implementation
# ========================================================================
"""Munich-region location matching without a driving-time or distance cutoff.
A regional town list still excludes unrelated locations; it is not a commute assessment.
"""



from typing import Any

import re
EXCLUDED_LOCATION = re.compile(r"\b(?:zurich|zürich|zuerich|switzerland|schweiz|suisse|svizzera|basel|basle|bern|berne|geneva|genève|geneve|lausanne|lucerne|luzern|zug|winterthur|kloten|wallisellen|dübendorf|duebendorf|opfikon|adliswil|horgen|dietikon|uster|regensdorf|schlieren|volketswil|wetzikon|thalwil)\b", re.I)

DEFAULT_SCORE_CEILING = 15

CITY_KEYWORDS = {'Munich': ['munich', 'münchen', 'muenchen', 'ottobrunn', 'taufkirchen', 'unterhaching', 'neubiberg', 'haar', 'putzbrunn', 'vaterstetten', 'parsdorf', 'poing', 'aschheim', 'kirchheim', 'feldkirchen', 'ismaning', 'unterföhring', 'unterfoehring', 'garching', 'freising', 'erding', 'hallbergmoos', 'fürstenfeldbruck', 'fuerstenfeldbruck', 'dachau', 'unterschleißheim', 'unterschleissheim', 'oberschleißheim', 'oberschleissheim', 'gilching', 'oberpfaffenhofen', 'weßling', 'wessling', 'germering', 'gräfelfing', 'graefelfing', 'planegg', 'martinsried', 'krailling', 'puchheim', 'starnberg', 'holzkirchen', 'grünwald', 'gruenwald', 'pullach', 'baierbrunn', 'höhenkirchen', 'hoehenkirchen', 'siegertsbrunn', 'brunnthal', 'sauerlach', 'hohenbrunn', 'bogenhausen', 'werksviertel', 'lehel', 'altstadt', 'haidhausen', 'berg am laim', 'obersendling', 'trudering', 'riem', 'schwabing']}

CITY_KEYWORDS['Munich'] = list(dict.fromkeys(CITY_KEYWORDS['Munich'] + REGIONAL_LABELS + MUNICH_LABELS))

def normalized_title(value):
    import unicodedata
    text=unicodedata.normalize('NFKC',str(value or '')).casefold()
    text=re.sub(r'[\s\-\u2010-\u2015_]+',' ',text)
    # Equivalent spelling only, not new occupational categories.
    text=re.sub(r'\bprojekt\s+(manager|leiter)(in)?\b',r'projekt\1\2',text)
    text=re.sub(r'\bproject\s*manager\b','project manager',text)
    text=re.sub(r'\b(programme|program)\s*manager\b',r'\1 manager',text)
    return text.strip()

def excluded_seniority(title):
    """Explicit junior/entry-level title labels only; never description mentions."""
    import unicodedata
    text=unicodedata.normalize('NFKC',str(title or '')).casefold()
    text=''.join(c for c in text if unicodedata.category(c)!='Cf')
    text=re.sub(r'[\s\u2010-\u2015_-]+',' ',text)
    return bool(re.search(r'(?<!\w)(?:junior|jr\.?|entry\s*level|berufseinsteiger(?:in|innen)?)(?!\w)',text))

def title_decision(title,must_match,must_not_match=None):
    if excluded_seniority(title):return {'keep':False,'reason':'excluded_junior_seniority','term':'junior / entry-level'}
    text=normalized_title(title)
    excluded=next((term for term in (must_not_match or []) if normalized_title(term) in text),None)
    included=next((term for term in must_match if normalized_title(term) in text),None)
    if excluded:return {'keep':False,'reason':'excluded_title_term','term':excluded}
    return {'keep':bool(included),'reason':'matched_title_term' if included else 'no_requested_title_term','term':included or ''}

def title_matches(title,must_match,must_not_match=None):
    return title_decision(title,must_match,must_not_match)['keep']

OUTSIDE_LOCATION=re.compile(r'\b(?:berlin|hamburg|frankfurt|stuttgart|dresden|leipzig|bremen|hannover|cologne|köln|koeln|düsseldorf|duesseldorf|nürnberg|nuernberg|nuremberg|karlsruhe|mannheim|london|paris|vienna|wien|graz|amsterdam|warsaw|prague|poland|czech|united states|united kingdom|india|china|singapore|canada|australia)\b',re.I)

def location_decision(job):
    # Only explicit vacancy location fields, never corporate/footer/body mentions.
    evidence=str(job.get('location') or '').strip()
    if EXCLUDED_LOCATION.search(evidence):return {'status':'excluded','reason':'excluded_swiss_location','evidence':evidence,'source':job.get('_location_source','vacancy_location')}
    if find_matching_city(evidence):return {'status':'confirmed','reason':'explicit_region_match','evidence':evidence,'source':job.get('_location_source','vacancy_location')}
    if not evidence:return {'status':'unconfirmed','reason':'missing_location','evidence':'','source':'none'}
    if OUTSIDE_LOCATION.search(evidence) and not re.search(r'available in|multiple|various|remote|\+\s*\d+',evidence,re.I):return {'status':'outside','reason':'explicit_outside_location','evidence':evidence,'source':job.get('_location_source','vacancy_location')}
    if re.search(r'available in|\+\s*\d+|multiple|various|remote|hybrid|deutschland|germany|europe|emea',evidence,re.I):
        return {'status':'unconfirmed','reason':'broad_remote_or_multiple_location','evidence':evidence,'source':job.get('_location_source','vacancy_location')}
    if OUTSIDE_LOCATION.search(evidence):return {'status':'outside','reason':'explicit_outside_location','evidence':evidence,'source':job.get('_location_source','vacancy_location')}
    return {'status':'unconfirmed','reason':'unrecognised_location_needs_review','evidence':evidence,'source':job.get('_location_source','vacancy_location')}

def location_status(location, expected_city):
    if not location:
        return 'unconfirmed'
    if expected_city != 'Munich' or EXCLUDED_LOCATION.search(location):
        return 'mismatch'
    return 'confirmed' if find_matching_city(location) else 'unconfirmed'

def filter_by_title_and_location(
    jobs: list[dict[str, Any]], cv_profile: dict[str, Any], expected_city: str = ""
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    must_match = cv_profile.get("title_must_match", [])
    must_not_match = cv_profile.get("title_must_not_match", [])

    kept = []
    title_matched_count = 0
    for job in jobs:
        title = job.get("title", "")
        if not title or not title_matches(title, must_match, must_not_match):
            continue
        title_matched_count += 1

        loc_status = location_status(job.get("location", ""), expected_city)
        if loc_status != "confirmed":
            continue

        kept.append(job)

    stats = {"title_matched": title_matched_count, "location_confirmed": len(kept)}
    return kept, stats



MAIN_LIST_CITIES = {"Munich"}
SWISS_CITIES = set()  # Compatibility only; no approved Swiss targets.
ALL_APPROVED_CITIES = list(CITY_KEYWORDS.keys())  # Munich, Zurich, Basel, Bern, Geneva, Lausanne, Lucerne


def find_matching_city(location, candidate_cities=None):
    if not location or EXCLUDED_LOCATION.search(location):
        return None
    if candidate_cities is not None and 'Munich' not in candidate_cities:
        return None
    loc = location.lower()
    return 'Munich' if any(contains(loc, kw) for kw in CITY_KEYWORDS['Munich']) else None

def filter_by_title_only(jobs: list[dict[str, Any]], cv_profile: dict[str, Any]) -> list[dict[str, Any]]:
    must_match = cv_profile.get("title_must_match", [])
    must_not_match = cv_profile.get("title_must_not_match", [])
    return [
        job for job in jobs
        if job.get("title") and title_matches(job["title"], must_match, must_not_match)
    ]


def resolve_city_for_job(job: dict[str, Any], search_text: str | None = None) -> str | None:
    matched_city = find_matching_city(search_text if search_text is not None else job.get("location", ""))
    if matched_city is None:
        return None
    job["matched_city"] = matched_city
    return matched_city


def extract_location_snippet(text: str, matched_city: str, window: int = 30) -> str:
    loc = text.lower()
    for kw in CITY_KEYWORDS.get(matched_city, [matched_city.lower()]):
        idx = loc.find(kw)
        if idx != -1:
            start = max(0, idx - window)
            end = min(len(text), idx + len(kw) + window)
            snippet = text[start:end].strip()
            return ("..." if start > 0 else "") + snippet + ("..." if end < len(text) else "")
    return matched_city


def filter_by_title_and_any_city(
    jobs: list[dict[str, Any]], cv_profile: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    title_matched = filter_by_title_only(jobs, cv_profile)
    kept = [job for job in title_matched if resolve_city_for_job(job) is not None]
    stats = {"title_matched": len(title_matched), "location_confirmed": len(kept)}
    return kept, stats


def _raw_score(description: str, title: str, keywords: list[dict[str, Any]]) -> int:
    text = f"{title} {description}".lower()
    score = 0
    for kw in keywords:
        if kw["term"].lower() in text:
            score += int(kw["weight"])
    return score


def score_job_1_to_10(description: str, title: str, keywords: list[dict[str, Any]], ceiling: int) -> int:
    raw = _raw_score(description, title, keywords)
    scaled = round((raw / ceiling) * 10) if ceiling else 0
    return max(1, min(10, scaled))


def score_jobs(jobs: list[dict[str, Any]], cv_profile: dict[str, Any]) -> list[dict[str, Any]]:
    keywords = cv_profile.get("scoring_keywords", [])
    ceiling = cv_profile.get("score_ceiling", DEFAULT_SCORE_CEILING)
    for job in jobs:
        job["relevance_score"] = score_job_1_to_10(
            job.get("description", ""), job.get("title", ""), keywords, ceiling
        )
    return jobs


# RELATED_ROLE_TRIAL_V1: does not change title_decision or keyword scoring.
def _responsibility_text(value):
    import html, unicodedata
    text = html.unescape(str(value or '')).casefold()
    text = text.replace('ä','ae').replace('ö','oe').replace('ü','ue').replace('ß','ss')
    text = unicodedata.normalize('NFKC', text)
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text)).strip()

def related_role_candidate(title, profile):
    settings = profile.get('related_roles') or {}
    if settings.get('enabled') is not True:
        return False
    direct = title_decision(title, profile.get('title_must_match', []), profile.get('title_must_not_match', []))
    if direct['keep'] or direct['reason'] != 'no_requested_title_term':
        return False
    text = normalized_title(title)
    return any(re.search(pattern, text, re.I) for pattern in settings.get('candidate_title_patterns', []))

def role_decision(job, profile):
    mismatch = background_decision(job, profile)
    if not mismatch['keep']:
        return dict(mismatch, match_type='', match_reason='', groups=[])
    direct = title_decision(job.get('title',''), profile.get('title_must_match', []), profile.get('title_must_not_match', []))
    if direct['keep']:
        return dict(direct, match_type='Direct role', match_reason='Title: '+direct['term'], groups=[])
    if not related_role_candidate(job.get('title',''), profile):
        return dict(direct, match_type='', match_reason='', groups=[])
    settings = profile['related_roles']
    text = _responsibility_text(job.get('description',''))
    groups, evidence = [], {}
    for group, terms in settings.get('responsibility_groups', {}).items():
        hits = [term for term in terms if re.search(r'(?<!\w)'+re.escape(_responsibility_text(term))+r'(?!\w)', text)]
        if hits:
            groups.append(group); evidence[group] = hits
    required = settings.get('required_any_group', [])
    # Missing keywords or descriptions are uncertainty, not a rejection.
    return dict(keep=True, reason='related_role_manual_review', term='',
                match_type='Related role',
                match_reason=('CV responsibility overlap: '+', '.join(groups) if groups else
                              'Related title; responsibilities need manual review'),
                groups=groups, evidence=evidence)


def background_decision(job, profile):
    """Reject explicit mismatches only; never reject on missing CV keywords.

    Title domains are explicit. Description exclusions require a requirement
    clause, not employer/industry boilerplate. This is a conservative heuristic,
    not a semantic guarantee; ambiguous requirements remain for manual review.
    """
    cfg = profile.get('background_filter') or {}
    title = _responsibility_text(job.get('title', ''))
    domains = cfg.get('excluded_specialist_domains', ['hospitality', 'hotel management', 'hotellerie', 'gastronomie'])
    for term in domains:
        if re.search(r'(?<!\w)' + re.escape(_responsibility_text(term)) + r'(?!\w)', title):
            return dict(keep=False, reason='explicit_background_mismatch', term=term)
    description = _responsibility_text(job.get('description', ''))
    clauses = re.split(r'[.!?;\n]+', description)
    for clause in clauses:
        if re.search(r'\b(?:preferred|desirable|advantage|nice to have|optional|ideally|wuenschenswert|von vorteil)\b', clause):
            continue
        if re.search(r'\b(?:not required|not mandatory|no .{0,60} required|nicht erforderlich|keine .{0,60} erforderlich)\b', clause):
            continue
        for term in domains:
            domain = re.escape(_responsibility_text(term))
            # Require experience + domain together and an explicit obligation.
            experience = r'(?:experience|background|erfahrung|berufserfahrung)'
            obligation = r'(?:required|mandatory|essential|must have|must bring|must possess|erforderlich|zwingend|voraussetzung)'
            patterns = [experience+r'[^.!?;]{0,55}'+domain+r'[^.!?;]{0,35}'+obligation,
                        obligation+r'[^.!?;]{0,35}'+experience+r'[^.!?;]{0,55}'+domain,
                        domain+r'[^.!?;]{0,20}'+experience+r'[^.!?;]{0,35}'+obligation]
            if any(re.search(p, clause) for p in patterns):
                return dict(keep=False, reason='explicit_background_mismatch', term=term)
    return dict(keep=True, reason='no_explicit_background_mismatch', term='')
'''

# Module: src/onboard_company.py
_SOURCES['onboard_company'] = r'''"""Test an employer and optionally promote only after quality acceptance."""
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
'''

# Module: src/reset_tracker.py
_SOURCES['reset_tracker'] = r'''"""Consolidated reset_tracker implementation. See INSTALL.txt for migration details."""
from __future__ import annotations

# ========================================================================
# reset_tracker: consolidated implementation
# ========================================================================



import logging
from pathlib import Path

from src.tracker import archive_and_reset
from src.website import generate as generate_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("job_scraper.reset")

ROOT = Path(__file__).resolve().parent.parent
TRACKER_FILE = ROOT / "data" / "job_tracker.xlsx"
ARCHIVE_DIR = ROOT / "archive"


def run() -> None:
    archived = archive_and_reset(TRACKER_FILE, ARCHIVE_DIR)
    if archived:
        logger.info("Archived previous tracker to %s", archived)
    else:
        logger.info("No existing tracker to archive; created a fresh one at %s", TRACKER_FILE)
    generate_html()


if __name__ == "__main__":
    run()
'''

# Module: src/review.py
_SOURCES['review'] = r'''"""Separate review queue; never bypasses the main tracker's filters."""
import csv
import re
from pathlib import Path
from src.extraction import usable_title, is_vacancy_url
from src.matching import title_decision, location_decision, date_decision, score_jobs, excluded_seniority, background_decision

FIELDS = ['company', 'title', 'location', 'url', 'relevance_score', 'review_reason', 'location_status']

def review_hint(job, profile):
    cfg = profile.get('review_candidates') or {}
    if cfg.get('enabled') is not True:
        return False
    title = job.get('title', '')
    direct = title_decision(title, profile.get('title_must_match', []), profile.get('title_must_not_match', []))
    if direct.get('reason') != 'no_requested_title_term' or excluded_seniority(title):
        return False
    text = title + ' ' + job.get('description', '')
    return any(re.search(r'(?<!\w)' + re.escape(t) + r'(?!\w)', text, re.I) for t in cfg.get('terms', []))

def review_candidate(job, profile, company):
    if not review_hint(job, profile) or not usable_title(job.get('title')) or not is_vacancy_url(job.get('url', '')):
        return None
    if not background_decision(job, profile)['keep'] or not date_decision(job)['keep']:
        return None
    loc = location_decision(job)
    if loc['status'] in ('outside', 'excluded'):
        return None
    return dict(company=company['name'], title=job['title'], location=job.get('location', ''),
                url=job['url'], relevance_score='', location_status=loc['status'],
                review_reason='Possible CV-related responsibilities; manual review, no score threshold')
def save_review(rows, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            if row['url'] in seen:
                continue
            seen.add(row['url'])
            # Prevent formula execution when opened in spreadsheet applications.
            safe = {k: ("'" + str(row.get(k, '')) if str(row.get(k, '')).startswith(('=', '+', '-', '@')) else row.get(k, '')) for k in FIELDS}
            writer.writerow(safe)
    temporary.replace(path)
'''

# Module: src/runtime.py
_SOURCES['runtime'] = r'''"""Validated run policy. Full coverage is the default; tiers are opt-in."""
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from contextvars import ContextVar
from contextlib import contextmanager

_OPTIONS = ContextVar('company_options', default={})
DEFAULT_SCHEDULE = {
    'priority_1': {'weekdays': list(range(7)), 'source_seconds': 90},
    'priority_2': {'weekdays': [1, 4], 'source_seconds': 45},
    'priority_3': {'weekdays': [6], 'source_seconds': 25},
}

def priority(company):
    value = int(company.get('priority', 3))
    if value not in (1, 2, 3):
        raise ValueError('priority must be 1, 2 or 3')
    return value

def selected(company, schedule=None, mode=None, now=None):
    if company.get('enabled', True) is not True:
        return False
    tier = priority(company)
    mode = (mode or os.getenv('SCRAPE_TIER', 'all')).lower()
    if mode in ('all', 'full'):
        return True
    if mode in ('daily', '1', 'priority_1'):
        return tier == 1
    if mode in ('2', 'priority_2'):
        return tier <= 2
    if mode != 'scheduled':
        raise ValueError('Unknown SCRAPE_TIER: ' + mode)
    rule = (schedule or DEFAULT_SCHEDULE).get('priority_' + str(tier), {})
    days = rule.get('weekdays', DEFAULT_SCHEDULE['priority_' + str(tier)]['weekdays'])
    if not isinstance(days, list) or any(type(d) is not int or d not in range(7) for d in days):
        raise ValueError('weekdays must contain integers 0 (Monday) through 6 (Sunday)')
    today = now or datetime.now(ZoneInfo('Europe/Berlin'))
    return today.weekday() in days

def source_seconds(company, schedule=None, mode=None):
    rule = (schedule or DEFAULT_SCHEDULE).get('priority_' + str(priority(company)), {})
    mode=(mode or os.getenv('SCRAPE_TIER','all')).lower()
    default=(schedule or {}).get('full_run_source_seconds',180) if mode in ('all','full') else rule.get('source_seconds',DEFAULT_SCHEDULE['priority_' + str(priority(company))]['source_seconds'])
    value = float(company.get('source_seconds',default))
    if not 1 <= value <= 600:
        raise ValueError('source_seconds must be between 1 and 600')
    return value

@contextmanager
def company_options(company):
    token = _OPTIONS.set(company)
    try:
        yield
    finally:
        _OPTIONS.reset(token)

def page_limit(default):
    value = int(_OPTIONS.get().get('max_pages', default))
    if not 1 <= value <= 1000:
        raise ValueError('max_pages must be between 1 and 1000')
    return value
'''

# Module: src/scrapers.py
_SOURCES['scrapers'] = r'''"""Consolidated scrapers implementation. See INSTALL.txt for migration details."""
from __future__ import annotations

# ========================================================================
# source_adapters: consolidated implementation
# ========================================================================
"""Explicit public-source adapters; no login, stealth or access-control bypass."""
import json,logging,re,time
from src.runtime import company_options, page_limit
from urllib.parse import urljoin,urlparse
from bs4 import BeautifulSoup
from src.extraction import walk, plain, location, usable_title, is_vacancy_url, dedupe, resolve_job_url
logger=logging.getLogger('job_scraper.ats')


def jsonld_records(data,url):
    jobs=[]
    for item in walk(data):
        target=resolve_job_url(url,item.get('url') or '')
        if not usable_title(item.get('title','')) or not is_vacancy_url(target):continue
        ident=item.get('identifier') or {}
        jobs.append(dict(title=plain(item['title']),location=location(item.get('jobLocation')),url=target,description=plain(item.get('description')),posted_date=item.get('datePosted') or '',_date_raw=item.get('datePosted') or '',_date_field='datePosted',_date_kind='publication',_date_source=url,_extraction='jsonld_feed',_job_id=str(ident.get('value','')) if isinstance(ident,dict) else str(ident)))
    return dedupe(jobs)


def scrape_jsonld_feed(name,url,get):
    response=get(url)
    if response is None:return []
    try:data=response.json()
    except (ValueError,TypeError):
        logger.warning('Public JSON-LD feed invalid for %s',name);return []
    jobs=jsonld_records(data,response.url or url)
    expected=data.get('numberOfItems') if isinstance(data,dict) else None
    completion='reported_total_reached' if isinstance(expected,int) and expected==len(jobs) else 'feed_unverified'
    if completion!='reported_total_reached':logger.warning('JSON-LD feed coverage unverified for %s: expected=%s extracted=%s',name,expected,len(jobs))
    for j in jobs:j['_pagination']={'reader':'jsonld_feed','pages':1,'advertised_total':expected,'unique_candidates':len(jobs),'completion':completion}
    return jobs


def bmw_cards(html,url):
    """Read observed BMW detail URLs, not invented API paths."""
    if urlparse(url).hostname not in ('www.bmwgroup.jobs','bmwgroup.jobs'):return []
    soup=BeautifulSoup(html,'html.parser');jobs=[]
    for anchor in soup.find_all('a',href=True):
        target=urljoin(url,anchor['href'])
        match=re.search(r'/jobfinder/job-description(?:-copy)?\.(\d+)\.html$',urlparse(target).path)
        if not match:continue
        lines=[x.strip() for x in anchor.get_text('\n',strip=True).splitlines() if x.strip()]
        heading=anchor.select_one('h2,h3,h4,h5,.job-title,[itemprop="title"]')
        title=heading.get_text(' ',strip=True) if heading else next((x for x in lines if not re.fullmatch(r'\d+|New|Neu',x,re.I) and not re.match(r'Published:|Veröffentlicht:',x,re.I)),'')
        title=re.sub(r'^\d{4,}\s+','',title)
        date_match=re.search(r'(?:Published|Veröffentlicht)\s*:\s*(\d{2}\.\d{2}\.\d{4})',anchor.get_text(' ',strip=True),re.I)
        loc=anchor.select_one('.job-location,.location,[itemprop="jobLocation"]')
        loc_text=loc.get_text(' ',strip=True) if loc else ''
        # The observed card repeats its explicit location at the end. Only use
        # this narrow repeated-value pattern, never the board's country/HQ.
        clean=[x for x in lines if x.lower() not in ('new','neu')]
        if not loc_text and len(clean)>3 and clean[-1]==clean[-2]:loc_text=clean[-1]
        if not usable_title(title):title=''
        jobs.append(dict(title=title,location=loc_text,url=target,description='',posted_date=date_match.group(1) if date_match else '',_date_raw=date_match.group(1) if date_match else '',_date_field='Published card label',_date_kind='publication',_date_source=url,_job_id=match.group(1),_extraction='bmw_card_unverified',**({'_needs_title':True} if not title else {})))
    return dedupe(jobs)


def scrape_amazon(name,url,get,max_pages=100,seconds=150):
    """Public search JSON, Germany-wide; no city/radius cutoff is introduced."""
    max_pages=page_limit(max_pages)
    jobs=[];seen=set();offset=0;total=None;pages=0;reason='empty_page';began=time.monotonic()
    endpoint='https://www.amazon.jobs/en/search.json'
    while pages<max_pages and time.monotonic()-began<seconds:
        response=get(endpoint,params={'country':'DEU','result_limit':100,'offset':offset,'sort':'recent'})
        if response is None:reason='request_failed';break
        try:data=response.json()
        except (ValueError,TypeError):reason='invalid_json';break
        if not isinstance(data,dict) or not isinstance(data.get('jobs'),list):reason='unexpected_schema';break
        page=data['jobs'];pages+=1
        if isinstance(data.get('hits'),int):total=data['hits']
        if not page:
            reason='empty_page' if total is None or len(jobs)>=total else 'early_empty_page';break
        added=0
        for item in page:
            target=item.get('job_path') or item.get('url') or ''
            target=urljoin('https://www.amazon.jobs',target)
            if not re.search(r'/jobs/\d+',urlparse(target).path) or not is_vacancy_url(target):continue
            ident=str(item.get('id_icims') or item.get('id') or target)
            if ident in seen:continue
            seen.add(ident);added+=1
            loc=item.get('location') or ', '.join(str(item[k]) for k in ('city','state','country') if item.get(k))
            raw=item.get('posted_date') or ''
            jobs.append(dict(title=plain(item.get('title')),location=loc if isinstance(loc,str) else '',url=target,description=plain(item.get('description')),posted_date=raw,_date_raw=raw,_date_field='posted_date',_date_kind='publication',_date_source=endpoint,_job_id=ident,_extraction='amazon_public_search'))
        if not added:reason='repeated_or_unreadable_page';break
        offset+=len(page)
        if total is not None and len(jobs)>=total:reason='reported_total_reached';break
    if pages>=max_pages:reason='page_limit'
    elif time.monotonic()-began>=seconds:reason='time_limit'
    if reason not in ('empty_page','reported_total_reached'):logger.warning('Amazon incomplete pagination: %s; retained %s',reason,len(jobs))
    if not jobs:

        return scrape_html_board(name,url,get)
    for j in jobs:j['_pagination']={'reader':'amazon_public_search','pages':pages,'advertised_total':total,'unique_candidates':len(jobs),'completion':reason}
    return jobs


# ========================================================================
# priority_boards: consolidated implementation
# ========================================================================
"""Narrow first-party extraction for IABG, Allianz and Infineon.
Only observed links/attributes are followed. No invented offer IDs, authentication,
CAPTCHA handling, guessed private endpoints or location inference from search terms.
"""
import logging,re
from urllib.parse import urlparse,urljoin,parse_qs,urlencode,urlunparse
from bs4 import BeautifulSoup
from src.extraction import clean_label, usable_title, ROLE, dedupe, plain
logger=logging.getLogger('job_scraper.ats')

def priority_cards(html,url):
    host=urlparse(url).hostname
    if host not in ('jobboerse.iabg.de','careers.allianz.com','jobs.infineon.com'):return []
    soup=BeautifulSoup(html,'html.parser');jobs=[]
    for anchor in soup.select('a[href], [data-href], [data-url]'):
        raw=anchor.get('href') or anchor.get('data-href') or anchor.get('data-url') or ''
        if raw.startswith(('javascript:','#')):continue
        target=urljoin(url,raw);p=urlparse(target);q=parse_qs(p.query)
        if p.hostname!=host:continue
        if host=='jobboerse.iabg.de':
            ident=q.get('jobOfferId',[''])[0]
            if not ident or 'showJobOfferDetail.do' not in p.path:continue
            path=re.sub(r'(?:;|%3[bB])jsessionid=[^/?;]+','',p.path)
            query={'jobOfferId':ident}
            if q.get('j'):query['j']=q['j'][0]
            target=urlunparse(p._replace(path=path,params='',query=urlencode(query),fragment=''))
        else:
            match=re.search(r'/job/([^/]+)',p.path)
            if not match:continue
            ident=match.group(1)
        heading=anchor.select_one('h2,h3,h4,[itemprop="title"],.job-title,.position-title')
        lines=[clean_label(x) for x in anchor.get_text('\n',strip=True).splitlines()]
        title=clean_label(heading.get_text(' ',strip=True)) if heading else next((x for x in lines if usable_title(x) and ROLE.search(x)), '')
        card=anchor
        for parent in list(anchor.parents)[:4]:
            if parent.name in ('body','html','main'):break
            distinct=set()
            for a in parent.select('a[href]'):
                link=urlparse(urljoin(url,a['href']));qs=parse_qs(link.query)
                if host=='jobboerse.iabg.de' and qs.get('jobOfferId'):distinct.add(qs['jobOfferId'][0])
                elif re.search(r'/job/([^/]+)',link.path):distinct.add(re.search(r'/job/([^/]+)',link.path).group(1))
            if len(distinct)>1:break
            card=parent
            if not title:
                heads=[clean_label(x.get_text(' ',strip=True)) for x in parent.select('h2,h3,h4,.job-title,.position-title')]
                valid=[x for x in heads if usable_title(x) and ROLE.search(x)]
                if len(set(valid))==1:title=valid[0]
        loc=card.select_one('[itemprop="jobLocation"],.job-location,.jobLocation,.location,.position-location,[data-ph-at-id="job-location"],[data-testid="job-location"]')
        loc_text=loc.get_text(' ',strip=True) if loc else ''
        # IABG table rows can explicitly label their workplace.
        if not loc_text:
            for label in card.select('dt,th,label'):
                if label.get_text(' ',strip=True).lower().rstrip(':') in ('standort','standorte','location','locations','arbeitsort'):
                    value=label.find_next_sibling(['dd','td','span'])
                    if value:loc_text=value.get_text(' ',strip=True);break
        # Never turn a filtered search or a "+ more" badge into a confirmed town.
        jobs.append(dict(title=title,location=loc_text,url=target,description='',posted_date='',
                         _job_id=ident,_extraction='priority_card_unverified',**({'_needs_title':True} if not title else {})))
    return dedupe(jobs)

def public_search_form(html,url):
    """Only an explicit same-host IABG search form; never login/application forms."""
    if urlparse(url).hostname!='jobboerse.iabg.de':return None
    soup=BeautifulSoup(html,'html.parser')
    for form in soup.find_all('form',action=True):
        action=urljoin(url,form['action']);p=urlparse(action)
        if p.hostname!='jobboerse.iabg.de' or not re.search(r'/searchJobOffers(?:Quick)?\.do(?:;|$)',p.path):continue
        if form.select('input[type="password"],input[type="file"]'):continue
        fields=[]
        for tag in form.select('input[name],select[name]'):
            if tag.name=='select':
                selected=tag.find('option',selected=True)
                if selected:fields.append((tag['name'],selected.get('value','')))
                continue
            kind=tag.get('type','text').lower()
            if kind in ('submit','button','image','reset'):continue
            if kind in ('checkbox','radio') and not tag.has_attr('checked'):continue
            fields.append((tag['name'],tag.get('value','')))
        return action,form.get('method','get').lower(),fields
    return None

def scrape_priority_board(name,url,get):

    return scrape_html_board(name,url,get)

def priority_detail_location(html,url):
    """Explicit label/value pairs on a priority vacancy page, never body/HQ text."""
    p=urlparse(url)
    if p.hostname not in ('jobboerse.iabg.de','careers.allianz.com','jobs.infineon.com'):return ''
    if not (re.search(r'/job/[^/]+',p.path) or parse_qs(p.query).get('jobOfferId')):return ''
    soup=BeautifulSoup(html,'html.parser')
    for tag in soup(['nav','header','footer','script','style']):tag.decompose()
    for label in soup.select('dt,th,label,span,div'):
        if label.get_text(' ',strip=True).casefold().rstrip(':') not in ('standort','standorte','arbeitsort','location','locations'):continue
        value=label.find_next_sibling(['dd','td','span','div'])
        if value:
            text=value.get_text(' ',strip=True)
            if 1<=len(text)<=250 and not value.find(['form','button','h1','h2']):return text
    return ''


def supplemental_cards(html,url):
    """Public HRlab and Personio card readers; no guessed locations or dates."""
    p=urlparse(url);host=p.hostname or ''
    personio=bool(re.fullmatch(r'[a-z0-9-]+\.jobs\.personio\.(?:de|com)',host))
    hrlab=host=='app.hrlab.de'
    if not (personio or hrlab):return []
    soup=BeautifulSoup(html,'html.parser');out=[]
    for a in soup.select('a[href]'):
        target=urljoin(url,a['href']);q=urlparse(target)
        if q.hostname!=host:continue
        if personio and not re.fullmatch(r'/job/\d+/?',q.path):continue
        if hrlab and not re.fullmatch(r'/(?:de|en)/jobs/[^/]+/\d+/?',q.path):continue
        heading=a.select_one('h2,h3,h4,[itemprop="title"],.job-title')
        lines=[clean_label(x) for x in a.get_text('\n',strip=True).splitlines()]
        title=clean_label(heading.get_text(' ',strip=True)) if heading else next((x for x in lines if usable_title(x) and ROLE.search(x)), '')
        card=a
        for parent in list(a.parents)[:3]:
            if parent.name in ('body','html','main'):break
            links={urlparse(urljoin(url,b['href'])).path for b in parent.select('a[href]') if re.search(r'/job/\d+|/jobs/[^/]+/\d+',urlparse(urljoin(url,b['href'])).path)}
            if len(links)>1:break
            card=parent
            if not title:
                heads=[clean_label(x.get_text(' ',strip=True)) for x in parent.select('h2,h3,h4,.job-title')]
                heads=[x for x in heads if usable_title(x) and ROLE.search(x)]
                if len(set(heads))==1:title=heads[0]
        loc=card.select_one('[itemprop="jobLocation"],.location,.job-location,[data-testid="job-location"]')
        out.append(dict(title=title,location=loc.get_text(' ',strip=True) if loc else '',url=target,description='',posted_date='',_extraction='supplemental_card_unverified',**({'_needs_title':True} if not title else {})))
    return dedupe(out)


# ========================================================================
# public_boards: consolidated implementation
# ========================================================================
"""Public career-board readers. No authentication, stealth, CAPTCHA solving or guessed private API.
Limits and stopping reasons are recorded; extraction is not a coverage certificate.
"""
import json,logging,re,time
from src.runtime import company_options, page_limit
import requests
from src.diagnostics import checkpoint, request_timeout, current_budget
from urllib.parse import urlparse,urljoin,parse_qs,urlencode,urlunparse
from bs4 import BeautifulSoup
from src.extraction import html_jobs, structured_jobs, plain, dedupe, usable_title, ROLE, clean_label, resolve_job_url, document_base, location as structured_location, is_vacancy_url
logger=logging.getLogger('job_scraper.ats')
MAX_PAGES=200
SOURCE_SECONDS=150
BROWSER_SECONDS=75


def job_url(url):
    p=urlparse(str(url));q=parse_qs(p.query)
    return is_vacancy_url(url) and bool(re.search(r'/(?:jobs?|positions?|postings?|vacancies|o|stellenanzeige|job-posting)/[^/]+|/companies/[^/]+/\d+[-/]',p.path,re.I) or any(k.lower() in ('jobofferid','jobid','job_id','postingid','career_job_req_id') for k in q))


def json_records(data,url,collection=False,depth=0):
    """Read only recognisable job records, not arbitrary titles in page configuration."""
    if depth>24:return []
    out=[]
    if isinstance(data,list):
        for item in data:out.extend(json_records(item,url,collection,depth+1))
    elif isinstance(data,dict):
        title=data.get('jobTitle') or data.get('job_title') or data.get('title') or (data.get('name') if collection else '')
        target=data.get('jobUrl') or data.get('job_url') or data.get('absolute_url') or data.get('public_url') or data.get('url')
        if collection and not target and urlparse(url).hostname in ('jobs.infineon.com','jobs.siemens.com','www.jobs.siemens.com') and data.get('id'):
            ident=str(data['id'])
            if re.fullmatch(r'\d+',ident):target=urljoin(url,'/careers/job/'+ident)
        if isinstance(title,str) and usable_title(clean_label(title)) and isinstance(target,str):
            target=resolve_job_url(url,target)
            if job_url(target) and (collection or data.get('jobId') or data.get('jobSeqNo')):
                locations=[]
                for key in ('location','locations','multi_location','multi_location_array'):
                    val=data.get(key)
                    if isinstance(val,str):locations.append(val)
                    elif isinstance(val,dict):
                        text=structured_location(val) or ', '.join(str(val[k]) for k in ('city','state','country') if isinstance(val.get(k),str))
                        if text:locations.append(text)
                    elif isinstance(val,list):
                        for loc in val:
                            if isinstance(loc,str):locations.append(loc)
                            elif isinstance(loc,dict):
                                text=loc.get('location') or ', '.join(str(loc[k]) for k in ('city','state','country') if isinstance(loc.get(k),str))
                                if isinstance(text,str) and text:locations.append(text)
                if not locations:
                    locations=[', '.join(str(data[k]) for k in ('city','state','country') if isinstance(data.get(k),str))]
                # No update timestamp, first-seen date or epoch guess is used as a posting date.
                date_field=next((k for k in ('datePosted','publishedAt','postedDate','postingDate') if isinstance(data.get(k),str)), '')
                date=data.get(date_field,'')
                out.append(dict(title=clean_label(title),location='; '.join(dict.fromkeys(filter(None,locations))),url=target,
                                description=plain(data.get('description') or data.get('jobDescription') or ''),posted_date=date,_date_raw=date,_date_field=date_field or 'none',_date_kind='publication',_date_source=url,_extraction='public_embedded_jobs'))
        for key,value in data.items():
            if isinstance(value,(dict,list)):
                out.extend(json_records(value,url,str(key).lower() in ('jobs','positions','jobpostings','joblist','joboffers'),depth+1))
    return dedupe(out)


def embedded_jobs(html,url):
    soup=BeautifulSoup(html,'html.parser');out=[];decoder=json.JSONDecoder()
    for script in soup.find_all('script'):
        text=script.string or script.get_text()
        if not text or len(text)>8_000_000:continue
        if script.get('type') in ('application/json','application/ld+json'):
            try:out.extend(json_records(json.loads(text),url))
            except (ValueError,TypeError):pass
        # JSON assignments only. Never eval JavaScript or follow embedded instructions.
        for match in re.finditer(r'(?:phApp\.ddo|window\.(?:PCS|__INITIAL_STATE__|__PRELOADED_STATE__|__NEXT_DATA__))\s*=\s*',text):
            try:data,_=decoder.raw_decode(text[match.end():].lstrip());out.extend(json_records(data,url))
            except (ValueError,TypeError):continue
    return dedupe(out)


def card_location(anchor):
    for parent in list(anchor.parents)[:4]:
        if parent.name in ('html','body','main'):break
        titles=parent.select('a[href*="/job/"],a[href*="jobOfferId="]')
        distinct={a.get('href') for a in titles}
        if len(distinct)>2:break
        tag=parent.select_one('[itemprop="jobLocation"],.job-location,.jobLocation,.jobLocationText,.job-location-label,.location,.jobGeoLocation,[data-ph-at-id="job-location"]')
        if tag:return tag.get_text(' ',strip=True)
    return ''




def coverage_cards(html, url):
    """Explicit public cards for Avature and employer-owned Ashby embeds.

    No location is inferred from the employer, search query or headquarters.
    """
    host=urlparse(url).hostname or ''
    if host not in ('jobs.siemens.com','jobs.siemens-energy.com','www.yoummday.com','yoummday.com','jobs.infineon.com'):
        return []
    soup=BeautifulSoup(html,'html.parser');jobs=[]
    for anchor in soup.select('a[href]'):
        target=urljoin(url,anchor['href']);parsed=urlparse(target)
        if parsed.hostname!=host:continue
        is_avature=host.startswith('jobs.siemens') and bool(re.search(r'/JobDetail/(?:[^/?]+/)?\d+(?:/|$)',parsed.path,re.I))
        is_embed=host.endswith('yoummday.com') and bool(parse_qs(parsed.query).get('ashby_jid'))
        is_infineon=host=='jobs.infineon.com' and bool(re.search(r'/careers/job/\d+',parsed.path))
        if not (is_avature or is_embed or is_infineon):continue
        heading=anchor.select_one('h2,h3,h4,[itemprop="title"]')
        lines=[clean_label(x) for x in anchor.get_text('\n',strip=True).splitlines() if clean_label(x)]
        title=clean_label(heading.get_text(' ',strip=True)) if heading else (lines[0] if lines else '')
        if not usable_title(title):continue
        card=anchor
        for parent in list(anchor.parents)[:5]:
            if parent.name in ('body','html','main'):break
            targets={urljoin(url,a['href']) for a in parent.select('a[href]') if re.search(r'/JobDetail/',a['href'],re.I) or 'ashby_jid=' in a['href']}
            if len(targets)>1:break
            if targets:card=parent
        location=''
        explicit=card.select_one('[itemprop="jobLocation"],.location,.job-location,[data-ph-at-id="job-location"],[data-testid="job-location"]')
        if explicit:location=explicit.get('content') or explicit.get_text(' ',strip=True)
        if not location and is_infineon and len(lines)>1:
            candidate=lines[1]
            if not re.match(r'^(?:HRC|Job.?ID|Hybrid|Remote|On.?site|Multiple locations)',candidate,re.I) and len(candidate)<=150:location=candidate
        if not location:
            for line in card.get_text('\n',strip=True).splitlines():
                if is_avature and re.search(r'Job[- ]?ID\s*:',line,re.I):
                    candidate=re.split(r'\s*[•|]\s*Job[- ]?ID\s*:',line,flags=re.I)[0].strip()
                    if candidate and candidate!=line and not re.search(r'multiple locations|mehrere standorte',candidate,re.I):location=candidate
                elif is_embed and '•' in line:
                    pieces=[v.strip() for v in line.split('•')]
                    # Employer cards explicitly use department / workplace / employment / mode.
                    if len(pieces)>=3:location=pieces[1]
        jobs.append(dict(title=title,url=target,location=location,description='',posted_date='',_location_source='explicit_public_card' if location else '',_extraction='avature_public_card' if is_avature else 'infineon_public_card' if is_infineon else 'employer_ashby_embed_card'))
    return dedupe(jobs)


def explicit_detail_locations(html):
    """Read only explicitly labelled vacancy locations, never whole-body city hits."""
    soup=BeautifulSoup(html,'html.parser');values=[]
    for tag in soup.select('nav,header,footer,script,style'):tag.decompose()
    selectors='[itemprop="jobLocation"],.job-location,.jobLocation,.job-location-label,[data-ph-at-id="job-location"],[data-testid="job-location"],.job-detail-location'
    for node in soup.select(selectors):
        text=clean_label(node.get('content') or node.get_text(' ',strip=True))
        if text and len(text)<=250:values.append(text)
    for label in soup.select('dt,th,label,span'):
        if clean_label(label.get_text(' ',strip=True)).casefold().rstrip(':') not in ('standort','standorte','arbeitsort','location','locations','city','stadt'):continue
        value=label.find_next_sibling(['dd','td','span','div','ul'])
        if value:
            text=clean_label(value.get_text(' ',strip=True))
            if text and len(text)<=250 and not value.find(['form','button','h1','h2']):values.append(text)
    return '; '.join(dict.fromkeys(values))


def next_public_control(context):
    """Choose one visible, enabled next/load-more element, not a multi-match locator."""
    selectors=['a[rel="next"]','button[title*="next" i]','a[title*="next" i]','button[aria-label*="next" i]','a[aria-label*="next" i]','[role="button"][aria-label*="next" i]',
               'button[aria-label*="nächste" i]','a[aria-label*="nächste" i]',
               'button:has-text("Load more")','button:has-text("Show more jobs")','button:has-text("Mehr Stellen")',
               'button:has-text("Show more jobs")','button:has-text("Mehr Jobs anzeigen")','button:has-text("Mehr laden")','button:has-text("Next")','a:has-text("Next")',
               'button:has-text("Weiter")','a:has-text("Weiter")','a:has-text("Nächste")']
    for selector in selectors:
        try:
            found=context.locator(selector)
            for i in range(min(found.count(),12)):
                item=found.nth(i)
                if item.is_visible() and item.is_enabled() and item.get_attribute('aria-disabled')!='true' and not item.get_attribute('disabled'):
                    return item
        except Exception:continue
    return None


def coverage_source(name, url):
    """Verified public listing routes; no guessed API tokens or workplace claims."""
    host=urlparse(url).hostname or '';name=name.casefold().strip()
    if name=='siemens' and host=='jobs.siemens.com':return 'https://jobs.siemens.com/de_DE/externaljobs/SearchJobs'
    if name=='yoummday' and (host.endswith('.personio.de') or host.endswith('.personio.com') or host in ('www.yoummday.com','yoummday.com')):return 'https://www.yoummday.com/de/jobs/'
    if name=='allianz' and host=='careers.allianz.com' and 'search-results' not in urlparse(url).path:return 'https://careers.allianz.com/global/en/search-results'
    return None


def employer_cards(html, url):
    """Read one vacancy card/row at a time. Never infer a workplace from HQ."""
    host=urlparse(url).hostname or ''
    success='hensoldt' in host or 'successfactors' in host
    quantum=host=='career.quantum-systems.com'
    if not (success or quantum):return []
    soup=BeautifulSoup(html,'html.parser');out=[]
    for a in soup.select('a[href]'):
        target=urljoin(url,a['href']);path=urlparse(target).path
        if urlparse(target).hostname!=host or not re.search(r'/(?:job|o)/[^/]+',path):continue
        title=clean_label(a.get_text(' ',strip=True))
        if not usable_title(title):continue
        card=None
        for parent in list(a.parents)[:6]:
            if parent.name in ('html','body','main','section'):break
            ids={canonical_job_url(urljoin(url,b['href'])) for b in parent.select('a[href]') if re.search(r'/(?:job|o)/[^/]+',urlparse(urljoin(url,b['href'])).path)}
            if len(ids)>1:break
            card=parent
            if parent.name=='tr':break
        if card is None:continue
        loc='';date=''
        node=card.select_one('[itemprop="jobLocation"],.jobLocation,.job-location,.job-location-label,[data-testid="job-location"],[data-testid="offer-location"],[data-label="Location"],[data-label="Locations"]')
        if node:loc=clean_label(node.get('content') or node.get_text(' ',strip=True))
        if card.name=='tr' and not loc:
            table=card.find_parent('table');heads=[h.get_text(' ',strip=True).casefold() for h in table.select('thead th')] if table else []
            cells=card.find_all(['td','th'],recursive=False)
            for i,label in enumerate(heads):
                if label in ('location','locations','standort','standorte') and i<len(cells):loc=cells[i].get_text(' ',strip=True)
        node=card.select_one('[itemprop="datePosted"],.jobDate,.job-date,time[datetime]')
        if node:date=node.get('content') or node.get('datetime') or node.get_text(' ',strip=True)
        if not date and success:
            lines=card.get_text('\n',strip=True).splitlines()
            date=next((x.strip() for x in lines if re.fullmatch(r'[A-Z][a-z]{2} \d{1,2}, \d{4}',x.strip())), '')
        if not loc:
            lines=[x.strip() for x in card.get_text('\n',strip=True).splitlines() if x.strip()]
            for i,label in enumerate(lines[:-1]):
                if label.casefold().rstrip(':') in ('locations','location','standort','standorte'):loc=lines[i+1];break
        out.append(dict(title=title,location=loc,url=target,description='',posted_date=date,
                        _date_raw=date,_date_field='explicit_card_date' if date else 'none',_date_kind='publication',_date_source=url,
                        _location_source='explicit_employer_card' if loc else '',_extraction='employer_card_unverified'))
    return dedupe(out)

from src.extraction import canonical_job_url

def extract_page(html,url):


    out=employer_cards(html,url)+structured_jobs(html,url)+embedded_jobs(html,url)+bmw_cards(html,url)+priority_cards(html,url)+coverage_cards(html,url)
    soup=BeautifulSoup(html,'html.parser');base=document_base(soup,url)
    for a in soup.find_all('a',href=True):
        target=resolve_job_url(base,a['href']);q=parse_qs(urlparse(target).query)
        is_iabg=urlparse(target).hostname=='jobboerse.iabg.de' and 'jobOfferId' in q
        is_mtu=urlparse(target).hostname in ('www.mtu.de','mtu.de') and bool(re.search(r'/(?:stellenanzeige|job-posting)/[^/]+',urlparse(target).path))
        is_card=a.get('class') and any(x in ('jobTitle-link','job-title-link') for x in a.get('class',[]))
        if not (is_iabg or is_card or is_mtu) or not job_url(target):continue
        title=clean_label(a.get_text(' ',strip=True))
        if is_mtu:
            heading=a.select_one('h2,h3,h4,[itemprop="title"]')
            title=clean_label(heading.get_text(' ',strip=True) if heading else a.get_text('\n',strip=True).split('\n')[0])
            title=re.sub(r'\s+New$', '', title)
        if not usable_title(title):continue
        # Session identifiers are not stable vacancy IDs; keep the actual offer ID.
        if is_iabg:
            parsed=urlparse(target);path=re.sub(r'(?:;|%3[Bb])jsessionid=[^/?;]+','',parsed.path)
            target=urlunparse(parsed._replace(path=path,params='',query=urlencode({'jobOfferId':q['jobOfferId'][0]})))
        out.append(dict(title=title,location=card_location(a),url=target,description='',posted_date='',_extraction='public_board_card_unverified'))

    combined=dedupe(out+html_jobs(html,url)+supplemental_cards(html,url))
    for job in combined:
        parsed=urlparse(job['url']);query=parse_qs(parsed.query)
        if parsed.hostname=='jobboerse.iabg.de' and query.get('jobOfferId'):
            path=re.sub(r'(?:;|%3[Bb])jsessionid=[^/?;]+','',parsed.path)
            job['url']=urlunparse(parsed._replace(path=path,params='',query=urlencode({'jobOfferId':query['jobOfferId'][0]})))
    for job in combined:
        parsed=urlparse(job['url']);q=parse_qs(parsed.query)
        if parsed.hostname=='jobboerse.iabg.de' and q.get('jobOfferId'):job['_job_id']=q['jobOfferId'][0]
        elif parsed.hostname in ('careers.allianz.com','jobs.infineon.com'):
            match=re.search(r'/job/([^/]+)',parsed.path)
            if match:job['_job_id']=match.group(1)
    combined=dedupe(combined)
    if urlparse(url).hostname=='careers.allianz.com':
        # Prefer public detail pages to duplicate Apply links for the same req ID.
        ids={re.search(r'/job/([^/]+)',urlparse(j['url']).path).group(1) for j in combined if re.search(r'/job/([^/]+)',urlparse(j['url']).path)}
        combined=[j for j in combined if not (parse_qs(urlparse(j['url']).query).get('career_job_req_id',[''])[0] in ids)]
    return checkpoint([j for j in combined if is_vacancy_url(j.get('url',''))])


def next_link(html,url):
    numeric=[]
    raw_offset=parse_qs(urlparse(url).query).get("startrow",["0"])[0]
    current_offset=int(raw_offset) if raw_offset.isdigit() else 0
    soup=BeautifulSoup(html,'html.parser')
    for a in soup.find_all('a',href=True):
        rel=a.get('rel',[]);label=clean_label(a.get('aria-label') or a.get('title') or a.get_text(' ',strip=True))
        if 'disabled' in a.get('class',[]) or a.get('aria-disabled')=='true':continue
        if 'next' in rel or re.fullmatch(r'(?:(?:(?:go to )?next(?: page)?|nächste(?: seite)?|naechste(?: seite)?|weiter)(?:\s*(?:>>|»|›|→))?|›|»|>)',label,re.I):
            target=urljoin(url,a['href'])
            if urlparse(target).hostname==urlparse(url).hostname and urlparse(target).scheme in ('http','https') and not a['href'].startswith(('#','javascript:')):return target
    for a in soup.select('a[href]'):
        if a.get('aria-disabled')=='true' or 'disabled' in a.get('class',[]):continue
        target=urljoin(url,a['href']);parsed=urlparse(target);query=parse_qs(parsed.query)
        if parsed.hostname!=urlparse(url).hostname or parsed.path!=urlparse(url).path:continue
        value=query.get('startrow',[''])[0]
        if value.isdigit() and int(value)>current_offset:numeric.append((int(value),target))
    return min(numeric)[1] if numeric else None


def _tag(jobs,stats):
    for j in jobs:j['_pagination']=dict(stats)
    return jobs


def scrape_html_board(name,url,get,max_pages=MAX_PAGES,seconds=SOURCE_SECONDS):
    """Follow actual public Next links; browser handles JS buttons if needed."""
    max_pages=page_limit(max_pages)
    started=time.monotonic();current=url;seen=set();jobs=[];cookies={};pages=0;reason='no_next_link_unverified';form_submitted=False;response=None
    while current and pages<max_pages and time.monotonic()-started<seconds:
        if current in seen:reason='repeated_url';break
        seen.add(current)
        response=get(current,cookies=cookies) if cookies else get(current)
        if response is None:reason='request_failed';break
        try:cookies.update(response.cookies.get_dict())
        except (AttributeError,TypeError):pass
        page_url=response.url or current;page_jobs=extract_page(response.text,page_url);pages+=1
        before=len(jobs);jobs=dedupe(jobs+page_jobs);checkpoint(jobs)
        if pages>1 and page_jobs and len(jobs)==before:reason='repeated_jobs';break
        if pages>1 and not page_jobs:
            reason='empty_page_unverified';break
        current=next_link(response.text,page_url)
        if not page_jobs and not current and not form_submitted:

            form=public_search_form(response.text,page_url)
            if form:
                action,method,fields=form;form_submitted=True
                if method=='get':
                    parsed=urlparse(action);query=parse_qs(parsed.query)
                    for key,value in fields:query[key]=[value]
                    candidate=urlunparse(parsed._replace(query=urlencode(query,doseq=True)))
                    if candidate not in seen:current=candidate
                elif method=='post':

                    posted=requests.post(action,data=fields,cookies=cookies,headers=HEADERS,timeout=request_timeout(20))
                    if posted.status_code>=400:
                        logger.warning('IABG public search form HTTP %s',posted.status_code)
                    else:
                        jobs=dedupe(jobs+extract_page(posted.text,posted.url or action));checkpoint(jobs)
                        cookies.update(posted.cookies.get_dict())
                        current=next_link(posted.text,posted.url or action)

        if not current and urlparse(page_url).hostname=='careers.allianz.com' and 'search-results' in urlparse(page_url).path and page_jobs:
            parsed=urlparse(page_url);query=parse_qs(parsed.query)
            # Public Allianz search uses ten-result pages; embedded related jobs must not inflate the offset.
            query['from']=[str(int(query.get('from',['0'])[0])+10)]
            current=urlunparse(parsed._replace(query=urlencode(query,doseq=True)))
        if not current and not page_jobs and pages==1:
            iframe_soup=BeautifulSoup(response.text,'html.parser')
            for iframe in iframe_soup.find_all('iframe',src=True):
                candidate=urljoin(page_url,iframe['src'])
                if urlparse(candidate).scheme=='https' and re.search(r'jobs|career|recruit|dvinci|onlyfy|softgarden|personio',urlparse(candidate).hostname or '',re.I):
                    current=candidate;break
        if not current and not page_jobs and pages==1:
            # Follow an actual public search link rather than guessing its route.
            soup=BeautifulSoup(response.text,'html.parser')
            for anchor in soup.find_all('a',href=True):
                if re.fullmatch(r'(?:search jobs|find jobs|stellen suchen|alle jobs(?: anzeigen)?|view all jobs|job search|stellensuche)',clean_label(anchor.get_text(' ',strip=True)),re.I):
                    candidate=urljoin(page_url,anchor['href'])
                    allowed_host=urlparse(candidate).hostname==urlparse(page_url).hostname or (urlparse(page_url).hostname=='careers.microsoft.com' and urlparse(candidate).hostname=='apply.careers.microsoft.com' and '/careers' in urlparse(candidate).path)
                    if allowed_host and candidate not in seen and not anchor['href'].startswith(('#','javascript:')):
                        current=candidate;break
        if not current and not page_jobs and pages==1 and urlparse(page_url).hostname=='careers.allianz.com' and '/de/de/' in page_url:
            current=page_url.replace('/de/de/','/global/en/',1)
    if current and pages>=max_pages:reason='page_limit'
    elif current and time.monotonic()-started>=seconds:reason='time_limit'
    stats={'reader':'public_html','pages':pages,'unique_candidates':len(jobs),'completion':reason}
    # Browser gets a separate bounded allowance, not the exhausted global fallback budget.
    # No login, stealth, certificate disabling or CAPTCHA handling is attempted.
    blocked_status=getattr(get,'last_status',lambda u:None)(url)
    if reason in ('no_next_link_unverified','request_failed') and blocked_status not in (401,403,429):
        remaining=current_budget().remaining() if current_budget() else BROWSER_SECONDS
        rendered,render_stats=browse_public_board(name,page_url if pages else url,seconds=min(BROWSER_SECONDS,remaining))
        jobs=dedupe(jobs+rendered);stats['browser']=render_stats;stats['unique_candidates']=len(jobs)
    if reason!='no_next_link_unverified':logger.warning('Board pagination %s: %s; retained %s candidates',name,reason,len(jobs))
    logger.warning('Board coverage unverified for %s: %s',name,json.dumps(stats))
    return _tag(jobs,stats)


def browse_public_board(name,url,seconds=BROWSER_SECONDS):
    started=time.monotonic();jobs=[];pages=0;reason='no_next_control_unverified';browser=None;search_submitted=False
    if seconds<=0:return [],{'pages':0,'completion':'source_budget_exhausted','unique_candidates':0}
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            try:
                page=browser.new_page();page.set_default_timeout(2500)
                network_jobs=[]
                def capture_public_jobs(resp):
                    try:
                        parsed=urlparse(resp.url)
                        if parsed.hostname!=urlparse(url).hostname:return
                        if not re.search(r'/api/apply/v2/jobs(?:/|$)|/widgets(?:/|$)|/jobs/search(?:/|$)',parsed.path):return
                        if resp.status!=200 or 'json' not in resp.headers.get('content-type',''):return
                        network_jobs.extend(json_records(resp.json(),url));checkpoint(network_jobs)
                    except Exception:
                        pass  # Optional public-feed observation must not abort DOM extraction.
                page.on('response',capture_public_jobs)
                response=page.goto(url,timeout=min(20000,int(seconds*1000)),wait_until='domcontentloaded')
                if response is not None and response.status>=400:
                    reason='http_'+str(response.status)
                    logger.warning('Public-board browser HTTP %s %s',response.status,url)
                    return [],{'pages':0,'completion':reason}
                page.wait_for_timeout(1000)
                previous=None;stagnant=0
                while pages<page_limit(MAX_PAGES) and time.monotonic()-started<seconds:
                    try:html=page.content()
                    except Exception:page.wait_for_timeout(300);html=page.content()
                    found=extract_page(html,page.url)
                    if not found and not search_submitted and urlparse(page.url).hostname=='jobboerse.iabg.de':

                        if public_search_form(html,page.url):
                            search_submitted=True
                            submits=page.locator('form[action*="searchJobOffers"] button[type="submit"],form[action*="searchJobOffers"] input[type="submit"]')
                            if submits.count() and submits.first.is_visible() and submits.first.is_enabled():
                                submits.first.click(timeout=2500);page.wait_for_timeout(700);continue
                    contexts=[page]
                    for frame in page.frames:
                        if frame==page.main_frame:continue
                        parsed_frame=urlparse(frame.url)
                        if parsed_frame.scheme!='https' or not re.search(r'jobs|career|recruit|dvinci|onlyfy|softgarden|personio',parsed_frame.hostname or '',re.I):continue
                        contexts.append(frame)
                        try:found.extend(extract_page(frame.content(),frame.url))
                        except Exception:pass
                    before=len(jobs);jobs=dedupe(jobs+network_jobs+found);checkpoint(jobs);pages+=1
                    signature=tuple(j['url'] for j in found)
                    stagnant=stagnant+1 if signature==previous else 0;previous=signature
                    if stagnant>=2:reason='repeated_page';break
                    control=None
                    for context in contexts:
                        control=next_public_control(context)
                        if control is not None:break
                    if control is None:
                        # Trigger ordinary lazy loading once; never assume this is full pagination.
                        page.evaluate('() => { const r=document.scrollingElement || document.documentElement; if(r) window.scrollTo(0,r.scrollHeight); }')
                        page.wait_for_timeout(700)
                        extra=extract_page(page.content(),page.url);jobs=dedupe(jobs+network_jobs+extra);checkpoint(jobs)
                        reason='no_next_control_unverified';break
                    try:
                        control.click(timeout=2500)
                    except Exception as exc:
                        reason='next_control_failed';logger.warning('Board pagination %s: next control failed; retained %s candidates: %s',name,len(jobs),str(exc)[:160]);break
                    # Allow ordinary asynchronous rendering to advance before
                    # concluding that the server repeated a page.
                    for wait_attempt in range(4):
                        page.wait_for_timeout(700)
                        try:
                            advanced=extract_page(page.content(),page.url)
                            for frame in contexts[1:]:advanced.extend(extract_page(frame.content(),frame.url))
                            advanced=dedupe(advanced+network_jobs);checkpoint(advanced)
                        except Exception:continue
                        if tuple(j['url'] for j in advanced)!=signature:break
                if time.monotonic()-started>=seconds:reason='time_limit'
                elif pages>=MAX_PAGES:reason='page_limit'
            finally:browser.close()
    except Exception as exc:
        reason='browser_error';logger.warning('Public-board browser failed for %s: %s',name,str(exc)[:250])
    return jobs,{'pages':pages,'completion':reason,'unique_candidates':len(jobs)}


def scrape_eightfold(name,url,domain,get,max_pages=500,seconds=SOURCE_SECONDS):
    """Public Eightfold route; runtime response schema and coverage remain checked.
    Tenant domain is explicit config, not inferred from a company display name.
    """
    if not domain or not re.fullmatch(r'[\w.-]+',domain):raise ValueError('Explicit Eightfold tenant domain required')
    host=urlparse(url)
    if host.scheme!='https' or not host.hostname:raise ValueError('HTTPS career board required')
    endpoint=urlunparse(host._replace(path='/api/apply/v2/jobs',params='',query='',fragment=''))
    max_pages=page_limit(max_pages)
    start=0;jobs=[];seen=set();pages=0;total=None;reason='empty_page';began=time.monotonic()
    while pages<max_pages and time.monotonic()-began<seconds:
        resp=get(endpoint,params={'domain':domain,'start':start,'num':10})
        if resp is None:reason='request_failed';break
        try:data=resp.json()
        except (ValueError,TypeError):reason='invalid_json';break
        if not isinstance(data,dict) or not isinstance(data.get('positions'),list):reason='unexpected_schema';break
        positions=data['positions'];pages+=1
        if isinstance(data.get('count'),int):total=data['count']
        if not positions:
            reason='empty_page'
            if total is not None and start<total:reason='early_empty_page'
            break
        found=json_records({'positions':positions},url);new=[j for j in found if j['url'] not in seen]
        if not new:reason='repeated_or_unreadable_page';break
        for j in new:j['_extraction']='eightfold_public_api';seen.add(j['url'])
        jobs.extend(new);checkpoint(jobs);start+=len(positions)
        if total is not None and total>10 and len(jobs)>=total:
            reason='reported_total_reached';break
    if pages>=max_pages:reason='page_limit'
    elif time.monotonic()-began>=seconds:reason='time_limit'
    if reason=='empty_page' and total is not None and len(jobs)<total:
        reason='count_mismatch'
    stats={'reader':'eightfold_public_api','pages':pages,'advertised_total':total,'unique_candidates':len(jobs),'completion':reason}
    if reason not in ('empty_page','reported_total_reached') or not jobs:
        logger.warning('Eightfold pagination %s: %s',name,json.dumps(stats))
        # Recover incomplete API pages too, not just an entirely empty API.
        rendered=scrape_html_board(name,url,get)
        browser_stats=next((j.get('_pagination') for j in rendered if j.get('_pagination')), {})
        jobs=dedupe(jobs+rendered);checkpoint(jobs)
        stats['fallback']=browser_stats;stats['unique_candidates']=len(jobs)
    return _tag(jobs,stats)


# ========================================================================
# ats_scrapers: consolidated implementation
# ========================================================================
"""
ATS (Applicant Tracking System) scrapers.

Rather than writing fragile, one-off HTML scraping code for every single
company, this module targets the handful of job-board *platforms* that
most companies' career pages actually run on. Several of these expose
plain JSON (or XML) endpoints that are far more stable than scraping
rendered HTML, so we prefer those wherever possible.

Supported platforms:
  - greenhouse       (boards-api.greenhouse.io)
  - lever            (api.lever.co)
  - smartrecruiters  (api.smartrecruiters.com)
  - personio         ({company}.jobs.personio.de/xml)
  - workday          (myworkdayjobs.com CXS API)
  - generic          (best-effort HTML fallback, keyword-based)
  - headless         (last-resort: real Chromium render via Playwright,
                      only for results the fast path found suspiciously
                      thin — time-budgeted for the whole run)

A recurring real problem, found by hand multiple times (Sixt, Jet
Aviation, Hensoldt) before being fixed generally: a company's
config entry often stores its marketing homepage, not its actual job
board, on a completely different domain/subdomain. Rather than fix
these one at a time as they're noticed, discover_real_careers_url +
_recover_thin_result run for ANY company whose result looks
suspiciously thin, regardless of what platform its config claims —
fetching the stored URL and looking for the real job board (a known
platform's URL takes priority, using its own fast structured
scraper with the correctly-discovered token; otherwise, any
different-domain "jobs."/"careers." link found is still worth a
generic retry) before finally falling back to headless rendering as
the last, slowest resort.

Each function returns a list of dicts with the same shape:
  {
    "title": str,
    "location": str,
    "url": str,
    "description": str,   # plain text, used for scoring/flagging only
    "posted_date": str,   # ISO date if the platform provides one, else ""
  }

Every function is defensive: on any network error, unexpected response
shape, etc. it logs a warning and returns an empty list rather than
raising, so one broken company never stops the whole run.

IMPORTANT LESSON BAKED IN HERE (from a real run):
If a company's `ats:` is set explicitly (e.g. "workday") but the
`careers_url` doesn't actually match that platform (wrong tag, or a
generic corporate URL instead of the real ATS-hosted one), the
platform-specific scraper correctly returns nothing — but previously
the dispatcher stopped there instead of trying the generic HTML
fallback. That silently produced zero results for a lot of companies
whose `ats:` was guessed wrong. Fixed below: any explicit platform
scraper that comes back empty now falls through to scrape_generic()
as a last resort, same as "auto" already did.
"""



import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse, urljoin, parse_qs
import html as html_lib

# RECENCY_PATCH_V1
from src.matching import parse_posted, today_local

def normalize_fresh_posted(value):
    observed = today_local()
    parsed = parse_posted(value, today=observed, allow_relative=True)
    return parsed.isoformat() if parsed is not None and parsed <= observed else value

import requests
from src.diagnostics import checkpoint, request_timeout
from bs4 import BeautifulSoup

logger = logging.getLogger("job_scraper.ats")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PersonalJobTracker/1.0; "
        "+https://github.com/) research/personal-use job search bot"
    )
}
TIMEOUT = 20

# Words stripped from the end of a display name before guessing a board
# token. Names like "Databricks Zurich" or "Cisco Munich" otherwise slugify
# to "databrickszurich" / "ciscomunich", which is never a real board token.
_TRAILING_WORDS_TO_STRIP = [
    "munich", "muenchen", "zurich", "zuerich", "germany", "switzerland",
    "deutschland", "schweiz", "gmbh", "ag", "se", "group",
]


_GET_CACHE = {}
_HTTP_STATUS = {}

def _safe_get(url: str, **kwargs) -> requests.Response | None:
    """Cache within this run; retry one transient GET failure within the source budget.
    Never retry access denial, rate limits, certificate errors or permanent 4xx.
    """
    from src.diagnostics import current_budget
    key=(url,repr(sorted(kwargs.items())))
    if key in _GET_CACHE:
        if _GET_CACHE[key] is None:logger.warning('Cached unsuccessful request: %s',url)
        return _GET_CACHE[key]
    for attempt in range(2):
        try:
            options=dict(kwargs);headers=options.pop('headers',HEADERS)
            timeout=options.pop('timeout',TIMEOUT)
            if not isinstance(timeout,(int,float)):timeout=TIMEOUT
            resp=requests.get(url,headers=headers,timeout=request_timeout(timeout),**options)
            _HTTP_STATUS[url]=resp.status_code
            transient=resp.status_code in (500,502,503,504)
            if resp.status_code<400:
                _GET_CACHE[key]=resp;return resp
            logger.warning('GET %s -> HTTP %s',url,resp.status_code)
            if not transient or attempt:return _cache_failed(key)
        except requests.exceptions.SSLError as exc:
            logger.warning('GET %s certificate failure; no retry: %s',url,exc)
            return _cache_failed(key)
        except (requests.exceptions.Timeout,requests.exceptions.ConnectionError) as exc:
            logger.warning('GET %s transient request failure: %s',url,exc)
            if attempt:return _cache_failed(key)
        except requests.RequestException as exc:
            logger.warning('GET %s failed: %s',url,exc);return _cache_failed(key)
        budget=current_budget()
        if budget and budget.remaining()<3:
            logger.warning('GET retry skipped: insufficient source budget: %s',url)
            return _cache_failed(key)
        logger.warning('Retrying transient GET once after 1 second: %s',url)
        time.sleep(1)
    return _cache_failed(key)

def _cache_failed(key):
    _GET_CACHE[key]=None
    return None


_safe_get.last_status=lambda url:_HTTP_STATUS.get(url)

def stamp_date(job, raw, field, kind='publication', source=''):
    job.update(_date_raw=raw or '',_date_field=field,_date_kind=kind,_date_source=source or job.get('url',''),_date_observed_on=today_local().isoformat())
    return job

def _slugify(name: str) -> str:
    """Best-effort guess at a company's board token from its display name."""
    words = name.lower().split()
    while words and re.sub(r"[^a-z]", "", words[-1]) in _TRAILING_WORDS_TO_STRIP:
        words.pop()
    s = " ".join(words) if words else name.lower()
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def _iso(dt_obj: datetime) -> str:
    return dt_obj.date().isoformat()


# --------------------------------------------------------------------------
# Greenhouse
# --------------------------------------------------------------------------
def scrape_greenhouse(company_name: str, board_token: str = "") -> list[dict[str, Any]]:
    token = board_token or _slugify(company_name)
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    resp = _safe_get(url)
    if resp is None:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    jobs = []
    for job in data.get("jobs", []):
        desc_html = job.get("content", "") or ""
        desc_text = BeautifulSoup(desc_html, "html.parser").get_text(" ", strip=True)
        posted = ""
        raw_date = job.get("first_published") or ""
        if raw_date:
            try:
                posted = _iso(datetime.fromisoformat(raw_date.replace("Z", "+00:00")))
            except ValueError:
                posted = ""
        jobs.append(
            {
                "title": job.get("title", "").strip(),
                "location": (job.get("location") or {}).get("name", ""),
                "url": job.get("absolute_url", ""),
                "description": desc_text,
                "posted_date": posted,
            }
        )
        stamp_date(jobs[-1],raw_date,'first_published')
    return SourceResult(jobs,dict(reader='scrape_greenhouse',pages=1,raw_candidates=len(data.get('jobs',[])),advertised_total=len(data.get('jobs',[])),unique_candidates=len(jobs),completion='single_feed_complete'))


# --------------------------------------------------------------------------
# Lever
# --------------------------------------------------------------------------


class SourceResult(list):
    """Preserve feed diagnostics even when a source returns zero records."""
    def __init__(self, jobs=(), pagination=None):
        super().__init__(jobs)
        self.pagination=pagination or {}
        for job in self:job['_pagination']=dict(self.pagination)

def _public_location(item):
    values=item.get('locations') or item.get('location') or item.get('city') or ''
    if not isinstance(values,list):values=[values]
    result=[]
    for value in values:
        if isinstance(value,dict):
            text=value.get('location_str') or ', '.join(str(value.get(k) or '') for k in ('city','region','country') if value.get(k))
        else:text=str(value or '')
        if text and text not in result:result.append(text)
    return '; '.join(result)

def scrape_recruitee(company_name: str, board_token: str = '') -> list[dict[str, Any]]:
    token=(board_token or '').strip()
    if not re.fullmatch(r'[\w-]+',token):return []
    url=f'https://{token}.recruitee.com/api/offers/'
    response=_safe_get(url)
    if response is None:return []
    try:data=response.json()
    except (ValueError,TypeError):return []
    offers=data.get('offers') if isinstance(data,dict) else None
    if not isinstance(offers,list):return []
    jobs=[]
    for item in offers:
        if not isinstance(item,dict):continue
        title=plain(item.get('title'));target=item.get('careers_url') or item.get('url') or ''
        if not usable_title(title) or not target:continue
        target=urljoin(url,target)
        date=item.get('published_at') or ''
        jobs.append(dict(title=title,location=_public_location(item),url=target,description=plain(item.get('description')),posted_date=date,_date_raw=date,_date_field='published_at',_date_kind='publication',_date_source=url,_job_id=str(item.get('id') or ''),_extraction='recruitee_public_api'))
    jobs=dedupe(jobs)
    stats=dict(reader='recruitee_public_api',pages=1,raw_candidates=len(offers),advertised_total=len(offers),unique_candidates=len(jobs),completion='single_feed_complete')
    checkpoint(jobs)
    return SourceResult(jobs,stats)

def scrape_workable(company_name: str, board_token: str = '') -> list[dict[str, Any]]:
    """Public careers POST search; nextPage is sent back as token. No credentials."""
    token=(board_token or '').strip()
    if not re.fullmatch(r'[\w-]+',token):return []
    url=f'https://apply.workable.com/api/v3/accounts/{token}/jobs'
    jobs=[];seen_tokens=set();cursor=None;pages=0;raw=0;total=None;reason='page_limit'
    for _ in range(page_limit(30)):
        payload=dict(query='',location=[],department=[],worktype=[],remote=[])
        if cursor:payload['token']=cursor
        try:
            response=requests.post(url,json=payload,headers=HEADERS,timeout=request_timeout(TIMEOUT))
            if response.status_code>=400:
                reason='http_'+str(response.status_code);break
            data=response.json()
        except (requests.RequestException,ValueError,TypeError) as exc:
            logger.warning('Workable public search failed for %s: %s',company_name,exc)
            reason='request_failed';break
        pages+=1
        if not isinstance(data,dict) or not isinstance(data.get('results'),list):reason='schema_unrecognized';break
        items=data['results'];raw+=len(items)
        if isinstance(data.get('total'),int):total=data['total']
        before=len(jobs)
        for item in items:
            if not isinstance(item,dict):continue
            title=plain(item.get('title'));shortcode=item.get('shortcode')
            target=item.get('url') or (f'https://apply.workable.com/{token}/j/{shortcode}/' if shortcode else '')
            if not usable_title(title) or not target:continue
            date=item.get('published') or item.get('published_on') or ''
            job=dict(title=title,location=_public_location(item),url=urljoin(url,target),description=plain(item.get('description')),posted_date=date,_date_raw=date,_date_field='published',_date_kind='publication',_date_source=url,_job_id=str(shortcode or target),_extraction='workable_public_api')
            if shortcode:job['_detail_api_url']=f'https://apply.workable.com/api/v2/accounts/{token}/jobs/{shortcode}'
            jobs.append(job)
        jobs=dedupe(jobs);checkpoint(jobs)
        next_cursor=data.get('nextPage')
        if not next_cursor:
            reason='reported_total_reached' if total is not None and len(jobs)>=total else ('single_feed_complete' if total is None else 'count_mismatch')
            break
        if str(next_cursor) in seen_tokens or (items and len(jobs)==before):reason='repeated_page';break
        seen_tokens.add(str(next_cursor));cursor=next_cursor
    stats=dict(reader='workable_public_api',pages=pages,raw_candidates=raw,advertised_total=total,unique_candidates=len(jobs),completion=reason)
    return SourceResult(jobs,stats)


def scrape_lever(company_name: str, board_token: str = "") -> list[dict[str, Any]]:
    token = board_token or _slugify(company_name)
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    resp = _safe_get(url)
    if resp is None:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    jobs = []
    for job in data:
        desc = job.get("descriptionPlain") or job.get("description", "") or ""
        posted = ""
        created_ms = job.get("createdAt")
        if created_ms:
            try:
                posted = _iso(datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc))
            except (ValueError, OSError):
                posted = ""
        jobs.append(
            {
                "title": job.get("text", "").strip(),
                "location": (job.get("categories") or {}).get("location", ""),
                "url": job.get("hostedUrl", ""),
                "description": desc,
                "posted_date": posted,
            }
        )
        stamp_date(jobs[-1],created_ms,'createdAt','creation_unverified')
        jobs[-1]['posted_date']=''
    return SourceResult(jobs,dict(reader='scrape_lever',pages=1,raw_candidates=len(data),advertised_total=len(data),unique_candidates=len(jobs),completion='single_feed_complete'))


# --------------------------------------------------------------------------
# SmartRecruiters
# --------------------------------------------------------------------------
def scrape_smartrecruiters(company_name: str, board_token: str = "") -> list[dict[str, Any]]:
    from urllib.parse import quote
    token = board_token or company_name.replace(" ", "")
    base = f"https://api.smartrecruiters.com/v1/companies/{quote(token, safe='')}/postings"
    jobs, seen, offset = [], set(), 0
    for _ in range(page_limit(200)):
        resp = _safe_get(base, params={"limit": 100, "offset": offset})
        if resp is None:
            logger.warning("SmartRecruiters incomplete pagination at offset %s; retaining %s candidates", offset, len(jobs))
            return jobs
        try:
            data = resp.json()
            page = data.get("content", [])
            if not isinstance(page, list):raise ValueError("SmartRecruiters content must be a list")
        except (ValueError, TypeError, AttributeError) as exc:
            logger.warning("SmartRecruiters incomplete pagination at offset %s: %s; retaining %s candidates", offset, str(exc)[:200], len(jobs))
            return jobs
        if not page:
            if data.get("totalFound") is not None and offset < int(data["totalFound"]):
                logger.warning("SmartRecruiters incomplete pagination: retrieved %s of %s", offset, data["totalFound"])
            return jobs
        added = 0
        for job in page:
            ident = str(job.get("id") or job.get("uuid") or "")
            if not ident or ident in seen:
                continue
            seen.add(ident)
            added += 1
            # Fetch descriptions only for date/title survivors in main.py.
            loc = job.get("location") or {}
            desc = ""
            raw_date = job.get("releasedDate") or ""
            posted = ""
            if raw_date:
                try:
                    posted = _iso(datetime.fromisoformat(raw_date.replace("Z", "+00:00")))
                except ValueError:
                    pass
            candidate = job.get("jobAdUrl") or ""
            if not isinstance(candidate, str) or not candidate.startswith(("https://", "http://")):
                candidate = f"https://jobs.smartrecruiters.com/{quote(token, safe='')}/{quote(ident, safe='')}"
            jobs.append({"title": (job.get("name") or "").strip(),
                         "location": ", ".join(str(loc[k]) for k in ("city", "country") if loc.get(k)),
                         "url": candidate,
                         "description": BeautifulSoup(desc, "html.parser").get_text(" ", strip=True),
                         "posted_date": posted, "_extraction": "smartrecruiters_api",
                         "_detail_api_url": base + "/" + quote(ident, safe="")})
            stamp_date(jobs[-1],raw_date,'releasedDate')
        checkpoint(jobs)
        if not added:
            logger.warning("SmartRecruiters incomplete pagination: repeated page; retaining %s candidates", len(jobs))
            return jobs
        offset += len(page)
        total = data.get("totalFound")
        if total is not None and offset >= int(total):
            return jobs
    logger.warning("SmartRecruiters incomplete pagination: safety limit; retaining %s candidates", len(jobs))
    return jobs


# --------------------------------------------------------------------------
# Personio
# --------------------------------------------------------------------------
def _parse_personio_xml(resp, token: str, domain: str) -> list[dict[str, Any]]:
    """Shared XML parsing for scrape_personio's .de/.com attempts."""
    try:
        soup = BeautifulSoup(resp.content, "xml")
    except Exception:
        soup = BeautifulSoup(resp.content, "html.parser")
    jobs = []
    for position in soup.find_all("position"):
        name = position.find("name")
        office = position.find("office")
        job_id = position.find("id")
        created = position.find("createdAt") or position.find("created_at")
        link = f"https://{token}.jobs.personio.{domain}/job/{job_id.text}" if job_id else ""
        posted = ""
        if created and created.text:
            # Personio's createdAt is usually "YYYY-MM-DD HH:MM:SS" or just a date
            posted = created.text.strip()[:10]

        # Personio's feed includes full description sections (title +
        # HTML body per section, e.g. "Your tasks", "Your profile") —
        # this was previously left blank; concatenating it gives the
        # relevance scorer real text to work with instead of just the
        # job title, which is the single biggest lever we have for
        # more accurate scores on Personio-based postings.
        description_parts = []
        for desc_block in position.find_all("jobDescription"):
            value = desc_block.find("value")
            if value and value.text:
                text_only = BeautifulSoup(value.text, "html.parser").get_text(" ", strip=True)
                if text_only:
                    description_parts.append(text_only)
        description = " ".join(description_parts)

        jobs.append(
            {
                "title": name.text.strip() if name else "",
                "location": office.text.strip() if office else "",
                "url": link,
                "description": description,
                "posted_date": posted,
            }
        )
        published=position.find('publishedAt') or position.find('published_at')
        if published and published.text:
            jobs[-1]['posted_date']=published.text.strip()
            stamp_date(jobs[-1],published.text.strip(),published.name)
        else:
            stamp_date(jobs[-1],created.text.strip() if created else '',created.name if created else 'none','creation_unverified')
            jobs[-1]['posted_date']=''
    return SourceResult(jobs,dict(reader='_parse_personio_xml',pages=1,raw_candidates=len(soup.find_all('position')),advertised_total=len(soup.find_all('position')),unique_candidates=len(jobs),completion='single_feed_complete'))


def scrape_personio(company_name: str, board_token: str = "", preferred_domain: str = "de") -> list[dict[str, Any]]:
    """
    Personio hosts company job boards on BOTH {token}.jobs.personio.de
    AND {token}.jobs.personio.com, depending on the company's own
    account configuration — there's no way to know which one a given
    company uses without trying (found via STARK Defence, whose real
    board is on .com; the original version of this function only ever
    tried .de, which would have silently returned nothing for any
    .com-hosted company, not just this one). Tries .de first (the more
    common case for German-market companies), falls back to .com only
    if .de returns nothing.
    """
    token = board_token or _slugify(company_name)
    for domain in dict.fromkeys((preferred_domain,"de","com")):
        url = f"https://{token}.jobs.personio.{domain}/xml"
        resp = _safe_get(url)
        if resp is None:
            continue
        jobs = _parse_personio_xml(resp, token, domain)
        if jobs:
            return jobs

    for domain in dict.fromkeys((preferred_domain,'de','com')):
        board=f'https://{token}.jobs.personio.{domain}/?language=en'
        result=scrape_html_board(company_name,board,_safe_get)
        if result:return result
    return []


# --------------------------------------------------------------------------
# Workday
# --------------------------------------------------------------------------
_WORKDAY_URL_RE = re.compile(
    r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[a-z]{2}/)?((?!wday(?:/|$))[\w-]+)", re.I
)


def _discover_workday_url(careers_url: str) -> str | None:
    """
    Most companies' `careers_url` in config is their own vanity domain
    (e.g. careers.astrazeneca.com), not the actual myworkdayjobs.com
    URL the workday API regex needs — so the direct match in
    scrape_workday fails for the large majority of workday-tagged
    companies even when they genuinely run on Workday.

    This fetches the vanity URL and looks for the real Workday URL in
    two places: (1) where the request actually ends up after redirects
    (requests follows redirects by default, and many corporate career
    pages permanently redirect straight to their Workday tenant), and
    (2) anywhere in the page's raw HTML (a myworkdayjobs.com link is
    very often embedded even without a server-side redirect — e.g. an
    "Apply" button pointing at it, or a client-side JS redirect that
    still leaves the target URL sitting in the page source as a string).

    Returns the discovered myworkdayjobs.com URL, or None if nothing
    was found (a real signal that the company likely isn't on Workday
    at all, whatever the config guessed).
    """
    resp = _safe_get(careers_url, allow_redirects=True)
    if resp is None:return None
    if _WORKDAY_URL_RE.search(resp.url):return resp.url
    source = html_lib.unescape(resp.text).replace('\\/', '/')
    match = _WORKDAY_URL_RE.search(source)
    return match.group(0) if match else None


def scrape_workday(company_name: str, careers_url: str):
    from src.runtime import _OPTIONS
    m=_WORKDAY_URL_RE.match(careers_url)
    if not m:
        found=_discover_workday_url(careers_url)
        m=_WORKDAY_URL_RE.match(found or '')
    if not m:return []
    tenant,dc,site=m.groups()
    base=f'https://{tenant}.{dc}.myworkdayjobs.com'
    api=f'{base}/wday/cxs/{tenant}/{site}'
    options=_OPTIONS.get();queries=list(dict.fromkeys(options.get('search_queries') or ['']))
    jobs=[];searches=[];started=time.monotonic();blocked=False
    for query in queries:
        offset=0;total=None;pages=0;seen=set();reason='page_limit'
        for _ in range(page_limit(500)):
            if time.monotonic()-started>=float(options.get('listing_seconds',150)):
                reason='listing_time_limit';break
            try:
                resp=requests.post(api+'/jobs',json={'limit':20,'offset':offset,'searchText':query,'appliedFacets':{}},headers=HEADERS,timeout=request_timeout(TIMEOUT))
                if resp.status_code in (401,403,429):
                    _HTTP_STATUS[careers_url]=resp.status_code;reason='http_'+str(resp.status_code);blocked=True
                    logger.warning('Workday %s: HTTP %s; stopping without retry',company_name,resp.status_code);break
                resp.raise_for_status();data=resp.json();page=data.get('jobPostings')
                if not isinstance(page,list):raise ValueError('jobPostings must be a list')
            except (requests.RequestException,ValueError,TypeError,AttributeError) as exc:
                reason='request_or_schema_failure';logger.warning('Workday %s: %s',company_name,str(exc)[:200]);break
            pages+=1
            if isinstance(data.get('total'),int):total=data['total']
            if not page:
                reason='empty_page' if total is None or offset>=total else 'early_empty_page';break
            added=0
            for posting in page:
                path=posting.get('externalPath') or ''
                if not path or path in seen:continue
                seen.add(path);added+=1
                item=dict(title=plain(posting.get('title')),location=posting.get('locationsText') or '',url=base+'/'+site+path,
                          description='',posted_date=normalize_fresh_posted(posting.get('postedOn','')),
                          _detail_api_url=api+path,_extraction='workday_api')
                stamp_date(item,posting.get('postedOn',''),'postedOn');jobs.append(item)
            jobs=dedupe(jobs);checkpoint(jobs)
            if not added:reason='repeated_page';break
            offset+=len(page)
            if total is not None and offset>=total and not (total==20 and pages==1):reason='reported_total_reached';break
        searches.append(dict(query=query,pages=pages,advertised_total=total,unique_candidates=len(seen),completion=reason))
        if blocked or reason=='listing_time_limit':break
    complete=len(searches)==len(queries) and all(x['completion'] in ('reported_total_reached','empty_page') for x in searches)
    stats=dict(reader='workday_api',pages=sum(x['pages'] for x in searches),unique_candidates=len(jobs),
               scope='configured_searches' if queries!=[''] else 'entire_board',searches=searches,
               completion='all_pages_fetched' if complete else (searches[-1]['completion'] if searches else 'not_started'))
    if not complete:logger.warning('Workday incomplete coverage %s: %s',company_name,json.dumps(stats))
    return SourceResult(jobs,stats)
# --------------------------------------------------------------------------
# Description enrichment (fetch the job's OWN posting page)
# --------------------------------------------------------------------------
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


def fetch_description_fallback(url: str, max_chars: int = 3000) -> str:
    """
    For platforms that don't include description text in their list
    endpoint (SmartRecruiters, Workday, generic HTML), this fetches
    the job posting's OWN page directly and pulls out visible text.

    This is first-party (the actual posting, not a third-party
    aggregator like Glassdoor/Indeed) — those are unreliable for
    scraping (actively blocked, against their terms, and would need
    fuzzy title/company matching to even find the right listing).
    Fetching the posting's own URL avoids all of that: we already
    know it's the exact right page.

    Deliberately called only for jobs that already passed the title +
    location filter (see main.py) — a handful of extra requests for
    genuine candidates, not one per raw posting scraped.

    Returns "" on any failure, or for pages that are JS-rendered SPAs
    with no server-side text (a real, unavoidable limitation — same
    one documented for scrape_generic).
    """
    resp = _safe_get(url)
    if resp is None:
        return ""
    try:
        html = resp.text
        html = _SCRIPT_STYLE_RE.sub(" ", html)
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        return text[:max_chars]
    except Exception as exc:
        logger.warning("Description fallback failed for %s: %s", url, exc)
        return ""


# --------------------------------------------------------------------------
# Generic HTML fallback
# --------------------------------------------------------------------------
JOB_LINK_HINTS = re.compile(
    r"(job|career|position|vacan|stelle|karriere)", re.IGNORECASE
)


def scrape_generic(company_name, careers_url):

    resp = _safe_get(careers_url)
    if resp is None:
        return []
    if next_link(resp.text,resp.url or careers_url):
        return scrape_html_board(company_name,careers_url,_safe_get)
    return checkpoint(extract_page(resp.text, resp.url or careers_url))


# --------------------------------------------------------------------------
# Headless-browser fallback — for JS-rendered single-page career sites
# (Sixt, Google, Amazon, Apple, Microsoft, SAP, Oracle, and others like
# them) where scrape_generic's plain HTTP request only ever sees the
# page's pre-JavaScript "shell" (nav links, footer, social links) — the
# actual job cards get injected by JavaScript after the page loads in a
# real browser, so they're invisible to a plain request no matter how
# scrape_generic's link-extraction is tuned.
#
# Rather than hand-maintaining a fixed list of "known JS-rendered
# companies" (inevitably incomplete — this exact problem was found on
# Sixt's site by accident, not because it was on any such list), this
# triggers automatically whenever the normal pipeline (API scraper +
# scrape_generic) comes back suspiciously thin for a given company —
# a real, low-effort proxy for "this is probably a JS shell, not a
# real empty job board." That means it opportunistically helps
# WHICHEVER companies actually need it, in Munich, Singapore, a Swiss
# city, or a Dream City alike, rather than only the ones already
# known about.
#
# GUARDRAIL: rendering a real browser page is 5-10x slower than a
# plain HTTP request. To keep total run time predictable regardless of
# how many companies trigger this, cumulative headless time across the
# WHOLE run (all passes combined — see main.py's reset_headless_budget
# call) is capped by _HEADLESS_BUDGET_SECONDS. Once the budget is
# spent, remaining companies just keep whatever scrape_generic already
# found — same as before this feature existed, not worse.
# --------------------------------------------------------------------------
_HEADLESS_BUDGET_SECONDS = 900  # 15 minutes reserved for the whole run
_headless_time_used = 0.0
GENERIC_RESULT_SUSPICIOUSLY_LOW = 5  # fewer real-looking links than this -> probably a JS shell, worth a retry


def reset_headless_budget() -> None:
    """Call once per full run (see main.py) so the budget doesn't leak across runs."""
    global _headless_time_used
    _headless_time_used = 0.0
    _GET_CACHE.clear()
    _HTTP_STATUS.clear()


def headless_budget_remaining() -> float:
    """Seconds of headless-rendering time left before this run's cap kicks in."""
    return max(0.0, _HEADLESS_BUDGET_SECONDS - _headless_time_used)


def scrape_headless(company_name: str, careers_url: str) -> list[dict[str, Any]]:
    """
    Renders careers_url in a real (headless) Chromium browser and applies
    the SAME link-extraction heuristic as scrape_generic, just against
    the fully-JavaScript-rendered page instead of the raw pre-JS HTML.

    Returns [] immediately (no browser launched) if the run's headless
    time budget is already spent — see module docstring above — or on
    any error (missing browser install, page timeout, etc.), same
    defensive contract as every other scraper here.
    """
    global _headless_time_used

    if headless_budget_remaining() <= 0:
        logger.warning("Browser budget exhausted; coverage unverified for %s", company_name)
        return []

    start = time.monotonic()
    try:
        from playwright.sync_api import sync_playwright  # imported lazily: only needed if this path is used

        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(user_agent=HEADERS["User-Agent"])
                response = page.goto(careers_url, timeout=15000, wait_until="domcontentloaded")
                if response is not None and response.status >= 400:
                    logger.warning("Browser HTTP %s %s", response.status, page.url)
                    _headless_time_used += time.monotonic() - start
                    return []
                try:
                    page.wait_for_selector("a[href*='/job'], a[href*='/position'], a[href*='/posting']", timeout=5000)
                except Exception:
                    pass
                # Bounded scroll to trigger lazy-loaded cards; not full pagination.
                for _ in range(2):
                    page.evaluate("() => { const root = document.scrollingElement || document.documentElement || document.body; if (root) window.scrollTo(0, root.scrollHeight); }")
                    page.wait_for_timeout(500)
                html = None
                for attempt in range(3):
                    try:
                        html = page.content()
                        break
                    except Exception:
                        if attempt == 2:raise
                        page.wait_for_timeout(500)
                final_url = page.url
            finally:
                browser.close()
    except Exception as exc:
        logger.warning("Headless render failed for %s: %s", company_name, exc)
        _headless_time_used += time.monotonic() - start
        return []
    _headless_time_used += time.monotonic() - start


    return checkpoint(extract_page(html, final_url))


# --------------------------------------------------------------------------
# Generalized platform discovery — extends the fix originally built for
# Workday (see _discover_workday_url above) to EVERY known ATS platform,
# not just Workday. The root problem is the same one found repeatedly
# by hand — Sixt, Jet Aviation, and Hensoldt were all silently broken
# because their config entry stored the company's own vanity homepage
# instead of the real job board, on three completely different
# platforms (a JS-rendered shell, a wrong domain entirely, and
# SuccessFactors). Rather than keep finding these one at a time, this
# runs the same "fetch the vanity page, look for the real board" check
# for every company whose result looks thin — regardless of what its
# config says the platform is.
# --------------------------------------------------------------------------
def scrape_ashby(company_name, board_token=''):
    from src.extraction import ashby_jobs
    from urllib.parse import quote
    if not board_token:
        raise ValueError('Ashby board token must come from an actual board URL')
    resp = _safe_get('https://api.ashbyhq.com/posting-api/job-board/' + quote(board_token, safe=''))
    if resp is None:
        raise RuntimeError('Ashby public feed unavailable')
    data=resp.json()
    jobs=ashby_jobs(data)
    count=len(data.get('jobs',[]))
    return SourceResult(jobs,dict(reader='ashby_public_api',pages=1,raw_candidates=count,advertised_total=count,unique_candidates=len(jobs),completion='single_feed_complete'))

_PLATFORM_URL_PATTERNS = {
    "ashby": re.compile(r"https?://jobs\.ashbyhq\.com/([\w-]+)"),
    "greenhouse": re.compile(r"https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/((?!embed(?:/|$))[\w-]+)"),
    "lever": re.compile(r"https?://jobs\.lever\.co/([\w-]+)"),
    "recruitee": re.compile(r"https?://([\w-]+)\.recruitee\.com"),
    "workable": re.compile(r"https?://apply\.workable\.com/([\w-]+)"),
    "personio": re.compile(r"https?://([\w-]+)\.jobs\.personio\.(?:de|com)"),
    "smartrecruiters": re.compile(r"https?://(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)"),
}

_PLATFORM_SCRAPERS = {
    "ashby": scrape_ashby,
    "greenhouse": scrape_greenhouse,
    "lever": scrape_lever,
    "recruitee": scrape_recruitee,
    "workable": scrape_workable,
    "personio": scrape_personio,
    "smartrecruiters": scrape_smartrecruiters,
}

# A same-domain "careers" link on the vanity page itself isn't a real
# discovery (that's just the page we already have) — only a DIFFERENT
# domain counts as finding the real, separately-hosted job board.
_GENERIC_JOB_BOARD_HINT = re.compile(r"jobs\.|careers\.|/careers|/jobs", re.IGNORECASE)


def platform_from_url(url):
    parsed = urlparse(html_lib.unescape(url))
    host = (parsed.hostname or '').lower()
    if host in ('boards.greenhouse.io', 'job-boards.greenhouse.io', 'boards.eu.greenhouse.io', 'job-boards.eu.greenhouse.io'):
        parts = parsed.path.strip('/').split('/')
        token = parse_qs(parsed.query).get('for', [''])[0] if parts[0] == 'embed' else parts[0]
        if token and token != 'embed' and re.fullmatch(r'[\w-]+', token):
            return url, 'greenhouse', token
        return None, None, None
    for platform, pattern in _PLATFORM_URL_PATTERNS.items():
        match = pattern.search(url)
        if match:return url, platform, match.group(1)
    if _WORKDAY_URL_RE.search(url):return url, 'workday', None
    return None, None, None

def discover_real_careers_url(vanity_url):
    resp = _safe_get(vanity_url, allow_redirects=True)
    if resp is None:return None, None, None
    soup = BeautifulSoup(resp.text, 'html.parser')
    candidates = [resp.url]
    for tag in soup.find_all(['a', 'iframe', 'script']):
        target = tag.get('href') or tag.get('src')
        if target:candidates.append(urljoin(resp.url, html_lib.unescape(target)))
    candidates += re.findall(r'https?://[^\s"<>\']+', html_lib.unescape(resp.text))
    candidates=[c for c in candidates if urlparse(c).scheme in ('http','https') and not urlparse(c).path.lower().endswith(('.pdf','.png','.jpg','.js','.css')) and not any(x in urlparse(c).path.lower() for x in ('privacy','datenschutz','legal'))]
    for candidate in candidates:
        found = platform_from_url(candidate)
        if found[0]:return found
    for candidate in candidates:
        path = urlparse(candidate).path.lower()
        if candidate.rstrip('/') == vanity_url.rstrip('/'):continue
        if path.endswith(('.pdf','.png','.jpg','.js','.css')) or any(x in path for x in ('privacy','datenschutz','legal')):continue
        if _GENERIC_JOB_BOARD_HINT.search(candidate):return candidate, None, None
    return None, None, None


def _recover_thin_result(company_name: str, careers_url: str, current_result: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Last-resort recovery chain for a suspiciously thin result, tried in
    order of cost — cheapest first, headless (the slowest, budget-
    capped option) only as the final step:
      1. discover_real_careers_url — if the stored URL is actually a
         vanity homepage, find the real job board. If it's a known
         platform, use that platform's own fast structured scraper
         with the correctly-discovered token, not a guessed one.
      2. If a URL was discovered but isn't a recognized platform,
         retry the generic scraper against THAT url instead of the
         original — often enough on its own without ever needing
         headless rendering.
      3. If still thin, headless-render whichever URL is most likely
         correct (discovered if found, else the original one).
    Only runs any of this if current_result is already thin — a
    healthy result never pays any of these costs.
    """
    from src.extraction import usable_title, dedupe
    usable = sum(usable_title(j.get('title','')) and not j.get('_needs_title') for j in current_result)
    if usable >= GENERIC_RESULT_SUSPICIOUSLY_LOW:
        return current_result

    best_result = current_result
    best_url = careers_url

    discovered_url, platform, token = discover_real_careers_url(careers_url)
    if discovered_url:
        best_url = discovered_url
        try:
            if platform in _PLATFORM_SCRAPERS:
                candidate = _PLATFORM_SCRAPERS[platform](company_name, token or "")
            elif platform == "workday":
                candidate = scrape_workday(company_name, discovered_url)
            else:
                candidate = scrape_generic(company_name, discovered_url)
        except Exception as exc:
            logger.warning('Discovered board failed for %s; keeping existing candidates: %s',company_name,str(exc)[:200])
            candidate = []
        # Same reasoning as the headless case below: any NON-EMPTY
        # result from here wins outright, not just a bigger one — a
        # real platform's own API (or a retry against the actually-
        # correct URL) is trustworthy even if it happens to return
        # fewer raw entries than whatever junk the thin result had.
        if candidate:
            best_result = dedupe(best_result + candidate)

    if sum(usable_title(j.get('title','')) and not j.get('_needs_title') for j in best_result) >= GENERIC_RESULT_SUSPICIOUSLY_LOW:
        return best_result
    if headless_budget_remaining() <= 0:
        logger.warning("Browser budget exhausted; coverage unverified for %s", company_name)
        return best_result

    if _HTTP_STATUS.get(best_url) in (401,403,429):
        logger.warning('Access/rate limit for %s; no browser retry against the same URL',company_name)
        return best_result
    headless_result = scrape_headless(company_name, best_url)
    from src.extraction import dedupe
    return dedupe(best_result + headless_result)


# Kept as an alias: _maybe_headless_upgrade was the name used before
# this was generalized beyond just headless rendering. Same signature,
# same call sites — _recover_thin_result is the real implementation now.
_maybe_headless_upgrade = _recover_thin_result


# --------------------------------------------------------------------------
# Dispatcher
# --------------------------------------------------------------------------
def scrape_company(company: dict[str, Any]) -> list[dict[str, Any]]:
    if company.get('enabled',True) is not True:return []
    if company.get('detail_fetch_mode','matched_only') not in ('matched_only','all','none'):
        raise ValueError('detail_fetch_mode must be matched_only, all or none')
    with company_options(company):
        return _scrape_company_impl(company)

def _scrape_company_impl(company: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Dispatch to the right scraper based on the company's configured ATS.
    Any explicit platform scraper that returns nothing falls back to the
    generic HTML scraper before giving up — see module docstring. If
    THAT also comes back suspiciously thin, one more retry happens via
    a real headless browser (scrape_headless / _maybe_headless_upgrade)
    before finally giving up — see that section's docstring for why
    and how this is time-budgeted.
    """
    name = company["name"]
    careers_url = company["careers_url"]
    ats = (company.get("ats") or "auto").lower()
    token = company.get("board_token") or ""

    try:
        host=urlparse(careers_url).hostname or ''
        if host in ('freeletics.com','www.freeletics.com') and urlparse(careers_url).path.rstrip('/') in ('/en/careers','/de/careers'):
            logger.warning('Using verified Freeletics careers entry instead of stale /careers URL; extraction remains unverified')
            careers_url='https://www.freeletics.com/de/corporate/jobs/'
        if name.casefold().strip()=='hat.tec' and host in ('hattec.de','www.hattec.de'):

            result=scrape_html_board(name,careers_url,_safe_get)
            if result:return result
            logger.warning('HAT.tec official page empty: trying public HRlab employer board; coverage unverified')
            return scrape_html_board(name,'https://app.hrlab.de/de/jobs/hattec',_safe_get)
        if host=='app.hrlab.de':

            return scrape_html_board(name,careers_url,_safe_get)
        replacement=coverage_source(name,careers_url)
        if replacement:
            logger.info('Using verified public listing route for %s: %s',name,replacement)
            result=scrape_html_board(name,replacement,_safe_get)
            if not result and host=='careers.allianz.com':
                return scrape_html_board(name,'https://careers.allianz.com/de/de/search-results?keywords=M%C3%BCnchen',_safe_get)
            return result
        if ats == "jsonld_feed":

            return scrape_jsonld_feed(name,careers_url,_safe_get)
        if ats == "amazon":

            return scrape_amazon(name,careers_url,_safe_get)
        if urlparse(careers_url).hostname in ('jobboerse.iabg.de','careers.allianz.com'):

            result=scrape_priority_board(name,careers_url,_safe_get)
            if not result and host=='careers.allianz.com':
                logger.warning('Allianz listing empty; trying verified Munich keyword listing. This is targeted, not complete metropolitan coverage.')
                return scrape_html_board(name,'https://careers.allianz.com/de/de/search-results?keywords=M%C3%BCnchen',_safe_get)
            return result
        if ats == "public_board":

            return scrape_html_board(name, careers_url, _safe_get)
        if ats == "eightfold":

            return scrape_eightfold(name, careers_url, token, _safe_get)
        if ats == "ashby":
            match = _PLATFORM_URL_PATTERNS["ashby"].search(careers_url)
            return scrape_ashby(name, token or (match.group(1) if match else ""))
        if ats == "greenhouse":
            result = scrape_greenhouse(name, token)
            if result:
                return result
            return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
        if ats == "lever":
            result = scrape_lever(name, token)
            if result:
                return result
            return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
        if ats == "recruitee":
            result = scrape_recruitee(name, token)
            if result or isinstance(result,SourceResult):return result
            return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
        if ats == "workable":
            result = scrape_workable(name, token)
            if result or isinstance(result,SourceResult):return result
            return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
        if ats == "smartrecruiters":
            result = scrape_smartrecruiters(name, token)
            if result:
                return result
            return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
        if ats == "personio":
            preferred='com' if '.jobs.personio.com' in careers_url else 'de'
            result = scrape_personio(name, token, preferred)
            if result:return result

            return scrape_html_board(name,careers_url,_safe_get)
        if ats == "workday":
            result = scrape_workday(name, careers_url)
            if result or isinstance(result,SourceResult):
                return result
            return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
        if ats == "auto":
            for platform, pattern in _PLATFORM_URL_PATTERNS.items():
                found_url, found_platform, found_token = platform_from_url(careers_url)
                if found_platform == platform:
                    result = _PLATFORM_SCRAPERS[platform](name, token or found_token)
                    if result:
                        return result
                    return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
            if _WORKDAY_URL_RE.match(careers_url):
                result = scrape_workday(name, careers_url)
                if result:
                    return result
            return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
        # unknown value in companies.yaml -> fall back to generic
        return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
    except Exception as exc:  # belt-and-braces: never let one company kill the run
        logger.error("Unhandled error scraping %s: %s", name, exc)
        raise


def fetch_job_details(url):
    from src.extraction import structured_jobs, plain, detail_title
    resp = _safe_get(url)
    if resp is None:
        return {}, ''
    if '.myworkdayjobs.com' in (urlparse(url).hostname or '') and '/wday/cxs/' in urlparse(url).path:
        try:data=resp.json()
        except (ValueError,TypeError):return {},''
        info=data.get('jobPostingInfo') if isinstance(data,dict) else None
        if not isinstance(info,dict):return {},''
        locations=[info.get('location','')]
        for loc in info.get('additionalLocations') or []:
            locations.append(loc.get('location','') if isinstance(loc,dict) else loc)
        desc=plain(info.get('jobDescription'))
        details=dict(title=plain(info.get('title')),description=desc,location='; '.join(dict.fromkeys(x for x in locations if isinstance(x,str) and x)),_location_source='workday_detail_locations')
        date=info.get('startDate') or info.get('postedOn')
        if date:
            details['posted_date']=normalize_fresh_posted(date);stamp_date(details,date,'startDate' if info.get('startDate') else 'postedOn',source=url)
        return details,desc
    if urlparse(url).hostname=='apply.workable.com' and '/api/v2/accounts/' in urlparse(url).path:
        try:data=resp.json()
        except (ValueError,TypeError):return {},''
        if not isinstance(data,dict):return {},''
        description=plain(data.get('full_description') or data.get('description'))
        details=dict(title=plain(data.get('title')),description=description,location=_public_location(data))
        date=data.get('published') or data.get('published_on')
        if date:
            details['posted_date']=date
            stamp_date(details,date,'published',source=url)
        return details,description
    if urlparse(url).hostname == 'api.smartrecruiters.com':
        data = resp.json()
        sections = (data.get('jobAd') or {}).get('sections') or {}
        desc = ' '.join(plain(section.get('text','')) for section in sections.values() if isinstance(section,dict))
        loc = data.get('location') or {}
        return {'description': desc, 'location': ', '.join(str(loc[k]) for k in ('city','country') if loc.get(k)), 'posted_date': normalize_fresh_posted(data.get('releasedDate','')),'_date_raw':data.get('releasedDate',''),'_date_field':'releasedDate','_date_kind':'publication','_date_source':url}, desc
    jobs = structured_jobs(resp.text, resp.url or url)
    # Only trust one identifiable vacancy, not a board containing many jobs.
    details = jobs[0] if len(jobs) == 1 else {}
    if not jobs:
        recovered = detail_title(resp.text)
        if recovered:details = {'title': recovered}
    soup = BeautifulSoup(resp.text, 'html.parser')
    if not details.get('location'):
        explicit=explicit_detail_locations(resp.text)
        if explicit:details['location']=explicit;details['_location_source']='detail_explicit_locations'
    if not details.get('location'):
        location_tag = soup.select_one('[itemprop="jobLocation"],.job-location,.jobLocation,.job-location-label,[data-ph-at-id="job-location"],[data-testid="job-location"]')
        if location_tag:
            details['location'] = location_tag.get('content') or location_tag.get_text(' ', strip=True)
            details['_location_source']='detail_explicit_location'
        else:
            for label in soup.select('dt,th'):
                if label.get_text(' ', strip=True).lower().rstrip(':') in ('standort','standorte','location','locations','arbeitsort'):
                    value = label.find_next_sibling(['dd','td'])
                    if value:details['location'] = value.get_text(' ', strip=True);break
    if not details.get('location'):

        explicit=priority_detail_location(resp.text,resp.url or url)
        if explicit:details['location']=explicit;details['_location_source']='detail_explicit_location'
    if not details.get('posted_date'):
        posted = soup.select_one('[itemprop="datePosted"]')
        if posted:
            details['posted_date'] = posted.get('content') or posted.get('datetime') or posted.get_text(' ',strip=True)
            stamp_date(details,details['posted_date'],'datePosted',source=url)
    for tag in soup(['script','style','nav','footer','header']):tag.decompose()
    return details, soup.get_text(' ',strip=True)[:10000]
'''

# Module: src/source_quality.py
_SOURCES['source_quality'] = r'''"""Conservative extraction acceptance: unknown coverage never passes."""
from urllib.parse import urlparse
from src.extraction import usable_title, is_vacancy_url

COMPLETE = {'reported_total_reached', 'empty_page', 'all_pages_fetched', 'single_feed_complete'}

def valid_url(value):
    try:
        u = urlparse(value or '')
        return u.scheme in ('https', 'http') and bool(u.hostname) and not u.username and not u.password
    except (TypeError, ValueError):
        return False

def quality(jobs, pagination=None, expected_locations=None):
    stats = pagination or getattr(jobs, 'pagination', None) or next((j.get('_pagination') for j in jobs if j.get('_pagination')), {})
    count = int(stats.get('advertised_total') or 0)
    # Some adapters drop malformed entries. Keep the original denominator where available.
    raw = max(len(jobs), int(stats.get('raw_candidates') or 0), count)
    usable = sum(usable_title(j.get('title', '')) and valid_url(j.get('url')) and is_vacancy_url(j.get('url', '')) for j in jobs)
    located = sum(bool(str(j.get('location') or '').strip()) for j in jobs)
    title_ratio = usable / raw if raw else 0
    location_ratio = located / raw if raw else 0
    complete = stats.get('completion') in COMPLETE and (not count or len(jobs) >= count)
    reasons = []
    if not jobs: reasons.append('empty_unverified')
    if title_ratio < .95: reasons.append('usable_title_url_below_95_percent')
    if location_ratio < .80: reasons.append('explicit_location_below_80_percent')
    if not complete: reasons.append('pagination_incomplete_or_unknown')
    expected = expected_locations or []
    expected_matches = sum(any(str(x).casefold() in str(j.get('location', '')).casefold() for x in expected) for j in jobs)
    return dict(raw=raw, extracted=len(jobs), usable=usable, with_location=located,
                usable_ratio=round(title_ratio, 4), location_ratio=round(location_ratio, 4),
                pagination=stats, pagination_complete=complete, expected_location_matches=expected_matches,
                passes=not reasons, reasons=reasons)
'''

# Module: src/tracker.py
_SOURCES['tracker'] = r'''"""Consolidated tracker implementation. See INSTALL.txt for migration details."""
from __future__ import annotations

# ========================================================================
# tracker: consolidated implementation
# ========================================================================



import datetime as dt
from pathlib import Path
from typing import Any, Callable

from src.matching import allowed_job
from src.matching import excluded_seniority
from src.matching import sort_key
from src.matching import filter_recent_jobs, prune_old_rows
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

COLUMNS = [
    "Job Posted",
    "Company",
    "City",
    "Job Title",
    "Location",
    "URL",
    "Match Type",
    "Match Reason",
]
COLUMN_WIDTHS = [14, 22, 9, 40, 30, 60, 18, 65]


HEADER_ALIASES = {
    "Relevance Score": "Relevance Score (1-10)",
}

HEADER_FILL = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True)
NEW_ROW_FILL = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid")  # green


def _style_header(ws: Worksheet, columns: list[str], widths: list[int]) -> None:
    for col_idx in range(1, len(columns) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    ws.freeze_panes = "A2"
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _new_workbook() -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Jobs"
    ws.append(COLUMNS)
    _style_header(ws, COLUMNS, COLUMN_WIDTHS)

    return wb


def _migrate_sheet(wb: Workbook, sheet_name: str, columns: list[str], widths: list[int], sheet_index: int) -> None:
    ws = wb[sheet_name]
    existing_headers = [c.value for c in ws[1]]
    if existing_headers == columns:
        return  # already current

    mapped_headers = [HEADER_ALIASES.get(h, h) for h in existing_headers]
    location_confirmed_idx = None
    if "Location Confirmed" in existing_headers:
        location_confirmed_idx = existing_headers.index("Location Confirmed")

    rows = []
    for row_idx in range(2, ws.max_row + 1):
        if location_confirmed_idx is not None:
            confirmed_val = ws.cell(row=row_idx, column=location_confirmed_idx + 1).value
            if confirmed_val != "Yes":
                continue  # drop rows that weren't a confirmed-location match
        row_dict = {}
        for col_idx, header in enumerate(mapped_headers, start=1):
            if header in columns:
                row_dict[header] = ws.cell(row=row_idx, column=col_idx).value
        if row_dict:
            rows.append(row_dict)

    wb.remove(ws)
    new_ws = wb.create_sheet(sheet_name, sheet_index)
    new_ws.append(columns)
    for row_dict in rows:
        new_ws.append([row_dict.get(col, "") for col in columns])
    _style_header(new_ws, columns, widths)


def _ensure_sheet(wb: Workbook, sheet_name: str, columns: list[str], widths: list[int], sheet_index: int) -> None:
    if sheet_name not in wb.sheetnames:
        ws = wb.create_sheet(sheet_name, sheet_index)
        ws.append(columns)
        _style_header(ws, columns, widths)
    else:
        _migrate_sheet(wb, sheet_name, columns, widths, sheet_index)


def load_or_create(path: Path) -> Workbook:
    if not path.exists():
        return _new_workbook()

    wb = load_workbook(path)
    _ensure_sheet(wb, "Jobs", COLUMNS, COLUMN_WIDTHS, 0)
    # Active workbook is Munich-only; historical archives are not modified.
    for name in list(wb.sheetnames):
        if any(term in name.lower() for term in ('swiss', 'zurich', 'zuerich', 'zürich', 'switzerland')):
            wb.remove(wb[name])
    ws = wb['Jobs']
    for row in range(ws.max_row, 1, -1):
        record = {col: ws.cell(row, i + 1).value for i, col in enumerate(COLUMNS)}
        if excluded_seniority(record.get('Job Title')) or not allowed_job({'city':record.get('City'), 'company':record.get('Company'), 'url':record.get('URL'), 'location':record.get('Location')}):
            ws.delete_rows(row)
    return wb


def _existing_urls(ws: Worksheet, columns: list[str]) -> set[str]:
    url_col = columns.index("URL") + 1
    return {
        ws.cell(row=row, column=url_col).value
        for row in range(2, ws.max_row + 1)
        if ws.cell(row=row, column=url_col).value
    }


def _prune_below_score(ws, columns, min_score):
    """Compatibility shim: numeric relevance never removes jobs."""
    return 0
def _sort_by_relevance(
    ws: Worksheet, columns: list[str], fill: PatternFill, newly_added_urls: set[str]
) -> None:
    url_col = columns.index("URL")

    rows = []
    for row in range(2, ws.max_row + 1):
        values = [ws.cell(row=row, column=c).value for c in range(1, len(columns) + 1)]
        rows.append(values)

    rows.sort(key=lambda v: (sort_key(v[columns.index('Location')], 0), str(v[columns.index('Company')] or '').casefold(), str(v[columns.index('Job Title')] or '').casefold()))

    for row in range(2, ws.max_row + 1):
        for col in range(1, len(columns) + 1):
            ws.cell(row=row, column=col).value = None
            ws.cell(row=row, column=col).fill = PatternFill(fill_type=None)

    for i, values in enumerate(rows):
        row_idx = i + 2
        for col_idx, value in enumerate(values, start=1):
            ws.cell(row=row_idx, column=col_idx).value = value
        if values[url_col] in newly_added_urls:
            for col_idx in range(1, len(columns) + 1):
                ws.cell(row=row_idx, column=col_idx).fill = fill


def _update_sheet(
    wb: Workbook,
    sheet_name: str,
    columns: list[str],
    fill: PatternFill,
    new_jobs: list[dict[str, Any]],
    row_builder: Callable[[dict[str, Any], str], list],
    min_score: int = 0,
) -> dict[str, int]:
    ws = wb[sheet_name]
    new_jobs = [j for j in filter_recent_jobs(new_jobs) if allowed_job(j) and not excluded_seniority(j.get('title'))]
    age_pruned = prune_old_rows(ws, columns)
    existing_urls = _existing_urls(ws, columns)

    added = 0
    already_tracked = 0
    newly_added_urls = set()
    for job in new_jobs:
        url = job.get("url", "")
        if not url:
            continue
        if url in existing_urls:
            already_tracked += 1
            url_col = columns.index("URL") + 1
            for existing_row in range(2, ws.max_row + 1):
                if ws.cell(existing_row, url_col).value == url:
                    for key, label in (("match_type", "Match Type"), ("match_reason", "Match Reason")):
                        if label in columns and job.get(key):ws.cell(existing_row, columns.index(label)+1).value = job[key]
                    break
            continue
        ws.append(row_builder(job, url))
        newly_added_urls.add(url)
        existing_urls.add(url)
        added += 1

    pruned = age_pruned + _prune_below_score(ws, columns, min_score)
    _sort_by_relevance(ws, columns, fill, newly_added_urls)
    return {"added": added, "already_tracked": already_tracked, "pruned": pruned, "total_rows": ws.max_row - 1}


def _build_jobs_row(job: dict[str, Any], url: str) -> list:
    return [
        job.get("posted_date", ""),
        job.get("company", ""),
        job.get("city", ""),
        job.get("title", ""),
        job.get("location", ""),
        url,
        job.get("match_type", ""),
        job.get("match_reason", ""),
    ]


def update_tracker(path: Path, new_jobs: list[dict[str, Any]], min_score: int = 0, related_roles_enabled: bool = True) -> dict[str, int]:
    wb = load_or_create(path)
    if not related_roles_enabled:
        ws = wb["Jobs"]
        match_col = COLUMNS.index("Match Type") + 1
        for row in range(ws.max_row, 1, -1):
            if ws.cell(row, match_col).value == "Related role":ws.delete_rows(row)
        new_jobs = [j for j in new_jobs if j.get("match_type") != "Related role"]
    # No numeric relevance gates, including for saved related-role rows.
    related_pruned = 0
    summary = _update_sheet(wb, "Jobs", COLUMNS, NEW_ROW_FILL, new_jobs, _build_jobs_row, min_score=min_score)
    summary["pruned"] += related_pruned
    summary["related_score_pruned"] = related_pruned
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + ".pending.xlsx")
    try:
        wb.save(temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return summary


def archive_and_reset(path: Path, archive_dir: Path) -> Path | None:
    if not path.exists():
        _new_workbook().save(path)
        return None

    archive_dir.mkdir(parents=True, exist_ok=True)
    month_tag = dt.date.today().strftime("%Y-%m")
    archive_path = archive_dir / f"job_tracker_{month_tag}.xlsx"

    counter = 2
    final_path = archive_path
    while final_path.exists():
        final_path = archive_dir / f"job_tracker_{month_tag}_v{counter}.xlsx"
        counter += 1

    path.rename(final_path)
    _new_workbook().save(path)
    return final_path
'''

# Module: src/validate_sources.py
_SOURCES['validate_sources'] = r'''"""Live extraction-quality checks; no configuration mutation by default."""
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
'''

# Module: src/validate_urls.py
_SOURCES['validate_urls'] = r'''"""Consolidated validate_urls implementation. See INSTALL.txt for migration details."""
from __future__ import annotations

# ========================================================================
# validate_urls: consolidated implementation
# ========================================================================



import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.matching import excluded_source
from src.scrapers import discover_real_careers_url

ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = ROOT / "config" / "companies.yaml"
JOB_BOARDS_FILE = ROOT / "config" / "job_boards.yaml"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PersonalJobTracker/1.0; "
        "+https://github.com/) research/personal-use job search bot"
    )
}
TIMEOUT = 8


def load_yaml(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("companies", [])


def check_one(company: dict) -> dict:
    name = company["name"]
    city = company.get("city", "")
    url = company.get("careers_url", "")

    if not url:
        return {"name": name, "city": city, "url": url, "status": "NO_URL", "detail": "no careers_url set", "suggested_fix": None}

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    except requests.exceptions.SSLError as exc:
        return {"name": name, "city": city, "url": url, "status": "SSL_ERROR", "detail": str(exc)[:150], "suggested_fix": None}
    except requests.exceptions.ConnectionError as exc:
        detail = "DNS resolution failed / connection refused" if "NameResolutionError" in str(exc) else str(exc)[:150]
        return {"name": name, "city": city, "url": url, "status": "CONNECTION_ERROR", "detail": detail, "suggested_fix": None}
    except requests.exceptions.Timeout:
        return {"name": name, "city": city, "url": url, "status": "TIMEOUT", "detail": f"no response within {TIMEOUT}s", "suggested_fix": None}
    except requests.exceptions.RequestException as exc:
        return {"name": name, "city": city, "url": url, "status": "OTHER_ERROR", "detail": str(exc)[:150], "suggested_fix": None}

    final_domain = urlparse(resp.url).netloc
    original_domain = urlparse(url).netloc
    redirect_note = f" (redirected to {final_domain})" if final_domain != original_domain else ""

    if resp.status_code == 200:
        return {"name": name, "city": city, "url": url, "status": "OK", "detail": f"200{redirect_note}", "suggested_fix": None}

    suggested_fix = None
    try:
        discovered_url, platform, _token = discover_real_careers_url(url)
        if discovered_url and discovered_url != url:
            suggested_fix = f"{discovered_url}" + (f" (platform: {platform})" if platform else "")
    except Exception:
        pass  # discovery is best-effort here; a failure just means no suggestion, not a crash

    if resp.status_code in (403, 999):
        return {"name": name, "city": city, "url": url, "status": "BLOCKED", "detail": f"{resp.status_code}{redirect_note} — may just be anti-bot, not necessarily wrong", "suggested_fix": suggested_fix}
    elif resp.status_code == 404:
        return {"name": name, "city": city, "url": url, "status": "NOT_FOUND", "detail": f"404{redirect_note}", "suggested_fix": suggested_fix}
    else:
        return {"name": name, "city": city, "url": url, "status": "HTTP_ERROR", "detail": f"{resp.status_code}{redirect_note}", "suggested_fix": suggested_fix}


def run() -> None:
    companies = load_yaml(COMPANIES_FILE) + load_yaml(JOB_BOARDS_FILE)
    companies = [c for c in companies if not excluded_source(c)]
    print(f"Checking {len(companies)} companies...\n")

    results = []
    for i, company in enumerate(companies, 1):
        result = check_one(company)
        results.append(result)
        print(f"[{i}/{len(companies)}] {result['status']:18} {result['name']}")
        time.sleep(0.1)  # be polite

    print("\n" + "=" * 70)
    by_status = {}
    for r in results:
        by_status.setdefault(r["status"], []).append(r)

    print("SUMMARY:")
    for status in ["OK", "BLOCKED", "NOT_FOUND", "CONNECTION_ERROR", "TIMEOUT", "SSL_ERROR", "HTTP_ERROR", "OTHER_ERROR", "NO_URL"]:
        if status in by_status:
            print(f"  {status}: {len(by_status[status])}")

    no_page_at_all = by_status.get("CONNECTION_ERROR", []) + by_status.get("NO_URL", [])
    other_broken = by_status.get("NOT_FOUND", []) + by_status.get("HTTP_ERROR", []) + by_status.get("TIMEOUT", []) + by_status.get("SSL_ERROR", []) + by_status.get("OTHER_ERROR", [])

    has_fix = [r for r in other_broken if r.get("suggested_fix")]
    needs_research = [r for r in other_broken if not r.get("suggested_fix")]

    if has_fix:
        print(f"\n{'=' * 70}")
        print(f"BROKEN WITH A SUGGESTED FIX ({len(has_fix)}) — discovery found a real link on the page:")
        for r in has_fix:
            print(f"  {r['name']} ({r['city']}): {r['url']}  [{r['status']}]")
            print(f"      -> suggested: {r['suggested_fix']}")

    if needs_research or no_page_at_all:
        combined = no_page_at_all + needs_research
        print(f"\n{'=' * 70}")
        print(f"BROKEN, NEEDS MANUAL RESEARCH ({len(combined)}) — no page to auto-discover from")
        print("(domain doesn't resolve at all, or nothing useful was found on the response):")
        for r in combined:
            print(f"  {r['name']} ({r['city']}): {r['url']}  [{r['status']}: {r['detail']}]")

    blocked = by_status.get("BLOCKED", [])
    if blocked:
        print(f"\n{'=' * 70}")
        print(f"BLOCKED ({len(blocked)}) — likely just anti-bot, probably fine (the real scraper uses a")
        print("real browser User-Agent and headless rendering as a last resort — check an actual")
        print("scrape run's log before assuming these are wrong):")
        for r in blocked:
            fix_note = f"  -> possible fix: {r['suggested_fix']}" if r.get("suggested_fix") else ""
            print(f"  {r['name']} ({r['city']}): {r['url']}{fix_note}")


if __name__ == "__main__":
    run()
'''

# Module: src/website.py
_SOURCES['website'] = r'''"""Consolidated website implementation. See INSTALL.txt for migration details."""
from __future__ import annotations

# ========================================================================
# generate_html: consolidated implementation
# ========================================================================



import datetime as dt
from pathlib import Path

import yaml
from urllib.parse import urlparse
from src.matching import area_priority, sort_key
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from src.matching import allowed_job
from src.matching import excluded_seniority
from src.tracker import COLUMNS, NEW_ROW_FILL

ROOT = Path(__file__).resolve().parent.parent
TRACKER_FILE = ROOT / "data" / "job_tracker.xlsx"
OUTPUT_FILE = ROOT / "docs" / "index.html"
CV_PROFILE_FILE = ROOT / "config" / "cv_profile.yaml"


def _rgb_matches(cell, fill) -> bool:
    cell_rgb = getattr(cell.fill.start_color, "rgb", None)
    fill_rgb = getattr(fill.start_color, "rgb", None)
    if not isinstance(cell_rgb, str) or not isinstance(fill_rgb, str):
        return False
    return cell_rgb[-6:] == fill_rgb[-6:]  # compare ignoring alpha prefix


def _escape(value) -> str:
    if value is None:
        return ""
    text = str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def _render_table(
    ws: Worksheet | None, columns: list[str], new_row_fill, empty_message: str, row_class: str,
) -> tuple[str, int]:
    if ws is None or ws.max_row < 2:
        return f"<tr><td colspan='{len([c for c in columns if c != 'URL']) + 1}'>{_escape(empty_message)}</td></tr>", 0

    url_col = columns.index("URL") + 1
    title_col = columns.index("Job Title")

    rows_html_parts = []
    ordered_rows = sorted(range(2, ws.max_row + 1), key=lambda row: sort_key(ws.cell(row, columns.index('Location') + 1).value, 0))
    for row in ordered_rows:
        values = [ws.cell(row=row, column=c).value for c in range(1, len(columns) + 1)]
        if all(v is None for v in values):
            continue  # skip fully-empty rows
        record = dict(zip(columns, values))
        if excluded_seniority(record.get('Job Title')) or not allowed_job({'city':record.get('City'), 'company':record.get('Company'), 'url':record.get('URL'), 'location':record.get('Location')}):
            continue
        is_highlighted = _rgb_matches(ws.cell(row=row, column=1), new_row_fill)
        url = str(values[url_col - 1] or "#")
        if urlparse(url).scheme not in ("http", "https"):
            url = "#"

        rank, area = area_priority(record.get('Location'))
        cells = [f"<td>{_escape('Preferred: ' + area if rank == 0 else area)}</td>"]
        for i, val in enumerate(values):
            if i == title_col and url and url != "#":
                cells.append(f"<td><a href='{_escape(url)}' target='_blank' rel='noopener'>{_escape(val)}</a></td>")
            elif i == url_col - 1:
                continue  # URL folded into the Job Title link, not shown as its own column
            else:
                cells.append(f"<td>{_escape(val)}</td>")
        cls = f" class='{row_class}'" if is_highlighted else ""
        rows_html_parts.append(f"<tr{cls}>{''.join(cells)}</tr>")

    rows_html = "\n".join(rows_html_parts) if rows_html_parts else f"<tr><td colspan='{len([c for c in columns if c != 'URL']) + 1}'>{_escape(empty_message)}</td></tr>"
    return rows_html, len(rows_html_parts)


def _section_html(title: str, columns: list[str], rows_html: str, total: int, legend_color: str, legend_label: str) -> str:
    display_columns = [c for c in columns if c != "URL"]
    header_html = "<th>Area priority</th>" + "".join(f"<th>{_escape(c)}</th>" for c in display_columns)
    return f"""
<h2>{_escape(title)}</h2>
<div class="meta">
  {total} tracked postings &middot;
  <span class="legend" style="background:{legend_color};"></span>{_escape(legend_label)}
</div>
<div class="scroll-wrap">
<table>
<thead><tr>{header_html}</tr></thead>
<tbody>
{rows_html}
</tbody>
</table>
</div>
"""



def _records(ws):
    """Presentation only: preserve the installed scope/seniority guards and ordering."""
    from src.matching import parse_posted
    if ws is None:return []
    records=[]
    profile=yaml.safe_load(CV_PROFILE_FILE.read_text(encoding="utf-8")) if CV_PROFILE_FILE.exists() else {}
    related_enabled=((profile or {}).get("related_roles") or {}).get("enabled") is True
    for row in range(2,ws.max_row+1):
        record={col:ws.cell(row,i+1).value for i,col in enumerate(COLUMNS)}
        if all(v is None for v in record.values()):continue
        if excluded_seniority(record.get('Job Title')) or not allowed_job({'city':record.get('City'),'company':record.get('Company'),'url':record.get('URL'),'location':record.get('Location')}):continue
        if record.get('Match Type')=='Related role' and not related_enabled:continue
        raw=record.get('Job Posted');posted=parse_posted(raw)
        score=record.get('Relevance Score (1-10)');score=score if isinstance(score,(int,float)) else 0
        rank,area=area_priority(record.get('Location'))
        url=str(record.get('URL') or '')
        if urlparse(url).scheme not in ('http','https') or not urlparse(url).netloc:url=''
        records.append(dict(match_type=str(record.get('Match Type') or ''),match_reason=str(record.get('Match Reason') or ''),title=str(record.get('Job Title') or 'Untitled vacancy'),company=str(record.get('Company') or 'Employer unspecified'),location=str(record.get('Location') or 'Location not recorded'),score=score,rank=rank,area=area,url=url,posted=posted.isoformat() if posted else '',raw_date=str(raw or ''),new=_rgb_matches(ws.cell(row,1),NEW_ROW_FILL)))
    return sorted(records,key=lambda r:(not r['new'],r['rank'],r['company'].casefold(),r['title'].casefold()))

STYLE = r"""
:root{--bg:#f4f6f8;--ink:#172b36;--muted:#596d79;--line:#dce5e9;--accent:#086c65;--soft:#e7f4f0;--white:#fff}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;line-height:1.5}a{color:var(--accent)}button,input,select{font:inherit}button,select{cursor:pointer}button,a,input,select,summary{touch-action:manipulation}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #c36b14;outline-offset:4px}[hidden]{display:none!important}.skip{position:absolute;left:16px;top:-80px;background:white;padding:12px;z-index:5}.skip:focus{top:12px}.topbar{background:#fff;border-bottom:1px solid var(--line)}.nav{max-width:1200px;margin:auto;padding:19px 28px;display:flex;align-items:center;justify-content:space-between;gap:20px}.brand{display:flex;align-items:center;gap:12px;font-weight:750;letter-spacing:-.4px}.mark{width:34px;height:34px;border-radius:11px;background:var(--accent);color:#fff;display:grid;place-items:center;font-size:18px}.nav small{color:var(--muted)}.nav a{font-size:13px;text-decoration:none}.shell{max-width:1200px;margin:auto;padding:42px 28px}.eyebrow{font-size:11px;letter-spacing:2px;font-weight:750;color:var(--accent);text-transform:uppercase}h1{font-size:clamp(30px,4vw,44px);letter-spacing:-1.6px;line-height:1.15;margin:10px 0 14px}h2{letter-spacing:-.5px}.hero{display:flex;align-items:flex-start;justify-content:space-between;gap:32px}.hero p{color:var(--muted);max-width:650px;margin:0 0 16px}.stamp{font-size:12px;color:var(--muted);padding:12px 15px;border:1px solid var(--line);border-radius:12px;background:white;white-space:nowrap}.stamp strong{display:block;color:var(--ink);font-weight:600}.scope{display:flex;flex-wrap:wrap;gap:8px}.scope span,.pill{display:inline-block;padding:5px 10px;border:1px solid var(--line);border-radius:30px;font-size:11px;background:white;color:var(--muted)}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:30px 0}.metric{background:white;border:1px solid var(--line);border-radius:16px;padding:19px 22px}.metric strong{display:block;font-size:29px;line-height:1.25;letter-spacing:-1px}.metric span{font-size:12px;color:var(--muted)}.workspace{display:grid;grid-template-columns:235px minmax(0,1fr);gap:26px;align-items:start}.filters{background:white;border:1px solid var(--line);border-radius:18px;padding:21px;position:sticky;top:20px}.filters h2{font-size:16px;margin:0 0 20px}.filters label{display:block;font-size:12px;font-weight:650;margin:18px 0 7px}.filters select,.filters input[type=search]{width:100%;border:1px solid #c9d7dc;border-radius:9px;background:#fff;color:var(--ink);padding:10px;min-height:43px}.filters .check{display:flex;align-items:center;gap:9px;font-weight:400;margin:18px 0}.filters input[type=checkbox]{accent-color:var(--accent);width:17px;height:17px}.hint{font-size:11px;color:var(--muted);line-height:1.6}.reset{border:0;background:transparent;color:var(--accent);padding:10px 0;font-size:12px;text-decoration:underline}.results-head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:0 0 16px}.results-head h2{font-size:19px;margin:0}.results-head p{font-size:12px;color:var(--muted);margin:3px 0}.results-head select{border:1px solid var(--line);background:white;padding:9px;border-radius:9px;font-size:12px;max-width:210px}.jobs{display:grid;gap:13px}.job-card{border:1px solid var(--line);border-radius:17px;background:white;padding:22px 24px;transition:border-color .15s,box-shadow .15s}.job-card:hover{border-color:#9bbdb7;box-shadow:0 6px 18px #172b3608}.job-top{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.employer{display:flex;align-items:center;gap:10px;font-size:12px;font-weight:650;color:var(--muted)}.avatar{display:grid;place-items:center;width:30px;height:30px;background:#f0f4f6;border:1px solid #e6ecef;border-radius:9px;font-size:10px;color:#526773}.job-card h3{font-size:19px;letter-spacing:-.35px;line-height:1.4;margin:12px 0 8px;overflow-wrap:anywhere}.job-card h3 a{color:var(--ink);text-decoration:none}.job-card h3 a:hover{text-decoration:underline}.score{background:var(--soft);border-radius:10px;padding:7px 11px;font-size:12px;white-space:nowrap;color:#075b55}.score b{font-size:17px}.location{font-size:12px;color:var(--muted);margin:0}.job-foot{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-top:18px;padding-top:15px;border-top:1px solid #edf1f3}.badges{display:flex;align-items:center;flex-wrap:wrap;gap:7px}.pill{background:#f8fafb;border-color:#e3eaed}.pill.preferred{color:#075b55;background:#edf7f3;border-color:#cce6dc}.pill.new{color:#895207;background:#fff7e5;border-color:#f0dfb4}.apply{font-size:12px;text-decoration:none;font-weight:700;white-space:nowrap}.empty{background:white;border:1px dashed #b6c9ce;border-radius:16px;padding:40px;text-align:center;color:var(--muted)}.empty strong{display:block;color:var(--ink);margin-bottom:6px}.board-footer{font-size:11px;color:var(--muted);margin-top:22px}.health-panel{max-width:1144px;margin:0 auto 36px;border:1px solid var(--line);border-radius:18px;background:white;overflow:hidden}.health-panel>summary{padding:22px 24px;cursor:pointer;font-size:15px;font-weight:650}.health-panel>summary span{font-weight:400;color:var(--muted);font-size:12px;margin-left:10px}.health-content{padding:0 24px 24px}.health-content>p{font-size:12px;color:var(--muted)}.health-stats{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:15px}.health-stats span{font-size:11px;padding:6px 10px;background:#f3f6f8;border-radius:8px}.scroll-wrap{overflow:auto;max-height:520px;border:1px solid var(--line);border-radius:10px}table{border-collapse:collapse;width:100%;font-size:11px}th,td{text-align:left;vertical-align:top;padding:11px;border-bottom:1px solid var(--line)}th{background:#edf3f4;position:sticky;top:0;z-index:1;white-space:nowrap}td{min-width:100px}td:last-child{min-width:290px;max-width:550px;overflow-wrap:anywhere}.health-content details{font-size:11px}.health-content details summary{cursor:pointer;color:var(--accent)}.health-content details div{padding-top:10px}.noscript{padding:12px;background:#fff4da;font-size:12px}.demo-banner{background:#fff2d7;padding:10px 28px;font-size:12px;text-align:center}@media(max-width:800px){.shell{padding:28px 18px}.nav{padding:15px 18px}.hero{display:block}.stamp{display:inline-block;margin-top:18px}.metrics{grid-template-columns:repeat(2,1fr);gap:10px}.workspace{grid-template-columns:1fr}.filters{position:static}.filters h2{margin-bottom:12px}.filters label{margin-top:12px}.health-panel{margin:0 18px 28px}.results-head{align-items:flex-start}.job-card{padding:18px}.job-card h3{font-size:17px}.job-foot{align-items:flex-start}.nav small{display:none}.health-panel>summary span{display:block;margin:5px 0 0}}@media(prefers-reduced-motion:reduce){*{transition:none!important}}
"""


def _card(r,index):
    e=_escape
    url=e(r['url']);title=e(r['title'])
    title_link=f"<a href='{url}' target='_blank' rel='noopener noreferrer'>{title}</a>" if r['url'] else title
    badge="<span class='new-flag'>NEW</span>" if r['new'] else ''
    related="<span class='related-flag'>Related</span>" if r.get('match_type')=='Related role' else ''
    date='Posted '+r['posted'] if r['posted'] else 'Date unknown'
    cls='job-row is-new' if r['new'] else 'job-row'
    return f"""<article class='{cls}'><div class='role'><div class='title-line'>{badge}<h2>{title_link}</h2>{related}</div><p class='employer'>{e(r['company'])}</p></div><div class='workplace'>{e(r['location'])}</div><div class='match-note'>{e(r.get('match_reason') or 'Manual review')}</div><div class='posted'>{e(date)}</div></article>"""

LIST_STYLE=r"""
.shell{max-width:1340px;padding:25px 28px}.nav{max-width:1340px;padding:16px 28px}.list-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:18px;margin-bottom:18px}.list-heading h1{font-size:27px;letter-spacing:-.8px;margin:0 0 5px}.list-heading p{margin:0;color:var(--muted);font-size:12px}.list-heading .stamp{font-size:11px;padding:9px 12px}.job-list{border:1px solid var(--line);border-radius:16px;background:white;overflow:hidden}.list-columns,.job-row{display:grid;grid-template-columns:minmax(0,2.6fr) minmax(0,1.3fr) 130px 150px 115px;gap:22px;align-items:center;padding:20px 24px}.list-columns{background:#edf3f4;padding-top:12px;padding-bottom:12px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:var(--muted)}.job-row{border-top:1px solid #e8eef1}.job-row:hover{background:#f7fbfa}.job-row h2{font-size:17px;line-height:1.45;letter-spacing:-.2px;margin:0 0 6px;overflow-wrap:anywhere}.job-row h2 a{color:var(--ink);text-decoration:none}.job-row h2 a:hover{text-decoration:underline}.job-row .employer{font-size:12px;margin:0 0 5px;font-weight:400}.workplace,.posted{font-size:12px;color:var(--muted);overflow-wrap:anywhere}.area-label{display:block;font-size:10px;color:var(--accent);margin-top:5px}.action{text-align:right}.mobile-label{display:none}.health-panel{max-width:1284px}.score{display:inline-block}.scope-note{font-size:11px;color:var(--muted);margin-top:14px}.empty{border:0}.job-row .pill.new{font-size:10px;padding:2px 8px}@media(max-width:1050px){.list-columns,.job-row{grid-template-columns:minmax(0,2fr) minmax(0,1fr) 100px 125px;gap:15px}.list-columns span:last-child{display:none}.action{grid-column:1/-1;text-align:left}}@media(max-width:700px){.shell{padding:22px 16px}.nav{padding:14px 16px}.list-heading{display:block}.list-heading .stamp{display:inline-block;margin-top:12px}.list-columns{display:none}.job-row{grid-template-columns:minmax(0,1fr) minmax(0,1fr);padding:20px 18px;gap:15px}.role{grid-column:1/-1}.job-row h2{font-size:18px}.mobile-label{display:block;font-size:10px;margin-bottom:6px;color:var(--muted)}.posted{grid-column:1/-1}.action{grid-column:1/-1}.health-panel{margin:0 16px 25px}.nav small{display:none}}
"""

SCAN_STYLE=r"""
.shell{max-width:1500px;padding:12px 20px}.nav{max-width:1500px;padding:10px 20px}.list-heading{margin-bottom:10px;align-items:center}.list-heading h1{font-size:21px;margin:0 0 3px}.list-heading .stamp{font-size:10px;padding:5px 9px}.list-heading .stamp strong{font-size:11px}.job-list{border-radius:8px}.list-columns,.job-row{grid-template-columns:minmax(0,3fr) minmax(0,1.5fr) minmax(120px,1.3fr) 145px;gap:14px;padding:10px 15px}.list-columns{font-size:11px;padding-top:8px;padding-bottom:8px}.job-row{border-left:4px solid transparent;align-items:start}.job-row.is-new{background:#effaf5;border-left-color:#087f5b}.job-row h2{font-size:15px;line-height:1.35;margin:0}.title-line{display:flex;gap:7px;align-items:baseline;flex-wrap:wrap}.title-line h2{min-width:0;overflow-wrap:anywhere}.employer{font-size:12px!important;margin:3px 0 0!important;color:#334155!important;font-weight:600}.workplace,.posted{font-size:12px;line-height:1.5}.score{padding:3px 7px;font-size:11px}.score b{font-size:15px}.new-flag{font-size:10px;font-weight:800;color:#fff;background:#087f5b;border-radius:3px;padding:2px 5px;letter-spacing:.4px}.related-flag{font-size:10px;color:#6b4d10;background:#fff2d4;padding:2px 5px;border-radius:3px}.scope-note{font-size:10px;margin-top:9px}.batch-title{padding:8px 15px;background:#e5f4ed;color:#14533e;font-size:12px;font-weight:800;border-top:1px solid #d6e6de}.batch-title.existing{background:#f1f5f9;color:#475569}.scan-summary{font-size:12px;color:#475569}.scan-summary strong{color:#087f5b}
@media(max-width:800px){.shell{padding:10px}.nav{padding:10px}.list-heading{align-items:flex-start}.list-heading .stamp{max-width:145px}.list-columns{display:none}.job-row{display:grid;grid-template-columns:minmax(0,1fr) 65px;gap:4px 10px;padding:10px}.role{grid-column:1}.match-note{grid-column:1/-1;font-size:11px;color:var(--muted)}.workplace{grid-column:1;grid-row:2}.posted{grid-column:1;grid-row:3;font-size:11px}.title-line h2{font-size:14px}.topbar small{display:none}.brand{font-size:14px}.list-heading h1{font-size:19px}}
@media(prefers-color-scheme:dark){.job-row.is-new{background:#142e26}.employer{color:#d2dedb!important}.workplace,.posted,.scan-summary{color:#c3cecb}.batch-title{background:#18382c;color:#c4f2da}.batch-title.existing{background:#26323b;color:#d9e3ec}.related-flag{background:#4a3a17;color:#ffe3a0}.scan-summary strong{color:#76d8c4}}
"""

def generate(tracker_path: Path = TRACKER_FILE, output_path: Path = OUTPUT_FILE) -> None:
    output_path=Path(output_path);output_path.parent.mkdir(parents=True,exist_ok=True)
    wb=None
    try:
        if Path(tracker_path).exists():wb=load_workbook(tracker_path)
        ws=wb['Jobs'] if wb and 'Jobs' in wb.sheetnames else (wb.active if wb else None)
        records=_records(ws)
    finally:
        if wb:wb.close()
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%d %b %Y, %H:%M UTC')
    new_count=sum(bool(r['new']) for r in records)
    cards=''.join(("<div class='batch-title'>New this update</div>" if i==0 and r['new'] else "<div class='batch-title existing'>Already tracked</div>" if (i==0 or records[i-1]['new']) and not r['new'] else '')+_card(r,i) for i,r in enumerate(records)) or "<div class='empty'><strong>No tracked jobs to display</strong>Results appear after a successful tracker update.</div>"
    html=f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Munich Job Tracker</title><style>{STYLE}{LIST_STYLE}{SCAN_STYLE}</style></head><body><a class='skip' href='#jobs'>Skip to jobs</a><header class='topbar'><div class='nav'><div class='brand'><span class='mark' aria-hidden='true'>m.</span>Munich Job Tracker</div><small>New first · Munich region</small><a href='#scraper-health'>Source health &#8595;</a></div></header><main class='shell'><section aria-labelledby='jobs-title'><div class='list-heading'><div><h1 id='jobs-title'>Your opportunities</h1><p class='scan-summary'><strong>{new_count} new this update</strong> · {len(records)} total · all jobs shown</p></div><div class='stamp'>Page generated<strong>{stamp}</strong>Not a guarantee of source freshness</div></div><div class='job-list' id='jobs'><div class='list-columns' aria-hidden='true'><span>Role &amp; employer</span><span>Workplace</span><span>Why included</span><span>Posting date</span></div>{cards}</div><p class='scope-note'>Munich metropolitan region · 21-day rule plus undated jobs · no commute cutoff. “New this update” means added in the latest tracker update, not newly posted or unread. Within each group: preferred areas, then employer and title. No relevance scores or score cutoffs. CV-related titles are included broadly for your manual review.</p></section></main></body></html>"""
    output_path.write_text(html,encoding='utf-8')

if __name__=='__main__':generate()
'''

class _BundledLoader(_abc.SourceLoader):
    def __init__(self, fullname):
        self.fullname = fullname

    def get_filename(self, fullname):
        return str(_Path(__file__).resolve().parent / (fullname.rsplit('.', 1)[-1] + '.py'))

    def get_data(self, path):
        name = _Path(path).stem
        if name not in _SOURCES:
            raise OSError('Unknown bundled source: ' + str(path))
        return _SOURCES[name].encode('utf-8')

    def path_stats(self, path):
        return {'mtime': 0, 'size': len(self.get_data(path))}

    def set_data(self, path, data):
        pass  # Never recreate individual source or bytecode files.

class _BundledFinder(_abc.MetaPathFinder):
    _tracker_bundle = __name__

    def find_spec(self, fullname, path=None, target=None):
        prefix = __name__ + '.'
        if fullname.startswith(prefix) and fullname[len(prefix):] in _SOURCES:
            loader = _BundledLoader(fullname)
            return _util.spec_from_file_location(fullname, loader.get_filename(fullname), loader=loader)
        return None

if not any(getattr(f, '_tracker_bundle', None) == __name__ for f in _sys.meta_path):
    _sys.meta_path.insert(0, _BundledFinder())
