# Audit 8A — Flow Toxicity Side/Session Attribution
**Generated**: 2026-07-04T17:48:18.228880+00:00
**Candidate**: Version B, W=20, threshold=0.65, Rule G H10 cooldown
**SHADOW / RESEARCH ONLY — no execution, no broker, no order placement**

---

## 1. Executive Summary

| Item | Value |
|------|-------|
| Total OOS raw signals (V=B W=20 ≥0.65) | 43,862 |
| Rule G trades (H10 cd 282s) | 1,074 |
| LONG trades (Rule G) | 532 |
| SHORT trades (Rule G) | 542 |
| Total net H10 (Rule G) | +40,032 ticks |
| Best side overall | LONG |
| Best session overall | US_Late |
| Best side×session combo (Rule G) | LONG×NY |
| Worst side×session combo (Rule G) | SHORT×US_Late |
| Toxicity lift (strong vs against) | +5.20t/signal |
| V=B vs V=A avg net improvement | +0.90t/signal |
| SESSION_KILL sessions | none |
| **Recommendation** | **BOTH_SIDES_ALL_SESSIONS_FORWARD_SHADOW__TOX_SUPPORT_FILTER_OPTIONAL** |

---

## 2. Side Survival (Rule G)

| Side | N | Avg H10 | WR H10 | Total H10 | Pos-day% | Decision |
|------|---|---------|--------|-----------|----------|----------|
| LONG | 532 | +40.56t | 0.577 | +21,580t | 72.2% | SIDE_PASS |
| SHORT | 542 | +34.04t | 0.579 | +18,452t | 66.7% | SIDE_PASS |

---

## 3. Session Survival (Rule G)

| Session | N | Avg H10 | WR | LONG net | SHORT net | Best side | Decision |
|---------|---|---------|-----|----------|-----------|-----------|----------|
| Asia | 202 | +29.52t | 0.540 | -340t | +6,303t | SHORT | SESSION_DEFER |
| EU | 188 | +43.63t | 0.601 | +5,478t | +2,724t | LONG | SESSION_PASS |
| US_AM | 239 | +29.19t | 0.569 | +3,282t | +3,694t | SHORT | SESSION_PASS |
| US_Late | 108 | +95.60t | 0.620 | +10,893t | -568t | LONG | SESSION_PASS |
| US_Open | 191 | +32.30t | 0.581 | +2,829t | +3,340t | SHORT | SESSION_PASS |
| US_PM | 146 | +16.42t | 0.582 | -562t | +2,959t | SHORT | SESSION_PASS |
| NY | 684 | +37.82t | 0.583 | +16,442t | +9,425t | LONG | SESSION_PASS |

---

## 4. Side × Session Matrix (Rule G)

| Session | Side | N | Avg H10 | WR | Tox Hit% | Decision |
|---------|------|---|---------|-----|----------|----------|
| Asia | LONG | 77 | -4.42t | 0.494 | 53.2% | BLOCK |
| Asia | SHORT | 125 | +50.42t | 0.568 | 55.2% | DEFER |
| EU | LONG | 67 | +81.76t | 0.672 | 62.7% | PASS |
| EU | SHORT | 121 | +22.51t | 0.562 | 37.2% | DEFER |
| US_Open | LONG | 107 | +26.44t | 0.542 | 50.5% | DEFER |
| US_Open | SHORT | 84 | +39.76t | 0.631 | 39.3% | PASS |
| US_AM | LONG | 144 | +22.79t | 0.562 | 50.7% | PASS |
| US_AM | SHORT | 95 | +38.88t | 0.579 | 48.4% | PASS |
| US_PM | LONG | 61 | -9.21t | 0.541 | 44.3% | BLOCK |
| US_PM | SHORT | 85 | +34.81t | 0.612 | 49.4% | PASS |
| US_Late | LONG | 76 | +143.33t | 0.684 | 46.1% | PASS |
| US_Late | SHORT | 32 | -17.75t | 0.469 | 59.4% | BLOCK |
| NY | LONG | 388 | +42.38t | 0.577 | 48.7% | PASS |
| NY | SHORT | 296 | +31.84t | 0.591 | 47.3% | PASS |

---

## 5. Toxicity Support Attribution (ALL sessions, ALL sides — raw signals)

| Tox Bucket | N | Avg H10 | WR | Avg Prob |
|------------|---|---------|----|----------|
| TOX_AGAINST | 10,967 | +18.79t | 0.574 | 0.857 |
| TOX_WEAK_SUPPORT | 21,952 | +70.53t | 0.667 | 0.838 |
| TOX_STRONG_SUPPORT | 10,943 | +24.00t | 0.587 | 0.844 |

**Toxicity lift (STRONG vs AGAINST): +5.20t/signal**

---

## 6. Version A vs B Incremental (Rule G event set)

| Group | N | A avg H10 | B avg H10 | B−A | Δ WR | Conf-up N |
|-------|---|-----------|-----------|-----|------|----------|
| OVERALL | 43,862 | +45.08t | +45.98t | +0.90t | +0.003 | 20,661 |
| side_LONG | 17,717 | +43.44t | +43.89t | +0.45t | +0.003 | 8,862 |
| side_SHORT | 26,145 | +46.19t | +47.40t | +1.20t | +0.003 | 11,799 |

---

## 7. Key Day Diagnosis

### 2026-06-23
- Total H10: +628,590t  (LONG: +43,050t, SHORT: +585,540t)
- Best session: Asia  Worst: US_PM
- Mean tox support: +0.531  Tox-against signals: 1616 (25.6%)
- Best side×session: ('Asia', 'SHORT') (74.5% of day P&L)

### 2026-06-08
- Total H10: +376,389t  (LONG: +255,158t, SHORT: +121,231t)
- Best session: US_AM  Worst: Asia
- Mean tox support: -0.254  Tox-against signals: 3986 (25.5%)
- Best side×session: ('US_AM', 'LONG') (68.2% of day P&L)

### 2026-06-28
- Total H10: -135,125t  (LONG: -118,618t, SHORT: -16,507t)
- Best session: US_PM  Worst: US_Open
- Mean tox support: -0.740  Tox-against signals: 265 (29.2%)
- Best side×session: ('US_PM', 'SHORT') (5.4% of day P&L)

---

## 8. Recommendation

**BOTH_SIDES_ALL_SESSIONS_FORWARD_SHADOW__TOX_SUPPORT_FILTER_OPTIONAL**

Evidence:
- LONG: net=+21,580t, wr=0.577 → PASS
- SHORT: net=+18,452t, wr=0.579 → PASS
- Toxicity lift: +5.20t → supports filtering
- Session kills: none

---

## Output Files

- `AUDIT_8A_FLOW_TOXICITY_SIDE_SESSION_REPORT.md`
- `audit_8a_side_survival_raw.csv`
- `audit_8a_side_survival_ruleG.csv`
- `audit_8a_session_survival_raw.csv`
- `audit_8a_session_survival_ruleG.csv`
- `audit_8a_side_session_matrix_ruleG.csv`
- `audit_8a_toxicity_support_by_side_session.csv`
- `audit_8a_versionA_vs_B_incremental.csv`
- `audit_8a_top_best_ruleG_trades.csv`
- `audit_8a_top_worst_ruleG_trades.csv`
- `audit_8a_daily_side_session.csv`
- `audit_8a_jun23_jun08_jun28_diagnosis.csv`

---

## Final Status
```
AUDIT_8A_COMPLETE:             true
RECOMMENDATION:               BOTH_SIDES_ALL_SESSIONS_FORWARD_SHADOW__TOX_SUPPORT_FILTER_OPTIONAL
PRODUCTION_FILES_MODIFIED:     false
TRADING_ENABLED:               false
```
