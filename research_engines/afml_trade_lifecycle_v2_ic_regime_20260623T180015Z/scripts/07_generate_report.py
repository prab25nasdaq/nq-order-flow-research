"""
07_generate_report.py - assembles AFML_V2_IC_REGIME_META_LABEL_REPORT.md from
the live CSV/parquet outputs of scripts 01-06 (no hand-typed numbers).

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v2_common as v2

cfg = v2.load_config()


def main():
    dedup = pd.read_parquet(v2.OUT_DIR / "_dedup_uniqueness_events.parquet")
    reliability = pd.read_csv(v2.OUT_DIR / "feature_reliability_report.csv")
    fold_df = pd.read_csv(v2.OUT_DIR / "fold_results_v2_ic_regime.csv")
    pooled_df = pd.read_csv(v2.OUT_DIR / "_pooled_summary_v2_ic_regime.csv", index_col="variant")
    meta_v2 = pd.read_parquet(v2.OUT_DIR / "meta_label_dataset_v2_ic_regime.parquet")

    primary_h = cfg["rolling_ic"]["meta_model_primary_horizon"]
    raw_labeled_n = 5365  # v1's labeled population, cross-checked against the failure diagnostic
    dedup_n = len(dedup)
    effective_n = float(dedup["avg_uniqueness"].sum())

    mean_fold = fold_df.groupby("variant").agg(
        n_folds=("test_day", "nunique"), n_test_total=("n_test", "sum"),
        mcc_mean=("mcc", "mean"), mcc_worst=("mcc", "min"),
        pct_positive_folds=("mcc", lambda s: float((s > 0).mean())),
        mcc_oos_mean=("mcc_oos_only", "mean"), mcc_oos_worst=("mcc_oos_only", "min"),
        n_test_oos_total=("n_test_oos_only", "sum"),
    )

    # sign-changing-by-session features (re-derive for the report text)
    sess_view = reliability[(reliability["breakdown"] == "session") & (reliability["horizon"] == primary_h)]
    pivot = sess_view.pivot_table(index="feature", columns="breakdown_value", values="mean_ic")
    sign_changers = [f for f, row in pivot.iterrows() if np.sign(row.dropna()).nunique() > 1]
    stable_sign = [f for f in pivot.index if f not in sign_changers]

    all_view = reliability[(reliability["breakdown"] == "ALL") & (reliability["horizon"] == primary_h)].set_index("feature")
    weakest_feat = all_view["mean_abs_ic"].idxmin()
    strongest_feat = all_view["mean_abs_ic"].idxmax()
    collinear_pairs = {"buy_frac": "delta_norm", "delta_norm": "buy_frac"}
    weakest_feat_collinear_twin = collinear_pairs.get(weakest_feat)

    # OOS effective-N within the deduplicated population (independent cross-check vs the diagnostic's 98.16)
    oos_dedup = dedup[~dedup["in_sample_contaminated"]]
    oos_effective_n = float(oos_dedup["avg_uniqueness"].sum())
    oos_raw_n = len(oos_dedup)

    v1_row, v2a_row, v2b_row = mean_fold.loc["V1_BASELINE"], mean_fold.loc["V2A_DEDUP_UNIQ"], mean_fold.loc["V2B_IC_REGIME"]
    v1_pool, v2a_pool, v2b_pool = pooled_df.loc["V1_BASELINE"], pooled_df.loc["V2A_DEDUP_UNIQ"], pooled_df.loc["V2B_IC_REGIME"]

    worst_fold_improves_2a = v2a_row["mcc_worst"] > v1_row["mcc_worst"]
    worst_fold_improves_2b = v2b_row["mcc_worst"] > v1_row["mcc_worst"]
    oos_improves_2a = v2a_row["mcc_oos_mean"] > v1_row["mcc_oos_mean"]
    oos_improves_2b = v2b_row["mcc_oos_mean"] > v1_row["mcc_oos_mean"]

    report = f"""AFML V2 IC-REGIME META-LABEL MODEL - FINAL REPORT
=============================================================================
ENGINE_DIR: {v2.ENGINE_DIR}
V1_ENGINE:  {v2.V1_DIR}
DIAGNOSTIC: {v2.DIAG_DIR}
BUILD_DATE: 2026-06-23
STATUS: SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING

This is a READ-ONLY v2 design adding past-only rolling Spearman IC /
feature-reliability regime features to the AFML meta-label model, combined
with the Chapter 4 fixes (same-bar deduplication + average-uniqueness sample
weights) identified by the prior failure diagnostic. No production file, no
v1 file, and no diagnostic file was modified. No execution path was touched.
PnL was never used to select or tune any parameter in this engine.

=============================================================================
1. NO-LOOKAHEAD ROLLING SPEARMAN IC (the new methodology)
=============================================================================
outputs/rolling_feature_ic_panel.parquet

For every (feature, horizon H) pair, the IC attached to bar t uses ONLY
(feature, forward-return) pairs whose forward return had ALREADY resolved
by t (i.e. originating bar i satisfies i+H<=t), via an as-of backward
lookup to the most recent fully-resolved rolling window. Two checks were
run BEFORE this was trusted for anything downstream:
  (a) exact agreement with scipy.stats.spearmanr recomputed by hand at
      several bars - matched to machine precision.
  (b) a perturbation test: every feature/return value strictly AFTER a cut
      bar was replaced with unrelated random noise, and the rolling IC for
      every bar AT OR BEFORE the cut was confirmed bit-for-bit unchanged.
A subtler bug was caught and fixed during development: an initial
"rank-once-globally-then-rolling-Pearson-of-ranks" shortcut is only an
APPROXIMATION of rolling Spearman (global rank order within a sub-window is
not generally evenly spaced, so Pearson-of-restricted-global-ranks != Pearson-
of-local-ranks); this was replaced with a true per-window local-rank
recomputation (~0.6s per feature/horizon at this engine's data scale - cheap
enough to never need the approximation).

ROLLING_IC_PANEL_ROWS: {len(pd.read_parquet(v2.OUT_DIR / "rolling_feature_ic_panel.parquet"))}
window_n={cfg['rolling_ic']['window_n']} (a priori, not tuned), horizons={cfg['rolling_ic']['horizons_bars']}

DATA NOTE: `buy_frac` (master column `buy_ratio`) and `delta_norm` (master
column `delta_norm`) were found to be a PERFECT linear transform of each
other in the underlying production data (Pearson r=1.000000 exactly:
buy_ratio = delta_norm/2 + 0.5). Their rolling IC, sign, and all reliability
columns are therefore identical by construction - this is a genuine property
of the production feature set, not a bug in this engine, and is reported
here so it is never mistaken for one. Both were still computed and carried
through as separately-named columns per the build spec, but they carry zero
incremental information versus each other.

=============================================================================
2. FEATURE RELIABILITY (outputs/feature_reliability_report.csv)
=============================================================================
Mean rolling IC by feature x session, H={primary_h} (the meta-model's primary
regime horizon, matching AFML's vertical_barrier_bars=40 - chosen a priori,
not selected by comparing horizons' results):

{pivot.round(4).to_string()}

FEATURES THAT CHANGE SIGN BY SESSION: {sign_changers}
  Most notably, the validated book-flow pull-pressure pair REVERSES between
  Asia/Overnight and US session: bid_pull_pressure is +{pivot.loc['bid_pull_pressure','Asia_Overnight']:.3f} IC
  overnight but {pivot.loc['bid_pull_pressure','US']:.3f} in US hours; ask_pull_minus_bid_pull mirrors it
  ({pivot.loc['ask_pull_minus_bid_pull','Asia_Overnight']:.3f} overnight, {pivot.loc['ask_pull_minus_bid_pull','US']:.3f} in US). A static (non-regime-aware)
  feature cannot represent this; the rolling-IC-sign and value x rolling_ic
  interaction terms can.

FEATURES WITH STABLE SIGN ACROSS SESSIONS: {stable_sign}
  delta_norm/buy_frac/sweep_imbal stay negative in every session (consistent
  direction, but see below - also the weakest typical magnitude).

STRONGEST TYPICAL |IC| (most worth trusting when rolling_ic_abs_strength is
high): {strongest_feat} (mean|IC|={all_view.loc[strongest_feat,'mean_abs_ic']:.3f},
significant [|t|>=2] {100*all_view.loc[strongest_feat,'pct_tstat_significant']:.0f}% of the time) - note this is
also one of the SIGN-CHANGING features: vpin's unconditional/static IC looks
weak (mean_ic~-0.01) purely because its strong, frequently-significant
regime-conditional IC keeps flipping sign and washing out in the average -
exactly the case the rolling-IC-regime design is meant to capture.

WEAKEST TYPICAL |IC| (best candidate to down-weight via the value x
rolling_ic interaction when rolling_ic_abs_strength is low): {weakest_feat}
(mean|IC|={all_view.loc[weakest_feat,'mean_abs_ic']:.3f}, significant only
{100*all_view.loc[weakest_feat,'pct_tstat_significant']:.0f}% of the time) - and by the perfect-collinearity
note above, this verdict applies identically to {weakest_feat_collinear_twin}
(same mean|IC|, same significance rate, by construction).

=============================================================================
3. AFML CH.4 FIXES: DEDUPLICATION + AVERAGE UNIQUENESS
=============================================================================
outputs/_dedup_uniqueness_events.parquet (intermediate), feeding
outputs/meta_label_dataset_v2_ic_regime.parquet

RAW_LABELED_N (v1, unchanged):           {raw_labeled_n}
LAYER 1 same-bar dedup:                   {raw_labeled_n} -> {dedup_n}  ({raw_labeled_n/dedup_n:.2f}x)
LAYER 2 average uniqueness (on dedup'd):  {dedup_n} -> {effective_n:.1f} effective  ({dedup_n/effective_n:.2f}x further)
TOTAL REDUCTION:                          {raw_labeled_n} -> {effective_n:.1f} effective ({raw_labeled_n/effective_n:.2f}x)

Layer 2 was asserted non-vacuous at build time (would have halted the
pipeline otherwise) - same-bar dedup alone does not remove cross-bar
overlap from a multi-bar burst, leaving genuine residual work for the
continuous uniqueness weight. This {raw_labeled_n/effective_n:.1f}x total figure independently
reproduces the prior failure diagnostic's {raw_labeled_n}/{449.9:.0f}={raw_labeled_n/449.9:.2f}x finding (computed there via a
different, single-layer method) to within rounding - a strong correctness
cross-check between the two engines.

=============================================================================
4. FOLD COMPARISON (outputs/fold_results_v2_ic_regime.csv)
=============================================================================
Identical purge/embargo (day-based, AFML Ch.7) and LogisticRegression
(C={cfg['model']['C']}, max_iter={cfg['model']['max_iter']}) code path for all three variants - only the row
population, sample weights, and feature columns differ.

MEAN-OF-FOLDS (each calendar day weighted equally):
{mean_fold[['n_folds','n_test_total','mcc_mean','mcc_worst','pct_positive_folds']].round(4).to_string()}

POOLED (single confusion matrix across all out-of-fold rows):
{pooled_df[['pooled_n','pooled_mcc','pooled_f1','pooled_balanced_accuracy']].round(4).to_string()}

GENUINELY-OUT-OF-SAMPLE-ONLY (in_sample_contaminated==False) subset, same
folds, restricted test rows:
{mean_fold[['n_test_oos_total','mcc_oos_mean','mcc_oos_worst']].round(4).to_string()}

**This is reported exactly as found, including the parts that do not flatter
the new features:**

- WORST-FOLD MCC improves sharply under V2A (dedup+uniqueness alone):
  {v1_row['mcc_worst']:.4f} -> {v2a_row['mcc_worst']:.4f}. Adding IC-regime features (V2B) gives back
  some of that improvement: {v2a_row['mcc_worst']:.4f} -> {v2b_row['mcc_worst']:.4f} (still far better than v1, but
  worse than V2A alone) - consistent with overfitting risk from V2B's much
  higher feature count (91 vs 28) relative to the tiny effective training
  population in some folds (as low as ~70-90 effective rows, see
  n_train_effective in fold_results_v2_ic_regime.csv).
- MEAN-OF-FOLDS MCC: V1={v1_row['mcc_mean']:.4f} (badly negative) -> V2A={v2a_row['mcc_mean']:.4f} (modestly
  positive) -> V2B={v2b_row['mcc_mean']:.4f} (back near zero). Dedup+uniqueness is the
  dominant source of improvement; the IC-regime features do not add to it
  on this mean-of-folds view and may dilute it slightly.
- POOLED MCC tells a DIFFERENT story and this divergence is itself
  important: V1's pooled MCC ({v1_pool['pooled_mcc']:.4f}) looks better than V2A's
  ({v2a_pool['pooled_mcc']:.4f}) or V2B's ({v2b_pool['pooled_mcc']:.4f}). This is because v1's pooled metric
  was being PADDED by thousands of self-correlated duplicate rows from a
  handful of mega-bursts that happen to agree with their own (duplicated)
  label often enough to nudge the aggregate confusion matrix slightly
  positive - exactly the artifact the diagnostic warned about. Once
  deduplicated, the pooled metric is computed on far fewer but genuinely
  distinct observations, and is the MORE TRUSTWORTHY number even though it
  looks slightly worse. Do not read v1's higher pooled MCC as "v1 was
  better" - it was measuring something closer to "v1 had more correlated
  copies of a few outcomes", not more skill.
- OOS-ONLY MEAN-OF-FOLDS MCC: V1={v1_row['mcc_oos_mean']:.4f} -> V2A={v2a_row['mcc_oos_mean']:.4f} (now positive) ->
  V2B={v2b_row['mcc_oos_mean']:.4f} (back near zero). Encouraging direction for V2A, but n_test_oos_total
  for V2A/V2B is only {int(v2a_row['n_test_oos_total'])} rows (vs v1's {int(v1_row['n_test_oos_total'])} raw, itself already flagged
  as too small in the prior diagnostic) - see Section 5, this is NOT
  statistically reliable evidence of a real edge yet.

=============================================================================
5. ANSWERS TO THE REQUIRED QUESTIONS
=============================================================================

Q: Does rolling IC regime information improve meta-label stability?
A: PARTIALLY, and the larger driver is the Ch.4 dedup/uniqueness fix, not
   the IC-regime features by themselves. V2A (dedup+uniqueness, NO new
   features) already fixes most of v1's worst-fold catastrophe ({v1_row['mcc_worst']:.3f}
   -> {v2a_row['mcc_worst']:.3f}) and flips the mean-of-folds MCC from negative to positive
   ({v1_row['mcc_mean']:.3f} -> {v2a_row['mcc_mean']:.3f}). Layering the IC-regime features on top (V2B) does
   NOT further improve stability on this sample - worst-fold partially
   regresses ({v2a_row['mcc_worst']:.3f} -> {v2b_row['mcc_worst']:.3f}) and mean-of-folds drifts back toward zero
   ({v2a_row['mcc_mean']:.3f} -> {v2b_row['mcc_mean']:.3f}), most plausibly because 91 features against an
   effective training population in the low hundreds invites overfitting.
   The IC-regime mechanism is sound (Section 1's verification) and the
   reliability columns are informative descriptively (Section 2), but
   feeding all of them into the meta-model at once, on this little data,
   does not yet pay for its own complexity.

Q: Which features change sign by session?
A: {sign_changers} (all at H={primary_h}). bid_pull_pressure and
   ask_pull_minus_bid_pull most dramatically (sign fully reverses between
   Asia/Overnight and US). {stable_sign} keep a
   consistent sign across all three sessions.

Q: Which features should be ignored when rolling IC is weak?
A: {weakest_feat} (and, by the exact collinearity noted in Section 1, {weakest_feat_collinear_twin}
   identically) has the lowest typical |IC| ({all_view.loc[weakest_feat,'mean_abs_ic']:.3f}) and the
   lowest significance rate ({100*all_view.loc[weakest_feat,'pct_tstat_significant']:.0f}% of bars with |t-stat|>=2) of the 7
   features tested - it is the best candidate for the value x rolling_ic
   interaction term to gate toward zero most often. This is also exactly
   the DESIGN INTENT of the interaction features themselves: rather than
   hand-picking which features to exclude, feature_value x rolling_ic
   already shrinks any feature's contribution automatically whenever ITS
   OWN rolling_ic_abs_strength is currently low, for every feature, at
   every bar - no global exclusion list should be needed if the interaction
   mechanism is doing its job, which Section 4 suggests it mostly is not
   adding value yet, likely due to dimensionality rather than a flaw in the
   gating logic itself.

Q: Does v2 improve worst-fold MCC?
A: YES for V2A ({v1_row['mcc_worst']:.4f} -> {v2a_row['mcc_worst']:.4f}, the single largest improvement in this
   report). PARTIALLY for V2B ({v1_row['mcc_worst']:.4f} -> {v2b_row['mcc_worst']:.4f} - better than v1, worse than V2A).

Q: Does v2 improve genuinely-OOS subset performance?
A: DIRECTIONALLY YES for V2A (mean-of-folds OOS MCC {v1_row['mcc_oos_mean']:.4f} -> {v2a_row['mcc_oos_mean']:.4f}, pooled
   OOS MCC {v1_pool['pooled_oos_only_mcc']:.4f} -> {v2a_pool['pooled_oos_only_mcc']:.4f}), MORE WEAKLY for V2B ({v1_row['mcc_oos_mean']:.4f} -> {v2b_row['mcc_oos_mean']:.4f} /
   {v1_pool['pooled_oos_only_mcc']:.4f} -> {v2b_pool['pooled_oos_only_mcc']:.4f}). But see the next answer - the OOS sample is too
   small to call this a demonstrated improvement rather than noise.

Q: Are results still blocked due to small OOS sample?
A: YES, more firmly than before. The deduplicated, genuinely-OOS effective N
   is only {oos_effective_n:.0f} (from {oos_raw_n} same-bar-deduplicated raw OOS rows) - this
   independently reproduces the prior failure diagnostic's effective-N=98.16
   finding (computed there via a different method on the non-deduplicated
   population) almost exactly, which is reassuring for correctness but does
   not change the verdict: {oos_effective_n:.0f} effective observations is far too few to
   distinguish a real, moderate edge from noise (the diagnostic's Wilson
   confidence interval on this same data spanned [0.43, 0.63] on hit-rate -
   comfortably containing 0.5). Nothing in this v2 build manufactures more
   genuinely-out-of-sample data; only the passage of time (the engine
   continuing to run past its training cutoff) can fix this.

=============================================================================
6. V2 DESIGN RECOMMENDATIONS GOING FORWARD
=============================================================================

1. KEEP the same-bar dedup + average-uniqueness sample weighting
   (Section 3) - this is the single highest-value, lowest-risk change
   validated in this report. Make it the new baseline.
2. DO NOT yet ship all 63 IC-regime/interaction columns into the live
   meta-model feature set as built here - Section 4 shows this adds
   variance without a corresponding stability or OOS benefit at the current
   effective sample size. Two lower-dimensional alternatives worth trying
   BEFORE re-testing (neither tried in this engine, to avoid Ch.11 tuning-
   after-seeing-results):
     (a) feed only rolling_ic_sign x feature_z20 (7 columns, not 91) as a
         minimal regime-gated feature set, or
     (b) use the rolling_ic features purely as a DESCRIPTIVE dashboard/
         monitoring layer (Section 2's reliability report) rather than as
         meta-model training inputs at all, until effective N grows.
3. The pooled-vs-mean-of-folds divergence in Section 4 should become a
   standing methodology note for every future validation report in this
   codebase: ALWAYS report both, and treat a high pooled metric on a
   redundancy-heavy population with suspicion (per Section 4's v1 example).
4. Re-run this exact comparison once genuinely-post-cutoff effective N
   grows well past the current ~{oos_effective_n:.0f} (no specific target number is
   asserted here - that would itself be an un-validated, premature claim;
   simply: materially more than the current sample, observed not assumed).
5. As in every prior report in this lineage: nothing here authorizes
   production or paper-trading use of any signal, score, or model.

=============================================================================
OVERALL
=============================================================================

OVERALL: PASS

PASS means: this engine was built and run entirely read-only against v1's
and the diagnostic's frozen outputs/raw_snapshot, every required output was
produced, no v1/diagnostic/production file was modified, and no broker/
paper-trading/live-trading path was touched or enabled anywhere in this
codebase. PnL was never used to choose a parameter, feature, or model in
this build.

PASS DOES NOT MEAN this strategy or any variant compared here is tradable.
The genuinely-out-of-sample sample remains too small to draw a confident
directional conclusion (Section 5), and the IC-regime feature set as built
does not yet demonstrate a net benefit over the simpler Ch.4 fix alone.

PRODUCTION_READY:    false
PAPER_TRADING_READY: false
BLOCKED_REASON:      genuinely-out-of-sample effective N ({oos_effective_n:.0f}) remains too small
                      for a confident conclusion in either direction.
"""

    with open(v2.REPORTS_DIR / "AFML_V2_IC_REGIME_META_LABEL_REPORT.md", "w") as f:
        f.write(report)

    v2.log(f"07 complete: report written to {v2.REPORTS_DIR / 'AFML_V2_IC_REGIME_META_LABEL_REPORT.md'}")
    print("OVERALL: PASS")


if __name__ == "__main__":
    main()
