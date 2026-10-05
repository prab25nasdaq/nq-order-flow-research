"""
01_feature_discovery.py - Part A: feature discovery for the current
dashboard/level-reaction model AND the v4 HistGB Entry ACT/PASS model.

Sources inspected (read-only):
  - active release's feature_names.json, FEATURE_POLICY.md, LABEL_POLICY.md,
    EVENT_POLICY.md, training_config.json, reaction_type_metadata.json,
    pipeline_continuous.py (for exact provenance of each of the 77 features)
  - v4's entry_meta_label_dataset_v4.parquet (the exact 132-column feature
    set actually used by script 07_train_entry_models.py's
    select_feature_cols(), reproduced from FEATURE_PREFIXES/
    STRUCTURAL_FEATURES verbatim - not re-derived, not guessed)
  - v4's v4_feature_inventory.csv / v4_leakage_guard_report.csv

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fm_common as fm

# ── 1. The 77 features of the CURRENT active dashboard/level-reaction model ──
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
DASHBOARD_SESSION_TIME = {"sess_Asia", "sess_EU", "sess_US_Open", "sess_US_AM", "sess_US_PM", "sess_US_Late"}
DASHBOARD_LEVEL_CONTEXT_NATIVE_LOOKAHEAD = {
    "dist_to_poc_ticks", "dist_to_vah_ticks", "dist_to_val_ticks", "dist_to_hvn_ticks", "dist_to_lvn_ticks",
    "dist_to_poc_vol", "dist_to_vah_vol", "dist_to_val_vol", "dist_to_hvn_vol", "dist_to_lvn_vol",
    "inside_value_area", "above_vah", "below_val",
}
DASHBOARD_OHLC_VOL_PATH = {
    "candle_body_vol", "candle_range_vol", "upper_wick_vol", "lower_wick_vol", "close_location",
    "open_to_close_sign", "close_vs_prev_close_vol", "close_vs_roll_mean_vol", "high_break_vol", "low_break_vol",
}
DASHBOARD_TOUCH_TRACKING = {"touch_count_past_only", "bars_since_prior_touch"}
DASHBOARD_LEVEL_ONEHOT = {"lvl_POC", "lvl_VAH", "lvl_VAL", "lvl_HVN", "lvl_LVN"}
DASHBOARD_RXN_ONEHOT = {"rxn_rejection_from_above", "rxn_rejection_from_below", "rxn_absorption",
                        "rxn_acceptance", "rxn_neutral_touch"}


def load_dashboard_feature_names() -> list:
    release = fm.resolved_active_release()
    return json.loads((release / "feature_names.json").read_text())


def load_v4_histgb_features() -> list:
    df = pd.read_parquet(fm.V4_DIR / "outputs" / "entry_meta_label_dataset_v4.parquet")
    FEATURE_PREFIXES = ("master_", "dash_", "ofild_", "bf_", "modelprob_", "rxn_", "lvl_", "gate_")
    STRUCTURAL_FEATURES = ("side_primary", "distance_ticks", "touch_count_past_only", "bars_since_prior_touch")
    return [c for c in df.columns if (c in STRUCTURAL_FEATURES or c.startswith(FEATURE_PREFIXES))
            and pd.api.types.is_numeric_dtype(df[c])]


def classify_dashboard_feature(name: str) -> dict:
    release = fm.resolved_active_release()
    if name in DASHBOARD_MASTER_PASSTHROUGH:
        return dict(source_type="master", source_file=str(fm.MASTER_NQU6),
                   formula_or_column=f"direct master column '{name}'", rolling_window="N/A (computed upstream by parser)",
                   bar_window=1, requires_closed_bar_only=True, live_safe=True, leakage_risk="LOW", action="KEEP")
    if name in DASHBOARD_SESSION_TIME:
        return dict(source_type="session_time", source_file=f"{release}/scripts/pipeline_continuous.py::session_label",
                   formula_or_column="minute_of_day binned (Asia<360,EU<720,US_Open<870,US_AM<1080,US_PM<1320,US_Late else)",
                   rolling_window="N/A", bar_window=1, requires_closed_bar_only=True, live_safe=True,
                   leakage_risk="LOW", action="KEEP")
    if name in DASHBOARD_LEVEL_CONTEXT_NATIVE_LOOKAHEAD:
        return dict(source_type="structural_event_feature",
                   source_file=f"{release}/scripts/pipeline_continuous.py::volume_profile_levels+build_level_stream",
                   formula_or_column="distance to per-day NATIVE POC/VAH/VAL/HVN/LVN (full-session volume profile)",
                   rolling_window="full trading day (NOT prior-bar-only)", bar_window=1,
                   requires_closed_bar_only=True, live_safe=True, leakage_risk="MEDIUM",
                   action="KEEP",
                   note="native level uses the FULL day's volume profile - early-session bars are compared "
                        "against a level informed by later-session volume. The existing model's own "
                        "leakage_audit.md does not flag this; this registry flags it MEDIUM for completeness "
                        "(stricter than the model's own self-assessment) since it is a genuine lookahead "
                        "characteristic for descriptive/explanatory use, separate from the model's own "
                        "temporal train/test purging which IS sound.")
    if name in DASHBOARD_OHLC_VOL_PATH:
        return dict(source_type="structural_event_feature",
                   source_file=f"{release}/scripts/pipeline_continuous.py::build_event_features::_add_ohlc_vol",
                   formula_or_column=f"derived from continuous_high/low/close/open + volatility_5 ('{name}')",
                   rolling_window="20-bar (close_vs_roll_mean_vol/high_break_vol/low_break_vol only)", bar_window=1,
                   requires_closed_bar_only=True, live_safe=True, leakage_risk="LOW", action="KEEP")
    if name in DASHBOARD_TOUCH_TRACKING:
        return dict(source_type="structural_event_feature",
                   source_file=f"{release}/scripts/pipeline_continuous.py::build_event_stream",
                   formula_or_column=f"'{name}' - PAST-ONLY touch counter per (level_price, level_name)",
                   rolling_window="N/A (cumulative within day, past-only)", bar_window=1,
                   requires_closed_bar_only=True, live_safe=True, leakage_risk="LOW", action="KEEP")
    if name in DASHBOARD_LEVEL_ONEHOT:
        return dict(source_type="structural_event_feature",
                   source_file=f"{release}/scripts/pipeline_continuous.py::build_event_features",
                   formula_or_column=f"one-hot: level_type=='{name[4:]}'", rolling_window="N/A", bar_window=1,
                   requires_closed_bar_only=True, live_safe=True, leakage_risk="LOW", action="KEEP")
    if name in DASHBOARD_RXN_ONEHOT:
        return dict(source_type="structural_event_feature",
                   source_file=f"{release}/scripts/pipeline_continuous.py::classify_bar_reaction",
                   formula_or_column=f"one-hot: '{name[4:]}' in reaction_type", rolling_window="N/A", bar_window=1,
                   requires_closed_bar_only=True, live_safe=True, leakage_risk="LOW", action="KEEP")
    return dict(source_type="UNKNOWN", source_file="UNKNOWN", formula_or_column="UNKNOWN",
               rolling_window="UNKNOWN", bar_window="UNKNOWN", requires_closed_bar_only=True,
               live_safe=False, leakage_risk="HIGH", action="MISSING")


RAW_PRICE_COLS = {"master_px_close", "master_px_high", "master_px_low"}
SESSION_TIME_MASTER = {"master_minute_of_day", "master_dow", "master_tod_minute"}
STRUCTURAL_V4 = {"side_primary", "distance_ticks", "touch_count_past_only", "bars_since_prior_touch"}


def classify_v4_feature(name: str) -> dict:
    if name in RAW_PRICE_COLS:
        return dict(source_type="master", source_file=str(fm.MASTER_NQU6),
                   formula_or_column=f"direct master column '{name[7:]}' (RAW ABSOLUTE PRICE)",
                   rolling_window="N/A", bar_window=1, requires_closed_bar_only=True, live_safe=True,
                   leakage_risk="MEDIUM", action="EXCLUDE",
                   note="RAW ABSOLUTE PRICE included as a trained feature in v4's HistGB entry model - this "
                        "directly contradicts the EXISTING model's OWN hard-banned feature policy "
                        "(FEATURE_POLICY.md: 'Hard-banned: Raw absolute prices'). Not a temporal-leakage issue, "
                        "but a stationarity/generalization risk (a model trained at one price regime may not "
                        "generalize to another) - flagged here as a genuine inconsistency discovered while "
                        "building this registry. EXCLUDED from the new feature master's training-safe set.")
    if name in SESSION_TIME_MASTER:
        return dict(source_type="session_time", source_file=str(fm.MASTER_NQU6),
                   formula_or_column=f"direct master column '{name[7:]}'", rolling_window="N/A", bar_window=1,
                   requires_closed_bar_only=True, live_safe=True, leakage_risk="LOW", action="KEEP")
    if name.startswith("master_"):
        return dict(source_type="master", source_file=str(fm.MASTER_NQU6),
                   formula_or_column=f"direct master column '{name[7:]}'", rolling_window="N/A (computed upstream by parser)",
                   bar_window=1, requires_closed_bar_only=True, live_safe=True, leakage_risk="LOW", action="KEEP")
    if name.startswith("dash_"):
        risk = "HIGH" if "swing" in name.lower() else "LOW"
        return dict(source_type="dashboard_formula", source_file=str(fm.DASHBOARD_SRC),
                   formula_or_column=f"dashboard-parity reproduction (v3_common.py) of '{name[5:]}'",
                   rolling_window="varies by formula (20/64/128/160/250/256 bars - see "
                                  "v3 dashboard_formula_catalog.csv)", bar_window=1,
                   requires_closed_bar_only=True, live_safe=True, leakage_risk=risk,
                   action="KEEP" if risk == "LOW" else "EXCLUDE")
    if name.startswith("ofild_"):
        return dict(source_type="OFI_Level_Decision", source_file=str(fm.OFI_LEVEL_DECISION_SRC),
                   formula_or_column=f"OFI Level Decision tab reproduction of '{name[6:]}'",
                   rolling_window="10-bar display window (N_RECENT_BARS=5 for scoring engine internals)",
                   bar_window=10, requires_closed_bar_only=True, live_safe=True, leakage_risk="LOW", action="KEEP")
    if name.startswith("bf_"):
        return dict(source_type="Book_Flow_cache", source_file=str(fm.BOOK_FLOW_CACHE_DIR),
                   formula_or_column=f"aggregate_book_flow_bars() of '{name[3:]}' (CLOSED bars only)",
                   rolling_window="N/A (per-bar aggregate of resting price levels)", bar_window=1,
                   requires_closed_bar_only=True, live_safe=True, leakage_risk="LOW", action="KEEP")
    if name.startswith("modelprob_"):
        return dict(source_type="model_probability", source_file="predictions.csv (asof-reconstructed)",
                   formula_or_column=f"CURRENT_EVENT/HELD_LAST asof reconstruction of '{name[10:]}'",
                   rolling_window="N/A", bar_window=1, requires_closed_bar_only=False, live_safe=True,
                   leakage_risk="LOW", action="KEEP",
                   note="feature only (model-probability freshness context) - NEVER the training target")
    if name.startswith("gate_"):
        return dict(source_type="structural_event_feature",
                   source_file=f"{fm.MODEL_REGISTRY_DIR}/reaction_type_metadata.json",
                   formula_or_column=f"one-hot: training_gate_status=='{name[5:]}'", rolling_window="N/A",
                   bar_window=1, requires_closed_bar_only=True, live_safe=True, leakage_risk="LOW", action="KEEP")
    if name in STRUCTURAL_V4 or name.startswith("rxn_") or name.startswith("lvl_"):
        return dict(source_type="structural_event_feature", source_file="v4 Part D/E (candidate construction)",
                   formula_or_column=f"v4-rebuilt label-policy structural field '{name}'", rolling_window="N/A",
                   bar_window=1, requires_closed_bar_only=True, live_safe=True, leakage_risk="LOW", action="KEEP")
    return dict(source_type="UNKNOWN", source_file="UNKNOWN", formula_or_column="UNKNOWN",
               rolling_window="UNKNOWN", bar_window="UNKNOWN", requires_closed_bar_only=True,
               live_safe=False, leakage_risk="HIGH", action="MISSING")


def main():
    fm.log("01: discovering features used by the current dashboard model + v4 HistGB entry model...")
    dash_feats = load_dashboard_feature_names()
    v4_feats = load_v4_histgb_features()
    fm.log(f"  current dashboard model: {len(dash_feats)} features")
    fm.log(f"  v4 HistGB entry model: {len(v4_feats)} features")

    dash_rows = []
    for f in dash_feats:
        meta = classify_dashboard_feature(f)
        dash_rows.append(dict(feature_name=f, used_by_current_dashboard_model=True,
                              used_by_v4_histgb_entry_model=False, **meta))
    dash_df = pd.DataFrame(dash_rows)
    dash_df.to_csv(fm.OUT_DIR / "current_dashboard_model_features.csv", index=False)

    v4_rows = []
    for f in v4_feats:
        meta = classify_v4_feature(f)
        v4_rows.append(dict(feature_name=f, used_by_current_dashboard_model=False,
                            used_by_v4_histgb_entry_model=True, **meta))
    v4_df = pd.DataFrame(v4_rows)
    v4_df.to_csv(fm.OUT_DIR / "v4_histgb_entry_features.csv", index=False)

    # normalized-name overlap mapping: dashboard bare name -> v4 prefixed name
    OVERLAP_MAP = {
        "vpin": "master_vpin", "delta_norm": "master_delta_norm", "volatility_5": "master_volatility_5",
        "entropy_score": "master_entropy_score", "flow_alignment": "master_flow_alignment",
        "touch_count_past_only": "touch_count_past_only", "bars_since_prior_touch": "bars_since_prior_touch",
        "lvl_POC": "lvl_POC", "lvl_VAH": "lvl_VAH", "lvl_VAL": "lvl_VAL", "lvl_HVN": "lvl_HVN", "lvl_LVN": "lvl_LVN",
        "rxn_rejection_from_above": "rxn_rejection_from_above", "rxn_rejection_from_below": "rxn_rejection_from_below",
        "rxn_absorption": "rxn_absorption", "rxn_neutral_touch": "rxn_neutral_touch",
        "sweep_imbalance_norm": "master_sweep_imbalance_norm", "buy_ratio": "master_buy_ratio",
        "sell_ratio": "master_sell_ratio", "mid_resid_z": "dash_mid_resid_z",
        "delta_norm_resid_z20": "dash_delta_norm_resid_z20", "volatility_5_resid_z20": "dash_volatility_5_resid_z20",
        "sweep_imbalance_norm_resid_z20": "dash_sweep_imbalance_norm_resid_z20", "vpin_resid_z20": "dash_vpin_resid_z20",
        "mlofi_norm": "master_mlofi_norm",
    }
    PARTIAL_OVERLAP = {"rxn_acceptance": "rxn_breakout_acceptance_above + rxn_breakdown_acceptance_below "
                       "(dashboard model combines both directions into ONE flag; v4 keeps them SEPARATE)"}

    combined_rows = []
    v4_by_name = {r["feature_name"]: r for r in v4_rows}
    dash_by_name = {r["feature_name"]: r for r in dash_rows}
    matched_v4_names = set()
    for f in dash_feats:
        v4_equiv = OVERLAP_MAP.get(f)
        used_v4 = v4_equiv is not None and v4_equiv in v4_by_name
        if used_v4:
            matched_v4_names.add(v4_equiv)
        row = dict(dash_by_name[f])
        row["used_by_v4_histgb_entry_model"] = used_v4
        row["v4_equivalent_feature_name"] = v4_equiv if used_v4 else (
            PARTIAL_OVERLAP.get(f, ""))
        combined_rows.append(row)
    for f in v4_feats:
        if f in matched_v4_names:
            continue
        row = dict(v4_by_name[f])
        row["v4_equivalent_feature_name"] = ""
        combined_rows.append(row)

    combined_df = pd.DataFrame(combined_rows)
    combined_df.to_csv(fm.OUT_DIR / "combined_model_feature_registry.csv", index=False)

    n_overlap = int((combined_df["used_by_current_dashboard_model"].astype(bool) &
                    combined_df["used_by_v4_histgb_entry_model"].astype(bool)).sum())
    fm.log(f"  combined registry: {len(combined_df)} unique features, {n_overlap} overlap between both models")
    fm.log(f"  raw-price features flagged for exclusion: {sorted(RAW_PRICE_COLS)}")
    print(combined_df["source_type"].value_counts())
    fm.log("01 complete.")
    return dash_df, v4_df, combined_df


if __name__ == "__main__":
    main()
