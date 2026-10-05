"""
07_clean_oos_subset.py - clean_oos_subset_report.csv

Restricts to in_sample_contaminated==False (strictly after the active
release's 2026-06-21T01:53Z training cutoff - the only labeled events this
engine is entitled to call genuinely out-of-sample). Concurrency and average
uniqueness are RECOMPUTED from scratch within this subset alone (not
inherited from the full-population numbers in script 01/02) - redundancy is
a property of WHICH events you are pooling together, so the OOS-only
subset's own internal overlap structure must be measured on its own terms.

Answers directly: is there enough clean OOS data, once AFML uniqueness
de-duplication is accounted for, to trust v1's reported GENUINELY_OOS_ONLY
MCC=-0.1485 (n=472)?

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diagnostic_common as dc


def main():
    dc.log("07: building clean_oos_subset_report.csv...")
    core = pd.read_parquet(dc.OUT_DIR / "_core_event_diagnostics.parquet")
    meta_pred = dc.load_v1_meta_model_predictions()[["event_id", "p_meta_is_out_of_fold", "pred_meta"]]
    df = core.merge(meta_pred, on="event_id", how="left")

    labeled = df[df["t1_idx"] >= 0].copy()
    oos = labeled[~labeled["in_sample_contaminated"]].copy()
    dc.log(f"  labeled rows total={len(labeled)}; genuinely-OOS (post-cutoff) labeled rows={len(oos)}")

    nqu6 = dc.load_nqu6_master().sort_values("bar_index").reset_index(drop=True)
    n_bars = len(nqu6)

    # recompute concurrency/uniqueness WITHIN the OOS-only subset alone
    t0_oos = oos["t0_idx"].to_numpy()
    t1_oos = oos["t1_idx"].to_numpy()
    c_t_oos = dc.num_co_events(t0_oos, t1_oos, n_bars)
    oos["avg_uniqueness_within_oos"] = dc.average_uniqueness(t0_oos, t1_oos, c_t_oos)
    oos["cluster_id_within_oos"] = dc.cluster_by_overlap(oos["day"].to_numpy(), t0_oos, t1_oos)

    rows = []
    for day, g in oos.groupby("day"):
        n_clusters = g.loc[g["cluster_id_within_oos"] >= 0, "cluster_id_within_oos"].nunique()
        eff_n = float(g["avg_uniqueness_within_oos"].sum())
        n_with_oof = int(g["p_meta_is_out_of_fold"].fillna(False).sum())
        hit = g["label_primary"] == 1
        n_hit = int(hit.sum())
        lo, hi = dc.wilson_ci(n_hit, len(g))
        eff_lo, eff_hi = dc.wilson_ci(round(eff_n * hit.mean()), max(round(eff_n), 1))
        rows.append(dict(
            day=int(day), n_raw=len(g), n_unique_bars=int(g["t0_idx"].nunique()),
            n_unique_clusters=int(n_clusters), n_with_oof_meta_prediction=n_with_oof,
            n_long=int((g["side_primary"] == 1).sum()), n_short=int((g["side_primary"] == -1).sum()),
            effective_n=eff_n, raw_hit_rate=float(hit.mean()),
            raw_hit_rate_wilson_lo=lo, raw_hit_rate_wilson_hi=hi,
            effective_n_hit_rate_wilson_lo=eff_lo, effective_n_hit_rate_wilson_hi=eff_hi,
            mean_realized_points=float(g["realized_points"].mean()),
        ))
    by_day = pd.DataFrame(rows)

    # overall aggregate row
    n_clusters_all = oos.loc[oos["cluster_id_within_oos"] >= 0, "cluster_id_within_oos"].nunique()
    eff_n_all = float(oos["avg_uniqueness_within_oos"].sum())
    hit_all = oos["label_primary"] == 1
    lo_all, hi_all = dc.wilson_ci(int(hit_all.sum()), len(oos))
    eff_lo_all, eff_hi_all = dc.wilson_ci(round(eff_n_all * hit_all.mean()), max(round(eff_n_all), 1))
    overall = dict(
        day="ALL_DAYS_COMBINED", n_raw=len(oos), n_unique_bars=int(oos["t0_idx"].nunique()),
        n_unique_clusters=int(n_clusters_all), n_with_oof_meta_prediction=int(oos["p_meta_is_out_of_fold"].fillna(False).sum()),
        n_long=int((oos["side_primary"] == 1).sum()), n_short=int((oos["side_primary"] == -1).sum()),
        effective_n=eff_n_all, raw_hit_rate=float(hit_all.mean()),
        raw_hit_rate_wilson_lo=lo_all, raw_hit_rate_wilson_hi=hi_all,
        effective_n_hit_rate_wilson_lo=eff_lo_all, effective_n_hit_rate_wilson_hi=eff_hi_all,
        mean_realized_points=float(oos["realized_points"].mean()),
    )
    by_day = pd.concat([by_day, pd.DataFrame([overall])], ignore_index=True)
    by_day.to_csv(dc.OUT_DIR / "clean_oos_subset_report.csv", index=False)

    dc.log(f"  OOS-only: raw_n={len(oos)}  unique_bars={overall['n_unique_bars']}  "
          f"unique_clusters={n_clusters_all}  effective_n={eff_n_all:.1f} "
          f"({100*eff_n_all/len(oos):.1f}% of raw)")
    dc.log(f"  raw hit-rate={overall['raw_hit_rate']:.3f}  Wilson 95% CI (raw n)=[{lo_all:.3f},{hi_all:.3f}]")
    dc.log(f"  same hit-rate, Wilson 95% CI using EFFECTIVE n instead = [{eff_lo_all:.3f},{eff_hi_all:.3f}] "
          f"({'excludes 0.5 - some evidence of edge' if eff_lo_all > 0.5 or eff_hi_all < 0.5 else 'INCLUDES 0.5 - cannot reject no-edge'})")
    dc.log(f"  v1's own reported GENUINELY_OOS_ONLY validation: MCC=-0.1485 on n=472 "
          f"(out-of-fold meta-predictions only, a SUBSET of the {len(oos)} raw labeled OOS events "
          f"-  this diagnostic's effective_n={eff_n_all:.1f} is smaller still)")

    print(f"OOS_RAW_N: {len(oos)}")
    print(f"OOS_UNIQUE_CLUSTERS: {n_clusters_all}")
    print(f"OOS_EFFECTIVE_N: {eff_n_all:.2f}")
    print(f"OOS_EFFECTIVE_N_PCT_OF_RAW: {100*eff_n_all/len(oos):.2f}")
    print(f"OOS_RAW_HIT_RATE: {overall['raw_hit_rate']:.4f}")
    print(f"OOS_RAW_HIT_RATE_WILSON_CI: [{lo_all:.4f}, {hi_all:.4f}]")
    print(f"OOS_EFFECTIVE_N_HIT_RATE_WILSON_CI: [{eff_lo_all:.4f}, {eff_hi_all:.4f}]")
    dc.log("07 complete.")
    return by_day


if __name__ == "__main__":
    main()
