# VPIN Z-Score + Book Flow Dominance / Market Switch Atlas v1
**SHADOW / RESEARCH ONLY — NO EXECUTION — NO BROKER — NO PAPER TRADING**
NQU6 / Model Feature Master | Generated: 2026-07-12T205328Z | 19,286 bars

---

## 1. What does positive VPIN z-score mean?

vpin_resid_z20 = (VPIN − rolling_mean_20) / rolling_std_20. VPIN is **unsigned**.
Positive z = elevated toxicity above recent baseline. Someone trades aggressively.
Does NOT specify buyers or sellers.

| State | Count | Pct |
|-------|-------|-----|
| EXTREME_NEG (z<=−2) | 648 | 3.4% |
| NEG (−2<z<=−1) | 3836 | 19.9% |
| NEUTRAL (−1<z<1) | 10217 | 53.0% |
| POS (1<=z<2) | 3606 | 18.7% |
| EXTREME_POS (z>=2) | 979 | 5.1% |

---

## 2. What does negative VPIN z-score mean?

Toxicity BELOW recent baseline. Lower-information phase. Does not mean sellers winning.

---

## 3. Does VPIN z-score alone predict price direction?

**NO — essentially no directional edge.**

- VPIN_Z_POSITIVE: hit_rate=0.500, mean_ret=0.23 pts, n=4585
- VPIN_Z_NEGATIVE: hit_rate=0.517, mean_ret=0.35 pts, n=4481
- Spearman(vpin_z20, H5): rho=-0.010, p=0.1594

All hit rates ~50%, p >> 0.05. VPIN needs Book Flow direction to generate edge.

---

## 4. Does VPIN z-score amplify Book Flow dominance?

**Marginally — all states near 50% H5 hit rate in isolation.**

| State | Freq | H5 Result |
|-------|------|-----------|
| HIGH_TOXIC_BUYER_CONTROL | 1861 (9.7%) | hit_rate=0.507, mean_ret=-0.22 pts, n=1861 |
| HIGH_TOXIC_SELLER_CONTROL | 1878 (9.7%) | hit_rate=0.488, mean_ret=0.18 pts, n=1878 |
| LOW_TOXIC_BUYER_CONTROL | 2099 (10.9%) | hit_rate=0.516, mean_ret=0.09 pts, n=2098 |
| LOW_TOXIC_SELLER_CONTROL | 1966 (10.2%) | hit_rate=0.511, mean_ret=0.39 pts, n=1966 |
| NEUTRAL_BUYER_CONTROL | 3952 (20.5%) | hit_rate=0.500, mean_ret=-1.05 pts, n=3950 |
| NEUTRAL_SELLER_CONTROL | 4059 (21.1%) | hit_rate=0.483, mean_ret=0.16 pts, n=4057 |
| TOXIC_CONFLICT | 846 (4.4%) | hit_rate=0.509, mean_ret=1.33 pts, n=846 |
| LOW_TOXIC_CHOP | 419 (2.2%) | hit_rate=0.552, mean_ret=1.47 pts, n=417 |
| NEUTRAL_BALANCED | 2206 (11.4%) | hit_rate=0.503, mean_ret=-0.23 pts, n=2205 |

**Key finding:** No combined state shows meaningful standalone edge.
Context (VAL proximity, seller exhaustion, Z4 vacancy) matters far more.

---

## 5. VPIN positive + buyers dominate?

**HIGH_TOXIC_BUYER_CONTROL**: hit_rate=0.507, mean_ret=-0.22 pts, n=1861

Hit rate near 50% alone. Best contexts: VAL reclaim + seller exhaustion + resistance_consumption > 0.

---

## 6. VPIN positive + sellers dominate?

**HIGH_TOXIC_SELLER_CONTROL**: hit_rate=0.488, mean_ret=0.18 pts, n=1878

Informed sellers winning. Best bearish context: near VAH from below (failed breakout).

---

## 7. VPIN negative + buyers dominate?

**LOW_TOXIC_BUYER_CONTROL**: hit_rate=0.516, mean_ret=0.09 pts, n=2098

Buyer trap archetype (bar 18924). Passive buyers; sellers replenish overhead.
Hit rate 51.6% — slight positive but inconsistent.

---

## 8. VPIN negative + sellers dominate?

**LOW_TOXIC_SELLER_CONTROL**: hit_rate=0.511, mean_ret=0.39 pts, n=1966

Passive selling / market-making. Watch for S2B switch: resistance_consumption > 0,
bullish_switch_score rising, mlofi_norm_lag_1 very negative.

---

## 9. What identifies buyer-to-seller switch?

**2934.00 B2S events**. H1=0.40 pts, H5=0.58 pts.
VPIN z20 at switch: 0.04. Pre-3-bar Signed: 90.92.

Leading indicators: (1) Signed turns negative, (2) seller_pressure overtakes buyer_pressure,
(3) resistance_consumption < 0 (sellers re-posting), (4) book_switch_net turns negative,
(5) support_consumption > 0 (bid support consumed)

---

## 10. What identifies seller-to-buyer switch?

**2954.00 S2B events**. H1=-0.01 pts, H5=-0.61 pts.
VPIN z20 at switch: 0.04. Pre-3-bar Signed: -88.12.

Leading indicators: (1) Signed rises to positive, (2) buyer_pressure overtakes seller_pressure,
(3) resistance_consumption > 0 (sellers pulling), (4) mlofi_lag1 very negative (exhaustion),
(5) bullish_switch_score rising, (6) close_location = 1.0

---

## 11. Does book_switch_net lead price turns?

Spearman(book_switch_net, H5): rho=0.001, p=0.9498

Minimal standalone correlation. Useful as confirmation, not leading indicator.

---

## 12. Does support/resistance consumption lead price turns?

Spearman(support_consumption, H5): rho=-0.004, p=0.5877
Spearman(resistance_consumption, H5): rho=-0.000, p=0.9841

No standalone Spearman edge across all bars. Context-specific: at VAL/VAH/POC
with dist_to_val_ticks <= 60, consumption is meaningful. Always combine with level.

---

## 13. Best continuation state?

                   state    n  hit_rate  mean_return  mfe_mae_ratio
          LOW_TOXIC_CHOP  417    0.5516       1.4688         0.9720
         VPIN_Z_NEGATIVE 4481    0.5168       0.3485         0.9801
 LOW_TOXIC_BUYER_CONTROL 2098    0.5157       0.0870         0.9704
LOW_TOXIC_SELLER_CONTROL 1966    0.5107       0.3900         0.9921
          TOXIC_CONFLICT  846    0.5095       1.3319         1.0500

Note: LOW_TOXIC_CHOP at 55.2% = only 417 bars (2.17% freq). All near 50%.

---

## 14. Best reversal / exhaustion state?

                    state    n  hit_rate  mean_return  mfe_mae_ratio
    TOXIC_SELLER_TO_BUYER  535    0.4542      -2.1771         0.8703
          VPIN_Z_CROSS_UP 1431    0.4759      -1.0491         0.9600
    TOXIC_BUYER_TO_SELLER  535    0.4804      -0.2921         0.9967
   NEUTRAL_SELLER_CONTROL 4057    0.4829       0.1643         1.0135
HIGH_TOXIC_SELLER_CONTROL 1878    0.4878       0.1824         1.0073

States < 50% hit_rate favor short positions from that bar's close.

---

## 15. Which states work near S/R?

See `best_vpin_bookflow_state_by_context.csv`.
- NEAR_VAL: HIGH_TOXIC_BUYER_CONTROL strongest
- NEAR_VAH: HIGH_TOXIC_SELLER_CONTROL or B2S switch
- SUPP_TEST: S2B switch strongest
- RESI_TEST: HIGH_TOXIC_SELLER_CONTROL strongest
- INSIDE_VA: all near base rate
- ABOVE_VAH / BELOW_VAL: HIGH_TOXIC states best filter

---

## 16. Which states work near VAL/VAH/POC/HVN/LVN?

| Level | Best state | Logic |
|-------|-----------|-------|
| VAL reclaim | HIGH_TOXIC_BUYER_CONTROL | Informed buying reclaiming support |
| VAH test from below | HIGH_TOXIC_BUYER_CONTROL | Breakout needs toxicity |
| VAH test from above | HIGH_TOXIC_SELLER_CONTROL | Informed rejection |
| POC | NEUTRAL states | Watch switch direction |
| HVN | LOW_TOXIC | Absorption zone; watch consumption for breakout |
| LVN | HIGH_TOXIC | Vacuum zone; fast travel |

---

## 17. Features to add to Feature Master?

| Feature | Priority | Formula | Computable Now |
|---------|----------|---------|----------------|
| buyer_pressure | HIGH | ofild_last_BidAdd + ofild_last_AskPull | YES |
| seller_pressure | HIGH | ofild_last_AskAdd + ofild_last_BidPull | YES |
| buyer_seller_balance | HIGH | buyer_pressure - seller_pressure | YES |
| buyer_seller_ratio | MEDIUM | buyer_pressure / (seller_pressure + 1) | YES |
| bookflow_direction | HIGH | BUYER_DOMINANT/SELLER_DOMINANT/BALANCED/CONFLICT | YES |
| vpin_bookflow_state | HIGH | vpin_z20 x bookflow_direction x Signed combined label | YES |
| buyer_to_seller_switch | HIGH | prev=BUYER_DOMINANT AND cur=SELLER_DOMINANT | YES |
| seller_to_buyer_switch | HIGH | prev=SELLER_DOMINANT AND cur=BUYER_DOMINANT | YES |
| support_consumption | HIGH | BidPull - BidAdd (>0 = bid support consumed) | YES |
| resistance_consumption | HIGH | AskPull - AskAdd (>0 = ask resistance cleared) | YES |
| vpin_z60 | MEDIUM | (VPIN - roll_mean_60) / roll_std_60 | YES |
| vpin_z120 | MEDIUM | (VPIN - roll_mean_120) / roll_std_120 | YES |
| vpin_z20_cross_up | MEDIUM | prev_z20 < 0 AND cur_z20 > 0 | YES |
| vpin_z20_cross_down | MEDIUM | prev_z20 > 0 AND cur_z20 < 0 | YES |
| w_buyer_pressure | MEDIUM | sum_window_BidAdd + sum_window_AskPull | YES |
| w_seller_pressure | MEDIUM | sum_window_AskAdd + sum_window_BidPull | YES |

---

## 18. Dashboard display?

See `vpin_bookflow_dashboard_recommendation.md`.
Panel: VPIN BOOK FLOW CONTROL — VPIN z20 + state + buyer/seller pressure + dominance
+ switch event indicator + combined state label. Color coded GREEN/RED/YELLOW/GRAY.

---

## 19. Production-ready?

**No. SHADOW / RESEARCH ONLY.**

No state provides standalone directional edge > 55% at H5 across all bars.
Value is in COMBINATION with price-level context (VAL/VAH/consumption signals).

Next steps: add buyer_pressure/seller_pressure/bookflow_direction to FM daemon;
display vpin_bookflow_state as read-only label; validate Rule5+Rule8 on OOS data.

---

## Files Generated

| File | Part | Description |
|------|------|-------------|
| vpin_zscore_formula_catalog.csv | A | Formula audit |
| vpin_zscore_state_counts.csv | A/B | VPIN z20 distribution |
| vpin_zscore_panel.parquet | B | z20–z480 + crossing events |
| bookflow_dominance_formula_catalog.csv | C | Dominance formulas |
| bookflow_dominance_panel.parquet | C | Buyer/seller pressure + consumption |
| vpin_bookflow_combined_states.parquet | D | Combined state panel |
| vpin_bookflow_state_counts.csv | D | State frequencies |
| vpin_zscore_forward_outcomes.csv | E | VPIN z-score outcomes H1-H80 |
| vpin_bookflow_combined_forward_outcomes.csv | E | All state outcomes H1-H80 |
| vpin_bookflow_mfe_mae_results.csv | E | MFE/MAE by state/horizon |
| buyer_seller_switch_events.csv | F | 5888 switch events |
| buyer_seller_switch_lead_lag_summary.csv | F | Switch statistics |
| vpin_bookflow_conditional_results.csv | G | Context-conditional tests |
| best_vpin_bookflow_state_by_context.csv | G | Best state per context |
| vpin_bookflow_spearman_phase_results.csv | H | Spearman phase map |
| vpin_bookflow_phase_transition_events.csv | H | Rolling Spearman events |
| recent_vpin_bookflow_case_study.csv | I | Latest 40-bar snapshot |
| recent_vpin_bookflow_case_study_report.md | I | Case study narrative |
| vpin_bookflow_rule_candidates.csv | J | Rule backtests |
| vpin_bookflow_rule_test_summary.md | J | Rule narrative |
| vpin_bookflow_feature_master_recommendation.csv | K | FM proposal |
| vpin_bookflow_dashboard_recommendation.md | K | Dashboard spec |
| VPIN_ZSCORE_BOOKFLOW_DOMINANCE_SWITCH_ATLAS_V1_REPORT.md | L | This report |

---

## Final Status Fields

```
PRODUCTION_FILES_MODIFIED:               false
DASHBOARD_CODE_MODIFIED:                 false
FEATURE_MASTER_CODE_MODIFIED:            false
BOOK_FLOW_CODE_MODIFIED:                 false
MODEL_ARTIFACTS_MODIFIED:                false
ACTIVE_MODEL_POINTER_CHANGED:            false
TRADING_ENABLED:                         false
BROKER_CONNECTED:                        false
PAPER_TRADING_ENABLED:                   false

VPIN_ZSCORE_FORMULA_AUDITED:             true
BOOKFLOW_DOMINANCE_FEATURES_CREATED:     true
VPIN_BOOKFLOW_STATES_CREATED:            true (9 states, 19286 bars)
BUYER_SELLER_SWITCH_EVENTS_CREATED:      true (5888 events: 2934 B2S + 2954 S2B)
VPIN_ZSCORE_PREDICTIVE_ALONE:            false (hit rate ~50%, Spearman p=0.16)
VPIN_AMPLIFIES_BOOKFLOW:                 marginal without level context
BEST_CONTINUATION_STATE:                 LOW_TOXIC_CHOP (55.2%, n=417)
BEST_BUYER_CONTROL_STATE:                HIGH_TOXIC_BUYER_CONTROL (conditioned on VAL+consumption)
BEST_SELLER_CONTROL_STATE:               HIGH_TOXIC_SELLER_CONTROL (conditioned on VAH+consumption)
KEY_FINDING:                             standalone VPIN-BookFlow states near 50% hit rate;
                                         must combine with VAL/VAH proximity + consumption signals
BUY_BAR_LINK:                            dash_vpin_pct 0.076 vs 0.892 (82 pct-pt gap) is primary
                                         discriminator between buyer trap and buyer continuation
REUSABLE_RULES:                          Rule5 ToxicBuyerAtVAL, Rule8 HighToxicBuyer_VALReclaim
FEATURE_MASTER_RECOMMENDATION_CREATED:   true (16 features, all from existing OFILD cols)
DASHBOARD_RECOMMENDATION_CREATED:        true
PRODUCTION_READY:                        false
PAPER_TRADING_READY:                     false
OVERALL:                                 PASS
```
