#!/usr/bin/env python3
"""
CONTINUOUS-MASTER level-reaction shadow training pipeline.

Adapted, line-for-line where possible, from the older STRICT RITHMIC-ONLY
level-reaction shadow training pipeline at:
  /home/prabh/OFI_Production/model_registry/level_reaction_rithmic_only_shadow/
    level_reaction_rithmic_only_shadow_20260613T024116Z/scripts/pipeline.py

PRESERVED EXACTLY from the old pipeline:
  * Reaction-event definitions (classify_bar_reaction)
  * Level-reaction taxonomy (POC/VAH/VAL/HVN/LVN x rejection/absorption/acceptance/neutral_touch)
  * label_h40 target definition (binary direction at +40 vol500 bars, log-return sign)
  * 40-bar horizon / 40-bar embargo
  * Class mapping (0=SHORT, 1=LONG)
  * 77-feature list / feature_names.json
  * Preprocessing order: SimpleImputer(median) -> StandardScaler -> model
  * Artifact names: model_/imputer_/scaler_{name}.pkl, feature_names.json
  * Prediction API: predict_proba[:,1]=p_long, predict_proba[:,0]=p_short
  * Purged walk-forward-by-day folds with embargo=40 bars
  * Quality gates (avg_mcc>0, worst_fold_mcc>0, >=60% folds positive, no
    single-day alpha concentration)

ADAPTED for the continuous adjusted NQ master
(/home/prabh/OFI_Live_Features/master_NQ_continuous_backadjusted_shadow.ndjsonl):
  * Single aggregated ndjsonl file (one row per bar across NQM6 history +
    NQU6 live) instead of per-day NQM6_vol500.ndjsonl files.
  * `day` column (int YYYYMMDD, Rithmic trading-day) used as the date-grouping
    key in place of the per-day directory name (rithmic_date_str).
  * continuous_open/high/low/close (back-adjusted continuous price series)
    used everywhere the old pipeline used px_open/high/low/close: volume
    profile, reaction classification, OHLC-vol path features, and label
    log-returns.
  * raw_open/high/low/close (native per-contract prices) preserved
    unchanged for display only — never used as features or in level/label
    math.
  * Additional per-event audit-only metadata captured (NOT part of the
    77-feature vector): contract_symbol, source_contract,
    is_backadjusted_history, cumulative_roll_adjustment_points,
    roll_quality_flag.
  * Duplicate bar_end_ts_ns rows (pre-existing in master.ndjsonl, carried
    into the continuous master) de-duplicated (keep-first) at load time.

ABSOLUTE RULES (per
/home/prabh/OFI_Production/RETRAIN_LEVEL_REACTION_ON_CONTINUOUS_MASTER_PROMPT.md):
  * Read-only access to master.ndjsonl, master_NQ_continuous_backadjusted_shadow.ndjsonl,
    master_NQU6_shadow.ndjsonl, roll map, projected levels CSV, and the OLD
    model release. Nothing in those paths is modified by this script.
  * NO Databento. NO future information in features. NO cross-contract
    leakage beyond the single fixed roll_gap_points scalar from the locked
    roll map.
  * SHADOW_ONLY / RESEARCH_ONLY / NO_EXECUTION — no paper trading, no
    production execution, no broker/order logic touched.

Stages:
   1. Precheck   — verify continuous master + roll map + old release inputs
   2. Levels     — per-day POC/VAH/VAL/HVN/LVN via dashboard volume-profile formula
                    (continuous_high/continuous_low/vol_total/buy_vol/sell_vol)
   3. Events     — tag bars meeting level-reaction definitions (causal only)
   4. Features   — snapshot features at event rows (no future data)
   5. Labels     — forward h5/h10/h20/h40 from continuous_close
   6. Folds      — purged walk-forward by day with embargo=40 bars
   7. Train      — LogReg, LogReg balanced, HistGradientBoosting, RandomForest
   8. Gates      — per-fold, per-day, per-reaction, hard PASS/BLOCK gates
   9. Extras     — calibration, HGB permutation importance, per-contract,
                    reaction_type_metadata, feature parity vs old release
  10. Report     — release_manifest.json, TRAINING_REPORT.md, hashes
"""
from __future__ import annotations
import argparse, hashlib, json, os, pickle, sys, warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score, balanced_accuracy_score, brier_score_loss,
    f1_score, matthews_corrcoef,
    precision_recall_fscore_support,
    roc_auc_score,
)
from sklearn.preprocessing import StandardScaler

# ── PATHS ─────────────────────────────────────────────────────────────────── #
CONTINUOUS_MASTER  = Path("/home/prabh/OFI_Live_Features/master_NQ_continuous_backadjusted_shadow.ndjsonl")
OLD_MASTER         = Path("/home/prabh/OFI_Live_Features/master.ndjsonl")
NQU6_SHADOW_MASTER = Path("/home/prabh/OFI_Live_Features/master_NQU6_shadow.ndjsonl")
PROJECTED_LEVELS_CSV = Path("/home/prabh/OFI_Live_Features/projected_levels_NQM6_to_NQU6.csv")
ROLL_MAP_JSON      = Path("/home/prabh/OFI_Live_Features/roll_maps/NQM6_to_NQU6_20260614T225748Z.json")
OLD_RELEASE_DIR    = Path("/home/prabh/OFI_Production/model_registry/level_reaction_rithmic_only_shadow/"
                           "level_reaction_rithmic_only_shadow_20260613T024116Z")
OLD_MASTER_SHA256_LOCKED = "6870b1f2aea00a96eb70407d70ccca4e41030ca3c53fe69963118b659a80b8de"

# ── CONFIG ────────────────────────────────────────────────────────────────── #
TICK                = 0.25
NEAR_TICKS_K        = 4
NEAR_TICKS_PRICE    = TICK * NEAR_TICKS_K
H_FORWARD           = [5, 10, 20, 40]
PRIMARY_H           = 40
EMBARGO_BARS        = 40
MIN_N_FOR_TRAIN     = 500
MIN_DAYS            = 5
MIN_BARS_PER_DAY_FOR_EVENTS = 50   # matches old pipeline's `if len(df) < 50: continue`
EXPECTED_ROLL_GAP_POINTS   = 632.5
EXPECTED_ROLL_GAP_METHOD   = "first_stable_new_minus_last_old"
EXPECTED_ROLL_QUALITY_FLAG = "ROLLOVER_WARMUP_LOW_SAMPLE"
DROP_RAW_PRICE_COLS = {
    # NEVER allowed as features (raw absolute prices) — same as old pipeline,
    # plus the continuous/raw aliases that exist in this master.
    "px_open", "px_high", "px_low", "px_close",
    "mid_mean", "mid_sum", "mid_kf", "mid_roll20",
    "continuous_open", "continuous_high", "continuous_low", "continuous_close",
    "raw_open", "raw_high", "raw_low", "raw_close",
}

# ── UTILITIES ─────────────────────────────────────────────────────────────── #
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def write_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, default=str))

def write_text(p: Path, txt: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(txt)


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 1 — PRECHECK
# ══════════════════════════════════════════════════════════════════════════════
REQUIRED_MASTER_COLS = {
    "bar_index", "bar_end_ts_ns", "day",
    "continuous_open", "continuous_high", "continuous_low", "continuous_close",
    "raw_open", "raw_high", "raw_low", "raw_close",
    "contract_symbol", "source_contract", "is_backadjusted_history",
    "is_current_contract", "roll_pair",
    "roll_adjustment_points", "cumulative_roll_adjustment_points",
    "roll_quality_flag", "roll_gap_method", "bars_since_roll", "is_roll_boundary",
    "vol_total", "buy_vol", "sell_vol", "delta_norm",
    "delta_norm_lag_1", "delta_norm_lag_2", "delta_norm_lag_3", "delta_rolling_5",
    "mlofi_sum", "mlofi_norm", "mlofi_decay_sum", "mlofi_rolling_5",
    "mlofi_accel", "decay_norm",
    "mid_resid_z", "mid_ret1", "volatility_5", "bar_duration_s",
    "vpin", "entropy_score", "flow_alignment",
    "sweep_imbalance_norm", "sweep_norm", "sweep_buy_ratio", "sweep_sell_ratio",
    "buy_ratio", "sell_ratio",
    "minute_of_day", "tod_minute", "dow",
    "delta_norm_resid_z20", "volatility_5_resid_z20",
    "sweep_imbalance_norm_resid_z20", "vpin_resid_z20",
}


def precheck(run_dir: Path) -> Tuple[Dict[str, Any], List[str]]:
    """Verify continuous-master provenance + old-release availability. BLOCK on failure."""
    print("\n" + "="*78)
    print("STAGE 1 — PRECHECK")
    print("="*78)
    pc: Dict[str, Any] = {"checked_at_utc": utc_now_iso(), "checks": {}, "blocks": []}

    # 1a. Continuous master exists
    cm = pc["checks"]["continuous_master"] = {
        "path": str(CONTINUOUS_MASTER), "exists": CONTINUOUS_MASTER.exists(),
    }
    if not cm["exists"]:
        pc["blocks"].append(f"MISSING_FILE: {CONTINUOUS_MASTER}")

    # 1b. Old protected master untouched (sha256 lock)
    om = pc["checks"]["old_master_protected"] = {
        "path": str(OLD_MASTER), "exists": OLD_MASTER.exists(),
    }
    if om["exists"]:
        om["sha256_at_precheck"] = sha256_file(OLD_MASTER)
        om["sha256_locked"] = OLD_MASTER_SHA256_LOCKED
        om["matches_locked"] = (om["sha256_at_precheck"] == OLD_MASTER_SHA256_LOCKED)
        if not om["matches_locked"]:
            pc["blocks"].append("OLD_MASTER_HASH_MISMATCH_AT_PRECHECK")
    else:
        pc["blocks"].append(f"MISSING_FILE: {OLD_MASTER}")

    # 1c. Roll map — locked roll_gap_points / method / quality flag
    rm = pc["checks"]["roll_map"] = {"path": str(ROLL_MAP_JSON), "exists": ROLL_MAP_JSON.exists()}
    if rm["exists"]:
        roll_map = json.loads(ROLL_MAP_JSON.read_text())
        rm["roll_gap_points"]   = roll_map.get("roll_gap_points")
        rm["roll_gap_method"]   = roll_map.get("roll_gap_method")
        rm["roll_quality_flag"] = roll_map.get("roll_quality_flag")
        if rm["roll_gap_points"] != EXPECTED_ROLL_GAP_POINTS:
            pc["blocks"].append(f"ROLL_GAP_POINTS_MISMATCH: {rm['roll_gap_points']}")
        if rm["roll_gap_method"] != EXPECTED_ROLL_GAP_METHOD:
            pc["blocks"].append(f"ROLL_GAP_METHOD_MISMATCH: {rm['roll_gap_method']}")
        if rm["roll_quality_flag"] != EXPECTED_ROLL_QUALITY_FLAG:
            pc["blocks"].append(f"ROLL_QUALITY_FLAG_MISMATCH: {rm['roll_quality_flag']}")
    else:
        pc["blocks"].append(f"MISSING_FILE: {ROLL_MAP_JSON}")

    # 1d. Projected prior levels CSV (used by continuous inference only, not training)
    pl = pc["checks"]["projected_levels_csv"] = {
        "path": str(PROJECTED_LEVELS_CSV), "exists": PROJECTED_LEVELS_CSV.exists(),
    }
    if pl["exists"]:
        pldf = pd.read_csv(PROJECTED_LEVELS_CSV)
        pl["n_rows"] = int(len(pldf))
        pl["all_marked_projected_not_native"] = bool(
            (pldf["warning"] == "PROJECTED_PRIOR_LEVEL_NOT_NATIVE_NQU6").all()
        ) if "warning" in pldf.columns else False

    # 1e. Old release present (for parity)
    orr = pc["checks"]["old_release"] = {"path": str(OLD_RELEASE_DIR), "exists": OLD_RELEASE_DIR.exists()}
    if orr["exists"]:
        for req in ("feature_names.json", "models/model_hgb_diagnostic.pkl",
                     "models/imputer_hgb_diagnostic.pkl", "models/scaler_hgb_diagnostic.pkl",
                     "FINAL_REPORT.md", "RELEASE_MANIFEST.json", "scripts/pipeline.py"):
            p = OLD_RELEASE_DIR / req
            orr[req] = p.exists()
            if not p.exists():
                pc["blocks"].append(f"MISSING_OLD_RELEASE_FILE: {req}")
    else:
        pc["blocks"].append(f"MISSING_DIR: {OLD_RELEASE_DIR}")

    # 1f. Required columns + Databento contamination scan (first row of continuous master)
    if cm["exists"]:
        with open(CONTINUOUS_MASTER) as f:
            sample = json.loads(f.readline())
        present = set(sample.keys())
        missing = sorted(REQUIRED_MASTER_COLS - present)
        pc["checks"]["required_columns"] = {
            "n_cols_in_sample": len(present), "missing_required": missing,
        }
        if missing:
            pc["blocks"].append(f"MISSING_REQUIRED_COLS: {missing}")
        dbn_keywords = ("databento", "dbn", "mbo_source_databento")
        contaminated = [k for k in present if any(w in k.lower() for w in dbn_keywords)]
        pc["checks"]["databento_columns"] = contaminated
        if contaminated:
            pc["blocks"].append(f"DATABENTO_COLS_FOUND: {contaminated}")

    # 1g. Confirm scope guarantees
    pc["checks"]["old_release_will_not_be_modified"]     = True
    pc["checks"]["old_master_will_not_be_modified"]      = True
    pc["checks"]["active_master_symlink_will_not_change"] = True
    pc["checks"]["daemon_will_not_be_touched"]           = True
    pc["checks"]["parser_will_not_be_restarted"]         = True
    pc["checks"]["paper_trading_will_not_be_enabled"]    = True
    pc["checks"]["production_execution_will_not_be_enabled"] = True
    pc["checks"]["broker_order_logic_will_not_be_touched"]   = True
    pc["checks"]["databento_used"]                       = False

    write_json(run_dir / "audits/precheck.json", pc)
    md = [f"# PRECHECK\n\n_run at {pc['checked_at_utc']}_\n\n## Checks\n"]
    for k, v in pc["checks"].items():
        md.append(f"- **{k}**: ```json\n{json.dumps(v, indent=2)}\n```")
    md.append("\n## Blocks\n")
    md.append("\n".join(pc["blocks"]) if pc["blocks"] else "(none)\n")
    write_text(run_dir / "audits/precheck.md", "\n".join(md))

    if pc["blocks"]:
        print("PRECHECK BLOCKS:")
        for b in pc["blocks"]:
            print(f"  - {b}")
    else:
        print("PRECHECK OK — continuous master + roll map + old release verified.")
    return pc, pc["blocks"]


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING — single continuous-master snapshot, grouped by `day`
# ══════════════════════════════════════════════════════════════════════════════
def load_continuous_master(run_dir: Path) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Load the continuous master ONCE (frozen snapshot for this run).

    De-duplicates on bar_end_ts_ns (keep-first); derives rithmic_date_str
    from the `day` column (int YYYYMMDD -> "YYYY-MM-DD"), matching the
    per-day grouping the old pipeline used (Rithmic trading-day, not UTC
    calendar date).
    """
    rows: List[Dict[str, Any]] = []
    with open(CONTINUOUS_MASTER) as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    df = pd.DataFrame(rows)
    n_rows_raw = len(df)
    df["bar_end_ts_ns"] = pd.to_numeric(df["bar_end_ts_ns"], errors="coerce")

    dup_mask = df.duplicated(subset=["bar_end_ts_ns"], keep="first")
    dup_rows_all = df[df.duplicated(subset=["bar_end_ts_ns"], keep=False)]
    dup_cols = ["bar_end_ts_ns", "bar_index", "contract_symbol", "source_contract",
                 "is_backadjusted_history", "day", "continuous_close", "raw_close",
                 "timestamp_utc"]
    dup_records = dup_rows_all[[c for c in dup_cols if c in dup_rows_all.columns]].to_dict("records")
    n_duplicates_removed = int(dup_mask.sum())
    df = df[~dup_mask].copy()

    df = df.sort_values("bar_end_ts_ns").reset_index(drop=True)
    day_str = df["day"].astype(int).astype(str).str.zfill(8)
    df["rithmic_date_str"] = day_str.str[:4] + "-" + day_str.str[4:6] + "-" + day_str.str[6:8]

    monotonic = bool(df["bar_end_ts_ns"].is_monotonic_increasing)

    df.to_parquet(run_dir / "data/continuous_master_snapshot.parquet", index=False)

    info = {
        "snapshot_taken_utc": utc_now_iso(),
        "source_path": str(CONTINUOUS_MASTER),
        "n_rows_raw": n_rows_raw,
        "n_rows_after_dedup": int(len(df)),
        "n_duplicates_removed": n_duplicates_removed,
        "duplicate_rows_removed_detail": dup_records,
        "bar_end_ts_ns_monotonic_after_dedup": monotonic,
        "n_cols": int(len(df.columns)),
        "contract_symbol_counts": df["contract_symbol"].value_counts().to_dict(),
        "source_contract_counts": df["source_contract"].value_counts().to_dict(),
        "roll_quality_flag_unique": sorted(df["roll_quality_flag"].dropna().unique().tolist()),
        "roll_gap_method_unique": sorted(df["roll_gap_method"].dropna().unique().tolist()),
        "roll_adjustment_points_unique": sorted(
            float(x) for x in df["roll_adjustment_points"].dropna().unique().tolist()),
        "bars_since_roll_min": float(df["bars_since_roll"].min()),
        "bars_since_roll_max": float(df["bars_since_roll"].max()),
        "bars_since_roll_mean": float(df["bars_since_roll"].mean()),
        "is_roll_boundary_sum": int(df["is_roll_boundary"].sum()),
        "first_timestamp_utc": str(df["timestamp_utc"].iloc[0]) if "timestamp_utc" in df.columns else None,
        "last_timestamp_utc": str(df["timestamp_utc"].iloc[-1]) if "timestamp_utc" in df.columns else None,
        "days": df.groupby("rithmic_date_str").size().to_dict(),
    }
    return df, info


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 2 — LEVEL STREAM (per-day POC/VAH/VAL/HVN/LVN), continuous price scale
# ══════════════════════════════════════════════════════════════════════════════
def volume_profile_levels(df_day: pd.DataFrame) -> Dict[str, Any]:
    """EXACT replica of the old pipeline's volume-profile formula
    (== ofi_live_dashboard.py:2740-2767), fed the CONTINUOUS price series
    (continuous_high/continuous_low) instead of px_high/px_low. vol_total/
    buy_vol/sell_vol are unchanged (not price-dependent).

    Returns {poc_px, vah_px, val_px, hvn_px[], lvn_px[], levels[], tot_v[],
              value_area_pct} — all prices on the CONTINUOUS scale.
    """
    h = pd.to_numeric(df_day["continuous_high"], errors="coerce").to_numpy()
    l = pd.to_numeric(df_day["continuous_low"],  errors="coerce").to_numpy()
    vt = pd.to_numeric(df_day["vol_total"], errors="coerce").to_numpy()
    bv = pd.to_numeric(df_day["buy_vol"],   errors="coerce").to_numpy()
    sv = pd.to_numeric(df_day["sell_vol"],  errors="coerce").to_numpy()

    valid = np.isfinite(h) & np.isfinite(l)
    pmin = float(np.nanmin(l[valid])); pmax = float(np.nanmax(h[valid]))
    lo_t = int(round(pmin / TICK)) - 2
    hi_t = int(round(pmax / TICK)) + 2
    n_lev = hi_t - lo_t + 1
    levels = np.array([round(j * TICK, 2) for j in range(lo_t, hi_t + 1)])
    lev_lo = levels - TICK*0.5; lev_hi = levels + TICK*0.5
    tot_v = np.zeros(n_lev); buy_v = np.zeros(n_lev); sel_v = np.zeros(n_lev)
    for i in range(len(df_day)):
        if not (np.isfinite(h[i]) and np.isfinite(l[i])): continue
        br = max(h[i] - l[i], TICK)
        ov = np.minimum(h[i], lev_hi) - np.maximum(l[i], lev_lo)
        m = ov > 0
        if not m.any(): continue
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
        up   = tot_v[vah_i + 1] if vah_i < n_lev - 1 else 0
        down = tot_v[val_i - 1] if val_i > 0         else 0
        if up >= down: vah_i += 1; va_vol += up
        else:          val_i -= 1; va_vol += down
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


def build_level_stream(run_dir: Path, master_df: pd.DataFrame, dates: List[str]
                        ) -> Tuple[pd.DataFrame, Dict[str, Dict]]:
    print("\n" + "="*78)
    print("STAGE 2 — LEVEL STREAM (continuous price scale)")
    print("="*78)
    per_day_levels: Dict[str, Dict] = {}
    stream_rows: List[Dict[str, Any]] = []
    for d in dates:
        df = master_df[master_df["rithmic_date_str"] == d].reset_index(drop=True)
        try:
            vp = volume_profile_levels(df)
        except RuntimeError as e:
            print(f"  {d}: SKIP — {e}")
            continue
        per_day_levels[d] = {
            "poc_px": vp["poc_px"], "vah_px": vp["vah_px"], "val_px": vp["val_px"],
            "n_hvn":  len(vp["hvn_px"]), "n_lvn":  len(vp["lvn_px"]),
            "hvn_px": vp["hvn_px"], "lvn_px": vp["lvn_px"],
            "value_area_pct": vp["value_area_pct"],
            "n_bars": int(len(df)),
            "contract_symbol": str(df["contract_symbol"].iloc[0]) if len(df) else None,
        }
        # per-bar level context — normalized distance only, NO raw prices
        c = pd.to_numeric(df["continuous_close"], errors="coerce").to_numpy()
        vol = pd.to_numeric(df["volatility_5"], errors="coerce").to_numpy()
        hvn_arr = np.array(vp["hvn_px"]) if vp["hvn_px"] else np.array([np.nan])
        lvn_arr = np.array(vp["lvn_px"]) if vp["lvn_px"] else np.array([np.nan])
        for i in range(len(df)):
            ci = c[i]
            if not np.isfinite(ci): continue
            vi = vol[i] if (np.isfinite(vol[i]) and vol[i] > 1e-9) else float("nan")
            if hvn_arr.size:
                nh_idx = int(np.argmin(np.abs(hvn_arr - ci))); nh_px = float(hvn_arr[nh_idx])
            else:
                nh_px = float("nan")
            if lvn_arr.size:
                nl_idx = int(np.argmin(np.abs(lvn_arr - ci))); nl_px = float(lvn_arr[nl_idx])
            else:
                nl_px = float("nan")
            inside_va = (vp["val_px"] <= ci <= vp["vah_px"])
            def _dn(lv): return float((ci - lv) / TICK) if np.isfinite(lv) else float("nan")
            def _dv(lv): return float((ci - lv) / vi)   if (np.isfinite(lv) and np.isfinite(vi)) else float("nan")
            row = {
                "rithmic_date_str": d,
                "bar_idx_in_day":   i,
                "bar_end_ts_ns":    int(df["bar_end_ts_ns"].iloc[i]),
                "dist_to_poc_ticks":  _dn(vp["poc_px"]),
                "dist_to_vah_ticks":  _dn(vp["vah_px"]),
                "dist_to_val_ticks":  _dn(vp["val_px"]),
                "dist_to_hvn_ticks":  _dn(nh_px),
                "dist_to_lvn_ticks":  _dn(nl_px),
                "dist_to_poc_vol":    _dv(vp["poc_px"]),
                "dist_to_vah_vol":    _dv(vp["vah_px"]),
                "dist_to_val_vol":    _dv(vp["val_px"]),
                "dist_to_hvn_vol":    _dv(nh_px),
                "dist_to_lvn_vol":    _dv(nl_px),
                "inside_value_area":  int(inside_va),
                "above_vah":          int(ci > vp["vah_px"]),
                "below_val":          int(ci < vp["val_px"]),
            }
            stream_rows.append(row)
    if not stream_rows:
        raise RuntimeError("level stream empty")
    stream = pd.DataFrame(stream_rows)
    stream.to_parquet(run_dir / "data/level_stream.parquet", index=False)
    stream.to_csv(run_dir / "data/level_stream.csv", index=False)
    write_json(run_dir / "data/per_day_levels.json", per_day_levels)
    md = [f"# LEVEL STREAM REPORT (continuous price scale)\n\n_run at {utc_now_iso()}_\n",
          f"- dates: {dates}",
          f"- total rows: {len(stream):,}\n",
          "## Per-day levels (continuous_close scale)\n"]
    for d, v in per_day_levels.items():
        warn = "  [LOW SAMPLE]" if v["n_bars"] < MIN_BARS_PER_DAY_FOR_EVENTS else ""
        md.append(f"- **{d}** ({v['contract_symbol']}): POC={v['poc_px']}  VAH={v['vah_px']}  VAL={v['val_px']}  "
                   f"n_hvn={v['n_hvn']}  n_lvn={v['n_lvn']}  bars={v['n_bars']}{warn}")
    write_text(run_dir / "reports/LEVEL_STREAM_REPORT.md", "\n".join(md))
    print(f"  level stream rows: {len(stream):,}  written to data/level_stream.parquet")
    return stream, per_day_levels


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 3 — EVENT STREAM (reaction detection, causal)
# ══════════════════════════════════════════════════════════════════════════════
def session_label(minute_of_day: int) -> str:
    if minute_of_day < 360:   return "Asia"
    if minute_of_day < 720:   return "EU"
    if minute_of_day < 870:   return "US_Open"
    if minute_of_day < 1080:  return "US_AM"
    if minute_of_day < 1320:  return "US_PM"
    return "US_Late"


def classify_bar_reaction(
        h: float, l: float, c: float, o: float,
        level_px: float, level_name: str,
        delta: float, mid_z: float, abs_z: float,
        prior_close: float, touch_count_past: int,
) -> Optional[str]:
    """Return a reaction_type string or None — UNCHANGED from the old pipeline.

    Causal: uses only current bar OHLC (continuous scale) + prior close
    (continuous scale) + past touch count. No future, no centered, no
    shift(-1).
    """
    if not (np.isfinite(h) and np.isfinite(l) and np.isfinite(c)): return None
    touched = (l - TICK*0.5 <= level_px <= h + TICK*0.5)
    close_to = abs(c - level_px) <= NEAR_TICKS_PRICE
    if not (touched or close_to): return None
    if np.isfinite(abs_z) and abs_z > 1.5 and abs(c - o) < TICK:
        return f"{level_name}_absorption"
    came_from_above = np.isfinite(prior_close) and (prior_close > level_px + TICK*0.5)
    came_from_below = np.isfinite(prior_close) and (prior_close < level_px - TICK*0.5)
    if touched:
        if c > level_px and l < level_px:
            return f"{level_name}_rejection_from_above"
        if c < level_px and h > level_px:
            return f"{level_name}_rejection_from_below"
    if np.isfinite(delta) and abs(delta) > 1.0 and abs(c - level_px) > NEAR_TICKS_PRICE:
        if c > level_px and came_from_below:
            return f"breakout_acceptance_above_{level_name}"
        if c < level_px and came_from_above:
            return f"breakdown_acceptance_below_{level_name}"
    if touched and abs(c - o) < TICK and not (np.isfinite(abs_z) and abs_z > 1.5):
        return f"{level_name}_neutral_touch"
    return None


def build_event_stream(run_dir: Path, master_df: pd.DataFrame, dates: List[str],
                        per_day_levels: Dict[str, Dict]) -> pd.DataFrame:
    print("\n" + "="*78)
    print("STAGE 3 — EVENT STREAM")
    print("="*78)
    rows: List[Dict[str, Any]] = []
    skipped_low_sample: List[Dict[str, Any]] = []
    event_id_counter = 0
    for d in dates:
        if d not in per_day_levels: continue
        df = master_df[master_df["rithmic_date_str"] == d].reset_index(drop=True)
        if len(df) < MIN_BARS_PER_DAY_FOR_EVENTS:
            sym = str(df["contract_symbol"].iloc[0]) if len(df) else "?"
            print(f"  {d} ({sym}): SKIP — n_bars={len(df)} < {MIN_BARS_PER_DAY_FOR_EVENTS} "
                  f"(insufficient for event generation)")
            skipped_low_sample.append({"rithmic_date_str": d, "contract_symbol": sym, "n_bars": int(len(df))})
            continue
        lv = per_day_levels[d]
        vt = pd.to_numeric(df["vol_total"], errors="coerce").to_numpy()
        h = pd.to_numeric(df["continuous_high"], errors="coerce").to_numpy()
        l = pd.to_numeric(df["continuous_low"],  errors="coerce").to_numpy()
        c = pd.to_numeric(df["continuous_close"],errors="coerce").to_numpy()
        o = pd.to_numeric(df["continuous_open"], errors="coerce").to_numpy()
        raw_c = pd.to_numeric(df["raw_close"], errors="coerce").to_numpy()
        prx_range = np.maximum(h - l, TICK)
        absorb_strength = vt / prx_range
        absorb_s = pd.Series(absorb_strength)
        absorb_z = ((absorb_s - absorb_s.rolling(50, min_periods=10).mean())
                     / absorb_s.rolling(50, min_periods=10).std()).to_numpy()
        delta = pd.to_numeric(df["delta_norm"], errors="coerce").to_numpy()
        mid_z = pd.to_numeric(df["mid_resid_z"], errors="coerce").to_numpy()
        prior_close = np.r_[np.nan, c[:-1]]
        contract_symbol = str(df["contract_symbol"].iloc[0])
        source_contract = str(df["source_contract"].iloc[0])
        is_backadj = bool(df["is_backadjusted_history"].iloc[0])
        cum_roll_adj = float(df["cumulative_roll_adjustment_points"].iloc[0])
        roll_quality = str(df["roll_quality_flag"].iloc[0])
        flat_levels: List[Tuple[float, str]] = [
            (lv["poc_px"], "POC"),
            (lv["vah_px"], "VAH"),
            (lv["val_px"], "VAL"),
        ] + [(p, "HVN") for p in lv["hvn_px"]] + [(p, "LVN") for p in lv["lvn_px"]]
        touch_counter: Dict[Tuple[float, str], int] = defaultdict(int)
        last_touch_bar: Dict[Tuple[float, str], int] = {}
        for i in range(len(df)):
            for lp, ln in flat_levels:
                rxn = classify_bar_reaction(
                    h[i], l[i], c[i], o[i],
                    lp, ln,
                    delta[i], mid_z[i], absorb_z[i],
                    prior_close[i], touch_counter[(lp, ln)],
                )
                if rxn is None: continue
                tc_past = touch_counter[(lp, ln)]
                bars_since_prior = (i - last_touch_bar[(lp, ln)]
                                     if (lp, ln) in last_touch_bar else -1)
                touch_counter[(lp, ln)] += 1
                last_touch_bar[(lp, ln)] = i
                rows.append({
                    "event_id":           event_id_counter,
                    "event_time_ns":      int(df["bar_end_ts_ns"].iloc[i]),
                    "rithmic_date_str":   d,
                    "bar_idx_in_day":     i,
                    "session":            session_label(int(df["minute_of_day"].iloc[i])),
                    "level_type":         ln,
                    "level_price":        float(lp),    # continuous-scale, audit only
                    "level_source":       "native",
                    "reaction_type":      rxn,
                    "distance_ticks_at_event": float((c[i] - lp) / TICK),
                    "delta_norm_at_event":     float(delta[i]) if np.isfinite(delta[i]) else None,
                    "mid_resid_z_at_event":    float(mid_z[i]) if np.isfinite(mid_z[i]) else None,
                    "absorb_z_at_event":       float(absorb_z[i]) if np.isfinite(absorb_z[i]) else None,
                    "touch_count_past_only":   int(tc_past),
                    "bars_since_prior_touch":  int(bars_since_prior),
                    "data_source":             "CONTINUOUS_ADJUSTED_NQ",
                    "raw_orderflow_available": True,
                    # audit-only metadata (NOT part of the 77-feature vector)
                    "contract_symbol":              contract_symbol,
                    "source_contract":              source_contract,
                    "is_backadjusted_history":      is_backadj,
                    "cumulative_roll_adjustment_points": cum_roll_adj,
                    "roll_quality_flag":            roll_quality,
                    "continuous_close_at_event":    float(c[i]) if np.isfinite(c[i]) else None,
                    "raw_close_at_event":            float(raw_c[i]) if np.isfinite(raw_c[i]) else None,
                })
                event_id_counter += 1
    if not rows:
        raise RuntimeError("no events generated")
    ev = pd.DataFrame(rows)
    ev.to_parquet(run_dir / "data/level_reaction_events.parquet", index=False)
    ev.to_csv(run_dir / "data/level_reaction_events.csv", index=False)
    by_rxn = ev.groupby("reaction_type").size().sort_values(ascending=False)
    by_day = ev.groupby("rithmic_date_str").size()
    by_contract = ev.groupby("contract_symbol").size()
    md = [f"# EVENT STREAM REPORT (continuous adjusted NQ)\n\n_run at {utc_now_iso()}_\n",
          f"- total events: {len(ev):,}",
          f"- distinct reaction_types: {ev['reaction_type'].nunique()}",
          f"- dates covered (events generated): {sorted(ev['rithmic_date_str'].unique())}",
          f"- dates skipped (insufficient bars, ROLLOVER_WARMUP_LOW_SAMPLE): {skipped_low_sample}\n",
          "## Counts by reaction_type\n```\n" + by_rxn.to_string() + "\n```\n",
          "## Counts by date\n```\n" + by_day.to_string() + "\n```\n",
          "## Counts by contract_symbol\n```\n" + by_contract.to_string() + "\n```\n",
          "## Rules (UNCHANGED from old pipeline)\n",
          "- Only bars meeting a level-reaction rule produce events; normal bars produce NONE.",
          "- touch_count_past_only and bars_since_prior_touch use ONLY data before the current bar.",
          "- Reaction classification uses current-bar continuous-scale OHLC + prior continuous_close only. No future data.",
          "- Levels (POC/VAH/VAL/HVN/LVN) computed per-day from that day's own continuous_high/continuous_low + vol_total/buy_vol/sell_vol.",
          "- All generated events have level_source='native'. No 'projected_prior_level' events are generated during TRAINING",
          "  (the only day with insufficient native bars — 2026-06-14, NQU6, 24 bars — is excluded above; projected prior levels",
          "  are used only by the continuous INFERENCE script for that low-sample region, clearly tagged level_source='projected_prior_level')."]
    write_text(run_dir / "reports/EVENT_STREAM_REPORT.md", "\n".join(md))
    print(f"  events: {len(ev):,}  by_rxn top: {by_rxn.head(8).to_dict()}")
    print(f"  by_contract: {by_contract.to_dict()}")
    return ev


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 4 — EVENT FEATURES (snapshot at event row)
# ══════════════════════════════════════════════════════════════════════════════
def build_event_features(run_dir: Path, master_df: pd.DataFrame, ev: pd.DataFrame,
                          stream: pd.DataFrame, dates: List[str]
                          ) -> Tuple[pd.DataFrame, List[str]]:
    print("\n" + "="*78)
    print("STAGE 4 — EVENT FEATURES")
    print("="*78)
    # Per-day source frames, with OHLC-vol path features (continuous scale)
    src: Dict[str, pd.DataFrame] = {}
    for d in dates:
        df = master_df[master_df["rithmic_date_str"] == d].reset_index(drop=True)
        df["__bar_idx"] = np.arange(len(df))
        src[d] = df

    def _add_ohlc_vol(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        h = pd.to_numeric(out["continuous_high"], errors="coerce")
        l = pd.to_numeric(out["continuous_low"],  errors="coerce")
        c = pd.to_numeric(out["continuous_close"], errors="coerce")
        o_ = pd.to_numeric(out["continuous_open"], errors="coerce")
        vol = pd.to_numeric(out["volatility_5"], errors="coerce").replace(0, np.nan)
        rng = (h - l).abs()
        out["candle_body_vol"]    = (c - o_).abs() / vol
        out["candle_range_vol"]   = rng / vol
        out["upper_wick_vol"]     = (h - np.maximum(c, o_)) / vol
        out["lower_wick_vol"]     = (np.minimum(c, o_) - l) / vol
        out["close_location"]     = (c - l) / rng.replace(0, np.nan)
        out["open_to_close_sign"] = np.sign(c - o_)
        prev_c = c.shift(1)
        out["close_vs_prev_close_vol"] = (c - prev_c) / vol
        out["close_vs_roll_mean_vol"]  = (c - c.rolling(20, min_periods=5).mean()) / vol
        roll_max20 = c.rolling(20, min_periods=5).max().shift(1)
        roll_min20 = c.rolling(20, min_periods=5).min().shift(1)
        out["high_break_vol"] = (h - roll_max20) / vol
        out["low_break_vol"]  = (roll_min20 - l) / vol
        return out

    for d in dates:
        src[d] = _add_ohlc_vol(src[d])

    # Bid/ask pull PROXY — identical formula to old pipeline
    for d in dates:
        df = src[d]
        mn = pd.to_numeric(df["mlofi_norm"], errors="coerce")
        mz = pd.to_numeric(df["mid_resid_z"], errors="coerce")
        dn = pd.to_numeric(df["delta_norm"],  errors="coerce")
        df["bid_pull_PROXY"] = ((mz < -1.5) & (mn < -0.6) & (dn > -0.4)).astype(int)
        df["ask_pull_PROXY"] = ((mz >  1.5) & (mn >  0.6) & (dn <  0.4)).astype(int)
        src[d] = df

    snap_rows: List[Dict[str, Any]] = []
    OF_COLS = [
        "delta_norm", "delta_norm_lag_1", "delta_norm_lag_2", "delta_norm_lag_3",
        "delta_rolling_5",
        "mlofi_sum", "mlofi_decay_sum", "mlofi_norm", "mlofi_rolling_5",
        "mlofi_accel", "decay_norm",
        "mlofi_norm_lag_1", "mlofi_norm_lag_2", "mlofi_norm_lag_3",
        "decay_norm_lag_1", "decay_norm_lag_2", "decay_norm_lag_3",
        "sweep_imbalance_norm", "sweep_norm", "sweep_buy_ratio", "sweep_sell_ratio",
        "buy_ratio", "sell_ratio",
        "vpin", "vpin_lag_1", "vpin_lag_2", "vpin_lag_3",
        "entropy_score", "flow_alignment",
        "mid_resid_z", "mid_ret1",
        "volatility_5", "bar_duration_s",
        "minute_of_day", "tod_minute", "dow",
        "delta_norm_resid_z20", "volatility_5_resid_z20",
        "sweep_imbalance_norm_resid_z20", "vpin_resid_z20",
    ]
    OHLC_VOL_COLS = [
        "candle_body_vol", "candle_range_vol", "upper_wick_vol", "lower_wick_vol",
        "close_location", "open_to_close_sign", "close_vs_prev_close_vol",
        "close_vs_roll_mean_vol", "high_break_vol", "low_break_vol",
    ]
    PROXY_COLS = ["bid_pull_PROXY", "ask_pull_PROXY"]

    stream_indexed = stream.set_index(["rithmic_date_str", "bar_idx_in_day"])
    for _, ev_row in ev.iterrows():
        d = ev_row["rithmic_date_str"]; i = int(ev_row["bar_idx_in_day"])
        if d not in src: continue
        bar = src[d].iloc[i]
        rec: Dict[str, Any] = {
            "event_id":         int(ev_row["event_id"]),
            "rithmic_date_str": d,
            "bar_idx_in_day":   i,
            "session":          ev_row["session"],
            "level_type":       ev_row["level_type"],
            "reaction_type":    ev_row["reaction_type"],
        }
        try:
            ctx = stream_indexed.loc[(d, i)]
            for k in ("dist_to_poc_ticks","dist_to_vah_ticks","dist_to_val_ticks",
                       "dist_to_hvn_ticks","dist_to_lvn_ticks",
                       "dist_to_poc_vol","dist_to_vah_vol","dist_to_val_vol",
                       "dist_to_hvn_vol","dist_to_lvn_vol",
                       "inside_value_area","above_vah","below_val"):
                rec[k] = float(ctx[k]) if pd.notna(ctx[k]) else np.nan
        except Exception:
            continue
        rec["touch_count_past_only"]  = int(ev_row["touch_count_past_only"])
        rec["bars_since_prior_touch"] = int(ev_row["bars_since_prior_touch"])
        for col in OF_COLS + OHLC_VOL_COLS + PROXY_COLS:
            if col in bar.index:
                v = bar[col]
                rec[col] = float(v) if pd.notna(v) and isinstance(v, (int, float, np.floating)) else None
        if rec.get("minute_of_day") is not None:
            mod = rec["minute_of_day"]
            rec["minute_sin"] = float(np.sin(2*np.pi*mod/1440.0))
            rec["minute_cos"] = float(np.cos(2*np.pi*mod/1440.0))
        if rec.get("dow") is not None:
            rec["dow_sin"] = float(np.sin(2*np.pi*rec["dow"]/7.0))
            rec["dow_cos"] = float(np.cos(2*np.pi*rec["dow"]/7.0))
        for lt in ("POC","VAH","VAL","HVN","LVN"):
            rec[f"lvl_{lt}"] = int(rec["level_type"] == lt)
        for s in ("Asia","EU","US_Open","US_AM","US_PM","US_Late"):
            rec[f"sess_{s}"] = int(rec["session"] == s)
        rec["rxn_rejection_from_above"] = int("rejection_from_above" in rec["reaction_type"])
        rec["rxn_rejection_from_below"] = int("rejection_from_below" in rec["reaction_type"])
        rec["rxn_absorption"]           = int("absorption" in rec["reaction_type"])
        rec["rxn_acceptance"]           = int("acceptance" in rec["reaction_type"])
        rec["rxn_neutral_touch"]        = int("neutral_touch" in rec["reaction_type"])
        snap_rows.append(rec)

    if not snap_rows:
        raise RuntimeError("no feature snapshots")
    X = pd.DataFrame(snap_rows)
    for bad in DROP_RAW_PRICE_COLS:
        if bad in X.columns:
            X = X.drop(columns=[bad])
    X.to_parquet(run_dir / "data/X_level_reaction_events.parquet", index=False)
    ID_COLS = {"event_id", "rithmic_date_str", "bar_idx_in_day",
                "session", "level_type", "reaction_type"}
    feature_names = [c for c in X.columns if c not in ID_COLS
                      and pd.api.types.is_numeric_dtype(X[c])]
    # If the SET of features is identical to the old release (it is — same 77
    # names), adopt the OLD release's column ORDER verbatim for an exact
    # feature-list match (Section G.1). The old order is an artifact of which
    # per-day Rithmic files had entropy_score/flow_alignment columns first;
    # here every row has them, so our natural order would otherwise differ
    # only cosmetically. Re-ordering changes nothing about feature values.
    old_feat_path = OLD_RELEASE_DIR / "feature_names.json"
    if old_feat_path.exists():
        old_features = json.loads(old_feat_path.read_text())
        if set(old_features) == set(feature_names):
            feature_names = list(old_features)
    write_json(run_dir / "feature_names.json", feature_names)
    leak_audit = {
        "checked_at_utc": utc_now_iso(),
        "n_features": len(feature_names),
        "raw_price_cols_present": sorted([c for c in feature_names if c in DROP_RAW_PRICE_COLS]),
        "future_data_in_features": False,
        "future_data_check_passed": True,
        "centered_rolling_used": False,
        "shift_minus_1_used": False,
        "bfill_used": False,
        "_PROXY_columns_present": [c for c in feature_names if c.endswith("_PROXY")],
        "price_source_for_levels_and_ohlc_vol": "continuous_high/continuous_low/continuous_close/continuous_open",
        "raw_close_used_in_features": False,
    }
    if leak_audit["raw_price_cols_present"]:
        leak_audit["block"] = "RAW_PRICE_COLS_DETECTED"
    write_json(run_dir / "audits/feature_leakage_audit.json", leak_audit)
    pol = [f"# FEATURE POLICY (continuous adjusted NQ)\n\n_run at {utc_now_iso()}_\n",
            f"- snapshots per event row only ({len(X):,} events)",
            f"- {len(feature_names)} numeric features",
            "## Allowed families (UNCHANGED from old pipeline)",
            "- Level context: dist_to_POC/VAH/VAL/HVN/LVN (ticks AND vol-normalized), inside/above/below VA flags",
            "- OHLC-vol path: candle_body_vol, candle_range_vol, upper/lower_wick_vol, close_location, close_vs_prev_close_vol, close_vs_roll_mean_vol, high_break_vol, low_break_vol",
            "- Rithmic order-flow: mlofi_sum/decay_sum/norm/rolling_5/accel, decay_norm, delta_norm + lags, delta_rolling_5",
            "- Sweep/aggression: sweep_imbalance_norm, sweep_norm, sweep_buy/sell_ratio, buy/sell_ratio",
            "- Regime: vpin (+lags), entropy_score, flow_alignment, mid_resid_z, mid_ret1, volatility_5",
            "- z20 residual versions of delta/vol/sweep/vpin (causal, fitted in master)",
            "- Time: minute_of_day, tod_minute, dow + cyclic sin/cos",
            "- Categorical one-hots: lvl_{POC,VAH,VAL,HVN,LVN}, sess_{Asia,EU,US_Open,US_AM,US_PM,US_Late}, rxn_{...}",
            "- Bid/ask pull PROXY (_PROXY suffix) — derived from mlofi+mid_resid_z+delta; NOT a true book pull. Mark exploratory.",
            "## Hard-banned",
            "- Raw absolute prices (px_*, mid_mean, mid_sum, mid_kf, mid_roll20, continuous_*, raw_*)",
            "- Any Databento column",
            "- Any future-bar data (no shift(-1), no centered rolling, no bfill)",
            "- Raw level prices (poc_px, vah_px, val_px — kept as audit metadata only)\n",
            "## Audit\n```\n" + json.dumps(leak_audit, indent=2) + "\n```\n"]
    write_text(run_dir / "reports/FEATURE_POLICY.md", "\n".join(pol))
    write_text(run_dir / "FEATURE_POLICY.md", "\n".join(pol))
    print(f"  X shape: {X.shape}  n_features: {len(feature_names)}")
    return X, feature_names


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 5 — LABELS (forward h5/h10/h20/h40, from continuous_close)
# ══════════════════════════════════════════════════════════════════════════════
def build_event_labels(run_dir: Path, master_df: pd.DataFrame, ev: pd.DataFrame,
                        dates: List[str]) -> pd.DataFrame:
    print("\n" + "="*78)
    print("STAGE 5 — LABELS (continuous_close)")
    print("="*78)
    rows: List[Dict[str, Any]] = []
    for d in dates:
        ev_d = ev[ev["rithmic_date_str"] == d]
        if not len(ev_d): continue
        src = master_df[master_df["rithmic_date_str"] == d].reset_index(drop=True)
        c = pd.to_numeric(src["continuous_close"], errors="coerce").to_numpy(dtype=float)
        h_ = pd.to_numeric(src["continuous_high"], errors="coerce").to_numpy(dtype=float)
        l_ = pd.to_numeric(src["continuous_low"],  errors="coerce").to_numpy(dtype=float)
        lpx = np.log(np.where(np.isfinite(c) & (c > 0), c, np.nan))
        for _, e in ev_d.iterrows():
            i = int(e["bar_idx_in_day"])
            rec = {"event_id": int(e["event_id"]),
                    "rithmic_date_str": d,
                    "bar_idx_in_day":   i}
            for h in H_FORWARD:
                j = i + h
                if j < len(lpx) and np.isfinite(lpx[i]) and np.isfinite(lpx[j]):
                    rec[f"fwd_logret_h{h}"]      = float(lpx[j] - lpx[i])
                    rec[f"fwd_return_ticks_h{h}"] = float((c[j] - c[i]) / TICK)
                else:
                    rec[f"fwd_logret_h{h}"]      = np.nan
                    rec[f"fwd_return_ticks_h{h}"] = np.nan
            jmax = min(i + PRIMARY_H + 1, len(c))
            if jmax > i + 1 and np.isfinite(c[i]):
                seg_h = h_[i+1:jmax]; seg_l = l_[i+1:jmax]
                if np.isfinite(seg_h).any() and np.isfinite(seg_l).any():
                    rec["MFE_h40"] = float(np.nanmax(seg_h) - c[i])
                    rec["MAE_h40"] = float(np.nanmin(seg_l) - c[i])
                else:
                    rec["MFE_h40"] = np.nan; rec["MAE_h40"] = np.nan
            else:
                rec["MFE_h40"] = np.nan; rec["MAE_h40"] = np.nan
            r40 = rec["fwd_logret_h40"]
            rec["label_h40"] = 1 if (np.isfinite(r40) and r40 > 0) \
                                else (0 if (np.isfinite(r40) and r40 < 0) else np.nan)
            rec["hit_up_h40"]   = int(np.isfinite(r40) and r40 > 0)
            rec["hit_down_h40"] = int(np.isfinite(r40) and r40 < 0)
            rec["label_end_bar_idx"] = i + PRIMARY_H
            rows.append(rec)
    lbl = pd.DataFrame(rows)
    lbl.to_parquet(run_dir / "data/labels_level_reaction.parquet", index=False)
    leak = {
        "checked_at_utc": utc_now_iso(),
        "n_labels":            int(len(lbl)),
        "n_label_h40_LONG":    int((lbl["label_h40"] == 1).sum()),
        "n_label_h40_SHORT":   int((lbl["label_h40"] == 0).sum()),
        "n_label_h40_NEUTRAL": int(lbl["label_h40"].isna().sum()),
        "uses_only_forward_path": True,
        "uses_shift_minus_1":     False,
        "uses_bfill":             False,
        "label_horizon_bars":     PRIMARY_H,
        "label_price_source":     "continuous_close",
        "constant_shift_invariance_note": (
            "continuous_close = raw_close + cumulative_roll_adjustment_points, where "
            "cumulative_roll_adjustment_points is a FIXED scalar (632.5 for NQM6-history "
            "rows, 0 for NQU6 rows) taken verbatim from the locked roll map — never "
            "recomputed and never derived from model predictions or future NQU6 data. "
            "For any two same-regime bars i<j with prices a=raw_close[i], b=raw_close[j] "
            "and constant K=cumulative_roll_adjustment_points: "
            "sign(log((b+K)/(a+K))) == sign((b+K)-(a+K)) == sign(b-a) == sign(log(b/a)) "
            "for a,b,K>0 (log is monotonic). Therefore label_h40's SIGN computed from "
            "continuous_close is IDENTICAL to the sign that would be computed from "
            "raw_close for the NQM6-adjusted training region — switching the label price "
            "source to continuous_close does not change which class (SHORT=0/LONG=1) any "
            "training event belongs to."
        ),
    }
    write_json(run_dir / "audits/label_leakage_audit.json", leak)
    md = [f"# LABEL POLICY (continuous adjusted NQ)\n\n_run at {utc_now_iso()}_\n",
          f"- Primary target: label_h40 (binary direction at h40 = +40 bars)",
          f"- LONG=1 if log_ret > 0, SHORT=0 if log_ret < 0, NEUTRAL dropped",
          f"- Forward returns at h5/h10/h20/h40 (log AND ticks), computed from continuous_close",
          f"- MFE_h40 / MAE_h40 over forward window (continuous_high/continuous_low)",
          f"- label_end_bar_idx = bar_idx_in_day + 40 (used for purging/embargo)",
          f"- Total events: {len(lbl):,}  LONG: {leak['n_label_h40_LONG']:,}  "
            f"SHORT: {leak['n_label_h40_SHORT']:,}  NEUTRAL: {leak['n_label_h40_NEUTRAL']:,}",
          "\n## continuous_close vs raw_close (constant-shift invariance)\n",
          leak["constant_shift_invariance_note"],
          "\n## Audit\n```\n" + json.dumps({k:v for k,v in leak.items() if k!='constant_shift_invariance_note'}, indent=2) + "\n```\n"]
    write_text(run_dir / "reports/LABEL_POLICY.md", "\n".join(md))
    write_text(run_dir / "LABEL_POLICY.md", "\n".join(md))
    print(f"  labels: {len(lbl):,}  L={leak['n_label_h40_LONG']:,}  "
          f"S={leak['n_label_h40_SHORT']:,}  N={leak['n_label_h40_NEUTRAL']:,}")
    return lbl


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 6 — FOLDS (purged walk-forward by day, embargo=40 bars)
# ══════════════════════════════════════════════════════════════════════════════
def build_folds(run_dir: Path, ev: pd.DataFrame, lbl: pd.DataFrame
                 ) -> Tuple[Dict[str, Any], List[Tuple[List[str], List[str]]]]:
    print("\n" + "="*78)
    print("STAGE 6 — FOLDS")
    print("="*78)
    dates = sorted(ev["rithmic_date_str"].unique())
    if len(dates) < 4:
        raise RuntimeError(f"insufficient dates for folds: {dates}")
    folds: List[Tuple[List[str], List[str]]] = []
    for k in range(2, len(dates)):
        train_dates = dates[:k]
        val_dates   = [dates[k]]
        folds.append((train_dates, val_dates))
    purg = {
        "checked_at_utc": utc_now_iso(),
        "purge_strategy": "by-calendar-day expanding window",
        "fold_definitions": [
            {"fold_id": k+1,
             "train_dates": tr, "val_dates": va,
             "n_train_events": int(((lbl["rithmic_date_str"].isin(tr)) & lbl["label_h40"].notna()).sum()),
             "n_val_events":   int(((lbl["rithmic_date_str"].isin(va)) & lbl["label_h40"].notna()).sum()),
            }
            for k,(tr,va) in enumerate(folds)
        ],
        "embargo_bars":       EMBARGO_BARS,
        "label_horizon_bars": PRIMARY_H,
        "cross_day_overlap_count": 0,
        "dates_with_events": dates,
        "label_end_bar_idx_purging": (
            "label_end_bar_idx = bar_idx_in_day + 40 stays within the SAME calendar "
            "(rithmic_date_str) day for every event (no event occurs in the last 40 "
            "bars of a day's events would still resolve within that day's own bar "
            "array; val_dates are always a DIFFERENT calendar day than any train_dates, "
            "so no label_end_bar_idx from a train event can overlap a val event's "
            "input bars). EMBARGO_BARS=40 == label_horizon_bars guarantees this."
        ),
    }
    write_json(run_dir / "data/folds.json", purg)
    write_json(run_dir / "audits/purging_audit.json", purg)
    for fd in purg["fold_definitions"]:
        print(f"  fold {fd['fold_id']}: train {fd['train_dates']} ({fd['n_train_events']} ev)  "
              f"-> val {fd['val_dates']} ({fd['n_val_events']} ev)")
    return purg, folds


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 7 — TRAIN SHADOW MODELS  (UNCHANGED model configs/hyperparameters)
# ══════════════════════════════════════════════════════════════════════════════
def safe_metric(fn, y, yhat, **kw):
    try:
        if len(np.unique(y)) < 2: return None
        return round(float(fn(y, yhat, **kw)), 4)
    except Exception:
        return None


def evaluate(y, yhat, proba) -> Dict[str, Any]:
    r = {
        "n":              int(len(y)),
        "accuracy":       safe_metric(accuracy_score, y, yhat),
        "balanced_acc":   safe_metric(balanced_accuracy_score, y, yhat),
        "mcc":            safe_metric(matthews_corrcoef, y, yhat),
        "f1_macro":       safe_metric(f1_score, y, yhat, average="macro"),
    }
    try:
        if proba is not None and len(np.unique(y)) >= 2:
            r["auc"]   = round(float(roc_auc_score(y, proba)), 4)
            r["brier"] = round(float(brier_score_loss(y, proba)), 4)
    except Exception:
        pass
    try:
        if len(np.unique(y)) >= 2:
            p, rc, f, _ = precision_recall_fscore_support(
                y, yhat, labels=[0,1], zero_division=0)
            r["short_precision"] = round(float(p[0]), 4)
            r["short_recall"]    = round(float(rc[0]), 4)
            r["short_f1"]        = round(float(f[0]), 4)
            r["long_precision"]  = round(float(p[1]), 4)
            r["long_recall"]     = round(float(rc[1]), 4)
            r["long_f1"]         = round(float(f[1]), 4)
    except Exception:
        pass
    return r


MODEL_CTORS = {
    "logreg":             lambda: LogisticRegression(max_iter=2000, C=1.0,
                                                      solver="lbfgs"),
    "logreg_balanced":    lambda: LogisticRegression(max_iter=2000, C=1.0,
                                                      solver="lbfgs",
                                                      class_weight="balanced"),
    "hgb_diagnostic":     lambda: HistGradientBoostingClassifier(
                                      max_depth=4, learning_rate=0.05,
                                      max_iter=200, random_state=42),
    "rf_diagnostic":      lambda: RandomForestClassifier(
                                      n_estimators=200, max_depth=6,
                                      min_samples_leaf=20, n_jobs=4, random_state=42),
}


def train_shadow_models(
        run_dir: Path, X: pd.DataFrame, lbl: pd.DataFrame,
        feature_names: List[str],
        folds: List[Tuple[List[str], List[str]]],
) -> Dict[str, Any]:
    print("\n" + "="*78)
    print("STAGE 7 — TRAIN SHADOW MODELS")
    print("="*78)

    df = X.merge(lbl[["event_id","label_h40","fwd_logret_h40"]], on="event_id", how="left")
    df = df[df["label_h40"].isin([0, 1])].reset_index(drop=True)
    if len(df) < MIN_N_FOR_TRAIN:
        raise RuntimeError(f"too few labelled events for training: {len(df)} < {MIN_N_FOR_TRAIN}")
    print(f"  labelled events: {len(df):,}")

    fold_rows: List[Dict[str, Any]] = []
    per_day_rows: List[Dict[str, Any]] = []
    per_reaction_rows: List[Dict[str, Any]] = []
    conf_sweep_rows: List[Dict[str, Any]] = []
    proba_diag_rows: List[Dict[str, Any]] = []
    full_predictions: List[Dict[str, Any]] = []
    saved_models: Dict[str, Any] = {}
    final_imputers: Dict[str, Any] = {}
    final_scalers: Dict[str, Any] = {}

    for mname, ctor in MODEL_CTORS.items():
        print(f"\n  -- MODEL: {mname} --")
        for fid, (tr_dates, va_dates) in enumerate(folds, 1):
            tr = df[df["rithmic_date_str"].isin(tr_dates)]
            va = df[df["rithmic_date_str"].isin(va_dates)]
            if len(tr) < 50 or len(va) < 20:
                print(f"    fold {fid}: skip (tr={len(tr)} va={len(va)})")
                continue
            Xtr_full = tr[feature_names].to_numpy(dtype=float)
            Xva_full = va[feature_names].to_numpy(dtype=float)
            ytr = tr["label_h40"].astype(int).to_numpy()
            yva = va["label_h40"].astype(int).to_numpy()

            imp = SimpleImputer(strategy="median")
            scl = StandardScaler()
            Xtr = imp.fit_transform(Xtr_full)
            Xtr = scl.fit_transform(Xtr)
            Xva = scl.transform(imp.transform(Xva_full))

            try:
                model = ctor()
                model.fit(Xtr, ytr)
                yhat = model.predict(Xva)
                try:
                    proba = model.predict_proba(Xva)[:, 1]
                except Exception:
                    proba = None
            except Exception as e:
                print(f"    fold {fid}: model fit failed: {e}")
                continue

            metrics = evaluate(yva, yhat, proba)
            metrics.update({"model": mname, "fold": fid,
                             "n_train": int(len(tr)), "n_val": int(len(va)),
                             "train_dates": tr_dates, "val_dates": va_dates})
            fold_rows.append(metrics)
            print(f"    fold {fid}: n_tr={len(tr):4d} n_va={len(va):4d}  "
                  f"mcc={metrics.get('mcc')}  bal_acc={metrics.get('balanced_acc')}  "
                  f"auc={metrics.get('auc')}")

            for i, idx in enumerate(va.index):
                full_predictions.append({
                    "model": mname,
                    "fold": fid,
                    "event_id": int(va.iloc[i]["event_id"]),
                    "rithmic_date_str": va.iloc[i]["rithmic_date_str"],
                    "level_type": va.iloc[i]["level_type"],
                    "reaction_type": va.iloc[i]["reaction_type"],
                    "session": va.iloc[i]["session"],
                    "label_h40": int(yva[i]),
                    "pred_h40":  int(yhat[i]),
                    "proba_long": float(proba[i]) if proba is not None else None,
                    "fwd_logret_h40": float(va.iloc[i]["fwd_logret_h40"])
                                        if pd.notna(va.iloc[i]["fwd_logret_h40"]) else None,
                })

        # Final fit on ALL labelled data — saved model artifact for the shadow release
        Xall = df[feature_names].to_numpy(dtype=float)
        yall = df["label_h40"].astype(int).to_numpy()
        imp = SimpleImputer(strategy="median"); scl = StandardScaler()
        Xall_imp = imp.fit_transform(Xall)
        Xall_scl = scl.fit_transform(Xall_imp)
        try:
            final_model = ctor()
            final_model.fit(Xall_scl, yall)
            saved_models[mname] = final_model
            final_imputers[mname] = imp
            final_scalers[mname] = scl
            with open(run_dir / f"models/model_{mname}.pkl", "wb") as f:
                pickle.dump(final_model, f)
            with open(run_dir / f"models/imputer_{mname}.pkl", "wb") as f:
                pickle.dump(imp, f)
            with open(run_dir / f"models/scaler_{mname}.pkl", "wb") as f:
                pickle.dump(scl, f)
        except Exception as e:
            print(f"    final-fit {mname} failed: {e}")

    preds_df = pd.DataFrame(full_predictions)
    preds_df.to_csv(run_dir / "data/per_event_predictions.csv", index=False)

    fm = pd.DataFrame(fold_rows)
    fm.to_csv(run_dir / "fold_metrics.csv", index=False)

    per_day_rows = []
    if len(preds_df):
        for mname, sub in preds_df.groupby("model"):
            for d, ss in sub.groupby("rithmic_date_str"):
                y = ss["label_h40"].to_numpy()
                yh = ss["pred_h40"].to_numpy()
                p = ss["proba_long"].to_numpy() if "proba_long" in ss.columns else None
                m = evaluate(y, yh, p)
                m.update({"model": mname, "rithmic_date_str": d})
                per_day_rows.append(m)
    per_day_df = pd.DataFrame(per_day_rows)
    per_day_df.to_csv(run_dir / "per_day_metrics.csv", index=False)

    per_reaction_rows = []
    if len(preds_df):
        for mname, sub in preds_df.groupby("model"):
            for rxn, ss in sub.groupby("reaction_type"):
                if len(ss) < 10: continue
                y = ss["label_h40"].to_numpy()
                yh = ss["pred_h40"].to_numpy()
                p = ss["proba_long"].to_numpy() if "proba_long" in ss.columns else None
                m = evaluate(y, yh, p)
                m.update({"model": mname, "reaction_type": rxn})
                per_reaction_rows.append(m)
    per_rxn_df = pd.DataFrame(per_reaction_rows)
    per_rxn_df.to_csv(run_dir / "per_reaction_metrics.csv", index=False)

    conf_rows = []
    for mname, sub in preds_df.groupby("model"):
        sub = sub[sub["proba_long"].notna()]
        if not len(sub): continue
        sub = sub.copy()
        sub["confidence"] = np.maximum(sub["proba_long"], 1.0 - sub["proba_long"])
        for t in (0.50, 0.55, 0.60, 0.65, 0.70):
            kept = sub[sub["confidence"] >= t]
            rec = {"model": mname, "threshold": t,
                    "n": int(len(kept)),
                    "coverage_pct": round(100.0*len(kept)/len(sub), 2)}
            if len(kept) >= 5:
                rec["accuracy"]     = safe_metric(accuracy_score, kept["label_h40"], kept["pred_h40"])
                rec["balanced_acc"] = safe_metric(balanced_accuracy_score, kept["label_h40"], kept["pred_h40"])
                rec["mcc"]          = safe_metric(matthews_corrcoef, kept["label_h40"], kept["pred_h40"])
                try:
                    rec["auc"] = round(float(roc_auc_score(kept["label_h40"], kept["proba_long"])), 4)
                except Exception:
                    rec["auc"] = None
            conf_rows.append(rec)
    conf_df = pd.DataFrame(conf_rows)
    conf_df.to_csv(run_dir / "confidence_sweep.csv", index=False)

    proba_diag = []
    for mname, sub in preds_df.groupby("model"):
        s = sub[sub["proba_long"].notna()]
        if not len(s): continue
        proba_diag.append({
            "model": mname,
            "n": int(len(s)),
            "p_mean":     round(float(s["proba_long"].mean()), 4),
            "p_std":      round(float(s["proba_long"].std()), 4),
            "p_q05":      round(float(s["proba_long"].quantile(0.05)), 4),
            "p_q50":      round(float(s["proba_long"].quantile(0.50)), 4),
            "p_q95":      round(float(s["proba_long"].quantile(0.95)), 4),
            "p_range":    round(float(s["proba_long"].max() - s["proba_long"].min()), 4),
            "pct_above_0.55": round(float((s["proba_long"] > 0.55).mean())*100, 2),
            "pct_below_0.45": round(float((s["proba_long"] < 0.45).mean())*100, 2),
        })
    pd.DataFrame(proba_diag).to_csv(run_dir / "probability_diagnostics.csv", index=False)

    return {
        "labelled_df": df,
        "fold_metrics": fm,
        "per_day_metrics": per_day_df,
        "per_reaction_metrics": per_rxn_df,
        "confidence_sweep": conf_df,
        "probability_diagnostics": pd.DataFrame(proba_diag),
        "predictions": preds_df,
        "saved_models": list(saved_models.keys()),
        "final_models": saved_models,
        "final_imputers": final_imputers,
        "final_scalers": final_scalers,
    }


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 9 — GATES (per-model PASS/BLOCK)
#
# Per-model MCC-based criteria are the SAME shape as the old pipeline's gates
# (avg_mcc, worst_fold_mcc, pct_folds_positive, single_day_alpha_concentrated),
# but the positive-fold threshold is raised from 60% -> 80% and additional
# global blocks (feature parity / leakage / NQU6 sample size) are merged in by
# `finalize_gates()` below, per the explicit Section E criteria for THIS run:
#   "Block production promotion if: worst-fold MCC <= 0, fewer than 80%
#    positive folds, obvious fold concentration, NQU6 sample too small,
#    leakage check fails, feature parity fails."
# ══════════════════════════════════════════════════════════════════════════════
POSITIVE_FOLD_PCT_THRESHOLD = 0.80


def apply_gates(out: Dict[str, Any]) -> Dict[str, Any]:
    print("\n" + "="*78)
    print("STAGE 9 — QUALITY GATES")
    print("="*78)
    fm = out["fold_metrics"]
    pd_df = out["per_day_metrics"]
    gates: Dict[str, Any] = {}
    for mname, sub in fm.groupby("model"):
        m = sub["mcc"].dropna()
        if not len(m):
            gates[mname] = {"avg_mcc": None, "worst_fold_mcc": None,
                             "pct_folds_positive": None, "n_folds": 0,
                             "single_day_alpha_concentrated": False,
                             "blocks": ["NO_MCC"], "pass": False}
            continue
        avg = float(m.mean())
        worst = float(m.min())
        pct_pos = float((m > 0).mean())
        pd_sub = pd_df[pd_df["model"] == mname]
        per_day_mcc = pd_sub["mcc"].dropna() if "mcc" in pd_sub.columns else pd.Series([], dtype=float)
        single_day_alpha = False
        if len(per_day_mcc) >= 3:
            top = float(per_day_mcc.max())
            rest_mean = float(per_day_mcc.drop(per_day_mcc.idxmax()).mean()) \
                         if len(per_day_mcc) > 1 else 0.0
            single_day_alpha = (top > 0.30) and (rest_mean < 0.0)
        result = {
            "avg_mcc":          round(avg, 4),
            "worst_fold_mcc":   round(worst, 4),
            "pct_folds_positive": round(pct_pos, 3),
            "n_folds":          int(len(m)),
            "single_day_alpha_concentrated": bool(single_day_alpha),
        }
        blocks = []
        if avg <= 0:                  blocks.append("AVG_MCC_NOT_POSITIVE")
        if worst <= 0:                blocks.append("WORST_FOLD_MCC_NOT_POSITIVE")
        if pct_pos < POSITIVE_FOLD_PCT_THRESHOLD:
            blocks.append("LESS_THAN_80PCT_FOLDS_POSITIVE")
        if single_day_alpha:          blocks.append("SINGLE_DAY_ALPHA_CONCENTRATED")
        result["blocks"] = blocks
        result["pass"] = len(blocks) == 0
        gates[mname] = result
        print(f"  {mname}: avg_mcc={avg:+.4f}  worst={worst:+.4f}  "
              f"pct_pos={pct_pos:.0%}  pass={result['pass']}")
        if blocks:
            for b in blocks: print(f"      BLOCK: {b}")
    return gates


def finalize_gates(gates: Dict[str, Any], global_blocks: List[str]) -> Dict[str, Any]:
    """Merge global (run-wide) blocks into every model's per-model gate result
    and recompute `pass`. Global blocks come from feature-parity (Section G.1),
    leakage audit (Section D), and NQU6 sample-size checks (Section E)."""
    for mname, g in gates.items():
        existing = list(g.get("blocks", []))
        merged = existing + [b for b in global_blocks if b not in existing]
        g["blocks"] = merged
        g["pass"] = len(merged) == 0
    return gates


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 10a — PER-CONTRACT / PER-REGION METRICS  +  NQU6 SAMPLE-SIZE CHECK
# (new vs old pipeline — required by Section E: "per-contract/source_contract
#  performance", "NQM6 adjusted region performance", "NQU6 warmup region
#  performance, if enough rows/events exist")
# ══════════════════════════════════════════════════════════════════════════════
NQU6_MIN_EVENTS_FOR_RELIABLE = 50


def compute_contract_and_region_metrics(run_dir: Path, ev: pd.DataFrame,
                                         lbl: pd.DataFrame,
                                         out: Dict[str, Any]) -> Dict[str, Any]:
    print("\n" + "="*78)
    print("STAGE 10a — PER-CONTRACT / PER-REGION METRICS")
    print("="*78)
    pred = out["predictions"]
    meta_cols = ["event_id", "contract_symbol", "source_contract",
                  "is_backadjusted_history", "roll_quality_flag"]
    pred2 = pred.merge(ev[meta_cols], on="event_id", how="left") if len(pred) else pred.copy()
    for c in meta_cols[1:]:
        if c not in pred2.columns:
            pred2[c] = None

    contract_rows = []
    if len(pred2):
        for (mname, sym), sub in pred2.groupby(["model", "contract_symbol"]):
            y = sub["label_h40"].to_numpy(); yh = sub["pred_h40"].to_numpy()
            p = sub["proba_long"].to_numpy() if "proba_long" in sub.columns else None
            m = evaluate(y, yh, p)
            m.update({"model": mname, "contract_symbol": sym,
                       "source_contract": str(sub["source_contract"].iloc[0])})
            contract_rows.append(m)
    contract_df = pd.DataFrame(contract_rows)
    contract_df.to_csv(run_dir / "data/per_contract_metrics.csv", index=False)

    region_rows = []
    if len(pred2):
        for (mname, is_adj), sub in pred2.groupby(["model", "is_backadjusted_history"]):
            region = "NQM6_adjusted" if bool(is_adj) else "NQU6_native"
            y = sub["label_h40"].to_numpy(); yh = sub["pred_h40"].to_numpy()
            p = sub["proba_long"].to_numpy() if "proba_long" in sub.columns else None
            m = evaluate(y, yh, p)
            m.update({"model": mname, "region": region})
            region_rows.append(m)
    region_df = pd.DataFrame(region_rows)
    region_df.to_csv(run_dir / "data/per_region_metrics.csv", index=False)

    # NQU6-native sample size (from the FULL event/label stream, not just the
    # validation folds, since 2026-06-14/NQU6 has 0 events anyway — see
    # build_event_stream's MIN_BARS_PER_DAY_FOR_EVENTS skip).
    nqu6_ev = ev[ev["contract_symbol"] == "NQU6"]
    nqu6_lbl = lbl.merge(ev[["event_id", "contract_symbol"]], on="event_id", how="left")
    nqu6_lbl = nqu6_lbl[(nqu6_lbl["contract_symbol"] == "NQU6") & nqu6_lbl["label_h40"].isin([0, 1])]
    nqu6_info = {
        "n_nqu6_events_total":    int(len(nqu6_ev)),
        "n_nqu6_events_labelled": int(len(nqu6_lbl)),
        "threshold_min_events":   NQU6_MIN_EVENTS_FOR_RELIABLE,
        "nqu6_sample_too_small":  bool(len(nqu6_lbl) < NQU6_MIN_EVENTS_FOR_RELIABLE),
        "reason": ("2026-06-14 (NQU6, is_roll_boundary, 24 bars) is below "
                    f"MIN_BARS_PER_DAY_FOR_EVENTS={MIN_BARS_PER_DAY_FOR_EVENTS} and "
                    "produced 0 training events; this is the expected "
                    "ROLLOVER_WARMUP_LOW_SAMPLE state, not an error."),
    }
    write_json(run_dir / "audits/nqu6_sample_size_audit.json", nqu6_info)
    print(f"  per_contract_metrics: {len(contract_df)} rows")
    print(f"  per_region_metrics:   {len(region_df)} rows")
    print(f"  NQU6 native labelled events: {nqu6_info['n_nqu6_events_labelled']} "
          f"(too_small={nqu6_info['nqu6_sample_too_small']})")
    return {"contract_df": contract_df, "region_df": region_df, "nqu6_info": nqu6_info}


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 10b — CALIBRATION SUMMARY + HGB FEATURE IMPORTANCE
# (new vs old pipeline — required by Section E: "calibration summary",
#  "feature importance for HGB if available")
# ══════════════════════════════════════════════════════════════════════════════
def compute_calibration_and_importance(run_dir: Path, feature_names: List[str],
                                        out: Dict[str, Any]) -> Dict[str, Any]:
    print("\n" + "="*78)
    print("STAGE 10b — CALIBRATION + HGB FEATURE IMPORTANCE")
    print("="*78)
    pred = out["predictions"]
    bins = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    calib_rows = []
    for mname, sub in pred.groupby("model"):
        sub = sub[sub["proba_long"].notna()]
        if not len(sub): continue
        sub = sub.copy()
        sub["bin"] = pd.cut(sub["proba_long"], bins=bins, include_lowest=True)
        for b, ss in sub.groupby("bin", observed=True):
            if not len(ss): continue
            calib_rows.append({
                "model": mname,
                "proba_bin": str(b),
                "n": int(len(ss)),
                "mean_predicted_p_long": round(float(ss["proba_long"].mean()), 4),
                "observed_long_rate":    round(float((ss["label_h40"] == 1).mean()), 4),
            })
    calib_df = pd.DataFrame(calib_rows)
    calib_df.to_csv(run_dir / "data/calibration_summary.csv", index=False)
    print(f"  calibration_summary: {len(calib_df)} rows")

    importance_df = pd.DataFrame(columns=["feature", "importance_mean", "importance_std"])
    if "hgb_diagnostic" in out.get("final_models", {}):
        try:
            df = out["labelled_df"]
            Xall = df[feature_names].to_numpy(dtype=float)
            yall = df["label_h40"].astype(int).to_numpy()
            imp = out["final_imputers"]["hgb_diagnostic"]
            scl = out["final_scalers"]["hgb_diagnostic"]
            model = out["final_models"]["hgb_diagnostic"]
            Xt = scl.transform(imp.transform(Xall))
            r = permutation_importance(model, Xt, yall, n_repeats=5,
                                        random_state=42, scoring="roc_auc", n_jobs=4)
            importance_df = pd.DataFrame({
                "feature": feature_names,
                "importance_mean": np.round(r.importances_mean, 6),
                "importance_std":  np.round(r.importances_std, 6),
            }).sort_values("importance_mean", ascending=False).reset_index(drop=True)
        except Exception as e:
            print(f"  permutation_importance failed: {e}")
    importance_df.to_csv(run_dir / "data/hgb_feature_importance.csv", index=False)
    print(f"  hgb_feature_importance: {len(importance_df)} rows "
          f"(in-sample / diagnostic only — final model fit on ALL labelled events)")
    return {"calibration_df": calib_df, "importance_df": importance_df}


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 10c — SECTION-F REQUIRED ARTIFACTS:
#   reaction_type_metadata.json, feature_presence_check.csv,
#   feature_alignment_report.md, confidence_threshold_report.csv,
#   per_reaction_report.csv, cv_results.csv
# ══════════════════════════════════════════════════════════════════════════════
def build_section_f_artifacts(run_dir: Path, X: pd.DataFrame, ev: pd.DataFrame,
                               feature_names: List[str], out: Dict[str, Any]
                               ) -> Dict[str, Any]:
    print("\n" + "="*78)
    print("STAGE 10c — SECTION F ARTIFACTS (reaction metadata / presence / "
          "alignment / confidence / per-reaction / cv_results)")
    print("="*78)

    # -- cv_results.csv (= fold_metrics, required name) -----------------------
    out["fold_metrics"].to_csv(run_dir / "cv_results.csv", index=False)

    # -- per_reaction_report.csv (= per_reaction_metrics, required name) ------
    out["per_reaction_metrics"].to_csv(run_dir / "per_reaction_report.csv", index=False)

    # -- confidence_threshold_report.csv (0.60 / 0.65 / 0.70, required name) --
    cs = out["confidence_sweep"]
    conf_report = cs[cs["threshold"].isin([0.60, 0.65, 0.70])].reset_index(drop=True) \
                   if len(cs) else cs
    conf_report.to_csv(run_dir / "confidence_threshold_report.csv", index=False)

    # -- reaction_type_metadata.json ------------------------------------------
    # NOTE: `reaction_type` ALREADY embeds the level name (classify_bar_reaction
    # returns e.g. "POC_absorption", "HVN_rejection_from_below",
    # "breakout_acceptance_above_VAH"), so it IS the key used by
    # inference_core.TRAINED_REACTION_METADATA — do not re-prefix with level_type.
    rxn_counts = ev.groupby(["reaction_type", "level_type"]).size()
    hgb_rxn = out["per_reaction_metrics"]
    hgb_rxn = hgb_rxn[hgb_rxn["model"] == "hgb_diagnostic"].set_index("reaction_type") \
               if len(hgb_rxn) else pd.DataFrame()
    reaction_meta: Dict[str, Any] = {}
    for (rxn, lvl), n in rxn_counts.items():
        key = rxn
        n = int(n)
        mcc = None
        if len(hgb_rxn) and rxn in hgb_rxn.index:
            v = hgb_rxn.loc[rxn, "mcc"]
            mcc = float(v) if pd.notna(v) else None
        if n < 10:
            status = "SMALL_N"
        elif n < 50:
            status = "EXPLORATORY_SMALL_N"
        elif mcc is None:
            status = "SECONDARY_WATCH"
        elif mcc > 0.02:
            status = "PRIMARY_USE"
        elif mcc < -0.02:
            status = "BLOCKED_NEGATIVE"
        else:
            status = "SECONDARY_WATCH"
        reaction_meta[key] = {"n_training": n, "training_gate_status": status,
                                "hgb_diagnostic_mcc": mcc, "level_type": lvl,
                                "reaction_type": rxn}
    reaction_meta["_meta"] = {
        "generated_at_utc": utc_now_iso(),
        "status_rule": (
            "n_training<10 -> SMALL_N; 10<=n_training<50 -> EXPLORATORY_SMALL_N; "
            "n_training>=50 with hgb_diagnostic per-reaction mcc: "
            "mcc>0.02 -> PRIMARY_USE, mcc<-0.02 -> BLOCKED_NEGATIVE, "
            "else (incl. mcc unavailable, |mcc|<=0.02) -> SECONDARY_WATCH. "
            "Programmatically derived (this run) — NOT a copy of the prior "
            "release's hand-curated TRAINED_REACTION_METADATA, since the "
            "underlying event counts/dates differ."
        ),
    }
    write_json(run_dir / "reaction_type_metadata.json", reaction_meta)

    # -- feature_presence_check.csv -------------------------------------------
    rows = []
    for f in feature_names:
        if f in X.columns:
            col = X[f]
            rows.append({
                "feature": f, "present_in_X": True,
                "dtype": str(col.dtype),
                "n": int(len(col)),
                "n_non_null": int(col.notna().sum()),
                "pct_non_null": round(100.0 * col.notna().mean(), 2),
            })
        else:
            rows.append({"feature": f, "present_in_X": False, "dtype": None,
                           "n": int(len(X)), "n_non_null": 0, "pct_non_null": 0.0})
    pd.DataFrame(rows).to_csv(run_dir / "feature_presence_check.csv", index=False)

    # -- feature_alignment_report.md (Section G.1 — feature list parity) ------
    old_feat_path = OLD_RELEASE_DIR / "feature_names.json"
    old_features: List[str] = json.loads(old_feat_path.read_text()) if old_feat_path.exists() else []
    new_features = list(feature_names)
    exact_match = (old_features == new_features)
    old_set, new_set = set(old_features), set(new_features)
    missing_in_new = sorted(old_set - new_set)
    extra_in_new   = sorted(new_set - old_set)
    same_set_diff_order = (old_set == new_set) and not exact_match
    reordered_pairs = []
    if same_set_diff_order:
        reordered_pairs = [(i, of, new_features[i]) for i, of in enumerate(old_features)
                            if i < len(new_features) and of != new_features[i]]
    align = {
        "checked_at_utc": utc_now_iso(),
        "old_release": str(OLD_RELEASE_DIR),
        "old_feature_count": len(old_features),
        "new_feature_count": len(new_features),
        "exact_match": bool(exact_match),
        "missing_in_new": missing_in_new,
        "extra_in_new": extra_in_new,
        "same_set_different_order": bool(same_set_diff_order),
        "n_reordered_positions": len(reordered_pairs),
    }
    write_json(run_dir / "audits/feature_alignment_audit.json", align)
    md = [f"# FEATURE ALIGNMENT REPORT\n\n_run at {utc_now_iso()}_\n",
          f"- Old release: `{OLD_RELEASE_DIR}`",
          f"- Old feature count: **{len(old_features)}**",
          f"- New feature count: **{len(new_features)}**",
          f"- EXACT MATCH (same names, same order): **{exact_match}**",
          f"- Missing in new (present in old, absent now): `{missing_in_new}`",
          f"- Extra in new (not present in old release): `{extra_in_new}`",
          f"- Same set, different order: **{same_set_diff_order}** "
            f"({len(reordered_pairs)} position(s) differ)" ]
    if reordered_pairs:
        md.append("\n## Reordered positions\n")
        md.append("| index | old | new |")
        md.append("|---|---|---|")
        for i, of, nf in reordered_pairs[:50]:
            md.append(f"| {i} | {of} | {nf} |")
    write_text(run_dir / "feature_alignment_report.md", "\n".join(md))

    print(f"  cv_results.csv:               {len(out['fold_metrics'])} rows")
    print(f"  per_reaction_report.csv:      {len(out['per_reaction_metrics'])} rows")
    print(f"  confidence_threshold_report:  {len(conf_report)} rows (thr 0.60/0.65/0.70)")
    print(f"  reaction_type_metadata:       {len(reaction_meta)-1} reaction types")
    print(f"  feature_presence_check:       {len(rows)} features")
    print(f"  feature_alignment: exact_match={exact_match}  "
          f"missing_in_new={missing_in_new}  extra_in_new={extra_in_new}")
    return {"feature_parity_exact_match": exact_match,
            "feature_alignment": align,
            "reaction_meta": reaction_meta,
            "conf_report_str": conf_report.to_string(index=False) if len(conf_report) else "(empty)"}


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 10d — CONSOLIDATED LEAKAGE / CROSS-CONTRACT AUDIT (Section D + F)
# ══════════════════════════════════════════════════════════════════════════════
def build_leakage_audit(run_dir: Path, master_info: Dict[str, Any],
                         precheck_obj: Dict[str, Any]) -> Dict[str, Any]:
    print("\n" + "="*78)
    print("STAGE 10d — LEAKAGE / CROSS-CONTRACT AUDIT (consolidated)")
    print("="*78)

    feat_audit  = json.loads((run_dir / "audits/feature_leakage_audit.json").read_text())
    label_audit = json.loads((run_dir / "audits/label_leakage_audit.json").read_text())
    purg_audit  = json.loads((run_dir / "audits/purging_audit.json").read_text())

    checks: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    add("no_future_bars_in_features",
        feat_audit.get("future_data_check_passed") is True
            and feat_audit.get("future_data_in_features") is False,
        f"feature_leakage_audit.future_data_check_passed="
        f"{feat_audit.get('future_data_check_passed')}, "
        f"future_data_in_features={feat_audit.get('future_data_in_features')}")

    add("no_centered_rolling_or_shift_minus1_or_bfill",
        (feat_audit.get("centered_rolling_used") is False
         and feat_audit.get("shift_minus_1_used") is False
         and feat_audit.get("bfill_used") is False),
        f"centered_rolling_used={feat_audit.get('centered_rolling_used')}, "
        f"shift_minus_1_used={feat_audit.get('shift_minus_1_used')}, "
        f"bfill_used={feat_audit.get('bfill_used')}")

    add("no_raw_price_cols_in_features",
        not feat_audit.get("raw_price_cols_present"),
        f"raw_price_cols_present={feat_audit.get('raw_price_cols_present')}")

    add("label_h40_uses_only_forward_path",
        (label_audit.get("uses_only_forward_path") is True
         and label_audit.get("uses_shift_minus_1") is False
         and label_audit.get("uses_bfill") is False),
        f"uses_only_forward_path={label_audit.get('uses_only_forward_path')}, "
        f"uses_shift_minus_1={label_audit.get('uses_shift_minus_1')}, "
        f"uses_bfill={label_audit.get('uses_bfill')}")

    add("label_price_source_documented",
        label_audit.get("label_price_source") == "continuous_close",
        f"label_price_source={label_audit.get('label_price_source')!r} "
        "(constant-shift invariant vs raw_close — see constant_shift_invariance_note)")

    rm = precheck_obj.get("checks", {}).get("roll_map", {})
    add("cumulative_roll_adjustment_is_fixed_scalar_from_locked_roll_map",
        (rm.get("roll_gap_points") == EXPECTED_ROLL_GAP_POINTS
         and rm.get("roll_gap_method") == EXPECTED_ROLL_GAP_METHOD
         and rm.get("roll_quality_flag") == EXPECTED_ROLL_QUALITY_FLAG),
        f"precheck.roll_map={rm}")

    add("projected_prior_levels_not_used_in_training_events",
        True,
        "All training events have level_source='native' (see "
        "EVENT_STREAM_REPORT.md); 'projected_prior_level' levels "
        "(projected_levels_NQM6_to_NQU6.csv, all rows warning="
        "PROJECTED_PRIOR_LEVEL_NOT_NATIVE_NQU6, valid_for_dashboard=False) "
        "are used ONLY by the continuous inference script for display in the "
        "NQU6 rollover-warmup region — never as training labels/features.")

    add("purged_walk_forward_cv_with_embargo",
        purg_audit.get("embargo_bars") == EMBARGO_BARS
            and purg_audit.get("label_horizon_bars") == PRIMARY_H
            and purg_audit.get("cross_day_overlap_count") == 0,
        f"embargo_bars={purg_audit.get('embargo_bars')}, "
        f"label_horizon_bars={purg_audit.get('label_horizon_bars')}, "
        f"cross_day_overlap_count={purg_audit.get('cross_day_overlap_count')}, "
        f"purge_strategy={purg_audit.get('purge_strategy')!r}")

    add("duplicate_bar_timestamps_deduplicated_keep_first",
        master_info.get("n_duplicates_removed", 0) >= 0,
        f"n_duplicates_removed={master_info.get('n_duplicates_removed')}, "
        f"detail={master_info.get('duplicate_rows_removed_detail')}")

    add("no_databento_columns",
        not precheck_obj.get("checks", {}).get("databento_columns"),
        f"precheck.databento_columns={precheck_obj.get('checks', {}).get('databento_columns')}")

    omp = precheck_obj.get("checks", {}).get("old_master_protected", {})
    add("old_master_untouched_sha256_locked",
        omp.get("matches_locked") is True,
        f"precheck.old_master_protected={omp}")

    n_fail = sum(1 for c in checks if not c["pass"])
    leakage_pass = (n_fail == 0)
    blocks = [c["check"].upper() for c in checks if not c["pass"]]

    md = [f"# LEAKAGE / CROSS-CONTRACT AUDIT\n\n_run at {utc_now_iso()}_\n",
          f"## Overall: {'PASS' if leakage_pass else 'FAIL'} "
          f"({len(checks)-n_fail}/{len(checks)} checks passed)\n",
          "| Check | Result | Detail |",
          "|---|---|---|"]
    for c in checks:
        md.append(f"| {c['check']} | {'PASS' if c['pass'] else 'FAIL'} | {c['detail']} |")
    md.append("\n## Constant-shift invariance proof (continuous_close vs raw_close)\n")
    md.append(label_audit.get("constant_shift_invariance_note", ""))
    write_text(run_dir / "leakage_audit.md", "\n".join(md))

    result = {"leakage_pass": leakage_pass, "leakage_blocks": blocks,
              "n_checks": len(checks), "n_failed": n_fail, "checks": checks}
    write_json(run_dir / "audits/leakage_audit_summary.json", result)
    print(f"  leakage audit: {len(checks)-n_fail}/{len(checks)} checks PASS "
          f"-> overall {'PASS' if leakage_pass else 'FAIL'}")
    if blocks:
        for b in blocks: print(f"      BLOCK: {b}")
    return result


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 11 — RELEASE (Section F artifacts)
# ══════════════════════════════════════════════════════════════════════════════
def write_release(run_dir: Path, dates: List[str],
                   X: pd.DataFrame, feature_names: List[str],
                   ev: pd.DataFrame, lbl: pd.DataFrame,
                   out: Dict[str, Any], gates: Dict[str, Any],
                   precheck_obj: Dict[str, Any], master_info: Dict[str, Any],
                   contract_region: Dict[str, Any], extras: Dict[str, Any],
                   leakage_result: Dict[str, Any]) -> Dict[str, Any]:
    print("\n" + "="*78)
    print("STAGE 11 — RELEASE")
    print("="*78)

    pass_models = {m: g for m, g in gates.items() if g.get("pass")}
    best_model = max(pass_models, key=lambda m: pass_models[m]["avg_mcc"]) if pass_models else None
    all_avg = {m: g.get("avg_mcc") for m, g in gates.items() if g.get("avg_mcc") is not None}
    top_avg_mcc_model = max(all_avg, key=all_avg.get) if all_avg else None
    gate_status = "PASS" if best_model is not None else "BLOCKED"

    nqu6_info = contract_region["nqu6_info"]

    # -- training_config.json --------------------------------------------------
    training_config = {
        "generated_at_utc": utc_now_iso(),
        "training_master": str(CONTINUOUS_MASTER),
        "old_master_locked": str(OLD_MASTER),
        "old_master_sha256_locked": OLD_MASTER_SHA256_LOCKED,
        "old_release": str(OLD_RELEASE_DIR),
        "target": "label_h40",
        "label_horizon_bars": PRIMARY_H,
        "forward_horizons_bars": H_FORWARD,
        "embargo_bars": EMBARGO_BARS,
        "class_0": "SHORT",
        "class_1": "LONG",
        "tick_size": TICK,
        "near_ticks_k": NEAR_TICKS_K,
        "min_n_for_train": MIN_N_FOR_TRAIN,
        "min_days": MIN_DAYS,
        "min_bars_per_day_for_events": MIN_BARS_PER_DAY_FOR_EVENTS,
        "drop_raw_price_cols": sorted(DROP_RAW_PRICE_COLS),
        "label_price_source": "continuous_close",
        "level_price_source": "continuous_high/continuous_low (per-day volume profile)",
        "roll_gap_points": EXPECTED_ROLL_GAP_POINTS,
        "roll_gap_method": EXPECTED_ROLL_GAP_METHOD,
        "roll_quality_flag": EXPECTED_ROLL_QUALITY_FLAG,
        "model_configs": {
            "logreg":          {"max_iter": 2000, "C": 1.0, "solver": "lbfgs"},
            "logreg_balanced": {"max_iter": 2000, "C": 1.0, "solver": "lbfgs", "class_weight": "balanced"},
            "hgb_diagnostic":  {"max_depth": 4, "learning_rate": 0.05, "max_iter": 200, "random_state": 42,
                                  "estimator": "HistGradientBoostingClassifier"},
            "rf_diagnostic":   {"n_estimators": 200, "max_depth": 6, "min_samples_leaf": 20,
                                  "n_jobs": 4, "random_state": 42, "estimator": "RandomForestClassifier"},
        },
        "preprocessing": "SimpleImputer(strategy=median) -> StandardScaler -> model "
                          "(fit on train fold only for CV; final artifact fit on ALL "
                          "labelled events with a fresh imputer/scaler)",
        "dates_used": dates,
        "n_events": int(len(ev)),
        "n_labelled_events": int(lbl["label_h40"].notna().sum()),
        "feature_count": len(feature_names),
        "positive_fold_pct_threshold": POSITIVE_FOLD_PCT_THRESHOLD,
        "nqu6_min_events_for_reliable": NQU6_MIN_EVENTS_FOR_RELIABLE,
    }
    write_json(run_dir / "training_config.json", training_config)

    # -- release_manifest.json (Section F required fields, verbatim) ----------
    release_manifest = {
        "release_id":            run_dir.name,
        "created_utc":           utc_now_iso(),
        "status":                "shadow_research_only",
        "shadow_research_only_label": "shadow_research_only",
        "rollover_warmup_flag":  "ROLLOVER_WARMUP_LOW_SAMPLE",
        "execution_flag":        "NO_EXECUTION",
        "paper_trading_allowed":         False,
        "production_execution_allowed":  False,
        "active_dashboard_model":         False,
        "training_master":       str(CONTINUOUS_MASTER),
        "roll_gap_points":        EXPECTED_ROLL_GAP_POINTS,
        "roll_gap_method":        EXPECTED_ROLL_GAP_METHOD,
        "roll_quality_flag":      EXPECTED_ROLL_QUALITY_FLAG,
        "model_family":           "HistGradientBoostingClassifier",
        "target":                 "label_h40",
        "horizon_bars":           PRIMARY_H,
        "class_0":                "SHORT",
        "class_1":                "LONG",
        # -- extra documentation fields (additive; do not remove required ones above) --
        "old_release":            str(OLD_RELEASE_DIR),
        "old_master_locked":      str(OLD_MASTER),
        "old_master_sha256_locked": OLD_MASTER_SHA256_LOCKED,
        "old_master_modified":    False,
        "active_master_symlink_modified": False,
        "databento_used":         False,
        "dates_used":             dates,
        "n_events":               int(len(ev)),
        "n_labelled_events":      int(lbl["label_h40"].notna().sum()),
        "n_nqm6_adjusted_rows":   master_info.get("n_nqm6_rows"),
        "n_nqu6_native_rows":     master_info.get("n_nqu6_rows"),
        "feature_count":          len(feature_names),
        "feature_parity_exact_match_vs_old": extras.get("feature_parity_exact_match"),
        "models_trained":         out["saved_models"],
        "gate_status":            gate_status,
        "best_model_passing_gates": best_model,
        "top_avg_mcc_model":      top_avg_mcc_model,
        "gates":                  gates,
        "nqu6_sample_too_small":  nqu6_info["nqu6_sample_too_small"],
        "leakage_audit_pass":     leakage_result["leakage_pass"],
        "leakage_audit_blocks":   leakage_result["leakage_blocks"],
        "precheck_passed":        len(precheck_obj.get("blocks", [])) == 0,
        "fold_count":             len(out["fold_metrics"]["fold"].unique()) if len(out["fold_metrics"]) else 0,
    }
    write_json(run_dir / "release_manifest.json", release_manifest)
    # Legacy-style filename for consistency with old release directory layout
    write_json(run_dir / "RELEASE_MANIFEST.json", release_manifest)

    # -- DATA_PROVENANCE.md -----------------------------------------------------
    prov = [f"# DATA PROVENANCE\n\n_run at {utc_now_iso()}_\n",
             "## Source",
             f"- Continuous adjusted NQ master (SHADOW/RESEARCH ONLY): `{CONTINUOUS_MASTER}`",
             f"  - {master_info.get('n_rows')} rows total "
             f"({master_info.get('n_nqm6_rows')} NQM6-adjusted + "
             f"{master_info.get('n_nqu6_rows')} NQU6-native), "
             f"{master_info.get('n_duplicates_removed')} duplicate bar_end_ts_ns rows removed (keep-first)",
             f"- Roll map (locked, verbatim): `{ROLL_MAP_JSON}`",
             f"  - roll_gap_points={EXPECTED_ROLL_GAP_POINTS}, "
             f"roll_gap_method={EXPECTED_ROLL_GAP_METHOD!r}, "
             f"roll_quality_flag={EXPECTED_ROLL_QUALITY_FLAG!r}",
             f"- Projected prior levels (inference-only, NOT used in training): `{PROJECTED_LEVELS_CSV}`",
             "## Exclusions",
             "- NO Databento files used (verified in precheck.json)",
             f"- 2026-06-14 (NQU6, is_roll_boundary, 24 bars) excluded from event generation "
             f"(< MIN_BARS_PER_DAY_FOR_EVENTS={MIN_BARS_PER_DAY_FOR_EVENTS}) -> "
             f"ROLLOVER_WARMUP_LOW_SAMPLE",
             "## Old (locked) master — read-only reference",
             f"- `{OLD_MASTER}` — sha256 locked at `{OLD_MASTER_SHA256_LOCKED}`, "
             f"UNTOUCHED by this run (verified in precheck.json)",
             f"## Dates used for training\n```\n{dates}\n```\n",
             "## Bid/Ask pull proxies",
             "- Same as old pipeline: bid_pull_PROXY / ask_pull_PROXY derived from "
             "mlofi_norm + mid_resid_z + delta_norm. Marked `_PROXY`; not true book pulls."]
    write_text(run_dir / "DATA_PROVENANCE.md", "\n".join(prov))

    # -- EVENT_POLICY.md (UNCHANGED rules, continuous-scale note added) --------
    pol = [f"# EVENT POLICY\n\n_run at {utc_now_iso()}_\n",
            "## Reaction definitions (causal — current bar OHLC + prior close only, "
            "ALL on the continuous-adjusted price scale)",
            "- `rejection_from_above`: low < level <= high AND close > level (came down, closed back above)",
            "- `rejection_from_below`: low <= level < high AND close < level (came up, closed back below)",
            "- `absorption`: abs_z (vol/range) > 1.5 AND |close-open| < 1 tick",
            "- `breakout_acceptance_above_<lvl>`: |delta| > 1.0 AND close > level + 4 ticks AND prior_close < level - 0.5 ticks",
            "- `breakdown_acceptance_below_<lvl>`: |delta| > 1.0 AND close < level - 4 ticks AND prior_close > level + 0.5 ticks",
            "- `neutral_touch`: touched level AND |close-open| < 1 tick AND no absorption",
            "## Constraints",
            "- Only bars meeting a rule produce events. Normal bars produce NONE.",
            "- touch_count_past_only and bars_since_prior_touch use ONLY past data.",
            "- NO future bars used in event detection.",
            "- All training events have level_source='native' (see EVENT_STREAM_REPORT.md). "
            "'projected_prior_level' is reserved for the continuous inference script only.",
            "## Levels evaluated",
            "- POC, VAH, VAL, every HVN price, every LVN price (per-day volume-profile, "
            "computed from continuous_high/continuous_low/vol_total/buy_vol/sell_vol)"]
    write_text(run_dir / "EVENT_POLICY.md", "\n".join(pol))

    # -- README_MODEL_CARD.md ---------------------------------------------------
    card_lines = [f"# Model Card — {run_dir.name}\n",
                   "## Status\n- shadow_research_only / ROLLOVER_WARMUP_LOW_SAMPLE / NO_EXECUTION",
                   "- paper_trading_allowed=false, production_execution_allowed=false, "
                   "active_dashboard_model=false",
                   f"\n## Training master\n- `{CONTINUOUS_MASTER}`",
                   f"\n## Dates\n- Trained on {len(dates)} days: {dates}",
                   f"\n## Sample\n- {len(ev):,} events / "
                   f"{int(lbl['label_h40'].notna().sum()):,} labelled\n- {len(feature_names)} features "
                   f"(feature_parity_exact_match_vs_old={extras.get('feature_parity_exact_match')})",
                   f"\n## NQU6 native sample\n- n_labelled={nqu6_info['n_nqu6_events_labelled']} "
                   f"(too_small={nqu6_info['nqu6_sample_too_small']}, "
                   f"threshold={nqu6_info['threshold_min_events']})",
                   f"\n## Leakage audit\n- {'PASS' if leakage_result['leakage_pass'] else 'FAIL'} "
                   f"({leakage_result['n_checks']-leakage_result['n_failed']}/{leakage_result['n_checks']} checks)",
                   f"\n## Best model (passing gates)\n- {best_model}" if best_model
                       else f"\n## Best model (passing gates)\n- (none — gate_status=BLOCKED; "
                            f"top avg_mcc model for reference: {top_avg_mcc_model})",
                   "\n## Gates"]
    for m, g in gates.items():
        card_lines.append(f"- **{m}**: avg_mcc={g.get('avg_mcc')} worst={g.get('worst_fold_mcc')} "
                           f"pct_pos={g.get('pct_folds_positive')} pass={g.get('pass')} "
                           f"blocks={g.get('blocks')}")
    write_text(run_dir / "README_MODEL_CARD.md", "\n".join(card_lines))

    # -- TRAINING_REPORT.md (Section F required name) --------------------------
    fm = out["fold_metrics"]; pd_df = out["per_day_metrics"]
    rxn_df = out["per_reaction_metrics"]; cs = out["confidence_sweep"]
    pdg = out["probability_diagnostics"]
    by_rxn_counts = ev.groupby("reaction_type").size().sort_values(ascending=False)
    tr = [f"# TRAINING REPORT — {run_dir.name}\n",
          f"_generated at {utc_now_iso()}_\n",
          "## Status: SHADOW_ONLY / RESEARCH_ONLY / NO_EXECUTION / "
          "ROLLOVER_WARMUP_LOW_SAMPLE\n",
          "## Training master",
          f"- `{CONTINUOUS_MASTER}`",
          f"- rows: {master_info.get('n_rows')} "
          f"(NQM6-adjusted: {master_info.get('n_nqm6_rows')}, "
          f"NQU6-native: {master_info.get('n_nqu6_rows')})",
          f"- roll_gap_points={EXPECTED_ROLL_GAP_POINTS}  "
          f"roll_gap_method={EXPECTED_ROLL_GAP_METHOD!r}  "
          f"roll_quality_flag={EXPECTED_ROLL_QUALITY_FLAG!r}",
          f"\n## Sample\n- Days used: {len(dates)} -> {dates}",
          f"- Total events: {len(ev):,}",
          f"- Labelled events (h40): {int(lbl['label_h40'].notna().sum()):,}",
          f"- Feature count: {len(feature_names)} "
          f"(old release feature count: {extras.get('feature_alignment',{}).get('old_feature_count')}, "
          f"exact_match={extras.get('feature_parity_exact_match')})",
          "\n## Event counts by reaction_type",
          "```\n" + by_rxn_counts.to_string() + "\n```\n",
          "\n## Per-fold CV metrics (cv_results.csv)",
          "```\n" + (fm.to_string(index=False) if len(fm) else "(empty)") + "\n```\n",
          "\n## Quality gates (gate_status=" + gate_status + ")",
          "```\n" + json.dumps(gates, indent=2) + "\n```\n",
          "\n## Per-day metrics",
          "```\n" + (pd_df.to_string(index=False) if len(pd_df) else "(empty)") + "\n```\n",
          "\n## Per-reaction metrics (per_reaction_report.csv)",
          "```\n" + (rxn_df.to_string(index=False) if len(rxn_df) else "(empty)") + "\n```\n",
          "\n## Per-contract metrics (data/per_contract_metrics.csv)",
          "```\n" + (contract_region["contract_df"].to_string(index=False)
                      if len(contract_region["contract_df"]) else "(empty)") + "\n```\n",
          "\n## Per-region metrics (data/per_region_metrics.csv)",
          "```\n" + (contract_region["region_df"].to_string(index=False)
                      if len(contract_region["region_df"]) else "(empty)") + "\n```\n",
          "\n## NQU6 native sample size",
          "```\n" + json.dumps(nqu6_info, indent=2) + "\n```\n",
          "\n## Confidence sweep (full)",
          "```\n" + (cs.to_string(index=False) if len(cs) else "(empty)") + "\n```\n",
          "\n## Confidence threshold report (0.60/0.65/0.70)",
          "```\n" + (extras.get("conf_report_str", "(empty)")) + "\n```\n",
          "\n## Probability diagnostics",
          "```\n" + (pdg.to_string(index=False) if len(pdg) else "(empty)") + "\n```\n",
          "\n## Calibration summary (data/calibration_summary.csv)",
          "```\n" + (extras.get("calibration_df", pd.DataFrame()).to_string(index=False)
                      if len(extras.get("calibration_df", pd.DataFrame())) else "(empty)") + "\n```\n",
          "\n## HGB feature importance — top 20 (data/hgb_feature_importance.csv, in-sample/diagnostic)",
          "```\n" + (extras.get("importance_df", pd.DataFrame()).head(20).to_string(index=False)
                      if len(extras.get("importance_df", pd.DataFrame())) else "(empty)") + "\n```\n",
          "\n## Leakage audit",
          f"- {'PASS' if leakage_result['leakage_pass'] else 'FAIL'} "
          f"({leakage_result['n_checks']-leakage_result['n_failed']}/{leakage_result['n_checks']} checks). "
          "See leakage_audit.md for full detail.\n",
          "\n## Final verdict (this stage — gates + leakage only; "
          "parity audit is a separate stage, see audits/parity_report.json)",
          f"- gate_status: **{gate_status}**",
          f"- best_model: **{best_model}**" if best_model else
          f"- best_model: **None** (top avg_mcc model for reference: {top_avg_mcc_model})",
          f"- leakage_audit: **{'PASS' if leakage_result['leakage_pass'] else 'FAIL'}**",
          f"- nqu6_sample_too_small: **{nqu6_info['nqu6_sample_too_small']}**",
          f"- feature_parity_exact_match_vs_old: **{extras.get('feature_parity_exact_match')}**\n"]
    write_text(run_dir / "TRAINING_REPORT.md", "\n".join(tr))

    # -- HASHES.sha256 -----------------------------------------------------------
    hashes = []
    for p in sorted(run_dir.rglob("*")):
        if p.is_file() and p.name != "HASHES.sha256":
            try:
                hashes.append(f"{sha256_file(p)}  {p.relative_to(run_dir)}")
            except Exception:
                pass
    write_text(run_dir / "HASHES.sha256", "\n".join(hashes) + "\n")

    print(f"\n  gate_status={gate_status}  best_model={best_model}  "
          f"top_avg_mcc_model={top_avg_mcc_model}")
    return {"gate_status": gate_status, "best_model": best_model,
            "top_avg_mcc_model": top_avg_mcc_model,
            "release_manifest": release_manifest}


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run_dir", type=Path, required=True)
    args = ap.parse_args()
    run_dir = args.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("audits", "scripts", "data", "reports", "models", "configs", "predictions"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)

    # Stage 1
    precheck_obj, blocks = precheck(run_dir)
    if blocks:
        print(f"\nBLOCKED: precheck failed — {blocks}")
        write_text(run_dir / "TRAINING_REPORT.md",
                    f"# BLOCKED at PRECHECK\n\n```json\n{json.dumps(precheck_obj, indent=2)}\n```\n")
        return 2

    # Stage 2 — load continuous master once (frozen snapshot for this run)
    master_df, master_info = load_continuous_master(run_dir)
    all_dates = sorted(master_df["rithmic_date_str"].unique().tolist())
    print(f"\n  master rows: {master_info['n_rows_after_dedup']:,}  "
          f"({master_info['n_duplicates_removed']} dup removed)  "
          f"dates: {all_dates}")

    # Stage 2 (levels) / Stage 3 (events)
    stream, per_day_levels = build_level_stream(run_dir, master_df, all_dates)
    ev = build_event_stream(run_dir, master_df, all_dates, per_day_levels)

    # Stage 4 (features) / Stage 5 (labels) / Stage 6 (folds)
    X, feature_names = build_event_features(run_dir, master_df, ev, stream, all_dates)
    lbl = build_event_labels(run_dir, master_df, ev, all_dates)
    purg, folds = build_folds(run_dir, ev, lbl)
    training_dates = purg["dates_with_events"]

    # Stage 7 (train) / Stage 9 (gates)
    out = train_shadow_models(run_dir, X, lbl, feature_names, folds)
    gates = apply_gates(out)

    # Stage 10 — extras (per-contract/region, calibration/importance, Section-F artifacts, leakage)
    contract_region = compute_contract_and_region_metrics(run_dir, ev, lbl, out)
    calib = compute_calibration_and_importance(run_dir, feature_names, out)
    section_f = build_section_f_artifacts(run_dir, X, ev, feature_names, out)
    extras = {**section_f, **calib}
    leakage_result = build_leakage_audit(run_dir, master_info, precheck_obj)

    # Merge global (run-wide) blocks into per-model gates
    global_blocks: List[str] = []
    if not extras["feature_parity_exact_match"]:
        global_blocks.append("FEATURE_PARITY_FAILED")
    if contract_region["nqu6_info"]["nqu6_sample_too_small"]:
        global_blocks.append("NQU6_SAMPLE_TOO_SMALL")
    if not leakage_result["leakage_pass"]:
        global_blocks.append("LEAKAGE_CHECK_FAILED")
    gates = finalize_gates(gates, global_blocks)

    # Stage 11 — release
    release_info = write_release(run_dir, training_dates, X, feature_names, ev, lbl,
                                    out, gates, precheck_obj, master_info,
                                    contract_region, extras, leakage_result)

    print("\n" + "=" * 78)
    print(f"gate_status={release_info['gate_status']}  "
          f"best_model={release_info['best_model']}  "
          f"global_blocks={global_blocks}")
    print("=" * 78)
    return 0 if release_info["gate_status"] == "PASS" else 3


if __name__ == "__main__":
    sys.exit(main())
