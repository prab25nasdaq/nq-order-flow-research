"""
07_explanatory_classifier.py - Part G: optional simple EXPLANATORY
classifier (never trading) for level-touch behavior.

CRITICAL leakage guard: features are restricted to PRE-touch windows ONLY
(pre_100/pre_50/pre_20/pre_10) - touch/post_5/post_10/post_20/post_40
windows are NEVER used as inputs, since those windows overlap the exact
forward path the behavior labels are derived from (using them would leak
the label into the features by construction).

Targets (each a separate binary classifier):
  rejection_vs_not, breakout_acceptance_vs_not, fake_breakout_vs_not,
  favorable_mfe_mae_vs_not (reject_final_return_40 > 0)

Dedup (v1-diagnostic lesson, lightweight version given time/scope): native
touches are thinned to the first touch of a recurring (day, level_type,
level_price) OR touches separated by >=40 bars from the prior touch of
that same level (bars_since_prior_touch==-1 or >=40) - ROLLING_*/
PRIOR_SESSION_* sources don't track a touch counter and are NOT thinned by
this filter (documented, smaller share of the sample).

AFML: average-uniqueness sample weights (40-bar window per event, global
bar index), purged/embargoed day-based folds, no random K-fold.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION. Explanatory only - never
used for trading.
"""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import atlas_common as ac

cfg = ac.load_config()

PRE_WINDOWS = ["pre_100", "pre_50", "pre_20", "pre_10"]
ONE_HOT_DIMS = ["level_type", "session", "vol_state", "vpin_state", "liquidity_cost_state", "side_of_approach"]
TARGETS = {
    "rejection_vs_not": lambda lbl: (lbl["behavior_label"] == "REJECTION").astype(float),
    "breakout_acceptance_vs_not": lambda lbl: (lbl["behavior_label"] == "BREAKOUT_ACCEPTANCE").astype(float),
    "fake_breakout_vs_not": lambda lbl: (lbl["behavior_label"] == "FAKE_BREAKOUT_SWEEP").astype(float),
}


def build_dataset():
    ev = pd.read_parquet(ac.OUT_DIR / "level_touch_events.parquet")
    lbl = pd.read_parquet(ac.OUT_DIR / "level_behavior_labels.parquet")
    mech = pd.read_parquet(ac.OUT_DIR / "level_touch_full_book_mechanics.parquet")
    mfe = pd.read_parquet(ac.OUT_DIR / "level_touch_mfe_mae.parquet")
    nqu6 = ac.load_nqu6_master()

    thinned = ev[(ev["bars_since_prior_touch"] == -1) | (ev["bars_since_prior_touch"] >= 40)].copy()
    ac.log(f"  lightweight dedup: {len(thinned)} / {len(ev)} events retained")

    pre_cols = [c for c in mech.columns if any(c.startswith(w + "__") for w in PRE_WINDOWS)]
    df = thinned.merge(mech[["event_id"] + pre_cols], on="event_id", how="left")
    df = df.merge(lbl[["event_id", "behavior_label"]], on="event_id", how="left")
    df = df.merge(mfe[["event_id", "reject_final_return_40"]], on="event_id", how="left")
    df["favorable_mfe_mae_vs_not"] = (df["reject_final_return_40"] > 0).astype(float)
    for name, fn in TARGETS.items():
        df[name] = fn(df)

    bar_idx_map = nqu6.reset_index().set_index("bar_end_ts_ns")["index"]
    df["global_bar_idx"] = df["bar_end_ts_ns"].map(bar_idx_map)
    df["day"] = df["rithmic_date_str"].str.replace("-", "").astype(int)
    df["t0_idx"] = df["global_bar_idx"]
    df["t1_idx"] = df["global_bar_idx"] + 40

    for dim in ONE_HOT_DIMS:
        for v in df[dim].dropna().unique():
            df[f"{dim}_{v}"] = (df[dim] == v).astype(int)

    return df, pre_cols


def select_feature_cols(df, pre_cols):
    onehot_cols = [c for c in df.columns for dim in ONE_HOT_DIMS if c.startswith(f"{dim}_") and
                  pd.api.types.is_numeric_dtype(df[c])]
    feats = [c for c in pre_cols if pd.api.types.is_numeric_dtype(df[c])]
    return sorted(set(feats + onehot_cols))


def fit_predict(model_name, X_train, y_train, sw_train, X_test):
    rs = cfg["random_seed"]
    if model_name == "logreg_baseline":
        clf = LogisticRegression(C=cfg["models"]["logreg_baseline"]["C"], max_iter=cfg["models"]["logreg_baseline"]["max_iter"], random_state=rs)
    elif model_name == "hist_gb":
        p = cfg["models"]["hist_gb"]
        clf = HistGradientBoostingClassifier(max_iter=p["max_iter"], max_depth=p["max_depth"], learning_rate=p["learning_rate"], random_state=rs)
    elif model_name == "random_forest":
        p = cfg["models"]["random_forest"]
        clf = RandomForestClassifier(n_estimators=p["n_estimators"], max_depth=p["max_depth"], min_samples_leaf=p["min_samples_leaf"], class_weight="balanced", random_state=rs)
    else:
        raise ValueError(model_name)
    try:
        clf.fit(X_train, y_train, sample_weight=sw_train)
    except TypeError:
        clf.fit(X_train, y_train)
    return clf, clf.predict_proba(X_test)[:, 1]


def main():
    ac.log("07: training explanatory behavior classifiers (pre-touch features only, never trading)...")
    df, pre_cols = build_dataset()
    feature_cols = select_feature_cols(df, pre_cols)
    feature_cols = [c for c in feature_cols if df[c].notna().mean() > 0.5]
    elig = df.dropna(subset=feature_cols + ["global_bar_idx"]).reset_index(drop=True)
    ac.log(f"  eligible rows: {len(elig)} / {len(df)}  ({len(feature_cols)} pre-touch + context features)")

    n_bars = int(ac.load_nqu6_master()["bar_index"].max()) + 1
    t0 = elig["t0_idx"].to_numpy(); t1 = elig["t1_idx"].to_numpy()
    c_t = ac.num_co_events(t0, t1, n_bars)
    elig["avg_uniqueness"] = ac.average_uniqueness(t0, t1, c_t)
    ac.log(f"  effective N: {elig['avg_uniqueness'].sum():.1f} / raw N {len(elig)}")

    folds = ac.purged_embargo_day_splits(elig, "day", "t0_idx", "t1_idx", cfg["validation"]["embargo_bars"])
    model_names = ["logreg_baseline", "hist_gb", "random_forest"]
    target_names = list(TARGETS.keys()) + ["favorable_mfe_mae_vs_not"]

    results = []
    importance_rows = []
    for target in target_names:
        oof_store = {m: np.full(len(elig), np.nan) for m in model_names}
        for fd in folds:
            if fd["n_train"] < cfg["validation"]["min_train_rows"] or fd["n_test"] < 1:
                continue
            train = elig.loc[fd["train_idx"]]; test = elig.loc[fd["test_idx"]]
            if train[target].nunique() < 2:
                continue
            scaler = StandardScaler()
            X_train = scaler.fit_transform(train[feature_cols]); X_test = scaler.transform(test[feature_cols])
            sw = train["avg_uniqueness"].to_numpy()
            for m in model_names:
                try:
                    clf, p_test = fit_predict(m, X_train, train[target].to_numpy(), sw, X_test)
                    oof_store[m][fd["test_idx"]] = p_test
                    if m == "hist_gb":
                        try:
                            from sklearn.inspection import permutation_importance
                            r = permutation_importance(clf, X_test, test[target].to_numpy(), n_repeats=3,
                                                       random_state=cfg["random_seed"], scoring="roc_auc", n_jobs=1)
                            for fi, fname in enumerate(feature_cols):
                                importance_rows.append(dict(target=target, test_day=fd["test_day"], feature=fname,
                                                            importance=float(r.importances_mean[fi])))
                        except Exception:
                            pass
                except Exception as e:
                    ac.log(f"    {target}/{m} failed on fold {fd['test_day']}: {e}")

        for m in model_names:
            p = oof_store[m]
            mask = np.isfinite(p) & np.isfinite(elig[target].to_numpy())
            if mask.sum() < 10:
                continue
            yt = elig[target].to_numpy()[mask]; pb = (p[mask] >= 0.5).astype(float)
            results.append(dict(target=target, model=m, n_oof=int(mask.sum()), mcc=ac.mcc_binary(yt, pb),
                                base_rate=float(yt.mean())))

    res_df = pd.DataFrame(results)
    res_df.to_csv(ac.OUT_DIR / "level_behavior_model_results.csv", index=False)

    imp_df = pd.DataFrame(importance_rows)
    if len(imp_df):
        imp_summary = imp_df.groupby(["target", "feature"])["importance"].mean().reset_index()
        imp_summary = imp_summary.sort_values(["target", "importance"], ascending=[True, False])
        imp_summary = imp_summary.groupby("target").head(15)
    else:
        imp_summary = pd.DataFrame(columns=["target", "feature", "importance"])
    imp_summary.to_csv(ac.OUT_DIR / "level_behavior_feature_importance.csv", index=False)

    print(res_df.to_string(index=False))
    ac.log("07 complete.")
    return res_df, imp_summary


if __name__ == "__main__":
    main()
