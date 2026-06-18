from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import streamlit as st

import dw_pull

if "raw_csv_bytes" not in st.session_state:
    st.session_state.raw_csv_bytes = None

if "clean_csv_bytes" not in st.session_state:
    st.session_state.clean_csv_bytes = None

if "raw_preview" not in st.session_state:
    st.session_state.raw_preview = None

if "clean_preview" not in st.session_state:
    st.session_state.clean_preview = None

if "row_count" not in st.session_state:
    st.session_state.row_count = 0

st.set_page_config(page_title="Universal Credit Households OA level from DWP Stat-Xplore API Pull", layout="wide")

st.title("Universal Credit Households OA level from DWP Stat-Xplore API Pull")
st.markdown(
    """
Pull Universal Credit household counts from **DWP Stat-Xplore** at **Output Area (OA)** level.

This app builds the Stat-Xplore API query directly in Python and returns one row per OA for the selected month.

There are **two main requirements** for this app:

1. Latest API key from the Stat-Xplore account, instructions in `universal_credit_oa_read_me`.

2. Output Area to Local Authority District Lookup file named **oa_lad_small.csv**. 
   If you want to use an updated OA/LAD lookup version, instructions in `universal_credit_oa_read_me`.

And that's it! Just **RUN** the app.

**Notes:**

You should receive two outputs, the raw file and a cleaned version.
The app should run for around 10 minutes.

The columns should run with default settings.

**Date format**

Use `latest` to try the latest available month, or enter a specific month in `YYYYMM` format.

For example:

- February 2026 = `202602`
- March 2026 = `202603`

"""
)

try:
    api_key = st.secrets.get("STATXPLORE_API_KEY", "")
except Exception:
    api_key = ""

if api_key:
    st.success("Using Stat-Xplore API key from Streamlit secrets.")
else:
    api_key = st.text_input("Stat-Xplore API key", type="password")

st.subheader("Date")
date_code = st.text_input(
    "Stat-Xplore month",
    value="latest",
    help="Use 'latest' to ask Stat-Xplore for the latest available month, or enter YYYYMM, e.g. 202602.",
)

st.subheader("OA/LAD lookup")

default_lookup_path = Path("oa_lad_small.csv")
use_default_lookup = default_lookup_path.exists()

if use_default_lookup:
    st.info(f"Using lookup file from repo: {default_lookup_path.name}")
    lookup_file = None
else:
    lookup_file = st.file_uploader(
        "Upload your reduced OA/LAD lookup CSV",
        type=["csv"],
    )

with st.expander("Column settings", expanded=True):
    oa_col = st.text_input("OA column", value="oa21cd")
    lad_col = st.text_input("Local authority column", value="lad25cd")
    region_col = st.text_input("Region column", value="rgn25cd")
    only_lad = st.text_input("Optional: run one LAD code first", value="", placeholder="E09000001")
    chunk_size = st.number_input("Chunk size", min_value=50, max_value=5000, value=1000, step=50)

run_button = st.button("Run pull", type="primary")

if run_button:
    if not api_key:
        st.error("Add your Stat-Xplore API key first.")
        st.stop()

    if not use_default_lookup and lookup_file is None:
        st.error("Upload your OA/LAD lookup CSV first, or add oa_lad_small.csv to the repo.")
        st.stop()

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        output_dir = tmpdir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        if use_default_lookup:
            lookup_path = default_lookup_path
        else:
            lookup_path = tmpdir / "oa_lad_lookup.csv"
            lookup_path.write_bytes(lookup_file.getvalue())

        args = SimpleNamespace(
            api_key=api_key,
            date_code=date_code.strip() or "latest",
            oa_lad_lookup=lookup_path,
            output_dir=output_dir,
            oa_col=oa_col.strip() or None,
            lad_col=lad_col.strip() or None,
            lad_name_col=None,
            region_col=region_col.strip() or None,
            region_name_col=None,
            only_lad=only_lad.strip() or None,
            max_lads=None,
            chunk_size=int(chunk_size),
            sleep=0.3,
            combined=True,
            include_oa_total=False,
        )

        progress_bar = st.progress(0, text="Starting pull...")
        progress_text = st.empty()
        progress_log = st.empty()
        recent_updates = []

        def update_progress(
            completed_chunks,
            total_chunks,
            lad_code,
            lad_name,
            chunk_no,
            oa_count,
            text,
        ):
            percent = completed_chunks / total_chunks if total_chunks else 0
            progress_bar.progress(percent, text=text)
            progress_text.write(
                f"**Progress:** {completed_chunks:,}/{total_chunks:,} chunks ({percent:.1%})"
            )
            recent_updates.append(
                {
                    "chunk": f"{completed_chunks}/{total_chunks}",
                    "percent": f"{percent:.1%}",
                    "lad_code": lad_code,
                    "lad_name": lad_name,
                    "oa_count": oa_count,
                }
            )
            progress_log.dataframe(
                pd.DataFrame(recent_updates[-10:]),
                use_container_width=True,
                hide_index=True,
            )

        args.progress_callback = update_progress

        try:
            dw_pull.run(args)
            progress_bar.progress(1.0, text="Complete")
        except Exception as e:
            st.error("The pull failed.")
            st.exception(e)
            st.stop()

        combined_csv = output_dir / "uc_households_oa_all_lads.csv"
        if not combined_csv.exists():
            st.error("The script finished, but the combined CSV was not created.")
            st.stop()

        # Read raw output from dw_pull.py
        raw_csv_bytes = combined_csv.read_bytes()
        raw_df = pd.read_csv(combined_csv)

        # Create clean version
        columns_to_remove = [
            "coa_code_uri",
            "date_code",
            "date_name_uri",
            "hcpayment_label",
            "hcpayment_code",
            "hcpayment_uri",
            "measure_uri",
            "lad_name",
            "source_lad_code",
            "source_lad_name",
            "chunk_no",
        ]

        clean_df = raw_df.copy()

        clean_df = clean_df.drop(
            columns=columns_to_remove,
            errors="ignore",
        )

        clean_df = clean_df.rename(
            columns={
                "value": "number_of_households_claiming_universal_credit"
            }
        )

        clean_csv_bytes = clean_df.to_csv(index=False).encode("utf-8")

        st.success(f"Done. Created {len(raw_df):,} rows.")
        
        tab_raw, tab_clean = st.tabs(["Raw output", "Clean output"])
        
        with tab_raw:
            st.write("Raw output from the Stat-Xplore pull.")
            st.dataframe(raw_df.head(200), use_container_width=True)
        
            st.download_button(
                "Download raw CSV",
                data=raw_csv_bytes,
                file_name="uc_households_oa_all_lads_raw.csv",
                mime="text/csv",
            )
        
        with tab_clean:
            st.write("Cleaned output with unused technical columns removed.")
            st.dataframe(clean_df.head(200), use_container_width=True)
        
            st.download_button(
                "Download clean CSV",
                data=clean_csv_bytes,
                file_name="uc_households_oa_all_lads_clean.csv",
                mime="text/csv",
            )
