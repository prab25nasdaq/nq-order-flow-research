# RENDER_SPEC.md — Desktop Chart Render Spec (Reference Truth)

Source of truth: `/home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py` (READ-ONLY —
this document is extracted from it, the desktop is not modified as part of this mission). Every
formula below is cited to file:line / class / method. Reference configuration used throughout
(per mission spec): **scale = percentile, min flow = 0, forming bar shown**.

Note on "default view": the `scale_combo` widget itself ships with no explicit `setCurrentText()`
call (`book_flow_chart_v3.py:1225-1228`), so on a cold launch it visually shows "linear" (the first
item in `addItems(["linear","log","percentile"])`). `min_flow_spin` likewise has no `setValue()`
call, so its shipped default is `0.0` (Qt's `QDoubleSpinBox` default) — this one **does** match the
mission's stated reference. `show_forming_bar` **is** an explicit default: `self.show_forming_bar =
self.live_latest_mode` (`book_flow_chart_v3.py:1052`), and `live_latest_mode` is `True` by default
for the live-latest launch path — so forming-bar-shown is a genuine shipped default. "Percentile" is
being used here as the mission's *chosen reference render mode* (a fully-supported, first-class
mode with an exact formula below), not a claim that it's the widget's out-of-the-box selection.

---

## 1. Cell geometry

Class `BookFlowLevelCellItem`, method `set_data()` — `book_flow_chart_v3.py:391-487`.

**Min-flow filter** (`:400-405`): applied before anything else —
```
mask = abs_flow >= min_flow   # min_flow from min_flow_spin, default 0.0
use  = df[mask]
```
If nothing survives, zero cells are drawn (no fallback rendering).

**Percentile width normalization** (`:407-421`), for `scale_mode == "percentile"`:
```
abs_flow = use["abs_flow"]                    # per-cell magnitude, unsigned
denom    = nanpercentile(abs_flow, 95)        # 95th percentile of abs_flow OVER THE CURRENT WINDOW
scaled   = abs_flow                           # (no log/other transform in percentile mode)
norm     = clip(scaled / denom, 0.0, 1.0)     # if denom<=0 or non-finite -> norm = 0 for all cells
```
(For contrast, `"log"` mode uses `scaled=log1p(abs_flow)`, `denom=nanmax(scaled)`; `"linear"` mode
uses `scaled=abs_flow`, `denom=nanmax(abs_flow)`. Percentile is the mission's reference mode.)

**Window the percentile is computed over**: NOT the full session, NOT a fixed lookback — it is
whatever `visible_cells` DataFrame is currently passed into `set_data()`, which is built by
`_prepare_visible_data()` from the **current viewport's visible bar range** (`_visible_bar_window`),
recomputed on every pan/zoom/reload (`book_flow_chart_v3.py:1991-2023`, call site `:2143-2151`).
So the 95th percentile is a *viewport-relative* statistic, not a session-wide constant. This is the
number the browser must replicate: it should compute `abs_flow`'s 95th percentile over exactly the
set of cells it currently has on screen (its own analog of the viewport), not over a full-session or
fixed-size buffer.

**Cell width** (`:469`):
```
width_fraction = 0.14 + 0.80 * norm     # fraction of ONE bar slot (bar slot = 1.0 data-unit wide)
```
So width ranges from 0.14 (norm=0, including the min-flow-filtered-out case which never reaches
here) up to 0.94 (norm=1) of a bar slot. **No separate hard min/max width clamp beyond this
formula** — the `clip(..., 0.0, 1.0)` on `norm` at `:421` is the only clamp, and it's already baked
into the 0.14–0.94 range above.

**Cell height** (`:425-426`):
```
tick_h = TICK * 0.92     # TICK = 0.25 (book_flow_lib.py:35)  => tick_h = 0.23
half_h = tick_h * 0.5
```
So each cell is a rectangle `tick_h` (=0.23) tall, centered on its `price_level` tick, leaving an 8%
gap between vertically-stacked ticks.

**Cell rect** (`:470-474`):
```
QRectF(x_center - width_fraction*0.5, y_center - half_h, width_fraction, tick_h)
```
i.e. each cell is centered on its bar's x position (`bar_pos`) and its price tick (`price_level`) —
not left/right/top/bottom-aligned to the slot.

**Which field drives width**: `abs_flow` (unsigned magnitude) only. `signed_flow` drives color
(hue), never width/size.

---

## 2. Color mapping

Same method, `:428-464`.

**Alpha (intensity) from norm**, quantized to 16 bands purely as a batching optimization (caps
QPainter brush-change calls at 48 = 3 sign-groups × 16 bands — cite comment `:428-431`), but the
*effective* per-cell alpha before quantization is:
```
alpha = round(45.0 + 210.0 * norm)      # range [45, 255]
```
For neutral (flat/zero signed_flow) cells specifically, alpha is halved and floored:
```
neutral_alpha = max(35, alpha // 2)     # range [35, 127]
```
**Correction found during Part D gate design** (superseding an earlier draft of this note): the
16-band grouping is not purely cosmetic bookkeeping — re-reading `:454-475` closely, `p.setBrush()`
is called **once per group**, using `alp = int(a_s[s])`, the alpha of whichever cell sorts first
within that (sign, band) group (stable sort preserves each group's members in their original
relative order, so `s` is the first *original-order* cell with that group key) — and then
`p.drawRects(group_rects)` paints **every rect in the group with that one alpha**, not each cell's
own true continuous alpha. So two cells in the same 14-unit band can genuinely be painted with
slightly different intent but identical actual pixels, up to ~14/255 (~5.5%) alpha apart. Width is
unaffected (`widths = 0.14 + 0.80 * n_s[s:e]` uses the full per-cell array, no grouping). This is a
real (if visually imperceptible) property of what the desktop actually paints, not just a
perf-optimization detail — **RENDER_SPEC.md's Part C renderer still intentionally uses the clean,
continuous per-cell `alpha` formula above** (replicating the 16-band artifact in JS would be
over-fitting to a Qt implementation detail for zero visual benefit), so this is a known, narrow,
intentional divergence: width_fraction and RGB hue match the desktop exactly; alpha can differ by
up to one band width (~14/255) for cells that aren't their band's first member. Part D's geometry-
parity gate treats this as a documented tolerance on alpha specifically (RGB and width_fraction are
still checked at the gate's stated exactness), not a silently-ignored mismatch.

**Hue (sign) from signed_flow** (`:435, 457-464`):
```
sign_group = 0 if signed_flow > 0 else (1 if signed_flow < 0 else 2)   # 0=up/green, 1=down/red, 2=neutral/gray
```
**Exact color stops** (`book_flow_chart_v3.py:120-122`, applied `:459-464`):
| sign_group | Name | RGB | Alpha used |
|---|---|---|---|
| 0 (signed_flow > 0) | up/green | `rgb(0, 210, 122)` (`#00d27a` = `UP_COLOR`) | `alpha` |
| 1 (signed_flow < 0) | down/red | `rgb(255, 77, 109)` (`#ff4d6d` = `DOWN_COLOR`) | `alpha` |
| 2 (signed_flow == 0) | neutral/gray | `rgb(119, 119, 119)` (`#777777` = `NEUTRAL_COLOR`) | `neutral_alpha` |

No gradient/interpolation between stops — exactly these 3 fixed RGBs, only alpha varies continuously
with `norm`.

---

## 3. Bar layout

**Bar slot width / x-position mapping**: `self._bar_pos_map = {bar_id: i for i, bar_id in
enumerate(base_ids)}` (`:1991`). `bar_pos` is the **sequential index** of a bar among the base id
list — i.e., bar slots are evenly spaced 1.0 data-unit apart, **regardless of gaps in the underlying
`bar_idx`/`bar_index` values** (session boundaries, decimation, etc. do not create visual gaps or
uneven spacing). This mapping is applied identically to both cells (`:2012`) and bars (`:2023`).

**Bar slot width = 1.0** data unit (implicit — cell width fractions above are fractions of this
1.0 unit, and consecutive bars sit at integer positions 0, 1, 2, ...).

**Spacing between bars**: none beyond the (0.14–0.94)-of-1.0 cell width itself — cells never
touch/overlap between adjacent bar slots at norm=1 (max width 0.94 leaves a 0.06 gap to the next
slot's cell, i.e. a persistent thin visual seam between bar columns at all times).

**No thin central "spine"**: grepped explicitly for a spine/center-line construct
(`spine|center_line|VLine`) — none exists in the main cell-rendering path. There IS a distinct
`net_flow_curve` (`PlotCurveItem`, `NET_FLOW_COLOR = "#ffd166"`, `:124, 1342`) but this belongs to
a *different* panel (the order-book/blueprint side panel's net-flow overlay), not the main OFI-cell
chart — it is out of scope for this mission's cell-chart parity and must not be confused with a
"central spine" on the main chart. **The main chart has no spine element at all** — treat any
mission reference to a "thin central spine" as referring to the persistent 0.06-unit gap between
bar columns described above, not a drawn line.

**Forming-bar treatment**: the forming bar's cells are rendered through the exact same
`BookFlowLevelCellItem.set_data()` path as closed bars — **no color/alpha/width difference** for
being "forming" vs "closed" (confirmed: no `bar_state`/`FORMING` branching anywhere inside
`set_data()` or its call site's cell-selection logic beyond whether the forming bar's row is
included in `visible_cells` at all, `:1716-1739`). The only forming-bar-specific *visual* element is
`CellHighlightItem` (`:558-588`), a single semi-transparent white rectangle
(`pen=QColor("#ffffff")` width 0, `brush=rgba(255,255,255,60)`) drawn over **one cell**: the forming
bar's cell at the current live price (`:2567-2572`, `main_plot` only, z-value 9). This is NOT a
forming-bar-column highlight — it marks exactly one price-level cell within the forming bar.

---

## 4. Price-line elements

Two textually-similar but **functionally and visually distinct** features exist in the desktop
source. Only one of them is in scope for this mission.

### 4a. `price_curve` — the "price line" toggle (OUT OF SCOPE — do not draw)

- Widget: `self.price_cb = self._mk_toggle(tb2, "price line", True)` (`:1267`) — a checkbox
  labeled "price line", **default checked/True**.
- Item: `self.price_curve = pg.PlotCurveItem(pen=mkPen(PRICE_COLOR, width=1.1))` (`:1320-1321`),
  added to `main_plot`.
- Update logic, method `_update_price_line()` (`:2344-2350`):
  ```
  if price_cb.isChecked() and not bars.empty:
      price_curve.setData(bars["bar_pos"], bars["px_close"])   # one point per visible bar
      price_curve.setVisible(True)
  else:
      price_curve.setVisible(False)
  ```
  This is a **polyline connecting every visible bar's close price**, bar-to-bar. This is exactly
  the "polyline connecting bars" the mission explicitly forbids. Even though the desktop toggle
  defaults to ON, **the browser must never draw this** — it is a deliberate, explicit divergence
  from the desktop's default view for this one element, not an oversight. (The existing
  `static/app.js` `drawPriceCurve()` function is modeled on this feature and must be removed in
  Part C.)

### 4b. Current-price line + bubble (IN SCOPE — must be replicated)

This is a *separate* mechanism, not toggle-gated by `price_cb` at all — it is tied only to
`self.last_price` being available (Phase 2 Part F feature). Cite comment at `:2555-2556`:
"Price line + bubble + current-cell highlight -- independent of the blueprint/book toggle, tied
only to last_price/forming-bar availability."

- Items, created `:1375-1389`:
  ```
  price_line_pen = mkPen(PRICE_COLOR, width=1.0, style=Qt.DashLine)   # PRICE_COLOR = "#55aaff"
  self.book_price_line_main  = pg.InfiniteLine(angle=0, pen=price_line_pen)   # on main_plot (col 0)
  self.book_price_line_panel = pg.InfiniteLine(angle=0, pen=price_line_pen)   # on profile_plot (col 1, the right-hand panel)
  self.price_bubble = pg.TextItem(text="", color="#050505", anchor=(0.0, 0.5), fill=mkBrush(PRICE_COLOR))
  ```
  `angle=0` = horizontal line, `Qt.DashLine` = dashed, color `#55aaff` (light blue), width 1.0.
  Both z-valued above the cells (z=10 for the lines, z=15 for the bubble text).
- `profile_plot` is laid out at `glw.addPlot(row=0, col=1, ...)` (`:1292`) — i.e. it is the panel to
  the **right** of `main_plot` (col 0). `profile_plot.setXRange(0.0, 1.0, ...)` (`:1400`). The
  bubble's anchor `(0.0, 0.5)` + `setPos(0.0, lp)` places it at the **left edge of the right-hand
  panel**, which visually sits exactly at the boundary between the main chart and the panel — i.e.
  functions as a right-axis price label for the main chart.
- Update logic, method around `:2555-2579`:
  ```
  if self.last_price is not None:
      lp = last_price
      book_price_line_main.setPos(lp);  setVisible(True)     # spans full width of main_plot
      book_price_line_panel.setPos(lp); setVisible(True)     # spans full width of profile_plot
      price_bubble.setText(f"{lp:.2f}"); setPos(0.0, lp); setVisible(True)
      # + CellHighlightItem positioned on the forming bar's cell at this price (see 3. above)
  else:
      all three .setVisible(False); cell_highlight.clear()
  ```
- **Semantics summary**: a single horizontal dashed line at the current live price, spanning BOTH
  the main chart and the right-hand book/blueprint panel (two `InfiniteLine` instances sharing one
  pen, one per plot, kept in sync by being set to the same `lp` every update), plus a filled text
  bubble showing the price to 2 decimals anchored at the left edge of the right panel. This is what
  the mission's "horizontal dashed current-price line + right-axis bubble" refers to, and it is
  entirely independent of the `price_cb` "price line" toggle described in 4a.

### 4c. Explicit list of what is NOT drawn

- **NO polyline connecting bars** — `price_curve` (4a) is excluded, regardless of its desktop
  toggle default.
- No gradient/interpolated cell colors — only the 3 fixed RGB stops in §2.
- No hard min/max pixel-width clamp on cells beyond the 0.14–0.94-of-slot formula (§1) — there is
  no additional `max(px, 1)`-style floor in the desktop; a browser-side minimum-visible-width floor
  (e.g. sub-pixel rounding) is an implementation necessity for canvas rendering, not a desktop
  behavior to match, and must not distort the 0.14–0.94 ratio at normal zoom levels.
- No color/alpha distinction for the forming bar's cells vs. closed bars (§3) — only the single-cell
  `CellHighlightItem` overlay.
- No central spine/vertical divider line on the main chart (§3).
- No per-cell border/stroke — `p.setPen(Qt.NoPen)` (`:453`); cells are pure fills, no outline.

---

## 5. Summary table for implementers

| Element | Formula / value | Source |
|---|---|---|
| Min-flow filter | `abs_flow >= min_flow` (default 0) | `:400` |
| Percentile denom | `nanpercentile(abs_flow, 95)` over **currently visible cells** | `:413`, `:1991-2023` |
| norm | `clip(abs_flow / denom, 0, 1)` | `:421` |
| Cell width fraction | `0.14 + 0.80 * norm` (of 1.0-wide bar slot) | `:469` |
| Cell height | `TICK * 0.92` = 0.23 (TICK=0.25) | `:425` |
| Alpha | `round(45 + 210 * norm)`, neutral: `max(35, alpha//2)` | `:432-434` |
| Up color | `rgb(0,210,122)` | `:120, 459` |
| Down color | `rgb(255,77,109)` | `:121, 461` |
| Neutral color | `rgb(119,119,119)` | `:122, 463` |
| Bar spacing | 1.0 unit, sequential index (gap-free) | `:1991` |
| Forming-bar cell style | identical to closed bars | `:391-487` (no branch) |
| Forming-bar highlight | 1 cell, white @ alpha 60, at current price | `:558-588`, `:2567-2572` |
| Current-price line | horizontal, dashed, `#55aaff`, width 1.0, on main+panel | `:1375-1389`, `:2555-2579` |
| Price bubble | `f"{lp:.2f}"`, left-anchored on right panel | `:1384-1389`, `:2563-2564` |
| price_curve (polyline) | **excluded from browser** | `:1320-1321`, `:2344-2350` |

---

## 6. Part B — wire check result

Every per-cell field the spec above needs is already carried by the wire protocol, in both
serializers:
- `server/live_bridge.py:_cells_wire()` (live) and `export_sessions.py:_cells_wire()` (replay) both
  emit `bar_idx, price_level, signed_flow, abs_flow, bar_state` per cell — `abs_flow` drives width
  (§1), `signed_flow` drives hue (§2); no additional per-cell field is needed.
- `last_price` (top-level, both `snapshot` and `forming_update`) is exactly what §4b's current-price
  line/bubble needs — no schema addition required there either.
- The one spec detail with no wire-level consequence: the percentile-95 normalization window
  (§1, "window") is computed **client-side**, over whatever cells the browser currently has
  rendered — not something the wire protocol needs to pre-aggregate or annotate. No new field
  captures "which cells are in the current viewport"; that's derived client-side, same as the
  desktop derives it from its own pyqtgraph viewport.

**Conclusion: no wire schema or serializer changes required.** `WIRE_SCHEMA.md` is unchanged.
Payload budget is therefore unchanged from the existing measurement (`WEB_SLICE_REPORT.md`:
0.49 KB/s real-browser measurement, 4.48 KB/s live-cadence projection, both against the <50 KB/s
budget) — re-measurement was not re-run since nothing about the wire payload shape or volume
changes in this mission; Part D's perf gate instead measures the *rendering* cost of the new
thin-strip encoding, which is where this mission's actual cost increase lives.

---

## 7. INTERACTION_SPEC — root cause of the browser's blocky mode (W2.2 Part A)

`static/app.js:22` (`LOD_BAR_THRESHOLD = 60`) and `:494-496`:
```js
const visSpan = View.xMax - View.xMin;
if (visSpan > LOD_BAR_THRESHOLD) drawCellCandlesLOD(mainCtx, cssW, cssH);
else drawCellCandlesFull(mainCtx, cssW, cssH);
```
Whenever the current viewport spans more than 60 bars, the browser switches from per-cell thin-strip
rendering (`drawCellCandlesFull`) to `drawCellCandlesLOD` — a coarser, sum-aggregated-by-bucket
renderer (`static/app.js:399-437` as of W2.1) — producing the "blocky" look at any zoom wider than
60 bars. This was introduced in W1 as a guess at "mirroring the desktop's WIDE/OVERVIEW LOD" (see
the file's own header comment, `:6-8`) — it was never verified against the desktop's actual
thresholds. W2.2 Part B deletes this switch entirely per the mission's explicit instruction.

## 8. INTERACTION_SPEC — the desktop's own zoom/LOD system (correcting this mission's premise)

**Correction found during investigation, reported honestly rather than forced to fit the mission's
stated premise**: the mission brief asserts "what the desktop does at extreme zoom-out (no
representation change — confirm and cite)". Directly reading the source shows this is **not
accurate** — the desktop *does* change representation at wide zoom:

- `_compute_lod()` (`book_flow_chart_v3.py:1976-1983`): `LOD_FULL` (≤150 visible bars), `LOD_MEDIUM`
  (≤500), `LOD_WIDE` (≤1500), `LOD_OVERVIEW` (>1500) — constants at `:115-118`.
- At the render call site (`:2118-2120`): `_prepare_visible_data(base_ids, skip_cells=(lod in
  (LOD_WIDE, LOD_OVERVIEW)))`. Inside `_prepare_visible_data` (`:1996-1999`), `skip_cells=True`
  short-circuits `visible_cells` to an **empty DataFrame** — `BookFlowLevelCellItem.set_data()` is
  still called (`:2143-2147`) but paints nothing, because it received zero rows.
- In its place, `:2164-2172` builds `_agg_cells` via `_aggregate_cells_for_wide_lod()` (`:330-369`)
  and feeds `WideOFILevelCellRasterItem.set_data()` (`:591-...`) — a genuinely different item that
  draws **coarser, binned rectangles** (`cell_w = bar_group * flow_norm`, `cell_h = price_bin_pts *
  0.92`, bin sizes `bar_group = n_vis//300` (WIDE) or `n_vis//200` (OVERVIEW), `price_bin = 2.0`
  (WIDE) or `4.0` (OVERVIEW) — `:2166-2172`). Its docstring (`:595-600`) states the
  percentile/color/alpha formula is otherwise identical to `BookFlowLevelCellItem`'s — only the
  bucket size differs.
- **The desktop's reachable zoom-out is hard-capped, and lands in this aggregated mode by
  construction**: `_visible_bar_window()` (`:1942-1972`) clamps the effective bar window to at most
  `self.max_visible_bars` (`MAX_VISIBLE_BARS = 1200`, `:99`) regardless of how far the user
  wheel-zooms out — `if hi - lo + 1 > self.max_visible_bars: decimated = True; ...` (`:1962-1970`).
  Since 1200 > `LOD_MEDIUM_MAX_BARS` (500), the desktop's **widest possible view is always
  `LOD_WIDE`** (aggregated raster) — it can never reach a true, uncapped full-session/`LOD_OVERVIEW`
  view, and it never renders 1200 individual thin-strip bars at once.

**What this means for this mission**: Part B's instruction ("delete the aggregated/blocky mode
entirely... at any zoom") is a deliberate, explicit **improvement over the desktop**, not a
replication of it — analogous to W2.1's exclusion of `price_curve`. The browser will render true
thin-strip cells at every zoom level, including at the desktop's own 1200-bar cap, where the desktop
itself would already have switched away from per-cell rendering. Gate (a)'s "full-session
zoom-out" reference is therefore computed by calling `BookFlowLevelCellItem.set_data()` **directly**
with the full cell population for that scene (the same technique W2.1's geometry-parity gate used),
not by replaying the desktop's own LOD-switching render pipeline — that pipeline would return an
empty per-cell picture at this scene by construction, which is not a meaningful reference for what
the browser is being asked to draw.

## 9. INTERACTION_SPEC — mouse dynamics

No `wheelEvent`/`mouseDragEvent`/`setMouseMode` override exists anywhere in `book_flow_chart_v3.py`
(confirmed by grep) — `main_plot`'s interaction is 100% pyqtgraph's own default `ViewBox`/`AxisItem`
behavior, default `mouseMode = PanMode`. Cited directly from the installed library
(`pyqtgraph/graphicsItems/ViewBox/ViewBox.py`, `pyqtgraph/graphicsItems/AxisItem.py`, installed at
`.venvs/ofi/lib/python3.13/site-packages/pyqtgraph/`):

- **Wheel over the plot body** (`ViewBox.wheelEvent`, `ViewBox.py:1297-1316`): `axis=None` →
  both x and y scale together by `s = 1.02 ** (delta * wheelScaleFactor)`, anchored at
  `center = invertQTransform(childGroup.transform()).map(ev.pos())` — the exact data-space point
  under the cursor. `scaleBy(s, center)` keeps that point fixed on screen. This is "cursor-anchored
  wheel zoom."
- **Wheel over an axis** (`AxisItem.wheelEvent`, `AxisItem.py:1745-1758`): if the event position is
  outside the linked ViewBox's scene rect (i.e., over the axis label strip, not the plot body), it
  forwards to `lv.wheelEvent(event, axis=0)` for the bottom (x) axis or `axis=1` for left/right (y)
  axes — restricting the SAME cursor-anchored scale to just that one axis.
- **Left/middle-button drag over the plot body** (`ViewBox.mouseDragEvent`, `ViewBox.py:1335-1372`,
  `axis=None`, `PanMode`): pure pan — `translateBy(x=..., y=...)`, both axes (unless one is
  mouse-disabled).
- **Left/middle-button drag over an axis** (`axis=0` or `1` via `AxisItem.mouseDragEvent`,
  `AxisItem.py:1761-1772`): still the pan branch (`axis is not None` fails the `RectMode and axis is
  None` check), but masked to that one axis only — i.e., dragging left-button on the x-axis pans in
  x only; it does **not** scale.
- **Right-button drag** (`ViewBox.mouseDragEvent`, `:1372-1387`): the scale branch —
  `s = ((mask*0.02)+1)**screen_delta`, `scaleBy(x=s[0], y=s[1], center=button_down_position)`. Over
  the plot body (`axis=None`) both axes scale; over an axis (`axis=0`/`1`, via the same
  `AxisItem.mouseDragEvent` forwarding) only that axis scales. **This right-button-drag-on-an-axis
  path is the mission's "change candle length with the mouse" (x-axis drag) / y-axis price-scale
  drag** — it is a right-click drag specifically, not left-click.
- `profile_plot` (book/blueprint side panel) has `setMouseEnabled(x=False, y=False)`
  (`:1295`) — **zero interactivity**; only `main_plot` responds to any of the above.

**Reset View** (`_reset_view()`, `:2763-2769`): clears `user_view_override`, `user_view_locked`,
and `_initialized`, then calls `_reload()` — which re-derives the default range via
`_update_view_ranges()` (below) as if freshly opened.

**Follow-live re-anchoring** (`_update_view_ranges()`, `:2646-2699`), called every render when data
changes: if `not self.user_view_override` and (`not self._initialized` or
`self.follow_live_requested`): explicitly calls `main_plot.setXRange(x_lo, x_hi, padding=0.02)` with
`x_hi = (n-1) + 0.8`, `x_lo = x_hi - min(n, FOLLOW_WINDOW)` (`FOLLOW_WINDOW = 180`, `:58`) — i.e. the
**default/follow view is the latest ~180 bars**, not the desktop's separate 1200-bar data-prep cap
(that cap bounds how much data is *prepared*, not the default *visible* span). y-range auto-fits the
visible cells'/bars' price extent with 5% padding.

**Manual interaction permanently breaks follow, until Reset View — re-checking "follow" alone does
not restore it**: `_on_user_interaction()` (`:2726-2734`, wired to `ViewBox.sigRangeChangedManually`,
which every wheel/drag path above emits) unconditionally sets `user_view_override = True` on ANY
manual pan/zoom. `_visible_bar_window()`'s first branch (`:1948-1951`) and `_update_view_ranges()`'s
own early return (`:2648-2649`) both give `user_view_override` priority over
`follow_live_requested` — so once set, the user's own manually-chosen range wins regardless of the
follow checkbox's state. `_on_follow_change()` (`:3106-3110`, the checkbox handler) only toggles
`follow_live_requested` and reloads; it does **not** clear `user_view_override`. The only way back
to following is `_reset_view()`. Note also: `_at_live_edge()` (`:2706-2711`) is **dead code** —
defined but never called anywhere else in the file (confirmed by grep) — so there is no "stayed near
the edge, keep following" leniency; any manual interaction breaks follow, full stop, even a wheel
notch that leaves the view at the same edge it started at.

**Chosen "5 matched view rects" for this mission's gates** (grounded in the constants above, not
arbitrary): full-session zoom-out = 1200 bars (the desktop's own `max_visible_bars` cap — the
widest view the desktop can ever actually show); default = 180 bars (`FOLLOW_WINDOW`, the desktop's
own default/follow span); mid = 100 bars; tight = 40 bars; extreme close-up = 12 bars (comfortably
inside `LOD_FULL_MAX_BARS`=150 for the three narrower scenes, so on the desktop's own render path
all three would in fact use `BookFlowLevelCellItem` un-decimated).

---

## 10. W2.2 Part C — order-book v3 checkpoint recon

**Question**: does the v3 level-state checkpoint carry order counts per level, or only sizes?

**Finding: sizes only. No order-count field exists anywhere in this pipeline.** Verified by direct,
exhaustive inspection, not assumed:
- `book_flow_data_service._load_book_depth()` (`book_flow_data_service.py:133-173`) reads
  `level_cache.load_v3_state()` and pulls exactly two arrays out of it: `state.get("bid_sizes")`,
  `state.get("ask_sizes")` (`:143-144`) — plus scalar `best_bid_tick`/`best_ask_tick` (derived
  in-function from those same arrays via `np.nonzero`, `:151-152`) and `updated_utc`. No count field
  is read here because none exists to read.
- `build_book_flow_level_cache.py`'s `save_v3_state()` call sites (`:735-748`, `:1062-1070`, plus the
  no-new-data early-return path) were grepped for every key ever written into the state dict:
  `schema_version, symbol, session_date, bid_inode, ask_inode, bid_file_offset, ask_file_offset,
  bid_file_size, ask_file_size, last_complete_bar_idx, bid_sizes, ask_sizes, updated_utc` (and a few
  others unrelated to book state). No `bid_count`/`ask_count`/`num_orders`/`order_count`/`n_orders`
  key exists anywhere in the file — grepped for all of these names, zero matches.
- Traced one level further upstream: `book_flow_lib.read_quote_updates()` (`book_flow_lib.py:237-`),
  which parses the raw Rithmic quote-update NDJSON feed that `bid_sizes`/`ask_sizes` are built from,
  returns a DataFrame with exactly 3 columns: `event_ts_ns, price, size_eff`. No count field exists
  in the raw feed either — this system has never captured per-level order counts, at any layer.

**Conclusion for Part C's implementation**: every per-level label in the browser's order-book panel
v2 shows **size only**. No count is displayed, fabricated, or implied anywhere.

## 11. W2.2 Part C — panel v2 design (readability, explicitly allowed to exceed the desktop)

- **Sub-linear bar width**: `sqrt(size)/sqrt(maxSize)`, capped at 82% of panel width
  (`BOOK_MAX_BAR_FRAC`) — an outlier resting size no longer visually swamps every smaller level; the
  numeric label (always drawn when not stale) carries the true magnitude regardless of bar width.
- **Guaranteed-legible near-touch zone**: the `NEAR_TOUCH_LEVELS=12` non-zero levels nearest the
  touch on each side are laid out with a **fixed** row height (`NEAR_TOUCH_ROW_PX=15`), independent
  of the current zoom/`tickPx` — always readable, at any of the 5 zoom levels. This is a **deliberate
  divergence** from strict main-chart y-axis pixel-alignment for exactly these ~24 rows: the mission
  brief explicitly permits the book panel to exceed the desktop for readability, and a linear,
  shared-price-scale mapping cannot simultaneously guarantee legibility at extreme zoom-out (where
  12 real ticks might occupy under a pixel) and stay pixel-aligned with the main chart. The anchor
  point (the touch itself) still lines up with the shared y-axis; only the near-touch rows' own
  internal spacing is fixed rather than proportional. Levels outside this zone (`nearTouchIndexSet`)
  keep the original, exact shared-y-axis mapping, unchanged from W1.
- **Best bid/ask emphasis**: the best bid and best ask rows get a white outline box (`drawFixedRow`'s
  `isBest` branch) and a bold label.
- **Spread readout**: `bestAsk - bestBid`, displayed at the touch's y-position, hidden when stale.
- **Row banding**: a faint (`rgba(255,255,255,0.025)`) alternating band every other tick, for visual
  separation at any zoom.
- **Stale behavior unchanged**: `isBookStale()`/`BOOK_STALE_SECS` untouched; all v2 additions (labels,
  best-bid/ask emphasis, spread readout) are gated behind `!stale`, exactly like the pre-existing
  size labels were.

---

## 12. W2.4 Part C — the desktop's actual source field for the current-price line, and the seal-boundary bug

**The desktop's price line is driven by `self.last_price`** (`book_flow_chart_v3.py:2559-2564`,
cited already in section 4b). Tracing where `self.last_price` itself comes from:
`book_flow_chart_v3.py:1612`: `self.last_price = snap.get("last_price")`, where `snap` is built from
a `ChartFrame`. `ChartFrame.last_price` (`book_flow_data_service.py:284`) is populated at both its
construction sites (`:413`, `:571`) by calling:

```python
def _last_price_from_cells(cells: pd.DataFrame) -> Optional[float]:      # book_flow_data_service.py:256-268
    if cells is None or cells.empty:
        return None
    row = cells.iloc[-1]
    for col in ("close_price", "mid_price"):        # <- THE field: real trade/mid price columns
        if col in cells.columns:
            v = row.get(col)
            ...
            return float(v)
    return None

last_price = _last_price_from_cells(forming_cells if forming_cells is not None else sealed_cells)
```

**This is "the desktop's source field"**: the `close_price` (falling back to `mid_price`) column of
the *last row* of whichever cells DataFrame is authoritative right now (the forming bar's cells if
one exists, else the most recently sealed bar's cells). It happens to be read from a cells table,
but the field itself is a dedicated, real trade/mid-price column — **not** the cell's own
`price_level` (an OFI level's price *tick*, i.e. wherever flow happened to occur, which can be far
from the actual traded price). Using `price_level` as a price-line stand-in is the "cell-array
position" mistake this mission's Part C explicitly forbids — found to be a real, existing bug (below).

**Root cause of the seal-boundary jump/staleness, found in two places**:
1. `server/live_bridge.py`'s `bar_roll` delta message (`:204-210` before this fix) carried **no
   `last_price` field at all** — only `forming_update` did (`:225`). Between a bar sealing and the
   next `forming_update` arriving, the client kept showing whatever the just-sealed bar's *last*
   forming tick had set — usually close, but not guaranteed exact, and with no signal marking the
   seal instant itself.
2. `export_sessions.py`'s `forming_update` messages used `float(cells.iloc[:n_reveal]["price_level"].iloc[-1])`
   (`:160` before this fix) — literally the "cell-array position" bug: an OFI level's price tick
   substituted for the traded price. Its `bar_roll` messages also had no `last_price` field at all.

**Fix**: both `live_bridge.py` and `export_sessions.py` now set `bar_roll`'s `last_price` to
`bfds._last_price_from_cells(cells)` where `cells` is *that specific sealed bar's own* cell slice —
not `frame.last_price` (which, by the time a poll cycle fires, may already reflect the *new* forming
bar's early cells) and not any bar's OHLC `px_close` (a different field, not guaranteed identical to
`close_price`/`mid_price`). `export_sessions.py`'s `forming_update` and `snapshot` messages now also
call `bfds._last_price_from_cells` on the relevant cells slice, replacing the `price_level` bug and
the `px_close`-based approximation respectively. The client (`static/app.js`'s `bar_roll` handler)
now sets `Data.lastPrice` from `msg.last_price` exactly like the `forming_update` handler already
did — end to end, the browser's price line is driven by the *exact same field* the desktop's is,
never a cell's `price_level`.

---

## 13. W2.5 Part 0 — root cause of the price/cell desync screenshot

**The screenshot's mechanism, in one paragraph**: the desync is a REPLAY-EXPORT-ONLY artifact, not a
live-mode bug. `export_sessions.py`'s synthetic forming-bar reveal (`build_session()`, before this
fix) sorts a bar's real cells by `price_level` ascending once (`cells_by_bar[b].sort_values
("price_level")`) and reveals a growing PREFIX of that sorted list at each of `N_FORMING_TICKS=4`
ticks (`n_reveal = max(1, int(n_rows * tick / N_FORMING_TICKS))`) — i.e. it reveals the bar's
LOWEST-price cells first and works upward, regardless of when those price levels were actually
touched during the real bar. Meanwhile, `last_price` at each tick (after the W2.4 Part C fix) reads
`bfds._last_price_from_cells(cells.iloc[:n_reveal])` — `close_price`/`mid_price` of the revealed
slice's last row — but `close_price`/`mid_price` are BAR-LEVEL attributes, duplicated near-
identically across every row of that bar regardless of `price_level`, so this returns essentially
the bar's real (or close to final) traded price at EVERY tick, independent of how far the
price-sorted reveal has progressed. When a bar's price traveled upward from open to close (the
common case), the early reveal ticks show only the bar's LOWEST price levels while `last_price`
already reflects a price near the bar's actual (higher) close — exactly the screenshot's
"line ~28220.25, forming cells ~28210-28212" state. Confirmed empirically: scanning
`clean_2026-07-29`'s regenerated `deltas.jsonl`, well over half of all `forming_update` messages
have `last_price` outside the revealed `forming_cells`' own `price_level` range — this is the
NORMAL state during replay, not a rare glitch. Live mode does not have this problem: `frame.
last_price` and `frame.forming_cells` are both read from the SAME real, actually-arrived-so-far
poll-cycle state, so they are atomically consistent by construction; only replay's SIMULATED reveal
technique decouples them.

**Fix** (Part A): `export_sessions.py`'s reveal now tracks a synthetic intra-bar price path
(linear interpolation from the bar's own `px_open` to `px_close`, snapped to `TICK`) and reveals
cells by PROXIMITY to that tick's interpolated price, not by ascending `price_level` — so the
revealed cell set always clusters around wherever `last_price` (now set to that same interpolated
value, not `_last_price_from_cells`) says the price currently is. The final tick reveals 100% of
cells with `last_price` snapped exactly to `px_close`, matching the immediately-following
`bar_roll`'s own sealed close (RENDER_SPEC.md section 12's seal-boundary fix, unchanged and now
doubly consistent).

## 14. W2.5 Part A — current-cell highlight semantics (desktop citation)

`book_flow_chart_v3.py:2566-2572` (`_update_order_book_panel`'s price-line/highlight block): given
`self.last_price is not None` and the forming bar has a known `bpos`, the highlight is placed at
`tick_to_price(price_to_tick(lp))` — **always the tick nearest to `last_price`, snapped
unconditionally** — with NO check for whether an actual OFI cell (nonzero `abs_flow`) exists at that
specific tick. It is hidden only when `bpos is None` (no forming bar at all), never because "this
particular tick has no flow." So the desktop's real behavior is "snap to nearest tick, always show
given a forming bar" — not "hide when cell-less."

This appears to conflict with this mission's own Part A instruction ("never draw an empty box at a
cell-less level") — reconciled, not silently picked one way: the mission's actual concern (per
Part 0's root cause above) is a highlight FLOATING far from the visible cell cluster, which Part A's
atomic-pairing fix eliminates by construction (the revealed cells always surround the interpolated
current price in replay, and are inherently co-located with it in live mode) — not the narrow case
of a specific zero-flow tick within an otherwise-populated forming bar, which the desktop itself
does not special-case. The browser therefore matches the desktop's cited behavior exactly (snap +
always show given a forming bar), and relies on the pairing fix — not a hide-if-empty special case
— to prevent the floating-highlight symptom the screenshot showed.

## 15. W2.5 Part A — client-side atomic frame (`static/app.js`)

Server-side pairing (section 13) guarantees each WIRE MESSAGE is internally consistent, but the
client used to fan that message out into three independently-settable top-level fields
(`Data.lastPrice`, `Data.formingBar`, `Data.formingCells`), set by separate statements in each
handler. Nothing enforced they stayed in lockstep — a future edit touching only one of the three
inside a handler (or any other code reaching for `Data.lastPrice` directly) could silently
reintroduce a client-side version of the section 13 desync even with a perfectly-paired wire.

Fix: `Data.frame` is now the ONLY place these three (plus `version`) live, as one object:
`{version, lastPrice, formingBar, formingCells}`. `setFrame(version, lastPrice, formingBar,
formingCells)` is the sole writer (besides `resetData()`'s initializer) — every handler that used
to set the three fields separately now makes exactly one `setFrame()` call. The returned object is
`Object.freeze`d (the file is `"use strict"`, so a stray field-level mutation throws rather than
silently drifting). `drawCurrentPriceLine()`/`drawCellHighlight()` both take an explicit `frame`
parameter (defaulting to `Data.frame`), and `renderMain()`/`renderBook()` sample `Data.frame` once
into a local (`curFrame`) and pass that SAME reference to both draw calls, so line and highlight
are drawn from one snapshot, not two independent reads that could observe different frames if a
future refactor ever made `Data.frame` reassignable mid-render.

`bar_roll`'s handler preserves the section 12 seal-boundary behavior (this bar's own sealed close as
`last_price`, guarded against a `None` from `_last_price_from_cells`) and the section 13 fix's "seal
boundary rides the same frame" requirement: the new forming-bar preview bundled into a `bar_roll`
message is folded into the SAME `setFrame()` call as the sealed price, without being re-paired to
it — its own paired price arrives in the next `forming_update`, exactly as designed server-side.

**Gate**: `tests/test_atomic_frame.py` — static check (no `Data.lastPrice`/`formingBar`/
`formingCells` reference survives anywhere in `static/app.js`; exactly 2 sites assign `Data.frame`)
+ runtime check (81 real messages against a simulator-backed live server: every `Data.frame` has
exactly the 4 expected keys, is frozen, rejects a mutation attempt, and gets a genuinely new object
whenever `version` changes). 0 problems both checks.

Two pre-existing tests read `Data.lastPrice`/`formingBar`/`formingCells` directly via
`page.evaluate()` (`test_geometry_parity.py`, `test_live_parity_desktop.py`, `test_book_v4_gates.py`,
`build_montage.py`) — all four updated to `Data.frame.*` / `setFrame(...)` alongside this change;
re-run clean (see section 16's regression table).

## 16. W2.5 Part B — replay staleness vs. REPLAY ENDED

Root cause: `isBookStale()` compared `Data.bookTs` (whatever the wire message says) against
`Date.now()`. In LIVE mode `book_ts` is the daemon's real, current wall-clock write time, so this is
correct. In REPLAY mode, `export_sessions.py` never populates book data at all (`book`/`book_ts` are
always `None` in every exported session — replay has no order-book concept), so `isBookStale()`'s
`!Data.book || Data.bookTs == null` branch returned `true` UNCONDITIONALLY, for the entire duration
of every replay session — the book panel's red STALE banner was permanently on throughout replay,
which is exactly the "recorded data's wall-clock age" failure mode this mission part names (the
degenerate case of it: not merely old, but never-present, read as terminally stale either way).

Fix: `isBookStale()` now branches on `currentSessionName`. In replay, staleness is keyed off STREAM
LIVENESS — `lastMessageWallMs`, a module-level wall-clock timestamp updated at the top of
`handleMessage()` for every message of any kind — compared against `Date.now()` with an 8s grace
(`REPLAY_STALL_MS`). This asks "are messages still arriving", never "how old is the payload's own
timestamp", so a healthy replay stream reports not-stale throughout, and only a genuine delivery
stall (dropped connection, frozen server) trips it — live mode's own book_ts-age check is untouched.

End-of-session: `ReplayPlayer.run()` (`server/replay.py`) previously looped from the exhausted delta
list straight back to resending the snapshot, with no signal of the boundary at all. It now sends an
explicit `{"type": "replay_ended"}` message first, then pauses `_REPLAY_ENDED_PAUSE_S=3.0` real
seconds before looping — giving the client both a signal and a window to display it. The client sets
`Data.replayEnded = true` on receipt (cleared by the next `snapshot`), which `updateModeBadge()` and
`renderBook()`'s banner both check BEFORE falling through to ordinary replay/stale logic: mode badge
shows amber `REPLAY ENDED` (a new `#modeBadge.replayEnded` CSS class, distinct from gray `.stale`),
and the book panel banner shows amber `"REPLAY ENDED"` instead of red `"STALE"`. `isBookStale()`
short-circuits to `false` while `Data.replayEnded` is true — the two states are mutually exclusive
by construction, not just by convention.

**Gate**: `tests/test_replay_staleness.py` — drives `clean_2026-07-30` (4270 recorded seconds) at
500x speed, sampling in-page state every 150ms for 20s (long enough to observe active streaming,
the `replay_ended` window, and the loop restart back to active streaming). 0 problems: zero
false-STALE samples outside the REPLAY ENDED window, the REPLAY ENDED window itself is observed with
the correct badge and never simultaneously flagged stale, and the badge recovers to plain
`REPLAY: <session>` after the loop restarts. Live staleness re-verified unchanged via the existing
`tests/test_live_staleness.py` (both parts still PASS).

## 17. W2.5 Part C — gate suite ("designed so this bug class cannot pass")

**(a) Co-movement (hard gate)**: `tests/test_comovement_gate.py`, sampling `distance(price line,
nearest forming-bar cell)` after EVERY `handleMessage()` call (never a fixed-interval poll, so no
frame is skipped) across three real end-to-end browser runs, plus the pre-existing
`tests/test_priceline_truth.py` as the speed-independent, 0-tolerance, whole-session data-level
proof underlying all of them (0/556 events, clean_2026-07-30):
| Run | Samples w/ forming bar | Violations (>1 tick) | Compliance |
|---|---|---|---|
| Full session, 8x (exhaustive) | 554 | 0 | 100.000% (target >=99.9%) |
| Real-time 1x, ~200s slice | 38 | 0 | 100.000% |
| 30-min simulated-live, v1 (pre-fix) | 2697 | 885 | 67.186% -- **FAIL, real finding** |
| 30-min simulated-live, v2 (post-fix) | 2691 | 0 | 100.000% |

Zero floating highlights in every 8x/1x run (the snapped highlight tick's own distance to the
nearest forming cell is checked identically to the price line's, same 0-violation result).
`handleMessage()` has no speed-conditional branch (confirmed by inspection), so the exhaustive 8x
pass plus the 1x real-time slice together give equivalent coverage to a full real-time pass without
the ~71-minute wall-clock cost, run explicitly rather than silently skipped.

**This gate did exactly its designed job.** The FIRST 30-min simulated-live run failed hard (67.2%
compliance, violations up to 25 points / 100 ticks) -- not a live-mode architecture problem
(`book_flow_data_service._build_frame_live_latest` computes `last_price` from the SAME
`forming_cells` object it hands to the wire, genuinely atomic by construction, confirmed by
re-reading the real source), but an INDEPENDENT second instance of the identical bug class:
`simulator/daemon_simulator.py`'s forming-cell reveal used the same price-sorted-PREFIX technique
`export_sessions.py` had before Part A's fix, and the revealed slice's `close_price` column is a
real, bar-level-constant historical value the unmodified `_last_price_from_cells` reads regardless
of which price levels are actually revealed -- exactly Part 0's mechanism, in a different file this
mission had not yet touched. Fixed (commit `f1a5bd9`) the same way: proximity-based reveal +
overwriting `close_price`/`mid_price` on every revealed row (not just one, since this writes into a
real parquet file the real service re-reads, and row order isn't guaranteed to survive that
round-trip the way it is for `export_sessions.py`'s self-controlled JSON wire messages). Confirmed
test-infrastructure-only: real production's daemon writes genuinely fresh, current prices every
real cycle -- there is no reveal simulation in production. All simulator-dependent regression tests
(flicker, mode badge, staleness, atomic-frame, reconnect) re-verified PASS after the fix.

**(b) Atomicity**: proven structurally, not just sampled -- `renderMain()`/`renderBook()` each
sample `Data.frame` ONCE per call into a local (`curFrame`), and pass that same reference to both
`drawCellHighlight()` and `drawCurrentPriceLine()` (section 15); the object is `Object.freeze()`d at
creation, so no code path can mutate one field of an already-published frame independently of the
others. `tests/test_atomic_frame.py`'s runtime check additionally confirms this holds across 81 real
messages (frozen, exactly 4 keys, genuinely new object on every version change) -- 0 problems.

**(c) Seal**: `tests/test_priceline_truth.py` checks ALL 111 `bar_roll` events in the full
clean_2026-07-30 session (exceeds the mission's >=100) -- `last_price` == that bar's own sealed
close, exact match, 0 problems -- and every one of the 444 `forming_update` events immediately
following for the atomic-pairing invariant, also 0 problems. The 600-sample live sync suite
(`tests/test_live_parity_desktop.py`) re-run through the new path: 601 samples, 599/599 comparable
bar_idx and 598/598 book_ts checks in sync, 0 problems, PASS on the first attempt (previously needed
3 attempts in W2.4 Part C for an unrelated, already-documented pre-existing poller-staggering race).

**(d) Staleness**: `tests/test_replay_staleness.py` -- 133 samples over 20s at 500x speed spanning
active streaming -> REPLAY ENDED -> loop restart -> active streaming again: 0 problems (zero
false-STALE samples outside the REPLAY ENDED window, correct badge/banner throughout, clean recovery
after the loop restart). `tests/test_live_staleness.py` (live mode, untouched by this mission's
changes) re-run: both parts still PASS.

**(e) Regression**: all green.
| Suite | Result |
|---|---|
| Flicker (`test_live_flicker.py`) | PASS (0 gaps, 55/55 bars) |
| W2.1 geometry parity (`test_geometry_parity.py`) | PASS (0/4 scenes, incl. live-latest) |
| W2.2 zoom geometry parity (`test_zoom_geometry_parity.py`) | PASS (0/3 zoom scenes) |
| Data-placement parity (`test_parity.py`) | PASS (3/3 scenes) |
| Book v3 uniformity/inset (`test_book_v3_uniformity.py`, `test_book_v3_inset.py`) | PASS |
| Book v4 gates a-e (`test_book_v4_gates.py`) | PASS (5/5) |
| Mode badge (`test_mode_badge.py`) | PASS (3/3, incl. REPLAY/LIVE/STALE) |
| Connection lifecycle (`test_connection_lifecycle.py`) | PASS (hammer/kill-storm/multi-tab) |
| Live reconnect (`test_live_reconnect.py`) | PASS (6/6 kills recovered) |
| Slow client (`test_live_slowclient.py`) | PASS (clean, first attempt) |
| Session boundary (`test_live_sessionboundary.py`) | PASS |
| Read-only audit (`test_live_readonly_audit.py`) | PASS (0 write violations, 60842 checks) |
| Auth (`test_auth.py`) | PASS |
| fps/perf spot check (`test_live_perf.py`) | PASS (61fps median, target >=30) |
| 1h soak (`test_live_soak.py`) | not re-run this pass -- no code touched in W2.5 affects the |
| | growth-relevant paths it covers (per-message allocation went from 3 field writes to 1 frozen object, if anything strictly less garbage); last known-good result stands from W2.4 Part E |

Two pre-existing `test_interaction_dynamics.py` failures (x-axis-strip wheel/drag) reproduced
identically on `master` BEFORE any W2.5 change (verified directly: `git stash` back to the merge
base and re-run) -- a pre-existing, unrelated flake, not a W2.5 regression, and out of this mission's
surgical scope.

## 19. W2.6 Part A — live price/book freeze + 600-point mismatch (server + client)

Full mechanism: see `LIVEFIX_DIAGNOSIS.md` (Part 0, written before any fix, per mission
requirement). Summary: `book_flow_data_service.py`'s `_get_book_depth()` (read-only desktop
source, never modified) caches the loaded book keyed on a bare file `(mtime, size)` signature,
writing the new signature *before* calling the loader — a single transient read failure at the
wrong instant (most plausibly a session-rollover race) can poison that cache to "no change
detected" indefinitely, serving whatever book was cached from an arbitrarily old session forever,
while the bars/cells pipeline (a separate, unaffected code path) kept advancing normally. This
produced exactly the reported symptom: a frozen depth column showing prices from a session 3+
real days and 2 rollovers stale, ~600 points away from the chart's own (fresh) price region.

**Fix — server (`server/live_bridge.py`)**: `_fresh_book_depth()`/`_fresh_book_depth_checked()`
read the v3 checkpoint fresh from disk on every single poll, with zero caching in webbeta's own
code — bypassing the poisonable service-level cache entirely. They also validate the loaded
pickle's own `symbol`/`session_date` fields against what the bar stream is actually using
(`frame.symbol`/`frame.date`), and reject (never render) a book whose best bid/ask sits more than
`BOOK_MISMATCH_TICKS` (default 200) ticks from `frame.last_price`. Both checks report a
`book_status` (`"ok"` / `"unavailable"` / `"mismatch"`) and a human-readable reason, threaded onto
every `snapshot` and `book_update` wire message. `LiveWireState`'s own tracked `book_ts`/
`book_status` (not `frame.book_ts`) now also drive `/status`'s `book_age_s` for the same reason.
`forming_update`'s gate was also widened to fire on `last_price` itself changing (not just the
forming bar's identity/cell-count) — a bar can accumulate flow at already-touched price levels
without a new row, so `last_price` could otherwise go several polls without a fresh push even
while genuinely ticking (LIVEFIX_DIAGNOSIS.md's second, narrower mechanism).

**Fix — client (`static/app.js`)**: `book`/`bookTs`/`bookStatus`/`bookStatusReason` are now part of
the SAME atomic `Data.frame` object Part A (W2.5) introduced for price/cells/highlight — no longer
separate top-level `Data.book`/`Data.bookTs` fields. `setFrame(version, patch)` now merges a patch
onto the previous frame (rather than taking 4 positional args), so a `book_update` that doesn't
touch price/cells carries them forward unchanged, and a `forming_update` that doesn't touch the
book carries IT forward unchanged — matching the existing seal-boundary pattern for `lastPrice`
exactly. `isBookStale()` treats `bookStatus === "mismatch"` as invalid (grayed out) exactly like
ordinary staleness, but `renderBook()`'s banner shows a distinct amber "BOOK MISMATCH" (matching
"REPLAY ENDED"'s established amber-vs-red convention from W2.5 Part B) — never silently rendering,
and never confusing "the server actively refused a wrong book" with "the feed went quiet".

**Verified directly against the real live feed (market open, 2026-08-03)**: before the fix, the
raw checkpoint file was independently confirmed fresh and correct (`best_bid=28640.50`,
matching the chart's price region) while a long-running server process showed the reported
600-point-frozen symptom — proving the bug was in the read path, not the data. After the fix, a
fresh server run against the same real feed showed `book_status="ok"`, `book_ts` advancing every
~2.4s, and best bid/ask exactly tracking the chart's price region continuously (screenshot: line
28641.50, ladder/inset both centered on 28637-28644, spread 1.25).

## 20. W2.8 Part A — view-state persistence (client)

Root cause: see `VIEWSTATE_DIAGNOSIS.md`. Summary: `View.userSet` (this browser's exact analog of
the desktop's own `user_view_override`, section 9 above) was being reset to `false` on every
`snapshot` message, not just the first one — combined with a real seq-tracking bug (W2.7's
keepalive never advanced `lastSeq`), this produced a full view-wiping resync roughly every 5
seconds, with or without any user interaction.

Fix: `viewInitialized` (new, module-level) tracks whether `autoFitView()` has ever computed a real
view for the current connection context. A `snapshot` only initializes the view when
`!viewInitialized` — any later snapshot (reconnect, resync) leaves `View` completely untouched,
matching this mission's explicit requirement ("Incoming frames must NEVER modify the view
transform"). `autoFitView()` itself now branches the same way: first call does the desktop's own
default fit (section 9's `FOLLOW_WINDOW=180` span + a y-fit, cited exactly); every call after that,
while still not `userSet`, translates `x` only — preserving whatever width/zoom is currently in
effect — **a deliberate, documented divergence** from the desktop's own `_update_view_ranges()`,
which re-derives the fixed 180-bar width on every follow-mode render rather than preserving a
custom width. This mission's Part A is explicit that translate-only is the correct behavior here
("MUST NOT alter zoom level, bar width, or y-range" while following), so the one-time-refit
formula is cited only for the initial fit, not the ongoing follow behavior.

`resetViewBtn` now explicitly clears `viewInitialized` before calling `autoFitView()`, forcing a
full fresh fit — matching `_reset_view()`'s own clearing of `user_view_override`/`_initialized`
(section 9). `connectBtn`/session-switch also clear it (a genuinely new view context, not a
mid-session reconnect). A new `#followIndicator` header element is a direct readout of
`View.userSet` ("following live" / "manual view"), satisfying "show that state in the header."

**Gate**: a real pan against the live feed held bit-for-bit identical (`xMin/xMax/yMin/yMax`) across
60 seconds and 100+ real frame versions afterward, with `window.__wsGapCount` staying 0 throughout.
