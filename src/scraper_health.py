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
    return dict(company=company['name'],url=company.get('careers_url',''),status='',requests=0,raw_candidates=0,date_rejected=0,title_rejected=0,location_rejected=0,score_rejected=0,eligible=0,unknown_dates=0,detail_fetches=0,methods=[],issues=[],seconds=0)

def finish(row,error=None):
    row['issues']=list(dict.fromkeys(row['issues']))
    if error:row['status']='ERROR';row['issues'].append(str(error)[:300])
    elif row['issues']:row['status']='PARTIAL_OR_WARNING' if row['raw_candidates'] else 'FAILED_OR_BLOCKED'
    elif not row['raw_candidates']:row['status']='EMPTY_UNVERIFIED'
    elif row['eligible']:row['status']='MATCHES_FOUND'
    else:row['status']='NO_MATCHES_IN_EXTRACTED_RESULTS'
    return row

def save_report(rows,path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'generated_at':datetime.now(timezone.utc).isoformat(),'scope':'This reports extracted candidates, not proof that every vacancy was retrieved. Empty is not a certified no-vacancy result. Eligible is before tracker deduplication.','companies':rows},indent=2),encoding='utf-8')

def append_to_page(rows,path):
    path=Path(path)
    if not path.exists():return
    cols=['company','status','raw_candidates','date_rejected','title_rejected','location_rejected','score_rejected','eligible','unknown_dates','detail_fetches','requests','seconds']
    headers=''.join('<th>'+html.escape(c.replace('_',' ').title())+'</th>' for c in cols)
    body=[]
    for row in rows:
        cells=''.join('<td>'+html.escape(str(row.get(c,'')))+'</td>' for c in cols)
        details=html.escape('; '.join(row['issues']) or 'No captured HTTP/browser warnings')
        body.append('<tr>'+cells+'<td>'+details+'</td></tr>')
    section='<h2>Scraper health</h2><p>Empty does not prove no vacancies. Eligible counts precede tracker deduplication. Link candidates may be unverified. Warnings include detail-page requests.</p><div class="scroll-wrap"><table><thead><tr>'+headers+'<th>Issues</th></tr></thead><tbody>'+''.join(body)+'</tbody></table></div>'
    text=path.read_text(encoding='utf-8');path.write_text(text.replace('</body>',section+'</body>'),encoding='utf-8')
