"""Munich-region location matching without a driving-time or distance cutoff.
A regional town list still excludes unrelated locations; it is not a commute assessment.
"""

from __future__ import annotations

from typing import Any
from src.location_priority import REGIONAL_LABELS, MUNICH_LABELS, contains
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

def title_decision(title,must_match,must_not_match=None):
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
