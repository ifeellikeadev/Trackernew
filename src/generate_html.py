"""
Turns data/job_tracker.xlsx into a plain, static HTML page at
docs/index.html, so it can be published via GitHub Pages — one link,
opens in any browser, no Excel and no download needed.

Called automatically at the end of both src/main.py (daily scrape) and
src/reset_tracker.py (monthly reset), so the page is always in sync
with whatever's actually in the Excel file. Not meant to be run on its
own, though `python -m src.generate_html` works fine for testing.

PRIVACY NOTE: GitHub Pages sites are public to anyone with the link,
even when the repository itself is private (unless you're on GitHub
Enterprise). This page will show your company/job data to anyone who
finds the URL — see SETUP_GUIDE.md, "Enabling the public webpage view."
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from openpyxl import load_workbook

from src.tracker import COLUMNS, NEW_ROW_FILL

ROOT = Path(__file__).resolve().parent.parent
TRACKER_FILE = ROOT / "data" / "job_tracker.xlsx"
OUTPUT_FILE = ROOT / "docs" / "index.html"

NEW_ROW_RGB = NEW_ROW_FILL.start_color.rgb  # e.g. "00D1FAE5" or "FFD1FAE5"


def _is_new_row_fill(cell) -> bool:
    rgb = getattr(cell.fill.start_color, "rgb", None)
    if not isinstance(rgb, str) or not isinstance(NEW_ROW_RGB, str):
        return False
    return rgb[-6:] == NEW_ROW_RGB[-6:]  # compare ignoring alpha prefix


def _escape(value) -> str:
    if value is None:
        return ""
    text = str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def generate(tracker_path: Path = TRACKER_FILE, output_path: Path = OUTPUT_FILE) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not tracker_path.exists():
        rows_html = "<tr><td colspan='9'>No data yet — the scraper hasn't run.</td></tr>"
        total = 0
    else:
        wb = load_workbook(tracker_path)
        ws = wb["Jobs"] if "Jobs" in wb.sheetnames else wb.active
        url_col = COLUMNS.index("URL") + 1

        rows_html_parts = []
        for row in range(2, ws.max_row + 1):
            values = [ws.cell(row=row, column=c).value for c in range(1, len(COLUMNS) + 1)]
            is_new = _is_new_row_fill(ws.cell(row=row, column=1))
            url = values[url_col - 1] or "#"
            title_col = COLUMNS.index("Job Title")

            cells = []
            for i, val in enumerate(values):
                if i == title_col and url and url != "#":
                    cells.append(f"<td><a href='{_escape(url)}' target='_blank' rel='noopener'>{_escape(val)}</a></td>")
                elif i == url_col - 1:
                    continue  # URL is folded into the Job Title link, not shown as its own column
                else:
                    cells.append(f"<td>{_escape(val)}</td>")
            row_class = " class='new-row'" if is_new else ""
            rows_html_parts.append(f"<tr{row_class}>{''.join(cells)}</tr>")

        rows_html = "\n".join(rows_html_parts) if rows_html_parts else "<tr><td colspan='9'>No matching jobs yet.</td></tr>"
        total = ws.max_row - 1

    display_columns = [c for c in COLUMNS if c != "URL"]  # URL folded into Job Title link
    header_html = "".join(f"<th>{_escape(c)}</th>" for c in display_columns)
    updated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Job Tracker</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 0; padding: 16px; background: #f9fafb; color: #111827; }}
  h1 {{ font-size: 1.3rem; margin-bottom: 4px; }}
  .meta {{ color: #6b7280; font-size: 0.85rem; margin-bottom: 16px; }}
  .legend {{ display: inline-block; width: 12px; height: 12px; background: #D1FAE5; border: 1px solid #a7f3d0; margin-right: 6px; vertical-align: middle; }}
  table {{ border-collapse: collapse; width: 100%; background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  th, td {{ padding: 8px 10px; text-align: left; border-bottom: 1px solid #e5e7eb; font-size: 0.85rem; vertical-align: top; }}
  th {{ background: #1F2937; color: white; position: sticky; top: 0; white-space: nowrap; }}
  tr.new-row {{ background: #D1FAE5; }}
  tr:hover {{ background: #f3f4f6; }}
  tr.new-row:hover {{ background: #bbf7d0; }}
  a {{ color: #2563eb; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .scroll-wrap {{ overflow-x: auto; }}
  @media (max-width: 700px) {{
    th, td {{ font-size: 0.75rem; padding: 6px; }}
    body {{ padding: 8px; }}
  }}
</style>
</head>
<body>
<h1>Job Tracker — Munich &amp; Zurich</h1>
<div class="meta">
  {total} tracked postings &middot; last updated {updated} &middot;
  <span class="legend"></span>added since your last check
</div>
<div class="scroll-wrap">
<table>
<thead><tr>{header_html}</tr></thead>
<tbody>
{rows_html}
</tbody>
</table>
</div>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    generate()
