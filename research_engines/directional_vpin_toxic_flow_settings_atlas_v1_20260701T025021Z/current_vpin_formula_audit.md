# Current VPIN Formula Audit
**Generated**: 2026-07-01T02:56:40Z
**SHADOW / RESEARCH ONLY**

## Formula
```
vpin_bar = |buy_vol - sell_vol| / vol_total
         = |delta_norm|         (confirmed: max diff < 0.001)
```
## Type
- **Bar-level OFI approximation** — NOT Easley et al. (2011) volume-synchronized VPIN
- No volume bucketing; one observation per completed bar
- Buy/sell volume: real executed tape from Rithmic (not proxy)

## Dashboard pipeline (lines 3138–3149)
```python
# Primary path (when 'vpin' in master columns):
vpin = master_data['vpin'].ffill().fillna(0.5)   # = |delta_norm|

# Fallback (rare — when master lacks vpin column):
imb = |buy_vol - sell_vol|
vpin = rolling(75).sum(imb) / rolling(75).sum(vol_total)

# Normalization:
vpin_pct = rolling_pct(vpin, window=500)   # fraction of past values <= current
vpin_pct_latest = last_finite(vpin_pct)

# State thresholds:
NORMAL          vpin_pct < 0.70
ELEVATED        vpin_pct < 0.90
TOXIC           vpin_pct < 0.95
EXTREME TOXICITY vpin_pct >= 0.95
```
## Toxicity composite (line 3188)
```
toxicity = 0.45 * vpin_pct
         + 0.20 * spread_pct   (bid-ask spread rolling pct)
         + 0.15 * kyle_pct     (Kyle lambda rolling pct)
         + 0.10 * amihud_pct   (Amihud illiquidity rolling pct)
         + 0.10 * roll_pct     (Roll measure rolling pct)
```
## Critical Limitation
**VPIN is unsigned.** It measures the magnitude of order-flow imbalance,
not the direction. `vpin = 0.8` is equally consistent with 80% buy-side
or 80% sell-side toxicity. This is why directional/sided VPIN features
must be constructed using delta_norm, mlofi, and book-side data.

## Distribution (24,207 bars)
- Mean: 0.1337
- Std:  0.0399
- P95:  0.2036
- P99:  0.2532
- Max:  0.3804

## What VPIN does NOT capture
- Which side is informed (buy vs sell)
- Whether high toxicity is bullish or bearish
- Support/resistance context
- Book-switch direction
- Session/regime context