"""Migration: load the existing Excel files into job_tracker.db.

Run by hand before first using job_scraper_daily_sql.py / job_viewer_sql.py, and safe to
re-run any time job_scraper_daily.py (the Excel-only script) has been run instead of the SQL
version — already-migrated rows are left untouched (ON CONFLICT DO NOTHING), only genuinely
new job_ids get added. No need to delete job_tracker.db first.
Does not modify or delete the source .xlsx files — they stay on disk as a backup.
"""
import json
import sqlite3
from datetime import datetime
import pandas as pd

DB_PATH = "job_tracker.db"
TRACKER_XLSX = "my_job_tracker.xlsx"
JOBSPY_XLSX = "jobspy_output.xlsx"
APIFY_XLSX = "apify_output.xlsx"

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
JOBS_COLS = KEEP_COLS + WORKFLOW_COLS
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
    seniority            TEXT,
    visa_eligibility     TEXT,
    is_agent             INTEGER,
    is_ai_llm            INTEGER,
    is_de                INTEGER,
    is_ds                INTEGER,
    is_swe               INTEGER,
    applied              INTEGER,
    status               TEXT,
    notes                TEXT,
    date_scraped         TEXT
)
"""


def migrate_jobs(conn):
    df = pd.read_excel(TRACKER_XLSX)
    for col in BOOL_COLS:
        if col in df.columns:
            df[col] = df[col].fillna(False).astype(int)
    df = df.reindex(columns=JOBS_COLS)
    # sqlite3 doesn't coerce NaN to NULL on its own for executemany bindings — do it explicitly.
    df_insert = df.astype(object).where(pd.notnull(df), None)

    col_list = ", ".join(JOBS_COLS)
    placeholders = ", ".join(f":{c}" for c in JOBS_COLS)
    sql = f"INSERT INTO jobs ({col_list}) VALUES ({placeholders}) ON CONFLICT(job_id) DO NOTHING"

    before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.executemany(sql, df_insert.to_dict("records"))
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    print(f"jobs: {len(df)} rows in {TRACKER_XLSX}. DB had {before}, now has {after} ({after - before} newly added).")

    edited = pd.read_sql("SELECT job_id, status, notes FROM jobs WHERE status != 'New'", conn)
    if not edited.empty:
        print(f"Spot-check — {len(edited)} rows with non-default status survived migration, e.g.:")
        print(edited.head(5).to_string(index=False))
    else:
        print("Spot-check — no rows with non-default status found (nothing to verify against).")


def stringify_unsupported(df):
    df = df.copy()
    for col in df.columns:
        if df[col].apply(lambda v: isinstance(v, (list, dict))).any():
            df[col] = df[col].apply(lambda v: json.dumps(v) if isinstance(v, (list, dict)) else v)
        if df[col].apply(lambda v: isinstance(v, datetime)).any():
            df[col] = df[col].apply(lambda v: v.isoformat() if isinstance(v, datetime) else v)
    return df


def migrate_raw(conn, xlsx_path, table_name):
    df = pd.read_excel(xlsx_path)
    df = stringify_unsupported(df)
    df.to_sql(table_name, conn, if_exists='replace', index=False)
    db_rows = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    print(f"{table_name}: {len(df)} rows in {xlsx_path}, {db_rows} rows in DB after migration.")


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(JOBS_SCHEMA)

    migrate_jobs(conn)
    migrate_raw(conn, JOBSPY_XLSX, "jobspy_raw")
    migrate_raw(conn, APIFY_XLSX, "apify_raw")

    conn.close()
    print(f"\nMigration complete. Source .xlsx files were not modified. New DB: {DB_PATH}")


if __name__ == "__main__":
    main()
