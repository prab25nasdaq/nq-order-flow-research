# V3_REPORT

Generated: 2026-06-15 UTC

## Raw Data Audit

Inspected 2026-06-15 NQU6 raw Rithmic files:

- `depth.ndjson`: full depth snapshot with `side`, `level`, `price`, `size`, `timestamp_ns`; 760 rows; bid levels 0-609; ask levels 0-149.
- `bid_quote_updates.ndjson`: multi-price bid updates with `event_ts_ns`, `price`, `size`; 339 unique bid prices sampled.
- `ask_quote_updates.ndjson`: multi-price ask updates with `event_ts_ns`, `price`, `size`; 353 unique ask prices sampled.
- `trades.ndjson`: trade price/size/aggressor fields available for trade volume at price.
- `NQU6_vol500.ndjsonl`: bar mapping uses `bar_start_ts_ns` and `bar_end_ts_ns`.

Top-depth reconstruction is available. V3 initializes the ladder from `depth.ndjson`, processes raw bid/ask quote updates, ranks the affected price against the active bid/ask books, and aggregates only updates that occur inside the selected top 5 / 10 / 15 / 20 depth.

## Files Changed

- `/home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py`
- `/home/prabh/OFI_Production/book_flow_chart/build_book_flow_level_cache.py`
- `/home/prabh/OFI_Production/launch_book_flow_chart.sh`
- `/mnt/wd_work/workspace/Work Place/Data/Project OFI/_codex_work/ofi_live_dashboard_WORKING_NEXT_with_logreg.py`

## Validation

- `python -m py_compile /home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py`: PASS
- `python -m py_compile /home/prabh/OFI_Production/book_flow_chart/build_book_flow_level_cache.py`: PASS
- `python -m py_compile "/mnt/wd_work/workspace/Work Place/Data/Project OFI/_codex_work/ofi_live_dashboard_WORKING_NEXT_with_logreg.py"` with `PYTHONPYCACHEPREFIX=/tmp/ofi_pycache_check`: PASS
- `bash -n /home/prabh/OFI_Production/launch_book_flow_chart.sh`: PASS
- `timeout 20s /home/prabh/.venvs/ofi/bin/python /home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py --smoke-test`: PASS

## Smoke Results

- `RAW_DEPTH_RECONSTRUCTION=true`
- `TOP_DEPTH_RECONSTRUCTION=true`
- `RAW_DEPTH_ROWS=760`
- `BID_LEVEL_COUNT=610`
- `ASK_LEVEL_COUNT=150`
- `LEVEL_CACHE_ROWS_BY_DEPTH={'5': 1917, '10': 2139, '15': 2361, '20': 2647}`
- `BEST_BID_ASK_ONLY=false`
- `MAIN_VISUAL_TYPE=PRICE_AXIS_BOOK_FLOW_LEVEL_CANDLES`
- `Y_AXIS=price`
- `PRICE_OHLC_USED_AS_MAIN_CANDLES=false`
- `MLOFI_USED_AS_MAIN_CANDLES=false`
- `CELLS_SOURCE=raw Rithmic depth + bid/ask quote updates`
- `DEPTH_CHOICES=[5, 10, 15, 20]`
- `LEVEL_OVERLAYS=true`
- `VOLUME_BLUEPRINT=true`
- `AUTO_RESET_FIXED=true`
- `DASHBOARD_LAUNCH_BUTTON_USES_V3=true`

## Required Final Output

APP_PATH: `/home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py`

CACHE_BUILDER_PATH: `/home/prabh/OFI_Production/book_flow_chart/build_book_flow_level_cache.py`

LAUNCHER_PATH: `/home/prabh/OFI_Production/launch_book_flow_chart.sh`

DASHBOARD_FILE: `/mnt/wd_work/workspace/Work Place/Data/Project OFI/_codex_work/ofi_live_dashboard_WORKING_NEXT_with_logreg.py`

MAIN_VISUAL_TYPE: `PRICE_AXIS_BOOK_FLOW_LEVEL_CANDLES`

Y_AXIS: `price`

PRICE_OHLC_USED_AS_MAIN_CANDLES: false

MLOFI_USED_AS_MAIN_CANDLES: false

BEST_BID_ASK_ONLY: false

RAW_DEPTH_RECONSTRUCTION: true

TOP_DEPTH_RECONSTRUCTION: true

DEPTH_CHOICES: `[5,10,15,20]`

CACHE_PATH: `/home/prabh/OFI_Production/book_flow_chart/cache/book_flow_level_candles_NQU6_2026-06-15_top5.parquet`

LEVEL_OVERLAYS_SUPPORTED: true

VOLUME_BLUEPRINT_SUPPORTED: true

PULLS_PANEL_SUPPORTED: true

AUTO_RESET_FIXED: true

DASHBOARD_LAUNCH_BUTTON_USES_V3: true

PY_COMPILE_APP: PASS

PY_COMPILE_CACHE_BUILDER: PASS

PY_COMPILE_DASHBOARD: PASS

SMOKE_TEST: PASS

TRADING_ENABLED: false

OVERALL PASS
