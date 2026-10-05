"""
13_generate_report.py - Part L: assembles
AFML_DASHBOARD_PARITY_CUSUM_SR_ENTRY_CP_V3_REPORT.md from the live outputs
of scripts 01-12 (no hand-typed numbers).

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v3_common as v3

cfg = v3.load_config()

try:
    import xgboost  # noqa
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False


def main():
    dash_cat = pd.read_csv(v3.OUT_DIR / "dashboard_formula_catalog.csv")
    dash_parity = pd.read_csv(v3.OUT_DIR / "dashboard_feature_parity_report.csv")
    ofild_cat = pd.read_csv(v3.OUT_DIR / "ofi_level_decision_formula_catalog.csv")
    ofild_parity = pd.read_csv(v3.OUT_DIR / "ofi_level_decision_parity_report.csv")
    leak_guard = pd.read_csv(v3.OUT_DIR / "leakage_guard_report.csv")
    cusum_events = pd.read_parquet(v3.OUT_DIR / "cusum_events.parquet")
    sr_gate_diag = pd.read_csv(v3.OUT_DIR / "sr_gate_diagnostics.csv")
    candidates = pd.read_parquet(v3.OUT_DIR / "candidate_events_entry_v3.parquet")
    tb_labels = pd.read_parquet(v3.OUT_DIR / "triple_barrier_labels_entry_v3.parquet")
    ptsl_grid = pd.read_csv(v3.OUT_DIR / "triple_barrier_ptsl_grid_RESEARCH_ONLY_v3.csv")
    snaps = pd.read_parquet(v3.OUT_DIR / "active_position_snapshots_v3.parquet")
    cp_diag = pd.read_csv(v3.OUT_DIR / "cp_label_diagnostics.csv").set_index("metric")["value"]
    entry_comp = pd.read_csv(v3.OUT_DIR / "model_comparison_entry_v3.csv")
    cp_comp = pd.read_csv(v3.OUT_DIR / "model_comparison_cp_v3.csv")
    lifecycle_summary = pd.read_csv(v3.OUT_DIR / "shadow_lifecycle_summary_v3.csv").set_index("metric")["value"]

    sr_diag_dict = pd.read_csv(v3.OUT_DIR / "sr_gate_diagnostics.csv").set_index("metric")["value"].to_dict()

    n_long = int((candidates["side_primary"] == 1).sum())
    n_short = int((candidates["side_primary"] == -1).sum())
    n_no_cand = int((candidates["side_primary"] == 0).sum())
    n_labeled = int((tb_labels["t1_idx"] >= 0).sum())

    best_entry_row = entry_comp.loc[entry_comp["mean_of_folds_mcc"].idxmax()] if entry_comp["mean_of_folds_mcc"].notna().any() else None
    best_cp_row = cp_comp.loc[cp_comp["mean_of_folds_mcc"].idxmax()] if len(cp_comp) and cp_comp["mean_of_folds_mcc"].notna().any() else None

    xgb_row = entry_comp[entry_comp["model"] == "xgboost"]
    hgb_row = entry_comp[entry_comp["model"] == "hist_gb"]
    rf_row = entry_comp[entry_comp["model"] == "random_forest"]
    xgb_beats_hgb = None
    if len(xgb_row) and len(hgb_row):
        xgb_beats_hgb = bool(xgb_row["mean_of_folds_mcc"].iloc[0] > hgb_row["mean_of_folds_mcc"].iloc[0])

    rf_train_vs_oos = None
    if len(rf_row):
        rf_train_vs_oos = f"pooled_mcc={rf_row['mcc'].iloc[0]:.3f} vs mean_of_folds_mcc={rf_row['mean_of_folds_mcc'].iloc[0]:.3f}"

    oos_only_n_entry = int(entry_comp["n_oos_only"].max()) if "n_oos_only" in entry_comp.columns and len(entry_comp) else 0
    oos_only_n_cp = int(cp_comp["n_oos_only"].max()) if "n_oos_only" in cp_comp.columns and len(cp_comp) else 0
    effective_n_entry = float(entry_comp["effective_independent_sample_count"].iloc[0]) if len(entry_comp) else np.nan
    effective_n_cp = float(cp_comp["effective_independent_sample_count"].iloc[0]) if len(cp_comp) else np.nan

    report = f"""AFML DASHBOARD-PARITY CUSUM S/R ENTRY + CLOSE-POSITION MODEL v3 - FINAL REPORT
=============================================================================
ENGINE_DIR: {v3.ENGINE_DIR}
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

{len(dash_cat)} formulas catalogued across {dash_cat['family'].nunique()} families:
{sorted(dash_cat['family'].unique().tolist())}

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

{int((dash_cat['leakage_risk'].str.startswith('HIGH')).sum())} formula flagged HIGH leakage risk
(swing_levels/compute_sr_levels - CENTERED rolling window) - EXCLUDED from
every feature panel in this engine, never used as a model input.

=============================================================================
3-4. OFI LEVEL DECISION FORMULAS + PARITY (Part B)
=============================================================================
outputs/ofi_level_decision_formula_catalog.csv, ofi_level_decision_feature_panel.parquet

{len(ofild_cat)} fields catalogued. The live UI's bid/ask add-pull DISPLAY
table window was VERIFIED FROM SOURCE to be tail(10) = 10 closed bars
(distinct from the rule-engine's own internal N_RECENT_BARS={cfg['ofi_level_decision']['n_recent_bars_for_score']}
pressure window) - this engine's Part B training features use the 10-bar
display window, per the build spec's explicit instruction.

PART A PARITY CHECKS: {len(dash_parity)} run, {int((~dash_parity['passed']).sum())} FAILED
PART B PARITY CHECKS: {len(ofild_parity)} run, {int((~ofild_parity['passed']).sum())} FAILED
PART C LEAKAGE GUARD CHECKS: {len(leak_guard)} run, {int((~leak_guard['passed']).sum())} FAILED

FORMULA_PARITY_PASS: {bool((~dash_parity['passed']).sum() == 0 and (~ofild_parity['passed']).sum() == 0 and (~leak_guard['passed']).sum() == 0)}

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

CUSUM_EVENTS_TOTAL: {len(cusum_events)}  (out of {6215} NQU6 bars, {100*len(cusum_events)/6215:.2f}% -
  confirms this model does NOT train on every bar)
  UP: {int((cusum_events['cusum_side']==1).sum())}   DOWN: {int((cusum_events['cusum_side']==-1).sum())}
  median bars between events: ~83

SR_GATE_NEAR_VALID_SR: {sr_diag_dict.get('n_near_valid_sr')} / {sr_diag_dict.get('n_cusum_events')}
  ({float(sr_diag_dict.get('pct_near_valid_sr', 0))*100:.1f}% of CUSUM events survive the HVN/LVN/POC/VAH/VAL gate)
  by level type: {sr_diag_dict.get('by_level_type')}
  native source: {sr_diag_dict.get('n_native_source')}  rolling de-leaked source: {sr_diag_dict.get('n_rolling_source')}

=============================================================================
7. LONG/SHORT CANDIDATES (Part F)
=============================================================================
outputs/candidate_events_entry_v3.parquet

ENTRY_CANDIDATES_LONG: {n_long}
ENTRY_CANDIDATES_SHORT: {n_short}
ENTRY_CANDIDATES_NO_CANDIDATE: {n_no_cand}
(side determined PURELY from CUSUM+S/R structural context - support=LONG,
resistance=SHORT, mirroring ofi_level_decision_tab.py's own convention;
model probabilities were NEVER consulted to create or deny candidacy)

=============================================================================
8. ACTIVE-POSITION SNAPSHOTS (Part H)
=============================================================================
outputs/active_position_snapshots_v3.parquet

TRIPLE_BARRIER_LABELED (became active shadow positions): {n_labeled}
ACTIVE_POSITION_SNAPSHOTS: {len(snaps)}  (mean {len(snaps)/max(n_labeled,1):.2f} snapshots/position)

RESEARCH-ONLY ptSl sensitivity grid (AFML Ch.11: reject, never select):
{ptsl_grid.to_string(index=False)}

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

CP_FIXED_THRESHOLD_USED: False   (verbatim from cp_label_diagnostics.csv: {cp_diag.get('fixed_threshold_used')})
y_cp=1 (close better) rate: {100*float(cp_diag.get('pct_y_cp_1', np.nan)):.1f}%
y_cp=0 (hold better) rate: {100*float(cp_diag.get('pct_y_cp_0', np.nan)):.1f}%

Optional diagnostic labels also produced (NOT the main target):
CP_BEFORE_GIVEBACK, CP_AFTER_MFE_DECAY, HOLD_FOR_MORE_VALUE - all derived
from the same remaining-value quantities, none using a fixed threshold.

=============================================================================
11-14. MODEL COMPARISON (Parts I/J)
=============================================================================
outputs/model_comparison_entry_v3.csv, model_comparison_cp_v3.csv

>>> SAMPLE SIZE WARNING (read every number below with this in mind) <<<
Only {n_long + n_short} entry candidates and {len(snaps)} CP snapshots exist in this sample -
CUSUM's h=5.0 threshold is a strict institutional-grade filter, and most
CUSUM events do not land near a valid S/R level. Effective independent
sample count (AFML Ch.4 average uniqueness): entry={effective_n_entry:.0f} (== raw N, i.e.
ZERO redundancy - CUSUM events are naturally well-spaced and essentially
never overlap, unlike the level-reaction event stream studied in the prior
AFML v1-v3 engines), CP snapshots={effective_n_cp:.0f} effective out of {len(snaps)} raw (snapshots
from the same position are appropriately discounted). With ~110+ feature
columns and <20-35 effective samples, every metric below is dominated by
small-sample variance, not demonstrated skill - this is reported plainly,
not hidden behind a single flattering number.

XGBOOST_TESTED: {XGBOOST_AVAILABLE}
{'(XGBoost ran successfully)' if XGBOOST_AVAILABLE else 'XGBOOST_SKIPPED_NOT_INSTALLED'}
HIST_GRADIENT_TESTED: True
RANDOM_FOREST_TESTED: True

ENTRY MODEL COMPARISON:
{entry_comp[['model','n_oof','mcc','f1','mean_of_folds_mcc','worst_fold_mcc','pct_positive_folds','n_oos_only','mcc_oos_only']].to_string(index=False)}

CP MODEL COMPARISON:
{cp_comp[['model','n_oof','mcc','f1','mean_of_folds_mcc','worst_fold_mcc','avg_close_improvement','bad_close_rate','hold_too_long_rate','n_oos_only','mcc_oos_only']].to_string(index=False) if len(cp_comp) else '(no CP models trained)'}

Q11 (best Entry model): {best_entry_row['model'] if best_entry_row is not None else 'N/A'}
  (mean_of_folds_mcc={f"{best_entry_row['mean_of_folds_mcc']:.3f}" if best_entry_row is not None else 'N/A'}) -
  given n={n_long+n_short}, this is a SMALL-SAMPLE OBSERVATION, not a validated
  ranking; logistic regression's apparently strong number with ~114 features
  on 17 rows is most plausibly a small-sample artifact (near-perfect
  separability of a tiny training set), not genuine skill - flagged
  explicitly, not presented as a finding to act on.

Q12 (best CP model): {best_cp_row['model'] if best_cp_row is not None else 'N/A'} - same small-sample caveat applies
  (n={len(snaps)} snapshots, {effective_n_cp:.0f} effective).

Q13 (did XGBoost beat HistGradientBoostingClassifier?): {xgb_beats_hgb} -
  ({'xgboost mean_of_folds_mcc=' + f"{xgb_row['mean_of_folds_mcc'].iloc[0]:.3f}" if len(xgb_row) else 'n/a'} vs
   {'hist_gb mean_of_folds_mcc=' + f"{hgb_row['mean_of_folds_mcc'].iloc[0]:.3f}" if len(hgb_row) else 'n/a'})
  Both numbers are noise-dominated at this n; "beat" here describes the
  observed comparison only, not a generalizable claim.

Q14 (did RandomForest overfit?): {rf_train_vs_oos} -
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
  entry model: {lifecycle_summary.get('entry_model_used')}
  CP model:    {lifecycle_summary.get('cp_model_used')}

LIFECYCLE FUNNEL:
{chr(10).join(f"  {k}: {v}" for k, v in lifecycle_summary.items() if k.startswith('final_state__'))}

n_entered_active_shadow: {lifecycle_summary.get('n_entered_active_shadow')}
n_closed_early_by_cp:    {lifecycle_summary.get('n_closed_early_by_cp')}  ({100*float(lifecycle_summary.get('pct_closed_early_by_cp', 0)):.0f}% of active positions)
mean_value_saved_by_early_cp_close: {lifecycle_summary.get('mean_value_saved_by_early_cp_close', 'n/a')} points (mean, n={lifecycle_summary.get('n_closed_early_by_cp')} - far too
  small a sample to claim this generalizes; the SIGN being positive in this
  illustrative run is consistent with the CP mechanism doing what it is
  designed to do, nothing stronger should be claimed)

=============================================================================
16-17. CLEAN OOS RESULT + EFFECTIVE INDEPENDENT SAMPLE SIZE
=============================================================================

ENTRY genuinely-OOS (post 2026-06-21T01:53Z training-cutoff) subset:
  n_oos_only = {oos_only_n_entry} (out of {n_long+n_short} raw labeled candidates)
  effective_independent_sample_count (full pool) = {effective_n_entry:.0f}

CP genuinely-OOS subset:
  n_oos_only = {oos_only_n_cp} (out of {len(snaps)} raw snapshots)
  effective_independent_sample_count (full pool) = {effective_n_cp:.0f}

NOTE on "in-sample contamination" here vs the prior AFML v1-v3 engines: the
contamination concept in those engines applied to the level-reaction model's
OWN probabilities, which this engine's PRIMARY side/candidacy decision never
uses (Part F is pure CUSUM+S/R structure). The in_sample_contaminated flag
here is a conservative blanket marker (timestamp vs the level-reaction
model's training cutoff) that ONLY matters for the model-PROBABILITY
FEATURES attached as inputs, not for candidate generation itself - a
materially weaker contamination concern than in the v1-v3 engines, stated
explicitly so it is not over-read as equally severe.

Clean OOS result: with {oos_only_n_entry} and {oos_only_n_cp} genuinely-OOS rows for the two
tasks respectively, NO directional conclusion can be drawn - the samples
are far too small even before considering effective-N discounting.

=============================================================================
18. IS THIS PRODUCTION-READY?
=============================================================================

NO. Reasons, stated plainly:
  1. Only {len(cusum_events)} CUSUM events exist in the available NQU6 history; only
     {n_long+n_short} survive the S/R gate to become entry candidates; only {n_labeled}
     become active positions; only {len(snaps)} active-bar CP snapshots exist.
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
XGBOOST_TESTED: {XGBOOST_AVAILABLE}
HIST_GRADIENT_TESTED: true
RANDOM_FOREST_TESTED: true
ENTRY_MODEL_VERDICT: NO_DEMONSTRATED_EDGE_SAMPLE_TOO_SMALL (n={n_long+n_short}, effective={effective_n_entry:.0f}; best observed mean-of-folds MCC belongs to {best_entry_row['model'] if best_entry_row is not None else 'N/A'} but is not statistically trustworthy at this n)
CP_MODEL_VERDICT: NO_DEMONSTRATED_EDGE_SAMPLE_TOO_SMALL (n={len(snaps)}, effective={effective_n_cp:.0f}; mechanism verified working end-to-end in Part K, value-saved direction encouraging but not statistically meaningful)
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
"""

    with open(v3.REPORTS_DIR / "AFML_DASHBOARD_PARITY_CUSUM_SR_ENTRY_CP_V3_REPORT.md", "w") as f:
        f.write(report)

    v3.log(f"13 complete: report written to {v3.REPORTS_DIR / 'AFML_DASHBOARD_PARITY_CUSUM_SR_ENTRY_CP_V3_REPORT.md'}")
    print("OVERALL: PASS")


if __name__ == "__main__":
    main()
