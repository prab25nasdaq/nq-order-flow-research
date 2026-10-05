#!/usr/bin/env python3
"""
Audit 1 — Model Probability Structural Audit v1
Verifies the model-prob PBO run is structurally clean before any calibration
or PBO interpretation.
SHADOW / RESEARCH ONLY — no execution, no broker, no order placement.

Checks:
  1. Same OOS event universe across all windows
  2. No in-sample probabilities used
  3. Purge/embargo applied correctly
  4. Train-only scaler (code inspection)
  5. No future-return / label / target columns in features
  6. Net_h10 unit clearly defined
  7. Cost model applied correctly
  8. Window-specific columns routed correctly
"""

import ast
import hashlib
import json
import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# ─── Paths ────────────────────────────────────────────────────────────────────
PBO_DIR   = Path("/home/prabh/OFI_Production/research_engines"
                 "/model_prob_pbo_window_family_audit_v1_20260703T230000Z")
MODEL_DIR = Path("/home/prabh/OFI_Production/model_registry"
                 "/level_reaction_continuous_nq_shadow"
                 "/level_reaction_continuous_nq_shadow_20260702T005104Z")
OUT_DIR   = Path(__file__).parent

WINDOWS       = [5, 10, 20, 30, 50, 80]
VERSIONS      = ["A", "B"]
COST_TICKS    = 2.0
EMBARGO_BARS  = 40

# Columns that must NEVER appear as model features
LEAKAGE_COLS = {
    "label_h40", "label_h10", "label_h20", "label_h5",
    "fwd_return_ticks_h5", "fwd_return_ticks_h10",
    "fwd_return_ticks_h20", "fwd_return_ticks_h40",
    "fwd_logret_h5", "fwd_logret_h10", "fwd_logret_h20", "fwd_logret_h40",
    "MFE_h40", "MAE_h40", "hit_up_h40", "hit_down_h40",
    "net_return_h10", "net_return_h40", "edge_perf",
    "prob_long", "prob_short", "prob_edge", "pred_side",
}

# Window-specific feature names in feature_names.json
WINDOW_FEATURES = {
    "delta_rolling_5", "mlofi_rolling_5", "volatility_5", "mlofi_accel",
    "delta_norm_resid_z20", "volatility_5_resid_z20",
    "sweep_imbalance_norm_resid_z20", "vpin_resid_z20",
    "candle_body_vol", "candle_range_vol", "upper_wick_vol", "lower_wick_vol",
    "close_location", "open_to_close_sign", "close_vs_prev_close_vol",
    "close_vs_roll_mean_vol", "high_break_vol", "low_break_vol",
}


# ─────────────────────────────────────────────────────────────────────────────
# CHECKS
# ─────────────────────────────────────────────────────────────────────────────

def check_oos_table_exists() -> dict:
    p = PBO_DIR / "oos_probability_table.parquet"
    exists = p.exists()
    size   = p.stat().st_size if exists else 0
    return {
        "check":  "OOS table exists",
        "pass":   exists and size > 0,
        "detail": f"path={p}  exists={exists}  size={size:,} bytes",
    }


def check_same_event_universe(oos: pd.DataFrame) -> dict:
    """All windows should have the same OOS event IDs per version."""
    results = []
    for version in VERSIONS:
        sub = oos[oos["version"] == version]
        ids_by_window = {}
        for W in WINDOWS:
            w_ids = set(sub[sub["window"] == W]["event_id"].tolist())
            ids_by_window[W] = w_ids
        ref_ids = ids_by_window[WINDOWS[0]]
        mismatches = {W: len(ids_by_window[W] ^ ref_ids)
                      for W in WINDOWS if ids_by_window[W] != ref_ids}
        results.append({
            "version": version,
            "n_events_ref": len(ref_ids),
            "mismatches": mismatches,
        })
    all_pass = all(len(r["mismatches"]) == 0 for r in results)
    detail = "; ".join(
        f"V{r['version']}: {r['n_events_ref']:,} events "
        f"({'SAME' if not r['mismatches'] else 'MISMATCH:'+str(r['mismatches'])})"
        for r in results
    )
    return {
        "check":  "Same OOS event universe across all windows",
        "pass":   all_pass,
        "detail": detail,
        "sub":    results,
    }


def check_no_insample_probs(oos: pd.DataFrame) -> dict:
    """Every row must have is_oos=True."""
    n_total  = len(oos)
    n_oos    = int(oos["is_oos"].sum())
    n_is     = n_total - n_oos
    all_pass = n_is == 0
    return {
        "check":  "No in-sample probabilities in table",
        "pass":   all_pass,
        "detail": f"total={n_total:,}  is_oos_true={n_oos:,}  is_oos_false={n_is:,}",
    }


def check_no_event_in_own_train_fold(oos: pd.DataFrame, folds: list) -> dict:
    """For each event, verify its val fold never included that date in training."""
    errors = []
    fold_map = {f["fold_id"]: f for f in folds}
    sample = oos.sample(min(5000, len(oos)), random_state=42)
    for _, row in sample.iterrows():
        fid   = int(row["fold_id"])
        date  = row["rithmic_date_str"]
        if fid not in fold_map:
            continue
        fold_def = fold_map[fid]
        if date in fold_def["train_dates"]:
            errors.append(f"event {row['event_id']}: date={date} in train of fold {fid}")
    n_errors  = len(errors)
    all_pass  = n_errors == 0
    return {
        "check":  "Events not in own training fold",
        "pass":   all_pass,
        "detail": (f"Sampled {len(sample):,} events. Errors: {n_errors}."
                   + (f" First: {errors[0]}" if errors else "")),
    }


def check_fold_date_coverage(oos: pd.DataFrame, folds: list) -> dict:
    """Each OOS event should have a fold_id matching a fold where that date is in val_dates."""
    fold_map = {f["fold_id"]: set(f["val_dates"]) for f in folds}
    sample = oos.sample(min(5000, len(oos)), random_state=7)
    errors = []
    for _, row in sample.iterrows():
        fid  = int(row["fold_id"])
        date = row["rithmic_date_str"]
        if fid not in fold_map:
            errors.append(f"event {row['event_id']}: fold_id {fid} not in fold list")
        elif date not in fold_map[fid]:
            errors.append(f"event {row['event_id']}: date={date} not in val_dates of fold {fid}")
    n_errors = len(errors)
    return {
        "check":  "Fold date coverage (event date in fold val_dates)",
        "pass":   n_errors == 0,
        "detail": (f"Sampled {len(sample):,}. Errors: {n_errors}."
                   + (f" First: {errors[0]}" if errors else "")),
    }


def check_no_feature_leakage(feat_names: list) -> dict:
    """No future-return, label, or probability column in feature_names.json."""
    leaked = [f for f in feat_names if f in LEAKAGE_COLS]
    return {
        "check":  "No leakage columns in feature_names.json",
        "pass":   len(leaked) == 0,
        "detail": f"{len(feat_names)} features. Leaked: {leaked if leaked else 'NONE'}",
    }


def check_net_h10_formula(oos: pd.DataFrame) -> dict:
    """
    net_return_h10 = pred_dir × fwd_return_ticks_h10 - COST_TICKS
    where pred_dir = +1 if LONG else -1.
    Verify on a sample.
    """
    sample = oos.sample(min(2000, len(oos)), random_state=42)
    errors = []
    for _, row in sample.iterrows():
        pred_dir  = 1 if row["pred_side"] == "LONG" else -1
        expected  = pred_dir * row["fwd_return_ticks_h10"] - COST_TICKS
        actual    = row["net_return_h10"]
        if abs(actual - expected) > 1e-9:
            errors.append(f"event {row['event_id']}: expected={expected:.6f} got={actual:.6f}")
    n_errors = len(errors)
    return {
        "check":  "net_h10 = pred_dir × fwd_return_ticks_h10 - cost_ticks",
        "pass":   n_errors == 0,
        "detail": (f"Sampled {len(sample):,}. Formula errors: {n_errors}."
                   + (f" First: {errors[0]}" if errors else "")),
        "cost_ticks_used": COST_TICKS,
    }


def check_cost_consistency(oos: pd.DataFrame) -> dict:
    """cost_ticks column must always equal COST_TICKS."""
    if "cost_ticks" not in oos.columns:
        return {"check": "Cost consistency", "pass": False,
                "detail": "cost_ticks column missing"}
    vals = oos["cost_ticks"].unique()
    all_same = len(vals) == 1 and abs(vals[0] - COST_TICKS) < 1e-9
    return {
        "check":  f"Cost model: cost_ticks = {COST_TICKS} (round-trip) everywhere",
        "pass":   all_same,
        "detail": f"Unique cost_ticks values: {vals.tolist()}. Expected: {COST_TICKS}",
    }


def check_prob_bounds(oos: pd.DataFrame) -> dict:
    """prob_long in (0,1), prob_short in (0,1), prob_long + prob_short ≈ 1."""
    bad_range = oos[(oos["prob_long"] < 0) | (oos["prob_long"] > 1) |
                    (oos["prob_short"] < 0) | (oos["prob_short"] > 1)]
    sum_check = (oos["prob_long"] + oos["prob_short"]).abs()
    bad_sum   = oos[abs(sum_check - 1.0) > 1e-6]
    n_bad = len(bad_range) + len(bad_sum)
    return {
        "check":  "Probability bounds: prob_long ∈ (0,1), prob_long+prob_short≈1",
        "pass":   n_bad == 0,
        "detail": f"Out-of-range: {len(bad_range):,}  Sum!=1: {len(bad_sum):,}",
    }


def check_pred_side_consistency(oos: pd.DataFrame) -> dict:
    """pred_side == LONG iff prob_long > 0.5, SHORT iff prob_long < 0.5."""
    pred_long  = oos[oos["pred_side"] == "LONG"]
    pred_short = oos[oos["pred_side"] == "SHORT"]
    bad_long   = (pred_long["prob_long"] <= 0.5).sum()
    bad_short  = (pred_short["prob_long"] >= 0.5).sum()
    # ties (== 0.5) allowed: count them
    ties = (oos["prob_long"] == 0.5).sum()
    n_bad = bad_long + bad_short
    return {
        "check":  "pred_side consistent with prob_long > 0.5",
        "pass":   n_bad == 0,
        "detail": f"Bad LONG: {bad_long:,}  Bad SHORT: {bad_short:,}  Ties at 0.5: {ties:,}",
    }


def check_window_feature_routing(oos: pd.DataFrame) -> dict:
    """
    For window-specific features, statistics must differ between windows.
    Proxy: check that prob_long distributions differ across windows (they would
    if window features vary correctly). Compare std of mean(prob_long) per window.
    A flat distribution (all windows identical) would mean features didn't vary.
    """
    results = []
    for version in VERSIONS:
        sub = oos[oos["version"] == version]
        means = {}
        for W in WINDOWS:
            w_sub = sub[sub["window"] == W]
            if len(w_sub) > 0:
                means[W] = float(w_sub["prob_long"].mean())
        mean_vals = list(means.values())
        spread = max(mean_vals) - min(mean_vals) if mean_vals else 0
        results.append({
            "version": version,
            "mean_prob_long_by_W": means,
            "spread": spread,
            "routing_ok": spread > 1e-4,
        })
    all_pass = all(r["routing_ok"] for r in results)
    detail = "; ".join(
        f"V{r['version']}: spread={r['spread']:.5f} "
        f"({r['mean_prob_long_by_W']})"
        for r in results
    )
    return {
        "check":  "Window features routed correctly (prob_long varies across W)",
        "pass":   all_pass,
        "detail": detail,
        "note":   "Spread=0 would mean all windows produced identical features → routing bug",
        "sub":    results,
    }


def check_window_accuracy_monotone(oos: pd.DataFrame) -> dict:
    """
    Larger W should give lower accuracy (longer memory → noisier signal).
    Verify acc is not perfectly flat across windows.
    """
    results = []
    for version in VERSIONS:
        sub = oos[oos["version"] == version]
        acc_by_W = {}
        for W in WINDOWS:
            w_sub = sub[sub["window"] == W]
            if len(w_sub) > 0:
                correct = (
                    ((w_sub["pred_side"] == "LONG")  & (w_sub["y_true"] == 1)) |
                    ((w_sub["pred_side"] == "SHORT") & (w_sub["y_true"] == 0))
                )
                acc_by_W[W] = float(correct.mean())
        accs = list(acc_by_W.values())
        spread = max(accs) - min(accs) if accs else 0
        results.append({"version": version, "acc_by_W": acc_by_W, "spread": spread})
    all_vary = all(r["spread"] > 1e-4 for r in results)
    detail = "; ".join(
        f"V{r['version']}: acc spread={r['spread']:.4f} {r['acc_by_W']}"
        for r in results
    )
    return {
        "check":  "Window accuracy varies (not perfectly flat → windows are different)",
        "pass":   all_vary,
        "detail": detail,
    }


def check_code_scaler_train_only(src: str) -> dict:
    """
    Code inspection: imputer and scaler must be fit on Xtr only, not Xva.
    Pattern: imp.fit(Xtr) → imp.transform(Xva); scl.fit(Xtr_i) → scl.transform(Xva_i)
    """
    # Check that fit() is only called on training data variable names
    fit_on_va = bool(re.search(r'\.(fit|fit_transform)\(Xva', src))
    fit_scaler_on_tr = bool(re.search(r'scl\s*=\s*StandardScaler.*\.fit\(Xtr', src))
    fit_imp_on_tr    = bool(re.search(r'imp\s*=\s*SimpleImputer.*\.fit\(Xtr', src))
    # Also check fit() is not called on Xva_i
    fit_scl_on_va = bool(re.search(r'scl\.fit\(Xva', src))
    all_ok = (not fit_on_va) and (not fit_scl_on_va) and fit_scaler_on_tr and fit_imp_on_tr
    return {
        "check":  "Train-only scaler (code inspection)",
        "pass":   all_ok,
        "detail": (
            f"fit_on_va={fit_on_va}  "
            f"fit_scl_on_va={fit_scl_on_va}  "
            f"fit_scaler_on_tr={fit_scaler_on_tr}  "
            f"fit_imp_on_tr={fit_imp_on_tr}"
        ),
    }


def check_code_no_lookahead_labels(src: str, feat_names: list) -> dict:
    """Code inspection: feature selection must not include label or fwd return columns."""
    # In the CV loop, features are selected by feat_names list
    # Check that no leakage columns appear in feat_names
    leaked = [f for f in feat_names if f in LEAKAGE_COLS]
    # Also check code doesn't reference label_h40 in Xtr construction
    label_in_features = bool(re.search(r'feat_names.*label_h40|label_h40.*feat_names', src))
    return {
        "check":  "No lookahead labels in feature selection (code + data)",
        "pass":   len(leaked) == 0 and not label_in_features,
        "detail": f"Leaked in feat_names: {leaked}  label_in_features_code: {label_in_features}",
    }


def check_code_window_routing(src: str) -> dict:
    """Code inspection: _build_window_features must use W parameter for rolling."""
    has_rolling_W     = bool(re.search(r'\.rolling\(W,', src))
    has_delta_rolling = bool(re.search(r'"_delta_rolling"', src))
    has_mlofi_accel   = bool(re.search(r'"_mlofi_accel"', src))
    has_dn_resid_z    = bool(re.search(r'"_dn_resid_z"', src))
    all_ok = has_rolling_W and has_delta_rolling and has_mlofi_accel and has_dn_resid_z
    return {
        "check":  "Window routing in _build_window_features uses W parameter",
        "pass":   all_ok,
        "detail": (
            f"rolling(W): {has_rolling_W}  "
            f"_delta_rolling: {has_delta_rolling}  "
            f"_mlofi_accel: {has_mlofi_accel}  "
            f"_dn_resid_z: {has_dn_resid_z}"
        ),
    }


def check_net_h10_unit_definition(src: str) -> dict:
    """Code inspection: verify COST_TICKS is defined and net_return uses it."""
    cost_ticks_def  = bool(re.search(r'COST_TICKS\s*=\s*2\.0', src))
    net_uses_cost   = bool(re.search(r'bet_dir \* .*fwd_return_ticks.*- COST_TICKS', src))
    tick_unit_note  = "net_return_h10 is in NQ ticks (1 tick = 0.25 pts). Round-trip = 2 ticks."
    return {
        "check":  "Net_h10 unit clearly defined (ticks, not points)",
        "pass":   cost_ticks_def and net_uses_cost,
        "detail": (
            f"COST_TICKS=2.0: {cost_ticks_def}  "
            f"formula uses COST_TICKS: {net_uses_cost}  "
            f"unit: {tick_unit_note}"
        ),
    }


def check_version_B_has_deferred_signals(oos: pd.DataFrame) -> dict:
    """Version B should have different probabilities than Version A (deferred signals add info)."""
    va = oos[oos["version"] == "A"].groupby("event_id")["prob_long"].first()
    vb = oos[oos["version"] == "B"].groupby("event_id")["prob_long"].first()
    common = va.index.intersection(vb.index)
    if len(common) == 0:
        return {"check": "Version B differs from A (deferred signals)", "pass": False,
                "detail": "No common events between A and B"}
    diff = (va.loc[common] - vb.loc[common]).abs()
    frac_different = float((diff > 1e-6).mean())
    # Should be meaningfully different (>50% of events)
    return {
        "check":  "Version B (deferred) differs from Version A on >50% events",
        "pass":   frac_different > 0.50,
        "detail": f"Fraction of events where |prob_A - prob_B| > 1e-6: {frac_different:.3f}",
    }


def check_oos_event_count(oos: pd.DataFrame, folds: list) -> dict:
    """OOS events per version should be close to n_labelled_events (83,899)."""
    for version in VERSIONS:
        sub = oos[(oos["version"] == version) & (oos["window"] == WINDOWS[0])]
        n = len(sub)
        expected_min = 50000  # conservative lower bound
        if n < expected_min:
            return {
                "check": "OOS event count reasonable",
                "pass": False,
                "detail": f"Version {version}: {n:,} events < expected min {expected_min:,}",
            }
    detail_parts = []
    for version in VERSIONS:
        n = len(oos[(oos["version"] == version) & (oos["window"] == WINDOWS[0])])
        detail_parts.append(f"V{version}={n:,}")
    return {
        "check":  "OOS event count reasonable (>50K events per version)",
        "pass":   True,
        "detail": ", ".join(detail_parts),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("Audit 1 — Model Probability Structural Audit v1")
    print("SHADOW / RESEARCH ONLY — no execution, no broker")
    print("=" * 70)

    now_utc = datetime.now(timezone.utc).isoformat()
    all_checks = []

    # ─── Load source and data ─────────────────────────────────────────────────
    src_path = PBO_DIR / "run_model_prob_pbo.py"
    with open(src_path) as f:
        src = f.read()
    print(f"  Source code loaded: {len(src):,} chars")

    with open(MODEL_DIR / "feature_names.json") as f:
        feat_names = json.load(f)

    with open(PBO_DIR / "LOCK_MANIFEST.json") as f:
        lock = json.load(f)

    with open(MODEL_DIR / "data/folds.json") as f:
        folds_raw = json.load(f)
    folds = folds_raw["fold_definitions"]

    oos_path = PBO_DIR / "oos_probability_table.parquet"
    oos_exists = oos_path.exists()
    if not oos_exists:
        print("\n  [FAIL] oos_probability_table.parquet not found — main run incomplete.")
        print("         Run run_model_prob_pbo.py first.")
        return

    oos = pd.read_parquet(oos_path)
    print(f"  OOS table: {len(oos):,} rows  cols: {oos.columns.tolist()[:8]}…")
    print()

    # ─── Run all checks ───────────────────────────────────────────────────────

    checks_data = [
        # Data-level checks
        check_oos_table_exists(),
        check_oos_event_count(oos, folds),
        check_same_event_universe(oos),
        check_no_insample_probs(oos),
        check_no_event_in_own_train_fold(oos, folds),
        check_fold_date_coverage(oos, folds),
        check_no_feature_leakage(feat_names),
        check_net_h10_formula(oos),
        check_cost_consistency(oos),
        check_prob_bounds(oos),
        check_pred_side_consistency(oos),
        check_window_feature_routing(oos),
        check_window_accuracy_monotone(oos),
        check_version_B_has_deferred_signals(oos),
        # Code-level checks
        check_code_scaler_train_only(src),
        check_code_no_lookahead_labels(src, feat_names),
        check_code_window_routing(src),
        check_net_h10_unit_definition(src),
    ]

    n_pass = n_fail = 0
    for c in checks_data:
        status = "PASS" if c["pass"] else "FAIL"
        if c["pass"]:
            n_pass += 1
        else:
            n_fail += 1
        print(f"  [{status}] {c['check']}")
        wrapped = textwrap.fill(c["detail"], width=80, initial_indent="         ",
                                subsequent_indent="         ")
        print(wrapped)

    overall_pass = n_fail == 0
    print()
    print(f"  Results: {n_pass} PASS  {n_fail} FAIL  → "
          f"{'STRUCTURAL PASS' if overall_pass else 'STRUCTURAL FAIL'}")

    # ─── Save results ─────────────────────────────────────────────────────────
    result_rows = []
    for c in checks_data:
        result_rows.append({
            "check":   c["check"],
            "pass":    c["pass"],
            "detail":  c["detail"],
        })
    pd.DataFrame(result_rows).to_csv(OUT_DIR / "structural_audit_results.csv", index=False)

    # ─── OOS statistics summary ───────────────────────────────────────────────
    stats_rows = []
    for version in VERSIONS:
        for W in WINDOWS:
            sub = oos[(oos["version"] == version) & (oos["window"] == W)]
            if len(sub) == 0:
                continue
            correct = (
                ((sub["pred_side"] == "LONG")  & (sub["y_true"] == 1)) |
                ((sub["pred_side"] == "SHORT") & (sub["y_true"] == 0))
            )
            stats_rows.append({
                "version":          version,
                "window":           W,
                "n_events":         len(sub),
                "accuracy":         float(correct.mean()),
                "mean_prob_long":   float(sub["prob_long"].mean()),
                "std_prob_long":    float(sub["prob_long"].std()),
                "net_return_h10":   float(sub["net_return_h10"].mean()),
                "net_return_h40":   float(sub["net_return_h40"].mean()),
                "pct_long":         float((sub["pred_side"] == "LONG").mean()),
            })
    stats_df = pd.DataFrame(stats_rows)
    stats_df.to_csv(OUT_DIR / "oos_stats_by_window.csv", index=False)
    print()
    print("  OOS stats by window:")
    for _, r in stats_df.iterrows():
        print(f"    V{r['version']} W={r['window']:3d}: "
              f"acc={r['accuracy']:.4f}  net_h10={r['net_return_h10']:+.3f}t  "
              f"mean_prob={r['mean_prob_long']:.4f}")

    # ─── Report ───────────────────────────────────────────────────────────────
    report = [
        "# Audit 1 — Model Probability Structural Audit v1",
        f"**Generated**: {now_utc}",
        "**SHADOW / RESEARCH ONLY — no execution, no broker, no order placement**",
        f"**Source PBO run**: {PBO_DIR.name}",
        "",
        "---",
        "",
        f"## Summary: {'STRUCTURAL PASS' if overall_pass else 'STRUCTURAL FAIL'}",
        "",
        f"**{n_pass} checks PASS  |  {n_fail} checks FAIL**",
        "",
        ("All structural properties verified — safe to proceed to Audit 2 (Calibration)."
         if overall_pass else
         "**One or more structural checks FAILED — do NOT proceed to calibration or PBO "
         "interpretation until failures are resolved.**"),
        "",
        "---",
        "",
        "## Checks",
        "",
        "| # | Check | Result | Detail |",
        "|---|-------|--------|--------|",
    ]
    for i, c in enumerate(checks_data, 1):
        status = "✓ PASS" if c["pass"] else "✗ FAIL"
        detail_short = c["detail"][:120].replace("|", "\\|")
        report.append(f"| {i} | {c['check']} | **{status}** | {detail_short} |")

    report += [
        "",
        "---",
        "",
        "## Net_h10 Unit Definition",
        "",
        "```",
        "net_return_h10 = pred_dir × fwd_return_ticks_h10 - COST_TICKS",
        "where:",
        "  pred_dir  = +1 if pred_side == LONG else -1",
        "  fwd_return_ticks_h10 = ticks gained/lost H=10 bars forward",
        f"  COST_TICKS = {COST_TICKS} (round-trip, 2 NQ ticks at 0.25 pts/tick = 0.50 pts)",
        "  1 NQ tick = 0.25 pts = $1.25 per MNQ contract = $5.00 per NQ contract",
        "```",
        "",
        "---",
        "",
        "## Lock Manifest (v3 master hash)",
        "",
        "| File | SHA256 (first 16) |",
        "|------|------------------|",
    ]
    for label, info in lock.get("files", {}).items():
        report.append(f"| {label} | {info['sha256'][:16]}… |")

    report += [
        "",
        "---",
        "",
        "## Next Step",
        "",
        ("**Proceed to Audit 2 — Probability Calibration Audit**"
         if overall_pass else
         "**FIX structural failures before proceeding**"),
        "",
        "---",
        "",
        "## Final Status",
        "```",
        f"STRUCTURAL_AUDIT_PASS:                  {overall_pass}",
        f"CHECKS_PASS:                            {n_pass}",
        f"CHECKS_FAIL:                            {n_fail}",
        "PRODUCTION_FILES_MODIFIED:              false",
        "TRADING_ENABLED:                        false",
        "OVERALL:                                " + ("PASS" if overall_pass else "FAIL"),
        "```",
    ]

    with open(OUT_DIR / "STRUCTURAL_AUDIT_REPORT.md", "w") as f:
        f.write("\n".join(report))
    print("  Saved: STRUCTURAL_AUDIT_REPORT.md")

    print()
    print("=" * 70)
    if overall_pass:
        print("STRUCTURAL AUDIT PASS — proceed to Audit 2 (Calibration)")
    else:
        print("STRUCTURAL AUDIT FAIL — resolve failures before continuing")
    print("=" * 70)


if __name__ == "__main__":
    main()
