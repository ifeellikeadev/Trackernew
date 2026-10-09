
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

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

_TRAILING_WORDS_TO_STRIP = [
    "munich", "muenchen", "germany", 
    "deutschland", "gmbh", "ag", "se", "group",
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
    words = name.lower().split()
    while words and re.sub(r"[^a-z]", "", words[-1]) in _TRAILING_WORDS_TO_STRIP:
        words.pop()
    s = " ".join(words) if words else name.lower()
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def _iso(dt_obj: datetime) -> str:
    return dt_obj.date().isoformat()


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
    return jobs


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


def scrape_smartrecruiters(company_name: str, board_token: str = "") -> list[dict[str, Any]]:
    from urllib.parse import quote
    token = board_token or company_name.replace(" ", "")
    base = f"https://api.smartrecruiters.com/v1/companies/{quote(token, safe='')}/postings"
    jobs, seen, offset = [], set(), 0
    for _ in range(200):
        resp = _safe_get(base, params={"limit": 100, "offset": offset})
        if resp is None:
            raise RuntimeError(f"SmartRecruiters feed unavailable at offset {offset}")
        data = resp.json()
        page = data.get("content", [])
        if not isinstance(page, list):
            raise ValueError("SmartRecruiters content must be a list")
        if not page:
            return jobs
        added = 0
        for job in page:
            ident = str(job.get("id") or job.get("uuid") or "")
            if not ident or ident in seen:
                continue
            seen.add(ident)
            added += 1
            details = {}
            detail_resp = _safe_get(base + "/" + quote(ident, safe=''))
            if detail_resp is not None:
                try:
                    details = detail_resp.json()
                except ValueError:
                    pass
            if not isinstance(details, dict):
                details = {}
            loc = details.get("location") or job.get("location") or {}
            ad = details.get("jobAd") or {}
            sections = ad.get("sections", {}) if isinstance(ad, dict) else {}
            desc = " ".join(str(v.get("text", "")) for v in sections.values() if isinstance(v, dict)) if isinstance(sections, dict) else ""
            raw_date = job.get("releasedDate") or ""
            posted = ""
            if raw_date:
                try:
                    posted = _iso(datetime.fromisoformat(raw_date.replace("Z", "+00:00")))
                except ValueError:
                    pass
            candidate = details.get("jobAdUrl") or job.get("jobAdUrl") or ""
            if not isinstance(candidate, str) or not candidate.startswith(("https://", "http://")):
                candidate = f"https://jobs.smartrecruiters.com/{quote(token, safe='')}/{quote(ident, safe='')}"
            jobs.append({"title": (job.get("name") or "").strip(),
                         "location": ", ".join(str(loc[k]) for k in ("city", "country") if loc.get(k)),
                         "url": candidate,
                         "description": BeautifulSoup(desc, "html.parser").get_text(" ", strip=True),
                         "posted_date": posted})
        if not added:
            raise RuntimeError("SmartRecruiters repeated a page; pagination incomplete")
        offset += len(page)
        total = data.get("totalFound")
        if total is not None and offset >= int(total):
            return jobs
    raise RuntimeError("SmartRecruiters pagination safety limit reached")


def _parse_personio_xml(resp, token: str, domain: str) -> list[dict[str, Any]]:
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
            posted = created.text.strip()[:10]

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


def scrape_personio(company_name: str, board_token: str = "") -> list[dict[str, Any]]:
    token = board_token or _slugify(company_name)
    for domain in ("de", "com"):
        url = f"https://{token}.jobs.personio.{domain}/xml"
        resp = _safe_get(url)
        if resp is None:
            continue
        jobs = _parse_personio_xml(resp, token, domain)
        if jobs:
            return jobs
    return []


_WORKDAY_URL_RE = re.compile(
    r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)"
)


def _discover_workday_url(careers_url: str) -> str | None:
    try:
        resp = requests.get(careers_url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException:
        return None

    if _WORKDAY_URL_RE.search(resp.url):
        return resp.url

    match = _WORKDAY_URL_RE.search(resp.text)
    if match:
        return match.group(0)

    return None


def scrape_workday(company_name: str, careers_url: str) -> list[dict[str, Any]]:
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
    for _ in range(500):
        resp = requests.post(api_url, json={"limit": 20, "offset": offset, "searchText": ""}, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        page = data.get("jobPostings", [])
        if not isinstance(page, list):
            raise ValueError("Workday jobPostings must be a list")
        if not page:
            return jobs
        added = 0
        for posting in page:
            path = posting.get("externalPath") or ""
            if not path or path in seen:
                continue
            seen.add(path)
            added += 1
            jobs.append({"title": (posting.get("title") or "").strip(),
                         "location": posting.get("locationsText", ""),
                         "url": base + path,
                         "description": "", "posted_date": normalize_fresh_posted(posting.get("postedOn", ""))})
        if not added:
            raise RuntimeError("Workday repeated a page; pagination incomplete")
        offset += len(page)
        total = data.get("total")
        if total is not None and offset >= int(total):
            return jobs
    raise RuntimeError("Workday pagination safety limit reached")


_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


def fetch_description_fallback(url: str, max_chars: int = 3000) -> str:
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


JOB_LINK_HINTS = re.compile(
    r"(job|career|position|vacan|stelle|karriere)", re.IGNORECASE
)


def scrape_generic(company_name: str, careers_url: str) -> list[dict[str, Any]]:
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


_HEADLESS_BUDGET_SECONDS = 900  # 15 minutes reserved for the whole run
_headless_time_used = 0.0
GENERIC_RESULT_SUSPICIOUSLY_LOW = 5  # fewer real-looking links than this -> probably a JS shell, worth a retry


def reset_headless_budget() -> None:
    global _headless_time_used
    _headless_time_used = 0.0


def headless_budget_remaining() -> float:
    return max(0.0, _HEADLESS_BUDGET_SECONDS - _headless_time_used)


def scrape_headless(company_name: str, careers_url: str) -> list[dict[str, Any]]:
    global _headless_time_used

    if headless_budget_remaining() <= 0:
        return []

    start = time.monotonic()
    try:
        from playwright.sync_api import sync_playwright  # imported lazily: only needed if this path is used

        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(user_agent=HEADERS["User-Agent"])
                page.goto(careers_url, timeout=15000, wait_until="domcontentloaded")
                try:
                    page.wait_for_selector("a[href*='/job'], a[href*='/position'], a[href*='/posting']", timeout=5000)
                except Exception:
                    pass
                html = page.content()
            finally:
                browser.close()
    except Exception as exc:
        logger.warning("Headless render failed for %s: %s", company_name, exc)
        _headless_time_used += time.monotonic() - start
        return []
    _headless_time_used += time.monotonic() - start

    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    seen_urls = set()
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        href = a["href"]
        if not text or len(text) < 6 or len(text) > 120:
            continue
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


_PLATFORM_URL_PATTERNS = {
    "greenhouse": re.compile(r"https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([\w-]+)"),
    "lever": re.compile(r"https?://jobs\.lever\.co/([\w-]+)"),
    "personio": re.compile(r"https?://([\w-]+)\.jobs\.personio\.(?:de|com)"),
    "smartrecruiters": re.compile(r"https?://(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)"),
}

_PLATFORM_SCRAPERS = {
    "greenhouse": scrape_greenhouse,
    "lever": scrape_lever,
    "personio": scrape_personio,
    "smartrecruiters": scrape_smartrecruiters,
}

_GENERIC_JOB_BOARD_HINT = re.compile(r"jobs\.|careers\.|/careers|/jobs", re.IGNORECASE)


def discover_real_careers_url(vanity_url: str) -> tuple[str | None, str | None, str | None]:
    try:
        resp = requests.get(vanity_url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException:
        return None, None, None

    for space in (resp.url, resp.text):
        for platform, pattern in _PLATFORM_URL_PATTERNS.items():
            m = pattern.search(space)
            if m:
                return m.group(0), platform, m.group(1)
        wm = _WORKDAY_URL_RE.search(space)
        if wm:
            return wm.group(0), "workday", None

    vanity_domain = urlparse(vanity_url).netloc
    for m in re.finditer(r'href=["\']((https?://)[^"\']+)["\']', resp.text):
        candidate = m.group(1)
        if urlparse(candidate).netloc != vanity_domain and _GENERIC_JOB_BOARD_HINT.search(candidate):
            return candidate, None, None

    return None, None, None


def _recover_thin_result(company_name: str, careers_url: str, current_result: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(current_result) >= GENERIC_RESULT_SUSPICIOUSLY_LOW:
        return current_result

    best_result = current_result
    best_url = careers_url

    discovered_url, platform, token = discover_real_careers_url(careers_url)
    if discovered_url:
        best_url = discovered_url
        if platform in _PLATFORM_SCRAPERS:
            candidate = _PLATFORM_SCRAPERS[platform](company_name, token or "")
        elif platform == "workday":
            candidate = scrape_workday(company_name, discovered_url)
        else:
            candidate = scrape_generic(company_name, discovered_url)
        if candidate:
            best_result = candidate

    if len(best_result) >= GENERIC_RESULT_SUSPICIOUSLY_LOW or headless_budget_remaining() <= 0:
        return best_result

    headless_result = scrape_headless(company_name, best_url)
    return headless_result if headless_result else best_result


_maybe_headless_upgrade = _recover_thin_result


def scrape_company(company: dict[str, Any]) -> list[dict[str, Any]]:
    name = company["name"]
    careers_url = company["careers_url"]
    ats = (company.get("ats") or "auto").lower()
    token = company.get("board_token") or ""

    try:
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
            result = scrape_personio(name, token)
            if result:
                return result
            return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
        if ats == "workday":
            result = scrape_workday(name, careers_url)
            if result:
                return result
            return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
        if ats == "auto":
            for platform, pattern in _PLATFORM_URL_PATTERNS.items():
                match = pattern.search(careers_url)
                if match:
                    result = _PLATFORM_SCRAPERS[platform](name, token or match.group(1))
                    if result:
                        return result
                    return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
            if _WORKDAY_URL_RE.match(careers_url):
                result = scrape_workday(name, careers_url)
                if result:
                    return result
            return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
        return _maybe_headless_upgrade(name, careers_url, scrape_generic(name, careers_url))
    except Exception as exc:  # belt-and-braces: never let one company kill the run
        logger.error("Unhandled error scraping %s: %s", name, exc)
        raise
