# Matured Calibration Bug Check Report
Generated: 2026-06-30T21:33:44.540957+00:00

## CRITICAL: last_N Window Uses Event ROWS, Not Bar Decisions

### The Problem
`matured.tail(N)` takes the last N EVENT ROWS sorted by bar_end_ts_ns.
Average events per bar = 13.8.

| Window   | Event Rows | Effective Bars | % of total bars |
|----------|-----------|----------------|-----------------|
| last_25  | 25        | ~2          | ~0.0%           |
| last_50  | 50        | ~4          | ~0.1%           |
| last_100 | 100       | ~7          | ~0.1%          |
| last_250 | 250       | ~18         | ~0.3%          |

The label "last_N predictions" implies N independent decisions.
In reality, last_100 = only 7 bars of predictions.

## Current Performance Numbers

### Raw (event rows, as displayed in dashboard)
            window  n_matured  hit_rate       mcc  avg_realized_return_h40
0          last_25         25  0.000000       NaN               -26.400000
1          last_50         50  0.000000       NaN               -22.490000
2         last_100        100  0.000000       NaN               -18.297500
3         last_250        250  0.004000 -0.974873               -16.448000
4  session_to_date       3821  0.725988  0.395772                 9.820989

### Bar-deduped (first event per bar, correct way to measure)
            window  bar_n_matured  bar_hit_rate   bar_mcc  bar_balanced_accuracy  bar_brier  bar_log_loss  bar_mean_confidence  bar_avg_realized_return_h40  bar_long_hit_rate  bar_short_hit_rate  bar_high_conf_hit_rate  bar_high_conf_mcc  bar_n_high_conf
0          last_25             25      0.280000 -0.080700               0.446970   0.362995      0.938423             0.726363                   -22.750000           0.105263            0.833333                0.375000           0.174078               16
1          last_50             50      0.580000 -0.035503               0.473485   0.234644      0.653717             0.786274                   -33.170000           0.105263            0.870968                0.682927          -0.013575               41
2         last_100            100      0.730000  0.418418               0.718750   0.190087      0.562413             0.780958                   -11.732500           0.564103            0.836066                0.772152           0.377769               79
3         last_250            250      0.728000  0.435276               0.716945   0.198027      0.583075             0.680874                     8.282000           0.766667            0.670000                0.778689           0.555203              122
4  session_to_date            340      0.735294  0.428796               0.709909   0.196774      0.579959             0.685754                    14.732353           0.771300            0.666667                0.773256           0.564737              172

## Why last_25/50/100 = 0.0%?

The last 100 matured event rows come from June 29 bars 888-899 (10 bars total).
ALL 100 rows have pred_class=1 (LONG) and label_h40=0 (SHORT).
Hit rate = 0.0%.

### What happened in bars 888-899:
- Reaction types: HVN_rejection_from_below (LONG bias), LVN_rejection_from_above (LONG bias)
- Model predicted LONG with high confidence (0.66-0.82)
- Market FELL: realized H40 return = -18.3 pts average
- Price was between 30510-30522, testing HVN around 30522-30527 from below
  and LVN around 30509-30513 from above
- These zones typically (in training data) led to bounces → model predicted LONG
- On June 29 bars 888-899, price broke DOWN through support instead

## Why session_to_date = 72.6% but last_100 = 0.0%?

session_to_date covers ALL 3,821 matured events on June 29.
The first ~87 bars (3,721 events) were correctly predicted (early-to-mid session).
Only the last 10 bars (100 events) were wrong.

The "session_to_date" metric masks the recent failure.
The "last_100" metric over-represents the failure (10 bars, 100 rows).
Neither metric is ideal for measuring model reliability.

## Is HELD_LAST being counted as performance?

NO. Matured labels are computed by joining pred on (rithmic_date_str, future_idx).
Only rows where future_continuous_close is non-null are counted.
HELD_LAST is a display artifact only — it appears in sequence_pred (the display dataframe)
but is not in the base pred DataFrame used for maturation.

## Is dashboard counting HELD_LAST as performance?

NO (confirmed by code audit of compute_matured_labels in model_probs_trust_tab.py lines 263-302).

## Is forward horizon correct?

YES. HORIZON_BARS=40, matches training_config label_horizon_bars=40.
