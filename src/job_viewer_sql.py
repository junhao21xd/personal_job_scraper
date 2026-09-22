import sqlite3
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Job Tracker Viewer", layout="wide")

DATA_DB = "job_tracker.db"

EDITABLE_COLS = [
    'salary_str', 'applied', 'status', 'notes', 'is_expired',
    'min_years_exp', 'seniority', 'visa_eligibility',
    'is_agent', 'is_ai_llm', 'is_de', 'is_ds', 'is_swe',
]
BOOL_COLS = ['is_expired', 'applied', 'is_agent', 'is_ai_llm', 'is_de', 'is_ds', 'is_swe']
CONTEXT_COLS = ['title', 'company', 'location', 'source', 'work_arrangement']
# From resume_match_hybrid.ipynb's resume_match_scores table (LEFT JOINed below) -- read-only,
# same as CONTEXT_COLS, and absent (all-None) if that notebook has never been run.
MATCH_COLS = ['rrf_score', 'semantic_score', 'bm25_score', 'top_matching_chunks']
EDITOR_COLS = CONTEXT_COLS + EDITABLE_COLS + MATCH_COLS


def table_exists(conn, name):
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


@st.cache_data
def load_data(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    if table_exists(conn, "resume_match_scores"):
        df = pd.read_sql(
            """
            SELECT jobs.*, resume_match_scores.rrf_score, resume_match_scores.semantic_score,
                   resume_match_scores.bm25_score, resume_match_scores.top_matching_chunks,
                   resume_match_scores.computed_at AS match_computed_at
            FROM jobs
            LEFT JOIN resume_match_scores ON jobs.job_id = resume_match_scores.job_id
            """,
            conn,
        )
    else:
        # resume_match_hybrid.ipynb hasn't been run against this DB yet -- degrade gracefully
        # (empty fit-score columns, no crash), same spirit as how gold_* is handled.
        df = pd.read_sql("SELECT * FROM jobs", conn)
        for col in MATCH_COLS + ["match_computed_at"]:
            df[col] = None
    conn.close()
    df["min_years_exp"] = pd.to_numeric(df["min_years_exp"], errors="coerce")
    df["date_scraped"] = pd.to_datetime(df["date_scraped"], errors="coerce")
    for col in BOOL_COLS:
        df[col] = df[col].fillna(0).astype(bool)
    return df


def save_edits(edited_df, original_df):
    conn = sqlite3.connect(DATA_DB)
    conn.execute("PRAGMA journal_mode=WAL;")
    rows_changed = 0
    for job_id in edited_df.index:
        row_edited = edited_df.loc[job_id]
        row_original = original_df.loc[job_id]
        changed_cols = [
            c for c in EDITABLE_COLS
            if row_edited[c] != row_original[c]
            and not (pd.isna(row_edited[c]) and pd.isna(row_original[c]))
        ]
        if not changed_cols:
            continue
        set_clause = ", ".join(f"{c} = ?" for c in changed_cols)
        values = []
        for c in changed_cols:
            v = row_edited[c]
            if c in BOOL_COLS:
                v = int(bool(v))
            elif pd.isna(v):
                v = None
            values.append(v)
        values.append(job_id)
        conn.execute(f"UPDATE jobs SET {set_clause} WHERE job_id = ?", values)
        rows_changed += 1
    conn.commit()
    conn.close()
    return rows_changed


df = load_data(DATA_DB)

st.title("Job Tracker")
st.caption(f"{len(df)} jobs loaded from {DATA_DB}")

# --- Sidebar filters ---
if st.sidebar.button("Refresh data"):
    # Picks up changes made outside this dashboard
    # load_data's cache otherwise only gets cleared after
    # a Save in the edit grid below, so without this there's no way to see fresh data short of
    # restarting Streamlit.
    load_data.clear()
    st.rerun()

st.sidebar.header("Filters")

search = st.sidebar.text_input("Search title / company / job_id")

sources = st.sidebar.multiselect("Source", sorted(df["source"].dropna().unique()))
work_arrangements = st.sidebar.multiselect("Work arrangement", sorted(df["work_arrangement"].dropna().unique()))
visa = st.sidebar.multiselect("Visa eligibility", sorted(df["visa_eligibility"].dropna().unique()))
statuses = st.sidebar.multiselect("Status", sorted(df["status"].dropna().unique()))

seniority_options = sorted({s.strip() for cell in df["seniority"].dropna() for s in cell.split(",")})
seniority = st.sidebar.multiselect("Seniority", seniority_options)

st.sidebar.markdown("**Category tags** (must match all checked)")
tag_cols = ["is_agent", "is_ai_llm", "is_de", "is_ds", "is_swe"]
tag_filters = {col: st.sidebar.checkbox(col) for col in tag_cols}

hide_expired = st.sidebar.checkbox("Hide expired", value=True)
hide_applied = st.sidebar.checkbox("Hide applied", value=True)

years_available = df["min_years_exp"].dropna()
years_range = None
if not years_available.empty:
    y_min, y_max = int(years_available.min()), int(years_available.max())
    if y_min < y_max:
        years_range = st.sidebar.slider("Min years experience", y_min, y_max, (y_min, y_max))
    else:
        years_range = (y_min, y_max)

dates_available = df["date_scraped"].dropna()
date_range = None
if not dates_available.empty:
    d_min, d_max = dates_available.min().date(), dates_available.max().date()
    if d_min < d_max:
        date_range = st.sidebar.slider("Date scraped", d_min, d_max, (d_min, d_max))
    else:
        date_range = (d_min, d_max)

# --- Apply filters ---
filtered = df.copy()

if search:
    mask = (
        filtered["title"].str.contains(search, case=False, na=False, regex=False)
        | filtered["company"].str.contains(search, case=False, na=False, regex=False)
        | filtered["job_id"].str.contains(search, case=False, na=False, regex=False)
    )
    filtered = filtered[mask]

if sources:
    filtered = filtered[filtered["source"].isin(sources)]
if work_arrangements:
    filtered = filtered[filtered["work_arrangement"].isin(work_arrangements)]
if visa:
    filtered = filtered[filtered["visa_eligibility"].isin(visa)]
if statuses:
    filtered = filtered[filtered["status"].isin(statuses)]
if seniority:
    filtered = filtered[filtered["seniority"].apply(lambda cell: isinstance(cell, str) and any(s in cell for s in seniority))]

for col, checked in tag_filters.items():
    if checked:
        filtered = filtered[filtered[col]]

if hide_expired and "is_expired" in filtered.columns:
    filtered = filtered[filtered["is_expired"] != True]  # noqa: E712 — NaN treated as "not expired"

if hide_applied and "applied" in filtered.columns:
    filtered = filtered[filtered["applied"] != True]  # noqa: E712 — NaN treated as "not applied"

if years_range:
    filtered = filtered[
        filtered["min_years_exp"].isna()
        | filtered["min_years_exp"].between(years_range[0], years_range[1])
    ]

if date_range:
    filtered = filtered[
        filtered["date_scraped"].isna()
        | filtered["date_scraped"].dt.date.between(date_range[0], date_range[1])
    ]

filtered = filtered.reset_index(drop=True)
st.write(f"Showing {len(filtered)} of {len(df)} jobs")

# --- Editable table ---
editor_snapshot = filtered.set_index("job_id")[EDITOR_COLS].copy()
edited = st.data_editor(
    editor_snapshot,
    key="editor",
    disabled=CONTEXT_COLS + MATCH_COLS,
    hide_index=False,
    width="stretch",
)
st.caption(
    "Save changes before switching a filter or clicking Refresh data above — either one before "
    "saving may discard unsaved edits."
)

if st.button("Save changes"):
    changed = save_edits(edited, editor_snapshot)
    if changed:
        st.toast(f"Saved changes to {changed} row(s).")
        load_data.clear()
        st.rerun()
    else:
        st.info("No changes to save.")

# --- Detail view (full description) ---
st.subheader("Job detail")
if filtered.empty:
    st.info("No jobs match the current filters.")
else:
    labels = [f"{row.title} — {row.company} [{row.job_id}]" for row in filtered.itertuples()]
    choice = st.selectbox("Select a job to read", options=range(len(labels)), format_func=lambda i: labels[i])
    job = filtered.iloc[choice]

    st.markdown(f"### {job['title']}")
    st.markdown(f"**{job['company']}** · {job['location']} · {job['source']}")
    st.caption(f"job_id: {job['job_id']}")

    if pd.notna(job.get("salary_str")) and job.get("salary_str"):
        st.markdown(f"**Salary:** {job['salary_str']}")

    st.markdown(f"**Job posting:** [{job['job_url']}]({job['job_url']})")
    if pd.notna(job.get("job_url_direct")):
        st.markdown(f"**Direct link:** [{job['job_url_direct']}]({job['job_url_direct']})")

    st.markdown("---")
    st.write(job.get("description", ""))
