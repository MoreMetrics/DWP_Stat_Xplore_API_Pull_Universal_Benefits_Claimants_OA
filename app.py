from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import streamlit as st

import dw_pull


st.set_page_config(page_title="DWP Stat-Xplore OA Pull", layout="wide")

st.title("DWP Stat-Xplore OA Pull")
st.write(
    "Pull Universal Credit household counts from DWP Stat-Xplore at OA level. "
    "This version is set up to return the latest selected date only, giving one row per OA."
)

# Prefer Streamlit secrets, but allow manual entry when running locally.
try:
    api_key = st.secrets.get("STATXPLORE_API_KEY", "")
except Exception:
    api_key = st.secrets.get("STATXPLORE_API_KEY", "")

if api_key:
    st.success("Using Stat-Xplore API key from Streamlit secrets.")
else:
    api_key = st.text_input("Stat-Xplore API key", type="password")

st.subheader("Base query JSON")

default_json_path = Path("table_2026-06-18_19-45-32_latest_date_only.json")
use_default_json = default_json_path.exists()

if use_default_json:
    st.info(f"Using JSON file from repo: {default_json_path.name}")
    base_query_file = None
else:
    base_query_file = st.file_uploader("Upload your Stat-Xplore JSON", type=["json"])

st.subheader("OA/LAD lookup")

lookup_file = st.file_uploader(
    "Upload your OA/LAD lookup CSV, for example an ONSPD/NSPL file",
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

    if not use_default_json and base_query_file is None:
        st.error("Upload the Stat-Xplore JSON file first.")
        st.stop()

    if lookup_file is None:
        st.error("Upload your OA/LAD lookup CSV first.")
        st.stop()

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        output_dir = tmpdir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        if use_default_json:
            base_query_path = default_json_path
        else:
            base_query_path = tmpdir / "base_query.json"
            base_query_path.write_bytes(base_query_file.getvalue())

        lookup_path = tmpdir / "oa_lad_lookup.csv"
        lookup_path.write_bytes(lookup_file.getvalue())

        args = SimpleNamespace(
            api_key=api_key,
            base_query=base_query_path,
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

        try:
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
                    f"**Progress:** {completed_chunks:,}/{total_chunks:,} chunks "
                    f"({percent:.1%})"
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

        csv_bytes = combined_csv.read_bytes()
        df = pd.read_csv(combined_csv)

        st.success(f"Done. Created {len(df):,} rows.")
        st.dataframe(df.head(200), use_container_width=True)

        st.download_button(
            "Download combined CSV",
            data=csv_bytes,
            file_name="uc_households_oa_all_lads.csv",
            mime="text/csv",
        )
