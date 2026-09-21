"""One-off backfill: pull recent rows from jobspy_raw into the jobs table.

For the specific gap where job_scraper_daily_sql.py successfully wrote jobspy_raw but crashed
before reaching the jobs upsert — this reconstructs
those missing jobs rows directly from what's already in jobspy_raw, no re-scrape needed.

Only inserts job_ids not already in `jobs`, so it's safe to run even if ROWS_TO_CHECK is off —
anything already tracked is just skipped, not duplicated or overwritten.

Run from the repo root: python src/backfill_jobs_from_raw.py
"""
import sqlite3
from datetime import datetime
import pandas as pd

from job_scraper_daily_sql import (
    KEEP_COLS,
    WORKFLOW_COLS,
    BOOL_COLS,
    extract_years_exp,
    determine_seniority,
    determine_visa_eligibility,
    setup_keyword_processors,
)

DB_PATH = "job_tracker.db"
ROWS_TO_CHECK = 18


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")

    raw = pd.read_sql(f"SELECT * FROM jobspy_raw ORDER BY rowid DESC LIMIT {ROWS_TO_CHECK}", conn)
    existing_ids = set(pd.read_sql("SELECT job_id FROM jobs", conn)["job_id"])
    new_rows = raw[~raw["job_id"].isin(existing_ids)].copy()

    print(
        f"Checked last {ROWS_TO_CHECK} rows in jobspy_raw: {len(raw)} fetched, "
        f"{len(raw) - len(new_rows)} already in jobs, {len(new_rows)} genuinely new."
    )

    if new_rows.empty:
        print("Nothing to backfill.")
        conn.close()
        return
    
    df = new_rows.reindex(columns=KEEP_COLS)

    combined_text = df["title"].fillna("") + " " + df["description"].fillna("")
    df["min_years_exp"] = df["description"].apply(extract_years_exp)
    df["seniority"] = df["title"].apply(determine_seniority)
    df["visa_eligibility"] = df["description"].apply(determine_visa_eligibility)

    keyword_processors = setup_keyword_processors()
    for col_name, kp in keyword_processors.items():
        df[col_name] = combined_text.apply(lambda text: bool(kp.extract_keywords(text)))

    df["applied"] = False
    df["status"] = "New"
    df["notes"] = ""
    df["date_scraped"] = datetime.now().strftime("%Y-%m-%d")

    for col in BOOL_COLS:
        if col in df.columns:
            df[col] = df[col].fillna(False).astype(int)
    df_insert = df.astype(object).where(pd.notnull(df), None)

    cols = KEEP_COLS + WORKFLOW_COLS
    col_list = ", ".join(cols)
    placeholders = ", ".join(f":{c}" for c in cols)
    sql = f"INSERT INTO jobs ({col_list}) VALUES ({placeholders}) ON CONFLICT(job_id) DO NOTHING"

    before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.executemany(sql, df_insert.to_dict("records"))
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    print(f"jobs table: {before} -> {after} ({after - before} newly added).")
    conn.close()


if __name__ == "__main__":
    main()
