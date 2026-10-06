BOOK FLOW TWO-PART FIX REPORT
==============================

SPEC:     /home/prabh/OFI_Production/BOOK_FLOW_TWO_PART_FIX_SPEC.md
REPORTED: 2026-06-18T07:57 UTC
SESSION:  NQU6 2026-06-17 (active — session ends Jun 18 ~21:00 UTC)

================================================================
BACKUP
================================================================

BACKUP_PATH: /home/prabh/OFI_Production/backups/book_flow_two_part_fix_20260618_074148/
  book_flow_cache_daemon.py
  book_flow_chart_v3.py
  build_book_flow_level_cache.py

================================================================
CHANGES MADE
================================================================

FILE: build_book_flow_level_cache.py  (Bug 1 — closed-bar parity)

  Added _CLOSED_GAP_NS = 3_000_000_000 (3-second clock buffer for raw-file
  flush lag) and closure _resolve_forming_and_closed(fb_pos) inside
  incremental_build_level_caches().

  Logic: if forming_bar_idx points at the last vol500 bar AND that bar's
  bar_end_ts_ns is >3 seconds in the past, the bar is truly closed — return
  (None, bar_index) instead of marking it FORMING.

  After the main event loop, added a corresponding clock-based check:
  if new_forming_bar_idx == len(bars)-1 and bar_end_ts < now-3s, push that
  position into newly_complete and advance new_forming_bar_idx to len(bars)
  (sentinel = no forming bar within vol500 range).

  State save clamps sentinel back to len(bars)-1 so the 3-second check
  re-fires on the next compact cycle; no state migration needed.

FILE: book_flow_cache_daemon.py  (Bug 2 — live forming bar for synthetic next)

  Added _OVERFLOW_GAP_NS = 3_000_000_000 and flag _last_vol500_closed at the
  top of build_forming_bar_cache().

  In the main event loop, when bar_i advances to len(bars) (overflow past the
  last vol500 bar), save _first_overflow_ev = ev_i and break instead of
  silently discarding events.

  After the loop, when _last_vol500_closed and overflow events exist, run a
  second pass over those events into overflow_aggs using the same side/tick/
  rank logic. This builds OFI aggregates for the synthetic bar N+1.

  Output selection: when _last_vol500_closed, write bar_idx = last_actual+1,
  bar_start_ts_ns = last_bar_end_ts_ns, bar_end_ts_ns = 0 (open-ended),
  bar_state = "FORMING", is_synthetic_next_bar = True.

  When no overflow events, write empty parquet (clears stale data).

FILE: book_flow_chart_v3.py  (GUI — rendering guard + synthetic bars_df entry)

  Changed forming cache acceptance from:
    forming_fast_bar > last_closed_in_compact
  to strict:
    forming_fast_bar == last_closed_in_compact + 1

  This prevents any already-CLOSED bar from ever being overwritten by a
  forming cache entry.

  Added: when forming_fast_bar > last_vol500_bar_idx, insert a synthetic
  bars_df row so the forming bar is visible on the x-axis without touching
  vol500 or any existing bar row.

================================================================
A. CLOSED BAR VALIDATION
================================================================

latest_closed_vol500_bar_idx:       2829
  (vol500 last line: bar_end_ts_ns = 1781768980542243000,
   end_utc = 2026-06-18T07:49:40.542243+00:00, 145 bars total)

latest_closed_ofi_bar_idx:          2829
  (compact cache max_bar_idx = 2829, all 15700 rows bar_state = CLOSED)

last_10_closed_bar_idx_matches:     True
  vol500 bars 2820-2829 all present in compact as CLOSED
  vol500 end timestamps match compact bar_end_ts_ns exactly:
    bar 2820 end: 2026-06-18T07:04:10.933130 UTC
    bar 2821 end: 2026-06-18T07:06:51.831111 UTC
    bar 2822 end: 2026-06-18T07:12:03.111441 UTC
    bar 2823 end: 2026-06-18T07:15:59.630326 UTC
    bar 2824 end: 2026-06-18T07:20:52.722687 UTC
    bar 2825 end: 2026-06-18T07:26:42.213654 UTC
    bar 2826 end: 2026-06-18T07:30:56.904437 UTC
    bar 2827 end: 2026-06-18T07:37:02.217556 UTC
    bar 2828 end: 2026-06-18T07:43:04.116643 UTC
    bar 2829 end: 2026-06-18T07:49:40.542243 UTC

last_10_closed_timestamp_matches:   True

last_10_closed_bars_partial_count:  0
  (FORMING rows in compact cache = 0; all 15700 rows are CLOSED)

closed_bar_render_source:           COMPACT_CACHE_ONLY
  (forming_bar_actual_idx = null in builder_result; no forming data merged
   into closed bars; GUI reads compact directly)

closed_bar_parity_pass:             PASS

================================================================
B. FORMING BAR VALIDATION
================================================================

latest_closed_bar_idx:              2829
forming_bar_idx:                    2830  (= 2829 + 1, synthetic next bar)
forming_bar_valid:                  True
  is_synthetic_next_bar = True
  bar_start_ts_ns = 1781768980542243000 (= bar 2829 end)
  bar_end_ts_ns   = 0 (open-ended, bar not yet in vol500)

forming_raw_updates_processed:      11
  (new_events_since_checkpoint = 11 at forming cycle 07:53:13 UTC;
   11 overflow events past bar 2829 close processed into overflow_aggs;
   depth-20 cache has 2 rows for bar 2830)

latest_raw_book_timestamp_utc:      2026-06-18T07:57:01.332717+00:00
  (event_ts_ns = 1781769421332717000 from last line of bid_quote_updates.ndjson)

forming_bar_timestamp_utc:          2026-06-18T07:49:40.542243+00:00
  (forming bar 2830 starts at bar 2829's close time)

forming_lag_seconds:                -225.76
  (per heartbeat; negative = forming cache is 226s newer than last closed
   bar end timestamp; HEAD_TO_HEAD_OK)

forming_rendered_in_gui:            True
  (strict == last_closed+1 guard accepts bar 2830; synthetic bars_df entry
   added for x-axis; bar_state = FORMING)

forming_bar_pass:                   PASS

================================================================
C. GUI RENDERING VALIDATION
================================================================

any_closed_bar_rendered_from_forming_cache:  False
  (strict forming_fast_bar == last_closed+1 guard; if forming cache ever
   contained a bar <= last_closed it would be silently discarded)

stale_forming_cache_ignored:        True
  (if forming_bar_idx jumps ahead or behind last_closed+1, the whole forming
   cache entry is dropped; compact-only data is displayed instead)

rendered_latest_closed_bar_idx:     2829
rendered_forming_bar_idx:           2830

show_forming_bar_state:             FORMING
  (bar_state = "FORMING", is_synthetic_next_bar = True in forming cache meta)

overall_rendering_pass:             PASS

================================================================
COMPILE CHECKS
================================================================

PY_COMPILE_LEVEL_CACHE:  PASS  (python -m py_compile build_book_flow_level_cache.py)
PY_COMPILE_DAEMON:       PASS  (python -m py_compile book_flow_cache_daemon.py)
PY_COMPILE_CHART:        PASS  (python -m py_compile book_flow_chart_v3.py)

================================================================
DAEMON STATE
================================================================

Daemon (re)started with patched code after verification.
Compact cycle: every 2s, INCREMENTAL, forming_bar_actual_idx = null (no partial bars)
Forming sub-cycle: every 350ms, bar 2830 tracked as synthetic next bar
State checkpoint: cache/state/NQU6_2026-06-17_v3_level_state.pkl
  bid_file_offset = 914554733 (= bid_file_size, fully consumed at last cycle)
  ask_file_offset = 934407250 (= ask_file_size, fully consumed at last cycle)
  forming_bar_idx = 144 (last vol500 bar position; 3s check re-fires each cycle)
  last_complete_bar_idx = 144 (all 145 vol500 bars complete)

================================================================
FILES NOT TOUCHED
================================================================

model artifacts:         unchanged
parser:                  unchanged
scheduler:               unchanged
ofi-rithmic.service:     unchanged
master files:            unchanged
trading flags:           unchanged
dashboard model tabs:    unchanged
candle semantics:        unchanged
shadow_only:             true (not applicable to book flow — no trading)

================================================================
OVERALL RESULT
================================================================

Bug 1 (closed-bar parity):   PASS — zero FORMING rows in compact; all 145 bars CLOSED
Bug 2 (live forming bar):    PASS — bar 2830 is synthetic, built from overflow events,
                                     is_synthetic_next_bar=True, forming_bar_idx=last_closed+1
GUI rendering:               PASS — strict guard, no closed bar overwritten by forming cache

OVERALL: PASS
