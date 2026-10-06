# FAST MARKET PERFORMANCE FIX REPORT

Timestamp UTC: 2026-06-16T18:13:58Z

APP_PATH: `/home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py`

BACKUP_DIR: `/home/prabh/OFI_Production/backups/book_flow_v3_fast_market_perf_20260616T181358Z`

BACKUP_CREATED: PASS

Backed up before this request's code patch:

- `/home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py`
- `/home/prabh/OFI_Production/book_flow_chart/build_book_flow_level_cache.py`
- `/home/prabh/OFI_Production/launch_book_flow_chart.sh`
- `/home/prabh/OFI_Production/backups/book_flow_v3_fast_market_perf_20260616T181358Z/BACKUP_MANIFEST.txt`

Old chart process stop:

- `pkill -f '[b]ook_flow_chart_v3.py' || true`: PASS
- `pkill -f '[l]aunch_book_flow_chart.sh' || true`: PASS
- Post-stop process check: PASS, no matching process remained

PERF_ROOT_CAUSE: The lag came from refresh/render-loop work being too broad during bursts: full cached parquet/bar reloads, visible-data rebuilds, cell lookup rebuilds, level/profile recomputation, pulls-panel groupby, and render updates were triggered too often. The cache worker also checked cache freshness on every interval even when file signatures were unchanged.

FULL_RAW_REPARSE_ON_REFRESH: NO. The GUI refresh path does not read raw files. Current-cache checks use metadata before raw support inspection, and unchanged worker cycles are skipped by file signature.

FULL_CACHE_REBUILD_ON_REFRESH: NO. The GUI reloads cached parquet/bars only when cache or vol500 file signatures change. Full stale-cache rebuilds are not performed on GUI refresh.

GUI_THREAD_HEAVY_WORK: PROTECTED. GUI work is limited to cached-data loading on file-signature changes and visible-window render preparation. Raw parsing/cache build work remains in the worker/background path.

INCREMENTAL_READER_ENABLED: PASS. Refresh uses cache/input mtime-size signatures and existing offset-safe raw readers are kept out of the GUI path.

CACHE_INCREMENTAL_UPDATE_ENABLED: PASS for the refresh/render path. Current cache metadata short-circuits raw inspection and row counting; unchanged worker cycles skip cache builder calls. If cache is genuinely stale, rebuild work is background-worker work, not GUI-refresh work.

RENDER_THROTTLING_ENABLED: PASS

MAX_RENDER_FPS: `OFI_BOOK_FLOW_MAX_FPS`, default `4`

FRAME_COALESCING_ENABLED: PASS. `_queue_reload()` combines burst updates into one render.

REENTRANCY_GUARD_ENABLED: PASS. `_refresh_busy` skips/coalesces overlapping refreshes and increments skipped-frame stats.

VISIBLE_WINDOW_RENDERING_ENABLED: PASS. The renderer selects visible bars plus margin, with `OFI_BOOK_FLOW_MAX_VISIBLE_BARS` default `900`.

MAX_VISIBLE_CELLS: `OFI_BOOK_FLOW_MAX_VISIBLE_CELLS`, default `120000`

OBJECT_REUSE_OR_BATCH_RENDERING_ENABLED: PASS. Existing cell, curve, bar, and volume-profile graphics items are reused; no thousands of per-cell `QGraphicsItem`s are created.

VOLUME_PROFILE_THROTTLED: PASS. Level/profile recomputation is keyed by data signature, visible window, and overlay toggles.

PULLS_PANEL_THROTTLED: PASS. Pulls-panel groupby is keyed by data signature, visible window, cell count, and panel toggle.

VIEW_LOCK_STILL_WORKS: PASS

FOLLOW_LIVE_STILL_WORKS: PASS

CHART_SEMANTICS_CHANGED: false

PRICE_OHLC_USED_AS_MAIN_CANDLES: false

MLOFI_USED_AS_MAIN_CANDLES: false

TOP_DEPTH_STILL_SUPPORTED: PASS (`[5, 10, 15, 20]`)

PY_COMPILE: PASS

- `env PYTHONPYCACHEPREFIX=/tmp/ofi_pycompile_cache python -m py_compile /home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py`
- `env PYTHONPYCACHEPREFIX=/tmp/ofi_pycompile_cache python -m py_compile /home/prabh/OFI_Production/book_flow_chart/build_book_flow_level_cache.py`
- `bash -n /home/prabh/OFI_Production/launch_book_flow_chart.sh`

SMOKE_TEST: PASS

- Command: `timeout 20s /home/prabh/.venvs/ofi/bin/python -u /home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py --smoke-test`
- Result: `SMOKE_TEST=PASS`
- Verified: raw depth reconstruction, top-depth support, level overlays, volume blueprint, view lock, follow-live, reset view, dashboard V3 launch wiring, performance stats, visible-window rendering, render throttle, re-entrancy guard.

BENCHMARK_RESULT: PASS

- Command: `/home/prabh/.venvs/ofi/bin/python -u /home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py --benchmark --bars 1000 --depth 20`
- `load_ms=59.284`
- `compute_ms=1.867`
- `render_prepare_ms=1.867`
- `render_ms=111.694`
- `gui_update_ms=172.921`
- `visible_bars=75`
- `visible_cells=9114`
- `estimated_refresh_ms=172.921`
- `bottleneck_component=render`
- `backend=cached-parquet-visible-window`

TRADING_ENABLED: false

Files not touched:

- parser
- scheduler
- master files
- model artifacts
- trading flags
- dashboard model logic

OVERALL PASS
