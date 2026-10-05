"""
01_build_bar_feature_panel.py - bar-level feature + forward-return panel.

Builds the NQU6 bar-level panel (read-only from v1's frozen snapshot) with
the 7 raw features named in the build spec:
  vpin                     <- master column 'vpin'
  delta_norm               <- master column 'delta_norm'
  sweep_imbal              <- master column 'sweep_imbalance_norm'
  buy_frac                 <- master column 'buy_ratio'
  bid_pull_pressure        <- book-flow cache (abs_flow-denominator convention,
                               same formula validated in the prior institutional
                               audit and re-used unchanged in v1/diagnostic)
  ask_pull_minus_bid_pull  <- book-flow cache (same)
  volatility_5             <- master column 'volatility_5'

Day-bounded forward log returns at H in {1,3,5,10,20,40} are also computed
here - these are FUTURE values, used ONLY as the dependent variable for the
rolling-IC estimator in script 02, never as a model input feature themselves.

Causal (backward-only) z20 / rolling-5-bar-sum transforms of each of the 7
raw features are also added here, for later use in the feature_value x
rolling_ic interaction terms (script 04).

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
HORIZONS = cfg["rolling_ic"]["horizons_bars"]
ZW = cfg["zscore"]["window"]
RW = cfg["zscore"]["roll_sum_window"]

MASTER_COL_MAP = {
    "vpin": "vpin",
    "delta_norm": "delta_norm",
    "sweep_imbal": "sweep_imbalance_norm",
    "buy_frac": "buy_ratio",
    "volatility_5": "volatility_5",
}
BOOKFLOW_FEATURES = {"bid_pull_pressure", "ask_pull_minus_bid_pull"}


def main():
    v2.log("01: loading NQU6 master + book-flow cache, building bar feature panel...")
    nqu6 = v2.load_nqu6_master().sort_values("bar_index").reset_index(drop=True)
    n_bars = len(nqu6)
    v2.log(f"  NQU6 master bars: {n_bars}")

    panel = pd.DataFrame({
        "bar_idx": nqu6["bar_index"].to_numpy(),
        "day": nqu6["day"].to_numpy(),
        "bar_end_ts_ns": nqu6["bar_end_ts_ns"].to_numpy(),
        "px_close": nqu6["px_close"].to_numpy(),
        "px_high": nqu6["px_high"].to_numpy(),
        "px_low": nqu6["px_low"].to_numpy(),
    })
    for new_name, master_col in MASTER_COL_MAP.items():
        panel[new_name] = nqu6[master_col].to_numpy()

    v2.log(f"  merging book-flow cache (depth={cfg['scope']['level_candle_depth']}) for bid_pull_pressure / ask_pull_minus_bid_pull...")
    lc = v2.load_level_candles(cfg["scope"]["level_candle_depth"])
    bf = v2.aggregate_book_flow_bars(lc).rename(columns={"bar_idx": "bar_idx"})
    bf_small = bf[["bar_idx", "bid_pull_pressure", "ask_pull_minus_bid_pull"]]
    panel = panel.merge(bf_small, on="bar_idx", how="left")
    n_bf_missing = panel["bid_pull_pressure"].isna().sum()
    v2.log(f"  book-flow features missing (no cache coverage, e.g. 2026-06-14 rollover-warmup): "
          f"{n_bf_missing} / {n_bars} bars")

    v2.log("  computing day-bounded forward log returns H in " + str(HORIZONS) + "...")
    panel = v2.add_forward_returns_day_bounded(panel, price_col="px_close", day_col="day", horizons=HORIZONS)

    v2.log("  computing CAUSAL z20 / roll5sum transforms for all 7 raw features...")
    for feat in RAW_FEATURES:
        panel[f"{feat}_z20"] = v2.causal_z20(panel[feat], window=ZW)
        panel[f"{feat}_roll5sum"] = v2.causal_roll_sum(panel[feat], window=RW)

    panel.to_parquet(v2.OUT_DIR / "_bar_feature_panel.parquet", index=False)

    v2.log(f"01 complete: panel shape={panel.shape}")
    for feat in RAW_FEATURES:
        n_valid = panel[feat].notna().sum()
        print(f"FEATURE_COVERAGE__{feat}: {n_valid} / {n_bars}")
    for h in HORIZONS:
        n_valid = panel[f"fwd_ret_{h}"].notna().sum()
        print(f"FWD_RET_COVERAGE__H{h}: {n_valid} / {n_bars}")
    return panel


if __name__ == "__main__":
    main()
