# EVENT POLICY

_run at 2026-06-21T01:53:35.581184+00:00_

## Reaction definitions (causal — current bar OHLC + prior close only, ALL on the continuous-adjusted price scale)
- `rejection_from_above`: low < level <= high AND close > level (came down, closed back above)
- `rejection_from_below`: low <= level < high AND close < level (came up, closed back below)
- `absorption`: abs_z (vol/range) > 1.5 AND |close-open| < 1 tick
- `breakout_acceptance_above_<lvl>`: |delta| > 1.0 AND close > level + 4 ticks AND prior_close < level - 0.5 ticks
- `breakdown_acceptance_below_<lvl>`: |delta| > 1.0 AND close < level - 4 ticks AND prior_close > level + 0.5 ticks
- `neutral_touch`: touched level AND |close-open| < 1 tick AND no absorption
## Constraints
- Only bars meeting a rule produce events. Normal bars produce NONE.
- touch_count_past_only and bars_since_prior_touch use ONLY past data.
- NO future bars used in event detection.
- All training events have level_source='native' (see EVENT_STREAM_REPORT.md). 'projected_prior_level' is reserved for the continuous inference script only.
## Levels evaluated
- POC, VAH, VAL, every HVN price, every LVN price (per-day volume-profile, computed from continuous_high/continuous_low/vol_total/buy_vol/sell_vol)