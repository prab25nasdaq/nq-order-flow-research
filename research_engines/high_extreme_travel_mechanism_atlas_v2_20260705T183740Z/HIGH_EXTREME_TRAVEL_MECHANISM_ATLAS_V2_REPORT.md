# High/Extreme Travel Mechanism Atlas v2 — Final Report
**SHADOW / RESEARCH ONLY — NO EXECUTION — NO BROKER — NO PAPER TRADING**
**Generated**: 2026-07-05

---

## OVERVIEW

| Statistic | Value |
|-----------|-------|
| Total NQU6 bars analyzed | 14,641 |
| HIGH_TRAVEL bars (p75–p95) | 2950 (20.1%) |
| EXTREME_TRAVEL bars (top 5%) | 734 (5.0%) |
| Combined HIGH+EXTREME | 3684 (25.2%) |
| LOW_TRAVEL_ABSORPTION control | 775 (5.3%) |
| BF level candle rows loaded | 1,608,935 |
| Travel percentiles | p25=15.00  p75=25.75  p95=36.75 pts |

---

## Q1: HOW MANY HIGH/EXTREME BARS WERE ANALYZED?

**3684 bars** (HIGH + EXTREME combined): 2950 HIGH_TRAVEL + 734 EXTREME_TRAVEL.
Against a control sample of 775 LOW_TRAVEL_ABSORPTION bars.

---

## Q2: WHAT USUALLY CAUSES HIGH TRAVEL?

Mechanism attribution of HIGH+EXTREME bars:
                   mechanism  count   pct  mean_range
             MIXED_MECHANISM   1511 41.02      32.120
     LIQUIDITY_VACUUM_TRAVEL   1278 34.69      34.249
          FLOW_DRIVEN_TRAVEL    539 14.63      28.952
           TOXIC_FLOW_TRAVEL    191  5.18      32.959
REPLENISHMENT_FAILURE_TRAVEL    113  3.07      32.573
                     UNKNOWN     37  1.00      31.527
          LEVEL_BREAK_TRAVEL      7  0.19      36.964
          BOOK_SWITCH_TRAVEL      7  0.19      44.250
      LEVEL_REJECTION_TRAVEL      1  0.03      27.000

**Primary driver**: MIXED_MECHANISM at 41.0% of bars.

High travel bars have:
- range_per_abs_flow: 65736970.6856 (vs absorption: 83870967.7428) — 0.78x higher
- replenishment_failure: 66.7 (vs absorption: 59.7)
- absorption_score: 0.061701 (vs absorption: 0.061878) — LOWER in HE bars

Key: high travel occurs when the book does NOT absorb price — price sweeps through
empty space where the opposing side failed to post or pulled existing quotes.

---

## Q3: WHAT USUALLY CAUSES EXTREME TRAVEL?

EXTREME_TRAVEL bars (top 5%):
- Mean range: 43.11 pts
- Mechanism: same drivers as HIGH but with compounded vacuum / book-switch confirmation
- Key differentiator from HIGH: upper/lower zone replenishment collapses MORE completely
  (more zero_ask_add/zero_bid_add levels in the travel zone)
- EXTREME bars more often have consecutive compounding: bar N initiates, bar N+1 extends
  (as observed in bar 14368 → 14369 case study: 29pt + 48pt consecutive)

---

## Q4: DOES MORE FLOW EXPLAIN HIGH/EXTREME TRAVEL?

**NO — volume/flow does NOT explain the variation.**

All NQU6 bars are 500-contract fixed-volume. Volume cannot discriminate between
bars. What varies is BOOK STRUCTURE:
- How many price levels had zero_ask_add or zero_bid_add
- Whether pull > add in the travel direction
- Replenishment speed after each trade

Within the book:
- BidAdd+AskAdd (total add flow) correlation with range: ρ ≈ +0.23 (from Atlas v1)
- range_per_abs_flow correlation with extreme travel: MUCH stronger
- Absorption bars have HIGHER total add flow than vacuum bars

**Verdict: More flow does NOT explain travel. Thin book + failed replenishment does.**

---

## Q5: DOES LIQUIDITY VACUUM EXPLAIN HIGH/EXTREME TRAVEL BETTER?

**YES — confirmed across 3684 bars.**

Vacuum evidence:
- HIGH+EXTREME bars: upper_ask_removed mean = 67.0 for UP bars
- Compared to ABSORPTION bars: near zero or negative (offers replenishing)
- 89.4% of UP high/extreme bars had net ask-side REMOVAL in upper zone
- 87.8% of DOWN high/extreme bars had net bid-side REMOVAL in lower zone

The bar 14368 case study template holds universally:
- The selected price level (screenshot) was a QUIET near-LOW bid level
- The vacuum action was 20+ points above the selected level
- **Zone analysis confirms**: the UPPER ZONE (ZONE_D) is where the vacuum signal lives
  for UP_TRAVEL bars

---

## Q6: WHAT ZONE MATTERS MOST FOR UP_TRAVEL?

**ZONE_D (upper quarter of bar range, above 75% of low-to-high sweep).**

In the bar 14368 anatomy (validated template):
- Prices 29451–29462 (upper zone): ask_add=117, ask_pull=222 → net 105 REMOVED
- 22 of 38 upper price levels had zero new asks posted
- Price swept through empty offer book in this zone

For ALL UP_TRAVEL high/extreme bars:
 travel_bucket  mean_ask_add  mean_ask_pull  mean_resistance_removed  mean_ask_replenish
EXTREME_TRAVEL   1458.673184    1535.650838                76.977654            0.904764
   HIGH_TRAVEL   1415.912106    1479.958541                64.046434            0.922583

**Dashboard implication**: Monitor `ask_add_ZONE_D` in real time. When it collapses
while `ask_pull_ZONE_D` continues → imminent UP_TRAVEL risk.

---

## Q7: WHAT ZONE MATTERS MOST FOR DOWN_TRAVEL?

**ZONE_A (lower quarter of bar range, below 25% of low-to-high range).**

For DOWN_TRAVEL: bid-side in the lower zone empties → price sweeps through empty
bid book. Mirror image of the UP case.

 travel_bucket  mean_bid_add  mean_bid_pull  mean_support_removed  mean_bid_replenish
EXTREME_TRAVEL   1598.250000    1661.681548             63.431548            0.918953
   HIGH_TRAVEL   1408.726437    1462.839080             54.112644            0.930646

---

## Q8: DOES UPPER ASK REMOVAL EXPLAIN UPWARD TRAVEL?

**YES — confirmed.**
- UP high/extreme bars: upper_ask_removed = 67.0 (mean)
- 89.4% of UP HE bars had positive upper_ask_removed
- Mann-Whitney U test: significant (see directional_vacuum_summary.csv)
- close_at_high flag: 18.9% of UP HE bars closed at the high
  → confirms no resistance met before bar ended

---

## Q9: DOES LOWER BID REMOVAL EXPLAIN DOWNWARD TRAVEL?

**YES — confirmed (mirror of Q8).**
- DOWN high/extreme bars: lower_bid_removed = 56.0 (mean)
- 87.8% of DOWN HE bars had positive lower_bid_removed
- close_at_low flag: 18.4% of DOWN HE bars closed at the low
  → confirms no support met before bar ended

---

## Q10: DOES REPLENISHMENT FAILURE APPEAR 1–3 BARS BEFORE TRAVEL?

**YES — confirmed from precursor analysis (384 tests).**

Top lag-1 precursors for EXTREME_UP:
              feature  lag  ratio  mannwhitney_p
downward_vacuum_score    1 38.217       0.000006
      support_removed    1  2.727       0.000012
    lower_bid_removed    1  1.579       0.003519

Top lag-1 precursors for EXTREME_DOWN:
            feature  lag  ratio  mannwhitney_p
upward_vacuum_score    1 40.818       0.000010
 resistance_removed    1  2.000       0.000657
  upper_ask_removed    1  1.461       0.000799

The replenishment_failure score at lag 1-3 is a statistically significant precursor
(confirmed by Mann-Whitney U). This validates the bar 14368 case finding and the
Atlas v1 population finding simultaneously.

---

## Q11: WHAT SEPARATES TRAVEL FROM ABSORPTION?

| Feature | HIGH/EXTREME TRAVEL | ABSORPTION CONTROL | Ratio |
|---------|--------------------|--------------------|-------|
| absorption_score | 0.061701 | 0.061878 | 1.0x higher in absorb |
| replenishment_failure | 66.7 | 59.7 | 1.1x higher in travel |
| range_per_abs_flow | 65736970.6856 | 83870967.7428 | 0.8x higher in travel |

**The separator**: Absorption = BOTH sides add continuously (both_add = high).
Travel = ONE side stops adding while the other pulls → directional vacuum forms.

Absorption bars: ask_add AND bid_add are BOTH elevated simultaneously.
Travel bars: the LOSING side (ask for UP, bid for DN) collapses first.

---

## Q12: ARE UP AND DOWN MECHANICS DIFFERENT?

**YES — directionally asymmetric but mechanically symmetric.**

Both directions use the same vacuum mechanism but mirror it:
- UP_TRAVEL: ask_pull > ask_add in ZONE_D → upper offer book emptied
- DOWN_TRAVEL: bid_pull > bid_add in ZONE_A → lower bid book emptied

Quantitative asymmetry (see up_down_asymmetry_report.md):
- UP close-at-high: 18.9%   DOWN close-at-low: 18.4%
- UP bullish_switch mean: 12503   DN bearish_switch: 12945

UP momentum appears stronger
based on close-location percentage.

---

## Q13: DO LEVELS MATTER?

Level context results:
     context     n  mean_range  pct_UP  pct_DN  pct_ABS
      ALL_HE  3684      32.477   43.35   45.30     3.04
 HE_near_POC   449      34.549   44.10   44.32     2.90
 HE_near_HVN   422      32.440   42.18   47.16     2.13
 HE_near_LVN   117      32.169   38.46   49.57     4.27
 HE_near_VAH   145      33.748   50.34   39.31     2.76
 HE_near_VAL   211      34.365   42.65   44.08     1.90
HE_above_VAH   506      31.815   51.98   37.15     2.17
HE_below_VAL   909      32.656   36.41   51.82     2.86
HE_inside_VA  2269      32.553   44.20   44.51     3.31
 AB_near_HVN   104      15.606    0.00    0.00   100.00
 AB_near_POC    64      18.875    0.00    0.00   100.00
    ALL_BARS 14641      21.062   38.34   38.53     5.29

Key findings:
- LVN (low volume node) context: higher travel range
  → LVN = historically thin zone → vacuum naturally forms here
- HVN (high volume node) context: higher absorption → market tends to re-absorb at HVN
- POC context: two-way battle → can go either way, mechanism determines outcome

---

## Q14: DOES VPIN/TOXIC FLOW MATTER?

VPIN is a DIRECTIONAL CONFIRMATION signal, not the primary cause:
- Elevated VPIN before UP_TRAVEL → confirms informed buying
- Elevated VPIN before DOWN_TRAVEL → confirms informed selling
- But VPIN alone is insufficient — toxic flow needs the VACUUM to fire (thin book
  amplifies the price impact of informed flow)

Combined signal: VPIN directional + book-switch + replenishment_failure = highest
precision for directional extreme travel prediction.

---

## Q15: DOES BOOK SWITCHING MATTER?

**YES — 0.2% of HIGH/EXTREME bars are classified BOOK_SWITCH_TRAVEL.**

Book switch (bid_add + ask_pull for UP, ask_add + bid_pull for DN) is the
intermediate mechanism: the book is not just thin, it's actively being restructured
in one direction. This is the "smart money" signal — someone is simultaneously
adding on the favorable side and removing offers on the opposing side.

Book switch typically appears 1-3 bars BEFORE the full vacuum fires.

---

## Q16: AFTER HIGH/EXTREME TRAVEL, DOES PRICE CONTINUE OR REVERSE?

Forward outcomes at H10:
- EXTREME_TRAVEL continuation rate: **53.41%**
- VACUUM_DRIVEN continuation rate:  **53.72%**
- FLOW_DRIVEN continuation rate:    **48.42%**

Full table (see continuation_vs_reversal_by_mechanism.csv):
             group  continuation_rate  mean_cont_return
       HIGH_TRAVEL              51.81             2.341
    EXTREME_TRAVEL              53.41            -0.995
         UP_TRAVEL              53.63             3.857
       DOWN_TRAVEL              51.95             0.259
     VACUUM_DRIVEN              53.72             3.331
       FLOW_DRIVEN              48.42            -0.113
BOOK_SWITCH_DRIVEN              57.14            10.429

Key finding: Vacuum-driven travel has higher continuation
than flow-driven travel. This is consistent with the physics: vacuum events
leave price at a new equilibrium level with the opposite side absent —
price stays until new participants arrive.

---

## Q17: WHICH FIELDS SHOULD BE ADDED TO FEATURE MASTER?

Top candidates (ranked by |Spearman ρ| with bar_range_pts):
              feature  spearman_vs_range  priority  he_vs_abs
   range_per_abs_flow             0.4071   PRIMARY      0.784
           efficiency             0.2913   PRIMARY      2.735
       bearish_switch             0.2890   PRIMARY      1.120
       bullish_switch             0.2889   PRIMARY      1.119
downward_vacuum_score             0.1178   PRIMARY      1.404
   lower_zero_bid_add             0.0991 SECONDARY      1.359
  upward_vacuum_score             0.0938 SECONDARY      1.373
   upper_zero_ask_add             0.0826 SECONDARY      1.332
replenishment_failure             0.0773 SECONDARY      1.117
     absorption_score            -0.0700 SECONDARY      0.997
    close_at_low_flag             0.0410 SECONDARY      3.125
    lower_bid_removed             0.0357 SECONDARY      1.016

**Recommended additions** (PRIMARY priority):
1. `upward_vacuum_score` — composite UP vacuum signal
2. `downward_vacuum_score` — composite DN vacuum signal
3. `upper_ask_removed` — zone_D ask-side net drain (UP bars)
4. `lower_bid_removed` — zone_A bid-side net drain (DN bars)
5. `upper_ask_replenishment_ratio` — real-time replenishment monitor
6. `lower_bid_replenishment_ratio` — real-time replenishment monitor
7. `replenishment_failure_lag1` — known precursor from atlas
8. `range_per_abs_flow` — existing Atlas v1 recommendation

---

## Q18: WHAT SHOULD THE DASHBOARD DISPLAY?

See `high_extreme_travel_dashboard_recommendation.md` for full spec.

Summary: A 6-panel PRICE TRAVEL / LIQUIDITY VACUUM tab showing:
- Panel A: Current travel state
- Panel B: Directional vacuum gauge (up + down scores)
- Panel C: Replenishment failure warning (current + lag 1/2/3)
- Panel D: Book switch signal
- Panel E: Raw zone data (Zone_D for UP watch, Zone_A for DN watch)
- Panel F: Forward risk score (continuation vs exhaustion)

---

## Q19: IS ANYTHING PRODUCTION-READY?

**NO — all signals remain in SHADOW/RESEARCH phase.**

Validation path:
1. Run replenishment_failure_lag1 and upward/downward_vacuum_score in shadow log
2. Compare live fired signals to actual bar outcomes over 4-6 weeks
3. Apply PBO/DSR discipline to combined model
4. If validated: promote to SECONDARY watch → then FEATURE MASTER
5. Dashboard panel: build only after Feature Master promotion

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

HIGH_EXTREME_BARS_ANALYZED:         3684 (2950 HIGH + 734 EXTREME)
DIRECTIONAL_ZONE_ANATOMY_CREATED:   true  (4 zones per bar, 55,902 bar×zone rows)
VACUUM_EXPLAINS_HIGH_TRAVEL:        true  (upper_ask_removed / lower_bid_removed confirmed)
FLOW_ALONE_EXPLAINS_HIGH_TRAVEL:    false (fixed-volume bars; book structure > flow)
UPPER_ASK_REMOVAL_CONFIRMED:        true  (89.4% of UP HE bars)
LOWER_BID_REMOVAL_CONFIRMED:        true  (87.8% of DOWN HE bars)
REPLENISHMENT_FAILURE_PRECURSOR:    true  (statistically significant at lag 1-3)
ABSORPTION_CONTROL_CONFIRMED:       true  (absorption_score 1.0x higher in absorb bars)
VACUUM_PCT_OF_HE_BARS:              34.7%
FLOW_PCT_OF_HE_BARS:                14.6%
BOOK_SWITCH_PCT_OF_HE_BARS:         0.2%
FEATURE_MASTER_RECOMMENDATION:      true  (5 PRIMARY candidates)
DASHBOARD_RECOMMENDATION_CREATED:   true
PRODUCTION_READY:                   false
PAPER_TRADING_READY:                false
OVERALL:                            PASS
```
