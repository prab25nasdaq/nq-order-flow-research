# W22_REPORT.md — BETA W2.2: Zoom-Invariant Rendering + Interaction + Order-Book Readability

SHADOW/RESEARCH ONLY. Branch `webbeta-zoom` off `master`. Cache daemon (PID 2527) never touched,
signaled, or restarted — confirmed running throughout (`ps -p 2527`) and confirmed by
`git diff master...webbeta-zoom --stat` that zero files under `webbeta/server/`, `webbeta/simulator/`,
or `book_flow_chart/` were touched in this branch; only `webbeta/RENDER_SPEC.md`, `webbeta/static/`,
and new files under `webbeta/tests/`/`webbeta/montage_out_w22/` changed.

## Part A — root cause + spec, with one correction to the mission's own premise

- **Root cause of the blocky mode**: `static/app.js`'s `LOD_BAR_THRESHOLD=60` switched to a
  coarser, aggregated renderer (`drawCellCandlesLOD`) whenever the viewport spanned more than 60
  bars — an unverified W1-era guess at "mirroring the desktop's LOD," never actually checked against
  the desktop's real thresholds.
- **Correction found and reported, not silently forced to fit the brief**: the mission text asserts
  the desktop has "no representation change" at extreme zoom-out. Direct inspection of
  `book_flow_chart_v3.py` shows this is false — `_compute_lod()`'s `LOD_WIDE`/`LOD_OVERVIEW`
  thresholds (>500 visible bars) skip per-cell rendering entirely in favor of a coarser aggregated
  raster (`WideOFILevelCellRasterItem`), and `max_visible_bars=1200` hard-caps the desktop's
  reachable zoom-out so it *always* lands in that aggregated mode at its widest. Part B's "one
  renderer, no blocky mode, at any zoom" is therefore a deliberate **improvement over** the desktop,
  not a replication of it (documented in `RENDER_SPEC.md` section 8, mirroring the same honest
  divergence pattern as W2.1's `price_curve` exclusion).
- **Mouse dynamics** traced to pyqtgraph's own `ViewBox`/`AxisItem` defaults (no override in
  `book_flow_chart_v3.py`, confirmed by grep), cited against the installed library source: wheel/
  right-drag scale (cursor- or button-down-anchored, both axes over the plot body, one axis over an
  axis strip); left-drag pans; Reset View / follow-live precedence (`user_view_override` beats
  `follow_live_requested`, cleared only by `_reset_view()` — re-checking the follow box alone does
  not restore it). Full citations in `RENDER_SPEC.md` section 9.

## Part B — one renderer, bitmap caching, real interaction dynamics

- Deleted `drawCellCandlesLOD`/`LOD_BAR_THRESHOLD` entirely. `computeVisibleCellGeometry()` is the
  only per-cell computation at any zoom span.
- Added a bitmap cache (`sealedCache`) for sealed-bar cells, keyed on the view transform + canvas
  size + `lastClosedBarIdx`; the forming bar is always painted live on top every frame. Verified
  byte-identical geometry with caching on vs off (`window.__setCachingEnabled`) at a 1200-bar wide
  zoom: 14072/14072 cells, equal.
- Implemented cursor-anchored wheel zoom, axis-strip single-axis wheel/drag, right-drag scale
  (both/single axis), left-drag pan (both/single axis), Reset View — all verified against scripted
  Playwright sequences (gate b).

## Part C — order-book v3 checkpoint recon + panel v2

**Recon finding (RENDER_SPEC.md section 10): sizes only, no order-count field anywhere in the
pipeline.** Exhaustively grepped `build_book_flow_level_cache.py` (every `save_v3_state` call site)
and the raw quote-update reader (`book_flow_lib.read_quote_updates`: columns `event_ts_ns, price,
size_eff` only) — zero count-like keys exist at any layer, from the raw Rithmic feed to the
checkpoint. Every v2 label is size-only; nothing is fabricated.

Panel v2: sub-linear (sqrt) bar width capped at 82% of panel width so an outlier resting order can't
hide smaller levels (exact labels still carry true magnitude); a fixed-height, always-legible
near-touch zone (12 levels/side, 15px rows regardless of either axis's zoom — a deliberate,
mission-permitted divergence from strict main-chart y-axis alignment for exactly these ~24 rows);
best bid/ask white-outline emphasis; spread readout; subtle row banding. Stale behavior
(`isBookStale()`/`BOOK_STALE_SECS`) unchanged; all v2 additions gated behind `!stale`.

## Part D — gates

| Gate | Result | Detail |
|---|---|---|
| (a) Zoom invariance (hard gate) | **PASS** | 5/5 scenes, 0 problems, 0 representation switches |
| (b) Interaction dynamics | **PASS** | 7/7 scripted wheel/drag/reset assertions |
| (c) Order-book panel v2 | **PASS** | 4/4 sub-gates (labels, legibility, emphasis, stale) |
| (d) Perf, all 5 zoom levels | **PASS** | 7/7 combinations, 58-61fps median |
| (e) Regression | **PASS** | flicker, W2.1 geometry parity, live parity suites all green |

**(a) Zoom invariance detail** (`tests/test_zoom_geometry_parity.py`), real production data
(NQU6 2026-07-30, 1317 real bars, 124125 real cell rows), diffed against the REAL, unmodified
`BookFlowLevelCellItem.set_data()`'s actual painted output:

| Scene | Bars | Desktop cells | Browser cells | Problems |
|---|---|---|---|---|
| full_session_zoomout | 1200 | 115778 | 115778 | 0 |
| default | 180 | 20870 | 20870 | 0 |
| mid | 100 | 11540 | 11540 | 0 |
| tight | 40 | 4765 | 4765 | 0 |
| extreme_closeup | 12 | 1416 | 1416 | 0 |

100% cell presence, RGB exact, alpha within the documented ±14/255 band tolerance, width_fraction
within 1%, **exact cell-count match at every scene including the widest** — zero representation
switches, by measurement, not just by code inspection.

**(b) Interaction detail**: wheel cursor-anchor drift 0.0064 bars / 0.0000 price (both ≈0); x-axis-
strip and y-axis-strip wheel each confirmed single-axis; right-drag on the plot body confirmed
both-axis scale; left-drag confirmed pan with span unchanged; left-drag on the x-axis strip confirmed
x-only pan; Reset View confirmed clearing `View.userSet`.

**(c) Order-book detail**: 200/200 real book_update deltas (accelerated simulator) with 0 label
mismatches between the wire payload and `Data.book`; 16/16 expected near-touch rows visually
distinct (pixel-sampled) at all 5 zoom scenes crossed with a normal + a deliberately extreme y-zoom
(10 combinations); best bid/ask + spread confirmed present on a synthetic book; `isBookStale()`
confirmed unchanged.

**(d) Perf detail** (`tests/test_zoom_perf.py`, cache on, real 1200-bar data injected directly,
driven by a synthetic forming-tick stream at both cadences):

| Scene | Cadence | Median fps | Cells |
|---|---|---|---|
| full_session_zoomout | 8x (~44ms) | 58.0 | 115778 |
| full_session_zoomout | live (~350ms) | 60.0 | 115778 |
| default | 8x | 60.5 | 20870 |
| default | live | 60.0 | 20870 |
| mid | 8x | 60.0 | 11540 |
| tight | 8x | 61.0 | 4765 |
| extreme_closeup | 8x | 60.5 | 1416 |

The near-flat fps across an 80x cell-count range is itself evidence the bitmap cache works as
designed — cost no longer scales with visible cell count once cached.

**(e) Regression detail**: `test_live_flicker.py` PASS (55 bars, 0 gaps); `test_geometry_parity.py`
(W2.1 default view) PASS (0 problems, 4/4 scenes); `test_parity.py` PASS (3/3 scenes);
`test_live_parity_desktop.py` (10-minute desktop-vs-hub run) — first attempt was killed by the host
environment mid-run (system swap was at ~90% from unrelated, genuinely-running production processes
at the time — `book_flow_cache_daemon.py`, `model_feature_master_daemon.py`,
`ofi_level_decision_cache_daemon.py`, plus a live desktop chart window already open — not a code
issue); re-run completed cleanly, **PASS, 0 problems, 599/597 comparable samples**. Reported here
rather than silently retried and forgotten.

## Part E — artifacts

- `webbeta/montage_out_w22/ZOOM_LADDER_MONTAGE.png` (+ 5 per-scene pairs): desktop vs browser at
  all 5 zoom levels, real data — matching price-path shape and color pattern from 115778 cells down
  to 1416.
- `webbeta/montage_out_w22/BOOK_BEFORE_AFTER.png`: same synthetic book (one 400-lot outlier),
  pre-Part-C v1 panel (linear scaling, checked out from commit `d325db1`) vs the current v2 panel —
  v1 squashes every level to near-identical width against the outlier; v2 clearly differentiates
  5-through-30-lot levels and adds labels/spread/best-bid-ask emphasis.
