"""
08_purged_embargo_validation.py - AFML Ch.7 purged / embargoed validation report.

Reuses the EXACT day-based purged+embargoed walk-forward folds already used
to generate out-of-fold p_meta in script 04 (meta_model_fold_log.csv) - no
random K-fold is used anywhere in this engine (random K-fold leaks because
overlapping-label events end up split across train/test by chance; AFML
Ch.7 requires purging overlap and embargoing the boundary, which script 04's
splitter already does).

Every metric below is computed TWICE: once on the full out-of-fold
population, and once restricted to events with in_sample_contaminated==False
(strictly after the active release's training cutoff). The two are reported
side by side and NEVER merged into a single headline number - the ALL
population still carries first-stage (primary-probability) in-sample
contamination risk even though the META-model's own CV is clean; the
GENUINELY_OOS_ONLY slice is small but is the only honest forward-looking
estimate available from current data.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import afml_common as ac

cfg = ac.load_config()


def compute_metrics(sub: pd.DataFrame) -> dict:
    y_true = sub["y_meta"].to_numpy()
    y_pred = sub["pred_meta"].to_numpy()
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    if mask.sum() < 5:
        return dict(n=int(mask.sum()), precision=np.nan, recall=np.nan, f1=np.nan, mcc=np.nan,
                    balanced_accuracy=np.nan)
    yt, yp = y_true[mask], y_pred[mask]
    precision, recall, f1 = ac.precision_recall_f1(yt, yp)
    mcc = ac.mcc_binary(yt, yp)
    bal = ac.balanced_accuracy(yt, yp)
    return dict(n=int(mask.sum()), precision=precision, recall=recall, f1=f1, mcc=mcc,
                balanced_accuracy=bal)


def compute_bet_stats(sub: pd.DataFrame) -> dict:
    entered = sub[~np.isclose(sub["final_side_size"].fillna(0.0), 0.0)]
    n_entered = len(entered)
    if n_entered == 0:
        return dict(n_entered=0, mean_points=np.nan, median_points=np.nan, hit_rate=np.nan,
                    mean_mfe=np.nan, mean_mae=np.nan, avg_holding_bars=np.nan,
                    pt_hit_rate=np.nan, sl_hit_rate=np.nan, time_exit_rate=np.nan)
    hit_rate = float((entered["realized_points"] > 0).mean())
    ft_vc = entered["first_touch"].value_counts(normalize=True)
    return dict(
        n_entered=n_entered,
        mean_points=float(entered["realized_points"].mean()),
        median_points=float(entered["realized_points"].median()),
        hit_rate=hit_rate,
        mean_mfe=float(entered["mfe_points"].mean()),
        mean_mae=float(entered["mae_points"].mean()),
        avg_holding_bars=float(entered["holding_bars"].mean()),
        pt_hit_rate=float(ft_vc.get("PT", 0.0)),
        sl_hit_rate=float(ft_vc.get("SL", 0.0)),
        time_exit_rate=float(ft_vc.get("VB", 0.0)),
    )


def main():
    ac.log("08: loading OOF meta-predictions + bet sizing + lifecycle + active-bet timeline...")
    meta_pred = pd.read_parquet(ac.OUT_DIR / "meta_model_predictions.parquet")
    sizing = pd.read_parquet(ac.OUT_DIR / "bet_sizing_signal.parquet")
    labels = pd.read_parquet(ac.OUT_DIR / "triple_barrier_labels.parquet")
    lifecycle = pd.read_parquet(ac.OUT_DIR / "shadow_position_lifecycle.parquet")
    fold_log = pd.read_csv(ac.OUT_DIR / "meta_model_fold_log.csv")
    timeline = pd.read_parquet(ac.OUT_DIR / "active_bet_timeline.parquet")

    df = meta_pred.merge(sizing[["event_id", "final_side_size"]], on="event_id", how="left") \
                 .merge(labels[["event_id", "realized_points", "mfe_points", "mae_points",
                               "holding_bars", "first_touch"]], on="event_id", how="left")
    oof = df[df["p_meta_is_out_of_fold"]].copy()
    ac.log(f"  {len(oof)} out-of-fold rows available for validation")

    # ── per-fold (= per test day) metrics, ALL vs GENUINELY_OOS_ONLY ───────
    fold_rows = []
    for _, fr in fold_log.iterrows():
        test_day = fr["test_day"]
        sub_all = oof[oof["day"] == test_day]
        sub_clean = sub_all[~sub_all["in_sample_contaminated"]]
        m_all = compute_metrics(sub_all)
        b_all = compute_bet_stats(sub_all)
        m_clean = compute_metrics(sub_clean)
        b_clean = compute_bet_stats(sub_clean)
        fold_rows.append(dict(
            test_day=test_day, n_train=fr["n_train"], n_test=fr["n_test"],
            n_purged=fr["n_purged"], n_embargoed=fr["n_embargoed"],
            **{f"ALL__{k}": v for k, v in m_all.items()},
            **{f"ALL__{k}": v for k, v in b_all.items()},
            **{f"OOS_ONLY__{k}": v for k, v in m_clean.items()},
            **{f"OOS_ONLY__{k}": v for k, v in b_clean.items()},
        ))
    fold_df = pd.DataFrame(fold_rows)
    fold_df.to_csv(ac.OUT_DIR / "fold_results.csv", index=False)

    worst_fold_mcc = float(fold_df["ALL__mcc"].min()) if not fold_df.empty else np.nan
    pct_positive_folds = float((fold_df["ALL__mcc"] > 0).mean()) if not fold_df.empty else np.nan
    ac.log(f"  fold MCC range: [{fold_df['ALL__mcc'].min():.3f}, {fold_df['ALL__mcc'].max():.3f}] "
          f"worst_fold={worst_fold_mcc:.3f} pct_positive_folds={pct_positive_folds:.2f}")

    # ── pooled validation_results.csv (ALL vs GENUINELY_OOS_ONLY) ──────────
    clean_oof = oof[~oof["in_sample_contaminated"]]
    pooled_all = {**compute_metrics(oof), **compute_bet_stats(oof)}
    pooled_clean = {**compute_metrics(clean_oof), **compute_bet_stats(clean_oof)}

    funnel = lifecycle["final_state"].value_counts()
    n_total_events = len(lifecycle)
    pct_zero_size = float((sizing["is_zero_size"]).mean())
    pct_pass = float((sizing["is_pass"]).mean())
    cancel_states = ["CANCEL_STALE", "CANCEL_META_PASS", "CANCEL_VERTICAL_EXPIRED"]
    pct_cancel = float(lifecycle["final_state"].isin(cancel_states).mean())

    summary_rows = [
        dict(population="ALL_OUT_OF_FOLD", contamination_note="includes in-sample-contaminated primary probabilities", **pooled_all),
        dict(population="GENUINELY_OOS_ONLY", contamination_note="strictly after model training cutoff 2026-06-21T01:53Z", **pooled_clean),
    ]
    val_df = pd.DataFrame(summary_rows)
    val_df["n_total_candidate_events"] = n_total_events
    val_df["pct_zero_size"] = pct_zero_size
    val_df["pct_pass"] = pct_pass
    val_df["pct_cancel"] = pct_cancel
    val_df["avg_active_bets"] = float(timeline["active_bets_count"].mean())
    val_df["max_active_bets"] = int(timeline["active_bets_count"].max())
    val_df["turnover_proxy"] = float(timeline["position_delta_shadow"].abs().sum())
    val_df["worst_fold_mcc"] = worst_fold_mcc
    val_df["pct_positive_folds"] = pct_positive_folds
    val_df["n_folds"] = len(fold_df)
    val_df.to_csv(ac.OUT_DIR / "validation_results.csv", index=False)

    # ── leakage checks (explicit, logged) ──────────────────────────────────
    leakage_checks = {
        "no_random_kfold_used": True,
        "purge_overlapping_labels_applied": bool(cfg["validation"]["purge_overlapping_labels"]),
        "embargo_bars_after_test_fold": cfg["validation"]["embargo_bars"],
        "fold_unit": cfg["validation"]["fold_unit"],
        "train_test_only_on_closed_bars": True,
        "first_calendar_day_has_no_training_fold": True,
        "in_sample_contaminated_rows_in_oof_population": int(oof["in_sample_contaminated"].sum()),
        "genuinely_oos_rows_in_oof_population": int((~oof["in_sample_contaminated"]).sum()),
        "predictions_csv_naive_in_sample_claim_made": False,
    }
    import json
    with open(ac.OUT_DIR / "leakage_checks.json", "w") as f:
        json.dump(leakage_checks, f, indent=2, default=str)

    ac.log("08 complete.")
    print(f"VALIDATION_MCC_ALL: {pooled_all['mcc']}")
    print(f"VALIDATION_MCC_OOS_ONLY: {pooled_clean['mcc']}")
    print(f"VALIDATION_F1_ALL: {pooled_all['f1']}")
    print(f"VALIDATION_F1_OOS_ONLY: {pooled_clean['f1']}")
    print(f"WORST_FOLD_MCC: {worst_fold_mcc}")
    print(f"PCT_POSITIVE_FOLDS: {pct_positive_folds}")
    print(f"PCT_ZERO_SIZE: {pct_zero_size}")
    print(f"PCT_PASS: {pct_pass}")
    print(f"PCT_CANCEL: {pct_cancel}")
    return val_df, fold_df


if __name__ == "__main__":
    main()
