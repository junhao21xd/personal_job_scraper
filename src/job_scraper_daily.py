import os
import re
import pandas as pd
from datetime import datetime
from jobspy import scrape_jobs
from apify_client import ApifyClient
from flashtext import KeywordProcessor
from dotenv import load_dotenv

load_dotenv()
APIFY_TOKEN = os.getenv("APIFY_TOKEN")
TRACKER_FILE = "my_job_tracker.xlsx"
JOBSPY_FILE = "jobspy_output.xlsx"   # same filenames as Job_scraper.ipynb
APIFY_FILE = "apify_output.xlsx"

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


# --- Shared helpers (mirrors Job_scraper.ipynb) ---

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
        'is_agent': ['claude', 'gemini', 'cursor', 'langchain', 'llamaindex', 'autogen', 'crewai', 'agentic', 'devin', 'copilot'],
        'is_ai_llm': ['llm', 'generative ai', 'rag', 'fine-tuning', 'prompt engineering', 'transformer', 'vector db', 'openai', 'anthropic'],
        'is_de': ['etl', 'elt', 'hadoop', 'pyspark', 'spark', 'airflow', 'snowflake', 'databricks', 'bigquery', 'kafka', 'dbt'],
        'is_ds': ['machine learning', 'deep learning', 'pytorch', 'tensorflow', 'scikit-learn', 'xgboost', 'nlp'],
        'is_swe': ['backend', 'frontend', 'fullstack', 'rest api', 'microservices', 'docker', 'kubernetes', 'ci/cd', 'fastapi', 'django'],
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

    # The actor's "query" only searches its first term even when given a JSON array —
    # confirmed via apify_df['searchQuery'].unique() showing just one value. Loop per
    # term instead (mirrors the jobspy loop above), splitting the results budget across terms.
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
    # Same job can surface under multiple search terms; drop early so downstream
    # per-row work (date parsing, description fallback, etc.) isn't done twice.
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


# --- Persistence (the actual "daily update" loop) ---
# Same pattern for all three files: load existing (if any) -> append new rows ->
# drop_duplicates(keep='first') so old rows (and any manual edits on the tracker) win -> save back.

def load_existing(path, columns=None):
    if not os.path.exists(path):
        print(f"No existing '{path}' — treating this as a first run for it.")
        return pd.DataFrame(columns=columns) if columns else None
    return pd.read_excel(path)


def append_and_save(path, existing_df, new_df, label):
    frames = [new_df] if existing_df is None else [existing_df, new_df]
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset='job_id', keep='first').reset_index(drop=True)
    try:
        combined.to_excel(path, index=False)
    except PermissionError:
        print(f"Could not write to '{path}' — close it in Excel first, then re-run.")
        return None
    print(f"{label}: {len(new_df)} scraped, {len(combined) - (0 if existing_df is None else len(existing_df))} new. Saved {len(combined)} rows to {path}.")
    return combined


def main():
    jobspy_new = scrape_jobspy_source()
    apify_new = scrape_apify_source()

    jobspy_existing = load_existing(JOBSPY_FILE)
    apify_existing = load_existing(APIFY_FILE)

    # Both dataframes are already scraped (and the Apify one cost money) — a locked
    # debug/history file shouldn't discard that or block the tracker update below,
    # so these failures warn (inside append_and_save) rather than stopping the run.
    append_and_save(JOBSPY_FILE, jobspy_existing, jobspy_new, "jobspy_output.xlsx")
    append_and_save(APIFY_FILE, apify_existing, apify_new, "apify_output.xlsx")

    new_df = build_merged(jobspy_new, apify_new)

    new_df['applied'] = False
    new_df['status'] = 'New'
    new_df['notes'] = ''
    new_df['date_scraped'] = datetime.now().strftime('%Y-%m-%d')

    existing_tracker = load_existing(TRACKER_FILE, columns=KEEP_COLS + WORKFLOW_COLS)
    combined_tracker = append_and_save(TRACKER_FILE, existing_tracker, new_df, "my_job_tracker.xlsx")
    if combined_tracker is None:
        return


if __name__ == "__main__":
    main()
