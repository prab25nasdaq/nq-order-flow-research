# Audit 5C — Timestamp Dependency / PnL Concentration Audit
**Generated**: 2026-07-04T06:44:11.691007+00:00
**Candidate**: Version B, W=20, threshold=0.65, H10 primary, H40 secondary
**SHADOW / RESEARCH ONLY — no execution, no broker, no order placement**

---

## 1. Executive Decision

### **BURST_DEPENDENT_DEFER**

| Criterion | Value |
|-----------|-------|
| Total OOS net H10 | +2,016,880 ticks |
| Total OOS net H40 | +6,852,658 ticks |
| Top-2 days % of total H10 | 49.8% |
| Thinning rules with PASS | 6/8 |
| Days: HEALTHY_SPREAD | 1/18 |
| Days: BURST_DEPENDENT | 15/18 |
| Proceed to Audit 6? | **NO — resolve concentration first** |

---

## 2. Daily Concentration

| Date | N signals | Net H10 | % of Total | Win rate |
|------|-----------|---------|-----------|---------|
| 2026-06-23 | 6,317 | +628,590 | 31.2% | 0.761 |
| 2026-06-08 | 15,611 | +376,389 | 18.7% | 0.572 |
| 2026-06-07 | 7,678 | +331,341 | 16.4% | 0.582 |
| 2026-06-24 | 2,057 | +215,913 | 10.7% | 0.685 |
| 2026-06-17 | 933 | +158,295 | 7.8% | 0.889 |
| 2026-06-10 | 3,849 | +133,661 | 6.6% | 0.570 |
| 2026-06-15 | 1,011 | +65,720 | 3.3% | 0.697 |
| 2026-06-25 | 244 | +62,661 | 3.1% | 0.775 |
| 2026-06-09 | 1,630 | +46,395 | 2.3% | 0.680 |
| 2026-06-14 | 591 | +46,265 | 2.3% | 0.792 |
| 2026-06-29 | 1,923 | +38,108 | 1.9% | 0.686 |
| 2026-06-16 | 353 | +21,174 | 1.0% | 0.660 |
| 2026-06-30 | 232 | +14,676 | 0.7% | 0.707 |
| 2026-06-21 | 225 | +11,005 | 0.5% | 0.462 |
| 2026-06-11 | 124 | +9,814 | 0.5% | 0.710 |
| 2026-06-22 | 12 | +3,435 | 0.2% | 0.833 |
| 2026-06-18 | 163 | -11,437 | -0.6% | 0.184 |
| 2026-06-28 | 909 | -135,125 | -6.7% | 0.320 |

**Top-2 days (Jun-23 + Jun-08) combined**: +1,004,979t = 49.8% of total H10.

---

## 3. Jun-23 and Jun-08: Broad Edge or Burst Only?

### Jun-23
- Signals: 6,317  Net H10: +628,590t  Win rate: 0.761
- Top-3 15-min buckets % of day: 39.3%
- Burst flag: **HEALTHY_SPREAD**
- Verdict: Broadly distributed — not burst-dependent

### Jun-08
- Signals: 15,611  Net H10: +376,389t  Win rate: 0.572
- Top-3 15-min buckets % of day: 111.7%
- Burst flag: **SINGLE_BURST_DEPENDENT**
- Verdict: Burst-concentrated — a few 15-min windows drove the day

---

## 4. Jun-28: One Bad Burst or Whole-Day Failure?

- Signals: 909  Net H10: -135,125t  Win rate: 0.320
- H40 net on Jun-28: -205,549t  H40 win rate: 0.251
- Confidently wrong signals (prob≥0.80, net<0): 173
- Burst flag: **MIXED**
- H40 confirms failure: YES — both H10 and H40 negative

**Diagnosis**: See `audit_5c_jun28_failure_clusters.csv` for cluster details.

---

## 5. Signal Thinning Results

| Rule | N trades | Avg net H10 | Total H10 | Win rate | Best-day% | Top-2% | Decision |
|------|----------|------------|---------|---------|---------|------|---------|
| A__raw_all_signals | 43,862 | +45.98 | +2,016,880 | 0.624 | 31.2% | 49.8% | DEFER__concentration_remains_high |
| B__1min_per_dir | 2,201 | +30.22 | +66,516 | 0.573 | 29.5% | 40.5% | DEFER__concentration_remains_high |
| C__5min_per_dir | 1,272 | +34.69 | +44,132 | 0.572 | 23.4% | 34.4% | PASS |
| D__10min_per_dir | 930 | +36.57 | +34,008 | 0.577 | 18.3% | 31.9% | PASS |
| E__15min_per_dir | 747 | +35.99 | +26,887 | 0.572 | 14.8% | 28.7% | PASS |
| F__best_prob_10min | 862 | +35.85 | +30,907 | 0.567 | 16.2% | 31.5% | PASS |
| G__cooldown_H10bars_282s | 1,071 | +36.83 | +39,442 | 0.578 | 20.5% | 31.4% | PASS |
| H__cooldown_H40bars_1131s | 535 | +31.17 | +16,677 | 0.550 | 20.6% | 36.7% | PASS |

---

## 6. Burst Concentration Flags

| Date | Day Net H10 | Best-5min% | Top3-15min% | Flag |
|------|------------|-----------|------------|------|
| 2026-06-07 | +331,341 | 24.9% | 71.7% | BURST_DEPENDENT |
| 2026-06-08 | +376,389 | 84.7% | 111.7% | SINGLE_BURST_DEPENDENT |
| 2026-06-09 | +46,395 | 81.4% | 145.6% | SINGLE_BURST_DEPENDENT |
| 2026-06-10 | +133,661 | 50.4% | 129.3% | SINGLE_BURST_DEPENDENT |
| 2026-06-11 | +9,814 | 83.9% | 105.9% | SINGLE_BURST_DEPENDENT |
| 2026-06-14 | +46,265 | 31.2% | 69.7% | BURST_DEPENDENT |
| 2026-06-15 | +65,720 | 25.2% | 115.3% | SINGLE_BURST_DEPENDENT |
| 2026-06-16 | +21,174 | 41.3% | 149.6% | SINGLE_BURST_DEPENDENT |
| 2026-06-17 | +158,295 | 20.5% | 71.2% | SINGLE_BURST_DEPENDENT |
| 2026-06-18 | -11,437 | -33.5% | -41.3% | MIXED |
| 2026-06-21 | +11,005 | 88.3% | 208.6% | SINGLE_BURST_DEPENDENT |
| 2026-06-22 | +3,435 | 92.7% | 100.0% | SINGLE_BURST_DEPENDENT |
| 2026-06-23 | +628,590 | 11.8% | 39.3% | HEALTHY_SPREAD |
| 2026-06-24 | +215,913 | 22.2% | 87.4% | BURST_DEPENDENT |
| 2026-06-25 | +62,661 | 37.2% | 81.8% | BURST_DEPENDENT |
| 2026-06-28 | -135,125 | -5.1% | -8.7% | MIXED |
| 2026-06-29 | +38,108 | 23.5% | 69.6% | BURST_DEPENDENT |
| 2026-06-30 | +14,676 | 26.0% | 70.7% | BURST_DEPENDENT |

---

## 7. Required Next Actions

- **Overall decision**: BURST_DEPENDENT_DEFER
- Do NOT proceed to Audit 6 until concentration is resolved.

**Specific actions:**

1. Identify the specific 15-min windows driving Jun-23 and Jun-08 gains.
2. Determine whether those windows are model-driven or event-driven.
3. Test whether edge survives removing the top-3 burst windows per day.
4. If edge survives → proceed to Audit 6 with thinning rules E or G.
5. If edge does not survive → KILL candidate.

---

## Output Files

| File | Description |
|------|-------------|
| `audit_5c_daily_concentration.csv` | Daily P&L breakdown |
| `audit_5c_top_raw_events_key_days.csv` | Top raw events on key days |
| `audit_5c_top_1min_buckets_key_days.csv` | 1-min buckets on key days |
| `audit_5c_top_5min_buckets_key_days.csv` | 5-min buckets on key days |
| `audit_5c_top_15min_buckets_key_days.csv` | 15-min buckets on key days |
| `audit_5c_top_60min_buckets_key_days.csv` | 60-min buckets on key days |
| `audit_5c_burst_concentration_by_day.csv` | Burst flags per day |
| `audit_5c_thinning_results.csv` | 8-rule thinning results |
| `audit_5c_big_vs_normal_days.csv` | Big/bad/normal day comparison |
| `audit_5c_jun28_failure_clusters.csv` | Jun-28 failure cluster detail |
| `AUDIT_5C_TIMESTAMP_DEPENDENCY_REPORT.md` | This report |

---

## Final Status
```
AUDIT_5C_COMPLETE:             true
EXECUTIVE_DECISION:            BURST_DEPENDENT_DEFER
PROCEED_TO_AUDIT_6:            False
PRODUCTION_FILES_MODIFIED:     false
TRADING_ENABLED:               false
```