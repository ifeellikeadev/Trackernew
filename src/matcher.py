"""
Filters and scores scraped jobs against config/cv_profile.yaml.

- A job is KEPT only if its title matches one of `title_must_match`.
- Kept jobs get a `relevance_score` on a 1-10 scale (10 = best match to
  your CV) and a `german_required` flag, both used in the Excel output.
"""

from __future__ import annotations

from typing import Any

# How the 1-10 scale works:
#   1. Add up the weight of every scoring_keyword found in the title +
#      description (this is the "raw" score - unbounded).
#   2. Divide by SCORE_CEILING and scale to 10, capping at 10 and
#      flooring at 1 (a job that passed the title filter always shows
#      at least 1, never 0).
#   3. SCORE_CEILING is set so that a genuinely strong match (title hit
#      + several keyword hits) lands around 8-10, and a bare-minimum
#      match (title hit only, few/no keyword hits) lands around 1-3.
#   Adjust SCORE_CEILING in config/cv_profile.yaml if scores all cluster
#   too high or too low once you've seen real results.
DEFAULT_SCORE_CEILING = 15


def title_matches(title: str, must_match: list[str]) -> bool:
    t = title.lower()
    return any(term.lower() in t for term in must_match)


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


def flag_german_requirement(description: str, title: str, markers: list[str]) -> bool:
    text = f"{title} {description}".lower()
    return any(marker.lower() in text for marker in markers)


def filter_and_score(jobs: list[dict[str, Any]], cv_profile: dict[str, Any]) -> list[dict[str, Any]]:
    must_match = cv_profile.get("title_must_match", [])
    keywords = cv_profile.get("scoring_keywords", [])
    markers = cv_profile.get("german_requirement_markers", [])
    ceiling = cv_profile.get("score_ceiling", DEFAULT_SCORE_CEILING)

    kept = []
    for job in jobs:
        title = job.get("title", "")
        if not title or not title_matches(title, must_match):
            continue
        description = job.get("description", "")
        job["relevance_score"] = score_job_1_to_10(description, title, keywords, ceiling)
        job["german_required"] = flag_german_requirement(description, title, markers)
        kept.append(job)
    return kept
