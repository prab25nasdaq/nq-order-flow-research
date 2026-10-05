"""
04_mfe_mae_after_touch.py - Part D: MFE/MAE after level touch.

Computed side-adjusted TWO ways per event, at horizons 5/10/20/40 bars
(day-bounded):
  REJECTION hypothesis side: the trade direction that profits if the level
    HOLDS (FROM_ABOVE approach -> hypothesis side=LONG i.e. betting price
    bounces back up away from the level; FROM_BELOW -> hypothesis
    side=SHORT) - matches v4's own side_primary convention exactly.
  BREAKOUT hypothesis side: the OPPOSITE direction - the trade that profits
    if the level FAILS and price continues through it.

For AT_LEVEL approach events (no clear prior-bar direction), the
"rejection" hypothesis is defined as fading whichever direction the price
moves LEAST over the first near-term bars (symmetric proxy) - documented,
small sample.

MFE/MAE are SIGNED (favorable >=0 typically, adverse <=0 typically) - same
convention as v4's own realized_mfe_side_adjusted/realized_mae_side_adjusted.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atlas_common as ac

cfg = ac.load_config()
HORIZONS = cfg["mfe_mae_horizons_bars"]  # [5,10,20,40]


def side_for_hypothesis(side_of_approach: str, hypothesis: str) -> int:
    if side_of_approach == "FROM_ABOVE":
        return 1 if hypothesis == "reject" else -1
    if side_of_approach == "FROM_BELOW":
        return -1 if hypothesis == "reject" else 1
    return 0  # AT_LEVEL - handled separately


def compute_mfe_mae(c0, side, seg_h, seg_l, seg_c):
    if side == 0 or len(seg_h) == 0:
        return dict(mfe=np.nan, mae=np.nan, final_return=np.nan)
    if side == 1:
        mfe = float(np.nanmax(seg_h) - c0); mae = float(np.nanmin(seg_l) - c0)
        final_return = float(seg_c[-1] - c0) if len(seg_c) else np.nan
    else:
        mfe = float(c0 - np.nanmin(seg_l)); mae = float(c0 - np.nanmax(seg_h))
        final_return = float(c0 - seg_c[-1]) if len(seg_c) else np.nan
    return dict(mfe=mfe, mae=mae, final_return=final_return)


def main():
    ac.log("04: computing side-adjusted MFE/MAE after level touch (rejection + breakout hypotheses)...")
    ev = pd.read_parquet(ac.OUT_DIR / "level_touch_events.parquet")
    nqu6 = ac.load_nqu6_master()
    nqu6_scoped = nqu6[nqu6["rithmic_date_str"].isin(cfg["scope"]["days"])].reset_index(drop=True)

    rows = []
    for d in sorted(ev["rithmic_date_str"].unique()):
        day_df = nqu6_scoped[nqu6_scoped["rithmic_date_str"] == d].reset_index(drop=True)
        c = pd.to_numeric(day_df["px_close"], errors="coerce").to_numpy()
        h = pd.to_numeric(day_df["px_high"], errors="coerce").to_numpy()
        l = pd.to_numeric(day_df["px_low"], errors="coerce").to_numpy()
        n = len(day_df)
        day_events = ev[ev["rithmic_date_str"] == d]
        for _, e in day_events.iterrows():
            t = int(e["bar_idx_in_day"])
            if not np.isfinite(c[t]):
                continue
            c0 = float(c[t])
            rec = dict(event_id=int(e["event_id"]))
            for hyp in ("reject", "breakout"):
                if e["side_of_approach"] == "AT_LEVEL":
                    # symmetric proxy: use sign of the very next bar's close vs c0;
                    # "reject" fades that move, "breakout" follows it
                    nxt = c[t + 1] if t + 1 < n and np.isfinite(c[t + 1]) else np.nan
                    implied = 1 if (np.isfinite(nxt) and nxt < c0) else (-1 if np.isfinite(nxt) else 0)
                    side = implied if hyp == "reject" else -implied
                else:
                    side = side_for_hypothesis(e["side_of_approach"], hyp)
                for H in HORIZONS:
                    jmax = min(t + H, n - 1)
                    if jmax <= t:
                        for k in ("mfe", "mae", "final_return"):
                            rec[f"{hyp}_{k}_{H}"] = np.nan
                        continue
                    seg_h, seg_l, seg_c = h[t + 1:jmax + 1], l[t + 1:jmax + 1], c[t + 1:jmax + 1]
                    res = compute_mfe_mae(c0, side, seg_h, seg_l, seg_c)
                    rec[f"{hyp}_mfe_{H}"] = res["mfe"]
                    rec[f"{hyp}_mae_{H}"] = res["mae"]
                    rec[f"{hyp}_final_return_{H}"] = res["final_return"]
            rows.append(rec)

    mfe_mae = pd.DataFrame(rows)
    mfe_mae.to_parquet(ac.OUT_DIR / "level_touch_mfe_mae.parquet", index=False)

    ctx = ev[["event_id", "level_type", "level_source", "session", "rithmic_date_str"]]
    joined = mfe_mae.merge(ctx, on="event_id", how="left")

    def summarize_group(df, group_col):
        rows = []
        for g, sub in df.groupby(group_col):
            rec = dict(group=g, n=len(sub))
            for hyp in ("reject", "breakout"):
                for H in HORIZONS:
                    rec[f"mean_{hyp}_mfe_{H}"] = float(sub[f"{hyp}_mfe_{H}"].mean())
                    rec[f"mean_{hyp}_mae_{H}"] = float(sub[f"{hyp}_mae_{H}"].mean())
                    rec[f"mean_{hyp}_final_return_{H}"] = float(sub[f"{hyp}_final_return_{H}"].mean())
            rows.append(rec)
        return pd.DataFrame(rows)

    by_type = summarize_group(joined, "level_type")
    by_type.to_csv(ac.OUT_DIR / "level_touch_mfe_mae_by_type.csv", index=False)
    by_session = summarize_group(joined, "session")
    by_session.to_csv(ac.OUT_DIR / "level_touch_mfe_mae_by_session.csv", index=False)

    ac.log(f"  events: {len(mfe_mae)}")
    print(by_type[["group", "n", "mean_reject_mfe_40", "mean_reject_mae_40", "mean_reject_final_return_40",
                  "mean_breakout_mfe_40", "mean_breakout_mae_40", "mean_breakout_final_return_40"]].to_string(index=False))
    ac.log("04 complete.")
    return mfe_mae, by_type, by_session


if __name__ == "__main__":
    main()
