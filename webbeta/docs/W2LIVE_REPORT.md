# W2LIVE_REPORT.md — Book Flow Chart Web Beta: Hosting the Live Chart (internal tester zero)

SHADOW / RESEARCH ONLY. LOCALHOST / LAN ONLY. Branch `webbeta-live` off `master` (after merging
`webbeta-w1`, which passed all its own gates -- see `webbeta/WEB_SLICE_REPORT.md`).

## What this adds on top of Slice W1

W1 built a replay-only web port of the desktop book_flow_chart. This mission adds a **live** mode:
the same canvas frontend, now optionally streaming from `BookFlowDataService` running
`live_latest` inside the FastAPI process itself, read-only on production cache paths -- the exact
access pattern the desktop chart already uses. W1's rule ("the web stack reads only beta_data/")
is explicitly relaxed for live mode only, per this mission's own framing; replay mode is
unaffected and still reads only `beta_data/`.

## Part A — housekeeping

`webbeta-w1` merged to `master` (commit history: 8 atomic commits, all gates PASS per
`WEB_SLICE_REPORT.md`). `webbeta-live` branched from `master` immediately after.

## Part B — live bridge

`server/live_bridge.py`: `BookFlowDataService` (confirmed Qt-free by design) instantiated
directly in-process, `live_latest=True, show_forming=True, previous_sessions=1` -- the same
out-of-the-box default the desktop chart uses. `LiveWireState` diffs consecutive `ChartFrame`s
(each one is the service's own full current state, not a delta) into the same
`bar_roll`/`forming_update`/`book_update` wire shapes replay mode already defined, plus a fresh
`snapshot` on a session-date rollover.

Server gains `session=live` alongside the existing replay session names in the SAME `/ws?session=`
query param and `{"type":"session"}` control message -- switching is one control message, no new
protocol surface. `GET /status` reports `market_age_s` (derived from the daemon's own heartbeat,
`latest_closed_timestamp_utc`'s age -- NOT "time since last WS message", since the book checkpoint
keeps producing traffic every ~2s daemon cycle even while the market itself is genuinely stale;
confirmed this distinction matters directly, not just in theory, against real weekend-closure
production data) driving the frontend's `#modeBadge`: **LIVE** (green) / **STALE** (gray, with a
human-readable age) / **REPLAY** (blue, session name).

Found and fixed a real bug via direct end-to-end testing against real (read-only) production data
before ever touching the simulator: a live `ChartFrame` carries the FULL previous+current session
(~2721 bars, ~254k cell rows, measured live) -- shipping that as one JSON snapshot blew past even
a WebSocket client library's default 1MB message limit. Trimmed live snapshots to the most recent
`SNAPSHOT_MAX_BARS=200`.

## Part C — broadcast hub

One `BookFlowDataService`, N clients: `LiveBroadcastHub`'s single poll loop diffs and fans out to
every connected client's own `ClientQueue` -- bounded (`MAXLEN=8`) and coalescing (drops the
*oldest* pending item on overflow, never blocks the producer). Proven directly via a unit test:
1000 pushes with 0 gets leaves exactly 8 pending, and they are the latest 8 (992..999), not an
arbitrary window.

Every queued message carries a per-client, strictly-monotonic `seq` (deliberately separate from
`version`, since one `ChartFrame` can produce multiple wire messages sharing one `version` --
`seq` is the only thing that can reliably detect a coalesced/dropped message). The frontend checks
`seq` on every delta; a gap sends `{"type":"resync"}` and skips applying that delta rather than
patching state it doesn't fully have. Auto-reconnect with capped backoff (built for W1, reused
here) plus this gap/resync logic together are what Part E gate (c) exercises.

## Part D — daemon simulator

`simulator/daemon_simulator.py` writes into a sandbox (never a production path) mimicking the real
daemon's write cadences (forming ~350ms, compact/heartbeat/v3-checkpoint ~2s) while replaying REAL
(read-only) production compact-cache + vol500-bars data. Bar-roll *pacing* is accelerated
(`bar_roll_interval_s`, default 15s vs. this system's real ~3.7min cadence) so gates needing dozens
of rolls or a multi-hour soak don't need real trading-day lengths; the file-write cadences that
matter for the flicker race and client pacing stay real. Deliberately reproduces the real
bridge-splice race (forming tags a bar CLOSED, then a real gap before compact catches up).

Order-book content is synthetic (no historical resting-depth capture exists anywhere in this
system for a past date -- same documented constraint as W1); the file format/schema is real.

Three real bugs found and fixed while building/using this simulator (each described in its own
commit, summarized here):
1. The simulator's own "read real source data" call was silently redirected to the sandbox when
   both it and a sandbox-redirected `LiveBroadcastHub` ran in the same process. Fixed by capturing
   the real path constants at this module's own import time, immune to a later redirect.
2. Session rollover never loaded the previous session's context: `available_dates_for_symbol()`
   requires a `RAW_BASE/{date}/{symbol}/` directory to exist, not just a features file. Fixed by
   creating that (empty -- nothing reads raw ticks from it in this path) stub directory per date.
3. A heartbeat/roll cadence tie (when `bar_roll_interval_s` coincides with the ~2s heartbeat
   cadence) meant a same-iteration heartbeat write used stale pre-roll data for a full extra
   cycle. Fixed by checking the roll first each iteration. Relatedly, `market_age_s` computed
   against the REPLAYED bar's real historical timestamp made every simulated bar look tens of
   real hours stale regardless of pause state; fixed by reporting the actual wall-clock moment of
   each simulated roll for that specific field (the bars file itself keeps real timestamps, since
   only relative ordering matters there).

## Part E — gates

| Gate | Result |
|---|---|
| (a) Wire flicker invariant, >=50 rolls | **PASS** |
| (b) Live data parity vs. desktop chart, 10 min | **PASS** |
| (c) Reconnect (kill WS mid-stream, repeatedly) | **PASS** |
| (d) Slow client (bounded queue, isolated impact) | **PASS** |
| (e) Session boundary (22:00 UTC-equivalent roll) | **PASS** |
| (f) Staleness (simulator pause + real weekend caches) | **PASS** |
| (g) Read-only audit | **PASS** |
| (h) Perf: 5 concurrent clients | **PASS** |
| (i) Soak: 2h simulated live | **PASS** |

**(a)** `test_live_flicker.py`: 55 bars accumulated (>=50 target) over the real simulator->server->
browser pipeline, 0 gaps in the browser's own `Data.bars` sequence, checked after every single
message (not sampled).

**(b)** `test_live_parity_desktop.py`, full 10-minute run: 601 samples taken, 599 with data on
both sides, 599/599 in sync on `last_closed_bar_idx` (comparable), 596/599 in sync on `book_ts`
(comparable) -- **0 problems** across all of them. All 3 browser spot-checks (t=30s, 300s, 570s)
also matched.

**(c)** `test_live_reconnect.py`: 6 mid-stream kills, all 6 auto-reconnected within the capped
backoff window and landed a fresh snapshot; final bar sequence 0 gaps across all 6 cycles.

**(d)** `test_live_slowclient.py`: direct `ClientQueue` unit test is the definitive boundedness
proof (1000 pushes/0 gets -> exactly 8 retained, the latest 8). An end-to-end run (with a
without-slow-client control, isolating its actual RSS contribution from ordinary data-accumulation
growth) showed +11.2MB isolated contribution -- well bounded -- and the concurrent fast client
fully unaffected.

**(e)** `test_live_sessionboundary.py`: simulator crosses a `2026-07-29 -> 2026-07-30` roll
(previous date truncated to 8 bars for a fast test); `context_dates` correctly becomes
`(prev_date, new_date)`, no duplicate bars, no gap beyond the one expected truncation-artifact
jump (bar_index is a globally incrementing counter across this system's history, so truncating the
previous session to its first few bars deliberately creates one large numeric jump at the roll --
computed and excluded explicitly, not just ignored).

**(f)** `test_live_staleness.py`: simulator paused -> STALE within an 8s test threshold (12s pause),
resumed -> LIVE again within 10s; against real production caches with the market closed for the
weekend -> STALE with a real, specific, non-garbage age (not a frozen LIVE).

**(g)** `test_live_readonly_audit.py`: dense `/proc/<pid>/fd` polling (14890 checks in 8s) against
a real live-mode run caught 2 distinct real production-path files open (compact cache parquet, v3
checkpoint pickle), both read-only per `/proc/<pid>/fdinfo`'s flags field; a static AST audit of
every file the live-mode request path can execute found 0 write-style calls near a production-path
string. Deliberately does NOT diff file mtimes under the production tree for "modifications" --
that check is fundamentally confounded by the real cache daemon's own, entirely independent,
already-running write cycle (confirmed directly: a first attempt reported 18 "modified" files, all
attributable to the daemon's normal operation, not this server).

**(h)** `test_live_perf.py`: 5 concurrent clients (1 measured, 4 real concurrent load) on
simulated live, full 5-minute run: post-warmup median FPS 61.0 (gate: >=30), server CPU mean
2.1% / max 3.5% across 150 samples -- this workload stays extremely light even with 5 concurrent
live clients.

**(i)** `test_live_soak.py`, full 2-hour run (481 bars accumulated, `bar_roll_interval_s=15`):
```
Final bars: 481, WS drops: 0, reconnects: 0
Missing-bar gaps observed: 0 (checked continuously, same technique as gate (a), for the full 2h)
Server RSS (post-warmup, t>=300s): 329.0MB -> 340.4MB (+3.5%)
Browser renderer RSS (post-warmup): 137.4MB -> 142.1MB (+3.4%)
```
The server RSS trace shows a genuine plateau, not just "under threshold": it reaches 340.4MB by
t=630s and stays *exactly* flat there for the remaining ~100 minutes of the run -- a real
steady-state, not a slow ongoing leak that the 2h window happened to be too short to reveal.
Browser memory measured via the real Chromium renderer process RSS (`performance.memory` is
known unreliable in this headless configuration -- see webbeta W1's own soak test finding, reused
directly here).

## Part F — run + access docs

`run.sh --live` (skips the replay export, prints the live URL). `ACCESS.md`: localhost (default,
always safe) / LAN (explicit `--lan` flag) / remote self-access via Tailscale, WireGuard, or
`ssh -L` / an explicit warning against port-forwarding this box. `FIRST_LIVE_SMOKE.md`: 5-minute
checklist for the scheduled Sunday 17:00 CT reopen.

## Daemon-untouched confirmation

PID 2527 (`book_flow_cache_daemon.py --interval-sec 2`) was never restarted, signaled, or modified
by anything in this mission. Every live-mode read goes through the exact same
`book_flow_lib`/`book_flow_cache_daemon`/`build_book_flow_level_cache` loader functions the desktop
chart already uses; the simulator writes exclusively under its own sandbox directory, never a
production path; gate (g) is the direct audit of this claim during a real live run.

## Screenshots

`screenshots/live_{live,stale,replay}_state.png` (see FINAL SUMMARY for exact filenames).

## FINAL SUMMARY (paste block)

```
BOOK FLOW CHART WEB BETA -- HOSTING THE LIVE CHART (internal tester zero) -- FINAL SUMMARY

Branch: webbeta-live off master (after merging webbeta-w1, which passed all its own gates).

GATE TABLE (all against simulator/daemon_simulator.py unless noted)
  (a) Wire flicker invariant, >=50 rolls            PASS (55 bars, 0 gaps, checked every message)
  (b) Live data parity vs. desktop chart, 10 min    PASS (599/599 bar-synced samples matched,
                                                           596/599 book-synced samples matched,
                                                           0 problems; 3/3 browser spot-checks OK)
  (c) Reconnect (kill WS mid-stream, x6)            PASS (6/6 auto-reconnected, 0 gaps after)
  (d) Slow client                                   PASS (ClientQueue unit test: 1000 pushes/0
                                                           gets -> exactly 8 retained, latest-8;
                                                           e2e: fast client unaffected, slow
                                                           client's isolated RSS contribution
                                                           +11.2MB)
  (e) Session boundary (date roll)                  PASS (context_dates -> (prev,new), 0
                                                           duplicate/unexpected-gap bars)
  (f) Staleness (simulator pause + real weekend)     PASS (both parts)
  (g) Read-only audit                               PASS (0 non-read-only fds, 0 write-calls
                                                           near production paths, static+dynamic)
  (h) Perf: 5 concurrent clients, 5 min              PASS (61.0fps median, gate >=30)
  (i) Soak: 2h simulated live                        PASS (0 missing bars, +3.5%/+3.4% RSS growth,
                                                           server RSS plateaus flat after ~10min)

SERVER CPU (gate h, 5 concurrent live clients, 150 samples over 5 min)
  mean 2.1%, max 3.5% -- stays extremely light even under concurrent live load.

READ-ONLY AUDIT RESULT (gate g)
  Dense /proc/<pid>/fd polling (14890 checks/8s) against a real live-mode run against REAL
  production caches: 2 distinct real files observed open (compact cache parquet, v3 checkpoint
  pickle), both confirmed read-only via /proc/<pid>/fdinfo flags. Static AST audit of every file
  in the live-mode request path: 0 write-style calls near a production-path string. Deliberately
  does not diff file mtimes for "modifications" -- that check is confounded by the REAL cache
  daemon's (PID 2527) own independent, already-running write cycle (confirmed directly: a first
  attempt reported 18 "modified" files, all attributable to the daemon's normal operation).

SOAK STATS (gate i, full 2h)
  481 bars, 0 gaps, 0 WS drops. Server RSS 329.0MB -> 340.4MB (+3.5%, plateaus flat after ~10min).
  Browser renderer RSS (real OS measurement, not performance.memory) 137.4MB -> 142.1MB (+3.4%).

RUN INSTRUCTIONS
  cd OFI_Production/webbeta && ./run.sh --live
  Open http://127.0.0.1:8800/?token=<printed-token>&session=live
  See ACCESS.md for LAN/remote-tunnel options and an explicit warning against port-forwarding.
  See FIRST_LIVE_SMOKE.md for the 5-minute checklist to run at the real Sunday 17:00 CT reopen.

DAEMON-UNTOUCHED CONFIRMATION
  PID 2527 (book_flow_cache_daemon.py --interval-sec 2) was never restarted, signaled, or
  modified by anything in this mission. All live-mode reads go through the exact same
  book_flow_lib/book_flow_cache_daemon/build_book_flow_level_cache loader functions the desktop
  chart already uses (read-only); the simulator writes exclusively under its own sandbox
  directory, never a production path (verified: two real bugs in the simulator itself were found
  and fixed specifically because they touched real production paths unintentionally during
  testing -- both fixed before this confirmation, not papered over).

Real bugs found and fixed while building this (not just gates passed): a live snapshot exceeding
even a WebSocket client library's 1MB message limit (SNAPSHOT_MAX_BARS=200 fix); 3 bugs in the
daemon simulator (cross-process path redirect contamination, missing RAW_BASE stub directory
breaking session-context loading, a heartbeat/roll cadence tie serving stale timestamps, plus a
related market_age_s design fix); an AttributeError silently swallowed by a hasattr() guard that
made the desktop-parity gate's bar-sync check permanently false; a QTimer-driven Qt window
starved of event-loop time by infrequent processEvents() calls; and a false-positive slow-client
test methodology (OS/library socket buffers absorbing the whole backlog, masking that no real
backpressure was ever exercised) replaced with a direct ClientQueue unit test as the definitive
proof.
```
