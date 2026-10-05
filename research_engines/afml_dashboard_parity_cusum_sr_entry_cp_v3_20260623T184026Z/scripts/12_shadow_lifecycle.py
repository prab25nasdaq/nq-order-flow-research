"""
12_shadow_lifecycle.py - Part K: shadow lifecycle simulation.

Combines the Entry ACT/PASS model (script 10) + CP CLOSE/HOLD model
(script 11) + triple-barrier TP/SL/TIME labels (script 08) into a single
research ledger. NO ORDERS, NO EXECUTION anywhere in this script.

States: NO_CANDIDATE -> CLOSED (side_primary==0, no opportunity)
        CANDIDATE -> PASS_ENTRY -> CLOSED (entry model says PASS, or no
                     out-of-fold entry prediction available - "no usable
                     prediction" defaults to PASS, never fabricated)
        CANDIDATE -> ACTIVE_SHADOW -> CLOSE_CP -> CLOSED (CP model says
                     CLOSE on some active bar strictly before the AFML
                     barrier resolves)
        CANDIDATE -> ACTIVE_SHADOW -> {EXIT_TP|EXIT_SL|EXIT_TIME} -> CLOSED
                     (held all the way to the triple-barrier outcome - no
                     CP model said CLOSE before then, or no CP prediction
                     was available)

The specific entry/CP model used to DRIVE this illustrative simulation is
the one with the best mean-of-folds MCC in model_comparison_{entry,cp}_v3.csv
- chosen for demonstration of the full plumbing only, NOT a claim that this
selection is "the best model" in any production sense (n is far too small
for that - see Part L report).

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v3_common as v3

cfg = v3.load_config()


def pick_representative_model(comparison_csv_name: str) -> str:
    path = v3.OUT_DIR / comparison_csv_name
    df = pd.read_csv(path)
    if df.empty or "mean_of_folds_mcc" not in df.columns:
        return None
    df = df.dropna(subset=["mean_of_folds_mcc"])
    if df.empty:
        return None
    return df.loc[df["mean_of_folds_mcc"].idxmax(), "model"]


def main():
    v3.log("12: building shadow lifecycle ledger (entry ACT/PASS + CP CLOSE/HOLD + TP/SL/TIME)...")
    candidates = pd.read_parquet(v3.OUT_DIR / "candidate_events_entry_v3.parquet")
    labels = pd.read_parquet(v3.OUT_DIR / "triple_barrier_labels_entry_v3.parquet")
    entry_pred = pd.read_parquet(v3.OUT_DIR / "entry_model_predictions_v3.parquet")
    cp_pred = pd.read_parquet(v3.OUT_DIR / "cp_model_predictions_v3.parquet")

    entry_model = pick_representative_model("model_comparison_entry_v3.csv")
    cp_model = pick_representative_model("model_comparison_cp_v3.csv")
    v3.log(f"  representative entry model: {entry_model}  |  representative CP model: {cp_model}")

    df = candidates.merge(labels[["event_id", "t1_idx", "label_primary", "first_touch",
                                  "realized_points", "holding_bars"]], on="event_id", how="left")

    final_state = np.full(len(df), "", dtype=object)
    exit_reason = np.full(len(df), "", dtype=object)
    cp_close_bar = np.full(len(df), -1, dtype=int)

    side0 = df["side_primary"] == 0
    final_state[side0.to_numpy()] = "NO_CANDIDATE"
    exit_reason[side0.to_numpy()] = "no_structural_side"

    candidate_mask = ~side0
    entry_p = None
    if entry_model is not None and f"p_{entry_model}" in entry_pred.columns:
        entry_p = entry_pred.set_index("event_id")[f"p_{entry_model}"]

    not_labeled = candidate_mask & (df["t1_idx"] < 0)
    final_state[not_labeled.to_numpy()] = "PASS_ENTRY"
    exit_reason[not_labeled.to_numpy()] = "never_triple_barrier_labeled"

    labeled_mask = candidate_mask & (df["t1_idx"] >= 0)
    for idx in df.index[labeled_mask]:
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

        # ACTIVE_SHADOW: walk bars t0..t1-1 checking CP model; if no signal, ride to barrier
        t0, t1 = int(df.at[idx, "bar_idx"]), int(df.at[idx, "t1_idx"])
        cp_sub = cp_pred[(cp_pred["event_id"] == eid)].sort_values("snapshot_bar_idx") if cp_pred is not None and len(cp_pred) else pd.DataFrame()
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
            ft = df.at[idx, "first_touch"]
            final_state[idx] = {"PT": "EXIT_TP", "SL": "EXIT_SL", "VB": "EXIT_TIME"}.get(ft, "EXIT_TIME")
            exit_reason[idx] = f"rode_to_triple_barrier_{ft}"

    df["final_state"] = final_state
    df["exit_reason"] = exit_reason
    df["cp_close_bar"] = cp_close_bar
    df["entry_model_used"] = entry_model
    df["cp_model_used"] = cp_model

    df["status_path"] = np.where(
        side0, "NO_CANDIDATE->CLOSED",
        np.where(df["final_state"] == "PASS_ENTRY", "CANDIDATE->PASS_ENTRY->CLOSED",
                np.where(df["final_state"] == "CLOSE_CP", "CANDIDATE->ACTIVE_SHADOW->CLOSE_CP->CLOSED",
                        "CANDIDATE->ACTIVE_SHADOW->" + df["final_state"].astype(str) + "->CLOSED")),
    )

    df.to_parquet(v3.OUT_DIR / "shadow_lifecycle_v3.parquet", index=False)

    funnel = df["final_state"].value_counts()
    summary_rows = [{"metric": "entry_model_used", "value": entry_model},
                    {"metric": "cp_model_used", "value": cp_model}]
    for state, cnt in funnel.items():
        summary_rows.append({"metric": f"final_state__{state}", "value": int(cnt)})
    n_active_shadow_total = int((df["final_state"].isin(["CLOSE_CP", "EXIT_TP", "EXIT_SL", "EXIT_TIME"])).sum())
    n_closed_early = int((df["final_state"] == "CLOSE_CP").sum())
    summary_rows.append({"metric": "n_entered_active_shadow", "value": n_active_shadow_total})
    summary_rows.append({"metric": "n_closed_early_by_cp", "value": n_closed_early})
    summary_rows.append({"metric": "pct_closed_early_by_cp", "value": float(n_closed_early / max(n_active_shadow_total, 1))})
    # value comparison: CP-closed positions' actual realized vs what holding to barrier would have given
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
    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(v3.OUT_DIR / "shadow_lifecycle_summary_v3.csv", index=False)

    v3.log(f"  lifecycle funnel: {funnel.to_dict()}")
    print(f"ENTRY_MODEL_USED: {entry_model}")
    print(f"CP_MODEL_USED: {cp_model}")
    print(f"LIFECYCLE_FUNNEL: {funnel.to_dict()}")
    print(f"N_CLOSED_EARLY_BY_CP: {n_closed_early}")
    v3.log("12 complete.")
    return df, summary_df


if __name__ == "__main__":
    main()
