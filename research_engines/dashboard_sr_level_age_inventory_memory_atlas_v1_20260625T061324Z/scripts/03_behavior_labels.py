"""
03_behavior_labels.py - Part D: behavior labels + MFE/MAE after each
dashboard S/R retouch event. Future path is used ONLY as the label/target,
never as a feature (Part H's feature panel only uses past-only inputs).

LEVEL-CENTERED, side-agnostic convention (documented, a-priori, not fit to
data): any level acts as SUPPORT when tested from above, RESISTANCE when
tested from below - independent of how swing detection originally classified
it. This matches standard price-action convention and lets a level's role
flip naturally (e.g. an old swing-resistance level being retested as support
after a breakout) without needing a separate label.

  support_test = approach_side == 'from_above'
  resistance_test = approach_side == 'from_below'

  MFE_h (favorable) / MAE_h (adverse), both measured from level_price:
    support_test:    MFE_h = max(high[t+1:t+h]) - level_price
                      MAE_h = level_price - min(low[t+1:t+h])
    resistance_test:  MFE_h = level_price - min(low[t+1:t+h])
                      MAE_h = max(high[t+1:t+h]) - level_price
  final_return_h = signed favorable-direction return at close[t+h]

Behavior labels (a-priori thresholds from atlas_config.yaml, NOT fit to
data): reject_bar = first bar with favorable excursion >= hold_reject_move
(4 ticks); break_bar = first bar with adverse excursion >= fail_break_move
(4 ticks); reclaim = a favorable excursion >= hold_reject_move occurring
within fake_breakout_reclaim_bars (10) of break_bar.
  - reject before (or no) break -> SUPPORT_HELD / RESISTANCE_HELD
  - break, then reclaimed quickly -> FAKE_BREAKOUT_SWEEP
  - break, not reclaimed, strong continuation (adverse_20 >= 2x threshold) -> BREAKOUT_ACCEPTANCE
  - break, not reclaimed, no strong continuation -> SUPPORT_FAILED / RESISTANCE_FAILED
  - neither ever triggers within the horizon -> NO_REACTION

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
TICK = sc.TICK_SIZE
HOLD_TICKS = cfg["behavior_labels"]["hold_reject_move_ticks"]
BREAK_TICKS = cfg["behavior_labels"]["fail_break_move_ticks"]
RECLAIM_BARS = cfg["behavior_labels"]["fake_breakout_reclaim_bars"]
HORIZONS = cfg["forward_horizons_bars"]
MAX_H = max(HORIZONS)
HOLD_MOVE = HOLD_TICKS * TICK
BREAK_MOVE = BREAK_TICKS * TICK


def main():
    sc.log("03: loading master + retouch events...")
    df = sc.load_continuous_master()
    n = len(df)
    high = pd.to_numeric(df["px_high"], errors="coerce").to_numpy()
    low = pd.to_numeric(df["px_low"], errors="coerce").to_numpy()
    close = pd.to_numeric(df["px_close"], errors="coerce").to_numpy()

    events = pd.read_parquet(sc.OUT_DIR / "dashboard_sr_retouch_events.parquet")
    sc.log(f"  {len(events)} events")

    bar_t = events["bar_t"].to_numpy()
    level_price = events["level_price"].to_numpy()
    support_test = (events["approach_side"] == "from_above").to_numpy()

    valid = bar_t + MAX_H < n
    sc.log(f"  {valid.sum()}/{len(events)} events have a full {MAX_H}-bar forward path available")

    out_cols = {h: {"mfe": np.full(len(events), np.nan), "mae": np.full(len(events), np.nan),
                     "final_return": np.full(len(events), np.nan)} for h in HORIZONS}
    labels = np.array(["NO_REACTION"] * len(events), dtype=object)
    time_to_rejection = np.full(len(events), np.nan)
    time_to_break = np.full(len(events), np.nan)
    time_to_reclaim = np.full(len(events), np.nan)
    max_adverse_before_reclaim = np.full(len(events), np.nan)
    max_favorable_before_failure = np.full(len(events), np.nan)

    for i in range(len(events)):
        t = int(bar_t[i])
        if t + 1 >= n:
            continue
        h_end = min(t + MAX_H, n - 1)
        fwd_high = high[t + 1:h_end + 1]
        fwd_low = low[t + 1:h_end + 1]
        fwd_close = close[t + 1:h_end + 1]
        if len(fwd_high) == 0:
            continue
        lp = level_price[i]
        is_supp = bool(support_test[i])

        if is_supp:
            fav_path = fwd_high - lp       # favorable = up
            adv_path = lp - fwd_low        # adverse = down through level
        else:
            fav_path = lp - fwd_low        # favorable = down
            adv_path = fwd_high - lp       # adverse = up through level

        for h in HORIZONS:
            hh = min(h, len(fwd_high))
            if hh == 0:
                continue
            out_cols[h]["mfe"][i] = float(np.max(fav_path[:hh]))
            out_cols[h]["mae"][i] = float(np.max(adv_path[:hh]))
            fr = (fwd_close[hh - 1] - lp) if is_supp else (lp - fwd_close[hh - 1])
            out_cols[h]["final_return"][i] = float(fr)

        reject_idx = np.where(fav_path >= HOLD_MOVE)[0]
        break_idx = np.where(adv_path >= BREAK_MOVE)[0]
        reject_bar = int(reject_idx[0]) if len(reject_idx) else None
        break_bar = int(break_idx[0]) if len(break_idx) else None

        if reject_bar is not None:
            time_to_rejection[i] = reject_bar + 1
        if break_bar is not None:
            time_to_break[i] = break_bar + 1

        if break_bar is None:
            if reject_bar is not None:
                labels[i] = "SUPPORT_HELD" if is_supp else "RESISTANCE_HELD"
            else:
                labels[i] = "NO_REACTION"
            continue

        if reject_bar is not None and reject_bar <= break_bar:
            labels[i] = "SUPPORT_HELD" if is_supp else "RESISTANCE_HELD"
            max_adverse_before_reclaim[i] = float(np.max(adv_path[:reject_bar + 1])) if reject_bar >= 0 else np.nan
            continue

        # break happened (and happened before/without any reject) - check for a quick reclaim
        reclaim_window_end = min(break_bar + RECLAIM_BARS + 1, len(fav_path))
        reclaim_idx = np.where(fav_path[break_bar + 1:reclaim_window_end] >= HOLD_MOVE)[0]
        if len(reclaim_idx):
            reclaim_bar = break_bar + 1 + int(reclaim_idx[0])
            labels[i] = "FAKE_BREAKOUT_SWEEP"
            time_to_reclaim[i] = reclaim_bar + 1
            max_adverse_before_reclaim[i] = float(np.max(adv_path[:reclaim_bar + 1]))
            continue

        h20 = min(20, len(adv_path))
        strong_continuation = h20 > 0 and np.max(adv_path[:h20]) >= 2 * BREAK_MOVE
        labels[i] = "BREAKOUT_ACCEPTANCE" if strong_continuation else ("SUPPORT_FAILED" if is_supp else "RESISTANCE_FAILED")
        max_favorable_before_failure[i] = float(np.max(fav_path[:break_bar + 1])) if break_bar >= 0 else np.nan

    behavior = events[["event_id", "level_id", "level_type", "dashboard_level_source", "lookback_window",
                        "level_age_bars", "level_age_bucket", "touch_number_for_this_level",
                        "approach_side", "session"]].copy()
    behavior["behavior_label"] = labels
    behavior["time_to_rejection"] = time_to_rejection
    behavior["time_to_break"] = time_to_break
    behavior["time_to_reclaim"] = time_to_reclaim
    behavior["maximum_adverse_before_reclaim"] = max_adverse_before_reclaim
    behavior["maximum_favorable_before_failure"] = max_favorable_before_failure
    behavior["has_full_forward_path"] = valid

    mfe_mae = events[["event_id", "level_id", "level_type", "dashboard_level_source", "lookback_window",
                       "level_age_bars", "level_age_bucket", "touch_number_for_this_level"]].copy()
    for h in HORIZONS:
        mfe_mae[f"MFE_{h}"] = out_cols[h]["mfe"]
        mfe_mae[f"MAE_{h}"] = out_cols[h]["mae"]
        mfe_mae[f"final_return_{h}"] = out_cols[h]["final_return"]
        with np.errstate(invalid="ignore", divide="ignore"):
            mfe_mae[f"MFE_MAE_ratio_{h}"] = out_cols[h]["mfe"] / np.where(out_cols[h]["mae"] == 0, np.nan, out_cols[h]["mae"])

    beh_path = sc.OUT_DIR / "dashboard_sr_behavior_labels.parquet"
    behavior.to_parquet(beh_path, index=False)
    sc.log(f"  wrote {beh_path} ({len(behavior)} rows)")

    mfe_path = sc.OUT_DIR / "dashboard_sr_mfe_mae_by_horizon.parquet"
    mfe_mae.to_parquet(mfe_path, index=False)
    sc.log(f"  wrote {mfe_path} ({len(mfe_mae)} rows)")

    sc.log("  label distribution:")
    sc.log(str(behavior["behavior_label"].value_counts().to_dict()))
    sc.log("03: done.")


if __name__ == "__main__":
    main()
