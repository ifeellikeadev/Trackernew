"""
Entry point for the scrape. Run manually with:
    python -m src.main

Or via the GitHub Actions workflow (.github/workflows/daily_scrape.yml,
manually triggered — see SETUP_GUIDE.md).

Runs THREE passes:
  1. Munich/Zurich (config/companies.yaml) -> "Jobs" sheet.
  2. Dream cities (config/dream_cities.yaml) -> "Dream Cities" sheet.
  3. Wildcard (whole approved countries, not specific cities) ->
     "Global Top Picks" sheet — only the top 3-4 postings scoring 9-10,
     rebuilt fresh every run rather than accumulated. Pulls from
     Zurich companies (companies.yaml), the approved-country subset of
     dream_cities.yaml, and config/wildcard_countries.yaml (Sweden,
     UAE, South Korea — the only genuinely new companies needed, since
     the other approved countries already had real companies from
     passes 1-2). Note: this does mean those shared companies get
     scraped twice (once for their city-specific pass, once here) —
     a known tradeoff for keeping the three passes independent and
     simple rather than threading shared state between them.

Neither of the first two passes filters by score — every job that
passes the title + confirmed-location filter is kept and shown. The
wildcard pass is the one exception: score >= 9 only, top 4 max.

Pipeline per company: scrape -> filter by title+location(or country)
-> enrich description for survivors that don't have one yet (fetches
the job's own posting page, see ats_scrapers.fetch_description_fallback)
-> score.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ats_scrapers import scrape_company, fetch_description_fallback
from src.matcher import filter_by_title_and_location, filter_by_title_and_country, score_jobs, title_matches
from src.tracker import update_tracker, update_dream_tracker, update_wildcard_tracker
from src.generate_html import generate as generate_html

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("job_scraper.main")

ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = ROOT / "config" / "companies.yaml"
DREAM_CITIES_FILE = ROOT / "config" / "dream_cities.yaml"
WILDCARD_COUNTRIES_FILE = ROOT / "config" / "wildcard_countries.yaml"
CV_PROFILE_FILE = ROOT / "config" / "cv_profile.yaml"
TRACKER_FILE = ROOT / "data" / "job_tracker.xlsx"

# Countries approved for the wildcard "Global Top Picks" section — see
# chat history / config/wildcard_countries.yaml for reasoning (visa
# feasibility given Italian citizenship + Nepali spouse). Deliberately
# does NOT include the UK (explicitly ruled out) or Japan (explicitly
# ruled out), and doesn't repeat Australia/Singapore here since those
# already have their own Dream Cities coverage.
APPROVED_WILDCARD_COUNTRIES = {
    "Norway", "Sweden", "Denmark", "Netherlands", "Finland",
    "Switzerland", "Austria", "Canada", "UAE", "South Korea",
}


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_wildcard_companies() -> list[dict]:
    """
    Builds the wildcard candidate list from three sources:
      - Zurich-tagged companies in companies.yaml (tagged Switzerland
        here, since that file has no country field of its own)
      - dream_cities.yaml companies already in an approved country
      - config/wildcard_countries.yaml (Sweden, UAE, South Korea —
        the countries with no existing coverage to reuse)
    """
    companies = []

    main_companies = load_yaml(COMPANIES_FILE)["companies"]
    for c in main_companies:
        if c.get("city") == "Zurich":
            c2 = dict(c)
            c2["country"] = "Switzerland"
            companies.append(c2)

    dream_companies = load_yaml(DREAM_CITIES_FILE)["companies"]
    for c in dream_companies:
        if c.get("country") in APPROVED_WILDCARD_COUNTRIES:
            companies.append(c)

    if WILDCARD_COUNTRIES_FILE.exists():
        companies.extend(load_yaml(WILDCARD_COUNTRIES_FILE)["companies"])

    return companies


def scrape_wildcard(companies: list[dict], cv_profile: dict) -> tuple[list[dict], dict]:
    """
    Like scrape_all, but matches against the whole COUNTRY (via
    filter_by_title_and_country) rather than one city. Scores
    everything that matches — filtering to score >= 9 and top 4
    happens later, in tracker.update_wildcard_tracker.
    """
    all_matched = []
    ok_count = 0
    empty_count = 0
    error_count = 0

    for company in companies:
        name = company["name"]
        country = company.get("country", "")
        try:
            raw_jobs = scrape_company(company)
        except Exception as exc:
            logger.error("FAILED  %-35s %s", name, exc)
            error_count += 1
            continue

        if not raw_jobs:
            logger.info("EMPTY   %-35s (no postings found / scraper returned nothing)", name)
            empty_count += 1
            continue

        matched = filter_by_title_and_country(raw_jobs, cv_profile, country=country)

        for job in matched:
            if not job.get("description") and job.get("url"):
                job["description"] = fetch_description_fallback(job["url"])

        score_jobs(matched, cv_profile)

        for job in matched:
            job["company"] = name
            job["city"] = company.get("city", "")
            job["country"] = country
        all_matched.extend(matched)

        logger.info(
            "OK      %-35s %3d postings, %2d matched [Wildcard/%s]",
            name, len(raw_jobs), len(matched), country,
        )
        ok_count += 1
        time.sleep(0.3)

    counts = {"ok": ok_count, "empty": empty_count, "errored": error_count, "total": len(companies)}
    return all_matched, counts


def scrape_all(companies: list[dict], cv_profile: dict, label: str) -> tuple[list[dict], dict]:
    """
    Scrapes every company in `companies`, filters by title+location,
    enriches missing descriptions, scores, and logs progress per
    company. No score-based filtering — everything that matches is
    returned. Returns (all_matched_jobs, counts).
    """
    all_matched = []
    ok_count = 0
    empty_count = 0
    error_count = 0
    total_title_matched = 0
    total_location_confirmed = 0

    for company in companies:
        name = company["name"]
        try:
            raw_jobs = scrape_company(company)
        except Exception as exc:  # extra safety net at the orchestration level
            logger.error("FAILED  %-35s %s", name, exc)
            error_count += 1
            continue

        if not raw_jobs:
            logger.info("EMPTY   %-35s (no postings found / scraper returned nothing)", name)
            empty_count += 1
            continue

        matched, stats = filter_by_title_and_location(raw_jobs, cv_profile, expected_city=company.get("city", ""))

        # If titles matched but none were location-confirmed, log the
        # actual raw location text for the title-matched postings —
        # this is what tells us WHY location matching failed (blank
        # field? different format? genuinely a different city?)
        # instead of just knowing THAT it failed.
        if stats["title_matched"] > 0 and stats["location_confirmed"] == 0:
            title_matched_jobs = [
                j for j in raw_jobs if title_matches(j.get("title", ""), cv_profile.get("title_must_match", []))
            ]
            for j in title_matched_jobs[:5]:  # cap to avoid log spam
                logger.info(
                    "  DIAG: title=%r  raw_location=%r  (expected city: %s)",
                    j.get("title", ""), j.get("location", ""), company.get("city", ""),
                )

        # Enrich description only for genuine candidates (already
        # filtered), not every raw posting — keeps the extra requests
        # proportional to what actually matters.
        for job in matched:
            if not job.get("description") and job.get("url"):
                job["description"] = fetch_description_fallback(job["url"])

        score_jobs(matched, cv_profile)

        for job in matched:
            job["company"] = name
            job["city"] = company.get("city", "")
            if "country" in company:
                job["country"] = company["country"]
        all_matched.extend(matched)

        logger.info(
            "OK      %-35s %3d postings, %2d title-matched, %2d location-confirmed [%s]",
            name,
            len(raw_jobs),
            stats["title_matched"],
            stats["location_confirmed"],
            label,
        )
        total_title_matched += stats["title_matched"]
        total_location_confirmed += stats["location_confirmed"]
        ok_count += 1
        time.sleep(0.3)  # be polite to career-page servers

    counts = {
        "ok": ok_count,
        "empty": empty_count,
        "errored": error_count,
        "total": len(companies),
        "title_matched": total_title_matched,
        "location_confirmed": total_location_confirmed,
    }
    return all_matched, counts


def run() -> None:
    cv_profile = load_yaml(CV_PROFILE_FILE)

    # --- Pass 1: Munich / Zurich ---
    companies = load_yaml(COMPANIES_FILE)["companies"]
    main_jobs, main_counts = scrape_all(companies, cv_profile, label="Munich/Zurich")
    main_summary = update_tracker(TRACKER_FILE, main_jobs)

    logger.info("-" * 60)
    logger.info(
        "Munich/Zurich: %d ok / %d empty / %d errored (of %d total)",
        main_counts["ok"], main_counts["empty"], main_counts["errored"], main_counts["total"],
    )
    logger.info(
        "Munich/Zurich: %d total title-matched, %d total location-confirmed across all companies",
        main_counts["title_matched"], main_counts["location_confirmed"],
    )
    logger.info(
        "Jobs sheet: %d new rows added, %d already tracked, %d total rows",
        main_summary["added"], main_summary["already_tracked"], main_summary["total_rows"],
    )

    # --- Pass 2: Dream cities ---
    dream_companies = load_yaml(DREAM_CITIES_FILE)["companies"]
    dream_jobs, dream_counts = scrape_all(dream_companies, cv_profile, label="Dream Cities")
    dream_summary = update_dream_tracker(TRACKER_FILE, dream_jobs)

    logger.info("-" * 60)
    logger.info(
        "Dream cities: %d ok / %d empty / %d errored (of %d total)",
        dream_counts["ok"], dream_counts["empty"], dream_counts["errored"], dream_counts["total"],
    )
    logger.info(
        "Dream cities: %d total title-matched, %d total location-confirmed across all companies",
        dream_counts["title_matched"], dream_counts["location_confirmed"],
    )
    logger.info(
        "Dream Cities sheet: %d new rows added, %d already tracked, %d total rows",
        dream_summary["added"], dream_summary["already_tracked"], dream_summary["total_rows"],
    )

    # --- Pass 3: Wildcard (whole approved countries, top scorers only) ---
    wildcard_companies = load_wildcard_companies()
    wildcard_jobs, wildcard_counts = scrape_wildcard(wildcard_companies, cv_profile)
    wildcard_summary = update_wildcard_tracker(TRACKER_FILE, wildcard_jobs)

    logger.info("-" * 60)
    logger.info(
        "Wildcard: %d ok / %d empty / %d errored (of %d total)",
        wildcard_counts["ok"], wildcard_counts["empty"], wildcard_counts["errored"], wildcard_counts["total"],
    )
    logger.info(
        "Global Top Picks sheet: %d qualified (score>=9), showing top %d",
        wildcard_summary["total_qualifying"], wildcard_summary["shown"],
    )

    generate_html()
    logger.info("Saved to %s", TRACKER_FILE)


if __name__ == "__main__":
    run()
