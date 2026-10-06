"""
webbeta W3: live bridge.

W3 DECISION (user, 2026-08-07): every wire-protocol bug from W2.8 through W2.14 lived in the
diff/reconciliation layer this file used to carry -- LiveWireState.diff() decided what was "new"
by watching frame.sealed_bars for previously-unseen bar_index values, but frame.sealed_bars can
contain a SYNTHETIC row for the bar that is still forming (book_flow_data_service.py's own
ChartFrame docstring: "Includes a synthetic row for the forming bar... so it has an x-axis slot
even though it's not in the vol500 bars file yet"). That row's bar_index looks brand new to
bar_pos_map the INSTANT the bar starts forming, not when it actually seals -- diff() fired a
"bar_roll" (and committed a bar's cells to the wire) roughly one full bar early, while the bar had
accumulated ~0 cells, and never revisited it once genuinely sealed. Confirmed directly against the
real production log (W2.14): bar 40954 was marked new_sealed at 06:01:00 with new_sealed(adds)=
[40954], then continued accumulating forming cells (served_cells=122 at 06:05:45) for another 4m45s
before the bar that ACTUALLY replaced it as forming (40955) appeared -- by which point diff() had
long since committed and forgotten 40954's (empty) cell payload.

This file no longer tries to detect "what changed" at all. Instantiates book_flow_data_service's
BookFlowDataService (Qt-free by design) directly inside this FastAPI process, in live_latest mode
(forming bar + previous session, matching the desktop's out-of-the-box default) -- read-only on
production cache paths, the exact same access pattern book_flow_chart_v3.py already uses. On every
poll, whatever the service's own frame currently reports is broadcast, in full, for whatever
entities are affected -- exactly mirroring book_flow_chart_v3.py's own _poll_data_service():
`frame = self._data_service.get_latest(); if frame is None or frame.version ==
self._dataservice_last_version: <recheck book only>; else: <fully re-render from frame>`. No
webbeta-only reconciliation layer sits on top of the service's own output anywhere in this file --
the desktop's _bridge_gap_if_needed() (book_flow_data_service.py:228) is the ONLY bridge, trusted
exactly as the desktop trusts it, because "the desktop chart works perfectly" (this mission's own
framing) means that bridge is not the thing that was broken.

"Sealed" is determined the same way the desktop's own _bridge_gap_if_needed() determines it: by
frame.sealed_cells' bar_state column (== "CLOSED", i.e. frame.last_closed_bar_idx, which
ChartFrame.__post_init__ derives from sealed_cells) -- never by mere presence in frame.sealed_bars,
which is exactly the signal that misled the old diff layer.

Wire protocol (WIRE_SCHEMA.md v3): snapshot (on connect) / seal (a bar finished, complete cells,
under sealed identity, plus the new forming bar) / update (the forming bar, complete, plus
book/last_price) / book_update (book moved, nothing else did) / keepalive. Every one of these is
built by the SAME two small helpers (_bar_and_cells_for, _forming_payload) that build the
snapshot's own bars/cells/forming fields -- snapshot and live messages cannot diverge because they
are not two implementations of the same idea, they are one. The client applies every message by
replacing whatever it holds for the named bar_index/forming-slot wholesale; nothing is ever merged
or patched.

LiveBroadcastHub is the "one DataService, N clients" fan-out: one background poll loop reads the
service and pushes the resulting wire message(s) into every connected client's own bounded,
coalescing queue (ClientQueue) -- a slow client only ever coalesces to the latest state, never
grows unbounded, and never blocks or slows down other clients or the poll loop itself. This part
has no desktop equivalent (the desktop IS the single client, in-process) and is kept: it solves a
genuinely different problem (network fan-out to N independent clients) that a diff layer was never
required to solve.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("webbeta")

import numpy as np
import pandas as pd

BOOK_FLOW_CHART_DIR = Path(__file__).resolve().parent.parent.parent / "book_flow_chart"
sys.path.insert(0, str(BOOK_FLOW_CHART_DIR))
import book_flow_data_service as bfds  # noqa: E402
import book_flow_lib as bfl  # noqa: E402
import book_flow_cache_daemon as cache_daemon  # noqa: E402
import build_book_flow_level_cache as level_cache  # noqa: E402

from . import session_boundaries

SYMBOL = os.environ.get("WEBBETA_LIVE_SYMBOL", "NQU6")
DEPTH = int(os.environ.get("WEBBETA_LIVE_DEPTH", "10"))
POLL_INTERVAL_S = 0.3  # matches the desktop's live-latest service poll_interval_s
BROADCAST_INTERVAL_S = 0.1  # how often the hub checks the service for a new frame
BOOK_STALE_SECS = bfds.BOOK_STALE_SECS
# W2.6 Part A: how far (in ticks) the book's own mid may sit from the bar stream's last_price
# before it's treated as belonging to a different instrument/session entirely, rather than just
# a normal cross-poll skew (see LIVEFIX_DIAGNOSIS.md -- the 600-point/2400-tick real incident this
# guards against was two full orders of magnitude past this).
BOOK_MISMATCH_TICKS = int(os.environ.get("WEBBETA_BOOK_MISMATCH_TICKS", "200"))
# W2.7 Part B: an explicit, unconditional heartbeat -- independent of whether market data changed
# -- so a client always sees SOMETHING within this interval on a genuinely healthy connection.
KEEPALIVE_INTERVAL_S = 5.0
# Threshold for the connection/market status badge (LIVE vs STALE). Configurable for the simulator
# gates, which need a shorter threshold to keep test runtimes reasonable.
LIVE_STALE_SECS = float(os.environ.get("WEBBETA_LIVE_STALE_SECS", "600"))
# W2.9 Part 2: /status reports the server as "degraded" once this many seconds have passed since
# the poll loop last completed an iteration.
POLL_DEGRADED_THRESHOLD_S = 2 * KEEPALIVE_INTERVAL_S
# W3 Part D gate (d) / W4 Part 3: minimum spacing between "update" (forming-bar-complete)
# messages -- see _build_messages' own comment for why this exists (measured bandwidth, not a
# guess). Originally 2.0s (measured: brought steady-state bandwidth from ~54 to ~18 KB/s against
# the 50 KB/s ceiling). W4 Part 3 found this 2.0s gap was the likely real explanation for a
# reported "price line/book look static mid-bar" symptom on live (never reproducible via replay,
# since replay bypasses this live-only throttle entirely -- confirmed by the harness's own
# invariant (c) showing zero price-update misses against replay). Tightened to 0.75s after
# re-measuring: ~28 KB/s at a fast (3s/bar) simulator stress cadence, still comfortably under the
# 50 KB/s budget, with real RTH cadence expected lower still (seal's own bandwidth share scales
# down with bar-roll frequency; this constant's cost does not).
UPDATE_MIN_INTERVAL_S = float(os.environ.get("WEBBETA_UPDATE_MIN_INTERVAL_S", "0.75"))


def _maybe_redirect_to_sandbox() -> Optional[Path]:
    """Part D: if WEBBETA_LIVE_SANDBOX is set, monkeypatch book_flow_lib/book_flow_cache_daemon's
    module-level path constants so every downstream path-computing function (compact_cache_path,
    forming_cache_path, load_heartbeat, v3_state_path, etc.) transparently reads/writes under the
    sandbox instead of the real production cache -- without editing a single line of
    book_flow_chart/ itself. Two of these constants (HEARTBEAT_PATH, FORMING_CACHE_DIR) are
    captured ONCE at book_flow_cache_daemon's import time from bfl.CACHE_DIR, not recomputed per
    call like compact_cache_path/v3_state_path are -- so patching bfl.CACHE_DIR alone would miss
    them; both are patched explicitly here too. Returns the sandbox path if redirected, else None
    (real production paths, used as-is -- this is the live-mode default)."""
    sandbox = os.environ.get("WEBBETA_LIVE_SANDBOX")
    if not sandbox:
        return None
    root = Path(sandbox)
    bfl.RAW_BASE = root / "Rithmic_Raw"
    bfl.FEATURES_BASE = root / "Live_Features"
    bfl.CACHE_DIR = root / "book_flow_chart_cache"
    cache_daemon.HEARTBEAT_PATH = bfl.CACHE_DIR / "book_flow_cache_heartbeat.json"
    cache_daemon.FORMING_CACHE_DIR = bfl.CACHE_DIR / "forming"
    return root


# MISSION diagnose-negative-spread: root cause (confirmed by direct evidence, bypassing webbeta
# entirely -- polled build_book_flow_level_cache's own v3 checkpoint live, plus tailed the raw
# per-side ndjson files SampleMD writes): bid_sizes/ask_sizes are indexed by absolute price tick
# and accumulate every incremental quote update FOREVER. Rithmic's depth feed only sends updates
# for price levels currently within its reported depth window; once price moves on, a vacated
# level is never guaranteed an explicit clear. Confirmed directly: a stale bid entry's last raw
# update was 58.5 minutes old, while the feed had processed 110,495 more sequence numbers since --
# the level genuinely scrolled out of view and was simply never told to expire, not lost/dropped.
# The desktop app is unaffected (book_flow_chart_v3.py never computes a scalar best_bid/spread
# from this array -- it just draws the array as a windowed ladder, so a far-away ghost sits
# off-screen). The bug is specific to how THIS function previously derived best_bid/best_ask: an
# UNSCOPED np.nonzero(...).max()/.min() over the entire 80,000-tick array picks up whatever the
# single most extreme entry in the array's whole history happens to be -- including a ghost from
# an hour ago -- rather than the genuinely active touch.
#
# Fix, part 1 (proximity): anchor the search on the bar stream's own last-trade price and only
# accept a bid tick <= ref+GHOST_SLACK_TICKS / an ask tick >= ref-GHOST_SLACK_TICKS -- i.e. only
# bound the DIRECTION each side could actually be poisoned from (a bid ghost can only ever inflate
# best_bid upward; an ask ghost can only ever deflate best_ask downward), never the safe direction.
# GHOST_SLACK_TICKS=24 (6 points) was chosen empirically: polled the real live book every 3s over a
# 60s window, computing the resulting spread at several candidate slacks side by side against the
# SAME live ghost -- 8/16/24 ticks all produced clean, realistic spreads (<=1.25pt) on every one of
# 20 samples, while 32+ ticks started re-admitting the same ghost on some samples.
#
# Fix, part 2 (recency) -- proximity alone is NOT sufficient, confirmed live: price later drifted
# back toward the same ghost's price level and it started passing the proximity filter again
# (only ~9 ticks from last_price at that point, well inside GHOST_SLACK_TICKS). A stale level and
# a genuinely fresh one can sit at the same distance from last_price; distance alone can't tell
# them apart. What's missing is WHEN a tick last actually changed, which the checkpoint itself
# doesn't store -- so this module now tracks it independently, in-process, by diffing each fresh
# read against the previous one (module-level, not per-symbol/multi-instance -- this process only
# ever runs one live symbol/date at a time, matching every other module-level constant here).
# This is metadata about CHANGE HISTORY, not a cache of the book itself -- _fresh_book_depth still
# reads the checkpoint fresh from disk on every call, unchanged from W2.6 Part A's own principle.
GHOST_SLACK_TICKS = 24
RECENCY_WINDOW_S = 60.0  # a tick must have changed within this long to count as a real candidate
_prev_bid_sizes: Optional[np.ndarray] = None
_prev_ask_sizes: Optional[np.ndarray] = None
_bid_last_changed_ts: Optional[np.ndarray] = None
_ask_last_changed_ts: Optional[np.ndarray] = None


def _update_recency_tracking(bid_sizes: np.ndarray, ask_sizes: np.ndarray) -> None:
    global _prev_bid_sizes, _prev_ask_sizes, _bid_last_changed_ts, _ask_last_changed_ts
    now = time.time()
    if (_prev_bid_sizes is None or _prev_ask_sizes is None
            or _prev_bid_sizes.shape != bid_sizes.shape or _prev_ask_sizes.shape != ask_sizes.shape):
        # First call, or the array shape changed underneath us (e.g. daemon restart with a
        # different N_TICKS) -- nothing is known to have "just changed"; everything starts
        # unattributed (0.0 == never observed to change == correctly excluded as non-recent until
        # the next real diff establishes otherwise, typically the very next poll).
        _bid_last_changed_ts = np.zeros_like(bid_sizes, dtype=np.float64)
        _ask_last_changed_ts = np.zeros_like(ask_sizes, dtype=np.float64)
    else:
        _bid_last_changed_ts[bid_sizes != _prev_bid_sizes] = now
        _ask_last_changed_ts[ask_sizes != _prev_ask_sizes] = now
    _prev_bid_sizes = bid_sizes.copy()
    _prev_ask_sizes = ask_sizes.copy()


def _fresh_book_depth(symbol: str, date: str, last_price: Optional[float] = None,
                       window_ticks: int = bfds.BOOK_WINDOW_TICKS) -> tuple[Optional[dict], str, Optional[str]]:
    """W2.6 Part A (LIVEFIX_DIAGNOSIS.md): reads the v3 checkpoint FRESH from disk every call --
    no caching of any kind in this module, deliberately bypassing book_flow_data_service's own
    _get_book_depth()/_cached_book (read-only desktop source, never modified -- see the
    diagnosis for exactly how that cache can get poisoned to an arbitrarily old session). Also
    validates the loaded checkpoint's OWN symbol/session_date fields against what was requested --
    "never fall back silently to a different date/contract" -- rather than trusting that the
    resolved path always contains what its name implies.

    Returns (book_dict_or_None, status, reason). status is "ok", "unavailable" (no book yet -- a
    fresh session, or a past date with nothing cached; not an error), or "mismatch" (the
    checkpoint exists but doesn't belong to this symbol/date). Never returns a book dict unless
    status == "ok".
    """
    state = level_cache.load_v3_state(symbol, date)
    if state is None:
        return None, "unavailable", f"no checkpoint for {symbol}/{date}"
    if state.get("symbol") != symbol or str(state.get("session_date")) != str(date):
        return None, "mismatch", (
            f"checkpoint identity mismatch: file has symbol={state.get('symbol')!r} "
            f"session_date={state.get('session_date')!r}, expected {symbol!r}/{date!r}"
        )
    bid_sizes, ask_sizes = state.get("bid_sizes"), state.get("ask_sizes")
    if bid_sizes is None or ask_sizes is None:
        return None, "unavailable", "checkpoint missing bid/ask arrays"
    bid_nz = np.nonzero(bid_sizes)[0]
    ask_nz = np.nonzero(ask_sizes)[0]
    if bid_nz.size == 0 and ask_nz.size == 0:
        return None, "unavailable", "checkpoint book empty on both sides"
    _update_recency_tracking(bid_sizes, ask_sizes)
    if last_price is not None:
        # See GHOST_SLACK_TICKS/RECENCY_WINDOW_S's own comments above -- only bound the direction
        # each side can actually be poisoned from, AND require the candidate to have genuinely
        # changed recently (proximity alone was proven live-insufficient: a stale level and a
        # fresh one can sit at the same distance from last_price once price wanders back near it).
        # No last_price (e.g. a brand-new session with no trades yet) falls through to the plain
        # global extremum below -- there's no reference to anchor on, and a session that young has
        # had no time to accumulate a distant ghost anyway.
        ref_tick = int(round(bfl.price_to_tick(np.array([last_price]))[0]))
        now = time.time()
        bid_in_range = bid_nz[bid_nz <= ref_tick + GHOST_SLACK_TICKS]
        ask_in_range = ask_nz[ask_nz >= ref_tick - GHOST_SLACK_TICKS]
        bid_candidates = bid_in_range[(now - _bid_last_changed_ts[bid_in_range]) <= RECENCY_WINDOW_S]
        ask_candidates = ask_in_range[(now - _ask_last_changed_ts[ask_in_range]) <= RECENCY_WINDOW_S]
        best_bid_tick = int(bid_candidates.max()) if bid_candidates.size else None
        best_ask_tick = int(ask_candidates.min()) if ask_candidates.size else None
        if best_bid_tick is None and best_ask_tick is None:
            # Nothing on either side is both near last_price AND recently changed -- correctly "no
            # trustworthy book right now," not a crash on a None mid_tick, and never a silent
            # fall-back to whatever the stale global extremum happens to be.
            return None, "unavailable", (
                f"no bid/ask within {GHOST_SLACK_TICKS} ticks of last_price {last_price} that "
                f"changed in the last {RECENCY_WINDOW_S:.0f}s (checkpoint updated_utc={state.get('updated_utc')})"
            )
    else:
        best_bid_tick = int(bid_nz.max()) if bid_nz.size else None
        best_ask_tick = int(ask_nz.min()) if ask_nz.size else None
    mid_tick = (
        (best_bid_tick + best_ask_tick) // 2 if best_bid_tick is not None and best_ask_tick is not None
        else (best_bid_tick if best_bid_tick is not None else best_ask_tick)
    )
    n_ticks = bid_sizes.shape[0]
    lo = max(0, mid_tick - window_ticks)
    hi = min(n_ticks, mid_tick + window_ticks + 1)
    ticks = np.arange(lo, hi)
    book_ts = None
    try:
        book_ts = datetime.fromisoformat(str(state.get("updated_utc"))).timestamp()
    except (TypeError, ValueError):
        pass

    # MISSION diagnose-session-boundary-book-mixing: GHOST_SLACK_TICKS/RECENCY_WINDOW_S above only
    # ever protected best_bid_tick/best_ask_tick (the window's own center point) -- never the
    # bid_sizes[lo:hi]/ask_sizes[lo:hi] slice actually drawn. Confirmed live, bypassing webbeta
    # entirely: the real production checkpoint for 2026-08-19 (the session that opened with a real
    # 15pt gap, 29561.00 -> 29576.00) still had a dense two-sided ghost cluster sitting at
    # 29569.00-29577.00 -- almost exactly the gap's own price-discovery zone -- hours later: 23 real
    # ask ticks below the true best_bid, 142 real bid ticks above the true best_ask, all well inside
    # the +-100pt drawn window. A resting bid can never legitimately sit above the current ask (nor
    # an ask below the current bid) -- once best_bid_tick/best_ask_tick are themselves trustworthy
    # (already ghost-resistant, above), that's a hard, always-true invariant, not a heuristic: any
    # drawn level on the wrong side of them is guaranteed stale/crossed residue, most commonly left
    # behind by exactly this kind of large, abrupt session-boundary gap.
    drawn_bid_sizes = bid_sizes[lo:hi].copy()
    drawn_ask_sizes = ask_sizes[lo:hi].copy()
    if best_ask_tick is not None:
        drawn_bid_sizes[ticks > best_ask_tick] = 0.0
    if best_bid_tick is not None:
        drawn_ask_sizes[ticks < best_bid_tick] = 0.0

    book = {
        "prices": bfl.tick_to_price(ticks),
        "bid_sizes": drawn_bid_sizes,
        "ask_sizes": drawn_ask_sizes,
        "book_ts": book_ts,
        "best_bid": (float(bfl.tick_to_price(np.array([best_bid_tick]))[0]) if best_bid_tick is not None else None),
        "best_ask": (float(bfl.tick_to_price(np.array([best_ask_tick]))[0]) if best_ask_tick is not None else None),
    }
    return book, "ok", None


def _fresh_book_depth_checked(symbol: str, date: str, last_price: Optional[float],
                               max_mismatch_ticks: int = BOOK_MISMATCH_TICKS) -> tuple[Optional[dict], str, Optional[str]]:
    """W2.6 Part A: adds the sanity guard on top of _fresh_book_depth -- a book that's structurally
    valid AND correctly identified but whose best bid/ask sits implausibly far from the bar
    stream's own last_price is ALSO never rendered ("never render a book that doesn't belong to
    the displayed instrument" covers both a wrong-identity file and a right-identity file with
    implausible content). MISSION diagnose-negative-spread: last_price is now also passed straight
    into _fresh_book_depth itself (see GHOST_SLACK_TICKS' own comment there) -- it anchors the
    best_bid/best_ask derivation, not just this function's own after-the-fact plausibility check."""
    book, status, reason = _fresh_book_depth(symbol, date, last_price)
    if status != "ok" or book is None:
        return None, status, reason
    if last_price is not None and book["best_bid"] is not None and book["best_ask"] is not None:
        mid = (book["best_bid"] + book["best_ask"]) / 2.0
        dist_ticks = abs(last_price - mid) / bfl.TICK
        if dist_ticks > max_mismatch_ticks:
            return None, "mismatch", (
                f"book mid {mid:.2f} is {dist_ticks:.0f} ticks from last_price {last_price:.2f} "
                f"(max {max_mismatch_ticks})"
            )
    return book, "ok", None


def _bar_wire(row: pd.Series) -> dict:
    # W3 Part B: bar_pos == bar_index, always -- no separately-tracked position counter (the old
    # bar_pos_map/next_bar_pos statefulness is exactly the kind of webbeta-only bookkeeping this
    # mission removes; book_index values are already a globally monotonic integer sequence for
    # this service's lifetime, safe to use directly as an x-axis coordinate).
    bi = int(row["bar_index"])
    return {
        "bar_index": bi,
        "bar_pos": bi,
        "px_close": float(row["px_close"]),
        "bar_start_ts_ns": int(row["bar_start_ts_ns"]),
    }


def _cells_wire(cells: pd.DataFrame, state_char: str) -> dict:
    return {
        "bar_idx": [int(x) for x in cells["bar_idx"]],
        "price_level": [float(x) for x in cells["price_level"]],
        "signed_flow": [float(x) for x in cells["signed_flow"]],
        "abs_flow": [float(x) for x in cells["abs_flow"]],
        "bar_state": [state_char] * len(cells),
    }


def _book_wire(book: Optional[dict]) -> Optional[dict]:
    if book is None:
        return None
    # W4.1 Part 2: best_bid/best_ask were already computed by _fresh_book_depth (used for the
    # mismatch-distance guard) but never sent over the wire -- added here to back the "market"
    # (book-mid) indicator, a SEPARATE, continuously-updating signal from the existing last-trade
    # price line. Confirmed by direct grep of rithmic_live_features.py: last_price/close_price is
    # fed EXCLUSIVELY by Vol500BarBuilder.apply_trade() (trade prints only; "OHLC (per split
    # piece, using trade price)"), never by apply_book_obs() (quote/book updates, which only ever
    # touch mlofi_sum/mid_sum/obs_count) -- so last-trade going flat between trades is correct,
    # desktop-matching behavior, not a bug, and is kept exactly as-is. This field is what a
    # "where is the market right now" indicator needs instead.
    mid = (
        (book["best_bid"] + book["best_ask"]) / 2.0
        if book.get("best_bid") is not None and book.get("best_ask") is not None
        else None
    )
    return {
        "prices": [float(x) for x in book["prices"]],
        "bid_sizes": [float(x) for x in book["bid_sizes"]],
        "ask_sizes": [float(x) for x in book["ask_sizes"]],
        "best_bid": book.get("best_bid"),
        "best_ask": book.get("best_ask"),
        "mid": mid,
    }


# MISSION lookback-sessions-and-timestamps: the #lookbackSelect control now selects a number of
# whole trading SESSIONS (1-5, session_boundaries.py) rather than a bar count -- bars are filtered
# by bar_start_ts_ns against the Nth-session-back cutoff (_trim_bars_to_session_cutoff below), not
# sliced to a fixed count. SNAPSHOT_MAX_BARS is kept only as a defensive ceiling (a session-count
# selection can never legitimately produce more bars than this) -- raised from 2000 to 8000 to
# clear the real measured 5-session figure (4,141 bars / 257,954 cells over 2026-08-09..08-13, the
# 5 most recent consecutive real trading sessions available) with headroom, rather than being the
# thing that actually decides how many bars ship.
SNAPSHOT_MAX_BARS = int(os.environ.get("WEBBETA_SNAPSHOT_MAX_BARS", "8000"))

DEFAULT_N_SESSIONS = int(os.environ.get("WEBBETA_DEFAULT_N_SESSIONS", "2"))


def _reference_now(frame: "bfds.ChartFrame") -> datetime:
    """"Now", for session-cutoff purposes, is anchored to the DATA's own latest timestamp -- never
    the wall clock directly. In real live mode these are the same thing (modulo normal poll
    latency), but the two genuinely diverge for the Part D simulator and any future replay-driven
    testing, whose synthetic sessions are dated in the past relative to whatever today actually is
    -- a wall-clock cutoff would filter out 100% of that data regardless of n_sessions, which is
    exactly the regression this function exists to prevent (found directly: test_live_flicker.py
    went from 55+ observed bars to 2 once a real datetime.now() cutoff was tried here). Anchoring
    to the data's own timeline instead makes "last N sessions" well-defined and testable
    independent of calendar today, and is arguably the more correct definition even in live mode
    (immune to any poll-loop stall making the wall clock momentarily run ahead of the data)."""
    candidates = []
    if frame.forming_bar is not None:
        candidates.append(int(frame.forming_bar["bar_start_ts_ns"]))
    if not frame.sealed_bars.empty:
        candidates.append(int(frame.sealed_bars["bar_start_ts_ns"].max()))
    if not candidates:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(max(candidates) / 1_000_000_000, tz=timezone.utc)


def _trim_bars_to_session_cutoff(
    frame: "bfds.ChartFrame", n_sessions: int
) -> tuple[pd.DataFrame, list[dict]]:
    """Filters to bars whose bar_start_ts_ns falls within the last `n_sessions` real trading
    sessions (session_boundaries.py, the same 22:00-21:00 UTC convention the live daemons already
    use), then applies SNAPSHOT_MAX_BARS only as a defensive ceiling on top. Returns (bars,
    session_boundaries) -- the boundaries are shipped in the snapshot so the client can draw
    session-crossing markers on the x-axis without independently recomputing them for the initial
    view (see session_boundaries.py's own docstring for why the client still owns a small local
    copy of this same rule for the continuous live-follow case only)."""
    bars = frame.sealed_bars.sort_values("bar_index").reset_index(drop=True)
    boundaries = session_boundaries.last_n_session_boundaries(n_sessions, now=_reference_now(frame))
    cutoff_ts_ns = boundaries[0]["start_ts_ns"]
    bars = bars[bars["bar_start_ts_ns"] >= cutoff_ts_ns].reset_index(drop=True)
    if len(bars) > SNAPSHOT_MAX_BARS:
        bars = bars.iloc[-SNAPSHOT_MAX_BARS:].reset_index(drop=True)
    return bars, boundaries


def _closed_cells(frame: "bfds.ChartFrame") -> pd.DataFrame:
    """The one authoritative "is this bar actually sealed" signal, matching exactly how the
    desktop's own _bridge_gap_if_needed() (book_flow_data_service.py:228) defines "closed": cells
    whose bar_state != FORMING. frame.last_closed_bar_idx (ChartFrame.__post_init__) is this same
    frame's own max over exactly this set -- never frame.sealed_bars' mere presence, which can
    include a synthetic placeholder row for the bar that is still forming (see this module's own
    docstring for why that distinction is the entire W2.8-W2.14 bug)."""
    cells = frame.sealed_cells
    if cells.empty:
        return cells
    if "bar_state" in cells.columns:
        return cells[cells["bar_state"] != "FORMING"]
    return cells


def _bar_and_cells_for(frame: "bfds.ChartFrame", bar_indices: list[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Shared by the snapshot builder and the seal-message builder (W3 Part B's own invariant:
    "snapshot and live update are produced by ONE shared function"). Returns (bars, cells) for
    exactly the requested bar_index set, sourced directly from frame.sealed_bars/sealed_cells with
    no reconciliation of any kind -- if the desktop's own bridge hasn't spliced a bar's cells in
    yet, neither has this; the desktop's own _poll_data_service() would show the same thing at the
    same instant, and self-corrects the same way (the very next poll, once the service's frame
    actually has it)."""
    bar_set = set(bar_indices)
    bars = frame.sealed_bars[frame.sealed_bars["bar_index"].isin(bar_set)]
    cells = _closed_cells(frame)
    cells = cells[cells["bar_idx"].isin(bar_set)]
    return bars, cells


def _forming_content_sig(frame: "bfds.ChartFrame") -> tuple:
    """W3 Part D gate (d): a pure bandwidth optimization, not a diff of what's semantically "new"
    -- frame.version bumps on essentially every poll (the service's own internal signature covers
    far more than this wire protocol's forming/book fields), so gating the "update" message purely
    on version_changed resends the forming bar's ENTIRE current cell set (up to ~150-200 cells by
    a bar's end) many times a second even when nothing about the forming bar actually changed,
    measured directly at ~54 KB/s against the mission's own 50 KB/s ceiling. This signature answers
    "did the forming bar's own wire-visible content change" cheaply (two sums, not a full frame
    comparison); if it hasn't, the update collapses to a book_update (no cell payload) instead.
    Never affects correctness either way -- a real forming change always differs in at least one
    of these fields, and the worst case of a false "unchanged" is one extra poll's delay before the
    genuinely-changed content ships, never a permanent loss (the very next poll re-checks)."""
    if frame.forming_bar is None:
        return (None, None, None, None)
    cells = frame.forming_cells
    flow_sig = (float(cells["abs_flow"].sum()), float(cells["signed_flow"].sum())) if cells is not None and not cells.empty else None
    return (int(frame.forming_bar["bar_index"]), len(cells) if cells is not None else 0, flow_sig, frame.last_price)


def _forming_payload(frame: "bfds.ChartFrame") -> tuple[Optional[dict], Optional[dict]]:
    """Shared by every message kind that ever mentions the forming bar (snapshot/seal/update) --
    always the service's OWN current frame.forming_bar/frame.forming_cells, complete, with no
    independent re-read and no fallback: book_flow_data_service.get_latest() is a queue pop from a
    background thread that already handles its own torn-read/staleness concerns internally (it is
    not disk I/O on this thread at all), matching exactly how the desktop's _poll_data_service()
    trusts it with zero retry/guard logic of its own."""
    if frame.forming_bar is None:
        return None, None
    forming_bar = _bar_wire(frame.forming_bar)
    forming_cells = None
    if frame.forming_cells is not None and not frame.forming_cells.empty:
        forming_cells = _cells_wire(frame.forming_cells, "F")
    return forming_bar, forming_cells


def build_snapshot(frame: "bfds.ChartFrame", n_sessions: int = DEFAULT_N_SESSIONS) -> dict:
    bars, session_bounds = _trim_bars_to_session_cutoff(frame, n_sessions)
    bar_indices = [int(x) for x in bars["bar_index"]]
    _, cells = _bar_and_cells_for(frame, bar_indices)
    forming_bar, forming_cells = _forming_payload(frame)
    # W2.6 Part A: always a fresh, identity-+-distance-checked read -- never frame.book_prices/
    # book_ts, which ride through book_flow_data_service's own (poisonable) cache. A brand-new
    # client's very first view must never show a stale/mismatched book either.
    book, book_status, book_status_reason = _fresh_book_depth_checked(frame.symbol, frame.date, frame.last_price)
    return {
        "type": "snapshot",
        "version": frame.version,
        "symbol": frame.symbol, "date": frame.date, "depth": frame.depth,
        "last_closed_bar_idx": frame.last_closed_bar_idx,
        "last_price": frame.last_price,
        "book_ts": book["book_ts"] if book else None,
        "n_sessions": n_sessions,
        "session_boundaries": session_bounds,
        "bars": {
            "bar_index": bar_indices,
            "bar_pos": bar_indices,
            "px_close": [float(x) for x in bars["px_close"]],
            "bar_start_ts_ns": [int(x) for x in bars["bar_start_ts_ns"]],
        },
        "cells": _cells_wire(cells, "C"),
        "forming_bar": forming_bar,
        "forming_cells": forming_cells,
        "book": _book_wire(book),
        "book_status": book_status,
        "book_status_reason": book_status_reason,
    }


class ClientQueue:
    """Bounded, coalescing per-client outbox: at most MAXLEN pending messages; pushing past that
    drops the OLDEST pending message, never blocks the producer and never grows unbounded.

    This class has no desktop equivalent -- the desktop IS the single, in-process consumer of
    BookFlowDataService, with no network and no fan-out. It is kept anyway (W3 Part A's own rule
    is "cite the desktop equivalent or delete it" -- this solves a genuinely different problem: N
    independent network clients reading one shared stream, which the desktop's single-process
    design never has to solve at all). Every pushed message is tagged with a per-client, strictly-
    monotonic `seq` (assigned here, at push time, BEFORE a possible drop): if a message is dropped
    by the MAXLEN trim, the client sees a gap in `seq` on the next item it receives, which is the
    signal it uses to request a fresh snapshot -- the only "resync" concept this protocol has left,
    since every message kind is already complete/replace-by-key on its own."""

    MAXLEN = 8

    def __init__(self) -> None:
        self._items: list[dict] = []
        self._event = asyncio.Event()
        self._next_seq = 0

    def push(self, msg: dict) -> None:
        tagged = dict(msg)
        tagged["seq"] = self._next_seq
        self._next_seq += 1
        self._items.append(tagged)
        if len(self._items) > self.MAXLEN:
            self._items.pop(0)
        self._event.set()

    async def get(self) -> dict:
        while not self._items:
            await self._event.wait()
            self._event.clear()
        return self._items.pop(0)

    def pending_count(self) -> int:
        return len(self._items)


class LiveBroadcastHub:
    def __init__(self) -> None:
        self.service: Optional[bfds.BookFlowDataService] = None
        self.clients: dict[int, ClientQueue] = {}
        self._next_id = 0
        self._task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self.last_frame: Optional[bfds.ChartFrame] = None
        self.started_at = time.time()
        self.sandbox_root: Optional[Path] = None
        # W2.9 Part 2: poll-loop health, surfaced via status() so a frozen server is diagnosable
        # with one curl instead of an investigation.
        self.last_poll_ts: Optional[float] = None
        self.frames_published = 0
        self.consecutive_read_errors = 0
        self.last_error: Optional[str] = None
        self.poll_task_restarts = 0
        # W3: replaces LiveWireState entirely -- the only two things this hub needs to remember
        # between polls are "have I already broadcast this exact frame version" (mirrors the
        # desktop's own self._dataservice_last_version) and "what was the newest ACTUALLY sealed
        # bar_idx last time" (frame.last_closed_bar_idx, the sealed_cells-derived signal, never
        # frame.sealed_bars' own presence -- see _closed_cells' docstring).
        self._last_broadcast_version: Optional[int] = None
        self._last_closed_bar_idx_broadcast: Optional[int] = None
        # W3 Part D gate (d): last forming-bar content signature actually SENT in an "update"
        # message -- see _forming_content_sig's own docstring for why this exists (bandwidth only,
        # never a correctness concern).
        self._last_forming_sig: tuple = (None, None, None, None)
        self._last_update_sent_at: float = 0.0
        self._last_book_ts_sent: Optional[float] = None
        self.book_status: Optional[str] = None
        self.book_ts: Optional[float] = None

    def start(self) -> None:
        if self.service is not None:
            return
        sandbox = _maybe_redirect_to_sandbox()
        self.sandbox_root = sandbox
        # MISSION lookback-sessions-and-timestamps: previous_sessions=1 (current + 1 prior day)
        # was enough to back the old bar-count lookback (max 2000, comfortably inside 2 sessions'
        # worth of bars), but structurally caps how far back _trim_bars_to_session_cutoff can ever
        # filter -- the #lookbackSelect control now goes up to 5 sessions, so the underlying
        # service must actually hold that much (current + 4 prior days). This only costs a
        # filesystem read at session rollover (book_flow_data_service.py's own
        # _rebuild_previous_session_if_rolled docstring: "the ONLY place this service ever does a
        # filesystem date-discovery walk -- exactly once per detected rollover, never once per
        # poll"), not a per-poll cost.
        self.service = bfds.BookFlowDataService(
            SYMBOL, "", DEPTH, show_forming=True, poll_interval_s=POLL_INTERVAL_S,
            live_latest=True, previous_sessions=4,
        )
        self.service.start()
        self._task = asyncio.ensure_future(self._poll_loop_supervised())
        self._keepalive_task = asyncio.ensure_future(self._keepalive_loop())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            self._keepalive_task = None
        if self.service is not None:
            self.service.stop()
            self.service = None

    def register(self, n_sessions: int = DEFAULT_N_SESSIONS) -> tuple[int, ClientQueue]:
        cid = self._next_id
        self._next_id += 1
        q = ClientQueue()
        self.clients[cid] = q
        if self.last_frame is not None:
            q.push(build_snapshot(self.last_frame, n_sessions=n_sessions))
        return cid, q

    def unregister(self, cid: int) -> None:
        self.clients.pop(cid, None)

    def snapshot_now(self, n_sessions: int = DEFAULT_N_SESSIONS) -> Optional[dict]:
        if self.last_frame is None:
            return None
        return build_snapshot(self.last_frame, n_sessions=n_sessions)

    def status(self) -> dict:
        book_age = None
        market_age = None
        if self.last_frame is not None:
            if self.book_ts is not None:
                book_age = time.time() - self.book_ts
            # Market/connection staleness (distinct from the book panel's own BOOK_STALE_SECS,
            # which is specifically about resting-depth freshness): the daemon's heartbeat reports
            # the real-world timestamp of the latest CLOSED bar system-wide.
            hb = self.last_frame.heartbeat or {}
            ts_str = hb.get("latest_closed_timestamp_utc")
            if ts_str:
                try:
                    ts = pd.Timestamp(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.tz_localize("UTC")
                    market_age = (pd.Timestamp.now(tz="UTC") - ts).total_seconds()
                except (ValueError, TypeError):
                    pass
        seconds_since_last_poll = (time.time() - self.last_poll_ts) if self.last_poll_ts is not None else None
        degraded = seconds_since_last_poll is None or seconds_since_last_poll > POLL_DEGRADED_THRESHOLD_S
        return {
            "has_frame": self.last_frame is not None,
            "date": self.last_frame.date if self.last_frame else None,
            "last_closed_bar_idx": self.last_frame.last_closed_bar_idx if self.last_frame else None,
            "book_age_s": book_age,
            "book_status": self.book_status,
            "market_age_s": market_age,
            "market_stale_secs": LIVE_STALE_SECS,
            "n_clients": len(self.clients),
            "sandbox": str(self.sandbox_root) if self.sandbox_root else None,
            "last_poll_ts": self.last_poll_ts,
            "seconds_since_last_poll": seconds_since_last_poll,
            "frames_published": self.frames_published,
            "consecutive_read_errors": self.consecutive_read_errors,
            "last_error": self.last_error,
            "poll_task_restarts": self.poll_task_restarts,
            "degraded": degraded,
        }

    def _build_messages(self, frame: "bfds.ChartFrame") -> list[dict]:
        """W3 Part B: THE INVARIANT -- every entity mentioned here (bars/cells/forming) is built by
        the exact same _bar_and_cells_for/_forming_payload helpers build_snapshot() uses. A newly
        sealed bar's cells are never partially assembled here and fully assembled there; there is
        only one way to turn a bar_idx into its wire cells, used everywhere.

        Mirrors book_flow_chart_v3.py's own _poll_data_service() decision structure exactly:
        "frame is None or frame.version == last_version -> book-only recheck; else -> full
        re-render from frame" -- the "full re-render" here is a seal message per bar that newly
        finished (frame.last_closed_bar_idx advanced -- the sealed_cells-derived signal, matching
        _bridge_gap_if_needed's own definition of "closed", see _closed_cells) plus an update
        message carrying the (possibly new) forming bar, otherwise just an update."""
        msgs: list[dict] = []
        book, book_status, book_status_reason = _fresh_book_depth_checked(
            frame.symbol, frame.date, frame.last_price)
        self.book_status = book_status
        self.book_ts = book["book_ts"] if book else None
        book_w = _book_wire(book)

        version_changed = frame.version != self._last_broadcast_version
        newly_closed: list[int] = []
        if version_changed and frame.last_closed_bar_idx is not None:
            prev = self._last_closed_bar_idx_broadcast
            if prev is None:
                # First frame this hub has ever broadcast -- nothing to seal retroactively (the
                # snapshot each client already received on register() covers history); only bars
                # that close AFTER this point are new seal events. Must still initialize the
                # tracker here (not just inside the "if newly_closed" branch below) -- otherwise
                # prev stays None forever and no seal ever fires again.
                self._last_closed_bar_idx_broadcast = frame.last_closed_bar_idx
            elif frame.last_closed_bar_idx > prev:
                newly_closed = list(range(prev + 1, frame.last_closed_bar_idx + 1))

        forming_bar, forming_cells = _forming_payload(frame)
        forming_sig = _forming_content_sig(frame)
        # W4.1 Part 1: log EVERY poll's decision inputs, unconditionally -- the live evidence this
        # mission needs (a bar_roll/update/book_update decision trail matching a screenshot showing
        # a frozen price line + static book while cells were visibly forming) can only come from
        # seeing what every single poll actually decided, not from re-reading this function's logic.
        sig_changed = forming_sig != self._last_forming_sig
        throttle_elapsed = time.time() - self._last_update_sent_at
        throttle_ok = throttle_elapsed >= UPDATE_MIN_INTERVAL_S
        book_ts_changed = self.book_ts != self._last_book_ts_sent
        log.info(
            "[live_bridge] Part 1 poll: version_changed=%s newly_closed=%s sig_changed=%s "
            "throttle_elapsed=%.3f throttle_ok=%s book_ts=%s last_book_ts_sent=%s "
            "book_ts_changed=%s last_price=%s forming_bar_idx=%s forming_cell_count=%s",
            version_changed, newly_closed, sig_changed, throttle_elapsed, throttle_ok,
            self.book_ts, self._last_book_ts_sent, book_ts_changed, frame.last_price,
            int(frame.forming_bar["bar_index"]) if frame.forming_bar is not None else None,
            len(frame.forming_cells) if frame.forming_cells is not None else None,
        )
        if newly_closed:
            bars, cells = _bar_and_cells_for(frame, newly_closed)
            bars_by_idx = bars.set_index("bar_index", drop=False)
            # MISSION diagnose-bar-disappears-at-seal Fix 2: a REAL, confirmed regression --
            # _last_closed_bar_idx_broadcast used to advance to frame.last_closed_bar_idx
            # unconditionally below, even for a bar_idx skipped this poll because its metadata row
            # hadn't landed in frame.sealed_bars yet (a normal, brief lag behind sealed_cells,
            # exactly the race ROLLDELTA_DIAGNOSIS.md's original _reconcile_sealed_bars() fix
            # existed to cover -- deleted in the W3 rewrite with nothing put back in its place).
            # The old comment here claimed "the next poll's newly_closed range starts from this
            # same prev, so nothing is skipped" -- false: prev was already advanced past it, so it
            # was silently and PERMANENTLY skipped, never retried. Fixed: only advance the tracker
            # through the highest bar_idx actually broadcast, stopping at the first gap, so
            # anything at or after a gap stays eligible for newly_closed on the very next poll.
            # (A bar found AFTER a gap in the same batch is still sent now -- no reason to
            # withhold data that's actually ready -- it may just get harmlessly re-sent once the
            # gap closes; the client's seal handler is already replace-by-key.)
            sendable = [b for b in newly_closed if b in bars_by_idx.index]
            last_sendable = sendable[-1] if sendable else None
            broadcast_through = prev
            hit_gap = False
            for bar_idx in newly_closed:
                if bar_idx not in bars_by_idx.index:
                    hit_gap = True
                    continue
                row = bars_by_idx.loc[bar_idx]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]
                bar_cells = cells[cells["bar_idx"] == bar_idx]
                is_last = (bar_idx == last_sendable)
                msg = {
                    "type": "seal",
                    "version": frame.version,
                    "sealed_bar": _bar_wire(row),
                    "sealed_cells": _cells_wire(bar_cells, "C"),
                    "last_closed_bar_idx": frame.last_closed_bar_idx,
                    "last_price": (
                        bfds._last_price_from_cells(bar_cells) if not bar_cells.empty else frame.last_price
                    ),
                    "book": book_w, "book_ts": self.book_ts, "book_status": book_status, "book_status_reason": book_status_reason,
                }
                # Only the LAST seal message ACTUALLY SENT in this poll's batch carries the
                # forming_bar/forming_cells KEYS AT ALL -- avoids sending the same forming snapshot
                # N times when a poll batches multiple rolls at once (a resync catching up, a slow
                # poll cycle), while keeping "genuinely no forming bar" (a real None) distinguishable
                # from "this message says nothing about forming state" (key omitted entirely) --
                # the client must be able to tell those apart, or it would wrongly clear a real
                # forming bar on every non-last message in a batch.
                if is_last:
                    msg["forming_bar"] = forming_bar
                    msg["forming_cells"] = forming_cells
                msgs.append(msg)
                if not hit_gap:
                    broadcast_through = bar_idx
            self._last_closed_bar_idx_broadcast = broadcast_through
            self._last_forming_sig = forming_sig
            self._last_book_ts_sent = self.book_ts
        elif (
            version_changed and forming_sig != self._last_forming_sig
            and time.time() - self._last_update_sent_at >= UPDATE_MIN_INTERVAL_S
        ):
            # W3 Part D gate (d): measured directly -- the content-signature check above alone
            # only got bandwidth from ~54 KB/s to ~50 KB/s (still over the 50 KB/s budget) because
            # abs_flow/signed_flow legitimately shift on nearly every poll in an active market; the
            # signature check correctly says "yes, this really is new data" almost every time. The
            # actual cost driver is FREQUENCY, not redundancy: resending the forming bar's entire,
            # growing cell set (up to ~150-200 cells) at the service's own ~0.3s poll cadence is
            # simply expensive regardless of bar-roll cadence (RTH or overnight -- this cost comes
            # from POLL_INTERVAL_S, not BAR_ROLL_INTERVAL_S). Rate-limited here rather than tuning
            # cell-payload content: every update actually sent is still the forming bar's real,
            # complete current state (never a partial/stale one) -- this only controls how often a
            # fresh one goes out, trading a bounded worst-case latency (UPDATE_MIN_INTERVAL_S) for
            # roughly an order of magnitude less bandwidth. A "seal" (rare, once per bar, high
            # value) is never throttled.
            self._last_forming_sig = forming_sig
            self._last_update_sent_at = time.time()
            self._last_book_ts_sent = self.book_ts
            msgs.append({
                "type": "update",
                "version": frame.version,
                "forming_bar": forming_bar, "forming_cells": forming_cells,
                "last_price": frame.last_price,
                "book": book_w, "book_ts": self.book_ts, "book_status": book_status, "book_status_reason": book_status_reason,
            })
        elif self.book_ts != self._last_book_ts_sent:
            # W2.8 Part B, still valid under W3: reached either when the service saw no bars/
            # cells-level change this poll at all, OR when it did but the forming bar's own
            # content is unchanged/still within its rate-limit window (the elif above) -- either
            # way, the book itself is read fresh every single call (never cached) and can move
            # independently, so it's still worth sending on its own; the desktop's own
            # _poll_data_service() calls _update_order_book_panel() unconditionally on its
            # equivalent "nothing else changed" branch too.
            #
            # W3 Part D gate (d): the book_ts gate is new -- measured directly, this branch was
            # the single largest cost (~32 of ~50 KB/s measured) because it fired on essentially
            # EVERY poll with a full, freshly-read book payload (801 ticks x 3 arrays, ~15-16 KB
            # as JSON) regardless of whether the book had actually moved since the last one sent.
            # book_ts is the daemon's own checkpoint timestamp (~2s cycle) -- gating on it changing
            # sends a fresh book only about as often as the underlying data source itself updates,
            # never a stale one (an unchanged book_ts really does mean an unchanged book).
            self._last_book_ts_sent = self.book_ts
            msgs.append({
                "type": "book_update",
                "version": frame.version,
                "book": book_w, "book_ts": self.book_ts, "book_status": book_status, "book_status_reason": book_status_reason,
            })
        self._last_broadcast_version = frame.version
        log.info(
            "[live_bridge] Part 1 poll result: emitted=%s",
            [(m["type"], m.get("last_price"), m.get("book_ts")) for m in msgs] if msgs else "NOTHING",
        )
        return msgs

    async def _poll_loop_supervised(self) -> None:
        """W2.9 Part 2: the loop must not be able to stop. _poll_loop() (below) already guards its
        own body with a try/except so an exception INSIDE one poll iteration can't kill the task --
        this wraps the TASK itself: if _poll_loop ever exits for any other reason, that is logged
        with a full traceback and a fresh loop is started after a short backoff."""
        backoff = 1.0
        while True:
            try:
                await self._poll_loop()
                log.error("[live_bridge] _poll_loop returned without raising (should never happen) "
                          "-- restarting in %.1fs", backoff)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("[live_bridge] _poll_loop task died -- restarting in %.1fs", backoff)
            self.poll_task_restarts += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, 30.0)

    async def _poll_loop(self) -> None:
        assert self.service is not None
        while True:
            if os.environ.get("WEBBETA_TEST_KILL_POLL_LOOP_ONCE") == "1":
                os.environ["WEBBETA_TEST_KILL_POLL_LOOP_ONCE"] = "0"
                raise RuntimeError("W2.9 Part 4 gate (c): deliberate test-injected crash")
            self.last_poll_ts = time.time()
            try:
                # W3: get_latest() is a plain queue pop from a background thread the service owns
                # (book_flow_data_service.BookFlowDataService.get_latest -> self._queue.get) --
                # it does its own file I/O and torn-read handling entirely off this thread, so
                # there is nothing here for a retry loop to meaningfully protect against (the
                # desktop's own _poll_data_service() calls it with no try/except at all). Offloaded
                # to a worker thread anyway purely because pandas work on a ~254k-row frame is not
                # free, and this must never stall _keepalive_loop or /status on this same event loop.
                frame = await asyncio.to_thread(self.service.get_latest)
                if frame is not None:
                    self.last_frame = frame
                    msgs = self._build_messages(frame)
                    for q in self.clients.values():
                        for m in msgs:
                            q.push(m)
                    self.frames_published += 1
                self.consecutive_read_errors = 0
                self.last_error = None
            except Exception as exc:
                self.consecutive_read_errors += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("[live_bridge] _poll_loop: swallowed exception (consecutive=%d), "
                               "continuing", self.consecutive_read_errors)
            await asyncio.sleep(BROADCAST_INTERVAL_S)

    async def _keepalive_loop(self) -> None:
        """W2.7 Part B: pushed to every client every KEEPALIVE_INTERVAL_S regardless of market
        activity -- lets the client tell "quiet market, healthy pipe" apart from "pipe silently
        died" without guessing from real-data cadence alone."""
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL_S)
            for q in self.clients.values():
                q.push({"type": "keepalive", "t": time.time()})
