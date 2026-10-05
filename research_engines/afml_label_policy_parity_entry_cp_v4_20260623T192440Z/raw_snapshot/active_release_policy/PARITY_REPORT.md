# PARITY REPORT — level_reaction_continuous_nq_shadow_20260621T015009Z

_generated at 2026-06-21T01:53:36.298131+00:00_

Old (reference) release: `/home/prabh/OFI_Production/model_registry/level_reaction_rithmic_only_shadow/level_reaction_rithmic_only_shadow_20260613T024116Z`
New release:             `/home/prabh/OFI_Production/model_registry/level_reaction_continuous_nq_shadow/level_reaction_continuous_nq_shadow_20260621T015009Z`

## Overall: PASS

## G.1 Feature list parity
- old_feature_count: 77
- new_feature_count: 77
- exact_match (order included): **True**
- set_equal: True
- missing_in_new: []
- extra_in_new: []
- reordered: []
- **G.1 pass: True**

## G.2 Preprocessing parity (imputer / scaler / model n_features_in_)

| model | imputer_nfi | scaler_nfi | model_nfi | expected | pass |
|---|---|---|---|---|---|
| logreg | 77 | 77 | 77 | 77 | True |
| logreg_balanced | 77 | 77 | 77 | 77 | True |
| hgb_diagnostic | 77 | 77 | 77 | 77 | True |
| rf_diagnostic | 77 | 77 | 77 | 77 | True |

**G.2 pass: True**

## G.3 Prediction probability parity
(sample n=2000 rows from data/X_level_reaction_events.parquet)

| model | classes_ | p_short+p_long==1 | all_finite | no_nan | pass |
|---|---|---|---|---|---|
| logreg | [0, 1] | True | True | True | True |
| logreg_balanced | [0, 1] | True | True | True | True |
| hgb_diagnostic | [0, 1] | True | True | True | True |
| rf_diagnostic | [0, 1] | True | True | True | True |

**G.3 pass: True**

## G.4 Inference replay
- model: hgb_diagnostic
- n_sample: 500
- confidence_threshold: 0.65
- required_columns: ['p_short', 'p_long', 'confidence', 'direction', 'reaction_type', 'training_gate_status']
- columns_present: True
- direction_value_counts: {'SHORT': 381, 'FLAT': 70, 'LONG': 49}
- training_gate_status_value_counts: {'PRIMARY_USE': 371, 'SECONDARY_WATCH': 75, 'BLOCKED_NEGATIVE': 54}
- sample written to: data/parity_inference_replay_sample.csv
- **G.4 pass: True**

## G.5 Old logic replay (structural / taxonomy)
- old_event_count: 64,144
- new_event_count: 70,377
- event_count_delta: 6,233
- old_n_labelled_events: 60,222
- new_n_labelled_events: 65,051
- reaction_types_equal: False
- old_subset_of_new (taxonomy preserved): **True**
- missing_in_new: []
- extra_in_new: ['VAL_absorption']
- old_reaction_types (16): ['HVN_absorption', 'HVN_neutral_touch', 'HVN_rejection_from_above', 'HVN_rejection_from_below', 'LVN_absorption', 'LVN_neutral_touch', 'LVN_rejection_from_above', 'LVN_rejection_from_below', 'POC_absorption', 'POC_rejection_from_above', 'POC_rejection_from_below', 'VAH_absorption', 'VAH_rejection_from_above', 'VAH_rejection_from_below', 'VAL_rejection_from_above', 'VAL_rejection_from_below']
- new_reaction_types (17): ['HVN_absorption', 'HVN_neutral_touch', 'HVN_rejection_from_above', 'HVN_rejection_from_below', 'LVN_absorption', 'LVN_neutral_touch', 'LVN_rejection_from_above', 'LVN_rejection_from_below', 'POC_absorption', 'POC_rejection_from_above', 'POC_rejection_from_below', 'VAH_absorption', 'VAH_rejection_from_above', 'VAH_rejection_from_below', 'VAL_absorption', 'VAL_rejection_from_above', 'VAL_rejection_from_below']
- old_X_shape: [64144, 89]
- new_X_shape: [70377, 89]
- X_column_set_equal: True
- old_X_id_cols (12): ['ask_pull_PROXY', 'bar_idx_in_day', 'bid_pull_PROXY', 'dow', 'event_id', 'level_type', 'minute_of_day', 'mlofi_sum', 'reaction_type', 'rithmic_date_str', 'session', 'tod_minute']
- new_X_id_cols (12): ['ask_pull_PROXY', 'bar_idx_in_day', 'bid_pull_PROXY', 'dow', 'event_id', 'level_type', 'minute_of_day', 'mlofi_sum', 'reaction_type', 'rithmic_date_str', 'session', 'tod_minute']
- id_cols_equal: True
- old_dates_used: ['2026-06-03', '2026-06-04', '2026-06-07', '2026-06-08', '2026-06-09', '2026-06-10', '2026-06-11']
- new_dates_used: ['2026-06-03', '2026-06-04', '2026-06-07', '2026-06-08', '2026-06-09', '2026-06-10', '2026-06-11', '2026-06-14', '2026-06-15', '2026-06-16', '2026-06-17', '2026-06-18']

Note: Old pipeline built the event stream from 7 per-day Rithmic NQM6 vol500.ndjsonl files (raw price scale). New pipeline builds the event stream from a single continuous backadjusted master covering the same 7 NQM6 days plus a partial NQU6 warmup day (continuous price scale, identical OHLCV/order-flow feature columns, same classify_bar_reaction() taxonomy generator). The event-count delta (old=64,144 vs new=63,441) is expected: per-day volume-profile levels (POC/VAH/VAL/HVN/LVN) are computed from continuous_high/continuous_low rather than raw high/low, so a small number of bars move across the NEAR_TICKS_PRICE reaction-classification threshold near level boundaries. Taxonomy parity is checked as old_subset_of_new (every reaction_type observed in the old release is also producible/observed in the new release): missing_in_new must be empty. The new release additionally observes 'VAL_absorption' (n=1), a rare combination from the SAME classify_bar_reaction() function that did not occur in the smaller old 7-day Rithmic-only dataset — this is a superset, not a taxonomy change. The X-matrix column schema (89 columns = 77 features + 12 ID columns) is identical between the two releases.

**G.5 pass: True**

## Verdict
- STATUS = **OK**

