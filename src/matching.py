"""Consolidated matching implementation. See INSTALL.txt for migration details."""
from __future__ import annotations

# ========================================================================
# location_priority: consolidated implementation
# ========================================================================
"""Location priority uses explicit vacancy location text, never company HQ."""
import re,unicodedata

def normalize(value):
    text=str(value or '').lower().replace('ä','ae').replace('ö','oe').replace('ü','ue').replace('ß','ss')
    return ''.join(c for c in unicodedata.normalize('NFKD',text) if not unicodedata.combining(c))

def contains(text,word):
    return re.search(r'(?<!\w)'+re.escape(normalize(word))+r'(?!\w)',normalize(text)) is not None

PREFERRED={
 'Taufkirchen':['taufkirchen'],
 'Unterhaching':['unterhaching'],
 'Ottobrunn':['ottobrunn'],
 'Unterfoehring':['unterfoehring','unterföhring'],
 'Bogenhausen':['bogenhausen'],
 'Werksviertel':['werksviertel'],
 'Lehel':['lehel'],
 'Downtown':['altstadt','innenstadt','city centre','city center','downtown','marienplatz','odeonsplatz','karlsplatz','sendlinger tor'],
}
MUNICH_LABELS=['munich','münchen','muenchen','munich metropolitan region','metropolregion münchen']
REGIONAL_LABELS=['augsburg','ingolstadt','kaufbeuren','landshut','altötting','bad tölz','wolfratshausen','dachau','dillingen an der donau','dingolfing','landau an der isar','donau-ries','ebersberg','freising','fürstenfeldbruck','garmisch-partenkirchen','kelheim','landsberg am lech','miesbach','mühldorf am inn','ostallgäu','pfaffenhofen an der ilm','starnberg']

def area_priority(location):
    for label,terms in PREFERRED.items():
        if any(contains(location,t) for t in terms):
            # Other towns share the name Taufkirchen: do not prioritise
            # an explicit contradictory district as the Hachinger Tal site.
            if label=='Taufkirchen' and any(contains(location,t) for t in ['vils','erding','mühldorf']):continue
            return 0,label
    if any(contains(location,t) for t in ['munich','münchen','muenchen']):return 1,'Munich (area unspecified)'
    return 2,'Wider region / area unspecified'

def sort_key(location,score):
    rank,_=area_priority(location)
    return rank,-(score if isinstance(score,(int,float)) else 0)


# ========================================================================
# job_scope: consolidated implementation
# ========================================================================
"""Shared Munich-area scope and excluded recruitment-source checks."""
import re
from urllib.parse import urlparse
SCOPE_EXCLUDED_LOCATION = re.compile(r"\b(?:zurich|zürich|zuerich|switzerland|schweiz|suisse|svizzera|basel|basle|bern|berne|geneva|genève|geneve|lausanne|lucerne|luzern|zug|winterthur|kloten|wallisellen|dübendorf|duebendorf|opfikon|adliswil|horgen|dietikon|uster|regensdorf|schlieren|volketswil|wetzikon|thalwil)\b", re.I)
def excluded_agency(name='', url=''):
    compact = re.sub(r'[^a-z0-9]', '', str(name).lower())
    host = urlparse(str(url)).netloc.lower()
    return any(x in compact for x in ('randstad','michaelpage','michalpage')) or any(x in host for x in ('randstad','michaelpage'))
def excluded_source(entry):
    return excluded_agency(entry.get('name',''), entry.get('careers_url','')) or bool(SCOPE_EXCLUDED_LOCATION.search(' '.join(str(entry.get(k,'')) for k in ('city','country','name'))))
def allowed_job(job):
    return job.get('city') == 'Munich' and not excluded_agency(job.get('company',''),job.get('url','')) and not SCOPE_EXCLUDED_LOCATION.search(str(job.get('location','')))


# ========================================================================
# job_recency: consolidated implementation
# ========================================================================
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import re
MAX_JOB_AGE_DAYS = 21

def today_local():
    return datetime.now(ZoneInfo("Europe/Berlin")).date()

def parse_posted(value, *, today=None, allow_relative=False):
    today = today or today_local()
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo("Europe/Berlin")).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone(ZoneInfo("Europe/Berlin")).date() if parsed.tzinfo else parsed.date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", text):
        try:
            return datetime.strptime(text, "%d.%m.%Y").date()
        except ValueError:
            return None
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:return datetime.strptime(text,fmt).date()
        except ValueError:pass
    if not allow_relative:
        return None
    t = re.sub(r"^(?:posted|published|veröffentlicht)\s+", "", text.lower()).strip()
    t = re.sub(r"(?<=\d)\+", "", t)
    if t in ("today", "heute", "just posted"):
        return today
    if t in ("yesterday", "gestern"):
        return today - timedelta(days=1)
    m = re.fullmatch(r"(\d+)\s+(day|days|week|weeks|hour|hours)\s+ago", t)
    if m:
        n = int(m.group(1)); unit = m.group(2)
        days = n * 7 if unit.startswith("week") else n if unit.startswith("day") else n // 24
        return today - timedelta(days=days)
    m = re.fullmatch(r"vor\s+(\d+)\s+(tag|tagen|tage|woche|wochen)", t)
    if m:
        n = int(m.group(1))
        return today - timedelta(days=n * 7 if m.group(2).startswith("woch") else n)
    return None

def keep_posted(value, *, today=None, allow_relative=False):
    today = today or today_local()
    posted = parse_posted(value, today=today, allow_relative=allow_relative)
    return posted is None or posted > today or (today - posted).days <= MAX_JOB_AGE_DAYS

def date_decision(job, *, today=None):
    today=today or today_local()
    raw=job.get('_date_raw',job.get('posted_date',''))
    field=job.get('_date_field','posted_date_unspecified')
    kind=job.get('_date_kind','publication_unspecified')
    observed=job.get('_date_observed_on',today.isoformat())
    if kind in ('creation_unverified','update','first_seen'):
        return dict(keep=True,reason='not_a_verified_publication_date',raw=str(raw or ''),field=field,kind=kind,parsed='',age_days=None,observed_on=observed,source=job.get('_date_source',job.get('url','')))
    # Prefer an absolute date already anchored at retrieval. Relative text is
    # parsed against this run only; it is never refreshed in saved workbook rows.
    value=job.get('posted_date','')
    posted=parse_posted(value,today=today,allow_relative=True)
    if posted is None:reason='missing_date' if not str(value or '').strip() else 'unparseable_date'
    elif posted>today:reason='future_date_needs_review'
    elif (today-posted).days>MAX_JOB_AGE_DAYS:reason='older_than_21_days'
    else:reason='within_21_days'
    return dict(keep=reason!='older_than_21_days',reason=reason,raw=str(raw or ''),field=field,kind=kind,parsed=posted.isoformat() if posted else '',age_days=(today-posted).days if posted else None,observed_on=observed,source=job.get('_date_source',job.get('url','')))

def filter_recent_jobs(jobs, *, today=None):
    today=today or today_local();kept=[]
    for job in jobs:
        decision=date_decision(job,today=today)
        if not decision['keep']:continue
        item=dict(job);item['_date_audit']=decision
        if decision['reason']=='not_a_verified_publication_date':item['posted_date']=''
        elif decision['parsed'] and decision['reason']!='future_date_needs_review':item['posted_date']=decision['parsed']
        kept.append(item)
    return kept

def prune_old_rows(ws, columns, *, today=None):
    today = today or today_local()
    if "Job Posted" not in columns:
        return 0
    col = columns.index("Job Posted") + 1
    removed = 0
    for row in range(ws.max_row, 1, -1):
        if not keep_posted(ws.cell(row=row, column=col).value, today=today):
            ws.delete_rows(row)
            removed += 1
    return removed


# ========================================================================
# matcher: consolidated implementation
# ========================================================================
"""Munich-region location matching without a driving-time or distance cutoff.
A regional town list still excludes unrelated locations; it is not a commute assessment.
"""



from typing import Any

import re
EXCLUDED_LOCATION = re.compile(r"\b(?:zurich|zürich|zuerich|switzerland|schweiz|suisse|svizzera|basel|basle|bern|berne|geneva|genève|geneve|lausanne|lucerne|luzern|zug|winterthur|kloten|wallisellen|dübendorf|duebendorf|opfikon|adliswil|horgen|dietikon|uster|regensdorf|schlieren|volketswil|wetzikon|thalwil)\b", re.I)

DEFAULT_SCORE_CEILING = 15

CITY_KEYWORDS = {'Munich': ['munich', 'münchen', 'muenchen', 'ottobrunn', 'taufkirchen', 'unterhaching', 'neubiberg', 'haar', 'putzbrunn', 'vaterstetten', 'parsdorf', 'poing', 'aschheim', 'kirchheim', 'feldkirchen', 'ismaning', 'unterföhring', 'unterfoehring', 'garching', 'freising', 'erding', 'hallbergmoos', 'fürstenfeldbruck', 'fuerstenfeldbruck', 'dachau', 'unterschleißheim', 'unterschleissheim', 'oberschleißheim', 'oberschleissheim', 'gilching', 'oberpfaffenhofen', 'weßling', 'wessling', 'germering', 'gräfelfing', 'graefelfing', 'planegg', 'martinsried', 'krailling', 'puchheim', 'starnberg', 'holzkirchen', 'grünwald', 'gruenwald', 'pullach', 'baierbrunn', 'höhenkirchen', 'hoehenkirchen', 'siegertsbrunn', 'brunnthal', 'sauerlach', 'hohenbrunn', 'bogenhausen', 'werksviertel', 'lehel', 'altstadt', 'haidhausen', 'berg am laim', 'obersendling', 'trudering', 'riem', 'schwabing']}

CITY_KEYWORDS['Munich'] = list(dict.fromkeys(CITY_KEYWORDS['Munich'] + REGIONAL_LABELS + MUNICH_LABELS))

def normalized_title(value):
    import unicodedata
    text=unicodedata.normalize('NFKC',str(value or '')).casefold()
    text=re.sub(r'[\s\-\u2010-\u2015_]+',' ',text)
    # Equivalent spelling only, not new occupational categories.
    text=re.sub(r'\bprojekt\s+(manager|leiter)(in)?\b',r'projekt\1\2',text)
    text=re.sub(r'\bproject\s*manager\b','project manager',text)
    text=re.sub(r'\b(programme|program)\s*manager\b',r'\1 manager',text)
    return text.strip()

def excluded_seniority(title):
    """Explicit junior/entry-level title labels only; never description mentions."""
    import unicodedata
    text=unicodedata.normalize('NFKC',str(title or '')).casefold()
    text=''.join(c for c in text if unicodedata.category(c)!='Cf')
    text=re.sub(r'[\s\u2010-\u2015_-]+',' ',text)
    return bool(re.search(r'(?<!\w)(?:junior|jr\.?|entry\s*level|berufseinsteiger(?:in|innen)?)(?!\w)',text))

def title_decision(title,must_match,must_not_match=None):
    if excluded_seniority(title):return {'keep':False,'reason':'excluded_junior_seniority','term':'junior / entry-level'}
    text=normalized_title(title)
    excluded=next((term for term in (must_not_match or []) if normalized_title(term) in text),None)
    included=next((term for term in must_match if normalized_title(term) in text),None)
    if excluded:return {'keep':False,'reason':'excluded_title_term','term':excluded}
    return {'keep':bool(included),'reason':'matched_title_term' if included else 'no_requested_title_term','term':included or ''}

def title_matches(title,must_match,must_not_match=None):
    return title_decision(title,must_match,must_not_match)['keep']

OUTSIDE_LOCATION=re.compile(r'\b(?:berlin|hamburg|frankfurt|stuttgart|dresden|leipzig|bremen|hannover|cologne|köln|koeln|düsseldorf|duesseldorf|nürnberg|nuernberg|nuremberg|karlsruhe|mannheim|london|paris|vienna|wien|graz|amsterdam|warsaw|prague|poland|czech|united states|united kingdom|india|china|singapore|canada|australia)\b',re.I)

def location_decision(job):
    # Only explicit vacancy location fields, never corporate/footer/body mentions.
    evidence=str(job.get('location') or '').strip()
    if EXCLUDED_LOCATION.search(evidence):return {'status':'excluded','reason':'excluded_swiss_location','evidence':evidence,'source':job.get('_location_source','vacancy_location')}
    if find_matching_city(evidence):return {'status':'confirmed','reason':'explicit_region_match','evidence':evidence,'source':job.get('_location_source','vacancy_location')}
    if not evidence:return {'status':'unconfirmed','reason':'missing_location','evidence':'','source':'none'}
    if OUTSIDE_LOCATION.search(evidence) and not re.search(r'available in|multiple|various|remote|\+\s*\d+',evidence,re.I):return {'status':'outside','reason':'explicit_outside_location','evidence':evidence,'source':job.get('_location_source','vacancy_location')}
    if re.search(r'available in|\+\s*\d+|multiple|various|remote|hybrid|deutschland|germany|europe|emea',evidence,re.I):
        return {'status':'unconfirmed','reason':'broad_remote_or_multiple_location','evidence':evidence,'source':job.get('_location_source','vacancy_location')}
    if OUTSIDE_LOCATION.search(evidence):return {'status':'outside','reason':'explicit_outside_location','evidence':evidence,'source':job.get('_location_source','vacancy_location')}
    return {'status':'unconfirmed','reason':'unrecognised_location_needs_review','evidence':evidence,'source':job.get('_location_source','vacancy_location')}

def location_status(location, expected_city):
    if not location:
        return 'unconfirmed'
    if expected_city != 'Munich' or EXCLUDED_LOCATION.search(location):
        return 'mismatch'
    return 'confirmed' if find_matching_city(location) else 'unconfirmed'

def filter_by_title_and_location(
    jobs: list[dict[str, Any]], cv_profile: dict[str, Any], expected_city: str = ""
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    must_match = cv_profile.get("title_must_match", [])
    must_not_match = cv_profile.get("title_must_not_match", [])

    kept = []
    title_matched_count = 0
    for job in jobs:
        title = job.get("title", "")
        if not title or not title_matches(title, must_match, must_not_match):
            continue
        title_matched_count += 1

        loc_status = location_status(job.get("location", ""), expected_city)
        if loc_status != "confirmed":
            continue

        kept.append(job)

    stats = {"title_matched": title_matched_count, "location_confirmed": len(kept)}
    return kept, stats



MAIN_LIST_CITIES = {"Munich"}
SWISS_CITIES = set()  # Compatibility only; no approved Swiss targets.
ALL_APPROVED_CITIES = list(CITY_KEYWORDS.keys())  # Munich, Zurich, Basel, Bern, Geneva, Lausanne, Lucerne


def find_matching_city(location, candidate_cities=None):
    if not location or EXCLUDED_LOCATION.search(location):
        return None
    if candidate_cities is not None and 'Munich' not in candidate_cities:
        return None
    loc = location.lower()
    return 'Munich' if any(contains(loc, kw) for kw in CITY_KEYWORDS['Munich']) else None

def filter_by_title_only(jobs: list[dict[str, Any]], cv_profile: dict[str, Any]) -> list[dict[str, Any]]:
    must_match = cv_profile.get("title_must_match", [])
    must_not_match = cv_profile.get("title_must_not_match", [])
    return [
        job for job in jobs
        if job.get("title") and title_matches(job["title"], must_match, must_not_match)
    ]


def resolve_city_for_job(job: dict[str, Any], search_text: str | None = None) -> str | None:
    matched_city = find_matching_city(search_text if search_text is not None else job.get("location", ""))
    if matched_city is None:
        return None
    job["matched_city"] = matched_city
    return matched_city


def extract_location_snippet(text: str, matched_city: str, window: int = 30) -> str:
    loc = text.lower()
    for kw in CITY_KEYWORDS.get(matched_city, [matched_city.lower()]):
        idx = loc.find(kw)
        if idx != -1:
            start = max(0, idx - window)
            end = min(len(text), idx + len(kw) + window)
            snippet = text[start:end].strip()
            return ("..." if start > 0 else "") + snippet + ("..." if end < len(text) else "")
    return matched_city


def filter_by_title_and_any_city(
    jobs: list[dict[str, Any]], cv_profile: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    title_matched = filter_by_title_only(jobs, cv_profile)
    kept = [job for job in title_matched if resolve_city_for_job(job) is not None]
    stats = {"title_matched": len(title_matched), "location_confirmed": len(kept)}
    return kept, stats


def _raw_score(description: str, title: str, keywords: list[dict[str, Any]]) -> int:
    text = f"{title} {description}".lower()
    score = 0
    for kw in keywords:
        if kw["term"].lower() in text:
            score += int(kw["weight"])
    return score


def score_job_1_to_10(description: str, title: str, keywords: list[dict[str, Any]], ceiling: int) -> int:
    raw = _raw_score(description, title, keywords)
    scaled = round((raw / ceiling) * 10) if ceiling else 0
    return max(1, min(10, scaled))


def score_jobs(jobs: list[dict[str, Any]], cv_profile: dict[str, Any]) -> list[dict[str, Any]]:
    keywords = cv_profile.get("scoring_keywords", [])
    ceiling = cv_profile.get("score_ceiling", DEFAULT_SCORE_CEILING)
    for job in jobs:
        job["relevance_score"] = score_job_1_to_10(
            job.get("description", ""), job.get("title", ""), keywords, ceiling
        )
    return jobs


# RELATED_ROLE_TRIAL_V1: does not change title_decision or keyword scoring.
def _responsibility_text(value):
    import html, unicodedata
    text = html.unescape(str(value or '')).casefold()
    text = text.replace('ä','ae').replace('ö','oe').replace('ü','ue').replace('ß','ss')
    text = unicodedata.normalize('NFKC', text)
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text)).strip()

def related_role_candidate(title, profile):
    settings = profile.get('related_roles') or {}
    if settings.get('enabled') is not True:
        return False
    direct = title_decision(title, profile.get('title_must_match', []), profile.get('title_must_not_match', []))
    if direct['keep'] or direct['reason'] != 'no_requested_title_term':
        return False
    text = normalized_title(title)
    return any(re.search(pattern, text, re.I) for pattern in settings.get('candidate_title_patterns', []))

def role_decision(job, profile):
    direct = title_decision(job.get('title',''), profile.get('title_must_match', []), profile.get('title_must_not_match', []))
    if direct['keep']:
        return dict(direct, match_type='Direct role', match_reason='Title: '+direct['term'], groups=[])
    if not related_role_candidate(job.get('title',''), profile):
        return dict(direct, match_type='', match_reason='', groups=[])
    settings = profile['related_roles']
    text = _responsibility_text(job.get('description',''))
    groups, evidence = [], {}
    for group, terms in settings.get('responsibility_groups', {}).items():
        hits = [term for term in terms if re.search(r'(?<!\w)'+re.escape(_responsibility_text(term))+r'(?!\w)', text)]
        if hits:
            groups.append(group); evidence[group] = hits
    required = settings.get('required_any_group', [])
    keep = len(groups) >= max(2, int(settings.get('min_responsibility_groups', 2))) and (not required or any(g in groups for g in required))
    return dict(keep=keep, reason='related_responsibilities_match' if keep else ('related_description_missing' if not text else 'related_insufficient_evidence'), term='', match_type='Related role' if keep else '', match_reason='Responsibilities: '+', '.join(groups) if keep else '', groups=groups, evidence=evidence)
