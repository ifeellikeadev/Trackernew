"""Consolidated scrapers implementation. See INSTALL.txt for migration details."""
from __future__ import annotations

# ========================================================================
# source_adapters: consolidated implementation
# ========================================================================
"""Explicit public-source adapters; no login, stealth or access-control bypass."""
import json,logging,re,time
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
               'button:has-text("Mehr laden")','button:has-text("Next")','a:has-text("Next")',
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

def extract_page(html,url):


    out=structured_jobs(html,url)+embedded_jobs(html,url)+bmw_cards(html,url)+priority_cards(html,url)+coverage_cards(html,url)
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
    soup=BeautifulSoup(html,'html.parser')
    for a in soup.find_all('a',href=True):
        rel=a.get('rel',[]);label=clean_label(a.get('aria-label') or a.get('title') or a.get_text(' ',strip=True))
        if 'disabled' in a.get('class',[]) or a.get('aria-disabled')=='true':continue
        if 'next' in rel or re.fullmatch(r'(?:(?:(?:go to )?next(?: page)?|nächste(?: seite)?|naechste(?: seite)?|weiter)(?:\s*(?:>>|»|›|→))?|›|»|>)',label,re.I):
            target=urljoin(url,a['href'])
            if urlparse(target).hostname==urlparse(url).hostname and urlparse(target).scheme in ('http','https') and not a['href'].startswith(('#','javascript:')):return target
    return None


def _tag(jobs,stats):
    for j in jobs:j['_pagination']=dict(stats)
    return jobs


def scrape_html_board(name,url,get,max_pages=MAX_PAGES,seconds=SOURCE_SECONDS):
    """Follow actual public Next links; browser handles JS buttons if needed."""
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
                while pages<MAX_PAGES and time.monotonic()-started<seconds:
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
    return jobs


# --------------------------------------------------------------------------
# Lever
# --------------------------------------------------------------------------
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
    return jobs


# --------------------------------------------------------------------------
# SmartRecruiters
# --------------------------------------------------------------------------
def scrape_smartrecruiters(company_name: str, board_token: str = "") -> list[dict[str, Any]]:
    from urllib.parse import quote
    token = board_token or company_name.replace(" ", "")
    base = f"https://api.smartrecruiters.com/v1/companies/{quote(token, safe='')}/postings"
    jobs, seen, offset = [], set(), 0
    for _ in range(200):
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
    return jobs


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


def scrape_workday(company_name: str, careers_url: str) -> list[dict[str, Any]]:
    """
    Workday career sites follow the pattern:
      https://{tenant}.{dc}.myworkdayjobs.com/{site}
    with a matching JSON API at:
      https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs

    We try to derive the API URL from the given careers_url. If that
    doesn't match directly — the common case, since most companies'
    careers_url in config is their own vanity domain rather than the
    myworkdayjobs.com one — we fetch that page and try to discover the
    real Workday URL from it (see _discover_workday_url) before giving
    up. If discovery also finds nothing, we return an empty list and
    let the dispatcher fall back to the generic scraper — a genuine
    signal the company likely isn't actually on Workday, not just a
    URL-format mismatch.

    Note on dates: Workday's API gives a relative string like "Posted
    3 Days Ago" (postedOn), not an absolute date. We store that string
    as-is in posted_date rather than guessing an exact date from it —
    it's still useful context, just not a precise calendar date.
    """
    m = _WORKDAY_URL_RE.match(careers_url)
    if not m:
        discovered = _discover_workday_url(careers_url)
        if discovered:
            m = _WORKDAY_URL_RE.match(discovered)
        if not m:
            logger.info("Workday URL pattern not recognized for %s: %s", company_name, careers_url)
            return []
    tenant, dc, site = m.groups()
    api_url = f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    jobs, seen, offset = [], set(), 0
    base = f"https://{tenant}.{dc}.myworkdayjobs.com/{site}"
    pages = 0
    advertised_total = None
    completion = 'safety_limit'
    request_limit=20
    repeated_recovery=False
    source_started=time.monotonic()
    for _ in range(500):
        if time.monotonic()-source_started>=150:
            completion='time_limit';logger.warning('Workday incomplete pagination: time limit; retaining %s candidates',len(jobs));break
        try:
            resp = requests.post(api_url, json={"limit": request_limit, "offset": offset, "searchText": "", "appliedFacets": {}}, headers=HEADERS, timeout=request_timeout(TIMEOUT))
            resp.raise_for_status()
            data = resp.json()
            page = data.get("jobPostings", [])
            if not isinstance(page, list):raise ValueError("Workday jobPostings must be a list")
        except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
            completion = 'request_or_schema_failure'
            logger.warning("Workday incomplete pagination at offset %s: %s; retaining %s candidates", offset, str(exc)[:200], len(jobs))
            break
        pages += 1
        if data.get('total') is not None:
            try:advertised_total = int(data['total'])
            except (ValueError,TypeError):
                logger.warning('Workday invalid advertised total for %s', company_name)
        if not page:
            completion = 'empty_page'
            if advertised_total is not None and offset < advertised_total:
                logger.warning("Workday incomplete pagination: retrieved %s of advertised %s", offset, advertised_total)
            break
        added = 0
        for posting in page:
            path = posting.get("externalPath") or ""
            if not path or path in seen:continue
            seen.add(path);added += 1
            jobs.append({"title": (posting.get("title") or "").strip(), "location": posting.get("locationsText", ""),
                         "url": base + path, "description": "", "posted_date": normalize_fresh_posted(posting.get("postedOn", "")),
                         "_extraction": "workday_api"})
            stamp_date(jobs[-1],posting.get("postedOn", ""),"postedOn")
        checkpoint(jobs)
        if not added:
            if advertised_total is not None and len(jobs) >= advertised_total:
                completion = 'reported_total_reached'
            elif not repeated_recovery and offset>0 and len(page)!=request_limit:
                request_limit=len(page);repeated_recovery=True
                logger.info('Workday retry with observed page size %s at offset %s',request_limit,offset)
                continue
            else:
                completion = 'repeated_page'
                logger.warning("Workday incomplete pagination: offset %s repeated a page; retaining %s unique candidates", offset, len(jobs))
            break
        offset += len(page)
        if advertised_total is not None and advertised_total>20 and offset>=advertised_total and len(jobs)<advertised_total:
            completion='advertised_range_exhausted_with_duplicates'
            logger.warning('Workday incomplete coverage: advertised range exhausted, %s unique of %s; duplicate gap retained explicitly',len(jobs),advertised_total)
            break
        # Stop at a multi-page reported total. Probe a page-sized total once:
        # some feeds have reported only the first page's size as their total.
        if advertised_total is not None and advertised_total > 20 and len(jobs) >= advertised_total:
            completion = 'reported_total_reached'
            break
    else:
        logger.warning("Workday incomplete pagination: safety limit reached; retaining partial results")
    for job in jobs:
        job['_pagination'] = {'pages': pages, 'advertised_total': advertised_total, 'unique_candidates': len(jobs), 'completion': completion}
    logger.info("Workday pagination %s: pages=%s advertised_total=%s unique=%s completion=%s", company_name, pages, advertised_total, len(jobs), completion)
    return jobs

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
    return ashby_jobs(resp.json())

_PLATFORM_URL_PATTERNS = {
    "ashby": re.compile(r"https?://jobs\.ashbyhq\.com/([\w-]+)"),
    "greenhouse": re.compile(r"https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/((?!embed(?:/|$))[\w-]+)"),
    "lever": re.compile(r"https?://jobs\.lever\.co/([\w-]+)"),
    "personio": re.compile(r"https?://([\w-]+)\.jobs\.personio\.(?:de|com)"),
    "smartrecruiters": re.compile(r"https?://(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)"),
}

_PLATFORM_SCRAPERS = {
    "ashby": scrape_ashby,
    "greenhouse": scrape_greenhouse,
    "lever": scrape_lever,
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
    from src.extraction import usable_title
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
            best_result = candidate

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
            if result:
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
