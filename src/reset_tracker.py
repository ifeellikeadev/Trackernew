"""Consolidated reset_tracker implementation. See INSTALL.txt for migration details."""
from __future__ import annotations

# ========================================================================
# reset_tracker: consolidated implementation
# ========================================================================



import logging
from pathlib import Path

from src.tracker import archive_and_reset
from src.website import generate as generate_html

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
