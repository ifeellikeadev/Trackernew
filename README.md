# Job Tracker — company career pages only

A personal job scraper that checks two curated company lists directly
on their own career pages — no LinkedIn, StepStone, Indeed, or other
aggregators:
- `config/companies.yaml` (274 companies) — Munich and Zurich, any
  score at or above `main_min_score`.
- `config/dream_cities.yaml` (211 companies) — Copenhagen, Oslo,
  Helsinki, Vienna, Basel, Bern, Geneva, Lausanne, Lucerne, Vancouver,
  Perth, Melbourne, Sydney, Singapore — chosen for realistic visa
  feasibility (EU/EEA free movement, Switzerland's bilateral
  agreement, or an established skilled-migration program; the US was
  deliberately left out, no reliable H-1B-lottery-free path) — score
  at or above `dream_city_min_score` only.

Both feed the same `data/job_tracker.xlsx`, as two separate sheets:
"Jobs" and "Dream Cities."

**If you have zero GitHub experience, start with `SETUP_GUIDE.md`
instead of this file** — it walks through every click.

## How it works

1. `.github/workflows/daily_scrape.yml` runs every morning (GitHub's
   servers do this — your computer doesn't need to be on).
2. It runs `src/main.py`, which does two passes — one over
   `config/companies.yaml`, one over `config/dream_cities.yaml` — each:
   - fetches current job postings (via `src/ats_scrapers.py`)
   - keeps only postings whose title looks like a PM/Program Manager
     role, AND whose location text explicitly confirms it's actually
     in the target city/metro area (not just wherever the company is
     generally headquartered — see "Tracker columns" below)
   - scores what's left for relevance against your CV
     (`config/cv_profile.yaml`, via `src/matcher.py`)
   - adds anything scoring at or above that list's threshold to its
     sheet in `data/job_tracker.xlsx` (`src/tracker.py`) — existing
     rows that have since dropped below threshold get pruned too,
     not just new ones blocked
3. The workflow commits the updated Excel file (and the generated
   `docs/index.html` webpage) back to the repository.
4. On the 1st of each month, `.github/workflows/monthly_reset.yml`
   archives the current tracker (both sheets) into `archive/` and
   starts fresh.

## Files you might want to edit

- `config/companies.yaml` / `config/dream_cities.yaml` — add/remove
  companies, or fix a `careers_url`/`board_token` if one isn't
  returning results.
- `config/cv_profile.yaml` — update if your CV changes, adjust which
  job titles count as "PM roles," tune `main_min_score` /
  `dream_city_min_score` (the score floor for each sheet), or
  `score_ceiling` if relevance scores all cluster too high or too low.

## Tracker columns

Each sheet has: Job Posted (when the platform provides it — blank for
companies where it isn't available), Company, City (+ Country on the
Dream Cities sheet), Job Title, Relevance Score (1-10, 10 = best match
to your CV), German Required (flagged, not filtered), Location, and
URL. Sorted by Relevance Score, highest first, every run.

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
