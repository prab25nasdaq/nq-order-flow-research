# VPIN Formula Comparison Audit
Generated: 2026-07-02T03:20:49Z
SHADOW / RESEARCH ONLY

## Current Dashboard VPIN (bar-level approximation)
```
current_vpin_bar = |buy_vol - sell_vol| / vol_total
                 = |delta_norm|   (since delta_norm = (buy_vol-sell_vol)/vol_total)
```
- **Clock**: Fixed-volume bar clock (500 contracts per bar)
- **Granularity**: One observation per sealed bar
- **Buy/sell split**: Rithmic tape aggressor classification (accumulated per bar by the recorder)
- **Rolling smoothing**: Optional EWM post-hoc
- **Normalization**: Rolling 500-bar percentile rank → vpin_pct
- **Limitation**: Unsigned — magnitude only, no direction

## True Easley/López de Prado VPIN (2011, 2012)
```
true_vpin(τ) = rolling_sum(|VB_τ - VS_τ|, n) / rolling_sum(VB_τ + VS_τ, n)
```
- **Clock**: Volume clock — equal-volume buckets V (e.g., 500 contracts each)
- **Bucket construction**: Stream through raw trade prints; accumulate size until V reached;
  trades crossing bucket boundaries split proportionally.
- **VB_τ**: Sum of buy-initiated sizes within bucket τ (aggressor_side == "B")
- **VS_τ**: Sum of sell-initiated sizes within bucket τ (aggressor_side == "S")
- **Rolling window n**: Typically 50 buckets (≈ one trading day at V=500)
- **Key difference**: Bucket clock is independent of time — bucket completion times
  cluster around high-activity periods; sparse in low-volatility periods.

## Data Source
- Master bars: /home/prabh/OFI_Live_Features/master_NQU6_shadow.ndjsonl
- Raw trades: /home/prabh/OFI_Live_Data/Rithmic_Raw/<date>/NQU6/trades.ndjson
- Aggressor side field: "aggressor_side" ∈ {"B", "S"}
- Trade size field: "size" (contracts)

## Key Structural Differences
| Property | Current VPIN | True VPIN |
|---|---|---|
| Time clock | Volume-bar | Volume-bucket |
| Bar/bucket size | 500 vol/bar | Variable (200–5000 tested) |
| Observation count | = n_bars | = n_buckets > n_bars |
| Direction | Unsigned | Unsigned (per bucket) |
| Normalization | Pct rank over bars | Intrinsic (ratio, range 0–1) |
| Lookahead risk | None (sealed bars) | None (sealed buckets before bar_end) |
| Rithmic dependency | Bar-level buy/sell | Raw trade-level prints |

## Alignment Protocol (no lookahead)
For each sealed bar ending at T_bar:
  true_vpin_at_bar = true_vpin(τ_max)  where τ_max = last bucket with end_ts_ns ≤ T_bar
