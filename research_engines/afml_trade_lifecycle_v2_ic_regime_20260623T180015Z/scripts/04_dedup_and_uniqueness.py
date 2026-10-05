"""
04_dedup_and_uniqueness.py - AFML Ch.4 fixes identified by the failure
diagnostic, applied as a two-layer pipeline (NOT vacuous - see rationale):

LAYER 1 - SAME-BAR DEDUPLICATION (exact administrative duplicates):
  The diagnostic proved that multiple candidate rows sharing the same
  (day, t0_idx) are the literal SAME model prediction (byte-identical
  side_primary / primary_confidence / triple-barrier outcome), just tagged
  with different reaction_type/level_type labels because several price
  levels were simultaneously in range. Collapsing to one representative row
  per (day, t0_idx) - deterministic tie-break: lowest event_id - removes
  this redundancy at the source (diagnostic found this alone takes 5,365
  labeled rows down to 700 unique bars).

LAYER 2 - AVERAGE UNIQUENESS SAMPLE WEIGHTS (AFML Ch.4 snippet 4.2):
  Same-bar dedup does NOT remove cross-bar overlap: a burst spanning 33
  bars (diagnostic's biggest cluster) still produces ~33 same-bar-deduped
  rows whose [t0,t1] triple-barrier windows continue to pairwise-overlap
  each other (new candidates keep arriving every ~1 bar while a typical
  position is still held). This layer computes continuous average-
  uniqueness weights on the ALREADY-DEDUPLICATED population's own overlap
  structure - this is verified NON-VACUOUS below (effective_n is
  meaningfully less than the deduplicated raw count, confirming residual
  overlap remains for this layer to do real work on).

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v2_common as v2


def main():
    v2.log("04: loading v1 candidate events + triple-barrier labels (read-only)...")
    events = v2.load_v1_candidate_events()
    labels = v2.load_v1_triple_barrier_labels().drop(columns=["t0_idx"])
    df = events.merge(labels, on="event_id", how="left")
    labeled = df[df["t1_idx"] >= 0].copy()
    v2.log(f"  raw candidate events: {len(df)}; triple-barrier-labeled (side!=0, resolved): {len(labeled)}")

    # ── LAYER 1: same-bar dedup ──────────────────────────────────────────
    rep_idx = (
        labeled.sort_values(["day", "t0_idx", "event_id"])
        .groupby(["day", "t0_idx"])["event_id"].first()
    )
    dedup = labeled[labeled["event_id"].isin(rep_idx.values)].copy().reset_index(drop=True)
    v2.log(f"  LAYER 1 (same-bar dedup): {len(labeled)} -> {len(dedup)} rows "
          f"({len(labeled) / len(dedup):.2f}x reduction)")

    # ── LAYER 2: average uniqueness on the deduplicated population's own
    # overlap structure ───────────────────────────────────────────────────
    nqu6 = v2.load_nqu6_master().sort_values("bar_index").reset_index(drop=True)
    n_bars = len(nqu6)
    t0 = dedup["t0_idx"].to_numpy()
    t1 = dedup["t1_idx"].to_numpy()
    day = dedup["day"].to_numpy()

    c_t = v2.num_co_events(t0, t1, n_bars)
    dedup["avg_uniqueness"] = v2.average_uniqueness(t0, t1, c_t)
    dedup["cluster_id"] = v2.cluster_by_overlap(day, t0, t1)
    cluster_sizes = dedup.groupby("cluster_id").size()
    dedup["cluster_size"] = dedup["cluster_id"].map(cluster_sizes)

    n_dedup = len(dedup)
    effective_n = float(dedup["avg_uniqueness"].sum())
    is_vacuous = np.isclose(effective_n, n_dedup, atol=1.0)
    v2.log(f"  LAYER 2 (average uniqueness on deduplicated set): raw_n={n_dedup} "
          f"effective_n={effective_n:.1f} ({100 * effective_n / n_dedup:.1f}% of raw) "
          f"-> {'VACUOUS (no residual overlap found - layer 2 has nothing to do) - UNEXPECTED' if is_vacuous else 'NON-VACUOUS (confirmed residual cross-bar overlap remains, layer 2 is doing real work)'}")
    assert not is_vacuous, "Layer 2 uniqueness weighting is vacuous - design assumption violated, stop and investigate"

    n_clusters = dedup["cluster_id"].nunique()
    n_singleton = (dedup["cluster_size"] == 1).sum()
    v2.log(f"  residual overlap clusters after same-bar dedup: {n_clusters} "
          f"({n_singleton} singleton / fully unique, {n_clusters - n_singleton} still have residual overlap)")

    dedup.to_parquet(v2.OUT_DIR / "_dedup_uniqueness_events.parquet", index=False)

    print(f"RAW_LABELED_N: {len(labeled)}")
    print(f"DEDUP_N: {n_dedup}")
    print(f"LAYER1_REDUCTION_RATIO: {len(labeled) / n_dedup:.2f}")
    print(f"EFFECTIVE_N_AFTER_LAYER2: {effective_n:.2f}")
    print(f"LAYER2_REDUCTION_RATIO: {n_dedup / effective_n:.2f}")
    print(f"TOTAL_REDUCTION_RATIO (raw_labeled / effective_n): {len(labeled) / effective_n:.2f}")
    print(f"N_RESIDUAL_CLUSTERS: {n_clusters}")
    print(f"N_SINGLETON_CLUSTERS: {n_singleton}")
    v2.log("04 complete.")
    return dedup


if __name__ == "__main__":
    main()
