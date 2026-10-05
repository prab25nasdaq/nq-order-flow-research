"""
03_build_meta_label_dataset.py - AFML meta-labeling dataset.

y_meta = 1{label_primary == 1}: did the primary side produce a gain (PT
touched, or favorable sign at the vertical barrier) before invalidation?
y_meta = 0 otherwise (SL touched, or unfavorable/flat at vertical barrier).

The meta-model must NOT learn side - side_primary==0 (FLAT, no model call)
events are excluded from the meta-training population (config:
meta_labeling.exclude_flat_primary_side), since there is no side for a
secondary model to gate/size.

ALL features are from t0 or strictly earlier:
  - model probability freshness/confidence (already t0-only by construction)
  - reaction_type / training_gate_status (t0 event metadata)
  - book-flow pull-pressure features: rolling z20/roll5sum computed causally
    (pandas .rolling(), backward-looking only, NEVER centered) on the
    book-flow bar series, THEN joined at t0's bar index - the rolling window
    for any bar t uses only bars <= t, so the value attached to an event at
    t0 never sees bar t0+1 or later.
  - volatility/VPIN/regime features: pulled directly from the master, which
    are already-causal production features per the model's own FEATURE_POLICY.md
    (centered_rolling_used=False, shift_minus_1_used=False, bfill_used=False
    - independently audited in that file).
  - level context: nearest_level_type / nearest_level_distance (t0 metadata).
  - session/time: minute_of_day, dow, derived 3-bucket session (pure
    time-of-day functions, leakage-free by construction).

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import afml_common as ac

cfg = ac.load_config()


def assign_session_3bucket(hour_utc: pd.Series) -> pd.Series:
    def _f(h):
        if 6 <= h <= 12:
            return "London"
        elif 13 <= h <= 21:
            return "US"
        else:
            return "Asia_Overnight"
    return hour_utc.map(_f)


def main():
    ac.log("03: loading candidate events + triple-barrier labels + book-flow + master features...")
    events = pd.read_parquet(ac.OUT_DIR / "candidate_events.parquet")
    labels = pd.read_parquet(ac.OUT_DIR / "triple_barrier_labels.parquet")
    df = events.merge(labels[["event_id", "label_primary", "first_touch", "realized_points",
                              "holding_bars", "mfe_points", "mae_points"]], on="event_id", how="left")

    nqu6 = ac.load_nqu6_master().sort_values("bar_index").reset_index(drop=True)
    nqu6["hour_utc"] = pd.to_datetime(nqu6["bar_end_ts_ns"], unit="ns", utc=True).dt.hour
    nqu6["session_3b"] = assign_session_3bucket(nqu6["hour_utc"])
    market_cols = ["bar_index", "volatility_5", "vpin", "regime_ic_mlofi", "cusum_up_break",
                   "cusum_down_break", "mlofi_norm", "delta_norm", "minute_of_day", "dow", "session_3b"]
    df = df.merge(nqu6[market_cols].rename(columns={"bar_index": "t0_idx"}), on="t0_idx", how="left")

    ac.log(f"  building CAUSAL book-flow features (depth={cfg['scope']['level_candle_depth']})...")
    lc = ac.load_level_candles(cfg["scope"]["level_candle_depth"])
    bf = ac.aggregate_book_flow_bars(lc).sort_values(["session_date", "bar_idx"]).reset_index(drop=True)
    for col in ["bid_pull_pressure", "ask_pull_minus_bid_pull", "bid_add_minus_ask_add", "signed_flow", "abs_flow"]:
        # causal: pandas .rolling() is backward-looking only (never centered) -
        # value at row t uses bars [t-window+1, t] within the SAME session_date
        bf[f"{col}_z20"] = bf.groupby("session_date")[col].transform(
            lambda s: (s - s.rolling(20, min_periods=5).mean()) / s.rolling(20, min_periods=5).std().replace(0, np.nan)
        )
        bf[f"{col}_roll5sum"] = bf.groupby("session_date")[col].transform(
            lambda s: s.rolling(5, min_periods=3).sum()
        )
    bf_feat_cols = [c for c in bf.columns if c.endswith("_z20") or c.endswith("_roll5sum")]
    bf_small = bf[["bar_idx"] + bf_feat_cols].rename(columns={"bar_idx": "t0_idx"})
    df = df.merge(bf_small, on="t0_idx", how="left")

    eligible = (df["side_primary"] != 0) if cfg["meta_labeling"]["exclude_flat_primary_side"] else pd.Series(True, index=df.index)
    # also require a resolved triple-barrier label (label_primary not NaN) - a
    # handful of early-session events fall inside the EWM-vol warm-up window
    # (min_periods) and were never labeled in script 02; treating "could not
    # be labeled" as y_meta=0 would silently fabricate a loss that never
    # happened, so those rows are excluded from training, not mislabeled.
    eligible = eligible & df["label_primary"].notna()
    df["eligible_for_meta_training"] = eligible
    df["y_meta"] = np.where(eligible, (df["label_primary"] == 1).astype(float), np.nan)

    # categorical encodings (gate/reaction/level/session) — ordinal/dummy, no
    # external information beyond the category label itself, available at t0
    df["training_gate_status_code"] = df["training_gate_status"].astype("category").cat.codes
    df["reaction_type_code"] = df["reaction_type"].astype("category").cat.codes
    df["nearest_level_type_code"] = df["nearest_level_type"].astype("category").cat.codes
    df["session_3b_code"] = df["session_3b"].astype("category").cat.codes

    feature_cols = (
        ["primary_probability", "primary_confidence", "bars_since_last_event",
         "training_gate_status_code", "reaction_type_code", "nearest_level_type_code",
         "nearest_level_distance", "volatility_target", "volatility_5", "vpin",
         "regime_ic_mlofi", "cusum_up_break", "cusum_down_break", "mlofi_norm", "delta_norm",
         "minute_of_day", "dow", "session_3b_code"]
        + bf_feat_cols
    )
    meta_cols = ["event_id", "t0", "t0_idx", "day", "side_primary", "label_primary", "y_meta",
                 "eligible_for_meta_training", "in_sample_contaminated", "reaction_type",
                 "training_gate_status", "nearest_level_type", "session_3b", "realized_points",
                 "holding_bars", "first_touch"] + feature_cols

    out = df[meta_cols].copy()
    out.to_parquet(ac.OUT_DIR / "meta_label_dataset.parquet", index=False)

    n_eligible = int(eligible.sum())
    n_pos = int((out["y_meta"] == 1).sum())
    n_neg = int((out["y_meta"] == 0).sum())
    ac.log(f"03 complete: {len(out)} rows, {n_eligible} eligible for meta-training "
          f"(y_meta=1: {n_pos}, y_meta=0: {n_neg}), feature columns: {len(feature_cols)}")
    print(f"META_DATASET_ROWS: {len(out)}")
    print(f"META_DATASET_ELIGIBLE: {n_eligible}")
    print(f"META_DATASET_Y_META_POSITIVE: {n_pos}")
    print(f"META_DATASET_Y_META_NEGATIVE: {n_neg}")
    print(f"META_DATASET_FEATURE_COLUMNS: {feature_cols}")
    return out


if __name__ == "__main__":
    main()
