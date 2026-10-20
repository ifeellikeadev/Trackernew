"""Consolidated extraction implementation. See INSTALL.txt for migration details."""
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

NAV = re.compile(r'^(?:contact(?: us)?|kontakt|faqs?(?:.*)?|support|help(?:.*)?|review accommodations.*|privacy.*|datenschutz.*|impressum|legal(?: notice| information| disclaimer)?|cookie.*|sign in|log in|register|search(?: jobs)?|all jobs|view all jobs|careers|karriere|jobs|stellenangebote|open positions|job alerts?|talent community|benefits|our culture|about us|locations|saved jobs|actions|to the vacancies|apply unsolicited now|bewerberlogin|candidate login|my applications)$',re.I)
APPLY = re.compile(r'^(?:apply(?: now| here| for this job)?|bewerben|jetzt bewerben|direkt bewerben|mehr erfahren|learn more|read more|details|view job|view details|job details|view|hier|here|to the complete job advertisement|zur stellenanzeige|zur stellenausschreibung|zur bewerbung|zur online bewerbung)$',re.I)
ROLE = re.compile(r'\b(?:manager|management|engineer|engineering|consultant|specialist|analyst|coordinator|director|head|lead|developer|architect|officer|assistant|associate|buyer|intern|technician|scientist|designer|administrator|controller|accountant|nurse|sales|recruiter|werkstudent|praktikant|leiter|leitung|referent|berater|entwickler|projekt|mitarbeiter|sachbearbeiter|kaufmann|kauffrau|fachkraft|monteur|techniker|ingenieur)\w*\b',re.I)
DETAIL = re.compile(r'/(?:jobs?|positions?|postings?|vacancies|stellenangebote|job-detail|o)/[^/?#]+',re.I)
BAD_PATH = re.compile(r'(?:^|/)(?:contact|kontakt|faq|support|privacy|legal|accommodations|benefits|culture|alerts?|search|saved|saved-jobs|login|register|hvhapply|apply|application|bewerbung|bewerben)(?:/|$)',re.I)

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
