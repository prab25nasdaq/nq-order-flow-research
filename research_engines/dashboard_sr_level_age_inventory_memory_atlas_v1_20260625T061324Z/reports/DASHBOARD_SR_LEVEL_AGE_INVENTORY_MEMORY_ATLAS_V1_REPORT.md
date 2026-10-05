# Dashboard S/R Level Age + Inventory Memory Atlas v1 — FINAL REPORT
=============================================================================
BUILD_DATE: 2026-06-25
STATUS: SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING
SCOPE: descriptive/mechanistic only - NO MODEL TRAINING anywhere in this atlas.

=============================================================================
1. What exact dashboard S/R level logic was used?
=============================================================================
Extracted verbatim from `ofi_live_dashboard_WORKING_NEXT_with_logreg.py`,
no inference, no invented method:

- **Swing S/R** (`swing_levels`/`compute_sr_levels`, source lines 597-651):
  a bar is a swing high/low if it is the max/min of a centered window of
  `2*lb+1` bars; swing points are then clustered by price (`cluster_dist`)
  and scored `count*(0.4+0.6*recency)`. The dashboard's own CHART-tab call
  site (lines 5237-5247) uses `lb=4`, `cluster_dist=6.0` (the adaptive
  branch for windows ≥300 bars - always true here), over `window_bars`
  (default 120, user-adjustable, including "ALL"=full history) bars ending
  at the current bar - **already a fixed-lookback-window method, not a
  zoom/viewport dependency**.
- **Volume profile POC/VAH/VAL/HVN/LVN** (lines 4760-4894): standard 70%
  value-area-around-POC with HVN/LVN via rolling-mean deviation
  (threshold 1.45/0.55, kernel `max(3,n_lev//15)`) - **but the bar RANGE
  fed into it is the live `ax_price.get_xlim()` zoom state**, a genuine
  display-only dependency.
- **Projected prior-contract levels**: a static CSV
  (`projected_levels_NQM6_to_NQU6.csv`), read-only, never computed by the
  dashboard - prior NQM6 levels shifted to NQU6 scale.

Full detail: `outputs/dashboard_sr_formula_catalog.{csv,md}`,
`outputs/dashboard_sr_source_audit.md`.

=============================================================================
2. Are dashboard S/R levels past-only, or do any carry lookahead risk?
=============================================================================
**Past-only, once one specific, narrow, fully-corrected confirmation lag is
respected.** Swing detection's centered window means a swing at bar `i`
cannot be confirmed until bar `i+lb`; this atlas enforces that lag
explicitly everywhere a level is created (Part B), so no level is ever
backdated to "exist" before it could genuinely have been known. The volume
profile's own math reads no future bars either way; its only issue is the
**live zoom dependency**, which is excluded from this atlas entirely
(`DISPLAY_BEHAVIOR_ONLY`, not used in Parts B-H) and rebuilt instead over
fixed past-only lookback windows. Full audit:
`outputs/dashboard_sr_leakage_audit.csv` (every row: `LOW` risk, with the
specific reason documented per level family and one explicitly-flagged
compute-budget deviation: the volume-profile `n_lev` cap was relaxed from
the dashboard's literal 3000 to 6000, since that cap is a Tkinter
UI-redraw guard with no statistical meaning, irrelevant to this offline
batch job).

=============================================================================
3. Do old dashboard S/R levels work in our data?
=============================================================================
**Yes, clearly.** Across 57,773 swing S/R retouch events (built by rerunning
the dashboard's own `compute_sr_levels` formula at six fixed lookback
windows over the full continuous backadjusted master, 19,862+ bars,
2026-06-03 to 2026-06-24), the overall hold rate (SUPPORT_HELD +
RESISTANCE_HELD as a share of all 116,028 total events, including the
secondary volume-profile-level events) is **88.6%**. Critically, this rate
is **flat across age buckets** - old levels do not show a materially lower
hold rate than young ones (see Q4-Q6).

=============================================================================
4. How does performance change from 500 to 1000/5000/10000/full-history lookback?
=============================================================================
Essentially **no degradation with larger lookback / older levels**.
`outputs/dashboard_sr_age_bucket_performance.csv` (overall_hold_rate by
window x age bucket):

| window | age_0_500 | age_500_1000 | age_1000_5000 | age_5000_10000 | age_10000_plus |
|---|---|---|---|---|---|
| 500bar | 0.891 | 0.813 (n=91) | - | - | - |
| 1000bar | 0.894 | 0.876 | 0.921 (n=38) | - | - |
| 2500bar | 0.893 | 0.880 | 0.884 | - | - |
| 5000bar | 0.898 | 0.886 | 0.885 | 0.333 (n=3) | - |
| 10000bar | 0.898 | 0.885 | 0.887 | 0.887 | - |
| full_history | 0.899 | 0.887 | 0.888 | 0.908 | 0.897 |

All rates cluster tightly in the high-0.8s to low-0.9s band regardless of
window or age, **except cells with n<40** (500bar's age_500_1000, 1000bar's
age_1000_5000, 5000bar's age_5000_10000), which are noise, not signal -
flagged explicitly in `dashboard_sr_level_half_life_estimates.csv`'s
`n_valid_age_buckets` column.

=============================================================================
5. Do 5,000-bar-old levels still matter?
=============================================================================
**Yes.** `full_history` window's `age_5000_10000` bucket: hold rate 0.908
(n=866), `old_level_reactivation_rate=1.0` (every old level produced a real
reaction, never `NO_REACTION`, when retested) - no different from young
levels.

=============================================================================
6. Do 10,000-bar-old levels still matter?
=============================================================================
**Yes.** `full_history` window's `age_10000_plus` bucket: hold rate 0.897
(n=2,410), reactivation rate 1.0 - statistically indistinguishable from
young levels. `level_half_life_estimate_bars` is `NOT_STATISTICALLY_VALID`
for every window (`dashboard_sr_level_half_life_estimates.csv`) precisely
*because* there is no meaningful decay to fit a half-life to - hold rate
simply does not decline with age in this dataset.

=============================================================================
7. Which dashboard level types work best?
=============================================================================
Swing S/R (the primary, age-tracked family) holds at ~88-90% regardless of
type (support vs resistance perform near-identically: overall support
hold-rate and resistance hold-rate are both in the same band - see
`dashboard_sr_age_decay_summary.csv` grouped by `level_type`). The
secondary volume-profile families (POC/VAH/VAL/HVN/LVN) are daily
snapshots without a comparable age dimension (by design - see Part C
scoping note) so they are not directly comparable on "which type works
best across age"; they are used here as **confluence context**, not a
competing primary signal.

=============================================================================
8. Does repeated touching weaken a dashboard S/R level?
=============================================================================
**No clear weakening.** `dashboard_sr_touch_number_decay.csv`: overall hold
rate by touch bucket, e.g. `full_history`: 1st touch 0.882, 2nd touch
0.900, 3rd touch 0.908, 4th-plus 0.894 - touches 2 and 3 are if anything
*slightly* higher than touch 1, not lower. The pattern repeats across all
six lookback windows. There is no "the more it's tested, the weaker it
gets" signal in this dataset at this bar resolution.

=============================================================================
9. Does first touch work better than second/third touch?
=============================================================================
**No** - see Q8. First touch is consistently the *lowest* (not highest)
hold rate across all six windows (e.g. 500bar: 0.880 vs 0.903/0.897/0.888
for 2nd/3rd/4th+), the opposite of the "fresh level is strongest" prior.
This is a genuine, somewhat counter-intuitive empirical finding worth
flagging for follow-up rather than over-interpreting from one dataset.

=============================================================================
10-11. Order-flow mechanics: dashboard support holding vs failing
=============================================================================
`outputs/why_dashboard_old_support_works.csv` (old = age ≥5,000 bars,
n=2,575 old-support tests, 1,619 with Book Flow mechanics coverage):

| outcome | mean touch BidPull−BidAdd | mean touch Signed | mean pre_20 support_consumption |
|---|---|---|---|
| HELD (n=1,446 w/ mechanics) | **+40.6** | -30.3 | +488.6 |
| BROKE (n=173 w/ mechanics) | **+198.0** | -368.2 | +665.1 |

Support that **holds** shows mild bid-side consumption right at the touch
(BidPull modestly exceeds BidAdd); support that **breaks** shows nearly
**5x more** bid-side consumption at the exact touch bar, plus a much more
negative `Signed` flow (more aggressive selling into the level) - this is
the clearest, most interpretable mechanics signature in this atlas.

=============================================================================
12-13. Order-flow mechanics: dashboard resistance holding vs failing
=============================================================================
`outputs/why_dashboard_old_resistance_works.csv` mirrors the support
finding: BROKE resistance shows `AskPull−AskAdd` and ask-side pull pressure
materially elevated relative to HELD resistance at the touch bar, and a
more strongly positive `Signed` flow (more aggressive buying through the
level) - directionally symmetric to the support case.

=============================================================================
14. Is BidPull > BidAdd still the best dashboard support-failure warning?
=============================================================================
**Yes, directionally confirmed.** `touch_support_consumption` (=
BidPull−BidAdd) is positive (BidPull exceeds BidAdd) in both HELD and
BROKE groups on average, but the **magnitude** is the discriminator: ~5x
larger at BROKE touches than HELD touches (198.0 vs 40.6). A simple
"BidPull>BidAdd" boolean alone is not sufficiently discriminating on its
own (it is true in both groups on average); the **degree** of consumption
is what separates failure from hold in this data.

=============================================================================
15. Is AskPull > AskAdd still the best dashboard resistance-failure warning?
=============================================================================
**Same pattern, confirmed directionally.** See Q12-13 -
`resistance_consumption` (AskPull−AskAdd) is materially more negative-to-
positive-shifted (more ask-side depletion) at BROKE resistance touches
than HELD ones in `why_dashboard_old_resistance_works.csv`.

=============================================================================
16. Do fat zones preserve old dashboard levels better than thin zones?
=============================================================================
**Not directly testable to a firm conclusion in this build.** This atlas
does not recompute its own thickness percentile (per the task's input
list, the Book-Flow Thickness/Auction Friction Atlas is reused only "for
thickness/order-flow context", not rebuilt here) and the events table does
not currently carry a joined thin/fat-zone flag from that atlas. The
`touch_resting_depth_mean` field in `dashboard_sr_orderflow_mechanics.parquet`
is a usable depth proxy for a follow-up cut but was not used to answer this
question definitively in this report - flagged as an open follow-up rather
than answered with a forced conclusion.

=============================================================================
17. Are old dashboard levels more useful in certain sessions?
=============================================================================
`dashboard_sr_age_decay_summary.csv` is grouped by `session` alongside
`lookback_window`/`level_age_bucket`/etc. Sample sizes for old-age-bucket x
specific-session cells are generally thin (the overnight/Asia-EU session
dominates raw bar count in this 15-day sample); within the cells that do
have n≥30, hold rates remain in the same 0.85-0.92 band seen everywhere
else - no session emerged as a strong outlier for old-level performance
specifically. Treat as a preliminary, not a strong finding given the
limited sample.

=============================================================================
18. Are old dashboard levels more useful in certain volatility/VPIN regimes?
=============================================================================
Regime labels (`volatility_regime`, `vpin_regime` - past-only rolling
terciles, `sr_common.rolling_state_tercile`, window=500) are attached to
every retouch event and available for cross-tabulation in
`dashboard_sr_age_decay_summary.csv` (note: `liquidity_cost_regime` is
`NOT_AVAILABLE` - the continuous master has no spread/liquidity-cost
column, and this was not derived from a separate source within this
atlas's scope). As with Q17, no single regime cut produced a result
strong enough, at adequate sample size, to assert a confident "yes, old
levels matter more under X regime" beyond the general finding (Q3-Q6) that
age itself does not erode hold rate.

=============================================================================
19. Which old-dashboard-level features should be added to LEVEL STATE / OFI Level Decision?
=============================================================================
Strongest, most defensible candidates from `dashboard_old_sr_feature_panel.parquet`
(all past-only, see `dashboard_old_sr_feature_leakage_audit.csv`):
- **`dashboard_old_level_hold_rate_past_only`** and **`touch_count`** - the
  clearest, most data-grounded signal in this atlas (a level's own track
  record predicts nothing gets worse with age, so a level with many past
  holds is simply a reliable reference, independent of how old it is).
- **`nearest_dashboard_old_support/resistance_distance_ticks`** - basic
  proximity context, cheap and already past-only.
- **`dashboard_old_support_consumed_last_test`/`...resistance_consumed...`**
  - directly operationalizes the Q14/Q15 finding (consumption MAGNITUDE at
    the last test is the real signal); recommend pairing with the
    continuous consumption magnitude, not just the boolean, given Q14/Q15's
    finding that the boolean alone under-discriminates.
- `dashboard_old_level_memory_score` is a reasonable composite starting
  point but is an a-priori, undata-fit construction - if adopted, it
  should be validated empirically before being trusted as a ranking, not
  just used because it is intuitive.
None of these are proposed as ready to wire into LEVEL STATE/OFI Level
Decision without that team's own validation - this atlas only confirms
they are well-formed, past-only, and grounded in a real empirical pattern.

=============================================================================
20. Is anything production-ready?
=============================================================================
**No.** This is a descriptive/mechanistic research atlas only. No model was
trained, no production code was touched, and the feature panel is offered
as *candidates for future consideration*, not a validated, production-ready
feature set. See Final Fields below.

=============================================================================
DATA SCOPE (documented, not silently assumed)
=============================================================================
- Price series for all level-building (Part B) and retouch/behavior/age
  analysis (Parts C-D, F-H): `master_NQ_continuous_backadjusted_shadow.ndjsonl`,
  19,862+ bars (file is live and growing; row count varies slightly run to
  run), 2026-06-03 to 2026-06-24, spanning the NQM6→NQU6 roll.
- Order-flow mechanics (Part E): Book Flow level-candle cache, NQU6-only,
  2026-06-15 to 2026-06-24 (8 days), top10 depth - a **subset** of the
  price series' date range. 43,190/116,028 retouch events (37.2%) fall
  within this coverage and carry real mechanics fields; the remaining
  62.8% have `has_orderflow_mechanics=False` and all-NaN mechanics columns
  - never imputed. This mirrors the same documented scope decision already
  established in the Book Flow Thickness/Auction Friction Atlas and the
  Level Mechanics Atlas (reused, not re-derived).
- Behavior labels: `SUPPORT_FAILED`/`RESISTANCE_FAILED` never occurred
  under the a-priori thresholds used (every break either reclaimed
  quickly = `FAKE_BREAKOUT_SWEEP`, or continued strongly =
  `BREAKOUT_ACCEPTANCE`). "Held vs failed" comparisons in Part G therefore
  use HELD vs (BREAKOUT_ACCEPTANCE + FAKE_BREAKOUT_SWEEP combined) as
  "worked vs broke" - documented explicitly, not silently substituted.

=============================================================================
DELIVERABLES (all under this engine's own outputs/)
=============================================================================
Part A: dashboard_sr_formula_catalog.{csv,md}, dashboard_sr_source_audit.md
Part B: dashboard_sr_levels_by_lookback.parquet (696,169 rows),
        dashboard_sr_levels_by_lookback_diagnostics.csv, dashboard_sr_leakage_audit.csv,
        dashboard_sr_projected_prior_contract_levels_static.csv
Part C: dashboard_sr_retouch_events.parquet (116,028 rows), dashboard_sr_retouch_event_diagnostics.csv
Part D: dashboard_sr_behavior_labels.parquet, dashboard_sr_mfe_mae_by_horizon.parquet
Part E: dashboard_sr_orderflow_mechanics.parquet (125 cols), dashboard_sr_mechanics_catalog.csv
Part F: dashboard_sr_age_decay_summary.csv, dashboard_sr_age_bucket_performance.csv,
        dashboard_sr_touch_number_decay.csv, dashboard_sr_level_half_life_estimates.csv
Part G: why_dashboard_old_support_works.csv, why_dashboard_old_resistance_works.csv,
        why_dashboard_old_levels_fail.csv, dashboard_old_level_confluence_report.csv
Part H: dashboard_old_sr_feature_panel.parquet, dashboard_old_sr_feature_catalog.csv,
        dashboard_old_sr_feature_leakage_audit.csv

=============================================================================
FINAL FIELDS
=============================================================================
PRODUCTION_FILES_MODIFIED: false
DASHBOARD_CODE_MODIFIED: false
BOOK_FLOW_CODE_MODIFIED: false
ACTIVE_MODEL_POINTER_CHANGED: false
MODEL_STUDY_RUN: false
TRADING_ENABLED: false
BROKER_CONNECTED: false
PAPER_TRADING_ENABLED: false
DASHBOARD_SR_LOGIC_EXTRACTED: true
DASHBOARD_SR_ONLY: true
PAST_ONLY_LEVELS_VERIFIED: true
LEVEL_AGE_EFFECT_FOUND: false (hold rate is flat across age buckets - the notable finding IS the absence of decay)
TOUCH_DECAY_EFFECT_FOUND: false (touch 2/3 perform as well as or better than touch 1 - no weakening with repeated touches)
CONSUMPTION_SIGNATURE_CONFIRMED: true (BidPull/AskPull magnitude at the touch bar discriminates HELD from BROKE)
DASHBOARD_OLD_SR_FEATURES_CREATED: true
PRODUCTION_READY: false
PAPER_TRADING_READY: false
OVERALL: PASS

PASS means: the dashboard's own S/R formula was extracted verbatim (no
invented method), confirmed past-only with one documented, fully-corrected
confirmation-lag adjustment, rebuilt faithfully at six fixed lookback
windows, and used to generate a large, real retouch-event/behavior-label/
order-flow-mechanics dataset that answers the core research question with
genuine empirical evidence (old levels - even 10,000+ bars old - hold at
the same ~89% rate as young ones, and the order-flow consumption-magnitude
signature meaningfully separates holds from breaks). No production,
trading, broker, model-artifact, or Book Flow code was touched, no model
was trained, and every feature in the Part H panel is verified past-only.
PASS does NOT mean any feature here is validated for production use, nor
does it claim a definitive answer on the thickness (Q16) or
session/regime (Q17-18) questions, which are flagged as open follow-ups
rather than forced conclusions.
