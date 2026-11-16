"""Conservative extraction acceptance: unknown coverage never passes."""
from urllib.parse import urlparse
from src.extraction import usable_title, is_vacancy_url

COMPLETE = {'reported_total_reached', 'empty_page', 'all_pages_fetched', 'single_feed_complete'}

def valid_url(value):
    try:
        u = urlparse(value or '')
        return u.scheme in ('https', 'http') and bool(u.hostname) and not u.username and not u.password
    except (TypeError, ValueError):
        return False

def quality(jobs, pagination=None, expected_locations=None):
    stats = pagination or getattr(jobs, 'pagination', None) or next((j.get('_pagination') for j in jobs if j.get('_pagination')), {})
    count = int(stats.get('advertised_total') or 0)
    # Some adapters drop malformed entries. Keep the original denominator where available.
    raw = max(len(jobs), int(stats.get('raw_candidates') or 0), count)
    usable = sum(usable_title(j.get('title', '')) and valid_url(j.get('url')) and is_vacancy_url(j.get('url', '')) for j in jobs)
    located = sum(bool(str(j.get('location') or '').strip()) for j in jobs)
    title_ratio = usable / raw if raw else 0
    location_ratio = located / raw if raw else 0
    complete = stats.get('completion') in COMPLETE and (not count or len(jobs) >= count)
    reasons = []
    if not jobs: reasons.append('empty_unverified')
    if title_ratio < .95: reasons.append('usable_title_url_below_95_percent')
    if location_ratio < .80: reasons.append('explicit_location_below_80_percent')
    if not complete: reasons.append('pagination_incomplete_or_unknown')
    expected = expected_locations or []
    expected_matches = sum(any(str(x).casefold() in str(j.get('location', '')).casefold() for x in expected) for j in jobs)
    return dict(raw=raw, extracted=len(jobs), usable=usable, with_location=located,
                usable_ratio=round(title_ratio, 4), location_ratio=round(location_ratio, 4),
                pagination=stats, pagination_complete=complete, expected_location_matches=expected_matches,
                passes=not reasons, reasons=reasons)
