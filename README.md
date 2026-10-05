# NQ Order-Flow Research & Shadow Trading System

**SHADOW / RESEARCH ONLY.** This repository contains the source code and research reports from
a personal research system built to capture, process, and analyze live CME NQ (Nasdaq-100
E-mini futures) order-flow data from Rithmic's market data feed. No code here places, routes,
or executes real orders — every model and signal in this system runs in shadow mode, logging
predictions against live data without ever touching a broker.

This is a curated subset of a much larger (28GB+) private working repository, selected to show
the engineering and research methodology without including:
- Rithmic's proprietary SDK/API libraries (licensed, not redistributable).
- Raw tick data, trained model weight files, or generated feature/report data (too large for
  git, and not meaningful without the full multi-hundred-GB dataset behind them).
- Credentials, API keys, or any other operational secrets (verified absent from every file in
  this repo before publishing).

## What's here

### `rithmic_capture/`
`SampleMD.cpp` — the live market-data capture client against Rithmic's `RApiPlus` C++ SDK:
subscribes to depth/quote/trade feeds, reconstructs the order book, and writes raw NDJSON
streams to disk. `REBUILD_RUNBOOK.md` documents a real production fix (a missing
`setprecision()` call that silently truncated depth price precision) and the deliberate decision
to defer redeployment to a scheduled maintenance window rather than interrupt a live capture.

### `live_parser/`
The production C++ feature-pipeline parser: reads the raw Rithmic NDJSON capture, reconstructs
the limit order book, and builds volume/dollar bars with ~100+ order-flow features per bar
(multi-level order-flow imbalance, VPIN, sweep detection, CUSUM regime breaks, and more).
`PARSER_MLOFI_PARITY_PATCH_REPORT.md` documents a real parity bug found and fixed between the
live and backtested feature computation paths.

### `model_feature_master/scripts/`
The feature-master daemon: polls the live bar stream, maintains a rolling feature panel, and
feeds downstream models — the bridge between raw market data and anything that consumes it.

### `inference_scripts/`
Live shadow-inference loops: HistGradientBoosting and logistic-regression entry models,
continuous level-reaction classifiers, and the chart/dashboard code that visualizes live
predictions against the order-flow chart.

### `model_registry/`
Versioned training/update pipelines for the shadow models actually running in production:
dataset builders, baseline trainers, audit scripts (label leakage checks, registry integrity
checks), and live-parity validators that confirm a backtested feature computation matches what
the live parser actually produced.

### `research_engines/`
~20 independent research studies, each a self-contained pipeline with its own scripts and
written findings — the actual research process, not just conclusions:
- **AFML-style labeling/meta-labeling pipelines** (triple-barrier labeling, CUSUM event
  detection, meta-labeling, bet sizing, purged/embargo cross-validation) following López de
  Prado's *Advances in Financial Machine Learning* methodology.
- **Overfitting audits**: probability-of-backtest-overfitting (PBO) and deflated Sharpe ratio
  analysis, model lineage/calibration audits.
- **Market microstructure studies**: order-book level mechanics, VPIN/toxic-flow regime
  analysis, book-thickness and auction-friction atlases, case studies of specific large price
  moves traced back to their order-flow cause.

### `v11_reversal/` and `model_training_masters/`
A specific shadow model's retraining pipeline, and a from-scratch training-master rebuild with
full schema/integrity documentation.

### `backtest_harness/`
A separate, later effort: a causality-guarded backtest engine that lets a model propose a
trading signal as a single pure function (`signal(df) -> bool Series`) rather than free-form
code. The harness — not the model — owns data loading, the one-bar execution shift, the
train/test split, PnL, and every metric. A dedicated leakage guard
(`run_signal_with_leakage_guard`) re-runs every submitted signal against ~100 truncated
prefixes of the dataset and rejects anything whose output differs from the full-series
computation — a direct, mechanical proof of no look-ahead bias, not a heuristic.
`tests/test_agent_replay_parity.py` is a permanent regression gate confirming a
sequential, trade-by-trade execution simulator produces byte-identical results to the
vectorized backtest engine it mirrors.

## Methodology notes

A few things that show up repeatedly across these research reports, worth knowing before
reading them:
- **Every finding is reported honestly, including null results.** Several studies here
  conclude "no edge found" or "this result doesn't survive multiple-testing correction" — that's
  treated as a real, useful finding, not a failure to report positive results for.
- **Backtests are compared against randomized/permuted controls**, not evaluated in isolation —
  a strategy's Sharpe ratio is only meaningful next to what pure chance would produce at the
  same trial count (Bailey, Borwein, López de Prado & Zhu, 2014).
- **Real production bugs are documented, not hidden.** Several reports here are post-mortems of
  genuine bugs found in the live system (a data-ordering bug that silently corrupted a chunk of
  backtest results; a precision-truncation bug in the raw capture client) — including exactly
  what was wrong, how it was found, and what changed as a result.
