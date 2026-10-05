"""
04_thin_fat_behavior.py - Part D: thin vs fat zone behavior, grouped by
thickness bucket x HVN/LVN/POC/VAH/VAL x session x volatility regime x
VPIN/toxicity regime x trend/regime state.

Joins this Atlas's own bar-level thickness/friction/forward-path panels
(Parts A-C) with the prior Level Mechanics Atlas's already-validated
level_touch_events.parquet (read-only reuse, not re-derived) for the
HVN/LVN/POC/VAH/VAL summary, and with the existing model's own
dash_market_state (CHOP_RANDOM/NO_EDGE/REGIME_BREAK) for the
trend/regime-state dimension.

READ-ONLY. Writes only thin_vs_fat_behavior_summary.csv,
thin_zone_behavior.csv, fat_zone_behavior.csv, and
hvn_lvn_poc_thickness_summary.csv under this engine's own outputs/.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import thickness_common as tc

cfg = tc.load_config()
HORIZONS = cfg["windows_bars"]["forward_horizons"]
DEPTH = cfg["scope"]["depth"]


def build_master_panel() -> pd.DataFrame:
    pv = pd.read_parquet(tc.OUT_DIR / "price_velocity_panel.parquet")
    fric = pd.read_parquet(tc.OUT_DIR / "auction_friction_features.parquet")
    fwd = pd.read_parquet(tc.OUT_DIR / "thickness_forward_path_panel.parquet")

    fric_small = fric[["bar_end_ts_ns", "auction_friction_score", "low_friction_travel_score",
                       "absorption_score", "liquidity_vacuum_score", "two_way_exchange_score",
                       "replenishment_score", "imbalance_score"]]
    fwd_small = fwd.drop(columns=["rithmic_date_str", "bar_idx_in_day", "close",
                                  "thickness_percentile", "is_thin_zone", "is_medium_zone", "is_fat_zone"])
    panel = pv.merge(fric_small, on="bar_end_ts_ns", how="left").merge(fwd_small, on="bar_end_ts_ns", how="left")

    # regime context: session, vol_state (causal tercile), vpin_state/toxicity (from v4 feature panel)
    nqu6 = tc.load_nqu6_master()
    nqu6 = nqu6[nqu6["rithmic_date_str"].isin(cfg["scope"]["cache_days_available"])].reset_index(drop=True)
    nqu6["minute_of_day"] = pd.to_numeric(nqu6["minute_of_day"], errors="coerce")
    nqu6["session"] = nqu6["minute_of_day"].apply(lambda m: tc.session_label(int(m)) if pd.notna(m) else "UNKNOWN")
    nqu6["vol_state"] = tc.rolling_state_tercile(nqu6["volatility_5"].to_numpy(),
                                                 window=cfg["state_binning"]["window"],
                                                 min_ref=cfg["state_binning"]["min_ref"])
    ctx = nqu6[["bar_end_ts_ns", "session", "vol_state"]]
    panel = panel.merge(ctx, on="bar_end_ts_ns", how="left")

    v4panel = tc.load_v4_feature_panel()
    state_cols = v4panel[["bar_end_ts_ns", "dash_vpin_state", "dash_toxicity", "dash_market_state"]].copy()
    state_cols = state_cols.rename(columns={"dash_vpin_state": "vpin_state", "dash_market_state": "trend_state"})
    panel = panel.merge(state_cols, on="bar_end_ts_ns", how="left")

    panel["thickness_bucket"] = np.where(
        panel["is_thin_zone"], "THIN", np.where(panel["is_fat_zone"], "FAT",
        np.where(panel["is_medium_zone"], "MEDIUM", "UNKNOWN")))

    # derived rate flags (Part D metrics), built from Part B's already-computed forward fields
    panel["is_rejection"] = panel["rejection_count"] > 0
    panel["is_breakout_accepted"] = panel["breakout_count"] == 1
    panel["is_fake_breakout"] = (panel["breakout_count"] == 0) & (panel["rejection_count"] > 0)
    panel["is_revisited"] = panel["number_of_revisits"] > 0
    for H in HORIZONS:
        panel[f"continuation_{H}"] = np.sign(panel[f"final_return_{H}"]) == np.sign(panel["close_to_close_return"])
    panel["mfe_mae_ratio_40"] = panel["MFE_40"] / panel["MAE_40"].abs().replace(0, np.nan)
    return panel


def summarize_group(df: pd.DataFrame, group_col) -> pd.DataFrame:
    rows = []
    for g, sub in df.groupby(group_col, dropna=False):
        if len(sub) == 0:
            continue
        rec = dict(group=str(g), n=len(sub))
        rec["mean_bar_range_points"] = float(sub["bar_range_points"].mean())
        rec["mean_price_velocity"] = float(sub["price_velocity"].mean())
        for H in HORIZONS:
            rec[f"mean_MFE_{H}"] = float(sub[f"MFE_{H}"].mean())
            rec[f"mean_MAE_{H}"] = float(sub[f"MAE_{H}"].mean())
            ratio = sub[f"MFE_{H}"].mean() / abs(sub[f"MAE_{H}"].mean()) if sub[f"MAE_{H}"].mean() != 0 else np.nan
            rec[f"MFE_MAE_ratio_{H}"] = float(ratio) if np.isfinite(ratio) else None
            rec[f"continuation_rate_{H}"] = float(sub[f"continuation_{H}"].mean())
        rec["rejection_rate"] = float(sub["is_rejection"].mean())
        rec["breakout_acceptance_rate"] = float(sub["is_breakout_accepted"].mean())
        rec["fake_breakout_rate"] = float(sub["is_fake_breakout"].mean())
        rec["revisit_rate"] = float(sub["is_revisited"].mean())
        rec["mean_time_spent_near_price"] = float(sub["time_spent_near_price"].mean())
        rows.append(rec)
    return pd.DataFrame(rows)


def main():
    tc.log("04: building thin/fat zone behavior summaries...")
    panel = build_master_panel()

    summary_rows = []
    for dim, col in [("thickness_bucket", "thickness_bucket"), ("session", "session"),
                     ("vol_state", "vol_state"), ("vpin_state", "vpin_state"),
                     ("trend_state", "trend_state")]:
        sub_summary = summarize_group(panel, col)
        sub_summary.insert(0, "group_dim", dim)
        summary_rows.append(sub_summary)
    thin_vs_fat_summary = pd.concat(summary_rows, ignore_index=True)
    out1 = tc.OUT_DIR / "thin_vs_fat_behavior_summary.csv"
    thin_vs_fat_summary.to_csv(out1, index=False)
    tc.log(f"  wrote {out1}")

    # thin-zone deep dive, cross-tabbed by session/vol_state/vpin_state/trend_state
    thin = panel[panel["thickness_bucket"] == "THIN"]
    thin_rows = []
    for dim, col in [("session", "session"), ("vol_state", "vol_state"),
                     ("vpin_state", "vpin_state"), ("trend_state", "trend_state")]:
        s = summarize_group(thin, col)
        s.insert(0, "group_dim", dim)
        thin_rows.append(s)
    thin_zone_behavior = pd.concat(thin_rows, ignore_index=True)
    out2 = tc.OUT_DIR / "thin_zone_behavior.csv"
    thin_zone_behavior.to_csv(out2, index=False)
    tc.log(f"  wrote {out2} (n_thin_bars={len(thin)})")

    fat = panel[panel["thickness_bucket"] == "FAT"]
    fat_rows = []
    for dim, col in [("session", "session"), ("vol_state", "vol_state"),
                     ("vpin_state", "vpin_state"), ("trend_state", "trend_state")]:
        s = summarize_group(fat, col)
        s.insert(0, "group_dim", dim)
        fat_rows.append(s)
    fat_zone_behavior = pd.concat(fat_rows, ignore_index=True)
    out3 = tc.OUT_DIR / "fat_zone_behavior.csv"
    fat_zone_behavior.to_csv(out3, index=False)
    tc.log(f"  wrote {out3} (n_fat_bars={len(fat)})")

    # HVN/LVN/POC/VAH/VAL thickness summary: join level_touch_events (prior
    # Atlas, read-only reuse) with THIS atlas's thickness panel evaluated AT
    # the touched level's exact price (nearest 0.25 tick).
    tc.log("  joining level_touch_events with thickness-at-level...")
    touches = tc.load_level_touch_events()
    thickness = pd.read_parquet(tc.OUT_DIR / "book_flow_thickness_panel.parquet")
    thickness_key = thickness[["bar_end_ts_ns", "price_level", "thickness_percentile",
                               "is_thin_zone", "is_medium_zone", "is_fat_zone"]].copy()
    thickness_key["price_level_r"] = (thickness_key["price_level"] / tc.TICK_SIZE).round()
    touches = touches.copy()
    touches["level_price_r"] = (touches["level_price"] / tc.TICK_SIZE).round()
    joined = touches.merge(
        thickness_key.rename(columns={"price_level_r": "level_price_r"}),
        on=["bar_end_ts_ns", "level_price_r"], how="left", suffixes=("", "_at_level"))

    hvn_lvn_rows = []
    for lt, sub in joined.groupby("level_type"):
        n_with_thickness = int(sub["thickness_percentile"].notna().sum())
        rec = dict(
            level_type=lt, n_touch_events=len(sub), n_with_thickness_data=n_with_thickness,
            pct_with_thickness_data=round(n_with_thickness / len(sub) * 100, 1) if len(sub) else None,
            mean_thickness_percentile_at_level=float(sub["thickness_percentile"].mean()) if n_with_thickness else None,
            pct_thin=float(sub["is_thin_zone"].mean()) if n_with_thickness else None,
            pct_medium=float(sub["is_medium_zone"].mean()) if n_with_thickness else None,
            pct_fat=float(sub["is_fat_zone"].mean()) if n_with_thickness else None,
        )
        hvn_lvn_rows.append(rec)
    hvn_lvn_summary = pd.DataFrame(hvn_lvn_rows).sort_values("level_type")
    out4 = tc.OUT_DIR / "hvn_lvn_poc_thickness_summary.csv"
    hvn_lvn_summary.to_csv(out4, index=False)
    tc.log(f"  wrote {out4}")

    print(thin_vs_fat_summary[thin_vs_fat_summary["group_dim"] == "thickness_bucket"][
        ["group", "n", "mean_price_velocity", "mean_MFE_40", "mean_MAE_40", "MFE_MAE_ratio_40",
         "rejection_rate", "breakout_acceptance_rate", "fake_breakout_rate", "revisit_rate"]
    ].to_string(index=False))
    print()
    print(hvn_lvn_summary.to_string(index=False))
    tc.log("04 complete.")
    return thin_vs_fat_summary, hvn_lvn_summary


if __name__ == "__main__":
    main()
