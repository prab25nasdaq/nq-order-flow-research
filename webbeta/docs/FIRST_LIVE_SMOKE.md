# FIRST_LIVE_SMOKE.md — 5-Minute Checklist for the Real Live Open

All of Part E's live-mode gates were verified against `simulator/daemon_simulator.py` (the market
has been closed for the weekend for most of this mission -- see `webbeta/WEB_SLICE_REPORT.md` and
`book_flow_chart/STEP3_REPORT.md` for the same, earlier-established constraint). This is the
checklist for the first time you actually watch it against the real, live market -- the scheduled
reopen is **Sunday, 17:00 CT** (per `rithmic_scheduler.py`'s own schedule / the `project-ofi-dead-
feed` memory's documented reopen pattern).

Do this once, near/after that reopen, before trusting the live view for anything else.

## Before market open

- [ ] Confirm the cache daemon (PID 2527, `book_flow_cache_daemon.py`) is running: `ps -p 2527`.
      If it's not running or was restarted, note the new PID and confirm it's still the same
      script/args (`--symbol NQU6 --date latest --depth all --interval-sec 2`).
- [ ] Start the web server in live mode: `./run.sh --live` (or with `--lan` if you need LAN
      access -- see `ACCESS.md`). Confirm no errors on startup.
- [ ] Open the printed URL. Confirm the page loads and the toolbar's session dropdown includes
      `live` (selected by default).

## At/after reopen (allow a few minutes for the first real bars to arrive)

- [ ] **LIVE badge is green**, not gray/STALE. If it's still gray a few minutes after the
      scheduled reopen, that's a real signal worth investigating (check the cache daemon and
      `book_flow_cache_daemon.py`'s heartbeat directly) -- don't just wait it out.
- [ ] **Price line is moving**: the dashed horizontal line + its price-bubble label should be
      visibly at/near the current market price and update as bars/forming-ticks arrive (watch for
      at least one visible move over a minute or two).
- [ ] **Current-cell highlight is tracking**: the highlighted cell (white-bordered box) should sit
      at the forming bar's x-position and the price line's y-position, moving together with the
      price line, not stuck at an old position.
- [ ] **Book panel is updating**: bid (green) / ask (red) bars should be present and change over a
      few refresh cycles (not frozen), with a visible gap between best bid and best ask where the
      price line sits. If the book panel shows STALE instead, that's a distinct signal from the
      main LIVE badge -- see `WIRE_SCHEMA.md`'s note on why the two staleness concepts (market vs.
      book) are tracked independently.

## Order-book panel (W2.4 Part F — added for tonight's reopen)

- [ ] **Ladder populates within one cycle of the open** (~2s, the daemon's compact-cache
      cadence): bid (green)/ask (red) rows should appear in the ladder promptly after the mode
      badge flips LIVE, not sit empty for multiple cycles while the rest of the chart updates.
- [ ] **Best bid/ask bracket the current-price line**: the dashed price line should sit inside the
      visible gap between the topmost green (bid) row and the bottommost red (ask) row — if the
      price line is above the ask side or below the bid side, something's crossed or stale.
- [ ] **Spread is sane**: the DOM inset's `spread X.XX` row should read a small, plausible number
      of ticks (a handful of `TICK=0.25` increments for NQ, not zero, not negative, not absurdly
      wide) — read it directly off the inset, don't estimate from the ladder.
- [ ] **Sizes are plausible vs. the desktop's own order-book panel**: open the desktop chart's
      live-latest book side by side; spot-check 3-4 price levels near the touch and confirm the
      browser's ladder/inset sizes are in the same ballpark as the desktop's (exact match isn't
      expected — both read the same v3 checkpoint, but at slightly different poll instants).
- [ ] **Badge flips STALE→LIVE** during this window, not stuck on STALE (no data yet) — confirms
      the market-age heartbeat is genuinely advancing, not just that a connection was accepted.
- [ ] If any of the above looks wrong, cross-check `tests/LIVE_BOOK_WATCH.log` (the automated
      30-minute watchdog run alongside this manual checklist) for the same window — it independently
      flags book_ts stalls, a crossed book, an empty ladder while the chart is otherwise updating,
      and price-line-vs-last-trade divergence, so you don't have to catch a transient by eye alone.

## Side-by-side spot-check vs. the desktop chart

- [ ] Launch the desktop chart (`launch_book_flow_chart.sh` or your usual method) in live-latest
      mode, side by side with the browser tab.
- [ ] Confirm the current price shown in both matches (to the tick).
- [ ] Confirm the most recently sealed bar's index matches in both.
- [ ] Pan/zoom the browser chart a little; confirm the candle shapes/colors in the matching region
      look the same as the desktop's (styling will differ -- canvas vs. pyqtgraph -- but the
      placement/coloring of flow should not).

## If something looks wrong

- Check `/status?token=...` directly (`curl` or browser) for `market_age_s`, `book_age_s`,
  `last_closed_bar_idx`, and `date` -- this is the same data the badge is computed from, and is
  useful for distinguishing "the page is wrong" from "the underlying data genuinely looks stale".
- Compare against the desktop chart, which reads the exact same production caches independently --
  if the desktop also looks wrong, the issue is upstream of this web slice (the daemon/feed, not
  this code); if only the web view looks wrong, it's this slice's bug to chase.
- The cache daemon (PID 2527) must never be restarted or modified as part of debugging this --
  that constraint holds even during a live incident.
