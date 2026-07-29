"""
Manages the persistent Excel tracker file.

Behaviour, matching what Giorgio described:
  - Each run, only genuinely NEW postings (by URL) are appended.
  - Postings already in the sheet are left alone, but their
    "Last Seen" date is refreshed so you can tell what's still open.
  - Rows found in a previous run but NOT in today's scrape are left
    in place too (a company's page can be flaky) — "Last Seen" simply
    stops updating for them, so stale rows are visible, not deleted.
  - A separate script (reset_tracker.py) archives the file and starts
    a fresh one; that's the "clears once a month" behaviour, run on
    its own schedule so it stays independent of the daily scrape.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

COLUMNS = [
    "First Seen",
    "Last Seen",
    "Company",
    "City",
    "Job Title",
    "Relevance Score",
    "German Required",
    "Location",
    "URL",
]

HEADER_FILL = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True)
NEW_ROW_FILL = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid")


def _new_workbook() -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Jobs"
    ws.append(COLUMNS)
    for col_idx in range(1, len(COLUMNS) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    ws.freeze_panes = "A2"
    widths = [12, 12, 22, 9, 40, 8, 8, 22, 60]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    return wb


def load_or_create(path: Path) -> Workbook:
    if path.exists():
        return load_workbook(path)
    return _new_workbook()


def _existing_urls(ws: Worksheet) -> dict[str, int]:
    """Map URL -> row number for existing entries."""
    url_col = COLUMNS.index("URL") + 1
    out = {}
    for row in range(2, ws.max_row + 1):
        val = ws.cell(row=row, column=url_col).value
        if val:
            out[val] = row
    return out


def update_tracker(path: Path, new_jobs: list[dict[str, Any]]) -> dict[str, int]:
    """
    new_jobs: list of dicts with keys matching filter_and_score() output
              plus "company" and "city" added by the caller.
    Returns a small summary dict for logging (added / refreshed counts).
    """
    wb = load_or_create(path)
    ws = wb["Jobs"] if "Jobs" in wb.sheetnames else wb.active
    today = dt.date.today().isoformat()

    existing = _existing_urls(ws)
    last_seen_col = COLUMNS.index("Last Seen") + 1

    added = 0
    refreshed = 0
    for job in new_jobs:
        url = job.get("url", "")
        if not url:
            continue
        if url in existing:
            row = existing[url]
            ws.cell(row=row, column=last_seen_col).value = today
            refreshed += 1
            continue
        row_values = [
            today,  # First Seen
            today,  # Last Seen
            job.get("company", ""),
            job.get("city", ""),
            job.get("title", ""),
            job.get("relevance_score", 0),
            "Yes" if job.get("german_required") else "",
            job.get("location", ""),
            url,
        ]
        ws.append(row_values)
        new_row_idx = ws.max_row
        for col_idx in range(1, len(COLUMNS) + 1):
            ws.cell(row=new_row_idx, column=col_idx).fill = NEW_ROW_FILL
        added += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return {"added": added, "refreshed": refreshed, "total_rows": ws.max_row - 1}


def archive_and_reset(path: Path, archive_dir: Path) -> Path | None:
    """
    Moves the current tracker to archive/job_tracker_YYYY-MM.xlsx and
    creates a fresh empty tracker at `path`. Returns the archive path,
    or None if there was nothing to archive.
    """
    if not path.exists():
        _new_workbook().save(path)
        return None

    archive_dir.mkdir(parents=True, exist_ok=True)
    month_tag = dt.date.today().strftime("%Y-%m")
    archive_path = archive_dir / f"job_tracker_{month_tag}.xlsx"

    # If we've already archived this month (e.g. workflow ran twice),
    # don't overwrite — append a counter instead.
    counter = 2
    final_path = archive_path
    while final_path.exists():
        final_path = archive_dir / f"job_tracker_{month_tag}_v{counter}.xlsx"
        counter += 1

    path.rename(final_path)
    _new_workbook().save(path)
    return final_path
