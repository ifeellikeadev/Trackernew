"""
A fast, lightweight health check for every company's careers_url —
answers "does this URL even respond?" without doing any of the actual
scraping/matching work src/main.py does. Where main.py takes 60-90
minutes (fetching every posting, enriching descriptions, scoring),
this takes a few minutes, since it's just one HEAD/GET request per
company with a short timeout.

Exists because of a real, recurring pattern found by hand throughout
this project (Sixt, Jet Aviation, Hensoldt, and — at real scale — 35
companies with an auto-guessed, never-real Personio URL): a config
entry silently pointing at the wrong page. Rather than only catching
these one at a time when a specific missing posting gets noticed, this
gives a full picture in one pass.

Run manually with:
    python -m src.validate_urls
Or via the GitHub Actions workflow (.github/workflows/validate_urls.yml,
manual-trigger only — this is a diagnostic tool, not part of the daily
scrape).

IMPORTANT — what this does and doesn't tell you:
  - A non-200 status, a connection error, or a DNS failure is a strong
    signal something is genuinely wrong (see discover_real_careers_url
    in ats_scrapers.py for the automated fix path once you know which
    ones to look at).
  - A 200 response does NOT guarantee the page actually has real job
    postings on it — some legitimate career pages 403/anti-bot a
    plain request like this one even though src/main.py's full
    scraper (which uses a real browser User-Agent, and as a last
    resort a real headless browser) can still get through. Treat a
    non-200 result here as "worth checking," not as an automatic
    verdict — cross-reference with an actual scrape run's OK/EMPTY/
    FAILED lines before concluding a URL is truly broken.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = ROOT / "config" / "companies.yaml"
JOB_BOARDS_FILE = ROOT / "config" / "job_boards.yaml"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PersonalJobTracker/1.0; "
        "+https://github.com/) research/personal-use job search bot"
    )
}
TIMEOUT = 8


def load_yaml(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("companies", [])


def check_one(company: dict) -> dict:
    """Returns a result dict: {name, city, url, status, detail}."""
    name = company["name"]
    city = company.get("city", "")
    url = company.get("careers_url", "")

    if not url:
        return {"name": name, "city": city, "url": url, "status": "NO_URL", "detail": "no careers_url set"}

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    except requests.exceptions.SSLError as exc:
        return {"name": name, "city": city, "url": url, "status": "SSL_ERROR", "detail": str(exc)[:150]}
    except requests.exceptions.ConnectionError as exc:
        # This is where a wrong/nonexistent domain shows up (DNS failure)
        detail = "DNS resolution failed / connection refused" if "NameResolutionError" in str(exc) else str(exc)[:150]
        return {"name": name, "city": city, "url": url, "status": "CONNECTION_ERROR", "detail": detail}
    except requests.exceptions.Timeout:
        return {"name": name, "city": city, "url": url, "status": "TIMEOUT", "detail": f"no response within {TIMEOUT}s"}
    except requests.exceptions.RequestException as exc:
        return {"name": name, "city": city, "url": url, "status": "OTHER_ERROR", "detail": str(exc)[:150]}

    final_domain = urlparse(resp.url).netloc
    original_domain = urlparse(url).netloc
    redirect_note = f" (redirected to {final_domain})" if final_domain != original_domain else ""

    if resp.status_code == 200:
        return {"name": name, "city": city, "url": url, "status": "OK", "detail": f"200{redirect_note}"}
    elif resp.status_code in (403, 999):
        # Common anti-bot response to a plain request — worth knowing
        # about, but NOT necessarily broken (see module docstring).
        return {"name": name, "city": city, "url": url, "status": "BLOCKED", "detail": f"{resp.status_code}{redirect_note} — may just be anti-bot, not necessarily wrong"}
    elif resp.status_code == 404:
        return {"name": name, "city": city, "url": url, "status": "NOT_FOUND", "detail": f"404{redirect_note}"}
    else:
        return {"name": name, "city": city, "url": url, "status": "HTTP_ERROR", "detail": f"{resp.status_code}{redirect_note}"}


def run() -> None:
    companies = load_yaml(COMPANIES_FILE) + load_yaml(JOB_BOARDS_FILE)
    print(f"Checking {len(companies)} companies...\n")

    results = []
    for i, company in enumerate(companies, 1):
        result = check_one(company)
        results.append(result)
        print(f"[{i}/{len(companies)}] {result['status']:18} {result['name']}")
        time.sleep(0.1)  # be polite

    print("\n" + "=" * 70)
    by_status = {}
    for r in results:
        by_status.setdefault(r["status"], []).append(r)

    print("SUMMARY:")
    for status in ["OK", "BLOCKED", "NOT_FOUND", "CONNECTION_ERROR", "TIMEOUT", "SSL_ERROR", "HTTP_ERROR", "OTHER_ERROR", "NO_URL"]:
        if status in by_status:
            print(f"  {status}: {len(by_status[status])}")

    # The genuinely actionable ones — likely real, wrong URLs.
    real_problems = by_status.get("CONNECTION_ERROR", []) + by_status.get("NOT_FOUND", []) + by_status.get("NO_URL", [])
    if real_problems:
        print(f"\n{'=' * 70}")
        print(f"LIKELY GENUINELY BROKEN ({len(real_problems)}) — worth fixing:")
        for r in real_problems:
            print(f"  {r['name']} ({r['city']}): {r['url']}  [{r['detail']}]")

    blocked = by_status.get("BLOCKED", [])
    if blocked:
        print(f"\n{'=' * 70}")
        print(f"BLOCKED ({len(blocked)}) — likely just anti-bot, probably fine (the real scraper uses a")
        print("real browser User-Agent and headless rendering as a last resort — check an actual")
        print("scrape run's log before assuming these are wrong):")
        for r in blocked:
            print(f"  {r['name']} ({r['city']}): {r['url']}")


if __name__ == "__main__":
    run()
