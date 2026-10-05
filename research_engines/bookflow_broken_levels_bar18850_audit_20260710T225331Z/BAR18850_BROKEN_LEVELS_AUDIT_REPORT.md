# Bar 18850 Broken Book Flow Levels — Audit Report
**Timestamp**: 2026-07-10T225331Z  
**Symbol**: NQU6 | **Session**: 2026-07-09 (CME CT date) | **Depth**: all (top5/10/15/20)  
**SHADOW / RESEARCH ONLY — NO EXECUTION — NO BROKER — NO PAPER TRADING**

---

## 1. What Happened After Bar 18850?

A **CME Stop Logic Functionality (SLF) halt** triggered at `2026-07-10T14:32:41.424Z`, following a rapid crash that started at bar 18849.

**Timeline (second-by-second, raw trades):**

| Time (UTC)    | n_trades | vol (contracts) | lo      | hi      | event |
|---------------|----------|-----------------|---------|---------|-------|
| 14:32:24–14:32:40 | ~20/s | normal     | 29950   | 29957   | pre-crash |
| **14:32:41** | **463**  | **506**         | 29924.75| 29956.75| **CRASH — 30pt drop in 1 second** |
| 14:32:42–14:32:50 | **0** | **0**       | —       | —       | **CME SLF HALT (10.007s)** |
| 14:32:51      | 195      | 357             | 29850.75| 29893.25| halt ended, cascade resumes |
| 14:32:52–14:33:07 | 100-300/s | high     | 29675   | 29893   | cascade continuation |
| 14:33:15+     | ~70-100/s | recovering   | 29750+  | —       | partial recovery |

**BBO gap confirmed**: bid froze at 29951.25/ask at 29955.25 → bid resumed at 29890.75/ask at 29891.5.  
**Book repositioned 62 points lower during the 10-second halt** (depth updates continued, trades/BBO frozen).  
**Total crash**: 29957.25 → 29675.00 = **−282.25 points in 46 seconds**.

---

## 2. Was the Apparent Break Real Market Movement?

**YES — entirely real market movement.** Raw Rithmic data is continuous and confirmed:

- 4,242 trades in the crash window (14:32:20–14:33:15 UTC)
- 577,593 BBO updates in 14:25–14:45 UTC window
- 675,912 bid quote updates, 687,884 ask quote updates (depth book active throughout halt)
- No Rithmic subscription drop. No parser failure. No data loss.

The "broken" appearance is the chart's CORRECT representation of an extreme real event.

---

## 3. Was There a Raw Data Gap?

**NO traditional data gap.** But there was a **structured 10-second CME SLF halt**:

| Feed       | Max gap in 14:25–14:45 window | Gap cause |
|------------|-------------------------------|-----------|
| trades     | **9.996 seconds**             | CME SLF halt (no trades during halt) |
| BBO        | **10.007 seconds**            | CME SLF halt (BBO frozen during halt) |
| bid_q      | 0.301 seconds                 | Continuous (depth updated during halt) |
| ask_q      | 0.302 seconds                 | Continuous (depth updated during halt) |

The data_gap_markers.csv has NO entry for this halt (supervisor's 900-second threshold is too slow for a 10-second SLF halt). This halt is not recorded anywhere in the pipeline's gap tracking.

---

## 4. Was Raw Data Available but Parser/Cache Missed It?

**NO.** Parser and cache processed all data correctly:

- Master–BookFlow alignment: 0.00s timestamp drift for ALL bars 18820–18880
- All bars 18849–18860 present in both master and Book Flow cache
- No missing bar indices (bar_idx_delta=1 throughout, no skipped indices)
- Cache correctly captured 34 levels for bar 18850, 289 levels for bar 18851

The Book Flow cache is accurate. The parser did not miss any events.

---

## 5. Was Book Flow Cache Malformed?

**NO.** The cache is correct. Specific validation:

**Bar 18850 tooltip match** (depth=5, price_level=29953.75):
```
Cache:   signed_flow=-3, abs_flow=657, bid_add=160, bid_pull=164, ask_add=166, ask_pull=167
Tooltip: signed_flow=-3, abs_flow=657, bid_add=160, bid_pull=164, ask_add=166, ask_pull=167
MATCH: EXACT ✓
```

**Bar 18850 all-levels-above-close** (all depths):
```
depth=5:  34 levels, ALL above close, close=29929, level range=29950.25–29958.50
depth=10: 41 levels, ALL above close, level range=29948.75–29959.50
depth=15: 51 levels, ALL above close, level range=29948.25–29961.00
depth=20: 58 levels, ALL above close, level range=29947.75–29962.50
```

This is NOT malformed — it correctly reflects that the order book was at 29948–29962 for the first 16 of bar 18850's 17 seconds, while the final crash second pulled the last trade (close) down to 29929.

**Bar 18852 sentinel mid_price rows** (price 29893–29964.75):
- 69–151 rows (by depth) with mid_price = 29872.46206 (fallback sentinel)
- These are real **bid cancellation events** at above-market prices after the crash
- Participants pulling above-market bids after being swept through
- Real data, correctly labeled, but far from current market

**2026-07-09 v3 state**: clean (no crossed book, 441 ask entries starting at 30068.50, 0 ghost entries below best bid). The ask_add=0 problem from 2026-07-08 does NOT affect this session.

---

## 6. Was Master Aligned with Book Flow Cache?

**YES — perfect alignment.** All bars 18820–18880:
- Timestamp diff (master_bar_end vs bf_ts): **0.00 seconds for every bar**
- Close price: identical between master and Book Flow cache
- No shifted bars, no off-by-one

---

## 7. Was the Chart Rendering Across a Gap Incorrectly?

**PARTIALLY.** The rendering is technically correct (it shows accurate data) but lacks visual markers for the CME SLF halt.

**Price line rendering** (`_update_price_line`, line 1837):
```python
self.price_curve.setData(bars["bar_pos"].to_numpy(float), bars["px_close"].to_numpy(float))
```
- X-coordinates are sequential integers (`enumerate(base_ids)`) — NOT timestamps
- No NaN insertion for timestamp gaps
- The 10-second CME halt is invisible: bars 18850 and 18851 appear adjacent at x=N, x=N+1

**What the chart shows** (correctly):
- Continuous price line dropping from 29929 → 29891.5 → 29835 → ... → 29675
- Level candles for bar 18850 at 29950–29958, above the close line at that x-position
- Bar 18851: 289 level candles spanning a 96-point range (halt repositioning + cascade)

**What is missing**:
- No visual halt marker at bar 18850/18851 boundary
- No annotation that the BBO/trade feed was silent for 10 seconds
- No indication that bar 18851's 96-point level span includes the halt repositioning
- Level candles for bar 18850 appear "floating" above the close line with no explanation

This is a **rendering gap in UX**, not a data bug.

---

## 8. Were POC/HVN/LVN/S/R Levels Contaminated?

**YES — level calculations are affected if the lookback window includes crash bars.**

The crash bars have anomalous statistics vs normal:
```
                   Normal (18820-18849)    Crash (18849-18857)
avg n_levels:           81.2                    182.0  (2.2× normal)
avg price_span (pts):   20.24                    59.83  (3.0× normal)
max price_span (pts):   46.46                   146.75  (3.2× max normal)
avg bid_add:           3987                     1228   (0.3× normal — atypical)
sentinel_mid_rows:        0                      69+   (bid cancellations)
```

With a 200-bar lookback ending at bar 18880, the window is bars 18681–18880, which includes the entire crash (18849–18857 = 4.5% of window). Volume profile computed from this window will have:
- Massive volume node at 29675–29957 (the crash range, swept in 46 seconds)
- Distorted POC (high volume throughout crash range, not a true acceptance area)
- S/R levels derived from extreme closes may not represent genuine supply/demand

**Impact on current display**: S/R computed POST-crash (after bar 18857) will include the crash distribution. The POC likely sits in the crash range (29700-29900), which was never an acceptance area — just a flash sweep.

---

## 9. What Exact Fix Is Recommended?

### Priority 1 — CME Halt Visual Marker (UX)
In `book_flow_chart_v3.py`, `_update_price_line()`: detect consecutive bar timestamp gaps > 5 seconds during trading hours, insert `np.nan` to break the price curve, add a vertical dashed line and "SLF HALT" annotation at that x-position. (Details in `recommended_fix_plan.md`)

### Priority 2 — Crash Bar S/R Exclusion (Analysis)
In S/R / volume profile computation: add `exclude_extreme_bars` flag. Filter bars with `price_span > 3× median_span` of the window before computing S/R, POC, HVN/LVN.

### Priority 3 — Level Span Cap / Sentinel-Mid Suppression (Cosmetic)
Suppress level rows where `mid_price == 29872.46206` (sentinel fallback) OR `|price_level - close_price| > 50`. Reduces visual clutter during extreme bars.

### Priority 4 — Add CME Halt to data_gap_markers.csv
Detect SLF pattern (trades/BBO frozen, bid_q/ask_q active) and write a `CME_SLF_HALT` marker. Not urgent but improves observability.

**None of these patches are implemented in this audit.**

---

## 10. Should This Window Be Excluded from Research/Model Labels?

**YES — recommended exclusion:**

| Bar range      | Action    | Reason |
|----------------|-----------|--------|
| 18849          | EXCLUDE   | SLF trigger bar (close below entire level window, atypical) |
| 18850          | EXCLUDE   | SLF halt bar (all levels above close, halt included in bar duration) |
| 18851–18857    | EXCLUDE   | Crash cascade (sub-5-second bars, atypical flow, sentinel mid rows) |
| 18858–18870    | FLAG      | Post-crash recovery (contaminated proximity, elevated volatility) |

The crash bars have characteristics that do not generalize:
- Sub-3-second bar durations (extreme temporal compression)
- Price spans 3–7× the session median
- Bid_add/ask_add ratios inverted vs normal accumulation/distribution patterns
- 69–151 sentinel mid rows per bar (bid cancellation at far-from-market prices)
- Level candles displaced from close by 20+ points

These bars should have `halt_flag=True` or `exclude_from_training=True` in the feature master. The event is informative for real-time monitoring but should not contribute to stationary regime model training.

---

## FINAL STATUS

```
PRODUCTION_FILES_MODIFIED:              false
DASHBOARD_CODE_MODIFIED:                false
BOOK_FLOW_CODE_MODIFIED:                false
FEATURE_MASTER_CODE_MODIFIED:           false
MODEL_ARTIFACTS_MODIFIED:               false
ACTIVE_MODEL_POINTER_CHANGED:           false
TRADING_ENABLED:                        false
BROKER_CONNECTED:                       false
PAPER_TRADING_ENABLED:                  false

BAR18850_FOUND:                         true
  - ts=2026-07-10T14:32:41.431742Z
  - close=29929.00, mid=29951.625 (all 4 depths confirmed)
  - Tooltip match EXACT at price_level=29953.75 (all fields)

RAW_COVERAGE_CONTINUOUS:                true
  - trades: 26,240 records in 14:25–14:45, max_gap=9.996s (CME SLF halt)
  - BBO: 577,593 records, max_gap=10.007s (CME SLF halt)
  - bid_q/ask_q: 675K/687K records, max_gap=0.30s (continuous through halt)
  - Raw feed: NOT stale, no Rithmic subscription drop

DATA_GAP_FOUND:                         true (CME SLF HALT — NOT a feed failure)
  - halt_start: 2026-07-10T14:32:41.424Z
  - halt_end:   2026-07-10T14:32:51.431Z
  - halt_duration_s: 10.007
  - halt_type: CME_STOP_LOGIC_FUNCTIONALITY (not Rithmic, not pipeline)
  - recorded_in_data_gap_markers: false (supervisor threshold too slow for 10s halt)

BOOKFLOW_CACHE_MALFORMED:               false
  - Cache correct, tooltip matches exactly
  - All 4 depths show bar 18850 correctly (all levels above close — real event)
  - 2026-07-09 v3 state clean (no crossed book, no ghost ask entries)

MASTER_BOOKFLOW_ALIGNMENT_OK:           true
  - 0.00s timestamp drift for all bars 18820–18880
  - No shifted or missing bars
  - bar_idx_delta=1 throughout crash window

RENDERING_ARTIFACT_FOUND:               true (no halt marker / gap-break in price line)
  - Price line connects bar 18850→18851 with no visual break
  - Chart uses sequential integer x-positions (not timestamps)
  - No NaN insertion for CME halt gaps
  - Level candles for bar 18850 float 21–29 pts above close line (visually confusing but data-correct)

LEVEL_CALCULATION_AFFECTED:             true
  - Crash bars (18849–18857) have 3× normal price span, 2.2× normal n_levels
  - If lookback includes crash, POC/HVN/LVN/S/R will be distorted
  - data_gap_markers.csv has no halt marker to trigger S/R reset

ROOT_CAUSE:                             REAL_MARKET_MOVE + CME_STOP_LOGIC_FUNCTIONALITY_HALT
  - No data bug. No cache bug. No alignment bug.
  - Crash is real. Halt is real. Book repositioned 62pts during 10s halt.
  - Visual disconnect = chart has no halt-gap-break rendering logic.

RECOMMENDED_FIX:                        RENDERING_HALT_MARKER + CRASH_BAR_SR_EXCLUSION
  (see recommended_fix_plan.md — patch not implemented, awaiting instruction)

BARS_TO_EXCLUDE_FROM_LABELS:            18849–18857 (exclude), 18858–18870 (flag)

OVERALL:                                PASS
```

---

## Files in This Audit Directory

| File | Contents |
|------|----------|
| `bar18850_master_window.csv` | Master window 18820–18880, ts/close/gap deltas |
| `bar18850_master_window_wide.csv` | Master window 18750–18950 |
| `bar18850_gap_audit.csv` | Timestamp deltas, price gaps, gap flags |
| `bar18850_raw_coverage.csv` | Raw feed counts/gaps in 14:25–14:45 UTC |
| `bar18850_bookflow_cache_audit.csv` | Cache parity all 4 depths, bars 18849–18852 |
| `bar18850_per_level_rows.csv` | All 34 level rows for bar 18850 at depth=5 |
| `master_vs_bookflow_alignment_bar18850.csv` | Timestamp/close alignment 18820–18880 |
| `level_calculation_around_bar18850.csv` | Level stats, crash classification, sentinel counts |
| `bookflow_rendering_logic_audit.md` | Rendering code analysis, gap-handling audit |
| `recommended_fix_plan.md` | Prioritized fix recommendations |
| `root_cause_classification.json` | Machine-readable classification |
| `BAR18850_BROKEN_LEVELS_AUDIT_REPORT.md` | This report |
