# Recommended Fix Plan — Bar 18850 Visual Break
**Classification**: REAL_MARKET_MOVE + CME_STOP_LOGIC_FUNCTIONALITY_HALT  
**Status**: Recommendations only — no patch in this audit  
**SHADOW / RESEARCH ONLY — NO CODE MODIFIED**

---

## What Happened

A **CME Stop Logic Functionality (SLF) halt** occurred at 2026-07-10T14:32:41.424Z:
- Trades froze: 14:32:41.424Z → 14:32:51.431Z (**10.007 seconds**)
- BBO froze simultaneously
- Depth book (bid_q/ask_q) continued updating throughout the halt
- Pre-halt book: bid=29951.25, ask=29955.25
- Post-halt book: bid=29890.75, ask=29891.5 (**−62 points repositioned during halt**)
- Total crash: 29957.25 → 29675.00 (**−282.25 points in ~46 seconds**)

The visual "break" is REAL, not a data or cache bug.

---

## Why Bar 18850 Looks Disconnected

Bar 18850 (14:32:24–14:32:41 UTC):
- Close price = 29929.00 (last trade in cascade)
- ALL 34 level candles at 29950.25–29958.50 (book was at 29950+ for 16/17 seconds of bar)
- Level candles render 21–29 points ABOVE the close line

This is physically correct but visually confusing. The close of the bar dragged below the entire level window because the crash hit in the final second of the bar.

---

## Fix 1: CME Halt Visual Marker (HIGH VALUE — chart rendering)

**What to do**: In `book_flow_chart_v3.py`, in `_update_price_line()`, detect timestamp gaps > 5 seconds between consecutive bars (during confirmed trading hours) and insert `np.nan` to break the price curve. Add a vertical dashed line and annotation.

**Implementation guidance**:
```python
# In _update_price_line, detect gaps before setData:
# visible_bars must be sorted by bar_pos
timestamps = pd.to_datetime(bars["timestamp_utc"], utc=True)
ts_diff_s = timestamps.diff().dt.total_seconds().fillna(0)
# Insert NaN in close array where gap > 5s during session hours
close_arr = bars["px_close"].to_numpy(float)
x_arr = bars["bar_pos"].to_numpy(float)
halt_positions = bars["bar_pos"][ts_diff_s > 5].tolist()
# NaN injection: insert NaN at halt x-positions
# ... or draw vertical InfiniteLine at halt positions
```

**Note**: Only trigger if within confirmed CME trading hours (22:00 UTC previous day to 21:00 UTC, Mon-Fri), not at break (21:00-22:00 UTC) or weekend. The CME break already produces a date-level chart break (different date parquet), so the halt is the intra-session concern.

**Impact**: Eliminates the visual "connected crash" appearance. User sees a clean break marker at the halt point.

---

## Fix 2: Level Display Close-Distance Cap (MEDIUM VALUE — clarity)

**What to do**: In the level candle rendering, add a per-bar filter that suppresses level rows where `|price_level - close_price| > threshold` (e.g., 30 points = 120 ticks for NQU6).

**Rationale**: Bar 18850's levels at 29950-29958 are 21-29 pts above close. Bar 18852's sentinel-mid rows at 29893-29964 are 58-129 pts above close. These far-from-close levels are real events but add visual noise during extreme bars.

**Alternative**: Suppress rows where `mid_price == 29872.46206` (sentinel fallback) — these are bid cancellations at above-market prices with no valid book mid, adding visual clutter without actionable information.

**Impact**: Bars 18851-18852 would show fewer far-from-market levels. Chart would appear less "stretched."

---

## Fix 3: Crash Bar S/R Exclusion (MEDIUM VALUE — analysis integrity)

**What to do**: In `build_book_flow_lib.py` (or wherever `compute_sr_levels` / `volume_profile` live), add a parameter `exclude_extreme_bars=True` that filters bars with:
- `price_span > 3 × median_price_span` of the window (crash bars: 60-147 pts vs normal 20 pts)
- OR `sentinel_mid_row_fraction > 0.25`

**Rationale**: The crash bars (18849-18857) represent 4.5% of a 200-bar window but have 3× normal price span and atypical flow (ask_add ≫ bid_add, massive pull dominance). Including them distorts S/R and volume profile calculations.

**Impact**: S/R levels computed post-crash would not be contaminated by the extreme crash distribution.

---

## Fix 4: CME Halt data_gap_markers Entry (LOW VALUE — bookkeeping)

**What to do**: Enhance the pipeline health supervisor (`ofi_pipeline_health_supervisor.py`) to detect CME SLF halt signature:
- trades.ndjson mtime frozen for 5-15 seconds
- bbo.ndjson mtime frozen for 5-15 seconds  
- bid_quote_updates.ndjson mtime still updating (depth book active)

When this pattern is detected, write a `CME_SLF_HALT` marker to `data_gap_markers.csv` with the halt duration and estimated price gap.

**Current state**: No marker exists for the 14:32:41 UTC halt. The supervisor's 900s threshold is too slow to detect a 10s SLF halt.

---

## What NOT to Change

- Do NOT modify raw Rithmic files
- Do NOT modify the parser binary
- Do NOT modify build_book_flow_level_cache.py (the cache data is correct)
- Do NOT re-process these bars (data is accurate, visual issue is display-only)
- Do NOT exclude crash bars from the cache (they are real, valid data)

---

## Research/Model Label Exclusion Recommendation

**Exclude the following bar range from model training labels and backtesting PnL calculations**:

| Bar range | Reason |
|-----------|--------|
| 18849–18857 | CME SLF halt + crash cascade, atypical flow signature |
| 18858–18870 | Post-crash recovery, contaminated by crash proximity |

The crash bars have:
- Extreme ask_add/bid_add imbalance (recovery bars have ask_pull >> everything)
- Anomalous sentinel mid_price rows (fallback mid = invalid level context)
- Price spans 3-7× normal
- Sub-3-second bar durations (extreme compression in vol terms)

These bars should be **FLAGGED** in the feature master and **EXCLUDED** from forward-label computation (the event is informative for real-time monitoring but not for stationary regime models).

Recommend adding a `halt_flag` column to the feature master for bars within ±10 bars of a CME SLF halt event.

---

## Priority Order

1. **Fix 1** (halt visual marker) — prevents user confusion about chart validity
2. **Fix 3** (S/R exclusion) — improves level calculation quality
3. **Fix 2** (level span cap) — cosmetic improvement for extreme bars
4. **Fix 4** (halt in data_gap_markers) — bookkeeping, low urgency

All fixes require modifying `book_flow_chart_v3.py` or related library code. Outside the scope of this audit. Request explicit instruction before patching.
