"""Explicit public-source adapters; no login, stealth or access-control bypass."""
import json,logging,re,time
from urllib.parse import urljoin,urlparse
from bs4 import BeautifulSoup
from src.vacancy_extract import walk,plain,location,usable_title,is_vacancy_url,dedupe,resolve_job_url
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
        from src.public_boards import scrape_html_board
        return scrape_html_board(name,url,get)
    for j in jobs:j['_pagination']={'reader':'amazon_public_search','pages':pages,'advertised_total':total,'unique_candidates':len(jobs),'completion':reason}
    return jobs
