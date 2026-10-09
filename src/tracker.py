
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Callable

from src.job_scope import allowed_job
from src.job_recency import filter_recent_jobs, prune_old_rows
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
    "Location",
    "URL",
]
COLUMN_WIDTHS = [14, 22, 9, 40, 12, 30, 60]


HEADER_ALIASES = {
    "Relevance Score": "Relevance Score (1-10)",
}

HEADER_FILL = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True)
NEW_ROW_FILL = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid")  # green


def _style_header(ws: Worksheet, columns: list[str], widths: list[int]) -> None:
    for col_idx in range(1, len(columns) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    ws.freeze_panes = "A2"
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _new_workbook() -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Jobs"
    ws.append(COLUMNS)
    _style_header(ws, COLUMNS, COLUMN_WIDTHS)

    return wb


def _migrate_sheet(wb: Workbook, sheet_name: str, columns: list[str], widths: list[int], sheet_index: int) -> None:
    ws = wb[sheet_name]
    existing_headers = [c.value for c in ws[1]]
    if existing_headers == columns:
        return  # already current

    mapped_headers = [HEADER_ALIASES.get(h, h) for h in existing_headers]
    location_confirmed_idx = None
    if "Location Confirmed" in existing_headers:
        location_confirmed_idx = existing_headers.index("Location Confirmed")

    rows = []
    for row_idx in range(2, ws.max_row + 1):
        if location_confirmed_idx is not None:
            confirmed_val = ws.cell(row=row_idx, column=location_confirmed_idx + 1).value
            if confirmed_val != "Yes":
                continue  # drop rows that weren't a confirmed-location match
        row_dict = {}
        for col_idx, header in enumerate(mapped_headers, start=1):
            if header in columns:
                row_dict[header] = ws.cell(row=row_idx, column=col_idx).value
        if row_dict:
            rows.append(row_dict)

    wb.remove(ws)
    new_ws = wb.create_sheet(sheet_name, sheet_index)
    new_ws.append(columns)
    for row_dict in rows:
        new_ws.append([row_dict.get(col, "") for col in columns])
    _style_header(new_ws, columns, widths)


def _ensure_sheet(wb: Workbook, sheet_name: str, columns: list[str], widths: list[int], sheet_index: int) -> None:
    if sheet_name not in wb.sheetnames:
        ws = wb.create_sheet(sheet_name, sheet_index)
        ws.append(columns)
        _style_header(ws, columns, widths)
    else:
        _migrate_sheet(wb, sheet_name, columns, widths, sheet_index)


def load_or_create(path: Path) -> Workbook:
    if not path.exists():
        return _new_workbook()

    wb = load_workbook(path)
    _migrate_sheet(wb, "Jobs", COLUMNS, COLUMN_WIDTHS, 0)
    # Active workbook is Munich-only; historical archives are not modified.
    for name in list(wb.sheetnames):
        if any(term in name.lower() for term in ('swiss', 'zurich', 'zuerich', 'zürich', 'switzerland')):
            wb.remove(wb[name])
    ws = wb['Jobs']
    for row in range(ws.max_row, 1, -1):
        record = {col: ws.cell(row, i + 1).value for i, col in enumerate(COLUMNS)}
        if not allowed_job({'city':record.get('City'), 'company':record.get('Company'), 'url':record.get('URL'), 'location':record.get('Location')}):
            ws.delete_rows(row)
    return wb


def _existing_urls(ws: Worksheet, columns: list[str]) -> set[str]:
    url_col = columns.index("URL") + 1
    return {
        ws.cell(row=row, column=url_col).value
        for row in range(2, ws.max_row + 1)
        if ws.cell(row=row, column=url_col).value
    }


def _prune_below_score(ws: Worksheet, columns: list[str], min_score: int) -> int:
    if not min_score:
        return 0
    score_col = columns.index("Relevance Score (1-10)") + 1
    rows_to_delete = [
        row
        for row in range(2, ws.max_row + 1)
        if not (
            isinstance(ws.cell(row=row, column=score_col).value, (int, float))
            and ws.cell(row=row, column=score_col).value >= min_score
        )
    ]
    for row in reversed(rows_to_delete):
        ws.delete_rows(row)
    return len(rows_to_delete)


def _sort_by_relevance(
    ws: Worksheet, columns: list[str], fill: PatternFill, newly_added_urls: set[str]
) -> None:
    score_col = columns.index("Relevance Score (1-10)")
    url_col = columns.index("URL")

    rows = []
    for row in range(2, ws.max_row + 1):
        values = [ws.cell(row=row, column=c).value for c in range(1, len(columns) + 1)]
        rows.append(values)

    rows.sort(key=lambda v: (v[score_col] if isinstance(v[score_col], (int, float)) else 0), reverse=True)

    for row in range(2, ws.max_row + 1):
        for col in range(1, len(columns) + 1):
            ws.cell(row=row, column=col).value = None
            ws.cell(row=row, column=col).fill = PatternFill(fill_type=None)

    for i, values in enumerate(rows):
        row_idx = i + 2
        for col_idx, value in enumerate(values, start=1):
            ws.cell(row=row_idx, column=col_idx).value = value
        if values[url_col] in newly_added_urls:
            for col_idx in range(1, len(columns) + 1):
                ws.cell(row=row_idx, column=col_idx).fill = fill


def _update_sheet(
    wb: Workbook,
    sheet_name: str,
    columns: list[str],
    fill: PatternFill,
    new_jobs: list[dict[str, Any]],
    row_builder: Callable[[dict[str, Any], str], list],
    min_score: int = 0,
) -> dict[str, int]:
    ws = wb[sheet_name]
    new_jobs = [j for j in filter_recent_jobs(new_jobs) if allowed_job(j)]
    age_pruned = prune_old_rows(ws, columns)
    existing_urls = _existing_urls(ws, columns)

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
        ws.append(row_builder(job, url))
        newly_added_urls.add(url)
        existing_urls.add(url)
        added += 1

    pruned = age_pruned + _prune_below_score(ws, columns, min_score)
    _sort_by_relevance(ws, columns, fill, newly_added_urls)
    return {"added": added, "already_tracked": already_tracked, "pruned": pruned, "total_rows": ws.max_row - 1}


def _build_jobs_row(job: dict[str, Any], url: str) -> list:
    return [
        job.get("posted_date", ""),
        job.get("company", ""),
        job.get("city", ""),
        job.get("title", ""),
        job.get("relevance_score", 1),
        job.get("location", ""),
        url,
    ]


def update_tracker(path: Path, new_jobs: list[dict[str, Any]], min_score: int = 0) -> dict[str, int]:
    wb = load_or_create(path)
    summary = _update_sheet(wb, "Jobs", COLUMNS, NEW_ROW_FILL, new_jobs, _build_jobs_row, min_score=min_score)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return summary


def archive_and_reset(path: Path, archive_dir: Path) -> Path | None:
    if not path.exists():
        _new_workbook().save(path)
        return None

    archive_dir.mkdir(parents=True, exist_ok=True)
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
