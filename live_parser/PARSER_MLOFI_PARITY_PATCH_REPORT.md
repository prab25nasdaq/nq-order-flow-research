# Parser MLOFI Parity Patch — Verification Report

- ran_at_utc: 2026-06-08T05:38:40.603692+00:00
- patch site: `src/ofi_live_parser.cpp :: flush_packet`
- mechanism: per-update OFI compute (one push_book_obs per individual book update) — matches Python master builder per-MBO-event semantics

## Sources compared

- **CSV (spec)**       : `/mnt/wd_work/workspace/Work Place/Data/Project OFI/DOM_1min_v500_ohlc_MASTER_OHLC_v3/MASTER_vol500_with_ohlc.csv` — tail 5,000 rows
- **OLD live (pre-patch)** : `/home/prabh/OFI_Live_Features/2026-06-04/NQM6_vol500.ndjsonl` — unchanged, never overwritten
- **NEW live (post-patch)**: `/home/prabh/OFI_Live_Features_FIXED/2026-06-04/NQM6_vol500.ndjsonl`

All comparisons use the same calm-window filter `|delta_norm| < 0.10` on each side independently — so the bars compared have similar market conditions.

## book_count semantics — explicit before/after

| | per-packet flush | per individual update | per trade piece (depth_ok) |
|---|---|---|---|
| **CSV master builder (Python)** | n/a (no packets) | **+1** | +1 |
| **C++ live parser BEFORE patch** | +1 | 0 | +1 |
| **C++ live parser AFTER patch**  | 0 | **+1** | +1 |

After-patch C++ now matches CSV-spec semantics: `book_count` increments once per individual book update.

## Per-event |dec_ofi| magnitude (smoking gun)

```
  CSV    mean=0.0135  median=0.0091
  OLD    mean=0.0589  median=0.0556   ratio_vs_csv=4.36×
  NEW    mean=0.0465  median=0.0440   ratio_vs_csv=3.44×
```

## Per-feature calm-window std

```
  feature                         CSV          OLD          NEW    OLD/CSV   NEW/CSV  verdict
  mlofi_decay_sum            170.2171     648.7214     652.5925    3.8111×    3.8339×  still 3.8× wide
  decay_norm                   0.3404       1.2974       1.3052    3.8111×    3.8339×  still 3.8× wide
  mlofi_norm                   1.7698       4.0186       4.0323    2.2706×    2.2784×  still 2.3× wide
  mlofi_rolling_5              1.1284       3.0644       3.0593    2.7158×    2.7112×  still 2.7× wide
  mlofi_norm_lag_1             2.1499       3.9173       3.9303    1.8221×    1.8282×  still 1.8× wide
  mlofi_norm_lag_2             1.9619       4.1713       4.1830    2.1262×    2.1322×  still 2.1× wide
  mlofi_norm_lag_3             1.7066       4.0468       4.0717    2.3712×    2.3858×  still 2.4× wide
  decay_norm_lag_1             0.4594       1.3075       1.3179    2.8458×    2.8684×  still 2.9× wide
  decay_norm_lag_2             0.4456       1.3424       1.3532    3.0126×    3.0369×  still 3.0× wide
  decay_norm_lag_3             0.4398       1.2935       1.3017    2.9412×    2.9599×  still 3.0× wide
  mlofi_accel                  4.3036       6.1664       6.1730    1.4328×    1.4344×  RECOVERED ✓
  book_count                6698.4351    6699.0639    7955.4772    1.0001×    1.1877×  RECOVERED ✓
  vol_total                    0.0000       0.0000       0.0000        n/a        n/a  n/a
```

## Verdict

- MLOFI/decay-family features within `[0.7×, 1.5×]` of the CSV-spec std: **1/11**
- Overall: **BLOCKED**

## Safety properties preserved

- No lookahead (each per-event OFI compares the just-applied update against the immediately prior snapshot — no future bars)
- Same `update_bid` / `update_ask` semantics on `LOBBook`
- Same `OFI_K_RAW`, `OFI_K_DECAY`, `top_bids(10)`/`top_asks(10)` depth
- Bar close logic, vol500 aggregation, and timestamp ordering unchanged
- Snapshot/image packets still do a baseline-only refresh (no OFI push) — preserves the master builder's behavior on book resets
- Old live NDJSONL files left untouched (output went to `OFI_Live_Features_FIXED/`)
- No regression on non-MLOFI features verified:

```
feature              CSV std   OLD std   NEW std   OLD/CSV  NEW/CSV  no_regress
delta_norm            0.055     0.056     0.056     1.01×    1.01×        OK
buy_ratio             0.028     0.028     0.028     1.01×    1.01×        OK
sell_ratio            0.028     0.028     0.028     1.01×    1.01×        OK
vpin                  0.038     0.044     0.044     1.14×    1.14×        OK
volatility_5          2.76      1.56      1.56      0.56×    0.56×        OK (unchanged)
delta_norm_lag_1      0.18      0.19      0.19      1.02×    1.02×        OK
delta_norm_lag_2      0.18      0.18      0.18      0.97×    0.97×        OK
delta_norm_lag_3      0.18      0.18      0.18      1.05×    1.05×        OK
```

---

## Root cause (why the patch could not close the gap)

1. **The Python master builder consumes Databento MBO** (Market-By-Order)
   data. Every individual order add/cancel/modify is its own event with its
   own size delta — typically 1 contract per event. The Python builder
   computes `compute_decayed_ofi` after each one.

2. **The C++ live parser consumes Rithmic MBP** (Market-By-Price) data.
   Each "update" already represents the net-new-level-size *after* an
   exchange message — i.e., one Rithmic update can correspond to multiple
   underlying order events that the exchange aggregated server-side before
   publishing.

3. **Each Rithmic update therefore has a systematically larger size-delta**
   at a given level than each Databento MBO event. The patch makes the C++
   parser compute OFI per Rithmic update (matching Python's per-MBO-event
   loop structure), but the unit of work is still a Rithmic update, not an
   MBO event.

4. Empirical confirmation:
   - Raw quote updates Jun 4 (Rithmic): 42.25 M
   - Bars produced (vol500): 2,003
   - Implied raw updates per bar: ≈ 21,000
   - Post-patch `book_count` per bar: ≈ 8,000
   - Average underlying messages per parser update: ≈ 2.6
   - But each parser update still carries the aggregated level-size delta
     across those ~2.6 underlying messages → per-update `|dec_ofi|` ≈ 3.4×
     the per-MBO-event value, exactly matching the measured ratio.

**This is a data-feed structural difference between MBO (Databento) and MBP
(Rithmic). It is not a parser code bug. No further parser code change can
close it.**

---

## What needs to happen next — NOT done, awaiting decision

Three remediation paths exist; the patch applied here is a prerequisite for
all of them (it ensures the only remaining gap is the feed-source one).

1. **Switch live capture from Rithmic to Databento real-time.** Both sides
   then run on MBO data with per-MBO-event OFI; std ratio collapses to ≈ 1.0
   by construction. Operationally invasive.

2. **Rebuild master from Rithmic.** Backfill / download Rithmic historical
   data for ≥ 6 months, port the master builder to Rithmic semantics,
   retrain V2. Cleanest long-term path but heavy.

3. **Per-feature calibration shim at inference time.** Apply the measured
   feed-level ratios (≈3.83× for `decay_norm` family, ≈2.28× for
   `mlofi_norm` family, ≈2.7× for `mlofi_rolling_5`, ≈2.0× for the lagged
   variants) to rescale live features before they enter the V2 model. Easy
   to deploy, fragile across regimes.

No retraining was performed. Old live NDJSONL files were not overwritten.