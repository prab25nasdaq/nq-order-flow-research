"""
05_why_levels_work.py - Part E: explain why levels work / fail.

Joins level_touch_events + level_behavior_labels + level_touch_mfe_mae +
level_touch_full_book_mechanics (pre-touch window) + v4's own reaction_type
(where the broader touch matches one of v4's reaction-rule-gated events),
then groups by level_type / behavior_label / session / volatility regime /
VPIN-toxicity regime / liquidity-cost regime / approach side / v4
reaction_type, computing the requested mechanics statistics for each group.

"WORKING" = behavior_label in {REJECTION, ABSORPTION} (the level held).
"FAILING" = behavior_label in {BREAKOUT_ACCEPTANCE, FAKE_BREAKOUT_SWEEP}
(the level did not hold). NO_REACTION is reported in the full table only
(neither clearly working nor failing).

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atlas_common as ac

cfg = ac.load_config()
WORKING_LABELS = {"REJECTION", "ABSORPTION"}
FAILING_LABELS = {"BREAKOUT_ACCEPTANCE", "FAKE_BREAKOUT_SWEEP"}


def build_master() -> pd.DataFrame:
    ev = pd.read_parquet(ac.OUT_DIR / "level_touch_events.parquet")
    lbl = pd.read_parquet(ac.OUT_DIR / "level_behavior_labels.parquet")
    mfe = pd.read_parquet(ac.OUT_DIR / "level_touch_mfe_mae.parquet")
    mech = pd.read_parquet(ac.OUT_DIR / "level_touch_full_book_mechanics.parquet")

    df = ev.merge(lbl[["event_id", "behavior_label"]], on="event_id", how="left")
    df = df.merge(mfe[["event_id", "reject_mfe_40", "reject_mae_40", "reject_final_return_40",
                       "breakout_mfe_40", "breakout_mae_40", "breakout_final_return_40"]],
                  on="event_id", how="left")
    mech_cols = ["event_id", "pre_10__signed_flow", "pre_10__bid_pull_pressure", "pre_10__ask_pull_pressure",
                "pre_10__bid_add", "pre_10__bid_pull", "pre_10__ask_add", "pre_10__ask_pull",
                "pre_10__aggressive_buy_ratio", "pre_10__aggressive_sell_ratio",
                "queue_replenishment_after_trade_PROXY", "depth_recovery_speed_PROXY"]
    mech_avail = [c for c in mech_cols if c in mech.columns]
    df = df.merge(mech[mech_avail], on="event_id", how="left")

    # v4's own reaction_type where this broader touch matches one of v4's
    # reaction-rule-gated events (same bar + level_type + level_price)
    v4ev = ac.load_v4_deduped_candidates()
    v4_match = v4ev[["bar_end_ts_ns", "level_type", "nearest_level_price", "reaction_type",
                     "training_gate_status"]].rename(columns={"nearest_level_price": "level_price"})
    df = df.merge(v4_match, on=["bar_end_ts_ns", "level_type", "level_price"], how="left")

    df["liquidity_added"] = df[["pre_10__bid_add", "pre_10__ask_add"]].sum(axis=1, skipna=True)
    df["liquidity_pulled"] = df[["pre_10__bid_pull", "pre_10__ask_pull"]].sum(axis=1, skipna=True)
    df["aggressive_imbalance"] = df["pre_10__aggressive_buy_ratio"] - df["pre_10__aggressive_sell_ratio"]
    df["working"] = df["behavior_label"].isin(WORKING_LABELS)
    df["failing"] = df["behavior_label"].isin(FAILING_LABELS)
    return df


def summarize(df: pd.DataFrame, group_col) -> pd.DataFrame:
    rows = []
    for g, sub in df.groupby(group_col, dropna=False):
        n = len(sub)
        if n < 3:
            continue
        rows.append(dict(
            group=str(g), event_count=n,
            rejection_rate=float((sub["behavior_label"] == "REJECTION").mean()),
            breakout_acceptance_rate=float((sub["behavior_label"] == "BREAKOUT_ACCEPTANCE").mean()),
            fake_breakout_rate=float((sub["behavior_label"] == "FAKE_BREAKOUT_SWEEP").mean()),
            absorption_rate=float((sub["behavior_label"] == "ABSORPTION").mean()),
            no_reaction_rate=float((sub["behavior_label"] == "NO_REACTION").mean()),
            mean_mfe=float(sub["reject_mfe_40"].mean()), mean_mae=float(sub["reject_mae_40"].mean()),
            mfe_mae_ratio=float(sub["reject_mfe_40"].mean() / -sub["reject_mae_40"].mean())
                if sub["reject_mae_40"].mean() < 0 else np.nan,
            mean_signed_flow_before_touch=float(sub["pre_10__signed_flow"].mean()),
            mean_bid_pull_pressure_before_touch=float(sub["pre_10__bid_pull_pressure"].mean()),
            mean_ask_pull_pressure_before_touch=float(sub["pre_10__ask_pull_pressure"].mean()),
            mean_liquidity_added=float(sub["liquidity_added"].mean()),
            mean_liquidity_pulled=float(sub["liquidity_pulled"].mean()),
            mean_aggressive_imbalance=float(sub["aggressive_imbalance"].mean()),
            mean_replenishment_after_touch=float(sub["queue_replenishment_after_trade_PROXY"].mean()),
        ))
    return pd.DataFrame(rows).sort_values("event_count", ascending=False)


def main():
    ac.log("05: explaining why levels work/fail (grouped mechanics summaries)...")
    df = build_master()
    df.to_parquet(ac.OUT_DIR / "_why_levels_master_joined.parquet", index=False)

    group_dims = {
        "level_type": "level_type", "behavior_label": "behavior_label", "session": "session",
        "vol_state": "vol_state", "vpin_state": "vpin_state", "liquidity_cost_state": "liquidity_cost_state",
        "side_of_approach": "side_of_approach", "reaction_type": "reaction_type",
    }
    full_tables = {name: summarize(df, col) for name, col in group_dims.items()}

    by_type_behavior = summarize(df, ["level_type", "behavior_label"])
    working = df[df["working"]]
    failing = df[df["failing"]]
    work_summary = summarize(working, ["level_type", "session"])
    fail_summary = summarize(failing, ["level_type", "session"])
    work_summary.to_csv(ac.OUT_DIR / "why_levels_work_summary.csv", index=False)
    fail_summary.to_csv(ac.OUT_DIR / "why_levels_fail_summary.csv", index=False)

    for lvl in ["HVN", "LVN", "POC"]:
        sub = df[df["level_type"] == lvl]
        rep = pd.concat([
            summarize(sub, "behavior_label").assign(dimension="behavior_label"),
            summarize(sub, "session").assign(dimension="session"),
            summarize(sub, "vol_state").assign(dimension="vol_state"),
            summarize(sub, "vpin_state").assign(dimension="vpin_state"),
            summarize(sub, "liquidity_cost_state").assign(dimension="liquidity_cost_state"),
            summarize(sub, "side_of_approach").assign(dimension="side_of_approach"),
        ], ignore_index=True)
        rep.to_csv(ac.OUT_DIR / f"{lvl.lower()}_mechanics_report.csv", index=False)

    ac.log(f"  master joined rows: {len(df)}")
    print("By level_type x behavior_label (top rows):")
    print(by_type_behavior.head(10).to_string(index=False))
    ac.log("05 complete.")
    return df, full_tables


if __name__ == "__main__":
    main()
