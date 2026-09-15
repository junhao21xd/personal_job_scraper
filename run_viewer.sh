#!/bin/bash
source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate nlp_env
streamlit run src/job_viewer.py
