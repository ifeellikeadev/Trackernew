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
    if row.get('title_unresolved',0):category,action='UNRESOLVED_JOB_TITLES','Some job titles could not be extracted. These are extraction gaps, not rejected job preferences. Review unresolved URL examples.'
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
        if row.get('pagination'):
            details += '<br>Pagination: ' + html.escape(json.dumps(row['pagination']))
        if row.get('unresolved_samples'):
            details += '<br>Unresolved examples: ' + html.escape(json.dumps(row['unresolved_samples'],ensure_ascii=False))
        for key in ('date_decisions','date_rejected_samples','date_kept_samples','location_rejected_samples','title_rejection_details','score_rejected_samples'):
            if row.get(key):details += '<br>'+html.escape(key.replace('_',' ').title())+': '+html.escape(json.dumps(row[key],ensure_ascii=False))
        samples=html.escape('; '.join(row.get('title_rejected_samples',[])))
        details += '<br>Rejected-title examples: '+samples if samples else ''
        body.append('<tr>'+cells+'<td>'+details+'</td></tr>')
    section='<h2>Scraper health</h2><p>Empty does not prove no vacancies. Eligible counts precede tracker deduplication. Link candidates may be unverified. Unresolved titles are extraction gaps, not title-filter rejections. Warnings include detail-page requests.</p><div class="scroll-wrap"><table><thead><tr>'+headers+'<th>Issues</th></tr></thead><tbody>'+''.join(body)+'</tbody></table></div>'
    text=path.read_text(encoding='utf-8');path.write_text(text.replace('</body>',section+'</body>'),encoding='utf-8')
