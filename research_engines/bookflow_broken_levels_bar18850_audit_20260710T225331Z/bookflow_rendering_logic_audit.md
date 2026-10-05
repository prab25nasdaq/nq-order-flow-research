# Book Flow Rendering Logic Audit — Bar 18850 Visual Break
**File**: `book_flow_chart_v3.py` (2813 lines)  
**SHADOW / RESEARCH ONLY — NO CODE MODIFIED**

---

## X-Coordinate Assignment

```python
# _prepare_visible_data (line ~1516)
self._bar_pos_map = {int(bar_id): i for i, bar_id in enumerate(base_ids)}
visible_cells["bar_pos"] = visible_cells["bar_idx"].map(self._bar_pos_map).astype(float)
visible_bars["bar_pos"] = visible_bars["bar_index"].map(self._bar_pos_map).astype(float)
```

**Finding**: X-coordinates are sequential integers (0, 1, 2, ...) assigned by enumeration order of `base_ids`. Time is NOT used for x positioning. This means:
- Bar 18849 (14:32:24 UTC) → x = N
- Bar 18850 (14:32:41 UTC, 17s later) → x = N+1
- Bar 18851 (14:32:51 UTC, 10s halt) → x = N+2

**The 10-second CME halt produces no visual x-axis gap.** Bars appear adjacent regardless of timestamp spacing.

---

## Price Line Rendering

```python
# _update_price_line (line 1837)
def _update_price_line(self, bars: pd.DataFrame) -> None:
    if self.price_cb.isChecked() and not bars.empty:
        self.price_curve.setData(bars["bar_pos"].to_numpy(float),
                                 bars["px_close"].to_numpy(float))
        self.price_curve.setVisible(True)
```

**Finding**: `pg.PlotCurveItem.setData()` with sequential x and close prices. **No NaN insertion for gaps.** PyQtGraph connects all points with straight lines. The price line:
- Drops from 29929 (bar 18850) directly to 29891.5 (bar 18851) with a connected line
- Continues crashing to 29700 through bars 18852-18856
- No break marker, no halt indicator, no gap annotation

---

## Level Candle Rendering

Level candles (per-price-level rectangles) are placed at their `price_level` (y-axis) and `bar_pos` (x-axis). For bar 18850:
- ALL 34 level rows have price_level = 29950.25–29958.50
- close_price = 29929.00
- The level candles are drawn 21–29 points ABOVE the close price line for that bar

This creates the visual disconnect: price line touches 29929 at bar 18850's x-position, while all level candles at the same x-position are at 29950–29958. The viewer sees floating level candles disconnected from the close line.

**Cause**: Legitimate — the order book depth was at 29950–29958 for 16/17 seconds of bar 18850, while the 500th contract (bar close) landed at 29929 during the cascade's final second.

---

## Gap Handling

**Does the chart handle CME halt gaps?** NO.

- No `np.nan` insertion when consecutive bar timestamps differ by >threshold
- No `connect=False` on PlotCurveItem for halts
- No "DATA_GAP" vertical marker
- No x-axis stretch for timestamp gaps
- The 10-second CME SLF halt is visually indistinguishable from a normal 10-second bar

For comparison: the `data_gap_markers.csv` records traditional feed gaps (Rithmic subscription drops), but does NOT record CME SLF halts. The supervisor's 900s threshold wouldn't fire on a 10s halt.

---

## Missing-Bar Handling

When `bars_df` is synced to the level_df coverage (line ~572):
```python
bars_df = bars_df[bars_df["bar_index"].isin(cached_bar_ids)].copy()
```

No synthetic NaN bars are inserted for missing indices. The sequential `enumerate()` position assignment naturally closes any gaps. No missing-bar safeguards exist for structural market halts.

---

## Level Cache per-bar Coverage

For bar 18850, all 4 depths show 0 levels below close:
```
depth=5:  34 levels, ALL above close (levels_below_close=0)
depth=10: 41 levels, ALL above close
depth=15: 51 levels, ALL above close
depth=20: 58 levels, ALL above close
```

The pattern intensifies at higher depths (deeper book captures even more above-close activity).

For bar 18852:
- 69–151 sentinel mid_price rows (29872.46206 fallback) at prices 29893–29964 (bid cancellations)
- These are real events (bid pulls at above-market prices after crash) captured with fallback mid

---

## S/R Level Calculation

From `_get_analysis_levels()` (line 1440+):
- Uses `_analysis_base_bar_ids()` — the fixed lookback window
- Calls `bfl.compute_sr_levels(analysis_bars, lb=4, cluster_dist=6.0)` and `bfl.volume_profile(analysis_bars)`
- Uses `bars_df` (vol500 bars), NOT the level_df price distribution for S/R computation

**Finding**: S/R uses vol500 bar OHLC. The crash bars (18849-18856) have:
- avg price_span: 59.83 points vs 20.24 points for normal bars
- bar 18852: 146.75 point span
- bid_add drastically reduced (crash = massive pull, not add)

If the lookback window includes the crash bars, S/R / volume profile calculations will be distorted by extreme price ranges and atypical flow signatures. A 200-bar lookback at bar 18880 includes bars 18681–18880, spanning the entire crash and recovery. The crash represents 9/200 = 4.5% of the window with anomalous statistics.

---

## Recommended Fix (DO NOT IMPLEMENT — for REPORT only)

1. **CME halt visual marker**: Insert `np.nan` at the close curve between bar 18850 and 18851 when timestamp gap > X seconds (e.g., 5 seconds during trading hours), then add a vertical line or annotation at the halt x-position.

2. **Level display clamp**: For individual bars where `all(price_level > close_price)` (all levels above close, or all below), add a visual annotation noting the bar close is outside the level window.

3. **Crash bar level span cap**: Cap visible level range per bar at ±N points from close (e.g., ±50 points), hiding sentinel-mid rows at far-from-market prices.

4. **S/R exclusion flag**: Provide a `exclude_extreme_bars` option in S/R computation that flags bars with price_span > 3× median span or sentinel_mid_rows > threshold.

5. **data_gap_markers.csv CME halt entries**: Add halt detection to the supervisor: when trades are frozen but bid_q/ask_q are still flowing (the pattern observed here), record a CME_HALT marker.
