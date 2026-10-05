"""
03_level_behavior_labels.py - Part C: classify level-touch outcome using
the FUTURE path only. These are LABELS (research targets), never used as
features anywhere else in this Atlas.

Path-based rules (priority order, side-aware via side_of_approach):
  "beyond" direction = the direction price would continue if the level
  failed to hold (FROM_ABOVE -> beyond=below the level; FROM_BELOW ->
  beyond=above the level). "reject" direction = the opposite (back toward
  where price came from).

  1. BREAKOUT_ACCEPTANCE: price closes beyond the level by >=
     breakout_acceptance_min_hold_ticks at the END of the 40-bar horizon
     (day-bounded) AND the max excursion in the beyond direction also
     clears that threshold (not just a last-bar fluke).
  2. FAKE_BREAKOUT_SWEEP: price DID cross beyond the level (by >=2 ticks)
     within fake_breakout_sweep_max_hold_bars of the touch, but the final
     close at the horizon reverted back to the original (reject) side.
  3. REJECTION: max excursion in the reject direction >=
     rejection_min_reversal_ticks (a real, meaningful reversal away from
     the level).
  4. ABSORPTION: above-average trade activity at the touch bar itself
     (abs_flow or trade_volume z-scored vs that day's own distribution)
     but price barely moved either direction (max(|beyond|,|reject|)
     excursion <= absorption_max_progress_ticks).
  5. NO_REACTION: none of the above - the touch produced no meaningful
     path response by any of these criteria.

AT_LEVEL approach events (no clear prior-bar direction) use the same rules
with "beyond"/"reject" simply mapped to whichever direction the eventual
path moves furthest (symmetric treatment, documented).

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atlas_common as ac

cfg = ac.load_config()
B = cfg["behavior_labels"]
HORIZON = 40


def classify_day(day_df: pd.DataFrame, day_events: pd.DataFrame) -> list:
    c = pd.to_numeric(day_df["px_close"], errors="coerce").to_numpy()
    h = pd.to_numeric(day_df["px_high"], errors="coerce").to_numpy()
    l = pd.to_numeric(day_df["px_low"], errors="coerce").to_numpy()
    vt = pd.to_numeric(day_df["vol_total"], errors="coerce").to_numpy()
    rng = np.maximum(h - l, ac.TICK_SIZE)
    flow_strength = vt / rng
    fs = pd.Series(flow_strength)
    flow_z = ((fs - fs.rolling(50, min_periods=10).mean()) / fs.rolling(50, min_periods=10).std()).to_numpy()
    n = len(day_df)

    rows = []
    for _, ev in day_events.iterrows():
        t = int(ev["bar_idx_in_day"])
        lp = float(ev["level_price"])
        side = ev["side_of_approach"]
        jmax = min(t + HORIZON + 1, n)
        if jmax <= t + 1 or not np.isfinite(c[t]):
            rows.append(dict(event_id=int(ev["event_id"]), behavior_label="NO_REACTION",
                            reason="insufficient_forward_bars", beyond_excursion_ticks=np.nan,
                            reject_excursion_ticks=np.nan, final_close_ticks_from_level=np.nan))
            continue
        seg_h = h[t + 1:jmax]; seg_l = l[t + 1:jmax]; seg_c = c[t + 1:jmax]
        final_close = c[jmax - 1]

        if side == "FROM_ABOVE":
            beyond_excursion = (lp - np.nanmin(seg_l)) / ac.TICK_SIZE
            reject_excursion = (np.nanmax(seg_h) - lp) / ac.TICK_SIZE
            final_beyond_ticks = (lp - final_close) / ac.TICK_SIZE
        elif side == "FROM_BELOW":
            beyond_excursion = (np.nanmax(seg_h) - lp) / ac.TICK_SIZE
            reject_excursion = (lp - np.nanmin(seg_l)) / ac.TICK_SIZE
            final_beyond_ticks = (final_close - lp) / ac.TICK_SIZE
        else:  # AT_LEVEL - symmetric: "beyond" = whichever direction moved furthest
            up_exc = (np.nanmax(seg_h) - lp) / ac.TICK_SIZE
            down_exc = (lp - np.nanmin(seg_l)) / ac.TICK_SIZE
            beyond_excursion = max(up_exc, down_exc)
            reject_excursion = min(up_exc, down_exc)
            final_beyond_ticks = abs(final_close - lp) / ac.TICK_SIZE

        # did price cross beyond (by >=fake_breakout_sweep_cross_ticks) within the sweep-check window?
        sweep_n = min(B["fake_breakout_sweep_max_hold_bars"], len(seg_h))
        cross_thr = B["fake_breakout_sweep_cross_ticks"]
        if side == "FROM_ABOVE":
            crossed_beyond_early = (lp - np.nanmin(seg_l[:sweep_n])) / ac.TICK_SIZE >= cross_thr if sweep_n else False
        elif side == "FROM_BELOW":
            crossed_beyond_early = (np.nanmax(seg_h[:sweep_n]) - lp) / ac.TICK_SIZE >= cross_thr if sweep_n else False
        else:
            crossed_beyond_early = beyond_excursion >= cross_thr

        # absorption is a NEAR-TERM phenomenon (aggressive flow hits the level and
        # makes no immediate progress in the bars right after touch) - checking
        # "low progress" over the FULL 40-bar horizon is far too strict (some
        # movement over 40 volume-bars is essentially guaranteed either way), so
        # use a short near-term window for this specific check only.
        near_n = min(B["absorption_near_term_bars"], len(seg_h))
        if near_n:
            near_up = (np.nanmax(seg_h[:near_n]) - lp) / ac.TICK_SIZE
            near_down = (lp - np.nanmin(seg_l[:near_n])) / ac.TICK_SIZE
            near_term_max_progress = max(near_up, near_down)
        else:
            near_term_max_progress = np.nan

        label, reason = None, None
        if (final_beyond_ticks >= B["breakout_acceptance_min_hold_ticks"] and
                beyond_excursion >= B["breakout_acceptance_min_hold_ticks"]):
            label, reason = "BREAKOUT_ACCEPTANCE", "final_close_and_max_excursion_both_beyond_threshold"
        elif crossed_beyond_early and final_beyond_ticks < 0:
            label, reason = "FAKE_BREAKOUT_SWEEP", "crossed_beyond_early_then_reverted_to_original_side"
        elif reject_excursion >= B["rejection_min_reversal_ticks"]:
            label, reason = "REJECTION", "max_reject_direction_excursion_cleared_threshold"
        elif (np.isfinite(flow_z[t]) and flow_z[t] >= B["absorption_min_flow_zscore"] and
              np.isfinite(near_term_max_progress) and near_term_max_progress <= B["absorption_max_progress_ticks"]):
            label, reason = "ABSORPTION", "high_flow_at_touch_low_near_term_price_progress"
        else:
            label, reason = "NO_REACTION", "no_rule_triggered"

        rows.append(dict(event_id=int(ev["event_id"]), behavior_label=label, reason=reason,
                         beyond_excursion_ticks=float(beyond_excursion), reject_excursion_ticks=float(reject_excursion),
                         final_close_ticks_from_level=float(final_beyond_ticks),
                         touch_bar_flow_z=float(flow_z[t]) if np.isfinite(flow_z[t]) else np.nan))
    return rows


def main():
    ac.log("03: classifying level-touch behavior (future-path labels only)...")
    ev = pd.read_parquet(ac.OUT_DIR / "level_touch_events.parquet")
    nqu6 = ac.load_nqu6_master()
    nqu6_scoped = nqu6[nqu6["rithmic_date_str"].isin(cfg["scope"]["days"])].reset_index(drop=True)

    all_rows = []
    for d in sorted(ev["rithmic_date_str"].unique()):
        day_df = nqu6_scoped[nqu6_scoped["rithmic_date_str"] == d].reset_index(drop=True)
        day_events = ev[ev["rithmic_date_str"] == d]
        all_rows.extend(classify_day(day_df, day_events))

    lbl = pd.DataFrame(all_rows)
    lbl.to_parquet(ac.OUT_DIR / "level_behavior_labels.parquet", index=False)

    diag = lbl["behavior_label"].value_counts(normalize=False).rename_axis("behavior_label").reset_index(name="n")
    diag["pct"] = diag["n"] / diag["n"].sum()
    diag.to_csv(ac.OUT_DIR / "level_behavior_label_diagnostics.csv", index=False)

    print(diag.to_string(index=False))
    ac.log(f"  labeled {len(lbl)} events")
    ac.log("03 complete.")
    return lbl, diag


if __name__ == "__main__":
    main()
