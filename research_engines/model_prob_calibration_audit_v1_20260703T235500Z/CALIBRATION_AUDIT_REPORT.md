# Audit 2 — Probability Calibration Audit v1
**Generated**: 2026-07-03T23:20:29.245324+00:00
**SHADOW / RESEARCH ONLY**

---

## Calibration Summary

Calibrated = Spearman ρ(bucket_midpoint, hit_rate) > 0 AND ρ(bucket_midpoint, net_return) > 0.
Kill warning = 0.70+ does NOT outperform 0.55–0.60 in net return.

| Version | Window | H | Calibrated | ρ(hit_rate) | 0.70+ net | 0.55-0.60 net | Kill warning |
|---------|--------|---|-----------|-------------|-----------|--------------|-------------|
| A | W=  5 | 10 | YES | +0.700 | +54.736 | -5.081 | **no** |
| A | W=  5 | 40 | YES | +0.700 | +248.957 | -17.369 | **no** |
| A | W= 10 | 10 | no | +0.300 | +53.205 | +12.006 | **no** |
| A | W= 10 | 40 | no | +0.300 | +244.048 | -17.860 | **no** |
| A | W= 20 | 10 | no | +0.500 | +45.581 | +31.186 | **no** |
| A | W= 20 | 40 | no | +0.500 | +191.051 | +42.936 | **no** |
| A | W= 30 | 10 | no | +0.400 | +42.363 | +21.656 | **no** |
| A | W= 30 | 40 | no | +0.400 | +192.937 | -8.167 | **no** |
| A | W= 50 | 10 | no | +0.100 | +54.632 | -49.621 | **no** |
| A | W= 50 | 40 | no | +0.100 | +184.266 | -75.782 | **no** |
| A | W= 80 | 10 | no | +0.300 | +33.151 | -33.810 | **no** |
| A | W= 80 | 40 | no | +0.300 | +156.876 | -131.269 | **no** |
| B | W=  5 | 10 | no | +0.600 | +57.631 | +8.525 | **no** |
| B | W=  5 | 40 | no | +0.600 | +249.657 | +66.642 | **no** |
| B | W= 10 | 10 | no | -0.100 | +46.488 | -22.998 | **no** |
| B | W= 10 | 40 | no | -0.100 | +214.889 | -45.684 | **no** |
| B | W= 20 | 10 | no | +0.200 | +48.246 | +25.850 | **no** |
| B | W= 20 | 40 | no | +0.200 | +180.967 | +149.766 | **no** |
| B | W= 30 | 10 | no | +0.200 | +34.869 | +35.691 | **YES** |
| B | W= 30 | 40 | no | +0.200 | +172.697 | +73.095 | **no** |
| B | W= 50 | 10 | no | +0.400 | +51.286 | -24.255 | **no** |
| B | W= 50 | 40 | no | +0.400 | +180.603 | -101.846 | **no** |
| B | W= 80 | 10 | YES | +0.700 | +31.705 | +29.323 | **no** |
| B | W= 80 | 40 | YES | +0.700 | +118.327 | -30.838 | **no** |

---

## Family Decision per Version

**Version A**: 2/12 calibrated, 0/12 kill warnings → **CALIBRATION DEFER — less than half of windows calibrated**

**Version B**: 2/12 calibrated, 1/12 kill warnings → **CALIBRATION DEFER — less than half of windows calibrated**

---

## Kill Warning Definition

If prob bucket 0.70+ does NOT produce higher net return than 0.55-0.60:
→ Model probabilities do not reflect higher trading edge at high confidence.
→ Threshold trading (e.g., 'only trade when prob > 0.65') would not work.

---

## Output Files

| File | Description |
|------|-------------|
| calibration_curves.csv | Full per-bucket stats per version/window/horizon |
| session_calibration.csv | Per-session per-bucket hit rate |
| regime_calibration.csv | Per-regime (vol) per-bucket hit rate |
| calibration_summary.csv | One row per version/window/horizon |
| CALIBRATION_AUDIT_REPORT.md | This report |

---

## Final Status
```
CALIBRATION_AUDIT_COMPLETE:             true
PRODUCTION_FILES_MODIFIED:              false
TRADING_ENABLED:                        false
OVERALL:                                PASS
```