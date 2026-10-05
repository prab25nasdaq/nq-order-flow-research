# Level-Reaction Shadow Inference Report (ALL REACTIONS)

_run: 2026-06-14T02:26:39.706797+00:00_

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
- Reaction mode: all_trained
- Bars loaded: 11,960

## Reactions
- All trained (16): ['HVN_absorption', 'HVN_neutral_touch', 'HVN_rejection_from_above', 'HVN_rejection_from_below', 'LVN_absorption', 'LVN_neutral_touch', 'LVN_rejection_from_above', 'LVN_rejection_from_below', 'POC_absorption', 'POC_rejection_from_above', 'POC_rejection_from_below', 'VAH_absorption', 'VAH_rejection_from_above', 'VAH_rejection_from_below', 'VAL_rejection_from_above', 'VAL_rejection_from_below']
- Detected in window: ['HVN_absorption', 'HVN_neutral_touch', 'HVN_rejection_from_above', 'HVN_rejection_from_below', 'LVN_absorption', 'LVN_neutral_touch', 'LVN_rejection_from_above', 'LVN_rejection_from_below', 'POC_absorption', 'POC_rejection_from_above', 'POC_rejection_from_below', 'VAH_rejection_from_above', 'VAH_rejection_from_below', 'VAL_rejection_from_above', 'VAL_rejection_from_below']
- Trained present:    ['HVN_rejection_from_below', 'HVN_rejection_from_above', 'LVN_rejection_from_below', 'LVN_rejection_from_above', 'POC_rejection_from_below', 'POC_rejection_from_above', 'VAH_rejection_from_below', 'HVN_absorption', 'VAH_rejection_from_above', 'VAL_rejection_from_above', 'VAL_rejection_from_below', 'HVN_neutral_touch', 'LVN_absorption', 'POC_absorption', 'LVN_neutral_touch']
- Trained missing:    ['VAH_absorption']
- Total events scored: 58,774

## Per-reaction summary
```
           reaction_type     n  trained_reaction training_gate_status  n_training_for_reaction  mean_p_long  mean_p_short  mean_confidence  pct_conf_ge_threshold  pct_high_conf_long  pct_high_conf_short                    latest_timestamp latest_direction  latest_confidence
HVN_rejection_from_below 23213              True          PRIMARY_USE                    25080       0.5427        0.4573           0.7346                  69.00               39.78                29.21 2026-06-12 13:45:57.364607000+00:00             LONG             0.8970
HVN_rejection_from_above 22762              True          PRIMARY_USE                    24783       0.5166        0.4834           0.7404                  70.75               37.76                32.99 2026-06-12 13:46:07.762444000+00:00             LONG             0.8597
LVN_rejection_from_below  5871              True     BLOCKED_NEGATIVE                     6726       0.4072        0.5928           0.7633                  73.31               22.48                50.83 2026-06-12 15:26:00.447583000+00:00             FLAT             0.5120
LVN_rejection_from_above  5536              True  EXPLORATORY_SMALL_N                     6471       0.5915        0.4085           0.7708                  77.29               53.11                24.19 2026-06-12 13:46:26.615317000+00:00             LONG             0.9242
POC_rejection_from_above   362              True     BLOCKED_UNSTABLE                      295       0.5552        0.4448           0.6212                  36.19               26.24                 9.94 2026-06-12 20:02:01.982100000+00:00            SHORT             0.6968
POC_rejection_from_below   353              True      SECONDARY_WATCH                      312       0.5411        0.4589           0.6238                  37.96               25.50                12.46 2026-06-12 20:00:32.123425000+00:00            SHORT             0.7324
VAH_rejection_from_below   152              True     BLOCKED_NEGATIVE                      117       0.4759        0.5241           0.6841                  50.00               22.37                27.63 2026-06-12 20:20:31.319065000+00:00            SHORT             0.7432
VAL_rejection_from_above   135              True  EXPLORATORY_SMALL_N                       83       0.6461        0.3539           0.6837                  63.70               58.52                 5.19 2026-06-12 16:07:32.390916000+00:00             LONG             0.6889
VAH_rejection_from_above   132              True     BLOCKED_NEGATIVE                       87       0.4790        0.5210           0.6885                  50.76               24.24                26.52 2026-06-12 20:17:22.401298000+00:00            SHORT             0.7436
VAL_rejection_from_below   132              True     BLOCKED_NEGATIVE                       72       0.6060        0.3940           0.6623                  56.06               47.73                 8.33 2026-06-12 16:06:05.384058000+00:00             LONG             0.7143
          HVN_absorption   103              True  EXPLORATORY_SMALL_N                       94       0.5577        0.4423           0.7090                  68.93               44.66                24.27 2026-06-11 17:59:20.790224000+00:00             LONG             0.8176
          LVN_absorption    10              True              SMALL_N                        8       0.6101        0.3899           0.6101                   0.00                0.00                 0.00 2026-06-10 19:15:41.028872000+00:00             FLAT             0.6101
       HVN_neutral_touch     9              True              SMALL_N                       10       0.4274        0.5726           0.7053                  77.78               22.22                55.56 2026-06-09 16:54:32.285111000+00:00             LONG             0.8639
       LVN_neutral_touch     2              True              SMALL_N                        2       0.2503        0.7497           0.7497                 100.00                0.00               100.00 2026-06-05 16:04:02.832898000+00:00            SHORT             0.6986
          POC_absorption     2              True              SMALL_N                        3       0.3937        0.6063           0.6063                  50.00                0.00                50.00 2026-06-09 19:59:51.464172000+00:00             FLAT             0.5395
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
      bar_end_ts_ns            reaction_type level_type    close  p_long  p_short  confidence direction   signal_status
1781221561965769000 LVN_rejection_from_above        LVN 29580.75 0.01043  0.98957     0.98957     SHORT HIGH_CONF_SHORT
1781221561965769000 LVN_rejection_from_above        LVN 29580.75 0.01043  0.98957     0.98957     SHORT HIGH_CONF_SHORT
1781221561965769000 LVN_rejection_from_above        LVN 29580.75 0.01043  0.98957     0.98957     SHORT HIGH_CONF_SHORT
1781221561965769000 LVN_rejection_from_above        LVN 29580.75 0.01043  0.98957     0.98957     SHORT HIGH_CONF_SHORT
1781221561965769000 LVN_rejection_from_above        LVN 29580.75 0.01043  0.98957     0.98957     SHORT HIGH_CONF_SHORT
1781221561965769000 LVN_rejection_from_above        LVN 29580.75 0.01043  0.98957     0.98957     SHORT HIGH_CONF_SHORT
1781221561965769000 LVN_rejection_from_above        LVN 29580.75 0.01043  0.98957     0.98957     SHORT HIGH_CONF_SHORT
1781221561965769000 LVN_rejection_from_above        LVN 29580.75 0.01043  0.98957     0.98957     SHORT HIGH_CONF_SHORT
1781221561965769000 LVN_rejection_from_above        LVN 29580.75 0.01043  0.98957     0.98957     SHORT HIGH_CONF_SHORT
1781221561965769000 LVN_rejection_from_above        LVN 29580.75 0.01043  0.98957     0.98957     SHORT HIGH_CONF_SHORT
1781221561965769000 LVN_rejection_from_above        LVN 29580.75 0.01043  0.98957     0.98957     SHORT HIGH_CONF_SHORT
1781221561965769000 LVN_rejection_from_above        LVN 29580.75 0.01043  0.98957     0.98957     SHORT HIGH_CONF_SHORT
1781221561965769000 LVN_rejection_from_above        LVN 29580.75 0.01043  0.98957     0.98957     SHORT HIGH_CONF_SHORT
1781221561965769000 LVN_rejection_from_above        LVN 29580.75 0.01043  0.98957     0.98957     SHORT HIGH_CONF_SHORT
1781221561965769000 LVN_rejection_from_above        LVN 29580.75 0.01043  0.98957     0.98957     SHORT HIGH_CONF_SHORT
1781221561965769000 LVN_rejection_from_above        LVN 29580.75 0.01043  0.98957     0.98957     SHORT HIGH_CONF_SHORT
1781221561965769000 HVN_rejection_from_above        HVN 29580.75 0.01043  0.98957     0.98957     SHORT HIGH_CONF_SHORT
1781221561965769000 HVN_rejection_from_above        HVN 29580.75 0.01043  0.98957     0.98957     SHORT HIGH_CONF_SHORT
1781221561965769000 LVN_rejection_from_above        LVN 29580.75 0.01043  0.98957     0.98957     SHORT HIGH_CONF_SHORT
1781221561965769000 LVN_rejection_from_above        LVN 29580.75 0.01043  0.98957     0.98957     SHORT HIGH_CONF_SHORT
```

## Warnings / Errors
(none)

## Verdict
**PASS**