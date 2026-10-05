"""
05_build_meta_label_dataset_v2.py - meta_label_dataset_v2_ic_regime.parquet

Builds ONE dataset (on the same-bar-deduplicated, average-uniqueness-weighted
candidate population from script 04) containing BOTH feature sets so that
downstream fold comparisons can select column subsets:

  V1_FEATURE_COLS  - v1's original t0-or-earlier feature set, unchanged,
                     reused read-only from v1's meta_label_dataset.parquet
                     (joined on event_id - the surviving deduplicated rows
                     are a subset of v1's full candidate population).
  IC_REGIME_COLS   - the new reliability columns (rolling_ic, rolling_ic_sign,
                     rolling_ic_abs_strength, rolling_ic_tstat,
                     rolling_ic_stability, rolling_ic_window_n) for each of
                     the 7 raw features, AT THE PRIMARY REGIME HORIZON
                     (config rolling_ic.meta_model_primary_horizon=40, an a
                     priori choice matching the AFML vertical-barrier horizon
                     - not selected by comparing horizons' results). Joined
                     on t0_idx, so by the no-lookahead construction in
                     v2_common.rolling_spearman_ic_no_lookahead, every value
                     attached to an event at t0 used only (feature,
                     forward-return) pairs that had ALREADY resolved by t0.
  INTERACTION_COLS - feature_value x rolling_ic, feature_z20 x rolling_ic,
                     feature_roll5sum x rolling_ic, for each of the 7
                     features (21 columns) - all factors are t0-or-earlier.

eligible_for_meta_training (carried from v1, side_primary!=0 & resolved) and
avg_uniqueness (from script 04, the AFML Ch.4 sample weight to be used in
script 06's LogisticRegression.fit(..., sample_weight=...)) are both present
on every row.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v2_common as v2

cfg = v2.load_config()
RAW_FEATURES = cfg["rolling_ic"]["features"]
PRIMARY_H = cfg["rolling_ic"]["meta_model_primary_horizon"]

V1_FEATURE_COLS = [
    "primary_probability", "primary_confidence", "bars_since_last_event",
    "training_gate_status_code", "reaction_type_code", "nearest_level_type_code",
    "nearest_level_distance", "volatility_target", "volatility_5", "vpin",
    "regime_ic_mlofi", "cusum_up_break", "cusum_down_break", "mlofi_norm", "delta_norm",
    "minute_of_day", "dow", "session_3b_code",
    "bid_pull_pressure_z20", "bid_pull_pressure_roll5sum",
    "ask_pull_minus_bid_pull_z20", "ask_pull_minus_bid_pull_roll5sum",
    "bid_add_minus_ask_add_z20", "bid_add_minus_ask_add_roll5sum",
    "signed_flow_z20", "signed_flow_roll5sum", "abs_flow_z20", "abs_flow_roll5sum",
]


def main():
    v2.log("05: building meta_label_dataset_v2_ic_regime.parquet (v2a + v2b feature sets)...")
    dedup = pd.read_parquet(v2.OUT_DIR / "_dedup_uniqueness_events.parquet")
    # `dedup` (from script 04, derived from v1's candidate_events.parquet) already
    # carries primary_probability/primary_confidence/bars_since_last_event/
    # reaction_type/training_gate_status/nearest_level_type/nearest_level_distance/
    # volatility_target/in_sample_contaminated - pull ONLY the genuinely-new
    # columns from v1's meta_label_dataset.parquet (y_meta, eligibility, session_3b,
    # and the engineered V1_FEATURE_COLS not already present) to avoid duplicate-
    # column collisions on merge.
    v1_meta_extra_cols = ["event_id", "y_meta", "eligible_for_meta_training", "session_3b"] + [
        c for c in V1_FEATURE_COLS if c not in dedup.columns
    ]
    v1_meta = v2.load_v1_meta_label_dataset()[v1_meta_extra_cols]
    df = dedup.merge(v1_meta, on="event_id", how="left")
    v2.log(f"  deduplicated population joined with v1 features: {df.shape}")

    # vpin/delta_norm/volatility_5 are ALREADY present in df (carried via
    # V1_FEATURE_COLS from v1's own master-join) - numerically identical to
    # this engine's fresh bar-panel computation (same master source, same
    # bar_idx key), so they are NOT re-merged here to avoid a silent
    # duplicate-column collision. Only genuinely-new raw values (sweep_imbal,
    # buy_frac, bid_pull_pressure, ask_pull_minus_bid_pull) plus the z20/
    # roll5sum transforms for ALL 7 features (v1 never computed z20/roll5sum
    # for vpin/delta_norm/sweep_imbal/buy_frac/volatility_5) are merged.
    bar_panel = pd.read_parquet(v2.OUT_DIR / "_bar_feature_panel.parquet")
    already_present = [f for f in RAW_FEATURES if f in df.columns]
    new_raw_features = [f for f in RAW_FEATURES if f not in df.columns]
    v2.log(f"  raw features already present via v1 join (re-used, not re-merged): {already_present}")
    v2.log(f"  raw features newly merged from this engine's bar panel: {new_raw_features}")
    candidate_bar_cols = (
        new_raw_features + [f"{f}_z20" for f in RAW_FEATURES] + [f"{f}_roll5sum" for f in RAW_FEATURES]
    )
    bar_cols = [c for c in candidate_bar_cols if c not in df.columns]
    skipped = [c for c in candidate_bar_cols if c in df.columns]
    if skipped:
        v2.log(f"  skipping bar-panel columns already present via v1 join (re-used as-is): {skipped}")
    bar_small = bar_panel[["bar_idx"] + bar_cols].rename(columns={"bar_idx": "t0_idx"})
    df = df.merge(bar_small, on="t0_idx", how="left")

    ic_panel = pd.read_parquet(v2.OUT_DIR / "rolling_feature_ic_panel.parquet")
    ic_h = ic_panel[ic_panel["horizon"] == PRIMARY_H]
    ic_regime_cols_all = []
    for feat in RAW_FEATURES:
        sub = ic_h[ic_h["feature"] == feat][
            ["bar_idx", "rolling_ic", "rolling_ic_sign", "rolling_ic_abs_strength",
             "rolling_ic_tstat", "rolling_ic_stability", "rolling_ic_window_n"]
        ].rename(columns={
            "rolling_ic": f"{feat}_rolling_ic", "rolling_ic_sign": f"{feat}_rolling_ic_sign",
            "rolling_ic_abs_strength": f"{feat}_rolling_ic_abs_strength",
            "rolling_ic_tstat": f"{feat}_rolling_ic_tstat",
            "rolling_ic_stability": f"{feat}_rolling_ic_stability",
            "rolling_ic_window_n": f"{feat}_rolling_ic_window_n",
        }).rename(columns={"bar_idx": "t0_idx"})
        df = df.merge(sub, on="t0_idx", how="left")
        ic_regime_cols_all += [f"{feat}_rolling_ic", f"{feat}_rolling_ic_sign",
                               f"{feat}_rolling_ic_abs_strength", f"{feat}_rolling_ic_tstat",
                               f"{feat}_rolling_ic_stability", f"{feat}_rolling_ic_window_n"]

    interaction_cols = []
    for feat in RAW_FEATURES:
        ic_col = f"{feat}_rolling_ic"
        df[f"{feat}_value_x_ic"] = df[feat] * df[ic_col]
        df[f"{feat}_z20_x_ic"] = df[f"{feat}_z20"] * df[ic_col]
        df[f"{feat}_roll5sum_x_ic"] = df[f"{feat}_roll5sum"] * df[ic_col]
        interaction_cols += [f"{feat}_value_x_ic", f"{feat}_z20_x_ic", f"{feat}_roll5sum_x_ic"]

    meta_cols = ["event_id", "t0", "t0_idx", "day", "side_primary", "label_primary", "y_meta",
                 "eligible_for_meta_training", "in_sample_contaminated", "reaction_type",
                 "training_gate_status", "nearest_level_type", "session_3b", "realized_points",
                 "holding_bars", "first_touch", "avg_uniqueness", "cluster_id", "cluster_size"]
    out = df[meta_cols + V1_FEATURE_COLS + ic_regime_cols_all + interaction_cols].copy()
    out.to_parquet(v2.OUT_DIR / "meta_label_dataset_v2_ic_regime.parquet", index=False)

    n_eligible = int(out["eligible_for_meta_training"].sum())
    n_v2a_complete = int(out.loc[out["eligible_for_meta_training"], V1_FEATURE_COLS].dropna().shape[0])
    n_v2b_complete = int(
        out.loc[out["eligible_for_meta_training"], V1_FEATURE_COLS + ic_regime_cols_all + interaction_cols]
        .dropna().shape[0]
    )
    v2.log(f"05 complete: {len(out)} deduplicated rows; {n_eligible} eligible_for_meta_training")
    v2.log(f"  v2a (V1_FEATURE_COLS only) complete-feature rows: {n_v2a_complete}")
    v2.log(f"  v2b (V1_FEATURE_COLS + IC-regime + interactions) complete-feature rows: {n_v2b_complete} "
          f"(extra NaN drop = IC rolling-window warm-up, on top of v1's own warm-up)")
    print(f"DEDUP_ROWS_TOTAL: {len(out)}")
    print(f"ELIGIBLE_ROWS: {n_eligible}")
    print(f"V2A_FEATURE_COLS: {len(V1_FEATURE_COLS)}")
    print(f"V2B_FEATURE_COLS: {len(V1_FEATURE_COLS) + len(ic_regime_cols_all) + len(interaction_cols)}")
    print(f"V2A_COMPLETE_ROWS: {n_v2a_complete}")
    print(f"V2B_COMPLETE_ROWS: {n_v2b_complete}")
    return out


if __name__ == "__main__":
    main()
