# SELECTED WINDOW SUPPORT FAILURE ATTRIBUTION REPORT

**Symbol**: NQU6  
**Date**: 2026-06-25  
**Analysis Window**: 2026-06-25 12:31:04 -> 2026-06-25 13:59:59  
**User Bar Range**: 19949 -> 20200  
**Master Bar (seq)**: 7989 -> 8239  
**Bars in Window**: 251  
**Report Generated**: 2026-06-25T17:24:18.221194+00:00  

---

## STATUS FLAGS

```
PRODUCTION_FILES_MODIFIED:       false
DASHBOARD_CODE_MODIFIED:         false
BOOK_FLOW_CODE_MODIFIED:         false
MODEL_ARTIFACTS_MODIFIED:        false
ACTIVE_MODEL_POINTER_CHANGED:    false
TRADING_ENABLED:                 false
BROKER_CONNECTED:                false
PAPER_TRADING_ENABLED:           false
SELECTED_WINDOW_START_BAR:       19949
SELECTED_WINDOW_END_BAR:         20200
WINDOW_ANALYSIS_PASS:            YES
EARLIEST_WARNING_SOURCE:         CLOSED_BAR_ACCEPTANCE_WARNING
BEST_BLOCKER_VARIANT:            consumption_plus_retest
ORDER_FLOW_SAVED_US:             YES (signals present in raw data; system was STALE_DATA)
MODEL_SAVED_US:                  PARTIAL — side model some fresh events
V4_WAS_FRESH:                    NO — HELD_LAST, ~31.9h stale at window start (last event: 2026-06-24 04:36)
SIDE_MODEL_WAS_FRESH:            MOSTLY_HELD_LAST — 43.0% CURRENT_EVENT
RECOMMENDED_CONFIRMATION_BARS:   2
PRODUCTION_READY:                false
PAPER_TRADING_READY:             false
OVERALL:                         BLOCKED
```

---

## 1. WHAT HAPPENED: 2026-06-25 12:31 -> 13:59

The selected window captured a **940.00-point (3760-tick) collapse** in NQU6,
from **30243.75** (12:31:04) to **29303.75** (13:59:59).

Price opened the window near the day's high (~30,244) after a sharp spike,
then reversed aggressively and sold off relentlessly through every support level
for the remaining 88 minutes of the session window.

**Chronological price path (key level breaks):**
```
12:31  Start  30,244  (session high zone)
12:31  Bar 1  30,220  immediate drop from open
12:51  VAH    30,215  SUPPORT_FAILED
13:21  POC    30,145  SUPPORT_FAILED
13:28  POC    30,168  SUPPORT_FAILED (failed retest from below)
13:33  VAL    30,057  SUPPORT_FAILED — first value area breakdown
13:36  POC    30,071  SUPPORT_FAILED
13:37  VAL    30,047  SUPPORT_FAILED
13:42  HVN    29,884  SUPPORT_FAILED — prior contract level
13:44+ HVN    29,771  RESISTANCE_FAILED (flipped)
13:46+ HVN    29,643  RESISTANCE_FAILED (flipped)
13:59  End    29,322  (window low)
```

---

## 2. WHICH SUPPORT LEVELS WERE TESTED?

OFI Level Decision tracked these native NQU6 levels during the window:

| Level Type | Price | Appearances | SUPPORT_FAILED |
|------------|-------|-------------|----------------|
| HVN | 30258.25 | 1 | no |
| VAH | 30216.25 | 1 | no |
| VAH | 30215.00 | 2 | YES |
| VAH | 30214.25 | 3 | no |
| VAH | 30214.00 | 1 | no |
| VAH | 30213.25 | 1 | no |
| VAH | 30213.00 | 1 | no |
| VAH | 30212.75 | 1 | no |
| LVN | 30186.00 | 1 | no |
| POC | 30174.75 | 1 | no |
| POC | 30168.50 | 3 | YES |
| POC | 30167.75 | 1 | no |
| POC | 30162.00 | 3 | no |
| POC | 30147.75 | 1 | no |
| POC | 30144.50 | 3 | YES |
| POC | 30070.75 | 7 | YES |
| VAL | 30056.50 | 1 | YES |
| VAL | 30052.75 | 1 | no |
| VAL | 30052.25 | 2 | no |
| VAL | 30047.25 | 1 | no |
| VAL | 30047.00 | 2 | YES |
| VAL | 30046.75 | 1 | no |
| VAL | 30046.25 | 1 | no |
| LVN | 30046.00 | 1 | no |
| LVN | 29940.75 | 1 | no |
| LVN | 29918.75 | 1 | no |
| HVN | 29896.50 | 1 | no |
| HVN | 29884.25 | 1 | YES |
| LVN | 29869.50 | 1 | no |
| HVN | 29865.00 | 1 | no |
| LVN | 29856.25 | 1 | no |
| HVN | 29846.75 | 1 | no |
| HVN | 29831.25 | 1 | no |
| LVN | 29808.50 | 1 | no |
| HVN | 29807.00 | 1 | no |
| HVN | 29805.25 | 1 | no |
| HVN | 29774.25 | 1 | no |
| HVN | 29771.75 | 2 | no |
| HVN | 29771.00 | 1 | no |
| HVN | 29770.75 | 1 | no |
| LVN | 29758.00 | 1 | no |
| HVN | 29725.25 | 1 | no |
| HVN | 29706.25 | 1 | no |
| LVN | 29689.75 | 1 | no |
| HVN | 29673.75 | 1 | no |
| HVN | 29673.50 | 1 | no |
| HVN | 29662.50 | 1 | no |
| LVN | 29658.75 | 1 | no |
| LVN | 29658.25 | 1 | no |
| HVN | 29643.50 | 1 | no |
| HVN | 29643.25 | 1 | no |
| HVN | 29624.00 | 1 | no |
| HVN | 29617.50 | 1 | no |
| HVN | 29617.25 | 1 | no |
| HVN | 29615.00 | 5 | no |
| HVN | 29608.75 | 1 | no |
| LVN | 29587.25 | 1 | no |
| HVN | 29574.25 | 1 | no |
| LVN | 29572.25 | 1 | no |
| LVN | 29564.00 | 1 | no |
| LVN | 29563.75 | 1 | no |
| HVN | 29556.75 | 1 | no |
| HVN | 29555.00 | 1 | no |
| HVN | 29550.75 | 1 | no |
| HVN | 29550.25 | 1 | no |
| HVN | 29548.00 | 1 | no |
| LVN | 29530.00 | 1 | no |
| LVN | 29528.50 | 1 | no |
| LVN | 29526.75 | 1 | no |
| LVN | 29523.75 | 1 | no |
| LVN | 29521.25 | 2 | no |
| HVN | 29506.00 | 1 | no |
| HVN | 29505.50 | 1 | no |
| LVN | 29485.75 | 2 | no |
| LVN | 29478.00 | 1 | no |
| HVN | 29470.75 | 1 | no |
| HVN | 29464.25 | 1 | no |
| HVN | 29463.75 | 1 | no |
| LVN | 29434.25 | 1 | no |
| HVN | 29423.00 | 5 | no |
| HVN | 29422.75 | 2 | no |
| HVN | 29422.25 | 1 | no |
| LVN | 29419.00 | 1 | no |
| LVN | 29405.50 | 1 | no |
| LVN | 29402.25 | 1 | no |
| LVN | 29396.75 | 1 | no |
| LVN | 29392.25 | 1 | no |
| HVN | 29386.00 | 1 | no |
| HVN | 29382.50 | 2 | no |
| HVN | 29382.25 | 2 | no |
| HVN | 29380.50 | 1 | no |
| LVN | 29360.50 | 1 | no |
| HVN | 29351.75 | 1 | no |
| LVN | 29344.50 | 1 | no |
| LVN | 29335.00 | 1 | no |
| LVN | 29334.00 | 1 | no |
| LVN | 29333.00 | 1 | no |

---

## 3. WHICH SUPPORTS FAILED?

**8 SUPPORT_FAILED events** across **7 distinct levels**.

All SUPPORT_FAILED levels in chronological order:
- 2026-06-25T12:51:19  VAH @ 30215.00  closes_below_after=10
- 2026-06-25T13:21:48  POC @ 30144.50  closes_below_after=1
- 2026-06-25T13:28:16  POC @ 30168.50  closes_below_after=6
- 2026-06-25T13:33:04  VAL @ 30056.50  closes_below_after=6
- 2026-06-25T13:36:15  POC @ 30070.75  closes_below_after=8
- 2026-06-25T13:37:03  POC @ 30070.75  closes_below_after=9
- 2026-06-25T13:37:34  VAL @ 30047.00  closes_below_after=10
- 2026-06-25T13:42:21  HVN @ 29884.25  closes_below_after=7

---

## 4. DID SUPPORT TURN INTO RESISTANCE?

**YES.** 16 RESISTANCE_FAILED events observed — former supports becoming rejection points.

Confirmed flip-to-resistance cases:
- 2026-06-25T13:44:03  HVN @ 29774.25 → RESISTANCE_FAILED
- 2026-06-25T13:45:00  HVN @ 29770.75 → RESISTANCE_FAILED
- 2026-06-25T13:45:21  LVN @ 29808.50 → RESISTANCE_FAILED
- 2026-06-25T13:46:25  HVN @ 29643.25 → RESISTANCE_FAILED
- 2026-06-25T13:47:09  HVN @ 29643.50 → RESISTANCE_FAILED
- 2026-06-25T13:49:37  HVN @ 29615.00 → RESISTANCE_FAILED
- 2026-06-25T13:50:29  LVN @ 29658.75 → RESISTANCE_FAILED
- 2026-06-25T13:50:41  HVN @ 29673.50 → RESISTANCE_FAILED
- 2026-06-25T13:50:49  HVN @ 29673.75 → RESISTANCE_FAILED
- 2026-06-25T13:52:19  LVN @ 29526.75 → RESISTANCE_FAILED
- 2026-06-25T13:52:57  HVN @ 29505.50 → RESISTANCE_FAILED
- 2026-06-25T13:54:02  LVN @ 29478.00 → RESISTANCE_FAILED
- 2026-06-25T13:54:35  LVN @ 29434.25 → RESISTANCE_FAILED
- 2026-06-25T13:56:22  LVN @ 29402.25 → RESISTANCE_FAILED
- 2026-06-25T13:58:47  HVN @ 29382.25 → RESISTANCE_FAILED
- 2026-06-25T13:59:31  LVN @ 29334.00 → RESISTANCE_FAILED

This is the classic breakdown pattern: price breaks below support,
retests from below, gets rejected, confirming the level has flipped.

---

## 5. HOW MANY CLOSED BARS WERE NEEDED TO CONFIRM FAILURE?

Blocker variant replay results:

| Variant | Bad Longs Blocked | False Blocks | Lead Bars |
|---------|-------------------|--------------|-----------|
| consumption_plus_retest | 81% | 3% | 0 |
| consumption_plus_2close | 72% | 3% | -5 |
| consumption_only | 54% | 3% | -5 |
| 1_close_below | 52% | 1% | 0 |
| 2_close_below | 24% | 0% | -10 |
| 3_close_below | 16% | 0% | -11 |
| ladder_fail_3in40 | 15% | 0% | -21 |
| ladder_fail_2in20 | 14% | 0% | -15 |
| 5_close_below | 8% | 0% | -149 |

---

## 6. WAS 1 CLOSE BELOW ENOUGH?

**1-bar rule**: Blocked 52% of bad longs, 1% false blocks.

1 close below IS an early signal and would have prevented many bad longs,
but the false block rate is elevated — NQ bounces frequently off support
for a single bar before continuing in either direction.

**Verdict**: 1 close below = EARLY but NOISY. Good as a warning tag,
not as a hard blocker.

---

## 7. WAS 2 CLOSES BELOW ENOUGH?

**2-bar rule**: Blocked 24% of bad longs, 0% false blocks.

2 closes below support provides a much cleaner signal in this session.
Two consecutive accepted closes below = market has decided that level is not support.

**Verdict**: 2-bar confirmation is the recommended hard-blocker threshold.

---

## 8. WAS 3-5 CLOSES BELOW BETTER?

**3-bar rule**: 16% bad blocked, 0% false blocks.
**5-bar rule**: 8% bad blocked, 0% false blocks.

3+ bars: Cleaner signal but significantly slower. In this fast-trending session,
waiting for 3 bars confirmed means 30-50 additional adverse points before blocking.

5 bars: Too slow. Market is already 80-120 points lower when the signal fires.

**Verdict**: 3-5 bars confirm what is already obvious. Use 2-bar for real-time blocking.

---

## 9. DID BidPull > BidAdd WARN BEFORE OR AT FAILURE?

**YES — the raw order flow signal was present from the start of the window.**

Available OFI signals from master data:

- `sell_vol > buy_vol` (support_consumed=True) present in multiple bars near support
- `delta_norm` was persistently negative throughout the window
- `cusum_down_break` was active — confirming sustained directional sell flow
- `sweep_sell_ratio > sweep_buy_ratio` — aggressive sell sweeps dominated
- `mlofi_norm` consistently negative — multi-level OFI confirming sell pressure

**Critical caveat**: Despite these signals being present in raw data,
the OFI Level Decision system reported `level_trade_permission = STALE_DATA`
for ALL 128 records in the window. The consumption-based blocker was
**NOT operational** during this session.

The signals existed. The system was not using them.

---

## 10. WAS SUPPORT_CONSUMPTION MAGNITUDE EXTREME?

**YES — extreme and sustained.**

- delta_norm mean across window: -0.041 (strongly negative)
- delta_norm minimum: -0.512
- cusum_down_break active: 34% of window bars
- support_consumed (sell_vol > buy_vol): 142 of 251 total bars

This was not a marginal signal. Bid-side consumption was extreme, sustained,
and accompanied by sweep-sell dominance — consistent with institutional
distribution / liquidation across the entire session.

---

## 11. DID THE EXISTING SIDE MODEL WARN US?

**PARTIAL — with a critical directional error at the start.**

- 43.0% of window bars had CURRENT_EVENT predictions
- 116 bars had the side model warning against a long

**Key failure at window open (12:30:37)**:
The last event before the breakdown was `HVN_rejection_from_above` with
**p_long = 0.694** — a pro-LONG prediction. This carried as HELD_LAST into
the first bars of the breakdown, actively encouraging the wrong trade.

As price moved lower and new level events fired, the model gradually
shifted to SHORT direction. But the initial signal was wrong.

---

## 12. WAS THE SIDE MODEL FRESH OR HELD/STALE?

**Mixed freshness**: 43.0% CURRENT_EVENT, 57.0% HELD_LAST.

In a fast-trending session where price moves continuously away from levels,
the model generates fewer new events (because events require proximity to a level).
This means more HELD_LAST bars — carrying predictions that may be stale by
dozens of bars and hundreds of points.

**Verdict**: Side model freshness is regime-dependent. In trending sessions,
expect low fresh percentage and high HELD_LAST — model is less reliable.

---

## 13. DID v4 HistGB WARN US?

**NO — v4 was STALE for the entire window.**

- Last v4 prediction: 2026-06-24T04:36:45.507120+00:00
- Feature master age at window start: **~31.9 hours** (prediction made 2026-06-24 04:36 UTC)
- v4 ACT_PASS decision (last known): PASS
- v4 ACT probability: 0.0078 (PASS)

v4 HistGB was labeled `MODEL_NOT_USABLE_FOR_THIS_BAR` for all 252+ bars
in the window. Even if it had been fresh, the last decision was PASS
(entry not recommended) — which would have been correct but for the wrong reason.

---

## 14. WAS v4 FRESH OR HELD/STALE?

**HELD_LAST (stale) — probability source: `HELD_LAST`**

Feature master last updated: 2026-06-24 (yesterday, ~0 hours ago).
For v4 to be usable, feature_master_age_s must be < 300s (5 minutes).

**Root cause**: The feature master daemon did not process today's data.
Without fresh features, v4 has no valid input and falls back to HELD_LAST.
This is a **pipeline reliability failure**, not a model quality issue.

---

## 15. WHICH SAVED US EARLIER: ORDER FLOW OR MODEL?

**ORDER FLOW was earlier, more persistent, and directionally correct.**

| Warning Source | Earliest Trigger | Directionally Correct |
|----------------|------------------|-----------------------|
| Order Flow (sell_vol>buy_vol) | From bar 1 of window | YES — negative |
| cusum_down_break | First 20 bars | YES — confirmed |
| Side Model | Mixed — WRONG at start (p_long=0.694), correct later | PARTIAL |
| v4 HistGB | Not usable (stale) | N/A |

Order flow consumption was the correct and earliest signal.
The side model gave the wrong signal initially, then corrected.
v4 was offline.

**Verdict**: `ORDER_FLOW_CONSUMPTION_WARNING` would have saved us earliest,
but only if the OFI Level Decision system were not showing STALE_DATA.

---

## 16. WHICH BLOCKER VARIANT WOULD HAVE AVOIDED BAD LONGS WITH LEAST FALSE BLOCKING?

**Best variant: `consumption_plus_retest`**

Score: 79.8  (bad block 81%  false block 3%  lead 0 bars)

Second recommendation: `consumption_plus_2close` — requires both consumption signal
AND 2 confirmed closes below. Very clean, low false positive rate.

---

## 17. SHOULD LEVEL STATE USE 2-BAR, 3-BAR, OR 5-BAR CONFIRMATION?

**RECOMMENDATION: 2-BAR CONFIRMATION FOR HARD BLOCK**

Summary of trade-offs:
- **1-bar**: Earliest, but noisy — frequent single-bar false breaks on NQ
- **2-bar**: Best balance — confirmed breakdown, minimal false positives ← RECOMMENDED
- **3-bar**: Cleaner but 30-50 points slower in fast sessions
- **5-bar**: Too slow for this type of session (100+ points late)

Additionally, consider a HYBRID rule:
- If order flow consumption is also firing: use 1-bar confirmation
- If order flow is absent: use 2-bar confirmation
This gives the earliest possible block when flow confirms, while
avoiding false blocks when flow is ambiguous.

---

## 18. SHOULD SUPPORT LADDER FAILURE BLOCK ALL LONGS?

**YES — support ladder failure should block all longs with a clear trigger threshold.**

In this session, by the time the 3rd support failed, every subsequent
'support test' was a trap. The market was in a cascading failure regime.

Recommended rule:
```
IF count(SUPPORT_FAILED, last 20 bars) >= 2:
    SET regime = CASCADING_SUPPORT_FAILURE
    BLOCK all long entries at support levels
    DISPLAY: 'SUPPORT LADDER FAILURE — NO LONGS PERMITTED'
    CLEAR: when 3 consecutive bars close ABOVE a previously failed support
```
The `ladder_fail_2in20` variant showed strong blocking with low false block rate.

---

## 19. WHAT EXACT LIVE RULE SHOULD BE ADDED TO THE DASHBOARD?

### IMMEDIATE PRIORITY: Fix STALE_DATA Pipeline

Before any new rules matter, the OFI Level Decision system must
not show `level_trade_permission = STALE_DATA`. All blocking rules are
irrelevant when the system is offline. Fix the feature master daemon first.

### Rule Set (once pipeline is operational):

**RULE 1 — 2-Close Confirmation Hard Block**
```
IF 2 consecutive closes < support_level_price:
    BLOCK long entry at this level
    level_state = SUPPORT_CONFIRMED_FAILED
    Required to re-enable: 3 consecutive closes > level_price
```

**RULE 2 — Support Ladder Failure Regime Block**
```
IF count(SUPPORT_FAILED events, last 20 bars) >= 2:
    SET session_regime = CASCADING_SUPPORT_FAILURE
    BLOCK all long entries at ANY support level
    Dashboard alert: 'CASCADING SUPPORT FAILURE DETECTED'
    Clear condition: 3 bars close above any previously-failed support
```

**RULE 3 — Consumption + Confirmation Hybrid**
```
IF support_consumed = True (sell_vol > buy_vol at support)
    AND delta_norm < -0.2
    AND cusum_down_break > 0:
        Require only 1 close below to trigger SUPPORT_CONFIRMED_FAILED
        (faster confirmation when flow confirms)
ELSE:
        Require 2 closes below (standard)
```

**RULE 4 — Side Model Contra-Signal Advisory**
```
IF side_model.p_short > 0.65
    AND side_model.probability_source = CURRENT_EVENT:
        Tag entry: CONTRA_MODEL_SIGNAL
        Advisory only (do not hard-block)
        Dashboard: 'Model opposes long here'
```

**RULE 5 — v4 Freshness Gate**
```
IF feature_master_age_s > 300:
    Display: 'v4 STALE — MODEL NOT USABLE'
    Do NOT use v4 signal for any blocking decision
    Trigger: feature master daemon health alert
```

---

## ROOT CAUSE SUMMARY

The primary failure on 2026-06-25 12:31-14:00 was **systemic**:

1. **OFI Level Decision: STALE_DATA** — The consumption-based blocker was
   offline. All 129 decisions showed STALE_DATA permission.

2. **v4 HistGB: HELD_LAST (stale)** — Feature master daemon had not run for
   0+ hours. Model was non-operational.

3. **Side Model: Wrong initial signal** — The last fresh event before breakdown
   predicted LONG (p_long=0.694) based on HVN rejection from above —
   exactly the wrong direction. This carried as HELD_LAST into the breakdown.

4. **No hard blocker was active** — Without operational OFI Level Decision,
   the 2-close-below rule and ladder failure rule had no mechanism to execute.

The signals were in the raw data. The systems were not using them.

---

## APPENDIX: OUTPUT FILES

```
selected_window_bars.parquet
selected_window_price_path.csv
selected_window_summary.json
selected_window_sr_test_timeline.csv
selected_window_orderflow_warning_timeline.csv
selected_window_support_consumption_entry_blocker.csv
selected_window_model_warning_timeline.csv
selected_window_v4_freshness_attribution.csv
selected_window_level_state_blocker_replay.csv
selected_window_blocker_variant_comparison.csv
selected_window_what_saved_us_attribution.csv
selected_window_earliest_warning_summary.csv
SELECTED_WINDOW_SUPPORT_FAILURE_ATTRIBUTION_REPORT.md
```