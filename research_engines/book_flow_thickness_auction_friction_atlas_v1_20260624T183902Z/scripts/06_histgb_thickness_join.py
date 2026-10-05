"""
06_histgb_thickness_join.py - Part F: HistGB ACT/PASS thickness attribution.

Joins this Atlas's thickness/friction panels with v4's own historical
candidate-event dataset (entry_meta_label_dataset_v4.parquet), batch-scored
through the v4 HistGB shadow-display-only daemon's already-reconstructed
model artifact (research_engines/.../inference_scripts/v4_histgb_entry_
shadow/model_candidate/ - loaded read-only via pickle, NEVER retrained or
modified here; same model + same batch-scoring approach already used and
validated in the LEVEL STATE tab's Part F historical validation).

ACT/PASS decision: ACT if v4_ACT_probability >= 0.5 (v4 daemon's own
threshold), else PASS. ACT winners = ACT & y_entry==1 (v4's own H40 label).
ACT losers = ACT & y_entry==0. PASS candidates = PASS regardless of
y_entry (v4 chose not to act on these).

READ-ONLY. Writes only histgb_act_pass_thickness_attribution.csv,
histgb_winners_losers_thickness_report.csv, and
bad_fold_20260617_thickness_diagnostic.csv under this engine's own outputs/.
"""
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import thickness_common as tc

cfg = tc.load_config()
TICK = tc.TICK_SIZE
WORST_DAY = cfg["worst_fold_day"]
V4_MODEL_PKL = tc.V4_MODEL_DIR / "model_hist_gb_shadow.pkl"
V4_SCALER_PKL = tc.V4_MODEL_DIR / "scaler_hist_gb_shadow.pkl"
V4_FEATURE_NAMES_JSON = tc.V4_MODEL_DIR / "feature_names.json"
V4_ACT_THRESHOLD = 0.5


def load_v4_model():
    if not (V4_MODEL_PKL.exists() and V4_SCALER_PKL.exists() and V4_FEATURE_NAMES_JSON.exists()):
        return None, None, None
    with open(V4_MODEL_PKL, "rb") as f:
        clf = pickle.load(f)
    with open(V4_SCALER_PKL, "rb") as f:
        scaler = pickle.load(f)
    feature_names = json.loads(V4_FEATURE_NAMES_JSON.read_text())
    return clf, scaler, feature_names


def batch_score_v4(df: pd.DataFrame, clf, scaler, feature_names) -> pd.Series:
    if clf is None or not feature_names:
        return pd.Series([None] * len(df), index=df.index)
    df = df.copy()
    for c in ("touch_count_past_only",):
        if c in df.columns:
            df[c] = df[c].fillna(0)
    for c in ("bars_since_prior_touch",):
        if c in df.columns:
            df[c] = df[c].fillna(-1)
    if "modelprob_bars_since_last_event" in feature_names and "modelprob_bars_since_last_event" not in df.columns:
        df["modelprob_bars_since_last_event"] = 0
    X = pd.DataFrame(index=df.index)
    for c in feature_names:
        X[c] = df[c] if c in df.columns else np.nan
    row_ok = X.notna().all(axis=1)
    proba = pd.Series([None] * len(df), index=df.index, dtype=object)
    if row_ok.any():
        Xs = scaler.transform(X.loc[row_ok].astype(float))
        p = clf.predict_proba(Xs)[:, 1]
        proba.loc[row_ok] = p
    return proba


def band_mean(lookup, bar_ts, level_price, lo_ticks, hi_ticks, direction):
    arrs = lookup.get(bar_ts)
    if arrs is None:
        return np.nan
    prices, pcts = arrs
    if direction == "below":
        lo, hi = level_price - hi_ticks * TICK, level_price - lo_ticks * TICK
    else:
        lo, hi = level_price + lo_ticks * TICK, level_price + hi_ticks * TICK
    mask = (prices >= lo) & (prices <= hi)
    return float(np.nanmean(pcts[mask])) if mask.any() else np.nan


def at_level(lookup, bar_ts, level_price, tol_ticks=2):
    arrs = lookup.get(bar_ts)
    if arrs is None:
        return np.nan
    prices, pcts = arrs
    mask = np.abs(prices - level_price) <= tol_ticks * TICK
    return float(np.nanmean(pcts[mask])) if mask.any() else np.nan


def main():
    tc.log("06: joining v4 HistGB ACT/PASS candidates with thickness/friction...")
    events = tc.load_v4_entry_dataset()
    clf, scaler, feature_names = load_v4_model()
    tc.log(f"  v4 model loaded: {clf is not None}, n_features={len(feature_names) if feature_names else 0}")
    events["v4_ACT_probability"] = batch_score_v4(events, clf, scaler, feature_names)
    n_scored = events["v4_ACT_probability"].notna().sum()
    tc.log(f"  scored {n_scored}/{len(events)} candidates")

    events["v4_decision"] = np.where(events["v4_ACT_probability"].astype(float) >= V4_ACT_THRESHOLD, "ACT",
                                     np.where(events["v4_ACT_probability"].notna(), "PASS", "UNSCORED"))
    events["outcome_group"] = np.select(
        [(events["v4_decision"] == "ACT") & (events["y_entry"] == 1),
         (events["v4_decision"] == "ACT") & (events["y_entry"] == 0),
         events["v4_decision"] == "PASS"],
        ["ACT_WINNER", "ACT_LOSER", "PASS_CANDIDATE"], default="UNSCORED")

    thickness = pd.read_parquet(tc.OUT_DIR / "book_flow_thickness_panel.parquet")
    lookup = {ts: (g["price_level"].to_numpy(), g["thickness_percentile"].to_numpy())
              for ts, g in thickness.groupby("bar_end_ts_ns")}

    tc.log("  computing thickness at/above/below entry for each candidate...")
    events["thickness_at_entry"] = [at_level(lookup, ts, lp) for ts, lp in
                                    zip(events["bar_end_ts_ns"], events["nearest_level_price"])]
    events["thickness_above_entry"] = [band_mean(lookup, ts, lp, 4, 20, "above") for ts, lp in
                                       zip(events["bar_end_ts_ns"], events["nearest_level_price"])]
    events["thickness_below_entry"] = [band_mean(lookup, ts, lp, 4, 20, "below") for ts, lp in
                                       zip(events["bar_end_ts_ns"], events["nearest_level_price"])]

    friction = pd.read_parquet(tc.OUT_DIR / "auction_friction_features.parquet")[
        ["bar_end_ts_ns", "auction_friction_score", "liquidity_vacuum_score", "absorption_score",
         "two_way_exchange_score"]]
    events = events.merge(friction, on="bar_end_ts_ns", how="left")
    events["MFE"] = events["realized_mfe_side_adjusted"]
    events["MAE"] = events["realized_mae_side_adjusted"]
    events["mfe_mae_ratio"] = events["MFE"] / events["MAE"].abs().replace(0, np.nan)
    events["exit_result"] = np.where(events["y_entry"] == 1, "WIN", np.where(events["y_entry"] == 0, "LOSS", "UNKNOWN"))
    events["long_short"] = np.where(events["side_primary"] == 1, "LONG", "SHORT")

    def summarize(df, group_col):
        rows = []
        for g, sub in df.groupby(group_col, dropna=False):
            if len(sub) == 0:
                continue
            ratio_mean = sub["MFE"].mean() / abs(sub["MAE"].mean()) if sub["MAE"].mean() != 0 else np.nan
            rows.append(dict(
                group=str(g), n=len(sub),
                mean_thickness_at_entry=float(sub["thickness_at_entry"].mean()),
                mean_thickness_above_entry=float(sub["thickness_above_entry"].mean()),
                mean_thickness_below_entry=float(sub["thickness_below_entry"].mean()),
                mean_auction_friction_score=float(sub["auction_friction_score"].mean()),
                mean_liquidity_vacuum_score=float(sub["liquidity_vacuum_score"].mean()),
                mean_absorption_score=float(sub["absorption_score"].mean()),
                mean_MFE=float(sub["MFE"].mean()), mean_MAE=float(sub["MAE"].mean()),
                MFE_MAE_ratio_of_means=float(ratio_mean) if np.isfinite(ratio_mean) else None,
                pct_WIN=float((sub["exit_result"] == "WIN").mean()),
            ))
        return pd.DataFrame(rows)

    attribution = summarize(events, "outcome_group")
    out1 = tc.OUT_DIR / "histgb_act_pass_thickness_attribution.csv"
    attribution.to_csv(out1, index=False)
    tc.log(f"  wrote {out1}")

    # winners vs losers report: overall + by LONG/SHORT + by worst-fold-day vs normal
    act_only = events[events["outcome_group"].isin(["ACT_WINNER", "ACT_LOSER"])]
    wl_rows = []
    s = summarize(act_only, "outcome_group")
    s.insert(0, "breakdown", "overall_ACT")
    wl_rows.append(s)
    for side in ("LONG", "SHORT"):
        sub = act_only[act_only["long_short"] == side]
        s2 = summarize(sub, "outcome_group")
        s2.insert(0, "breakdown", f"{side}_ACT")
        wl_rows.append(s2)
    events["fold_label"] = np.where(events["day"] == WORST_DAY, "WORST_FOLD_20260617", "NORMAL_DAYS")
    for fold in ("WORST_FOLD_20260617", "NORMAL_DAYS"):
        sub = act_only[events.loc[act_only.index, "fold_label"] == fold]
        s3 = summarize(sub, "outcome_group")
        s3.insert(0, "breakdown", fold)
        wl_rows.append(s3)
    winners_losers = pd.concat(wl_rows, ignore_index=True)
    out2 = tc.OUT_DIR / "histgb_winners_losers_thickness_report.csv"
    winners_losers.to_csv(out2, index=False)
    tc.log(f"  wrote {out2}")

    # bad fold diagnostic: 2026-06-17 vs normal days, ALL outcome groups (not just ACT)
    bad_fold = events[events["day"] == WORST_DAY]
    normal_days = events[events["day"] != WORST_DAY]
    diag_rows = []
    for label, sub in [("WORST_FOLD_20260617", bad_fold), ("NORMAL_DAYS", normal_days)]:
        s = summarize(sub, "outcome_group")
        s.insert(0, "day_group", label)
        diag_rows.append(s)
    bad_fold_diag = pd.concat(diag_rows, ignore_index=True)
    out3 = tc.OUT_DIR / "bad_fold_20260617_thickness_diagnostic.csv"
    bad_fold_diag.to_csv(out3, index=False)
    tc.log(f"  wrote {out3}")

    print(attribution.to_string(index=False))
    print()
    print(bad_fold_diag.to_string(index=False))
    tc.log("06 complete.")
    return attribution, winners_losers, bad_fold_diag


if __name__ == "__main__":
    main()
