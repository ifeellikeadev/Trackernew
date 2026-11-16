# Job scraper expansion pack

## New capabilities
- 18 additional target employers across defence, aerospace, energy, grid and transformation.
- Recruitee and Workable public adapters.
- Priority-based schedule: tier 1 daily, tier 2 Tuesday/Friday, tier 3 Sunday.
- Per-source budgets of 90/45/25 seconds.
- `enabled`, `priority`, `sector`, `expected_locations`, `max_pages`, `detail_fetch_mode`, and `verified_on` metadata.
- Expanded PM, programme, delivery, PMO and transformation title vocabulary.
- Company onboarding utility with ATS detection and extraction-quality gate.

## Run modes
- Normal scheduled run: `python main.py`
- Daily priority only: `SCRAPE_TIER=daily python main.py`
- Priority 1 and 2: `SCRAPE_TIER=2 python main.py`
- Complete run: `SCRAPE_TIER=all python main.py`

## Onboard another employer
`python onboard_company.py "Company" "https://company.example/careers" --priority 1 --sector energy`

The utility exits with code 2 unless the test returns jobs and at least 95% have a usable title and URL. A zero result remains unverified, not proof of no vacancies.
