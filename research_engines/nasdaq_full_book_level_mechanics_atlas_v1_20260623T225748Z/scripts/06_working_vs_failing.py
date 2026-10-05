"""
06_working_vs_failing.py - Part F: matched working-vs-failing level
comparisons. For each comparison pair, computes the mean difference and
Cohen's d (standardized effect size) of every candidate "before touch"
mechanics feature between the two groups, then ranks features by |d| to
find the strongest separators.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atlas_common as ac

cfg = ac.load_config()

SEPARATOR_FEATURES = [
    "pre_10__bid_add", "pre_10__bid_pull", "pre_10__ask_add", "pre_10__ask_pull",
    "pre_10__net_bid_flow", "pre_10__net_ask_flow", "pre_10__signed_flow", "pre_10__abs_flow",
    "pre_10__aggressive_buy_ratio", "pre_10__aggressive_sell_ratio", "pre_10__book_imbalance",
    "depth_recovery_speed_PROXY", "queue_replenishment_after_trade_PROXY", "microprice_slope_PROXY",
    "pre_10__master_vpin", "pre_10__dash_toxicity", "pre_10__dash_liquidity_cost",
    "pre_10__dash_cs_spread", "pre_10__master_entropy_score", "pre_10__master_mlofi_norm",
]


def cohens_d(a: pd.Series, b: pd.Series) -> float:
    a, b = a.dropna(), b.dropna()
    if len(a) < 3 or len(b) < 3:
        return np.nan
    pooled_std = np.sqrt(((len(a) - 1) * a.std() ** 2 + (len(b) - 1) * b.std() ** 2) / (len(a) + len(b) - 2))
    return float((a.mean() - b.mean()) / pooled_std) if pooled_std > 0 else np.nan


def compare(df: pd.DataFrame, mask1: pd.Series, mask2: pd.Series, label1: str, label2: str,
            mech: pd.DataFrame) -> pd.DataFrame:
    g1 = mech.loc[mech["event_id"].isin(df.loc[mask1, "event_id"])]
    g2 = mech.loc[mech["event_id"].isin(df.loc[mask2, "event_id"])]
    rows = []
    for feat in SEPARATOR_FEATURES:
        if feat not in mech.columns:
            continue
        m1, m2 = float(g1[feat].mean()), float(g2[feat].mean())
        rows.append(dict(comparison=f"{label1}_vs_{label2}", feature=feat, n1=len(g1), n2=len(g2),
                         mean_group1=m1, mean_group2=m2, diff=m1 - m2, cohens_d=cohens_d(g1[feat], g2[feat])))
    return pd.DataFrame(rows)


def main():
    ac.log("06: matched working-vs-failing level comparisons...")
    df = pd.read_parquet(ac.OUT_DIR / "_why_levels_master_joined.parquet")
    mech = pd.read_parquet(ac.OUT_DIR / "level_touch_full_book_mechanics.parquet")

    comparisons = []

    hvn = df["level_type"] == "HVN"
    comparisons.append(compare(df, hvn & (df["behavior_label"] == "REJECTION"),
                              hvn & df["behavior_label"].isin(["BREAKOUT_ACCEPTANCE", "FAKE_BREAKOUT_SWEEP"]),
                              "HVN_rejection_winners", "HVN_failures", mech))

    lvn = df["level_type"] == "LVN"
    comparisons.append(compare(df, lvn & (df["behavior_label"] == "BREAKOUT_ACCEPTANCE"),
                              lvn & df["behavior_label"].isin(["REJECTION", "FAKE_BREAKOUT_SWEEP"]),
                              "LVN_breakout_winners", "LVN_failures_held", mech))

    poc = df["level_type"] == "POC"
    comparisons.append(compare(df, poc & (df["behavior_label"] == "REJECTION"),
                              poc & (df["behavior_label"] == "BREAKOUT_ACCEPTANCE"),
                              "POC_magnet_rejection", "POC_break_failure", mech))

    high_vpin = df["vpin_state"].isin(["TOXIC", "EXTREME_TOXICITY"])
    low_vpin = df["vpin_state"] == "NORMAL"
    comparisons.append(compare(df, high_vpin, low_vpin, "high_VPIN", "low_VPIN", mech))

    high_liq = df["liquidity_cost_state"] == "HIGH"
    low_liq = df["liquidity_cost_state"] == "LOW"
    comparisons.append(compare(df, high_liq, low_liq, "high_liquidity_cost", "low_liquidity_cost", mech))

    bid_add_z = (df["pre_10__bid_add"] - df["pre_10__bid_add"].mean()) / df["pre_10__bid_add"].std()
    ask_pull_z = (df["pre_10__ask_pull"] - df["pre_10__ask_pull"].mean()) / df["pre_10__ask_pull"].std()
    bullish_score = bid_add_z + ask_pull_z
    strong_bull = bullish_score >= bullish_score.quantile(0.67)
    weak_bull = bullish_score <= bullish_score.quantile(0.33)
    comparisons.append(compare(df, strong_bull, weak_bull, "strong_bidAdd_askPull", "weak_bidAdd_askPull", mech))

    bid_pull_z = (df["pre_10__bid_pull"] - df["pre_10__bid_pull"].mean()) / df["pre_10__bid_pull"].std()
    ask_add_z = (df["pre_10__ask_add"] - df["pre_10__ask_add"].mean()) / df["pre_10__ask_add"].std()
    bearish_score = bid_pull_z + ask_add_z
    strong_bear = bearish_score >= bearish_score.quantile(0.67)
    weak_bear = bearish_score <= bearish_score.quantile(0.33)
    comparisons.append(compare(df, strong_bear, weak_bear, "strong_bidPull_askAdd", "weak_bidPull_askAdd", mech))

    all_comp = pd.concat(comparisons, ignore_index=True)
    all_comp.to_csv(ac.OUT_DIR / "working_vs_failing_level_differences.csv", index=False)

    top_sep = (all_comp.dropna(subset=["cohens_d"])
              .assign(abs_d=lambda x: x["cohens_d"].abs())
              .sort_values("abs_d", ascending=False)
              .groupby("comparison").head(5)
              .drop(columns=["abs_d"]))
    top_sep.to_csv(ac.OUT_DIR / "top_mechanics_separators.csv", index=False)

    ac.log(f"  comparisons: {all_comp['comparison'].nunique()}  rows: {len(all_comp)}")
    print(top_sep.to_string(index=False))
    ac.log("06 complete.")
    return all_comp, top_sep


if __name__ == "__main__":
    main()
