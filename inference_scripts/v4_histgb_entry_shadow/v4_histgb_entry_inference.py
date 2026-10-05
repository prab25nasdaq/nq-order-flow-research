#!/usr/bin/env python3
"""
v4_histgb_entry_inference.py — v4 HistGB Entry ACT/PASS SHADOW DISPLAY ONLY
inference daemon.

Makes the v4 research HistGB Entry model ("HistGB ACT/PASS Filter",
afml_label_policy_parity_entry_cp_v4_20260623T192440Z) score the LATEST
level-reaction event for SHADOW DISPLAY in the dashboard's MODEL CONTROL
tab. This is explanatory/research scoring only:

  - NEVER promotes v4 to production
  - NEVER touches ACTIVE_SHADOW_RELEASE (that symlink belongs exclusively
    to the EXISTING dashboard model's own registry; v4 is a completely
    separate research model and has no production release slot at all)
  - NEVER enables broker/paper-trading/execution
  - NEVER writes to any production master/feature file - reads
    model_feature_master_shadow.parquet / *_latest.csv / *_status.json and
    predictions.csv read-only

Model artifact handling (Requirement #1/#2):
  v4's own research build never saved a fitted model object (its training
  scripts only ran out-of-fold cross-validation for VALIDATION, never a
  final fit-on-all-data step) - confirmed by direct inspection (no .pkl
  anywhere under the v4 research folder). This script reconstructs ONE
  exactly, by fitting a final HistGradientBoostingClassifier (same
  hyperparameters v4's own config used: max_iter=200, max_depth=4,
  learning_rate=0.05, random_state=42) on v4's own
  entry_meta_label_dataset_v4.parquet, using v4's own average-uniqueness
  sample weights - and SAVES it under model_candidate/ so subsequent runs
  load instead of retraining. The v4 RESEARCH folder itself is never
  written to (read-only) - the reconstructed artifact lives entirely under
  this new inference_scripts/v4_histgb_entry_shadow/ directory.

Feature list (Requirement #3): the exact 132-name feature set v4 itself
trained on (master_/dash_/ofild_/bf_/modelprob_/rxn_/lvl_/gate_ prefixes +
side_primary/distance_ticks/touch_count_past_only/bars_since_prior_touch),
with the 3 raw-absolute-price columns (master_px_close/high/low) EXCLUDED -
those were found, during the model_feature_master build, to have been
included in v4's original training despite the existing model's own
hard-banned feature policy; this shadow-inference script does NOT repeat
that inconsistency.

READ-ONLY against all production paths except this script's own output
directory. SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER
TRADING.
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.preprocessing import StandardScaler

THIS_DIR = Path(__file__).resolve().parent
OUT_DIR = THIS_DIR / "outputs"
MODEL_DIR = THIS_DIR / "model_candidate"
LOG_DIR = THIS_DIR / "logs"
for d in (OUT_DIR, MODEL_DIR, LOG_DIR):
    d.mkdir(exist_ok=True, parents=True)

V4_DIR = Path("/home/prabh/OFI_Production/research_engines/afml_label_policy_parity_entry_cp_v4_20260623T192440Z")
FM_ROOT = Path("/home/prabh/OFI_Production/model_feature_master")
FM_DATA_DIR = FM_ROOT / "data"
PRED_CSV = Path("/home/prabh/OFI_Production/inference_scripts/level_reaction_continuous_nq_shadow/outputs/"
               "latest_continuous_nq_predictions.csv")
ACTIVE_RELEASE_LINK = Path(
    "/home/prabh/OFI_Production/model_registry/level_reaction_continuous_nq_shadow/ACTIVE_SHADOW_RELEASE")

PRED_CSV_OUT = OUT_DIR / "latest_v4_histgb_entry_predictions.csv"
SUMMARY_JSON_OUT = OUT_DIR / "latest_v4_histgb_entry_summary.json"
MODEL_PKL = MODEL_DIR / "model_hist_gb_shadow.pkl"
SCALER_PKL = MODEL_DIR / "scaler_hist_gb_shadow.pkl"
FEATURE_NAMES_JSON = MODEL_DIR / "feature_names.json"
TRAINING_METADATA_JSON = MODEL_DIR / "training_metadata.json"

FM_STALE_THRESHOLD_SECONDS = 600  # matches model_control_tab.py's own threshold
ACT_THRESHOLD = 0.5

FEATURE_PREFIXES = ("master_", "dash_", "ofild_", "bf_", "modelprob_", "rxn_", "lvl_", "gate_")
STRUCTURAL_FEATURES = ("side_primary", "distance_ticks", "touch_count_past_only", "bars_since_prior_touch")
BANNED_RAW_PRICE_COLS = {"master_px_close", "master_px_high", "master_px_low"}

# v4's own existing-model reaction-type -> side rule (Part D derive_side,
# reproduced verbatim - never re-derived from scratch).
SIDE_RULE = {"rejection_from_above": 1, "rejection_from_below": -1}


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_DIR / "v4_histgb_entry_inference.log", "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def derive_side(reaction_type: str) -> int:
    base = reaction_type.split("_", 1)[1] if reaction_type.split("_", 1)[0] in ("POC", "VAH", "VAL", "HVN", "LVN") else reaction_type
    if base in SIDE_RULE:
        return SIDE_RULE[base]
    if reaction_type.startswith("breakout_acceptance_above_"):
        return 1
    if reaction_type.startswith("breakdown_acceptance_below_"):
        return -1
    return 0


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


def select_feature_cols(df: pd.DataFrame) -> list:
    keep = []
    for c in df.columns:
        if c in BANNED_RAW_PRICE_COLS:
            continue  # Requirement #3: exclude banned absolute price columns
        if not pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c]):
            continue
        if c in STRUCTURAL_FEATURES or c.startswith(FEATURE_PREFIXES):
            keep.append(c)
    return keep


def fm_path(filename: str) -> Optional[Path]:
    for cand in (FM_ROOT / filename, FM_DATA_DIR / filename):
        if cand.exists():
            return cand
    return None


def _read_json(path: Optional[Path]) -> dict:
    if path is None:
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def train_and_save_candidate() -> Tuple[Any, Any, list]:
    """Requirement #2: reconstruct a final-fit HistGB shadow candidate from
    v4's own training outputs (no saved artifact existed). Writes the
    fitted model/scaler/feature_names under model_candidate/ - never writes
    anything under the v4 research folder itself (read-only)."""
    log("no saved model artifact found - reconstructing a shadow candidate from v4 training outputs...")
    df = pd.read_parquet(V4_DIR / "outputs" / "entry_meta_label_dataset_v4.parquet")
    feature_cols = select_feature_cols(df)
    feature_cols = [c for c in feature_cols if df[c].notna().mean() > 0.5]
    elig = df.dropna(subset=feature_cols).reset_index(drop=True)
    log(f"  training rows: {len(elig)}  features: {len(feature_cols)}  "
       f"(excluded banned raw-price cols: {sorted(BANNED_RAW_PRICE_COLS)})")

    n_bars = int(elig["nqu6_bar_idx"].max()) + 41
    t0 = elig["nqu6_bar_idx"].to_numpy()
    t1 = t0 + (elig["t1_idx"].to_numpy() - elig["t0_idx"].to_numpy())
    c_t = num_co_events(t0, t1, n_bars)
    sw = average_uniqueness(t0, t1, c_t)
    sw = np.nan_to_num(sw, nan=float(np.nanmean(sw)) if np.isfinite(np.nanmean(sw)) else 1.0)
    log(f"  effective N (sum avg_uniqueness): {sw.sum():.2f} / raw N {len(elig)}")

    scaler = StandardScaler()
    X = scaler.fit_transform(elig[feature_cols])
    y = elig["y_entry"].to_numpy()
    clf = HistGradientBoostingClassifier(max_iter=200, max_depth=4, learning_rate=0.05, random_state=42)
    clf.fit(X, y, sample_weight=sw)
    log("  final-fit HistGradientBoostingClassifier trained on ALL eligible v4 entry rows.")

    with open(MODEL_PKL, "wb") as f:
        pickle.dump(clf, f)
    with open(SCALER_PKL, "wb") as f:
        pickle.dump(scaler, f)
    with open(FEATURE_NAMES_JSON, "w") as f:
        json.dump(feature_cols, f, indent=2)
    metadata = {
        "reconstructed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_v4_dir": str(V4_DIR),
        "source_dataset": "entry_meta_label_dataset_v4.parquet",
        "n_training_rows": int(len(elig)),
        "n_features": len(feature_cols),
        "effective_independent_sample_count": float(sw.sum()),
        "model_type": "HistGradientBoostingClassifier",
        "hyperparameters": {"max_iter": 200, "max_depth": 4, "learning_rate": 0.05, "random_state": 42},
        "banned_features_excluded": sorted(BANNED_RAW_PRICE_COLS),
        "note": "RESEARCH-ONLY reconstructed final fit, NOT the v4 build's own out-of-fold cross-"
                "validation models (v4 itself never saved a fitted artifact). Never promoted to "
                "production. v4's own research folder was never written to.",
    }
    with open(TRAINING_METADATA_JSON, "w") as f:
        json.dump(metadata, f, indent=2)
    log(f"  saved shadow candidate model -> {MODEL_DIR}")
    return clf, scaler, feature_cols


def load_or_train_model() -> Tuple[Any, Any, list]:
    if MODEL_PKL.exists() and SCALER_PKL.exists() and FEATURE_NAMES_JSON.exists():
        try:
            with open(MODEL_PKL, "rb") as f:
                clf = pickle.load(f)
            with open(SCALER_PKL, "rb") as f:
                scaler = pickle.load(f)
            feature_cols = json.loads(FEATURE_NAMES_JSON.read_text())
            log(f"loaded existing shadow candidate model from {MODEL_DIR}")
            return clf, scaler, feature_cols
        except Exception as e:
            log(f"existing model artifact unreadable ({e}) - reconstructing fresh")
    return train_and_save_candidate()


def _file_age_seconds(path: Optional[Path]) -> Optional[float]:
    if path is None or not path.exists():
        return None
    return time.time() - path.stat().st_mtime


def score_latest_event(clf, scaler, feature_cols: list) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "scored_at_utc": datetime.now(timezone.utc).isoformat(),
        "threshold_used": ACT_THRESHOLD,
    }

    if not PRED_CSV.exists():
        out["status"] = "INFERENCE_FILE_MISSING"
        return out
    pred = pd.read_csv(PRED_CSV)
    if pred.empty:
        out["status"] = "INFERENCE_FILE_MISSING"
        return out
    latest = pred.sort_values("bar_end_ts_ns", kind="stable").iloc[-1]

    out["timestamp"] = latest.get("timestamp_utc")
    out["bar_end_ts_ns"] = int(latest["bar_end_ts_ns"]) if pd.notna(latest.get("bar_end_ts_ns")) else None
    out["reaction_type"] = latest.get("reaction_type")
    out["level_type"] = latest.get("level_type")

    # active model probability_source (CURRENT_EVENT vs HELD_LAST) for context
    fm_status = _read_json(fm_path("feature_master_status.json"))
    fm_latest_ts = fm_status.get("max_bar_end_ts_ns") or fm_status.get("last_successful_bar_end_ts_ns")
    if fm_latest_ts is not None and out["bar_end_ts_ns"] is not None:
        fm_latest_ts = int(fm_latest_ts)
        if fm_latest_ts == out["bar_end_ts_ns"]:
            out["active_model_probability_source"] = "CURRENT_EVENT"
        elif fm_latest_ts > out["bar_end_ts_ns"]:
            out["active_model_probability_source"] = "HELD_LAST"
        else:
            out["active_model_probability_source"] = "CURRENT_EVENT_FM_LAGGING"
    else:
        out["active_model_probability_source"] = "NONE"

    # Requirement #4: only score structurally-valid v4-style candidates
    side_primary = derive_side(str(out["reaction_type"])) if out["reaction_type"] else 0
    out["side_primary"] = side_primary
    if side_primary == 0:
        out["status"] = "NO_CANDIDATE"
        out["ACT_probability"] = None
        out["PASS_probability"] = None
        out["ACT_PASS_decision"] = None
        out["feature_master_age_s"] = _file_age_seconds(fm_path("feature_master_status.json"))
        return out

    # Requirement #7: feature-master staleness gate
    fm_age = _file_age_seconds(fm_path("feature_master_status.json"))
    out["feature_master_age_s"] = fm_age
    if fm_age is None or fm_age > FM_STALE_THRESHOLD_SECONDS:
        out["status"] = "FEATURE_MASTER_STALE"
        out["ACT_probability"] = None
        out["PASS_probability"] = None
        out["ACT_PASS_decision"] = None
        return out

    # Requirement #5: join feature master row by bar_end_ts_ns
    fm_latest = pd.read_csv(fm_path("model_feature_master_latest.csv")) if fm_path("model_feature_master_latest.csv") else pd.DataFrame()
    if fm_latest.empty:
        out["status"] = "FM_EMPTY"
        out["ACT_probability"] = None
        out["PASS_probability"] = None
        out["ACT_PASS_decision"] = None
        return out
    if out["bar_end_ts_ns"] not in set(fm_latest["bar_end_ts_ns"]):
        prob_src = out.get("active_model_probability_source", "NONE")
        if prob_src == "CURRENT_EVENT_FM_LAGGING":
            # New event bar not yet processed by feature master; transient, resolves next FM cycle (~30s)
            out["status"] = "EVENT_BAR_NOT_YET_IN_FM"
        else:
            # Event bar scrolled out of FM's 500-row rolling window (500+ bars since last event)
            out["status"] = "EVENT_BAR_SCROLLED_FROM_FM_WINDOW"
        out["ACT_probability"] = None
        out["PASS_probability"] = None
        out["ACT_PASS_decision"] = None
        return out
    fm_row = fm_latest[fm_latest["bar_end_ts_ns"] == out["bar_end_ts_ns"]].iloc[-1]

    # structural features v4 itself derives from the candidate's own event,
    # not generically present per-bar in the feature master (touch tracking
    # IS already per-bar in the feature master; distance_ticks/side_primary
    # are candidate-specific and computed here from the SAME predictions.csv
    # row, mirroring v4's own Part D/E construction).
    row_feats: Dict[str, Any] = dict(fm_row)
    row_feats["side_primary"] = side_primary
    dist_ticks = latest.get("dist_to_level_ticks")
    row_feats["distance_ticks"] = float(dist_ticks) if pd.notna(dist_ticks) else np.nan
    for lt in ("POC", "VAH", "VAL", "HVN", "LVN"):
        row_feats[f"lvl_{lt}"] = int(out["level_type"] == lt)
    rxn = str(out["reaction_type"])
    for r in ("rejection_from_above", "rejection_from_below", "absorption", "neutral_touch",
             "breakout_acceptance_above", "breakdown_acceptance_below"):
        row_feats[f"rxn_{r}"] = int(rxn.endswith(r) or (r in rxn))
    gate_status = latest.get("training_gate_status")
    for gs in ("PRIMARY_USE", "SECONDARY_WATCH", "BLOCKED_NEGATIVE", "SMALL_N", "EXPLORATORY_SMALL_N"):
        row_feats[f"gate_{gs}"] = int(gate_status == gs)

    # Targeted, documented fallbacks for columns already classified
    # EXPECTED_SPARSE in the model_feature_master build's own validation
    # report (event-gated: only populated when the feature master's OWN
    # per-bar reaction/touch detection independently fires AT THAT EXACT
    # bar, which need not coincide with predictions.csv's own event bar).
    # This is NOT a blanket NaN-fill - only these 3 specific, already-
    # documented-sparse columns get a default; any OTHER missing feature
    # still blocks scoring (HARD_FEATURE_MISSING / NAN_REQUIRED_FEATURE below).
    if pd.isna(row_feats.get("touch_count_past_only")):
        row_feats["touch_count_past_only"] = 0  # "first known touch" default
    if pd.isna(row_feats.get("bars_since_prior_touch")):
        row_feats["bars_since_prior_touch"] = -1  # existing model's own "no prior touch" sentinel
    if pd.isna(row_feats.get("modelprob_bars_since_last_event")):
        # this candidate's bar IS the latest predictions.csv event by
        # construction, so bars-since-its-own-event is 0, computed
        # directly rather than relying on the feature master's own
        # (possibly lagging) asof reconstruction for this one field.
        row_feats["modelprob_bars_since_last_event"] = 0

    absent = [c for c in feature_cols if c not in row_feats]
    nan_cols = [c for c in feature_cols if c in row_feats and pd.isna(row_feats.get(c))]
    if absent:
        # Feature column not in FM schema at all - FM build mismatch
        out["status"] = "HARD_FEATURE_MISSING"
        out["missing_features_sample"] = absent[:10]
        out["ACT_probability"] = None
        out["PASS_probability"] = None
        out["ACT_PASS_decision"] = None
        return out
    if nan_cols:
        # Feature column present in FM but NaN for this event bar
        # (typical cause: ofild_* when OFI Level Decision daemon was offline,
        #  or bf_* when Book Flow cache was missing for that bar)
        out["status"] = "NAN_REQUIRED_FEATURE"
        out["missing_features_sample"] = nan_cols[:10]
        out["ACT_probability"] = None
        out["PASS_probability"] = None
        out["ACT_PASS_decision"] = None
        return out

    X_row = pd.DataFrame([{c: row_feats[c] for c in feature_cols}])
    X_scaled = scaler.transform(X_row)
    p_act = float(clf.predict_proba(X_scaled)[0, 1])
    out["ACT_probability"] = round(p_act, 4)
    out["PASS_probability"] = round(1.0 - p_act, 4)
    out["ACT_PASS_decision"] = "ACT" if p_act >= ACT_THRESHOLD else "PASS"
    out["status"] = "OK"
    return out


def atomic_append_csv(row: Dict[str, Any], path: Path) -> None:
    """Append a new bar_end_ts_ns row, or UPSERT (replace) the existing row
    for that same bar if it was already scored - e.g. a bar first scored as
    FEATURE_MASTER_STALE should be overwritten once a later cycle has fresh
    data and produces a real OK/ACT/PASS result for that same bar, rather
    than being silently skipped (skip-only would leave a permanently stale
    row in the history even after the data caught up)."""
    df_row = pd.DataFrame([row])
    if path.exists():
        try:
            existing = pd.read_csv(path)
            if "bar_end_ts_ns" in existing.columns and row.get("bar_end_ts_ns") in set(existing["bar_end_ts_ns"]):
                existing = existing[existing["bar_end_ts_ns"] != row.get("bar_end_ts_ns")]
            combined = pd.concat([existing, df_row], ignore_index=True)
        except Exception:
            combined = df_row
    else:
        combined = df_row
    combined = combined.sort_values("bar_end_ts_ns", kind="stable").reset_index(drop=True)
    tmp = path.with_suffix(".csv.tmp")
    combined.to_csv(tmp, index=False)
    tmp.replace(path)


def atomic_write_json(obj: dict, path: Path) -> None:
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, default=str)
    tmp.replace(path)


def run_once() -> Dict[str, Any]:
    clf, scaler, feature_cols = load_or_train_model()
    result = score_latest_event(clf, scaler, feature_cols)
    atomic_append_csv(result, PRED_CSV_OUT)
    summary = dict(result)
    summary["active_shadow_release_unchanged_check"] = str(ACTIVE_RELEASE_LINK.resolve())
    summary["trading_enabled"] = False
    summary["broker_connected"] = False
    summary["paper_trading_enabled"] = False
    summary["shadow_research_only"] = True
    atomic_write_json(summary, SUMMARY_JSON_OUT)
    log(f"status={result.get('status')}  side_primary={result.get('side_primary')}  "
       f"ACT_probability={result.get('ACT_probability')}  decision={result.get('ACT_PASS_decision')}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["once", "loop"], default="once")
    ap.add_argument("--interval-sec", type=int, default=30)
    args = ap.parse_args()

    log(f"v4_histgb_entry_inference starting (mode={args.mode}) - SHADOW DISPLAY ONLY, NO EXECUTION")
    if args.mode == "once":
        run_once()
        return 0

    while True:
        try:
            run_once()
        except Exception as e:
            log(f"WARNING: scoring cycle failed ({e}) - daemon continues, will retry next cycle")
        time.sleep(args.interval_sec)


if __name__ == "__main__":
    sys.exit(main())
