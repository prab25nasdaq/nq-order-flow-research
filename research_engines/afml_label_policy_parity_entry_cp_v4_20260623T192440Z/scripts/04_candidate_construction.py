"""
04_candidate_construction.py - Part D: candidate construction using the
rebuilt existing-model label-policy events (NOT dashboard CUSUM h=5.0, NOT
every bar), with v1-diagnostic-style clustering/dedup to prevent the
duplicate-overweighting failure found in the v1 build.

Scope decision (documented, not silent): candidates are restricted to
NQU6-native rebuilt events (contract_symbol=='NQU6') because the
dashboard-parity / OFI Level Decision / Book Flow feature panel (v4 Part C)
only exists for the live NQU6 contract - book-flow add/pull and the OFI
Level Decision table are NQU6-only artifacts in this codebase, never
computed historically for NQM6. This does not change the LABEL POLICY
(Part B already proved exact parity across the full NQM6+NQU6 universe) -
it only scopes which of those correctly-labeled events can be paired with
the dashboard-parity feature set this build is asked to test.

side_primary derivation (rule-based, from the existing model's OWN
reaction_type semantics in EVENT_POLICY.md/classify_bar_reaction - NOT
invented, NOT model-probability-based):
  rejection_from_above            -> LONG  (level held as support)
  rejection_from_below            -> SHORT (level held as resistance)
  breakout_acceptance_above_<lvl> -> LONG  (bullish continuation accepted)
  breakdown_acceptance_below_<lvl>-> SHORT (bearish continuation accepted)
  absorption / neutral_touch      -> NO_CANDIDATE (no directional signal)

Clustering/dedup (v1 diagnostic lesson - afml_trade_lifecycle_v1_failure_
diagnostic_20260623T065452Z found 5,365 raw labeled rows collapsed to an
effective N of 449.9, an 11.9x overweight, when overlapping label windows
were treated as independent): events are first grouped by
(day, side_primary, level_type, nearest_level_price) - i.e. the SAME
economic level/direction - since POC/VAH/VAL/HVN/LVN prices are fixed for
the whole day, this groups exactly the repeated-touch bursts the v1
diagnostic flagged, WITHOUT conflating genuinely distinct setups (a LONG at
one HVN and a SHORT at a different VAH on the same day are different
trades, not duplicates, even if their 40-bar windows happen to overlap in
time). WITHIN each (day, side, level_type, level_price) group, events whose
[bar_idx_in_day, bar_idx_in_day+40] windows overlap are further clustered
by time (v4_common.cluster_by_overlap) - a level touched again much later
in the day, after a gap exceeding the 40-bar horizon, is a genuinely new
opportunity and gets its own cluster. ONE representative event per cluster
is kept (earliest bar_idx_in_day in the cluster) - this becomes the
deduplicated candidate set used in Parts E/F/G onward.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v4_common as v4

cfg = v4.load_config()

SIDE_RULE = {
    "rejection_from_above": 1,
    "rejection_from_below": -1,
}


def derive_side(reaction_type: str) -> int:
    base = reaction_type.split("_", 1)[1] if reaction_type.split("_", 1)[0] in (
        "POC", "VAH", "VAL", "HVN", "LVN") else reaction_type
    if base in SIDE_RULE:
        return SIDE_RULE[base]
    if reaction_type.startswith("breakout_acceptance_above_"):
        return 1
    if reaction_type.startswith("breakdown_acceptance_below_"):
        return -1
    return 0


def main():
    v4.log("04: constructing candidates from rebuilt label-policy events (NQU6-scoped)...")
    ev = pd.read_parquet(v4.OUT_DIR / "rebuilt_label_policy_events.parquet")
    ev_nqu6 = ev[ev["contract_symbol"] == "NQU6"].reset_index(drop=True).copy()
    v4.log(f"  rebuilt NQU6-native events: {len(ev_nqu6)} (of {len(ev)} total rebuilt events)")

    panel = pd.read_parquet(v4.OUT_DIR / "v4_feature_panel.parquet")
    panel_ts = set(panel["bar_end_ts_ns"].astype(np.int64))
    ev_nqu6["has_feature_coverage"] = ev_nqu6["bar_end_ts_ns"].astype(np.int64).isin(panel_ts)
    n_no_coverage = int((~ev_nqu6["has_feature_coverage"]).sum())
    v4.log(f"  events with NO_FEATURE_COVERAGE (beyond v3 panel's snapshot window): {n_no_coverage}")

    ev_nqu6["side_primary"] = ev_nqu6["reaction_type"].map(derive_side)
    rxn_meta = v4.load_reaction_type_metadata()
    ev_nqu6["training_gate_status"] = ev_nqu6["reaction_type"].map(
        lambda r: rxn_meta.get(r, {}).get("training_gate_status", "UNKNOWN_REACTION_TYPE"))

    nqu6_master = v4.load_nqu6_master()
    day_map = nqu6_master.set_index("bar_end_ts_ns")["day"]
    ev_nqu6["day"] = ev_nqu6["bar_end_ts_ns"].map(day_map)
    bar_idx_map = nqu6_master.reset_index().set_index("bar_end_ts_ns")["index"]
    ev_nqu6["nqu6_bar_idx"] = ev_nqu6["bar_end_ts_ns"].map(bar_idx_map)

    ev_nqu6 = ev_nqu6.rename(columns={
        "level_price": "nearest_level_price", "distance_ticks_at_event": "distance_ticks",
    })
    raw_cols = ["event_id", "bar_end_ts_ns", "event_time_ns", "rithmic_date_str", "day",
                "bar_idx_in_day", "nqu6_bar_idx", "session", "side_primary", "reaction_type",
                "level_type", "level_source", "nearest_level_price", "distance_ticks",
                "training_gate_status", "has_feature_coverage", "touch_count_past_only",
                "bars_since_prior_touch"]
    raw_candidates = ev_nqu6[raw_cols].copy()
    raw_candidates.to_parquet(v4.OUT_DIR / "raw_candidates_v4.parquet", index=False)
    v4.log(f"  raw_candidates_v4: {len(raw_candidates)} rows")

    # ── clustering / dedup (v1 diagnostic lesson) ──────────────────────────
    directional = raw_candidates[raw_candidates["side_primary"] != 0].copy().reset_index(drop=True)
    directional["t0_idx"] = directional["bar_idx_in_day"].to_numpy()
    directional["t1_idx"] = directional["bar_idx_in_day"].to_numpy() + cfg["label_policy"]["label_horizon_bars"]
    horizon = cfg["label_policy"]["label_horizon_bars"]

    cluster_rows = []
    rep_event_ids = []
    next_cluster_id = 0
    directional["cluster_id"] = -1
    group_cols = ["day", "side_primary", "level_type", "nearest_level_price"]
    for (d, side, ltype, lpx), grp in directional.groupby(group_cols, sort=False):
        grp = grp.sort_values("bar_idx_in_day")
        sub_cluster = v4.cluster_by_overlap(
            np.zeros(len(grp), dtype=np.int64),  # already same-day/side/level - cluster purely by time overlap
            grp["t0_idx"].to_numpy(), grp["t1_idx"].to_numpy())
        for local_cid in np.unique(sub_cluster):
            mask = sub_cluster == local_cid
            sub = grp[mask]
            global_cid = next_cluster_id
            next_cluster_id += 1
            directional.loc[sub.index, "cluster_id"] = global_cid
            rep = sub.iloc[0]  # earliest bar_idx_in_day within this same-level/side/time-overlap cluster
            rep_event_ids.append(rep["event_id"])
            cluster_rows.append(dict(
                cluster_id=int(global_cid), day=int(d), side_primary=int(side),
                level_type=ltype, nearest_level_price=float(lpx),
                n_events_in_cluster=len(sub), dominant_reaction_type=sub["reaction_type"].mode().iloc[0],
                representative_event_id=int(rep["event_id"]),
                cluster_t0_bar_idx_in_day=int(sub["bar_idx_in_day"].min()),
                cluster_t1_bar_idx_in_day=int(sub["bar_idx_in_day"].max()) + horizon,
            ))
    cluster_report = pd.DataFrame(cluster_rows)
    cluster_report.to_csv(v4.OUT_DIR / "candidate_cluster_report_v4.csv", index=False)

    deduped = directional[directional["event_id"].isin(rep_event_ids)].copy()
    deduped = deduped.merge(
        cluster_report[["representative_event_id", "n_events_in_cluster"]],
        left_on="event_id", right_on="representative_event_id", how="left",
    ).drop(columns=["representative_event_id"])
    deduped.to_parquet(v4.OUT_DIR / "deduped_candidates_v4.parquet", index=False)
    v4.log(f"  deduped_candidates_v4: {len(deduped)} rows "
           f"(from {len(directional)} directional raw candidates, {len(cluster_report)} clusters)")

    # ── average uniqueness, both on raw directional pop and on the deduped pop ──
    n_bars_nqu6 = int(nqu6_master["bar_index"].max()) + 1
    c_t_raw = v4.num_co_events(directional["t0_idx"].to_numpy(), directional["t1_idx"].to_numpy(), n_bars_nqu6)
    directional["avg_uniqueness_raw_pop"] = v4.average_uniqueness(
        directional["t0_idx"].to_numpy(), directional["t1_idx"].to_numpy(), c_t_raw)

    deduped["t0_idx"] = deduped["bar_idx_in_day"].to_numpy()
    deduped["t1_idx"] = deduped["bar_idx_in_day"].to_numpy() + cfg["label_policy"]["label_horizon_bars"]
    c_t_dedup = v4.num_co_events(deduped["t0_idx"].to_numpy(), deduped["t1_idx"].to_numpy(), n_bars_nqu6)
    deduped["avg_uniqueness_dedup_pop"] = v4.average_uniqueness(
        deduped["t0_idx"].to_numpy(), deduped["t1_idx"].to_numpy(), c_t_dedup)

    uniq_rows = [
        dict(metric="n_raw_candidates_all_reaction_types", value=len(raw_candidates)),
        dict(metric="n_raw_candidates_directional_only", value=len(directional)),
        dict(metric="n_raw_no_feature_coverage", value=n_no_coverage),
        dict(metric="effective_N_raw_directional_(sum_avg_uniqueness)", value=float(directional["avg_uniqueness_raw_pop"].sum())),
        dict(metric="redundancy_multiple_raw_directional_(raw/effective)",
             value=float(len(directional) / max(directional["avg_uniqueness_raw_pop"].sum(), 1e-9))),
        dict(metric="n_clusters", value=len(cluster_report)),
        dict(metric="n_deduped_candidates", value=len(deduped)),
        dict(metric="effective_N_deduped_(sum_avg_uniqueness)", value=float(deduped["avg_uniqueness_dedup_pop"].sum())),
        dict(metric="redundancy_multiple_deduped_(deduped/effective)",
             value=float(len(deduped) / max(deduped["avg_uniqueness_dedup_pop"].sum(), 1e-9))),
        dict(metric="n_long_deduped", value=int((deduped["side_primary"] == 1).sum())),
        dict(metric="n_short_deduped", value=int((deduped["side_primary"] == -1).sum())),
        dict(metric="n_no_candidate_raw_(absorption_neutral_touch)",
             value=int((raw_candidates["side_primary"] == 0).sum())),
    ]
    uniq_df = pd.DataFrame(uniq_rows)
    uniq_df.to_csv(v4.OUT_DIR / "candidate_uniqueness_v4.csv", index=False)

    print(uniq_df.to_string(index=False))
    v4.log("04 complete.")
    return raw_candidates, deduped, cluster_report, uniq_df


if __name__ == "__main__":
    main()
