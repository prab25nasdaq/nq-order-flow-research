"""
03_feature_reliability_report.py - feature_reliability_report.csv

Aggregates the rolling IC panel by feature x horizon x session (and a
separate by-day cut), to answer directly: which features change sign by
session, and which should be ignored when rolling IC is weak (low
|rolling_ic|, low rolling_ic_stability, or low |t-stat|)?

Session convention (3-bucket, identical to the prior institutional audit
and the v1/diagnostic engines): Asia/Overnight 22:00-05:59 UTC, London
06:00-12:59 UTC, US 13:00-21:59 UTC.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v2_common as v2


def assign_session_3bucket(hour_utc: pd.Series) -> pd.Series:
    def _f(h):
        if 6 <= h <= 12:
            return "London"
        elif 13 <= h <= 21:
            return "US"
        else:
            return "Asia_Overnight"
    return hour_utc.map(_f)


def main():
    v2.log("03: building feature_reliability_report.csv...")
    ic = pd.read_parquet(v2.OUT_DIR / "rolling_feature_ic_panel.parquet")
    ic["hour_utc"] = pd.to_datetime(ic["bar_end_ts_ns"], unit="ns", utc=True).dt.hour
    ic["session"] = assign_session_3bucket(ic["hour_utc"])

    rows = []
    for (feat, h), g in ic.groupby(["feature", "horizon"]):
        valid = g[g["rolling_ic"].notna()]
        if valid.empty:
            continue
        rows.append(dict(
            feature=feat, horizon=h, breakdown="ALL", breakdown_value="ALL",
            n_bars=len(valid), mean_ic=float(valid["rolling_ic"].mean()),
            std_ic=float(valid["rolling_ic"].std()),
            pct_positive_sign=float((valid["rolling_ic_sign"] > 0).mean()),
            pct_negative_sign=float((valid["rolling_ic_sign"] < 0).mean()),
            mean_abs_ic=float(valid["rolling_ic_abs_strength"].mean()),
            mean_tstat=float(valid["rolling_ic_tstat"].mean()),
            pct_tstat_significant=float((valid["rolling_ic_tstat"].abs() >= 2).mean()),
            mean_stability=float(valid["rolling_ic_stability"].mean()),
            pct_weak_ic_below_0p02=float((valid["rolling_ic_abs_strength"] < 0.02).mean()),
        ))
        for sess, gs in valid.groupby("session"):
            if len(gs) < 10:
                continue
            rows.append(dict(
                feature=feat, horizon=h, breakdown="session", breakdown_value=sess,
                n_bars=len(gs), mean_ic=float(gs["rolling_ic"].mean()),
                std_ic=float(gs["rolling_ic"].std()),
                pct_positive_sign=float((gs["rolling_ic_sign"] > 0).mean()),
                pct_negative_sign=float((gs["rolling_ic_sign"] < 0).mean()),
                mean_abs_ic=float(gs["rolling_ic_abs_strength"].mean()),
                mean_tstat=float(gs["rolling_ic_tstat"].mean()),
                pct_tstat_significant=float((gs["rolling_ic_tstat"].abs() >= 2).mean()),
                mean_stability=float(gs["rolling_ic_stability"].mean()),
                pct_weak_ic_below_0p02=float((gs["rolling_ic_abs_strength"] < 0.02).mean()),
            ))
        for day, gd in valid.groupby("day"):
            if len(gd) < 10:
                continue
            rows.append(dict(
                feature=feat, horizon=h, breakdown="day", breakdown_value=str(int(day)),
                n_bars=len(gd), mean_ic=float(gd["rolling_ic"].mean()),
                std_ic=float(gd["rolling_ic"].std()),
                pct_positive_sign=float((gd["rolling_ic_sign"] > 0).mean()),
                pct_negative_sign=float((gd["rolling_ic_sign"] < 0).mean()),
                mean_abs_ic=float(gd["rolling_ic_abs_strength"].mean()),
                mean_tstat=float(gd["rolling_ic_tstat"].mean()),
                pct_tstat_significant=float((gd["rolling_ic_tstat"].abs() >= 2).mean()),
                mean_stability=float(gd["rolling_ic_stability"].mean()),
                pct_weak_ic_below_0p02=float((gd["rolling_ic_abs_strength"] < 0.02).mean()),
            ))

    report = pd.DataFrame(rows)
    report.to_csv(v2.OUT_DIR / "feature_reliability_report.csv", index=False)

    # ── sign-change-by-session detector (uses the model's primary horizon) ──
    primary_h = v2.load_config()["rolling_ic"]["meta_model_primary_horizon"]
    sess_view = report[(report["breakdown"] == "session") & (report["horizon"] == primary_h)]
    v2.log(f"  mean_ic by feature x session, H={primary_h} (primary regime horizon):")
    pivot = sess_view.pivot_table(index="feature", columns="breakdown_value", values="mean_ic")
    print(pivot.round(4).to_string())

    sign_changes = []
    for feat, row in pivot.iterrows():
        signs = np.sign(row.dropna())
        if signs.nunique() > 1:
            sign_changes.append(feat)
    v2.log(f"  FEATURES THAT CHANGE SIGN ACROSS SESSIONS (H={primary_h}): {sign_changes}")

    # ── "should be ignored when IC is weak" detector ──
    all_view = report[(report["breakdown"] == "ALL") & (report["horizon"] == primary_h)]
    weak_features = all_view[all_view["mean_abs_ic"] < 0.02]["feature"].tolist()
    v2.log(f"  FEATURES WITH WEAK MEAN |IC| (<0.02) AT H={primary_h} (candidates to down-weight/ignore "
          f"when rolling_ic_abs_strength is low): {weak_features}")

    print(f"FEATURE_RELIABILITY_ROWS: {len(report)}")
    print(f"SIGN_CHANGING_FEATURES_H{primary_h}: {sign_changes}")
    print(f"WEAK_IC_FEATURES_H{primary_h}: {weak_features}")
    v2.log("03 complete.")
    return report


if __name__ == "__main__":
    main()
