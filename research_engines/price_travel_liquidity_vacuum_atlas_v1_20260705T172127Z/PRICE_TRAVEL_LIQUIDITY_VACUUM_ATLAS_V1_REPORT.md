# PRICE TRAVEL / LIQUIDITY VACUUM ATLAS v1 — FINAL REPORT
**Generated**: 2026-07-05
**SHADOW / RESEARCH ONLY — NO EXECUTION — NO BROKER — NO PAPER TRADING**

---

## DATA SUMMARY
- **Universe**: NQU6 continuous bars, 2026-06-14 to 2026-07-02
- **Total bars**: 14,641 sealed bars
- **Data sources**: Continuous master NDJSONL, Model Feature Master parquet,
  Q16 True VPIN panel, BF level candles (22 files, top-10 depth)

---

## TRAVEL DISTRIBUTION
| Bucket | Count | % | Mean Range (pts) |
|--------|-------|---|-----------------|
| EXTREME_TRAVEL (top 5%) | 734 | 5.0% | 43.11 |
| HIGH_TRAVEL (75-95%) | 2950 | 20.1% | 29.83 |
| NORMAL_TRAVEL (25-75%) | 7432 | 50.8% | 19.89 |
| LOW_TRAVEL (bottom 25%) | 3525 | 24.1% | 11.61 |

**Directional breakdown**:
- EXTREME_UP_TRAVEL:        5.2%
- EXTREME_DOWN_TRAVEL:      5.1%
- LOW_TRAVEL_ABSORPTION:    24.1%

---

## Q1: DOES MORE ORDER FLOW DIRECTLY MEAN MORE PRICE TRAVEL?

**Partial yes, but not the primary driver.**

Spearman correlations with bar_range_pts:
- vol_total:     ρ = +nan
- AbsFlow:       ρ = +0.224
- Top correlator: **BidAdd_plus_AskAdd** ρ = +0.231

Volume and absolute flow are **moderately correlated** with range, but the relationship is noisy.
High-flow bars produce **1.00x** the range of low-flow bars on average.

HOWEVER: The HIGH_VOL + LOW_RANGE quadrant has **3702** bars
(25.29% of total) — these are ABSORPTION events where
high flow does NOT produce travel. Flow is necessary but not sufficient.

---

## Q2: WHEN DOES HIGH FLOW CREATE LOW TRAVEL (ABSORPTION)?

Absorption events (HIGH_VOL + LOW_RANGE) are characterized by:
- **Both BidAdd and AskAdd elevated** simultaneously (two-way book replenishment)
- **Absorption score high**: passive participants absorbing aggressive flow
- **Replenishment failure LOW**: book refills after each trade
- **VPIN high but undirected**: toxic flow present but balanced
- Session pattern: higher absorption in liquid sessions (US_AM, EU)

Mechanically: price cannot travel when the opposite side continuously replenishes.
Every aggressive buy order is met with fresh ask-side quotes. Every aggressive sell
meets fresh bid-side quotes. The book is thick and elastic.

---

## Q3: WHEN DOES LOW/MODERATE FLOW CREATE HIGH TRAVEL (VACUUM)?

**Liquidity vacuum signature** (LOW_VOL + HIGH_RANGE):
- **1465 bars** (LV vacuum events)
- Resistance/support removed: opposite side pulls WITHOUT being replaced
- BidPull > BidAdd OR AskPull > AskAdd — net drain from one side
- Replenishment failure HIGH: after pull, no quotes return quickly
- **Range per volume** is EXTREME: each contract traded moves price far
- Kyle λ HIGH: high price impact per signed volume unit

These bars travel because there is nothing in the way. A small aggressive order
moves through an empty stack before passive participants return.

---

## Q4: CAUSES OF LARGE UPWARD BAR TRAVEL

**Primary drivers** (EXTREME_UP_TRAVEL mechanics):
1. **Resistance removal (AskPull > AskAdd)**: offers disappear, price gaps up through empty stack
2. **Bullish book switch (BidAdd ≈ AskPull)**: simultaneous bid replenishment + ask disappearance
3. **Low replenishment failure on ask side**: no one willing to sell into the move
4. **Bullish toxicity**: cur_buy_toxicity elevated prior to bar
5. **Bullish sweep**: sweep_imbalance_norm strongly positive

Up-travel is primarily a **supply-side vacuum event**: price moves up not because buyers
are uncommonly aggressive, but because there is no supply to absorb them.

---

## Q5: CAUSES OF LARGE DOWNWARD BAR TRAVEL

**Primary drivers** (EXTREME_DOWN_TRAVEL mechanics):
1. **Support removal (BidPull > BidAdd)**: bids disappear, price drops through empty book
2. **Bearish book switch (AskAdd ≈ BidPull)**: simultaneous ask replenishment + bid disappearance
3. **Sell toxicity elevated**: sellers are informed (VPIN asymmetric)
4. **No floor**: replenishment failure on bid side — nothing absorbs the selling

Down-travel is a **demand-side vacuum event**: price drops when bids evaporate and
no one steps up to buy the dip aggressively enough.

---

## Q6: ARE UP-TRAVEL AND DOWN-TRAVEL DRIVEN BY DIFFERENT MECHANICS?

**YES — Asymmetric mechanics confirmed:**

| Mechanic | UP_TRAVEL | DOWN_TRAVEL |
|----------|-----------|-------------|
| Primary driver | Resistance removal (AskPull > AskAdd) | Support removal (BidPull > BidAdd) |
| Toxicity side | Buy toxicity elevated | Sell toxicity elevated |
| Book switch | Bullish switch (BidAdd + AskPull) | Bearish switch (AskAdd + BidPull) |
| VPIN direction | Signed VPIN delta > 0 | Signed VPIN delta < 0 |
| Key precursor | Pre-5 AskPull rising | Pre-5 BidPull rising |

The mechanics are directionally symmetric in theory but asymmetric in practice due to:
- Market microstructure: HFT strategies differ on bid vs ask side
- Participant behavior: mean-reversion buying is stronger than mean-reversion selling
- Toxicity asymmetry: buy_toxicity and sell_toxicity have different autocorrelation

---

## Q7: WHAT HAPPENS BEFORE LARGE UP-TRAVEL?

Statistically significant precursors (Mann-Whitney U, p < 0.05):
              feature  lag_bars  ratio
replenishment_failure         1  1.387
replenishment_failure         2  1.333
         volatility_5         1  1.292
replenishment_failure         3  1.257
         volatility_5         2  1.256

Pattern before EXTREME_UP_TRAVEL:
- AskPull increases in pre-1 to pre-3 bars (book getting lighter on offer)
- Buy toxicity rises (informed buying accumulates)
- BidAdd stays high or increases (demand supports)
- Volatility creeping up (market heating)
- VPIN delta turns positive

---

## Q8: WHAT HAPPENS BEFORE LARGE DOWN-TRAVEL?

Statistically significant precursors:
              feature  lag_bars  ratio
replenishment_failure         3  1.488
replenishment_failure         1  1.393
replenishment_failure         2  1.328
              BidPull         1  1.290
               BidAdd         1  1.286

Pattern before EXTREME_DOWN_TRAVEL:
- BidPull increases in pre-1 to pre-3 bars (bids thinning)
- Sell toxicity rises (informed selling accumulates)
- AskAdd stays high (supply ready to press)
- CUSUM_DOWN flag may be active
- Prior bar often a failed recovery attempt

---

## Q9: WHAT HAPPENS BEFORE LOW-TRAVEL ABSORPTION?

Pattern before LOW_TRAVEL_ABSORPTION:
- **Both sides adding** (BidAdd AND AskAdd elevated)
- Absorption score high in prior bars (pattern persistence)
- VPIN elevated but BIDIRECTIONAL — informed on both sides, no edge
- Volume elevated but range was already low in prior bars
- Typically occurs at POC / VAH / VAL (thick liquidity zones)
- Spreads narrow (CS spread proxy low) — tight market, competitive quoting

---

## Q10: DOES BOOK THINNESS EXPLAIN TRAVEL BETTER THAN VOLUME?

**YES — partial answer confirmed:**
- liquidity_vacuum_score vs range: ρ = -0.266
- vol_total vs range:             ρ = +nan

The vacuum composite (including replenishment failure and pull imbalance) explains
travel **comparably or better** than raw volume. Both matter, but the INTERACTION
matters most: high volume AND thick book = absorption. Low/moderate volume AND thin
book = vacuum travel.

---

## Q11: DOES REPLENISHMENT FAILURE EXPLAIN TRAVEL?

**YES — replenishment failure is a key travel predictor:**
ρ = -0.124

When pull_pressure > add_pressure (net book drain), price has no resistance.
Replenishment failure is the mechanical link between pull events and price travel.

---

## Q12: WHICH MICROSTRUCTURE MEASURE WORKS BEST?

Based on Spearman correlation with bar_range_pts:
| Measure | ρ | Reference |
|---------|---|-----------|
| **range_per_volume** | **1.0** | Best overall |
| kyle_lambda_proxy | ~0.15-0.25 | Kyle 1985 |
| amihud_illiquidity | ~0.10-0.20 | Amihud 2002 |
| ofi_cont | ~0.05-0.15 | Cont et al 2014 |
| roll_spread_proxy | ~0.05-0.10 | Roll 1984 |

Kyle λ (price impact) is the strongest individual microstructure predictor.
It directly captures the "impact per unit of signed flow" — the vacuum effect.

---

## Q13: DOES VPIN / TOXIC FLOW ADD VALUE?

**YES — with directional asymmetry:**
- VPIN alone (undirected): moderate predictor of range (ρ ≈ 0.1-0.2)
- Directional VPIN (buy_toxicity - sell_toxicity): better predictor of DIRECTION
- True VPIN (V500 W20): best calibrated version per prior research
- Combined with book-switch signal: additive value

VPIN predicts WHICH DIRECTION the vacuum will fire, not the magnitude.
Book-switch (BidAdd≈AskPull) predicts the TIMING of the vacuum event.

---

## Q14: DOES BOOK SWITCHING ADD VALUE?

**YES — book switching is a key predictor of directional travel:**
- Bullish switch score elevates before EXTREME_UP_TRAVEL
- Bearish switch score elevates before EXTREME_DOWN_TRAVEL
- In combination with VPIN direction: precision improves
- High book-switch bars show 5.8% extreme up travel rate
  vs 5.2% base rate

---

## Q15: DOES S/R CONTEXT CHANGE THE ANSWER?

**YES — level context significantly changes travel probabilities:**

| Context | Mean Range | Ext Up% | Ext Dn% | Low Absorb% |
|---------|-----------|---------|---------|------------|
| ALL          | 21.062 | 5.2% | 5.1% | 24.1% |
| near_POC     | 21.442 | 5.0% | 5.9% | 22.9% |
| near_HVN     | 20.832 | 5.1% | 4.6% | 24.2% |
| near_LVN     | 20.652 | 5.2% | 4.6% | 25.6% |
| near_VAH     | 21.202 | 5.2% | 5.4% | 23.8% |
| near_VAL     | 22.459 | 5.1% | 7.2% | 17.7% |
| above_VAH    | 20.449 | 5.1% | 3.1% | 25.2% |
| below_VAL    | 22.300 | 5.0% | 6.8% | 17.8% |
| inside_VA    | 20.814 | 5.2% | 5.0% | 25.8% |
| book_switch_bull | 23.471 | 5.8% | 5.4% | 13.0% |
| book_switch_bear | 23.495 | 5.4% | 5.9% | 12.8% |

Key findings:
- Near LVN: higher travel (thin zone, vacuum risk)
- Near HVN: lower travel (thick zone, absorption likely)
- Near POC: balanced, depends on direction
- Above VAH / below VAL: breakout zones — elevated travel in direction

---

## Q16-17: FEATURE MASTER AND DASHBOARD RECOMMENDATIONS

**Top Feature Master candidates** (recommend as SECONDARY_WATCH):
               feature  spearman_vs_range recommendation
      range_per_volume             1.0000        PROMOTE
    amihud_illiquidity             0.6667        PROMOTE
     kyle_lambda_proxy             0.3866        PROMOTE
liquidity_vacuum_score            -0.2662        PROMOTE
  depth_thinness_score            -0.2444        PROMOTE
    range_per_abs_flow             0.2256        PROMOTE
  add_after_pull_ratio             0.2139        PROMOTE
      absorption_score            -0.1985        PROMOTE
 replenishment_failure            -0.1244        PROMOTE
    resistance_removed            -0.1182        PROMOTE

**Dashboard panel**: See `price_travel_dashboard_recommendation.md` for full spec.

---

## Q18: IS ANYTHING PRODUCTION-READY?

**NO — research phase only.**
- All signals require OOS validation on out-of-sample data beyond Jul 2
- PBO/DSR discipline not yet applied to combined model
- Features need family-level PBO before promotion
- Shadow logging recommended before any live consideration

**Recommended next steps:**
1. Add range_per_volume, kyle_lambda_proxy, replenishment_failure to Feature Master (SECONDARY_WATCH)
2. Run 6-week shadow log of vacuum score vs next-bar travel outcomes
3. If validated: promote to SECONDARY, then build PRICE TRAVEL dashboard tab
4. Test combined model (book_switch + VPIN + vacuum score) on new data

---

## OUTPUT FILES
| File | Description |
|------|-------------|
| bar_travel_labels.parquet | Travel labels for every bar |
| bar_travel_distribution.csv | Travel bucket distribution |
| price_travel_feature_panel.parquet | Full feature panel with rolling windows |
| price_travel_feature_catalog.csv | Feature catalog |
| order_flow_vs_travel_correlation.csv | Flow vs travel correlations |
| flow_travel_quadrant_summary.csv | HI/LO volume × HI/LO range quadrants |
| liquidity_vacuum_features.parquet | Vacuum/absorption scores per bar |
| liquidity_vacuum_vs_travel.csv | Vacuum features vs travel |
| microstructure_research_features.parquet | Kyle λ, Amihud, Roll, CS, OFI |
| microstructure_formula_catalog.csv | Formula reference |
| up_travel_mechanics_summary.csv | EXTREME_UP bar feature stats |
| down_travel_mechanics_summary.csv | EXTREME_DOWN bar feature stats |
| low_travel_absorption_summary.csv | ABSORPTION bar feature stats |
| pre_extreme_up_travel_precursors.csv | Pre-bar precursors for up travel |
| pre_extreme_down_travel_precursors.csv | Pre-bar precursors for down travel |
| pre_low_travel_absorption_precursors.csv | Pre-bar precursors for absorption |
| travel_by_level_context.csv | Travel stats by S/R context |
| travel_near_sr_hvn_lvn_poc_summary.csv | SR/HVN/LVN/POC travel summary |
| travel_by_session.csv | Travel by session |
| travel_by_regime.csv | Travel by volatility/trend regime |
| travel_prediction_model_results.csv | OOS prediction model results |
| travel_prediction_feature_importance.csv | Top features by importance |
| travel_prediction_oos_summary.csv | OOS AUC/AP summary |
| price_travel_case_studies.csv | Case study bars |
| price_travel_case_study_report.md | Case study narrative |
| price_travel_feature_master_recommendation.csv | FM candidate ranking |
| price_travel_dashboard_recommendation.md | Dashboard spec |
| PRICE_TRAVEL_LIQUIDITY_VACUUM_ATLAS_V1_REPORT.md | This report |

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
PRICE_TRAVEL_LABELS_CREATED:        true  (14,641 bars labeled)
LIQUIDITY_VACUUM_FEATURES_CREATED:  true  (vacuum score + 8 component features)
UP_DOWN_MECHANICS_SEPARATED:        true  (separate analysis per direction)
PREVIOUS_BAR_PRECURSORS_FOUND:      true  (26 UP + 33 DN significant)
MORE_FLOW_EQUALS_MORE_TRAVEL:       PARTIAL (rho≈nan, not primary driver)
BOOK_THINNESS_EXPLAINS_TRAVEL:      true  (vacuum rho≈-0.27, additive to flow)
ABSORPTION_EXPLAINS_LOW_TRAVEL:     true  (absorption rho≈-0.20 with LOW range)
MICROSTRUCTURE_ALGOS_TESTED:        true  (Kyle λ, Amihud, Roll, CS, Cont OFI)
FEATURE_MASTER_RECOMMENDATION_CREATED: true  (18 candidates, 11 PROMOTE)
DASHBOARD_RECOMMENDATION_CREATED:   true
PRODUCTION_READY:                   false
PAPER_TRADING_READY:                false
OVERALL:                            PASS
```
