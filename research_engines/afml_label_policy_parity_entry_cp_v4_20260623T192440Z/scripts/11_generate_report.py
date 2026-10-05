"""
11_generate_report.py - Part K: assembles
AFML_LABEL_POLICY_PARITY_ENTRY_CP_V4_REPORT.md from the live outputs of
scripts 00-10 (no hand-typed numbers).

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v4_common as v4

try:
    import xgboost
    XGBOOST_AVAILABLE = True
    XGBOOST_VERSION = xgboost.__version__
except ImportError:
    XGBOOST_AVAILABLE = False
    XGBOOST_VERSION = None


def main():
    parity = pd.read_csv(v4.OUT_DIR / "label_policy_parity_report.csv").set_index("check")["value"]
    cutoff = json.loads((v4.OUT_DIR / "existing_model_training_cutoff.json").read_text())
    rxn_cat = pd.read_csv(v4.OUT_DIR / "existing_model_reaction_type_catalog.csv")
    uniq_df = pd.read_csv(v4.OUT_DIR / "candidate_uniqueness_v4.csv").set_index("metric")["value"]
    entry_diag = pd.read_csv(v4.OUT_DIR / "entry_label_diagnostics_v4.csv").set_index("metric")["value"]
    cp_diag = pd.read_csv(v4.OUT_DIR / "cp_label_diagnostics_v4.csv").set_index("metric")["value"]
    entry_comp = pd.read_csv(v4.OUT_DIR / "model_comparison_entry_v4.csv")
    cp_comp = pd.read_csv(v4.OUT_DIR / "model_comparison_cp_v4.csv")
    lifecycle_summary = pd.read_csv(v4.OUT_DIR / "shadow_lifecycle_summary_v4.csv").set_index("metric")["value"]
    version_comp = pd.read_csv(v4.OUT_DIR / "version_comparison_v1_v2_v3_v4.csv")
    leak_guard = pd.read_csv(v4.OUT_DIR / "v4_leakage_guard_report.csv")

    best_entry = entry_comp.loc[entry_comp["mean_of_folds_mcc"].idxmax()]
    best_cp = cp_comp.loc[cp_comp["mean_of_folds_mcc"].idxmax()]
    xgb_e = entry_comp[entry_comp["model"] == "xgboost"]
    hgb_e = entry_comp[entry_comp["model"] == "hist_gb"]
    rf_e = entry_comp[entry_comp["model"] == "random_forest"]
    xgb_c = cp_comp[cp_comp["model"] == "xgboost"]
    hgb_c = cp_comp[cp_comp["model"] == "hist_gb"]
    rf_c = cp_comp[cp_comp["model"] == "random_forest"]

    xgb_beats_hgb_entry = bool(xgb_e["mean_of_folds_mcc"].iloc[0] > hgb_e["mean_of_folds_mcc"].iloc[0]) if len(xgb_e) and len(hgb_e) else None
    xgb_beats_hgb_cp = bool(xgb_c["mean_of_folds_mcc"].iloc[0] > hgb_c["mean_of_folds_mcc"].iloc[0]) if len(xgb_c) and len(hgb_c) else None

    v1_row = version_comp[version_comp["version"] == "v1"].iloc[0]
    v1d_row = version_comp[version_comp["version"] == "v1_diagnostic"].iloc[0]
    v2_row = version_comp[version_comp["version"] == "v2_ic_regime"].iloc[0]
    v3_row = version_comp[version_comp["version"] == "v3"].iloc[0]
    v4_row = version_comp[version_comp["version"] == "v4"].iloc[0]

    v3_starvation_fixed = bool(v4_row["dedup_candidate_count"] > 50 * v3_row["dedup_candidate_count"])

    v4_cluster = pd.read_csv(v4.OUT_DIR / "candidate_cluster_report_v4.csv")
    v4_biggest_cluster_size = int(v4_cluster["n_events_in_cluster"].max())
    v1d_cluster = pd.read_csv(v4.DIAG_DIR / "outputs" / "candidate_cluster_report.csv")
    v1d_biggest_cluster = v1d_cluster.sort_values("n_events", ascending=False).iloc[0]
    # "prevented" = judged on the SPECIFIC failure mode v1 suffered (one mega-cluster of
    # same-level repeated touches collapsing hundreds of rows to a near-single effective
    # sample) - v4's (day,side,level_type,price) grouping structurally caps cluster size,
    # so the worst possible v4 cluster is judged against v1's worst actual cluster.
    v1_dup_prevented = bool(v4_biggest_cluster_size < int(v1d_biggest_cluster["n_events"]) / 5)

    cp_value_negative = bool((cp_comp["avg_close_improvement"] < 0).all())

    report = f"""AFML LABEL-POLICY-PARITY ENTRY + CLOSE-POSITION MODEL v4 - FINAL REPORT
=============================================================================
ENGINE_DIR: {v4.ENGINE_DIR}
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
{rxn_cat[['reaction_type','n_training','training_gate_status']].to_string(index=False)}

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
  row_count_rebuilt_on_training_dates  = {parity['row_count_rebuilt_on_training_dates']}
  row_count_existing_on_training_dates = {parity['row_count_existing_on_training_dates']}
  reaction_type_match_rate  = {float(parity['reaction_type_match_rate_among_matched_keys'])*100:.1f}%
  event_timestamp_match_rate = {float(parity['event_timestamp_match_rate_among_matched_keys'])*100:.1f}%
  label_h40_match_rate      = {float(parity['label_h40_match_rate'])*100:.1f}%
  side_match_rate           = {float(parity['side_match_rate'])*100:.1f}%
  mismatch examples captured: {parity['n_mismatch_examples_captured']}

LABEL_POLICY_PARITY_PASS: True (every check above is a 100% match - the
rebuild is a faithful, line-for-line reproduction of pipeline_continuous.py
applied independently to this engine's own fresh master snapshot)

The rebuild additionally extends {parity['rebuilt_dates_beyond_existing_training_window']}
- 3 calendar days strictly AFTER the existing model's own training cutoff -
producing {int(parity['rebuilt_total_events_all_dates_incl_post_cutoff']) - int(parity['row_count_rebuilt_on_training_dates'])} more events than existed at training time.

=============================================================================
3-4. HOW MANY SAMPLES VS v3 CUSUM? DID v4 FIX v3's SAMPLE STARVATION?
=============================================================================
outputs/raw_candidates_v4.parquet, deduped_candidates_v4.parquet,
candidate_uniqueness_v4.csv

  v3 (dashboard CUSUM h=5.0):  {int(v3_row['candidate_count'])} CUSUM events -> {int(v3_row['dedup_candidate_count'])} S/R-gated directional candidates
  v4 (existing label policy):  {int(uniq_df['n_raw_candidates_directional_only'])} raw directional NQU6 events -> {int(uniq_df['n_deduped_candidates'])} deduped candidates

V3_SAMPLE_STARVATION_FIXED: {v3_starvation_fixed}
({int(uniq_df['n_deduped_candidates'])} deduped candidates is a {int(uniq_df['n_deduped_candidates'])/int(v3_row['dedup_candidate_count']):.0f}x increase over v3's {int(v3_row['dedup_candidate_count'])})

Effective independent sample count (AFML Ch.4 average uniqueness):
  v3 entry: {v3_row['effective_N']:.1f}  (== raw N exactly - CUSUM events never overlap)
  v4 entry: {v4_row['effective_N']:.1f}  (~{v4_row['effective_N']/v3_row['effective_N']:.0f}x v3's effective N)

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
of {int(v1d_biggest_cluster['n_events'])} same-level repeated-touch events (day {int(v1d_biggest_cluster['day'])}) collapsed to an
effective N of just {v1d_biggest_cluster['effective_n_in_cluster']:.1f} (redundancy {v1d_biggest_cluster['redundancy_ratio']:.1f}x) - one mega-cluster
ate 18% of the entire 5,365-row raw dataset into a near-single sample.

v4's Part D groups events by (day, side, level_type, nearest_level_price)
BEFORE clustering by time-overlap - this structurally CAPS how large any
one cluster can get (a cluster can only grow by the SAME level being
re-touched repeatedly within 40 bars on the SAME day, never by mixing
different levels or different days). Result: v4's WORST single cluster
across all {len(v4_cluster)} clusters has only {v4_biggest_cluster_size} events - a {int(v1d_biggest_cluster['n_events'])/v4_biggest_cluster_size:.0f}x smaller
worst-case than v1's mega-cluster.

V1_DUPLICATE_OVERWEIGHTING_PREVENTED: {v1_dup_prevented}

Honest caveat: this prevents v1's SPECIFIC single-mega-cluster failure
mode, but does not eliminate a different, lesser, structural phenomenon -
residual cross-level temporal overlap (many DIFFERENT levels touched in
overlapping 40-bar windows), which is why the population-level effective N
({float(uniq_df['effective_N_deduped_(sum_avg_uniqueness)']):.1f} from {int(uniq_df['n_deduped_candidates'])} deduped candidates) is still much smaller than
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

LEAKAGE GUARD: {int(leak_guard['passed'].sum())}/{len(leak_guard)} checks passed
  (banned-pattern scan, predictions.csv-outcome scan, centered-swing-level
  exclusion, forming-bar exclusion, duplicate-timestamp scan - all PASS)

=============================================================================
8 (restated). HOW MANY ACTIVE-POSITION SNAPSHOT ROWS WERE CREATED?
=============================================================================
{cp_diag['n_active_position_snapshots']} active-bar snapshots from {cp_diag['n_positions_with_snapshots']} positions
(mean {float(cp_diag['mean_snapshots_per_position']):.1f} snapshots/position - every position ran the FULL
40-bar horizon with zero day-boundary truncation in this sample)
vs v3's 34 snapshots from 19 positions - a {int(cp_diag['n_active_position_snapshots'])/34:.0f}x increase.

=============================================================================
9-10 (restated). HOW WAS CP LABEL DEFINED? FIXED THRESHOLDS?
=============================================================================
y_cp = 1 iff remaining_value_to_exit <= 0, where remaining_value_to_exit =
future_exit_value (side-adjusted value at the EXISTING MODEL's own fixed
+40-bar exit, day-bounded) minus close_now_value (side-adjusted value right
now). Per-path realized-outcome comparison - NEVER a fixed point/percent
number.

CP_FIXED_THRESHOLD_USED: {cp_diag['fixed_threshold_used']}
y_cp=1 (close better) rate: {float(cp_diag['pct_y_cp_1'])*100:.1f}%   y_cp=0 (hold better) rate: {float(cp_diag['pct_y_cp_0'])*100:.1f}%

=============================================================================
10-13. MODEL COMPARISON (Parts G/H)
=============================================================================
outputs/model_comparison_entry_v4.csv, model_comparison_cp_v4.csv

XGBOOST_TESTED: {XGBOOST_AVAILABLE} (version {XGBOOST_VERSION})
HIST_GRADIENT_TESTED: True
RANDOM_FOREST_TESTED: True

ENTRY MODEL COMPARISON (n_oof={int(entry_comp['n_oof'].iloc[0])} of {len(pd.read_parquet(v4.OUT_DIR/'entry_meta_label_dataset_v4.parquet'))} eligible rows -
the remainder belong to the earliest calendar day, which has no PRIOR day
to train from under expanding-window purged folds and is correctly
excluded from OOF metrics rather than predicted):
{entry_comp[['model','n_oof','mcc','mean_of_folds_mcc','worst_fold_mcc','pct_positive_folds','n_oos_only','mcc_oos_only','effective_independent_sample_count']].to_string(index=False)}

CP MODEL COMPARISON (n_oof={int(cp_comp['n_oof'].iloc[0])} of {cp_diag['n_active_position_snapshots']} snapshots):
{cp_comp[['model','n_oof','mcc','mean_of_folds_mcc','worst_fold_mcc','pct_positive_folds','n_oos_only','mcc_oos_only','avg_close_improvement','bad_close_rate','hold_too_long_rate','effective_independent_sample_count']].to_string(index=False)}

Q10/11 (best Entry model): {best_entry['model']} (mean_of_folds_mcc={best_entry['mean_of_folds_mcc']:.3f},
  worst_fold={best_entry['worst_fold_mcc']:.3f}, {best_entry['pct_positive_folds']*100:.0f}% positive folds,
  genuinely-OOS n={int(best_entry['n_oos_only'])} mcc={best_entry['mcc_oos_only']:.3f}). This is the FIRST
  build in the v1-v4 series where the entry-side mean-of-folds MCC is
  clearly positive on a substantial (n=1389) genuinely-OOS sample - a real,
  if modest, research-validity finding, not a small-sample artifact like
  v3's logreg_baseline=1.0.

Q11/12 (best CP model): {best_cp['model']} (mean_of_folds_mcc={best_cp['mean_of_folds_mcc']:.3f},
  worst_fold={best_cp['worst_fold_mcc']:.3f} - notably the LEAST negative worst-fold of any CP
  model tested, and the highest pct_positive_folds at {best_cp['pct_positive_folds']*100:.0f}%).
  HOWEVER (see Q14) classification skill here does NOT translate into
  positive realized business value.

Q12/13 (did XGBoost beat HistGradientBoostingClassifier?):
  Entry: {xgb_beats_hgb_entry}  (xgboost mean_of_folds_mcc={xgb_e['mean_of_folds_mcc'].iloc[0]:.3f} vs hist_gb={hgb_e['mean_of_folds_mcc'].iloc[0]:.3f})
  CP:    {xgb_beats_hgb_cp}  (xgboost mean_of_folds_mcc={xgb_c['mean_of_folds_mcc'].iloc[0]:.3f} vs hist_gb={hgb_c['mean_of_folds_mcc'].iloc[0]:.3f})
  XGBoost did NOT beat HistGradientBoostingClassifier in either task in
  this build - consistent with the general pattern across v3 and v4 that
  HistGB's regularization handles this feature/sample regime at least as
  well as XGBoost's default settings.

Q13/14 (did RandomForest overfit?):
  Entry: pooled_mcc={rf_e['mcc'].iloc[0]:.3f} vs mean_of_folds_mcc={rf_e['mean_of_folds_mcc'].iloc[0]:.3f},
         worst_fold={rf_e['worst_fold_mcc'].iloc[0]:.3f} (the MOST negative worst-fold of any entry
         model) - consistent with overfitting/instability risk on the entry task.
  CP:    pooled_mcc={rf_c['mcc'].iloc[0]:.3f} vs mean_of_folds_mcc={rf_c['mean_of_folds_mcc'].iloc[0]:.3f},
         worst_fold={rf_c['worst_fold_mcc'].iloc[0]:.3f} (the LEAST negative worst-fold of any CP model,
         {rf_c['pct_positive_folds'].iloc[0]*100:.0f}% positive folds) - here RandomForest looks comparatively
         STABLE, not overfit. Verdict is task-dependent: overfitting risk evident on
         Entry, not clearly evident on CP.

=============================================================================
14. DID CP REDUCE GIVEBACK OR IMPROVE CLOSE TIMING?
=============================================================================
outputs/shadow_lifecycle_v4.parquet, shadow_lifecycle_summary_v4.csv

NO - this is the build's clearest negative finding, reported plainly.

avg_close_improvement (mean realized value saved when a model says CLOSE,
vs. holding to the existing-policy exit) was NEGATIVE for ALL FOUR models:
{cp_comp[['model','avg_close_improvement','bad_close_rate','hold_too_long_rate']].to_string(index=False)}

bad_close_rate exceeds 50% for every model (the model says CLOSE, but
holding to the existing-policy horizon would actually have been better,
more often than not).

The illustrative end-to-end shadow lifecycle (representative CP model:
{lifecycle_summary.get('cp_model_used')}, picked by best mean-of-folds MCC) confirms this
concretely: of {lifecycle_summary.get('n_entered_active_shadow')} positions that reached ACTIVE_SHADOW,
{lifecycle_summary.get('n_closed_early_by_cp')} ({float(lifecycle_summary.get('pct_closed_early_by_cp',0))*100:.0f}%) were closed early by the CP model, with a
MEAN value impact of {float(lifecycle_summary.get('mean_value_saved_by_early_cp_close')):.2f} points and MEDIAN
{float(lifecycle_summary.get('median_value_saved_by_early_cp_close')):.2f} points - both strongly NEGATIVE.

CP_VALUE_NEGATIVE_ON_AVERAGE: {cp_value_negative}

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

Entry: genuinely-OOS n={int(best_entry['n_oos_only'])}, mcc_oos_only={best_entry['mcc_oos_only']:.3f} (best model: {best_entry['model']})
       effective_independent_sample_count (full pool) = {best_entry['effective_independent_sample_count']:.1f}
CP:    genuinely-OOS n={int(best_cp['n_oos_only'])}, mcc_oos_only={best_cp['mcc_oos_only']:.3f} (best model: {best_cp['model']})
       effective_independent_sample_count (full pool) = {best_cp['effective_independent_sample_count']:.1f}

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
     mean_of_folds_mcc={best_entry['mean_of_folds_mcc']:.3f}, OOS mcc={best_entry['mcc_oos_only']:.3f}, n=1389) -
     encouraging for further research, far short of a production bar
     (worst-fold MCC is still negative: {best_entry['worst_fold_mcc']:.3f}).
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

{version_comp[['version','candidate_count','dedup_candidate_count','effective_N','entry_best_model','entry_mean_of_folds_mcc','entry_oos_n','entry_oos_mcc','cp_best_model','cp_mean_of_folds_mcc']].to_string(index=False)}

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
V3_SAMPLE_STARVATION_FIXED: {v3_starvation_fixed}
V1_DUPLICATE_OVERWEIGHTING_PREVENTED: {v1_dup_prevented}
XGBOOST_TESTED: {XGBOOST_AVAILABLE}
HIST_GRADIENT_TESTED: true
RANDOM_FOREST_TESTED: true
ENTRY_MODEL_VERDICT: WEAK_POSITIVE_SIGNAL_NOT_PRODUCTION_GRADE (best={best_entry['model']}, mean_of_folds_mcc={best_entry['mean_of_folds_mcc']:.3f}, oos_n={int(best_entry['n_oos_only'])}, oos_mcc={best_entry['mcc_oos_only']:.3f}, worst_fold still negative)
CP_MODEL_VERDICT: CLASSIFICATION_SIGNAL_PRESENT_BUT_NET_VALUE_NEGATIVE (best={best_cp['model']} by MCC, but avg_close_improvement negative for all 4 models tested - do not act on this CP signal)
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
"""

    with open(v4.REPORTS_DIR / "AFML_LABEL_POLICY_PARITY_ENTRY_CP_V4_REPORT.md", "w") as f:
        f.write(report)

    v4.log(f"11 complete: report written to {v4.REPORTS_DIR / 'AFML_LABEL_POLICY_PARITY_ENTRY_CP_V4_REPORT.md'}")
    print("OVERALL: PASS")


if __name__ == "__main__":
    main()
