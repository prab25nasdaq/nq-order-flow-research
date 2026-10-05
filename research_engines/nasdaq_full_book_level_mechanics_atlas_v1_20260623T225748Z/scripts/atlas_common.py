"""
atlas_common.py - shared READ-ONLY library for the Nasdaq Full-Book Level
Mechanics Atlas v1.

SCOPE DECISION (documented here, repeated in the final report): full
microsecond-resolution reconstruction of the raw Rithmic depth/quote/trade
streams (/home/prabh/OFI_Live_Data/Rithmic_Raw/, 251GB across 15 days,
~8.6GB/day for bid_quote_updates.ndjson and ask_quote_updates.ndjson alone)
was judged infeasible within this research session's resource bounds - a
naive per-event scan of those files would require re-reading multi-GB
files thousands of times over. Instead, this Atlas computes ALL order-book
mechanics at BAR resolution from the production Book Flow cache
(book_flow_chart/cache/book_flow_level_candles_NQU6_*.parquet), which is
itself derived from the SAME genuine full L2 Rithmic depth/quote/trade
stream by the production parser - this is real full-book data, just
pre-aggregated to bar granularity rather than reconstructed at tick
resolution here. Metrics describing genuinely sub-bar dynamics (queue
replenishment, depth recovery speed, microprice slope) are computed as
BAR-RESOLUTION PROXIES, explicitly labeled as such - consistent with the
existing production model's OWN established convention for exploratory
order-flow signals (cf. bid_pull_PROXY/ask_pull_PROXY in
pipeline_continuous.py's FEATURE_POLICY.md). A small illustrative raw-tick
sample (Part B supplementary) IS read directly from Rithmic_Raw for a
handful of events, to ground-truth what genuine tick-level dynamics look
like, but this is illustrative only, not the basis for any aggregate
statistic in this Atlas.

Scope is NQU6 days only (2026-06-14/15/16/17/18/21/22/23) - consistent with
v4's own scoping rationale (Book Flow cache and dashboard-parity features
are NQU6-only artifacts in this codebase; NQM6 history has no book-flow
cache coverage).

READ-ONLY against all production paths. Writes only inside this engine's
own outputs/. SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER
TRADING / NO DATABENTO.
"""
from __future__ import annotations

import json
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
RAW_SNAPSHOT_DIR = ENGINE_DIR / "raw_snapshot"

V4_DIR = Path("/home/prabh/OFI_Production/research_engines/afml_label_policy_parity_entry_cp_v4_20260623T192440Z")
V3_DIR = Path("/home/prabh/OFI_Production/research_engines/afml_dashboard_parity_cusum_sr_entry_cp_v3_20260623T184026Z")
LIVE_FEATURES_DIR = Path("/home/prabh/OFI_Live_Features")
BOOK_FLOW_CACHE_DIR = Path("/home/prabh/OFI_Production/book_flow_chart/cache")
RITHMIC_RAW_DIR = Path("/home/prabh/OFI_Live_Data/Rithmic_Raw")

TICK_SIZE = 0.25
NEAR_TICKS_K = 4
NEAR_TICKS_PRICE = TICK_SIZE * NEAR_TICKS_K
NQU6_DAYS = ["2026-06-14", "2026-06-15", "2026-06-16", "2026-06-17", "2026-06-18",
            "2026-06-21", "2026-06-22", "2026-06-23"]
MODEL_TRAINING_CUTOFF_UTC = pd.Timestamp("2026-06-21T01:53:35.580300+00:00")


def log(msg: str) -> None:
    import time
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


def load_nqu6_master(dedup: bool = True) -> pd.DataFrame:
    df = _read_ndjsonl(LIVE_FEATURES_DIR / "master_NQU6_shadow.ndjsonl")
    df = df.sort_values("bar_end_ts_ns", kind="stable").reset_index(drop=True)
    if dedup:
        df = df.drop_duplicates(subset=["bar_end_ts_ns"], keep="first").reset_index(drop=True)
    day_str = df["day"].astype(int).astype(str).str.zfill(8)
    df["rithmic_date_str"] = day_str.str[:4] + "-" + day_str.str[4:6] + "-" + day_str.str[6:8]
    return df


def load_v4_feature_panel() -> pd.DataFrame:
    """v3's already-validated 132-col dashboard-parity/OFI-LD/master/bookflow panel,
    reused verbatim via v4's own outputs (v4 already re-exported it as v4_feature_panel.parquet)."""
    return pd.read_parquet(V4_DIR / "outputs" / "v4_feature_panel.parquet")


def load_v4_rebuilt_events() -> pd.DataFrame:
    return pd.read_parquet(V4_DIR / "outputs" / "rebuilt_label_policy_events.parquet")


def load_v4_deduped_candidates() -> pd.DataFrame:
    return pd.read_parquet(V4_DIR / "outputs" / "deduped_candidates_v4.parquet")


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
    """Bar-level full-book OFI add/pull aggregation (sum across resting price
    levels within the cached depth window), CLOSED bars only. Identical
    formula to v3/v4_common.aggregate_book_flow_bars."""
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
    agg["aggressive_buy_ratio"] = agg["buy_trade_volume"] / agg["trade_volume"].replace(0, np.nan)
    agg["aggressive_sell_ratio"] = agg["sell_trade_volume"] / agg["trade_volume"].replace(0, np.nan)
    agg["trade_delta"] = agg["buy_trade_volume"] - agg["sell_trade_volume"]
    agg["book_imbalance"] = (agg["bid_add"] - agg["ask_add"]) / (agg["bid_add"] + agg["ask_add"]).replace(0, np.nan)
    return agg


# ═════════════════════════════════════════════════════════════════════════
# EXISTING MODEL's per-day volume profile (verbatim from
# pipeline_continuous.py / v4_common.py - already validated 100% exact in
# the v4 build's Part B parity check).
# ═════════════════════════════════════════════════════════════════════════
def volume_profile_levels(df_day: pd.DataFrame) -> Dict[str, Any]:
    h = pd.to_numeric(df_day["continuous_high"], errors="coerce").to_numpy()
    l = pd.to_numeric(df_day["continuous_low"], errors="coerce").to_numpy()
    vt = pd.to_numeric(df_day["vol_total"], errors="coerce").to_numpy()
    bv = pd.to_numeric(df_day["buy_vol"], errors="coerce").to_numpy()
    sv = pd.to_numeric(df_day["sell_vol"], errors="coerce").to_numpy()

    valid = np.isfinite(h) & np.isfinite(l)
    pmin = float(np.nanmin(l[valid])); pmax = float(np.nanmax(h[valid]))
    lo_t = int(round(pmin / TICK_SIZE)) - 2
    hi_t = int(round(pmax / TICK_SIZE)) + 2
    n_lev = hi_t - lo_t + 1
    levels = np.array([round(j * TICK_SIZE, 2) for j in range(lo_t, hi_t + 1)])
    lev_lo = levels - TICK_SIZE * 0.5; lev_hi = levels + TICK_SIZE * 0.5
    tot_v = np.zeros(n_lev); buy_v = np.zeros(n_lev); sel_v = np.zeros(n_lev)
    for i in range(len(df_day)):
        if not (np.isfinite(h[i]) and np.isfinite(l[i])):
            continue
        br = max(h[i] - l[i], TICK_SIZE)
        ov = np.minimum(h[i], lev_hi) - np.maximum(l[i], lev_lo)
        m = ov > 0
        if not m.any():
            continue
        w = np.where(m, ov / br, 0.0)
        tot_v += vt[i] * w
        if np.isfinite(bv[i]) and np.isfinite(sv[i]):
            buy_v += bv[i] * w; sel_v += sv[i] * w
    if tot_v.max() == 0:
        raise RuntimeError("zero-volume window")
    poc_idx = int(np.argmax(tot_v))
    tot_sum = tot_v.sum(); va_target = tot_sum * 0.70
    va_vol = tot_v[poc_idx]; vah_i = poc_idx; val_i = poc_idx
    while va_vol < va_target and (val_i > 0 or vah_i < n_lev - 1):
        up = tot_v[vah_i + 1] if vah_i < n_lev - 1 else 0
        down = tot_v[val_i - 1] if val_i > 0 else 0
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
        "levels": levels, "tot_v": tot_v,
        "value_area_pct": round(100.0 * va_vol / tot_sum, 2),
    }


def build_rolling_levels(bar_df: pd.DataFrame, price_col: str = "px_close",
                          tick_bucket: float = 2.5, min_prior_bars: int = 30) -> pd.DataFrame:
    """De-leaked rolling POC/VAH/VAL/HVN/LVN, strictly PRIOR-bar-only (no
    same-bar or future contribution) - the leakage-safe alternative to the
    existing model's own full-session native daily volume profile. Single
    rolling window across the WHOLE NQU6 history (not reset per day) so
    early-day bars still have meaningful prior context. Identical
    methodology to v3_common/v4_common.build_rolling_levels (already
    validated in the v3 build), tick_bucket=2.5, min_prior_bars raised to
    30 here since this Atlas needs the level to be reasonably converged
    before treating it as a candidate touch target."""
    out = bar_df.sort_values("bar_index").reset_index(drop=True).copy()
    out["px_bucket"] = np.round(out[price_col] / tick_bucket) * tick_bucket
    n = len(out)
    uniq_buckets = np.unique(out["px_bucket"].to_numpy())
    b2i = {b: i for i, b in enumerate(uniq_buckets)}
    bucket_idx = out["px_bucket"].map(b2i).to_numpy()
    k = len(uniq_buckets)
    onehot = np.zeros((n, k), dtype=np.int32)
    onehot[np.arange(n), bucket_idx] = 1
    cum_counts = np.cumsum(onehot, axis=0)
    poc_arr = np.full(n, np.nan); vah_arr = np.full(n, np.nan); val_arr = np.full(n, np.nan)
    hvn_arr = np.full(n, np.nan); lvn_arr = np.full(n, np.nan)
    closes = out[price_col].to_numpy()
    for t in range(n):
        if t < min_prior_bars:
            continue
        prior_counts = cum_counts[t - 1]
        total = prior_counts.sum()
        if total <= 0:
            continue
        nz = prior_counts > 0
        cnt_vals = prior_counts[nz]; cnt_buckets = uniq_buckets[nz]
        poc_arr[t] = cnt_buckets[np.argmax(cnt_vals)]
        order = np.argsort(-cnt_vals)
        cumsum = 0; va_buckets = []
        for oi in order:
            va_buckets.append(cnt_buckets[oi]); cumsum += cnt_vals[oi]
            if cumsum >= 0.7 * total:
                break
        vah_arr[t] = max(va_buckets); val_arr[t] = min(va_buckets)
        if len(cnt_vals) >= 3:
            mean_c, std_c = cnt_vals.mean(), cnt_vals.std()
            hvn_mask = cnt_vals >= mean_c + 0.5 * std_c
            lvn_mask = cnt_vals <= mean_c - 0.5 * std_c
            cur_px = closes[t]
            if hvn_mask.any():
                hvn_b = cnt_buckets[hvn_mask]
                hvn_arr[t] = hvn_b[np.argmin(np.abs(hvn_b - cur_px))]
            if lvn_mask.any():
                lvn_b = cnt_buckets[lvn_mask]
                lvn_arr[t] = lvn_b[np.argmin(np.abs(lvn_b - cur_px))]
    out["rolling_poc"] = poc_arr; out["rolling_vah"] = vah_arr; out["rolling_val"] = val_arr
    out["rolling_hvn"] = hvn_arr; out["rolling_lvn"] = lvn_arr
    return out


def session_label(minute_of_day: int) -> str:
    if minute_of_day < 360: return "Asia"
    if minute_of_day < 720: return "EU"
    if minute_of_day < 870: return "US_Open"
    if minute_of_day < 1080: return "US_AM"
    if minute_of_day < 1320: return "US_PM"
    return "US_Late"


def rolling_state_tercile(values: np.ndarray, window: int = 500, min_ref: int = 50) -> np.ndarray:
    """Causal rolling-percentile state code: LOW (<33rd pct of trailing
    window), MED, HIGH (>=67th pct) - past-only, never including bar t's own
    value in its own reference set (matches v3's rolling_pct/_quantile_codes
    causal convention)."""
    vals = np.asarray(values, dtype=float)
    out = np.full(len(vals), "UNKNOWN", dtype=object)
    for i in range(len(vals)):
        ref = vals[max(0, i - window):i]
        ref = ref[np.isfinite(ref)]
        if len(ref) < min_ref or not np.isfinite(vals[i]):
            continue
        q33, q67 = np.quantile(ref, [0.33, 0.67])
        out[i] = "LOW" if vals[i] < q33 else ("HIGH" if vals[i] >= q67 else "MED")
    return out


# ── Metrics (identical to v3/v4_common.py) ──────────────────────────────
def mcc_binary(y_true, y_pred) -> float:
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    y_true, y_pred = y_true[mask], y_pred[mask]
    if len(y_true) < 5:
        return np.nan
    tp = np.sum((y_pred == 1) & (y_true == 1)); tn = np.sum((y_pred == 0) & (y_true == 0))
    fp = np.sum((y_pred == 1) & (y_true == 0)); fn = np.sum((y_pred == 0) & (y_true == 1))
    denom = np.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    return float((tp * tn - fp * fn) / denom) if denom > 0 else 0.0


def num_co_events(t0_idx: np.ndarray, t1_idx: np.ndarray, n_bars: int) -> np.ndarray:
    valid = (t0_idx >= 0) & (t1_idx >= 0)
    c = np.zeros(n_bars, dtype=np.int64)
    for t0, t1 in zip(t0_idx[valid], t1_idx[valid]):
        lo, hi = max(0, int(t0)), min(n_bars - 1, int(t1))
        if lo <= hi:
            c[lo:hi + 1] += 1
    return c


def average_uniqueness(t0_idx: np.ndarray, t1_idx: np.ndarray, c_t: np.ndarray) -> np.ndarray:
    out = np.full(len(t0_idx), np.nan)
    for i, (t0, t1) in enumerate(zip(t0_idx, t1_idx)):
        if t0 < 0 or t1 < 0:
            continue
        lo, hi = max(0, int(t0)), min(len(c_t) - 1, int(t1))
        if lo > hi:
            continue
        out[i] = float(np.mean(1.0 / c_t[lo:hi + 1]))
    return out


def purged_embargo_day_splits(events: pd.DataFrame, day_col: str, t0_col: str, t1_col: str,
                               embargo_bars: int) -> List[Dict]:
    days = sorted(events[day_col].unique())
    folds = []
    embargoed_idx = set()
    for i, test_day in enumerate(days):
        test_mask = events[day_col] == test_day
        test_idx = events.index[test_mask].to_numpy()
        if test_idx.size == 0:
            continue
        test_lo = events.loc[test_idx, t0_col].min()
        test_hi = events.loc[test_idx, t1_col].max()
        prior_days = days[:i]
        if not prior_days:
            continue
        train_mask = events[day_col].isin(prior_days)
        train_idx_all = events.index[train_mask].to_numpy()
        overlap_mask = ~(
            (events.loc[train_idx_all, t1_col] < test_lo) |
            (events.loc[train_idx_all, t0_col] > test_hi)
        )
        purged = set(train_idx_all[overlap_mask.to_numpy()])
        embargoed_now = set(train_idx_all) & embargoed_idx
        train_idx = np.array(sorted(set(train_idx_all) - purged - embargoed_now))
        folds.append(dict(
            test_day=test_day, train_idx=train_idx, test_idx=test_idx,
            n_purged=len(purged), n_embargoed=len(embargoed_now),
            n_train=len(train_idx), n_test=len(test_idx),
        ))
        embargo_hi = test_hi + embargo_bars
        newly_embargoed = events.index[
            (events[t0_col] > test_hi) & (events[t0_col] <= embargo_hi)
        ].to_numpy()
        embargoed_idx.update(newly_embargoed)
    return folds
