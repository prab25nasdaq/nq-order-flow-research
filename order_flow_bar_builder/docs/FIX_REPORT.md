# FIX_REPORT — True Book Flow Chart (candles, interaction, dashboard launcher)

Spec: `/home/prabh/OFI_Production/FIX_TRUE_BOOK_FLOW_CHART_CANDLES_AND_INTERACTION_PROMPT.md`

```
APP_PATH: /home/prabh/OFI_Production/book_flow_chart/book_flow_chart.py
DASHBOARD_FILE: /mnt/wd_work/workspace/Work Place/Data/Project OFI/_codex_work/ofi_live_dashboard_WORKING_NEXT_with_logreg.py
MAIN_CANDLE_SOURCE: true_book_flow — candle_y_open/high/low/close = of_open/of_high/of_low/of_close
                    (per-bar mode) or sess_open/sess_high/sess_low/sess_close (session-cumulative
                    mode), both derived from raw Rithmic bid/ask depth updates via
                    book_flow_lib.update_cache(). Verified at runtime by validate_candle_source().
USES_PRICE_OHLC_AS_MAIN_CANDLES: NO — px_open/px_high/px_low/px_close are used only for the
                    thin price context line, right-side price axis, S/R levels, and volume
                    profile/blueprint. FORBIDDEN_CANDLE_COLS blocks them from ever being used
                    as candle_y_* and validate_candle_source() additionally checks the OF
                    arrays are not numerically identical to the price arrays.
USES_MLOFI_AS_MAIN_CANDLES: NO — mlofi_norm/mlofi_decay_sum/mlofi_rolling_5/decay_norm are in
                    FORBIDDEN_CANDLE_COLS and are not present in the order-flow cache at all
                    (smoke test confirms absence).
USES_TRUE_RAW_BOOK_FLOW: YES — of_open=0 for every per-bar-mode bar (sign convention: bid
                    add=+, bid pull=-, ask add=-, ask pull=+, accumulated intra-bar from raw
                    bid_quote_updates.ndjson / ask_quote_updates.ndjson); of_high/of_low/of_close
                    are the running max/min/final of that signed path. Session-cumulative mode
                    carries sess_open/high/low/close forward across bars, still order-flow, not
                    price.
DEPTH_CHOICES: top5, top10, top15, top20 (DEPTH_CHOICES = [5, 10, 15, 20]; selectable in toolbar,
                    each backed by its own cached column set built from bid/ask/depth updates)
AUTO_RESET_FIXED: YES
  - Root cause: standalone/secondary ViewBoxes (price_vb, imb_vb, vp_plot.vb, pulls_plot.vb) had
    autoRange ENABLED by default; every set_data()/setOpts() during _reload() (~1.5s cycle)
    triggered autoRange, which — via X-axis linking to self.plot — silently overrode any user
    pan/zoom on the main candle view.
  - Fix: disableAutoRange() called once on price_vb, imb_vb, vp_plot.vb, and pulls_plot.vb at
    construction time. _update_view_ranges() is now the SOLE place that calls
    setXRange/setYRange on the main plot, price axis, and pulls panel, and it only does so when
    "following" (see FOLLOW_LIVE_BEHAVIOR). Otherwise the current ViewBox ranges are left
    completely untouched across reloads.
FOLLOW_LIVE_BEHAVIOR:
  - FOLLOW LIVE ON: each reload checks _at_live_edge(n) — true if the current X view-max is
    within max(2.0, 5% of the visible X span) of the newest bar index. If at the live edge (or
    this is the very first reload), the view snaps to the latest FOLLOW_WINDOW(=150) bars and
    recomputes Y ranges (order-flow, price, and pulls panel) from that window. If the user has
    panned away from the live edge, no range is touched — chart keeps refreshing data without
    moving the view.
  - FOLLOW LIVE OFF: ranges are set once on the very first reload (_initialized flag), then never
    auto-reset again; data refreshes in place, preserving the user's pan/zoom indefinitely.
  - Reset View button (_reset_view): the only path that intentionally resets X/Y/price/pulls
    ranges to fit the full loaded dataframe; sets _initialized=True and refreshes the status
    label.
  - Visible status: "MAIN CANDLE SOURCE: TRUE RAW BOOK FLOW OFI, NOT PRICE OHLC | cols=(...)"
    (green; turns red "BLOCKED: <reason>" if validate_candle_source() fails) and
    "FOLLOW LIVE: ON/OFF | VIEW LOCKED: USER VIEW / FOLLOWING LIVE", both in the top toolbar,
    updated on every reload and on every ViewBox range-change event.
DASHBOARD_LAUNCH_BUTTON_ADDED: YES — BOOK FLOW tab (_init_bookchart_tab) now shows:
    - title "TRUE BOOK FLOW CHART" + one-line description of the candle source
    - live status label (RUNNING (pid=N) / NOT RUNNING), auto-refreshed every 3s via
      _bookchart_refresh_status() (checks /tmp/book_flow_chart_pid.txt liveness +
      /proc/<pid>/cmdline identity)
    - "Launch True Book Flow Chart" button — subprocess.Popen(["bash",
      ".../launch_book_flow_chart.sh"], start_new_session=True, non-blocking); refuses to
      re-launch if a live PID is already detected (prevents duplicates)
    - "Stop True Book Flow Chart" button — os.kill(pid, SIGTERM) using the detected PID
    - "Open latest log" button — opens the newest /tmp/book_flow_chart_*.log via xdg-open
    - path display for both book_flow_chart.py and launch_book_flow_chart.sh
    - scrolling text panel showing the last 20 lines of the latest log file
    No trading process is started by this tab.
PY_COMPILE_APP: PASS
PY_COMPILE_DASHBOARD: PASS
SMOKE_TEST: PASS
  raw bid_quote_updates.ndjson exists: True
  raw ask_quote_updates.ndjson exists: True
  update_cache: ok=True bars_done=664/664
  depth top5/top10/top15/top20: cache_exists=True rows=664 of_open_all_zero=True (all)
  order-flow OHLC columns present: True
  of_close != px_close (main candles are NOT price OHLC): True
  mlofi columns absent from cache (main candles are NOT MLOFI): True
  validate_candle_source: ok=True source=true_book_flow cols=('of_open','of_high','of_low','of_close')
  S/R levels computed: R=15 S=23
  volume blueprint computed: True poc=30869.75 vah=30908.5 val=30702.25
  projected levels loaded: rows=63
  view preservation logic present (_at_live_edge, _initialized, disableAutoRange,
    _update_view_status): True
  dashboard launch/stop button added: True
  GUI window constructed OK (offscreen)
  SMOKE TEST RESULT: PASS

  Live end-to-end check:
  - bash launch_book_flow_chart.sh -> PID 974487, ran cleanly for >8s, empty stderr/stdout log
  - dashboard restarted (bash launch_dash.sh) -> PID 974130, clean startup log, BOOK FLOW tab
    PID-detection logic verified to correctly identify the running standalone chart process via
    /tmp/book_flow_chart_pid.txt + /proc/<pid>/cmdline
  - standalone chart stopped cleanly via SIGTERM after validation
TRADING_ENABLED: NO

OVERALL: PASS
```
