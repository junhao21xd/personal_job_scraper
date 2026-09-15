import pandas as pd
import streamlit as st

st.set_page_config(page_title="Job Tracker Viewer", layout="wide")

DATA_FILE = "my_job_tracker.xlsx"


@st.cache_data
def load_data(path):
    df = pd.read_excel(path)
    df["min_years_exp"] = pd.to_numeric(df["min_years_exp"], errors="coerce")
    df["date_scraped"] = pd.to_datetime(df["date_scraped"], errors="coerce")
    return df


df = load_data(DATA_FILE)

st.title("Job Tracker")
st.caption(f"{len(df)} jobs loaded from {DATA_FILE}")

# --- Sidebar filters ---
st.sidebar.header("Filters")

search = st.sidebar.text_input("Search title / company")

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
        filtered["title"].str.contains(search, case=False, na=False)
        | filtered["company"].str.contains(search, case=False, na=False)
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

st.write(f"Showing {len(filtered)} of {len(df)} jobs")

# --- Table ---
display_cols = [
    "title", "company", "location", "source", "salary_str",
    "seniority", "work_arrangement", "visa_eligibility", "min_years_exp", "status",
]
st.dataframe(
    filtered[display_cols],
    use_container_width=True,
    hide_index=True,
)

# --- Detail view (full description) ---
st.subheader("Job detail")
if filtered.empty:
    st.info("No jobs match the current filters.")
else:
    filtered = filtered.reset_index(drop=True)
    labels = [f"{row.title} — {row.company}" for row in filtered.itertuples()]
    choice = st.selectbox("Select a job to read", options=range(len(labels)), format_func=lambda i: labels[i])
    job = filtered.iloc[choice]

    st.markdown(f"### {job['title']}")
    st.markdown(f"**{job['company']}** · {job['location']} · {job['source']}")
    if pd.notna(job.get("salary_str")) and job.get("salary_str"):
        st.markdown(f"**Salary:** {job['salary_str']}")

    st.markdown(f"**Job posting:** [{job['job_url']}]({job['job_url']})")
    if pd.notna(job.get("job_url_direct")):
        st.markdown(f"**Direct link:** [{job['job_url_direct']}]({job['job_url_direct']})")

    st.markdown("---")
    st.write(job.get("description", ""))
