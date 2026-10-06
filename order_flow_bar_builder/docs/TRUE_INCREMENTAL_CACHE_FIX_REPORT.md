# True Incremental V3 Book-Flow Level Cache Fix Report

Generated: 2026-06-16

---

## APP_PATH:
`/home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py`

## CACHE_DAEMON_PATH:
`/home/prabh/OFI_Production/book_flow_chart/book_flow_cache_daemon.py`

## CACHE_BUILDER_PATH:
`/home/prabh/OFI_Production/book_flow_chart/build_book_flow_level_cache.py`

## LAUNCHER_PATH:
`/home/prabh/OFI_Production/launch_book_flow_chart.sh`

## BACKUP_DIR:
`/home/prabh/OFI_Production/backups/book_flow_true_incremental_cache_fix_20260616_194810/`

## BACKUP_CREATED:
YES — BACKUP_MANIFEST.txt written, all 4 files backed up.

---

## ROOT_CAUSE:
`build_level_caches()` in `build_book_flow_level_cache.py` had no persistent checkpoint.
The `_cache_current()` check compared `bars_count == len(bars)` and `last_bar_end_ts_ns`.
On every new vol500 bar this check failed, triggering `_read_merged_quote_events()` which
read ALL ~4.3 GB of bid + ask quote update files from byte 0. Took ~156–168 seconds per
daemon cycle, so the cache could never catch up with live bars.

---

## INITIAL_BACKFILL_REQUIRED:
YES — no checkpoint existed, one full replay ran to bootstrap book state.

## INITIAL_BACKFILL_SECONDS:
167.8 seconds (21,545,740 lines processed, 893 bars written)

## CHECKPOINT_STATE_PATH:
`/home/prabh/OFI_Production/book_flow_chart/cache/state/NQU6_2026-06-15_v3_level_state.pkl`

## CHECKPOINT_CREATED:
YES — written atomically after initial backfill.

## BOOK_STATE_PERSISTED:
YES — `bid_sizes[80000]` and `ask_sizes[80000]` numpy float64 arrays (full tick-indexed book),
765 active bid ticks and 1024 active ask ticks as of last checkpoint.
Inode tracking for rotation detection: bid_inode=107903737, ask_inode=107903738.

## RAW_OFFSETS_PERSISTED:
YES — `bid_file_offset` and `ask_file_offset` saved per run.
After initial backfill: bid_offset=4680351991, ask_offset=4728407734.
After 2nd run: bid_offset=4716522996, ask_offset=4755989079.
After 3rd run: bid_offset=4729941479, ask_offset=4767921034.

## FULL_RAW_REPLAY_AFTER_CHECKPOINT:
NO — verified across 3 consecutive incremental runs after checkpoint.

---

## INCREMENTAL_UPDATE_ENABLED:
YES — `incremental_update: true` in all runs after initial backfill.

## INCREMENTAL_UPDATE_SECONDS:
- 2nd run: **1.35 seconds** (145,874 lines, 12 bars)
- 3rd run: **0.55 seconds** (39,038 lines, 6 bars)

## RAW_LINES_PROCESSED_INCREMENTAL:
- 2nd run: 145,874 (vs 21,545,740 for initial backfill — 148× fewer)
- 3rd run: 39,038

## AFFECTED_BARS_INCREMENTAL:
- 2nd run: 12
- 3rd run: 6

---

## LATEST_FEATURE_BAR_IDX:
1567 (as of 2nd run)

## CACHE_LAST_BAR_IDX_BY_DEPTH:
`{5: 1567, 10: 1567, 15: 1567, 20: 1567}` (all depths in sync, lag=0 after 2nd run)

## CACHE_LAG_BARS_BY_DEPTH:
- After 2nd run: `{5: 0, 10: 0, 15: 0, 20: 0}` — fully caught up
- After 3rd run: `{5: 1, 10: 1, 15: 1, 20: 1}` — 1 bar lag (forming bar, expected)

---

## HEARTBEAT_STATUS:
PASS (incremental=true, lag≤1, no errors, latency < 2 seconds)

New heartbeat fields added:
- `incremental_update` — true/false
- `full_raw_replay` — true/false
- `raw_lines_processed_this_run` — lines read this cycle
- `affected_bars_this_run` — bars written this cycle
- `raw_bid_offset` / `raw_ask_offset` — current file offsets
- `raw_bid_size` / `raw_ask_size` — current file sizes
- `checkpoint_path` / `checkpoint_exists`

---

## GUI_READS_CACHE_ONLY:
YES — GUI reads compact parquet only, no raw file access.

## GUI_RELOADS_ON_HEARTBEAT:
YES — CacheFileMonitor watches heartbeat mtime; status bar shows `INC`/`FULL_REPLAY`,
latency, and lag warning flag (`⚠ LAG` if lag > 1).

## VIEW_LOCK_STILL_WORKS:
YES — smoke test: PAN_ZOOM_PRESERVES_VIEW=true, CURSOR_MOVE_PRESERVES_VIEW=true

## FOLLOW_LIVE_STILL_WORKS:
YES — smoke test: FOLLOW_LIVE_STARTS_CHECKED=true, FOLLOW_LIVE_CHECKBOX_STAYS_CHECKED=true

## CHART_SEMANTICS_CHANGED:
NO — only heartbeat status display changed (added INC/lag info). Candle logic untouched.

---

## PY_COMPILE_CACHE_DAEMON:
PASS

## PY_COMPILE_CACHE_BUILDER:
PASS

## PY_COMPILE_APP:
PASS

## LAUNCHER_BASH_CHECK:
PASS

## SMOKE_TEST:
PASS — `SMOKE_TEST=PASS` from `book_flow_chart_v3.py --smoke-test --date latest`

## TRADING_ENABLED:
NO — not touched. All trading flags, model artifacts, live data services unchanged.

---

## Algorithm Summary

**`incremental_build_level_caches()`** added to `build_book_flow_level_cache.py`:

1. Load checkpoint pickle from `cache/state/NQU6_{date}_v3_level_state.pkl`
2. Detect file rotation/truncation via inode + size check
3. If no checkpoint (or force): initial backfill from byte 0 using `depth.ndjson` for
   initial book state, then stream full bid+ask files
4. If checkpoint exists: seek bid/ask files to saved byte offsets, read only appended lines
5. Reconstruct `active_bid`/`active_ask` sorted lists from saved `bid_sizes`/`ask_sizes` arrays
6. Process only new events; accumulate into `forming_bar_aggs` (partial open bar) + new bars
7. Load existing parquet, drop rows for `bar_idx >= forming_bar_idx`, append new rows
8. Write compact parquet atomically (`.parquet.tmp` → rename)
9. Save checkpoint atomically (`.pkl.tmp` → rename)

**`build_level_caches()`** now delegates to `incremental_build_level_caches()` by default.
Full replay preserved as `_build_level_caches_full_replay()` for reference.

**Daemon `ensure_compact_caches()`** updated:
- Removed `allow_rebuild` gate (incremental builder is always safe, returns fast if nothing new)
- Always calls `build_level_caches()` each cycle; relies on offset check for early exit
- Propagates `incremental_update`, `full_raw_replay`, `raw_lines_processed_this_run`,
  `affected_bars_this_run`, `raw_bid_offset`, `raw_ask_offset` into heartbeat

---

## Performance Comparison

| Metric               | Before (full replay) | After (incremental) |
|----------------------|---------------------|---------------------|
| Per-cycle latency    | ~156–168 seconds    | 0.5–1.4 seconds     |
| Lines processed      | 21,545,740          | 39,000–146,000      |
| Full raw replay      | YES (every cycle)   | NO (after backfill) |
| Cache lag at steady  | 2+ bars behind      | 0–1 bar (forming)   |
| Status               | BLOCKED             | PASS                |

---

## OVERALL: PASS

- Backup created before any changes ✓
- Initial backfill ran once and saved checkpoint ✓
- Second run after checkpoint was truly incremental (not full replay) ✓
- Third run confirmed steady-state at 0.55s ✓
- Cache lag reaches 0 (or 1 = forming bar) in incremental mode ✓
- GUI follows cache heartbeat, shows INC/FULL_REPLAY status ✓
- Chart semantics unchanged ✓
- Trading remains disabled ✓
