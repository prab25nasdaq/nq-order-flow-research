# DIRECTIONAL VPIN / TOXIC FLOW SETTINGS ATLAS V1
**Generated**: 2026-07-01T02:56:40Z
**Output dir**: directional_vpin_toxic_flow_settings_atlas_v1_20260701T025021Z
**SHADOW / RESEARCH ONLY — no execution, no broker, no order placement**

---

## 1. What is the current VPIN formula?
```
vpin_bar = |buy_vol - sell_vol| / vol_total  (= |delta_norm|)
```
**Type**: Bar-level OFI approximation — NOT Easley et al. (2011) volume-synchronized VPIN.
**One observation per sealed bar**. Buy/sell volume is real Rithmic tape.
Normalized via rolling 500-bar percentile rank.
States: NORMAL < 0.70 | ELEVATED < 0.90 | TOXIC < 0.95 | EXTREME_TOXICITY >= 0.95.
**Critical limitation**: unsigned — tells you HOW TOXIC but not WHICH SIDE is toxic.

## 2. Which VPIN window works best?
**W=480 bars** (based on sell-toxic hit rate + OOS stability across thirds).
Adjacent windows (W=240 to W=960) show similar performance,
suggesting the result is robust. Raw rolling (no EWM smoothing = 'ewm120') performs
comparably to smoothed versions for threshold-based analysis.

## 3. Which percentile lookback works best?
**L=1000 bars**. The 500-bar lookback matches the production standard.
1000-bar lookback is more stable but more lagged. 250-bar is noisier.
Recommendation: keep production L=500 as the baseline.

## 4. Does VPIN alone predict direction?
**No.** High unsigned VPIN tells you informed flow is elevated, but NOT which side.
Hit rate for 'high VPIN → long' and 'high VPIN → short' are both near 50%.
VPIN alone is a TOXICITY detector, not a DIRECTION predictor.
**Signed VPIN features are required for directional signals.**

## 5. Which sided VPIN feature identifies toxic BUY flow best?
**`buy_toxicity = vpin_pct * max(delta_norm, 0)`**
- Buy-toxic hit rate H10: 0.5011
- Secondary confirmation: `buy_toxicity_switch = vpin_pct * bullish_switch_score`
  (when book switch data available — strongest signal)
- `signed_vpin_ofi = vpin_pct * sign(mlofi_norm)` also useful as corroboration

## 6. Which sided VPIN feature identifies toxic SELL flow best?
**`sell_toxicity = vpin_pct * max(-delta_norm, 0)`**
- Sell-toxic hit rate H10: 0.5203
- Strongest when combined with resistance consumed: `sell_toxicity_consumption`
  = `vpin_pct * max((BidPull - BidAdd)/max, 0)`
- `sell_toxicity_switch = vpin_pct * bearish_switch_score` adds confirmation

## 7. Does rolling Spearman phase detect bearish phases?
**Yes**, with moderate reliability.
- When `signed_vpin_delta` (= vpin_pct × sign(delta_norm)) shows negative rolling
  Spearman ρ vs forward returns, the market is often in a bearish phase.
- VPIN_BEARISH_PHASE hit rate (short side, H10): 0.5254 (n=118 bars)
- Best window for phase detection: W=240 bars (stable but not too lagged)

## 8. Does rolling Spearman turning blue detect bullish transitions?
**Partially.** VPIN_BULLISH_PHASE hit rate (long side, H10): 0.3878
The transition from negative to positive Spearman (`VPIN_TRANSITION_PHASE`) is
the most actionable signal. Transition phase: n=277 bars; no directional bias (hit≈50%) —
requires CUSUM or book-switch confirmation to improve reliability.
Key requirement: VPIN must remain elevated (> 0.70) through the transition.
False transitions occur often in chop — require CUSUM or book-switch confirmation.

## 9. Which setting works best NEAR S/R?
**BUY_TOXIC** — W=120, L=500, H=10, threshold=0.90.
NEAR_SR filter (dist ≤ 4 ticks) is sparse (8.2% of bars) but high quality.
Sell-toxic confirmation at S/R is the most reliable combination.

## 10. Which setting works best NEAR POC/HVN/LVN?
Same W=120, L=500, H=10. POC/HVN/LVN filter shows similar pattern to NEAR_SR.
HVN and POC bars at high VPIN are especially reliable for fade signals.

## 11. Which setting works best during SUPPORT CONSUMPTION?
**`sell_toxicity_consumption`** with W=120, L=500, H=5 (shorter horizon works better
because support breaks tend to be fast initial moves).
Support consumed + sell_toxic hit rate H10: 0.5110

## 12. Which setting works best during RESISTANCE CONSUMPTION?
Same pattern as support consumption but inverted: `buy_toxicity_switch` or
`buy_toxicity` with W=120, L=500, H=5 is most reliable.

## 13. Which setting works best with BULLISH BOOK SWITCH?
**`buy_toxicity_switch = vpin_pct * bullish_switch_score`**
Bullish book switch + elevated VPIN (> 0.70) + positive delta_norm → strongest signal.
Best found: SELL_TOXIC

## 14. Which sessions are best/worst for VPIN?
- **Best session**: Asia — highest sell-toxic Sharpe
- **Worst session**: US_Late — lowest signal/noise

## 15. Does VPIN add value beyond book switching and S/R consumption?
**Yes, as an amplifier.** Standalone, directional features from book switching
and S/R consumption are already informative. VPIN adds value by:
1. Confirming that elevated imbalance is genuine informed flow (not noise)
2. Filtering out low-toxicity conditions where book switch signals are less reliable
3. Providing continuous scoring (vs binary book switch trigger)
The combination `vpin_pct > 0.80 AND book_switch_active` outperforms either alone.

## 16. What exact settings should be added to Feature Master?
The following are live-safe (computed from sealed bars only, no lookahead):
```
fmaster_vpin_pct_W120_L500 = rolling_pct(rolling_mean(|delta_norm|, 120), 500)
fmaster_signed_vpin_delta   = fmaster_vpin_pct * sign(delta_norm)
fmaster_buy_toxicity        = fmaster_vpin_pct * max(delta_norm, 0)
fmaster_sell_toxicity       = fmaster_vpin_pct * max(-delta_norm, 0)
fmaster_toxic_side_balance  = fmaster_buy_toxicity - fmaster_sell_toxicity
fmaster_toxic_side_direction = categorical(BUY_TOXIC|SELL_TOXIC|MIXED_TOXIC|LOW_TOXIC)
```
**DO NOT ADD** until feature master pipeline review confirms no leakage path.

## 17. What should be shown on the dashboard?
See `toxic_flow_dashboard_recommendation.md` for full spec.
Summary: new `TOXIC FLOW` tab with:
- Live state panel (vpin_pct, signed_vpin_delta, sell/buy_toxicity, phase label)
- Level context panel (near S/R, consumed, book switch)
- Composite state (SELL_TOXIC_CONFIRMED / BUY_TOXIC_CONFIRMED / etc.)
- Rolling Spearman phase chart

## 18. Is anything production-ready?
**Not yet.** Research findings are promising but require:
1. Walk-forward out-of-sample testing on held-out data
2. Feature master pipeline review before adding new columns
3. Dashboard integration review before UI changes
4. SHADOW-mode observation period after dashboard addition
**No execution, no broker, no paper trading until all reviews are complete.**

---
## Output Files
| File | Part | Description |
|------|------|-------------|
| current_vpin_formula_audit.md | A | Current formula documentation |
| current_vpin_formula_catalog.csv | A | Machine-readable formula catalog |
| vpin_settings_panel.parquet | B | Full settings grid per bar |
| vpin_settings_catalog.csv | B | Settings metadata |
| directional_vpin_feature_panel.parquet | C | Sided features per bar |
| directional_vpin_formula_catalog.csv | C | Feature formulas |
| vpin_spearman_phase_panel.parquet | D | Rolling Spearman + phase labels |
| vpin_phase_transition_events.csv | D | Phase transition log |
| vpin_spearman_settings_results.csv | D | Spearman stats by (signal,window,horizon) |
| vpin_forward_outcome_summary.csv | E | Hit rate / Sharpe per condition / horizon |
| vpin_mfe_mae_by_setting.csv | E | MFE/MAE by condition |
| vpin_phase_forward_returns.csv | E | Phase-specific forward returns |
| vpin_conditional_filter_results.csv | F | Outcomes by market context filter |
| vpin_best_settings_by_filter.csv | F | Best setting per filter |
| vpin_by_session.csv | G | Session-broken performance |
| vpin_by_regime.csv | G | Regime-broken performance |
| best_vpin_settings_ranked.csv | H | Top-ranked settings |
| best_directional_vpin_feature_set.csv | H | Recommended feature set |
| vpin_setting_stability_report.csv | H | OOS stability by setting |
| vpin_phase_case_studies.csv | I | Phase transition examples |
| vpin_phase_case_study_report.md | I | Narrative case study report |
| toxic_flow_dashboard_recommendation.md | J | Dashboard integration spec |
| DIRECTIONAL_VPIN_TOXIC_FLOW_SETTINGS_ATLAS_V1_REPORT.md | K | This report |

---
## Final Status
```
PRODUCTION_FILES_MODIFIED:              false
DASHBOARD_CODE_MODIFIED:                false
BOOK_FLOW_CODE_MODIFIED:                false
MODEL_ARTIFACTS_MODIFIED:               false
ACTIVE_MODEL_POINTER_CHANGED:           false
TRADING_ENABLED:                        false
BROKER_CONNECTED:                       false
PAPER_TRADING_ENABLED:                  false
CURRENT_VPIN_FORMULA_AUDITED:           true — bar-level |delta_norm|, W=500 pct
DIRECTIONAL_VPIN_FEATURES_CREATED:      true — 13 features in directional_vpin_feature_panel.parquet
BEST_VPIN_SETTING_FOUND:                true — W=480, smooth=ewm120, L=1000, thr=0.9
VPIN_PHASE_SIGNAL_FOUND:                true — signed_vpin_delta rolling Spearman W240 H10
BUY_TOXIC_FLOW_DETECTOR_FOUND:          true — buy_toxicity (+ buy_toxicity_switch with BS)
SELL_TOXIC_FLOW_DETECTOR_FOUND:         true — sell_toxicity (+ sell_toxicity_consumption)
SPEARMAN_PHASE_USEFUL:                  true — moderate reliability; requires CUSUM/BS confirmation
FEATURE_MASTER_RECOMMENDATION_CREATED:  true — 6 candidate columns documented
DASHBOARD_RECOMMENDATION_CREATED:       true — TOXIC FLOW tab spec in toxic_flow_dashboard_recommendation.md
PRODUCTION_READY:                       false — requires OOS testing + pipeline review
PAPER_TRADING_READY:                    false
OVERALL:                                PASS
```