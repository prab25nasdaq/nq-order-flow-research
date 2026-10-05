# AFML Trade Lifecycle Shadow Engine v1

SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO DATABENTO / NO PAPER TRADING.

Implements only methodology from Marcos López de Prado's *Advances in
Financial Machine Learning* (AFML): dynamic-volatility-threshold
triple-barrier labeling (Ch.3), meta-labeling (Ch.3 §3.6), purged/embargoed
validation (Ch.7), probability-based bet sizing / averaging active bets /
size discretization (Ch.10), and a research-only strategy lifecycle ledger
(Ch.1 oversight lifecycle, applied here only up through "embargo" — no
graduation/re-allocation/decommission step exists because this engine is
never connected to paper trading or execution in the first place).

**No discretionary trading rule is invented anywhere in this codebase.**
The primary trade side comes exclusively from the existing production
level-reaction model's probability/direction output
(`latest_continuous_nq_predictions.csv`). This engine only ever decides
*whether and how much* to size a bet on a side someone else already called
— never which side to take.

Read the full results in
[`reports/AFML_TRADE_LIFECYCLE_SHADOW_V1_REPORT.md`](reports/AFML_TRADE_LIFECYCLE_SHADOW_V1_REPORT.md).

## Headline result (read before anything else)

The purged/embargoed walk-forward meta-model does **not** show a stable,
positive out-of-sample edge: pooled MCC is ≈0 on the full out-of-fold
population and **negative** (-0.15) on the small, genuinely-clean,
post-training-cutoff subset (472 events). Per AFML Ch.11, rejecting a
specification at this stage is the methodology working correctly, not a
bug in the engine. **PRODUCTION_READY: false. PAPER_TRADING_READY: false.**

## Why this exists / scope decisions

- Restricted to the **NQU6 contract** only, because true book-flow (bid/ask
  add-pull) meta-labeling features only exist for NQU6 in
  `book_flow_chart/cache/` — the model probability stream itself spans
  NQM6+NQU6, but this engine only uses the slice where both the primary
  signal and the OFI meta-features are available.
- `predictions.csv` is **~99% in-sample** for the active model release
  (proved in the prior institutional audit,
  `research_reports/ofi_model_level_alpha_research_*`). Every event here
  carries an `in_sample_contaminated` flag, and every validation metric is
  reported twice — once for the full out-of-fold population, once for the
  genuinely-clean subset — never collapsed into a single number.
- All triple-barrier / bet-sizing parameters in `configs/lifecycle_config.yaml`
  were fixed *before* any script was run against data. A ptSl multiple grid
  is reported as an explicitly-labeled research-only diagnostic (AFML
  Ch.11: backtesting rejects bad specs, it never selects good ones) and was
  never used to retune the primary config.

## Folder layout

```
raw_snapshot/     frozen, read-only copy of production inputs (see 00_snapshot_inputs.py)
scripts/          00..09, run in order; afml_common.py holds all shared AFML logic
configs/          lifecycle_config.yaml - every parameter, set a priori
outputs/          every research ledger (parquet/csv/json) - see below
reports/          AFML_TRADE_LIFECYCLE_SHADOW_V1_REPORT.md - the full writeup
```

## Pipeline (run in order)

| Script | Produces | AFML chapter |
|---|---|---|
| `00_snapshot_inputs.py` | `raw_snapshot/` | — (read-only data hygiene) |
| `01_build_candidate_events.py` | `candidate_events.parquet` | Ch.1 (opportunity, not a trade) |
| `02_apply_triple_barrier.py` | `triple_barrier_labels.parquet`, ptSl grid (research-only) | Ch.3 |
| `03_build_meta_label_dataset.py` | `meta_label_dataset.parquet` | Ch.3 §3.6 |
| `04_train_meta_label_model.py` | `meta_model_predictions.parquet`, `meta_model_fold_log.csv` | Ch.7 (purge/embargo) |
| `05_afml_bet_sizing.py` | `bet_sizing_signal.parquet` | Ch.10 |
| `06_average_active_bets.py` | `active_bet_timeline.parquet` | Ch.10 |
| `07_shadow_position_lifecycle.py` | `shadow_position_lifecycle.*`, `shadow_position_bar_timeline.*`, `shadow_active_bets.csv` | Ch.1 lifecycle, ledger only |
| `08_purged_embargo_validation.py` | `validation_results.csv`, `fold_results.csv`, `leakage_checks.json` | Ch.7 / Ch.14-15 |
| `09_generate_report.py` | `reports/AFML_TRADE_LIFECYCLE_SHADOW_V1_REPORT.md`, `final_report_summary.json` | — |

Each script reads only from `raw_snapshot/` or this engine's own `outputs/`,
and writes only inside this engine folder. None of them import or call any
broker, order-routing, dashboard, or Book Flow chart module.

## Re-running

```bash
cd scripts/
for f in 0*.py; do python3 "$f" || break; done
```

`afml_common.py` requires `pyyaml`, `numpy`, `pandas`, `scipy`, `scikit-learn`
(all already available in this environment).

## Acceptance criteria (verbatim from the build spec)

- PRODUCTION_FILES_MODIFIED: **false**
- TRADING_ENABLED: **false**
- BROKER_CONNECTED: **false**
- PAPER_TRADING_ENABLED: **false**
- OVERALL: **PASS** (built read-only, every required ledger produced)
- PASS does **not** mean the strategy is tradable.
