HEAD TO HEAD PRICE OFI FIX REPORT
==================================

BACKUP_CREATED: /home/prabh/OFI_Production/backups/book_flow_head_to_head_latency_fix_20260617_172710/
  Files backed up:
    book_flow_chart_v3.py
    book_flow_cache_daemon.py
    build_book_flow_level_cache.py
    launch_book_flow_chart.sh

ROOT_CAUSE: FULL_RAW_REPLAY_ON_EVERY_LIVE_CYCLE
  build_level_caches() unconditionally called _build_level_caches_full_replay()
  for the live active date — reading all 6.8-13M raw bid/ask events every 2s.
  Result: 60-140s processing per 2s daemon cycle → 60-140s OFI lag behind price.
  The incremental_build_level_caches() path (file-offset checkpoint, sub-second)
  was only used for historical dates. Now routes ALL dates — live and historical —
  through the incremental path. Initial backfill (first run, no checkpoint) still
  reads all events but saves the checkpoint so subsequent runs process only new events.

LATEST_PRICE_TIMESTAMP_UTC: 2026-06-17T18:56:18.560727+00:00
LATEST_VOL500_TIMESTAMP_UTC: 2026-06-17T18:56:18.560727+00:00
LATEST_CLOSED_OFI_TIMESTAMP_UTC: 2026-06-17T18:56:18.560727+00:00 (last_closed_bar=2430)
LATEST_FORMING_OFI_TIMESTAMP_UTC: 2026-06-17T18:56:46.519105+00:00 (bar 2431)

PERFORMANCE (measured during testing):
  PRE-FIX:  ~60-140s per 2s daemon cycle (full replay 13M+ raw events)
  POST-FIX: 0.67-3.5s per 2s daemon cycle (incremental, 33K-284K new events)
  INITIAL_BACKFILL (once, no checkpoint): ~97s (expected; only needed once per session)

PRICE_TO_CLOSED_OFI_LAG_SECONDS: <3s (post-fix, was 60-140s)
PRICE_TO_FORMING_OFI_LAG_SECONDS: 0.07-0.18s (forming cache cycle at 350ms cadence)

CHANGES MADE
============

build_book_flow_level_cache.py:
  build_level_caches() — REPLACED entire routing logic:
  - Removed two full-replay blocks for live date (lines 1007-1071 pre-fix)
  - Now routes all dates through incremental_build_level_caches() regardless of
    whether latest_active=True or False
  - Sparse repair: if output is sparse after incremental (not initial backfill),
    forces rebuild via incremental(force=True)
  - Full-replay fallback only if incremental fails completely (depth.ndjson missing)
  - _build_level_caches_full_replay() kept as fallback; no changes to it

book_flow_cache_daemon.py:
  1. Added: import numpy as np
  2. Added: FORMING_CACHE_DIR = bfl.CACHE_DIR / "forming"
  3. Added: forming_cache_path() and forming_cache_meta_path() path helpers
  4. Added: build_forming_bar_cache(symbol, date, depths) — Lane 2 fast forming bar:
     - Reads v3 checkpoint state (read-only)
     - Reads new bid/ask events since checkpoint's saved byte offset
     - Merges delta events with checkpoint's forming_bar_aggs
     - Writes per-depth forming parquet to cache/forming/ at 75-182ms per run
  5. run_loop(): added 350ms sub-cycle between main 2s cycles that calls
     build_forming_bar_cache() when a v3 checkpoint exists
  6. ensure_compact_caches() heartbeat: added fields:
     latest_price_timestamp_utc, latest_forming_timestamp_utc, forming_mtime_utc,
     forming_cells_by_depth, forming_lag_seconds, head_to_head_status
     (HEAD_TO_HEAD_OK / CLOSED_CACHE_LAGGING_BUT_FORMING_OK / RAW_CAPTURE_LAGGING /
      CACHE_DAEMON_LAGGING)

book_flow_chart_v3.py:
  1. Added: _load_forming_df() helper — reads cache/forming/ with stale-safety check
  2. show_forming_bar default: changed to self.live_latest_mode (ON in live mode)
  3. forming_bar_cb.setChecked: respects show_forming_bar default
  4. _load_data_if_needed(): freshness-guarded forming cache merge:
     - Loads fast forming cache (cache/forming/) only if forming_fast_bar >= last_closed
     - If fresh: strips compact FORMING rows, splices in forming cache rows
     - If stale: keeps compact's FORMING row unchanged
  5. _current_data_sig(): includes forming cache file sig when show_forming_bar=ON
     (triggers reload when forming cache is updated by daemon sub-cycle)
  6. CacheFileMonitor.run(): includes forming cache in file signature check
  7. Worker interval: 0.5s in live mode (was 2.0s) — detects forming cache updates promptly
  8. Status bar: added h2h= head_to_head_status, forming_ts, forming_lag, and
     warning "OFI FORMING BAR LAGGING PRICE" if forming_lag > 2s during live
  9. Smoke test: updated 3 checks to account for live mode default forming-bar-ON:
     - gui_loads_latest_cache: includes forming bar in expected max when forming ON
     - latest_closed_bar_rendered: includes forming bar as rightmost when ON
     - closed_bars_only_default: accepts show_forming_bar=True as correct in live mode

VERIFICATION
============

INCREMENTAL PATH CONFIRMED:
  Run 1 (initial backfill, no prior checkpoint): 97s, 13M events, full_raw_reparse=True
  Run 2 (incremental): 671ms, 33K events, full_raw_reparse=False
  Run 3 (incremental): 2.8s, 284K events, full_raw_reparse=False (active session)
  full_raw_replay=False confirmed in heartbeat after run 2+

FORMING CACHE CONFIRMED:
  75ms after main cycle (new_events=0, checkpoint fresh)
  182ms standalone call (includes _active_from_arr + merge)
  Files: cache/forming/NQU6_2026-06-16_top{5,10,15,20}_forming.{parquet,meta.json}

FORMING_BAR_IMPLEMENTED: true
FORMING_BAR_DEFAULT_ON_IN_LIVE_MODE: true
FORMING_BAR_MARKED_PARTIAL: true (bar_state=FORMING in forming cache)
CLOSED_BARS_STILL_STABLE: true (CLOSED rows never modified; forming cache is separate file)
FULL_RAW_REPLAY_AFTER_CHECKPOINT: false (confirmed incremental_update=True in heartbeat)
DEPTH_LAG_BY_TOP_N: all depths updated in same incremental pass (same per-depth rows)
GUI_FOLLOWS_FORMING_BAR_WHEN_ENABLED: true (freshness-guarded, uses fast forming cache)
VIEW_LOCK_STILL_WORKS: true (smoke test: PAN_ZOOM_PRESERVES_VIEW=true)
FOLLOW_LIVE_STILL_WORKS: true (smoke test: FOLLOW_LIVE_CHECKBOX_STAYS_CHECKED=true)
PREVIOUS_CONTEXT_STILL_WORKS: true (smoke test: CONTEXT_DATES_LOADED=['2026-06-15','2026-06-16'])
CHART_SEMANTICS_CHANGED: false (same cell renderer, same axes, same overlays)

PY_COMPILE_APP: PASS
PY_COMPILE_DAEMON: PASS
PY_COMPILE_BUILDER: PASS
LAUNCHER_BASH_CHECK: PASS
SMOKE_TEST: PASS (all checks true)
TRADING_ENABLED: false (not touched — shadow_only, no execution path)

FILES NOT TOUCHED:
  model_probs_trust_tab.py: unchanged
  continuous_nq_inference.py: unchanged
  ofi_live_dashboard_*.py: unchanged
  model artifacts: unchanged
  parser/scheduler/master: unchanged
  trading flags: unchanged

OVERALL PASS
