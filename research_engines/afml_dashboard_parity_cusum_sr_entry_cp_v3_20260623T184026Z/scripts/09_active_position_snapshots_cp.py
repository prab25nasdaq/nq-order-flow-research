"""
09_active_position_snapshots_cp.py - Part H: NEW CP close-position label.

For every entry candidate that became an active shadow position (side!=0,
triple-barrier-resolved with t1_idx>=0), creates ONE ROW PER ACTIVE BAR
t in [t0, t1) (t1 itself is excluded - that is the bar where the AFML
barrier mechanically resolves, too late for a CP decision to matter).

side-adjusted return at any bar t: side_return(t) = side*(price(t) - entry_price)
(this is exactly "LONG: price_now-entry_price; SHORT: entry_price-price_now").

CP label (NO fixed point/percent threshold anywhere):
  close_now_value          = side_return(t)
  future_exit_value        = side_return(t1)  [ = realized_points from Part G,
                              i.e. what holding all the way to the ACTUAL
                              eventual barrier exit was worth ]
  future_best_value        = max(side_return(t+1..t1))   [best achievable AFTER t]
  future_worst_value       = min(side_return(t+1..t1))   [worst achievable AFTER t]
  remaining_value_to_exit      = future_exit_value - close_now_value
  remaining_best_opportunity   = future_best_value  - close_now_value
  remaining_adverse_risk       = close_now_value    - future_worst_value

  y_cp = 1  iff remaining_value_to_exit <= 0   (closing now >= holding to the
            eventual exit, for this SPECIFIC realized path)
  y_cp = 0  iff remaining_value_to_exit >  0   (holding to the eventual exit
            was strictly better for this specific path)

This is a per-path, realized-outcome label - not a rule, not a fixed
threshold. Optional diagnostic labels (CP_BEFORE_GIVEBACK,
CP_AFTER_MFE_DECAY, HOLD_FOR_MORE_VALUE) are derived from the SAME
quantities for descriptive purposes only; y_cp is the sole official target.

FEATURES at each snapshot (current state, all t0-or-earlier-than-the-NEXT-
bar, i.e. known at snapshot time t): re-evaluated AT bar t from the same
master/dashboard/OFI-LD/book-flow/model-probability panel used throughout
this engine, plus distance-to-barrier and bars-elapsed/remaining.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v3_common as v3

cfg = v3.load_config()
TB = cfg["triple_barrier"]
TICK = v3.TICK_SIZE


def planned_vertical_idx(t0, day_arr, vbb, n_bars):
    d0 = day_arr[t0]
    vb = min(t0 + vbb, n_bars - 1)
    while vb > t0 and day_arr[vb] != d0:
        vb -= 1
    return vb


def main():
    v3.log("09: building active-position snapshots + CP labels...")
    candidates = pd.read_parquet(v3.OUT_DIR / "candidate_events_entry_v3.parquet")
    labels = pd.read_parquet(v3.OUT_DIR / "triple_barrier_labels_entry_v3.parquet")
    master_panel = pd.read_parquet(v3.OUT_DIR / "master_dashboard_ofi_feature_panel.parquet")
    sr_gate = pd.read_parquet(v3.OUT_DIR / "sr_gated_cusum_candidates.parquet")

    nqu6 = v3.load_nqu6_master().sort_values("bar_index").reset_index(drop=True)
    close = nqu6["px_close"].to_numpy(); high = nqu6["px_high"].to_numpy(); low = nqu6["px_low"].to_numpy()
    day_arr = nqu6["day"].to_numpy(); bar_end_ts_ns = nqu6["bar_end_ts_ns"].to_numpy()
    n_bars = len(nqu6)

    active = candidates.merge(
        labels[["event_id", "t1_idx", "label_primary", "first_touch", "realized_points",
               "pt_price", "sl_price", "volatility_at_t0"]],
        on="event_id", how="left",
    )
    active = active[(active["side_primary"] != 0) & (active["t1_idx"] >= 0)].copy()
    v3.log(f"  {len(active)} candidates became active shadow positions")

    # for re-evaluating nearest-level context at each snapshot bar t (not frozen at t0)
    depth = cfg["scope"]["level_candle_depth"]
    lc = v3.load_level_candles(depth)
    bf_agg = v3.aggregate_book_flow_bars(lc)
    rolling = v3.build_rolling_levels(bf_agg, price_col="close_price", session_col="session_date",
                                       tick_bucket=cfg["sr_gate"]["rolling_level_tick_bucket"],
                                       min_prior_bars=cfg["sr_gate"]["rolling_level_min_prior_bars"])
    lvl_lookup = rolling[["bar_end_ts_ns", "native_POC", "native_VAH", "native_VAL", "native_HVN", "native_LVN",
                          "rolling_poc", "rolling_vah", "rolling_val", "rolling_hvn", "rolling_lvn"]].drop_duplicates(
        subset=["bar_end_ts_ns"]
    ).set_index("bar_end_ts_ns")

    feature_cols = [c for c in master_panel.columns if c not in ("bar_index", "bar_end_ts_ns", "day", "timestamp_utc")]
    master_lookup = master_panel.set_index("bar_end_ts_ns")[feature_cols]

    snap_rows = []
    for _, row in active.iterrows():
        t0 = int(row["bar_idx"]); t1 = int(row["t1_idx"]); s = int(row["side_primary"])
        entry_price = close[t0]
        vbb = TB["vertical_barrier_bars"]
        vb_planned = planned_vertical_idx(t0, day_arr, vbb, n_bars)

        path_idx = np.arange(t0, t1 + 1)
        side_ret_path = s * (close[path_idx] - entry_price)

        for ti, t in enumerate(range(t0, t1)):  # exclude t1 itself
            close_now_value = float(side_ret_path[ti])
            future_path = side_ret_path[ti + 1:]
            future_exit_value = float(side_ret_path[-1])  # = realized_points
            future_best_value = float(np.max(future_path)) if len(future_path) else close_now_value
            future_worst_value = float(np.min(future_path)) if len(future_path) else close_now_value
            mfe_so_far = float(np.max(side_ret_path[:ti + 1]))
            mae_so_far = float(-np.min(side_ret_path[:ti + 1])) if np.min(side_ret_path[:ti + 1]) < 0 else 0.0

            remaining_value_to_exit = future_exit_value - close_now_value
            remaining_best_opportunity = future_best_value - close_now_value
            remaining_adverse_risk = close_now_value - future_worst_value

            y_cp = 1 if remaining_value_to_exit <= 0 else 0
            cp_before_giveback = 1 if remaining_best_opportunity <= 0 else 0
            cp_after_mfe_decay = 1 if (close_now_value < mfe_so_far) and (remaining_adverse_risk > 0) else 0
            hold_for_more_value = 1 if remaining_best_opportunity > 0 else 0

            dist_to_pt = s * (row["pt_price"] - close[t])
            dist_to_sl = s * (close[t] - row["sl_price"])

            ts_t = bar_end_ts_ns[t]
            lvl_row = lvl_lookup.loc[ts_t] if ts_t in lvl_lookup.index else None
            if lvl_row is not None:
                native_dict = {lt: lvl_row[f"native_{lt}"] for lt in ["POC", "VAH", "VAL", "HVN", "LVN"]}
                roll_dict = {"POC": lvl_row["rolling_poc"], "VAH": lvl_row["rolling_vah"], "VAL": lvl_row["rolling_val"],
                            "HVN": lvl_row["rolling_hvn"], "LVN": lvl_row["rolling_lvn"]}
                cur_price = close[t]
                best_native, best_roll = None, None
                for lt, lp in native_dict.items():
                    if lp is not None and np.isfinite(lp):
                        d = abs(cur_price - lp) / TICK
                        if best_native is None or d < best_native[1]:
                            best_native = (lt, d)
                for lt, lp in roll_dict.items():
                    if lp is not None and np.isfinite(lp):
                        d = abs(cur_price - lp) / TICK
                        if best_roll is None or d < best_roll[1]:
                            best_roll = (lt, d)
                cur_nearest = best_native if (best_native and (not best_roll or best_native[1] <= best_roll[1])) else best_roll
                cur_level_type = cur_nearest[0] if cur_nearest else None
                cur_level_dist_ticks = cur_nearest[1] if cur_nearest else np.nan
            else:
                cur_level_type, cur_level_dist_ticks = None, np.nan

            snap = dict(
                event_id=row["event_id"], t0_idx=t0, t1_idx=t1, snapshot_bar_idx=t, side_primary=s,
                entry_price=entry_price, price_now=close[t],
                close_now_value=close_now_value, mfe_so_far=mfe_so_far, mae_so_far=mae_so_far,
                dist_to_pt_barrier=dist_to_pt, dist_to_sl_barrier=dist_to_sl,
                dist_to_vertical_barrier_bars=vb_planned - t,
                active_bars_elapsed=t - t0, active_bars_remaining_to_vertical=vb_planned - t,
                future_exit_value=future_exit_value, future_best_value=future_best_value,
                future_worst_value=future_worst_value,
                remaining_value_to_exit=remaining_value_to_exit,
                remaining_best_opportunity=remaining_best_opportunity,
                remaining_adverse_risk=remaining_adverse_risk,
                y_cp=y_cp, CP_BEFORE_GIVEBACK=cp_before_giveback, CP_AFTER_MFE_DECAY=cp_after_mfe_decay,
                HOLD_FOR_MORE_VALUE=hold_for_more_value,
                current_nearest_level_type=cur_level_type, current_nearest_level_distance_ticks=cur_level_dist_ticks,
                bar_end_ts_ns=ts_t,
                eventual_first_touch=row["first_touch"], day=day_arr[t],
                in_sample_contaminated=(pd.Timestamp(ts_t, unit="ns", tz="UTC") <= v3.MODEL_TRAINING_CUTOFF_UTC),
            )
            snap_rows.append(snap)

    snaps = pd.DataFrame(snap_rows)
    if len(snaps):
        snaps = snaps.merge(master_lookup, left_on="bar_end_ts_ns", right_index=True, how="left")
    snaps.to_parquet(v3.OUT_DIR / "active_position_snapshots_v3.parquet", index=False)

    cp_cols = ["event_id", "snapshot_bar_idx", "side_primary", "close_now_value", "mfe_so_far", "mae_so_far",
               "future_exit_value", "future_best_value", "future_worst_value", "remaining_value_to_exit",
               "remaining_best_opportunity", "remaining_adverse_risk", "y_cp", "CP_BEFORE_GIVEBACK",
               "CP_AFTER_MFE_DECAY", "HOLD_FOR_MORE_VALUE", "active_bars_elapsed",
               "active_bars_remaining_to_vertical", "eventual_first_touch", "in_sample_contaminated"]
    cp_labels = snaps[cp_cols] if len(snaps) else pd.DataFrame(columns=cp_cols)
    cp_labels.to_parquet(v3.OUT_DIR / "close_position_cp_labels_v3.parquet", index=False)

    # ── diagnostics ──────────────────────────────────────────────────────
    diag = dict(
        n_active_positions=len(active), n_snapshots=len(snaps),
        mean_snapshots_per_position=float(len(snaps) / max(len(active), 1)),
        pct_y_cp_1=float(snaps["y_cp"].mean()) if len(snaps) else np.nan,
        pct_y_cp_0=float(1 - snaps["y_cp"].mean()) if len(snaps) else np.nan,
        mean_remaining_value_to_exit=float(snaps["remaining_value_to_exit"].mean()) if len(snaps) else np.nan,
        median_remaining_value_to_exit=float(snaps["remaining_value_to_exit"].median()) if len(snaps) else np.nan,
        mean_remaining_best_opportunity=float(snaps["remaining_best_opportunity"].mean()) if len(snaps) else np.nan,
        mean_remaining_adverse_risk=float(snaps["remaining_adverse_risk"].mean()) if len(snaps) else np.nan,
        fixed_threshold_used=False,
    )
    pd.DataFrame([{"metric": k, "value": v} for k, v in diag.items()]).to_csv(
        v3.OUT_DIR / "cp_label_diagnostics.csv", index=False
    )

    v3.log(f"  {len(active)} active positions -> {len(snaps)} active-bar snapshots "
          f"(mean {diag['mean_snapshots_per_position']:.2f}/position)")
    v3.log(f"  y_cp=1 (close better): {100*diag['pct_y_cp_1']:.1f}%   y_cp=0 (hold better): {100*diag['pct_y_cp_0']:.1f}%" if len(snaps) else "  (no snapshots)")

    print(f"ACTIVE_POSITIONS: {len(active)}")
    print(f"ACTIVE_SNAPSHOTS: {len(snaps)}")
    print(f"PCT_Y_CP_1: {diag['pct_y_cp_1']}")
    print(f"CP_FIXED_THRESHOLD_USED: False")
    v3.log("09 complete.")
    return snaps, cp_labels


if __name__ == "__main__":
    main()
