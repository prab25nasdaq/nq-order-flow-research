# GUI Live Edge Fix Report

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
`/home/prabh/OFI_Production/backups/book_flow_gui_live_edge_fix_20260616_203035/`

## BACKUP_CREATED:
YES — 6 files backed up: build_book_flow_level_cache.py, book_flow_cache_daemon.py,
book_flow_chart_v3.py, launch_book_flow_chart.sh, corrupted_top5.parquet,
corrupted_checkpoint.pkl.

---

## ROOT_CAUSE:
`CACHE_STALE` — compound data corruption in the incremental parquet updater.

The `incremental_build_level_caches()` function in `build_book_flow_level_cache.py`
contained a one-line bug at line 694 in the `keep` filter:

```python
# BUGGY (before fix):
keep = existing[existing["bar_idx"] < forming_bar_idx]
```

`forming_bar_idx` is a **0-based bar position** (e.g., 948) in the vol500 DataFrame.
`bar_idx` is the **actual bar_index value** in the parquet (e.g., 664–1616, since
NQU6 session 2026-06-15 starts at bar_index=664).

The filter compared position 948 against bar_index values 664–1616. Bars at positions
228–950 have bar_index 892–1614, all greater than 948 (position), so they were dropped
from the parquet on every incremental run. Only bars with bar_index < 948 survived
(positions 0–283, bar_index 664–947), which maps to bar_index ≤ 947. Visually, the
parquet converged to showing only bars 664–891 (228 unique bars).

The daemon ran this buggy code every 2 seconds. After many cycles, all 4 depth parquets
were truncated to bar_idx range 664–891. The heartbeat was reporting `cache_last_bar_idx=1613`
(read from meta, which is set from vol500 last bar), while the actual parquet max was 891.
The GUI loaded the corrupted parquet and correctly displayed the live edge of THAT data —
bar_pos 227 out of 228 — which appeared far behind the live vol500 chart.

The GUI code itself was correct: CacheFileMonitor, _on_cache_updated, _update_view_ranges,
_reset_view all worked as intended. The only failure was the corrupted cache.

---

## DIAGNOSIS:

Cause classified as: **CACHE_STALE**

Sub-cause: Bug in incremental builder's `keep` filter mixing bar POSITION (0-based) with
bar INDEX values (session-offset, starting at 664).

Specific evidence:
- Parquet before fix: 228 unique bars, range 664–891
- position 227 = bar_index 891 exactly (664+227=891) — confirms position/index confusion
- Heartbeat falsely claimed `cache_last_bar_idx=1613` (reads meta, not parquet)
- GUI loaded 228 bars correctly per the (corrupted) parquet

Not causes:
- GUI did not have a view-lock bug (Reset View and Follow Live worked correctly)
- GUI was not running from an old process
- CacheFileMonitor was correctly detecting and signaling changes

---

## THE FIX:

**`build_book_flow_level_cache.py` line 694** — convert position → actual bar_index before
applying the `keep` filter:

```python
# FIXED:
forming_bar_actual_idx = int(bars.iloc[min(forming_bar_idx, len(bars) - 1)]["bar_index"])
keep = existing[existing["bar_idx"] < forming_bar_actual_idx]
```

**`book_flow_chart_v3.py` `_update_view_status()`** — improved locked-view message:
```python
# Before:
mode = "USER LOCKED"
# After:
mode = "USER LOCKED — showing historical window (press Reset View to follow latest)"
```

**Recovery**: After code fix, ran `book_flow_cache_daemon.py --once --force` to trigger
one clean initial backfill (22,195,839 lines, ~3 min), then ran `--once` (no force) to
verify incremental path with the fix applied.

---

## CACHE_LAG_BARS:
After fix: `{5: 0, 10: 0, 15: 0, 20: 0}` — fully caught up

## HEARTBEAT_FRESH:
YES — `run_utc=2026-06-16T20:36:43`, `status=PASS`,
`incremental_update=true`, `full_raw_replay=false`, `update_latency_sec=0.63s`

## GUI_LOADED_MAX_BAR:
1616 (verified via smoke test: `CACHE_LAST_BAR_IDX_BY_DEPTH={'5': 1616, ...}`)

## HEARTBEAT_CACHE_BAR:
1616 (matches GUI loaded max bar)

## GUI_LOADS_LATEST_CACHE:
`GUI_LOADS_LATEST_CACHE=true` (from smoke test)

## VIEW_WAS_LOCKED_OLD:
NO — GUI view lock logic was correct. Root cause was cache data, not view state.

## RESET_VIEW_JUMPS_TO_LIVE:
YES — `RESET_VIEW_STILL_WORKS=true` (from smoke test).
`_reset_view()` clears `user_view_override`, `user_view_locked`, sets `_initialized=False`,
then reloads; `_update_view_ranges()` with `follow_live_requested=True` sets
`x_hi = float(n-1) + 0.8` where n = total bars (e.g., 953) → jumps to bar_pos 952.

## JUMP_TO_LIVE_ADDED:
YES — Reset View button serves as Jump To Live (per spec: "Current Reset View can serve
as Jump To Live if simpler"). Behavior: clears user_view_locked, keeps Follow Live checked,
moves x-range to latest cache bar, does not reset candle semantics. Locked status message
updated to include "press Reset View to follow latest".

## FOLLOW_LIVE_STILL_WORKS:
YES — `FOLLOW_LIVE_STARTS_CHECKED=true`, `FOLLOW_LIVE_CHECKBOX_STAYS_CHECKED=true`

## CHART_SEMANTICS_CHANGED:
NO — only two changes: (1) 1-line fix in `keep` filter (no semantic change), (2) improved
"USER LOCKED" message text in view status label.

---

## Data Verification After Fix:

| Metric | Before Fix | After Fix |
|--------|-----------|-----------|
| Compact top5 unique bars | 228 | 953 |
| bar_idx range | 664–891 | 664–1616 |
| Gaps in bar_idx sequence | N/A | **None** |
| Heartbeat cache_last_bar_idx | 1613 (wrong, from meta) | 1616 (correct) |
| GUI loaded max bar | 891 | 1616 |
| Cache lag (bars) | false 0 (actually 725 bars behind) | True 0 |
| Incremental run latency | 0.6s (but corrupting data) | 0.6s (correct) |

---

## PY_COMPILE_APP:
PASS

## PY_COMPILE_DAEMON:
PASS

## LAUNCHER_BASH_CHECK:
PASS

## SMOKE_TEST:
PASS — `SMOKE_TEST=PASS` from `book_flow_chart_v3.py --smoke-test --date latest`

All key checks:
```
GUI_LOADS_LATEST_CACHE=true
CACHE_MTIME_WATCHER_WORKS=true
FOLLOW_LIVE_STARTS_CHECKED=true
RESET_VIEW_STILL_WORKS=true
PAN_ZOOM_PRESERVES_VIEW=true
CURSOR_MOVE_PRESERVES_VIEW=true
LEVEL_CACHE_VALIDATION_OK=true
CACHE_LAG_BARS_BY_DEPTH={'5': 0, '10': 0, '15': 0, '20': 0}
```

## TRADING_ENABLED:
NO — not touched. All trading flags, model artifacts, live data services unchanged.

---

## Fix Summary

**Root cause**: `build_book_flow_level_cache.py:694` — `keep = existing[existing["bar_idx"] < forming_bar_idx]` compared a 0-based bar position (e.g., 948) against actual bar_index values (664–1616). The filter silently dropped ~725 bars per incremental run.

**Fix**: One line change — compute `forming_bar_actual_idx` via `bars.iloc[forming_bar_idx]["bar_index"]` before filtering.

**Recovery**: `--force` triggered clean initial backfill to rebuild all 4 depth parquets from scratch. Subsequent incremental runs confirm no further data loss.

---

## OVERALL: PASS

- Backup created before any changes ✓
- Root cause precisely diagnosed: CACHE_STALE (keep-filter position vs index mismatch) ✓
- Bug fixed in one line (position → actual bar_index conversion) ✓
- Cache rebuilt with `--force`; all 4 depth parquets now have 953 bars, 664–1616, no gaps ✓
- Incremental update confirmed working after fix (0.6s, full_raw_replay=false) ✓
- GUI loads newest cache rows (max bar_idx 1616, matches heartbeat) ✓
- Reset View jumps to latest cache bar (bar_pos 952 = bar_idx 1616) ✓
- Follow Live tracks live edge correctly ✓
- VIEW USER LOCKED message updated to hint at Reset View ✓
- Chart semantics unchanged ✓
- Trading remains disabled ✓
- Daemon restarted and running (PID 1562471, PASS, incremental, lag=0) ✓
