"""
03_sample_weights.py - AFML Chapter 4 sample weight report.

Produces sample_weight_report.csv: per labeled event, the three AFML Ch.4
weight components (average uniqueness, return-attribution, time-decay) and
their combination, joined with whether the event was eligible for v1's
meta-label training and what y_meta/p_meta it got - so the weight a row
WOULD have received can be compared directly against how v1 actually
treated it (uniform weight = 1.0 for every row, with no Ch.4 adjustment
anywhere in v1's pipeline).

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diagnostic_common as dc


def main():
    dc.log("03: building sample_weight_report.csv...")
    core = pd.read_parquet(dc.OUT_DIR / "_core_event_diagnostics.parquet")
    meta_ds = dc.load_v1_meta_label_dataset()[["event_id", "eligible_for_meta_training", "y_meta"]]
    meta_pred = dc.load_v1_meta_model_predictions()[["event_id", "p_meta", "p_meta_is_out_of_fold", "pred_meta"]]

    df = core.merge(meta_ds, on="event_id", how="left").merge(meta_pred, on="event_id", how="left")
    labeled = df[df["t1_idx"] >= 0].copy()
    labeled["v1_actual_weight_used"] = 1.0  # v1 never called sample_weight=... anywhere

    out_cols = [
        "event_id", "t0_idx", "t1_idx", "day", "side_primary", "reaction_type",
        "nearest_level_type", "cluster_id", "cluster_size", "concurrency_at_t0",
        "avg_uniqueness", "return_attribution_weight_raw", "time_decay_weight",
        "combined_sample_weight", "v1_actual_weight_used", "eligible_for_meta_training",
        "y_meta", "p_meta", "p_meta_is_out_of_fold", "pred_meta", "in_sample_contaminated",
    ]
    out = labeled[out_cols].sort_values("avg_uniqueness")
    out.to_csv(dc.OUT_DIR / "sample_weight_report.csv", index=False)

    eligible = out[out["eligible_for_meta_training"] == True]
    dc.log(f"  {len(out)} labeled rows; {len(eligible)} were eligible_for_meta_training in v1")
    dc.log(f"  v1 ACTUAL total training weight (uniform): {len(eligible):.1f}")
    dc.log(f"  AFML uniqueness-only effective weight:      {eligible['avg_uniqueness'].sum():.1f}")
    dc.log(f"  AFML combined-weight effective total:        {eligible['combined_sample_weight'].sum():.1f}")
    overweight_ratio = len(eligible) / max(eligible["avg_uniqueness"].sum(), 1e-9)
    dc.log(f"  v1 over-counted its effective training sample size by {overweight_ratio:.1f}x "
          f"relative to AFML average-uniqueness weighting")

    print(f"SAMPLE_WEIGHT_ROWS: {len(out)}")
    print(f"ELIGIBLE_ROWS_V1: {len(eligible)}")
    print(f"V1_UNIFORM_WEIGHT_TOTAL: {len(eligible):.2f}")
    print(f"AFML_UNIQUENESS_WEIGHT_TOTAL: {eligible['avg_uniqueness'].sum():.2f}")
    print(f"OVERWEIGHT_RATIO: {overweight_ratio:.2f}")
    dc.log("03 complete.")
    return out


if __name__ == "__main__":
    main()
