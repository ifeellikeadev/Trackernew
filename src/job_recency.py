from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import re
MAX_JOB_AGE_DAYS = 21

def today_local():
    return datetime.now(ZoneInfo("Europe/Berlin")).date()

def parse_posted(value, *, today=None, allow_relative=False):
    today = today or today_local()
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo("Europe/Berlin")).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone(ZoneInfo("Europe/Berlin")).date() if parsed.tzinfo else parsed.date()
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
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:return datetime.strptime(text,fmt).date()
        except ValueError:pass
    if not allow_relative:
        return None
    t = re.sub(r"^(?:posted|published|veröffentlicht)\s+", "", text.lower()).strip()
    t = re.sub(r"(?<=\d)\+", "", t)
    if t in ("today", "heute", "just posted"):
        return today
    if t in ("yesterday", "gestern"):
        return today - timedelta(days=1)
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
    return posted is None or posted > today or (today - posted).days <= MAX_JOB_AGE_DAYS

def date_decision(job, *, today=None):
    today=today or today_local()
    raw=job.get('_date_raw',job.get('posted_date',''))
    field=job.get('_date_field','posted_date_unspecified')
    kind=job.get('_date_kind','publication_unspecified')
    observed=job.get('_date_observed_on',today.isoformat())
    if kind in ('creation_unverified','update','first_seen'):
        return dict(keep=True,reason='not_a_verified_publication_date',raw=str(raw or ''),field=field,kind=kind,parsed='',age_days=None,observed_on=observed,source=job.get('_date_source',job.get('url','')))
    # Prefer an absolute date already anchored at retrieval. Relative text is
    # parsed against this run only; it is never refreshed in saved workbook rows.
    value=job.get('posted_date','')
    posted=parse_posted(value,today=today,allow_relative=True)
    if posted is None:reason='missing_date' if not str(value or '').strip() else 'unparseable_date'
    elif posted>today:reason='future_date_needs_review'
    elif (today-posted).days>MAX_JOB_AGE_DAYS:reason='older_than_21_days'
    else:reason='within_21_days'
    return dict(keep=reason!='older_than_21_days',reason=reason,raw=str(raw or ''),field=field,kind=kind,parsed=posted.isoformat() if posted else '',age_days=(today-posted).days if posted else None,observed_on=observed,source=job.get('_date_source',job.get('url','')))

def filter_recent_jobs(jobs, *, today=None):
    today=today or today_local();kept=[]
    for job in jobs:
        decision=date_decision(job,today=today)
        if not decision['keep']:continue
        item=dict(job);item['_date_audit']=decision
        if decision['reason']=='not_a_verified_publication_date':item['posted_date']=''
        elif decision['parsed'] and decision['reason']!='future_date_needs_review':item['posted_date']=decision['parsed']
        kept.append(item)
    return kept

def prune_old_rows(ws, columns, *, today=None):
    today = today or today_local()
    if "Job Posted" not in columns:
        return 0
    col = columns.index("Job Posted") + 1
    removed = 0
    for row in range(ws.max_row, 1, -1):
        if not keep_posted(ws.cell(row=row, column=col).value, today=today):
            ws.delete_rows(row)
            removed += 1
    return removed
