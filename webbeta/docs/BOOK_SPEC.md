# BOOK_SPEC.md — Order-Book Panel v3

SHADOW/RESEARCH ONLY. Authority for this mission (per the mission brief): this document, not the
desktop's own book panel — the user has approved exceeding the desktop for this element. Builds on
`RENDER_SPEC.md` sections 10-11 (W2.2's v3-checkpoint recon: **sizes only, no order-count field
anywhere in the pipeline** — still true, unchanged, cited again below since it's load-bearing for
every label this spec defines).

## Part A — root cause, read fresh from the current source (not assumed from prior missions)

Current implementation: `webbeta/static/app.js`, function `renderBook()` (as of the W2.2 merge,
`static/app.js:592-730`).

**Confirmed: two render paths exist, exactly as the mission states.**
- `drawLevel()` (`:619-623`) — the "far zone." Positions every row via `toY(book.prices[i])`, the
  **true** price→pixel mapping shared with the main chart's y-axis. Row height is `barH`, a fixed
  fraction of `tickPx` (`:607-608`) — proportional to the current zoom, with **no minimum**: at wide
  zoom this shrinks toward (and below) 1px, exactly the "legacy thin-line" the mission means.
- `drawFixedRow()` (`:664-680`) — the "near-touch zone." Positions rows via `touchY ± rank *
  NEAR_TOUCH_ROW_PX` (`:655/660`) — a **fixed pixel pitch counted in rank order**, not derived from
  `toY(price)` at all. This is deliberately NOT price-aligned (W2.2's own design note at `:637-640`
  says so explicitly) — which is exactly the problem this mission's title names: two techniques,
  visually blurring into what looks like one continuous ladder but isn't one underneath.

**Correction to the mission's premise, found by re-reading the actual current code rather than
assumed**: the mission states there are "two independent width normalizations." Re-reading
`renderBook()` line by line shows this is **not accurate** — `maxSize` is computed exactly once per
call (`:604-606`, scanning the full `book.bidSizes`/`book.askSizes` arrays) and the *same* value
is passed into `bookScaleWidth()` (`:577-583`, one shared function) at every call site: `drawLevel`
(`:620`), `drawFixedRow` (`:665`), and the far-zone label-width recompute (`:693`). There is one
normalization today, not two. This is reported honestly rather than forced to fit the brief — the
same convention used in W2.1 (`price_curve` exclusion) and W2.2 (the desktop's own LOD-switch
correction). It does not change what Part B must build: a *fresh*, cleanly-specified shared
normalization is still the right design for v3 (see below) — specifically because today's `maxSize`
is taken over the **entire loaded book array** (out to `BOOK_WINDOW_TICKS=±400` ticks,
`book_flow_data_service.py:121`), not over what's actually *visible* in the current chart viewport.
A single whale order far outside the current zoom can still distort on-screen bar widths under
today's code — v3 fixes this by scoping the normalization to visible levels only, which today's
single-but-too-broadly-scoped `maxSize` does not.

## Part A — v3 spec

Two clearly separated elements, deliberately never rendered as one blurred zone again.

### 1. LADDER (canvas, price-true, full visible range, one code path)

- **Y-position**: `toY(price)` for every level, always — no fixed-pitch exception anywhere. This is
  the ladder's entire reason to exist: what you see always corresponds to where that price actually
  sits on the shared y-axis.
- **Row height**: `min(tickPx, LADDER_MAX_ROW_PX)` where `tickPx = |toY(0) - toY(TICK)|` (the same
  quantity `renderMain`'s cell renderer already uses) and `LADDER_MAX_ROW_PX = 20`. "Bar height
  follows price pitch (capped)" per the mission text — grows with zoom, capped so extreme close-in
  zoom doesn't produce absurd block heights.
- **Color**: bid green (`UP_COLOR`), ask red (`DOWN_COLOR`) — unchanged from v1/v2.
- **Width — one shared sub-linear scale, computed once per frame, over visible levels only**:
  `sqrt(size) / sqrt(maxVisibleSize)`, capped at `LADDER_MAX_BAR_FRAC = 0.82` of panel width.
  Chosen over log: sqrt keeps a small level's bar still-visible-but-honestly-small relative to a
  large one (log compresses the *whole* range so aggressively that a 1-lot and a 20-lot level look
  almost the same width — sqrt preserves more felt magnitude difference while still taming outliers).
  `maxVisibleSize` = the largest bid/ask size among levels whose price falls inside the **current**
  `[View.yMin, View.yMax]` — not the full loaded ±400-tick array (the correction above). Recomputed
  every frame the book changes or the view changes, exactly once, before any row is drawn.
- **Labels**: numeric size, drawn only when `tickPx >= LADDER_LABEL_MIN_PITCH_PX = 11` — one
  threshold, one place, no separate "always-on near touch" exception (that job now belongs entirely
  to the inset below).
- **Hover**: mousemove anywhere over the ladder canvas maps cursor Y → price via the inverse of
  `toY`, snaps to the nearest tick, and shows a `price / size` readout (DOM tooltip, positioned at
  the cursor) if that tick has a non-zero size on either side; hidden otherwise and on mouseleave.
- **The legacy thin-line routine (`drawLevel`) and the fixed-pitch routine (`drawFixedRow`) are both
  deleted** — replaced by one function that draws every visible level identically.

### 2. DOM INSET (real HTML, not canvas — "visually unmistakable as an inset")

A `<div>` overlay (bordered, semi-opaque backdrop) anchored near the current price, inside
`#bookCanvasWrap`, absolutely positioned, `pointer-events` limited to itself so it never blocks
ladder hover underneath it. Built from real DOM rows (not canvas pixels) specifically so its content
is trivially inspectable/testable and so it reads, at a glance, as a *different kind of UI element*
than the chart underneath it — the mission's "never blur together again."

- Top `INSET_LEVELS_PER_SIDE = 12` non-zero levels per side nearest the touch (same count W2.2
  established for its near-touch zone — the count itself was never the problem, only where it lived).
- Fixed row pitch (CSS, not canvas px-math) — always legible regardless of any zoom, by construction
  (a DOM `<div>` per row has its own natural text size, independent of the canvas's `toY` scale).
- Best bid / best ask rows visually emphasized (bold + accent border).
- A spread readout row: `best_ask_price - best_bid_price`.
- **Toggle**: a button in the toolbar, default **on**. State persisted in the URL as `?inset=0|1`,
  read on load and updated (via `history.replaceState`, no page reload) on toggle — matching how
  `?debug=1` already persists across reloads.
- Labels are size-only, per the standing W2.2 recon (RENDER_SPEC.md section 10) — no count is ever
  displayed or implied.

### 3. Current-price line + bubble

Unchanged (`drawCurrentPriceLine`, the price bubble in `renderBook`) — out of scope for this
mission's redesign; still drawn on the canvas, spanning both panels, exactly as W2.1 specified it.

## Part A — static mock

`webbeta/BOOK_SPEC_mock.png`, produced by `webbeta/tests/build_book_spec_mock.py` from **real,
read-only** v3 checkpoint data (NQU6, 2026-07-30, `book_flow_chart/cache/` — the same file the
desktop and daemon already read) — a real ±40-tick window around the real touch (best_bid_tick,
best_ask_tick read directly from the checkpoint's own `bid_sizes`/`ask_sizes` arrays, same technique
`_load_book_depth` uses). This mock is a standalone PIL rendering (not `static/app.js` — Part B
hasn't been written yet at this point in the mission) whose *only* purpose is to validate the layout
above before any implementation code exists.

---

## BOOK_SPEC.md v2 (W2.4 Part A) — institutional polish

Builds on v3 above (ladder + DOM inset architecture unchanged) with exact design tokens. All
constants below are declared once in `static/app.js` near the existing `LADDER_*`/`INSET_*` block —
no new "routine": every value below is a parameter to the SAME `computeLadderGeometry`/`drawLadder`
functions, varying continuously with zoom, never branching to a different code path.

### Row geometry
```
LADDER_GAP_THRESHOLD_PX = 3   // pitch (tickPx) below this: contiguous rows, no gap
LADDER_MAX_ROW_PX        = 20  // unchanged cap from v3
rowHeight(tickPx) =
    tickPx >= LADDER_GAP_THRESHOLD_PX ? min(tickPx - 1, LADDER_MAX_ROW_PX)   // visible 1px gap
                                       : max(tickPx, 1)                       // contiguous, floor 1px
```
Chosen so the visual transition between "gapped rows" and "contiguous rows" is smooth (both branches
agree at `tickPx == 3`: `3-1=2` vs `max(3,1)=3` — a 1px seam at the exact boundary, acceptable and
far less jarring than a discontinuous jump would be) and is a *value* of one continuous function of
zoom, not a different drawing routine.

### Bar geometry
```
LADDER_MIN_STUB_PX  = 6     // every nonzero level gets at least this much length (was 1px in v3)
LADDER_MAX_BAR_FRAC = 0.82  // unchanged: sub-linear (sqrt) width scale, shared per frame, capped
```
`width(size) = max(min(sqrt(size)/sqrt(maxVisible), 1) * cssW * LADDER_MAX_BAR_FRAC, LADDER_MIN_STUB_PX)`
— same shared-per-frame-over-visible-levels scale as v3, just with a real minimum stub instead of a
1px floor, so a lone 1-lot resting order is still a clean, visible mark rather than a hairline.

### Brightness/alpha ramp (magnitude still reads once width is capped)
```
LADDER_ALPHA_MIN = 0.55   // floor -- 0th percentile among currently-visible levels
LADDER_ALPHA_MAX = 1.00   // ceiling -- 100th percentile
alpha(size, visibleSizes) = LADDER_ALPHA_MIN + (LADDER_ALPHA_MAX - LADDER_ALPHA_MIN) * percentileRank(size, visibleSizes)
```
`percentileRank` = fraction of visible same-frame sizes `<=` this level's size (computed from one
sort of the visible-size array per frame, not per level — O(n log n) once, O(log n) lookup per
level via binary search). This is *in addition to* width, not instead of it: several levels bunched
near the 0.82 width cap (a common case once 3-4 large orders exist) still visually separate by
brightness, where width alone would make them look identical.

### Merged-with-chart
- **Gridlines**: the main chart gets faint horizontal gridlines at the same "nice" tick positions
  `niceStep()` already computes for the y-axis label strip (RENDER_SPEC.md section 9's
  `drawAxisStrips`), at `GRIDLINE_ALPHA = 0.12` — cited exactly from the desktop's own
  `main_plot.showGrid(x=True, y=True, alpha=0.12)` (`book_flow_chart_v3.py:1288`). The **same**
  gridlines are drawn on the book canvas at the **same** Y pixel positions (shared `toY`), so a line
  drawn on the main chart at, say, 28400.00 continues, unbroken in look, straight through the
  1px separator into the book panel at 28400.00 — this is the literal meaning of "merged."
- **Shared y-axis**: already true since W1 (both canvases read `View.yMin/yMax` through the same
  `makeYMapper`) — cited against the desktop's own `profile_plot.setYLink(self.main_plot)`
  (`:1297`), confirming this is the desktop's actual design, not a browser-only convenience.
- **Slim separator**: `#bookCanvasWrap`'s existing `border-left: 1px solid #222` (`index.html`) is
  already exactly this — confirmed, not changed.
- **Best bid/ask emphasis in the LADDER itself** (new — v3 only emphasized these in the DOM inset,
  not the canvas ladder): the best bid and best ask rows get a thin bright outline
  (`rgba(255,255,255,0.9)`, 1px), matching the desktop's own touch-marking conventions
  (`CellHighlightItem`, `RENDER_SPEC.md` section 3) applied here to the book's own touch rows.
- **Spread gap left visually empty**: no gridline, silhouette, or bar is ever drawn for price rows
  strictly between best bid and best ask — already true by construction (nothing to draw there,
  since both `bidSizes`/`askSizes` are zero in that range) and reconfirmed as an explicit invariant
  now that gridlines/silhouette are being added, so neither accidentally fills it.
- **Cumulative-depth silhouette** (new, toggleable, default on, `?depth=0|1` persisted the same way
  `?inset=0|1` already is): a faint (`DEPTH_SILHOUETTE_ALPHA = 0.07`) filled area tracing the
  running cumulative size from the touch outward on each side (typical "market depth" shape),
  drawn *behind* the per-level bars, using the *same* sub-linear width scale applied to the
  cumulative sum (so its own shape is legible rather than immediately saturating).

### Typography
- Size labels: monospace (`"10px ui-monospace, 'Courier New', monospace"`), **right-aligned to a
  fixed column** at the panel's right edge (`ctx.textAlign = "right"`, anchored at `cssW - 4`) —
  decoupled from bar length, so labels form a clean numeric column exactly like a real ladder,
  instead of v3's bar-length-relative left-aligned text. Shown when `tickPx >= 12` (bumped from
  v3's `11` to match this spec's own stated `~12px` exactly).
- No price column in the ladder itself — prices remain hover-only (tooltip) and inset-only, per v3;
  reconfirmed, not changed.
- Color palette: `UP_COLOR`/`DOWN_COLOR` (`#00d27a`/`#ff4d6d`) — already the exact same constants
  the main chart's cells use; reconfirmed, not changed.

### Inset polish
Same `INSET_LEVELS_PER_SIDE=12`/`INSET_ROW_PX=15` tokens; row font switched to the same monospace
stack as the ladder's labels for visual consistency between the two elements; price column
right-aligned, size column right-aligned, matching the ladder's new right-aligned convention.

### Mock
`BOOK_SPEC_mock_v2.png` (`tests/build_book_spec_mock_v2.py`), from the same real, read-only v3
checkpoint window as the original mock, now rendering the tokens above (min stub, alpha ramp,
gridlines, depth silhouette, right-aligned monospace labels, best-bid/ask outline) before any
`static/app.js` changes are made.
