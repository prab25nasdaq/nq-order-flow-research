"""
09_shadow_lifecycle.py - Part I: shadow lifecycle simulation.

Combines the Entry ACT/PASS model (script 07) + CP CLOSE/HOLD model
(script 08) + the existing model's OWN exit policy (fixed +40-bar horizon,
day-bounded - NOT triple-barrier) into a single research ledger. NO
ORDERS, NO EXECUTION anywhere in this script.

IMPORTANT: the existing dashboard/level-reaction model's label policy has
NO PT/SL horizontal barriers at all (Part A/B) - only a fixed time horizon.
Therefore EXIT_POLICY_TP and EXIT_POLICY_SL can never fire in this v4
lifecycle; only EXIT_POLICY_TIME is reachable via the policy itself. Both
states are still defined in the schema (per the build spec) and reported
as having zero occurrences, rather than silently omitted - this is a
genuine, important difference from v3 (which inherited the dashboard's own
triple-barrier-shaped CUSUM/S/R framework) and is called out explicitly in
the final report.

States: NO_CANDIDATE -> CLOSED (side_primary==0, no opportunity)
        CANDIDATE -> DUPLICATE_SUPPRESSED_BY_CLUSTERING -> CLOSED (raw event
                     absorbed into another cluster's representative -
                     Part D's v1-diagnostic-driven dedup; never an
                     independent shadow decision)
        CANDIDATE -> NEUTRAL_DROPPED_NO_LABEL -> CLOSED (representative
                     event but label_h40 was NaN/neutral - excluded from
                     Part E, matching the existing model's own convention)
        CANDIDATE -> PASS_ENTRY -> CLOSED (entry model says PASS, or no
                     out-of-fold entry prediction available)
        CANDIDATE -> ACTIVE_SHADOW -> CLOSE_CP -> CLOSED (CP model says
                     CLOSE on some active bar strictly before the existing
                     policy's horizon resolves)
        CANDIDATE -> ACTIVE_SHADOW -> EXIT_POLICY_TIME -> CLOSED (held to
                     the existing model's own +40-bar horizon; no CP model
                     said CLOSE before then, or no CP prediction available)

The representative entry/CP model used to DRIVE this illustrative
simulation is the one with the best mean-of-folds MCC in
model_comparison_{entry,cp}_v4.csv - chosen for demonstration of the full
plumbing, consistent with the v3 build's own convention.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v4_common as v4

cfg = v4.load_config()


def pick_representative_model(comparison_csv_name: str) -> str:
    path = v4.OUT_DIR / comparison_csv_name
    df = pd.read_csv(path)
    if df.empty or "mean_of_folds_mcc" not in df.columns:
        return None
    df = df.dropna(subset=["mean_of_folds_mcc"])
    if df.empty:
        return None
    return df.loc[df["mean_of_folds_mcc"].idxmax(), "model"]


def main():
    v4.log("09: building shadow lifecycle ledger (entry ACT/PASS + CP CLOSE/HOLD + existing-policy horizon)...")
    raw = pd.read_parquet(v4.OUT_DIR / "raw_candidates_v4.parquet")
    deduped = pd.read_parquet(v4.OUT_DIR / "deduped_candidates_v4.parquet")
    entry_lbl = pd.read_parquet(v4.OUT_DIR / "entry_meta_label_dataset_v4.parquet")
    entry_pred = pd.read_parquet(v4.OUT_DIR / "entry_model_predictions_v4.parquet")
    cp_pred = pd.read_parquet(v4.OUT_DIR / "cp_model_predictions_v4.parquet")

    entry_model = pick_representative_model("model_comparison_entry_v4.csv")
    cp_model = pick_representative_model("model_comparison_cp_v4.csv")
    v4.log(f"  representative entry model: {entry_model}  |  representative CP model: {cp_model}")

    df = raw.copy()
    final_state = np.full(len(df), "", dtype=object)
    exit_reason = np.full(len(df), "", dtype=object)
    cp_close_bar = np.full(len(df), -1, dtype=np.int64)

    side0 = df["side_primary"] == 0
    final_state[side0.to_numpy()] = "NO_CANDIDATE"
    exit_reason[side0.to_numpy()] = "no_directional_reaction_rule_matched"

    directional_ids = set(df.loc[~side0, "event_id"])
    deduped_ids = set(deduped["event_id"])
    suppressed = (~side0) & (~df["event_id"].isin(deduped_ids))
    final_state[suppressed.to_numpy()] = "DUPLICATE_SUPPRESSED_BY_CLUSTERING"
    exit_reason[suppressed.to_numpy()] = "absorbed_into_another_clusters_representative_event_(Part_D_dedup)"

    is_rep = (~side0) & (df["event_id"].isin(deduped_ids))
    labeled_ids = set(entry_lbl["event_id"])
    no_label = is_rep & (~df["event_id"].isin(labeled_ids))
    final_state[no_label.to_numpy()] = "NEUTRAL_DROPPED_NO_LABEL"
    exit_reason[no_label.to_numpy()] = "label_h40_was_NaN_(neutral_outcome)_or_no_feature_coverage"

    entry_p = None
    if entry_model is not None and f"p_{entry_model}" in entry_pred.columns:
        entry_p = entry_pred.set_index("event_id")[f"p_{entry_model}"]

    has_label = is_rep & df["event_id"].isin(labeled_ids)
    cp_sub_by_event = {eid: g.sort_values("snapshot_bar_idx") for eid, g in cp_pred.groupby("event_id")} if len(cp_pred) else {}

    for idx in df.index[has_label]:
        eid = df.at[idx, "event_id"]
        p = entry_p.get(eid, np.nan) if entry_p is not None else np.nan
        if not np.isfinite(p):
            final_state[idx] = "PASS_ENTRY"
            exit_reason[idx] = "no_out_of_fold_entry_prediction_available"
            continue
        if p < 0.5:
            final_state[idx] = "PASS_ENTRY"
            exit_reason[idx] = f"entry_model_{entry_model}_p_act={p:.3f}_below_0.5"
            continue

        cp_sub = cp_sub_by_event.get(eid, pd.DataFrame())
        closed_early = False
        if cp_model is not None and len(cp_sub) and f"p_{cp_model}" in cp_sub.columns:
            for _, srow in cp_sub.iterrows():
                pc = srow[f"p_{cp_model}"]
                if np.isfinite(pc) and pc >= 0.5:
                    final_state[idx] = "CLOSE_CP"
                    exit_reason[idx] = f"cp_model_{cp_model}_p_close={pc:.3f}_at_bar={int(srow['snapshot_bar_idx'])}"
                    cp_close_bar[idx] = int(srow["snapshot_bar_idx"])
                    closed_early = True
                    break
        if not closed_early:
            final_state[idx] = "EXIT_POLICY_TIME"
            exit_reason[idx] = "rode_to_existing_model_fixed_horizon_(no_PT/SL_exists_in_this_label_policy)"

    df["final_state"] = final_state
    df["exit_reason"] = exit_reason
    df["cp_close_bar"] = cp_close_bar
    df["entry_model_used"] = entry_model
    df["cp_model_used"] = cp_model

    path_map = {
        "NO_CANDIDATE": "NO_CANDIDATE->CLOSED",
        "DUPLICATE_SUPPRESSED_BY_CLUSTERING": "CANDIDATE->DUPLICATE_SUPPRESSED_BY_CLUSTERING->CLOSED",
        "NEUTRAL_DROPPED_NO_LABEL": "CANDIDATE->NEUTRAL_DROPPED_NO_LABEL->CLOSED",
        "PASS_ENTRY": "CANDIDATE->PASS_ENTRY->CLOSED",
        "CLOSE_CP": "CANDIDATE->ACTIVE_SHADOW->CLOSE_CP->CLOSED",
        "EXIT_POLICY_TIME": "CANDIDATE->ACTIVE_SHADOW->EXIT_POLICY_TIME->CLOSED",
    }
    df["status_path"] = df["final_state"].map(path_map)

    df.to_parquet(v4.OUT_DIR / "shadow_lifecycle_v4.parquet", index=False)

    funnel = df["final_state"].value_counts()
    summary_rows = [{"metric": "entry_model_used", "value": entry_model},
                    {"metric": "cp_model_used", "value": cp_model}]
    for state in ["NO_CANDIDATE", "DUPLICATE_SUPPRESSED_BY_CLUSTERING", "NEUTRAL_DROPPED_NO_LABEL",
                  "PASS_ENTRY", "CLOSE_CP", "EXIT_POLICY_TIME", "EXIT_POLICY_TP", "EXIT_POLICY_SL"]:
        summary_rows.append({"metric": f"final_state__{state}", "value": int(funnel.get(state, 0))})
    n_active_shadow_total = int((df["final_state"].isin(["CLOSE_CP", "EXIT_POLICY_TIME"])).sum())
    n_closed_early = int((df["final_state"] == "CLOSE_CP").sum())
    summary_rows.append({"metric": "n_entered_active_shadow", "value": n_active_shadow_total})
    summary_rows.append({"metric": "n_closed_early_by_cp", "value": n_closed_early})
    summary_rows.append({"metric": "pct_closed_early_by_cp", "value": float(n_closed_early / max(n_active_shadow_total, 1))})
    summary_rows.append({"metric": "note_PT_SL_never_reachable", "value":
                          "existing model label policy is fixed-horizon only (no triple-barrier) - "
                          "EXIT_POLICY_TP/EXIT_POLICY_SL are schema-complete but structurally unreachable"})

    if n_closed_early:
        cp_rows = df[df["final_state"] == "CLOSE_CP"]
        cp_snap_match = cp_pred.set_index(["event_id", "snapshot_bar_idx"])
        deltas = []
        for _, r in cp_rows.iterrows():
            key = (r["event_id"], r["cp_close_bar"])
            if key in cp_snap_match.index:
                deltas.append(-cp_snap_match.loc[key, "remaining_value_to_exit"])
        if deltas:
            summary_rows.append({"metric": "mean_value_saved_by_early_cp_close", "value": float(np.mean(deltas))})
            summary_rows.append({"metric": "median_value_saved_by_early_cp_close", "value": float(np.median(deltas))})
    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(v4.OUT_DIR / "shadow_lifecycle_summary_v4.csv", index=False)

    v4.log(f"  lifecycle funnel: {funnel.to_dict()}")
    print(f"ENTRY_MODEL_USED: {entry_model}")
    print(f"CP_MODEL_USED: {cp_model}")
    print(f"LIFECYCLE_FUNNEL: {funnel.to_dict()}")
    print(f"N_CLOSED_EARLY_BY_CP: {n_closed_early}")
    v4.log("09 complete.")
    return df, summary_df


if __name__ == "__main__":
    main()
