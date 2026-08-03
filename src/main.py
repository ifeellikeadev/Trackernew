"""
Entry point for the scrape. Run manually with:
    python -m src.main

Or via the GitHub Actions workflow (.github/workflows/daily_scrape.yml,
manually triggered — see SETUP_GUIDE.md).

Runs TWO passes:
  1. Combined scrape of every company in config/companies.yaml AND
     config/dream_cities.yaml (each scraped once), matched against
     ANY approved city (Munich, Zurich, or any of the 16 dream
     cities) — not just whichever single city that company's config
     entry happens to be tagged with. This matters: a company like
     Databricks is listed once, tagged "Zurich," but its job board
     spans many locations — a genuine Databricks posting in Munich is
     just as real a match as one in Zurich, and previously got
     silently rejected because only the Zurich question was being
     asked. Matches route to the "Jobs" sheet (Munich/Zurich) or the
     "Dream Cities" sheet based on which city actually matched, using
     src.matcher.filter_by_title_and_any_city.
  2. Wildcard (whole approved countries, not specific cities) ->
     "Global Top Picks" sheet — only the top 3-4 postings scoring
     9-10, rebuilt fresh every run rather than accumulated. Pulls
     from Zurich companies (companies.yaml), the approved-country
     subset of dream_cities.yaml, and config/wildcard_countries.yaml
     (Sweden, UAE, South Korea). Note: this does mean those shared
     companies get scraped twice (once in pass 1, once here) — a
     known tradeoff for keeping the wildcard pass's whole-country
     matching independent and simple rather than threading shared
     state between fundamentally different matching rules.

No score floor on pass 1 — every job that passes the title +
confirmed-city filter is kept and shown. The wildcard pass is the one
exception: score >= 9 only, top 4 max.

Pipeline per company: scrape -> filter by title + any approved city
(or country, for the wildcard pass) -> enrich description for
survivors that don't have one yet (fetches the job's own posting
page, see ats_scrapers.fetch_description_fallback) -> score.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ats_scrapers import scrape_company, fetch_description_fallback, reset_headless_budget
from src.matcher import filter_by_title_and_any_city, filter_by_title_and_country, score_jobs, title_matches, MAIN_LIST_CITIES
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
    Matches against the whole COUNTRY (via filter_by_title_and_country)
    rather than one city or the any-city set — this is deliberately a
    different, broader question ("anywhere in Sweden?") than pass 1
    asks ("Munich, Zurich, or one of the 16 dream cities?"). Scores
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


def scrape_all_any_city(companies: list[dict], cv_profile: dict) -> tuple[list[dict], list[dict], dict]:
    """
    Scrapes every company once, matches each title-matched posting
    against ANY approved city (not just that company's configured
    one), and splits results into (main_jobs, dream_jobs) based on
    where each posting actually matched. Returns (main_jobs,
    dream_jobs, counts).
    """
    main_jobs = []
    dream_jobs = []
    ok_count = 0
    empty_count = 0
    error_count = 0
    total_title_matched = 0
    total_confirmed = 0

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

        matched, stats = filter_by_title_and_any_city(raw_jobs, cv_profile)

        # If titles matched but none confirmed against any approved
        # city, log the actual raw location text for a few of them —
        # tells us WHY (blank field? different format? genuinely
        # nowhere on the approved list?) instead of just knowing THAT.
        if stats["title_matched"] > 0 and stats["location_confirmed"] == 0:
            title_matched_jobs = [
                j for j in raw_jobs if title_matches(j.get("title", ""), cv_profile.get("title_must_match", []))
            ]
            for j in title_matched_jobs[:5]:
                logger.info(
                    "  DIAG: title=%r  raw_location=%r  (no approved city matched)",
                    j.get("title", ""), j.get("location", ""),
                )

        for job in matched:
            if not job.get("description") and job.get("url"):
                job["description"] = fetch_description_fallback(job["url"])

        score_jobs(matched, cv_profile)

        for job in matched:
            job["company"] = name
            matched_city = job.pop("matched_city")
            job["city"] = matched_city
            if matched_city in MAIN_LIST_CITIES:
                main_jobs.append(job)
            else:
                job["country"] = job.pop("matched_country", "")
                dream_jobs.append(job)

        logger.info(
            "OK      %-35s %3d postings, %2d title-matched, %2d confirmed",
            name, len(raw_jobs), stats["title_matched"], stats["location_confirmed"],
        )
        total_title_matched += stats["title_matched"]
        total_confirmed += stats["location_confirmed"]
        ok_count += 1
        time.sleep(0.3)  # be polite to career-page servers

    counts = {
        "ok": ok_count,
        "empty": empty_count,
        "errored": error_count,
        "total": len(companies),
        "title_matched": total_title_matched,
        "location_confirmed": total_confirmed,
    }
    return main_jobs, dream_jobs, counts


def run() -> None:
    cv_profile = load_yaml(CV_PROFILE_FILE)
    reset_headless_budget()  # one shared 15-min headless budget for the ENTIRE run, both passes

    # --- Pass 1: combined scrape, matched against ANY approved city ---
    all_companies = load_yaml(COMPANIES_FILE)["companies"] + load_yaml(DREAM_CITIES_FILE)["companies"]
    main_jobs, dream_jobs, counts = scrape_all_any_city(all_companies, cv_profile)

    main_summary = update_tracker(TRACKER_FILE, main_jobs)
    dream_summary = update_dream_tracker(TRACKER_FILE, dream_jobs)

    logger.info("-" * 60)
    logger.info(
        "Combined scrape: %d ok / %d empty / %d errored (of %d total)",
        counts["ok"], counts["empty"], counts["errored"], counts["total"],
    )
    logger.info(
        "%d total title-matched, %d total confirmed against any approved city",
        counts["title_matched"], counts["location_confirmed"],
    )
    logger.info(
        "Jobs sheet: %d new rows added, %d already tracked, %d total rows",
        main_summary["added"], main_summary["already_tracked"], main_summary["total_rows"],
    )
    logger.info(
        "Dream Cities sheet: %d new rows added, %d already tracked, %d total rows",
        dream_summary["added"], dream_summary["already_tracked"], dream_summary["total_rows"],
    )

    # --- Pass 2: Wildcard (whole approved countries, top scorers only) ---
    wildcard_companies = load_wildcard_companies()
    wildcard_jobs, wildcard_counts = scrape_wildcard(wildcard_companies, cv_profile)
    wildcard_summary = update_wildcard_tracker(TRACKER_FILE, wildcard_jobs)

    logger.info("-" * 60)
    logger.info(
        "Wildcard: %d ok / %d empty / %d errored (of %d total)",
        wildcard_counts["ok"], wildcard_counts["empty"], wildcard_counts["errored"], wildcard_counts["total"],
    )
    logger.info(
        "Global Top Picks sheet: %d qualified (score>=8), %d excluded as repeats from last run, showing top %d",
        wildcard_summary["total_qualifying"], wildcard_summary["excluded_as_repeat"], wildcard_summary["shown"],
    )

    generate_html()
    logger.info("Saved to %s", TRACKER_FILE)


if __name__ == "__main__":
    run()
