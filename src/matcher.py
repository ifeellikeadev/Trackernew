"""
Filters and scores scraped jobs against config/cv_profile.yaml.

Two-step pipeline (split so main.py can enrich description text in
between — see src/ats_scrapers.py, fetch_description_fallback):

  1. filter_by_title_and_location(jobs, cv_profile, expected_city):
     - KEEPS a job only if its title matches one of `title_must_match`.
     - Then checks it against the company's expected metro area
       (Munich or Zurich, including satellite towns — Ottobrunn,
       Taufkirchen, Freising, Zug, Winterthur, etc. — or one of the
       dream cities): if the location text doesn't explicitly confirm
       the target area, the job is dropped. This matters because
       several platforms (Lever, Greenhouse, SmartRecruiters, Workday)
       return a company's ENTIRE global job board in one call — e.g.
       a Munich-tagged company's board can include a New York or Abu
       Dhabi posting alongside genuinely local ones.
     - Does NOT score yet — that happens after enrichment.

  2. score_jobs(jobs, cv_profile):
     - Adds `relevance_score` (1-10 scale, 10 = best match to your CV)
       to each job. Does not filter anything out by score — every job
       that passed step 1 is kept and shown, regardless of score. The
       score is for sorting/ranking only.

No score threshold is applied anywhere in this file — main_min_score
and dream_city_min_score in cv_profile.yaml both default to 0 (no
floor), reflecting that a title+location match is considered
worth seeing regardless of how strong the keyword match is.
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
    # --- Dream-city expansion (now includes metro-area satellite
    # towns, same approach as Munich/Zurich — the city itself plus
    # nearby commuter towns actually within reach, not the whole
    # surrounding region) ---
    "Copenhagen": [
        "copenhagen", "københavn", "kobenhavn",
        "frederiksberg", "gentofte", "lyngby", "kongens lyngby",
        "ballerup", "glostrup", "hvidovre", "rødovre", "rodovre",
        "taastrup",
    ],
    "Oslo": ["oslo", "bærum", "baerum", "asker", "lillestrøm", "lillestrom", "lørenskog", "lorenskog"],
    "Helsinki": ["helsinki", "helsingfors", "espoo", "vantaa", "kauniainen"],
    "Vienna": ["vienna", "wien", "klosterneuburg", "schwechat", "mödling", "modling", "baden bei wien"],
    "Basel": [
        "basel", "bâle", "bale",
        "allschwil", "muttenz", "reinach", "binningen", "pratteln",
        "birsfelden", "riehen", "bettingen", "arlesheim", "aesch",
        "liestal", "münchenstein", "muenchenstein", "oberwil",
        "therwil", "sissach", "bubendorf",
    ],
    "Bern": [
        "bern", "berne",
        "köniz", "koniz", "ostermundigen", "zollikofen",
        "muri bei bern", "ittigen", "bolligen", "worb", "belp",
        "wabern", "kirchlindach", "thun",
    ],
    "Geneva": [
        "geneva", "genève", "geneve",
        "meyrin", "carouge", "vernier", "lancy", "nyon",
        "onex", "thônex", "thonex", "plan-les-ouates", "grand-saconnex",
        "bernex", "versoix", "chêne-bourg", "chene-bourg",
    ],
    "Lausanne": [
        "lausanne", "vevey", "apples", "pully", "renens", "morges",
        "prilly", "epalinges", "le mont-sur-lausanne", "mont-sur-lausanne",
        "chavannes-près-renens", "chavannes-pres-renens", "crissier", "ecublens",
    ],
    "Lucerne": [
        "lucerne", "luzern",
        "kriens", "emmen", "horw",
        "ebikon", "adligenswil", "root", "malters", "meggen",
    ],
    "Amsterdam": [
        "amsterdam", "amstelveen", "diemen", "zaandam", "schiphol", "haarlem",
        "hoofddorp", "zaanstad",
    ],
    "Rotterdam": [
        "rotterdam", "schiedam", "papendrecht", "vlaardingen",
        "capelle aan den ijssel", "spijkenisse", "barendrecht",
    ],
    "Vancouver": [
        "vancouver", "burnaby", "richmond",
        "surrey", "coquitlam", "north vancouver", "new westminster", "delta",
    ],
    "Perth": ["perth", "fremantle", "joondalup", "rockingham"],
    "Melbourne": ["melbourne", "dandenong", "frankston", "box hill"],
    "Sydney": ["sydney", "parramatta", "north sydney", "chatswood"],
    "Singapore": ["singapore"],  # city-state, no separate metro area to add
    "Berlin": [
        "berlin", "potsdam", "falkensee", "oranienburg", "bernau",
        "teltow", "kleinmachnow", "hennigsdorf",
    ],
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
    "hamburg", "frankfurt", "cologne", "köln", "stuttgart",
    "düsseldorf", "duesseldorf", "leipzig", "dresden", "nuremberg",
    "augsburg", "regensburg", "würzburg", "wuerzburg", "ingolstadt",
    "geneva", "genève", "basel", "bern", "lausanne", "lucerne",
    "st. gallen", "st gallen", "chur", "lugano", "biel", "fribourg",
    "copenhagen", "københavn", "oslo", "helsinki", "vancouver",
    "perth", "melbourne", "sydney", "brisbane", "adelaide", "auckland",
    "rotterdam", "the hague", "utrecht", "eindhoven",
]


def title_matches(title: str, must_match: list[str]) -> bool:
    t = title.lower()
    return any(term.lower() in t for term in must_match)


def location_status(location: str, expected_city: str) -> str:
    """
    Returns "confirmed" (location text names the expected city/area),
    "mismatch" (location text clearly names a different city), or
    "unconfirmed" (no location text available, or it's too generic
    to tell - e.g. just "Germany" or "Remote").
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


def filter_by_title_and_location(
    jobs: list[dict[str, Any]], cv_profile: dict[str, Any], expected_city: str = ""
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """
    Returns (kept_jobs, stats) where stats = {"title_matched": N,
    "location_confirmed": M} — title_matched counts jobs whose title
    passed regardless of location; location_confirmed is the final
    count (title AND location both passed, i.e. len(kept_jobs)).
    Logging both separately is what lets you tell, from a run's
    output, whether a company's postings are being filtered out by
    the title check or the location check — without it, "0 matched"
    is a dead end to debug.
    """
    must_match = cv_profile.get("title_must_match", [])

    kept = []
    title_matched_count = 0
    for job in jobs:
        title = job.get("title", "")
        if not title or not title_matches(title, must_match):
            continue
        title_matched_count += 1

        loc_status = location_status(job.get("location", ""), expected_city)
        if loc_status != "confirmed":
            # Only keep jobs whose location text explicitly names the
            # target city/area — a company's HQ city is frequently not
            # where a given posting actually is.
            continue

        kept.append(job)

    stats = {"title_matched": title_matched_count, "location_confirmed": len(kept)}
    return kept, stats


# --------------------------------------------------------------------------
# Any-city matching — checks a job against EVERY approved city (Munich,
# Zurich, and all 16 dream cities) instead of just the one city a
# company's config entry happens to be tagged with.
#
# Why this exists: a company like Databricks or Palantir is listed
# under ONE config entry (e.g. "Databricks Zurich"), but its actual job
# board spans many locations. Checking that entry's postings only
# against "is this in Zurich?" means a genuine Databricks posting in
# Munich — which absolutely IS one of your approved cities — was being
# silently rejected, because the wrong question was being asked. This
# checks every title-matched posting against the full approved-city
# list and routes it to whichever city actually matches, regardless of
# which config entry (and which nominal city) it came from.
# --------------------------------------------------------------------------

# Three tiers, feeding three separate tracker sheets/sections, in this
# order top to bottom: "Jobs" (Munich, Zurich) -> "Singapore & Swiss
# Cities" (Singapore, Basel, Bern, Geneva, Lausanne, Lucerne) ->
# "Dream Cities" (everything else on the approved list). Kept as two
# separate sets (not one combined "main" set) specifically so main.py
# can route a match to the right one of three sheets, not just two.
MAIN_LIST_CITIES = {"Munich", "Zurich"}
SWISS_SG_CITIES = {"Singapore", "Basel", "Bern", "Geneva", "Lausanne", "Lucerne"}
ALL_APPROVED_CITIES = list(CITY_KEYWORDS.keys())  # Munich, Zurich, Singapore, Swiss cities, + all dream cities


def find_matching_city(location: str, candidate_cities: list[str] | None = None) -> str | None:
    """
    Returns the name of the first approved city whose keywords are
    found in `location`, or None if the location doesn't confirm any
    of them. Checks against ALL_APPROVED_CITIES by default.
    """
    if not location:
        return None
    loc = location.lower()
    for city in candidate_cities or ALL_APPROVED_CITIES:
        if any(kw in loc for kw in CITY_KEYWORDS.get(city, [city.lower()])):
            return city
    return None


def filter_by_title_and_any_city(
    jobs: list[dict[str, Any]], cv_profile: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """
    Like filter_by_title_and_location, but matches against ANY
    approved city rather than one fixed expected_city. Each kept job
    gets a "matched_city" field (and "matched_country" if it's a
    Dream City, via CITY_TO_COUNTRY) added, reflecting where it
    actually was found to be — not wherever its source company entry
    happened to be tagged. main.py uses MAIN_LIST_CITIES /
    SWISS_SG_CITIES membership on matched_city to decide which of the
    three sheets a job goes to.
    """
    must_match = cv_profile.get("title_must_match", [])

    kept = []
    title_matched_count = 0
    for job in jobs:
        title = job.get("title", "")
        if not title or not title_matches(title, must_match):
            continue
        title_matched_count += 1

        matched_city = find_matching_city(job.get("location", ""))
        if matched_city is None:
            continue

        job["matched_city"] = matched_city
        if matched_city not in MAIN_LIST_CITIES and matched_city not in SWISS_SG_CITIES:
            job["matched_country"] = CITY_TO_COUNTRY.get(matched_city, "")
        kept.append(job)

    stats = {"title_matched": title_matched_count, "location_confirmed": len(kept)}
    return kept, stats


# --------------------------------------------------------------------------
# CITY_TO_COUNTRY — used to tag the "Country" column on the Dream
# Cities sheet. Munich/Zurich/Singapore/Basel/Bern/Geneva/Lausanne/
# Lucerne don't need it (they're main-list cities, no Country column
# there), but the mapping doesn't hurt to include them.
# --------------------------------------------------------------------------
CITY_TO_COUNTRY = {
    "Munich": "Germany",
    "Zurich": "Switzerland",
    "Copenhagen": "Denmark",
    "Oslo": "Norway",
    "Helsinki": "Finland",
    "Vienna": "Austria",
    "Basel": "Switzerland",
    "Bern": "Switzerland",
    "Geneva": "Switzerland",
    "Lausanne": "Switzerland",
    "Lucerne": "Switzerland",
    "Amsterdam": "Netherlands",
    "Rotterdam": "Netherlands",
    "Vancouver": "Canada",
    "Perth": "Australia",
    "Melbourne": "Australia",
    "Sydney": "Australia",
    "Singapore": "Singapore",
    "Berlin": "Germany",
}


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
    """Adds relevance_score to every job in place. Does not filter
    anything out — score is for ranking/sorting only."""
    keywords = cv_profile.get("scoring_keywords", [])
    ceiling = cv_profile.get("score_ceiling", DEFAULT_SCORE_CEILING)
    for job in jobs:
        job["relevance_score"] = score_job_1_to_10(
            job.get("description", ""), job.get("title", ""), keywords, ceiling
        )
    return jobs
