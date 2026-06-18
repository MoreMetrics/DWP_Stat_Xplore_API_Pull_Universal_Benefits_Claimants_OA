#!/usr/bin/env python
# coding: utf-8

# In[1]:


#!/usr/bin/env python3
"""
Pull DWP Stat-Xplore Universal Credit Households at Census Output Area level,
split into one CSV per Local Authority.

This script is tailored to your exported Stat-Xplore JSON:

  database: UC_Households
  measure:  count of UC households
  geography field: COA_CODE
  geography hierarchy: V_C_MASTERGEOG21_COA_TO_LSOA
  date field: DATE_NAME
  filter: HCPAYMENT = 1

You need:
  1. Your Stat-Xplore API key
  2. Your exported query JSON, e.g. table_2026-06-18_19-45-32.json
  3. An OA-to-Local-Authority lookup CSV, e.g. from ONSPD/NSPL or an OA-LAD lookup

Example:

  export STATXPLORE_API_KEY="your_api_key"

  python pull_uc_households_oa_by_lad.py \
    --base-query table_2026-06-18_19-45-32.json \
    --oa-lad-lookup oa_to_lad.csv \
    --output-dir uc_households_oa_by_lad \
    --combined

Test one LA first:

  python pull_uc_households_oa_by_lad.py \
    --base-query table_2026-06-18_19-45-32.json \
    --oa-lad-lookup oa_to_lad.csv \
    --only-lad E09000001 \
    --output-dir test_city_of_london
"""

from __future__ import annotations

import argparse
import copy
import itertools
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests


STATXPLORE_TABLE_URL = "https://stat-xplore.dwp.gov.uk/webapi/rest/v1/table"

COA_FIELD = "str:field:UC_Households:V_F_UC_HOUSEHOLDS:COA_CODE"

DATE_FIELD = "str:field:UC_Households:F_UC_HH_DATE:DATE_NAME"

COA_VALUE_TEMPLATE = (
    "str:value:UC_Households:V_F_UC_HOUSEHOLDS:COA_CODE:"
    "V_C_MASTERGEOG21_COA_TO_LSOA:{oa_code}"
)

OA_CODE_RE = re.compile(r"^[EWS]\d{8}$", re.IGNORECASE)


def is_oa_code(x: str) -> bool:
    return bool(OA_CODE_RE.match(str(x).strip()))


def code_from_uri(uri: str) -> str:
    return str(uri).rsplit(":", 1)[-1]


def safe_filename(x: str) -> str:
    x = str(x).strip()
    x = re.sub(r"[^\w\-.]+", "_", x)
    return x.strip("_") or "unknown"


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def find_column(df: pd.DataFrame, explicit: str | None, candidates: list[str], label: str) -> str:
    if explicit:
        if explicit not in df.columns:
            raise ValueError(f"{label} column '{explicit}' not found in lookup CSV.")
        return explicit

    lower_to_original = {c.lower(): c for c in df.columns}

    for candidate in candidates:
        if candidate.lower() in lower_to_original:
            return lower_to_original[candidate.lower()]

    raise ValueError(
        f"Could not auto-detect {label} column.\n"
        f"Available columns: {', '.join(df.columns)}\n"
        f"Pass it explicitly, e.g. --oa-col OA21CD --lad-col LAD24CD"
    )


def optional_column(df: pd.DataFrame, explicit: str | None, candidates: list[str]) -> str | None:
    if explicit:
        if explicit not in df.columns:
            raise ValueError(f"Optional column '{explicit}' not found in lookup CSV.")
        return explicit

    lower_to_original = {c.lower(): c for c in df.columns}

    for candidate in candidates:
        if candidate.lower() in lower_to_original:
            return lower_to_original[candidate.lower()]

    return None


def load_oa_lad_lookup(args: argparse.Namespace) -> pd.DataFrame:
    """
    Accepts either a clean OA-LAD lookup or a postcode-level ONSPD/NSPL file.
    If postcode-level, it deduplicates to unique OA/LAD pairs.
    """

    lookup = pd.read_csv(args.oa_lad_lookup, dtype=str).fillna("")

    oa_col = find_column(
        lookup,
        args.oa_col,
        [
            "OA21CD", "oa21cd", "oa21",
            "OA11CD", "oa11cd", "oa11",
            "OACD", "oa_code", "oa",
        ],
        "OA",
    )

    lad_col = find_column(
        lookup,
        args.lad_col,
        [
            "LAD24CD", "lad24cd",
            "LAD23CD", "lad23cd",
            "LAD22CD", "lad22cd",
            "LAD21CD", "lad21cd",
            "LAD11CD", "lad11cd",
            "LADCD", "ladcd",
            "LAUA", "laua",
            "OSLAUA", "oslaua",
            "la_code",
        ],
        "Local Authority",
    )

    lad_name_col = optional_column(
        lookup,
        args.lad_name_col,
        [
            "LAD24NM", "lad24nm",
            "LAD23NM", "lad23nm",
            "LAD22NM", "lad22nm",
            "LAD21NM", "lad21nm",
            "LAD11NM", "lad11nm",
            "LADNM", "ladnm",
            "LA_NAME", "la_name",
        ],
    )

    region_col = optional_column(
        lookup,
        args.region_col,
        [
            "RGN24CD", "rgn24cd",
            "RGN23CD", "rgn23cd",
            "RGN22CD", "rgn22cd",
            "RGN21CD", "rgn21cd",
            "RGN11CD", "rgn11cd",
            "RGNCD", "rgncd",
            "RGN", "rgn",
        ],
    )

    region_name_col = optional_column(
        lookup,
        args.region_name_col,
        [
            "RGN24NM", "rgn24nm",
            "RGN23NM", "rgn23nm",
            "RGN22NM", "rgn22nm",
            "RGN21NM", "rgn21nm",
            "RGN11NM", "rgn11nm",
            "RGNNM", "rgnnm",
        ],
    )

    out = pd.DataFrame()
    out["oa_code"] = lookup[oa_col].astype(str).str.strip()
    out["lad_code"] = lookup[lad_col].astype(str).str.strip()

    if lad_name_col:
        out["lad_name"] = lookup[lad_name_col].astype(str).str.strip()
    else:
        out["lad_name"] = out["lad_code"]

    if region_col:
        out["region_code"] = lookup[region_col].astype(str).str.strip()

    if region_name_col:
        out["region_name"] = lookup[region_name_col].astype(str).str.strip()

    out = out[out["oa_code"].map(is_oa_code)].copy()
    out = out[out["lad_code"] != ""].copy()
    out = out.drop_duplicates(["oa_code", "lad_code"])

    if out.empty:
        raise ValueError(
            "No usable OA/LAD rows found. Check that your lookup uses OA codes "
            "matching the Stat-Xplore COA_CODE geography."
        )

    return out

def get_latest_date_uri(base_query: dict[str, Any]) -> str:
    """
    Finds the latest DATE_NAME value in the exported Stat-Xplore JSON.

    Example date URI ends with:
      C_UC_HH_DATE:202602

    Returns the full URI for the latest month.
    """

    date_recode = base_query.get("recodes", {}).get(DATE_FIELD)

    if not date_recode:
        raise ValueError(
            f"Could not find DATE_NAME recode in base query: {DATE_FIELD}"
        )

    date_uris = []

    for group in date_recode.get("map", []):
        for uri in group:
            date_code = str(uri).rsplit(":", 1)[-1]

            if date_code.isdigit():
                date_uris.append((date_code, uri))

    if not date_uris:
        raise ValueError("No valid DATE_NAME values found in base query.")

    latest_date_code, latest_date_uri = max(date_uris, key=lambda x: x[0])

    print(f"Using latest Stat-Xplore date: {latest_date_code}")

    return latest_date_uri

def patch_query_for_oa_list(
    base_query: dict[str, Any],
    oa_codes: list[str],
    include_total: bool,
) -> dict[str, Any]:
    """
    Replace the COA_CODE recode map with the requested OA list.

    Also reduces DATE_NAME to the latest date only, so output is:
      one row per OA
    instead of:
      OA x month
    """

    query = copy.deepcopy(base_query)
    query.setdefault("recodes", {})

    # Replace OAs.
    query["recodes"][COA_FIELD] = {
        "map": [[COA_VALUE_TEMPLATE.format(oa_code=oa)] for oa in oa_codes],
        "total": bool(include_total),
    }

    # Keep only the latest date from the exported JSON.
    latest_date_uri = get_latest_date_uri(base_query)

    query["recodes"][DATE_FIELD] = {
        "map": [[latest_date_uri]],
        "total": False,
    }

    if "dimensions" not in query:
        query["dimensions"] = [
            [COA_FIELD],
            [DATE_FIELD],
            ["str:field:UC_Households:V_F_UC_HOUSEHOLDS:HCPAYMENT"],
        ]

    return query


def call_statxplore(query: dict[str, Any], api_key: str, timeout: int = 300) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "Accept-Language": "en",
        "APIKey": api_key,
    }

    for attempt in range(5):
        response = requests.post(
            STATXPLORE_TABLE_URL,
            headers=headers,
            json=query,
            timeout=timeout,
        )

        if response.status_code == 429:
            reset = response.headers.get("X-RateLimit-Reset")
            if reset and reset.isdigit():
                wait_seconds = max(10, int(int(reset) / 1000 - time.time()) + 2)
            else:
                wait_seconds = 60
            print(f"Rate limited. Sleeping {wait_seconds}s.")
            time.sleep(wait_seconds)
            continue

        if response.status_code >= 500 and attempt < 4:
            wait_seconds = 2 ** attempt
            print(f"Server error {response.status_code}. Retrying in {wait_seconds}s.")
            time.sleep(wait_seconds)
            continue

        if not response.ok:
            raise RuntimeError(
                f"Stat-Xplore API error {response.status_code}\n"
                f"{response.text[:4000]}"
            )

        return response.json()

    raise RuntimeError("Stat-Xplore request failed after retries.")


def normalise_label(labels: Any) -> str:
    if isinstance(labels, list):
        return " | ".join(str(x) for x in labels)
    return str(labels)


def normalise_uri(uris: Any) -> str:
    if isinstance(uris, list):
        return str(uris[0]) if uris else ""
    return str(uris)


def flatten_nested_values(x: Any) -> list[Any]:
    """
    Recursively flatten Stat-Xplore cube arrays.

    Stat-Xplore often returns cube data as nested lists, e.g.
      OA -> Date -> HCPAYMENT -> value

    We need one flat list of cell values.
    """
    if isinstance(x, list):
        out = []
        for item in x:
            out.extend(flatten_nested_values(item))
        return out

    if isinstance(x, dict):
        # Common Stat-Xplore wrapper keys.
        for key in ["values", "cells", "data"]:
            if key in x:
                return flatten_nested_values(x[key])

        # If it is a one-key measure wrapper, recurse into its value.
        if len(x) == 1:
            return flatten_nested_values(next(iter(x.values())))

        # Otherwise flatten all nested values that look cube-like.
        out = []
        for value in x.values():
            if isinstance(value, (list, dict)):
                out.extend(flatten_nested_values(value))
        if out:
            return out

    # Scalar cell value.
    return [x]


def find_cube_values(cube: Any) -> list[Any]:
    values = flatten_nested_values(cube)

    # Remove obvious non-cell empty wrappers, but keep zeros.
    values = [v for v in values if v is not None]

    return values


def flatten_statxplore_response(response: dict[str, Any]) -> pd.DataFrame:
    """
    Converts a Stat-Xplore table response into long-form rows.
    """

    fields = response.get("fields", [])
    measures = response.get("measures", [])
    cubes = response.get("cubes", [])

    if isinstance(cubes, dict):
      cubes = list(cubes.values())
    elif isinstance(cubes, list):
      pass
    else:
      raise ValueError(f"Unexpected cubes format: {type(cubes)}")

    if not fields:
      raise ValueError("API response contains no fields.")
    if not cubes:
      raise ValueError("API response contains no cubes.")

    field_item_lists: list[list[dict[str, str]]] = []

    for field in fields:
        field_uri = field.get("uri", "")
        field_label = field.get("label", field_uri)
        field_key = field_uri.rsplit(":", 1)[-1].lower()

        items = field.get("items", [])
        item_rows = []

        for item in items:
            uri = normalise_uri(item.get("uris", ""))
            label = normalise_label(item.get("labels", ""))

            code = code_from_uri(uri)

            item_rows.append(
                {
                    f"{field_key}_label": label,
                    f"{field_key}_code": code,
                    f"{field_key}_uri": uri,
                    "_field_label": str(field_label),
                }
            )

        field_item_lists.append(item_rows)

    combos = list(itertools.product(*field_item_lists))
    flat_combo_rows = []

    for combo in combos:
        row = {}
        for part in combo:
            part = {k: v for k, v in part.items() if k != "_field_label"}
            row.update(part)
        flat_combo_rows.append(row)

    all_rows = []

    for measure_index, cube in enumerate(cubes):
        values = find_cube_values(cube)

        if len(values) != len(flat_combo_rows):
            raise ValueError(
                f"Response shape mismatch: {len(values)} cube values but "
                f"{len(flat_combo_rows)} field combinations. "
                f"Try reducing the query to COA rows and DATE columns, or lower --chunk-size."
            )

        if measure_index < len(measures):
            measure_label = measures[measure_index].get("label", f"measure_{measure_index + 1}")
            measure_uri = measures[measure_index].get("uri", "")
        else:
            measure_label = f"measure_{measure_index + 1}"
            measure_uri = ""

        for base_row, value in zip(flat_combo_rows, values):
            row = dict(base_row)
            row["measure"] = measure_label
            row["measure_uri"] = measure_uri
            row["value"] = value
            all_rows.append(row)

    df = pd.DataFrame(all_rows)

    # Rename the key fields from your query into friendlier names.
    rename_map = {
        "coa_code_code": "oa_code",
        "coa_code_label": "oa_label",
        "date_name_code": "date_code",
        "date_name_label": "date_label",
        "hcpayment_code": "hcpayment_code",
        "hcpayment_label": "hcpayment_label",
    }

    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    if "oa_code" not in df.columns:
        for col in df.columns:
            if col.endswith("_code") and df[col].astype(str).map(is_oa_code).any():
                df["oa_code"] = df[col].astype(str)
                break

    if "value" in df.columns:
        df["value"] = pd.to_numeric(df["value"], errors="coerce")

    return df


def chunks(values: list[str], size: int):
    for i in range(0, len(values), size):
        yield i // size + 1, values[i : i + size]


def run(args: argparse.Namespace) -> None:
    api_key = args.api_key or os.getenv("STATXPLORE_API_KEY")
    if not api_key:
        raise ValueError("Missing API key. Set STATXPLORE_API_KEY or pass --api-key.")

    base_query = read_json(args.base_query)
    lookup = load_oa_lad_lookup(args)

    if args.only_lad:
        wanted_lads = {x.strip() for x in args.only_lad.split(",") if x.strip()}
        lookup = lookup[lookup["lad_code"].isin(wanted_lads)].copy()

    if lookup.empty:
        raise ValueError("No lookup rows left after filtering.")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    lad_groups = list(lookup.groupby(["lad_code", "lad_name"], dropna=False))
    lad_groups = sorted(lad_groups, key=lambda x: x[0][0])

    if args.max_lads:
        lad_groups = lad_groups[: args.max_lads]

    # Work out total number of API chunks for progress reporting.
    total_chunks = 0
    for _, lad_df_for_count in lad_groups:
        oa_count_for_lad = lad_df_for_count["oa_code"].dropna().nunique()
        total_chunks += (oa_count_for_lad + args.chunk_size - 1) // args.chunk_size

    completed_chunks = 0
    progress_callback = getattr(args, "progress_callback", None)

    combined_path = args.output_dir / "uc_households_oa_all_lads.csv"
    if args.combined and combined_path.exists():
        combined_path.unlink()

    manifest = []

    for lad_index, ((lad_code, lad_name), lad_df) in enumerate(lad_groups, start=1):
        oa_codes = sorted(lad_df["oa_code"].dropna().unique().tolist())

        print(
            f"[{lad_index}/{len(lad_groups)}] "
            f"{lad_code} {lad_name}: {len(oa_codes):,} OAs"
        )

        la_frames = []

        for chunk_no, oa_chunk in chunks(oa_codes, args.chunk_size):
            completed_chunks += 1

            percent = completed_chunks / total_chunks if total_chunks else 0
            progress_text = (
                f"Chunk {completed_chunks:,}/{total_chunks:,} "
                f"({percent:.1%}) — "
                f"{lad_code} — chunk {chunk_no}, {len(oa_chunk):,} OAs"
            )

            print("  " + progress_text)

            if progress_callback:
                progress_callback(
                    completed_chunks=completed_chunks,
                    total_chunks=total_chunks,
                    lad_code=lad_code,
                    lad_name=lad_name,
                    chunk_no=chunk_no,
                    oa_count=len(oa_chunk),
                    text=progress_text,
                )

            query = patch_query_for_oa_list(
                base_query=base_query,
                oa_codes=oa_chunk,
                include_total=args.include_oa_total,
            )

            response = call_statxplore(query=query, api_key=api_key)
            out = flatten_statxplore_response(response)

            if "oa_code" not in out.columns:
                raise ValueError(
                    "Could not find OA code in output. Check that COA_CODE is included "
                    "as a dimension in the exported base query."
                )

            meta_cols = ["oa_code", "lad_code", "lad_name"]
            for optional in ["region_code", "region_name"]:
                if optional in lookup.columns:
                    meta_cols.append(optional)

            out = out.merge(
                lookup[meta_cols].drop_duplicates("oa_code"),
                on="oa_code",
                how="left",
            )

            out["source_lad_code"] = lad_code
            out["source_lad_name"] = lad_name
            out["chunk_no"] = chunk_no

            la_frames.append(out)

            if args.sleep:
                time.sleep(args.sleep)

        la_out = pd.concat(la_frames, ignore_index=True)

        filename = f"{safe_filename(lad_code)}_{safe_filename(lad_name)}.csv"
        path = args.output_dir / filename
        la_out.to_csv(path, index=False)

        if args.combined:
            la_out.to_csv(
                combined_path,
                mode="a",
                header=not combined_path.exists(),
                index=False,
            )

        manifest.append(
            {
                "lad_code": lad_code,
                "lad_name": lad_name,
                "oa_count": len(oa_codes),
                "rows_written": len(la_out),
                "file": str(path),
            }
        )

    manifest_df = pd.DataFrame(manifest)
    manifest_df.to_csv(args.output_dir / "manifest.csv", index=False)

    print("Done.")
    print(f"Manifest: {args.output_dir / 'manifest.csv'}")

    if args.combined:
        print(f"Combined CSV: {combined_path}")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument("--base-query", required=True, type=Path)
    parser.add_argument("--oa-lad-lookup", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)

    parser.add_argument("--api-key", default=None)

    parser.add_argument("--oa-col", default=None)
    parser.add_argument("--lad-col", default=None)
    parser.add_argument("--lad-name-col", default=None)
    parser.add_argument("--region-col", default=None)
    parser.add_argument("--region-name-col", default=None)

    parser.add_argument("--only-lad", default=None)
    parser.add_argument("--max-lads", type=int, default=None)

    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1000,
        help="Number of OAs per API request. Lower this if the API errors.",
    )

    parser.add_argument(
        "--sleep",
        type=float,
        default=0.3,
        help="Pause between API calls.",
    )

    parser.add_argument(
        "--combined",
        action="store_true",
        help="Also write one combined CSV across all local authorities.",
    )

    parser.add_argument(
        "--include-oa-total",
        action="store_true",
        help="Keep Stat-Xplore total rows for the COA_CODE dimension. Default is off.",
    )

    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())

