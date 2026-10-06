#!/usr/bin/env python3
"""
book_flow_data_service.py

SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING.

BookFlowDataService -- a renderer-agnostic data layer for the book-flow level-candle chart.
Step 3 Phase 1 (STEP3_REPORT.md / P95_DECISION.md) ports the chart's live-latest mode (forming
bar + previous-session context, the out-of-the-box default UX) natively into this service --
previously only replay/single-date viewing ran through here; live-latest still ran on the legacy
CacheFileMonitor/GUI-thread path, which P95_DECISION.md named as the source of most of the
idle-live latency tail (uncached heartbeat JSON + filesystem date-discovery walk paid on every
poll, a GUI-thread slow-path race, multi-date pandas concat/groupby per update).

DEPENDENCIES: pandas, numpy, and book_flow_cache_daemon/book_flow_lib only. Deliberately imports
NOTHING from book_flow_chart_v3.py or pyqtgraph/PySide6/Qt of any kind -- a web backend consuming
this service must never be forced to pull in a desktop GUI toolkit. Enforced by convention (no Qt
imports anywhere in this file) rather than a runtime check.

┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ ChartFrame schema                                                                        │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│ version           int    Monotonically increasing per BookFlowDataService instance.      │
│ symbol, date, depth       The parameters this frame was built for (date = the RESOLVED   │
│                          active date when live_latest=True, not a literal "latest").      │
│ generated_at_mono float   time.monotonic() timestamp of when this frame was published.    │
│ sealed_bars       pd.DataFrame  OHLC bar-level rows for every CLOSED bar in the selected   │
│                                 context (previous session(s), if any, + current session). │
│                                 Includes a synthetic row for the forming bar (matching the │
│                                 legacy path's "Bug 2" handling) so it has an x-axis slot    │
│                                 even though it's not in the vol500 bars file yet.          │
│ sealed_cells      pd.DataFrame  OFI cell-level rows for every CLOSED bar, same context.    │
│                                 Includes the forming/compact handoff-gap bridge fix (see    │
│                                 _bridge_gap_if_needed) -- a bar sealed in the last cache-   │
│                                 daemon cycle is never missing from this frame.             │
│ forming_cells     Optional[pd.DataFrame]  Cell-level rows for the currently-forming bar,   │
│                                 populated ONLY if show_forming=True. Always None otherwise.│
│ forming_bar       Optional[pd.Series]  Convenience metadata for the forming bar (its       │
│                                 bar_index and synthetic timing) -- Phase 2 (order-book      │
│                                 panel) needs this without re-deriving it from forming_cells.│
│ last_price        Optional[float]  Convenience: current market price, derived from the     │
│                                 forming bar's cells if present, else the last sealed bar's. │
│ heartbeat         dict    The cache daemon's global heartbeat dict at publish time.        │
│ last_closed_bar_idx  Optional[int]  Convenience: max bar_idx in sealed_cells.               │
│ book_prices/book_bid_sizes/book_ask_sizes  Optional[np.ndarray]  Phase 2: current resting-  │
│                                 depth snapshot from build_book_flow_level_cache's v3         │
│                                 checkpoint (same daemon cycle, ~2s, as the compact cache),   │
│                                 windowed to +-BOOK_WINDOW_TICKS around the touch. All None   │
│                                 if no book is available yet.                                │
│ book_ts           Optional[float]  Checkpoint's updated_utc as epoch seconds. A consumer     │
│                                 must treat the book as STALE when                            │
│                                 time.time() - book_ts > BOOK_STALE_SECS (or book_ts is None) │
│                                 and never render a stale book as live.                       │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│ Immutability contract: every DataFrame on a ChartFrame is a fresh copy at publish time.    │
│ Consumers may read freely but must never mutate them in place -- treat every ChartFrame    │
│ as a value, not a handle into service-internal state.                                     │
└─────────────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ BookFlowDataService API                                                                  │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│ __init__(symbol, date, depth, show_forming=False, poll_interval_s=0.5,                    │
│          live_latest=False, previous_sessions=0)                                          │
│   live_latest=False (default): unchanged from before -- fixed `date`, no rollover          │
│   tracking, no previous-session context. Replay/historical behavior is untouched.          │
│   live_latest=True: `date` is ignored; the active date is resolved from the cache daemon's  │
│   heartbeat and tracked for rollover. previous_sessions controls how many prior sessions    │
│   are assembled as static context (1, matching CONTEXT_PREV_1, is the out-of-the-box UX).  │
│ start() -> None            Starts the background polling thread. Idempotent.              │
│ stop(timeout=2.0) -> None  Signals the thread to stop and joins it.                        │
│ get_latest(block=False, timeout=None) -> Optional[ChartFrame]                              │
│                             Returns the newest published frame, or None if nothing new     │
│                             since the last call (non-blocking by default). maxsize=1 --    │
│                             a stale pending frame is replaced, never accumulated.          │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│ Cost discipline in live_latest mode (this is what Phase 1 Part A was for):                 │
│  - Heartbeat JSON is read at most once per (mtime, size) change of the heartbeat file --   │
│    never unconditionally, never folded into any data-change signature.                     │
│  - The date-discovery filesystem walk (book_flow_lib.available_dates_for_symbol, two        │
│    iterdir() passes) runs ONLY when a rollover is detected (active_date changed) -- never   │
│    once per poll.                                                                          │
│  - Previous session(s)' bars+cells are loaded ONCE per rollover and held static in memory   │
│    until the next one -- never re-read, re-parsed, or re-concatenated on every poll.        │
│  - The current session's bars file uses the incremental, byte-identical-verified            │
│    load_vol500_bars_incremental() cache (Step 2.5) instead of a full reparse.                │
│  - A frame is only built (and the version bumped) when a real, targeted, cheap sig check    │
│    (compact/bars/forming file (mtime,size), no filesystem walk) detects an actual change.   │
└─────────────────────────────────────────────────────────────────────────────────────────┘

Run standalone for a quick manual check against live cache data (read-only):
    /home/prabh/.venvs/ofi/bin/python book_flow_data_service.py --symbol NQU6 --date 2026-07-30 --depth 10
    /home/prabh/.venvs/ofi/bin/python book_flow_data_service.py --symbol NQU6 --live-latest --depth 10
"""
from __future__ import annotations

import argparse
import threading
import queue
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from datetime import datetime, timezone

import numpy as np
import pandas as pd

import book_flow_cache_daemon as cache_daemon
import book_flow_lib as bfl
import build_book_flow_level_cache as level_cache

# Phase 2 Part E: current resting-depth window and staleness threshold. The checkpoint
# (level_cache.v3_state_path) covers the FULL PRICE_MIN..PRICE_MAX range (80000 ticks) but only
# ~1-2k of those are ever non-zero, and levels far from the touch are sparse, tiny (1-3 lots) and
# scattered at implausible prices (confirmed by direct inspection -- almost certainly stale/
# never-reconciled add/pull replay artifacts, not real resting liquidity). Only the window around
# the touch is dense and trustworthy, which also happens to be the only part any sane zoom level
# would ever render -- so the window clip below is a correctness fix, not just a perf one.
BOOK_WINDOW_TICKS = 400  # +-100.0 points around the touch at TICK=0.25
BOOK_STALE_SECS = 10.0  # checkpoint is rewritten every daemon cycle (~2s); 5x cadence for jitter margin


def _file_sig(path: Path) -> tuple[int, int]:
    try:
        st = path.stat()
        return (int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return (0, 0)


def _load_book_depth(symbol: str, date: str, window_ticks: int = BOOK_WINDOW_TICKS) -> Optional[dict]:
    """Reads the level-cache builder's v3 checkpoint (same daemon, same ~2s cycle as the compact
    cache -- see build_book_flow_level_cache.save_v3_state, called every book_flow_cache_daemon
    poll). Read-only; uses the module's own load_v3_state(), which already does an atomic-write-
    safe try/except (the writer does tmp+os.replace, so a torn read should never happen, but a
    missing/mid-rotation file is tolerated here regardless). Returns None if there's no book yet
    (fresh session, or a past date with no checkpoint) or the book is empty on both sides."""
    state = level_cache.load_v3_state(symbol, date)
    if state is None:
        return None
    bid_sizes = state.get("bid_sizes")
    ask_sizes = state.get("ask_sizes")
    if bid_sizes is None or ask_sizes is None:
        return None
    bid_nz = np.nonzero(bid_sizes)[0]
    ask_nz = np.nonzero(ask_sizes)[0]
    if bid_nz.size == 0 and ask_nz.size == 0:
        return None
    best_bid_tick = int(bid_nz.max()) if bid_nz.size else None
    best_ask_tick = int(ask_nz.min()) if ask_nz.size else None
    if best_bid_tick is not None and best_ask_tick is not None:
        mid_tick = (best_bid_tick + best_ask_tick) // 2
    else:
        mid_tick = best_bid_tick if best_bid_tick is not None else best_ask_tick
    n_ticks = bid_sizes.shape[0]
    lo = max(0, mid_tick - window_ticks)
    hi = min(n_ticks, mid_tick + window_ticks + 1)
    ticks = np.arange(lo, hi)
    book_ts = None
    try:
        book_ts = datetime.fromisoformat(str(state.get("updated_utc"))).timestamp()
    except (TypeError, ValueError):
        pass
    return {
        "prices": bfl.tick_to_price(ticks),
        "bid_sizes": bid_sizes[lo:hi].copy(),
        "ask_sizes": ask_sizes[lo:hi].copy(),
        "best_bid_tick": best_bid_tick,
        "best_ask_tick": best_ask_tick,
        "book_ts": book_ts,
    }


def _bars_path(symbol: str, date: str) -> Path:
    return bfl.FEATURES_BASE / date / f"{symbol}_vol500.ndjsonl"


def _load_bars(symbol: str, date: str) -> pd.DataFrame:
    # Step 2.5 Part C (see P95_FORENSICS.md): incremental cache, see
    # book_flow_lib.load_vol500_bars_incremental()'s docstring for why.
    bars = bfl.load_vol500_bars_incremental(_bars_path(symbol, date))
    if not bars.empty:
        bars = bars.sort_values("bar_index").reset_index(drop=True)
    return bars


def _load_compact(symbol: str, date: str, depth: int) -> pd.DataFrame:
    cpath = cache_daemon.compact_cache_path(symbol, date, depth)
    if not cpath.exists():
        return pd.DataFrame()
    df = pd.read_parquet(cpath)
    return df.sort_values(["bar_idx", "price_level"]).reset_index(drop=True)


def _load_forming(symbol: str, date: str, depth: int, stale_secs: float = 5.0) -> pd.DataFrame:
    fpath = cache_daemon.forming_cache_path(symbol, date, depth)
    if not fpath.exists():
        return pd.DataFrame()
    try:
        if time.time() - fpath.stat().st_mtime > stale_secs:
            return pd.DataFrame()
    except OSError:
        return pd.DataFrame()
    # Meta consistency guard, ported from the legacy _load_forming_df() for parity (Step 3 Phase
    # 1) -- rejects a technically-fresh-by-mtime file whose companion meta.json says there isn't
    # really a forming bar yet (forming_bar_idx absent/zero, e.g. mid bar-roll transition).
    meta_path = cache_daemon.forming_cache_meta_path(symbol, date, depth)
    try:
        import json
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
    return df


def _bridge_gap_if_needed(symbol: str, date: str, depth: int, sealed_cells: pd.DataFrame) -> pd.DataFrame:
    """Ports the Part B fix (DIAGNOSIS.md section 3) natively for this service: the fast
    forming-bar cache tags a just-sealed bar CLOSED for one cycle before the slower compact
    cache picks it up. Without bridging, the just-sealed bar is briefly absent from every
    source -- the bar-roll flicker. Splices in the bridge record if present."""
    if "bar_state" in sealed_cells.columns:
        closed_only = sealed_cells[sealed_cells["bar_state"] != "FORMING"]
    else:
        closed_only = sealed_cells
    closed_ids = set(int(x) for x in closed_only["bar_idx"].unique()) if not closed_only.empty else set()
    last_closed = max(closed_ids) if closed_ids else None
    bridge_bar_idx = (last_closed + 1) if last_closed is not None else None
    if bridge_bar_idx is None or bridge_bar_idx in closed_ids:
        return closed_only.reset_index(drop=True)

    forming = _load_forming(symbol, date, depth)
    if forming.empty or "bar_state" not in forming.columns:
        return closed_only.reset_index(drop=True)
    bridge_rows = forming[(forming["bar_idx"] == bridge_bar_idx) & (forming["bar_state"] == "CLOSED")]
    if bridge_rows.empty:
        return closed_only.reset_index(drop=True)
    return (
        pd.concat([closed_only, bridge_rows], ignore_index=True)
        .sort_values(["bar_idx", "price_level"])
        .reset_index(drop=True)
    )


def _last_price_from_cells(cells: pd.DataFrame) -> Optional[float]:
    if cells is None or cells.empty:
        return None
    row = cells.iloc[-1]
    for col in ("close_price", "mid_price"):
        if col in cells.columns:
            v = row.get(col)
            try:
                if v is not None and np.isfinite(float(v)):
                    return float(v)
            except (TypeError, ValueError):
                continue
    return None


@dataclass(frozen=True)
class ChartFrame:
    version: int
    symbol: str
    date: str
    depth: int
    generated_at_mono: float
    sealed_bars: pd.DataFrame
    sealed_cells: pd.DataFrame
    forming_cells: Optional[pd.DataFrame]
    heartbeat: dict
    last_closed_bar_idx: Optional[int] = field(default=None)
    forming_bar: Optional[pd.Series] = field(default=None)
    last_price: Optional[float] = field(default=None)
    context_dates: tuple = field(default=())
    # Phase 2 Part E: current resting-depth snapshot, windowed to +-BOOK_WINDOW_TICKS around the
    # touch (see _load_book_depth). All three arrays share the same index; book_ts is the
    # checkpoint's updated_utc as epoch seconds, or None if no book is available yet. A consumer
    # must treat the book as STALE when time.time() - book_ts > BOOK_STALE_SECS (or book_ts is
    # None) and never render a stale book as live.
    book_prices: Optional[np.ndarray] = field(default=None)
    book_bid_sizes: Optional[np.ndarray] = field(default=None)
    book_ask_sizes: Optional[np.ndarray] = field(default=None)
    book_ts: Optional[float] = field(default=None)

    def __post_init__(self):
        if self.last_closed_bar_idx is None and not self.sealed_cells.empty:
            object.__setattr__(self, "last_closed_bar_idx", int(self.sealed_cells["bar_idx"].max()))
        if not self.context_dates:
            object.__setattr__(self, "context_dates", (self.date,))


class BookFlowDataService:
    """Renderer-agnostic. No Qt/pyqtgraph import anywhere in this module. Uses stdlib
    threading + queue only, so any renderer (desktop, web backend, notebook, test) can
    consume it identically."""

    def __init__(self, symbol: str, date: str, depth: int, show_forming: bool = False,
                 poll_interval_s: float = 0.5, live_latest: bool = False,
                 previous_sessions: int = 0):
        self.symbol = symbol
        self.date = date  # fixed-mode date; ignored (heartbeat-resolved) when live_latest=True
        self.depth = depth
        self.show_forming = show_forming
        self.poll_interval_s = poll_interval_s
        self.live_latest = live_latest
        self.previous_sessions = int(previous_sessions) if live_latest else 0

        self._queue: "queue.Queue[ChartFrame]" = queue.Queue(maxsize=1)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._version = 0
        self._sigs: dict[str, tuple[int, int]] = {}

        # live_latest-only state
        self._heartbeat_file_sig: tuple[int, int] = (0, 0)
        self._heartbeat_cache: dict = {}
        self._active_date: Optional[str] = None
        self._prev_cells: pd.DataFrame = pd.DataFrame()
        self._prev_bars: pd.DataFrame = pd.DataFrame()
        self._context_dates: tuple = ()
        # Step 3 Phase 1 Part C: cached previous+current combination, only rebuilt when the
        # "core" (compact+bars) sig changes -- see _build_frame_live_latest()'s docstring note.
        self._core_sigs: dict[str, tuple[int, int]] = {}
        self._forming_sig: Optional[tuple[int, int]] = None
        self._cached_sealed_cells: pd.DataFrame = pd.DataFrame()
        self._cached_bars_out: pd.DataFrame = pd.DataFrame()
        self._cached_cur_bars: pd.DataFrame = pd.DataFrame()

        # Phase 2 Part E: order-book depth checkpoint, sig-gated like everything else here -- the
        # pickle is only reloaded when the checkpoint file's (mtime, size) actually changes.
        self._book_sig: tuple[int, int] = (0, 0)
        self._cached_book: Optional[dict] = None

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="BookFlowDataService")
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    # ── consumer API ───────────────────────────────────────────────────────
    def get_latest(self, block: bool = False, timeout: Optional[float] = None) -> Optional[ChartFrame]:
        try:
            return self._queue.get(block=block, timeout=timeout)
        except queue.Empty:
            return None

    def _get_book_depth(self, symbol: str, date: str) -> Optional[dict]:
        sig = _file_sig(level_cache.v3_state_path(symbol, date))
        if sig != self._book_sig:
            self._book_sig = sig
            self._cached_book = _load_book_depth(symbol, date)
        return self._cached_book

    # ── internals: fixed-date mode (unchanged from before Step 3) ─────────
    def _current_sigs(self) -> dict[str, tuple[int, int]]:
        compact_path = cache_daemon.compact_cache_path(self.symbol, self.date, self.depth)
        sigs = {"compact": _file_sig(compact_path), "bars": _file_sig(_bars_path(self.symbol, self.date))}
        if self.show_forming:
            sigs["forming"] = _file_sig(cache_daemon.forming_cache_path(self.symbol, self.date, self.depth))
        return sigs

    def _build_frame(self) -> Optional[ChartFrame]:
        bars = _load_bars(self.symbol, self.date)
        if bars.empty:
            return None
        compact = _load_compact(self.symbol, self.date, self.depth)
        if compact.empty:
            return None

        sealed_cells = _bridge_gap_if_needed(self.symbol, self.date, self.depth, compact)
        forming_cells = None
        if self.show_forming:
            forming = _load_forming(self.symbol, self.date, self.depth)
            if not forming.empty and "bar_state" in forming.columns:
                fbar = int(forming["bar_idx"].max())
                fstate = str(forming.loc[forming["bar_idx"] == fbar, "bar_state"].iloc[0])
                if fstate == "FORMING":
                    forming_cells = forming.copy()

        sealed_ids = set(int(x) for x in sealed_cells["bar_idx"].unique()) if not sealed_cells.empty else set()
        bars_out = bars[bars["bar_index"].isin(sealed_ids)].copy().reset_index(drop=True)

        heartbeat = cache_daemon.load_heartbeat()
        book = self._get_book_depth(self.symbol, self.date)
        self._version += 1
        return ChartFrame(
            version=self._version,
            symbol=self.symbol, date=self.date, depth=self.depth,
            generated_at_mono=time.monotonic(),
            sealed_bars=bars_out,
            sealed_cells=sealed_cells.copy(),
            forming_cells=(forming_cells.copy() if forming_cells is not None else None),
            heartbeat=dict(heartbeat),
            last_price=_last_price_from_cells(forming_cells if forming_cells is not None else sealed_cells),
            book_prices=(book["prices"] if book else None),
            book_bid_sizes=(book["bid_sizes"] if book else None),
            book_ask_sizes=(book["ask_sizes"] if book else None),
            book_ts=(book["book_ts"] if book else None),
        )

    # ── internals: live_latest mode (Step 3 Phase 1) ───────────────────────
    def _get_heartbeat(self) -> dict:
        sig = _file_sig(cache_daemon.heartbeat_path())
        if sig != self._heartbeat_file_sig:
            self._heartbeat_cache = dict(cache_daemon.load_heartbeat())
            self._heartbeat_file_sig = sig
        return self._heartbeat_cache

    def _rebuild_previous_session_if_rolled(self, active_date: str) -> bool:
        """Returns True if a rollover was detected (and previous-session cache rebuilt)."""
        if active_date == self._active_date:
            return False
        self._active_date = active_date
        if self.previous_sessions <= 0:
            self._prev_cells = pd.DataFrame()
            self._prev_bars = pd.DataFrame()
            self._context_dates = (active_date,)
            return True
        # The ONLY place this service ever does a filesystem date-discovery walk -- exactly once
        # per detected rollover, never once per poll.
        context_dates = bfl.context_dates_for(self.symbol, active_date, self.previous_sessions)
        prev_dates = [d for d in context_dates if d != active_date]
        cells_list, bars_list = [], []
        loaded_prev_dates = []
        for d in prev_dates:
            b = _load_bars(self.symbol, d)
            if b.empty:
                continue
            lvl = _load_compact(self.symbol, d, self.depth)
            if lvl.empty:
                continue
            cells_list.append(lvl)
            bars_list.append(b)
            loaded_prev_dates.append(d)
        if cells_list:
            self._prev_cells = (
                pd.concat(cells_list, ignore_index=True)
                .sort_values(["bar_idx", "price_level"]).reset_index(drop=True)
            )
            self._prev_bars = (
                pd.concat(bars_list, ignore_index=True)
                .sort_values("bar_index").reset_index(drop=True)
                .drop_duplicates(subset=["bar_index"], keep="last").reset_index(drop=True)
            )
        else:
            self._prev_cells = pd.DataFrame()
            self._prev_bars = pd.DataFrame()
        # Step 3 Phase 1 Part B fix: must reflect the dates ACTUALLY combined into sealed_cells/
        # sealed_bars below, not just the active date -- downstream consumers (the chart's
        # context_dates_loaded, which _visible_bar_window's follow-span calculation keys off of)
        # need an accurate count. Found via a real legacy-vs-service pixel-parity mismatch: the
        # adapter was reporting loaded_dates=[frame.date] (length 1) even when a previous session
        # was genuinely loaded, silently giving the service path a narrower follow window than
        # the legacy path's wider one for the identical live-latest+forming-bar configuration.
        self._context_dates = tuple(loaded_prev_dates) + (active_date,)
        return True

    def _build_frame_live_latest(self) -> Optional[ChartFrame]:
        hb = self._get_heartbeat()
        active_date = str(hb.get("active_date") or self.date)
        rolled = self._rebuild_previous_session_if_rolled(active_date)

        # Targeted, cheap sig checks -- 2-3 stat() calls, never a filesystem walk. Split into
        # "core" (compact+bars -- determines whether the expensive previous+current concat needs
        # to redo) vs "forming" (the fast ~350ms-cadence lane) so a forming-only update (the
        # overwhelming majority of polls -- forming updates roughly 6x more often than compact
        # rewrites, and orders of magnitude more often than a bar actually seals) does not pay
        # for re-concatenating the static previous session on every single poll. This is the
        # Part C fix: P95_FORENSICS.md/a Part C profiling pass found this concat was the
        # dominant cost inside forming-update frames specifically, and Phase 1 Part A's own
        # mission text called for "no full concat... per update" -- the original implementation
        # only avoided re-loading the previous session's files, not re-concatenating them.
        core_sigs = {
            "compact": _file_sig(cache_daemon.compact_cache_path(self.symbol, active_date, self.depth)),
            "bars": _file_sig(_bars_path(self.symbol, active_date)),
        }
        forming_sig = (
            _file_sig(cache_daemon.forming_cache_path(self.symbol, active_date, self.depth))
            if self.show_forming else None
        )
        core_changed = rolled or core_sigs != self._core_sigs
        forming_changed = self.show_forming and forming_sig != self._forming_sig
        if not core_changed and not forming_changed:
            return None

        if core_changed:
            cur_bars = _load_bars(self.symbol, active_date)
            if cur_bars.empty:
                return None
            cur_compact = _load_compact(self.symbol, active_date, self.depth)
            if cur_compact.empty:
                return None
            cur_sealed_cells = _bridge_gap_if_needed(self.symbol, active_date, self.depth, cur_compact)

            if not self._prev_cells.empty:
                sealed_cells = (
                    pd.concat([self._prev_cells, cur_sealed_cells], ignore_index=True)
                    .sort_values(["bar_idx", "price_level"]).reset_index(drop=True)
                )
            else:
                sealed_cells = cur_sealed_cells
            sealed_ids = set(int(x) for x in sealed_cells["bar_idx"].unique()) if not sealed_cells.empty else set()

            if not self._prev_bars.empty:
                all_bars = (
                    pd.concat([self._prev_bars, cur_bars], ignore_index=True)
                    .sort_values("bar_index").reset_index(drop=True)
                    .drop_duplicates(subset=["bar_index"], keep="last").reset_index(drop=True)
                )
            else:
                all_bars = cur_bars
            bars_out = all_bars[all_bars["bar_index"].isin(sealed_ids)].copy().reset_index(drop=True)

            self._core_sigs = core_sigs
            self._cached_sealed_cells = sealed_cells
            self._cached_bars_out = bars_out
            self._cached_cur_bars = cur_bars
        else:
            # Forming-only update: reuse the cached previous+current combination untouched.
            sealed_cells = self._cached_sealed_cells
            bars_out = self._cached_bars_out
            cur_bars = self._cached_cur_bars

        forming_cells = None
        if self.show_forming:
            forming = _load_forming(self.symbol, active_date, self.depth)
            if not forming.empty and "bar_state" in forming.columns:
                fbar = int(forming["bar_idx"].max())
                fstate = str(forming.loc[forming["bar_idx"] == fbar, "bar_state"].iloc[0])
                if fstate == "FORMING":
                    forming_cells = forming.copy()
            self._forming_sig = forming_sig

        forming_bar_row = None
        if forming_cells is not None and not forming_cells.empty:
            fbar_idx = int(forming_cells["bar_idx"].max())
            last_vol500_bar_idx = int(cur_bars["bar_index"].iloc[-1]) if not cur_bars.empty else -1
            if fbar_idx > last_vol500_bar_idx and not bars_out.empty:
                # Bug-2 parity with the legacy path: the forming bar isn't in the vol500 bars
                # file yet, so it needs a synthetic row to get an x-axis slot. bars_out may be the
                # SHARED cache (forming-only update) -- append to a copy, never mutate the cache.
                synth = bars_out.iloc[-1].copy()
                synth["bar_index"] = fbar_idx
                synth["bar_start_ts_ns"] = int(synth.get("bar_end_ts_ns", 0))
                synth["bar_end_ts_ns"] = int(synth.get("bar_end_ts_ns", 0))
                bars_out = pd.concat([bars_out, pd.DataFrame([synth])], ignore_index=True)
                bars_out = bars_out.drop_duplicates(subset=["bar_index"], keep="last").reset_index(drop=True)
                forming_bar_row = synth
            elif not bars_out.empty and fbar_idx in set(bars_out["bar_index"]):
                forming_bar_row = bars_out[bars_out["bar_index"] == fbar_idx].iloc[-1]

        last_price = _last_price_from_cells(forming_cells if forming_cells is not None else sealed_cells)

        heartbeat = self._heartbeat_cache
        book = self._get_book_depth(self.symbol, active_date)
        self._version += 1
        return ChartFrame(
            version=self._version, symbol=self.symbol, date=active_date, depth=self.depth,
            generated_at_mono=time.monotonic(),
            sealed_bars=bars_out, sealed_cells=sealed_cells.copy(),
            forming_cells=(forming_cells.copy() if forming_cells is not None else None),
            forming_bar=forming_bar_row,
            last_price=last_price,
            heartbeat=dict(heartbeat),
            context_dates=self._context_dates,
            book_prices=(book["prices"] if book else None),
            book_bid_sizes=(book["bid_sizes"] if book else None),
            book_ask_sizes=(book["ask_sizes"] if book else None),
            book_ts=(book["book_ts"] if book else None),
        )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self.live_latest:
                    frame = self._build_frame_live_latest()
                else:
                    sigs = self._current_sigs()
                    frame = None
                    if sigs != self._sigs:
                        frame = self._build_frame()
                        if frame is not None:
                            self._sigs = sigs
                if frame is not None:
                    try:
                        self._queue.get_nowait()  # drop any stale unread frame
                    except queue.Empty:
                        pass
                    self._queue.put_nowait(frame)
            except Exception:
                pass  # a single bad poll must never kill the background thread
            self._stop_event.wait(self.poll_interval_s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="NQU6")
    ap.add_argument("--date", default=None)
    ap.add_argument("--live-latest", action="store_true")
    ap.add_argument("--previous-sessions", type=int, default=1)
    ap.add_argument("--show-forming", action="store_true")
    ap.add_argument("--depth", type=int, default=10)
    ap.add_argument("--seconds", type=float, default=5.0)
    args = ap.parse_args()
    if not args.live_latest and not args.date:
        raise SystemExit("--date is required unless --live-latest is given")

    svc = BookFlowDataService(
        args.symbol, args.date or "", args.depth, show_forming=(args.show_forming or args.live_latest),
        poll_interval_s=0.3, live_latest=args.live_latest,
        previous_sessions=args.previous_sessions if args.live_latest else 0,
    )
    svc.start()
    t0 = time.time()
    last_version = -1
    try:
        while time.time() - t0 < args.seconds:
            frame = svc.get_latest()
            if frame is not None and frame.version != last_version:
                last_version = frame.version
                book_age = (time.time() - frame.book_ts) if frame.book_ts else None
                book_n = len(frame.book_prices) if frame.book_prices is not None else 0
                print(f"ChartFrame v{frame.version}: date={frame.date} sealed_bars={len(frame.sealed_bars)} "
                      f"sealed_cells={len(frame.sealed_cells)} last_closed_bar_idx={frame.last_closed_bar_idx} "
                      f"forming={'yes' if frame.forming_cells is not None else 'no'} "
                      f"last_price={frame.last_price} book_levels={book_n} "
                      f"book_age_s={f'{book_age:.1f}' if book_age is not None else 'n/a'}")
            time.sleep(0.2)
    finally:
        svc.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
