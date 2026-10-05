"""
v2_common.py - shared READ-ONLY library for the AFML v2 IC-regime meta-label
engine.

Reads exclusively from:
  - v1 engine's frozen raw_snapshot/ and outputs/ (already static, read-only
    research artifacts - never opened for writing)
  - the diagnostic engine's frozen outputs/ (same)
Writes only inside THIS engine's own outputs/.

Implements, in spirit and substance, only:
  AFML Ch.3  - meta-labeling (unchanged from v1; re-used, not re-derived)
  AFML Ch.4  - sample weights / average uniqueness (re-used from the
               diagnostic, applied to the deduplicated population here)
  AFML Ch.7  - purging / embargo (re-used from v1/diagnostic, unchanged)
  AFML Ch.10 - bet sizing from probability, averaging active bets, size
               discretization (re-used from v1, unchanged)
plus a NEW, no-lookahead rolling-Spearman-IC feature-reliability layer
(this engine's only new methodology), built strictly from past-only,
already-resolved (feature, forward-return) pairs.

No discretionary trading rule is added anywhere in this module. No model
or signal produced here is connected to execution.

SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import yaml
from scipy.stats import norm, rankdata

V1_DIR = Path(
    "/home/prabh/OFI_Production/research_engines/afml_trade_lifecycle_shadow_v1_20260623T062435Z"
)
V1_SNAPSHOT_DIR = V1_DIR / "raw_snapshot"
V1_OUT_DIR = V1_DIR / "outputs"
DIAG_DIR = Path(
    "/home/prabh/OFI_Production/research_engines/afml_trade_lifecycle_v1_failure_diagnostic_20260623T065452Z"
)

ENGINE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = ENGINE_DIR / "outputs"
OUT_DIR.mkdir(exist_ok=True)
REPORTS_DIR = ENGINE_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
CONFIG_PATH = ENGINE_DIR / "configs" / "v2_config.yaml"

TICK_SIZE = 0.25
MODEL_TRAINING_CUTOFF_UTC = pd.Timestamp("2026-06-21T01:53:35.580300+00:00")


def log(msg: str) -> None:
    import time
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ── Read-only loaders: v1 frozen snapshot / outputs ─────────────────────────

def load_nqu6_master() -> pd.DataFrame:
    rows = []
    with open(V1_SNAPSHOT_DIR / "master_NQU6_shadow.ndjsonl") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    df = pd.DataFrame(rows).sort_values("bar_end_ts_ns", kind="stable")
    df = df.drop_duplicates(subset=["bar_end_ts_ns"], keep="first").reset_index(drop=True)
    return df


def list_level_candle_dates(depth: int) -> List[str]:
    import glob
    pattern = str(V1_SNAPSHOT_DIR / "cache" / f"book_flow_level_candles_NQU6_*_top{depth}.parquet")
    return sorted({Path(f).name.split("_")[5] for f in glob.glob(pattern)})


def load_level_candles(depth: int) -> pd.DataFrame:
    frames = []
    for d in list_level_candle_dates(depth):
        path = V1_SNAPSHOT_DIR / "cache" / f"book_flow_level_candles_NQU6_{d}_top{depth}.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        df["session_date"] = d
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def aggregate_book_flow_bars(level_df: pd.DataFrame) -> pd.DataFrame:
    """Identical formulas to v1/diagnostic (abs_flow-denominator convention)."""
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
    agg["bid_pull_pressure"] = agg["bid_pull"] / agg["abs_flow"].replace(0, np.nan)
    agg["ask_pull_pressure"] = agg["ask_pull"] / agg["abs_flow"].replace(0, np.nan)
    agg["ask_pull_minus_bid_pull"] = agg["ask_pull_pressure"] - agg["bid_pull_pressure"]
    agg["bid_add_minus_ask_add"] = agg["bid_add"] - agg["ask_add"]
    return agg


def load_v1_candidate_events() -> pd.DataFrame:
    return pd.read_parquet(V1_OUT_DIR / "candidate_events.parquet")


def load_v1_triple_barrier_labels() -> pd.DataFrame:
    return pd.read_parquet(V1_OUT_DIR / "triple_barrier_labels.parquet")


def load_v1_meta_label_dataset() -> pd.DataFrame:
    return pd.read_parquet(V1_OUT_DIR / "meta_label_dataset.parquet")


# ── Causal transforms (no lookahead; rolling() is always backward-looking) ──

def causal_z20(s: pd.Series, window: int = 20, min_periods: int = 5) -> pd.Series:
    mu = s.rolling(window, min_periods=min_periods).mean()
    sd = s.rolling(window, min_periods=min_periods).std()
    return (s - mu) / sd.replace(0, np.nan)


def causal_roll_sum(s: pd.Series, window: int = 5, min_periods: int = 3) -> pd.Series:
    return s.rolling(window, min_periods=min_periods).sum()


def add_forward_returns_day_bounded(df: pd.DataFrame, price_col: str, day_col: str,
                                     horizons: List[int]) -> pd.DataFrame:
    """Day-bounded forward log return, identical convention to v1/diagnostic:
    fwd_ret_H[t] is NaN if bar t+H falls in a different day or past the end
    of data. These are FUTURE values (used only as labels for the rolling-IC
    estimator below, never as model input features)."""
    out = df.copy()
    px = out[price_col].to_numpy()
    day = out[day_col].to_numpy()
    n = len(out)
    for h in horizons:
        fwd = np.full(n, np.nan)
        idx = np.arange(n)
        shifted = idx + h
        ok = shifted < n
        same_day = np.zeros(n, dtype=bool)
        same_day[ok] = day[idx[ok]] == day[shifted[ok]]
        valid = ok & same_day
        with np.errstate(divide="ignore", invalid="ignore"):
            fwd[valid] = np.log(px[shifted[valid]] / px[idx[valid]])
        out[f"fwd_ret_{h}"] = fwd
    return out


# ── NEW: no-lookahead rolling Spearman IC / feature-reliability ────────────

def _rolling_spearman_local_rank(x: np.ndarray, y: np.ndarray, window: int,
                                  min_periods: int) -> np.ndarray:
    """TRUE rolling Spearman correlation: ranks are recomputed LOCALLY within
    each trailing window (NOT a single global rank restricted to the window,
    which is only an approximation - global rank order within a sub-window
    need not be evenly spaced, so Pearson-of-restricted-global-ranks is not
    mathematically identical to Pearson-of-local-ranks=true Spearman; this
    was verified to differ from scipy.stats.spearmanr by a measurable margin
    in a synthetic test and is therefore computed exactly, not approximated,
    here). O(N*window*log(window)); ~0.6s per feature/horizon combo at
    N~5200, window=200 - cheap enough at this engine's data scale to never
    need the global-rank shortcut.
    """
    n = len(x)
    out = np.full(n, np.nan)
    for i in range(n):
        if i + 1 < min_periods:
            continue
        lo = max(0, i + 1 - window)
        xw, yw = x[lo:i + 1], y[lo:i + 1]
        if len(xw) < min_periods:
            continue
        rx, ry = rankdata(xw), rankdata(yw)
        if rx.std() == 0 or ry.std() == 0:
            continue
        out[i] = float(np.corrcoef(rx, ry)[0, 1])
    return out


def rolling_spearman_ic_no_lookahead(
    feature: pd.Series, fwd_ret_h: pd.Series, horizon: int, window_n: int,
    min_window_n: int, stability_n_subwindows: int = 4,
) -> pd.DataFrame:
    """
    Past-only rolling Spearman IC, per feature per horizon H.

    Requirement (verbatim): "For horizon H, rolling IC at bar t must only use
    samples whose forward return has already completed by t." Concretely:
    every (feature[i], fwd_ret_h[i]) pair used to estimate the IC value
    attached to bar t satisfies i + H <= t (the forward return resolved at
    or before t). The estimate attached to bar t uses the MOST RECENT such
    eligible window (i.e. the freshest available regime read that does not
    violate the constraint).

    Implementation: restrict to rows where BOTH feature and fwd_ret_h are
    defined (this also naturally excludes any day where the underlying
    feature has no coverage, e.g. book-flow features on 2026-06-14). Rank
    both series WITHIN that valid subsequence, take a rolling Pearson
    correlation of the ranks over `window_n` consecutive valid pairs
    (Spearman IC) - this series is indexed by VALID-SEQUENCE position, each
    of which maps back to one original bar index `orig_idx[s]`. The
    information in that window is only fully known once the LAST point in
    the window has resolved, i.e. at bar `orig_idx[s] + H`. The output
    `rolling_ic_*` for every original bar t is then an as-of (backward) lookup:
    the most recent valid-sequence position s with `orig_idx[s] + H <= t`.

    Returns a DataFrame indexed exactly like `feature` (same original bar
    positions, full range, with NaN wherever no eligible window exists yet)
    with columns: rolling_ic, rolling_ic_sign, rolling_ic_abs_strength,
    rolling_ic_tstat, rolling_ic_stability, rolling_ic_window_n.
    """
    n_bars = len(feature)
    valid_mask = feature.notna().to_numpy() & fwd_ret_h.notna().to_numpy()
    orig_idx = np.where(valid_mask)[0]
    out = pd.DataFrame(
        {c: np.full(n_bars, np.nan) for c in
         ["rolling_ic", "rolling_ic_sign", "rolling_ic_abs_strength",
          "rolling_ic_tstat", "rolling_ic_stability", "rolling_ic_window_n"]}
    )
    if len(orig_idx) < min_window_n:
        return out

    feat_valid = feature.to_numpy()[orig_idx]
    ret_valid = fwd_ret_h.to_numpy()[orig_idx]

    ic_at_pos = _rolling_spearman_local_rank(feat_valid, ret_valid, window_n, min_window_n)
    n_used_at_pos = np.minimum(np.arange(1, len(orig_idx) + 1), window_n).astype(float)
    n_used_at_pos[np.isnan(ic_at_pos)] = np.nan

    short_window = max(5, window_n // stability_n_subwindows)
    short_ic = _rolling_spearman_local_rank(
        feat_valid, ret_valid, short_window, max(short_window // 2, 5)
    )
    full_sign = np.sign(ic_at_pos)
    agree = np.zeros(len(orig_idx))
    n_compared = np.zeros(len(orig_idx))
    for k in range(stability_n_subwindows):
        shift_k = k * short_window
        shifted = np.full(len(orig_idx), np.nan)
        if shift_k < len(orig_idx):
            shifted[shift_k:] = short_ic[: len(orig_idx) - shift_k]
        cmp_mask = ~np.isnan(shifted) & ~np.isnan(full_sign)
        agree[cmp_mask] += (np.sign(shifted[cmp_mask]) == full_sign[cmp_mask]).astype(float)
        n_compared[cmp_mask] += 1
    with np.errstate(invalid="ignore"):
        stability_at_pos = np.divide(agree, n_compared, out=np.full(len(orig_idx), np.nan), where=n_compared > 0)

    with np.errstate(invalid="ignore", divide="ignore"):
        tstat_at_pos = ic_at_pos * np.sqrt(np.clip(n_used_at_pos - 2, 0, None)) / np.sqrt(
            np.clip(1 - ic_at_pos ** 2, 1e-12, None)
        )

    # as-of (backward) lookup: for every original bar t, find the most recent
    # valid-sequence position s with orig_idx[s] + H <= t
    t_minus_h_target = np.arange(n_bars) - horizon
    pos = np.searchsorted(orig_idx, t_minus_h_target, side="right") - 1
    has_val = pos >= 0
    pos_clipped = np.clip(pos, 0, len(orig_idx) - 1)

    out.loc[has_val, "rolling_ic"] = ic_at_pos[pos_clipped[has_val]]
    out.loc[has_val, "rolling_ic_window_n"] = n_used_at_pos[pos_clipped[has_val]]
    out.loc[has_val, "rolling_ic_tstat"] = tstat_at_pos[pos_clipped[has_val]]
    out.loc[has_val, "rolling_ic_stability"] = stability_at_pos[pos_clipped[has_val]]
    out["rolling_ic_sign"] = np.sign(out["rolling_ic"])
    out["rolling_ic_abs_strength"] = out["rolling_ic"].abs()
    return out


# ── Re-used Ch.4 concurrency / uniqueness (identical algorithm to diagnostic) ──

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


def cluster_by_overlap(day: np.ndarray, t0_idx: np.ndarray, t1_idx: np.ndarray) -> np.ndarray:
    """Connected components of the 1-D overlap graph (merge-overlapping-
    intervals sweep), identical algorithm to the diagnostic engine."""
    n = len(t0_idx)
    cluster_id = np.full(n, -1, dtype=np.int64)
    valid = np.where((t0_idx >= 0) & (t1_idx >= 0))[0]
    if len(valid) == 0:
        return cluster_id
    df = pd.DataFrame({"idx": valid, "day": day[valid], "t0": t0_idx[valid], "t1": t1_idx[valid]})
    df = df.sort_values(["day", "t0", "t1"], kind="stable")
    next_cluster = 0
    current_day = None
    running_hi = -1
    current_cluster = -1
    for row in df.itertuples(index=False):
        if row.day != current_day or row.t0 > running_hi:
            current_cluster = next_cluster
            next_cluster += 1
            running_hi = row.t1
            current_day = row.day
        else:
            running_hi = max(running_hi, row.t1)
            current_day = row.day
        cluster_id[row.idx] = current_cluster
    return cluster_id


# ── Re-used Ch.7 purge/embargo (identical algorithm to v1/diagnostic) ──────

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


# ── Re-used Ch.10 bet sizing / averaging (identical formulas to v1) ────────

def meta_prob_to_size(p_meta: np.ndarray, side: np.ndarray, num_classes: int = 2) -> np.ndarray:
    p = np.clip(p_meta, 1e-6, 1 - 1e-6)
    z = (p - 1.0 / num_classes) / np.sqrt(p * (1 - p))
    magnitude = np.clip(2 * norm.cdf(z) - 1, 0, 1)
    return side * magnitude


def discretize_signal(signal: np.ndarray, step_size: float) -> np.ndarray:
    out = np.round(signal / step_size) * step_size
    return np.clip(out, -1.0, 1.0)


def avg_active_signals(events: pd.DataFrame, bar_index: np.ndarray,
                        t0_col: str = "t0_idx", t1_col: str = "t1_idx",
                        size_col: str = "discretized_size") -> pd.DataFrame:
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
    return pd.DataFrame({"bar_idx": bar_index, "active_bets_count": counts, "average_active_signal": avg})


# ── Metrics (identical formulas to v1/diagnostic) ──────────────────────────

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


def wilson_ci(n_success: float, n_total: float, z: float = 1.96) -> Tuple[float, float]:
    if n_total <= 0:
        return (np.nan, np.nan)
    p = n_success / n_total
    denom = 1 + z**2 / n_total
    centre = p + z**2 / (2 * n_total)
    half = z * np.sqrt(max(p * (1 - p) / n_total, 0) + z**2 / (4 * n_total**2))
    lo = (centre - half) / denom
    hi = (centre + half) / denom
    return (float(lo), float(hi))
