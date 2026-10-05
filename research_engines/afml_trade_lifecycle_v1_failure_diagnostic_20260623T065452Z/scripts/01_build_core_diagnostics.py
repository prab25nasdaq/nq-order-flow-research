"""
01_build_core_diagnostics.py - builds the master per-event AFML Ch.4
diagnostic table (concurrency, average uniqueness, return-attribution
weight, time-decay weight, combined sample weight, overlap-cluster id) once,
shared by every downstream report script in this diagnostic.

READ-ONLY: reads only v1's frozen outputs/raw_snapshot. Writes only to this
diagnostic's own outputs/. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diagnostic_common as dc


def main():
    dc.log("01: loading v1 candidate events + triple-barrier labels + master price path...")
    events = dc.load_v1_candidate_events()
    labels = dc.load_v1_triple_barrier_labels().drop(columns=["t0_idx"])
    df = events.merge(labels, on="event_id", how="left")

    nqu6 = dc.load_nqu6_master().sort_values("bar_index").reset_index(drop=True)
    n_bars = len(nqu6)
    px_close = nqu6["px_close"].to_numpy()
    log_ret = np.full(n_bars, np.nan)
    log_ret[1:] = np.log(px_close[1:] / px_close[:-1])

    t0 = df["t0_idx"].to_numpy()
    t1 = df["t1_idx"].to_numpy()
    day = df["day"].to_numpy()

    dc.log(f"  {len(df)} candidate events total, {(t1 >= 0).sum()} triple-barrier-labeled (side!=0, resolved)")

    c_t = dc.num_co_events(t0, t1, n_bars)
    dc.log(f"  label concurrency c_t: max={c_t.max()} at bar_idx={int(c_t.argmax())}, "
          f"mean(nonzero)={c_t[c_t > 0].mean():.2f}")

    df["avg_uniqueness"] = dc.average_uniqueness(t0, t1, c_t)
    df["return_attribution_weight_raw"] = dc.return_attribution_weight(t0, t1, c_t, log_ret)
    df["time_decay_weight"] = dc.time_decay_weights(t1, df["avg_uniqueness"].to_numpy(), oldest_weight=0.5)
    df["combined_sample_weight"] = dc.combined_sample_weight(
        df["avg_uniqueness"].to_numpy(), df["return_attribution_weight_raw"].to_numpy(),
        df["time_decay_weight"].to_numpy(),
    )
    df["cluster_id"] = dc.cluster_by_overlap(day, t0, t1)
    df["concurrency_at_t0"] = np.where(t0 >= 0, c_t[np.clip(t0, 0, n_bars - 1)], np.nan)

    cluster_sizes = df.loc[df["cluster_id"] >= 0].groupby("cluster_id").size()
    df["cluster_size"] = df["cluster_id"].map(cluster_sizes).fillna(0).astype(int)

    valid = df["t1_idx"] >= 0
    n_valid = int(valid.sum())
    effective_n = float(df.loc[valid, "avg_uniqueness"].sum())
    n_unique_bars = int(df.loc[valid, "t0_idx"].nunique())
    n_clusters = int(df.loc[df["cluster_id"] >= 0, "cluster_id"].nunique())

    dc.log(f"  raw labeled N={n_valid}  unique t0 bars={n_unique_bars}  "
          f"unique overlap-clusters={n_clusters}  effective N (sum avg_uniqueness)={effective_n:.1f} "
          f"({100 * effective_n / n_valid:.1f}% of raw N)")

    df.to_parquet(dc.OUT_DIR / "_core_event_diagnostics.parquet", index=False)

    bar_timeline = pd.DataFrame({
        "bar_idx": np.arange(n_bars), "day": nqu6["day"].to_numpy(),
        "bar_end_ts_ns": nqu6["bar_end_ts_ns"].to_numpy(), "concurrency_c_t": c_t,
    })
    bar_timeline.to_parquet(dc.OUT_DIR / "_bar_concurrency_timeline.parquet", index=False)

    print(f"RAW_LABELED_N: {n_valid}")
    print(f"UNIQUE_T0_BARS: {n_unique_bars}")
    print(f"UNIQUE_OVERLAP_CLUSTERS: {n_clusters}")
    print(f"EFFECTIVE_N_SUM_AVG_UNIQUENESS: {effective_n:.2f}")
    print(f"EFFECTIVE_N_PCT_OF_RAW: {100 * effective_n / n_valid:.2f}")
    print(f"MAX_CONCURRENCY_C_T: {int(c_t.max())}")
    print(f"MAX_CONCURRENCY_BAR_IDX: {int(c_t.argmax())}")
    dc.log("01 complete.")
    return df, bar_timeline


if __name__ == "__main__":
    main()
