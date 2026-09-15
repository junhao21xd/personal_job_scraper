# Personal Job Scraper

A personal, single-user job tracker for Data/ML/AI/SWE roles in Singapore. Scrapes listings from
Indeed, LinkedIn, and JobStreet, normalizes them onto one schema, tags them with deterministic
NLP (regex + FlashText — no LLM calls in the pipeline), and keeps a local tracker you can browse,
filter, and annotate as you apply.

Built as a personal tool for my own job search, not a hosted scraping service — see
[Responsible use](#responsible-use) below.

## What it does

- **Ingests** from two structurally different sources per search term: [`python-jobspy`](https://github.com/speedyapply/JobSpy)
  for Indeed + LinkedIn, and a custom [Apify](https://apify.com) actor
  (`blackfalcondata/jobstreet-scraper`) for JobStreet SG.
- **Normalizes** both onto a shared schema and deduplicates on each platform's native job ID
  (not URL — job board URLs carry volatile tracking params that break naive URL-based dedup).
- **Classifies** each listing via regex/FlashText: minimum years of experience, seniority
  (title-based), visa/work-pass eligibility, and five tech-category tags (agentic tooling,
  AI/LLM, data engineering, data science, software engineering).
- **Tracks** everything in a single local store you own — either an Excel workbook or a SQLite
  database, browsed through an interactive Streamlit dashboard with a search bar, faceted
  filters, and (in the SQLite version) an editable grid so you can update application status
  directly in the UI.

## Project structure

Two parallel implementations of the same pipeline exist side by side:

| | Excel-based (original) | SQLite-based (current) |
|---|---|---|
| Daily scrape script | `job_scraper_daily.py` | `job_scraper_daily_sql.py` |
| Dashboard | `job_viewer.py` | `job_viewer_sql.py` |
| Storage | `my_job_tracker.xlsx` + two raw per-source `.xlsx` dumps | `job_tracker.db` (one file, three tables) |

The SQLite version exists because the Excel version has no safe way to edit tracker data from
the dashboard — Excel's exclusive file lock makes concurrent read/write from more than one
process fragile. SQLite in WAL mode allows the daily scrape and the dashboard to read/write
concurrently without that problem, and the dashboard gained an editable grid as a result.
`migrate_to_sqlite.py` is a one-time (safely re-runnable) script that loads the Excel version's
data into the SQLite version.

`Job_scraper.ipynb` is the original exploratory notebook — both sources, the merge, tagging, and
the Excel writes, all in one place. It's the easiest way to see the whole pipeline end to end,
but each run overwrites its output rather than appending, unlike the two daily scripts above.

## Setup

```
pip install -r requirements.txt
```

Create a `.env` file in the project root with an [Apify](https://console.apify.com/) API token:

```
APIFY_TOKEN=your_token_here
```

Run the pipeline once (either version):

```
python job_scraper_daily_sql.py   # SQLite version
python job_scraper_daily.py       # Excel version
```

Then browse the results:

```
streamlit run job_viewer_sql.py   # SQLite version, editable
streamlit run job_viewer.py       # Excel version, read-only
```

## Responsible use

This scrapes public job listing pages, which most job boards' Terms of Service prohibit in some
form. It's built and used as a personal, low-volume, manual-cadence tool for my own job search —
not deployed as a public service, and not intended to be run at scale or on other people's
behalf. If you fork this, run it against your own accounts/IPs, at your own low volume, and
understand the ToS of whatever you point it at.

## Known limitations

- Classification is deterministic (regex/FlashText), not ML-based — fast and free, but blind to
  negation and context (e.g. a title containing "Senior Vice President" gets tagged as senior
  engineering seniority).
- `is_expired`/`applied`/`status`/`notes` are manual-edit fields, not auto-refreshed — an expired
  listing typically just stops appearing in search results rather than reappearing with an
  updated status, so there's usually nothing to auto-detect against.
- No test suite — this is a personal tool, validated empirically during development rather than
  with a formal test harness.

## License

MIT — see [LICENSE](LICENSE).
