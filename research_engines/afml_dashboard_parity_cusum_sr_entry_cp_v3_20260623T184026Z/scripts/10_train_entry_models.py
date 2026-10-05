"""
10_train_entry_models.py - Part I/J: Entry ACT/PASS models + AFML validation.

Task 1 (Entry meta-label ACT/PASS): at a CUSUM event near a valid HVN/LVN/
POC/VAH/VAL level, decide whether to act on the structurally-determined
LONG/SHORT side or pass. y_meta = 1{label_primary==1} (the triple-barrier
side-aware outcome from Part G - PT touched, or favorable sign at the
vertical barrier).

Models: XGBoost (if installed), HistGradientBoostingClassifier,
RandomForestClassifier, LogisticRegression (baseline only). AFML: average-
uniqueness sample weights (Ch.4), purged/embargoed day-based folds (Ch.7),
deduplicated test metrics, genuinely-post-cutoff OOS subset reported
separately.

SAMPLE SIZE WARNING (reported honestly, not hidden): only 19 labeled
candidates exist (CUSUM events are rare by AFML design - h=5.0 is a strict
institutional threshold - and most don't land near a valid S/R level). Every
metric below must be read with that n in mind; this script computes
whatever purge/embargo folds are actually available and reports NaN/"fold
too small" rather than fabricating statistical power that does not exist.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION. No model trained here is
connected to any broker, order, or execution path.
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
import v3_common as v3

cfg = v3.load_config()

try:
    import xgboost as xgb
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False

FEATURE_BLOCKLIST_PATTERNS = (
    "label_primary", "y_cp", "y_meta", "first_touch", "t1_idx", "realized_points",
    "realized_ret", "mfe_points", "mae_points", "holding_bars", "pt_price", "sl_price",
    "event_id", "bar_idx", "bar_end_ts_ns", "day", "side_primary", "side_source",
    "candidate_label", "cusum_side",  # cusum_side kept as a feature deliberately - see below
)


def select_feature_cols(df: pd.DataFrame) -> list:
    keep = []
    for c in df.columns:
        if not pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c]):
            continue
        if c in ("cusum_side", "event_magnitude", "volatility_at_event", "z_at_event",
                 "distance_ticks", "distance_points", "signed_distance_pts", "is_near_valid_sr",
                 "is_near_hvn", "is_near_lvn", "nearest_native_distance_ticks", "nearest_rolling_distance_ticks"):
            keep.append(c); continue
        if any(c == p or c.startswith(p) for p in FEATURE_BLOCKLIST_PATTERNS):
            continue
        if c.startswith("master_") or c.startswith("dash_") or c.startswith("ofild_") or \
           c.startswith("bf_") or c.startswith("modelprob_"):
            keep.append(c)
    return keep


def fit_predict(model_name, X_train, y_train, sw_train, X_test, cfg):
    rs = cfg["random_seed"]
    if model_name == "logreg_baseline":
        clf = LogisticRegression(C=cfg["models"]["logreg_baseline"]["C"],
                                 max_iter=cfg["models"]["logreg_baseline"]["max_iter"], random_state=rs)
    elif model_name == "hist_gb":
        p = cfg["models"]["hist_gb"]
        clf = HistGradientBoostingClassifier(max_iter=p["max_iter"], max_depth=p["max_depth"],
                                             learning_rate=p["learning_rate"], random_state=rs)
    elif model_name == "random_forest":
        p = cfg["models"]["random_forest"]
        clf = RandomForestClassifier(n_estimators=p["n_estimators"], max_depth=p["max_depth"],
                                     min_samples_leaf=p["min_samples_leaf"], class_weight="balanced", random_state=rs)
    elif model_name == "xgboost":
        p = cfg["models"]["xgboost"]
        clf = xgb.XGBClassifier(n_estimators=p["n_estimators"], max_depth=p["max_depth"],
                                learning_rate=p["learning_rate"], random_state=rs,
                                eval_metric="logloss", verbosity=0)
    else:
        raise ValueError(model_name)
    try:
        clf.fit(X_train, y_train, sample_weight=sw_train)
    except TypeError:
        clf.fit(X_train, y_train)
    p_test = clf.predict_proba(X_test)[:, 1]
    return p_test


def compute_metrics(y_true, y_pred, p=None) -> dict:
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    n = int(mask.sum())
    if n < 3:
        return dict(n=n, precision=np.nan, recall=np.nan, f1=np.nan, mcc=np.nan,
                    balanced_accuracy=np.nan, auc=np.nan, brier=np.nan, hit_rate=np.nan)
    yt, yp = y_true[mask], y_pred[mask]
    precision, recall, f1 = v3.precision_recall_f1(yt, yp)
    mcc = v3.mcc_binary(yt, yp)
    bal = v3.balanced_accuracy(yt, yp)
    hit = float((yt == yp).mean())
    auc = v3.safe_auc(yt, p[mask]) if p is not None else np.nan
    brier = v3.brier_score(yt, p[mask]) if p is not None else np.nan
    return dict(n=n, precision=precision, recall=recall, f1=f1, mcc=mcc,
                balanced_accuracy=bal, auc=auc, brier=brier, hit_rate=hit)


def main():
    v3.log("10: training Entry ACT/PASS models (XGBoost/HistGB/RandomForest + LogReg baseline)...")
    cand = pd.read_parquet(v3.OUT_DIR / "candidate_events_entry_v3.parquet")
    labels = pd.read_parquet(v3.OUT_DIR / "triple_barrier_labels_entry_v3.parquet")
    df = cand.merge(labels[["event_id", "t1_idx", "label_primary", "first_touch", "realized_points",
                            "holding_bars"]], on="event_id", how="left")
    elig = df[(df["side_primary"] != 0) & (df["t1_idx"] >= 0)].copy().reset_index(drop=True)
    elig["y_meta"] = (elig["label_primary"] == 1).astype(float)
    elig["in_sample_contaminated"] = pd.to_datetime(elig["bar_end_ts_ns"], unit="ns", utc=True) <= v3.MODEL_TRAINING_CUTOFF_UTC

    feature_cols = select_feature_cols(elig)
    feature_cols = [c for c in feature_cols if elig[c].notna().mean() > 0.5]
    elig = elig.dropna(subset=feature_cols).reset_index(drop=True)
    v3.log(f"  {len(elig)} eligible labeled candidates with complete features ({len(feature_cols)} feature columns)")

    # AFML Ch.4: average uniqueness sample weight on the entry-candidate population
    n_bars_master = v3.load_nqu6_master()["bar_index"].max() + 1
    t0 = elig["bar_idx"].to_numpy(); t1 = elig["t1_idx"].to_numpy()
    c_t = v3.num_co_events(t0, t1, n_bars_master)
    elig["avg_uniqueness"] = v3.average_uniqueness(t0, t1, c_t)
    elig["cluster_id"] = v3.cluster_by_overlap(elig["day"].to_numpy(), t0, t1)
    v3.log(f"  effective N (sum avg_uniqueness): {elig['avg_uniqueness'].sum():.2f} / raw N {len(elig)}")

    elig["t1_idx_for_purge"] = elig["bar_idx"] + elig["holding_bars"].fillna(cfg["triple_barrier"]["vertical_barrier_bars"])
    folds = v3.purged_embargo_day_splits(elig, "day", "bar_idx", "t1_idx_for_purge", cfg["validation"]["embargo_bars"])
    v3.log(f"  {len(folds)} candidate purge/embargo day-folds (many will be too small to train/test - reported honestly)")

    model_names = ["logreg_baseline", "hist_gb", "random_forest"] + (["xgboost"] if XGBOOST_AVAILABLE else [])
    oof_store = {m: np.full(len(elig), np.nan) for m in model_names}
    fold_log = []
    MIN_TRAIN = cfg["validation"]["min_train_rows"]
    for fd in folds:
        usable = fd["n_train"] >= max(6, MIN_TRAIN // 8) and fd["n_test"] >= 1
        train = elig.loc[fd["train_idx"]]; test = elig.loc[fd["test_idx"]]
        if not usable or train["y_meta"].nunique() < 2:
            fold_log.append(dict(test_day=fd["test_day"], n_train=fd["n_train"], n_test=fd["n_test"],
                                 status="SKIPPED_INSUFFICIENT_DATA"))
            continue
        scaler = StandardScaler()
        X_train = scaler.fit_transform(train[feature_cols]); X_test = scaler.transform(test[feature_cols])
        sw = train["avg_uniqueness"].to_numpy()
        for m in model_names:
            try:
                p_test = fit_predict(m, X_train, train["y_meta"].to_numpy(), sw, X_test, cfg)
                oof_store[m][fd["test_idx"]] = p_test
            except Exception as e:
                v3.log(f"    {m} failed on fold {fd['test_day']}: {e}")
        fold_log.append(dict(test_day=fd["test_day"], n_train=fd["n_train"], n_test=fd["n_test"], status="OK"))

    pd.DataFrame(fold_log).to_csv(v3.OUT_DIR / "_entry_fold_log.csv", index=False)

    pred_cols = {"event_id": elig["event_id"], "day": elig["day"], "y_meta": elig["y_meta"],
                 "in_sample_contaminated": elig["in_sample_contaminated"], "avg_uniqueness": elig["avg_uniqueness"]}
    for m in model_names:
        pred_cols[f"p_{m}"] = oof_store[m]
        pred_cols[f"pred_{m}"] = (oof_store[m] >= 0.5).astype(float)
    pred_df = pd.DataFrame(pred_cols)
    pred_df.to_parquet(v3.OUT_DIR / "entry_model_predictions_v3.parquet", index=False)

    comp_rows = []
    for m in model_names:
        p = oof_store[m]
        mask_all = ~np.isnan(p)
        m_all = compute_metrics(elig["y_meta"].to_numpy(), (p >= 0.5).astype(float), p)
        clean_mask = mask_all & (~elig["in_sample_contaminated"].to_numpy())
        m_oos = compute_metrics(elig.loc[clean_mask, "y_meta"].to_numpy(),
                                (p[clean_mask] >= 0.5).astype(float), p[clean_mask])
        ft_match = elig["first_touch"].to_numpy()
        pt_rate = float((ft_match == "PT").mean()) if len(ft_match) else np.nan
        sl_rate = float((ft_match == "SL").mean()) if len(ft_match) else np.nan
        time_rate = float((ft_match == "VB").mean()) if len(ft_match) else np.nan
        rp = elig["realized_points"]
        comp_rows.append(dict(
            model=m, n_oof=m_all["n"], mcc=m_all["mcc"], f1=m_all["f1"], precision=m_all["precision"],
            recall=m_all["recall"], balanced_accuracy=m_all["balanced_accuracy"], auc=m_all["auc"],
            brier=m_all["brier"], hit_rate=m_all["hit_rate"],
            n_oos_only=m_oos["n"], mcc_oos_only=m_oos["mcc"], f1_oos_only=m_oos["f1"],
            pt_rate=pt_rate, sl_rate=sl_rate, time_rate=time_rate,
            mean_points=float(rp.mean()), median_points=float(rp.median()),
            effective_independent_sample_count=float(elig["avg_uniqueness"].sum()),
        ))
    comp_df = pd.DataFrame(comp_rows)

    # worst-fold / mean-of-folds MCC per model (day-level, using whatever folds ran)
    per_fold_metric_rows = []
    for fd_info, fd in zip(fold_log, folds):
        if fd_info["status"] != "OK":
            continue
        test_idx = fd["test_idx"]
        for m in model_names:
            p_fold = oof_store[m][test_idx]
            if np.all(np.isnan(p_fold)):
                continue
            yt = elig.loc[test_idx, "y_meta"].to_numpy()
            yp = (p_fold >= 0.5).astype(float)
            mcc = v3.mcc_binary(yt, yp)
            per_fold_metric_rows.append(dict(model=m, test_day=fd["test_day"], n_test=len(test_idx), mcc=mcc))
    per_fold_df = pd.DataFrame(per_fold_metric_rows)
    if len(per_fold_df):
        fold_summary = per_fold_df.groupby("model")["mcc"].agg(["mean", "min", lambda s: float((s > 0).mean())])
        fold_summary.columns = ["mean_of_folds_mcc", "worst_fold_mcc", "pct_positive_folds"]
        comp_df = comp_df.merge(fold_summary, left_on="model", right_index=True, how="left")
    else:
        comp_df["mean_of_folds_mcc"] = np.nan; comp_df["worst_fold_mcc"] = np.nan; comp_df["pct_positive_folds"] = np.nan

    comp_df.to_csv(v3.OUT_DIR / "model_comparison_entry_v3.csv", index=False)
    per_fold_df.to_csv(v3.OUT_DIR / "_entry_per_fold_metrics.csv", index=False)

    print(f"XGBOOST_TESTED: {XGBOOST_AVAILABLE}")
    if not XGBOOST_AVAILABLE:
        print("XGBOOST_SKIPPED_NOT_INSTALLED")
    print(comp_df.to_string(index=False))
    v3.log("10 complete.")
    return comp_df, pred_df


if __name__ == "__main__":
    main()
