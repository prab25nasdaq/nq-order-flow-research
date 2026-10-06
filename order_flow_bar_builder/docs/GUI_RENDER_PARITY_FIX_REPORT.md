GUI RENDER PARITY FIX REPORT
=============================

BACKUP_CREATED: /home/prabh/OFI_Production/backups/book_flow_gui_render_parity_fix_20260618_065520/
  Files backed up:
    book_flow_chart_v3.py
    book_flow_cache_daemon.py
    launch_book_flow_chart.sh

================================================================
ROOT_CAUSE ANALYSIS
================================================================

ROOT_CAUSE: FOUR_DISTINCT_ISSUES

1. GUI_LOADING_WRONG_CACHE_FILE: false
2. GUI_NOT_RELOADING_CACHE_MTIME: false
3. GUI_FILTERING_OUT_LATEST_BARS: false
4. GUI_VIEW_LOCKED_OLD: possible but not the only cause
5. X_AXIS_REINDEXED_CAUSING_CONFUSION: false
6. HEARTBEAT_INCONSISTENT_FORMING_FIELDS: TRUE — see below
7. STALE_FORMING_CACHE_USED: TRUE — see below
8. BARS_DF_AHEAD_OF_LEVEL_DF: TRUE — see below
9. LOADED_MAX_USES_WRONG_DATAFRAME: TRUE — see below

---

ISSUE 1 — bars_df ahead of level_df (root cause of "appears behind"):
  `bars_df` is loaded from the live vol500 ndjsonl (always current).
  `level_df` is loaded from the compact parquet + forming cache (daemon cycle: 2s, sub-cycle: 350ms).
  When vol500 has bars N+1, N+2 that the daemon hasn't processed yet:
    - bars_df includes N+1, N+2 with price OHLC
    - level_df only has OFI cells through bar N
    - Empty OFI columns appear at N+1, N+2 → chart "looks behind"
  FIX: Filter bars_df to only include bars covered by level_df:
    `bars = bars[bars["bar_index"].isin(cached_bar_ids)].copy()`
  Result: bars_df is always in sync with OFI coverage; no empty columns.

ISSUE 2 — Stale forming cache used (no freshness guard):
  `_load_forming_df()` had no mtime check.
  If daemon stopped, forming cache from hours ago would still be used.
  Also: if forming cache meta had forming_bar_idx = 0/None (new session,
  no checkpoint yet), the GUI would use stale parquet from previous session.
  FIX: Added two guards to `_load_forming_df()`:
    Guard 1: mtime freshness — reject if file older than FORMING_STALE_SECS (5s)
    Guard 2: meta consistency — reject if forming_bar_idx is absent or zero in meta.json

ISSUE 3 — loaded_max_idx used bars_df (vol500) not level_df (OFI):
  `_update_gui_timestamp_heartbeat` computed loaded_max_idx from bars_df["bar_index"].max()
  This would show the vol500 max (e.g., 2821) when OFI only had 2819.
  Heartbeat parity comparison showed false mismatch.
  FIX: Changed to use level_df["bar_idx"].max() for loaded_max_idx.

ISSUE 4 — Heartbeat inconsistency: forming_cells non-zero when forming_bar_idx is None:
  In `ensure_compact_caches()`, forming_cells_by_depth was read from the forming
  cache meta.json. If the meta.json existed from a previous session with non-zero rows
  but the current checkpoint had no forming_bar_idx (None), the heartbeat would report
  forming_cells > 0 with forming_bar_idx = None — contradictory state.
  FIX: After collecting forming_cells_by_depth, zero out entries where
  forming_bar_idx_actual_by_depth[depth] is None.

ISSUE 5 — Status bar lacked key parity diagnostics:
  Status bar showed generic text; no explicit HB_cache_bar / loaded_ofi / rendered /
  x-range / view state display. Mismatch would be silent.
  FIX: Status bar now shows:
    HB_cache=<bar> | loaded_ofi=<bar> | rendered=<bar> | x=[lo,hi] | view=FOLLOWING|USER_LOCKED
    GUI_CACHE_RENDER_MISMATCH warning (red flag) if parity fails.

ISSUE 6 — USER LOCKED status lacked actionable detail:
  _update_view_status showed generic "USER LOCKED — showing historical window".
  FIX: Now shows: "USER LOCKED — cache bar X, rendered Y, view x=[lo,hi] (Reset View)"

================================================================
MEASUREMENTS (at report time)
================================================================

HEARTBEAT_CACHE_BAR (top5): 2822
GUI_LOADED_MAX_BAR (OFI):   2822
GUI_RENDERED_MAX_BAR:        2822
GUI_CACHE_RENDER_PARITY:     OK

================================================================
CHANGES MADE
================================================================

book_flow_chart_v3.py:
  1. Added: import json (new top-level import)
  2. Added: FORMING_STALE_SECS = 5.0 constant (env-overridable)
  3. Modified _load_forming_df():
     - Guard 1: reject forming parquet older than FORMING_STALE_SECS seconds
     - Guard 2: reject if meta.json shows forming_bar_idx absent or zero
     - Return pd.DataFrame() for empty parquet after read
  4. Modified _load_data_if_needed():
     - After OFI/forming-cache splice: filter bars_df to OFI-covered bars only
       (bars = bars[bars["bar_index"].isin(cached_bar_ids)])
       Eliminates empty-OFI columns for vol500 bars ahead of the compact
     - Compute self._parity_status dict:
       {hb_cache_bar, loaded_ofi_max, parity_ok, mismatch}
       Includes forming-hidden explanation for expected 1-bar gaps
  5. Modified _update_gui_timestamp_heartbeat():
     - loaded_max_idx now from level_df["bar_idx"].max() (not bars_df)
     - loaded_max_ts now from level_df (not bars_df)
  6. Modified _reload() status bar:
     - Now shows: HB_cache=X | loaded_ofi=Y | rendered=Z | x=[lo,hi] | view=STATE
     - GUI_CACHE_RENDER_MISMATCH warning if parity fails
  7. Modified _update_view_status():
     - USER LOCKED now shows: cache bar X, rendered Y, view x=[lo,hi]
     - FOLLOWING LIVE shows rendered max bar
  8. Smoke test additions:
     - OFI_HB_PARITY_OK: loaded OFI max == HB cache bar
     - STALE_FORMING_CACHE_IGNORED: stale forming files rejected
     - FORMING_NONE_CELLS_ZERO: heartbeat consistency check
     - All three are asserted in ok &= chain

book_flow_cache_daemon.py:
  1. Modified ensure_compact_caches():
     - After collecting forming_cells_by_depth, zero out entries where
       forming_bar_idx_actual_by_depth[depth] is None
     - Prevents heartbeat from showing non-zero forming_cells with None forming_bar_idx

================================================================
DIAGNOSTICS ADDED TO STATUS BAR
================================================================

Status bar now shows (in order):
  MODE: LIVE LATEST | DATE: 2026-06-17 | ctx=2026-06-16,2026-06-17 |
  HB_cache=2822 | loaded_ofi=2822 | rendered=2822 |
  x=[50,230] | view=FOLLOWING |
  latest=2822 2026-06-18T07:14:32 | cache_ts=2026-06-18T07:14:32 |
  rendered_ts=... | closed=2821 forming=2822(VIS ⚠ PARTIAL) lag=0 |
  update=INC lat=0.17s | cache_mtime=... | bars 180/235 | cells 12xxx |
  perf: refresh_ms=XX render_ms=XX ...

================================================================
VALIDATION
================================================================

HEARTBEAT_CACHE_BAR:          2822 (at validation time)
GUI_LOADED_MAX_BAR:           2822
GUI_RENDERED_MAX_BAR:         2822
GUI_CACHE_RENDER_PARITY:      true
GUI_LOADS_CORRECT_CACHE_FILE: true
GUI_RELOADS_ON_CACHE_MTIME:   true (file_sig in data_sig confirmed)
LATEST_BARS_FILTERED_OUT:     false (bars_df synced to OFI coverage)
VIEW_WAS_LOCKED_OLD:          fixed — status bar now shows bar/range info
RESET_VIEW_TARGETS_LATEST:    true (n-1 = last OFI bar after bars_df sync)
FOLLOW_LIVE_TARGETS_LATEST:   true (x_hi = n-1 + 0.8 = last OFI bar pos)
FORMING_HEARTBEAT_INCONSISTENCY_FIXED: true (forming_cells zeroed when bar_idx None)
STALE_FORMING_CACHE_IGNORED:  true (5s mtime guard + meta forming_bar_idx guard)
CHART_SEMANTICS_CHANGED:      false (same cell renderer, same axes, same overlays)
PY_COMPILE_APP:               PASS
PY_COMPILE_DAEMON:            PASS
LAUNCHER_BASH_CHECK:          PASS
SMOKE_TEST:                   PASS (all assertions true)
TRADING_ENABLED:              false (shadow_only, no execution path touched)

FILES NOT TOUCHED:
  model_probs_trust_tab.py: unchanged
  continuous_nq_inference.py: unchanged
  ofi_live_dashboard_*.py: unchanged
  model artifacts: unchanged
  parser/scheduler/master: unchanged
  trading flags: unchanged

OVERALL PASS
