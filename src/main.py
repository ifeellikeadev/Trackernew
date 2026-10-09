
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ats_scrapers import scrape_company, fetch_description_fallback, reset_headless_budget
from src.matcher import (
    filter_by_title_only, resolve_city_for_job, extract_location_snippet,
    score_jobs, MAIN_LIST_CITIES,
)
from src.job_recency import filter_recent_jobs
from src.tracker import update_tracker
from src.job_scope import excluded_source
from src.generate_html import generate as generate_html

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("job_scraper.main")

ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = ROOT / "config" / "companies.yaml"
JOB_BOARDS_FILE = ROOT / "config" / "job_boards.yaml"
CV_PROFILE_FILE = ROOT / "config" / "cv_profile.yaml"
TRACKER_FILE = ROOT / "data" / "job_tracker.xlsx"


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def scrape_all_any_city(companies: list[dict], cv_profile: dict) -> tuple[list[dict], dict]:
    main_jobs = []
    ok_count = 0
    empty_count = 0
    error_count = 0
    total_title_matched = 0
    total_confirmed = 0

    companies = [c for c in companies if not excluded_source(c)]
    for company in companies:
        name = company["name"]
        try:
            raw_jobs = scrape_company(company)
            raw_jobs = filter_recent_jobs(raw_jobs)
        except Exception as exc:  # extra safety net at the orchestration level
            logger.error("FAILED  %-35s %s", name, exc)
            error_count += 1
            continue

        if not raw_jobs:
            logger.info("EMPTY   %-35s (no postings found / scraper returned nothing)", name)
            empty_count += 1
            continue

        title_matched_jobs = filter_by_title_only(raw_jobs, cv_profile)

        matched = []
        for job in title_matched_jobs:
            enrichment_text = None
            if not job.get("location") and job.get("url"):
                enrichment_text = fetch_description_fallback(job["url"])

            matched_city = resolve_city_for_job(job, search_text=enrichment_text)
            if matched_city is None:
                continue

            if enrichment_text and not job.get("location"):
                job["location"] = extract_location_snippet(enrichment_text, matched_city)

            if not job.get("description") and enrichment_text:
                job["description"] = enrichment_text
            elif not job.get("description") and job.get("url"):
                job["description"] = fetch_description_fallback(job["url"])

            matched.append(job)

        stats = {"title_matched": len(title_matched_jobs), "location_confirmed": len(matched)}

        if stats["title_matched"] > 0 and stats["location_confirmed"] == 0:
            for j in title_matched_jobs[:5]:
                logger.info(
                    "  DIAG: title=%r  raw_location=%r  (no approved city matched, even after enrichment)",
                    j.get("title", ""), (j.get("location", "") or "")[:200],
                )

        score_jobs(matched, cv_profile)

        for job in matched:
            job["company"] = name
            matched_city = job.pop("matched_city")
            job["city"] = matched_city
            if matched_city in MAIN_LIST_CITIES:
                main_jobs.append(job)

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
    return main_jobs, counts


def run() -> None:
    cv_profile = load_yaml(CV_PROFILE_FILE)
    reset_headless_budget()  # one shared 15-min headless budget for the whole run

    all_companies = (
        load_yaml(COMPANIES_FILE)["companies"]
        + (load_yaml(JOB_BOARDS_FILE)["companies"] if JOB_BOARDS_FILE.exists() else [])
    )
    main_jobs, counts = scrape_all_any_city(all_companies, cv_profile)

    main_summary = update_tracker(TRACKER_FILE, main_jobs, min_score=cv_profile.get("main_min_score", 0))
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
        "Jobs sheet (Munich area): %d new rows added, %d already tracked, %d pruned (age/score; score floor %d), %d total rows",
        main_summary["added"], main_summary["already_tracked"], main_summary["pruned"],
        cv_profile.get("main_min_score", 0), main_summary["total_rows"],
    )
    generate_html()
    logger.info("Saved to %s", TRACKER_FILE)


if __name__ == "__main__":
    run()
