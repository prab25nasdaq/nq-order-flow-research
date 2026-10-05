# OFI Cell Training Master FROM SCRATCH -- Build Report

SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING.

## Answers

1. **What was built?** A full-fidelity cell-level OFI Book Flow training master: every sealed 500-volume bar's individual order-book cells (price-level level BidAdd/BidPull/AskAdd/AskPull and derived flow) are preserved, in three complementary layouts (long, bar-packed, fixed-width relative ladder).
2. **Long master row grain:** one row = one sealed 500-volume bar x one OFI cell x one depth_n.
3. **Packed master row grain:** one row = one sealed 500-volume bar x one depth_n, with all cells packed into `cells_json` + parallel sorted list columns.
4. **Relative ladder row grain:** one row = one sealed 500-volume bar x one depth_n, fixed-width columns per relative tick K in [-W,+W] for W in {40,80,160}.
5. **If a candle has 50 cells, are all 50 included?** Yes -- verified per-bar in Part H/I (`long_count == cells_count_check`, sum-of-parts checks). See examples/ for concrete proof bars.
6. **Total bars processed:** 128170
7. **Total cell rows processed:** 12820871
8. **Date range covered:** 2026-06-03 .. 2026-07-23
9. **Internal timestamps used instead of folder dates?** Yes -- session_date and all bar timestamps are derived from `timestamp_utc`/`bar_start_ts_ns`/`bar_end_ts_ns` inside each file; filenames are only used to locate candidate files, never trusted for date/time truth.
10. **Multi-date folders/files handled?** Yes -- each file's rows are split into session dates purely by internal timestamp (see canonical_schema.md session_date rule).
11. **Shutdown/missing days detected?** Yes -- 8 DATA_GAP weekday sessions found and 1 PARTIAL/short sessions flagged. See daily_coverage_audit.csv.
12. **Depth values available:** [5, 10, 15, 20]
13. **Duplicates found?** Cross-family duplicate rows: 11300971; within-file exact duplicates dropped: None.
14. **Conflicting duplicates found?** True. When two redundant cache-file families (`book_flow_level_candles_*` and the bare `<CONTRACT>_<date>_top<N>.parquet` cache copy) disagreed for the same (contract, date, depth), the incomplete/stale copy was never ingested -- the pipeline always deterministically keeps the more-complete `book_flow_level_candles_*` source file and excludes the other, so no blended/ambiguous rows ever reach the training masters. Full details of every disagreement are in `duplicate_cell_rows_report.csv` and `conflicting_duplicate_cell_rows.parquet`. Unresolved (blocking) conflicts: False.
15. **Did formulas validate?** True
16. **Did packed cell counts validate?** True
17. **Forming bars excluded?** Yes -- only bar_state == 'CLOSED' rows are ingested.
18. **Feature Master context joined?** True (see feature_master_join_report.csv)
19. **Transformer/sequence training file:** `ofi_bar_packed_cells_master.parquet` (cells_json / cells_*_list per bar, ready to pad/mask into a variable-length sequence per bar) or `ofi_cell_master_long.parquet` grouped by (bar_idx, depth_n) for a from-scratch tokenizer.
20. **XGBoost/LightGBM file:** `ofi_relative_ladder_wide_pm80.parquet` (or pm40 for a smaller feature set) -- fixed-width tabular columns per bar.
21. **CNN/tensor-style training file:** `ofi_relative_ladder_wide_pm160.parquet` -- reshape the rel_K_* columns per bar into a 1D (or stacked multi-depth) tensor of length 2W+1.
22. **Ready for model training?** PASS
23. **Next step for labels:** this build intentionally contains NO labels/targets/forward returns. A separate labeling script should join forward N-bar returns / triple-barrier labels onto `bar_idx`+`depth_n` keys from `ofi_bar_packed_cells_master.parquet`, strictly after this build, so no future information ever enters this feature master.

## Final fields

SOURCE_FILES_DISCOVERED: 725
START_DATE: 2026-06-03
END_DATE: 2026-07-23
LONG_CELL_MASTER_CREATED: True
BAR_PACKED_MASTER_CREATED: True
RELATIVE_LADDER_WIDE_CREATED: True
FEATURE_MASTER_CONTEXT_JOINED: True
DAILY_COVERAGE_AUDIT_CREATED: True
DATA_GAPS_FOUND: True
FORMING_BARS_EXCLUDED: True
DUPLICATE_CELL_ROWS_FOUND: True
CONFLICTING_DUPLICATES_FOUND: True
CONFLICTING_DUPLICATES_UNRESOLVED: False
FORMULA_VALIDATION_PASS: True
GRAIN_UNIQUENESS_PASS: True
PACKED_CELL_COUNT_VALIDATION_PASS: True
PACKED_CELL_SUM_VALIDATION_PASS: True
RELATIVE_LADDER_CREATED: True
EXAMPLE_PROOF_FILES_CREATED: True
RAW_FILES_MODIFIED: false
PRODUCTION_FILES_MODIFIED: false
DASHBOARD_CODE_MODIFIED: false
BOOK_FLOW_CODE_MODIFIED: false
FEATURE_MASTER_CODE_MODIFIED: false
MODEL_ARTIFACTS_MODIFIED: false
ACTIVE_MODEL_POINTER_CHANGED: false
TRADING_ENABLED: false
BROKER_CONNECTED: false
PAPER_TRADING_ENABLED: false
MODEL_TRAINING_READY: PASS
OVERALL: PASS
