# Book Flow Chart — Step 2 Part B: Live-Mode RSS Root-Cause Verdict

SHADOW / RESEARCH ONLY. No fixes applied in this part — diagnosis only, per the mission brief.
All measurements read-only against the live cache daemon (PID 2527); nothing written to any
cache/data file.

## Verdict (one paragraph)

The idle-live RSS growth (+20-24% over 10 minutes, consistent across every measurement in this
part) is driven by a single, specific over-invalidation bug, not generic allocator fragmentation:
`_current_data_sig()` (`book_flow_chart_v3.py:1376`) unconditionally folds the live daemon's
heartbeat file's raw `(mtime_ns, size)` into the chart's data-signature tuple whenever
`live_latest_mode` is True, and that heartbeat file is rewritten by the cache daemon on every
~2-second write cycle *regardless of whether any bar or cell actually changed*. Because the
signature changes on almost every poll even when nothing on screen has, it defeats both the
"data unchanged" fast-path short-circuit (`_load_data_if_needed`, line 1474) and the render
coalescing check (`_reload`, line 1919) — forcing a full `pd.read_parquet()` reparse of the
compact cache *and* a full re-render (including a fresh `QtGui.QPicture()` rebuild in
`BookFlowLevelCellItem.set_data()`/`WideOFILevelCellRasterItem.set_data()`) on nearly every single
poll cycle, confirmed directly: 273 QPicture rebuilds occurred in a 900s window against only ~4
real bar rolls in that same window (a ~68x over-invalidation rate). Each unnecessary
`pd.read_parquet()` call allocates through PyArrow's parquet reader, which is backed by Arrow's
own `mimalloc` memory pool and issues raw `mmap()` syscalls for its column buffers — confirmed via
`memray --native` at both a 20s smoke scale and the full 10-minute native capture: in both,
`unix_mmap_prim_aligned` (reached via
`arrow::PoolBuffer::Resize → BaseMemoryPoolImpl<MimallocAllocator>::Reallocate → mi_heap_malloc_generic → unix_mmap`)
accounted for ~89% of all tracked native "own memory" (~1.07GB in each capture) — a mechanism
entirely invisible to `tracemalloc` (which tracked only ~10MB of Python-level growth against ~87MB
of total observed RSS growth over the same 15-minute window, under 12% of the total) and untouched
by CPython/glibc allocator tuning, exactly as the A/B results confirm below. There is no Python
reference-cycle leak (`gc.collect()` freed 0 objects at the end of every run, consistent with
Step 1's finding).

## Supporting evidence

### 15-minute deep profile (5s cadence; `perf_instrumentation/rss_deep_profile_15min.csv`)
- RSS: 364.0MB → 450.6MB (**+23.8%**) over 900s.
- `gc.collect()` at end: **0 objects freed** — rules out a Python-level reference-cycle leak.
- Scene item count: stable, fluctuating 137-159 — rules out an unbounded QGraphicsScene item leak.
- QPicture rebuilds: **273** (cell) + **273** (raster) over 900s, vs. ~4 real bar rolls in the same
  window (daemon rolls a vol500 bar roughly every 3.7 minutes during this measurement's quiet
  period) — the direct, measured signature of the over-invalidation bug.
- tracemalloc top growth site: `pathlib/_local.py:274` (+7.5MB, 21 allocations) — the single
  largest *Python-tracked* growth site, but tiny next to the ~87MB of total RSS growth in the same
  window; the remainder is not visible to tracemalloc at all.

### memray --native (20s smoke + full 10-minute capture; `perf_instrumentation/memray_native_10min.bin`)
- `memray summary -s 3` (sorted by Own Memory) on both captures: **`unix_mmap_prim_aligned`
  ~1.07GB, ~89% of tracked own memory**, in only 21-22 allocation calls — near-identical absolute
  size at 20s and at 600s, indicating Arrow's mimalloc pool reaches and maintains a large working
  set quickly through repeated allocate/reuse churn rather than growing without bound in this
  window; the psutil-observed RSS creep on top of that working set is the slower, smaller residue
  (consistent with the ~87MB/900s figure above).
- Full call stack (via flamegraph HTML) traced end-to-end:
  `parquet::arrow::FileReaderImpl::DecodeRowGroups → ReadColumn → ColumnReaderImpl::NextBatch →
  LeafReader::LoadBatch → TypedRecordReader::Reserve/ReserveLevels → arrow::PoolBuffer::Resize →
  arrow::PoolBuffer::Reserve → BaseMemoryPoolImpl<MimallocAllocator>::Reallocate →
  mi_heap_malloc_zero_aligned_at_generic → _mi_malloc_generic → mi_heap_get_default →
  mi_thread_init → _mi_os_zalloc/_mi_os_alloc → mi_os_prim_alloc_at → _mi_prim_alloc →
  unix_mmap → unix_mmap_prim_aligned` — i.e. every parquet read of the compact cache.

### A/B comparisons (10 min each, identical live scenario, parallel; `perf_instrumentation/ab/`)

| Variant | RSS start→end | Growth % | Slope (MB/min) | p95 frame ms | CPU % |
|---|---|---|---|---|---|
| baseline | 340.8→417.8 | +22.6% | 7.70 | 103.8 | 10.9 |
| MALLOC_ARENA_MAX=2 | 336.5→405.5 | +20.5% | 6.90 | 113.3 | 11.4 |
| PYTHONMALLOC=malloc | 347.3→429.9 | +23.8% | 8.26 | 121.6 | 11.4 |
| pyqtgraph OpenGL on | 340.4→415.1 | +22.0% | 7.47 | 113.6 | 11.0 |
| poll cadence 350ms | 340.6→424.0 | +24.5% | 8.34 | 130.3 | **20.8** |
| poll cadence 1000ms | 339.5→419.5 | +23.6% | 8.00 | 114.4 | 13.8 |

- **MALLOC_ARENA_MAX=2** and **PYTHONMALLOC=malloc**: no meaningful RSS improvement (within
  run-to-run noise on a real, variably-active live market) — expected, since Arrow's mimalloc pool
  bypasses both glibc's malloc-arena machinery and CPython's pymalloc entirely.
- **OpenGL on**: no meaningful difference — rules out the pyqtgraph rendering backend as a
  contributor.
- **Poll cadence**: RSS growth-% itself is noisy across a real live market (not a controlled
  replay), but **CPU% scales cleanly with poll frequency** — 350ms cadence draws ~2x the CPU of
  baseline (20.8% vs 10.9%) and ~1.5x the 1000ms cadence (13.8%) — for the *same* underlying rate
  of real bar rolls. This is the clean confirmation that the cost is driven by poll frequency, not
  by actual market data change rate, exactly as the over-invalidation theory predicts.

## Root-cause chain (summary)

```
heartbeat file mtime changes every ~2s daemon cycle (content usually unchanged)
   -> _current_data_sig() includes the raw heartbeat file sig unconditionally in live-latest mode
   -> data-unchanged fast-path AND render-coalescing checks both defeated every poll
   -> full pd.read_parquet() reparse of the compact cache, every poll
        -> PyArrow's Arrow-mimalloc memory pool -> mmap() -> RSS growth (invisible to tracemalloc)
   -> full re-render incl. fresh QtGui.QPicture() rebuild, every poll
        -> Qt-internal buffer churn, same class of native/untracked allocation
```

Fix candidate for Part C (not applied here): `_sync_live_latest_from_heartbeat()` is already
called unconditionally inside `_current_data_sig()` (line 1354) and is what actually performs
date-rollover detection by comparing `heartbeat["active_date"]` to `self.date` — a genuine
rollover already changes `self.date`, which is *already* a separate component of the sig tuple
(line 1362). The raw heartbeat-file sig component (line 1376) is therefore redundant for
correctness and purely harmful for performance: removing it (in live-latest mode, replacing it
with the same `(0, 0)` placeholder already used for historical-date viewing) should eliminate the
over-invalidation without weakening rollover detection at all.
