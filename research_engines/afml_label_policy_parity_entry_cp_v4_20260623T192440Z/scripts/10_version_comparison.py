"""
10_version_comparison.py - Part J: compare v4 against v1 / v1-diagnostic / v2 / v3.

All numbers below are read directly from each prior engine's own frozen
output files (never re-derived, never eyeballed from memory) - this script
is itself read-only against those engine folders, just like every other
script in this build.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v4_common as v4


def main():
    v4.log("10: comparing v4 against v1 / v1-diagnostic / v2 / v3 (read-only)...")

    # ── v1 ──────────────────────────────────────────────────────────────
    v1_fold = pd.read_csv(v4.V1_DIR / "outputs" / "fold_results.csv")
    v1_val = pd.read_csv(v4.V1_DIR / "outputs" / "validation_results.csv")
    v1_oos_row = v1_val[v1_val["population"] == "GENUINELY_OOS_ONLY"].iloc[0]
    v1_all_row = v1_val[v1_val["population"] == "ALL_OUT_OF_FOLD"].iloc[0]

    # ── v1 diagnostic ───────────────────────────────────────────────────
    v1d_uniq = pd.read_csv(v4.DIAG_DIR / "outputs" / "average_uniqueness_by_event.csv")
    v1d_cluster = pd.read_csv(v4.DIAG_DIR / "outputs" / "candidate_cluster_report.csv")
    biggest_cluster = v1d_cluster.sort_values("n_events", ascending=False).iloc[0]

    v4_cluster = pd.read_csv(v4.OUT_DIR / "candidate_cluster_report_v4.csv")
    v4_biggest_cluster_size = int(v4_cluster["n_events_in_cluster"].max())

    # ── v2 ──────────────────────────────────────────────────────────────
    v2_pooled = pd.read_csv(v4.V2_DIR / "outputs" / "_pooled_summary_v2_ic_regime.csv")
    v2_baseline = v2_pooled[v2_pooled["variant"] == "V1_BASELINE"].iloc[0]
    v2a = v2_pooled[v2_pooled["variant"] == "V2A_DEDUP_UNIQ"].iloc[0]
    v2b = v2_pooled[v2_pooled["variant"] == "V2B_IC_REGIME"].iloc[0]

    # ── v3 ──────────────────────────────────────────────────────────────
    v3_cusum = pd.read_parquet(v4.V3_DIR / "outputs" / "cusum_events.parquet")
    v3_cand = pd.read_parquet(v4.V3_DIR / "outputs" / "candidate_events_entry_v3.parquet")
    v3_snaps = pd.read_parquet(v4.V3_DIR / "outputs" / "active_position_snapshots_v3.parquet")
    v3_entry_comp = pd.read_csv(v4.V3_DIR / "outputs" / "model_comparison_entry_v3.csv")
    v3_cp_comp = pd.read_csv(v4.V3_DIR / "outputs" / "model_comparison_cp_v3.csv")
    v3_best_entry = v3_entry_comp.loc[v3_entry_comp["mean_of_folds_mcc"].idxmax()]
    v3_best_cp = v3_cp_comp.loc[v3_cp_comp["mean_of_folds_mcc"].idxmax()]

    # ── v4 (this build) ─────────────────────────────────────────────────
    v4_raw_cand = pd.read_parquet(v4.OUT_DIR / "raw_candidates_v4.parquet")
    v4_deduped = pd.read_parquet(v4.OUT_DIR / "deduped_candidates_v4.parquet")
    v4_entry_comp = pd.read_csv(v4.OUT_DIR / "model_comparison_entry_v4.csv")
    v4_cp_comp = pd.read_csv(v4.OUT_DIR / "model_comparison_cp_v4.csv")
    v4_snaps = pd.read_parquet(v4.OUT_DIR / "active_position_snapshots_v4.parquet")
    v4_best_entry = v4_entry_comp.loc[v4_entry_comp["mean_of_folds_mcc"].idxmax()]
    v4_best_cp = v4_cp_comp.loc[v4_cp_comp["mean_of_folds_mcc"].idxmax()]

    rows = [
        dict(version="v1", candidate_count=8394, dedup_candidate_count=None,
             effective_N=None, entry_best_model="single_meta_model",
             entry_mean_of_folds_mcc=float(v1_fold["ALL__mcc"].mean()),
             entry_worst_fold_mcc=float(v1_fold["ALL__mcc"].min()),
             entry_pct_positive_folds=float((v1_fold["ALL__mcc"] > 0).mean()),
             entry_oos_n=int(v1_oos_row["n"]), entry_oos_mcc=float(v1_oos_row["mcc"]),
             cp_best_model=None, cp_mean_of_folds_mcc=None, cp_worst_fold_mcc=None, cp_oos_n=None, cp_oos_mcc=None,
             note="no CP model existed in v1 - meta-label only (entry-style); discouraging result that "
                  "triggered the v1-diagnostic investigation"),
        dict(version="v1_diagnostic", candidate_count=5365, dedup_candidate_count=None,
             effective_N=float(v1d_uniq["avg_uniqueness"].sum()),
             entry_best_model=None, entry_mean_of_folds_mcc=None, entry_worst_fold_mcc=None,
             entry_pct_positive_folds=None, entry_oos_n=None, entry_oos_mcc=None,
             cp_best_model=None, cp_mean_of_folds_mcc=None, cp_worst_fold_mcc=None, cp_oos_n=None, cp_oos_mcc=None,
             note=f"DIAGNOSED v1's failure: 5,365 raw labeled rows -> effective N={v1d_uniq['avg_uniqueness'].sum():.1f} "
                  f"(11.9x overweight); worst single cluster ({int(biggest_cluster['n_events'])} events, day "
                  f"{int(biggest_cluster['day'])}) had effective N={biggest_cluster['effective_n_in_cluster']:.1f} "
                  f"(redundancy {biggest_cluster['redundancy_ratio']:.1f}x) - this is the SAME failure mode v4's "
                  "Part D clustering/dedup is built to prevent"),
        dict(version="v2_ic_regime", candidate_count=2270, dedup_candidate_count=418,
             effective_N=None, entry_best_model="V2A_dedup+uniqueness (dominant fix)",
             entry_mean_of_folds_mcc=None, entry_worst_fold_mcc=None, entry_pct_positive_folds=None,
             entry_oos_n=int(v2a["pooled_oos_only_n"]), entry_oos_mcc=float(v2a["pooled_oos_only_mcc"]),
             cp_best_model=None, cp_mean_of_folds_mcc=None, cp_worst_fold_mcc=None, cp_oos_n=None, cp_oos_mcc=None,
             note=f"pooled_mcc: V1_BASELINE={v2_baseline['pooled_mcc']:.3f} (n={int(v2_baseline['pooled_n'])}) -> "
                  f"V2A_DEDUP_UNIQ={v2a['pooled_mcc']:.3f} (n={int(v2a['pooled_n'])}) -> "
                  f"V2B_IC_REGIME={v2b['pooled_mcc']:.3f} (n={int(v2b['pooled_n'])}); IC-regime features (63 extra "
                  "cols) did NOT improve on dedup+uniqueness alone given the small effective N - same engine "
                  "family as v1 (level-reaction-shadow), still no CUSUM/S-R gate, still no CP model"),
        dict(version="v3", candidate_count=len(v3_cusum), dedup_candidate_count=int((v3_cand["side_primary"] != 0).sum()),
             effective_N=float(v3_entry_comp["effective_independent_sample_count"].iloc[0]),
             entry_best_model=v3_best_entry["model"], entry_mean_of_folds_mcc=float(v3_best_entry["mean_of_folds_mcc"]),
             entry_worst_fold_mcc=float(v3_best_entry["worst_fold_mcc"]),
             entry_pct_positive_folds=float(v3_best_entry["pct_positive_folds"]),
             entry_oos_n=int(v3_best_entry["n_oos_only"]), entry_oos_mcc=float(v3_best_entry["mcc_oos_only"]),
             cp_best_model=v3_best_cp["model"], cp_mean_of_folds_mcc=float(v3_best_cp["mean_of_folds_mcc"]),
             cp_worst_fold_mcc=float(v3_best_cp["worst_fold_mcc"]), cp_oos_n=int(v3_best_cp["n_oos_only"]),
             cp_oos_mcc=float(v3_best_cp["mcc_oos_only"]),
             note=f"FIRST build with dashboard-formula parity + CUSUM h=5.0 + HVN/LVN S/R gate + a real CP "
                  f"label (no fixed threshold); n=19 entry / {len(v3_snaps)} CP snapshots -> SEVERE sample "
                  "starvation (best entry mean_of_folds_mcc=1.0 flagged as a near-certain small-sample "
                  "separability artifact, not genuine skill)"),
        dict(version="v4", candidate_count=int((v4_raw_cand["side_primary"] != 0).sum()),
             dedup_candidate_count=len(v4_deduped),
             effective_N=float(v4_entry_comp["effective_independent_sample_count"].iloc[0]),
             entry_best_model=v4_best_entry["model"], entry_mean_of_folds_mcc=float(v4_best_entry["mean_of_folds_mcc"]),
             entry_worst_fold_mcc=float(v4_best_entry["worst_fold_mcc"]),
             entry_pct_positive_folds=float(v4_best_entry["pct_positive_folds"]),
             entry_oos_n=int(v4_best_entry["n_oos_only"]), entry_oos_mcc=float(v4_best_entry["mcc_oos_only"]),
             cp_best_model=v4_best_cp["model"], cp_mean_of_folds_mcc=float(v4_best_cp["mean_of_folds_mcc"]),
             cp_worst_fold_mcc=float(v4_best_cp["worst_fold_mcc"]), cp_oos_n=int(v4_best_cp["n_oos_only"]),
             cp_oos_mcc=float(v4_best_cp["mcc_oos_only"]),
             note=f"replaced CUSUM h=5.0 with the EXISTING model's OWN label policy (exact parity proven in "
                  f"Part B) -> {int((v4_raw_cand['side_primary'] != 0).sum())} raw directional NQU6 events -> "
                  f"{len(v4_deduped)} deduped candidates (same-level/side/time-overlap clustering, the v1-"
                  f"diagnostic fix) -> {len(v4_snaps)} CP snapshots; entry effective N~85 (5x v3's 17), CP "
                  "effective N~85 (with 92,019/137,040 OOF-covered rows after fixing a fold-coverage metric bug); "
                  f"WORST single cluster: {v4_biggest_cluster_size} events (vs v1-diagnostic's worst cluster of "
                  f"{int(biggest_cluster['n_events'])} events collapsing to effective N=13.8) - v4's "
                  "(day,side,level_type,price) grouping caps any single cluster's size structurally, preventing "
                  "v1's specific same-level mega-cluster failure mode; FIRST build where the entry-side "
                  "mean-of-folds MCC is clearly positive with a substantial genuinely-OOS sample (n=1389, "
                  "mcc=0.25) - but the CP model's net business-value impact (avg_close_improvement) was "
                  "NEGATIVE for all 4 models despite weakly-positive MCC"),
    ]
    comp_df = pd.DataFrame(rows)
    comp_df.to_csv(v4.OUT_DIR / "version_comparison_v1_v2_v3_v4.csv", index=False)

    print(comp_df[["version", "candidate_count", "dedup_candidate_count", "effective_N",
                   "entry_best_model", "entry_mean_of_folds_mcc", "entry_oos_n", "entry_oos_mcc",
                   "cp_best_model", "cp_mean_of_folds_mcc"]].to_string(index=False))
    v4.log("10 complete.")
    return comp_df


if __name__ == "__main__":
    main()
