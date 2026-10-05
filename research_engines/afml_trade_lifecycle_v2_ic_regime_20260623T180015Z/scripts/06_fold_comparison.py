"""
06_fold_comparison.py - fold_results_v2_ic_regime.csv

The central comparison required by the build spec:
  V1_BASELINE       - v1's original (non-deduplicated) population, uniform
                       sample weight, v1's 28-column feature set. Re-trained
                       here (not just read from v1's old CSV) using the exact
                       same purge/embargo + LogisticRegression code path as
                       v2a/v2b, for a guaranteed apples-to-apples comparison.
                       (The failure diagnostic already verified this
                       reproduces v1's reported pooled MCC=0.014770 exactly.)
  V2A_DEDUP_UNIQ    - same-bar-deduplicated population (script 04),
                       sample_weight=avg_uniqueness, v1's SAME 28-column
                       feature set (isolates the marginal effect of the AFML
                       Ch.4 fix alone, with no new features).
  V2B_IC_REGIME     - same deduplicated/weighted population, PLUS the 7
                       features x (6 reliability + 3 interaction) = 63 new
                       IC-regime columns (91 features total).

All three use IDENTICAL purge/embargo logic, day-based folds, and
LogisticRegression(C=1.0, max_iter=1000) - only the row population, sample
weights, and feature columns differ between variants.

Every metric is reported BOTH pooled (single confusion matrix across all
out-of-fold rows) AND mean-of-folds (each calendar day weighted equally),
and BOTH for the full out-of-fold population and the genuinely-OOS-only
(in_sample_contaminated==False) subset - never collapsed into one number,
per the standing in-sample-contamination discipline from v1/the diagnostic.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION. No model trained here is
connected to any broker, order, or execution path.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v2_common as v2

cfg = v2.load_config()
EMBARGO_BARS = cfg["validation"]["embargo_bars"]
MIN_TRAIN_ROWS = cfg["validation"]["min_train_rows"]
VERTICAL_BARRIER_BARS = cfg["triple_barrier"]["vertical_barrier_bars"]

V1_FEATURE_COLS = [
    "primary_probability", "primary_confidence", "bars_since_last_event",
    "training_gate_status_code", "reaction_type_code", "nearest_level_type_code",
    "nearest_level_distance", "volatility_target", "volatility_5", "vpin",
    "regime_ic_mlofi", "cusum_up_break", "cusum_down_break", "mlofi_norm", "delta_norm",
    "minute_of_day", "dow", "session_3b_code",
    "bid_pull_pressure_z20", "bid_pull_pressure_roll5sum",
    "ask_pull_minus_bid_pull_z20", "ask_pull_minus_bid_pull_roll5sum",
    "bid_add_minus_ask_add_z20", "bid_add_minus_ask_add_roll5sum",
    "signed_flow_z20", "signed_flow_roll5sum", "abs_flow_z20", "abs_flow_roll5sum",
]


def compute_metrics(y_true, y_pred) -> dict:
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    n = int(mask.sum())
    if n < 5:
        return dict(n=n, precision=np.nan, recall=np.nan, f1=np.nan, mcc=np.nan, balanced_accuracy=np.nan)
    yt, yp = y_true[mask], y_pred[mask]
    precision, recall, f1 = v2.precision_recall_f1(yt, yp)
    mcc = v2.mcc_binary(yt, yp)
    bal = v2.balanced_accuracy(yt, yp)
    return dict(n=n, precision=precision, recall=recall, f1=f1, mcc=mcc, balanced_accuracy=bal)


def run_variant(elig: pd.DataFrame, feature_cols: list, weight_col: str, variant_name: str):
    """Returns (fold_rows, oof_pred_array aligned to elig.index, elig (with t1_idx_for_purge))."""
    elig = elig.dropna(subset=feature_cols).reset_index(drop=True)
    elig["t1_idx_for_purge"] = elig["t0_idx"] + elig["holding_bars"].fillna(VERTICAL_BARRIER_BARS)

    folds = v2.purged_embargo_day_splits(
        elig, day_col="day", t0_col="t0_idx", t1_col="t1_idx_for_purge", embargo_bars=EMBARGO_BARS
    )
    oof_pred = np.full(len(elig), np.nan)
    fold_rows = []
    for fd in folds:
        if fd["n_train"] < MIN_TRAIN_ROWS or fd["n_test"] < 5:
            continue
        train, test = elig.loc[fd["train_idx"]], elig.loc[fd["test_idx"]]
        if train["y_meta"].nunique() < 2:
            continue
        sw = train[weight_col].to_numpy() if weight_col else None
        scaler = StandardScaler()
        X_train = scaler.fit_transform(train[feature_cols])
        X_test = scaler.transform(test[feature_cols])
        clf = LogisticRegression(max_iter=cfg["model"]["max_iter"], C=cfg["model"]["C"],
                                  random_state=cfg["random_seed"])
        clf.fit(X_train, train["y_meta"], sample_weight=sw)
        p_test = clf.predict_proba(X_test)[:, 1]
        pred = (p_test >= 0.5).astype(float)
        oof_pred[fd["test_idx"]] = pred

        m_all = compute_metrics(test["y_meta"].to_numpy(), pred)
        clean_mask = ~test["in_sample_contaminated"].to_numpy()
        m_oos = compute_metrics(test.loc[clean_mask, "y_meta"].to_numpy(), pred[clean_mask])
        fold_rows.append(dict(
            variant=variant_name, test_day=fd["test_day"], n_train_raw=len(train),
            n_train_effective=float(train[weight_col].sum()) if weight_col else float(len(train)),
            n_purged=fd["n_purged"], n_embargoed=fd["n_embargoed"],
            n_test=m_all["n"], precision=m_all["precision"], recall=m_all["recall"],
            f1=m_all["f1"], mcc=m_all["mcc"], balanced_accuracy=m_all["balanced_accuracy"],
            n_test_oos_only=m_oos["n"], mcc_oos_only=m_oos["mcc"], f1_oos_only=m_oos["f1"],
        ))
    return fold_rows, oof_pred, elig


def main():
    v2.log("06: loading v1 baseline + v2a/v2b populations, running identical purge/embargo CV on each...")
    v1_full = v2.load_v1_meta_label_dataset()
    v1_elig = v1_full[v1_full["eligible_for_meta_training"]].copy()
    v1_elig["_weight_uniform"] = 1.0
    v2_ds = pd.read_parquet(v2.OUT_DIR / "meta_label_dataset_v2_ic_regime.parquet")
    v2_elig = v2_ds[v2_ds["eligible_for_meta_training"]].copy()

    ic_regime_cols = []
    interaction_cols = []
    for feat in cfg["rolling_ic"]["features"]:
        ic_regime_cols += [f"{feat}_rolling_ic", f"{feat}_rolling_ic_sign",
                           f"{feat}_rolling_ic_abs_strength", f"{feat}_rolling_ic_tstat",
                           f"{feat}_rolling_ic_stability", f"{feat}_rolling_ic_window_n"]
        interaction_cols += [f"{feat}_value_x_ic", f"{feat}_z20_x_ic", f"{feat}_roll5sum_x_ic"]
    v2b_feature_cols = V1_FEATURE_COLS + ic_regime_cols + interaction_cols

    v2.log(f"  V1_BASELINE pool: {len(v1_elig)} eligible rows, {len(V1_FEATURE_COLS)} features, uniform weight")
    v2.log(f"  V2A_DEDUP_UNIQ pool: {len(v2_elig)} eligible rows, {len(V1_FEATURE_COLS)} features, avg_uniqueness weight")
    v2.log(f"  V2B_IC_REGIME pool: {len(v2_elig)} eligible rows, {len(v2b_feature_cols)} features, avg_uniqueness weight")

    all_fold_rows = []
    oof_results = {}
    for name, elig, feats, wcol in [
        ("V1_BASELINE", v1_elig, V1_FEATURE_COLS, "_weight_uniform"),
        ("V2A_DEDUP_UNIQ", v2_elig, V1_FEATURE_COLS, "avg_uniqueness"),
        ("V2B_IC_REGIME", v2_elig, v2b_feature_cols, "avg_uniqueness"),
    ]:
        fold_rows, oof_pred, elig_used = run_variant(elig, feats, wcol, name)
        all_fold_rows += fold_rows
        oof_results[name] = (elig_used, oof_pred)
        v2.log(f"  {name}: {len(fold_rows)} folds run, {(~np.isnan(oof_pred)).sum()} OOF predictions")

    fold_df = pd.DataFrame(all_fold_rows)
    fold_df.to_csv(v2.OUT_DIR / "fold_results_v2_ic_regime.csv", index=False)

    v2.log("  === MEAN-OF-FOLDS summary (each calendar day weighted equally) ===")
    summary = fold_df.groupby("variant").agg(
        n_folds=("test_day", "nunique"), n_test_total=("n_test", "sum"),
        mcc_mean=("mcc", "mean"), mcc_worst=("mcc", "min"),
        pct_positive_folds=("mcc", lambda s: float((s > 0).mean())),
        mcc_oos_only_mean=("mcc_oos_only", "mean"), mcc_oos_only_worst=("mcc_oos_only", "min"),
        n_test_oos_only_total=("n_test_oos_only", "sum"),
    )
    print(summary.to_string())

    v2.log("  === POOLED (single confusion matrix across all OOF rows) summary ===")
    pooled_rows = []
    for name, (elig_used, oof_pred) in oof_results.items():
        mask = ~np.isnan(oof_pred)
        m_all = compute_metrics(elig_used.loc[mask, "y_meta"].to_numpy(), oof_pred[mask])
        clean_mask = mask & (~elig_used["in_sample_contaminated"].to_numpy())
        m_oos = compute_metrics(elig_used.loc[clean_mask, "y_meta"].to_numpy(), oof_pred[clean_mask])
        pooled_rows.append(dict(variant=name, **{f"pooled_{k}": v for k, v in m_all.items()},
                                **{f"pooled_oos_only_{k}": v for k, v in m_oos.items()}))
    pooled_df = pd.DataFrame(pooled_rows).set_index("variant")
    print(pooled_df.to_string())
    pooled_df.to_csv(v2.OUT_DIR / "_pooled_summary_v2_ic_regime.csv")

    for name in ["V1_BASELINE", "V2A_DEDUP_UNIQ", "V2B_IC_REGIME"]:
        row = summary.loc[name]
        prow = pooled_df.loc[name]
        print(f"MEAN_OF_FOLDS_MCC__{name}: {row['mcc_mean']:.4f}")
        print(f"WORST_FOLD_MCC__{name}: {row['mcc_worst']:.4f}")
        print(f"POOLED_MCC__{name}: {prow['pooled_mcc']:.4f}")
        print(f"POOLED_MCC_OOS_ONLY__{name}: {prow['pooled_oos_only_mcc']:.4f}")
        print(f"MEAN_OF_FOLDS_MCC_OOS_ONLY__{name}: {row['mcc_oos_only_mean']:.4f}")
        print(f"N_TEST_OOS_ONLY__{name}: {int(row['n_test_oos_only_total'])}")
    v2.log("06 complete.")
    return fold_df, summary, pooled_df


if __name__ == "__main__":
    main()
