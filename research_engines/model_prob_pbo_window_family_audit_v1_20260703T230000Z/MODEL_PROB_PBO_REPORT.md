# MODEL-PROBABILITY PBO — WINDOW FAMILY AUDIT v1
**Generated**: 2026-07-03T23:18:57.240623+00:00
**Runtime**: 346.8 seconds
**SHADOW / RESEARCH ONLY — no execution, no broker, no order placement**
**Method**: Bailey et al. (2015) CSCV on OOS model probabilities

---

## Setup

- **Source model**: level_reaction_continuous_nq_shadow (HGB, target=label_h40)
- **Feature count (Version A)**: 77 base features
- **Feature count (Version B)**: 81 (base + 4 deferred)
- **Window variants tested**: [5, 10, 20, 30, 50, 80]
- **Walk-forward folds**: 18 expanding-window calendar-day splits
- **Embargo**: 40 bars
- **Cost model**: 2.0 ticks round-trip per trade
- **CSCV**: S=16 subperiods → C(16,8)=12,870 combinations

---

## What 'window' means

The rolling window W controls the lookback for all bar-level statistics:
- `delta_rolling_W`, `mlofi_rolling_W` — rolling mean of OFI/flow features
- `volatility_W` — rolling std of per-bar return
- `mlofi_accel` — change in mlofi_rolling over 1 bar (window-parameterized)
- `*_resid_z{W}` — z-score residuals of delta_norm, vpin, sweep_imbalance, volatility
- OHLC-vol features — normalized by `volatility_W`

Current production shadow uses W=20. This audit tests W ∈ [5, 10, 20, 30, 50, 80].
**Kill criterion**: If only 1 window variant shows positive edge_perf,
the result is a lucky bar-construction artifact, not a durable market effect.

---

## CSCV Results — Model-Probability PBO

Performance metric: `edge_perf = (prob_long - 0.5) × label_direction`
Positive = model confidence points in the correct direction.

| Version | N_windows | Family PBO | Logit mean | Prob(loss) | Degradation β |
|---------|-----------|-----------|------------|------------|----------------|
| A (base)    | 6 | **0.722** | -0.243 | 0.001 | -0.916 |
| B (base+tox)| 6 | **0.329** | +0.393 | 0.000 | -0.869 |

PBO < 0.05 = strong  |  < 0.20 = acceptable  |  < 0.40 = marginal  |  ≥ 0.60 = overfit → KILL

---

## Per-Window OOS Performance

| Version | Window | Edge perf mean | Positive? | Calibrated (H=10)? |
|---------|--------|---------------|-----------|-------------------|
| A | W=  5 | +0.088932 | YES | YES |
| A | W= 10 | +0.091737 | YES | NO |
| A | W= 20 | +0.094438 | YES | NO |
| A | W= 30 | +0.092388 | YES | NO |
| A | W= 50 | +0.074123 | YES | NO |
| A | W= 80 | +0.063757 | YES | NO |
| B | W=  5 | +0.086990 | YES | NO |
| B | W= 10 | +0.094733 | YES | NO |
| B | W= 20 | +0.091859 | YES | NO |
| B | W= 30 | +0.098164 | YES | NO |
| B | W= 50 | +0.076454 | YES | NO |
| B | W= 80 | +0.056106 | YES | YES |

---

## Probability Calibration Summary

Calibrated = Spearman ρ(bucket_midpoint, hit_rate) > 0 with p < 0.20
Buckets: [0.50-0.55), [0.55-0.60), [0.60-0.65), [0.65-0.70), [0.70+]

| Version | Window | H=10 calibrated | H=40 calibrated | ρ (H=10) |
|---------|--------|----------------|----------------|---------|
| A | W=  5 | YES | YES | +0.700 |
| A | W= 10 | NO | NO | +0.300 |
| A | W= 20 | NO | NO | +0.500 |
| A | W= 30 | NO | NO | +0.400 |
| A | W= 50 | NO | NO | +0.100 |
| A | W= 80 | NO | NO | +0.300 |
| B | W=  5 | NO | NO | +0.600 |
| B | W= 10 | NO | NO | -0.100 |
| B | W= 20 | NO | NO | +0.200 |
| B | W= 30 | NO | NO | +0.200 |
| B | W= 50 | NO | NO | +0.400 |
| B | W= 80 | YES | YES | +0.700 |

---

## Decisions

Decision criteria:
- **PROMOTE_TO_SHADOW**: family_PBO < 0.40 AND ≥4 windows positive AND
  calibration OK (≥half windows monotone) AND best_net_h10 > 0
- **DEFER_RESEARCH**: family_PBO < 0.60 AND ≥2 windows positive AND
  ≥1 window calibrated (not all broken)
- **KILL**: family_PBO ≥ 0.60 OR only_one_window OR no_calibration

Critical kill conditions (from specification):
- family_PBO ≥ 0.60 → configuration selection is overfit
- Only 1 window works → lucky bar construction, not durable market effect
- No calibration → model probabilities do not rank-order outcomes
- Net expectancy disappears after costs → no edge

| Version | PBO | Windows+ | Calibrated | Best net H10 | Decision |
|---------|-----|----------|-----------|--------------|----------|
| A (base) | 0.722 | 6/6 | 1/6 | +37.433t | **KILL** |
| B (+tox) | 0.329 | 6/6 | 1/6 | +40.063t | **DEFER_RESEARCH** |

**Version A KILL reason**: family_PBO>=0.60
**Version B DEFER_RESEARCH**: Borderline result. Requires longer OOS sample and session/regime stability confirmation.

---

## Deferred Signal Status (from signal-level PBO)

The 4 deferred signals in Version B are:
| Signal | Signal-level PBO verdict | In Version B? |
|--------|-------------------------|--------------|
| buy_toxicity | DEFER (window/session stable, DSR too low vs N=97K) | YES |
| sell_toxicity | DEFER (window/session stable) | YES |
| signed_vpin_delta | DEFER (window/session stable) | YES |
| cur_vpin_pct_L500_signed | DEFER (window/session stable) | YES |

Version B tests whether these deferred signals improve model-probability
quality on top of the base v3 feature set.

---

## Output Files

| File | Part | Description |
|------|------|-------------|
| LOCK_MANIFEST.json | A | v3 master config hash lock |
| oos_probability_table.parquet | G | Full OOS prob table (all windows, both versions) |
| cscv_per_window_results.csv | E | CSCV per-window edge_perf and family PBO |
| calibration_by_window.csv | F | Prob calibration per window / horizon / bucket |
| model_prob_pbo_decisions.csv | H | Final decisions |
| MODEL_PROB_PBO_REPORT.md | I | This report |

---

## Final Status
```
PRODUCTION_FILES_MODIFIED:              false
DASHBOARD_CODE_MODIFIED:                false
FEATURE_MASTER_CODE_MODIFIED:           false
ACTIVE_MODEL_POINTER_CHANGED:           false
TRADING_ENABLED:                        false
BROKER_CONNECTED:                       false
PAPER_TRADING_ENABLED:                  false
WINDOWS_TESTED:                         [5, 10, 20, 30, 50, 80]
FOLDS:                                  18
COST_TICKS:                             2.0
VERSION_A_DECISION:                     KILL
VERSION_B_DECISION:                     DEFER_RESEARCH
RUNTIME_SECONDS:                        346.8
OVERALL:                                PASS
```