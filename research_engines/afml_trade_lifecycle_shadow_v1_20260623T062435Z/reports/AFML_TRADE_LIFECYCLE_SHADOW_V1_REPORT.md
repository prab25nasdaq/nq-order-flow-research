AFML TRADE LIFECYCLE SHADOW ENGINE v1 - FINAL REPORT
=============================================================================
ENGINE_DIR: /home/prabh/OFI_Production/research_engines/afml_trade_lifecycle_shadow_v1_20260623T062435Z
BUILD_DATE: 2026-06-23
SNAPSHOT:   raw_snapshot/SNAPSHOT_MANIFEST.json (frozen copy; production paths read-only)
STATUS:     SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO DATABENTO / NO PAPER TRADING

This engine implements ONLY methodology from Marcos Lopez de Prado's
"Advances in Financial Machine Learning" (dynamic-threshold triple-barrier
labeling, meta-labeling, probability-based bet sizing, averaging active
bets, size discretization, purged/embargoed validation, and a research-only
strategy lifecycle). No discretionary trading rule was invented anywhere in
this codebase. The primary side comes exclusively from the existing
production level-reaction model probabilities (predictions.csv) - this
engine never decides direction on its own.

=============================================================================
AFML_METHODS_USED
=============================================================================
- triple_barrier                  (script 02, afml_common.apply_triple_barrier)
- meta_labeling                   (script 03, y_meta = 1{label_primary==1})
- bet_sizing_from_probabilities    (script 05, afml_common.meta_prob_to_size)
- averaging_active_bets           (script 06, afml_common.avg_active_signals)
- size_discretization             (script 05/06, afml_common.discretize_signal)
- purging_embargo                 (script 04/08, afml_common.purged_embargo_day_splits)
- strategy_lifecycle_shadow_only   (script 07, research ledger, NO ORDERS)

PRODUCTION_FILES_MODIFIED: false
TRADING_ENABLED:           false
BROKER_CONNECTED:          false
PAPER_TRADING_ENABLED:     false

=============================================================================
1. SCOPE AND DATA
=============================================================================

Candidate events are sourced exclusively from `predictions.csv` rows where
contract_symbol == 'NQU6' (8394 rows) - scoped to NQU6 because true
book-flow (OFI bid/ask add-pull) meta-labeling features only exist for NQU6
in `book_flow_chart/cache/`. The primary side (`side_primary`) is the
production model's own `direction` field (LONG/SHORT/FLAT) - this engine
never invents or overrides it.

CANDIDATE_EVENTS_TOTAL:              8394
  side_primary = +1 (LONG):          2744
  side_primary = -1 (SHORT):         2794
  side_primary =  0 (FLAT/no call):  2856  (immediate PASS - no side to act on)
GENUINELY_OUT_OF_SAMPLE (timestamp strictly after the active release's
  2026-06-21T01:53:35Z training cutoff): 1458 / 8394
IN_SAMPLE_CONTAMINATED (at or before cutoff):                6936 / 8394

>>> IN-SAMPLE CONTAMINATION WARNING (carried through every section below) <<<
The prior institutional audit (research_reports/ofi_model_level_alpha_research_*)
proved that the active level-reaction model's own probabilities are ~99%
in-sample when evaluated against `predictions.csv` directly, because the
model is scored over the same history it was trained on. This engine's
PRIMARY side and PRIMARY probability inherit that same risk for any event at
or before the cutoff. The meta-model trained in script 04 is validated with
a genuinely out-of-fold CV of its OWN (see Section 5), but its input
features still partially encode an upstream-contaminated primary
probability for in-sample events. Every metric in this report is therefore
shown for BOTH the full out-of-fold population (`ALL_OUT_OF_FOLD`) AND the
genuinely-clean subset (`GENUINELY_OOS_ONLY`) - never collapsed into one
number, exactly per the build instructions.

=============================================================================
2. TRIPLE-BARRIER LABELING (AFML Ch.3)
=============================================================================

Dynamic volatility threshold: causal EWM std of log returns
(span=100 bars, min_periods=20).
Primary config (set a priori, never tuned on a result):
  pt_multiple=1.0  sl_multiple=1.0
  vertical_barrier_bars=40 (day-bounded; matches the
  production model's own native label_h40 horizon)

TRIPLE_BARRIER_LABELED_EVENTS (side != 0, vol available): 5365
first_touch breakdown: {'PT': 3183, 'SL': 2151, 'VB': 31}

RESEARCH-ONLY ptSl grid (AFML Ch.11: backtesting can only REJECT a bad spec,
never SELECT a good one - this grid was generated for transparency, never
used to retune the primary config above):

 pt_multiple  sl_multiple  n_labeled  pct_label_pos  pct_label_neg   pct_PT   pct_SL   pct_VB  mean_realized_points  median_realized_points  mean_holding_bars  is_primary_config
         1.0          1.0       5365       0.599068       0.400932 0.593290 0.400932 0.005778              4.124278                    7.50           1.679590               True
         1.0          2.0       5365       0.843616       0.156384 0.837838 0.153588 0.008574              8.007176                   12.25           2.580615              False
         2.0          1.0       5365       0.491705       0.508295 0.485927 0.508295 0.005778              8.076654                    6.00           2.956570              False
         1.5          1.5       5365       0.699348       0.300652 0.693569 0.300652 0.005778              9.057316                   15.50           3.026095              False
         0.5          0.5       5365       0.476421       0.523579 0.476421 0.523579 0.000000              2.855126                    2.50           1.082572              False

=============================================================================
3. META-LABELING DATASET (AFML Ch.3 Sec 3.6)
=============================================================================

y_meta = 1 if the primary side would have produced a gain before
invalidation (first_touch=='PT', or a favorable sign at the vertical
barrier), else 0. side_primary==0 events are excluded from meta-training
(config: meta_labeling.exclude_flat_primary_side=true) - there is no side
for a secondary model to gate.

META_DATASET_ELIGIBLE (resolved, side!=0): 5365
  y_meta=1: 3214   y_meta=0: 2151
Feature groups used (ALL strictly t0-or-earlier, see script 03 docstring for
the causality argument for each group): model probability freshness/
confidence, reaction_type/training_gate_status, book-flow pull-pressure
(causal rolling z20 + roll5sum), volatility/VPIN/regime, level context,
session/time-of-day.

Rows dropped for missing book-flow features (mostly the 2026-06-14
NQU6-rollover-warmup day, which has zero book-flow cache coverage - an
already-documented, explained gap, not a new defect): see script 04 log.

=============================================================================
4. BET SIZING FROM PROBABILITIES (AFML Ch.10)
=============================================================================

raw_size = side_primary * max(0, 2*Phi(z)-1), z = t-value of p_meta vs the
1/num_classes null (num_classes=2) - magnitude only,
side is NEVER flipped by the meta-model (explicit AFML Ch.3 meta-labeling
constraint). discretized in steps of 0.1 (AFML Ch.10
discreteSignal) to avoid bet-size jitter.

BET_SIZING_ACTIVE_NONZERO ("number passing meta-label"): 906
BET_SIZING_ZERO_SIZED:                                    3460
BET_SIZING_PASS (zero-sized OR no OOF prediction available): 3460

=============================================================================
5. AVERAGING ACTIVE BETS (AFML Ch.10) AND SHADOW LIFECYCLE
=============================================================================

Each entered candidate's active lifespan runs from t0 to its ACTUAL
triple-barrier resolution (not the planned vertical barrier). At every bar,
all currently-active bets' discretized sizes are averaged
(average_active_signal), then the averaged signal is itself re-discretized
(target_position_shadow) - matching AFML snippet 10.1's full pipeline order.

AVERAGE_ACTIVE_BETS: 0.4412
MAX_ACTIVE_BETS:     168
TURNOVER_PROXY (sum|position_delta_shadow| across the whole timeline): 85.40

Note: a small number of bars show a very large active-bet count (up to
168) - this is a genuine, explainable property of the
underlying event stream (production generates one candidate per
(bar x level-type), so a fast, multi-level price move can fire dozens of
simultaneous, mostly-same-direction reaction events with short 1-4 bar
holding periods), not an engine defect.

NO ORDERS were placed anywhere in this pipeline. Per-event lifecycle funnel
(shadow_position_lifecycle.csv/.parquet, one row per candidate event_id):

CANCEL_STALE               3064
PASS                       2856
CANCEL_META_PASS           1364
EXIT_PT                     571
EXIT_SL                     335
CANCEL_VERTICAL_EXPIRED     204

  PASS                     = side_primary==0, no side to act on
  CANCEL_VERTICAL_EXPIRED  = side!=0 but no usable meta verdict ever arrived
                             before the opportunity itself expired (either
                             never triple-barrier-labeled, or resolved VB)
  CANCEL_STALE             = side!=0, no usable meta verdict ever arrived,
                             but the underlying path resolved via PT/SL
                             (the SIGNAL went stale, not the opportunity)
  CANCEL_META_PASS         = meta verdict arrived, sized to exactly zero
  EXIT_PT / EXIT_SL        = entered a shadow position, triple-barrier
                             first-touch was the profit-take / stop-loss
  (EXIT_TIME would appear here if any entered position's first touch were
  the vertical barrier - none did in this sample, see Section 6)

Aggregate (portfolio-level) state transitions, derived bar-by-bar from
target_position_shadow (shadow_position_bar_timeline.csv):
  EXIT_SIZE_ZERO events (position returns to exactly flat): 81
  EXIT_META_FLIP events (position sign flips bar-to-bar without passing through flat): 0

=============================================================================
6. EXIT RATES (entered / shadow-active positions only, n=906)
=============================================================================

PT_EXIT_RATE:   0.6302
SL_EXIT_RATE:   0.3698
TIME_EXIT_RATE: 0.0000
AVERAGE_HOLDING_BARS (entered positions): 1.541
MEAN_POINTS (entered positions, realized, side-signed): 3.138
MEDIAN_POINTS:                                          4.250
HIT_RATE (realized_points > 0):                          0.5784
MEAN_MFE / MEAN_MAE (points):                            14.529 / 10.736

=============================================================================
7. PURGED / EMBARGOED VALIDATION (AFML Ch.7) — outputs/validation_results.csv, fold_results.csv
=============================================================================

Day-based expanding-window walk-forward (NO random K-fold anywhere in this
engine). For each test day, training events whose [t0,t1] label window
overlaps the test day are PURGED, and events starting within
40 bars after the test day's last label are EMBARGOED
from all subsequent folds. Fold date ranges, purge counts, and embargo
counts are in `fold_results.csv`. The earliest calendar day in the cleaned
sample has no prior day to train on and is correctly excluded (not
back-filled) from out-of-fold predictions.

                          ALL_OUT_OF_FOLD          GENUINELY_OOS_ONLY
  n                       2270                     472
  precision               0.6296                   0.3763
  recall                  0.4220                   0.3167
  F1                      0.5053                   0.3440
  MCC                     0.0148                   -0.1485
  balanced_accuracy       0.5075                   0.4273

WORST_FOLD: 20260622 (MCC=-0.4470, n_test=25)
BEST_FOLD:  20260618 (MCC=0.1065, n_test=197)
PCT_POSITIVE_FOLDS (MCC>0): 0.333  (2/6 folds)

Per-fold detail:
 test_day  n_train  n_test  n_purged  n_embargoed  ALL__mcc  ALL__f1  ALL__hit_rate  OOS_ONLY__n  OOS_ONLY__mcc
 20260616     2096     480         0            0  0.031381 0.350000       0.538462            0            NaN
 20260617     2576    1121         0            0 -0.012718 0.548510       0.718816            0            NaN
 20260618     3037     197         0          660  0.106475 0.738351       0.313433            0            NaN
 20260621     3160     311         0          734 -0.129208 0.424837       0.477987          311      -0.129208
 20260622     3453      25         0          752 -0.447024 0.357143       0.200000           25      -0.447024
 20260623     3477     136         0          753 -0.239203      NaN       0.000000          136      -0.239203

**Honest reading of this result:** the GENUINELY_OOS_ONLY MCC (-0.1485) is
negative,
driven entirely by the three most recent, smallest folds (2026-06-21/22/23,
n=311/25/136) - exactly the only data this engine is entitled to trust as a
forward test. This meta-model does NOT currently demonstrate a stable,
positive out-of-sample edge. That is a valid, important research finding,
not a failure of the engine: AFML Ch.11 explicitly anticipates that most
specifications will be rejected at this stage, and rejecting one here is
the correct, disciplined outcome of running the methodology honestly.

LEAKAGE CHECKS:
```json
{
  "no_random_kfold_used": true,
  "purge_overlapping_labels_applied": true,
  "embargo_bars_after_test_fold": 40,
  "fold_unit": "calendar_day",
  "train_test_only_on_closed_bars": true,
  "first_calendar_day_has_no_training_fold": true,
  "in_sample_contaminated_rows_in_oof_population": 1798,
  "genuinely_oos_rows_in_oof_population": 472,
  "predictions_csv_naive_in_sample_claim_made": false
}
```

=============================================================================
8. OVERALL
=============================================================================

OVERALL: PASS

PASS means: this engine was built and run entirely read-only against a
frozen snapshot, every required research ledger was produced, no production
file (parser/scheduler/Rithmic feed/master files/model artifacts/dashboard/
Book Flow chart/model release pointers/trading flags/broker logic) was
modified, and no broker/paper-trading/live-trading path was touched or
enabled anywhere in this codebase.

PASS DOES NOT MEAN:
- the strategy is tradable
- the meta-model has a demonstrated edge (Section 7 shows it currently does
  not, on the only data this engine is entitled to call out-of-sample)
- any number in this report is production-ready or paper-trading-ready

PRODUCTION_READY:      false
PAPER_TRADING_READY:   false
RECOMMENDED_NEXT_STEP: Accumulate more genuinely-post-training-cutoff NQU6
  data (currently only 1458 candidate events, 472 of which have an
  out-of-fold meta-prediction) before drawing any conclusion about this
  meta-model's real edge; the ALL_OUT_OF_FOLD numbers in this report should
  not be used for that purpose even though their own CV is clean, because
  their primary-side inputs are not.
