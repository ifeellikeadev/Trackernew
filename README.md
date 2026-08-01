# Job Tracker — company career pages only

A personal job scraper that checks two curated company lists directly
on their own career pages — no LinkedIn, StepStone, Indeed, or other
aggregators:
- `config/companies.yaml` (274 companies) — Munich and Zurich.
- `config/dream_cities.yaml` (238 companies) — Copenhagen, Oslo,
  Helsinki, Vienna, Basel, Bern, Geneva, Lausanne, Lucerne, Amsterdam,
  Rotterdam, Vancouver, Perth, Melbourne, Sydney, Singapore — chosen for
  realistic visa feasibility (EU/EEA free movement, Switzerland's
  bilateral agreement, or an established skilled-migration program;
  the US was deliberately left out, no reliable H-1B-lottery-free
  path).

Both feed the same `data/job_tracker.xlsx`, as three separate sheets:
"Jobs," "Dream Cities," and "Global Top Picks" (a wildcard section —
whole approved countries rather than specific cities, score 9-10
only, top 3-4, rebuilt fresh every run rather than accumulated — see
`config/wildcard_countries.yaml` and `SETUP_GUIDE.md` Part 12). No score threshold is applied on either —
every job matching the title + location filter is shown, sorted by
relevance score (highest first), but nothing is excluded for scoring
low.

**If you have zero GitHub experience, start with `SETUP_GUIDE.md`
instead of this file** — it walks through every click.

## How it works

1. Runs only when you trigger it — Actions tab → the workflow → "Run
   workflow." (Automatic scheduling was deliberately turned off; see
   `.github/workflows/daily_scrape.yml` if you want it back on a cron.)
2. `src/main.py` does two passes — one over `config/companies.yaml`,
   one over `config/dream_cities.yaml` — each:
   - fetches current job postings (via `src/ats_scrapers.py`)
   - keeps only postings whose title looks like a PM/Program Manager
     role, AND whose location text explicitly confirms it's actually
     in the target city/metro area (not just wherever the company is
     generally headquartered — see "Tracker columns" below)
   - for postings that matched but came back with no description text
     (some platforms just don't provide it), fetches the job's own
     posting page directly and pulls the text from there
     (`fetch_description_fallback` in `src/ats_scrapers.py`) — this
     can still come up empty for career sites built as JavaScript
     apps, since that's rendered client-side and there's no server-
     side text to read; a real, unavoidable limitation, not a bug to
     chase
   - scores everything that matched against your CV
     (`config/cv_profile.yaml`, via `src/matcher.py`) and adds it all
     to its sheet in `data/job_tracker.xlsx` (`src/tracker.py`)
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
location text explicitly names the target city or its metro area (the
city plus immediate satellite towns — Ottobrunn, Freising, Zug,

Winterthur, and similar; see `src/matcher.py`, `CITY_KEYWORDS`) — not
the whole surrounding state/canton/country. This matters because some
platforms (Lever, Greenhouse, SmartRecruiters, Workday) return a
company's entire global job board in one call, so a Munich-tagged
company can easily have postings in, say, Abu Dhabi or New York
mixed in — those get dropped, along with anything whose location is
too vague to confirm at all (blank, or just "Germany (Remote)").

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
