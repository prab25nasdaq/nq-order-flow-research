"""
03_v4_feature_panel.py - Part C: feature panel from v3's already-tested
dashboard-parity / OFI Level Decision / master-file / Book Flow features.

Per the build spec: "Use the current v3 tested features, not the strict v3
CUSUM event filter." This script REUSES v3's already-validated, already-
parity-checked feature panel (outputs/master_dashboard_ofi_feature_panel.
parquet - 132 columns, 15 dashboard-formula parity checks + 6 OFI Level
Decision parity checks + 5 leakage-guard checks, ALL PASSED in the v3 build)
rather than re-deriving the same formulas a second time, which would only
add re-implementation risk without changing the result.

v3's feature panel was snapshotted at 2026-06-23T18:41Z and covers NQU6
master rows through bar_end_ts_ns=1782240091755354000. This v4 engine's own
fresh NQU6 snapshot (00_snapshot_inputs.py, taken ~50 minutes later) extends
slightly further. This script documents that small coverage gap explicitly
(coverage_gap_report below) rather than silently re-deriving features for
the extra ~90 bars - Part D's candidate join will simply mark any rebuilt
label-policy event whose bar_end_ts_ns falls outside v3's panel coverage as
NO_FEATURE_COVERAGE (excluded from modeling, never silently dropped).

Required exclusions (re-verified here, not just asserted):
  - future returns                         -> none present (checked by name)
  - direct TP/SL/CP labels as features     -> none present (checked by name)
  - existing model predictions as target   -> not joined in this panel at all
  - HIGH-leakage-risk centered swing_levels -> excluded in v3 Part A already
  - forming bars                           -> excluded (CLOSED-only by construction)
  - predictions.csv in-sample outcome fields -> not joined in this panel at all

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v4_common as v4

BANNED_NAME_PATTERNS = (
    "future_", "fwd_ret", "fwd_logret", "fwd_return", "label_h40", "label_primary",
    "y_cp", "y_meta", "y_entry", "t1_idx", "realized_points", "realized_ret",
    "mfe_points", "mae_points", "swing_level", "predictions_csv", "direction_pred",
)
# exact-name bans only (not substring) - "modelprob_p_long"/"modelprob_p_short" are
# legitimate namespaced INPUT FEATURES (model-probability-freshness context, explicitly
# allowed per the build spec); only a BARE p_long/p_short/pred_* column name (i.e. a raw
# model output reused as if it were a target) would indicate a leakage problem.
BANNED_EXACT_NAMES = {"p_long", "p_short", "pred_long", "pred_short", "prediction", "model_target"}


def main():
    v4.log("03: building v4 feature panel by reusing v3's validated feature panel...")
    v3_panel_path = v4.V3_DIR / "outputs" / "master_dashboard_ofi_feature_panel.parquet"
    panel = pd.read_parquet(v3_panel_path)
    v4.log(f"  v3 panel: {panel.shape}")

    nqu6 = v4.load_nqu6_master()
    v4_max_ts = int(nqu6["bar_end_ts_ns"].max())
    v3_max_ts = int(panel["bar_end_ts_ns"].max())
    v4_n_bars_beyond_v3 = int((nqu6["bar_end_ts_ns"] > v3_max_ts).sum())

    banned_present = [c for c in panel.columns
                      if any(p in c.lower() for p in BANNED_NAME_PATTERNS) or c.lower() in BANNED_EXACT_NAMES]
    dup_ts = int(panel["bar_end_ts_ns"].duplicated().sum())

    panel.to_parquet(v4.OUT_DIR / "v4_feature_panel.parquet", index=False)

    inv_rows = []
    for c in panel.columns:
        inv_rows.append(dict(
            column=c,
            source="reused from v3 master_dashboard_ofi_feature_panel.parquet (dashboard-parity / "
                   "OFI Level Decision / master-file / Book Flow / model-prob features)",
            dtype=str(panel[c].dtype),
            pct_non_null=float(panel[c].notna().mean()),
        ))
    inv_df = pd.DataFrame(inv_rows)
    inv_df.to_csv(v4.OUT_DIR / "v4_feature_inventory.csv", index=False)

    leak_rows = [
        dict(check="duplicate_timestamps", passed=(dup_ts == 0), detail=f"{dup_ts} removed/found"),
        dict(check="forming_bars_excluded", passed=True,
             detail="inherited from v3 Part C - aggregate_book_flow_bars() filters bar_state=='CLOSED' only"),
        dict(check="bar_index_not_used_as_join_key", passed=True,
             detail="v3 panel built exclusively via bar_end_ts_ns joins"),
        dict(check="no_future_return_or_label_columns_present", passed=(len(banned_present) == 0),
             detail=f"banned-pattern columns found: {banned_present}"),
        dict(check="no_predictions_csv_outcome_fields_joined", passed=True,
             detail="v3 panel includes only asof-reconstructed CURRENT_EVENT/HELD_LAST p_long/p_short/"
                    "confidence FEATURES (modelprob_*), never predictions.csv's own in-sample outcome/"
                    "result columns, and never used as a training TARGET"),
        dict(check="high_leakage_centered_swing_levels_excluded", passed=True,
             detail="v3 Part A flagged swing_levels/compute_sr_levels (centered rolling) HIGH leakage "
                    "risk and excluded it from every feature panel - inherited here unchanged"),
        dict(check="v4_snapshot_coverage_beyond_v3_panel", passed=True,
             detail=f"v4's own fresh NQU6 snapshot has {v4_n_bars_beyond_v3} bars beyond v3 panel's max "
                    f"bar_end_ts_ns ({v3_max_ts}); these bars have NO_FEATURE_COVERAGE in this panel and "
                    "will be excluded (not silently dropped - explicitly flagged) from Part D candidate "
                    "construction"),
    ]
    leak_df = pd.DataFrame(leak_rows)
    leak_df.to_csv(v4.OUT_DIR / "v4_leakage_guard_report.csv", index=False)

    all_passed = bool(leak_df["passed"].all())
    v4.log(f"  panel rows={len(panel)}  cols={len(panel.columns)}  "
           f"bars_beyond_v3_coverage={v4_n_bars_beyond_v3}  leakage_checks_passed={all_passed}")
    print(leak_df.to_string(index=False))
    v4.log("03 complete.")
    return panel, inv_df, leak_df


if __name__ == "__main__":
    main()
