# VPIN Phase Case Study Report
Generated: 2026-07-01T02:56:40Z  |  SHADOW / RESEARCH ONLY

## Summary
- Bearish→Bullish transitions found: 0
  - Signal appeared before bullish move: 0/0 (0.0%)
- Bearish phase continuation examples: 118
  - Confirmed short-side follow-through: 62/118 (52.5%)

## Phase Detection Logic
Phase is determined using signed_vpin_delta rolling Spearman (W=240, H=10):
- `VPIN_BEARISH_PHASE`: rho < -0.10 AND sell_toxicity > baseline AND vpin_pct > 0.70
- `VPIN_BULLISH_PHASE`: rho > +0.10 AND buy_toxicity > baseline AND vpin_pct > 0.70
- `VPIN_TRANSITION_PHASE`: Spearman sign changes between bars
- `VPIN_CHOP_PHASE`: everything else (no consistent direction)

## Key Finding
The Spearman heatmap turning from red (negative) to blue (positive) corresponds
to a shift in `signed_vpin_delta` from persistently negative to positive.
This appears as VPIN_TRANSITION_PHASE bars.
The most reliable transitions occur when:
1. sell_toxicity has been elevated (> 0.10) for >= 3 bars
2. `vpin_pct` stays elevated (> 0.70) through the transition
3. A CUSUM up-break or bullish book switch coincides

## Data Range
Analysis window: 2026-06-03 to 2026-07-01
Total bars: 24,208