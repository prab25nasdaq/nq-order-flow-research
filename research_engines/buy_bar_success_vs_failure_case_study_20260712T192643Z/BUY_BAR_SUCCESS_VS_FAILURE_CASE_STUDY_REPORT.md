# Buy Bar Success vs Failure — Case Study Report
**SHADOW / RESEARCH ONLY — NO EXECUTION — NO BROKER — NO PAPER TRADING**  
**Symbol**: NQU6  
**Date**: 2026-07-10  
**Generated**: 2026-07-12  
**Case A**: Bar 18924 — Aggressive buy bar that FAILED (sellers regained control)  
**Case B**: Bar 18932 — Aggressive buy bar that SUCCEEDED (sellers failed, buyers took full control)

---

## Executive Summary

Two visually similar bullish order-flow bars on the same session (2026-07-10, US_AM) produced
opposite outcomes within 1–2 bars. Both bars had positive signed_flow and bullish closes. One
resulted in an immediate 28-pt reversal. The other produced a 53-pt continuation with only 3.25
pts of adverse movement across 40 forward bars.

The difference was NOT the OFI signal itself. It was:
1. **Toxicity** (informed vs uninformed flow)
2. **Ask replenishment above close** (seller re-entry vs seller failure)
3. **Prior seller exhaustion context** (sellers spent vs sellers fresh)
4. **VAL proximity** (buying from support vs buying into stretched territory)

---

## 1. Are bars 18924 and 18932 truly similar aggressive buy bars?

**Visually similar but fundamentally different.**

| Field | Bar 18924 (FAILED) | Bar 18932 (SUCCEEDED) |
|-------|--------------------|-----------------------|
| px_open | 29809.75 | 29783.75 |
| px_high | 29834.00 | 29816.50 |
| px_low | 29805.50 | 29783.25 |
| px_close | 29830.00 | 29816.50 |
| bar_range_pts | 28.50 pts | 33.25 pts |
| body_pts | 20.25 pts | 32.75 pts |
| close_location | 0.86 (near top) | 1.00 (close = high) |
| direction | UP | UP |
| Signed_total (from level cache) | +499 | +749 |
| AbsFlow_total | 23,925 | 21,953 |
| BidAdd | 5,920 | 5,601 |
| BidPull | 5,735 | 5,291 |
| AskAdd | 5,978 | 5,311 |
| AskPull | 6,292 | 5,750 |
| NetBid (BidAdd - BidPull) | +185 | +310 |
| NetAsk (AskPull - AskAdd) | +314 | +439 |
| mlofi_norm | **+2.094** | **-1.670** |
| buy_ratio | 0.604 | 0.654 |
| **dash_vpin_pct** | **0.076 (7.6th pct)** | **0.892 (89.2nd pct)** |
| dash_toxicity | 0.5506 | 0.9322 |
| bf_bs_book_switch_direction | NEUTRAL | NEUTRAL |
| dist_to_val_ticks | 88 (+22 pts above VAL) | 46 (+11.5 pts above VAL) |

**Verdict**: They only LOOK similar. The most critical differences:
- Bar 18924 has **very low toxicity** (7.6th pct) but **strongly positive mlofi** (+2.094)
- Bar 18932 has **very high toxicity** (89.2nd pct) but **negative mlofi** (−1.670)
- Bar 18932 closed at the exact high (close_location = 1.00); bar 18924 left 4 pts of upper wick

This is the **MLOFI vs TOXICITY paradox**: the bar with stronger-looking book flow OFI failed,
while the bar with negative mid-level OFI but high toxic flow succeeded.

---

## 2. What happened after bar 18924?

**Immediate reversal within 1 bar; sellers completely reclaimed by bar 18926.**

| Horizon | Forward Close | Return (Long) | Max Adverse | Broke Midpoint |
|---------|--------------|---------------|-------------|----------------|
| H1 | 29821.75 | -8.25 pts | 10.00 pts | No |
| H2 | 29801.75 | **-28.25 pts** | 28.25 pts | **Yes** |
| H3 | 29802.75 | -27.25 pts | 37.00 pts | Yes |
| H5 | 29808.50 | -21.50 pts | 50.00 pts | Yes |
| H10 | 29838.50 | +8.50 pts | 50.00 pts | Yes |
| H20 | 29876.00 | +46.00 pts | 50.00 pts | Yes |
| H40 | 29957.25 | +127.25 pts | 50.00 pts | Yes |

Longs from bar 18924 close would have experienced:
- Maximum adverse excursion of **50 pts** within 5 bars
- Broke through the bar midpoint (29819.88) by H2
- Broke through the bar LOW (29805.50) by H2
- Market eventually recovered but required 10+ bars and 50 pts of pain

A short entry from bar 18924 close would have returned +28.25 pts by H2.

---

## 3. Why did sellers regain control after bar 18924?

**Primary cause: Seller absorption — sellers were still active and maintained asks above bar 18924's close.**

Zone anatomy for bar 18924:

| Zone | Price Range | Key Finding |
|------|------------|-------------|
| Z1 lower bid (< open 29809.75) | 29800.75–29809.50 | Neutral: perfect bid/ask match. 20 of 35 levels had ZERO ask activity (sellers absent below) |
| Z2 near-open to mid (29809.75–29819.88) | | HEAVY seller activity: AskAdd=3,735 >> BidAdd=2,631. Sellers posting aggressively at mid zone |
| Z3 upper ask (29819.88–29830.00) | | Modest flow (1,312 abs). Ask_replenishment=0.66 — ask pulling slightly > adding in this zone |
| **Z4 above close (> 29830)** | 29830.25–29837.50 | **AskAdd=436 vs AskPull=445, ratio=0.98 — SELLERS REPLENISHING.** Sellers maintained an active wall just above the close |

The tell: In Z4 (above bar 18924's close of 29830), AskAdd = 436 vs AskPull = 445. The ratio of 0.979 means sellers were almost perfectly replenishing every ask that was pulled. This is **seller absorption in action**: buyers lifted asks, sellers immediately re-posted. The wall held.

Additional confirmation:
- Low toxicity (dash_vpin_pct = 7.6%) — buyers were not informed/aggressive enough to force sellers out
- mlofi_norm = +2.094 was driven by Z2 passive bid-side activity (BidAdd = 2,631 at mid), not aggressive upside forcing
- The high mlofi was actually a BULLISH TRAP signal: buyers were adding bids passively while sellers added asks aggressively at mid
- Bar 18925 (H1) immediately opened at 29830 and dropped: High = 29833, Close = 29821.75 (−8.25 pts)
- Bar 18926 (H2) was a full-range down bar: Open = 29821.75, Close = 29801.75, mlofi = +4.902

Sellers were never exhausted. They absorbed the buy bar and re-established control within 1 bar.

---

## 4. What happened after bar 18932?

**Immediate continuation with near-zero adversity across 40 forward bars.**

| Horizon | Forward Close | Return (Long) | Max Adverse | Broke Midpoint |
|---------|--------------|---------------|-------------|----------------|
| H1 | 29833.25 | **+16.75 pts** | 3.25 pts | No |
| H2 | 29838.50 | +22.00 pts | 3.25 pts | No |
| H3 | 29852.00 | +35.50 pts | 3.25 pts | No |
| H5 | 29870.00 | **+53.50 pts** | 3.25 pts | No |
| H10 | 29869.75 | +53.25 pts | 3.25 pts | No |
| H20 | 29941.50 | +125.00 pts | 3.25 pts | No |
| H40 | 29972.25 | +155.75 pts | 3.25 pts | No |

The MAE of **3.25 pts** across ALL horizons (H1 through H40) means price NEVER came back
below 29813.25 (bar 18932 close − 3.25 pts) after the bar closed. This is essentially a
one-way move with no retracement opportunity for sellers.

---

## 5. Why did sellers fail after bar 18932?

**Primary cause: Seller exhaustion prior to bar 18932 + ask vacuum above close.**

### The Sequence (Critical Context):
- **Bar 18930**: Open=29808.75, Close=29793.00, mlofi=−2.554 — sellers pushing
- **Bar 18931**: Open=29793.00, Close=29783.75, mlofi=**−10.102** — EXTREME seller push
  - This is the most negative mlofi value in the local window
  - Sellers were hammering asks and pulling bids aggressively
  - Low printed: 29781.25 (the session push-low)
  - buy_ratio = 0.502 — buyers were still showing up even here
- **Bar 18932**: Open=29783.75 (= prior close), Close=29816.5 (= High), mlofi=−1.670

After the extreme mlofi=−10.102 in bar 18931, sellers had exhausted their inventory.
Bar 18932 opened right at the prior close (29783.75) and closed at the HIGH (29816.50) — a 32.75 pt body with essentially no upper wick. This is a textbook seller-exhaustion reversal.

### Zone Anatomy of Bar 18932:

| Zone | Price Range | Key Finding |
|------|------------|-------------|
| Z1 lower bid (< open 29783.75) | 29777.50–29783.50 | 15 of 17 levels had ZERO ask activity — sellers completely absent below open |
| Z2 near-open to mid (29783.75–29800) | | AskAdd=1,236 vs AskPull=1,438 — ask pulling > adding (slight ask vacuum forming) |
| Z3 upper ask (29800–29816.50 = CLOSE) | | MASSIVE flow (16,208 abs_flow). BidAdd=4,020, AskAdd=4,048, AskPull=4,266 — intense battle; buyers absorbed all selling and pushed to close=high |
| **Z4 above close (> 29816.50)** | 29816.75–29820 | **AskAdd=25 vs AskPull=44, ratio=0.568 — SELLERS FAILING.** Only 14 levels, sellers couldn't maintain asks above the high |

The critical tell in Z4: only 25 units of ask were added above 29816.50 vs 44 units pulled. The
ask_replenishment_ratio of 0.568 is far below 1.0. Sellers could not re-establish a wall above
the close. The vacuum above close is confirmed.

### Why the Negative mlofi Does Not Explain Failure:
The mlofi_norm = −1.670 for bar 18932 is NOT a bearish signal in this context. It reflects the
time-integrated mid-level OFI across the entire bar — which started with high seller activity
(continuation of bar 18931's −10.102 mlofi). The early phase of bar 18932 was still seller-
dominated at mid. But the END of the bar (close=high) tells the truth. The trade-side confirming
signal: buy_ratio = 0.654 (65.4% of all trades were aggressive buys), dash_vpin_pct = 89.2nd pct.

---

## 6. Was Case A buyer exhaustion, seller absorption, or resistance rejection?

**SELLER ABSORPTION** — primarily.

- Sellers actively replenished asks above bar 18924's close (Z4: AskAdd=436 vs AskPull=445, ratio=0.979)
- Z2 showed heavy AskAdd=3,735 — sellers were volume-posting at mid, not being forced out
- Low toxicity (7.6th pct) means buyers could not overwhelm sellers
- Price was 22 pts above VAL (88 ticks) — buyers had already done the recovery work; sellers at this level were entrenched
- Reaction type: `VAL_rejection_from_above` at VAL=29808 — the model correctly identified sellers at this level

Resistance rejection is secondary (price was at old R levels, resi_rows=45 in level data).
Buyer exhaustion is tertiary (buy_ratio=0.604 is good, not exhausted, but not toxic enough).

---

## 7. Was Case B ask-liquidity vacuum, VAL reclaim, or buy-toxic continuation?

**All three were present simultaneously — they are not mutually exclusive.**

| Mechanism | Evidence |
|-----------|----------|
| Ask-liquidity vacuum | Z4 ratio=0.568 (sellers failing above close), 15 of 17 Z1 levels zero ask (sellers absent below) |
| VAL reclaim | VAL=29805, bar 18932 low=29783.25 (touched below VAL at bar 18931), bar 18932 closed 11.5 pts above VAL = reclaim |
| Buy-toxic continuation | dash_vpin_pct=89.2nd pct, dash_toxicity=0.9322, buy_ratio=0.654 |
| Prior seller exhaustion | mlofi_norm_lag_1=−10.102 (extreme), sellers spent from bar 18931 |

The **VAL reclaim** is the structural anchor: price touched just below VAL (29781.25 at bar 18931
low) and immediately reversed. The VAL acted as support. Bar 18932 was the acceptance bar above
VAL — buyers confirmed the reclaim with a close=high.

The **ask vacuum** is the mechanical explanation: once sellers exhausted, their resting sell
orders above price were pulled faster than new sellers could re-post.

The **toxic flow** confirms institutional buying: the 89.2nd-pct VPIN means the buy trades were
disproportionately informed/directional — not market-makers crossing the spread randomly.

---

## 8. What did AskAdd vs AskPull show above price?

| Zone | Bar 18924 (FAILED) | Bar 18932 (SUCCEEDED) |
|------|--------------------|-----------------------|
| Z3 (mid to close) | AskAdd=244, AskPull=370 — sellers pulling (modest vacuum) | AskAdd=4,048, AskPull=4,266 — intense battle but buyers won |
| Z4 (above close) | **AskAdd=436, AskPull=445 → ratio=0.979** | **AskAdd=25, AskPull=44 → ratio=0.568** |
| Z4 interpretation | Sellers actively replenishing above close = **ABSORPTION** | Sellers failing above close = **VACUUM** |

The Z4 ask replenishment ratio is the single most discriminating feature. A ratio near 1.0 in Z4
means sellers are absorbed every ask lift and re-post immediately. A ratio below 0.7 means
sellers cannot maintain their wall — the close is clean and continuation is likely.

---

## 9. What did BidAdd vs BidPull show below price?

| Zone | Bar 18924 (FAILED) | Bar 18932 (SUCCEEDED) |
|------|--------------------|-----------------------|
| Z1 (below open) | BidAdd=2,632 ≈ BidPull=2,630 (neutral, small net) | BidAdd=64, BidPull=51 — minimal, sellers absent below |
| Z2 (open to mid) | BidAdd=2,631, BidPull=2,528 — bid support present | BidAdd=1,517, BidPull=1,366 — bid support, buyers defending |
| Z3 (upper zone) | BidAdd=389, BidPull=309 — light bid support at upper levels | BidAdd=4,020, BidPull=3,874 — STRONG bid support in upper zone |

For bar 18932, the bid-side in Z3 (the upper buying zone) shows 4,020 added vs 3,874 pulled —
buyers were aggressively adding bid support at prices above the midpoint. This is "buying the
offer zone" behavior: buyers were confident enough to add limit bids even at higher prices.

For bar 18924, Z3 bid activity was light (BidAdd=389). Buyers were not committing defensively to
the upper zone — only passive bids at lower levels (Z2).

---

## 10. What did book switching show?

Both bars showed a **NEUTRAL** book switch direction with conflict:

| Feature | Bar 18924 | Bar 18932 |
|---------|-----------|-----------|
| bf_bs_bullish_switch_score | 0.656 | 0.586 |
| bf_bs_bearish_switch_score | 0.640 | 0.539 |
| bf_bs_book_switch_net | +0.016 | +0.048 |
| bf_bs_book_switch_direction | NEUTRAL | NEUTRAL |
| bf_bs_book_switch_conflict | 1 (conflict) | 1 (conflict) |

Neither bar showed a clean bullish book switch. Bar 18932 had a slightly larger book_switch_net
(+0.048 vs +0.016) but both were in "conflict" territory.

**Conclusion**: Book switching was NOT the discriminating signal in this case. Both bars showed
similar (modest) bullish switch scores. The switch framework correctly identifies the conflict
but doesn't separate success from failure here. VPIN and zone anatomy are more powerful.

---

## 11. What did VPIN/toxic flow show?

| Metric | Bar 18924 (FAILED) | Bar 18932 (SUCCEEDED) |
|--------|--------------------|-----------------------|
| master_vpin | 0.0968 | 0.1812 |
| dash_vpin_pct | 0.076 (7.6th pct) | 0.892 (89.2nd pct) |
| dash_vpin_state | NORMAL | ELEVATED |
| dash_toxicity | 0.5506 | 0.9322 |

This is the **strongest single-feature separation in the dataset** (82 pct-point gap in vpin_pct).

**Bar 18924 interpretation**: VPIN at 7.6th pct means the buy activity was barely above the
market-making noise floor. Buyers were not informed. The positive book-flow OFI (mlofi=+2.094)
was largely passive bid-posting — not aggressive take-side buying. Without toxic flow, sellers
could easily re-absorb any upward pressure.

**Bar 18932 interpretation**: VPIN at 89.2nd pct means the flow was highly toxic/directional.
Buyers were aggressively lifting offers. Combined with the close=high and prior seller exhaustion,
this represents informed institutional accumulation after a seller wash-down.

---

## 12. What did price-travel/liquidity-vacuum features show?

| Feature | Bar 18924 (FAILED) | Bar 18932 (SUCCEEDED) |
|---------|--------------------|-----------------------|
| bar_range_pts | 28.50 | 33.25 |
| body_pts | 20.25 | 32.75 |
| efficiency (body/range) | 0.711 | 0.985 |
| range_per_abs_flow | 0.00119 pts/unit | 0.00152 pts/unit |
| upward_vacuum_score | 0.000040 | 0.000090 |
| Z4 upper_ask_removed | +9 | +19 |
| Z4 zero_ask_add_levels | 0 | 1 |
| Z1 zero_ask_add_levels | 20/35 = 57% | 15/17 = 88% |

The **efficiency** (0.711 vs 0.985) shows bar 18932 moved almost entirely as a body with almost
no wicks — the most efficient possible bullish bar structure. Bar 18924 had more wasting (upper
wick of 4 pts, reflecting failed tests above close).

The **Z1 zero_ask_add** tells the seller-confidence story below the bar open: for bar 18924,
20 of 35 levels (57%) below open had zero ask activity — sellers weren't worried about downside.
For bar 18932, 15 of 17 levels (88%) below open had zero ask — sellers had completely vacated
the sub-open zone. They had no conviction below.

---

## 13. What level context mattered?

| Level Feature | Bar 18924 | Bar 18932 | Interpretation |
|--------------|-----------|-----------|----------------|
| bf_native_VAL | 29808.00 | 29805.00 | Session VAL |
| dist_to_val_ticks | 88 (22 pts above VAL) | 46 (11.5 pts above VAL) | 18932 closer to VAL |
| VAL reclaim | No (already above VAL) | Yes (touched below VAL at 18931 low, then reclaimed) |
| inside_value_area | 1 | 1 | Both inside VA |
| modelprob_reaction_type | VAL_rejection_from_above | VAL_rejection_from_above | Same label |
| touch_count_past_only | 43 | 46 | Heavy-touch level |

**Case A**: Bar 18924 was already 22 pts (88 ticks) above VAL. Buyers were buying into
"stretched" territory. The VAL was not supporting the buy at this point — it was far below.
The buy was heading INTO old resistance (resi_rows=45 from level data).

**Case B**: Bar 18931 (the extreme seller bar) drove price to 29781.25 — piercing BELOW VAL=29805.
Bar 18932 opened at 29783.75 (still below VAL) and closed at 29816.50 (11.5 pts above VAL).
This is a **VAL reclaim** in the strictest sense: price tested below VAL, was rejected, and closed
above VAL with strength. VAL support worked exactly as expected.

The modelprob system correctly identified both as "VAL_rejection_from_above" (because the level
type = VAL and the reaction was tested from the VAL boundary) but missed the directionality
difference. The level system knew both were at VAL — only the VAL-below-test in bar 18931
context makes bar 18932 structurally superior.

---

## 14. What reusable rule separates buy-bar success from buy-bar failure?

**Rule: BUY_BAR_CONTINUATION_SIGNAL**

A buy bar signals continuation (not absorption/trap) if ALL of:
1. `close_location >= 0.90` (close at or near top of range — no upper wick rejection)
2. `dash_vpin_pct >= 0.60` (elevated toxicity — informed/aggressive buying)
3. `dist_to_val_ticks <= 60` OR `lvl_VAL == 1` (near VAL support)
4. [Optional but powerful] `mlofi_norm_lag_1 < −3.0` (prior seller exhaustion)

**Buy-bar TRAP signals** (predict failure):
1. Z4 ask_replenishment_ratio ≥ 0.90 (sellers actively replenishing above close)
2. `dash_vpin_pct < 0.30` (non-toxic, passive flow)
3. `close_location < 0.90` (upper wick rejection)

**Backtest on 2,636 similar aggressive buy bars (NQU6 history)**:
- Base H5 success rate: **18.6%**
- With continuation signal (Rule A): **37.5%** (32 samples, 2× lift)
- Without continuation signal: **18.3%** (effectively base rate)

**Strongest single discriminator**: `dash_vpin_pct` — 82 pct-point separation between
the two cases. Low toxicity buy bars almost always fail against organized sellers.

---

## 15. Should we add this to Feature Master/dashboard?

**YES — add as SHADOW/RESEARCH diagnostic only.** No execution logic.

**Recommended additions to Feature Master** (low friction, no new data needed):
- `buy_bar_trap_risk` = 1 if (dash_vpin_pct < 0.30 AND close_location < 0.90)
- `buy_bar_continuation_signal` = 1 if (dash_vpin_pct ≥ 0.60 AND close_location ≥ 0.90 AND dist_to_val_ticks ≤ 60)
- `prior_seller_exhaustion_flag` = 1 if (mlofi_norm_lag_1 < −3.0 OR mlofi_norm_lag_2 < −3.0)

**Recommended addition to dashboard display** (requires per-level cache at bar close):
- Z4 ask_replenishment_ratio (seller wall strength above close)
- `BUY_BAR_SELLER_ABSORPTION` / `BUY_BAR_SELLER_FAILURE` label

**Implementation note**: The Z4 per-level features require running a zone anatomy calculation
from the Book Flow per-level cache after each bar closes. The cache is available and maintained.
This adds one lightweight pandas computation per closed bar.

---

## Files Generated

| File | Description |
|------|-------------|
| `caseA_bar18924_window.csv` | Bars 18904–18944, all key features (Part A) |
| `caseB_bar18932_window.csv` | Bars 18912–18952, all key features (Part A) |
| `case_comparison_window_summary.csv` | Window-level summary statistics (Part A) |
| `selected_bar_similarity_check.csv` | Side-by-side comparison of 18924 vs 18932, 63 fields (Part B) |
| `caseA_bar18924_zone_anatomy.csv` | Per-price-zone anatomy for bar 18924 (Part C) |
| `caseB_bar18932_zone_anatomy.csv` | Per-price-zone anatomy for bar 18932 (Part C) |
| `zone_anatomy_comparison.csv` | Combined zone anatomy for both cases (Part C) |
| `seller_absorption_vs_failure_scores.csv` | Computed scores for both bars (Part D) |
| `post_selected_bar_outcomes.csv` | H1–H40 forward returns, MFE, MAE (Part E) |
| `previous_context_caseA_vs_caseB.csv` | Lag 1–20 context for each case (Part F) |
| `level_context_caseA_vs_caseB.csv` | Level proximity and reaction context (Part G) |
| `book_switch_vpin_case_comparison.csv` | Book switch and VPIN features (Part H) |
| `price_travel_vacuum_case_comparison.csv` | Vacuum and travel features (Part I) |
| `aggressive_buy_bar_success_failure_rule_test.csv` | Backtest on 2,636 bars (Part J) |
| `rule_candidate_summary.md` | Rule definitions and backtest results (Part J) |
| `buy_bar_dashboard_recommendation.md` | Dashboard field recommendations (Part K) |
| `BUY_BAR_SUCCESS_VS_FAILURE_CASE_STUDY_REPORT.md` | This report (Part L) |

---

## Final Status Fields

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

CASE_A_BAR_FOUND:                   true (bar 18924, 2026-07-10 15:03:49 UTC, close=29830)
CASE_B_BAR_FOUND:                   true (bar 18932, 2026-07-10 15:07:05 UTC, close=29816.5)

CASE_A_CLASSIFICATION:              SELLER_ABSORPTION (sellers replenished above close, Z4 ratio=0.979)
CASE_B_CLASSIFICATION:              SELLER_FAILURE + VAL_RECLAIM + BUY_TOXIC_CONTINUATION

SELLER_ABSORPTION_DIFFERENCE_FOUND: true (Z4 AskAdd ratio: 0.979 vs 0.568)
SELLER_FAILURE_DIFFERENCE_FOUND:    true (Z4 seller wall absent in Case B; ask vacuum confirmed)
ASK_REPLENISHMENT_EXPLAINS_DIFFERENCE: true (primary mechanical explanation)
LEVEL_CONTEXT_EXPLAINS_DIFFERENCE:  true (VAL reclaim vs stretched above VAL)
BOOK_SWITCH_EXPLAINS_DIFFERENCE:    false (both neutral/conflict — not discriminating)
VPIN_TOXICITY_EXPLAINS_DIFFERENCE:  true (82 pct-point VPIN gap; strongest single signal)

REUSABLE_RULE_FOUND:                true (Rule A: toxic+close_high+VAL; 37.5% hit vs 18.6% base)
DASHBOARD_RECOMMENDATION_CREATED:   true (buy_bar_dashboard_recommendation.md)

OVERALL:                            PASS
```
