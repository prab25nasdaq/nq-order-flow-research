"""
05_age_touch_decay.py - Part F: age and touch decay analysis for dashboard
S/R levels. Pure descriptive grouped statistics - NO model fitting.

READ-ONLY. Writes only inside this engine's own outputs/.
SHADOW / RESEARCH ONLY / NO MODEL TRAINING.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sr_common as sc

MIN_N_HALF_LIFE = 30
AGE_ORDER = ["age_0_500", "age_500_1000", "age_1000_5000", "age_5000_10000", "age_10000_plus"]
AGE_MIDPOINT = {"age_0_500": 250, "age_500_1000": 750, "age_1000_5000": 3000,
                "age_5000_10000": 7500, "age_10000_plus": 13000}


def rate(mask_num: pd.Series, mask_den: pd.Series) -> float:
    d = int(mask_den.sum())
    return float(mask_num.sum() / d) if d > 0 else np.nan


def summarize(g: pd.DataFrame) -> dict:
    is_supp = g["approach_side"] == "from_above"
    is_res = g["approach_side"] == "from_below"
    n = len(g)
    return {
        "event_count": n,
        "n_support_tests": int(is_supp.sum()),
        "n_resistance_tests": int(is_res.sum()),
        "support_hold_rate": rate((g["behavior_label"] == "SUPPORT_HELD") & is_supp, is_supp),
        "support_failure_rate": rate((g["behavior_label"] == "SUPPORT_FAILED") & is_supp, is_supp),
        "resistance_hold_rate": rate((g["behavior_label"] == "RESISTANCE_HELD") & is_res, is_res),
        "resistance_failure_rate": rate((g["behavior_label"] == "RESISTANCE_FAILED") & is_res, is_res),
        "breakout_acceptance_rate": rate(g["behavior_label"] == "BREAKOUT_ACCEPTANCE", pd.Series(True, index=g.index)),
        "fake_breakout_rate": rate(g["behavior_label"] == "FAKE_BREAKOUT_SWEEP", pd.Series(True, index=g.index)),
        "no_reaction_rate": rate(g["behavior_label"] == "NO_REACTION", pd.Series(True, index=g.index)),
        "overall_hold_rate": rate(g["behavior_label"].isin(["SUPPORT_HELD", "RESISTANCE_HELD"]), pd.Series(True, index=g.index)),
        "mean_MFE_20": float(g["MFE_20"].mean()), "mean_MAE_20": float(g["MAE_20"].mean()),
        "MFE_MAE_ratio_20": float(g["MFE_20"].mean() / g["MAE_20"].mean()) if g["MAE_20"].mean() else np.nan,
        "mean_final_return_20": float(g["final_return_20"].mean()),
        "median_final_return_20": float(g["final_return_20"].median()),
        "average_bars_to_reaction": float(g["time_to_rejection"].mean()),
        "average_bars_to_failure": float(g["time_to_break"].mean()),
    }


def main():
    sc.log("05: loading behavior labels + mfe/mae + retouch events...")
    events = pd.read_parquet(sc.OUT_DIR / "dashboard_sr_retouch_events.parquet")
    beh = pd.read_parquet(sc.OUT_DIR / "dashboard_sr_behavior_labels.parquet")
    mfe = pd.read_parquet(sc.OUT_DIR / "dashboard_sr_mfe_mae_by_horizon.parquet")

    j = beh.merge(mfe[["event_id", "MFE_20", "MAE_20", "final_return_20"]], on="event_id")
    j = j.merge(events[["event_id", "session"]], on="event_id", suffixes=("", "_dup"))
    sc.log(f"  {len(j)} joined event rows")

    group_cols = ["lookback_window", "level_age_bucket", "level_type", "dashboard_level_source",
                  "approach_side", "session", "touch_number_for_this_level"]
    sc.log("  Part F main grouped summary (lookback x age_bucket x type x source x side x session x touch#)...")
    main_rows = []
    for keys, g in j.groupby(group_cols, dropna=False):
        row = dict(zip(group_cols, keys))
        row.update(summarize(g))
        main_rows.append(row)
    main_df = pd.DataFrame(main_rows)
    main_path = sc.OUT_DIR / "dashboard_sr_age_decay_summary.csv"
    main_df.to_csv(main_path, index=False)
    sc.log(f"  wrote {main_path} ({len(main_df)} rows)")

    sc.log("  Part F: age-bucket performance (coarser, swing S/R only)...")
    swing = j[j["level_type"].isin(["SWING_RESISTANCE", "SWING_SUPPORT"])]
    age_rows = []
    for keys, g in swing.groupby(["lookback_window", "level_age_bucket"], dropna=False):
        row = dict(zip(["lookback_window", "level_age_bucket"], keys))
        row.update(summarize(g))
        age_rows.append(row)
    age_df = pd.DataFrame(age_rows)
    age_path = sc.OUT_DIR / "dashboard_sr_age_bucket_performance.csv"
    age_df.to_csv(age_path, index=False)
    sc.log(f"  wrote {age_path} ({len(age_df)} rows)")

    sc.log("  Part F: touch-number decay (swing S/R only, touch# bucketed 1/2/3/4+)...")
    swing = swing.copy()
    swing["touch_bucket"] = swing["touch_number_for_this_level"].clip(upper=4).map(
        {1: "1st_touch", 2: "2nd_touch", 3: "3rd_touch", 4: "4th_touch_or_more"})
    touch_rows = []
    for keys, g in swing.groupby(["lookback_window", "touch_bucket"], dropna=False):
        row = dict(zip(["lookback_window", "touch_bucket"], keys))
        row.update(summarize(g))
        touch_rows.append(row)
    touch_df = pd.DataFrame(touch_rows)
    touch_path = sc.OUT_DIR / "dashboard_sr_touch_number_decay.csv"
    touch_df.to_csv(touch_path, index=False)
    sc.log(f"  wrote {touch_path} ({len(touch_df)} rows)")

    sc.log("  Part F: old-level reactivation rate + half-life estimate...")
    hl_rows = []
    for wlabel, g_w in age_df.groupby("lookback_window"):
        g_w = g_w.set_index("level_age_bucket")
        old_buckets = [b for b in ("age_5000_10000", "age_10000_plus") if b in g_w.index]
        reactivation = np.nan
        if old_buckets:
            n_old = sum(g_w.loc[b, "event_count"] for b in old_buckets)
            n_react = sum(g_w.loc[b, "event_count"] * (1 - g_w.loc[b, "no_reaction_rate"])
                          for b in old_buckets if np.isfinite(g_w.loc[b, "no_reaction_rate"]))
            reactivation = float(n_react / n_old) if n_old else np.nan

        # half-life: find the age at which overall_hold_rate first drops to <= half
        # of the youngest bucket's (age_0_500) hold rate, by linear interpolation
        # across available bucket midpoints. Requires >=3 buckets with n>=MIN_N_HALF_LIFE
        # and a monotonic-ish decline; otherwise flagged NOT_STATISTICALLY_VALID.
        valid_buckets = [b for b in AGE_ORDER if b in g_w.index and g_w.loc[b, "event_count"] >= MIN_N_HALF_LIFE]
        half_life = "NOT_STATISTICALLY_VALID"
        if "age_0_500" in valid_buckets and len(valid_buckets) >= 3:
            base_rate = g_w.loc["age_0_500", "overall_hold_rate"]
            target = base_rate / 2.0
            xs = [AGE_MIDPOINT[b] for b in valid_buckets]
            ys = [g_w.loc[b, "overall_hold_rate"] for b in valid_buckets]
            # monotonic-ish check: allow at most one local increase
            increases = sum(1 for i in range(1, len(ys)) if ys[i] > ys[i - 1] + 0.05)
            if increases <= 1 and np.isfinite(base_rate) and base_rate > 0:
                below = [i for i, y in enumerate(ys) if y <= target]
                if below:
                    i1 = below[0]
                    if i1 == 0:
                        half_life = float(xs[0])
                    else:
                        i0 = i1 - 1
                        x0, x1, y0, y1 = xs[i0], xs[i1], ys[i0], ys[i1]
                        half_life = float(x0 + (target - y0) * (x1 - x0) / (y1 - y0)) if y1 != y0 else float(x1)
        hl_rows.append({
            "lookback_window": wlabel, "old_level_reactivation_rate": reactivation,
            "level_half_life_estimate_bars": half_life,
            "n_valid_age_buckets": len(valid_buckets),
        })
    hl_df = pd.DataFrame(hl_rows)
    hl_path = sc.OUT_DIR / "dashboard_sr_level_half_life_estimates.csv"
    hl_df.to_csv(hl_path, index=False)
    sc.log(f"  wrote {hl_path}")
    sc.log("05: done.")


if __name__ == "__main__":
    main()
