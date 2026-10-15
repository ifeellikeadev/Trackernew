"""Public career-board readers. No authentication, stealth, CAPTCHA solving or guessed private API.
Limits and stopping reasons are recorded; extraction is not a coverage certificate.
"""
import json,logging,re,time
from urllib.parse import urlparse,urljoin,parse_qs,urlencode,urlunparse
from bs4 import BeautifulSoup
from src.vacancy_extract import html_jobs,structured_jobs,plain,dedupe,usable_title,ROLE,clean_label
logger=logging.getLogger('job_scraper.ats')
MAX_PAGES=200
SOURCE_SECONDS=150
BROWSER_SECONDS=75


def job_url(url):
    p=urlparse(str(url));q=parse_qs(p.query)
    return p.scheme in ('http','https') and bool(re.search(r'/(?:jobs?|positions?|postings?|vacancies|o|stellenanzeige|job-posting)/[^/]+|/companies/[^/]+/\d+[-/]',p.path,re.I) or any(k.lower() in ('jobofferid','jobid','job_id','postingid','career_job_req_id') for k in q))


def json_records(data,url,collection=False,depth=0):
    """Read only recognisable job records, not arbitrary titles in page configuration."""
    if depth>24:return []
    out=[]
    if isinstance(data,list):
        for item in data:out.extend(json_records(item,url,collection,depth+1))
    elif isinstance(data,dict):
        title=data.get('jobTitle') or data.get('title') or (data.get('name') if collection else '')
        target=data.get('jobUrl') or data.get('job_url') or data.get('public_url') or data.get('url')
        if collection and not target and urlparse(url).hostname in ('jobs.infineon.com','jobs.siemens.com','www.jobs.siemens.com') and data.get('id'):
            ident=str(data['id'])
            if re.fullmatch(r'\d+',ident):target=urljoin(url,'/careers/job/'+ident)
        if isinstance(title,str) and usable_title(clean_label(title)) and isinstance(target,str):
            target=urljoin(url,target)
            if job_url(target) and (collection or data.get('jobId') or data.get('jobSeqNo')):
                locations=[]
                for key in ('location','locations','multi_location','multi_location_array'):
                    val=data.get(key)
                    if isinstance(val,str):locations.append(val)
                    elif isinstance(val,list):
                        for loc in val:
                            if isinstance(loc,str):locations.append(loc)
                            elif isinstance(loc,dict):
                                text=loc.get('location') or ', '.join(str(loc[k]) for k in ('city','state','country') if isinstance(loc.get(k),str))
                                if isinstance(text,str) and text:locations.append(text)
                if not locations:
                    locations=[', '.join(str(data[k]) for k in ('city','state','country') if isinstance(data.get(k),str))]
                # No update timestamp, first-seen date or epoch guess is used as a posting date.
                date=next((data[k] for k in ('datePosted','publishedAt','postedDate','postingDate') if isinstance(data.get(k),str)), '')
                out.append(dict(title=clean_label(title),location='; '.join(dict.fromkeys(filter(None,locations))),url=target,
                                description=plain(data.get('description') or data.get('jobDescription') or ''),posted_date=date,_extraction='public_embedded_jobs'))
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


def extract_page(html,url):
    out=structured_jobs(html,url)+embedded_jobs(html,url)
    soup=BeautifulSoup(html,'html.parser')
    for a in soup.find_all('a',href=True):
        target=urljoin(url,a['href']);q=parse_qs(urlparse(target).query)
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
    combined=dedupe(out+html_jobs(html,url))
    for job in combined:
        parsed=urlparse(job['url']);query=parse_qs(parsed.query)
        if parsed.hostname=='jobboerse.iabg.de' and query.get('jobOfferId'):
            path=re.sub(r'(?:;|%3[Bb])jsessionid=[^/?;]+','',parsed.path)
            job['url']=urlunparse(parsed._replace(path=path,params='',query=urlencode({'jobOfferId':query['jobOfferId'][0]})))
    combined=dedupe(combined)
    if urlparse(url).hostname=='careers.allianz.com':
        # Prefer public detail pages to duplicate Apply links for the same req ID.
        ids={re.search(r'/job/([^/]+)',urlparse(j['url']).path).group(1) for j in combined if re.search(r'/job/([^/]+)',urlparse(j['url']).path)}
        combined=[j for j in combined if not (parse_qs(urlparse(j['url']).query).get('career_job_req_id',[''])[0] in ids)]
    return combined


def next_link(html,url):
    soup=BeautifulSoup(html,'html.parser')
    for a in soup.find_all('a',href=True):
        rel=a.get('rel',[]);label=clean_label(a.get('aria-label') or a.get('title') or a.get_text(' ',strip=True))
        if 'disabled' in a.get('class',[]) or a.get('aria-disabled')=='true':continue
        if 'next' in rel or re.fullmatch(r'(?:next(?: page)?|nächste(?: seite)?|naechste(?: seite)?|weiter|›|»|>)',label,re.I):
            target=urljoin(url,a['href'])
            if urlparse(target).hostname==urlparse(url).hostname and urlparse(target).scheme in ('http','https') and not a['href'].startswith(('#','javascript:')):return target
    return None


def _tag(jobs,stats):
    for j in jobs:j['_pagination']=dict(stats)
    return jobs


def scrape_html_board(name,url,get,max_pages=MAX_PAGES,seconds=SOURCE_SECONDS):
    """Follow actual public Next links; browser handles JS buttons if needed."""
    started=time.monotonic();current=url;seen=set();jobs=[];cookies={};pages=0;reason='no_next_link_unverified'
    while current and pages<max_pages and time.monotonic()-started<seconds:
        if current in seen:reason='repeated_url';break
        seen.add(current)
        response=get(current,cookies=cookies) if cookies else get(current)
        if response is None:reason='request_failed';break
        try:cookies.update(response.cookies.get_dict())
        except (AttributeError,TypeError):pass
        page_url=response.url or current;page_jobs=extract_page(response.text,page_url);pages+=1
        before=len(jobs);jobs=dedupe(jobs+page_jobs)
        if pages>1 and page_jobs and len(jobs)==before:reason='repeated_jobs';break
        if pages>1 and not page_jobs:
            reason='empty_page_unverified';break
        current=next_link(response.text,page_url)
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
                if re.fullmatch(r'(?:search jobs|stellen suchen|alle jobs(?: anzeigen)?|view all jobs)',clean_label(anchor.get_text(' ',strip=True)),re.I):
                    candidate=urljoin(page_url,anchor['href'])
                    if urlparse(candidate).hostname==urlparse(page_url).hostname and candidate not in seen and not anchor['href'].startswith(('#','javascript:')):
                        current=candidate;break
    if current and pages>=max_pages:reason='page_limit'
    elif current and time.monotonic()-started>=seconds:reason='time_limit'
    stats={'reader':'public_html','pages':pages,'unique_candidates':len(jobs),'completion':reason}
    # Browser gets a separate bounded allowance, not the exhausted global fallback budget.
    # No login, stealth, certificate disabling or CAPTCHA handling is attempted.
    if reason in ('no_next_link_unverified','request_failed'):
        rendered,render_stats=browse_public_board(name,page_url if pages else url,seconds=BROWSER_SECONDS)
        jobs=dedupe(jobs+rendered);stats['browser']=render_stats;stats['unique_candidates']=len(jobs)
    if reason!='no_next_link_unverified':logger.warning('Board pagination %s: %s; retained %s candidates',name,reason,len(jobs))
    logger.warning('Board coverage unverified for %s: %s',name,json.dumps(stats))
    return _tag(jobs,stats)


def browse_public_board(name,url,seconds=BROWSER_SECONDS):
    started=time.monotonic();jobs=[];pages=0;reason='no_next_control_unverified';browser=None
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
                        network_jobs.extend(json_records(resp.json(),url))
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
                while pages<MAX_PAGES and time.monotonic()-started<seconds:
                    try:html=page.content()
                    except Exception:page.wait_for_timeout(300);html=page.content()
                    found=extract_page(html,page.url);before=len(jobs);jobs=dedupe(jobs+network_jobs+found);pages+=1
                    signature=tuple(j['url'] for j in found)
                    stagnant=stagnant+1 if signature==previous else 0;previous=signature
                    if stagnant>=2:reason='repeated_page';break
                    control=None
                    for selector in ('a[rel="next"]','button[aria-label*="Next"]','button[aria-label*="next"]','a[aria-label*="Next"]','button[aria-label*="Weiter"]','button[aria-label*="Nächste"]','button:has-text("Load more")','button:has-text("Show more jobs")','button:has-text("Mehr Stellen")','button:has-text("Next")','a:has-text("Next")','a:has-text("Weiter")','button:has-text("Stellen suchen")','button:has-text("Search Jobs")'):
                        candidates=page.locator(selector)
                        for index in range(min(candidates.count(),4)):
                            candidate=candidates.nth(index)
                            if candidate.is_visible() and candidate.is_enabled() and candidate.get_attribute('aria-disabled')!='true':control=candidate;break
                        if control is not None:break
                    if control is None:
                        # Trigger ordinary lazy loading once; never assume this is full pagination.
                        page.evaluate('() => { const r=document.scrollingElement || document.documentElement; if(r) window.scrollTo(0,r.scrollHeight); }')
                        page.wait_for_timeout(700)
                        extra=extract_page(page.content(),page.url);jobs=dedupe(jobs+network_jobs+extra)
                        reason='no_next_control_unverified';break
                    control.click(timeout=2500);page.wait_for_timeout(700)
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
        jobs.extend(new);start+=len(positions)
    if pages>=max_pages:reason='page_limit'
    elif time.monotonic()-began>=seconds:reason='time_limit'
    if reason=='empty_page' and total is not None and len(jobs)<total:
        reason='count_mismatch'
    stats={'reader':'eightfold_public_api','pages':pages,'advertised_total':total,'unique_candidates':len(jobs),'completion':reason}
    if reason!='empty_page' or not jobs:
        logger.warning('Eightfold pagination %s: %s',name,json.dumps(stats))
        if not jobs:
            return scrape_html_board(name,url,get)
    return _tag(jobs,stats)
