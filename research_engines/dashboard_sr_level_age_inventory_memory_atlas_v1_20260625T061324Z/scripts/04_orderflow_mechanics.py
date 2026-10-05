"""
04_orderflow_mechanics.py - Part E: order-flow mechanics at dashboard S/R
retouch events.

Data source: the production Book Flow level-candle cache
(book_flow_chart/cache/book_flow_level_candles_NQU6_*_top10.parquet),
aggregated to bar resolution via sr_common.aggregate_book_flow_bars - the
SAME formula already established and validated in the Book Flow Thickness/
Auction Friction Atlas and the Level Mechanics Atlas (reused, not
reinvented). This is genuine full L2 order-book add/pull/trade data from
the production parser, pre-aggregated to bar granularity.

SCOPE: the cache only covers NQU6 days 2026-06-15..2026-06-24 (8 days).
Retouch events outside this range get has_orderflow_mechanics=False and
NaN mechanics fields - explicitly flagged, never silently imputed.

Joined via exact bar_end_ts_ns match between the continuous master and the
book-flow per-day aggregate (confirmed 958/959 exact matches on a spot
check day) - both are derived from the same underlying parser bar clock.

Windows: pre_20/pre_10 (bars before the touch bar), touch (the bar itself),
post_5/post_10/post_20 (bars after). All "pre" and "touch" fields are
past-only relative to the event; "post" fields describe what happened
AFTER the touch (mechanics context, not used as a predictive feature in
Part H).

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
DEPTH = cfg["scope"]["book_flow_depth"]
CACHE_DAYS = cfg["scope"]["book_flow_cache_days_available"]


def window_agg(sub: pd.DataFrame) -> dict:
    if sub.empty:
        return {}
    bid_add = sub["bid_add"].sum(); bid_pull = sub["bid_pull"].sum()
    ask_add = sub["ask_add"].sum(); ask_pull = sub["ask_pull"].sum()
    signed = sub["signed_flow"].sum()
    net_bid = sub["net_bid_flow"].sum(); net_ask = sub["net_ask_flow"].sum()
    return {
        "BidAdd": float(bid_add), "BidPull": float(bid_pull),
        "AskAdd": float(ask_add), "AskPull": float(ask_pull),
        "NetBid": float(net_bid), "NetAsk": float(net_ask),
        "Signed": float(signed),
        "BidPP": float(bid_pull / bid_add) if bid_add else np.nan,
        "AskPP": float(ask_pull / ask_add) if ask_add else np.nan,
        "support_consumption": float(bid_pull - bid_add),
        "resistance_consumption": float(ask_pull - ask_add),
        "support_consumed": bool(bid_pull > bid_add),
        "resistance_consumed": bool(ask_pull > ask_add),
        "bid_add_vs_bid_pull": float(bid_add - bid_pull),
        "ask_add_vs_ask_pull": float(ask_add - ask_pull),
        "trade_volume": float(sub["trade_volume"].sum()),
        "buy_trade_volume": float(sub["buy_trade_volume"].sum()),
        "sell_trade_volume": float(sub["sell_trade_volume"].sum()),
        "resting_depth_mean": float(sub["resting_depth"].mean()),
        "book_imbalance_mean": float(sub["book_imbalance"].mean()),
    }


def main():
    sc.log("04: loading continuous master + retouch events + book flow cache...")
    df = sc.load_continuous_master()
    events = pd.read_parquet(sc.OUT_DIR / "dashboard_sr_retouch_events.parquet")
    sc.log(f"  {len(events)} retouch events total")

    sc.log("  loading Book Flow level candles (NQU6, top10, 8-day cache)...")
    raw = sc.load_level_candles(depth=DEPTH, dates=CACHE_DAYS)
    sc.log(f"  {len(raw)} raw level-candle rows across {raw['session_date'].nunique()} days")
    agg = sc.aggregate_book_flow_bars(raw)
    agg = agg.sort_values("bar_end_ts_ns").reset_index(drop=True)
    agg["bar_end_ts_ns"] = agg["bar_end_ts_ns"].astype("int64")
    sc.log(f"  {len(agg)} bar-aggregated book-flow rows")

    # map continuous-master bar_end_ts_ns -> position within the book-flow agg table
    ts_to_pos = pd.Series(np.arange(len(agg)), index=agg["bar_end_ts_ns"].to_numpy())
    master_ts = pd.to_numeric(df["bar_end_ts_ns"], errors="coerce").astype("Int64")

    bf_pos_for_bar = pd.Series(index=np.arange(len(df)), dtype="Int64")
    matched_ts = master_ts[master_ts.isin(agg["bar_end_ts_ns"])]
    bf_pos_for_bar.loc[matched_ts.index] = ts_to_pos.reindex(matched_ts.values).to_numpy()
    sc.log(f"  {bf_pos_for_bar.notna().sum()}/{len(df)} continuous-master bars have book-flow coverage")

    WINS = {"pre_20": (-20, -1), "pre_10": (-10, -1), "touch": (0, 0),
            "post_5": (1, 5), "post_10": (1, 10), "post_20": (1, 20)}

    rows = []
    n_in_scope = 0
    for _, ev in events.iterrows():
        t = int(ev["bar_t"])
        bf_pos = bf_pos_for_bar.get(t, pd.NA)
        rec = {"event_id": ev["event_id"], "lookback_window": ev["lookback_window"],
               "level_type": ev["level_type"], "level_age_bucket": ev["level_age_bucket"]}
        if pd.isna(bf_pos):
            rec["has_orderflow_mechanics"] = False
            rows.append(rec)
            continue
        bf_pos = int(bf_pos)
        n_in_scope += 1
        rec["has_orderflow_mechanics"] = True
        for wname, (lo, hi) in WINS.items():
            lo_pos = max(0, bf_pos + lo)
            hi_pos = min(len(agg) - 1, bf_pos + hi)
            sub = agg.iloc[lo_pos:hi_pos + 1] if lo_pos <= hi_pos else agg.iloc[0:0]
            stats = window_agg(sub)
            for k, v in stats.items():
                rec[f"{wname}_{k}"] = v
        rows.append(rec)

    sc.log(f"  {n_in_scope}/{len(events)} events fall within Book Flow cache coverage")
    mech = pd.DataFrame(rows)
    out_path = sc.OUT_DIR / "dashboard_sr_orderflow_mechanics.parquet"
    mech.to_parquet(out_path, index=False)
    sc.log(f"  wrote {out_path} ({len(mech)} rows, {mech.shape[1]} cols)")

    catalog_rows = [
        dict(field="BidAdd/BidPull/AskAdd/AskPull", formula="sum of bid_add/bid_pull/ask_add/ask_pull "
             "(Book Flow level-candle cache, aggregated across price levels within a bar) over the window",
             notes="genuine full-book add/pull events from the production parser, bar-aggregated"),
        dict(field="NetBid/NetAsk", formula="sum of net_bid_flow/net_ask_flow over the window", notes="signed net per side"),
        dict(field="Signed", formula="sum of signed_flow over the window", notes="directional, all-levels-combined"),
        dict(field="BidPP/AskPP", formula="BidPull/BidAdd, AskPull/AskAdd over the window", notes="pull-pressure ratio"),
        dict(field="support_consumption", formula="BidPull - BidAdd", notes="positive = bid side net depleting (support eroding)"),
        dict(field="resistance_consumption", formula="AskPull - AskAdd", notes="positive = ask side net depleting (resistance eroding)"),
        dict(field="support_consumed/resistance_consumed", formula="BidPull>BidAdd / AskPull>AskAdd", notes="boolean flag"),
        dict(field="resting_depth_mean", formula="mean resting price-level count per CLOSED bar in window", notes="thickness proxy"),
        dict(field="book_imbalance_mean", formula="mean of (BidAdd-AskAdd)/(BidAdd+AskAdd) per bar", notes="directional book pressure"),
        dict(field="has_orderflow_mechanics", formula="event's bar_end_ts_ns matched in the Book Flow cache "
             "(NQU6, 2026-06-15..2026-06-24 only)", notes="events outside this date range get all-NaN mechanics "
             "fields, never imputed - see Part J report for coverage %"),
    ]
    cat_path = sc.OUT_DIR / "dashboard_sr_mechanics_catalog.csv"
    pd.DataFrame(catalog_rows).to_csv(cat_path, index=False)
    sc.log(f"  wrote {cat_path}")
    sc.log("04: done.")


if __name__ == "__main__":
    main()
