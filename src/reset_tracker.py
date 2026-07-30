"""
Archives the current tracker and starts a fresh one.
Run manually with:  python -m src.reset_tracker
Or automatically via .github/workflows/monthly_reset.yml on the 1st
of each month.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.tracker import archive_and_reset
from src.generate_html import generate as generate_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("job_scraper.reset")

ROOT = Path(__file__).resolve().parent.parent
TRACKER_FILE = ROOT / "data" / "job_tracker.xlsx"
ARCHIVE_DIR = ROOT / "archive"


def run() -> None:
    archived = archive_and_reset(TRACKER_FILE, ARCHIVE_DIR)
    if archived:
        logger.info("Archived previous tracker to %s", archived)
    else:
        logger.info("No existing tracker to archive; created a fresh one at %s", TRACKER_FILE)
    generate_html()


if __name__ == "__main__":
    run()
