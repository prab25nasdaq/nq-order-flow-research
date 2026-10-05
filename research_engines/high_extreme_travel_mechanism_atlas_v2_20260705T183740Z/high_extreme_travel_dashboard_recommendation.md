# High/Extreme Travel Mechanism Dashboard Recommendation
**SHADOW / RESEARCH ONLY — NOT YET PROMOTED TO PRODUCTION**

## Purpose
Surface the vacuum / book-thinness signals that predict high/extreme travel
in real time, allowing the dashboard observer to anticipate (not react to) moves.

## Recommended Tab: "PRICE TRAVEL / LIQUIDITY VACUUM" (from Atlas v1 spec)

### PANEL A — Travel State
- Current travel bucket: LOW / NORMAL / HIGH / EXTREME (live, per bar)
- Travel direction: UP / DOWN / CHOP / ABSORB
- Range efficiency (body/range)
- Close-at-high / close-at-low flag

### PANEL B — Directional Vacuum Gauge
- **upward_vacuum_score**: composite of upper_ask_removed + zero_ask_add_levels above mid
  → HIGH = ask side being pulled above price; UP move likely
- **downward_vacuum_score**: composite of lower_bid_removed + zero_bid_add_levels below mid
  → HIGH = bid side being pulled below price; DN move likely
- upper_ask_replenishment_ratio (target: < 0.5 = danger zone)
- lower_bid_replenishment_ratio (target: < 0.5 = danger zone)

### PANEL C — Replenishment Failure Warning
- replenishment_failure (current bar)
- replenishment_failure_lag1 (prior bar — Atlas-confirmed lag-1 precursor)
- replenishment_failure_lag2 (lag-2 precursor)
- replenishment_failure_lag3 (lag-3 precursor)
- Light: GREEN = replenishing OK / YELLOW = partial failure / RED = book failing

### PANEL D — Book Switch Signal
- bullish_switch_score = bid_add + ask_pull (UP pressure)
- bearish_switch_score = ask_add + bid_pull (DN pressure)
- Net switch direction: BULL / BEAR / NEUTRAL
- Switch z-score vs 20-bar rolling

### PANEL E — Upper/Lower Zone Raw Data
- Zone D (upper quarter): ask_add vs ask_pull — for UP watch
- Zone A (lower quarter): bid_add vs bid_pull — for DN watch
- These are the "vacuum zones" — where price travels if empty

### PANEL F — Forward Risk
- travel_continuation_score (from mechanism label: vacuum = higher continuation)
- travel_exhaustion_score (high range after absorption = likely fade)

## Implementation Notes
- All data from BF level candles (aggregated to bar level per zone)
- No new data sources required
- Zone split: price < low + 0.25×range = zone_A, price > low + 0.75×range = zone_D
- Do NOT patch dashboard until explicitly requested
