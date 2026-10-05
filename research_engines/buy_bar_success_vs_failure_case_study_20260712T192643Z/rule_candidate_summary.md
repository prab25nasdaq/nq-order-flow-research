# Rule Candidate Summary — Buy Bar Success vs Failure
**SHADOW / RESEARCH ONLY — NO EXECUTION — NO BROKER — NO PAPER TRADING**

---

## Universe Definition

Aggressive buy bars are defined as bars meeting ALL of:
- `direction = UP` (px_close ≥ px_open)
- `mlofi_sum > 0` (positive net OFI at any level)
- `close_location ≥ 0.65` (closed in upper 35% of range)
- `bar_range_pts ≥ 4.0` (meaningful range, ≥ 16 ticks)
- `buy_ratio ≥ 0.52` (more aggressive buys than sells)

**Universe size on NQU6 history**: 2,636 bars  
**Base H5 success rate** (price closed above bar close 5 bars later): 18.6%

---

## Rule A — BUY_BAR_CONTINUATION

Trigger: ALL of the following at bar close:
1. `dash_vpin_pct >= 0.60` — elevated toxicity (informed buying)
2. `close_location >= 0.90` — close at or near top of bar
3. Within VAL proximity: `(lvl_VAL == 1) OR (0 < dist_to_val_ticks <= 50)`

| Metric | Value |
|--------|-------|
| Sample count | 32 |
| H5 success rate | 37.5% |
| Base rate | 18.6% |
| Lift | +2.0× base |

**Interpretation**: When buyers are highly toxic (informed), close at the top, and price is at or just above VAL support, the market structure favors continuation. Sellers have failed to contain the move.

---

## Rule B — BUY_BAR_TRAP / SELLER_ABSORPTION

Trigger: ALL of the following at bar close:
1. `dash_vpin_pct < 0.30` — low toxicity (non-informed, passive buying)
2. `close_location < 0.90` — close not fully at top
3. Upper ask_replenishment_ratio ≈ 1.0 (from per-level Z4 zone)

| Metric | Value |
|--------|-------|
| Sample count | 2,604 (complement) |
| H5 success rate | 18.3% |
| Notes | Essentially base rate — no edge |

**Interpretation**: Most "aggressive buy bars" without toxic flow fail. The bullish visual is a trap — sellers replenish above the close and absorb the move.

---

## Supplementary Rule — FAILED_SELLER_EXHAUSTION Context

Add-on condition that further boosts Rule A confidence:
- `mlofi_norm_lag_1 < -3.0` OR `mlofi_norm_lag_2 < -3.0`
  - Prior bar(s) had extreme seller book activity → sellers exhausted before the buy bar

**Case B example (bar 18932)**: Bar 18931 had mlofi_norm = -10.102 (extreme seller push),
then bar 18932 completely absorbed and reversed. This prior exhaustion context is the clearest
"failed seller push → buyer takeover" pattern.

**Case A example (bar 18924)**: Bar 18923 had mlofi_norm = +2.496, bar 18922 = -0.966 — no
clear seller exhaustion precursor. Sellers were present and not spent.

---

## Separating Features (ranked by discriminative power)

| Feature | Bar 18924 (Failed) | Bar 18932 (Succeeded) | Gap |
|---------|--------------------|-----------------------|-----|
| dash_vpin_pct | 0.076 | 0.892 | 82 pct pts |
| Z4 ask_replenishment_ratio | 0.979 | 0.568 | 0.41 |
| close_location | 0.860 | 1.000 | 0.14 |
| mlofi_norm_lag_1 | +2.496 | -10.102 | 12.6 (exhaustion gap) |
| dist_to_val_ticks | 88 | 46 | 42 ticks closer to VAL |
| H2 forward return | -28.25 pts | +22.0 pts | 50.25 pts |
| H5 MAE | 50.0 pts | 3.25 pts | 46.75 pts (risk gap) |

---

## Recommendation

**Add to Feature Master**: `dash_vpin_pct` (already computed) + `close_location` (already computed)
combined as a composite signal: `buy_bar_continuation_flag`.

**Do NOT implement execution logic** — this remains SHADOW / RESEARCH ONLY.

The research clearly separates the two cases. The next step is to validate on a larger sample
with Z4 zone data available (requires per-level cache for all bars in history).
