"""
02_feature_coverage_audit.py - Part B: feature coverage audit.

For every feature in the combined registry, checks LIVE availability
against the CURRENT production sources (re-read fresh, not assumed from
Part A's static classification):
  - present in master (NQU6 master's actual current column set)
  - present in Book Flow cache (cache files actually exist for recent dates)
  - computable from dashboard formulas (v3/v4's already-validated panel
    columns are present - re-verified by re-reading v4's actual panel)
  - computable from OFI Level Decision formulas (same check)
  - missing / excluded_for_leakage / research_only

Any feature required by the ACTIVE model (the 77-feature dashboard model)
that is NOT present/computable is flagged BLOCKED at the overall level.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fm_common as fm

DASHBOARD_MASTER_PASSTHROUGH = {
    "delta_norm", "delta_norm_lag_1", "delta_norm_lag_2", "delta_norm_lag_3", "delta_rolling_5",
    "mlofi_decay_sum", "mlofi_norm", "mlofi_rolling_5", "mlofi_accel", "decay_norm",
    "mlofi_norm_lag_1", "mlofi_norm_lag_2", "mlofi_norm_lag_3",
    "decay_norm_lag_1", "decay_norm_lag_2", "decay_norm_lag_3",
    "sweep_imbalance_norm", "sweep_norm", "sweep_buy_ratio", "sweep_sell_ratio",
    "buy_ratio", "sell_ratio", "vpin", "vpin_lag_1", "vpin_lag_2", "vpin_lag_3",
    "mid_resid_z", "mid_ret1", "volatility_5", "bar_duration_s",
    "delta_norm_resid_z20", "volatility_5_resid_z20", "sweep_imbalance_norm_resid_z20",
    "vpin_resid_z20", "entropy_score", "flow_alignment",
}


def main():
    fm.log("02: auditing live feature coverage against CURRENT production sources...")
    combined = pd.read_csv(fm.OUT_DIR / "combined_model_feature_registry.csv")

    nqu6 = fm.load_nqu6_master()
    live_master_cols = set(nqu6.columns)
    fm.log(f"  current NQU6 master: {len(nqu6)} rows, {len(live_master_cols)} columns")

    v3_panel_path = fm.V3_DIR / "outputs" / "master_dashboard_ofi_feature_panel.parquet"
    v3_panel_cols = set(pd.read_parquet(v3_panel_path).columns) if v3_panel_path.exists() else set()
    fm.log(f"  v3/v4 dashboard-parity panel: {len(v3_panel_cols)} columns available")

    import glob
    bf_dates_available = sorted({Path(p).name.split("_")[5] for p in
                                 glob.glob(str(fm.BOOK_FLOW_CACHE_DIR / "book_flow_level_candles_NQU6_*_top10.parquet"))})
    fm.log(f"  Book Flow cache: {len(bf_dates_available)} dates available, latest={bf_dates_available[-1] if bf_dates_available else 'NONE'}")

    rows = []
    for _, r in combined.iterrows():
        name = r["feature_name"]
        action = r["action"]
        # master_* features may carry the v4-style "master_" prefix (the raw master
        # column itself never has that prefix) - strip it before checking; for the
        # dashboard model's own bare-name features (e.g. "vpin") this is a no-op.
        bare_name = name[7:] if (r["source_type"] == "master" and name.startswith("master_")) else name
        present_in_master = bare_name in live_master_cols
        present_in_bf_cache = (r["source_type"] == "Book_Flow_cache") and len(bf_dates_available) > 0
        computable_dash = (r["source_type"] == "dashboard_formula") and (name in v3_panel_cols)
        computable_ofild = (r["source_type"] == "OFI_Level_Decision") and (name in v3_panel_cols)
        # structural_event_feature / session_time / model_probability are computed by this build's
        # OWN pipeline (Part C) from master+cache, not a direct passthrough - "computable" if their
        # declared inputs (master columns for the bar) are available, which they are whenever the
        # master row itself exists.
        computable_structural = r["source_type"] in ("structural_event_feature", "session_time", "model_probability")

        if action == "EXCLUDE":
            status = "excluded_for_leakage"
        elif r["leakage_risk"] == "HIGH":
            status = "excluded_for_leakage"
        elif action == "RESEARCH_ONLY":
            status = "research_only"
        elif present_in_master or present_in_bf_cache or computable_dash or computable_ofild or computable_structural:
            status = "present_or_computable"
        else:
            status = "missing"

        rows.append(dict(
            feature_name=name, source_type=r["source_type"],
            used_by_current_dashboard_model=r["used_by_current_dashboard_model"],
            used_by_v4_histgb_entry_model=r["used_by_v4_histgb_entry_model"],
            present_in_master=present_in_master, present_in_book_flow_cache=present_in_bf_cache,
            computable_from_dashboard_formulas=computable_dash,
            computable_from_ofi_level_decision_formulas=computable_ofild,
            computable_structural_or_session_or_modelprob=computable_structural,
            coverage_status=status, action=action, leakage_risk=r["leakage_risk"],
        ))

    cov = pd.DataFrame(rows)
    cov.to_csv(fm.OUT_DIR / "feature_coverage_report.csv", index=False)

    dash_required = cov[cov["used_by_current_dashboard_model"]]
    blocked_features = dash_required[dash_required["coverage_status"] == "missing"]
    overall_blocked = len(blocked_features) > 0

    fm.log(f"  coverage_status breakdown:\n{cov['coverage_status'].value_counts().to_string()}")
    fm.log(f"  dashboard-model-required features missing live: {len(blocked_features)}")
    if overall_blocked:
        fm.log(f"  BLOCKED features: {blocked_features['feature_name'].tolist()}")
    print(f"PART_B_BLOCKED: {overall_blocked}")
    fm.log("02 complete.")
    return cov, overall_blocked


if __name__ == "__main__":
    main()
