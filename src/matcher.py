
from __future__ import annotations

from typing import Any
import re
from src.job_scope import EXCLUDED_LOCATION

DEFAULT_SCORE_CEILING = 15

CITY_KEYWORDS = {'Munich': ['munich', 'münchen', 'muenchen', 'ottobrunn', 'taufkirchen', 'unterhaching', 'neubiberg', 'haar', 'putzbrunn', 'poing', 'aschheim', 'kirchheim', 'feldkirchen', 'ismaning', 'unterföhring', 'unterfoehring', 'garching', 'erding', 'fürstenfeldbruck', 'hallbergmoos', 'freising', 'vaterstetten', 'bogenhausen', 'werksviertel', 'lehel', 'altstadt']}

def title_matches(title: str, must_match: list[str], must_not_match: list[str] | None = None) -> bool:
    t = title.lower()
    if not any(term.lower() in t for term in must_match):
        return False
    if must_not_match and any(term.lower() in t for term in must_not_match):
        return False
    return True


def location_status(location: str, expected_city: str) -> str:
    if not location:
        return "unconfirmed"
    loc = location.lower()
    if EXCLUDED_LOCATION.search(loc):
        return "mismatch"
    expected_keywords = CITY_KEYWORDS.get(expected_city, [expected_city.lower()])
    if any(kw in loc for kw in expected_keywords):
        return "confirmed"
    return "mismatch"
    return "unconfirmed"


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
ALL_APPROVED_CITIES = list(CITY_KEYWORDS.keys())  # Munich, Zurich, Basel, Bern, Geneva, Lausanne, Lucerne


def find_matching_city(location, candidate_cities=None):
    if not location or EXCLUDED_LOCATION.search(location):
        return None
    if candidate_cities is not None and 'Munich' not in candidate_cities:
        return None
    loc = location.lower()
    for kw in CITY_KEYWORDS['Munich']:
        if re.search(r'(?<!\w)' + re.escape(kw) + r'(?!\w)', loc):
            return 'Munich'
    return None

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
