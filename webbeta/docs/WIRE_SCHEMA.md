# WIRE_SCHEMA.md — Book Flow Chart Web Slice (W1 replay + W2-live)

SHADOW / RESEARCH ONLY. This is the contract between the FastAPI replay backend
(`webbeta/server/`) and the canvas frontend (`webbeta/static/`). It is a JSON serialization of
`book_flow_chart/book_flow_data_service.py`'s `ChartFrame` — trimmed to exactly what the browser
renders (no OHLC-candle fields, no Book Pressure/Pulls flow-delta columns; those are out of scope
for W1's canvas per the mission brief) and reshaped from row-oriented DataFrames into **column-
oriented parallel arrays**, never per-row objects. This is also the contract the future Model
Probs and Z-Scores panels will extend — new fields should be added as new top-level keys, never by
changing the shape of an existing one.

## Transport

One WebSocket per client, `/ws?token=...`. All messages are JSON text frames.
`permessage-deflate` is enabled on the server (see `server/app.py`); the <50KB/s steady-state
budget (Part A) is measured with compression on, matching how a real browser client negotiates it.

Client → server control messages:
```json
{"type": "speed", "value": 1}     // 1, 8, or 0 (0 = pause) -- ignored in live mode (always real-time)
{"type": "session", "name": "clean_2026-07-30"}   // switch session; "live" is a reserved name
                                                    // (see "Live mode" below), any other value
                                                    // must be one of `hello`'s available_sessions
{"type": "resync"}   // W2-live: request a fresh snapshot (see "Live mode: gap detection" below)
```

Server → client messages, in the order a client actually receives them:
1. `hello` — once, immediately on connect.
2. `snapshot` — once, immediately after `hello` (and again any time the session/date changes,
   including a live session-boundary rollover -- see "Live mode" below).
3. `delta` — zero or more, at the recorded cadence × the current speed multiplier (replay) or
   real-time as the underlying service produces them (live).

## `hello`

```json
{"type": "hello", "protocol_version": 1, "session": "clean_2026-07-30",
 "available_sessions": ["clean_2026-07-30", "clean_2026-07-29", "deadfeed_2026-07-08"]}
```

## `snapshot`

Full state as of `version`. Sent once per session start (not per delta) — the client builds its
entire in-memory model from this message, then applies `delta` messages on top of it.

```json
{
  "type": "snapshot",
  "version": 1042,
  "symbol": "NQU6", "date": "2026-07-30", "depth": 10,
  "last_closed_bar_idx": 35124,
  "last_price": 28290.0,
  "book_ts": null,
  "bars": {
    "bar_index":      [35075, 35076, ...],
    "bar_pos":        [0, 1, ...],
    "px_close":       [28291.25, 28290.75, ...],
    "bar_start_ts_ns":[1785... , ...]
  },
  "cells": {
    "bar_idx":      [35075, 35075, 35075, ...],
    "price_level":  [28280.0, 28280.25, 28280.5, ...],
    "signed_flow":  [-1.0, 0.0, 3.0, ...],
    "abs_flow":     [1.0, 4.0, 3.0, ...],
    "bar_state":    ["C", "C", "C", ...]
  },
  "forming_bar": {"bar_index": 35125, "bar_pos": 50, "px_close": 28291.0, "bar_start_ts_ns": 1785...} ,
  "forming_cells": { "bar_idx": [...], "price_level": [...], "signed_flow": [...], "abs_flow": [...], "bar_state": ["F", ...] },
  "book": null
}
```

`forming_bar`/`forming_cells`/`book` are `null` when not applicable (no forming bar active, or no
book data available/trusted — see "Book data availability" below). `bar_state` is the single
character `"C"` (closed/sealed) or `"F"` (forming) — kept as a short string, not a bit-packed
enum, since `permessage-deflate` already collapses the repetition to a handful of bits per symbol
and a string is trivial to consume from JS with no lookup table needed.

## `delta` — three kinds, `"kind"` discriminates

**`bar_roll`** — one bar has just sealed; append it, and (if present) replace the forming target:
```json
{"type": "delta", "kind": "bar_roll", "version": 1043,
 "sealed_bar": {"bar_index": 35125, "bar_pos": 50, "px_close": 28291.0, "bar_start_ts_ns": 1785...},
 "sealed_cells": {"bar_idx": [...], "price_level": [...], "signed_flow": [...], "abs_flow": [...], "bar_state": ["C", ...]},
 "forming_bar": {"bar_index": 35126, ...} | null,
 "forming_cells": {...} | null,
 "last_closed_bar_idx": 35125}
```
Client behavior: append `sealed_bar`/`sealed_cells` to its bar/cell arrays (never re-send history),
replace whatever forming state it was holding with the new one (or clear it if `null`).

**`forming_update`** — the current forming bar's cells changed (more of it has "arrived"); replace,
never append:
```json
{"type": "delta", "kind": "forming_update", "version": 1044,
 "forming_bar": {...}, "forming_cells": {...}, "last_price": 28291.25}
```

**`book_update`** — the order-book snapshot changed; replace in place:
```json
{"type": "delta", "kind": "book_update", "version": 1045,
 "book": {"prices": [...], "bid_sizes": [...], "ask_sizes": [...]} , "book_ts": 1785540...}
```
`book` may be `null` with a fresh `book_ts` — this never happens in practice (an empty book has no
useful `book_ts` either) but the shape allows it. A client that hasn't received a `book_update` (or
whose last `book_ts` is more than `BOOK_STALE_SECS=10.0` — mirroring
`book_flow_data_service.BOOK_STALE_SECS` exactly — older than "now") must render the book panel
greyed out with the STALE badge, exactly like the desktop app (`_update_order_book_panel`'s
`is_stale` check) — never render a stale book as live.

## Live mode (added in W2-live)

`session: "live"` is a reserved name (alongside the replay session names) in `hello`'s
`available_sessions` and the `{"type":"session"}` control message. In live mode, `snapshot`/
`delta` messages are produced by `server/live_bridge.py`'s `LiveBroadcastHub`, which runs a real
`BookFlowDataService` (`live_latest=True`) in-process, read-only against production cache paths
(the desktop chart's own access pattern) — see `book_flow_chart/book_flow_data_service.py` for
that service's own contract. Unlike replay, live `book_update` deltas are real (or, against the
Part D simulator, schema-real with synthetic content — see `simulator/daemon_simulator.py`), not
subject to the "book data availability" limitation below, which is specific to *historical replay*
sessions. A live snapshot is filtered to real trading-session boundaries
(`server/session_boundaries.py`, mirroring `master_live/cme_schedule.py`'s 22:00 UTC session-start
convention), selected by the client's `{"type":"lookback","n_sessions":N}` control message (N = 1-5;
default `DEFAULT_N_SESSIONS=2`) rather than a fixed bar count — the snapshot's own `n_sessions` and
`session_boundaries` fields report exactly what was used, and `bars.bar_start_ts_ns` carries the
real per-bar timestamp the client renders on the x-axis. `SNAPSHOT_MAX_BARS=8000` remains only as a
defensive ceiling on top of the session filter (a real measured 5-session snapshot has run ~4,141
bars / ~258k cell rows), not the mechanism that decides how many bars ship.

**Gap detection.** Every message delivered through the live broadcast hub carries a per-client,
strictly-monotonic `seq` (assigned by `ClientQueue.push()`), deliberately separate from `version`:
a single `ChartFrame` can produce more than one wire message (e.g. `bar_roll` + `forming_update`)
sharing one `version`, so `version` alone can't distinguish that from a dropped message; `seq`
can. The hub's per-client queue is bounded (`MAXLEN=8`) and coalesces (drops the *oldest* pending
message on overflow) so a slow client can never grow server memory or stall the broadcast to other
clients. The client checks `seq` on every `delta`; a gap (`seq != lastSeq + 1`) sends
`{"type":"resync"}` and does *not* apply that delta (its state is known incomplete) — the server
responds with a fresh `snapshot`, which resets the client's `seq` tracking. Replay-mode deltas
never carry `seq` (nothing about that path can drop a message), so no gap check applies there.

**Market vs. book staleness are two independent concepts.** `GET /status` (token-gated) reports
`market_age_s` — the age of the daemon's own heartbeat-reported `latest_closed_timestamp_utc`,
**not** "time since the last WS message" — because the book checkpoint (and therefore
`book_update` traffic) keeps being rewritten every ~2s daemon cycle even while the underlying
market is genuinely stale (confirmed directly against real weekend-closure production data), so
message arrival alone would under-detect market staleness. This drives the frontend's `#modeBadge`
(LIVE / STALE-with-age / REPLAY), which is separate from the book panel's own `BOOK_STALE_SECS`
badge covered above. `market_age_s`'s threshold (`LIVE_STALE_SECS`, default 600s) is configurable
via `WEBBETA_LIVE_STALE_SECS` for faster gate testing against the simulator.

## Book data availability for REPLAY sessions (an honest limitation, not a bug)

Order-book **resting depth** is not historically recorded anywhere in this system — the source
checkpoint (`build_book_flow_level_cache.v3_state_path`, see `book_flow_chart/STEP3_REPORT.md`
Part E) is overwritten in place every ~2s and only ever reflects the *current* book, never a
history. This is true for the desktop app too: replaying a past session date there also shows the
book panel as permanently STALE, for the identical reason. Consequently:
- The 3 exported replay sessions (`export_sessions.py`) carry `book_ts: null` throughout — no
  `book_update` deltas are ever emitted for them. This is not a placeholder to fill in later; it
  reflects a real, permanent constraint of what data exists for a past date. Live mode does not
  have this limitation (see above).
- The `book_update` wire path itself (server encoding, client decoding, stale-badge behavior) is
  exercised by a dedicated synthetic fixture (`webbeta/tests/`), not by the 3 real replay
  sessions — kept clearly separate so nobody mistakes synthetic book data for a real historical
  capture.

## Forming-bar replay technique (also documented here, not hidden)

A sealed historical session has no recorded intra-bar forming ticks either — only each bar's final,
complete cell set. To still exercise (and visually demonstrate) the `forming_update` code path with
*real* data, the export tool treats the session's *last* bar before each `bar_roll` as "forming"
for a few ticks, revealing a growing subset of that bar's real, final `price_level`/`signed_flow`/
`abs_flow` values before the `bar_roll` delta reveals the rest and seals it. This is a replay
*pacing* technique applied to real data, not fabricated data — documented here so it's never
mistaken for a genuine captured forming-tick history.

## Compact-array budget (Part A measurement)

Measured via `export_sessions.py --report-sizes` (gzip-compressed, matching `permessage-deflate`'s
effect): see `WEB_SLICE_REPORT.md` for the actual numbers against the <50KB/s @ 1x budget.
