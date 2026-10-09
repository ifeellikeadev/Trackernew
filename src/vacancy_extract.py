"""Structured vacancy extraction; no guessed ATS tokens or dates."""
import json,re
from urllib.parse import urljoin,urlparse
from bs4 import BeautifulSoup

def plain(value):
    return BeautifulSoup(str(value or ''),'html.parser').get_text(' ',strip=True)

def walk(value):
    if isinstance(value,dict):
        kind=value.get('@type',[])
        if kind=='JobPosting' or isinstance(kind,list) and 'JobPosting' in kind:
            yield value
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

def structured_jobs(html,url):
    soup=BeautifulSoup(html,'html.parser');jobs=[]
    for script in soup.find_all('script',type='application/ld+json'):
        try:data=json.loads(script.string or script.get_text())
        except (ValueError,TypeError):continue
        for item in walk(data):
            title=plain(item.get('title'))
            target=item.get('url') or (url if len(list(walk(data)))==1 else '')
            if not title or not isinstance(target,str) or not target:continue
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

def html_jobs(html,url):
    structured=structured_jobs(html,url)
    if structured:return structured
    soup=BeautifulSoup(html,'html.parser');jobs=[]
    path_hint=re.compile(r'/(?:jobs?|positions?|postings?|vacancies|stellenangebote|job-detail|o)/[^/?#]+',re.I)
    nav=re.compile(r'^(?:all |view |search |open |our )?(?:jobs|careers|positions|vacancies|stellenangebote|karriere)(?: now)?$',re.I)
    for a in soup.find_all('a',href=True):
        title=a.get_text(' ',strip=True);target=urljoin(url,a['href'])
        if not 6<=len(title)<=180 or nav.match(title) or urlparse(target).scheme not in ('http','https'):continue
        if not path_hint.search(urlparse(target).path):continue
        jobs.append(dict(title=title,location='',url=target,description='',posted_date='',_extraction='html_link_unverified'))
    return dedupe(jobs)

def ashby_jobs(data):
    if not isinstance(data,dict) or not isinstance(data.get('jobs'),list):raise ValueError('Ashby jobs response shape invalid')
    jobs=[]
    for j in data['jobs']:
        if j.get('isListed') is False:continue
        if not j.get('title') or not j.get('jobUrl'):continue
        loc=[j.get('location','')]+[x.get('location','') for x in j.get('secondaryLocations',[]) if isinstance(x,dict)]
        jobs.append(dict(title=j['title'],location='; '.join(filter(None,loc)),url=j['jobUrl'],description=j.get('descriptionPlain') or plain(j.get('descriptionHtml')),posted_date=j.get('publishedAt') or '',_extraction='ashby_api'))
    return dedupe(jobs)
