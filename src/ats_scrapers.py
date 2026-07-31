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


def _safe_get(url: str, **kwargs) -> requests.Response | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kwargs)
        if resp.status_code >= 400:
            logger.warning("GET %s -> HTTP %s", url, resp.status_code)
            return None
        return resp
    except requests.RequestException as exc:
        logger.warning("GET %s failed: %s", url, exc)
        return None


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
        raw_date = job.get("updated_at") or job.get("first_published") or ""
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
    return jobs


# --------------------------------------------------------------------------
# SmartRecruiters
# --------------------------------------------------------------------------
def scrape_smartrecruiters(company_name: str, board_token: str = "") -> list[dict[str, Any]]:
    token = board_token or company_name.replace(" ", "")
    url = f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
    resp = _safe_get(url)
    if resp is None:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    jobs = []
    for job in data.get("content", []):
        location = ""
        loc = job.get("location") or {}
        if loc:
            location = ", ".join(filter(None, [loc.get("city"), loc.get("country")]))
        posted = ""
        raw_date = job.get("releasedDate") or job.get("createdOn") or ""
        if raw_date:
            try:
                posted = _iso(datetime.fromisoformat(raw_date.replace("Z", "+00:00")))
            except ValueError:
                posted = ""
        jobs.append(
            {
                "title": job.get("name", "").strip(),
                "location": location,
                "url": (job.get("ref") or {}).get("jobAd", "") or job.get("id", ""),
                "description": "",  # SmartRecruiters needs a 2nd call per job; kept light
                "posted_date": posted,
            }
        )
    return jobs


# --------------------------------------------------------------------------
# Personio
# --------------------------------------------------------------------------
def scrape_personio(company_name: str, board_token: str = "") -> list[dict[str, Any]]:
    token = board_token or _slugify(company_name)
    url = f"https://{token}.jobs.personio.de/xml"
    resp = _safe_get(url)
    if resp is None:
        return []
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
        link = f"https://{token}.jobs.personio.de/job/{job_id.text}" if job_id else ""
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
    return jobs


# --------------------------------------------------------------------------
# Workday
# --------------------------------------------------------------------------
def scrape_workday(company_name: str, careers_url: str) -> list[dict[str, Any]]:
    """
    Workday career sites follow the pattern:
      https://{tenant}.{dc}.myworkdayjobs.com/{site}
    with a matching JSON API at:
      https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs

    We try to derive the API URL from the given careers_url. If that
    fails (many companies are NOT actually on Workday despite the label,
    or customize the URL heavily), we return an empty list and let the
    dispatcher fall back to the generic scraper.

    Note on dates: Workday's API gives a relative string like "Posted
    3 Days Ago" (postedOn), not an absolute date. We store that string
    as-is in posted_date rather than guessing an exact date from it —
    it's still useful context, just not a precise calendar date.
    """
    m = re.match(
        r"https?://([\w-]+)\.(\w+)\.myworkdayjobs\.com/(?:\w+/)?([\w-]+)",
        careers_url,
    )
    if not m:
        logger.info("Workday URL pattern not recognized for %s: %s", company_name, careers_url)
        return []
    tenant, dc, site = m.groups()
    api_url = f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    payload = {"limit": 50, "offset": 0, "searchText": ""}
    try:
        resp = requests.post(api_url, json=payload, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code >= 400:
            return []
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Workday API failed for %s: %s", company_name, exc)
        return []

    jobs = []
    base = f"https://{tenant}.{dc}.myworkdayjobs.com/{site}"
    for posting in data.get("jobPostings", []):
        jobs.append(
            {
                "title": posting.get("title", "").strip(),
                "location": posting.get("locationsText", ""),
                "url": base + posting.get("externalPath", ""),
                "description": "",
                "posted_date": posting.get("postedOn", ""),  # relative text, see docstring
            }
        )
    return jobs


# --------------------------------------------------------------------------
# Generic HTML fallback
# --------------------------------------------------------------------------
JOB_LINK_HINTS = re.compile(
    r"(job|career|position|vacan|stelle|karriere)", re.IGNORECASE
)


def scrape_generic(company_name: str, careers_url: str) -> list[dict[str, Any]]:
    """
    Best-effort fallback for companies not on a known ATS platform, where
    the specific board token/tenant isn't known, or where the specific
    platform scraper came back empty (see the dispatcher below).

    This will not be as reliable as the API-based scrapers above, and it
    cannot see anything loaded by JavaScript after the page loads — a
    fair number of large companies (Apple, Amazon, Google, Microsoft,
    SAP, Oracle, etc.) build their career sites as JS-rendered single-
    page apps, so this fallback will legitimately find nothing there
    no matter how it's configured. That's a real limitation, not a bug
    to chase — see SETUP_GUIDE.md, "Companies that may never work well."

    No posted_date here: plain HTML link scraping has no reliable date
    field to read, so it's left blank for generic-sourced postings.
    """
    resp = _safe_get(careers_url)
    if resp is None:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    jobs = []
    seen_urls = set()
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        href = a["href"]
        if not text or len(text) < 6 or len(text) > 120:
            continue
        # Heuristic: title-looking text + link that smells like a job posting
        if not JOB_LINK_HINTS.search(href) and not JOB_LINK_HINTS.search(text):
            continue
        if href.startswith("/"):
            from urllib.parse import urljoin

            href = urljoin(careers_url, href)
        if href in seen_urls or not href.startswith("http"):
            continue
        seen_urls.add(href)
        jobs.append(
            {"title": text, "location": "", "url": href, "description": "", "posted_date": ""}
        )
    return jobs


# --------------------------------------------------------------------------
# Dispatcher
# --------------------------------------------------------------------------
def scrape_company(company: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Dispatch to the right scraper based on the company's configured ATS.
    Any explicit platform scraper that returns nothing falls back to the
    generic HTML scraper before giving up — see module docstring.
    """
    name = company["name"]
    careers_url = company["careers_url"]
    ats = (company.get("ats") or "auto").lower()
    token = company.get("board_token") or ""

    try:
        if ats == "greenhouse":
            result = scrape_greenhouse(name, token)
            return result or scrape_generic(name, careers_url)
        if ats == "lever":
            result = scrape_lever(name, token)
            return result or scrape_generic(name, careers_url)
        if ats == "smartrecruiters":
            result = scrape_smartrecruiters(name, token)
            return result or scrape_generic(name, careers_url)
        if ats == "personio":
            result = scrape_personio(name, token)
            return result or scrape_generic(name, careers_url)
        if ats == "workday":
            result = scrape_workday(name, careers_url)
            return result or scrape_generic(name, careers_url)
        if ats == "auto":
            # Try the API-based platforms cheaply before falling back to HTML.
            for fn in (
                lambda: scrape_greenhouse(name, token),
                lambda: scrape_lever(name, token),
                lambda: scrape_personio(name, token),
            ):
                result = fn()
                if result:
                    return result
                time.sleep(0.2)
            return scrape_generic(name, careers_url)
        # unknown value in companies.yaml -> fall back to generic
        return scrape_generic(name, careers_url)
    except Exception as exc:  # belt-and-braces: never let one company kill the run
        logger.error("Unhandled error scraping %s: %s", name, exc)
        return []
