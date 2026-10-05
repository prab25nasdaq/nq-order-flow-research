# Dashboard Formula Catalog (Part A)

Extracted by direct line-by-line reading of the dashboard source files listed below.
No formula was invented; every entry cites exact source lines.

- ofi_live_dashboard_WORKING_NEXT_with_logreg.py
- model_probs_trust_tab.py
- ofi_level_decision_tab.py (catalogued separately in Part B)
- level_reaction_dashboard_tab.py (a predictions.csv VIEWER, no new formulas beyond what is already documented for the level-reaction model elsewhere in this codebase)

## Z-SCORES

### residual_z
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 455-460
- **source columns**: mid_mean; delta_norm; volatility_5; sweep_imbalance_norm; vpin
- **rolling/bar window**: 20 / 20 bars
- **z-score window**: 20  |  **percentile window**: n/a
- **session/day reset**: none (continuous across session boundary) / none (continuous across day boundary)
- **closed vs forming**: closed-bar master rows only (dashboard reads master ndjsonl, which only logs closed bars)
- **normalization**: (x - rolling_mean(w=20)) / rolling_std(w=20, ddof=1); min_periods=20 STRICT (no partial window)
- **output column(s)**: mid_resid_z, delta_norm_resid_z20, volatility_5_resid_z20, sweep_imbalance_norm_resid_z20, vpin_resid_z20
- **leakage risk**: NONE - rolling window is backward-looking only (pandas default, not centered)

## Z-SCORES / Q-SCIENCE

### _causal_z
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 858-864
- **source columns**: return r; delta_norm; ofi (mlofi_norm); sweep_imbalance_norm
- **rolling/bar window**: 128 / 128 bars (64 for price/flow slope variants)
- **z-score window**: 128  |  **percentile window**: n/a
- **session/day reset**: none / none
- **closed vs forming**: closed-bar only
- **normalization**: (x - rolling_mean(w).shift(1)) / rolling_std(w).shift(1); min_ref=30 (20 for slope variants); EXTRA shift(1) beyond residual_z means bar t's own value never enters its own mean/std
- **output column(s)**: ret_z (regime codes), delta_z, ofi_z, sweep_z, price_slope_z, flow_slope_z
- **leakage risk**: NONE - causal by construction (explicit .shift(1))

## REGIME BREAKS

### _cusum_breaks
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 925-940
- **source columns**: log-return r (derived from mid_mean/mid_kf/px_close)
- **rolling/bar window**: EWM halflife=64 (dynamic vol threshold) / unbounded EWM (no fixed window)
- **z-score window**: n/a (EWM-standardized, not a fixed rolling window)  |  **percentile window**: n/a
- **session/day reset**: none / none (s_plus/s_minus accumulate across day boundaries in the dashboard's own usage)
- **closed vs forming**: closed-bar only
- **normalization**: z=(r-ewm_mean(hl=64))/ewm_std(hl=64); standard CUSUM filter, drift k=0.35, barrier h=5.0, resets to 0 on break
- **output column(s)**: cusum_up_break, cusum_down_break (also present natively in master ndjsonl - same formula family)
- **leakage risk**: NONE - EWM and cumulative sum are strictly backward-looking. THIS ENGINE reproduces the dashboard's cross-day accumulation EXACTLY (s_plus/s_minus are NOT reset at session/day boundaries, matching source behavior) - see Part D

### _csw_scores (Chu-Stinchcombe-White)
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 943-961
- **source columns**: log-mid y
- **rolling/bar window**: expanding, capped at max_anchor=250 bars back / up to 250 bars
- **z-score window**: n/a  |  **percentile window**: n/a (raw score vs analytic critical value 4.6+log(gap))
- **session/day reset**: none / none
- **closed vs forming**: closed-bar only
- **normalization**: max over anchors of |y[t]-y[anchor]| / (sigma[t]*sqrt(gap)); sigma=expanding RMS of first differences
- **output column(s)**: csw_score, csw_crit, csw_pct (rolling_pct of csw_score)
- **leakage risk**: NONE - anchors are strictly t-1 and earlier

### _sample_heavy_scores (SADF/SMT proxy)
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 999-1071
- **source columns**: log-mid y
- **rolling/bar window**: SADF: wmin=50,wmax=500,step=10; SMT: wmin=32,wmax=500,step=8 / 50-500 bars
- **z-score window**: n/a  |  **percentile window**: historical re-scan, start=max(60,len-350), step=12
- **session/day reset**: none / none
- **closed vs forming**: closed-bar only; COMPUTED IN A BACKGROUND THREAD in the live dashboard (expensive), cached per (vol_type,window_bars,n,first_ts,last_ts) key
- **normalization**: max ADF/SMT t-stat over a window grid, normalized to a historical percentile
- **output column(s)**: sadf_up, sadf_down, sadf_pct, smt_up, smt_down, smt_pct
- **leakage risk**: NONE for the per-bar score itself (window only ever looks backward), but EXCLUDED from this engine's training feature panel: too computationally expensive to run at full historical density (5-10 min per call in the live dashboard) for the ~6,000-bar history here - documented scope exclusion, not a leakage issue

### break_score / break_age / market_state
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 2839-2879
- **source columns**: cusum up/down, csw_pct, sadf_pct, smt_pct, toxicity, spread_pct, entropy_score, flow_alignment
- **rolling/bar window**: n/a (point-in-time composite of already-rolling inputs) / n/a
- **z-score window**: n/a  |  **percentile window**: n/a
- **session/day reset**: none / none
- **closed vs forming**: closed-bar only
- **normalization**: break_score=max(cusum_event?1:0, csw_pct, sadf_pct, smt_pct); break_age=bars since last break; market_state in {EXPLOSIVE MOVE, TOXIC FLOW, REGIME BREAK, CHOP/RANDOM, CLEAN TREND, NO EDGE} via threshold rules on the above
- **output column(s)**: break_score, break_age, market_state, direction
- **leakage risk**: NONE for break_score/break_age; sadf_pct/smt_pct excluded from this engine's panel (see above) so market_state is reproduced WITHOUT the SADF/SMT branches - documented partial parity, see dashboard_feature_parity_report.csv

## ENTROPY

### _quantile_codes + _rolling_abs_quantile (state-code inputs)
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 826-855
- **source columns**: delta_norm; ofi (mlofi_norm); sweep_imbalance_norm; spread
- **rolling/bar window**: 256 / 256 bars
- **z-score window**: n/a  |  **percentile window**: 256-bar quantile breakpoints (k=5 buckets) / 256-bar abs-quantile deadband (q=0.60)
- **session/day reset**: none / none
- **closed vs forming**: closed-bar only
- **normalization**: digitize(v, quantile_breakpoints(prior 256 bars)); deadband sign via rolling abs 60th percentile (floor 0.05)
- **output column(s)**: delta_codes, ofi_codes, sweep_codes, spread_codes, flow_joint_codes
- **leakage risk**: NONE - breakpoints computed from bars STRICTLY BEFORE i (ref = vals[i-window:i], excludes i)

### _rolling_entropy + composite entropy_score
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 803-823, 2765-2782
- **source columns**: ret_codes(k=5), delta_codes(k=5), ofi_codes(k=5), sweep_codes(k=3), spread_codes(k=4), flow_joint_codes(k=27)
- **rolling/bar window**: 128 / 128 bars (trailing, INCLUSIVE of current bar)
- **z-score window**: n/a  |  **percentile window**: n/a
- **session/day reset**: none / none
- **closed vs forming**: closed-bar only
- **normalization**: Shannon entropy / log(k), normalized [0,1]; composite = 0.35*return + 0.35*flow_joint + 0.15*spread + 0.15*mean(delta,ofi,sweep); min_n=max(8,min(window//4,32))=32
- **output column(s)**: entropy_score, entropy_return, entropy_delta, entropy_ofi, entropy_sweep, entropy_spread, entropy_flow
- **leakage risk**: LOW - the 128-bar entropy window INCLUDES the current bar's own code (not lagged), unlike the causal_z/quantile_codes inputs feeding it. This is the dashboard's own live-display convention; this engine's feature panel keeps it as-is for parity but flags it explicitly since a model feature including bar t's own discretized state is a one-bar look-VERY-slightly-forward in the sense that t's code does help define t's own entropy bucket weighting (not the underlying market data itself, which remains causal) - low materiality, documented not hidden

## FLOW TOXICITY / Q-SCIENCE

### flow_score / flow_score_raw / flow_divergence
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 2784-2799
- **source columns**: delta_z, ofi_z, sweep_z (each _causal_z(window=128)); return r (_causal_z(window=64))
- **rolling/bar window**: 128 (z inputs), 64 (slope), 5 (EMA span) / varies by component
- **z-score window**: 128  |  **percentile window**: n/a
- **session/day reset**: none / none
- **closed vs forming**: closed-bar only
- **normalization**: flow_score_raw=0.40*tanh(delta_z/2)+0.35*tanh(ofi_z/2)+0.25*tanh(sweep_z/2); flow_score=EMA(span=5) of that; flow_divergence=causal_z(r,64)-causal_z(diff(flow_score),64)
- **output column(s)**: flow_score, flow_alignment, flow_direction, flow_divergence, flow_consistency
- **leakage risk**: NONE - all inputs causal

## FLOW TOXICITY

### vpin / vpin_pct / vpin_state
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 2801-2812
- **source columns**: vpin (native master column if present); else buy_vol/sell_vol
- **rolling/bar window**: native vpin: production parser's own window (external); fallback: 75 / 75 bars (fallback only)
- **z-score window**: n/a  |  **percentile window**: 500
- **session/day reset**: none / none
- **closed vs forming**: closed-bar only
- **normalization**: vpin_pct=rolling_pct(vpin,window=500); state: NORMAL<0.70, ELEVATED<0.90, TOXIC<0.95, else EXTREME_TOXICITY
- **output column(s)**: vpin, vpin_pct, vpin_state
- **leakage risk**: NONE

## LIQUIDITY COST

### rolling_kyle (Kyle's lambda)
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 1074-1087
- **source columns**: mid; order-flow imbalance q=buy_vol-sell_vol (LAGGED 1 bar)
- **rolling/bar window**: 160 / 160
- **z-score window**: n/a  |  **percentile window**: 500 (kyle_pct)
- **session/day reset**: none / none
- **closed vs forming**: closed-bar only
- **normalization**: rolling_cov(dp[t], q[t-1]) / rolling_var(q[t-1]); min_periods=max(20,window//4)=40
- **output column(s)**: kyle, kyle_pct
- **leakage risk**: NONE - q is explicitly lagged 1 bar before regression

### amihud illiquidity
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 2817-2818
- **source columns**: |dp| (abs price change); dollar_vol=mid*volume
- **rolling/bar window**: 160 / 160
- **z-score window**: n/a  |  **percentile window**: 500
- **session/day reset**: none / none
- **closed vs forming**: closed-bar only
- **normalization**: rolling_median(160, min_periods=30) of |dp|/dollar_vol
- **output column(s)**: amihud, amihud_pct
- **leakage risk**: NONE

### roll_measure / roll_impact
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 1090-1094, 2819-2821
- **source columns**: dp (price change)
- **rolling/bar window**: 160 / 160
- **z-score window**: n/a  |  **percentile window**: 500
- **session/day reset**: none / none
- **closed vs forming**: closed-bar only
- **normalization**: 2*sqrt(max(0,-rolling_cov(dp,dp.shift(1)))); roll_impact=roll/rolling_mean(dollar_vol,160)
- **output column(s)**: roll, roll_impact, roll_pct
- **leakage risk**: NONE

### corwin_schulz spread estimator
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 1097-1113
- **source columns**: px_high, px_low (current + 1-bar-lagged)
- **rolling/bar window**: 2 bars (closed-form, no fixed window) / 2
- **z-score window**: n/a  |  **percentile window**: 500
- **session/day reset**: none / none
- **closed vs forming**: closed-bar only
- **normalization**: Corwin & Schulz (2012) high-low spread estimator, beta/gamma/alpha closed form
- **output column(s)**: cs_spread, cs_spread_pct
- **leakage risk**: NONE - uses bar t and t-1 only

## LIQUIDITY COST / FLOW TOXICITY

### toxicity / liquidity_cost composites
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 2851-2855
- **source columns**: vpin_pct, spread_pct, kyle_pct, amihud_pct, roll_pct, cs_spread_pct
- **rolling/bar window**: n/a (combination of already-rolling inputs) / n/a
- **z-score window**: n/a  |  **percentile window**: n/a
- **session/day reset**: none / none
- **closed vs forming**: closed-bar only
- **normalization**: toxicity=clip(0.45*vpin_pct+0.20*spread_pct+0.15*kyle_pct+0.10*amihud_pct+0.10*roll_pct,0,1); liquidity_cost=clip(0.35*spread_pct+0.25*roll_pct+0.25*amihud_pct+0.15*cs_spread_pct,0,1)
- **output column(s)**: toxicity, liquidity_cost
- **leakage risk**: NONE (inherits NONE from all inputs)

## Q-SCIENCE

### _hurst_rs (R/S Hurst exponent)
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 687-715
- **source columns**: log-price
- **rolling/bar window**: full available window up to call time (R/S over geomspace(10,80,12) lags) / variable (lag-dependent)
- **z-score window**: n/a  |  **percentile window**: n/a
- **session/day reset**: none / none
- **closed vs forming**: closed-bar only
- **normalization**: slope of log(R/S) vs log(lag), clipped [0.01,0.99]
- **output column(s)**: hurst (display-only in the dashboard's Q-SCIENCE tab)
- **leakage risk**: EXCLUDED from this engine's feature panel - the dashboard calls this on the FULL visible window each redraw (not a true expanding causal series); reproducing a leakage-free per-bar Hurst would require redesigning the call pattern, which is out of scope here and documented as such rather than silently approximated

### lead/lag Spearman correlation panel
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 676-684, ~2100-2184
- **source columns**: various signals vs forward return at several lag horizons
- **rolling/bar window**: full visible window (N bars) / variable
- **z-score window**: n/a  |  **percentile window**: n/a
- **session/day reset**: none / none
- **closed vs forming**: closed-bar only
- **normalization**: rank correlation (argsort-of-argsort, no scipy) of signal[t] vs return[t+lag]
- **output column(s)**: display heatmap only - NOT a per-bar feature in the dashboard (it is a SUMMARY panel over the visible window), therefore not reproduced as a row-level feature here; this engine's own Part D/feature-IC work (re-used from the v2 engine's rolling-IC methodology) supersedes it with a genuinely causal, no-lookahead per-bar version
- **leakage risk**: N/A - not used as a per-row feature; the dashboard's OWN version uses forward returns directly in a window that includes 'future' bars relative to the window start, which is fine for a DISPLAY summary but would leak if naively used per-row, which is exactly why it is not reproduced row-wise here

## Q-SCIENCE / MICROSTRUCTURE

### _adx_series (Wilder ADX)
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 718-740
- **source columns**: px_high, px_low, px_close
- **rolling/bar window**: 14 / 14
- **z-score window**: n/a  |  **percentile window**: n/a
- **session/day reset**: none / none
- **closed vs forming**: closed-bar only
- **normalization**: Wilder EWM (alpha=1/14) smoothed +DI/-DI/ADX, standard formula
- **output column(s)**: adx, plus_di, minus_di
- **leakage risk**: NONE - ewm(adjust=False) is strictly causal

## ORDER FLOW

### bull/bear pressure composite
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 2186-2256
- **source columns**: delta_norm, sweep_imbalance_norm, buy_vol/sell_vol, vpin, DOM depth history, absorption proxy
- **rolling/bar window**: 80 (_pctrank default) per component / 80
- **z-score window**: n/a  |  **percentile window**: 80
- **session/day reset**: none / none
- **closed vs forming**: closed-bar only (DOM-depth component additionally needs live depth history, EXCLUDED here - not reconstructable from historical master/book-flow files)
- **normalization**: mean of per-component rolling percentile ranks (window=80), then EMA(span=8)
- **output column(s)**: bull_pressure, bear_pressure
- **leakage risk**: NONE for the reproduced components (buy_delta, buy_sweep, bid_liquidity proxy, vpin_inv, absorption); DOM-depth component dropped (live-only data, not a leakage issue)

## MICROSTRUCTURE

### swing_levels / compute_sr_levels
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 570-624
- **source columns**: px_high, px_low
- **rolling/bar window**: 2*lb+1 (lb=3 chart default, lb=4 S/R-panel default) / 7-9 bars (CENTERED)
- **z-score window**: n/a  |  **percentile window**: n/a
- **session/day reset**: none / none
- **closed vs forming**: closed-bar only
- **normalization**: pandas rolling(window, center=True).max()/.min() swing-high/low detection, then price-clustered (cluster_dist=2.0 pts) and scored by touch-count x recency
- **output column(s)**: swing highs/lows, sr_levels (chart overlay only)
- **leakage risk**: HIGH - center=True means the swing label at bar i depends on bars BOTH before AND after i (i+lb). EXCLUDED from this engine's feature panel entirely for this reason; this engine's own S/R gate (Part E) uses the book-flow cache's HVN/LVN/POC/VAH/VAL fields and a causal/de-leaked rolling-level construction instead, never this centered swing detector

### detect_absorptions
- **source**: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` lines 627-646
- **source columns**: buy_vol, sell_vol, vol_total, px_open, px_close
- **rolling/bar window**: full visible window (75th percentile of vol_total computed over it) / variable
- **z-score window**: n/a  |  **percentile window**: 75
- **session/day reset**: none / none
- **closed vs forming**: closed-bar only
- **normalization**: high-volume (>=p75) bar with one-sided flow (buy or sell ratio>=0.60) closing AGAINST that flow's direction (down on buy-dominant, up on sell-dominant)
- **output column(s)**: absorption flags (chart overlay; also feeds bull/bear pressure 'absorption' component above)
- **leakage risk**: LOW - the 75th-percentile threshold in the LIVE dashboard is computed over the FULL visible window (could include bars after t); this engine's feature panel recomputes it as a trailing rolling percentile instead (documented deviation, made MORE conservative/causal than the source, not less)

## MODEL PROBS

### compute_market_support_factors
- **source**: `model_probs_trust_tab.py` lines 456-543
- **source columns**: mlofi_norm, mlofi_rolling_5, decay_norm, delta_norm, *_resid_z20, entropy_score, vpin, bar_duration_s, sweep_norm, cusum_up_break, cusum_down_break, regime_ic_delta, regime_ic_mlofi, prev_wk_h40_ic, reaction_type, dist_to_level_ticks
- **rolling/bar window**: n/a (point-in-time factor combination of already-rolling master columns) / n/a
- **z-score window**: n/a  |  **percentile window**: n/a
- **session/day reset**: none / none
- **closed vs forming**: closed-bar only
- **normalization**: 8 factors each scored SUPPORTS_LONG/SUPPORTS_SHORT/NEUTRAL/WARNS_CHOP/WARNS_TOXIC/MISSING relative to the model's predicted direction; combined score=100*max(0,supports+0.5*neutral)/denom - 15*warns - 20*opposes, clipped [0,100]
- **output column(s)**: market_support_score (per-event)
- **leakage risk**: NONE - every input is a t0-or-earlier master/prediction column

### compute_ml_prob_trust_score
- **source**: `model_probs_trust_tab.py` lines 866-972
- **source columns**: live wiring/alignment flags, validation pass/fail flags, recent matured performance, calibration error, market_support_score
- **rolling/bar window**: n/a / n/a
- **z-score window**: n/a  |  **percentile window**: n/a
- **session/day reset**: n/a / n/a
- **closed vs forming**: n/a - THIS IS A LIVE OPERATIONAL HEALTH SCORE, not a per-bar/per-event feature
- **normalization**: weighted combination (wiring+validation+performance+calibration+market_support) with hard caps on data-quality red flags
- **output column(s)**: trust_score, trust_state (NOT used as a training feature anywhere in this engine)
- **leakage risk**: N/A - excluded from the feature panel entirely because it answers 'is the CURRENT production deployment healthy right now', not a property of any individual historical bar; including it as a per-row training feature would be meaningless (every historical row would just get today's single live health score)
