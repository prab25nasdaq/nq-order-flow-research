# Book Flow Chart — Production-Grade Track, Part A: Diagnosis

SHADOW / RESEARCH ONLY. No fixes applied in this document — recon and measurement only.

## 1. Module identification

- **Active app**: `book_flow_chart/book_flow_chart_v3.py` (3,063 lines). Launched standalone by
  `launch_book_flow_chart.sh` (`--symbol NQU6 --date latest`), separate process from the main
  dashboard, using a dedicated venv (`/home/prabh/.venvs/ofi/bin/python`). The dashboard's
  "BOOK FLOW" tab (`dashboard_tab_registry.py`) is a launcher that spawns this same script as a
  subprocess — it does not embed the chart in-process.
- **UI framework**: PySide6 (`pyqtgraph.Qt.QT_LIB == "PySide6"`) via `pyqtgraph` (`pg`). Draw path
  is custom: two `pg.GraphicsObject` subclasses (`BookFlowLevelCellItem`, `WideOFILevelCellRasterItem`)
  pre-render all cells into a `QPicture` in `set_data()`; `paint()` is a single `self._picture.play(painter)`
  call (already batched — see §5, this was a prior fix from 2026-06-22).
- **Data source**: parquet files under `book_flow_chart/cache/`:
  - Compact ("sealed") cache: `<SYMBOL>_<DATE>_top<DEPTH>.parquet`, rewritten by
    `book_flow_cache_daemon.py`'s main loop every `interval_sec` (launcher passes `2`, i.e. **every 2s**).
  - Forming-bar fast cache: `cache/forming/<SYMBOL>_<DATE>_top<DEPTH>_forming.parquet`, rewritten
    by `build_forming_bar_cache()` on a **350ms sub-cycle**, independent of the main 2s cycle.
  - Daemon (`book_flow_cache_daemon.py`, PID 2527, running continuously since Jun 23) and its cache
    files were only ever **read** during this investigation; nothing in `cache/` was written to.
- **Refresh trigger**: `CacheFileMonitor(QtCore.QThread)` polls file signatures (mtime+size) every
  2s and, on change, hands off to `DataLoadWorker(QThread)` which does the actual parquet
  read/parse **off the GUI thread** (this offload was already implemented in the 2026-06-22 "UI
  freeze final" fix — see §5). The GUI thread also has an independent `QTimer` at
  `RENDER_INTERVAL_MS = 1000/MAX_FPS` (**250ms, MAX_FPS=4 by default**) that calls `_reload()`;
  `_reload()` coalesces via a `render_key`/`quick_key` comparison and returns in <1ms when neither
  data nor view changed.

## 2. Prior work already done to this module (do not re-litigate, do not undo)

This chart has a long, heavily-documented history of prior perf/freeze fixes (all pre-git; tracked
only via `*_FIX_REPORT.md` files and manual `.bak_*` snapshots in `book_flow_chart/`, none of which
are in the git history now started for this effort). Most relevant:

- **2026-06-22, `BOOK_FLOW_UI_FREEZE_FINAL_REPORT.md`**: moved all parquet I/O + pandas concat off
  the GUI thread (`DataLoadWorker`), added `_level_idx` dict for O(visible_bars) cell-window lookup
  instead of O(all_rows) `.isin()` scans, and converted `BookFlowLevelCellItem` from a per-cell
  Python draw loop to the QPicture-batched design described above. Confirmed still in place.
- **2026-07-12**: added `WideOFILevelCellRasterItem` + `_aggregate_cells_for_wide_lod()` for
  WIDE/OVERVIEW LOD tiers (collapsing cells to per-bar/price-bin aggregates beyond ~500 visible
  bars) — this already implements most of what Part C's LOD ask describes; existing thresholds:
  `LOD_FULL<=150 bars, LOD_MEDIUM<=500, LOD_WIDE<=1500, else LOD_OVERVIEW` (`MAX_RENDER_CELLS_FULL=20000`).
- `BOOK_FLOW_REMAINING_ISSUES_NOTES_V2.md` (2026-06-16) documents open issues from an earlier round
  (timestamp parity, missing previous-session context, partial-looking bars) that were addressed by
  later fixes (`LIVE_TIMESTAMP_CONTEXT_CACHE_FIX_REPORT.md`, 2026-06-17) — **not** the same mechanism
  as the bar-roll flicker found below, which no prior report describes or fixes.

Net effect: the *cell-batch draw path* and *GUI-thread I/O offload* were already substantially
optimized. This investigation's new findings (§4) are in **level-overlay object churn**, the
**per-frame tooltip-lookup rebuild**, and the **forming/sealed cache handoff gap** — none of which
appear in any prior fix report.

## 3. Bar-roll flicker — root cause (empirically confirmed)

**Mechanism**: two independent, differently-paced cache writers.

1. `build_forming_bar_cache()` (350ms cycle) tracks the currently-forming bar. When it detects the
   bar has just sealed (`bar_closed_since_checkpoint=True`), it writes **one** forming-cache record
   tagged `bar_state="CLOSED"` for that just-sealed bar (`book_flow_cache_daemon.py:1063`) before
   advancing to the next forming bar on its *next* 350ms cycle.
2. `ensure_compact_caches()` (2s cycle) is the only place a bar is durably added to the compact/
   sealed cache as `CLOSED`.
3. `book_flow_chart_v3.py:_bg_load_snapshot()`, in the **default** state (`show_forming_bar=False`,
   confirmed default from the 2026-06-22 fix — "closed bars only default"), only reads the compact
   cache (`level_df = level_df[level_df["bar_state"] == "CLOSED"]`, line ~765) and **never loads or
   consults the forming cache at all** in this branch. The forming cache's one-cycle "CLOSED"
   bridge record (item 1 above) is therefore never read by the default render path.

Result: for however long it takes the compact cache to catch up (0 to ~2s, since it rewrites every
2s), the just-sealed bar is absent from **every** data source the default chart view reads — not
shown as forming (it isn't, and forming is hidden anyway), not shown as closed (compact cache
hasn't caught up). This is the flicker: the rightmost column of the chart briefly has a gap where
the newest bar should be, then it pops in once the compact cache's next 2s cycle runs.

**Empirical confirmation** (read-only forensics logger, `perf_instrumentation/bar_roll_forensics.py`,
polling both cache files every 100ms against the live daemon for a full 30-minute run, zero writes;
full log/JSON at `perf_instrumentation/roll_forensics.log` / `roll_forensics_result.json`):

- **13 rolls observed, 12 gaps detected** (one roll's gap window fell entirely between two 100ms
  polls and was missed by the logger itself — a detection-resolution artifact, not evidence the
  gap didn't occur; every OTHER roll showed a clear gap).
- Gap durations (ms): 620, 725, 2064, 1656, 2071, 1756, 621, 1030, 619, 931, 2067, 1752.
- **Mean 1326 ms, max 2071 ms** — consistent with the theoretical 0–2s bound set by the compact
  cache's 2s rewrite interval.
- Every gap closed cleanly (`still_open_at_end: []`) — no bar was ever permanently lost, confirming
  this is a **transient handoff race**, not a data-loss bug.

**Fix direction (for Part B, not applied here)**: in `_bg_load_snapshot()`, always additionally read
the forming-fast cache (cheap — it's a small file) and splice in any bar tagged `CLOSED` there whose
`bar_idx == last_closed_in_compact + 1`, rendering it as a normal closed bar (not forming), even
when `show_forming_bar=False`. This uses data that already exists on disk today; no daemon changes
needed, and it exactly matches the mission's suggested remedy ("read both, tolerate one poll of
overlap — a duplicate frame is acceptable, a missing bar is not").

## 4. Render performance — measured (headless, offscreen Qt, `perf_instrumentation/perf_harness.py`)

### 4a. Frame timing

| Scenario | n frames | p50 | p95 | max | effective FPS |
|---|---|---|---|---|---|
| **Live** (attached read-only to today's session, 60s, idle/overnight) | 43 rendered / 240 polled | 48.2 ms | 58.9 ms | 92.4 ms | 0.72 (real-render rate; coalescing suppressed 197/240 polls) |
| **Replay** (fixed historical full session, 60s, synthetic pan @10Hz forced-render) | 355 | 129.6 ms | 389.4 ms | 436.9 ms | 5.9 |

**Gate check (Part E target: p95 < 50ms at default view)**: live-idle p95 (58.9ms) is already close
but over; replay/panning p95 (389ms) is **~7.8x over target**. The live number is measured during a
quiet overnight session with few visible cells (3,323) — a busier RTH session or any active
pan/zoom will land in the replay regime.

### 4b. Where the time goes (cProfile, 60 forced-render iterations panning across a full historical session)

| Function | Self time (60 iters) | Per-call self | Notes |
|---|---|---|---|
| `_build_cell_lookup` | 2.772 s | **46.2 ms** | Rebuilds a Python dict-of-dicts, one entry per visible cell (up to ~20k), every non-coalesced frame — for mouse-hover tooltips. Biggest single hotspot found. |
| `BookFlowLevelCellItem.set_data` | 0.990 s (2.024s incl. children) | 16.5 ms / 33.7 ms | The already-batched QPicture cell draw path (§2) — still O(n_cells) Python `drawRect` calls (1,019,756 total calls across 60 iters ≈ 17k/frame) to *build* the picture; `paint()` itself is O(1). |
| `_update_level_overlays` → `_add_level_line` | 1.281 s (4,440 calls = 74/frame) | — | Rebuilds **every** S/R/POC/VAH/VAL/HVN/LVN/projected-level line and text label from scratch (`removeItem`+`addItem`+new `pg.InfiniteLine`/`pg.TextItem`) whenever the view window changes — even though the underlying level *values* are cached separately and don't change on pan (only the label x-position needs to move). |
| pandas `concat` (context/bars loading) | 2.687 s cumulative (162 calls) | — | From `_load_context_frames`/`_get_analysis_bars_cells`, triggered more often than expected during rapid synthetic panning (14/60 iterations) — needs Part C attention to confirm it isn't over-triggering on pure view-window changes. |

Scene item count at a normal default view (210 bars, MEDIUM LOD, 13,962 visible cells): **122** Qt
graphics items in the viewbox, of which **92** are level-overlay lines/labels recreated every time
`_update_level_overlays`'s cache key changes (i.e., every pan/zoom).

### 4c. Memory

| Scenario | RSS start | RSS end | Duration | Growth |
|---|---|---|---|---|
| Live, idle/overnight | 326.8 MB | 362.7 MB | 60s | +11.0% |
| Replay, synthetic pan @10Hz | 625.4 MB | 1,624.8 MB (peak 1,653.6 MB) | 60s | **+159.8%** |

A 10-minute live-idle RSS run was also launched (`perf_instrumentation/live_10min_rss_before.json`)
to check whether the live-mode growth plateaus or is linear; see that file for the longer-horizon
number, appended below once complete. The replay-mode growth is large enough over just 60 seconds
that it must be treated as a real finding regardless: **Part E's "<5% growth over 30 min" gate is
very likely to fail today** under any workload involving active panning, and is at meaningful risk
even under idle live viewing. The most likely contributor, based on the profiling above, is the
per-frame `pg.InfiniteLine`/`pg.TextItem` churn in `_update_level_overlays` (Qt/PySide6 objects are
not always released promptly on `removeItem()`, and 74+ new objects per pan step adds up fast) —
to be confirmed with a longer/targeted allocation trace in Part C before fixing.

## 5. What Part B/C should target, in priority order

1. **Bar-roll gap** (§3) — correctness bug, user-visible on every single roll, root cause fully
   understood, fix is small and localized to `_bg_load_snapshot()`.
2. **`_update_level_overlays`/`_add_level_line` per-frame object churn** (§4b) — likely the largest
   single contributor to both frame time (1.28s/60 iters) and RSS growth; fix is to keep persistent
   line/text objects and update their properties in place rather than destroy+recreate, invalidating
   only when the underlying level *values* change (already tracked via `_analysis_window_key()`)
   rather than on every view-window change.
3. **`_build_cell_lookup` full rebuild every frame** (§4b) — biggest single self-time hotspot;
   candidate fixes: lazy-build only on mouse-move, or replace the per-row dict construction with a
   vectorized numpy/pandas structure queried by array index instead of a Python dict.
4. **`BookFlowLevelCellItem.set_data`'s remaining O(n_cells) Python loop** — already far better than
   the pre-2026-06-22 per-frame-paint version, but still meaningful at ~17-20k cells; consider
   `QPainter.drawRects()` (batch call) per color-group instead of one `drawRect()` call per cell.
5. Confirm/bound the `pd.concat` context-reload frequency during pure pan/zoom (§4b) — should be
   fully suppressed once §2's `_level_idx`/data-sig caching is respected; the 14/60 trigger rate
   during synthetic panning warrants a closer look before assuming it's already correct.

## 6. Constraints observed during this investigation

- The live cache daemon (PID 2527) and its cache files were only ever read; no file under
  `book_flow_chart/cache/` was written to at any point.
- All measurement tooling lives under `book_flow_chart/perf_instrumentation/` (new files, not yet
  wired into anything the daemon or launcher touches).
- Git repository initialized at `/home/prabh/OFI_Production` (was not previously version-controlled)
  scoped via `.gitignore` to the chart module + dashboard-launcher glue only (excludes the 3.6GB
  cache dir and all unrelated multi-GB project directories). Branch `bookflow-perf` created off a
  baseline commit of the current (pre-fix) state.
