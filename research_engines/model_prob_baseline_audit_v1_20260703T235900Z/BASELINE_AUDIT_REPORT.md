# Audit 3 — Dumb Baseline / Naive Model Audit v1
**Generated**: 2026-07-03T23:21:23.273168+00:00
**SHADOW / RESEARCH ONLY**

---

## v3 Reference (Version A, W=20, H=10)
Net return H10 = +32.6557 ticks (OOS average)

---

## Baseline Comparison

| H | Baseline | Net return (ticks) | Beats v3? |
|---|----------|-------------------|-----------|
| 10 | always_long | -24.8778 | no |
| 10 | always_short | +20.8778 | no |
| 10 | majority | +20.8778 | no |
| 10 | random | +1.2194 | no |
| 10 | session_only | -12.1764 | no |
| 10 | regime_only | +20.8778 | no |
| 10 | tod_only | +23.0478 | no |
| 10 | prev_bar_dir | -3.7613 | no |
| 40 | always_long | -39.9301 | no |
| 40 | always_short | +35.9301 | no |
| 40 | majority | +35.9301 | no |
| 40 | random | +3.8655 | no |
| 40 | session_only | -5.9020 | no |
| 40 | regime_only | +35.9301 | no |
| 40 | tod_only | +46.2333 | no |
| 40 | prev_bar_dir | -4.2105 | no |

---

## Decision

**BASELINE PASS** — v3 beats all 16 dumb baselines after cost.

Key: if session-only or TOD-only beats v3 → model is not learning microstructure.

---

## Final Status
```
BASELINE_AUDIT_COMPLETE: true
N_BASELINES_BEAT_V3: 0/16
SESSION_ONLY_BEATS_V3: False
TOD_ONLY_BEATS_V3: False
PRODUCTION_FILES_MODIFIED: false
TRADING_ENABLED: false
```