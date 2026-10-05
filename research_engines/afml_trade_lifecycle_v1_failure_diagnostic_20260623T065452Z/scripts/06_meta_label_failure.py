"""
06_meta_label_failure.py - meta_label_failure_report.csv

THE core diagnostic experiment. Re-trains v1's exact meta-label model
(same features, same purged/embargoed day-based folds, same LogisticRegression
config) under controlled variants, to separate "no signal" from "redundant
overlapping labels" as the cause of v1's flat/negative validation MCC:

  TRAIN variants (test set is identical across all three - see TEST variants
  below for how it is itself evaluated):
    UNWEIGHTED   - v1-IDENTICAL: every training row gets weight 1.0
                   (reproduces v1's fold_results.csv as a correctness check)
    AFML_UNIQUENESS_WEIGHTED - sample_weight = average uniqueness (Ch.4 4.2)
    AFML_COMBINED_WEIGHTED   - sample_weight = uniqueness x return-attribution
                               x time-decay (Ch.4 4.2/4.10/4.11 combined)
    DEDUPLICATED - one representative row per overlap-cluster (earliest
                   t0_idx in the cluster), i.e. literally remove the
                   redundant copies before fitting at all, weight 1.0

  TEST variants (how the held-out fold is scored):
    RAW_TEST     - v1-IDENTICAL: every test row scored and counted once
                   (a single giant burst can dominate the fold's metric)
    DEDUPLICATED_TEST - one representative row per overlap-cluster in the
                   test fold too, so a 981-event burst contributes ONE
                   verdict instead of 981 identical ones

If AFML_*_WEIGHTED / DEDUPLICATED training (evaluated on RAW_TEST, to stay
comparable to v1's own reported numbers) materially changes MCC versus
UNWEIGHTED, the redundancy itself was distorting the fit - not merely a
sample-size artifact of evaluation. If nothing changes under any variant,
the honest conclusion is "no signal", not "redundant labels".

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION. No model trained here is
connected to any broker, order, or execution path - this is a diagnostic
re-analysis only.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diagnostic_common as dc

V1_CFG = yaml.safe_load(open(dc.V1_DIR / "configs" / "lifecycle_config.yaml"))
EMBARGO_BARS = V1_CFG["validation"]["embargo_bars"]
MIN_TRAIN_ROWS = V1_CFG["validation"]["min_train_rows"]
VERTICAL_BARRIER_BARS = V1_CFG["triple_barrier"]["vertical_barrier_bars"]


def compute_metrics(y_true, y_pred) -> dict:
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    n = int(mask.sum())
    if n < 5:
        return dict(n=n, precision=np.nan, recall=np.nan, f1=np.nan, mcc=np.nan,
                    balanced_accuracy=np.nan, hit_rate=np.nan)
    yt, yp = y_true[mask], y_pred[mask]
    precision, recall, f1 = dc.precision_recall_f1(yt, yp)
    mcc = dc.mcc_binary(yt, yp)
    bal = dc.balanced_accuracy(yt, yp)
    hit = float((yt == yp).mean())
    return dict(n=n, precision=precision, recall=recall, f1=f1, mcc=mcc,
                balanced_accuracy=bal, hit_rate=hit)


def dedup_representative(idx: np.ndarray, cluster_id: np.ndarray, t0_idx: np.ndarray) -> np.ndarray:
    """Returns the subset of idx keeping only the earliest-t0_idx row per
    cluster_id (non-cherry-picked: chronological first, not best-confidence)."""
    df = pd.DataFrame({"idx": idx, "cluster_id": cluster_id, "t0_idx": t0_idx})
    rep = df.sort_values(["cluster_id", "t0_idx"]).groupby("cluster_id").first()
    return rep["idx"].to_numpy()


def main():
    dc.log("06: rebuilding v1's exact meta-training population + AFML weights/clusters...")
    meta_ds = dc.load_v1_meta_label_dataset()
    core = pd.read_parquet(dc.OUT_DIR / "_core_event_diagnostics.parquet")[
        ["event_id", "avg_uniqueness", "combined_sample_weight", "cluster_id", "cluster_size"]
    ]
    df = meta_ds.merge(core, on="event_id", how="left")

    feature_cols = [c for c in meta_ds.columns if c not in (
        "event_id", "t0", "t0_idx", "day", "side_primary", "label_primary", "y_meta",
        "eligible_for_meta_training", "in_sample_contaminated", "reaction_type",
        "training_gate_status", "nearest_level_type", "session_3b", "realized_points",
        "holding_bars", "first_touch",
    )]
    elig = df[df["eligible_for_meta_training"]].dropna(subset=feature_cols).reset_index(drop=True)
    dc.log(f"  {len(elig)} eligible, NaN-feature-free rows (matches v1 script04's 4366 by construction)")

    elig["t1_idx_for_purge"] = elig["t0_idx"] + elig["holding_bars"].fillna(VERTICAL_BARRIER_BARS)
    folds = dc.purged_embargo_day_splits(
        elig, day_col="day", t0_col="t0_idx", t1_col="t1_idx_for_purge", embargo_bars=EMBARGO_BARS
    )
    dc.log(f"  {len(folds)} purged/embargoed folds (identical split logic to v1 script04)")

    train_variants = ["UNWEIGHTED", "AFML_UNIQUENESS_WEIGHTED", "AFML_COMBINED_WEIGHTED", "DEDUPLICATED"]
    fold_rows = []
    # per-row OOF predictions, keyed by (train_variant, event row index) - needed for the
    # by-reaction_type / by-level_type / by-cluster-bucket breakdowns below
    oof_store = {v: np.full(len(elig), np.nan) for v in train_variants}

    for fd in folds:
        if fd["n_train"] < MIN_TRAIN_ROWS or fd["n_test"] < 5:
            continue
        train_idx_full, test_idx = fd["train_idx"], fd["test_idx"]
        train_full = elig.loc[train_idx_full]
        test = elig.loc[test_idx]
        if train_full["y_meta"].nunique() < 2:
            continue

        for variant in train_variants:
            if variant == "DEDUPLICATED":
                rep_idx = dedup_representative(
                    train_full.index.to_numpy(), train_full["cluster_id"].to_numpy(),
                    train_full["t0_idx"].to_numpy(),
                )
                train = elig.loc[rep_idx]
                sw = None
            else:
                train = train_full
                if variant == "UNWEIGHTED":
                    sw = None
                elif variant == "AFML_UNIQUENESS_WEIGHTED":
                    sw = train["avg_uniqueness"].to_numpy()
                else:  # AFML_COMBINED_WEIGHTED
                    sw = train["combined_sample_weight"].to_numpy()

            if train["y_meta"].nunique() < 2 or len(train) < 20:
                continue
            scaler = StandardScaler()
            X_train = scaler.fit_transform(train[feature_cols])
            X_test = scaler.transform(test[feature_cols])
            clf = LogisticRegression(max_iter=1000, C=1.0, random_state=V1_CFG["random_seed"])
            clf.fit(X_train, train["y_meta"], sample_weight=sw)
            p_test = clf.predict_proba(X_test)[:, 1]
            pred_test = (p_test >= 0.5).astype(float)
            oof_store[variant][test_idx] = pred_test

            m_raw = compute_metrics(test["y_meta"].to_numpy(), pred_test)

            # DEDUPLICATED_TEST: one representative prediction per cluster in the test fold
            test_rep_idx = dedup_representative(
                test.index.to_numpy(), test["cluster_id"].to_numpy(), test["t0_idx"].to_numpy()
            )
            test_rep_mask = np.isin(test.index.to_numpy(), test_rep_idx)
            m_dedup_test = compute_metrics(
                test.loc[test_rep_mask, "y_meta"].to_numpy(), pred_test[test_rep_mask]
            )

            n_train_eff = float(train_full["avg_uniqueness"].sum()) if variant != "DEDUPLICATED" else len(train)
            for test_variant, m in [("RAW_TEST", m_raw), ("DEDUPLICATED_TEST", m_dedup_test)]:
                fold_rows.append(dict(
                    breakdown_dim="fold", breakdown_value=str(fd["test_day"]),
                    train_variant=variant, test_variant=test_variant,
                    n_train_raw=len(train), n_train_effective_uniqueness=n_train_eff,
                    n_purged=fd["n_purged"], n_embargoed=fd["n_embargoed"],
                    **m,
                ))

    fold_df = pd.DataFrame(fold_rows)

    # ── pooled-by-source breakdowns (reaction_type / nearest_level_type / cluster_size_bucket),
    # using the OOF predictions already collected above, RAW_TEST evaluation ──
    elig["cluster_size_bucket"] = pd.cut(
        elig["cluster_size"], bins=[-1, 1, 5, 20, 100, 1e9],
        labels=["1_singleton", "2-5", "6-20", "21-100", "100+"],
    )
    extra_rows = []
    for variant in train_variants:
        elig["_pred"] = oof_store[variant]
        for dim in ["reaction_type", "nearest_level_type", "cluster_size_bucket"]:
            for val, g in elig.groupby(dim, observed=True):
                m = compute_metrics(g["y_meta"].to_numpy(), g["_pred"].to_numpy())
                eff_n = float(g["avg_uniqueness"].sum())
                extra_rows.append(dict(
                    breakdown_dim=dim, breakdown_value=str(val), train_variant=variant,
                    test_variant="RAW_TEST", n_train_raw=len(g), n_train_effective_uniqueness=eff_n,
                    n_purged=np.nan, n_embargoed=np.nan, **m,
                ))
    extra_df = pd.DataFrame(extra_rows)
    extra_df["redundancy_ratio"] = extra_df["n_train_raw"] / extra_df["n_train_effective_uniqueness"].replace(0, np.nan)

    dc.log("  REDUNDANCY RATIO by reaction_type (raw_n / effective_n=sum(avg_uniqueness), "
          "identical across train_variant since it only depends on which rows are in the group):")
    redund_by_rxn = extra_df[(extra_df["breakdown_dim"] == "reaction_type") & (extra_df["train_variant"] == "UNWEIGHTED")]
    print(redund_by_rxn[["breakdown_value", "n_train_raw", "n_train_effective_uniqueness", "redundancy_ratio"]]
          .sort_values("redundancy_ratio", ascending=False).to_string(index=False))

    # ── pooled-confusion-matrix metrics (v1's OWN aggregation method: ONE
    # confusion matrix across all OOF rows, NOT the mean of six per-fold MCCs)
    # computed alongside the mean-of-folds view above - the two can differ
    # sharply when fold sizes/class-balance vary, and v1's headline number
    # (MCC=0.0148) is specifically the POOLED version, confirmed to be exactly
    # reproduced by this script's UNWEIGHTED variant below. ────────────────
    pooled_rows = []
    for variant in train_variants:
        pred = oof_store[variant]
        mask = np.isfinite(pred)
        m = compute_metrics(elig.loc[mask, "y_meta"].to_numpy(), pred[mask])
        pooled_rows.append(dict(breakdown_dim="pooled_confusion_matrix", breakdown_value="ALL_OOF",
                                train_variant=variant, test_variant="RAW_TEST",
                                n_train_raw=np.nan, n_train_effective_uniqueness=np.nan,
                                n_purged=np.nan, n_embargoed=np.nan, **m))
    pooled_df = pd.DataFrame(pooled_rows)
    dc.log("  POOLED (single confusion matrix across all OOF rows - v1's own headline aggregation):")
    print(pooled_df[["train_variant", "n", "precision", "recall", "f1", "mcc", "balanced_accuracy"]].to_string(index=False))

    report = pd.concat([fold_df, extra_df, pooled_df], ignore_index=True)
    report.to_csv(dc.OUT_DIR / "meta_label_failure_report.csv", index=False)

    dc.log("  MEAN-OF-FOLDS MCC by train_variant (RAW_TEST; each calendar day weighted equally,"
          " regardless of size - a day-by-day stability view, NOT v1's headline pooled number):")
    pooled = fold_df[fold_df["test_variant"] == "RAW_TEST"].groupby("train_variant").agg(
        n_folds=("breakdown_value", "nunique"), n_test_total=("n", "sum"),
        mcc_mean=("mcc", "mean"), mcc_worst=("mcc", "min"),
        pct_positive_folds=("mcc", lambda s: float((s > 0).mean())),
    )
    print(pooled.to_string())

    dc.log("  pooled MCC by train_variant x test_variant (sanity: does test-side dedup matter?):")
    pooled2 = fold_df.groupby(["train_variant", "test_variant"])["mcc"].mean()
    print(pooled2.to_string())

    dc.log("  by reaction_type (UNWEIGHTED vs DEDUPLICATED training, RAW_TEST):")
    by_rxn = extra_df[(extra_df["breakdown_dim"] == "reaction_type") &
                      (extra_df["train_variant"].isin(["UNWEIGHTED", "DEDUPLICATED"]))]
    print(by_rxn.pivot_table(index="breakdown_value", columns="train_variant", values=["n", "mcc"]).to_string())

    v1_unweighted_pooled_mcc = float(pooled_df.loc[pooled_df["train_variant"] == "UNWEIGHTED", "mcc"].iloc[0])
    dc.log(f"  REPRODUCTION CHECK: this script's UNWEIGHTED pooled-confusion-matrix MCC = "
          f"{v1_unweighted_pooled_mcc:.6f} vs v1's own reported ALL_OUT_OF_FOLD MCC = 0.014770 "
          f"({'MATCH - fold/feature reconstruction verified exact' if abs(v1_unweighted_pooled_mcc - 0.014770) < 1e-4 else 'MISMATCH - investigate'})")

    print(f"N_FOLDS: {fold_df['breakdown_value'].nunique()}")
    for tv in train_variants:
        sub = fold_df[(fold_df["train_variant"] == tv) & (fold_df["test_variant"] == "RAW_TEST")]
        pooled_mcc = float(pooled_df.loc[pooled_df["train_variant"] == tv, "mcc"].iloc[0])
        print(f"MEAN_OF_FOLDS_MCC__{tv}: {sub['mcc'].mean():.4f}")
        print(f"POOLED_CONFUSION_MATRIX_MCC__{tv}: {pooled_mcc:.4f}")
        print(f"WORST_FOLD_MCC__{tv}: {sub['mcc'].min():.4f}")
    dc.log("06 complete.")
    return report


if __name__ == "__main__":
    main()
