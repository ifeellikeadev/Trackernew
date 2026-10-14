"""Evidence-based vacancy extraction; unknown dates and locations stay unknown."""
import json,re
from urllib.parse import urljoin,urlparse,parse_qs
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

NAV = re.compile(r'^(?:contact(?: us)?|kontakt|faqs?(?:.*)?|support|help(?:.*)?|review accommodations.*|privacy.*|datenschutz.*|impressum|legal.*|cookie.*|sign in|log in|register|search(?: jobs)?|all jobs|view all jobs|careers|karriere|jobs|stellenangebote|open positions|job alerts?|talent community|benefits|our culture|about us|locations|saved jobs)$',re.I)
APPLY = re.compile(r'^(?:apply(?: now| here| for this job)?|bewerben|jetzt bewerben|direkt bewerben|mehr erfahren|learn more|read more|details|view job|view details|job details)$',re.I)
ROLE = re.compile(r'\b(?:manager|management|engineer|engineering|consultant|specialist|analyst|coordinator|director|head|lead|developer|architect|officer|assistant|associate|buyer|intern|technician|scientist|designer|administrator|controller|accountant|nurse|sales|recruiter|werkstudent|praktikant|leiter|leitung|referent|berater|entwickler|projekt|mitarbeiter|sachbearbeiter|kaufmann|kauffrau|fachkraft|monteur|techniker|ingenieur)\w*\b',re.I)
DETAIL = re.compile(r'/(?:jobs?|positions?|postings?|vacancies|stellenangebote|job-detail|o)/[^/?#]+',re.I)
BAD_PATH = re.compile(r'(?:^|/)(?:contact|kontakt|faq|support|privacy|legal|accommodations|benefits|culture|alerts?|search|saved|login|register)(?:/|$)',re.I)

def usable_title(title):
    return bool(3<=len(title)<=200 and not NAV.fullmatch(title) and not APPLY.fullmatch(title))

def structured_jobs(html,url):
    soup=BeautifulSoup(html,'html.parser');jobs=[]
    for script in soup.find_all('script',type='application/ld+json'):
        try:items=list(walk(json.loads(script.string or script.get_text())))
        except (ValueError,TypeError):continue
        for item in items:
            title=plain(item.get('title'));target=item.get('url') or (url if len(items)==1 else '')
            if not usable_title(title) or not isinstance(target,str) or not target:continue
            target=urljoin(url,target)
            if urlparse(target).scheme not in ('http','https'):continue
            jobs.append(dict(title=title,location=location(item.get('jobLocation')),url=target,description=plain(item.get('description')),posted_date=item.get('datePosted') or '',_extraction='jsonld'))
    return dedupe(jobs)

def dedupe(jobs):
    result=[];seen=set()
    for j in jobs:
        key=j.get('url','').split('#')[0]
        if key and key not in seen:seen.add(key);result.append(j)
    return result

def card_title(anchor):
    # Recover a nearby title only from a small card, not the whole job board.
    for parent in list(anchor.parents)[:3]:
        if parent.name in ('body','html','nav','footer','header'):break
        headings=parent.select('h2,h3,h4,[itemprop="title"],.job-title,.position-title')
        valid=list(dict.fromkeys(h.get_text(' ',strip=True) for h in headings if usable_title(h.get_text(' ',strip=True))))
        if len(valid)==1:return valid[0],parent
        if len(valid)>1:break
    return '',None

def html_jobs(html,url):
    structured=structured_jobs(html,url);soup=BeautifulSoup(html,'html.parser');jobs=[]
    for a in soup.find_all('a',href=True):
        if a.find_parent(['nav','footer','header']):continue
        target=urljoin(url,a['href']);parsed=urlparse(target);title=a.get_text(' ',strip=True)
        if parsed.scheme not in ('http','https') or parsed.path.lower().endswith(('.pdf','.png','.jpg','.css','.js')) or BAD_PATH.search(parsed.path):continue
        if NAV.fullmatch(title):continue
        query=parse_qs(parsed.query)
        detail=bool(DETAIL.search(parsed.path) or any(k.lower() in ('jobid','job_id','postingid','positionid') for k in query))
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
        if card is not None:
            tag=card.select_one('[itemprop="jobLocation"],.job-location,.position-location,[data-testid="job-location"]')
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
        jobs.append(dict(title=j['title'],location='; '.join(filter(None,loc)),url=j['jobUrl'],description=j.get('descriptionPlain') or plain(j.get('descriptionHtml')),posted_date=j.get('publishedAt') or '',_extraction='ashby_api'))
    return dedupe(jobs)
