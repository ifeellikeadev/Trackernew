"""
Entry point for the scrape. Run manually with:
    python -m src.main

Or via the GitHub Actions workflow (.github/workflows/daily_scrape.yml,
manually triggered — see SETUP_GUIDE.md).

Runs ONE combined pass: every company in config/companies.yaml AND
config/dream_cities.yaml (each scraped once), matched against ANY
approved city (Munich, Zurich, Singapore, Basel, Bern, Geneva,
Lausanne, Lucerne, or any of the Dream Cities) — not just whichever
single city that company's config entry happens to be tagged with.
This matters: a company like Databricks is listed once, tagged
"Zurich," but its job board spans many locations — a genuine
Databricks posting in Munich or Singapore is just as real a match as
one in Zurich, and previously got silently rejected because only the
Zurich question was being asked.

Matches route to the "Jobs" sheet (Munich, Zurich, Singapore, Basel,
Bern, Geneva, Lausanne, Lucerne — see matcher.MAIN_LIST_CITIES) or the
"Dream Cities" sheet (everything else on the approved list), based on
which city actually matched — see src.matcher.filter_by_title_and_any_city.

No score floor — every job that passes the title + confirmed-city
filter is kept and shown.

(There used to be a second "wildcard" pass here — whole-country
matching feeding a "Global Top Picks" sheet. Removed per request:
Singapore and the Swiss cities it partly existed to surface are now
proper main-list cities instead, which covers the same need more
directly.)

Pipeline per company: scrape -> filter by title + any approved city
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

from src.ats_scrapers import scrape_company, fetch_description_fallback, reset_headless_budget
from src.matcher import filter_by_title_and_any_city, score_jobs, title_matches, MAIN_LIST_CITIES
from src.tracker import update_tracker, update_dream_tracker
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
CV_PROFILE_FILE = ROOT / "config" / "cv_profile.yaml"
TRACKER_FILE = ROOT / "data" / "job_tracker.xlsx"


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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
    reset_headless_budget()  # one shared 15-min headless budget for the whole run

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

    generate_html()
    logger.info("Saved to %s", TRACKER_FILE)


if __name__ == "__main__":
    run()
