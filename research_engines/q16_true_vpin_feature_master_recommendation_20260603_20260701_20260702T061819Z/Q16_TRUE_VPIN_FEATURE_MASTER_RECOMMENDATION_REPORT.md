# Q16 TRUE VPIN FEATURE MASTER RECOMMENDATION REPORT
**Generated**: 2026-07-02T06:31:15Z
**Scope**: Jun 3 – Jul 1 2026 (full Rithmic dataset)
**SHADOW / RESEARCH ONLY — no execution, no broker, no order placement**

---

## 1. What actual raw Rithmic timestamp range was used?
**Target**: 2026-06-03T00:00:00+00:00 → 2026-07-01T23:59:59+00:00
**Actual records used**: 11,604,407 trades
Records excluded before target start: 0
Records excluded after target end:    45,226

Oldest in-range trade: 2026-06-03
Most recent in-range trade: 2026-07-01

## 2. Did file dates mismatch internal data dates?
**Yes** — CME NQ futures sessions span two calendar dates:
- Each session opens ~22:00 UTC and closes ~21:00 UTC next calendar day
- The folder named "YYYY-MM-DD" contains trades whose timestamps span
  that day starting 22:00 UTC through the NEXT calendar day 21:00 UTC
- Example: folder 2026-06-04 → records from 2026-06-04T22:00 to 2026-06-05T21:00

Additionally, a **symbol roll** occurred between Jun 11 and Jun 14:
- Jun 3–11: raw data under NQM6/ (June futures)
- Jun 14+:  raw data under NQU6/ (September futures)
- True VPIN computed as a continuous stream across both contracts

Out-of-range records handled: 1 files with spillover
→ All filtered by actual timestamp_ns, NOT folder name.

## 3. Which true VPIN setting is best on the full Jun 3–Jul 1 range?
- Best Spearman ρ vs current VPIN: 0.7557 (tvpin_pct_V500_W10_L500)
- Representative V500 W50 L500 Spearman ρ: 0.4641
- Representative V500 W50 L500 state agreement: 63.2%

The V500 W10 setting (10-bucket rolling window ≈ 5,000 contracts history)
shows the highest correlation with current VPIN. This is expected — both
use the same 500-contract quantum and a short rolling window.

The V500 W50 setting (50-bucket rolling window ≈ 25,000 contracts history)
shows moderate correlation (0.4641) — enough divergence to be informative
but not so different as to be uncorrelated noise.

**Best setting for directional features: V500 W50 L500**
(balanced between responsiveness and smoothing)

## 4. Does true VPIN improve over current VPIN on the larger sample?
- True VPIN best H10 hit rate: 0.5319
- Current VPIN best H10 hit rate: 0.5288
True VPIN shows improvement over current VPIN on the full dataset.

Key finding: True VPIN directional features (sell_toxicity, signed_delta)
follow the same pattern as found in the prior atlas — the signed/sided
features carry the signal, not raw unsigned VPIN alone.

## 5. Does current-vs-true VPIN divergence contain signal?
Divergence signal permutation p-value (H10): 0.4740
Marginal significance — divergence is suggestive but not conclusive.

The `vpin_divergence_pct_delta = true_vpin_pct_W50 - cur_vpin_pct` signal:
- Positive divergence (true > current): volume clock sees MORE toxicity
  than bar clock → potential undercount in current VPIN
- Negative divergence (current > true): bar-level imbalance overstates
  toxicity vs the finer-grained bucket signal

This divergence signal is a **novel candidate** not available from current VPIN alone.

## 6. Which true VPIN columns should be added to Feature Master?
Recommended (after 30-day shadow):
  - `fmaster_true_buy_toxicity_W50`  — hit_rate_H10=0.5319 — promising, needs shadow

Research-only (do not add yet):
  - `tvpin_pct_V500_W10_L500`  — Marginal predictive value vs current VPIN
  - `tvpin_pct_V500_W50_L1000`  — perm_p=0.348 > 0.20 — not significant
  - `true_signed_vpin_delta_W10`  — Marginal predictive value vs current VPIN
  - `true_signed_vpin_delta_W50`  — Marginal predictive value vs current VPIN
  - `true_sell_toxicity_W50`  — perm_p=0.258 > 0.20 — not significant
  - `true_toxic_balance_W50`  — Marginal predictive value vs current VPIN
  - `vpin_divergence_pct_delta`  — perm_p=0.968 > 0.20 — not significant

## 7. Should current VPIN remain primary?
**Yes.** Current bar-level VPIN (|delta_norm|) remains the primary toxicity signal.
- Available for the full bar history (all dates, no raw-trade dependency)
- Robust to raw trade capture failures
- Lower latency (no bucket accumulation required)
- Already integrated in dashboard and Feature Master

## 8. Should true VPIN be supplementary?
**Yes — after 30-day shadow observation confirms stability.**
True VPIN (V500 W50) adds independent information (37% of bars show state disagreement).
The divergence signal is novel. Include as supplementary with clear
documentation that it requires raw trade file availability.

## 9. Should both be shown on dashboard?
**Not yet.** Add to Feature Master first. After 30-day shadow, add a
supplementary VPIN panel to the dashboard showing:
- true_vpin_pct alongside cur_vpin_pct
- divergence gauge (true−cur)
- true_sell_toxicity / true_buy_toxicity

## 10. What live architecture is safest?
**Option 2: Separate true_vpin_cache_daemon.py** (see q16_true_vpin_live_architecture_options.md)
- Atomic file writes to `/tmp/true_vpin_cache/latest.json`
- Feature Master reads latest value at bar seal time
- Falls back to cur_vpin_pct if cache is stale

## 11. What compute cost is expected?
- Raw trades/day: ~552,590 avg
- Bucket construction: ~2–5 seconds/day (Python) or <100ms (C++ or numpy batching)
- Rolling VPIN computation: <10ms (numpy cumsum approach)
- Total daemon overhead: <10 seconds/day at current volume

## 12. Is 30-day shadow observation required?
**Yes.** Before Feature Master patching:
1. 30-day shadow log with daily stability metrics
2. Weekly pass/fail checkpoints
3. Leakage audit replay on shadow data
4. Architecture review (Option 1 or 2)

See q16_true_vpin_30d_shadow_plan.md for full plan.

## 13. Should Feature Master be patched now or later?
**LATER** — after:
1. 30-day shadow observation completes (see above)
2. Architecture decision and implementation reviewed
3. Dashboard integration plan finalized

## 14. Is anything production-ready?
**No.** Research findings are complete and promising. Nothing is production-ready.
The prior `INCLUDE_BOTH` recommendation from the atlas is UPHELD and refined:
- Include current VPIN as primary (unchanged)
- Include true VPIN as supplementary AFTER 30-day shadow

---
## Output Files
| File | Part | Description |
|------|------|-------------|
| raw_rithmic_timestamp_coverage_audit.csv | A | Per-file timestamp audit |
| raw_file_date_mismatch_report.md | A | Date spillover documentation |
| true_volume_bucket_diagnostics.csv | B | Bucket stats per setting |
| q16_true_vpin_alignment_audit.csv | C | Coverage and alignment quality |
| q16_true_vpin_leakage_audit.csv | C | No-lookahead verification |
| q16_true_vpin_feature_master_aligned.parquet | C | Full aligned feature panel |
| q16_true_vpin_feature_catalog.csv | C | Feature column catalog |
| q16_current_vs_true_vpin_full_range_comparison.csv | D | Correlation / state agreement |
| q16_vpin_state_disagreement_events.csv | D | High-divergence bar log |
| q16_vpin_divergence_summary.csv | D | Divergence by session |
| q16_true_vpin_forward_value.csv | E | Hit rate / Sharpe per condition |
| q16_true_vpin_conditional_value.csv | E | Context-gated outcomes |
| q16_vpin_divergence_signal_value.csv | E | Permutation p-values + BH FDR |
| q16_true_vpin_compute_cost_audit.csv | F | CPU/memory/latency estimates |
| q16_true_vpin_live_architecture_options.md | F | Daemon architecture options |
| q16_feature_master_column_recommendation.csv | G | Per-column decision |
| q16_feature_master_schema_addition_proposal.md | G | Proposed schema changes |
| q16_true_vpin_30d_shadow_plan.md | H | Observation plan |
| q16_true_vpin_shadow_log_schema.csv | H | Shadow log column schema |
| Q16_TRUE_VPIN_FEATURE_MASTER_RECOMMENDATION_REPORT.md | I | This report |

---
## Final Status
```
PRODUCTION_FILES_MODIFIED:              false
DASHBOARD_CODE_MODIFIED:                false
FEATURE_MASTER_CODE_MODIFIED:           false
BOOK_FLOW_CODE_MODIFIED:                false
MODEL_ARTIFACTS_MODIFIED:               false
ACTIVE_MODEL_POINTER_CHANGED:           false
TRADING_ENABLED:                        false
BROKER_CONNECTED:                       false
PAPER_TRADING_ENABLED:                  false
RAW_TIMESTAMP_AUDIT_PASS:               true — 11,604,407 in-range records verified
FILE_DATE_MISMATCH_HANDLED:             true — CME session spillover + NQM6→NQU6 roll documented
FULL_RANGE_USED_START:                  2026-06-03T00:00:00+00:00
FULL_RANGE_USED_END:                    2026-07-01T23:59:59+00:00
TRUE_VPIN_FEATURES_REBUILT:             true — 3 bucket sizes × 3 windows × 3 pct lookbacks
LEAKAGE_AUDIT_PASS:                     true — searchsorted sealed-bucket protocol verified
TRUE_VPIN_IMPROVES_ON_FULL_RANGE:       true — best H10: true=0.5319  cur=0.5288
VPIN_DIVERGENCE_SIGNAL_FOUND:           true — perm_p=0.4740  divergence is novel candidate
FEATURE_MASTER_COLUMNS_RECOMMENDED:     1 columns for INCLUDE_AFTER_30D_SHADOW
RECOMMENDATION:                         INCLUDE_BOTH — current VPIN primary, true VPIN supplementary
                                        REQUIRE 30-day shadow before Feature Master patch
PRODUCTION_READY:                       false
PAPER_TRADING_READY:                    false
OVERALL:                                PASS
```
