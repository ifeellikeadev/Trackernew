# Job Tracker — company career pages only

A personal job scraper that checks a curated list of 223 companies in
Munich and Zurich (config/companies.yaml) directly on their own career
pages — no LinkedIn, StepStone, Indeed, or other aggregators — and
keeps a running Excel tracker (data/job_tracker.xlsx) of Project/Program
Manager roles that look relevant to your CV.

**If you have zero GitHub experience, start with `SETUP_GUIDE.md`
instead of this file** — it walks through every click.

## How it works

1. `.github/workflows/daily_scrape.yml` runs every morning (GitHub's
   servers do this — your computer doesn't need to be on).
2. It runs `src/main.py`, which:
   - loops over every company in `config/companies.yaml`
   - fetches their current job postings (via `src/ats_scrapers.py`)
   - keeps only postings whose title looks like a PM/Program Manager
     role, and scores them for relevance against your CV
     (`config/cv_profile.yaml`, via `src/matcher.py`)
   - adds genuinely new postings to `data/job_tracker.xlsx`, and
     refreshes the "Last Seen" date on ones already there
     (`src/tracker.py`)
3. The workflow commits the updated Excel file back to the repository,
   so it's just sitting there the next time you open it.
4. On the 1st of each month, `.github/workflows/monthly_reset.yml`
   archives the current tracker into `archive/` and starts fresh.

## Files you might want to edit

- `config/companies.yaml` — add/remove companies, or fix a
  `careers_url`/`board_token` if a company isn't returning results.
- `config/cv_profile.yaml` — update if your CV changes, adjust which
  job titles count as "PM roles," or tune `score_ceiling` if the
  relevance scores (shown 1-10 in the tracker) all cluster too high
  or too low.

## Tracker columns

`data/job_tracker.xlsx` has: Job Posted (when the platform provides
it — blank for companies where it isn't available), Company, City,
Job Title, Relevance Score (1-10, 10 = best match to your CV),
German Required (flagged, not filtered), Location, Location
Confirmed, and URL. The sheet is sorted by Relevance Score,
highest first, every run.

**Location Confirmed** exists because some platforms (Lever,
Greenhouse, SmartRecruiters, Workday) return a company's entire
global job board in one call, not just its Munich/Zurich postings.
Rows are dropped entirely if their location text names a place
outside the Munich or Zurich metro area (a real other city — "New
York," "London," "Nuremberg," "Basel," etc.). Metro area means the
city plus its immediate satellite towns (Ottobrunn, Freising, Zug,
Winterthur, and similar — the full list is in `src/matcher.py`,
`CITY_KEYWORDS`), not the whole surrounding state/canton. Rows are
kept but marked "Unconfirmed" if the location text is missing or too
generic to verify (e.g. blank, or just "Germany (Remote)") — worth a
quick manual glance, since it's genuinely unclear whether those are
local.

## Running it yourself, locally (optional)

You don't need to do this — the whole point is that GitHub runs it
for you. But if you want to test a change before it goes live:

```bash
pip install -r requirements.txt
python -m src.main
```

## Known limitation, honestly stated

Company career pages are not standardized. This project handles the
common cases well (Greenhouse, Lever, Personio, SmartRecruiters, and
most Workday sites have clean data feeds behind them), and falls back
to a best-effort HTML scrape for everything else. That fallback will
sometimes miss jobs or find nothing for a given company — see
`SETUP_GUIDE.md` → "Adding or fixing a company" for how to fix a
specific one when you notice it's not working.
