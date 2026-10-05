"""
afml_common.py - shared READ-ONLY data loading + AFML methodology library for
the AFML Trade Lifecycle Shadow Engine v1.

SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO DATABENTO.
Reads only from raw_snapshot/ (frozen at engine build time by
00_snapshot_inputs.py). Never writes outside this engine's own outputs/.
Never imports/calls broker, order-routing, dashboard, or Book Flow chart
modules. No fit()/predict() result here is ever connected to execution.

Implements, in spirit and substance, only methodology from Marcos Lopez de
Prado's "Advances in Financial Machine Learning" (AFML):
  Ch.3  - dynamic volatility thresholds, triple-barrier labeling, meta-labeling
  Ch.7  - purging / embargo
  Ch.10 - bet sizing from predicted probabilities, averaging active bets,
          size discretization
No discretionary trading rule is implemented anywhere in this module.
"""
from __future__ import annotations

import json
import glob
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml
from scipy import stats
from scipy.stats import norm

warnings.filterwarnings("ignore")

ENGINE_DIR = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = ENGINE_DIR / "raw_snapshot"
OUT_DIR = ENGINE_DIR / "outputs"
OUT_DIR.mkdir(exist_ok=True)
CONFIG_PATH = ENGINE_DIR / "configs" / "lifecycle_config.yaml"

MASTER_CONTINUOUS_PATH = SNAPSHOT_DIR / "master_NQ_continuous_backadjusted_shadow.ndjsonl"
MASTER_NQU6_PATH = SNAPSHOT_DIR / "master_NQU6_shadow.ndjsonl"
PREDICTIONS_CSV = SNAPSHOT_DIR / "latest_continuous_nq_predictions.csv"
BOOK_FLOW_CACHE_DIR = SNAPSHOT_DIR / "cache"

TICK_SIZE = 0.25
# Active release training cutoff (level_reaction_continuous_nq_shadow_20260621T015009Z,
# training_config.json generated_at_utc). Verified in the prior institutional audit
# (research_reports/ofi_model_level_alpha_research_20260623T014539Z) that 99.2% of
# predictions.csv rows predate this timestamp and are therefore IN-SAMPLE, not a clean
# forecast log. Any event with timestamp_utc <= this cutoff MUST be flagged
# IN_SAMPLE_CONTAMINATED in this engine; only events strictly after it are clean.
MODEL_TRAINING_CUTOFF_UTC = pd.Timestamp("2026-06-21T01:53:35.580300+00:00")


def log(msg: str) -> None:
    import time
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ── Master loaders ───────────────────────────────────────────────────────────

def _read_ndjsonl(path: Path) -> pd.DataFrame:
    rows = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return pd.DataFrame(rows)


def load_nqu6_master(dedup: bool = True) -> pd.DataFrame:
    df = _read_ndjsonl(MASTER_NQU6_PATH)
    df = df.sort_values("bar_end_ts_ns", kind="stable").reset_index(drop=True)
    if dedup:
        df = df.drop_duplicates(subset=["bar_end_ts_ns"], keep="first").reset_index(drop=True)
    return df


def load_predictions() -> pd.DataFrame:
    df = pd.read_csv(PREDICTIONS_CSV)
    df["_orig_idx"] = np.arange(len(df))
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    return df


def list_level_candle_dates(depth: int) -> List[str]:
    pattern = str(BOOK_FLOW_CACHE_DIR / f"book_flow_level_candles_NQU6_*_top{depth}.parquet")
    return sorted({Path(f).name.split("_")[5] for f in glob.glob(pattern)})


def load_level_candles(depth: int, dates: Optional[List[str]] = None) -> pd.DataFrame:
    if dates is None:
        dates = list_level_candle_dates(depth)
    frames = []
    for d in dates:
        path = BOOK_FLOW_CACHE_DIR / f"book_flow_level_candles_NQU6_{d}_top{depth}.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        df["session_date"] = d
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def aggregate_book_flow_bars(level_df: pd.DataFrame) -> pd.DataFrame:
    """Bar-level true-book-flow OFI features (bid/ask add-pull), CLOSED bars only.
    Same formulas as the prior institutional audit (book_flow_formula_alpha_audit
    / book_flow_deleaked_alpha_validation): bid_pull_pressure = bid_pull/abs_flow,
    etc. (abs_flow-denominator convention, validated there)."""
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
            close_price=("close_price", "last"), bar_end_ts_ns=("bar_end_ts_ns", "last"),
        )
        .reset_index()
        .sort_values(["session_date", "bar_idx"])
        .reset_index(drop=True)
    )
    eps = 1e-9
    agg["bid_pull_pressure"] = agg["bid_pull"] / agg["abs_flow"].replace(0, np.nan)
    agg["ask_pull_pressure"] = agg["ask_pull"] / agg["abs_flow"].replace(0, np.nan)
    agg["ask_pull_minus_bid_pull"] = agg["ask_pull_pressure"] - agg["bid_pull_pressure"]
    agg["bid_add_minus_ask_add"] = agg["bid_add"] - agg["ask_add"]
    return agg


# ── AFML Ch.3: dynamic volatility threshold (getDailyVol analogue) ─────────

def ewm_log_return_vol(close: pd.Series, span_bars: int, min_periods: int) -> pd.Series:
    """Causal EWM standard deviation of log returns - the bar-level analogue of
    AFML snippet 3.1 getDailyVol. Uses only data up to and including bar t
    (no centered window, no lookahead)."""
    log_ret = np.log(close).diff()
    vol = log_ret.ewm(span=span_bars, min_periods=min_periods).std()
    return vol


# ── AFML Ch.3: side-aware triple-barrier first-touch ────────────────────────

def apply_triple_barrier(
    t0_idx: np.ndarray, side: np.ndarray, close: np.ndarray, high: np.ndarray,
    low: np.ndarray, vol: np.ndarray, day: np.ndarray, pt_multiple: float,
    sl_multiple: float, vertical_barrier_bars: int, tie_break: str = "SL_FIRST",
) -> Dict[str, np.ndarray]:
    """
    Side-aware AFML triple-barrier first-touch labeling (Ch.3, snippets 3.2-3.4),
    adapted to bar-indexed vol500 series with day-bounded vertical barrier (no
    cross-session bleed). For side=+1 (long): PT is ABOVE close_t0, SL is BELOW.
    For side=-1 (short): PT is BELOW close_t0, SL is ABOVE. For side=0 (no
    primary call): barriers are not computed (NaN) - there is no side to label.

    Returns arrays aligned to t0_idx: t1_idx, label_primary, first_touch,
    realized_ret (signed by side, + = favorable), realized_points (signed),
    mfe_points, mae_points (both unsigned, in the side-favorable / side-adverse
    sense), holding_bars, pt_price, sl_price.
    """
    n = len(t0_idx)
    t1_idx = np.full(n, -1, dtype=np.int64)
    label_primary = np.full(n, np.nan)
    first_touch = np.array([""] * n, dtype=object)
    realized_ret = np.full(n, np.nan)
    realized_points = np.full(n, np.nan)
    mfe_points = np.full(n, np.nan)
    mae_points = np.full(n, np.nan)
    holding_bars = np.full(n, np.nan)
    pt_price_arr = np.full(n, np.nan)
    sl_price_arr = np.full(n, np.nan)
    n_bars_total = len(close)

    for i in range(n):
        t0 = t0_idx[i]
        s = side[i]
        v = vol[i]
        if s == 0 or not np.isfinite(v) or v <= 0:
            continue
        c0 = close[t0]
        d0 = day[t0]
        pt_price = c0 * np.exp(s * pt_multiple * v)
        sl_price = c0 * np.exp(-s * sl_multiple * v)
        pt_price_arr[i] = pt_price
        sl_price_arr[i] = sl_price

        # vertical barrier: day-bounded, max vertical_barrier_bars ahead
        vb_idx = min(t0 + vertical_barrier_bars, n_bars_total - 1)
        while vb_idx > t0 and day[vb_idx] != d0:
            vb_idx -= 1
        if vb_idx <= t0:
            continue  # no room to label (end of day/data right at t0)

        touch_idx = None
        touch_kind = None
        best_fav = 0.0
        worst_adv = 0.0
        for j in range(t0 + 1, vb_idx + 1):
            if day[j] != d0:
                break
            hi, lo = high[j], low[j]
            if s > 0:
                fav_excursion = hi - c0
                adv_excursion = c0 - lo
                hit_pt = hi >= pt_price
                hit_sl = lo <= sl_price
            else:
                fav_excursion = c0 - lo
                adv_excursion = hi - c0
                hit_pt = lo <= pt_price
                hit_sl = hi >= sl_price
            best_fav = max(best_fav, fav_excursion)
            worst_adv = max(worst_adv, adv_excursion)
            if hit_pt and hit_sl:
                touch_idx, touch_kind = j, ("SL" if tie_break == "SL_FIRST" else "PT")
                break
            elif hit_pt:
                touch_idx, touch_kind = j, "PT"
                break
            elif hit_sl:
                touch_idx, touch_kind = j, "SL"
                break

        if touch_idx is None:
            touch_idx, touch_kind = vb_idx, "VB"

        exit_price = close[touch_idx]
        ret_signed = s * np.log(exit_price / c0)
        pts_signed = s * (exit_price - c0)
        if touch_kind == "PT":
            lbl = 1.0
        elif touch_kind == "SL":
            lbl = -1.0
        else:
            lbl = float(np.sign(ret_signed))

        t1_idx[i] = touch_idx
        label_primary[i] = lbl
        first_touch[i] = touch_kind
        realized_ret[i] = ret_signed
        realized_points[i] = pts_signed
        mfe_points[i] = best_fav
        mae_points[i] = worst_adv
        holding_bars[i] = touch_idx - t0

    return dict(
        t1_idx=t1_idx, label_primary=label_primary, first_touch=first_touch,
        realized_ret=realized_ret, realized_points=realized_points,
        mfe_points=mfe_points, mae_points=mae_points, holding_bars=holding_bars,
        pt_price=pt_price_arr, sl_price=sl_price_arr,
    )


# ── AFML Ch.10: bet sizing from predicted probability ───────────────────────

def meta_prob_to_size(p_meta: np.ndarray, side: np.ndarray, num_classes: int = 2) -> np.ndarray:
    """
    AFML Ch.10 snippet 10.1 (getSignal) probability-to-size mapping, adapted to
    the meta-labeling constraint that the meta-model must NOT learn/flip side
    (AFML Ch.3 Sec 3.6): magnitude = max(0, 2*Phi(z) - 1) where
    z = (p - 1/num_classes) / sqrt(p*(1-p)) is the t-value of the predicted
    probability versus the 1/num_classes null, and Phi is the standard normal
    CDF. The max(0, .) clip (rather than allowing the AFML formula's natural
    sign flip below p=0.5) is the explicit meta-labeling adaptation: a
    meta-probability below 0.5 means "more likely to fail than succeed" ->
    zero size / pass, never a bet AGAINST the primary side.
    raw_size = side * magnitude, in [-1, 1].
    """
    p = np.clip(p_meta, 1e-6, 1 - 1e-6)
    z = (p - 1.0 / num_classes) / np.sqrt(p * (1 - p))
    magnitude = np.clip(2 * norm.cdf(z) - 1, 0, 1)
    return side * magnitude


def discretize_signal(signal: np.ndarray, step_size: float) -> np.ndarray:
    """AFML Ch.10 snippet 10.4 discreteSignal: round to the nearest step,
    clipped to [-1, 1], to avoid excessive bet-size jitter / over-trading."""
    out = np.round(signal / step_size) * step_size
    return np.clip(out, -1.0, 1.0)


# ── AFML Ch.10: averaging active bets ────────────────────────────────────────

def avg_active_signals(events: pd.DataFrame, bar_index: np.ndarray,
                        t0_col: str = "t0_idx", t1_col: str = "t1_idx",
                        size_col: str = "discretized_size") -> pd.DataFrame:
    """
    AFML Ch.10 snippet 10.2 (avgActiveSignals) analogue: for every bar in
    bar_index, average the size of every event whose [t0_idx, t1_idx] interval
    contains that bar (an "active" bet at that bar). Events with t1_idx<0
    (never resolved / no side) are excluded. Returns one row per bar with
    active_bets_count and average_active_signal (0 if no active bets that bar).
    """
    ev = events[(events[t1_col] >= 0) & (events[t0_col] >= 0)]
    n_bars = len(bar_index)
    sums = np.zeros(n_bars)
    counts = np.zeros(n_bars, dtype=np.int64)
    for t0, t1, sz in zip(ev[t0_col].to_numpy(), ev[t1_col].to_numpy(), ev[size_col].to_numpy()):
        lo, hi = max(0, t0), min(n_bars - 1, t1)
        if lo > hi:
            continue
        sums[lo:hi + 1] += sz
        counts[lo:hi + 1] += 1
    avg = np.divide(sums, counts, out=np.zeros_like(sums), where=counts > 0)
    return pd.DataFrame({
        "bar_idx": bar_index, "active_bets_count": counts, "average_active_signal": avg,
    })


# ── AFML Ch.7: purged / embargoed day-based walk-forward splits ────────────

def purged_embargo_day_splits(events: pd.DataFrame, day_col: str, t0_col: str, t1_col: str,
                               embargo_bars: int) -> List[Dict]:
    """
    Day-based expanding-window walk-forward split (test = one calendar day at a
    time, train = all STRICTLY PRIOR days), with explicit AFML Ch.7 purge +
    embargo applied on top:
      - PURGE: any training-set event whose [t0,t1] interval overlaps the test
        day's bar-index range is removed from that fold's training set (even
        though it is chronologically "prior", its label could still encode
        information that resolves inside the test window).
      - EMBARGO: events in the `embargo_bars` immediately following the test
        day's last bar are also removed from training in every subsequent
        fold (serial-correlation safety margin, AFML Ch.7 Sec 7.4).
    Returns a list of dicts: {test_day, train_idx, test_idx, n_purged, n_embargoed}.
    """
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

        # embargo: events starting within embargo_bars after this test day's
        # last bar get excluded from training in all FUTURE folds too
        embargo_hi = test_hi + embargo_bars
        newly_embargoed = events.index[
            (events[t0_col] > test_hi) & (events[t0_col] <= embargo_hi)
        ].to_numpy()
        embargoed_idx.update(newly_embargoed)

    return folds


# ── Metrics ───────────────────────────────────────────────────────────────

def mcc_binary(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    y_true, y_pred = y_true[mask], y_pred[mask]
    if len(y_true) < 5:
        return np.nan
    tp = np.sum((y_pred == 1) & (y_true == 1)); tn = np.sum((y_pred == 0) & (y_true == 0))
    fp = np.sum((y_pred == 1) & (y_true == 0)); fn = np.sum((y_pred == 0) & (y_true == 1))
    denom = np.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    return float((tp * tn - fp * fn) / denom) if denom > 0 else 0.0


def precision_recall_f1(y_true: np.ndarray, y_pred: np.ndarray) -> Tuple[float, float, float]:
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    y_true, y_pred = y_true[mask], y_pred[mask]
    tp = np.sum((y_pred == 1) & (y_true == 1)); fp = np.sum((y_pred == 1) & (y_true == 0))
    fn = np.sum((y_pred == 0) & (y_true == 1))
    precision = tp / (tp + fp) if (tp + fp) > 0 else np.nan
    recall = tp / (tp + fn) if (tp + fn) > 0 else np.nan
    f1 = 2 * precision * recall / (precision + recall) if precision and recall and (precision + recall) > 0 else np.nan
    return float(precision), float(recall), float(f1)


def balanced_accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    y_true, y_pred = y_true[mask], y_pred[mask]
    if len(y_true) < 5:
        return np.nan
    tp = np.sum((y_pred == 1) & (y_true == 1)); fn = np.sum((y_pred == 0) & (y_true == 1))
    tn = np.sum((y_pred == 0) & (y_true == 0)); fp = np.sum((y_pred == 1) & (y_true == 0))
    sens = tp / (tp + fn) if (tp + fn) > 0 else np.nan
    spec = tn / (tn + fp) if (tn + fp) > 0 else np.nan
    return float(np.nanmean([sens, spec]))
