# Integrity Validation Report

## Long master

| check                              | pass   | detail                                           |
|:-----------------------------------|:-------|:-------------------------------------------------|
| grain_uniqueness_no_duplicate_keys | True   | 0 duplicate canonical keys found                 |
| formula_net_bid_flow               | True   | net_bid_flow == bid_add - bid_pull               |
| formula_net_ask_flow               | True   | net_ask_flow == ask_pull - ask_add               |
| formula_signed_flow                | True   | signed_flow == net_bid_flow + net_ask_flow       |
| formula_abs_flow                   | True   | abs_flow == bid_add+bid_pull+ask_add+ask_pull    |
| formula_buyer_pressure_cell        | True   | buyer_pressure_cell == bid_add + ask_pull        |
| formula_seller_pressure_cell       | True   | seller_pressure_cell == ask_add + bid_pull       |
| primitive_nonneg_bid_add           | True   | 0 negative values in bid_add                     |
| primitive_nonneg_bid_pull          | True   | 0 negative values in bid_pull                    |
| primitive_nonneg_ask_add           | True   | 0 negative values in ask_add                     |
| primitive_nonneg_ask_pull          | True   | 0 negative values in ask_pull                    |
| primitive_nonneg_abs_flow          | True   | 0 negative values in abs_flow                    |
| forming_bars_excluded              | True   | 0 non-CLOSED rows found                          |
| no_future_leakage_columns          | True   | columns matching label/target/future pattern: [] |
| rel_tick_from_mid_correct          | True   | rel_tick_from_mid == price_tick - mid_tick       |
| rel_tick_from_close_correct        | True   | rel_tick_from_close == price_tick - close_tick   |

## Packed master

| check                                                         | pass   | detail                                                             |
|:--------------------------------------------------------------|:-------|:-------------------------------------------------------------------|
| long_count_equals_cells_count_check                           | True   | 0 bar/depth rows where long-master cell count != packed cell_count |
| cells_json_length_matches_count_check (sampled)               | True   | 0 mismatches in sample of 2000                                     |
| list_columns_length_matches_count_check (sampled)             | True   | 0 mismatches in sample of 2000                                     |
| sum_cells_bid_add_list_equals_total_bid_add (sampled)         | True   | 0 mismatches in sample of 2000                                     |
| sum_cells_bid_pull_list_equals_total_bid_pull (sampled)       | True   | 0 mismatches in sample of 2000                                     |
| sum_cells_ask_add_list_equals_total_ask_add (sampled)         | True   | 0 mismatches in sample of 2000                                     |
| sum_cells_ask_pull_list_equals_total_ask_pull (sampled)       | True   | 0 mismatches in sample of 2000                                     |
| sum_cells_signed_flow_list_equals_total_signed_flow (sampled) | True   | 0 mismatches in sample of 2000                                     |
| sum_cells_abs_flow_list_equals_total_abs_flow (sampled)       | True   | 0 mismatches in sample of 2000                                     |

## Relative ladder

| check                                 | pass   | detail                    |
|:--------------------------------------|:-------|:--------------------------|
| pm40_file_exists                      | True   | 128170 rows               |
| pm40_row_count_matches_packed_master  | True   | wide=128170 packed=128170 |
| pm80_file_exists                      | True   | 128170 rows               |
| pm80_row_count_matches_packed_master  | True   | wide=128170 packed=128170 |
| pm160_file_exists                     | True   | 128170 rows               |
| pm160_row_count_matches_packed_master | True   | wide=128170 packed=128170 |

## Raw files untouched

This script opens all inputs read-only (`pq.read_table`, plain file reads on the ndjsonl masters) and never writes to any path under book_flow_chart/cache/, OFI_Live_Features/, or model_feature_master/. All outputs are written exclusively under `/home/prabh/OFI_Production/model_training_masters/ofi_cell_training_master_from_scratch_20260724_195602Z`.
