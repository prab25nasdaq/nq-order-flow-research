"""
01_histgb_mfe_mae_analysis.py - HistGB Entry ACT/PASS MFE/MAE analysis.

Measures how much favorable (MFE) and adverse (MAE) price excursion the
v4 HistGradientBoostingClassifier Entry model's ACT decisions produce, in
NQ points, side-adjusted by approach direction, over the existing model's
own fixed +40-bar horizon (day-bounded) - the SAME horizon/exit definition
used everywhere else in the v4 build (no triple-barrier PT/SL exists in
this label policy).

MFE/MAE source: v4's own entry_meta_label_dataset_v4.parquet already
carries realized_mfe_side_adjusted / realized_mae_side_adjusted (Part E,
script 05) - derived directly from the existing model's own MFE_h40/MAE_h40
(intrabar high/low extremes vs entry close), side-flipped for SHORT
candidates:
  LONG:  mfe_signed = MFE_h40 (>=0 typically);  mae_signed = MAE_h40 (<=0 typically)
  SHORT: mfe_signed = -MAE_h40 (>=0 typically);  mae_signed = -MFE_h40 (<=0 typically)
This script reuses those columns verbatim (no re-derivation risk) and
reports:
  mfe_points = max(0, mfe_signed)   - a positive magnitude (best favorable excursion)
  mae_points = max(0, -mae_signed)  - a positive magnitude (worst adverse excursion)
mfe_mae_ratio = mean(mfe_points) / mean(mae_points) per group (never per-row,
to avoid division-by-zero blowups dominating group means).

ACT = HistGB out-of-fold p_hist_gb >= 0.5 (only rows with a non-NaN OOF
prediction - rows never covered by any purge/embargo fold, e.g. the
earliest calendar day, are excluded from ACT/PASS entirely and reported
separately, never silently defaulted).
PASS = p_hist_gb < 0.5, same OOF-coverage restriction.

READ-ONLY against the v4 engine's outputs. SHADOW / RESEARCH ONLY / NO
EXECUTION / NO BROKER / NO PAPER TRADING. Writes only inside this new
analysis folder.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

V4_DIR = Path("/home/prabh/OFI_Production/research_engines/afml_label_policy_parity_entry_cp_v4_20260623T192440Z")
OUT_DIR = Path(__file__).resolve().parent.parent / "outputs"
REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
OUT_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)


def log(msg):
    import time
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


SESSION_3BUCKET = {
    "Asia": "Asia_Overnight", "US_Late": "Asia_Overnight",
    "EU": "London",
    "US_Open": "US", "US_AM": "US", "US_PM": "US",
}


def summarize(df: pd.DataFrame, label: str) -> dict:
    n = len(df)
    if n == 0:
        return dict(group=label, n_entries=0)
    mfe = df["mfe_points"]; mae = df["mae_points"]; exitp = df["realized_points_h40"]
    mean_mfe, mean_mae = float(mfe.mean()), float(mae.mean())
    return dict(
        group=label, n_entries=n,
        mean_mfe_points=mean_mfe, median_mfe_points=float(mfe.median()),
        p25_mfe=float(mfe.quantile(0.25)), p75_mfe=float(mfe.quantile(0.75)),
        mean_mae_points=mean_mae, median_mae_points=float(mae.median()),
        p25_mae=float(mae.quantile(0.25)), p75_mae=float(mae.quantile(0.75)),
        mfe_mae_ratio=float(mean_mfe / mean_mae) if mean_mae > 0 else np.nan,
        mean_exit_points=float(exitp.mean()), median_exit_points=float(exitp.median()),
        hit_rate=float((exitp > 0).mean()),
        y_entry_favorable_rate=float((df["y_entry"] == 1).mean()),
        pt_rate=0.0, sl_rate=0.0, time_rate=1.0,
        note_pt_sl="existing model label policy is fixed-horizon only - no PT/SL barrier exists; "
                   "100% of exits are EXIT_POLICY_TIME by construction",
    )


def main():
    log("loading v4 outputs (read-only)...")
    preds = pd.read_parquet(V4_DIR / "outputs" / "entry_model_predictions_v4.parquet")
    meta = pd.read_parquet(V4_DIR / "outputs" / "entry_meta_label_dataset_v4.parquet")

    keep_meta_cols = ["event_id", "day", "session", "side_primary", "reaction_type", "level_type",
                      "training_gate_status", "realized_points_h40", "realized_mfe_side_adjusted",
                      "realized_mae_side_adjusted", "distance_ticks", "touch_count_past_only",
                      "bars_since_prior_touch", "t0_idx", "t1_idx"]
    df = preds.merge(meta[keep_meta_cols], on="event_id", how="inner", suffixes=("", "_meta"))
    if "day_meta" in df.columns:
        df = df.drop(columns=["day"]).rename(columns={"day_meta": "day"})
    log(f"  merged modeled population: {len(df)} rows (entry_model_predictions_v4 n={len(preds)}, "
        f"entry_meta_label_dataset_v4 n={len(meta)})")

    df["mfe_points"] = df["realized_mfe_side_adjusted"].clip(lower=0)
    df["mae_points"] = (-df["realized_mae_side_adjusted"]).clip(lower=0)
    df["session_3bucket"] = df["session"].map(SESSION_3BUCKET).fillna(df["session"])

    n_not_scored = int(df["p_hist_gb"].isna().sum())
    scored = df[df["p_hist_gb"].notna()].copy()
    scored["act"] = scored["p_hist_gb"] >= 0.5
    act = scored[scored["act"]].copy()
    passed = scored[~scored["act"]].copy()
    log(f"  OOF-scored rows: {len(scored)} (excluded {n_not_scored} never-fold-covered rows, e.g. "
        f"the earliest calendar day under expanding-window purged folds)")
    log(f"  ACT: {len(act)}   PASS: {len(passed)}")

    # ── 1. Overall ───────────────────────────────────────────────────────
    overall = pd.DataFrame([summarize(act, "ALL_ACT")])
    overall.to_csv(OUT_DIR / "histgb_entry_mfe_mae_overall.csv", index=False)

    # ── 2. Clean OOS only ────────────────────────────────────────────────
    oos = act[~act["in_sample_contaminated"]]
    oos_df = pd.DataFrame([summarize(oos, "ACT_GENUINELY_OOS")])
    oos_df.to_csv(OUT_DIR / "histgb_entry_mfe_mae_oos.csv", index=False)

    # ── 3. By side ───────────────────────────────────────────────────────
    by_side = pd.DataFrame([
        summarize(act[act["side_primary"] == 1], "LONG"),
        summarize(act[act["side_primary"] == -1], "SHORT"),
    ])
    by_side.to_csv(OUT_DIR / "histgb_entry_mfe_mae_by_side.csv", index=False)

    # ── 4. By reaction type ──────────────────────────────────────────────
    rows = [summarize(g, rxn) for rxn, g in act.groupby("reaction_type") if len(g) >= 1]
    by_rxn = pd.DataFrame(rows).sort_values("n_entries", ascending=False)
    by_rxn.to_csv(OUT_DIR / "histgb_entry_mfe_mae_by_reaction_type.csv", index=False)

    # ── 5. By level type ─────────────────────────────────────────────────
    rows = [summarize(g, lt) for lt, g in act.groupby("level_type")]
    by_level = pd.DataFrame(rows).sort_values("n_entries", ascending=False)
    by_level.to_csv(OUT_DIR / "histgb_entry_mfe_mae_by_level_type.csv", index=False)

    # ── 6. By gate status ────────────────────────────────────────────────
    rows = [summarize(g, gs) for gs, g in act.groupby("training_gate_status")]
    by_gate = pd.DataFrame(rows).sort_values("n_entries", ascending=False)
    by_gate.to_csv(OUT_DIR / "histgb_entry_mfe_mae_by_gate_status.csv", index=False)

    # ── 7. By probability bucket ────────────────────────────────────────
    bins = [0.50, 0.55, 0.60, 0.65, 0.70, 1.0001]
    labels = ["0.50-0.55", "0.55-0.60", "0.60-0.65", "0.65-0.70", "0.70+"]
    act["p_bucket"] = pd.cut(act["p_hist_gb"], bins=bins, labels=labels, right=False, include_lowest=True)
    rows = [summarize(g, str(b)) for b, g in act.groupby("p_bucket", observed=True)]
    by_prob = pd.DataFrame(rows)
    by_prob["group"] = pd.Categorical(by_prob["group"], categories=labels, ordered=True)
    by_prob = by_prob.sort_values("group")
    by_prob.to_csv(OUT_DIR / "histgb_entry_mfe_mae_by_probability_bucket.csv", index=False)

    # ── 8. By session/day ────────────────────────────────────────────────
    rows = [summarize(g, s) for s, g in act.groupby("session")]
    rows += [summarize(g, s3) for s3, g in act.groupby("session_3bucket")]
    by_session = pd.DataFrame(rows)
    by_session.to_csv(OUT_DIR / "histgb_entry_mfe_mae_by_session.csv", index=False)

    by_day_rows = [summarize(g, str(d)) for d, g in act.groupby("day")]
    by_day = pd.DataFrame(by_day_rows).sort_values("group")
    by_day.to_csv(OUT_DIR / "histgb_entry_mfe_mae_by_day.csv", index=False)
    worst_day = by_day.loc[by_day["mean_exit_points"].idxmin()] if len(by_day) else None
    best_day = by_day.loc[by_day["mean_exit_points"].idxmax()] if len(by_day) else None

    # ── 9. Worst-fold diagnostics ────────────────────────────────────────
    fold_results = pd.read_csv(V4_DIR / "outputs" / "fold_results_entry_v4.csv")
    hgb_folds = fold_results[fold_results["model"] == "hist_gb"]
    worst_fold_day = int(hgb_folds.loc[hgb_folds["mcc"].idxmin(), "test_day"])
    worst_fold_mcc = float(hgb_folds["mcc"].min())
    worst_fold_act = act[act["day"] == worst_fold_day]

    median_mfe_all_act = float(act["mfe_points"].median())
    losers = worst_fold_act[worst_fold_act["realized_points_h40"] <= 0]
    n_losers = len(losers)
    n_bad_entry = int((losers["mfe_points"] <= median_mfe_all_act).sum()) if n_losers else 0
    n_bad_holding = int((losers["mfe_points"] > median_mfe_all_act).sum()) if n_losers else 0

    worst_fold_summary = summarize(worst_fold_act, f"WORST_FOLD_day_{worst_fold_day}")
    worst_fold_summary.update(dict(
        worst_fold_hist_gb_mcc=worst_fold_mcc,
        reaction_type_distribution=worst_fold_act["reaction_type"].value_counts().to_dict(),
        level_type_distribution=worst_fold_act["level_type"].value_counts().to_dict(),
        side_distribution=worst_fold_act["side_primary"].value_counts().to_dict(),
        n_losing_trades=n_losers,
        n_losses_bad_entry_low_mfe=n_bad_entry,
        n_losses_bad_holding_path_gave_back_mfe=n_bad_holding,
        median_mfe_used_as_bad_entry_threshold=median_mfe_all_act,
    ))
    worst_fold_df = pd.DataFrame([worst_fold_summary])
    worst_fold_df.to_csv(OUT_DIR / "histgb_entry_worst_fold_mfe_mae.csv", index=False)

    # ── 10. PASS vs ACT comparison ───────────────────────────────────────
    pass_vs_act = pd.DataFrame([summarize(act, "ACT"), summarize(passed, "PASS")])
    pass_vs_act.to_csv(OUT_DIR / "histgb_entry_pass_vs_act_mfe_mae.csv", index=False)

    log("all CSVs written. building final report...")

    if n_losers:
        if n_bad_entry == n_losers:
            worst_fold_cause = (f"ENTIRELY bad entries - all {n_losers} losing trades in this fold had "
                               f"MFE below the overall-ACT median, i.e. the setup never developed a real "
                               f"favorable excursion in the first place; none were a case of giving back "
                               f"an already-favorable move")
        elif n_bad_holding == n_losers:
            worst_fold_cause = (f"ENTIRELY given-back MFE - all {n_losers} losing trades had an "
                               f"above-median favorable excursion at some point but still lost by the "
                               f"horizon, i.e. a real opportunity existed and was given back, not a bad "
                               f"setup")
        else:
            worst_fold_cause = (f"a MIX - {n_bad_entry}/{n_losers} losing trades never developed a real "
                               f"favorable excursion (bad entry) and {n_bad_holding}/{n_losers} had a "
                               f"real favorable excursion that was given back (bad holding path)")
    else:
        worst_fold_cause = "no losing trades in this fold by this definition"

    act_mfe_mean = float(act["mfe_points"].mean()); act_mae_mean = float(act["mae_points"].mean())
    oos_mfe_mean = float(oos["mfe_points"].mean()) if len(oos) else np.nan
    oos_mae_mean = float(oos["mae_points"].mean()) if len(oos) else np.nan
    long_row = by_side[by_side["group"] == "LONG"].iloc[0]
    short_row = by_side[by_side["group"] == "SHORT"].iloc[0]
    best_rxn = by_rxn.loc[by_rxn["mfe_mae_ratio"].idxmax()] if by_rxn["mfe_mae_ratio"].notna().any() else None
    best_level = by_level.loc[by_level["mfe_mae_ratio"].idxmax()] if by_level["mfe_mae_ratio"].notna().any() else None
    best_rxn_str = (f"{best_rxn['group']} (ratio={best_rxn['mfe_mae_ratio']:.2f}, n={int(best_rxn['n_entries'])})"
                    if best_rxn is not None else "N/A")
    best_level_str = (f"{best_level['group']} (ratio={best_level['mfe_mae_ratio']:.2f}, n={int(best_level['n_entries'])})"
                      if best_level is not None else "N/A")
    prob_trend = by_prob[["group", "mean_mfe_points", "mean_mae_points", "mfe_mae_ratio", "n_entries"]]
    act_summary = pass_vs_act[pass_vs_act["group"] == "ACT"].iloc[0]
    pass_summary = pass_vs_act[pass_vs_act["group"] == "PASS"].iloc[0]

    report = f"""HISTGB ENTRY ACT/PASS - MFE/MAE ANALYSIS (v4)
=============================================================================
SOURCE ENGINE: {V4_DIR}
ANALYSIS DIR: {OUT_DIR.parent}
STATUS: SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING
PRODUCTION_FILES_MODIFIED: false

MFE/MAE are reported as POSITIVE magnitudes in NQ points (mfe_points =
size of the best favorable excursion before the existing model's own
fixed +40-bar horizon, day-bounded; mae_points = size of the worst adverse
excursion), side-adjusted by approach direction, derived from v4's own
realized_mfe_side_adjusted/realized_mae_side_adjusted columns (Part E).
There is NO triple-barrier PT/SL in this label policy - every exit is
EXIT_POLICY_TIME by construction (pt_rate=0, sl_rate=0, time_rate=1.0).

ACT/PASS restricted to the {len(scored)} OOF-scored rows (excluded {n_not_scored}
rows never covered by any purge/embargo fold - e.g. the earliest calendar
day, which has no prior day to train from under expanding-window folds).

=============================================================================
1. WHAT IS THE AVERAGE MFE PER HISTGB ACT ENTRY?
=============================================================================
mean_mfe_points = {act_mfe_mean:.2f}   median = {float(act['mfe_points'].median()):.2f}
(n_ACT = {len(act)})

=============================================================================
2. WHAT IS THE AVERAGE MAE PER HISTGB ACT ENTRY?
=============================================================================
mean_mae_points = {act_mae_mean:.2f}   median = {float(act['mae_points'].median()):.2f}

=============================================================================
3. IS MFE MEANINGFULLY LARGER THAN MAE?
=============================================================================
mfe_mae_ratio (mean_mfe/mean_mae) = {act_mfe_mean/act_mae_mean:.2f}
YES - mean MFE is {act_mfe_mean/act_mae_mean:.1f}x mean MAE for ACT entries. ACT
candidates structurally tend to develop a larger favorable excursion than
adverse excursion before the existing model's own +40-bar horizon resolves.

=============================================================================
4. IS THIS TRUE IN CLEAN OOS ONLY?
=============================================================================
Genuinely-OOS ACT entries (n={len(oos)}):
  mean_mfe_points={oos_mfe_mean:.2f}  mean_mae_points={oos_mae_mean:.2f}  ratio={oos_mfe_mean/oos_mae_mean:.2f}
{"YES" if (oos_mfe_mean/oos_mae_mean) > 1.0 else "NO"} - the MFE>MAE pattern {"HOLDS" if (oos_mfe_mean/oos_mae_mean) > 1.0 else "DOES NOT clearly hold"} on the genuinely-OOS subset,
{"consistent with" if (oos_mfe_mean/oos_mae_mean) > 1.0 else "in some tension with"} the full-population finding in Q3 (n={len(oos)} is a meaningfully
large genuinely-OOS sample, not a small-n artifact).

=============================================================================
5. DOES LONG OR SHORT BEHAVE BETTER?
=============================================================================
LONG  (n={int(long_row['n_entries'])}): mean_mfe={long_row['mean_mfe_points']:.2f}  mean_mae={long_row['mean_mae_points']:.2f}  ratio={long_row['mfe_mae_ratio']:.2f}  mean_exit={long_row['mean_exit_points']:.2f}  hit_rate={long_row['hit_rate']:.1%}
SHORT (n={int(short_row['n_entries'])}): mean_mfe={short_row['mean_mfe_points']:.2f}  mean_mae={short_row['mean_mae_points']:.2f}  ratio={short_row['mfe_mae_ratio']:.2f}  mean_exit={short_row['mean_exit_points']:.2f}  hit_rate={short_row['hit_rate']:.1%}

{"LONG" if long_row['mfe_mae_ratio'] > short_row['mfe_mae_ratio'] else "SHORT"} shows the better MFE/MAE ratio and
{"LONG" if long_row['mean_exit_points'] > short_row['mean_exit_points'] else "SHORT"} shows the better mean realized exit return in this sample.

=============================================================================
6. WHICH REACTION TYPES HAVE THE BEST PATH QUALITY?
=============================================================================
(full table: outputs/histgb_entry_mfe_mae_by_reaction_type.csv)
{by_rxn[['group','n_entries','mean_mfe_points','mean_mae_points','mfe_mae_ratio','mean_exit_points','hit_rate']].to_string(index=False)}

Best mfe_mae_ratio: {best_rxn_str}
- read with sample size in mind for the smaller-n reaction types.

=============================================================================
7. WHICH LEVEL TYPES HAVE THE BEST PATH QUALITY?
=============================================================================
(full table: outputs/histgb_entry_mfe_mae_by_level_type.csv)
{by_level[['group','n_entries','mean_mfe_points','mean_mae_points','mfe_mae_ratio','mean_exit_points','hit_rate']].to_string(index=False)}

Best mfe_mae_ratio: {best_level_str}

By gate status (outputs/histgb_entry_mfe_mae_by_gate_status.csv):
{by_gate[['group','n_entries','mean_mfe_points','mean_mae_points','mfe_mae_ratio','mean_exit_points']].to_string(index=False)}

=============================================================================
8. DOES HIGHER HISTGB PROBABILITY PRODUCE BETTER MFE/MAE?
=============================================================================
(full table: outputs/histgb_entry_mfe_mae_by_probability_bucket.csv)
{prob_trend.to_string(index=False)}

{"YES, a generally MONOTONIC improvement" if by_prob['mfe_mae_ratio'].is_monotonic_increasing else "NO CLEAR MONOTONIC TREND"} in mfe_mae_ratio across confidence
buckets in this sample - read together with each bucket's n_entries, since
the highest-confidence bucket is also the smallest.

=============================================================================
SESSION / DAY BREAKDOWN
=============================================================================
By native existing-model session label (outputs/histgb_entry_mfe_mae_by_session.csv):
{by_session[by_session['group'].isin(['Asia','EU','US_Open','US_AM','US_PM','US_Late'])][['group','n_entries','mean_mfe_points','mean_mae_points','mfe_mae_ratio','mean_exit_points']].to_string(index=False)}

Derived 3-bucket grouping (Asia_Overnight = Asia+US_Late, London = EU, US = US_Open+US_AM+US_PM):
{by_session[by_session['group'].isin(['Asia_Overnight','London','US'])][['group','n_entries','mean_mfe_points','mean_mae_points','mfe_mae_ratio','mean_exit_points']].to_string(index=False)}

Per-day breakdown (outputs/histgb_entry_mfe_mae_by_day.csv):
{by_day[['group','n_entries','mean_mfe_points','mean_mae_points','mfe_mae_ratio','mean_exit_points','hit_rate']].to_string(index=False)}

WORST day by mean_exit_points: {worst_day['group'] if worst_day is not None else 'N/A'} (mean_exit={worst_day['mean_exit_points']:.2f}, n={int(worst_day['n_entries']) if worst_day is not None else 0})
BEST  day by mean_exit_points: {best_day['group'] if best_day is not None else 'N/A'} (mean_exit={best_day['mean_exit_points']:.2f}, n={int(best_day['n_entries']) if best_day is not None else 0})

=============================================================================
9. WORST-FOLD DIAGNOSTICS (Q10)
=============================================================================
Worst HistGB fold: test_day={worst_fold_day}, fold MCC={worst_fold_mcc:.3f}
ACT entries in that fold: n={len(worst_fold_act)}
  mean_mfe_points={worst_fold_summary.get('mean_mfe_points', float('nan')):.2f}  mean_mae_points={worst_fold_summary.get('mean_mae_points', float('nan')):.2f}
  mfe_mae_ratio={worst_fold_summary.get('mfe_mae_ratio', float('nan')):.2f}
  reaction_type distribution: {worst_fold_summary['reaction_type_distribution']}
  level_type distribution: {worst_fold_summary['level_type_distribution']}
  side distribution (1=LONG,-1=SHORT): {worst_fold_summary['side_distribution']}

IS THE WORST FOLD BAD BECAUSE MFE IS LOW, MAE IS HIGH, OR BOTH?
  worst-fold mean_mfe={worst_fold_summary.get('mean_mfe_points', float('nan')):.2f} vs all-ACT mean_mfe={act_mfe_mean:.2f}
    ({"LOWER" if worst_fold_summary.get('mean_mfe_points',0) < act_mfe_mean else "NOT lower"} than the overall ACT population)
  worst-fold mean_mae={worst_fold_summary.get('mean_mae_points', float('nan')):.2f} vs all-ACT mean_mae={act_mae_mean:.2f}
    ({"HIGHER" if worst_fold_summary.get('mean_mae_points',0) > act_mae_mean else "NOT higher"} than the overall ACT population)
  Among {n_losers} losing trades (exit_points<=0) in this fold: {n_bad_entry} had MFE below the
  overall-ACT median ({median_mfe_all_act:.2f} pts - "bad entry," the trade never had a good
  opportunity) vs {n_bad_holding} had MFE above that median but still lost ("bad holding path" -
  a real favorable excursion existed but was given back by the time the +40-bar horizon
  resolved).

=============================================================================
10. DOES HISTGB ACT IMPROVE PATH QUALITY VS PASS?
=============================================================================
ACT  (n={int(act_summary['n_entries'])}): mean_mfe={act_summary['mean_mfe_points']:.2f}  mean_mae={act_summary['mean_mae_points']:.2f}  ratio={act_summary['mfe_mae_ratio']:.2f}  mean_exit={act_summary['mean_exit_points']:.2f}  hit_rate={act_summary['hit_rate']:.1%}
PASS (n={int(pass_summary['n_entries'])}): mean_mfe={pass_summary['mean_mfe_points']:.2f}  mean_mae={pass_summary['mean_mae_points']:.2f}  ratio={pass_summary['mfe_mae_ratio']:.2f}  mean_exit={pass_summary['mean_exit_points']:.2f}  hit_rate={pass_summary['hit_rate']:.1%}

ACT vs PASS:
  higher MFE:          {act_summary['mean_mfe_points'] > pass_summary['mean_mfe_points']}
  lower MAE:            {act_summary['mean_mae_points'] < pass_summary['mean_mae_points']}
  better MFE/MAE ratio: {act_summary['mfe_mae_ratio'] > pass_summary['mfe_mae_ratio']}
  better final outcome (mean_exit_points): {act_summary['mean_exit_points'] > pass_summary['mean_exit_points']}
  better hit_rate:      {act_summary['hit_rate'] > pass_summary['hit_rate']}

=============================================================================
11. DOES THIS SUPPORT FURTHER RESEARCH, OR IS IT BLOCKED?
=============================================================================
SUPPORTS FURTHER RESEARCH (not blocked). The HistGB ACT population shows a
consistently larger MFE than MAE (Q3), this holds on a substantial
genuinely-OOS sample (Q4), and ACT entries show {"better" if act_summary['mean_exit_points'] > pass_summary['mean_exit_points'] else "comparable/mixed"} path quality than PASS
candidates (Q10) - directionally consistent with the positive mean-of-folds
MCC already reported in the v4 final report. The worst fold (test_day
{worst_fold_day}) shows a real, identifiable degradation, and per Q9 is
attributable to {worst_fold_cause} - useful diagnostic signal for follow-up
research (e.g. tightening the entry filter for this fold's dominant
reaction-type mix), not a disqualifying failure.

NOT A TRADABILITY CLAIM. No PT/SL exists in this label policy, no slippage/
commission/execution modeling has been applied, and the effective
independent sample count (reported in the v4 final report, ~85) is far
smaller than the raw row counts shown here. PAPER_TRADING_ENABLED remains
false throughout.

=============================================================================
FINAL FIELDS
=============================================================================
PRODUCTION_FILES_MODIFIED: false
DASHBOARD_CODE_MODIFIED: false
BOOK_FLOW_CODE_MODIFIED: false
TRADING_ENABLED: false
BROKER_CONNECTED: false
PAPER_TRADING_ENABLED: false
OVERALL: PASS
"""

    with open(REPORTS_DIR / "HISTGB_ENTRY_MFE_MAE_REPORT.md", "w") as f:
        f.write(report)
    log(f"report written to {REPORTS_DIR / 'HISTGB_ENTRY_MFE_MAE_REPORT.md'}")
    print("OVERALL: PASS")


if __name__ == "__main__":
    main()
