"""
08_triple_barrier.py - Part G: AFML triple-barrier TP/SL/TIME labels.

Dynamic volatility threshold (causal EWM std of log returns, span=100,
min_periods=20 - identical to the v1 engine), day-bounded vertical barrier
(40 bars), side-aware PT/SL placed at +/- pt_multiple/sl_multiple * vol
around close_t0. pt_multiple=sl_multiple=1.0 is the PRIMARY config, fixed a
priori (configs/v3_config.yaml) and never tuned on this engine's own
results. A research-only ptSl grid is also run for sensitivity reporting
(AFML Ch.11: reject, never select).

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v3_common as v3

cfg = v3.load_config()


def main():
    v3.log("08: applying AFML triple-barrier labels to entry candidates...")
    candidates = pd.read_parquet(v3.OUT_DIR / "candidate_events_entry_v3.parquet")
    nqu6 = v3.load_nqu6_master().sort_values("bar_index").reset_index(drop=True)
    nqu6["vol_target"] = v3.ewm_log_return_vol(
        nqu6["px_close"], span_bars=cfg["volatility"]["span_bars"], min_periods=cfg["volatility"]["min_periods"]
    )
    close = nqu6["px_close"].to_numpy(); high = nqu6["px_high"].to_numpy(); low = nqu6["px_low"].to_numpy()
    day = nqu6["day"].to_numpy(); vol_arr = nqu6["vol_target"].to_numpy()

    t0_idx = candidates["bar_idx"].to_numpy()
    side = candidates["side_primary"].to_numpy()
    vol_t0 = vol_arr[t0_idx]

    TB = cfg["triple_barrier"]
    res = v3.apply_triple_barrier(t0_idx, side, close, high, low, vol_t0, day,
                                   TB["pt_multiple"], TB["sl_multiple"], TB["vertical_barrier_bars"],
                                   tie_break=TB["tie_break_same_bar"])

    labels = pd.DataFrame({
        "event_id": candidates["event_id"].to_numpy(),
        "t0_idx": t0_idx,
        "t1_idx": res["t1_idx"],
        "label_primary": res["label_primary"],
        "first_touch": res["first_touch"],
        "realized_ret": res["realized_ret"],
        "realized_points": res["realized_points"],
        "mfe_points": res["mfe_points"],
        "mae_points": res["mae_points"],
        "holding_bars": res["holding_bars"],
        "pt_price": res["pt_price"],
        "sl_price": res["sl_price"],
        "volatility_at_t0": vol_t0,
    })
    labels.loc[labels["t1_idx"] < 0, "first_touch"] = "NO_SIDE_NOT_LABELED"
    labels.to_parquet(v3.OUT_DIR / "triple_barrier_labels_entry_v3.parquet", index=False)

    n_labeled = int((labels["t1_idx"] >= 0).sum())
    vc = labels.loc[labels["t1_idx"] >= 0, "first_touch"].value_counts()
    v3.log(f"  labeled (side!=0) candidates: {n_labeled} / {len(labels)}")
    v3.log(f"  first_touch breakdown: {vc.to_dict()}")

    # ── research-only ptSl grid (sensitivity report, never used to retune) ──
    grid_rows = []
    for pt_g, sl_g in TB["research_grid_pt_sl_pairs"]:
        res_g = v3.apply_triple_barrier(t0_idx, side, close, high, low, vol_t0, day,
                                         pt_g, sl_g, TB["vertical_barrier_bars"], tie_break=TB["tie_break_same_bar"])
        mask = res_g["t1_idx"] >= 0
        n = int(mask.sum())
        if n == 0:
            continue
        lbl = res_g["label_primary"][mask]
        ft = pd.Series(res_g["first_touch"][mask])
        grid_rows.append(dict(
            pt_multiple=pt_g, sl_multiple=sl_g, n_labeled=n,
            pct_label_pos=float((lbl == 1).mean()), pct_PT=float((ft == "PT").mean()),
            pct_SL=float((ft == "SL").mean()), pct_VB=float((ft == "VB").mean()),
            mean_realized_points=float(np.nanmean(res_g["realized_points"][mask])),
            mean_holding_bars=float(np.nanmean(res_g["holding_bars"][mask])),
            is_primary_config=(pt_g == TB["pt_multiple"] and sl_g == TB["sl_multiple"]),
        ))
    grid_df = pd.DataFrame(grid_rows)
    grid_df.to_csv(v3.OUT_DIR / "triple_barrier_ptsl_grid_RESEARCH_ONLY_v3.csv", index=False)
    v3.log("  RESEARCH-ONLY ptSl sensitivity grid (not used to tune the primary config):")
    print(grid_df.to_string(index=False))

    print(f"TRIPLE_BARRIER_LABELED: {n_labeled}")
    print(f"TRIPLE_BARRIER_FIRST_TOUCH: {vc.to_dict()}")
    v3.log("08 complete.")
    return labels, grid_df


if __name__ == "__main__":
    main()
