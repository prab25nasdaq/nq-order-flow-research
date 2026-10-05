"""
02_velocity_forward_path.py - Part B: price movement / velocity features +
forward path (MFE/MAE/final_return) metrics, per CLOSED bar.

Bar-aggregated (sum across the price axis at that bar - reuses
thickness_common.aggregate_book_flow_bars, identical formula to the prior
Atlas), joined to the NQU6 master's own OHLC. Day-bounded (no cross-day
carryover - matches the prior Atlas's 04_mfe_mae_after_touch.py
convention), CLOSED bars only.

MFE_N/MAE_N/final_return_N here use a FIXED LONG-equivalent convention
(there is no trade side defined for an arbitrary bar, unlike a level-touch
event which has side_of_approach) - documented explicitly: these are
descriptive forward-path diagnostics for studying how price behaves after
bars of a given thickness, NOT trade recommendations. Side-AWARE MFE/MAE
for actual level touches and HistGB events are handled separately in
Parts E/F by reusing the prior Atlas's / v4's own side-adjusted fields.

READ-ONLY. Writes only price_velocity_panel.parquet and
thickness_forward_path_panel.parquet under this engine's own outputs/.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import thickness_common as tc

cfg = tc.load_config()
TICK = tc.TICK_SIZE
NEAR_PRICE = tc.NEAR_TICKS_PRICE
HORIZONS = cfg["windows_bars"]["forward_horizons"]            # [5,10,20,40]
MOVE_TARGETS = cfg["windows_bars"]["bars_to_move_targets_ticks"]  # [5,10,20]
REVISIT_WINDOW = cfg["windows_bars"]["revisit_window_bars"]    # 20
NEAR_PRICE_TICKS_DWELL = cfg["windows_bars"]["time_spent_near_price_ticks"]  # 4
DEPTH = cfg["scope"]["depth"]
DAYS = cfg["scope"]["cache_days_available"]


def compute_day_features(day_df: pd.DataFrame) -> pd.DataFrame:
    c = day_df["px_close"].to_numpy(dtype=float)
    h = day_df["px_high"].to_numpy(dtype=float)
    l = day_df["px_low"].to_numpy(dtype=float)
    n = len(day_df)
    near_band = NEAR_PRICE_TICKS_DWELL * TICK

    bar_range_points = h - l
    close_to_close_return = np.r_[np.nan, np.diff(c)]
    absolute_return = np.abs(close_to_close_return)

    bars_to_move = {tgt: np.full(n, np.nan) for tgt in MOVE_TARGETS}
    time_spent_near_price = np.full(n, np.nan)
    number_of_revisits = np.full(n, np.nan)
    rejection_count = np.full(n, np.nan)
    breakout_count = np.full(n, np.nan)
    mfe = {H: np.full(n, np.nan) for H in HORIZONS}
    mae = {H: np.full(n, np.nan) for H in HORIZONS}
    final_return = {H: np.full(n, np.nan) for H in HORIZONS}

    for t in range(n):
        if not np.isfinite(c[t]):
            continue
        c0 = c[t]
        jmax_full = n - 1

        # bars_to_move_N_ticks: first j>=1 such that |c[t+j]-c0| >= N ticks
        for tgt in MOVE_TARGETS:
            thresh = tgt * TICK
            found = np.nan
            for j in range(t + 1, min(t + 200, n)):  # cap search at 200 bars ahead
                if np.isfinite(c[j]) and abs(c[j] - c0) >= thresh:
                    found = j - t
                    break
            bars_to_move[tgt][t] = found

        # time_spent_near_price / number_of_revisits over the revisit window
        win_end = min(t + REVISIT_WINDOW, n - 1)
        if win_end > t:
            seg = c[t + 1:win_end + 1]
            valid_seg = np.isfinite(seg)
            near_mask = valid_seg & (np.abs(seg - c0) <= near_band)
            time_spent_near_price[t] = float(near_mask.sum())
            departed = False
            revisits = 0
            for v, is_near in zip(seg, near_mask):
                if not np.isfinite(v):
                    continue
                if not is_near:
                    departed = True
                elif is_near and departed:
                    revisits += 1
                    departed = False
            number_of_revisits[t] = revisits

            # rejection_count: direction reversals (sign changes in bar-to-bar
            # return) WHILE price stays within the near-band of c0
            seg_full = c[t:win_end + 1]
            rej = 0
            prev_sign = 0
            for k in range(1, len(seg_full)):
                if not (np.isfinite(seg_full[k]) and np.isfinite(seg_full[k - 1])):
                    continue
                if abs(seg_full[k] - c0) > near_band:
                    continue
                d = seg_full[k] - seg_full[k - 1]
                sign = 1 if d > 0 else (-1 if d < 0 else 0)
                if sign != 0 and prev_sign != 0 and sign != prev_sign:
                    rej += 1
                if sign != 0:
                    prev_sign = sign
            rejection_count[t] = rej

            # breakout_count: 1 if price decisively clears the near-band and
            # does not return within the window (a "decisive move" proxy),
            # else 0
            beyond = np.where(valid_seg, np.abs(seg - c0) > near_band, False)
            if beyond.any():
                first_beyond = int(np.argmax(beyond))
                stays_beyond = bool(np.all(beyond[first_beyond:][np.isfinite(seg[first_beyond:])]))
                breakout_count[t] = 1.0 if stays_beyond else 0.0
            else:
                breakout_count[t] = 0.0

        for H in HORIZONS:
            jmax = min(t + H, n - 1)
            if jmax <= t:
                continue
            seg_h = h[t + 1:jmax + 1]
            seg_l = l[t + 1:jmax + 1]
            seg_c = c[t + 1:jmax + 1]
            if len(seg_h) == 0:
                continue
            mfe[H][t] = float(np.nanmax(seg_h) - c0)   # LONG-equivalent convention, documented above
            mae[H][t] = float(np.nanmin(seg_l) - c0)
            final_return[H][t] = float(seg_c[-1] - c0) if len(seg_c) and np.isfinite(seg_c[-1]) else np.nan

    out = pd.DataFrame({
        "bar_end_ts_ns": day_df["bar_end_ts_ns"].to_numpy(),
        "bar_idx_in_day": np.arange(n),
        "close": c, "px_high": h, "px_low": l,
        "bar_range_points": bar_range_points,
        "close_to_close_return": close_to_close_return,
        "absolute_return": absolute_return,
        "directional_velocity": np.nan,  # filled after merge with abs_flow (Part B)
        "price_velocity": np.nan,
        "time_spent_near_price": time_spent_near_price,
        "number_of_revisits": number_of_revisits,
        "rejection_count": rejection_count,
        "breakout_count": breakout_count,
        "window_truncated_at_day_edge": np.array([t + max(HORIZONS) >= n for t in range(n)]),
    })
    for tgt in MOVE_TARGETS:
        out[f"bars_to_move_{tgt}_ticks"] = bars_to_move[tgt]
    for H in HORIZONS:
        out[f"MFE_{H}"] = mfe[H]
        out[f"MAE_{H}"] = mae[H]
        out[f"final_return_{H}"] = final_return[H]
    return out


def main():
    tc.log("02: loading NQU6 master + bar-aggregated book flow...")
    nqu6 = tc.load_nqu6_master()
    nqu6_scoped = nqu6[nqu6["rithmic_date_str"].isin(DAYS)].reset_index(drop=True)

    raw_levels = tc.load_level_candles(depth=DEPTH)
    bar_agg = tc.aggregate_book_flow_bars(raw_levels)
    tc.log(f"  bar-aggregated book flow: {len(bar_agg)} closed bars")

    frames = []
    for d in sorted(nqu6_scoped["rithmic_date_str"].unique()):
        day_df = nqu6_scoped[nqu6_scoped["rithmic_date_str"] == d].reset_index(drop=True)
        feats = compute_day_features(day_df)
        feats["rithmic_date_str"] = d
        frames.append(feats)
        tc.log(f"  {d}: {len(day_df)} bars processed")
    panel = pd.concat(frames, ignore_index=True)

    # attach bar-aggregated book-flow (abs_flow/signed_flow/etc.) and compute
    # velocity = |return| / abs_flow (volume-proxy denominator)
    bar_agg_small = bar_agg[["bar_end_ts_ns", "abs_flow", "signed_flow", "trade_volume",
                              "bid_add", "bid_pull", "ask_add", "ask_pull",
                              "bid_pull_pressure", "ask_pull_pressure", "book_imbalance"]]
    panel = panel.merge(bar_agg_small, on="bar_end_ts_ns", how="left")
    panel["price_velocity"] = panel["absolute_return"] / panel["abs_flow"].replace(0, np.nan)
    panel["directional_velocity"] = panel["close_to_close_return"] / panel["abs_flow"].replace(0, np.nan)

    # attach the bar's own thickness classification (Part A panel, evaluated
    # at the price level NEAREST TO THIS BAR'S OWN CLOSE - NOT the thickest
    # level active anywhere in the bar's price range, which would trivially
    # bias every bar toward "FAT" by construction) so Part D's group-bys
    # reflect "is the zone price is actually sitting in thin or fat".
    thickness = pd.read_parquet(tc.OUT_DIR / "book_flow_thickness_panel.parquet")
    thickness["price_level_rounded"] = (thickness["price_level"] / TICK).round() * TICK
    panel["close_rounded"] = (panel["close"] / TICK).round() * TICK
    thickness_small = thickness[["bar_end_ts_ns", "price_level_rounded", "price_level",
                                 "thickness_percentile", "thickness_zscore",
                                 "is_thin_zone", "is_medium_zone", "is_fat_zone"]]
    nearest_thick = panel[["bar_end_ts_ns", "close_rounded"]].merge(
        thickness_small, left_on=["bar_end_ts_ns", "close_rounded"],
        right_on=["bar_end_ts_ns", "price_level_rounded"], how="left")
    # a bar's close can coincide with >1 cached depth row only in pathological
    # cases (it should not, given price_level granularity == tick size); guard
    # with drop_duplicates to keep this a strict 1:1 join regardless.
    nearest_thick = nearest_thick.drop_duplicates(subset=["bar_end_ts_ns"], keep="first")
    nearest_thick = nearest_thick.rename(columns={"price_level": "thickness_price_level_at_close"})
    nearest_thick = nearest_thick[["bar_end_ts_ns", "thickness_price_level_at_close",
                                   "thickness_percentile", "thickness_zscore",
                                   "is_thin_zone", "is_medium_zone", "is_fat_zone"]]
    panel = panel.merge(nearest_thick, on="bar_end_ts_ns", how="left")

    price_velocity_cols = [
        "bar_end_ts_ns", "rithmic_date_str", "bar_idx_in_day", "close", "px_high", "px_low",
        "bar_range_points", "close_to_close_return", "absolute_return", "price_velocity",
        "directional_velocity", "abs_flow", "signed_flow",
        "bars_to_move_5_ticks", "bars_to_move_10_ticks", "bars_to_move_20_ticks",
        "time_spent_near_price", "number_of_revisits", "rejection_count", "breakout_count",
        "thickness_percentile", "thickness_zscore", "is_thin_zone", "is_medium_zone", "is_fat_zone",
    ]
    price_velocity_panel = panel[price_velocity_cols]
    pv_path = tc.OUT_DIR / "price_velocity_panel.parquet"
    price_velocity_panel.to_parquet(pv_path, index=False)
    tc.log(f"  wrote {pv_path} ({len(price_velocity_panel)} rows)")

    fwd_cols = ["bar_end_ts_ns", "rithmic_date_str", "bar_idx_in_day", "close",
                "window_truncated_at_day_edge", "thickness_percentile", "is_thin_zone",
                "is_medium_zone", "is_fat_zone"]
    for H in HORIZONS:
        fwd_cols += [f"MFE_{H}", f"MAE_{H}", f"final_return_{H}"]
    forward_path_panel = panel[fwd_cols]
    fp_path = tc.OUT_DIR / "thickness_forward_path_panel.parquet"
    forward_path_panel.to_parquet(fp_path, index=False)
    tc.log(f"  wrote {fp_path} ({len(forward_path_panel)} rows)")

    print(panel[["price_velocity", "directional_velocity", "MFE_40", "MAE_40"]].describe().to_string())
    tc.log("02 complete.")
    return panel


if __name__ == "__main__":
    main()
