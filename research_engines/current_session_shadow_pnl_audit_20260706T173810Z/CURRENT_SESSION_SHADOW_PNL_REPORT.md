# Current Session Shadow PnL Audit
**SHADOW_THEORETICAL_PNL ONLY — NO EXECUTION — NO BROKER — NO PAPER TRADING**
**Audit run**: 2026-07-06T18:00:51.866272+00:00
**Session**: 20260705

---

## CRITICAL DISCLAIMERS
- All figures are SHADOW_THEORETICAL_PNL — no real trades executed
- trading_enabled = False
- shadow_only = True
- roll_quality_flag = ROLLOVER_WARMUP_LOW_SAMPLE
- ROLLOVER_WARMUP_LOW_SAMPLE: model may have reduced calibration during roll

---

## Q1: CURRENT SESSION DATE AND TIMESTAMP RANGE

| Field | Value |
|-------|-------|
| session_date | 20260705 |
| Calendar date (UTC) | 2026-07-06 (NQU6 session labeled as Jul 5 internally) |
| Session start bar (global) | 14643 |
| Session end bar (global) | 15338 |
| Master latest bar | 15341 |
| Master latest close | 29911.0 |
| Master latest timestamp | 2026-07-06 18:00:44.083646000+00:00 |
| Inference run UTC | 2026-07-06T18:00:51.866272+00:00 |

---

## Q2: HOW MANY MODEL EVENT ROWS OCCURRED?

**186 event rows** in the current session.

| Gate Status | Rows |
|-------------|------|
| PRIMARY_USE | 94 |
| BLOCKED_NEGATIVE | 55 |
| SECONDARY_WATCH | 37 |

Reaction types:
| POC_rejection_from_below | 37 |
| POC_rejection_from_above | 37 |
| VAL_rejection_from_above | 30 |
| VAL_rejection_from_below | 25 |
| LVN_rejection_from_above | 23 |
| LVN_rejection_from_below | 14 |
| VAH_rejection_from_below | 11 |
| VAH_rejection_from_above | 9 |

---

## Q3: HOW MANY INDEPENDENT BAR DECISIONS OCCURRED?

**152 unique bar decisions** (deduplicated from 186 event rows)
- LONG decisions: 14
- SHORT decisions: 27
- FLAT (below threshold 0.65): 56
- BLOCKED (all rows blocked): 55

Bar-level dedup is the **honest metric** — multiple level events on the same bar count as ONE trade signal.

---

## Q4: HOW MUCH DID MODEL-ONLY MAKE/LOSE IN POINTS? (H40)

**SHADOW_THEORETICAL_PNL — Model Only (V1_H40, threshold=0.65):**
- Trades matured: 41
- Total raw points: +435.25
- Total net points: **+435.25**
- Hit rate: 75.6%
- Avg win: +41.40 pts
- Avg loss: -84.82 pts
- Profit factor: 1.513
- Max drawdown: -694.25 pts

---

## Q5: USD PnL FOR 1 NQ CONTRACT (SHADOW ONLY)

**SHADOW_THEORETICAL_PNL — 1 NQ, zero slippage, H40:**
- Gross USD: $+8,705.00
- Net USD:   **$+8,705.00**

---

## Q6: USD PnL FOR 1 MNQ CONTRACT (SHADOW ONLY)

**SHADOW_THEORETICAL_PNL — 1 MNQ, zero slippage, H40:**
- Gross USD: $+870.50
- Net USD:   **$+870.50**

---

## Q7: COST/SLIPPAGE SENSITIVITY (1 NQ, H40)

 slip_ticks  n_contracts  total_net_pts  hit_rate  net_usd_NQ
          0            1         435.25      75.6      8705.0
          1            1         425.00      75.6      8500.0
          2            1         414.75      75.6      8295.0
          4            1         394.25      75.6      7885.0

---

## THRESHOLD SENSITIVITY (1 NQ, H40)

 threshold  n_trades  total_net_pts  hit_rate  profit_factor  net_usd_NQ
      0.55        77         126.00      68.8          1.078      2520.0
      0.60        55         141.50      67.3          1.117      2830.0
      0.65        41         435.25      75.6          1.513      8705.0
      0.70        30         901.50      90.0          4.828     18030.0
      0.75        25         932.00      92.0          9.855     18640.0
      0.80        23        1001.50      95.7        191.762     20030.0

---

## Q8: HOW DID LONG TRADES PERFORM?

 side  n  hit_rate  total_net_pts  avg_pts  avg_win_pts  avg_loss_pts  best_win_pts  worst_loss_pts  avg_MFE  avg_MAE  total_NQ_USD  total_MNQ_USD       best_reaction_type  best_rxn_pts      worst_reaction_type  worst_rxn_pts best_level_type worst_level_type
 LONG 14      35.7        -665.75   -47.55        35.45        -93.67         94.25         -130.25    32.00    79.43      -13315.0        -1331.5 LVN_rejection_from_above         94.25 POC_rejection_from_below        -536.75             LVN              POC
SHORT 27      96.3        1101.00    40.78        42.55         -5.25         72.25           -5.25    56.68    18.17       22020.0         2202.0 VAH_rejection_from_below        413.25 LVN_rejection_from_below         104.25             VAH              LVN

---

## Q9: HOW DID SHORT TRADES PERFORM?

See Q8 table above (SHORT row).

---

## Q10: WHICH REACTION TYPES MADE MONEY?

Reaction type PnL (V1_H40 main variant):
                reaction  total_pts  avg_pts  n
VAH_rejection_from_below     413.25    37.57 11
VAH_rejection_from_above     401.25    44.58  9
LVN_rejection_from_below     104.25    52.12  2
LVN_rejection_from_above      94.25    94.25  1
POC_rejection_from_above     -41.00    -5.86  7
POC_rejection_from_below    -536.75   -48.80 11

---

## Q11: WHICH REACTION TYPES LOST MONEY?

See table above — bottom rows.

---

## Q12: WHICH LEVEL TYPES MADE MONEY?

Level type PnL (V1_H40):
level  total_pts  avg_pts  n
  VAH     814.50    40.73 20
  LVN     198.50    66.17  3
  POC    -577.75   -32.10 18

---

## Q13: DID OFI/BOOKSWITCH/TOXICFLOW/TRAVEL FILTERS IMPROVE PnL?

Filter comparison (V1_H40 baseline):
                 filter  n_trades  total_net_pts  hit_rate  profit_factor  net_usd_NQ
          F1_MODEL_ONLY        41         435.25      75.6          1.513      8705.0
       F2_NO_GATE_BLOCK        41         435.25      75.6          1.513      8705.0
       F3_OFI_DIR_AGREE         0            NaN       0.0            NaN         0.0
      F4_NO_SR_CONFLICT        36        1003.50      86.1          4.584     20070.0
 F5_BOOK_SWITCH_CONFIRM         2        -223.25       0.0          0.000     -4465.0
  F6_TOXIC_FLOW_CONFIRM        14        -665.75      35.7          0.210    -13315.0
   F7_PRIMARY_GATE_ONLY        30         972.00      90.0          5.254     19440.0
F8_ALL_FILTERS_COMBINED         0            NaN       0.0            NaN         0.0

**Best filter**: F4_NO_SR_CONFLICT with +1003.50 pts

---

## Q14: HOW MUCH DID BLOCKERS SAVE OR COST?

Opportunity cost analysis (delta vs model-only):
                 filter  n_trades_base  n_trades_filtered  trades_removed  pts_base  pts_filtered  pts_delta  usd_delta_NQ  filter_helped
       F2_NO_GATE_BLOCK             41                 41               0    435.25        435.25       0.00           0.0          False
       F3_OFI_DIR_AGREE             41                  0              41    435.25          0.00    -435.25       -8705.0          False
      F4_NO_SR_CONFLICT             41                 36               5    435.25       1003.50     568.25       11365.0           True
 F5_BOOK_SWITCH_CONFIRM             41                  2              39    435.25       -223.25    -658.50      -13170.0          False
  F6_TOXIC_FLOW_CONFIRM             41                 14              27    435.25       -665.75   -1101.00      -22020.0          False
   F7_PRIMARY_GATE_ONLY             41                 30              11    435.25        972.00     536.75       10735.0           True
F8_ALL_FILTERS_COMBINED             41                  0              41    435.25          0.00    -435.25       -8705.0          False

---

## Q15: HOW MUCH MFE WAS LEFT ON TABLE?

MFE is decomposed by outcome — mixing winners and losers into one number is misleading
because losers need a better STOP rule, not a better target.

**Winners (31 trades) — exit-too-early problem:**

| Metric | Value |
|--------|-------|
| Realized (wins) | +1283.50 pts |
| MFE available (wins) | 1786.50 pts |
| MFE capture ratio (wins only) | **71.8%** |
| Left on table — winners | **503.00 pts  ($+10,060 NQ shadow)** |

**Losers (10 trades) — stop placement problem:**

| Metric | Value |
|--------|-------|
| Realized (losses) | -848.25 pts |
| MFE before reversal (losers) | 191.75 pts |
| Saved if stopped at MFE peak | **1040.00 pts  ($+20,800 NQ shadow)** |

**Combined exit improvement potential: 1543.00 pts  ($+30,860 NQ shadow)**

| Horizon | Pts |
|---------|-----|
| H10 | +137.50 |
| H40 | +435.25 |
| H40 gave back vs H10 | False |

---

## Q16: DID MODEL FAIL IN ANY SPECIFIC PHASE?

See pnl_by_session_phase.csv and pnl_by_hour.csv for breakdown.

Key concern: **ROLLOVER_WARMUP_LOW_SAMPLE** flag active.
During roll warmup, the model has fewer training samples for the new contract,
potentially reducing calibration accuracy. All signals should be treated with
additional skepticism during this period.

---

## Q17: IS THE RESULT PRODUCTION-READY?

**NO — shadow only. Results are theoretical and subject to:**
1. ROLLOVER_WARMUP_LOW_SAMPLE — reduced calibration
2. SECONDARY_WATCH gate status on many signals
3. Fixed 500-contract volume bars (actual fills would depend on book depth)
4. No slippage, no commissions assumed in baseline
5. Back-adjusted continuous prices — roll offsets affect raw USD values
6. Single session (current session only) — insufficient OOS validation window

Minimum required before any paper trading consideration:
- Validated over 20+ sessions
- PBO/DSR applied
- PRIMARY_USE gate status only
- Signal confirmed by OFI Level Decision dir agreement
- No ROLLOVER_WARMUP period

---

## ALL VARIANTS SUMMARY

             variant  n_trades  total_net_pts  hit_rate  profit_factor  net_usd_NQ  max_drawdown_pts
              V1_H40        41         435.25      75.6          1.513      8705.0           -694.25
              V2_H20        41          86.50      63.4          1.159      1730.0           -388.25
              V3_H10        41         137.50      56.1          1.419      2750.0           -192.75
         V4_NEXT_BAR        41          52.75      63.4          1.521      1055.0            -18.00
V5_ONE_AT_A_TIME_H40         7         114.25      71.4          2.041      2285.0            -97.25
             V6_FLIP        17         254.50      88.2          2.838      5090.0           -132.50
  V7_CONF_SCALED_H40        41         435.25      75.6          1.513     13717.5           -694.25

---

## FINAL STATUS

```
PRODUCTION_FILES_MODIFIED:          false
DASHBOARD_CODE_MODIFIED:            false
FEATURE_MASTER_CODE_MODIFIED:       false
BOOK_FLOW_CODE_MODIFIED:            false
MODEL_ARTIFACTS_MODIFIED:           false
ACTIVE_MODEL_POINTER_CHANGED:       false
TRADING_ENABLED:                    false
BROKER_CONNECTED:                   false
PAPER_TRADING_ENABLED:              false

CURRENT_SESSION_DATE:               20260705
CURRENT_SESSION_START:              bar_global=14643
CURRENT_SESSION_END:                bar_global=15338
EVENT_ROWS:                         186
BAR_LEVEL_DECISIONS:                152 (14L / 27S / 56F / 55BLK)
MODEL_ONLY_POINTS_H40:              +435.25
MODEL_ONLY_USD_NQ_1_CONTRACT:       $+8,705.00 SHADOW_THEORETICAL_PNL
MODEL_ONLY_USD_MNQ_1_CONTRACT:      $+870.50 SHADOW_THEORETICAL_PNL
BEST_FILTER:                        F4_NO_SR_CONFLICT
BEST_FILTER_POINTS:                 +1003.50
BEST_FILTER_USD_NQ_1_CONTRACT:      $+20,070.00 SHADOW_THEORETICAL_PNL
LONG_POINTS_H40:                    -665.75
SHORT_POINTS_H40:                   1101.0
MAX_DRAWDOWN_POINTS:                -694.25
MFE_LEFT_ON_TABLE_WINNERS_POINTS:   503.00
SAVED_IF_STOPPED_AT_MFE_LOSERS:    1040.00
TOTAL_EXIT_IMPROVEMENT_POINTS:     1543.00
ROLL_QUALITY_WARNING:               ROLLOVER_WARMUP_LOW_SAMPLE
PRODUCTION_READY:                   false
PAPER_TRADING_READY:                false
OVERALL:                            PASS
```
