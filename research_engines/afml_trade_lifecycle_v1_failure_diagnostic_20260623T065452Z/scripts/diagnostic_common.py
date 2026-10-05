"""
diagnostic_common.py - READ-ONLY AFML Chapter 4 diagnostic library for the
v1 failure diagnostic.

Reads exclusively from the v1 engine's own frozen artifacts:
  /home/prabh/OFI_Production/research_engines/afml_trade_lifecycle_shadow_v1_20260623T062435Z/
    raw_snapshot/   (already a frozen, read-only copy of production inputs)
    outputs/        (already-generated, static research parquet/csv)
Never modifies anything in v1's folder. Never touches any live production
path. Writes only inside THIS diagnostic engine's own outputs/.

Implements ONLY AFML Chapter 4 methodology (sample weights / uniqueness):
  - snippet 4.1  mpNumCoEvents -> num_co_events()      (label concurrency c_t)
  - snippet 4.2  mpSampleTW    -> average_uniqueness()  (per-event avg 1/c_t)
  - snippet 4.10 mpSampleW     -> return_attribution_weight()
  - snippet 4.11 getTimeDecay  -> time_decay_weights()
plus an interval-overlap clustering helper (connected components of the
1-D overlap graph implied by the SAME concurrency structure - not a new
heuristic, just the natural grouping AFML's own c_t already defines).

SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

V1_DIR = Path(
    "/home/prabh/OFI_Production/research_engines/afml_trade_lifecycle_shadow_v1_20260623T062435Z"
)
V1_SNAPSHOT_DIR = V1_DIR / "raw_snapshot"
V1_OUT_DIR = V1_DIR / "outputs"

DIAG_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = DIAG_DIR / "outputs"
OUT_DIR.mkdir(exist_ok=True)
REPORTS_DIR = DIAG_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)


def log(msg: str) -> None:
    import time
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ── Load v1's own frozen artifacts (read-only) ──────────────────────────────

def load_v1_candidate_events() -> pd.DataFrame:
    return pd.read_parquet(V1_OUT_DIR / "candidate_events.parquet")


def load_v1_triple_barrier_labels() -> pd.DataFrame:
    return pd.read_parquet(V1_OUT_DIR / "triple_barrier_labels.parquet")


def load_v1_meta_label_dataset() -> pd.DataFrame:
    return pd.read_parquet(V1_OUT_DIR / "meta_label_dataset.parquet")


def load_v1_meta_model_predictions() -> pd.DataFrame:
    return pd.read_parquet(V1_OUT_DIR / "meta_model_predictions.parquet")


def load_v1_bet_sizing() -> pd.DataFrame:
    return pd.read_parquet(V1_OUT_DIR / "bet_sizing_signal.parquet")


def load_v1_lifecycle() -> pd.DataFrame:
    return pd.read_parquet(V1_OUT_DIR / "shadow_position_lifecycle.parquet")


def load_v1_active_bet_timeline() -> pd.DataFrame:
    return pd.read_parquet(V1_OUT_DIR / "active_bet_timeline.parquet")


def load_v1_fold_results() -> pd.DataFrame:
    return pd.read_csv(V1_OUT_DIR / "fold_results.csv")


def load_nqu6_master() -> pd.DataFrame:
    """Read-only load of the SAME frozen NQU6 master snapshot v1 used."""
    import json
    rows = []
    with open(V1_SNAPSHOT_DIR / "master_NQU6_shadow.ndjsonl") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    df = pd.DataFrame(rows).sort_values("bar_end_ts_ns", kind="stable")
    df = df.drop_duplicates(subset=["bar_end_ts_ns"], keep="first").reset_index(drop=True)
    return df


# ── AFML Ch.4 snippet 4.1: mpNumCoEvents (label concurrency) ───────────────

def num_co_events(t0_idx: np.ndarray, t1_idx: np.ndarray, n_bars: int) -> np.ndarray:
    """c_t = number of events whose [t0,t1] interval contains bar t, for every
    bar t in [0, n_bars). Events with t1<0 or t0<0 (never resolved/no side)
    are excluded - concurrency is defined over LABELED outcome windows only."""
    valid = (t0_idx >= 0) & (t1_idx >= 0)
    c = np.zeros(n_bars, dtype=np.int64)
    for t0, t1 in zip(t0_idx[valid], t1_idx[valid]):
        lo, hi = max(0, int(t0)), min(n_bars - 1, int(t1))
        if lo <= hi:
            c[lo:hi + 1] += 1
    return c


# ── AFML Ch.4 snippet 4.2: mpSampleTW (average uniqueness per event) ───────

def average_uniqueness(t0_idx: np.ndarray, t1_idx: np.ndarray, c_t: np.ndarray) -> np.ndarray:
    """For each event i, avg_uniqueness_i = mean over t in [t0_i,t1_i] of 1/c_t.
    A value of 1.0 means the event never overlapped any other label; a value
    of 1/120 means it spent its whole life inside a 120-way-overlapping burst."""
    out = np.full(len(t0_idx), np.nan)
    for i, (t0, t1) in enumerate(zip(t0_idx, t1_idx)):
        if t0 < 0 or t1 < 0:
            continue
        lo, hi = max(0, int(t0)), min(len(c_t) - 1, int(t1))
        if lo > hi:
            continue
        window = c_t[lo:hi + 1]
        out[i] = float(np.mean(1.0 / window))
    return out


# ── AFML Ch.4 snippet 4.10: mpSampleW (return-attribution weight) ──────────

def return_attribution_weight(t0_idx: np.ndarray, t1_idx: np.ndarray, c_t: np.ndarray,
                               log_ret: np.ndarray) -> np.ndarray:
    """w_i = | sum_{t=t0_i+1}^{t1_i} (r_t / c_t) |  - each event is credited
    with the return that occurred during its life, but that return is first
    SPLIT EQUALLY among every event concurrently active at bar t (c_t-way
    split), exactly as AFML snippet 4.10 specifies. Events whose life
    overlaps a large burst therefore each attribute only a small SHARE of
    the underlying market move, even though their realized_points (the raw,
    un-shared P&L) is identical to every other event in that same burst -
    this is precisely the mechanism that should have down-weighted the
    120-way-duplicated bar 2689 burst during meta-model training."""
    out = np.full(len(t0_idx), np.nan)
    for i, (t0, t1) in enumerate(zip(t0_idx, t1_idx)):
        if t0 < 0 or t1 < 0:
            continue
        lo, hi = max(0, int(t0) + 1), min(len(c_t) - 1, int(t1))
        if lo > hi:
            out[i] = 0.0
            continue
        out[i] = float(np.abs(np.sum(log_ret[lo:hi + 1] / c_t[lo:hi + 1])))
    return out


# ── AFML Ch.4 snippet 4.11: getTimeDecay (piecewise-linear time decay) ─────

def time_decay_weights(t1_idx: np.ndarray, avg_uniqueness: np.ndarray,
                        oldest_weight: float = 0.5) -> np.ndarray:
    """Sort by t1 (chronological resolution order), build the cumulative sum
    of average uniqueness, then apply a linear decay so the MOST RECENTLY
    resolved observation has weight 1.0 and the OLDEST has weight
    `oldest_weight` (a priori research default - not tuned on any result;
    AFML Ch.4 notes this can also go negative to hard-zero a fraction of the
    oldest data, which this engine does not use). NaN avg_uniqueness (never
    resolved) gets NaN decay weight."""
    n = len(t1_idx)
    out = np.full(n, np.nan)
    valid_idx = np.where((t1_idx >= 0) & np.isfinite(avg_uniqueness))[0]
    if len(valid_idx) == 0:
        return out
    order = valid_idx[np.argsort(t1_idx[valid_idx], kind="stable")]
    cum_u = np.cumsum(avg_uniqueness[order])
    total_u = cum_u[-1] if cum_u[-1] > 0 else 1.0
    slope = (1.0 - oldest_weight) / total_u
    decay = oldest_weight + slope * cum_u
    out[order] = decay
    return out


def combined_sample_weight(uniqueness: np.ndarray, return_attr: np.ndarray,
                            time_decay: np.ndarray) -> np.ndarray:
    """AFML Ch.4's layered combination: uniqueness * |return attribution|,
    further scaled by time-decay, then rescaled so the weights usable by
    sklearn's sample_weight average to 1.0 across valid rows (a pure
    rescaling - it does not change which events are relatively up- or
    down-weighted, only puts them on a convenient absolute scale)."""
    raw = uniqueness * return_attr * time_decay
    valid = np.isfinite(raw)
    if valid.sum() == 0:
        return raw
    mean_valid = np.nanmean(raw[valid])
    if mean_valid > 0:
        raw = raw / mean_valid
    return raw


# ── Interval-overlap clustering (connected components of the c_t graph) ───

def cluster_by_overlap(day: np.ndarray, t0_idx: np.ndarray, t1_idx: np.ndarray) -> np.ndarray:
    """Assigns a cluster_id to every (valid) event such that two events share
    a cluster iff they are connected by a chain of pairwise-overlapping
    [t0,t1] windows WITHIN THE SAME DAY. This is exactly the classic
    'merge overlapping intervals' sweep (sort by t0, extend the running
    cluster's t1-high-water-mark, start a new cluster when the next event's
    t0 exceeds it) - it is not a new heuristic, it is the connected-component
    structure already implied by AFML's own concurrency definition (two
    events that overlap necessarily share at least one bar's c_t count).
    Events with t0<0 or t1<0 get cluster_id = -1 (unclustered / never resolved).
    """
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


# ── AFML Ch.7: purged / embargoed day-based walk-forward splits ───────────
# (re-implemented here, identical algorithm to v1's afml_common.py, so this
# diagnostic can re-train controlled variants on EXACTLY the same folds
# without importing across engine folders or modifying v1's code/outputs.)

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


# ── Metrics (identical formulas to v1's afml_common.py) ────────────────────

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


def wilson_ci(n_success: int, n_total: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score confidence interval for a binomial proportion - used here
    to test whether a hit-rate estimated on a small EFFECTIVE n could still
    plausibly be 0.5 (no edge)."""
    if n_total == 0:
        return (np.nan, np.nan)
    p = n_success / n_total
    denom = 1 + z**2 / n_total
    centre = p + z**2 / (2 * n_total)
    half = z * np.sqrt(p * (1 - p) / n_total + z**2 / (4 * n_total**2))
    lo = (centre - half) / denom
    hi = (centre + half) / denom
    return (float(lo), float(hi))
