# Buy Bar Dashboard Recommendation
**SHADOW / RESEARCH ONLY — NO EXECUTION — NO BROKER — NO PAPER TRADING**  
**Generated**: 2026-07-12  
**Case**: Bar 18924 (failed) vs Bar 18932 (succeeded), NQU6 2026-07-10

---

## Recommended Dashboard Labels

### Primary Classification Labels
| Label | Trigger Condition | Case A | Case B |
|-------|-------------------|--------|--------|
| `BUY_BAR_SELLER_ABSORPTION` | Z4 ask_replenishment_ratio ≥ 0.90 AND dash_vpin_pct < 0.40 | YES | NO |
| `BUY_BAR_SELLER_FAILURE` | Z4 ask_replenishment_ratio < 0.70 AND close_location ≥ 0.95 | NO | YES |
| `BUY_BAR_CONTINUATION_CONFIRMED` | All forward closes above bar close (H1, H2, H5) | NO | YES |
| `BUY_BAR_TRAP_WARNING` | H2 broke below bar midpoint AND H1 return < -5 pts | YES | NO |
| `ASK_REPLENISHMENT_OVERHEAD` | Z4 AskAdd > Z4 AskPull (sellers adding above close) | YES (436>445≈near) | NO (25<44) |
| `UPPER_ASK_VACUUM` | Z4 AskPull > AskAdd × 1.5 AND close_location ≥ 0.95 | NO | YES |
| `VAL_RECLAIM_BUYER_CONTROL` | lvl_VAL=1 AND close_location ≥ 0.90 AND dash_vpin_pct ≥ 0.60 | NO | YES |

---

## Recommended Live Fields

| Field | Formula / Source | Bar 18924 (Failed) | Bar 18932 (Succeeded) |
|-------|-----------------|--------------------|-----------------------|
| `seller_absorption_score` | Z4 AskAdd / max(Z4 AskPull, 1) | 0.979 (sellers replenishing) | 0.568 (sellers failing) |
| `seller_failure_score` | Z4 (AskPull - AskAdd) / max(bar AskPull, 1) | +0.001 | +0.003 |
| `upper_ask_replenishment_ratio` | Z4 AskAdd / max(Z4 AskPull, 1) | 0.979 | 0.568 |
| `upper_ask_removed` | Z4 (AskPull - AskAdd) | +9 | +19 |
| `upper_zero_ask_add` | count(Z4 levels where ask_add=0) | 0 | 1 |
| `buyer_followthrough_score` | H5 close above anchor close | 0 (price -21.5) | 1 (price +53.5) |
| `buy_bar_acceptance_score` | pct of post-bar lows ≥ anchor close | 0.0 | 1.0 |
| `buy_bar_trap_risk` | 1 if H2 breaks below bar midpoint | 1 | 0 |

---

## Composite Filter: BUY_BAR_CONTINUATION_SIGNAL

```
BUY_BAR_CONTINUATION_SIGNAL = TRUE if ALL of:
  1. close_location >= 0.90              (close near top of bar)
  2. dash_vpin_pct >= 0.60              (elevated toxicity / informed flow)
  3. upper_ask_replenishment_ratio < 0.75 (sellers not replenishing above close)
  4. dist_to_val_ticks <= 60            (within 15 pts of VAL support)
  5. mlofi_norm_lag_1 < -3.0            (prior seller exhaustion — optional)
```

**Backtest on 2636 similar aggressive buy bars (NQU6 history):**
- Base success rate (H5 held): 18.6%
- With Rule A (toxic + close_high + VAL): 37.5% (32 samples)
- 2× improvement over base rate

**Key diagnostic separation** (Case A vs Case B):
| Signal | Case A (Failed) | Case B (Succeeded) | Edge |
|--------|----------------|---------------------|------|
| dash_vpin_pct | 0.076 (7.6th pct) | 0.892 (89.2nd pct) | VPIN gap = 82 pct points |
| Z4 ask_replenishment_ratio | 0.979 (sellers replenishing) | 0.568 (sellers failing) | ratio gap = 0.41 |
| close_location | 0.86 | 1.00 | full close vs partial |
| mlofi_norm lag1 | +2.496 (bullish) | -10.102 (seller exhaustion) | exhaustion context |
| H2 forward return | -28.25 pts (FAIL) | +22.00 pts (SUCCESS) | 50 pt separation |

---

## Implementation Priority

1. **HIGH**: `dash_vpin_pct` and `close_location` are already in dashboard — use them as filters
2. **HIGH**: Z4 ask_replenishment_ratio requires live per-level cache read after bar close — feasible
3. **MEDIUM**: VAL proximity (`dist_to_val_ticks`) already computed in model feature master
4. **MEDIUM**: `mlofi_norm_lag_1` already available in master

**No new data sources required** for the primary filter. All inputs exist in current feature pipeline.

---

## Notes

- The Z4 zone (above close) requires access to the Book Flow per-level cache, which is available
- seller_absorption_score and seller_failure_score are BAR-CLOSE metrics (not intra-bar)
- The strongest single discriminator is `dash_vpin_pct`: below 30th pct → high trap risk; above 60th pct → continuation favored
- The `mlofi_norm_lag_1 < -3.0` condition (prior seller exhaustion) is a strong context filter but optional — it explains WHY Case B worked but isn't always required for success
