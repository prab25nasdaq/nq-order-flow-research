"""
04_candidate_clusters.py - candidate_cluster_report.csv

One row per overlap-cluster (connected component of mutually-overlapping
[t0,t1] label windows within a day - see diagnostic_common.cluster_by_overlap).
Characterizes how redundant each burst is: how many raw events vs how many
unique bars vs how many AFML-effective (uniqueness-weighted) samples it
really contains, and whether the events inside it agree with each other on
side / reaction_type / level_type / outcome (high agreement + low effective-N
= a single underlying signal copy-pasted across many simultaneous level-type
labels, not independent corroborating evidence).

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diagnostic_common as dc


def mode_and_pct(s: pd.Series):
    vc = s.value_counts()
    if vc.empty:
        return None, np.nan
    return vc.index[0], float(vc.iloc[0] / vc.sum())


def main():
    dc.log("04: building candidate_cluster_report.csv...")
    core = pd.read_parquet(dc.OUT_DIR / "_core_event_diagnostics.parquet")
    labeled = core[core["cluster_id"] >= 0].copy()

    rows = []
    for cid, g in labeled.groupby("cluster_id"):
        side_mode, side_pct = mode_and_pct(g["side_primary"])
        rxn_mode, rxn_pct = mode_and_pct(g["reaction_type"])
        lvl_mode, lvl_pct = mode_and_pct(g["nearest_level_type"])
        lbl_mode, lbl_pct = mode_and_pct(g["label_primary"])
        eff_n = float(g["avg_uniqueness"].sum())
        rows.append(dict(
            cluster_id=cid, day=int(g["day"].iloc[0]), n_events=len(g),
            n_unique_bars=int(g["t0_idx"].nunique()),
            t0_min=int(g["t0_idx"].min()), t1_max=int(g["t1_idx"].max()),
            span_bars=int(g["t1_idx"].max() - g["t0_idx"].min()),
            dominant_side=side_mode, pct_dominant_side=side_pct,
            n_unique_reaction_types=int(g["reaction_type"].nunique()),
            dominant_reaction_type=rxn_mode, pct_dominant_reaction_type=rxn_pct,
            n_unique_level_types=int(g["nearest_level_type"].nunique()),
            dominant_level_type=lvl_mode, pct_dominant_level_type=lvl_pct,
            label_primary_mode=lbl_mode, pct_label_agreement=lbl_pct,
            mean_primary_confidence=float(g["primary_confidence"].mean()),
            std_primary_confidence=float(g["primary_confidence"].std()),
            effective_n_in_cluster=eff_n,
            redundancy_ratio=float(len(g) / max(eff_n, 1e-9)),
            pct_in_sample_contaminated=float(g["in_sample_contaminated"].mean()),
        ))
    cluster_df = pd.DataFrame(rows).sort_values("n_events", ascending=False)
    cluster_df.to_csv(dc.OUT_DIR / "candidate_cluster_report.csv", index=False)

    dc.log(f"  {len(cluster_df)} clusters total")
    dc.log(f"  singleton clusters (n_events==1, genuinely unique): "
          f"{(cluster_df['n_events'] == 1).sum()} ({(cluster_df['n_events'] == 1).mean():.1%})")
    dc.log(f"  clusters with n_events>=10: {(cluster_df['n_events'] >= 10).sum()}, "
          f"holding {cluster_df.loc[cluster_df['n_events'] >= 10, 'n_events'].sum()} / "
          f"{cluster_df['n_events'].sum()} of all labeled events "
          f"({cluster_df.loc[cluster_df['n_events'] >= 10, 'n_events'].sum() / cluster_df['n_events'].sum():.1%})")
    dc.log(f"  median redundancy_ratio (n_events / effective_n) among clusters with n_events>=5: "
          f"{cluster_df.loc[cluster_df['n_events'] >= 5, 'redundancy_ratio'].median():.2f}")
    dc.log(f"  median pct_dominant_side: {cluster_df['pct_dominant_side'].median():.3f}  "
          f"median pct_label_agreement: {cluster_df['pct_label_agreement'].median():.3f}")

    biggest = cluster_df.iloc[0]
    dc.log(f"  biggest cluster: id={biggest['cluster_id']} day={biggest['day']} "
          f"n_events={biggest['n_events']} span_bars={biggest['span_bars']} "
          f"dominant_reaction_type={biggest['dominant_reaction_type']} ({biggest['pct_dominant_reaction_type']:.1%}) "
          f"effective_n={biggest['effective_n_in_cluster']:.2f}")

    print(f"N_CLUSTERS: {len(cluster_df)}")
    print(f"N_SINGLETON_CLUSTERS: {(cluster_df['n_events'] == 1).sum()}")
    print(f"PCT_EVENTS_IN_CLUSTERS_GE10: {cluster_df.loc[cluster_df['n_events'] >= 10, 'n_events'].sum() / cluster_df['n_events'].sum():.4f}")
    print(f"BIGGEST_CLUSTER_N_EVENTS: {int(biggest['n_events'])}")
    print(f"BIGGEST_CLUSTER_EFFECTIVE_N: {biggest['effective_n_in_cluster']:.2f}")
    dc.log("04 complete.")
    return cluster_df


if __name__ == "__main__":
    main()
