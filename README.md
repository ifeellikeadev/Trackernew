# Job Tracker — company career pages only

A personal job scraper that checks two curated lists directly on
their own career pages — no LinkedIn, StepStone, Indeed, or other
aggregators:
- `config/companies.yaml` (590 companies) — Munich, Zurich, and
  Basel/Bern/Geneva/Lausanne/Lucerne (every major Swiss city except
  Lugano). Deliberately refocused to just these — Singapore and every
  Dream City were dropped per an explicit scope decision to focus on
  Munich and Swiss cities/nearby areas only.
- `config/job_boards.yaml` (7 entries) — a handful of staffing-agency
  search pages (Randstad, Michael Page, Robert Walters, EMEA
  Recruitment — Switzerland/Germany only), kept deliberately smaller
  and lower priority than the direct-company list.

Both feed the same `data/job_tracker.xlsx`, as two separate sheets:
"Jobs" (Munich, Zurich) and "Swiss Cities" (Basel, Bern, Geneva,
Lausanne, Lucerne) — kept separate so Munich/Zurich's much higher
posting volume doesn't bury the Swiss cities' genuinely fewer matches.
A minimum relevance score of 3 applies to both, pruned retroactively
every run (see `config/cv_profile.yaml`, `main_min_score` /
`swiss_min_score`).

(`config/dream_cities.yaml` still exists on disk but is no longer
loaded by `src/main.py` — kept in case this scope decision gets
reversed later, rather than deleted outright. If your tracker file
still has an old "Dream Cities" or "Singapore & Swiss Cities" sheet
with real historical data, it's left untouched, just no longer
updated — delete it by hand in Excel if you want it gone.)

**If you have zero GitHub experience, start with `SETUP_GUIDE.md`
instead of this file** — it walks through every click.

## How it works

1. Runs automatically once a day (06:30 Munich time — see
   `.github/workflows/daily_scrape.yml` for the exact cron and its
   daylight-saving caveat), plus a manual "Run workflow" button on the
   Actions tab any time you want an extra check.
2. `src/main.py` does one combined pass over every company in both
   `config/companies.yaml` and `config/job_boards.yaml`:
   - fetches current job postings (via `src/ats_scrapers.py`) —
     including a generalized real-vs-vanity-URL discovery step and a
     last-resort real-browser render for companies whose career page
     is a JavaScript app the fast path can't see into (see
     `SETUP_GUIDE.md` Part 9)
   - keeps only postings whose title looks like a PM/Program Manager
     role, AND whose location text explicitly confirms it's in ANY
     approved city — not just whichever single city that company's
     config entry happens to be tagged with (see "Tracker columns"
     below)
   - for postings that matched but came back with no description text
     (some platforms just don't provide it), fetches the job's own
     posting page directly and pulls the text from there
     (`fetch_description_fallback` in `src/ats_scrapers.py`)
   - scores everything that matched against your CV
     (`config/cv_profile.yaml`, via `src/matcher.py`) and routes it to
     the "Jobs" sheet (Munich/Zurich) or "Swiss Cities" sheet, based
     on which city actually matched (`src/tracker.py`)
3. The workflow commits the updated Excel file (and the generated
   `docs/index.html` webpage) back to the repository.
4. The monthly reset workflow (also manual-trigger-only) archives the
   current tracker (both sheets) into `archive/` and starts fresh.

## Files you might want to edit

- `config/companies.yaml` / `config/job_boards.yaml` — add/remove
  companies, or fix a `careers_url`/`board_token` if one isn't
  returning results.
- `config/cv_profile.yaml` — update if your CV changes, adjust which
  job titles count as "PM roles," tune `main_min_score` /
  `swiss_min_score` (the minimum relevance score floor, both 3 by
  default), or tune `score_ceiling` if relevance scores cluster too
  high/low.

## Tracker columns

Each sheet has: Job Posted (when the platform provides it — blank for
companies where it isn't available), Company, City, Job Title,
Relevance Score (1-10, 10 = best match to your CV), Location, and URL.
Sorted by Relevance Score, highest first, every run.

**Location filtering** is strict: a job only survives if its own
location text explicitly names an approved city or its metro area
(the city plus immediate satellite towns — Ottobrunn, Freising, Zug,
Winterthur, Ebikon, Thun, and similar; see `src/matcher.py`,
`CITY_KEYWORDS`) — not the whole surrounding state/canton/country.
This matters because some platforms (Lever, Greenhouse,
SmartRecruiters, Workday) return a company's entire global job board
in one call, so a Munich-tagged company can easily have postings in,
say, Abu Dhabi or New York mixed in — those get dropped, along with
anything whose location is too vague to confirm at all (blank, or
just "Germany (Remote)"). Every company is checked against every
approved city, not just whichever one it happens to be tagged with —
see `src/matcher.py`, `filter_by_title_and_any_city`.

## Running it yourself, locally (optional)

You don't need to do this — the whole point is that GitHub runs it
for you. But if you want to test a change before it goes live:

```bash
pip install -r requirements.txt
python -m src.main
```

## Known limitation, honestly stated

Company career pages are not standardized. This project handles the
common cases well (Greenhouse, Lever, Personio — both `.de` and
`.com`, SmartRecruiters, and most Workday sites have clean data feeds
behind them), falls back to a best-effort HTML scrape for everything
else, and as a last resort retries with a real headless browser when
that HTML scrape looks suspiciously thin (see `SETUP_GUIDE.md` Part
9). A recurring real problem — a company's config entry pointing at
its marketing homepage instead of its actual job board (found by hand
multiple times: Sixt, Jet Aviation, Hensoldt) — now has a general
automated recovery step too (`discover_real_careers_url` in
`src/ats_scrapers.py`). None of this catches everything — see
`SETUP_GUIDE.md` → "Adding or fixing a company" for how to fix a
specific one when you notice it's not working.
