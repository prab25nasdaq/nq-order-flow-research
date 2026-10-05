# OFI Phase 2B Report: Signal Iteration + Track 2 CNN

Generated: 2026-07-28T04:37:28Z

**h=40 is the REGISTERED PRIMARY endpoint.** All other horizons/metrics (h10, h20, h60, h80) are EXPLORATORY. Universe: NQU6 only (NQM6 EXCLUDED from all training/evaluation per locked decision from Phase 2 results).

## Model comparison (mean across folds)

| model                                         |       ic_h10 |      ic_h20 |     ic_h40 |   ic_h40_tstat |    ic_h40_rth |   decile_spread_pts |   decile_spread_cost_adj_pts |      ic_h60 |      ic_h80 |
|:----------------------------------------------|-------------:|------------:|-----------:|---------------:|--------------:|--------------------:|-----------------------------:|------------:|------------:|
| GBM v1 baseline (Phase 2)                     |   0.00645736 |   0.0202775 |  0.0522065 |       1.70976  |   0.0346423   |           nan       |                    nan       | nan         | nan         |
| GBM tuned (Part J)                            |   0.0236299  |   0.0313693 |  0.0247832 |       0.759661 |   0.0106686   |             7.41632 |                      6.41632 |   0.0359246 |   0.127257  |
| GBM CONTROL: trailing+time only (h40 only)    | nan          | nan         |  0.0217001 |       0.736205 | nan           |           nan       |                    nan       | nan         | nan         |
| CNN Track 2 (Part K)                          |   0.0090582  |   0.0030605 | -0.0419316 |      -1.99103  |  -0.000401318 |           -16.5075  |                    -17.5075  | nan         |   0.0250334 |
| Blend: rank(GBM tuned) + rank(CNN) (h40 only) | nan          | nan         | -0.0148379 |      -0.576311 |   0.00247293  |            -3.78622 |                     -4.78622 | nan         | nan         |

Columns ic_h10/h20/h60/h80 are EXPLORATORY. ic_h40 / ic_h40_tstat / ic_h40_rth / decile_spread_* are the PRIMARY comparison. CONTROL and ablation ic_h40 are computed overall-only (no RTH split, to bound compute) -- ic_h40_rth is blank there by design, not missing data.

## Key finding: cell geometry vs price history (Part J ablations)

Full tuned GBM model mean IC (h40): **0.0248**. The trailing+time-only CONTROL (zero cell-geometry features) mean IC (h40): **0.0217** (delta vs full: -0.0031). Per-family drop-one / only-one deltas are in gbm2_ablation_results.csv (mean deltas also summarized in part_J_report.json).

Interpretation: if the CONTROL nearly matches the full model, cell-geometry features (bands/levels/side-zone/pressure/profile/context) are contributing little beyond what trailing price-history and time-of-day features already capture, at least for the tuned GBM track at this data volume -- a result to weigh directly against the CNN's use of the full ladder geometry below.

## Fold-by-fold detail

- GBM tuned: gbm2_tuned_results.csv (sweep grid + chosen hyperparams: gbm2_sweep_results.csv)
- GBM ablations: gbm2_ablation_results.csv
- GBM economics: gbm2_economics.csv
- GBM importance stability: gbm2_importance_fold_correlation.csv (mean pairwise Spearman corr: 0.7443776636601029)
- CNN: cnn_results.csv, cnn_economics.csv (param count: 174340, brief target ~0.3-0.5M -- measured count is below that range using the brief's literal channel-width spec 8->32,32,64,64,128, reported not force-fit)
- Blend: blend_results.csv

## Calibration tables (Part I)

### Global reference: |z_40| -> pts/ticks

|   abs_z40 |     pts |    ticks |   global_median_sigma_t |   global_unit_pts_per_z |
|----------:|--------:|---------:|------------------------:|------------------------:|
|      0.25 | 20.5281 |  82.1126 |                 12.9831 |                 82.1126 |
|      0.5  | 41.0563 | 164.225  |                 12.9831 |                 82.1126 |
|      1    | 82.1126 | 328.45   |                 12.9831 |                 82.1126 |

### Per-fold train 30/70 z_40 quantile cuts (pt/tick equivalents at fold-median sigma_t)

|   fold_id |   n_train_fit |   q30_z40 |   q70_z40 |   fold_median_sigma_t |   q30_pts |   q70_pts |   q30_ticks |   q70_ticks |
|----------:|--------------:|----------:|----------:|----------------------:|----------:|----------:|------------:|------------:|
|         0 |          4724 | -0.465231 |  0.499156 |               12.399  |  -36.4826 |   39.1429 |    -145.93  |     156.572 |
|         1 |          7401 | -0.528959 |  0.495351 |               12.9526 |  -43.332  |   40.5789 |    -173.328 |     162.315 |
|         2 |          9750 | -0.562294 |  0.493624 |               13.9013 |  -49.4367 |   43.3993 |    -197.747 |     173.597 |
|         3 |         11641 | -0.500739 |  0.529805 |               13.8647 |  -43.909  |   46.4577 |    -175.636 |     185.831 |
|         4 |         13836 | -0.562204 |  0.504101 |               13.8283 |  -49.169  |   44.0875 |    -196.676 |     176.35  |
|         5 |         14832 | -0.544654 |  0.509669 |               13.8489 |  -47.7051 |   44.6409 |    -190.82  |     178.564 |
|         6 |         17669 | -0.513661 |  0.516572 |               13.6927 |  -44.4833 |   44.7354 |    -177.933 |     178.941 |
|         7 |         19436 | -0.513528 |  0.499289 |               13.5638 |  -44.053  |   42.8315 |    -176.212 |     171.326 |
|         8 |         21266 | -0.512986 |  0.505232 |               13.4974 |  -43.7909 |   43.1291 |    -175.164 |     172.516 |
|         9 |         23398 | -0.541587 |  0.492158 |               13.4811 |  -46.1769 |   41.9624 |    -184.708 |     167.85  |

## MASTER_DIR integrity check

- master_untouched: **True**
- mtimes: {"ofi_cell_master_long.parquet": 1784925587.9521687, "ofi_bar_packed_cells_master.parquet": 1784925587.9651685, "ofi_bar_packed_cells_master_with_context.parquet": 1784925589.12615, "daily_coverage_audit.csv": 1784925591.3995857}
