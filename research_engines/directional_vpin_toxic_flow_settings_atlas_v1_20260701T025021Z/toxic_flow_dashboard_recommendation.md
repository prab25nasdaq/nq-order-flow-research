# TOXIC FLOW Dashboard Panel — Integration Recommendation
Generated: 2026-07-01T02:56:40Z  |  SHADOW / RESEARCH ONLY

**DO NOT PATCH DASHBOARD WITHOUT EXPLICIT REQUEST.**
This is a recommendation only.

## Proposed Tab: `TOXIC FLOW`
Add as a new tab in the existing dashboard notebook (after LEAD/LAG SPEARMAN MAP).

## Display Panels

### Panel 1 — Live Toxic Flow State (top left, always visible)
```
┌─────────────────────────────────────────────────────┐
│  TOXIC FLOW STATE                                    │
│  VPIN (W120 L500):  0.847  [ELEVATED]               │
│  signed_vpin_delta: -0.342  ←  SELL-TOXIC           │
│  sell_toxicity:      0.231  ↑ HIGH                  │
│  buy_toxicity:       0.071  ↓ LOW                   │
│  toxic_side_direction: SELL_TOXIC                   │
│  toxic_side_balance:  -0.160                        │
│                                                      │
│  PHASE:  ██ VPIN_BEARISH_PHASE ██                   │
│  Spearman ρ (W240 H10):  -0.284  [BEARISH SIGNAL]   │
└─────────────────────────────────────────────────────┘
```

### Panel 2 — Context Filter (top right)
```
┌─────────────────────────────────────────────────────┐
│  LEVEL CONTEXT                                       │
│  Near S/R: YES (dist=2.5 ticks)                     │
│  Level type: HVN                                     │
│  Support test: NO   Resistance test: YES             │
│  BidPull > BidAdd: YES  (support consumed)          │
│  AskPull > AskAdd: NO                               │
│  Bullish book switch: NO   Bearish: NO              │
└─────────────────────────────────────────────────────┘
```

### Panel 3 — Composite State Output
```
STATE: SELL_TOXIC_CONFIRMED
```
Possible states (in priority order):
| State                        | Condition                                              |
|------------------------------|--------------------------------------------------------|
| `SELL_TOXIC_CONFIRMED`       | sell_toxicity high + resistance_consumed + VPIN>0.90   |
| `BUY_TOXIC_CONFIRMED`        | buy_toxicity high + support_consumed + VPIN>0.90       |
| `TOXIC_TRANSITION_BEARISH`   | signed_vpin_delta flipping negative + VPIN>0.70        |
| `TOXIC_TRANSITION_BULLISH`   | signed_vpin_delta flipping positive + VPIN>0.70        |
| `TOXIC_BUT_DIRECTIONLESS`    | VPIN>0.90 but toxic_side_balance near zero             |
| `LOW_TOXICITY`               | VPIN<0.70                                              |

### Panel 4 — Rolling Spearman Phase Chart (bottom)
- Matplotlib: rolling Spearman ρ of signed_vpin_delta vs fwd_H10
- Windows: W120 (blue), W240 (orange), W480 (white)
- Colour: negative=red, positive=green, zero=grey
- Horizontal reference lines at ±0.20 and ±0.10
- Annotation: phase transitions (▲ BULLISH / ▼ BEARISH)

## Key Features Required (live-safe, no lookahead)
All features below exist in master ndjsonl or computable from it:
| Feature | Source | Live-safe |
|---------|--------|-----------|
| vpin (= \|delta_norm\|) | master | YES |
| signed_vpin_delta = vpin_pct * sign(delta_norm) | computed | YES |
| buy_toxicity = vpin_pct * max(delta_norm, 0) | computed | YES |
| sell_toxicity = vpin_pct * max(-delta_norm, 0) | computed | YES |
| toxic_side_balance = buy_tox - sell_tox | computed | YES |
| toxic_side_direction (categorical) | computed | YES |
| Rolling Spearman ρ (sealed bars, no lookahead) | computed | YES |
| VPIN phase label | computed | YES |
| Level context (via OFI level decision JSON) | ofi_level_decision/ | YES |
| Book switch context (via book_flow_chart/) | book_flow_chart/ | YES |

## Feature Master Candidates (if confirmed live-safe)
These can be added to feature master after review:
- `fmaster_signed_vpin_delta` = vpin_pct * sign(delta_norm)
- `fmaster_buy_toxicity` = vpin_pct * max(delta_norm, 0)
- `fmaster_sell_toxicity` = vpin_pct * max(-delta_norm, 0)
- `fmaster_toxic_side_balance` = buy_toxicity - sell_toxicity
- `fmaster_toxic_side_direction` = categorical (4 states)

## Implementation Notes
- All features use sealed bars only (no forming-bar lookahead)
- VPIN percentile window: 500 bars (production standard)
- Rolling Spearman: W=240 bars, H=10 bars forward (best setting from atlas)
- Permutation p-value: skip for live display (too slow); use sign stability instead
- Update on each bar seal event (same as other dashboard panels)
- No broker, no execution, no order logic — DISPLAY ONLY