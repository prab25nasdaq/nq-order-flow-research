# Dashboard S/R Source Audit

Read-only audit of `ofi_live_dashboard_WORKING_NEXT_with_logreg.py`. No
dashboard code was modified to produce this audit.

## Safe / past-only (no leakage, safe for statistical testing as-is)

- **`swing_levels` / `compute_sr_levels`** (lines 597-651) — once a swing
  point has cleared its `lb`-bar confirmation lag, the resulting level is
  fully past-only. The dashboard's own call site (`self._win()`, lines
  4466-4472) already only looks backward from `current_idx`. **Safe to use
  directly for statistical testing once the confirmation lag is respected**
  (a level born at swing index `i` is only "known" starting at bar `i+lb`,
  not at `i` itself — Part B enforces this explicitly).
- **Volume profile formula itself** (POC/VAH/VAL/HVN/LVN math, lines
  4861-4894) — uses only bars already inside whatever window is fed to it;
  no forward bars are read. Safe as a *formula*.
- **Projected prior-contract levels file** (lines 325-348) — static,
  externally built, describes an already-completed contract. Safe to use
  as a fixed confluence reference.

## Display-only (correct for the live UI, but not meant to be reproduced as a backtest signal)

- **Volume profile's bar RANGE** (`i0, i1` from `self.ax_price.get_xlim()`,
  lines 4763-4766) — this is what makes POC/VAH/VAL/HVN/LVN values shift as
  a user zooms/pans the chart. It is a UI/display behavior, not a
  statistical method. Flagged as `DISPLAY_BEHAVIOR_ONLY` and **excluded**
  from Part B's rebuilt time series; Part B instead applies the identical
  POC/VAH/VAL/HVN/LVN math over the same fixed lookback windows used for
  swing S/R.
- **Visible-price-range label filtering** for swing S/R (`ch_lo - _pad <=
  px <= ch_hi + _pad`, lines 5252-5255) and the **top-6-per-side cap**
  (`max_r`/`max_s` = 6, lines 1873-1880) — these only decide which already-
  computed levels get a drawn line/label on screen; they do not change the
  underlying `compute_sr_levels` output itself. Not a leakage concern,
  irrelevant to Part B (Part B uses the full clustered level list, not the
  on-screen-visible/top-6 subset).

## Lookahead risk classification

| Component | Risk | Reason |
|---|---|---|
| Swing S/R level birth | MEDIUM (resolved by confirmation-lag handling) | A swing at bar `i` needs bars up to `i+lb` to exist before it can be confirmed. Part B treats the level as unknown until bar `i+lb`. |
| Swing S/R clustering/score | LOW once birth lag is handled | Clustering/score only use info already inside the (already past-only) window. |
| Volume profile formula | LOW | No bar outside the window is read. |
| Volume profile's live zoom range | N/A (excluded from quantitative testing) | Not a leakage issue — a reproducibility issue. Excluded per task instruction (`DISPLAY_BEHAVIOR_ONLY`, not used in Parts B-H). |
| Projected prior-contract file | LOW | Static, describes a finished contract. |

## What is excluded from statistical testing in this atlas

Only the live-zoom-range-dependent bar selection for the volume profile
(`ax_price.get_xlim()`) is excluded, per the task's explicit instruction:
*"if dashboard currently uses visible zoom, reproduce it separately as
DISPLAY_BEHAVIOR_ONLY and build the research version using fixed lookback
windows."* Everything else extracted above is either already past-only or
made past-only by a single, explicit, documented correction (the
swing-detection confirmation lag).

## Exact columns the dashboard's S/R logic reads

`px_high`, `px_low` (swing detection / clustering), `px_close` (chart
reference), `vol_total` (or `buy_vol`+`sell_vol` if present, else a
`delta_norm`-derived buy/sell split — volume profile only),
`timestamp_utc`/`timestamp` (cache keys, display only), and the static
`projected_levels_NQM6_to_NQU6.csv` columns `level_type`,
`projected_level_price`. No model-derived columns (z-scores, VPIN,
toxicity, liquidity-cost) feed the S/R formula itself — those are
independent dashboard tabs, used in this atlas only as Part E/G *context*
around retouch events, never as inputs to the S/R level definition itself.
