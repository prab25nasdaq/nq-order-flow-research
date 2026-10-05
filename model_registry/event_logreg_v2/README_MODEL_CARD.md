# Event LogReg V2 — Retrain through 2026-06-06

- release_id          : event_logreg_v2_retrain_20260606
- created_utc         : 2026-06-08T05:11:08.296439+00:00
- trained_through     : **2026-06-06**
- evaluation_allowed_from : **2026-06-07**
- status              : dashboard_only_experimental
- paper_trading_allowed: false
- production_execution_allowed: false

## What changed vs `event_logreg_v2_20260607`

- Master extended through 20260607 (includes Jun 3 / 4 / 5 / 6 if available)
- Re-fit V2 LogReg on events with day < 20260607.
- Jun 3-6 are now TRAINING data, not evaluation data.
- Forward evaluation reserved for **day >= 20260607**.

## Frozen V2 policy (unchanged)

- 63 features (`feature_set_v2_names.json`)
- POLICY_E_srv_tight
- TBM barriers: tp_sigma_max=5.0, sl_sigma_max=4.0, vertical_max=40, target_pct=0.7
- drift_guard_threshold = 4.0 (NOT retuned)
- seed = 42

## CV (within-training, purged expanding walk-forward)

- folds              : 5
- avg balanced acc   : 0.5493
- avg MCC            : 0.1061

## Forward evaluation (Jun 7+, drop-NEUTRAL binary)

```
{
  "status": "PASS",
  "label": "FORWARD_EVAL_JUN7_ONWARD",
  "scope": "events with day >= 20260607",
  "all_events": {
    "n": 9,
    "accuracy": 0.4444,
    "balanced_accuracy": 0.5,
    "mcc": 0.0,
    "roc_auc": 0.85
  },
  "drift_pass_only": {
    "n": 0,
    "accuracy": null,
    "balanced_accuracy": null,
    "mcc": null,
    "roc_auc": null
  },
  "n_drift_pass": 0,
  "n_drift_block": 9
}
```

## Important — Jun 3-6 are TRAINING data

- Jun 3-6 performance is NOT a fair evaluation — those days were included in the training set.
- Do NOT use Jun 3-6 to claim or refute model performance for this release.
- The drift guard threshold is frozen at 4.0 and was NOT recalibrated against Jun 3-6 results.

## Warnings
- Jun 5 missing from master
- Jun 6 missing from master