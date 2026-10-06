# Book Flow Chart V3 Performance Fix Report

Timestamp UTC: 2026-06-16T17:53:21Z

## Files

APP_PATH: `/home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py`

BACKUP_DIR: `/home/prabh/OFI_Production/backups/book_flow_v3_perf_fix_20260616T175321Z`

BACKUP_CREATED: PASS

Backed up before patching:

- `/home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py`
- `/home/prabh/OFI_Production/book_flow_chart/build_book_flow_level_cache.py`
- `/home/prabh/OFI_Production/launch_book_flow_chart.sh`
- Backup manifest: `/home/prabh/OFI_Production/backups/book_flow_v3_perf_fix_20260616T175321Z/BACKUP_MANIFEST.txt`

## Root Cause

PERF_ROOT_CAUSE: `_reload()` was doing too much synchronous GUI-thread work on every cache-worker update: full parquet cache read, full vol500 bar read, full visible cache rebuild, full cell lookup rebuild, level/profile recomputation, pressure-panel groupby, and render object data rebuild. The cache worker also called the cache builder every interval even when inputs were unchanged, and the builder inspected raw depth before checking current cache metadata.

## Fix Summary

FULL_RAW_REPARSE_REMOVED: PASS for refresh path. Current-cache checks now use metadata before raw support inspection; unchanged worker cycles skip builder calls entirely by file signature.

INCREMENTAL_READER_ENABLED: PASS for live update path via cache/input file-signature reader. The GUI only reloads cached parquet/bars when file signatures change; unchanged cycles do no raw or parquet reload. Any genuinely stale cache rebuild remains in the worker thread, not the GUI thread.

GUI_THREAD_BLOCKING_REMOVED: PASS. Raw cache build work stays in `CacheWorker`; GUI render consumes already-built parquet cache and only reloads it on mtime/size changes.

RENDER_THROTTLING_ENABLED: PASS. Default render throttle is controlled by `OFI_BOOK_FLOW_MAX_FPS` with default 4 FPS.

FRAME_COALESCING_ENABLED: PASS. `_queue_reload()` coalesces pending data/render updates and skips ticks while a refresh is busy.

VISIBLE_WINDOW_RENDERING_ENABLED: PASS. Renderer now selects the current visible bar window plus margin instead of rendering the full lookback/history.

OBJECT_REUSE_OR_BATCH_RENDERING_ENABLED: PASS. Existing single `BookFlowLevelCellItem`, curves, bars, and profile item are reused; updates use `set_data`/`setOpts` instead of thousands of per-refresh graphics item objects.

VOLUME_PROFILE_THROTTLED: PASS. Level overlays/profile recompute only when data signature, visible window, or overlay toggles change.

PULLS_PANEL_THROTTLED: PASS. Pressure/pulls panel groupby updates only when data signature, visible window, or panel toggle changes.

REENTRANCY_GUARD_ENABLED: PASS. `_refresh_busy` skips/coalesces overlapping refreshes and records skipped frames.

VIEW_LOCK_STILL_WORKS: PASS. Smoke verified pan/zoom/cursor/data-refresh preserve locked view.

FOLLOW_LIVE_STILL_WORKS: PASS. Smoke verified follow-live starts checked, stays checked during cursor/pan/zoom/data refresh, and explicit checkbox clicks still work.

PRICE_OHLC_USED_AS_MAIN_CANDLES: false

MLOFI_USED_AS_MAIN_CANDLES: false

TOP_DEPTH_STILL_SUPPORTED: PASS. Smoke verified raw depth/top-depth reconstruction and depth choices `[5, 10, 15, 20]`.

TRADING_ENABLED: false

## Validation

PY_COMPILE: PASS

- `env PYTHONPYCACHEPREFIX=/tmp/ofi_pycompile_cache python -m py_compile /home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py`
- `env PYTHONPYCACHEPREFIX=/tmp/ofi_pycompile_cache python -m py_compile /home/prabh/OFI_Production/book_flow_chart/build_book_flow_level_cache.py`

LAUNCHER_SYNTAX: PASS

- `bash -n /home/prabh/OFI_Production/launch_book_flow_chart.sh`

SMOKE_TEST: PASS

- Command: `timeout 20s /home/prabh/.venvs/ofi/bin/python -u /home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py --smoke-test`
- Result: `SMOKE_TEST=PASS`
- Performance guards: `PERFORMANCE_STATS_EXIST=true`, `VISIBLE_WINDOW_RENDERING=true`, `RENDER_THROTTLE_ENABLED=true`, `REENTRANCY_GUARD_ENABLED=true`
- View/follow checks: `PAN_ZOOM_PRESERVES_VIEW=true`, `DATA_REFRESH_PRESERVES_VIEW=true`, `RESET_VIEW_STILL_WORKS=true`, `FOLLOW_LIVE_CHECKBOX_STAYS_CHECKED=true`

BENCHMARK_RESULT: PASS

- Command: `/home/prabh/.venvs/ofi/bin/python -u /home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py --benchmark --bars 1000 --depth 20`
- `load_ms=43.532`
- `render_prepare_ms=1.825`
- `render_ms=92.496`
- `gui_update_ms=137.907`
- `visible_bars=75`
- `visible_cells=9114`
- `max_update_ms_estimate=137.907`
- `backend=cached-parquet-visible-window`

Additional top5 stress check:

- Command: `/home/prabh/.venvs/ofi/bin/python -u /home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py --benchmark --bars 1000 --depth 5`
- Result: `BENCHMARK_RESULT=PASS`
- `render_ms=132.248`, `gui_update_ms=191.952`, `visible_bars=210`, `visible_cells=17170`

## Notes

- No parser, scheduler, master files, model artifacts, dashboard model logic, or trading flags were touched.
- Dashboard file was not modified.
- Visual design and chart meaning are unchanged: main visual remains `PRICE_AXIS_BOOK_FLOW_LEVEL_CANDLES` using raw Rithmic depth/book-flow level cells on the price axis.

OVERALL PASS
