# Job scraper: corrected expansion package

This replaces the earlier flat ZIP. It contains the supplied scraper with targeted fixes,
318 configured employers (299 existing plus 19 expansion candidates), regression tests,
and a real `src/`, `config/`, `data/`, `docs/` layout.

**Configured is not verified.** No employer is marked newly verified in this release.
The 19 new career URLs returned HTTP 403 from this execution environment. That does not
establish whether the denial came from the destination or the environment. Their URLs,
ATS choices and extraction still require validation from your normal runner.
See `LIVE_CHECKS.json` for the exact reachability results.

## Quick start

Python 3.10 or newer is required. From the extracted package root:

    python -m venv .venv

Activate the environment using the normal command for your operating system, then:

    python -m pip install -r requirements.txt
    python run.py --check-config
    python -m unittest discover -s tests -v
    python run.py --companies "Helsing"

The last command makes live requests. To attempt every enabled employer:

    python run.py

A full run retains the original 180-second per-source budget and can take hours.
Existing source exclusions, including the Munich scope and named recruiter exclusions,
remain in place. Not every configured source is necessarily attempted or usable.

For JavaScript-rendered sources, optional browser support requires:

    python -m pip install -r requirements-browser.txt
    python -m playwright install chromium

On Linux, Playwright may additionally require its documented operating-system dependencies.
There is no login, stealth mode or access-control bypass. Access denials are reported.

## Updating your existing GitHub repository

1. Make a branch or backup first.
2. Copy `src/`, `run.py`, the requirements files and `tests/` into the matching locations.
3. Merge `config/companies.yaml` and `config/cv_profile.yaml` with any changes you made after uploading them.
4. **Keep your existing `config/job_boards.yaml`.** The supplied file is an empty fresh-install template.
5. **Keep your existing tracker, generated webpage, workflows and historical data.** No tracker or production webpage is bundled.
6. Existing commands such as `python -m src.main` remain supported. The new onboarding command is `python -m src.onboard_company`.
7. Run the configuration check and tests, then a selected-company live run before enabling the full run.

## Run modes

- `python run.py`: all enabled, non-excluded companies on every invocation. No automatic weekly reduction.
- `python run.py --tier daily`: priority 1 only.
- `python run.py --tier 2`: priority 1 and 2.
- `python run.py --tier scheduled`: weekdays and budgets from `scrape_schedule` in the YAML.
- `python run.py --companies "Helsing" "BayWa r.e."`: restrict to exact company names.

The existing `SCRAPE_TIER` and new `SCRAPE_COMPANIES` environment variables are also supported.
`--tier` overrides `SCRAPE_TIER`. In scheduled mode, weekdays use Europe/Berlin time,
with 0 = Monday and 6 = Sunday. Full-run budget is `full_run_source_seconds` (180 by default).
A per-company `source_seconds` overrides either mode.

## Implemented fixes

- Correct package paths and imports; required dependencies include the Personio XML parser.
- Unknown ATS detection retains the original careers URL rather than replacing it with null.
- False verification dates removed. A date is written only after successful live quality acceptance.
- YAML schedule and budget values are consumed by the pipeline; tiered scheduling is opt-in.
- `detail_fetch_mode` works: `matched_only`, `all` (non-outside candidates), or `none`.
- `max_pages` is applied to HTML/browser, Workday, SmartRecruiters, Eightfold, Amazon and Workable pagination.
  Single-feed adapters do not need a page limit. Existing employers retain their original default caps.
- `expected_locations` is a diagnostic count of explicit source locations, never proof of a job's location.
- Recruitee handles multiple locations and preserves feed counts.
- Workable uses the public careers POST search with cursor pagination, repeated-page detection,
  rate-limit stop, page-cap reporting and public job-detail enrichment.
- Greenhouse, Lever, Personio and Ashby successful feeds expose explicit count/coverage metadata.
- A separate review queue is actually generated. It does not bypass the main tracker's filters.
- Source-quality validation and gated, backed-up configuration promotion are implemented.

## Review queue

`data/review_candidates.csv` is generated for the latest run. It contains unusual-title jobs
with strong **description-only** keyword evidence, excluding known outside locations,
excluded titles, junior titles and rejected dates. Unknown locations are labelled for review.
Jobs are not automatically added to the main tracker. The queue is replaced on each run,
so a selected-company run produces a selected-company review queue.

Configure `review_candidates` in the CV profile. Default description score threshold is 7/10.
This is keyword scoring, not a prediction of suitability or hiring probability.

## Validate or onboard sources

Test an existing employer using its configured ATS and token:

    python -m src.validate_sources --company "Helsing"

Test all expansion candidates:

    python -m src.validate_sources --expansion

Test a new employer:

    python -m src.onboard_company "Company name" "https://company.example/careers" --priority 1 --sector energy

Add `--promote` to either command only when you want to update `config/companies.yaml`.
Promotion requires all of:

- at least one extracted vacancy;
- at least 95% usable titles and vacancy URLs;
- at least 80% explicit locations;
- a recognised complete-feed/pagination stop, with no reported count gap;
- no exhausted source deadline.

Zero results and unknown pagination do not pass. This is intentionally conservative:
a source can return useful jobs without being eligible for automatic promotion.
Failed validation exits with code 2. No production config is changed without `--promote`.
Successful promotion makes a `.yaml.bak` backup and atomically replaces the configuration.
Validation writes `docs/source_validation.json`; normal scraping writes `docs/scraper_health.json`.
The legacy `python -m src.validate_urls` remains a URL-only check, not an extraction-quality check.

## Validation performed for this release

See `TEST_RESULTS.txt` for the exact executed tests. The suite covers imports/configuration,
URL fallback, promotion safeguards, schedules, page limits, cursor pagination, rate-limit handling,
location quality, review output, and a mocked-network integration run that exercises real
filtering, scoring, tracker writing and webpage generation in a temporary directory.

Test vacancies are synthetic fixtures, not real job listings. No synthetic vacancy data is
included in production outputs. The test suite blocks accidental real network calls.
Live checks are limited to HTTP reachability of the 19 added URLs; all returned 403.
No full live scrape or end-to-end verification of all 318 employers is claimed.

## API implementation references

- Recruitee public offers reference: https://docs.recruitee.com/reference/offers
- Workable public careers cursor behaviour (community reference): https://github.com/arabinowe/job-board-apis/blob/main/workable-jobs-api.md

The public Workable careers endpoint is distinct from its authenticated HR API. Public
endpoints can change; unsuccessful responses must remain failures rather than fabricated jobs.
