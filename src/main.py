"""
Entry point for the scrape. Run manually with:
    python -m src.main

Or via the GitHub Actions workflow (.github/workflows/daily_scrape.yml,
runs daily at 06:30 Munich time, plus manual "Run workflow" trigger).

Runs ONE combined pass: every company in config/companies.yaml AND
config/job_boards.yaml (each scraped once), matched against ANY
approved city — not just whichever single city that company's config
entry happens to be tagged with. This matters: a company like
Databricks is listed once, tagged "Zurich," but its job board spans
many locations — a genuine Databricks posting in Munich is just as
real a match as one in Zurich, and would otherwise get silently
rejected because only the Zurich question was being asked.

Matches route to one of TWO sheets, based on which city actually
matched (see src.matcher.MAIN_LIST_CITIES / SWISS_CITIES and
filter_by_title_and_any_city):
  1. "Jobs" — Munich, Zurich.
  2. "Swiss Cities" — Basel, Bern, Geneva, Lausanne, Lucerne. Kept as
     its own sheet rather than merged into "Jobs" so these cities'
     (genuinely fewer) matches aren't buried under Munich/Zurich's
     much higher posting volume.

Scope refocused per request to Munich + Swiss cities/nearby areas
ONLY — config/dream_cities.yaml is no longer loaded at all (Singapore
and every Dream City dropped). The file itself is left in place,
unused, in case this gets reversed later rather than deleted outright.

No score floor — every job that passes the title + confirmed-city
filter is kept and shown.

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
from src.matcher import (
    filter_by_title_only, resolve_city_for_job, extract_location_snippet,
    score_jobs, MAIN_LIST_CITIES, SWISS_CITIES,
)
from src.tracker import update_tracker, update_swiss_tracker
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


def scrape_all_any_city(companies: list[dict], cv_profile: dict) -> tuple[list[dict], list[dict], dict]:
    """
    Scrapes every company once, matches each title-matched posting
    against ANY approved city (not just that company's configured
    one), and splits results into (main_jobs, swiss_jobs) based on
    where each posting actually matched. Returns (main_jobs,
    swiss_jobs, counts).

    IMPORTANT ordering fix: scrape_generic (ats_scrapers.py) always
    leaves location blank — plain HTML link scraping has no reliable
    way to find it near an arbitrary job link. Previously, the location
    filter ran BEFORE any individual-page fetch, so every generic-
    scraped posting was rejected before it ever had a chance to have
    its real location discovered (fetch_description_fallback was only
    called on postings that had ALREADY survived that filter — meaning
    never, for these). This silently discarded real, relevant postings
    from any company whose final source was the generic scraper — not
    a small edge case; this is likely why entire sources (e.g. the
    Randstad/Michael Page/etc. job-board entries, whose listings put
    location in a separate line from the title link) showed nothing at
    all rather than just a partial miss.

    Fixed by enriching BEFORE deciding: for title-matched postings with
    a blank location, fetch the individual posting page FIRST, and use
    that page's text as the location search space — then apply the
    location decision. That same fetched text is reused as the
    description too, so no posting gets fetched twice.
    """
    main_jobs = []
    swiss_jobs = []
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

        title_matched_jobs = filter_by_title_only(raw_jobs, cv_profile)

        matched = []
        for job in title_matched_jobs:
            enrichment_text = None
            if not job.get("location") and job.get("url"):
                # Fetch BEFORE the location decision, not after — this is
                # the actual fix. Reused as description below too.
                enrichment_text = fetch_description_fallback(job["url"])

            matched_city = resolve_city_for_job(job, search_text=enrichment_text)
            if matched_city is None:
                continue

            if enrichment_text and not job.get("location"):
                # Don't dump the whole fetched page into the Location
                # column — pull a short readable window around the
                # actual matched keyword instead.
                job["location"] = extract_location_snippet(enrichment_text, matched_city)

            if not job.get("description") and enrichment_text:
                job["description"] = enrichment_text
            elif not job.get("description") and job.get("url"):
                job["description"] = fetch_description_fallback(job["url"])

            matched.append(job)

        stats = {"title_matched": len(title_matched_jobs), "location_confirmed": len(matched)}

        # If titles matched but none confirmed against any approved
        # city, log the actual raw location text for a few of them —
        # tells us WHY (blank field? different format? genuinely
        # nowhere on the approved list?) instead of just knowing THAT.
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
            else:
                swiss_jobs.append(job)

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
    return main_jobs, swiss_jobs, counts


def run() -> None:
    cv_profile = load_yaml(CV_PROFILE_FILE)
    reset_headless_budget()  # one shared 15-min headless budget for the whole run

    all_companies = (
        load_yaml(COMPANIES_FILE)["companies"]
        + (load_yaml(JOB_BOARDS_FILE)["companies"] if JOB_BOARDS_FILE.exists() else [])
    )
    main_jobs, swiss_jobs, counts = scrape_all_any_city(all_companies, cv_profile)

    main_summary = update_tracker(TRACKER_FILE, main_jobs, min_score=cv_profile.get("main_min_score", 0))
    swiss_summary = update_swiss_tracker(
        TRACKER_FILE, swiss_jobs, min_score=cv_profile.get("swiss_min_score", 0)
    )

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
        "Jobs sheet (Munich/Zurich): %d new rows added, %d already tracked, %d pruned (below score %d), %d total rows",
        main_summary["added"], main_summary["already_tracked"], main_summary["pruned"],
        cv_profile.get("main_min_score", 0), main_summary["total_rows"],
    )
    logger.info(
        "Swiss Cities sheet: %d new rows added, %d already tracked, %d pruned (below score %d), %d total rows",
        swiss_summary["added"], swiss_summary["already_tracked"], swiss_summary["pruned"],
        cv_profile.get("swiss_min_score", 0), swiss_summary["total_rows"],
    )

    generate_html()
    logger.info("Saved to %s", TRACKER_FILE)


if __name__ == "__main__":
    run()
