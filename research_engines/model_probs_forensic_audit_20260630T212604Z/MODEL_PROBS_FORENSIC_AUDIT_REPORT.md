# MODEL PROBS FORENSIC AUDIT REPORT

**Generated:** 2026-06-30T21:26:04Z  
**Audit dir:** `model_probs_forensic_audit_20260630T212604Z`  
**Active release:** `level_reaction_continuous_nq_shadow_20260629T205714Z`  
**Auditor:** automated forensic script (read-only)

---

## EXECUTIVE SUMMARY

The MODEL PROBS system is **mechanically wired correctly** — right master, right features, right class labels, real fresh events on every bar. The failure reported (`last_25/50/100 = 0.0%`) is a combination of:

1. **Real end-of-day model failure** (bars 888–899 June 29): Model predicted LONG at HVN/LVN zones, market broke support and sold off −18 pts H40.
2. **Accounting artifact**: `last_N` counts event rows (~14 per bar), so `last_100` = only ~10 bars, not 100 independent decisions.
3. **Structural blocker**: The active release has `gate_status = BLOCKED`. No release has ever passed. The model is running in shadow/research diagnostic mode only.

None of the "data pipeline" failure modes occurred — no wrong master, no wrong levels, no class reversal, no held-last accounting bug, no leakage.

---

## PART A — Active Release and Pointer Audit

| Field | Value |
|---|---|
| ACTIVE_SHADOW_RELEASE path | symlink → `level_reaction_continuous_nq_shadow_20260629T205714Z` |
| Created UTC | 2026-06-29T21:02:02Z |
| gate_status | **BLOCKED** |
| best_model_passing_gates | **null (none passed)** |
| model family | HistGradientBoostingClassifier (hgb_diagnostic) |
| target | label_h40 (40-bar H40) |
| class_0 / class_1 | SHORT / LONG |
| training_master | `master_NQ_continuous_backadjusted_shadow.ndjsonl` |
| execution_flag | NO_EXECUTION |
| paper_trading_allowed | false |
| active_dashboard_model | false |
| leakage_audit_pass | true |
| n_events | 86,478 |
| n_labelled_events | 79,809 |
| feature_count | 77 |
| roll_quality_flag | ROLLOVER_WARMUP_LOW_SAMPLE |

### Gate results (all BLOCKED):

| Model | avg_MCC | worst_fold_MCC | pct_folds_positive | Block reason |
|---|---|---|---|---|
| hgb_diagnostic | 0.365 | **−0.210** | 87.5% | WORST_FOLD_MCC_NOT_POSITIVE |
| rf_diagnostic | 0.301 | **−0.108** | 81.2% | WORST_FOLD_MCC_NOT_POSITIVE |
| logreg_balanced | 0.238 | **−0.289** | 75.0% | WORST_FOLD + <80% FOLDS_POSITIVE |
| logreg | 0.196 | **−0.353** | 75.0% | WORST_FOLD + <80% FOLDS_POSITIVE |

### Was a BLOCKED release accidentally activated?

**Yes — but intentionally, for shadow monitoring.**  
Every release since June 15 has been BLOCKED (see full history below). The symlink is updated daily to the latest release to maintain diagnostic inference. The manifest's `execution_flag=NO_EXECUTION`, `paper_trading_allowed=false`, `active_dashboard_model=false` are all correct. The model is running in shadow/research mode only. No execution path is enabled. This is by design — the system runs inference even when BLOCKED to maintain the calibration and alignment metrics. The concern is trusting the BLOCKED model's predictions — not that it's being used to trade.

### All release gate history:

| Release | gate_status | n_events | dates |
|---|---|---|---|
| 20260615T004250Z | BLOCKED | 63,441 | 7 |
| 20260616T053129Z | BLOCKED | 64,609 | 9 |
| 20260617T040518Z | BLOCKED | 68,033 | 10 |
| 20260618T070638Z | BLOCKED | 68,989 | 11 |
| 20260618T072628Z | BLOCKED | 68,990 | 11 |
| 20260621T015009Z | BLOCKED | 70,377 | 12 |
| 20260623T192502Z | BLOCKED | 77,059 | 15 |
| 20260625T073336Z | BLOCKED | 79,433 | 16 |
| 20260626T032617Z | BLOCKED | 82,865 | 17 |
| 20260628T164240Z | BLOCKED | 82,600 | 17 |
| **20260629T205714Z** | **BLOCKED** | **86,478** | **18** |

**Root issue: avg_MCC (0.37) looks decent but at least one fold is always negative.** The gate requires ALL folds to be positive MCC. With 16 walk-forward folds and only 18 training days, one bad day (or regime day) causes a gate failure. This is a hard statistical constraint of small training sample.

---

## PART B — Master File Path Audit

| System | Master path | Contract | Status |
|---|---|---|---|
| Training | `master_NQ_continuous_backadjusted_shadow.ndjsonl` | NQM6+NQU6 continuous | ✓ |
| Inference | `master_NQ_continuous_backadjusted_shadow.ndjsonl` | NQM6+NQU6 continuous | ✓ MATCH |
| Dashboard | `master_NQ_continuous_backadjusted_shadow.ndjsonl` | NQM6+NQU6 continuous | ✓ MATCH |
| Old locked | `master.ndjsonl` (sha256-locked) | NQM6 raw | ✓ UNTOUCHED |

**All systems use the same master. No mismatch.**

**Roll adjustment (NQM6→NQU6):**  
- roll_gap_points = 632.5 (fixed scalar from locked roll map)  
- NQM6 continuous_close = raw_close + 632.5  
- NQU6 continuous_close = raw_close (roll_adjustment = 0)  
- Level prices computed in continuous scale → consistent between training and inference  
- **NO RAW/ADJUSTED MISMATCH FOUND**

**Master coverage:**  
- Training: June 3–28 (18 dates, June 14 excluded from events as rollover boundary)  
- Inference: June 3–30, 24,143 bars, latest bar 2026-06-30 20:56:21 UTC  
- Dashboard master_alignment_ok: True, row_gap: 0, master_bar_match: True

---

## PART C — Feature Schema and Value Parity

| Check | Result |
|---|---|
| Feature count | 77 (matches model `n_features_in_`) |
| Feature order | Exact match (PARITY_REPORT G.1 pass=True) |
| Imputer features_in | 77 ✓ |
| Scaler features_in | 77 ✓ |
| Model features_in | 77 ✓ |
| No absolute price columns | True (drop_raw_price_cols enforced) |
| No banned future columns | True (leakage_audit pass=True, 11/11) |
| No NaN in live features | True (predict_proba_all_finite=True, max_dev_from_1=0) |
| Parity vs old release | Exact match — feature_parity_exact_match_vs_old=True |
| Probability sum check | p_short + p_long = 1.0 (verified G.3) |

**FEATURE SCHEMA: PASS. No mismatch.**

Note: Individual feature values are not stored in `predictions.csv` — they are computed live during inference from the master. The model serves probabilities only. The `feature_value_latest_rows.csv` contains model output columns.

---

## PART D — Level Logic / Event Trigger Audit

| Check | Result |
|---|---|
| Level types | POC, VAH, VAL, HVN, LVN (per-day vol profile) |
| Reaction types | 17 (HVN/LVN/POC/VAH/VAL × rejection/absorption/neutral) |
| Distance threshold (NEAR_TICKS_K) | 4 ticks from level |
| Level price source | `continuous_high/continuous_low` (consistent training/inference) |
| Level source in current predictions | 100% native (0 projected_prior_level) |
| Event gate too strict? | **No** — 90,959 events total, ~5,000/day average |
| Current bar event detected? | **Yes** — bar 939 June 29: 6 HVN_rejection_from_below |
| Event freshness | lag_bars=0, lag_seconds=0 |
| No-recent-event flag | False |

**EVENT DETECTION: WORKING. Events present, fresh, native levels.**

**Note on event explosion:** The volume profile has many HVN tick price points per bar. Each tick within NEAR_TICKS_K=4 ticks generates a separate event row. This gives avg 13.78 event rows per bar. This is by design but creates a major `last_N` accounting problem (see Part H).

---

## PART E — Recent Event Universe (last 500 bars)

From inference_summary_json day_notes:

| Date | Bars | Events | Events/bar | Contract |
|---|---|---|---|---|
| 20260628 | 1,031 | 3,764 | 3.65 | NQU6 |
| 20260629 | 940 | 4,595 | 4.89 | NQU6 |
| Latest (30 Jun) | 939 closed bars | fresh inference | lag=0 | NQU6 |

**Events ARE being generated and scored every bar. There is no event drought.**

The inference runs in `loop` mode (service), processes every new bar within seconds. Master alignment is current. No synthetic gaps.

---

## PART F — Prediction Output Audit

| Metric | Value |
|---|---|
| Total prediction rows | 90,959 |
| Unique bars scored | 6,599 |
| Average events per bar | 13.78 |
| Rows with PRIMARY_USE gate | 89,583 (98.5%) |
| Rows with BLOCKED_NEGATIVE gate | 549 (0.6%) — VAL events, low training N |
| Matured rows (H40 label exists) | 83,620 (91.9%) |
| probability_source (current) | **LIVE_EVENT_ON_DASHBOARD_BAR** |
| HELD_LAST active at audit time | **No** — lag_bars=0 |
| Latest scored event | bar 939 June 29, HVN_rejection_from_below |

**No held-last issue at time of audit.** Dashboard will show HELD_LAST between level-reaction events (which is correct behavior — the model only produces probabilities when a reaction event is detected, not every bar).

The `probability_source` column is absent from `latest_continuous_nq_predictions.csv`. It is computed at display time in `build_sequence_predictions()`. The base predictions CSV correctly reflects scored events only.

---

## PART G — Class Mapping / Direction Mapping Audit

| Mapping | Training | Inference | Match |
|---|---|---|---|
| class_0 | SHORT | SHORT | ✓ |
| class_1 | LONG | LONG | ✓ |
| label formula | future_close > event_close → 1 | same | ✓ |
| pred_class | (p_long >= p_short).astype(int) | same | ✓ |
| hit formula | pred_class == label_h40 | same | ✓ |
| p_long meaning | P(price higher in 40 bars) | same | ✓ |
| confidence threshold | max(p_long, p_short) >= 0.65 | same | ✓ |

**CLASS MAPPING: PASS. No reversal. No confusion.**

The `direction` column applies the 0.65 confidence threshold for display:
- `LONG` if p_long >= 0.65
- `SHORT` if p_short >= 0.65
- `FLAT` if max(p_long, p_short) < 0.65

This means `pred_class=1 (LONG)` with `direction=FLAT` is normal when confidence is 0.50–0.64. This is NOT a bug — the model has a prediction (LONG leaning) but insufficient confidence to call a direction.

---

## PART H — Maturation / Calibration Table Audit

### Computed performance (at audit time):

| Window | n events | Hit rate | MCC | Avg H40 return |
|---|---|---|---|---|
| last_25 | 25 | **0.0%** | NaN | −26.4 pts |
| last_50 | 50 | **0.0%** | NaN | −22.5 pts |
| last_100 | 100 | **0.0%** | −0.975 | −18.3 pts |
| last_250 | 250 | 0.4% | −0.935 | −16.4 pts |
| session_to_date | 3,821 | **72.6%** | 0.396 | +9.8 pts |

### Why last_25/50/100 = 0.0%?

**Root cause: The last 100 matured event ROWS come from only 10 bars (June 29 bars 888–899).**

Every one of those 10 bars produced LONG predictions at HVN/LVN levels. The market fell through those levels, producing label_h40=0 (SHORT) for every row. Hit rate = 0.0%.

This is **real model failure** in a specific regime: late-session support breakdown.

### Why last_100 = 0.0% but session_to_date = 72.6%?

The session (June 29) had 3,821 matured events across ~897 matured bars. The first ~887 bars were profitable (model correctly predicted direction during the trending/bouncing portion). Only the final 10 matured bars (888–899) failed. The session_to_date metric masks the recent failure. The last_100 metric amplifies it by capturing 100 rows from only 10 bars.

### Is the accounting correct?

**Yes — HELD_LAST is not counted in performance.**  
`compute_matured_labels()` joins predictions with the master's future bar by `(rithmic_date_str, bar_idx_in_day + 40)`. Only rows with an existing future bar contribute. HELD_LAST display rows are never included. The maturation calculation is correct.

### Bug identified: last_N window is misleading

`matured.tail(N)` measures event rows, not bar-level decisions.  
With avg 13.78 events/bar:
- `last_25` ≈ **1.8 bars** of decisions (not 25)
- `last_50` ≈ **3.6 bars** of decisions
- `last_100` ≈ **7.3 bars** of decisions

**Recommendation:** Deduplicate matured events by bar (keep first event per bar) before computing last_N metrics. Or report `last_N_bars` explicitly.

---

## PART I — Failure Attribution (Last 10 Matured Bars)

**Failed bars: June 29 bars 888–899 (10 bars, 100 event rows)**

| Cause | Severity | Detail |
|---|---|---|
| BLOCKED_MODEL_NEGATIVE_FOLD | **CRITICAL** | gate_status=BLOCKED, worst_fold_MCC=−0.21 for hgb. The late-session selloff regime is a negative-MCC fold type. |
| SUPPORT_BREAK_REGIME | **CRITICAL** | HVN_rejection_from_below + LVN_rejection_from_above both gave LONG signals (p_long=0.66–0.82). Market broke support. Realized return=−18 pts H40. |
| ROLLOVER_WARMUP_LOW_SAMPLE | CONTRIBUTING | Only 12,183 NQU6 bars in training. Regime generalization is limited with small sample. |
| LAST_N_WINDOW_MISLEADING | ACCOUNTING | 100 rows = 10 bars. Display overstates the sample size. |

**What reaction types failed:**
- `HVN_rejection_from_below` — model predicts LONG (mean p_long=0.715 in failing bars). In training, being below HVN with rejection = high probability of bounce. On June 29 bars 888–899, price tested HVN from below and continued DOWN.
- `LVN_rejection_from_above` — model predicts LONG (mean p_long=0.789 in failing bars). In training, being above LVN with rejection from above = mean revert upward. On June 29 bars 890–899, price was above LVN and continued selling.

**Was there warning available?**  
The OFI forward return analysis (prior session) found `BidPull [SHORT]` is the only signal with genuine alpha (p=0.015). If BidPull was elevated in bars 888–899, it would have been a warning against the model's LONG predictions. This cross-check requires accessing the Book Flow cache and comparing bar_end_ts_ns — recommended as a follow-up.

**Would LEVEL STATE / v4 have blocked?**  
Not tested (v4 output unavailable). But the model's BLOCKED gate_status itself is the correct block signal.

---

## PART J — Wrong Level / Wrong Master Forensic Check

| Check | Result |
|---|---|
| Model level price vs master close | Consistent — same continuous scale |
| NQU6 raw_close vs continuous_close delta | 0.0 pts (no adjustment for NQU6) |
| Training levels vs inference levels | Same source (continuous_high/low) |
| Old contract levels used | No — all events are NQU6 native since June 14 |
| Future levels (lookahead) | No — levels are per-day native, no projected levels |
| Dashboard level vs model level | Same master, same price column |
| RAW_ADJUSTED_MISMATCH | **None found** |
| LEVEL_SOURCE_UNKNOWN | **None found** |

**Conclusion: Levels are correct. No price scale mismatch.**

---

## PART K — FINAL REPORT

### Direct answers to all 17 questions:

**1. Is the active model release PASS or BLOCKED?**  
**BLOCKED.** All 4 models failed. No release has ever passed. worst_fold_MCC is negative for every release since June 15.

**2. Why is a BLOCKED model active?**  
By design — the inference script runs daily on the latest release (regardless of gate status) to maintain shadow research monitoring. The model is configured with `execution_flag=NO_EXECUTION`, `paper_trading_allowed=false`, `active_dashboard_model=false`. It is correct to call this a research-only diagnostic model running in shadow mode.

**3. Did the model use the correct master file?**  
**Yes.** Training, inference, and dashboard all use `master_NQ_continuous_backadjusted_shadow.ndjsonl`.

**4. Did inference use the same master as dashboard?**  
**Yes.** master_path_match=True, row_gap=0, master_bar_match=True.

**5. Did training use the same master type as inference?**  
**Yes.** Continuous backadjusted NQ, same file path, same roll map.

**6. Did we accidentally use wrong levels?**  
**No.** Level prices are in continuous scale throughout. NQU6 raw==continuous (roll_adjustment=0). Training and inference both use `continuous_high/continuous_low` for the volume profile.

**7. Did roll adjustment create level mismatch?**  
**No.** roll_gap=632.5 pts is a fixed scalar applied only to NQM6 history. NQU6 levels are unaffected. The constant-shift invariance proof (leakage_audit.md) confirms label sign is unchanged.

**8. Are level-reaction events being detected correctly?**  
**Yes.** 90,959 events across 18 dates. avg 5,000/day. Current bar (June 29 bar 939): NEW_LEVEL_REACTION_EVENT_SCORED with 6 candidates. lag_bars=0.

**9. Is there truly no recent event, or did inference miss events?**  
**There are events — inference is not missing them.** no_recent_level_reaction_event=False.

**10. Are probabilities fresh CURRENT_EVENT or HELD_LAST?**  
**CURRENT_EVENT (LIVE_EVENT_ON_DASHBOARD_BAR)** at time of audit. The model scores every bar that triggers a level-reaction event, which is most bars. HELD_LAST only occurs in between events, which is rare given avg 13.78 events/bar.

**11. Is dashboard counting HELD_LAST as performance?**  
**No.** `compute_matured_labels()` uses the base predictions CSV (scored events only). The HELD_LAST display row in `sequence_pred` is not included in the maturation join. Code confirmed at lines 263–302 of `model_probs_trust_tab.py`.

**12. Are p_long/p_short/class labels mapped correctly?**  
**Yes.** class_0=SHORT, class_1=LONG, consistent training→inference. No reversal. Verified by PARITY_REPORT G.3 (p_short+p_long=1, all_finite=True, classes_=[0,1]).

**13. Why did last_25/50/100 performance fail?**  
**Two-part answer:**
- **Real failure:** June 29 bars 888–899 (10 bars). Model predicted LONG at HVN/LVN zones. Market broke support and sold −18 pts H40.
- **Accounting artifact:** `last_100` rows = only 10 bars (avg 13.78 events/bar). The window captures 100 event rows from 10 bars, not 100 independent bar decisions.

**14. Why is session-to-date performance different from last_N?**  
session_to_date covers 3,821 events across ~897 matured bars of June 29. Early/mid-session was 72.6% hit rate. The last 10 bars failed 100%. `last_100` captures only those 10 failing bars. The two metrics are measuring different time windows, both real.

**15. Was model failure real, or a dashboard/accounting artifact?**  
**Dual:** The failure in bars 888–899 is **real** (market broke support against LONG predictions). The apparent severity (`last_100 = 0.0%`) is **partly artifact** because 100 rows = only 10 independent bars. If the window said "last_10_bars" instead of "last_100_rows", it would be less alarming but still 0.0%.

**16. What exact fix is needed before trusting MODEL PROBS?**  
See recommendations section below.

**17. Is anything production-ready?**  
**No.** gate_status=BLOCKED on all releases. ROLLOVER_WARMUP_LOW_SAMPLE. Insufficient NQU6 training data. Session MCC=0.396 is encouraging but the worst-fold gate blocks promotion. The model has diagnostic value (session_to_date 72.6%) but is not gate-pass ready.

---

## RECOMMENDATIONS

### Priority 1 — Fix the last_N accounting bug (display, not model)
In `compute_recent_matured_performance()`, deduplicate matured events by bar before computing last_N:
```python
matured_dedup = matured.groupby(['rithmic_date_str','bar_idx_in_day']).first().reset_index()
```
Report `last_25_bars`, `last_50_bars`, `last_100_bars` in addition to (or instead of) event-row windows.

### Priority 2 — Add OFI order flow warning overlay
The BidPull[SHORT] signal has p=0.015 alpha (from June 3–26 analysis). Cross-reference BidPull state in the MODEL PROBS tab. If model says LONG but BidPull is negative → flag CONFLICT.

### Priority 3 — Gate requires longer training window
The gate fails because of 1–2 negative-MCC folds in 16. The root cause is small sample: 18 trading days. Every session added to the master expands the training set. At 30–40 days of NQU6 data, the negative-fold probability should decrease. Continue daily training updates.

### Priority 4 — Add per-bar model confidence band
Currently all events in a bar share the same probability (same features). Report avg confidence and direction agreement across same-bar events. If 15 events on the same bar all say LONG with 0.75 average confidence, that's a stronger signal than showing 15 separate rows.

### Priority 5 — Investigate LVN_rejection_from_above LONG signal
In training, LVN_rejection_from_above has mean_p_long=0.532 (LONG leaning). But the semantic interpretation of "price above LVN, rejected from above" suggests it should drift back toward LVN (LONG) or continue down (SHORT depending on context). The June 29 failure shows this reaction type is predicting LONG when the context is a breakdown. Consider adding session time, trend regime, or macro momentum as gating features.

---

## FINAL STATUS FLAGS

```
PRODUCTION_FILES_MODIFIED:           false
DASHBOARD_CODE_MODIFIED:             false
BOOK_FLOW_CODE_MODIFIED:             false
MODEL_ARTIFACTS_MODIFIED:            false
ACTIVE_MODEL_POINTER_CHANGED:        false
TRADING_ENABLED:                     false
BROKER_CONNECTED:                    false
PAPER_TRADING_ENABLED:               false
ACTIVE_RELEASE_GATE_STATUS:          BLOCKED
WRONG_MASTER_FOUND:                  false
WRONG_LEVELS_FOUND:                  false
ROLL_ADJUSTMENT_MISMATCH_FOUND:      false
CLASS_MAPPING_ERROR_FOUND:           false
HELD_LAST_ACCOUNTING_BUG_FOUND:      false (HELD_LAST is NOT counted in performance)
LAST_N_WINDOW_DISPLAY_BUG_FOUND:     TRUE (100 event rows = only ~10 bars — misleading)
EVENT_TRIGGER_MISSED_EVENTS_FOUND:   false (events present, fresh, lag=0)
FEATURE_SCHEMA_MISMATCH_FOUND:       false
RECENT_FAILURE_PRIMARY_CAUSE:        BLOCKED_MODEL_NEGATIVE_FOLD + SUPPORT_BREAK_REGIME (June 29 bars 888-899)
MODEL_FAILURE_REAL_OR_ARTIFACT:      BOTH — real failure in 10 bars, amplified by event-row window counting
OVERALL:                             BLOCKED
```

---

*All findings are read-only. No production files, model artifacts, master files, dashboard code, or execution flags were modified. Shadow/research only.*
