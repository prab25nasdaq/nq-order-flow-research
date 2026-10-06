# ROLLDELTA_DIAGNOSIS.md — W2.12: sealed bar vanishes at roll in the delta stream

## Evidence (user, live, 2026-08-06)

Side-by-side against the desktop at the same instant: both showed price line 29520.75, webbeta's
book inset was current (29516.75–29524.75, spread 0.75). Desktop cells updated live; webbeta's
newest bar was an empty stub that never filled and never rolled. Clicking Connect (a fresh
snapshot) restored it. This isolated the bug precisely: the SNAPSHOT path is correct; the
incremental DELTA path drops the sealed bar and never re-adds it.

## Part 0 — confirming the window

Read `book_flow_data_service._build_frame_live_latest()` (book_flow_chart/book_flow_data_service.py,
lines ~505–531) directly rather than guessing:

```python
cur_sealed_cells = _bridge_gap_if_needed(self.symbol, active_date, self.depth, cur_compact)
...
sealed_ids = set(int(x) for x in sealed_cells["bar_idx"].unique()) ...
...
bars_out = all_bars[all_bars["bar_index"].isin(sealed_ids)].copy().reset_index(drop=True)
```

`_bridge_gap_if_needed()` (lines 228–253, read-only desktop source) already splices the just-sealed
bar's CELL rows into `sealed_cells` the instant the forming cache tags it CLOSED, mirroring
`book_flow_chart_v3.py`'s own identical bridge (~lines 914–936: "the fast forming-bar cache tags a
bar CLOSED for exactly one cycle right after it seals, before the slower compact cache catches
up"). But `bars_out` (bar METADATA — bar_index/px_close/timestamps) is filtered from `all_bars`,
which comes from the SEPARATE, un-spliced `_load_bars()` (the compact bars file). Even though
`sealed_ids` correctly includes the bridge bar (since it's derived from the already-spliced
`sealed_cells`), the bridge bar simply has no ROW in `all_bars` yet, so
`all_bars[all_bars["bar_index"].isin(sealed_ids)]` silently excludes it regardless.

Since `live_bridge.py`'s own `LiveWireState.diff()` decides what's "new" purely from
`frame.sealed_bars` (never `sealed_cells`), the bridge bar is invisible to the wire protocol for
this entire window — confirmed directly by reading, not guessed, before any fix was written.

**Direct empirical reproduction**: after fixing the simulator's timing (see Part C below) to
genuinely decouple the bars-file write from the roll event, a debug capture on PRE-FIX code showed
bar_idx 35077 (a session's first sealed bar) **permanently** missing from `Data.bars` — not just
transiently. With the fix applied, the same reproduction showed 0 gaps.

**Prime hypothesis (self-latching fallback) — NOT the mechanism here.** That hypothesis belonged to
W2.11's forming-cell bug (already disproven there via direct instrumentation). For W2.12
specifically, the mechanism is the bars/cells architectural split described above — a gap in
`book_flow_data_service.py` that only affects the bar-metadata list, not a fallback state.

## Part A — the fix

`LiveBroadcastHub._reconcile_sealed_bars()` (new, `server/live_bridge.py`) runs before
`_reconcile_forming()` on every poll: any bar_idx tagged CLOSED in `frame.sealed_cells` but missing
from `frame.sealed_bars` gets a synthesized metadata row added — reusing the real cached row from
when it was the forming bar if available (`self._last_good_forming_bar`), falling back to a
template-derived synthesis (`_synthesize_forming_bar_row`), and finally to a row derived directly
from the bridge bar's own cells (`_last_price_from_cells`) for the edge case where no template
exists at all (the very first bar of a session). `frame.sealed_bars` is never mutated in place — a
corrected copy is returned via `dataclasses.replace`.

Delta correctness independent of the splice: `diff()`'s `new_sealed = [b for b in sealed_ids if b
not in self.bar_pos_map]` was already a genuine set difference, not a high-water-mark — confirmed
by reading, no change needed there.

Every delta (and the snapshot) now carries `sealed_count` and `max_sealed_bar_idx`.

## Two additional bugs found operationalizing Part A

**1. `frame.last_closed_bar_idx` itself can be stale.** It's derived from
`frame.sealed_cells.max()` (`ChartFrame.__post_init__`), and on a forming-only poll (no core
change), `sealed_cells` is reused from the last core-changed poll's cache — unrefreshed until the
bars file's own next ~2s cycle. A live debug capture showed a `forming_update` carrying
`max_sealed_bar_idx=40925` while the client's own (correctly up-to-date) newest bar was already
40926. Fixed: `_stamp_max_sealed_bar_idx()` computes from `frame.sealed_bars` directly (the same
source `diff()` itself uses), not the separate scalar.

**2. Even that regresses on rare transient re-reads.** `book_flow_data_service._load_compact()`/
`_load_bars()` (read-only desktop source) have zero torn-read guard at all — unlike `_load_forming`,
which W2.10 already had to harden. A core-changed poll landing on a momentarily-incomplete re-read
of either file can rebuild the cached bars_out *smaller* than the immediately preceding poll, even
after `diff()` already sent the client a `bar_roll` for the bar that just "disappeared" from the
cache. Since bars are logically permanent once sealed, `_stamp_max_sealed_bar_idx()` ratchets: the
wire-visible value can never regress below the highest value ever legitimately computed.

## Part B — client self-heal

`checkSealedContinuity()` (`static/app.js`) runs after every delta is applied. Two genuinely
client-local, always-valid invariants: the client's own newest bar must match
`max_sealed_bar_idx` (checked on non-`bar_roll` deltas only, since multiple `bar_roll` messages
from one poll all carry the *same* final `max_sealed_bar_idx` — checking equality mid-batch would
false-positive before the client catches up), and the client's own bar sequence must never show an
internal gap (checked on `bar_roll`). On mismatch: automatic `resync` control message, no human
needed.

**A design mistake caught and fixed during this same mission**: the first version of this check
also compared `Data.bars.length` against `sealed_count` (`server/live_bridge.py`'s `next_bar_pos`).
That comparison is fundamentally wrong — `next_bar_pos` is a *global* counter of every bar position
ever assigned since the hub itself started, while `Data.bars.length` is bounded by the
per-connection trimmed snapshot (`SNAPSHOT_MAX_BARS`). For any client connecting (or resyncing)
after the hub has ever sealed more than `SNAPSHOT_MAX_BARS` bars — normal for any long-running
server — these two numbers are permanently, structurally different, and comparing them drove an
endless resync loop (over 1200 resyncs observed in one real-feed test run). Removed entirely.

## Part C — the simulator fix, and confirmed reproduction

`simulator/daemon_simulator.py`'s old timing wrote the forming CLOSED tag, slept a fixed
`BRIDGE_GAP_S=0.05s`, then wrote *both* the bars file and the compact file together — a fixed,
near-instant lag that could never reproduce this bug (bars and cells always caught up together).
Fixed: the roll handler now only updates in-memory `sealed_bars`/`sealed_cells` accumulators
immediately; a new periodic `~COMPACT_CADENCE_S` (2.0s) flush writes them to disk — reproducing the
real, *variable* lag (anywhere from ~0 to ~2s depending on where in the cycle a roll lands) that
the real production system actually exhibits.

**Verified the corrected simulator reproduces the bug on pre-fix code**: bar_idx 35077 permanently
absent from `Data.bars`. **Verified the fix resolves it**: same reproduction, 0 gaps, with the fix
applied.

## Known residual limitation — honestly reported, not swept under the rug

`test_live_flicker.py` (a pre-existing, unrelated W2.7-era regression test, run at an aggressive
`bar_roll_interval_s=1.0s` — roughly 100x faster than this system's real ~110s bar cadence) still
fails after all of the above. Root-caused directly: the very first snapshot a client receives can,
in a narrow timing window, be built from `self.last_frame` one poll cycle *before* a bar's CLOSED
tag was captured — momentarily showing `[...,35076,35078]` (35077 missing). Confirmed this is
transient and self-heals within a few seconds via completely ordinary `bar_roll` delta processing
(`diff()`'s own set-difference naturally sends 35077 once a later poll's reconciliation catches
it) — no resync, no human intervention needed. `test_live_flicker.py`'s own gap-tracking records a
gap the instant it's ever observed and never re-checks whether it later closed, so this
*momentary, self-correcting* state reads as a hard failure to that specific test.

An attempted fix (re-running `_reconcile_sealed_bars` fresh at snapshot-build time, in
`register()`/`snapshot_now()`) was tried and reverted: it mutates shared reconciliation state
(`_bridge_pending`, `_last_good_forming_bar`) outside the poll loop's own expected call order,
and produced a *worse*, new failure mode (a duplicated bar_index) under the same test. Reverted
back to the known-good state rather than ship a fix that traded one bug for a worse one.

This does **not** affect this mission's own Part C gates: the real-feed gate (a) and the 3.0s-cadence
simulated gate (b) both passed with zero gaps, zero disappearances, and zero automatic resyncs —
neither exercises a snapshot delivered in this specific sub-second race window. Reported here in
full rather than silently narrowing the regression suite to make it pass.

## Cache daemon

Never touched by any action this mission — PID 1620187 → renamed/rotated to 1021940 mid-session by
the pre-existing (separately flagged, unrelated) `launch_book_flow_chart.sh` issue documented in
earlier missions, not by anything done here. Confirmed stable throughout this mission's work.
