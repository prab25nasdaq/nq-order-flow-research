"""
05_cancel_reason_breakdown.py - cancel_reason_breakdown.csv

Joins v1's per-event lifecycle final_state (CANDIDATE -> ... -> CLOSED, see
v1 script 07) with this diagnostic's concurrency/cluster context, broken
down by reaction_type, nearest_level_type, cluster-size bucket, and
in_sample_contaminated - to identify which cancel reason dominates and
whether cancellation correlates with redundancy (i.e. does the meta-model
mostly PASS on the giant, low-information bursts, or does it size them
non-zero just as often as genuinely unique events?).

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diagnostic_common as dc


def cluster_size_bucket(n):
    if n == 0:
        return "0_no_side_or_unlabeled"
    elif n == 1:
        return "1_singleton"
    elif n <= 5:
        return "2-5"
    elif n <= 20:
        return "6-20"
    elif n <= 100:
        return "21-100"
    else:
        return "100+"


def main():
    dc.log("05: building cancel_reason_breakdown.csv...")
    core = pd.read_parquet(dc.OUT_DIR / "_core_event_diagnostics.parquet")
    lifecycle = dc.load_v1_lifecycle()[["event_id", "final_state", "exit_reason", "entered_shadow_position"]]
    df = core.merge(lifecycle, on="event_id", how="left")
    df["cluster_size_bucket"] = df["cluster_size"].apply(cluster_size_bucket)

    overall = df["final_state"].value_counts()
    dc.log(f"  overall final_state distribution:\n{overall.to_string()}")
    dominant = overall.idxmax()
    dc.log(f"  DOMINANT cancel/final reason overall: {dominant} ({overall.max()} / {len(df)} = {overall.max()/len(df):.1%})")

    rows = []
    for (state, rxn, lvl, bucket), g in df.groupby(
        ["final_state", "reaction_type", "nearest_level_type", "cluster_size_bucket"], dropna=False
    ):
        rows.append(dict(
            final_state=state, reaction_type=rxn, nearest_level_type=lvl,
            cluster_size_bucket=bucket, n=len(g),
            pct_in_sample_contaminated=float(g["in_sample_contaminated"].mean()),
            mean_avg_uniqueness=float(g["avg_uniqueness"].mean()) if g["avg_uniqueness"].notna().any() else np.nan,
            mean_primary_confidence=float(g["primary_confidence"].mean()),
        ))
    breakdown = pd.DataFrame(rows).sort_values("n", ascending=False)
    breakdown.to_csv(dc.OUT_DIR / "cancel_reason_breakdown.csv", index=False)

    # cancel rate by cluster-size bucket - the key redundancy-vs-cancellation check
    cancel_states = {"CANCEL_STALE", "CANCEL_META_PASS", "CANCEL_VERTICAL_EXPIRED"}
    df["is_cancel"] = df["final_state"].isin(cancel_states)
    df["is_entered"] = df["entered_shadow_position"].fillna(False)
    by_bucket = df.groupby("cluster_size_bucket").agg(
        n=("event_id", "size"), pct_cancel=("is_cancel", "mean"), pct_entered=("is_entered", "mean"),
        mean_avg_uniqueness=("avg_uniqueness", "mean"),
    ).reset_index()
    dc.log("  cancel/entry rate by cluster-size bucket (does the meta-model thin out big bursts?):")
    print(by_bucket.to_string(index=False))

    print(f"DOMINANT_FINAL_STATE: {dominant}")
    print(f"DOMINANT_FINAL_STATE_PCT: {overall.max()/len(df):.4f}")
    for _, r in by_bucket.iterrows():
        print(f"PCT_ENTERED__{r['cluster_size_bucket']}: {r['pct_entered']:.4f} (n={int(r['n'])})")
    dc.log("05 complete.")
    return breakdown, by_bucket


if __name__ == "__main__":
    main()
