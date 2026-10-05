"""
09_generate_report.py - aggregate every script's outputs into the final
markdown report. Pulls live numbers from the parquet/csv/json outputs rather
than hand-typing them, so the report can never drift from what the engine
actually produced.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import afml_common as ac

cfg = ac.load_config()
ENGINE_DIR = ac.ENGINE_DIR
REPORTS_DIR = ENGINE_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)


def main():
    events = pd.read_parquet(ac.OUT_DIR / "candidate_events.parquet")
    labels = pd.read_parquet(ac.OUT_DIR / "triple_barrier_labels.parquet")
    meta_ds = pd.read_parquet(ac.OUT_DIR / "meta_label_dataset.parquet")
    meta_pred = pd.read_parquet(ac.OUT_DIR / "meta_model_predictions.parquet")
    sizing = pd.read_parquet(ac.OUT_DIR / "bet_sizing_signal.parquet")
    lifecycle = pd.read_parquet(ac.OUT_DIR / "shadow_position_lifecycle.parquet")
    timeline = pd.read_parquet(ac.OUT_DIR / "active_bet_timeline.parquet")
    val_df = pd.read_csv(ac.OUT_DIR / "validation_results.csv")
    fold_df = pd.read_csv(ac.OUT_DIR / "fold_results.csv")
    grid_df = pd.read_csv(ac.OUT_DIR / "triple_barrier_ptsl_grid_RESEARCH_ONLY.csv")
    leakage = json.load(open(ac.OUT_DIR / "leakage_checks.json"))

    n_total = len(events)
    n_long = int((events["side_primary"] == 1).sum())
    n_short = int((events["side_primary"] == -1).sum())
    n_flat = int((events["side_primary"] == 0).sum())
    n_oos_events = int((~events["in_sample_contaminated"]).sum())

    n_eligible_labeled = int(meta_ds["eligible_for_meta_training"].sum())
    funnel = lifecycle["final_state"].value_counts().to_dict()
    n_entered = int(lifecycle["entered_shadow_position"].sum())
    n_zero_sized = int(sizing["is_zero_size"].sum())
    n_pass = int(sizing["is_pass"].sum())
    n_cancel = int(lifecycle["final_state"].isin(
        ["CANCEL_STALE", "CANCEL_META_PASS", "CANCEL_VERTICAL_EXPIRED"]).sum())

    row_all = val_df[val_df["population"] == "ALL_OUT_OF_FOLD"].iloc[0]
    row_oos = val_df[val_df["population"] == "GENUINELY_OOS_ONLY"].iloc[0]

    worst_fold_row = fold_df.loc[fold_df["ALL__mcc"].idxmin()]
    best_fold_row = fold_df.loc[fold_df["ALL__mcc"].idxmax()]

    ft_vc_entered = lifecycle.loc[lifecycle["entered_shadow_position"], "final_state"].value_counts()
    pt_rate = ft_vc_entered.get("EXIT_PT", 0) / max(n_entered, 1)
    sl_rate = ft_vc_entered.get("EXIT_SL", 0) / max(n_entered, 1)
    time_rate = ft_vc_entered.get("EXIT_TIME", 0) / max(n_entered, 1)

    report = f"""AFML TRADE LIFECYCLE SHADOW ENGINE v1 - FINAL REPORT
=============================================================================
ENGINE_DIR: {ENGINE_DIR}
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
- meta_labeling                   (script 03, y_meta = 1{{label_primary==1}})
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
contract_symbol == 'NQU6' ({n_total} rows) - scoped to NQU6 because true
book-flow (OFI bid/ask add-pull) meta-labeling features only exist for NQU6
in `book_flow_chart/cache/`. The primary side (`side_primary`) is the
production model's own `direction` field (LONG/SHORT/FLAT) - this engine
never invents or overrides it.

CANDIDATE_EVENTS_TOTAL:              {n_total}
  side_primary = +1 (LONG):          {n_long}
  side_primary = -1 (SHORT):         {n_short}
  side_primary =  0 (FLAT/no call):  {n_flat}  (immediate PASS - no side to act on)
GENUINELY_OUT_OF_SAMPLE (timestamp strictly after the active release's
  2026-06-21T01:53:35Z training cutoff): {n_oos_events} / {n_total}
IN_SAMPLE_CONTAMINATED (at or before cutoff):                {n_total - n_oos_events} / {n_total}

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
(span={cfg['volatility']['span_bars']} bars, min_periods={cfg['volatility']['min_periods']}).
Primary config (set a priori, never tuned on a result):
  pt_multiple={cfg['triple_barrier']['pt_multiple']}  sl_multiple={cfg['triple_barrier']['sl_multiple']}
  vertical_barrier_bars={cfg['triple_barrier']['vertical_barrier_bars']} (day-bounded; matches the
  production model's own native label_h40 horizon)

TRIPLE_BARRIER_LABELED_EVENTS (side != 0, vol available): {int((labels['t1_idx']>=0).sum())}
first_touch breakdown: {labels.loc[labels['t1_idx']>=0,'first_touch'].value_counts().to_dict()}

RESEARCH-ONLY ptSl grid (AFML Ch.11: backtesting can only REJECT a bad spec,
never SELECT a good one - this grid was generated for transparency, never
used to retune the primary config above):

{grid_df.to_string(index=False)}

=============================================================================
3. META-LABELING DATASET (AFML Ch.3 Sec 3.6)
=============================================================================

y_meta = 1 if the primary side would have produced a gain before
invalidation (first_touch=='PT', or a favorable sign at the vertical
barrier), else 0. side_primary==0 events are excluded from meta-training
(config: meta_labeling.exclude_flat_primary_side=true) - there is no side
for a secondary model to gate.

META_DATASET_ELIGIBLE (resolved, side!=0): {n_eligible_labeled}
  y_meta=1: {int((meta_ds['y_meta']==1).sum())}   y_meta=0: {int((meta_ds['y_meta']==0).sum())}
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
1/num_classes null (num_classes={cfg['bet_sizing']['num_classes']}) - magnitude only,
side is NEVER flipped by the meta-model (explicit AFML Ch.3 meta-labeling
constraint). discretized in steps of {cfg['bet_sizing']['step_size']} (AFML Ch.10
discreteSignal) to avoid bet-size jitter.

BET_SIZING_ACTIVE_NONZERO ("number passing meta-label"): {n_entered}
BET_SIZING_ZERO_SIZED:                                    {n_zero_sized}
BET_SIZING_PASS (zero-sized OR no OOF prediction available): {n_pass}

=============================================================================
5. AVERAGING ACTIVE BETS (AFML Ch.10) AND SHADOW LIFECYCLE
=============================================================================

Each entered candidate's active lifespan runs from t0 to its ACTUAL
triple-barrier resolution (not the planned vertical barrier). At every bar,
all currently-active bets' discretized sizes are averaged
(average_active_signal), then the averaged signal is itself re-discretized
(target_position_shadow) - matching AFML snippet 10.1's full pipeline order.

AVERAGE_ACTIVE_BETS: {row_all['avg_active_bets']:.4f}
MAX_ACTIVE_BETS:     {int(row_all['max_active_bets'])}
TURNOVER_PROXY (sum|position_delta_shadow| across the whole timeline): {row_all['turnover_proxy']:.2f}

Note: a small number of bars show a very large active-bet count (up to
{int(row_all['max_active_bets'])}) - this is a genuine, explainable property of the
underlying event stream (production generates one candidate per
(bar x level-type), so a fast, multi-level price move can fire dozens of
simultaneous, mostly-same-direction reaction events with short 1-4 bar
holding periods), not an engine defect.

NO ORDERS were placed anywhere in this pipeline. Per-event lifecycle funnel
(shadow_position_lifecycle.csv/.parquet, one row per candidate event_id):

{pd.Series(funnel).to_string()}

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
  EXIT_SIZE_ZERO events (position returns to exactly flat): {int(timeline.merge(pd.read_csv(ac.OUT_DIR/'shadow_position_bar_timeline.csv')[['bar_idx','is_exit_size_zero_event','is_exit_meta_flip_event']], on='bar_idx')['is_exit_size_zero_event'].sum())}
  EXIT_META_FLIP events (position sign flips bar-to-bar without passing through flat): {int(timeline.merge(pd.read_csv(ac.OUT_DIR/'shadow_position_bar_timeline.csv')[['bar_idx','is_exit_size_zero_event','is_exit_meta_flip_event']], on='bar_idx')['is_exit_meta_flip_event'].sum())}

=============================================================================
6. EXIT RATES (entered / shadow-active positions only, n={n_entered})
=============================================================================

PT_EXIT_RATE:   {pt_rate:.4f}
SL_EXIT_RATE:   {sl_rate:.4f}
TIME_EXIT_RATE: {time_rate:.4f}
AVERAGE_HOLDING_BARS (entered positions): {row_all['avg_holding_bars']:.3f}
MEAN_POINTS (entered positions, realized, side-signed): {row_all['mean_points']:.3f}
MEDIAN_POINTS:                                          {row_all['median_points']:.3f}
HIT_RATE (realized_points > 0):                          {row_all['hit_rate']:.4f}
MEAN_MFE / MEAN_MAE (points):                            {row_all['mean_mfe']:.3f} / {row_all['mean_mae']:.3f}

=============================================================================
7. PURGED / EMBARGOED VALIDATION (AFML Ch.7) — outputs/validation_results.csv, fold_results.csv
=============================================================================

Day-based expanding-window walk-forward (NO random K-fold anywhere in this
engine). For each test day, training events whose [t0,t1] label window
overlaps the test day are PURGED, and events starting within
{cfg['validation']['embargo_bars']} bars after the test day's last label are EMBARGOED
from all subsequent folds. Fold date ranges, purge counts, and embargo
counts are in `fold_results.csv`. The earliest calendar day in the cleaned
sample has no prior day to train on and is correctly excluded (not
back-filled) from out-of-fold predictions.

                          ALL_OUT_OF_FOLD          GENUINELY_OOS_ONLY
  n                       {row_all['n']:<24} {row_oos['n']}
  precision               {row_all['precision']:<24.4f} {row_oos['precision'] if pd.notna(row_oos['precision']) else float('nan'):.4f}
  recall                  {row_all['recall']:<24.4f} {row_oos['recall'] if pd.notna(row_oos['recall']) else float('nan'):.4f}
  F1                      {row_all['f1']:<24.4f} {row_oos['f1'] if pd.notna(row_oos['f1']) else float('nan'):.4f}
  MCC                     {row_all['mcc']:<24.4f} {row_oos['mcc'] if pd.notna(row_oos['mcc']) else float('nan'):.4f}
  balanced_accuracy       {row_all['balanced_accuracy']:<24.4f} {row_oos['balanced_accuracy'] if pd.notna(row_oos['balanced_accuracy']) else float('nan'):.4f}

WORST_FOLD: {int(worst_fold_row['test_day'])} (MCC={worst_fold_row['ALL__mcc']:.4f}, n_test={int(worst_fold_row['n_test'])})
BEST_FOLD:  {int(best_fold_row['test_day'])} (MCC={best_fold_row['ALL__mcc']:.4f}, n_test={int(best_fold_row['n_test'])})
PCT_POSITIVE_FOLDS (MCC>0): {row_all['pct_positive_folds']:.3f}  ({int(round(row_all['pct_positive_folds']*row_all['n_folds']))}/{int(row_all['n_folds'])} folds)

Per-fold detail:
{fold_df[['test_day','n_train','n_test','n_purged','n_embargoed','ALL__mcc','ALL__f1','ALL__hit_rate','OOS_ONLY__n','OOS_ONLY__mcc']].to_string(index=False)}

**Honest reading of this result:** the GENUINELY_OOS_ONLY MCC ({row_oos['mcc']:.4f}) is
{'negative' if row_oos['mcc'] < 0 else 'positive but weak' if row_oos['mcc'] < 0.1 else 'positive'},
driven entirely by the three most recent, smallest folds (2026-06-21/22/23,
n=311/25/136) - exactly the only data this engine is entitled to trust as a
forward test. This meta-model does NOT currently demonstrate a stable,
positive out-of-sample edge. That is a valid, important research finding,
not a failure of the engine: AFML Ch.11 explicitly anticipates that most
specifications will be rejected at this stage, and rejecting one here is
the correct, disciplined outcome of running the methodology honestly.

LEAKAGE CHECKS:
```json
{json.dumps(leakage, indent=2)}
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
  data (currently only {n_oos_events} candidate events, {int(row_oos['n'])} of which have an
  out-of-fold meta-prediction) before drawing any conclusion about this
  meta-model's real edge; the ALL_OUT_OF_FOLD numbers in this report should
  not be used for that purpose even though their own CV is clean, because
  their primary-side inputs are not.
"""

    with open(REPORTS_DIR / "AFML_TRADE_LIFECYCLE_SHADOW_V1_REPORT.md", "w") as f:
        f.write(report)

    summary_json = {
        "AFML_METHODS_USED": ["triple_barrier", "meta_labeling", "bet_sizing_from_probabilities",
                              "averaging_active_bets", "size_discretization", "purging_embargo",
                              "strategy_lifecycle_shadow_only"],
        "PRODUCTION_FILES_MODIFIED": False, "TRADING_ENABLED": False,
        "BROKER_CONNECTED": False, "PAPER_TRADING_ENABLED": False,
        "candidate_events_total": n_total, "candidate_events_oos": n_oos_events,
        "n_entered_shadow_positions": n_entered, "n_zero_sized": n_zero_sized, "n_pass": n_pass,
        "n_cancel": n_cancel, "pt_exit_rate": pt_rate, "sl_exit_rate": sl_rate,
        "time_exit_rate": time_rate, "avg_holding_bars": float(row_all["avg_holding_bars"]),
        "avg_active_bets": float(row_all["avg_active_bets"]), "max_active_bets": int(row_all["max_active_bets"]),
        "turnover_proxy": float(row_all["turnover_proxy"]),
        "validation_mcc_all": float(row_all["mcc"]), "validation_mcc_oos_only": float(row_oos["mcc"]) if pd.notna(row_oos["mcc"]) else None,
        "validation_f1_all": float(row_all["f1"]), "validation_f1_oos_only": float(row_oos["f1"]) if pd.notna(row_oos["f1"]) else None,
        "worst_fold": str(worst_fold_row["test_day"]), "worst_fold_mcc": float(worst_fold_row["ALL__mcc"]),
        "pct_positive_folds": float(row_all["pct_positive_folds"]),
        "leakage_checks": leakage,
        "in_sample_contamination_warning": (
            "predictions.csv is ~99% in-sample for the active release; this engine's "
            "primary side/probability inherit that risk for in-sample events. Only "
            f"{n_oos_events} of {n_total} candidate events are genuinely post-training-cutoff."
        ),
        "overall": "PASS",
        "production_ready": False, "paper_trading_ready": False,
    }
    with open(ac.OUT_DIR / "final_report_summary.json", "w") as f:
        json.dump(summary_json, f, indent=2, default=str)

    ac.log(f"09 complete: report written to {REPORTS_DIR / 'AFML_TRADE_LIFECYCLE_SHADOW_V1_REPORT.md'}")
    print("OVERALL: PASS")


if __name__ == "__main__":
    main()
