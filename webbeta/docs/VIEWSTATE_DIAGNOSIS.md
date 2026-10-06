# VIEWSTATE_DIAGNOSIS.md — W2.8: view resets + frozen price/book

## Part 0(1) — reconnect loop check

A 3-minute live capture (no user interaction) showed **zero** ws closes/reconnects
(`window.__wsDropCount`, `__wsReconnectCount`, `__wsSuppressedCloseCount` all stayed 0 throughout,
`connState` stayed `"connected"`) — W2.7's keepalive fix holds; there is no reconnect(1005) loop.

## Part 0(2) — the ACTUAL view-reset mechanism: a spurious full-resync loop, not a reconnect loop

Every write to the view transform was traced to three call sites: `autoFitView()` (the only
function that sets `View.xMin/xMax/yMin/yMax` from data), the wheel/drag handlers (set
`View.userSet = true`, correct — user interaction), and `resetViewBtn`/`connectBtn`/
`sessionSelect` (set `View.userSet = false`, correct — explicit user requests for a fresh view).

`autoFitView()` itself early-returns whenever `View.userSet` is true, so a real user pan/zoom
*should* be permanent until Reset View. It wasn't, because **`View.userSet` was being reset to
`false` on every `snapshot` message** (`handleMessage`'s snapshot branch, unconditionally) — correct
for the very first snapshot on initial connect, wrong for any later one.

That still requires something to keep sending snapshots. Direct instrumentation
(`window.__wsGapCount`, `window.__snapshotCount`, `window.__resyncSentCount`) over a real live
connection with **zero** user interaction showed all three incrementing by exactly 1 every 5.0
seconds, forever — precisely `KEEPALIVE_INTERVAL_S` (W2.7 Part B). Mechanism: `ClientQueue.push()`
(`server/live_bridge.py`) tags **every** pushed item with a strictly-incrementing `seq`, keepalives
included. The client's gap-detection code lived only inside the `delta`-type branch of
`handleMessage`, so it only ever advanced `lastSeq` for `delta` messages — a keepalive arriving
between two real deltas consumed a `seq` value the client never accounted for, so the very next
delta's `seq` looked exactly like a dropped message. That triggered `sendControl({type:
"resync"})`, which the server answers with a fresh `snapshot` — which (via the bug above)
unconditionally wiped the view. **W2.7's own keepalive fix, combined with a pre-existing
seq-tracking gap, was the direct cause of this mission's bug (a).** Verified with the fix applied:
zero gaps, zero resyncs, view held bit-for-bit across a real pan for the full 60s tested.

## Part 0(3) — frozen price/book: two different findings, not one bug

**Book is not frozen.** Direct instrumentation of a fresh `BookFlowDataService` (`live_latest=True`)
plus a real browser client both show `book_ts`/`Data.frame.bookTs` advancing every single sample
over a 3-minute window (through the real client-rendering path, post-seq-fix). The user's
perception of a "frozen ladder" is fully explained by the resync-storm above: a full
`resetData()` + rebuild every 5 seconds is disruptive enough to look like nothing is progressing,
even though each rebuild's own snapshot was individually correct.

**A real, separate bug WAS found and fixed regardless**, matching this mission's Part B exactly:
`book_flow_data_service.py`'s own change signature (`core_sigs` = compact+bars, `forming_sig` =
forming cache — read-only desktop source, confirmed by direct reading) **never includes the v3
level-state file**. `LiveBroadcastHub._poll_loop()` only ever checked the book (via
`_fresh_book_depth_checked`) as a side effect of `LiveWireState.diff()`, which only runs when the
service publishes a non-None frame — so a poll where *only* the book file changed (bars/cells
genuinely unchanged) would see the service return `None`, and the book check never ran at all.
Fixed: `_poll_loop()` now calls `wire_state.check_book_only()` directly on every poll where the
service saw nothing new, independent of whether bars/cells advanced.

**Price genuinely does stay constant for extended periods — and this matches the desktop exactly,
verified directly, not a webbeta bug.** `last_price` comes from `book_flow_data_service.
_last_price_from_cells` (the desktop's own exact source field, cited since W2.4 Part C), which
prefers a cell's `close_price` column. Direct inspection of the real, live forming-cache parquet
mid-session: `close_price` was **identical across all 64 rows** of an in-progress bar
(`nunique()==1`) for a continuous 15+ second polling window, while the SAME rows' `mid_price`
column varied meaningfully (29863.75–29873.5) and the touched `price_level`s spanned an even wider
range — `close_price` only changed at the instant the bar rolled to a new `bar_idx`. Running the
desktop's own unmodified `BookFlowDataService(live_latest=True)` class directly (not through any
webbeta code) reproduced the identical pattern: `frame.last_price` held one value
(`29865.0`) for a full 110 real seconds while `forming_cells_len` changed 9 times, then jumped to
`29867.25` at the exact moment the bar sealed. Since `_last_price_from_cells` is what the desktop
itself calls for its own price line, the desktop would show the identical hold-then-jump pattern
given the same data — this is a genuine characteristic of how `close_price` is written for an
in-progress bar in the real production pipeline (write-once-per-forming-bar), not a defect this
mission's code can or should override by silently switching to a different field and diverging
from the desktop's own displayed value.

Given the mission's own gate (b) allows for this ("...or their own age indicator explains why"),
Part B adds a client-side price-age readout (`lastPriceChangeWallMs`, shown once stale past
`PRICE_AGE_SHOW_MS`) rather than silently displaying a number with no context either way. First
set to 30s; the real 10-min gate run (Part C) found several samples where a stale-but-not-yet-
flagged price (10-30s old) produced a book-bracket overshoot with no visible explanation on
screen -- lowered to 8s, comfortably under gate (b)'s own 10s bar, so a price old enough to matter
is always already flagged before that gate would otherwise fire on it.

## Part 0(4) — post-gate live user report: reconnect loop + total freeze, and a defensive fix

After the above was fixed and gated, the user ran their own server on the committed fix (`dd129e4`)
and reported a live reconnect loop with **varying** intervals (40s, 9s, 13s, 40s, 8s, 18s, 11s, 46s,
2s, 2s...) -- distinct from the already-fixed fixed-5s keepalive/resync cycle above -- ending in the
whole UI stuck: cells frozen, book frozen, price line not moving at all. The user's own server
process had exited by the time this was investigated, with no captured traceback (stdout/stderr
went to their terminal, not a file), so the exact root cause could not be pinned directly.

Read `LiveBroadcastHub._poll_loop()` (`server/live_bridge.py`) and found it had **zero exception
handling** around its body -- unlike the upstream `book_flow_data_service.py`'s own poll loop, which
explicitly wraps itself in `except Exception: pass` ("a single bad poll must never kill the
background thread"). Any exception inside `wire_state.diff()` or the newly-added
`wire_state.check_book_only()` call (Part 0(3) above) would silently and permanently kill the
`while True` loop: the server process stays alive (so `ps`/`/status` still look healthy), but no
client is ever pushed another frame again -- an exact match for "stuck completely, price line
doesn't move at all." Separately (unrelated, now resolved on its own): a second cache-daemon
process was found briefly running concurrently with the long-standing one against the same
production cache files, a plausible source of a transient torn-read exception at exactly the wrong
moment, though this was never proven as the trigger.

**Fix applied**: wrapped `_poll_loop()`'s entire body in `try/except Exception: log.exception(...)`,
matching the upstream service's own philosophy exactly -- a single bad poll now logs a full
traceback and the loop continues, instead of dying silently forever.

**Reproduction attempt, honestly reported**: a fresh diagnostic server was run with the fix applied,
driven by a real Playwright browser connection for 5 minutes with 6 forced `ws.close()` calls at
varying intervals (20s, 21s, 26s, 31s, 36s, 40s -- deliberately irregular, echoing the user's
report) to stress the reconnect path. Result: all 6 reconnects succeeded cleanly, the frame version
kept advancing throughout (150 samples, ending at version 796, never stuck), zero exceptions or
tracebacks appeared in the server log, and zero browser console/page errors were seen. **This did
NOT reproduce the freeze** -- varying-interval reconnects alone are not sufficient to trigger it, so
the exact original trigger remains unconfirmed. The exception-handling fix is still correct and
warranted on its own merits (it closes a real, previously-unprotected single point of total failure
that has no legitimate reason to exist, and it directly matches the upstream service's own
documented defensive pattern) -- but it should be reported to the user as a hardening fix made from
a strong hypothesis, not as a confirmed root-cause fix, since no traceback was ever captured to
prove which exception (if any) was actually involved. If it recurs, the new `log.exception(...)`
call will now capture the exact traceback for a definitive diagnosis.
