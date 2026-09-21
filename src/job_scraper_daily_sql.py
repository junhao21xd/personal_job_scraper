import os
import re
import json
import sqlite3
import pandas as pd
from datetime import datetime
from jobspy import scrape_jobs
from apify_client import ApifyClient
from flashtext import KeywordProcessor
from dotenv import load_dotenv

load_dotenv()
APIFY_TOKEN = os.getenv("APIFY_TOKEN")
DB_PATH = "job_tracker.db"

SEARCH_TERMS = ["data engineer", "data scientist", "machine learning engineer", "data analyst", "AI engineer"]
JOBSPY_RESULTS_PER_TERM = 3   # ~30 per site across all terms — daily cadence, not the day-1 volume
APIFY_MAX_RESULTS = 15        # total cap across all terms (actor dedupes internally)

KEEP_COLS = [
    'job_id', 'title', 'company', 'location', 'source', 'country',
    'salary_str', 'min_amount', 'max_amount', 'currency', 'interval',
    'work_type', 'work_arrangement', 'is_expired',
    'company_url', 'company_industry',
    'emails',
    'job_url', 'job_url_direct',
    'description', 'jobstreet_job_score',
    'date_posted', 'min_years_exp', 'seniority', 'visa_eligibility',
    'is_agent', 'is_ai_llm', 'is_de', 'is_ds', 'is_swe',
]
WORKFLOW_COLS = ['applied', 'status', 'notes', 'date_scraped']
BOOL_COLS = ['is_expired', 'applied', 'is_agent', 'is_ai_llm', 'is_de', 'is_ds', 'is_swe']

JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id              TEXT PRIMARY KEY,
    title               TEXT,
    company             TEXT,
    location            TEXT,
    source              TEXT,
    country             TEXT,
    salary_str          TEXT,
    min_amount          REAL,
    max_amount          REAL,
    currency            TEXT,
    interval            TEXT,
    work_type           TEXT,
    work_arrangement    TEXT,
    is_expired          INTEGER,
    company_url         TEXT,
    company_industry    TEXT,
    emails              TEXT,
    job_url             TEXT,
    job_url_direct      TEXT,
    description          TEXT,
    jobstreet_job_score  REAL,
    date_posted          TEXT,
    min_years_exp        REAL,
    seniority             TEXT,
    visa_eligibility      TEXT,
    is_agent              INTEGER,
    is_ai_llm             INTEGER,
    is_de                 INTEGER,
    is_ds                 INTEGER,
    is_swe                INTEGER,
    applied               INTEGER,
    status                TEXT,
    notes                 TEXT,
    date_scraped          TEXT
)
"""


# --- Shared helpers (mirrors Job_scraper.ipynb / job_scraper_daily.py) ---

def extract_years_exp(description):
    if not isinstance(description, str): return None
    sentences = re.split(r'[\n\.]', description)
    for sentence in sentences:
        if 'year' in sentence.lower() and ('experience' in sentence.lower() or 'required' in sentence.lower()):
            match = re.search(r'(\d+)\s*(?:-\s*\d+)?\s*\+?\s*years?', sentence, re.IGNORECASE)
            if match:
                return int(match.group(1))
    return None


def determine_seniority(title):
    title_lower = str(title).lower()
    found = set()
    if any(w in title_lower for w in ['senior', 'lead', 'principal', 'head', 'manager', 'staff', 'director', 'vp']):
        found.add('Senior/Lead')
    if any(w in title_lower for w in ['mid', 'associate']):
        found.add('Mid')
    if any(w in title_lower for w in ['junior', 'entry', 'graduate', 'trainee', 'fresh']):
        found.add('Entry')
    if any(w in title_lower for w in ['intern']):
        found.add('Intern')
    return ", ".join(found) if found else 'Unknown'


def determine_visa_eligibility(description):
    if not isinstance(description, str): return 'Unknown'
    desc_lower = description.lower()
    local_patterns = [
        r'singaporeans? only', r'singapore citizens?', r'prs? only',
        r'singaporeans? and prs?', r'singaporeans?/prs?', r'no quota',
        r'sponsorship not available', r'no work pass quota', r'locals only', r'pr preferred'
    ]
    if any(re.search(pat, desc_lower) for pat in local_patterns):
        return 'Local/PR Only'
    sponsor_patterns = [
        r'sponsorship available', r'visa sponsorship', r'work pass sponsorship',
        r'quota available', r'ep\s?/\s?sp available', r'employment pass provided'
    ]
    if any(re.search(pat, desc_lower) for pat in sponsor_patterns):
        return 'Sponsorship Available'
    return 'Unknown'


def setup_keyword_processors():
    processors = {}
    categories = {
        'is_agent': ['claude', 'gemini', 'cursor', 'langchain', 'llamaindex', 'autogen', 'crewai', 'agentic', 'devin', 'copilot', 'ai agent', 'ai agents', 'autonomous agent', 'autonomous agents', 'multi-agent', 'agentic workflow', 'langgraph', 'semantic kernel', 'model context protocol', 'tool-calling'],
        'is_ai_llm': ['llm', 'llms', 'generative ai', 'rag', 'fine-tuning', 'prompt engineering', 'transformer', 'vector db', 'openai', 'anthropic', 'genai', 'gpt', 'chatgpt', 'large language model', 'vector database', 'embeddings', 'huggingface', 'bedrock'],
        'is_de': ['etl', 'elt', 'hadoop', 'pyspark', 'spark', 'airflow', 'snowflake', 'databricks', 'bigquery', 'kafka', 'dbt', 'data warehouse', 'data warehousing', 'data lake', 'data pipeline', 'redshift', 'hive', 'flink', 'data engineering'],
        'is_ds': ['machine learning', 'deep learning', 'pytorch', 'tensorflow', 'scikit-learn', 'xgboost', 'nlp', 'power bi', 'tableau', 'looker', 'dashboard', 'business intelligence', 'data visualization', 'forecasting', 'statistical modeling', 'predictive modeling', 'data analysis', 'qlik'],
        'is_swe': ['backend', 'frontend', 'fullstack', 'rest api',  'restapi', 'fastapi', 'django', 'microservices', 'docker', 'kubernetes', 'ci/cd', 'load balancing', 'horizontal scaling', 'autoscaling', 'high availability', 'distributed systems', 'site reliability', 'terraform', 'infrastructure as code', 'service mesh', 'fault tolerance', 'production traffic', 'reliability engineering', 'helm'],
    }
    for col_name, keywords in categories.items():
        kp = KeywordProcessor(case_sensitive=False)
        for kw in keywords:
            kp.add_keyword(kw)
        processors[col_name] = kp
    return processors


def clean_emails(x):
    if isinstance(x, list):
        emails = x
    elif isinstance(x, str):
        emails = x.split(',')
    else:
        emails = []
    emails = list(dict.fromkeys(e.strip() for e in emails if e and e.strip()))
    return ', '.join(emails)


def clean_html(html):
    if not isinstance(html, str): return ''
    return re.sub('<[^<]+?>', ' ', html)


def format_jobspy_salary(row):
    min_amt, max_amt = row.get('min_amount'), row.get('max_amount')
    curr = row.get('currency', 'Unknown')
    interval = row.get('interval', '')
    if pd.notna(min_amt) and pd.notna(max_amt):
        return f"{curr} {int(min_amt):,} - {int(max_amt):,} ({interval})".strip()
    elif pd.notna(min_amt):
        return f"{curr} minimum amount: {int(min_amt):,} ({interval})".strip()
    return ""


def get_work_arrangement(row):
    if row.get('is_remote') == True: return 'Remote'
    wfh = str(row.get('work_from_home_type', '')).lower()
    if 'hybrid' in wfh: return 'Hybrid'
    return 'On-site/Unspecified'


# --- Source scrapers ---

def scrape_jobspy_source():
    indeed_frames, linkedin_frames = [], []
    for term in SEARCH_TERMS:
        print(f"Scraping Indeed for '{term}'...")
        indeed_frames.append(scrape_jobs(
            site_name=["indeed"],
            search_term=term,
            location="Singapore",
            country_indeed='singapore',
            results_wanted=JOBSPY_RESULTS_PER_TERM,
        ))
        print(f"Scraping LinkedIn for '{term}'...")
        linkedin_frames.append(scrape_jobs(
            site_name=["linkedin"],
            search_term=term,
            location="Singapore",
            results_wanted=JOBSPY_RESULTS_PER_TERM,
            linkedin_fetch_description=True,
        ))

    jobspy_df = pd.concat(indeed_frames + linkedin_frames, ignore_index=True)

    jobspy_df['job_id'] = jobspy_df['id']
    jobspy_df['salary_str'] = jobspy_df.apply(format_jobspy_salary, axis=1)
    jobspy_df['source'] = jobspy_df['site']
    jobspy_df['work_type'] = jobspy_df['job_type'].fillna('')
    jobspy_df['work_arrangement'] = jobspy_df.apply(get_work_arrangement, axis=1)
    jobspy_df['is_expired'] = False
    jobspy_df['country'] = 'SG'
    jobspy_df['emails'] = jobspy_df['emails'].apply(clean_emails)

    return jobspy_df


def scrape_apify_source():
    print("Scraping JobStreet SG via Apify (blackfalcondata/jobstreet-scraper)...")
    client = ApifyClient(APIFY_TOKEN)

    results_per_term = max(1, APIFY_MAX_RESULTS // len(SEARCH_TERMS))
    apify_raw_data = []
    for term in SEARCH_TERMS:
        print(f"Scraping JobStreet for '{term}'...")
        run = client.actor("blackfalcondata/jobstreet-scraper").call(run_input={
            "query": term,
            "country": "SG",
            "maxResults": results_per_term,
            "includeDetails": True,
        })
        apify_raw_data.extend(client.dataset(run.default_dataset_id).list_items().items)

    apify_df = pd.DataFrame(apify_raw_data)

    apify_df = apify_df[apify_df['locationCountry'] == 'SG']

    apify_df['job_id'] = apify_df['seekJobId']
    apify_df = apify_df.drop_duplicates(subset='job_id').reset_index(drop=True)
    apify_df['emails'] = apify_df['extractedEmails'].apply(clean_emails)

    apify_df['salary_str'] = apify_df['salaryText'].fillna('')
    apify_df['min_amount'] = apify_df['salaryMin']
    apify_df['max_amount'] = apify_df['salaryMax']
    apify_df['currency'] = apify_df['salaryCurrency']
    apify_df['interval'] = apify_df['salaryType']
    apify_df['source'] = 'JobStreet'
    apify_df['work_type'] = apify_df['employmentType'].fillna('')
    apify_df['is_expired'] = False
    apify_df['country'] = 'SG'

    apify_df['job_url'] = apify_df['canonicalUrl']
    apify_df['company_url'] = apify_df['companyUrl']
    apify_df['company_industry'] = apify_df['companyIndustry']

    arrangement_map = {'onsite': 'On-site', 'hybrid': 'Hybrid', 'remote': 'Remote'}
    apify_df['work_arrangement'] = apify_df['workArrangement'].str.lower().replace(arrangement_map).fillna('On-site/Unspecified')

    apify_df['postedDate'] = pd.to_datetime(apify_df['postedDate'], utc=True).dt.tz_localize(None)
    apify_df['date_posted'] = (
        apify_df['postedDate'].dt.day.astype(str) + '/'
        + apify_df['postedDate'].dt.month.astype(str) + '/'
        + apify_df['postedDate'].dt.year.astype(str)
    )
    apify_df['jobstreet_job_score'] = apify_df['jobScore']

    apify_df['description'] = apify_df['description'].fillna('')
    needs_fallback = apify_df['description'].str.strip() == ''
    apify_df.loc[needs_fallback, 'description'] = apify_df.loc[needs_fallback, 'descriptionMarkdown'].fillna('')
    needs_fallback = apify_df['description'].str.strip() == ''
    apify_df.loc[needs_fallback, 'description'] = apify_df.loc[needs_fallback, 'descriptionHtml'].apply(clean_html)

    bullets_str = apify_df['bulletPoints'].apply(lambda x: ' '.join(x) if isinstance(x, list) else x).fillna('')
    apify_df['description'] = (apify_df['teaser'].fillna('') + ' ' + apify_df['description'] + ' ' + bullets_str).str.strip()

    return apify_df


def build_merged(jobspy_df, apify_df):
    jobspy_keep = jobspy_df.reindex(columns=KEEP_COLS)
    apify_keep = apify_df.reindex(columns=KEEP_COLS)
    merged_df = pd.concat([jobspy_keep, apify_keep], ignore_index=True)
    merged_df = merged_df.drop_duplicates(subset='job_id', keep='first').reset_index(drop=True)

    combined_text = merged_df['title'].fillna('') + ' ' + merged_df['description'].fillna('')
    merged_df['min_years_exp'] = merged_df['description'].apply(extract_years_exp)
    merged_df['seniority'] = merged_df['title'].apply(determine_seniority)
    merged_df['visa_eligibility'] = merged_df['description'].apply(determine_visa_eligibility)

    keyword_processors = setup_keyword_processors()
    for col_name, kp in keyword_processors.items():
        merged_df[col_name] = combined_text.apply(lambda text: bool(kp.extract_keywords(text)))

    return merged_df


# --- Persistence (SQLite) ---
# Two patterns for two different needs:
#   - jobspy_raw/apify_raw: wide, loosely-structured debug dumps with no fixed schema — load
#     existing table (if any) -> concat with new -> dedupe(keep='first') -> replace the whole
#     table, same whole-file-rewrite semantics as the Excel version's append_and_save.
#   - jobs: fixed schema, needs real upsert semantics so a re-scrape never overwrites an existing
#     (possibly hand-edited) row -> INSERT ... ON CONFLICT(job_id) DO NOTHING.

def table_exists(conn, name):
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


def load_existing_sql(conn, table_name):
    if not table_exists(conn, table_name):
        print(f"No existing '{table_name}' table — treating this as a first run for it.")
        return None
    return pd.read_sql(f"SELECT * FROM {table_name}", conn)


def stringify_unsupported(df):
    # sqlite3 can only bind primitives (text/int/real/blob/null). Two ways a raw scraper
    # column ends up holding something else by the time it reaches to_sql():
    #   - list/dict values straight from Apify's JSON (e.g. bulletPoints, extractedEmails)
    #     that were never touched by the pipeline.
    #   - a column explicitly parsed to datetime (postedDate) that, after concatenating with
    #     existing rows read back from SQL as plain text, becomes a mixed object-dtype column
    #     still holding real Timestamp objects for the freshly-scraped rows — to_sql's
    #     automatic datetime serialization only kicks in for a genuine datetime64 dtype column,
    #     not a mixed object one, so those Timestamps hit the same unbindable-object problem.
    # Stringify both so the write never trips on an unbindable Python object, whatever the
    # column's dtype ended up being after the concat.
    df = df.copy()
    for col in df.columns:
        if df[col].apply(lambda v: isinstance(v, (list, dict))).any():
            df[col] = df[col].apply(lambda v: json.dumps(v) if isinstance(v, (list, dict)) else v)
        if df[col].apply(lambda v: isinstance(v, datetime)).any():
            df[col] = df[col].apply(lambda v: v.isoformat() if isinstance(v, datetime) else v)
    return df


def append_and_save_raw_sql(conn, table_name, existing_df, new_df, label):
    frames = [new_df] if existing_df is None else [existing_df, new_df]
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset='job_id', keep='first').reset_index(drop=True)
    combined = stringify_unsupported(combined)
    try:
        combined.to_sql(table_name, conn, if_exists='replace', index=False)
    except sqlite3.OperationalError:
        print(f"Could not write to '{table_name}' — database file may be locked, skipping this run.")
        return None
    added = len(combined) - (0 if existing_df is None else len(existing_df))
    print(f"{label}: {len(new_df)} scraped, {added} new. Saved {len(combined)} rows to '{table_name}'.")
    return combined


def upsert_jobs(conn, new_df):
    df = new_df.reindex(columns=KEEP_COLS + WORKFLOW_COLS)
    for col in BOOL_COLS:
        df[col] = df[col].fillna(False).astype(int)
    # sqlite3 doesn't coerce NaN to NULL on its own for executemany bindings — do it explicitly.
    df = df.astype(object).where(pd.notnull(df), None)

    cols = KEEP_COLS + WORKFLOW_COLS
    col_list = ", ".join(cols)
    placeholders = ", ".join(f":{c}" for c in cols)
    sql = f"INSERT INTO jobs ({col_list}) VALUES ({placeholders}) ON CONFLICT(job_id) DO NOTHING"
    conn.executemany(sql, df.to_dict("records"))
    conn.commit()


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(JOBS_SCHEMA)

    jobspy_new = scrape_jobspy_source()
    apify_new = scrape_apify_source()

    jobspy_existing = load_existing_sql(conn, "jobspy_raw")
    apify_existing = load_existing_sql(conn, "apify_raw")

    append_and_save_raw_sql(conn, "jobspy_raw", jobspy_existing, jobspy_new, "jobspy_raw")
    append_and_save_raw_sql(conn, "apify_raw", apify_existing, apify_new, "apify_raw")

    new_df = build_merged(jobspy_new, apify_new)

    new_df['applied'] = False
    new_df['status'] = 'New'
    new_df['notes'] = ''
    new_df['date_scraped'] = datetime.now().strftime('%Y-%m-%d')

    before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    try:
        upsert_jobs(conn, new_df)
    except sqlite3.OperationalError:
        print("Could not write to 'jobs' table — database file may be locked, skipping this run.")
        conn.close()
        return
    after = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    print(f"my_job_tracker: {len(new_df)} scraped, {after - before} new. 'jobs' table now has {after} rows.")

    conn.close()


if __name__ == "__main__":
    main()
