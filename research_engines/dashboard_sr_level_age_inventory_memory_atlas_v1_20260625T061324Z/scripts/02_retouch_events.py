"""
02_retouch_events.py - Part C: dashboard S/R retouch event universe.

For each lookback window's level registry (Part B output), forward-fills
each level's tracked price across its active lifetime (bar range between
its snapshots, never beyond its first/last observed snapshot, never beyond
its window-implied max age), then scans the continuous master's high/low
range for bars where price comes within near_ticks_price of the level.
Contiguous near-bars collapse into a single retouch EVENT.

READ-ONLY. Writes only inside this engine's own outputs/.
SHADOW / RESEARCH ONLY / NO MODEL TRAINING.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sr_common as sc

cfg = sc.load_config()
NEAR_PRICE = cfg["retouch"]["near_ticks_price"]
MIN_GAP = cfg["retouch"]["min_bars_between_touches_same_level"]
AGE_EDGES = cfg["age_buckets_bars"]
AGE_LABELS = cfg["age_bucket_labels"]


def age_bucket(age_bars: int, window_label: str) -> str:
    if window_label == "full_history":
        # full-history levels have no window-implied cap, so the bucket
        # edges below are used as given, including the open-ended top bucket
        pass
    for lo, hi, lbl in zip(AGE_EDGES[:-1], AGE_EDGES[1:], AGE_LABELS):
        if lo <= age_bars < hi:
            return lbl
    return AGE_LABELS[-1]


def main():
    sc.log("02: loading continuous master + level registry...")
    df = sc.load_continuous_master()
    n = len(df)
    high = pd.to_numeric(df["px_high"], errors="coerce").to_numpy()
    low = pd.to_numeric(df["px_low"], errors="coerce").to_numpy()
    close = pd.to_numeric(df["px_close"], errors="coerce").to_numpy()
    ts_col = "timestamp_utc" if "timestamp_utc" in df.columns else "timestamp"
    ts_arr = df[ts_col].to_numpy()
    bar_end_ts_ns = pd.to_numeric(df["bar_end_ts_ns"], errors="coerce").to_numpy()
    minute_of_day = pd.to_numeric(df.get("minute_of_day", pd.Series(np.nan, index=df.index)), errors="coerce").to_numpy()

    sc.log("  computing past-only volatility/VPIN regime labels (rolling tercile)...")
    vol_arr = pd.to_numeric(df.get("volatility_5", pd.Series(np.nan, index=df.index)), errors="coerce").to_numpy()
    vpin_arr = pd.to_numeric(df.get("vpin", pd.Series(np.nan, index=df.index)), errors="coerce").to_numpy()
    vol_regime = sc.rolling_state_tercile(vol_arr, window=500, min_ref=50)
    vpin_regime = sc.rolling_state_tercile(vpin_arr, window=500, min_ref=50)
    sessions = np.array([sc.session_label(m) for m in minute_of_day], dtype=object)

    levels = pd.read_parquet(sc.OUT_DIR / "dashboard_sr_levels_by_lookback.parquet")
    sr_levels = levels[levels["level_type"].isin(["SWING_RESISTANCE", "SWING_SUPPORT"])].copy()
    vp_levels = levels[~levels["level_type"].isin(["SWING_RESISTANCE", "SWING_SUPPORT"])].copy()
    sc.log(f"  {len(sr_levels)} swing S/R snapshot rows, {len(vp_levels)} volume-profile snapshot rows")

    all_events = []
    event_id_ctr = 0

    for (window_label,), lv_grp in sr_levels.groupby(["lookback_window"]):
        sc.log(f"  window={window_label}: building per-bar forward-filled level prices...")
        for level_id, lv in lv_grp.groupby("level_id"):
            lv = lv.sort_values("snapshot_bar")
            first_bar = int(lv["snapshot_bar"].iloc[0])
            last_bar = int(lv["snapshot_bar"].iloc[-1])
            created_at_bar = int(lv["created_at_bar"].iloc[0])
            level_type = lv["level_type"].iloc[0]
            if last_bar <= first_bar:
                continue
            idx = np.arange(first_bar, last_bar + 1)
            snap_bars = lv["snapshot_bar"].to_numpy()
            snap_px = lv["level_price"].to_numpy()
            snap_touch = lv["current_touch_count_past_only"].to_numpy()
            px_ff = np.interp(idx, snap_bars, snap_px)  # linear interp between snapshots (price drift is small)
            touch_ff = pd.Series(snap_touch, index=snap_bars).reindex(idx, method="ffill").to_numpy()

            seg_high = high[first_bar:last_bar + 1]
            seg_low = low[first_bar:last_bar + 1]
            near = (seg_low - NEAR_PRICE <= px_ff) & (seg_high + NEAR_PRICE >= px_ff)
            # first_bar is always >= created_at_bar by construction (a level can't be
            # snapshotted before it exists), so no extra guard is needed here.
            if not near.any():
                continue

            # collapse contiguous (or near-contiguous, gap<MIN_GAP) True runs into events
            near_idx = idx[near]
            splits = np.where(np.diff(near_idx) > MIN_GAP)[0]
            run_starts = np.concatenate(([0], splits + 1))
            run_ends = np.concatenate((splits, [len(near_idx) - 1]))

            touch_n = 0
            prev_touch_bar = None
            for rs, re in zip(run_starts, run_ends):
                run_bars = near_idx[rs:re + 1]
                # representative bar = closest approach within the run
                local_dist = np.minimum(
                    np.abs(low[run_bars] - px_ff[run_bars - first_bar]),
                    np.abs(high[run_bars] - px_ff[run_bars - first_bar]),
                )
                rep_bar = int(run_bars[np.argmin(local_dist)])
                rep_px = float(px_ff[rep_bar - first_bar])
                touch_n += 1
                level_age_bars = rep_bar - created_at_bar
                approach_side = "from_below" if close[max(0, rep_bar - 1)] < rep_px else "from_above"
                dist_ticks = float(round(abs(close[rep_bar] - rep_px) / sc.TICK_SIZE, 2))
                event_id_ctr += 1
                all_events.append({
                    "event_id": f"EVT_{event_id_ctr}",
                    "timestamp": str(ts_arr[rep_bar]),
                    "bar_end_ts_ns": int(bar_end_ts_ns[rep_bar]) if np.isfinite(bar_end_ts_ns[rep_bar]) else None,
                    "bar_t": int(rep_bar),
                    "close": float(close[rep_bar]), "high": float(high[rep_bar]), "low": float(low[rep_bar]),
                    "level_id": level_id, "level_price": rep_px, "level_type": level_type,
                    "dashboard_level_source": "compute_sr_levels", "lookback_window": window_label,
                    "distance_ticks": dist_ticks,
                    "level_age_bars": int(level_age_bars),
                    "level_age_bucket": age_bucket(level_age_bars, window_label),
                    "touch_number_for_this_level": touch_n,
                    "bars_since_previous_touch": (rep_bar - prev_touch_bar) if prev_touch_bar is not None else None,
                    "approach_side": approach_side,
                    "session": sessions[rep_bar],
                    "volatility_regime": vol_regime[rep_bar],
                    "vpin_regime": vpin_regime[rep_bar],
                    "liquidity_cost_regime": "NOT_AVAILABLE",
                    "touch_count_at_event_past_only": float(touch_ff[rep_bar - first_bar]) if np.isfinite(touch_ff[rep_bar - first_bar]) else None,
                })
                prev_touch_bar = rep_bar

    sc.log(f"  built {len(all_events)} swing-S/R retouch events")

    # volume-profile (POC/VAH/VAL/HVN/LVN) retouches - daily-snapshot levels, age always 0 by design
    sc.log("  building volume-profile retouch events (daily snapshots, no aging)...")
    for (window_label,), lv_grp in vp_levels.groupby(["lookback_window"]):
        for _, lv in lv_grp.iterrows():
            t0 = int(lv["snapshot_bar"])
            # this level is "live" reference context from t0 until the next day's snapshot
            t1 = min(t0 + 2000, n - 1)  # cap lookahead window for retouch scan to avoid O(n^2); generous (~2 days)
            px = float(lv["level_price"])
            seg_high = high[t0:t1 + 1]
            seg_low = low[t0:t1 + 1]
            near = (seg_low - NEAR_PRICE <= px) & (seg_high + NEAR_PRICE >= px)
            if not near.any():
                continue
            near_idx = np.where(near)[0] + t0
            splits = np.where(np.diff(near_idx) > MIN_GAP)[0]
            run_starts = np.concatenate(([0], splits + 1))
            run_ends = np.concatenate((splits, [len(near_idx) - 1]))
            touch_n = 0
            prev_touch_bar = None
            for rs, re in zip(run_starts, run_ends):
                run_bars = near_idx[rs:re + 1]
                local_dist = np.minimum(np.abs(low[run_bars] - px), np.abs(high[run_bars] - px))
                rep_bar = int(run_bars[np.argmin(local_dist)])
                touch_n += 1
                event_id_ctr += 1
                all_events.append({
                    "event_id": f"EVT_{event_id_ctr}",
                    "timestamp": str(ts_arr[rep_bar]),
                    "bar_end_ts_ns": int(bar_end_ts_ns[rep_bar]) if np.isfinite(bar_end_ts_ns[rep_bar]) else None,
                    "bar_t": rep_bar,
                    "close": float(close[rep_bar]), "high": float(high[rep_bar]), "low": float(low[rep_bar]),
                    "level_id": lv["level_id"], "level_price": px, "level_type": lv["level_type"],
                    "dashboard_level_source": "volume_profile_levels_fixed_window", "lookback_window": window_label,
                    "distance_ticks": float(round(abs(close[rep_bar] - px) / sc.TICK_SIZE, 2)),
                    "level_age_bars": rep_bar - t0,
                    "level_age_bucket": "age_0_500",  # daily-snapshot levels - "age" is intra-day only, never aged S/R memory
                    "touch_number_for_this_level": touch_n,
                    "bars_since_previous_touch": (rep_bar - prev_touch_bar) if prev_touch_bar is not None else None,
                    "approach_side": "from_below" if close[max(0, rep_bar - 1)] < px else "from_above",
                    "session": sessions[rep_bar],
                    "volatility_regime": vol_regime[rep_bar],
                    "vpin_regime": vpin_regime[rep_bar],
                    "liquidity_cost_regime": "NOT_AVAILABLE",
                    "touch_count_at_event_past_only": None,
                })
                prev_touch_bar = rep_bar

    events = pd.DataFrame(all_events)
    out_path = sc.OUT_DIR / "dashboard_sr_retouch_events.parquet"
    events.to_parquet(out_path, index=False)
    sc.log(f"  wrote {out_path} ({len(events)} total events)")

    diag = events.groupby(["lookback_window", "level_type"]).size().reset_index(name="n_events")
    diag_path = sc.OUT_DIR / "dashboard_sr_retouch_event_diagnostics.csv"
    diag.to_csv(diag_path, index=False)
    sc.log(f"  wrote {diag_path}")
    sc.log("02: done.")


if __name__ == "__main__":
    main()
