#!/usr/bin/env python3
"""Build V3 price-axis book-flow level candle caches.

This is a read-only cache builder for the standalone True Book Flow Chart V3.
It reconstructs the book from Rithmic depth snapshots plus bid/ask quote
updates, then aggregates signed raw book flow by vol500 bar and actual price
level.
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

import book_flow_lib as bfl


DEPTH_CHOICES = [5, 10, 15, 20]
REQUIRED_DEPTH_FIELDS = {"side", "level", "price", "size", "timestamp_ns"}
REQUIRED_QUOTE_FIELDS = {"event_ts_ns", "price", "size"}
REQUIRED_TRADE_FIELDS = {"timestamp_ns", "price", "size", "aggressor_side"}


def level_cache_path(symbol: str, date: str, depth: int) -> Path:
    return bfl.CACHE_DIR / f"book_flow_level_candles_{symbol}_{date}_top{depth}.parquet"


def level_cache_meta_path(symbol: str, date: str, depth: int) -> Path:
    return level_cache_path(symbol, date, depth).with_suffix(".meta.json")


def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def _price_to_tick_scalar(price: float) -> int:
    return int(round((float(price) - bfl.PRICE_MIN) / bfl.TICK))


def _tick_to_price_float(tick: int) -> float:
    return round(float(tick) * bfl.TICK + bfl.PRICE_MIN, 2)


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_first_json(path: Path) -> dict:
    with path.open() as f:
        for line in f:
            if line.strip():
                return json.loads(line)
    return {}


def inspect_raw_depth_support(symbol: str, date: str) -> dict:
    raw_dir = bfl.RAW_BASE / date / symbol
    depth_path = raw_dir / "depth.ndjson"
    bid_path = raw_dir / "bid_quote_updates.ndjson"
    ask_path = raw_dir / "ask_quote_updates.ndjson"
    trades_path = raw_dir / "trades.ndjson"
    vol500_path = bfl.FEATURES_BASE / date / f"{symbol}_vol500.ndjsonl"

    missing_files = [
        str(p) for p in (depth_path, bid_path, ask_path, vol500_path) if not p.exists()
    ]
    if missing_files:
        return {
            "ok": False,
            "reason": "missing required raw/feature files",
            "missing_files": missing_files,
            "blocked_code": "BLOCKED_TOP_DEPTH_UNAVAILABLE",
        }

    depth_first = _read_first_json(depth_path)
    bid_first = _read_first_json(bid_path)
    ask_first = _read_first_json(ask_path)
    missing_depth_fields = sorted(REQUIRED_DEPTH_FIELDS - set(depth_first))
    missing_bid_fields = sorted(REQUIRED_QUOTE_FIELDS - set(bid_first))
    missing_ask_fields = sorted(REQUIRED_QUOTE_FIELDS - set(ask_first))
    if missing_depth_fields or missing_bid_fields or missing_ask_fields:
        return {
            "ok": False,
            "reason": "missing fields needed for top-depth reconstruction",
            "missing_depth_fields": missing_depth_fields,
            "missing_bid_fields": missing_bid_fields,
            "missing_ask_fields": missing_ask_fields,
            "blocked_code": "BLOCKED_TOP_DEPTH_UNAVAILABLE",
        }

    levels_by_side = {"B": set(), "A": set()}
    prices_by_quote = {"B": set(), "A": set()}
    depth_rows = 0
    with depth_path.open() as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            side = str(row.get("side", ""))
            if side in levels_by_side:
                levels_by_side[side].add(int(row.get("level", -1)))
                depth_rows += 1
    for path, side in ((bid_path, "B"), (ask_path, "A")):
        with path.open() as f:
            for i, line in enumerate(f):
                if i >= 20000:
                    break
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("price_valid", True):
                    prices_by_quote[side].add(_price_to_tick_scalar(row.get("price", 0.0)))

    max_bid_level = max(levels_by_side["B"]) if levels_by_side["B"] else -1
    max_ask_level = max(levels_by_side["A"]) if levels_by_side["A"] else -1
    top_depth_supported = max_bid_level >= 19 and max_ask_level >= 19
    multi_price_quotes = len(prices_by_quote["B"]) > 20 and len(prices_by_quote["A"]) > 20
    trades_fields_ok = True
    if trades_path.exists():
        trades_first = _read_first_json(trades_path)
        trades_fields_ok = not (REQUIRED_TRADE_FIELDS - set(trades_first))

    return {
        "ok": bool(top_depth_supported and multi_price_quotes),
        "reason": "" if top_depth_supported and multi_price_quotes else "top-depth ladder unavailable",
        "blocked_code": "" if top_depth_supported and multi_price_quotes else "BLOCKED_TOP_DEPTH_UNAVAILABLE",
        "depth_rows": depth_rows,
        "bid_level_count": len(levels_by_side["B"]),
        "ask_level_count": len(levels_by_side["A"]),
        "max_bid_level": max_bid_level,
        "max_ask_level": max_ask_level,
        "bid_quote_unique_prices_sample": len(prices_by_quote["B"]),
        "ask_quote_unique_prices_sample": len(prices_by_quote["A"]),
        "top_depth_supported": bool(top_depth_supported),
        "multi_price_quotes": bool(multi_price_quotes),
        "trades_fields_ok": bool(trades_fields_ok),
        "paths": {
            "depth": str(depth_path),
            "bid": str(bid_path),
            "ask": str(ask_path),
            "trades": str(trades_path),
            "vol500": str(vol500_path),
        },
    }


def _insert_active(active: list[int], tick: int) -> None:
    pos = bisect.bisect_left(active, tick)
    if pos == len(active) or active[pos] != tick:
        active.insert(pos, tick)


def _remove_active(active: list[int], tick: int) -> None:
    pos = bisect.bisect_left(active, tick)
    if pos < len(active) and active[pos] == tick:
        active.pop(pos)


def _bid_rank(active_bid: list[int], tick: int) -> int:
    if not active_bid:
        return -1
    return len(active_bid) - bisect.bisect_left(active_bid, tick)


def _ask_rank(active_ask: list[int], tick: int) -> int:
    if not active_ask:
        return -1
    return bisect.bisect_right(active_ask, tick)


def _load_initial_book(depth_path: Path) -> tuple[dict[int, float], dict[int, float], list[int], list[int]]:
    bid_sizes: dict[int, float] = {}
    ask_sizes: dict[int, float] = {}
    with depth_path.open() as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            side = str(row.get("side", ""))
            size = _safe_float(row.get("size"), 0.0)
            tick = _price_to_tick_scalar(row.get("price", 0.0))
            if size <= 0:
                continue
            if side == "B":
                bid_sizes[tick] = size
            elif side == "A":
                ask_sizes[tick] = size
    active_bid = sorted(bid_sizes)
    active_ask = sorted(ask_sizes)
    return bid_sizes, ask_sizes, active_bid, active_ask


def _read_merged_quote_events(raw_dir: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    bid_df, _ = bfl.read_quote_updates(raw_dir / "bid_quote_updates.ndjson")
    ask_df, _ = bfl.read_quote_updates(raw_dir / "ask_quote_updates.ndjson")
    return bfl.merge_events(bid_df, ask_df)


def _best_mid(active_bid: list[int], active_ask: list[int]) -> float:
    if not active_bid or not active_ask:
        return float("nan")
    best_bid = active_bid[-1]
    best_ask = active_ask[0]
    if best_bid > best_ask:
        return float("nan")
    return (_tick_to_price_float(best_bid) + _tick_to_price_float(best_ask)) / 2.0


def _bar_lookup_for_ts(ts_value: int, bars: pd.DataFrame, start_i: int) -> int:
    starts = bars["bar_start_ts_ns"].to_numpy(dtype=np.int64)
    ends = bars["bar_end_ts_ns"].to_numpy(dtype=np.int64)
    i = start_i
    while i < len(bars) and ts_value >= ends[i]:
        i += 1
    if i >= len(bars):
        return i
    if ts_value < starts[i]:
        return -i - 1
    return i


def _aggregate_trades(raw_dir: Path, bars: pd.DataFrame) -> dict[tuple[int, int], list[float]]:
    path = raw_dir / "trades.ndjson"
    if not path.exists() or bars.empty:
        return {}
    out: dict[tuple[int, int], list[float]] = {}
    bar_i = 0
    starts = bars["bar_start_ts_ns"].to_numpy(dtype=np.int64)
    ends = bars["bar_end_ts_ns"].to_numpy(dtype=np.int64)
    with path.open() as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            ts = int(row.get("timestamp_ns", row.get("source_ts_ns", 0)))
            while bar_i < len(bars) and ts >= ends[bar_i]:
                bar_i += 1
            if bar_i >= len(bars):
                break
            if ts < starts[bar_i]:
                continue
            tick = _price_to_tick_scalar(row.get("price", 0.0))
            size = _safe_float(row.get("size"), 0.0)
            if size <= 0:
                continue
            key = (bar_i, tick)
            vals = out.setdefault(key, [0.0, 0.0, 0.0])
            vals[0] += size
            if str(row.get("aggressor_side", "")).upper().startswith("B"):
                vals[1] += size
            elif str(row.get("aggressor_side", "")).upper().startswith("S"):
                vals[2] += size
    return out


def _collect_reference_levels(bars: pd.DataFrame) -> list[tuple[str, float]]:
    levels: list[tuple[str, float]] = []
    try:
        resistance, support = bfl.compute_sr_levels(bars, lb=4, cluster_dist=6.0)
        levels.extend(("R", float(px)) for px, _count, _score in resistance[:12])
        levels.extend(("S", float(px)) for px, _count, _score in support[:12])
    except Exception:
        pass
    try:
        profile = bfl.volume_profile(bars)
        if profile:
            levels.extend([
                ("POC", float(profile["poc"])),
                ("VAH", float(profile["vah"])),
                ("VAL", float(profile["val"])),
            ])
            levels.extend(("HVN", float(px)) for px in profile.get("hvn_px", [])[:24])
            levels.extend(("LVN", float(px)) for px in profile.get("lvn_px", [])[:24])
    except Exception:
        pass
    try:
        projected = bfl.load_projected_levels(bfl.FEATURES_BASE / "projected_levels_NQM6_to_NQU6.csv")
        for _, row in projected.iterrows():
            px = row.get("projected_level_price")
            if pd.notna(px):
                levels.append((f"PROJECTED_PRIOR_NQM6_{row.get('level_type', '')}", float(px)))
    except Exception:
        pass
    return levels


def _nearest_level(levels: list[tuple[str, float]], price: float) -> tuple[str, float]:
    if not levels:
        return "", float("nan")
    name, level_price, dist = bfl.nearest_level(levels, price)
    if name is None:
        return "", float("nan")
    return str(name), float(dist)


def _cache_current(symbol: str, date: str, depth: int, bars: pd.DataFrame) -> bool:
    cpath = level_cache_path(symbol, date, depth)
    mpath = level_cache_meta_path(symbol, date, depth)
    if not cpath.exists() or not mpath.exists() or bars.empty:
        return False
    try:
        meta = json.loads(mpath.read_text())
    except Exception:
        return False
    last_end = int(bars["bar_end_ts_ns"].iloc[-1])
    return (
        int(meta.get("bars_count", -1)) == len(bars)
        and int(meta.get("last_bar_end_ts_ns", -1)) == last_end
        and int(meta.get("depth", -1)) == depth
        and str(meta.get("symbol", "")) == symbol
        and str(meta.get("date", "")) == date
    )


def _cache_meta(symbol: str, date: str, depth: int) -> dict:
    try:
        return json.loads(level_cache_meta_path(symbol, date, depth).read_text())
    except Exception:
        return {}


def _path_signature(path: Path) -> dict:
    try:
        st = path.stat()
    except OSError:
        return {"path": str(path), "exists": False}
    return {
        "path": str(path),
        "exists": True,
        "size": int(st.st_size),
        "mtime_ns": int(st.st_mtime_ns),
        "inode": int(st.st_ino),
    }


def _raw_input_signature(symbol: str, date: str) -> dict:
    raw_dir = bfl.RAW_BASE / date / symbol
    return {
        "depth": _path_signature(raw_dir / "depth.ndjson"),
        "bid": _path_signature(raw_dir / "bid_quote_updates.ndjson"),
        "ask": _path_signature(raw_dir / "ask_quote_updates.ndjson"),
        "trades": _path_signature(raw_dir / "trades.ndjson"),
        "vol500": _path_signature(bfl.FEATURES_BASE / date / f"{symbol}_vol500.ndjsonl"),
    }


def _latest_active_date(symbol: str) -> Optional[str]:
    raw_dates: list[str] = []
    feature_dates: list[str] = []
    try:
        raw_dates = sorted(
            p.name for p in bfl.RAW_BASE.iterdir()
            if p.is_dir() and (p / symbol).is_dir()
        )
    except OSError:
        pass
    try:
        feature_dates = sorted(
            p.name for p in bfl.FEATURES_BASE.iterdir()
            if p.is_dir() and (p / f"{symbol}_vol500.ndjsonl").exists()
        )
    except OSError:
        pass
    both = sorted(set(raw_dates) & set(feature_dates))
    if both:
        return both[-1]
    if feature_dates:
        return feature_dates[-1]
    if raw_dates:
        return raw_dates[-1]
    return None


def _is_latest_active_date(symbol: str, date: str) -> bool:
    latest = _latest_active_date(symbol)
    return bool(latest and str(date) == latest)


def _level_cache_sparsity(
    symbol: str,
    date: str,
    depth: int,
    bars: pd.DataFrame,
    *,
    missing_is_sparse: bool = False,
) -> dict:
    info = {
        "symbol": symbol,
        "date": date,
        "depth": int(depth),
        "sparse": False,
        "reason": "",
        "cache_exists": False,
        "feature_rows": int(len(bars)),
        "cache_rows": 0,
        "unique_cached_bars": 0,
        "missing_recent_bar_count": 0,
        "recent_median_cell_count": 0.0,
        "recent_min_cell_count": 0,
    }
    cpath = level_cache_path(symbol, date, depth)
    if bars.empty:
        info.update({"sparse": True, "reason": "empty_feature_bars"})
        return info
    if not cpath.exists():
        info.update({
            "sparse": bool(missing_is_sparse),
            "reason": "missing_source_level_cache" if missing_is_sparse else "source_level_cache_missing",
        })
        return info

    info["cache_exists"] = True
    try:
        df = pd.read_parquet(cpath, columns=["bar_idx"])
    except Exception as exc:
        info.update({"sparse": True, "reason": f"source_level_cache_read_failed: {exc}"})
        return info
    if df.empty or "bar_idx" not in df.columns:
        info.update({"sparse": True, "reason": "source_level_cache_empty"})
        return info

    bar_ids = [int(x) for x in bars["bar_index"].tolist()]
    cached_counts = df.groupby("bar_idx", sort=True).size()
    cached_ids = set(int(x) for x in cached_counts.index.tolist())
    recent_ids = bar_ids[-min(20, len(bar_ids)):]
    recent_counts = cached_counts.reindex(recent_ids).fillna(0).astype(int)
    missing_recent = int((recent_counts <= 0).sum())
    recent_median = float(recent_counts.median()) if len(recent_counts) else 0.0
    recent_min = int(recent_counts.min()) if len(recent_counts) else 0
    unique_cached = int(len(cached_ids & set(bar_ids)))
    cache_rows = int(len(df))

    info.update({
        "cache_rows": cache_rows,
        "unique_cached_bars": unique_cached,
        "missing_bar_count": int(len(set(bar_ids) - cached_ids)),
        "cache_min_bar_idx": int(min(cached_ids)) if cached_ids else None,
        "cache_max_bar_idx": int(max(cached_ids)) if cached_ids else None,
        "latest_feature_bar_idx": int(bar_ids[-1]) if bar_ids else None,
        "missing_recent_bar_count": missing_recent,
        "recent_median_cell_count": recent_median,
        "recent_min_cell_count": recent_min,
        "recent_cell_counts": {str(int(k)): int(v) for k, v in recent_counts.items()},
    })

    if len(bar_ids) < 4:
        if unique_cached == 0:
            info.update({"sparse": True, "reason": "no_cached_bars_for_small_session"})
        return info

    min_unique = max(1, int(len(bar_ids) * 0.85))
    max_recent_missing = max(2, int(len(recent_ids) * 0.25))
    min_recent_median_cells = 20 if int(depth) <= 5 else 25

    if unique_cached < min_unique:
        info.update({
            "sparse": True,
            "reason": f"cached_bar_coverage_low:{unique_cached}/{len(bar_ids)}",
        })
    elif missing_recent > max_recent_missing:
        info.update({
            "sparse": True,
            "reason": f"recent_cached_bars_missing:{missing_recent}/{len(recent_ids)}",
        })
    elif len(recent_counts) >= 8 and recent_median < min_recent_median_cells:
        info.update({
            "sparse": True,
            "reason": (
                f"recent_cell_count_low:median={recent_median:.1f}"
                f"<{min_recent_median_cells}"
            ),
        })
    return info


def _level_caches_sparsity(
    symbol: str,
    date: str,
    depths: Iterable[int],
    bars: pd.DataFrame,
    *,
    missing_is_sparse: bool = False,
) -> dict:
    by_depth = {
        str(int(depth)): _level_cache_sparsity(
            symbol, date, int(depth), bars, missing_is_sparse=missing_is_sparse
        )
        for depth in depths
    }
    sparse_depths = [
        depth for depth, diag in by_depth.items()
        if bool(diag.get("sparse"))
    ]
    return {
        "sparse": bool(sparse_depths),
        "sparse_depths": sparse_depths,
        "by_depth": by_depth,
    }


# ── Incremental V3 checkpoint ─────────────────────────────────────────────── #

def _v3_state_dir() -> Path:
    return bfl.CACHE_DIR / "state"


def v3_state_path(symbol: str, date: str) -> Path:
    return _v3_state_dir() / f"{symbol}_{date}_v3_level_state.pkl"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_v3_state(symbol: str, date: str) -> Optional[dict]:
    p = v3_state_path(symbol, date)
    if not p.exists():
        return None
    try:
        with open(p, "rb") as f:
            state = pickle.load(f)
        if state.get("schema_version") != 2:
            return None
        # Validate required keys
        for k in ("bid_sizes", "ask_sizes", "bid_file_offset", "ask_file_offset",
                  "last_complete_bar_idx"):
            if k not in state:
                return None
        return state
    except Exception:
        return None


def save_v3_state(symbol: str, date: str, state: dict) -> None:
    p = v3_state_path(symbol, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".pkl.tmp")
    with open(tmp, "wb") as f:
        pickle.dump(state, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(p)


def _byte_offset_after_n_lines(path: Path, start: int, n: int) -> int:
    """Return byte offset after exactly n complete non-empty NDJSON lines starting at `start`.

    Used to roll back the saved file offset when the event loop breaks early
    due to overflow (events past the last vol500 bar).  By saving the rolled-back
    offset, those overflow events are re-read on the next cycle once the new
    vol500 bar appears.
    """
    if n <= 0:
        return start
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read()
    count = 0
    pos = 0
    while pos < len(data):
        nl = data.find(b"\n", pos)
        if nl < 0:
            break
        if data[pos:nl].strip():
            count += 1
            if count >= n:
                return start + nl + 1
        pos = nl + 1
    return start + len(data)


def _load_initial_book_arrays(depth_path: Path) -> tuple[np.ndarray, np.ndarray]:
    bid_sizes = np.zeros(bfl.N_TICKS, dtype=np.float64)
    ask_sizes = np.zeros(bfl.N_TICKS, dtype=np.float64)
    with depth_path.open() as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            side = str(row.get("side", ""))
            size = _safe_float(row.get("size"), 0.0)
            price = _safe_float(row.get("price"), 0.0)
            tick = int(round((price - bfl.PRICE_MIN) / bfl.TICK))
            if size <= 0 or tick < 0 or tick >= bfl.N_TICKS:
                continue
            if side == "B":
                bid_sizes[tick] = size
            elif side == "A":
                ask_sizes[tick] = size
    return bid_sizes, ask_sizes


def _active_from_arr(sizes: np.ndarray) -> list:
    return sorted(int(i) for i in np.nonzero(sizes)[0].tolist())


def incremental_build_level_caches(
    symbol: str,
    date: str,
    depths: list,
    force: bool = False,
) -> dict:
    """True incremental V3 level-cache builder.

    Uses a persistent pickle checkpoint (book state + file offsets) so that
    after the initial backfill only newly appended raw lines are processed.
    """
    raw_dir = bfl.RAW_BASE / date / symbol
    bid_path = raw_dir / "bid_quote_updates.ndjson"
    ask_path = raw_dir / "ask_quote_updates.ndjson"
    depth_path = raw_dir / "depth.ndjson"
    vol500_path = bfl.FEATURES_BASE / date / f"{symbol}_vol500.ndjsonl"

    bars = bfl.load_vol500_bars(vol500_path)
    if bars.empty:
        return {"ok": False, "reason": f"empty vol500 bars: {vol500_path}"}

    try:
        bid_stat = bid_path.stat()
        ask_stat = ask_path.stat()
    except OSError as exc:
        return {"ok": False, "reason": f"cannot stat raw files: {exc}"}

    bid_inode = bid_stat.st_ino
    ask_inode = ask_stat.st_ino
    bid_file_size = bid_stat.st_size
    ask_file_size = ask_stat.st_size

    state = None if force else load_v3_state(symbol, date)
    rotation_detected = False

    if state is not None:
        if (state.get("bid_inode") != bid_inode
                or state.get("ask_inode") != ask_inode
                or bid_file_size < state.get("bid_file_offset", 0)
                or ask_file_size < state.get("ask_file_offset", 0)):
            rotation_detected = True
            state = None

    initial_backfill = state is None

    if initial_backfill:
        if not depth_path.exists():
            return {"ok": False, "reason": f"depth.ndjson missing: {depth_path}"}
        bid_sizes, ask_sizes = _load_initial_book_arrays(depth_path)
        bid_offset = 0
        ask_offset = 0
        last_complete_bar_idx = -1
        forming_bar_idx = 0
        forming_bar_aggs: dict = {d: {} for d in depths}
    else:
        bid_sizes = state["bid_sizes"].copy()
        ask_sizes = state["ask_sizes"].copy()
        bid_offset = state["bid_file_offset"]
        ask_offset = state["ask_file_offset"]
        last_complete_bar_idx = state.get("last_complete_bar_idx", -1)
        forming_bar_idx = state.get("forming_bar_idx", last_complete_bar_idx + 1)
        forming_bar_aggs = state.get("forming_bar_aggs", {d: {} for d in depths})
        # Ensure all depth keys present
        for d in depths:
            forming_bar_aggs.setdefault(d, {})

    # Clamp forming_bar_idx to valid range
    forming_bar_idx = max(0, min(forming_bar_idx, len(bars) - 1))

    # Bug 1 fix: helper — if the forming bar is the last vol500 bar and its
    # bar_end_ts_ns is already in the past by > 3s, vol500 has closed it.
    # Return (forming_actual_idx, last_closed_idx) accounting for this.
    _CLOSED_GAP_NS = 3_000_000_000  # 3s buffer for raw-file flush lag
    def _resolve_forming_and_closed(fb_pos: int) -> tuple[Optional[int], int]:
        """Return (forming_bar_actual_idx, last_closed_bar_idx).

        forming_bar_actual_idx is None when all vol500 bars are closed.
        """
        last_pos = len(bars) - 1
        if (fb_pos == last_pos
                and int(bars["bar_end_ts_ns"].iloc[last_pos]) < time.time_ns() - _CLOSED_GAP_NS):
            # Last vol500 bar has passed its end time — all bars are CLOSED.
            return None, int(bars["bar_index"].iloc[last_pos])
        actual = int(bars["bar_index"].iloc[min(fb_pos, last_pos)])
        last_closed = (
            int(bars["bar_index"].iloc[fb_pos - 1])
            if fb_pos > 0 else actual
        )
        return actual, last_closed

    # Check for new data
    if not initial_backfill and bid_file_size == bid_offset and ask_file_size == ask_offset:
        # Nothing new — cache is already up to date
        rows_by_depth = {str(d): 0 for d in depths}
        try:
            for d in depths:
                cp = level_cache_path(symbol, date, d)
                if cp.exists():
                    rows_by_depth[str(d)] = int(pd.read_parquet(cp).shape[0])
        except Exception:
            pass
        _cached_forming_actual, _cached_last_closed = _resolve_forming_and_closed(forming_bar_idx)
        return {
            "ok": True,
            "cached": True,
            "incremental_update": True,
            "full_raw_reparse": False,
            "raw_lines_processed": 0,
            "affected_bars": 0,
            "newly_complete_bars": 0,
            "rows_by_depth": rows_by_depth,
            "paths": {str(d): str(level_cache_path(symbol, date, d)) for d in depths},
            "forming_bar_actual_idx": _cached_forming_actual,
            "last_closed_bar_idx": _cached_last_closed,
        }

    # Read new events from saved offsets
    bid_df, new_bid_offset = bfl.read_quote_updates(bid_path, bid_offset)
    ask_df, new_ask_offset = bfl.read_quote_updates(ask_path, ask_offset)
    raw_lines_processed = len(bid_df) + len(ask_df)

    if bid_df.empty and ask_df.empty:
        # No complete new lines available yet
        save_v3_state(symbol, date, {
            "schema_version": 2, "symbol": symbol, "session_date": date,
            "bid_inode": bid_inode, "ask_inode": ask_inode,
            "bid_file_offset": bid_offset, "ask_file_offset": ask_offset,
            "bid_file_size": bid_file_size, "ask_file_size": ask_file_size,
            "last_complete_bar_idx": last_complete_bar_idx,
            "forming_bar_idx": forming_bar_idx,
            "forming_bar_aggs": forming_bar_aggs,
            "bid_sizes": bid_sizes, "ask_sizes": ask_sizes,
            "created_utc": (state or {}).get("created_utc", _utc_now_iso()),
            "updated_utc": _utc_now_iso(),
        })
        _nodata_forming_actual, _nodata_last_closed = _resolve_forming_and_closed(forming_bar_idx)
        return {
            "ok": True, "cached": True, "incremental_update": True,
            "full_raw_reparse": False, "raw_lines_processed": 0, "affected_bars": 0,
            "newly_complete_bars": 0,
            "rows_by_depth": {str(d): 0 for d in depths},
            "paths": {str(d): str(level_cache_path(symbol, date, d)) for d in depths},
            "forming_bar_actual_idx": _nodata_forming_actual,
            "last_closed_bar_idx": _nodata_last_closed,
        }

    ts_arr, side_arr, tick_arr, size_arr = bfl.merge_events(bid_df, ask_df)

    # Reconstruct active sorted lists from arrays
    active_bid = _active_from_arr(bid_sizes)
    active_ask = _active_from_arr(ask_sizes)

    starts = bars["bar_start_ts_ns"].to_numpy(dtype=np.int64)
    ends = bars["bar_end_ts_ns"].to_numpy(dtype=np.int64)
    max_depth = max(depths)

    # bar_aggs[bar_pos][depth][tick] = [signed, abs, bid_add, bid_pull, ask_add, ask_pull, net_bid, net_ask, mid]
    bar_aggs: dict = {}
    bar_aggs[forming_bar_idx] = forming_bar_aggs  # continue accumulating forming bar

    bar_i = forming_bar_idx
    current_bar_idx = forming_bar_idx
    newly_complete: list = []
    events_used = 0
    _first_overflow_ev_i = len(ts_arr)  # sentinel: no overflow

    for ev_i in range(len(ts_arr)):
        ts = int(ts_arr[ev_i])

        # Advance bar pointer, recording complete bars
        while bar_i < len(bars) and ts >= ends[bar_i]:
            if bar_i > last_complete_bar_idx:
                newly_complete.append(bar_i)
            bar_i += 1
            current_bar_idx = bar_i
            if bar_i < len(bars):
                bar_aggs.setdefault(bar_i, {d: {} for d in depths})

        # MISSION fix: bid_sizes/ask_sizes (live book depth) are NOT bar-scoped, unlike
        # cell/flow aggregation below -- they must be updated for every raw quote event
        # regardless of whether the event's bar is yet known to vol500. The old code
        # `break`d the whole loop the instant ANY event fell past the last KNOWN
        # (closed) bar -- which is every poll, for a forming bar's entire life, since
        # `bars` never contains a row for the bar currently forming. That silently
        # skipped the book-depth mutation below for that event and all later ones in
        # the same batch, every single cycle, until the bar closed and the backlog was
        # applied in one lump. Record the overflow point once (unchanged, still drives
        # the existing offset-rollback below, which still correctly waits to attribute
        # cell flow to a bar until it's known) but keep iterating so book depth stays live.
        overflow = bar_i >= len(bars)
        if overflow and _first_overflow_ev_i == len(ts_arr):
            _first_overflow_ev_i = ev_i

        side = int(side_arr[ev_i])
        tick = int(tick_arr[ev_i])
        new_size = float(size_arr[ev_i])
        if tick < 0 or tick >= bfl.N_TICKS:
            continue

        # Update book state
        if side == 0:  # bid
            old_size = float(bid_sizes[tick])
            if new_size == old_size:
                continue
            rank_before = _bid_rank(active_bid, tick) if old_size > 0 else -1
            if old_size > 0 and new_size <= 0:
                _remove_active(active_bid, tick)
            elif old_size <= 0 and new_size > 0:
                _insert_active(active_bid, tick)
            bid_sizes[tick] = new_size if new_size > 0 else 0.0
            rank_after = _bid_rank(active_bid, tick) if new_size > 0 else -1
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
            rank_before = _ask_rank(active_ask, tick) if old_size > 0 else -1
            if old_size > 0 and new_size <= 0:
                _remove_active(active_ask, tick)
            elif old_size <= 0 and new_size > 0:
                _insert_active(active_ask, tick)
            ask_sizes[tick] = new_size if new_size > 0 else 0.0
            rank_after = _ask_rank(active_ask, tick) if new_size > 0 else -1
            delta = new_size - old_size
            signed = -delta
            bid_add = bid_pull = 0.0
            ask_add = delta if delta > 0 else 0.0
            ask_pull = (-delta) if delta < 0 else 0.0
            net_bid = 0.0
            net_ask = signed

        if overflow:
            # Book depth is already updated above. Cell/flow attribution below needs a
            # known bar to bucket into -- skip it for this event exactly as before; the
            # existing offset-rollback (below) still re-reads and correctly attributes
            # it once vol500 catches up. This is the ONLY behavior change from before:
            # previously this event (and the rest of the batch) never even reached the
            # book-state mutation above.
            continue

        ranks = [r for r in (rank_before, rank_after) if r > 0]
        if not ranks:
            continue
        min_rank = min(ranks)
        if min_rank > max_depth:
            continue
        if ts < starts[bar_i]:
            continue

        mid = _best_mid(active_bid, active_ask)
        events_used += 1
        abs_flow = abs(signed)

        cur_aggs = bar_aggs.setdefault(bar_i, {d: {} for d in depths})
        for depth in depths:
            if min_rank > depth:
                continue
            tick_map = cur_aggs.setdefault(depth, {})
            vals = tick_map.setdefault(tick, [0.0] * 9)
            vals[0] += signed
            vals[1] += abs_flow
            vals[2] += bid_add
            vals[3] += bid_pull
            vals[4] += ask_add
            vals[5] += ask_pull
            vals[6] += net_bid
            vals[7] += net_ask
            vals[8] = mid

    # Overflow rollback: if any events were past the last vol500 bar, roll back
    # the saved file offsets to just before those events.  On the next compact
    # cycle, bars_df will (hopefully) contain the new vol500 bar, so those
    # events will be attributed to the correct bar instead of being lost.
    # Only roll back within a 2-hour window so end-of-session noise doesn't
    # trigger an infinite re-read loop.
    _OVERFLOW_ROLLBACK_WINDOW_NS = 2 * 3600 * 1_000_000_000
    if _first_overflow_ev_i < len(ts_arr):
        _first_overflow_ts = int(ts_arr[_first_overflow_ev_i])
        _last_bar_end_ts = int(ends[-1])
        if _first_overflow_ts - _last_bar_end_ts < _OVERFLOW_ROLLBACK_WINDOW_NS:
            _ts_bid = bid_df["event_ts_ns"].to_numpy(dtype=np.int64)
            _ts_ask = ask_df["event_ts_ns"].to_numpy(dtype=np.int64)
            _n_bid_safe = int(np.searchsorted(_ts_bid, _last_bar_end_ts, side="left"))
            _n_ask_safe = int(np.searchsorted(_ts_ask, _last_bar_end_ts, side="left"))
            new_bid_offset = _byte_offset_after_n_lines(bid_path, bid_offset, _n_bid_safe)
            new_ask_offset = _byte_offset_after_n_lines(ask_path, ask_offset, _n_ask_safe)

    # forming bar after loop = bar_i (or len(bars)-1 if we ran out)
    new_forming_bar_idx = min(bar_i, len(bars) - 1)
    new_last_complete = max(newly_complete) if newly_complete else last_complete_bar_idx
    newly_complete_set = set(newly_complete)

    # Bug 1 fix: if the computed forming bar is the last vol500 bar AND its
    # bar_end_ts_ns is already in the past, vol500 has closed it.  Force it
    # to CLOSED so the compact never shows the last vol500 bar as FORMING.
    if (new_forming_bar_idx == len(bars) - 1
            and int(bars["bar_end_ts_ns"].iloc[new_forming_bar_idx]) < time.time_ns() - _CLOSED_GAP_NS):
        _last_pos = new_forming_bar_idx
        if _last_pos not in newly_complete_set:
            newly_complete.append(_last_pos)
            newly_complete_set.add(_last_pos)
        new_last_complete = _last_pos
        # Use len(bars) as sentinel: "no forming bar within vol500"
        new_forming_bar_idx = len(bars)

    # Build new parquet rows for all bars in bar_aggs
    ref_levels = _collect_reference_levels(bars)
    bfl.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    rows_by_depth: dict = {}
    # New forming bar's actual bar_index (used for keep threshold and stale-FORMING cleanup).
    # When new_forming_bar_idx == len(bars) (sentinel), use last_bar_index + 1 so the
    # stale-FORMING cleanup drops all vol500 FORMING rows (none should exist).
    new_forming_actual_idx = (
        int(bars.iloc[new_forming_bar_idx]["bar_index"])
        if new_forming_bar_idx < len(bars)
        else int(bars.iloc[-1]["bar_index"]) + 1
    )

    for depth in depths:
        cpath = level_cache_path(symbol, date, depth)
        new_rows = []

        for bar_pos in sorted(bar_aggs.keys()):
            if bar_pos >= len(bars):
                continue
            bar = bars.iloc[bar_pos]
            tick_map = bar_aggs[bar_pos].get(depth, {})
            rows_before = len(new_rows)
            for tick, vals in sorted(tick_map.items()):
                price = _tick_to_price_float(int(tick))
                mid = vals[8]
                if not np.isfinite(mid):
                    mid = _safe_float(bar.get("mid_mean"),
                                      _safe_float(bar.get("px_close"), price))
                if abs(price - mid) <= bfl.TICK:
                    side_zone = "near_mid"
                elif price < mid:
                    side_zone = "bid"
                else:
                    side_zone = "ask"
                nearest, distance = _nearest_level(ref_levels, price)
                new_rows.append({
                    "bar_idx": int(bar["bar_index"]),
                    "timestamp_utc": bar.get("timestamp_utc", bar.get("timestamp", "")),
                    "bar_start_ts_ns": int(bar["bar_start_ts_ns"]),
                    "bar_end_ts_ns": int(bar["bar_end_ts_ns"]),
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
                    "close_price": _safe_float(bar.get("px_close")),
                    "mid_price": float(mid),
                    "nearest_level": nearest,
                    "distance_to_nearest_level": float(distance) if np.isfinite(float(distance)) else 0.0,
                    "bar_state": "FORMING" if bar_pos == new_forming_bar_idx else "CLOSED",
                })
            # If this bar completed in this cycle but had no book events, write a
            # zero-flow placeholder so the bar is not silently dropped from the compact.
            if len(new_rows) == rows_before and bar_pos in newly_complete_set:
                close_px = _safe_float(bar.get("px_close"))
                if close_px is not None and np.isfinite(close_px):
                    close_tick = int(round(close_px / bfl.TICK))
                    mid_px = _safe_float(bar.get("mid_mean"), close_px)
                    nearest, distance = _nearest_level(ref_levels, close_px)
                    new_rows.append({
                        "bar_idx": int(bar["bar_index"]),
                        "timestamp_utc": bar.get("timestamp_utc", bar.get("timestamp", "")),
                        "bar_start_ts_ns": int(bar["bar_start_ts_ns"]),
                        "bar_end_ts_ns": int(bar["bar_end_ts_ns"]),
                        "price_level": float(close_px),
                        "price_tick": close_tick,
                        "depth_n": int(depth),
                        "side_zone": "near_mid",
                        "signed_flow": 0.0, "abs_flow": 0.0,
                        "bid_add": 0.0, "bid_pull": 0.0,
                        "ask_add": 0.0, "ask_pull": 0.0,
                        "net_bid_flow": 0.0, "net_ask_flow": 0.0,
                        "trade_volume_at_price": 0.0,
                        "buy_trade_volume_at_price": 0.0,
                        "sell_trade_volume_at_price": 0.0,
                        "close_price": float(close_px),
                        "mid_price": float(mid_px) if mid_px is not None and np.isfinite(mid_px) else float(close_px),
                        "nearest_level": nearest,
                        "distance_to_nearest_level": float(distance) if np.isfinite(float(distance)) else 0.0,
                        "bar_state": "CLOSED",
                    })

        new_df = pd.DataFrame(new_rows)

        if initial_backfill or not cpath.exists():
            if new_df.empty:
                rows_by_depth[str(depth)] = 0
                continue
            _atomic_write_parquet(new_df, cpath)
            rows_by_depth[str(depth)] = len(new_df)
        else:
            # Keep all existing CLOSED bars that are NOT covered by new_df (to avoid
            # duplicates).  Also drop stale FORMING rows for bars now < new_forming to
            # prevent old FORMING state persisting after the bar has closed.
            try:
                existing = pd.read_parquet(cpath)
                if "bar_state" not in existing.columns:
                    existing["bar_state"] = "CLOSED"
            except Exception:
                existing = pd.DataFrame()
            if new_df.empty:
                # Nothing new this cycle — preserve all existing rows intact.
                combined = existing
            else:
                new_bar_idxs = set(new_df["bar_idx"].unique())
                keep = existing[
                    (~existing["bar_idx"].isin(new_bar_idxs)) &
                    ~((existing["bar_state"] == "FORMING") &
                      (existing["bar_idx"] < new_forming_actual_idx))
                ]
                combined = pd.concat([keep, new_df], ignore_index=True)
            if combined.empty:
                rows_by_depth[str(depth)] = 0
                continue
            _atomic_write_parquet(combined, cpath)
            rows_by_depth[str(depth)] = len(combined)

        # Write meta
        meta = {
            "symbol": symbol, "date": date, "depth": int(depth),
            "bars_count": int(len(bars)),
            "first_bar_idx": int(bars["bar_index"].iloc[0]),
            "last_bar_idx": int(bars["bar_index"].iloc[-1]),
            "last_bar_end_ts_ns": int(bars["bar_end_ts_ns"].iloc[-1]),
            "rows": rows_by_depth.get(str(depth), 0),
            "events_used": int(events_used),
            "source": "RAW_RITHMIC_INCREMENTAL",
            "incremental": not initial_backfill,
            "full_raw_reparse": initial_backfill,
        }
        level_cache_meta_path(symbol, date, depth).write_text(
            json.dumps(meta, indent=2, sort_keys=True))

    # Save checkpoint.
    # When new_forming_bar_idx == len(bars) (sentinel: no forming bar), clamp
    # the stored value to len(bars)-1 so the next load's clamp doesn't corrupt
    # it.  The sentinel case is re-detected at the start of the next cycle via
    # _resolve_forming_and_closed().
    _save_forming_bar_idx = min(new_forming_bar_idx, len(bars) - 1)
    new_forming_aggs = bar_aggs.get(_save_forming_bar_idx, {d: {} for d in depths})
    new_state = {
        "schema_version": 2, "symbol": symbol, "session_date": date,
        "bid_inode": bid_inode, "ask_inode": ask_inode,
        "bid_file_offset": new_bid_offset, "ask_file_offset": new_ask_offset,
        "bid_file_size": bid_file_size, "ask_file_size": ask_file_size,
        "last_complete_bar_idx": new_last_complete,
        "forming_bar_idx": _save_forming_bar_idx,
        "forming_bar_aggs": new_forming_aggs,
        "bid_sizes": bid_sizes, "ask_sizes": ask_sizes,
        "last_cached_bar_idx_by_depth": {
            str(d): int(bars["bar_index"].iloc[min(new_forming_bar_idx, len(bars) - 1)])
            for d in depths
        },
        "created_utc": (state or {}).get("created_utc", _utc_now_iso()),
        "updated_utc": _utc_now_iso(),
    }
    save_v3_state(symbol, date, new_state)

    # Resolve forming / last_closed using the same closed-bar logic used in the
    # early-return paths so all code paths return consistent values.
    forming_bar_actual_idx_val, last_closed_bar_idx_val = _resolve_forming_and_closed(
        _save_forming_bar_idx
    )
    return {
        "ok": True,
        "cached": False,
        "incremental_update": not initial_backfill,
        "full_raw_reparse": bool(initial_backfill),
        "initial_backfill": bool(initial_backfill),
        "rotation_detected": bool(rotation_detected),
        "raw_lines_processed": int(raw_lines_processed),
        "events_used": int(events_used),
        "affected_bars": int(len(bar_aggs)),
        "newly_complete_bars": int(len(newly_complete)),
        "forming_bar_idx": int(_save_forming_bar_idx),
        "forming_bar_actual_idx": forming_bar_actual_idx_val,
        "last_closed_bar_idx": last_closed_bar_idx_val,
        "last_complete_bar_idx": int(new_last_complete),
        "bid_file_offset": int(new_bid_offset),
        "ask_file_offset": int(new_ask_offset),
        "bid_file_size": int(bid_file_size),
        "ask_file_size": int(ask_file_size),
        "rows_by_depth": rows_by_depth,
        "paths": {str(d): str(level_cache_path(symbol, date, d)) for d in depths},
    }


def ensure_level_cache(
    symbol: str = "NQU6",
    date: str = "2026-06-15",
    depth: int = 5,
    force: bool = False,
) -> dict:
    result = build_level_caches(symbol=symbol, date=date, depths=[depth], force=force)
    if result.get("ok"):
        result["cache_path"] = str(level_cache_path(symbol, date, depth))
    return result


def build_level_caches(
    symbol: str = "NQU6",
    date: str = "2026-06-15",
    depths: Optional[Iterable[int]] = None,
    force: bool = False,
) -> dict:
    depths = sorted({int(d) for d in (depths or DEPTH_CHOICES)})
    bad_depths = [d for d in depths if d not in DEPTH_CHOICES]
    if bad_depths:
        return {"ok": False, "reason": f"unsupported depth(s): {bad_depths}"}

    vol500_path = bfl.FEATURES_BASE / date / f"{symbol}_vol500.ndjsonl"
    bars = bfl.load_vol500_bars(vol500_path)
    if bars.empty:
        return {"ok": False, "reason": f"empty vol500 bars: {vol500_path}"}

    # Route all dates — live and historical — through the checkpointed incremental
    # builder. Initial backfill (no checkpoint) reads all raw events and saves the
    # first checkpoint; subsequent calls process only new events since the saved
    # byte offsets. This eliminates the 60-140 s full-replay lag on the live date.
    result = incremental_build_level_caches(symbol=symbol, date=date, depths=depths, force=force)
    if result.get("ok"):
        if not result.get("cached"):
            bars = bfl.load_vol500_bars(bfl.FEATURES_BASE / date / f"{symbol}_vol500.ndjsonl")
            sparse_after = _level_caches_sparsity(symbol, date, depths, bars)
            if sparse_after.get("sparse") and not result.get("initial_backfill"):
                repair = incremental_build_level_caches(
                    symbol=symbol, date=date, depths=depths, force=True
                )
                if repair.get("ok"):
                    repair.update({
                        "rebuild_reason": "sparse_incremental_repaired",
                        "sparse_cache_repair": sparse_after,
                        "incremental_reader_enabled": True,
                    })
                return repair
        result.setdefault("incremental_reader_enabled", True)
        return result

    # Incremental failed (e.g. depth.ndjson missing on initial backfill).
    # Fall back to the legacy full-replay builder only as a last resort.
    fallback = _build_level_caches_full_replay(symbol=symbol, date=date, depths=depths, force=True)
    if fallback.get("ok"):
        fallback.update({
            "rebuild_reason": f"incremental_failed_fallback: {result.get('reason', '')}",
            "incremental_reader_enabled": False,
        })
    return fallback


def _build_level_caches_full_replay(
    symbol: str = "NQU6",
    date: str = "2026-06-15",
    depths: Optional[Iterable[int]] = None,
    force: bool = False,
) -> dict:
    """Legacy full-replay builder (kept for reference / forced rebuilds)."""
    depths = sorted({int(d) for d in (depths or DEPTH_CHOICES)})
    raw_dir = bfl.RAW_BASE / date / symbol
    depth_path = raw_dir / "depth.ndjson"
    vol500_path = bfl.FEATURES_BASE / date / f"{symbol}_vol500.ndjsonl"
    bars = bfl.load_vol500_bars(vol500_path)
    if bars.empty:
        return {"ok": False, "reason": f"empty vol500 bars: {vol500_path}", "support": {}}

    if not force and all(_cache_current(symbol, date, depth, bars) for depth in depths):
        metas = {str(depth): _cache_meta(symbol, date, depth) for depth in depths}
        first_meta = metas.get(str(depths[0]), {}) if depths else {}
        return {
            "ok": True,
            "cached": True,
            "support": {"ok": True, "reason": "cache_current_raw_inspect_skipped",
                         "top_depth_supported": True, "incremental_reader_enabled": False},
            "rows_by_depth": {str(depth): int(metas[str(depth)].get("rows", 0)) for depth in depths},
            "paths": {str(depth): str(level_cache_path(symbol, date, depth)) for depth in depths},
            "full_raw_reparse": False,
            "incremental_reader_enabled": False,
            "raw_input_signature": first_meta.get("raw_input_signature"),
            "bid_file_size": first_meta.get("bid_file_size"),
            "ask_file_size": first_meta.get("ask_file_size"),
        }

    support = inspect_raw_depth_support(symbol, date)
    if not support.get("ok"):
        return {"ok": False, "reason": support.get("reason"), "support": support}
    raw_signature = _raw_input_signature(symbol, date)

    bid_sizes, ask_sizes, active_bid, active_ask = _load_initial_book(depth_path)
    if len(active_bid) < 20 or len(active_ask) < 20:
        return {
            "ok": False,
            "reason": "initial depth snapshot has fewer than 20 active bid or ask levels",
            "support": support,
        }

    ts_arr, side_arr, tick_arr, size_arr = _read_merged_quote_events(raw_dir)
    if len(ts_arr) == 0:
        return {"ok": False, "reason": "no quote update events found", "support": support}

    starts = bars["bar_start_ts_ns"].to_numpy(dtype=np.int64)
    ends = bars["bar_end_ts_ns"].to_numpy(dtype=np.int64)
    max_depth = max(depths)
    aggregates: dict[int, dict[tuple[int, int], list[float]]] = {d: {} for d in depths}
    bar_i = 0
    events_used = 0
    top_depth_events = 0
    best_bid_ask_only = True

    for event_i in range(len(ts_arr)):
        ts = int(ts_arr[event_i])
        while bar_i < len(bars) and ts >= ends[bar_i]:
            bar_i += 1
        if bar_i >= len(bars):
            break

        side = int(side_arr[event_i])
        tick = int(tick_arr[event_i])
        new_size = float(size_arr[event_i])
        if tick < 0:
            continue

        if side == 0:
            old_size = float(bid_sizes.get(tick, 0.0))
            if new_size == old_size:
                continue
            rank_before = _bid_rank(active_bid, tick) if old_size > 0 else -1
            if old_size > 0 and new_size <= 0:
                _remove_active(active_bid, tick)
            elif old_size <= 0 and new_size > 0:
                _insert_active(active_bid, tick)
            if new_size > 0:
                bid_sizes[tick] = new_size
            else:
                bid_sizes.pop(tick, None)
            rank_after = _bid_rank(active_bid, tick) if new_size > 0 else -1
            delta = new_size - old_size
            signed = delta
            bid_add = delta if delta > 0 else 0.0
            bid_pull = -delta if delta < 0 else 0.0
            ask_add = 0.0
            ask_pull = 0.0
            net_bid = signed
            net_ask = 0.0
        else:
            old_size = float(ask_sizes.get(tick, 0.0))
            if new_size == old_size:
                continue
            rank_before = _ask_rank(active_ask, tick) if old_size > 0 else -1
            if old_size > 0 and new_size <= 0:
                _remove_active(active_ask, tick)
            elif old_size <= 0 and new_size > 0:
                _insert_active(active_ask, tick)
            if new_size > 0:
                ask_sizes[tick] = new_size
            else:
                ask_sizes.pop(tick, None)
            rank_after = _ask_rank(active_ask, tick) if new_size > 0 else -1
            delta = new_size - old_size
            signed = -delta
            bid_add = 0.0
            bid_pull = 0.0
            ask_add = delta if delta > 0 else 0.0
            ask_pull = -delta if delta < 0 else 0.0
            net_bid = 0.0
            net_ask = signed

        ranks = [r for r in (rank_before, rank_after) if r > 0]
        if not ranks:
            continue
        min_rank = min(ranks)
        if min_rank > max_depth:
            continue
        if min_rank > 1:
            best_bid_ask_only = False
        if ts < starts[bar_i]:
            continue

        mid = _best_mid(active_bid, active_ask)
        top_depth_events += 1
        events_used += 1
        abs_flow = abs(signed)
        for depth in depths:
            if min_rank > depth:
                continue
            vals = aggregates[depth].setdefault((bar_i, tick), [0.0] * 9)
            vals[0] += signed
            vals[1] += abs_flow
            vals[2] += bid_add
            vals[3] += bid_pull
            vals[4] += ask_add
            vals[5] += ask_pull
            vals[6] += net_bid
            vals[7] += net_ask
            vals[8] = mid

    trades_by_bar_price = _aggregate_trades(raw_dir, bars)
    ref_levels = _collect_reference_levels(bars)

    bfl.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    rows_by_depth: dict[str, int] = {}
    for depth in depths:
        rows = []
        for (bar_pos, tick), vals in sorted(aggregates[depth].items()):
            bar = bars.iloc[bar_pos]
            price = _tick_to_price_float(tick)
            mid = vals[8]
            if not np.isfinite(mid):
                mid = _safe_float(bar.get("mid_mean"), _safe_float(bar.get("px_close"), price))
            if abs(price - mid) <= bfl.TICK:
                side_zone = "near_mid"
            elif price < mid:
                side_zone = "bid"
            else:
                side_zone = "ask"
            trade_vals = trades_by_bar_price.get((bar_pos, tick), [0.0, 0.0, 0.0])
            nearest, distance = _nearest_level(ref_levels, price)
            rows.append({
                "bar_idx": int(bar["bar_index"]),
                "timestamp_utc": bar.get("timestamp_utc", bar.get("timestamp", "")),
                "bar_start_ts_ns": int(bar["bar_start_ts_ns"]),
                "bar_end_ts_ns": int(bar["bar_end_ts_ns"]),
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
                "trade_volume_at_price": float(trade_vals[0]),
                "buy_trade_volume_at_price": float(trade_vals[1]),
                "sell_trade_volume_at_price": float(trade_vals[2]),
                "close_price": _safe_float(bar.get("px_close")),
                "mid_price": float(mid),
                "nearest_level": nearest,
                "distance_to_nearest_level": float(distance),
            })

        df = pd.DataFrame(rows)
        cpath = level_cache_path(symbol, date, depth)
        if df.empty:
            return {
                "ok": False,
                "reason": f"no level-flow rows generated for top{depth}",
                "support": support,
                "events_used": events_used,
            }
        _atomic_write_parquet(df, cpath)
        meta = {
            "symbol": symbol,
            "date": date,
            "depth": int(depth),
            "bars_count": int(len(bars)),
            "first_bar_idx": int(bars["bar_index"].iloc[0]),
            "last_bar_idx": int(bars["bar_index"].iloc[-1]),
            "last_bar_end_ts_ns": int(bars["bar_end_ts_ns"].iloc[-1]),
            "rows": int(len(df)),
            "events_used": int(events_used),
            "top_depth_events": int(top_depth_events),
            "best_bid_ask_only": bool(best_bid_ask_only),
            "source": "RAW_RITHMIC_DEPTH_AND_QUOTE_UPDATES",
            "full_replay_safe_for_live": True,
            "raw_input_signature": raw_signature,
            "bid_file_size": raw_signature.get("bid", {}).get("size"),
            "ask_file_size": raw_signature.get("ask", {}).get("size"),
        }
        level_cache_meta_path(symbol, date, depth).write_text(json.dumps(meta, indent=2, sort_keys=True))
        rows_by_depth[str(depth)] = int(len(df))

    return {
        "ok": True,
        "cached": False,
        "support": support,
        "rows_by_depth": rows_by_depth,
        "paths": {str(depth): str(level_cache_path(symbol, date, depth)) for depth in depths},
        "events_used": int(events_used),
        "top_depth_events": int(top_depth_events),
        "best_bid_ask_only": bool(best_bid_ask_only),
        "full_raw_reparse": True,
        "incremental_reader_enabled": False,
        "raw_input_signature": raw_signature,
        "bid_file_size": raw_signature.get("bid", {}).get("size"),
        "ask_file_size": raw_signature.get("ask", {}).get("size"),
    }


def _default_date() -> str:
    try:
        dates = sorted(p.name for p in bfl.RAW_BASE.iterdir() if p.is_dir())
        if dates:
            return dates[-1]
    except OSError:
        pass
    return "2026-06-15"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build V3 book-flow level candle cache")
    parser.add_argument("--symbol", default="NQU6")
    parser.add_argument("--date", default=_default_date())
    parser.add_argument("--depth", default="all", help="5, 10, 15, 20, or all")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--inspect-only", action="store_true")
    args = parser.parse_args()

    support = inspect_raw_depth_support(args.symbol, args.date)
    print(json.dumps(support, indent=2, sort_keys=True))
    if args.inspect_only:
        return 0 if support.get("ok") else 2
    if not support.get("ok"):
        print("BLOCKED_TOP_DEPTH_UNAVAILABLE")
        return 2

    depths = DEPTH_CHOICES if args.depth == "all" else [int(args.depth)]
    result = build_level_caches(args.symbol, args.date, depths=depths, force=args.force)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
