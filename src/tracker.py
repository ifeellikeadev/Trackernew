"""
Manages the persistent Excel tracker file.

Behaviour, matching what Giorgio described:
  - Each run, only genuinely NEW postings (by URL) are added.
  - Postings already in the sheet are left alone (deduped by URL).
  - The whole sheet is re-sorted by Relevance Score (highest first)
    every run, so the best matches are always at the top.
  - A separate script (reset_tracker.py) archives the file and starts
    a fresh one; that's the "clears once a month" behaviour, run on
    its own schedule so it stays independent of the daily scrape. It
    can also be triggered manually any time — see SETUP_GUIDE.md,
    "How to reset the tracker manually."

Column history (kept here so future-you understands old files):
  v1: First Seen, Last Seen, Company, City, Job Title, Relevance Score,
      German Required, Location, URL
  v2: added Job Posted; renamed score column to "Relevance Score (1-10)"
  v3: added Location Confirmed
  v4 (current): dropped First Seen and Last Seen (no longer shown —
      dedupe still happens internally by URL, it's just not displayed);
      sheet is sorted by relevance instead of being append-only.
Migration below reads whatever columns an existing file has by NAME,
not position, and rebuilds the sheet against the current COLUMNS list —
so it doesn't matter which older version your file is on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

COLUMNS = [
    "Job Posted",
    "Company",
    "City",
    "Job Title",
    "Relevance Score (1-10)",
    "German Required",
    "Location",
    "Location Confirmed",
    "URL",
]
COLUMN_WIDTHS = [14, 22, 9, 40, 12, 8, 22, 16, 60]

# Old header names that should map onto a current column, so a rename
# (like "Relevance Score" -> "Relevance Score (1-10)") doesn't strand
# existing data. Names not listed here and not in COLUMNS are dropped
# (that's how First Seen / Last Seen disappear on migration).
HEADER_ALIASES = {
    "Relevance Score": "Relevance Score (1-10)",
}

HEADER_FILL = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True)
NEW_ROW_FILL = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid")


def _style_header(ws: Worksheet) -> None:
    for col_idx in range(1, len(COLUMNS) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    ws.freeze_panes = "A2"
    for i, w in enumerate(COLUMN_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _new_workbook() -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Jobs"
    ws.append(COLUMNS)
    _style_header(ws)
    return wb


def load_or_create(path: Path) -> Workbook:
    if not path.exists():
        return _new_workbook()

    wb = load_workbook(path)
    ws = wb["Jobs"] if "Jobs" in wb.sheetnames else wb.active
    existing_headers = [c.value for c in ws[1]]
    if existing_headers == COLUMNS:
        return wb

    # Migration: read every row into a dict keyed by (aliased) header
    # name, then rebuild the sheet from scratch against current COLUMNS.
    # Anything not in COLUMNS (First Seen, Last Seen, ...) is dropped;
    # anything in COLUMNS but missing from the old file is left blank.
    mapped_headers = [HEADER_ALIASES.get(h, h) for h in existing_headers]
    rows = []
    for row_idx in range(2, ws.max_row + 1):
        row_dict = {}
        for col_idx, header in enumerate(mapped_headers, start=1):
            if header in COLUMNS:
                row_dict[header] = ws.cell(row=row_idx, column=col_idx).value
        if row_dict:
            rows.append(row_dict)

    new_wb = Workbook()
    new_ws = new_wb.active
    new_ws.title = "Jobs"
    new_ws.append(COLUMNS)
    for row_dict in rows:
        new_ws.append([row_dict.get(col, "") for col in COLUMNS])
    _style_header(new_ws)
    return new_wb


def _existing_urls(ws: Worksheet) -> set[str]:
    url_col = COLUMNS.index("URL") + 1
    return {
        ws.cell(row=row, column=url_col).value
        for row in range(2, ws.max_row + 1)
        if ws.cell(row=row, column=url_col).value
    }


def _sort_by_relevance(ws: Worksheet, newly_added_urls: set[str]) -> None:
    """Re-sorts all data rows by Relevance Score (highest first), then
    re-applies the "new" highlight to whichever URLs were added this
    run (their row position may have moved during the sort)."""
    score_col = COLUMNS.index("Relevance Score (1-10)")
    url_col = COLUMNS.index("URL")

    rows = []
    for row in range(2, ws.max_row + 1):
        values = [ws.cell(row=row, column=c).value for c in range(1, len(COLUMNS) + 1)]
        rows.append(values)

    rows.sort(key=lambda v: (v[score_col] if isinstance(v[score_col], (int, float)) else 0), reverse=True)

    # Clear existing data rows, then rewrite in sorted order.
    for row in range(2, ws.max_row + 1):
        for col in range(1, len(COLUMNS) + 1):
            ws.cell(row=row, column=col).value = None
            ws.cell(row=row, column=col).fill = PatternFill(fill_type=None)

    for i, values in enumerate(rows):
        row_idx = i + 2
        for col_idx, value in enumerate(values, start=1):
            ws.cell(row=row_idx, column=col_idx).value = value
        if values[url_col] in newly_added_urls:
            for col_idx in range(1, len(COLUMNS) + 1):
                ws.cell(row=row_idx, column=col_idx).fill = NEW_ROW_FILL


def update_tracker(path: Path, new_jobs: list[dict[str, Any]]) -> dict[str, int]:
    """
    new_jobs: list of dicts with keys matching filter_and_score() output
              plus "company" and "city" added by the caller.
    Returns a small summary dict for logging (added / already-tracked
    counts). The sheet ends up sorted by relevance score, highest first.
    """
    wb = load_or_create(path)
    ws = wb["Jobs"] if "Jobs" in wb.sheetnames else wb.active

    existing_urls = _existing_urls(ws)

    added = 0
    already_tracked = 0
    newly_added_urls = set()
    for job in new_jobs:
        url = job.get("url", "")
        if not url:
            continue
        if url in existing_urls:
            already_tracked += 1
            continue
        row_values = [
            job.get("posted_date", ""),
            job.get("company", ""),
            job.get("city", ""),
            job.get("title", ""),
            job.get("relevance_score", 1),
            "Yes" if job.get("german_required") else "",
            job.get("location", ""),
            "Yes" if job.get("location_status") == "confirmed" else "Unconfirmed",
            url,
        ]
        ws.append(row_values)
        newly_added_urls.add(url)
        existing_urls.add(url)
        added += 1

    _sort_by_relevance(ws, newly_added_urls)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return {"added": added, "already_tracked": already_tracked, "total_rows": ws.max_row - 1}


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
    import datetime as dt

    month_tag = dt.date.today().strftime("%Y-%m")
    archive_path = archive_dir / f"job_tracker_{month_tag}.xlsx"

    counter = 2
    final_path = archive_path
    while final_path.exists():
        final_path = archive_dir / f"job_tracker_{month_tag}_v{counter}.xlsx"
        counter += 1

    path.rename(final_path)
    _new_workbook().save(path)
    return final_path
