"""
Entry point for the daily scrape. Run manually with:
    python -m src.main

Or via the GitHub Actions workflow (.github/workflows/daily_scrape.yml),
which runs this automatically on a schedule and commits the updated
data/job_tracker.xlsx back to the repo.

Runs TWO passes:
  1. Munich/Zurich (config/companies.yaml) -> "Jobs" sheet, any score.
  2. Dream cities (config/dream_cities.yaml) -> "Dream Cities" sheet,
     only postings scoring >= cv_profile.yaml's dream_city_min_score.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ats_scrapers import scrape_company
from src.matcher import filter_and_score
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

DEFAULT_DREAM_MIN_SCORE = 7


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def scrape_all(companies: list[dict], cv_profile: dict, min_score: int, label: str) -> tuple[list[dict], dict]:
    """
    Scrapes every company in `companies`, filters/scores each result,
    optionally drops anything below `min_score` (pass 0 for no floor),
    and logs progress per company. Returns (all_relevant_jobs, counts).
    """
    all_relevant = []
    ok_count = 0
    empty_count = 0
    error_count = 0

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

        relevant = filter_and_score(raw_jobs, cv_profile, expected_city=company.get("city", ""))
        if min_score:
            relevant = [j for j in relevant if j.get("relevance_score", 0) >= min_score]
        for job in relevant:
            job["company"] = name
            job["city"] = company.get("city", "")
            if "country" in company:
                job["country"] = company["country"]
        all_relevant.extend(relevant)

        logger.info(
            "OK      %-35s %3d postings found, %2d relevant [%s]",
            name,
            len(raw_jobs),
            len(relevant),
            label,
        )
        ok_count += 1
        time.sleep(0.3)  # be polite to career-page servers

    counts = {"ok": ok_count, "empty": empty_count, "errored": error_count, "total": len(companies)}
    return all_relevant, counts


def run() -> None:
    cv_profile = load_yaml(CV_PROFILE_FILE)

    # --- Pass 1: Munich / Zurich (main list, any score) ---
    companies = load_yaml(COMPANIES_FILE)["companies"]
    main_jobs, main_counts = scrape_all(companies, cv_profile, min_score=0, label="Munich/Zurich")
    main_summary = update_tracker(TRACKER_FILE, main_jobs)

    logger.info("-" * 60)
    logger.info(
        "Munich/Zurich: %d ok / %d empty / %d errored (of %d total)",
        main_counts["ok"], main_counts["empty"], main_counts["errored"], main_counts["total"],
    )
    logger.info(
        "Jobs sheet: %d new rows added, %d already tracked, %d total rows",
        main_summary["added"], main_summary["already_tracked"], main_summary["total_rows"],
    )

    # --- Pass 2: Dream cities (separate sheet, score threshold applied) ---
    dream_min_score = cv_profile.get("dream_city_min_score", DEFAULT_DREAM_MIN_SCORE)
    dream_companies = load_yaml(DREAM_CITIES_FILE)["companies"]
    dream_jobs, dream_counts = scrape_all(
        dream_companies, cv_profile, min_score=dream_min_score, label=f"Dream Cities, score>={dream_min_score}"
    )
    dream_summary = update_dream_tracker(TRACKER_FILE, dream_jobs)

    logger.info("-" * 60)
    logger.info(
        "Dream cities: %d ok / %d empty / %d errored (of %d total)",
        dream_counts["ok"], dream_counts["empty"], dream_counts["errored"], dream_counts["total"],
    )
    logger.info(
        "Dream Cities sheet: %d new rows added, %d already tracked, %d total rows",
        dream_summary["added"], dream_summary["already_tracked"], dream_summary["total_rows"],
    )

    generate_html()
    logger.info("Saved to %s", TRACKER_FILE)


if __name__ == "__main__":
    run()
