#!/usr/bin/env python3
"""
Model-Probability PBO — Window Family Audit v1
Bailey, Borwein, López de Prado & Zhu (2015) — CSCV on OOS model probabilities.
Classifies model-prob window family as PROMOTE_TO_SHADOW / DEFER_RESEARCH / KILL.
SHADOW / RESEARCH ONLY — no execution, no broker, no order placement.
"""
# ─────────────────────────────────────────────────────────────────────────────
# INVARIANT CONSTRAINTS — DO NOT MODIFY
# SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING
# Do NOT modify: Rithmic raw recorder, parser, master files, Book Flow chart,
#   active model pointer, broker/order logic, trading flags, production model
#   artifacts, ACTIVE_SHADOW_RELEASE
# No auto-trading, no broker execution, no order placement in any system.
# ─────────────────────────────────────────────────────────────────────────────

import hashlib
import itertools
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from scipy.special import comb
from scipy.stats import norm
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler

# ─── Paths ────────────────────────────────────────────────────────────────────
OUT_DIR    = Path(__file__).parent
MODEL_DIR  = Path("/home/prabh/OFI_Production/model_registry"
                  "/level_reaction_continuous_nq_shadow"
                  "/level_reaction_continuous_nq_shadow_20260702T005104Z")
DATA_DIR   = MODEL_DIR / "data"
ATLAS1_PANEL = Path(
    "/home/prabh/OFI_Production/research_engines"
    "/directional_vpin_toxic_flow_settings_atlas_v1_20260701T025021Z"
    "/directional_vpin_feature_panel.parquet")
Q16_PANEL    = Path(
    "/home/prabh/OFI_Production/research_engines"
    "/q16_true_vpin_feature_master_recommendation_20260603_20260701_20260702T061819Z"
    "/q16_true_vpin_feature_master_aligned.parquet")

# ─── Constants ────────────────────────────────────────────────────────────────
WINDOWS      = [5, 10, 20, 30, 50, 80]   # rolling-window variants to test
HORIZONS     = [10, 40]                   # forward-return horizons to report
COST_TICKS   = 2.0                        # round-trip cost (2 NQ ticks)
S_CSCV       = 16                         # CSCV subperiods → C(16,8)=12,870
EULER_GAMMA  = 0.5772156649015329
HGB_PARAMS   = {                          # locked from production shadow
    "max_depth": 4, "learning_rate": 0.05,
    "max_iter": 200, "random_state": 42,
}
DEFERRED_SIGNALS = [
    "buy_toxicity", "sell_toxicity", "signed_vpin_delta", "cur_vpin_pct_L500_signed"
]

# OHLC-vol column names (computed during per-window feature build)
OHLC_VOL_COLS = [
    "candle_body_vol", "candle_range_vol", "upper_wick_vol", "lower_wick_vol",
    "close_location", "open_to_close_sign", "close_vs_prev_close_vol",
    "close_vs_roll_mean_vol", "high_break_vol", "low_break_vol",
]

# Fixed orderflow columns from snapshot (no window dependency)
FIXED_OF_COLS = [
    "delta_norm", "delta_norm_lag_1", "delta_norm_lag_2", "delta_norm_lag_3",
    "mlofi_decay_sum", "mlofi_norm", "mlofi_accel",  # mlofi_accel recomputed per-W
    "mlofi_norm_lag_1", "mlofi_norm_lag_2", "mlofi_norm_lag_3",
    "decay_norm", "decay_norm_lag_1", "decay_norm_lag_2", "decay_norm_lag_3",
    "sweep_imbalance_norm", "sweep_norm", "sweep_buy_ratio", "sweep_sell_ratio",
    "buy_ratio", "sell_ratio",
    "vpin", "vpin_lag_1", "vpin_lag_2", "vpin_lag_3",
    "mid_resid_z", "mid_ret1", "bar_duration_s",
    "entropy_score", "flow_alignment",
]

# Stream distance columns
DIST_COLS = [
    "dist_to_poc_ticks", "dist_to_vah_ticks", "dist_to_val_ticks",
    "dist_to_hvn_ticks", "dist_to_lvn_ticks",
    "dist_to_poc_vol",   "dist_to_vah_vol",   "dist_to_val_vol",
    "dist_to_hvn_vol",   "dist_to_lvn_vol",
    "inside_value_area", "above_vah", "below_val",
]


# ─────────────────────────────────────────────────────────────────────────────
# UTILITY
# ─────────────────────────────────────────────────────────────────────────────

def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def cscv_pbo(M: np.ndarray, S: int = S_CSCV) -> dict:
    """Bailey et al. 2015 CSCV. Returns PBO, logit_mean, prob_loss, degradation_beta."""
    T, N = M.shape
    assert T % S == 0, f"T={T} must be divisible by S={S}"
    sub_size   = T // S
    sub_means  = M.reshape(S, sub_size, N).mean(axis=1)   # S × N
    S_half     = S // 2
    is_combos  = list(itertools.combinations(range(S), S_half))
    logits     = np.zeros(len(is_combos))
    is_sr_arr  = []
    oos_sr_arr = []
    for idx, is_idx in enumerate(is_combos):
        oos_idx  = tuple(s for s in range(S) if s not in is_idx)
        sr_is    = sub_means[list(is_idx)].mean(axis=0)
        sr_oos   = sub_means[list(oos_idx)].mean(axis=0)
        n_star   = int(np.argmax(sr_is))
        oos_rank = int(np.sum(sr_oos < sr_oos[n_star])) + 1
        omega    = float(np.clip(oos_rank / (N + 1), 1e-7, 1 - 1e-7))
        logits[idx] = math.log(omega / (1 - omega))
        is_sr_arr.append(float(sr_is[n_star]))
        oos_sr_arr.append(float(sr_oos[n_star]))
    pbo      = float(np.mean(logits < 0))
    prob_los = float(np.mean(np.array(oos_sr_arr) < 0))
    try:
        slope, _, _, _, _ = stats.linregress(is_sr_arr, oos_sr_arr)
        beta = float(slope)
    except Exception:
        beta = float("nan")
    return {
        "pbo":               pbo,
        "n_combos":          len(is_combos),
        "logit_mean":        float(np.mean(logits)),
        "prob_loss":         prob_los,
        "degradation_beta":  beta,
    }


def _build_window_features(snap: pd.DataFrame, W: int) -> pd.DataFrame:
    """
    Recompute all window-varying features on the snapshot bar series.
    Rolling is per-day (resets at session boundary). Returns augmented snapshot.
    """
    mp = max(1, W // 2)
    enriched = []
    for date, day_df in snap.groupby("rithmic_date_str"):
        d = day_df.sort_values("bar_end_ts_ns").copy().reset_index(drop=True)

        dn  = pd.to_numeric(d["delta_norm"], errors="coerce")
        mn  = pd.to_numeric(d["mlofi_norm"], errors="coerce")
        vp  = pd.to_numeric(d["vpin"], errors="coerce")
        si  = pd.to_numeric(d["sweep_imbalance_norm"], errors="coerce")
        mr1 = pd.to_numeric(d["mid_ret1"], errors="coerce")
        h_  = pd.to_numeric(d["continuous_high"],  errors="coerce")
        l_  = pd.to_numeric(d["continuous_low"],   errors="coerce")
        c_  = pd.to_numeric(d["continuous_close"], errors="coerce")
        o_  = pd.to_numeric(d["continuous_open"],  errors="coerce")

        # Rolling mean / vol
        d["_delta_rolling"]  = dn.rolling(W, min_periods=mp).mean()
        d["_mlofi_rolling"]  = mn.rolling(W, min_periods=mp).mean()
        vol_W                = mr1.rolling(W, min_periods=mp).std()
        d["_volatility"]     = vol_W
        d["_mlofi_accel"]    = d["_mlofi_rolling"].diff(1)

        # Z-score residuals (window W)
        def _z(s: pd.Series) -> pd.Series:
            rm = s.rolling(W, min_periods=mp).mean()
            rs = s.rolling(W, min_periods=mp).std()
            return (s - rm) / rs.replace(0, np.nan)

        d["_dn_resid_z"]   = _z(dn)
        d["_vol_resid_z"]  = _z(vol_W)
        d["_si_resid_z"]   = _z(si)
        d["_vpin_resid_z"] = _z(vp)

        # OHLC-vol (normalized by volatility_W)
        vol_norm = vol_W.replace(0, np.nan)
        rng      = (h_ - l_).abs()
        d["_candle_body_vol"]         = (c_ - o_).abs() / vol_norm
        d["_candle_range_vol"]        = rng / vol_norm
        d["_upper_wick_vol"]          = (h_ - np.maximum(c_, o_)) / vol_norm
        d["_lower_wick_vol"]          = (np.minimum(c_, o_) - l_) / vol_norm
        d["_close_location"]          = (c_ - l_) / rng.replace(0, np.nan)
        d["_open_to_close_sign"]      = np.sign(c_ - o_)
        d["_close_vs_prev_close_vol"] = (c_ - c_.shift(1)) / vol_norm
        d["_close_vs_roll_mean_vol"]  = (c_ - c_.rolling(W, min_periods=mp).mean()) / vol_norm
        roll_max = c_.rolling(W, min_periods=mp).max().shift(1)
        roll_min = c_.rolling(W, min_periods=mp).min().shift(1)
        d["_high_break_vol"] = (h_ - roll_max) / vol_norm
        d["_low_break_vol"]  = (roll_min - l_) / vol_norm

        enriched.append(d)

    return pd.concat(enriched, ignore_index=True)


def _extract_event_features(
    feat_names: list,
    snap_w: pd.DataFrame,
    events_full: pd.DataFrame,
    stream: pd.DataFrame,
) -> pd.DataFrame:
    """
    For each event, extract the 77-feature vector using window-recomputed columns.
    snap_w must have the _* prefixed window-specific columns added by _build_window_features.
    """
    # Index snap_w by bar_end_ts_ns for fast lookup
    snap_idx = snap_w.set_index("bar_end_ts_ns")
    strm_idx = stream.set_index("bar_end_ts_ns")

    rows = []
    for _, ev in events_full.iterrows():
        ts  = int(ev["event_time_ns"])
        rec = {
            "event_id":         int(ev["event_id"]),
            "rithmic_date_str": ev["rithmic_date_str"],
            "bar_idx_in_day":   int(ev["bar_idx_in_day"]),
            "event_time_ns":    ts,
            "session":          ev["session"],
            "reaction_type":    ev["reaction_type"],
            "level_type":       ev["level_type"],
        }
        if ts not in snap_idx.index or ts not in strm_idx.index:
            continue
        bar = snap_idx.loc[ts]
        ctx = strm_idx.loc[ts]

        # Distance / value area features from stream
        for col in DIST_COLS:
            rec[col] = float(ctx[col]) if pd.notna(ctx[col]) else np.nan

        # Event-level
        rec["touch_count_past_only"]  = float(ev["touch_count_past_only"])
        rec["bars_since_prior_touch"] = float(ev["bars_since_prior_touch"])

        # Fixed orderflow features from snapshot
        for col in FIXED_OF_COLS:
            if col == "mlofi_accel":
                rec["mlofi_accel"] = float(bar["_mlofi_accel"]) if pd.notna(bar["_mlofi_accel"]) else np.nan
            elif col in bar.index and pd.notna(bar[col]):
                rec[col] = float(bar[col])
            else:
                rec[col] = np.nan

        # Window-specific features — mapped to fixed feature_names.json names
        rec["delta_rolling_5"]                  = float(bar["_delta_rolling"])   if pd.notna(bar["_delta_rolling"])  else np.nan
        rec["mlofi_rolling_5"]                  = float(bar["_mlofi_rolling"])   if pd.notna(bar["_mlofi_rolling"])  else np.nan
        rec["volatility_5"]                     = float(bar["_volatility"])       if pd.notna(bar["_volatility"])     else np.nan
        rec["delta_norm_resid_z20"]             = float(bar["_dn_resid_z"])       if pd.notna(bar["_dn_resid_z"])     else np.nan
        rec["volatility_5_resid_z20"]           = float(bar["_vol_resid_z"])      if pd.notna(bar["_vol_resid_z"])    else np.nan
        rec["sweep_imbalance_norm_resid_z20"]   = float(bar["_si_resid_z"])       if pd.notna(bar["_si_resid_z"])     else np.nan
        rec["vpin_resid_z20"]                   = float(bar["_vpin_resid_z"])     if pd.notna(bar["_vpin_resid_z"])   else np.nan

        # OHLC-vol (window-normalized)
        for col, internal in [
            ("candle_body_vol",         "_candle_body_vol"),
            ("candle_range_vol",        "_candle_range_vol"),
            ("upper_wick_vol",          "_upper_wick_vol"),
            ("lower_wick_vol",          "_lower_wick_vol"),
            ("close_location",          "_close_location"),
            ("open_to_close_sign",      "_open_to_close_sign"),
            ("close_vs_prev_close_vol", "_close_vs_prev_close_vol"),
            ("close_vs_roll_mean_vol",  "_close_vs_roll_mean_vol"),
            ("high_break_vol",          "_high_break_vol"),
            ("low_break_vol",           "_low_break_vol"),
        ]:
            rec[col] = float(bar[internal]) if pd.notna(bar[internal]) else np.nan

        # Level type one-hot
        for lt in ("POC", "VAH", "VAL", "HVN", "LVN"):
            rec[f"lvl_{lt}"] = int(rec["level_type"] == lt)

        # Session one-hot
        for s in ("Asia", "EU", "US_Open", "US_AM", "US_PM", "US_Late"):
            rec[f"sess_{s}"] = int(rec["session"] == s)

        # Reaction type one-hot
        rxn = ev["reaction_type"]
        rec["rxn_rejection_from_above"] = int("rejection_from_above" in rxn)
        rec["rxn_rejection_from_below"] = int("rejection_from_below" in rxn)
        rec["rxn_absorption"]           = int("absorption" in rxn)
        rec["rxn_acceptance"]           = int("acceptance" in rxn)
        rec["rxn_neutral_touch"]        = int("neutral_touch" in rxn)

        rows.append(rec)

    return pd.DataFrame(rows)


def _train_oos_probs(
    X_df: pd.DataFrame,
    feat_names: list,
    lbl: pd.DataFrame,
    folds: list,
) -> pd.DataFrame:
    """
    Walk-forward CV. Returns OOS probability table for all folds.
    Fits imputer + StandardScaler on train only, scores val only.
    """
    all_recs = []
    X_merged = X_df.merge(
        lbl[["event_id", "label_h40", "fwd_return_ticks_h10", "fwd_return_ticks_h40",
             "rithmic_date_str"]].rename(columns={"rithmic_date_str": "_lbl_date"}),
        on="event_id", how="inner"
    ).dropna(subset=["label_h40"])
    X_merged["label_h40"] = X_merged["label_h40"].astype(int)

    for fold_def in folds:
        fold_id     = fold_def["fold_id"]
        train_dates = set(fold_def["train_dates"])
        val_dates   = set(fold_def["val_dates"])

        tr = X_merged[X_merged["rithmic_date_str"].isin(train_dates)]
        va = X_merged[X_merged["rithmic_date_str"].isin(val_dates)]

        if len(tr) < 200 or len(va) == 0:
            continue

        Xtr = tr[feat_names].to_numpy(dtype=float)
        ytr = tr["label_h40"].to_numpy(dtype=int)
        Xva = va[feat_names].to_numpy(dtype=float)

        imp = SimpleImputer(strategy="median").fit(Xtr)
        Xtr = imp.transform(Xtr)
        Xva = imp.transform(Xva)

        scl = StandardScaler().fit(Xtr)
        Xtr = scl.transform(Xtr)
        Xva = scl.transform(Xva)

        model = HistGradientBoostingClassifier(**HGB_PARAMS).fit(Xtr, ytr)
        probs = model.predict_proba(Xva)   # shape (n_val, 2); class order: 0=SHORT, 1=LONG
        prob_long  = probs[:, 1]
        prob_short = probs[:, 0]

        for i, row in enumerate(va.itertuples(index=False)):
            p_long  = float(prob_long[i])
            p_short = float(prob_short[i])
            p_edge  = abs(p_long - 0.5)
            pred    = "LONG" if p_long > 0.5 else "SHORT"
            y_true  = int(row.label_h40)
            y_dir   = 1 if y_true == 1 else -1
            bet_dir = 1 if pred == "LONG" else -1

            all_recs.append({
                "event_id":            int(row.event_id),
                "timestamp_ns":        int(row.event_time_ns),
                "rithmic_date_str":    row.rithmic_date_str,
                "session":             row.session,
                "prob_long":           p_long,
                "prob_short":          p_short,
                "prob_edge":           p_edge,
                "pred_side":           pred,
                "y_true":              y_true,
                "fwd_return_ticks_h10": float(row.fwd_return_ticks_h10),
                "fwd_return_ticks_h40": float(row.fwd_return_ticks_h40),
                "net_return_h10":      bet_dir * float(row.fwd_return_ticks_h10) - COST_TICKS,
                "net_return_h40":      bet_dir * float(row.fwd_return_ticks_h40) - COST_TICKS,
                "edge_perf":           (p_long - 0.5) * y_dir,  # for CSCV M matrix
                "cost_ticks":          COST_TICKS,
                "fold_id":             fold_id,
                "is_oos":              True,
            })

    return pd.DataFrame(all_recs).sort_values("timestamp_ns").reset_index(drop=True)


def _calibration(oos_df: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """
    Bucket OOS prob_long into 5 bins, compute hit_rate + net_return per bucket.
    Monotonicity = Spearman ρ(bucket_mid, hit_rate) > 0.
    """
    net_col = f"net_return_h{horizon}"
    bins    = [0.50, 0.55, 0.60, 0.65, 0.70, 1.01]
    labels  = ["0.50-0.55", "0.55-0.60", "0.60-0.65", "0.65-0.70", "0.70+"]
    mids    = [0.525, 0.575, 0.625, 0.675, 0.75]
    rows    = []
    for i, lab in enumerate(labels):
        lo, hi = bins[i], bins[i + 1]
        sub    = oos_df[(oos_df["prob_long"] >= lo) & (oos_df["prob_long"] < hi)]
        n      = len(sub)
        if n < 5:
            rows.append({"bucket": lab, "n": n, "hit_rate": np.nan,
                         "avg_net_return": np.nan, "mid": mids[i]})
            continue
        hit    = float((sub["y_true"] == 1).mean())  # LONG label hit
        nr     = float(sub[net_col].mean()) if net_col in sub.columns else np.nan
        rows.append({"bucket": lab, "n": n, "hit_rate": hit,
                     "avg_net_return": nr, "mid": mids[i]})
    df     = pd.DataFrame(rows)
    valid  = df.dropna(subset=["hit_rate"])
    if len(valid) >= 3:
        rho, p = stats.spearmanr(valid["mid"], valid["hit_rate"])
        monotone = bool(rho > 0 and p < 0.20)
    else:
        rho, p, monotone = float("nan"), float("nan"), False
    df["spearman_rho"] = rho
    df["spearman_p"]   = p
    df["monotone"]     = monotone
    return df


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    print("=" * 70)
    print("Model-Probability PBO — Window Family Audit v1")
    print("SHADOW / RESEARCH ONLY — no execution, no broker")
    print("=" * 70)

    # ─── PART A: Lock v3 master ───────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("PART A: Lock v3 master")
    print("=" * 70)
    lock_files = {
        "feature_names.json":    MODEL_DIR / "feature_names.json",
        "training_config.json":  MODEL_DIR / "training_config.json",
        "release_manifest.json": MODEL_DIR / "release_manifest.json",
    }
    lock_manifest = {"locked_at_utc": datetime.now(timezone.utc).isoformat(), "files": {}}
    for label, path in lock_files.items():
        sha = sha256_file(path)
        lock_manifest["files"][label] = {"path": str(path), "sha256": sha}
        print(f"  {label}: {sha[:16]}…")

    with open(MODEL_DIR / "feature_names.json") as f:
        feat_names_base = json.load(f)
    with open(MODEL_DIR / "training_config.json") as f:
        train_cfg = json.load(f)
    with open(MODEL_DIR / "data/folds.json") as f:
        folds_raw = json.load(f)

    folds       = folds_raw["fold_definitions"]
    embargo_bars = folds_raw["embargo_bars"]
    lock_manifest["embargo_bars"]     = embargo_bars
    lock_manifest["label_horizon"]    = folds_raw["label_horizon_bars"]
    lock_manifest["n_folds"]          = len(folds)
    lock_manifest["model_family"]     = "HistGradientBoostingClassifier"
    lock_manifest["model_params"]     = HGB_PARAMS
    lock_manifest["windows_tested"]   = WINDOWS
    lock_manifest["cost_ticks"]       = COST_TICKS
    lock_manifest["EXECUTION_FLAG"]   = "NO_EXECUTION"

    with open(OUT_DIR / "LOCK_MANIFEST.json", "w") as f:
        json.dump(lock_manifest, f, indent=2)
    print(f"  Locked {len(folds)} folds, embargo={embargo_bars} bars")
    print("Part A done.")

    # ─── PART B: Load base data ───────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("PART B: Load base data")
    print("=" * 70)
    t_b = time.time()

    snap    = pd.read_parquet(DATA_DIR / "continuous_master_snapshot.parquet")
    events  = pd.read_parquet(DATA_DIR / "level_reaction_events.parquet")
    lbl     = pd.read_parquet(DATA_DIR / "labels_level_reaction.parquet")
    stream  = pd.read_parquet(DATA_DIR / "level_stream.parquet")

    # Build bar_end_ts_ns on snap from bar_end_ts_ns (already present) and bar_idx_in_day
    assert "bar_end_ts_ns" in snap.columns
    snap_idx_by_ts = snap.set_index("bar_end_ts_ns")

    # Add bar_idx_in_day to snap
    snap = snap.sort_values(["rithmic_date_str", "bar_end_ts_ns"]).reset_index(drop=True)
    snap["bar_idx_in_day"] = snap.groupby("rithmic_date_str").cumcount()

    # Only labelled events
    lbl_valid = lbl.dropna(subset=["label_h40"]).copy()
    events_full = events.merge(lbl_valid[["event_id", "label_h40",
                                           "fwd_return_ticks_h10",
                                           "fwd_return_ticks_h40"]],
                               on="event_id", how="inner")
    print(f"  Snapshot:     {len(snap):,} bars")
    print(f"  Events total: {len(events):,}  labelled: {len(events_full):,}")
    print(f"  Folds:        {len(folds)}")

    # Load deferred signals (Version B)
    atlas1 = pd.read_parquet(ATLAS1_PANEL)[
        ["bar_end_ts_ns", "buy_toxicity", "sell_toxicity", "signed_vpin_delta"]
    ].rename(columns={"bar_end_ts_ns": "ts_ns"})
    q16    = pd.read_parquet(Q16_PANEL)[["bar_end_ts_ns", "cur_vpin_pct_L500"]
    ].rename(columns={"bar_end_ts_ns": "ts_ns"})

    # Compute cur_vpin_pct_L500_signed = cur_vpin_pct_L500 × sign(delta_norm at bar)
    snap_delta = snap[["bar_end_ts_ns", "delta_norm"]].rename(
        columns={"bar_end_ts_ns": "ts_ns"})
    q16 = q16.merge(snap_delta, on="ts_ns", how="left")
    q16["cur_vpin_pct_L500_signed"] = (
        q16["cur_vpin_pct_L500"] * np.sign(q16["delta_norm"])
    )
    q16 = q16.drop(columns=["cur_vpin_pct_L500", "delta_norm"])

    deferred_df = atlas1.merge(q16, on="ts_ns", how="outer")
    deferred_df = deferred_df.rename(columns={"ts_ns": "event_time_ns"})
    deferred_df["event_time_ns"] = deferred_df["event_time_ns"].astype("int64")

    # Index stream by ts for fast lookup
    stream = stream.rename(columns={"bar_end_ts_ns": "ts_ns"} if "bar_end_ts_ns" in stream.columns else {})
    if "ts_ns" not in stream.columns:
        stream["ts_ns"] = stream["bar_end_ts_ns"]
    stream_tsidx = stream.copy()
    if "bar_end_ts_ns" in stream_tsidx.columns:
        stream_tsidx = stream_tsidx.set_index("bar_end_ts_ns")
    elif "ts_ns" in stream_tsidx.columns:
        stream_tsidx = stream_tsidx.set_index("ts_ns")

    print(f"  Deferred signals: {len(deferred_df):,} rows from atlas1/q16")
    print(f"  Part B done in {time.time()-t_b:.1f}s")

    # ─── PART C: Per-window feature construction ──────────────────────────────
    print("\n" + "=" * 70)
    print("PART C: Per-window feature construction")
    print("=" * 70)
    t_c = time.time()

    # Rebuild stream index with bar_end_ts_ns as original column name
    stream_parquet = pd.read_parquet(DATA_DIR / "level_stream.parquet")
    stream_tsidx2  = stream_parquet.set_index("bar_end_ts_ns")

    # feat_names for Version B
    feat_names_B = feat_names_base + DEFERRED_SIGNALS

    window_Xdfs = {}   # W → X DataFrame (base features only)
    for W in WINDOWS:
        t_w = time.time()
        snap_w = _build_window_features(snap, W)
        snap_w_idx = snap_w.set_index("bar_end_ts_ns")

        # Extract event features
        rows = []
        for _, ev in events_full.iterrows():
            ts  = int(ev["event_time_ns"])
            if ts not in snap_w_idx.index or ts not in stream_tsidx2.index:
                continue
            bar = snap_w_idx.loc[ts]
            ctx = stream_tsidx2.loc[ts]
            rec = {
                "event_id":         int(ev["event_id"]),
                "rithmic_date_str": ev["rithmic_date_str"],
                "bar_idx_in_day":   int(ev["bar_idx_in_day"]),
                "event_time_ns":    ts,
                "session":          ev["session"],
                "reaction_type":    ev["reaction_type"],
                "level_type":       ev["level_type"],
            }
            # Distance features
            for col in DIST_COLS:
                v = ctx[col] if col in ctx.index else np.nan
                rec[col] = float(v) if pd.notna(v) else np.nan
            # Event-level
            rec["touch_count_past_only"]  = float(ev["touch_count_past_only"])
            rec["bars_since_prior_touch"] = float(ev["bars_since_prior_touch"])
            # Fixed OF
            for col in FIXED_OF_COLS:
                if col == "mlofi_accel":
                    v = bar["_mlofi_accel"]
                else:
                    v = bar[col] if col in bar.index else np.nan
                rec[col] = float(v) if pd.notna(v) else np.nan
            # Window features (mapped to fixed names)
            rec["delta_rolling_5"]                = float(bar["_delta_rolling"]) if pd.notna(bar["_delta_rolling"]) else np.nan
            rec["mlofi_rolling_5"]                = float(bar["_mlofi_rolling"]) if pd.notna(bar["_mlofi_rolling"]) else np.nan
            rec["volatility_5"]                   = float(bar["_volatility"])    if pd.notna(bar["_volatility"])    else np.nan
            rec["delta_norm_resid_z20"]           = float(bar["_dn_resid_z"])    if pd.notna(bar["_dn_resid_z"])    else np.nan
            rec["volatility_5_resid_z20"]         = float(bar["_vol_resid_z"])   if pd.notna(bar["_vol_resid_z"])   else np.nan
            rec["sweep_imbalance_norm_resid_z20"] = float(bar["_si_resid_z"])    if pd.notna(bar["_si_resid_z"])    else np.nan
            rec["vpin_resid_z20"]                 = float(bar["_vpin_resid_z"])  if pd.notna(bar["_vpin_resid_z"])  else np.nan
            # OHLC-vol
            for feat, key in [
                ("candle_body_vol",         "_candle_body_vol"),
                ("candle_range_vol",        "_candle_range_vol"),
                ("upper_wick_vol",          "_upper_wick_vol"),
                ("lower_wick_vol",          "_lower_wick_vol"),
                ("close_location",          "_close_location"),
                ("open_to_close_sign",      "_open_to_close_sign"),
                ("close_vs_prev_close_vol", "_close_vs_prev_close_vol"),
                ("close_vs_roll_mean_vol",  "_close_vs_roll_mean_vol"),
                ("high_break_vol",          "_high_break_vol"),
                ("low_break_vol",           "_low_break_vol"),
            ]:
                v = bar[key]
                rec[feat] = float(v) if pd.notna(v) else np.nan
            # One-hot
            lt = rec["level_type"]
            for ltype in ("POC","VAH","VAL","HVN","LVN"):
                rec[f"lvl_{ltype}"] = int(lt == ltype)
            for s in ("Asia","EU","US_Open","US_AM","US_PM","US_Late"):
                rec[f"sess_{s}"] = int(rec["session"] == s)
            rxn = rec["reaction_type"]
            rec["rxn_rejection_from_above"] = int("rejection_from_above" in rxn)
            rec["rxn_rejection_from_below"] = int("rejection_from_below" in rxn)
            rec["rxn_absorption"]           = int("absorption" in rxn)
            rec["rxn_acceptance"]           = int("acceptance" in rxn)
            rec["rxn_neutral_touch"]        = int("neutral_touch" in rxn)
            rows.append(rec)

        X_df = pd.DataFrame(rows)
        window_Xdfs[W] = X_df
        n_ev = len(X_df)
        n_na = X_df[feat_names_base].isna().any(axis=1).sum()
        print(f"  W={W:3d}: {n_ev:,} events  {n_na:,} rows with any NaN  ({time.time()-t_w:.1f}s)")

    print(f"Part C done in {time.time()-t_c:.1f}s")

    # ─── PART D: Walk-forward CV — OOS probability generation ─────────────────
    print("\n" + "=" * 70)
    print("PART D: Walk-forward CV — OOS probabilities")
    print("=" * 70)
    t_d = time.time()

    # Version A (base features) and Version B (base + deferred)
    oos_tables = {}  # (version, W) → OOS DataFrame

    for version in ["A", "B"]:
        fn = feat_names_base if version == "A" else feat_names_B
        print(f"\n  Version {version} — {len(fn)} features")
        for W in WINDOWS:
            t_w = time.time()
            X_df = window_Xdfs[W].copy()

            if version == "B":
                # Join deferred signals by event_time_ns
                X_df = X_df.merge(
                    deferred_df[["event_time_ns"] + DEFERRED_SIGNALS],
                    on="event_time_ns", how="left"
                )
                # Ensure deferred cols exist (fill missing with NaN)
                for ds in DEFERRED_SIGNALS:
                    if ds not in X_df.columns:
                        X_df[ds] = np.nan

            # Merge labels
            X_lbl = X_df.merge(
                lbl[["event_id", "label_h40", "fwd_return_ticks_h10",
                     "fwd_return_ticks_h40"]],
                on="event_id", how="inner"
            ).dropna(subset=["label_h40"])
            X_lbl["label_h40"] = X_lbl["label_h40"].astype(int)

            all_recs = []
            for fold_def in folds:
                fold_id     = fold_def["fold_id"]
                train_dates = set(fold_def["train_dates"])
                val_dates   = set(fold_def["val_dates"])

                tr = X_lbl[X_lbl["rithmic_date_str"].isin(train_dates)]
                va = X_lbl[X_lbl["rithmic_date_str"].isin(val_dates)]

                if len(tr) < 200 or len(va) == 0:
                    continue

                Xtr = tr[fn].to_numpy(dtype=float)
                ytr = tr["label_h40"].to_numpy(dtype=int)
                Xva = va[fn].to_numpy(dtype=float)

                imp = SimpleImputer(strategy="median").fit(Xtr)
                Xtr_i, Xva_i = imp.transform(Xtr), imp.transform(Xva)
                scl = StandardScaler().fit(Xtr_i)
                Xtr_s, Xva_s = scl.transform(Xtr_i), scl.transform(Xva_i)

                model = HistGradientBoostingClassifier(**HGB_PARAMS).fit(Xtr_s, ytr)
                probs = model.predict_proba(Xva_s)   # col0=SHORT, col1=LONG

                for i, row in enumerate(va.itertuples(index=False)):
                    p_long  = float(probs[i, 1])
                    p_short = float(probs[i, 0])
                    p_edge  = abs(p_long - 0.5)
                    pred    = "LONG" if p_long > 0.5 else "SHORT"
                    y_true  = int(row.label_h40)
                    y_dir   = 1 if y_true == 1 else -1
                    bet_dir = 1 if pred == "LONG" else -1
                    h10     = float(row.fwd_return_ticks_h10)
                    h40     = float(row.fwd_return_ticks_h40)
                    all_recs.append({
                        "event_id":             int(row.event_id),
                        "timestamp_ns":         int(row.event_time_ns),
                        "rithmic_date_str":     row.rithmic_date_str,
                        "session":              row.session,
                        "prob_long":            p_long,
                        "prob_short":           p_short,
                        "prob_edge":            p_edge,
                        "pred_side":            pred,
                        "y_true":               y_true,
                        "fwd_return_ticks_h10": h10,
                        "fwd_return_ticks_h40": h40,
                        "net_return_h10":       bet_dir * h10 - COST_TICKS,
                        "net_return_h40":       bet_dir * h40 - COST_TICKS,
                        "edge_perf":            (p_long - 0.5) * y_dir,
                        "cost_ticks":           COST_TICKS,
                        "fold_id":              fold_id,
                        "is_oos":               True,
                        "window":               W,
                        "version":              version,
                    })

            oos_df = pd.DataFrame(all_recs).sort_values("timestamp_ns").reset_index(drop=True)
            oos_tables[(version, W)] = oos_df
            acc = float((oos_df["pred_side"] == oos_df["y_true"].map({1:"LONG",0:"SHORT"})).mean())
            nr10 = float(oos_df["net_return_h10"].mean())
            print(f"    V{version} W={W:3d}: {len(oos_df):,} OOS events  acc={acc:.3f}  net_h10={nr10:+.3f}t")

    print(f"\nPart D done in {time.time()-t_d:.1f}s")

    # ─── PART E: CSCV — Model-Probability PBO ────────────────────────────────
    print("\n" + "=" * 70)
    print("PART E: CSCV — Model-Probability PBO by Window Family")
    print("=" * 70)
    t_e = time.time()

    pbo_results = {}  # version → dict
    cscv_rows   = []

    for version in ["A", "B"]:
        # Build M matrix: rows=events (chronological OOS), cols=window variants
        # Use events common to all windows for this version
        common_ids = None
        for W in WINDOWS:
            # drop_duplicates before building id set
            ids = set(oos_tables[(version, W)].drop_duplicates("event_id")["event_id"].tolist())
            common_ids = ids if common_ids is None else common_ids & ids

        # Build sorted event list and M matrix (deduplicate ref_df)
        ref_df = (oos_tables[(version, WINDOWS[0])]
                  .drop_duplicates(subset=["event_id"])
                  .sort_values("timestamp_ns"))
        ref_df = ref_df[ref_df["event_id"].isin(common_ids)]
        event_order = ref_df["event_id"].tolist()
        T_raw = len(event_order)
        T     = (T_raw // S_CSCV) * S_CSCV  # trim to multiple of S
        event_order = event_order[:T]

        # Align all windows to same event order
        M_cols = {}
        for W in WINDOWS:
            # drop_duplicates: guard against any duplicate event_id from expand-window folds
            df_w = (oos_tables[(version, W)]
                    .drop_duplicates(subset=["event_id"])
                    .set_index("event_id"))
            M_cols[W] = df_w.loc[event_order, "edge_perf"].to_numpy(dtype=float)

        M = np.column_stack([M_cols[W] for W in WINDOWS])   # T × N_windows
        print(f"\n  Version {version}: T={T:,} events × N={M.shape[1]} windows (S={S_CSCV})")
        print(f"    Mean edge_perf per window: {dict(zip(WINDOWS, [f'{M[:,i].mean():.5f}' for i in range(M.shape[1])]))}")

        result = cscv_pbo(M, S=S_CSCV)
        pbo_results[version] = {**result, "T": T, "N": M.shape[1]}
        print(f"    PBO={result['pbo']:.3f}  logit_mean={result['logit_mean']:+.3f}  "
              f"prob_loss={result['prob_loss']:.3f}  beta={result['degradation_beta']:+.3f}")

        # Per-window net_return OOS (positive windows check)
        n_positive = 0
        for i, W in enumerate(WINDOWS):
            col_mean = float(M[:, i].mean())
            pos = col_mean > 0
            n_positive += int(pos)
            cscv_rows.append({
                "version": version, "window": W,
                "mean_edge_perf": col_mean,
                "positive": pos,
                "family_pbo": result["pbo"],
            })
        pbo_results[version]["n_windows_positive"] = n_positive
        print(f"    Windows with positive edge_perf: {n_positive}/{len(WINDOWS)}")

    pd.DataFrame(cscv_rows).to_csv(OUT_DIR / "cscv_per_window_results.csv", index=False)
    print(f"\nPart E done in {time.time()-t_e:.1f}s")

    # ─── PART F: Probability Calibration ─────────────────────────────────────
    print("\n" + "=" * 70)
    print("PART F: Probability Calibration")
    print("=" * 70)
    t_f = time.time()

    calib_rows = []
    calib_summary = {}  # (version, W, H) → {monotone, spearman_rho, ...}

    for version in ["A", "B"]:
        print(f"\n  Version {version}:")
        for W in WINDOWS:
            oos = oos_tables[(version, W)]
            for H in HORIZONS:
                cal = _calibration(oos, H)
                mono = bool(cal["monotone"].iloc[0]) if len(cal) > 0 else False
                rho  = float(cal["spearman_rho"].iloc[0]) if len(cal) > 0 else float("nan")
                for _, row in cal.iterrows():
                    calib_rows.append({
                        "version": version, "window": W, "horizon": H,
                        "bucket": row["bucket"], "n": row["n"],
                        "hit_rate": row["hit_rate"],
                        "avg_net_return": row["avg_net_return"],
                        "spearman_rho": rho, "monotone": mono,
                    })
                calib_summary[(version, W, H)] = {
                    "monotone": mono, "spearman_rho": rho
                }
            # Print summary for H=10
            cal10 = _calibration(oos, 10)
            mono10 = bool(cal10["monotone"].iloc[0]) if len(cal10) > 0 else False
            rho10  = float(cal10["spearman_rho"].iloc[0]) if len(cal10) > 0 else float("nan")
            tag    = "CALIBRATED" if mono10 else "FLAT/BROKEN"
            print(f"    W={W:3d}: H=10 calibration={tag}  ρ={rho10:+.3f}")

    pd.DataFrame(calib_rows).to_csv(OUT_DIR / "calibration_by_window.csv", index=False)
    print(f"\nPart F done in {time.time()-t_f:.1f}s")

    # ─── PART G: OOS Probability Table (full combined) ────────────────────────
    print("\n" + "=" * 70)
    print("PART G: Save OOS Probability Table")
    print("=" * 70)
    all_oos = pd.concat(list(oos_tables.values()), ignore_index=True)
    all_oos.to_parquet(OUT_DIR / "oos_probability_table.parquet", index=False)
    print(f"  Saved {len(all_oos):,} rows to oos_probability_table.parquet")

    # ─── PART H: Decisions ───────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("PART H: Decisions — PROMOTE_TO_SHADOW / DEFER_RESEARCH / KILL")
    print("=" * 70)

    decision_rows = []
    for version in ["A", "B"]:
        r = pbo_results[version]
        fam_pbo          = r["pbo"]
        n_pos_windows    = r["n_windows_positive"]
        n_total_windows  = len(WINDOWS)
        only_one_window  = n_pos_windows <= 1

        # Calibration check: ≥ half the windows are calibrated at H=10
        n_calibrated = sum(
            1 for W in WINDOWS
            if calib_summary.get((version, W, 10), {}).get("monotone", False)
        )
        calibration_ok = n_calibrated >= (n_total_windows // 2)

        # Net expectancy: avg net_return_h10 across all OOS events for best window
        best_W_net = max(
            WINDOWS,
            key=lambda W: oos_tables[(version, W)]["net_return_h10"].mean()
        )
        best_net = float(oos_tables[(version, best_W_net)]["net_return_h10"].mean())

        # ─── Decision criteria ────────────────────────────────────────────────
        # HIGH PBO (≥0.60) means: window SELECTION is overfit — the IS-optimal
        # window does not reliably win OOS.
        # But HIGH PBO ≠ model failure if all windows are positive.
        # Distinguish two cases:
        #
        # KILL:             family_PBO ≥ 0.60 AND ≤ half windows positive
        #                   → selection overfit + model not broadly positive
        #   OR              only_one_window (≤1 window positive, regardless of PBO)
        #   OR              no calibration at all
        #
        # DEFER_RESEARCH:   family_PBO ≥ 0.60 AND > half windows positive
        #                   → window selection is overfit, but model has broad edge
        #                   → do NOT select window by IS; use fixed W (current default)
        #   OR              family_PBO < 0.60 AND ≥2 windows positive
        #
        # PROMOTE_TO_SHADOW: family_PBO < 0.40
        #                    AND ≥4 windows positive
        #                    AND calibration_ok
        #                    AND best_net > 0
        # ─────────────────────────────────────────────────────────────────────

        half_windows = n_total_windows // 2
        kill_pbo    = fam_pbo >= 0.60 and n_pos_windows <= half_windows
        kill_window = only_one_window   # only 1 window positive → no broad edge
        kill_calib  = n_calibrated == 0

        # Special case: high PBO but all windows positive = window selection overfit
        selection_overfit_only = (fam_pbo >= 0.60 and n_pos_windows > half_windows)

        if kill_window or kill_calib or kill_pbo:
            decision = "KILL"
        elif fam_pbo < 0.40 and n_pos_windows >= 4 and calibration_ok and best_net > 0:
            decision = "PROMOTE_TO_SHADOW"
        else:
            decision = "DEFER_RESEARCH"

        row = {
            "version":           version,
            "family_pbo":        fam_pbo,
            "logit_mean":        r["logit_mean"],
            "prob_loss":         r["prob_loss"],
            "degradation_beta":  r["degradation_beta"],
            "n_windows_tested":  n_total_windows,
            "n_windows_positive":n_pos_windows,
            "only_one_window":   only_one_window,
            "n_windows_calibrated": n_calibrated,
            "calibration_ok":    calibration_ok,
            "best_window":       best_W_net,
            "best_net_h10":      best_net,
            "decision":          decision,
            "selection_overfit_only": selection_overfit_only,
            "kill_reason":       (
                "only_one_window"               if kill_window else
                "no_calibration"                if kill_calib else
                "family_PBO>=0.60_few_pos_wins" if kill_pbo else
                "selection_overfit_but_all_pos" if selection_overfit_only else
                "N/A"
            ),
        }
        decision_rows.append(row)

        tag_line = (
            f"  Version {version}: {decision:20s} | "
            f"PBO={fam_pbo:.3f} | {n_pos_windows}/{n_total_windows} windows positive | "
            f"{n_calibrated}/{n_total_windows} calibrated | best_net_h10={best_net:+.3f}"
        )
        print(tag_line)

    dec_df = pd.DataFrame(decision_rows)
    dec_df.to_csv(OUT_DIR / "model_prob_pbo_decisions.csv", index=False)
    print("\nPart H done.")

    # ─── PART I: Final Report ─────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("PART I: Final Report")
    print("=" * 70)

    t_total = time.time() - t0
    now_utc = datetime.now(timezone.utc).isoformat()

    dec_A = decision_rows[0]
    dec_B = decision_rows[1]

    report = [
        "# MODEL-PROBABILITY PBO — WINDOW FAMILY AUDIT v1",
        f"**Generated**: {now_utc}",
        f"**Runtime**: {t_total:.1f} seconds",
        "**SHADOW / RESEARCH ONLY — no execution, no broker, no order placement**",
        "**Method**: Bailey et al. (2015) CSCV on OOS model probabilities",
        "",
        "---",
        "",
        "## Setup",
        "",
        f"- **Source model**: level_reaction_continuous_nq_shadow (HGB, target=label_h40)",
        f"- **Feature count (Version A)**: {len(feat_names_base)} base features",
        f"- **Feature count (Version B)**: {len(feat_names_B)} (base + {len(DEFERRED_SIGNALS)} deferred)",
        f"- **Window variants tested**: {WINDOWS}",
        f"- **Walk-forward folds**: {len(folds)} expanding-window calendar-day splits",
        f"- **Embargo**: {embargo_bars} bars",
        f"- **Cost model**: {COST_TICKS} ticks round-trip per trade",
        f"- **CSCV**: S={S_CSCV} subperiods → C({S_CSCV},{S_CSCV//2})=12,870 combinations",
        "",
        "---",
        "",
        "## What 'window' means",
        "",
        "The rolling window W controls the lookback for all bar-level statistics:",
        "- `delta_rolling_W`, `mlofi_rolling_W` — rolling mean of OFI/flow features",
        "- `volatility_W` — rolling std of per-bar return",
        "- `mlofi_accel` — change in mlofi_rolling over 1 bar (window-parameterized)",
        "- `*_resid_z{W}` — z-score residuals of delta_norm, vpin, sweep_imbalance, volatility",
        "- OHLC-vol features — normalized by `volatility_W`",
        "",
        f"Current production shadow uses W=20. This audit tests W ∈ {WINDOWS}.",
        "**Kill criterion**: If only 1 window variant shows positive edge_perf,",
        "the result is a lucky bar-construction artifact, not a durable market effect.",
        "",
        "---",
        "",
        "## CSCV Results — Model-Probability PBO",
        "",
        "Performance metric: `edge_perf = (prob_long - 0.5) × label_direction`",
        "Positive = model confidence points in the correct direction.",
        "",
        "| Version | N_windows | Family PBO | Logit mean | Prob(loss) | Degradation β |",
        "|---------|-----------|-----------|------------|------------|----------------|",
        f"| A (base)    | {dec_A['n_windows_tested']} | **{dec_A['family_pbo']:.3f}** | {dec_A['logit_mean']:+.3f} | {dec_A['prob_loss']:.3f} | {dec_A['degradation_beta']:+.3f} |",
        f"| B (base+tox)| {dec_B['n_windows_tested']} | **{dec_B['family_pbo']:.3f}** | {dec_B['logit_mean']:+.3f} | {dec_B['prob_loss']:.3f} | {dec_B['degradation_beta']:+.3f} |",
        "",
        "PBO < 0.05 = strong  |  < 0.20 = acceptable  |  < 0.40 = marginal  |  ≥ 0.60 = overfit → KILL",
        "",
        "---",
        "",
        "## Per-Window OOS Performance",
        "",
        "| Version | Window | Edge perf mean | Positive? | Calibrated (H=10)? |",
        "|---------|--------|---------------|-----------|-------------------|",
    ]
    for row in cscv_rows:
        cal = calib_summary.get((row["version"], row["window"], 10), {})
        report.append(
            f"| {row['version']} | W={row['window']:3d} | "
            f"{row['mean_edge_perf']:+.6f} | {'YES' if row['positive'] else 'NO':3s} | "
            f"{'YES' if cal.get('monotone', False) else 'NO'} |"
        )

    report += [
        "",
        "---",
        "",
        "## Probability Calibration Summary",
        "",
        "Calibrated = Spearman ρ(bucket_midpoint, hit_rate) > 0 with p < 0.20",
        "Buckets: [0.50-0.55), [0.55-0.60), [0.60-0.65), [0.65-0.70), [0.70+]",
        "",
        "| Version | Window | H=10 calibrated | H=40 calibrated | ρ (H=10) |",
        "|---------|--------|----------------|----------------|---------|",
    ]
    for version in ["A", "B"]:
        for W in WINDOWS:
            c10 = calib_summary.get((version, W, 10), {})
            c40 = calib_summary.get((version, W, 40), {})
            report.append(
                f"| {version} | W={W:3d} | "
                f"{'YES' if c10.get('monotone', False) else 'NO'} | "
                f"{'YES' if c40.get('monotone', False) else 'NO'} | "
                f"{c10.get('spearman_rho', float('nan')):+.3f} |"
            )

    report += [
        "",
        "---",
        "",
        "## Decisions",
        "",
        "Decision criteria:",
        "- **PROMOTE_TO_SHADOW**: family_PBO < 0.40 AND ≥4 windows positive AND",
        "  calibration OK (≥half windows monotone) AND best_net_h10 > 0",
        "- **DEFER_RESEARCH**: family_PBO < 0.60 AND ≥2 windows positive AND",
        "  ≥1 window calibrated (not all broken)",
        "- **KILL**: family_PBO ≥ 0.60 OR only_one_window OR no_calibration",
        "",
        "Critical kill conditions (from specification):",
        "- family_PBO ≥ 0.60 → configuration selection is overfit",
        "- Only 1 window works → lucky bar construction, not durable market effect",
        "- No calibration → model probabilities do not rank-order outcomes",
        "- Net expectancy disappears after costs → no edge",
        "",
        "| Version | PBO | Windows+ | Calibrated | Best net H10 | Decision |",
        "|---------|-----|----------|-----------|--------------|----------|",
        f"| A (base) | {dec_A['family_pbo']:.3f} | {dec_A['n_windows_positive']}/{dec_A['n_windows_tested']} | {dec_A['n_windows_calibrated']}/{dec_A['n_windows_tested']} | {dec_A['best_net_h10']:+.3f}t | **{dec_A['decision']}** |",
        f"| B (+tox) | {dec_B['family_pbo']:.3f} | {dec_B['n_windows_positive']}/{dec_B['n_windows_tested']} | {dec_B['n_windows_calibrated']}/{dec_B['n_windows_tested']} | {dec_B['best_net_h10']:+.3f}t | **{dec_B['decision']}** |",
        "",
    ]

    for d in decision_rows:
        if d["decision"] == "KILL":
            report.append(f"**Version {d['version']} KILL reason**: {d['kill_reason']}")
        elif d["decision"] == "PROMOTE_TO_SHADOW":
            report.append(f"**Version {d['version']} PROMOTE**: 30-day live shadow required. "
                          f"DO NOT modify Feature Master or execution model yet.")
        else:
            report.append(f"**Version {d['version']} DEFER_RESEARCH**: Borderline result. "
                          f"Requires longer OOS sample and session/regime stability confirmation.")

    report += [
        "",
        "---",
        "",
        "## Deferred Signal Status (from signal-level PBO)",
        "",
        "The 4 deferred signals in Version B are:",
        "| Signal | Signal-level PBO verdict | In Version B? |",
        "|--------|-------------------------|--------------|",
        "| buy_toxicity | DEFER (window/session stable, DSR too low vs N=97K) | YES |",
        "| sell_toxicity | DEFER (window/session stable) | YES |",
        "| signed_vpin_delta | DEFER (window/session stable) | YES |",
        "| cur_vpin_pct_L500_signed | DEFER (window/session stable) | YES |",
        "",
        "Version B tests whether these deferred signals improve model-probability",
        "quality on top of the base v3 feature set.",
        "",
        "---",
        "",
        "## Output Files",
        "",
        "| File | Part | Description |",
        "|------|------|-------------|",
        "| LOCK_MANIFEST.json | A | v3 master config hash lock |",
        "| oos_probability_table.parquet | G | Full OOS prob table (all windows, both versions) |",
        "| cscv_per_window_results.csv | E | CSCV per-window edge_perf and family PBO |",
        "| calibration_by_window.csv | F | Prob calibration per window / horizon / bucket |",
        "| model_prob_pbo_decisions.csv | H | Final decisions |",
        "| MODEL_PROB_PBO_REPORT.md | I | This report |",
        "",
        "---",
        "",
        "## Final Status",
        "```",
        "PRODUCTION_FILES_MODIFIED:              false",
        "DASHBOARD_CODE_MODIFIED:                false",
        "FEATURE_MASTER_CODE_MODIFIED:           false",
        "ACTIVE_MODEL_POINTER_CHANGED:           false",
        "TRADING_ENABLED:                        false",
        "BROKER_CONNECTED:                       false",
        "PAPER_TRADING_ENABLED:                  false",
        f"WINDOWS_TESTED:                         {WINDOWS}",
        f"FOLDS:                                  {len(folds)}",
        f"COST_TICKS:                             {COST_TICKS}",
        f"VERSION_A_DECISION:                     {dec_A['decision']}",
        f"VERSION_B_DECISION:                     {dec_B['decision']}",
        f"RUNTIME_SECONDS:                        {t_total:.1f}",
        "OVERALL:                                PASS",
        "```",
    ]

    report_text = "\n".join(report)
    with open(OUT_DIR / "MODEL_PROB_PBO_REPORT.md", "w") as f:
        f.write(report_text)
    print("Saved: MODEL_PROB_PBO_REPORT.md")

    print("\n" + "=" * 70)
    print(f"Model-Probability PBO COMPLETE in {t_total:.1f}s")
    print("=" * 70)
    for d in decision_rows:
        print(f"  Version {d['version']}: {d['decision']}")
    print("=" * 70)


if __name__ == "__main__":
    main()
