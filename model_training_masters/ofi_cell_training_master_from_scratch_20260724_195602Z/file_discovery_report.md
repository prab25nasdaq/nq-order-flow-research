# File Discovery Report

Scanned: `/home/prabh/OFI_Production/book_flow_chart/cache/`

- Total files found: 725
- Cell-level dated parquet candidates: 232
- Included (sealed CLOSED rows confirmed): 232
- Excluded bar-summary-only files (OHLC, no cells): 4

## By category

| category                       |   count |
|:-------------------------------|--------:|
| meta_or_state_json             |     341 |
| cell_level_dated_source_family |     120 |
| cell_level_dated_cache_family  |     112 |
| unrecognized_parquet           |     108 |
| pickle_internal_state          |      38 |
| bar_summary_no_cells           |       4 |
| log_file                       |       1 |
| other_non_parquet              |       1 |

## By file_type

| file_type         |   count |
|:------------------|--------:|
| parquet           |     344 |
| json              |     341 |
| pkl               |      38 |
| log               |       1 |
| pre_repair_backup |       1 |

Forming-only and non-CLOSED-only files were excluded from the training masters. See `include_in_cell_master` / `exclude_reason` columns in discovered_cell_files.csv for the per-file decision.
