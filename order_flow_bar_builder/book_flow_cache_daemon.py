#!/usr/bin/env python3
"""Background compact-cache daemon for Book Flow Chart V3.

SHADOW/RESEARCH ONLY. This process owns raw/cache computation for the V3
price-axis book-flow level cells. The GUI reads the compact parquet outputs
only and never parses raw Rithmic files during refresh.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

import book_flow_lib as bfl
import build_book_flow_level_cache as level_cache


DEPTH_CHOICES = [5, 10, 15, 20]
COMPACT_COLUMNS = [
    "bar_idx", "timestamp_utc", "bar_start_ts_ns", "bar_end_ts_ns",
    "price_level", "price_tick", "depth_n", "side_zone",
    "signed_flow", "abs_flow", "bid_add", "bid_pull", "ask_add", "ask_pull",
    "net_bid_flow", "net_ask_flow", "trade_volume_at_price",
    "buy_trade_volume_at_price", "sell_trade_volume_at_price",
    "close_price", "mid_price", "nearest_level", "distance_to_nearest_level",
    "bar_state",
]
RUN_FLAG = True
HEARTBEAT_PATH = bfl.CACHE_DIR / "book_flow_cache_heartbeat.json"
FORMING_CACHE_DIR = bfl.CACHE_DIR / "forming"

# No-change skip: track last-written state per (symbol, date, depth) to avoid
# rewriting forming parquet every 350ms when no new events have arrived.
# Key: (symbol, date, depth_int) → (events_count, bar_actual_idx, bar_state)
_forming_prev_state: dict = {}
_forming_prev_rows: dict = {}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mtime_utc(path: Path) -> Optional[str]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def _candidate_raw_dates(symbol: str) -> list[str]:
    out: list[str] = []
    try:
        for p in bfl.RAW_BASE.iterdir():
            if p.is_dir() and (p / symbol).is_dir():
                out.append(p.name)
    except OSError:
        pass
    return sorted(set(out))


def _candidate_feature_dates(symbol: str) -> list[str]:
    out: list[str] = []
    try:
        for p in bfl.FEATURES_BASE.iterdir():
            if p.is_dir() and (p / f"{symbol}_vol500.ndjsonl").exists():
                out.append(p.name)
    except OSError:
        pass
    return sorted(set(out))


def latest_dates(symbol: str = "NQU6") -> dict:
    raw_dates = _candidate_raw_dates(symbol)
    feature_dates = _candidate_feature_dates(symbol)
    both = sorted(set(raw_dates) & set(feature_dates))
    active = both[-1] if both else (feature_dates[-1] if feature_dates else (raw_dates[-1] if raw_dates else "2026-06-15"))
    return {
        "latest_raw_date": raw_dates[-1] if raw_dates else None,
        "latest_feature_date": feature_dates[-1] if feature_dates else None,
        "active_date": active,
        "raw_dates": raw_dates,
        "feature_dates": feature_dates,
    }


def _default_date() -> str:
    detected = latest_dates("NQU6").get("active_date")
    if detected:
        return str(detected)
    return "2026-06-15"


def resolve_date(date: str) -> str:
    val = str(date).strip()
    return _default_date() if val.lower().replace("_", " ") in ("latest", "live latest") else val


def heartbeat_path() -> Path:
    return HEARTBEAT_PATH


def load_heartbeat() -> dict:
    try:
        return json.loads(HEARTBEAT_PATH.read_text())
    except Exception:
        return {}


def compact_cache_path(symbol: str, date: str, depth: int) -> Path:
    return bfl.CACHE_DIR / f"{symbol}_{date}_top{int(depth)}.parquet"


def compact_cache_meta_path(symbol: str, date: str, depth: int) -> Path:
    return compact_cache_path(symbol, date, depth).with_suffix(".meta.json")


def forming_cache_path(symbol: str, date: str, depth: int) -> Path:
    return FORMING_CACHE_DIR / f"{symbol}_{date}_top{int(depth)}_forming.parquet"


def forming_cache_meta_path(symbol: str, date: str, depth: int) -> Path:
    return FORMING_CACHE_DIR / f"{symbol}_{date}_top{int(depth)}_forming.meta.json"


def _file_sig(path: Path) -> tuple[int, int]:
    try:
        st = path.stat()
        return int(st.st_mtime_ns), int(st.st_size)
    except OSError:
        return 0, 0


def _load_bars(symbol: str, date: str) -> pd.DataFrame:
    return bfl.load_vol500_bars(bfl.FEATURES_BASE / date / f"{symbol}_vol500.ndjsonl")


def _latest_bar_info(symbol: str, date: str) -> dict:
    bars = _load_bars(symbol, date)
    if bars.empty:
        return {"latest_bar_idx": None, "latest_bar_timestamp_utc": None}
    last = bars.iloc[-1]
    return {
        "latest_bar_idx": int(last.get("bar_index", len(bars) - 1)),
        "latest_bar_timestamp_utc": str(last.get("timestamp_utc") or last.get("timestamp") or ""),
        "feature_rows": int(len(bars)),
    }


def _utc_ts(value) -> Optional[pd.Timestamp]:
    try:
        ts = pd.to_datetime(value, utc=True)
    except Exception:
        return None
    if pd.isna(ts):
        return None
    return ts


def _ts_iso(value) -> Optional[str]:
    ts = _utc_ts(value)
    return None if ts is None else str(ts)


def _lag_seconds(later, earlier) -> Optional[float]:
    later_ts = _utc_ts(later)
    earlier_ts = _utc_ts(earlier)
    if later_ts is None or earlier_ts is None:
        return None
    return round(float((later_ts - earlier_ts).total_seconds()), 6)


def _cache_diagnostics(symbol: str, date: str, depth: int, bars: pd.DataFrame, result: dict) -> dict:
    latest_idx = None
    latest_ts = None
    if not bars.empty:
        latest_idx = int(bars["bar_index"].iloc[-1])
        latest_ts = str(bars.get("timestamp_utc", bars.get("timestamp")).iloc[-1])

    out = {
        "feature_rows": int(len(bars)),
        "latest_feature_bar_idx": latest_idx,
        "latest_feature_timestamp_utc": latest_ts,
        "latest_closed_bar_idx": latest_idx,
        "latest_closed_timestamp_utc": latest_ts,
        "cache_rows": 0,
        "unique_cached_bars": 0,
        "cache_min_bar_idx": None,
        "cache_max_bar_idx": None,
        "cache_min_timestamp_utc": None,
        "cache_max_timestamp_utc": None,
        "cache_last_timestamp_utc": None,
        "cache_closed_last_timestamp_utc": None,
        "missing_bar_count_in_cache": int(len(bars)),
        "last_20_bars_cell_count": {},
        "last_20_bars_abs_flow_sum": {},
        "last_20_bars_signed_flow_sum": {},
        "bars_with_zero_cells": int(len(bars)),
        "bars_with_abnormally_low_cells": [],
        "forming_bar_idx": result.get("forming_bar_actual_idx"),
        "last_closed_bar_idx": result.get("last_closed_bar_idx"),
        "forming_bar_rows_included": False,
        "timestamp_lag_seconds": None,
        "closed_timestamp_lag_seconds": None,
        "timestamp_parity_ok": False,
    }

    cpath = compact_cache_path(symbol, date, depth)
    if not cpath.exists() or bars.empty:
        return out
    try:
        df = pd.read_parquet(cpath)
    except Exception as exc:
        out["error"] = f"cache read failed: {exc}"
        return out
    if df.empty or "bar_idx" not in df.columns:
        return out

    bar_ids = [int(x) for x in bars["bar_index"].tolist()]
    cached_ids = set(int(x) for x in pd.unique(df["bar_idx"]))
    grouped = df.groupby("bar_idx", sort=True).agg(
        cell_count=("bar_idx", "size"),
        abs_flow_sum=("abs_flow", "sum"),
        signed_flow_sum=("signed_flow", "sum"),
        timestamp_utc=("timestamp_utc", "max"),
    )
    if "bar_state" in df.columns:
        forming_rows = df[df["bar_state"] == "FORMING"]
        out["forming_bar_rows_included"] = bool(not forming_rows.empty)
        closed_df = df[df["bar_state"] != "FORMING"]
    else:
        closed_df = df

    feature_ids = set(bar_ids)
    missing_ids = sorted(feature_ids - cached_ids)
    cell_counts = grouped["cell_count"] if not grouped.empty else pd.Series(dtype=float)
    median_cells = float(cell_counts.median()) if len(cell_counts) else 0.0
    low_threshold = max(3.0, median_cells * 0.20) if median_cells > 0 else 3.0
    low_ids = [
        int(idx) for idx, count in cell_counts.items()
        if float(count) > 0 and float(count) < low_threshold
    ]
    last20_ids = bar_ids[-20:]
    last20 = grouped.reindex(last20_ids).fillna({
        "cell_count": 0,
        "abs_flow_sum": 0.0,
        "signed_flow_sum": 0.0,
        "timestamp_utc": "",
    })
    cache_last_ts = grouped["timestamp_utc"].max() if not grouped.empty else None
    cache_closed_last_ts = (
        closed_df["timestamp_utc"].max()
        if not closed_df.empty and "timestamp_utc" in closed_df.columns
        else None
    )
    out.update({
        "cache_rows": int(len(df)),
        "unique_cached_bars": int(len(cached_ids)),
        "cache_min_bar_idx": int(min(cached_ids)) if cached_ids else None,
        "cache_max_bar_idx": int(max(cached_ids)) if cached_ids else None,
        "cache_min_timestamp_utc": _ts_iso(grouped["timestamp_utc"].min()) if not grouped.empty else None,
        "cache_max_timestamp_utc": _ts_iso(grouped["timestamp_utc"].max()) if not grouped.empty else None,
        "cache_last_timestamp_utc": _ts_iso(cache_last_ts),
        "cache_closed_last_timestamp_utc": _ts_iso(cache_closed_last_ts),
        "missing_bar_count_in_cache": int(len(missing_ids)),
        "missing_bar_ids_in_cache_sample": missing_ids[:25],
        "last_20_bars_cell_count": {str(int(k)): int(v) for k, v in last20["cell_count"].items()},
        "last_20_bars_abs_flow_sum": {str(int(k)): float(v) for k, v in last20["abs_flow_sum"].items()},
        "last_20_bars_signed_flow_sum": {str(int(k)): float(v) for k, v in last20["signed_flow_sum"].items()},
        "bars_with_zero_cells": int(len(missing_ids)),
        "bars_with_abnormally_low_cells": low_ids[:50],
        "timestamp_lag_seconds": _lag_seconds(latest_ts, cache_last_ts),
        "closed_timestamp_lag_seconds": _lag_seconds(latest_ts, cache_closed_last_ts),
        "timestamp_parity_ok": _lag_seconds(latest_ts, cache_last_ts) == 0.0,
    })
    return out


def update_gui_heartbeat_fields(fields: dict) -> None:
    heartbeat = load_heartbeat()
    symbol = str(heartbeat.get("symbol") or fields.get("symbol") or "NQU6")
    date = str(heartbeat.get("active_date") or fields.get("active_date") or resolve_date("latest"))
    if "latest_feature_timestamp_utc" not in heartbeat or "cache_diagnostics_by_depth" not in heartbeat:
        bars = _load_bars(symbol, date)
        latest = _latest_bar_info(symbol, date)
        diagnostics = {
            str(depth): _cache_diagnostics(symbol, date, int(depth), bars, {})
            for depth in DEPTH_CHOICES
            if compact_cache_path(symbol, date, int(depth)).exists()
        }
        heartbeat.update({
            "symbol": symbol,
            "active_date": date,
            "latest_feature_bar_idx": latest.get("latest_bar_idx"),
            "latest_feature_timestamp_utc": latest.get("latest_bar_timestamp_utc"),
            "latest_closed_bar_idx": latest.get("latest_bar_idx"),
            "latest_closed_timestamp_utc": latest.get("latest_bar_timestamp_utc"),
            "cache_diagnostics_by_depth": diagnostics,
            "cache_last_timestamp_utc_by_depth": {
                depth: diag.get("cache_last_timestamp_utc") for depth, diag in diagnostics.items()
            },
            "timestamp_lag_seconds_by_depth": {
                depth: diag.get("timestamp_lag_seconds") for depth, diag in diagnostics.items()
            },
            "timestamp_parity_ok": bool(diagnostics and all(diag.get("timestamp_parity_ok") for diag in diagnostics.values())),
        })
    heartbeat.update(fields)
    _atomic_write_json(heartbeat, HEARTBEAT_PATH)


def _atomic_write_json(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True, default=str))
    tmp.replace(path)


def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def _compact_current(symbol: str, date: str, depth: int, bars: pd.DataFrame) -> bool:
    cpath = compact_cache_path(symbol, date, depth)
    mpath = compact_cache_meta_path(symbol, date, depth)
    src = level_cache.level_cache_path(symbol, date, depth)
    if not cpath.exists() or not mpath.exists() or bars.empty:
        return False
    try:
        meta = json.loads(mpath.read_text())
    except Exception:
        return False
    source_signature = meta.get("source_signature")
    if source_signature is not None and tuple(source_signature) != _file_sig(src):
        return False
    return (
        int(meta.get("bars_count", -1)) == len(bars)
        and int(meta.get("last_bar_end_ts_ns", -1)) == int(bars["bar_end_ts_ns"].iloc[-1])
        and int(meta.get("depth", -1)) == int(depth)
        and str(meta.get("symbol", "")) == symbol
        and str(meta.get("date", "")) == date
    )


def _compact_source_current(symbol: str, date: str, depth: int) -> Optional[dict]:
    cpath = compact_cache_path(symbol, date, depth)
    mpath = compact_cache_meta_path(symbol, date, depth)
    src = level_cache.level_cache_path(symbol, date, depth)
    if not cpath.exists() or not mpath.exists() or not src.exists():
        return None
    try:
        meta = json.loads(mpath.read_text())
    except Exception:
        return None
    if tuple(meta.get("source_signature", ())) != _file_sig(src):
        return None
    meta.update({
        "cached": True,
        "incremental_update": True,
        "full_raw_reparse": False,
        "raw_lines_processed": 0,
    })
    return meta


def _compact_from_level_cache(symbol: str, date: str, depth: int, bars: pd.DataFrame) -> dict:
    src = level_cache.level_cache_path(symbol, date, depth)
    if not src.exists():
        return {"ok": False, "reason": f"source level cache missing: {src}"}
    t0 = time.time()
    df = pd.read_parquet(src)
    keep = [c for c in COMPACT_COLUMNS if c in df.columns]
    compact = df[keep].copy()
    for col in COMPACT_COLUMNS:
        if col not in compact.columns:
            if col == "bar_state":
                compact[col] = "CLOSED"
            elif col in ("timestamp_utc", "side_zone", "nearest_level"):
                compact[col] = ""
            else:
                compact[col] = 0.0
    compact = compact[COMPACT_COLUMNS]
    out = compact_cache_path(symbol, date, depth)
    _atomic_write_parquet(compact, out)
    src_meta = level_cache._cache_meta(symbol, date, depth)
    rows = int(len(compact))
    meta = {
        "ok": True,
        "symbol": symbol,
        "date": date,
        "depth": int(depth),
        "cache_path": str(out),
        "source_cache_path": str(src),
        "rows": rows,
        "bars_count": int(src_meta.get("bars_count", len(bars))),
        "last_bar_idx": int(src_meta.get("last_bar_idx", compact["bar_idx"].max() if rows else -1)),
        "last_bar_end_ts_ns": int(src_meta.get(
            "last_bar_end_ts_ns",
            bars["bar_end_ts_ns"].iloc[-1] if not bars.empty else -1,
        )),
        "source_signature": _file_sig(src),
        "bars_signature": _file_sig(bfl.FEATURES_BASE / date / f"{symbol}_vol500.ndjsonl"),
        "source": "RAW_RITHMIC_DEPTH_AND_QUOTE_UPDATES",
        "compact_schema": COMPACT_COLUMNS,
        "atomic_write": True,
        "gui_reads_cache_only": True,
        "raw_parse_in_gui": False,
        "load_ms": round((time.time() - t0) * 1000.0, 3),
    }
    _atomic_write_json(meta, compact_cache_meta_path(symbol, date, depth))
    return meta


def ensure_compact_cache(
    symbol: str,
    date: str,
    depth: int,
    force: bool = False,
    rebuild_stale: bool = False,
) -> dict:
    """Ensure one compact cache exists.

    Initial backfill may use the historical raw builder in this daemon process.
    Normal loop iterations short-circuit on compact/cache metadata and do not
    touch raw files unless the source cache is stale and must be rebuilt.
    """
    date = resolve_date(date)
    depth = int(depth)
    bars = _load_bars(symbol, date)
    if bars.empty:
        return {"ok": False, "reason": f"empty vol500 bars for {symbol} {date}"}

    if not force:
        if _compact_current(symbol, date, depth, bars):
            meta = json.loads(compact_cache_meta_path(symbol, date, depth).read_text())
            meta.update({
                "cached": True,
                "incremental_update": True,
                "full_raw_reparse": False,
                "raw_lines_processed": 0,
            })
            return meta
        source_cached = _compact_source_current(symbol, date, depth)
        if source_cached is not None and not rebuild_stale:
            source_cached["stale_to_vol500"] = True
            return source_cached

    src = level_cache.level_cache_path(symbol, date, depth)
    source_current = level_cache._cache_current(symbol, date, depth, bars)
    builder_ms = 0.0
    full_raw_reparse = False
    builder_result = {"ok": True, "cached": source_current}
    if src.exists() and not force and (source_current or not rebuild_stale):
        pass
    elif force or not src.exists() or not source_current:
        t_build = time.time()
        builder_result = level_cache.build_level_caches(symbol, date, depths=[depth], force=force)
        builder_ms = (time.time() - t_build) * 1000.0
        full_raw_reparse = bool(builder_result.get("full_raw_reparse", not builder_result.get("cached", False)))
        if not builder_result.get("ok"):
            return {
                "ok": False,
                "reason": builder_result.get("reason", "level cache build failed"),
                "builder_result": builder_result,
                "builder_ms": round(builder_ms, 3),
            }

    compact = _compact_from_level_cache(symbol, date, depth, bars)
    compact.update({
        "cached": False,
        "builder_ms": round(builder_ms, 3),
        "builder_result": builder_result,
        "incremental_update": not full_raw_reparse,
        "full_raw_reparse": full_raw_reparse,
        "raw_lines_processed": int(builder_result.get("events_used", 0)) if full_raw_reparse else 0,
    })
    _atomic_write_json(compact, compact_cache_meta_path(symbol, date, depth))
    return compact


def ensure_compact_caches(
    symbol: str,
    date: str,
    depths: Iterable[int] = DEPTH_CHOICES,
    force: bool = False,
    rebuild_stale: bool = False,
) -> dict:
    requested_date = str(date)
    live_latest_mode = requested_date.lower().replace("_", " ") in ("latest", "live latest")
    date_info = latest_dates(symbol)
    date = resolve_date(date)
    t0 = time.time()
    depth_list = sorted({int(d) for d in depths})
    results = {}
    ok = True
    errors: list[str] = []
    bars = _load_bars(symbol, date)

    # Incremental stats aggregated across this run
    run_incremental_update = True
    run_full_raw_replay = False
    run_raw_lines_processed = 0
    run_affected_bars = 0
    run_bid_offset: Optional[int] = None
    run_ask_offset: Optional[int] = None
    run_bid_size: Optional[int] = None
    run_ask_size: Optional[int] = None

    if bars.empty:
        ok = False
        errors.append(f"empty vol500 bars for {symbol} {date}")
        for depth in depth_list:
            results[str(depth)] = {"ok": False, "reason": errors[-1], "depth": int(depth)}
    else:
        # Always run the incremental builder — it returns fast when nothing new
        t_build = time.time()
        builder_result = level_cache.build_level_caches(symbol, date, depths=depth_list, force=force)
        builder_ms = round((time.time() - t_build) * 1000.0, 3)

        full_raw_reparse = bool(builder_result.get("full_raw_reparse", False))
        incremental_update = bool(builder_result.get("incremental_update", not full_raw_reparse))
        raw_lines = int(builder_result.get("raw_lines_processed", 0))
        affected_bars = int(builder_result.get("affected_bars", 0))

        run_incremental_update = incremental_update
        run_full_raw_replay = full_raw_reparse
        run_raw_lines_processed = raw_lines
        run_affected_bars = affected_bars
        run_bid_offset = builder_result.get("bid_file_offset")
        run_ask_offset = builder_result.get("ask_file_offset")
        run_bid_size = builder_result.get("bid_file_size")
        run_ask_size = builder_result.get("ask_file_size")

        if not builder_result.get("ok"):
            ok = False
            reason = builder_result.get("reason", "level cache build failed")
            for depth in depth_list:
                results[str(depth)] = {
                    "ok": False,
                    "reason": reason,
                    "depth": int(depth),
                    "builder_result": builder_result,
                    "builder_ms": builder_ms,
                }
                errors.append(f"top{depth}: {reason}")
        else:
            # Refresh bars (may have changed during build)
            bars = _load_bars(symbol, date)
            for depth in depth_list:
                if builder_result.get("cached"):
                    # Nothing new, compact may already be current
                    if not force and _compact_current(symbol, date, depth, bars):
                        try:
                            meta = json.loads(compact_cache_meta_path(symbol, date, depth).read_text())
                        except Exception as exc:
                            meta = {"ok": True, "depth": int(depth)}
                        meta.update({
                            "cached": True,
                            "incremental_update": True,
                            "full_raw_reparse": False,
                            "raw_lines_processed": 0,
                            "affected_bars": 0,
                            "last_closed_bar_idx": builder_result.get("last_closed_bar_idx"),
                            "forming_bar_actual_idx": builder_result.get("forming_bar_actual_idx"),
                        })
                        results[str(depth)] = meta
                        continue
                compact = _compact_from_level_cache(symbol, date, depth, bars)
                compact.update({
                    "cached": bool(builder_result.get("cached")),
                    "builder_ms": builder_ms,
                    "builder_result": builder_result,
                    "incremental_update": incremental_update,
                    "full_raw_reparse": full_raw_reparse,
                    "raw_lines_processed": raw_lines,
                    "affected_bars": affected_bars,
                    "last_closed_bar_idx": builder_result.get("last_closed_bar_idx"),
                    "forming_bar_actual_idx": builder_result.get("forming_bar_actual_idx"),
                })
                _atomic_write_json(compact, compact_cache_meta_path(symbol, date, depth))
                results[str(depth)] = compact

    for depth in depth_list:
        res = results.get(str(depth), {})
        ok = ok and bool(res.get("ok"))
        if not res.get("ok") and not any(e.startswith(f"top{depth}:") for e in errors):
            errors.append(f"top{depth}: {res.get('reason', 'unknown error')}")

    elapsed_ms = round((time.time() - t0) * 1000.0, 3)
    cache_paths = {
        str(depth): str(compact_cache_path(symbol, date, int(depth)))
        for depth in depth_list
    }
    cache_rows_by_depth = {
        str(depth): int(results.get(str(depth), {}).get("rows", 0))
        for depth in depth_list
    }
    cache_diagnostics_by_depth = {
        str(depth): _cache_diagnostics(symbol, date, int(depth), bars, results.get(str(depth), {}))
        for depth in depth_list
    }
    cache_last_bar_idx_by_depth = {
        str(depth): results.get(str(depth), {}).get("last_bar_idx")
        for depth in depth_list
    }
    last_closed_bar_idx_by_depth = {
        str(depth): results.get(str(depth), {}).get("last_closed_bar_idx")
        for depth in depth_list
    }
    forming_bar_idx_actual_by_depth = {
        str(depth): results.get(str(depth), {}).get("forming_bar_actual_idx")
        for depth in depth_list
    }
    latest_bar = _latest_bar_info(symbol, date)
    latest_bar_idx = latest_bar.get("latest_bar_idx")
    cache_lag_bars_by_depth = {
        str(depth): (
            None
            if latest_bar_idx is None or cache_last_bar_idx_by_depth.get(str(depth)) is None
            else int(latest_bar_idx) - int(cache_last_bar_idx_by_depth[str(depth)])
        )
        for depth in depth_list
    }
    cache_lag_values = [int(v) for v in cache_lag_bars_by_depth.values() if v is not None]
    cache_last_timestamp_utc_by_depth = {
        str(depth): cache_diagnostics_by_depth[str(depth)].get("cache_last_timestamp_utc")
        for depth in depth_list
    }
    cache_closed_last_timestamp_utc_by_depth = {
        str(depth): cache_diagnostics_by_depth[str(depth)].get("cache_closed_last_timestamp_utc")
        for depth in depth_list
    }
    timestamp_lag_seconds_by_depth = {
        str(depth): cache_diagnostics_by_depth[str(depth)].get("timestamp_lag_seconds")
        for depth in depth_list
    }
    closed_timestamp_lag_seconds_by_depth = {
        str(depth): cache_diagnostics_by_depth[str(depth)].get("closed_timestamp_lag_seconds")
        for depth in depth_list
    }
    timestamp_parity_ok = bool(
        cache_diagnostics_by_depth
        and all(bool(v.get("timestamp_parity_ok")) for v in cache_diagnostics_by_depth.values())
    )
    max_lag = max(cache_lag_values) if cache_lag_values else 0
    # PASS: incremental mode, lag <= 1 (forming bar), no errors
    if ok and timestamp_parity_ok and not run_full_raw_replay and max_lag <= 1:
        heartbeat_status = "PASS"
    elif ok and max_lag <= 2:
        heartbeat_status = "WARN"
    else:
        heartbeat_status = "BLOCKED" if not ok else "WARN"
    cache_mtimes = {
        str(depth): _mtime_utc(compact_cache_path(symbol, date, int(depth)))
        for depth in depth_list
    }
    raw_path = bfl.RAW_BASE / date / symbol
    feature_path = bfl.FEATURES_BASE / date / f"{symbol}_vol500.ndjsonl"

    # Resolve checkpoint path for reporting
    checkpoint_path = str(level_cache.v3_state_path(symbol, date))
    checkpoint_exists = level_cache.v3_state_path(symbol, date).exists()

    # Raw file sizes from builder or from disk
    if run_bid_size is None:
        try:
            run_bid_size = (raw_path / "bid_quote_updates.ndjson").stat().st_size
        except OSError:
            run_bid_size = None
    if run_ask_size is None:
        try:
            run_ask_size = (raw_path / "ask_quote_updates.ndjson").stat().st_size
        except OSError:
            run_ask_size = None

    # Collect forming-cache stats for head-to-head heartbeat fields
    latest_forming_timestamp_utc: Optional[str] = None
    forming_cells_by_depth: dict = {}
    forming_mtime_utc: Optional[str] = None
    latest_price_timestamp_utc = latest_bar.get("latest_bar_timestamp_utc")
    for depth in depth_list:
        fmeta_path = forming_cache_meta_path(symbol, date, depth)
        try:
            fmeta = json.loads(fmeta_path.read_text())
            forming_cells_by_depth[str(depth)] = int(fmeta.get("rows", 0))
            fm_ts = fmeta.get("updated_utc")
            if fm_ts and (latest_forming_timestamp_utc is None or fm_ts > latest_forming_timestamp_utc):
                latest_forming_timestamp_utc = fm_ts
                forming_mtime_utc = fm_ts
        except Exception:
            forming_cells_by_depth[str(depth)] = 0
    # Consistency: if no forming bar checkpoint exists for a depth, report 0 forming cells
    # so the heartbeat doesn't show non-zero cells when forming_bar_idx is None (stale meta).
    for _d in depth_list:
        if forming_bar_idx_actual_by_depth.get(str(_d)) is None:
            forming_cells_by_depth[str(_d)] = 0

    forming_lag_seconds: Optional[float] = None
    if latest_price_timestamp_utc and latest_forming_timestamp_utc:
        try:
            from datetime import datetime as _dt
            _p = _dt.fromisoformat(latest_price_timestamp_utc.replace("Z", "+00:00"))
            _f = _dt.fromisoformat(latest_forming_timestamp_utc.replace("Z", "+00:00"))
            forming_lag_seconds = round((_p - _f).total_seconds(), 2)
        except Exception:
            pass
    closed_lag = max(
        [v for v in closed_timestamp_lag_seconds_by_depth.values() if v is not None] or [None]
    )
    if not ok:
        head_to_head_status = "CACHE_DAEMON_LAGGING"
    elif run_full_raw_replay:
        head_to_head_status = "CACHE_DAEMON_LAGGING"
    elif forming_lag_seconds is not None and forming_lag_seconds > 5.0:
        head_to_head_status = "RAW_CAPTURE_LAGGING"
    elif closed_lag is not None and closed_lag > 5.0:
        head_to_head_status = "CLOSED_CACHE_LAGGING_BUT_FORMING_OK" if (
            forming_lag_seconds is not None and forming_lag_seconds <= 2.0
        ) else "CACHE_DAEMON_LAGGING"
    elif forming_lag_seconds is not None and forming_lag_seconds <= 2.0:
        head_to_head_status = "HEAD_TO_HEAD_OK"
    elif latest_forming_timestamp_utc is None:
        head_to_head_status = "CLOSED_CACHE_LAGGING_BUT_FORMING_OK"
    else:
        head_to_head_status = "HEAD_TO_HEAD_OK"

    heartbeat = {
        "run_utc": utc_now_iso(),
        "symbol": symbol,
        "requested_date": requested_date,
        "live_latest_mode": live_latest_mode,
        "active_date": date,
        "latest_raw_date": date_info.get("latest_raw_date"),
        "latest_feature_date": date_info.get("latest_feature_date"),
        "raw_path": str(raw_path),
        "feature_path": str(feature_path),
        "cache_paths": cache_paths,
        "raw_mtime_utc": _mtime_utc(raw_path),
        "feature_mtime_utc": _mtime_utc(feature_path),
        "cache_mtime_utc": max([v for v in cache_mtimes.values() if v] or [None]),
        "cache_mtimes_utc": cache_mtimes,
        "cache_rows_by_depth": cache_rows_by_depth,
        "cache_diagnostics_by_depth": cache_diagnostics_by_depth,
        "cache_last_bar_idx_by_depth": cache_last_bar_idx_by_depth,
        "cache_last_timestamp_utc_by_depth": cache_last_timestamp_utc_by_depth,
        "cache_closed_last_timestamp_utc_by_depth": cache_closed_last_timestamp_utc_by_depth,
        "last_closed_bar_idx_by_depth": last_closed_bar_idx_by_depth,
        "forming_bar_idx_actual_by_depth": forming_bar_idx_actual_by_depth,
        "cache_lag_bars_by_depth": cache_lag_bars_by_depth,
        "timestamp_lag_seconds_by_depth": timestamp_lag_seconds_by_depth,
        "closed_timestamp_lag_seconds_by_depth": closed_timestamp_lag_seconds_by_depth,
        "timestamp_parity_ok": timestamp_parity_ok,
        "latest_price_timestamp_utc": latest_price_timestamp_utc,
        "latest_bar_timestamp_utc": latest_bar.get("latest_bar_timestamp_utc"),
        "latest_bar_idx": latest_bar.get("latest_bar_idx"),
        "latest_feature_timestamp_utc": latest_bar.get("latest_bar_timestamp_utc"),
        "latest_feature_bar_idx": latest_bar.get("latest_bar_idx"),
        "latest_closed_timestamp_utc": latest_bar.get("latest_bar_timestamp_utc"),
        "latest_closed_bar_idx": latest_bar.get("latest_bar_idx"),
        "latest_forming_timestamp_utc": latest_forming_timestamp_utc,
        "forming_mtime_utc": forming_mtime_utc,
        "forming_cells_by_depth": forming_cells_by_depth,
        "forming_lag_seconds": forming_lag_seconds,
        "head_to_head_status": head_to_head_status,
        "feature_rows": latest_bar.get("feature_rows"),
        "incremental_update": run_incremental_update,
        "full_raw_replay": run_full_raw_replay,
        "raw_lines_processed_this_run": run_raw_lines_processed,
        "affected_bars_this_run": run_affected_bars,
        "raw_bid_offset": run_bid_offset,
        "raw_ask_offset": run_ask_offset,
        "raw_bid_size": run_bid_size,
        "raw_ask_size": run_ask_size,
        "checkpoint_path": checkpoint_path,
        "checkpoint_exists": checkpoint_exists,
        "update_latency_sec": round(elapsed_ms / 1000.0, 6),
        "errors": errors,
        "status": heartbeat_status,
    }
    _atomic_write_json(heartbeat, HEARTBEAT_PATH)
    return {
        "ok": ok,
        "symbol": symbol,
        "date": date,
        "heartbeat": heartbeat,
        "heartbeat_path": str(HEARTBEAT_PATH),
        "results": results,
        "elapsed_ms": elapsed_ms,
    }


def build_forming_bar_cache(symbol: str, date: str, depths: list) -> dict:
    """Lane-2: fast forming bar snapshot updated every 250-500 ms.

    Reads the v3 checkpoint (read-only), reads new bid/ask events since the
    checkpoint's saved byte offset, merges with the checkpoint's forming_bar_aggs,
    and writes per-depth forming parquet files to cache/forming/.  This provides
    a live view of the current bar that is updated far faster than the main 2s
    compact-cache cycle.
    """
    t0 = time.time()
    date = resolve_date(date)

    state = level_cache.load_v3_state(symbol, date)
    if state is None:
        return {"ok": False, "reason": "no_v3_checkpoint"}

    raw_dir = bfl.RAW_BASE / date / symbol
    bid_path = raw_dir / "bid_quote_updates.ndjson"
    ask_path = raw_dir / "ask_quote_updates.ndjson"

    if not bid_path.exists() or not ask_path.exists():
        return {"ok": False, "reason": "raw_files_missing"}

    bars = _load_bars(symbol, date)
    if bars.empty:
        return {"ok": False, "reason": "empty_bars"}

    forming_bar_idx = int(state.get("forming_bar_idx", 0))
    forming_bar_idx = max(0, min(forming_bar_idx, len(bars) - 1))
    forming_bar = bars.iloc[forming_bar_idx]
    forming_bar_actual_idx = int(forming_bar["bar_index"])
    forming_bar_start_ts_ns = int(forming_bar["bar_start_ts_ns"])
    forming_bar_end_ts_ns = int(forming_bar["bar_end_ts_ns"])

    bid_sizes = state["bid_sizes"].copy()
    ask_sizes = state["ask_sizes"].copy()
    bid_offset = int(state["bid_file_offset"])
    ask_offset = int(state["ask_file_offset"])
    ckpt_forming_aggs = state.get("forming_bar_aggs", {})
    forming_bar_aggs: dict = {}
    for d in depths:
        forming_bar_aggs[d] = dict(ckpt_forming_aggs.get(d, {}))

    # Bug 2: detect whether the "forming bar" from the checkpoint is actually the
    # last vol500 bar whose end time has already passed.  If so, the REAL live
    # forming bar is the synthetic NEXT bar (bar_index + 1), built from raw
    # events that arrive after forming_bar_end_ts_ns.
    _OVERFLOW_GAP_NS = 3_000_000_000  # 3s buffer
    _last_vol500_closed = (
        forming_bar_idx == len(bars) - 1
        and int(forming_bar_end_ts_ns) < time.time_ns() - _OVERFLOW_GAP_NS
    )

    # Read new events that arrived after the last main-cycle checkpoint
    bid_df, _ = bfl.read_quote_updates(bid_path, bid_offset)
    ask_df, _ = bfl.read_quote_updates(ask_path, ask_offset)
    new_events_count = 0
    delta_aggs: dict = {d: {} for d in depths}
    bar_closed_since_checkpoint = False  # True when bar N closed in this delta window
    # overflow_aggs: events for the synthetic NEXT bar beyond vol500
    overflow_aggs: dict = {d: {} for d in depths}
    overflow_events_count = 0
    _first_overflow_ev = len(bid_df) + len(ask_df) + 1  # sentinel = no overflow

    if not bid_df.empty or not ask_df.empty:
        ts_arr, side_arr, tick_arr, size_arr = bfl.merge_events(bid_df, ask_df)
        active_bid = level_cache._active_from_arr(bid_sizes)
        active_ask = level_cache._active_from_arr(ask_sizes)
        max_depth = max(depths)
        starts = bars["bar_start_ts_ns"].to_numpy(dtype=np.int64)
        ends = bars["bar_end_ts_ns"].to_numpy(dtype=np.int64)
        bar_i = forming_bar_idx

        for ev_i in range(len(ts_arr)):
            ts = int(ts_arr[ev_i])
            while bar_i < len(bars) and ts >= ends[bar_i]:
                bar_i += 1
            if bar_i >= len(bars):
                _first_overflow_ev = ev_i  # record where overflow begins
                break
            if bar_i != forming_bar_idx:
                # Bar has closed since last checkpoint — delta_aggs already has all
                # events up to bar N's close boundary; mark it closed and stop.
                bar_closed_since_checkpoint = True
                break
            if ts < starts[bar_i]:
                continue

            side = int(side_arr[ev_i])
            tick = int(tick_arr[ev_i])
            new_size = float(size_arr[ev_i])
            if tick < 0 or tick >= bfl.N_TICKS:
                continue

            if side == 0:  # bid
                old_size = float(bid_sizes[tick])
                if new_size == old_size:
                    continue
                rank_before = level_cache._bid_rank(active_bid, tick) if old_size > 0 else -1
                if old_size > 0 and new_size <= 0:
                    level_cache._remove_active(active_bid, tick)
                elif old_size <= 0 and new_size > 0:
                    level_cache._insert_active(active_bid, tick)
                bid_sizes[tick] = new_size if new_size > 0 else 0.0
                rank_after = level_cache._bid_rank(active_bid, tick) if new_size > 0 else -1
                delta = new_size - old_size
                signed = delta
                bid_add = delta if delta > 0 else 0.0
                bid_pull = (-delta) if delta < 0 else 0.0
                ask_add = ask_pull = 0.0
                net_bid = signed
                net_ask = 0.0
            else:  # ask
                old_size = float(ask_sizes[tick])
                if new_size == old_size:
                    continue
                rank_before = level_cache._ask_rank(active_ask, tick) if old_size > 0 else -1
                if old_size > 0 and new_size <= 0:
                    level_cache._remove_active(active_ask, tick)
                elif old_size <= 0 and new_size > 0:
                    level_cache._insert_active(active_ask, tick)
                ask_sizes[tick] = new_size if new_size > 0 else 0.0
                rank_after = level_cache._ask_rank(active_ask, tick) if new_size > 0 else -1
                delta = new_size - old_size
                signed = -delta
                bid_add = bid_pull = 0.0
                ask_add = delta if delta > 0 else 0.0
                ask_pull = (-delta) if delta < 0 else 0.0
                net_bid = 0.0
                net_ask = signed

            ranks = [r for r in (rank_before, rank_after) if r > 0]
            if not ranks:
                continue
            min_rank = min(ranks)
            if min_rank > max_depth:
                continue

            mid = level_cache._best_mid(active_bid, active_ask)
            new_events_count += 1
            abs_flow = abs(signed)

            for depth in depths:
                if min_rank > depth:
                    continue
                tick_map = delta_aggs[depth]
                vals = tick_map.get(tick)
                if vals is None:
                    vals = [0.0] * 9
                    tick_map[tick] = vals
                vals[0] += signed
                vals[1] += abs_flow
                vals[2] += bid_add
                vals[3] += bid_pull
                vals[4] += ask_add
                vals[5] += ask_pull
                vals[6] += net_bid
                vals[7] += net_ask
                vals[8] = mid

        # Bug 2: process overflow events (past last vol500 bar's end) to build
        # the live forming bar for the NEXT bar (synthetic, not yet in vol500).
        if _last_vol500_closed and _first_overflow_ev < len(ts_arr):
            for ev_i in range(_first_overflow_ev, len(ts_arr)):
                ts = int(ts_arr[ev_i])
                side = int(side_arr[ev_i])
                tick = int(tick_arr[ev_i])
                new_size = float(size_arr[ev_i])
                if tick < 0 or tick >= bfl.N_TICKS:
                    continue

                if side == 0:  # bid
                    old_size = float(bid_sizes[tick])
                    if new_size == old_size:
                        continue
                    rank_before = level_cache._bid_rank(active_bid, tick) if old_size > 0 else -1
                    if old_size > 0 and new_size <= 0:
                        level_cache._remove_active(active_bid, tick)
                    elif old_size <= 0 and new_size > 0:
                        level_cache._insert_active(active_bid, tick)
                    bid_sizes[tick] = new_size if new_size > 0 else 0.0
                    rank_after = level_cache._bid_rank(active_bid, tick) if new_size > 0 else -1
                    delta = new_size - old_size
                    signed = delta
                    bid_add = delta if delta > 0 else 0.0
                    bid_pull = (-delta) if delta < 0 else 0.0
                    ask_add = ask_pull = 0.0
                    net_bid = signed
                    net_ask = 0.0
                else:  # ask
                    old_size = float(ask_sizes[tick])
                    if new_size == old_size:
                        continue
                    rank_before = level_cache._ask_rank(active_ask, tick) if old_size > 0 else -1
                    if old_size > 0 and new_size <= 0:
                        level_cache._remove_active(active_ask, tick)
                    elif old_size <= 0 and new_size > 0:
                        level_cache._insert_active(active_ask, tick)
                    ask_sizes[tick] = new_size if new_size > 0 else 0.0
                    rank_after = level_cache._ask_rank(active_ask, tick) if new_size > 0 else -1
                    delta = new_size - old_size
                    signed = -delta
                    bid_add = bid_pull = 0.0
                    ask_add = delta if delta > 0 else 0.0
                    ask_pull = (-delta) if delta < 0 else 0.0
                    net_bid = 0.0
                    net_ask = signed

                ranks = [r for r in (rank_before, rank_after) if r > 0]
                if not ranks:
                    continue
                min_rank = min(ranks)
                if min_rank > max_depth:
                    continue

                mid = level_cache._best_mid(active_bid, active_ask)
                overflow_events_count += 1
                abs_flow = abs(signed)

                for depth in depths:
                    if min_rank > depth:
                        continue
                    tick_map = overflow_aggs[depth]
                    vals = tick_map.get(tick)
                    if vals is None:
                        vals = [0.0] * 9
                        tick_map[tick] = vals
                    vals[0] += signed
                    vals[1] += abs_flow
                    vals[2] += bid_add
                    vals[3] += bid_pull
                    vals[4] += ask_add
                    vals[5] += ask_pull
                    vals[6] += net_bid
                    vals[7] += net_ask
                    vals[8] = mid

    ref_levels = level_cache._collect_reference_levels(bars)
    FORMING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    rows_by_depth: dict = {}
    updated_utc = utc_now_iso()

    # Determine whether to write the forming bar for the LAST vol500 bar or the
    # synthetic NEXT bar (Bug 2 fix).
    # * _last_vol500_closed=True  → write overflow forming bar (bar N+1, synthetic)
    # * _last_vol500_closed=False → write forming bar for vol500's current forming bar
    if _last_vol500_closed:
        # Synthetic next bar — not yet in vol500.  Use last vol500 bar's end as start.
        _out_bar_actual_idx = forming_bar_actual_idx + 1
        _out_bar_start_ts_ns = forming_bar_end_ts_ns  # start = previous bar's end
        _out_bar_end_ts_ns = 0  # unknown — open-ended
        _out_aggs_source = overflow_aggs
        _out_events_count = overflow_events_count
        _out_close_price = float(bars.iloc[-1].get("px_close") or 0.0)
        _out_bar_state = "FORMING"
    else:
        _out_bar_actual_idx = forming_bar_actual_idx
        _out_bar_start_ts_ns = forming_bar_start_ts_ns
        _out_bar_end_ts_ns = forming_bar_end_ts_ns
        _out_events_count = new_events_count
        _out_close_price = float(forming_bar.get("px_close") or 0.0)
        _out_bar_state = "CLOSED" if bar_closed_since_checkpoint else "FORMING"
        # Merge checkpoint aggs with new delta aggs for normal forming path
        _out_aggs_source = {}
        for depth in depths:
            merged: dict = {}
            for tick, vals in forming_bar_aggs.get(depth, {}).items():
                merged[tick] = list(vals)
            for tick, vals in delta_aggs.get(depth, {}).items():
                if tick not in merged:
                    merged[tick] = list(vals)
                else:
                    for i in range(8):
                        merged[tick][i] += vals[i]
                    merged[tick][8] = vals[8]
            _out_aggs_source[depth] = merged

    for depth in depths:
        # No-change skip: avoid touching the filesystem when nothing has arrived
        # since the last sub-cycle write — prevents 350ms mtime churn during quiet
        # market conditions, which otherwise triggers unnecessary GUI reloads.
        _fkey = (symbol, date, int(depth))
        _fstate = (_out_events_count, _out_bar_actual_idx, _out_bar_state)
        if _forming_prev_state.get(_fkey) == _fstate:
            rows_by_depth[str(depth)] = _forming_prev_rows.get(_fkey, 0)
            continue

        if _last_vol500_closed:
            merged = dict(overflow_aggs.get(depth, {}))
        else:
            merged = _out_aggs_source.get(depth, {})

        rows = []
        for tick, vals in sorted(merged.items()):
            price = level_cache._tick_to_price_float(int(tick))
            mid_val = float(vals[8]) if np.isfinite(float(vals[8])) else _out_close_price or price
            if abs(price - mid_val) <= bfl.TICK:
                side_zone = "near_mid"
            elif price < mid_val:
                side_zone = "bid"
            else:
                side_zone = "ask"
            nearest, distance = level_cache._nearest_level(ref_levels, price)
            rows.append({
                "bar_idx": _out_bar_actual_idx,
                "timestamp_utc": updated_utc,
                "bar_start_ts_ns": _out_bar_start_ts_ns,
                "bar_end_ts_ns": _out_bar_end_ts_ns,
                "price_level": price,
                "price_tick": int(tick),
                "depth_n": int(depth),
                "side_zone": side_zone,
                "signed_flow": float(vals[0]),
                "abs_flow": float(vals[1]),
                "bid_add": float(vals[2]),
                "bid_pull": float(vals[3]),
                "ask_add": float(vals[4]),
                "ask_pull": float(vals[5]),
                "net_bid_flow": float(vals[6]),
                "net_ask_flow": float(vals[7]),
                "trade_volume_at_price": 0.0,
                "buy_trade_volume_at_price": 0.0,
                "sell_trade_volume_at_price": 0.0,
                "close_price": _out_close_price,
                "mid_price": mid_val,
                "nearest_level": nearest,
                "distance_to_nearest_level": float(distance) if np.isfinite(float(distance)) else 0.0,
                "bar_state": _out_bar_state,
            })

        df = pd.DataFrame(rows)
        fpath = forming_cache_path(symbol, date, depth)
        if not df.empty:
            _atomic_write_parquet(df, fpath)
        elif fpath.exists():
            # Clear stale forming data so the GUI doesn't display old bars
            _atomic_write_parquet(pd.DataFrame(columns=list(df.columns) if df.columns.any() else [
                "bar_idx", "timestamp_utc", "bar_start_ts_ns", "bar_end_ts_ns",
                "price_level", "price_tick", "depth_n", "side_zone",
                "signed_flow", "abs_flow", "bid_add", "bid_pull", "ask_add", "ask_pull",
                "net_bid_flow", "net_ask_flow", "trade_volume_at_price",
                "buy_trade_volume_at_price", "sell_trade_volume_at_price",
                "close_price", "mid_price", "nearest_level", "distance_to_nearest_level",
                "bar_state",
            ]), fpath)
        meta = {
            "symbol": symbol, "date": date, "depth": int(depth),
            "forming_bar_idx": _out_bar_actual_idx,
            "forming_bar_start_ts_ns": _out_bar_start_ts_ns,
            "forming_bar_end_ts_ns": _out_bar_end_ts_ns,
            "rows": len(df),
            "new_events_since_checkpoint": _out_events_count,
            "is_synthetic_next_bar": bool(_last_vol500_closed),
            "updated_utc": updated_utc,
        }
        _atomic_write_json(meta, forming_cache_meta_path(symbol, date, depth))
        rows_by_depth[str(depth)] = len(df)
        _forming_prev_state[_fkey] = _fstate
        _forming_prev_rows[_fkey] = len(df)

    elapsed_ms = round((time.time() - t0) * 1000.0, 3)
    return {
        "ok": True,
        "forming_bar_idx": _out_bar_actual_idx,
        "is_synthetic_next_bar": bool(_last_vol500_closed),
        "new_events_count": _out_events_count,
        "rows_by_depth": rows_by_depth,
        "updated_utc": updated_utc,
        "elapsed_ms": elapsed_ms,
    }


def _depths_arg(value: str) -> list[int]:
    if value == "all":
        return DEPTH_CHOICES
    return [int(v.strip()) for v in value.split(",") if v.strip()]


def _install_signal_handlers() -> None:
    def _stop(_signum, _frame) -> None:
        global RUN_FLAG
        RUN_FLAG = False
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)


def run_loop(
    symbol: str,
    date: str,
    depths: list[int],
    interval_sec: float,
    force: bool = False,
    rebuild_stale: bool = False,
) -> int:
    _install_signal_handlers()
    requested_date = str(date)
    forming_interval = 0.35  # 350 ms sub-cycle for Lane-2 forming bar
    print(
        f"[CACHE_DAEMON] symbol={symbol} date={requested_date} depths={depths} "
        f"interval={interval_sec}s forming_interval={forming_interval}s pid={os.getpid()}",
        flush=True,
    )
    while RUN_FLAG:
        result = ensure_compact_caches(symbol, requested_date, depths, force=force, rebuild_stale=rebuild_stale)
        print(json.dumps(result, sort_keys=True, default=str), flush=True)
        force = False
        deadline = time.time() + interval_sec
        # Fast sub-cycle: update the forming bar between main compact-cache cycles
        while RUN_FLAG and time.time() < deadline:
            try:
                active_date = resolve_date(requested_date)
                if level_cache.v3_state_path(symbol, active_date).exists():
                    build_forming_bar_cache(symbol, active_date, depths)
            except Exception:
                pass
            remaining = deadline - time.time()
            time.sleep(max(0.05, min(forming_interval, remaining)))
    print("[CACHE_DAEMON] stopped", flush=True)
    return 0


def run_benchmark(symbol: str, date: str, depth: int) -> int:
    date = resolve_date(date)
    t0 = time.time()
    first = ensure_compact_cache(symbol, date, depth, force=False, rebuild_stale=False)
    backfill_ms = (time.time() - t0) * 1000.0
    t1 = time.time()
    second = ensure_compact_cache(symbol, date, depth, force=False, rebuild_stale=False)
    incremental_ms = (time.time() - t1) * 1000.0
    ok = bool(first.get("ok") and second.get("ok"))
    print(f"CACHE_DAEMON_BENCHMARK={'PASS' if ok else 'FAIL'}")
    print(f"symbol={symbol}")
    print(f"date={date}")
    print(f"depth={depth}")
    print(f"initial_backfill_ms={backfill_ms:.3f}")
    print(f"incremental_update_ms={incremental_ms:.3f}")
    print(f"initial_full_raw_reparse={str(bool(first.get('full_raw_reparse'))).lower()}")
    print(f"incremental_full_raw_reparse={str(bool(second.get('full_raw_reparse'))).lower()}")
    print(f"cache_path={second.get('cache_path', first.get('cache_path'))}")
    print(f"rows={second.get('rows', first.get('rows'))}")
    print(f"raw_lines_processed_incremental={second.get('raw_lines_processed')}")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Book Flow V3 compact cache daemon")
    parser.add_argument("--symbol", default="NQU6")
    parser.add_argument("--date", default="latest")
    parser.add_argument("--depth", default="all", help="all or comma-separated depths")
    parser.add_argument("--interval-sec", type=float, default=2.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--rebuild-stale", action="store_true")
    parser.add_argument("--benchmark", action="store_true")
    args = parser.parse_args()
    depths = _depths_arg(args.depth)
    if args.benchmark:
        depth = depths[-1] if depths else 20
        return run_benchmark(args.symbol, args.date, depth)
    if args.once:
        result = ensure_compact_caches(
            args.symbol, args.date, depths, force=args.force, rebuild_stale=args.rebuild_stale
        )
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0 if result.get("ok") else 1
    return run_loop(
        args.symbol,
        args.date,
        depths,
        args.interval_sec,
        force=args.force,
        rebuild_stale=args.rebuild_stale,
    )


if __name__ == "__main__":
    raise SystemExit(main())
