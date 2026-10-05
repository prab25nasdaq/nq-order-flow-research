"""
01_dashboard_formula_catalog.py - Part A formula catalog.

Catalogs every quantitative formula found by direct line-by-line reading of:
  ofi_live_dashboard_WORKING_NEXT_with_logreg.py  (5,919 lines)
  model_probs_trust_tab.py                          (MODEL PROBS tab)
Source line ranges are cited per row. The OFI LEVEL DECISION and LEVEL-
REACTION SHADOW MODEL families are catalogued in Part B
(02_ofi_level_decision_catalog.py) and are cross-referenced here, not
duplicated.

No formula below was invented - every row's "formula" column is copied
verbatim (as a textual description of the actual code) from the cited
source lines, and v3_common.py's implementations are line-for-line
translations of that same code.

Writes: dashboard_formula_catalog.csv, dashboard_formula_catalog.md

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v3_common as v3

DASH = "ofi_live_dashboard_WORKING_NEXT_with_logreg.py"
MPT = "model_probs_trust_tab.py"

CATALOG = [
    dict(family="Z-SCORES", formula_name="residual_z",
         source_file=DASH, source_lines="455-460",
         source_columns="mid_mean; delta_norm; volatility_5; sweep_imbalance_norm; vpin",
         rolling_window=20, bar_window="20 bars", z_score_window=20, percentile_window="n/a",
         session_reset_rule="none (continuous across session boundary)",
         day_reset_rule="none (continuous across day boundary)",
         closed_or_forming="closed-bar master rows only (dashboard reads master ndjsonl, which only logs closed bars)",
         normalization_rule="(x - rolling_mean(w=20)) / rolling_std(w=20, ddof=1); min_periods=20 STRICT (no partial window)",
         output_column_name="mid_resid_z, delta_norm_resid_z20, volatility_5_resid_z20, sweep_imbalance_norm_resid_z20, vpin_resid_z20",
         leakage_risk="NONE - rolling window is backward-looking only (pandas default, not centered)"),
    dict(family="Z-SCORES / Q-SCIENCE", formula_name="_causal_z",
         source_file=DASH, source_lines="858-864",
         source_columns="return r; delta_norm; ofi (mlofi_norm); sweep_imbalance_norm",
         rolling_window=128, bar_window="128 bars (64 for price/flow slope variants)",
         z_score_window=128, percentile_window="n/a",
         session_reset_rule="none", day_reset_rule="none",
         closed_or_forming="closed-bar only",
         normalization_rule="(x - rolling_mean(w).shift(1)) / rolling_std(w).shift(1); min_ref=30 (20 for slope variants); EXTRA shift(1) beyond residual_z means bar t's own value never enters its own mean/std",
         output_column_name="ret_z (regime codes), delta_z, ofi_z, sweep_z, price_slope_z, flow_slope_z",
         leakage_risk="NONE - causal by construction (explicit .shift(1))"),
    dict(family="REGIME BREAKS", formula_name="_cusum_breaks",
         source_file=DASH, source_lines="925-940",
         source_columns="log-return r (derived from mid_mean/mid_kf/px_close)",
         rolling_window="EWM halflife=64 (dynamic vol threshold)", bar_window="unbounded EWM (no fixed window)",
         z_score_window="n/a (EWM-standardized, not a fixed rolling window)", percentile_window="n/a",
         session_reset_rule="none", day_reset_rule="none (s_plus/s_minus accumulate across day boundaries in the dashboard's own usage)",
         closed_or_forming="closed-bar only",
         normalization_rule="z=(r-ewm_mean(hl=64))/ewm_std(hl=64); standard CUSUM filter, drift k=0.35, barrier h=5.0, resets to 0 on break",
         output_column_name="cusum_up_break, cusum_down_break (also present natively in master ndjsonl - same formula family)",
         leakage_risk="NONE - EWM and cumulative sum are strictly backward-looking. THIS ENGINE reproduces the dashboard's cross-day accumulation EXACTLY (s_plus/s_minus are NOT reset at session/day boundaries, matching source behavior) - see Part D"),
    dict(family="REGIME BREAKS", formula_name="_csw_scores (Chu-Stinchcombe-White)",
         source_file=DASH, source_lines="943-961",
         source_columns="log-mid y",
         rolling_window="expanding, capped at max_anchor=250 bars back", bar_window="up to 250 bars",
         z_score_window="n/a", percentile_window="n/a (raw score vs analytic critical value 4.6+log(gap))",
         session_reset_rule="none", day_reset_rule="none",
         closed_or_forming="closed-bar only",
         normalization_rule="max over anchors of |y[t]-y[anchor]| / (sigma[t]*sqrt(gap)); sigma=expanding RMS of first differences",
         output_column_name="csw_score, csw_crit, csw_pct (rolling_pct of csw_score)",
         leakage_risk="NONE - anchors are strictly t-1 and earlier"),
    dict(family="REGIME BREAKS", formula_name="_sample_heavy_scores (SADF/SMT proxy)",
         source_file=DASH, source_lines="999-1071",
         source_columns="log-mid y",
         rolling_window="SADF: wmin=50,wmax=500,step=10; SMT: wmin=32,wmax=500,step=8",
         bar_window="50-500 bars", z_score_window="n/a",
         percentile_window="historical re-scan, start=max(60,len-350), step=12",
         session_reset_rule="none", day_reset_rule="none",
         closed_or_forming="closed-bar only; COMPUTED IN A BACKGROUND THREAD in the live dashboard (expensive), cached per (vol_type,window_bars,n,first_ts,last_ts) key",
         normalization_rule="max ADF/SMT t-stat over a window grid, normalized to a historical percentile",
         output_column_name="sadf_up, sadf_down, sadf_pct, smt_up, smt_down, smt_pct",
         leakage_risk="NONE for the per-bar score itself (window only ever looks backward), but EXCLUDED from this engine's training feature panel: too computationally expensive to run at full historical density (5-10 min per call in the live dashboard) for the ~6,000-bar history here - documented scope exclusion, not a leakage issue"),
    dict(family="ENTROPY", formula_name="_quantile_codes + _rolling_abs_quantile (state-code inputs)",
         source_file=DASH, source_lines="826-855",
         source_columns="delta_norm; ofi (mlofi_norm); sweep_imbalance_norm; spread",
         rolling_window=256, bar_window="256 bars", z_score_window="n/a",
         percentile_window="256-bar quantile breakpoints (k=5 buckets) / 256-bar abs-quantile deadband (q=0.60)",
         session_reset_rule="none", day_reset_rule="none",
         closed_or_forming="closed-bar only",
         normalization_rule="digitize(v, quantile_breakpoints(prior 256 bars)); deadband sign via rolling abs 60th percentile (floor 0.05)",
         output_column_name="delta_codes, ofi_codes, sweep_codes, spread_codes, flow_joint_codes",
         leakage_risk="NONE - breakpoints computed from bars STRICTLY BEFORE i (ref = vals[i-window:i], excludes i)"),
    dict(family="ENTROPY", formula_name="_rolling_entropy + composite entropy_score",
         source_file=DASH, source_lines="803-823, 2765-2782",
         source_columns="ret_codes(k=5), delta_codes(k=5), ofi_codes(k=5), sweep_codes(k=3), spread_codes(k=4), flow_joint_codes(k=27)",
         rolling_window=128, bar_window="128 bars (trailing, INCLUSIVE of current bar)",
         z_score_window="n/a", percentile_window="n/a",
         session_reset_rule="none", day_reset_rule="none",
         closed_or_forming="closed-bar only",
         normalization_rule="Shannon entropy / log(k), normalized [0,1]; composite = 0.35*return + 0.35*flow_joint + 0.15*spread + 0.15*mean(delta,ofi,sweep); min_n=max(8,min(window//4,32))=32",
         output_column_name="entropy_score, entropy_return, entropy_delta, entropy_ofi, entropy_sweep, entropy_spread, entropy_flow",
         leakage_risk="LOW - the 128-bar entropy window INCLUDES the current bar's own code (not lagged), unlike the causal_z/quantile_codes inputs feeding it. This is the dashboard's own live-display convention; this engine's feature panel keeps it as-is for parity but flags it explicitly since a model feature including bar t's own discretized state is a one-bar look-VERY-slightly-forward in the sense that t's code does help define t's own entropy bucket weighting (not the underlying market data itself, which remains causal) - low materiality, documented not hidden"),
    dict(family="FLOW TOXICITY / Q-SCIENCE", formula_name="flow_score / flow_score_raw / flow_divergence",
         source_file=DASH, source_lines="2784-2799",
         source_columns="delta_z, ofi_z, sweep_z (each _causal_z(window=128)); return r (_causal_z(window=64))",
         rolling_window="128 (z inputs), 64 (slope), 5 (EMA span)", bar_window="varies by component",
         z_score_window=128, percentile_window="n/a",
         session_reset_rule="none", day_reset_rule="none",
         closed_or_forming="closed-bar only",
         normalization_rule="flow_score_raw=0.40*tanh(delta_z/2)+0.35*tanh(ofi_z/2)+0.25*tanh(sweep_z/2); flow_score=EMA(span=5) of that; flow_divergence=causal_z(r,64)-causal_z(diff(flow_score),64)",
         output_column_name="flow_score, flow_alignment, flow_direction, flow_divergence, flow_consistency",
         leakage_risk="NONE - all inputs causal"),
    dict(family="FLOW TOXICITY", formula_name="vpin / vpin_pct / vpin_state",
         source_file=DASH, source_lines="2801-2812",
         source_columns="vpin (native master column if present); else buy_vol/sell_vol",
         rolling_window="native vpin: production parser's own window (external); fallback: 75",
         bar_window="75 bars (fallback only)", z_score_window="n/a",
         percentile_window=500,
         session_reset_rule="none", day_reset_rule="none",
         closed_or_forming="closed-bar only",
         normalization_rule="vpin_pct=rolling_pct(vpin,window=500); state: NORMAL<0.70, ELEVATED<0.90, TOXIC<0.95, else EXTREME_TOXICITY",
         output_column_name="vpin, vpin_pct, vpin_state",
         leakage_risk="NONE"),
    dict(family="LIQUIDITY COST", formula_name="rolling_kyle (Kyle's lambda)",
         source_file=DASH, source_lines="1074-1087",
         source_columns="mid; order-flow imbalance q=buy_vol-sell_vol (LAGGED 1 bar)",
         rolling_window=160, bar_window=160, z_score_window="n/a", percentile_window="500 (kyle_pct)",
         session_reset_rule="none", day_reset_rule="none",
         closed_or_forming="closed-bar only",
         normalization_rule="rolling_cov(dp[t], q[t-1]) / rolling_var(q[t-1]); min_periods=max(20,window//4)=40",
         output_column_name="kyle, kyle_pct",
         leakage_risk="NONE - q is explicitly lagged 1 bar before regression"),
    dict(family="LIQUIDITY COST", formula_name="amihud illiquidity",
         source_file=DASH, source_lines="2817-2818",
         source_columns="|dp| (abs price change); dollar_vol=mid*volume",
         rolling_window=160, bar_window=160, z_score_window="n/a", percentile_window=500,
         session_reset_rule="none", day_reset_rule="none",
         closed_or_forming="closed-bar only",
         normalization_rule="rolling_median(160, min_periods=30) of |dp|/dollar_vol",
         output_column_name="amihud, amihud_pct",
         leakage_risk="NONE"),
    dict(family="LIQUIDITY COST", formula_name="roll_measure / roll_impact",
         source_file=DASH, source_lines="1090-1094, 2819-2821",
         source_columns="dp (price change)",
         rolling_window=160, bar_window=160, z_score_window="n/a", percentile_window=500,
         session_reset_rule="none", day_reset_rule="none",
         closed_or_forming="closed-bar only",
         normalization_rule="2*sqrt(max(0,-rolling_cov(dp,dp.shift(1)))); roll_impact=roll/rolling_mean(dollar_vol,160)",
         output_column_name="roll, roll_impact, roll_pct",
         leakage_risk="NONE"),
    dict(family="LIQUIDITY COST", formula_name="corwin_schulz spread estimator",
         source_file=DASH, source_lines="1097-1113",
         source_columns="px_high, px_low (current + 1-bar-lagged)",
         rolling_window="2 bars (closed-form, no fixed window)", bar_window=2, z_score_window="n/a",
         percentile_window=500,
         session_reset_rule="none", day_reset_rule="none",
         closed_or_forming="closed-bar only",
         normalization_rule="Corwin & Schulz (2012) high-low spread estimator, beta/gamma/alpha closed form",
         output_column_name="cs_spread, cs_spread_pct",
         leakage_risk="NONE - uses bar t and t-1 only"),
    dict(family="LIQUIDITY COST / FLOW TOXICITY", formula_name="toxicity / liquidity_cost composites",
         source_file=DASH, source_lines="2851-2855",
         source_columns="vpin_pct, spread_pct, kyle_pct, amihud_pct, roll_pct, cs_spread_pct",
         rolling_window="n/a (combination of already-rolling inputs)", bar_window="n/a",
         z_score_window="n/a", percentile_window="n/a",
         session_reset_rule="none", day_reset_rule="none",
         closed_or_forming="closed-bar only",
         normalization_rule="toxicity=clip(0.45*vpin_pct+0.20*spread_pct+0.15*kyle_pct+0.10*amihud_pct+0.10*roll_pct,0,1); liquidity_cost=clip(0.35*spread_pct+0.25*roll_pct+0.25*amihud_pct+0.15*cs_spread_pct,0,1)",
         output_column_name="toxicity, liquidity_cost",
         leakage_risk="NONE (inherits NONE from all inputs)"),
    dict(family="REGIME BREAKS", formula_name="break_score / break_age / market_state",
         source_file=DASH, source_lines="2839-2879",
         source_columns="cusum up/down, csw_pct, sadf_pct, smt_pct, toxicity, spread_pct, entropy_score, flow_alignment",
         rolling_window="n/a (point-in-time composite of already-rolling inputs)", bar_window="n/a",
         z_score_window="n/a", percentile_window="n/a",
         session_reset_rule="none", day_reset_rule="none",
         closed_or_forming="closed-bar only",
         normalization_rule="break_score=max(cusum_event?1:0, csw_pct, sadf_pct, smt_pct); break_age=bars since last break; market_state in {EXPLOSIVE MOVE, TOXIC FLOW, REGIME BREAK, CHOP/RANDOM, CLEAN TREND, NO EDGE} via threshold rules on the above",
         output_column_name="break_score, break_age, market_state, direction",
         leakage_risk="NONE for break_score/break_age; sadf_pct/smt_pct excluded from this engine's panel (see above) so market_state is reproduced WITHOUT the SADF/SMT branches - documented partial parity, see dashboard_feature_parity_report.csv"),
    dict(family="Q-SCIENCE", formula_name="_hurst_rs (R/S Hurst exponent)",
         source_file=DASH, source_lines="687-715",
         source_columns="log-price",
         rolling_window="full available window up to call time (R/S over geomspace(10,80,12) lags)",
         bar_window="variable (lag-dependent)", z_score_window="n/a", percentile_window="n/a",
         session_reset_rule="none", day_reset_rule="none",
         closed_or_forming="closed-bar only",
         normalization_rule="slope of log(R/S) vs log(lag), clipped [0.01,0.99]",
         output_column_name="hurst (display-only in the dashboard's Q-SCIENCE tab)",
         leakage_risk="EXCLUDED from this engine's feature panel - the dashboard calls this on the FULL visible window each redraw (not a true expanding causal series); reproducing a leakage-free per-bar Hurst would require redesigning the call pattern, which is out of scope here and documented as such rather than silently approximated"),
    dict(family="Q-SCIENCE / MICROSTRUCTURE", formula_name="_adx_series (Wilder ADX)",
         source_file=DASH, source_lines="718-740",
         source_columns="px_high, px_low, px_close",
         rolling_window=14, bar_window=14, z_score_window="n/a", percentile_window="n/a",
         session_reset_rule="none", day_reset_rule="none",
         closed_or_forming="closed-bar only",
         normalization_rule="Wilder EWM (alpha=1/14) smoothed +DI/-DI/ADX, standard formula",
         output_column_name="adx, plus_di, minus_di",
         leakage_risk="NONE - ewm(adjust=False) is strictly causal"),
    dict(family="Q-SCIENCE", formula_name="lead/lag Spearman correlation panel",
         source_file=DASH, source_lines="676-684, ~2100-2184",
         source_columns="various signals vs forward return at several lag horizons",
         rolling_window="full visible window (N bars)", bar_window="variable",
         z_score_window="n/a", percentile_window="n/a",
         session_reset_rule="none", day_reset_rule="none",
         closed_or_forming="closed-bar only",
         normalization_rule="rank correlation (argsort-of-argsort, no scipy) of signal[t] vs return[t+lag]",
         output_column_name="display heatmap only - NOT a per-bar feature in the dashboard (it is a SUMMARY panel over the visible window), therefore not reproduced as a row-level feature here; this engine's own Part D/feature-IC work (re-used from the v2 engine's rolling-IC methodology) supersedes it with a genuinely causal, no-lookahead per-bar version",
         leakage_risk="N/A - not used as a per-row feature; the dashboard's OWN version uses forward returns directly in a window that includes 'future' bars relative to the window start, which is fine for a DISPLAY summary but would leak if naively used per-row, which is exactly why it is not reproduced row-wise here"),
    dict(family="ORDER FLOW", formula_name="bull/bear pressure composite",
         source_file=DASH, source_lines="2186-2256",
         source_columns="delta_norm, sweep_imbalance_norm, buy_vol/sell_vol, vpin, DOM depth history, absorption proxy",
         rolling_window="80 (_pctrank default) per component", bar_window=80,
         z_score_window="n/a", percentile_window=80,
         session_reset_rule="none", day_reset_rule="none",
         closed_or_forming="closed-bar only (DOM-depth component additionally needs live depth history, EXCLUDED here - not reconstructable from historical master/book-flow files)",
         normalization_rule="mean of per-component rolling percentile ranks (window=80), then EMA(span=8)",
         output_column_name="bull_pressure, bear_pressure",
         leakage_risk="NONE for the reproduced components (buy_delta, buy_sweep, bid_liquidity proxy, vpin_inv, absorption); DOM-depth component dropped (live-only data, not a leakage issue)"),
    dict(family="MICROSTRUCTURE", formula_name="swing_levels / compute_sr_levels",
         source_file=DASH, source_lines="570-624",
         source_columns="px_high, px_low",
         rolling_window="2*lb+1 (lb=3 chart default, lb=4 S/R-panel default)",
         bar_window="7-9 bars (CENTERED)", z_score_window="n/a", percentile_window="n/a",
         session_reset_rule="none", day_reset_rule="none",
         closed_or_forming="closed-bar only",
         normalization_rule="pandas rolling(window, center=True).max()/.min() swing-high/low detection, then price-clustered (cluster_dist=2.0 pts) and scored by touch-count x recency",
         output_column_name="swing highs/lows, sr_levels (chart overlay only)",
         leakage_risk="HIGH - center=True means the swing label at bar i depends on bars BOTH before AND after i (i+lb). EXCLUDED from this engine's feature panel entirely for this reason; this engine's own S/R gate (Part E) uses the book-flow cache's HVN/LVN/POC/VAH/VAL fields and a causal/de-leaked rolling-level construction instead, never this centered swing detector"),
    dict(family="MICROSTRUCTURE", formula_name="detect_absorptions",
         source_file=DASH, source_lines="627-646",
         source_columns="buy_vol, sell_vol, vol_total, px_open, px_close",
         rolling_window="full visible window (75th percentile of vol_total computed over it)",
         bar_window="variable", z_score_window="n/a", percentile_window=75,
         session_reset_rule="none", day_reset_rule="none",
         closed_or_forming="closed-bar only",
         normalization_rule="high-volume (>=p75) bar with one-sided flow (buy or sell ratio>=0.60) closing AGAINST that flow's direction (down on buy-dominant, up on sell-dominant)",
         output_column_name="absorption flags (chart overlay; also feeds bull/bear pressure 'absorption' component above)",
         leakage_risk="LOW - the 75th-percentile threshold in the LIVE dashboard is computed over the FULL visible window (could include bars after t); this engine's feature panel recomputes it as a trailing rolling percentile instead (documented deviation, made MORE conservative/causal than the source, not less)"),
    dict(family="MODEL PROBS", formula_name="compute_market_support_factors",
         source_file=MPT, source_lines="456-543",
         source_columns="mlofi_norm, mlofi_rolling_5, decay_norm, delta_norm, *_resid_z20, entropy_score, vpin, bar_duration_s, sweep_norm, cusum_up_break, cusum_down_break, regime_ic_delta, regime_ic_mlofi, prev_wk_h40_ic, reaction_type, dist_to_level_ticks",
         rolling_window="n/a (point-in-time factor combination of already-rolling master columns)", bar_window="n/a",
         z_score_window="n/a", percentile_window="n/a",
         session_reset_rule="none", day_reset_rule="none",
         closed_or_forming="closed-bar only",
         normalization_rule="8 factors each scored SUPPORTS_LONG/SUPPORTS_SHORT/NEUTRAL/WARNS_CHOP/WARNS_TOXIC/MISSING relative to the model's predicted direction; combined score=100*max(0,supports+0.5*neutral)/denom - 15*warns - 20*opposes, clipped [0,100]",
         output_column_name="market_support_score (per-event)",
         leakage_risk="NONE - every input is a t0-or-earlier master/prediction column"),
    dict(family="MODEL PROBS", formula_name="compute_ml_prob_trust_score",
         source_file=MPT, source_lines="866-972",
         source_columns="live wiring/alignment flags, validation pass/fail flags, recent matured performance, calibration error, market_support_score",
         rolling_window="n/a", bar_window="n/a", z_score_window="n/a", percentile_window="n/a",
         session_reset_rule="n/a", day_reset_rule="n/a",
         closed_or_forming="n/a - THIS IS A LIVE OPERATIONAL HEALTH SCORE, not a per-bar/per-event feature",
         normalization_rule="weighted combination (wiring+validation+performance+calibration+market_support) with hard caps on data-quality red flags",
         output_column_name="trust_score, trust_state (NOT used as a training feature anywhere in this engine)",
         leakage_risk="N/A - excluded from the feature panel entirely because it answers 'is the CURRENT production deployment healthy right now', not a property of any individual historical bar; including it as a per-row training feature would be meaningless (every historical row would just get today's single live health score)"),
]

cat_df = pd.DataFrame(CATALOG)
cat_df.to_csv(v3.OUT_DIR / "dashboard_formula_catalog.csv", index=False)

md_lines = ["# Dashboard Formula Catalog (Part A)", "",
            "Extracted by direct line-by-line reading of the dashboard source files listed below.",
            "No formula was invented; every entry cites exact source lines.", "",
            f"- {DASH}", f"- {MPT}", f"- ofi_level_decision_tab.py (catalogued separately in Part B)",
            f"- level_reaction_dashboard_tab.py (a predictions.csv VIEWER, no new formulas beyond what is already documented for the level-reaction model elsewhere in this codebase)",
            ""]
for fam in cat_df["family"].unique():
    md_lines.append(f"## {fam}")
    md_lines.append("")
    for _, row in cat_df[cat_df["family"] == fam].iterrows():
        md_lines.append(f"### {row['formula_name']}")
        md_lines.append(f"- **source**: `{row['source_file']}` lines {row['source_lines']}")
        md_lines.append(f"- **source columns**: {row['source_columns']}")
        md_lines.append(f"- **rolling/bar window**: {row['rolling_window']} / {row['bar_window']}")
        md_lines.append(f"- **z-score window**: {row['z_score_window']}  |  **percentile window**: {row['percentile_window']}")
        md_lines.append(f"- **session/day reset**: {row['session_reset_rule']} / {row['day_reset_rule']}")
        md_lines.append(f"- **closed vs forming**: {row['closed_or_forming']}")
        md_lines.append(f"- **normalization**: {row['normalization_rule']}")
        md_lines.append(f"- **output column(s)**: {row['output_column_name']}")
        md_lines.append(f"- **leakage risk**: {row['leakage_risk']}")
        md_lines.append("")

with open(v3.OUT_DIR / "dashboard_formula_catalog.md", "w") as f:
    f.write("\n".join(md_lines))

v3.log(f"01 complete: {len(cat_df)} formulas catalogued across {cat_df['family'].nunique()} families")
print(f"FORMULAS_CATALOGUED: {len(cat_df)}")
print(f"FAMILIES: {sorted(cat_df['family'].unique().tolist())}")
print(f"HIGH_LEAKAGE_RISK_COUNT: {(cat_df['leakage_risk'].str.startswith('HIGH')).sum()}")
