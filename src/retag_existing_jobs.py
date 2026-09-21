"""
One-off utility: re-tag every row in `jobs` using the CURRENT regex/FlashText keyword
lists (setup_keyword_processors() in job_scraper_daily_sql.py), overwriting the stored
is_agent/is_ai_llm/is_de/is_ds/is_swe values. Not part of the regular pipeline -- run
by hand after a keyword-list change, same spirit as backfill_jobs_from_raw.py.

WARNING: is_* are editable in job_viewer_sql.py's grid, and there's no audit trail on
them -- a manual correction made there will
be silently reverted back to whatever the current keyword list computes. Check the
dry-run diff for any job_id you know was hand-corrected before running --apply.

Usage (from repo root):
    python src/retag_existing_jobs.py          # dry run -- prints diff, writes nothing
    python src/retag_existing_jobs.py --apply  # writes the new tags to job_tracker.db
"""
import sys
import sqlite3
import pandas as pd

from job_scraper_daily_sql import setup_keyword_processors, DB_PATH

TAG_COLS = ["is_agent", "is_ai_llm", "is_de", "is_ds", "is_swe"]


def main():
    apply = "--apply" in sys.argv

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    df = pd.read_sql(f"SELECT job_id, title, description, {', '.join(TAG_COLS)} FROM jobs", conn)

    combined_text = df["title"].fillna("") + " " + df["description"].fillna("")
    processors = setup_keyword_processors()
    new_tags = {col: combined_text.apply(lambda t: bool(processors[col].extract_keywords(t))) for col in TAG_COLS}

    print(f"{len(df)} rows checked.\n")
    total_changed = 0
    for col in TAG_COLS:
        old = df[col].fillna(0).astype(bool)
        diff = old != new_tags[col]
        n = int(diff.sum())
        total_changed += n
        print(f"  {col}: {n} row(s) change")
        for job_id in df.loc[diff, "job_id"]:
            i = df.index[df["job_id"] == job_id][0]
            print(f"    {job_id}: {bool(old[i])} -> {bool(new_tags[col][i])}")

    if not apply:
        print(f"\nDry run only -- {total_changed} value(s) would change, nothing written.")
        print("Re-run with --apply to commit.")
        conn.close()
        return

    set_clause = ", ".join(f"{c} = ?" for c in TAG_COLS)
    for i, row in df.iterrows():
        values = [int(new_tags[col][i]) for col in TAG_COLS]
        conn.execute(f"UPDATE jobs SET {set_clause} WHERE job_id = ?", values + [row["job_id"]])
    conn.commit()
    conn.close()
    print(f"\nUpdated tags for {len(df)} rows.")


if __name__ == "__main__":
    main()
