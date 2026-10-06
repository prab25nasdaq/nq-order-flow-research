# CACHE / GUI SPLIT REPORT

Timestamp UTC: 2026-06-16T18:23:18Z

BACKUP_CREATED: PASS

BACKUP_DIR: `/home/prabh/OFI_Production/backups/book_flow_cache_gui_split_20260616T182318Z`

Backed up before patching:

- `/home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py`
- `/home/prabh/OFI_Production/book_flow_chart/build_book_flow_level_cache.py`
- `/home/prabh/OFI_Production/launch_book_flow_chart.sh`
- `/home/prabh/OFI_Production/backups/book_flow_cache_gui_split_20260616T182318Z/BACKUP_MANIFEST.txt`

## Split Architecture

CACHE_DAEMON_CREATED: PASS

New daemon:

- `/home/prabh/OFI_Production/book_flow_chart/book_flow_cache_daemon.py`

Daemon responsibilities:

- Owns compact book-flow level cache creation/update outside the GUI process.
- Writes compact parquet atomically under:
  - `/home/prabh/OFI_Production/book_flow_chart/cache/NQU6_2026-06-15_top5.parquet`
  - `/home/prabh/OFI_Production/book_flow_chart/cache/NQU6_2026-06-15_top10.parquet`
  - `/home/prabh/OFI_Production/book_flow_chart/cache/NQU6_2026-06-15_top15.parquet`
  - `/home/prabh/OFI_Production/book_flow_chart/cache/NQU6_2026-06-15_top20.parquet`
- Uses atomic temp-write then rename.
- Uses compact cache/source signatures to avoid repeated work.
- Has raw-builder fallback only in daemon context, not GUI refresh.

GUI_RAW_PARSING_REMOVED: PASS

GUI_READS_CACHE_ONLY: PASS

GUI changes:

- `book_flow_chart_v3.py` now reads `book_flow_cache_daemon.compact_cache_path(...)`.
- GUI worker was changed from a cache-builder worker to `CacheFileMonitor`, which only watches compact cache/meta file signatures.
- GUI no longer calls `level_cache.ensure_level_cache()` from the chart process.
- GUI refresh does not parse raw Rithmic files.

INCREMENTAL_CACHE_UPDATE: PASS

- Daemon second-pass benchmark reused compact/source signatures.
- `raw_lines_processed_incremental=0`
- `incremental_full_raw_reparse=false`
- `incremental_update_ms=24.520`

FULL_RAW_REPARSE_REMOVED: PASS for GUI refresh and normal daemon no-change path. Full raw rebuild is available only as daemon fallback/forced rebuild, never in the GUI refresh loop.

VISIBLE_WINDOW_RENDERING: PASS

BATCH_RENDERING: PASS

- Rendering uses one reused `BookFlowLevelCellItem` for visible cells rather than per-cell `QGraphicsItem`s.
- GUI benchmark backend: `compact-cache-visible-window`

VOLUME_PROFILE_THROTTLED: PASS

- Profile/level overlay recomputation is keyed by data signature, visible window, and overlay toggles.

PULLS_PANEL_THROTTLED: PASS

- Pulls panel groupby is keyed by data signature, visible window, cell count, and panel toggle.

VIEW_LOCK_STILL_WORKS: PASS

FOLLOW_LIVE_STILL_WORKS: PASS

Chart semantics:

- `MAIN_VISUAL_TYPE=PRICE_AXIS_BOOK_FLOW_LEVEL_CANDLES`
- `Y_AXIS=price`
- `CELLS_SOURCE=raw Rithmic depth + bid/ask quote updates`
- `PRICE_OHLC_USED_AS_MAIN_CANDLES=false`
- `MLOFI_USED_AS_MAIN_CANDLES=false`
- `TOP_DEPTH_STILL_SUPPORTED=PASS`
- `CHART_SEMANTICS_CHANGED=false`

Launcher:

- `/home/prabh/OFI_Production/launch_book_flow_chart.sh` now starts or verifies `book_flow_cache_daemon.py` before launching the GUI.
- Writes daemon PID to `/tmp/book_flow_cache_daemon_pid.txt`.
- Writes daemon log to `/tmp/book_flow_cache_daemon_<timestamp>.log`.

## Validation

Compile:

- `python -m py_compile book_flow_chart_v3.py`: PASS
- `python -m py_compile book_flow_cache_daemon.py`: PASS
- `python -m py_compile build_book_flow_level_cache.py`: PASS
- `bash -n launch_book_flow_chart.sh`: PASS

Daemon once:

- Command: `/home/prabh/.venvs/ofi/bin/python -u /home/prabh/OFI_Production/book_flow_chart/book_flow_cache_daemon.py --once --symbol NQU6 --date latest --depth all`
- Result: PASS
- `elapsed_ms=232.799`
- `full_raw_reparse=false` for all compact cache outputs in this run

Smoke:

- Command: `timeout 20s /home/prabh/.venvs/ofi/bin/python -u /home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py --smoke-test`
- Result: `SMOKE_TEST=PASS`
- Compact cache path verified: `/home/prabh/OFI_Production/book_flow_chart/cache/NQU6_2026-06-15_top5.parquet`

Daemon benchmark:

- Command: `/home/prabh/.venvs/ofi/bin/python -u /home/prabh/OFI_Production/book_flow_chart/book_flow_cache_daemon.py --benchmark --symbol NQU6 --date latest --depth 20`
- Result: `CACHE_DAEMON_BENCHMARK=PASS`

BENCHMARK_BACKFILL_MS: `27.542`

BENCHMARK_INCREMENTAL_MS: `24.520`

GUI benchmark:

- Command: `/home/prabh/.venvs/ofi/bin/python -u /home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py --benchmark --bars 1000 --depth 20`
- Result: `BENCHMARK_RESULT=PASS`

BENCHMARK_RENDER_MS: `84.351`

Additional GUI benchmark details:

- `load_ms=41.891`
- `render_prepare_ms=1.672`
- `gui_update_ms=127.990`
- `visible_bars=75`
- `visible_cells=9114`
- `backend=compact-cache-visible-window`

TRADING_ENABLED: false

Files not touched:

- parser
- scheduler
- master files
- model artifacts
- trading flags
- dashboard model logic

OVERALL PASS
