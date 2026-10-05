"""
07_explanatory_model.py - Part G: optional small explanatory models.

CRITICAL leakage guard: every feature used (thickness_at/above/below_level,
velocity_into_level, directional_velocity, flow/signed_flow_into_level,
bid/ask add/pull, the 6 auction-friction scores, one-hot level_type/
session/vol_state/vpin_state/side_of_approach) is computed from the
TOUCH BAR ITSELF ONLY (contemporaneous) - never from the forward window the
targets are derived from. Targets all depend on level_state_after_touch /
MFE_after_touch / MAE_after_touch, which look forward up to 40 bars - same
separation the prior Atlas's 07_explanatory_classifier.py already
established for its own targets.

AFML: average-uniqueness sample weights (40-bar forward window per event,
global bar index), purged/embargoed day-based folds (NOT random K-fold).
Reuses thickness_common.purged_embargo_day_splits/num_co_events/
average_uniqueness verbatim (identical formulas to the prior Atlas).

READ-ONLY. Explanatory only - NEVER used for trading. Writes only
thickness_explanatory_model_results.csv and
thickness_feature_importance.csv under this engine's own outputs/.
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
import thickness_common as tc

cfg = tc.load_config()
ONE_HOT_DIMS = ["level_type", "session", "vol_state", "vpin_state", "side_of_approach"]
BASE_FEATURES = [
    "thickness_at_level", "thickness_below_level", "thickness_above_level",
    "velocity_into_level", "directional_velocity", "flow_into_level", "signed_flow_into_level",
    "bid_add", "bid_pull", "ask_add", "ask_pull", "bid_add_vs_bid_pull", "ask_add_vs_ask_pull",
]
FRICTION_FEATURES = ["auction_friction_score", "liquidity_vacuum_score", "absorption_score",
                     "two_way_exchange_score", "replenishment_score", "imbalance_score"]


def build_dataset():
    sr = pd.read_csv(tc.OUT_DIR / "support_resistance_thickness_failure_analysis.csv")
    sr = sr[sr["touched_intrabar"] & sr["side_of_approach"].isin(["FROM_ABOVE", "FROM_BELOW"])].copy()
    fric = pd.read_parquet(tc.OUT_DIR / "auction_friction_features.parquet")[
        ["bar_end_ts_ns", "thickness_percentile"] + FRICTION_FEATURES]
    df = sr.merge(fric, on="bar_end_ts_ns", how="left", suffixes=("", "_friction"))

    velocity_median = df["velocity_into_level"].median()
    df["high_velocity_move"] = (df["velocity_into_level"] > velocity_median).astype(float)
    df["rejection_from_fat_zone"] = (df["is_fat_at_level"].fillna(False) &
                                     df["level_state_after_touch"].isin(["SUPPORT_HELD", "RESISTANCE_HELD"])).astype(float)
    df["breakout_through_thin_zone"] = (df["is_thin_at_level"].fillna(False) &
                                        df["level_state_after_touch"].isin(["SUPPORT_FAILED", "RESISTANCE_FAILED"])).astype(float)
    df["support_failure"] = np.where(df["side_of_approach"] == "FROM_ABOVE",
                                     (df["level_state_after_touch"] == "SUPPORT_FAILED").astype(float), np.nan)
    df["favorable_MFE_MAE"] = (df["MFE_after_touch"] > df["MAE_after_touch"].abs()).astype(float)

    nqu6 = tc.load_nqu6_master()
    bar_idx_map = nqu6.reset_index().set_index("bar_end_ts_ns")["index"]
    df["global_bar_idx"] = df["bar_end_ts_ns"].map(bar_idx_map)
    df["day"] = df["rithmic_date_str"].str.replace("-", "").astype(int)
    df["t0_idx"] = df["global_bar_idx"]
    df["t1_idx"] = df["global_bar_idx"] + 40

    for dim in ONE_HOT_DIMS:
        for v in df[dim].dropna().unique():
            df[f"{dim}_{v}"] = (df[dim] == v).astype(int)
    return df


# LEAKAGE GUARD for high_velocity_move specifically: velocity_into_level ==
# absolute_return / abs_flow (bar-aggregated), and bid_add+bid_pull+ask_add+
# ask_pull sum to ~abs_flow - i.e. the RAW flow/add/pull magnitudes and all
# 6 friction scores (each a function of velocity_percentile and/or
# abs_flow_percentile internally) are mathematically entangled with this
# target's own denominator. Using them as features would be circular, not a
# genuine "does thickness explain velocity" test. Only the PERCENTILE-
# RANKED, day-normalized thickness fields (a different, rank-transformed
# quantity) and pure context one-hots are used for this target.
TARGET_FEATURE_EXCLUDE = {
    "high_velocity_move": {"velocity_into_level", "directional_velocity", "flow_into_level",
                           "signed_flow_into_level", "bid_add", "bid_pull", "ask_add", "ask_pull",
                           "bid_add_vs_bid_pull", "ask_add_vs_ask_pull"} | set(FRICTION_FEATURES),
}


def select_feature_cols(df, target=None):
    onehot_cols = [c for c in df.columns for dim in ONE_HOT_DIMS if c.startswith(f"{dim}_") and
                  pd.api.types.is_numeric_dtype(df[c])]
    feats = [c for c in BASE_FEATURES + FRICTION_FEATURES if c in df.columns and pd.api.types.is_numeric_dtype(df[c])]
    exclude = TARGET_FEATURE_EXCLUDE.get(target, set())
    feats = [c for c in feats if c not in exclude]
    return sorted(set(feats + onehot_cols))


def fit_predict(model_name, X_train, y_train, sw_train, X_test):
    rs = cfg["random_seed"]
    if model_name == "logreg_baseline":
        p = cfg["models"]["logreg_baseline"]
        clf = LogisticRegression(C=p["C"], max_iter=p["max_iter"], random_state=rs)
    elif model_name == "hist_gb":
        p = cfg["models"]["hist_gb"]
        clf = HistGradientBoostingClassifier(max_iter=p["max_iter"], max_depth=p["max_depth"],
                                             learning_rate=p["learning_rate"], random_state=rs)
    elif model_name == "random_forest":
        p = cfg["models"]["random_forest"]
        clf = RandomForestClassifier(n_estimators=p["n_estimators"], max_depth=p["max_depth"],
                                     min_samples_leaf=p["min_samples_leaf"], class_weight="balanced", random_state=rs)
    else:
        raise ValueError(model_name)
    try:
        clf.fit(X_train, y_train, sample_weight=sw_train)
    except TypeError:
        clf.fit(X_train, y_train)
    return clf, clf.predict_proba(X_test)[:, 1]


def main():
    tc.log("07: training thickness explanatory models (touch-bar contemporaneous features only)...")
    df = build_dataset()
    targets = ["high_velocity_move", "rejection_from_fat_zone", "breakout_through_thin_zone",
              "support_failure", "favorable_MFE_MAE"]

    n_bars = int(tc.load_nqu6_master()["bar_index"].max()) + 1
    model_names = ["logreg_baseline", "hist_gb", "random_forest"]
    results, importance_rows = [], []

    for target in targets:
        feature_cols = select_feature_cols(df, target=target)
        feature_cols = [c for c in feature_cols if df[c].notna().mean() > 0.5]
        elig = df.dropna(subset=feature_cols + ["global_bar_idx", target]).reset_index(drop=True)
        tc.log(f"  target={target}: eligible rows {len(elig)} / {len(df)}, base_rate={elig[target].mean():.3f}" if len(elig) else
              f"  target={target}: 0 eligible rows")
        if len(elig) < cfg["validation"]["min_train_rows"] * 2:
            tc.log(f"    SKIPPED - insufficient sample size ({len(elig)} rows)")
            results.append(dict(target=target, model="ALL", n_oof=len(elig), mcc=None, base_rate=None,
                                effective_n=None, status="SKIPPED_INSUFFICIENT_SAMPLE"))
            continue

        t0 = elig["t0_idx"].to_numpy(); t1 = elig["t1_idx"].to_numpy()
        c_t = tc.num_co_events(t0, t1, n_bars)
        elig["avg_uniqueness"] = tc.average_uniqueness(t0, t1, c_t)
        effective_n = float(elig["avg_uniqueness"].sum())
        tc.log(f"    effective N: {effective_n:.1f} / raw N {len(elig)}")

        folds = tc.purged_embargo_day_splits(elig, "day", "t0_idx", "t1_idx", cfg["validation"]["embargo_bars"])
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
                    tc.log(f"    {target}/{m} failed on fold {fd['test_day']}: {e}")

        for m in model_names:
            p = oof_store[m]
            mask = np.isfinite(p) & np.isfinite(elig[target].to_numpy())
            if mask.sum() < 10:
                results.append(dict(target=target, model=m, n_oof=int(mask.sum()), mcc=None, base_rate=None,
                                    effective_n=effective_n, status="INSUFFICIENT_OOF"))
                continue
            yt = elig[target].to_numpy()[mask]; pb = (p[mask] >= 0.5).astype(float)
            results.append(dict(target=target, model=m, n_oof=int(mask.sum()),
                                mcc=tc.mcc_binary(yt, pb), base_rate=float(yt.mean()),
                                effective_n=effective_n, status="OK"))

    res_df = pd.DataFrame(results)
    out1 = tc.OUT_DIR / "thickness_explanatory_model_results.csv"
    res_df.to_csv(out1, index=False)
    tc.log(f"  wrote {out1}")

    imp_df = pd.DataFrame(importance_rows)
    if len(imp_df):
        imp_summary = imp_df.groupby(["target", "feature"])["importance"].mean().reset_index()
        imp_summary = imp_summary.sort_values(["target", "importance"], ascending=[True, False])
        imp_summary = imp_summary.groupby("target").head(15)
    else:
        imp_summary = pd.DataFrame(columns=["target", "feature", "importance"])
    out2 = tc.OUT_DIR / "thickness_feature_importance.csv"
    imp_summary.to_csv(out2, index=False)
    tc.log(f"  wrote {out2}")

    print(res_df.to_string(index=False))
    tc.log("07 complete.")
    return res_df, imp_summary


if __name__ == "__main__":
    main()
