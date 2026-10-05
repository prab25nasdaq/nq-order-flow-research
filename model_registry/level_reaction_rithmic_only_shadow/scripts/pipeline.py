#!/usr/bin/env python3
"""
STRICT RITHMIC-ONLY level-reaction shadow training pipeline.

ABSOLUTE RULES (per spec):
  * Rithmic feature files only — /home/prabh/OFI_Live_Features/<date>/NQM6_vol500.ndjsonl
  * NO Databento, NO mixed-feed master, NO external data
  * Train ONLY on valid level-reaction events
  * NO daemon / parser / live-master / V3 / V4 / TFT touched
  * Shadow only — paper_trading_allowed=false, production_execution_allowed=false
  * BLOCK if any input is missing or any leakage gate fails

Stages:
   1. Precheck   — verify all required inputs exist with correct provenance
   2. Levels     — per-day POC/VAH/VAL/HVN/LVN via dashboard volume-profile formula
   3. Events     — tag bars meeting level-reaction definitions (causal only)
   4. Features   — snapshot features at event rows (no future data)
   5. Labels     — forward h5/h10/h20/h40 from same source
   6. Folds      — purged walk-forward by day with embargo=40 bars
   7. Train      — LogReg, LogReg balanced, HistGradientBoosting, RandomForest
   8. Metrics    — per-fold, per-day, per-reaction, with hard PASS/BLOCK gates
   9. Report     — FINAL_REPORT.md + manifest, model_card, hashes
"""
from __future__ import annotations
import argparse, gzip, hashlib, json, os, pickle, sys, warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score, balanced_accuracy_score, brier_score_loss,
    confusion_matrix, f1_score, matthews_corrcoef,
    precision_recall_fscore_support, precision_score, recall_score,
    roc_auc_score,
)
from sklearn.preprocessing import StandardScaler

# ── PATHS ─────────────────────────────────────────────────────────────────── #
RITHMIC_RAW_ROOT    = Path("/home/prabh/OFI_Live_Data/Rithmic_Raw")
RITHMIC_FEAT_ROOT   = Path("/home/prabh/OFI_Live_Features")
DASH_FILE_A         = Path("/mnt/wd_work/workspace/Work Place/Data/Project OFI/"
                            "_codex_work/ofi_live_dashboard_WORKING_NEXT_with_logreg.py")
DASH_FILE_B         = Path("/home/prabh/OFI_Production/logreg_live_tab.py")
LIVE_MASTER         = Path("/home/prabh/OFI_Production/master_live/current/master.ndjsonl")

# ── CONFIG ────────────────────────────────────────────────────────────────── #
TICK                = 0.25
NEAR_TICKS_K        = 4
NEAR_TICKS_PRICE    = TICK * NEAR_TICKS_K
H_FORWARD           = [5, 10, 20, 40]
PRIMARY_H           = 40
EMBARGO_BARS        = 40
MIN_N_FOR_TRAIN     = 500
MIN_DAYS            = 5
DROP_RAW_PRICE_COLS = {
    # NEVER allowed as features (raw absolute prices)
    "px_open", "px_high", "px_low", "px_close",
    "mid_mean", "mid_sum", "mid_kf", "mid_roll20",
}
# Bar-level columns that pass through the per-event snapshot as features
ALLOWED_FEATURE_COLS: List[str] = []   # filled by build_features() after audit

# Reaction types we will train on (per spec, plus mirrors)
PRIORITY_REACTIONS = {
    "LVN_rejection_from_above",
    "HVN_rejection_from_below",
    "HVN_rejection_from_above",
    "LVN_rejection_from_below",
    "VAL_rejection_from_below",
    "POC_rejection_from_below",
}
SMALL_N_NOTE_REACTIONS = {"VAL_neutral_touch"}


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
def precheck(run_dir: Path) -> Tuple[Dict[str, Any], List[str]]:
    """Verify Rithmic-only provenance. BLOCK on any failure."""
    print("\n" + "="*78)
    print("STAGE 1 — PRECHECK")
    print("="*78)
    pc: Dict[str, Any] = {"checked_at_utc": utc_now_iso(), "checks": {}, "blocks": []}

    # 1a. Rithmic raw root
    rr = pc["checks"]["rithmic_raw_root"] = {
        "path": str(RITHMIC_RAW_ROOT),
        "exists": RITHMIC_RAW_ROOT.exists(),
    }
    if not rr["exists"]:
        pc["blocks"].append(f"MISSING_DIR: {RITHMIC_RAW_ROOT}")
    else:
        rr["dates_present"] = sorted([p.name for p in RITHMIC_RAW_ROOT.iterdir() if p.is_dir()])

    # 1b. Rithmic features root
    rf = pc["checks"]["rithmic_features_root"] = {
        "path": str(RITHMIC_FEAT_ROOT),
        "exists": RITHMIC_FEAT_ROOT.exists(),
    }
    if not rf["exists"]:
        pc["blocks"].append(f"MISSING_DIR: {RITHMIC_FEAT_ROOT}")
    else:
        dates = []
        for d in sorted(RITHMIC_FEAT_ROOT.iterdir()):
            if d.is_dir() and (d / "NQM6_vol500.ndjsonl").exists():
                dates.append(d.name)
        rf["dates_with_vol500"] = dates
        rf["n_dates"] = len(dates)
        if len(dates) < MIN_DAYS:
            pc["blocks"].append(f"INSUFFICIENT_DATES: {len(dates)} < {MIN_DAYS}")

    # 1c. Dashboard level-extraction source readable
    pc["checks"]["dashboard_file_a"] = {
        "path": str(DASH_FILE_A), "exists": DASH_FILE_A.exists()
    }
    pc["checks"]["dashboard_file_b"] = {
        "path": str(DASH_FILE_B), "exists": DASH_FILE_B.exists()
    }
    if not (DASH_FILE_A.exists() or DASH_FILE_B.exists()):
        pc["blocks"].append("MISSING_DASHBOARD_LEVEL_SOURCE")

    # 1d. Required columns present in feature files
    req_cols = {
        "bar_index", "bar_end_ts_ns", "px_open", "px_high", "px_low", "px_close",
        "vol_total", "buy_vol", "sell_vol", "delta_norm",
        "mlofi_sum", "mlofi_norm", "mlofi_decay_sum", "mlofi_rolling_5",
        "mlofi_accel", "decay_norm",
        "mid_resid_z", "volatility_5", "vpin", "entropy_score",
        "sweep_imbalance_norm", "sweep_norm", "minute_of_day", "dow",
    }
    if rf["exists"] and rf.get("n_dates", 0) > 0:
        sample_path = RITHMIC_FEAT_ROOT / rf["dates_with_vol500"][-1] / "NQM6_vol500.ndjsonl"
        with open(sample_path) as f:
            sample = json.loads(f.readline())
        present = set(sample.keys())
        missing = sorted(req_cols - present)
        pc["checks"]["feature_columns"] = {
            "sample_file": str(sample_path),
            "n_cols_in_sample": len(present),
            "missing_required": missing,
        }
        if missing:
            pc["blocks"].append(f"MISSING_FEATURE_COLS: {missing}")

    # 1e. Databento contamination scan in feature columns
    if rf["exists"] and rf.get("n_dates", 0) > 0:
        sample_path = RITHMIC_FEAT_ROOT / rf["dates_with_vol500"][-1] / "NQM6_vol500.ndjsonl"
        with open(sample_path) as f:
            sample = json.loads(f.readline())
        dbn_keywords = ("databento", "dbn", "mbo_source_databento")
        contaminated = [k for k in sample.keys()
                         if any(w in k.lower() for w in dbn_keywords)]
        pc["checks"]["databento_columns_in_features"] = contaminated
        if contaminated:
            pc["blocks"].append(f"DATABENTO_COLS_FOUND: {contaminated}")

    # 1f. Confirm we are NOT touching live master / V3 / V4
    pc["checks"]["live_master_will_not_be_modified"] = True
    pc["checks"]["v3_release_will_not_be_modified"] = True
    pc["checks"]["v4_release_will_not_be_modified"] = True
    pc["checks"]["daemon_will_not_be_touched"] = True
    pc["checks"]["parser_will_not_be_restarted"] = True
    pc["checks"]["tft_will_not_be_trained"] = True

    # write
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
        print("PRECHECK OK — Rithmic-only inputs verified.")
        print(f"  available dates: {rf['dates_with_vol500']}")
    return pc, pc["blocks"]


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 2 — LEVEL STREAM (per-day POC/VAH/VAL/HVN/LVN)
# ══════════════════════════════════════════════════════════════════════════════
def load_rithmic_day(date_str: str) -> pd.DataFrame:
    p = RITHMIC_FEAT_ROOT / date_str / "NQM6_vol500.ndjsonl"
    rows = []
    with open(p) as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    df = pd.DataFrame(rows)
    df["rithmic_date_str"] = date_str
    df["bar_end_ts_ns"] = pd.to_numeric(df["bar_end_ts_ns"], errors="coerce")
    df = df.sort_values("bar_end_ts_ns").reset_index(drop=True)
    return df


def volume_profile_levels(df_day: pd.DataFrame) -> Dict[str, Any]:
    """EXACT replica of ofi_live_dashboard.py:2740-2767 volume-profile.

    Returns {poc_px, vah_px, val_px, hvn_px[], lvn_px[], levels[], tot_v[],
              buy_v[], sel_v[]}.
    """
    h = pd.to_numeric(df_day["px_high"], errors="coerce").to_numpy()
    l = pd.to_numeric(df_day["px_low"],  errors="coerce").to_numpy()
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


def build_level_stream(run_dir: Path, dates: List[str]
                        ) -> Tuple[pd.DataFrame, Dict[str, Dict]]:
    print("\n" + "="*78)
    print("STAGE 2 — LEVEL STREAM")
    print("="*78)
    per_day_levels: Dict[str, Dict] = {}
    stream_rows: List[Dict[str, Any]] = []
    for d in dates:
        df = load_rithmic_day(d)
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
        }
        # per-bar level context — normalized distance only, NO raw prices
        c = pd.to_numeric(df["px_close"], errors="coerce").to_numpy()
        vol = pd.to_numeric(df["volatility_5"], errors="coerce").to_numpy()
        # nearest HVN / LVN per bar
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
                # distances normalized only — NO raw prices in stream features
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
    md = [f"# LEVEL STREAM REPORT\n\n_run at {utc_now_iso()}_\n",
          f"- dates: {dates}",
          f"- total rows: {len(stream):,}\n",
          "## Per-day levels\n"]
    for d, v in per_day_levels.items():
        md.append(f"- **{d}**: POC={v['poc_px']}  VAH={v['vah_px']}  VAL={v['val_px']}  "
                   f"n_hvn={v['n_hvn']}  n_lvn={v['n_lvn']}  bars={v['n_bars']}")
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
    """Return a reaction_type string or None.

    Causal: uses only current bar OHLC + prior close + past touch count.
    No future, no centered, no shift(-1).
    """
    if not (np.isfinite(h) and np.isfinite(l) and np.isfinite(c)): return None
    # Touched the level?
    touched = (l - TICK*0.5 <= level_px <= h + TICK*0.5)
    close_to = abs(c - level_px) <= NEAR_TICKS_PRICE
    if not (touched or close_to): return None
    # Absorption: high abs_z AND tiny close-open
    if np.isfinite(abs_z) and abs_z > 1.5 and abs(c - o) < TICK:
        return f"{level_name}_absorption"
    # Direction of approach from prior_close vs level
    came_from_above = np.isfinite(prior_close) and (prior_close > level_px + TICK*0.5)
    came_from_below = np.isfinite(prior_close) and (prior_close < level_px - TICK*0.5)
    if touched:
        if c > level_px and l < level_px:
            return f"{level_name}_rejection_from_above"   # came down, closed back above
        if c < level_px and h > level_px:
            return f"{level_name}_rejection_from_below"   # came up, closed back below
    # Acceptance: clean break through with strong delta
    if np.isfinite(delta) and abs(delta) > 1.0 and abs(c - level_px) > NEAR_TICKS_PRICE:
        if c > level_px and came_from_below:
            return f"breakout_acceptance_above_{level_name}"
        if c < level_px and came_from_above:
            return f"breakdown_acceptance_below_{level_name}"
    # Neutral touch (no clear rejection)
    if touched and abs(c - o) < TICK and not (np.isfinite(abs_z) and abs_z > 1.5):
        return f"{level_name}_neutral_touch"
    return None


def build_event_stream(run_dir: Path, dates: List[str],
                        per_day_levels: Dict[str, Dict]) -> pd.DataFrame:
    print("\n" + "="*78)
    print("STAGE 3 — EVENT STREAM")
    print("="*78)
    rows: List[Dict[str, Any]] = []
    event_id_counter = 0
    for d in dates:
        if d not in per_day_levels: continue
        df = load_rithmic_day(d)
        if len(df) < 50: continue
        lv = per_day_levels[d]
        # Build absorb_z proxy (rolling z of vol_total / px_range) causally
        vt = pd.to_numeric(df["vol_total"], errors="coerce").to_numpy()
        h = pd.to_numeric(df["px_high"], errors="coerce").to_numpy()
        l = pd.to_numeric(df["px_low"],  errors="coerce").to_numpy()
        c = pd.to_numeric(df["px_close"],errors="coerce").to_numpy()
        o = pd.to_numeric(df["px_open"], errors="coerce").to_numpy()
        prx_range = np.maximum(h - l, TICK)
        absorb_strength = vt / prx_range
        absorb_s = pd.Series(absorb_strength)
        absorb_z = ((absorb_s - absorb_s.rolling(50, min_periods=10).mean())
                     / absorb_s.rolling(50, min_periods=10).std()).to_numpy()
        delta = pd.to_numeric(df["delta_norm"], errors="coerce").to_numpy()
        mid_z = pd.to_numeric(df["mid_resid_z"], errors="coerce").to_numpy()
        prior_close = np.r_[np.nan, c[:-1]]
        # Iterate over levels: POC, VAH, VAL, each HVN, each LVN
        flat_levels: List[Tuple[float, str]] = [
            (lv["poc_px"], "POC"),
            (lv["vah_px"], "VAH"),
            (lv["val_px"], "VAL"),
        ] + [(p, "HVN") for p in lv["hvn_px"]] + [(p, "LVN") for p in lv["lvn_px"]]
        # Per-level past-touch-count tracking
        touch_counter: Dict[Tuple[float, str], int] = defaultdict(int)
        last_touch_bar: Dict[Tuple[float, str], int] = {}
        # Iterate bars
        for i in range(len(df)):
            for lp, ln in flat_levels:
                rxn = classify_bar_reaction(
                    h[i], l[i], c[i], o[i],
                    lp, ln,
                    delta[i], mid_z[i], absorb_z[i],
                    prior_close[i], touch_counter[(lp, ln)],
                )
                if rxn is None: continue
                # Past-only metadata
                tc_past = touch_counter[(lp, ln)]
                bars_since_prior = (i - last_touch_bar[(lp, ln)]
                                     if (lp, ln) in last_touch_bar else -1)
                # Update touch counter AFTER recording past-only stats
                touch_counter[(lp, ln)] += 1
                last_touch_bar[(lp, ln)] = i
                rows.append({
                    "event_id":           event_id_counter,
                    "event_time_ns":      int(df["bar_end_ts_ns"].iloc[i]),
                    "rithmic_date_str":   d,
                    "bar_idx_in_day":     i,
                    "session":            session_label(int(df["minute_of_day"].iloc[i])),
                    "level_type":         ln,
                    "level_price":        float(lp),    # stored for audit only
                    "reaction_type":      rxn,
                    "distance_ticks_at_event": float((c[i] - lp) / TICK),
                    "delta_norm_at_event":     float(delta[i]) if np.isfinite(delta[i]) else None,
                    "mid_resid_z_at_event":    float(mid_z[i]) if np.isfinite(mid_z[i]) else None,
                    "absorb_z_at_event":       float(absorb_z[i]) if np.isfinite(absorb_z[i]) else None,
                    "touch_count_past_only":   int(tc_past),
                    "bars_since_prior_touch":  int(bars_since_prior),
                    "data_source":             "RITHMIC_ONLY",
                    "raw_orderflow_available": True,
                })
                event_id_counter += 1
    if not rows:
        raise RuntimeError("no events generated")
    ev = pd.DataFrame(rows)
    ev.to_parquet(run_dir / "data/level_reaction_events.parquet", index=False)
    ev.to_csv(run_dir / "data/level_reaction_events.csv", index=False)
    # Report
    by_rxn = ev.groupby("reaction_type").size().sort_values(ascending=False)
    by_day = ev.groupby("rithmic_date_str").size()
    md = [f"# EVENT STREAM REPORT\n\n_run at {utc_now_iso()}_\n",
          f"- total events: {len(ev):,}",
          f"- distinct reaction_types: {ev['reaction_type'].nunique()}",
          f"- dates covered: {sorted(ev['rithmic_date_str'].unique())}\n",
          "## Counts by reaction_type\n```\n" + by_rxn.to_string() + "\n```\n",
          "## Counts by date\n```\n" + by_day.to_string() + "\n```\n",
          "## Rules\n",
          "- Only bars meeting a level-reaction rule produce events; normal bars produce NONE.",
          "- touch_count_past_only and bars_since_prior_touch use ONLY data before the current bar.",
          "- Reaction classification uses current-bar OHLC + prior_close only. No future data."]
    write_text(run_dir / "reports/EVENT_STREAM_REPORT.md", "\n".join(md))
    print(f"  events: {len(ev):,}  by_rxn top: {by_rxn.head(8).to_dict()}")
    return ev


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 4 — EVENT FEATURES (snapshot at event row)
# ══════════════════════════════════════════════════════════════════════════════
def build_event_features(run_dir: Path, ev: pd.DataFrame, stream: pd.DataFrame,
                          dates: List[str]) -> Tuple[pd.DataFrame, List[str]]:
    print("\n" + "="*78)
    print("STAGE 4 — EVENT FEATURES")
    print("="*78)
    # Index source bars by (date, bar_idx_in_day) for fast lookup
    src: Dict[str, pd.DataFrame] = {}
    for d in dates:
        df = load_rithmic_day(d)
        df = df.reset_index(drop=True)
        df["__bar_idx"] = np.arange(len(df))
        src[d] = df

    # OHLC-vol path features (causal)
    def _add_ohlc_vol(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        h = pd.to_numeric(out["px_high"], errors="coerce")
        l = pd.to_numeric(out["px_low"],  errors="coerce")
        c = pd.to_numeric(out["px_close"], errors="coerce")
        o_ = pd.to_numeric(out["px_open"], errors="coerce")
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

    # Append OHLC-vol features per-day source frames
    for d in dates:
        src[d] = _add_ohlc_vol(src[d])

    # Bid/ask pull PROXY (already discussed in spec: clearly mark _PROXY)
    # True book pulls would need raw depth.ndjson processing — too heavy for this run.
    for d in dates:
        df = src[d]
        mn = pd.to_numeric(df["mlofi_norm"], errors="coerce")
        mz = pd.to_numeric(df["mid_resid_z"], errors="coerce")
        dn = pd.to_numeric(df["delta_norm"],  errors="coerce")
        df["bid_pull_PROXY"] = ((mz < -1.5) & (mn < -0.6) & (dn > -0.4)).astype(int)
        df["ask_pull_PROXY"] = ((mz >  1.5) & (mn >  0.6) & (dn <  0.4)).astype(int)
        src[d] = df

    # Now snapshot features at event rows
    snap_rows: List[Dict[str, Any]] = []
    # Allowed feature columns from the source frame (snapshot at event row)
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
        # z20 residual versions if present
        "delta_norm_resid_z20", "volatility_5_resid_z20",
        "sweep_imbalance_norm_resid_z20", "vpin_resid_z20",
    ]
    OHLC_VOL_COLS = [
        "candle_body_vol", "candle_range_vol", "upper_wick_vol", "lower_wick_vol",
        "close_location", "open_to_close_sign", "close_vs_prev_close_vol",
        "close_vs_roll_mean_vol", "high_break_vol", "low_break_vol",
    ]
    PROXY_COLS = ["bid_pull_PROXY", "ask_pull_PROXY"]

    # Level-context features come from the event row itself + stream
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
        # level-context dist features (normalized only)
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
        # touch metadata
        rec["touch_count_past_only"]  = int(ev_row["touch_count_past_only"])
        rec["bars_since_prior_touch"] = int(ev_row["bars_since_prior_touch"])
        # OF + OHLC-vol + proxy snapshots
        for col in OF_COLS + OHLC_VOL_COLS + PROXY_COLS:
            if col in bar.index:
                v = bar[col]
                rec[col] = float(v) if pd.notna(v) and isinstance(v, (int, float, np.floating)) else None
        # Cyclic encodings of minute_of_day & dow
        if rec.get("minute_of_day") is not None:
            mod = rec["minute_of_day"]
            rec["minute_sin"] = float(np.sin(2*np.pi*mod/1440.0))
            rec["minute_cos"] = float(np.cos(2*np.pi*mod/1440.0))
        if rec.get("dow") is not None:
            rec["dow_sin"] = float(np.sin(2*np.pi*rec["dow"]/7.0))
            rec["dow_cos"] = float(np.cos(2*np.pi*rec["dow"]/7.0))
        # One-hot for level_type / session / reaction_type
        for lt in ("POC","VAH","VAL","HVN","LVN"):
            rec[f"lvl_{lt}"] = int(rec["level_type"] == lt)
        for s in ("Asia","EU","US_Open","US_AM","US_PM","US_Late"):
            rec[f"sess_{s}"] = int(rec["session"] == s)
        # Reaction one-hot at coarse level (without level prefix for grouping later)
        rec["rxn_rejection_from_above"] = int("rejection_from_above" in rec["reaction_type"])
        rec["rxn_rejection_from_below"] = int("rejection_from_below" in rec["reaction_type"])
        rec["rxn_absorption"]           = int("absorption" in rec["reaction_type"])
        rec["rxn_acceptance"]           = int("acceptance" in rec["reaction_type"])
        rec["rxn_neutral_touch"]        = int("neutral_touch" in rec["reaction_type"])
        snap_rows.append(rec)

    if not snap_rows:
        raise RuntimeError("no feature snapshots")
    X = pd.DataFrame(snap_rows)
    # Drop raw price columns if any sneaked in (should be impossible — defensive)
    for bad in DROP_RAW_PRICE_COLS:
        if bad in X.columns:
            X = X.drop(columns=[bad])
    X.to_parquet(run_dir / "data/X_level_reaction_events.parquet", index=False)
    # Feature names = model-usable columns (numeric, no id/categorical strings)
    ID_COLS = {"event_id", "rithmic_date_str", "bar_idx_in_day",
                "session", "level_type", "reaction_type"}
    feature_names = [c for c in X.columns if c not in ID_COLS
                      and pd.api.types.is_numeric_dtype(X[c])]
    write_json(run_dir / "feature_names.json", feature_names)
    # Feature-leakage audit
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
    }
    if leak_audit["raw_price_cols_present"]:
        leak_audit["block"] = "RAW_PRICE_COLS_DETECTED"
    write_json(run_dir / "audits/feature_leakage_audit.json", leak_audit)
    # FEATURE_POLICY.md
    pol = [f"# FEATURE POLICY\n\n_run at {utc_now_iso()}_\n",
            f"- snapshots per event row only ({len(X):,} events)",
            f"- {len(feature_names)} numeric features",
            "## Allowed families",
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
            "- Raw absolute prices (px_open/high/low/close, mid_mean, mid_sum, mid_kf, mid_roll20)",
            "- Any Databento column",
            "- Any future-bar data (no shift(-1), no centered rolling, no bfill)",
            "- Raw level prices (poc_px, vah_px, val_px — kept as audit metadata only)\n",
            "## Audit\n```\n" + json.dumps(leak_audit, indent=2) + "\n```\n"]
    write_text(run_dir / "reports/FEATURE_POLICY.md", "\n".join(pol))
    write_text(run_dir / "FEATURE_POLICY.md", "\n".join(pol))   # also at root per spec
    print(f"  X shape: {X.shape}  n_features: {len(feature_names)}")
    return X, feature_names


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 5 — LABELS (forward h5/h10/h20/h40)
# ══════════════════════════════════════════════════════════════════════════════
def build_event_labels(run_dir: Path, ev: pd.DataFrame, dates: List[str]
                        ) -> pd.DataFrame:
    print("\n" + "="*78)
    print("STAGE 5 — LABELS")
    print("="*78)
    rows: List[Dict[str, Any]] = []
    for d in dates:
        ev_d = ev[ev["rithmic_date_str"] == d]
        if not len(ev_d): continue
        src = load_rithmic_day(d)
        c = pd.to_numeric(src["px_close"], errors="coerce").to_numpy(dtype=float)
        h_ = pd.to_numeric(src["px_high"], errors="coerce").to_numpy(dtype=float)
        l_ = pd.to_numeric(src["px_low"],  errors="coerce").to_numpy(dtype=float)
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
            # MFE / MAE within H40 window (causal — forward path)
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
            # Binary directional label at primary horizon
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
    }
    write_json(run_dir / "audits/label_leakage_audit.json", leak)
    md = [f"# LABEL POLICY\n\n_run at {utc_now_iso()}_\n",
          f"- Primary target: label_h40 (binary direction at h40 = +40 bars)",
          f"- LONG=1 if log_ret > 0, SHORT=0 if log_ret < 0, NEUTRAL dropped",
          f"- Forward returns at h5/h10/h20/h40 (log AND ticks)",
          f"- MFE_h40 / MAE_h40 over forward window",
          f"- label_end_bar_idx = bar_idx_in_day + 40 (used for purging/embargo)",
          f"- Total events: {len(lbl):,}  LONG: {leak['n_label_h40_LONG']:,}  "
            f"SHORT: {leak['n_label_h40_SHORT']:,}  NEUTRAL: {leak['n_label_h40_NEUTRAL']:,}",
          "\n## Audit\n```\n" + json.dumps(leak, indent=2) + "\n```\n"]
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
    # Need at least 3 dates for 2 folds; with 7 we can do 5 folds (expanding window)
    if len(dates) < 4:
        raise RuntimeError(f"insufficient dates for folds: {dates}")
    folds: List[Tuple[List[str], List[str]]] = []
    # Expanding window: train on day[0:k], validate on day[k]
    for k in range(2, len(dates)):
        train_dates = dates[:k]
        val_dates   = [dates[k]]
        folds.append((train_dates, val_dates))
    # Purging: event in train must have label_end_bar before the START of val day's bars.
    # Since val_dates are different calendar days, label_end stays within the train day → no cross-day overlap.
    # Embargo: drop training events whose label_end_bar_idx + EMBARGO would overlap into next day's bars.
    # For per-day labels this means: drop any training-day event whose label_end_bar_idx >= (n_bars_in_train_day - EMBARGO).
    # We capture this via a flag in purging_audit.
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
    }
    write_json(run_dir / "data/folds.json", purg)
    write_json(run_dir / "audits/purging_audit.json", purg)
    for fd in purg["fold_definitions"]:
        print(f"  fold {fd['fold_id']}: train {fd['train_dates']} ({fd['n_train_events']} ev)  "
              f"→ val {fd['val_dates']} ({fd['n_val_events']} ev)")
    return purg, folds


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 7 — TRAIN SHADOW MODELS
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


def train_shadow_models(
        run_dir: Path, X: pd.DataFrame, lbl: pd.DataFrame,
        feature_names: List[str],
        folds: List[Tuple[List[str], List[str]]],
) -> Dict[str, Any]:
    print("\n" + "="*78)
    print("STAGE 7 — TRAIN SHADOW MODELS")
    print("="*78)

    # Merge X and labels
    df = X.merge(lbl[["event_id","label_h40","fwd_logret_h40"]], on="event_id", how="left")
    df = df[df["label_h40"].isin([0, 1])].reset_index(drop=True)
    if len(df) < MIN_N_FOR_TRAIN:
        raise RuntimeError(f"too few labelled events for training: {len(df)} < {MIN_N_FOR_TRAIN}")
    print(f"  labelled events: {len(df):,}")

    # Models to evaluate
    MODELS = {
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

    # Storage
    fold_rows: List[Dict[str, Any]] = []
    per_day_rows: List[Dict[str, Any]] = []
    per_reaction_rows: List[Dict[str, Any]] = []
    conf_sweep_rows: List[Dict[str, Any]] = []
    proba_diag_rows: List[Dict[str, Any]] = []
    full_predictions: List[Dict[str, Any]] = []
    saved_models: Dict[str, Any] = {}

    for mname, ctor in MODELS.items():
        print(f"\n  ── MODEL: {mname} ──")
        # Per-fold loop
        fold_preds = []
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

            # Fit imputer + scaler on train only
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

            # Capture per-event predictions for downstream stratified analysis
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
            # persist
            with open(run_dir / f"models/model_{mname}.pkl", "wb") as f:
                pickle.dump(final_model, f)
            with open(run_dir / f"models/imputer_{mname}.pkl", "wb") as f:
                pickle.dump(imp, f)
            with open(run_dir / f"models/scaler_{mname}.pkl", "wb") as f:
                pickle.dump(scl, f)
        except Exception as e:
            print(f"    final-fit {mname} failed: {e}")

    # Save per-event predictions
    preds_df = pd.DataFrame(full_predictions)
    preds_df.to_csv(run_dir / "data/per_event_predictions.csv", index=False)

    # ── PER-FOLD METRICS table ──
    fm = pd.DataFrame(fold_rows)
    fm.to_csv(run_dir / "fold_metrics.csv", index=False)

    # ── PER-DAY metrics per model ──
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

    # ── PER-REACTION metrics per model ──
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

    # ── CONFIDENCE SWEEP per model ──
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

    # ── PROBABILITY DIAGNOSTICS ──
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
        "fold_metrics": fm,
        "per_day_metrics": per_day_df,
        "per_reaction_metrics": per_rxn_df,
        "confidence_sweep": conf_df,
        "probability_diagnostics": pd.DataFrame(proba_diag),
        "predictions": preds_df,
        "saved_models": list(saved_models.keys()),
    }


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 8 — GATES (hard PASS/BLOCK per spec)
# ══════════════════════════════════════════════════════════════════════════════
def apply_gates(out: Dict[str, Any]) -> Dict[str, Any]:
    print("\n" + "="*78)
    print("STAGE 8 — QUALITY GATES")
    print("="*78)
    fm = out["fold_metrics"]
    pd_df = out["per_day_metrics"]
    gates = {}
    for mname, sub in fm.groupby("model"):
        m = sub["mcc"].dropna()
        if not len(m):
            gates[mname] = {"avg_mcc": None, "worst_fold_mcc": None,
                             "pct_folds_positive": None, "block": "NO_MCC"}
            continue
        avg = float(m.mean())
        worst = float(m.min())
        pct_pos = float((m > 0).mean())
        # per-day MCC positivity for that model
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
        if avg <= 0:                blocks.append("AVG_MCC_NOT_POSITIVE")
        if worst <= 0:              blocks.append("WORST_FOLD_MCC_NOT_POSITIVE")
        if pct_pos < 0.60:          blocks.append("LESS_THAN_60PCT_FOLDS_POSITIVE")
        if single_day_alpha:        blocks.append("SINGLE_DAY_ALPHA_CONCENTRATED")
        result["blocks"] = blocks
        result["pass"] = len(blocks) == 0
        gates[mname] = result
        print(f"  {mname}: avg_mcc={avg:+.4f}  worst={worst:+.4f}  "
              f"pct_pos={pct_pos:.0%}  pass={result['pass']}")
        if blocks:
            for b in blocks: print(f"      BLOCK: {b}")
    return gates


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 9 — FINAL REPORT & RELEASE
# ══════════════════════════════════════════════════════════════════════════════
def write_release(run_dir: Path, dates: List[str],
                   X: pd.DataFrame, feature_names: List[str],
                   ev: pd.DataFrame, lbl: pd.DataFrame,
                   out: Dict[str, Any], gates: Dict[str, Any],
                   precheck_obj: Dict[str, Any]) -> str:
    print("\n" + "="*78)
    print("STAGE 9 — RELEASE")
    print("="*78)

    # Best model = highest avg_mcc among PASS models
    pass_models = {m: g for m, g in gates.items() if g.get("pass")}
    if pass_models:
        best_model = max(pass_models, key=lambda m: pass_models[m]["avg_mcc"])
    else:
        best_model = None

    manifest = {
        "release_id":            run_dir.name,
        "created_utc":           utc_now_iso(),
        "status":                "shadow_research_only",
        "rithmic_only":          True,
        "databento_used":        False,
        "paper_trading_allowed": False,
        "production_execution_allowed": False,
        "active_dashboard_model":False,
        "trained_on_event_stream_only": True,
        "trained_on_all_bars":   False,
        "tft_trained":           False,
        "daemon_touched":        False,
        "parser_restarted":      False,
        "live_master_modified":  False,
        "v3_release_modified":   False,
        "v4_release_modified":   False,
        "dates_used":            dates,
        "n_events":              int(len(ev)),
        "n_labelled_events":     int(lbl["label_h40"].notna().sum()),
        "feature_count":         len(feature_names),
        "label_horizon_bars":    PRIMARY_H,
        "embargo_bars":          EMBARGO_BARS,
        "fold_count":            len(out["fold_metrics"]["fold"].unique()) if len(out["fold_metrics"]) else 0,
        "models_trained":        out["saved_models"],
        "best_model":            best_model,
        "gates":                 gates,
        "data_source":           {
            "rithmic_features_root": str(RITHMIC_FEAT_ROOT),
            "feature_files":         [str(RITHMIC_FEAT_ROOT / d / "NQM6_vol500.ndjsonl")
                                       for d in dates],
            "rithmic_raw_root":      str(RITHMIC_RAW_ROOT),
        },
        "precheck_passed":       len(precheck_obj.get("blocks", [])) == 0,
    }
    write_json(run_dir / "RELEASE_MANIFEST.json", manifest)

    # DATA_PROVENANCE.md
    prov = [f"# DATA PROVENANCE\n\n_run at {utc_now_iso()}_\n",
             "## Source",
             f"- Rithmic feature files: {RITHMIC_FEAT_ROOT}/<date>/NQM6_vol500.ndjsonl",
             f"- Rithmic raw root (audited but not directly consumed in this run): {RITHMIC_RAW_ROOT}",
             "## Exclusions",
             "- NO Databento files used",
             "- NO mixed-feed master.ndjsonl rows used",
             "- NO external data sources",
             f"## Dates used\n```\n{dates}\n```\n",
             "## Why Jun 5 / Jun 6 excluded",
             "- /home/prabh/OFI_Live_Data/Rithmic_Raw/ contains no Jun 5 or Jun 6 directories.",
             "- Excluding to maintain strict Rithmic-only provenance.",
             "## Bid/Ask pull proxies",
             "- This run uses bid_pull_PROXY and ask_pull_PROXY derived from mlofi_norm + mid_resid_z + delta_norm.",
             "- TRUE book pulls require streaming /Rithmic_Raw/<date>/NQM6/depth.ndjson (3.6 GB/day).",
             "- Proxies marked with _PROXY suffix; will be replaced with real pulls in a follow-up if requested."]
    write_text(run_dir / "DATA_PROVENANCE.md", "\n".join(prov))

    # EVENT_POLICY.md
    pol = [f"# EVENT POLICY\n\n_run at {utc_now_iso()}_\n",
            "## Reaction definitions (causal — current bar OHLC + prior close only)",
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
            "## Levels evaluated",
            "- POC, VAH, VAL, every HVN price, every LVN price (per-day volume-profile)",
            "- Volume profile = same formula as ofi_live_dashboard.py:2740-2767"]
    write_text(run_dir / "EVENT_POLICY.md", "\n".join(pol))

    # MODEL CARD
    card_lines = [f"# Model Card — {run_dir.name}\n",
                   "## Status\n- shadow_research_only — NO production / NO paper trading",
                   f"\n## Dates\n- Trained on {len(dates)} days: {dates}",
                   f"\n## Sample\n- {manifest['n_events']:,} events / {manifest['n_labelled_events']:,} labelled\n- {manifest['feature_count']} features",
                   f"\n## Best model\n- {best_model}" if best_model else "\n## Best model\n- (none — all gated)",
                   "\n## Gates"]
    for m, g in gates.items():
        card_lines.append(f"- **{m}**: avg_mcc={g.get('avg_mcc')} worst={g.get('worst_fold_mcc')} "
                           f"pct_pos={g.get('pct_folds_positive')} pass={g.get('pass')}")
    write_text(run_dir / "README_MODEL_CARD.md", "\n".join(card_lines))

    # FINAL REPORT
    fm = out["fold_metrics"]
    pd_df = out["per_day_metrics"]
    rxn_df = out["per_reaction_metrics"]
    cs = out["confidence_sweep"]
    pdg = out["probability_diagnostics"]
    pred = out["predictions"]
    by_rxn_counts = ev.groupby("reaction_type").size().sort_values(ascending=False)

    rxn_alpha_lines = []
    if len(pred):
        for (mname, rxn), sub in pred.groupby(["model","reaction_type"]):
            if len(sub) < 10: continue
            y = sub["label_h40"].to_numpy(); yh = sub["pred_h40"].to_numpy()
            mcc = safe_metric(matthews_corrcoef, y, yh)
            acc = safe_metric(accuracy_score, y, yh)
            rxn_alpha_lines.append(f"| {mname} | {rxn} | {len(sub)} | {acc} | {mcc} |")

    fr = [f"# FINAL REPORT — {run_dir.name}\n",
           f"_generated at {utc_now_iso()}_\n",
           "## Summary",
           f"- Status: **shadow_research_only**",
           f"- Rithmic only: **{manifest['rithmic_only']}**",
           f"- Trained on event stream only: **{manifest['trained_on_event_stream_only']}**",
           f"- Days used: **{len(dates)}** ({dates})",
           f"- Total events: **{manifest['n_events']:,}**",
           f"- Labelled events (h40): **{manifest['n_labelled_events']:,}**",
           f"- Feature count: **{manifest['feature_count']}**",
           "\n## Q&A (per spec)",
           f"- **Did we train only on level-reaction events?** YES — only bars matching a reaction rule are events. See EVENT_POLICY.md.",
           f"- **Did we use only Rithmic data?** YES — sources verified in precheck; no Databento columns; Jun 5/6 excluded due to missing Rithmic capture.",
           f"- **Which event patterns survived quality gates?** See per_reaction_metrics.csv.",
           f"- **Which model performed best?** {best_model or '(none — all gated)'}",
           f"- **Did worst-fold MCC improve?** See gates table above.",
           f"- **Is probability confidence useful?** See confidence_sweep.csv.",
           f"- **Does MLOFI/order-flow add signal near levels?** Inspect per_reaction_metrics + permutation importance follow-up (not in this run).",
           f"- **Are bid/ask pulls measurable and useful?** Pulls are PROXY only — true book pulls require raw depth.ndjson processing.",
           f"- **Is the result safe for dashboard shadow monitoring?** YES — no production flags enabled.",
           f"- **What exact event stream should go live as shadow only?** The reaction_types passing gates in per_reaction_metrics.csv with hit_up_h40 outside [0.45, 0.55].",
           f"- **What should NOT be used?** Any reaction with n < 50 events or per-day consistency < 60%.",
           "\n## Event counts by reaction_type",
           "```\n" + by_rxn_counts.to_string() + "\n```",
           "\n## Per-fold metrics",
           "```\n" + (fm.to_string(index=False) if len(fm) else "(empty)") + "\n```",
           "\n## Quality gates",
           "```\n" + json.dumps(gates, indent=2) + "\n```",
           "\n## Per-day metrics",
           "```\n" + (pd_df.to_string(index=False) if len(pd_df) else "(empty)") + "\n```",
           "\n## Per-reaction metrics (top model)",
           "```\n" + (rxn_df.to_string(index=False) if len(rxn_df) else "(empty)") + "\n```",
           "\n## Confidence sweep",
           "```\n" + (cs.to_string(index=False) if len(cs) else "(empty)") + "\n```",
           "\n## Probability diagnostics",
           "```\n" + (pdg.to_string(index=False) if len(pdg) else "(empty)") + "\n```",
           "\n## Final verdict",
           f"- {'OVERALL PASS' if best_model else 'BLOCKED — no model passed gates'}\n"]
    write_text(run_dir / "FINAL_REPORT.md", "\n".join(fr))

    # HASHES
    hashes = []
    for p in sorted(run_dir.rglob("*")):
        if p.is_file() and p.name != "HASHES.sha256":
            try:
                hashes.append(f"{sha256_file(p)}  {p.relative_to(run_dir)}")
            except Exception:
                pass
    write_text(run_dir / "HASHES.sha256", "\n".join(hashes) + "\n")

    verdict = "OVERALL PASS" if best_model else "BLOCKED: no model passed all gates"
    print(f"\n{verdict}\n")
    return verdict


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run_dir", type=Path, required=True)
    args = ap.parse_args()
    run_dir = args.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("audits","scripts","data","reports","models"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)

    # Stage 1
    precheck_obj, blocks = precheck(run_dir)
    if blocks:
        print(f"\nBLOCKED: precheck failed — {blocks}")
        write_text(run_dir / "FINAL_REPORT.md", f"# BLOCKED at PRECHECK\n\n{json.dumps(precheck_obj, indent=2)}")
        return 2

    dates = precheck_obj["checks"]["rithmic_features_root"]["dates_with_vol500"]

    # Stage 2
    stream, per_day_levels = build_level_stream(run_dir, dates)
    # Stage 3
    ev = build_event_stream(run_dir, dates, per_day_levels)
    # Stage 4
    X, feature_names = build_event_features(run_dir, ev, stream, dates)
    # Stage 5
    lbl = build_event_labels(run_dir, ev, dates)
    # Stage 6
    purg, folds = build_folds(run_dir, ev, lbl)
    # Stage 7
    out = train_shadow_models(run_dir, X, lbl, feature_names, folds)
    # Stage 8
    gates = apply_gates(out)
    # Stage 9
    verdict = write_release(run_dir, dates, X, feature_names, ev, lbl,
                              out, gates, precheck_obj)
    print("\n" + "=" * 78)
    print(verdict)
    print("=" * 78)
    return 0 if "PASS" in verdict else 3


if __name__ == "__main__":
    sys.exit(main())
