"""Narrow first-party extraction for IABG, Allianz and Infineon.
Only observed links/attributes are followed. No invented offer IDs, authentication,
CAPTCHA handling, guessed private endpoints or location inference from search terms.
"""
import logging,re
from urllib.parse import urlparse,urljoin,parse_qs,urlencode,urlunparse
from bs4 import BeautifulSoup
from src.vacancy_extract import clean_label,usable_title,ROLE,dedupe,plain
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
    from src.public_boards import scrape_html_board
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
