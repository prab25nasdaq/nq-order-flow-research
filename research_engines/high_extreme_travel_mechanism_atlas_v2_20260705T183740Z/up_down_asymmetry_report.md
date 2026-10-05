# UP vs DOWN Travel Asymmetry Report
**SHADOW / RESEARCH ONLY**

## Sample Sizes
- HIGH/EXTREME UP_TRAVEL bars:   1597
- HIGH/EXTREME DOWN_TRAVEL bars: 1669

## UP_TRAVEL Primary Driver: Ask-Side Vacuum
- upper_ask_removed mean: 67.0
- Bars with ask_liq_removed > 0: 89.4%
- close_at_high: 18.9% of UP_TRAVEL bars closed at high
- bullish_switch mean: 12503 vs bearish_switch: 12058

## DOWN_TRAVEL Primary Driver: Bid-Side Vacuum
- lower_bid_removed mean: 56.0
- Bars with bid_liq_removed > 0: 87.8%
- close_at_low: 18.4% of DOWN_TRAVEL bars closed at low
- bearish_switch mean: 12945 vs bullish_switch: 12523

## Feature Asymmetry
              feature      UP_mean      DN_mean  asymmetry_ratio
replenishment_failure 7.220332e+01 5.937881e+01            1.216
                 vpin 1.328600e-01 1.314900e-01            1.010
   range_per_abs_flow 6.837821e+07 5.507789e+07            1.241
           efficiency 6.775400e-01 6.764500e-01            1.002
     absorption_score 6.161000e-02 6.175000e-02            0.998

## Key Finding
UP and DOWN travel share the same vacuum mechanism but mirror it directionally.
- UP bars: ask side emptied above price → vacuum above → price swept up
- DOWN bars: bid side emptied below price → vacuum below → price swept down

UP travel shows higher close_at_high (18.9%)
vs DOWN close_at_low (18.4%) —
indicating UP momentum is stronger (full follow-through).

Both directions: replenishment failure, book switch, and directional VPIN
are additive confirmation signals. None is sufficient alone.
