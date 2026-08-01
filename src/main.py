"""
Entry point for the scrape. Run manually with:
    python -m src.main

Or via the GitHub Actions workflow (.github/workflows/daily_scrape.yml,
manually triggered — see SETUP_GUIDE.md).

Runs TWO passes:
  1. Munich/Zurich (config/companies.yaml) -> "Jobs" sheet.
  2. Dream cities (config/dream_cities.yaml) -> "Dream Cities" sheet.

Neither pass filters by score anymore — every job that passes the
title + confirmed-location filter is kept and shown, regardless of
how strong its keyword match is. Score is for sorting only (both
sheets are still sorted highest-score-first).

Pipeline per company: scrape -> filter by title+location -> enrich
description for survivors that don't have one yet (fetches the job's
own posting page, see ats_scrapers.fetch_description_fallback) -> score.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ats_scrapers import scrape_company, fetch_description_fallback
from src.matcher import filter_by_title_and_location, score_jobs
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

    generate_html()
    logger.info("Saved to %s", TRACKER_FILE)


if __name__ == "__main__":
    run()
