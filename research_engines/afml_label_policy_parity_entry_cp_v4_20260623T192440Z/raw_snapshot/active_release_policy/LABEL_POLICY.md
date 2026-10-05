# LABEL POLICY (continuous adjusted NQ)

_run at 2026-06-21T01:50:47.569294+00:00_

- Primary target: label_h40 (binary direction at h40 = +40 bars)
- LONG=1 if log_ret > 0, SHORT=0 if log_ret < 0, NEUTRAL dropped
- Forward returns at h5/h10/h20/h40 (log AND ticks), computed from continuous_close
- MFE_h40 / MAE_h40 over forward window (continuous_high/continuous_low)
- label_end_bar_idx = bar_idx_in_day + 40 (used for purging/embargo)
- Total events: 70,377  LONG: 28,755  SHORT: 36,296  NEUTRAL: 5,326

## continuous_close vs raw_close (constant-shift invariance)

continuous_close = raw_close + cumulative_roll_adjustment_points, where cumulative_roll_adjustment_points is a FIXED scalar (632.5 for NQM6-history rows, 0 for NQU6 rows) taken verbatim from the locked roll map — never recomputed and never derived from model predictions or future NQU6 data. For any two same-regime bars i<j with prices a=raw_close[i], b=raw_close[j] and constant K=cumulative_roll_adjustment_points: sign(log((b+K)/(a+K))) == sign((b+K)-(a+K)) == sign(b-a) == sign(log(b/a)) for a,b,K>0 (log is monotonic). Therefore label_h40's SIGN computed from continuous_close is IDENTICAL to the sign that would be computed from raw_close for the NQM6-adjusted training region — switching the label price source to continuous_close does not change which class (SHORT=0/LONG=1) any training event belongs to.

## Audit
```
{
  "checked_at_utc": "2026-06-21T01:50:47.568739+00:00",
  "n_labels": 70377,
  "n_label_h40_LONG": 28755,
  "n_label_h40_SHORT": 36296,
  "n_label_h40_NEUTRAL": 5326,
  "uses_only_forward_path": true,
  "uses_shift_minus_1": false,
  "uses_bfill": false,
  "label_horizon_bars": 40,
  "label_price_source": "continuous_close"
}
```
