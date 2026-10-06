# Book Flow Chart — Step 2 Part C: Fix + Containment Result

SHADOW / RESEARCH ONLY. Continues from `PART_B_RSS_VERDICT.md`. Read-only against the live cache
daemon (PID 2527) throughout; nothing written to any cache/data file.

## What was applied

1. **Targeted fix** (named bug, per Part B): `_current_data_sig()` no longer folds the raw
   heartbeat-file `(mtime_ns, size)` into the signature in live-latest mode. Verified: identical
   pre-existing `--smoke-test` failures before/after (no new regressions), all three bar-roll
   regression suites still 0/50 missing.
2. **Containment (allocator-level)**: `MIMALLOC_PURGE_DELAY=0` set via `os.environ.setdefault()`
   at the very top of `book_flow_chart_v3.py`, before PyArrow initializes. Isolated experiment
   (120 repeated reads of the real compact cache): plateaus ~190MB with this set vs. climbing
   past 280MB with no deceleration in the same test without it.
3. **Containment (secondary)**: low-frequency (60s) `ctypes` `malloc_trim(0)`, targeting the
   smaller glibc-side share of the growth (pandas/numpy allocations tracemalloc did detect in
   Part B) — not the dominant mechanism, but cheap and included per the mission's explicit ask.

## Isolated fix-only measurement (before the 2-hour soak)

`perf_harness.py`, continuous event-pump, apples-to-apples with the Part B baseline:

| | Part B baseline (600s) | After Part C fix (1800s) | Δ |
|---|---|---|---|
| RSS growth slope | 7.70 MB/min | 3.06 MB/min | **-60%** |
| CPU | 10.9% | 7.3% | **-33%** |
| Cumulative growth % | +22.6% | +28.2% | (longer window, not comparable directly — see slope) |

The fix alone cuts the growth **rate** substantially but does not reach the mission's primary
quantitative gate (<5% growth over 30 min) — at a ~330-420MB baseline that gate requires a rate
under ~0.6 MB/min, well past what the fix + containment achieve in isolation. Falling back to the
mission's explicit alternate gate: a demonstrated plateau over a 2-hour window.

## 2-hour combined soak (`perf_instrumentation/soak_test_2h.csv`, 1441 samples @ 5s cadence)

```
RSS: 328.7MB -> 431.2MB (+31.2%) over 7200s
  quarter 1 (0-30min):   328.7MB -> 421.3MB, slope=3.087 MB/min
  quarter 2 (30-60min):  421.3MB -> 428.6MB, slope=0.242 MB/min
  quarter 3 (60-90min):  428.6MB -> 424.5MB, slope=-0.135 MB/min  (net negative)
  quarter 4 (90-120min): 424.5MB -> 431.2MB, slope=0.221 MB/min
  slope change Q1 -> Q4: 3.087 -> 0.221 MB/min (-92.8%)

post-warmup (t=30min to t=120min, 90 min): 421.3MB -> 431.2MB (+2.3%), slope=0.110 MB/min
max RSS observed: 454.4MB (transient, not sustained)
```

**Ceiling: ~431MB. Time-to-plateau: ~30 minutes.** 92.6MB of the total 102.5MB of growth over the
full 2 hours happens in the first 30-minute quarter (startup: Qt font/pixmap caches, mimalloc's
own arena sizing to its working set, pandas/pyarrow warming their internal buffer pools) — for the
remaining 90 minutes, growth is +2.3% total (0.110 MB/min), and quarter 3 was net *negative*,
confirming this is genuine steady-state noise around a stable ceiling, not a slow residual leak.

**GATE: demonstrated plateau — PASS** (ceiling 431MB, time-to-plateau ~30min, documented above).
**GATE: primary <5%/30min RSS growth — NOT MET** (only reachable via the alternate condition).

## Other metrics from the same 2-hour run (carries into Part D)

- **Flicker invariant, continuously armed against the real live daemon for 2 hours**: **0 missing-
  bar frames** (real bar rolls occurred throughout at the observed ~3.7 min/bar cadence — this is
  not a synthetic replay, it is a live 2-hour certification of the same invariant).
- **No repeated-warning log spam**: clean — zero warning/error/exception lines anywhere in the
  2-hour log.
- **Frame timing**: p50 mean 75.3ms, overall p95 (95th percentile of bucketed p95s) **118.7ms**,
  max single-bucket p95 275.0ms. **This does NOT meet the idle-live p95 < 50ms hard gate.**
  Consistent with every idle-live p95 measurement across both Step 1 and Step 2 (never observed
  below ~70ms in this mode regardless of which fix was applied) — this points to a separate,
  not-yet-root-caused cost distinct from the RSS mechanism this part targeted. Step 1's
  PERF_REPORT.md already reported this same gate as unmet ("PARTIAL") for the same reason; it
  remains open, reported honestly rather than closed by this pass.
- Mean CPU 7.94%.

## Honest summary

The named bug is fixed and verified with zero regressions. The mimalloc containment measure is
real and demonstrated (isolated 120-read experiment) and materially changes the growth curve's
shape (a plateau instead of unbounded growth) even though the strict <5%/30min number isn't
reachable at this RSS baseline. The idle-live p95 hard gate remains unmet and is out of scope for
what Part B's investigation named — it is not masked or claimed as fixed here.
