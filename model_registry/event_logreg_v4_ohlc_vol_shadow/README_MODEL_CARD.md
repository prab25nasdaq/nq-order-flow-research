# V4 OHLC-Vol Shadow Model Card

- status: `shadow_research_only`
- paper_trading_allowed: `false`
- production_execution_allowed: `false`
- active_dashboard_model: `false`
- model_family: `LogisticRegression`
- target: `SHORT=0, LONG=1`, NEUTRAL dropped
- primary candidate: `V3_27_plus_OHLC_vol`
- no model artifact is activated by this release.

## Feature Policy

OHLC features are volatility-normalized path/shape features. Raw price levels are not included directly.