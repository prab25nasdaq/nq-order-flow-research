"""
01_build_candidate_events.py - Candidate Event Ledger.

Each row is a POTENTIAL OPPORTUNITY, not a trade. The primary side comes
exclusively from the existing production model probability / level-reaction
event stream (predictions.csv) - no new side rule is invented here. Scope is
restricted to the NQU6 contract because true book-flow (OFI) meta-labeling
features (used in script 03) only exist for NQU6 in book_flow_chart/cache/.

READ-ONLY. Reads only from raw_snapshot/. Writes only to outputs/.
SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import afml_common as ac

cfg = ac.load_config()


def main():
    ac.log("01: loading NQU6 master + predictions.csv (NQU6-era)...")
    nqu6 = ac.load_nqu6_master().sort_values("bar_index").reset_index(drop=True)
    nqu6["vol_target"] = ac.ewm_log_return_vol(
        nqu6["px_close"], span_bars=cfg["volatility"]["span_bars"],
        min_periods=cfg["volatility"]["min_periods"],
    )

    preds = ac.load_predictions()
    preds_nqu6 = preds[preds["contract_symbol"] == cfg["scope"]["contract"]].copy()
    ac.log(f"  predictions.csv NQU6-era rows: {len(preds_nqu6)}")

    bar_lookup = nqu6.set_index("bar_end_ts_ns")[
        ["bar_index", "day", "px_close", "vol_target"]
    ]
    preds_nqu6 = preds_nqu6.merge(
        bar_lookup, left_on="bar_end_ts_ns", right_index=True, how="left",
        suffixes=("", "_master"),
    )
    n_unmatched = preds_nqu6["bar_index"].isna().sum()
    if n_unmatched:
        ac.log(f"  WARNING: {n_unmatched} prediction rows did not match a master bar - dropping")
        preds_nqu6 = preds_nqu6.dropna(subset=["bar_index"])
    preds_nqu6["bar_index"] = preds_nqu6["bar_index"].astype(int)

    preds_nqu6 = preds_nqu6.sort_values(["bar_end_ts_ns", "_orig_idx"]).reset_index(drop=True)

    side_map = {"LONG": 1, "SHORT": -1, "FLAT": 0}
    side_primary = preds_nqu6["direction"].map(side_map).to_numpy()

    # bars_since_last_event: density of the candidate stream itself (informational,
    # not a trading rule) - bars elapsed since the immediately preceding candidate
    bar_idx_arr = preds_nqu6["bar_index"].to_numpy()
    bars_since_last_event = np.empty(len(bar_idx_arr))
    bars_since_last_event[0] = 0
    bars_since_last_event[1:] = np.diff(bar_idx_arr)
    bars_since_last_event = np.clip(bars_since_last_event, 0, None)

    n_bars_total = len(nqu6)
    px_close = nqu6["px_close"].to_numpy()
    day_arr = nqu6["day"].to_numpy()
    vol_arr = nqu6["vol_target"].to_numpy()
    vbb = cfg["triple_barrier"]["vertical_barrier_bars"]
    pt_mult = cfg["triple_barrier"]["pt_multiple"]
    sl_mult = cfg["triple_barrier"]["sl_multiple"]

    close_t0 = px_close[bar_idx_arr]
    vol_t0 = vol_arr[bar_idx_arr]
    day_t0 = day_arr[bar_idx_arr]

    # planned vertical barrier bar index (day-bounded), BEFORE resolution is known
    vb_idx = np.minimum(bar_idx_arr + vbb, n_bars_total - 1)
    for i in range(len(vb_idx)):
        while vb_idx[i] > bar_idx_arr[i] and day_arr[vb_idx[i]] != day_t0[i]:
            vb_idx[i] -= 1
    vertical_barrier_t1 = nqu6["bar_end_ts_ns"].to_numpy()[vb_idx]

    pt_price = np.where(
        side_primary != 0, close_t0 * np.exp(side_primary * pt_mult * vol_t0), np.nan
    )
    sl_price = np.where(
        side_primary != 0, close_t0 * np.exp(-side_primary * sl_mult * vol_t0), np.nan
    )

    in_sample_contaminated = preds_nqu6["timestamp_utc"] <= ac.MODEL_TRAINING_CUTOFF_UTC

    events = pd.DataFrame({
        "event_id": np.arange(len(preds_nqu6)),
        "t0": preds_nqu6["timestamp_utc"].values,
        "t0_idx": bar_idx_arr,
        "bar_end_ts_ns": preds_nqu6["bar_end_ts_ns"].to_numpy(),
        "contract": preds_nqu6["contract_symbol"].to_numpy(),
        "side_primary": side_primary,
        "side_source": "model_probability_reaction_event",
        "primary_probability": preds_nqu6["p_long"].to_numpy(),
        "primary_confidence": preds_nqu6["confidence"].to_numpy(),
        "probability_source": "CURRENT_EVENT",
        "bars_since_last_event": bars_since_last_event,
        "reaction_type": preds_nqu6["reaction_type"].to_numpy(),
        "training_gate_status": preds_nqu6["training_gate_status"].to_numpy(),
        "nearest_level_type": preds_nqu6["level_type"].to_numpy(),
        "nearest_level_distance": preds_nqu6["dist_to_level_ticks"].to_numpy(),
        "feature_snapshot_id": np.arange(len(preds_nqu6)),  # == event_id, join key for script 03
        "close_t0": close_t0,
        "volatility_target": vol_t0,
        "vertical_barrier_t1": vertical_barrier_t1,
        "vertical_barrier_idx": vb_idx,
        "pt_multiple": pt_mult,
        "sl_multiple": sl_mult,
        "pt_price": pt_price,
        "sl_price": sl_price,
        "status_initial": "CANDIDATE",
        "in_sample_contaminated": in_sample_contaminated.to_numpy(),
        "day": day_t0,
    })

    n_total = len(events)
    n_long = int((events["side_primary"] == 1).sum())
    n_short = int((events["side_primary"] == -1).sum())
    n_flat = int((events["side_primary"] == 0).sum())
    n_oos = int((~events["in_sample_contaminated"]).sum())

    events.to_parquet(ac.OUT_DIR / "candidate_events.parquet", index=False)
    ac.log(f"01 complete: {n_total} candidate events (LONG={n_long} SHORT={n_short} FLAT={n_flat}); "
          f"genuinely-out-of-sample (post training cutoff {ac.MODEL_TRAINING_CUTOFF_UTC}): {n_oos}")
    print(f"CANDIDATE_EVENTS_TOTAL: {n_total}")
    print(f"CANDIDATE_EVENTS_LONG: {n_long}")
    print(f"CANDIDATE_EVENTS_SHORT: {n_short}")
    print(f"CANDIDATE_EVENTS_FLAT_NO_SIDE: {n_flat}")
    print(f"CANDIDATE_EVENTS_GENUINELY_OOS: {n_oos}")
    print(f"CANDIDATE_EVENTS_IN_SAMPLE_CONTAMINATED: {n_total - n_oos}")
    return events


if __name__ == "__main__":
    main()
