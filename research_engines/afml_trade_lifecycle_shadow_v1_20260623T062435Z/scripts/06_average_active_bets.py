"""
06_average_active_bets.py - AFML Ch.10 averaging active bets.

Each candidate's active lifespan runs from t0_idx to its ACTUAL resolved
t1_idx (triple-barrier first touch from script 02), not the originally
planned vertical barrier - a bet that hit PT/SL early stops being "active"
at that bar, exactly as AFML's snippet 10.2 (avgActiveSignals) intends.

Only events that were actually sized (final_side_size != 0, i.e. NOT a
meta-label pass) and successfully resolved (t1_idx >= 0) are included in the
active set - a passed/zero-sized candidate was never a position, so it must
not dilute the average of bets that WERE taken.

Pipeline (matches AFML snippet 10.1's ordering): per-event discretized size
(script 05) -> average across all bets active at each bar
(average_active_signal) -> re-discretize the AVERAGED signal
(target_position_shadow), so the final shadow position itself is jitter-free,
not just each individual bet.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION. target_position_shadow is
a research ledger column only - it is never read by any order/broker path.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import afml_common as ac

cfg = ac.load_config()


def main():
    ac.log("06: loading bet sizes + triple-barrier resolutions, averaging active bets...")
    sizing = pd.read_parquet(ac.OUT_DIR / "bet_sizing_signal.parquet")
    labels = pd.read_parquet(ac.OUT_DIR / "triple_barrier_labels.parquet")[["event_id", "t1_idx"]]
    events = sizing.merge(labels, on="event_id", how="left")

    active = events[(~np.isclose(events["final_side_size"], 0.0)) & (events["t1_idx"] >= 0)].copy()
    ac.log(f"  {len(active)} / {len(events)} events are genuinely active positions "
          f"(non-zero size AND resolved triple-barrier outcome)")

    nqu6 = ac.load_nqu6_master().sort_values("bar_index").reset_index(drop=True)
    bar_index = nqu6["bar_index"].to_numpy()

    timeline = ac.avg_active_signals(
        active, bar_index, t0_col="t0_idx", t1_col="t1_idx", size_col="final_side_size"
    )
    timeline["target_position_shadow"] = ac.discretize_signal(
        timeline["average_active_signal"].to_numpy(), cfg["bet_sizing"]["step_size"]
    )
    timeline["position_delta_shadow"] = timeline["target_position_shadow"].diff().fillna(0.0)
    timeline["bar_end_ts_ns"] = nqu6["bar_end_ts_ns"].to_numpy()
    timeline["day"] = nqu6["day"].to_numpy()

    timeline.to_parquet(ac.OUT_DIR / "active_bet_timeline.parquet", index=False)

    max_active = int(timeline["active_bets_count"].max())
    mean_active = float(timeline["active_bets_count"].mean())
    turnover_proxy = float(timeline["position_delta_shadow"].abs().sum())
    pct_flat_bars = float((timeline["active_bets_count"] == 0).mean())

    ac.log(f"06 complete: max_active_bets={max_active} mean_active_bets={mean_active:.3f} "
          f"turnover_proxy(sum|delta|)={turnover_proxy:.2f} pct_bars_flat={pct_flat_bars:.3f}")
    print(f"ACTIVE_BETS_MAX: {max_active}")
    print(f"ACTIVE_BETS_MEAN: {mean_active:.4f}")
    print(f"TURNOVER_PROXY_SUM_ABS_DELTA: {turnover_proxy:.4f}")
    print(f"PCT_BARS_FLAT: {pct_flat_bars:.4f}")
    return timeline


if __name__ == "__main__":
    main()
