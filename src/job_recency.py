"""Keep jobs no more than 21 days old; unknown dates are retained."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import re
MAX_JOB_AGE_DAYS = 21

def today_local():
    return datetime.now(ZoneInfo("Europe/Berlin")).date()

def parse_posted(value, *, today=None, allow_relative=False):
    today = today or today_local()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", text):
        try:
            return datetime.strptime(text, "%d.%m.%Y").date()
        except ValueError:
            return None
    if not allow_relative:
        return None
    t = re.sub(r"^posted\s+", "", text.lower()).strip()
    if t in ("today", "heute", "just posted"):
        return today
    if t in ("yesterday", "gestern"):
        return today - timedelta(days=1)
    # Do not invent a date for '30+ days ago' or approximate ranges.
    m = re.fullmatch(r"(\d+)\s+(day|days|week|weeks|hour|hours)\s+ago", t)
    if m:
        n = int(m.group(1)); unit = m.group(2)
        days = n * 7 if unit.startswith("week") else n if unit.startswith("day") else n // 24
        return today - timedelta(days=days)
    m = re.fullmatch(r"vor\s+(\d+)\s+(tag|tagen|tage|woche|wochen)", t)
    if m:
        n = int(m.group(1))
        return today - timedelta(days=n * 7 if m.group(2).startswith("woch") else n)
    return None

def keep_posted(value, *, today=None, allow_relative=False):
    today = today or today_local()
    posted = parse_posted(value, today=today, allow_relative=allow_relative)
    # Future dates are treated as uncertain source data, not silently deleted.
    return posted is None or posted > today or (today - posted).days <= MAX_JOB_AGE_DAYS

def filter_recent_jobs(jobs, *, today=None):
    today = today or today_local()
    kept = []
    for job in jobs:
        raw = job.get("posted_date", "")
        if not keep_posted(raw, today=today, allow_relative=True):
            continue
        item = dict(job)
        posted = parse_posted(raw, today=today, allow_relative=True)
        if posted is not None and posted <= today:
            item["posted_date"] = posted.isoformat()
        kept.append(item)
    return kept

def prune_old_rows(ws, columns, *, today=None):
    today = today or today_local()
    if "Job Posted" not in columns:
        return 0
    col = columns.index("Job Posted") + 1
    removed = 0
    for row in range(ws.max_row, 1, -1):
        # Old relative strings have no observation date: never rebase them
        # against today, which would make them stay recent forever.
        if not keep_posted(ws.cell(row=row, column=col).value, today=today):
            ws.delete_rows(row)
            removed += 1
    return removed
