# Audit 5A — Retrospective Shadow Report
**Generated**: 2026-07-04T04:35:05.297275+00:00
**Candidate**: Version B, W=20, threshold=0.65, H=10 primary
**SHADOW / RESEARCH ONLY**

---

## Summary

| Metric | Value |
|--------|-------|
| N signals (≥0.65) | 43,862 |
| N LONG | 17,717 |
| N SHORT | 26,145 |
| Trading days | 18 |
| Avg signals/day | 2436.8 |
| Avg net H10 | +45.9824 t |
| Total net H10 | +2016880.0 t |
| Win rate H10 | 0.624 |
| Max drawdown H10 | -135125.0 t |
| Avg net H40 | +156.2322 t |
| Win rate H40 | 0.674 |
| Max drawdown H40 | -205549.0 t |

---

## Session Breakdown

| Session | N signals | Avg net H10 | Win rate | Total net H10 |
|---------|-----------|-------------|----------|----------------|
| Asia | 5,791 | +105.8403 | 0.698 | +612921.0 |
| EU | 3,787 | +39.8894 | 0.631 | +151061.0 |
| US_AM | 14,418 | +32.6894 | 0.591 | +471316.0 |
| US_Late | 2,328 | +134.1954 | 0.715 | +312407.0 |
| US_Open | 7,501 | +46.6630 | 0.634 | +350019.0 |
| US_PM | 10,037 | +11.8717 | 0.596 | +119156.0 |

---

## Volatility Regime Breakdown

| Regime | N signals | Avg net H10 | Win rate |
|--------|-----------|-------------|----------|
| HIGH_VOL | 21,989 | +80.3412 | 0.669 |
| LOW_VOL | 21,873 | +11.4414 | 0.579 |

---

## Probability Bucket Breakdown

| Bucket | N signals | Avg net H10 | Avg net H40 | Win rate H10 |
|--------|-----------|-------------|-------------|-------------|
| 0.50-0.55 | 4,896 | +10.2335 | -1.1176 | 0.504 |
| 0.55-0.60 | 6,302 | +15.0411 | +81.1081 | 0.501 |
| 0.60-0.65 | 6,104 | +8.2860 | +51.7369 | 0.526 |
| 0.65-0.70 | 4,739 | +33.2022 | +119.7364 | 0.568 |
| 0.70+ | 39,123 | +47.5305 | +160.6530 | 0.630 |

---

## Pass Conditions

- [OK] avg_net_h10 > 0
- [OK] win_rate > 0.50
- [OK] ≥5/6 sessions profitable
- [OK] rel_drawdown < 20% (actual=6.7%)
- [OK] ≥3 signals/day

---

## DECISION: **PASS_TO_FORWARD_SHADOW**

**PASS_TO_FORWARD_SHADOW** → proceed to live forward shadow monitoring.
**DEFER** → insufficient signal frequency or minor drawdown concern; re-evaluate.
**KILL** → net negative expectancy after cost; do not shadow.

---

## Final Status
```
AUDIT_5A_COMPLETE:    true
DECISION:             PASS_TO_FORWARD_SHADOW
TRADING_ENABLED:      false
PRODUCTION_MODIFIED:  false
```