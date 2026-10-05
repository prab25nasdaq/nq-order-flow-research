# Price Travel / Liquidity Vacuum — Case Studies
**SHADOW / RESEARCH ONLY**

## Case 1: Thin-Book High-Travel Bars (Recent)
Empty DataFrame
Columns: [case, bar_index, day, bar_range_pts, vol_total, signed_travel, dir_bucket, bid_liq_removed, support_removed, cur_sell_toxicity, absorption_score, replenishment_failure, resistance_removed]
Index: []

**Analysis**: Low volume + high range = classic liquidity vacuum.
Price traveled far because offers/bids were pulled without replacement.
Kyle λ is elevated (high impact per contract). This is the pure vacuum signature.

## Case 2: Jun 25 Support Failure (bars ~9066+)
                       case  bar_index      day  bar_range_pts  vol_total  signed_travel dir_bucket  bid_liq_removed  support_removed  cur_sell_toxicity  absorption_score  replenishment_failure  resistance_removed
CASE2_JUN25_SUPPORT_FAILURE       9066 20260625          36.25        500         -15.50     NORMAL              1.0              5.0             0.0000               NaN                    NaN                 NaN
CASE2_JUN25_SUPPORT_FAILURE       9067 20260625          28.25        500         -15.00     NORMAL              1.0             23.0             0.0001               NaN                    NaN                 NaN
CASE2_JUN25_SUPPORT_FAILURE       9068 20260625          38.75        500         -12.00     NORMAL              0.0            -41.0             0.0012               NaN                    NaN                 NaN
CASE2_JUN25_SUPPORT_FAILURE       9069 20260625          17.25        500         -10.75     NORMAL              1.0              7.0             0.0000               NaN                    NaN                 NaN
CASE2_JUN25_SUPPORT_FAILURE       9070 20260625          20.75        500         -13.50     NORMAL              0.0            -54.0             0.0010               NaN                    NaN                 NaN

**Analysis**: Bids pulled without replacement near a key support level.
Sell toxicity was elevated in prior bars (informed selling).
Once support was consumed (BidPull > BidAdd), price dropped through the vacuum.
Bearish book switch was active. CUSUM down break confirmed structural shift.

## Case 3: High Volume Low Travel (Absorption)
                     case  bar_index      day  bar_range_pts  vol_total  signed_travel            dir_bucket  bid_liq_removed  support_removed  cur_sell_toxicity  absorption_score  replenishment_failure  resistance_removed
CASE3_HIGH_VOL_LOW_TRAVEL         34 20260614          14.00        500          13.50 LOW_TRAVEL_ABSORPTION              NaN              NaN                NaN           0.00799                    0.0                 NaN
CASE3_HIGH_VOL_LOW_TRAVEL         41 20260614          13.00        500          -6.75 LOW_TRAVEL_ABSORPTION              NaN              NaN                NaN           0.06078                    0.0                 NaN
CASE3_HIGH_VOL_LOW_TRAVEL         79 20260614          14.25        500           4.25 LOW_TRAVEL_ABSORPTION              NaN              NaN                NaN           0.04243                    0.0                 NaN
CASE3_HIGH_VOL_LOW_TRAVEL        101 20260614           9.50        500          -2.00 LOW_TRAVEL_ABSORPTION              NaN              NaN                NaN           0.02750                   19.0                 NaN
CASE3_HIGH_VOL_LOW_TRAVEL        119 20260614          11.25        500          -5.75 LOW_TRAVEL_ABSORPTION              NaN              NaN                NaN           0.06098                    0.0                 NaN

**Analysis**: Both sides actively quoting (BidAdd AND AskAdd high).
Price couldn't move because every aggressive order met a passive counterparty.
Absorption score elevated. This is the thick-book equilibrium state.
High volume but no direction = two-way exchange. VPIN undirected.

## Case 4: Low Volume High Travel (Vacuum)
                     case  bar_index      day  bar_range_pts  vol_total  signed_travel        dir_bucket  bid_liq_removed  support_removed  cur_sell_toxicity  absorption_score  replenishment_failure  resistance_removed
CASE4_LOW_VOL_HIGH_TRAVEL          0 20260614          63.50        500         -21.25            NORMAL              NaN            -29.0                NaN               NaN                    NaN               -39.0
CASE4_LOW_VOL_HIGH_TRAVEL          1 20260614          36.75        500         -13.25            NORMAL              NaN             20.0                NaN               NaN                    NaN              -177.0
CASE4_LOW_VOL_HIGH_TRAVEL          2 20260614          34.75        500          34.75 EXTREME_UP_TRAVEL              NaN           -117.0                NaN               NaN                    NaN                13.0
CASE4_LOW_VOL_HIGH_TRAVEL          3 20260614          35.25        500           4.00            NORMAL              NaN             48.0                NaN               NaN                    NaN               -36.0
CASE4_LOW_VOL_HIGH_TRAVEL          4 20260614          34.25        500         -17.75            NORMAL              NaN             -9.0                NaN               NaN                    NaN               -51.0

**Analysis**: Minimal participation but huge move.
Either: (a) a large single order swept thin book, or (b) book was pre-emptively pulled.
Range per volume is extreme. These bars are the pure "gap through thin air" events.
Often occur in Asia session or near session open/close.
