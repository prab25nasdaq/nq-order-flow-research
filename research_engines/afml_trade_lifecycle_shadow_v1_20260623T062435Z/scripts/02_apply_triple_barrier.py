"""
02_apply_triple_barrier.py - AFML Ch.3 triple-barrier labeling.

Side-aware: PT/SL barriers are placed using the dynamic (EWM) volatility
target around close_t0, asymmetric to side_primary (favorable side = PT,
adverse side = SL); vertical barrier is the day-bounded bar from script 01.
First touch is path-dependent (continuous_high/low equivalent: px_high/px_low),
scanned bar-by-bar - no future leakage (the scan only ever looks forward from
t0, which is exactly what AFML's triple barrier requires by definition: the
LABEL of an event necessarily depends on its own future path; this is not a
feature, so it carries no leakage into any predictive feature set).

A ptSl multiple grid is run purely as a RESEARCH-ONLY diagnostic (AFML Ch.11:
backtesting rejects bad specs, never selects good ones) - it is reported
alongside the primary (1.0, 1.0) labeling but never substituted for it.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import afml_common as ac

cfg = ac.load_config()


def label_with_ptsl(events: pd.DataFrame, nqu6: pd.DataFrame, pt_mult: float, sl_mult: float) -> dict:
    close = nqu6["px_close"].to_numpy()
    high = nqu6["px_high"].to_numpy()
    low = nqu6["px_low"].to_numpy()
    day = nqu6["day"].to_numpy()
    t0_idx = events["t0_idx"].to_numpy()
    side = events["side_primary"].to_numpy()
    vol = events["volatility_target"].to_numpy()
    return ac.apply_triple_barrier(
        t0_idx, side, close, high, low, vol, day, pt_mult, sl_mult,
        cfg["triple_barrier"]["vertical_barrier_bars"],
        tie_break=cfg["triple_barrier"]["tie_break_same_bar"],
    )


def main():
    ac.log("02: loading candidate events + NQU6 master price path...")
    events = pd.read_parquet(ac.OUT_DIR / "candidate_events.parquet")
    nqu6 = ac.load_nqu6_master().sort_values("bar_index").reset_index(drop=True)
    bar_end_ts_ns_arr = nqu6["bar_end_ts_ns"].to_numpy()

    pt_mult = cfg["triple_barrier"]["pt_multiple"]
    sl_mult = cfg["triple_barrier"]["sl_multiple"]
    ac.log(f"  PRIMARY labeling: pt_multiple={pt_mult} sl_multiple={sl_mult} "
          f"vertical_barrier_bars={cfg['triple_barrier']['vertical_barrier_bars']} "
          f"(set a priori in lifecycle_config.yaml)")
    res = label_with_ptsl(events, nqu6, pt_mult, sl_mult)

    labels = pd.DataFrame({
        "event_id": events["event_id"].to_numpy(),
        "t0_idx": events["t0_idx"].to_numpy(),
        "t1_idx": res["t1_idx"],
        "t1": np.where(res["t1_idx"] >= 0, bar_end_ts_ns_arr[np.clip(res["t1_idx"], 0, None)], np.nan),
        "label_primary": res["label_primary"],
        "first_touch": res["first_touch"],
        "realized_ret": res["realized_ret"],
        "realized_points": res["realized_points"],
        "mfe_points": res["mfe_points"],
        "mae_points": res["mae_points"],
        "holding_bars": res["holding_bars"],
        "pt_price_resolved": res["pt_price"],
        "sl_price_resolved": res["sl_price"],
    })
    labels.loc[labels["t1_idx"] < 0, "first_touch"] = "NO_SIDE_NOT_LABELED"

    labels.to_parquet(ac.OUT_DIR / "triple_barrier_labels.parquet", index=False)

    n_labeled = int((labels["t1_idx"] >= 0).sum())
    vc = labels.loc[labels["t1_idx"] >= 0, "first_touch"].value_counts()
    ac.log(f"  labeled (side!=0) events: {n_labeled} / {len(labels)}")
    ac.log(f"  first_touch breakdown: {vc.to_dict()}")
    ac.log(f"  mean holding_bars: {labels['holding_bars'].mean():.2f}  "
          f"mean realized_points: {labels['realized_points'].mean():.3f}")

    # ── RESEARCH-ONLY ptSl grid diagnostic (never used to select a "best" pair) ──
    ac.log("  running RESEARCH-ONLY ptSl grid diagnostic (AFML Ch.11: reject, never select)...")
    grid_rows = []
    for pt_g, sl_g in cfg["research_grid"]["pt_sl_pairs"]:
        res_g = label_with_ptsl(events, nqu6, pt_g, sl_g)
        mask = res_g["t1_idx"] >= 0
        n = int(mask.sum())
        if n == 0:
            continue
        lbl = res_g["label_primary"][mask]
        ft = pd.Series(res_g["first_touch"][mask])
        grid_rows.append(dict(
            pt_multiple=pt_g, sl_multiple=sl_g, n_labeled=n,
            pct_label_pos=float((lbl == 1).mean()), pct_label_neg=float((lbl == -1).mean()),
            pct_PT=float((ft == "PT").mean()), pct_SL=float((ft == "SL").mean()),
            pct_VB=float((ft == "VB").mean()),
            mean_realized_points=float(np.nanmean(res_g["realized_points"][mask])),
            median_realized_points=float(np.nanmedian(res_g["realized_points"][mask])),
            mean_holding_bars=float(np.nanmean(res_g["holding_bars"][mask])),
            is_primary_config=(pt_g == pt_mult and sl_g == sl_mult),
        ))
    grid_df = pd.DataFrame(grid_rows)
    grid_df.to_csv(ac.OUT_DIR / "triple_barrier_ptsl_grid_RESEARCH_ONLY.csv", index=False)
    ac.log("  grid (RESEARCH_ONLY, not used to tune the primary config):")
    print(grid_df.to_string(index=False))

    print(f"TRIPLE_BARRIER_LABELED_EVENTS: {n_labeled}")
    print(f"TRIPLE_BARRIER_FIRST_TOUCH: {vc.to_dict()}")
    return labels, grid_df


if __name__ == "__main__":
    main()
