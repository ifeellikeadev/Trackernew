
from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.job_scope import excluded_source
from src.ats_scrapers import discover_real_careers_url

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
    name = company["name"]
    city = company.get("city", "")
    url = company.get("careers_url", "")

    if not url:
        return {"name": name, "city": city, "url": url, "status": "NO_URL", "detail": "no careers_url set", "suggested_fix": None}

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    except requests.exceptions.SSLError as exc:
        return {"name": name, "city": city, "url": url, "status": "SSL_ERROR", "detail": str(exc)[:150], "suggested_fix": None}
    except requests.exceptions.ConnectionError as exc:
        detail = "DNS resolution failed / connection refused" if "NameResolutionError" in str(exc) else str(exc)[:150]
        return {"name": name, "city": city, "url": url, "status": "CONNECTION_ERROR", "detail": detail, "suggested_fix": None}
    except requests.exceptions.Timeout:
        return {"name": name, "city": city, "url": url, "status": "TIMEOUT", "detail": f"no response within {TIMEOUT}s", "suggested_fix": None}
    except requests.exceptions.RequestException as exc:
        return {"name": name, "city": city, "url": url, "status": "OTHER_ERROR", "detail": str(exc)[:150], "suggested_fix": None}

    final_domain = urlparse(resp.url).netloc
    original_domain = urlparse(url).netloc
    redirect_note = f" (redirected to {final_domain})" if final_domain != original_domain else ""

    if resp.status_code == 200:
        return {"name": name, "city": city, "url": url, "status": "OK", "detail": f"200{redirect_note}", "suggested_fix": None}

    suggested_fix = None
    try:
        discovered_url, platform, _token = discover_real_careers_url(url)
        if discovered_url and discovered_url != url:
            suggested_fix = f"{discovered_url}" + (f" (platform: {platform})" if platform else "")
    except Exception:
        pass  # discovery is best-effort here; a failure just means no suggestion, not a crash

    if resp.status_code in (403, 999):
        return {"name": name, "city": city, "url": url, "status": "BLOCKED", "detail": f"{resp.status_code}{redirect_note} — may just be anti-bot, not necessarily wrong", "suggested_fix": suggested_fix}
    elif resp.status_code == 404:
        return {"name": name, "city": city, "url": url, "status": "NOT_FOUND", "detail": f"404{redirect_note}", "suggested_fix": suggested_fix}
    else:
        return {"name": name, "city": city, "url": url, "status": "HTTP_ERROR", "detail": f"{resp.status_code}{redirect_note}", "suggested_fix": suggested_fix}


def run() -> None:
    companies = load_yaml(COMPANIES_FILE) + load_yaml(JOB_BOARDS_FILE)
    companies = [c for c in companies if not excluded_source(c)]
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

    no_page_at_all = by_status.get("CONNECTION_ERROR", []) + by_status.get("NO_URL", [])
    other_broken = by_status.get("NOT_FOUND", []) + by_status.get("HTTP_ERROR", []) + by_status.get("TIMEOUT", []) + by_status.get("SSL_ERROR", []) + by_status.get("OTHER_ERROR", [])

    has_fix = [r for r in other_broken if r.get("suggested_fix")]
    needs_research = [r for r in other_broken if not r.get("suggested_fix")]

    if has_fix:
        print(f"\n{'=' * 70}")
        print(f"BROKEN WITH A SUGGESTED FIX ({len(has_fix)}) — discovery found a real link on the page:")
        for r in has_fix:
            print(f"  {r['name']} ({r['city']}): {r['url']}  [{r['status']}]")
            print(f"      -> suggested: {r['suggested_fix']}")

    if needs_research or no_page_at_all:
        combined = no_page_at_all + needs_research
        print(f"\n{'=' * 70}")
        print(f"BROKEN, NEEDS MANUAL RESEARCH ({len(combined)}) — no page to auto-discover from")
        print("(domain doesn't resolve at all, or nothing useful was found on the response):")
        for r in combined:
            print(f"  {r['name']} ({r['city']}): {r['url']}  [{r['status']}: {r['detail']}]")

    blocked = by_status.get("BLOCKED", [])
    if blocked:
        print(f"\n{'=' * 70}")
        print(f"BLOCKED ({len(blocked)}) — likely just anti-bot, probably fine (the real scraper uses a")
        print("real browser User-Agent and headless rendering as a last resort — check an actual")
        print("scrape run's log before assuming these are wrong):")
        for r in blocked:
            fix_note = f"  -> possible fix: {r['suggested_fix']}" if r.get("suggested_fix") else ""
            print(f"  {r['name']} ({r['city']}): {r['url']}{fix_note}")


if __name__ == "__main__":
    run()
