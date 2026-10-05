# OFI Phase 2 Report

Generated: 2026-07-28T01:12:56Z

## Gates

- spine_total_41308: True
- labels_rows_eq_spine: True
- features_rows_eq_spine: True
- ladder_rows_eq_spine: True
- B1_ordering_gate_pass: True
- B6_dead_columns_confirmed: True

## Corrections to Brief (discovered during Part A/B audits)

The brief's per-contract row/session counts do not match the master as delivered. Total spine
rows (41,308) and the hard gate on that total are both confirmed correct; the per-contract split
is not:

| | brief | actual (measured) |
|---|---|---|
| NQM6 bars | 12,354 | 11,690 |
| NQU6 bars | 28,954 | 29,618 |
| NQM6 sessions | 7 | 7 (matches) |
| NQU6 sessions | 28 | 29 (2026-06-14..07-23, includes 1 PARTIAL session 2026-06-22) |

Other brief priors corrected by measurement:
- ohlcv_source=='unavailable' at bar level (depth 10): brief said "~40%"; measured **18.71%** (see B4).
- NQU6 bar_idx gaps: brief said "known to skip ~665 indices"; measured **2 missing indices total**
  (runs [1622,1622] and [2684,2684] -- see part_B1_bar_idx_gaps.csv). NQM6 has zero gaps.
- B1 ordering gate: 3 (contract, session_date) groups have **tied** (duplicate-nanosecond)
  `bar_end_ts_ns` values -- NQM6 2026-06-09 (1 tie) and NQU6 2026-07-07 / 2026-07-08 (1 tie each,
  the latter two being the known Rithmic dead-feed recovery sessions from project history). In all
  three, `bar_idx` stays strictly increasing and the close-diff reconciliation is exact, so this is
  reported as a WARNING (not a gate failure) -- see part_B1_ordering_reconciliation.csv. No true
  timestamp reversal was found anywhere in the spine.

## B2 bar timing (seconds between bar closes; see part_B2_bar_timing.csv)

| segment | n | median s | p25 | p75 | p99 |
|---|---|---|---|---|---|
| overall | 41,272 | 31.0 | 16.0 | 70.5 | 511.5 |
| RTH | 29,931 | 22.7 | 13.5 | 38.1 | 102.8 |
| overnight | 11,306 | 158.5 | 80.5 | 261.9 | 716.8 |

72.5% of bars fall in RTH (13:30-20:00 UTC); RTH bars seal roughly 7x faster than overnight bars,
consistent with 500-volume bars sealing on trade volume rather than wall-clock time.

## B7 ctx coverage (null fraction overall; see part_B7_ctx_null_frac_*.csv for per-session detail)

Core context columns (vpin/mlofi/delta/rxn_absorption/dist_to_{poc,vah,val}) are ~28.4% null;
dist_to_hvn/lvn are 33.5%/29.5% null; the bf_bs bullish/bearish switch-score columns are 73.0% null
(a sparse, event-triggered signal by construction, not a data-quality defect).

## B3 corrected forward-return table (see part_B3_fwd_return_table.csv for full detail)

session-implied sigma (1-bar std 13.6878 pts * sqrt(median 1032 bars/session)) = 439.6104 pts vs realized per-session close-range median = 652.7500 pts

## B4 mid-staleness / B5 ask-flow / B7 ctx coverage
- overall pct ohlcv_source==unavailable: 0.1871066137309964
- sessions with zero ask-side flow: 0
- B6 dead trade_volume columns confirmed dead: True

## Label stats (Part C)
{
  "valid_counts": {
    "1": 41272,
    "2": 41236,
    "5": 41128,
    "10": 40948,
    "20": 40588,
    "40": 39868
  },
  "z_describe": {
    "1": {
      "n": 41152,
      "mean": -0.01040446332109525,
      "std": 1.004020353045471,
      "p05": -1.6365367900770116,
      "p50": 0.0,
      "p95": 1.5913740640170504
    },
    "2": {
      "n": 41116,
      "mean": -0.014432856391110983,
      "std": 0.995871719434855,
      "p05": -1.6006586659745965,
      "p50": -0.01288050070623924,
      "p95": 1.5721588864128948
    },
    "5": {
      "n": 41008,
      "mean": -0.021182089990411306,
      "std": 0.996031489709524,
      "p05": -1.6163052049098132,
      "p50": -0.01710347089270907,
      "p95": 1.5656186396780352
    },
    "10": {
      "n": 40828,
      "mean": -0.028425176609959423,
      "std": 0.9949094478995696,
      "p05": -1.606244323039556,
      "p50": -0.033389107975846086,
      "p95": 1.5598899681307172
    },
    "20": {
      "n": 40468,
      "mean": -0.039966910436328947,
      "std": 0.9951361072767925,
      "p05": -1.649808679139374,
      "p50": -0.022140774215225444,
      "p95": 1.5331220262792336
    },
    "40": {
      "n": 39748,
      "mean": -0.056664965374230075,
      "std": 1.0018643500297528,
      "p05": -1.6962970566288023,
      "p50": -0.03142553303953652,
      "p95": 1.494296936886783
    }
  },
  "reference_class_balance_30_70": {
    "1": {
      "q30": -0.5352961941801331,
      "q70": 0.517160499906559,
      "frac_below": 0.3000097200622084,
      "frac_mid": 0.39998055987558323,
      "frac_above": 0.3000097200622084
    },
    "2": {
      "q30": -0.5449722828043906,
      "q70": 0.5140483888776033,
      "frac_below": 0.30000486428640916,
      "frac_mid": 0.39999027142718163,
      "frac_above": 0.30000486428640916
    },
    "5": {
      "q30": -0.5554007916948521,
      "q70": 0.5068050488529855,
      "frac_below": 0.30001463129145534,
      "frac_mid": 0.39997073741708933,
      "frac_above": 0.30001463129145534
    },
    "10": {
      "q30": -0.560215042882011,
      "q70": 0.5054731703442323,
      "frac_below": 0.3000146957970021,
      "frac_mid": 0.3999706084059959,
      "frac_above": 0.3000146957970021
    },
    "20": {
      "q30": -0.5598951935570927,
      "q70": 0.4846632882816998,
      "frac_below": 0.30001482652960365,
      "frac_mid": 0.3999703469407927,
      "frac_above": 0.30001482652960365
    },
    "40": {
      "q30": -0.5678940552123938,
      "q70": 0.48533559681324134,
      "frac_below": 0.3000150950991245,
      "frac_mid": 0.399969809801751,
      "frac_above": 0.3000150950991245
    }
  }
}

## Ladder coverage (Part D)
{
  "total_rows": 41308,
  "gate_rows_eq_spine_pass": true,
  "n_truncated_bars": 14794,
  "pct_truncated_bars": 0.3581388593008618,
  "total_truncated_cells": 450896,
  "cells_retained": 3520825
}

## Fold table (Part F)
{
  "n_folds": 10,
  "n_nqu6_sessions": 29,
  "partial_sessions": [
    "2026-06-22"
  ]
}

## Part H LightGBM baseline
{
  "EXCLUDED": {
    "h10_mean_ic": 0.006457363384294869,
    "h10_ic_tstat": 0.25748256006004067,
    "h20_mean_ic": 0.020277526010328294,
    "h20_ic_tstat": 0.7293250185398503,
    "h40_mean_ic": 0.052206480575481204,
    "h40_ic_tstat": 1.7097616849381783,
    "mean_class_accuracy_z40": 0.36138292845154035,
    "class0_mean_precision": 0.31795528952126834,
    "class1_mean_precision": 0.4549935664424642,
    "class2_mean_precision": 0.3328758445128125
  },
  "TRAIN_ONLY": {
    "h10_mean_ic": 0.00941042983524857,
    "h10_ic_tstat": 0.4128761117331046,
    "h20_mean_ic": 0.021926687475656702,
    "h20_ic_tstat": 0.6768314051513643,
    "h40_mean_ic": -0.02620142914134897,
    "h40_ic_tstat": -0.7640215672310651,
    "mean_class_accuracy_z40": 0.36488515582964554,
    "class0_mean_precision": 0.3151556495774781,
    "class1_mean_precision": 0.4647454269207639,
    "class2_mean_precision": 0.33347880101732635
  }
}