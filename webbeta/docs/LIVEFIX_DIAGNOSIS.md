# LIVEFIX_DIAGNOSIS.md — W2.6: frozen live price/book, ~600-point mismatch

## Evidence gathered (live, market open, 2026-08-03 ~05:15-05:30 UTC)

1. **Desktop/ground-truth read** (direct, read-only, independent of any webbeta code): the real
   production checkpoint `book_flow_chart/cache/state/NQU6_2026-08-02_v3_level_state.pkl` — `symbol=
   NQU6`, `session_date=2026-08-02`, `updated_utc` 1.0s old at read time — has `best_bid=28640.50`,
   `best_ask=28641.00`. This is the SAME price region as the chart (28630-28660). **The raw file on
   disk is correct and fresh.**

2. **Fresh service instance** (a brand-new `BookFlowDataService(..., live_latest=True,
   previous_sessions=1)`, identical construction to `live_bridge.py`'s, polled for 60s): every frame
   is internally consistent and correct — `date=2026-08-02`, `last_price` and `best_bid`/`best_ask`
   both track the real file, `book_ts` advances every ~2.3s matching the daemon's cadence. **A fresh
   process exhibits no bug at all.**

3. **The frozen values reported by tester zero, decomposed against the real historical bars file**:
   - `last_price=28628.75` matches bar **36500**'s real close at **2026-08-03 05:15:42 UTC** —
     recent, plausible, from TODAY's session. The bars/price pipeline was not badly stale.
   - The book's `28026.00–28033.00` / spread `1.25` range matches **NOTHING in today's
     (2026-08-02) or Friday's (2026-07-30) session** — it matches **2026-07-29's** session
     (bar_index ~34153-34562, timestamps 2026-07-30 13:46-15:34 UTC). That date is **3+ real
     calendar days and TWO session rollovers stale** relative to the live session under test.

   The two frozen values are NOT from the same moment — `last_price` was fresh, the book was
   reading a completely different, much older session. This rules out a single "everything froze
   at once" explanation and points at the BOOK path specifically.

## The two mechanisms, answered separately (as Part 0 requires)

**Wrong file (symbol/contract/date)?** No. Only `NQU6_*` files exist anywhere in
`book_flow_chart/cache/` — no `NQZ6` file exists, so the U6→Z6 rollover is not yet in play (U6
expires mid-September; early August is normal U6 territory). The path-resolution inputs
(`symbol="NQU6"`, `active_date` resolved from the heartbeat) are demonstrably correct elsewhere in
the SAME frame (bars/cells used the identical `active_date` and were fresh) — so this is not a
wrong-symbol/wrong-date-*computed* bug.

**Right file, but never re-read (cached path/handle) — YES, this is the mechanism.**
`book_flow_data_service.py`'s `_get_book_depth()` (read-only desktop source, never modified by any
webbeta mission):
```python
def _get_book_depth(self, symbol, date):
    sig = _file_sig(level_cache.v3_state_path(symbol, date))
    if sig != self._book_sig:
        self._book_sig = sig
        self._cached_book = _load_book_depth(symbol, date)
    return self._cached_book
```
`self._book_sig` is written on the line *before* `_load_book_depth()` runs. `_run()`'s poll loop
wraps the entire per-poll frame build in a bare `except Exception: pass` ("a single bad poll must
never kill the background thread" — by design, and correctly so for bars/cells). If
`_load_book_depth()` (or the pickle read inside it) ever fails or raises on a given poll —
plausible at exactly a session-rollover instant, when a brand-new date's checkpoint file may not
yet be fully written the first time the service reaches for it — `self._book_sig` has *already*
been updated to that poll's (mtime, size) signature before the failure. On the next poll, if the
checkpoint file's (mtime, size) has not changed again by then, the sig comparison reports "no
change" and `_load_book_depth()` is never retried — `self._cached_book` stays at whatever it was
*before* this session even started (a previous date's book), indefinitely, until the file's
(mtime, size) signature happens to change to something new again. This is a real, structural gap
in the cache-invalidation logic: the cache key is a bare file signature with no cross-check against
`(symbol, date)` identity, and a single transient failure at the wrong instant can silently pin it
to an arbitrarily old value. This exact process could not be reproduced byte-for-byte in a fresh
60s run (the process that showed the bug no longer exists — it was live_bridge's server, running
since 22:06 UTC), but the mechanism is fully supported by: (a) the code path's structure, (b) the
book being stale by *exactly* a stale-session's worth of price levels while bars were fresh, and
(c) `book_flow_data_service.py` never validating the loaded pickle's own `symbol`/`session_date`
fields against what was requested.

**"Frozen-ness" is the same root cause, not a separate one.** Because `_cached_book` never gets
reassigned once poisoned, `frame.book_ts` (read from the cached dict) is *also* frozen at the same
old value every poll — which is exactly "depth column frozen" from the report. `last_price` itself
is NOT computed from the book at all (it's `_last_price_from_cells` on `forming_cells`/
`sealed_cells` — a completely separate pipeline) — the report's "price line frozen" symptom is a
*second*, independent question: is `webbeta`'s own wire layer (`live_bridge.py`'s `LiveWireState.
diff()`) correctly forwarding a fresh `last_price` on every poll where the forming bar's cell count
doesn't change (a bar can accumulate flow at already-touched price levels without a new row)? This
gates `forming_update` on `cur_forming_len != self.forming_cells_len`, so `last_price` can go
several polls without a fresh push even while the market is genuinely printing new prices at
already-touched levels — a narrower, secondary staleness window worth closing defensively even
though it did not explain the 600-point book gap.

## Fix approach (Part A, not yet applied as of this document)

`book_flow_data_service.py` is read-only (desktop source — never modified by any webbeta mission).
The fix lives entirely in `webbeta/server/live_bridge.py`:
- Stop trusting the service's own `frame.book_prices`/`book_bid_sizes`/`book_ask_sizes`/`book_ts`
  (which ride through the poisoned cache above). Read the v3 state checkpoint **independently, from
  scratch, every poll**, via the same read-only `level_cache.load_v3_state(symbol, date)` the
  desktop uses — no caching of any kind in webbeta's own code — and validate the loaded pickle's
  own `symbol`/`session_date` fields equal the bar stream's `frame.symbol`/`frame.date` before using
  it at all.
- Sanity guard: if the resulting best bid/ask is more than N ticks (default ~200) from
  `frame.last_price`, treat the book as invalid for that poll — do not forward it; the client shows
  a distinct BOOK MISMATCH state instead of a wrong book.
- Liveness: track book_ts advancement server-side; if it stops advancing past a threshold, flip the
  book to STALE independent of the overall LIVE badge.

## Addendum — Part B gate (a) "book brackets price" finding (post-fix, real feed)

Two live runs against the real feed after the fix (30min: 754 samples; a clean 12min re-run: 383
samples, after discarding an intermediate run contaminated by an operator mistake — the server was
restarted mid-run, producing one spurious 59-tick reading that lines up exactly with that restart)
both show the SAME pattern: co-movement (price vs. forming cell) 100% compliant, and the
independent checkpoint cross-check 0 mismatches across 838 combined samples — the book is
*always* exactly what the real production checkpoint says, at every sampled instant. The targeted
bug (a book frozen on an arbitrarily old, wrong-identity session) is conclusively fixed.

The mission's literal "book brackets the price line >=99%" sub-metric is NOT met (59-85%
depending on the window), but the violations are NOT the bug this mission targets. Overshoot
magnitude is bimodal: a p50 of 1-3 ticks (ordinary, expected cross-poll skew, unrelated to this
mission -- see W2.4 Part F's live smoke test, which found the identical small overshoot before
this mission existed), plus a handful of larger excursions up to ~98 ticks (24.5 points) that
line up with genuinely volatile trading: this session's own recent bars show true ranges of
17-31 points each (checked directly against the real bars file). `last_price` is sourced from the
forming-cache (~350ms write cadence); the book is sourced from the v3 state checkpoint (~2s write
cadence). During a fast, wide-range bar, the last-trade tick can legitimately move 10-25 points
within one v3-checkpoint cycle while the resting book (which the checkpoint snapshots) has not
yet been requoted that far -- a real, physical consequence of combining two independently-cadenced
real market data feeds, not a caching bug: the (b)/(c) cross-check proves the book itself is never
stale or wrong at the instant it's read.

This is NOT fixable by changing the read path (the book only advances when the daemon writes it,
~2s, by design) without either slowing the price line down to match (a regression against W2.5's
own atomicity/responsiveness requirements) or fabricating an interpolated book between real
snapshots (inventing data, which every prior mission in this project has explicitly refused to do
for either the chart or the book panel). Reported here transparently rather than adjusting the
gate's target after the fact to force a PASS.
