"""
01_level_touch_events.py - Part A: level-touch event universe.

Broader than v4's reaction-rule-gated events: here EVERY touch/near-touch
of a level produces an event, regardless of whether the bar's OHLC pattern
matches one of the existing model's 5 specific reaction rules - this lets
Part C's behavior classification observe genuine NO_REACTION cases, which
by construction never appear in v4's own event stream.

Level sources:
  - NATIVE (per-day volume profile, POC/VAH/VAL/HVN/LVN) - flagged
    RESEARCH_ONLY_LOOKAHEAD_RISK: this level is computed from the day's
    FULL session (including bars after the touch), so using it as
    "known" context for an EARLY-session touch is lookahead. Valid for
    DESCRIPTIVE/EXPLANATORY research (this Atlas's purpose), NOT valid as
    a real-time feature.
  - ROLLING_DELEAKED (strictly prior-bar-only rolling POC/VAH/VAL/HVN/LVN,
    atlas_common.build_rolling_levels) - the leakage-SAFE alternative,
    flagged accordingly.
  - PRIOR_SESSION (previous day's own fully-resolved native POC/VAH/VAL) -
    causally valid: the previous day is complete and known before the
    current day begins.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atlas_common as ac

cfg = ac.load_config()
NEAR_TICKS_PRICE = ac.TICK_SIZE * cfg["touch_detection"]["near_ticks_k"]


def detect_touches_for_day(df: pd.DataFrame, day_str: str, flat_levels: List[Tuple[float, str, str]],
                            event_id_start: int) -> List[Dict[str, Any]]:
    """flat_levels: list of (level_price, level_type, level_source)."""
    rows = []
    h = pd.to_numeric(df["px_high"], errors="coerce").to_numpy()
    l = pd.to_numeric(df["px_low"], errors="coerce").to_numpy()
    c = pd.to_numeric(df["px_close"], errors="coerce").to_numpy()
    prior_close = np.r_[np.nan, c[:-1]]
    eid = event_id_start
    touch_counter: Dict[Tuple[float, str, str], int] = defaultdict(int)
    last_touch_bar: Dict[Tuple[float, str, str], int] = {}
    for i in range(len(df)):
        if not (np.isfinite(h[i]) and np.isfinite(l[i]) and np.isfinite(c[i])):
            continue
        for lp, ltype, lsrc in flat_levels:
            if not np.isfinite(lp):
                continue
            touched = (l[i] - ac.TICK_SIZE * 0.5 <= lp <= h[i] + ac.TICK_SIZE * 0.5)
            close_to = abs(c[i] - lp) <= NEAR_TICKS_PRICE
            if not (touched or close_to):
                continue
            key = (lp, ltype, lsrc)
            came_from_above = np.isfinite(prior_close[i]) and (prior_close[i] > lp + ac.TICK_SIZE * 0.5)
            came_from_below = np.isfinite(prior_close[i]) and (prior_close[i] < lp - ac.TICK_SIZE * 0.5)
            side_of_approach = "FROM_ABOVE" if came_from_above else ("FROM_BELOW" if came_from_below else "AT_LEVEL")
            tc_past = touch_counter[key]
            bars_since_prior = (i - last_touch_bar[key]) if key in last_touch_bar else -1
            touch_counter[key] += 1
            last_touch_bar[key] = i
            rows.append(dict(
                event_id=eid, rithmic_date_str=day_str, bar_idx_in_day=i,
                bar_end_ts_ns=int(df["bar_end_ts_ns"].iloc[i]),
                close=float(c[i]), level_type=ltype, level_source=lsrc, level_price=float(lp),
                distance_ticks=float((c[i] - lp) / ac.TICK_SIZE), distance_points=float(c[i] - lp),
                side_of_approach=side_of_approach, touched_intrabar=bool(touched),
                touch_count_past_only=int(tc_past), bars_since_prior_touch=int(bars_since_prior),
            ))
            eid += 1
    return rows, eid


def main():
    ac.log("01: building level-touch event universe (NQU6, all level types, native+rolling+prior-session)...")
    nqu6 = ac.load_nqu6_master()
    nqu6_scoped = nqu6[nqu6["rithmic_date_str"].isin(cfg["scope"]["days"])].reset_index(drop=True)
    ac.log(f"  NQU6 scoped bars: {len(nqu6_scoped)} across {nqu6_scoped['rithmic_date_str'].nunique()} days")

    # alias for volume_profile_levels() (NQU6: cumulative_roll_adjustment_points==0,
    # so continuous_* == px_* exactly - no re-derivation, just a column rename)
    nqu6_scoped["continuous_high"] = nqu6_scoped["px_high"]
    nqu6_scoped["continuous_low"] = nqu6_scoped["px_low"]

    per_day_levels = {}
    days = sorted(nqu6_scoped["rithmic_date_str"].unique())
    for d in days:
        day_df = nqu6_scoped[nqu6_scoped["rithmic_date_str"] == d].reset_index(drop=True)
        try:
            per_day_levels[d] = ac.volume_profile_levels(day_df)
        except RuntimeError as e:
            ac.log(f"  {d}: SKIP native levels - {e}")

    rolling = ac.build_rolling_levels(nqu6_scoped, price_col="px_close", tick_bucket=2.5, min_prior_bars=30)
    rolling_by_ts = rolling.set_index("bar_end_ts_ns")[["rolling_poc", "rolling_vah", "rolling_val",
                                                        "rolling_hvn", "rolling_lvn"]]

    all_rows = []
    eid_counter = 0
    for i, d in enumerate(days):
        if d not in per_day_levels:
            continue
        day_df = nqu6_scoped[nqu6_scoped["rithmic_date_str"] == d].reset_index(drop=True)
        lv = per_day_levels[d]
        flat_levels = [(lv["poc_px"], "POC", "native"), (lv["vah_px"], "VAH", "native"),
                       (lv["val_px"], "VAL", "native")]
        flat_levels += [(p, "HVN", "native") for p in lv["hvn_px"]]
        flat_levels += [(p, "LVN", "native") for p in lv["lvn_px"]]

        # prior-session levels: previous SCOPED day's own fully-resolved native POC/VAH/VAL
        if i > 0 and days[i - 1] in per_day_levels:
            plv = per_day_levels[days[i - 1]]
            flat_levels += [(plv["poc_px"], "PRIOR_SESSION_POC", "prior_session"),
                           (plv["vah_px"], "PRIOR_SESSION_VAH", "prior_session"),
                           (plv["val_px"], "PRIOR_SESSION_VAL", "prior_session")]

        # rolling de-leaked levels: per-bar values (not flat across the day) - handle separately below
        rows, eid_counter = detect_touches_for_day(day_df, d, flat_levels, eid_counter)
        all_rows.extend(rows)

        # rolling de-leaked touches (per-bar level values, prior-bar-only)
        rday = rolling[rolling["rithmic_date_str"] == d].reset_index(drop=True) if "rithmic_date_str" in rolling.columns else None
        if rday is None:
            rday = nqu6_scoped[nqu6_scoped["rithmic_date_str"] == d].reset_index(drop=True)
            rday = rday.merge(rolling_by_ts, left_on="bar_end_ts_ns", right_index=True, how="left")
        h_ = day_df["px_high"].to_numpy(); l_ = day_df["px_low"].to_numpy(); c_ = day_df["px_close"].to_numpy()
        prior_c = np.r_[np.nan, c_[:-1]]
        for rcol, rtype in [("rolling_poc", "ROLLING_POC"), ("rolling_vah", "ROLLING_VAH"),
                           ("rolling_val", "ROLLING_VAL"), ("rolling_hvn", "ROLLING_HVN"),
                           ("rolling_lvn", "ROLLING_LVN")]:
            rvals = rday[rcol].to_numpy() if rcol in rday.columns else np.full(len(day_df), np.nan)
            for j in range(len(day_df)):
                lp = rvals[j]
                if not np.isfinite(lp) or not (np.isfinite(h_[j]) and np.isfinite(l_[j])):
                    continue
                touched = (l_[j] - ac.TICK_SIZE * 0.5 <= lp <= h_[j] + ac.TICK_SIZE * 0.5)
                close_to = abs(c_[j] - lp) <= NEAR_TICKS_PRICE
                if not (touched or close_to):
                    continue
                came_from_above = np.isfinite(prior_c[j]) and (prior_c[j] > lp + ac.TICK_SIZE * 0.5)
                came_from_below = np.isfinite(prior_c[j]) and (prior_c[j] < lp - ac.TICK_SIZE * 0.5)
                side = "FROM_ABOVE" if came_from_above else ("FROM_BELOW" if came_from_below else "AT_LEVEL")
                all_rows.append(dict(
                    event_id=eid_counter, rithmic_date_str=d, bar_idx_in_day=j,
                    bar_end_ts_ns=int(day_df["bar_end_ts_ns"].iloc[j]),
                    close=float(c_[j]), level_type=rtype, level_source="rolling_deleaked",
                    level_price=float(lp), distance_ticks=float((c_[j] - lp) / ac.TICK_SIZE),
                    distance_points=float(c_[j] - lp), side_of_approach=side, touched_intrabar=bool(touched),
                    touch_count_past_only=-1, bars_since_prior_touch=-1,
                ))
                eid_counter += 1

    ev = pd.DataFrame(all_rows)
    ev["lookahead_risk_flag"] = np.where(
        ev["level_source"] == "native", "RESEARCH_ONLY_LOOKAHEAD_RISK",
        np.where(ev["level_source"] == "rolling_deleaked", "SAFE_CAUSAL", "SAFE_CAUSAL_PRIOR_DAY_KNOWN"))

    # context: session, day, volatility/VPIN/liquidity-cost state (joined via bar_end_ts_ns)
    panel = ac.load_v4_feature_panel()
    nqu6_scoped["minute_of_day"] = pd.to_numeric(nqu6_scoped["minute_of_day"], errors="coerce")
    nqu6_scoped["session"] = nqu6_scoped["minute_of_day"].apply(lambda m: ac.session_label(int(m)) if pd.notna(m) else "UNKNOWN")
    nqu6_scoped["day"] = nqu6_scoped["day"].astype(int)
    nqu6_scoped["vol_state"] = ac.rolling_state_tercile(nqu6_scoped["volatility_5"].to_numpy(),
                                                       window=cfg["state_binning"]["window"],
                                                       min_ref=cfg["state_binning"]["min_ref"])
    ctx_cols = nqu6_scoped[["bar_end_ts_ns", "day", "session", "vol_state"]]
    panel_state = panel[["bar_end_ts_ns", "dash_vpin_state", "dash_toxicity", "dash_liquidity_cost"]].copy()
    panel_state["liquidity_cost_state"] = ac.rolling_state_tercile(
        panel_state["dash_liquidity_cost"].to_numpy(), window=cfg["state_binning"]["window"],
        min_ref=cfg["state_binning"]["min_ref"])
    ev = ev.merge(ctx_cols, on="bar_end_ts_ns", how="left")
    ev = ev.merge(panel_state[["bar_end_ts_ns", "dash_vpin_state", "dash_toxicity", "liquidity_cost_state"]],
                  on="bar_end_ts_ns", how="left")
    ev = ev.rename(columns={"dash_vpin_state": "vpin_state", "dash_toxicity": "toxicity_score"})

    ev.to_parquet(ac.OUT_DIR / "level_touch_events.parquet", index=False)

    diag_rows = [
        dict(metric="n_events_total", value=len(ev)),
        dict(metric="n_days", value=ev["rithmic_date_str"].nunique()),
        dict(metric="n_native_lookahead_risk", value=int((ev["level_source"] == "native").sum())),
        dict(metric="n_rolling_deleaked_safe", value=int((ev["level_source"] == "rolling_deleaked").sum())),
        dict(metric="n_prior_session_safe", value=int((ev["level_source"] == "prior_session").sum())),
        dict(metric="by_level_type", value=str(ev["level_type"].value_counts().to_dict())),
        dict(metric="by_session", value=str(ev["session"].value_counts().to_dict())),
        dict(metric="by_vpin_state", value=str(ev["vpin_state"].value_counts().to_dict())),
        dict(metric="by_vol_state", value=str(ev["vol_state"].value_counts().to_dict())),
        dict(metric="pct_touched_intrabar", value=float(ev["touched_intrabar"].mean())),
    ]
    diag_df = pd.DataFrame(diag_rows)
    diag_df.to_csv(ac.OUT_DIR / "level_touch_event_diagnostics.csv", index=False)

    print(diag_df.to_string(index=False))
    ac.log("01 complete.")
    return ev, diag_df


if __name__ == "__main__":
    main()
