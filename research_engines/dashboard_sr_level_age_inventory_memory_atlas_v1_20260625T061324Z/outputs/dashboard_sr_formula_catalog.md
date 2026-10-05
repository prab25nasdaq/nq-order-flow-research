# Dashboard S/R Formula Catalog (extracted verbatim from source — no inference)

Source: `ofi_live_dashboard_WORKING_NEXT_with_logreg.py` (the file named in this
task's "Dashboard source" input). All line numbers below refer to this file as
it currently exists on disk; nothing in it was modified for this analysis.

## 1. Swing S/R (`swing_levels`, lines 597-613)

```python
def swing_levels(win: pd.DataFrame, lb: int = 3):
    h, l = win["px_high"], win["px_low"]
    win_n = lb * 2 + 1
    h_roll = h.rolling(win_n, center=True, min_periods=win_n).max()
    l_roll = l.rolling(win_n, center=True, min_periods=win_n).min()
    hi_idx = where(h is finite and h_roll is finite and h >= h_roll)
    lo_idx = where(l is finite and l_roll is finite and l <= l_roll)
    return [(i, h[i]) for i in hi_idx], [(i, l[i]) for i in lo_idx]
```

A bar is a swing high/low if it is the max/min of a **centered** window of
`2*lb+1` bars (`lb` bars before it AND `lb` bars after it). This is the
dashboard's literal, only swing-detection formula — there is no alternative
version anywhere else in the file.

**Inherent confirmation lag**: because the window is centered, bar `i` cannot
be confirmed as a swing point until bar `i+lb` has occurred. This is a
property of swing detection itself (you cannot know a bar was a local
extreme until you've seen what came after it) — not a bug, but it must be
respected in Part B: a swing level is only "known" to an observer standing
at bar `t` if `t >= i + lb`.

## 2. S/R clustering + scoring (`compute_sr_levels`, lines 616-651)

```python
def compute_sr_levels(df, lb=3, cluster_dist=2.0):
    raw_h, raw_l = swing_levels(df, lb=lb)
    n_total = len(df)
    def _cluster(raw):
        sort raw by price
        group consecutive points within cluster_dist of each other
        for each group:
            med_px  = median(price in group)
            count   = len(group)
            recency = max(bar_idx_in_group) / n_total      # 0..1
            score   = count * (0.4 + 0.6 * recency)
        return groups sorted by score descending
    return _cluster(raw_h), _cluster(raw_l)
```

This is **the** dashboard S/R formula — swing detection, then price
clustering (merge swings within `cluster_dist` points into one level), then
a recency-weighted touch-count score. `n_total` and `recency` are always
relative to whatever window `df` was passed — there is no absolute/global
recency concept in the dashboard's own code.

## 3. Dashboard's own CHART-tab call site (lines 5237-5247)

```python
win, cur = self._win()                                   # last window_bars bars
cluster_dist = 6.0 if len(win) >= 300 else 3.0
_r, _s = compute_sr_levels(win, lb=4, cluster_dist=cluster_dist)
```

`self._win()` (lines 4466-4472):
```python
def _win(self):
    idx   = self.current_idx
    start = max(0, idx - self.window_bars + 1)
    return self.df.iloc[start:idx+1], ...
```

`self.window_bars` (line 1153 default 120; lines 4391-4399 user-adjustable via
a dropdown/entry — accepts an explicit bar count or `"ALL"` which sets
`window_bars = len(self.df)`, i.e. full history to date).

**Finding: the dashboard's own S/R is already a fixed-bar-count lookback
window anchored at the user's current bar position — not a pixel/zoom
viewport.** The in-source comment at line 5242-5243 confirms this
explicitly: *"S/R must follow the selected chart window. If the user
selects 200 bars, levels come from those 200 bars; 800 uses those 800."*
This means Part B does not need a separate DISPLAY_BEHAVIOR_ONLY
reproduction for swing S/R — applying `compute_sr_levels(lb=4,
cluster_dist=6.0)` over fixed windows of 500/1,000/2,500/5,000/10,000/full
bars IS using the dashboard's own mechanism, just with window sizes the UI
itself supports (the UI's free-text window field accepts any integer or
`"ALL"`).

**Exact parameters used for Part B**: `lb=4`, `cluster_dist=6.0` (all six
target window sizes are ≥300 bars, so the adaptive `cluster_dist` branch
always resolves to 6.0 — confirmed from the source, not assumed).

## 4. Volume profile — POC / VAH / VAL / HVN / LVN (lines 4760-4910)

```python
# Visible bar range (DISPLAY-ONLY zoom dependency)
xl = self.ax_price.get_xlim()
i0, i1 = floor(xl[0]), ceil(xl[1])
vis_h, vis_l = win["px_high"][i0:i1+1], win["px_low"][i0:i1+1]

tick = 0.25
levels = price grid from min(vis_l)-2 ticks to max(vis_h)+2 ticks
for each bar i in [i0, i1]:
    overlap-weighted volume distributed across levels overlapping [low_i, high_i]
    tot_v[level] += vt[i] * overlap_fraction
    buy_v / sel_v similarly split (using buy_vol/sell_vol if present, else
        a delta_norm-derived split)

poc_idx = argmax(tot_v)
# Value area: greedy 70%-of-volume expansion around POC
va_target = 0.70 * sum(tot_v)
expand vah_i / val_i outward, always taking the larger-volume side first,
until va_vol >= va_target
poc_px, vah_px, val_px = levels[poc_idx], levels[vah_i], levels[val_i]

# HVN / LVN: rolling-mean deviation
k = max(3, n_lev // 15)
smooth = convolve(tot_v, ones(k)/k, mode="same")
is_hvn = tot_v > smooth * 1.45
is_lvn = tot_v < smooth * 0.55
```

**Finding: this computation is genuinely zoom/viewport-dependent** —
`i0`/`i1` come directly from `self.ax_price.get_xlim()`, the chart's live
visible x-axis range, explicitly commented in source as `"Visible bar
range"`. As the user zooms or pans the chart (without changing
`window_bars`), POC/VAH/VAL/HVN/LVN values shift, because the set of bars
feeding the histogram changes. This is the same display-instability
pattern identified and fixed in the separate, standalone Book Flow chart
(`book_flow_chart_v3.py`) in an earlier task — but per this task's
constraints, the main dashboard's own code is **not** modified here; this
is purely a read-only finding.

This computation **does not use any bar outside the current window** (no
forward leakage — `i0..i1` is always `<= len(win)-1`, and `win` itself is
already bounded by `current_idx`), so it carries no statistical lookahead
risk. The risk it carries is purely about display reproducibility: the
"current POC/VAH/VAL" the user sees depends on their zoom state, which a
backtest cannot reproduce without arbitrarily picking a zoom level. Part B
therefore reproduces this with the IDENTICAL formula (same value-area %,
same HVN/LVN thresholds and kernel) over a fixed lookback window ending at
bar `t`, never a zoom range — labeled `DISPLAY_BEHAVIOR_ONLY` for the
zoom-based version (not rebuilt) vs. `dashboard_level_source =
volprof_fixed_<N>` for the research version.

## 5. Projected (prior-contract) levels (lines 325-348)

```python
def load_projected_levels(path):
    # plain pd.read_csv(projected_levels_NQM6_to_NQU6.csv), mtime/size cached
    return df
```

Docstring (verbatim, lines 330-332): *"These are PROJECTED PRIOR NQM6 LEVELS
SHIFTED TO NQU6 SCALE — never native NQU6 levels, must always be
displayed/labeled separately."* Confirmed (lines 4987-4994) the dashboard
filters this file to `level_type in ("POC","VAH","VAL")` for chart overlay
and explicitly never merges it with the live-computed POC/VAH/VAL/HVN/LVN
above. This is not a dashboard *computation* at all — it is a static,
externally-built file the dashboard only reads and displays. It is
inherently past-only (it describes a contract that had already finished
trading before NQU6 began), so it is used in Part C/G purely as a static
confluence-check reference, never rebuilt per lookback window.

## Summary table

| Level family | Zoom-dependent? | Past-only as displayed? | Rebuilt in Part B as |
|---|---|---|---|
| Swing S/R (`compute_sr_levels`) | No (fixed `window_bars`) | Yes | Same formula, `lb=4, cluster_dist=6.0`, walked forward per fixed lookback window |
| Volume profile POC/VAH/VAL/HVN/LVN | **Yes** (`ax_price.get_xlim()`) | No future bars used, but display-unstable across zoom | Same formula, fixed lookback window instead of zoom range — `DISPLAY_BEHAVIOR_ONLY` kept separate |
| Projected prior-contract levels | No | Yes (static, completed contract) | Read as-is, used only for confluence checks |
