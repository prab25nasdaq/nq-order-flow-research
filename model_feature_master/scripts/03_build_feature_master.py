"""
03_build_feature_master.py - Part C: research-safe feature master builder.

Reads the EXISTING production master (read-only) and Book Flow cache
(read-only), computes the UNION of every feature needed by:
  (1) the current dashboard/level-reaction model (77 features)
  (2) the v4 HistGB Entry ACT/PASS model (132 features)
and writes a SEPARATE derived feature master. NEVER writes to the existing
master file. NEVER modifies production model artifacts.

Rules enforced:
  - bar_end_ts_ns is the PRIMARY KEY (never bar_index)
  - duplicate timestamps removed (keep-first)
  - CLOSED bars only for training-safe rows; this build's source
    (master_NQU6_shadow.ndjsonl) only contains closed bars already (the
    daemon's forming-bar handling is in Part D, since forming-bar context
    comes from a different, live-only source not present in this batch run)
  - centered swing-level S/R and raw absolute price columns (master_px_close/
    high/low) are EXCLUDED from the training-safe column set (see Part A/B's
    discovered v4 inconsistency) - kept ONLY as audit-only columns prefixed
    with a leading underscore so they are trivially filterable, never
    silently mixed in with the training-safe feature block
  - no future data: every formula reproduced here was already perturbation-
    tested for no-lookahead in the v3 build this is copied from

READ-ONLY against all production paths. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fm_common as fm


def build_asof_modelprob_timeline(continuous: pd.DataFrame) -> pd.DataFrame:
    preds = fm.load_predictions()
    cont_small = continuous[["bar_index", "bar_end_ts_ns", "day"]].sort_values("bar_end_ts_ns").reset_index(drop=True)
    if preds is None:
        fm.log("  WARNING: predictions.csv not available - modelprob_* columns will be all-NaN")
        asof = cont_small.copy()
        for col in ["p_long", "p_short", "confidence", "direction", "reaction_type", "level_type",
                   "dist_to_level_ticks", "training_gate_status"]:
            asof[col] = np.nan
        asof["is_fresh_event_bar"] = False
        asof["bars_since_last_event"] = -1
        asof["probability_source"] = "PREDICTIONS_UNAVAILABLE"
        asof["current_probability_is_fresh"] = False
        return asof
    preds_sorted = preds.sort_values(["bar_end_ts_ns", "_orig_idx"], kind="stable")
    representative = preds_sorted.groupby("bar_end_ts_ns", as_index=False).last()
    asof = cont_small.merge(
        representative[["bar_end_ts_ns", "p_long", "p_short", "confidence", "direction",
                        "reaction_type", "level_type", "dist_to_level_ticks", "training_gate_status"]],
        on="bar_end_ts_ns", how="left")
    # is_fresh_event_bar computed BEFORE ffill (matches v3's exact pre-ffill
    # timing) - same boolean as current_probability_is_fresh in practice
    # (kept as two columns since v4 trained on both names).
    asof["is_fresh_event_bar"] = asof["p_long"].notna() & asof["bar_end_ts_ns"].isin(representative["bar_end_ts_ns"])
    fresh_mask = asof["p_long"].notna()
    for col in ["p_long", "p_short", "confidence", "direction", "reaction_type", "level_type",
               "dist_to_level_ticks", "training_gate_status"]:
        asof[col] = asof[col].ffill()
    bars_since = np.zeros(len(asof), dtype=int)
    counter = 0
    for i in range(len(asof)):
        if fresh_mask.iloc[i]:
            counter = 0
        else:
            counter += 1
        bars_since[i] = counter
    asof["bars_since_last_event"] = bars_since
    asof["probability_source"] = np.where(fresh_mask, "CURRENT_EVENT",
                                          np.where(asof["p_long"].notna(), "HELD_LAST_SCORED_EVENT", "NO_LEVEL_REACTION_EVENTS"))
    asof["current_probability_is_fresh"] = fresh_mask
    return asof


def main():
    fm.log("03: building model_feature_master from CURRENT production sources (read-only)...")
    nqu6 = fm.load_nqu6_master().sort_values("bar_index").reset_index(drop=True)
    fm.log(f"  NQU6 master: {len(nqu6)} rows (read-only, source of truth untouched)")

    n_dupes = int(nqu6.duplicated(subset=["bar_end_ts_ns"]).sum())

    master_cols = ["bar_index", "bar_end_ts_ns", "day", "timestamp_utc", "px_close", "px_high", "px_low",
                   "vpin", "delta_norm", "volatility_5", "regime_ic_mlofi", "regime_ic_delta",
                   "cusum_up_break", "cusum_down_break", "mlofi_norm", "sweep_imbalance_norm",
                   "buy_ratio", "sell_ratio", "minute_of_day", "dow", "tod_minute",
                   "entropy_score", "flow_alignment", "delta_norm_lag_1", "delta_norm_lag_2", "delta_norm_lag_3",
                   "delta_rolling_5", "mlofi_decay_sum", "mlofi_rolling_5", "mlofi_accel", "decay_norm",
                   "mlofi_norm_lag_1", "mlofi_norm_lag_2", "mlofi_norm_lag_3", "decay_norm_lag_1",
                   "decay_norm_lag_2", "decay_norm_lag_3", "sweep_norm", "sweep_buy_ratio", "sweep_sell_ratio",
                   "vpin_lag_1", "vpin_lag_2", "vpin_lag_3", "mid_resid_z", "mid_ret1", "bar_duration_s",
                   "delta_norm_resid_z20", "volatility_5_resid_z20", "sweep_imbalance_norm_resid_z20", "vpin_resid_z20"]
    master_cols = [c for c in master_cols if c in nqu6.columns]
    base = nqu6[master_cols].copy().rename(
        columns={c: f"master_{c}" for c in master_cols if c not in ("bar_end_ts_ns", "bar_index", "day")})

    fm.log("  computing dashboard-parity formulas (build_dashboard_panel)...")
    dash = fm.build_dashboard_panel(nqu6)
    dash = dash.drop(columns=["day", "px_close", "bar_idx"]).rename(
        columns={c: f"dash_{c}" if not c.startswith("dash_") else c for c in dash.columns
                 if c not in ("bar_idx", "bar_end_ts_ns")})

    fm.log("  computing OFI Level Decision formulas (build_ofild_panel)...")
    depth = 10
    lc = fm.load_level_candles(depth)
    agg = fm.aggregate_book_flow_bars(lc)
    ofild = fm.build_ofild_panel(agg)
    ofild = ofild.drop(columns=["bar_idx"]).rename(
        columns={c: f"ofild_{c}" for c in ofild.columns if c not in ("bar_end_ts_ns", "session_date")})

    bf_small = agg[["bar_end_ts_ns", "bid_pull_pressure", "ask_pull_pressure", "ask_pull_minus_bid_pull",
                    "bid_add_minus_ask_add", "native_POC", "native_VAH", "native_VAL", "native_HVN", "native_LVN"]].copy()
    bf_small = bf_small.rename(columns={c: f"bf_{c}" for c in bf_small.columns if c != "bar_end_ts_ns"})

    fm.log("  computing model-probability freshness (asof CURRENT_EVENT/HELD_LAST)...")
    continuous = fm.load_continuous_master()
    asof = build_asof_modelprob_timeline(continuous)
    asof_small = asof.drop(columns=["bar_index", "day"]).rename(
        columns={c: f"modelprob_{c}" for c in asof.columns if c not in ("bar_end_ts_ns", "bar_index", "day")})

    fm.log("  computing existing-model structural features (per-day volume profile, reactions, OHLC-vol path)...")
    struct_frames = []
    for d, g in nqu6.groupby("day"):
        struct_frames.append(fm.build_structural_features(g))
    struct_df = pd.concat(struct_frames, ignore_index=True) if struct_frames else pd.DataFrame()

    # gate_* one-hots: map the full reaction_type string (kept internally by
    # build_structural_features) through the active release's OWN reaction_
    # type_metadata.json - same lookup v4 itself used (Part D), not re-derived.
    rxn_meta = json.loads((fm.resolved_active_release() / "reaction_type_metadata.json").read_text())
    GATE_STATUSES = ["PRIMARY_USE", "SECONDARY_WATCH", "BLOCKED_NEGATIVE", "SMALL_N", "EXPLORATORY_SMALL_N"]
    gate_status = struct_df["reaction_type_full_string"].map(
        lambda r: rxn_meta.get(r, {}).get("training_gate_status") if r else None)
    for gs in GATE_STATUSES:
        struct_df[f"gate_{gs}"] = (gate_status == gs).astype(int)
    struct_df = struct_df.drop(columns=["reaction_type_full_string"])

    panel = base.merge(dash, on="bar_end_ts_ns", how="left") \
                .merge(ofild, on="bar_end_ts_ns", how="left") \
                .merge(bf_small, on="bar_end_ts_ns", how="left") \
                .merge(asof_small, on="bar_end_ts_ns", how="left") \
                .merge(struct_df, on="bar_end_ts_ns", how="left")

    n_before = len(panel)
    panel = panel.drop_duplicates(subset=["bar_end_ts_ns"], keep="first").reset_index(drop=True)
    n_dupes_removed = n_before - len(panel)
    panel = panel.sort_values("bar_index").reset_index(drop=True)

    # rename raw-price columns with a leading underscore - audit-only, never
    # part of the training-safe feature block (Part A/B's discovered finding)
    rename_audit_only = {c: f"_AUDIT_ONLY_{c}" for c in fm.EXCLUDED_HIGH_LEAKAGE if c in panel.columns}
    panel = panel.rename(columns=rename_audit_only)

    # bare-name ALIASES for the current dashboard model's own 35 master-
    # passthrough feature names (feature_names.json uses bare names like
    # "vpin"/"delta_norm"; this panel's base columns carry a "master_" prefix
    # for v4-style namespacing) - duplicate views of the SAME column, added
    # so this one feature master satisfies BOTH models' exact expected names
    # without computing anything twice.
    DASHBOARD_BARE_ALIASES = {
        "delta_norm": "master_delta_norm", "delta_norm_lag_1": "master_delta_norm_lag_1",
        "delta_norm_lag_2": "master_delta_norm_lag_2", "delta_norm_lag_3": "master_delta_norm_lag_3",
        "delta_rolling_5": "master_delta_rolling_5", "mlofi_decay_sum": "master_mlofi_decay_sum",
        "mlofi_norm": "master_mlofi_norm", "mlofi_rolling_5": "master_mlofi_rolling_5",
        "mlofi_accel": "master_mlofi_accel", "decay_norm": "master_decay_norm",
        "mlofi_norm_lag_1": "master_mlofi_norm_lag_1", "mlofi_norm_lag_2": "master_mlofi_norm_lag_2",
        "mlofi_norm_lag_3": "master_mlofi_norm_lag_3", "decay_norm_lag_1": "master_decay_norm_lag_1",
        "decay_norm_lag_2": "master_decay_norm_lag_2", "decay_norm_lag_3": "master_decay_norm_lag_3",
        "sweep_imbalance_norm": "master_sweep_imbalance_norm", "sweep_norm": "master_sweep_norm",
        "sweep_buy_ratio": "master_sweep_buy_ratio", "sweep_sell_ratio": "master_sweep_sell_ratio",
        "buy_ratio": "master_buy_ratio", "sell_ratio": "master_sell_ratio", "vpin": "master_vpin",
        "vpin_lag_1": "master_vpin_lag_1", "vpin_lag_2": "master_vpin_lag_2", "vpin_lag_3": "master_vpin_lag_3",
        "mid_resid_z": "master_mid_resid_z", "mid_ret1": "master_mid_ret1", "volatility_5": "master_volatility_5",
        "bar_duration_s": "master_bar_duration_s", "delta_norm_resid_z20": "master_delta_norm_resid_z20",
        "volatility_5_resid_z20": "master_volatility_5_resid_z20",
        "sweep_imbalance_norm_resid_z20": "master_sweep_imbalance_norm_resid_z20",
        "vpin_resid_z20": "master_vpin_resid_z20", "entropy_score": "master_entropy_score",
        "flow_alignment": "master_flow_alignment",
    }
    for bare, prefixed in DASHBOARD_BARE_ALIASES.items():
        if prefixed in panel.columns and bare not in panel.columns:
            panel[bare] = panel[prefixed]

    panel["is_closed_bar"] = True  # batch source is master_NQU6_shadow.ndjsonl - closed bars only
    panel["live_context_only"] = False  # no forming-bar rows in this batch build
    panel = panel.copy()  # de-fragment after many incremental column assignments above

    panel.to_parquet(fm.DATA_DIR / "model_feature_master_shadow.parquet", index=False)
    panel.tail(500).to_csv(fm.DATA_DIR / "model_feature_master_latest.csv", index=False)

    schema = {
        "primary_key": "bar_end_ts_ns",
        "n_rows": int(len(panel)),
        "n_columns": int(len(panel.columns)),
        "generated_at_utc": pd.Timestamp.now('UTC').isoformat(),
        "source_master": str(fm.MASTER_NQU6),
        "source_master_never_modified": True,
        "columns": [{"name": c, "dtype": str(panel[c].dtype),
                    "audit_only_excluded_from_training": c.startswith("_AUDIT_ONLY_")}
                   for c in panel.columns],
        "excluded_high_leakage_features": sorted(fm.EXCLUDED_HIGH_LEAKAGE),
    }
    with open(fm.DATA_DIR / "model_feature_master_schema.json", "w") as f:
        json.dump(schema, f, indent=2)

    status = {
        "status_written_at_utc": pd.Timestamp.now('UTC').isoformat(),
        "build_mode": "FULL_HISTORICAL_BACKFILL",
        "n_rows": int(len(panel)),
        "n_columns": int(len(panel.columns)),
        "min_bar_end_ts_ns": int(panel["bar_end_ts_ns"].min()),
        "max_bar_end_ts_ns": int(panel["bar_end_ts_ns"].max()),
        "duplicate_timestamps_in_source_master": n_dupes,
        "duplicate_timestamps_removed_in_panel_join": n_dupes_removed,
        "existing_master_file_modified": False,
        "existing_master_daemon_modified": False,
        "production_model_artifacts_modified": False,
        "daemon_running": False,
        "last_successful_bar_end_ts_ns": int(panel["bar_end_ts_ns"].max()),
    }
    with open(fm.DATA_DIR / "feature_master_status.json", "w") as f:
        json.dump(status, f, indent=2)

    fm.log(f"  model_feature_master_shadow.parquet: {panel.shape}")
    fm.log(f"  duplicate timestamps in source: {n_dupes}, removed in panel: {n_dupes_removed}")
    print(f"FEATURE_MASTER_ROWS: {len(panel)}")
    print(f"FEATURE_MASTER_COLUMNS: {len(panel.columns)}")
    fm.log("03 complete.")
    return panel, schema, status


if __name__ == "__main__":
    main()
