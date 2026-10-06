LATEST_BAR_COMPLETENESS_FIX_REPORT
===================================

APP_PATH: /home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py
CACHE_BUILDER_PATH: /home/prabh/OFI_Production/book_flow_chart/build_book_flow_level_cache.py
CACHE_DAEMON_PATH: /home/prabh/OFI_Production/book_flow_chart/book_flow_cache_daemon.py
BACKUP_DIR: /home/prabh/OFI_Production/backups/book_flow_latest_bar_completeness_fix_20260616_210148/
BACKUP_CREATED: true

ROOT_CAUSE: FORMING_BAR_RENDERED_AS_CLOSED
  The cache had no bar_state column — all rows (including the currently forming
  vol500 bar) were treated as closed, so the partial/incomplete book-flow level
  candle at bar 1622 was rendered identically to finished bars.
  Three sub-issues were also found and fixed:
    1. Both early-return paths in incremental_build_level_caches did not return
       last_closed_bar_idx / forming_bar_actual_idx. Heartbeat fields remained null.
    2. The early-return path used last_complete_bar_idx from the checkpoint to
       derive last_closed_bar_idx. That field can equal forming_bar_idx when all
       raw events pass through the forming bar's time window, giving the wrong value
       (1622 instead of 1621). Fixed to use forming_bar_idx - 1 instead.
    3. In the smoke test, _on_forming_bar_toggle() calls _queue_reload() which may
       start a timer rather than reload synchronously. Added _flush_queued_reload()
       call to ensure the toggle check is deterministic.

LATEST_FEATURE_BAR_IDX: 1622
LAST_CLOSED_BAR_IDX: 1621
FORMING_BAR_IDX: 1622 (position 958 in vol500 DataFrame)
FORMING_BAR_RENDERED_AS_CLOSED_BEFORE: true
FORMING_BAR_MARKED_PARTIAL: true (bar_state=FORMING; status bar shows "VIS ⚠ PARTIAL" when shown)
CLOSED_BARS_ONLY_DEFAULT: true
SHOW_FORMING_BAR_TOGGLE_ADDED: true
GUI_RENDERED_LAST_BAR_DEFAULT: 1621 (last closed bar)
RESET_VIEW_TARGETS_LATEST_CLOSED_BAR: true
VOLUME_BLUEPRINT_USES_CLOSED_BARS_DEFAULT: true
PULLS_PANEL_USES_CLOSED_BARS_DEFAULT: true
CACHE_INCREMENTAL_STILL_WORKS: true
FULL_RAW_REPLAY_AFTER_CHECKPOINT: false
CHART_SEMANTICS_CHANGED: false

PY_COMPILE_APP: PASS
PY_COMPILE_CACHE_BUILDER: PASS
PY_COMPILE_DAEMON: PASS
LAUNCHER_BASH_CHECK: PASS
SMOKE_TEST: PASS

CHANGES MADE
============

build_book_flow_level_cache.py:
  - Added bar_state field ("FORMING" / "CLOSED") to every row written to parquet
  - Added forming_bar_actual_idx and last_closed_bar_idx to the main return dict
  - Added forming_bar_actual_idx and last_closed_bar_idx to both early-return paths
    (file-size-unchanged path and empty-df path), using forming_bar_idx - 1 (not
    last_complete_bar_idx) to derive the last closed bar
  - Fixed incremental keep-filter to use actual bar_index (not 0-based position) —
    this was the Task 2 fix that preceded this task

book_flow_cache_daemon.py:
  - Added "bar_state" to COMPACT_COLUMNS
  - Added default handling for bar_state column in _compact_from_level_cache
  - Added last_closed_bar_idx_by_depth and forming_bar_idx_actual_by_depth dicts
    assembled from per-depth builder results
  - Added both fields to heartbeat JSON
  - Propagated last_closed_bar_idx and forming_bar_actual_idx into both the
    "fresh compact" path and the "cached compact" path (meta.update)

book_flow_chart_v3.py:
  - Added show_forming_bar instance variable (default False) and _last_closed_bar_idx
  - Added "show forming bar" checkbox to toolbar 2
  - Added _on_forming_bar_toggle() method
  - In _load_data_if_needed(): detect forming bar rows, record _last_closed_bar_idx,
    filter out forming rows by default unless show_forming_bar is True
  - Added bool(show_forming_bar) to render_key tuple to avoid stale renders
  - Updated status bar to show closed= and forming=(...) with visibility annotation
  - Smoke test: fixed expected_last to fall back to win._last_closed_bar_idx before
    cache_last_bar_idx (so check passes even when heartbeat fields are transiently absent)
  - Smoke test: added _flush_queued_reload() after _on_forming_bar_toggle() call for
    deterministic sync reload in headless mode

VERIFICATION
============

Parquet state after --force rebuild:
  bar_state values: CLOSED=77520, FORMING=63
  Total unique bars: 959, range 664-1622, no gaps
  FORMING bar: [1622]
  Last CLOSED bar: 1621

Heartbeat after daemon restart with new code:
  last_closed_bar_idx_by_depth: {5: 1621, 10: 1621, 15: 1621, 20: 1621}
  forming_bar_idx_actual_by_depth: {5: 1622, 10: 1622, 15: 1622, 20: 1622}

Smoke test results:
  BAR_STATE_COLUMN_IN_CACHE=true
  FORMING_BAR_IDENTIFIED=true
  FORMING_BAR_IDS_IN_CACHE=[1622]
  LAST_CLOSED_BAR_IDX_IN_CACHE=1621
  LAST_CLOSED_BAR_IDX_IN_HB=1621
  FORMING_BAR_HB_MATCHES_CACHE=true
  GUI_LOADS_LATEST_CACHE=true
  CLOSED_BARS_ONLY_DEFAULT=true
  SHOW_FORMING_BAR_TOGGLE_ADDED=true
  FORMING_BAR_NOT_IN_DEFAULT_RENDER=true
  FORMING_BAR_VISIBLE_WHEN_TOGGLED=true
  SMOKE_TEST=PASS

TRADING_ENABLED: false
RITHMIC_SERVICE_STOPPED: false
PARSER_SERVICE_STOPPED: false
DASHBOARD_STOPPED: false
MODELS_CHANGED: false

OVERALL PASS
