# PRICE TRAVEL / LIQUIDITY VACUUM — Dashboard Panel Recommendation
**Status**: SHADOW / RESEARCH ONLY — NOT YET PROMOTED

## Proposed Tab: "PRICE TRAVEL / LIQUIDITY VACUUM"

### Panel A — Current Travel State
- Travel bucket: LOW / NORMAL / HIGH / EXTREME
- Directional: UP_TRAVEL / DOWN_TRAVEL / TWO_WAY_CHOP / LOW_TRAVEL_ABSORPTION
- Bar range (pts) vs 20-bar rolling median
- Efficiency: body / range ratio

### Panel B — Liquidity Vacuum Gauge
- Liquidity vacuum score (composite z-score)
- Ask-side removed (resistance_removed_score)
- Bid-side removed (support_removed_score)
- Replenishment failure score
- Absorption score

### Panel C — Book Flow Pressure
- Up-travel pressure (resist_removed + bullish_switch)
- Down-travel pressure (support_removed + bearish_switch)
- BidAdd vs BidPull (bar chart)
- AskAdd vs AskPull (bar chart)
- Range per volume (efficiency)

### Panel D — Pre-Bar Precursor Warnings
- Pre-5 AskPull trend (rising = potential UP squeeze)
- Pre-5 BidPull trend (rising = potential DN squeeze)
- Pre-5 toxicity trend
- Precursor state: NEUTRAL / PRE_UP_SQUEEZE / PRE_DN_SQUEEZE / PRE_ABSORB

### Panel E — Microstructure Measures
- Kyle λ (price impact)
- Amihud illiquidity
- OFI (Cont-style)
- Add/pull replenishment ratio

### Implementation notes:
- All data from Feature Master + BF level candles (already available)
- No new data sources required
- Do NOT patch dashboard until explicitly requested
