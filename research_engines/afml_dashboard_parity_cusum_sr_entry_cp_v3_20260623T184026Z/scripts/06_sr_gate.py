"""
06_sr_gate.py - Part E: support/resistance gate for CUSUM events.

For every CUSUM event (Part D), computes the nearest valid S/R level from
TWO sources:
  1. native_session - the book-flow cache's own per-bar POC/VAH/VAL/HVN/LVN
     (same extraction as ofi_level_decision_tab.py; flagged RESEARCH_ONLY/
     possible intra-session lookahead per the prior institutional audit -
     used here only as a CANDIDATE filter criterion, never as a label).
  2. rolling_deleaked - strictly prior-bar-only rolling POC/VAH/VAL/HVN/LVN
     (v3_common.build_rolling_levels, identical methodology to the prior
     institutional audit's de-leaked validation).
Projected prior-NQM6-contract levels are computed too but marked
level_source='projected_prior' and NEVER set is_near_valid_sr=True by
themselves (display/context only, per build spec).

A candidate can only be created close to a valid (native or rolling-
deleaked) S/R level - this script is the gate; Part F decides side.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v3_common as v3

cfg = v3.load_config()
TOUCH_TICKS = cfg["sr_gate"]["touch_threshold_ticks"]
TICK = v3.TICK_SIZE
LEVEL_TYPES = ["POC", "VAH", "VAL", "HVN", "LVN"]


def load_projected_level_list():
    proj = v3.load_projected_levels()
    seen, out = set(), []
    for _, row in proj.iterrows():
        ltype = str(row.get("level_type", "")); price = float(row.get("projected_level_price", np.nan))
        if not np.isfinite(price):
            continue
        key = (ltype, round(price, 2))
        if key in seen:
            continue
        seen.add(key)
        out.append({"level_type": ltype, "level_price": price})
    return out


def nearest_of(current_price, level_dict, tick=TICK):
    best = None
    for ltype, lprice in level_dict.items():
        if lprice is None or not np.isfinite(lprice):
            continue
        dist_pts = current_price - lprice
        dist_ticks = abs(dist_pts) / tick
        if best is None or dist_ticks < best["distance_ticks"]:
            best = dict(level_type=ltype, level_price=lprice, distance_pts=abs(dist_pts),
                        distance_ticks=dist_ticks, signed_dist_pts=dist_pts)
    return best


def main():
    v3.log("06: building S/R gate for CUSUM events (native + rolling de-leaked + projected context)...")
    events = pd.read_parquet(v3.OUT_DIR / "cusum_events.parquet")
    depth = cfg["scope"]["level_candle_depth"]
    lc = v3.load_level_candles(depth)
    bf_agg = v3.aggregate_book_flow_bars(lc)
    rolling = v3.build_rolling_levels(bf_agg, price_col="close_price", session_col="session_date",
                                       tick_bucket=cfg["sr_gate"]["rolling_level_tick_bucket"],
                                       min_prior_bars=cfg["sr_gate"]["rolling_level_min_prior_bars"])
    proj_levels = load_projected_level_list()

    lvl_cols = ["bar_end_ts_ns", "native_POC", "native_VAH", "native_VAL", "native_HVN", "native_LVN",
                "rolling_poc", "rolling_vah", "rolling_val", "rolling_hvn", "rolling_lvn"]
    lvl_lookup = rolling[lvl_cols].drop_duplicates(subset=["bar_end_ts_ns"])
    ev = events.merge(lvl_lookup, on="bar_end_ts_ns", how="left")

    rows = []
    for _, row in ev.iterrows():
        cp = row["close_at_event"]
        native_dict = {lt: row[f"native_{lt}"] for lt in LEVEL_TYPES}
        rolling_dict = {"POC": row["rolling_poc"], "VAH": row["rolling_vah"], "VAL": row["rolling_val"],
                        "HVN": row["rolling_hvn"], "LVN": row["rolling_lvn"]}
        proj_dict = {f"PROJ_{r['level_type']}": r["level_price"] for r in proj_levels}

        nat = nearest_of(cp, native_dict)
        roll = nearest_of(cp, rolling_dict)
        proj = nearest_of(cp, proj_dict)

        # prefer native if available and within touch distance, else rolling de-leaked
        valid_candidates = [c for c in [
            dict(**nat, level_source="native") if nat else None,
            dict(**roll, level_source="rolling_deleaked") if roll else None,
        ] if c is not None]
        chosen = min(valid_candidates, key=lambda c: c["distance_ticks"]) if valid_candidates else None

        is_near_valid_sr = bool(chosen is not None and chosen["distance_ticks"] <= TOUCH_TICKS)
        support_or_resistance = "n/a"
        if chosen is not None:
            support_or_resistance = "support" if chosen["signed_dist_pts"] >= 0 else "resistance"

        is_near_hvn = bool(chosen is not None and chosen["level_type"] == "HVN" and is_near_valid_sr)
        is_near_lvn = bool(chosen is not None and chosen["level_type"] == "LVN" and is_near_valid_sr)

        rec = dict(
            event_id=row["event_id"],
            nearest_level_type=chosen["level_type"] if chosen else None,
            nearest_level_price=chosen["level_price"] if chosen else np.nan,
            distance_ticks=chosen["distance_ticks"] if chosen else np.nan,
            distance_points=chosen["distance_pts"] if chosen else np.nan,
            signed_distance_pts=chosen["signed_dist_pts"] if chosen else np.nan,
            level_source=chosen["level_source"] if chosen else None,
            support_or_resistance=support_or_resistance,
            is_near_valid_sr=is_near_valid_sr,
            is_near_hvn=is_near_hvn,
            is_near_lvn=is_near_lvn,
            nearest_native_type=nat["level_type"] if nat else None,
            nearest_native_distance_ticks=nat["distance_ticks"] if nat else np.nan,
            nearest_rolling_type=roll["level_type"] if roll else None,
            nearest_rolling_distance_ticks=roll["distance_ticks"] if roll else np.nan,
            nearest_projected_type=proj["level_type"] if proj else None,
            nearest_projected_distance_ticks=proj["distance_ticks"] if proj else np.nan,
            nearest_projected_price=proj["level_price"] if proj else np.nan,
        )
        rows.append(rec)

    gate_df = pd.DataFrame(rows)
    candidates = events.merge(gate_df, on="event_id", how="left")
    candidates.to_parquet(v3.OUT_DIR / "sr_gated_cusum_candidates.parquet", index=False)

    n_total = len(candidates)
    n_valid_sr = int(candidates["is_near_valid_sr"].sum())
    n_hvn = int(candidates["is_near_hvn"].sum())
    n_lvn = int(candidates["is_near_lvn"].sum())

    diag = dict(
        n_cusum_events=n_total, n_near_valid_sr=n_valid_sr,
        n_near_hvn=n_hvn, n_near_lvn=n_lvn,
        n_native_source=int((candidates["level_source"] == "native").sum()),
        n_rolling_source=int((candidates["level_source"] == "rolling_deleaked").sum()),
        n_no_level_found=int(candidates["level_source"].isna().sum()),
        pct_near_valid_sr=float(n_valid_sr / n_total) if n_total else np.nan,
        median_distance_ticks_when_valid=float(candidates.loc[candidates["is_near_valid_sr"], "distance_ticks"].median()),
        touch_threshold_ticks=TOUCH_TICKS,
    )
    by_level_type = candidates[candidates["is_near_valid_sr"]]["nearest_level_type"].value_counts().to_dict()
    diag["by_level_type"] = by_level_type
    diag_df = pd.DataFrame([diag])
    # explode by_level_type into separate rows for csv-friendliness
    diag_rows = [{"metric": k, "value": str(v)} for k, v in diag.items()]
    pd.DataFrame(diag_rows).to_csv(v3.OUT_DIR / "sr_gate_diagnostics.csv", index=False)

    v3.log(f"  {n_total} CUSUM events -> {n_valid_sr} near valid S/R ({100*n_valid_sr/max(n_total,1):.1f}%)")
    v3.log(f"  by level type (when valid): {by_level_type}")
    print(f"SR_GATE_TOTAL_EVENTS: {n_total}")
    print(f"SR_GATE_NEAR_VALID_SR: {n_valid_sr}")
    print(f"SR_GATE_NEAR_HVN: {n_hvn}")
    print(f"SR_GATE_NEAR_LVN: {n_lvn}")
    print(f"SR_GATE_BY_LEVEL_TYPE: {by_level_type}")
    v3.log("06 complete.")
    return candidates, diag_df


if __name__ == "__main__":
    main()
