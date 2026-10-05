# LEAKAGE / CROSS-CONTRACT AUDIT

_run at 2026-06-21T01:53:35.579969+00:00_

## Overall: PASS (11/11 checks passed)

| Check | Result | Detail |
|---|---|---|
| no_future_bars_in_features | PASS | feature_leakage_audit.future_data_check_passed=True, future_data_in_features=False |
| no_centered_rolling_or_shift_minus1_or_bfill | PASS | centered_rolling_used=False, shift_minus_1_used=False, bfill_used=False |
| no_raw_price_cols_in_features | PASS | raw_price_cols_present=[] |
| label_h40_uses_only_forward_path | PASS | uses_only_forward_path=True, uses_shift_minus_1=False, uses_bfill=False |
| label_price_source_documented | PASS | label_price_source='continuous_close' (constant-shift invariant vs raw_close — see constant_shift_invariance_note) |
| cumulative_roll_adjustment_is_fixed_scalar_from_locked_roll_map | PASS | precheck.roll_map={'path': '/home/prabh/OFI_Live_Features/roll_maps/NQM6_to_NQU6_20260614T225748Z.json', 'exists': True, 'roll_gap_points': 632.5, 'roll_gap_method': 'first_stable_new_minus_last_old', 'roll_quality_flag': 'ROLLOVER_WARMUP_LOW_SAMPLE'} |
| projected_prior_levels_not_used_in_training_events | PASS | All training events have level_source='native' (see EVENT_STREAM_REPORT.md); 'projected_prior_level' levels (projected_levels_NQM6_to_NQU6.csv, all rows warning=PROJECTED_PRIOR_LEVEL_NOT_NATIVE_NQU6, valid_for_dashboard=False) are used ONLY by the continuous inference script for display in the NQU6 rollover-warmup region — never as training labels/features. |
| purged_walk_forward_cv_with_embargo | PASS | embargo_bars=40, label_horizon_bars=40, cross_day_overlap_count=0, purge_strategy='by-calendar-day expanding window' |
| duplicate_bar_timestamps_deduplicated_keep_first | PASS | n_duplicates_removed=1, detail=[{'bar_end_ts_ns': 1781101501298432000, 'bar_index': 8911, 'contract_symbol': 'NQM6', 'source_contract': 'NQM6', 'is_backadjusted_history': True, 'day': 20260609, 'continuous_close': 29852.5, 'raw_close': 29220.0, 'timestamp_utc': '2026-06-10 14:25:01.298432000+00:00'}, {'bar_end_ts_ns': 1781101501298432000, 'bar_index': 8912, 'contract_symbol': 'NQM6', 'source_contract': 'NQM6', 'is_backadjusted_history': True, 'day': 20260609, 'continuous_close': 29852.5, 'raw_close': 29220.0, 'timestamp_utc': '2026-06-10 14:25:01.298432000+00:00'}] |
| no_databento_columns | PASS | precheck.databento_columns=[] |
| old_master_untouched_sha256_locked | PASS | precheck.old_master_protected={'path': '/home/prabh/OFI_Live_Features/master.ndjsonl', 'exists': True, 'sha256_at_precheck': '6870b1f2aea00a96eb70407d70ccca4e41030ca3c53fe69963118b659a80b8de', 'sha256_locked': '6870b1f2aea00a96eb70407d70ccca4e41030ca3c53fe69963118b659a80b8de', 'matches_locked': True} |

## Constant-shift invariance proof (continuous_close vs raw_close)

continuous_close = raw_close + cumulative_roll_adjustment_points, where cumulative_roll_adjustment_points is a FIXED scalar (632.5 for NQM6-history rows, 0 for NQU6 rows) taken verbatim from the locked roll map — never recomputed and never derived from model predictions or future NQU6 data. For any two same-regime bars i<j with prices a=raw_close[i], b=raw_close[j] and constant K=cumulative_roll_adjustment_points: sign(log((b+K)/(a+K))) == sign((b+K)-(a+K)) == sign(b-a) == sign(log(b/a)) for a,b,K>0 (log is monotonic). Therefore label_h40's SIGN computed from continuous_close is IDENTICAL to the sign that would be computed from raw_close for the NQM6-adjusted training region — switching the label price source to continuous_close does not change which class (SHORT=0/LONG=1) any training event belongs to.