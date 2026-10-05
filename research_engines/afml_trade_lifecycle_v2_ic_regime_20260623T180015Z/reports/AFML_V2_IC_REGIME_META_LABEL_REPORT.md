AFML V2 IC-REGIME META-LABEL MODEL - FINAL REPORT
=============================================================================
ENGINE_DIR: /home/prabh/OFI_Production/research_engines/afml_trade_lifecycle_v2_ic_regime_20260623T180015Z
V1_ENGINE:  /home/prabh/OFI_Production/research_engines/afml_trade_lifecycle_shadow_v1_20260623T062435Z
DIAGNOSTIC: /home/prabh/OFI_Production/research_engines/afml_trade_lifecycle_v1_failure_diagnostic_20260623T065452Z
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

ROLLING_IC_PANEL_ROWS: 219114
window_n=200 (a priori, not tuned), horizons=[1, 3, 5, 10, 20, 40]

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
Mean rolling IC by feature x session, H=40 (the meta-model's primary
regime horizon, matching AFML's vertical_barrier_bars=40 - chosen a priori,
not selected by comparing horizons' results):

breakdown_value          Asia_Overnight  London      US
feature                                                
ask_pull_minus_bid_pull         -0.1084 -0.0421  0.0261
bid_pull_pressure                0.1164  0.0437 -0.0218
buy_frac                        -0.0586 -0.0469 -0.0824
delta_norm                      -0.0586 -0.0469 -0.0824
sweep_imbal                     -0.0583 -0.0522 -0.0955
volatility_5                    -0.0578  0.0045  0.1117
vpin                            -0.0561  0.0458 -0.0130

FEATURES THAT CHANGE SIGN BY SESSION: ['ask_pull_minus_bid_pull', 'bid_pull_pressure', 'volatility_5', 'vpin']
  Most notably, the validated book-flow pull-pressure pair REVERSES between
  Asia/Overnight and US session: bid_pull_pressure is +0.116 IC
  overnight but -0.022 in US hours; ask_pull_minus_bid_pull mirrors it
  (-0.108 overnight, 0.026 in US). A static (non-regime-aware)
  feature cannot represent this; the rolling-IC-sign and value x rolling_ic
  interaction terms can.

FEATURES WITH STABLE SIGN ACROSS SESSIONS: ['buy_frac', 'delta_norm', 'sweep_imbal']
  delta_norm/buy_frac/sweep_imbal stay negative in every session (consistent
  direction, but see below - also the weakest typical magnitude).

STRONGEST TYPICAL |IC| (most worth trusting when rolling_ic_abs_strength is
high): vpin (mean|IC|=0.211,
significant [|t|>=2] 61% of the time) - note this is
also one of the SIGN-CHANGING features: vpin's unconditional/static IC looks
weak (mean_ic~-0.01) purely because its strong, frequently-significant
regime-conditional IC keeps flipping sign and washing out in the average -
exactly the case the rolling-IC-regime design is meant to capture.

WEAKEST TYPICAL |IC| (best candidate to down-weight via the value x
rolling_ic interaction when rolling_ic_abs_strength is low): buy_frac
(mean|IC|=0.099, significant only
24% of the time) - and by the perfect-collinearity
note above, this verdict applies identically to delta_norm
(same mean|IC|, same significance rate, by construction).

=============================================================================
3. AFML CH.4 FIXES: DEDUPLICATION + AVERAGE UNIQUENESS
=============================================================================
outputs/_dedup_uniqueness_events.parquet (intermediate), feeding
outputs/meta_label_dataset_v2_ic_regime.parquet

RAW_LABELED_N (v1, unchanged):           5365
LAYER 1 same-bar dedup:                   5365 -> 700  (7.66x)
LAYER 2 average uniqueness (on dedup'd):  700 -> 450.5 effective  (1.55x further)
TOTAL REDUCTION:                          5365 -> 450.5 effective (11.91x)

Layer 2 was asserted non-vacuous at build time (would have halted the
pipeline otherwise) - same-bar dedup alone does not remove cross-bar
overlap from a multi-bar burst, leaving genuine residual work for the
continuous uniqueness weight. This 11.9x total figure independently
reproduces the prior failure diagnostic's 5365/450=11.92x finding (computed there via a
different, single-layer method) to within rounding - a strong correctness
cross-check between the two engines.

=============================================================================
4. FOLD COMPARISON (outputs/fold_results_v2_ic_regime.csv)
=============================================================================
Identical purge/embargo (day-based, AFML Ch.7) and LogisticRegression
(C=1.0, max_iter=1000) code path for all three variants - only the row
population, sample weights, and feature columns differ.

MEAN-OF-FOLDS (each calendar day weighted equally):
                n_folds  n_test_total  mcc_mean  mcc_worst  pct_positive_folds
variant                                                                       
V1_BASELINE           6          2270   -0.1150    -0.4470              0.3333
V2A_DEDUP_UNIQ        6           418    0.0231    -0.1223              0.5000
V2B_IC_REGIME         6           418    0.0011    -0.3055              0.5000

POOLED (single confusion matrix across all out-of-fold rows):
                pooled_n  pooled_mcc  pooled_f1  pooled_balanced_accuracy
variant                                                                  
V1_BASELINE         2270      0.0148     0.5053                    0.5075
V2A_DEDUP_UNIQ       418     -0.0342     0.5023                    0.4829
V2B_IC_REGIME        418     -0.0029     0.5070                    0.4986

GENUINELY-OUT-OF-SAMPLE-ONLY (in_sample_contaminated==False) subset, same
folds, restricted test rows:
                n_test_oos_total  mcc_oos_mean  mcc_oos_worst
variant                                                      
V1_BASELINE                  472       -0.2718        -0.4470
V2A_DEDUP_UNIQ               134        0.0503        -0.1223
V2B_IC_REGIME                134       -0.0205        -0.3055

**This is reported exactly as found, including the parts that do not flatter
the new features:**

- WORST-FOLD MCC improves sharply under V2A (dedup+uniqueness alone):
  -0.4470 -> -0.1223. Adding IC-regime features (V2B) gives back
  some of that improvement: -0.1223 -> -0.3055 (still far better than v1, but
  worse than V2A alone) - consistent with overfitting risk from V2B's much
  higher feature count (91 vs 28) relative to the tiny effective training
  population in some folds (as low as ~70-90 effective rows, see
  n_train_effective in fold_results_v2_ic_regime.csv).
- MEAN-OF-FOLDS MCC: V1=-0.1150 (badly negative) -> V2A=0.0231 (modestly
  positive) -> V2B=0.0011 (back near zero). Dedup+uniqueness is the
  dominant source of improvement; the IC-regime features do not add to it
  on this mean-of-folds view and may dilute it slightly.
- POOLED MCC tells a DIFFERENT story and this divergence is itself
  important: V1's pooled MCC (0.0148) looks better than V2A's
  (-0.0342) or V2B's (-0.0029). This is because v1's pooled metric
  was being PADDED by thousands of self-correlated duplicate rows from a
  handful of mega-bursts that happen to agree with their own (duplicated)
  label often enough to nudge the aggregate confusion matrix slightly
  positive - exactly the artifact the diagnostic warned about. Once
  deduplicated, the pooled metric is computed on far fewer but genuinely
  distinct observations, and is the MORE TRUSTWORTHY number even though it
  looks slightly worse. Do not read v1's higher pooled MCC as "v1 was
  better" - it was measuring something closer to "v1 had more correlated
  copies of a few outcomes", not more skill.
- OOS-ONLY MEAN-OF-FOLDS MCC: V1=-0.2718 -> V2A=0.0503 (now positive) ->
  V2B=-0.0205 (back near zero). Encouraging direction for V2A, but n_test_oos_total
  for V2A/V2B is only 134 rows (vs v1's 472 raw, itself already flagged
  as too small in the prior diagnostic) - see Section 5, this is NOT
  statistically reliable evidence of a real edge yet.

=============================================================================
5. ANSWERS TO THE REQUIRED QUESTIONS
=============================================================================

Q: Does rolling IC regime information improve meta-label stability?
A: PARTIALLY, and the larger driver is the Ch.4 dedup/uniqueness fix, not
   the IC-regime features by themselves. V2A (dedup+uniqueness, NO new
   features) already fixes most of v1's worst-fold catastrophe (-0.447
   -> -0.122) and flips the mean-of-folds MCC from negative to positive
   (-0.115 -> 0.023). Layering the IC-regime features on top (V2B) does
   NOT further improve stability on this sample - worst-fold partially
   regresses (-0.122 -> -0.306) and mean-of-folds drifts back toward zero
   (0.023 -> 0.001), most plausibly because 91 features against an
   effective training population in the low hundreds invites overfitting.
   The IC-regime mechanism is sound (Section 1's verification) and the
   reliability columns are informative descriptively (Section 2), but
   feeding all of them into the meta-model at once, on this little data,
   does not yet pay for its own complexity.

Q: Which features change sign by session?
A: ['ask_pull_minus_bid_pull', 'bid_pull_pressure', 'volatility_5', 'vpin'] (all at H=40). bid_pull_pressure and
   ask_pull_minus_bid_pull most dramatically (sign fully reverses between
   Asia/Overnight and US). ['buy_frac', 'delta_norm', 'sweep_imbal'] keep a
   consistent sign across all three sessions.

Q: Which features should be ignored when rolling IC is weak?
A: buy_frac (and, by the exact collinearity noted in Section 1, delta_norm
   identically) has the lowest typical |IC| (0.099) and the
   lowest significance rate (24% of bars with |t-stat|>=2) of the 7
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
A: YES for V2A (-0.4470 -> -0.1223, the single largest improvement in this
   report). PARTIALLY for V2B (-0.4470 -> -0.3055 - better than v1, worse than V2A).

Q: Does v2 improve genuinely-OOS subset performance?
A: DIRECTIONALLY YES for V2A (mean-of-folds OOS MCC -0.2718 -> 0.0503, pooled
   OOS MCC -0.1485 -> -0.0110), MORE WEAKLY for V2B (-0.2718 -> -0.0205 /
   -0.1485 -> -0.0251). But see the next answer - the OOS sample is too
   small to call this a demonstrated improvement rather than noise.

Q: Are results still blocked due to small OOS sample?
A: YES, more firmly than before. The deduplicated, genuinely-OOS effective N
   is only 98 (from 137 same-bar-deduplicated raw OOS rows) - this
   independently reproduces the prior failure diagnostic's effective-N=98.16
   finding (computed there via a different method on the non-deduplicated
   population) almost exactly, which is reassuring for correctness but does
   not change the verdict: 98 effective observations is far too few to
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
   grows well past the current ~98 (no specific target number is
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
BLOCKED_REASON:      genuinely-out-of-sample effective N (98) remains too small
                      for a confident conclusion in either direction.
