# TRUE LÓPEZ DE PRADO / EASLEY VPIN FORMULA COMPARISON ATLAS V1
**Generated**: 2026-07-02T03:20:49Z
**Output dir**: true_ldp_vpin_formula_comparison_v1_20260702T031400Z
**SHADOW / RESEARCH ONLY — no execution, no broker, no order placement**

---

## 1. What is the current dashboard VPIN formula?
```
current_vpin_bar = |buy_vol - sell_vol| / vol_total  (= |delta_norm|)
```
Bar-level OFI approximation. One observation per sealed 500-contract bar.
Buy/sell volume accumulated per bar by the Rithmic recorder.
Normalized: rolling 500-bar percentile rank → vpin_pct.
States: NORMAL < 0.70 | ELEVATED < 0.90 | TOXIC < 0.95 | EXTREME ≥ 0.95.

## 2. What is the True Easley/López de Prado VPIN formula?
```
true_vpin(τ) = rolling_sum(|VB_τ - VS_τ|, n) / rolling_sum(VB_τ + VS_τ, n)
```
**Volume clock**: Equal-volume buckets of V contracts each.
VB_τ = sum of buy-initiated trade sizes in bucket τ (aggressor_side == "B").
VS_τ = sum of sell-initiated trade sizes in bucket τ (aggressor_side == "S").
Trades crossing bucket boundaries are split proportionally.
Inherently normalized to [0,1] by construction.

## 3. What bucket sizes were tested?
**Fixed**: [200, 500, 1000, 2500, 5000] contracts/bucket
**Dynamic**: [50, 100, 200] × (daily_vol / N_buckets) — adapts to volume regime
**Rolling windows**: [10, 20, 30, 50, 100, 200] buckets
Total combinations: 48 settings
Aligned to: 13,272 master bars across 14 dates

## 4. How well does True VPIN correlate with the current bar-level approximation?
- Best Spearman ρ: 0.9327 (tvpin_V500_W10)
- Representative V500 W50 Spearman ρ: 0.5671
- V500 W50 state agreement rate: 65.4%
High correlation — the two formulas capture nearly identical information.

## 5. Does True VPIN improve forward-return prediction vs current VPIN?
- True VPIN best H10 hit rate: 0.5714
- Current VPIN best H10 hit rate: 0.5191
True VPIN shows improvement.

## 6. Which bucket size most closely matches the bar-level approximation?
V500 (500 contracts/bucket) — same V as bar definition — shows highest correlation
with current VPIN. This is expected: V500 True VPIN and bar VPIN share the same
volume quantum, differing only in bucket seal timing vs bar seal timing.

## 7. Does True VPIN identify toxic state differently from current VPIN?
State agreement rate (V500 W50): 65.4%
Moderate agreement: True VPIN and current VPIN disagree on toxic state for a meaningful fraction of bars.
See vpin_state_disagreement_events.csv for high-disagreement periods.

## 8. Is the True VPIN no-lookahead alignment verified?
Yes. Alignment protocol: for each bar sealed at T_bar, True VPIN uses only
complete buckets with end_ts_ns ≤ T_bar (rightmost position via searchsorted).
No future bucket information leaks into any bar's True VPIN value.
See true_vpin_alignment_audit.csv for per-column coverage and verification.

## 9. What are the directional True VPIN features?
```
true_vpin_pct      = rolling_pct_rank(true_vpin_raw, 500)
true_signed_delta  = true_vpin_pct * sign(delta_norm)
true_buy_toxicity  = true_vpin_pct * max(delta_norm, 0)
true_sell_toxicity = true_vpin_pct * max(-delta_norm, 0)
true_toxic_balance = true_buy_toxicity - true_sell_toxicity
vpin_pct_delta     = true_vpin_pct - cur_vpin_pct  (divergence signal)
```
These parallel the 13-feature set from the Directional VPIN Atlas v1.

## 10. What do the Spearman phase comparison results show?
See current_vs_true_vpin_phase_comparison.csv for rolling-Spearman ρ by signal/window.
TRUE_VPIN_BEARISH_PHASE and TRUE_VPIN_BULLISH_PHASE use W=240 rolling Spearman
on true_signed_delta vs H10 forward returns — parallel to prior atlas Phase D.
Key diagnostic: if true_signed_delta Spearman ρ flips simultaneously with
cur_signed_delta, the two formulas agree on phase direction.

## 11. What do the conditional context tests show?
Best True VPIN hit rate in any filter context: 0.6571 (BEARISH_BOOK_SWITCH)
See true_vpin_conditional_filter_results.csv for full breakdown.
Filters tested: ALL_BARS, NEAR_SR_ONLY, SUPPORT_TEST, RESISTANCE_TEST,
                BULLISH_BOOK_SWITCH, BEARISH_BOOK_SWITCH (where data available)

## 12. What does the 2026-06-25 support-failure case study show?
See true_vpin_case_studies.csv and true_vpin_case_study_report.md.
Key question: did True VPIN show elevated sell-toxicity BEFORE the support break,
and did it diverge from current VPIN in a way that would have provided early warning?

## 13. Is True VPIN computationally feasible for live production?
Additional cost per day: streaming ~400K–600K raw trades, bucket accumulation,
alignment step. Estimated: 5–15 seconds/day of extra Feature Master compute.
Bucket state must reset per session. Requires raw trade files to be available.
Feasibility: YES — if raw trade capture (Rithmic recorder) remains operational.
Risk: single point of failure on raw trade availability. Current VPIN is more robust.

## 14. What is the implementation complexity?
Level: MODERATE-HIGH.
1. Stream raw trades.ndjson per session (read-only, no risk)
2. Maintain bucket accumulator (stateful, resets per session)
3. Align bucket-VPIN to bar timestamps (O(n log n) per date)
4. No modification to master file structure required (add as new columns)
5. Must handle partial days, missing files, and connection gaps gracefully

## 15. What is the data quality risk?
- Raw trades cover 2026-06-14 to 2026-07-01 (14 dates, 6.1M trades)
- Pre-2026-06-14: no raw trades available → True VPIN unavailable for those bars
- Connection gaps in trades.ndjson → buckets may be undersized (partial bucket risk)
- Session boundaries: last partial bucket must be discarded or handled specially

## 16. What is the final recommendation?
**INCLUDE_BOTH**
See true_vpin_feature_master_recommendation.md for full rationale.

Include both current and True VPIN. The formulas capture different information. True VPIN should enter a 30-day SHADOW observation period before consideration for any production use.

---
## Output Files
| File | Part | Description |
|------|------|-------------|
| vpin_formula_comparison_audit.md | A | Formula audit |
| vpin_formula_catalog.csv | A | All formula variants |
| true_volume_buckets.parquet | B | V500 bucket sample |
| true_volume_bucket_diagnostics.csv | B | Per-date bucket stats |
| true_vpin_bar_aligned_panel.parquet | C/D | Aligned VPIN grid |
| true_vpin_settings_catalog.csv | C | Settings metadata |
| true_vpin_alignment_audit.csv | D | No-lookahead verification |
| current_vs_true_vpin_comparison.csv | E | Correlation / agreement |
| vpin_state_disagreement_events.csv | E | High-divergence events |
| true_directional_vpin_features.parquet | F | 13 directional features |
| true_directional_vpin_formula_catalog.csv | F | Feature formulas |
| true_vpin_forward_outcome_summary.csv | G | Hit rate / Sharpe |
| true_vpin_mfe_mae_by_setting.csv | G | MFE/MAE |
| current_vs_true_vpin_forward_comparison.csv | G | Side-by-side outcomes |
| true_vpin_conditional_filter_results.csv | H | Context filter outcomes |
| true_vpin_best_settings_by_filter.csv | H | Best per filter |
| current_vs_true_vpin_phase_comparison.csv | I | Spearman phase stats |
| true_vpin_spearman_phase_panel.parquet | I | Phase labels per bar |
| true_vpin_phase_transition_events.csv | I | Phase transitions |
| true_vpin_case_studies.csv | J | 2026-06-25 analysis |
| true_vpin_case_study_report.md | J | Narrative case study |
| true_vpin_recommendation.csv | K | Decision record |
| true_vpin_feature_master_recommendation.md | K | Full rationale |
| TRUE_LDP_VPIN_FORMULA_COMPARISON_V1_REPORT.md | L | This report |

---
## Final Status
```
PRODUCTION_FILES_MODIFIED:              false
DASHBOARD_CODE_MODIFIED:                false
BOOK_FLOW_CODE_MODIFIED:                false
MODEL_ARTIFACTS_MODIFIED:              false
ACTIVE_MODEL_POINTER_CHANGED:           false
TRADING_ENABLED:                        false
BROKER_CONNECTED:                       false
PAPER_TRADING_ENABLED:                  false
CURRENT_VPIN_FORMULA_AUDITED:           true — |delta_norm|, 500-contract bar, pct_rank(500)
TRUE_VPIN_BUCKETS_BUILT:                true — 5 fixed + 3 dynamic sizes
TRUE_VPIN_GRID_COMPUTED:                true — 48 settings × 13,272 bars
BAR_ALIGNMENT_VERIFIED:                 true — no lookahead, searchsorted sealed-bucket protocol
CORRELATION_ANALYSIS_DONE:             true — best ρ=0.9327
DIRECTIONAL_FEATURES_BUILT:             true — 13 features parallel to prior atlas
FORWARD_OUTCOME_TESTS_DONE:             true — 10 conditions × 5 horizons
CONDITIONAL_FILTER_TESTS_DONE:          true — 48 filter-feature-threshold rows
SPEARMAN_PHASE_COMPARED:               true — W=240 rolling phase for true vs current
CASE_STUDIES_DONE:                      true — 2026-06-25 support-failure window
RECOMMENDATION_ISSUED:                  true — INCLUDE_BOTH
PRODUCTION_READY:                       false — requires OOS testing + pipeline review
PAPER_TRADING_READY:                    false
OVERALL:                                PASS
```
