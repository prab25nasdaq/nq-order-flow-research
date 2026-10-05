"""
06_cp_label_engineering.py - Part F: Close-Position (CP) label engineering.

Builds one active-position snapshot row per active bar (t0..t1-1, t1
EXCLUDED - it is the exit bar itself) for every entry-meta-label candidate
(Part E's entry_meta_label_dataset_v4.parquet - ALL candidates with a valid
label_h40, regardless of whether the entry model would ultimately say ACT
or PASS, since the CP model needs realized-path examples from the full
population to learn the close-timing decision).

The existing model's OWN policy has NO triple-barrier PT/SL - the "eventual
policy exit/horizon" for v4 is the FIXED +40-bar horizon, day-bounded
(label_end_bar_idx = bar_idx_in_day+40, truncated at the day's last bar -
identical day-boundary truncation to pipeline_continuous.py's own forward-
return computation, which silently produces NaN past the day's array end).

CP label (NO fixed point/percent threshold anywhere):
  close_now_value         = side*(close[t]  - entry_price)
  future_exit_value       = side*(close[t1] - entry_price)   [[t1 = existing-policy exit bar]]
  future_best_value[t]    = max over s in (t, t1] of side-adjusted favorable extreme (high/low)
  future_worst_value[t]   = min over s in (t, t1] of side-adjusted adverse extreme (high/low)
  remaining_value_to_exit    = future_exit_value - close_now_value
  remaining_best_opportunity = future_best_value  - close_now_value
  remaining_adverse_risk     = close_now_value    - future_worst_value
  y_cp = 1{remaining_value_to_exit <= 0}   (closing now >= holding to the existing-policy exit)

"current S/R context" at each snapshot bar is recomputed AT bar t (not
frozen at entry) using the SAME per-day volume-profile levels already
verified exact in Part B (v4_common.volume_profile_levels) - this is a
GENERIC per-bar nearest-level lookup (every bar, not gated by a reaction
rule firing), reproduced from pipeline_continuous.py's own build_level_
stream() per-bar distance computation.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v4_common as v4

cfg = v4.load_config()
HORIZON = cfg["label_policy"]["label_horizon_bars"]


def nearest_level_distance(close_arr: np.ndarray, lv: dict) -> tuple:
    """Vectorized generic nearest-level lookup (every bar) - type/price/distance_ticks."""
    n = len(close_arr)
    cand_types = ["POC", "VAH", "VAL"]
    cand_price = np.array([lv["poc_px"], lv["vah_px"], lv["val_px"]])
    dists = np.abs(close_arr[:, None] - cand_price[None, :])  # (n, 3)
    best_idx = np.argmin(dists, axis=1)
    best_dist = dists[np.arange(n), best_idx]
    best_type = np.array(cand_types)[best_idx]
    best_price = cand_price[best_idx]

    if lv["hvn_px"]:
        hvn_arr = np.array(lv["hvn_px"])
        hvn_dists = np.abs(close_arr[:, None] - hvn_arr[None, :])
        hvn_min_idx = np.argmin(hvn_dists, axis=1)
        hvn_min_dist = hvn_dists[np.arange(n), hvn_min_idx]
        hvn_min_price = hvn_arr[hvn_min_idx]
        better = hvn_min_dist < best_dist
        best_dist = np.where(better, hvn_min_dist, best_dist)
        best_price = np.where(better, hvn_min_price, best_price)
        best_type = np.where(better, "HVN", best_type)

    if lv["lvn_px"]:
        lvn_arr = np.array(lv["lvn_px"])
        lvn_dists = np.abs(close_arr[:, None] - lvn_arr[None, :])
        lvn_min_idx = np.argmin(lvn_dists, axis=1)
        lvn_min_dist = lvn_dists[np.arange(n), lvn_min_idx]
        lvn_min_price = lvn_arr[lvn_min_idx]
        better = lvn_min_dist < best_dist
        best_dist = np.where(better, lvn_min_dist, best_dist)
        best_price = np.where(better, lvn_min_price, best_price)
        best_type = np.where(better, "LVN", best_type)

    return best_type, best_price, best_dist / v4.TICK_SIZE


def main():
    v4.log("06: building CP active-position snapshots + close-position labels (no fixed thresholds)...")
    cand = pd.read_parquet(v4.OUT_DIR / "entry_meta_label_dataset_v4.parquet")
    v4.log(f"  candidates eligible to become active positions: {len(cand)}")

    nqu6 = v4.load_nqu6_master().reset_index(drop=True)
    nqu6["day"] = nqu6["day"].astype(int)
    # NQU6 master has no continuous_high/continuous_low aliases (single-contract file) -
    # for NQU6 rows cumulative_roll_adjustment_points==0 so continuous_high==px_high exactly;
    # volume_profile_levels() needs the continuous_* names, so alias them here (NQU6-only,
    # adjustment-free - this is NOT a re-derivation of the formula, just a column rename).
    nqu6["continuous_high"] = nqu6["px_high"]
    nqu6["continuous_low"] = nqu6["px_low"]
    day_groups = {d: g.reset_index(drop=True) for d, g in nqu6.groupby("day")}
    per_day_levels = {}
    for d, g in day_groups.items():
        try:
            per_day_levels[d] = v4.volume_profile_levels(g)
        except RuntimeError:
            per_day_levels[d] = None

    panel = pd.read_parquet(v4.OUT_DIR / "v4_feature_panel.parquet")
    panel_feat_cols = [c for c in panel.columns if c not in ("bar_index", "bar_end_ts_ns", "day")]
    panel_indexed = panel.set_index("bar_end_ts_ns")

    snap_rows = []
    n_zero_active_bars = 0
    for _, c in cand.iterrows():
        d = int(c["day"])
        g = day_groups[d]
        t0 = int(c["bar_idx_in_day"])
        t1 = min(t0 + HORIZON, len(g) - 1)
        if t1 <= t0:
            n_zero_active_bars += 1
            continue
        side = int(c["side_primary"])
        high = pd.to_numeric(g["high"] if "high" in g.columns else g["px_high"], errors="coerce").to_numpy()
        low = pd.to_numeric(g["low"] if "low" in g.columns else g["px_low"], errors="coerce").to_numpy()
        close = pd.to_numeric(g["close"] if "close" in g.columns else g["px_close"], errors="coerce").to_numpy()
        entry_price = float(close[t0])

        s_idx = np.arange(t0, t1 + 1)
        high_s, low_s, close_s = high[s_idx], low[s_idx], close[s_idx]
        if side == 1:
            value_high = high_s - entry_price
            value_low = low_s - entry_price
        else:
            value_high = entry_price - low_s
            value_low = entry_price - high_s
        value_close = side * (close_s - entry_price)

        n = t1 - t0  # number of active bars (t0..t1-1)
        cm_fav = np.maximum.accumulate(np.concatenate([[0.0], value_high[1:]]))[:n]
        cm_adv = np.maximum.accumulate(np.concatenate([[0.0], -value_low[1:]]))[:n]
        mfe_so_far = np.maximum(cm_fav, 0.0)
        mae_so_far = np.maximum(cm_adv, 0.0)

        rev_max_fav = np.maximum.accumulate(value_high[::-1])[::-1]
        rev_min_adv = np.minimum.accumulate(value_low[::-1])[::-1]
        future_best_value = rev_max_fav[1:n + 1]
        future_worst_value = rev_min_adv[1:n + 1]

        future_exit_value = value_close[n]  # constant: value at t1
        close_now_value = value_close[:n]

        remaining_value_to_exit = future_exit_value - close_now_value
        remaining_best_opportunity = future_best_value - close_now_value
        remaining_adverse_risk = close_now_value - future_worst_value
        y_cp = (remaining_value_to_exit <= 0).astype(float)

        lv = per_day_levels.get(d)
        if lv is not None:
            lvl_type, lvl_price, lvl_dist_ticks = nearest_level_distance(close[t0:t1], lv)
        else:
            lvl_type = np.array(["UNKNOWN"] * n); lvl_price = np.full(n, np.nan); lvl_dist_ticks = np.full(n, np.nan)

        bar_end_ts = g["bar_end_ts_ns"].to_numpy()[t0:t1]
        for i in range(n):
            snap_rows.append(dict(
                event_id=int(c["event_id"]), day=d, t0_idx=t0, t1_idx=t1,
                snapshot_bar_idx=t0 + i, bar_end_ts_ns=int(bar_end_ts[i]),
                side_primary=side, entry_price=entry_price,
                active_bars_elapsed=i, active_bars_remaining=n - i,
                close_now_value=float(close_now_value[i]),
                mfe_so_far=float(mfe_so_far[i]), mae_so_far=float(mae_so_far[i]),
                future_exit_value=float(future_exit_value),
                future_best_value=float(future_best_value[i]),
                future_worst_value=float(future_worst_value[i]),
                remaining_value_to_exit=float(remaining_value_to_exit[i]),
                remaining_best_opportunity=float(remaining_best_opportunity[i]),
                remaining_adverse_risk=float(remaining_adverse_risk[i]),
                y_cp=float(y_cp[i]),
                current_nearest_level_type=str(lvl_type[i]),
                current_nearest_level_price=float(lvl_price[i]) if np.isfinite(lvl_price[i]) else np.nan,
                current_distance_ticks=float(lvl_dist_ticks[i]) if np.isfinite(lvl_dist_ticks[i]) else np.nan,
                reaction_type=c["reaction_type"], training_gate_status=c["training_gate_status"],
                in_sample_contaminated=bool(pd.Timestamp(int(bar_end_ts[i]), unit="ns", tz="UTC")
                                             <= v4.MODEL_TRAINING_CUTOFF_UTC),
            ))

    snaps = pd.DataFrame(snap_rows)
    v4.log(f"  active position snapshots: {len(snaps)}  (from {len(cand) - n_zero_active_bars} positions with "
           f">=1 active bar; {n_zero_active_bars} candidates fired with 0 active bars remaining in their day)")

    snaps = snaps.merge(panel[["bar_end_ts_ns"] + panel_feat_cols], on="bar_end_ts_ns", how="left")
    snaps.to_parquet(v4.OUT_DIR / "active_position_snapshots_v4.parquet", index=False)

    cp_cols = ["event_id", "snapshot_bar_idx", "day", "side_primary", "active_bars_elapsed",
               "active_bars_remaining", "close_now_value", "mfe_so_far", "mae_so_far",
               "future_exit_value", "future_best_value", "future_worst_value",
               "remaining_value_to_exit", "remaining_best_opportunity", "remaining_adverse_risk",
               "y_cp", "in_sample_contaminated"]
    snaps[cp_cols].to_parquet(v4.OUT_DIR / "close_position_cp_labels_v4.parquet", index=False)

    diag_rows = [
        dict(metric="n_candidates_input", value=len(cand)),
        dict(metric="n_candidates_zero_active_bars", value=n_zero_active_bars),
        dict(metric="n_positions_with_snapshots", value=int(snaps["event_id"].nunique())),
        dict(metric="n_active_position_snapshots", value=len(snaps)),
        dict(metric="mean_snapshots_per_position", value=float(len(snaps) / max(snaps["event_id"].nunique(), 1))),
        dict(metric="pct_y_cp_1", value=float((snaps["y_cp"] == 1).mean())),
        dict(metric="pct_y_cp_0", value=float((snaps["y_cp"] == 0).mean())),
        dict(metric="fixed_threshold_used", value=False),
        dict(metric="label_definition", value="y_cp=1 iff remaining_value_to_exit<=0 (per-path realized "
                                              "comparison vs the existing model's own fixed +40-bar "
                                              "horizon exit, day-bounded) - no fixed point/percent threshold"),
        dict(metric="mean_remaining_value_to_exit", value=float(snaps["remaining_value_to_exit"].mean())),
        dict(metric="mean_remaining_best_opportunity", value=float(snaps["remaining_best_opportunity"].mean())),
        dict(metric="mean_remaining_adverse_risk", value=float(snaps["remaining_adverse_risk"].mean())),
    ]
    diag_df = pd.DataFrame(diag_rows)
    diag_df.to_csv(v4.OUT_DIR / "cp_label_diagnostics_v4.csv", index=False)

    print(diag_df.to_string(index=False))
    v4.log("06 complete.")
    return snaps, diag_df


if __name__ == "__main__":
    main()
