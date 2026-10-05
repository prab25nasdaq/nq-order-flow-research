# Relative Ladder Wide Master Schema

Grain: one row = one sealed 500-volume bar x one depth_n. Fixed-width columns for every relative tick K in [-W, +W] around the bar's mid_price.

## Missing-cell semantics

- A relative tick K that falls WITHIN the bar's actually-observed depth range (`min_rel_tick_from_mid` <= K <= `max_rel_tick_from_mid`) but has no cell row: this means genuinely zero order-flow activity was recorded at that tick during the bar. Numeric flow columns are set to 0.0 and `rel_K_observed=false`.
- A relative tick K that falls OUTSIDE the bar's observed depth range: the depth window never reached that tick during the bar, so activity there is unknown/unavailable, not zero. Numeric flow columns are set to NA and the row's `data_quality_flag` is set to `NA_GAPS_OUTSIDE_BAR_DEPTH_RANGE`.
- If the bar's observed range extends beyond +-W, the outermost cells are truncated from this fixed-width file (they remain fully intact in the long and packed masters) and `data_quality_flag` is set to `TRUNCATED_CELLS_OUTSIDE_WIDTH`.

## Widths built: [40, 80, 160]
