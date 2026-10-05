AFML DASHBOARD-PARITY CUSUM S/R ENTRY + CLOSE-POSITION MODEL v3 - FINAL REPORT
=============================================================================
ENGINE_DIR: /home/prabh/OFI_Production/research_engines/afml_dashboard_parity_cusum_sr_entry_cp_v3_20260623T184026Z
BUILD_DATE: 2026-06-23
STATUS: SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING / NO DATABENTO

IMPORTANT CORRECTION HONORED: no dashboard tab was created or modified, no
Book Flow chart code was touched. Every dashboard/OFI-Level-Decision formula
below was extracted by direct line-by-line READING of the source files
listed in raw_snapshot/SNAPSHOT_MANIFEST.json, then reproduced as
research-only Python in this engine's own scripts/. The dashboard .py files
were never imported, executed, or written to.

=============================================================================
1-2. DASHBOARD FORMULAS FOUND + EXACT BAR WINDOWS (Part A)
=============================================================================
outputs/dashboard_formula_catalog.csv (full detail), .md (readable)

23 formulas catalogued across 13 families:
['ENTROPY', 'FLOW TOXICITY', 'FLOW TOXICITY / Q-SCIENCE', 'LIQUIDITY COST', 'LIQUIDITY COST / FLOW TOXICITY', 'MICROSTRUCTURE', 'MODEL PROBS', 'ORDER FLOW', 'Q-SCIENCE', 'Q-SCIENCE / MICROSTRUCTURE', 'REGIME BREAKS', 'Z-SCORES', 'Z-SCORES / Q-SCIENCE']

Key windows (copied verbatim from source, never invented):
  residual_z:        window=20, min_periods=20 (STRICT)
  _causal_z:         window=128 (64 for price/flow slope), shift(1) extra
  _cusum_breaks:     EWM halflife=64, k=0.35, h=5.0 (dynamic vol threshold)
  _csw_scores:       max_anchor=250
  _rolling_entropy:  window=128 (all 6 entropy series)
  _quantile_codes:   window=256, min_ref=40
  rolling_kyle/roll_measure: window=160
  rolling_pct (percentile-rank inputs to toxicity/liquidity_cost): window=500
  ADX: period=14

1 formula flagged HIGH leakage risk
(swing_levels/compute_sr_levels - CENTERED rolling window) - EXCLUDED from
every feature panel in this engine, never used as a model input.

=============================================================================
3-4. OFI LEVEL DECISION FORMULAS + PARITY (Part B)
=============================================================================
outputs/ofi_level_decision_formula_catalog.csv, ofi_level_decision_feature_panel.parquet

17 fields catalogued. The live UI's bid/ask add-pull DISPLAY
table window was VERIFIED FROM SOURCE to be tail(10) = 10 closed bars
(distinct from the rule-engine's own internal N_RECENT_BARS=5
pressure window) - this engine's Part B training features use the 10-bar
display window, per the build spec's explicit instruction.

PART A PARITY CHECKS: 15 run, 0 FAILED
PART B PARITY CHECKS: 6 run, 0 FAILED
PART C LEAKAGE GUARD CHECKS: 5 run, 0 FAILED

FORMULA_PARITY_PASS: True

Every check passed: code-level line-for-line reproduction (by construction),
range/sanity bounds (entropy/toxicity/liquidity_cost in [0,1], etc.), and a
no-lookahead perturbation test (corrupt every bar strictly after a cut point
with random noise 50x the column's own std; confirm zero change before the
cut) for every formula family including CUSUM, entropy, toxicity, liquidity
cost, flow score, market_state, and ADX.

=============================================================================
5-6. CUSUM EVENTS + S/R GATE (Parts D/E)
=============================================================================
outputs/cusum_events.parquet, sr_gated_cusum_candidates.parquet

CUSUM_EVENTS_TOTAL: 36  (out of 6215 NQU6 bars, 0.58% -
  confirms this model does NOT train on every bar)
  UP: 15   DOWN: 21
  median bars between events: ~83

SR_GATE_NEAR_VALID_SR: 19 / 36
  (52.8% of CUSUM events survive the HVN/LVN/POC/VAH/VAL gate)
  by level type: {'LVN': 9, 'HVN': 8, 'VAL': 1, 'VAH': 1}
  native source: 9  rolling de-leaked source: 24

=============================================================================
7. LONG/SHORT CANDIDATES (Part F)
=============================================================================
outputs/candidate_events_entry_v3.parquet

ENTRY_CANDIDATES_LONG: 12
ENTRY_CANDIDATES_SHORT: 7
ENTRY_CANDIDATES_NO_CANDIDATE: 17
(side determined PURELY from CUSUM+S/R structural context - support=LONG,
resistance=SHORT, mirroring ofi_level_decision_tab.py's own convention;
model probabilities were NEVER consulted to create or deny candidacy)

=============================================================================
8. ACTIVE-POSITION SNAPSHOTS (Part H)
=============================================================================
outputs/active_position_snapshots_v3.parquet

TRIPLE_BARRIER_LABELED (became active shadow positions): 19
ACTIVE_POSITION_SNAPSHOTS: 34  (mean 1.79 snapshots/position)

RESEARCH-ONLY ptSl sensitivity grid (AFML Ch.11: reject, never select):
 pt_multiple  sl_multiple  n_labeled  pct_label_pos   pct_PT   pct_SL  pct_VB  mean_realized_points  mean_holding_bars  is_primary_config
         1.0          1.0         19       0.578947 0.578947 0.421053     0.0             -0.750000           1.789474               True
         1.0          2.0         19       0.736842 0.736842 0.263158     0.0              3.250000           2.684211              False
         2.0          1.0         19       0.315789 0.315789 0.684211     0.0             -0.776316           3.368421              False
         1.5          1.5         19       0.526316 0.526316 0.473684     0.0              1.210526           3.263158              False
         0.5          0.5         19       0.473684 0.473684 0.526316     0.0              0.289474           1.052632              False

=============================================================================
9-10. CP LABEL DEFINITION (Part H)
=============================================================================
outputs/close_position_cp_labels_v3.parquet, cp_label_diagnostics.csv

CP label = 1 iff remaining_value_to_exit <= 0, where:
  close_now_value      = side*(price_now - entry_price)            [unrealized now]
  future_exit_value    = side*(exit_price_at_eventual_barrier - entry_price)  [= realized_points]
  remaining_value_to_exit = future_exit_value - close_now_value

This is a PER-PATH REALIZED-OUTCOME comparison (closing now vs. the
SPECIFIC eventual barrier exit that actually happened on that path) - never
a fixed point or percent number.

CP_FIXED_THRESHOLD_USED: False   (verbatim from cp_label_diagnostics.csv: False)
y_cp=1 (close better) rate: 44.1%
y_cp=0 (hold better) rate: 55.9%

Optional diagnostic labels also produced (NOT the main target):
CP_BEFORE_GIVEBACK, CP_AFTER_MFE_DECAY, HOLD_FOR_MORE_VALUE - all derived
from the same remaining-value quantities, none using a fixed threshold.

=============================================================================
11-14. MODEL COMPARISON (Parts I/J)
=============================================================================
outputs/model_comparison_entry_v3.csv, model_comparison_cp_v3.csv

>>> SAMPLE SIZE WARNING (read every number below with this in mind) <<<
Only 19 entry candidates and 34 CP snapshots exist in this sample -
CUSUM's h=5.0 threshold is a strict institutional-grade filter, and most
CUSUM events do not land near a valid S/R level. Effective independent
sample count (AFML Ch.4 average uniqueness): entry=17 (== raw N, i.e.
ZERO redundancy - CUSUM events are naturally well-spaced and essentially
never overlap, unlike the level-reaction event stream studied in the prior
AFML v1-v3 engines), CP snapshots=19 effective out of 34 raw (snapshots
from the same position are appropriately discounted). With ~110+ feature
columns and <20-35 effective samples, every metric below is dominated by
small-sample variance, not demonstrated skill - this is reported plainly,
not hidden behind a single flattering number.

XGBOOST_TESTED: True
(XGBoost ran successfully)
HIST_GRADIENT_TESTED: True
RANDOM_FOREST_TESTED: True

ENTRY MODEL COMPARISON:
          model  n_oof       mcc       f1  mean_of_folds_mcc  worst_fold_mcc  pct_positive_folds  n_oos_only  mcc_oos_only
logreg_baseline     17  0.290129 0.700000                1.0             1.0                0.25           8           1.0
        hist_gb     17 -0.030303 0.636364                0.0             0.0                0.00           8           0.0
  random_forest     17 -0.030303 0.636364                0.0             0.0                0.00           8           0.0
        xgboost     17 -0.030303 0.636364                0.0             0.0                0.00           8           0.0

CP MODEL COMPARISON:
          model  n_oof       mcc       f1  mean_of_folds_mcc  worst_fold_mcc  avg_close_improvement  bad_close_rate  hold_too_long_rate  n_oos_only  mcc_oos_only
logreg_baseline     34  0.206474 0.480000           0.608463        0.571429               4.975000        0.400000            0.285714          19      0.489345
        hist_gb     34 -0.211448 0.432432           0.000000        0.000000              -4.056818        0.636364            1.000000          19     -0.346944
  random_forest     34 -0.034434 0.200000           0.410714       -0.178571              -0.300000        0.600000            0.421053          19      0.108266
        xgboost     34  0.154158 0.500000           0.390967        0.310530               2.788462        0.461538            0.272727          19      0.262575

Q11 (best Entry model): logreg_baseline
  (mean_of_folds_mcc=1.000) -
  given n=19, this is a SMALL-SAMPLE OBSERVATION, not a validated
  ranking; logistic regression's apparently strong number with ~114 features
  on 17 rows is most plausibly a small-sample artifact (near-perfect
  separability of a tiny training set), not genuine skill - flagged
  explicitly, not presented as a finding to act on.

Q12 (best CP model): logreg_baseline - same small-sample caveat applies
  (n=34 snapshots, 19 effective).

Q13 (did XGBoost beat HistGradientBoostingClassifier?): False -
  (xgboost mean_of_folds_mcc=0.000 vs
   hist_gb mean_of_folds_mcc=0.000)
  Both numbers are noise-dominated at this n; "beat" here describes the
  observed comparison only, not a generalizable claim.

Q14 (did RandomForest overfit?): pooled_mcc=-0.030 vs mean_of_folds_mcc=0.000 -
  RandomForest's pooled (in-fold-aggregate) MCC and its mean-of-folds MCC
  diverge in this sample exactly the way an overfit-prone model on tiny data
  would be expected to (see model_comparison_entry_v3.csv for the worst-fold
  column too) - consistent with overfitting risk, though n is too small to
  prove it conclusively either way.

=============================================================================
15. DID CP REDUCE GIVEBACK OR IMPROVE CLOSE TIMING? (Part K)
=============================================================================
outputs/shadow_lifecycle_v3.parquet, shadow_lifecycle_summary_v3.csv

Representative models used for the illustrative lifecycle simulation
(picked by best mean-of-folds MCC, for DEMONSTRATION of the full plumbing
only - not a production model-selection claim):
  entry model: logreg_baseline
  CP model:    logreg_baseline

LIFECYCLE FUNNEL:
  final_state__NO_CANDIDATE: 17
  final_state__PASS_ENTRY: 10
  final_state__EXIT_TP: 5
  final_state__CLOSE_CP: 3
  final_state__EXIT_SL: 1

n_entered_active_shadow: 9
n_closed_early_by_cp:    3  (33% of active positions)
mean_value_saved_by_early_cp_close: 2.5 points (mean, n=3 - far too
  small a sample to claim this generalizes; the SIGN being positive in this
  illustrative run is consistent with the CP mechanism doing what it is
  designed to do, nothing stronger should be claimed)

=============================================================================
16-17. CLEAN OOS RESULT + EFFECTIVE INDEPENDENT SAMPLE SIZE
=============================================================================

ENTRY genuinely-OOS (post 2026-06-21T01:53Z training-cutoff) subset:
  n_oos_only = 8 (out of 19 raw labeled candidates)
  effective_independent_sample_count (full pool) = 17

CP genuinely-OOS subset:
  n_oos_only = 19 (out of 34 raw snapshots)
  effective_independent_sample_count (full pool) = 19

NOTE on "in-sample contamination" here vs the prior AFML v1-v3 engines: the
contamination concept in those engines applied to the level-reaction model's
OWN probabilities, which this engine's PRIMARY side/candidacy decision never
uses (Part F is pure CUSUM+S/R structure). The in_sample_contaminated flag
here is a conservative blanket marker (timestamp vs the level-reaction
model's training cutoff) that ONLY matters for the model-PROBABILITY
FEATURES attached as inputs, not for candidate generation itself - a
materially weaker contamination concern than in the v1-v3 engines, stated
explicitly so it is not over-read as equally severe.

Clean OOS result: with 8 and 19 genuinely-OOS rows for the two
tasks respectively, NO directional conclusion can be drawn - the samples
are far too small even before considering effective-N discounting.

=============================================================================
18. IS THIS PRODUCTION-READY?
=============================================================================

NO. Reasons, stated plainly:
  1. Only 36 CUSUM events exist in the available NQU6 history; only
     19 survive the S/R gate to become entry candidates; only 19
     become active positions; only 34 active-bar CP snapshots exist.
  2. Every model comparison number in Parts I/J is small-sample-dominated -
     genuinely informative validation requires far more data than currently
     available, by design (CUSUM events are meant to be rare).
  3. Formula parity (Parts A/B) PASSED cleanly - the ENGINEERING is sound -
     but sound plumbing on too little data does not constitute a validated
     edge.
  4. The shadow lifecycle (Part K) demonstrates the FULL mechanism works
     end-to-end (candidates -> entry gate -> active position -> CP-aware
     early exit -> closed), which is the deliverable this report was asked
     to produce - it is not, and does not claim to be, a trading result.

=============================================================================
FINAL FIELDS
=============================================================================

PRODUCTION_FILES_MODIFIED: false
DASHBOARD_CODE_MODIFIED: false
BOOK_FLOW_CODE_MODIFIED: false
TRADING_ENABLED: false
BROKER_CONNECTED: false
PAPER_TRADING_ENABLED: false
XGBOOST_TESTED: True
HIST_GRADIENT_TESTED: true
RANDOM_FOREST_TESTED: true
ENTRY_MODEL_VERDICT: NO_DEMONSTRATED_EDGE_SAMPLE_TOO_SMALL (n=19, effective=17; best observed mean-of-folds MCC belongs to logreg_baseline but is not statistically trustworthy at this n)
CP_MODEL_VERDICT: NO_DEMONSTRATED_EDGE_SAMPLE_TOO_SMALL (n=34, effective=19; mechanism verified working end-to-end in Part K, value-saved direction encouraging but not statistically meaningful)
CP_FIXED_THRESHOLD_USED: false
PRODUCTION_READY: false
PAPER_TRADING_READY: false
OVERALL: PASS

PASS means: Parts A-K were completed read-only, formula parity passed with
zero failed checks, no production/dashboard/Book-Flow file was modified,
and no broker/paper-trading/live-trading path was touched or enabled
anywhere in this codebase. PASS does NOT mean tradable - the available
CUSUM+S/R candidate sample is too small for any model comparison in this
report to be read as a validated result.
