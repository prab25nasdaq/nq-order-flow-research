# Audit 8B — Empirical Book Event Semantics Atlas
**Generated**: 2026-07-05T00:55:47.801625+00:00
**SHADOW / RESEARCH ONLY — no execution, no broker, no order placement**
**Principle: NO pre-assigned directional semantics. Data defines meaning.**

---

## 1. Data Coverage

| Source | Rows | Dates | Coverage |
|--------|------|-------|----------|
| book_switching_feature_panel (bar-level) | 8,923 | Jun 14–24 | 9/18 OOS days |
| level_touch_full_book_mechanics (events) | 19,469 | Jun 14–23 (only 2,076 have pre-event primitives) | Supplementary |
| OOS V=B W=20 ≥0.65 signals | 43,862 | Jun 07–30 | Full OOS |
| OOS signals with bar book data | 11,661 | Jun 14–24 | **26.6%** |

**Coverage limitation**: Jun 07–13 (missing first week incl. Jun 08 +376K win day) and Jun 25, 28–30 (missing Jun 28 regime failure) have NO book data.

---

## 2. Causal Verification

- BF features (BF_bid_add/pull, BF_ask_add/pull): computed during bar N (causal)
- Forward returns: computed from px_close at bar N+H relative to bar N (causal)
- post_* event windows from book mechanics: **explicitly excluded** (future data)
- Causal constraint: **SATISFIED**

---

## 3. Empirical Role Atlas

| Role | Count |
|------|-------|
| CONTEXT_ONLY | 19 |
| CONTRADICTORY | 8 |

---

## 4. Standalone IC (H10, top Spearman)

| Feature | IC (IS) | IC (OOS) | Net Long H10 | Net Short H10 |
|---------|---------|----------|-------------|---------------|
| pull_add_ratio | -0.0196 | +0.0572 | +3.51t | -7.66t |
| book_event_pressure | +0.0085 | +0.0128 | -2.18t | +3.21t |
| bid_event_balance | +0.0128 | -0.0034 | -1.33t | +4.02t |
| net_add_side | -0.0367 | -0.0124 | -13.47t | -3.71t |
| net_pull_side | -0.0411 | -0.0172 | -10.48t | -2.65t |
| BF_ask_pull | +0.0735 | -0.0179 | +0.48t | +4.36t |
| BF_ask_add | +0.0724 | -0.0200 | -0.14t | +3.41t |
| total_pull | +0.0674 | -0.0244 | -3.13t | +2.99t |
| two_sided_pull | +0.0674 | -0.0244 | -3.13t | +2.99t |
| event_churn | +0.0674 | -0.0250 | -3.58t | +3.65t |

---

## 5. PBO / DSR

| Item | Value |
|------|-------|
| Total tested configurations | 720 |
| Configurations evaluated for IS/OOS | 31 |
| Family PBO | 1.000 |
| DSR proxy | 0.684 |
| Best OOS IC | +0.0572 (pull_add_ratio) |
| OOS IC > 0 | 2/31 |
| Promoted | 0 |
| Deferred | 0 |
| Killed | 31 |

---

## 6. Toxicity Interaction

- OOS coverage: 11,661/43,862 (26.6%)
- prob_edge Spearman IC with H10 return: +0.1038
- book_event_pressure Spearman IC with H10 return: -0.0453

---

## 7. Discovered States

| State | N | Avg H10 | WR | Role |
|-------|---|---------|-----|------|
| 5 | 50 | +54.68t | 0.640 | LONG_SUPPORTIVE |
| 11 | 6,699 | -0.36t | 0.502 | NOISE |
| 8 | 627 | -5.95t | 0.450 | SHORT_SUPPORTIVE |
| 3 | 1,366 | -8.08t | 0.446 | SHORT_SUPPORTIVE |
| 9 | 51 | -53.43t | 0.275 | SHORT_SUPPORTIVE |
| 12 | 66 | -59.48t | 0.303 | SHORT_SUPPORTIVE |
| 4 | 54 | -76.85t | 0.296 | SHORT_SUPPORTIVE |

---

## 8. Recommendation

**DEFER_BOOK_EVENT_RESEARCH**

Family PBO=1.00 (high). No feature promoted. Coverage=26.6%.

---

## Final Status
```
AUDIT_8B_COMPLETE:             true
RECOMMENDATION:               DEFER_BOOK_EVENT_RESEARCH
PRODUCTION_FILES_MODIFIED:     false
TRADING_ENABLED:               false
```
