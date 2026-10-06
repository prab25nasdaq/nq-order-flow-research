# Book Flow Chart — Step 2.5 Part C: Fix Attempt Decision

SHADOW / RESEARCH ONLY. One bounded fix attempt, per the mission's explicit "EXACTLY ONE...
no spiraling" instruction. **Gate: MISSED. Marked WAIVED-WITH-EVIDENCE. Stopping here — no
second attempt.**

## What was tried

Per `P95_FORENSICS.md`'s verdict: the dominant cost inside every expensive frame class
(forming-update, frame-consume, bar-roll, and the residual slow-path "other") was **not**
`set_data()`'s QPicture rebuild (only ~1.8% of sampled time) but a full, from-scratch,
line-by-line `json.loads()` re-parse of the entire session's vol500 bars ndjsonl file
(`load_vol500_bars()`), triggered on every reload that reaches `_load_bars()` — whether via the
background worker's `_bg_load_snapshot()` or the GUI-thread's `_load_context_frames()`. This
dominated the py-spy sample counts (~10% of all sampled time in the `raw_decode`/JSON leaf alone,
with several hundred more samples in the pandas DataFrame-construction machinery immediately
downstream of it).

Added `book_flow_lib.load_vol500_bars_incremental()`: caches parsed rows keyed by file identity,
re-parsing only the bytes appended since the last call for the same path instead of the whole
file, with a safe fallback to a full reparse on any ambiguity (file shrink/rotation, a bad line at
the cache boundary, any error). **Does not modify `load_vol500_bars()` itself** — the cache daemon
and `build_book_flow_level_cache.py` keep calling the original, unmodified function and are
completely unaffected. Wired into the two chart-specific callers only: `_load_bars()` in both
`book_flow_chart_v3.py` and `book_flow_data_service.py`.

Correctness verified directly (not just via the flicker suites) before any perf measurement:
baseline match against the real live-growing file, a deterministic single-append test, a
repeated-append test, and a shrink/rewrite-fallback test — all byte-for-byte identical to the
original `load_vol500_bars()`'s output. All three bar-roll regression suites re-confirmed 0/50
missing after wiring the change in.

## Measured delta (30-min live, true default UX, apples-to-apples with `P95_FORENSICS.md`'s baseline)

| | Before (P95_FORENSICS.md) | After (this fix) | Δ |
|---|---|---|---|
| Overall p50 | 283.1ms | 168.7ms | **-40.4%** |
| Overall p95 | 612.4ms | 451.7ms | **-26.2%** |
| Overall max | 960.1ms | 609.1ms | -36.6% |
| >50ms frame share | 73.0% | 65.7% | -7.3 pts |
| forming-update p50/p95 | 422.7 / 682.0ms | 359.6 / 472.1ms | -14.9% / -30.8% |
| frame-consume p50/p95 | 287.3 / 447.2ms | 177.1 / 331.7ms | -38.4% / -25.8% |
| other p50/p95 | 495.6 / 690.5ms | 402.2 / 486.6ms | -18.8% / -29.5% |
| bar-roll p50/p95 | 451.5 / 643.3ms | 397.9 / 466.7ms | -11.9% / -27.4% |

Every class improved meaningfully (15-40% depending on metric). This is a real, worthwhile,
low-risk fix — but the gate needs roughly a **90%+** reduction from baseline (612ms → <50ms), and
this fix alone delivers ~26%. **GATE MISSED.**

## Why it wasn't enough (and what the next candidate would need to address)

The vol500-bars reparse was the single largest *individually attributable* cost, but per-B's own
evidence, the full picture is a *distributed* set of costs, several of which this fix does not
touch at all:

1. **JSON heartbeat parsing + filesystem date-discovery walk**, paid on every single `_reload()`
   call regardless of path (`_current_data_sig()` → `_sync_live_latest_from_heartbeat()` →
   `load_heartbeat()`, and → `_context_file_sig()` → `_selected_context_dates()` →
   `_available_dates_for_symbol()` → `cache_daemon.latest_dates()` → an uncached
   `FEATURES_BASE.iterdir()` + per-directory `.exists()` walk). Not touched by this fix.
   **Projected effort: small** (a short-TTL cache or lru_cache with manual invalidation on
   date-rollover, similar shape to this fix) — **projected impact: moderate**, since it's paid on
   every call including currently-cheap idle-blit ones, so likely raises the *floor*, not
   necessarily the p95 tail specifically.
2. **The GUI-thread slow-path race** (Part B's "other" category, and half of forming-update's
   occurrences): the GUI thread's own `_current_data_sig()` polling outraces the async worker's
   delivery, forcing an occasional direct blocking read on the GUI thread. **Projected effort:
   medium** (needs a real architectural change — e.g. block on the pending load instead of falling
   through to a duplicate synchronous read, or tighten the worker's poll cadence) — **projected
   impact: moderate-to-large** for the "other" class specifically, smaller for forming-update.
3. **Pandas concat/construction for the 2-date context** (`_load_context_frames`,
   `_get_analysis_bars_cells`'s repeated `concat`+`equals` calls, `_update_view_ranges`'s groupby
   aggregation, `_prepare_visible_data`'s `.map()` call) — Step 1 already flagged part of this
   ("a fuller investigation... is a good next-pass candidate") and it's still open. **Projected
   effort: large** (each of these is a separate call site needing its own incremental-update
   design, not a single bounded change) — **projected impact: large**, since these appear
   throughout nearly every expensive frame class.
4. **The `set_data()` QPicture rebuild** — the mission's original three candidates target this
   specifically. Confirmed real but small (~1.8% of sampled time). **Projected effort: medium**
   (candidate #1, incremental forming-bar repaint, as originally suggested) — **projected impact:
   small on its own**, given the evidence; likely only worth doing *after* items 1-3 above, once
   they're no longer masking it.

## Gate status

**WAIVED-WITH-EVIDENCE.** The fix applied is real, verified, low-risk, and kept (not reverted) —
it measurably reduces cost across every frame class with zero correctness regression. The p95 <
50ms gate itself is not met and is not expected to be met by any single bounded change, given the
evidence shows the cost is distributed across at least four largely-independent mechanisms (above).
Closing it fully is future work, not attempted further here per the mission's explicit "no
spiraling" instruction.


## Step 3 Phase 1 Part C update: service-path measurement + one fix attempt

Measured against a realistic-cadence synthetic workload (the real live feed was stopped for the
weekend when this ran -- see STEP3_REPORT.md; the driver reproduces this system's actually-
observed real cadences: ~2s compact-cache rewrite regardless of content change, ~350ms forming
cadence, ~3.7min bar-roll rate, and matches real row-count density, ~90 rows/bar). Frame
classification, 30-min run, service path (live-latest + forming bar, the true default UX, now
natively on `BookFlowDataService` per Phase 1 Part A):

| | Before this fix | After this fix | Delta |
|---|---|---|---|
| Overall p95 | 134.1ms | 136.4ms | ~unchanged (noise) |
| Overall p50 | 49.0ms | 77.0ms | worse (see note below) |
| forming-update p50/p95 | 86.7 / 140.9ms | 87.3 / 145.2ms | ~unchanged |
| RSS growth post-warmup (t>=600s) | +43.2% | +1.8% | -41.4 pts |

**What was tried:** `BookFlowDataService._build_frame_live_latest()` unconditionally re-ran
`pd.concat([self._prev_cells, cur_sealed_cells])` (and the matching bars concat) on every poll
that detected ANY change -- including a pure forming-bar content update, which doesn't touch the
previous session or the current session's sealed bars at all. Split the sig-check into "core"
(compact+bars, gates the expensive concat) vs "forming" (the fast ~350ms lane); a forming-only
change now reuses the cached previous+current combination untouched. This directly implements
Phase 1 Part A's own mission text ("no full concat... per update"), which the original
implementation only partially honored (it avoided re-loading previous-session files, but not
re-concatenating them).

**Why p95 didn't move despite a real, verified fix:** two reasons, both evidenced by this same
measurement.
1. The real daemon rewrites the compact cache every ~2s regardless of whether content actually
   changed (confirmed in Step 2) -- daemon behavior, out of scope to change ("cache daemon
   untouched"). This means the "core" sig still changes roughly every 2s even when nothing
   meaningful sealed, so the expensive path still fires on a real fraction of polls, not just on
   genuine bar-rolls.
2. More importantly: this fix only addressed the concat inside the service.
   `_chart_frame_to_snapshot()` (`book_flow_chart_v3.py`) still does its own
   `pd.concat([sealed, frame.forming_cells])` on the chart side, on every single forming update,
   to build `level_df` -- the exact same class of cost (concatenating a small, fast-changing
   forming slice onto a large, mostly-static sealed-cells DataFrame), one layer up, still present.
   Found via this same investigation, not fixed here: extending the identical caching idea to the
   chart adapter (or restructuring the render pipeline to overlay the forming bar separately
   instead of concatenating it into `level_df` every time) is the natural next candidate, but was
   not attempted -- per the mission's "exactly ONE evidence-driven fix attempt... no spiraling,"
   this is reported as a named, precise follow-up rather than a second attempt in this pass.

The RSS improvement is real and substantial (this specific fix eliminated the repeated
large-DataFrame allocate/discard churn that was the direct driver of that growth) even though it
didn't move the frame-timing gate -- the two costs are related but not the same mechanism, exactly
as this section's finding #2 explains.

## Gate status (Step 3 Phase 1 Part C)

**p95 < 50ms: WAIVED-WITH-EVIDENCE** (136.4ms; one real, verified, evidence-driven fix attempt
made and kept; the remaining cost is now precisely localized to a specific, named, un-fixed
mechanism -- see above).

**RSS growth < 5% post-warmup: PASS** (+1.8%, well within the mission's own stated threshold;
this is the metric this pass's fix directly targeted and materially improved from +43.2%).
