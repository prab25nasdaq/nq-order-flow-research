"""
06_why_levels_work.py - Part G: why dashboard S/R levels work or fail.

Empirical note (carried over from Part F): SUPPORT_FAILED/RESISTANCE_FAILED
never occurred in this dataset under the a-priori thresholds in
atlas_config.yaml - every break either reclaimed quickly (FAKE_BREAKOUT_
SWEEP) or continued strongly (BREAKOUT_ACCEPTANCE). So "held vs failed" is
operationalized here as HELD vs BROKE (BREAKOUT_ACCEPTANCE + FAKE_BREAKOUT_
SWEEP combined) - documented, not silently substituted.

Confluence score: for each swing S/R retouch event, count how many OTHER
dashboard level families (HVN, LVN, POC, VAH/VAL, prior-contract projected
levels) had a level within near_ticks_price of this event's level_price as
of the latest snapshot at/before the event's bar - all dashboard-native
level types, no new method invented.

READ-ONLY. Writes only inside this engine's own outputs/.
SHADOW / RESEARCH ONLY / NO MODEL TRAINING.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sr_common as sc

cfg = sc.load_config()
NEAR_PRICE = cfg["retouch"]["near_ticks_price"]


def mech_summary(g: pd.DataFrame, prefix_cols: list) -> dict:
    out = {"n_events": len(g)}
    for c in prefix_cols:
        if c in g.columns:
            out[f"mean_{c}"] = float(g[c].mean()) if g[c].notna().any() else np.nan
    return out


def main():
    sc.log("06: loading events, behavior, mechanics, levels...")
    events = pd.read_parquet(sc.OUT_DIR / "dashboard_sr_retouch_events.parquet")
    beh = pd.read_parquet(sc.OUT_DIR / "dashboard_sr_behavior_labels.parquet")
    mech = pd.read_parquet(sc.OUT_DIR / "dashboard_sr_orderflow_mechanics.parquet")
    levels = pd.read_parquet(sc.OUT_DIR / "dashboard_sr_levels_by_lookback.parquet")
    proj = pd.read_csv(sc.OUT_DIR / "dashboard_sr_projected_prior_contract_levels_static.csv")

    j = beh.merge(events[["event_id", "bar_t", "level_price", "lookback_window"]].rename(
        columns={"lookback_window": "lookback_window_e"}), on="event_id")
    mech_cols = [c for c in mech.columns if c not in
                 ("event_id", "lookback_window", "level_type", "level_age_bucket", "has_orderflow_mechanics")]
    j = j.merge(mech[["event_id", "has_orderflow_mechanics"] + mech_cols], on="event_id")

    swing = j[j["level_type"].isin(["SWING_RESISTANCE", "SWING_SUPPORT"])].copy()
    swing["worked"] = swing["behavior_label"].isin(["SUPPORT_HELD", "RESISTANCE_HELD"])
    swing["broke"] = swing["behavior_label"].isin(["BREAKOUT_ACCEPTANCE", "FAKE_BREAKOUT_SWEEP"])

    mech_feats = ["touch_support_consumption", "touch_resistance_consumption", "touch_BidPP", "touch_AskPP",
                  "touch_Signed", "pre_20_support_consumption", "pre_20_resistance_consumption",
                  "post_10_support_consumption", "post_10_resistance_consumption", "touch_book_imbalance_mean",
                  "touch_resting_depth_mean"]

    sc.log("  why old support works / fails...")
    old_supp = swing[(swing["approach_side"] == "from_above") &
                      (swing["level_age_bucket"].isin(["age_5000_10000", "age_10000_plus"]))]
    rows = []
    for outcome_name, mask in (("HELD", old_supp["worked"]), ("BROKE", old_supp["broke"])):
        g = old_supp[mask]
        for has_mech, gg in g.groupby("has_orderflow_mechanics"):
            row = {"outcome": outcome_name, "has_orderflow_mechanics": bool(has_mech)}
            row.update(mech_summary(gg, mech_feats))
            rows.append(row)
    pd.DataFrame(rows).to_csv(sc.OUT_DIR / "why_dashboard_old_support_works.csv", index=False)
    sc.log(f"  wrote why_dashboard_old_support_works.csv ({len(rows)} rows, n_old_support_events={len(old_supp)})")

    sc.log("  why old resistance works / fails...")
    old_res = swing[(swing["approach_side"] == "from_below") &
                     (swing["level_age_bucket"].isin(["age_5000_10000", "age_10000_plus"]))]
    rows = []
    for outcome_name, mask in (("HELD", old_res["worked"]), ("BROKE", old_res["broke"])):
        g = old_res[mask]
        for has_mech, gg in g.groupby("has_orderflow_mechanics"):
            row = {"outcome": outcome_name, "has_orderflow_mechanics": bool(has_mech)}
            row.update(mech_summary(gg, mech_feats))
            rows.append(row)
    pd.DataFrame(rows).to_csv(sc.OUT_DIR / "why_dashboard_old_resistance_works.csv", index=False)
    sc.log(f"  wrote why_dashboard_old_resistance_works.csv ({len(rows)} rows, n_old_resistance_events={len(old_res)})")

    sc.log("  why dashboard levels fail (BREAKOUT_ACCEPTANCE vs FAKE_BREAKOUT_SWEEP comparison, all ages)...")
    broke_all = swing[swing["broke"]]
    rows = []
    for label, gg in broke_all.groupby("behavior_label"):
        for has_mech, ggg in gg.groupby("has_orderflow_mechanics"):
            row = {"break_type": label, "has_orderflow_mechanics": bool(has_mech)}
            row.update(mech_summary(ggg, mech_feats))
            rows.append(row)
    pd.DataFrame(rows).to_csv(sc.OUT_DIR / "why_dashboard_old_levels_fail.csv", index=False)
    sc.log(f"  wrote why_dashboard_old_levels_fail.csv ({len(rows)} rows)")

    sc.log("  building confluence score (HVN/LVN/POC/VAH-VAL/prior-session proximity)...")
    vp_levels = levels[~levels["level_type"].isin(["SWING_RESISTANCE", "SWING_SUPPORT"])].copy()
    vp_by_window = {w: g.sort_values("snapshot_bar") for w, g in vp_levels.groupby("lookback_window")}
    proj_prices = proj["projected_level_price"].dropna().to_numpy() if "projected_level_price" in proj.columns else np.array([])

    def confluence_for(row) -> dict:
        w = row["lookback_window_e"]
        bar_t = row["bar_t"]
        lp = row["level_price"]
        vp = vp_by_window.get(w)
        flags = {"confluence_hvn": False, "confluence_lvn": False, "confluence_poc": False,
                 "confluence_vah_val": False, "confluence_prior_session": False}
        if vp is not None and len(vp):
            recent = vp[vp["snapshot_bar"] <= bar_t]
            if len(recent):
                last_snap_bar = recent["snapshot_bar"].max()
                today = recent[recent["snapshot_bar"] == last_snap_bar]
                for lvl_type, key in (("HVN", "confluence_hvn"), ("LVN", "confluence_lvn"),
                                       ("POC", "confluence_poc")):
                    sub = today[today["level_type"] == lvl_type]
                    if len(sub) and (np.abs(sub["level_price"].to_numpy() - lp) <= NEAR_PRICE).any():
                        flags[key] = True
                sub = today[today["level_type"].isin(["VAH", "VAL"])]
                if len(sub) and (np.abs(sub["level_price"].to_numpy() - lp) <= NEAR_PRICE).any():
                    flags["confluence_vah_val"] = True
        if len(proj_prices) and (np.abs(proj_prices - lp) <= NEAR_PRICE).any():
            flags["confluence_prior_session"] = True
        flags["confluence_score"] = sum(1 for k in ("confluence_hvn", "confluence_lvn", "confluence_poc",
                                                       "confluence_vah_val", "confluence_prior_session") if flags[k])
        return flags

    conf = swing.apply(confluence_for, axis=1, result_type="expand")
    swing_conf = pd.concat([swing[["event_id", "lookback_window_e", "level_age_bucket", "behavior_label",
                                    "worked", "broke"]].reset_index(drop=True), conf.reset_index(drop=True)], axis=1)

    conf_rows = []
    for keys, g in swing_conf.groupby(["lookback_window_e", "confluence_score"]):
        wlabel, score = keys
        conf_rows.append({
            "lookback_window": wlabel, "confluence_score": int(score), "n_events": len(g),
            "hold_rate": float(g["worked"].mean()), "break_rate": float(g["broke"].mean()),
            "pct_with_hvn": float(g["confluence_hvn"].mean()), "pct_with_lvn": float(g["confluence_lvn"].mean()),
            "pct_with_poc": float(g["confluence_poc"].mean()), "pct_with_vah_val": float(g["confluence_vah_val"].mean()),
            "pct_with_prior_session": float(g["confluence_prior_session"].mean()),
        })
    conf_df = pd.DataFrame(conf_rows).sort_values(["lookback_window", "confluence_score"])
    conf_path = sc.OUT_DIR / "dashboard_old_level_confluence_report.csv"
    conf_df.to_csv(conf_path, index=False)
    sc.log(f"  wrote {conf_path} ({len(conf_df)} rows)")
    sc.log("06: done.")


if __name__ == "__main__":
    main()
