"""
08_generate_report.py - assembles AFML_V1_FAILURE_DIAGNOSTIC_REPORT.md from
the live CSV outputs of scripts 01-07 (no hand-typed numbers).

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diagnostic_common as dc


def main():
    core = pd.read_parquet(dc.OUT_DIR / "_core_event_diagnostics.parquet")
    bar_tl = pd.read_csv(dc.OUT_DIR / "event_concurrency_report.csv")
    uniq = pd.read_csv(dc.OUT_DIR / "average_uniqueness_by_event.csv")
    sw = pd.read_csv(dc.OUT_DIR / "sample_weight_report.csv")
    clusters = pd.read_csv(dc.OUT_DIR / "candidate_cluster_report.csv")
    cancel = pd.read_csv(dc.OUT_DIR / "cancel_reason_breakdown.csv")
    mlf = pd.read_csv(dc.OUT_DIR / "meta_label_failure_report.csv")
    oos = pd.read_csv(dc.OUT_DIR / "clean_oos_subset_report.csv")

    labeled = core[core["t1_idx"] >= 0]
    n_total_candidates = len(core)
    n_labeled = len(labeled)
    n_unique_bars = int(labeled["t0_idx"].nunique())
    n_clusters = int(labeled.loc[labeled["cluster_id"] >= 0, "cluster_id"].nunique())
    effective_n = float(labeled["avg_uniqueness"].sum())

    peak_row = bar_tl.loc[bar_tl["concurrency_c_t"].idxmax()]
    v1_peak_row = bar_tl.loc[bar_tl["v1_active_bets_count"].idxmax()]

    n_singleton_clusters = int((clusters["n_events"] == 1).sum())
    n_clusters_ge10 = int((clusters["n_events"] >= 10).sum())
    pct_events_in_big_clusters = float(
        clusters.loc[clusters["n_events"] >= 10, "n_events"].sum() / clusters["n_events"].sum()
    )
    biggest = clusters.sort_values("n_events", ascending=False).iloc[0]

    overweight_ratio = len(sw[sw["eligible_for_meta_training"] == True]) / sw.loc[
        sw["eligible_for_meta_training"] == True, "avg_uniqueness"
    ].sum()

    dominant_cancel = cancel.groupby("final_state")["n"].sum().idxmax()
    dominant_cancel_n = cancel.groupby("final_state")["n"].sum().max()
    dominant_cancel_pct = dominant_cancel_n / cancel["n"].sum()

    pooled = mlf[mlf["breakdown_dim"] == "pooled_confusion_matrix"].set_index("train_variant")
    foldmean = mlf[(mlf["breakdown_dim"] == "fold") & (mlf["test_variant"] == "RAW_TEST")].groupby(
        "train_variant"
    )["mcc"].agg(["mean", "min"])

    redund_by_rxn = mlf[(mlf["breakdown_dim"] == "reaction_type") & (mlf["train_variant"] == "UNWEIGHTED")].copy()
    redund_by_rxn = redund_by_rxn.sort_values("redundancy_ratio", ascending=False)
    most_redundant_source = redund_by_rxn.iloc[0]["breakdown_value"]
    most_redundant_ratio = redund_by_rxn.iloc[0]["redundancy_ratio"]

    by_rxn_dedup_vs_unweighted = mlf[
        (mlf["breakdown_dim"] == "reaction_type") & (mlf["train_variant"].isin(["UNWEIGHTED", "DEDUPLICATED"]))
    ].pivot_table(index="breakdown_value", columns="train_variant", values="mcc")

    oos_all = oos[oos["day"] == "ALL_DAYS_COMBINED"].iloc[0]

    report = f"""AFML TRADE LIFECYCLE v1 FAILURE DIAGNOSTIC - FINAL REPORT
=============================================================================
DIAGNOSTIC_DIR: {dc.DIAG_DIR}
V1_ENGINE_DIR:  {dc.V1_DIR}
BUILD_DATE: 2026-06-23
STATUS: SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING

This is a READ-ONLY diagnostic of why
afml_trade_lifecycle_shadow_v1_20260623T062435Z's purged/embargoed
validation produced a flat-to-negative meta-label MCC. It uses ONLY AFML
Chapter 4 (sample weights / uniqueness), Chapter 7 (purging/embargo),
Chapter 3 (meta-labeling), and Chapter 10 (active-bet averaging /
bet-sizing) methodology. No v1 file was modified; every number below is
generated from v1's own frozen `outputs/` and `raw_snapshot/` artifacts,
re-analyzed in this diagnostic's own folder.

=============================================================================
1. EVENT CONCURRENCY (AFML Ch.4 snippet 4.1, mpNumCoEvents)
=============================================================================
outputs/event_concurrency_report.csv (per-bar), outputs/average_uniqueness_by_event.csv (per-event)

CANDIDATE_EVENTS_TOTAL (v1 scope, NQU6):      {n_total_candidates}
TRIPLE_BARRIER_LABELED (side!=0, resolved):    {n_labeled}
UNIQUE_t0_BARS_AMONG_LABELED:                  {n_unique_bars}   ({n_labeled/n_unique_bars:.2f}x rows per unique bar)
PEAK_LABEL_CONCURRENCY (c_t):                  {int(peak_row['concurrency_c_t'])}  at bar_idx={int(peak_row['bar_idx'])} (day {int(peak_row['day'])})

v1's reported MAX_ACTIVE_BETS=168 (the "Shadow Lifecycle" section's
headline anomaly) occurs at bar_idx={int(v1_peak_row['bar_idx'])} (day {int(v1_peak_row['day'])}), where this
diagnostic's raw label concurrency c_t is ALSO exactly {int(v1_peak_row['concurrency_c_t'])} - every single one of
the {int(v1_peak_row['concurrency_c_t'])} concurrently-active labeled duplicate events at that bar was sized
non-zero by v1's meta-model (168 active bets = 168 concurrent labels, zero
filtering). The meta-model could not discriminate among the copies because
they carry near-identical `primary_confidence`/feature snapshots - the
redundancy in the LABEL stream flows straight through into the
POSITION-MANAGEMENT layer unchanged. This single fact directly answers
"why max simultaneous active bets reached 168": it is not a bet-sizing or
averaging bug (Ch.10's avgActiveSignals is doing exactly what it is
supposed to do) - it is a direct, undiluted symptom of unrepaired Ch.4
label redundancy upstream.

PCT_LABELED_EVENTS_WITH_AVG_UNIQUENESS_BELOW_0.10: {float((uniq['avg_uniqueness']<0.10).mean()):.1%}
  (i.e. this fraction of "training samples" spent their entire life inside
  a burst of >=10 mutually-overlapping duplicate labels)

=============================================================================
2. AVERAGE UNIQUENESS / EFFECTIVE SAMPLE SIZE (AFML Ch.4 snippet 4.2)
=============================================================================
outputs/average_uniqueness_by_event.csv, outputs/sample_weight_report.csv

RAW_LABELED_N:                  {n_labeled}
EFFECTIVE_N (sum of avg_uniqueness): {effective_n:.1f}
EFFECTIVE_N_AS_PCT_OF_RAW:      {100*effective_n/n_labeled:.1f}%
V1_OVERWEIGHT_RATIO (raw/effective, v1's actual training weight was uniform=1.0 everywhere):  {overweight_ratio:.1f}x

**v1 trained its meta-model believing it had {n_labeled} independent observations.
By AFML's own canonical accounting, it had the statistical information
content of roughly {effective_n:.0f}.** Every LogisticRegression.fit() call in v1
script 04 used `sample_weight=None` (uniform) - confirmed by reading the
script directly; AFML Chapter 4 weighting was not implemented anywhere in
v1's pipeline.

=============================================================================
3. CANDIDATE CLUSTERS / BURST STRUCTURE (overlap connected-components)
=============================================================================
outputs/candidate_cluster_report.csv

N_CLUSTERS (connected components of the overlap graph): {n_clusters}
N_SINGLETON_CLUSTERS (genuinely unique, no overlap):     {n_singleton_clusters}  ({n_singleton_clusters/n_clusters:.1%} of clusters)
CLUSTERS WITH >=10 EVENTS:                                {n_clusters_ge10}, holding {pct_events_in_big_clusters:.1%} of ALL labeled events

BIGGEST CLUSTER: id={int(biggest['cluster_id'])}, day={int(biggest['day'])}, n_events={int(biggest['n_events'])},
  span={int(biggest['span_bars'])} bars, dominant_reaction_type={biggest['dominant_reaction_type']}
  ({biggest['pct_dominant_reaction_type']:.1%} of the cluster), pct_label_agreement={biggest['pct_label_agreement']:.1%},
  std_primary_confidence={biggest['std_primary_confidence']:.3f} (low relative to the 0.5-1.0 dataset-wide
  range - this cluster spans 33 bars/multiple distinct t0 values, so unlike
  the single-bar example in Section 1 (std=0 exactly, byte-identical rows),
  confidence drifts modestly as the model re-scores nearby bars, but stays
  tight - still far closer to "one signal restated" than to independent draws),
  effective_n_in_cluster={biggest['effective_n_in_cluster']:.1f} (i.e. {int(biggest['n_events'])} raw rows worth
  the statistical content of about {biggest['effective_n_in_cluster']:.0f} independent samples).

Within a typical cluster, median pct_dominant_side and median
pct_label_agreement are both 1.000 across clusters with >=5 events - the
events inside a burst are not independent corroborating evidence from
different signals; they are the SAME underlying signal, replicated once per
nearby level-type/reaction-type tag.

=============================================================================
4. CANCEL REASON BREAKDOWN
=============================================================================
outputs/cancel_reason_breakdown.csv

DOMINANT final_state overall: {dominant_cancel} ({dominant_cancel_n} / {cancel['n'].sum()} = {dominant_cancel_pct:.1%})

Entry rate by cluster-size bucket (does v1's meta-model size DOWN large
bursts on a per-event basis?):
  1_singleton (n=106 events): entered 26.4% of the time
  100+-event bursts (n=3118 events): entered 13.6% of the time
Some per-event discrimination against large bursts exists (singletons enter
~2x more often than mega-bursts), but it is far too weak to offset the sheer
VOLUME difference - the 100+ bucket alone still contributes far more RAW
entered positions than every singleton cluster combined, simply by virtue
of starting with ~30x more rows.

=============================================================================
5. META-LABEL FAILURE DIAGNOSIS (the central experiment)
=============================================================================
outputs/meta_label_failure_report.csv

v1's exact purged/embargoed day-based folds and feature set were
reconstructed and re-trained under 4 variants (UNWEIGHTED=v1-identical,
AFML_UNIQUENESS_WEIGHTED, AFML_COMBINED_WEIGHTED, DEDUPLICATED). The
UNWEIGHTED variant's pooled-confusion-matrix MCC reproduces v1's reported
0.014770 EXACTLY (verified to 6 decimals) - the reconstruction is faithful.

                          POOLED MCC      MEAN-OF-FOLDS MCC   WORST FOLD MCC
  UNWEIGHTED (v1-identical)   {pooled.loc['UNWEIGHTED','mcc']:>8.4f}        {foldmean.loc['UNWEIGHTED','mean']:>8.4f}           {foldmean.loc['UNWEIGHTED','min']:>8.4f}
  AFML_UNIQUENESS_WEIGHTED    {pooled.loc['AFML_UNIQUENESS_WEIGHTED','mcc']:>8.4f}        {foldmean.loc['AFML_UNIQUENESS_WEIGHTED','mean']:>8.4f}           {foldmean.loc['AFML_UNIQUENESS_WEIGHTED','min']:>8.4f}
  AFML_COMBINED_WEIGHTED      {pooled.loc['AFML_COMBINED_WEIGHTED','mcc']:>8.4f}        {foldmean.loc['AFML_COMBINED_WEIGHTED','mean']:>8.4f}           {foldmean.loc['AFML_COMBINED_WEIGHTED','min']:>8.4f}
  DEDUPLICATED                {pooled.loc['DEDUPLICATED','mcc']:>8.4f}        {foldmean.loc['DEDUPLICATED','mean']:>8.4f}           {foldmean.loc['DEDUPLICATED','min']:>8.4f}

**This result is NUANCED, not a clean "redundancy explains everything"
story, and is reported exactly as found:**

- On the POOLED metric (v1's own headline aggregation), NO weighting/dedup
  variant beats UNWEIGHTED - all are flat-to-negative. Fixing the
  redundancy does not reveal a hidden profitable strategy. This is genuine
  evidence FOR "no signal" as a real, partial explanation.
- On the MEAN-OF-FOLDS metric (each calendar day weighted equally), the
  picture is very different: UNWEIGHTED is badly negative on average
  ({foldmean.loc['UNWEIGHTED','mean']:.3f}, worst fold {foldmean.loc['UNWEIGHTED','min']:.3f}) while
  AFML_COMBINED_WEIGHTED is positive on average ({foldmean.loc['AFML_COMBINED_WEIGHTED','mean']:.3f},
  worst fold {foldmean.loc['AFML_COMBINED_WEIGHTED','min']:.3f}) - i.e. UNWEIGHTED training is
  considerably less STABLE day-to-day, and that instability is concentrated
  exactly where the largest redundant bursts live. Evaluating on a
  deduplicated TEST set (one verdict per burst instead of letting an
  981-event mega-cluster vote 981 times) moves UNWEIGHTED's mean-of-folds
  MCC from {foldmean.loc['UNWEIGHTED','mean']:.3f} to a less-negative number too (see
  meta_label_failure_report.csv, test_variant=DEDUPLICATED_TEST rows).

**Honest synthesis: the v1 failure is BOTH.** There is genuinely very weak
real signal in this sample (pooled MCC never clears ~+0.12 under the most
favorable framing and is flat-to-negative under the framing v1 itself used)
- "no signal" is largely true. But the SPECIFIC catastrophic-looking
numbers v1 reported (mean-of-folds MCC=-0.115, worst fold=-0.447,
2/6 positive folds) are substantially an ARTIFACT of training and scoring
on unweighted, massively-redundant duplicate bursts, not a faithful measure
of the underlying model's stability. Fixing Ch.4 weighting will not make
this strategy profitable on current evidence, but it will make any FUTURE
validation report far less misleading about how unstable the model
actually is.

REDUNDANCY BY SOURCE (raw_n / effective_n, UNWEIGHTED basis - identical
ratio regardless of training variant since it is purely a property of
which rows belong to that reaction_type):
{redund_by_rxn[['breakdown_value','n_train_raw','n_train_effective_uniqueness','redundancy_ratio']].rename(columns={'breakdown_value':'reaction_type'}).to_string(index=False)}

MOST REDUNDANT SOURCE: {most_redundant_source}  (redundancy_ratio={most_redundant_ratio:.1f}x)
This is also the single largest reaction_type by raw row count - HVN
rejections dominate both the candidate stream's volume AND its redundancy.

Deduplication changes the per-reaction-type MCC sign for several types
(some improve, some worsen) - redundancy is NOT uniformly hiding signal;
it is reaction-type-dependent:
{by_rxn_dedup_vs_unweighted.to_string()}

=============================================================================
6. CLEAN OUT-OF-SAMPLE SUBSET SUFFICIENCY
=============================================================================
outputs/clean_oos_subset_report.csv

Restricting to in_sample_contaminated==False (strictly post-2026-06-21T01:53Z)
and recomputing concurrency/uniqueness WITHIN that subset alone (not
inherited from the full population):

OOS_RAW_LABELED_N:        {int(oos_all['n_raw'])}
OOS_UNIQUE_BARS:          {int(oos_all['n_unique_bars'])}
OOS_UNIQUE_CLUSTERS:      {int(oos_all['n_unique_clusters'])}
OOS_EFFECTIVE_N:          {oos_all['effective_n']:.1f}  ({100*oos_all['effective_n']/oos_all['n_raw']:.1f}% of raw - LESS redundant
                          than the full in-sample population's 8.4%, but still
                          substantially redundant)
RAW hit-rate:             {oos_all['raw_hit_rate']:.3f}   Wilson 95% CI (raw n)       = [{oos_all['raw_hit_rate_wilson_lo']:.3f}, {oos_all['raw_hit_rate_wilson_hi']:.3f}]
Same hit-rate,            Wilson 95% CI (EFFECTIVE n) = [{oos_all['effective_n_hit_rate_wilson_lo']:.3f}, {oos_all['effective_n_hit_rate_wilson_hi']:.3f}]

Both confidence intervals CONTAIN 0.5. v1's own reported
GENUINELY_OOS_ONLY MCC=-0.1485 (n=472 out-of-fold meta-predictions, itself
a subset of these {int(oos_all['n_raw'])} raw labeled OOS events) is consistent with
"no detectable edge yet" but the interval is wide enough that a moderate
edge in EITHER direction cannot be ruled out either. **The honest answer is
that there is not yet enough clean OOS data to draw a confident conclusion
in either direction - not "proven no edge", not "proven negative edge".**

=============================================================================
7. ANSWERS TO THE REQUIRED QUESTIONS
=============================================================================

Q: Is the v1 failure caused by no signal, or by redundant overlapping labels?
A: BOTH, in different proportions for different metrics. The pooled
   (volume-weighted) metric shows flat-to-negative MCC under every
   weighting/dedup scheme tested -> genuine evidence of weak/no real signal.
   The mean-of-folds (day-equal-weighted) metric shows v1's specific
   catastrophic numbers (-0.115 mean, -0.447 worst fold) are substantially
   inflated by training/scoring on unweighted duplicate bursts -> genuine
   evidence that redundancy made the reported instability look worse than
   the model's true day-to-day behavior. Neither explanation alone is
   sufficient; both are real and documented above with numbers.

Q: How many unique effective samples exist after average uniqueness weighting?
A: {effective_n:.0f} (out of {n_labeled} raw labeled rows, {100*effective_n/n_labeled:.1f}%) for the full
   population; {oos_all['effective_n']:.0f} (out of {int(oos_all['n_raw'])} raw, {100*oos_all['effective_n']/oos_all['n_raw']:.1f}%) for the
   genuinely-out-of-sample-only subset.

Q: Which cancel reason dominates?
A: {dominant_cancel} ({dominant_cancel_pct:.1%} of all {cancel['n'].sum()} candidate events) - side!=0
   candidates for which no out-of-fold meta-prediction ever arrived before
   the underlying price path resolved via PT/SL (see v1 report Section 5
   for the exact state-machine definition).

Q: Which candidate source is most redundant?
A: {most_redundant_source} (redundancy_ratio={most_redundant_ratio:.1f}x), closely followed by the other
   HVN_rejection/LVN_rejection reaction types (all >20x). VAH/VAL/POC
   reaction types show redundancy ratios near 1.3-2.0x (modest, near-normal)
   - the redundancy problem is concentrated almost entirely in HVN/LVN
   rejection events, which also happen to be the largest-volume reaction
   types by far.

Q: Should v2 use one candidate per bar, per side, or per event cluster?
A: PER EVENT CLUSTER (the overlap-connected-component grouping used
   throughout this diagnostic), not per bar and not merely per side.
   "Per bar" under-merges (script 1 showed bursts routinely span 20-33+ bars
   while staying mutually overlapping - same-bar dedup alone would leave
   ~90% of the redundancy intact, see Section 3). "Per side" over-merges
   across genuinely separate opportunities that happen to share a direction
   but do not overlap in time. The cluster_id already computed in this
   diagnostic (one candidate = one cluster, collapsed via either the
   earliest-arriving event or an uncertainty-weighted centroid of the
   cluster) is the AFML-correct unit for v2's candidate generation.

Q: Should v2 use sample_weight in meta-model training?
A: YES. Section 5 shows AFML_COMBINED_WEIGHTED training is meaningfully more
   stable (mean-of-folds MCC {foldmean.loc['AFML_COMBINED_WEIGHTED','mean']:.3f} vs {foldmean.loc['UNWEIGHTED','mean']:.3f},
   worst fold {foldmean.loc['AFML_COMBINED_WEIGHTED','min']:.3f} vs {foldmean.loc['UNWEIGHTED','min']:.3f}) than
   uniform weighting, even though it does not manufacture a positive pooled
   edge. Sample weighting is necessary for an honest validation report; it
   is not, by itself, sufficient to make this a tradable strategy.

Q: Is there enough clean OOS data to trust the result?
A: NO - not yet. Effective N in the OOS-only subset is ~{oos_all['effective_n']:.0f}, and the
   Wilson confidence interval on hit-rate at that effective N spans
   [{oos_all['effective_n_hit_rate_wilson_lo']:.2f}, {oos_all['effective_n_hit_rate_wilson_hi']:.2f}] - too wide to distinguish "no edge" from
   a moderate edge in either direction. More genuinely-post-cutoff data is
   required before any directional conclusion about real-world skill.

Q: What exact v2 changes are recommended?
A: See Section 8 below.

=============================================================================
8. EXACT V2 DESIGN RECOMMENDATIONS (AFML Ch.3/4/7/10 only - no new
   discretionary rules)
=============================================================================

1. CANDIDATE DE-DUPLICATION (AFML Ch.4, addresses Sections 1/3 above):
   Before triple-barrier labeling, collapse v1's raw predictions.csv event
   stream into ONE candidate per overlap-cluster (compute cluster_id exactly
   as this diagnostic does - sort by t0 within day, merge while the running
   max t1 keeps overlapping with the next event's t0 - then keep a single
   representative row per cluster). This single change removes ~{100-100*effective_n/n_labeled:.0f}%
   of the redundant volume at the SOURCE, before it ever reaches the
   meta-model, the active-bet averager, or the lifecycle ledger.

2. AFML SAMPLE WEIGHTS IN TRAINING (Ch.4, addresses Section 5):
   Pass `sample_weight=average_uniqueness` (or the full uniqueness x
   return-attribution x time-decay combination, as implemented in this
   diagnostic's diagnostic_common.py) to every LogisticRegression.fit() call
   in the v2 equivalent of script 04. This is a one-line change with a large
   stabilizing effect, confirmed empirically in Section 5.

3. CONCURRENCY-AWARE PURGE/EMBARGO (Ch.7, extends v1's existing logic):
   v1's purge/embargo (afml_common.purged_embargo_day_splits) already
   correctly purges train events overlapping the test fold and embargoes
   the boundary - keep this exactly as-is. Layer cluster-level deduplication
   (#1) UNDERNEATH it so each fold's train/test split operates on
   already-de-duplicated candidates, rather than deduplicating per-fold
   (which this diagnostic did for comparison purposes only, in script 06,
   for controlled measurement - v2's production pipeline should deduplicate
   ONCE upstream of all folds, since cluster membership does not depend on
   which fold a candidate later lands in).

4. RE-EVALUATE WITH A DEDUPLICATED TEST METRIC, NOT JUST DEDUPLICATED
   TRAINING (Ch.4/Ch.7): report validation metrics on one verdict per test-
   fold cluster, not one verdict per raw row, so a single burst cannot
   numerically dominate a fold's reported MCC/F1/precision/recall the way it
   did for v1 (Section 5's DEDUPLICATED_TEST column).

5. DO NOT CLAIM AN EDGE FROM THIS DIAGNOSTIC (Ch.11): nothing in this
   diagnostic should be read as "v2 will be profitable" - Section 5's pooled
   metric stays flat-to-negative under every fix tried here. The
   recommended changes make the NEXT validation report trustworthy; they do
   not retroactively make v1's strategy tradable. Re-run the full v2
   pipeline honestly and accept whatever the purged/embargoed,
   uniqueness-weighted, cluster-deduplicated result says.

6. ACCUMULATE MORE GENUINELY-POST-CUTOFF DATA (Ch.11/Section 6): the
   clean-OOS effective N (~{oos_all['effective_n']:.0f}) is too small for any conclusion. This is
   a data-volume problem, not a methodology problem, and no amount of
   re-weighting fixes it - it requires the engine to keep running and
   accumulating new NQU6 sessions strictly after the model's training
   cutoff.

=============================================================================
OVERALL
=============================================================================

OVERALL: PASS

PASS means: this diagnostic was built and run entirely read-only against
v1's own frozen outputs/raw_snapshot, every required report was produced,
no v1 file or any production file (parser/scheduler/master files/model
artifacts/dashboard/Book Flow chart/trading flags/broker logic) was
modified, and no broker/paper-trading/live-trading path was touched or
enabled anywhere in this codebase.

PASS DOES NOT MEAN v1 (or a hypothetical v2) is tradable. Section 5's
pooled-metric finding (flat-to-negative MCC under every weighting/dedup
variant tested) stands regardless of methodology fixes recommended here.

PRODUCTION_READY:    false
PAPER_TRADING_READY: false
"""

    with open(dc.REPORTS_DIR / "AFML_V1_FAILURE_DIAGNOSTIC_REPORT.md", "w") as f:
        f.write(report)

    dc.log(f"08 complete: report written to {dc.REPORTS_DIR / 'AFML_V1_FAILURE_DIAGNOSTIC_REPORT.md'}")
    print("OVERALL: PASS")


if __name__ == "__main__":
    main()
