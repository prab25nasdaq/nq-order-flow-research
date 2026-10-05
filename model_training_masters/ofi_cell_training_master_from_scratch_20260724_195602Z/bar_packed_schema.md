# Bar-Packed Master Schema

Grain: one row = one sealed 500-volume bar x one depth_n. All cells inside the bar are packed into `cells_json` (full fidelity) plus parallel sorted list columns for quick columnar/tensor access. All packed lists and cells_json are sorted by rel_tick_from_mid ascending.

## Columns

- `symbol`
- `contract`
- `continuous_symbol`
- `session_date`
- `timestamp_utc`
- `bar_idx`
- `depth_n`
- `open_price`
- `high_price`
- `low_price`
- `close_price`
- `mid_price`
- `volume`
- `cell_count`
- `min_price_level`
- `max_price_level`
- `min_rel_tick_from_mid`
- `max_rel_tick_from_mid`
- `total_bid_add`
- `total_bid_pull`
- `total_ask_add`
- `total_ask_pull`
- `total_net_bid_flow`
- `total_net_ask_flow`
- `total_signed_flow`
- `total_abs_flow`
- `buyer_pressure_total`
- `seller_pressure_total`
- `buyer_seller_balance_total`
- `buyer_seller_ratio_total`
- `green_cell_count`
- `red_cell_count`
- `neutral_cell_count`
- `green_abs_flow_share`
- `red_abs_flow_share`
- `cells_json`
- `cells_count_check`
- `cells_rel_tick_list`
- `cells_price_level_list`
- `cells_bid_add_list`
- `cells_bid_pull_list`
- `cells_ask_add_list`
- `cells_ask_pull_list`
- `cells_net_bid_flow_list`
- `cells_net_ask_flow_list`
- `cells_signed_flow_list`
- `cells_abs_flow_list`
- `cells_buyer_pressure_list`
- `cells_seller_pressure_list`
