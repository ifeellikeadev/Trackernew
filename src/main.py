"""
Entry point for the daily scrape. Run manually with:
    python -m src.main

Or via the GitHub Actions workflow (.github/workflows/daily_scrape.yml),
which runs this automatically on a schedule and commits the updated
data/job_tracker.xlsx back to the repo.
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
from src.tracker import update_tracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("job_scraper.main")

ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = ROOT / "config" / "companies.yaml"
CV_PROFILE_FILE = ROOT / "config" / "cv_profile.yaml"
TRACKER_FILE = ROOT / "data" / "job_tracker.xlsx"


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run() -> None:
    companies = load_yaml(COMPANIES_FILE)["companies"]
    cv_profile = load_yaml(CV_PROFILE_FILE)

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

        relevant = filter_and_score(raw_jobs, cv_profile)
        for job in relevant:
            job["company"] = name
            job["city"] = company.get("city", "")
        all_relevant.extend(relevant)

        logger.info(
            "OK      %-35s %3d postings found, %2d PM-relevant",
            name,
            len(raw_jobs),
            len(relevant),
        )
        ok_count += 1
        time.sleep(0.3)  # be polite to career-page servers

    summary = update_tracker(TRACKER_FILE, all_relevant)

    logger.info("-" * 60)
    logger.info(
        "Companies: %d ok / %d empty / %d errored (of %d total)",
        ok_count,
        empty_count,
        error_count,
        len(companies),
    )
    logger.info(
        "Tracker: %d new rows added, %d existing rows refreshed, %d total rows",
        summary["added"],
        summary["refreshed"],
        summary["total_rows"],
    )
    logger.info("Saved to %s", TRACKER_FILE)


if __name__ == "__main__":
    run()
