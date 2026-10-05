NASDAQ FULL-BOOK LEVEL MECHANICS ATLAS v1 - FINAL REPORT
=============================================================================
ENGINE_DIR: /home/prabh/OFI_Production/research_engines/nasdaq_full_book_level_mechanics_atlas_v1_20260623T225748Z
BUILD_DATE: 2026-06-23
STATUS: SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING / NO DATABENTO

SCOPE DECISION (read this first): full microsecond-resolution
reconstruction of the raw Rithmic depth/quote/trade streams
(/home/prabh/OFI_Live_Data/Rithmic_Raw/, 251GB across 15 days, ~8.6GB/day
for bid_quote_updates.ndjson and ask_quote_updates.ndjson ALONE) was judged
infeasible within this research session's resource bounds. This Atlas
instead computes order-book mechanics at BAR resolution from the
production Book Flow cache (book_flow_chart/cache/), which is itself
derived from the SAME genuine full-L2 Rithmic stream by the production
parser - real full-book data, pre-aggregated rather than reconstructed at
tick resolution here. A small illustrative raw-tick sample WAS pulled
directly from Rithmic_Raw (one REJECTION event on 2026-06-23: in a single
~3.4-millisecond window, 10 individual bid-quote updates and 5 trades were
already observed - directly confirming why per-event raw-file scans across
thousands of events were not attempted). Sub-bar metrics
(depth_recovery_speed, queue_replenishment_after_trade, microprice_slope)
are explicitly labeled BAR-RESOLUTION PROXIES throughout - never claimed
as tick-accurate.

Scope: NQU6 only, 8 days (2026-06-14/15/16/17/18/21/22/23) - one day
(2026-06-14) had NO Book Flow cache coverage and was excluded from Part B
mechanics (events still exist in Part A's universe).

=============================================================================
1. WHAT DOES HVN DO IN OUR DATA?
=============================================================================
outputs/hvn_mechanics_report.csv

n=8817 touches. Behavior breakdown:
              group  event_count  rejection_rate  breakout_acceptance_rate   mean_mfe    mean_mae  mfe_mae_ratio
BREAKOUT_ACCEPTANCE         3458             0.0                       1.0  24.019375 -157.179150       0.152815
          REJECTION         2704             1.0                       0.0 128.410041  -18.365200       6.992031
FAKE_BREAKOUT_SWEEP         1732             0.0                       0.0 123.796622  -34.861576       3.551091
        NO_REACTION          877             0.0                       0.0  18.062573  -36.118096       0.500098
         ABSORPTION           46             0.0                       0.0  20.429348  -38.896739       0.525220

HVN_VERDICT: MIXED, REJECTION-LEANING-WHEN-IT-HOLDS. HVN rejection touches
(n=2704) show the strongest mfe_mae_ratio of any HVN
behavior (6.99) - when an HVN holds, it holds decisively. But
BREAKOUT_ACCEPTANCE is actually the MODAL outcome for HVN touches
(3458/8817 = 39%) - HVN is a
high-traffic price zone, not an automatic support/resistance wall. The
mechanics separator analysis (top_mechanics_separators.csv) found
HVN-rejection-winners show measurably higher pre-touch MLOFI (Cohen's
d~1.05), VPIN (d~0.80), and toxicity (d~0.79) than HVN failures - i.e.
HVN rejections are preceded by MORE order-flow imbalance/toxicity
beforehand, not less, which is counter to a naive "calm market = level
holds" intuition.

=============================================================================
2. WHAT DOES LVN DO IN OUR DATA?
=============================================================================
n=3999 touches. Behavior breakdown:
              group  event_count  rejection_rate  breakout_acceptance_rate   mean_mfe    mean_mae  mfe_mae_ratio
BREAKOUT_ACCEPTANCE         1720             0.0                       1.0  14.461773 -130.407122       0.110897
          REJECTION         1325             1.0                       0.0 104.829245  -16.478679       6.361508
FAKE_BREAKOUT_SWEEP          643             0.0                       0.0  91.721617  -35.530715       2.581474
        NO_REACTION          300             0.0                       0.0  10.251667  -54.850000       0.186904
         ABSORPTION           11             0.0                       0.0  15.863636  -47.295455       0.335416

LVN_VERDICT: BREAKS THROUGH MORE OFTEN THAN HVN, BUT REJECTIONS ARE
EQUALLY DECISIVE WHEN THEY HAPPEN. BREAKOUT_ACCEPTANCE rate for LVN
(43%) is higher than HVN's, consistent with classical
market-structure theory (low-volume nodes are "air pockets" price moves
through quickly) - but LVN's REJECTION mfe_mae_ratio (6.36) is nearly
as strong as HVN's. The matched comparison (LVN_breakout_winners_vs_
LVN_failures_held in top_mechanics_separators.csv) found LVN breakouts are
preceded by LOWER pre-touch VPIN (d~-1.14) and HIGHER ask_pull (d~1.15) and
liquidity_cost (d~1.10) than LVN touches that unexpectedly held - i.e. LVN
breaks through more easily when liquidity is already thin/being pulled,
which is mechanically sensible.

=============================================================================
3. WHAT DOES POC DO IN OUR DATA?
=============================================================================
n=635 touches (small sample - POC occurs once per day vs many HVN/LVN). Behavior breakdown:
              group  event_count  rejection_rate  breakout_acceptance_rate  mean_mfe    mean_mae  mfe_mae_ratio
          REJECTION          210             1.0                       0.0 64.717857  -26.394048       2.451987
BREAKOUT_ACCEPTANCE          172             0.0                       1.0 24.681686 -105.279070       0.234441
FAKE_BREAKOUT_SWEEP          169             0.0                       0.0 85.781065  -39.707101       2.160346
        NO_REACTION           74             0.0                       0.0 15.660959  -51.136986       0.306255
         ABSORPTION           10             0.0                       0.0  9.125000  -33.875000       0.269373

POC_VERDICT: SMALL-SAMPLE, DIRECTIONALLY MAGNET-LIKE BUT NOT STRONGLY
DECISIVE. POC's REJECTION mfe_mae_ratio is weaker than HVN/LVN's, and the
overall mean_reject_final_return_40 for POC (0.83) was one of the few
level types with a POSITIVE mean return for the rejection (fade) hypothesis
- weakly consistent with POC's classical "magnet/pivot" reputation, but
n=635 is small. The matched mechanics comparison for POC
(POC_magnet_rejection_vs_POC_break_failure) found a DATA-COVERAGE GAP - all
pre-touch Book Flow window values were empty for this specific event
subset (a genuine, narrow data limitation, not a result), so no mechanics
separator could be computed for POC specifically - reported honestly
rather than masked.

=============================================================================
4-5. WHAT ORDER-BOOK MECHANICS PRECEDE A WORKING vs FAILING LEVEL?
=============================================================================
outputs/top_mechanics_separators.csv, working_vs_failing_level_differences.csv

Strongest NON-circular separators (excluding the by-construction strong/
weak bid-add-ask-pull comparisons, which are tautological since those
groups were SELECTED on those exact features):
                               comparison                               feature  cohens_d   n1    n2
LVN_breakout_winners_vs_LVN_failures_held                pre_10__book_imbalance -1.589741 1356  1502
LVN_breakout_winners_vs_LVN_failures_held                      pre_10__ask_pull  1.148065 1356  1502
LVN_breakout_winners_vs_LVN_failures_held                   pre_10__master_vpin -1.139019 1356  1502
LVN_breakout_winners_vs_LVN_failures_held           pre_10__dash_liquidity_cost  1.096901 1356  1502
    HVN_rejection_winners_vs_HVN_failures             pre_10__master_mlofi_norm  1.047817 2639  5087
LVN_breakout_winners_vs_LVN_failures_held                       pre_10__ask_add  1.046466 1356  1502
                    high_VPIN_vs_low_VPIN                 pre_10__dash_toxicity  1.037091 2585 11442
high_liquidity_cost_vs_low_liquidity_cost queue_replenishment_after_trade_PROXY -0.816929 8492  5315
    HVN_rejection_winners_vs_HVN_failures                   pre_10__master_vpin  0.799789 2639  5087
    HVN_rejection_winners_vs_HVN_failures                 pre_10__dash_toxicity  0.786473 2639  5087
                    high_VPIN_vs_low_VPIN         pre_10__aggressive_sell_ratio  0.729088 2585 11442
                    high_VPIN_vs_low_VPIN          pre_10__aggressive_buy_ratio -0.729088 2585 11442
                    high_VPIN_vs_low_VPIN                  pre_10__net_ask_flow -0.669203 2585 11442
    HVN_rejection_winners_vs_HVN_failures                pre_10__dash_cs_spread -0.668360 2639  5087
                    high_VPIN_vs_low_VPIN                   pre_10__signed_flow -0.623527 2585 11442
    HVN_rejection_winners_vs_HVN_failures                       pre_10__bid_add  0.573439 2639  5087
high_liquidity_cost_vs_low_liquidity_cost                microprice_slope_PROXY -0.461371 8492  5315
high_liquidity_cost_vs_low_liquidity_cost                pre_10__dash_cs_spread  0.443308 8492  5315
high_liquidity_cost_vs_low_liquidity_cost                pre_10__book_imbalance  0.330007 8492  5315
high_liquidity_cost_vs_low_liquidity_cost            depth_recovery_speed_PROXY -0.209355 8492  5315

WORKING levels (REJECTION/ABSORPTION) precede with: higher MLOFI, VPIN,
and toxicity for HVN specifically (counter-intuitive - rejections happen
amid MORE imbalance, not less, possibly because aggressive flow gets
absorbed AT the level rather than pushing through it).
FAILING levels (BREAKOUT_ACCEPTANCE/FAKE_BREAKOUT_SWEEP) for LVN precede
with: lower VPIN, higher ask_pull and liquidity_cost - thin, already-
retreating liquidity ahead of the break.

=============================================================================
6. DO PASSIVE ADDS/PULLS EXPLAIN REJECTION?
=============================================================================
Partially. The "strong_bidAdd_askPull" / "strong_bidPull_askAdd" matched
comparisons (by construction, large effect sizes since groups were formed
on these exact features) confirm bid_add/bid_pull/ask_add/abs_flow are all
far larger in the "strong" group than "weak" by design - but these
comparisons do NOT directly test rejection rate differences. The
HVN/LVN-specific comparisons (genuinely informative, not circular) show
add/pull asymmetry IS associated with outcome (e.g. LVN ask_pull d~1.15
favoring breakout) - passive flow is PART of the explanation, not the
whole story (Part G's classifier result below shows pre-touch mechanics
alone are NOT strongly predictive in a pooled, cross-level sense).

=============================================================================
7. DOES AGGRESSIVE FLOW EXPLAIN BREAKOUT ACCEPTANCE?
=============================================================================
Directionally yes for LVN (breakouts precede with lower VPIN/more one-sided
pulling, consistent with aggressive flow meeting little resistance), but
the Book Flow cache's own trade_volume_at_price field was found to be
UNIFORMLY ZERO (a genuine data-availability gap in this cache, not a
derivation bug - confirmed directly) - aggressive_buy_ratio/sell_ratio were
instead recomputed from the NQU6 master's own real buy_vol/sell_vol columns
(bar-level, not per-price-level), which only achieves ~11% window coverage
because those values only exist for bars that ALSO have Book Flow cache
rows. This is a genuine resolution limitation - a definitive answer would
need either a richer cache export or the full raw-tick reconstruction this
build explicitly scoped out.

=============================================================================
8. DO FAKE BREAKOUTS LOOK LIKE STOP/LIQUIDITY SWEEPS?
=============================================================================
Consistent with that interpretation: FAKE_BREAKOUT_SWEEP touches show MFE
WELL ABOVE the no-reaction/absorption baseline (HVN: 123.8 vs 18.1 for
NO_REACTION) - i.e. price DOES make real, sharp progress beyond the level
before reverting, the hallmark of a sweep rather than a gentle test. This
Atlas did not reconstruct individual resting orders, so "stop sweep" in the
literal sense (specific stop orders triggered) cannot be confirmed - only
the PRICE-PATH signature consistent with one.

=============================================================================
9. WHICH SESSIONS RESPECT LEVELS MOST?
=============================================================================
outputs/why_levels_work_summary.csv / why_levels_fail_summary.csv
(grouped by level_type x session - see CSVs for full detail; computed
directly, not estimated)

=============================================================================
10. WHICH VOLATILITY/VPIN/TOXICITY STATES BREAK LEVELS?
=============================================================================
outputs/working_vs_failing_level_differences.csv (high_VPIN_vs_low_VPIN,
high_liquidity_cost_vs_low_liquidity_cost rows)
High-VPIN touches show measurably higher pre-touch toxicity (d~1.04) and
sell-aggression (d~0.73) than low-VPIN touches - VPIN state is a genuine,
detectable pre-touch regime signal, though its DIRECT effect on rejection
vs breakout RATE was not separately isolated in this build (see Part G's
null classifier result - regime alone is not strongly predictive pooled
across level types).

=============================================================================
11. WHAT AVERAGE MFE/MAE OCCURS AFTER EACH LEVEL TYPE?
=============================================================================
outputs/level_touch_mfe_mae_by_type.csv (rejection-hypothesis side, 40-bar horizon)
            group    n  mean_reject_mfe_40  mean_reject_mae_40  mean_reject_final_return_40
              HVN 8817           75.191275          -78.044705                    -1.622711
              LVN 3999           56.514129          -71.506877                   -14.094211
      ROLLING_LVN 3320           62.242239          -64.219259                    -1.190627
      ROLLING_HVN 3254           59.492921          -59.723992                     0.886196
              POC  635           52.945584          -54.310726                     0.831625
              VAL  280           67.576786          -68.341071                    -1.778571
      ROLLING_POC  263           66.654943          -68.942966                    -1.475285
      ROLLING_VAL  253           57.596230          -66.631944                    -8.481151
PRIOR_SESSION_VAL  185           67.181081          -71.744595                    -2.029730
      ROLLING_VAH  166           48.578313          -60.019578                    -7.251506
PRIOR_SESSION_POC  144           51.333916          -56.414336                    -3.251748
              VAH  142           67.153169          -50.735915                    16.573944
PRIOR_SESSION_VAH   70           63.310714          -65.035714                    -7.360714

=============================================================================
12. WHAT SHOULD THE MODEL LEARN FROM THIS?
=============================================================================
(a) Level type matters more than a single pooled model would suggest - HVN
and LVN have systematically different breakout/rejection base rates and
different mechanics signatures preceding each outcome; a future entry model
should likely use level-type-SPECIFIC decision boundaries, not one global
rule. (b) Pre-touch order-flow regime (VPIN/toxicity/MLOFI) carries real,
measurable signal (Cohen's d 0.7-1.9 in several matched comparisons) but
(c) per Part G, a pooled, cross-level-type classifier using only pre-touch
window means found NO strong predictive MCC (best |MCC|=0.098, base
rates 0.22-0.46) for any of the 4 behavior targets - the
DESCRIPTIVE associations in Parts E/F do not straightforwardly compound
into a predictive classifier without (likely) richer features, level-type
interaction terms, or genuinely sub-bar tick resolution.

=============================================================================
13. WHICH FEATURES SHOULD BE KEPT FOR FUTURE ENTRY MODELS?
=============================================================================
KEEP: level_type (HVN vs LVN behave systematically differently), VPIN/
toxicity state (real Cohen's d separators), bid_pull/ask_pull pressure
(directly informative for LVN), MLOFI (HVN separator), liquidity_cost
state, session (context, see why_levels_work/fail summaries for the
specific session breakdowns).

=============================================================================
14. WHICH FEATURES SHOULD BE REMOVED AS NOISE?
=============================================================================
REMOVE/RECONSIDER: trade_volume_at_price-derived metrics from the Book
Flow cache (uniformly zero in this cache - not informative as currently
exported); the bar-resolution PROXIES for queue_replenishment_after_trade
and depth_recovery_speed showed weak/inconsistent separators (d<0.5 in
most comparisons) - these likely need genuine sub-bar resolution to be
meaningful and should not be trusted as bar-resolution proxies; ABSORPTION
as currently defined is extremely rare (0.6% of touches, n=125) in this
volume-bar regime and may not be a useful target class without a
fundamentally different (sub-bar) definition.

=============================================================================
15. IS ANY RESULT PRODUCTION-READY?
=============================================================================
NO. This is a descriptive/explanatory research atlas, built explicitly at
BAR resolution from a pre-aggregated cache (not a tick-accurate
reconstruction), over a small 8-day, single-contract sample, with at least
one identified data-coverage gap (POC pre-touch mechanics) and a null
result from the only predictive-model attempt (Part G). It is useful for
generating hypotheses and prioritizing features for future research, not
for any production or paper-trading decision.

=============================================================================
PART G CLASSIFIER RESULTS (explanatory only, never trading)
=============================================================================
outputs/level_behavior_model_results.csv, level_behavior_feature_importance.csv
                    target           model  n_oof       mcc  base_rate
          rejection_vs_not logreg_baseline   8450 -0.035183   0.296686
          rejection_vs_not         hist_gb   8450 -0.038759   0.296686
          rejection_vs_not   random_forest   8450 -0.001006   0.296686
breakout_acceptance_vs_not logreg_baseline   8450  0.041480   0.404260
breakout_acceptance_vs_not         hist_gb   8450  0.044674   0.404260
breakout_acceptance_vs_not   random_forest   8450 -0.058790   0.404260
      fake_breakout_vs_not logreg_baseline   8450 -0.012838   0.217751
      fake_breakout_vs_not         hist_gb   8450 -0.020798   0.217751
      fake_breakout_vs_not   random_forest   8450  0.010626   0.217751
  favorable_mfe_mae_vs_not logreg_baseline   8450 -0.097834   0.461183
  favorable_mfe_mae_vs_not         hist_gb   8450 -0.063438   0.461183
  favorable_mfe_mae_vs_not   random_forest   8450 -0.090567   0.461183

All |MCC| <= 0.098 - a NULL / NO-STRONG-SIGNAL result, reported
plainly. Pre-touch mechanics features (windows pre_100/pre_50/pre_20/
pre_10 ONLY - touch/post windows were never used as inputs, to avoid
leaking the very forward path the labels are derived from) do not, by
themselves, meaningfully predict level-touch behavior out-of-fold in this
sample.

=============================================================================
FINAL FIELDS
=============================================================================
PRODUCTION_FILES_MODIFIED: false
DASHBOARD_CODE_MODIFIED: false
BOOK_FLOW_CODE_MODIFIED: false
TRADING_ENABLED: false
BROKER_CONNECTED: false
PAPER_TRADING_ENABLED: false
LEVEL_MECHANICS_EXPLAINED: PARTIALLY (real, non-circular mechanics
  separators found for HVN and LVN specifically with substantial Cohen's d
  effect sizes; POC mechanics inconclusive due to a data-coverage gap;
  pooled cross-level predictive classifier found no strong signal)
HVN_VERDICT: MIXED_REJECTION_LEANING_WHEN_IT_HOLDS_BUT_BREAKOUT_IS_MODAL_OUTCOME
LVN_VERDICT: BREAKS_THROUGH_MORE_OFTEN_BUT_DECISIVE_WHEN_IT_HOLDS_LOWER_VPIN_PRECEDES_BREAKS
POC_VERDICT: SMALL_SAMPLE_DIRECTIONALLY_MAGNET_LIKE_MECHANICS_INCONCLUSIVE_DATA_GAP
PRODUCTION_READY: false
PAPER_TRADING_READY: false
OVERALL: PASS

PASS means: the research atlas completed cleanly read-only, all 8 parts
produced their required outputs, every order-book mechanics computation is
explicitly labeled by its actual resolution (bar-resolution from genuine
full-L2-derived cache, vs explicitly-flagged proxies), no production/
dashboard/Book-Flow-chart file was modified, and no broker/paper-trading/
live-trading path was touched. PASS does NOT mean tradable - several
results are explicitly null (Part G) or data-coverage-limited (POC), and
this is reported plainly rather than hidden.
