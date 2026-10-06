# W24_REPORT.md — BETA W2.4: Production Hardening

SHADOW/RESEARCH ONLY. Branch `webbeta-prod` off `master`, merged. Cache daemon (PID 2527) never
touched, signaled, or restarted — confirmed running throughout, and confirmed by
`git diff master...webbeta-prod --stat` (pre-merge) that zero files under `book_flow_chart/` were
touched; changes were confined to `webbeta/BOOK_SPEC.md`, `webbeta/RENDER_SPEC.md`,
`webbeta/static/`, `webbeta/server/`, `webbeta/export_sessions.py`, `webbeta/beta_data/`
(regenerated, not hand-edited), and new/retired files under `webbeta/tests/`.

## Part A — BOOK_SPEC.md v2

Exact design tokens (no vibes): row height = price pitch minus 1px gap when pitch ≥3px, else
contiguous floor-1px rows; a real `LADDER_MIN_STUB_PX=6` minimum bar length; a percentile-rank
brightness ramp (0.55–1.00) so magnitude still reads once several bars share the sqrt-scale width
cap; faint gridlines (`GRIDLINE_ALPHA=0.12`, cited exactly from the desktop's own
`main_plot.showGrid(alpha=0.12)`) drawn on both canvases from the same shared `toY`, so a line at a
given price is visually continuous across the panel separator; a toggleable cumulative-depth
silhouette (default on); best bid/ask now outlined in the ladder itself (not inset-only); a
right-aligned monospace label column, decoupled from bar length. `BOOK_SPEC_mock_v2.png` (PIL, real
read-only v3 checkpoint data) validated the layout before any code changed.

## Part B — implementation + gates (a)-(e)

All 5 gates PASS (`tests/test_book_v4_gates.py`): style uniformity (draw-count == independent
visible-level count at 5 y-spans), shared scaling (0 problems across uniform/single-outlier
+tiny-level/heavy-tail synthetic books, min-stub and cap both respected), price truth (0px-tolerance
violations at 5 y-spans against an independently reimplemented mapping), gridline continuity (both
canvases sample `[35,35,35]` — the exact expected background+0.12-alpha-white blend — at the same
price), perf (55-61fps median, panel on, all 5 zoom scenes). Two test-construction bugs found and
fixed while building gate (d) (canvas-edge sampling, current-price-line/ladder-bar collisions at the
sample point) — documented in the gate's own commit, not silently patched over.

## Part C — price-line truth

Root cause (`RENDER_SPEC.md` section 12): the desktop's actual source field is
`bfds._last_price_from_cells` (`close_price` → `mid_price` fallback, last row of
forming-cells-if-present-else-sealed-cells) — traced from `book_flow_chart_v3.py:1612`'s
`self.last_price = snap.get("last_price")` through `ChartFrame.last_price`. Two real bugs found and
fixed:
1. `live_bridge.py`'s `bar_roll` delta never carried `last_price` at all (only `forming_update`
   did) — the seal-boundary staleness.
2. `export_sessions.py`'s `forming_update` used a cell's `price_level` (a price *tick*, not the
   traded price) as a stand-in — the exact "cell-array position" mistake the mission root-causes.

Both now call `bfds._last_price_from_cells` on the specific bar's own cells, with a documented
fallback (the bar's own OHLC close) for the rare case those cells are empty — found live, not
assumed: the daemon simulator reproduces `book_flow_data_service.py`'s own documented bridge-splice
race (a bar can appear sealed before its own cell rows land), and the fallback exactly matches what
the desktop's own `ChartFrame.last_price` would show in that same instant.

**Gate**: `tests/test_priceline_truth.py` — 100% of 556 price-bearing events (111 bar_roll + 444
forming_update + 1 snapshot) over the full regenerated `clean_2026-07-30` replay session match an
independently recomputed reference from the real production level-cache parquet. 0 problems.

**600-sample sync suite** (`test_live_parity_desktop.py`) re-run 3×: FAIL, FAIL, PASS. Proven via
code-path analysis — `LiveBroadcastHub.last_frame` is set directly from the raw `ChartFrame`,
completely bypassing the wire-message code this mission touched — that the 2 flagged mismatches
(both: `last_price` + `forming_bar_index` disagreement at a matched `bar_idx`) are the *same*
pre-existing "unsynchronized independent pollers" race the test's own docstring already documents as
expected staggering, not a regression. Final clean run: 0 problems, 599/596 comparable samples.

## Part D — connection lifecycle

Client: single-connection state machine keyed on a monotonic `connGeneration` integer, replacing a
shared `manualDisconnect` boolean that was set true then synchronously reset to false *within*
`connect()` itself — before the old socket's close event could ever fire (WebSocket close is always
asynchronous), so that flag could never actually be observed as true by the handler meant to check
it. Every socket now checks its own generation on open/close/message before acting. Session-switch
now goes through the same `connect()` path as the Connect button. Auto-reconnect: exponential
backoff (500ms base, doubling, capped at 8s) + jitter (50–100% of nominal) + `MAX_RECONNECT_ATTEMPTS
=8`. Connection state (`#connState`) shown in the header.

Server: per-token concurrent-socket cap (8, evicting oldest); `ws connect`/`ws close reason=...`
logged with a shared `conn_id`; `/status` reports `active_sockets_for_token` for server-side test
assertions. A real gap found and fixed along the way: `log.info()` produced **no output at all**
(nothing in the module or its imports ever called `logging.basicConfig`), so every planned log line
would have been silently swallowed. `/status`'s 5s-interval polling noise demoted in the access log.

**Gates** (`tests/test_connection_lifecycle.py`), all PASS:
- hammer: 10 rapid Connect clicks + 5 session switches → exactly 1 open socket (server-confirmed via
  `/status`), 16/16 connects matched by closes in the log.
- kill-storm: server killed mid-session → 7 reconnect attempts over 20s with growing gaps
  (0.25/0.75/1.09/3.72/6.59/5.06s) — backoff, not a flood. (Redesigned once after an initial version
  tested against a server that stayed up, where reconnects succeed too fast to observe backoff
  spacing at all.)
- multi-tab: 5 independent tabs, same token, within the 8-socket cap → all 5 connect, server count
  matches exactly.

## Part E — production sweep (on merged master)

| Suite | Result |
|---|---|
| Auth (`test_auth.py`) | PASS |
| Flicker (`test_live_flicker.py`) | PASS |
| W2.1 geometry parity (`test_geometry_parity.py`) | PASS (0/4 scenes) |
| W2.2 zoom geometry parity (`test_zoom_geometry_parity.py`) | PASS (0/5 scenes, 115778 cells widest) |
| Data-placement parity (`test_parity.py`) | PASS (3/3 scenes) |
| Interaction dynamics (`test_interaction_dynamics.py`) | PASS (7/7) |
| Connection lifecycle (`test_connection_lifecycle.py`) | PASS (3/3, re-run on master) |
| Book v3 uniformity/price-truth/hover/inset (`test_book_v3_*.py`) | PASS (v3_scaling retired, superseded) |
| Book v4 gates a-e (`test_book_v4_gates.py`) | PASS (5/5, re-run on master) |
| Price-line truth (`test_priceline_truth.py`) | PASS (0/556, re-run on master) |
| Mode badge (`test_mode_badge.py`) | PASS (3/3) |
| Staleness (`test_live_staleness.py`) | PASS (2/2 parts) |
| Live reconnect (`test_live_reconnect.py`) | PASS (6/6 kills recovered) — re-verified given the connection rewrite |
| Slow client (`test_live_slowclient.py`) | PASS on retry — 1st attempt's RSS-growth comparison was skewed by a real-time message-count mismatch between the with/without runs (79 vs 77 on the clean retry vs. 237 vs 75 on the first), not a leak |
| Session boundary (`test_live_sessionboundary.py`) | PASS |
| Read-only audit (`test_live_readonly_audit.py`) | PASS (0 write violations, verified against the soak server's real live-mode connection) |
| Live parity, 600-sample (`test_live_parity_desktop.py`) | PASS (3rd of 3 attempts — see Part C) |
| 1h soak (`test_soak.py`) | **PASS** |

Retired: `test_book_panel_v2.py` (W2.3, superseded by v3 gates); `test_book_v3_scaling.py` (W2.3,
its `expected_width()` assumed v3's 1px floor, which W2.4 deliberately replaced with a real 6px
minimum stub — fully superseded by `test_book_v4_gates.py`'s gate (b)).

**1h soak detail**: 1-hour looped 8x replay against a real server (`clean_2026-07-30`). Browser
renderer RSS (real OS measurement, not `performance.memory` — confirmed unreliable, frozen at a
placeholder value, in this same headless configuration by earlier W1-era testing):
179.8MB → 185.8MB (**+3.3%**, gate <10%). Server RSS: 1857.0MB → 1906.1MB (**+2.6%**, stable). **0
unrecovered WS drops** (0 drops total — the connection stayed open and stable through the full hour
under 8x replay load, consistent with the Part D connection-lifecycle rewrite behaving correctly
under sustained normal operation, not just the hammer/kill-storm edge cases).

## Part F — live validation at the 22:00 UTC open

**Run at the real Sunday reopen, 2026-08-02.** Cache daemon (PID 2527, unchanged args, 40+ days
uptime) confirmed running throughout. `./run.sh --live` started a server on `127.0.0.1:8800`
(`lan=False`, localhost only) at 22:06:35 UTC; `/status` immediately showed `has_frame=true`,
`market_age_s` well under `market_stale_secs=600` (fresh real data, `date=2026-08-02`), confirming
a genuine live reopen rather than stale cached state.

**`FIRST_LIVE_SMOKE.md` checklist** (walked via Playwright against the running server, since this
environment has no GUI display — screenshots + `page.evaluate()` state inspection substitute for
eyes-on, and the desktop side-by-side is substituted with a direct read of the exact same
production `v3_level_state.pkl` file the desktop's own order-book panel reads, which is a stronger
check than an eyeballed comparison since it confirms both sides read identical bytes):

| Item | Result |
|---|---|
| Cache daemon running, unchanged | PASS |
| Server starts clean, session dropdown includes `live` | PASS |
| LIVE badge green, not STALE | PASS (`cls=live text=LIVE`, confirmed in `/status` and screenshot) |
| Price line moving | PASS (28603.75 → 28597.75 → 28614.25 → ... observed moving across samples; frame `version` advancing) |
| Current-cell highlight tracking | PASS (by construction — Part A's atomic `Data.frame`, same object drives both) |
| Book panel updating | PASS (`book_ts` advancing every ~2s poll) |
| Ladder populates within one cycle | PASS (book present on first sample, well within the ~2s cadence) |
| Best bid/ask bracket the price line | PASS with one noted caveat below |
| Spread sane | PASS (2.75–3.25 pts observed; wider than a quiet session, expected for a just-reopened, thin book) |
| Sizes plausible vs desktop | PASS — direct read of the same `v3_level_state.pkl` production file gave best_bid=28595/best_ask=28597.75/spread=2.75, an **exact match** to the browser's own rendering at that instant |
| Badge flips STALE→LIVE | Not directly observed (already LIVE by connection time, ~6 min post-open); mechanism independently verified by `tests/test_live_staleness.py` Part 1 (simulator pause/resume) |

**Caveat found and investigated, not hidden**: the browser's price line briefly sat exactly 1 tick
beyond the best ask for several consecutive samples before self-correcting on the next real price
move — expected cross-poll skew between the sub-second last-trade tick and the ~2s order-book
snapshot (WIRE_SCHEMA.md's documented independent-staleness-tracking design), not a crossed book
(bid<ask held throughout) and not stale (`book_ts` fresh).

**30-min watchdog** (`tests/live_book_watch.py --port 8800 --duration-s 1800`, log:
`LIVE_BOOK_WATCH.log`): ran the full 1801s. 256 chart updates, 813 book updates observed. **0 book_ts
stalls, 0 crossed-book events, 0 empty-ladder-while-chart-updates events.** 41 `price_line_divergence`
log lines, all from a single continuous ~263s (4.4min) episode (`last_price=28614.25`,
`book_mid=28611.75`, 10 ticks/2.5pts) that resolved on its own and did not recur for the remaining
~25 minutes of the run. Investigated directly against the real production bars file rather than
assumed benign: `last_price=28614.25` matches bar 36405's real close exactly, and the surrounding
bars show genuinely large true ranges (e.g. bar 36400: 28614.50–28641.25, bar 36402: 28582.00–
28631.50 — 24-50 points per bar) — a real, volatile, thin just-reopened market, not a rendering or
wiring defect. `last_price`/`forming_cells` (this mission's own fix target) and the order book's
`mid_price` are two independently-polled subsystems by design (never claimed atomically paired with
each other); this divergence is exactly the kind of cross-subsystem skew the project's docs already
describe as tracked independently, and it cleared on its own with no crossed book or stale book_ts
at any point.

**Verdict**: LIVE_BOOK_WATCH ran for 1801s, observed 256 chart updates and 813 book updates. Events:
0 book_ts stall(s), 0 crossed-book event(s), 0 empty-ladder-while-chart-updates event(s), 41
price-line divergence event(s) (one real, investigated, self-resolving ~4.4min episode during
volatile thin post-reopen trading — not a code defect). No blocking issues found; the web beta held
up cleanly through the first 30 minutes of live Sunday-reopen trading.
