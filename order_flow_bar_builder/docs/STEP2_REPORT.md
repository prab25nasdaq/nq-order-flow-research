# Book Flow Chart — Step 2 Report: DataService Cutover + Live-Mode Memory Root-Cause + Soak Cert

SHADOW / RESEARCH ONLY. Branch `bookflow-perf`, continues from Step 1 (`PERF_REPORT.md`). Cache
daemon (PID 2527) confirmed untouched and read-only throughout — no writes to any file under
`cache/` at any point in this step. All work confined to `book_flow_chart/` + its
`perf_instrumentation/` subdirectory.

## Part A — DataService cutover (feature-flagged)

`BookFlowDataService` (extracted in Step 1, not wired in until now) is live behind
`BOOKFLOW_DATASERVICE` (default **OFF** — `_BOOKFLOW_DATASERVICE_DEFAULT_ON = False`). Eligible
configs (single-date context, `show_forming_bar=False`) route through the service's background
thread; everything else falls back to the legacy `CacheFileMonitor` path automatically, per the
mission's "legacy path retained as fallback for one release" framing. A `_chart_frame_to_snapshot`
adapter converts a `ChartFrame` into the exact dict shape `_apply_snapshot()` already consumes, so
100% of the Step-1-optimized render pipeline is reused unchanged regardless of data source.

**Parity — PASS.**
- Replay (fixed historical date): 1404 bars identical, `last_closed_bar_idx` identical, all 3
  fixed scenes pixel-identical (bbox=None).
- Live (10-minute real capture, not a smoke test): 2679 samples, **0 mismatches (0.0%)**.

**Flicker suites through the service path — 0 missing-bar frames.** Three independent gates, all
50/50 (or 2-hour-live) with 0 missing: the legacy path (`test_bar_roll_regression.py`), the
standalone service (`test_data_service_bridge.py`), and a new
`test_integrated_dataservice_bridge.py` that closes the one gap the first two didn't cover — the
full adapter → `_apply_snapshot()` → render path through the service, not just the service or
legacy mechanism in isolation.

**Idle-live p95 re-measurement**: measured 72-83ms in this step's live sessions, vs. Step 1's
318ms baseline — but this reflects a quieter market window (fewer bars/cells accumulated at
measurement time) more than a code-driven improvement; reported as-is rather than overclaiming a
"collapse" the controlled data doesn't cleanly attribute to the cutover alone. See the 2-hour soak
below for the trustworthy, apples-to-apples idle-live p95 number.

## Part B — Live-mode RSS root-cause (verdict before fix)

Full verdict and evidence: `PART_B_RSS_VERDICT.md`. One-paragraph summary:

> `_current_data_sig()` unconditionally folded the live daemon's heartbeat-file `(mtime, size)`
> into the chart's data signature in live-latest mode. That file is rewritten every ~2s regardless
> of whether any bar/cell changed, so the signature changed almost every poll — defeating both the
> "data unchanged" fast-path and the render-coalescing check, forcing a full parquet reparse *and*
> a full re-render (including a QPicture rebuild) on nearly every cycle (measured: 273 rebuilds
> per 900s vs. ~4 real bar rolls in that window). Each unnecessary reparse allocates through
> PyArrow's parquet reader, backed by Arrow's own `mimalloc` memory pool, which issues raw
> `mmap()` calls — confirmed via `memray --native` at both 20s and 600s scale:
> `unix_mmap_prim_aligned` accounted for ~89% of tracked native "own memory" (~1.07GB) in both
> captures, traced end-to-end to `arrow::PoolBuffer::Resize → MimallocAllocator::Reallocate →
> unix_mmap`. This is invisible to `tracemalloc` (~10MB tracked vs. ~87MB actual RSS growth over
> 15 minutes) and untouched by CPython/glibc allocator tuning — confirmed by the required A/B
> runs: `MALLOC_ARENA_MAX=2` and `PYTHONMALLOC=malloc` showed no improvement, OpenGL on/off made
> no difference, and poll-cadence A/B showed CPU scaling with poll frequency (not real data-change
> rate) exactly as the theory predicts. No Python reference-cycle leak (`gc.collect()` freed 0
> objects).

## Part C — Fix or contain

Full result: `PART_C_CONTAINMENT_RESULT.md`.

1. **Named-bug fix**: removed the heartbeat-file component from `_current_data_sig()`. Verified
   safe — date-rollover detection already flows through `self.date` (a separate, already-present
   sig component) and `_sync_live_latest_from_heartbeat()` runs unconditionally regardless.
2. **Containment (primary)**: `MIMALLOC_PURGE_DELAY=0`, set before PyArrow initializes — targets
   the actual identified allocator (mimalloc), not a generic glibc lever. Isolated 120-read
   experiment: plateaus ~190MB with it vs. climbing past 280MB without.
3. **Containment (secondary)**: low-frequency (60s) `ctypes malloc_trim(0)` for the smaller
   glibc-side share of the growth.

**Measured effect** (apples-to-apples with Part B's baseline, `perf_harness.py`, continuous event
pump): RSS growth rate **7.70 → 3.06 MB/min (-60%)**, CPU **10.9% → 7.3% (-33%)**.

**Gate**: the primary quantitative gate (<5% growth over 30 min) is **not met** — at this RSS
baseline that requires a rate under ~0.6 MB/min, well past what this fix achieves alone. Falls
back to the mission's explicit alternate gate — **demonstrated plateau — PASS**: ceiling ~431MB,
time-to-plateau ~30 minutes (see the 2-hour soak below; 92.6 of the total 102.5MB of growth
happens in the first 30-minute quarter, then post-warmup growth is +2.3% over the remaining 90
minutes, with the third quarter net *negative*).

## Part D — Soak certification (2-hour live, combined with Part C's plateau evidence)

`perf_instrumentation/soak_test.py`, 1441 samples @ 5s cadence, real live daemon attachment,
continuously-armed flicker invariant (not a synthetic replay). Chart:
`perf_instrumentation/soak_test_2h_chart.png`.

| Gate | Target | Result | Status |
|---|---|---|---|
| Idle-live p95 | < 50 ms | **118.7ms** (p50 mean 75.3ms) | **FAIL** |
| Missing-bar frames | 0 | **0** (over 2 hours of real bar rolls, ~30+ real rolls observed) | PASS |
| RSS growth | <5%/30min OR demonstrated plateau/2h | plateau: ceiling 431MB, ~30min to plateau | PASS (alternate) |
| Repeated-warning log spam | none | zero warning/error/exception lines in the full 2h log | PASS |
| Replay-suite regression check | zero regression from cutover | see below | PASS |

Replay/interactive suite re-run (final, after all Step 2 changes): p50 52.5ms / p95 100.0ms / RSS
+3.0%/60s / CPU 57.0% — matches Step 1's final numbers (51.7ms / 93.5ms / +3.3% / 55.2%) within
noise. No regression from the cutover or the Part C changes.

**The idle-live p95 < 50ms gate is not met.** This is consistent with every idle-live p95
measurement across both Step 1 and Step 2, regardless of which fix was applied — Step 1's
`PERF_REPORT.md` already reported this same gate as unmet ("PARTIAL") for a separate cost this
step's investigation did not target (Part B's root-cause chain explains the *RSS* growth, not the
idle-live frame-time floor). Reported honestly as an open item, not closed by this pass.

## Before/after table (Step 1 baseline → Step 2 final)

| Scenario | Metric | Step 1 final | Step 2 final | Δ |
|---|---|---|---|---|
| Replay/interactive | frame p50 | 51.7ms | 52.5ms | ~unchanged |
| | frame p95 | 93.5ms | 100.0ms | ~unchanged (noise) |
| | RSS growth/60s | +3.3% | +3.0% | ~unchanged |
| | CPU | 55.2% | 57.0% | ~unchanged |
| Live/idle | frame p95 | 318.2ms | 118.7ms (2h soak) | **-63%** |
| | RSS growth rate | not previously slope-measured | 3.06 MB/min post-fix (was 7.70 MB/min pre-fix, this step) | **-60%** (this step's own before/after) |
| | RSS over 2h | n/a (not measured in Step 1) | plateau, ceiling 431MB, ~30min to plateau | new capability: bounded instead of unbounded |
| Bar-roll flicker | missing-bar frames | 0/50 (synthetic) | 0/50 ×3 suites + **0 over a real 2-hour live run** | strictly stronger evidence |

## Hard gate summary

| Gate | Status |
|---|---|
| Parity (replay + 10-min live) | **PASS** |
| 0 missing-bar frames through the service path | **PASS** |
| Idle-live p95 < 50ms | **FAIL** |
| RSS growth < 5%/30min OR demonstrated plateau/2h | **PASS** (via alternate) |
| No repeated-warning log spam | **PASS** |
| Replay-suite zero regression | **PASS** |
| Cache daemon untouched, read-only | **PASS** |
| `BOOKFLOW_DATASERVICE` flag defaulted ON | **NO** — stays OFF per plan (parity passed, but the mission gated default-ON on parity passing; leaving it OFF one more release is the more conservative reading given the unmet p95 gate sits in the same idle-live code path family) |

**Not all gates pass.** Per the mission's own framing ("If all gates PASS: merge... and tag"),
this does not meet the bar for an automatic merge to `main` / tag `bookflow-v1.0-beta-core`. The
merge decision is deferred to the user — see the final summary below.

## Files changed/added this step (branch `bookflow-perf` only)

- `book_flow_chart/book_flow_chart_v3.py` — Part A cutover wiring, Part C fix + containment
- `book_flow_chart/book_flow_data_service.py` — unchanged this step (Step 1 artifact, now wired in)
- `book_flow_chart/PART_B_RSS_VERDICT.md`, `PART_C_CONTAINMENT_RESULT.md`, `STEP2_REPORT.md` — new
- `book_flow_chart/perf_instrumentation/` — new: `parity_harness.py`,
  `test_integrated_dataservice_bridge.py`, `rss_deep_profile.py`, `soak_test.py`, plus all raw
  measurement JSON/CSV/log output, the memray native capture, and the soak chart PNG

## Confirmed untouched

- Cache daemon (`book_flow_cache_daemon.py`), cache files (`book_flow_chart/cache/`), live daemon
  process (PID 2527) — read-only throughout, zero writes at any point.
- `book_flow_cache_daemon.py`, `book_flow_lib.py`, `build_book_flow_level_cache.py` — not modified.
- `launch_book_flow_chart.sh` and the dashboard's "BOOK FLOW" tab launcher — not modified.
- No model artifacts, master files, parser, scheduler, or trading-flag code anywhere in this diff.
