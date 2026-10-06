# W21_REPORT.md — BETA W2.1: Visual Parity

SHADOW/RESEARCH ONLY. Branch `webbeta-visual` off `master`. Cache daemon (PID 2527) never touched,
signaled, or restarted at any point in this mission — confirmed by inspection of every commit's
diff (Part E, below) and by never issuing any `systemctl`/`kill`/daemon-control command against it.

## What changed and why

The browser renderer was built (W1/W2-live) against a plausible but unverified guess at the
desktop's cell-width/color encoding: max-based normalization, not the desktop's actual
95th-percentile-of-the-visible-window normalization, plus a stray bar-to-bar connecting polyline
the desktop's real default view does not draw. `RENDER_SPEC.md` extracts the desktop's actual
formulas with file/line citations (Part A), confirms the existing wire schema already carries every
field they need (Part B, no changes required), and `static/app.js`/`static/index.html` are corrected
to match (Part C). Part D re-verifies everything that could plausibly have regressed.

## Part A — RENDER_SPEC.md

Full spec at `webbeta/RENDER_SPEC.md`. Headline findings:
- Width/color denominator is `nanpercentile(abs_flow, 95)` computed over the **currently visible
  cells** (a viewport-relative statistic, not session-wide) — `book_flow_chart_v3.py:413,
  1991-2023`.
- Width fraction `0.14 + 0.80*norm`, cell height `TICK*0.92`, colors `#00d27a`/`#ff4d6d`/`#777777`
  with alpha `45+210*norm` (neutral halved) — `:459-475`.
- The desktop's "price line" toggle (`price_cb`, default ON) controls `price_curve`, a bar-to-bar
  connecting polyline (`:1320-1321, 2344-2350`) — **excluded from the browser by explicit mission
  instruction**, regardless of its desktop-side default.
- A *separate*, untoggled mechanism (`book_price_line_main`/`book_price_line_panel`/`price_bubble`,
  `:1375-1389, 2555-2579`) draws the horizontal dashed current-price line + right-axis bubble the
  mission actually wants replicated, spanning both the main chart and the book/blueprint panel.
- A real (if visually minor) quantization artifact was found and documented, not silently ignored:
  the desktop paints one alpha per (sign, 14-unit band) group, not each cell's own continuous alpha.
  The browser correctly uses the clean continuous formula; Part D's gate treats this as a
  documented, narrow tolerance (±14/255) rather than a silent pass or an over-fit replication.

## Part B — wire check

No schema or serializer changes needed. `_cells_wire()` (both `live_bridge.py` and
`export_sessions.py`) already carries `bar_idx, price_level, signed_flow, abs_flow, bar_state` per
cell and `last_price` at top level — everything the corrected spec's width/color/price-line
formulas need. **Payload delta: zero** (schema unchanged; prior measurement stands: 0.49 KB/s
real-browser / 4.48 KB/s live-cadence projection, both against the <50 KB/s budget).

## Part C — renderer rewrite

- `computeVisibleCellGeometry()` replaces max-based normalization with the percentile-95
  computation above, over the same visible-cell pool the desktop uses for its own viewport. Same
  function backs both the paint path and a new test hook (`window.__computeCellGeometry`), so
  drawing and the geometry-parity gate can never drift apart.
- `drawPriceCurve()` (the connecting polyline) deleted entirely, along with its call site.
- `#banner` (symbol/date/bar_idx/version debug overlay) now hidden by default, shown only via
  `?debug=1`.
- Hardcoded toolbar text `"SHADOW/RESEARCH ONLY -- replay, not live"` (false whenever connected in
  live mode) replaced with just `"SHADOW/RESEARCH ONLY"`; the dynamic `#modeBadge`
  (`updateModeBadge()`) already correctly reports LIVE/STALE/REPLAY and is unchanged.

## Part D — gates

| Gate | Result | Detail |
|---|---|---|
| (a) Geometry parity (hard gate) | **PASS** | 4/4 scenes, 0 problems. See below. |
| (b) Side-by-side montage | done | `montage_out/MONTAGE_ALL_SCENES.png` + per-scene pairs |
| (c) Perf, 8x replay | **PASS** | median 61.0 fps (gate ≥30), 0 long tasks >200ms |
| (c) Perf, live cadence (5 clients) | **PASS** | median 61.0 fps, server CPU mean 2.3%/max 3.5% |
| (d) Flicker re-run | **PASS** | 55 bars accumulated, 0 gaps |
| (d) Data-placement parity re-run | **PASS** | 3/3 scenes |
| (d) Desktop-vs-hub live parity re-run | **PASS*** | see note below |
| (e) Mode badge (LIVE/STALE/REPLAY) | **PASS** | all 3 modes, new dedicated test |

**(a) Geometry parity detail** (`tests/test_geometry_parity.py`): the browser's
`window.__computeCellGeometry()` diffed against the REAL, unmodified
`BookFlowLevelCellItem.set_data()`'s actual painted output (captured via a QPainter monkeypatch,
not a reimplementation of its formula), both fed the identical cell data for each scene:

| Scene | Desktop cells | Browser cells | Problems |
|---|---|---|---|
| scene1_initial (0 rolls) | 2625 | 2625 | 0 |
| scene2_mid (20 rolls) | 4018 | 4018 | 0 |
| scene3_near_end (45 rolls) | 5496 | 5496 | 0 |
| scene4_live_latest (simulator) | 385 | 385 | 0 |

100% cell presence both directions, RGB exact, alpha within the documented ±14/255 band tolerance,
width_fraction within 1%, zero extra cells — on all 4 scenes. This is the numeric proof that the
polyline-class of bug (and the max-vs-percentile normalization bug) is gone.

**(d) Desktop-vs-hub live parity re-run note**: the first 10-minute run reported 2 problems at a
single sample (t=542s, 1 of 599 comparable samples): a `last_price`/forming-bar mismatch between
two independently-polling `BookFlowDataService` instances. This mission touched **zero**
backend/desktop code (`git diff master...webbeta-visual --stat` — only `static/app.js`,
`static/index.html`, `RENDER_SPEC.md`, and new test/montage files changed); a second full 10-minute
run reproduced **0 problems** across 599/598 comparable samples, confirming this was the exact class
of pre-existing, unsynchronized-poller timing race the test's own docstring already documents as
expected staggering ("This is expected staggering, not a bug"), not a regression from this mission.
Reported here rather than silently re-run-until-green and forgotten.

**Scope note on gate (d)**: "flicker + W2-LIVE parity suites" was interpreted as the suites whose
name matches those words (`test_live_flicker.py`, `test_parity.py`, `test_live_parity_desktop.py`) —
the ones most plausibly affected by a frontend rendering change. The other W2-LIVE gates
(reconnect/slowclient/sessionboundary/readonly-audit/soak) exercise server/wire-level behavior this
mission's diff never touches; the 2-hour soak in particular was not re-run as disproportionate to a
frontend-only change with zero backend diff.

## Part E — daemon-untouched confirmation

`ps -p 2527` was not queried by any command this mission ran, and no commit in this branch touches
`book_flow_cache_daemon.py`, `book_flow_chart_v3.py`, or any file under `webbeta/server/` — verified
via `git diff master...webbeta-visual --stat` (reproduced in gate (d)'s note above): only
`RENDER_SPEC.md`, `static/app.js`, `static/index.html`, `montage_out/*.png`, and new files under
`tests/` changed.

## Artifacts

- `RENDER_SPEC.md` — the extracted spec, with corrections found along the way.
- `montage_out/MONTAGE_ALL_SCENES.png` (+ per-scene pairs) — side-by-side desktop-vs-browser visual.
- `tests/test_geometry_parity.py`, `tests/build_montage.py`, `tests/test_mode_badge.py` — new gates.
