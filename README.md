# Personal Job Scraper

A personal, single-user job tracker for Data/ML/AI/SWE roles in Singapore. Scrapes listings from
Indeed, LinkedIn, and JobStreet, normalizes them onto one schema, tags each listing via
deterministic regex/FlashText classification, and ranks tracked jobs by fit against your resume.
Keeps everything in a local tracker you browse, filter, and annotate as you apply.

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
- **Tracks** everything in a single local SQLite database you own, browsed through an interactive
  Streamlit dashboard with a search bar, faceted filters, and an editable grid so you can update
  application status directly in the UI.
- **Ranks** tracked jobs by fit against a resume, combining semantic and lexical (BM25) similarity 
  via Reciprocal Rank Fusion — see [Resume matching](#resume-matching) below.

## Project structure

- `src/job_scraper_daily_sql.py` — the daily scrape script. Scrapes both sources, tags/normalizes,
  and upserts into `job_tracker.db` (SQLite, WAL mode) without overwriting existing rows, so
  manual edits (status, notes) made in the dashboard always survive a re-scrape.
- `src/job_viewer_sql.py` — the Streamlit dashboard: search, faceted filters, and an editable
  grid for updating application status directly in the UI.
- `src/migrate_to_sqlite.py` — a one-time, safely re-runnable utility for importing data from an
  older Excel-based tracker. Not needed for a fresh setup.

`classification_benchmark.ipynb` benchmarks the regex/FlashText tagging above against three
zero-shot ML alternatives (two Sentence-Transformers and an NLI model) on a hand-labeled sample of
tracked jobs — see [Known limitations](#known-limitations) for the result.

## Setup

```
pip install -r requirements.txt
```

Create a `.env` file in the project root with an [Apify](https://console.apify.com/) API token:

```
APIFY_TOKEN=your_token_here
```

Run the pipeline once **from the repo root** — scripts reference `.env` and data files as
relative paths that resolve against the current working directory, not the script's own location:

```
python src/job_scraper_daily_sql.py
```

Then browse the results:

```
streamlit run src/job_viewer_sql.py
```

## Resume matching

`resume_match_hybrid.ipynb` ranks jobs currently in the tracker by fit against a resume
(`resume.md`, not included in this repo — point `RESUME_PATH` at your own), combining two
independent signals via Reciprocal Rank Fusion:

- **Semantic** — splits the resume into per-project/per-role chunks and embeds each with a
  long-context model ([`nomic-ai/nomic-embed-text-v1.5`](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5)). 
  A job's score is the average of its top-K best-matching chunks.
- **Lexical** — [`bm25s`](https://github.com/xhluca/bm25s) matches the resume against the job
  description corpus for exact keyword/tool-name overlap that embeddings alone can under-reward.

Read-only — writes a ranked CSV, never modifies `job_tracker.db`. There's no labeled "good fit"
ground truth for this, so the ranking is a relative signal to sanity-check by eye, not a calibrated score.

## Responsible use

This scrapes public job listing pages, which most job boards' Terms of Service prohibit in some
form. It's built and used as a personal, low-volume, manual-cadence tool for my own job search —
not deployed as a public service, and not intended to be run at scale or on other people's
behalf. If you fork this, run it against your own accounts/IPs, at your own low volume, and
understand the ToS of whatever you point it at.

## Known limitations

- Classification is deterministic (regex/FlashText), not ML-based — fast and free, but blind to
  negation and context (e.g. a title containing "Senior Vice President" gets tagged as senior
  engineering seniority). Benchmarked against Sentence-Transformer and NLI zero-shot alternatives
  on a hand-labeled set; the regex/FlashText baseline won on both accuracy and cost, so this
  stays the production approach. A follow-up test swapped in a long-context embedding
  model (`nomic-embed-text-v1.5`, 8,192-token limit) specifically to check whether the original
  Sentence-Transformer model's 256-token limit was truncating job descriptions and unfairly
  hurting its score — it was (93.8% of postings in this dataset exceed 256 tokens) — and fixing
  it did improve the embedding-based method, but not enough to catch up to regex.
- `is_expired`/`applied`/`status`/`notes` are manual-edit fields, not auto-refreshed — an expired
  listing typically just stops appearing in search results rather than reappearing with an
  updated status, so there's usually nothing to auto-detect against.
- No test suite — this is a personal tool, validated empirically during development rather than
  with a formal test harness.

## License

MIT — see [LICENSE](LICENSE).
