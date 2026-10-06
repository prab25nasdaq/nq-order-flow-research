# LIVEADVANCE_DIAGNOSIS.md — W2.7: live path serves one snapshot then (reportedly) never advances

## What was tested (against the REAL, currently-healthy feed, market open, 2026-08-04)

1. **Ground truth, direct file reads**: `book_flow_cache_heartbeat.json`, the compact parquet, the
   forming-cache parquet, and the v3 state pickle are all being rewritten sub-2-seconds fresh,
   confirmed by direct stat + heartbeat read.

2. **Isolated `BookFlowDataService` instrumentation** (a fresh instance, `live_latest=True,
   previous_sessions=1`, identical construction to `live_bridge.py`'s, with `_file_sig` wrapped to
   log every call): 349 frames published in 180s (~1.9/s). Every file family's signature (compact,
   bars, forming, v3 state) genuinely varies over time — `mtime_ns` changes on every real write.
   `forming_cells_len` and `last_price` both change repeatedly and independently within the window.
   **The unmodified upstream service is not stuck — none of the four named suspects (under-
   invalidation, inode/handle caching, a dead poll loop, or a pinned active date) reproduce here.**

3. **`LiveWireState.diff()` instrumented directly** (wrapping the method on a real
   `LiveBroadcastHub`, 30s): confirms `forming_update` fires exactly when `forming_cells_len` or
   `last_price` actually changes relative to the previous frame processed, and `book_update` fires
   on the book's own ~2s cadence — both consistent with the isolated service test, not with a stuck
   comparison. A real, unrelated bug **was** found here (below) and fixed.

4. **Real end-to-end reproduction attempts** — a raw `websockets` Python client (30s) and a real
   Playwright/Chromium browser running the actual `static/app.js` (two separate runs, ~60s and
   175s, against a live server on port 8800): in every run, `Data.frame.version` climbed
   continuously (e.g. 821→1185 over 175s), `book_ts` advanced every ~2-6s, the price line moved
   exactly when the market printed a new price (29824.0 → 29815.5 at real t=105s, matching a
   genuine trade, not a bug), the connection state stayed `connected`/`readyState=1` throughout,
   and **`window.__wsReconnectCount` stayed 0 for the entire duration of every run** — no
   `ws close reason=client_disconnect code=1005` reconnect loop occurred.

**I could not reproduce the reported symptom** ("connects once, then never updates; ws close
code=1005 every ~10s, repeating") against the feed as it stands now, across ~6 cumulative minutes
of live end-to-end testing. This is reported honestly rather than assumed away or forced into a
tidy root cause that doesn't match the evidence.

## Named suspects (a)-(d), explicitly ruled out for the CURRENT, stable feed

- **(a) under-invalidation**: false. `_build_frame_live_latest`'s signature (`core_sigs` =
  compact+bars file sig, `forming_sig` = forming-cache file sig) never included the heartbeat at
  any point in the code as it exists today, and both sigs demonstrably vary every real write
  (confirmed directly, item 2 above). The heartbeat is used only to resolve `active_date`, never as
  part of the change-detection signature.
- **(b) inode/handle caching**: false. `_file_sig` reads `(mtime_ns, size)` fresh via `path.stat()`
  on every call — no cached file handle or inode anywhere in this path, confirmed by direct
  instrumentation showing the sig value itself changing every write.
- **(c) poll loop never starts / exception swallowed**: false. The isolated test's 349 published
  frames in 180s prove the service's background thread is running and its exceptions (if any)
  are not silently killing it; the hub-level instrumentation shows `_poll_loop` processing frames
  continuously for the full duration of every test run.
- **(d) active date pinned at startup**: false. `active_date = str(hb.get("active_date") or
  self.date)` is re-read from the heartbeat on every poll (not cached in `__init__`); observed
  `frame.date` was `"2026-08-04"` (today) throughout, correctly matching the heartbeat's own
  `active_date`.

## A real bug found and fixed anyway (not the reported symptom, but a genuine latent defect)

`LiveWireState`'s rollover branch initializes `self.bar_pos_map` from the snapshot's TRIMMED bar
list (`SNAPSHOT_MAX_BARS=200`), but a live_latest `ChartFrame.sealed_bars` covers the FULL
previous+current session (thousands of bars). On the very next `diff()` call, every one of those
older, never-sent bars looked like `new_sealed` (not yet in `bar_pos_map`) and got its own
`bar_roll` message — **observed directly: 1027 bar_roll messages from a single `diff()` call** on
a freshly-started hub. Each of the (up to `ClientQueue.MAXLEN=8`) most recent ones would reach a
client; the rest are wasted synchronous work (building + serializing ~1000 dicts, each iterating a
cells DataFrame) inside the SAME coroutine driving the hub's poll loop for every other client too.
This is a real, if narrow, source of event-loop latency spiking at the worst possible moment (right
when a client's context is being established) -- fixed (Part A) by marking those older bars
"already known" via a sentinel position in the same pass that builds the snapshot, so `new_sealed`
correctly comes back empty on the next call. Re-verified after the fix: max messages from any
single `diff()` call over an 8s startup window dropped from 1027 to 1.

## Honest hypothheses for the discrepancy (not confirmed, offered for the record)

The mission's own report places the observed hang specifically in the hours right after the
dead-feed recovery (daemon hand-restarted ~16:50 UTC 2026-08-03; my testing here is ~24h later,
2026-08-04, against a feed that has been stable and settled the entire time). Plausible,
unconfirmed candidates for something that could have been true only in that narrower window and
has since self-resolved:
- A `core_sigs`-triggering event (a session/date transition, or the daemon's own restart) landing
  at exactly the wrong moment relative to a client's connect, producing a transient stall that
  requires the specific timing of "client connects during an active rollover/restart" to trigger --
  not reproduced here because the feed has been continuously stable throughout this session's tests.
- A one-time cost from the bar-flood bug above (1027 messages' worth of synchronous work at
  connect-time) being large enough, on a host under load, to push a client past a client-side or
  network-intermediary idle/response timeout -- plausible as a contributing factor even though it
  wouldn't by itself explain a *repeating* loop.

## Part B is still the right hardening regardless

Independent of whether the exact original trigger is ever pinned down, the client currently has
**no way to distinguish "the connection is healthy and the market is just quiet" from "the
connection is dead"** other than the server's own liveness (which the client can't directly see).
Given this mission's own framing -- "today is the FIRST time this path has had a genuinely fresh
feed... a total failure to publish would have passed unnoticed" -- building that distinction now,
and gating on it explicitly (Part C gate (c): a frozen-feed sandbox test), is the correct response
whether or not today's specific incident recurs in exactly this form.

## Part D — recurrence-prevention proposals (NOT applied; presenting for approval)

These touch files outside `webbeta/` (the launch script and, optionally, the daemon's own log
volume and the existing pipeline supervisor) — per this mission's own "THIS MISSION TOUCHES
PRODUCTION" framing, none of the following has been applied. Concrete, ready-to-review proposals
only.

### D1. `launch_book_flow_chart.sh` — log path + rotation

Confirmed root cause location: line 7, `DAEMON_LOG="/tmp/book_flow_cache_daemon_$(date -u
+%Y%m%dT%H%M%SZ).log"` — every relaunch points at `/tmp` (a tmpfs), no rotation, no cap. Proposed:

```bash
# was:
DAEMON_LOG="/tmp/book_flow_cache_daemon_$(date -u +%Y%m%dT%H%M%SZ).log"
# proposed:
DAEMON_LOG_DIR="/home/prabh/OFI_Production/book_flow_chart/logs"
mkdir -p "$DAEMON_LOG_DIR"
DAEMON_LOG="$DAEMON_LOG_DIR/book_flow_cache_daemon_$(date -u +%Y%m%dT%H%M%SZ).log"
# plus a startup-time prune of anything older than 7 days in DAEMON_LOG_DIR, e.g.:
find "$DAEMON_LOG_DIR" -name 'book_flow_cache_daemon_*.log' -mtime +7 -delete
```
(This matches where the daemon's log is ALREADY pointed after today's hand-restart — this change
just makes that permanent across future relaunches, rather than reverting on the next one.)

### D2. `book_flow_cache_daemon.py` — cut the per-cycle full JSON dump

Confirmed: `run_loop()`'s main loop (`--interval-sec 2` default) does
`print(json.dumps(result, sort_keys=True, default=str), flush=True)` on every single cycle --
`result` is the full per-depth stats dict from `ensure_compact_caches()`. At ~2s cadence this is
~43,200 lines/day of a non-trivial JSON blob, which is what turned into the 25G/several-months log
that filled the tmpfs. Proposed (illustrative, would need the real key names from `result`
confirmed against a live run before applying):
```python
# was:
print(json.dumps(result, sort_keys=True, default=str), flush=True)
# proposed:
n_rows = sum(r.get("rows", 0) for r in result.values()) if isinstance(result, dict) else "?"
print(f"[CACHE_DAEMON] cycle ok rows_total={n_rows} depths={list(result.keys())}", flush=True)
```
This is the daemon's own code (more sensitive than the launch script) -- flagged here as a proposal
only, not applied, pending explicit approval.

### D3. systemd user unit for `book_flow_cache_daemon.py`

The single highest-leverage fix: today's daemon didn't hang forever, it **died** (mid-write,
EDQUOT) with no traceback -- a plain `Restart=always` unit would have brought it back within
seconds of that exit, instead of needing a human to notice ~7.5 hours later. Proposed unit
(not installed):
```ini
# ~/.config/systemd/user/book-flow-cache-daemon.service
[Unit]
Description=Book Flow Cache Daemon (NQU6) — SHADOW/RESEARCH ONLY
After=default.target

[Service]
Type=simple
ExecStart=/home/prabh/.venvs/ofi/bin/python /home/prabh/OFI_Production/book_flow_chart/book_flow_cache_daemon.py --symbol NQU6 --date latest --depth all --interval-sec 2
Restart=always
RestartSec=5
StandardOutput=append:/home/prabh/OFI_Production/book_flow_chart/logs/book_flow_cache_daemon.log
StandardError=inherit

[Install]
WantedBy=default.target
```
Pairs with D1/D2 (the log this points at must already be rotated/low-volume) and D4 (a supervisor
check needs something to `systemctl --user restart` — this unit is what makes that possible; today
there is nothing for a supervisor to restart even if it detected the staleness).

### D4. A book-flow entry in `ofi_pipeline_health_supervisor.py`, keyed on cache PARQUET mtime

The existing supervisor (`ofi_pipeline_health_supervisor.py`) already has exactly the right
building blocks: `cme_state()`/`is_cme_holiday()` (market-hours-aware, reusable as-is),
`_age_s()` (mtime-based freshness), `_svc_restart()` (systemctl restart + logged reason),
`_write_status()` (the JSON status file webbeta could read). Proposed addition (illustrative,
matching the file's existing style, not applied): a new check function keyed on
`book_flow_chart/cache/NQU6_<date>_top10.parquet`'s own mtime -- explicitly NOT the heartbeat,
which (per this incident, and the mission's own explicit instruction) kept updating for hours
after the real depth data stopped in a prior related incident:
```python
BOOK_FLOW_STALE_S = 120  # 2 min -- daemon writes every ~2s; anything past this during trading is dead
SVC_BOOK_FLOW = "book-flow-cache-daemon.service"  # from D3, once installed

def _check_book_flow(now: float) -> tuple[bool, float, str]:
    p = _find_latest(pathlib.Path("/home/prabh/OFI_Production/book_flow_chart/cache"),
                      "NQU6_*_top10.parquet")
    if p is None:
        return True, 9999.0, "no book-flow compact cache found"
    age = now - p.stat().st_mtime
    return (age > BOOK_FLOW_STALE_S), age, f"book-flow cache parquet stale {age:.0f}s"
```
wired into `_tick()`/`_check_all()` alongside the existing raw/parser/master/inference checks, with
its own restart-cap/cooldown/warmup via the existing `RestartTracker`, restarting `SVC_BOOK_FLOW`
(D3) rather than anything already covered.

### D5. Standalone watchdog (alternative/complement to D4) + webbeta-side consumption

If a fully independent process (not sharing fate with the main supervisor) is preferred: a small
new script polling the same parquet mtime every ~30s, writing a status file (e.g.
`book_flow_chart/cache/book_flow_watchdog_status.json`: `{"ok": bool, "age_s": float,
"last_checked_utc": str}`) and firing a desktop notification (`notify-send`) + a loud log line on
first crossing `BOOK_FLOW_STALE_S` during confirmed trading hours (reusing `cme_state()`/
`is_cme_holiday()` from the existing supervisor module rather than re-deriving the calendar) --
silent otherwise (breaks, weekends, holidays). `webbeta/server/live_bridge.py` could read that same
status file and distinguish MARKET CLOSED (calm gray) from FEED DOWN (red, only during trading
hours) directly, rather than inferring it solely from `market_age_s`'s raw number -- this is the
piece that would live in `webbeta/` and IS proposed for direct implementation once D3/D4/D5's
upstream pieces exist to read from.
