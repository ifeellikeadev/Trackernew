"""Validated run policy. Full coverage is the default; tiers are opt-in."""
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from contextvars import ContextVar
from contextlib import contextmanager

_OPTIONS = ContextVar('company_options', default={})
DEFAULT_SCHEDULE = {
    'priority_1': {'weekdays': list(range(7)), 'source_seconds': 90},
    'priority_2': {'weekdays': [1, 4], 'source_seconds': 45},
    'priority_3': {'weekdays': [6], 'source_seconds': 25},
}

def priority(company):
    value = int(company.get('priority', 3))
    if value not in (1, 2, 3):
        raise ValueError('priority must be 1, 2 or 3')
    return value

def selected(company, schedule=None, mode=None, now=None):
    if company.get('enabled', True) is not True:
        return False
    tier = priority(company)
    mode = (mode or os.getenv('SCRAPE_TIER', 'all')).lower()
    if mode in ('all', 'full'):
        return True
    if mode in ('daily', '1', 'priority_1'):
        return tier == 1
    if mode in ('2', 'priority_2'):
        return tier <= 2
    if mode != 'scheduled':
        raise ValueError('Unknown SCRAPE_TIER: ' + mode)
    rule = (schedule or DEFAULT_SCHEDULE).get('priority_' + str(tier), {})
    days = rule.get('weekdays', DEFAULT_SCHEDULE['priority_' + str(tier)]['weekdays'])
    if not isinstance(days, list) or any(type(d) is not int or d not in range(7) for d in days):
        raise ValueError('weekdays must contain integers 0 (Monday) through 6 (Sunday)')
    today = now or datetime.now(ZoneInfo('Europe/Berlin'))
    return today.weekday() in days

def source_seconds(company, schedule=None, mode=None):
    rule = (schedule or DEFAULT_SCHEDULE).get('priority_' + str(priority(company)), {})
    mode=(mode or os.getenv('SCRAPE_TIER','all')).lower()
    default=(schedule or {}).get('full_run_source_seconds',180) if mode in ('all','full') else rule.get('source_seconds',DEFAULT_SCHEDULE['priority_' + str(priority(company))]['source_seconds'])
    value = float(company.get('source_seconds',default))
    if not 1 <= value <= 600:
        raise ValueError('source_seconds must be between 1 and 600')
    return value

@contextmanager
def company_options(company):
    token = _OPTIONS.set(company)
    try:
        yield
    finally:
        _OPTIONS.reset(token)

def page_limit(default):
    value = int(_OPTIONS.get().get('max_pages', default))
    if not 1 <= value <= 1000:
        raise ValueError('max_pages must be between 1 and 1000')
    return value
