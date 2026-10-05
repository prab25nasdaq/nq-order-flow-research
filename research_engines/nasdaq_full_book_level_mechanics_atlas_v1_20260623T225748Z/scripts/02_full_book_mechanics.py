"""
02_full_book_mechanics.py - Part B: full-book mechanics around each level
touch, computed at BAR resolution from the production Book Flow cache (see
atlas_common.py module docstring for the scope-decision rationale - this is
genuine full-L2-derived data, pre-aggregated to bar granularity rather than
reconstructed at tick resolution).

For each event, computes the MEAN of every mechanics metric over each
window (pre_100/pre_50/pre_20/pre_10/touch/post_5/post_10/post_20/post_40),
relative to the event's own bar_idx_in_day, DAY-BOUNDED (a window never
reads into the previous/next day's bars - truncated at the day edge and
the truncation is recorded).

queue_replenishment_after_trade, depth_recovery_speed, and
microprice_slope are reported as BAR-RESOLUTION PROXIES (see module
docstring) - never claimed as microsecond-accurate.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path
from typing import Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atlas_common as ac

cfg = ac.load_config()
PRE_WINDOWS = cfg["windows_bars"]["pre"]      # [100,50,20,10]
POST_WINDOWS = cfg["windows_bars"]["post"]    # [5,10,20,40]

BOOKFLOW_METRICS = ["bid_add", "bid_pull", "ask_add", "ask_pull", "net_bid_flow", "net_ask_flow",
                    "signed_flow", "abs_flow", "trade_volume", "buy_trade_volume", "sell_trade_volume",
                    "resting_depth", "bid_pull_pressure", "ask_pull_pressure", "aggressive_buy_ratio",
                    "aggressive_sell_ratio", "trade_delta", "book_imbalance", "mid_price", "close_price"]
PANEL_METRICS = ["master_vpin", "master_volatility_5", "master_entropy_score", "dash_toxicity",
                 "dash_liquidity_cost", "dash_cs_spread", "master_mlofi_norm"]


def build_per_day_arrays(day: str) -> pd.DataFrame:
    """One row per bar_idx_in_day for that day, with all Book Flow + panel metrics aligned."""
    bf = ac.load_level_candles(depth=10, dates=[day])
    if bf.empty:
        return pd.DataFrame()
    agg = ac.aggregate_book_flow_bars(bf)
    agg = agg.sort_values("bar_idx").reset_index(drop=True)
    agg["bar_idx_in_day"] = agg["bar_idx"]

    # the Book Flow cache's trade_volume_at_price (and the aggressive_buy/sell_ratio
    # derived from it) is uniformly ZERO in this cache - a genuine data-availability
    # gap, not a derivation bug (verified directly). The NQU6 master's own buy_vol/
    # sell_vol columns ARE genuinely populated bar-level aggressor volume, so
    # recompute aggressive_buy_ratio/aggressive_sell_ratio/trade_delta from THAT
    # real source instead, joined on bar_end_ts_ns.
    nqu6 = ac.load_nqu6_master()
    bv = nqu6[["bar_end_ts_ns", "buy_vol", "sell_vol"]].copy()
    agg = agg.drop(columns=["aggressive_buy_ratio", "aggressive_sell_ratio", "trade_delta"], errors="ignore")
    agg = agg.merge(bv, on="bar_end_ts_ns", how="left")
    agg["trade_volume"] = agg["buy_vol"].fillna(0) + agg["sell_vol"].fillna(0)
    agg["buy_trade_volume"] = agg["buy_vol"]
    agg["sell_trade_volume"] = agg["sell_vol"]
    agg["aggressive_buy_ratio"] = agg["buy_vol"] / agg["trade_volume"].replace(0, np.nan)
    agg["aggressive_sell_ratio"] = agg["sell_vol"] / agg["trade_volume"].replace(0, np.nan)
    agg["trade_delta"] = agg["buy_vol"] - agg["sell_vol"]

    panel = ac.load_v4_feature_panel()
    keep_panel = [c for c in PANEL_METRICS if c and c in panel.columns]
    panel_small = panel[["bar_end_ts_ns"] + keep_panel].copy()
    out = agg.merge(panel_small, on="bar_end_ts_ns", how="left")

    # microprice_slope PROXY: per-bar delta of mid_price (used later for window-mean of slope)
    out["microprice_delta"] = out["mid_price"].diff()
    return out


def window_slice_mean(arr: np.ndarray, center: int, lo: int, hi: int, n_bars: int) -> Tuple[float, bool]:
    lo_c = max(0, lo); hi_c = min(n_bars - 1, hi)
    truncated = (lo_c != lo) or (hi_c != hi)
    if lo_c > hi_c:
        return np.nan, True
    seg = arr[lo_c:hi_c + 1]
    seg = seg[np.isfinite(seg)]
    return (float(np.mean(seg)) if len(seg) else np.nan), truncated


def main():
    ac.log("02: computing full-book mechanics windows (bar-resolution, Book Flow cache)...")
    ev = pd.read_parquet(ac.OUT_DIR / "level_touch_events.parquet")
    days = sorted(ev["rithmic_date_str"].unique())

    metric_cols = BOOKFLOW_METRICS + [c for c in PANEL_METRICS if c] + ["microprice_delta"]
    window_defs = [(f"pre_{w}", -w, -1) for w in PRE_WINDOWS] + [("touch", 0, 0)] + \
                  [(f"post_{w}", 1, w) for w in POST_WINDOWS]

    all_rows = []
    n_truncated = 0
    for d in days:
        day_arr = build_per_day_arrays(d)
        if day_arr.empty:
            ac.log(f"  {d}: NO Book Flow cache coverage - skipping")
            continue
        n_bars = int(day_arr["bar_idx_in_day"].max()) + 1
        arr_by_metric = {m: day_arr.set_index("bar_idx_in_day")[m].reindex(range(n_bars)).to_numpy()
                         for m in metric_cols if m in day_arr.columns}
        ev_day = ev[ev["rithmic_date_str"] == d]
        for _, row in ev_day.iterrows():
            t = int(row["bar_idx_in_day"])
            rec = dict(event_id=int(row["event_id"]))
            day_truncated = False
            for wname, off_lo, off_hi in window_defs:
                lo, hi = t + off_lo, t + off_hi
                for m in metric_cols:
                    if m not in arr_by_metric:
                        continue
                    val, trunc = window_slice_mean(arr_by_metric[m], t, lo, hi, n_bars)
                    rec[f"{wname}__{m}"] = val
                    day_truncated = day_truncated or trunc
            # bar-resolution PROXIES (never tick-level - see module docstring)
            rec["depth_recovery_speed_PROXY"] = (rec.get("post_5__resting_depth", np.nan) -
                                                 rec.get("pre_10__resting_depth", np.nan))
            rec["queue_replenishment_after_trade_PROXY"] = np.nansum([
                rec.get("post_5__bid_add", np.nan), rec.get("post_5__ask_add", np.nan)])
            rec["microprice_slope_PROXY"] = rec.get("post_10__microprice_delta", np.nan)
            rec["window_truncated_at_day_edge"] = day_truncated
            n_truncated += int(day_truncated)
            all_rows.append(rec)

    mech = pd.DataFrame(all_rows)
    mech.to_parquet(ac.OUT_DIR / "level_touch_full_book_mechanics.parquet", index=False)

    catalog_rows = []
    for m in metric_cols:
        catalog_rows.append(dict(
            feature_family="bookflow_cache_bar_resolution" if m in BOOKFLOW_METRICS else "dashboard_parity_panel",
            base_metric=m,
            window_columns=", ".join(f"{w[0]}__{m}" for w in window_defs),
            source="book_flow_chart/cache (genuine full-L2-derived, bar-aggregated)" if m in BOOKFLOW_METRICS
                   else "v4 dashboard-parity feature panel (reused from v3)",
        ))
    for proxy in ["depth_recovery_speed_PROXY", "queue_replenishment_after_trade_PROXY", "microprice_slope_PROXY"]:
        catalog_rows.append(dict(feature_family="BAR_RESOLUTION_PROXY_NOT_TICK_LEVEL", base_metric=proxy,
                                 window_columns=proxy, source="derived from bar-resolution Book Flow windows - "
                                 "see atlas_common.py scope-decision docstring"))
    cat_df = pd.DataFrame(catalog_rows)
    cat_df.to_csv(ac.OUT_DIR / "full_book_mechanics_feature_catalog.csv", index=False)

    ac.log(f"  mechanics rows: {len(mech)}  cols: {len(mech.columns)}  "
           f"window_truncated_at_day_edge: {n_truncated} ({100*n_truncated/max(len(mech),1):.1f}%)")
    ac.log("02 complete.")
    return mech, cat_df


if __name__ == "__main__":
    main()
