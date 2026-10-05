# event_logreg_v4_ohlc_vol_shadow

Shadow research release only. This is not an active dashboard, paper-trading, or production-execution model.

## Final Status

- PASS: `True`
- primary_candidate: `V3_27_plus_OHLC_vol`
- candidate beats V3 avg MCC: `True`
- candidate beats V3 worst-fold MCC: `True`
- leakage audit pass: `True`
- live Jun 8/9 diagnostic runs: `True`

## Fold / Training Metrics

| feature_set | n_features | n_folds | avg_mcc | worst_fold_mcc | avg_balanced_accuracy | avg_auc | avg_accuracy | avg_short_precision | avg_short_recall | avg_short_f1 | avg_long_precision | avg_long_recall | avg_long_f1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| OHLC_vol_only | 10 | 5 | 0.1381 | 0.1103 | 0.568 | 0.593 | 0.5754 | 0.6013 | 0.6576 | 0.6269 | 0.539 | 0.4784 | 0.5052 |
| V3_27_plus_OHLC_vol | 37 | 5 | 0.1341 | 0.1191 | 0.5657 | 0.5919 | 0.5734 | 0.5982 | 0.6693 | 0.63 | 0.5388 | 0.4621 | 0.495 |
| V3_27_baseline | 27 | 5 | 0.098 | 0.0606 | 0.546 | 0.5711 | 0.5601 | 0.5784 | 0.7193 | 0.6399 | 0.5261 | 0.3727 | 0.4344 |

## Probability Spread

| feature_set | mean_max_probability | pct_proba_ge_060 | pct_proba_ge_065 | pct_proba_ge_070 | avg_mcc | worst_fold_mcc | avg_balanced_accuracy | avg_auc |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| V3_27_baseline | 0.5701 | 26.1384 | 8.0616 | 1.6646 | 0.098 | 0.0606 | 0.546 | 0.5711 |
| OHLC_vol_only | 0.5773 | 31.6825 | 12.1612 | 1.0043 | 0.1381 | 0.1103 | 0.568 | 0.593 |
| V3_27_plus_OHLC_vol | 0.5857 | 36.2086 | 16.7836 | 4.4573 | 0.1341 | 0.1191 | 0.5657 | 0.5919 |

## Jun 8/9 Live Diagnostic

Diagnostic only; not used for feature selection.

| feature_set | diagnostic_scope | n_events | pred_long_pct | actual_long_pct | accuracy | balanced_accuracy | mcc | auc | short_precision | short_recall | short_f1 | short_support | long_precision | long_recall | long_f1 | long_support | mean_max_probability | pct_proba_ge_060 | pct_proba_ge_065 | pct_proba_ge_070 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| V3_27_baseline | Jun8_9_resolved_binary_not_for_feature_selection | 184 | 38.0435 | 43.4783 | 0.5326 | 0.5173 | 0.0353 | 0.5371 | 0.5789 | 0.6346 | 0.6055 | 104 | 0.4571 | 0.4 | 0.4267 | 80 | 0.5636 | 23.3696 | 7.6087 | 1.087 |
| OHLC_vol_only | Jun8_9_resolved_binary_not_for_feature_selection | 184 | 45.6522 | 43.4783 | 0.6196 | 0.6159 | 0.2306 | 0.6412 | 0.67 | 0.6442 | 0.6569 | 104 | 0.5595 | 0.5875 | 0.5732 | 80 | 0.591 | 42.3913 | 18.4783 | 5.9783 |
| V3_27_plus_OHLC_vol | Jun8_9_resolved_binary_not_for_feature_selection | 184 | 54.3478 | 43.4783 | 0.5978 | 0.6053 | 0.2096 | 0.6335 | 0.6786 | 0.5481 | 0.6064 | 104 | 0.53 | 0.6625 | 0.5889 | 80 | 0.6096 | 52.7174 | 28.2609 | 9.2391 |

## Leakage Audit

| name | pass | detail |
| --- | --- | --- |
| no_shift_minus_one_in_ohlc_builder | True | shift(-1 |
| no_bfill_in_ohlc_builder | True | bfill |
| no_backfill_in_ohlc_builder | True | backfill |
| no_center_true_in_ohlc_builder | True | center=True |
| V3_27_baseline_no_raw_price_level_features | True | [] |
| OHLC_vol_only_no_raw_price_level_features | True | [] |
| V3_27_plus_OHLC_vol_no_raw_price_level_features | True | [] |
| ohlc_formulas_current_or_past_only | True | {'current_bar': ['open', 'high', 'low', 'close'], 'prior_bar': ['prev_close'], 'causal_rolls': ['rolling_mean_20.shift(1)', 'rolling_high_20.shift(1)', 'rolling_low_20.shift(1)', 'rolling_std_logret_60.shift(1)']} |
| train_only_imputer_scaler_in_cv | True | Each fold fits Pipeline(SimpleImputer, StandardScaler, LogisticRegression) only on train fold. |

## Interpretation

The V4 shadow candidate is only a research candidate. It must remain inactive until a larger out-of-sample Rithmic-native history confirms the lift.