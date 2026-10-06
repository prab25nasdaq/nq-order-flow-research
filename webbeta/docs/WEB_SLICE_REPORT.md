# WEB_SLICE_REPORT.md — Book Flow Chart Web Beta, Slice W1 (Replay-First)

SHADOW / RESEARCH ONLY. LOCALHOST / LAN ONLY: no public exposure, no TLS, no live data anywhere in
this slice. Branch `webbeta-w1`, new directory `OFI_Production/webbeta/`.

## What this is

A browser port of the book_flow_chart desktop app's replay mode: a canvas-rendered price-axis
book-flow level chart with an order-book side panel, current-price line, and current-cell
highlight, streamed over a WebSocket from a small FastAPI backend that replays 3 exported sessions
at 1x/8x/pause speed. No framework, no build step on the frontend; invite-token auth; nothing here
reads a production cache path except the one-time, read-only export tool.

## Run it

```
cd OFI_Production/webbeta
./run.sh
```

This exports the 3 sessions on first run (read-only against production caches, writes only under
`beta_data/`), then starts the server on `http://127.0.0.1:8800` and prints an invite token (or set
`WEBBETA_TOKENS=yourtoken` yourself). Open:

```
http://127.0.0.1:8800/?token=<token>&session=clean_2026-07-30
```

## Part A — wire schema + data export

Full contract: `WIRE_SCHEMA.md`. Summary: a `snapshot` message (bars/cells as compact parallel
arrays, never per-cell objects) sent once per session start, plus 3 delta kinds (`bar_roll`,
`forming_update`, `book_update`), trimmed to exactly what the canvas renders.

**3 exported sessions** (`beta_data/`, via `export_sessions.py`, the only place in this slice that
reads a production cache path):

| session | kind | bars (snapshot+streamed) | notable |
|---|---|---|---|
| `clean_2026-07-30` | clean | 151 (40+111) | ordinary recent session |
| `clean_2026-07-29` | clean | 151 (40+111) | ordinary recent session |
| `deadfeed_2026-07-08` | dead_feed | 151 (60+91) | spans the Jul 7-8 Rithmic dead-feed recovery boundary; bar_index 17538 has a real ~64-minute inter-bar gap (largest of 6 gaps >10min in this session) |

**Order-book depth is not exported for any session** — historical resting-depth capture does not
exist anywhere in this system for a past date, on the desktop app either (confirmed in
`book_flow_chart/STEP3_REPORT.md` Part E). All 3 sessions correctly show the STALE badge
throughout replay; this is real, expected behavior, not a missing feature (see `stale_state.png`).
The `book_update` wire path itself was verified via a direct synthetic message injection, kept
explicitly separate from the 3 real sessions (`book_panel_live.png`, `book_panel_zoomed_labels.png`).

**Wire size (Part A budget: <50 KB/s @ 1x steady-state, with permessage-deflate):**

| measurement | result |
|---|---|
| `export_sessions.py --report-sizes`, this replay's own (sparse) pacing | 0.01-0.09 KB/s across all 3 sessions |
| Live-cadence projection (largest real `forming_update` message at the ~350ms production forming-cache rate) | 4.48 KB/s |
| **Real browser measurement** (`test_payload.py`, actual WS frames via CDP, 20s @ 1x, gzip-recompressed to approximate what permessage-deflate puts on the wire) | **0.49 KB/s** |

All three ways of measuring this land comfortably under the 50 KB/s budget, including the
live-cadence projection that stress-tests the wire *format* rather than this particular
replay's own sparse historical pacing.

## Part B — FastAPI replay backend

`GET /sessions` (token-gated) and `WS /ws?token=&session=` (streams `ChartFrameWire` at recorded
cadence, speed/session control messages). Auth via a dependency raising `HTTPException(401)` —
verified this correctly rejects the WebSocket handshake with an HTTP 401 before any accept/upgrade,
for both a missing and an incorrect token. `permessage-deflate` is negotiated automatically by
uvicorn's default WS implementation (confirmed via a live connection, no server config needed).
Binds `127.0.0.1` by default; a non-loopback `--host` is refused without an explicit `--lan` flag.

All session data loads once at startup into read-only structures; each connection owns its own
playback position/speed — concurrent clients never share mutable state.

## Part C — canvas frontend

Vanilla JS + Canvas2D, two canvases (main chart, book panel) sharing one price-axis mapping
function (exact by construction, unlike the desktop's pyqtgraph `setYLink`, which turned out to
have a small pixel-geometry-based imprecision — see `book_flow_chart/STEP3_REPORT.md` Part G gate
(a)). Cell candles batched by color group (sign x alpha-band) to minimize `fillStyle` changes;
switches to an aggregated bar-group x price-bin LOD render once the visible span exceeds 60 bars,
mirroring the desktop's WIDE/OVERVIEW LOD. Order-book panel: batched bid/ask bars, size labels only
once pixels-per-tick >=11, STALE badge + grey palette when the book is stale — ticked by a 1s
interval so staleness is caught even if the whole feed pauses and no new message ever arrives
(the same class of fix `book_flow_chart_v3.py` needed for its own stale-badge gate). Render loop is
`requestAnimationFrame` gated by a dirty flag — no busy loop.

Screenshots: `screenshots/{chart,book_panel_live,book_panel_zoomed_labels,stale_state}.png`.

## Part D — gates

| Gate | Result |
|---|---|
| (a) Data-placement parity, 3 fixed scenes | **PASS** |
| (b) Perf: 10-min 8x replay, headless Chrome | **PASS** |
| (c) Payload budget | **PASS** |
| (d) Auth (401 / stream) | **PASS** |
| (e) Soak: 1h looped replay | **PASS** — see below |
| (f) Multi-client: 5 concurrent | **PASS** |

**(a) Data-placement parity** (`test_parity.py`) — 3 scenes (initial snapshot, mid-replay after 20
bar-rolls, near end-of-content after 45): cell prices, bar positions, and color-classes (sign of
`signed_flow`) compared between the browser's live `Data` model and an independent reference
computed directly from `beta_data/*.jsonl`. All 3 scenes matched exactly (0 problems). Built by
deterministically injecting the exact wire-message prefix via `handleMessage()` rather than racing
real-time speed control against JS/network timing — an earlier version of this test used an
extreme speed multiplier to "fast-forward," which reliably overshot the target scene because the
whole session could stream through before the next poll observed it.

**(b) Perf** (`test_perf.py`, 10 real minutes at 8x, headless Chrome via Playwright, an injected
`requestAnimationFrame` counter + `PerformanceObserver` for long tasks):
```
Post-warmup median FPS: 61.0 (gate: >=30)
Long tasks >200ms during warmup: 0
Long tasks >200ms post-warmup: 0 (gate: 0)
```
Found and fixed a real bug while building this: Playwright's `add_init_script()` evaluates its
argument as a raw script, not a value to call — wrapping the injected counter code in `() => {...}`
silently defined and discarded a function without ever running its body. Fixed with an IIFE.

**(c) Payload** — see Part A's table; real-browser measurement 0.49 KB/s vs. the <50 KB/s budget.

**(d) Auth** (`test_auth.py`): no token and a bad token both return HTTP 401 on `GET /sessions` and
reject the WebSocket handshake with HTTP 401 before upgrade; a valid token streams normally on both.

**(e) Soak** (`test_soak.py`, 1 hour, 8x replay — long enough to exercise the server's session-loop
restart several times over, not just steady-state within one pass — final version `v509`, vs.
`v1` at connect, of the same ~151-bar session replaying on an ~9-minute cycle at 8x):
```
WS drops: 0, reconnects: 0, final socket open: True
Browser renderer RSS (post-warmup t>=300s): 146.3MB -> 146.1MB (-0.1%)
Server RSS (post-warmup t>=300s): 84.0MB -> 84.0MB (+0.0%)
Gate: browser memory growth <10.0% -> PASS
Gate: zero unrecovered WS drops -> PASS
```
Real finding caught and fixed while building this test: the first full run measured browser
memory via `performance.memory.usedJSHeapSize` and got a suspiciously exact flat 10000000 bytes
across all 12 samples over the whole hour. Verified directly that this is a non-functional
measurement in this headless configuration — the value stays frozen at that exact placeholder even
immediately after deliberately allocating a 20-million-element array on the page (Chrome quantizes
this API against timing-attack fingerprinting). Re-measured using the actual OS-level RSS of the
Chromium renderer process (found by walking the test process's own descendants, since a global
process scan on this shared machine also turns up unrelated Chrome/Android-emulator processes with
the same `--type=renderer` flag) — the corrected numbers above are real, non-static readings (a
genuine small ramp during the first several minutes, then a stable plateau for the rest of the
hour), not a repeat of the same measurement artifact.

**(f) Multi-client** (`test_multiclient.py`, 5 concurrent clients across the 3 sessions with
repeats so cross-talk would be immediately visible as a client showing another session's date):
0/5 cross-talk. Server CPU during the test (psutil, sampled every 1s): mean 0.6%, max 3.0% — this
workload is extremely light for the server (small, infrequent JSON messages), which is a genuine
finding, not a broken measurement (caught and fixed a real bug in the CPU-sampling helper itself
first: it matched this test harness's own bash wrapper process instead of the actual Python
server, since the wrapper's argv also contains the string "server.app" — caught via an implausible
~4MB RSS reading where the real process is ~84MB; fixed by also requiring the process's actual
executable name, not just a cmdline substring).

## Confirmation: production cache paths were never opened by the web stack

`export_sessions.py` is the **only** file in `webbeta/` that imports or reads from
`book_flow_chart/` — every other file (`server/`, `static/`, `tests/`) touches `webbeta/beta_data/`
exclusively. Verified by inspection (no other file references `book_flow_lib`,
`book_flow_data_service`, `book_flow_cache_daemon`, or any path under `book_flow_chart/cache/`),
and consistent with every test in this report running against a server process that was launched
without any production-cache environment or working directory. The cache daemon (PID 2527) was
never touched, restarted, or read from directly by anything in this slice.

## FINAL SUMMARY (paste block)

```
BOOK FLOW CHART WEB BETA -- SLICE W1 (REPLAY-FIRST) -- FINAL SUMMARY

Branch: webbeta-w1 (new dir OFI_Production/webbeta/). SHADOW/RESEARCH ONLY, LOCALHOST/LAN ONLY.

WIRE SIZE (budget: <50 KB/s @ 1x, permessage-deflate on)
  export_sessions.py --report-sizes (this replay's own sparse pacing): 0.01-0.09 KB/s
  Live-cadence projection (largest real forming_update msg @ ~350ms prod cadence): 4.48 KB/s
  Real browser measurement (CDP frame capture, 20s @ 1x, gzip-recompressed): 0.49 KB/s

PERF (10-min headless-Chrome, 8x replay)
  Post-warmup median FPS: 61.0 (gate: >=30)              -> PASS
  Long tasks >200ms, warmup: 0 | post-warmup: 0 (gate 0) -> PASS

GATE TABLE
  (a) Data-placement parity, 3 fixed scenes           PASS (0 problems, all 3 scenes)
  (b) Perf: 10-min 8x replay                          PASS (61.0 fps median, 0 long tasks)
  (c) Payload budget                                  PASS (0.49 KB/s vs <50 KB/s)
  (d) Auth (401 on missing/bad token; valid streams)   PASS
  (e) Soak: 1h looped replay                          PASS (renderer RSS -0.1%, server RSS +0.0%,
                                                              0 unrecovered WS drops)
  (f) Multi-client: 5 concurrent                      PASS (0/5 cross-talk)

MULTI-CLIENT SERVER CPU (5 concurrent clients, 15s, psutil @ 1s)
  mean 0.6%, max 3.0% -- this workload is extremely light for the server (small, infrequent
  JSON messages); a genuine finding, not a broken measurement (a real bug in the CPU-sampling
  helper was found and fixed first -- see Part D).

PRODUCTION CACHE PATHS: never opened by the web stack. export_sessions.py is the only file in
webbeta/ that reads book_flow_chart/ (read-only, one-time, offline) -- server/, static/, and
tests/ touch webbeta/beta_data/ exclusively. The cache daemon (PID 2527) was never touched,
restarted, or read from directly by anything in this slice.

Real bugs found and fixed while building this (not just gates passed): a missing initial `hello`
message + a session-switch race that could end a connection early (server/app.py); export pacing
anchored at the session's absolute start instead of the snapshot/stream boundary, so the first
live update wouldn't arrive for ~25+ minutes even at 8x (export_sessions.py); Playwright's
add_init_script() silently discarding a wrapped-but-never-called function (test_perf.py); psutil
process-matching picking up this test harness's own bash wrapper instead of the real server
process (test_soak.py / test_multiclient.py); and Chrome's performance.memory API returning a
frozen placeholder value instead of real heap data in headless mode (test_soak.py).
```
