# Job Tracker — company career pages only

A personal job scraper that checks two curated company lists directly
on their own career pages — no LinkedIn, StepStone, Indeed, or other
aggregators:
- `config/companies.yaml` (537 companies) — Munich, Zurich, Singapore,
  and Basel/Bern/Geneva/Lausanne/Lucerne (every major Swiss city
  except Lugano). Singapore and those Swiss cities were promoted from
  Dream Cities to full main-list status with much larger, dedicated
  company lists, since they're genuine relocation options, not just
  aspirational ones.
- `config/dream_cities.yaml` (198 companies) — Copenhagen, Oslo,
  Helsinki, Vienna, Berlin, Amsterdam, Rotterdam, Vancouver, Perth,
  Melbourne, Sydney — chosen for realistic visa feasibility (EU/EEA
  free movement, or an established skilled-migration program; the US
  was deliberately left out, no reliable H-1B-lottery-free path).

Both feed the same `data/job_tracker.xlsx`, as two separate sheets:
"Jobs" and "Dream Cities." No score threshold is applied on either —
every job matching the title + location filter is shown, sorted by
relevance score (highest first), but nothing is excluded for scoring
low.

**If you have zero GitHub experience, start with `SETUP_GUIDE.md`
instead of this file** — it walks through every click.

## How it works

1. Runs only when you trigger it — Actions tab → the workflow → "Run
   workflow." (Automatic scheduling was deliberately turned off; see
   `.github/workflows/daily_scrape.yml` if you want it back on a cron.)
2. `src/main.py` does one combined pass over every company in both
   `config/companies.yaml` and `config/dream_cities.yaml`:
   - fetches current job postings (via `src/ats_scrapers.py`) —
     including a last-resort real-browser render for companies whose
     career page is a JavaScript app the fast path can't see into (see
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
     the "Jobs" sheet (Munich/Zurich/Singapore/Swiss cities) or "Dream
     Cities" sheet, based on which city actually matched
     (`src/tracker.py`)
3. The workflow commits the updated Excel file (and the generated
   `docs/index.html` webpage) back to the repository.
4. The monthly reset workflow (also manual-trigger-only) archives the
   current tracker (both sheets) into `archive/` and starts fresh.

## Files you might want to edit

- `config/companies.yaml` / `config/dream_cities.yaml` — add/remove
  companies, or fix a `careers_url`/`board_token` if one isn't
  returning results.
- `config/cv_profile.yaml` — update if your CV changes, adjust which
  job titles count as "PM roles," turn a score floor back on via
  `main_min_score` / `dream_city_min_score` (both 0 = off by default),
  or tune `score_ceiling` if relevance scores cluster too high/low.

## Tracker columns

Each sheet has: Job Posted (when the platform provides it — blank for
companies where it isn't available), Company, City (+ Country on the
Dream Cities sheet), Job Title, Relevance Score (1-10, 10 = best match
to your CV), Location, and URL. Sorted by Relevance Score, highest
first, every run.

**Location filtering** is strict: a job only survives if its own
location text explicitly names an approved city or its metro area
(the city plus immediate satellite towns — Ottobrunn, Freising, Zug,
Winterthur, and similar; see `src/matcher.py`, `CITY_KEYWORDS`) — not
the whole surrounding state/canton/country. This matters because some
platforms (Lever, Greenhouse, SmartRecruiters, Workday) return a
company's entire global job board in one call, so a Munich-tagged
company can easily have postings in, say, Abu Dhabi or New York
mixed in — those get dropped, along with anything whose location is
too vague to confirm at all (blank, or just "Germany (Remote)").
Every company is checked against every approved city, not just
whichever one it happens to be tagged with — see `src/matcher.py`,
`filter_by_title_and_any_city`.

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
most Workday sites have clean data feeds behind them), falls back to
a best-effort HTML scrape for everything else, and as a last resort
retries with a real headless browser when that HTML scrape looks
suspiciously thin (see `SETUP_GUIDE.md` Part 9). That still won't
catch everything — see `SETUP_GUIDE.md` → "Adding or fixing a
company" for how to fix a specific one when you notice it's not
working.
