"""
thickness_common.py - shared READ-ONLY library for the Book-Flow Thickness /
Auction Friction Atlas v1.

SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING.

Reuses (verbatim where unchanged) the already-validated helpers from the
prior Nasdaq Full-Book Level Mechanics Atlas's atlas_common.py
(research_engines/nasdaq_full_book_level_mechanics_atlas_v1_20260623T225748Z/
scripts/atlas_common.py) - level-candle loading, bar-aggregated book-flow,
native/rolling volume-profile levels, session labeling, causal regime
terciles, AFML uniqueness weighting, purged-embargo day splits. This module
ADDS the price-axis THICKNESS layer (per bar x price_level granularity,
percentile-scaled within a session-day context) that the prior Atlas never
computed, since it worked at bar-aggregated resolution only.

READ-ONLY against all production paths. Writes only inside this engine's
own outputs/. Never touches the parser, Rithmic feed, Book Flow chart code,
dashboard, master files, model artifacts, or any trading/broker/paper-
trading flag.
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

V4_DIR = Path("/home/prabh/OFI_Production/research_engines/afml_label_policy_parity_entry_cp_v4_20260623T192440Z")
LEVEL_ATLAS_DIR = Path("/home/prabh/OFI_Production/research_engines/nasdaq_full_book_level_mechanics_atlas_v1_20260623T225748Z")
LIVE_FEATURES_DIR = Path("/home/prabh/OFI_Live_Features")
BOOK_FLOW_CACHE_DIR = Path("/home/prabh/OFI_Production/book_flow_chart/cache")
FM_ROOT = Path("/home/prabh/OFI_Production/model_feature_master")
FM_DATA_DIR = FM_ROOT / "data"
V4_MODEL_DIR = Path("/home/prabh/OFI_Production/inference_scripts/v4_histgb_entry_shadow/model_candidate")

TICK_SIZE = 0.25
NEAR_TICKS_K = 4
NEAR_TICKS_PRICE = TICK_SIZE * NEAR_TICKS_K


def log(msg: str) -> None:
    import time
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_config() -> dict:
    with open(ENGINE_DIR / "configs" / "atlas_config.yaml") as f:
        return yaml.safe_load(f)


def _first_existing(*candidates: Path):
    for c in candidates:
        if c is not None and c.exists():
            return c
    return None


def fm_path(filename: str):
    return _first_existing(FM_ROOT / filename, FM_DATA_DIR / filename)


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
    return pd.read_parquet(V4_DIR / "outputs" / "v4_feature_panel.parquet")


def load_v4_entry_dataset() -> pd.DataFrame:
    return pd.read_parquet(V4_DIR / "outputs" / "entry_meta_label_dataset_v4.parquet")


def load_level_touch_events() -> pd.DataFrame:
    """Reuse, read-only, the prior Atlas's already-validated level-touch
    event universe (21,528 events, native+rolling+prior-session levels,
    8 NQU6 days) rather than re-deriving touch detection from scratch."""
    return pd.read_parquet(LEVEL_ATLAS_DIR / "outputs" / "level_touch_events.parquet")


def load_level_touch_mfe_mae() -> pd.DataFrame:
    return pd.read_parquet(LEVEL_ATLAS_DIR / "outputs" / "level_touch_mfe_mae.parquet")


def load_level_behavior_labels() -> pd.DataFrame:
    return pd.read_parquet(LEVEL_ATLAS_DIR / "outputs" / "level_behavior_labels.parquet")


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
    formula to atlas_common.py / v3/v4_common.aggregate_book_flow_bars."""
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


def session_label(minute_of_day: int) -> str:
    if minute_of_day < 360: return "Asia"
    if minute_of_day < 720: return "EU"
    if minute_of_day < 870: return "US_Open"
    if minute_of_day < 1080: return "US_AM"
    if minute_of_day < 1320: return "US_PM"
    return "US_Late"


def rolling_state_tercile(values: np.ndarray, window: int = 500, min_ref: int = 50) -> np.ndarray:
    """Causal rolling-percentile state code: LOW (<33rd pct of trailing
    window), MED, HIGH (>=67th pct) - past-only. Verbatim from
    atlas_common.py."""
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


# ═════════════════════════════════════════════════════════════════════════
# NEW: price-axis thickness layer (percentile-scaled within a session-day
# context, matching book_flow_chart_v3.py's own scale_mode=="percentile"
# branch: denom = np.nanpercentile(abs_flow, 95) computed across the
# currently-visible cell population).
# ═════════════════════════════════════════════════════════════════════════
def percentile_rank_in_context(values: np.ndarray, context: np.ndarray,
                                restrict_nonzero: bool = True) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    For each value, compute (percentile_rank 0-100, dense_rank, zscore)
    relative to the OTHER values sharing the same context label (e.g. the
    same session_date) - the population the live chart's percentile scale
    would be normalizing against if the user were viewing that whole day.

    restrict_nonzero=True (the default, and this Atlas's documented
    choice): the reference population per context is restricted to cells
    with values > 0 - most (bar, price_level) cells on a 10-deep price
    axis have NO activity in any given bar, and including those degenerate
    zeros would collapse nearly the whole percentile distribution to 0,
    which is uninformative for distinguishing "thin but active" from "no
    activity at all". Zero-value cells are still assigned percentile_rank
    NaN (never silently coerced to 0) - "no information" is reported as
    missing, not as the bottom of the active distribution.
    """
    values = np.asarray(values, dtype=float)
    context = np.asarray(context)
    pct = np.full(len(values), np.nan)
    rank = np.full(len(values), np.nan)
    z = np.full(len(values), np.nan)
    df = pd.DataFrame({"v": values, "ctx": context})
    for ctx_val, grp in df.groupby("ctx"):
        idx = grp.index.to_numpy()
        v = grp["v"].to_numpy()
        pop_mask = (v > 0) if restrict_nonzero else np.isfinite(v)
        if pop_mask.sum() < 2:
            continue
        pop = v[pop_mask]
        mean_p, std_p = float(np.mean(pop)), float(np.std(pop))
        ranks_pop = pd.Series(pop).rank(method="average", pct=True).to_numpy() * 100.0
        rank_pop = pd.Series(pop).rank(method="average").to_numpy()
        pct[idx[pop_mask]] = ranks_pop
        rank[idx[pop_mask]] = rank_pop
        if std_p > 0:
            z[idx[pop_mask]] = (pop - mean_p) / std_p
        else:
            z[idx[pop_mask]] = 0.0
    return pct, rank, z


def classify_thin_fat(pct: np.ndarray, thin_max: float = 33.0, fat_min: float = 67.0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    pct = np.asarray(pct, dtype=float)
    is_thin = pct <= thin_max
    is_fat = pct >= fat_min
    is_medium = np.isfinite(pct) & ~is_thin & ~is_fat
    is_thin = np.isfinite(pct) & is_thin
    is_fat = np.isfinite(pct) & is_fat
    return is_thin, is_medium, is_fat
