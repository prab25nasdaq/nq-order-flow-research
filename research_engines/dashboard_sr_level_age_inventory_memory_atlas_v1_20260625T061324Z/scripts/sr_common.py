"""
sr_common.py - shared READ-ONLY library for the Dashboard S/R Level Age +
Inventory Memory Atlas v1.

This module reproduces the dashboard's OWN S/R formulas (swing_levels /
compute_sr_levels, and the volume-profile POC/VAH/VAL/HVN/LVN math) VERBATIM
from ofi_live_dashboard_WORKING_NEXT_with_logreg.py, applied here over fixed
past-only lookback windows instead of the dashboard's live window_bars /
ax_price.get_xlim() display state. No new S/R method is invented anywhere
in this file - every formula below is a line-for-line port, documented with
its dashboard source line numbers.

READ-ONLY against all production paths. Writes only inside this engine's
own outputs/. SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER
TRADING / NO MODEL TRAINING.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
import yaml

ENGINE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = ENGINE_DIR / "outputs"
OUT_DIR.mkdir(exist_ok=True)
REPORTS_DIR = ENGINE_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

LIVE_FEATURES_DIR = Path("/home/prabh/OFI_Live_Features")
BOOK_FLOW_CACHE_DIR = Path("/home/prabh/OFI_Production/book_flow_chart/cache")
THICKNESS_ATLAS_GLOB = "/home/prabh/OFI_Production/research_engines/book_flow_thickness_auction_friction_atlas_v1_*"
MECHANICS_ATLAS_GLOB = "/home/prabh/OFI_Production/research_engines/nasdaq_full_book_level_mechanics_atlas_v1_*"

TICK_SIZE = 0.25


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_config() -> dict:
    with open(ENGINE_DIR / "configs" / "atlas_config.yaml") as f:
        return yaml.safe_load(f)


def _read_ndjsonl(path: Path) -> pd.DataFrame:
    rows = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    return pd.DataFrame(rows)


def load_continuous_master() -> pd.DataFrame:
    """master_NQ_continuous_backadjusted_shadow.ndjsonl, sorted/deduped/bar-indexed."""
    df = _read_ndjsonl(LIVE_FEATURES_DIR / "master_NQ_continuous_backadjusted_shadow.ndjsonl")
    df = df.sort_values("bar_end_ts_ns", kind="stable").drop_duplicates(
        subset=["bar_end_ts_ns"], keep="first").reset_index(drop=True)
    df["t"] = np.arange(len(df))  # absolute integer bar position used throughout this atlas
    day_str = df["day"].astype(int).astype(str).str.zfill(8)
    df["session_date"] = day_str.str[:4] + "-" + day_str.str[4:6] + "-" + day_str.str[6:8]
    for c in ("px_high", "px_low", "px_close", "px_open", "vol_total", "buy_vol", "sell_vol",
              "delta_norm", "vpin", "vpin_resid_z20", "entropy_score", "volatility_5",
              "sweep_imbalance_norm_resid_z20"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def list_level_candle_dates(depth: int) -> List[str]:
    import glob
    pattern = str(BOOK_FLOW_CACHE_DIR / f"book_flow_level_candles_NQU6_*_top{depth}.parquet")
    return sorted({Path(f).name.split("_")[5] for f in glob.glob(pattern)})


def load_level_candles(depth: int = 10, dates: List[str] = None) -> pd.DataFrame:
    frames = []
    dates = dates or list_level_candle_dates(depth)
    for d in dates:
        path = BOOK_FLOW_CACHE_DIR / f"book_flow_level_candles_NQU6_{d}_top{depth}.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        df["session_date"] = d
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def aggregate_book_flow_bars(level_df: pd.DataFrame) -> pd.DataFrame:
    """Bar-level full-book OFI add/pull aggregation, CLOSED bars only.
    Identical formula to atlas_common.aggregate_book_flow_bars (this
    codebase's established convention) - reused verbatim, not reinvented."""
    df = level_df[level_df["bar_state"] == "CLOSED"]
    if df.empty:
        return pd.DataFrame()
    agg = (
        df.groupby(["session_date", "bar_idx"])
        .agg(
            bid_add=("bid_add", "sum"), bid_pull=("bid_pull", "sum"),
            ask_add=("ask_add", "sum"), ask_pull=("ask_pull", "sum"),
            signed_flow=("signed_flow", "sum"), abs_flow=("abs_flow", "sum"),
            net_bid_flow=("net_bid_flow", "sum"), net_ask_flow=("net_ask_flow", "sum"),
            trade_volume=("trade_volume_at_price", "sum"),
            buy_trade_volume=("buy_trade_volume_at_price", "sum"),
            sell_trade_volume=("sell_trade_volume_at_price", "sum"),
            resting_depth=("abs_flow", "size"),
            close_price=("close_price", "last"), mid_price=("mid_price", "last"),
            bar_end_ts_ns=("bar_end_ts_ns", "last"), timestamp_utc=("timestamp_utc", "last"),
        )
        .reset_index()
        .sort_values(["session_date", "bar_idx"])
        .reset_index(drop=True)
    )
    agg["bid_pull_pressure"] = agg["bid_pull"] / agg["abs_flow"].replace(0, np.nan)
    agg["ask_pull_pressure"] = agg["ask_pull"] / agg["abs_flow"].replace(0, np.nan)
    agg["book_imbalance"] = (agg["bid_add"] - agg["ask_add"]) / (agg["bid_add"] + agg["ask_add"]).replace(0, np.nan)
    return agg


# ════════════════════════════════════════════════════════════════════════
# DASHBOARD'S OWN S/R FORMULA - verbatim port of swing_levels() /
# compute_sr_levels() from ofi_live_dashboard_WORKING_NEXT_with_logreg.py
# lines 597-651. Only change: the centered-rolling-window confirmation lag
# is made explicit (a swing at index i is not returned until the caller's
# window already includes bar i+lb) so this can be walked forward bar-by-
# bar without ever using information from beyond "now".
# ════════════════════════════════════════════════════════════════════════
def swing_levels(win: pd.DataFrame, lb: int = 4) -> Tuple[list, list]:
    """Verbatim port of dashboard swing_levels(), lines 597-613."""
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


def compute_sr_levels(df: pd.DataFrame, lb: int = 4, cluster_dist: float = 6.0) -> Tuple[list, list]:
    """Verbatim port of dashboard compute_sr_levels(), lines 616-651.
    Returns two lists of (price, touch_count, score) sorted by score desc."""
    if df.empty or "px_high" not in df.columns:
        return [], []
    raw_h, raw_l = swing_levels(df, lb=lb)
    n_total = max(len(df), 1)

    def _cluster(raw: list) -> list:
        if not raw:
            return []
        raw_s = sorted(raw, key=lambda t: t[1])
        groups: list = [[raw_s[0]]]
        for bi, px in raw_s[1:]:
            if abs(px - groups[-1][-1][1]) <= cluster_dist:
                groups[-1].append((bi, px))
            else:
                groups.append([(bi, px)])
        result = []
        for grp in groups:
            med_px = float(np.median([p for _, p in grp]))
            count = len(grp)
            recency = max(bi for bi, _ in grp) / n_total
            score = count * (0.4 + 0.6 * recency)
            result.append((med_px, count, score, max(bi for bi, _ in grp)))
        return sorted(result, key=lambda t: -t[2])

    return _cluster(raw_h), _cluster(raw_l)


def volume_profile_levels(df_win: pd.DataFrame, tick_size: float = TICK_SIZE) -> Dict[str, Any]:
    """Verbatim port of the dashboard's volume-profile POC/VAH/VAL/HVN/LVN
    math, lines 4861-4894 (formula only - bar RANGE is the caller's fixed
    df_win, never ax_price.get_xlim())."""
    h = pd.to_numeric(df_win["px_high"], errors="coerce").to_numpy()
    l = pd.to_numeric(df_win["px_low"], errors="coerce").to_numpy()
    vt = pd.to_numeric(df_win["vol_total"], errors="coerce").fillna(0).to_numpy()
    bv = pd.to_numeric(df_win.get("buy_vol", pd.Series(np.nan, index=df_win.index)), errors="coerce").to_numpy()
    sv = pd.to_numeric(df_win.get("sell_vol", pd.Series(np.nan, index=df_win.index)), errors="coerce").to_numpy()
    valid = np.isfinite(h) & np.isfinite(l)
    if not valid.any():
        return {}
    pmin = float(np.nanmin(l[valid])); pmax = float(np.nanmax(h[valid]))
    if pmax - pmin < 0.5:
        return {}
    lo_t = int(round(pmin / tick_size)) - 2
    hi_t = int(round(pmax / tick_size)) + 2
    n_lev = hi_t - lo_t + 1
    if n_lev < 2 or n_lev > 6000:
        # DOCUMENTED DEVIATION from the dashboard's literal cap (n_lev > 3000,
        # source line ~4786): that cap exists ONLY as a Tkinter UI-redraw
        # responsiveness guard ("VPROF SKIPPED - COMPUTE TOO SLOW" / a 400ms
        # timeout on the same bar loop). This is an offline batch computation
        # with no UI to freeze, so the cap is relaxed to 6000 purely to let
        # full-history/10000-bar window snapshots compute on wide price
        # ranges. The underlying POC/VAH/VAL/HVN/LVN math itself is
        # unchanged - this only affects whether a snapshot is skipped, never
        # what it returns when it does run.
        return {}
    levels = np.array([round(j * tick_size, 2) for j in range(lo_t, hi_t + 1)])
    lev_lo = levels - tick_size * 0.5
    lev_hi = levels + tick_size * 0.5
    tot_v = np.zeros(n_lev)
    for i in range(len(df_win)):
        if not (np.isfinite(h[i]) and np.isfinite(l[i])):
            continue
        br = max(h[i] - l[i], tick_size)
        ov = np.minimum(h[i], lev_hi) - np.maximum(l[i], lev_lo)
        m = ov > 0
        if not m.any():
            continue
        w = np.where(m, ov / br, 0.0)
        tot_v += vt[i] * w
    if tot_v.max() == 0:
        return {}
    poc_idx = int(np.argmax(tot_v))
    total_sum = float(tot_v.sum())
    va_target = total_sum * 0.70
    va_vol = float(tot_v[poc_idx]); vah_i = poc_idx; val_i = poc_idx
    _max_iter = n_lev + 5; _it = 0
    while va_vol < va_target and (val_i > 0 or vah_i < n_lev - 1) and _it < _max_iter:
        _it += 1
        up = float(tot_v[vah_i + 1]) if vah_i < n_lev - 1 else 0.0
        down = float(tot_v[val_i - 1]) if val_i > 0 else 0.0
        if up == 0.0 and down == 0.0:
            break
        if up >= down:
            vah_i += 1; va_vol += up
        else:
            val_i -= 1; va_vol += down
    poc_px = float(levels[poc_idx]); vah_px = float(levels[vah_i]); val_px = float(levels[val_i])
    k = max(3, n_lev // 15)
    kernel = np.ones(k) / k
    smooth = np.convolve(tot_v, kernel, mode="same")
    is_hvn = (tot_v > 0) & (tot_v > smooth * 1.45)
    is_lvn = (tot_v > 0) & (tot_v < smooth * 0.55)
    return {
        "poc_px": poc_px, "vah_px": vah_px, "val_px": val_px,
        "hvn_px": levels[is_hvn].tolist(), "lvn_px": levels[is_lvn].tolist(),
    }


def session_label(minute_of_day) -> str:
    m = float(minute_of_day) if minute_of_day is not None and np.isfinite(float(minute_of_day or np.nan)) else np.nan
    if not np.isfinite(m):
        return "UNKNOWN"
    if 30 <= m < 9 * 60 + 30:
        return "OVERNIGHT_ASIA_EU"
    if 9 * 60 + 30 <= m < 11 * 60 + 30:
        return "RTH_OPEN"
    if 11 * 60 + 30 <= m < 14 * 60:
        return "RTH_MIDDAY"
    if 14 * 60 <= m < 16 * 60:
        return "RTH_CLOSE"
    return "OVERNIGHT_OTHER"


def rolling_state_tercile(values: np.ndarray, window: int = 500, min_ref: int = 50) -> np.ndarray:
    """Past-only LOW/MED/HIGH tercile label vs the trailing `window` history.
    Identical convention to atlas_common.rolling_state_tercile (this
    codebase's established a-priori regime-binning method)."""
    n = len(values)
    out = np.array(["UNKNOWN"] * n, dtype=object)
    v = pd.Series(values).astype(float)
    for i in range(n):
        lo = max(0, i - window)
        ref = v.iloc[lo:i].dropna()
        if len(ref) < min_ref or not np.isfinite(v.iloc[i]):
            continue
        p33, p67 = np.nanpercentile(ref, [33, 67])
        x = v.iloc[i]
        out[i] = "LOW" if x <= p33 else ("HIGH" if x >= p67 else "MED")
    return out
