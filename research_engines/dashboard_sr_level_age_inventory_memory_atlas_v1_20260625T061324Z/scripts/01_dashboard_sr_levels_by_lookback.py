"""
01_dashboard_sr_levels_by_lookback.py - Part B: rebuild the dashboard's OWN
S/R formula (sr_common.compute_sr_levels, a verbatim port of the dashboard's
swing_levels()/compute_sr_levels(), lb=4, cluster_dist=6.0) at fixed,
past-only lookback windows: 500 / 1000 / 2500 / 5000 / 10000 / full-history.

DESIGN (documented, not a silent shortcut):
- The dashboard's own call site computes win = last `window_bars` bars
  ending at the user's current position, then calls compute_sr_levels(win).
  We reproduce this exactly: win = df.iloc[max(0,t-W+1):t+1].
- Recomputing this at literally every one of 19,862 bars x 6 windows would
  be wasteful (levels rarely change bar-to-bar). We snapshot every
  SNAPSHOT_STRIDE=20 bars, which is fine-grained relative to the swing
  confirmation lag (lb=4 bars) and the events this feeds (retouches, which
  are themselves multi-bar phenomena). This is a compute-budget choice, not
  a change to the formula itself - documented in the leakage audit.
- Level identity across snapshots is tracked by nearest-price match (within
  cluster_dist) to the immediately preceding snapshot's level of the same
  (window, type) - this lets level_age_bars / current_touch_count evolve
  correctly over a level's life instead of minting a fresh id every snapshot.
- A level born by swing index i is only created starting at snapshot bars
  t >= i + lb (the dashboard's own confirmation lag - see Part A audit). No
  level is ever backdated to "exist" before its swing point could actually
  have been confirmed.
- POC/VAH/VAL/HVN/LVN (volume_profile_levels, also a verbatim dashboard
  port) are snapshotted once per session day (a coarser, explicitly
  documented cadence - this level family is SECONDARY/confluence-only in
  this atlas, not the primary retouch-event driver).

READ-ONLY against the dashboard source and master files. Writes only inside
this engine's own outputs/. SHADOW / RESEARCH ONLY / NO MODEL TRAINING.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sr_common as sc

cfg = sc.load_config()
LB = cfg["dashboard_sr_formula"]["swing_lb"]
CLUSTER_DIST = cfg["dashboard_sr_formula"]["cluster_dist"]
WINDOWS = cfg["lookback_windows_bars"]
SNAPSHOT_STRIDE = 20


def window_label(w: int) -> str:
    return "full_history" if w == -1 else f"{w}bar"


def main():
    sc.log("01: loading continuous backadjusted master...")
    df = sc.load_continuous_master()
    n = len(df)
    sc.log(f"  {n} bars, {df['session_date'].nunique()} session days")

    ts_col = "timestamp_utc" if "timestamp_utc" in df.columns else "timestamp"
    ts_arr = df[ts_col].to_numpy()
    bar_end_ts_ns = pd.to_numeric(df["bar_end_ts_ns"], errors="coerce").to_numpy()

    snapshot_bars = list(range(0, n, SNAPSHOT_STRIDE))
    if snapshot_bars[-1] != n - 1:
        snapshot_bars.append(n - 1)
    sc.log(f"  {len(snapshot_bars)} snapshot bars (stride={SNAPSHOT_STRIDE})")

    # day-start bars for the coarser volume-profile cadence
    day_change = df["session_date"] != df["session_date"].shift(1)
    day_start_bars = df.index[day_change].tolist()

    all_rows = []
    diag_rows = []
    leakage_rows = []

    for W in WINDOWS:
        wlabel = window_label(W)
        sc.log(f"  window={wlabel} : swing S/R snapshots...")
        # level_id tracking state: dict[(type)] -> list of dict(level_id, price, created_at_bar)
        active = {"R": [], "S": []}
        next_id = {"R": 0, "S": 0}
        n_levels_seen = 0
        n_snapshots_with_levels = 0

        for t in snapshot_bars:
            eff_w = (t + 1) if W == -1 else min(W, t + 1)
            start = max(0, t - eff_w + 1)
            win_df = df.iloc[start:t + 1]
            if len(win_df) < (2 * LB + 1):
                continue
            r_levels, s_levels = sc.compute_sr_levels(win_df, lb=LB, cluster_dist=CLUSTER_DIST)
            if r_levels or s_levels:
                n_snapshots_with_levels += 1

            for side, levels in (("R", r_levels), ("S", s_levels)):
                seen_ids_this_snap = set()
                for med_px, count, score, bi_rel in levels:
                    bi_abs = start + bi_rel  # absolute index of the most-recent swing in this cluster
                    # confirmation-lag check: this swing cannot be "known" until bar bi_abs+LB
                    if bi_abs + LB > t:
                        continue
                    # match to an existing tracked level (nearest price within cluster_dist)
                    match = None
                    best_d = None
                    for lv in active[side]:
                        if lv["id"] in seen_ids_this_snap:
                            continue
                        d = abs(lv["price"] - med_px)
                        if d <= CLUSTER_DIST and (best_d is None or d < best_d):
                            match = lv
                            best_d = d
                    if match is None:
                        lvl_id = f"{side}_{wlabel}_{next_id[side]}"
                        next_id[side] += 1
                        created_at_bar = bi_abs
                        match = {"id": lvl_id, "price": med_px, "created_at_bar": created_at_bar}
                        active[side].append(match)
                        n_levels_seen += 1
                    else:
                        match["price"] = med_px  # drift the tracked price to the latest cluster median
                    seen_ids_this_snap.add(match["id"])

                    created_at_bar = match["created_at_bar"]
                    level_age_bars = t - created_at_bar
                    level_age_minutes = None
                    try:
                        dt = (pd.Timestamp(bar_end_ts_ns[t]) - pd.Timestamp(bar_end_ts_ns[created_at_bar]))
                        level_age_minutes = dt.total_seconds() / 60.0
                    except Exception:
                        pass

                    all_rows.append({
                        "level_id": match["id"],
                        "level_price": float(med_px),
                        "level_type": "SWING_RESISTANCE" if side == "R" else "SWING_SUPPORT",
                        "dashboard_level_source": "compute_sr_levels",
                        "lookback_window": wlabel,
                        "created_at_bar": int(created_at_bar),
                        "created_at_timestamp": str(ts_arr[created_at_bar]),
                        "snapshot_bar": int(t),
                        "snapshot_timestamp": str(ts_arr[t]),
                        "level_age_bars": int(level_age_bars),
                        "level_age_minutes": level_age_minutes,
                        "current_touch_count_past_only": int(count),
                        "current_score_past_only": float(score),
                        "source_is_past_only": True,
                        "source_leakage_risk": "LOW",
                    })
            # prune levels that fell out of the window entirely (price- and
            # age-wise they can no longer be regenerated by this window size)
            for side in ("R", "S"):
                active[side] = [lv for lv in active[side] if t - lv["created_at_bar"] <= max(W, 0) or W == -1]

        diag_rows.append({
            "lookback_window": wlabel, "level_family": "swing_S/R",
            "n_snapshots": len(snapshot_bars), "n_snapshots_with_levels": n_snapshots_with_levels,
            "n_distinct_levels_tracked": n_levels_seen,
        })
        leakage_rows.append({
            "lookback_window": wlabel, "level_family": "swing_S/R",
            "source_is_past_only": True, "source_leakage_risk": "LOW",
            "reason": "confirmation-lag (swing birth+lb) enforced before a level can appear; "
                      "window itself is df.iloc[t-W+1:t+1], never includes bar>t",
        })

        # ── volume profile (POC/VAH/VAL/HVN/LVN), once-per-day cadence ──
        sc.log(f"  window={wlabel} : volume profile (daily cadence)...")
        n_vp_days = 0
        for t in day_start_bars:
            if t == 0:
                continue  # no prior history at all for the very first day
            eff_w = t if W == -1 else min(W, t)
            start = max(0, t - eff_w)
            win_df = df.iloc[start:t]  # strictly PRIOR bars only (this day hasn't started yet)
            if len(win_df) < 20:
                continue
            vp = sc.volume_profile_levels(win_df)
            if not vp:
                continue
            n_vp_days += 1
            created_at_bar = t  # the snapshot itself is the "as of" reference; volume profile has no
                                # single birth bar (it's a distributional summary of the whole window)
            for lvl_type, px in (("POC", vp["poc_px"]), ("VAH", vp["vah_px"]), ("VAL", vp["val_px"])):
                all_rows.append({
                    "level_id": f"VP_{wlabel}_{lvl_type}_{t}",
                    "level_price": float(px), "level_type": lvl_type,
                    "dashboard_level_source": "volume_profile_levels_fixed_window",
                    "lookback_window": wlabel, "created_at_bar": int(t),
                    "created_at_timestamp": str(ts_arr[t]), "snapshot_bar": int(t),
                    "snapshot_timestamp": str(ts_arr[t]), "level_age_bars": 0,
                    "level_age_minutes": 0.0, "current_touch_count_past_only": np.nan,
                    "current_score_past_only": np.nan, "source_is_past_only": True,
                    "source_leakage_risk": "LOW",
                })
            for lvl_type, pxlist in (("HVN", vp["hvn_px"]), ("LVN", vp["lvn_px"])):
                for px in pxlist:
                    all_rows.append({
                        "level_id": f"VP_{wlabel}_{lvl_type}_{t}_{px}",
                        "level_price": float(px), "level_type": lvl_type,
                        "dashboard_level_source": "volume_profile_levels_fixed_window",
                        "lookback_window": wlabel, "created_at_bar": int(t),
                        "created_at_timestamp": str(ts_arr[t]), "snapshot_bar": int(t),
                        "snapshot_timestamp": str(ts_arr[t]), "level_age_bars": 0,
                        "level_age_minutes": 0.0, "current_touch_count_past_only": np.nan,
                        "current_score_past_only": np.nan, "source_is_past_only": True,
                        "source_leakage_risk": "LOW",
                    })
        diag_rows.append({
            "lookback_window": wlabel, "level_family": "volume_profile_POC_VAH_VAL_HVN_LVN",
            "n_snapshots": len(day_start_bars) - 1, "n_snapshots_with_levels": n_vp_days,
            "n_distinct_levels_tracked": np.nan,
        })
        leakage_rows.append({
            "lookback_window": wlabel, "level_family": "volume_profile_POC_VAH_VAL_HVN_LVN",
            "source_is_past_only": True, "source_leakage_risk": "LOW",
            "reason": "window = df.iloc[t-W:t] (strictly prior bars, this day excluded); "
                      "formula identical to dashboard's, only the bar-range source differs "
                      "(fixed window vs live ax_price.get_xlim() zoom - the zoom variant is "
                      "DISPLAY_BEHAVIOR_ONLY and is not used anywhere in this atlas)",
        })

    out = pd.DataFrame(all_rows)
    out_path = sc.OUT_DIR / "dashboard_sr_levels_by_lookback.parquet"
    out.to_parquet(out_path, index=False)
    sc.log(f"  wrote {out_path} ({len(out)} rows)")

    diag_df = pd.DataFrame(diag_rows)
    diag_path = sc.OUT_DIR / "dashboard_sr_levels_by_lookback_diagnostics.csv"
    diag_df.to_csv(diag_path, index=False)
    sc.log(f"  wrote {diag_path}")

    leak_df = pd.DataFrame(leakage_rows)
    leak_path = sc.OUT_DIR / "dashboard_sr_leakage_audit.csv"
    leak_df.to_csv(leak_path, index=False)
    sc.log(f"  wrote {leak_path}")

    # also persist the projected (prior-contract) static levels, read-only, unmodified
    proj_path = Path("/home/prabh/OFI_Live_Features/projected_levels_NQM6_to_NQU6.csv")
    if proj_path.exists():
        proj = pd.read_csv(proj_path)
        proj_out = sc.OUT_DIR / "dashboard_sr_projected_prior_contract_levels_static.csv"
        proj.to_csv(proj_out, index=False)
        sc.log(f"  copied static projected-levels reference -> {proj_out} ({len(proj)} rows)")

    sc.log("01: done.")


if __name__ == "__main__":
    main()
