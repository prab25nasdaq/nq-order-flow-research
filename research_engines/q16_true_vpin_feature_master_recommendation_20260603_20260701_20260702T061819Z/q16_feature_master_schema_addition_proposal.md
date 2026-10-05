# Feature Master Schema Addition Proposal — True VPIN
Generated: 2026-07-02T06:31:15Z
SHADOW / RESEARCH ONLY — DO NOT PATCH FEATURE MASTER YET

## Decision Summary
| Column | Decision | Reason |
|--------|----------|--------|
| tvpin_pct_V500_W10_L500 | RESEARCH_ONLY | Marginal predictive value vs current VPIN |
| tvpin_pct_V500_W50_L1000 | RESEARCH_ONLY | perm_p=0.348 > 0.20 — not significant |
| true_signed_vpin_delta_W10 | RESEARCH_ONLY | Marginal predictive value vs current VPIN |
| true_signed_vpin_delta_W50 | RESEARCH_ONLY | Marginal predictive value vs current VPIN |
| true_buy_toxicity_W50 | INCLUDE_AFTER_30D_SHADOW | hit_rate_H10=0.5319 — promising, needs shadow |
| true_sell_toxicity_W50 | RESEARCH_ONLY | perm_p=0.258 > 0.20 — not significant |
| true_toxic_balance_W50 | RESEARCH_ONLY | Marginal predictive value vs current VPIN |
| vpin_divergence_pct_delta | RESEARCH_ONLY | perm_p=0.968 > 0.20 — not significant |

## Proposed additions (after 30-day shadow)
```python
# Feature Master daemon additions — add to bar_features dict:
fmaster_true_vpin_pct_V500_W10_L500   = tvpin_pct_V500_W10_L500   # rolling_pct_rank(true_vpin_V500_W10, 500)
fmaster_true_vpin_pct_V500_W50_L1000  = tvpin_pct_V500_W50_L1000  # rolling_pct_rank(true_vpin_V500_W50, 1000)
fmaster_true_sell_toxicity_W50         = tvpin_pct_V500_W50_L500 * max(-delta_norm, 0)
fmaster_true_buy_toxicity_W50          = tvpin_pct_V500_W50_L500 * max(delta_norm, 0)
fmaster_vpin_divergence_pct_delta      = tvpin_pct_V500_W50_L500 - cur_vpin_pct_L500
```

## Pre-conditions for patch
1. 30-day shadow observation log must show stable, consistent values
2. Feature Master daemon review must confirm no lookahead path
3. Live compute cost must be benchmarked on actual daemon tick
4. Architecture option selected (Option 1 or 2) and implemented as separate PR
5. Rollback plan documented (easy: just remove the columns from bar_features dict)

## DO NOT DO YET
- Do not modify feature_master_daemon.py
- Do not add columns to model_feature_master_shadow.parquet
- Do not add columns to model_feature_master_schema.json
