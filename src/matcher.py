"""
Filters and scores scraped jobs against config/cv_profile.yaml.

- A job is KEPT only if its title matches one of `title_must_match`.
- A job is then checked against the company's expected metro area
  (Munich or Zurich, including their surrounding satellite towns —
  Ottobrunn, Taufkirchen, Freising, Zug, Winterthur, etc.): if the
  scraped location text clearly names a place OUTSIDE that area, the
  job is dropped. This matters because several platforms (Lever,
  Greenhouse, SmartRecruiters, Workday) return a company's ENTIRE
  global job board in one call — e.g. Palantir Zurich's Lever board
  includes New York, London, DC, etc. alongside Zurich. Without this
  check, every one of those non-local postings would pass through
  just because the title matched "Project Manager."
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

# Keywords that count as "this location IS in the Munich metro area" /
# "this location IS in the Zurich metro area" — i.e. the city itself
# plus its commuter-belt satellite towns, since a job in Ottobrunn or
# Freising is just as reachable/relevant as one in Munich proper.
#
# Deliberately does NOT extend to all of Bavaria or all of Switzerland
# — Nuremberg, Augsburg, Regensburg, Geneva, Basel, Bern, etc. are
# real cities in their own right, ~1.5-3h away, not "the Munich/Zurich
# area." If you want a specific town added or removed, edit the lists
# below (see SETUP_GUIDE.md for how to make and upload this kind of
# change).
CITY_KEYWORDS = {
    "Munich": [
        "munich", "münchen", "muenchen",
        "ottobrunn", "taufkirchen", "manching", "garching",
        "oberpfaffenhofen", "unterschleissheim", "unterschleißheim",
        "ismaning", "unterföhring", "unterfoehring", "neubiberg",
        "poing", "feldkirchen", "holzkirchen", "dachau", "freising",
        "erding", "fürstenfeldbruck", "fuerstenfeldbruck", "starnberg",
        "germering", "gräfelfing", "graefelfing", "planegg", "gilching",
        "puchheim", "vaterstetten", "haar", "aschheim", "kirchheim",
    ],
    "Zurich": [
        "zurich", "zürich", "zuerich",
        "zug", "winterthur", "baden", "dietikon", "wallisellen",
        "dübendorf", "duebendorf", "opfikon", "kloten", "adliswil",
        "horgen", "meilen", "rüschlikon", "rueschlikon", "uster",
        "regensdorf", "schlieren", "volketswil", "wetzikon", "thalwil",
        "wädenswil", "waedenswil",
    ],
    # --- Dream-city expansion (see /areas/job-search.md context: score
    # 7+ postings only, visa-feasible locations given Italian + Nepali
    # spouse family reunification routes). Kept in the same dict/lookup
    # as Munich/Zurich so the existing location_status() logic just
    # works for these too — no separate matching path needed. ---
    "Copenhagen": ["copenhagen", "københavn", "kobenhavn"],
    "Oslo": ["oslo"],
    "Helsinki": ["helsinki", "helsingfors", "espoo"],
    "Vienna": ["vienna", "wien"],
    "Basel": ["basel", "bâle", "bale"],
    "Bern": ["bern", "berne"],
    "Geneva": ["geneva", "genève", "geneve"],
    "Lausanne": ["lausanne", "vevey", "apples"],
    "Lucerne": ["lucerne", "luzern"],
    "Vancouver": ["vancouver", "burnaby", "richmond"],
    "Perth": ["perth"],
    "Melbourne": ["melbourne"],
    "Sydney": ["sydney"],
    "Singapore": ["singapore"],
}

# Keywords that count as "this location is clearly somewhere else" even
# when it happens to also mention the right country in passing (e.g. a
# location string like "Germany (Remote)" without a specific city is
# treated as unconfirmed, not excluded - only a NAMED other city/region
# triggers exclusion).
OTHER_MAJOR_LOCATIONS = [
    "new york", "san francisco", "london", "paris", "warsaw", "krakow",
    "dublin", "amsterdam", "madrid", "barcelona", "lisbon", "milan",
    "vienna", "prague", "budapest", "singapore", "tokyo", "bangalore",
    "hyderabad", "delhi", "mumbai", "toronto", "seattle", "austin",
    "boston", "chicago", "los angeles", "washington", "atlanta",
    "berlin", "hamburg", "frankfurt", "cologne", "köln", "stuttgart",
    "düsseldorf", "duesseldorf", "leipzig", "dresden", "nuremberg",
    "augsburg", "regensburg", "würzburg", "wuerzburg", "ingolstadt",
    "geneva", "genève", "basel", "bern", "lausanne", "lucerne",
    "st. gallen", "st gallen", "chur", "lugano", "biel", "fribourg",
    "copenhagen", "københavn", "oslo", "helsinki", "vancouver",
    "perth", "melbourne", "sydney", "brisbane", "adelaide", "auckland",
]


def title_matches(title: str, must_match: list[str]) -> bool:
    t = title.lower()
    return any(term.lower() in t for term in must_match)


def location_status(location: str, expected_city: str) -> str:
    """
    Returns "confirmed" (location text names the expected city),
    "mismatch" (location text clearly names a different city), or
    "unconfirmed" (no location text available, or it's too generic
    to tell - e.g. just "Germany" or "Remote"). Callers drop
    "mismatch" and keep the other two, flagging "unconfirmed" in the
    tracker so you know it wasn't location-verified.
    """
    if not location:
        return "unconfirmed"
    loc = location.lower()
    expected_keywords = CITY_KEYWORDS.get(expected_city, [expected_city.lower()])
    if any(kw in loc for kw in expected_keywords):
        return "confirmed"
    if any(other in loc for other in OTHER_MAJOR_LOCATIONS):
        return "mismatch"
    return "unconfirmed"


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


def filter_and_score(
    jobs: list[dict[str, Any]], cv_profile: dict[str, Any], expected_city: str = ""
) -> list[dict[str, Any]]:
    must_match = cv_profile.get("title_must_match", [])
    keywords = cv_profile.get("scoring_keywords", [])
    markers = cv_profile.get("german_requirement_markers", [])
    ceiling = cv_profile.get("score_ceiling", DEFAULT_SCORE_CEILING)

    kept = []
    for job in jobs:
        title = job.get("title", "")
        if not title or not title_matches(title, must_match):
            continue

        loc_status = location_status(job.get("location", ""), expected_city)
        if loc_status == "mismatch":
            continue

        description = job.get("description", "")
        job["relevance_score"] = score_job_1_to_10(description, title, keywords, ceiling)
        job["german_required"] = flag_german_requirement(description, title, markers)
        job["location_status"] = loc_status  # "confirmed" or "unconfirmed"
        kept.append(job)
    return kept
