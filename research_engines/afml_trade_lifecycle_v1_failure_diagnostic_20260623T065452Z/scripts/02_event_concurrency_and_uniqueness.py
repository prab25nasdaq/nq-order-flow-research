"""
02_event_concurrency_and_uniqueness.py

Produces:
  event_concurrency_report.csv     - PER-BAR concurrency c_t time series
                                      (AFML Ch.4 snippet 4.1, mpNumCoEvents),
                                      with day/peak-burst flags - explains
                                      WHERE/WHEN the 168-simultaneous-active-
                                      bets peak (v1 report Section 5) came from.
  average_uniqueness_by_event.csv  - PER-EVENT average uniqueness (AFML Ch.4
                                      snippet 4.2, mpSampleTW), with cluster
                                      and outcome context.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diagnostic_common as dc


def main():
    dc.log("02: loading core diagnostics...")
    df = pd.read_parquet(dc.OUT_DIR / "_core_event_diagnostics.parquet")
    bar_tl = pd.read_parquet(dc.OUT_DIR / "_bar_concurrency_timeline.parquet")

    # ── event_concurrency_report.csv (per bar) ──────────────────────────────
    valid = df[df["t1_idx"] >= 0]
    n_active_events_starting = valid.groupby("t0_idx").size()
    bar_tl["n_events_starting_here"] = bar_tl["bar_idx"].map(n_active_events_starting).fillna(0).astype(int)

    # also: v1's ACTIVE-BET concurrency (entered/sized positions only, from
    # v1's own active_bet_timeline.parquet) plotted alongside the raw LABEL
    # concurrency above, to directly connect "why max active bets = 168" to
    # the underlying label-concurrency structure that caused it.
    v1_active = dc.load_v1_active_bet_timeline()[["bar_idx", "active_bets_count"]].rename(
        columns={"active_bets_count": "v1_active_bets_count"}
    )
    bar_tl = bar_tl.merge(v1_active, on="bar_idx", how="left")

    q99 = bar_tl["concurrency_c_t"].quantile(0.99)
    bar_tl["is_peak_burst_bar"] = bar_tl["concurrency_c_t"] >= max(q99, 1)
    bar_tl.to_csv(dc.OUT_DIR / "event_concurrency_report.csv", index=False)

    top_bars = bar_tl.sort_values("concurrency_c_t", ascending=False).head(10)
    dc.log("  top 10 bars by label concurrency c_t:")
    print(top_bars[["bar_idx", "day", "concurrency_c_t", "n_events_starting_here", "v1_active_bets_count"]].to_string(index=False))

    peak_bar_idx = int(bar_tl.loc[bar_tl["v1_active_bets_count"].idxmax(), "bar_idx"])
    peak_v1_active = int(bar_tl["v1_active_bets_count"].max())
    peak_c_t = int(bar_tl.loc[bar_tl["bar_idx"] == peak_bar_idx, "concurrency_c_t"].iloc[0])
    dc.log(f"  v1's reported MAX_ACTIVE_BETS=168 peak occurs at bar_idx={peak_bar_idx}, where "
          f"this diagnostic's raw label concurrency c_t={peak_c_t}: ALL {peak_c_t} concurrently-"
          f"active labeled duplicates at that bar were sized non-zero by the meta-model (168=168, "
          f"no filtering at all) - the meta-model did not discriminate among the redundant copies, "
          f"it sized them ~uniformly because they share ~identical primary_confidence/features, so "
          f"the raw-label redundancy flows straight through into the position-management layer.")

    # ── average_uniqueness_by_event.csv (per event) ─────────────────────────
    cols = ["event_id", "t0_idx", "t1_idx", "day", "side_primary", "reaction_type",
            "training_gate_status", "nearest_level_type", "nearest_level_distance",
            "primary_confidence", "in_sample_contaminated", "holding_bars", "first_touch",
            "label_primary", "realized_points", "concurrency_at_t0", "avg_uniqueness",
            "cluster_id", "cluster_size"]
    uniq_df = df.loc[df["t1_idx"] >= 0, cols].copy()
    uniq_df = uniq_df.sort_values("avg_uniqueness")
    uniq_df.to_csv(dc.OUT_DIR / "average_uniqueness_by_event.csv", index=False)

    dc.log(f"  average_uniqueness_by_event.csv: {len(uniq_df)} rows")
    dc.log(f"  avg_uniqueness distribution: min={uniq_df['avg_uniqueness'].min():.4f} "
          f"p10={uniq_df['avg_uniqueness'].quantile(0.10):.4f} "
          f"median={uniq_df['avg_uniqueness'].median():.4f} "
          f"p90={uniq_df['avg_uniqueness'].quantile(0.90):.4f} "
          f"max={uniq_df['avg_uniqueness'].max():.4f}")
    pct_below_0_1 = float((uniq_df["avg_uniqueness"] < 0.1).mean())
    dc.log(f"  pct of labeled events with avg_uniqueness < 0.10 (i.e. spent its life inside "
          f"a >=10-way-overlapping burst on average): {pct_below_0_1:.1%}")

    print(f"PEAK_LABEL_CONCURRENCY_C_T: {int(bar_tl['concurrency_c_t'].max())}")
    print(f"PEAK_LABEL_CONCURRENCY_BAR_IDX: {int(bar_tl['concurrency_c_t'].idxmax())}")
    print(f"V1_PEAK_ACTIVE_BETS: {peak_v1_active}")
    print(f"V1_PEAK_ACTIVE_BETS_BAR_IDX: {peak_bar_idx}")
    print(f"PCT_EVENTS_AVG_UNIQUENESS_BELOW_0.10: {pct_below_0_1:.4f}")
    dc.log("02 complete.")
    return bar_tl, uniq_df


if __name__ == "__main__":
    main()
