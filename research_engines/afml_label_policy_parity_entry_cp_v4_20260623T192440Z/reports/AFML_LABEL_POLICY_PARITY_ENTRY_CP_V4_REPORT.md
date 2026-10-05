AFML LABEL-POLICY-PARITY ENTRY + CLOSE-POSITION MODEL v4 - FINAL REPORT
=============================================================================
ENGINE_DIR: /home/prabh/OFI_Production/research_engines/afml_label_policy_parity_entry_cp_v4_20260623T192440Z
BUILD_DATE: 2026-06-23
STATUS: SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING / NO DATABENTO

CORRECTION HONORED: no dashboard tab was created or modified, no Book Flow
chart code was touched, the EXISTING production model (artifacts, release
pointer, training config) was never modified - only READ from its frozen
release files. predictions.csv was loaded once for SCHEMA reference only
and never used as ground truth anywhere in this build (see Part B - the
label-policy rebuild compares against the existing model's OWN frozen
TRAINING artifacts, data/level_reaction_events.parquet +
labels_level_reaction.parquet, never predictions.csv).

=============================================================================
1. WHAT EXACT LABELING METHOD DOES THE EXISTING DASHBOARD MODEL USE?
=============================================================================
outputs/existing_model_label_policy_catalog.md / .csv

LEVEL_REACTION_EVENT_TRIGGERED_FIXED_HORIZON_DIRECTION. NOT triple-barrier
(no PT/SL anywhere). A bar produces an EVENT only if it touches/closes
within 4 ticks of a per-day POC/VAH/VAL/HVN/LVN level AND matches one of 5
causal reaction rules (rejection_from_above/below, absorption,
breakout_acceptance_above/breakdown_acceptance_below, neutral_touch). The
LABEL itself (label_h40) is the SIGN of the forward log-return at a FIXED
+40-bar horizon (day-bounded), with NEUTRAL (zero-sign) events dropped.
Embargo = 40 bars (== label horizon). Folds: purged walk-forward BY
CALENDAR DAY, expanding window - NOT random K-fold.

Reaction-type gate status (existing model's own rule: n<10->SMALL_N,
10<=n<50->EXPLORATORY_SMALL_N, n>=50 then hgb_diagnostic per-reaction
mcc>0.02->PRIMARY_USE, <-0.02->BLOCKED_NEGATIVE, else SECONDARY_WATCH):
           reaction_type  n_training training_gate_status
HVN_rejection_from_below       26686          PRIMARY_USE
HVN_rejection_from_above       26206          PRIMARY_USE
LVN_rejection_from_below        7890      SECONDARY_WATCH
LVN_rejection_from_above        7875     BLOCKED_NEGATIVE
POC_rejection_from_above         496          PRIMARY_USE
POC_rejection_from_below         485      SECONDARY_WATCH
VAL_rejection_from_above         186     BLOCKED_NEGATIVE
VAH_rejection_from_below         165          PRIMARY_USE
VAL_rejection_from_below         160     BLOCKED_NEGATIVE
VAH_rejection_from_above         108          PRIMARY_USE
          HVN_absorption          89          PRIMARY_USE
          LVN_absorption          13  EXPLORATORY_SMALL_N
       HVN_neutral_touch          10  EXPLORATORY_SMALL_N
          POC_absorption           4              SMALL_N
       LVN_neutral_touch           2              SMALL_N
          VAH_absorption           1              SMALL_N
          VAL_absorption           1              SMALL_N

Honest note: the existing model's OWN release gate_status is "BLOCKED" -
every one of its 4 trained models (logreg/logreg_balanced/hgb_diagnostic/
rf_diagnostic) had a negative worst-fold MCC and never passed its own
promotion gate. v4 extends this model's labeling METHOD with a much larger
sample and modern feature set - it does not inherit a "previously
validated" baseline.

=============================================================================
2. DID THE REBUILT LABELS MATCH THE EXISTING LABEL POLICY?
=============================================================================
outputs/label_policy_parity_report.csv

YES - EXACT match on the overlapping training-date region:
  row_count_rebuilt_on_training_dates  = 70377
  row_count_existing_on_training_dates = 70377
  reaction_type_match_rate  = 100.0%
  event_timestamp_match_rate = 100.0%
  label_h40_match_rate      = 100.0%
  side_match_rate           = 100.0%
  mismatch examples captured: 0

LABEL_POLICY_PARITY_PASS: True (every check above is a 100% match - the
rebuild is a faithful, line-for-line reproduction of pipeline_continuous.py
applied independently to this engine's own fresh master snapshot)

The rebuild additionally extends ['2026-06-21', '2026-06-22', '2026-06-23']
- 3 calendar days strictly AFTER the existing model's own training cutoff -
producing 6624 more events than existed at training time.

=============================================================================
3-4. HOW MANY SAMPLES VS v3 CUSUM? DID v4 FIX v3's SAMPLE STARVATION?
=============================================================================
outputs/raw_candidates_v4.parquet, deduped_candidates_v4.parquet,
candidate_uniqueness_v4.csv

  v3 (dashboard CUSUM h=5.0):  36 CUSUM events -> 19 S/R-gated directional candidates
  v4 (existing label policy):  13500 raw directional NQU6 events -> 3773 deduped candidates

V3_SAMPLE_STARVATION_FIXED: True
(3773 deduped candidates is a 199x increase over v3's 19)

Effective independent sample count (AFML Ch.4 average uniqueness):
  v3 entry: 17.0  (== raw N exactly - CUSUM events never overlap)
  v4 entry: 85.1  (~5x v3's effective N)

Honest caveat: v4's raw/deduped counts are far larger than v3's, but the
AFML-effective-N (accounting for temporal overlap of DIFFERENT levels
touched close together in time, a phenomenon distinct from the same-level
duplicate-burst problem dedup fixes) is "only" ~85, not ~3,700 - the
40-bar horizon applied to a dense multi-level touch process means many
candidates remain temporally correlated even after removing same-level
bursts. Reported plainly, not overstated.

=============================================================================
5. DID CANDIDATE CLUSTERING PREVENT v1-STYLE DUPLICATE OVERWEIGHTING?
=============================================================================
outputs/candidate_cluster_report_v4.csv

YES, judged on the SPECIFIC failure mode v1 suffered. v1's failure
(diagnosed in afml_trade_lifecycle_v1_failure_diagnostic): a SINGLE cluster
of 981 same-level repeated-touch events (day 20260617) collapsed to an
effective N of just 13.8 (redundancy 70.9x) - one mega-cluster
ate 18% of the entire 5,365-row raw dataset into a near-single sample.

v4's Part D groups events by (day, side, level_type, nearest_level_price)
BEFORE clustering by time-overlap - this structurally CAPS how large any
one cluster can get (a cluster can only grow by the SAME level being
re-touched repeatedly within 40 bars on the SAME day, never by mixing
different levels or different days). Result: v4's WORST single cluster
across all 3773 clusters has only 31 events - a 32x smaller
worst-case than v1's mega-cluster.

V1_DUPLICATE_OVERWEIGHTING_PREVENTED: True

Honest caveat: this prevents v1's SPECIFIC single-mega-cluster failure
mode, but does not eliminate a different, lesser, structural phenomenon -
residual cross-level temporal overlap (many DIFFERENT levels touched in
overlapping 40-bar windows), which is why the population-level effective N
(29.9 from 3773 deduped candidates) is still much smaller than
the raw deduped count - that residual overlap is handled downstream by the
SAME AFML average-uniqueness sample weights used in Part G, not by
clustering alone.

=============================================================================
6-9. WHICH FEATURES WERE USED? (Part C)
=============================================================================
outputs/v4_feature_inventory.csv, v4_leakage_guard_report.csv

v4 reuses v3's already-validated 132-column feature panel verbatim (no
re-derivation risk) - dashboard-parity (residual_z, CUSUM z/breaks, entropy
family, flow toxicity/liquidity-cost composites, CSW score, break_score/
market_state, ADX, bull/bear pressure), OFI Level Decision tab features
(10-bar display-window table: last_*/sum_window_*/mean_window_*/slopes/
bull-bear-mix counts/direction_flip_count/z-scores), master-file features
(vpin, delta_norm, mlofi, sweep, entropy_score, flow_alignment,
volatility_5, z20 residuals), Book Flow add/pull (bid/ask pull pressure,
native HVN/LVN/POC/VAH/VAL), and model-probability freshness features
(modelprob_* - CURRENT_EVENT/HELD_LAST asof reconstruction, included as
FEATURES only, never as the target).

v4 additionally adds (Part D/E) reaction_type/level_type/training_gate_
status one-hots (rxn_*/lvl_*/gate_*) and structural fields (side_primary,
distance_ticks, touch_count_past_only, bars_since_prior_touch) from the
existing model's OWN event taxonomy.

LEAKAGE GUARD: 7/7 checks passed
  (banned-pattern scan, predictions.csv-outcome scan, centered-swing-level
  exclusion, forming-bar exclusion, duplicate-timestamp scan - all PASS)

=============================================================================
8 (restated). HOW MANY ACTIVE-POSITION SNAPSHOT ROWS WERE CREATED?
=============================================================================
137040 active-bar snapshots from 3426 positions
(mean 40.0 snapshots/position - every position ran the FULL
40-bar horizon with zero day-boundary truncation in this sample)
vs v3's 34 snapshots from 19 positions - a 4031x increase.

=============================================================================
9-10 (restated). HOW WAS CP LABEL DEFINED? FIXED THRESHOLDS?
=============================================================================
y_cp = 1 iff remaining_value_to_exit <= 0, where remaining_value_to_exit =
future_exit_value (side-adjusted value at the EXISTING MODEL's own fixed
+40-bar exit, day-bounded) minus close_now_value (side-adjusted value right
now). Per-path realized-outcome comparison - NEVER a fixed point/percent
number.

CP_FIXED_THRESHOLD_USED: False
y_cp=1 (close better) rate: 43.6%   y_cp=0 (hold better) rate: 56.4%

=============================================================================
10-13. MODEL COMPARISON (Parts G/H)
=============================================================================
outputs/model_comparison_entry_v4.csv, model_comparison_cp_v4.csv

XGBOOST_TESTED: True (version 3.1.3)
HIST_GRADIENT_TESTED: True
RANDOM_FOREST_TESTED: True

ENTRY MODEL COMPARISON (n_oof=2186 of 3426 eligible rows -
the remainder belong to the earliest calendar day, which has no PRIOR day
to train from under expanding-window purged folds and is correctly
excluded from OOF metrics rather than predicted):
          model  n_oof       mcc  mean_of_folds_mcc  worst_fold_mcc  pct_positive_folds  n_oos_only  mcc_oos_only  effective_independent_sample_count
logreg_baseline   2186  0.152158           0.167725       -0.234133            0.833333        1389      0.111972                           85.121951
        hist_gb   2186  0.153214           0.338524       -0.158693            0.833333        1389      0.251660                           85.121951
  random_forest   2186  0.017177           0.183560       -0.504156            0.666667        1389      0.139705                           85.121951
        xgboost   2186 -0.061959           0.001117       -0.377850            0.666667        1389      0.106243                           85.121951

CP MODEL COMPARISON (n_oof=92019 of 137040 snapshots):
          model  n_oof      mcc  mean_of_folds_mcc  worst_fold_mcc  pct_positive_folds  n_oos_only  mcc_oos_only  avg_close_improvement  bad_close_rate  hold_too_long_rate  effective_independent_sample_count
logreg_baseline  92019 0.088673           0.090445       -0.303113            0.500000       57526      0.237589             -12.649293        0.548252            0.363072                           85.219512
        hist_gb  92019 0.045721           0.091841       -0.281718            0.666667       57526      0.133971             -19.406305        0.566780            0.387170                           85.219512
  random_forest  92019 0.088229           0.151869       -0.022971            0.833333       57526      0.091742             -11.190736        0.562955            0.329396                           85.219512
        xgboost  92019 0.112955           0.067686       -0.181772            0.500000       57526      0.233457             -15.238427        0.545149            0.337314                           85.219512

Q10/11 (best Entry model): hist_gb (mean_of_folds_mcc=0.339,
  worst_fold=-0.159, 83% positive folds,
  genuinely-OOS n=1389 mcc=0.252). This is the FIRST
  build in the v1-v4 series where the entry-side mean-of-folds MCC is
  clearly positive on a substantial (n=1389) genuinely-OOS sample - a real,
  if modest, research-validity finding, not a small-sample artifact like
  v3's logreg_baseline=1.0.

Q11/12 (best CP model): random_forest (mean_of_folds_mcc=0.152,
  worst_fold=-0.023 - notably the LEAST negative worst-fold of any CP
  model tested, and the highest pct_positive_folds at 83%).
  HOWEVER (see Q14) classification skill here does NOT translate into
  positive realized business value.

Q12/13 (did XGBoost beat HistGradientBoostingClassifier?):
  Entry: False  (xgboost mean_of_folds_mcc=0.001 vs hist_gb=0.339)
  CP:    False  (xgboost mean_of_folds_mcc=0.068 vs hist_gb=0.092)
  XGBoost did NOT beat HistGradientBoostingClassifier in either task in
  this build - consistent with the general pattern across v3 and v4 that
  HistGB's regularization handles this feature/sample regime at least as
  well as XGBoost's default settings.

Q13/14 (did RandomForest overfit?):
  Entry: pooled_mcc=0.017 vs mean_of_folds_mcc=0.184,
         worst_fold=-0.504 (the MOST negative worst-fold of any entry
         model) - consistent with overfitting/instability risk on the entry task.
  CP:    pooled_mcc=0.088 vs mean_of_folds_mcc=0.152,
         worst_fold=-0.023 (the LEAST negative worst-fold of any CP model,
         83% positive folds) - here RandomForest looks comparatively
         STABLE, not overfit. Verdict is task-dependent: overfitting risk evident on
         Entry, not clearly evident on CP.

=============================================================================
14. DID CP REDUCE GIVEBACK OR IMPROVE CLOSE TIMING?
=============================================================================
outputs/shadow_lifecycle_v4.parquet, shadow_lifecycle_summary_v4.csv

NO - this is the build's clearest negative finding, reported plainly.

avg_close_improvement (mean realized value saved when a model says CLOSE,
vs. holding to the existing-policy exit) was NEGATIVE for ALL FOUR models:
          model  avg_close_improvement  bad_close_rate  hold_too_long_rate
logreg_baseline             -12.649293        0.548252            0.363072
        hist_gb             -19.406305        0.566780            0.387170
  random_forest             -11.190736        0.562955            0.329396
        xgboost             -15.238427        0.545149            0.337314

bad_close_rate exceeds 50% for every model (the model says CLOSE, but
holding to the existing-policy horizon would actually have been better,
more often than not).

The illustrative end-to-end shadow lifecycle (representative CP model:
random_forest, picked by best mean-of-folds MCC) confirms this
concretely: of 1130 positions that reached ACTIVE_SHADOW,
1036 (92%) were closed early by the CP model, with a
MEAN value impact of -47.20 points and MEDIAN
-92.25 points - both strongly NEGATIVE.

CP_VALUE_NEGATIVE_ON_AVERAGE: True

This is an important, honest distinction: the CP model shows statistically
detectable (if weak) CLASSIFICATION signal (positive mean-of-folds MCC,
mostly-positive folds, positive genuinely-OOS MCC), but a naive 0.5-
threshold CLOSE/HOLD policy built on that signal would have DESTROYED
realized value on this data, primarily because it closes far too eagerly
(here, on net) relative to how often early closing was actually the better
choice. Classification accuracy and dollar-value impact are NOT the same
thing, and this build's CP signal does not clear the second, harder bar.

=============================================================================
15-16. CLEAN OOS RESULT + EFFECTIVE INDEPENDENT SAMPLE SIZE
=============================================================================

Entry: genuinely-OOS n=1389, mcc_oos_only=0.252 (best model: hist_gb)
       effective_independent_sample_count (full pool) = 85.1
CP:    genuinely-OOS n=57526, mcc_oos_only=0.092 (best model: random_forest)
       effective_independent_sample_count (full pool) = 85.2

Clean OOS result: the Entry task shows a real, modest, positive signal on a
substantial genuinely-OOS sample (n=1,389) - the strongest entry-side
result across the whole v1-v4 series. The CP task shows a similarly modest
positive CLASSIFICATION signal on an even larger genuinely-OOS sample
(n=57,526), but that signal does NOT translate into positive average
realized value when acted on (Q14) - the two questions ("is there
detectable structure?" vs "would acting on it have helped?") have
DIFFERENT answers here, and both are reported rather than collapsing to one
headline number.

=============================================================================
17. IS THIS PRODUCTION-READY?
=============================================================================

NO. Specific reasons:
  1. Label-policy parity (Part B) is EXACT (100% match) - the plumbing is
     sound - but exact parity on the LABELING method does not by itself
     validate the MODELS trained on top of it.
  2. Entry model: a real, modest, positive signal exists (hist_gb,
     mean_of_folds_mcc=0.339, OOS mcc=0.252, n=1389) -
     encouraging for further research, far short of a production bar
     (worst-fold MCC is still negative: -0.159).
  3. CP model: net business value was NEGATIVE on average for every model
     tested (Q14) - this component is NOT ready to drive any close
     decision, shadow or otherwise, at its current 0.5-threshold
     configuration.
  4. The existing model this build extends never itself passed its own
     production promotion gates (release gate_status=BLOCKED).
  5. No execution, broker, or paper-trading path was touched or enabled
     anywhere in this build.

=============================================================================
VERSION COMPARISON (Part J)
=============================================================================
outputs/version_comparison_v1_v2_v3_v4.csv

      version  candidate_count  dedup_candidate_count  effective_N                    entry_best_model  entry_mean_of_folds_mcc  entry_oos_n  entry_oos_mcc   cp_best_model  cp_mean_of_folds_mcc
           v1             8394                    NaN          NaN                   single_meta_model                -0.115050        472.0      -0.148486             NaN                   NaN
v1_diagnostic             5365                    NaN   449.866246                                 NaN                      NaN          NaN            NaN             NaN                   NaN
 v2_ic_regime             2270                  418.0          NaN V2A_dedup+uniqueness (dominant fix)                      NaN        134.0      -0.011022             NaN                   NaN
           v3               36                   19.0    17.000000                     logreg_baseline                 1.000000          8.0       1.000000 logreg_baseline              0.608463
           v4            13500                 3773.0    85.121951                             hist_gb                 0.338524       1389.0       0.251660   random_forest              0.151869

=============================================================================
FINAL FIELDS
=============================================================================

PRODUCTION_FILES_MODIFIED: false
DASHBOARD_CODE_MODIFIED: false
BOOK_FLOW_CODE_MODIFIED: false
MODEL_ARTIFACTS_MODIFIED: false
TRADING_ENABLED: false
BROKER_CONNECTED: false
PAPER_TRADING_ENABLED: false
LABEL_POLICY_PARITY_PASS: true
V3_SAMPLE_STARVATION_FIXED: True
V1_DUPLICATE_OVERWEIGHTING_PREVENTED: True
XGBOOST_TESTED: True
HIST_GRADIENT_TESTED: true
RANDOM_FOREST_TESTED: true
ENTRY_MODEL_VERDICT: WEAK_POSITIVE_SIGNAL_NOT_PRODUCTION_GRADE (best=hist_gb, mean_of_folds_mcc=0.339, oos_n=1389, oos_mcc=0.252, worst_fold still negative)
CP_MODEL_VERDICT: CLASSIFICATION_SIGNAL_PRESENT_BUT_NET_VALUE_NEGATIVE (best=random_forest by MCC, but avg_close_improvement negative for all 4 models tested - do not act on this CP signal)
CP_FIXED_THRESHOLD_USED: false
PRODUCTION_READY: false
PAPER_TRADING_READY: false
OVERALL: PASS

PASS means: Parts A-J were completed read-only, label-policy parity passed
with an exact 100% match, no production/dashboard/Book-Flow/model-artifact
file was modified, and no broker/paper-trading/live-trading path was
touched or enabled anywhere in this codebase. PASS does NOT mean tradable -
the Entry model shows a real but modest research-validity signal, and the
CP model's net business-value impact was negative in this build's results.
