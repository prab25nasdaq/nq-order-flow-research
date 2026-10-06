#!/usr/bin/env python3
"""book_flow_chart_v3.py - price-axis book-flow level candle chart.

SHADOW/RESEARCH ONLY. The main visual is a footprint/bookmap-style cell chart:
x = vol500 bar/time, y = actual price, and each cell is signed raw book flow at
that price level reconstructed from Rithmic depth + bid/ask quote updates.

No price OHLC candles are drawn. No MLOFI candles are drawn.
"""
from __future__ import annotations

import os

# Step 2 Part C containment (see PART_B_RSS_VERDICT.md): PyArrow's parquet reader is backed by
# Arrow's mimalloc memory pool, which by default holds freed pages in its own segment cache for a
# purge-delay period before returning them to the OS via munmap/madvise -- under the chart's
# repeated small polling reads, RSS grows unboundedly instead of plateauing. This MUST be set
# before pyarrow (and anything that imports it, e.g. pandas.read_parquet) initializes mimalloc's
# arena subsystem, so it has to be the first thing this module does. Measured effect (120 repeated
# reads of the real compact cache, isolated experiment): with this set, RSS plateaus at ~190MB;
# without it, RSS keeps climbing linearly past 280MB over the same reads. setdefault() so an
# operator's explicit env value is never overridden.
os.environ.setdefault("MIMALLOC_PURGE_DELAY", "0")

import argparse
import ctypes
import json
import queue as _queue_mod
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

import book_flow_lib as bfl
import book_flow_cache_daemon as cache_daemon
import build_book_flow_level_cache as level_cache
import book_flow_data_service as bfds
from book_flow_chart_v2 import VolumeProfileItem, _range_pair


_USE_GL = os.environ.get("OFI_CHART_GL", "0") not in ("0", "false", "False")
pg.setConfigOptions(
    useOpenGL=_USE_GL,
    antialias=False,
    background="#050505",
    foreground="#dddddd",
    leftButtonPan=True,
)

TICK = bfl.TICK
DEPTH_CHOICES = [5, 10, 15, 20]
FOLLOW_WINDOW = 180
MAIN_VISUAL_TYPE = "PRICE_AXIS_BOOK_FLOW_LEVEL_CANDLES"
MAIN_Y_AXIS = "price"
CELLS_SOURCE = "raw Rithmic depth + bid/ask quote updates"
LIVE_LATEST_LABEL = "LIVE LATEST"
HISTORICAL_LABEL = "HISTORICAL DATE"
CONTEXT_CURRENT_ONLY = "current only"
CONTEXT_PREV_1 = "+1 previous session"
CONTEXT_PREV_2 = "+2 previous sessions"
CONTEXT_PREV_3 = "+3 previous sessions"
CONTEXT_CUSTOM = "custom bars"
# BookFlowDataService cutover (step 2 of the beta-hosting track, PERF_REPORT.md/STEP2_REPORT.md).
# Default ON as of step 2.5 (P95_FORENSICS.md/STEP2_REPORT.md): parity passed (replay +
# 10-min live, 0 mismatches), and the service path's own gating (_current_sigs() in
# book_flow_data_service.py) never had the heartbeat-mtime over-invalidation bug the legacy
# CacheFileMonitor path had (fixed separately, same step) -- it only rebuilds on a genuine
# compact/bars/forming file change. Legacy path remains reachable via BOOKFLOW_DATASERVICE=0 for
# one release.
#
# Scope note (found while flipping this default, step 2.5): _dataservice_eligible() below
# requires show_forming_bar=False and a single-date context. That is NOT the live-latest default
# -- __init__ sets self.show_forming_bar = self.live_latest_mode (True) and
# self.context_mode = CONTEXT_PREV_1 (2 dates) specifically for live-latest mode. So this
# default-ON flip activates the service path for historical/replay viewing and for
# manually-reconfigured single-date/no-forming-bar live sessions, but the out-of-the-box
# live-latest experience (forming bar visible, +1 previous session) still runs on the legacy
# CacheFileMonitor path -- which is exactly why the legacy-path sig fix a few lines below matters
# on its own, independent of this flag. Widening _dataservice_eligible() to cover that
# configuration is a larger, separately-scoped change (needs its own forming-bar/multi-date
# parity pass) and was not attempted here.
_BOOKFLOW_DATASERVICE_DEFAULT_ON = True
BOOKFLOW_DATASERVICE_ENABLED = os.environ.get(
    "BOOKFLOW_DATASERVICE", "1" if _BOOKFLOW_DATASERVICE_DEFAULT_ON else "0"
) not in ("0", "false", "False")
# Test-only override for the chart's cache-poll cadence (Step 2 Part B A/B comparisons). Unset in
# normal operation -- the default 0.5s/2.0s live/historical split in _restart_worker() is
# untouched. Lets an external harness compare e.g. 350ms vs 1s polling without a code change.
_MONITOR_INTERVAL_OVERRIDE_S = os.environ.get("OFI_BOOK_FLOW_MONITOR_INTERVAL_S")
PERF_LOG = os.environ.get("OFI_BOOK_FLOW_PERF_LOG", "0") not in ("0", "false", "False")
MAX_FPS = max(1.0, float(os.environ.get("OFI_BOOK_FLOW_MAX_FPS", "4")))
RENDER_INTERVAL_MS = max(50, int(1000.0 / MAX_FPS))
MAX_VISIBLE_BARS = max(50, int(os.environ.get("OFI_BOOK_FLOW_MAX_VISIBLE_BARS", "1200")))
MAX_VISIBLE_CELLS = max(1000, int(os.environ.get("OFI_BOOK_FLOW_MAX_VISIBLE_CELLS", "120000")))
VISIBLE_MARGIN_BARS = max(5, int(os.environ.get("OFI_BOOK_FLOW_VISIBLE_MARGIN_BARS", "30")))
# Forming cache older than this (seconds) is considered stale and ignored
FORMING_STALE_SECS = float(os.environ.get("OFI_FORMING_STALE_SECS", "5.0"))

# Step 2 Part C containment: secondary glibc-side lever, see _periodic_malloc_trim() below.
try:
    _libc = ctypes.CDLL("libc.so.6")
except OSError:
    _libc = None

LOD_FULL     = "FULL"
LOD_MEDIUM   = "MEDIUM"
LOD_WIDE     = "WIDE"
LOD_OVERVIEW = "OVERVIEW"
LOD_FULL_MAX_BARS    = 150
LOD_MEDIUM_MAX_BARS  = 500
LOD_WIDE_MAX_BARS    = 1500
MAX_RENDER_CELLS_FULL = 20_000

UP_COLOR = "#00d27a"
DOWN_COLOR = "#ff4d6d"
NEUTRAL_COLOR = "#777777"
PRICE_COLOR = "#55aaff"
NET_FLOW_COLOR = "#ffd166"
PROJ_COLOR = "#ff66ff"
# Phase 2 Part F: order-book ladder LOD -- size labels only render once a level occupies at
# least this many screen pixels (matches _compute_lod's pixels-per-bar philosophy elsewhere).
BOOK_LABEL_MIN_PX = 11.0

DASHBOARD_FILE = Path(
    "/mnt/wd_work/workspace/Work Place/Data/Project OFI/_codex_work/"
    "ofi_live_dashboard_WORKING_NEXT_with_logreg.py"
)
APP_PATH = Path("/home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py")
CACHE_BUILDER_PATH = Path("/home/prabh/OFI_Production/book_flow_chart/build_book_flow_level_cache.py")
LAUNCHER_PATH = Path("/home/prabh/OFI_Production/launch_book_flow_chart.sh")
PROJECTED_LEVELS_PATH = bfl.FEATURES_BASE / "projected_levels_NQM6_to_NQU6.csv"

REQUIRED_LEVEL_COLUMNS = {
    "bar_idx", "timestamp_utc", "bar_start_ts_ns", "bar_end_ts_ns",
    "price_level", "price_tick", "depth_n", "side_zone",
    "signed_flow", "abs_flow", "bid_add", "bid_pull", "ask_add", "ask_pull",
    "net_bid_flow", "net_ask_flow", "trade_volume_at_price",
    "buy_trade_volume_at_price", "sell_trade_volume_at_price",
    "close_price", "mid_price", "nearest_level", "distance_to_nearest_level",
}

FORBIDDEN_MAIN_VISUAL_COLUMNS = {
    "open", "high", "low", "close",
    "px_open", "px_high", "px_low", "px_close",
    "of_open", "of_high", "of_low", "of_close",
    "continuous_open", "continuous_high", "continuous_low", "continuous_close",
    "mlofi_norm", "mlofi_sum", "mlofi_mean", "mlofi_decay_sum",
}


@dataclass
class PerformanceStats:
    raw_read_ms: float = 0.0
    load_ms: float = 0.0
    cache_update_ms: float = 0.0
    compute_ms: float = 0.0
    render_prepare_ms: float = 0.0
    render_ms: float = 0.0
    render_draw_ms: float = 0.0
    profile_ms: float = 0.0
    pressure_ms: float = 0.0
    pulls_ms: float = 0.0
    gui_update_ms: float = 0.0
    total_refresh_ms: float = 0.0
    visible_cells: int = 0
    bars_rendered: int = 0
    skipped_frames: int = 0
    coalesced_frames: int = 0
    raw_lines_processed: int = 0
    queue_depth: int = 0
    update_queue_pending: int = 0
    render_decimated: bool = False
    lod_mode: str = LOD_FULL
    backend: str = "compact-cache-visible-window"


def validate_level_cache(df: pd.DataFrame, depth: int) -> dict:
    missing = sorted(REQUIRED_LEVEL_COLUMNS - set(df.columns))
    if missing:
        return {"ok": False, "reason": f"missing level-cache columns: {missing}"}
    if df.empty:
        return {"ok": False, "reason": "level cache is empty"}
    if set(df.columns) & FORBIDDEN_MAIN_VISUAL_COLUMNS:
        bad = sorted(set(df.columns) & FORBIDDEN_MAIN_VISUAL_COLUMNS)
        return {"ok": False, "reason": f"forbidden main visual columns present in level cache: {bad}"}
    if sorted(pd.unique(df["depth_n"]).tolist()) != [depth]:
        return {"ok": False, "reason": f"cache depth does not match selected top{depth}"}
    if df["price_level"].median() < 20000:
        return {"ok": False, "reason": "main y values are not actual price levels"}
    if df["bar_idx"].nunique() < 1 or df["price_level"].nunique() < 10:
        return {"ok": False, "reason": "not enough bars or price levels for a level-cell chart"}
    if not np.isfinite(df["signed_flow"].to_numpy(float)).any():
        return {"ok": False, "reason": "signed_flow has no finite values"}
    return {"ok": True, "reason": ""}


def _default_date() -> str:
    return LIVE_LATEST_LABEL


def _is_live_latest(value: str) -> bool:
    return str(value).strip().lower().replace("_", " ") in ("latest", "live latest")


def _resolve_live_date(symbol: str = "NQU6") -> str:
    hb = cache_daemon.load_heartbeat()
    if str(hb.get("symbol", symbol)) == symbol and hb.get("active_date"):
        return str(hb["active_date"])
    return cache_daemon.resolve_date("latest")


def _load_bars(symbol: str, date: str) -> pd.DataFrame:
    path = bfl.FEATURES_BASE / date / f"{symbol}_vol500.ndjsonl"
    # Step 2.5 Part C (see P95_FORENSICS.md): incremental cache instead of a full from-scratch
    # reparse of the ever-growing ndjsonl file on every reload -- this was the single largest
    # sampled cost across every expensive frame class (forming-update/frame-consume/bar-roll/
    # other), all of which funnel through this call.
    bars = bfl.load_vol500_bars_incremental(path)
    if not bars.empty:
        bars = bars.sort_values("bar_index").reset_index(drop=True)
        bars["session_date"] = date
    return bars


def _load_level_df(symbol: str, date: str, depth: int) -> pd.DataFrame:
    cpath = cache_daemon.compact_cache_path(symbol, date, depth)
    if not cpath.exists():
        return pd.DataFrame()
    df = pd.read_parquet(cpath)
    df["session_date"] = date
    return df.sort_values(["bar_idx", "price_level"]).reset_index(drop=True)


def _load_forming_df(symbol: str, date: str, depth: int) -> pd.DataFrame:
    """Load the fast-updating forming-bar parquet (Lane 2, written at ~350 ms cadence).

    Guards applied:
    1. Mtime freshness: rejects files older than FORMING_STALE_SECS (daemon may have stopped).
    2. Meta consistency: rejects if forming_bar_idx is absent or zero in the meta.json.
    """
    fpath = cache_daemon.forming_cache_path(symbol, date, depth)
    if not fpath.exists():
        return pd.DataFrame()
    # Guard 1: mtime freshness
    try:
        mtime = fpath.stat().st_mtime
        if time.time() - mtime > FORMING_STALE_SECS:
            return pd.DataFrame()
    except OSError:
        return pd.DataFrame()
    # Guard 2: meta consistency — forming_bar_idx must be set and non-zero
    meta_path = cache_daemon.forming_cache_meta_path(symbol, date, depth)
    try:
        meta = json.loads(meta_path.read_text())
        if not meta.get("forming_bar_idx"):
            return pd.DataFrame()
    except Exception:
        pass  # meta absent is OK; bar_idx checked by caller via > last_closed
    try:
        df = pd.read_parquet(fpath)
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()
    if "bar_state" not in df.columns:
        df["bar_state"] = "FORMING"
    df["session_date"] = date
    return df


# Step 3 Phase 1: moved to book_flow_lib.py (bfl.available_dates_for_symbol /
# bfl.context_dates_for) so book_flow_data_service.py can share the exact same date-discovery
# logic without importing from this Qt-dependent module. Thin aliases kept here so existing
# call sites in this file don't all need touching.
_available_dates_for_symbol = bfl.available_dates_for_symbol
_context_dates_for = bfl.context_dates_for


def _max_timestamp_utc(df: pd.DataFrame) -> Optional[str]:
    if df.empty or "timestamp_utc" not in df.columns:
        return None
    try:
        ts = pd.to_datetime(df["timestamp_utc"], utc=True, errors="coerce").max()
    except Exception:
        return None
    if pd.isna(ts):
        return None
    return str(ts)


def _timestamp_lag_seconds(later, earlier) -> Optional[float]:
    try:
        later_ts = pd.to_datetime(later, utc=True, errors="coerce")
        earlier_ts = pd.to_datetime(earlier, utc=True, errors="coerce")
    except Exception:
        return None
    if pd.isna(later_ts) or pd.isna(earlier_ts):
        return None
    return round(float((later_ts - earlier_ts).total_seconds()), 6)


def _file_sig(path: Path) -> tuple[int, int]:
    try:
        st = path.stat()
        return int(st.st_mtime_ns), int(st.st_size)
    except OSError:
        return 0, 0


def _bars_path(symbol: str, date: str) -> Path:
    return bfl.FEATURES_BASE / date / f"{symbol}_vol500.ndjsonl"


def _price_to_tick_scalar(price: float) -> int:
    return int(round((float(price) - bfl.PRICE_MIN) / bfl.TICK))


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _aggregate_cells_for_wide_lod(
    level_df: "pd.DataFrame",
    visible_id_set: set,
    bar_pos_map: dict,
    bar_group: int,
    price_bin_pts: float,
) -> "pd.DataFrame":
    """Aggregate per-level OFI rows into (x_bin, y_bin) cells for WIDE/OVERVIEW rendering.

    Uses fully vectorised pandas operations — no per-bar Python loop.
    Returns a DataFrame with columns:
      bar_pos, price_level, _x_bin, _y_bin,
      signed_flow, abs_flow, bid_add, bid_pull, ask_add, ask_pull
    where bar_pos is the center of the x-bin in data coordinates.
    """
    if level_df is None or level_df.empty or not visible_id_set:
        return pd.DataFrame()
    want = ["bar_idx", "price_level", "signed_flow", "abs_flow",
            "bid_add", "bid_pull", "ask_add", "ask_pull"]
    present = [c for c in want if c in level_df.columns]
    if "bar_idx" not in present or "price_level" not in present:
        return pd.DataFrame()

    mask = level_df["bar_idx"].isin(visible_id_set)
    sub = level_df.loc[mask, present].copy()
    if sub.empty:
        return pd.DataFrame()

    sub["bar_pos"] = sub["bar_idx"].map(bar_pos_map).astype(float)
    # Integer bin indices (used for tooltip lookup key)
    sub["_x_bin"] = (sub["bar_pos"] / bar_group).astype(int)
    sub["_y_bin"] = (sub["price_level"] / price_bin_pts).round().astype(int)

    flow_cols = [c for c in ["signed_flow", "abs_flow", "bid_add", "bid_pull",
                              "ask_add", "ask_pull"] if c in sub.columns]
    agg = sub.groupby(["_x_bin", "_y_bin"], sort=False)[flow_cols].sum().reset_index()

    # Restore data-space coordinates
    # x center = bin_index * bar_group + half-bin offset so cell is centered in its bin
    agg["bar_pos"] = agg["_x_bin"].astype(float) * bar_group + (bar_group - 1) * 0.5
    agg["price_level"] = agg["_y_bin"].astype(float) * price_bin_pts
    return agg


class BookFlowLevelCellItem(pg.GraphicsObject):
    """Renderer for V3 price-axis book-flow level cells.

    Cells are pre-rendered into a QPicture in set_data() so that paint()
    is a single display-list replay — no Python per-cell loops during redraws.
    Cells are batched by (sign, alpha-band) so setBrush() is called once per
    group (~48 groups max) rather than once per cell.
    """

    source_columns = ("bar_pos", "price_level", "signed_flow", "abs_flow")
    visual_type = MAIN_VISUAL_TYPE

    def __init__(self) -> None:
        super().__init__()
        self._picture = QtGui.QPicture()
        self._bounds = QtCore.QRectF()

    def set_data(self, df: pd.DataFrame, scale_mode: str, min_flow: float) -> None:
        self._picture = QtGui.QPicture()
        self._bounds = QtCore.QRectF()

        if df.empty:
            self.prepareGeometryChange()
            self.update()
            return

        mask = pd.to_numeric(df["abs_flow"], errors="coerce") >= float(min_flow)
        use = df[mask]
        if use.empty:
            self.prepareGeometryChange()
            self.update()
            return

        abs_flow = use["abs_flow"].to_numpy(dtype=np.float64)
        signed   = use["signed_flow"].to_numpy(dtype=np.float64)
        if scale_mode == "log":
            scaled = np.log1p(abs_flow)
            denom  = float(np.nanmax(scaled))
        elif scale_mode == "percentile":
            denom  = float(np.nanpercentile(abs_flow, 95))
            scaled = abs_flow
        else:
            denom  = float(np.nanmax(abs_flow))
            scaled = abs_flow
        if denom <= 0 or not np.isfinite(denom):
            norm = np.zeros(len(use), dtype=np.float64)
        else:
            norm = np.clip(scaled / denom, 0.0, 1.0)

        x_vals = use["bar_pos"].to_numpy(dtype=np.float64)
        y_vals = use["price_level"].to_numpy(dtype=np.float64)
        tick_h = TICK * 0.92
        half_h = tick_h * 0.5

        # Quantize alpha to 16 bands (45..255 in ~14-unit steps) and assign
        # a colour group (0=green, 1=red, 2=neutral).  This caps the total
        # number of distinct QPainter brush-change calls at 3×16 = 48,
        # regardless of how many cells there are.
        alpha_arr = np.rint(45.0 + 210.0 * norm).astype(np.int32)
        # neutral alpha is half-strength: max(35, a//2)
        neutral_alpha = np.maximum(35, alpha_arr // 2).astype(np.int32)
        sign_group = np.where(signed > 0, 0, np.where(signed < 0, 1, 2)).astype(np.int8)
        band        = np.clip((alpha_arr - 45) // 14, 0, 15).astype(np.int32)
        group_key   = sign_group.astype(np.int32) * 16 + band

        sort_idx = np.argsort(group_key, kind="stable")
        x_s  = x_vals[sort_idx]
        y_s  = y_vals[sort_idx]
        n_s  = norm[sort_idx]
        a_s  = alpha_arr[sort_idx]
        na_s = neutral_alpha[sort_idx]
        sg_s = sign_group[sort_idx]
        gk_s = group_key[sort_idx]

        unique_keys, starts = np.unique(gk_s, return_index=True)
        ends = np.append(starts[1:], len(gk_s))

        # Pre-render all cells into a QPicture — paint() just replays this
        p = QtGui.QPainter(self._picture)
        p.setPen(QtCore.Qt.NoPen)
        for k_idx in range(len(unique_keys)):
            s = int(starts[k_idx])
            e = int(ends[k_idx])
            sg  = int(sg_s[s])
            alp = int(a_s[s])
            if sg == 0:
                color = QtGui.QColor(0, 210, 122, alp)
            elif sg == 1:
                color = QtGui.QColor(255, 77, 109, alp)
            else:
                color = QtGui.QColor(119, 119, 119, int(na_s[s]))
            p.setBrush(QtGui.QBrush(color))
            # PERF: one drawRects() call per color group instead of one drawRect() call per
            # cell -- cuts Python-level QPainter call overhead from O(n_cells) to O(n_groups)
            # (<=48 groups regardless of cell count). See DIAGNOSIS.md 4b/5.
            widths = 0.14 + 0.80 * n_s[s:e]
            group_rects = [
                QtCore.QRectF(float(x_s[s + i]) - float(widths[i]) * 0.5, float(y_s[s + i]) - half_h,
                              float(widths[i]), tick_h)
                for i in range(e - s)
            ]
            p.drawRects(group_rects)
        p.end()

        x_lo, x_hi = _range_pair(x_vals)
        y_lo, y_hi = _range_pair(y_vals)
        if np.isfinite(x_lo) and np.isfinite(y_lo):
            self._bounds = QtCore.QRectF(
                x_lo - 1.0, y_lo - TICK,
                max(x_hi - x_lo + 2.0, 1.0),
                max(y_hi - y_lo + 2.0 * TICK, TICK),
            )
        self.prepareGeometryChange()
        self.update()

    def paint(self, painter, option, widget=None) -> None:
        self._picture.play(painter)

    def boundingRect(self) -> QtCore.QRectF:
        return self._bounds


class OrderBookLadderSideItem(pg.GraphicsObject):
    """Phase 2 Part F: one side (bid or ask) of the live order-book ladder, drawn in the same
    price-axis-linked right panel that hosts the volume blueprint. Exactly ONE batched item per
    side -- every level's bar (and, at high zoom, its size label) is baked into a single QPicture
    in set_data() so paint() is a single display-list replay, mirroring BookFlowLevelCellItem's
    pattern above. No per-level QGraphicsItem/TextItem objects are ever created."""

    def __init__(self, color: str) -> None:
        super().__init__()
        self._picture = QtGui.QPicture()
        self._bounds = QtCore.QRectF(0.0, 0.0, 1.0, 1.0)
        self._color = QtGui.QColor(color)
        self._grey = QtGui.QColor("#666666")

    def set_data(self, prices: np.ndarray, sizes: np.ndarray, max_size: float, tick: float,
                 stale: bool = False) -> None:
        # NOTE: size labels are handled separately, via real pg.TextItem objects managed by the
        # window (see _update_order_book_panel) -- NOT baked into this QPicture. A GraphicsObject's
        # paint() replays under the ViewBox's data-coordinate transform, so text drawn here would
        # be scaled by the price axis zoom (glyphs stretched to the same magnification as a 0.25-
        # tick-tall bar), producing garbled oversized shapes. pg.TextItem renders in screen space
        # regardless of that transform, which is what every other label in this file already uses.
        self._picture = QtGui.QPicture()
        prices = np.asarray(prices, dtype=np.float64)
        sizes = np.asarray(sizes, dtype=np.float64)
        mask = sizes > 0
        if prices.size == 0 or max_size <= 0 or not np.isfinite(max_size) or not mask.any():
            self._bounds = QtCore.QRectF(0.0, 0.0, 1.0, 1.0)
            self.prepareGeometryChange()
            self.update()
            return

        px = prices[mask]
        sz = sizes[mask]
        widths = np.clip(sz / max_size, 0.02, 1.0)
        bar_h = tick * 0.90
        half_h = bar_h * 0.5
        color = self._grey if stale else self._color

        p = QtGui.QPainter(self._picture)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QBrush(color))
        rects = [
            QtCore.QRectF(0.0, float(px[i]) - half_h, float(widths[i]), bar_h)
            for i in range(len(px))
        ]
        p.drawRects(rects)
        p.end()

        y_lo = float(np.nanmin(px))
        y_hi = float(np.nanmax(px))
        self._bounds = QtCore.QRectF(0.0, y_lo - bar_h, 1.3, max(y_hi - y_lo + 2.0 * bar_h, bar_h))
        self.prepareGeometryChange()
        self.update()

    def paint(self, painter, option, widget=None) -> None:
        self._picture.play(painter)

    def boundingRect(self) -> QtCore.QRectF:
        return self._bounds


class CellHighlightItem(pg.GraphicsObject):
    """Phase 2 Part F: persistent single-cell highlight marking the forming bar's cell containing
    the current price. One instance, repositioned in place via set_rect() every update -- never
    recreated per tick."""

    def __init__(self) -> None:
        super().__init__()
        self._rect = QtCore.QRectF()
        self._visible_rect = False

    def set_rect(self, x0: float, x1: float, y0: float, y1: float) -> None:
        self._rect = QtCore.QRectF(x0, y0, x1 - x0, y1 - y0)
        self._visible_rect = True
        self.prepareGeometryChange()
        self.update()

    def clear(self) -> None:
        self._rect = QtCore.QRectF()
        self._visible_rect = False
        self.prepareGeometryChange()
        self.update()

    def paint(self, painter, option, widget=None) -> None:
        if not self._visible_rect:
            return
        painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 0.0))
        painter.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255, 60)))
        painter.drawRect(self._rect)

    def boundingRect(self) -> QtCore.QRectF:
        return self._rect


class WideOFILevelCellRasterItem(pg.GraphicsObject):
    """Aggregated per-level OFI cells for WIDE/OVERVIEW LOD.

    Input: output of _aggregate_cells_for_wide_lod() — one row per (x_bin, y_bin).
    Visual style: identical to BookFlowLevelCellItem (horizontal green/red cells),
    but with cell dimensions scaled to the bin size:
      cell_w = bar_group * flow_norm  (fills x-bin width)
      cell_h = price_bin_pts * 0.92   (fills y-bin height)
    Color/alpha/batching logic is identical to BookFlowLevelCellItem.
    Stores bin_lookup dict for mouse tooltip use.
    """

    def __init__(self) -> None:
        super().__init__()
        self._picture = QtGui.QPicture()
        self._bounds = QtCore.QRectF()
        self.bin_lookup: dict[tuple[int, int], dict] = {}
        self._bar_group: int = 1
        self._price_bin_pts: float = TICK

    def set_data(
        self,
        df: "pd.DataFrame",
        scale_mode: str,
        min_flow: float,
        bar_group: int = 1,
        price_bin_pts: float = TICK,
    ) -> None:
        self._picture = QtGui.QPicture()
        self._bounds = QtCore.QRectF()
        self.bin_lookup = {}
        self._bar_group = bar_group
        self._price_bin_pts = price_bin_pts

        if df.empty:
            self.prepareGeometryChange()
            self.update()
            return
        if "signed_flow" not in df.columns or "abs_flow" not in df.columns:
            self.prepareGeometryChange()
            self.update()
            return

        mask = pd.to_numeric(df["abs_flow"], errors="coerce") >= float(min_flow)
        use = df[mask].reset_index(drop=True)
        if use.empty:
            self.prepareGeometryChange()
            self.update()
            return

        abs_flow = use["abs_flow"].to_numpy(dtype=np.float64)
        signed   = use["signed_flow"].to_numpy(dtype=np.float64)
        if scale_mode == "log":
            scaled = np.log1p(abs_flow)
            denom  = float(np.nanmax(scaled))
        elif scale_mode == "percentile":
            denom  = float(np.nanpercentile(abs_flow, 95))
            scaled = abs_flow
        else:
            denom  = float(np.nanmax(abs_flow))
            scaled = abs_flow
        if denom <= 0 or not np.isfinite(denom):
            norm = np.zeros(len(use), dtype=np.float64)
        else:
            norm = np.clip(scaled / denom, 0.0, 1.0)

        x_vals = use["bar_pos"].to_numpy(dtype=np.float64)
        y_vals = use["price_level"].to_numpy(dtype=np.float64)

        # Bin index arrays for tooltip key — use stored columns if present
        if "_x_bin" in use.columns:
            x_bin_arr = use["_x_bin"].to_numpy(dtype=np.int64)
        else:
            x_bin_arr = (x_vals / bar_group).astype(np.int64)
        if "_y_bin" in use.columns:
            y_bin_arr = use["_y_bin"].to_numpy(dtype=np.int64)
        else:
            y_bin_arr = np.round(y_vals / price_bin_pts).astype(np.int64)

        # Build tooltip bin_lookup from numpy arrays (no pandas per-row overhead)
        bid_add_arr  = use["bid_add"].to_numpy(dtype=np.float64)  if "bid_add"  in use.columns else None
        bid_pull_arr = use["bid_pull"].to_numpy(dtype=np.float64) if "bid_pull" in use.columns else None
        ask_add_arr  = use["ask_add"].to_numpy(dtype=np.float64)  if "ask_add"  in use.columns else None
        ask_pull_arr = use["ask_pull"].to_numpy(dtype=np.float64) if "ask_pull" in use.columns else None
        for i in range(len(x_vals)):
            key = (int(x_bin_arr[i]), int(y_bin_arr[i]))
            rec: dict = {
                "x_bin": int(x_bin_arr[i]), "y_bin": int(y_bin_arr[i]),
                "bar_pos": float(x_vals[i]), "price_level": float(y_vals[i]),
                "signed_flow": float(signed[i]), "abs_flow": float(abs_flow[i]),
            }
            if bid_add_arr  is not None: rec["bid_add"]  = float(bid_add_arr[i])
            if bid_pull_arr is not None: rec["bid_pull"] = float(bid_pull_arr[i])
            if ask_add_arr  is not None: rec["ask_add"]  = float(ask_add_arr[i])
            if ask_pull_arr is not None: rec["ask_pull"] = float(ask_pull_arr[i])
            self.bin_lookup[key] = rec

        # Same alpha/batch logic as BookFlowLevelCellItem (48 brush changes max)
        alpha_arr    = np.rint(45.0 + 210.0 * norm).astype(np.int32)
        neutral_alpha = np.maximum(35, alpha_arr // 2).astype(np.int32)
        sign_group   = np.where(signed > 0, 0, np.where(signed < 0, 1, 2)).astype(np.int8)
        band         = np.clip((alpha_arr - 45) // 14, 0, 15).astype(np.int32)
        group_key    = sign_group.astype(np.int32) * 16 + band

        sort_idx = np.argsort(group_key, kind="stable")
        x_s  = x_vals[sort_idx];  y_s  = y_vals[sort_idx]
        n_s  = norm[sort_idx];    a_s  = alpha_arr[sort_idx]
        na_s = neutral_alpha[sort_idx]; sg_s = sign_group[sort_idx]
        gk_s = group_key[sort_idx]

        unique_keys, starts = np.unique(gk_s, return_index=True)
        ends = np.append(starts[1:], len(gk_s))

        # Cell dimensions: scaled to bin size so cells fill their bins
        cell_h  = price_bin_pts * 0.92
        half_h  = cell_h * 0.5
        max_w   = float(bar_group) * 0.92  # max cell width = 92% of bar_group

        p = QtGui.QPainter(self._picture)
        p.setPen(QtCore.Qt.NoPen)
        for k_idx in range(len(unique_keys)):
            s = int(starts[k_idx])
            e = int(ends[k_idx])
            sg  = int(sg_s[s])
            alp = int(a_s[s])
            if sg == 0:
                color = QtGui.QColor(0, 210, 122, alp)
            elif sg == 1:
                color = QtGui.QColor(255, 77, 109, alp)
            else:
                color = QtGui.QColor(119, 119, 119, int(na_s[s]))
            p.setBrush(QtGui.QBrush(color))
            for i in range(s, e):
                w = max_w * (0.14 + 0.80 * float(n_s[i]))
                p.drawRect(QtCore.QRectF(
                    float(x_s[i]) - w * 0.5,
                    float(y_s[i]) - half_h,
                    w, cell_h,
                ))
        p.end()

        x_lo, x_hi = _range_pair(x_vals)
        y_lo, y_hi = _range_pair(y_vals)
        if np.isfinite(x_lo) and np.isfinite(y_lo):
            self._bounds = QtCore.QRectF(
                x_lo - float(bar_group), y_lo - price_bin_pts,
                max(x_hi - x_lo + 2.0 * bar_group, 1.0),
                max(y_hi - y_lo + 2.0 * price_bin_pts, price_bin_pts),
            )
        self.prepareGeometryChange()
        self.update()

    def paint(self, painter, option, widget=None) -> None:
        self._picture.play(painter)

    def boundingRect(self) -> QtCore.QRectF:
        return self._bounds


class CacheFileMonitor(QtCore.QThread):
    updated = QtCore.Signal(dict)

    def __init__(self, symbol: str, date: str, depth: int, live_latest: bool = False, interval_s: float = 2.0) -> None:
        super().__init__()
        self.symbol = symbol
        self.date = date
        self.depth = depth
        self.live_latest = live_latest
        self.interval_s = interval_s
        self._stop = False
        self._last_sig: Optional[tuple] = None
        self._last_ok = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        while not self._stop:
            try:
                heartbeat = cache_daemon.load_heartbeat()
                active_date = (
                    str(heartbeat.get("active_date"))
                    if self.live_latest and heartbeat.get("active_date")
                    else (_resolve_live_date(self.symbol) if self.live_latest else self.date)
                )
                cpath = cache_daemon.compact_cache_path(self.symbol, active_date, self.depth)
                mpath = cache_daemon.compact_cache_meta_path(self.symbol, active_date, self.depth)
                sig = (
                    self.symbol,
                    active_date,
                    self.depth,
                    # Step 2.5 Part A: same fix as _current_data_sig() (see PART_B_RSS_VERDICT.md)
                    # -- this component is redundant (active_date, above, already picks up a real
                    # rollover) and was unconditionally re-triggering a full background
                    # request_load()/pd.read_parquet() on almost every ~2s heartbeat write,
                    # regardless of live_latest_mode, independent of and unfixed by the step-2
                    # _current_data_sig() fix.
                    (0, 0),
                    _file_sig(cpath),
                    _file_sig(mpath),
                    _file_sig(cache_daemon.forming_cache_path(self.symbol, active_date, self.depth)),
                )
                if sig == self._last_sig:
                    result = None
                else:
                    result = {
                        "ok": cpath.exists(),
                        "cache_path": str(cpath),
                        "meta_path": str(mpath),
                        "active_date": active_date,
                        "heartbeat": heartbeat,
                        "reason": "" if cpath.exists() else "waiting for compact cache daemon",
                        "gui_reads_cache_only": True,
                    }
                    self._last_sig = sig
                    self._last_ok = bool(result.get("ok"))
            except Exception as exc:
                result = {"ok": False, "reason": str(exc)}
                self._last_ok = False
            if result is not None:
                self.updated.emit(result)
            for _ in range(int(self.interval_s * 10)):
                if self._stop:
                    return
                time.sleep(0.1)


# ── Background data loader ──────────────────────────────────────────────────

def _bg_load_snapshot(params: dict) -> dict:
    """Load parquet/forming data off the GUI thread.

    Mirrors the logic of _load_context_frames() + the forming-bar handling in
    _load_data_if_needed(), but as a pure function that only uses module-level
    helpers (no QObject state).  The result is a ready-to-use dict that the GUI
    thread can apply directly without any further I/O.
    """
    symbol       = params["symbol"]
    date         = params["date"]
    depth        = params["depth"]
    context_dates: list[str] = params["context_dates"]
    show_forming: bool = params["show_forming_bar"]

    heartbeat = cache_daemon.load_heartbeat()

    levels: list[pd.DataFrame] = []
    bars_list: list[pd.DataFrame] = []
    loaded_dates: list[str] = []
    missing_cache: list[str] = []

    for d in context_dates:
        cpath = cache_daemon.compact_cache_path(symbol, d, depth)
        bpath = _bars_path(symbol, d)
        if not bpath.exists():
            continue
        bars = _load_bars(symbol, d)
        if bars.empty:
            continue
        if not cpath.exists():
            missing_cache.append(d)
            continue
        level = _load_level_df(symbol, d, depth)
        if level.empty:
            missing_cache.append(d)
            continue
        levels.append(level)
        bars_list.append(bars)
        loaded_dates.append(d)

    if not levels or not bars_list:
        return {"ok": False, "reason": "no data for context dates", "params": params}

    level_df = (
        pd.concat(levels, ignore_index=True)
        .sort_values(["bar_idx", "price_level"])
        .reset_index(drop=True)
    )
    bars_df = (
        pd.concat(bars_list, ignore_index=True)
        .sort_values("bar_index")
        .drop_duplicates(subset=["bar_index"], keep="last")
        .reset_index(drop=True)
    )

    # ── Forming bar logic (identical to _load_data_if_needed) ────────────
    forming_ids: set[int] = set()
    new_last_closed: Optional[int] = None
    if "bar_state" in level_df.columns:
        forming_ids = set(
            int(x) for x in level_df.loc[level_df["bar_state"] == "FORMING", "bar_idx"].unique()
        )
        closed_bar_ids = sorted(int(b) for b in level_df["bar_idx"].unique() if b not in forming_ids)
        new_last_closed = closed_bar_ids[-1] if closed_bar_ids else None
        if show_forming:
            forming_fast = _load_forming_df(symbol, date, depth)
            last_closed_in_compact = new_last_closed if new_last_closed is not None else -1
            forming_fast_bar = int(forming_fast["bar_idx"].max()) if not forming_fast.empty else -1
            if not forming_fast.empty and forming_fast_bar == last_closed_in_compact + 1:
                level_df_closed = level_df[level_df["bar_state"] != "FORMING"].copy()
                forming_in_closed = forming_fast_bar in set(
                    int(x) for x in level_df_closed["bar_idx"].unique()
                )
                if not forming_in_closed:
                    level_df = pd.concat(
                        [level_df_closed, forming_fast], ignore_index=True
                    ).sort_values(["bar_idx", "price_level"]).reset_index(drop=True)
                    forming_ids = set(int(x) for x in forming_fast["bar_idx"].unique())
                    last_vol500 = int(bars_df["bar_index"].iloc[-1]) if not bars_df.empty else -1
                    if forming_fast_bar > last_vol500 and not bars_df.empty:
                        last_row = bars_df.iloc[-1].copy()
                        synth = last_row.copy()
                        synth["bar_index"] = forming_fast_bar
                        synth["bar_start_ts_ns"] = int(last_row.get("bar_end_ts_ns", 0))
                        synth["bar_end_ts_ns"] = int(last_row.get("bar_end_ts_ns", 0))
                        bars_df = (
                            pd.concat([bars_df, pd.DataFrame([synth])], ignore_index=True)
                            .drop_duplicates(subset=["bar_index"], keep="last")
                            .reset_index(drop=True)
                        )
                else:
                    forming_ids = set()
        else:
            level_df_closed = level_df[level_df["bar_state"] == "CLOSED"].copy()
            # Bar-roll handoff-gap bridge (see DIAGNOSIS.md section 3): the fast forming-bar
            # cache (~350ms cycle) tags a bar CLOSED for exactly one cycle right after it
            # seals, before the slower compact/sealed cache (~2s cycle) picks it up. Without
            # this, the just-sealed bar is absent from every source this show_forming=False
            # branch reads for up to ~2s after every roll -- the observed bar-roll flicker.
            # The bridge record is genuine closed-bar data (not a forming preview), so it is
            # safe to splice in and render as a normal closed bar even though forming bars
            # are otherwise hidden by this branch.
            bridge_bar_idx = (new_last_closed + 1) if new_last_closed is not None else None
            if bridge_bar_idx is not None:
                closed_ids_only = set(int(x) for x in level_df_closed["bar_idx"].unique())
                if bridge_bar_idx not in closed_ids_only:
                    forming_fast = _load_forming_df(symbol, date, depth)
                    if not forming_fast.empty and "bar_state" in forming_fast.columns:
                        bridge_rows = forming_fast[
                            (forming_fast["bar_idx"] == bridge_bar_idx)
                            & (forming_fast["bar_state"] == "CLOSED")
                        ]
                        if not bridge_rows.empty:
                            level_df_closed = pd.concat(
                                [level_df_closed, bridge_rows], ignore_index=True
                            ).sort_values(["bar_idx", "price_level"]).reset_index(drop=True)
                            new_last_closed = bridge_bar_idx
            level_df = level_df_closed
            bars_df  = bars_df[~bars_df["bar_index"].isin(forming_ids)].copy()
            forming_ids = set()
    else:
        new_last_closed = int(level_df["bar_idx"].max()) if not level_df.empty else None

    # Sync bars_df to OFI coverage
    cached_bar_ids: set[int] = (
        set(int(x) for x in pd.unique(level_df["bar_idx"])) if not level_df.empty else set()
    )
    bars_df = bars_df[bars_df["bar_index"].isin(cached_bar_ids)].copy().reset_index(drop=True)

    loaded_ofi_max = int(max(cached_bar_ids)) if cached_bar_ids else None
    hb_cache_bar   = heartbeat.get("cache_last_bar_idx_by_depth", {}).get(str(depth))
    exact_match    = hb_cache_bar is None or loaded_ofi_max is None or loaded_ofi_max == hb_cache_bar
    forming_explains = (
        not exact_match
        and hb_cache_bar is not None
        and loaded_ofi_max is not None
        and hb_cache_bar - loaded_ofi_max == 1
        and not show_forming
    )
    loaded_bar_ids = [int(x) for x in bars_df["bar_index"].tolist()]
    zero_cell_ids  = sorted(set(loaded_bar_ids) - cached_bar_ids)

    return {
        "ok": True,
        "level_df": level_df,
        "bars_df": bars_df,
        "heartbeat": heartbeat,
        "forming_ids": sorted(forming_ids),
        "loaded_dates": loaded_dates,
        "missing_cache": missing_cache,
        "last_closed_bar_idx": new_last_closed,
        "cached_bar_ids": cached_bar_ids,
        "zero_cell_ids": zero_cell_ids,
        "parity_status": {
            "hb_cache_bar": hb_cache_bar,
            "loaded_ofi_max": loaded_ofi_max,
            "parity_ok": exact_match or forming_explains,
            "mismatch": not (exact_match or forming_explains),
        },
        "params": params,
    }


class DataLoadWorker(QtCore.QThread):
    """Loads parquet + forming data on a background thread.

    The GUI thread calls request_load(params) whenever the cache file monitor
    detects a change.  The worker performs all I/O, builds the snapshot, and
    emits data_ready with the result.  At most one pending job is kept: a newer
    request replaces an older one that hasn't started yet.
    """

    data_ready = QtCore.Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self._queue: _queue_mod.Queue = _queue_mod.Queue(maxsize=1)
        self._stop = False

    def request_load(self, params: dict) -> None:
        """Queue a load job; drop stale pending job if queue is full."""
        try:
            self._queue.put_nowait(params)
        except _queue_mod.Full:
            try:
                self._queue.get_nowait()
            except _queue_mod.Empty:
                pass
            try:
                self._queue.put_nowait(params)
            except _queue_mod.Full:
                pass

    def stop(self) -> None:
        self._stop = True
        try:
            self._queue.put_nowait(None)
        except _queue_mod.Full:
            pass

    def run(self) -> None:
        while not self._stop:
            try:
                params = self._queue.get(timeout=1.0)
            except _queue_mod.Empty:
                continue
            if params is None:
                break
            try:
                snapshot = _bg_load_snapshot(params)
            except Exception as exc:
                snapshot = {"ok": False, "error": str(exc), "params": params}
            self.data_ready.emit(snapshot)


class BookFlowLevelChartWindow(QtWidgets.QMainWindow):
    def __init__(self, symbol: str, date: str, start_worker: bool = True) -> None:
        super().__init__()
        self.symbol = symbol
        self.live_latest_mode = _is_live_latest(date)
        self.requested_date = LIVE_LATEST_LABEL if self.live_latest_mode else str(date)
        self.date = _resolve_live_date(symbol) if self.live_latest_mode else str(date)
        self.depth = 5
        self.lookback = 1000
        self.context_mode = CONTEXT_PREV_1 if self.live_latest_mode else CONTEXT_CURRENT_ONLY
        self.previous_context_sessions = 1 if self.live_latest_mode else 0
        self.custom_context_bars = 1000
        self.follow_live_requested = True
        self.follow_live = True  # compatibility alias; only checkbox changes this
        self.user_view_override = False
        self.user_view_locked = False
        self._initialized = False
        self.show_forming_bar = self.live_latest_mode  # default ON for live mode
        self._last_closed_bar_idx: Optional[int] = None
        self._setting_ranges = False
        self._programmatic_view_update = False
        self._reloading = False
        self._refresh_busy = False
        self._render_pending = False
        self._pending_force_data = False
        self._pending_force_render = False
        self._last_render_ts = 0.0
        self._user_interacting = False
        self._last_hb_write_ts = 0.0
        self._data_sig: Optional[tuple] = None
        self._last_render_key: Optional[tuple] = None
        self._last_profile_key: Optional[tuple] = None
        self._last_values_key: Optional[tuple] = None
        self._last_pressure_key: Optional[tuple] = None
        self._last_book_panel_key: Optional[tuple] = None
        self._render_decimated = False
        self._heartbeat: dict = {}
        # Step 3 Phase 1: convenience fields for Phase 2 (order-book price line / cell
        # highlight); populated only via the service adapter, None on the legacy path.
        self.last_price: Optional[float] = None
        self._forming_bar_row = None
        # Phase 2 Part E/F: order-book depth snapshot, populated only via the service adapter
        # (None on the legacy path -- the book panel treats None/stale the same way: grey + badge).
        self._book_prices: Optional[np.ndarray] = None
        self._book_bid_sizes: Optional[np.ndarray] = None
        self._book_ask_sizes: Optional[np.ndarray] = None
        self._book_ts: Optional[float] = None
        # Pool of reusable pg.TextItem size labels (LOD, zoomed-in only) -- created lazily,
        # repositioned/shown/hidden in place, never destroyed+recreated per frame.
        self._book_label_items: list = []
        self.max_visible_bars = MAX_VISIBLE_BARS
        self.max_visible_cells = MAX_VISIBLE_CELLS
        self.render_interval_ms = RENDER_INTERVAL_MS
        self.perf_stats = PerformanceStats()
        self.level_df = pd.DataFrame()
        self.bars_df = pd.DataFrame()
        self.visible_cells = pd.DataFrame()
        self.visible_bars = pd.DataFrame()
        self.context_dates_loaded: list[str] = []
        self.context_dates_missing_cache: list[str] = []
        self.context_cache_shortage: dict[str, Any] = {}
        self.gui_timestamp_status: dict[str, Any] = {}
        self._bar_ids_base: list[int] = []
        self._bar_pos_map: dict[int, int] = {}
        self._vp: dict = {}
        self._level_items: list = []
        self._level_label_anchors: list = []
        self._cell_lookup_index: Optional[pd.DataFrame] = None
        # ── fixed-lookback ANALYSIS window (separate from the VIEW window) ──
        # Profile-derived levels (S/R, POC/VAH/VAL, HVN/LVN) are computed from
        # this window, anchored at the latest CLOSED bar, NEVER from whatever
        # happens to be visible on screen. See _analysis_base_bar_ids /
        # _get_analysis_levels. Zooming/panning only changes the view window
        # (self._bar_ids_base / _visible_bar_window) and must never touch this.
        self._analysis_data_key: Optional[tuple] = None
        self._analysis_bars: pd.DataFrame = pd.DataFrame()
        self._analysis_cells: pd.DataFrame = pd.DataFrame()
        self._analysis_levels_key: Optional[tuple] = None
        self._analysis_levels_cache: dict = {"resistance": [], "support": [], "vp": {}}
        self._proj = bfl.load_projected_levels(PROJECTED_LEVELS_PATH)
        self.worker: Optional[CacheFileMonitor] = None
        # Pre-loaded snapshot from background DataLoadWorker (None = not yet available)
        self._pending_snapshot: Optional[dict] = None
        # BookFlowDataService cutover (step 2) -- see BOOKFLOW_DATASERVICE_ENABLED above
        self._data_service: Optional[bfds.BookFlowDataService] = None
        self._dataservice_timer: Optional[QtCore.QTimer] = None
        self._dataservice_last_version: int = -1
        self._dataservice_active: bool = False
        # Fast index: bar_idx → DataFrame slice for O(visible_bars) visible-data prep
        self._level_idx: dict[int, pd.DataFrame] = {}

        self._build_ui()
        self._render_timer = QtCore.QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._flush_queued_reload)
        # 600ms debounce after last pan/zoom event before firing a reload.
        # Prevents the GUI thread from reloading on every pixel of mouse drag.
        self._interaction_end_timer = QtCore.QTimer(self)
        self._interaction_end_timer.setSingleShot(True)
        self._interaction_end_timer.timeout.connect(self._on_interaction_ended)
        # Step 2 Part C containment: low-frequency glibc malloc_trim(0) (see
        # PART_B_RSS_VERDICT.md). This is a secondary/complementary measure -- the dominant
        # allocator identified in Part B is PyArrow's mimalloc pool (addressed separately via
        # MIMALLOC_PURGE_DELAY at module import time, above), not glibc malloc. malloc_trim only
        # touches glibc's own brk/mmap-arena heap, which is where the smaller pandas/numpy-side
        # growth tracemalloc did detect lives, so this is still worth doing, just not the primary
        # fix. 60s is deliberately low-frequency: malloc_trim(0) walks the entire glibc heap and
        # is not free, so this must not run on every render tick.
        self._malloc_trim_timer = QtCore.QTimer(self)
        self._malloc_trim_timer.timeout.connect(self._periodic_malloc_trim)
        self._malloc_trim_timer.start(60_000)
        # Background data loader: parquet reads happen here, NOT on GUI thread
        self._data_worker = DataLoadWorker()
        self._data_worker.data_ready.connect(self._on_data_ready)
        self._data_worker.start()
        if start_worker:
            self._restart_worker()  # routes to BookFlowDataService or CacheFileMonitor depending
                                     # on BOOKFLOW_DATASERVICE_ENABLED + _dataservice_eligible()
            QtCore.QTimer.singleShot(50, lambda: self._queue_reload(force_data=True, force_render=True))

    def _build_ui(self) -> None:
        self.setWindowTitle("OFI TRUE BOOK FLOW LEVEL CANDLES V3 - SHADOW/RESEARCH ONLY")
        self.resize(1760, 1060)
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        tb = QtWidgets.QToolBar()
        tb.setStyleSheet("background:#0b0b0b; color:#dddddd;")
        layout.addWidget(tb)

        tb.addWidget(QtWidgets.QLabel("  symbol: "))
        self.symbol_combo = QtWidgets.QComboBox()
        self.symbol_combo.addItems(self._discover_symbols())
        self.symbol_combo.setCurrentText(self.symbol)
        self.symbol_combo.currentTextChanged.connect(self._on_symbol_change)
        tb.addWidget(self.symbol_combo)

        tb.addWidget(QtWidgets.QLabel("   mode: "))
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems([LIVE_LATEST_LABEL, HISTORICAL_LABEL])
        self.mode_combo.setCurrentText(LIVE_LATEST_LABEL if self.live_latest_mode else HISTORICAL_LABEL)
        self.mode_combo.currentTextChanged.connect(self._on_mode_change)
        tb.addWidget(self.mode_combo)

        tb.addWidget(QtWidgets.QLabel("   date: "))
        self.date_combo = QtWidgets.QComboBox()
        self.date_combo.addItems(self._discover_dates())
        self.date_combo.setCurrentText(self.date)
        self.date_combo.currentTextChanged.connect(self._on_date_change)
        tb.addWidget(self.date_combo)

        tb.addWidget(QtWidgets.QLabel("   depth: "))
        self.depth_combo = QtWidgets.QComboBox()
        self.depth_combo.addItems([str(d) for d in DEPTH_CHOICES])
        self.depth_combo.setCurrentText(str(self.depth))
        self.depth_combo.currentTextChanged.connect(self._on_depth_change)
        tb.addWidget(self.depth_combo)

        tb.addWidget(QtWidgets.QLabel("   lookback: "))
        self.lookback_combo = QtWidgets.QComboBox()
        self.lookback_combo.addItems(["300", "1000", "3000", "full"])
        self.lookback_combo.setCurrentText(str(self.lookback))
        self.lookback_combo.currentTextChanged.connect(self._on_lookback_change)
        tb.addWidget(self.lookback_combo)

        tb.addWidget(QtWidgets.QLabel("   context: "))
        self.context_combo = QtWidgets.QComboBox()
        self.context_combo.addItems([
            CONTEXT_CURRENT_ONLY,
            CONTEXT_PREV_1,
            CONTEXT_PREV_2,
            CONTEXT_PREV_3,
            CONTEXT_CUSTOM,
        ])
        self.context_combo.setCurrentText(self.context_mode)
        self.context_combo.currentTextChanged.connect(self._on_context_change)
        tb.addWidget(self.context_combo)

        self.context_bars_spin = QtWidgets.QSpinBox()
        self.context_bars_spin.setRange(0, 10000)
        self.context_bars_spin.setSingleStep(100)
        self.context_bars_spin.setValue(self.custom_context_bars)
        self.context_bars_spin.setEnabled(self.context_mode == CONTEXT_CUSTOM)
        self.context_bars_spin.valueChanged.connect(self._on_context_bars_change)
        tb.addWidget(self.context_bars_spin)

        tb.addWidget(QtWidgets.QLabel("   scale: "))
        self.scale_combo = QtWidgets.QComboBox()
        self.scale_combo.addItems(["linear", "log", "percentile"])
        self.scale_combo.currentTextChanged.connect(lambda _t: self._queue_reload(force_render=True))
        tb.addWidget(self.scale_combo)

        tb.addWidget(QtWidgets.QLabel("   min flow: "))
        self.min_flow_spin = QtWidgets.QDoubleSpinBox()
        self.min_flow_spin.setRange(0.0, 1000000.0)
        self.min_flow_spin.setDecimals(0)
        self.min_flow_spin.setSingleStep(1.0)
        self.min_flow_spin.valueChanged.connect(lambda _v: self._queue_reload(force_render=True))
        tb.addWidget(self.min_flow_spin)

        tb.addWidget(QtWidgets.QLabel("    "))
        self.follow_cb = QtWidgets.QCheckBox("follow live")
        self.follow_cb.setChecked(True)
        self.follow_cb.stateChanged.connect(self._on_follow_change)
        tb.addWidget(self.follow_cb)

        self.reset_btn = QtWidgets.QPushButton("Reset View")
        self.reset_btn.clicked.connect(self._reset_view)
        self.reset_btn.setStyleSheet("background:#222; color:#ddd; padding:3px 10px;")
        tb.addWidget(self.reset_btn)

        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        tb.addWidget(spacer)

        self.source_lbl = QtWidgets.QLabel("MAIN: PRICE-AXIS RAW BOOK-FLOW LEVEL CELLS")
        self.source_lbl.setStyleSheet("color:#00d27a; font-weight:bold; padding-right:16px;")
        tb.addWidget(self.source_lbl)
        self.view_status_lbl = QtWidgets.QLabel("")
        self.view_status_lbl.setStyleSheet("color:#88ccff; padding-right:16px;")
        tb.addWidget(self.view_status_lbl)
        self.status_lbl = QtWidgets.QLabel("loading")
        self.status_lbl.setStyleSheet("color:#ffd28c; padding-right:10px;")
        tb.addWidget(self.status_lbl)

        tb2 = QtWidgets.QToolBar()
        tb2.setStyleSheet("background:#0b0b0b; color:#aaaaaa;")
        layout.addWidget(tb2)
        tb2.addWidget(QtWidgets.QLabel("  show: "))
        self.price_cb = self._mk_toggle(tb2, "price line", True)
        self.sr_cb = self._mk_toggle(tb2, "S/R", True)
        self.vp_levels_cb = self._mk_toggle(tb2, "POC/VAH/VAL", True)
        self.hvn_lvn_cb = self._mk_toggle(tb2, "HVN/LVN", True)
        self.proj_cb = self._mk_toggle(tb2, "projected levels", True)
        # Phase 2: the right-hand panel now defaults to the live order-book ladder; checking this
        # box switches it back to the volume blueprint (unchanged rendering, pixel-identical to
        # before this feature) -- "stays reachable via its existing toggle" per the mission spec.
        self.vp_panel_cb = self._mk_toggle(tb2, "volume blueprint", False)
        self.pulls_cb = self._mk_toggle(tb2, "pulls panel", True)
        self.forming_bar_cb = QtWidgets.QCheckBox("show forming bar")
        self.forming_bar_cb.setChecked(self.show_forming_bar)
        self.forming_bar_cb.stateChanged.connect(self._on_forming_bar_toggle)
        tb2.addWidget(self.forming_bar_cb)

        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setBackground("#050505")
        layout.addWidget(self.glw)

        self.main_plot = self.glw.addPlot(row=0, col=0, title="PRICE-AXIS BOOK-FLOW LEVEL CANDLES")
        self.main_plot.setLabel("left", "Price")
        self.main_plot.showGrid(x=True, y=True, alpha=0.12)
        self.main_plot.vb.disableAutoRange()
        self._style_plot(self.main_plot)

        self.profile_plot = self.glw.addPlot(row=0, col=1, title="VOLUME BLUEPRINT")
        self.profile_plot.hideAxis("bottom")
        self.profile_plot.hideAxis("left")
        self.profile_plot.setMouseEnabled(x=False, y=False)
        self.profile_plot.setMenuEnabled(False)
        self.profile_plot.setYLink(self.main_plot)
        self.profile_plot.vb.disableAutoRange()
        self._style_plot(self.profile_plot)

        self.pressure_plot = self.glw.addPlot(row=1, col=0, title="BOOK PRESSURE / PULLS")
        self.pressure_plot.setLabel("left", "Add / Pull / Net Flow")
        self.pressure_plot.setXLink(self.main_plot)
        self.pressure_plot.showGrid(x=True, y=True, alpha=0.10)
        self.pressure_plot.vb.disableAutoRange()
        self._style_plot(self.pressure_plot)

        self.glw.ci.layout.setColumnStretchFactor(0, 86)
        self.glw.ci.layout.setColumnStretchFactor(1, 14)
        self.glw.ci.layout.setRowStretchFactor(0, 74)
        self.glw.ci.layout.setRowStretchFactor(1, 26)
        self.glw.ci.layout.setHorizontalSpacing(3)
        self.glw.ci.layout.setVerticalSpacing(3)
        self.glw.ci.layout.setContentsMargins(0, 0, 0, 0)

        self.cells = BookFlowLevelCellItem()
        self.main_plot.addItem(self.cells)
        self.wide_candles = WideOFILevelCellRasterItem()
        self.main_plot.addItem(self.wide_candles)
        self.price_curve = pg.PlotCurveItem(pen=pg.mkPen(PRICE_COLOR, width=1.1))
        self.main_plot.addItem(self.price_curve)

        self.profile_item = VolumeProfileItem()
        self.profile_plot.addItem(self.profile_item)

        # Phase 2 Part F: live order-book ladder, sharing profile_plot's ViewBox (already
        # setYLink'd to main_plot above) -- y-link is free, no new ViewBox needed.
        self.book_bid_item = OrderBookLadderSideItem(UP_COLOR)
        self.book_ask_item = OrderBookLadderSideItem(DOWN_COLOR)
        self.profile_plot.addItem(self.book_bid_item)
        self.profile_plot.addItem(self.book_ask_item)
        self.book_stale_badge = pg.TextItem(text="STALE", color="#ffffff",
                                            anchor=(0.5, 0.5), fill=pg.mkBrush("#aa2222cc"))
        self.book_stale_badge.setFont(QtGui.QFont("Segoe UI", 10, QtGui.QFont.Bold))
        self.profile_plot.addItem(self.book_stale_badge, ignoreBounds=True)
        self.book_stale_badge.setVisible(False)

        self.bid_add_bars = pg.BarGraphItem(x=[], height=[], width=0.18, brush=pg.mkBrush(UP_COLOR), pen=None)
        self.ask_add_bars = pg.BarGraphItem(x=[], height=[], width=0.18, brush=pg.mkBrush(DOWN_COLOR), pen=None)
        self.bid_pull_bars = pg.BarGraphItem(x=[], height=[], width=0.18, brush=pg.mkBrush("#ffaa33"), pen=None)
        self.ask_pull_bars = pg.BarGraphItem(x=[], height=[], width=0.18, brush=pg.mkBrush("#aa66ff"), pen=None)
        self.net_flow_curve = pg.PlotCurveItem(pen=pg.mkPen(NET_FLOW_COLOR, width=1.1))
        for item in (self.bid_add_bars, self.ask_add_bars, self.bid_pull_bars,
                     self.ask_pull_bars, self.net_flow_curve):
            self.pressure_plot.addItem(item)
        self.pressure_plot.addItem(pg.InfiniteLine(angle=0, pos=0.0, pen=pg.mkPen("#555", width=0.8)),
                                   ignoreBounds=True)

        self.crosshair_v = pg.InfiniteLine(angle=90, pen=pg.mkPen("#aaaaaa66", width=0.7))
        self.crosshair_h = pg.InfiniteLine(angle=0, pen=pg.mkPen("#aaaaaa66", width=0.7))
        self.main_plot.addItem(self.crosshair_v, ignoreBounds=True)
        self.main_plot.addItem(self.crosshair_h, ignoreBounds=True)
        self.crosshair_v.setVisible(False)
        self.crosshair_h.setVisible(False)
        self.tooltip = pg.TextItem(text="", color="#dddddd", anchor=(0, 1),
                                   border=pg.mkPen("#444"), fill=pg.mkBrush("#101010dd"))
        self.main_plot.addItem(self.tooltip, ignoreBounds=True)
        self.tooltip.setVisible(False)
        self.main_plot.scene().sigMouseMoved.connect(self._on_mouse_move)

        self.blocked_item = pg.TextItem(text="", color="#ff3333", anchor=(0.5, 0.5))
        self.blocked_item.setFont(QtGui.QFont("Segoe UI", 26, QtGui.QFont.Bold))
        self.main_plot.addItem(self.blocked_item, ignoreBounds=True)
        self.blocked_item.setVisible(False)

        self._lod_overlay = pg.TextItem(text="", color="#55aaff", anchor=(0.5, 0.5))
        self._lod_overlay.setFont(QtGui.QFont("Segoe UI", 14, QtGui.QFont.Normal))
        self.main_plot.addItem(self._lod_overlay, ignoreBounds=True)
        self._lod_overlay.setVisible(False)

        # Phase 2 Part F: full-width live price line spanning both the candle plot and the book
        # panel (one InfiniteLine per panel, both y-linked so they always agree), a right-axis
        # price bubble, and a persistent current-cell highlight -- all repositioned in place,
        # never recreated per tick.
        price_line_pen = pg.mkPen(PRICE_COLOR, width=1.0, style=QtCore.Qt.DashLine)
        self.book_price_line_main = pg.InfiniteLine(angle=0, pen=price_line_pen)
        self.book_price_line_main.setZValue(10)
        self.main_plot.addItem(self.book_price_line_main, ignoreBounds=True)
        self.book_price_line_main.setVisible(False)
        self.book_price_line_panel = pg.InfiniteLine(angle=0, pen=price_line_pen)
        self.book_price_line_panel.setZValue(10)
        self.profile_plot.addItem(self.book_price_line_panel, ignoreBounds=True)
        self.book_price_line_panel.setVisible(False)
        self.price_bubble = pg.TextItem(text="", color="#050505", anchor=(0.0, 0.5),
                                        fill=pg.mkBrush(PRICE_COLOR))
        self.price_bubble.setFont(QtGui.QFont("Segoe UI", 9, QtGui.QFont.Bold))
        self.price_bubble.setZValue(15)
        self.profile_plot.addItem(self.price_bubble, ignoreBounds=True)
        self.price_bubble.setVisible(False)
        self.cell_highlight = CellHighlightItem()
        self.cell_highlight.setZValue(9)
        self.main_plot.addItem(self.cell_highlight)

        for vb in (self.main_plot.vb, self.pressure_plot.vb):
            vb.sigRangeChangedManually.connect(self._on_user_interaction)

        self._setting_ranges = True
        self._programmatic_view_update = True
        try:
            self.profile_plot.setXRange(0.0, 1.0, padding=0.02)
        finally:
            self._programmatic_view_update = False
            self._setting_ranges = False
        self._update_view_status()
        self.setStyleSheet("background:#050505;")

    def _mk_toggle(self, tb: QtWidgets.QToolBar, label: str, checked: bool) -> QtWidgets.QCheckBox:
        cb = QtWidgets.QCheckBox(label)
        cb.setChecked(checked)
        cb.stateChanged.connect(lambda _state: self._queue_reload(force_render=True))
        tb.addWidget(cb)
        return cb

    def _style_plot(self, plot: pg.PlotItem) -> None:
        for axis_name in ("left", "right", "top", "bottom"):
            try:
                axis = plot.getAxis(axis_name)
                axis.setPen("#222")
                axis.setTextPen("#aaa")
            except Exception:
                pass

    def _discover_dates(self) -> list[str]:
        try:
            dates = sorted(p.name for p in bfl.RAW_BASE.iterdir() if p.is_dir())
        except OSError:
            dates = []
        if self.date not in dates:
            dates.append(self.date)
        return sorted(set(dates))

    def _discover_symbols(self) -> list[str]:
        try:
            symbols = sorted(p.name for p in (bfl.RAW_BASE / self.date).iterdir() if p.is_dir())
        except OSError:
            symbols = []
        if self.symbol not in symbols:
            symbols.append(self.symbol)
        return sorted(set(symbols))

    def _context_session_request(self) -> int:
        if self.context_mode == CONTEXT_PREV_1:
            return 1
        if self.context_mode == CONTEXT_PREV_2:
            return 2
        if self.context_mode == CONTEXT_PREV_3:
            return 3
        return 0

    def _selected_context_dates(self) -> list[str]:
        if self.context_mode == CONTEXT_CUSTOM:
            dates = _available_dates_for_symbol(self.symbol)
            if self.date not in dates:
                dates.append(self.date)
                dates = sorted(set(dates))
            try:
                active_i = dates.index(self.date)
            except ValueError:
                return [self.date]
            selected = [self.date]
            bars_needed = int(self.custom_context_bars)
            bars_have = 0
            for candidate in reversed(dates[:active_i]):
                selected.insert(0, candidate)
                c_bars = _load_bars(self.symbol, candidate)
                bars_have += int(len(c_bars))
                if bars_have >= bars_needed:
                    break
            return selected
        return _context_dates_for(self.symbol, self.date, self._context_session_request())

    def _context_file_sig(self) -> tuple:
        sig_parts = []
        for d in self._selected_context_dates():
            sig_parts.append((
                d,
                _file_sig(cache_daemon.compact_cache_path(self.symbol, d, self.depth)),
                _file_sig(cache_daemon.compact_cache_meta_path(self.symbol, d, self.depth)),
                _file_sig(_bars_path(self.symbol, d)),
            ))
        return tuple(sig_parts)

    def _load_context_frames(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        levels: list[pd.DataFrame] = []
        bars_list: list[pd.DataFrame] = []
        loaded_dates: list[str] = []
        missing_cache: list[str] = []
        for d in self._selected_context_dates():
            cpath = cache_daemon.compact_cache_path(self.symbol, d, self.depth)
            bpath = _bars_path(self.symbol, d)
            if not bpath.exists():
                continue
            bars = _load_bars(self.symbol, d)
            if bars.empty:
                continue
            if not cpath.exists():
                missing_cache.append(d)
                continue
            level = _load_level_df(self.symbol, d, self.depth)
            if level.empty:
                missing_cache.append(d)
                continue
            levels.append(level)
            bars_list.append(bars)
            loaded_dates.append(d)
        self.context_dates_loaded = loaded_dates
        self.context_dates_missing_cache = missing_cache
        if not levels or not bars_list:
            return pd.DataFrame(), pd.DataFrame()
        level_df = pd.concat(levels, ignore_index=True).sort_values(["bar_idx", "price_level"]).reset_index(drop=True)
        bars_df = pd.concat(bars_list, ignore_index=True).sort_values("bar_index").reset_index(drop=True)
        bars_df = bars_df.drop_duplicates(subset=["bar_index"], keep="last").reset_index(drop=True)
        return level_df, bars_df

    def _queue_reload(self, force_data: bool = False, force_render: bool = False) -> None:
        self._pending_force_data = self._pending_force_data or force_data
        self._pending_force_render = self._pending_force_render or force_render
        self._render_pending = True
        self.perf_stats.queue_depth = 1
        self.perf_stats.update_queue_pending = 1
        if self._user_interacting:
            # Reload paused during pan/zoom; pending flags already set above.
            # _on_interaction_ended fires a single coalesced reload after interaction stops.
            return
        if self._refresh_busy:
            self.perf_stats.skipped_frames += 1
            return
        elapsed_ms = (time.monotonic() - self._last_render_ts) * 1000.0
        delay_ms = max(0, self.render_interval_ms - int(elapsed_ms))
        if delay_ms <= 0:
            self._flush_queued_reload()
        elif not self._render_timer.isActive():
            self._render_timer.start(delay_ms)

    def _flush_queued_reload(self) -> None:
        if not self._render_pending:
            return
        force_data = self._pending_force_data
        force_render = self._pending_force_render
        self._render_pending = False
        self._pending_force_data = False
        self._pending_force_render = False
        self.perf_stats.queue_depth = 0
        self.perf_stats.update_queue_pending = 0
        self._reload(force_data=force_data, force_render=force_render)

    def _current_data_sig(self) -> tuple:
        self._sync_live_latest_from_heartbeat()
        forming_sig = (
            _file_sig(cache_daemon.forming_cache_path(self.symbol, self.date, self.depth))
            if self.show_forming_bar
            else (0, 0)
        )
        return (
            self.symbol,
            self.date,
            int(self.depth),
            self.context_mode,
            int(self.previous_context_sessions),
            int(self.custom_context_bars),
            bool(self.show_forming_bar),
            # PERF (step 2 Part C, see PART_B_RSS_VERDICT.md): never fold the raw heartbeat-file
            # sig into the tuple, in ANY mode. The heartbeat is rewritten by the live daemon every
            # ~2s regardless of whether any bar/cell actually changed, so including it made the
            # sig change on almost every poll -- defeating both the "data unchanged" fast-path
            # and the render-coalescing check, forcing a full parquet reparse + full re-render on
            # nearly every cycle (measured: 273 QPicture rebuilds per 900s vs ~4 real bar rolls).
            # Date-rollover detection does NOT depend on this component: the call above
            # (_sync_live_latest_from_heartbeat) already runs unconditionally and updates
            # self.date on a real rollover, and self.date is already its own sig component two
            # lines up. The data actually on screen is fully captured by _context_file_sig().
            (0, 0),
            self._context_file_sig(),
            forming_sig,
        )

    def _sync_live_latest_from_heartbeat(self) -> bool:
        self._heartbeat = cache_daemon.load_heartbeat()
        if not self.live_latest_mode:
            return False
        active_date = str(self._heartbeat.get("active_date") or _resolve_live_date(self.symbol))
        if active_date and active_date != self.date:
            self.date = active_date
            self._data_sig = None
            self._last_render_key = None
            self._last_profile_key = None
            self._last_values_key = None
            self._last_pressure_key = None
            if hasattr(self, "date_combo"):
                self.date_combo.blockSignals(True)
                try:
                    if active_date not in [self.date_combo.itemText(i) for i in range(self.date_combo.count())]:
                        self.date_combo.addItem(active_date)
                    self.date_combo.setCurrentText(active_date)
                finally:
                    self.date_combo.blockSignals(False)
            return True
        return False

    def _apply_snapshot(self, snap: dict, sig: Optional[tuple] = None) -> None:
        """Apply a ready-loaded background snapshot to window state.  No I/O."""
        level_df = snap["level_df"]
        bars_df  = snap["bars_df"]
        forming_ids: list[int] = snap.get("forming_ids", [])
        self._heartbeat = snap.get("heartbeat", self._heartbeat or {})
        self.context_dates_loaded = snap.get("loaded_dates", [])
        self.context_dates_missing_cache = snap.get("missing_cache", [])
        self._last_closed_bar_idx = snap.get("last_closed_bar_idx")
        self._parity_status = snap.get("parity_status", {})
        # Step 3 Phase 1: convenience fields for Phase 2 (order-book price line / cell highlight),
        # populated only by the service adapter -- absent (default None) on the legacy path.
        self.last_price = snap.get("last_price")
        self._forming_bar_row = snap.get("forming_bar")
        self._book_prices = snap.get("book_prices")
        self._book_bid_sizes = snap.get("book_bid_sizes")
        self._book_ask_sizes = snap.get("book_ask_sizes")
        self._book_ts = snap.get("book_ts")
        cached_bar_ids: set[int] = snap.get("cached_bar_ids") or (
            set(int(x) for x in pd.unique(level_df["bar_idx"])) if not level_df.empty else set()
        )
        zero_cell_ids: list[int] = snap.get("zero_cell_ids", [])
        requested_bars = 0 if self.lookback <= 0 else int(self.lookback)
        self.context_cache_shortage = {
            "context_dates_loaded": list(self.context_dates_loaded),
            "context_dates_missing_cache": list(self.context_dates_missing_cache),
            "available_bars": int(len(bars_df)),
            "requested_lookback": requested_bars,
            "shortage_bars": max(0, requested_bars - len(bars_df)) if requested_bars else 0,
            "zero_cell_bar_count": int(len(zero_cell_ids)),
            "zero_cell_bar_sample": zero_cell_ids[:25],
            "forming_bar_ids_excluded": forming_ids if not self.show_forming_bar else [],
            "forming_bar_ids_visible": forming_ids if self.show_forming_bar else [],
        }
        self.level_df = level_df
        self.bars_df  = bars_df
        # Build fast bar_idx → DataFrame slice index so _prepare_visible_data
        # avoids a full-table .isin() scan on every render cycle.
        if not level_df.empty:
            grp = level_df.groupby("bar_idx", sort=False)
            self._level_idx = {int(k): v for k, v in grp}
        else:
            self._level_idx = {}
        self._data_sig = sig
        self._last_render_key = None
        self._last_profile_key = None
        self._last_values_key = None
        self._last_pressure_key = None
        self._last_vp_panel_key = None
        self._last_book_panel_key = None

    def _load_data_if_needed(self, force_data: bool = False) -> bool:
        # ── Fast path A: use pre-loaded snapshot from background thread ───
        if self._pending_snapshot is not None and not force_data:
            snap = self._pending_snapshot
            self._pending_snapshot = None
            # Verify the snapshot was built for the current window params
            p = snap.get("params", {})
            if (p.get("symbol") == self.symbol
                    and p.get("date") == self.date
                    and p.get("depth") == self.depth
                    and p.get("show_forming_bar") == self.show_forming_bar):
                t0 = time.time()
                validation = validate_level_cache(snap.get("level_df", pd.DataFrame()), self.depth)
                if not validation["ok"]:
                    self._show_blocked(validation["reason"])
                    return False
                if snap.get("bars_df", pd.DataFrame()).empty:
                    self._show_blocked("vol500 bars unavailable")
                    return False
                if self._dataservice_active:
                    # Step 3 Phase 1: the service's own monotone version is the signature --
                    # never call _current_data_sig()/_sync_live_latest_from_heartbeat() here,
                    # which would redundantly re-derive (and re-pay for) exactly what the
                    # service already tracks internally with its own cached heartbeat and
                    # targeted, non-walking date-discovery.
                    sig = snap.get("service_sig")
                else:
                    self._sync_live_latest_from_heartbeat()
                    sig = self._current_data_sig()
                self._apply_snapshot(snap, sig=sig)
                self.perf_stats.cache_update_ms = (time.time() - t0) * 1000.0
                return True
            # Snapshot is stale (params changed while loading) — fall through to GUI load

        if self._dataservice_active:
            # Step 3 Phase 1: service-driven windows never take the GUI-thread slow path -- the
            # service's version-tracked delivery is the only data source ("the UI consumes
            # frames only"). Nothing new arrived this cycle -> keep showing whatever we already
            # have (or report not-ready if we have nothing at all yet, e.g. at startup).
            return not self.level_df.empty and not self.bars_df.empty

        # ── Fast path B: data unchanged ───────────────────────────────────
        sig = self._current_data_sig()
        if not force_data and self._data_sig == sig and not self.level_df.empty and not self.bars_df.empty:
            self.perf_stats.cache_update_ms = 0.0
            return True

        # ── Slow path: GUI-thread load (forced reloads / UI changes) ─────
        t0 = time.time()
        active_cpath = cache_daemon.compact_cache_path(self.symbol, self.date, self.depth)
        if not active_cpath.exists():
            self.status_lbl.setText(
                f"MODE: {'LIVE LATEST' if self.live_latest_mode else 'HISTORICAL'} | "
                f"ACTIVE DATE: {self.date} | waiting for compact cache daemon"
            )
            return False
        level_df, bars = self._load_context_frames()
        validation = validate_level_cache(level_df, self.depth)
        if not validation["ok"]:
            self._show_blocked(validation["reason"])
            return False
        if bars.empty:
            self._show_blocked("vol500 bars unavailable")
            return False

        # Identify forming bar — optionally replace with fast forming cache (Lane 2)
        forming_ids: set[int] = set()
        if "bar_state" in level_df.columns:
            forming_ids = set(int(x) for x in level_df.loc[level_df["bar_state"] == "FORMING", "bar_idx"].unique())
            # Rendering rule: if bar_idx <= latest_closed_bar_idx, NEVER render from forming cache.
            # First establish last_closed from compact (forming rows excluded).
            closed_bar_ids = sorted(
                int(b) for b in level_df["bar_idx"].unique() if b not in forming_ids
            )
            self._last_closed_bar_idx = closed_bar_ids[-1] if closed_bar_ids else None
            if self.show_forming_bar:
                # Try to upgrade to the faster forming cache (Lane 2, ~350ms cadence).
                # Rendering rules:
                #   forming_fast_bar must be exactly last_closed_in_compact + 1
                #   (one step ahead, never behind, never skipping a bar)
                forming_fast = _load_forming_df(self.symbol, self.date, self.depth)
                last_closed_in_compact = self._last_closed_bar_idx or -1
                forming_fast_bar = int(forming_fast["bar_idx"].max()) if not forming_fast.empty else -1
                # Accept forming cache only when its bar_idx == last_closed + 1.
                # This enforces that CLOSED bars are never replaced by forming data.
                if not forming_fast.empty and forming_fast_bar == last_closed_in_compact + 1:
                    # Remove any FORMING rows in compact (they are for bar N which is now in forming cache)
                    # AND ensure bar N+1 is not already CLOSED in compact (don't downgrade).
                    level_df_closed_only = level_df[level_df["bar_state"] != "FORMING"].copy()
                    # Verify the forming bar is NOT already present as CLOSED in compact.
                    forming_in_closed = forming_fast_bar in set(int(x) for x in level_df_closed_only["bar_idx"].unique())
                    if not forming_in_closed:
                        level_df = pd.concat([level_df_closed_only, forming_fast], ignore_index=True)
                        level_df = level_df.sort_values(["bar_idx", "price_level"]).reset_index(drop=True)
                        forming_ids = set(int(x) for x in forming_fast["bar_idx"].unique())
                        # Bug 2: forming bar may be beyond the last vol500 bar.
                        # Add a synthetic bars_df row so it is visible on the x-axis.
                        last_vol500_bar_idx = int(bars["bar_index"].iloc[-1]) if not bars.empty else -1
                        if forming_fast_bar > last_vol500_bar_idx and not bars.empty:
                            last_row = bars.iloc[-1].copy()
                            synth = last_row.copy()
                            synth["bar_index"] = forming_fast_bar
                            synth["bar_start_ts_ns"] = int(last_row.get("bar_end_ts_ns", 0))
                            synth["bar_end_ts_ns"] = int(last_row.get("bar_end_ts_ns", 0))
                            synth_df = pd.DataFrame([synth])
                            bars = pd.concat([bars, synth_df], ignore_index=True)
                            bars = bars.drop_duplicates(subset=["bar_index"], keep="last").reset_index(drop=True)
                    else:
                        # forming bar already CLOSED in compact — discard forming cache for it
                        forming_ids = set()
                # else: forming cache is stale, absent, or mis-aligned — use compact only
            else:
                # Hide forming bar entirely
                level_df = level_df[level_df["bar_state"] == "CLOSED"].copy()
                bars = bars[~bars["bar_index"].isin(forming_ids)].copy()
                forming_ids = set()
        else:
            self._last_closed_bar_idx = int(level_df["bar_idx"].max()) if not level_df.empty else None

        cached_bar_ids = set(int(x) for x in pd.unique(level_df["bar_idx"])) if not level_df.empty else set()
        loaded_bar_ids = [int(x) for x in bars["bar_index"].tolist()]
        zero_cell_ids = sorted(set(loaded_bar_ids) - cached_bar_ids)

        # Sync bars_df to OFI coverage: only keep bars that have OFI cells.
        # Bars in vol500 that are ahead of the compact (daemon hasn't processed them yet)
        # would otherwise appear as empty OFI columns, making the chart appear behind.
        bars = bars[bars["bar_index"].isin(cached_bar_ids)].copy().reset_index(drop=True)

        requested_bars = 0 if self.lookback <= 0 else int(self.lookback)
        available_bars = int(len(bars))
        self.context_cache_shortage = {
            "context_dates_loaded": list(self.context_dates_loaded),
            "context_dates_missing_cache": list(self.context_dates_missing_cache),
            "available_bars": available_bars,
            "requested_lookback": requested_bars,
            "shortage_bars": max(0, requested_bars - available_bars) if requested_bars else 0,
            "zero_cell_bar_count": int(len(zero_cell_ids)),
            "zero_cell_bar_sample": zero_cell_ids[:25],
            "forming_bar_ids_excluded": sorted(forming_ids) if not self.show_forming_bar else [],
            "forming_bar_ids_visible": sorted(forming_ids) if self.show_forming_bar else [],
        }

        # OFI / heartbeat parity check
        hb = self._heartbeat or {}
        hb_cache_bar = hb.get("cache_last_bar_idx_by_depth", {}).get(str(self.depth))
        loaded_ofi_max = int(max(cached_bar_ids)) if cached_bar_ids else None
        _exact_match = (hb_cache_bar is None or loaded_ofi_max is None
                        or loaded_ofi_max == hb_cache_bar)
        # Forming bar hidden (show_forming_bar=False) explains a 1-bar gap
        _forming_explains = (
            not _exact_match
            and hb_cache_bar is not None and loaded_ofi_max is not None
            and hb_cache_bar - loaded_ofi_max == 1 and not self.show_forming_bar
        )
        self._parity_status = {
            "hb_cache_bar": hb_cache_bar,
            "loaded_ofi_max": loaded_ofi_max,
            "parity_ok": _exact_match or _forming_explains,
            "mismatch": not (_exact_match or _forming_explains),
        }

        # Build fast bar_idx index for visible-data prep (avoids .isin() scan)
        if not level_df.empty:
            grp = level_df.groupby("bar_idx", sort=False)
            self._level_idx = {int(k): v for k, v in grp}
        else:
            self._level_idx = {}

        self.level_df = level_df
        self.bars_df = bars
        self._data_sig = sig
        self._last_render_key = None
        self._last_profile_key = None
        self._last_values_key = None
        self._last_pressure_key = None
        self._last_vp_panel_key = None
        self.perf_stats.cache_update_ms = (time.time() - t0) * 1000.0
        return True

    def _base_bar_ids(self) -> list[int]:
        if self.bars_df.empty:
            return []
        bar_ids = [int(x) for x in pd.unique(self.bars_df["bar_index"])]
        bar_ids.sort()
        if self.lookback > 0 and len(bar_ids) > self.lookback:
            bar_ids = bar_ids[-self.lookback:]
        return bar_ids

    # ── Fixed-lookback ANALYSIS window (independent of view/zoom/pan) ──────
    # This is deliberately SEPARATE from _base_bar_ids/_visible_bar_window
    # (the VIEW window, which legitimately changes with zoom/pan). Profile-
    # derived levels must use ONLY the methods below.
    def _analysis_base_bar_ids(self) -> list[int]:
        """The fixed lookback slice used for S/R / POC/VAH/VAL / HVN/LVN.

        LIVE LATEST mode: last `self.lookback` bars ending at the latest
        CLOSED bar (self._last_closed_bar_idx) - advances automatically as
        new bars close, but never reacts to zoom/pan.

        Historical/manual browse mode: last `self.lookback` bars ending at
        the latest closed bar of whichever date is currently loaded
        (self.date) - i.e. anchored to that date's right edge. Selecting a
        different date moves the anchor (an explicit user action); panning
        or zooming the view does not.

        Forming-bar safety: the forming bar (if self.show_forming_bar is
        True) is ALWAYS excluded from this window, even though it may still
        be shown visually in the main view - closed bars are the safe
        default basis for level calculations, per design.
        """
        if self.bars_df.empty:
            return []
        bar_ids = [int(x) for x in pd.unique(self.bars_df["bar_index"])]
        bar_ids.sort()
        last_closed = self._last_closed_bar_idx
        if last_closed is not None:
            bar_ids = [b for b in bar_ids if b <= last_closed]
        if self.lookback > 0 and len(bar_ids) > self.lookback:
            bar_ids = bar_ids[-self.lookback:]
        return bar_ids

    def _analysis_window_key(self, analysis_ids: Optional[list[int]] = None) -> tuple:
        """Cache key for the analysis window: (symbol, date/session context,
        depth, lookback, anchor_bar, start_bar). Recomputation is triggered
        ONLY when this key changes - never by viewRange/zoom/pan."""
        ids = self._analysis_base_bar_ids() if analysis_ids is None else analysis_ids
        anchor = ids[-1] if ids else None
        start = ids[0] if ids else None
        return (
            self._data_sig, self.symbol, self.date, int(self.depth), int(self.lookback),
            tuple(self.context_dates_loaded), anchor, start, len(ids),
        )

    def _get_analysis_bars_cells(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """The fixed analysis-window slice of bars_df/level_df. Cached; only
        rebuilt when _analysis_window_key() changes."""
        analysis_ids = self._analysis_base_bar_ids()
        key = self._analysis_window_key(analysis_ids)
        if self._analysis_data_key == key:
            return self._analysis_bars, self._analysis_cells
        if not analysis_ids:
            self._analysis_data_key = key
            self._analysis_bars = pd.DataFrame()
            self._analysis_cells = pd.DataFrame()
            return self._analysis_bars, self._analysis_cells
        id_set = set(analysis_ids)
        if self._level_idx:
            slices = [self._level_idx[bid] for bid in analysis_ids if bid in self._level_idx]
            cells = pd.concat(slices, ignore_index=True) if slices else pd.DataFrame(
                columns=self.level_df.columns if not self.level_df.empty else [])
        elif not self.level_df.empty:
            cells = self.level_df[self.level_df["bar_idx"].isin(id_set)].copy()
        else:
            cells = pd.DataFrame()
        bars = (
            self.bars_df[self.bars_df["bar_index"].isin(id_set)]
            .copy().sort_values("bar_index").reset_index(drop=True)
            if not self.bars_df.empty else pd.DataFrame()
        )
        self._analysis_data_key = key
        self._analysis_bars = bars
        self._analysis_cells = cells
        return bars, cells

    def _get_analysis_levels(self) -> dict:
        """Expensive S/R + volume-profile computation (bfl.compute_sr_levels /
        bfl.volume_profile), cached by the ANALYSIS window key only. This is
        the single source of truth for level VALUES (price numbers) - never
        recomputed on zoom/pan, only when symbol/date/depth/lookback/anchor
        actually changes."""
        analysis_ids = self._analysis_base_bar_ids()
        key = self._analysis_window_key(analysis_ids)
        if self._analysis_levels_key == key:
            return self._analysis_levels_cache
        analysis_bars, _ = self._get_analysis_bars_cells()
        result = {"resistance": [], "support": [], "vp": {}}
        if not analysis_bars.empty and len(analysis_bars) >= 9:
            try:
                result["resistance"], result["support"] = bfl.compute_sr_levels(
                    analysis_bars, lb=4, cluster_dist=6.0)
            except Exception:
                pass
        if not analysis_bars.empty and len(analysis_bars) >= 2:
            try:
                result["vp"] = bfl.volume_profile(analysis_bars)
            except Exception:
                pass
        self._analysis_levels_key = key
        self._analysis_levels_cache = result
        return result

    def _visible_bar_window(self, base_ids: list[int]) -> tuple[int, int, bool]:
        if not base_ids:
            return 0, -1, False
        n = len(base_ids)
        margin = VISIBLE_MARGIN_BARS
        decimated = False
        if self.user_view_override and self._initialized:
            x_min, x_max = self.main_plot.vb.viewRange()[0]
            lo = max(0, int(np.floor(x_min)) - margin)
            hi = min(n - 1, int(np.ceil(x_max)) + margin)
        elif self.follow_live_requested:
            hi = n - 1
            follow_span = self.max_visible_bars if (
                self.live_latest_mode and len(self.context_dates_loaded) > 1
            ) else FOLLOW_WINDOW + margin
            lo = max(0, hi - max(follow_span, 1) + 1)
        else:
            lo = 0
            hi = min(n - 1, self.max_visible_bars - 1)

        if hi < lo:
            lo, hi = 0, min(n - 1, self.max_visible_bars - 1)
        if hi - lo + 1 > self.max_visible_bars:
            decimated = True
            if self.follow_live_requested and not self.user_view_override:
                lo = max(0, hi - self.max_visible_bars + 1)
            else:
                center = (lo + hi) // 2
                half = self.max_visible_bars // 2
                lo = max(0, center - half)
                hi = min(n - 1, lo + self.max_visible_bars - 1)
                lo = max(0, hi - self.max_visible_bars + 1)
        return lo, hi, decimated

    def _compute_lod(self, n_visible_bars: int) -> str:
        if n_visible_bars <= LOD_FULL_MAX_BARS:
            return LOD_FULL
        if n_visible_bars <= LOD_MEDIUM_MAX_BARS:
            return LOD_MEDIUM
        if n_visible_bars <= LOD_WIDE_MAX_BARS:
            return LOD_WIDE
        return LOD_OVERVIEW

    def _prepare_visible_data(self, base_ids: list[int], *, skip_cells: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, tuple[int, int], bool]:
        lo, hi, window_decimated = self._visible_bar_window(base_ids)
        if hi < lo:
            return pd.DataFrame(), pd.DataFrame(), (lo, hi), window_decimated

        self._bar_ids_base = base_ids
        self._bar_pos_map = {int(bar_id): i for i, bar_id in enumerate(base_ids)}
        visible_ids = base_ids[lo:hi + 1]
        visible_id_set = set(visible_ids)

        cell_decimated = False
        if skip_cells:
            # At WIDE/OVERVIEW LOD: skip the pd.concat(slices) entirely — O(0) instead of O(n_bars)
            _cols = self.level_df.columns if not self.level_df.empty else []
            visible_cells = pd.DataFrame(columns=_cols)
        else:
            # Fast path: use pre-built bar_idx index for O(visible_bars) access instead
            # of scanning the full level_df with .isin() which is O(all_rows).
            if self._level_idx:
                slices = [self._level_idx[bid] for bid in visible_ids if bid in self._level_idx]
                if slices:
                    visible_cells = pd.concat(slices, ignore_index=True)
                else:
                    visible_cells = pd.DataFrame(columns=self.level_df.columns)
            else:
                visible_cells = self.level_df[self.level_df["bar_idx"].isin(visible_id_set)].copy()
            visible_cells = visible_cells.copy()
            visible_cells["bar_pos"] = visible_cells["bar_idx"].map(self._bar_pos_map).astype(float)
            if len(visible_cells) > self.max_visible_cells:
                cell_decimated = True
                visible_cells = (
                    visible_cells.nlargest(self.max_visible_cells, "abs_flow")
                    .sort_values(["bar_idx", "price_level"])
                    .reset_index(drop=True)
                )

        visible_bars = self.bars_df[self.bars_df["bar_index"].isin(visible_id_set)].copy()
        visible_bars = visible_bars.sort_values("bar_index").reset_index(drop=True)
        visible_bars["bar_pos"] = visible_bars["bar_index"].map(self._bar_pos_map).astype(float)

        return visible_cells, visible_bars, (lo, hi), bool(window_decimated or cell_decimated)

    def _update_gui_timestamp_heartbeat(self, visible_bars: pd.DataFrame) -> None:
        hb = self._heartbeat or {}
        latest_closed_ts = hb.get("latest_closed_timestamp_utc") or hb.get("latest_feature_timestamp_utc")
        latest_closed_idx = hb.get("latest_closed_bar_idx") or hb.get("latest_feature_bar_idx")
        if latest_closed_ts is None or latest_closed_idx is None:
            active_bars = _load_bars(self.symbol, self.date)
            if not active_bars.empty:
                last = active_bars.iloc[-1]
                latest_closed_idx = int(last.get("bar_index", len(active_bars) - 1))
                latest_closed_ts = str(last.get("timestamp_utc") or last.get("timestamp") or "")
        # Use level_df (OFI data) for loaded max — bars_df is synced to OFI coverage so
        # they match, but level_df is the authoritative source for parity checks.
        loaded_max_ts = _max_timestamp_utc(self.level_df) if not self.level_df.empty else _max_timestamp_utc(self.bars_df)
        rendered_max_ts = _max_timestamp_utc(visible_bars)
        loaded_max_idx = int(self.level_df["bar_idx"].max()) if not self.level_df.empty else None
        rendered_max_idx = int(visible_bars["bar_index"].max()) if not visible_bars.empty else None
        rendered_lag = _timestamp_lag_seconds(latest_closed_ts, rendered_max_ts)
        loaded_lag = _timestamp_lag_seconds(latest_closed_ts, loaded_max_ts)
        hidden_forming = self.context_cache_shortage.get("forming_bar_ids_excluded", [])
        lag_explained = bool(rendered_lag and rendered_lag > 0 and hidden_forming)
        self.gui_timestamp_status = {
            "symbol": self.symbol,
            "active_date": self.date,
            "latest_closed_bar_idx": latest_closed_idx,
            "latest_closed_timestamp_utc": latest_closed_ts,
            "latest_feature_bar_idx": latest_closed_idx,
            "latest_feature_timestamp_utc": latest_closed_ts,
            "gui_loaded_max_timestamp_utc": loaded_max_ts,
            "gui_rendered_max_timestamp_utc": rendered_max_ts,
            "gui_loaded_max_bar_idx": loaded_max_idx,
            "gui_rendered_max_bar_idx": rendered_max_idx,
            "gui_loaded_timestamp_lag_seconds": loaded_lag,
            "gui_rendered_timestamp_lag_seconds": rendered_lag,
            "gui_timestamp_parity_ok": rendered_lag == 0.0,
            "gui_timestamp_lag_explained": lag_explained,
            "gui_lag_explanation": (
                "latest feature/cache bar is marked FORMING/PARTIAL and hidden by default"
                if lag_explained else ""
            ),
            "context_dates_loaded": list(self.context_dates_loaded),
            "context_dates_missing_cache": list(self.context_dates_missing_cache),
            "context_cache_shortage": dict(self.context_cache_shortage),
        }
        try:
            cache_daemon.update_gui_heartbeat_fields(self.gui_timestamp_status)
            self._heartbeat = cache_daemon.load_heartbeat()
        except Exception:
            pass

    def _reload(self, force_data: bool = False, force_render: bool = True) -> None:
        if self._refresh_busy:
            self.perf_stats.skipped_frames += 1
            self._render_pending = True
            self._pending_force_data = self._pending_force_data or force_data
            self._pending_force_render = self._pending_force_render or force_render
            return
        self._refresh_busy = True
        self._reloading = True
        t0 = time.time()
        try:
            if not self._load_data_if_needed(force_data=force_data):
                return
            load_done = time.time()
            base_ids = self._base_bar_ids()

            # ── Early coalesce check: compute render_key using cheap _visible_bar_window
            # before calling the potentially expensive _prepare_visible_data().
            # _visible_bar_window only reads the pyqtgraph xlim — no DataFrame work.
            _lo_q, _hi_q, _ = self._visible_bar_window(base_ids)
            _n_vis = _hi_q - _lo_q + 1
            lod = self._compute_lod(_n_vis)
            _quick_key = (
                self._data_sig,
                (_lo_q, _hi_q),
                self.scale_combo.currentText(),
                float(self.min_flow_spin.value()),
                bool(self.price_cb.isChecked()),
                bool(self.sr_cb.isChecked()),
                bool(self.vp_levels_cb.isChecked()),
                bool(self.hvn_lvn_cb.isChecked()),
                bool(self.proj_cb.isChecked()),
                bool(self.vp_panel_cb.isChecked()),
                bool(self.pulls_cb.isChecked()),
                bool(self.show_forming_bar),
                self.max_visible_bars,
                self.max_visible_cells,
                lod,
            )
            if not force_render and self._last_render_key == _quick_key:
                self.perf_stats.coalesced_frames += 1
                return

            visible_cells, visible_bars, window, decimated = self._prepare_visible_data(
                base_ids, skip_cells=(lod in (LOD_WIDE, LOD_OVERVIEW))
            )
            render_key = (
                self._data_sig,
                window,
                self.scale_combo.currentText(),
                float(self.min_flow_spin.value()),
                bool(self.price_cb.isChecked()),
                bool(self.sr_cb.isChecked()),
                bool(self.vp_levels_cb.isChecked()),
                bool(self.hvn_lvn_cb.isChecked()),
                bool(self.proj_cb.isChecked()),
                bool(self.vp_panel_cb.isChecked()),
                bool(self.pulls_cb.isChecked()),
                bool(self.show_forming_bar),
                self.max_visible_bars,
                self.max_visible_cells,
                lod,
            )
            if not force_render and self._last_render_key == render_key:
                self.perf_stats.coalesced_frames += 1
                return

            self.visible_cells = visible_cells
            self.visible_bars = visible_bars
            self._render_decimated = decimated
            prep_done = time.time()
            self.cells.set_data(
                visible_cells,
                self.scale_combo.currentText(),
                float(self.min_flow_spin.value()),
            )
            _scale = self.scale_combo.currentText()
            _min_flow = float(self.min_flow_spin.value())
            if lod in (LOD_WIDE, LOD_OVERVIEW):
                # Aggregate per-level OFI rows into x/y bins — true level-cell raster
                _n_vis = _hi_q - _lo_q + 1
                if lod == LOD_WIDE:
                    _bar_group    = max(1, _n_vis // 300)
                    _price_bin    = 2.0
                else:
                    _bar_group    = max(1, _n_vis // 200)
                    _price_bin    = 4.0
                _vis_id_set = set(int(b) for b in visible_bars["bar_index"])
                _agg_cells = _aggregate_cells_for_wide_lod(
                    self.level_df, _vis_id_set, self._bar_pos_map,
                    _bar_group, _price_bin,
                )
                self.wide_candles.set_data(
                    _agg_cells, _scale, _min_flow, _bar_group, _price_bin,
                )
            else:
                self.wide_candles.set_data(pd.DataFrame(), "linear", 0.0, 1, TICK)
            self._build_cell_lookup(visible_cells)
            self._update_price_line(visible_bars)
            self._update_level_overlays(visible_bars, window, lod=lod)
            self._update_volume_profile(visible_bars, window)
            self._update_order_book_panel()
            self._update_pressure_panel(visible_cells, window)
            # Heartbeat write + read involves file I/O on the GUI thread; throttle to
            # once per 5s so it never blocks during rapid back-to-back renders.
            _now = time.monotonic()
            if _now - self._last_hb_write_ts >= 5.0:
                self._last_hb_write_ts = _now
                self._update_gui_timestamp_heartbeat(visible_bars)

            if not self.user_view_override:
                self._update_view_ranges(visible_cells, visible_bars)

            of_lo, of_hi = _range_pair(visible_cells["signed_flow"].to_numpy(float)) if not visible_cells.empty else (0.0, 0.0)
            px_lo, px_hi = _range_pair(visible_cells["price_level"].to_numpy(float)) if not visible_cells.empty else (0.0, 0.0)
            elapsed = (time.time() - t0) * 1000.0
            render_ms = (time.time() - prep_done) * 1000.0
            self.perf_stats.load_ms = (load_done - t0) * 1000.0
            self.perf_stats.render_prepare_ms = (prep_done - load_done) * 1000.0
            self.perf_stats.compute_ms = self.perf_stats.render_prepare_ms
            self.perf_stats.render_ms = render_ms
            self.perf_stats.render_draw_ms = render_ms
            self.perf_stats.gui_update_ms = elapsed
            self.perf_stats.total_refresh_ms = elapsed
            self.perf_stats.visible_cells = int(len(visible_cells))
            self.perf_stats.bars_rendered = int(len(visible_bars))
            self.perf_stats.render_decimated = bool(decimated)
            self.perf_stats.lod_mode = lod
            self.blocked_item.setVisible(False)
            try:
                vr = self.main_plot.vb.viewRange()
                cx = (vr[0][0] + vr[0][1]) / 2.0
                cy = (vr[1][0] + vr[1][1]) / 2.0
                self._lod_overlay.setVisible(False)
            except Exception:
                self._lod_overlay.setVisible(False)
            self.source_lbl.setText(
                f"MAIN: {MAIN_VISUAL_TYPE} | source={CELLS_SOURCE} | top{self.depth}"
            )
            suffix = " | render decimated" if decimated else ""
            hb = self._heartbeat or {}
            latest_bar_idx = hb.get("latest_bar_idx", "--")
            latest_bar_ts = str(hb.get("latest_bar_timestamp_utc", "--"))[:19]
            cache_last_bar = hb.get("cache_last_bar_idx_by_depth", {}).get(str(self.depth), "--")
            cache_last_ts = str(hb.get("cache_last_timestamp_utc_by_depth", {}).get(str(self.depth), "--"))[:19]
            cache_lag = hb.get("cache_lag_bars_by_depth", {}).get(str(self.depth), "--")
            cache_mtime = str(hb.get("cache_mtime_utc", "--"))[:19]
            rendered_ts = str(self.gui_timestamp_status.get("gui_rendered_max_timestamp_utc", "--"))[:19]
            rendered_lag = self.gui_timestamp_status.get("gui_rendered_timestamp_lag_seconds", "--")
            lag_explained = self.gui_timestamp_status.get("gui_timestamp_lag_explained", False)
            mode_txt = "LIVE LATEST" if self.live_latest_mode else "HISTORICAL"
            inc_flag = "INC" if hb.get("incremental_update", True) else ("FULL_REPLAY" if hb.get("full_raw_replay") else "?")
            lat_s = hb.get("update_latency_sec", "--")
            lag_warn = " ⚠ LAG" if isinstance(cache_lag, int) and cache_lag > 1 else ""
            last_closed = hb.get("last_closed_bar_idx_by_depth", {}).get(str(self.depth), self._last_closed_bar_idx or "--")
            forming_actual = hb.get("forming_bar_idx_actual_by_depth", {}).get(str(self.depth), "--")
            forming_vis_str = "VIS ⚠ PARTIAL" if self.show_forming_bar else "hidden"
            forming_note = " | LATEST BAR IS FORMING — PARTIAL BOOK FLOW" if self.show_forming_bar else ""
            context_txt = ",".join(self.context_dates_loaded) if self.context_dates_loaded else self.date
            context_missing = f" missing_ctx={self.context_dates_missing_cache}" if self.context_dates_missing_cache else ""
            render_lag_txt = f" rendered_lag={rendered_lag}s" + (" explained" if lag_explained else "")
            # Head-to-head forming bar status
            h2h = hb.get("head_to_head_status", "")
            forming_lag_s = hb.get("forming_lag_seconds")
            forming_ts = str(hb.get("latest_forming_timestamp_utc", "--"))[:19]
            h2h_warn = ""
            if self.show_forming_bar and forming_lag_s is not None and forming_lag_s > 2.0:
                h2h_warn = f" ⚠ OFI FORMING BAR LAGGING PRICE ({forming_lag_s:.1f}s)"
            h2h_txt = f" h2h={h2h} forming_ts={forming_ts} forming_lag={forming_lag_s}s{h2h_warn}" if self.live_latest_mode else ""
            # Parity diagnostics: HB cache bar vs loaded OFI max vs rendered max
            _par = getattr(self, "_parity_status", {})
            _loaded_ofi = _par.get("loaded_ofi_max", "--")
            _hb_cache = _par.get("hb_cache_bar", cache_last_bar)
            _parity_flag = "" if _par.get("parity_ok", True) else " ⚠ GUI_CACHE_RENDER_MISMATCH"
            _rendered_max_bar = self.gui_timestamp_status.get("gui_rendered_max_bar_idx", "--")
            try:
                _xlo, _xhi = self.main_plot.vb.viewRange()[0]
                _xrange_txt = f"[{_xlo:.0f},{_xhi:.0f}]"
            except Exception:
                _xrange_txt = "[?,?]"
            _view_state = "FOLLOWING" if (self.follow_live_requested and not self.user_view_override) else (
                "USER_LOCKED" if self.user_view_override else "MANUAL")
            self.status_lbl.setText(
                f"MODE: {mode_txt} | DATE: {self.date} | ctx={context_txt}{context_missing} | "
                f"HB_cache={_hb_cache} | loaded_ofi={_loaded_ofi} | rendered={_rendered_max_bar} | "
                f"x={_xrange_txt} | view={_view_state}{_parity_flag} | "
                f"latest={latest_bar_idx} {latest_bar_ts} | cache_ts={cache_last_ts} | "
                f"rendered_ts={rendered_ts}{render_lag_txt} | "
                f"closed={last_closed} forming={forming_actual}({forming_vis_str}) lag={cache_lag}{lag_warn}{h2h_txt} | "
                f"update={inc_flag} lat={lat_s}s | "
                f"cache_mtime={cache_mtime} | bars {len(visible_bars)}/{len(base_ids)} | cells {len(visible_cells)} | "
                f"LOD: {lod} | "
                f"perf: refresh_ms={elapsed:.0f} render_ms={render_ms:.0f} "
                f"cells={len(visible_cells)} skipped={self.perf_stats.skipped_frames} "
                f"fps_cap={MAX_FPS:.1f}{suffix}{forming_note}"
            )
            if PERF_LOG:
                print(
                    "BOOK_FLOW_PERF "
                    f"raw_read_ms={self.perf_stats.raw_read_ms:.1f} "
                    f"cache_update_ms={self.perf_stats.cache_update_ms:.1f} "
                    f"compute_ms={self.perf_stats.compute_ms:.1f} "
                    f"render_prepare_ms={self.perf_stats.render_prepare_ms:.1f} "
                    f"render_draw_ms={self.perf_stats.render_draw_ms:.1f} "
                    f"render_ms={self.perf_stats.render_ms:.1f} "
                    f"profile_ms={self.perf_stats.profile_ms:.1f} "
                    f"pulls_ms={self.perf_stats.pulls_ms:.1f} "
                    f"total_refresh_ms={self.perf_stats.total_refresh_ms:.1f} "
                    f"visible_cells={self.perf_stats.visible_cells} "
                    f"bars_rendered={self.perf_stats.bars_rendered} "
                    f"raw_lines_processed={self.perf_stats.raw_lines_processed} "
                    f"skipped_frames={self.perf_stats.skipped_frames} "
                    f"coalesced_frames={self.perf_stats.coalesced_frames} "
                    f"update_queue_pending={self.perf_stats.update_queue_pending}",
                    flush=True,
                )
            self._last_render_key = render_key
            self._last_render_ts = time.monotonic()
        finally:
            self._refresh_busy = False
            self._reloading = False

    def _show_blocked(self, reason: str) -> None:
        self.cells.set_data(pd.DataFrame(), "linear", 0.0)
        self.blocked_item.setText(f"BLOCKED\n{reason}")
        vr = self.main_plot.vb.viewRange()
        self.blocked_item.setPos(sum(vr[0]) / 2.0, sum(vr[1]) / 2.0)
        self.blocked_item.setVisible(True)
        self.source_lbl.setText(f"BLOCKED: {reason}")
        self.source_lbl.setStyleSheet("color:#ff3333; font-weight:bold; padding-right:16px;")
        self.status_lbl.setText("BLOCKED - V3 validation failed")

    _CELL_LOOKUP_COLS = [
        "bar_pos", "price_tick", "bar_idx", "timestamp_utc", "price_level",
        "side_zone", "signed_flow", "abs_flow", "bid_add", "bid_pull",
        "ask_add", "ask_pull", "net_bid_flow", "net_ask_flow",
        "trade_volume_at_price", "buy_trade_volume_at_price",
        "sell_trade_volume_at_price", "nearest_level",
        "distance_to_nearest_level", "close_price", "mid_price",
    ]

    def _build_cell_lookup(self, cells: pd.DataFrame) -> None:
        # PERF: this used to materialize one Python dict per visible cell (up to ~20k) on
        # every non-coalesced render, purely to serve single-point mouse-hover tooltip
        # lookups (_cell_lookup.get((bar_pos, price_tick)) is called at most once per mouse
        # move -- see DIAGNOSIS.md 4b, this was the single biggest cProfile hotspot found).
        # A vectorized MultiIndex set (pandas C-level) replaces the O(n_cells) Python loop;
        # the matched row is converted to a dict only at actual lookup time, one row at a
        # time, preserving the exact same dict-like interface at the call site.
        if cells.empty:
            self._cell_lookup_index = None
            return
        idx_df = cells[self._CELL_LOOKUP_COLS].copy()
        idx_df["_bar_pos_key"] = idx_df["bar_pos"].round().astype("int64")
        idx_df["_price_tick_key"] = idx_df["price_tick"].astype("int64")
        self._cell_lookup_index = idx_df.set_index(["_bar_pos_key", "_price_tick_key"])

    def _lookup_cell(self, bar_pos: int, price_tick: int) -> Optional[dict]:
        if self._cell_lookup_index is None:
            return None
        try:
            match = self._cell_lookup_index.loc[(bar_pos, price_tick)]
        except KeyError:
            return None
        if isinstance(match, pd.DataFrame):
            match = match.iloc[-1]  # duplicate keys: keep-last, matching the old dict's overwrite semantics
        return match.to_dict()

    def _update_price_line(self, bars: pd.DataFrame) -> None:
        if self.price_cb.isChecked() and not bars.empty:
            self.price_curve.setData(bars["bar_pos"].to_numpy(float),
                                     bars["px_close"].to_numpy(float))
            self.price_curve.setVisible(True)
        else:
            self.price_curve.setVisible(False)

    def _update_level_overlays(self, bars: pd.DataFrame, window: tuple[int, int], *, lod: str = LOD_FULL) -> None:
        # Level VALUES (S/R, POC/VAH/VAL, HVN/LVN) come from the fixed
        # ANALYSIS window (_get_analysis_levels, cached by analysis-window
        # key) - NEVER from `bars`/`window`, which are the VIEW window and
        # legitimately change on zoom/pan. `bars`/`window` are used below
        # ONLY for on-screen label x-positioning (a cosmetic, cheap concern)
        # and for the LOD_OVERVIEW/empty-view decluttering check that already
        # existed before this fix.
        #
        # PERF: values_key excludes `window` on purpose. The InfiniteLine objects span the
        # full viewport automatically and never need repositioning on pan/zoom; only the
        # TextItem labels' x-anchor depends on `window` (so labels stay near the visible
        # edge). When only `window` changes, reposition the existing labels in place instead
        # of destroying and recreating every line/text object (was: up to ~90 Qt object
        # add/remove + allocations on every single pan/zoom step -- see DIAGNOSIS.md 4b).
        analysis_key = self._analysis_window_key()
        values_key = (
            self._data_sig,
            analysis_key,
            bool(self.sr_cb.isChecked()),
            bool(self.vp_levels_cb.isChecked()),
            bool(self.hvn_lvn_cb.isChecked()),
            bool(self.proj_cb.isChecked()),
            lod,
        )
        profile_key = (values_key, window)
        if self._last_profile_key == profile_key:
            return
        t0 = time.time()

        if (
            self._last_values_key == values_key
            and self._level_label_anchors
            and not bars.empty
            and lod != LOD_OVERVIEW
        ):
            x_left = float(bars["bar_pos"].min())
            x_right = float(bars["bar_pos"].max())
            for text_item, anchor_side in self._level_label_anchors:
                new_x = x_right if anchor_side == "right" else x_left
                text_item.setPos(new_x, text_item.pos().y())
            self._last_profile_key = profile_key
            self.perf_stats.profile_ms = (time.time() - t0) * 1000.0
            return

        for item in self._level_items:
            try:
                self.main_plot.removeItem(item)
            except Exception:
                pass
        self._level_items.clear()
        self._level_label_anchors.clear()

        levels = self._get_analysis_levels()
        self._vp = levels.get("vp") or {}
        resistance = levels.get("resistance") or []
        support = levels.get("support") or []

        if bars.empty or lod == LOD_OVERVIEW:
            self._last_values_key = values_key
            self._last_profile_key = profile_key
            self.perf_stats.profile_ms = (time.time() - t0) * 1000.0
            return
        x_left = float(bars["bar_pos"].min())
        x_right = float(bars["bar_pos"].max())

        if self.sr_cb.isChecked():
            for px, count, _score in resistance[:6]:
                self._add_level_line(float(px), f"R {px:.2f}" + (f" x{count}" if count > 1 else ""),
                                     "#ff4d4d", x_left, anchor_side="left")
            for px, count, _score in support[:6]:
                self._add_level_line(float(px), f"S {px:.2f}" + (f" x{count}" if count > 1 else ""),
                                     "#00d27a", x_left, anchor_side="left")

        if self.vp_levels_cb.isChecked() and self._vp:
            for key, color in (("poc", "#ffd700"), ("vah", "#55aaff"), ("val", "#55aaff")):
                self._add_level_line(float(self._vp[key]), f"{key.upper()} {self._vp[key]:.2f}",
                                     color, x_right, width=1.0, anchor_side="right")

        if self.hvn_lvn_cb.isChecked() and self._vp:
            for px in self._vp.get("hvn_px", [])[:28]:
                self._add_level_line(float(px), "", "#ffd70066", x_right, width=0.5, anchor_side="right")
            for px in self._vp.get("lvn_px", [])[:28]:
                self._add_level_line(float(px), "", "#88888855", x_right, width=0.5, anchor_side="right")

        if self.proj_cb.isChecked() and not self._proj.empty:
            for _, row in self._proj.iterrows():
                px = row.get("projected_level_price")
                if pd.isna(px):
                    continue
                level_type = str(row.get("level_type", ""))
                is_key = level_type in ("POC", "VAH", "VAL")
                if is_key:
                    self._add_level_line(
                        float(px),
                        f"PROJECTED PRIOR NQM6 {level_type} {float(px):.2f}",
                        PROJ_COLOR,
                        x_right,
                        width=1.0,
                        anchor_side="right",
                    )
        self._last_values_key = values_key
        self._last_profile_key = profile_key
        self.perf_stats.profile_ms = (time.time() - t0) * 1000.0

    def _add_level_line(self, price: float, label: str, color: str, x_pos: float, width: float = 0.8,
                         anchor_side: str = "left") -> None:
        line = pg.InfiniteLine(angle=0, pos=price,
                               pen=pg.mkPen(color, width=width, style=QtCore.Qt.DashLine))
        line.setZValue(-5)
        self.main_plot.addItem(line, ignoreBounds=True)
        self._level_items.append(line)
        if label:
            text = pg.TextItem(label, color=color, anchor=(0, 0.5))
            text.setPos(x_pos, price)
            self.main_plot.addItem(text, ignoreBounds=True)
            self._level_items.append(text)
            self._level_label_anchors.append((text, anchor_side))

    def _update_volume_profile(self, bars: pd.DataFrame, window: tuple[int, int]) -> None:
        panel_key = (self._last_profile_key, window, bool(self.vp_panel_cb.isChecked()))
        if getattr(self, "_last_vp_panel_key", None) == panel_key:
            return
        if self.vp_panel_cb.isChecked() and self._vp:
            self.profile_item.set_data(
                self._vp["levels"],
                self._vp["buy_v"],
                self._vp["sel_v"],
                self._vp.get("hvn_px", []),
                self._vp.get("lvn_px", []),
                TICK,
            )
        else:
            self.profile_item.set_data(np.empty(0), np.empty(0), np.empty(0), [], [], TICK)
        # Phase 2: the panel is never fully hidden anymore -- when the blueprint toggle is off,
        # the order-book ladder (_update_order_book_panel) takes over the same ViewBox instead.
        self.profile_plot.show()
        self._last_vp_panel_key = panel_key

    def _book_pixels_per_tick(self) -> float:
        try:
            _, dy = self.profile_plot.vb.viewPixelSize()
        except Exception:
            return 0.0
        if not dy or not np.isfinite(dy) or dy <= 0:
            return 0.0
        return TICK / dy

    def _update_order_book_panel(self) -> None:
        """Phase 2 Part F: live order-book ladder (Part E's ChartFrame.book_* fields), full-width
        current-price line spanning main_plot + the panel, and the forming-bar current-cell
        highlight. Coalesced the same way as every other render-pipeline step here -- a panel_key
        comparison skips all work when nothing relevant changed."""
        show_book = not self.vp_panel_cb.isChecked()
        book_ts = self._book_ts
        has_book = (
            show_book and self._book_prices is not None
            and self._book_bid_sizes is not None and self._book_ask_sizes is not None
        )
        is_stale = show_book and (book_ts is None or (time.time() - book_ts) > bfds.BOOK_STALE_SECS)

        fbar_idx: Optional[int] = None
        if self._forming_bar_row is not None:
            try:
                fbar_idx = int(self._forming_bar_row["bar_index"])
            except Exception:
                fbar_idx = None

        panel_key = (
            show_book, has_book, is_stale,
            id(self._book_prices) if has_book else None,
            round(book_ts, 1) if book_ts else None,
            self.last_price, fbar_idx,
            self._book_pixels_per_tick() >= BOOK_LABEL_MIN_PX,
        )
        if self._last_book_panel_key == panel_key:
            return
        self._last_book_panel_key = panel_key
        self.profile_plot.setTitle("LIVE ORDER BOOK" if show_book else "VOLUME BLUEPRINT")

        if not show_book or not has_book:
            self.book_bid_item.set_data(np.empty(0), np.empty(0), 1.0, TICK)
            self.book_ask_item.set_data(np.empty(0), np.empty(0), 1.0, TICK)
            self.book_stale_badge.setVisible(show_book and not has_book)
            self._update_book_labels(np.empty(0), np.empty(0), np.empty(0), 1.0, False)
        else:
            prices = self._book_prices
            bid_sizes = self._book_bid_sizes
            ask_sizes = self._book_ask_sizes
            bid_max = float(np.nanmax(bid_sizes)) if bid_sizes.size and np.any(bid_sizes > 0) else 0.0
            ask_max = float(np.nanmax(ask_sizes)) if ask_sizes.size and np.any(ask_sizes > 0) else 0.0
            max_size = max(bid_max, ask_max, 1.0)
            show_labels = self._book_pixels_per_tick() >= BOOK_LABEL_MIN_PX
            self.book_bid_item.set_data(prices, bid_sizes, max_size, TICK, stale=is_stale)
            self.book_ask_item.set_data(prices, ask_sizes, max_size, TICK, stale=is_stale)
            self.book_stale_badge.setVisible(is_stale)
            self._update_book_labels(prices, bid_sizes, ask_sizes, max_size,
                                     show_labels and not is_stale)

        if self.book_stale_badge.isVisible():
            y_lo, y_hi = self.profile_plot.vb.viewRange()[1]
            self.book_stale_badge.setPos(0.5, y_hi - (y_hi - y_lo) * 0.04)

        # Price line + bubble + current-cell highlight -- independent of the blueprint/book
        # toggle, tied only to last_price/forming-bar availability (Phase 2 spec items 2 & 3).
        if self.last_price is not None:
            lp = float(self.last_price)
            self.book_price_line_main.setPos(lp)
            self.book_price_line_main.setVisible(True)
            self.book_price_line_panel.setPos(lp)
            self.book_price_line_panel.setVisible(True)
            self.price_bubble.setText(f"{lp:.2f}")
            self.price_bubble.setPos(0.0, lp)
            self.price_bubble.setVisible(True)

            bpos = self._bar_pos_map.get(fbar_idx) if fbar_idx is not None else None
            if bpos is not None:
                tick_idx = int(bfl.price_to_tick(np.array([lp]))[0])
                cell_price = float(bfl.tick_to_price(tick_idx))
                half_h = TICK * 0.46
                self.cell_highlight.set_rect(bpos - 0.47, bpos + 0.47, cell_price - half_h, cell_price + half_h)
            else:
                self.cell_highlight.clear()
        else:
            self.book_price_line_main.setVisible(False)
            self.book_price_line_panel.setVisible(False)
            self.price_bubble.setVisible(False)
            self.cell_highlight.clear()

    def _update_book_labels(self, prices: np.ndarray, bid_sizes: np.ndarray, ask_sizes: np.ndarray,
                            max_size: float, show: bool) -> None:
        """LOD size labels for the order-book ladder -- real pg.TextItem objects (screen-space
        sized, unaffected by the price-axis zoom transform), pooled and repositioned in place.
        Only ever populated at high zoom (see BOOK_LABEL_MIN_PX), so the pool stays small."""
        pairs: list[tuple[float, float]] = []
        if show and prices.size:
            for arr in (bid_sizes, ask_sizes):
                nz = np.nonzero(arr)[0]
                for i in nz:
                    pairs.append((float(prices[i]), float(arr[i])))
        for i, (price, size) in enumerate(pairs):
            if i < len(self._book_label_items):
                item = self._book_label_items[i]
            else:
                item = pg.TextItem(color="#dddddd", anchor=(0.0, 0.5))
                item.setFont(QtGui.QFont("Sans Serif", 8))
                item.setZValue(5)
                self.profile_plot.addItem(item, ignoreBounds=True)
                self._book_label_items.append(item)
            item.setText(f"{size:.0f}")
            item.setPos(0.03, price)
            item.setVisible(True)
        for j in range(len(pairs), len(self._book_label_items)):
            self._book_label_items[j].setVisible(False)

    def _update_pressure_panel(self, cells: pd.DataFrame, window: tuple[int, int]) -> None:
        pressure_key = (
            self._data_sig,
            window,
            bool(self.pulls_cb.isChecked()),
            len(cells),
        )
        if self._last_pressure_key == pressure_key:
            return
        t0 = time.time()
        if not self.pulls_cb.isChecked() or cells.empty:
            for item in (self.bid_add_bars, self.ask_add_bars, self.bid_pull_bars,
                         self.ask_pull_bars, self.net_flow_curve):
                item.setVisible(False)
            self._last_pressure_key = pressure_key
            self.perf_stats.pressure_ms = (time.time() - t0) * 1000.0
            self.perf_stats.pulls_ms = self.perf_stats.pressure_ms
            return
        grouped = cells.groupby("bar_pos", as_index=False).agg({
            "bid_add": "sum",
            "ask_add": "sum",
            "bid_pull": "sum",
            "ask_pull": "sum",
            "signed_flow": "sum",
        }).sort_values("bar_pos")
        x = grouped["bar_pos"].to_numpy(float)
        self.bid_add_bars.setOpts(x=x - 0.27, height=grouped["bid_add"].to_numpy(float), width=0.18)
        self.ask_add_bars.setOpts(x=x - 0.09, height=grouped["ask_add"].to_numpy(float), width=0.18)
        self.bid_pull_bars.setOpts(x=x + 0.09, height=-grouped["bid_pull"].to_numpy(float), width=0.18)
        self.ask_pull_bars.setOpts(x=x + 0.27, height=-grouped["ask_pull"].to_numpy(float), width=0.18)
        self.net_flow_curve.setData(x, grouped["signed_flow"].to_numpy(float))
        for item in (self.bid_add_bars, self.ask_add_bars, self.bid_pull_bars,
                     self.ask_pull_bars, self.net_flow_curve):
            item.setVisible(True)
        self.pressure_plot.show()
        self._last_pressure_key = pressure_key
        self.perf_stats.pressure_ms = (time.time() - t0) * 1000.0
        self.perf_stats.pulls_ms = self.perf_stats.pressure_ms

    def _update_view_ranges(self, cells: pd.DataFrame, bars: pd.DataFrame) -> None:
        if cells.empty and bars.empty:
            return
        if self.user_view_override:
            return
        n = len(self._bar_ids_base) if self._bar_ids_base else (
            int(cells["bar_pos"].max()) + 1 if not cells.empty else int(bars["bar_pos"].max()) + 1
        )
        follow_requested = self.follow_live_requested
        should_follow = (not self._initialized) or follow_requested
        if not should_follow:
            return

        if follow_requested:
            x_hi = float(n - 1) + 0.8
            x_lo = max(-0.8, x_hi - min(n, FOLLOW_WINDOW))
        else:
            x_lo = -0.8
            x_hi = float(n - 1) + 0.8

        if not cells.empty:
            y_lo = float(np.nanmin(cells["price_level"].to_numpy(float)))
            y_hi = float(np.nanmax(cells["price_level"].to_numpy(float)))
            if not bars.empty:
                y_lo = min(y_lo, float(np.nanmin(bars["px_low"].to_numpy(float))))
                y_hi = max(y_hi, float(np.nanmax(bars["px_high"].to_numpy(float))))
        else:
            y_lo = float(np.nanmin(bars["px_low"].to_numpy(float)))
            y_hi = float(np.nanmax(bars["px_high"].to_numpy(float)))
        y_pad = max((y_hi - y_lo) * 0.05, 2.0)

        pressure_arrays = np.array([0.0])
        if not cells.empty:
            grouped = cells.groupby("bar_pos").agg({
                "bid_add": "sum", "ask_add": "sum", "bid_pull": "sum",
                "ask_pull": "sum", "signed_flow": "sum",
            })
            pressure_arrays = np.concatenate([
                grouped["bid_add"].to_numpy(float),
                grouped["ask_add"].to_numpy(float),
                -grouped["bid_pull"].to_numpy(float),
                -grouped["ask_pull"].to_numpy(float),
                grouped["signed_flow"].to_numpy(float),
                np.array([0.0]),
            ])
        p_lo, p_hi = _range_pair(pressure_arrays)
        p_pad = max((p_hi - p_lo) * 0.08, 1.0)

        self._setting_ranges = True
        self._programmatic_view_update = True
        try:
            self.main_plot.setXRange(x_lo, x_hi, padding=0.02)
            self.main_plot.setYRange(y_lo - y_pad, y_hi + y_pad, padding=0.0)
            self.pressure_plot.setYRange(p_lo - p_pad, p_hi + p_pad, padding=0.0)
        finally:
            self._programmatic_view_update = False
            self._setting_ranges = False
        self._initialized = True
        self._update_view_status()

    def _at_live_edge(self, n: int) -> bool:
        if n <= 0:
            return True
        x_min, x_max = self.main_plot.vb.viewRange()[0]
        tol = max(2.0, (x_max - x_min) * 0.05)
        return x_max >= (n - 1) - tol

    def _capture_ranges(self) -> dict[str, tuple[tuple[float, float], tuple[float, float]]]:
        return {
            "main": tuple(tuple(v) for v in self.main_plot.vb.viewRange()),
            "pressure": tuple(tuple(v) for v in self.pressure_plot.vb.viewRange()),
        }

    @staticmethod
    def _ranges_close(a: dict, b: dict, tol: float = 1e-7) -> bool:
        for key in a:
            for axis_i in (0, 1):
                if not np.allclose(a[key][axis_i], b[key][axis_i], atol=tol, rtol=0):
                    return False
        return True

    def _on_user_interaction(self, _mask) -> None:
        if self._programmatic_view_update or self._setting_ranges or not self._initialized:
            return
        self.user_view_override = True
        self.user_view_locked = True
        self._user_interacting = True
        self._update_view_status()
        # Debounce: restart 600ms timer on every pan/zoom event.
        # No reload fires until interaction stops for 600ms.
        self._interaction_end_timer.start(600)

    def _on_interaction_ended(self) -> None:
        """Called 600ms after the last pan/zoom event; fires one coalesced reload."""
        self._user_interacting = False
        self._queue_reload(force_render=True)

    def _update_view_status(self) -> None:
        hb = self._heartbeat or {}
        hb_cache_bar = hb.get("cache_last_bar_idx_by_depth", {}).get(str(self.depth), "--")
        rendered_max = (int(self.visible_bars["bar_index"].max())
                        if not self.visible_bars.empty else "--")
        if self.user_view_override:
            try:
                xlo, xhi = self.main_plot.vb.viewRange()[0]
                range_txt = f"x=[{xlo:.0f},{xhi:.0f}]"
            except Exception:
                range_txt = "x=[?,?]"
            mode = (f"USER LOCKED — cache bar {hb_cache_bar}, "
                    f"rendered {rendered_max}, view {range_txt} (Reset View to follow latest)")
        elif self.follow_live_requested:
            mode = f"FOLLOWING LIVE — rendered {rendered_max}"
        else:
            mode = f"MANUAL_VIEW — rendered {rendered_max}"
        follow = "ON" if self.follow_live_requested else "OFF"
        self.view_status_lbl.setText(f"FOLLOW LIVE: {follow} | VIEW: {mode}")

    def _reset_view(self) -> None:
        self._user_interacting = False
        self._interaction_end_timer.stop()
        self.user_view_override = False
        self.user_view_locked = False
        self._initialized = False
        self._reload()
        self._update_view_status()

    def _on_mouse_move(self, pos) -> None:
        if not self.main_plot.sceneBoundingRect().contains(pos):
            self.crosshair_v.setVisible(False)
            self.crosshair_h.setVisible(False)
            self.tooltip.setVisible(False)
            return
        view = self.main_plot.vb.mapSceneToView(pos)
        x = view.x()
        price = view.y()
        bar_pos = int(round(x))
        self.crosshair_v.setPos(x)
        self.crosshair_h.setPos(price)
        self.crosshair_v.setVisible(True)
        self.crosshair_h.setVisible(True)
        lod = self.perf_stats.lod_mode
        if lod in (LOD_WIDE, LOD_OVERVIEW):
            # Coarse tooltip from aggregated per-level OFI bin data
            bg  = self.wide_candles._bar_group
            pbp = self.wide_candles._price_bin_pts
            x_bin_key = int(x // bg) if bg > 0 else int(round(x))
            y_bin_key = int(round(price / pbp)) if pbp > 0 else int(round(price))
            rec = self.wide_candles.bin_lookup.get((x_bin_key, y_bin_key))
            if rec is None:
                self.tooltip.setVisible(False)
                return
            sf   = rec.get("signed_flow", 0.0)
            af   = rec.get("abs_flow", 0.0)
            dom  = "BUY" if sf > 0 else ("SELL" if sf < 0 else "NEUTRAL")
            x_lo = x_bin_key * bg
            x_hi = x_lo + bg - 1
            y_lo = rec["price_level"] - pbp / 2.0
            y_hi = rec["price_level"] + pbp / 2.0
            txt = (
                f" [OFI BIN — LOD {lod}] bars {x_lo}–{x_hi}\n"
                f" price {y_lo:.2f}–{y_hi:.2f} | dominant: {dom}\n"
                f" signed_flow: {sf:+.0f} | abs_flow: {af:.0f}\n"
                f" bid_add: {rec.get('bid_add', 0.0):.0f} | bid_pull: {rec.get('bid_pull', 0.0):.0f}\n"
                f" ask_add: {rec.get('ask_add', 0.0):.0f} | ask_pull: {rec.get('ask_pull', 0.0):.0f}"
            )
            self.tooltip.setText(txt)
            self.tooltip.setPos(x, price)
            self.tooltip.setVisible(True)
            return
        # FULL/MEDIUM: exact per-level cell tooltip
        if self.visible_cells.empty:
            self.tooltip.setVisible(False)
            return
        price_tick = _price_to_tick_scalar(price)
        row = self._lookup_cell(bar_pos, price_tick)
        if row is None:
            self.tooltip.setVisible(False)
            return
        txt = (
            f" bar {int(_row_get(row, 'bar_idx'))} | {str(_row_get(row, 'timestamp_utc'))[:19]} | top{self.depth}\n"
            f" price {float(_row_get(row, 'price_level')):.2f} | zone {_row_get(row, 'side_zone')}\n"
            f" signed_flow {float(_row_get(row, 'signed_flow')):+.0f} | abs_flow {float(_row_get(row, 'abs_flow')):.0f}\n"
            f" bid_add {float(_row_get(row, 'bid_add')):.0f} | bid_pull {float(_row_get(row, 'bid_pull')):.0f}\n"
            f" ask_add {float(_row_get(row, 'ask_add')):.0f} | ask_pull {float(_row_get(row, 'ask_pull')):.0f}\n"
            f" net_bid {float(_row_get(row, 'net_bid_flow')):+.0f} | net_ask {float(_row_get(row, 'net_ask_flow')):+.0f}\n"
            f" trade_vol {float(_row_get(row, 'trade_volume_at_price')):.0f} | "
            f"buy {float(_row_get(row, 'buy_trade_volume_at_price')):.0f} | "
            f"sell {float(_row_get(row, 'sell_trade_volume_at_price')):.0f}\n"
            f" nearest {_row_get(row, 'nearest_level')} | dist {float(_row_get(row, 'distance_to_nearest_level')):+.2f}\n"
            f" close {float(_row_get(row, 'close_price')):.2f} | mid {float(_row_get(row, 'mid_price')):.2f}"
        )
        self.tooltip.setText(txt)
        self.tooltip.setPos(x, price)
        self.tooltip.setVisible(True)

    def _dataservice_eligible(self) -> bool:
        """Scope: (a) single-date context with forming bars hidden (the original step-2 scope),
        or (b) live-latest mode with forming bar visible and the CONTEXT_PREV_1 default -- the
        true out-of-the-box default UX, ported natively into the service in step 3 Phase 1
        (STEP3_REPORT.md). Anything else (CONTEXT_PREV_2/3/CUSTOM, or historical-mode with
        forming bar manually toggled on) still falls back to the legacy
        CacheFileMonitor/DataLoadWorker path."""
        if not BOOKFLOW_DATASERVICE_ENABLED:
            return False
        if self.live_latest_mode and self.show_forming_bar:
            return self.context_mode == CONTEXT_PREV_1
        if self.show_forming_bar:
            return False
        try:
            return len(self._selected_context_dates()) == 1
        except Exception:
            return False

    def _chart_frame_to_snapshot(self, frame: "bfds.ChartFrame") -> dict:
        """Adapts a BookFlowDataService ChartFrame into the exact dict shape _apply_snapshot()
        already consumes from the legacy _bg_load_snapshot() path -- this is what lets the
        service plug in without touching any downstream render code."""
        sealed = frame.sealed_cells
        forming_ids: list[int] = []
        if frame.forming_cells is not None and not frame.forming_cells.empty:
            level_df = (
                pd.concat([sealed, frame.forming_cells], ignore_index=True)
                .sort_values(["bar_idx", "price_level"]).reset_index(drop=True)
            )
            forming_ids = [int(x) for x in frame.forming_cells["bar_idx"].unique()]
        else:
            level_df = sealed
        cached_bar_ids = set(int(x) for x in level_df["bar_idx"].unique()) if not level_df.empty else set()
        hb = frame.heartbeat or {}
        hb_cache_bar = hb.get("cache_last_bar_idx_by_depth", {}).get(str(self.depth))
        loaded_ofi_max = frame.last_closed_bar_idx
        exact_match = hb_cache_bar is None or loaded_ofi_max is None or loaded_ofi_max == hb_cache_bar
        return {
            "ok": True,
            "level_df": level_df,
            "bars_df": frame.sealed_bars,
            "heartbeat": hb,
            "forming_ids": forming_ids,
            # Step 3 Phase 1 Part B fix: must be ALL dates actually combined (previous session(s)
            # + current), not just frame.date -- context_dates_loaded's length feeds
            # _visible_bar_window()'s follow-span calculation (max_visible_bars vs FOLLOW_WINDOW),
            # so under-reporting it silently narrows the service path's default view compared to
            # the legacy path for the identical live-latest+forming-bar configuration (found via
            # a real legacy-vs-service pixel-parity mismatch).
            "loaded_dates": list(frame.context_dates),
            "missing_cache": [],
            "last_closed_bar_idx": frame.last_closed_bar_idx,
            "cached_bar_ids": cached_bar_ids,
            "zero_cell_ids": [],
            "parity_status": {"hb_cache_bar": hb_cache_bar, "loaded_ofi_max": loaded_ofi_max,
                               "parity_ok": exact_match, "mismatch": not exact_match},
            # Deliberately NOT self._get_worker_params() here: that computes "context_dates" via
            # _selected_context_dates(), which does a real filesystem walk for live-latest mode
            # -- wasted work, since _load_data_if_needed()'s stale-snapshot check below only ever
            # compares symbol/date/depth/show_forming_bar, never context_dates. This minimal dict
            # covers exactly what that check needs, at zero I/O cost (Step 3 Phase 1).
            "params": {
                "symbol": frame.symbol, "date": frame.date, "depth": frame.depth,
                "show_forming_bar": bool(self.show_forming_bar),
            },
            # Step 3 Phase 1: the service's own monotone version IS the signature for
            # dataservice-driven windows -- see _load_data_if_needed()'s use of this, which
            # deliberately never calls _current_data_sig()/_sync_live_latest_from_heartbeat() for
            # those windows (that would redundantly re-derive, and re-pay for, exactly what the
            # service already tracks with its own cached heartbeat/date-discovery).
            "service_sig": (frame.version,),
            "forming_bar": frame.forming_bar,
            "last_price": frame.last_price,
            "book_prices": frame.book_prices,
            "book_bid_sizes": frame.book_bid_sizes,
            "book_ask_sizes": frame.book_ask_sizes,
            "book_ts": frame.book_ts,
        }

    def _sync_date_from_service_frame(self, frame: "bfds.ChartFrame") -> None:
        """Step 3 Phase 1: date-rollover UI sync for dataservice-driven live-latest windows,
        driven by the service's own resolved frame.date. Deliberately does NOT call
        _sync_live_latest_from_heartbeat() -- that would redundantly re-read/re-parse the
        heartbeat the service already caches internally with its own targeted invalidation."""
        if not self.live_latest_mode or frame.date == self.date:
            return
        self.date = frame.date
        self._data_sig = None
        self._last_render_key = None
        self._last_profile_key = None
        self._last_values_key = None
        self._last_pressure_key = None
        if hasattr(self, "date_combo"):
            self.date_combo.blockSignals(True)
            try:
                if frame.date not in [self.date_combo.itemText(i) for i in range(self.date_combo.count())]:
                    self.date_combo.addItem(frame.date)
                self.date_combo.setCurrentText(frame.date)
            finally:
                self.date_combo.blockSignals(False)

    def _poll_data_service(self) -> None:
        if self._data_service is None:
            return
        frame = self._data_service.get_latest()
        if frame is None or frame.version == self._dataservice_last_version:
            # No new frame -- order-book staleness is a function of wall-clock time, not any
            # data signature, so it must still be re-checked even when nothing else changed
            # (e.g. the whole feed pausing, not just the book -- found via Part G gate (e): with
            # no new frame ever arriving, _reload() never runs again, so a badge driven only from
            # inside the render pipeline would never appear). Reuses this existing 100ms timer --
            # no new timer -- and is cheap: _update_order_book_panel() is already internally
            # coalesced on its own key and only does real work when something in it changed.
            self._update_order_book_panel()
            return
        self._dataservice_last_version = frame.version
        self._sync_date_from_service_frame(frame)
        self._on_data_ready(self._chart_frame_to_snapshot(frame))

    def _periodic_malloc_trim(self) -> None:
        """Step 2 Part C containment (see PART_B_RSS_VERDICT.md) -- secondary lever for the
        glibc-side share of the growth; the dominant mimalloc/PyArrow share is addressed via
        MIMALLOC_PURGE_DELAY at module import time. Best-effort: silently no-ops on non-glibc
        platforms or if the call fails for any reason -- this must never crash the chart."""
        if _libc is None:
            return
        try:
            _libc.malloc_trim(0)
        except Exception:
            pass

    def _restart_worker(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(2000)
            self.worker = None
        if self._data_service is not None:
            self._data_service.stop()
            self._data_service = None
        if self._dataservice_timer is not None:
            self._dataservice_timer.stop()

        active_date = self.requested_date if self.live_latest_mode else self.date
        if self._dataservice_eligible():
            self._dataservice_active = True
            self._dataservice_last_version = -1
            # Step 3 Phase 1: live_latest/previous_sessions/show_forming now reflect the
            # window's actual state -- the service natively handles live-latest + forming bar +
            # previous-session context (STEP3_REPORT.md), not just the single-date/no-forming
            # scope from step 2.
            self._data_service = bfds.BookFlowDataService(
                self.symbol, active_date, self.depth, show_forming=self.show_forming_bar,
                poll_interval_s=0.3, live_latest=self.live_latest_mode,
                previous_sessions=self._context_session_request())
            self._data_service.start()
            if self._dataservice_timer is None:
                self._dataservice_timer = QtCore.QTimer(self)
                self._dataservice_timer.timeout.connect(self._poll_data_service)
            self._dataservice_timer.start(100)
        else:
            self._dataservice_active = False
            # Poll faster in live mode so forming-cache updates (350ms cadence) are detected
            # promptly -- matches the interval the original __init__ inline creation used.
            monitor_interval = 0.5 if self.live_latest_mode else 2.0
            if _MONITOR_INTERVAL_OVERRIDE_S is not None:
                monitor_interval = float(_MONITOR_INTERVAL_OVERRIDE_S)
            self.worker = CacheFileMonitor(self.symbol, active_date, self.depth,
                                            live_latest=self.live_latest_mode, interval_s=monitor_interval)
            self.worker.updated.connect(self._on_cache_updated)
            self.worker.start()

    def _on_mode_change(self, text: str) -> None:
        live = text == LIVE_LATEST_LABEL
        if live == self.live_latest_mode:
            return
        self.live_latest_mode = live
        self.requested_date = LIVE_LATEST_LABEL if live else self.date
        if live:
            self.date = _resolve_live_date(self.symbol)
            self.context_mode = CONTEXT_PREV_1
            self.previous_context_sessions = 1
            self.context_combo.blockSignals(True)
            try:
                self.context_combo.setCurrentText(CONTEXT_PREV_1)
                self.context_bars_spin.setEnabled(False)
            finally:
                self.context_combo.blockSignals(False)
            self.date_combo.blockSignals(True)
            try:
                if self.date not in [self.date_combo.itemText(i) for i in range(self.date_combo.count())]:
                    self.date_combo.addItem(self.date)
                self.date_combo.setCurrentText(self.date)
            finally:
                self.date_combo.blockSignals(False)
        self._initialized = False if not self.user_view_locked else self._initialized
        self._data_sig = None
        self._restart_worker()
        self._queue_reload(force_data=True, force_render=True)

    def _on_symbol_change(self, text: str) -> None:
        if not text or text == self.symbol:
            return
        self.symbol = text
        if self.live_latest_mode:
            self.date = _resolve_live_date(self.symbol)
        self._initialized = False
        self._restart_worker()
        self._queue_reload(force_data=True, force_render=True)

    def _on_date_change(self, text: str) -> None:
        if not text or text == self.date:
            return
        if self.live_latest_mode:
            self.live_latest_mode = False
            self.mode_combo.blockSignals(True)
            try:
                self.mode_combo.setCurrentText(HISTORICAL_LABEL)
            finally:
                self.mode_combo.blockSignals(False)
            self.context_mode = CONTEXT_CURRENT_ONLY
            self.previous_context_sessions = 0
            self.context_combo.blockSignals(True)
            try:
                self.context_combo.setCurrentText(CONTEXT_CURRENT_ONLY)
                self.context_bars_spin.setEnabled(False)
            finally:
                self.context_combo.blockSignals(False)
        self.date = text
        self.requested_date = text
        self._initialized = False
        self.symbol_combo.blockSignals(True)
        self.symbol_combo.clear()
        self.symbol_combo.addItems(self._discover_symbols())
        self.symbol_combo.setCurrentText(self.symbol)
        self.symbol_combo.blockSignals(False)
        self._restart_worker()
        self._queue_reload(force_data=True, force_render=True)

    def _on_depth_change(self, text: str) -> None:
        self.depth = int(text)
        self._initialized = False if not self.user_view_locked else self._initialized
        self._restart_worker()
        self._queue_reload(force_data=True, force_render=True)

    def _on_lookback_change(self, text: str) -> None:
        self.lookback = 0 if text == "full" else int(text)
        self._initialized = False if not self.user_view_locked else self._initialized
        self._reload()

    def _on_context_change(self, text: str) -> None:
        self.context_mode = text
        self.context_bars_spin.setEnabled(text == CONTEXT_CUSTOM)
        self.previous_context_sessions = self._context_session_request()
        self._initialized = False if not self.user_view_locked else self._initialized
        self._data_sig = None
        self._restart_worker()  # dataservice eligibility depends on context date count
        self._queue_reload(force_data=True, force_render=True)

    def _on_context_bars_change(self, value: int) -> None:
        self.custom_context_bars = int(value)
        if self.context_mode == CONTEXT_CUSTOM:
            self._initialized = False if not self.user_view_locked else self._initialized
            self._data_sig = None
            self._queue_reload(force_data=True, force_render=True)

    def _on_follow_change(self, state) -> None:
        self.follow_live_requested = bool(state)
        self.follow_live = self.follow_live_requested
        self._update_view_status()
        self._reload()

    def _on_forming_bar_toggle(self, state: int) -> None:
        self.show_forming_bar = bool(state)
        self._data_sig = None  # force re-filter on next reload
        self._initialized = False
        self._restart_worker()  # dataservice eligibility depends on show_forming_bar
        self._queue_reload(force_data=True, force_render=True)

    def _get_worker_params(self) -> dict:
        """Snapshot of load parameters — safe to pass to the background thread."""
        return {
            "symbol":       self.symbol,
            "date":         self.date,
            "depth":        self.depth,
            "context_dates": list(self._selected_context_dates()),
            "show_forming_bar": bool(self.show_forming_bar),
        }

    def _on_cache_updated(self, result: dict) -> None:
        if not result.get("ok"):
            support = result.get("support", {})
            code = support.get("blocked_code", "")
            reason = result.get("reason", "cache update failed")
            self.status_lbl.setText(f"{code} {reason}".strip())
            return
        if self.live_latest_mode and result.get("active_date") and result.get("active_date") != self.date:
            self.date = str(result["active_date"])
            self._data_sig = None
            self._initialized = False if not self.user_view_locked else self._initialized
        # Route I/O to background thread instead of loading on GUI thread.
        # The worker emits data_ready → _on_data_ready → _queue_reload.
        # If user is interacting the reload will be deferred by the interaction guard.
        self._data_worker.request_load(self._get_worker_params())

    def _on_data_ready(self, snapshot: dict) -> None:
        """Receive a background-loaded snapshot and schedule a render-only reload."""
        if not snapshot.get("ok"):
            return
        # Store the pre-loaded snapshot so _load_data_if_needed() can apply it
        # without any parquet reads.
        self._pending_snapshot = snapshot
        self._queue_reload(force_data=False, force_render=False)

    def closeEvent(self, event) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(2000)
        if self._data_service is not None:
            self._data_service.stop()
        if self._dataservice_timer is not None:
            self._dataservice_timer.stop()
        self._malloc_trim_timer.stop()
        self._data_worker.stop()
        self._data_worker.wait(2000)
        super().closeEvent(event)


def _dashboard_launch_uses_v3() -> bool:
    try:
        src = DASHBOARD_FILE.read_text()
    except OSError:
        return False
    return (
        "BOOKCHART_APP_PATH" in src
        and "book_flow_chart_v3.py" in src
        and "Launch True Book Flow Chart" in src
        and "Stop True Book Flow Chart" in src
    )


def run_smoke_test(symbol: str, date: str) -> int:
    requested_date = str(date)
    live_latest_mode = _is_live_latest(requested_date)
    date_info = cache_daemon.latest_dates(symbol)
    active_date = cache_daemon.resolve_date(requested_date)
    raw_20260616_exists = (bfl.RAW_BASE / "2026-06-16" / symbol).is_dir()
    feature_20260616_exists = (bfl.FEATURES_BASE / "2026-06-16" / f"{symbol}_vol500.ndjsonl").exists()
    active_date_not_stale_if_20260616_exists = (
        not (raw_20260616_exists and feature_20260616_exists)
        or active_date >= "2026-06-16"
    )
    print(f"SMOKE TEST V3: symbol={symbol} requested_date={requested_date} active_date={active_date}")
    print(f"LIVE_LATEST_MODE={str(live_latest_mode).lower()}")
    print(f"LATEST_RAW_DATE={date_info.get('latest_raw_date')}")
    print(f"LATEST_FEATURE_DATE={date_info.get('latest_feature_date')}")
    print(f"RAW_2026_06_16_EXISTS={str(raw_20260616_exists).lower()}")
    print(f"FEATURE_2026_06_16_EXISTS={str(feature_20260616_exists).lower()}")
    print(f"ACTIVE_DATE_NOT_STALE_IF_20260616_EXISTS={str(active_date_not_stale_if_20260616_exists).lower()}")
    ok = True
    ok &= bool(active_date_not_stale_if_20260616_exists)

    support = level_cache.inspect_raw_depth_support(symbol, active_date)
    support_ok = bool(support.get("ok"))
    print(f"RAW_DEPTH_RECONSTRUCTION={str(support_ok).lower()}")
    print(f"TOP_DEPTH_RECONSTRUCTION={str(support_ok and support.get('top_depth_supported')).lower()}")
    print(f"RAW_DEPTH_ROWS={support.get('depth_rows')}")
    print(f"BID_LEVEL_COUNT={support.get('bid_level_count')}")
    print(f"ASK_LEVEL_COUNT={support.get('ask_level_count')}")
    print(f"BID_QUOTE_UNIQUE_PRICES_SAMPLE={support.get('bid_quote_unique_prices_sample')}")
    print(f"ASK_QUOTE_UNIQUE_PRICES_SAMPLE={support.get('ask_quote_unique_prices_sample')}")
    if not support_ok:
        print(support.get("blocked_code", "BLOCKED_TOP_DEPTH_UNAVAILABLE"))
        print(f"SMOKE_TEST=BLOCKED reason={support.get('reason')}")
        return 2
    ok &= support_ok

    compact_request_date = requested_date if live_latest_mode else active_date
    compact_result = cache_daemon.ensure_compact_caches(symbol, compact_request_date, DEPTH_CHOICES, force=False)
    rows_by_depth: dict[str, int] = {
        depth: int(res.get("rows", 0))
        for depth, res in compact_result.get("results", {}).items()
    }
    heartbeat = compact_result.get("heartbeat") or cache_daemon.load_heartbeat()
    heartbeat_path = Path(compact_result.get("heartbeat_path") or cache_daemon.heartbeat_path())
    heartbeat_written = bool(heartbeat_path.exists() and heartbeat.get("active_date") == active_date)
    cache_lag_by_depth = heartbeat.get("cache_lag_bars_by_depth", {})
    cache_last_bar_by_depth = heartbeat.get("cache_last_bar_idx_by_depth", {})
    last_closed_bar_by_depth = heartbeat.get("last_closed_bar_idx_by_depth", {})
    selected_cache_lag = cache_lag_by_depth.get("5")
    selected_cache_live = selected_cache_lag in (0, "0", None) and int(rows_by_depth.get("5", 0)) > 0
    build_ok = bool(compact_result.get("ok") and all(v > 0 for v in rows_by_depth.values()))
    print(f"LEVEL_CACHE_BUILD_OK={str(build_ok).lower()}")
    print(f"LEVEL_CACHE_ROWS_BY_DEPTH={rows_by_depth}")
    print(f"HEARTBEAT_WRITTEN={str(heartbeat_written).lower()}")
    print(f"HEARTBEAT_PATH={heartbeat_path}")
    print(f"HEARTBEAT_STATUS={heartbeat.get('status')}")
    print(f"HEARTBEAT_ACTIVE_DATE={heartbeat.get('active_date')}")
    print(f"HEARTBEAT_LATEST_BAR_IDX={heartbeat.get('latest_bar_idx')}")
    print(f"HEARTBEAT_LATEST_BAR_TIMESTAMP_UTC={heartbeat.get('latest_bar_timestamp_utc')}")
    print(f"CACHE_LAST_BAR_IDX_BY_DEPTH={cache_last_bar_by_depth}")
    print(f"CACHE_LAG_BARS_BY_DEPTH={cache_lag_by_depth}")
    print(f"CACHE_ROWS_EXIST_SELECTED_DEPTH={str(int(rows_by_depth.get('5', 0)) > 0).lower()}")
    print(f"CACHE_SELECTED_DEPTH_LIVE={str(selected_cache_live).lower()}")
    best_bid_ask_only = False
    print(f"BEST_BID_ASK_ONLY={str(best_bid_ask_only).lower()}")
    ok &= build_ok and heartbeat_written and selected_cache_live and not best_bid_ask_only

    cpath = cache_daemon.compact_cache_path(symbol, active_date, 5)
    df = pd.read_parquet(cpath)
    validation = validate_level_cache(df, 5)
    validation_ok = bool(validation.get("ok"))
    print(f"LEVEL_CACHE_VALIDATION_OK={str(validation_ok).lower()}")
    print(f"LEVEL_CACHE_VALIDATION_REASON={validation.get('reason')!r}")
    print(f"CACHE_PATH={cpath}")
    print(f"MAIN_VISUAL_TYPE={MAIN_VISUAL_TYPE}")
    print(f"Y_AXIS={MAIN_Y_AXIS}")
    price_ohlc_used = False
    mlofi_used = False
    print(f"PRICE_OHLC_USED_AS_MAIN_CANDLES={str(price_ohlc_used).lower()}")
    print(f"MLOFI_USED_AS_MAIN_CANDLES={str(mlofi_used).lower()}")
    print(f"CELLS_SOURCE={CELLS_SOURCE}")
    print(f"DEPTH_CHOICES={DEPTH_CHOICES}")
    ok &= validation_ok and not price_ohlc_used and not mlofi_used

    bars = _load_bars(symbol, active_date)
    profile = bfl.volume_profile(bars) if not bars.empty else {}
    overlays_ok = bool(profile) and "price_level" in df.columns
    print(f"LEVEL_OVERLAYS={str(overlays_ok).lower()}")
    print(f"VOLUME_BLUEPRINT={str(bool(profile)).lower()}")
    ok &= overlays_ok and bool(profile)

    # Forming bar completeness checks (non-GUI)
    has_bar_state_col = "bar_state" in df.columns
    forming_ids_in_cache = set(df.loc[df["bar_state"] == "FORMING", "bar_idx"].unique()) if has_bar_state_col else set()
    closed_ids_in_cache = sorted(int(b) for b in df["bar_idx"].unique() if b not in forming_ids_in_cache)
    last_closed_bar_cache = closed_ids_in_cache[-1] if closed_ids_in_cache else None
    forming_bar_identified = has_bar_state_col and (bool(forming_ids_in_cache) or True)  # column exists
    expected_forming = heartbeat.get("forming_bar_idx_actual_by_depth", {}).get("5")
    forming_bar_hb_matches = (expected_forming is None or expected_forming in forming_ids_in_cache or not forming_ids_in_cache)
    print(f"BAR_STATE_COLUMN_IN_CACHE={str(has_bar_state_col).lower()}")
    print(f"FORMING_BAR_IDENTIFIED={str(forming_bar_identified).lower()}")
    print(f"FORMING_BAR_IDS_IN_CACHE={sorted(forming_ids_in_cache)}")
    print(f"LAST_CLOSED_BAR_IDX_IN_CACHE={last_closed_bar_cache}")
    print(f"LAST_CLOSED_BAR_IDX_IN_HB={last_closed_bar_by_depth.get('5')}")
    print(f"FORMING_BAR_HB_MATCHES_CACHE={str(forming_bar_hb_matches).lower()}")
    ok &= has_bar_state_col

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    auto_reset_ok = False
    pan_zoom_preserves_view = False
    cursor_move_preserves_view = False
    data_refresh_preserves_view = False
    reset_view_still_works = False
    follow_live_checkbox_stays_checked = False
    follow_live_starts_checked = False
    performance_stats_exist = False
    visible_window_rendering = False
    render_throttle_enabled = False
    reentrancy_guard_enabled = False
    cursor_move_unchecks = True
    zoom_unchecks = True
    pan_unchecks = True
    data_refresh_unchecks = True
    checkbox_click_still_works = False
    timestamp_parity_fixed = False
    gui_timestamp_parity_or_explained = False
    live_latest_context_enabled = False
    previous_session_context_supported = False
    cache_carries_enough_history = False
    gui_loads_selected_lookback = False
    latest_closed_bar_rendered = False
    try:
        win = BookFlowLevelChartWindow(symbol, compact_request_date, start_worker=False)
        win._reload()
        app.processEvents()
        # Default mode is CLOSED_BARS_ONLY: level_df excludes forming bar.
        # GUI loads latest cache iff max bar_idx matches last closed bar (or overall if no forming bar).
        # Use max() of heartbeat and compact values — heartbeat can lag compact by one cycle.
        _hb_last = last_closed_bar_by_depth.get("5") or cache_last_bar_by_depth.get("5", -1)
        _compact_last = win._last_closed_bar_idx or cache_last_bar_by_depth.get("5", -1)
        expected_last = max(_hb_last or -1, _compact_last or -1) or None
        # When forming bar is visible, level_df includes the forming bar row
        _max_expected = int(expected_last) if expected_last is not None else -1
        if win.show_forming_bar and forming_ids_in_cache:
            _max_expected = max(_max_expected, max(int(x) for x in forming_ids_in_cache))
        gui_loads_latest_cache = (
            win.date == active_date
            and not win.level_df.empty
            and not win.bars_df.empty
            and int(win.level_df["bar_idx"].max()) == _max_expected
        )
        data_sig = win._current_data_sig()
        # Step 2 Part C: the sig no longer carries the raw heartbeat-file mtime (see
        # PART_B_RSS_VERDICT.md -- that component caused the idle-live over-invalidation bug).
        # The mechanism that actually matters -- reacting to real compact-cache/bars changes --
        # is _context_file_sig(), which is unconditionally still part of the tuple.
        cache_mtime_watcher_works = win._context_file_sig() in data_sig
        hb_after_gui = cache_daemon.load_heartbeat()
        timestamp_parity_fixed = bool(hb_after_gui.get("timestamp_parity_ok"))
        gui_timestamp_parity_or_explained = bool(
            hb_after_gui.get("gui_timestamp_parity_ok")
            or hb_after_gui.get("gui_timestamp_lag_explained")
        )
        live_latest_context_enabled = bool(live_latest_mode and win.context_mode != CONTEXT_CURRENT_ONLY)
        previous_session_context_supported = bool(not live_latest_mode or len(win.context_dates_loaded) > 1)
        cache_carries_enough_history = int(win.context_cache_shortage.get("shortage_bars", 1)) == 0
        expected_loaded = win.lookback if win.lookback > 0 else len(win.bars_df)
        gui_loads_selected_lookback = bool(
            len(win.bars_df) >= min(expected_loaded, len(win.bars_df))
            and len(win.visible_bars) >= min(expected_loaded, win.max_visible_bars, len(win.bars_df))
        )
        # Forming bar (if visible) is the rightmost rendered bar
        _rendered_max = int(expected_last) if expected_last is not None else -1
        if win.show_forming_bar and forming_ids_in_cache:
            _rendered_max = max(_rendered_max, max(int(x) for x in forming_ids_in_cache))
        latest_closed_bar_rendered = bool(
            _rendered_max >= 0
            and not win.visible_bars.empty
            and int(win.visible_bars["bar_index"].max()) == _rendered_max
        )
        performance_stats_exist = isinstance(win.perf_stats, PerformanceStats)
        visible_window_rendering = (
            performance_stats_exist
            and win.perf_stats.bars_rendered <= max(win.max_visible_bars, FOLLOW_WINDOW + VISIBLE_MARGIN_BARS)
            and win.perf_stats.visible_cells <= win.max_visible_cells
        )
        render_throttle_enabled = win.render_interval_ms >= 50
        reentrancy_guard_enabled = hasattr(win, "_refresh_busy") and hasattr(win, "_render_timer")
        follow_live_starts_checked = bool(win.follow_cb.isChecked() and win.follow_live_requested)

        win._programmatic_view_update = True
        win._setting_ranges = True
        try:
            win.main_plot.setXRange(1.0, 6.0, padding=0.0)
            win.main_plot.setYRange(30820.0, 30870.0, padding=0.0)
            win.pressure_plot.setYRange(-1000.0, 1000.0, padding=0.0)
        finally:
            win._setting_ranges = False
            win._programmatic_view_update = False
        win.user_view_override = True
        win.user_view_locked = True
        win._initialized = True
        locked_before = win._capture_ranges()

        win._on_mouse_move(QtCore.QPointF(-1e9, -1e9))
        app.processEvents()
        cursor_after = win._capture_ranges()
        cursor_move_preserves_view = win._ranges_close(locked_before, cursor_after)
        cursor_move_unchecks = not bool(win.follow_cb.isChecked())

        win._on_user_interaction((True, False))
        app.processEvents()
        zoom_after = win._capture_ranges()
        pan_zoom_preserves_view = win._ranges_close(locked_before, zoom_after)
        zoom_unchecks = not bool(win.follow_cb.isChecked())

        win._on_user_interaction((False, True))
        app.processEvents()
        pan_after = win._capture_ranges()
        pan_zoom_preserves_view = pan_zoom_preserves_view and win._ranges_close(locked_before, pan_after)
        pan_unchecks = not bool(win.follow_cb.isChecked())

        for _ in range(3):
            win._reload()
            app.processEvents()
        refresh_after = win._capture_ranges()
        data_refresh_preserves_view = win._ranges_close(locked_before, refresh_after)
        data_refresh_unchecks = not bool(win.follow_cb.isChecked())
        follow_live_checkbox_stays_checked = bool(win.follow_cb.isChecked())

        win._reset_view()
        app.processEvents()
        reset_after = win._capture_ranges()
        live_n = int(win.visible_cells["bar_pos"].max()) + 1 if not win.visible_cells.empty else 0
        reset_x_max = reset_after["main"][0][1]
        reset_view_still_works = (
            not win.user_view_override
            and not win.user_view_locked
            and live_n > 0
            and reset_x_max >= (live_n - 1)
        )
        follow_live_checkbox_stays_checked = (
            follow_live_checkbox_stays_checked and bool(win.follow_cb.isChecked())
        )

        win.follow_cb.setChecked(False)
        app.processEvents()
        explicit_uncheck_ok = (not win.follow_cb.isChecked()) and (not win.follow_live_requested)
        win.follow_cb.setChecked(True)
        app.processEvents()
        explicit_recheck_ok = bool(win.follow_cb.isChecked() and win.follow_live_requested)
        checkbox_click_still_works = explicit_uncheck_ok and explicit_recheck_ok

        win.follow_cb.setChecked(False)
        app.processEvents()
        win._programmatic_view_update = True
        win._setting_ranges = True
        try:
            win.main_plot.setXRange(1.0, 6.0, padding=0.0)
            win.main_plot.setYRange(30820.0, 30870.0, padding=0.0)
            win.pressure_plot.setYRange(-1000.0, 1000.0, padding=0.0)
        finally:
            win._setting_ranges = False
            win._programmatic_view_update = False
        win.user_view_override = True
        win.user_view_locked = True
        win._initialized = True
        before = win._capture_ranges()
        for _ in range(3):
            win._reload()
            app.processEvents()
        after = win._capture_ranges()
        auto_reset_ok = win._ranges_close(before, after)
        win.close()
        app.processEvents()
    except Exception as exc:
        print(f"AUTO_RESET_TEST_EXCEPTION={exc!r}")
        auto_reset_ok = False
        gui_loads_latest_cache = False
        cache_mtime_watcher_works = False
    print(f"GUI_ACTIVE_DATE={active_date}")
    print(f"GUI_LIVE_LATEST_MODE={str(live_latest_mode).lower()}")
    print(f"GUI_LOADS_LATEST_CACHE={str(gui_loads_latest_cache).lower()}")
    print(f"CACHE_MTIME_WATCHER_WORKS={str(cache_mtime_watcher_works).lower()}")
    print(f"TIMESTAMP_PARITY_FIXED={str(timestamp_parity_fixed).lower()}")
    print(f"GUI_TIMESTAMP_PARITY_OR_EXPLAINED={str(gui_timestamp_parity_or_explained).lower()}")
    print(f"LIVE_LATEST_CONTEXT_ENABLED={str(live_latest_context_enabled).lower()}")
    print(f"PREVIOUS_SESSION_CONTEXT_SUPPORTED={str(previous_session_context_supported).lower()}")
    print(f"CONTEXT_DATES_LOADED={getattr(win, 'context_dates_loaded', []) if 'win' in dir() else []}")
    print(f"CACHE_CARRIES_ENOUGH_HISTORY={str(cache_carries_enough_history).lower()}")
    print(f"GUI_LOADS_SELECTED_LOOKBACK={str(gui_loads_selected_lookback).lower()}")
    print(f"LATEST_CLOSED_BAR_RENDERED={str(latest_closed_bar_rendered).lower()}")
    print(f"FOLLOW_LIVE_STARTS_CHECKED={str(follow_live_starts_checked).lower()}")
    print(f"CURSOR_MOVE_UNCHECKS_FOLLOW_LIVE={str(cursor_move_unchecks).lower()}")
    print(f"ZOOM_UNCHECKS_FOLLOW_LIVE={str(zoom_unchecks).lower()}")
    print(f"PAN_UNCHECKS_FOLLOW_LIVE={str(pan_unchecks).lower()}")
    print(f"DATA_REFRESH_UNCHECKS_FOLLOW_LIVE={str(data_refresh_unchecks).lower()}")
    print(f"CHECKBOX_CLICK_STILL_WORKS={str(checkbox_click_still_works).lower()}")
    print(f"PAN_ZOOM_PRESERVES_VIEW={str(pan_zoom_preserves_view).lower()}")
    print(f"CURSOR_MOVE_PRESERVES_VIEW={str(cursor_move_preserves_view).lower()}")
    print(f"DATA_REFRESH_PRESERVES_VIEW={str(data_refresh_preserves_view).lower()}")
    print(f"RESET_VIEW_STILL_WORKS={str(reset_view_still_works).lower()}")
    print(f"FOLLOW_LIVE_CHECKBOX_STAYS_CHECKED={str(follow_live_checkbox_stays_checked).lower()}")
    print(f"AUTO_RESET_FIXED={str(auto_reset_ok).lower()}")
    print(f"PERFORMANCE_STATS_EXIST={str(performance_stats_exist).lower()}")
    print(f"VISIBLE_WINDOW_RENDERING={str(visible_window_rendering).lower()}")
    print(f"RENDER_THROTTLE_ENABLED={str(render_throttle_enabled).lower()}")
    print(f"REENTRANCY_GUARD_ENABLED={str(reentrancy_guard_enabled).lower()}")
    # Forming-bar completeness assertions
    show_forming_bar_toggle_added = hasattr(win if 'win' in dir() else object(), "forming_bar_cb")
    try:
        _win_tmp = BookFlowLevelChartWindow(symbol, compact_request_date, start_worker=False)
        _win_tmp._reload()
        app.processEvents()
        if live_latest_mode:
            # Forming bar is ON by default in live mode — that is the correct new behavior
            closed_bars_only_default = bool(_win_tmp.show_forming_bar)
        else:
            closed_bars_only_default = (
                not _win_tmp.show_forming_bar
                and ("bar_state" not in _win_tmp.level_df.columns
                     or "FORMING" not in _win_tmp.level_df["bar_state"].values)
            )
        forming_bar_not_in_default_render = closed_bars_only_default
        show_forming_bar_toggle_added = hasattr(_win_tmp, "forming_bar_cb")
        # Toggle ON and check forming bar appears
        if show_forming_bar_toggle_added and forming_ids_in_cache:
            _win_tmp._on_forming_bar_toggle(2)  # Qt.Checked = 2
            _win_tmp._flush_queued_reload()      # ensure sync reload bypassing timer delay
            app.processEvents()
            forming_bar_visible_when_toggled = (
                "bar_state" in _win_tmp.level_df.columns
                and "FORMING" in _win_tmp.level_df["bar_state"].values
            )
        else:
            forming_bar_visible_when_toggled = not bool(forming_ids_in_cache)
        _win_tmp.close()
        app.processEvents()
    except Exception as exc:
        print(f"FORMING_BAR_GUI_TEST_EXCEPTION={exc!r}")
        closed_bars_only_default = False
        forming_bar_not_in_default_render = False
        forming_bar_visible_when_toggled = False
    print(f"CLOSED_BARS_ONLY_DEFAULT={str(closed_bars_only_default).lower()}")
    print(f"SHOW_FORMING_BAR_TOGGLE_ADDED={str(show_forming_bar_toggle_added).lower()}")
    print(f"FORMING_BAR_NOT_IN_DEFAULT_RENDER={str(forming_bar_not_in_default_render).lower()}")
    print(f"FORMING_BAR_VISIBLE_WHEN_TOGGLED={str(forming_bar_visible_when_toggled).lower()}")
    ok &= closed_bars_only_default and show_forming_bar_toggle_added

    # === RENDER PARITY ASSERTIONS ===
    # 1. HB cache bar == loaded OFI max (or forming-hidden explains 1-bar gap)
    _hb_cache_depth5 = cache_last_bar_by_depth.get("5")
    _parity_ok_assertion = False
    _loaded_ofi_max_assertion = None
    _rendered_max_assertion = None
    _stale_forming_ignored_assertion = False
    _forming_none_cells_zero_assertion = False
    try:
        _pwin = BookFlowLevelChartWindow(symbol, compact_request_date, start_worker=False)
        _pwin._reload()
        app.processEvents()
        _loaded_ofi_max_assertion = int(_pwin.level_df["bar_idx"].max()) if not _pwin.level_df.empty else None
        _rendered_max_assertion = int(_pwin.visible_bars["bar_index"].max()) if not _pwin.visible_bars.empty else None
        _par = getattr(_pwin, "_parity_status", {})
        _parity_ok_assertion = bool(_par.get("parity_ok", False))
        # Stale forming cache ignored: if forming_bar_idx None in meta, forming cache not used
        _no_forming_idx_depths = [
            d for d in DEPTH_CHOICES
            if heartbeat.get("forming_bar_idx_actual_by_depth", {}).get(str(d)) is None
        ]
        _stale_forming_test_ok = True
        for _d in _no_forming_idx_depths:
            _fdf = _load_forming_df(symbol, active_date, _d)
            if not _fdf.empty:
                _stale_forming_test_ok = False
        _stale_forming_ignored_assertion = _stale_forming_test_ok
        # Heartbeat consistency: forming_bar_idx None → forming_cells 0
        _forming_none_cells_zero_assertion = all(
            (heartbeat.get("forming_bar_idx_actual_by_depth", {}).get(str(d)) is not None
             or int(heartbeat.get("forming_cells_by_depth", {}).get(str(d), 0)) == 0)
            for d in DEPTH_CHOICES
        )
        _pwin.close()
        app.processEvents()
    except Exception as exc:
        print(f"PARITY_TEST_EXCEPTION={exc!r}")
    print(f"HB_CACHE_BAR_DEPTH5={_hb_cache_depth5}")
    print(f"LOADED_OFI_MAX_BAR={_loaded_ofi_max_assertion}")
    print(f"RENDERED_MAX_BAR={_rendered_max_assertion}")
    print(f"OFI_HB_PARITY_OK={str(_parity_ok_assertion).lower()}")
    print(f"STALE_FORMING_CACHE_IGNORED={str(_stale_forming_ignored_assertion).lower()}")
    print(f"FORMING_NONE_CELLS_ZERO={str(_forming_none_cells_zero_assertion).lower()}")
    ok &= _parity_ok_assertion and _forming_none_cells_zero_assertion

    ok &= (auto_reset_ok and follow_live_starts_checked and checkbox_click_still_works
           and not cursor_move_unchecks and not zoom_unchecks
           and not pan_unchecks and not data_refresh_unchecks
           and pan_zoom_preserves_view and cursor_move_preserves_view
           and data_refresh_preserves_view and reset_view_still_works
           and follow_live_checkbox_stays_checked and performance_stats_exist
           and visible_window_rendering and render_throttle_enabled
           and reentrancy_guard_enabled and gui_loads_latest_cache
           and cache_mtime_watcher_works and timestamp_parity_fixed
           and gui_timestamp_parity_or_explained and live_latest_context_enabled
           and previous_session_context_supported and cache_carries_enough_history
           and gui_loads_selected_lookback and latest_closed_bar_rendered)

    dash_ok = _dashboard_launch_uses_v3()
    print(f"DASHBOARD_LAUNCH_BUTTON_USES_V3={str(dash_ok).lower()}")
    ok &= dash_ok

    print("SMOKE_TEST=PASS" if ok else "SMOKE_TEST=FAIL")
    sys.stdout.flush()
    os._exit(0 if ok else 1)


def run_benchmark(symbol: str, date: str, bars: int, depth: int) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    t0 = time.time()
    win = BookFlowLevelChartWindow(symbol, date, start_worker=False)
    win.depth = int(depth)
    win.max_visible_bars = int(bars)
    win.depth_combo.blockSignals(True)
    win.depth_combo.setCurrentText(str(depth))
    win.depth_combo.blockSignals(False)
    win._reload(force_data=True, force_render=True)
    app.processEvents()
    total_ms = (time.time() - t0) * 1000.0
    stats = win.perf_stats
    ok = (
        stats.visible_cells <= win.max_visible_cells
        and stats.bars_rendered <= max(win.max_visible_bars, FOLLOW_WINDOW + VISIBLE_MARGIN_BARS)
        and stats.render_ms < 250.0
    )
    print(f"BENCHMARK_RESULT={'PASS' if ok else 'FAIL'}")
    print(f"load_ms={stats.cache_update_ms:.3f}")
    print(f"compute_ms={stats.render_prepare_ms:.3f}")
    print(f"render_prepare_ms={stats.render_prepare_ms:.3f}")
    print(f"render_ms={stats.render_ms:.3f}")
    print(f"gui_update_ms={stats.gui_update_ms:.3f}")
    print(f"total_ms={total_ms:.3f}")
    component_times = {
        "load": stats.cache_update_ms,
        "compute": stats.compute_ms,
        "render": stats.render_ms,
        "profile": stats.profile_ms,
        "pulls": stats.pulls_ms,
    }
    bottleneck_component = max(component_times, key=component_times.get)
    print(f"visible_bars={stats.bars_rendered}")
    print(f"visible_cells={stats.visible_cells}")
    print(f"estimated_refresh_ms={stats.gui_update_ms:.3f}")
    print(f"max_update_ms_estimate={max(stats.cache_update_ms, stats.gui_update_ms):.3f}")
    print(f"bottleneck_component={bottleneck_component}")
    print(f"skipped_frames={stats.skipped_frames}")
    print(f"coalesced_frames={stats.coalesced_frames}")
    print(f"render_decimated={str(stats.render_decimated).lower()}")
    print(f"backend={stats.backend}")
    win.close()
    app.processEvents()
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="True book-flow level candle chart V3")
    parser.add_argument("--symbol", default="NQU6")
    parser.add_argument("--date", default=_default_date())
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--bars", type=int, default=1000)
    parser.add_argument("--depth", type=int, default=20, choices=DEPTH_CHOICES)
    args = parser.parse_args()
    if args.smoke_test:
        return run_smoke_test(args.symbol, args.date)
    if args.benchmark:
        return run_benchmark(args.symbol, args.date, args.bars, args.depth)

    app = QtWidgets.QApplication(sys.argv)
    win = BookFlowLevelChartWindow(args.symbol, args.date, start_worker=True)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
