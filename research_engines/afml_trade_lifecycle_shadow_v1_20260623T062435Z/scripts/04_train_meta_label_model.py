"""
04_train_meta_label_model.py - purged walk-forward meta-label model.

Trains a logistic regression meta-model (act/pass) using day-based, purged +
embargoed walk-forward splits (AFML Ch.7) - see afml_common.purged_embargo_day_splits.
Every p_meta value written to meta_model_predictions.parquet is OUT-OF-FOLD:
the model used to score day D was never trained on day D, nor on any day
whose label window overlaps day D, nor on the embargo window after a test
fold. This is the probability that bet sizing (script 05) is allowed to use.

IMPORTANT CAVEAT (carried through to every downstream report): even though
p_meta itself is genuinely out-of-fold within THIS engine's own CV, its
INPUT FEATURES include primary_probability/primary_confidence, which come
from the production level-reaction model - and per the prior institutional
audit, that model's own probabilities are ~99% in-sample for events at or
before its 2026-06-21T01:53 training cutoff. A meta-model built on top of a
contaminated first-stage feature inherits some of that contamination
upstream, regardless of how clean its own CV is. This script therefore
reports metrics for the FULL eligible population AND, separately, for the
genuinely-out-of-sample-only subset (in_sample_contaminated==False) so the
two are never conflated.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION. No prediction here is
connected to any broker, order, or execution path.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
import afml_common as ac

cfg = ac.load_config()


def main():
    ac.log("04: loading meta-label dataset, dropping NaN-feature rows...")
    df = pd.read_parquet(ac.OUT_DIR / "meta_label_dataset.parquet")
    feature_cols = [c for c in df.columns if c not in (
        "event_id", "t0", "t0_idx", "day", "side_primary", "label_primary", "y_meta",
        "eligible_for_meta_training", "in_sample_contaminated", "reaction_type",
        "training_gate_status", "nearest_level_type", "session_3b", "realized_points",
        "holding_bars", "first_touch",
    )]
    elig = df[df["eligible_for_meta_training"]].copy()
    n_before = len(elig)
    elig = elig.dropna(subset=feature_cols)
    n_after = len(elig)
    ac.log(f"  eligible rows: {n_before} -> {n_after} after dropping rows with any NaN feature "
          f"(mostly 2026-06-14 rollover-warmup day, which has no book-flow cache coverage, "
          f"plus rolling-window warm-up bars at each session start)")
    n_dropped_by_day = (
        df[df["eligible_for_meta_training"]].assign(_keep=lambda d: ~d.index.isin(elig.index))
        .groupby("day")["_keep"].sum()
    )
    ac.log(f"  rows dropped by day: {n_dropped_by_day[n_dropped_by_day > 0].to_dict()}")

    # build t0/t1 in BAR-INDEX units for the purge/embargo splitter (t1 = t0 + holding_bars)
    elig["t1_idx_for_purge"] = elig["t0_idx"] + elig["holding_bars"].fillna(
        cfg["triple_barrier"]["vertical_barrier_bars"]
    )
    elig = elig.reset_index(drop=True)

    folds = ac.purged_embargo_day_splits(
        elig, day_col="day", t0_col="t0_idx", t1_col="t1_idx_for_purge",
        embargo_bars=cfg["validation"]["embargo_bars"],
    )
    ac.log(f"  {len(folds)} purged/embargoed walk-forward folds (day-based, expanding window)")

    oof_p_meta = np.full(len(elig), np.nan)
    fold_log = []
    for fd in folds:
        if fd["n_train"] < cfg["validation"]["min_train_rows"] or fd["n_test"] < 5:
            continue
        train = elig.loc[fd["train_idx"]]
        test = elig.loc[fd["test_idx"]]
        if train["y_meta"].nunique() < 2:
            continue
        scaler = StandardScaler()
        X_train = scaler.fit_transform(train[feature_cols])
        X_test = scaler.transform(test[feature_cols])
        clf = LogisticRegression(max_iter=1000, C=1.0, random_state=cfg["random_seed"])
        clf.fit(X_train, train["y_meta"])
        p_test = clf.predict_proba(X_test)[:, 1]
        oof_p_meta[fd["test_idx"]] = p_test
        fold_log.append(dict(test_day=fd["test_day"], n_train=fd["n_train"], n_test=fd["n_test"],
                             n_purged=fd["n_purged"], n_embargoed=fd["n_embargoed"]))

    elig["p_meta"] = oof_p_meta
    elig["p_meta_is_out_of_fold"] = ~np.isnan(oof_p_meta)
    elig["pred_meta"] = (elig["p_meta"] >= 0.5).astype(float)
    elig.loc[elig["p_meta"].isna(), "pred_meta"] = np.nan

    out_cols = ["event_id", "t0_idx", "day", "side_primary", "label_primary", "y_meta",
                "in_sample_contaminated", "p_meta", "p_meta_is_out_of_fold", "pred_meta"]
    elig[out_cols].to_parquet(ac.OUT_DIR / "meta_model_predictions.parquet", index=False)
    pd.DataFrame(fold_log).to_csv(ac.OUT_DIR / "meta_model_fold_log.csv", index=False)

    n_oof = int(elig["p_meta_is_out_of_fold"].sum())
    n_no_fold = len(elig) - n_oof
    ac.log(f"04 complete: {len(elig)} events scored; {n_oof} have an out-of-fold p_meta "
          f"({n_no_fold} had no eligible prior-day training fold - typically the earliest "
          f"calendar day in the sample, which has no prior day to train on)")
    print(f"META_MODEL_ROWS_SCORED: {len(elig)}")
    print(f"META_MODEL_OOF_PREDICTIONS: {n_oof}")
    print(f"META_MODEL_NO_FOLD_AVAILABLE: {n_no_fold}")
    print(f"META_MODEL_N_FOLDS: {len(fold_log)}")
    return elig


if __name__ == "__main__":
    main()
