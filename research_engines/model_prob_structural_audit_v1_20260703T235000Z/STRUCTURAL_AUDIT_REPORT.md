# Audit 1 — Model Probability Structural Audit v1
**Generated**: 2026-07-03T23:20:21.359317+00:00
**SHADOW / RESEARCH ONLY — no execution, no broker, no order placement**
**Source PBO run**: model_prob_pbo_window_family_audit_v1_20260703T230000Z

---

## Summary: STRUCTURAL PASS

**18 checks PASS  |  0 checks FAIL**

All structural properties verified — safe to proceed to Audit 2 (Calibration).

---

## Checks

| # | Check | Result | Detail |
|---|-------|--------|--------|
| 1 | OOS table exists | **✓ PASS** | path=/home/prabh/OFI_Production/research_engines/model_prob_pbo_window_family_audit_v1_20260703T230000Z/oos_probability_ |
| 2 | OOS event count reasonable (>50K events per version) | **✓ PASS** | VA=61,122, VB=61,164 |
| 3 | Same OOS event universe across all windows | **✓ PASS** | VA: 61,122 events (SAME); VB: 61,122 events (SAME) |
| 4 | No in-sample probabilities in table | **✓ PASS** | total=733,716  is_oos_true=733,716  is_oos_false=0 |
| 5 | Events not in own training fold | **✓ PASS** | Sampled 5,000 events. Errors: 0. |
| 6 | Fold date coverage (event date in fold val_dates) | **✓ PASS** | Sampled 5,000. Errors: 0. |
| 7 | No leakage columns in feature_names.json | **✓ PASS** | 77 features. Leaked: NONE |
| 8 | net_h10 = pred_dir × fwd_return_ticks_h10 - cost_ticks | **✓ PASS** | Sampled 2,000. Formula errors: 0. |
| 9 | Cost model: cost_ticks = 2.0 (round-trip) everywhere | **✓ PASS** | Unique cost_ticks values: [2.0]. Expected: 2.0 |
| 10 | Probability bounds: prob_long ∈ (0,1), prob_long+prob_short≈1 | **✓ PASS** | Out-of-range: 0  Sum!=1: 0 |
| 11 | pred_side consistent with prob_long > 0.5 | **✓ PASS** | Bad LONG: 0  Bad SHORT: 0  Ties at 0.5: 0 |
| 12 | Window features routed correctly (prob_long varies across W) | **✓ PASS** | VA: spread=0.04056 ({5: 0.41554216554842477, 10: 0.4172435594291537, 20: 0.43941902757858603, 30: 0.43435302596075354, 5 |
| 13 | Window accuracy varies (not perfectly flat → windows are different) | **✓ PASS** | VA: acc spread=0.0653 {5: 0.6401132161905697, 10: 0.6327508916593043, 20: 0.633519845554792, 30: 0.6313765910801348, 50: |
| 14 | Version B (deferred) differs from Version A on >50% events | **✓ PASS** | Fraction of events where \|prob_A - prob_B\| > 1e-6: 1.000 |
| 15 | Train-only scaler (code inspection) | **✓ PASS** | fit_on_va=False  fit_scl_on_va=False  fit_scaler_on_tr=True  fit_imp_on_tr=True |
| 16 | No lookahead labels in feature selection (code + data) | **✓ PASS** | Leaked in feat_names: []  label_in_features_code: False |
| 17 | Window routing in _build_window_features uses W parameter | **✓ PASS** | rolling(W): True  _delta_rolling: True  _mlofi_accel: True  _dn_resid_z: True |
| 18 | Net_h10 unit clearly defined (ticks, not points) | **✓ PASS** | COST_TICKS=2.0: True  formula uses COST_TICKS: True  unit: net_return_h10 is in NQ ticks (1 tick = 0.25 pts). Round-trip |

---

## Net_h10 Unit Definition

```
net_return_h10 = pred_dir × fwd_return_ticks_h10 - COST_TICKS
where:
  pred_dir  = +1 if pred_side == LONG else -1
  fwd_return_ticks_h10 = ticks gained/lost H=10 bars forward
  COST_TICKS = 2.0 (round-trip, 2 NQ ticks at 0.25 pts/tick = 0.50 pts)
  1 NQ tick = 0.25 pts = $1.25 per MNQ contract = $5.00 per NQ contract
```

---

## Lock Manifest (v3 master hash)

| File | SHA256 (first 16) |
|------|------------------|
| feature_names.json | aa8e7290228ac2c0… |
| training_config.json | bccffa831c0b9761… |
| release_manifest.json | 07d1de9f79efccc3… |

---

## Next Step

**Proceed to Audit 2 — Probability Calibration Audit**

---

## Final Status
```
STRUCTURAL_AUDIT_PASS:                  True
CHECKS_PASS:                            18
CHECKS_FAIL:                            0
PRODUCTION_FILES_MODIFIED:              false
TRADING_ENABLED:                        false
OVERALL:                                PASS
```