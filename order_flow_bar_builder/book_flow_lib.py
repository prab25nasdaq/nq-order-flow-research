#!/usr/bin/env python3
"""book_flow_lib.py — TRUE BOOK FLOW shared library.

SHADOW/RESEARCH ONLY — read-only consumer of:
  - Rithmic raw capture files (bid_quote_updates.ndjson / ask_quote_updates.ndjson)
  - parsed vol500 bars (NQU6_vol500.ndjsonl)
  - projected-levels CSV (NQM6 -> NQU6)

Does NOT touch the parser, scheduler, model artifacts, or trading flags.

Core job: reconstruct the live limit-order book from the raw per-price-level
update stream and derive ORDER-FLOW CANDLES for the top-N book depth
(N in 5 / 10 / 15 / 20), per vol500 bar.

Signed-flow convention (matches live_parser/include/ofi_math.h compute_raw_ofi):
  bid size increase  -> +delta   (bid add / liquidity added)
  bid size decrease  -> -delta   (bid pull)
  ask size increase  -> -delta   (ask add / liquidity added)
  ask size decrease  -> +delta   (ask pull, i.e. -delta is negative -> contributes +)
"""
from __future__ import annotations

import json
import pickle
import threading
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import numba


# ── constants ──────────────────────────────────────────────────────────────── #
TICK = 0.25
PRICE_MIN = 20000.0
PRICE_MAX = 40000.0
N_TICKS = int(round((PRICE_MAX - PRICE_MIN) / TICK))  # 80_000
DEPTHS = np.array([5, 10, 15, 20], dtype=np.int64)
N_DEPTHS = int(DEPTHS.shape[0])
EPS = 1e-9

RAW_BASE = Path("/home/prabh/OFI_Live_Data/Rithmic_Raw")
FEATURES_BASE = Path("/home/prabh/OFI_Live_Features")
CACHE_DIR = Path(__file__).resolve().parent / "cache"


def price_to_tick(price: np.ndarray) -> np.ndarray:
    return np.round((price - PRICE_MIN) / TICK).astype(np.int64)


def tick_to_price(tick: np.ndarray | int | float) -> np.ndarray:
    return np.asarray(tick) * TICK + PRICE_MIN


# ── Fenwick tree (numba) ──────────────────────────────────────────────────── #
@numba.njit(cache=True, inline="always")
def _fen_update(tree: np.ndarray, i: int, delta: int) -> None:
    n = tree.shape[0] - 1
    i += 1
    while i <= n:
        tree[i] += delta
        i += i & (-i)


@numba.njit(cache=True, inline="always")
def _fen_prefix(tree: np.ndarray, i: int) -> int:
    if i < 0:
        return 0
    i += 1
    s = 0
    while i > 0:
        s += tree[i]
        i -= i & (-i)
    return s


# ── Core event processor (numba) ─────────────────────────────────────────── #
@numba.njit(cache=True)
def _process_events(
    ts: np.ndarray, side: np.ndarray, tick: np.ndarray, size: np.ndarray,
    bar_end_ts: np.ndarray,
    bid_size_arr: np.ndarray, bid_fen: np.ndarray,
    ask_size_arr: np.ndarray, ask_fen: np.ndarray,
    depths: np.ndarray,
    cum_bar: np.ndarray, of_high_cur: np.ndarray, of_low_cur: np.ndarray,
    cum_sess: np.ndarray, sess_open_cur: np.ndarray,
    sess_high_cur: np.ndarray, sess_low_cur: np.ndarray,
    bid_add_cur: np.ndarray, bid_pull_cur: np.ndarray,
    ask_add_cur: np.ndarray, ask_pull_cur: np.ndarray,
    abs_flow_cur: np.ndarray,
):
    """Process a time-sorted batch of book-update events, mutating the
    book/accumulator state arrays in place and finalizing any vol500 bars
    whose `bar_end_ts` is reached. Returns (bars_filled, out_arrays...).

    `bar_end_ts` holds the end timestamps of bars NOT YET finalized
    (oldest-first). At most `len(bar_end_ts)` bars can be finalized in one
    call; the trailing "in-progress" bar's live state is left in
    cum_bar/of_high_cur/.../abs_flow_cur for the caller to read.
    """
    n_d = depths.shape[0]
    M = bar_end_ts.shape[0]
    n_ticks = bid_size_arr.shape[0]

    out_of_high = np.zeros((M, n_d))
    out_of_low = np.zeros((M, n_d))
    out_of_close = np.zeros((M, n_d))
    out_sess_open = np.zeros((M, n_d))
    out_sess_high = np.zeros((M, n_d))
    out_sess_low = np.zeros((M, n_d))
    out_sess_close = np.zeros((M, n_d))
    out_bid_add = np.zeros((M, n_d))
    out_bid_pull = np.zeros((M, n_d))
    out_ask_add = np.zeros((M, n_d))
    out_ask_pull = np.zeros((M, n_d))
    out_abs_flow = np.zeros((M, n_d))

    bar_i = 0

    for ei in range(ts.shape[0]):
        t = ts[ei]
        while bar_i < M and t >= bar_end_ts[bar_i]:
            for d in range(n_d):
                out_of_high[bar_i, d] = of_high_cur[d]
                out_of_low[bar_i, d] = of_low_cur[d]
                out_of_close[bar_i, d] = cum_bar[d]
                out_sess_open[bar_i, d] = sess_open_cur[d]
                out_sess_high[bar_i, d] = sess_high_cur[d]
                out_sess_low[bar_i, d] = sess_low_cur[d]
                out_sess_close[bar_i, d] = cum_sess[d]
                out_bid_add[bar_i, d] = bid_add_cur[d]
                out_bid_pull[bar_i, d] = bid_pull_cur[d]
                out_ask_add[bar_i, d] = ask_add_cur[d]
                out_ask_pull[bar_i, d] = ask_pull_cur[d]
                out_abs_flow[bar_i, d] = abs_flow_cur[d]
                # reset accumulators for the next bar
                cum_bar[d] = 0.0
                of_high_cur[d] = 0.0
                of_low_cur[d] = 0.0
                sess_open_cur[d] = cum_sess[d]
                sess_high_cur[d] = cum_sess[d]
                sess_low_cur[d] = cum_sess[d]
                bid_add_cur[d] = 0.0
                bid_pull_cur[d] = 0.0
                ask_add_cur[d] = 0.0
                ask_pull_cur[d] = 0.0
                abs_flow_cur[d] = 0.0
            bar_i += 1

        ti = tick[ei]
        if ti < 0 or ti >= n_ticks:
            continue
        sz = size[ei]

        if side[ei] == 0:  # ── bid ──────────────────────────────────────────
            old = bid_size_arr[ti]
            if sz == old:
                continue
            delta = sz - old
            if old > 0.0:
                total_before = _fen_prefix(bid_fen, n_ticks - 1)
                rank_before = total_before - _fen_prefix(bid_fen, ti - 1)
            else:
                rank_before = -1
            if sz > 0.0 and old == 0.0:
                _fen_update(bid_fen, ti, 1)
            elif sz <= 0.0 and old > 0.0:
                _fen_update(bid_fen, ti, -1)
            bid_size_arr[ti] = sz if sz > 0.0 else 0.0
            if sz > 0.0:
                total_after = _fen_prefix(bid_fen, n_ticks - 1)
                rank_after = total_after - _fen_prefix(bid_fen, ti - 1)
            else:
                rank_after = -1
            is_bid = True
            contribution = delta
        else:  # ── ask ─────────────────────────────────────────────────────
            old = ask_size_arr[ti]
            if sz == old:
                continue
            delta = sz - old
            if old > 0.0:
                rank_before = _fen_prefix(ask_fen, ti)
            else:
                rank_before = -1
            if sz > 0.0 and old == 0.0:
                _fen_update(ask_fen, ti, 1)
            elif sz <= 0.0 and old > 0.0:
                _fen_update(ask_fen, ti, -1)
            ask_size_arr[ti] = sz if sz > 0.0 else 0.0
            if sz > 0.0:
                rank_after = _fen_prefix(ask_fen, ti)
            else:
                rank_after = -1
            is_bid = False
            contribution = -delta

        if rank_before > 0 and rank_after > 0:
            min_rank = rank_before if rank_before < rank_after else rank_after
        elif rank_before > 0:
            min_rank = rank_before
        else:
            min_rank = rank_after

        abs_c = contribution if contribution >= 0.0 else -contribution
        for d in range(n_d):
            if min_rank <= depths[d]:
                cum_bar[d] += contribution
                if cum_bar[d] > of_high_cur[d]:
                    of_high_cur[d] = cum_bar[d]
                if cum_bar[d] < of_low_cur[d]:
                    of_low_cur[d] = cum_bar[d]
                cum_sess[d] += contribution
                if cum_sess[d] > sess_high_cur[d]:
                    sess_high_cur[d] = cum_sess[d]
                if cum_sess[d] < sess_low_cur[d]:
                    sess_low_cur[d] = cum_sess[d]
                abs_flow_cur[d] += abs_c
                if is_bid:
                    if delta > 0.0:
                        bid_add_cur[d] += delta
                    else:
                        bid_pull_cur[d] += -delta
                else:
                    if delta > 0.0:
                        ask_add_cur[d] += delta
                    else:
                        ask_pull_cur[d] += -delta

    return (bar_i, out_of_high, out_of_low, out_of_close,
            out_sess_open, out_sess_high, out_sess_low, out_sess_close,
            out_bid_add, out_bid_pull, out_ask_add, out_ask_pull, out_abs_flow)


# ── Raw file reading ──────────────────────────────────────────────────────── #
def read_quote_updates(path: Path, start_offset: int = 0,
                        max_bytes: Optional[int] = None) -> tuple[pd.DataFrame, int]:
    """Read complete NDJSON lines starting at byte offset `start_offset`.

    Returns (DataFrame[event_ts_ns, price, size_eff], new_offset). Never
    returns a partial trailing line, so callers can resume safely from
    `new_offset` once the writer appends more data.
    """
    cols = ["event_ts_ns", "price", "size_eff"]
    try:
        with open(path, "rb") as f:
            f.seek(start_offset)
            data = f.read(max_bytes) if max_bytes else f.read()
    except OSError:
        return pd.DataFrame(columns=cols), start_offset
    if not data:
        return pd.DataFrame(columns=cols), start_offset
    last_nl = data.rfind(b"\n")
    if last_nl < 0:
        return pd.DataFrame(columns=cols), start_offset
    chunk = data[:last_nl + 1]
    new_offset = start_offset + last_nl + 1
    ts_list, px_list, sz_list = [], [], []
    for line in chunk.split(b"\n"):
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if not r.get("price_valid", True):
            continue
        sz = r.get("size", 0.0) if r.get("size_valid", True) else 0.0
        ts_list.append(r.get("event_ts_ns", 0))
        px_list.append(r.get("price", 0.0))
        sz_list.append(float(sz))
    df = pd.DataFrame({"event_ts_ns": ts_list, "price": px_list, "size_eff": sz_list})
    return df, new_offset


def merge_events(bid_df: pd.DataFrame, ask_df: pd.DataFrame) -> tuple[np.ndarray, ...]:
    """Time-sort-merge bid/ask update batches into (ts, side, tick, size)."""
    n_b, n_a = len(bid_df), len(ask_df)
    ts = np.concatenate([
        bid_df["event_ts_ns"].to_numpy(dtype=np.int64) if n_b else np.empty(0, dtype=np.int64),
        ask_df["event_ts_ns"].to_numpy(dtype=np.int64) if n_a else np.empty(0, dtype=np.int64),
    ])
    side = np.concatenate([np.zeros(n_b, dtype=np.int8), np.ones(n_a, dtype=np.int8)])
    price = np.concatenate([
        bid_df["price"].to_numpy(dtype=np.float64) if n_b else np.empty(0),
        ask_df["price"].to_numpy(dtype=np.float64) if n_a else np.empty(0),
    ])
    size = np.concatenate([
        bid_df["size_eff"].to_numpy(dtype=np.float64) if n_b else np.empty(0),
        ask_df["size_eff"].to_numpy(dtype=np.float64) if n_a else np.empty(0),
    ])
    order = np.argsort(ts, kind="stable")
    return ts[order], side[order], price_to_tick(price[order]), size[order]


# ── date/session discovery (Step 3 Phase 1) ─────────────────────────────── #
# Self-contained duplicates of book_flow_cache_daemon._candidate_raw_dates/_candidate_feature_dates
# rather than importing cache_daemon here -- cache_daemon already imports THIS module, so the
# reverse import would be circular. Both sides read the same RAW_BASE/FEATURES_BASE directories;
# there is nothing cache_daemon-specific in the logic being duplicated.
def _candidate_raw_dates(symbol: str) -> list[str]:
    out: list[str] = []
    try:
        for p in RAW_BASE.iterdir():
            if p.is_dir() and (p / symbol).is_dir():
                out.append(p.name)
    except OSError:
        pass
    return sorted(set(out))


def _candidate_feature_dates(symbol: str) -> list[str]:
    out: list[str] = []
    try:
        for p in FEATURES_BASE.iterdir():
            if p.is_dir() and (p / f"{symbol}_vol500.ndjsonl").exists():
                out.append(p.name)
    except OSError:
        pass
    return sorted(set(out))


def available_dates_for_symbol(symbol: str) -> list[str]:
    """A real filesystem walk (two iterdir() passes) -- callers that poll frequently (the chart's
    render loop, the data service) MUST cache this and only call it when a targeted, cheap signal
    (e.g. the heartbeat's active_date changing) indicates the answer might have changed. Never
    call this unconditionally on every poll -- see book_flow_data_service.py's
    BookFlowDataService for the caching pattern this was extracted for (Step 3 Phase 1,
    P95_DECISION.md item 1)."""
    raw_dates = _candidate_raw_dates(symbol)
    feature_dates = _candidate_feature_dates(symbol)
    return sorted(set(raw_dates) & set(feature_dates))


def context_dates_for(symbol: str, active_date: str, previous_sessions: int) -> list[str]:
    """Same caching caveat as available_dates_for_symbol() above -- this calls it internally."""
    dates = available_dates_for_symbol(symbol)
    if active_date not in dates:
        dates.append(active_date)
        dates = sorted(set(dates))
    try:
        active_i = dates.index(active_date)
    except ValueError:
        return [active_date]
    start = max(0, active_i - max(0, int(previous_sessions)))
    return dates[start:active_i + 1]


# ── vol500 bar loader ────────────────────────────────────────────────────── #
def load_vol500_bars(path: Path) -> pd.DataFrame:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for c in ("bar_start_ts_ns", "bar_end_ts_ns"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["bar_start_ts_ns", "bar_end_ts_ns"]).reset_index(drop=True)
    df["bar_start_ts_ns"] = df["bar_start_ts_ns"].astype("int64")
    df["bar_end_ts_ns"] = df["bar_end_ts_ns"].astype("int64")
    if "bar_index" not in df.columns:
        df["bar_index"] = np.arange(len(df))
    return df


def _rows_to_vol500_df(rows: list) -> pd.DataFrame:
    """Shared post-processing so the incremental and full-parse paths in
    load_vol500_bars_incremental() produce byte-for-byte identical output to load_vol500_bars()."""
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for c in ("bar_start_ts_ns", "bar_end_ts_ns"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["bar_start_ts_ns", "bar_end_ts_ns"]).reset_index(drop=True)
    df["bar_start_ts_ns"] = df["bar_start_ts_ns"].astype("int64")
    df["bar_end_ts_ns"] = df["bar_end_ts_ns"].astype("int64")
    if "bar_index" not in df.columns:
        df["bar_index"] = np.arange(len(df))
    return df


_vol500_bars_cache: dict[str, tuple[int, list, pd.DataFrame]] = {}
_vol500_bars_cache_lock = threading.Lock()


def load_vol500_bars_incremental(path: Path) -> pd.DataFrame:
    """Step 2.5 Part C (see P95_FORENSICS.md): same output as load_vol500_bars(path), but caches
    the parsed rows keyed by file identity and only re-parses bytes appended since the last call
    for the same path, instead of re-parsing the entire (append-only, ever-growing) ndjsonl file
    from scratch on every call. Does NOT modify load_vol500_bars() itself -- the cache daemon and
    build_book_flow_level_cache.py keep calling the original, unmodified function and are
    completely unaffected by this addition. Chart-only optimization (see _load_bars() in
    book_flow_chart_v3.py and book_flow_data_service.py, the two callers this was added for).

    Safety: falls back to a full reparse whenever anything is ambiguous (file shrank/rotated, a
    line at the cached boundary fails to parse, or any other error) -- this is a cache for a
    strictly append-only file, never a source of staleness beyond "one poll cycle behind on a
    read-only chart display," and it self-heals on the next call either way.
    """
    path = Path(path)
    key = str(path)
    st = path.stat()

    with _vol500_bars_cache_lock:
        cached = _vol500_bars_cache.get(key)

    if cached is not None:
        c_size, c_rows, c_df = cached
        if st.st_size == c_size:
            return c_df
        if st.st_size > c_size:
            try:
                with open(path, "rb") as f:
                    f.seek(c_size)
                    tail = f.read().decode("utf-8")
                new_rows = []
                for line in tail.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    new_rows.append(json.loads(line))
                combined_rows = c_rows + new_rows
                df = _rows_to_vol500_df(combined_rows)
                with _vol500_bars_cache_lock:
                    _vol500_bars_cache[key] = (st.st_size, combined_rows, df)
                return df
            except Exception:
                pass  # ambiguous append or a bad line at the boundary -- fall through to full reparse

    # First call for this path, or a fallback from the branch above (shrink/rotation/any error).
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    df = _rows_to_vol500_df(rows)
    with _vol500_bars_cache_lock:
        _vol500_bars_cache[key] = (st.st_size, rows, df)
    return df


# ── BookFlowState — persistent reconstruction state ─────────────────────── #
class BookFlowState:
    """All mutable state needed to incrementally reconstruct the book and
    accumulate order-flow candles. Picklable for checkpointing."""

    def __init__(self):
        self.bid_size_arr = np.zeros(N_TICKS, dtype=np.float64)
        self.bid_fen = np.zeros(N_TICKS + 1, dtype=np.int64)
        self.ask_size_arr = np.zeros(N_TICKS, dtype=np.float64)
        self.ask_fen = np.zeros(N_TICKS + 1, dtype=np.int64)

        self.cum_bar = np.zeros(N_DEPTHS)
        self.of_high_cur = np.zeros(N_DEPTHS)
        self.of_low_cur = np.zeros(N_DEPTHS)
        self.cum_sess = np.zeros(N_DEPTHS)
        self.sess_open_cur = np.zeros(N_DEPTHS)
        self.sess_high_cur = np.zeros(N_DEPTHS)
        self.sess_low_cur = np.zeros(N_DEPTHS)
        self.bid_add_cur = np.zeros(N_DEPTHS)
        self.bid_pull_cur = np.zeros(N_DEPTHS)
        self.ask_add_cur = np.zeros(N_DEPTHS)
        self.ask_pull_cur = np.zeros(N_DEPTHS)
        self.abs_flow_cur = np.zeros(N_DEPTHS)

        self.bid_offset = 0
        self.ask_offset = 0
        self.n_bars_done = 0

    def process(self, bid_df: pd.DataFrame, ask_df: pd.DataFrame,
                bar_end_ts_remaining: np.ndarray) -> tuple[int, dict]:
        ts, side, tick, size = merge_events(bid_df, ask_df)
        result = _process_events(
            ts, side, tick, size, bar_end_ts_remaining,
            self.bid_size_arr, self.bid_fen, self.ask_size_arr, self.ask_fen,
            DEPTHS,
            self.cum_bar, self.of_high_cur, self.of_low_cur,
            self.cum_sess, self.sess_open_cur, self.sess_high_cur, self.sess_low_cur,
            self.bid_add_cur, self.bid_pull_cur, self.ask_add_cur, self.ask_pull_cur,
            self.abs_flow_cur,
        )
        (bars_filled, of_high, of_low, of_close, sess_open, sess_high, sess_low,
         sess_close, bid_add, bid_pull, ask_add, ask_pull, abs_flow) = result
        out = {
            "of_high": of_high[:bars_filled], "of_low": of_low[:bars_filled],
            "of_close": of_close[:bars_filled],
            "sess_open": sess_open[:bars_filled], "sess_high": sess_high[:bars_filled],
            "sess_low": sess_low[:bars_filled], "sess_close": sess_close[:bars_filled],
            "bid_add": bid_add[:bars_filled], "bid_pull": bid_pull[:bars_filled],
            "ask_add": ask_add[:bars_filled], "ask_pull": ask_pull[:bars_filled],
            "abs_flow": abs_flow[:bars_filled],
        }
        return bars_filled, out

    def current_bar_snapshot(self) -> dict:
        """Live (in-progress) bar state — for the currently forming candle."""
        return {
            "of_open": np.zeros(N_DEPTHS), "of_high": self.of_high_cur.copy(),
            "of_low": self.of_low_cur.copy(), "of_close": self.cum_bar.copy(),
            "sess_open": self.sess_open_cur.copy(), "sess_high": self.sess_high_cur.copy(),
            "sess_low": self.sess_low_cur.copy(), "sess_close": self.cum_sess.copy(),
            "bid_add": self.bid_add_cur.copy(), "bid_pull": self.bid_pull_cur.copy(),
            "ask_add": self.ask_add_cur.copy(), "ask_pull": self.ask_pull_cur.copy(),
            "abs_flow": self.abs_flow_cur.copy(),
        }


# ── cache / checkpoint I/O ───────────────────────────────────────────────── #
def cache_path(symbol: str, depth: int) -> Path:
    return CACHE_DIR / f"book_flow_candles_{symbol}_top{depth}.parquet"


def checkpoint_path(symbol: str, date: str) -> Path:
    return CACHE_DIR / f"book_flow_state_{symbol}_{date}.pkl"


def save_checkpoint(state: BookFlowState, symbol: str, date: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = checkpoint_path(symbol, date).with_suffix(".pkl.tmp")
    with open(tmp, "wb") as f:
        pickle.dump(state, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(checkpoint_path(symbol, date))


def load_checkpoint(symbol: str, date: str) -> Optional[BookFlowState]:
    p = checkpoint_path(symbol, date)
    if not p.exists():
        return None
    try:
        with open(p, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def build_bar_rows(bar_meta: pd.DataFrame, depth_idx: int, depth: int, out: dict,
                    of_open0: float = 0.0) -> pd.DataFrame:
    """Build the per-bar cache rows for one depth band from a `process()` output dict."""
    n = len(bar_meta)
    bid_add = out["bid_add"][:, depth_idx]
    bid_pull = out["bid_pull"][:, depth_idx]
    ask_add = out["ask_add"][:, depth_idx]
    ask_pull = out["ask_pull"][:, depth_idx]
    net_bid_flow = bid_add - bid_pull
    net_ask_flow = ask_pull - ask_add
    net_book_flow = net_bid_flow + net_ask_flow
    abs_book_flow = out["abs_flow"][:, depth_idx]
    book_imbalance = net_book_flow / np.maximum(abs_book_flow, EPS)
    pull_imbalance = (bid_pull - ask_pull) / np.maximum(bid_pull + ask_pull, EPS)

    df = pd.DataFrame({
        "bar_index": bar_meta["bar_index"].to_numpy(),
        "timestamp_utc": bar_meta.get("timestamp_utc", bar_meta.get("timestamp")).to_numpy(),
        "bar_start_ts_ns": bar_meta["bar_start_ts_ns"].to_numpy(),
        "bar_end_ts_ns": bar_meta["bar_end_ts_ns"].to_numpy(),
        "px_open": pd.to_numeric(bar_meta["px_open"], errors="coerce").to_numpy(),
        "px_high": pd.to_numeric(bar_meta["px_high"], errors="coerce").to_numpy(),
        "px_low": pd.to_numeric(bar_meta["px_low"], errors="coerce").to_numpy(),
        "px_close": pd.to_numeric(bar_meta["px_close"], errors="coerce").to_numpy(),
        "vol_total": pd.to_numeric(bar_meta.get("vol_total", pd.Series([np.nan] * n)), errors="coerce").to_numpy(),
        "buy_vol": pd.to_numeric(bar_meta.get("buy_vol", pd.Series([np.nan] * n)), errors="coerce").to_numpy(),
        "sell_vol": pd.to_numeric(bar_meta.get("sell_vol", pd.Series([np.nan] * n)), errors="coerce").to_numpy(),
        # aliases matching STEP C feature naming (same values as vol_total/buy_vol/sell_vol)
        "trade_volume": pd.to_numeric(bar_meta.get("vol_total", pd.Series([np.nan] * n)), errors="coerce").to_numpy(),
        "buy_trade_volume": pd.to_numeric(bar_meta.get("buy_vol", pd.Series([np.nan] * n)), errors="coerce").to_numpy(),
        "sell_trade_volume": pd.to_numeric(bar_meta.get("sell_vol", pd.Series([np.nan] * n)), errors="coerce").to_numpy(),
        "sweep_imbalance_norm": pd.to_numeric(
            bar_meta.get("sweep_imbalance_norm", pd.Series([np.nan] * n)), errors="coerce").to_numpy(),
        "depth": depth,
        "of_open": np.full(n, of_open0),
        "of_high": out["of_high"][:, depth_idx],
        "of_low": out["of_low"][:, depth_idx],
        "of_close": out["of_close"][:, depth_idx],
        "sess_open": out["sess_open"][:, depth_idx],
        "sess_high": out["sess_high"][:, depth_idx],
        "sess_low": out["sess_low"][:, depth_idx],
        "sess_close": out["sess_close"][:, depth_idx],
        "bid_add_volume": bid_add,
        "bid_pull_volume": bid_pull,
        "ask_add_volume": ask_add,
        "ask_pull_volume": ask_pull,
        "net_bid_flow": net_bid_flow,
        "net_ask_flow": net_ask_flow,
        "net_book_flow": net_book_flow,
        "abs_book_flow": abs_book_flow,
        "book_imbalance": book_imbalance,
        "pull_imbalance": pull_imbalance,
    })
    return df


# ── update_cache — incremental driver ────────────────────────────────────── #
def update_cache(symbol: str = "NQU6", date: str = "2026-06-14",
                  chunk_lines: int = 1_000_000, progress=None) -> dict:
    """Incrementally (re)build the per-depth order-flow-candle caches.

    Safe to call repeatedly: resumes from the persisted checkpoint
    (byte offsets + book state). On the very first call this performs the
    full historical backfill from the raw NQU6 capture files.

    Returns a status dict including the live in-progress-bar snapshot.
    """
    raw_dir = RAW_BASE / date / symbol
    bid_path = raw_dir / "bid_quote_updates.ndjson"
    ask_path = raw_dir / "ask_quote_updates.ndjson"
    vol500_path = FEATURES_BASE / date / f"{symbol}_vol500.ndjsonl"

    if not bid_path.exists() or not ask_path.exists():
        return {"ok": False, "reason": f"raw quote-update files not found under {raw_dir}"}
    if not vol500_path.exists():
        return {"ok": False, "reason": f"vol500 file not found: {vol500_path}"}

    bars = load_vol500_bars(vol500_path)
    if bars.empty:
        return {"ok": False, "reason": "vol500 file empty"}
    bar_end_ts = bars["bar_end_ts_ns"].to_numpy(dtype=np.int64)

    state = load_checkpoint(symbol, date)
    fresh = state is None
    if state is None:
        state = BookFlowState()

    # leftover events from the previous chunk that were "ahead" of the
    # slower stream's last timestamp — re-merged with the next chunk.
    leftover_bid = pd.DataFrame(columns=["event_ts_ns", "price", "size_eff"])
    leftover_ask = pd.DataFrame(columns=["event_ts_ns", "price", "size_eff"])

    bid_size = bid_path.stat().st_size
    ask_size = ask_path.stat().st_size
    total_filled = 0
    iterations = 0
    avg_bid_line = 480.0  # rough bytes/line estimate, refined below
    avg_ask_line = 480.0

    while state.n_bars_done < len(bars):
        if state.bid_offset >= bid_size and state.ask_offset >= ask_size and not (
                len(leftover_bid) or len(leftover_ask)):
            break  # caught up — remaining bar is the in-progress bar

        max_bid_bytes = int(chunk_lines * avg_bid_line)
        max_ask_bytes = int(chunk_lines * avg_ask_line)
        bid_chunk, new_bid_off = read_quote_updates(bid_path, state.bid_offset, max_bid_bytes)
        ask_chunk, new_ask_off = read_quote_updates(ask_path, state.ask_offset, max_ask_bytes)
        if len(bid_chunk):
            avg_bid_line = max(64.0, (new_bid_off - state.bid_offset) / max(len(bid_chunk), 1))
        if len(ask_chunk):
            avg_ask_line = max(64.0, (new_ask_off - state.ask_offset) / max(len(ask_chunk), 1))

        if len(leftover_bid):
            bid_chunk = pd.concat([leftover_bid, bid_chunk], ignore_index=True)
        if len(leftover_ask):
            ask_chunk = pd.concat([leftover_ask, ask_chunk], ignore_index=True)

        if bid_chunk.empty and ask_chunk.empty:
            state.bid_offset, state.ask_offset = new_bid_off, new_ask_off
            break

        bid_max = int(bid_chunk["event_ts_ns"].max()) if len(bid_chunk) else -1
        ask_max = int(ask_chunk["event_ts_ns"].max()) if len(ask_chunk) else -1
        if bid_max < 0:
            safe_ts = ask_max
        elif ask_max < 0:
            safe_ts = bid_max
        else:
            safe_ts = min(bid_max, ask_max)

        # split into "safe to process now" vs "carry over"
        if len(bid_chunk):
            mask = bid_chunk["event_ts_ns"].to_numpy() <= safe_ts
            proc_bid, leftover_bid = bid_chunk[mask], bid_chunk[~mask].reset_index(drop=True)
        else:
            proc_bid, leftover_bid = bid_chunk, bid_chunk
        if len(ask_chunk):
            mask = ask_chunk["event_ts_ns"].to_numpy() <= safe_ts
            proc_ask, leftover_ask = ask_chunk[mask], ask_chunk[~mask].reset_index(drop=True)
        else:
            proc_ask, leftover_ask = ask_chunk, ask_chunk

        remaining = bar_end_ts[state.n_bars_done:]
        bars_filled, out = state.process(proc_bid, proc_ask, remaining)

        if bars_filled:
            for d_idx, depth in enumerate(DEPTHS.tolist()):
                bar_meta = bars.iloc[state.n_bars_done:state.n_bars_done + bars_filled]
                new_rows = build_bar_rows(bar_meta, d_idx, depth, out)
                cpath = cache_path(symbol, depth)
                if cpath.exists():
                    existing = pd.read_parquet(cpath)
                    combined = pd.concat([existing, new_rows], ignore_index=True)
                else:
                    combined = new_rows
                combined.to_parquet(cpath, index=False)
            state.n_bars_done += bars_filled
            total_filled += bars_filled

        state.bid_offset, state.ask_offset = new_bid_off, new_ask_off
        iterations += 1
        if progress:
            progress(state.n_bars_done, len(bars), state.bid_offset, bid_size,
                     state.ask_offset, ask_size)

        # only the very first (fresh) call does a full backfill loop;
        # incremental calls process at most a couple of chunks per refresh.
        if not fresh and iterations >= 4:
            break
        if new_bid_off >= bid_size and new_ask_off >= ask_size and not (
                len(leftover_bid) or len(leftover_ask)):
            break

    save_checkpoint(state, symbol, date)

    return {
        "ok": True,
        "bars_total": len(bars),
        "bars_done": state.n_bars_done,
        "bars_filled_this_call": total_filled,
        "bid_offset": state.bid_offset, "bid_size": bid_size,
        "ask_offset": state.ask_offset, "ask_size": ask_size,
        "current_bar": state.current_bar_snapshot(),
        "current_bar_index": state.n_bars_done,
    }


# ── Level overlays — VERBATIM math from ofi_live_dashboard / chart_gpu ──── #
def swing_levels(win: pd.DataFrame, lb: int = 3):
    if "px_high" not in win.columns or "px_low" not in win.columns:
        return [], []
    h = pd.to_numeric(win["px_high"], errors="coerce").to_numpy()
    l = pd.to_numeric(win["px_low"], errors="coerce").to_numpy()
    n = len(h)
    win_n = lb * 2 + 1
    if n < win_n:
        return [], []
    h_roll = pd.Series(h).rolling(win_n, center=True, min_periods=win_n).max().to_numpy()
    l_roll = pd.Series(l).rolling(win_n, center=True, min_periods=win_n).min().to_numpy()
    hi_idx = np.where(np.isfinite(h) & np.isfinite(h_roll) & (h >= h_roll))[0]
    lo_idx = np.where(np.isfinite(l) & np.isfinite(l_roll) & (l <= l_roll))[0]
    return [(int(i), float(h[i])) for i in hi_idx], [(int(i), float(l[i])) for i in lo_idx]


def compute_sr_levels(df: pd.DataFrame, lb: int = 3, cluster_dist: float = 2.0):
    if df.empty or "px_high" not in df.columns:
        return [], []
    raw_h, raw_l = swing_levels(df, lb=lb)
    n_total = max(len(df), 1)

    def _cluster(raw: list) -> list:
        if not raw:
            return []
        raw_s = sorted(raw, key=lambda t: t[1])
        groups = [[raw_s[0]]]
        for bi, px in raw_s[1:]:
            if abs(px - groups[-1][-1][1]) <= cluster_dist:
                groups[-1].append((bi, px))
            else:
                groups.append([(bi, px)])
        out = []
        for grp in groups:
            med_px = float(np.median([p for _, p in grp]))
            count = len(grp)
            recency = max(bi for bi, _ in grp) / n_total
            score = count * (0.4 + 0.6 * recency)
            out.append((med_px, count, score))
        return sorted(out, key=lambda t: -t[2])

    return _cluster(raw_h), _cluster(raw_l)


def volume_profile(df: pd.DataFrame, tick: float = TICK) -> dict:
    """Full volume profile (POC/VAH/VAL/HVN/LVN) — verbatim formula from chart_gpu.py."""
    h = pd.to_numeric(df["px_high"], errors="coerce").to_numpy()
    l = pd.to_numeric(df["px_low"], errors="coerce").to_numpy()
    vt = pd.to_numeric(df["vol_total"], errors="coerce").to_numpy()
    bv = pd.to_numeric(df.get("buy_vol", pd.Series([0.0] * len(df))), errors="coerce").to_numpy()
    sv = pd.to_numeric(df.get("sell_vol", pd.Series([0.0] * len(df))), errors="coerce").to_numpy()
    valid = np.isfinite(h) & np.isfinite(l)
    if not valid.any():
        return {}
    pmin = float(np.nanmin(l[valid]))
    pmax = float(np.nanmax(h[valid]))
    lo_t = int(round(pmin / tick)) - 2
    hi_t = int(round(pmax / tick)) + 2
    n_lev = hi_t - lo_t + 1
    if n_lev < 2 or n_lev > 12000:
        return {}
    levels = np.array([round(j * tick, 2) for j in range(lo_t, hi_t + 1)])
    lev_lo = levels - tick * 0.5
    lev_hi = levels + tick * 0.5
    tot_v = np.zeros(n_lev)
    buy_v = np.zeros(n_lev)
    sel_v = np.zeros(n_lev)
    for i in range(len(df)):
        if not (np.isfinite(h[i]) and np.isfinite(l[i])):
            continue
        br = max(h[i] - l[i], tick)
        ov = np.minimum(h[i], lev_hi) - np.maximum(l[i], lev_lo)
        m = ov > 0
        if m.any():
            w = np.where(m, ov / br, 0.0)
            tot_v += vt[i] * w
            if np.isfinite(bv[i]) and np.isfinite(sv[i]):
                buy_v += bv[i] * w
                sel_v += sv[i] * w
    if tot_v.max() == 0:
        return {}
    poc_idx = int(np.argmax(tot_v))
    tot_sum = tot_v.sum()
    va_target = tot_sum * 0.70
    va = tot_v[poc_idx]
    vah_i = poc_idx
    val_i = poc_idx
    _max_va_iter = n_lev + 5
    _va_iter = 0
    while va < va_target and (val_i > 0 or vah_i < n_lev - 1) and _va_iter < _max_va_iter:
        _va_iter += 1
        up = tot_v[vah_i + 1] if vah_i < n_lev - 1 else 0.0
        down = tot_v[val_i - 1] if val_i > 0 else 0.0
        if up == 0.0 and down == 0.0:
            break
        if up >= down:
            vah_i += 1
            va += up
        else:
            val_i -= 1
            va += down
    k = max(3, n_lev // 15)
    kernel = np.ones(k) / k
    smooth = np.convolve(tot_v, kernel, mode="same")
    hvn_mask = (tot_v > 0) & (tot_v > smooth * 1.45)
    lvn_mask = (tot_v > 0) & (tot_v < smooth * 0.55)
    return {
        "poc": float(levels[poc_idx]), "vah": float(levels[vah_i]), "val": float(levels[val_i]),
        "levels": levels, "tot_v": tot_v, "buy_v": buy_v, "sel_v": sel_v,
        "hvn_px": levels[hvn_mask].tolist(), "lvn_px": levels[lvn_mask].tolist(),
    }


_projected_levels_cache: dict = {}


def load_projected_levels(path: Path) -> pd.DataFrame:
    """Load projected_levels_NQM6_to_NQU6.csv (mtime/size cached).

    These are PROJECTED PRIOR NQM6 LEVELS SHIFTED TO NQU6 SCALE — never
    native NQU6 levels, must always be displayed/labeled separately.
    """
    try:
        st = path.stat()
    except OSError:
        return pd.DataFrame()
    key = str(path)
    cached = _projected_levels_cache.get(key)
    if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
        return cached[2]
    try:
        df = pd.read_csv(path)
    except Exception:
        return pd.DataFrame()
    _projected_levels_cache[key] = (st.st_mtime, st.st_size, df)
    return df


def nearest_level(levels: list[tuple[str, float]], price: float):
    """levels: list of (name, price). Returns (name, level_price, distance) or (None, nan, nan)."""
    if not levels or not np.isfinite(price):
        return None, float("nan"), float("nan")
    best = min(levels, key=lambda t: abs(t[1] - price))
    return best[0], best[1], price - best[1]
