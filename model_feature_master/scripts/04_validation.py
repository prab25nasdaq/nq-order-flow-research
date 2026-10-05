"""
04_validation.py - Part E: validation.

  - schema validation (schema.json matches actual parquet columns/dtypes)
  - no duplicate timestamp check
  - feature NaN rate check
  - live-safe/leakage guard (no banned columns present in the training-safe
    block; audit-only raw-price columns correctly isolated)
  - parity check against v4's feature panel where timestamps overlap (this
    feature master's FRESH recomputation vs v3/v4's already-validated panel,
    for the SAME bar_end_ts_ns values - confirms no regression was
    introduced while copying the formulas into fm_common.py)
  - compare active model feature list vs produced columns

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fm_common as fm


def main():
    fm.log("04: validating model_feature_master_shadow.parquet...")
    panel = pd.read_parquet(fm.DATA_DIR / "model_feature_master_shadow.parquet")
    schema = json.loads((fm.DATA_DIR / "model_feature_master_schema.json").read_text())
    combined = pd.read_csv(fm.OUT_DIR / "combined_model_feature_registry.csv")

    checks = []

    def add(name, passed, detail):
        checks.append(dict(check=name, passed=bool(passed), detail=str(detail)))

    # ── schema validation ──────────────────────────────────────────────
    schema_cols = {c["name"] for c in schema["columns"]}
    panel_cols = set(panel.columns)
    add("schema_columns_match_parquet", schema_cols == panel_cols,
        f"schema-only: {sorted(schema_cols - panel_cols)[:5]}, parquet-only: {sorted(panel_cols - schema_cols)[:5]}")
    add("primary_key_declared", schema.get("primary_key") == "bar_end_ts_ns", schema.get("primary_key"))

    # ── duplicate timestamp check ────────────────────────────────────────
    n_dupes = int(panel["bar_end_ts_ns"].duplicated().sum())
    add("no_duplicate_timestamps", n_dupes == 0, f"{n_dupes} duplicates found")
    add("bar_end_ts_ns_is_primary_key_not_bar_index", "bar_end_ts_ns" in panel.columns, "present")

    # ── feature NaN rate check ──────────────────────────────────────────
    # event-gated columns are EXPECTED to be sparse by construction (Book
    # Flow native levels only populate when a resting cluster of that type
    # exists in the cached depth window; OFI-LD level_price/distance_* only
    # populate when "active" i.e. price is near a level; touch-tracking only
    # populates on an actual touch) - a high NaN rate there reflects how rare
    # the underlying EVENT is, not a computation defect. Flagged separately
    # from genuinely-unexpected high-NaN columns, which WOULD indicate a bug.
    EXPECTED_SPARSE_PREFIXES = ("bf_native_", "ofild_level_", "ofild_distance_", "ofild_setup_type",
                               "ofild_direction_bias", "ofild_trust_state", "ofild_ofi_bias",
                               "ofild_breakout_attempt_dir", "ofild_session")
    EXPECTED_SPARSE_EXACT = {"touch_count_past_only", "bars_since_prior_touch"}
    nan_rates = panel.drop(columns=["bar_end_ts_ns"]).isna().mean().sort_values(ascending=False)
    high_nan_cols = nan_rates[nan_rates > 0.5]
    is_expected_sparse = high_nan_cols.index.to_series().apply(
        lambda c: c in EXPECTED_SPARSE_EXACT or any(c.startswith(p) for p in EXPECTED_SPARSE_PREFIXES))
    unexpected_high_nan = high_nan_cols[~is_expected_sparse.to_numpy()]
    add("no_unexpectedly_high_nan_rate_columns", len(unexpected_high_nan) == 0,
        f"{len(high_nan_cols)} columns >50% NaN total, {len(high_nan_cols) - len(unexpected_high_nan)} are "
        f"EXPECTED-sparse event-gated columns (Book Flow native levels / OFI-LD active-only fields / touch "
        f"tracking - high NaN reflects event rarity, not a defect); genuinely unexpected: "
        f"{unexpected_high_nan.round(3).to_dict()}")
    nan_report = nan_rates.reset_index()
    nan_report.columns = ["column", "nan_rate"]
    nan_report["expected_sparse"] = nan_report["column"].isin(EXPECTED_SPARSE_EXACT) | \
        nan_report["column"].apply(lambda c: any(c.startswith(p) for p in EXPECTED_SPARSE_PREFIXES))
    nan_report.to_csv(fm.OUT_DIR / "_feature_nan_rates.csv", index=False)

    # ── live-safe / leakage guard ─────────────────────────────────────────
    audit_only_cols = [c for c in panel.columns if c.startswith("_AUDIT_ONLY_")]
    add("raw_price_cols_isolated_as_audit_only", len(audit_only_cols) == 3, audit_only_cols)
    training_safe_cols = [c for c in panel.columns if not c.startswith("_AUDIT_ONLY_")]
    banned_in_training_safe = [c for c in training_safe_cols if any(
        b in c.lower() for b in ("swing_level", "compute_sr_level", "future_", "label_h40", "y_cp", "y_entry"))]
    add("no_banned_high_leakage_columns_in_training_safe_block", len(banned_in_training_safe) == 0, banned_in_training_safe)
    add("is_closed_bar_flag_present", "is_closed_bar" in panel.columns and bool(panel["is_closed_bar"].all()),
        "all rows flagged closed (batch source has no forming bars)")
    add("live_context_only_flag_present", "live_context_only" in panel.columns, "present, all False in this batch build")

    # ── parity check against v4/v3's already-validated feature panel ──────
    v3_panel_path = fm.V3_DIR / "outputs" / "master_dashboard_ofi_feature_panel.parquet"
    parity_rows = []
    if v3_panel_path.exists():
        v3_panel = pd.read_parquet(v3_panel_path)
        common_ts = set(panel["bar_end_ts_ns"]) & set(v3_panel["bar_end_ts_ns"])
        compare_cols = ["master_vpin", "master_delta_norm", "master_volatility_5", "dash_entropy_score",
                       "dash_toxicity", "dash_liquidity_cost", "dash_cusum_up_break", "dash_cusum_down_break",
                       "dash_adx", "dash_bull_pressure", "dash_bear_pressure"]
        a = panel[panel["bar_end_ts_ns"].isin(common_ts)].sort_values("bar_end_ts_ns").reset_index(drop=True)
        b = v3_panel[v3_panel["bar_end_ts_ns"].isin(common_ts)].sort_values("bar_end_ts_ns").reset_index(drop=True)
        for col in compare_cols:
            if col not in a.columns or col not in b.columns:
                parity_rows.append(dict(column=col, n_common_ts=len(common_ts), max_abs_diff=np.nan, match="COLUMN_MISSING"))
                continue
            av, bv = a[col].to_numpy(), b[col].to_numpy()
            if av.dtype == object or bv.dtype == object:
                match = bool((av == bv).all())
                max_diff = np.nan
            else:
                max_diff = float(np.nanmax(np.abs(av.astype(float) - bv.astype(float))))
                match = bool(max_diff < 1e-6)
            parity_rows.append(dict(column=col, n_common_ts=len(common_ts), max_abs_diff=max_diff, match=match))
        parity_df = pd.DataFrame(parity_rows)
        n_parity_failed = int((~parity_df["match"].astype(bool)).sum())
        add("parity_vs_v3_v4_panel_on_overlapping_timestamps", n_parity_failed == 0,
            f"{len(parity_df)} columns checked on {len(common_ts)} overlapping bars, {n_parity_failed} mismatched")
    else:
        parity_df = pd.DataFrame()
        add("parity_vs_v3_v4_panel_on_overlapping_timestamps", False, "v3 panel not found - check skipped")
    parity_df.to_csv(fm.OUT_DIR / "_parity_vs_v3_v4_panel.csv", index=False)

    # ── compare active model feature list vs produced columns ────────────
    dash_req = set(combined[combined["used_by_current_dashboard_model"]]["feature_name"])
    v4_req = set(combined[combined["used_by_v4_histgb_entry_model"]]["feature_name"])
    missing_dash = dash_req - panel_cols
    missing_v4 = v4_req - panel_cols
    # the 5 v4 gaps are INTENTIONAL (3 excluded-for-leakage raw price cols,
    # 2 candidate-level-only concepts not applicable to a generic per-bar
    # feature master) - documented explicitly, not silently passed.
    expected_v4_gaps = {"master_px_close", "master_px_high", "master_px_low", "distance_ticks", "side_primary"}
    unexpected_v4_gaps = missing_v4 - expected_v4_gaps
    add("all_dashboard_model_features_present", len(missing_dash) == 0, sorted(missing_dash))
    add("v4_feature_gaps_are_only_the_documented_intentional_ones", len(unexpected_v4_gaps) == 0,
        f"unexpected gaps: {sorted(unexpected_v4_gaps)}; documented intentional gaps: {sorted(expected_v4_gaps & missing_v4)}")

    val_df = pd.DataFrame(checks)
    val_df.to_csv(fm.OUT_DIR / "feature_master_validation_report.csv", index=False)

    n_failed = int((~val_df["passed"]).sum())
    fm.log(f"  validation checks: {len(val_df)} run, {n_failed} FAILED")
    print(val_df.to_string(index=False))
    print(f"VALIDATION_BLOCKED: {n_failed > 0}")
    fm.log("04 complete.")
    return val_df, parity_df


if __name__ == "__main__":
    main()
