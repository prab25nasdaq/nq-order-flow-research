"""
01_thickness_panel.py - Part A: price-axis book-flow thickness panel.

For every (bar, price_level) CLOSED-bar cell in the Book Flow level-candle
cache (NQU6, top10 depth, the 7 days with level-candle coverage), compute
the raw per-price order-flow fields plus a PERCENTILE-SCALED thickness
measure - exactly matching the live Book Flow chart's own scale_mode==
"percentile" semantics (book_flow_chart_v3.py: denom = np.nanpercentile(
abs_flow, 95) over the visible cell population) but computed per cell
(percentile RANK, not just the 95th-pct normalizer) within a session-day
context, restricted to cells with genuine activity (abs_flow > 0) - see
thickness_common.percentile_rank_in_context for the documented rationale.

READ-ONLY. Writes only book_flow_thickness_panel.parquet and
thickness_formula_catalog.csv under this engine's own outputs/.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import thickness_common as tc

cfg = tc.load_config()
DEPTH = cfg["scope"]["depth"]
THIN_MAX = cfg["thickness"]["thin_pct_max"]
FAT_MIN = cfg["thickness"]["fat_pct_min"]


def main():
    tc.log(f"01: loading Book Flow level candles (NQU6, top{DEPTH})...")
    raw = tc.load_level_candles(depth=DEPTH)
    tc.log(f"  loaded {len(raw)} raw rows across {raw['session_date'].nunique()} days")
    closed = raw[raw["bar_state"] == "CLOSED"].copy().reset_index(drop=True)
    tc.log(f"  {len(closed)} CLOSED-bar cells (training/analysis-safe; forming bars excluded)")

    panel = pd.DataFrame({
        "session_date": closed["session_date"],
        "bar_idx": closed["bar_idx"],
        "bar_end_ts_ns": closed["bar_end_ts_ns"].astype("int64"),
        "timestamp_utc": closed["timestamp_utc"],
        "price_level": closed["price_level"].astype(float),
        "close": closed["close_price"].astype(float),
        "mid_price": closed["mid_price"].astype(float),
        "visible_context_id": closed["session_date"],
        "nearest_level": closed["nearest_level"],
        "distance_to_nearest_level": closed["distance_to_nearest_level"],
        "raw_flow_at_price": closed["signed_flow"].astype(float),
        "abs_flow_at_price": closed["abs_flow"].astype(float),
        "trade_volume_at_price": closed["trade_volume_at_price"].astype(float),
        "bid_add_at_price": closed["bid_add"].astype(float),
        "bid_pull_at_price": closed["bid_pull"].astype(float),
        "ask_add_at_price": closed["ask_add"].astype(float),
        "ask_pull_at_price": closed["ask_pull"].astype(float),
        "signed_flow_at_price": closed["signed_flow"].astype(float),
        "depth_n": closed["depth_n"],
        "side_zone": closed["side_zone"],
    })

    tc.log("  computing percentile/rank/zscore within session-day context (abs_flow_at_price, nonzero population)...")
    pct, rank, z = tc.percentile_rank_in_context(
        panel["abs_flow_at_price"].to_numpy(), panel["visible_context_id"].to_numpy(), restrict_nonzero=True)
    panel["thickness_percentile"] = pct
    panel["thickness_rank"] = rank
    panel["thickness_zscore"] = z

    is_thin, is_medium, is_fat = tc.classify_thin_fat(pct, thin_max=THIN_MAX, fat_min=FAT_MIN)
    panel["is_thin_zone"] = is_thin
    panel["is_medium_zone"] = is_medium
    panel["is_fat_zone"] = is_fat

    # secondary thickness measure based on trade_volume_at_price (documented,
    # not the primary - the chart's own percentile scale normalizes on
    # abs_flow, not trade volume)
    pct_vol, _, _ = tc.percentile_rank_in_context(
        panel["trade_volume_at_price"].to_numpy(), panel["visible_context_id"].to_numpy(), restrict_nonzero=True)
    panel["thickness_percentile_by_trade_volume"] = pct_vol

    out_path = tc.OUT_DIR / "book_flow_thickness_panel.parquet"
    panel.to_parquet(out_path, index=False)
    tc.log(f"  wrote {out_path} ({len(panel)} rows)")

    n_active = int((panel["abs_flow_at_price"] > 0).sum())
    catalog_rows = [
        dict(field="price_level", formula="price_level (level-candle cache, $0.25 tick)", notes="price-axis key"),
        dict(field="bar_end_ts_ns", formula="bar_end_ts_ns (level-candle cache)", notes="bar key, CLOSED bars only"),
        dict(field="visible_context_id", formula="= session_date (one Book Flow cache file per day)",
             notes="the reference population for percentile scaling - reproduces the live chart's "
                   "'currently visible window' semantics using the natural per-day cache partition"),
        dict(field="raw_flow_at_price / signed_flow_at_price", formula="signed_flow (level-candle cache, sum of "
             "signed add/pull events at this price within the bar)", notes="directional"),
        dict(field="abs_flow_at_price", formula="abs_flow (level-candle cache)",
             notes="PRIMARY thickness measure - matches book_flow_chart_v3.py scale_mode=='percentile': "
                   "denom=np.nanpercentile(abs_flow,95) over the visible cell population"),
        dict(field="thickness_percentile", formula="percentile RANK (0-100, average method) of "
             "abs_flow_at_price among all cells with abs_flow>0 sharing the same visible_context_id",
             notes=f"scale=percentile per task spec; population restricted to nonzero cells "
                   f"({n_active}/{len(panel)} = {n_active/len(panel)*100:.1f}% of all cells) - "
                   f"see thickness_common.percentile_rank_in_context docstring"),
        dict(field="thickness_rank", formula="dense average rank (1=lowest) within the same nonzero population",
             notes="companion to thickness_percentile, same population"),
        dict(field="thickness_zscore", formula="(abs_flow_at_price - mean)/std within the same nonzero population",
             notes="same population as thickness_percentile"),
        dict(field="is_thin_zone", formula=f"thickness_percentile <= {THIN_MAX}",
             notes="EXACT THRESHOLD USED - bottom tercile, a-priori, matches this codebase's existing "
                   "LOW/MED/HIGH regime convention (atlas_common.rolling_state_tercile), not fit to data"),
        dict(field="is_medium_zone", formula=f"{THIN_MAX} < thickness_percentile < {FAT_MIN}", notes="middle tercile"),
        dict(field="is_fat_zone", formula=f"thickness_percentile >= {FAT_MIN}",
             notes="EXACT THRESHOLD USED - top tercile, a-priori, same convention as is_thin_zone"),
        dict(field="thickness_percentile_by_trade_volume", formula="same percentile-rank method, applied to "
             "trade_volume_at_price instead of abs_flow_at_price", notes="SECONDARY measure, documented for "
             "comparison only - not used as the primary thin/medium/fat classification"),
    ]
    cat_df = pd.DataFrame(catalog_rows)
    cat_path = tc.OUT_DIR / "thickness_formula_catalog.csv"
    cat_df.to_csv(cat_path, index=False)
    tc.log(f"  wrote {cat_path}")

    diag = dict(
        n_rows=len(panel), n_days=panel["visible_context_id"].nunique(),
        n_active_cells=n_active, pct_active=round(n_active / len(panel) * 100, 2),
        n_thin=int(is_thin.sum()), n_medium=int(is_medium.sum()), n_fat=int(is_fat.sum()),
        thin_pct_threshold=THIN_MAX, fat_pct_threshold=FAT_MIN,
    )
    print(diag)
    tc.log("01 complete.")
    return panel


if __name__ == "__main__":
    main()
