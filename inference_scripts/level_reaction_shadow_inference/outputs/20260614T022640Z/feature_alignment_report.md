# Feature Alignment Report

_run: 2026-06-14T02:26:46.660070+00:00_

- feature_names.json: `/home/prabh/OFI_Production/model_registry/level_reaction_rithmic_only_shadow/level_reaction_rithmic_only_shadow_20260613T024116Z/feature_names.json`
- Required feature count: **77**
- Fed feature count:       **77**
- Exact ordered match:     **True**
- model.n_features_in_:    **77**
- imputer.n_features_in_:  **77**
- scaler.n_features_in_:   **77**
- Missing required:        **(none) ✓**
- Extra (dropped):         **(none) ✓**

## Per-feature presence
```
 position                   feature_name  in_X  non_null_pct
        1              dist_to_poc_ticks  True        100.00
        2              dist_to_vah_ticks  True        100.00
        3              dist_to_val_ticks  True        100.00
        4              dist_to_hvn_ticks  True        100.00
        5              dist_to_lvn_ticks  True        100.00
        6                dist_to_poc_vol  True        100.00
        7                dist_to_vah_vol  True        100.00
        8                dist_to_val_vol  True        100.00
        9                dist_to_hvn_vol  True        100.00
       10                dist_to_lvn_vol  True        100.00
       11              inside_value_area  True        100.00
       12                      above_vah  True        100.00
       13                      below_val  True        100.00
       14          touch_count_past_only  True        100.00
       15         bars_since_prior_touch  True        100.00
       16                     delta_norm  True        100.00
       17               delta_norm_lag_1  True        100.00
       18               delta_norm_lag_2  True        100.00
       19               delta_norm_lag_3  True        100.00
       20                delta_rolling_5  True        100.00
       21                mlofi_decay_sum  True        100.00
       22                     mlofi_norm  True        100.00
       23                mlofi_rolling_5  True        100.00
       24                    mlofi_accel  True        100.00
       25                     decay_norm  True        100.00
       26               mlofi_norm_lag_1  True        100.00
       27               mlofi_norm_lag_2  True        100.00
       28               mlofi_norm_lag_3  True        100.00
       29               decay_norm_lag_1  True        100.00
       30               decay_norm_lag_2  True        100.00
       31               decay_norm_lag_3  True        100.00
       32           sweep_imbalance_norm  True        100.00
       33                     sweep_norm  True        100.00
       34                sweep_buy_ratio  True        100.00
       35               sweep_sell_ratio  True        100.00
       36                      buy_ratio  True        100.00
       37                     sell_ratio  True        100.00
       38                           vpin  True        100.00
       39                     vpin_lag_1  True        100.00
       40                     vpin_lag_2  True        100.00
       41                     vpin_lag_3  True        100.00
       42                    mid_resid_z  True        100.00
       43                       mid_ret1  True        100.00
       44                   volatility_5  True        100.00
       45                 bar_duration_s  True        100.00
       46           delta_norm_resid_z20  True        100.00
       47         volatility_5_resid_z20  True        100.00
       48 sweep_imbalance_norm_resid_z20  True        100.00
       49                 vpin_resid_z20  True        100.00
       50                candle_body_vol  True        100.00
       51               candle_range_vol  True        100.00
       52                 upper_wick_vol  True        100.00
       53                 lower_wick_vol  True        100.00
       54                 close_location  True         99.98
       55             open_to_close_sign  True        100.00
       56        close_vs_prev_close_vol  True         99.68
       57         close_vs_roll_mean_vol  True         98.52
       58                 high_break_vol  True         98.02
       59                  low_break_vol  True         98.02
       60                        lvl_POC  True        100.00
       61                        lvl_VAH  True        100.00
       62                        lvl_VAL  True        100.00
       63                        lvl_HVN  True        100.00
       64                        lvl_LVN  True        100.00
       65                      sess_Asia  True        100.00
       66                        sess_EU  True        100.00
       67                   sess_US_Open  True        100.00
       68                     sess_US_AM  True        100.00
       69                     sess_US_PM  True        100.00
       70                   sess_US_Late  True        100.00
       71       rxn_rejection_from_above  True        100.00
       72       rxn_rejection_from_below  True        100.00
       73                 rxn_absorption  True        100.00
       74                 rxn_acceptance  True        100.00
       75              rxn_neutral_touch  True        100.00
       76                  entropy_score  True        100.00
       77                 flow_alignment  True        100.00
```