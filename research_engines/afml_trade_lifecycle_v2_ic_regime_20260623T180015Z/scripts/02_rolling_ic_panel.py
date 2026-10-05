"""
02_rolling_ic_panel.py - rolling_feature_ic_panel.parquet

For each of the 7 raw features x each horizon H in {1,3,5,10,20,40}, computes
the past-only rolling Spearman IC and its reliability columns (rolling_ic,
rolling_ic_sign, rolling_ic_abs_strength, rolling_ic_tstat,
rolling_ic_stability, rolling_ic_window_n) at every bar - see
v2_common.rolling_spearman_ic_no_lookahead for the exact no-lookahead
construction (independently verified against scipy.stats.spearmanr and
against a future-data perturbation test before this script was written).

Output is LONG format: one row per (bar_idx, feature, horizon).

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v2_common as v2

cfg = v2.load_config()
RAW_FEATURES = cfg["rolling_ic"]["features"]
HORIZONS = cfg["rolling_ic"]["horizons_bars"]
WINDOW_N = cfg["rolling_ic"]["window_n"]
MIN_WINDOW_N = cfg["rolling_ic"]["min_window_n"]
N_SUBWINDOWS = cfg["rolling_ic"]["stability_n_subwindows"]


def main():
    v2.log("02: loading bar feature panel, computing rolling IC for all feature x horizon combos...")
    panel = pd.read_parquet(v2.OUT_DIR / "_bar_feature_panel.parquet")

    frames = []
    for feat in RAW_FEATURES:
        for h in HORIZONS:
            v2.log(f"  feature={feat} horizon=H{h} ...")
            ic_df = v2.rolling_spearman_ic_no_lookahead(
                panel[feat], panel[f"fwd_ret_{h}"], horizon=h,
                window_n=WINDOW_N, min_window_n=MIN_WINDOW_N,
                stability_n_subwindows=N_SUBWINDOWS,
            )
            ic_df["bar_idx"] = panel["bar_idx"].to_numpy()
            ic_df["day"] = panel["day"].to_numpy()
            ic_df["bar_end_ts_ns"] = panel["bar_end_ts_ns"].to_numpy()
            ic_df["feature"] = feat
            ic_df["horizon"] = h
            frames.append(ic_df)

    ic_panel = pd.concat(frames, ignore_index=True)
    cols = ["bar_idx", "day", "bar_end_ts_ns", "feature", "horizon", "rolling_ic",
            "rolling_ic_sign", "rolling_ic_abs_strength", "rolling_ic_tstat",
            "rolling_ic_stability", "rolling_ic_window_n"]
    ic_panel = ic_panel[cols]
    ic_panel.to_parquet(v2.OUT_DIR / "rolling_feature_ic_panel.parquet", index=False)

    v2.log(f"02 complete: rolling_feature_ic_panel.parquet shape={ic_panel.shape} "
          f"({len(RAW_FEATURES)} features x {len(HORIZONS)} horizons x {panel.shape[0]} bars)")
    n_valid = ic_panel["rolling_ic"].notna().sum()
    print(f"ROLLING_IC_PANEL_ROWS: {len(ic_panel)}")
    print(f"ROLLING_IC_VALID_ROWS: {n_valid}")
    print(f"ROLLING_IC_PCT_VALID: {100 * n_valid / len(ic_panel):.2f}")
    return ic_panel


if __name__ == "__main__":
    main()
