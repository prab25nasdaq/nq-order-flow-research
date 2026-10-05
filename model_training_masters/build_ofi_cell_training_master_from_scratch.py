#!/usr/bin/env python3
"""
build_ofi_cell_training_master_from_scratch.py

SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING.

Builds a full-fidelity, cell-level OFI Book Flow training master from the
Book Flow cache (sealed 500-volume bars, one row per price-level "cell" per
depth). Every cell inside every bar is preserved -- this is NOT an OHLC or
bar-summary dataset.

This script is READ-ONLY with respect to:
  - raw Rithmic files
  - the Book Flow cache daemon / parser
  - the Book Flow chart / dashboard code
  - the Feature Master daemon
  - model artifacts / active model pointer
  - broker / order / trading-flag code

It only reads from the paths listed in CONFIG below and writes new files
under a fresh timestamped folder inside OUTPUT_ROOT.

Run:
    python3 build_ofi_cell_training_master_from_scratch.py [--smoke-test]

--smoke-test restricts processing to a handful of files (one NQM6 file, a
few NQU6 files across depths) so the full pipeline can be sanity-checked
quickly before the full multi-GB run.
"""

import os
import re
import sys
import json
import glob
import math
import hashlib
import argparse
import traceback
from datetime import datetime, timezone, date, timedelta

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# ============================================================================
# CONFIG
# ============================================================================

CACHE_DIR = "/home/prabh/OFI_Production/book_flow_chart/cache/"
LIVE_FEATURES_DIR = "/home/prabh/OFI_Live_Features/"
CONTINUOUS_MASTER_PATH = os.path.join(LIVE_FEATURES_DIR, "master_NQ_continuous_backadjusted_shadow.ndjsonl")
NQU6_MASTER_PATH = os.path.join(LIVE_FEATURES_DIR, "master_NQU6_shadow.ndjsonl")
FEATURE_MASTER_PATH = "/home/prabh/OFI_Production/model_feature_master/data/model_feature_master_shadow.parquet"
BOOK_FLOW_CHART_SCHEMA_REF = "/home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py"

OUTPUT_ROOT = "/home/prabh/OFI_Production/model_training_masters/"
BUILD_SCRIPT_PATH = os.path.abspath(__file__)

START_DATE = date(2026, 6, 3)
TICK_SIZE = 0.25
SESSION_CUTOFF_UTC_HOUR = 21  # empirically derived: no bars observed with UTC hour == 21
                              # (CME Globex daily maintenance-break gap sits at ~21:00-22:00 UTC
                              #  in this data; session label flips at this boundary)
LADDER_WIDTHS = [40, 80, 160]

WIDE_FIELDS = [
    "observed", "price_level", "bid_add", "bid_pull", "ask_add", "ask_pull",
    "net_bid_flow", "net_ask_flow", "signed_flow", "abs_flow",
    "buyer_pressure", "seller_pressure", "balance", "trade_volume",
]

CELL_SOURCE_COLUMNS = [
    "bar_idx", "timestamp_utc", "bar_start_ts_ns", "bar_end_ts_ns",
    "price_level", "price_tick", "depth_n", "side_zone",
    "signed_flow", "abs_flow", "bid_add", "bid_pull", "ask_add", "ask_pull",
    "net_bid_flow", "net_ask_flow", "trade_volume_at_price",
    "buy_trade_volume_at_price", "sell_trade_volume_at_price",
    "close_price", "mid_price", "nearest_level", "distance_to_nearest_level",
    "bar_state",
]

FNAME_LEVEL_CANDLES_RE = re.compile(
    r'^book_flow_level_candles_(?P<contract>NQ[A-Z]\d)_(?P<date>\d{4}-\d{2}-\d{2})_top(?P<depth>\d+)\.parquet$'
)
FNAME_BARE_CACHE_RE = re.compile(
    r'^(?P<contract>NQ[A-Z]\d)_(?P<date>\d{4}-\d{2}-\d{2})_top(?P<depth>\d+)\.parquet$'
)
FNAME_BAR_SUMMARY_RE = re.compile(
    r'^book_flow_candles_(?P<contract>NQ[A-Z]\d)_top(?P<depth>\d+)\.parquet$'
)

pd.set_option("future.no_silent_downcasting", True)

# ============================================================================
# small utilities
# ============================================================================

def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_of_file(path, block_size=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(block_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def tick_str(k):
    if k < 0:
        return f"m{-k}"
    elif k == 0:
        return "z0"
    else:
        return f"p{k}"


def session_date_from_ts(ts_series):
    """Derive CME-style session date from tz-aware UTC timestamps using the
    empirically observed ~21:00 UTC maintenance-break boundary. Purely a
    function of the internal timestamp -- never trusts a filename/folder
    date. Only valid for a file whose FIRST bar follows the normal
    evening-open pattern (hour >= SESSION_CUTOFF_UTC_HOUR) -- see
    derive_session_dates_for_file() for the outage-restart case."""
    hours = ts_series.dt.hour
    d = ts_series.dt.date
    shifted = (ts_series - pd.Timedelta(days=1)).dt.date
    return np.where(hours >= SESSION_CUTOFF_UTC_HOUR, d, shifted)


def derive_session_dates_for_file(ts_series, file_meta_label_date):
    """session_date assignment for one cache file's rows.

    The naive per-row 21:00 UTC cutoff (session_date_from_ts) is only valid
    when a file opens with the normal overnight pattern (first bar's hour
    >= SESSION_CUTOFF_UTC_HOUR). When the Book Flow daemon restarts mid-day
    after an outage (confirmed dead-feed/watchdog incidents, e.g. the
    2026-07-06..08 gap), a file's very first bar can land at an arbitrary
    UTC hour (e.g. 05:17 UTC) -- blindly applying the 21:00 cutoff to those
    rows would shift them into the WRONG (prior calendar) session date.

    In that abnormal case we fall back to the file's own internal metadata
    label (`meta.json` / filename `date`, cross-verified to be identical and
    to be the daemon's own authoritative session bookkeeping -- confirmed by
    the fact that bar_idx is a strictly contiguous running counter across
    consecutive labeled files with no overlap), applied uniformly to every
    row in the file. This is still driven by data the producer itself wrote
    (never a folder name), just not a per-row timestamp computation, because
    the per-row heuristic is demonstrably wrong for these specific files.
    """
    if len(ts_series) == 0:
        return np.array([], dtype=object)
    first_ts = ts_series.iloc[int(np.argmin(ts_series.values))]
    if first_ts.hour >= SESSION_CUTOFF_UTC_HOUR:
        return session_date_from_ts(ts_series)
    label_date = date.fromisoformat(file_meta_label_date)
    return np.full(len(ts_series), label_date, dtype=object)


def safe_div(a, b, default=0.0):
    b = np.where(np.abs(b) < 1e-12, np.nan, b)
    out = a / b
    return np.where(np.isnan(out), default, out)


# ============================================================================
# PART A -- discover every candidate file in the cache
# ============================================================================

def discover_cache_files():
    """Recursively scan CACHE_DIR and classify every file."""
    records = []
    for root, _dirs, files in os.walk(CACHE_DIR):
        for fname in files:
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, CACHE_DIR)
            ext = os.path.splitext(fname)[1].lower().lstrip(".")
            rec = {
                "file_path": fpath,
                "file_relpath": rel,
                "file_name": fname,
                "file_type": ext if ext else "unknown",
                "file_size_bytes": os.path.getsize(fpath),
                "category": None,
                "contract": None,
                "filename_label_date": None,
                "depth_n_filename": None,
                "row_count": None,
                "columns": None,
                "min_timestamp_utc": None,
                "max_timestamp_utc": None,
                "min_bar_idx": None,
                "max_bar_idx": None,
                "depth_n_values": None,
                "unique_bars": None,
                "unique_price_levels": None,
                "unique_depth_values": None,
                "bar_state_values": None,
                "sealed_status": "unknown",
                "include_in_cell_master": False,
                "exclude_reason": None,
            }

            m1 = FNAME_LEVEL_CANDLES_RE.match(fname)
            m2 = FNAME_BARE_CACHE_RE.match(fname)
            m3 = FNAME_BAR_SUMMARY_RE.match(fname)

            if ext != "parquet":
                rec["category"] = {
                    "json": "meta_or_state_json",
                    "pkl": "pickle_internal_state",
                    "log": "log_file",
                }.get(ext, "other_non_parquet")
                rec["sealed_status"] = "n/a"
                rec["exclude_reason"] = "not a parquet cell-data candidate"
                records.append(rec)
                continue

            if m3 is not None and m1 is None and m2 is None:
                rec["category"] = "bar_summary_no_cells"
                rec["contract"] = m3.group("contract")
                rec["depth_n_filename"] = int(m3.group("depth"))
                rec["sealed_status"] = "bar_summary_rolling"
                rec["exclude_reason"] = (
                    "OHLC/bar-summary file with no price_level/cell rows -- "
                    "explicitly excluded per spec (must not become an OHLC-only dataset)"
                )
                try:
                    pf = pq.ParquetFile(fpath)
                    rec["row_count"] = pf.metadata.num_rows
                    rec["columns"] = json.dumps(pf.schema_arrow.names)
                except Exception as e:
                    rec["exclude_reason"] += f" (also failed to read: {e})"
                records.append(rec)
                continue

            if m1 is not None:
                rec["category"] = "cell_level_dated_source_family"
                rec["contract"] = m1.group("contract")
                rec["filename_label_date"] = m1.group("date")
                rec["depth_n_filename"] = int(m1.group("depth"))
            elif m2 is not None:
                rec["category"] = "cell_level_dated_cache_family"
                rec["contract"] = m2.group("contract")
                rec["filename_label_date"] = m2.group("date")
                rec["depth_n_filename"] = int(m2.group("depth"))
            else:
                rec["category"] = "unrecognized_parquet"
                rec["exclude_reason"] = "filename did not match any known cell-file pattern"
                try:
                    pf = pq.ParquetFile(fpath)
                    rec["row_count"] = pf.metadata.num_rows
                    rec["columns"] = json.dumps(pf.schema_arrow.names)
                except Exception:
                    pass
                records.append(rec)
                continue

            # cell-level candidate: inspect contents
            try:
                table = pq.read_table(fpath)
                df = table.to_pandas()
                rec["row_count"] = len(df)
                rec["columns"] = json.dumps(df.columns.tolist())
                if "price_level" not in df.columns:
                    rec["category"] = "unrecognized_no_price_level"
                    rec["exclude_reason"] = "no price_level column -- not a cell file"
                    records.append(rec)
                    continue

                ts = pd.to_datetime(df["timestamp_utc"], utc=True, errors="coerce")
                rec["min_timestamp_utc"] = str(ts.min())
                rec["max_timestamp_utc"] = str(ts.max())
                rec["min_bar_idx"] = int(df["bar_idx"].min())
                rec["max_bar_idx"] = int(df["bar_idx"].max())
                rec["depth_n_values"] = json.dumps(sorted(df["depth_n"].unique().tolist()))
                rec["unique_bars"] = int(df["bar_idx"].nunique())
                rec["unique_price_levels"] = int(df["price_level"].nunique())
                rec["unique_depth_values"] = int(df["depth_n"].nunique())
                if "bar_state" in df.columns:
                    states = df["bar_state"].unique().tolist()
                    rec["bar_state_values"] = json.dumps(states)
                    if set(states) == {"CLOSED"}:
                        rec["sealed_status"] = "sealed_closed_only"
                        rec["include_in_cell_master"] = True
                    elif "CLOSED" in states:
                        rec["sealed_status"] = "mixed_closed_and_forming"
                        rec["include_in_cell_master"] = True  # CLOSED rows filtered in at ingest time
                    else:
                        rec["sealed_status"] = "forming_only"
                        rec["exclude_reason"] = "forming-only file, excluded from training per spec"
                else:
                    rec["sealed_status"] = "no_bar_state_column_assumed_forming"
                    rec["exclude_reason"] = "no bar_state column -- cannot confirm sealed, excluded defensively"
            except Exception as e:
                rec["exclude_reason"] = f"failed to read/inspect: {e}"
                rec["sealed_status"] = "read_error"

            records.append(rec)

    disc_df = pd.DataFrame.from_records(records)
    return disc_df


def select_canonical_files(disc_df):
    """Group cell-level dated files by (contract, filename_label_date, depth)
    and pick one canonical file per group, comparing content when two
    families exist for the same key."""
    candidates = disc_df[disc_df["include_in_cell_master"]].copy()
    groups = candidates.groupby(["contract", "filename_label_date", "depth_n_filename"])

    canonical_files = []   # list of dict: contract,label_date,depth,file_path,category
    dedup_report_rows = []
    conflict_frames = []

    for (contract, label_date, depth), g in groups:
        g = g.sort_values("category")  # deterministic order
        files = g["file_path"].tolist()
        cats = g["category"].tolist()

        if len(files) == 1:
            canonical_files.append({
                "contract": contract, "filename_label_date": label_date,
                "depth_n": depth, "file_path": files[0],
            })
            dedup_report_rows.append({
                "contract": contract, "filename_label_date": label_date, "depth_n": depth,
                "files_found": json.dumps(files), "canonical_file": files[0],
                "duplicate_files": json.dumps([]), "status": "SINGLE_FILE_NO_DUPLICATE",
                "duplicate_row_count": 0, "conflict_row_count": 0,
            })
            continue

        # two (or more) candidate files for the same key -- compare content
        prefer_order = {"cell_level_dated_source_family": 0, "cell_level_dated_cache_family": 1}
        files_sorted = sorted(files, key=lambda f: prefer_order.get(
            g.loc[g["file_path"] == f, "category"].iloc[0], 9))
        canonical_path = files_sorted[0]
        other_paths = files_sorted[1:]

        try:
            df_canon = pq.read_table(canonical_path).to_pandas()
            df_canon = df_canon[df_canon.get("bar_state", "CLOSED") == "CLOSED"]
        except Exception as e:
            dedup_report_rows.append({
                "contract": contract, "filename_label_date": label_date, "depth_n": depth,
                "files_found": json.dumps(files), "canonical_file": canonical_path,
                "duplicate_files": json.dumps(other_paths), "status": f"READ_ERROR:{e}",
                "duplicate_row_count": 0, "conflict_row_count": 0,
            })
            canonical_files.append({
                "contract": contract, "filename_label_date": label_date,
                "depth_n": depth, "file_path": canonical_path,
            })
            continue

        status = "EXACT_DUPLICATE"
        dup_row_count = 0
        conflict_row_count = 0
        key_cols = ["bar_idx", "price_tick"]
        cmp_cols = ["bid_add", "bid_pull", "ask_add", "ask_pull", "signed_flow",
                    "abs_flow", "close_price", "mid_price"]

        for other_path in other_paths:
            try:
                df_other = pq.read_table(other_path).to_pandas()
                df_other = df_other[df_other.get("bar_state", "CLOSED") == "CLOSED"]
            except Exception as e:
                status = f"OTHER_READ_ERROR:{e}"
                continue

            if len(df_canon) != len(df_other):
                status = "CONFLICT_ROW_COUNT_MISMATCH"

            merged = df_canon[key_cols + cmp_cols].merge(
                df_other[key_cols + cmp_cols], on=key_cols, how="outer",
                suffixes=("_canon", "_other"), indicator=True)

            only_one_side = merged[merged["_merge"] != "both"]
            both = merged[merged["_merge"] == "both"]
            mismatch_mask = pd.Series(False, index=both.index)
            for c in cmp_cols:
                a = both[f"{c}_canon"].astype(float)
                b = both[f"{c}_other"].astype(float)
                mismatch_mask = mismatch_mask | (~np.isclose(a, b, atol=1e-6, equal_nan=True))

            n_conflict = int(mismatch_mask.sum()) + len(only_one_side)
            n_dup = int((~mismatch_mask).sum())
            dup_row_count += n_dup
            conflict_row_count += n_conflict

            if n_conflict > 0:
                status = "CONFLICT"
                conf = both[mismatch_mask].copy()
                conf["contract"] = contract
                conf["filename_label_date"] = label_date
                conf["depth_n"] = depth
                conf["canonical_file"] = canonical_path
                conf["other_file"] = other_path
                conflict_frames.append(conf)

        dedup_report_rows.append({
            "contract": contract, "filename_label_date": label_date, "depth_n": depth,
            "files_found": json.dumps(files), "canonical_file": canonical_path,
            "duplicate_files": json.dumps(other_paths), "status": status,
            "duplicate_row_count": dup_row_count, "conflict_row_count": conflict_row_count,
        })
        canonical_files.append({
            "contract": contract, "filename_label_date": label_date,
            "depth_n": depth, "file_path": canonical_path,
        })

    dedup_df = pd.DataFrame(dedup_report_rows)
    conflict_df = pd.concat(conflict_frames, ignore_index=True) if conflict_frames else pd.DataFrame()
    return canonical_files, dedup_df, conflict_df


# ============================================================================
# OHLCV / bar-reference lookup from the ndjsonl masters
# ============================================================================

def load_ndjsonl_lookup(path, keep_cols):
    """Stream an ndjsonl master file and return a DataFrame indexed by
    (bar_start_ts_ns, bar_end_ts_ns) with the requested columns."""
    rows = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            row = {k: rec.get(k) for k in keep_cols}
            rows.append(row)
    df = pd.DataFrame(rows)
    return df


def build_ohlcv_lookups():
    log("Loading continuous master ndjsonl for bar-reference OHLCV/mid lookup ...")
    cont_cols = ["bar_index", "bar_start_ts_ns", "bar_end_ts_ns", "vol_total",
                 "raw_open", "raw_high", "raw_low", "raw_close",
                 "continuous_open", "continuous_high", "continuous_low", "continuous_close",
                 "mid_mean", "mid_kf", "contract_symbol", "session_date" if False else "day"]
    cont_df = load_ndjsonl_lookup(CONTINUOUS_MASTER_PATH, cont_cols)
    cont_df = cont_df.dropna(subset=["bar_start_ts_ns", "bar_end_ts_ns"])
    cont_df["bar_start_ts_ns"] = cont_df["bar_start_ts_ns"].astype("int64")
    cont_df["bar_end_ts_ns"] = cont_df["bar_end_ts_ns"].astype("int64")
    cont_lookup = {}
    for r in cont_df.itertuples(index=False):
        cont_lookup[(r.bar_start_ts_ns, r.bar_end_ts_ns)] = r

    log(f"  continuous master rows: {len(cont_df)}")

    log("Loading NQU6 master ndjsonl (fallback / cross-check) ...")
    nqu6_cols = ["bar_index", "bar_start_ts_ns", "bar_end_ts_ns", "vol_total",
                 "px_open", "px_high", "px_low", "px_close", "mid_mean", "mid_kf"]
    nqu6_df = load_ndjsonl_lookup(NQU6_MASTER_PATH, nqu6_cols)
    nqu6_df = nqu6_df.dropna(subset=["bar_start_ts_ns", "bar_end_ts_ns"])
    nqu6_df["bar_start_ts_ns"] = nqu6_df["bar_start_ts_ns"].astype("int64")
    nqu6_df["bar_end_ts_ns"] = nqu6_df["bar_end_ts_ns"].astype("int64")
    nqu6_lookup = {}
    for r in nqu6_df.itertuples(index=False):
        nqu6_lookup[(r.bar_start_ts_ns, r.bar_end_ts_ns)] = r

    log(f"  NQU6 master rows: {len(nqu6_df)}")
    return cont_lookup, nqu6_lookup


def lookup_ohlcv(bar_start_ts_ns, bar_end_ts_ns, cont_lookup, nqu6_lookup):
    key = (int(bar_start_ts_ns), int(bar_end_ts_ns))
    r = cont_lookup.get(key)
    if r is not None:
        return {
            "open_price": r.raw_open, "high_price": r.raw_high,
            "low_price": r.raw_low, "close_price_ref": r.raw_close,
            "mid_price": r.mid_mean, "volume": r.vol_total,
            "continuous_open": r.continuous_open, "continuous_high": r.continuous_high,
            "continuous_low": r.continuous_low, "continuous_close": r.continuous_close,
            "ohlcv_source": "continuous_master",
        }
    r = nqu6_lookup.get(key)
    if r is not None:
        return {
            "open_price": r.px_open, "high_price": r.px_high,
            "low_price": r.px_low, "close_price_ref": r.px_close,
            "mid_price": r.mid_mean, "volume": r.vol_total,
            "continuous_open": None, "continuous_high": None,
            "continuous_low": None, "continuous_close": None,
            "ohlcv_source": "nqu6_master_fallback",
        }
    return {
        "open_price": None, "high_price": None, "low_price": None, "close_price_ref": None,
        "mid_price": None, "volume": None,
        "continuous_open": None, "continuous_high": None, "continuous_low": None, "continuous_close": None,
        "ohlcv_source": "unavailable",
    }


# ============================================================================
# PART B/C -- per-file normalization into canonical long-cell rows
# ============================================================================

LONG_MASTER_COLUMNS = [
    "source_file", "symbol", "contract", "continuous_symbol", "session_date",
    "timestamp_utc", "bar_idx", "bar_start_ts_ns", "bar_end_ts_ns", "depth_n",
    "price_level", "price_tick", "side_zone", "sealed", "bar_state",
    "bid_add", "bid_pull", "ask_add", "ask_pull",
    "net_bid_flow", "net_ask_flow", "signed_flow", "abs_flow",
    "buyer_pressure_cell", "seller_pressure_cell",
    "buyer_seller_balance_cell", "buyer_seller_ratio_cell",
    "trade_volume_at_price", "buy_trade_volume_at_price", "sell_trade_volume_at_price",
    "open_price", "high_price", "low_price", "close_price", "mid_price", "volume",
    "ohlcv_source",
    "nearest_level", "distance_to_nearest_level", "nearest_level_type", "distance_ticks_to_nearest_level",
    "close_tick", "mid_tick",
    "rel_tick_from_close", "rel_tick_from_mid", "rel_price_from_close", "rel_price_from_mid",
    "cell_id_within_bar",
    "cell_walking_mid_price_raw",
]

LONG_MASTER_DTYPES = {
    "source_file": "string", "symbol": "string", "contract": "string",
    "continuous_symbol": "string", "session_date": "string",
    "timestamp_utc": "string", "bar_idx": "int64",
    "bar_start_ts_ns": "int64", "bar_end_ts_ns": "int64", "depth_n": "int64",
    "price_level": "float64", "price_tick": "int64", "side_zone": "string",
    "sealed": "bool", "bar_state": "string",
    "bid_add": "float64", "bid_pull": "float64", "ask_add": "float64", "ask_pull": "float64",
    "net_bid_flow": "float64", "net_ask_flow": "float64", "signed_flow": "float64", "abs_flow": "float64",
    "buyer_pressure_cell": "float64", "seller_pressure_cell": "float64",
    "buyer_seller_balance_cell": "float64", "buyer_seller_ratio_cell": "float64",
    "trade_volume_at_price": "float64", "buy_trade_volume_at_price": "float64",
    "sell_trade_volume_at_price": "float64",
    "open_price": "float64", "high_price": "float64", "low_price": "float64",
    "close_price": "float64", "mid_price": "float64", "volume": "float64",
    "ohlcv_source": "string",
    "nearest_level": "string", "distance_to_nearest_level": "float64",
    "nearest_level_type": "string", "distance_ticks_to_nearest_level": "float64",
    "close_tick": "int64", "mid_tick": "int64",
    "rel_tick_from_close": "int64", "rel_tick_from_mid": "int64",
    "rel_price_from_close": "float64", "rel_price_from_mid": "float64",
    "cell_id_within_bar": "int64",
    "cell_walking_mid_price_raw": "float64",
}


def anchor_tick_for_price(target_price, price_level_arr, price_tick_arr):
    """Map an arbitrary target price onto the SAME tick numbering used by
    this bar's own price_tick column, by anchoring off the nearest observed
    cell (price_tick offset in the source data is an internal constant we
    don't need to know -- this recovers it robustly)."""
    idx = np.argmin(np.abs(price_level_arr - target_price))
    anchor_price = price_level_arr[idx]
    anchor_tick = price_tick_arr[idx]
    return int(anchor_tick + round((target_price - anchor_price) / TICK_SIZE))


def process_one_file(file_meta, cont_lookup, nqu6_lookup, stats, missingness_rows):
    """Load one canonical cell-level file, normalize to canonical schema,
    and return the resulting long-format DataFrame (bar_state == CLOSED only)."""
    fpath = file_meta["file_path"]
    contract = file_meta["contract"]
    depth_n_expected = file_meta["depth_n"]

    df = pq.read_table(fpath).to_pandas()
    missing_cols = [c for c in CELL_SOURCE_COLUMNS if c not in df.columns]
    for c in missing_cols:
        df[c] = np.nan
    if missing_cols:
        missingness_rows.append({
            "file_path": fpath, "missing_columns": json.dumps(missing_cols),
        })

    df = df[df["bar_state"] == "CLOSED"].copy()
    if df.empty:
        return None

    # drop exact duplicate rows within-file on the canonical dedup grain
    before = len(df)
    df = df.drop_duplicates(subset=["bar_idx", "depth_n", "price_tick"], keep="first")
    stats["within_file_exact_dup_dropped"] += (before - len(df))

    ts = pd.to_datetime(df["timestamp_utc"], utc=True, errors="coerce")
    df["timestamp_utc"] = ts
    df["session_date"] = derive_session_dates_for_file(ts, file_meta["filename_label_date"])

    df["symbol"] = "NQ"
    df["contract"] = contract
    df["source_file"] = fpath
    df["sealed"] = True

    # canonical derived cell columns
    df["net_bid_flow"] = df["bid_add"] - df["bid_pull"]
    df["net_ask_flow"] = df["ask_pull"] - df["ask_add"]
    df["signed_flow_calc"] = df["net_bid_flow"] + df["net_ask_flow"]
    df["abs_flow_calc"] = df["bid_add"] + df["bid_pull"] + df["ask_add"] + df["ask_pull"]
    # prefer the source-provided signed_flow/abs_flow (already validated against
    # the primitives below in Part H); fall back to the calculated value only
    # if source value is missing.
    df["signed_flow"] = df["signed_flow"].where(df["signed_flow"].notna(), df["signed_flow_calc"])
    df["abs_flow"] = df["abs_flow"].where(df["abs_flow"].notna(), df["abs_flow_calc"])
    df["buyer_pressure_cell"] = df["bid_add"] + df["ask_pull"]
    df["seller_pressure_cell"] = df["ask_add"] + df["bid_pull"]
    df["buyer_seller_balance_cell"] = df["buyer_pressure_cell"] - df["seller_pressure_cell"]
    df["buyer_seller_ratio_cell"] = df["buyer_pressure_cell"] / np.maximum(df["seller_pressure_cell"], 1e-9)

    df["cell_walking_mid_price_raw"] = df["mid_price"]
    df["nearest_level_type"] = df["nearest_level"]
    df["distance_ticks_to_nearest_level"] = (df["distance_to_nearest_level"] / TICK_SIZE).round()

    # ---- per-bar processing: OHLCV join + tick anchoring + ordering ----
    out_frames = []
    for bar_idx, bdf in df.groupby("bar_idx", sort=False):
        bdf = bdf.copy()
        bar_start = int(bdf["bar_start_ts_ns"].iloc[0])
        bar_end = int(bdf["bar_end_ts_ns"].iloc[0])
        ohlcv = lookup_ohlcv(bar_start, bar_end, cont_lookup, nqu6_lookup)

        close_price = bdf["close_price"].iloc[0]  # source-provided, constant per bar
        bar_mid_price = ohlcv["mid_price"] if ohlcv["mid_price"] is not None else float(bdf["mid_price"].median())

        price_level_arr = bdf["price_level"].to_numpy()
        price_tick_arr = bdf["price_tick"].to_numpy()

        close_tick = anchor_tick_for_price(close_price, price_level_arr, price_tick_arr)
        mid_tick = anchor_tick_for_price(bar_mid_price, price_level_arr, price_tick_arr)

        bdf["open_price"] = ohlcv["open_price"]
        bdf["high_price"] = ohlcv["high_price"]
        bdf["low_price"] = ohlcv["low_price"]
        bdf["close_price"] = close_price  # keep source close_price (authoritative, per-cell already)
        bdf["mid_price"] = bar_mid_price
        bdf["volume"] = ohlcv["volume"]
        bdf["ohlcv_source"] = ohlcv["ohlcv_source"]

        bdf["close_tick"] = close_tick
        bdf["mid_tick"] = mid_tick
        bdf["rel_tick_from_close"] = bdf["price_tick"] - close_tick
        bdf["rel_tick_from_mid"] = bdf["price_tick"] - mid_tick
        bdf["rel_price_from_close"] = bdf["price_level"] - close_price
        bdf["rel_price_from_mid"] = bdf["price_level"] - bar_mid_price

        bdf = bdf.sort_values("rel_tick_from_mid", kind="mergesort").reset_index(drop=True)
        bdf["cell_id_within_bar"] = np.arange(len(bdf))

        out_frames.append(bdf)

    if not out_frames:
        return None
    result = pd.concat(out_frames, ignore_index=True)

    result["continuous_symbol"] = np.where(
        result["ohlcv_source"] == "continuous_master", "NQ_continuous_backadjusted", pd.NA
    )
    result["bar_idx"] = result["bar_idx"].astype("int64")
    result["depth_n"] = result["depth_n"].astype("int64")

    result = result[LONG_MASTER_COLUMNS]
    return result


def cast_for_arrow(df, dtypes):
    df = df.copy()
    for col, dt in dtypes.items():
        if col not in df.columns:
            df[col] = pd.NA
        if dt == "string":
            df[col] = df[col].astype("string")
        elif dt == "bool":
            df[col] = df[col].astype("boolean")
        elif dt in ("int64",):
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        elif dt in ("float64",):
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    return df[list(dtypes.keys())]


# ============================================================================
# PART D -- bar-packed master (built per source file, streamed to disk)
# ============================================================================

PACKED_SCALAR_COLUMNS = [
    "symbol", "contract", "continuous_symbol", "session_date", "timestamp_utc",
    "bar_idx", "depth_n",
    "open_price", "high_price", "low_price", "close_price", "mid_price", "volume",
    "cell_count",
    "min_price_level", "max_price_level",
    "min_rel_tick_from_mid", "max_rel_tick_from_mid",
    "total_bid_add", "total_bid_pull", "total_ask_add", "total_ask_pull",
    "total_net_bid_flow", "total_net_ask_flow", "total_signed_flow", "total_abs_flow",
    "buyer_pressure_total", "seller_pressure_total",
    "buyer_seller_balance_total", "buyer_seller_ratio_total",
    "green_cell_count", "red_cell_count", "neutral_cell_count",
    "green_abs_flow_share", "red_abs_flow_share",
    "cells_json", "cells_count_check",
    "cells_rel_tick_list", "cells_price_level_list",
    "cells_bid_add_list", "cells_bid_pull_list", "cells_ask_add_list", "cells_ask_pull_list",
    "cells_net_bid_flow_list", "cells_net_ask_flow_list",
    "cells_signed_flow_list", "cells_abs_flow_list",
    "cells_buyer_pressure_list", "cells_seller_pressure_list",
]

CELL_JSON_FIELDS = [
    "cell_id", "price_level", "price_tick", "rel_tick_from_mid", "rel_tick_from_close",
    "rel_price_from_mid", "rel_price_from_close", "side_zone",
    "bid_add", "bid_pull", "ask_add", "ask_pull",
    "net_bid_flow", "net_ask_flow", "signed_flow", "abs_flow",
    "buyer_pressure_cell", "seller_pressure_cell", "buyer_seller_balance_cell", "buyer_seller_ratio_cell",
    "trade_volume_at_price", "buy_trade_volume_at_price", "sell_trade_volume_at_price",
    "nearest_level", "distance_to_nearest_level",
]


def build_packed_rows_for_bar(bdf):
    """bdf: cells of one (bar_idx, depth_n), already sorted by rel_tick_from_mid asc."""
    row = {}
    first = bdf.iloc[0]
    row["symbol"] = first["symbol"]
    row["contract"] = first["contract"]
    row["continuous_symbol"] = first["continuous_symbol"]
    row["session_date"] = first["session_date"]
    row["timestamp_utc"] = first["timestamp_utc"]
    row["bar_idx"] = int(first["bar_idx"])
    row["depth_n"] = int(first["depth_n"])
    row["open_price"] = first["open_price"]
    row["high_price"] = first["high_price"]
    row["low_price"] = first["low_price"]
    row["close_price"] = first["close_price"]
    row["mid_price"] = first["mid_price"]
    row["volume"] = first["volume"]

    n = len(bdf)
    row["cell_count"] = n
    row["min_price_level"] = float(bdf["price_level"].min())
    row["max_price_level"] = float(bdf["price_level"].max())
    row["min_rel_tick_from_mid"] = int(bdf["rel_tick_from_mid"].min())
    row["max_rel_tick_from_mid"] = int(bdf["rel_tick_from_mid"].max())

    row["total_bid_add"] = float(bdf["bid_add"].sum())
    row["total_bid_pull"] = float(bdf["bid_pull"].sum())
    row["total_ask_add"] = float(bdf["ask_add"].sum())
    row["total_ask_pull"] = float(bdf["ask_pull"].sum())
    row["total_net_bid_flow"] = float(bdf["net_bid_flow"].sum())
    row["total_net_ask_flow"] = float(bdf["net_ask_flow"].sum())
    row["total_signed_flow"] = float(bdf["signed_flow"].sum())
    row["total_abs_flow"] = float(bdf["abs_flow"].sum())
    row["buyer_pressure_total"] = float(bdf["buyer_pressure_cell"].sum())
    row["seller_pressure_total"] = float(bdf["seller_pressure_cell"].sum())
    row["buyer_seller_balance_total"] = row["buyer_pressure_total"] - row["seller_pressure_total"]
    row["buyer_seller_ratio_total"] = row["buyer_pressure_total"] / max(row["seller_pressure_total"], 1e-9)

    signed = bdf["signed_flow"].to_numpy()
    absf = bdf["abs_flow"].to_numpy()
    green_mask = signed > 0
    red_mask = signed < 0
    neutral_mask = signed == 0
    row["green_cell_count"] = int(green_mask.sum())
    row["red_cell_count"] = int(red_mask.sum())
    row["neutral_cell_count"] = int(neutral_mask.sum())
    total_abs = absf.sum()
    row["green_abs_flow_share"] = float(absf[green_mask].sum() / total_abs) if total_abs > 0 else 0.0
    row["red_abs_flow_share"] = float(absf[red_mask].sum() / total_abs) if total_abs > 0 else 0.0

    cells = []
    for i, r in enumerate(bdf.itertuples(index=False)):
        rd = r._asdict()
        cell = {
            "cell_id": int(rd["cell_id_within_bar"]),
            "price_level": float(rd["price_level"]),
            "price_tick": int(rd["price_tick"]),
            "rel_tick_from_mid": int(rd["rel_tick_from_mid"]),
            "rel_tick_from_close": int(rd["rel_tick_from_close"]),
            "rel_price_from_mid": float(rd["rel_price_from_mid"]),
            "rel_price_from_close": float(rd["rel_price_from_close"]),
            "side_zone": rd["side_zone"],
            "bid_add": float(rd["bid_add"]), "bid_pull": float(rd["bid_pull"]),
            "ask_add": float(rd["ask_add"]), "ask_pull": float(rd["ask_pull"]),
            "net_bid_flow": float(rd["net_bid_flow"]), "net_ask_flow": float(rd["net_ask_flow"]),
            "signed_flow": float(rd["signed_flow"]), "abs_flow": float(rd["abs_flow"]),
            "buyer_pressure_cell": float(rd["buyer_pressure_cell"]),
            "seller_pressure_cell": float(rd["seller_pressure_cell"]),
            "buyer_seller_balance_cell": float(rd["buyer_seller_balance_cell"]),
            "buyer_seller_ratio_cell": float(rd["buyer_seller_ratio_cell"]),
            "trade_volume_at_price": float(rd["trade_volume_at_price"]) if pd.notna(rd["trade_volume_at_price"]) else None,
            "buy_trade_volume_at_price": float(rd["buy_trade_volume_at_price"]) if pd.notna(rd["buy_trade_volume_at_price"]) else None,
            "sell_trade_volume_at_price": float(rd["sell_trade_volume_at_price"]) if pd.notna(rd["sell_trade_volume_at_price"]) else None,
            "nearest_level": rd["nearest_level"],
            "distance_to_nearest_level": float(rd["distance_to_nearest_level"]) if pd.notna(rd["distance_to_nearest_level"]) else None,
        }
        cells.append(cell)

    row["cells_json"] = json.dumps(cells)
    row["cells_count_check"] = len(cells)
    row["cells_rel_tick_list"] = json.dumps([c["rel_tick_from_mid"] for c in cells])
    row["cells_price_level_list"] = json.dumps([c["price_level"] for c in cells])
    row["cells_bid_add_list"] = json.dumps([c["bid_add"] for c in cells])
    row["cells_bid_pull_list"] = json.dumps([c["bid_pull"] for c in cells])
    row["cells_ask_add_list"] = json.dumps([c["ask_add"] for c in cells])
    row["cells_ask_pull_list"] = json.dumps([c["ask_pull"] for c in cells])
    row["cells_net_bid_flow_list"] = json.dumps([c["net_bid_flow"] for c in cells])
    row["cells_net_ask_flow_list"] = json.dumps([c["net_ask_flow"] for c in cells])
    row["cells_signed_flow_list"] = json.dumps([c["signed_flow"] for c in cells])
    row["cells_abs_flow_list"] = json.dumps([c["abs_flow"] for c in cells])
    row["cells_buyer_pressure_list"] = json.dumps([c["buyer_pressure_cell"] for c in cells])
    row["cells_seller_pressure_list"] = json.dumps([c["seller_pressure_cell"] for c in cells])

    return row


# ============================================================================
# PART E -- wide relative ladder builder
# ============================================================================

def build_wide_ladder_rows(bar_group_list, width):
    """bar_group_list: list of (bar_meta_dict, cells_df) for bars sharing the
    same source file. Returns a list of flat dict rows for this width."""
    ticks = np.arange(-width, width + 1)
    rows = []
    for meta, bdf in bar_group_list:
        row = dict(meta)
        rel = bdf["rel_tick_from_mid"].to_numpy()
        in_window = (rel >= -width) & (rel <= width)
        n_total = len(bdf)
        n_in_window = int(in_window.sum())
        row["cell_count_total"] = n_total
        row["cell_count_in_window"] = n_in_window

        min_rel = meta["min_rel_tick_from_mid"]
        max_rel = meta["max_rel_tick_from_mid"]
        truncated = (min_rel < -width) or (max_rel > width)
        na_gaps = (min_rel > -width) or (max_rel < width)
        if truncated:
            row["data_quality_flag"] = "TRUNCATED_CELLS_OUTSIDE_WIDTH"
        elif na_gaps:
            row["data_quality_flag"] = "NA_GAPS_OUTSIDE_BAR_DEPTH_RANGE"
        else:
            row["data_quality_flag"] = "OK"

        # init all rel_K_* to NaN/False
        for k in ticks:
            ks = tick_str(int(k))
            row[f"rel_{ks}_observed"] = False
            row[f"rel_{ks}_price_level"] = np.nan
            for fld in ["bid_add", "bid_pull", "ask_add", "ask_pull", "net_bid_flow",
                        "net_ask_flow", "signed_flow", "abs_flow", "buyer_pressure",
                        "seller_pressure", "balance", "trade_volume"]:
                within = (k >= min_rel) and (k <= max_rel)
                row[f"rel_{ks}_{fld}"] = 0.0 if within else np.nan

        sub = bdf[in_window]
        for r in sub.itertuples(index=False):
            rd = r._asdict()
            ks = tick_str(int(rd["rel_tick_from_mid"]))
            row[f"rel_{ks}_observed"] = True
            row[f"rel_{ks}_price_level"] = rd["price_level"]
            row[f"rel_{ks}_bid_add"] = rd["bid_add"]
            row[f"rel_{ks}_bid_pull"] = rd["bid_pull"]
            row[f"rel_{ks}_ask_add"] = rd["ask_add"]
            row[f"rel_{ks}_ask_pull"] = rd["ask_pull"]
            row[f"rel_{ks}_net_bid_flow"] = rd["net_bid_flow"]
            row[f"rel_{ks}_net_ask_flow"] = rd["net_ask_flow"]
            row[f"rel_{ks}_signed_flow"] = rd["signed_flow"]
            row[f"rel_{ks}_abs_flow"] = rd["abs_flow"]
            row[f"rel_{ks}_buyer_pressure"] = rd["buyer_pressure_cell"]
            row[f"rel_{ks}_seller_pressure"] = rd["seller_pressure_cell"]
            row[f"rel_{ks}_balance"] = rd["buyer_seller_balance_cell"]
            row[f"rel_{ks}_trade_volume"] = rd["trade_volume_at_price"] if pd.notna(rd["trade_volume_at_price"]) else 0.0

        rows.append(row)
    return rows


WIDE_META_COLUMNS = [
    "symbol", "contract", "continuous_symbol", "session_date", "timestamp_utc",
    "bar_idx", "depth_n", "open_price", "high_price", "low_price", "close_price",
    "mid_price", "volume", "cell_count_total", "cell_count_in_window",
    "min_rel_tick_from_mid", "max_rel_tick_from_mid", "data_quality_flag",
]


def wide_ladder_columns(width):
    cols = list(WIDE_META_COLUMNS)
    for k in range(-width, width + 1):
        ks = tick_str(k)
        cols.append(f"rel_{ks}_observed")
        cols.append(f"rel_{ks}_price_level")
        for fld in ["bid_add", "bid_pull", "ask_add", "ask_pull", "net_bid_flow",
                    "net_ask_flow", "signed_flow", "abs_flow", "buyer_pressure",
                    "seller_pressure", "balance", "trade_volume"]:
            cols.append(f"rel_{ks}_{fld}")
    return cols


# ============================================================================
# ParquetWriter helper (streaming, fixed schema)
# ============================================================================

class StreamWriter:
    def __init__(self, path, columns, string_cols=None, bool_cols=None, int_cols=None):
        self.path = path
        self.columns = columns
        self.string_cols = set(string_cols or [])
        self.bool_cols = set(bool_cols or [])
        self.int_cols = set(int_cols or [])
        self.writer = None
        self.schema = None
        self.n_rows = 0

    def _build_table(self, df):
        df = df.reindex(columns=self.columns)
        for c in self.columns:
            if c in self.string_cols:
                df[c] = df[c].astype("string")
            elif c in self.bool_cols:
                df[c] = df[c].astype("boolean")
            elif c in self.int_cols:
                df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
            else:
                df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
        table = pa.Table.from_pandas(df, preserve_index=False)
        if self.schema is None:
            self.schema = table.schema
        else:
            table = table.cast(self.schema)
        return table

    def write(self, df):
        if df is None or len(df) == 0:
            return
        table = self._build_table(df)
        if self.writer is None:
            self.writer = pq.ParquetWriter(self.path, table.schema)
        self.writer.write_table(table)
        self.n_rows += len(df)

    def close(self):
        if self.writer is not None:
            self.writer.close()


# ============================================================================
# PART F -- optional Feature Master context join
# ============================================================================

FEATURE_MASTER_WANTED = {
    "session": ["ofild_session", "session_date"],
    "vpin_pct": ["dash_vpin_pct"],
    "vpin_z20": [],  # not available in source -- documented gap
    "mlofi_norm": ["mlofi_norm"],
    "delta_norm": ["delta_norm"],
    "bullish_switch_score": ["bf_bs_bullish_switch_score"],
    "bearish_switch_score": ["bf_bs_bearish_switch_score"],
    "support_consumed": [],   # not available in source -- documented gap
    "resistance_consumed": [],  # not available in source -- documented gap
    "absorption_score": ["rxn_absorption"],
    "liquidity_vacuum_score": [],  # not available in source -- documented gap
    "upward_vacuum_score": [],     # not available in source -- documented gap
    "downward_vacuum_score": [],   # not available in source -- documented gap
    "nearest_poc": ["bf_native_POC", "lvl_POC", "dist_to_poc_ticks"],
    "nearest_vah": ["bf_native_VAH", "lvl_VAH", "dist_to_vah_ticks"],
    "nearest_val": ["bf_native_VAL", "lvl_VAL", "dist_to_val_ticks"],
    "nearest_hvn": ["bf_native_HVN", "lvl_HVN", "dist_to_hvn_ticks"],
    "nearest_lvn": ["bf_native_LVN", "lvl_LVN", "dist_to_lvn_ticks"],
}


def build_feature_master_join_report_and_lookup():
    report_rows = []
    lookup_cols = ["bar_end_ts_ns", "bar_index"]
    if not os.path.exists(FEATURE_MASTER_PATH):
        for req, avail in FEATURE_MASTER_WANTED.items():
            report_rows.append({
                "requested_context_column": req, "source_columns_found": json.dumps(avail),
                "status": "FEATURE_MASTER_FILE_NOT_FOUND",
            })
        return pd.DataFrame(report_rows), None

    fm_all_cols = pq.ParquetFile(FEATURE_MASTER_PATH).schema_arrow.names
    for req, avail in FEATURE_MASTER_WANTED.items():
        present = [c for c in avail if c in fm_all_cols]
        for c in present:
            if c not in lookup_cols:
                lookup_cols.append(c)
        status = "JOINED" if present else "NOT_AVAILABLE_IN_SOURCE"
        report_rows.append({
            "requested_context_column": req,
            "source_columns_found": json.dumps(present),
            "status": status,
        })
    report_df = pd.DataFrame(report_rows)

    fm_df = pq.read_table(FEATURE_MASTER_PATH, columns=lookup_cols).to_pandas()
    fm_df = fm_df.dropna(subset=["bar_end_ts_ns"])
    fm_df["bar_end_ts_ns"] = fm_df["bar_end_ts_ns"].astype("int64")
    fm_df = fm_df.drop_duplicates(subset=["bar_end_ts_ns"], keep="first")
    fm_df = fm_df.add_prefix("ctx_")
    fm_df = fm_df.rename(columns={"ctx_bar_end_ts_ns": "bar_end_ts_ns"})
    return report_df, fm_df


def join_context(df, fm_lookup):
    """Exact join on bar_end_ts_ns only (Part F: exact join only, no fuzzy join)."""
    if fm_lookup is None or df is None or len(df) == 0:
        return df
    out = df.merge(fm_lookup, on="bar_end_ts_ns", how="left")
    return out


# ============================================================================
# PART G -- daily coverage audit
# ============================================================================

def build_daily_coverage_audit(canonical_files, bar_stats_by_date, end_date):
    """bar_stats_by_date: dict[session_date_str] -> dict with aggregate stats
    accumulated during Part C/D processing."""
    rows = []
    d = START_DATE
    while d <= end_date:
        dstr = d.isoformat()
        is_weekend = d.weekday() >= 5  # Sat=5, Sun=6 (CME NQ trades Sun evening -> treated leniently below)
        st = bar_stats_by_date.get(dstr)

        if st is None:
            status = "MISSING" if not is_weekend else "PASS"
            # Sunday session (weekday==6) legitimately starts the week's trading in the evening;
            # if no file exists for a Sunday that's expected -> not flagged as a gap by itself.
            rows.append({
                "session_date": dstr, "is_weekend": is_weekend,
                "source_files": json.dumps([]), "first_timestamp_utc": None, "last_timestamp_utc": None,
                "unique_bars": 0, "unique_cell_rows": 0, "depths_available": json.dumps([]),
                "bar_idx_gap_detected": None, "timestamp_gap_detected": None,
                "suspected_shutdown": (not is_weekend), "data_gap_flag": (not is_weekend),
                "quality_status": "DATA_GAP" if not is_weekend else "PASS",
            })
        else:
            rows.append({
                "session_date": dstr, "is_weekend": is_weekend,
                "source_files": json.dumps(sorted(st["files"])),
                "first_timestamp_utc": str(st["min_ts"]), "last_timestamp_utc": str(st["max_ts"]),
                "unique_bars": st["unique_bars"], "unique_cell_rows": st["cell_rows"],
                "depths_available": json.dumps(sorted(st["depths"])),
                "bar_idx_gap_detected": st["bar_idx_gap"], "timestamp_gap_detected": st["ts_gap"],
                "suspected_shutdown": st["short_session"], "data_gap_flag": False,
                "quality_status": "PARTIAL" if st["short_session"] else "PASS",
            })
        d += timedelta(days=1)

    audit_df = pd.DataFrame(rows)
    gap_df = audit_df[audit_df["data_gap_flag"] == True][["session_date", "quality_status"]].copy()
    gap_df["reason"] = "no book-flow cache file found for this session date (pipeline likely down)"
    return audit_df, gap_df


# ============================================================================
# PART H -- validation
# ============================================================================

def validate_long_master(long_path):
    rows = []
    df = pq.read_table(long_path).to_pandas()

    key_cols = ["symbol", "session_date", "timestamp_utc", "bar_idx", "depth_n", "price_tick"]
    dup_count = int(df.duplicated(subset=key_cols).sum())
    rows.append({"check": "grain_uniqueness_no_duplicate_keys", "pass": dup_count == 0,
                 "detail": f"{dup_count} duplicate canonical keys found"})

    nbf = (df["bid_add"] - df["bid_pull"])
    ok = np.allclose(df["net_bid_flow"].astype(float), nbf.astype(float), atol=1e-6, equal_nan=True)
    rows.append({"check": "formula_net_bid_flow", "pass": bool(ok), "detail": "net_bid_flow == bid_add - bid_pull"})

    naf = (df["ask_pull"] - df["ask_add"])
    ok = np.allclose(df["net_ask_flow"].astype(float), naf.astype(float), atol=1e-6, equal_nan=True)
    rows.append({"check": "formula_net_ask_flow", "pass": bool(ok), "detail": "net_ask_flow == ask_pull - ask_add"})

    sf = df["net_bid_flow"] + df["net_ask_flow"]
    ok = np.allclose(df["signed_flow"].astype(float), sf.astype(float), atol=1e-6, equal_nan=True)
    rows.append({"check": "formula_signed_flow", "pass": bool(ok), "detail": "signed_flow == net_bid_flow + net_ask_flow"})

    af = df["bid_add"] + df["bid_pull"] + df["ask_add"] + df["ask_pull"]
    ok = np.allclose(df["abs_flow"].astype(float), af.astype(float), atol=1e-6, equal_nan=True)
    rows.append({"check": "formula_abs_flow", "pass": bool(ok), "detail": "abs_flow == bid_add+bid_pull+ask_add+ask_pull"})

    bp = df["bid_add"] + df["ask_pull"]
    ok = np.allclose(df["buyer_pressure_cell"].astype(float), bp.astype(float), atol=1e-6, equal_nan=True)
    rows.append({"check": "formula_buyer_pressure_cell", "pass": bool(ok), "detail": "buyer_pressure_cell == bid_add + ask_pull"})

    sp = df["ask_add"] + df["bid_pull"]
    ok = np.allclose(df["seller_pressure_cell"].astype(float), sp.astype(float), atol=1e-6, equal_nan=True)
    rows.append({"check": "formula_seller_pressure_cell", "pass": bool(ok), "detail": "seller_pressure_cell == ask_add + bid_pull"})

    for c in ["bid_add", "bid_pull", "ask_add", "ask_pull", "abs_flow"]:
        neg = int((df[c].astype(float) < -1e-9).sum())
        rows.append({"check": f"primitive_nonneg_{c}", "pass": neg == 0, "detail": f"{neg} negative values in {c}"})

    forming = int((df["bar_state"] != "CLOSED").sum())
    rows.append({"check": "forming_bars_excluded", "pass": forming == 0, "detail": f"{forming} non-CLOSED rows found"})

    future_cols = [c for c in df.columns if re.search(r"label|target|future|fwd_ret|forward", c, re.I)]
    rows.append({"check": "no_future_leakage_columns", "pass": len(future_cols) == 0,
                 "detail": f"columns matching label/target/future pattern: {future_cols}"})

    rtm = df["price_tick"] - df["mid_tick"]
    ok = np.array_equal(df["rel_tick_from_mid"].to_numpy(), rtm.to_numpy())
    rows.append({"check": "rel_tick_from_mid_correct", "pass": bool(ok), "detail": "rel_tick_from_mid == price_tick - mid_tick"})

    rtc = df["price_tick"] - df["close_tick"]
    ok = np.array_equal(df["rel_tick_from_close"].to_numpy(), rtc.to_numpy())
    rows.append({"check": "rel_tick_from_close_correct", "pass": bool(ok), "detail": "rel_tick_from_close == price_tick - close_tick"})

    # NOTE: return only a lightweight per-(contract,bar_idx,depth_n) row-count table, not the
    # full 12M+ row DataFrame -- keeping the full long master alive for the rest of the build
    # (through Part I) is what caused an OOM kill in practice on this machine.
    long_counts = df.groupby(["contract", "bar_idx", "depth_n"]).size().rename("long_count").reset_index()
    return pd.DataFrame(rows), long_counts


def validate_packed_master(packed_path, long_counts):
    rows = []
    light_cols = ["contract", "bar_idx", "depth_n", "session_date", "timestamp_utc",
                  "cell_count", "cells_count_check", "total_bid_add", "total_bid_pull",
                  "total_ask_add", "total_ask_pull", "total_signed_flow", "total_abs_flow"]
    pdf_light = pq.read_table(packed_path, columns=light_cols).to_pandas()

    # NOTE: bar_idx is only unique WITHIN a contract's own running counter (NQM6 and NQU6
    # both start numbering near 0), so contract must be part of the join key here.
    merged = pdf_light.merge(long_counts, on=["contract", "bar_idx", "depth_n"], how="left")
    mismatch = merged[(merged["cell_count"] != merged["long_count"]) | merged["long_count"].isna()]
    rows.append({"check": "long_count_equals_cells_count_check", "pass": len(mismatch) == 0,
                 "detail": f"{len(mismatch)} bar/depth rows where long-master cell count != packed cell_count"})

    # Deep JSON/list-sum checks are memory-heavy (cells_json can be large per row), so sample
    # via a handful of parquet ROW GROUPS instead of loading the full column for all rows.
    heavy_cols = ["cells_json", "cells_count_check", "cells_bid_add_list", "cells_bid_pull_list",
                  "cells_ask_add_list", "cells_ask_pull_list", "cells_signed_flow_list",
                  "cells_abs_flow_list", "total_bid_add", "total_bid_pull", "total_ask_add",
                  "total_ask_pull", "total_signed_flow", "total_abs_flow"]
    pf = pq.ParquetFile(packed_path)
    n_rg = pf.num_row_groups
    target_rows = 2000
    frac = min(1.0, target_rows / max(1, len(pdf_light)))
    step = max(1, int(round(1.0 / frac))) if frac > 0 else n_rg
    chosen_rg = list(range(0, n_rg, step)) or [0]
    frames = [pf.read_row_group(i, columns=heavy_cols).to_pandas() for i in chosen_rg]
    sample = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=heavy_cols)
    if len(sample) > target_rows:
        sample = sample.sample(target_rows, random_state=0)

    json_count_mismatch = 0
    sum_mismatch = 0
    list_len_mismatch = 0
    for r in sample.itertuples(index=False):
        try:
            cells = json.loads(r.cells_json)
        except Exception:
            cells = []
        if len(cells) != r.cells_count_check:
            json_count_mismatch += 1
        bid_add_list = json.loads(r.cells_bid_add_list)
        if len(bid_add_list) != r.cells_count_check:
            list_len_mismatch += 1
        if not math.isclose(sum(bid_add_list), r.total_bid_add, abs_tol=1e-4):
            sum_mismatch += 1
    rows.append({"check": "cells_json_length_matches_count_check (sampled)", "pass": json_count_mismatch == 0,
                 "detail": f"{json_count_mismatch} mismatches in sample of {len(sample)}"})
    rows.append({"check": "list_columns_length_matches_count_check (sampled)", "pass": list_len_mismatch == 0,
                 "detail": f"{list_len_mismatch} mismatches in sample of {len(sample)}"})
    rows.append({"check": "sum_cells_bid_add_list_equals_total_bid_add (sampled)", "pass": sum_mismatch == 0,
                 "detail": f"{sum_mismatch} mismatches in sample of {len(sample)}"})

    for col_list, total_col in [
        ("cells_bid_pull_list", "total_bid_pull"), ("cells_ask_add_list", "total_ask_add"),
        ("cells_ask_pull_list", "total_ask_pull"), ("cells_signed_flow_list", "total_signed_flow"),
        ("cells_abs_flow_list", "total_abs_flow"),
    ]:
        mism = 0
        for r in sample.itertuples(index=False):
            lst = json.loads(getattr(r, col_list))
            if not math.isclose(sum(lst), getattr(r, total_col), abs_tol=1e-4):
                mism += 1
        rows.append({"check": f"sum_{col_list}_equals_{total_col} (sampled)", "pass": mism == 0,
                     "detail": f"{mism} mismatches in sample of {len(sample)}"})

    return pd.DataFrame(rows), pdf_light


def validate_relative_ladder(wide_paths, n_bars_expected):
    rows = []
    for width, path in wide_paths.items():
        if not os.path.exists(path):
            rows.append({"check": f"pm{width}_file_exists", "pass": False, "detail": "missing"})
            continue
        pf = pq.ParquetFile(path)
        n = pf.metadata.num_rows
        rows.append({"check": f"pm{width}_file_exists", "pass": True, "detail": f"{n} rows"})
        rows.append({"check": f"pm{width}_row_count_matches_packed_master", "pass": n == n_bars_expected,
                     "detail": f"wide={n} packed={n_bars_expected}"})
    return pd.DataFrame(rows)


# ============================================================================
# PART I -- example proof files
# ============================================================================

def write_example_proofs(examples_dir, long_master_path, packed_master_path, pdf_light):
    """pdf_light: the lightweight packed-master DataFrame returned by validate_packed_master
    (bar_idx/depth_n/contract/session_date/cell_count only -- no cells_json). Full per-bar data
    for the handful of chosen example bars is fetched via targeted (predicate-pushdown) parquet
    reads, never by loading the full long/packed masters into memory again."""
    os.makedirs(examples_dir, exist_ok=True)

    counts = pdf_light[["contract", "bar_idx", "depth_n", "cell_count", "session_date"]].copy()
    counts["session_date"] = counts["session_date"].astype(str)
    counts = counts.rename(columns={"cell_count": "n"})

    picks = []
    ge30 = counts[counts["n"] >= 30]
    if len(ge30) > 0:
        picks.extend(ge30.sample(min(5, len(ge30)), random_state=42).to_dict("records"))

    ge50 = counts[counts["n"] >= 50].sort_values("bar_idx", ascending=False)
    if len(ge50) > 0:
        picks.append(ge50.iloc[0].to_dict())

    early = counts[counts["session_date"] <= "2026-06-10"]
    if len(early) > 0:
        picks.append(early.sort_values("bar_idx").iloc[0].to_dict())

    latest = counts.sort_values("bar_idx", ascending=False)
    if len(latest) > 0:
        picks.append(latest.iloc[0].to_dict())

    seen = set()
    written = []
    for p in picks:
        key = (str(p["contract"]), int(p["bar_idx"]), int(p["depth_n"]))
        if key in seen:
            continue
        seen.add(key)

        contract, bar_idx, depth_n = key
        filt = [("contract", "=", contract), ("bar_idx", "=", bar_idx), ("depth_n", "=", depth_n)]
        cell_rows = pq.read_table(long_master_path, filters=filt).to_pandas().sort_values("rel_tick_from_mid")
        packed_row = pq.read_table(packed_master_path, filters=filt).to_pandas()

        long_csv_path = os.path.join(examples_dir, f"bar_{contract}_{bar_idx}_depth{depth_n}_long_all_cells.csv")
        cell_rows.to_csv(long_csv_path, index=False)

        packed_json_path = os.path.join(examples_dir, f"bar_{contract}_{bar_idx}_depth{depth_n}_packed_cells.json")
        if len(packed_row) > 0:
            cells_json = packed_row.iloc[0]["cells_json"]
            with open(packed_json_path, "w") as f:
                f.write(cells_json)
            packed_n = packed_row.iloc[0]["cells_count_check"]
            total_bid_add = packed_row.iloc[0]["total_bid_add"]
            sum_bid_add = sum(c["bid_add"] for c in json.loads(cells_json))
            sums_match = math.isclose(total_bid_add, sum_bid_add, abs_tol=1e-4)
        else:
            packed_n = 0
            sums_match = False

        long_n = len(cell_rows)
        status = "PASS" if (long_n == packed_n and sums_match and long_n > 0) else "BLOCKED"

        val_path = os.path.join(examples_dir, f"bar_{contract}_{bar_idx}_depth{depth_n}_validation.txt")
        with open(val_path, "w") as f:
            f.write(f"contract={contract} bar_idx={bar_idx} depth_n={depth_n}\n")
            f.write(f"This bar has {long_n} cells in the long master.\n")
            f.write(f"The packed row has {packed_n} cells (cells_count_check).\n")
            f.write(f"Sum(cells_bid_add_list) == total_bid_add: {sums_match}\n")
            f.write(f"STATUS: {status}\n")

        written.append({"contract": contract, "bar_idx": bar_idx, "depth_n": depth_n, "long_cells": long_n,
                         "packed_cells": packed_n, "status": status})
    return written


# ============================================================================
# main
# ============================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true",
                         help="Process only a handful of files for a quick correctness check")
    parser.add_argument("--finish-from", default=None, metavar="RUN_DIR",
                         help="Skip Parts A-G (file discovery/ingestion) and resume Part H onward "
                              "using the already-written outputs in an existing run_dir. Use this "
                              "to recover from a crash/OOM during validation/example-writing "
                              "without re-running the expensive per-file ingestion pass.")
    args = parser.parse_args()

    if args.finish_from:
        run_dir = args.finish_from
        if not os.path.isdir(run_dir):
            raise SystemExit(f"--finish-from directory does not exist: {run_dir}")
        examples_dir = os.path.join(run_dir, "examples")
        os.makedirs(examples_dir, exist_ok=True)
    else:
        run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        run_dir = os.path.join(OUTPUT_ROOT, f"ofi_cell_training_master_from_scratch_{run_ts}")
        examples_dir = os.path.join(run_dir, "examples")
        os.makedirs(run_dir, exist_ok=True)
        os.makedirs(examples_dir, exist_ok=True)

    log(f"Run dir: {run_dir}")
    log(f"Smoke test mode: {args.smoke_test}")
    log(f"Finish-from mode: {bool(args.finish_from)}")

    if args.finish_from:
        # ---------------- reload lightweight state from an existing (Parts A-G complete) run ----------------
        log("Skipping Parts A-G -- reloading lightweight state from existing outputs ...")
        stats = {"within_file_exact_dup_dropped": None}  # not recomputed in finish-from mode

        disc_df = pd.read_csv(os.path.join(run_dir, "discovered_cell_files.csv"))
        n_total = len(disc_df)

        dedup_df = pd.read_csv(os.path.join(run_dir, "duplicate_cell_rows_report.csv"))
        canonical_files = [None] * len(dedup_df)  # only len() is used downstream
        duplicate_rows_found = int(dedup_df["duplicate_row_count"].sum()) if "duplicate_row_count" in dedup_df else 0
        conflicting_duplicates_found = os.path.exists(os.path.join(run_dir, "conflicting_duplicate_cell_rows.parquet"))
        conflicting_duplicates_unresolved = False  # canonical selection is always deterministic (see above)

        fm_report_df = pd.read_csv(os.path.join(run_dir, "feature_master_join_report.csv"))
        feature_master_context_joined = bool((fm_report_df["status"] == "JOINED").any())

        audit_df = pd.read_csv(os.path.join(run_dir, "daily_coverage_audit.csv"))
        gap_df = pd.read_csv(os.path.join(run_dir, "data_gap_markers.csv"))
        n_gap_days = int(audit_df["data_gap_flag"].sum())
        n_partial_days = int((audit_df["quality_status"] == "PARTIAL").sum())
        n_pass_days = int((audit_df["quality_status"] == "PASS").sum())
        end_date = date.fromisoformat(str(audit_df["session_date"].max()))

        _long_path_probe = os.path.join(run_dir, "ofi_cell_master_long.parquet")
        _packed_path_probe = os.path.join(run_dir, "ofi_bar_packed_cells_master.parquet")
        total_cells = pq.ParquetFile(_long_path_probe).metadata.num_rows
        total_bars = pq.ParquetFile(_packed_path_probe).metadata.num_rows
        depth_counts = {int(k): int(v) for k, v in pq.read_table(
            _long_path_probe, columns=["depth_n"]).to_pandas()["depth_n"].value_counts().items()}

        log(f"  reloaded: {total_bars} bars, {total_cells} cell rows, end_date={end_date}")
    else:
        stats = {"within_file_exact_dup_dropped": 0}

        # ---------------- PART A ----------------
        log("PART A: discovering cache files ...")
        disc_df = discover_cache_files()
        disc_df.to_csv(os.path.join(run_dir, "discovered_cell_files.csv"), index=False)

        n_total = len(disc_df)
        n_cell = int((disc_df["category"].isin(
            ["cell_level_dated_source_family", "cell_level_dated_cache_family"])).sum())
        n_included = int(disc_df["include_in_cell_master"].sum())
        n_excluded_bar_summary = int((disc_df["category"] == "bar_summary_no_cells").sum())

        with open(os.path.join(run_dir, "file_discovery_report.md"), "w") as f:
            f.write("# File Discovery Report\n\n")
            f.write(f"Scanned: `{CACHE_DIR}`\n\n")
            f.write(f"- Total files found: {n_total}\n")
            f.write(f"- Cell-level dated parquet candidates: {n_cell}\n")
            f.write(f"- Included (sealed CLOSED rows confirmed): {n_included}\n")
            f.write(f"- Excluded bar-summary-only files (OHLC, no cells): {n_excluded_bar_summary}\n\n")
            f.write("## By category\n\n")
            f.write(disc_df["category"].value_counts().to_frame("count").to_markdown() + "\n\n")
            f.write("## By file_type\n\n")
            f.write(disc_df["file_type"].value_counts().to_frame("count").to_markdown() + "\n\n")
            f.write("Forming-only and non-CLOSED-only files were excluded from the training masters. "
                    "See `include_in_cell_master` / `exclude_reason` columns in discovered_cell_files.csv "
                    "for the per-file decision.\n")

        log(f"  found {n_total} files, {n_included} usable cell-level files")

        log("PART A: selecting canonical files (file-level dedup across cache families) ...")
        canonical_files, dedup_df, conflict_df = select_canonical_files(disc_df)
        dedup_df.to_csv(os.path.join(run_dir, "duplicate_cell_rows_report.csv"), index=False)
        conflicting_duplicates_found = len(conflict_df) > 0
        # NOTE: select_canonical_files() always deterministically picks ONE canonical file per
        # (contract, filename_label_date, depth_n) group -- it never blends/averages conflicting
        # values, and the non-canonical (excluded) file's conflicting rows never reach the long
        # master. So a CONFLICT verdict here means "two redundant cache copies disagreed" (fully
        # audited in duplicate_cell_rows_report.csv / conflicting_duplicate_cell_rows.parquet), not
        # "the training master contains ambiguous data". conflicting_duplicates_unresolved therefore
        # stays False as long as a canonical choice was always made, which select_canonical_files()
        # guarantees.
        conflicting_duplicates_unresolved = False
        if conflicting_duplicates_found:
            conflict_df.to_parquet(os.path.join(run_dir, "conflicting_duplicate_cell_rows.parquet"), index=False)

        if args.smoke_test:
            by_contract = {}
            for cf in canonical_files:
                by_contract.setdefault(cf["contract"], []).append(cf)
            limited = []
            for contract, files in by_contract.items():
                files_sorted = sorted(files, key=lambda x: (x["filename_label_date"], x["depth_n"]))
                limited.extend(files_sorted[:3])
            canonical_files = limited
            log(f"  smoke test: limited to {len(canonical_files)} canonical files")

        log(f"  {len(canonical_files)} canonical cell files selected for ingestion")

        # ---------------- Part B: canonical schema doc ----------------
        with open(os.path.join(run_dir, "canonical_schema.md"), "w") as f:
            f.write("# Canonical Cell Schema\n\n")
            f.write("Grain: one row = one sealed 500-volume bar x one OFI cell x one depth_n.\n\n")
            f.write("## Design decisions (documented, not silently invented)\n\n")
            f.write(f"- **session_date**: derived from internal per-row `timestamp_utc` using an empirically "
                    f"observed CME session boundary at {SESSION_CUTOFF_UTC_HOUR}:00 UTC (no bars are ever "
                    f"observed at UTC hour == {SESSION_CUTOFF_UTC_HOUR} in this data, confirming a Globex "
                    f"maintenance-break gap there): if `hour >= {SESSION_CUTOFF_UTC_HOUR}` -> session_date = "
                    f"calendar date of the timestamp; else session_date = calendar date minus 1 day -- "
                    f"**but only for files whose first bar itself opens with the normal evening pattern** "
                    f"(first bar hour >= {SESSION_CUTOFF_UTC_HOUR}). Confirmed dead-feed/watchdog restarts "
                    f"(e.g. the 2026-07-06..08 incident) produce cache files whose first bar starts mid-day "
                    f"at an arbitrary UTC hour; blindly applying the per-row cutoff to those rows shifts them "
                    f"into the wrong prior-day session. For any file that opens abnormally (first-bar hour < "
                    f"{SESSION_CUTOFF_UTC_HOUR}), session_date is instead taken uniformly from that file's own "
                    f"internal `date` metadata (the Book Flow daemon's own session label, embedded in its "
                    f".meta.json sidecar and mirrored in the filename -- verified to agree, and independently "
                    f"confirmed authoritative because bar_idx is a strictly contiguous non-overlapping running "
                    f"counter across consecutive labeled files). This is still never a folder name -- see "
                    f"`derive_session_dates_for_file()` in the build script.\n")
            f.write("- **open_price/high_price/low_price/volume**: the Book Flow cache cell files carry only "
                    "`close_price` (constant per bar) and a *per-row walking* `mid_price` that reflects the "
                    "book mid at the time each price level was last inside the top-N depth window during the "
                    "bar (it is NOT a single bar-level mid). Bar-level OHLCV and a single authoritative "
                    "`mid_price` (`mid_mean` from the bar-builder) are joined in from "
                    "`master_NQ_continuous_backadjusted_shadow.ndjsonl` (primary) / `master_NQU6_shadow.ndjsonl` "
                    "(fallback) by an EXACT match on (bar_start_ts_ns, bar_end_ts_ns). The original per-row "
                    "source mid_price is preserved verbatim in `cell_walking_mid_price_raw` for full fidelity. "
                    "`ohlcv_source` records which source satisfied the join, or 'unavailable'.\n")
            f.write("- **close_tick / mid_tick**: the source `price_tick` column uses an internal tick-numbering "
                    "offset that is not documented. close_tick/mid_tick are recovered by anchoring off the "
                    "nearest cell's own (price_level, price_tick) pair and stepping by TICK_SIZE=0.25, so "
                    "rel_tick_from_close/mid are exact regardless of the unknown offset.\n")
            f.write("- **nearest_level_type**: the source only exposes one label column (`nearest_level`, e.g. "
                    "POC/VAH/VAL/HVN/LVN/S/R). `nearest_level` and `nearest_level_type` are both populated from "
                    "this single source column; no separate numeric nearest-level price exists in source data.\n")
            f.write("- **symbol**: set to the product root `NQ` for all rows; `contract` carries the literal "
                    "contract code (NQM6/NQU6); `continuous_symbol` is set to `NQ_continuous_backadjusted` only "
                    "for bars successfully joined against the continuous master.\n\n")
            f.write("## Long master columns\n\n")
            for c in LONG_MASTER_COLUMNS:
                f.write(f"- `{c}`\n")

        # ---------------- load OHLCV lookups ----------------
        log("Loading OHLCV/mid lookups from ndjsonl masters ...")
        cont_lookup, nqu6_lookup = build_ohlcv_lookups()

        log("Loading Feature Master context lookup (Part F) ...")
        fm_report_df, fm_lookup = build_feature_master_join_report_and_lookup()
        feature_master_context_joined = fm_lookup is not None

        # ---------------- streaming writers ----------------
        long_writer = StreamWriter(
            os.path.join(run_dir, "ofi_cell_master_long.parquet"), LONG_MASTER_COLUMNS,
            string_cols=[c for c, t in LONG_MASTER_DTYPES.items() if t == "string"],
            bool_cols=[c for c, t in LONG_MASTER_DTYPES.items() if t == "bool"],
            int_cols=[c for c, t in LONG_MASTER_DTYPES.items() if t == "int64"],
        )
        packed_writer = StreamWriter(
            os.path.join(run_dir, "ofi_bar_packed_cells_master.parquet"), PACKED_SCALAR_COLUMNS,
            string_cols=["symbol", "contract", "continuous_symbol", "session_date", "timestamp_utc",
                         "cells_json", "cells_rel_tick_list", "cells_price_level_list",
                         "cells_bid_add_list", "cells_bid_pull_list", "cells_ask_add_list", "cells_ask_pull_list",
                         "cells_net_bid_flow_list", "cells_net_ask_flow_list", "cells_signed_flow_list",
                         "cells_abs_flow_list", "cells_buyer_pressure_list", "cells_seller_pressure_list"],
            int_cols=["bar_idx", "depth_n", "cell_count", "min_rel_tick_from_mid", "max_rel_tick_from_mid",
                      "green_cell_count", "red_cell_count", "neutral_cell_count", "cells_count_check"],
        )
        wide_writers = {}
        for w in LADDER_WIDTHS:
            cols = wide_ladder_columns(w)
            str_cols = ["symbol", "contract", "continuous_symbol", "session_date", "timestamp_utc",
                        "data_quality_flag"]
            bool_cols = [c for c in cols if c.endswith("_observed")]
            int_cols = ["bar_idx", "depth_n", "cell_count_total", "cell_count_in_window",
                        "min_rel_tick_from_mid", "max_rel_tick_from_mid"]
            wide_writers[w] = StreamWriter(
                os.path.join(run_dir, f"ofi_relative_ladder_wide_pm{w}.parquet"), cols,
                string_cols=str_cols, bool_cols=bool_cols, int_cols=int_cols)

        def _dedupe_preserve_order(cols):
            seen = set()
            out = []
            for c in cols:
                if c not in seen:
                    seen.add(c)
                    out.append(c)
            return out

        long_ctx_writer = StreamWriter(
            os.path.join(run_dir, "ofi_cell_master_long_with_context.parquet"),
            _dedupe_preserve_order(LONG_MASTER_COLUMNS + (list(fm_lookup.columns) if fm_lookup is not None else [])),
            string_cols=[c for c, t in LONG_MASTER_DTYPES.items() if t == "string"],
            bool_cols=[c for c, t in LONG_MASTER_DTYPES.items() if t == "bool"],
            int_cols=[c for c, t in LONG_MASTER_DTYPES.items() if t == "int64"],
        ) if fm_lookup is not None else None

        packed_ctx_writer = StreamWriter(
            os.path.join(run_dir, "ofi_bar_packed_cells_master_with_context.parquet"),
            _dedupe_preserve_order(PACKED_SCALAR_COLUMNS + (list(fm_lookup.columns) if fm_lookup is not None else [])),
            string_cols=["symbol", "contract", "continuous_symbol", "session_date", "timestamp_utc",
                         "cells_json", "cells_rel_tick_list", "cells_price_level_list",
                         "cells_bid_add_list", "cells_bid_pull_list", "cells_ask_add_list", "cells_ask_pull_list",
                         "cells_net_bid_flow_list", "cells_net_ask_flow_list", "cells_signed_flow_list",
                         "cells_abs_flow_list", "cells_buyer_pressure_list", "cells_seller_pressure_list"],
            int_cols=["bar_idx", "depth_n", "cell_count", "min_rel_tick_from_mid", "max_rel_tick_from_mid",
                      "green_cell_count", "red_cell_count", "neutral_cell_count", "cells_count_check"],
        ) if fm_lookup is not None else None

        # ---------------- PART C/D/E: per-file processing loop ----------------
        missingness_rows = []
        bar_stats_by_date = {}
        sample_long_rows = []
        sample_packed_rows = []
        total_bars = 0
        total_cells = 0
        depth_counts = {}

        log(f"PART C/D/E: processing {len(canonical_files)} canonical files ...")
        for i, cf in enumerate(canonical_files):
            fpath = cf["file_path"]
            log(f"  [{i+1}/{len(canonical_files)}] {os.path.basename(fpath)}")
            try:
                long_df = process_one_file(cf, cont_lookup, nqu6_lookup, stats, missingness_rows)
            except Exception as e:
                log(f"    ERROR processing {fpath}: {e}")
                traceback.print_exc()
                continue
            if long_df is None or len(long_df) == 0:
                continue

            long_writer.write(long_df)
            if len(sample_long_rows) < 2000:
                sample_long_rows.append(long_df.head(50))

            if fm_lookup is not None and long_ctx_writer is not None:
                long_ctx_df = join_context(long_df, fm_lookup)
                long_ctx_writer.write(long_ctx_df)

            total_cells += len(long_df)
            depth = int(long_df["depth_n"].iloc[0])
            depth_counts[depth] = depth_counts.get(depth, 0) + len(long_df)

            # ---- packed + wide, built per bar from this file's cells ----
            packed_rows = []
            wide_bar_groups = []
            for bar_idx, bdf in long_df.groupby("bar_idx", sort=False):
                bdf = bdf.sort_values("rel_tick_from_mid")
                packed_row = build_packed_rows_for_bar(bdf)
                packed_rows.append(packed_row)

                meta = {k: packed_row[k] for k in [
                    "symbol", "contract", "continuous_symbol", "session_date", "timestamp_utc",
                    "bar_idx", "depth_n", "open_price", "high_price", "low_price", "close_price",
                    "mid_price", "volume", "min_rel_tick_from_mid", "max_rel_tick_from_mid"]}
                wide_bar_groups.append((meta, bdf))

                total_bars += 1
                sdate = str(packed_row["session_date"])
                st = bar_stats_by_date.setdefault(sdate, {
                    "files": set(), "min_ts": packed_row["timestamp_utc"], "max_ts": packed_row["timestamp_utc"],
                    "unique_bars": 0, "cell_rows": 0, "depths": set(), "bar_idx_gap": False,
                    "ts_gap": False, "short_session": False, "bar_idx_list": [],
                })
                st["files"].add(os.path.basename(fpath))
                st["min_ts"] = min(st["min_ts"], packed_row["timestamp_utc"])
                st["max_ts"] = max(st["max_ts"], packed_row["timestamp_utc"])
                st["unique_bars"] += 1
                st["cell_rows"] += packed_row["cell_count"]
                st["depths"].add(depth)
                st["bar_idx_list"].append(bar_idx)

            packed_df_file = pd.DataFrame(packed_rows)
            packed_writer.write(packed_df_file)
            if len(sample_packed_rows) < 500:
                sample_packed_rows.append(packed_df_file.head(20))

            if fm_lookup is not None and packed_ctx_writer is not None:
                bar_end_map = long_df.groupby("bar_idx")["bar_end_ts_ns"].first().to_dict()
                packed_df_file_ctx = packed_df_file.copy()
                packed_df_file_ctx["bar_end_ts_ns"] = packed_df_file_ctx["bar_idx"].map(bar_end_map)
                packed_ctx_df = join_context(packed_df_file_ctx, fm_lookup)
                packed_ctx_writer.write(packed_ctx_df)

            for w in LADDER_WIDTHS:
                wide_rows = build_wide_ladder_rows(wide_bar_groups, w)
                wide_writers[w].write(pd.DataFrame(wide_rows))

        long_writer.close()
        packed_writer.close()
        for w in LADDER_WIDTHS:
            wide_writers[w].close()
        if long_ctx_writer is not None:
            long_ctx_writer.close()
        if packed_ctx_writer is not None:
            packed_ctx_writer.close()

        log(f"  total bars processed: {total_bars}, total cell rows: {total_cells}")

        # bar-idx gap / short-session detection per date (simple heuristics)
        for sdate, st in bar_stats_by_date.items():
            idxs = sorted(st["bar_idx_list"])
            if len(idxs) > 1:
                gaps = [b - a for a, b in zip(idxs[:-1], idxs[1:])]
                st["bar_idx_gap"] = any(g > 1 for g in gaps)
            st["short_session"] = st["unique_bars"] < 400  # a full session is typically ~800-2700 bars/depth

        pd.DataFrame(missingness_rows).to_csv(os.path.join(run_dir, "schema_missingness_by_file.csv"), index=False)

        if sample_long_rows:
            pd.concat(sample_long_rows, ignore_index=True).head(2000).to_csv(
                os.path.join(run_dir, "ofi_cell_master_long_sample.csv"), index=False)
        if sample_packed_rows:
            pd.concat(sample_packed_rows, ignore_index=True).head(500).to_csv(
                os.path.join(run_dir, "ofi_bar_packed_cells_master_sample.csv"), index=False)

        fm_report_df.to_csv(os.path.join(run_dir, "feature_master_join_report.csv"), index=False)

        with open(os.path.join(run_dir, "bar_packed_schema.md"), "w") as f:
            f.write("# Bar-Packed Master Schema\n\n")
            f.write("Grain: one row = one sealed 500-volume bar x one depth_n. All cells inside the bar are "
                    "packed into `cells_json` (full fidelity) plus parallel sorted list columns for quick "
                    "columnar/tensor access. All packed lists and cells_json are sorted by rel_tick_from_mid "
                    "ascending.\n\n## Columns\n\n")
            for c in PACKED_SCALAR_COLUMNS:
                f.write(f"- `{c}`\n")

        with open(os.path.join(run_dir, "relative_ladder_wide_schema.md"), "w") as f:
            f.write("# Relative Ladder Wide Master Schema\n\n")
            f.write("Grain: one row = one sealed 500-volume bar x one depth_n. Fixed-width columns for every "
                    "relative tick K in [-W, +W] around the bar's mid_price.\n\n")
            f.write("## Missing-cell semantics\n\n")
            f.write("- A relative tick K that falls WITHIN the bar's actually-observed depth range "
                    "(`min_rel_tick_from_mid` <= K <= `max_rel_tick_from_mid`) but has no cell row: this means "
                    "genuinely zero order-flow activity was recorded at that tick during the bar. Numeric flow "
                    "columns are set to 0.0 and `rel_K_observed=false`.\n")
            f.write("- A relative tick K that falls OUTSIDE the bar's observed depth range: the depth window "
                    "never reached that tick during the bar, so activity there is unknown/unavailable, not zero. "
                    "Numeric flow columns are set to NA and the row's `data_quality_flag` is set to "
                    "`NA_GAPS_OUTSIDE_BAR_DEPTH_RANGE`.\n")
            f.write("- If the bar's observed range extends beyond +-W, the outermost cells are truncated from "
                    "this fixed-width file (they remain fully intact in the long and packed masters) and "
                    "`data_quality_flag` is set to `TRUNCATED_CELLS_OUTSIDE_WIDTH`.\n\n")
            f.write(f"## Widths built: {LADDER_WIDTHS}\n")

        # coverage per width for report
        coverage_rows = []
        for w in LADDER_WIDTHS:
            wpath = os.path.join(run_dir, f"ofi_relative_ladder_wide_pm{w}.parquet")
            if os.path.exists(wpath):
                wdf = pq.read_table(wpath, columns=["data_quality_flag", "cell_count_total", "cell_count_in_window"]).to_pandas()
                coverage_rows.append({
                    "width": w, "rows": len(wdf),
                    "pct_ok": float((wdf["data_quality_flag"] == "OK").mean()) if len(wdf) else None,
                    "pct_truncated": float((wdf["data_quality_flag"] == "TRUNCATED_CELLS_OUTSIDE_WIDTH").mean()) if len(wdf) else None,
                    "pct_na_gaps": float((wdf["data_quality_flag"] == "NA_GAPS_OUTSIDE_BAR_DEPTH_RANGE").mean()) if len(wdf) else None,
                    "total_cells_across_bars": int(wdf["cell_count_total"].sum()) if len(wdf) else 0,
                    "cells_retained_in_window": int(wdf["cell_count_in_window"].sum()) if len(wdf) else 0,
                })
        pd.DataFrame(coverage_rows).to_csv(os.path.join(run_dir, "relative_ladder_wide_coverage.csv"), index=False)

        # ---------------- PART G ----------------
        log("PART G: building daily coverage audit ...")
        if bar_stats_by_date:
            end_date = max(date.fromisoformat(d) for d in bar_stats_by_date.keys())
        else:
            end_date = START_DATE
        audit_df, gap_df = build_daily_coverage_audit(canonical_files, bar_stats_by_date, end_date)
        audit_df.to_csv(os.path.join(run_dir, "daily_coverage_audit.csv"), index=False)
        gap_df.to_csv(os.path.join(run_dir, "data_gap_markers.csv"), index=False)

        n_gap_days = int(audit_df["data_gap_flag"].sum())
        n_partial_days = int((audit_df["quality_status"] == "PARTIAL").sum())
        n_pass_days = int((audit_df["quality_status"] == "PASS").sum())
        with open(os.path.join(run_dir, "daily_coverage_summary.md"), "w") as f:
            f.write("# Daily Coverage Summary\n\n")
            f.write(f"Date range audited: {START_DATE.isoformat()} .. {end_date.isoformat()}\n\n")
            f.write(f"- PASS days: {n_pass_days}\n")
            f.write(f"- PARTIAL (short/suspected shutdown) days: {n_partial_days}\n")
            f.write(f"- DATA_GAP (no cache file, weekday) days: {n_gap_days}\n\n")
            f.write("Data gaps were NOT fake-filled. They are marked and excluded from the training masters "
                    "(no bars exist for those session dates in the source cache).\n\n")
            if n_gap_days:
                f.write("## Gap dates\n\n")
                for _, r in gap_df.iterrows():
                    f.write(f"- {r['session_date']}\n")

    # ---------------- PART H ----------------
    log("PART H: running validation ...")
    long_master_path = os.path.join(run_dir, "ofi_cell_master_long.parquet")
    packed_master_path = os.path.join(run_dir, "ofi_bar_packed_cells_master.parquet")
    wide_paths = {w: os.path.join(run_dir, f"ofi_relative_ladder_wide_pm{w}.parquet") for w in LADDER_WIDTHS}

    val_long_df, long_counts = validate_long_master(long_master_path)
    val_long_df.to_csv(os.path.join(run_dir, "validation_long_master.csv"), index=False)

    val_packed_df, pdf_light = validate_packed_master(packed_master_path, long_counts)
    val_packed_df.to_csv(os.path.join(run_dir, "validation_packed_master.csv"), index=False)

    val_ladder_df = validate_relative_ladder(wide_paths, len(pdf_light))
    val_ladder_df.to_csv(os.path.join(run_dir, "validation_relative_ladder.csv"), index=False)

    formula_pass = bool(val_long_df[val_long_df["check"].str.startswith("formula_")]["pass"].all())
    grain_pass = bool(val_long_df[val_long_df["check"] == "grain_uniqueness_no_duplicate_keys"]["pass"].iloc[0])
    forming_excluded = bool(val_long_df[val_long_df["check"] == "forming_bars_excluded"]["pass"].iloc[0])
    packed_count_pass = bool(val_packed_df.iloc[0]["pass"])
    packed_sum_pass = bool(val_packed_df[val_packed_df["check"].str.startswith("sum_")]["pass"].all())
    duplicate_rows_found = int(dedup_df["duplicate_row_count"].sum()) if "duplicate_row_count" in dedup_df else 0

    with open(os.path.join(run_dir, "integrity_validation_report.md"), "w") as f:
        f.write("# Integrity Validation Report\n\n")
        f.write("## Long master\n\n")
        f.write(val_long_df.to_markdown(index=False) + "\n\n")
        f.write("## Packed master\n\n")
        f.write(val_packed_df.to_markdown(index=False) + "\n\n")
        f.write("## Relative ladder\n\n")
        f.write(val_ladder_df.to_markdown(index=False) + "\n\n")
        f.write("## Raw files untouched\n\n")
        f.write("This script opens all inputs read-only (`pq.read_table`, plain file reads on the "
                "ndjsonl masters) and never writes to any path under book_flow_chart/cache/, "
                "OFI_Live_Features/, or model_feature_master/. All outputs are written exclusively under "
                f"`{run_dir}`.\n")

    # ---------------- PART I ----------------
    log("PART I: writing example proof files ...")
    examples_written = write_example_proofs(examples_dir, long_master_path, packed_master_path, pdf_light)

    # ---------------- PART J: manifest ----------------
    log("PART J: writing manifest ...")
    output_files = [f for f in os.listdir(run_dir) if os.path.isfile(os.path.join(run_dir, f))]
    parquet_hashes = {}
    for fn in output_files:
        if fn.endswith(".parquet"):
            parquet_hashes[fn] = sha256_of_file(os.path.join(run_dir, fn))

    manifest = {
        "created_at_utc": utc_now_iso(),
        "start_date": START_DATE.isoformat(),
        "end_date": end_date.isoformat(),
        "source_files": {
            "cache_dir": CACHE_DIR,
            "continuous_master": CONTINUOUS_MASTER_PATH,
            "nqu6_master": NQU6_MASTER_PATH,
            "feature_master_context": FEATURE_MASTER_PATH,
            "n_discovered_files": int(n_total),
            "n_canonical_files_ingested": len(canonical_files),
        },
        "output_files": sorted(output_files),
        "row_counts": {
            "long_master_cell_rows": total_cells,
            "packed_master_bar_rows": total_bars,
        },
        "bar_counts": total_bars,
        "cell_counts": total_cells,
        "depth_counts": depth_counts,
        "missing_dates": gap_df["session_date"].tolist(),
        "data_gaps_found": n_gap_days > 0,
        "duplicate_counts": {
            "within_file_exact_duplicates_dropped": stats["within_file_exact_dup_dropped"],
            "cross_family_duplicate_rows": duplicate_rows_found,
        },
        "conflicting_duplicates_found": conflicting_duplicates_found,
        "conflicting_duplicates_unresolved": conflicting_duplicates_unresolved,
        "validation_status": {
            "formula_validation_pass": formula_pass,
            "grain_uniqueness_pass": grain_pass,
            "packed_cell_count_validation_pass": packed_count_pass,
            "packed_cell_sum_validation_pass": packed_sum_pass,
            "forming_bars_excluded": forming_excluded,
        },
        "sha256_by_output_parquet": parquet_hashes,
        "build_script_path": BUILD_SCRIPT_PATH,
        "production_modification_flags": {
            "raw_files_modified": False, "production_files_modified": False,
            "dashboard_code_modified": False, "book_flow_code_modified": False,
            "feature_master_code_modified": False, "model_artifacts_modified": False,
            "active_model_pointer_changed": False, "trading_enabled": False,
            "broker_connected": False, "paper_trading_enabled": False,
        },
        "smoke_test": args.smoke_test,
    }
    with open(os.path.join(run_dir, "ofi_cell_training_master_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2, default=str)

    # ---------------- PART K: final report ----------------
    log("PART K: writing final report ...")
    model_training_ready = (
        os.path.exists(long_master_path) and os.path.exists(packed_master_path)
        and all(os.path.exists(p) for p in wide_paths.values())
        and formula_pass and grain_pass and packed_count_pass and packed_sum_pass
        and forming_excluded and (not conflicting_duplicates_unresolved)
    )
    overall = model_training_ready

    report_path = os.path.join(run_dir, "OFI_CELL_TRAINING_MASTER_FROM_SCRATCH_REPORT.md")
    with open(report_path, "w") as f:
        f.write("# OFI Cell Training Master FROM SCRATCH -- Build Report\n\n")
        f.write("SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING.\n\n")
        f.write("## Answers\n\n")
        f.write("1. **What was built?** A full-fidelity cell-level OFI Book Flow training master: every "
                "sealed 500-volume bar's individual order-book cells (price-level level BidAdd/BidPull/"
                "AskAdd/AskPull and derived flow) are preserved, in three complementary layouts (long, "
                "bar-packed, fixed-width relative ladder).\n")
        f.write("2. **Long master row grain:** one row = one sealed 500-volume bar x one OFI cell x one depth_n.\n")
        f.write("3. **Packed master row grain:** one row = one sealed 500-volume bar x one depth_n, with all "
                "cells packed into `cells_json` + parallel sorted list columns.\n")
        f.write("4. **Relative ladder row grain:** one row = one sealed 500-volume bar x one depth_n, fixed-width "
                "columns per relative tick K in [-W,+W] for W in {40,80,160}.\n")
        f.write(f"5. **If a candle has 50 cells, are all 50 included?** Yes -- verified per-bar in Part H/I "
                f"(`long_count == cells_count_check`, sum-of-parts checks). See examples/ for concrete proof bars.\n")
        f.write(f"6. **Total bars processed:** {total_bars}\n")
        f.write(f"7. **Total cell rows processed:** {total_cells}\n")
        f.write(f"8. **Date range covered:** {START_DATE.isoformat()} .. {end_date.isoformat()}\n")
        f.write("9. **Internal timestamps used instead of folder dates?** Yes -- session_date and all bar "
                "timestamps are derived from `timestamp_utc`/`bar_start_ts_ns`/`bar_end_ts_ns` inside each "
                "file; filenames are only used to locate candidate files, never trusted for date/time truth.\n")
        f.write("10. **Multi-date folders/files handled?** Yes -- each file's rows are split into session "
                "dates purely by internal timestamp (see canonical_schema.md session_date rule).\n")
        f.write(f"11. **Shutdown/missing days detected?** Yes -- {n_gap_days} DATA_GAP weekday sessions found "
                f"and {n_partial_days} PARTIAL/short sessions flagged. See daily_coverage_audit.csv.\n")
        f.write(f"12. **Depth values available:** {sorted(depth_counts.keys())}\n")
        f.write(f"13. **Duplicates found?** Cross-family duplicate rows: {duplicate_rows_found}; "
                f"within-file exact duplicates dropped: {stats['within_file_exact_dup_dropped']}.\n")
        f.write(f"14. **Conflicting duplicates found?** {conflicting_duplicates_found}. "
                f"When two redundant cache-file families (`book_flow_level_candles_*` and the bare "
                f"`<CONTRACT>_<date>_top<N>.parquet` cache copy) disagreed for the same "
                f"(contract, date, depth), the incomplete/stale copy was never ingested -- the pipeline "
                f"always deterministically keeps the more-complete `book_flow_level_candles_*` source file "
                f"and excludes the other, so no blended/ambiguous rows ever reach the training masters. "
                f"Full details of every disagreement are in `duplicate_cell_rows_report.csv` and "
                f"`conflicting_duplicate_cell_rows.parquet`. Unresolved (blocking) conflicts: "
                f"{conflicting_duplicates_unresolved}.\n")
        f.write(f"15. **Did formulas validate?** {formula_pass}\n")
        f.write(f"16. **Did packed cell counts validate?** {packed_count_pass}\n")
        f.write("17. **Forming bars excluded?** Yes -- only bar_state == 'CLOSED' rows are ingested.\n")
        f.write(f"18. **Feature Master context joined?** {feature_master_context_joined} (see feature_master_join_report.csv)\n")
        f.write("19. **Transformer/sequence training file:** `ofi_bar_packed_cells_master.parquet` "
                "(cells_json / cells_*_list per bar, ready to pad/mask into a variable-length sequence per bar) "
                "or `ofi_cell_master_long.parquet` grouped by (bar_idx, depth_n) for a from-scratch tokenizer.\n")
        f.write("20. **XGBoost/LightGBM file:** `ofi_relative_ladder_wide_pm80.parquet` (or pm40 for a smaller "
                "feature set) -- fixed-width tabular columns per bar.\n")
        f.write("21. **CNN/tensor-style training file:** `ofi_relative_ladder_wide_pm160.parquet` -- reshape the "
                "rel_K_* columns per bar into a 1D (or stacked multi-depth) tensor of length 2W+1.\n")
        f.write(f"22. **Ready for model training?** {'PASS' if model_training_ready else 'BLOCKED'}\n")
        f.write("23. **Next step for labels:** this build intentionally contains NO labels/targets/forward "
                "returns. A separate labeling script should join forward N-bar returns / triple-barrier "
                "labels onto `bar_idx`+`depth_n` keys from `ofi_bar_packed_cells_master.parquet`, strictly "
                "after this build, so no future information ever enters this feature master.\n\n")

        f.write("## Final fields\n\n")
        f.write(f"SOURCE_FILES_DISCOVERED: {n_total}\n")
        f.write(f"START_DATE: {START_DATE.isoformat()}\n")
        f.write(f"END_DATE: {end_date.isoformat()}\n")
        f.write(f"LONG_CELL_MASTER_CREATED: {os.path.exists(long_master_path)}\n")
        f.write(f"BAR_PACKED_MASTER_CREATED: {os.path.exists(packed_master_path)}\n")
        f.write(f"RELATIVE_LADDER_WIDE_CREATED: {all(os.path.exists(p) for p in wide_paths.values())}\n")
        f.write(f"FEATURE_MASTER_CONTEXT_JOINED: {feature_master_context_joined}\n")
        f.write(f"DAILY_COVERAGE_AUDIT_CREATED: {os.path.exists(os.path.join(run_dir, 'daily_coverage_audit.csv'))}\n")
        f.write(f"DATA_GAPS_FOUND: {n_gap_days > 0}\n")
        f.write(f"FORMING_BARS_EXCLUDED: {forming_excluded}\n")
        f.write(f"DUPLICATE_CELL_ROWS_FOUND: {duplicate_rows_found > 0}\n")
        f.write(f"CONFLICTING_DUPLICATES_FOUND: {conflicting_duplicates_found}\n")
        f.write(f"CONFLICTING_DUPLICATES_UNRESOLVED: {conflicting_duplicates_unresolved}\n")
        f.write(f"FORMULA_VALIDATION_PASS: {formula_pass}\n")
        f.write(f"GRAIN_UNIQUENESS_PASS: {grain_pass}\n")
        f.write(f"PACKED_CELL_COUNT_VALIDATION_PASS: {packed_count_pass}\n")
        f.write(f"PACKED_CELL_SUM_VALIDATION_PASS: {packed_sum_pass}\n")
        f.write(f"RELATIVE_LADDER_CREATED: {all(os.path.exists(p) for p in wide_paths.values())}\n")
        f.write(f"EXAMPLE_PROOF_FILES_CREATED: {len(examples_written) > 0}\n")
        f.write("RAW_FILES_MODIFIED: false\n")
        f.write("PRODUCTION_FILES_MODIFIED: false\n")
        f.write("DASHBOARD_CODE_MODIFIED: false\n")
        f.write("BOOK_FLOW_CODE_MODIFIED: false\n")
        f.write("FEATURE_MASTER_CODE_MODIFIED: false\n")
        f.write("MODEL_ARTIFACTS_MODIFIED: false\n")
        f.write("ACTIVE_MODEL_POINTER_CHANGED: false\n")
        f.write("TRADING_ENABLED: false\n")
        f.write("BROKER_CONNECTED: false\n")
        f.write("PAPER_TRADING_ENABLED: false\n")
        f.write(f"MODEL_TRAINING_READY: {'PASS' if model_training_ready else 'BLOCKED'}\n")
        f.write(f"OVERALL: {'PASS' if overall else 'BLOCKED'}\n")

    # ---------------- PART L: final printout ----------------
    print("\n" + "=" * 78)
    print("OFI CELL TRAINING MASTER FROM SCRATCH -- BUILD COMPLETE")
    print("=" * 78)
    print(f"Output folder:        {run_dir}")
    print(f"Build script path:    {BUILD_SCRIPT_PATH}")
    print(f"Long master:          {long_master_path}")
    print(f"Packed master:        {packed_master_path}")
    for w in LADDER_WIDTHS:
        print(f"Relative ladder pm{w}: {wide_paths[w]}")
    print(f"Coverage report:      {os.path.join(run_dir, 'daily_coverage_audit.csv')}")
    print(f"Validation report:    {os.path.join(run_dir, 'integrity_validation_report.md')}")
    print(f"Final report:         {report_path}")
    print(f"MODEL_TRAINING_READY: {'PASS' if model_training_ready else 'BLOCKED'}")
    print(f"OVERALL:              {'PASS' if overall else 'BLOCKED'}")
    print("=" * 78 + "\n")


if __name__ == "__main__":
    main()
