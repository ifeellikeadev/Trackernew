"""
Filters and scores scraped jobs against config/cv_profile.yaml.

- A job is KEPT only if its title matches one of `title_must_match`.
- Kept jobs get a `relevance_score` (sum of matched keyword weights)
  and a `german_required` flag, both used in the Excel output.
"""

from __future__ import annotations

from typing import Any


def title_matches(title: str, must_match: list[str]) -> bool:
    t = title.lower()
    return any(term.lower() in t for term in must_match)


def score_job(description: str, title: str, keywords: list[dict[str, Any]]) -> int:
    text = f"{title} {description}".lower()
    score = 0
    for kw in keywords:
        if kw["term"].lower() in text:
            score += int(kw["weight"])
    return score


def flag_german_requirement(description: str, title: str, markers: list[str]) -> bool:
    text = f"{title} {description}".lower()
    return any(marker.lower() in text for marker in markers)


def filter_and_score(jobs: list[dict[str, Any]], cv_profile: dict[str, Any]) -> list[dict[str, Any]]:
    must_match = cv_profile.get("title_must_match", [])
    keywords = cv_profile.get("scoring_keywords", [])
    markers = cv_profile.get("german_requirement_markers", [])

    kept = []
    for job in jobs:
        title = job.get("title", "")
        if not title or not title_matches(title, must_match):
            continue
        description = job.get("description", "")
        job["relevance_score"] = score_job(description, title, keywords)
        job["german_required"] = flag_german_requirement(description, title, markers)
        kept.append(job)
    return kept
