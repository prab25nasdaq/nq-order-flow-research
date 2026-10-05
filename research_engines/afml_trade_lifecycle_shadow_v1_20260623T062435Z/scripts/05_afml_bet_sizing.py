"""
05_afml_bet_sizing.py - AFML Ch.10 bet sizing from predicted probabilities.

side comes from the primary model (script 01, unchanged). The meta-label
probability (p_meta, out-of-fold from script 04) controls MAGNITUDE only -
it can shrink a bet to zero ("pass") but never flips its side (see
afml_common.meta_prob_to_size docstring for the exact AFML Ch.10 formula and
the explicit meta-labeling no-side-flip adaptation). The signal is then
discretized (AFML Ch.10 discreteSignal) to avoid bet-size jitter.

Events with no out-of-fold p_meta available (script 04) default to
final_side_size = 0 - "no usable prediction" is treated as "no bet", which
is the conservative, non-discretionary default, not an invented trading rule.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION. No size here is ever sent
to a broker, order, or execution path - this is a research ledger column.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import afml_common as ac

cfg = ac.load_config()


def main():
    ac.log("05: loading meta-model predictions, computing AFML bet sizes...")
    df = pd.read_parquet(ac.OUT_DIR / "meta_model_predictions.parquet")

    has_pred = df["p_meta_is_out_of_fold"] & df["p_meta"].notna()
    raw_size = np.zeros(len(df))
    raw_size[has_pred.to_numpy()] = ac.meta_prob_to_size(
        df.loc[has_pred, "p_meta"].to_numpy(),
        df.loc[has_pred, "side_primary"].to_numpy(),
        num_classes=cfg["bet_sizing"]["num_classes"],
    )
    df["raw_size"] = raw_size
    df["discretized_size"] = ac.discretize_signal(df["raw_size"].to_numpy(), cfg["bet_sizing"]["step_size"])
    # final_side_size: the discretized size, but explicitly zeroed for events with
    # no out-of-fold prediction (conservative default - "no usable prediction" = "no bet")
    df["final_side_size"] = np.where(has_pred, df["discretized_size"], 0.0)
    df["is_zero_size"] = np.isclose(df["final_side_size"], 0.0)
    df["is_pass"] = (~has_pred) | df["is_zero_size"]

    out_cols = ["event_id", "t0_idx", "day", "side_primary", "label_primary", "y_meta",
                "in_sample_contaminated", "p_meta", "pred_meta", "raw_size",
                "discretized_size", "final_side_size", "is_zero_size", "is_pass"]
    df[out_cols].to_parquet(ac.OUT_DIR / "bet_sizing_signal.parquet", index=False)

    n = len(df)
    n_pass = int(df["is_pass"].sum())
    n_zero = int(df["is_zero_size"].sum())
    n_active = n - n_pass
    ac.log(f"05 complete: {n} events sized; {n_active} non-pass active sizes, "
          f"{n_zero} zero-sized, {n_pass} pass (incl. no-prediction-available)")
    print(f"BET_SIZING_TOTAL: {n}")
    print(f"BET_SIZING_ACTIVE_NONZERO: {n_active}")
    print(f"BET_SIZING_ZERO_SIZED: {n_zero}")
    print(f"BET_SIZING_PASS: {n_pass}")
    print(f"BET_SIZING_MEAN_ABS_SIZE_ACTIVE: {df.loc[~df['is_pass'], 'final_side_size'].abs().mean():.4f}")
    return df


if __name__ == "__main__":
    main()
