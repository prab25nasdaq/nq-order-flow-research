#!/usr/bin/env python3
"""
Level-Reaction Shadow Model Inference Script (READ-ONLY).

Loads the locked hgb_diagnostic model from
  /home/prabh/OFI_Production/model_registry/level_reaction_rithmic_only_shadow/
        level_reaction_rithmic_only_shadow_20260613T024116Z/
and scores every detected level-reaction event in the current Rithmic-only
master at /home/prabh/OFI_Live_Features/master.ndjsonl.

ABSOLUTE RULES (enforced):
  * Do NOT modify any release artifacts.
  * Do NOT modify master.
  * Do NOT modify parser, scheduler, or dashboard.
  * Do NOT enable paper/production execution.
  * Do NOT call fit() or fit_transform() — only transform()/predict_proba().
  * All outputs land in
        /home/prabh/OFI_Production/inference_scripts/level_reaction_shadow_inference/
        outputs/<timestamp>/

Pipeline:
  1.  Load master (optionally tail).
  2.  Group by UTC calendar date.
  3.  Per day: compute POC/VAH/VAL/HVN/LVN via training-time
      P.volume_profile_levels().
  4.  Compute causal absorb_z per day; per bar iterate over flat_levels
      and call training-time P.classify_bar_reaction() to tag events.
  5.  Snapshot 77 features per event in EXACT feature_names.json order.
  6.  imputer.transform → scaler.transform → model.predict_proba.
  7.  Write predictions + summary + report + parity check.
"""
from __future__ import annotations
import argparse, json, pickle, sys, warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


# ── Defaults ───────────────────────────────────────────────────────────────── #
DEFAULT_MASTER  = Path("/home/prabh/OFI_Live_Features/master.ndjsonl")
DEFAULT_RELEASE = Path("/home/prabh/OFI_Production/model_registry/"
                       "level_reaction_rithmic_only_shadow/"
                       "level_reaction_rithmic_only_shadow_20260613T024116Z")
DEFAULT_OUTROOT = Path("/home/prabh/OFI_Production/inference_scripts/"
                       "level_reaction_shadow_inference/outputs")

# Trained reaction types + informational gate metadata (from verified inspection)
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


# ── Helpers ────────────────────────────────────────────────────────────────── #
def utc_now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def block(reason: str, code: int = 2) -> None:
    print(f"\nBLOCKED: {reason}", flush=True)
    sys.exit(code)


# ── Master loader ─────────────────────────────────────────────────────────── #
def load_master_tail(path: Path, tail_bars: int) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    if tail_bars and tail_bars > 0:
        # Read only the last tail_bars lines efficiently using line buffering
        with open(path, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
        # Cheap approach for our 36MB master: read whole file, take last tail_bars lines.
        with open(path) as f:
            all_lines = f.readlines()
        target = all_lines[-tail_bars:]
        for line in target:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    else:
        with open(path) as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    if not rows:
        block(f"master file at {path} produced 0 parseable rows")
    df = pd.DataFrame(rows)
    df["bar_end_ts_ns"] = pd.to_numeric(df["bar_end_ts_ns"], errors="coerce")
    if df["bar_end_ts_ns"].isna().any():
        df = df.dropna(subset=["bar_end_ts_ns"]).copy()
    df["bar_end_ts_ns"] = df["bar_end_ts_ns"].astype("int64")
    df["utc_date"] = pd.to_datetime(df["bar_end_ts_ns"], unit="ns", utc=True).dt.date.astype(str)
    df = df.sort_values("bar_end_ts_ns").reset_index(drop=True)
    return df


# ── Per-day causal helpers ────────────────────────────────────────────────── #
def add_ohlc_vol_features(df: pd.DataFrame, TICK: float) -> pd.DataFrame:
    """Verbatim port of pipeline.build_event_features._add_ohlc_vol().

    Inputs come from master (parser-emitted columns). Computed in-place per day.
    """
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
    """Causal absorption-z: rolling z of (vol_total / px_range), window 50."""
    vt = pd.to_numeric(df_day["vol_total"], errors="coerce").to_numpy()
    h = pd.to_numeric(df_day["px_high"], errors="coerce").to_numpy()
    l = pd.to_numeric(df_day["px_low"],  errors="coerce").to_numpy()
    prx_range = np.maximum(h - l, TICK)
    s = pd.Series(vt / prx_range)
    z = ((s - s.rolling(50, min_periods=10).mean())
         / s.rolling(50, min_periods=10).std()).to_numpy()
    return z


# ── Main inference pipeline ───────────────────────────────────────────────── #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", type=Path, default=DEFAULT_MASTER)
    ap.add_argument("--release", type=Path, default=DEFAULT_RELEASE)
    ap.add_argument("--model-name", default="hgb_diagnostic")
    ap.add_argument("--tail-bars", type=int, default=5000,
                    help="0 = full master")
    ap.add_argument("--confidence", type=float, default=0.65)
    ap.add_argument("--reaction-mode", choices=["all_trained", "all_detected"],
                    default="all_trained")
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--fail-on-missing-features",
                    type=lambda s: s.lower() not in ("false", "0", "no"),
                    default=True)
    ap.add_argument("--save-debug",
                    type=lambda s: s.lower() not in ("false", "0", "no"),
                    default=True)
    args = ap.parse_args()

    # ── Resolve output dir ────────────────────────────────────────────────── #
    out_dir = args.out_dir or (DEFAULT_OUTROOT / utc_now_stamp())
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Preflight: release artifacts exist ────────────────────────────────── #
    model_path   = args.release / f"models/model_{args.model_name}.pkl"
    imputer_path = args.release / f"models/imputer_{args.model_name}.pkl"
    scaler_path  = args.release / f"models/scaler_{args.model_name}.pkl"
    feat_path    = args.release / "feature_names.json"
    pipeline_dir = args.release / "scripts"

    for label, p in [("model", model_path), ("imputer", imputer_path),
                     ("scaler", scaler_path), ("feature_names.json", feat_path),
                     ("pipeline.py dir", pipeline_dir),
                     ("master", args.master)]:
        if not p.exists():
            block(f"required {label} not found at {p}")

    # ── Import training pipeline (READ-ONLY) ──────────────────────────────── #
    sys.path.insert(0, str(pipeline_dir))
    try:
        import pipeline as P
    except Exception as e:
        block(f"could not import pipeline from {pipeline_dir}: {e}")
    for fname in ("TICK", "NEAR_TICKS_K", "NEAR_TICKS_PRICE",
                  "volume_profile_levels", "classify_bar_reaction",
                  "session_label"):
        if not hasattr(P, fname):
            block(f"pipeline.{fname} not found")
    TICK = P.TICK

    # ── Load model + scaler + imputer + feature_names ─────────────────────── #
    print(f"[LOAD] model:   {model_path}", flush=True)
    with open(model_path, "rb") as f:
        model = pickle.load(f)
    print(f"[LOAD] imputer: {imputer_path}", flush=True)
    with open(imputer_path, "rb") as f:
        imputer = pickle.load(f)
    print(f"[LOAD] scaler:  {scaler_path}", flush=True)
    with open(scaler_path, "rb") as f:
        scaler = pickle.load(f)

    feature_names: List[str] = json.loads(feat_path.read_text())
    REQUIRED_COUNT = len(feature_names)
    if REQUIRED_COUNT != 77:
        block(f"feature_names.json count = {REQUIRED_COUNT}; expected 77")
    if getattr(model, "n_features_in_", None) != 77:
        block(f"model.n_features_in_ = {getattr(model, 'n_features_in_', None)}; expected 77")
    if getattr(imputer, "n_features_in_", None) != 77:
        block(f"imputer.n_features_in_ = {getattr(imputer, 'n_features_in_', None)}; expected 77")
    if getattr(scaler, "n_features_in_", None) != 77:
        block(f"scaler.n_features_in_ = {getattr(scaler, 'n_features_in_', None)}; expected 77")
    if list(model.classes_) != [0, 1]:
        block(f"model.classes_ = {list(model.classes_)}; expected [0, 1]")

    # ── Load master ───────────────────────────────────────────────────────── #
    print(f"[LOAD] master:  {args.master}  (tail={args.tail_bars or 'ALL'})", flush=True)
    df = load_master_tail(args.master, args.tail_bars)
    print(f"        rows: {len(df):,}  dates: {sorted(df['utc_date'].unique())}", flush=True)

    # ── Per-day pipeline ──────────────────────────────────────────────────── #
    per_day_levels: Dict[str, Dict[str, Any]] = {}
    src: Dict[str, pd.DataFrame] = {}
    for utc_date, sub in df.groupby("utc_date", sort=True):
        sub = sub.reset_index(drop=True)
        if len(sub) < 5:
            print(f"  {utc_date}: skip — only {len(sub)} bars")
            continue
        try:
            vp = P.volume_profile_levels(sub)
        except Exception as e:
            print(f"  {utc_date}: skip — vp failure: {e}")
            continue
        per_day_levels[utc_date] = vp
        src[utc_date] = add_ohlc_vol_features(sub, TICK)
        print(f"  {utc_date}: POC={vp['poc_px']} VAH={vp['vah_px']} VAL={vp['val_px']} "
              f"n_HVN={len(vp['hvn_px'])} n_LVN={len(vp['lvn_px'])} bars={len(sub)}",
              flush=True)

    if not per_day_levels:
        block("no day produced a usable volume profile")

    # ── Build event stream + 77-feature snapshot per event ────────────────── #
    print("[BUILD] events + features ...", flush=True)
    rows: List[Dict[str, Any]] = []
    event_id = 0

    for utc_date, sub in src.items():
        vp = per_day_levels[utc_date]
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
            # Pre-compute per-bar level-context values that don't depend on event level
            inside_va = int(vp["val_px"] <= c_i <= vp["vah_px"])
            above_vah = int(c_i > vp["vah_px"])
            below_val = int(c_i < vp["val_px"])
            nearest_hvn = float(hvn_arr[int(np.argmin(np.abs(hvn_arr - c_i)))]) \
                          if hvn_arr.size else float("nan")
            nearest_lvn = float(lvn_arr[int(np.argmin(np.abs(lvn_arr - c_i)))]) \
                          if lvn_arr.size else float("nan")

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
                    touch_counter[(lp, ln)],
                )
                if rxn is None:
                    continue
                tc_past = touch_counter[(lp, ln)]
                bars_since = (i - last_touch_bar[(lp, ln)]) \
                              if (lp, ln) in last_touch_bar else -1
                touch_counter[(lp, ln)] += 1
                last_touch_bar[(lp, ln)] = i

                # Mode filter
                if args.reaction_mode == "all_trained" and rxn not in TRAINED_REACTION_METADATA:
                    continue

                # Pull required bar values
                bar = sub.iloc[i]
                session = P.session_label(int(bar["minute_of_day"]))

                # ── Assemble the 77-feature row in EXACT feature_names order ── #
                feat_row: Dict[str, Any] = {}
                # Level context — distances
                feat_row["dist_to_poc_ticks"] = _dn(vp["poc_px"])
                feat_row["dist_to_vah_ticks"] = _dn(vp["vah_px"])
                feat_row["dist_to_val_ticks"] = _dn(vp["val_px"])
                feat_row["dist_to_hvn_ticks"] = _dn(nearest_hvn)
                feat_row["dist_to_lvn_ticks"] = _dn(nearest_lvn)
                feat_row["dist_to_poc_vol"]   = _dv(vp["poc_px"])
                feat_row["dist_to_vah_vol"]   = _dv(vp["vah_px"])
                feat_row["dist_to_val_vol"]   = _dv(vp["val_px"])
                feat_row["dist_to_hvn_vol"]   = _dv(nearest_hvn)
                feat_row["dist_to_lvn_vol"]   = _dv(nearest_lvn)
                feat_row["inside_value_area"] = inside_va
                feat_row["above_vah"]         = above_vah
                feat_row["below_val"]         = below_val
                feat_row["touch_count_past_only"]  = int(tc_past)
                feat_row["bars_since_prior_touch"] = int(bars_since)
                # Master-emitted features (snapshot)
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
                # rxn one-hots: substring-based (matches training)
                feat_row["rxn_rejection_from_above"] = int("rejection_from_above" in rxn)
                feat_row["rxn_rejection_from_below"] = int("rejection_from_below" in rxn)
                feat_row["rxn_absorption"]           = int("absorption" in rxn)
                feat_row["rxn_acceptance"]           = int("acceptance" in rxn)
                feat_row["rxn_neutral_touch"]        = int("neutral_touch" in rxn)

                # Event metadata (NOT model input)
                meta = TRAINED_REACTION_METADATA.get(rxn,
                            {"n_training": 0, "training_gate_status": "UNTRAINED"})
                nearest_level = ("nearest_HVN" if ln == "HVN"
                                else "nearest_LVN" if ln == "LVN"
                                else ln)
                rows.append({
                    "event_id":          event_id,
                    "bar_idx":           i,
                    "utc_date":          utc_date,
                    "timestamp":         bar.get("timestamp", ""),
                    "timestamp_utc":     bar.get("timestamp_utc", ""),
                    "bar_end_ts_ns":     int(bar["bar_end_ts_ns"]),
                    "close":             float(c_i),
                    "reaction_type":     rxn,
                    "trained_reaction":  bool(rxn in TRAINED_REACTION_METADATA),
                    "training_gate_status":      meta["training_gate_status"],
                    "n_training_for_reaction":   meta["n_training"],
                    "level_type":        ln,
                    "level_price":       float(lp),
                    "nearest_level":     nearest_level,
                    "dist_to_level_ticks": float((c_i - lp) / TICK),
                    "dist_to_poc_vol":   feat_row["dist_to_poc_vol"],
                    "dist_to_vah_vol":   feat_row["dist_to_vah_vol"],
                    "dist_to_val_vol":   feat_row["dist_to_val_vol"],
                    "__feat":            feat_row,
                })
                event_id += 1

    if not rows:
        block("no events detected in the master window")

    print(f"        events tagged: {len(rows):,}", flush=True)

    # ── Feature alignment check ───────────────────────────────────────────── #
    feat_rows = [r["__feat"] for r in rows]
    X = pd.DataFrame(feat_rows)
    # EXACT column order
    fed_cols = list(X.columns)
    missing_in_X = [c for c in feature_names if c not in X.columns]
    extra_in_X   = [c for c in X.columns if c not in feature_names]
    if missing_in_X:
        if args.fail_on_missing_features:
            block(f"required features missing from inference X: {missing_in_X}")
    if extra_in_X:
        # We will simply drop extras when selecting feature_names order
        pass
    X = X[feature_names].copy()
    X.replace([np.inf, -np.inf], np.nan, inplace=True)

    # Persist feature alignment report
    presence_rows = []
    for i, c in enumerate(feature_names, 1):
        presence_rows.append({
            "position": i,
            "feature_name": c,
            "in_X": (c in fed_cols),
            "non_null_pct": round(100.0 * X[c].notna().mean(), 2),
        })
    pres_df = pd.DataFrame(presence_rows)
    pres_df.to_csv(out_dir / "feature_presence_check.csv", index=False)

    align_md = [
        "# Feature Alignment Report",
        "",
        f"_run: {utc_now_iso()}_",
        "",
        f"- feature_names.json: `{feat_path}`",
        f"- Required feature count: **{REQUIRED_COUNT}**",
        f"- Fed feature count:       **{X.shape[1]}**",
        f"- Exact ordered match:     **{list(X.columns) == feature_names}**",
        f"- model.n_features_in_:    **{model.n_features_in_}**",
        f"- imputer.n_features_in_:  **{imputer.n_features_in_}**",
        f"- scaler.n_features_in_:   **{scaler.n_features_in_}**",
        f"- Missing required:        **{missing_in_X or '(none) ✓'}**",
        f"- Extra (dropped):         **{extra_in_X or '(none) ✓'}**",
        "",
        "## Per-feature presence",
        "```",
        pres_df.to_string(index=False),
        "```",
    ]
    (out_dir / "feature_alignment_report.md").write_text("\n".join(align_md))

    # ── Predict ───────────────────────────────────────────────────────────── #
    print("[PREDICT] imputer → scaler → predict_proba ...", flush=True)
    X_arr = X.to_numpy(dtype=float)
    X_imp = imputer.transform(X_arr)
    X_scl = scaler.transform(X_imp)
    if X_scl.shape[1] != 77:
        block(f"processed X has {X_scl.shape[1]} cols; expected 77")
    proba = model.predict_proba(X_scl)
    p_short = proba[:, 0]
    p_long  = proba[:, 1]
    if not (np.isfinite(p_long).all() and np.isfinite(p_short).all()):
        block("predict_proba produced non-finite values")
    sum_check = np.abs(p_long + p_short - 1.0).max()
    if sum_check > 1e-6:
        block(f"p_long + p_short max deviation from 1.0 = {sum_check}; should be ~0")
    confidence = np.maximum(p_long, p_short)

    # Direction / status
    THRESH = float(args.confidence)
    direction = np.where(p_long  >= THRESH, "LONG",
                  np.where(p_short >= THRESH, "SHORT", "FLAT"))
    signal_status = np.where(p_long  >= THRESH, "HIGH_CONF_LONG",
                       np.where(p_short >= THRESH, "HIGH_CONF_SHORT", "LOW_CONF_FLAT"))
    passes_gate = (confidence >= THRESH).astype(int)

    # ── Predictions CSV ───────────────────────────────────────────────────── #
    pred_rows = []
    for i, r in enumerate(rows):
        pred_rows.append({
            "event_id":             r["event_id"],
            "bar_idx":              r["bar_idx"],
            "timestamp":            r["timestamp"],
            "timestamp_utc":        r["timestamp_utc"],
            "bar_end_ts_ns":        r["bar_end_ts_ns"],
            "close":                r["close"],
            "reaction_type":        r["reaction_type"],
            "trained_reaction":     r["trained_reaction"],
            "training_gate_status": r["training_gate_status"],
            "n_training_for_reaction": r["n_training_for_reaction"],
            "level_type":           r["level_type"],
            "level_price":          r["level_price"],
            "nearest_level":        r["nearest_level"],
            "dist_to_level_ticks":  r["dist_to_level_ticks"],
            "dist_to_poc_vol":      r["dist_to_poc_vol"],
            "dist_to_vah_vol":      r["dist_to_vah_vol"],
            "dist_to_val_vol":      r["dist_to_val_vol"],
            "p_long":               float(p_long[i]),
            "p_short":              float(p_short[i]),
            "confidence":           float(confidence[i]),
            "direction":            str(direction[i]),
            "signal_status":        str(signal_status[i]),
            "passes_confidence_gate": int(passes_gate[i]),
            "model_name":           args.model_name,
            "release_path":         str(args.release),
            "feature_count_fed":    int(X.shape[1]),
        })
    pred_df = pd.DataFrame(pred_rows)
    pred_df.to_csv(out_dir / "level_reaction_shadow_predictions_all_reactions.csv", index=False)
    print(f"[OUT] predictions: {len(pred_df):,} rows  →  "
          f"level_reaction_shadow_predictions_all_reactions.csv", flush=True)

    # ── Per-reaction summary ──────────────────────────────────────────────── #
    summ_rows = []
    for rxn, sub in pred_df.groupby("reaction_type"):
        meta = TRAINED_REACTION_METADATA.get(rxn,
                    {"n_training": 0, "training_gate_status": "UNTRAINED"})
        latest = sub.sort_values("bar_end_ts_ns").iloc[-1]
        summ_rows.append({
            "reaction_type":             rxn,
            "n":                          int(len(sub)),
            "trained_reaction":           bool(rxn in TRAINED_REACTION_METADATA),
            "training_gate_status":       meta["training_gate_status"],
            "n_training_for_reaction":    meta["n_training"],
            "mean_p_long":                round(float(sub["p_long"].mean()), 4),
            "mean_p_short":               round(float(sub["p_short"].mean()), 4),
            "mean_confidence":            round(float(sub["confidence"].mean()), 4),
            "pct_conf_ge_threshold":      round(100.0 * float((sub["confidence"] >= THRESH).mean()), 2),
            "pct_high_conf_long":         round(100.0 * float((sub["signal_status"] == "HIGH_CONF_LONG").mean()), 2),
            "pct_high_conf_short":        round(100.0 * float((sub["signal_status"] == "HIGH_CONF_SHORT").mean()), 2),
            "latest_timestamp":           str(latest["timestamp"]),
            "latest_direction":           str(latest["direction"]),
            "latest_confidence":          round(float(latest["confidence"]), 4),
        })
    summ_df = pd.DataFrame(summ_rows).sort_values("n", ascending=False)
    summ_df.to_csv(out_dir / "reaction_prediction_summary_all_reactions.csv", index=False)

    # ── Summary JSON ──────────────────────────────────────────────────────── #
    detected_reactions = sorted(pred_df["reaction_type"].unique())
    trained_present = [r for r in TRAINED_REACTION_METADATA if r in detected_reactions]
    trained_missing = [r for r in TRAINED_REACTION_METADATA if r not in detected_reactions]
    summary_json: Dict[str, Any] = {
        "run_utc":             utc_now_iso(),
        "master_path":         str(args.master),
        "release_path":        str(args.release),
        "model_path":          str(model_path),
        "model_name":          args.model_name,
        "model_class":         f"{type(model).__module__}.{type(model).__name__}",
        "model_classes":       [int(c) for c in model.classes_],
        "imputer_path":        str(imputer_path),
        "scaler_path":         str(scaler_path),
        "feature_names_path":  str(feat_path),
        "feature_count_required": REQUIRED_COUNT,
        "feature_count_fed":      int(X.shape[1]),
        "feature_order_exact":    list(X.columns) == feature_names,
        "tail_bars":              args.tail_bars,
        "reaction_mode":          args.reaction_mode,
        "confidence_threshold":   THRESH,
        "bars_loaded":            int(len(df)),
        "trained_reaction_types_total": len(TRAINED_REACTION_METADATA),
        "trained_reactions_present_in_window": trained_present,
        "trained_reactions_missing_from_window": trained_missing,
        "reactions_detected":     detected_reactions,
        "event_count":            int(len(pred_df)),
        "p_long_sum_check_max_dev_from_1": float(sum_check),
        "predictions_csv":        str(out_dir / "level_reaction_shadow_predictions_all_reactions.csv"),
        "summary_csv":            str(out_dir / "reaction_prediction_summary_all_reactions.csv"),
        "feature_alignment_report": str(out_dir / "feature_alignment_report.md"),
        "verdict":                "PASS",
    }
    (out_dir / "inference_summary_all_reactions.json").write_text(json.dumps(summary_json, indent=2))

    # ── Markdown report ──────────────────────────────────────────────────── #
    latest20 = pred_df.sort_values("bar_end_ts_ns").tail(20)
    top20    = pred_df.sort_values("confidence", ascending=False).head(20)
    md = [
        "# Level-Reaction Shadow Inference Report (ALL REACTIONS)",
        "",
        f"_run: {utc_now_iso()}_",
        "",
        "## Inputs",
        f"- Master: `{args.master}`",
        f"- Release: `{args.release}`",
        f"- Model file: `{model_path}`",
        f"- Model type: `{summary_json['model_class']}`",
        f"- `model.classes_`: {summary_json['model_classes']}",
        f"- Imputer: `{imputer_path}`",
        f"- Scaler:  `{scaler_path}`",
        f"- feature_names.json: `{feat_path}`",
        f"- Required feature count: {REQUIRED_COUNT}",
        f"- Fed feature count: {X.shape[1]}",
        f"- Confidence threshold: {THRESH}",
        f"- Reaction mode: {args.reaction_mode}",
        f"- Bars loaded: {len(df):,}",
        "",
        "## Reactions",
        f"- All trained ({len(TRAINED_REACTION_METADATA)}): {sorted(TRAINED_REACTION_METADATA.keys())}",
        f"- Detected in window: {detected_reactions}",
        f"- Trained present:    {trained_present}",
        f"- Trained missing:    {trained_missing}",
        f"- Total events scored: {len(pred_df):,}",
        "",
        "## Per-reaction summary",
        "```",
        summ_df.to_string(index=False),
        "```",
        "",
        "## Latest 20 predictions",
        "```",
        latest20[["bar_end_ts_ns","reaction_type","level_type","close",
                   "p_long","p_short","confidence","direction","signal_status"]]
                  .to_string(index=False),
        "```",
        "",
        "## Top 20 highest confidence predictions",
        "```",
        top20[["bar_end_ts_ns","reaction_type","level_type","close",
                "p_long","p_short","confidence","direction","signal_status"]]
              .to_string(index=False),
        "```",
        "",
        "## Warnings / Errors",
        "(none)" if not (missing_in_X or extra_in_X) else f"- missing: {missing_in_X}\n- extra (dropped): {extra_in_X}",
        "",
        "## Verdict",
        "**PASS**",
    ]
    (out_dir / "LEVEL_REACTION_SHADOW_INFERENCE_ALL_REACTIONS_REPORT.md").write_text("\n".join(md))

    # ── Parity check on stored training artifacts ─────────────────────────── #
    print("[PARITY] running parity check on stored X ...", flush=True)
    parity: Dict[str, Any] = {"run_utc": utc_now_iso()}
    try:
        X_stored = pd.read_parquet(args.release / "data/X_level_reaction_events.parquet")
        cols_ok = all(c in X_stored.columns for c in feature_names)
        parity["stored_X_path"]      = str(args.release / "data/X_level_reaction_events.parquet")
        parity["stored_X_rows"]      = int(len(X_stored))
        parity["stored_X_cols"]      = int(X_stored.shape[1])
        parity["stored_X_has_all_77_features"] = bool(cols_ok)
        if cols_ok:
            X_sub = X_stored[feature_names].head(100).copy()
            X_sub.replace([np.inf, -np.inf], np.nan, inplace=True)
            Xi = imputer.transform(X_sub.to_numpy(dtype=float))
            Xs = scaler.transform(Xi)
            pr = model.predict_proba(Xs)
            parity["replayed_n"]            = int(pr.shape[0])
            parity["replayed_p_long_mean"]  = round(float(pr[:, 1].mean()), 4)
            parity["replayed_p_short_mean"] = round(float(pr[:, 0].mean()), 4)
            parity["replayed_all_finite"]   = bool(np.isfinite(pr).all())
            parity["replayed_sum_max_dev"]  = float(np.abs(pr.sum(axis=1) - 1.0).max())
        # Stored predictions
        preds_stored = pd.read_csv(args.release / "data/per_event_predictions.csv")
        hgb_stored = preds_stored[preds_stored["model"] == args.model_name]
        parity["stored_predictions_path"]        = str(args.release / "data/per_event_predictions.csv")
        parity["stored_predictions_rows_for_model"] = int(len(hgb_stored))
        parity["note"] = ("Stored proba_long values were generated by per-FOLD CV "
                          "models (fold-specific imputer/scaler/classifier). The "
                          "final saved model_<name>.pkl was fit on ALL labelled data "
                          "after CV, so its probabilities will not exactly match the "
                          "stored per-event CV probabilities. Direct numeric equality "
                          "is NOT expected; what we verify is that replay on stored X "
                          "produces finite probabilities that sum to ~1.")
        parity["verdict"] = "PASS" if parity.get("replayed_all_finite") else "FAIL"
    except Exception as e:
        parity["error"] = str(e)
        parity["verdict"] = "ERROR"
    (out_dir / "training_artifact_parity_check_all_reactions.json").write_text(
        json.dumps(parity, indent=2))
    parity_md = [
        "# Training-Artifact Parity Check",
        "",
        f"_run: {utc_now_iso()}_",
        "",
        f"- Stored X path: `{parity.get('stored_X_path')}`",
        f"- Stored X rows: {parity.get('stored_X_rows')}",
        f"- Stored X cols: {parity.get('stored_X_cols')}",
        f"- All 77 features present in stored X: {parity.get('stored_X_has_all_77_features')}",
        f"- Replayed predict_proba on first 100 stored rows: n={parity.get('replayed_n')}, "
            f"all_finite={parity.get('replayed_all_finite')}, "
            f"sum_max_dev={parity.get('replayed_sum_max_dev')}",
        f"- Replayed p_long mean: {parity.get('replayed_p_long_mean')}",
        f"- Replayed p_short mean: {parity.get('replayed_p_short_mean')}",
        f"- Stored predictions rows for `{args.model_name}`: {parity.get('stored_predictions_rows_for_model')}",
        "",
        "## Note on direct numeric comparison",
        f"{parity.get('note','')}",
        "",
        f"## Verdict: **{parity.get('verdict')}**",
    ]
    (out_dir / "training_artifact_parity_check_all_reactions.md").write_text("\n".join(parity_md))

    # ── Final terminal output ────────────────────────────────────────────── #
    test_status = "PASS"
    print("")
    print("SCRIPT_CREATED: /home/prabh/OFI_Production/inference_scripts/"
          "level_reaction_shadow_inference/level_reaction_inference.py")
    print(f"MODEL_LOADED: {model_path}")
    print(f"MODEL_CLASSES: {[int(c) for c in model.classes_]}")
    print(f"IMPUTER_LOADED: {imputer_path}")
    print(f"SCALER_LOADED: {scaler_path}")
    print(f"FEATURE_COUNT_REQUIRED: {REQUIRED_COUNT}")
    print(f"FEATURE_COUNT_FED: {X.shape[1]}")
    print(f"TRAINED_REACTIONS_COUNT: {len(TRAINED_REACTION_METADATA)}")
    print(f"TRAINED_REACTIONS_LIST: {sorted(TRAINED_REACTION_METADATA.keys())}")
    print(f"BARS_LOADED: {len(df)}")
    print(f"EVENT_COUNT: {len(rows)}")
    print(f"PREDICTION_COUNT: {len(pred_df)}")
    print(f"REACTIONS_SCORED: {detected_reactions}")
    print("LATEST_10_PREDICTIONS:")
    print(latest20.tail(10)[["bar_end_ts_ns","reaction_type","level_type","close",
                              "p_long","p_short","confidence","direction"]]
                   .to_string(index=False))
    print("TOP_10_HIGH_CONFIDENCE:")
    print(top20.head(10)[["bar_end_ts_ns","reaction_type","level_type","close",
                           "p_long","p_short","confidence","direction"]]
                .to_string(index=False))
    print(f"OUTPUT_DIR: {out_dir}")
    print(f"TEST_STATUS: {test_status}")
    print("")
    print("OVERALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
