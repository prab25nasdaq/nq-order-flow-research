# FEATURE POLICY (continuous adjusted NQ)

_run at 2026-06-21T01:50:44.859914+00:00_

- snapshots per event row only (70,377 events)
- 77 numeric features
## Allowed families (UNCHANGED from old pipeline)
- Level context: dist_to_POC/VAH/VAL/HVN/LVN (ticks AND vol-normalized), inside/above/below VA flags
- OHLC-vol path: candle_body_vol, candle_range_vol, upper/lower_wick_vol, close_location, close_vs_prev_close_vol, close_vs_roll_mean_vol, high_break_vol, low_break_vol
- Rithmic order-flow: mlofi_sum/decay_sum/norm/rolling_5/accel, decay_norm, delta_norm + lags, delta_rolling_5
- Sweep/aggression: sweep_imbalance_norm, sweep_norm, sweep_buy/sell_ratio, buy/sell_ratio
- Regime: vpin (+lags), entropy_score, flow_alignment, mid_resid_z, mid_ret1, volatility_5
- z20 residual versions of delta/vol/sweep/vpin (causal, fitted in master)
- Time: minute_of_day, tod_minute, dow + cyclic sin/cos
- Categorical one-hots: lvl_{POC,VAH,VAL,HVN,LVN}, sess_{Asia,EU,US_Open,US_AM,US_PM,US_Late}, rxn_{...}
- Bid/ask pull PROXY (_PROXY suffix) — derived from mlofi+mid_resid_z+delta; NOT a true book pull. Mark exploratory.
## Hard-banned
- Raw absolute prices (px_*, mid_mean, mid_sum, mid_kf, mid_roll20, continuous_*, raw_*)
- Any Databento column
- Any future-bar data (no shift(-1), no centered rolling, no bfill)
- Raw level prices (poc_px, vah_px, val_px — kept as audit metadata only)

## Audit
```
{
  "checked_at_utc": "2026-06-21T01:50:44.859839+00:00",
  "n_features": 77,
  "raw_price_cols_present": [],
  "future_data_in_features": false,
  "future_data_check_passed": true,
  "centered_rolling_used": false,
  "shift_minus_1_used": false,
  "bfill_used": false,
  "_PROXY_columns_present": [],
  "price_source_for_levels_and_ohlc_vol": "continuous_high/continuous_low/continuous_close/continuous_open",
  "raw_close_used_in_features": false
}
```
