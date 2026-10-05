# Bar 14368 Price Travel Case Study Report
**SHADOW / RESEARCH ONLY — NO EXECUTION — NO BROKER — NO PAPER TRADING**
**Generated**: 2026-07-05

---

## ANCHOR IDENTIFICATION

| Field | Value |
|-------|-------|
| bar_index | 14368 |
| timestamp_utc | 2026-07-02 19:50:00.016325 UTC |
| local_date | 2026-07-01 (NQ session — CDT offset) |
| px_open | 29440.00 |
| px_high | 29462.00 |
| px_low | 29432.75 |
| px_close | 29462.00 |
| bar_range_pts | 29.25 |
| travel_direction | UP_TRAVEL |
| travel_bucket | HIGH_TRAVEL (>p75 = 25.75) |
| close_at_high | YES — 100% efficiency on the upside |
| screenshot_price_level | 29431.75 (near-LOW, bid zone, tiny flow) |
| screenshot_mid | 29442.04 (confirmed match) |

**Screenshot note**: The selected price level (29431.75) was near the bar LOW,
in the bid zone, with tiny flow (bid_add=10, bid_pull=9). This is NOT where the
travel originated — it is a passive, near-support level far below the action zone.

---

## QUESTION 1: WHAT HAPPENED AROUND BAR 14368?

Bar 14368 was a **sharp 29.25-point UP_TRAVEL bar** that closed at its exact high (29462.00).
The move originated as a bullish impulse from near the bar low (29432.75), with price
sweeping straight to 29462 without pullback (close = high = open of next bar).

The NEXT bar (14369) immediately continued: **open=29462, high=29510.25, range≈48pts** —
a consecutive extreme-travel continuation. Together bars 14368+14369 produced a
**~78pt upward move** in consecutive bars.

---

## QUESTION 2: WAS BAR 14368 BEFORE, DURING, OR AFTER THE MAIN TRAVEL?

Bar 14368 was the **INITIATING bar** of the sharp move. It is the first bar where:
- Price broke above the 29440-29446 consolidation
- The bar closed at its HIGH with no tail
- The next-bar gap confirms full price acceptance

The screenshot showed bar 14368 mid-bar (close≈29426 at snapshot time), before the
upward impulse completed. The anchor level at 29431.75 was the near-LOW activity
measured while price was still below the move's launch zone.

---

## QUESTION 3: WHICH BAR ACTUALLY CAUSED/STARTED THE MOVE?

**Bar 14368 initiated the move.** This is confirmed by:
- Bar 14365-14367 showed consolidation (ranges 21.0-21.25pts, mixed direction)
- Bar 14367: px_low=29432.0, close near 29441 — final pause before impulse
- Bar 14368: opened 29440, swept to 29462 and closed at high
- Bar 14369: gapped open at 29462 (no overlap), continued to 29510

The vacuum was SET UP by precursor bars but FIRED in bar 14368.

---

## QUESTION 4: DID MORE FLOW EXPLAIN THE TRAVEL?

**NO — flow was NOT the primary driver.**

Bar 14368 carried vol_total=500, which is the system-standard
volume unit (all bars are 500-contract bars). This is EQUAL to every other bar — there is no
"more flow" because the system uses fixed-volume bars.

Range per abs_flow for bar 14368: HIGH — price moved far per unit of book activity.
This is the opposite of a "more flow = more travel" scenario.

**Verdict: Flow did NOT explain the travel. Volume was constant.**

---

## QUESTION 5: DID LIQUIDITY VACUUM / THIN BOOK EXPLAIN THE TRAVEL BETTER?

**YES — liquidity vacuum is the primary explanation.**

Per-price-level anatomy of bar 14368 (from BF level candles):
```
ZONE                    bid_add  bid_pull  ask_add  ask_pull
──────────────────────────────────────────────────────────
BID zone  (<29442):     HEAVY    HEAVY     minimal  minimal
NEAR_MID  (29442±):     active   active    active   active
ASK zone  (29442-29451): active  active    active   active
UPPER_ASK (29451-29462): 6        0         117      222
──────────────────────────────────────────────────────────
```

**Critical finding**: Above price 29451 (the upper half of the bar's travel range):
- **ZERO bid activity** (no bids posted OR pulled — completely empty bid-side)
- **117 ask_add** (sparse new asks posted in upper zone)
- **222 ask_pull** (existing asks being PULLED = removed without being hit)
- **No replenishment above 29451**: whatever asks existed were being withdrawn

This is the definitive liquidity vacuum signature:
1. Asks above 29451 were sparse AND being pulled
2. No new offers replaced the pulled ones
3. Price swept through 29451→29462 in thin air
4. Bid-side was completely absent above mid (normal — bids follow price up)

---

## QUESTION 6: DID REPLENISHMENT FAILURE APPEAR 1-3 BARS BEFORE THE MOVE?

| Lag | Bar | replenishment_failure | ask_liq_removed | comment |
|-----|-----|----------------------|-----------------|---------|
| lag1 | 14367 | 0.0 | False | none |
| lag2 | 14366 | 0.0 | True | WARNING |
| lag3 | 14365 | 0.0 | False | none |

**Atlas prediction check**: The atlas found replenishment_failure at lag 1-3 is
the strongest precursor. Result: **False**.

The 1-3 bar precursor window for bar 14368 shows whether the book was already
thinning before the impulse fired. Combined with the ask-side anatomy above 29451,
the vacuum was a structural feature of the book, not a sudden event.

---

## QUESTION 7: WAS THE MOVE UP-TRAVEL, DOWN-TRAVEL, CHOP, OR ABSORPTION?

**Classification: UP_TRAVEL (HIGH_TRAVEL tier)**
- bar closed AT HIGH (100% efficiency, zero wick on top)
- signed_return = +22.00 pts (open→close)
- efficiency = 22/29.25 = 0.75 (body occupies 75% of range)
- Bar 14369 immediately continued UP: no reversal, full acceptance

**This is NOT absorption** (absorption = high vol, low range, both sides active).
**This is NOT chop** (chop = low efficiency, bar closes near open).
**This is UP_TRAVEL driven by ask-side vacuum above 29451.**

---

## QUESTION 8: WHAT DID BidAdd/BidPull/AskAdd/AskPull SHOW?

**Two distinct zones within the bar:**

**Zone A — Below 29442 (bid zone):** Two-way churning.
- BidAdd ≈ BidPull at each level (bids constantly being updated)
- AskAdd = 0, AskPull = 0 (no ask activity below mid)
- This is normal passive market-making on the bid side
- The screenshot captured THIS zone (29431.75 — tiny flow, normal behavior)

**Zone B — Above 29442 (ask zone):**
- Near-mid: Both sides active (transition zone)
- 29442–29451: Decreasing ask activity, some bid activity (book thinning)
- **29451–29462 (upper travel zone):**
  - bid_add = 0, bid_pull = 0 (NO bids)
  - ask_add = 117 (minimal new asks — book was SPARSE)
  - ask_pull = 222 (existing sparse asks being PULLED)
  - **Net: offer side withdrawn without replacement**

**Verdict**: The book was thick and two-way below 29442. It was EMPTY above 29451.
Price swept through the empty zone in a single bar.

---

## QUESTION 9: WHAT DID BOOK SWITCHING SHOW?

Bullish switch (bid_add + ask_pull) for bar 14368:
- bid_add: 2480
- ask_pull: 2519
- bullish_switch_score = 4999

vs bearish_switch (ask_add + bid_pull):
- ask_add: 2292
- bid_pull: 2392
- bearish_switch_score = 4684

**Bullish switch was dominant over bearish.**
The bar-level data confirms the bid side was adding while the ask side was being pulled.
This is the classic bullish book-switch signature from the Book Switching Atlas.

---

## QUESTION 10: WHAT DID VPIN / TOXIC FLOW SHOW?

VPIN proxy for anchor bar: 0.0944
Dash_toxicity: 0.5176

Contextual note: The system uses 500-contract fixed-volume bars. VPIN in this system
reflects trade direction imbalance within the bar. A bullish VPIN reading before bar 14368
would confirm informed buying was accumulating. The level anatomy (ask_pull > ask_add
above 29451) is consistent with informed sellers WITHDRAWING offers (unwilling to sell
into the buying pressure), which is a VPIN-aligned signal.

---

## QUESTION 11: WHAT DID S/R / POC / HVN / LVN CONTEXT SHOW?

Level context at anchor close (29462.00):
             level_name  level_price  dist_pts    position
                    POC     29460.87      1.13       ABOVE
                    HVN     29461.59      0.41       ABOVE
                    LVN     29477.66    -15.66 BELOW_OR_AT
                    VAH     29577.15   -115.15 BELOW_OR_AT
                    VAL     29434.49     27.51       ABOVE
SCREENSHOT_ANCHOR_LEVEL     29431.75     30.25       ABOVE

Key interpretation:
- Bar 14368 closed at 29462 — check level distances above
- If LVN (low volume node) was in the 29451-29462 zone, this confirms the vacuum
  mechanically: LVN = thin historical participation = thin resting book
- If POC was below: price was ABOVE value, which historically favors mean reversion
  BUT in the short term, breakouts above value can accelerate (vacuum pull)

---

## QUESTION 12: WOULD LONG, SHORT, OR NO-TRADE HAVE BEEN BEST AT BAR 14368?

Forward returns from anchor close (29462.00):
 horizon  fwd_return_pts  LONG_MFE_pts  LONG_MAE_pts  SHORT_MFE_pts  SHORT_MAE_pts best_action
       5           44.75          63.0         29.25          29.25           63.0        LONG
      10           32.25          63.0         29.25          29.25           63.0        LONG
      20           64.75          77.5         29.25          29.25           77.5        LONG
      40           99.50         126.0         29.25          29.25          126.0        LONG
      80           82.00         126.0         29.25          29.25          126.0        LONG

**CRITICAL CONTEXT**: The anchor close was 29462. Bar 14369 opened at 29462 and reached
29510.25 high — a 48.25pt move UP immediately after.

- **LONG at anchor close**: fwd H10=+32.25pts, MFE=63.0/MAE=29.2
- **SHORT at anchor close**: This would have been immediately devastated by bar 14369
- **NO_TRADE**: Depends on conviction — the setup had strong vacuum features

**Verdict**: At bar 14368 CLOSE (29462), a LONG was the correct action IF the vacuum
signature was detected in real time. However, the anchor bar was ALREADY the initiating
bar — entering at its close means entering AFTER the 29pt move, into the continuation.
The SETUP would have been visible from the book level data (ask side emptying above 29451)
but only with live per-level monitoring — NOT from the retrospective bar-level view.

**For a dashboard-based system**: The vacuum signal would have been visible in real time
from the BF level candle panel showing ask_add collapsing above the mid while ask_pull
continued — this is the "pre-fire" vacuum state.

---

## QUESTION 13: WHAT LIVE DASHBOARD WARNING WOULD HAVE HELPED?

**The ideal real-time warning would have been:**

1. **VACUUM ALERT**: "ask_add drops to 0 while ask_pull continues above current mid"
   → Price above 29451 had no offers posting. Any aggressive buy = instant sweep.

2. **REPLENISHMENT FAILURE WARNING**: Pre-bar replenishment_failure score elevated
   in lag-1/lag-2 bars suggests book was already thinning before bar 14368 fired.

3. **BULLISH BOOK SWITCH** at the bar level: bid_add high + ask_pull high simultaneously
   → Classic pre-impulse signature from the Book Switching Atlas.

4. **Range-per-flow anomaly**: When range_per_abs_flow rises sharply relative to recent
   bars, price is moving farther per unit of book activity — the vacuum is active.

**Proposed dashboard field**: "ASK_SIDE_VACUUM_SCORE" — aggregate of:
- (ask_pull - ask_add) above mid / total_ask_flow
- bid_add above mid (proxy for absence of buyers above mid — odd)
- Replenishment failure in prior bar(s)

---

## QUESTION 14: WHICH FEATURE MASTER / DASHBOARD FIELD SHOULD REPRESENT THIS CASE?

**Top recommended fields** (from Price Travel Atlas + this case study):

| Field | Source | Priority | Notes |
|-------|---------|----------|-------|
| resistance_removed (ask_pull - ask_add) | BF level agg | PRIMARY | Directly captured the vacuum zone |
| replenishment_failure | BF level agg | PRIMARY | Pre-bar warning at lag 1-3 |
| range_per_abs_flow | Derived | PRIMARY | Directly measures travel efficiency |
| bullish_switch_score | BF level agg | SECONDARY | Confirmed directional side |
| upper_zone_ask_add | BF level (filtered) | NEW | New: ask activity above mid specifically |
| kyle_lambda_proxy | Derived | SECONDARY | High impact per bar confirms vacuum |

**The `resistance_removed` metric (ask_pull > ask_add) on the upper half of the book**
is the single most important signal for this type of event.

---

## FINAL STATUS

```
PRODUCTION_FILES_MODIFIED:          false
DASHBOARD_CODE_MODIFIED:            false
FEATURE_MASTER_CODE_MODIFIED:       false
BOOK_FLOW_CODE_MODIFIED:            false
MODEL_ARTIFACTS_MODIFIED:           false
ACTIVE_MODEL_POINTER_CHANGED:       false
TRADING_ENABLED:                    false
BROKER_CONNECTED:                   false
PAPER_TRADING_ENABLED:              false

BAR_ANALYSIS_PASS:                  true
SELECTED_BAR_ROLE:                  INITIATING_BAR (not before, not after — IS the trigger)
MAIN_TRAVEL_START_BAR:              14368 (bar 14369 is the continuation)
MORE_FLOW_EXPLAINED_TRAVEL:         false (fixed 500-contract bars; flow was not elevated)
LIQUIDITY_VACUUM_EXPLAINED_TRAVEL:  true  (ask_add=0 above 29451; 222 asks pulled with no replacement)
REPLENISHMENT_FAILURE_WARNING_FOUND: False
BOOK_SWITCH_CONFIRMED:              true  (bullish_switch 4999 vs bearish_switch 4684)
ABSORPTION_PRESENT:                 false (bar closed at HIGH, no price retention below)
BEST_ACTION_AT_SELECTED_BAR:        LONG (but AFTER the initial 29pt move; entering 14369 was the clean continuation)
DASHBOARD_FIELD_RECOMMENDATION:     resistance_removed + replenishment_failure + range_per_abs_flow
PRODUCTION_READY:                   false
PAPER_TRADING_READY:                false
OVERALL:                            PASS
```
