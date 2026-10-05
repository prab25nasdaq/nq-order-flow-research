#!/usr/bin/env python3
"""Importable core of the level-reaction shadow inference.

Mirrors level_reaction_inference.py but exposes pure functions so the
Tkinter dashboard tab can call them directly without subprocess overhead.

ABSOLUTE RULES enforced here:
  * Only read-only access to release artifacts and master.
  * No fit() or fit_transform() — strictly transform()/predict_proba().
  * No file writes; the caller decides where (or whether) to persist.
"""
from __future__ import annotations
import json, pickle, sys, warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


# ── Paths (defaults — caller may override) ─────────────────────────────────── #
DEFAULT_MASTER  = Path("/home/prabh/OFI_Live_Features/master.ndjsonl")
DEFAULT_RELEASE = Path("/home/prabh/OFI_Production/model_registry/"
                       "level_reaction_rithmic_only_shadow/"
                       "level_reaction_rithmic_only_shadow_20260613T024116Z")


# ── Trained reaction metadata (informational only — do NOT block scoring) ─── #
TRAINED_REACTION_METADATA: Dict[str, Dict[str, Any]] = {
    "HVN_rejection_from_below":  {"n_training": 25080, "training_gate_status": "PRIMARY_USE"},
    "HVN_rejection_from_above":  {"n_training": 24783, "training_gate_status": "PRIMARY_USE"},
    "LVN_rejection_from_below":  {"n_training":  6726, "training_gate_status": "BLOCKED_NEGATIVE"},
    "LVN_rejection_from_above":  {"n_training":  6471, "training_gate_status": "EXPLORATORY_SMALL_N"},
    "POC_rejection_from_below":  {"n_training":   312, "training_gate_status": "SECONDARY_WATCH"},
    "POC_rejection_from_above":  {"n_training":   295, "training_gate_status": "BLOCKED_UNSTABLE"},
    "VAH_rejection_from_below":  {"n_training":   117, "training_gate_status": "BLOCKED_NEGATIVE"},
    "HVN_absorption":            {"n_training":    94, "training_gate_status": "EXPLORATORY_SMALL_N"},
    "VAH_rejection_from_above":  {"n_training":    87, "training_gate_status": "BLOCKED_NEGATIVE"},
    "VAL_rejection_from_above":  {"n_training":    83, "training_gate_status": "EXPLORATORY_SMALL_N"},
    "VAL_rejection_from_below":  {"n_training":    72, "training_gate_status": "BLOCKED_NEGATIVE"},
    "HVN_neutral_touch":         {"n_training":    10, "training_gate_status": "SMALL_N"},
    "LVN_absorption":            {"n_training":     8, "training_gate_status": "SMALL_N"},
    "POC_absorption":            {"n_training":     3, "training_gate_status": "SMALL_N"},
    "LVN_neutral_touch":         {"n_training":     2, "training_gate_status": "SMALL_N"},
    "VAH_absorption":            {"n_training":     1, "training_gate_status": "SMALL_N"},
}
TRAINED_REACTION_TYPES = set(TRAINED_REACTION_METADATA.keys())


# ──────────────────────────────────────────────────────────────────────────── #
# 1.  Load artifacts (called once; result is cached by the dashboard tab)
# ──────────────────────────────────────────────────────────────────────────── #
def load_artifacts(release: Path = DEFAULT_RELEASE,
                    model_name: str = "hgb_diagnostic") -> Dict[str, Any]:
    """Load model, imputer, scaler, feature_names, training pipeline module."""
    release = Path(release)
    model_path   = release / f"models/model_{model_name}.pkl"
    imputer_path = release / f"models/imputer_{model_name}.pkl"
    scaler_path  = release / f"models/scaler_{model_name}.pkl"
    feat_path    = release / "feature_names.json"
    pipeline_dir = release / "scripts"

    for label, p in [("model", model_path), ("imputer", imputer_path),
                     ("scaler", scaler_path), ("feature_names.json", feat_path),
                     ("pipeline.py dir", pipeline_dir)]:
        if not p.exists():
            raise FileNotFoundError(f"required {label} not found: {p}")

    # Import the training pipeline module (read-only)
    if str(pipeline_dir) not in sys.path:
        sys.path.insert(0, str(pipeline_dir))
    import pipeline as P  # type: ignore

    with open(model_path,   "rb") as f: model   = pickle.load(f)
    with open(imputer_path, "rb") as f: imputer = pickle.load(f)
    with open(scaler_path,  "rb") as f: scaler  = pickle.load(f)
    feature_names = json.loads(feat_path.read_text())

    # Validation
    if len(feature_names) != 77:
        raise ValueError(f"feature_names.json count = {len(feature_names)}; expected 77")
    if getattr(model, "n_features_in_", None) != 77:
        raise ValueError(f"model.n_features_in_ = {model.n_features_in_}")
    if getattr(imputer, "n_features_in_", None) != 77:
        raise ValueError(f"imputer.n_features_in_ = {imputer.n_features_in_}")
    if getattr(scaler, "n_features_in_", None) != 77:
        raise ValueError(f"scaler.n_features_in_ = {scaler.n_features_in_}")
    if list(model.classes_) != [0, 1]:
        raise ValueError(f"model.classes_ = {list(model.classes_)}; expected [0, 1]")

    return {
        "model":         model,
        "imputer":       imputer,
        "scaler":        scaler,
        "feature_names": feature_names,
        "pipeline":      P,
        "release":       release,
        "model_name":    model_name,
        "model_path":    model_path,
        "imputer_path":  imputer_path,
        "scaler_path":   scaler_path,
        "feature_names_path": feat_path,
    }


# ──────────────────────────────────────────────────────────────────────────── #
# 2.  Load master tail
# ──────────────────────────────────────────────────────────────────────────── #
def load_master_tail(master_path: Path = DEFAULT_MASTER,
                      tail_bars: int = 5000) -> pd.DataFrame:
    """Load master.ndjsonl, optionally restricted to the last N bars."""
    master_path = Path(master_path)
    with open(master_path) as f:
        lines = f.readlines()
    if tail_bars and tail_bars > 0:
        lines = lines[-tail_bars:]
    rows: List[Dict[str, Any]] = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    if not rows:
        raise RuntimeError(f"master file at {master_path} produced 0 parseable rows")
    df = pd.DataFrame(rows)
    df["bar_end_ts_ns"] = pd.to_numeric(df["bar_end_ts_ns"], errors="coerce")
    df = df.dropna(subset=["bar_end_ts_ns"])
    df["bar_end_ts_ns"] = df["bar_end_ts_ns"].astype("int64")
    df["utc_date"] = pd.to_datetime(df["bar_end_ts_ns"], unit="ns", utc=True).dt.date.astype(str)
    df = df.sort_values("bar_end_ts_ns").reset_index(drop=True)
    return df


# ──────────────────────────────────────────────────────────────────────────── #
# 3.  Per-day helpers
# ──────────────────────────────────────────────────────────────────────────── #
def add_ohlc_vol_features(df: pd.DataFrame, TICK: float) -> pd.DataFrame:
    """Verbatim port of pipeline.build_event_features._add_ohlc_vol()."""
    out = df.copy()
    h = pd.to_numeric(out["px_high"], errors="coerce")
    l = pd.to_numeric(out["px_low"], errors="coerce")
    c = pd.to_numeric(out["px_close"], errors="coerce")
    o = pd.to_numeric(out["px_open"], errors="coerce")
    vol = pd.to_numeric(out["volatility_5"], errors="coerce").replace(0, np.nan)
    rng = (h - l).abs()
    out["candle_body_vol"] = (c - o).abs() / vol
    out["candle_range_vol"] = rng / vol
    out["upper_wick_vol"] = (h - np.maximum(c, o)) / vol
    out["lower_wick_vol"] = (np.minimum(c, o) - l) / vol
    out["close_location"] = (c - l) / rng.replace(0, np.nan)
    out["open_to_close_sign"] = np.sign(c - o)
    prev_c = c.shift(1)
    out["close_vs_prev_close_vol"] = (c - prev_c) / vol
    out["close_vs_roll_mean_vol"] = (c - c.rolling(20, min_periods=5).mean()) / vol
    roll_max20 = c.rolling(20, min_periods=5).max().shift(1)
    roll_min20 = c.rolling(20, min_periods=5).min().shift(1)
    out["high_break_vol"] = (h - roll_max20) / vol
    out["low_break_vol"] = (roll_min20 - l) / vol
    return out


def compute_absorb_z(df_day: pd.DataFrame, TICK: float) -> np.ndarray:
    vt = pd.to_numeric(df_day["vol_total"], errors="coerce").to_numpy()
    h = pd.to_numeric(df_day["px_high"], errors="coerce").to_numpy()
    l = pd.to_numeric(df_day["px_low"], errors="coerce").to_numpy()
    prx_range = np.maximum(h - l, TICK)
    s = pd.Series(vt / prx_range)
    return ((s - s.rolling(50, min_periods=10).mean())
            / s.rolling(50, min_periods=10).std()).to_numpy()


# ──────────────────────────────────────────────────────────────────────────── #
# 4.  Run inference end-to-end
# ──────────────────────────────────────────────────────────────────────────── #
def run_inference(
        artifacts: Dict[str, Any],
        master_df: pd.DataFrame,
        confidence: float = 0.65,
        reaction_mode: str = "all_trained",
) -> pd.DataFrame:
    """Score every detected level-reaction event in the supplied master_df.

    Returns a predictions DataFrame ordered by bar_end_ts_ns.
    """
    P             = artifacts["pipeline"]
    model         = artifacts["model"]
    imputer       = artifacts["imputer"]
    scaler        = artifacts["scaler"]
    feature_names = artifacts["feature_names"]
    TICK          = P.TICK

    rows: List[Dict[str, Any]] = []
    event_id = 0
    feat_buf: List[Dict[str, Any]] = []

    for utc_date, sub in master_df.groupby("utc_date", sort=True):
        sub = sub.reset_index(drop=True)
        if len(sub) < 5:
            continue
        try:
            vp = P.volume_profile_levels(sub)
        except Exception:
            continue
        src_d = add_ohlc_vol_features(sub, TICK)

        h_arr = pd.to_numeric(sub["px_high"], errors="coerce").to_numpy()
        l_arr = pd.to_numeric(sub["px_low"], errors="coerce").to_numpy()
        c_arr = pd.to_numeric(sub["px_close"], errors="coerce").to_numpy()
        o_arr = pd.to_numeric(sub["px_open"], errors="coerce").to_numpy()
        delta = pd.to_numeric(sub["delta_norm"], errors="coerce").to_numpy()
        mid_z = pd.to_numeric(sub["mid_resid_z"], errors="coerce").to_numpy()
        vol_5 = pd.to_numeric(sub["volatility_5"], errors="coerce").to_numpy()
        prior_close = np.r_[np.nan, c_arr[:-1]]
        absorb_z = compute_absorb_z(sub, TICK)

        hvn_arr = np.array(vp["hvn_px"], dtype=float) if vp["hvn_px"] else np.array([], dtype=float)
        lvn_arr = np.array(vp["lvn_px"], dtype=float) if vp["lvn_px"] else np.array([], dtype=float)
        flat_levels: List[Tuple[float, str]] = [
            (vp["poc_px"], "POC"), (vp["vah_px"], "VAH"), (vp["val_px"], "VAL"),
        ] + [(p, "HVN") for p in vp["hvn_px"]] + [(p, "LVN") for p in vp["lvn_px"]]
        touch_counter: Dict[Tuple[float, str], int] = defaultdict(int)
        last_touch_bar: Dict[Tuple[float, str], int] = {}

        for i in range(len(sub)):
            c_i = c_arr[i]
            if not np.isfinite(c_i):
                continue
            v_i = vol_5[i] if (np.isfinite(vol_5[i]) and vol_5[i] > 1e-9) else float("nan")
            inside_va = int(vp["val_px"] <= c_i <= vp["vah_px"])
            above_vah = int(c_i > vp["vah_px"])
            below_val = int(c_i < vp["val_px"])
            nearest_hvn = (float(hvn_arr[int(np.argmin(np.abs(hvn_arr - c_i)))])
                            if hvn_arr.size else float("nan"))
            nearest_lvn = (float(lvn_arr[int(np.argmin(np.abs(lvn_arr - c_i)))])
                            if lvn_arr.size else float("nan"))

            def _dn(lv: float) -> float:
                return float((c_i - lv) / TICK) if np.isfinite(lv) else float("nan")

            def _dv(lv: float) -> float:
                if not (np.isfinite(lv) and np.isfinite(v_i)):
                    return float("nan")
                return float((c_i - lv) / v_i)

            for lp, ln in flat_levels:
                rxn = P.classify_bar_reaction(
                    h_arr[i], l_arr[i], c_arr[i], o_arr[i],
                    lp, ln,
                    delta[i], mid_z[i], absorb_z[i],
                    prior_close[i],
                    touch_counter[(lp, ln)])
                if rxn is None:
                    continue
                tc_past = touch_counter[(lp, ln)]
                bars_since = (i - last_touch_bar[(lp, ln)]) \
                              if (lp, ln) in last_touch_bar else -1
                touch_counter[(lp, ln)] += 1
                last_touch_bar[(lp, ln)] = i

                if reaction_mode == "all_trained" and rxn not in TRAINED_REACTION_TYPES:
                    continue

                bar = src_d.iloc[i]
                session = P.session_label(int(bar["minute_of_day"]))

                # 77-feature snapshot in exact order — build_dict then DataFrame select
                feat_row: Dict[str, Any] = {
                    "dist_to_poc_ticks": _dn(vp["poc_px"]),
                    "dist_to_vah_ticks": _dn(vp["vah_px"]),
                    "dist_to_val_ticks": _dn(vp["val_px"]),
                    "dist_to_hvn_ticks": _dn(nearest_hvn),
                    "dist_to_lvn_ticks": _dn(nearest_lvn),
                    "dist_to_poc_vol":   _dv(vp["poc_px"]),
                    "dist_to_vah_vol":   _dv(vp["vah_px"]),
                    "dist_to_val_vol":   _dv(vp["val_px"]),
                    "dist_to_hvn_vol":   _dv(nearest_hvn),
                    "dist_to_lvn_vol":   _dv(nearest_lvn),
                    "inside_value_area": inside_va,
                    "above_vah":         above_vah,
                    "below_val":         below_val,
                    "touch_count_past_only":  int(tc_past),
                    "bars_since_prior_touch": int(bars_since),
                }
                # Master-emitted cols
                for col in ("delta_norm","delta_norm_lag_1","delta_norm_lag_2","delta_norm_lag_3",
                            "delta_rolling_5",
                            "mlofi_decay_sum","mlofi_norm","mlofi_rolling_5","mlofi_accel","decay_norm",
                            "mlofi_norm_lag_1","mlofi_norm_lag_2","mlofi_norm_lag_3",
                            "decay_norm_lag_1","decay_norm_lag_2","decay_norm_lag_3",
                            "sweep_imbalance_norm","sweep_norm","sweep_buy_ratio","sweep_sell_ratio",
                            "buy_ratio","sell_ratio",
                            "vpin","vpin_lag_1","vpin_lag_2","vpin_lag_3",
                            "mid_resid_z","mid_ret1","volatility_5","bar_duration_s",
                            "delta_norm_resid_z20","volatility_5_resid_z20",
                            "sweep_imbalance_norm_resid_z20","vpin_resid_z20",
                            "entropy_score","flow_alignment"):
                    v = bar.get(col)
                    feat_row[col] = float(v) if pd.notna(v) else np.nan
                # OHLC-vol path
                for col in ("candle_body_vol","candle_range_vol","upper_wick_vol","lower_wick_vol",
                            "close_location","open_to_close_sign","close_vs_prev_close_vol",
                            "close_vs_roll_mean_vol","high_break_vol","low_break_vol"):
                    v = bar.get(col)
                    feat_row[col] = float(v) if pd.notna(v) else np.nan
                # One-hots
                for lt in ("POC", "VAH", "VAL", "HVN", "LVN"):
                    feat_row[f"lvl_{lt}"] = int(ln == lt)
                for s in ("Asia", "EU", "US_Open", "US_AM", "US_PM", "US_Late"):
                    feat_row[f"sess_{s}"] = int(session == s)
                feat_row["rxn_rejection_from_above"] = int("rejection_from_above" in rxn)
                feat_row["rxn_rejection_from_below"] = int("rejection_from_below" in rxn)
                feat_row["rxn_absorption"]           = int("absorption" in rxn)
                feat_row["rxn_acceptance"]           = int("acceptance" in rxn)
                feat_row["rxn_neutral_touch"]        = int("neutral_touch" in rxn)

                meta = TRAINED_REACTION_METADATA.get(rxn,
                            {"n_training": 0, "training_gate_status": "UNTRAINED"})
                rows.append({
                    "event_id":          event_id,
                    "bar_idx":           i,
                    "utc_date":          utc_date,
                    "timestamp":         bar.get("timestamp", ""),
                    "timestamp_utc":     bar.get("timestamp_utc", ""),
                    "bar_end_ts_ns":     int(bar["bar_end_ts_ns"]),
                    "close":             float(c_i),
                    "reaction_type":     rxn,
                    "trained_reaction":  bool(rxn in TRAINED_REACTION_TYPES),
                    "training_gate_status":    meta["training_gate_status"],
                    "n_training_for_reaction": meta["n_training"],
                    "level_type":        ln,
                    "level_price":       float(lp),
                    "dist_to_level_ticks": float((c_i - lp) / TICK),
                    "dist_to_poc_vol":   feat_row["dist_to_poc_vol"],
                    "dist_to_vah_vol":   feat_row["dist_to_vah_vol"],
                    "dist_to_val_vol":   feat_row["dist_to_val_vol"],
                })
                feat_buf.append(feat_row)
                event_id += 1

    if not rows:
        return pd.DataFrame()

    X = pd.DataFrame(feat_buf)
    X = X[feature_names].copy()
    X.replace([np.inf, -np.inf], np.nan, inplace=True)
    X_imp = imputer.transform(X.to_numpy(dtype=float))
    X_scl = scaler.transform(X_imp)
    proba = model.predict_proba(X_scl)
    p_short = proba[:, 0]
    p_long  = proba[:, 1]
    conf    = np.maximum(p_long, p_short)
    direction = np.where(p_long >= confidence, "LONG",
                  np.where(p_short >= confidence, "SHORT", "FLAT"))
    signal = np.where(p_long >= confidence, "HIGH_CONF_LONG",
              np.where(p_short >= confidence, "HIGH_CONF_SHORT", "LOW_CONF_FLAT"))

    pred = pd.DataFrame(rows)
    pred["p_long"]                  = p_long
    pred["p_short"]                 = p_short
    pred["confidence"]              = conf
    pred["direction"]               = direction
    pred["signal_status"]           = signal
    pred["passes_confidence_gate"]  = (conf >= confidence).astype(int)
    return pred.sort_values("bar_end_ts_ns").reset_index(drop=True)


def reaction_summary(pred: pd.DataFrame, confidence: float = 0.65) -> pd.DataFrame:
    if pred.empty:
        return pd.DataFrame()
    rows = []
    for rxn, sub in pred.groupby("reaction_type"):
        meta = TRAINED_REACTION_METADATA.get(rxn,
                    {"n_training": 0, "training_gate_status": "UNTRAINED"})
        latest = sub.sort_values("bar_end_ts_ns").iloc[-1]
        rows.append({
            "reaction_type":             rxn,
            "n":                          int(len(sub)),
            "trained_reaction":           bool(rxn in TRAINED_REACTION_TYPES),
            "training_gate_status":       meta["training_gate_status"],
            "n_training_for_reaction":    meta["n_training"],
            "mean_p_long":                round(float(sub["p_long"].mean()), 4),
            "mean_p_short":               round(float(sub["p_short"].mean()), 4),
            "mean_confidence":            round(float(sub["confidence"].mean()), 4),
            "pct_conf_ge_threshold":      round(100.0 * float((sub["confidence"] >= confidence).mean()), 2),
            "pct_high_conf_long":         round(100.0 * float((sub["signal_status"] == "HIGH_CONF_LONG").mean()), 2),
            "pct_high_conf_short":        round(100.0 * float((sub["signal_status"] == "HIGH_CONF_SHORT").mean()), 2),
            "latest_direction":           str(latest["direction"]),
            "latest_confidence":          round(float(latest["confidence"]), 4),
        })
    return pd.DataFrame(rows).sort_values("n", ascending=False).reset_index(drop=True)
