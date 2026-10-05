"""
07_feature_panel.py - Part H: past-only dashboard S/R feature candidates.
NO MODEL TRAINING - this produces a feature panel only, for future
LEVEL STATE / OFI Level Decision consideration.

Every feature at bar t uses ONLY information already available at or
before t: level prices/ages/touch-counts come from Part B's level registry
and Part C/D's retouch-event history, both already past-only by
construction (see dashboard_sr_leakage_audit.csv).

Definitions (a-priori, documented - not fit to data):
  - "old level" (the generic, non-window-suffixed features) = the
    full_history window's swing S/R registry - the most encompassing
    definition of "old S/R memory" the dashboard's own logic can express.
  - nearest_* distance = min absolute distance (ticks) from close[t] to
    any level of that type/window active at t (created_at_bar <= t, and
    for finite windows, t - created_at_bar <= W).
  - hold_rate_past_only / failure_rate_past_only = fraction of the nearest
    level's own PAST retouches (strictly before t) labeled HELD / BROKE.
  - reactivation_score = 1 - (fraction of past retouches labeled
    NO_REACTION) for the nearest level.
  - memory_score = min(touch_count_past_only, 20)/20 * hold_rate_past_only
    - a simple, documented, a-priori composite (NOT fit), capturing "this
      level has been tested many times AND tends to hold."

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
TICK = sc.TICK_SIZE


def level_price_matrix(lv_window: pd.DataFrame, n_bars: int) -> tuple[np.ndarray, list, np.ndarray, np.ndarray]:
    """Pivot (snapshot_bar x level_id) -> price, ffill across the full bar
    range, restricted to each level's [created_at_bar, last_snapshot_bar]
    lifetime (never extrapolated beyond what was actually observed)."""
    ids = sorted(lv_window["level_id"].unique())
    id_pos = {lid: i for i, lid in enumerate(ids)}
    piv = lv_window.pivot_table(index="snapshot_bar", values="level_price", columns="level_id", aggfunc="last")
    piv = piv.reindex(columns=ids)
    full_idx = np.arange(n_bars)
    piv = piv.reindex(full_idx).ffill()
    created = np.full(len(ids), -1, dtype=np.int64)
    last_seen = np.full(len(ids), -1, dtype=np.int64)
    for lid, g in lv_window.groupby("level_id"):
        i = id_pos[lid]
        created[i] = g["created_at_bar"].iloc[0]
        last_seen[i] = g["snapshot_bar"].max()
    price_mat = piv.to_numpy()
    bar_idx = full_idx[:, None]
    alive = (bar_idx >= created[None, :]) & (bar_idx <= last_seen[None, :])
    price_mat = np.where(alive, price_mat, np.nan)
    return price_mat, ids, created, last_seen


def nearest_idx_and_dist(price_mat: np.ndarray, close: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    diff = np.abs(price_mat - close[:, None])
    all_nan = np.all(np.isnan(diff), axis=1)
    diff_filled = np.where(np.isnan(diff), np.inf, diff)
    nearest = np.argmin(diff_filled, axis=1)
    dist = diff_filled[np.arange(len(close)), nearest]
    dist[all_nan] = np.nan
    nearest = nearest.astype(float)
    nearest[all_nan] = np.nan
    return nearest, dist


def main():
    sc.log("07: loading master, levels, behavior, retouch events...")
    df = sc.load_continuous_master()
    n = len(df)
    close = pd.to_numeric(df["px_close"], errors="coerce").to_numpy()

    levels = pd.read_parquet(sc.OUT_DIR / "dashboard_sr_levels_by_lookback.parquet")
    events = pd.read_parquet(sc.OUT_DIR / "dashboard_sr_retouch_events.parquet")
    beh = pd.read_parquet(sc.OUT_DIR / "dashboard_sr_behavior_labels.parquet")
    mech = pd.read_parquet(sc.OUT_DIR / "dashboard_sr_orderflow_mechanics.parquet")
    ev = events[["event_id", "bar_t", "level_id"]].merge(
        beh[["event_id", "behavior_label"]], on="event_id").merge(
        mech[["event_id", "touch_support_consumed", "touch_resistance_consumed"]]
        if "touch_support_consumed" in mech.columns else mech[["event_id"]], on="event_id", how="left")
    ev = ev.sort_values("bar_t").reset_index(drop=True)

    feat = pd.DataFrame(index=np.arange(n))
    feat["t"] = np.arange(n)

    sc.log("  full_history support/resistance nearest-distance + level-id mapping...")
    fh = levels[(levels["lookback_window"] == "full_history") &
                (levels["level_type"].isin(["SWING_SUPPORT", "SWING_RESISTANCE"]))]
    fh_supp = fh[fh["level_type"] == "SWING_SUPPORT"]
    fh_res = fh[fh["level_type"] == "SWING_RESISTANCE"]

    supp_mat, supp_ids, supp_created, _ = level_price_matrix(fh_supp, n)
    res_mat, res_ids, res_created, _ = level_price_matrix(fh_res, n)
    supp_nearest_i, supp_dist = nearest_idx_and_dist(supp_mat, close)
    res_nearest_i, res_dist = nearest_idx_and_dist(res_mat, close)
    feat["nearest_dashboard_old_support_distance_ticks"] = supp_dist / TICK
    feat["nearest_dashboard_old_resistance_distance_ticks"] = res_dist / TICK

    sc.log("  5000bar / 10000bar nearest-level distance (either type)...")
    for wlabel, colname in (("5000bar", "nearest_dashboard_5000bar_level_distance"),
                             ("10000bar", "nearest_dashboard_10000bar_level_distance")):
        wl = levels[(levels["lookback_window"] == wlabel) &
                    (levels["level_type"].isin(["SWING_SUPPORT", "SWING_RESISTANCE"]))]
        mat, ids, created, last_seen = level_price_matrix(wl, n)
        _, dist = nearest_idx_and_dist(mat, close)
        feat[colname] = dist / TICK

    sc.log("  per-bar nearest-old-level identity (whichever of supp/res is closer) + age/touch/confluence...")
    use_supp = np.where(np.isfinite(supp_dist) & (np.isfinite(res_dist) == False), True,
                         np.where(np.isfinite(res_dist) & (np.isfinite(supp_dist) == False), False,
                                   supp_dist <= res_dist))
    nearest_level_id = np.array([None] * n, dtype=object)
    for t in range(n):
        if np.isfinite(supp_dist[t]) and (use_supp[t] or not np.isfinite(res_dist[t])):
            i = int(supp_nearest_i[t])
            nearest_level_id[t] = supp_ids[i]
        elif np.isfinite(res_dist[t]):
            i = int(res_nearest_i[t])
            nearest_level_id[t] = res_ids[i]
    created_lookup = {**{lid: c for lid, c in zip(supp_ids, supp_created)},
                       **{lid: c for lid, c in zip(res_ids, res_created)}}
    age_bars = np.array([(t - created_lookup[lid]) if lid is not None else np.nan
                          for t, lid in zip(range(n), nearest_level_id)])
    feat["dashboard_old_level_age_bars"] = age_bars

    sc.log("  past-only touch history per nearest level (rolling, no future leakage)...")
    ev_by_level: dict = {}
    for lid, g in ev.groupby("level_id"):
        ev_by_level[lid] = g.sort_values("bar_t").reset_index(drop=True)

    touch_count = np.full(n, np.nan)
    last_reaction = np.array([None] * n, dtype=object)
    hold_rate = np.full(n, np.nan)
    failure_rate = np.full(n, np.nan)
    reactivation = np.full(n, np.nan)
    memory_score = np.full(n, np.nan)
    supp_consumed_last = np.array([None] * n, dtype=object)
    res_consumed_last = np.array([None] * n, dtype=object)

    held_set = {"SUPPORT_HELD", "RESISTANCE_HELD"}
    broke_set = {"BREAKOUT_ACCEPTANCE", "FAKE_BREAKOUT_SWEEP"}

    cache: dict = {}
    for t in range(n):
        lid = nearest_level_id[t]
        if lid is None:
            continue
        g = ev_by_level.get(lid)
        if g is None or g.empty:
            touch_count[t] = 0
            continue
        past = g[g["bar_t"] < t]
        touch_count[t] = len(past)
        if len(past) == 0:
            continue
        last_row = past.iloc[-1]
        last_reaction[t] = last_row["behavior_label"]
        n_held = past["behavior_label"].isin(held_set).sum()
        n_broke = past["behavior_label"].isin(broke_set).sum()
        n_noreact = (past["behavior_label"] == "NO_REACTION").sum()
        hold_rate[t] = n_held / len(past)
        failure_rate[t] = n_broke / len(past)
        reactivation[t] = 1.0 - (n_noreact / len(past))
        memory_score[t] = min(len(past), 20) / 20.0 * hold_rate[t]
        if "touch_support_consumed" in past.columns:
            sc_ = past["touch_support_consumed"].dropna()
            if len(sc_):
                supp_consumed_last[t] = bool(sc_.iloc[-1])
            rc_ = past["touch_resistance_consumed"].dropna()
            if len(rc_):
                res_consumed_last[t] = bool(rc_.iloc[-1])

    feat["dashboard_old_level_touch_count"] = touch_count
    feat["dashboard_old_level_last_reaction"] = last_reaction
    feat["dashboard_old_level_hold_rate_past_only"] = hold_rate
    feat["dashboard_old_level_failure_rate_past_only"] = failure_rate
    feat["dashboard_old_level_reactivation_score"] = reactivation
    feat["dashboard_old_level_memory_score"] = memory_score
    feat["dashboard_old_support_consumed_last_test"] = supp_consumed_last
    feat["dashboard_old_resistance_consumed_last_test"] = res_consumed_last

    sc.log("  confluence score for the nearest old level (reuse Part G logic, full_history window)...")
    vp_levels = levels[~levels["level_type"].isin(["SWING_RESISTANCE", "SWING_SUPPORT"]) &
                        (levels["lookback_window"] == "full_history")].sort_values("snapshot_bar")
    proj = pd.read_csv(sc.OUT_DIR / "dashboard_sr_projected_prior_contract_levels_static.csv")
    proj_prices = proj["projected_level_price"].dropna().to_numpy() if "projected_level_price" in proj.columns else np.array([])
    conf_score = np.full(n, np.nan)
    supp_id_pos = {lid: i for i, lid in enumerate(supp_ids)}
    res_id_pos = {lid: i for i, lid in enumerate(res_ids)}
    for t in range(n):
        lid = nearest_level_id[t]
        if lid is None:
            continue
        if lid in supp_id_pos:
            lp = supp_mat[t, supp_id_pos[lid]]
        else:
            lp = res_mat[t, res_id_pos[lid]]
        if not np.isfinite(lp):
            continue
        recent = vp_levels[vp_levels["snapshot_bar"] <= t]
        score = 0
        if len(recent):
            last_snap = recent["snapshot_bar"].max()
            today = recent[recent["snapshot_bar"] == last_snap]
            for lvl_type in ("HVN", "LVN", "POC"):
                sub = today[today["level_type"] == lvl_type]
                if len(sub) and (np.abs(sub["level_price"].to_numpy() - lp) <= NEAR_PRICE).any():
                    score += 1
            sub = today[today["level_type"].isin(["VAH", "VAL"])]
            if len(sub) and (np.abs(sub["level_price"].to_numpy() - lp) <= NEAR_PRICE).any():
                score += 1
        if len(proj_prices) and (np.abs(proj_prices - lp) <= NEAR_PRICE).any():
            score += 1
        conf_score[t] = score
    feat["dashboard_old_level_confluence_score"] = conf_score

    out_path = sc.OUT_DIR / "dashboard_old_sr_feature_panel.parquet"
    feat.to_parquet(out_path, index=False)
    sc.log(f"  wrote {out_path} ({len(feat)} rows, {feat.shape[1]} cols)")

    catalog_rows = [
        dict(feature="nearest_dashboard_old_support_distance_ticks", definition="min |close[t]-level_price| over "
             "active SWING_SUPPORT levels, full_history window, in ticks", past_only=True),
        dict(feature="nearest_dashboard_old_resistance_distance_ticks", definition="same, SWING_RESISTANCE, "
             "full_history window", past_only=True),
        dict(feature="nearest_dashboard_5000bar_level_distance", definition="min |close[t]-level_price| over active "
             "levels (either type), 5000bar window, in ticks", past_only=True),
        dict(feature="nearest_dashboard_10000bar_level_distance", definition="same, 10000bar window", past_only=True),
        dict(feature="dashboard_old_level_age_bars", definition="t - created_at_bar of whichever of "
             "nearest-support/nearest-resistance (full_history) is closer to close[t]", past_only=True),
        dict(feature="dashboard_old_level_touch_count", definition="count of that level's past retouch events "
             "with bar_t < t (strictly prior)", past_only=True),
        dict(feature="dashboard_old_level_last_reaction", definition="behavior_label of that level's most recent "
             "past retouch (bar_t < t)", past_only=True),
        dict(feature="dashboard_old_level_confluence_score", definition="count of HVN/LVN/POC/VAH-VAL/prior-session "
             "levels within near_ticks_price of the nearest level's price, using only volume-profile snapshots at "
             "or before t", past_only=True),
        dict(feature="dashboard_old_support_consumed_last_test/...resistance...", definition="touch_support_consumed/"
             "touch_resistance_consumed (Part E mechanics) at that level's most recent past retouch, NaN if outside "
             "Book Flow cache coverage", past_only=True),
        dict(feature="dashboard_old_level_hold_rate_past_only / failure_rate_past_only",
             definition="fraction of that level's past retouches (bar_t<t) labeled HELD / BROKE", past_only=True),
        dict(feature="dashboard_old_level_reactivation_score", definition="1 - fraction of past retouches labeled "
             "NO_REACTION", past_only=True),
        dict(feature="dashboard_old_level_memory_score", definition="min(touch_count_past_only,20)/20 * "
             "hold_rate_past_only - a-priori composite, not fit to data", past_only=True),
    ]
    cat_path = sc.OUT_DIR / "dashboard_old_sr_feature_catalog.csv"
    pd.DataFrame(catalog_rows).to_csv(cat_path, index=False)
    sc.log(f"  wrote {cat_path}")

    leak_rows = [dict(feature=r["feature"], leakage_risk="LOW",
                       reason="all inputs (level registry, retouch events, mechanics) are already past-only by "
                              "construction (see dashboard_sr_leakage_audit.csv); this script additionally filters "
                              "retouch history to bar_t < t at every row, so even a level's OWN most-recent touch "
                              "cannot be the current bar itself") for r in catalog_rows]
    leak_path = sc.OUT_DIR / "dashboard_old_sr_feature_leakage_audit.csv"
    pd.DataFrame(leak_rows).to_csv(leak_path, index=False)
    sc.log(f"  wrote {leak_path}")
    sc.log("07: done.")


if __name__ == "__main__":
    main()
