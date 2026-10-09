"""Shared Munich-area scope and excluded recruitment-source checks."""
import re
from urllib.parse import urlparse
EXCLUDED_LOCATION = re.compile(r"\b(?:zurich|zürich|zuerich|switzerland|schweiz|suisse|svizzera|basel|basle|bern|berne|geneva|genève|geneve|lausanne|lucerne|luzern|zug|winterthur|kloten|wallisellen|dübendorf|duebendorf|opfikon|adliswil|horgen|dietikon|uster|regensdorf|schlieren|volketswil|wetzikon|thalwil)\b", re.I)
def excluded_agency(name='', url=''):
    compact = re.sub(r'[^a-z0-9]', '', str(name).lower())
    host = urlparse(str(url)).netloc.lower()
    return any(x in compact for x in ('randstad','michaelpage','michalpage')) or any(x in host for x in ('randstad','michaelpage'))
def excluded_source(entry):
    return excluded_agency(entry.get('name',''), entry.get('careers_url','')) or bool(EXCLUDED_LOCATION.search(' '.join(str(entry.get(k,'')) for k in ('city','country','name'))))
def allowed_job(job):
    return job.get('city') == 'Munich' and not excluded_agency(job.get('company',''),job.get('url','')) and not EXCLUDED_LOCATION.search(str(job.get('location','')))
