# W23_REPORT.md — BETA W2.3: Order-Book Panel v3

SHADOW/RESEARCH ONLY. Branch `webbeta-book` off `master`. Cache daemon (PID 2527) never touched,
signaled, or restarted — confirmed running throughout and confirmed by
`git diff master...webbeta-book --stat` that zero files under `webbeta/server/`, `webbeta/simulator/`,
or `book_flow_chart/` were touched; only `webbeta/BOOK_SPEC.md`, `webbeta/static/`, `W23_REPORT.md`,
and new files under `webbeta/tests/`/`webbeta/montage_out_w23/` changed (plus retiring one obsolete
test, see Part C(g) below).

## Part A — root cause, read fresh (not assumed from prior missions)

Confirmed exactly as the mission states: **two render paths** existed in the W2.2 final code
(`drawLevel` — price-true via `toY()`, no minimum row height, the "legacy thin-line"; `drawFixedRow`
— fixed pixel pitch counted in rank order, deliberately *not* price-aligned, W2.2's own near-touch
zone). Both are now deleted.

**Correction to the mission's premise, found by re-reading the actual current code rather than
assumed**: "two independent width normalizations" does not match reality — W2.2's `maxSize` was
already computed once and shared by both paths. Reported honestly (BOOK_SPEC.md, same convention as
every prior correction in this series) rather than forced to fit. It still motivated a real fix: that
one shared `maxSize` spanned the full loaded ±400-tick array, not the *visible* viewport — v3's
normalization is scoped to visible levels only.

`BOOK_SPEC.md` specifies v3 (LADDER + DOM INSET, detailed below) and `BOOK_SPEC_mock.png` — a
standalone PIL rendering from real, read-only v3 checkpoint data — validated the layout before any
`static/app.js` code was written.

## Part B — implementation

- **LADDER** (`computeLadderGeometry`/`drawLadder`): one code path, every visible level. `y =
  toY(price)` always. Row height `min(tickPx, LADDER_MAX_ROW_PX=20)`. Shared `sqrt` width scale,
  capped at 82% of panel width, computed once per frame over levels within `[View.yMin, View.yMax]`
  only. Labels gated by one pitch threshold (`LADDER_LABEL_MIN_PITCH_PX=11`), no near-touch
  exception.
- **DOM INSET** (`renderInset`, `#bookInset`): real HTML rows, bordered/backdropped card, top 12
  levels/side, best bid/ask bold, spread row. Toggle (`#insetToggleBtn`) persists `?inset=0|1` via
  `history.replaceState`, default on.
- **Hover** (`initBookHover`): mousemove over the ladder maps cursor Y → price, snaps to nearest
  tick, shows a DOM tooltip with price+side+size.
- Runtime instrumentation (`window.__ladderDrawCount`) and a shared geometry test hook
  (`window.__computeLadderGeometry`) added for Part C's gates.

## Part C — gates

| Gate | Result | Detail |
|---|---|---|
| (a) Uniformity (hard gate) | **PASS** | legacy routines absent (grep); draw-count == independent count, 5/5 samples |
| (b) Shared scaling | **PASS** | 4/4: uniform, single-outlier, heavy-tail, distance-independence |
| (c) Price truth | **PASS** | 5/5 y-spans, 0 problems, independently-reimplemented mapping |
| (d) Inset | **PASS** | 201/201 real book_update deltas, 0 mismatches vs an independent reference |
| (e) Hover | **PASS** | 7/7 sampled positions (3 bid, 3 ask, 1 empty tick) |
| (f) Perf, panel on | **PASS** | 7/7 combinations, 57.5-61fps median |
| (g) Regression | **PASS** | flicker, W2.1+W2.2 geometry parity, live parity, stale-badge all green |

**(a) detail**: static grep confirms `drawLevel`/`drawFixedRow`/`nearTouchIndexSet`/
`bookScaleWidth`/`NEAR_TOUCH_LEVELS`/`NEAR_TOUCH_ROW_PX`/`BOOK_MAX_BAR_FRAC` no longer exist as
declarations or references (only explanatory comments naming them to describe the redesign).
Runtime: `window.__ladderDrawCount` exactly equalled an independently-computed count of non-zero
visible levels across 5 live samples (61, 80, 80, 80, 80).

**(b) detail**: uniform book → all 41 levels identical width (180.4px, i.e. at the cap since all
sizes equal maxVisible); single 500-lot outlier → outlier exactly at the cap, every other level's
width matches the documented curve within 0.5px, cap never exceeded; heavy tail (1 through 16384) →
0 problems across 15 levels; **distance independence** — two levels of identical size 42 placed at
opposite ends of the visible range rendered the *exact* same width (116.9125621992778px both,
difference < 1e-9) — directly disproving any leftover near-touch-vs-far distinction.

**(c) detail**: at y-spans of 5/20/50/200/1000 price points, every ladder level's y-center matched
an independently reimplemented `price↔pixel` formula within 1px (actual observed error: 0px in every
sample).

**(d) detail**: 201 real `book_update` deltas over an accelerated simulator (`V3_CADENCE_S=0.25s`),
each validated against a Python reference computed directly from that update's own wire payload —
bid rows, ask rows, best-bid/ask emphasis, and spread all matched exactly, 0 problems across all 201.
Two test-harness bugs were found and fixed while building this gate (not product bugs): a
capture-timing race (reading the DOM before `frame()`'s next paint — fixed by deferring capture via
`requestAnimationFrame`) and a display-order mismatch in the test's own reference construction
(ask rows display furthest-first; the reference wasn't reversed to match) — both documented in the
test file's commit.

**(e) detail**: 3 bid levels, 3 ask levels, and 1 empty tick — all 7 sampled hover positions returned
the exact expected price/side/size, or correctly hid the tooltip for the empty tick.

**(f) detail** (panel on, same 5 zoom scenes + cadences as W2.2's `test_zoom_perf.py`, now with a
synthetic book mutating every tick alongside the main chart):

| Scene | Cadence | Median fps |
|---|---|---|
| full_session_zoomout (115778 cells, 80 book levels) | 8x | 57.5 |
| full_session_zoomout | live | 60.0 |
| default (20870 cells, 35 book levels) | 8x | 60.0 |
| default | live | 60.5 |
| mid | 8x | 61.0 |
| tight | 8x | 61.0 |
| extreme_closeup | 8x | 60.5 |

Memory/swap pre-check (added directly in response to W2.2's environment-caused test kill): checked
at startup and before the heaviest scene, capped retries with a warning, never blocks forever. Both
checks reported OK in this run (38.7GB free, 88% swap — swap alone doesn't trip the warning; free RAM
does, and there was plenty).

**(g) detail**: `test_live_flicker.py` PASS (55 bars, 0 gaps); `test_geometry_parity.py` (W2.1) PASS
(0 problems, 4/4 scenes); `test_zoom_geometry_parity.py` (W2.2) PASS (0 problems, 5/5 scenes,
115778 cells at the widest); `test_parity.py` PASS (3/3 scenes); `test_mode_badge.py` PASS (3/3
modes); `test_live_parity_desktop.py` (10-minute run) PASS, 0 problems, 599/596 comparable samples —
completed cleanly this run, no environment kill. Book-panel stale behavior spot-checked directly:
`isBookStale()` unchanged, ladder greys out, `STALE` badge shows, inset dims via CSS opacity while
remaining legible (an intentional refinement for the DOM inset specifically — a card stays
informative-but-marked-stale rather than blanking, a deliberate design choice, not a regression,
noted here rather than silently decided).

One obsolete test was retired: `test_book_panel_v2.py` referenced `NEAR_TOUCH_LEVELS`/
`NEAR_TOUCH_ROW_PX`, deleted in Part B — it would now fail with a `ReferenceError`, not a real
regression signal, and its coverage is fully superseded by this mission's own gates (d)/(e).
`build_montage_w22.py`'s one small dependency on it was inlined so that script stays runnable.

## Part D — artifacts

- `BOOK_SPEC.md` + `BOOK_SPEC_mock.png` — the Part A design spec and its pre-code validation mock.
- `montage_out_w23/BOOK_V2_VS_V3_MONTAGE.png` — same real v3-checkpoint data through the W2.2 final
  state (v2, extracted from commit `c404d50`) vs the current v3: the seam between v2's fixed-pitch
  near-touch zone and its surrounding thin-line is directly visible on the left; v3's one continuous
  price-true ladder plus a clearly separated inset card is on the right.
