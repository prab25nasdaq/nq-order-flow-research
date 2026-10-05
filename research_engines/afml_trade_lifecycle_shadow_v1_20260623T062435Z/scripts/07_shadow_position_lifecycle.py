"""
07_shadow_position_lifecycle.py - research-only lifecycle state machine.

NO ORDERS. This is a ledger only - every state below describes what WOULD
have happened to a research position if the engine had been connected to
execution (it is not, and never will be from this code path).

Two layers, matching the task's cancellation-logic bullets one-for-one:

PER-CANDIDATE layer (does this specific opportunity ever become a position?):
  CANDIDATE -> PASS                      (side_primary==0: no side to act on)
  CANDIDATE -> CANCEL_VERTICAL_EXPIRED    (side!=0, no meta verdict ever arrived
                                           in time, AND the underlying triple-
                                           barrier path itself timed out at the
                                           vertical barrier - first_touch=='VB')
  CANDIDATE -> CANCEL_STALE               (side!=0, no meta verdict ever arrived
                                           in time, but the underlying path DID
                                           resolve via a price barrier (PT/SL) -
                                           the signal, not the opportunity, was
                                           what went stale)
  CANDIDATE -> CANCEL_META_PASS           (meta verdict arrived but sized to zero
                                           - "zero bet size from meta-label")
  CANDIDATE -> PENDING_ENTRY_SHADOW -> ACTIVE_SHADOW -> {EXIT_PT|EXIT_SL|EXIT_TIME}
                                           (meta verdict arrived, sized non-zero;
                                           exit reason = triple-barrier first_touch)
  -> CLOSED (terminal, every event_id ends here)

AGGREGATE (portfolio) layer, derived bar-by-bar from the averaged active-bet
signal (script 06's target_position_shadow), mechanically per the task text:
  FLAT <-> ACTIVE_SHADOW <-> REDUCE_SHADOW
  EXIT_SIZE_ZERO event:  target_position_shadow goes from non-zero to zero
  EXIT_META_FLIP event:  target_position_shadow's sign flips bar-to-bar

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import afml_common as ac

cfg = ac.load_config()


def build_per_event_lifecycle(sizing: pd.DataFrame, labels: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    df = events.merge(sizing, on="event_id", how="left", suffixes=("", "_sz")) \
               .merge(labels, on="event_id", how="left", suffixes=("", "_lbl"))

    final_state = np.full(len(df), "", dtype=object)
    exit_reason = np.full(len(df), "", dtype=object)

    side0 = df["side_primary"] == 0
    final_state[side0.to_numpy()] = "PASS"
    exit_reason[side0.to_numpy()] = "no_primary_side"

    not_eligible = (~side0) & (~df["eligible_for_meta_training"].fillna(False))
    # side!=0 but never resolved by triple-barrier at all (vol warm-up edge case from script 02/03)
    final_state[not_eligible.to_numpy()] = "CANCEL_VERTICAL_EXPIRED"
    exit_reason[not_eligible.to_numpy()] = "never_triple_barrier_labeled_vol_warmup"

    side_nonzero_eligible = (~side0) & df["eligible_for_meta_training"].fillna(False)
    no_meta = side_nonzero_eligible & (~df["p_meta_is_out_of_fold"].fillna(False))
    no_meta_vb = no_meta & (df["first_touch"] == "VB")
    no_meta_other = no_meta & (df["first_touch"] != "VB")
    final_state[no_meta_vb.to_numpy()] = "CANCEL_VERTICAL_EXPIRED"
    exit_reason[no_meta_vb.to_numpy()] = "no_meta_prediction_before_vertical_barrier"
    final_state[no_meta_other.to_numpy()] = "CANCEL_STALE"
    exit_reason[no_meta_other.to_numpy()] = "no_meta_prediction_signal_stale"

    has_meta = side_nonzero_eligible & df["p_meta_is_out_of_fold"].fillna(False)
    meta_pass = has_meta & np.isclose(df["final_side_size"].fillna(0.0), 0.0)
    final_state[meta_pass.to_numpy()] = "CANCEL_META_PASS"
    exit_reason[meta_pass.to_numpy()] = "meta_label_zero_size"

    entered = has_meta & ~np.isclose(df["final_side_size"].fillna(0.0), 0.0)
    touch_map = {"PT": "EXIT_PT", "SL": "EXIT_SL", "VB": "EXIT_TIME"}
    for touch, state in touch_map.items():
        m = entered & (df["first_touch"] == touch)
        final_state[m.to_numpy()] = state
        exit_reason[m.to_numpy()] = f"triple_barrier_first_touch_{touch}"

    df["final_state"] = final_state
    df["exit_reason"] = exit_reason
    df["entered_shadow_position"] = entered.to_numpy()
    df["status_path"] = np.where(
        side0, "CANDIDATE->PASS->CLOSED",
        np.where(entered, "CANDIDATE->PENDING_ENTRY_SHADOW->ACTIVE_SHADOW->" + df["final_state"] + "->CLOSED",
                "CANDIDATE->" + df["final_state"] + "->CLOSED"),
    )
    return df


def build_active_bets_long(active: pd.DataFrame, bar_index: np.ndarray) -> pd.DataFrame:
    rows = []
    for _, r in active.iterrows():
        lo, hi = max(0, int(r["t0_idx"])), min(len(bar_index) - 1, int(r["t1_idx"]))
        for b in range(lo, hi + 1):
            rows.append((b, int(r["event_id"]), r["final_side_size"]))
    return pd.DataFrame(rows, columns=["bar_idx", "event_id", "size"])


def main():
    ac.log("07: building per-event lifecycle + aggregate portfolio-state timeline...")
    events = pd.read_parquet(ac.OUT_DIR / "candidate_events.parquet")
    meta_ds = pd.read_parquet(ac.OUT_DIR / "meta_label_dataset.parquet")[
        ["event_id", "eligible_for_meta_training"]
    ]
    sizing = pd.read_parquet(ac.OUT_DIR / "meta_model_predictions.parquet")[
        ["event_id", "p_meta_is_out_of_fold"]
    ].merge(
        pd.read_parquet(ac.OUT_DIR / "bet_sizing_signal.parquet")[["event_id", "p_meta", "final_side_size"]],
        on="event_id", how="outer",
    )
    labels = pd.read_parquet(ac.OUT_DIR / "triple_barrier_labels.parquet")[
        ["event_id", "t1_idx", "first_touch", "realized_points", "holding_bars"]
    ]
    events_min = events[["event_id", "t0", "t0_idx", "day", "side_primary"]].merge(
        meta_ds, on="event_id", how="left"
    )

    lifecycle = build_per_event_lifecycle(sizing, labels, events_min)
    lifecycle.to_parquet(ac.OUT_DIR / "shadow_position_lifecycle.parquet", index=False)
    lifecycle.drop(columns=[c for c in lifecycle.columns if lifecycle[c].dtype == "object" and
                            lifecycle[c].map(lambda x: isinstance(x, (list, dict))).any()],
                   errors="ignore").to_csv(ac.OUT_DIR / "shadow_position_lifecycle.csv", index=False)

    ac.log("  final_state distribution:")
    print(lifecycle["final_state"].value_counts())

    # ── aggregate portfolio-level state timeline ────────────────────────────
    timeline = pd.read_parquet(ac.OUT_DIR / "active_bet_timeline.parquet")
    tps = timeline["target_position_shadow"].to_numpy()
    prev = np.concatenate([[0.0], tps[:-1]])
    state = np.where(tps == 0, "FLAT",
             np.where(np.sign(tps) != np.sign(np.where(prev == 0, tps, prev)), "ACTIVE_SHADOW",
             np.where(np.abs(tps) < np.abs(prev), "REDUCE_SHADOW", "ACTIVE_SHADOW")))
    timeline["portfolio_state"] = state
    timeline["is_exit_size_zero_event"] = (prev != 0) & (tps == 0)
    timeline["is_exit_meta_flip_event"] = (prev != 0) & (tps != 0) & (np.sign(prev) != np.sign(tps))
    timeline.to_csv(ac.OUT_DIR / "shadow_position_bar_timeline.csv", index=False)
    timeline.to_parquet(ac.OUT_DIR / "shadow_position_bar_timeline.parquet", index=False)

    n_exit_zero = int(timeline["is_exit_size_zero_event"].sum())
    n_exit_flip = int(timeline["is_exit_meta_flip_event"].sum())
    ac.log(f"  aggregate EXIT_SIZE_ZERO events: {n_exit_zero}  EXIT_META_FLIP events: {n_exit_flip}")

    # ── shadow_active_bets.csv: long-format (bar_idx, event_id, size) join table ──
    bet_sizing = sizing
    bf_labels = labels
    active_events = bet_sizing.merge(bf_labels, on="event_id", how="left").merge(
        events[["event_id", "t0_idx"]], on="event_id", how="left"
    )
    active_events = active_events[
        (~np.isclose(active_events["final_side_size"].fillna(0.0), 0.0)) & (active_events["t1_idx"] >= 0)
    ]
    nqu6 = ac.load_nqu6_master().sort_values("bar_index").reset_index(drop=True)
    bar_index = nqu6["bar_index"].to_numpy()
    long_table = build_active_bets_long(active_events, bar_index)
    long_table.to_csv(ac.OUT_DIR / "shadow_active_bets.csv", index=False)
    ac.log(f"  shadow_active_bets.csv: {len(long_table)} (bar, event) rows across "
          f"{active_events['event_id'].nunique()} active positions")

    funnel = lifecycle["final_state"].value_counts().to_dict()
    print(f"LIFECYCLE_FUNNEL: {funnel}")
    print(f"AGGREGATE_EXIT_SIZE_ZERO_EVENTS: {n_exit_zero}")
    print(f"AGGREGATE_EXIT_META_FLIP_EVENTS: {n_exit_flip}")
    return lifecycle, timeline, long_table


if __name__ == "__main__":
    main()
