"""
05_entry_meta_label.py - Part E: Entry ACT/PASS meta-label.

Primary side (side_primary) already comes from the existing-model-style
label-policy event (Part D - rule-based from reaction_type, NEVER from
future data). The meta-label asks a DIFFERENT, conditional question: GIVEN
that side_primary fired, was acting on it favorable under the existing
model's OWN realized-path label policy (label_h40 - sign of the forward
log-return at the existing model's own +40-bar horizon, day-bounded)?

  y_entry = 1  if (side_primary== 1 and label_h40==1)   [LONG fired, price rose by h40]
                or (side_primary==-1 and label_h40==0)   [SHORT fired, price fell by h40]
  y_entry = 0  otherwise (wrong-direction outcome)
  EXCLUDED   if label_h40 is NaN (NEUTRAL/flat outcome - dropped, exactly
             matching the existing model's OWN training convention of
             dropping NEUTRAL label_h40 rows; never coerced to a class)

This never lets the model "learn side from the future" - side_primary is
fixed BEFORE label_h40 is known (it comes only from the causal reaction
rule), and y_entry only asks whether to ACT or PASS on that already-fixed
side. reaction_type and training_gate_status are preserved as FEATURES
(one-hot rxn_* / categorical gate status), never used to compute y_entry
itself.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v4_common as v4

cfg = v4.load_config()

REACTION_FAMILIES = [
    "rejection_from_above", "rejection_from_below", "absorption",
    "neutral_touch", "breakout_acceptance_above", "breakdown_acceptance_below",
]
LEVEL_TYPES = ["POC", "VAH", "VAL", "HVN", "LVN"]
GATE_STATUSES = ["PRIMARY_USE", "SECONDARY_WATCH", "BLOCKED_NEGATIVE", "SMALL_N", "EXPLORATORY_SMALL_N"]


def reaction_family(rxn: str) -> str:
    for fam in ("breakout_acceptance_above", "breakdown_acceptance_below"):
        if rxn.startswith(fam):
            return fam
    return rxn.rsplit("_", 1)[-1] if "_" not in rxn[:3] else (
        rxn.split("_", 1)[1] if rxn.split("_", 1)[0] in LEVEL_TYPES else rxn)


def main():
    v4.log("05: building Entry ACT/PASS meta-label dataset...")
    deduped = pd.read_parquet(v4.OUT_DIR / "deduped_candidates_v4.parquet")
    deduped = deduped[deduped["has_feature_coverage"]].reset_index(drop=True)
    v4.log(f"  deduped candidates with feature coverage: {len(deduped)}")

    lbl = pd.read_parquet(v4.OUT_DIR / "rebuilt_label_policy_labels.parquet")
    df = deduped.merge(
        lbl[["event_id", "fwd_logret_h40", "fwd_return_ticks_h40", "MFE_h40", "MAE_h40", "label_h40"]],
        on="event_id", how="left")

    n_neutral = int(df["label_h40"].isna().sum())
    df = df.dropna(subset=["label_h40"]).reset_index(drop=True)
    v4.log(f"  dropped {n_neutral} NEUTRAL (label_h40 NaN) rows, matching existing model's own convention")

    favorable = ((df["side_primary"] == 1) & (df["label_h40"] == 1)) | \
                ((df["side_primary"] == -1) & (df["label_h40"] == 0))
    df["y_entry"] = favorable.astype(float)
    df["realized_points_h40"] = df["side_primary"] * df["fwd_return_ticks_h40"] * v4.TICK_SIZE
    df["realized_mfe_side_adjusted"] = np.where(df["side_primary"] == 1, df["MFE_h40"], -df["MAE_h40"])
    df["realized_mae_side_adjusted"] = np.where(df["side_primary"] == 1, df["MAE_h40"], -df["MFE_h40"])

    df["reaction_family"] = df["reaction_type"].apply(reaction_family)
    for fam in REACTION_FAMILIES:
        df[f"rxn_{fam}"] = (df["reaction_family"] == fam).astype(int)
    for lt in LEVEL_TYPES:
        df[f"lvl_{lt}"] = (df["level_type"] == lt).astype(int)
    for gs in GATE_STATUSES:
        df[f"gate_{gs}"] = (df["training_gate_status"] == gs).astype(int)

    panel = pd.read_parquet(v4.OUT_DIR / "v4_feature_panel.parquet")
    panel_feat_cols = [c for c in panel.columns if c not in ("bar_index", "bar_end_ts_ns", "day")]
    df = df.merge(panel[["bar_end_ts_ns"] + panel_feat_cols], on="bar_end_ts_ns", how="left")

    df.to_parquet(v4.OUT_DIR / "entry_meta_label_dataset_v4.parquet", index=False)

    diag_rows = [
        dict(metric="n_deduped_with_feature_coverage", value=len(deduped)),
        dict(metric="n_neutral_dropped_label_h40_nan", value=n_neutral),
        dict(metric="n_final_entry_meta_label_rows", value=len(df)),
        dict(metric="n_y_entry_1_favorable", value=int((df["y_entry"] == 1).sum())),
        dict(metric="n_y_entry_0_unfavorable", value=int((df["y_entry"] == 0).sum())),
        dict(metric="pct_y_entry_1", value=float((df["y_entry"] == 1).mean())),
        dict(metric="n_long_candidates", value=int((df["side_primary"] == 1).sum())),
        dict(metric="n_short_candidates", value=int((df["side_primary"] == -1).sum())),
        dict(metric="mean_realized_points_h40", value=float(df["realized_points_h40"].mean())),
        dict(metric="median_realized_points_h40", value=float(df["realized_points_h40"].median())),
        dict(metric="label_source", value="existing model's own label_h40 (fixed +40-bar horizon, "
                                          "day-bounded, sign of forward log-return) - NOT triple-barrier"),
    ]
    diag_df = pd.DataFrame(diag_rows)
    diag_df.to_csv(v4.OUT_DIR / "entry_label_diagnostics_v4.csv", index=False)

    print(diag_df.to_string(index=False))
    v4.log("05 complete.")
    return df, diag_df


if __name__ == "__main__":
    main()
