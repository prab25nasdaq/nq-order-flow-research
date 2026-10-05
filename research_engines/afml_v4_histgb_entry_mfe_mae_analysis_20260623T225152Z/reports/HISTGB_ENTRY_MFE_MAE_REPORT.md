HISTGB ENTRY ACT/PASS - MFE/MAE ANALYSIS (v4)
=============================================================================
SOURCE ENGINE: /home/prabh/OFI_Production/research_engines/afml_label_policy_parity_entry_cp_v4_20260623T192440Z
ANALYSIS DIR: /home/prabh/OFI_Production/research_engines/afml_v4_histgb_entry_mfe_mae_analysis_20260623T225152Z
STATUS: SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING
PRODUCTION_FILES_MODIFIED: false

MFE/MAE are reported as POSITIVE magnitudes in NQ points (mfe_points =
size of the best favorable excursion before the existing model's own
fixed +40-bar horizon, day-bounded; mae_points = size of the worst adverse
excursion), side-adjusted by approach direction, derived from v4's own
realized_mfe_side_adjusted/realized_mae_side_adjusted columns (Part E).
There is NO triple-barrier PT/SL in this label policy - every exit is
EXIT_POLICY_TIME by construction (pt_rate=0, sl_rate=0, time_rate=1.0).

ACT/PASS restricted to the 2186 OOF-scored rows (excluded 713
rows never covered by any purge/embargo fold - e.g. the earliest calendar
day, which has no prior day to train from under expanding-window folds).

=============================================================================
1. WHAT IS THE AVERAGE MFE PER HISTGB ACT ENTRY?
=============================================================================
mean_mfe_points = 115.82   median = 116.25
(n_ACT = 1130)

=============================================================================
2. WHAT IS THE AVERAGE MAE PER HISTGB ACT ENTRY?
=============================================================================
mean_mae_points = 59.22   median = 28.75

=============================================================================
3. IS MFE MEANINGFULLY LARGER THAN MAE?
=============================================================================
mfe_mae_ratio (mean_mfe/mean_mae) = 1.96
YES - mean MFE is 2.0x mean MAE for ACT entries. ACT
candidates structurally tend to develop a larger favorable excursion than
adverse excursion before the existing model's own +40-bar horizon resolves.

=============================================================================
4. IS THIS TRUE IN CLEAN OOS ONLY?
=============================================================================
Genuinely-OOS ACT entries (n=780):
  mean_mfe_points=115.06  mean_mae_points=61.59  ratio=1.87
YES - the MFE>MAE pattern HOLDS on the genuinely-OOS subset,
consistent with the full-population finding in Q3 (n=780 is a meaningfully
large genuinely-OOS sample, not a small-n artifact).

=============================================================================
5. DOES LONG OR SHORT BEHAVE BETTER?
=============================================================================
LONG  (n=362): mean_mfe=110.08  mean_mae=85.27  ratio=1.29  mean_exit=10.31  hit_rate=39.8%
SHORT (n=768): mean_mfe=118.52  mean_mae=46.95  ratio=2.52  mean_exit=68.41  hit_rate=86.1%

SHORT shows the better MFE/MAE ratio and
SHORT shows the better mean realized exit return in this sample.

=============================================================================
6. WHICH REACTION TYPES HAVE THE BEST PATH QUALITY?
=============================================================================
(full table: outputs/histgb_entry_mfe_mae_by_reaction_type.csv)
                   group  n_entries  mean_mfe_points  mean_mae_points  mfe_mae_ratio  mean_exit_points  hit_rate
LVN_rejection_from_below        393       127.277990        34.652036       3.673031         66.824427  0.882952
HVN_rejection_from_below        343       112.119534        60.702624       1.847029         74.838921  0.868805
HVN_rejection_from_above        202       113.446782        84.877475       1.336595          1.670792  0.326733
LVN_rejection_from_above        144       109.614583        87.517361       1.252490         24.871528  0.500000
POC_rejection_from_below         17        66.294118        54.794118       1.209877          5.382353  0.352941
VAH_rejection_from_below         10        78.875000        37.575000       2.099135         45.050000  0.700000
POC_rejection_from_above          8        63.687500        67.500000       0.943519          0.500000  0.500000
VAL_rejection_from_below          5       126.300000        61.750000       2.045344         13.750000  0.600000
VAH_rejection_from_above          4        81.062500        82.062500       0.987814        -42.125000  0.250000
VAL_rejection_from_above          4        78.375000        62.875000       1.246521         -5.562500  0.250000

Best mfe_mae_ratio: LVN_rejection_from_below (ratio=3.67, n=393)
- read with sample size in mind for the smaller-n reaction types.

=============================================================================
7. WHICH LEVEL TYPES HAVE THE BEST PATH QUALITY?
=============================================================================
(full table: outputs/histgb_entry_mfe_mae_by_level_type.csv)
group  n_entries  mean_mfe_points  mean_mae_points  mfe_mae_ratio  mean_exit_points  hit_rate
  HVN        545       112.611468        69.662844       1.616521         47.719725  0.667890
  LVN        537       122.541434        48.828212       2.509644         55.574488  0.780261
  POC         25        65.460000        58.860000       1.112130          3.820000  0.400000
  VAH         14        79.500000        50.285714       1.580966         20.142857  0.571429
  VAL          9       105.000000        62.250000       1.686747          5.166667  0.444444

Best mfe_mae_ratio: LVN (ratio=2.51, n=537)

By gate status (outputs/histgb_entry_mfe_mae_by_gate_status.csv):
           group  n_entries  mean_mfe_points  mean_mae_points  mfe_mae_ratio  mean_exit_points
     PRIMARY_USE        567       111.103616        69.153880       1.606614         46.372575
 SECONDARY_WATCH        410       124.749390        35.487195       3.515335         64.276829
BLOCKED_NEGATIVE        153       109.343137        86.031046       1.270973         23.712418

=============================================================================
8. DOES HIGHER HISTGB PROBABILITY PRODUCE BETTER MFE/MAE?
=============================================================================
(full table: outputs/histgb_entry_mfe_mae_by_probability_bucket.csv)
    group  mean_mfe_points  mean_mae_points  mfe_mae_ratio  n_entries
0.50-0.55       113.681818        51.629870       2.201861         77
0.55-0.60       140.182203        53.314972       2.629322        177
0.60-0.65       117.675824        66.115385       1.779855         91
0.65-0.70        61.578431       153.480392       0.401214         51
    0.70+       113.702657        54.041553       2.103986        734

NO CLEAR MONOTONIC TREND in mfe_mae_ratio across confidence
buckets in this sample - read together with each bucket's n_entries, since
the highest-confidence bucket is also the smallest.

=============================================================================
SESSION / DAY BREAKDOWN
=============================================================================
By native existing-model session label (outputs/histgb_entry_mfe_mae_by_session.csv):
  group  n_entries  mean_mfe_points  mean_mae_points  mfe_mae_ratio  mean_exit_points
   Asia        539       140.987941        62.519017       2.255121         79.448980
     EU        181        86.480663        47.687845       1.813474         16.899171
  US_AM         38        50.763158        48.032895       1.056842         21.828947
US_Late        211       124.906398        81.721564       1.528439         31.937204
US_Open         70        66.628571        45.142857       1.475949         17.096429
  US_PM         91        68.997253        25.989011       2.654863         17.890110

Derived 3-bucket grouping (Asia_Overnight = Asia+US_Late, London = EU, US = US_Open+US_AM+US_PM):
         group  n_entries  mean_mfe_points  mean_mae_points  mfe_mae_ratio  mean_exit_points
Asia_Overnight        750       136.463667        67.921333       2.009143         66.082333
        London        181        86.480663        47.687845       1.813474         16.899171
            US        199        64.682161        36.935930       1.751199         18.363065

Per-day breakdown (outputs/histgb_entry_mfe_mae_by_day.csv):
   group  n_entries  mean_mfe_points  mean_mae_points  mfe_mae_ratio  mean_exit_points  hit_rate
20260616         84       113.342262        26.107143       4.341427         55.464286  0.892857
20260617        131       106.721374        68.314885       1.562198         32.711832  0.610687
20260618        135       130.579630        57.314815       2.278288         47.707407  0.748148
20260621         89       135.682584        84.106742       1.613219         47.564607  0.685393
20260622         10       193.900000        20.250000       9.575309        131.350000  0.900000
20260623        681       111.201542        59.258443       1.876552         51.899046  0.703377

WORST day by mean_exit_points: 20260617 (mean_exit=32.71, n=131)
BEST  day by mean_exit_points: 20260622 (mean_exit=131.35, n=10)

=============================================================================
9. WORST-FOLD DIAGNOSTICS (Q10)
=============================================================================
Worst HistGB fold: test_day=20260617, fold MCC=-0.159
ACT entries in that fold: n=131
  mean_mfe_points=106.72  mean_mae_points=68.31
  mfe_mae_ratio=1.56
  reaction_type distribution: {'HVN_rejection_from_above': 54, 'HVN_rejection_from_below': 43, 'LVN_rejection_from_below': 17, 'LVN_rejection_from_above': 10, 'POC_rejection_from_below': 4, 'VAL_rejection_from_above': 2, 'VAH_rejection_from_below': 1}
  level_type distribution: {'HVN': 97, 'LVN': 27, 'POC': 4, 'VAL': 2, 'VAH': 1}
  side distribution (1=LONG,-1=SHORT): {1: 66, -1: 65}

IS THE WORST FOLD BAD BECAUSE MFE IS LOW, MAE IS HIGH, OR BOTH?
  worst-fold mean_mfe=106.72 vs all-ACT mean_mfe=115.82
    (LOWER than the overall ACT population)
  worst-fold mean_mae=68.31 vs all-ACT mean_mae=59.22
    (HIGHER than the overall ACT population)
  Among 51 losing trades (exit_points<=0) in this fold: 51 had MFE below the
  overall-ACT median (116.25 pts - "bad entry," the trade never had a good
  opportunity) vs 0 had MFE above that median but still lost ("bad holding path" -
  a real favorable excursion existed but was given back by the time the +40-bar horizon
  resolved).

=============================================================================
10. DOES HISTGB ACT IMPROVE PATH QUALITY VS PASS?
=============================================================================
ACT  (n=1130): mean_mfe=115.82  mean_mae=59.22  ratio=1.96  mean_exit=49.80  hit_rate=71.2%
PASS (n=1056): mean_mfe=106.58  mean_mae=76.27  ratio=1.40  mean_exit=29.35  hit_rate=56.5%

ACT vs PASS:
  higher MFE:          True
  lower MAE:            True
  better MFE/MAE ratio: True
  better final outcome (mean_exit_points): True
  better hit_rate:      True

=============================================================================
11. DOES THIS SUPPORT FURTHER RESEARCH, OR IS IT BLOCKED?
=============================================================================
SUPPORTS FURTHER RESEARCH (not blocked). The HistGB ACT population shows a
consistently larger MFE than MAE (Q3), this holds on a substantial
genuinely-OOS sample (Q4), and ACT entries show better path quality than PASS
candidates (Q10) - directionally consistent with the positive mean-of-folds
MCC already reported in the v4 final report. The worst fold (test_day
20260617) shows a real, identifiable degradation, and per Q9 is
attributable to ENTIRELY bad entries - all 51 losing trades in this fold had MFE below the overall-ACT median, i.e. the setup never developed a real favorable excursion in the first place; none were a case of giving back an already-favorable move - useful diagnostic signal for follow-up
research (e.g. tightening the entry filter for this fold's dominant
reaction-type mix), not a disqualifying failure.

NOT A TRADABILITY CLAIM. No PT/SL exists in this label policy, no slippage/
commission/execution modeling has been applied, and the effective
independent sample count (reported in the v4 final report, ~85) is far
smaller than the raw row counts shown here. PAPER_TRADING_ENABLED remains
false throughout.

=============================================================================
FINAL FIELDS
=============================================================================
PRODUCTION_FILES_MODIFIED: false
DASHBOARD_CODE_MODIFIED: false
BOOK_FLOW_CODE_MODIFIED: false
TRADING_ENABLED: false
BROKER_CONNECTED: false
PAPER_TRADING_ENABLED: false
OVERALL: PASS
