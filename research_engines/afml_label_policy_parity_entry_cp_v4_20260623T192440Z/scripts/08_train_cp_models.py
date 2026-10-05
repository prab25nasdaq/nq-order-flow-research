"""
08_train_cp_models.py - Part G/H: CP CLOSE/HOLD models + AFML validation.

Target: y_cp (Part F - remaining-realized-value based, NO fixed point/
percent threshold). Same model roster/AFML discipline as script 07. Sample
weight here uses average uniqueness computed on the SNAPSHOT'S OWN PARENT
POSITION [t0_idx_global, t1_idx_global] window - multiple snapshots from
the same position are maximally overlapping by construction (they share
the identical window), so this correctly down-weights a single position's
repeated snapshots relative to a genuinely distinct position elsewhere.

SAMPLE SIZE NOTE: 137,040 active-bar snapshots from 3,426 positions exist -
orders of magnitude larger than v3's 34/19. Reported honestly alongside the
effective independent sample count (which will be far smaller than the raw
row count, since 40 snapshots per position share one parent window).

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
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
import v4_common as v4

cfg = v4.load_config()

try:
    import xgboost as xgb
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False

FEATURE_PREFIXES = ("master_", "dash_", "ofild_", "bf_", "modelprob_")
STRUCTURAL_FEATURES = ("side_primary", "active_bars_elapsed", "active_bars_remaining",
                       "close_now_value", "mfe_so_far", "mae_so_far", "current_distance_ticks")


def select_feature_cols(df: pd.DataFrame) -> list:
    keep = []
    for c in df.columns:
        if not pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c]):
            continue
        if c in STRUCTURAL_FEATURES or c.startswith(FEATURE_PREFIXES):
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
    return clf.predict_proba(X_test)[:, 1]


def compute_metrics(y_true, y_pred, p=None) -> dict:
    # mask on the RAW probability p (when provided), not the already-thresholded
    # y_pred - comparisons like (NaN >= 0.5) silently evaluate to False (finite
    # 0.0), so masking on isfinite(y_pred) alone would wrongly include rows that
    # were never covered by any fold (no OOF prediction at all) as spurious
    # "predicted HOLD/PASS" rows, contaminating the pooled metrics.
    if p is not None:
        valid = np.isfinite(y_true) & np.isfinite(p)
    else:
        valid = np.isfinite(y_true) & np.isfinite(y_pred)
    mask = valid
    n = int(mask.sum())
    if n < 3:
        return dict(n=n, precision=np.nan, recall=np.nan, f1=np.nan, mcc=np.nan,
                    balanced_accuracy=np.nan, auc=np.nan, brier=np.nan, log_loss=np.nan)
    yt, yp = y_true[mask], y_pred[mask]
    precision, recall, f1 = v4.precision_recall_f1(yt, yp)
    mcc = v4.mcc_binary(yt, yp)
    bal = v4.balanced_accuracy(yt, yp)
    auc = v4.safe_auc(yt, p[mask]) if p is not None else np.nan
    brier = v4.brier_score(yt, p[mask]) if p is not None else np.nan
    ll = v4.log_loss_safe(yt, p[mask]) if p is not None else np.nan
    return dict(n=n, precision=precision, recall=recall, f1=f1, mcc=mcc, balanced_accuracy=bal,
                auc=auc, brier=brier, log_loss=ll)


def main():
    v4.log("08: training CP CLOSE/HOLD models (XGBoost/HistGB/RandomForest + LogReg baseline)...")
    snaps = pd.read_parquet(v4.OUT_DIR / "active_position_snapshots_v4.parquet").copy()

    cand = pd.read_parquet(v4.OUT_DIR / "entry_meta_label_dataset_v4.parquet")
    t1_global_map = (cand["nqu6_bar_idx"] + (cand["t1_idx"] - cand["t0_idx"])).to_numpy()
    t0_global_map = cand["nqu6_bar_idx"].to_numpy()
    ev_to_t0 = dict(zip(cand["event_id"], t0_global_map))
    ev_to_t1 = dict(zip(cand["event_id"], t1_global_map))
    snaps["t0_idx_global"] = snaps["event_id"].map(ev_to_t0)
    snaps["t1_idx_global"] = snaps["event_id"].map(ev_to_t1)

    feature_cols = select_feature_cols(snaps)
    feature_cols = [c for c in feature_cols if snaps[c].notna().mean() > 0.5]
    elig = snaps.dropna(subset=feature_cols).reset_index(drop=True)
    v4.log(f"  {len(elig)} / {len(snaps)} snapshots with complete features ({len(feature_cols)} feature columns)")

    n_bars_master = int(v4.load_nqu6_master()["bar_index"].max()) + 1
    t0 = elig["t0_idx_global"].to_numpy(); t1 = elig["t1_idx_global"].to_numpy()
    c_t = v4.num_co_events(t0, t1, n_bars_master)
    elig["avg_uniqueness"] = v4.average_uniqueness(t0, t1, c_t)
    v4.log(f"  effective N (sum avg_uniqueness over snapshots): {elig['avg_uniqueness'].sum():.2f} / raw N {len(elig)}")

    folds = v4.purged_embargo_day_splits(elig, "day", "t0_idx_global", "t1_idx_global", cfg["validation"]["embargo_bars"])
    v4.log(f"  {len(folds)} candidate purge/embargo day-folds")

    model_names = ["logreg_baseline", "hist_gb", "random_forest"] + (["xgboost"] if XGBOOST_AVAILABLE else [])
    oof_store = {m: np.full(len(elig), np.nan) for m in model_names}
    fold_log = []
    MIN_TRAIN = cfg["validation"]["min_train_rows"]
    for fd in folds:
        usable = fd["n_train"] >= MIN_TRAIN and fd["n_test"] >= 1
        train = elig.loc[fd["train_idx"]]; test = elig.loc[fd["test_idx"]]
        if not usable or train["y_cp"].nunique() < 2:
            fold_log.append(dict(test_day=fd["test_day"], n_train=fd["n_train"], n_test=fd["n_test"],
                                 status="SKIPPED_INSUFFICIENT_DATA"))
            continue
        scaler = StandardScaler()
        X_train = scaler.fit_transform(train[feature_cols]); X_test = scaler.transform(test[feature_cols])
        sw = train["avg_uniqueness"].to_numpy()
        for m in model_names:
            try:
                p_test = fit_predict(m, X_train, train["y_cp"].to_numpy(), sw, X_test, cfg)
                oof_store[m][fd["test_idx"]] = p_test
            except Exception as e:
                v4.log(f"    {m} failed on fold {fd['test_day']}: {e}")
        fold_log.append(dict(test_day=fd["test_day"], n_train=fd["n_train"], n_test=fd["n_test"], status="OK"))

    pd.DataFrame(fold_log).to_csv(v4.OUT_DIR / "_cp_fold_log.csv", index=False)

    pred_cols = {"event_id": elig["event_id"], "snapshot_bar_idx": elig["snapshot_bar_idx"], "day": elig["day"],
                 "y_cp": elig["y_cp"], "in_sample_contaminated": elig["in_sample_contaminated"],
                 "avg_uniqueness": elig["avg_uniqueness"], "remaining_value_to_exit": elig["remaining_value_to_exit"]}
    for m in model_names:
        pred_cols[f"p_{m}"] = oof_store[m]
        pred_cols[f"pred_{m}"] = (oof_store[m] >= 0.5).astype(float)
    pred_df = pd.DataFrame(pred_cols)
    pred_df.to_parquet(v4.OUT_DIR / "cp_model_predictions_v4.parquet", index=False)

    comp_rows = []
    per_fold_metric_rows = []
    for m in model_names:
        p = oof_store[m]
        mask_all = ~np.isnan(p)
        pred_bin = (p >= 0.5).astype(float)
        m_all = compute_metrics(elig["y_cp"].to_numpy(), pred_bin, p)
        clean_mask = mask_all & (~elig["in_sample_contaminated"].to_numpy())
        m_oos = compute_metrics(elig.loc[clean_mask, "y_cp"].to_numpy(), pred_bin[clean_mask], p[clean_mask])

        acted_close = mask_all & (pred_bin == 1)
        acted_hold = mask_all & (pred_bin == 0)
        rem = elig["remaining_value_to_exit"].to_numpy()
        value_saved = -rem[acted_close]
        avg_close_improvement = float(np.mean(value_saved)) if acted_close.sum() else np.nan
        avoided_giveback = float(np.mean(np.clip(-rem[acted_close], 0, None))) if acted_close.sum() else np.nan
        bad_close_rate = float(np.mean(rem[acted_close] > 0)) if acted_close.sum() else np.nan
        hold_too_long_rate = float(np.mean(rem[acted_hold] <= 0)) if acted_hold.sum() else np.nan

        comp_rows.append(dict(
            model=m, n_oof=m_all["n"], mcc=m_all["mcc"], f1=m_all["f1"], precision=m_all["precision"],
            recall=m_all["recall"], balanced_accuracy=m_all["balanced_accuracy"], auc=m_all["auc"],
            brier=m_all["brier"], log_loss=m_all["log_loss"],
            n_oos_only=m_oos["n"], mcc_oos_only=m_oos["mcc"],
            avg_close_improvement=avg_close_improvement, avoided_giveback=avoided_giveback,
            avg_value_saved_by_cp=avg_close_improvement, bad_close_rate=bad_close_rate,
            hold_too_long_rate=hold_too_long_rate,
            effective_independent_sample_count=float(elig["avg_uniqueness"].sum()),
        ))
        for fd_info, fd in zip(fold_log, folds):
            if fd_info["status"] != "OK":
                continue
            test_idx = fd["test_idx"]
            p_fold = oof_store[m][test_idx]
            if np.all(np.isnan(p_fold)):
                continue
            yt = elig.loc[test_idx, "y_cp"].to_numpy()
            yp = (p_fold >= 0.5).astype(float)
            per_fold_metric_rows.append(dict(model=m, test_day=fd["test_day"], n_test=len(test_idx), mcc=v4.mcc_binary(yt, yp)))
    comp_df = pd.DataFrame(comp_rows)
    per_fold_df = pd.DataFrame(per_fold_metric_rows)
    if len(per_fold_df):
        fold_summary = per_fold_df.groupby("model")["mcc"].agg(["mean", "min", lambda s: float((s > 0).mean())])
        fold_summary.columns = ["mean_of_folds_mcc", "worst_fold_mcc", "pct_positive_folds"]
        comp_df = comp_df.merge(fold_summary, left_on="model", right_index=True, how="left")
    else:
        comp_df["mean_of_folds_mcc"] = np.nan; comp_df["worst_fold_mcc"] = np.nan; comp_df["pct_positive_folds"] = np.nan

    comp_df.to_csv(v4.OUT_DIR / "model_comparison_cp_v4.csv", index=False)
    per_fold_df.to_csv(v4.OUT_DIR / "fold_results_cp_v4.csv", index=False)

    print(comp_df.to_string(index=False))
    v4.log("08 complete.")
    return comp_df, pred_df


if __name__ == "__main__":
    main()
