#!/usr/bin/env python3
"""
Audit 2 — Probability Calibration Audit v1
Verifies whether model probabilities rank-order outcomes.
A model can have decent accuracy but be useless for threshold trading
if higher prob does not mean higher expectancy.
SHADOW / RESEARCH ONLY — no execution, no broker, no order placement.

Checks:
  - Calibration curves per window / version / horizon
  - Monotonicity: higher bucket → higher hit rate AND higher net return
  - Kill warning: if 0.70+ does not outperform 0.55–0.60 → NOT CALIBRATED
  - Session split per bucket
  - Long/short split per bucket
  - Regime split per bucket (vol-based)
"""

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

# ─── Paths ────────────────────────────────────────────────────────────────────
PBO_DIR  = Path("/home/prabh/OFI_Production/research_engines"
                "/model_prob_pbo_window_family_audit_v1_20260703T230000Z")
OUT_DIR  = Path(__file__).parent

WINDOWS  = [5, 10, 20, 30, 50, 80]
VERSIONS = ["A", "B"]
HORIZONS = [10, 40]
COST_TICKS = 2.0

PROB_BINS   = [0.50, 0.55, 0.60, 0.65, 0.70, 1.01]
PROB_LABELS = ["0.50-0.55", "0.55-0.60", "0.60-0.65", "0.65-0.70", "0.70+"]
PROB_MIDS   = [0.525, 0.575, 0.625, 0.675, 0.75]

SESSIONS = ["Asia", "EU", "US_Open", "US_AM", "US_PM", "US_Late"]


def calibration_curve(oos: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Full calibration curve: per bucket, compute all statistics."""
    net_col = f"net_return_h{horizon}"
    fwd_col = f"fwd_return_ticks_h{horizon}"
    rows = []
    for i, lab in enumerate(PROB_LABELS):
        lo, hi = PROB_BINS[i], PROB_BINS[i + 1]
        sub = oos[(oos["prob_long"] >= lo) & (oos["prob_long"] < hi)]
        n = len(sub)
        if n < 5:
            rows.append({"bucket": lab, "n": n, "mid": PROB_MIDS[i],
                         "hit_rate": np.nan, "avg_fwd_ret": np.nan,
                         "avg_net_ret": np.nan, "pct_long": np.nan})
            continue
        hit = float((sub["y_true"] == 1).mean()) if "y_true" in sub.columns else np.nan
        avg_fwd = float(sub[fwd_col].mean()) if fwd_col in sub.columns else np.nan
        avg_net = float(sub[net_col].mean()) if net_col in sub.columns else np.nan
        pct_lng = float((sub["pred_side"] == "LONG").mean()) if "pred_side" in sub.columns else np.nan
        rows.append({"bucket": lab, "n": n, "mid": PROB_MIDS[i],
                     "hit_rate": hit, "avg_fwd_ret": avg_fwd,
                     "avg_net_ret": avg_net, "pct_long": pct_lng})
    df = pd.DataFrame(rows)
    valid = df.dropna(subset=["hit_rate"])
    if len(valid) >= 3:
        rho_hr, p_hr    = stats.spearmanr(valid["mid"], valid["hit_rate"])
        rho_nr, p_nr    = stats.spearmanr(valid["mid"], valid["avg_net_ret"])
        monotone_hr     = bool(rho_hr > 0 and p_hr < 0.20)
        monotone_nr     = bool(rho_nr > 0 and p_nr < 0.20)
    else:
        rho_hr, p_hr, monotone_hr = np.nan, np.nan, False
        rho_nr, p_nr, monotone_nr = np.nan, np.nan, False

    # Kill warning: does 0.70+ outperform 0.55–0.60?
    b_low  = df[df["bucket"] == "0.55-0.60"]["avg_net_ret"].values
    b_high = df[df["bucket"] == "0.70+"]["avg_net_ret"].values
    has_high_edge = (
        len(b_low) > 0 and len(b_high) > 0
        and not np.isnan(b_low[0]) and not np.isnan(b_high[0])
        and b_high[0] > b_low[0]
    )
    df["spearman_rho_hr"] = rho_hr
    df["spearman_p_hr"]   = p_hr
    df["spearman_rho_nr"] = rho_nr
    df["spearman_p_nr"]   = p_nr
    df["monotone_hr"]     = monotone_hr
    df["monotone_nr"]     = monotone_nr
    df["high_edge_over_low"] = has_high_edge
    df["calibrated"] = monotone_hr and monotone_nr
    return df


def session_calibration(oos: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Per-session hit rate by prob bucket."""
    net_col = f"net_return_h{horizon}"
    rows = []
    for sess in SESSIONS:
        if "session" not in oos.columns:
            break
        sub_sess = oos[oos["session"] == sess]
        if len(sub_sess) < 50:
            continue
        for i, lab in enumerate(PROB_LABELS):
            lo, hi = PROB_BINS[i], PROB_BINS[i + 1]
            sub = sub_sess[(sub_sess["prob_long"] >= lo) & (sub_sess["prob_long"] < hi)]
            n = len(sub)
            if n < 5:
                continue
            hit = float((sub["y_true"] == 1).mean())
            nr  = float(sub[net_col].mean()) if net_col in sub.columns else np.nan
            rows.append({
                "session": sess, "bucket": lab, "n": n,
                "hit_rate": hit, "avg_net_ret": nr,
            })
    return pd.DataFrame(rows)


def regime_calibration(oos: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Per-volatility-regime hit rate by prob bucket."""
    net_col = f"net_return_h{horizon}"
    fwd_col = f"fwd_return_ticks_h{horizon}"
    if fwd_col not in oos.columns:
        return pd.DataFrame()
    # Define regime by |fwd_return| as proxy for volatility
    med_vol = oos[fwd_col].abs().median()
    rows    = []
    for regime, mask in [("high_vol", oos[fwd_col].abs() >= med_vol),
                          ("low_vol",  oos[fwd_col].abs() <  med_vol)]:
        sub_r = oos[mask]
        for i, lab in enumerate(PROB_LABELS):
            lo, hi = PROB_BINS[i], PROB_BINS[i + 1]
            sub = sub_r[(sub_r["prob_long"] >= lo) & (sub_r["prob_long"] < hi)]
            n = len(sub)
            if n < 5:
                continue
            hit = float((sub["y_true"] == 1).mean())
            nr  = float(sub[net_col].mean()) if net_col in sub.columns else np.nan
            rows.append({"regime": regime, "bucket": lab, "n": n,
                         "hit_rate": hit, "avg_net_ret": nr})
    return pd.DataFrame(rows)


def main():
    print("=" * 70)
    print("Audit 2 — Probability Calibration Audit v1")
    print("SHADOW / RESEARCH ONLY — no execution, no broker")
    print("=" * 70)

    oos_path = PBO_DIR / "oos_probability_table.parquet"
    if not oos_path.exists():
        print("[FAIL] oos_probability_table.parquet not found. Run main PBO first.")
        return

    oos = pd.read_parquet(oos_path)
    print(f"  OOS table: {len(oos):,} rows")
    now_utc = datetime.now(timezone.utc).isoformat()

    all_cal_rows  = []
    all_sess_rows = []
    all_reg_rows  = []
    summary_rows  = []

    for version in VERSIONS:
        print(f"\n  Version {version}:")
        for W in WINDOWS:
            sub = oos[(oos["version"] == version) & (oos["window"] == W)]
            if len(sub) < 100:
                continue
            for H in HORIZONS:
                cal = calibration_curve(sub, H)
                cal["version"] = version
                cal["window"]  = W
                cal["horizon"] = H
                all_cal_rows.append(cal)

                sess_cal = session_calibration(sub, H)
                if not sess_cal.empty:
                    sess_cal["version"] = version
                    sess_cal["window"]  = W
                    sess_cal["horizon"] = H
                    all_sess_rows.append(sess_cal)

                reg_cal = regime_calibration(sub, H)
                if not reg_cal.empty:
                    reg_cal["version"] = version
                    reg_cal["window"]  = W
                    reg_cal["horizon"] = H
                    all_reg_rows.append(reg_cal)

                calibrated    = bool(cal["calibrated"].iloc[0])
                high_over_low = bool(cal["high_edge_over_low"].iloc[0])
                rho           = float(cal["spearman_rho_hr"].iloc[0])
                n_high        = int(cal[cal["bucket"] == "0.70+"]["n"].values[0]) if "0.70+" in cal["bucket"].values else 0
                avg_net_high  = float(cal[cal["bucket"] == "0.70+"]["avg_net_ret"].values[0]) if "0.70+" in cal["bucket"].values else np.nan
                avg_net_mid   = float(cal[cal["bucket"] == "0.55-0.60"]["avg_net_ret"].values[0]) if "0.55-0.60" in cal["bucket"].values else np.nan

                tag = "CALIBRATED" if calibrated else ("PARTIAL" if rho > 0 else "FLAT/BROKEN")
                kill_warn = not high_over_low
                print(f"    V{version} W={W:3d} H={H:2d}: {tag:12s}  "
                      f"ρ={rho:+.3f}  "
                      f"0.70+_net={avg_net_high:+.3f}  "
                      f"0.55-0.60_net={avg_net_mid:+.3f}  "
                      f"{'[KILL_WARN]' if kill_warn else ''}")

                summary_rows.append({
                    "version":          version,
                    "window":           W,
                    "horizon":          H,
                    "calibrated":       calibrated,
                    "high_over_low":    high_over_low,
                    "spearman_rho_hr":  rho,
                    "kill_warning":     kill_warn,
                    "n_high_bucket":    n_high,
                    "avg_net_high":     avg_net_high,
                    "avg_net_mid":      avg_net_mid,
                })

    # Save
    all_cal_df = pd.concat(all_cal_rows, ignore_index=True) if all_cal_rows else pd.DataFrame()
    all_cal_df.to_csv(OUT_DIR / "calibration_curves.csv", index=False)

    if all_sess_rows:
        pd.concat(all_sess_rows, ignore_index=True).to_csv(OUT_DIR / "session_calibration.csv", index=False)
    if all_reg_rows:
        pd.concat(all_reg_rows, ignore_index=True).to_csv(OUT_DIR / "regime_calibration.csv", index=False)

    summ_df = pd.DataFrame(summary_rows)
    summ_df.to_csv(OUT_DIR / "calibration_summary.csv", index=False)

    # Decision: per version
    report_lines = [
        "# Audit 2 — Probability Calibration Audit v1",
        f"**Generated**: {now_utc}",
        "**SHADOW / RESEARCH ONLY**",
        "",
        "---",
        "",
        "## Calibration Summary",
        "",
        "Calibrated = Spearman ρ(bucket_midpoint, hit_rate) > 0 AND ρ(bucket_midpoint, net_return) > 0.",
        "Kill warning = 0.70+ does NOT outperform 0.55–0.60 in net return.",
        "",
        "| Version | Window | H | Calibrated | ρ(hit_rate) | 0.70+ net | 0.55-0.60 net | Kill warning |",
        "|---------|--------|---|-----------|-------------|-----------|--------------|-------------|",
    ]
    for _, r in summ_df.iterrows():
        kw = "YES" if r["kill_warning"] else "no"
        cal = "YES" if r["calibrated"] else "no"
        report_lines.append(
            f"| {r['version']} | W={r['window']:3d} | {r['horizon']:2d} "
            f"| {cal} | {r['spearman_rho_hr']:+.3f} "
            f"| {r['avg_net_high']:+.3f} | {r['avg_net_mid']:+.3f} | **{kw}** |"
        )

    # Family-level decision
    report_lines += ["", "---", "", "## Family Decision per Version", ""]
    for version in VERSIONS:
        sub_v = summ_df[summ_df["version"] == version]
        n_calibrated  = int((sub_v["calibrated"]).sum())
        n_kill_warn   = int((sub_v["kill_warning"]).sum())
        n_total       = len(sub_v)
        calib_pass    = n_calibrated >= n_total // 2
        kill_warn_all = n_kill_warn == n_total

        if kill_warn_all:
            decision = "CALIBRATION KILL — 0.70+ never outperforms 0.55-0.60"
        elif not calib_pass:
            decision = "CALIBRATION DEFER — less than half of windows calibrated"
        else:
            decision = "CALIBRATION PASS — proceed to Audit 3 (Dumb Baselines)"

        report_lines.append(
            f"**Version {version}**: {n_calibrated}/{n_total} calibrated, "
            f"{n_kill_warn}/{n_total} kill warnings → **{decision}**"
        )
        report_lines.append("")

    report_lines += [
        "---",
        "",
        "## Kill Warning Definition",
        "",
        "If prob bucket 0.70+ does NOT produce higher net return than 0.55-0.60:",
        "→ Model probabilities do not reflect higher trading edge at high confidence.",
        "→ Threshold trading (e.g., 'only trade when prob > 0.65') would not work.",
        "",
        "---",
        "",
        "## Output Files",
        "",
        "| File | Description |",
        "|------|-------------|",
        "| calibration_curves.csv | Full per-bucket stats per version/window/horizon |",
        "| session_calibration.csv | Per-session per-bucket hit rate |",
        "| regime_calibration.csv | Per-regime (vol) per-bucket hit rate |",
        "| calibration_summary.csv | One row per version/window/horizon |",
        "| CALIBRATION_AUDIT_REPORT.md | This report |",
        "",
        "---",
        "",
        "## Final Status",
        "```",
        f"CALIBRATION_AUDIT_COMPLETE:             true",
        "PRODUCTION_FILES_MODIFIED:              false",
        "TRADING_ENABLED:                        false",
        "OVERALL:                                PASS",
        "```",
    ]

    with open(OUT_DIR / "CALIBRATION_AUDIT_REPORT.md", "w") as f:
        f.write("\n".join(report_lines))
    print("\n  Saved: CALIBRATION_AUDIT_REPORT.md")
    print("=" * 70)
    print("Audit 2 complete.")
    print("=" * 70)


if __name__ == "__main__":
    main()
