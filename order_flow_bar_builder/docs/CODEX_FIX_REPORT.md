# CODEX_FIX_REPORT

Generated: 2026-06-15 UTC

## Audit Summary

Raw files inspected:

- `/home/prabh/OFI_Live_Data/Rithmic_Raw/2026-06-14/NQU6/bid_quote_updates.ndjson`
  - timestamp: `event_ts_ns`
  - side: `side=B`
  - price: `price`
  - size: `size`, valid when `size_valid=true`
- `/home/prabh/OFI_Live_Data/Rithmic_Raw/2026-06-14/NQU6/ask_quote_updates.ndjson`
  - timestamp: `event_ts_ns`
  - side: `side=A`
  - price: `price`
  - size: `size`, valid when `size_valid=true`
- `/home/prabh/OFI_Live_Data/Rithmic_Raw/2026-06-14/NQU6/depth.ndjson`
  - timestamp: `timestamp_ns`
  - side: `side`
  - level/depth: `level`
  - price: `price`
  - size: `size`
- `/home/prabh/OFI_Live_Features/2026-06-14/NQU6_vol500.ndjsonl`
  - bar mapping: `bar_start_ts_ns`, `bar_end_ts_ns`
  - price context only: `px_open`, `px_high`, `px_low`, `px_close`
  - MLOFI fields present in source features but not used for V2 main candles.

Existing issue found:

- The old standalone app had partial true-flow logic, but price context was still attached to the main panel through a secondary price ViewBox. V2 removes that: main candles live only on the raw book-flow plot; price is in its own context panel.
- The dashboard launcher panel pointed at `book_flow_chart.py`. It now points at `book_flow_chart_v2.py`.
- The shared cache path is symbol/depth scoped, so V2 filters cached rows by selected `timestamp_utc` date before rendering or smoke validation.

## Files Changed

- `/home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v2.py`
- `/home/prabh/OFI_Production/launch_book_flow_chart.sh`
- `/mnt/wd_work/workspace/Work Place/Data/Project OFI/_codex_work/ofi_live_dashboard_WORKING_NEXT_with_logreg.py`

## Validation Commands

- `env PYTHONPYCACHEPREFIX=/tmp/ofi_pycache_check python -m py_compile /home/prabh/OFI_Production/book_flow_chart/book_flow_chart.py /home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v2.py`
- `env PYTHONPYCACHEPREFIX=/tmp/ofi_pycache_check python -m py_compile "/mnt/wd_work/workspace/Work Place/Data/Project OFI/_codex_work/ofi_live_dashboard_WORKING_NEXT_with_logreg.py"`
- `bash -n /home/prabh/OFI_Production/launch_book_flow_chart.sh`
- `timeout 15s /home/prabh/.venvs/ofi/bin/python /home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v2.py --smoke-test`

## Required Final Output

APP_PATH: `/home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v2.py`

LAUNCHER_PATH: `/home/prabh/OFI_Production/launch_book_flow_chart.sh`

DASHBOARD_FILE: `/mnt/wd_work/workspace/Work Place/Data/Project OFI/_codex_work/ofi_live_dashboard_WORKING_NEXT_with_logreg.py`

OLD_APP_PROCESSES_KILLED: true

CREATED_V2_APP: true

MAIN_CANDLE_RENDERER: `RawBookFlowCandleItem`

MAIN_CANDLE_ARRAYS: `of_open`, `of_high`, `of_low`, `of_close`

PRICE_OHLC_USED_FOR_MAIN_CANDLES: false

MLOFI_USED_FOR_MAIN_CANDLES: false

MAIN_CANDLE_SOURCE: `RAW_BOOK_FLOW_TOP_N`

OF_CANDLE_VALUE_RANGE: `-747.0,806.0`

PRICE_VALUE_RANGE: `30332.2,30915.0`

ORDER_FLOW_AXIS_SEPARATE_FROM_PRICE_AXIS: true

TOP_DEPTH_SUPPORTED: true

DEPTH_CHOICES: `5,10,15,20`

AUTO_RESET_FIXED: true

USER_VIEW_LOCK_IMPLEMENTED: true

FOLLOW_LIVE_DISABLED_ON_MANUAL_PAN: true

DASHBOARD_LAUNCH_BUTTON_ADDED: true

DASHBOARD_STOP_BUTTON_ADDED: true

PY_COMPILE_APP: PASS

PY_COMPILE_DASHBOARD: PASS

SMOKE_TEST: PASS

TRADING_ENABLED: false

OVERALL PASS
