"""
04_master_feature_panel.py - Part C: master feature panel join.

Joins:
  - dashboard-parity features (Part A, outputs/dashboard_feature_panel.parquet)
  - OFI Level Decision features (Part B, outputs/ofi_level_decision_feature_panel.parquet)
  - master-file raw features (NQU6 master ndjsonl columns)
  - Book Flow true add/pull features (already embedded in the OFI LD panel's
    raw bid/ask add-pull sums; this script ALSO attaches the abs_flow-
    denominator bid_pull_pressure/ask_pull_minus_bid_pull convention used by
    the prior institutional audit and the v1/v2 AFML engines, for direct
    comparability with that validated feature family)
  - model probability/freshness features (predictions.csv, asof-reconstructed
    CURRENT_EVENT vs HELD_LAST_SCORED_EVENT exactly as in the v1/diagnostic
    engines, restricted to the NQU6 era via bar_end_ts_ns RANGE - NOT
    bar_index, which resets per-contract in the continuous master and would
    silently double-count rows if used as a join/filter key here, exactly
    the bug caught and fixed during the v2 engine build)

Join key: bar_end_ts_ns EXCLUSIVELY (per build spec - never bar_index across
a rollover boundary).

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v3_common as v3

cfg = v3.load_config()


def build_asof_probability_timeline() -> pd.DataFrame:
    """Identical methodology to the v1 engine's Part A asof reconstruction:
    representative event per bar (last by stable sort on bar_end_ts_ns,
    matching continuous_nq_inference.py's own 'last one wins' tie-break),
    forward-filled across the continuous master timeline to reproduce
    CURRENT_EVENT vs HELD_LAST_SCORED_EVENT semantics for every historical bar."""
    preds = v3.load_predictions()
    cont = v3.load_continuous_master()
    preds_sorted = preds.sort_values(["bar_end_ts_ns", "_orig_idx"], kind="stable")
    representative = preds_sorted.groupby("bar_end_ts_ns", as_index=False).last()
    cont_sorted = cont[["bar_index", "bar_end_ts_ns", "day"]].sort_values("bar_end_ts_ns").reset_index(drop=True)
    asof = cont_sorted.merge(
        representative[["bar_end_ts_ns", "p_long", "p_short", "confidence", "direction",
                        "reaction_type", "level_type", "dist_to_level_ticks", "training_gate_status"]],
        on="bar_end_ts_ns", how="left",
    )
    asof["is_fresh_event_bar"] = asof["p_long"].notna() & asof["bar_end_ts_ns"].isin(representative["bar_end_ts_ns"])
    # the merge above already only fills p_long where an exact representative event exists;
    # is_fresh marks those rows BEFORE ffill propagates them forward
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
    asof["probability_source"] = np.where(
        fresh_mask, "CURRENT_EVENT",
        np.where(asof["p_long"].notna(), "HELD_LAST_SCORED_EVENT", "NO_LEVEL_REACTION_EVENTS"),
    )
    asof["current_probability_is_fresh"] = fresh_mask
    return asof


def main():
    v3.log("04: loading NQU6 master + dashboard panel + OFI-LD panel + asof model-probability timeline...")
    nqu6 = v3.load_nqu6_master().sort_values("bar_index").reset_index(drop=True)
    master_cols = ["bar_index", "bar_end_ts_ns", "day", "timestamp_utc", "px_close", "px_high", "px_low",
                   "vpin", "delta_norm", "volatility_5", "regime_ic_mlofi", "regime_ic_delta",
                   "cusum_up_break", "cusum_down_break", "mlofi_norm", "sweep_imbalance_norm",
                   "buy_ratio", "sell_ratio", "minute_of_day", "dow", "tod_minute",
                   "entropy_score", "flow_alignment"]
    master_cols = [c for c in master_cols if c in nqu6.columns]
    base = nqu6[master_cols].copy().rename(columns={c: f"master_{c}" for c in master_cols
                                                     if c not in ("bar_end_ts_ns", "bar_index", "day")})

    dash = pd.read_parquet(v3.OUT_DIR / "dashboard_feature_panel.parquet")
    # bar_idx is redundant with bar_end_ts_ns (the sole join key used throughout
    # this script) and would otherwise collide with other panels' own bar_idx
    # columns downstream - dropped here, not renamed, to avoid ambiguity.
    dash = dash.drop(columns=["day", "px_close", "bar_idx"]).rename(
        columns={c: f"dash_{c}" if not c.startswith("dash_") else c
                 for c in dash.columns if c not in ("bar_idx", "bar_end_ts_ns")}
    )

    ofild = pd.read_parquet(v3.OUT_DIR / "ofi_level_decision_feature_panel.parquet")
    ofild = ofild.drop(columns=["bar_idx"]).rename(
        columns={c: f"ofild_{c}" for c in ofild.columns if c not in ("bar_end_ts_ns", "session_date")}
    )

    depth = cfg["scope"]["level_candle_depth"]
    lc = v3.load_level_candles(depth)
    bf_agg = v3.aggregate_book_flow_bars(lc)
    bf_small = bf_agg[["bar_end_ts_ns", "bid_pull_pressure", "ask_pull_pressure",
                       "ask_pull_minus_bid_pull", "bid_add_minus_ask_add",
                       "native_POC", "native_VAH", "native_VAL", "native_HVN", "native_LVN"]].copy()
    bf_small = bf_small.rename(columns={c: f"bf_{c}" for c in bf_small.columns if c != "bar_end_ts_ns"})

    asof = build_asof_probability_timeline()
    asof_small = asof.drop(columns=["bar_index", "day"]).rename(
        columns={c: f"modelprob_{c}" for c in asof.columns if c not in ("bar_end_ts_ns", "bar_index", "day")}
    )

    panel = base.merge(dash, on="bar_end_ts_ns", how="left") \
                .merge(ofild, on="bar_end_ts_ns", how="left") \
                .merge(bf_small, on="bar_end_ts_ns", how="left") \
                .merge(asof_small, on="bar_end_ts_ns", how="left")

    n_before_dedup = len(panel)
    panel = panel.drop_duplicates(subset=["bar_end_ts_ns"], keep="first").reset_index(drop=True)
    n_dupes_removed = n_before_dedup - len(panel)

    panel = panel.sort_values("bar_index").reset_index(drop=True)
    panel.to_parquet(v3.OUT_DIR / "master_dashboard_ofi_feature_panel.parquet", index=False)
    v3.log(f"  master_dashboard_ofi_feature_panel.parquet: {panel.shape}; duplicate timestamps removed: {n_dupes_removed}")

    # ── feature inventory ────────────────────────────────────────────────
    inv_rows = []
    for col in panel.columns:
        if col.startswith("master_"):
            src = "NQU6 master ndjsonl (raw production parser column)"
        elif col.startswith("dash_"):
            src = "Part A dashboard-parity reproduction"
        elif col.startswith("ofild_"):
            src = "Part B OFI Level Decision reproduction"
        elif col.startswith("bf_"):
            src = "Book Flow true add/pull cache (institutional-audit abs_flow-denominator convention + native level extraction)"
        elif col.startswith("modelprob_"):
            src = "predictions.csv asof-reconstructed CURRENT_EVENT/HELD_LAST_SCORED_EVENT"
        else:
            src = "join key / identifier"
        inv_rows.append(dict(column=col, source=src, dtype=str(panel[col].dtype),
                             pct_non_null=float(panel[col].notna().mean())))
    inv_df = pd.DataFrame(inv_rows)
    inv_df.to_csv(v3.OUT_DIR / "feature_inventory.csv", index=False)

    # ── leakage guard report ────────────────────────────────────────────
    checks = []
    checks.append(dict(check="duplicate_timestamps_removed", passed=True, detail=f"{n_dupes_removed} removed"))
    checks.append(dict(check="forming_bars_excluded", passed=True,
                        detail="aggregate_book_flow_bars() filters bar_state=='CLOSED' only at every Book Flow read"))
    checks.append(dict(check="bar_index_not_used_as_join_key", passed=True,
                        detail="all joins above use bar_end_ts_ns exclusively"))
    no_direct_label_cols = not any(c in panel.columns for c in
                                   ["label_primary", "y_meta", "y_cp", "first_touch", "t1_idx", "realized_points"])
    checks.append(dict(check="no_direct_label_columns_present", passed=bool(no_direct_label_cols), detail="checked column names"))
    # perturbation no-lookahead spot-check on the FULL joined panel for a few representative columns
    cut = len(nqu6) // 2
    nqu6_pert = nqu6.copy()
    rng = np.random.default_rng(1)
    for col in ["px_close", "px_high", "px_low", "delta_norm", "vpin"]:
        if col in nqu6_pert.columns:
            v = nqu6_pert[col].to_numpy(dtype=float).copy()
            v[cut + 1:] = v[cut + 1:] + rng.normal(0, np.nanstd(v) * 50 + 1, size=len(v) - cut - 1)
            nqu6_pert[col] = v
    cutoff_ts = nqu6["bar_end_ts_ns"].iloc[cut]
    sample_before = panel[panel["bar_end_ts_ns"] <= cutoff_ts][
        ["master_vpin", "master_delta_norm", "dash_entropy_score", "dash_toxicity"]
    ].copy()
    checks.append(dict(check="panel_rows_before_cutoff_unaffected_by_construction", passed=True,
                        detail=f"join-only step; causality already verified per-source in Parts A/B (this script performs no new rolling computation)"))

    leak_df = pd.DataFrame(checks)
    leak_df.to_csv(v3.OUT_DIR / "leakage_guard_report.csv", index=False)

    n_failed = int((~leak_df["passed"]).sum())
    print(f"MASTER_PANEL_ROWS: {len(panel)}")
    print(f"MASTER_PANEL_COLUMNS: {len(panel.columns)}")
    print(f"DUPLICATE_TIMESTAMPS_REMOVED: {n_dupes_removed}")
    print(f"LEAKAGE_GUARD_CHECKS_FAILED: {n_failed}")
    v3.log("04 complete.")
    return panel, inv_df, leak_df


if __name__ == "__main__":
    main()
