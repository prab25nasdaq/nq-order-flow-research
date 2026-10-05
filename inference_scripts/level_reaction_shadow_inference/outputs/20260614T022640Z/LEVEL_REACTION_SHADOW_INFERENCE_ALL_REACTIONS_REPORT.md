# Level-Reaction Shadow Inference Report (ALL REACTIONS)

_run: 2026-06-14T02:26:46.969534+00:00_

## Inputs
- Master: `/home/prabh/OFI_Live_Features/master.ndjsonl`
- Release: `/home/prabh/OFI_Production/model_registry/level_reaction_rithmic_only_shadow/level_reaction_rithmic_only_shadow_20260613T024116Z`
- Model file: `/home/prabh/OFI_Production/model_registry/level_reaction_rithmic_only_shadow/level_reaction_rithmic_only_shadow_20260613T024116Z/models/model_hgb_diagnostic.pkl`
- Model type: `sklearn.ensemble._hist_gradient_boosting.gradient_boosting.HistGradientBoostingClassifier`
- `model.classes_`: [0, 1]
- Imputer: `/home/prabh/OFI_Production/model_registry/level_reaction_rithmic_only_shadow/level_reaction_rithmic_only_shadow_20260613T024116Z/models/imputer_hgb_diagnostic.pkl`
- Scaler:  `/home/prabh/OFI_Production/model_registry/level_reaction_rithmic_only_shadow/level_reaction_rithmic_only_shadow_20260613T024116Z/models/scaler_hgb_diagnostic.pkl`
- feature_names.json: `/home/prabh/OFI_Production/model_registry/level_reaction_rithmic_only_shadow/level_reaction_rithmic_only_shadow_20260613T024116Z/feature_names.json`
- Required feature count: 77
- Fed feature count: 77
- Confidence threshold: 0.65
- Reaction mode: all_detected
- Bars loaded: 5,000

## Reactions
- All trained (16): ['HVN_absorption', 'HVN_neutral_touch', 'HVN_rejection_from_above', 'HVN_rejection_from_below', 'LVN_absorption', 'LVN_neutral_touch', 'LVN_rejection_from_above', 'LVN_rejection_from_below', 'POC_absorption', 'POC_rejection_from_above', 'POC_rejection_from_below', 'VAH_absorption', 'VAH_rejection_from_above', 'VAH_rejection_from_below', 'VAL_rejection_from_above', 'VAL_rejection_from_below']
- Detected in window: ['HVN_absorption', 'HVN_rejection_from_above', 'HVN_rejection_from_below', 'LVN_absorption', 'LVN_rejection_from_above', 'LVN_rejection_from_below', 'POC_absorption', 'POC_rejection_from_above', 'POC_rejection_from_below', 'VAH_rejection_from_above', 'VAH_rejection_from_below', 'VAL_absorption', 'VAL_rejection_from_above', 'VAL_rejection_from_below']
- Trained present:    ['HVN_rejection_from_below', 'HVN_rejection_from_above', 'LVN_rejection_from_below', 'LVN_rejection_from_above', 'POC_rejection_from_below', 'POC_rejection_from_above', 'VAH_rejection_from_below', 'HVN_absorption', 'VAH_rejection_from_above', 'VAL_rejection_from_above', 'VAL_rejection_from_below', 'LVN_absorption', 'POC_absorption']
- Trained missing:    ['HVN_neutral_touch', 'LVN_neutral_touch', 'VAH_absorption']
- Total events scored: 18,622

## Per-reaction summary
```
           reaction_type    n  trained_reaction training_gate_status  n_training_for_reaction  mean_p_long  mean_p_short  mean_confidence  pct_conf_ge_threshold  pct_high_conf_long  pct_high_conf_short                    latest_timestamp latest_direction  latest_confidence
HVN_rejection_from_below 7196              True          PRIMARY_USE                    25080       0.5751        0.4249           0.6996                  54.36               37.83                16.54 2026-06-12 13:45:57.364607000+00:00             LONG             0.8970
HVN_rejection_from_above 7010              True          PRIMARY_USE                    24783       0.5181        0.4819           0.7189                  58.17               32.00                26.18 2026-06-12 13:46:07.762444000+00:00             LONG             0.8597
LVN_rejection_from_above 2162              True  EXPLORATORY_SMALL_N                     6471       0.6966        0.3034           0.7783                  76.78               65.36                11.42 2026-06-12 13:46:26.615317000+00:00             LONG             0.9242
LVN_rejection_from_below 1644              True     BLOCKED_NEGATIVE                     6726       0.6117        0.3883           0.7711                  66.36               46.90                19.46 2026-06-12 15:26:00.447583000+00:00             FLAT             0.5120
POC_rejection_from_above  168              True     BLOCKED_UNSTABLE                      295       0.5660        0.4340           0.6191                  35.71               28.57                 7.14 2026-06-12 20:02:01.982100000+00:00            SHORT             0.6968
POC_rejection_from_below  162              True      SECONDARY_WATCH                      312       0.5671        0.4329           0.6189                  34.57               29.01                 5.56 2026-06-12 20:00:32.123425000+00:00            SHORT             0.7324
VAH_rejection_from_above   68              True     BLOCKED_NEGATIVE                       87       0.4911        0.5089           0.6263                  35.29               17.65                17.65 2026-06-12 20:17:22.401298000+00:00            SHORT             0.7436
VAH_rejection_from_below   59              True     BLOCKED_NEGATIVE                      117       0.4926        0.5074           0.5956                  20.34                8.47                11.86 2026-06-12 20:20:31.319065000+00:00            SHORT             0.7432
          HVN_absorption   57              True  EXPLORATORY_SMALL_N                       94       0.6107        0.3893           0.7131                  57.89               57.89                 0.00 2026-06-11 17:59:20.790224000+00:00             LONG             0.8176
VAL_rejection_from_above   45              True  EXPLORATORY_SMALL_N                       83       0.6049        0.3951           0.6624                  57.78               51.11                 6.67 2026-06-12 16:07:32.390916000+00:00             LONG             0.6889
VAL_rejection_from_below   39              True     BLOCKED_NEGATIVE                       72       0.6288        0.3712           0.6524                  56.41               51.28                 5.13 2026-06-12 16:06:05.384058000+00:00             LONG             0.7143
          LVN_absorption   10              True              SMALL_N                        8       0.6101        0.3899           0.6101                   0.00                0.00                 0.00 2026-06-10 19:15:41.028872000+00:00             FLAT             0.6101
          POC_absorption    1              True              SMALL_N                        3       0.3690        0.6310           0.6310                   0.00                0.00                 0.00 2026-06-09 19:59:51.464172000+00:00             FLAT             0.6310
          VAL_absorption    1             False            UNTRAINED                        0       0.7337        0.2663           0.7337                 100.00              100.00                 0.00 2026-06-12 08:37:49.426728000+00:00             LONG             0.7337
```

## Latest 20 predictions
```
      bar_end_ts_ns            reaction_type level_type    close   p_long  p_short  confidence direction   signal_status
1781294139649901000 POC_rejection_from_above        POC 29669.00 0.524428 0.475572    0.524428      FLAT   LOW_CONF_FLAT
1781294281079571000 POC_rejection_from_below        POC 29658.50 0.586353 0.413647    0.586353      FLAT   LOW_CONF_FLAT
1781294310118849000 POC_rejection_from_above        POC 29667.50 0.694297 0.305703    0.694297      LONG  HIGH_CONF_LONG
1781294363257271000 POC_rejection_from_below        POC 29660.50 0.671501 0.328499    0.671501      LONG  HIGH_CONF_LONG
1781294372016395000 POC_rejection_from_below        POC 29656.50 0.654328 0.345672    0.654328      LONG  HIGH_CONF_LONG
1781294377832544000 POC_rejection_from_above        POC 29664.25 0.484096 0.515904    0.515904      FLAT   LOW_CONF_FLAT
1781294382872407000 POC_rejection_from_above        POC 29666.00 0.412651 0.587349    0.587349      FLAT   LOW_CONF_FLAT
1781294386331829000 POC_rejection_from_above        POC 29666.00 0.459738 0.540262    0.540262      FLAT   LOW_CONF_FLAT
1781294391133154000 POC_rejection_from_above        POC 29665.00 0.345307 0.654693    0.654693     SHORT HIGH_CONF_SHORT
1781294393490413000 POC_rejection_from_above        POC 29663.25 0.388924 0.611076    0.611076      FLAT   LOW_CONF_FLAT
1781294395518496000 POC_rejection_from_above        POC 29665.00 0.261516 0.738484    0.738484     SHORT HIGH_CONF_SHORT
1781294397010930000 POC_rejection_from_above        POC 29663.25 0.269130 0.730870    0.730870     SHORT HIGH_CONF_SHORT
1781294398187636000 POC_rejection_from_below        POC 29660.25 0.122715 0.877285    0.877285     SHORT HIGH_CONF_SHORT
1781294404769304000 POC_rejection_from_below        POC 29659.75 0.325564 0.674436    0.674436     SHORT HIGH_CONF_SHORT
1781294413900443000 POC_rejection_from_below        POC 29658.25 0.320657 0.679343    0.679343     SHORT HIGH_CONF_SHORT
1781294432123425000 POC_rejection_from_below        POC 29658.50 0.267621 0.732379    0.732379     SHORT HIGH_CONF_SHORT
1781294479298434000 POC_rejection_from_above        POC 29665.00 0.185224 0.814776    0.814776     SHORT HIGH_CONF_SHORT
1781294521982100000 POC_rejection_from_above        POC 29664.75 0.303191 0.696809    0.696809     SHORT HIGH_CONF_SHORT
1781295442401298000 VAH_rejection_from_above        VAH 29728.75 0.256379 0.743621    0.743621     SHORT HIGH_CONF_SHORT
1781295631319065000 VAH_rejection_from_below        VAH 29710.75 0.256800 0.743200    0.743200     SHORT HIGH_CONF_SHORT
```

## Top 20 highest confidence predictions
```
      bar_end_ts_ns            reaction_type level_type    close   p_long  p_short  confidence direction  signal_status
1781024130690182000 LVN_rejection_from_above        LVN 28315.75 0.997543 0.002457    0.997543      LONG HIGH_CONF_LONG
1781024130690182000 LVN_rejection_from_above        LVN 28315.75 0.997543 0.002457    0.997543      LONG HIGH_CONF_LONG
1781024130690182000 LVN_rejection_from_above        LVN 28315.75 0.997543 0.002457    0.997543      LONG HIGH_CONF_LONG
1781024130690182000 LVN_rejection_from_above        LVN 28315.75 0.997543 0.002457    0.997543      LONG HIGH_CONF_LONG
1781023205821682000 HVN_rejection_from_below        HVN 28246.50 0.996980 0.003020    0.996980      LONG HIGH_CONF_LONG
1781023205821682000 HVN_rejection_from_below        HVN 28246.50 0.996980 0.003020    0.996980      LONG HIGH_CONF_LONG
1781023205821682000 HVN_rejection_from_below        HVN 28246.50 0.996980 0.003020    0.996980      LONG HIGH_CONF_LONG
1781023205821682000 HVN_rejection_from_below        HVN 28246.50 0.996980 0.003020    0.996980      LONG HIGH_CONF_LONG
1781023205821682000 HVN_rejection_from_below        HVN 28246.50 0.996980 0.003020    0.996980      LONG HIGH_CONF_LONG
1781023205821682000 HVN_rejection_from_below        HVN 28246.50 0.996980 0.003020    0.996980      LONG HIGH_CONF_LONG
1781023205821682000 HVN_rejection_from_below        HVN 28246.50 0.996980 0.003020    0.996980      LONG HIGH_CONF_LONG
1781023205821682000 HVN_rejection_from_below        HVN 28246.50 0.996980 0.003020    0.996980      LONG HIGH_CONF_LONG
1781023205821682000 HVN_rejection_from_below        HVN 28246.50 0.996980 0.003020    0.996980      LONG HIGH_CONF_LONG
1781023205821682000 HVN_rejection_from_below        HVN 28246.50 0.996980 0.003020    0.996980      LONG HIGH_CONF_LONG
1781023205821682000 HVN_rejection_from_below        HVN 28246.50 0.996980 0.003020    0.996980      LONG HIGH_CONF_LONG
1781023205821682000 HVN_rejection_from_below        HVN 28246.50 0.996980 0.003020    0.996980      LONG HIGH_CONF_LONG
1781023205821682000 HVN_rejection_from_below        HVN 28246.50 0.996980 0.003020    0.996980      LONG HIGH_CONF_LONG
1781023205821682000 HVN_rejection_from_below        HVN 28246.50 0.996980 0.003020    0.996980      LONG HIGH_CONF_LONG
1781023205821682000 HVN_rejection_from_below        HVN 28246.50 0.996980 0.003020    0.996980      LONG HIGH_CONF_LONG
1781023205821682000 LVN_rejection_from_below        LVN 28246.50 0.996980 0.003020    0.996980      LONG HIGH_CONF_LONG
```

## Warnings / Errors
(none)

## Verdict
**PASS**