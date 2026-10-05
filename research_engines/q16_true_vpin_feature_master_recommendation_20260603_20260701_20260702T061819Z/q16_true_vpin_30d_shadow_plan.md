# True VPIN 30-Day Shadow Observation Plan
Generated: 2026-07-02T06:31:15Z
SHADOW / RESEARCH ONLY

## Objective
Observe true VPIN signal behavior in live/near-live conditions over 30 trading days
before any Feature Master patching or production promotion.

## Observation period
Start: First trading day after research review approval (suggest 2026-07-07)
End:   30 trading days later (suggest approximately 2026-08-15)
Review: Weekly checkpoint at day 5, 10, 20, 30

## What to log daily
See q16_true_vpin_shadow_log_schema.csv for full column list.

Key daily metrics to watch:
1. **Divergence rate**: Fraction of bars where |true_vpin_pct - cur_vpin_pct| > 0.20
   - Expected: 20–35% based on atlas (V500 W50 state agreement 65%)
   - Alert if > 50% sustained for 3+ days: investigate data quality
   - Alert if < 5% sustained: true VPIN may have degraded to current VPIN

2. **Toxic state agreement**: When cur_vpin=TOXIC, is true_vpin also TOXIC?
   - Expected: ~60–70% agreement at V500 W50
   - Watch for regime where they diverge consistently

3. **Divergence predictiveness**: Do high-divergence bars show better/worse
   forward returns? Track rolling Spearman of divergence vs H10 fwd return.
   - Alert if divergence signal inverts from historical finding

4. **True VPIN coverage**: Confirm true VPIN is available for >= 95% of bars
   - Any coverage drop means raw trades file missing or lagged

5. **Rolling Spearman stability**: true_signed_delta W240 H10 Spearman ρ
   - Should be in range −0.30 to +0.30 based on historical data
   - Alert if persistently outside this range

## Weekly checkpoints
Week 1 (day 5):  Confirm stable coverage, reasonable values, no obvious leakage
Week 2 (day 10): Compare first 10 days hit rate vs prior atlas (±5% tolerance)
Week 3 (day 20): First formal predictive value re-test (fresh 20-day OOS)
Week 4 (day 30): Full review — pass/fail decision for Feature Master inclusion

## Pass criteria for Feature Master inclusion
- Coverage >= 95% of bars
- H10 hit rate for TRUE_SELL_TOXIC_SHORT >= 0.52 on shadow period
- No systematic divergence reversal vs historical findings
- No leakage detected in alignment audit replay
- Architecture option (1 or 2) implemented and reviewed

## Fail criteria (defer inclusion)
- Coverage < 90%
- H10 hit rate below 0.49 (worse than current VPIN)
- Systematic reversal in divergence signal direction
- Any leakage detected

## How to run shadow logging
After each Feature Master bar seal (hook in daemon):
  1. Read latest true_vpin_cache (Option 2) or compute inline (Option 1)
  2. Append one row to `/home/prabh/OFI_Production/shadow_logs/true_vpin_shadow_log.csv`
  3. Realize H5/H10/H20/H40/H80 returns when the future bars seal (lagged append)
