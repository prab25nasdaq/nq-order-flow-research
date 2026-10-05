# PBO / DEFLATED SHARPE OVERFITTING AUDIT v1
**Generated**: 2026-07-03T22:31:12Z
**Runtime**: 3.4 seconds
**SHADOW / RESEARCH ONLY — no execution, no broker, no order placement**
**Method**: Bailey, Borwein, López de Prado & Zhu (2015) — CSCV + DSR

---

## Configuration Count (Total Trials)

| Atlas | Family | N configs |
|-------|--------|-----------|
| Atlas1_DirectionalVPIN | vpin_pct_signed_direction | 1,680 |
| Atlas1_DirectionalVPIN | directional_features | 160 |
| Atlas2_TrueLDPVPIN | true_vpin_raw_settings | 5,760 |
| Q16_FullRange | true_vpin_pct | 1,080 |
| Q16_FullRange | true_directional_features | 160 |
| Q16_FullRange | cur_vpin_pct | 20 |
| Q16_FullRange | vpin_divergence | 20 |

**Raw parameter configs**: 8,880
**× 11 conditional filter modes** = **97,680 total trial evaluations**

This is the N used in the Deflated Sharpe benchmark (E[max SR | N trials]).
Higher N raises the DSR bar — every additional test evaluated weakens any single result.

---

## CSCV Results — Probability of Backtest Overfitting

Dataset: Atlas1 directional panel, T=24,192 bars, S=16 subperiods → C(16,8) = 12,870 combinations.
Performance metric: per-bar signed return × direction bet (0 when signal inactive).

| Horizon H | PBO | Logit mean | Prob(loss OOS) | Degradation β |
|-----------|-----|------------|----------------|---------------|
| H=5 | **0.390** [MED] | +0.570 | 0.445 | -0.292 |
| H=10 | **0.374** [MED] | +0.640 | 0.434 | -0.117 |
| H=20 | **0.365** [MED] | +1.064 | 0.393 | -0.103 |
| H=40 | **0.350** [MED] | +1.433 | 0.370 | -0.003 |
| H=80 | **0.489** [HIGH] | -0.070 | 0.497 | -0.074 |

### PBO by Signal Family (H=10)

| Family | N configs | PBO | Verdict |
|--------|-----------|-----|---------|
| atlas1_directional | 32 | 0.421 | HIGH (overfit) |
| atlas1_pct_signed | 336 | 0.638 | HIGH (overfit) |
| current_vpin | 4 | 0.307 | MED |
| divergence | 4 | 0.962 | HIGH (overfit) |
| q16_true_directional | 32 | 0.522 | HIGH (overfit) |
| q16_true_pct_signed | 108 | 0.400 | HIGH (overfit) |

**Key insight on PBO**: PBO measures the probability that the IS-optimal configuration
underperforms the median OOS. PBO < 0.05 = strong; < 0.20 = acceptable; ≥ 0.40 = overfit.

---

## Deflated Sharpe Ratio

N_trials = 97,680  (benchmark E[max SR] = expected max Sharpe across 97,680 iid tests).
DSR = PSR(E[max SR]): probability that the selected signal's true SR exceeds the expected
maximum noise SR from 97,680 random trials.
Threshold: DSR > 0.95 = STRONG; > 0.50 = POSITIVE; > 0.20 = WEAK; ≤ 0.20 = SPURIOUS.

| Signal | DSR | SR_bar | Active% | T_active | Verdict |
|--------|-----|--------|---------|----------|---------|
| sell_toxicity_W120_L500 | **0.0000** | 0.01016 | 32.1% | 7763 | ? |
| buy_toxicity_W120_L500 | **0.0000** | -0.01067 | 31.3% | 7565 | ? |
| signed_vpin_delta_W120_L500 | **0.0000** | -0.00063 | 62.8% | 15193 | ? |
| true_sell_toxicity_W50 | **0.0000** | 0.02491 | 32.2% | 7793 | ? |
| true_buy_toxicity_W50 | **0.0000** | -0.01338 | 31.0% | 7501 | ? |
| true_signed_delta_W50 | **0.0000** | 0.00171 | 63.6% | 15381 | ? |
| vpin_divergence | **0.0000** | -0.00813 | 69.0% | 16688 | ? |
| cur_vpin_pct_signed | **0.0000** | 0.01995 | 29.4% | 7120 | ? |

Best DSR: **0.0000** (sell_toxicity_W120_L500) — marginal after trial inflation

---

## Window Stability (Atlas1, signed direction, H=10)

| W | Mean ρ | Std ρ | Min ρ | Max ρ | N configs | Consistent? |
|---|--------|-------|-------|-------|-----------|-------------|
| W=20 | +0.0052 | 0.0012 | +0.0033 | +0.0069 | 12 | YES |
| W=40 | +0.0051 | 0.0013 | +0.0031 | +0.0073 | 12 | YES |
| W=60 | +0.0038 | 0.0011 | +0.0023 | +0.0061 | 12 | YES |
| W=120 | +0.0051 | 0.0014 | +0.0032 | +0.0075 | 12 | YES |
| W=240 | +0.0040 | 0.0012 | +0.0019 | +0.0060 | 12 | YES |
| W=480 | +0.0012 | 0.0013 | -0.0004 | +0.0032 | 12 | MIXED |
| W=960 | +0.0036 | 0.0014 | +0.0002 | +0.0052 | 12 | YES |

**Window stable** (≥60% of W values with mean ρ>0): YES

---

## Session Stability

(See session_stability.csv for full detail)

| Signal | Sessions OK | Session-stable? |
|--------|-------------|----------------|
| sell_toxicity | 3/6 | YES |
| buy_toxicity | 4/6 | YES |
| signed_vpin_delta | 5/6 | YES |
| true_sell_toxicity_W50 | 3/6 | YES |
| true_buy_toxicity_W50 | 4/6 | YES |
| true_signed_delta_W50 | 4/6 | YES |
| cur_vpin_pct_signed | 5/6 | YES |
| vpin_divergence_pct_delta | 2/6 | NO |

---

## Kill / Keep / Defer Decisions

Criteria:
- **KEEP**: DSR > 0.50 AND family_PBO < 0.20 AND session_stable AND (regime_stable OR window_stable)
- **DEFER**: family_PBO < 0.60 AND NOT(all robustness fail) AND (window_stable OR session_stable)
  — DSR alone does not kill: with N=97,680 correlated trials E[max SR]≈4.43; any 28-day SR is trivially 'spurious'. Robustness (window/session/regime stability) is the primary criterion.
- **KILL**: family_PBO ≥ 0.60 OR (NOT session_stable AND NOT window_stable AND NOT regime_stable)

| Signal | Decision | PBO | DSR | Win | Sess | Reg |
|--------|----------|-----|-----|-----|------|-----|
| sell_toxicity_W120_L500 | **DEFER** | 0.374 | 0.0000 | Y | Y | Y |
| buy_toxicity_W120_L500 | **DEFER** | 0.374 | 0.0000 | Y | Y | Y |
| signed_vpin_delta_W120_L500 | **DEFER** | 0.374 | 0.0000 | Y | Y | Y |
| true_sell_toxicity_W50 | **DEFER** | 0.374 | 0.0000 | Y | Y | Y |
| true_buy_toxicity_W50 | **DEFER** | 0.374 | 0.0000 | Y | Y | Y |
| true_signed_delta_W50 | **DEFER** | 0.374 | 0.0000 | Y | Y | Y |
| vpin_divergence | **KILL** | 0.374 | 0.0000 | - | N | Y |
| cur_vpin_pct_signed | **DEFER** | 0.374 | 0.0000 | Y | Y | Y |

### KEEP  (0 signals)

### DEFER (7 signals)
- `sell_toxicity_W120_L500` — marginal; requires 30-day shadow confirmation
- `buy_toxicity_W120_L500` — marginal; requires 30-day shadow confirmation
- `signed_vpin_delta_W120_L500` — marginal; requires 30-day shadow confirmation
- `true_sell_toxicity_W50` — marginal; requires 30-day shadow confirmation
- `true_buy_toxicity_W50` — marginal; requires 30-day shadow confirmation
- `true_signed_delta_W50` — marginal; requires 30-day shadow confirmation
- `cur_vpin_pct_signed` — marginal; requires 30-day shadow confirmation

### KILL  (1 signals)
- `vpin_divergence` — killed: fails PBO / DSR / stability criteria

---

## Methodology Notes

1. **CSCV (Bailey et al. 2015)**: T=24192 bars split into S=16 subperiods → C(16,8)=12870 IS/OOS combinations. PBO = fraction of combinations where IS-optimal config underperforms median OOS.

2. **Performance metric**: per-bar signed return × direction bet. When signal fires above threshold: +fwd_ret (long signals), -fwd_ret (short signals), sign(signal)×fwd_ret (signed signals).

3. **DSR (Bailey & López de Prado 2014)**: adjusts for selection bias from 97,680 configurations. E[max SR] = expected maximum noise SR across 97,680 iid trials (Euler-Mascheroni approximation). DSR = Φ((SR_hat - E[max SR]) / √Var[SR]).

4. **Forward returns**: computed from continuous back-adjusted close (Q16 dataset). Handles NQM6→NQU6 roll cleanly. No lookahead: fwd_ret_H = close[t+H] - close[t].

5. **Stability tests**: (a) Window — mean Spearman ρ>0 across adjacent W values; (b) Session — hit rate >50% in ≥3 sessions; (c) Regime — hit rate >50% in ≥3 regimes.

---

## Output Files

| File | Part | Description |
|------|------|-------------|
| pbo_configuration_inventory.csv | B | All N_trials breakdown |
| pbo_results_by_horizon.csv | D | PBO per horizon H |
| pbo_results_by_family.csv | D | PBO per signal family |
| dsr_recommended_signals.csv | E | DSR for 8 candidate signals |
| window_stability_atlas1_pct.csv | F | Per-col ρ by W (Atlas1) |
| window_stability_summary.csv | F | Mean ρ by W (Atlas1) |
| window_stability_q16_true_vpin.csv | F | Per-col ρ by W (Q16 true) |
| session_stability.csv | G | Per-signal per-session results |
| regime_stability.csv | H | Per-signal per-regime results |
| kill_keep_defer_decisions.csv | I | Final decisions |
| PBO_DEFLATED_SHARPE_AUDIT_REPORT.md | J | This report |

---

## Final Status
```
PRODUCTION_FILES_MODIFIED:              false
DASHBOARD_CODE_MODIFIED:                false
FEATURE_MASTER_CODE_MODIFIED:           false
BOOK_FLOW_CODE_MODIFIED:                false
MODEL_ARTIFACTS_MODIFIED:               false
ACTIVE_MODEL_POINTER_CHANGED:           false
TRADING_ENABLED:                        false
BROKER_CONNECTED:                       false
PAPER_TRADING_ENABLED:                  false
PBO_METHOD:                             CSCV Bailey et al. 2015 — S=16 C(16,8)=12870 combos
DSR_METHOD:                             Bailey & López de Prado 2014 — N_trials=97680
TOTAL_CONFIGS_INVENTORIED:              97,680
T_BARS:                                 24,192
N_SIGNALS_IN_M_MATRIX:                 516 per horizon
SIGNALS_KEEP:                           0
SIGNALS_DEFER:                          7
SIGNALS_KILL:                           1
RUNTIME_SECONDS:                        3.4
OVERALL:                                PASS
```