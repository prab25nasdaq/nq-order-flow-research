"""
05_sr_thickness_failure.py - Part E: support/resistance thickness failure
use-case.

Reuses, read-only, the prior Level Mechanics Atlas's already-validated
level_touch_events.parquet / level_touch_mfe_mae.parquet /
level_behavior_labels.parquet (21,528 touch events, native + rolling-
de-leaked + prior-session levels, 8 NQU6 days - one more day than this
Atlas's own thickness panel covers since 2026-06-14 has no level-candle
cache; events on that day simply get NaN thickness fields, never dropped
silently) rather than re-deriving touch detection / MFE-MAE / behavior
classification from scratch.

level_state_after_touch is DERIVED from the prior Atlas's own
behavior_label x this event's side_of_approach - not a new classifier:
  FROM_ABOVE (support test): REJECTION->SUPPORT_HELD,
    BREAKOUT_ACCEPTANCE->SUPPORT_FAILED, FAKE_BREAKOUT_SWEEP->
    SUPPORT_FAKE_BREAKDOWN_RECLAIMED, ABSORPTION->SUPPORT_ABSORBED,
    NO_REACTION->NO_REACTION
  FROM_BELOW (resistance test): mirrored (RESISTANCE_*)
  AT_LEVEL: behavior_label kept as-is (ambiguous side)

READ-ONLY. Writes only support_resistance_thickness_failure_analysis.csv
and level_state_thickness_blocker_report.csv under this engine's own
outputs/.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import thickness_common as tc

cfg = tc.load_config()
TICK = tc.TICK_SIZE
NEAR_TICKS_K = tc.NEAR_TICKS_K
BAND_LO_TICKS, BAND_HI_TICKS = 4, 20  # "below/above level" band: 4..20 ticks away, same side


def derive_level_state(side_of_approach: str, behavior_label: str) -> str:
    if side_of_approach == "FROM_ABOVE":
        mapping = {"REJECTION": "SUPPORT_HELD", "BREAKOUT_ACCEPTANCE": "SUPPORT_FAILED",
                  "FAKE_BREAKOUT_SWEEP": "SUPPORT_FAKE_BREAKDOWN_RECLAIMED",
                  "ABSORPTION": "SUPPORT_ABSORBED", "NO_REACTION": "NO_REACTION"}
    elif side_of_approach == "FROM_BELOW":
        mapping = {"REJECTION": "RESISTANCE_HELD", "BREAKOUT_ACCEPTANCE": "RESISTANCE_FAILED",
                  "FAKE_BREAKOUT_SWEEP": "RESISTANCE_FAKE_BREAKOUT_REJECTED",
                  "ABSORPTION": "RESISTANCE_ABSORBED", "NO_REACTION": "NO_REACTION"}
    else:
        return f"AT_LEVEL_{behavior_label}"
    return mapping.get(behavior_label, behavior_label)


def build_thickness_lookup(thickness: pd.DataFrame):
    """bar_end_ts_ns -> (price_level array, thickness_percentile array) for fast per-event band lookups."""
    lookup = {}
    for ts, grp in thickness.groupby("bar_end_ts_ns"):
        lookup[ts] = (grp["price_level"].to_numpy(), grp["thickness_percentile"].to_numpy())
    return lookup


def band_mean_thickness(lookup, bar_ts, level_price, lo_ticks, hi_ticks, direction):
    arrs = lookup.get(bar_ts)
    if arrs is None:
        return np.nan
    prices, pcts = arrs
    if direction == "below":
        lo, hi = level_price - hi_ticks * TICK, level_price - lo_ticks * TICK
    else:
        lo, hi = level_price + lo_ticks * TICK, level_price + hi_ticks * TICK
    mask = (prices >= lo) & (prices <= hi)
    if not mask.any():
        return np.nan
    return float(np.nanmean(pcts[mask]))


def at_level_thickness(lookup, bar_ts, level_price, tol_ticks=2):
    arrs = lookup.get(bar_ts)
    if arrs is None:
        return np.nan
    prices, pcts = arrs
    mask = np.abs(prices - level_price) <= tol_ticks * TICK
    if not mask.any():
        return np.nan
    return float(np.nanmean(pcts[mask]))


def main():
    tc.log("05: building support/resistance thickness failure analysis...")
    touches = tc.load_level_touch_events()
    behavior = tc.load_level_behavior_labels()
    mfe_mae = pd.read_parquet(
        Path("/home/prabh/OFI_Production/research_engines/"
            "nasdaq_full_book_level_mechanics_atlas_v1_20260623T225748Z/outputs/level_touch_mfe_mae.parquet"))

    ev = touches.merge(behavior[["event_id", "behavior_label", "reason"]], on="event_id", how="left")
    ev = ev.merge(mfe_mae, on="event_id", how="left")
    ev["level_state_after_touch"] = [
        derive_level_state(s, b) for s, b in zip(ev["side_of_approach"], ev["behavior_label"])
    ]
    # "trade with the level" framing (reject hypothesis = betting the level holds) -
    # same convention as v4's own realized_mfe_side_adjusted
    ev["MFE_after_touch"] = ev["reject_mfe_40"]
    ev["MAE_after_touch"] = ev["reject_mae_40"]

    tc.log("  loading thickness panel + bar-level mechanics for the price-band lookups...")
    thickness = pd.read_parquet(tc.OUT_DIR / "book_flow_thickness_panel.parquet")
    lookup = build_thickness_lookup(thickness)

    pv = pd.read_parquet(tc.OUT_DIR / "price_velocity_panel.parquet")[
        ["bar_end_ts_ns", "price_velocity", "directional_velocity", "abs_flow", "signed_flow"]]
    raw_levels = tc.load_level_candles(depth=cfg["scope"]["depth"])
    bar_agg = tc.aggregate_book_flow_bars(raw_levels)[
        ["bar_end_ts_ns", "bid_add", "bid_pull", "ask_add", "ask_pull"]]
    ev = ev.merge(pv, on="bar_end_ts_ns", how="left").merge(bar_agg, on="bar_end_ts_ns", how="left")
    ev = ev.rename(columns={"price_velocity": "velocity_into_level",
                           "abs_flow": "flow_into_level", "signed_flow": "signed_flow_into_level"})
    ev["bid_add_vs_bid_pull"] = ev["bid_add"] - ev["bid_pull"]
    ev["ask_add_vs_ask_pull"] = ev["ask_add"] - ev["ask_pull"]

    tc.log("  computing thickness_at_level / thickness_below_level / thickness_above_level per event...")
    thickness_at, thickness_below, thickness_above = [], [], []
    for bar_ts, lvl_price in zip(ev["bar_end_ts_ns"], ev["level_price"]):
        thickness_at.append(at_level_thickness(lookup, bar_ts, lvl_price))
        thickness_below.append(band_mean_thickness(lookup, bar_ts, lvl_price, BAND_LO_TICKS, BAND_HI_TICKS, "below"))
        thickness_above.append(band_mean_thickness(lookup, bar_ts, lvl_price, BAND_LO_TICKS, BAND_HI_TICKS, "above"))
    ev["thickness_at_level"] = thickness_at
    ev["thickness_below_level"] = thickness_below
    ev["thickness_above_level"] = thickness_above
    ev["is_fat_at_level"] = ev["thickness_at_level"] >= cfg["thickness"]["fat_pct_min"]
    ev["is_thin_at_level"] = ev["thickness_at_level"] <= cfg["thickness"]["thin_pct_max"]
    ev["is_thin_below_level"] = ev["thickness_below_level"] <= cfg["thickness"]["thin_pct_max"]
    ev["is_thin_above_level"] = ev["thickness_above_level"] <= cfg["thickness"]["thin_pct_max"]

    out_cols = [
        "event_id", "rithmic_date_str", "bar_end_ts_ns", "level_type", "level_source", "level_price",
        "close", "distance_ticks", "side_of_approach", "touched_intrabar", "session", "vol_state", "vpin_state",
        "behavior_label", "level_state_after_touch",
        "thickness_at_level", "thickness_below_level", "thickness_above_level",
        "is_fat_at_level", "is_thin_at_level", "is_thin_below_level", "is_thin_above_level",
        "velocity_into_level", "directional_velocity", "flow_into_level", "signed_flow_into_level",
        "bid_add", "bid_pull", "ask_add", "ask_pull", "bid_add_vs_bid_pull", "ask_add_vs_ask_pull",
        "MFE_after_touch", "MAE_after_touch",
    ]
    sr_analysis = ev[out_cols]
    out1 = tc.OUT_DIR / "support_resistance_thickness_failure_analysis.csv"
    sr_analysis.to_csv(out1, index=False)
    tc.log(f"  wrote {out1} ({len(sr_analysis)} rows, {sr_analysis['thickness_at_level'].notna().sum()} with thickness data)")

    # blocker report: directly test the 5 hypotheses from the task spec.
    # PRIMARY population = genuine intrabar touches only (touched_intrabar==
    # True) - the broader "near but never actually touched" population
    # (included in the full CSV above for transparency) dilutes the S/R
    # hypotheses with incidental near-misses that were never a real test of
    # the level, so it is not used for these specific hypothesis numbers.
    has_thickness = sr_analysis.dropna(subset=["thickness_at_level"])
    touched = has_thickness[has_thickness["touched_intrabar"]]
    support_ev = touched[touched["side_of_approach"] == "FROM_ABOVE"]
    resistance_ev = touched[touched["side_of_approach"] == "FROM_BELOW"]

    def rate(df, mask):
        return float(mask.mean()) if len(df) else None

    rows = []
    # H1: support works when fat zone absorbs selling and price reclaims
    fat_support = support_ev[support_ev["is_fat_at_level"]]
    rows.append(dict(hypothesis="H1_support_works_fat_zone_absorbs_selling", n=len(fat_support),
                     rate_metric_name="pct_SUPPORT_HELD",
                     rate_metric_value=rate(fat_support, fat_support["level_state_after_touch"] == "SUPPORT_HELD"),
                     pct_bid_add_gt_pull=rate(fat_support, fat_support["bid_add_vs_bid_pull"] > 0),
                     mean_MFE=float(fat_support["MFE_after_touch"].mean()) if len(fat_support) else None,
                     mean_MAE=float(fat_support["MAE_after_touch"].mean()) if len(fat_support) else None))
    # H2: support fails when fat zone is consumed and bid liquidity pulls
    fat_support_failed = fat_support[fat_support["level_state_after_touch"] == "SUPPORT_FAILED"]
    rows.append(dict(hypothesis="H2_support_fails_fat_zone_consumed_bid_pulls", n=len(fat_support_failed),
                     rate_metric_name="pct_bid_pull_gt_bid_add_when_failed",
                     rate_metric_value=rate(fat_support_failed, fat_support_failed["bid_add_vs_bid_pull"] < 0),
                     pct_bid_add_gt_pull=rate(fat_support_failed, fat_support_failed["bid_add_vs_bid_pull"] > 0),
                     mean_MFE=float(fat_support_failed["MFE_after_touch"].mean()) if len(fat_support_failed) else None,
                     mean_MAE=float(fat_support_failed["MAE_after_touch"].mean()) if len(fat_support_failed) else None))
    # H3: thin zone below support leads to faster breakdown
    thin_below = support_ev[support_ev["is_thin_below_level"]]
    not_thin_below = support_ev[~support_ev["is_thin_below_level"].fillna(False)]
    rows.append(dict(hypothesis="H3a_thin_below_support_faster_breakdown", n=len(thin_below),
                     rate_metric_name="pct_SUPPORT_FAILED",
                     rate_metric_value=rate(thin_below, thin_below["level_state_after_touch"] == "SUPPORT_FAILED"),
                     pct_bid_add_gt_pull=None,
                     mean_MFE=float(thin_below["MFE_after_touch"].mean()) if len(thin_below) else None,
                     mean_MAE=float(thin_below["MAE_after_touch"].mean()) if len(thin_below) else None))
    rows.append(dict(hypothesis="H3b_NOT_thin_below_support_comparison", n=len(not_thin_below),
                     rate_metric_name="pct_SUPPORT_FAILED",
                     rate_metric_value=rate(not_thin_below, not_thin_below["level_state_after_touch"] == "SUPPORT_FAILED"),
                     pct_bid_add_gt_pull=None,
                     mean_MFE=float(not_thin_below["MFE_after_touch"].mean()) if len(not_thin_below) else None,
                     mean_MAE=float(not_thin_below["MAE_after_touch"].mean()) if len(not_thin_below) else None))
    # H4: resistance works when fat zone absorbs buying and price rejects
    fat_resistance = resistance_ev[resistance_ev["is_fat_at_level"]]
    rows.append(dict(hypothesis="H4_resistance_works_fat_zone_absorbs_buying", n=len(fat_resistance),
                     rate_metric_name="pct_RESISTANCE_HELD",
                     rate_metric_value=rate(fat_resistance, fat_resistance["level_state_after_touch"] == "RESISTANCE_HELD"),
                     pct_bid_add_gt_pull=rate(fat_resistance, fat_resistance["ask_add_vs_ask_pull"] > 0),
                     mean_MFE=float(fat_resistance["MFE_after_touch"].mean()) if len(fat_resistance) else None,
                     mean_MAE=float(fat_resistance["MAE_after_touch"].mean()) if len(fat_resistance) else None))
    # H5: resistance fails when offers pull and price accepts above
    resistance_failed = resistance_ev[resistance_ev["level_state_after_touch"] == "RESISTANCE_FAILED"]
    rows.append(dict(hypothesis="H5_resistance_fails_offers_pull_price_accepts_above", n=len(resistance_failed),
                     rate_metric_name="pct_ask_pull_gt_ask_add_when_failed",
                     rate_metric_value=rate(resistance_failed, resistance_failed["ask_add_vs_ask_pull"] < 0),
                     pct_bid_add_gt_pull=rate(resistance_failed, resistance_failed["ask_add_vs_ask_pull"] > 0),
                     mean_MFE=float(resistance_failed["MFE_after_touch"].mean()) if len(resistance_failed) else None,
                     mean_MAE=float(resistance_failed["MAE_after_touch"].mean()) if len(resistance_failed) else None))

    blocker_report = pd.DataFrame(rows)
    blocker_report.insert(1, "population", "touched_intrabar==True (genuine touches, not near-misses)")
    out2 = tc.OUT_DIR / "level_state_thickness_blocker_report.csv"
    blocker_report.to_csv(out2, index=False)
    tc.log(f"  wrote {out2}")

    print(blocker_report.to_string(index=False))
    tc.log("05 complete.")
    return sr_analysis, blocker_report


if __name__ == "__main__":
    main()
