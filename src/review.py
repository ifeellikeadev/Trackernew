"""Separate review queue; never bypasses the main tracker's filters."""
import csv
import re
from pathlib import Path
from src.extraction import usable_title, is_vacancy_url
from src.matching import title_decision, location_decision, date_decision, score_jobs, excluded_seniority

FIELDS = ['company', 'title', 'location', 'url', 'relevance_score', 'review_reason', 'location_status']

def review_hint(job, profile):
    cfg = profile.get('review_candidates') or {}
    if cfg.get('enabled') is not True:
        return False
    title = job.get('title', '')
    direct = title_decision(title, profile.get('title_must_match', []), profile.get('title_must_not_match', []))
    if direct.get('reason') != 'no_requested_title_term' or excluded_seniority(title):
        return False
    text = title + ' ' + job.get('description', '')
    return any(re.search(r'(?<!\w)' + re.escape(t) + r'(?!\w)', text, re.I) for t in cfg.get('terms', []))

def review_candidate(job, profile, company):
    if not review_hint(job, profile) or not usable_title(job.get('title')) or not is_vacancy_url(job.get('url', '')):
        return None
    if not job.get('description') or not date_decision(job)['keep']:
        return None
    loc = location_decision(job)
    if loc['status'] in ('outside', 'excluded'):
        return None
    # Description-only scoring prevents an unusual title from creating a false high score.
    description_job = dict(job, title='')
    score_jobs([description_job], profile)
    score = description_job['relevance_score']
    if score < int((profile.get('review_candidates') or {}).get('min_description_score', 7)):
        return None
    return dict(company=company['name'], title=job['title'], location=job.get('location', ''),
                url=job['url'], relevance_score=score, location_status=loc['status'],
                review_reason='Strong description evidence; title not accepted by main filter')

def save_review(rows, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            if row['url'] in seen:
                continue
            seen.add(row['url'])
            # Prevent formula execution when opened in spreadsheet applications.
            safe = {k: ("'" + str(row.get(k, '')) if str(row.get(k, '')).startswith(('=', '+', '-', '@')) else row.get(k, '')) for k in FIELDS}
            writer.writerow(safe)
    temporary.replace(path)
