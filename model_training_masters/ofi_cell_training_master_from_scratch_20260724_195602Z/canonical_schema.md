# Canonical Cell Schema

Grain: one row = one sealed 500-volume bar x one OFI cell x one depth_n.

## Design decisions (documented, not silently invented)

- **session_date**: derived from internal per-row `timestamp_utc` using an empirically observed CME session boundary at 21:00 UTC (no bars are ever observed at UTC hour == 21 in this data, confirming a Globex maintenance-break gap there): if `hour >= 21` -> session_date = calendar date of the timestamp; else session_date = calendar date minus 1 day -- **but only for files whose first bar itself opens with the normal evening pattern** (first bar hour >= 21). Confirmed dead-feed/watchdog restarts (e.g. the 2026-07-06..08 incident) produce cache files whose first bar starts mid-day at an arbitrary UTC hour; blindly applying the per-row cutoff to those rows shifts them into the wrong prior-day session. For any file that opens abnormally (first-bar hour < 21), session_date is instead taken uniformly from that file's own internal `date` metadata (the Book Flow daemon's own session label, embedded in its .meta.json sidecar and mirrored in the filename -- verified to agree, and independently confirmed authoritative because bar_idx is a strictly contiguous non-overlapping running counter across consecutive labeled files). This is still never a folder name -- see `derive_session_dates_for_file()` in the build script.
- **open_price/high_price/low_price/volume**: the Book Flow cache cell files carry only `close_price` (constant per bar) and a *per-row walking* `mid_price` that reflects the book mid at the time each price level was last inside the top-N depth window during the bar (it is NOT a single bar-level mid). Bar-level OHLCV and a single authoritative `mid_price` (`mid_mean` from the bar-builder) are joined in from `master_NQ_continuous_backadjusted_shadow.ndjsonl` (primary) / `master_NQU6_shadow.ndjsonl` (fallback) by an EXACT match on (bar_start_ts_ns, bar_end_ts_ns). The original per-row source mid_price is preserved verbatim in `cell_walking_mid_price_raw` for full fidelity. `ohlcv_source` records which source satisfied the join, or 'unavailable'.
- **close_tick / mid_tick**: the source `price_tick` column uses an internal tick-numbering offset that is not documented. close_tick/mid_tick are recovered by anchoring off the nearest cell's own (price_level, price_tick) pair and stepping by TICK_SIZE=0.25, so rel_tick_from_close/mid are exact regardless of the unknown offset.
- **nearest_level_type**: the source only exposes one label column (`nearest_level`, e.g. POC/VAH/VAL/HVN/LVN/S/R). `nearest_level` and `nearest_level_type` are both populated from this single source column; no separate numeric nearest-level price exists in source data.
- **symbol**: set to the product root `NQ` for all rows; `contract` carries the literal contract code (NQM6/NQU6); `continuous_symbol` is set to `NQ_continuous_backadjusted` only for bars successfully joined against the continuous master.

## Long master columns

- `source_file`
- `symbol`
- `contract`
- `continuous_symbol`
- `session_date`
- `timestamp_utc`
- `bar_idx`
- `bar_start_ts_ns`
- `bar_end_ts_ns`
- `depth_n`
- `price_level`
- `price_tick`
- `side_zone`
- `sealed`
- `bar_state`
- `bid_add`
- `bid_pull`
- `ask_add`
- `ask_pull`
- `net_bid_flow`
- `net_ask_flow`
- `signed_flow`
- `abs_flow`
- `buyer_pressure_cell`
- `seller_pressure_cell`
- `buyer_seller_balance_cell`
- `buyer_seller_ratio_cell`
- `trade_volume_at_price`
- `buy_trade_volume_at_price`
- `sell_trade_volume_at_price`
- `open_price`
- `high_price`
- `low_price`
- `close_price`
- `mid_price`
- `volume`
- `ohlcv_source`
- `nearest_level`
- `distance_to_nearest_level`
- `nearest_level_type`
- `distance_ticks_to_nearest_level`
- `close_tick`
- `mid_tick`
- `rel_tick_from_close`
- `rel_tick_from_mid`
- `rel_price_from_close`
- `rel_price_from_mid`
- `cell_id_within_bar`
- `cell_walking_mid_price_raw`
