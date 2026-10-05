"""
03_auction_friction.py - Part C: auction friction metrics.

STRICT RULE (per task spec, enforced here): friction scores are built ONLY
from CONTEMPORANEOUS-OR-PAST information for bar t - this bar's own
thickness_percentile (same-bar realized order flow, not a future return),
this bar's own price_velocity/directional_velocity (computed from
close[t]-close[t-1], backward-looking), and this bar's own bid/ask
add/pull mechanics. The forward-looking fields from Part B
(number_of_revisits, rejection_count, breakout_count, bars_to_move_*,
MFE/MAE) are NEVER read by this script - they are reserved as
labels/analysis to test AGAINST these friction scores in Parts D/E/F, never
as inputs to the scores themselves. This is the literal implementation of
"do not use future returns inside feature definitions."

READ-ONLY. Writes only auction_friction_features.parquet and
auction_friction_formula_catalog.csv under this engine's own outputs/.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import thickness_common as tc

cfg = tc.load_config()
fric_cfg = cfg["friction"]


def main():
    tc.log("03: building auction friction features (contemporaneous/past-only inputs)...")
    panel = pd.read_parquet(tc.OUT_DIR / "price_velocity_panel.parquet")

    # velocity percentile + abs_flow percentile, SAME convention as
    # thickness_percentile (per session-day context, nonzero population) -
    # both computed from bar t's OWN realized values only.
    vel_pct, _, _ = tc.percentile_rank_in_context(
        panel["price_velocity"].fillna(0).to_numpy(), panel["rithmic_date_str"].to_numpy(), restrict_nonzero=True)
    flow_pct, _, _ = tc.percentile_rank_in_context(
        panel["abs_flow"].fillna(0).to_numpy(), panel["rithmic_date_str"].to_numpy(), restrict_nonzero=True)
    dirvel_abs_pct, _, _ = tc.percentile_rank_in_context(
        panel["directional_velocity"].abs().fillna(0).to_numpy(), panel["rithmic_date_str"].to_numpy(), restrict_nonzero=True)

    feat = panel[["bar_end_ts_ns", "rithmic_date_str", "bar_idx_in_day", "close",
                 "thickness_percentile", "is_thin_zone", "is_medium_zone", "is_fat_zone",
                 "price_velocity", "directional_velocity", "abs_flow", "signed_flow"]].copy()
    feat["velocity_percentile"] = vel_pct
    feat["abs_flow_percentile"] = flow_pct
    feat["directional_velocity_abs_percentile"] = dirvel_abs_pct

    thick_s = feat["thickness_percentile"].fillna(0) / 100.0
    vel_s = feat["velocity_percentile"].fillna(0) / 100.0
    flow_s = feat["abs_flow_percentile"].fillna(0) / 100.0
    dirvel_s = feat["directional_velocity_abs_percentile"].fillna(0) / 100.0

    # need book_imbalance/bid_add/bid_pull/ask_add/ask_pull - join from bar-aggregated book flow
    bar_agg_cols = pd.read_parquet(tc.OUT_DIR / "price_velocity_panel.parquet")  # already has abs_flow/signed_flow
    raw_levels = tc.load_level_candles(depth=cfg["scope"]["depth"])
    bar_agg = tc.aggregate_book_flow_bars(raw_levels)
    flow_cols = bar_agg[["bar_end_ts_ns", "bid_add", "bid_pull", "ask_add", "ask_pull", "book_imbalance"]]
    feat = feat.merge(flow_cols, on="bar_end_ts_ns", how="left")

    eps = 1e-9
    feat["auction_friction_score"] = thick_s * (1.0 - vel_s)
    feat["low_friction_travel_score"] = vel_s * (1.0 - thick_s)
    feat["absorption_score"] = flow_s * (1.0 - vel_s)
    # liquidity_vacuum_score: thin + high velocity, using ONLY contemporaneous
    # thickness/velocity (NOT the forward-looking revisit count - that
    # relationship is TESTED, not assumed, in Part D)
    feat["liquidity_vacuum_score"] = (1.0 - thick_s) * vel_s
    feat["two_way_exchange_score"] = thick_s * (1.0 - feat["book_imbalance"].abs().fillna(1.0).clip(0, 1)) * (1.0 - dirvel_s)
    feat["replenishment_score"] = (feat["bid_add"].fillna(0) + feat["ask_add"].fillna(0)) / (
        feat["bid_pull"].fillna(0) + feat["ask_pull"].fillna(0) + eps)
    feat["imbalance_score"] = feat["book_imbalance"]

    out_path = tc.OUT_DIR / "auction_friction_features.parquet"
    feat.to_parquet(out_path, index=False)
    tc.log(f"  wrote {out_path} ({len(feat)} rows)")

    catalog_rows = [
        dict(field="velocity_percentile", formula="percentile rank of price_velocity within session-day "
             "context (nonzero population) - same method as thickness_percentile, bar t's OWN realized return only"),
        dict(field="abs_flow_percentile", formula="percentile rank of bar-aggregated abs_flow within session-day context"),
        dict(field="auction_friction_score", formula="thickness_pct_scaled * (1 - velocity_pct_scaled)",
             notes="high flow/thickness with low price movement = high friction (per task spec)"),
        dict(field="low_friction_travel_score", formula="velocity_pct_scaled * (1 - thickness_pct_scaled)",
             notes="high price movement with low thickness/flow = low friction (per task spec)"),
        dict(field="absorption_score", formula="abs_flow_pct_scaled * (1 - velocity_pct_scaled)",
             notes="high aggressive/signed flow but limited price progress (per task spec)"),
        dict(field="liquidity_vacuum_score", formula="(1 - thickness_pct_scaled) * velocity_pct_scaled",
             notes="thin thickness + high velocity (per task spec). The spec's third clause 'low revisits' is "
                   "DELIBERATELY EXCLUDED from this formula since number_of_revisits is a FORWARD-looking count "
                   "(Part B) - including it here would violate 'do not use future returns inside feature "
                   "definitions'. Whether vacuum-score bars actually show low revisits afterward is TESTED in "
                   "Part D as an outcome, not assumed as an input."),
        dict(field="two_way_exchange_score", formula="thickness_pct_scaled * (1 - |book_imbalance|) * "
             "(1 - |directional_velocity|_pct_scaled)",
             notes="fat thickness + balanced bid/ask flow + low net directional progress (per task spec)"),
        dict(field="replenishment_score", formula="(bid_add + ask_add) / (bid_pull + ask_pull + eps)",
             notes="liquidity added vs pulled at this bar, both sides combined"),
        dict(field="imbalance_score", formula="book_imbalance = (bid_add - ask_add) / (bid_add + ask_add)",
             notes="signed, -1..+1, same formula as thickness_common.aggregate_book_flow_bars"),
        dict(field="ALL SCORES", formula="N/A", notes="LEAKAGE POLICY: every score above is a function of bar "
             "t's own thickness_percentile / price_velocity / directional_velocity / bid_add / bid_pull / "
             "ask_add / ask_pull / book_imbalance ONLY - none of Part B's forward-looking fields "
             "(number_of_revisits, rejection_count, breakout_count, bars_to_move_*_ticks, MFE_*, MAE_*, "
             "final_return_*) are read by this script."),
    ]
    cat_df = pd.DataFrame(catalog_rows)
    cat_path = tc.OUT_DIR / "auction_friction_formula_catalog.csv"
    cat_df.to_csv(cat_path, index=False)
    tc.log(f"  wrote {cat_path}")

    print(feat[["auction_friction_score", "low_friction_travel_score", "absorption_score",
              "liquidity_vacuum_score", "two_way_exchange_score", "replenishment_score",
              "imbalance_score"]].describe().to_string())
    tc.log("03 complete.")
    return feat


if __name__ == "__main__":
    main()
