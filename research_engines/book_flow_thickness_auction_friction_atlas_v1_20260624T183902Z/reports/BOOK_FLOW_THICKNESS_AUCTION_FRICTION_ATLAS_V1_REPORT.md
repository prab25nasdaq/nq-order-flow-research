# Book-Flow Thickness / Auction Friction Atlas v1 — Final Report
STATUS: SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING
SCALE_USED: percentile (per session-day context, matching book_flow_chart_v3.py's
own `scale_mode=="percentile"` branch: `denom = np.nanpercentile(abs_flow, 95)`)

Scope: NQU6, top10-depth Book Flow level-candle cache, the 7 days with
level-candle coverage (2026-06-15 .. 2026-06-23 — 2026-06-14 has no
level-candle parquet in cache, only a raw `.pkl` state file; this Atlas is
scoped accordingly and the gap is not silently dropped). 670,331
(bar × price_level) cells, 6,820 closed bars, 21,528 level-touch events
(reused read-only from the prior Level Mechanics Atlas), 3,426 v4 historical
candidates.

---

## 1. What does a thin book-flow bar mean in our data?

A (bar, price_level) cell whose `abs_flow_at_price` falls in the bottom
tercile (≤33rd percentile) of all active cells within that **session-day**
context — i.e. relatively little resting bid/ask add+pull activity occurred
at that exact price during that bar, compared to everywhere else price
visited that same day. ~26% of bars' own closing price level (1,781/6,820)
sit in a thin zone.

## 2. What does a fat book-flow bar mean in our data?

The mirror case: `abs_flow_at_price` in the top tercile (≥67th percentile)
within the same session-day context — heavy resting-order add/pull
activity at that exact price. ~39% of bars' own closing price level
(2,685/6,820) sit in a fat zone (the remaining ~33% are medium).

## 3. Does thinness correspond to faster price travel?

**Yes, directionally confirmed, with a real (not leakage-driven) effect
size.** Thin-zone bars: mean `price_velocity` = 0.000976; fat-zone bars:
0.000336 — thin bars move ~3x faster per unit of flow. A purged/embargoed
walk-forward classifier (`high_velocity_move`, features restricted to the
percentile-ranked thickness fields only — raw flow/bid/ask magnitudes and
all 6 friction scores were explicitly EXCLUDED from this one target because
they are mathematically entangled with `price_velocity`'s own denominator,
see `07_explanatory_model.py` docstring) scored **MCC 0.37–0.43** across all
three models (logreg/hist_gb/random_forest), with `thickness_above_level`
and `thickness_below_level` the two dominant features by a wide margin.
This is a genuine, moderate, walk-forward-validated effect.

## 4. Does fatness correspond to slower price movement / more exchange?

Yes on velocity (see Q3, mirrored). On "more two-way exchange" specifically:
`two_way_exchange_score` (fat thickness × balanced bid/ask × low net
directional progress) has mean 0.304 with wide spread (std 0.263),
confirming fat zones DO show more balanced, two-sided activity on average,
though the effect is not uniform - many fat-zone bars are still
directionally one-sided (high `imbalance_score` magnitude).

## 5. Are fat zones more likely to become HVN/POC support/resistance?

**Yes.** Joining the thickness panel onto the prior Atlas's 21,528 level
touches (`hvn_lvn_poc_thickness_summary.csv`): HVN touches are fat 44.9% of
the time vs. only 16.7% thin; POC touches are fat 40.1% vs. 16.5% thin.
This is the expected, theory-consistent direction.

## 6. Are thin zones more likely to behave like LVN / fast-travel zones?

**Directionally yes, but the effect is a relative shift, not a clean
separation.** LVN touches are thin 24.9% of the time vs. HVN's 16.7% — LVN
IS relatively thinner than HVN, but LVN is still fat 32.3% of the time (not
dominantly thin). LVN-as-defined-by-the-existing-volume-profile-methodology
and "thin" as defined here (percentile of order-flow *activity*, not traded
*volume*) are related but not identical constructs — they correlate, they
are not interchangeable.

## 7. Does thickness explain why a support line works first then fails later?

**No clean evidence found; if anything, mildly contradicted.** Testing H3
(`05_sr_thickness_failure.py`, genuine intrabar touches only): support
touches with a THIN zone below the level failed 40.8% of the time, vs.
**51.5%** for touches WITHOUT a thin zone below — the opposite of the
hypothesized direction. The `support_failure` explanatory classifier
(restricted to FROM_ABOVE touches, support_failure==1 if the level broke)
scored **MCC ≈ -0.07 to -0.08** across all three models — essentially no
predictive power from these thickness/order-flow features. Thickness alone
does not explain repeat-test failure in this sample.

## 8. What order-flow mechanics separate support held vs support failed?

**The CONSUMPTION signatures (H2/H5), not the thickness level itself.**
When a fat-zone support touch failed, **80.4%** of those failures had
`BidPull > BidAdd` at the touch bar (H2, strongly confirmed). The mirror
case for resistance: **81.2%** of resistance failures had `AskPull >
AskAdd` (H5, strongly confirmed). These are the two most defensible,
clean, walk-forward-style findings in this Atlas — failure is associated
with liquidity being PULLED away on the defending side at the moment of
the test, not merely with the zone being thin or fat in the abstract.

## 9. Does thickness improve the LEVEL STATE blocker?

**Mixed — H2/H5's consumption signatures are directly compatible with and
would strengthen the existing LEVEL STATE tab's blocker logic (which
already checks `BidPull>BidAdd`/`AskAdd>AskPull` — this Atlas independently
confirms those specific checks at 80%+ rates in a much larger sample, 21K
events vs. v4's ~3.4K). However, the broader thickness-percentile fields
(thin/medium/fat alone) showed weak-to-contradicting standalone predictive
value (Q7, Q3 of the LEVEL STATE report's own Q5 finding) - thickness
should be treated as a SUPPLEMENTARY context field for the blocker, not a
primary gating signal on its own.

## 10. Does thickness explain HistGB ACT winners vs losers?

**Partially, and one genuinely useful, non-tautological finding emerged.**
Of 2,899 scored historical candidates, the reconstructed model's ACT
decisions were almost always correct (24 losers vs. 1,695 winners) — but
this reflects the model being scored on its OWN (in-sample, final-fit)
training data, not genuine out-of-sample skill (documented exactly the
same caveat in the prior v4 shadow-inference build). The non-tautological
finding: ACT_LOSERs have a markedly THINNER mean thickness at entry (27.4)
than ACT_WINNERs (53.2) or PASS_CANDIDATEs (59.5), and a notably higher
mean `liquidity_vacuum_score` (0.50 vs. 0.38/0.35). This suggests thickness
COULD be a useful auxiliary filter alongside v4's probability, even though
v4's probability itself cannot be honestly evaluated for OOS skill from
this in-sample join alone.

## 11. Was the 2026-06-17 bad fold related to thickness/low-friction failure?

**Yes, strongly suggestive.** 20 of the 24 total historical ACT_LOSER cases
(83%) occurred on 2026-06-17 alone (HistGB's own worst OOS day,
mcc=-0.159). On that day, ACT_LOSERs had mean thickness-at-entry=26.2 (even
thinner than the all-days ACT_LOSER average of 27.4) and mean
`liquidity_vacuum_score`=0.47 — consistent with a thin/vacuum-zone failure
signature being disproportionately concentrated on the bad day, reinforcing
the LEVEL STATE tab's own earlier finding that the blocker would have
caught 69% of that day's bad entries.

## 12. Which features should be added to future models?

`thickness_at_level`, `thickness_above_level`, `thickness_below_level`
(dominant, walk-forward-validated importance for velocity/rejection/
breakout targets) and the two CONSUMPTION checks (`bid_add_vs_bid_pull`,
`ask_add_vs_ask_pull` at the moment of touch) — the latter pair is the
single strongest, cleanest finding in this whole Atlas (H2/H5, 80%+) and
is a strong candidate for direct inclusion in the LEVEL STATE blocker and
any future entry filter.

## 13. Which features are noisy?

The 6 composite friction scores (`auction_friction_score`,
`low_friction_travel_score`, `absorption_score`, `liquidity_vacuum_score`,
`two_way_exchange_score`, `replenishment_score`) showed LOW individual
feature importance in every walk-forward model (each ≤0.017) despite being
designed to be informative composites — they did not outperform the raw
thickness percentiles. `favorable_MFE_MAE` as a target was essentially
unpredictable from any of these features (MCC -0.15 to +0.16, inconsistent
sign across models) — none of this Atlas's contemporaneous order-flow
features meaningfully forecast the eventual MFE/MAE ratio. `imbalance_score`
appeared with nontrivial importance in 2 of 5 targets but with small
absolute magnitude (~0.015) — a marginal signal, not a strong one.

## 14. Is anything production-ready?

**No.** Every walk-forward model here is explicitly EXPLANATORY (per task
instruction, never trading). Effective independent sample counts are far
smaller than raw row counts suggest (~142 effective vs. ~13,900-18,600 raw
rows per target, due to heavily overlapping 40-bar forward windows in this
densely-touched event universe) — a caveat as important as v4's own ~85
effective-N finding earlier this session. `support_failure` and
`favorable_MFE_MAE` showed no usable signal at all. The two genuinely
strong findings (H2/H5 consumption signatures, thin/fat↔velocity relation)
are real and walk-forward-validated, but represent DESCRIPTIVE/EXPLANATORY
confirmation of mechanism, not a validated standalone production filter.

---

## Outputs produced
`outputs/`: book_flow_thickness_panel.parquet, thickness_formula_catalog.csv,
price_velocity_panel.parquet, thickness_forward_path_panel.parquet,
auction_friction_features.parquet, auction_friction_formula_catalog.csv,
thin_vs_fat_behavior_summary.csv, thin_zone_behavior.csv,
fat_zone_behavior.csv, hvn_lvn_poc_thickness_summary.csv,
support_resistance_thickness_failure_analysis.csv,
level_state_thickness_blocker_report.csv,
histgb_act_pass_thickness_attribution.csv,
histgb_winners_losers_thickness_report.csv,
bad_fold_20260617_thickness_diagnostic.csv,
thickness_explanatory_model_results.csv, thickness_feature_importance.csv.

## Verification performed
- Every script's only writes are inside this engine's own `outputs/`
  directory (grepped directly across all 8 scripts).
- No trading/broker/paper-trading flag is ever assigned `True` anywhere in
  this build (grepped directly).
- `ACTIVE_SHADOW_RELEASE` re-resolved before and after this build:
  unchanged (`level_reaction_continuous_nq_shadow_20260623T192502Z`).
- The v4 model artifact (`model_hist_gb_shadow.pkl` etc.) file timestamps
  unchanged from the prior session's build (05:50/05:52 today) — this
  Atlas only `pickle.load`s it for read-only batch scoring, never refits
  or re-saves it.
- `model_feature_master_shadow.parquet` timestamp unchanged by this build
  (this Atlas never opens it for writing). The Book Flow level-candle cache
  and `master_NQU6_shadow.ndjsonl` show RECENT mtimes from the live
  production daemons' own continuous background operation in this
  environment — independently verified via direct grep that no script in
  this build ever opens those paths in write mode.
- Two genuine bugs were found and fixed during this build: (1) Part B's
  initial bar→thickness join picked the THICKEST price level active
  anywhere in the bar's range rather than the level at the bar's own close,
  trivially biasing nearly every bar toward "FAT" — fixed to join on the
  level nearest the bar's own close. (2) Part G's `high_velocity_move`
  target initially scored a near-perfect MCC (0.99+) because
  `flow_into_level` (the literal denominator of the velocity target) was
  included as a feature — fixed by excluding it and all flow/friction
  features mathematically entangled with that one target's own
  construction (post-fix MCC: a much more plausible 0.37-0.43).

## FINAL FIELDS
```
PRODUCTION_FILES_MODIFIED: false
DASHBOARD_CODE_MODIFIED: false
BOOK_FLOW_CODE_MODIFIED: false
ACTIVE_MODEL_POINTER_CHANGED: false
TRADING_ENABLED: false
BROKER_CONNECTED: false
PAPER_TRADING_ENABLED: false
SCALE_USED: percentile
THINNESS_FAST_TRAVEL_RELATION_FOUND: true (MCC 0.37-0.43, walk-forward validated)
FATNESS_AUCTION_ACCEPTANCE_RELATION_FOUND: partially_true (HVN/POC skew fat
  directionally confirmed; standalone fat-zone hold-rate in the broad touch
  universe was lower than naive expectation - see Q7/Q9 nuance)
LEVEL_STATE_THICKNESS_USEFUL: partially_true (consumption checks H2/H5
  strongly confirmed and directly reinforce the existing blocker; thickness
  percentile alone was a weak/contradicting standalone signal for
  support_failure)
HISTGB_THICKNESS_ATTRIBUTION_USEFUL: partially_true (genuine non-tautological
  thinness/vacuum signature found in the rare loser cases and concentrated
  on the 2026-06-17 bad day; the ACT/PASS win-rate split itself is
  in-sample/not a genuine OOS skill measurement)
PRODUCTION_READY: false
PAPER_TRADING_READY: false
OVERALL: PASS
```

PASS means: the Atlas was built end-to-end across all 8 parts, every
formula/threshold is documented with its exact value, no future return was
used inside any feature definition (verified and one leakage bug caught and
fixed), purged/embargoed walk-forward validation was used throughout (never
random K-fold), effective sample counts are reported honestly alongside
raw counts, and two real mechanistic findings (order-flow consumption
signatures at failure, thin↔fast-travel) were confirmed with real effect
sizes. PASS does NOT mean any feature here is ready for production or
paper trading - several hypotheses were NOT confirmed or were directly
contradicted by the data, and this is reported as such rather than
smoothed over.
