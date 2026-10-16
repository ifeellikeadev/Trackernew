"""
ATS (Applicant Tracking System) scrapers.

Rather than writing fragile, one-off HTML scraping code for every single
company, this module targets the handful of job-board *platforms* that
most companies' career pages actually run on. Several of these expose
plain JSON (or XML) endpoints that are far more stable than scraping
rendered HTML, so we prefer those wherever possible.

Supported platforms:
  - greenhouse       (boards-api.greenhouse.io)
  - lever            (api.lever.co)
  - smartrecruiters  (api.smartrecruiters.com)
  - personio         ({company}.jobs.personio.de/xml)
  - workday          (myworkdayjobs.com CXS API)
  - generic          (best-effort HTML fallback, keyword-based)
  - headless         (last-resort: real Chromium render via Playwright,
                      only for results the fast path found suspiciously
                      thin — time-budgeted for the whole run)

A recurring real problem, found by hand multiple times (Sixt, Jet
Aviation, Hensoldt) before being fixed generally: a company's
config entry often stores its marketing homepage, not its actual job
board, on a completely different domain/subdomain. Rather than fix
these one at a time as they're noticed, discover_real_careers_url +
_recover_thin_result run for ANY company whose result looks
suspiciously thin, regardless of what platform its config claims —
fetching the stored URL and looking for the real job board (a known
platform's URL takes priority, using its own fast structured
scraper with the correctly-discovered token; otherwise, any
different-domain "jobs."/"careers." link found is still worth a
generic retry) before finally falling back to headless rendering as
the last, slowest resort.

Each function returns a list of dicts with the same shape:
  {
    "title": str,
    "location": str,
    "url": str,
    "description": str,   # plain text, used for scoring/flagging only
    "posted_date": str,   # ISO date if the platform provides one, else ""
  }

Every function is defensive: on any network error, unexpected response
shape, etc. it logs a warning and returns an empty list rather than
raising, so one broken company never stops the whole run.

IMPORTANT LESSON BAKED IN HERE (from a real run):
If a company's `ats:` is set explicitly (e.g. "workday") but the
`careers_url` doesn't actually match that platform (wrong tag, or a
generic corporate URL instead of the real ATS-hosted one), the
platform-specific scraper correctly returns nothing — but previously
the dispatcher stopped there instead of trying the generic HTML
fallback. That silently produced zero results for a lot of companies
whose `ats:` was guessed wrong. Fixed below: any explicit platform
scraper that comes back empty now falls through to scrape_generic()
as a last resort, same as "auto" already did.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse, urljoin, parse_qs
import html as html_lib

# RECENCY_PATCH_V1
from src.job_recency import parse_posted, today_local

def normalize_fresh_posted(value):
    observed = today_local()
    parsed = parse_posted(value, today=observed, allow_relative=True)
    return parsed.isoformat() if parsed is not None and parsed <= observed else value

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("job_scraper.ats")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PersonalJobTracker/1.0; "
        "+https://github.com/) research/personal-use job search bot"
    )
}
TIMEOUT = 20

# Words stripped from the end of a display name before guessing a board
# token. Names like "Databricks Zurich" or "Cisco Munich" otherwise slugify
# to "databrickszurich" / "ciscomunich", which is never a real board token.
_TRAILING_WORDS_TO_STRIP = [
    "munich", "muenchen", "zurich", "zuerich", "germany", "switzerland",
    "deutschland", "schweiz", "gmbh", "ag", "se", "group",
]


_GET_CACHE = {}
_HTTP_STATUS = {}

def _safe_get(url: str, **kwargs) -> requests.Response | None:
    key = (url, repr(sorted(kwargs.items())))
    if key in _GET_CACHE:
        if _GET_CACHE[key] is None:logger.warning("Cached unsuccessful request: %s", url)
        return _GET_CACHE[key]
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kwargs)
        _HTTP_STATUS[url]=resp.status_code
        _GET_CACHE[key] = resp if resp.status_code < 400 else None
        if resp.status_code >= 400:
            logger.warning("GET %s -> HTTP %s", url, resp.status_code)
            return None
        return resp
    except requests.RequestException as exc:
        _GET_CACHE[key]=None
        logger.warning("GET %s failed: %s", url, exc)
        return None


_safe_get.last_status=lambda url:_HTTP_STATUS.get(url)

def stamp_date(job, raw, field, kind='publication', source=''):
    job.update(_date_raw=raw or '',_date_field=field,_date_kind=kind,_date_source=source or job.get('url',''),_date_observed_on=today_local().isoformat())
    return job

def _slugify(name: str) -> str:
    """Best-effort guess at a company's board token from its display name."""
    words = name.lower().split()
    while words and re.sub(r"[^a-z]", "", words[-1]) in _TRAILING_WORDS_TO_STRIP:
        words.pop()
    s = " ".join(words) if words else name.lower()
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def _iso(dt_obj: datetime) -> str:
    return dt_obj.date().isoformat()


# --------------------------------------------------------------------------
# Greenhouse
# --------------------------------------------------------------------------
def scrape_greenhouse(company_name: str, board_token: str = "") -> list[dict[str, Any]]:
    token = board_token or _slugify(company_name)
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    resp = _safe_get(url)
    if resp is None:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    jobs = []
    for job in data.get("jobs", []):
        desc_html = job.get("content", "") or ""
        desc_text = BeautifulSoup(desc_html, "html.parser").get_text(" ", strip=True)
        posted = ""
        raw_date = job.get("first_published") or ""
        if raw_date:
            try:
                posted = _iso(datetime.fromisoformat(raw_date.replace("Z", "+00:00")))
            except ValueError:
                posted = ""
        jobs.append(
            {
                "title": job.get("title", "").strip(),
                "location": (job.get("location") or {}).get("name", ""),
                "url": job.get("absolute_url", ""),
                "description": desc_text,
                "posted_date": posted,
            }
        )
        stamp_date(jobs[-1],raw_date,'first_published')
    return jobs


# --------------------------------------------------------------------------
# Lever
# --------------------------------------------------------------------------
def scrape_lever(company_name: str, board_token: str = "") -> list[dict[str, Any]]:
    token = board_token or _slugify(company_name)
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    resp = _safe_get(url)
    if resp is None:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    jobs = []
    for job in data:
        desc = job.get("descriptionPlain") or job.get("description", "") or ""
        posted = ""
        created_ms = job.get("createdAt")
        if created_ms:
            try:
                posted = _iso(datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc))
            except (ValueError, OSError):
                posted = ""
        jobs.append(
            {
                "title": job.get("text", "").strip(),
                "location": (job.get("categories") or {}).get("location", ""),
                "url": job.get("hostedUrl", ""),
                "description": desc,
                "posted_date": posted,
            }
        )
        stamp_date(jobs[-1],created_ms,'createdAt','creation_unverified')
        jobs[-1]['posted_date']=''
    return jobs


# --------------------------------------------------------------------------
# SmartRecruiters
# --------------------------------------------------------------------------
def scrape_smartrecruiters(company_name: str, board_token: str = "") -> list[dict[str, Any]]:
    from urllib.parse import quote
    token = board_token or company_name.replace(" ", "")
    base = f"https://api.smartrecruiters.com/v1/companies/{quote(token, safe='')}/postings"
    jobs, seen, offset = [], set(), 0
    for _ in range(200):
        resp = _safe_get(base, params={"limit": 100, "offset": offset})
        if resp is None:
            logger.warning("SmartRecruiters incomplete pagination at offset %s; retaining %s candidates", offset, len(jobs))
            return jobs
        try:
            data = resp.json()
            page = data.get("content", [])
            if not isinstance(page, list):raise ValueError("SmartRecruiters content must be a list")
        except (ValueError, TypeError, AttributeError) as exc:
            logger.warning("SmartRecruiters incomplete pagination at offset %s: %s; retaining %s candidates", offset, str(exc)[:200], len(jobs))
            return jobs
        if not page:
            if data.get("totalFound") is not None and offset < int(data["totalFound"]):
                logger.warning("SmartRecruiters incomplete pagination: retrieved %s of %s", offset, data["totalFound"])
            return jobs
        added = 0
        for job in page:
            ident = str(job.get("id") or job.get("uuid") or "")
            if not ident or ident in seen:
                continue
            seen.add(ident)
            added += 1
            # Fetch descriptions only for date/title survivors in main.py.
            loc = job.get("location") or {}
            desc = ""
            raw_date = job.get("releasedDate") or ""
            posted = ""
            if raw_date:
                try:
                    posted = _iso(datetime.fromisoformat(raw_date.replace("Z", "+00:00")))
                except ValueError:
                    pass
            candidate = job.get("jobAdUrl") or ""
            if not isinstance(candidate, str) or not candidate.startswith(("https://", "http://")):
                candidate = f"https://jobs.smartrecruiters.com/{quote(token, safe='')}/{quote(ident, safe='')}"
            jobs.append({"title": (job.get("name") or "").strip(),
                         "location": ", ".join(str(loc[k]) for k in ("city", "country") if loc.get(k)),
                         "url": candidate,
                         "description": BeautifulSoup(desc, "html.parser").get_text(" ", strip=True),
                         "posted_date": posted, "_extraction": "smartrecruiters_api",
                         "_detail_api_url": base + "/" + quote(ident, safe="")})
            stamp_date(jobs[-1],raw_date,'releasedDate')
        if not added:
            logger.warning("SmartRecruiters incomplete pagination: repeated page; retaining %s candidates", len(jobs))
            return jobs
        offset += len(page)
        total = data.get("totalFound")
        if total is not None and offset >= int(total):
            return jobs
    logger.warning("SmartRecruiters incomplete pagination: safety limit; retaining %s candidates", len(jobs))
    return jobs


# --------------------------------------------------------------------------
# Personio
# --------------------------------------------------------------------------
def _parse_personio_xml(resp, token: str, domain: str) -> list[dict[str, Any]]:
    """Shared XML parsing for scrape_personio's .de/.com attempts."""
    try:
        soup = BeautifulSoup(resp.content, "xml")
    except Exception:
        soup = BeautifulSoup(resp.content, "html.parser")
    jobs = []
    for position in soup.find_all("position"):
        name = position.find("name")
        office = position.find("office")
        job_id = position.find("id")
        created = position.find("createdAt") or position.find("created_at")
        link = f"https://{token}.jobs.personio.{domain}/job/{job_id.text}" if job_id else ""
        posted = ""
        if created and created.text:
            # Personio's createdAt is usually "YYYY-MM-DD HH:MM:SS" or just a date
            posted = created.text.strip()[:10]

        # Personio's feed includes full description sections (title +
        # HTML body per section, e.g. "Your tasks", "Your profile") —
        # this was previously left blank; concatenating it gives the
        # relevance scorer real text to work with instead of just the
        # job title, which is the single biggest lever we have for
        # more accurate scores on Personio-based postings.
        description_parts = []
        for desc_block in position.find_all("jobDescription"):
            value = desc_block.find("value")
            if value and value.text:
                text_only = BeautifulSoup(value.text, "html.parser").get_text(" ", strip=True)
                if text_only:
                    description_parts.append(text_only)
        description = " ".join(description_parts)

        jobs.append(
            {
                "title": name.text.strip() if name else "",
                "location": office.text.strip() if office else "",
                "url": link,
                "description": description,
                "posted_date": posted,
            }
        )
        published=position.find('publishedAt') or position.find('published_at')
        if published and published.text:
            jobs[-1]['posted_date']=published.text.strip()
            stamp_date(jobs[-1],published.text.strip(),published.name)
        else:
            stamp_date(jobs[-1],created.text.strip() if created else '',created.name if created else 'none','creation_unverified')
            jobs[-1]['posted_date']=''
    return jobs


def scrape_personio(company_name: str, board_token: str = "", preferred_domain: str = "de") -> list[dict[str, Any]]:
    """
    Personio hosts company job boards on BOTH {token}.jobs.personio.de
    AND {token}.jobs.personio.com, depending on the company's own
    account configuration — there's no way to know which one a given
    company uses without trying (found via STARK Defence, whose real
    board is on .com; the original version of this function only ever
    tried .de, which would have silently returned nothing for any
    .com-hosted company, not just this one). Tries .de first (the more
    common case for German-market companies), falls back to .com only
    if .de returns nothing.
    """
    token = board_token or _slugify(company_name)
    for domain in dict.fromkeys((preferred_domain,"de","com")):
        url = f"https://{token}.jobs.personio.{domain}/xml"
        resp = _safe_get(url)
        if resp is None:
            continue
        jobs = _parse_personio_xml(resp, token, domain)
        if jobs:
            return jobs
    return []


# --------------------------------------------------------------------------
# Workday
# --------------------------------------------------------------------------
_WORKDAY_URL_RE = re.compile(
    r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[a-z]{2}/)?((?!wday(?:/|$))[\w-]+)", re.I
)


def _discover_workday_url(careers_url: str) -> str | None:
    """
    Most companies' `careers_url` in config is their own vanity domain
    (e.g. careers.astrazeneca.com), not the actual myworkdayjobs.com
    URL the workday API regex needs — so the direct match in
    scrape_workday fails for the large majority of workday-tagged
    companies even when they genuinely run on Workday.

    This fetches the vanity URL and looks for the real Workday URL in
    two places: (1) where the request actually ends up after redirects
    (requests follows redirects by default, and many corporate career
    pages permanently redirect straight to their Workday tenant), and
    (2) anywhere in the page's raw HTML (a myworkdayjobs.com link is
    very often embedded even without a server-side redirect — e.g. an
    "Apply" button pointing at it, or a client-side JS redirect that
    still leaves the target URL sitting in the page source as a string).

    Returns the discovered myworkdayjobs.com URL, or None if nothing
    was found (a real signal that the company likely isn't on Workday
    at all, whatever the config guessed).
    """
    resp = _safe_get(careers_url, allow_redirects=True)
    if resp is None:return None
    if _WORKDAY_URL_RE.search(resp.url):return resp.url
    source = html_lib.unescape(resp.text).replace('\\/', '/')
    match = _WORKDAY_URL_RE.search(source)
    return match.group(0) if match else None


def scrape_workday(company_name: str, careers_url: str) -> list[dict[str, Any]]:
    """
    Workday career sites follow the pattern:
      https://{tenant}.{dc}.myworkdayjobs.com/{site}
    with a matching JSON API at:
      https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs

    We try to derive the API URL from the given careers_url. If that
    doesn't match directly — the common case, since most companies'
    careers_url in config is their own vanity domain rather than the
    myworkdayjobs.com one — we fetch that page and try to discover the
    real Workday URL from it (see _discover_workday_url) before giving
    up. If discovery also finds nothing, we return an empty list and
    let the dispatcher fall back to the generic scraper — a genuine
    signal the company likely isn't actually on Workday, not just a
    URL-format mismatch.

    Note on dates: Workday's API gives a relative string like "Posted
    3 Days Ago" (postedOn), not an absolute date. We store that string
    as-is in posted_date rather than guessing an exact date from it —
    it's still useful context, just not a precise calendar date.
    """
    m = _WORKDAY_URL_RE.match(careers_url)
    if not m:
        discovered = _discover_workday_url(careers_url)
        if discovered:
            m = _WORKDAY_URL_RE.match(discovered)
        if not m:
            logger.info("Workday URL pattern not recognized for %s: %s", company_name, careers_url)
            return []
    tenant, dc, site = m.groups()
    api_url = f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    jobs, seen, offset = [], set(), 0
    base = f"https://{tenant}.{dc}.myworkdayjobs.com/{site}"
    pages = 0
    advertised_total = None
    completion = 'safety_limit'
    request_limit=20
    repeated_recovery=False
    source_started=time.monotonic()
    for _ in range(500):
        if time.monotonic()-source_started>=150:
            completion='time_limit';logger.warning('Workday incomplete pagination: time limit; retaining %s candidates',len(jobs));break
        try:
            resp = requests.post(api_url, json={"limit": request_limit, "offset": offset, "searchText": "", "appliedFacets": {}}, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            page = data.get("jobPostings", [])
            if not isinstance(page, list):raise ValueError("Workday jobPostings must be a list")
        except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
            completion = 'request_or_schema_failure'
            logger.warning("Workday incomplete pagination at offset %s: %s; retaining %s candidates", offset, str(exc)[:200], len(jobs))
            break
        pages += 1
        if data.get('total') is not None:
            try:advertised_total = int(data['total'])
            except (ValueError,TypeError):
                logger.warning('Workday invalid advertised total for %s', company_name)
        if not page:
            completion = 'empty_page'
            if advertised_total is not None and offset < advertised_total:
                logger.warning("Workday incomplete pagination: retrieved %s of advertised %s", offset, advertised_total)
            break
        added = 0
        for posting in page:
            path = posting.get("externalPath") or ""
            if not path or path in seen:continue
            seen.add(path);added += 1
            jobs.append({"title": (posting.get("title") or "").strip(), "location": posting.get("locationsText", ""),
                         "url": base + path, "description": "", "posted_date": normalize_fresh_posted(posting.get("postedOn", "")),
                         "_extraction": "workday_api"})
            stamp_date(jobs[-1],posting.get("postedOn", ""),"postedOn")
        if not added:
            if advertised_total is not None and len(jobs) >= advertised_total:
                completion = 'reported_total_reached'
            elif not repeated_recovery and offset>0 and len(page)!=request_limit:
                request_limit=len(page);repeated_recovery=True
                logger.info('Workday retry with observed page size %s at offset %s',request_limit,offset)
                continue
            else:
                completion = 'repeated_page'
                logger.warning("Workday incomplete pagination: offset %s repeated a page; retaining %s unique candidates", offset, len(jobs))
            break
        offset += len(page)
        # Stop at a multi-page reported total. Probe a page-sized total once:
        # some feeds have reported only the first page's size as their total.
        if advertised_total is not None and advertised_total > 20 and len(jobs) >= advertised_total:
            completion = 'reported_total_reached'
            break
    else:
        logger.warning("Workday incomplete pagination: safety limit reached; retaining partial results")
    for job in jobs:
        job['_pagination'] = {'pages': pages, 'advertised_total': advertised_total, 'unique_candidates': len(jobs), 'completion': completion}
    logger.info("Workday pagination %s: pages=%s advertised_total=%s unique=%s completion=%s", company_name, pages, advertised_total, len(jobs), completion)
    return jobs

# --------------------------------------------------------------------------
# Description enrichment (fetch the job's OWN posting page)
# --------------------------------------------------------------------------
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


def fetch_description_fallback(url: str, max_chars: int = 3000) -> str:
    """
    For platforms that don't include description text in their list
    endpoint (SmartRecruiters, Workday, generic HTML), this fetches
    the job posting's OWN page directly and pulls out visible text.

    This is first-party (the actual posting, not a third-party
    aggregator like Glassdoor/Indeed) — those are unreliable for
    scraping (actively blocked, against their terms, and would need
    fuzzy title/company matching to even find the right listing).
    Fetching the posting's own URL avoids all of that: we already
    know it's the exact right page.

    Deliberately called only for jobs that already passed the title +
    location filter (see main.py) — a handful of extra requests for
    genuine candidates, not one per raw posting scraped.

    Returns "" on any failure, or for pages that are JS-rendered SPAs
    with no server-side text (a real, unavoidable limitation — same
    one documented for scrape_generic).
    """
    resp = _safe_get(url)
    if resp is None:
        return ""
    try:
        html = resp.text
        html = _SCRIPT_STYLE_RE.sub(" ", html)
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        return text[:max_chars]
    except Exception as exc:
        logger.warning("Description fallback failed for %s: %s", url, exc)
        return ""


# --------------------------------------------------------------------------
# Generic HTML fallback
# --------------------------------------------------------------------------
JOB_LINK_HINTS = re.compile(
    r"(job|career|position|vacan|stelle|karriere)", re.IGNORECASE
)


def scrape_generic(company_name, careers_url):
    from src.public_boards import extract_page
    resp = _safe_get(careers_url)
    if resp is None:
        return []
    return extract_page(resp.text, resp.url or careers_url)


# --------------------------------------------------------------------------
# Headless-browser fallback — for JS-rendered single-page career sites
# (Sixt, Google, Amazon, Apple, Microsoft, SAP, Oracle, and others like
# them) where scrape_generic's plain HTTP request only ever sees the
# page's pre-JavaScript "shell" (nav links, footer, social links) — the
# actual job cards get injected by JavaScript after the page loads in a
# real browser, so they're invisible to a plain request no matter how
# scrape_generic's link-extraction is tuned.
#
# Rather than hand-maintaining a fixed list of "known JS-rendered
# companies" (inevitably incomplete — this exact problem was found on
# Sixt's site by accident, not because it was on any such list), this
# triggers automatically whenever the normal pipeline (API scraper +
# scrape_generic) comes back suspiciously thin for a given company —
# a real, low-effort proxy for "this is probably a JS shell, not a
# real empty job board." That means it opportunistically helps
# WHICHEVER companies actually need it, in Munich, Singapore, a Swiss
# city, or a Dream City alike, rather than only the ones already
# known about.
#
# GUARDRAIL: rendering a real browser page is 5-10x slower than a
# plain HTTP request. To keep total run time predictable regardless of
# how many companies trigger this, cumulative headless time across the
# WHOLE run (all passes combined — see main.py's reset_headless_budget
# call) is capped by _HEADLESS_BUDGET_SECONDS. Once the budget is
# spent, remaining companies just keep whatever scrape_generic already
# found — same as before this feature existed, not worse.
# --------------------------------------------------------------------------
_HEADLESS_BUDGET_SECONDS = 900  # 15 minutes reserved for the whole run
_headless_time_used = 0.0
GENERIC_RESULT_SUSPICIOUSLY_LOW = 5  # fewer real-looking links than this -> probably a JS shell, worth a retry


def reset_headless_budget() -> None:
    """Call once per full run (see main.py) so the budget doesn't leak across runs."""
    global _headless_time_used
    _headless_time_used = 0.0
    _GET_CACHE.clear()
    _HTTP_STATUS.clear()


def headless_budget_remaining() -> float:
    """Seconds of headless-rendering time left before this run's cap kicks in."""
    return max(0.0, _HEADLESS_BUDGET_SECONDS - _headless_time_used)


def scrape_headless(company_name: str, careers_url: str) -> list[dict[str, Any]]:
    """
    Renders careers_url in a real (headless) Chromium browser and applies
    the SAME link-extraction heuristic as scrape_generic, just against
    the fully-JavaScript-rendered page instead of the raw pre-JS HTML.

    Returns [] immediately (no browser launched) if the run's headless
    time budget is already spent — see module docstring above — or on
    any error (missing browser install, page timeout, etc.), same
    defensive contract as every other scraper here.
    """
    global _headless_time_used

    if headless_budget_remaining() <= 0:
        logger.warning("Browser budget exhausted; coverage unverified for %s", company_name)
        return []

    start = time.monotonic()
    try:
        from playwright.sync_api import sync_playwright  # imported lazily: only needed if this path is used

        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(user_agent=HEADERS["User-Agent"])
                response = page.goto(careers_url, timeout=15000, wait_until="domcontentloaded")
                if response is not None and response.status >= 400:
                    logger.warning("Browser HTTP %s %s", response.status, page.url)
                    _headless_time_used += time.monotonic() - start
                    return []
                try:
                    page.wait_for_selector("a[href*='/job'], a[href*='/position'], a[href*='/posting']", timeout=5000)
                except Exception:
                    pass
                # Bounded scroll to trigger lazy-loaded cards; not full pagination.
                for _ in range(2):
                    page.evaluate("() => { const root = document.scrollingElement || document.documentElement || document.body; if (root) window.scrollTo(0, root.scrollHeight); }")
                    page.wait_for_timeout(500)
                html = None
                for attempt in range(3):
                    try:
                        html = page.content()
                        break
                    except Exception:
                        if attempt == 2:raise
                        page.wait_for_timeout(500)
                final_url = page.url
            finally:
                browser.close()
    except Exception as exc:
        logger.warning("Headless render failed for %s: %s", company_name, exc)
        _headless_time_used += time.monotonic() - start
        return []
    _headless_time_used += time.monotonic() - start

    from src.public_boards import extract_page
    return extract_page(html, final_url)


# --------------------------------------------------------------------------
# Generalized platform discovery — extends the fix originally built for
# Workday (see _discover_workday_url above) to EVERY known ATS platform,
# not just Workday. The root problem is the same one found repeatedly
# by hand — Sixt, Jet Aviation, and Hensoldt were all silently broken
# because their config entry stored the company's own vanity homepage
# instead of the real job board, on three completely different
# platforms (a JS-rendered shell, a wrong domain entirely, and
# SuccessFactors). Rather than keep finding these one at a time, this
# runs the same "fetch the vanity page, look for the real board" check
# for every company whose result looks thin — regardless of what its
# config says the platform is.
# --------------------------------------------------------------------------
def scrape_ashby(company_name, board_token=''):
    from src.vacancy_extract import ashby_jobs
    from urllib.parse import quote
    if not board_token:
        raise ValueError('Ashby board token must come from an actual board URL')
    resp = _safe_get('https://api.ashbyhq.com/posting-api/job-board/' + quote(board_token, safe=''))
    if resp is None:
        raise RuntimeError('Ashby public feed unavailable')
    return ashby_jobs(resp.json())

_PLATFORM_URL_PATTERNS = {
    "ashby": re.compile(r"https?://jobs\.ashbyhq\.com/([\w-]+)"),
    "greenhouse": re.compile(r"https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/((?!embed(?:/|$))[\w-]+)"),
    "lever": re.compile(r"https?://jobs\.lever\.co/([\w-]+)"),
    "personio": re.compile(r"https?://([\w-]+)\.jobs\.personio\.(?:de|com)"),
    "smartrecruiters": re.compile(r"https?://(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)"),
}

_PLATFORM_SCRAPERS = {
    "ashby": scrape_ashby,
    "greenhouse": scrape_greenhouse,
    "lever": scrape_lever,
    "personio": scrape_personio,
    "smartrecruiters": scrape_smartrecruiters,
}

# A same-domain "careers" link on the vanity page itself isn't a real
# discovery (that's just the page we already have) — only a DIFFERENT
# domain counts as finding the real, separately-hosted job board.
_GENERIC_JOB_BOARD_HINT = re.compile(r"jobs\.|careers\.|/careers|/jobs", re.IGNORECASE)


def platform_from_url(url):
    parsed = urlparse(html_lib.unescape(url))
    host = (parsed.hostname or '').lower()
    if host in ('boards.greenhouse.io', 'job-boards.greenhouse.io', 'boards.eu.greenhouse.io', 'job-boards.eu.greenhouse.io'):
        parts = parsed.path.strip('/').split('/')
        token = parse_qs(parsed.query).get('for', [''])[0] if parts[0] == 'embed' else parts[0]
        if token and token != 'embed' and re.fullmatch(r'[\w-]+', token):
            return url, 'greenhouse', token
        return None, None, None
    for platform, pattern in _PLATFORM_URL_PATTERNS.items():
        match = pattern.search(url)
        if match:return url, platform, match.group(1)
    if _WORKDAY_URL_RE.search(url):return url, 'workday', None
    return None, None, None

def discover_real_careers_url(vanity_url):
    resp = _safe_get(vanity_url, allow_redirects=True)
    if resp is None:return None, None, None
    soup = BeautifulSoup(resp.text, 'html.parser')
    candidates = [resp.url]
    for tag in soup.find_all(['a', 'iframe', 'script']):
        target = tag.get('href') or tag.get('src')
        if target:candidates.append(urljoin(resp.url, html_lib.unescape(target)))
    candidates += re.findall(r'https?://[^\s"<>\']+', html_lib.unescape(resp.text))
    for candidate in candidates:
        found = platform_from_url(candidate)
        if found[0]:return found
    for candidate in candidates:
        path = urlparse(candidate).path.lower()
        if candidate.rstrip('/') == vanity_url.rstrip('/'):continue
        if path.endswith(('.pdf','.png','.jpg','.js','.css')) or any(x in path for x in ('privacy','datenschutz','legal')):continue
        if _GENERIC_JOB_BOARD_HINT.search(candidate):return candidate, None, None
    return None, None, None


def _recover_thin_result(company_name: str, careers_url: str, current_result: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Last-resort recovery chain for a suspiciously thin result, tried in
    order of cost — cheapest first, headless (the slowest, budget-
    capped option) only as the final step:
      1. discover_real_careers_url — if the stored URL is actually a
         vanity homepage, find the real job board. If it's a known
         platform, use that platform's own fast structured scraper
         with the correctly-discovered token, not a guessed one.
      2. If a URL was discovered but isn't a recognized platform,
         retry the generic scraper against THAT url instead of the
         original — often enough on its own without ever needing
         headless rendering.
      3. If still thin, headless-render whichever URL is most likely
         correct (discovered if found, else the original one).
    Only runs any of this if current_result is already thin — a
    healthy result never pays any of these costs.
    """
    from src.vacancy_extract import usable_title
    usable = sum(usable_title(j.get('title','')) and not j.get('_needs_title') for j in current_result)
    if usable >= GENERIC_RESULT_SUSPICIOUSLY_LOW:
        return current_result

    best_result = current_result
    best_url = careers_url

    discovered_url, platform, token = discover_real_careers_url(careers_url)
    if discovered_url:
        best_url = discovered_url
        try:
            if platform in _PLATFORM_SCRAPERS:
                candidate = _PLATFORM_SCRAPERS[platform](company_name, token or "")
            elif platform == "workday":
                candidate = scrape_workday(company_name, discovered_url)
            else:
                candidate = scrape_generic(company_name, discovered_url)
        except Exception as exc:
            logger.warning('Discovered board failed for %s; keeping existing candidates: %s',company_name,str(exc)[:200])
            candidate = []
        # Same reasoning as the headless case below: any NON-EMPTY
        # result from here wins outright, not just a bigger one — a
        # real platform's own API (or a retry against the actually-
        # correct URL) is trustworthy even if it happens to return
        # fewer raw entries than whatever junk the thin result had.
        if candidate:
            best_result = candidate

    if sum(usable_title(j.get('title','')) and not j.get('_needs_title') for j in best_result) >= GENERIC_RESULT_SUSPICIOUSLY_LOW:
        return best_result
    if headless_budget_remaining() <= 0:
        logger.warning("Browser budget exhausted; coverage unverified for %s", company_name)
        return best_result

    if _HTTP_STATUS.get(best_url) in (401,403,429):
        logger.warning('Access/rate limit for %s; no browser retry against the same URL',company_name)
        return best_result
    headless_result = scrape_headless(company_name, best_url)
    from src.vacancy_extract import dedupe
    return dedupe(best_result + headless_result)


# Kept as an alias: _maybe_headless_upgrade was the name used before
# this was generalized beyond just headless rendering. Same signature,
# same call sites — _recover_thin_result is the real implementation now.
_maybe_headless_upgrade = _recover_thin_result


# --------------------------------------------------------------------------
# Dispatcher
# --------------------------------------------------------------------------
def scrape_company(company: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Dispatch to the right scraper based on the company's configured ATS.
    Any explicit platform scraper that returns nothing falls back to the
    generic HTML scraper before giving up — see module docstring. If
    THAT also comes back suspiciously thin, one more retry happens via
    a real headless browser (scrape_headless / _maybe_headless_upgrade)
    before finally giving up — see that section's docstring for why
    and how this is time-budgeted.
    """
    name = company["name"]
    careers_url = company["careers_url"]
    ats = (company.get("ats") or "auto").lower()
    token = company.get("board_token") or ""

    try:
        if ats == "jsonld_feed":
            from src.source_adapters import scrape_jsonld_feed
            return scrape_jsonld_feed(name,careers_url,_safe_get)
        if ats == "amazon":
            from src.source_adapters import scrape_amazon
            return scrape_amazon(name,careers_url,_safe_get)
        if ats == "public_board":
            from src.public_boards import scrape_html_board
            return scrape_html_board(name, careers_url, _safe_get)
        if ats == "eightfold":
            from src.public_boards import scrape_eightfold
            return scrape_eightfold(name, careers_url, token, _safe_get)
        if ats == "ashby":
            match = _PLATFORM_URL_PATTERNS["ashby"].search(careers_url)
            return scrape_ashby(name, token or (match.group(1) if match else ""))
        if ats == "greenhouse":
            result = scrape_greenhouse(name, token)
            if result:
                return result
            return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
        if ats == "lever":
            result = scrape_lever(name, token)
            if result:
                return result
            return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
        if ats == "smartrecruiters":
            result = scrape_smartrecruiters(name, token)
            if result:
                return result
            return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
        if ats == "personio":
            preferred='com' if '.jobs.personio.com' in careers_url else 'de'
            result = scrape_personio(name, token, preferred)
            if result:return result
            from src.public_boards import scrape_html_board
            return scrape_html_board(name,careers_url,_safe_get)
        if ats == "workday":
            result = scrape_workday(name, careers_url)
            if result:
                return result
            return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
        if ats == "auto":
            for platform, pattern in _PLATFORM_URL_PATTERNS.items():
                found_url, found_platform, found_token = platform_from_url(careers_url)
                if found_platform == platform:
                    result = _PLATFORM_SCRAPERS[platform](name, token or found_token)
                    if result:
                        return result
                    return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
            if _WORKDAY_URL_RE.match(careers_url):
                result = scrape_workday(name, careers_url)
                if result:
                    return result
            return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
        # unknown value in companies.yaml -> fall back to generic
        return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
    except Exception as exc:  # belt-and-braces: never let one company kill the run
        logger.error("Unhandled error scraping %s: %s", name, exc)
        raise


def fetch_job_details(url):
    from src.vacancy_extract import structured_jobs, plain, detail_title
    resp = _safe_get(url)
    if resp is None:
        return {}, ''
    if urlparse(url).hostname == 'api.smartrecruiters.com':
        data = resp.json()
        sections = (data.get('jobAd') or {}).get('sections') or {}
        desc = ' '.join(plain(section.get('text','')) for section in sections.values() if isinstance(section,dict))
        loc = data.get('location') or {}
        return {'description': desc, 'location': ', '.join(str(loc[k]) for k in ('city','country') if loc.get(k)), 'posted_date': normalize_fresh_posted(data.get('releasedDate','')),'_date_raw':data.get('releasedDate',''),'_date_field':'releasedDate','_date_kind':'publication','_date_source':url}, desc
    jobs = structured_jobs(resp.text, resp.url or url)
    # Only trust one identifiable vacancy, not a board containing many jobs.
    details = jobs[0] if len(jobs) == 1 else {}
    if not jobs:
        recovered = detail_title(resp.text)
        if recovered:details = {'title': recovered}
    soup = BeautifulSoup(resp.text, 'html.parser')
    if not details.get('location'):
        location_tag = soup.select_one('[itemprop="jobLocation"],.job-location,.jobLocation,.job-location-label,[data-ph-at-id="job-location"],[data-testid="job-location"]')
        if location_tag:
            details['location'] = location_tag.get('content') or location_tag.get_text(' ', strip=True)
            details['_location_source']='detail_explicit_location'
        else:
            for label in soup.select('dt,th'):
                if label.get_text(' ', strip=True).lower().rstrip(':') in ('standort','standorte','location','locations','arbeitsort'):
                    value = label.find_next_sibling(['dd','td'])
                    if value:details['location'] = value.get_text(' ', strip=True);break
    if not details.get('posted_date'):
        posted = soup.select_one('[itemprop="datePosted"]')
        if posted:
            details['posted_date'] = posted.get('content') or posted.get('datetime') or posted.get_text(' ',strip=True)
            stamp_date(details,details['posted_date'],'datePosted',source=url)
    for tag in soup(['script','style','nav','footer','header']):tag.decompose()
    return details, soup.get_text(' ',strip=True)[:10000]
