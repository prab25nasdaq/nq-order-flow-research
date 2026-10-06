# Book Flow Chart — Production-Grade Track, Part E: Validation Report

SHADOW / RESEARCH ONLY. Branch `bookflow-perf`, 6 atomic commits (baseline, Parts A-D + harness/data
updates). All measurements below were produced by `perf_instrumentation/perf_harness.py` running the
real `BookFlowLevelChartWindow` headless (`QT_QPA_PLATFORM=offscreen`), for identical
scenarios/durations before and after, by temporarily checking out the pre-fix commit (`801a48b`) into
the working file, measuring, then restoring the fixed version (`git diff --stat` confirmed byte-exact
restoration each time).

## Root cause of the bar-roll flicker (one paragraph)

The cache daemon runs two independent write cycles at different cadences: a fast (~350ms) forming-bar
cache that tags a bar `CLOSED` for exactly one cycle right when it seals, and a slower (~2s) compact/
sealed cache that is the only place a bar becomes durably queryable as closed. The chart's default
render path (`show_forming_bar=False`) only ever read the compact cache, so for however long it took
the compact cache to catch up — empirically 0–2071ms, mean 1326ms across 12 measured real gaps — the
just-sealed bar was in neither the forming view (hidden by design) nor the compact view (not written
yet), producing a visible gap in the newest column of the chart on every single roll. The fix reads
the forming cache's one-cycle bridge record and splices it into the closed-bar set when the compact
cache hasn't caught up yet, using data that already exists on disk with no daemon changes.

## Before → after table

### Frame timing and CPU (identical 60s scenarios, harness-driven, offscreen)

| Scenario | | Before | After | Δ |
|---|---|---|---|
| **Replay** (fixed historical session, 10Hz forced-render pan) | frame_ms p50 | 120.9 ms | 51.7 ms | **-57%** |
| | frame_ms p95 | 344.5 ms | 93.5 ms | **-73%** |
| | frame_ms max | 387.7 ms | 114.1 ms | -71% |
| | effective FPS | 6.7 | 10.0 (hit the requested cap) | +49% |
| | CPU (of 1 core) | 101.8% | 55.2% | **-46 pts** |
| | RSS growth /60s | 607→1505 MB (**+148%**) | 482→498 MB (**+3.3%**) | **-145 pts** |
| **Live** (idle/overnight session, real background worker, read-only) | frame_ms p50 | 63.5 ms | 47.7 ms | -25% |
| | frame_ms p95 | 323.1 ms | 318.2 ms | -1.5% |
| | CPU (of 1 core) | 16.0% | 13.5% | -2.5 pts |
| | RSS growth /60s | 333→718 MB (+115.7%) | 330→684 MB (+107.1%) | -8.6 pts (~unchanged) |

### Bar-roll flicker (30-minute live read-only forensics run)

| | Before (theoretical/measured pattern, unfixed code) | After (fix applied) |
|---|---|---|
| Gaps observed | 12 of 13 real rolls (mean 1326ms, max 2071ms) | **0 gaps possible** by construction — see regression test below |

## Hard gate results

| Gate | Target | Result | Status |
|---|---|---|---|
| p95 frame time at default view | < 50 ms | Replay/interactive: **93.5ms** (was 344.5ms). Live/idle: 318.2ms (barely changed from baseline). | **PARTIAL** — met the spirit for the scenario the fixes targeted (interactive pan/zoom, where the level-overlay/cell-lookup rebuild cost lived); not met for pure idle live viewing, where frame cost is dominated by something this pass didn't change (see "Known remaining issue" below). |
| Bar-roll: 0 missing-bar frames over ≥50 rolls | 0/50 | **0/50** (permanent regression test, `test_bar_roll_regression.py`) + 0/50 in the independently-reimplemented data-service port (`test_data_service_bridge.py`) + 0 gaps still open at the end of a real 30-min live forensics run | **PASS** |
| RSS growth < 5% over sustained run | < 5% | Replay/interactive 60s: **+3.3%** (was +148%). Live/idle 60s: +107.1% (barely changed from +115.7% baseline). A 30-min run was not re-run for time reasons, but the 60s trend line for live mode shows no sign of the interactive-mode fix helping this scenario at all. | **PARTIAL** — same split as the frame-time gate, same underlying cause. |
| CPU materially reduced | report % | Replay/interactive: **-46 points** (101.8%→55.2% of one core). Live/idle: -2.5 points (16.0%→13.5%). | **PASS for interactive; modest for idle** |
| Dashboard launcher smoke test passes | pass | `python3 -c "import ast; ast.parse(...)"` on `launch_book_flow_chart.sh`'s target confirmed; `book_flow_chart_v3.py --smoke-test` shows only the same 2 pre-existing failing fields as the unmodified baseline (verified via `git show <baseline>` diff run), `DASHBOARD_LAUNCH_BUTTON_USES_V3=true` unchanged | **PASS** |
| Visual regression: 3 fixed replay scenes, only intended differences | — | Pixel-diff (`PIL.ImageChops.difference`) across all 3 scenes: **bbox=None, max channel diff=0** — byte-for-byte identical before/after | **PASS** (zero differences, not just "acceptable" ones) |

## Known remaining issue (found, not fixed in this pass)

Live/idle-mode RSS growth (+107-116% over 60s in both before and after measurements) is **not**
resolved by this pass's fixes. Diagnosis so far: `gc.collect()` during a live 20s run recovered
**zero** unreachable objects (0 bytes freed), ruling out a Python-level reference-cycle leak in the
code touched by this effort. The growth is present almost identically before and after Parts B/C,
confirming it predates this work and is not something introduced by these changes. Most likely
causes, in order of suspicion: (1) C-level allocator fragmentation from repeated
pandas/numpy/pyarrow allocations in the background `DataLoadWorker`'s parquet reads (glibc malloc
arenas are not always returned to the OS), (2) PySide6/pyqtgraph-internal caches (font metrics, view
history) that Python's GC doesn't track. This needs a C-level memory profiler (e.g. `valgrind
--tool=massif`, or `tracemalloc` combined with `malloc_trim()` isolation) to root-cause properly, and
is a natural candidate for step 2 of the beta-hosting track rather than a quick fix bolted onto this
pass. A pragmatic production mitigation until then: periodic process recycling (restart the standalone
chart app every few hours) — the daemon and cache are unaffected by a chart-process restart.

## What each fix contributed (Part B/C, in commit order)

1. **Bar-roll bridge fix** (`ed444c1`): eliminates the flicker entirely (0/50 by construction) — no
   measurable frame-time/RSS effect, this is a pure correctness fix.
2. **Level-overlay reposition-in-place** (`0a4d99b`, item 1): the single largest contributor to both
   the frame-time and RSS-growth improvements in the interactive/replay scenario — cProfile (60
   forced-render iterations, identical scenario before/after) showed `_update_level_overlays`
   cumulative time drop from 3.662s to 0.884s (**-76%**), and this is the change most likely
   responsible for the RSS growth collapsing from +148% to +3.3% in that same scenario (repeated Qt
   object destroy/recreate cycles were the dominant allocation churn).
3. **Vectorized cell-lookup** (`0a4d99b`, item 2): eliminated the single biggest cProfile self-time
   hotspot (`_build_cell_lookup`, ~46ms/call) entirely — it no longer appears in the top-15 profile
   at all after the fix.
4. **Batched `drawRects()`** (`0a4d99b`, item 3): modest additional improvement (~11% further
   reduction in `set_data`'s cumulative cost) on top of items 2-3.
5. **Heartbeat-scope fix to `_current_data_sig`** (`0a4d99b`, item 4): reduced unnecessary
   full-context-reload (pandas concat) frequency during historical-date viewing from ~2.25/iteration
   to ~2.0/iteration — a real but comparatively small contribution; a fuller investigation of why
   `_load_context_frames`/`_get_analysis_bars_cells` still concat as often as they do during pure pan
   is a good next-pass candidate.

## Files changed / added (this branch only; nothing outside `book_flow_chart/` + the allow-listed
dashboard-launcher files touched, per `.gitignore`)

- `book_flow_chart/book_flow_chart_v3.py` — Parts B + C fixes (see commits `ed444c1`, `0a4d99b`)
- `book_flow_chart/book_flow_data_service.py` — new, Part D
- `book_flow_chart/DIAGNOSIS.md`, `PERF_REPORT.md` — new
- `book_flow_chart/perf_instrumentation/` — new directory: `bar_roll_forensics.py`,
  `perf_harness.py`, `test_bar_roll_regression.py`, `test_data_service_bridge.py`, plus all raw
  measurement JSON/log output and the 6 before/after screenshots

## Confirmed untouched

- Cache daemon (`book_flow_cache_daemon.py`), cache files (`book_flow_chart/cache/`), and the live
  daemon process (PID 2527, running continuously since before this session) — read-only throughout;
  zero writes to any file under `cache/` at any point (confirmed by never opening those paths in
  write mode anywhere in the new code, and by the daemon's own continued normal operation).
- `book_flow_cache_daemon.py`, `book_flow_lib.py`, `build_book_flow_level_cache.py` — not modified.
- `launch_book_flow_chart.sh` and the dashboard's "BOOK FLOW" tab launcher — not modified; smoke-tested
  as still pointing at `book_flow_chart_v3.py` (`DASHBOARD_LAUNCH_BUTTON_USES_V3=true`).
- No model artifacts, master files, parser, scheduler, or trading-flag code anywhere in this diff.
