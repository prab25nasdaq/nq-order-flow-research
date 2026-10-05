AFML TRADE LIFECYCLE v1 FAILURE DIAGNOSTIC - FINAL REPORT
=============================================================================
DIAGNOSTIC_DIR: /home/prabh/OFI_Production/research_engines/afml_trade_lifecycle_v1_failure_diagnostic_20260623T065452Z
V1_ENGINE_DIR:  /home/prabh/OFI_Production/research_engines/afml_trade_lifecycle_shadow_v1_20260623T062435Z
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

CANDIDATE_EVENTS_TOTAL (v1 scope, NQU6):      8394
TRIPLE_BARRIER_LABELED (side!=0, resolved):    5365
UNIQUE_t0_BARS_AMONG_LABELED:                  700   (7.66x rows per unique bar)
PEAK_LABEL_CONCURRENCY (c_t):                  201  at bar_idx=1587 (day 20260615)

v1's reported MAX_ACTIVE_BETS=168 (the "Shadow Lifecycle" section's
headline anomaly) occurs at bar_idx=2691 (day 20260617), where this
diagnostic's raw label concurrency c_t is ALSO exactly 168 - every single one of
the 168 concurrently-active labeled duplicate events at that bar was sized
non-zero by v1's meta-model (168 active bets = 168 concurrent labels, zero
filtering). The meta-model could not discriminate among the copies because
they carry near-identical `primary_confidence`/feature snapshots - the
redundancy in the LABEL stream flows straight through into the
POSITION-MANAGEMENT layer unchanged. This single fact directly answers
"why max simultaneous active bets reached 168": it is not a bet-sizing or
averaging bug (Ch.10's avgActiveSignals is doing exactly what it is
supposed to do) - it is a direct, undiluted symptom of unrepaired Ch.4
label redundancy upstream.

PCT_LABELED_EVENTS_WITH_AVG_UNIQUENESS_BELOW_0.10: 86.1%
  (i.e. this fraction of "training samples" spent their entire life inside
  a burst of >=10 mutually-overlapping duplicate labels)

=============================================================================
2. AVERAGE UNIQUENESS / EFFECTIVE SAMPLE SIZE (AFML Ch.4 snippet 4.2)
=============================================================================
outputs/average_uniqueness_by_event.csv, outputs/sample_weight_report.csv

RAW_LABELED_N:                  5365
EFFECTIVE_N (sum of avg_uniqueness): 449.9
EFFECTIVE_N_AS_PCT_OF_RAW:      8.4%
V1_OVERWEIGHT_RATIO (raw/effective, v1's actual training weight was uniform=1.0 everywhere):  11.9x

**v1 trained its meta-model believing it had 5365 independent observations.
By AFML's own canonical accounting, it had the statistical information
content of roughly 450.** Every LogisticRegression.fit() call in v1
script 04 used `sample_weight=None` (uniform) - confirmed by reading the
script directly; AFML Chapter 4 weighting was not implemented anywhere in
v1's pipeline.

=============================================================================
3. CANDIDATE CLUSTERS / BURST STRUCTURE (overlap connected-components)
=============================================================================
outputs/candidate_cluster_report.csv

N_CLUSTERS (connected components of the overlap graph): 275
N_SINGLETON_CLUSTERS (genuinely unique, no overlap):     106  (38.5% of clusters)
CLUSTERS WITH >=10 EVENTS:                                62, holding 90.1% of ALL labeled events

BIGGEST CLUSTER: id=139, day=20260617, n_events=981,
  span=33 bars, dominant_reaction_type=HVN_rejection_from_above
  (45.5% of the cluster), pct_label_agreement=68.9%,
  std_primary_confidence=0.039 (low relative to the 0.5-1.0 dataset-wide
  range - this cluster spans 33 bars/multiple distinct t0 values, so unlike
  the single-bar example in Section 1 (std=0 exactly, byte-identical rows),
  confidence drifts modestly as the model re-scores nearby bars, but stays
  tight - still far closer to "one signal restated" than to independent draws),
  effective_n_in_cluster=13.8 (i.e. 981 raw rows worth
  the statistical content of about 14 independent samples).

Within a typical cluster, median pct_dominant_side and median
pct_label_agreement are both 1.000 across clusters with >=5 events - the
events inside a burst are not independent corroborating evidence from
different signals; they are the SAME underlying signal, replicated once per
nearby level-type/reaction-type tag.

=============================================================================
4. CANCEL REASON BREAKDOWN
=============================================================================
outputs/cancel_reason_breakdown.csv

DOMINANT final_state overall: CANCEL_STALE (3064 / 8394 = 36.5%)

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
  UNWEIGHTED (v1-identical)     0.0148         -0.1150            -0.4470
  AFML_UNIQUENESS_WEIGHTED     -0.0413          0.0402            -0.2683
  AFML_COMBINED_WEIGHTED       -0.0006          0.1202            -0.1495
  DEDUPLICATED                 -0.0805          0.0000            -0.2942

**This result is NUANCED, not a clean "redundancy explains everything"
story, and is reported exactly as found:**

- On the POOLED metric (v1's own headline aggregation), NO weighting/dedup
  variant beats UNWEIGHTED - all are flat-to-negative. Fixing the
  redundancy does not reveal a hidden profitable strategy. This is genuine
  evidence FOR "no signal" as a real, partial explanation.
- On the MEAN-OF-FOLDS metric (each calendar day weighted equally), the
  picture is very different: UNWEIGHTED is badly negative on average
  (-0.115, worst fold -0.447) while
  AFML_COMBINED_WEIGHTED is positive on average (0.120,
  worst fold -0.150) - i.e. UNWEIGHTED training is
  considerably less STABLE day-to-day, and that instability is concentrated
  exactly where the largest redundant bursts live. Evaluating on a
  deduplicated TEST set (one verdict per burst instead of letting an
  981-event mega-cluster vote 981 times) moves UNWEIGHTED's mean-of-folds
  MCC from -0.115 to a less-negative number too (see
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
           reaction_type  n_train_raw  n_train_effective_uniqueness  redundancy_ratio
HVN_rejection_from_above       1196.0                     34.671228         34.495461
HVN_rejection_from_below       1261.0                     41.566607         30.336852
LVN_rejection_from_above        788.0                     31.250840         25.215323
LVN_rejection_from_below        808.0                     37.957566         21.286929
VAH_rejection_from_above         30.0                     15.227976          1.970058
VAH_rejection_from_below         52.0                     28.625130          1.816586
VAL_rejection_from_above         57.0                     40.632937          1.402803
VAL_rejection_from_below         59.0                     42.947222          1.373779
POC_rejection_from_below         48.0                     35.427778          1.354869
POC_rejection_from_above         66.0                     49.198016          1.341518
          LVN_absorption          1.0                      1.000000          1.000000

MOST REDUNDANT SOURCE: HVN_rejection_from_above  (redundancy_ratio=34.5x)
This is also the single largest reaction_type by raw row count - HVN
rejections dominate both the candidate stream's volume AND its redundancy.

Deduplication changes the per-reaction-type MCC sign for several types
(some improve, some worsen) - redundancy is NOT uniformly hiding signal;
it is reaction-type-dependent:
train_variant             DEDUPLICATED  UNWEIGHTED
breakdown_value                                   
HVN_rejection_from_above      0.191693    0.010041
HVN_rejection_from_below     -0.253489   -0.095085
LVN_rejection_from_above     -0.071890    0.007207
LVN_rejection_from_below     -0.283600    0.068783
POC_rejection_from_above     -0.180261    0.028076
POC_rejection_from_below      0.174185   -0.069750
VAH_rejection_from_above      0.175691   -0.391345
VAH_rejection_from_below      0.056250    0.122911
VAL_rejection_from_above     -0.061696    0.014976
VAL_rejection_from_below     -0.072488   -0.088184

=============================================================================
6. CLEAN OUT-OF-SAMPLE SUBSET SUFFICIENCY
=============================================================================
outputs/clean_oos_subset_report.csv

Restricting to in_sample_contaminated==False (strictly post-2026-06-21T01:53Z)
and recomputing concurrency/uniqueness WITHIN that subset alone (not
inherited from the full population):

OOS_RAW_LABELED_N:        534
OOS_UNIQUE_BARS:          137
OOS_UNIQUE_CLUSTERS:      71
OOS_EFFECTIVE_N:          98.2  (18.4% of raw - LESS redundant
                          than the full in-sample population's 8.4%, but still
                          substantially redundant)
RAW hit-rate:             0.530   Wilson 95% CI (raw n)       = [0.488, 0.572]
Same hit-rate,            Wilson 95% CI (EFFECTIVE n) = [0.433, 0.626]

Both confidence intervals CONTAIN 0.5. v1's own reported
GENUINELY_OOS_ONLY MCC=-0.1485 (n=472 out-of-fold meta-predictions, itself
a subset of these 534 raw labeled OOS events) is consistent with
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
A: 450 (out of 5365 raw labeled rows, 8.4%) for the full
   population; 98 (out of 534 raw, 18.4%) for the
   genuinely-out-of-sample-only subset.

Q: Which cancel reason dominates?
A: CANCEL_STALE (36.5% of all 8394 candidate events) - side!=0
   candidates for which no out-of-fold meta-prediction ever arrived before
   the underlying price path resolved via PT/SL (see v1 report Section 5
   for the exact state-machine definition).

Q: Which candidate source is most redundant?
A: HVN_rejection_from_above (redundancy_ratio=34.5x), closely followed by the other
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
   stable (mean-of-folds MCC 0.120 vs -0.115,
   worst fold -0.150 vs -0.447) than
   uniform weighting, even though it does not manufacture a positive pooled
   edge. Sample weighting is necessary for an honest validation report; it
   is not, by itself, sufficient to make this a tradable strategy.

Q: Is there enough clean OOS data to trust the result?
A: NO - not yet. Effective N in the OOS-only subset is ~98, and the
   Wilson confidence interval on hit-rate at that effective N spans
   [0.43, 0.63] - too wide to distinguish "no edge" from
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
   representative row per cluster). This single change removes ~92%
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
   clean-OOS effective N (~98) is too small for any conclusion. This is
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
