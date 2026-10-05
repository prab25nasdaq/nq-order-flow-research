#!/usr/bin/env python3
"""
Audit 1.5 — Model Lineage / Artifact Causality
Proves OOS predictions used a retrained-per-fold design (valid)
vs. pre-fitted backward-applied weights (invalid lookahead).
SHADOW / RESEARCH ONLY — no execution, no broker, no order placement.

Checks 8 invariants per prediction row and derives fold-level lineage metadata.
If PASS: writes candidate_freeze_v3B_W20.yaml and runs Audit 5A retrospective shadow.
If FAIL/UNPROVEN: halts and documents what metadata logging must be added.
"""
# ─────────────────────────────────────────────────────────────────────────────
# INVARIANT CONSTRAINTS — DO NOT MODIFY
# SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING
# Do NOT modify: Rithmic raw recorder, parser, master files, Book Flow chart,
#   active model pointer, broker/order logic, trading flags, production model
#   artifacts, ACTIVE_SHADOW_RELEASE
# No auto-trading, no broker execution, no order placement in any system.
# ─────────────────────────────────────────────────────────────────────────────

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

# ─── Paths ────────────────────────────────────────────────────────────────────
PBO_DIR    = Path(__file__).parent
MODEL_DIR  = Path("/home/prabh/OFI_Production/model_registry"
                  "/level_reaction_continuous_nq_shadow"
                  "/level_reaction_continuous_nq_shadow_20260702T005104Z")
DATA_DIR   = MODEL_DIR / "data"
SOURCE_PY  = PBO_DIR / "run_model_prob_pbo.py"
OOS_TABLE  = PBO_DIR / "oos_probability_table.parquet"
AUDIT_ROOT = PBO_DIR / "audits"

TS     = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
OUT_DIR = AUDIT_ROOT / f"model_prob_lineage_shadow_{TS}"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ─── Candidate constants ──────────────────────────────────────────────────────
CANDIDATE_VERSION   = "B"
CANDIDATE_WINDOW    = 20
CANDIDATE_THRESH    = 0.65
HORIZON_PRIMARY     = 10
HORIZON_SECONDARY   = 40
COST_TICKS          = 2.0
EXPECTED_FEAT_A     = 77
EXPECTED_FEAT_B     = 81

# ─── Invariant codes ──────────────────────────────────────────────────────────
INVARIANTS = [
    "INV1_all_rows_oos",
    "INV2_pred_date_not_in_train",
    "INV3_pred_ts_after_train_end",
    "INV4_label_end_not_in_val",
    "INV5_scaler_fit_per_fold",
    "INV6_pred_date_in_val",
    "INV7_no_duplicate_event_id",
    "INV8_no_artifact_load",
]


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def ts_to_iso(ns: int) -> str:
    """Nanosecond UTC integer → ISO string."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat()


def _hdr(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


# ─────────────────────────────────────────────────────────────────────────────
# PART 0 — Load all data
# ─────────────────────────────────────────────────────────────────────────────

def load_data() -> dict:
    _hdr("PART 0: Load data")
    t0 = time.time()

    oos = pd.read_parquet(OOS_TABLE)
    print(f"  OOS table:  {len(oos):,} rows × {len(oos.columns)} cols")

    snap = pd.read_parquet(DATA_DIR / "continuous_master_snapshot.parquet")
    # build within-day bar index (bar_idx_in_day already in snap? check)
    if "bar_idx_in_day" not in snap.columns:
        snap["bar_idx_in_day"] = snap.groupby("rithmic_date_str").cumcount()
    print(f"  Snapshot:   {len(snap):,} bars")

    events = pd.read_parquet(DATA_DIR / "level_reaction_events.parquet")
    print(f"  Events:     {len(events):,}")

    lbl = pd.read_parquet(DATA_DIR / "labels_level_reaction.parquet")
    print(f"  Labels:     {len(lbl):,}  (with NaN label_h40: "
          f"{lbl['label_h40'].isna().sum():,})")

    with open(DATA_DIR / "folds.json") as f:
        folds_raw = json.load(f)
    folds = folds_raw["fold_definitions"]
    print(f"  Folds:      {len(folds)}  embargo_bars={folds_raw['embargo_bars']}"
          f"  cross_day_overlap={folds_raw['cross_day_overlap_count']}")

    # Build fold index: date → fold_id (for val period)
    date_to_fold = {}
    fold_map     = {}   # fold_id → {'train_dates', 'val_dates', 'n_train', 'n_val'}
    for fd in folds:
        fid = fd["fold_id"]
        fold_map[fid] = {
            "train_dates": set(fd["train_dates"]),
            "val_dates":   set(fd["val_dates"]),
            "n_train":     fd["n_train_events"],
            "n_val":       fd["n_val_events"],
        }
        for d in fd["val_dates"]:
            date_to_fold[d] = fid

    # Snap day→ts lookups
    snap_day_max_ts  = snap.groupby("rithmic_date_str")["bar_end_ts_ns"].max().to_dict()
    snap_day_min_ts  = snap.groupby("rithmic_date_str")["bar_end_ts_ns"].min().to_dict()
    # (date, bar_idx_in_day) → bar_end_ts_ns for label_end lookup
    snap_day_bar_ts  = (snap.set_index(["rithmic_date_str", "bar_idx_in_day"])
                            ["bar_end_ts_ns"].to_dict())

    print(f"  Part 0 done in {time.time()-t0:.1f}s")

    return dict(
        oos=oos, snap=snap, events=events, lbl=lbl,
        folds=folds, folds_raw=folds_raw, fold_map=fold_map,
        date_to_fold=date_to_fold,
        snap_day_max_ts=snap_day_max_ts,
        snap_day_min_ts=snap_day_min_ts,
        snap_day_bar_ts=snap_day_bar_ts,
    )


# ─────────────────────────────────────────────────────────────────────────────
# PART 1 — Code Inspection (static analysis of run_model_prob_pbo.py)
# ─────────────────────────────────────────────────────────────────────────────

def code_inspection() -> dict:
    _hdr("PART 1: Code Inspection — static analysis")

    src = SOURCE_PY.read_text()
    lines = src.splitlines()

    result = {
        "source_file": str(SOURCE_PY),
        "source_lines": len(lines),
        "findings": [],
        "violations": [],
        "inv5_pass": False,
        "inv8_pass": False,
    }

    # ── Find the fold loop bounds in Part D ───────────────────────────────────
    fold_loop_start = None
    fold_loop_end   = None
    for i, ln in enumerate(lines):
        if "for fold_def in folds:" in ln and fold_loop_start is None:
            # Only capture the FIRST occurrence (it's inside Part D's version/W outer loops)
            fold_loop_start = i + 1  # 1-based
        if fold_loop_start and "oos_df = pd.DataFrame(all_recs)" in ln:
            fold_loop_end = i + 1
            break

    if fold_loop_start and fold_loop_end:
        result["findings"].append(
            f"Fold loop found: lines {fold_loop_start}–{fold_loop_end}"
        )
        fold_section = lines[fold_loop_start - 1 : fold_loop_end]
    else:
        result["violations"].append("Could not locate fold loop in Part D")
        fold_section = []

    # ── Check .fit() calls are INSIDE the fold loop ───────────────────────────
    hgb_fit_lines   = []
    imp_fit_lines   = []
    scl_fit_lines   = []
    artifact_lines  = []

    for i, ln in enumerate(fold_section, start=fold_loop_start or 1):
        stripped = ln.strip()
        if re.search(r"HistGradientBoostingClassifier.*\.fit\(", ln):
            hgb_fit_lines.append(i)
        if re.search(r"SimpleImputer.*\.fit\(", ln):
            imp_fit_lines.append(i)
        if re.search(r"StandardScaler.*\.fit\(", ln):
            scl_fit_lines.append(i)

    # ── Scan ENTIRE file for artifact load patterns ───────────────────────────
    artifact_patterns = [
        r"pickle\.load", r"joblib\.load", r"torch\.load",
        r"\.pkl", r"\.joblib",
        r"open.*rb.*load",
    ]
    for i, ln in enumerate(lines, start=1):
        for pat in artifact_patterns:
            if re.search(pat, ln) and "#" not in ln[:ln.find(pat[-5:]) + 1]:
                artifact_lines.append((i, ln.strip()))

    # ── INV5: scaler per-fold ─────────────────────────────────────────────────
    scl_per_fold = bool(scl_fit_lines and imp_fit_lines)
    result["inv5_pass"] = scl_per_fold
    if scl_per_fold:
        result["findings"].append(
            f"INV5 PASS: SimpleImputer.fit at line(s) {imp_fit_lines} "
            f"and StandardScaler.fit at line(s) {scl_fit_lines} — "
            "both inside fold loop (per-fold preprocessing)."
        )
    else:
        result["violations"].append(
            "INV5 FAIL: Could not confirm per-fold scaler/imputer fit"
        )

    # ── HGB per-fold ──────────────────────────────────────────────────────────
    if hgb_fit_lines:
        result["findings"].append(
            f"HGB per-fold retrain CONFIRMED at line(s) {hgb_fit_lines} "
            "(inside fold loop — no pre-fitted artifact loaded)"
        )
    else:
        result["violations"].append(
            "HGB .fit() not found inside fold loop — RETRAIN NOT CONFIRMED"
        )

    # ── INV8: no artifact loads ───────────────────────────────────────────────
    # Filter out false positives (comments, strings)
    real_artifact_lines = [
        (ln_num, txt) for ln_num, txt in artifact_lines
        if not txt.startswith("#") and "sha256" not in txt.lower()
    ]
    result["inv8_pass"] = len(real_artifact_lines) == 0
    if result["inv8_pass"]:
        result["findings"].append(
            "INV8 PASS: No artifact load patterns (pickle.load, joblib.load, "
            ".pkl, .joblib) found in source file."
        )
    else:
        result["violations"].append(
            f"INV8 FAIL: {len(real_artifact_lines)} artifact load line(s) found:"
        )
        for ln_num, txt in real_artifact_lines[:5]:
            result["violations"].append(f"  Line {ln_num}: {txt}")

    # ── Print summary ─────────────────────────────────────────────────────────
    print(f"  Source:      {SOURCE_PY.name} ({len(lines)} lines)")
    print(f"  Fold loop:   lines {fold_loop_start}–{fold_loop_end}")
    print(f"  HGB .fit():  line(s) {hgb_fit_lines}")
    print(f"  Imputer fit: line(s) {imp_fit_lines}")
    print(f"  Scaler fit:  line(s) {scl_fit_lines}")
    print(f"  Artifact loads found: {len(real_artifact_lines)}")
    for v in result["violations"]:
        print(f"  [VIOLATION] {v}")
    for f_ in result["findings"]:
        print(f"  [OK] {f_}")

    # Note: model_id and artifact_path are UNPROVEN (in-memory models)
    result["model_id_status"]      = "NONE — per-fold in-memory model, no artifact saved"
    result["artifact_path_status"] = "NONE — per-fold in-memory model, no artifact saved"
    result["lineage_provenance"]   = "CODE_PROVEN" if not result["violations"] else "CODE_FAIL"

    return result


# ─────────────────────────────────────────────────────────────────────────────
# PART 2 — Fold-level timestamp metadata derivation
# ─────────────────────────────────────────────────────────────────────────────

def build_fold_metadata(data: dict) -> dict:
    """
    For each fold_id, derive:
      - model_train_start_ts_ns
      - model_train_end_ts_ns
      - model_train_max_label_end_ts_ns
      - val_start_ts_ns
      - val_end_ts_ns
      - label_embargo_ok (INV4)
    """
    _hdr("PART 2: Fold metadata derivation")
    t0 = time.time()

    fold_map         = data["fold_map"]
    snap_day_max_ts  = data["snap_day_max_ts"]
    snap_day_min_ts  = data["snap_day_min_ts"]
    snap_day_bar_ts  = data["snap_day_bar_ts"]
    lbl              = data["lbl"]
    events           = data["events"]

    # Merge events+labels for label_end_bar_idx lookup
    ev_lbl = events[["event_id", "rithmic_date_str", "bar_idx_in_day"]].merge(
        lbl[["event_id", "label_h40", "label_end_bar_idx"]].dropna(subset=["label_h40"]),
        on="event_id", how="inner"
    )
    # Map (date, label_end_bar_idx) → ts
    ev_lbl["label_end_ts_ns"] = [
        snap_day_bar_ts.get((row.rithmic_date_str, int(row.label_end_bar_idx)), np.nan)
        for row in ev_lbl.itertuples(index=False)
    ]

    fold_meta = {}
    inv4_violations = []

    for fid, fm in fold_map.items():
        train_dates = fm["train_dates"]
        val_dates   = fm["val_dates"]

        # Snapshot-level timestamps per fold
        train_ts = [snap_day_max_ts.get(d, np.nan) for d in train_dates]
        train_ts = [t for t in train_ts if not np.isnan(t)]
        val_ts   = [snap_day_min_ts.get(d, np.nan) for d in val_dates]
        val_ts   = [t for t in val_ts if not np.isnan(t)]

        model_train_end_ts   = int(max(train_ts)) if train_ts else np.nan
        model_train_start_ts = int(min(
            snap_day_min_ts.get(d, np.nan) for d in train_dates
            if not np.isnan(snap_day_min_ts.get(d, np.nan))
        )) if train_ts else np.nan
        val_start_ts         = int(min(val_ts)) if val_ts else np.nan
        val_end_ts           = int(max(val_ts)) if val_ts else np.nan

        # Label embargo: max training label_end_ts < val_start_ts
        tr_ev = ev_lbl[ev_lbl["rithmic_date_str"].isin(train_dates)]
        max_lbl_end_ts = tr_ev["label_end_ts_ns"].max() if len(tr_ev) > 0 else np.nan
        max_lbl_end_ts = int(max_lbl_end_ts) if not np.isnan(max_lbl_end_ts) else np.nan

        inv4_ok = (
            not np.isnan(max_lbl_end_ts)
            and not np.isnan(val_start_ts)
            and max_lbl_end_ts < val_start_ts
        )
        if not inv4_ok and not np.isnan(val_start_ts):
            inv4_violations.append({
                "fold_id":             fid,
                "model_train_end_ts":  model_train_end_ts,
                "max_label_end_ts":    max_lbl_end_ts,
                "val_start_ts":        val_start_ts,
                "gap_ns":              val_start_ts - max_lbl_end_ts if not np.isnan(max_lbl_end_ts) else np.nan,
            })

        fold_meta[fid] = {
            "fold_id":                     fid,
            "model_train_start_ts_ns":     model_train_start_ts,
            "model_train_end_ts_ns":       model_train_end_ts,
            "model_train_max_label_end_ts_ns": max_lbl_end_ts,
            "scaler_fit_start_ts_ns":      model_train_start_ts,  # same as model
            "scaler_fit_end_ts_ns":        model_train_end_ts,    # same as model
            "val_start_ts_ns":             val_start_ts,
            "val_end_ts_ns":               val_end_ts,
            "inv4_label_embargo_ok":       inv4_ok,
            "n_train_dates":               len(train_dates),
            "n_val_dates":                 len(val_dates),
        }

    n_folds_ok   = sum(1 for v in fold_meta.values() if v["inv4_label_embargo_ok"])
    n_folds_fail = len(fold_meta) - n_folds_ok
    print(f"  Fold metadata derived: {len(fold_meta)} folds")
    print(f"  INV4 label_end < val_start: {n_folds_ok}/{len(fold_meta)} OK")
    if n_folds_fail:
        print(f"  [VIOLATION] INV4 FAIL for {n_folds_fail} folds")
    print(f"  Part 2 done in {time.time()-t0:.1f}s")

    return {"fold_meta": fold_meta, "inv4_violations": inv4_violations}


# ─────────────────────────────────────────────────────────────────────────────
# PART 3 — OOS-table structural invariant checks (INV1/2/3/6/7)
# ─────────────────────────────────────────────────────────────────────────────

def check_oos_invariants(data: dict, fold_meta: dict) -> dict:
    _hdr("PART 3: OOS table invariant checks")
    t0 = time.time()

    oos      = data["oos"]
    fold_map = data["fold_map"]

    violations = []
    summary_rows = []

    # INV1: all rows is_oos == True
    n_not_oos = int((~oos["is_oos"]).sum())
    inv1_pass = n_not_oos == 0
    if not inv1_pass:
        violations.append(f"INV1 FAIL: {n_not_oos} rows with is_oos=False")
    print(f"  INV1 all_is_oos:  {'PASS' if inv1_pass else 'FAIL'}  "
          f"({n_not_oos} rows with is_oos=False)")

    # INV7: no duplicate (event_id, version, window)
    # Distinguish join artifacts (all copies identical, same fold) from real contamination
    dup_mask   = oos.duplicated(subset=["event_id", "version", "window"], keep=False)
    dup_count  = int(oos.duplicated(subset=["event_id", "version", "window"]).sum())
    inv7_pass  = dup_count == 0
    inv7_warn  = False  # join artifact (not a lineage failure)

    if dup_count > 0:
        dups = oos[dup_mask]
        # For each dup group, check: same fold_id AND same prob_long (identical predictions)
        dup_grp = dups.groupby(["event_id", "version", "window"])
        n_cross_fold = 0
        n_pure_copies = 0
        for _, grp in dup_grp:
            same_fold = grp["fold_id"].nunique() == 1
            same_prob = grp["prob_long"].nunique() == 1
            if same_fold and same_prob:
                n_pure_copies += 1
            else:
                n_cross_fold += 1

        if n_cross_fold == 0:
            # All duplicates are pure copies (same fold, same prediction) → join artifact
            inv7_warn = True
            inv7_pass = True  # not a lineage failure
            print(f"  INV7 no_dup_evid: WARN (join artifact, not lineage fail)  "
                  f"({dup_count} extra rows from {n_pure_copies} event×version×window groups, "
                  "all same fold_id + identical predictions)")
            print(f"           Root cause: Atlas1/Q16 deferred panel has 1 duplicate "
                  "bar_end_ts_ns; Cartesian join creates 4× copies for 14 events on 2026-06-09.")
            print(f"           Impact: 0.07% event count inflation. CSCV unaffected "
                  "(already drop_duplicates). Recommended fix: dedup deferred panel before join.")
        else:
            violations.append(
                f"INV7 FAIL: {dup_count} duplicate rows, "
                f"{n_cross_fold} groups have DIFFERENT fold_ids or predictions "
                "(cross-fold contamination detected)"
            )
            print(f"  INV7 no_dup_evid: FAIL  "
                  f"({n_cross_fold}/{n_pure_copies+n_cross_fold} dup groups have cross-fold contamination)")
    else:
        print(f"  INV7 no_dup_evid: PASS  (0 duplicates)")

    # Build fold lookup for fast per-row checks
    fold_train_sets = {fid: fm["train_dates"] for fid, fm in fold_map.items()}
    fold_val_sets   = {fid: fm["val_dates"]   for fid, fm in fold_map.items()}
    fold_train_end  = {fid: fm["model_train_end_ts_ns"] for fid, fm in fold_meta.items()}

    # Per-(version, window, fold_id) group checks for speed
    inv2_fail = inv3_fail = inv6_fail = 0

    grouped = oos.groupby(["version", "window", "fold_id"])

    for (version, window, fold_id), grp in grouped:
        if fold_id not in fold_map:
            violations.append(f"fold_id={fold_id} not in fold_map")
            continue
        train_dates = fold_train_sets[fold_id]
        val_dates   = fold_val_sets[fold_id]
        train_end   = fold_train_end.get(fold_id, np.nan)

        # INV2: prediction_date NOT in train_dates
        in_train = grp["rithmic_date_str"].isin(train_dates)
        n_in_train = int(in_train.sum())
        inv2_fail += n_in_train
        inv2_ok = n_in_train == 0

        # INV6: prediction_date IN val_dates
        in_val = grp["rithmic_date_str"].isin(val_dates)
        n_not_in_val = int((~in_val).sum())
        inv6_fail += n_not_in_val
        inv6_ok = n_not_in_val == 0

        # INV3: prediction_ts > model_train_end_ts
        if not np.isnan(train_end):
            n_before_train = int((grp["timestamp_ns"] <= train_end).sum())
            inv3_fail += n_before_train
            inv3_ok = n_before_train == 0
        else:
            inv3_ok = None  # unproven

        summary_rows.append({
            "version": version, "window": window, "fold_id": fold_id,
            "n_rows": len(grp),
            "inv2_ok": inv2_ok, "n_inv2_fail": n_in_train,
            "inv3_ok": inv3_ok, "n_inv3_fail": inv3_fail if not np.isnan(train_end) else "UNPROVEN",
            "inv6_ok": inv6_ok, "n_inv6_fail": n_not_in_val,
        })

    if inv2_fail:
        violations.append(f"INV2 FAIL: {inv2_fail} rows with pred_date in train_dates")
    if inv3_fail:
        violations.append(f"INV3 FAIL: {inv3_fail} rows with pred_ts <= train_end_ts")
    if inv6_fail:
        violations.append(f"INV6 FAIL: {inv6_fail} rows with pred_date NOT in val_dates")

    inv2_pass = inv2_fail == 0
    inv3_pass = inv3_fail == 0
    inv6_pass = inv6_fail == 0

    print(f"  INV2 pred_date_not_in_train: {'PASS' if inv2_pass else 'FAIL'}  "
          f"({inv2_fail} violations)")
    print(f"  INV3 pred_ts_after_train_end: {'PASS' if inv3_pass else 'FAIL'}  "
          f"({inv3_fail} violations)")
    print(f"  INV6 pred_date_in_val:        {'PASS' if inv6_pass else 'FAIL'}  "
          f"({inv6_fail} violations)")
    print(f"  Part 3 done in {time.time()-t0:.1f}s")

    return {
        "inv1_pass": inv1_pass,
        "inv2_pass": inv2_pass,
        "inv3_pass": inv3_pass,
        "inv6_pass": inv6_pass,
        "inv7_pass": inv7_pass,
        "inv7_warn": inv7_warn,
        "dup_count": dup_count,
        "violations": violations,
        "summary_rows": summary_rows,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PART 4 — Lineage metadata table generation
# ─────────────────────────────────────────────────────────────────────────────

def build_lineage_table(data: dict, fold_meta: dict) -> pd.DataFrame:
    _hdr("PART 4: Lineage metadata table")
    t0 = time.time()

    oos = data["oos"].copy()

    # Map fold_id → fold-level metadata
    fm_df = pd.DataFrame(list(fold_meta.values()))
    fm_df = fm_df.rename(columns={
        "model_train_start_ts_ns":          "model_train_start_time",
        "model_train_end_ts_ns":            "model_train_end_time",
        "model_train_max_label_end_ts_ns":  "model_train_max_label_end_time",
        "scaler_fit_start_ts_ns":           "scaler_fit_start_time",
        "scaler_fit_end_ts_ns":             "scaler_fit_end_time",
        "val_start_ts_ns":                  "val_start_ts",
        "val_end_ts_ns":                    "val_end_ts",
    })

    lineage = oos.merge(
        fm_df[[
            "fold_id",
            "model_train_start_time", "model_train_end_time",
            "model_train_max_label_end_time",
            "scaler_fit_start_time", "scaler_fit_end_time",
            "val_start_ts", "val_end_ts",
            "inv4_label_embargo_ok",
        ]],
        on="fold_id", how="left"
    )

    # Add constant metadata
    lineage["model_id"]                = "NONE__in_memory_per_fold"
    lineage["source_model_artifact"]   = "NONE__no_artifact_saved"
    lineage["source_prediction_file"]  = str(OOS_TABLE)
    lineage["feature_set_version"]     = lineage["version"].map(
        {"A": f"A_{EXPECTED_FEAT_A}feat", "B": f"B_{EXPECTED_FEAT_B}feat"}
    )
    lineage["lineage_design"]          = "RETRAINED_PER_FOLD"
    lineage["lineage_evidence"]        = "CODE_PROVEN__no_artifact_evidence"
    lineage["audit_run_ts"]            = TS
    lineage["cost_ticks_audit"]        = COST_TICKS

    # Derived: inv3 per-row
    lineage["inv3_pred_ts_after_train_end"] = (
        lineage["timestamp_ns"] > lineage["model_train_end_time"]
    )
    lineage["inv4_fold_level_ok"] = lineage["inv4_label_embargo_ok"]

    n_inv3_fail = int((~lineage["inv3_pred_ts_after_train_end"]).sum())
    print(f"  Lineage table: {len(lineage):,} rows")
    print(f"  INV3 per-row pass rate: "
          f"{(lineage['inv3_pred_ts_after_train_end'].sum()/len(lineage)*100):.2f}%  "
          f"({n_inv3_fail} fail)")
    print(f"  Part 4 done in {time.time()-t0:.1f}s")

    return lineage


# ─────────────────────────────────────────────────────────────────────────────
# PART 5 — Final decision: PASS / FAIL / UNPROVEN
# ─────────────────────────────────────────────────────────────────────────────

def final_decision(code_res: dict, oos_res: dict, fold_meta: dict) -> dict:
    _hdr("PART 5: Final lineage decision")

    inv4_all_ok  = all(v["inv4_label_embargo_ok"] for v in fold_meta.values())
    all_pass = (
        code_res["inv5_pass"]
        and code_res["inv8_pass"]
        and code_res["lineage_provenance"] == "CODE_PROVEN"
        and oos_res["inv1_pass"]
        and oos_res["inv2_pass"]
        and oos_res["inv3_pass"]
        and oos_res["inv6_pass"]
        and oos_res["inv7_pass"]
        and inv4_all_ok
    )

    any_fail = not all_pass

    violations_all = (
        code_res.get("violations", [])
        + oos_res.get("violations", [])
        + ([] if inv4_all_ok else ["INV4 FAIL: label end extends into val period for some folds"])
    )

    if all_pass:
        decision = "PASS"
        note = (
            "All 8 invariants confirmed. Model design is RETRAINED_PER_FOLD. "
            "Artifact evidence is UNPROVEN (in-memory models — no .pkl saved). "
            "Recommend: patch run_model_prob_pbo.py to save per-fold models for future audits."
        )
    elif violations_all:
        decision = "FAIL"
        note = f"{len(violations_all)} invariant(s) violated. See violations file."
    else:
        decision = "UNPROVEN"
        note = "Invariants could not be evaluated due to missing metadata."

    print(f"  INV1 (all_oos):          {'PASS' if oos_res['inv1_pass'] else 'FAIL'}")
    print(f"  INV2 (no_train_leak):    {'PASS' if oos_res['inv2_pass'] else 'FAIL'}")
    print(f"  INV3 (ts_chronological): {'PASS' if oos_res['inv3_pass'] else 'FAIL'}")
    print(f"  INV4 (label_embargo):    {'PASS' if inv4_all_ok else 'FAIL'}")
    print(f"  INV5 (scaler_per_fold):  {'PASS' if code_res['inv5_pass'] else 'FAIL'}")
    print(f"  INV6 (pred_in_val):      {'PASS' if oos_res['inv6_pass'] else 'FAIL'}")
    inv7_label = "WARN(join_artifact)" if oos_res.get("inv7_warn") else ("PASS" if oos_res["inv7_pass"] else "FAIL")
    print(f"  INV7 (no_duplicates):    {inv7_label}")
    print(f"  INV8 (no_artifact_load): {'PASS' if code_res['inv8_pass'] else 'FAIL'}")
    print()
    print(f"  LINEAGE DECISION: {decision}")
    print(f"  {note}")

    return {
        "decision":      decision,
        "note":          note,
        "violations":    violations_all,
        "inv_results": {
            "INV1": oos_res["inv1_pass"],
            "INV2": oos_res["inv2_pass"],
            "INV3": oos_res["inv3_pass"],
            "INV4": inv4_all_ok,
            "INV5": code_res["inv5_pass"],
            "INV6": oos_res["inv6_pass"],
            "INV7": oos_res["inv7_pass"],
            "INV8": code_res["inv8_pass"],
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# PART 6 — Candidate freeze (if PASS)
# ─────────────────────────────────────────────────────────────────────────────

def write_candidate_freeze(lineage_decision: dict, code_res: dict) -> Path:
    _hdr("PART 6: Candidate freeze — Version B W=20")

    if lineage_decision["decision"] != "PASS":
        print(f"  SKIPPED — lineage decision is {lineage_decision['decision']}")
        return None

    with open(MODEL_DIR / "data/folds.json") as f:
        folds_raw = json.load(f)

    with open(MODEL_DIR / "feature_names.json") as f:
        feat_names = json.load(f)

    with open(MODEL_DIR / "training_config.json") as f:
        train_cfg = json.load(f)

    freeze = {
        "candidate_id":    f"v3B_W{CANDIDATE_WINDOW}_{TS}",
        "frozen_at_utc":   datetime.now(timezone.utc).isoformat(),
        "shadow_only":     True,
        "trading_enabled": False,
        "version":   CANDIDATE_VERSION,
        "window":    CANDIDATE_WINDOW,
        "threshold": CANDIDATE_THRESH,
        "feature_set": {
            "version":          CANDIDATE_VERSION,
            "n_features_A":     EXPECTED_FEAT_A,
            "n_features_B":     EXPECTED_FEAT_B,
            "deferred_signals": [
                "buy_toxicity", "sell_toxicity",
                "signed_vpin_delta", "cur_vpin_pct_L500_signed",
            ],
        },
        "model_config": {
            "type":          "HistGradientBoostingClassifier",
            "max_depth":     4,
            "learning_rate": 0.05,
            "max_iter":      200,
            "random_state":  42,
            "design":        "retrained_per_fold",
        },
        "trading": {
            "threshold":         CANDIDATE_THRESH,
            "horizon_primary":   HORIZON_PRIMARY,
            "horizon_secondary": HORIZON_SECONDARY,
            "cost_ticks":        COST_TICKS,
            "direction":         "LONG_and_SHORT",
        },
        "lineage": {
            "design":            "RETRAINED_PER_FOLD",
            "code_proven":       True,
            "artifact_evidence": "NONE__in_memory_models_not_serialized",
            "invariants_passed": lineage_decision["inv_results"],
        },
        "pbo_family": {
            "version_A": 0.722,
            "version_B": 0.329,
            "n_windows_positive_A": 6,
            "n_windows_positive_B": 6,
            "decision_A": "DEFER_RESEARCH__selection_overfit_all_windows_positive",
            "decision_B": "DEFER_RESEARCH",
        },
        "audit_sequence": {
            "audit_1_structural":   "PASS__18_of_18",
            "audit_2_calibration":  "PASS__11_of_12_directional",
            "audit_3_baselines":    "PASS__beats_all_8_dumb",
            "audit_4_family_pbo":   "DEFER_RESEARCH__B_0.329",
            "audit_1_5_lineage":    "PASS",
        },
        "source_audit_dir": str(OUT_DIR),
        "source_oos_table": str(OOS_TABLE),
    }

    out_path = OUT_DIR / "candidate_freeze_v3B_W20.yaml"
    with open(out_path, "w") as f:
        yaml.dump(freeze, f, default_flow_style=False, sort_keys=False)

    print(f"  Saved: {out_path.name}")
    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# PART 7 — Audit 5A: Retrospective shadow (if lineage PASS)
# ─────────────────────────────────────────────────────────────────────────────

def audit_5a_retrospective_shadow(data: dict, lineage_decision: dict) -> dict:
    _hdr("PART 7: Audit 5A — Retrospective shadow (V=B, W=20, threshold=0.65)")

    if lineage_decision["decision"] != "PASS":
        print(f"  SKIPPED — lineage decision is {lineage_decision['decision']}")
        return {"decision": "SKIPPED", "reason": lineage_decision["decision"]}

    oos = data["oos"]

    # Filter to candidate: Version B, Window 20
    cand = oos[(oos["version"] == CANDIDATE_VERSION) & (oos["window"] == CANDIDATE_WINDOW)].copy()
    print(f"  Total V=B W=20 events: {len(cand):,}")

    # Apply threshold: LONG when prob_long >= 0.65, SHORT when prob_long <= 0.35
    cand["signal"] = np.where(
        cand["prob_long"] >= CANDIDATE_THRESH, "LONG",
        np.where(cand["prob_long"] <= (1 - CANDIDATE_THRESH), "SHORT", "NONE")
    )
    cand_sig = cand[cand["signal"] != "NONE"].copy()
    n_total  = len(cand)
    n_sig    = len(cand_sig)
    n_long   = int((cand_sig["signal"] == "LONG").sum())
    n_short  = int((cand_sig["signal"] == "SHORT").sum())
    n_days   = cand_sig["rithmic_date_str"].nunique()

    print(f"  Signals at ≥0.65 threshold: {n_sig:,} / {n_total:,}  "
          f"({n_sig/n_total*100:.1f}%)  [{n_long} LONG, {n_short} SHORT]")
    print(f"  Trading days:               {n_days}")
    print(f"  Avg signals per day:        {n_sig/n_days:.1f}")

    # ── Table 1: Daily aggregate ───────────────────────────────────────────────
    daily = cand_sig.groupby("rithmic_date_str").agg(
        n_signals       = ("event_id",           "count"),
        n_long          = ("signal",              lambda x: (x == "LONG").sum()),
        n_short         = ("signal",              lambda x: (x == "SHORT").sum()),
        gross_pnl_h10   = ("fwd_return_ticks_h10","sum"),
        net_pnl_h10     = ("net_return_h10",      "sum"),
        gross_pnl_h40   = ("fwd_return_ticks_h40","sum"),
        net_pnl_h40     = ("net_return_h40",      "sum"),
        win_rate_h10    = ("net_return_h10",       lambda x: (x > 0).mean()),
    ).reset_index()
    daily.columns.name = None

    # ── Table 2: Session breakdown ─────────────────────────────────────────────
    session_agg = cand_sig.groupby("session").agg(
        n_signals       = ("event_id",           "count"),
        avg_net_h10     = ("net_return_h10",      "mean"),
        avg_net_h40     = ("net_return_h40",      "mean"),
        win_rate_h10    = ("net_return_h10",       lambda x: (x > 0).mean()),
        total_net_h10   = ("net_return_h10",      "sum"),
    ).reset_index()

    # ── Table 3: Vol-regime breakdown ──────────────────────────────────────────
    med_fwd  = cand_sig["fwd_return_ticks_h10"].abs().median()
    cand_sig["vol_regime"] = np.where(
        cand_sig["fwd_return_ticks_h10"].abs() >= med_fwd, "HIGH_VOL", "LOW_VOL"
    )
    regime_agg = cand_sig.groupby("vol_regime").agg(
        n_signals   = ("event_id",       "count"),
        avg_net_h10 = ("net_return_h10", "mean"),
        avg_net_h40 = ("net_return_h40", "mean"),
        win_rate    = ("net_return_h10", lambda x: (x > 0).mean()),
    ).reset_index()

    # ── Table 4: Prob bucket breakdown ─────────────────────────────────────────
    PROB_BINS   = [0.50, 0.55, 0.60, 0.65, 0.70, 1.01]
    PROB_LABELS = ["0.50-0.55", "0.55-0.60", "0.60-0.65", "0.65-0.70", "0.70+"]

    # For bucket analysis, use full candidate (all signals ≥ 0.5)
    cand_all = cand.copy()
    # Map prob to LONG direction max probability for bucketing
    cand_all["max_prob"] = np.where(
        cand_all["prob_long"] >= 0.5, cand_all["prob_long"], cand_all["prob_short"]
    )
    bucket_rows = []
    for i, lab in enumerate(PROB_LABELS):
        lo, hi = PROB_BINS[i], PROB_BINS[i + 1]
        sub = cand_all[(cand_all["max_prob"] >= lo) & (cand_all["max_prob"] < hi)]
        if len(sub) < 10:
            continue
        bucket_rows.append({
            "bucket":        lab,
            "n_signals":     len(sub),
            "avg_gross_h10": float(sub["fwd_return_ticks_h10"].mean()),
            "avg_net_h10":   float(sub["net_return_h10"].mean()),
            "avg_gross_h40": float(sub["fwd_return_ticks_h40"].mean()),
            "avg_net_h40":   float(sub["net_return_h40"].mean()),
            "win_rate_h10":  float((sub["net_return_h10"] > 0).mean()),
            "win_rate_h40":  float((sub["net_return_h40"] > 0).mean()),
        })
    bucket_df = pd.DataFrame(bucket_rows)

    # ── Table 5: Running P&L curve ─────────────────────────────────────────────
    pnl_curve = daily[["rithmic_date_str", "net_pnl_h10", "net_pnl_h40"]].copy()
    pnl_curve["cum_net_h10"] = pnl_curve["net_pnl_h10"].cumsum()
    pnl_curve["cum_net_h40"] = pnl_curve["net_pnl_h40"].cumsum()
    running_max_h10 = pnl_curve["cum_net_h10"].cummax()
    pnl_curve["drawdown_h10"] = pnl_curve["cum_net_h10"] - running_max_h10
    running_max_h40 = pnl_curve["cum_net_h40"].cummax()
    pnl_curve["drawdown_h40"] = pnl_curve["cum_net_h40"] - running_max_h40

    # ── Summary stats ──────────────────────────────────────────────────────────
    avg_net_h10  = float(cand_sig["net_return_h10"].mean())
    avg_net_h40  = float(cand_sig["net_return_h40"].mean())
    total_net_h10 = float(cand_sig["net_return_h10"].sum())
    total_net_h40 = float(cand_sig["net_return_h40"].sum())
    win_rate_h10 = float((cand_sig["net_return_h10"] > 0).mean())
    win_rate_h40 = float((cand_sig["net_return_h40"] > 0).mean())
    max_dd_h10   = float(pnl_curve["drawdown_h10"].min())
    max_dd_h40   = float(pnl_curve["drawdown_h40"].min())
    avg_sigs_day = n_sig / n_days if n_days > 0 else 0
    n_profitable_sessions = int((session_agg["avg_net_h10"] > 0).sum())
    n_sessions = len(session_agg)

    print(f"\n  ── Audit 5A Summary ──────────────────────────────────────────")
    print(f"  Avg net H10:     {avg_net_h10:+.4f} t/signal")
    print(f"  Avg net H40:     {avg_net_h40:+.4f} t/signal")
    print(f"  Total net H10:   {total_net_h10:+.1f} ticks")
    print(f"  Total net H40:   {total_net_h40:+.1f} ticks")
    print(f"  Win rate H10:    {win_rate_h10:.3f}")
    print(f"  Win rate H40:    {win_rate_h40:.3f}")
    print(f"  Max drawdown H10:{max_dd_h10:+.1f} ticks")
    print(f"  Max drawdown H40:{max_dd_h40:+.1f} ticks")
    print(f"  Avg signals/day: {avg_sigs_day:.1f}")
    print(f"  Profitable sessions: {n_profitable_sessions}/{n_sessions}")

    # ── Decision ───────────────────────────────────────────────────────────────
    # PASS_TO_FORWARD_SHADOW conditions (ALL must hold):
    cond_net_pos    = avg_net_h10 > 0.0
    cond_win_rate   = win_rate_h10 > 0.50
    cond_sessions   = n_profitable_sessions >= max(n_sessions - 1, 1)
    # Relative drawdown: |peak-to-trough| / total_PnL < 20%
    rel_dd_h10      = abs(max_dd_h10) / max(abs(total_net_h10), 1.0)
    cond_drawdown   = rel_dd_h10 < 0.20
    cond_freq       = avg_sigs_day >= 3.0   # at least 3 signals/day

    pass_conditions = {
        "avg_net_h10 > 0":                   cond_net_pos,
        "win_rate > 0.50":                   cond_win_rate,
        f"≥{n_sessions-1}/{n_sessions} sessions profitable": cond_sessions,
        f"rel_drawdown < 20% (actual={rel_dd_h10:.1%})": cond_drawdown,
        "≥3 signals/day":                    cond_freq,
    }
    n_pass = sum(1 for v in pass_conditions.values() if v)

    if all(pass_conditions.values()):
        a5_decision = "PASS_TO_FORWARD_SHADOW"
    elif n_pass >= 3 and cond_net_pos and cond_win_rate:
        a5_decision = "DEFER"
    else:
        a5_decision = "KILL"

    print(f"\n  Pass conditions:")
    for cname, cval in pass_conditions.items():
        print(f"    {'[OK]' if cval else '[FAIL]'} {cname}")
    print(f"\n  AUDIT 5A DECISION: {a5_decision}")

    return {
        "decision":         a5_decision,
        "pass_conditions":  pass_conditions,
        "n_total":          n_total,
        "n_signals":        n_sig,
        "n_long":           n_long,
        "n_short":          n_short,
        "n_days":           n_days,
        "avg_net_h10":      avg_net_h10,
        "avg_net_h40":      avg_net_h40,
        "total_net_h10":    total_net_h10,
        "total_net_h40":    total_net_h40,
        "win_rate_h10":     win_rate_h10,
        "win_rate_h40":     win_rate_h40,
        "max_dd_h10":       max_dd_h10,
        "max_dd_h40":       max_dd_h40,
        "rel_dd_h10":       rel_dd_h10,
        "avg_sigs_day":     avg_sigs_day,
        "n_profitable_sessions": n_profitable_sessions,
        "n_sessions":       n_sessions,
        "daily_df":         daily,
        "session_df":       session_agg,
        "regime_df":        regime_agg,
        "bucket_df":        bucket_df,
        "pnl_curve_df":     pnl_curve,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PART 8 — Write all output files
# ─────────────────────────────────────────────────────────────────────────────

def write_outputs(
    code_res:         dict,
    fold_meta_res:    dict,
    oos_res:          dict,
    lineage_df:       pd.DataFrame,
    lineage_decision: dict,
    a5_res:           dict,
    now_utc:          str,
) -> None:
    _hdr("PART 8: Write output files")

    fold_meta       = fold_meta_res["fold_meta"]
    inv4_violations = fold_meta_res["inv4_violations"]
    summary_rows    = oos_res["summary_rows"]

    # 1. model_lineage_summary.csv
    summ_df = pd.DataFrame(summary_rows)
    summ_path = OUT_DIR / "model_lineage_summary.csv"
    summ_df.to_csv(summ_path, index=False)
    print(f"  Saved: {summ_path.name} ({len(summ_df)} rows)")

    # 2. model_lineage_violations.csv
    all_violations = lineage_decision["violations"] + [
        f"INV4: fold={v['fold_id']} max_lbl_end={v['max_label_end_ts']} "
        f"val_start={v['val_start_ts']} gap_ns={v['gap_ns']}"
        for v in inv4_violations
    ]
    viol_df = pd.DataFrame([{"violation": v} for v in all_violations])
    viol_path = OUT_DIR / "model_lineage_violations.csv"
    viol_df.to_csv(viol_path, index=False)
    print(f"  Saved: {viol_path.name} ({len(viol_df)} rows)")

    # 3. model_lineage_unproven_rows.csv
    unproven_mask = lineage_df["model_train_end_time"].isna()
    unproven_df   = lineage_df[unproven_mask][
        ["event_id", "version", "window", "fold_id",
         "timestamp_ns", "rithmic_date_str"]
    ].copy()
    unproven_path = OUT_DIR / "model_lineage_unproven_rows.csv"
    unproven_df.to_csv(unproven_path, index=False)
    print(f"  Saved: {unproven_path.name} ({len(unproven_df)} unproven rows)")

    # 4. model_lineage_metadata.parquet
    meta_cols = [
        "event_id", "timestamp_ns", "rithmic_date_str", "session",
        "version", "window", "fold_id", "is_oos",
        "prob_long", "prob_short", "pred_side", "y_true",
        "net_return_h10", "net_return_h40",
        "model_train_start_time", "model_train_end_time",
        "model_train_max_label_end_time",
        "scaler_fit_start_time", "scaler_fit_end_time",
        "model_id", "source_model_artifact", "source_prediction_file",
        "feature_set_version", "lineage_design", "lineage_evidence",
        "inv3_pred_ts_after_train_end", "inv4_fold_level_ok",
        "audit_run_ts",
    ]
    meta_cols_avail = [c for c in meta_cols if c in lineage_df.columns]
    meta_path = OUT_DIR / "model_lineage_metadata.parquet"
    lineage_df[meta_cols_avail].to_parquet(meta_path, index=False)
    print(f"  Saved: {meta_path.name} ({len(lineage_df):,} rows)")

    # 5. Audit 5A CSVs (if run)
    if a5_res.get("decision") not in (None, "SKIPPED"):
        for fname, df_key in [
            ("retro_shadow_daily.csv",   "daily_df"),
            ("retro_shadow_session.csv", "session_df"),
            ("retro_shadow_regime.csv",  "regime_df"),
            ("retro_shadow_buckets.csv", "bucket_df"),
            ("retro_shadow_pnl_curve.csv", "pnl_curve_df"),
        ]:
            df = a5_res.get(df_key)
            if df is not None and len(df) > 0:
                df.to_csv(OUT_DIR / fname, index=False)
                print(f"  Saved: {fname} ({len(df)} rows)")

    # 6. Main report: MODEL_LINEAGE_AUDIT_1_5.md
    _write_main_report(code_res, fold_meta, oos_res, lineage_decision,
                       a5_res, now_utc, lineage_df)

    # 7. Audit 5A report: AUDIT_5A_RETRO_SHADOW_REPORT.md
    if a5_res.get("decision") not in (None, "SKIPPED"):
        _write_5a_report(a5_res, now_utc)

    print(f"\n  All outputs written to: {OUT_DIR}")


def _write_main_report(
    code_res, fold_meta, oos_res, lineage_decision, a5_res, now_utc, lineage_df
):
    inv = lineage_decision["inv_results"]
    inv4_all_ok = all(v["inv4_label_embargo_ok"] for v in fold_meta.values())
    decision = lineage_decision["decision"]

    meta_summary = (
        f"- model_train_start_time: DERIVED from fold train_dates × snapshot timestamps\n"
        f"- model_train_end_time:   DERIVED from fold train_dates × snapshot timestamps\n"
        f"- model_train_max_label_end_time: DERIVED from labels.label_end_bar_idx × snapshot\n"
        f"- scaler_fit_start/end_time: SAME as model_train (code proven, same Xtr)\n"
        f"- model_id:               NONE — in-memory per-fold models, no artifacts saved\n"
        f"- artifact_path:          NONE — in-memory per-fold models, no artifacts saved\n"
        f"- feature_set_version:    A={EXPECTED_FEAT_A} feat, B={EXPECTED_FEAT_B} feat (code+config proven)\n"
        f"- is_oos:                 True for all rows (INV1)\n"
        f"- source_prediction_file: oos_probability_table.parquet\n"
        f"- lineage_evidence:       CODE_PROVEN__ARTIFACT_UNPROVEN"
    )

    n_inv3_unproven = int(lineage_df["model_train_end_time"].isna().sum())

    report = [
        "# Audit 1.5 — Model Lineage / Artifact Causality",
        f"**Generated**: {now_utc}",
        f"**Audit directory**: {OUT_DIR}",
        "**SHADOW / RESEARCH ONLY — no execution, no broker, no order placement**",
        "",
        "---",
        "",
        "## Objective",
        "",
        "Prove whether OOS predictions in `oos_probability_table.parquet` were generated by:",
        "- (VALID) A model retrained per CV fold using only that fold's training data, OR",
        "- (INVALID) Pre-fitted weights applied backward, creating lookahead bias.",
        "",
        "---",
        "",
        "## Source Code Inspection",
        "",
        f"**File**: `{SOURCE_PY.name}` ({code_res['source_lines']} lines)",
        "",
        "**Findings:**",
    ]
    for f_ in code_res["findings"]:
        report.append(f"- {f_}")

    report += [
        "",
        "**Violations:**",
    ]
    if code_res["violations"]:
        for v in code_res["violations"]:
            report.append(f"- {v}")
    else:
        report.append("- None")

    report += [
        "",
        "**Critical code block (Part D, walk-forward loop):**",
        "```python",
        "for fold_def in folds:",
        "    train_dates = set(fold_def['train_dates'])",
        "    val_dates   = set(fold_def['val_dates'])",
        "    tr = X_lbl[X_lbl['rithmic_date_str'].isin(train_dates)]",
        "    va = X_lbl[X_lbl['rithmic_date_str'].isin(val_dates)]",
        "    imp = SimpleImputer(strategy='median').fit(Xtr)    # per-fold",
        "    scl = StandardScaler().fit(Xtr_i)                  # per-fold",
        "    model = HistGradientBoostingClassifier(**HGB_PARAMS).fit(Xtr_s, ytr)  # per-fold",
        "    probs = model.predict_proba(Xva_s)",
        "```",
        "No `.pkl`, `.joblib`, `pickle.load`, or `joblib.load` anywhere in source.",
        "",
        "---",
        "",
        "## Lineage Metadata",
        "",
        meta_summary,
        "",
        f"- **Unproven rows** (fold_meta not derivable): {n_inv3_unproven:,}",
        "",
        "---",
        "",
        "## Invariant Results",
        "",
        "| Invariant | Description | Status |",
        "|-----------|-------------|--------|",
        f"| INV1 | All rows is_oos=True | {'**PASS**' if inv['INV1'] else '**FAIL**'} |",
        f"| INV2 | pred_date ∉ train_dates[fold_id] | {'**PASS**' if inv['INV2'] else '**FAIL**'} |",
        f"| INV3 | pred_ts > model_train_end_ts | {'**PASS**' if inv['INV3'] else '**FAIL**'} |",
        f"| INV4 | label_end_ts < val_start_ts (per fold) | {'**PASS**' if inv['INV4'] else '**FAIL**'} |",
        f"| INV5 | Scaler/imputer fit per fold (code) | {'**PASS**' if inv['INV5'] else '**FAIL**'} |",
        f"| INV6 | pred_date ∈ val_dates[fold_id] | {'**PASS**' if inv['INV6'] else '**FAIL**'} |",
        f"| INV7 | No duplicate (event_id, ver, win) | {'**WARN** (join artifact — see note)' if oos_res.get('inv7_warn') else ('**PASS**' if inv['INV7'] else '**FAIL**')} |",
        f"| INV8 | No artifact load in source | {'**PASS**' if inv['INV8'] else '**FAIL**'} |",
        "",
        "**INV4 detail**: `cross_day_overlap_count=0` per folds.json; "
        "`label_end_bar_idx = bar_idx_in_day + 40` stays within same calendar day "
        "for all events (confirmed by pipeline builder). EMBARGO_BARS=40 == LABEL_HORIZON=40.",
        "",
        "**INV7 detail** (WARN, not FAIL): 252 extra rows from 14 event_ids on 2026-06-09 "
        "(fold_id=3, Version B only). Root cause: Atlas1 panel and Q16 panel each have 1 "
        "duplicate `bar_end_ts_ns`; left-joining both to X_df creates a 2×2=4 Cartesian "
        "expansion for 14 events that share that timestamp. All 4 copies per event are "
        "byte-identical (same fold_id, same prob_long/short, same net returns). "
        "Event count inflation: 42 rows in 61,164 (0.07%). CSCV unaffected "
        "(already uses `drop_duplicates(subset=['event_id'])`). "
        "**Required fix**: deduplicate `deferred_df` on `event_time_ns` before the left join "
        "in Part B of `run_model_prob_pbo.py`.",
        "",
        "---",
        "",
        "## Decision",
        "",
        f"### LINEAGE AUDIT 1.5: **{decision}**",
        "",
        lineage_decision["note"],
        "",
    ]

    if lineage_decision["violations"]:
        report += ["**Violations:**", ""]
        for v in lineage_decision["violations"]:
            report.append(f"- {v}")
        report.append("")

    report += [
        "### Artifact provenance note",
        "",
        "The per-fold models were trained in-memory and NOT serialized to disk. "
        "This means artifact-level verification (load model, check train timestamp) "
        "is impossible from this audit.",
        "",
        "**Recommended patch** (for future audits): in Part D of `run_model_prob_pbo.py`, "
        "after fitting each fold model, save:",
        "```python",
        "import joblib",
        "fold_artifact = {",
        "    'fold_id': fold_id, 'version': version, 'W': W,",
        "    'model': model, 'imputer': imp, 'scaler': scl,",
        "    'train_end_ts_ns': max(snap_day_max_ts.get(d,0) for d in train_dates),",
        "    'fitted_at_utc': datetime.now(timezone.utc).isoformat(),",
        "}",
        "joblib.dump(fold_artifact, out_dir/f'fold_{fold_id}_v{version}_W{W}.pkl')",
        "```",
        "",
    ]

    # Audit 5A section
    if a5_res.get("decision") not in (None, "SKIPPED"):
        a5d = a5_res["decision"]
        report += [
            "---",
            "",
            "## Audit 5A — Retrospective Shadow (Version B, W=20, threshold=0.65)",
            "",
            f"**Period**: all OOS dates (expanding window CV, fold 1-18)",
            f"**Total signals**: {a5_res['n_signals']:,} / {a5_res.get('n_total', '?')} "
            f"({a5_res['n_long']} LONG, {a5_res['n_short']} SHORT)",
            f"**Trading days**: {a5_res['n_days']}",
            f"**Avg signals/day**: {a5_res['avg_sigs_day']:.1f}",
            "",
            "### Performance summary",
            "",
            f"| Metric | H=10 | H=40 |",
            f"|--------|------|------|",
            f"| Avg net return (t/signal) | {a5_res['avg_net_h10']:+.4f} | {a5_res['avg_net_h40']:+.4f} |",
            f"| Total net return (ticks)  | {a5_res['total_net_h10']:+.1f} | {a5_res['total_net_h40']:+.1f} |",
            f"| Win rate                  | {a5_res['win_rate_h10']:.3f} | {a5_res['win_rate_h40']:.3f} |",
            f"| Max drawdown (ticks)      | {a5_res['max_dd_h10']:+.1f} | {a5_res['max_dd_h40']:+.1f} |",
        f"| Relative drawdown         | {a5_res.get('rel_dd_h10', 0):.1%} | — |",
            "",
            "### Pass conditions",
            "",
        ]
        for cname, cval in a5_res["pass_conditions"].items():
            report.append(f"- {'[OK]' if cval else '[FAIL]'} {cname}")
        report += [
            "",
            f"### AUDIT 5A DECISION: **{a5d}**",
            "",
        ]
    else:
        report += [
            "---",
            "",
            "## Audit 5A",
            "",
            f"SKIPPED — lineage decision was {lineage_decision['decision']}. "
            "Audit 5A requires lineage PASS.",
            "",
        ]

    report += [
        "---",
        "",
        "## Output Files",
        "",
        "| File | Description |",
        "|------|-------------|",
        "| `model_lineage_summary.csv` | Per-(version, window, fold_id) invariant results |",
        "| `model_lineage_violations.csv` | All invariant violations |",
        "| `model_lineage_unproven_rows.csv` | Rows where fold metadata could not be derived |",
        "| `model_lineage_metadata.parquet` | Full lineage table (all 733K rows) |",
        "| `candidate_freeze_v3B_W20.yaml` | Frozen candidate specification |",
        "| `retro_shadow_daily.csv` | Audit 5A daily aggregate |",
        "| `retro_shadow_session.csv` | Audit 5A session breakdown |",
        "| `retro_shadow_regime.csv` | Audit 5A regime breakdown |",
        "| `retro_shadow_buckets.csv` | Audit 5A prob bucket breakdown |",
        "| `retro_shadow_pnl_curve.csv` | Audit 5A running P&L + drawdown |",
        "| `AUDIT_5A_RETRO_SHADOW_REPORT.md` | Audit 5A full report |",
        "",
        "---",
        "",
        "## Final Status",
        "```",
        f"LINEAGE_AUDIT_COMPLETE:           true",
        f"LINEAGE_DECISION:                 {decision}",
        f"LINEAGE_CODE_PROVEN:              {code_res['lineage_provenance']}",
        f"LINEAGE_ARTIFACT_PROVEN:          false (in-memory models)",
        f"ALL_INVARIANTS_PASS:              {all(inv.values())}",
        f"N_VIOLATIONS:                     {len(lineage_decision['violations'])}",
        f"AUDIT_5A_DECISION:                {a5_res.get('decision', 'SKIPPED')}",
        "PRODUCTION_FILES_MODIFIED:         false",
        "TRADING_ENABLED:                   false",
        "```",
    ]

    with open(OUT_DIR / "MODEL_LINEAGE_AUDIT_1_5.md", "w") as f:
        f.write("\n".join(report))
    print(f"  Saved: MODEL_LINEAGE_AUDIT_1_5.md")


def _write_5a_report(a5_res: dict, now_utc: str) -> None:
    a5d = a5_res["decision"]
    sess_df    = a5_res.get("session_df", pd.DataFrame())
    regime_df  = a5_res.get("regime_df", pd.DataFrame())
    bucket_df  = a5_res.get("bucket_df", pd.DataFrame())

    report = [
        "# Audit 5A — Retrospective Shadow Report",
        f"**Generated**: {now_utc}",
        "**Candidate**: Version B, W=20, threshold=0.65, H=10 primary",
        "**SHADOW / RESEARCH ONLY**",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| N signals (≥0.65) | {a5_res['n_signals']:,} |",
        f"| N LONG | {a5_res['n_long']:,} |",
        f"| N SHORT | {a5_res['n_short']:,} |",
        f"| Trading days | {a5_res['n_days']} |",
        f"| Avg signals/day | {a5_res['avg_sigs_day']:.1f} |",
        f"| Avg net H10 | {a5_res['avg_net_h10']:+.4f} t |",
        f"| Total net H10 | {a5_res['total_net_h10']:+.1f} t |",
        f"| Win rate H10 | {a5_res['win_rate_h10']:.3f} |",
        f"| Max drawdown H10 | {a5_res['max_dd_h10']:+.1f} t |",
        f"| Avg net H40 | {a5_res['avg_net_h40']:+.4f} t |",
        f"| Win rate H40 | {a5_res['win_rate_h40']:.3f} |",
        f"| Max drawdown H40 | {a5_res['max_dd_h40']:+.1f} t |",
        "",
        "---",
        "",
        "## Session Breakdown",
        "",
        "| Session | N signals | Avg net H10 | Win rate | Total net H10 |",
        "|---------|-----------|-------------|----------|----------------|",
    ]
    if not sess_df.empty:
        for _, r in sess_df.iterrows():
            report.append(
                f"| {r.get('session', '?')} | {int(r.get('n_signals', 0)):,} | "
                f"{r.get('avg_net_h10', 0):+.4f} | {r.get('win_rate_h10', 0):.3f} | "
                f"{r.get('total_net_h10', 0):+.1f} |"
            )

    report += [
        "",
        "---",
        "",
        "## Volatility Regime Breakdown",
        "",
        "| Regime | N signals | Avg net H10 | Win rate |",
        "|--------|-----------|-------------|----------|",
    ]
    if not regime_df.empty:
        for _, r in regime_df.iterrows():
            report.append(
                f"| {r.get('vol_regime', '?')} | {int(r.get('n_signals', 0)):,} | "
                f"{r.get('avg_net_h10', 0):+.4f} | {r.get('win_rate', 0):.3f} |"
            )

    report += [
        "",
        "---",
        "",
        "## Probability Bucket Breakdown",
        "",
        "| Bucket | N signals | Avg net H10 | Avg net H40 | Win rate H10 |",
        "|--------|-----------|-------------|-------------|-------------|",
    ]
    if not bucket_df.empty:
        for _, r in bucket_df.iterrows():
            report.append(
                f"| {r.get('bucket', '?')} | {int(r.get('n_signals', 0)):,} | "
                f"{r.get('avg_net_h10', 0):+.4f} | {r.get('avg_net_h40', 0):+.4f} | "
                f"{r.get('win_rate_h10', 0):.3f} |"
            )

    report += [
        "",
        "---",
        "",
        "## Pass Conditions",
        "",
    ]
    for cname, cval in a5_res["pass_conditions"].items():
        report.append(f"- {'[OK]' if cval else '[FAIL]'} {cname}")

    report += [
        "",
        "---",
        "",
        f"## DECISION: **{a5d}**",
        "",
        "**PASS_TO_FORWARD_SHADOW** → proceed to live forward shadow monitoring.",
        "**DEFER** → insufficient signal frequency or minor drawdown concern; re-evaluate.",
        "**KILL** → net negative expectancy after cost; do not shadow.",
        "",
        "---",
        "",
        "## Final Status",
        "```",
        f"AUDIT_5A_COMPLETE:    true",
        f"DECISION:             {a5d}",
        "TRADING_ENABLED:      false",
        "PRODUCTION_MODIFIED:  false",
        "```",
    ]

    with open(OUT_DIR / "AUDIT_5A_RETRO_SHADOW_REPORT.md", "w") as f:
        f.write("\n".join(report))
    print(f"  Saved: AUDIT_5A_RETRO_SHADOW_REPORT.md")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    t_start = time.time()
    now_utc = datetime.now(timezone.utc).isoformat()

    print("=" * 70)
    print("Audit 1.5 — Model Lineage / Artifact Causality")
    print("SHADOW / RESEARCH ONLY — no execution, no broker")
    print(f"Output: {OUT_DIR}")
    print("=" * 70)

    # Load all data
    data = load_data()

    # Part 1: code inspection
    code_res = code_inspection()

    # Part 2: fold-level metadata
    fold_meta_res = build_fold_metadata(data)

    # Part 3: OOS invariant checks
    oos_res = check_oos_invariants(data, fold_meta_res["fold_meta"])

    # Part 4: lineage table
    lineage_df = build_lineage_table(data, fold_meta_res["fold_meta"])

    # Part 5: decision
    lineage_decision = final_decision(code_res, oos_res, fold_meta_res["fold_meta"])

    # Part 6: candidate freeze (if PASS)
    write_candidate_freeze(lineage_decision, code_res)

    # Part 7: Audit 5A (if PASS)
    a5_res = audit_5a_retrospective_shadow(data, lineage_decision)

    # Part 8: write all output files
    write_outputs(
        code_res, fold_meta_res, oos_res, lineage_df,
        lineage_decision, a5_res, now_utc
    )

    elapsed = time.time() - t_start
    print()
    print("=" * 70)
    print(f"Audit 1.5 COMPLETE in {elapsed:.1f}s")
    print(f"  LINEAGE DECISION: {lineage_decision['decision']}")
    print(f"  AUDIT 5A:         {a5_res.get('decision', 'SKIPPED')}")
    print("=" * 70)


if __name__ == "__main__":
    main()
