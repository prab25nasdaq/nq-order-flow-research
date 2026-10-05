# VPIN Book Flow Control — Dashboard Recommendation
**SHADOW / RESEARCH ONLY**

## Proposed Panel: VPIN BOOK FLOW CONTROL

```
VPIN z20: -0.91  | State: VPIN_Z_NEGATIVE
VPIN pct:  0.38  | Toxicity: NORMAL
Buyer:     1240  | Seller:   1890   | Balance: -650
Direction: SELLER_DOMINANT
Support consumption:    +320  (bid support consumed)
Resistance consumption: +85   (ask resistance clearing)
Book switch net: -0.18  Bull: 0.32  Bear: 0.50
COMBINED STATE: LOW_TOXIC_SELLER_CONTROL
SWITCH EVENT:   [NONE / BUYER->SELLER / SELLER->BUYER]
```

## Color Coding
- GREEN: HIGH_TOXIC_BUYER_CONTROL / SELLER_TO_BUYER_SWITCH
- RED: HIGH_TOXIC_SELLER_CONTROL / BUYER_TO_SELLER_SWITCH
- YELLOW: TOXIC_CONFLICT
- GRAY: LOW_TOXIC_CHOP / NEUTRAL

## Implementation Notes
- All features computable from existing OFILD columns (no new data sources)
- Add to Feature Master daemon; display as read-only dashboard label
- Flash switch event color for 3 bars after transition
- Dashboard code NOT modified — this is a proposal only
