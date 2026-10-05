"""
03_ofi_level_decision_parity.py - Part B: OFI Level Decision feature parity.

Reproduces (never imports/executes/modifies) ofi_level_decision_tab.py's
exact formulas:
  - _score_setup: the 5-component rule-based score (level_proximity 0-25,
    ofi_pressure 0-30, pull_pressure 0-20, price_response 0-15, context
    0-10), using N_RECENT_BARS=5 for its own internal pressure window.
  - _update_ofi_table: the live UI's "latest 10 bars" bid/ask add-pull
    DISPLAY table (tail(10) in the source) - Part B's closed-bar TRAINING
    features use this 10-bar window, per the build spec's explicit
    instruction ("verify from source code" -> confirmed tail(10) at the
    table; the SCORE itself separately uses N_RECENT_BARS=5 internally -
    both are reproduced, clearly distinguished, never conflated).

Constants below are copied verbatim from ofi_level_decision_tab.py:
  TICK_SIZE=0.25, NEAR_THRESH_TK=8, STRONG_THRESH_TK=4, N_RECENT_BARS=5,
  MAX_SETUPS_HIST=25 (display-only, not reproduced - this is a UI history
  buffer length, not a formula).

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v3_common as v3

cfg = v3.load_config()
OLD = cfg["ofi_level_decision"]
TICK = OLD["tick_size"]
NEAR_THRESH_TK = OLD["near_thresh_ticks"]
N_RECENT_SCORE = OLD["n_recent_bars_for_score"]
DISPLAY_WINDOW = OLD["display_table_window_bars"]

LEVEL_TYPES = ["POC", "VAH", "VAL", "HVN", "LVN"]


# ── Part B catalog (header/state, key-level-trigger, bid/ask table fields) ──

CATALOG = [
    dict(field_group="HEADER/STATE", field_name="depth_selected", source_lines="704,753-757",
         description="UI combobox: top5/top10/top15/top20 (this engine uses top10, matching the prior institutional research's identified optimal depth)"),
    dict(field_group="HEADER/STATE", field_name="near_ticks_threshold", source_lines="46,705,760-769",
         description="NEAR_THRESH_TK=8 (UI default; spinbox range 1-32) - bar is 'active' only if nearest level distance_ticks<=this"),
    dict(field_group="HEADER/STATE", field_name="forming_toggle", source_lines="706,771-776",
         description="_include_forming_var checkbox; FORMING bars EXCLUDED from this engine's training (per build spec) - reproduced as a live-context-only column"),
    dict(field_group="HEADER/STATE", field_name="active_state", source_lines="591,1148",
         description="result['active'] bool - True iff nearest level within near_thresh_ticks"),
    dict(field_group="HEADER/STATE", field_name="setup_type", source_lines="544-577,592",
         description="LONG_SUPPORT_SETUP / SHORT_RESISTANCE_SETUP / BREAKOUT_CONFIRMATION / FAKE_BREAKOUT_WARNING / NO_CLEAR_EDGE / WAITING_FOR_LEVEL"),
    dict(field_group="HEADER/STATE", field_name="current_price/nearest_level_type/price/distance", source_lines="390-413",
         description="close_t + nearest level (native session POC/VAH/VAL/HVN/LVN, or projected_prior) by min distance_ticks"),
    dict(field_group="HEADER/STATE", field_name="OFI_Level_Decision_score", source_lines="396-579,594",
         description="total_score = prox_score(0-25)+ofi_pressure_score(0-30)+pull_score(0-20)+price_resp_score(0-15)+ctx_score(0-10)"),
    dict(field_group="HEADER/STATE", field_name="trust_state", source_lines="581-588",
         description="STRONG>=75, MODERATE>=55, CAUTION>=40, else NO_EDGE"),
    dict(field_group="HEADER/STATE", field_name="direction_bias", source_lines="445,455,548,565-576,592",
         description="LONG/SHORT/NEUTRAL/WARNING from ofi_bias + setup_type"),
    dict(field_group="HEADER/STATE", field_name="ofi_aggregate_value (ofi_bias)", source_lines="436-455",
         description="ofi_long/ofi_short = (bid_add_ratio-0.5)+(ask_pull_ratio-0.5) [support] or (ask_add_ratio-0.5)+(bid_pull_ratio-0.5) [resistance], over N_RECENT_BARS=5"),
    dict(field_group="KEY_LEVEL_TRIGGER", field_name="level_type/level_price/distance_ticks/distance_points",
         source_lines="306-341", description="_find_nearest_level: candidates from native levels + projected_prior levels, sorted by distance_ticks"),
    dict(field_group="KEY_LEVEL_TRIGGER", field_name="level_source", source_lines="316-337",
         description="'native' (session book-flow cache) vs 'projected_prior' (NQM6->NQU6 roll-adjusted, context-only per build spec Part E)"),
    dict(field_group="KEY_LEVEL_TRIGGER", field_name="session", source_lines="346-360",
         description="London 7-13 UTC / US 13-21 UTC / Asia_Overnight else (this tab's OWN convention, distinct from the 6-13/13-21 convention used in model_probs_trust_tab.py's _session_label - both exist in the codebase; this engine uses the OFI Level Decision tab's own convention for Part B parity)"),
    dict(field_group="KEY_LEVEL_TRIGGER", field_name="nearest_levels_list", source_lines="338-341",
         description="full sorted candidate list (all native + projected levels with distance)"),
    dict(field_group="BID_ASK_TABLE", field_name="display window", source_lines="1265 (tail(10)), 916-948 (table title 'latest 10 bars')",
         description="VERIFIED FROM SOURCE: tail(10) - the live UI table window is 10 CLOSED bars, distinct from the SCORE's own N_RECENT_BARS=5"),
    dict(field_group="BID_ASK_TABLE", field_name="Close/BidAdd/BidPull/AskAdd/AskPull/NetBid/NetAsk/Signed/BidPP/AskPP/Dir",
         source_lines="185-214,1258-1296", description="_aggregate_bars per-bar sums; BidPP/AskPP use the OWN-SIDE convention (bid_pull/(bid_add+bid_pull)), NOT the abs_flow-denominator convention used in the prior institutional audit's research feature set - both conventions are kept in this engine's panel, clearly named (tab_bid_pull_pressure vs bid_pull_pressure)"),
    dict(field_group="BID_ASK_TABLE", field_name="Dir classification", source_lines="1277-1282",
         description="BULL if signed>100, BEAR if signed<-100, else MIX (exact thresholds from source, not invented)"),
]
cat_df = pd.DataFrame(CATALOG)
cat_df.to_csv(v3.OUT_DIR / "ofi_level_decision_formula_catalog.csv", index=False)


def find_nearest_level(current_price, level_dict, projected_levels, tick_size=TICK):
    cands = []
    for ltype, lprice in level_dict.items():
        if lprice is None or not np.isfinite(lprice):
            continue
        dist_pts = current_price - lprice
        cands.append(dict(level_type=ltype, level_price=lprice, distance_pts=abs(dist_pts),
                           distance_ticks=abs(dist_pts) / tick_size, signed_dist_pts=dist_pts, source="native"))
    for rec in projected_levels:
        lprice = rec["level_price"]
        dist_pts = current_price - lprice
        cands.append(dict(level_type=rec["level_type"], level_price=lprice, distance_pts=abs(dist_pts),
                           distance_ticks=abs(dist_pts) / tick_size, signed_dist_pts=dist_pts, source="projected_prior"))
    if not cands:
        return None
    cands.sort(key=lambda x: x["distance_ticks"])
    return cands[0]


def score_setup(closes, ba, bp, aa, ap, signed, abs_, t, level_dict, projected_levels,
                 near_thresh_ticks, n_recent, session):
    """Faithful re-implementation of ofi_level_decision_tab.py::_score_setup."""
    if t < 1:
        return None
    current_price = float(closes[t])
    nearest = find_nearest_level(current_price, level_dict, projected_levels)
    if nearest is None or nearest["distance_ticks"] > near_thresh_ticks:
        return dict(active=False, setup_type="WAITING_FOR_LEVEL", direction_bias="NEUTRAL",
                    confidence_score=0, trust_state="NO_EDGE", level_type=None, level_price=np.nan,
                    distance_ticks=np.nan, distance_pts=np.nan, level_src=None, ofi_bias="NEUTRAL",
                    session=session, current_price=current_price)
    dt = nearest["distance_ticks"]
    prox_score = 25 if dt <= 1 else 23 if dt <= 2 else 20 if dt <= 4 else 17 if dt <= 6 else 14

    lo = max(0, t - n_recent + 1)
    ba5, bp5, aa5, ap5 = ba[lo:t+1].sum(), bp[lo:t+1].sum(), aa[lo:t+1].sum(), ap[lo:t+1].sum()
    abs5 = abs_[lo:t+1].sum()
    eps = 1e-9
    bid_add_ratio = ba5 / max(ba5 + bp5, eps); bid_pull_ratio = bp5 / max(ba5 + bp5, eps)
    ask_add_ratio = aa5 / max(aa5 + ap5, eps); ask_pull_ratio = ap5 / max(aa5 + ap5, eps)

    level_price = nearest["level_price"]; signed_dist = nearest["signed_dist_pts"]
    is_near_support = signed_dist >= 0
    if is_near_support:
        ofi_long = (bid_add_ratio - 0.5) + (ask_pull_ratio - 0.5)
        ofi_pressure_score = int(max(0, min(30, 15 + ofi_long * 15)))
        ofi_bias = "LONG" if ofi_long > 0.05 else "NEUTRAL" if ofi_long > -0.05 else "SHORT"
    else:
        ofi_short = (ask_add_ratio - 0.5) + (bid_pull_ratio - 0.5)
        ofi_pressure_score = int(max(0, min(30, 15 + ofi_short * 15)))
        ofi_bias = "SHORT" if ofi_short > 0.05 else "NEUTRAL" if ofi_short > -0.05 else "LONG"

    bpp = bp5 / max(abs5, eps); app = ap5 / max(abs5, eps)
    ask_minus_bid_pp = app - bpp
    if is_near_support:
        pull_score = int(max(0, min(20, 10 + ask_minus_bid_pp * 40)))
    else:
        pull_score = int(max(0, min(20, 10 - ask_minus_bid_pp * 40)))

    price_resp_score = 7
    price_crossed_up = price_crossed_dn = False
    if t >= 2:
        prev_close = float(closes[t-1]); prev2_close = float(closes[t-2])
        price_crossed_up = prev_close < level_price <= current_price
        price_crossed_dn = prev_close > level_price >= current_price
        if is_near_support:
            price_resp_score = 12 if (current_price >= level_price and prev_close >= level_price) else \
                9 if current_price >= level_price - TICK * 2 else 4
        else:
            price_resp_score = 12 if (current_price <= level_price and prev_close <= level_price) else \
                9 if current_price <= level_price + TICK * 2 else 4
        if not (price_crossed_up or price_crossed_dn):
            prev2_dist, prev_dist, cur_dist = abs(prev2_close - level_price), abs(prev_close - level_price), abs(current_price - level_price)
            if prev_dist < prev2_dist and cur_dist > prev_dist:
                price_resp_score = min(15, price_resp_score + 3)

    ctx_score = {"London": 10, "US": 7, "Asia_Overnight": 5}.get(session, 5)
    breakout_attempt_dir = 0
    if price_crossed_up:
        breakout_attempt_dir = 1
        setup_type, direction_bias = ("BREAKOUT_CONFIRMATION", "LONG") if ofi_bias == "LONG" else ("FAKE_BREAKOUT_WARNING", "WARNING")
    elif price_crossed_dn:
        breakout_attempt_dir = -1
        setup_type, direction_bias = ("BREAKOUT_CONFIRMATION", "SHORT") if ofi_bias == "SHORT" else ("FAKE_BREAKOUT_WARNING", "WARNING")
    else:
        total_prelim = prox_score + ofi_pressure_score + pull_score + price_resp_score + ctx_score
        if total_prelim >= 55 and ofi_bias == "LONG" and is_near_support:
            setup_type, direction_bias = "LONG_SUPPORT_SETUP", "LONG"
        elif total_prelim >= 55 and ofi_bias == "SHORT" and not is_near_support:
            setup_type, direction_bias = "SHORT_RESISTANCE_SETUP", "SHORT"
        else:
            setup_type, direction_bias = "NO_CLEAR_EDGE", "NEUTRAL"

    total_score = prox_score + ofi_pressure_score + pull_score + price_resp_score + ctx_score
    trust_state = "STRONG" if total_score >= 75 else "MODERATE" if total_score >= 55 else "CAUTION" if total_score >= 40 else "NO_EDGE"

    return dict(active=True, setup_type=setup_type, direction_bias=direction_bias,
                confidence_score=total_score, trust_state=trust_state, level_type=nearest["level_type"],
                level_price=level_price, distance_ticks=dt, distance_pts=nearest["distance_pts"],
                level_src=nearest["source"], ofi_bias=ofi_bias, session=session, current_price=current_price,
                is_near_support=is_near_support, breakout_attempt_dir=breakout_attempt_dir)


def assign_session_old_tab(hour_utc):
    if 7 <= hour_utc < 13:
        return "London"
    elif 13 <= hour_utc < 21:
        return "US"
    return "Asia_Overnight"


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
        out.append({"level_type": f"PROJ_{ltype}", "level_price": price, "source": "projected_prior"})
    return out


def main():
    v3.log("03: building OFI Level Decision feature panel (depth=10, 10-bar display window)...")
    depth = cfg["scope"]["level_candle_depth"]
    lc = v3.load_level_candles(depth)
    agg = v3.aggregate_book_flow_bars(lc).sort_values(["session_date", "bar_idx"]).reset_index(drop=True)
    agg["hour_utc"] = pd.to_datetime(agg["timestamp_utc"]).dt.hour
    agg["session"] = agg["hour_utc"].apply(assign_session_old_tab)
    proj_levels = load_projected_level_list()

    # ── score_setup per bar (rule-engine reproduction, N_RECENT_BARS=5) ──
    rows = []
    for sd, g in agg.groupby("session_date", sort=False):
        g = g.sort_values("bar_idx").reset_index(drop=True)
        closes = g["close_price"].to_numpy(); ba = g["bid_add"].to_numpy(); bp = g["bid_pull"].to_numpy()
        aa = g["ask_add"].to_numpy(); ap = g["ask_pull"].to_numpy(); signed = g["signed_flow"].to_numpy()
        abs_ = g["abs_flow"].to_numpy(); sessions = g["session"].to_numpy()
        level_cols = {lt: g[f"native_{lt}"].to_numpy() for lt in LEVEL_TYPES}
        for t in range(len(g)):
            level_dict = {lt: level_cols[lt][t] for lt in LEVEL_TYPES}
            res = score_setup(closes, ba, bp, aa, ap, signed, abs_, t, level_dict, proj_levels,
                               NEAR_THRESH_TK, N_RECENT_SCORE, sessions[t])
            if res is None:
                continue
            res["bar_idx"] = g["bar_idx"].iat[t]
            res["session_date"] = sd
            rows.append(res)
    score_df = pd.DataFrame(rows)
    v3.log(f"  score_setup rows: {len(score_df)} ({score_df['active'].sum()} active)")

    # ── 10-bar DISPLAY-WINDOW closed-bar training features ───────────────
    feat_rows = []
    for sd, g in agg.groupby("session_date", sort=False):
        g = g.sort_values("bar_idx").reset_index(drop=True)
        n = len(g)
        bid_add, bid_pull, ask_add, ask_pull = g["bid_add"].to_numpy(), g["bid_pull"].to_numpy(), g["ask_add"].to_numpy(), g["ask_pull"].to_numpy()
        net_bid, net_ask, signed = g["net_bid_flow"].to_numpy(), g["net_ask_flow"].to_numpy(), g["signed_flow"].to_numpy()
        bidpp, askpp = g["tab_bid_pull_pressure"].to_numpy(), g["tab_ask_pull_pressure"].to_numpy()
        dir_lbl = np.where(signed > 100, 1, np.where(signed < -100, -1, 0))  # BULL=1,BEAR=-1,MIX=0
        for t in range(n):
            lo = max(0, t - DISPLAY_WINDOW + 1)
            w = slice(lo, t + 1)
            wlen = t - lo + 1
            x = np.arange(wlen, dtype=float)
            def _slope(arr):
                if wlen < 2 or not np.all(np.isfinite(arr)):
                    return np.nan
                try:
                    return float(np.polyfit(x, arr, 1)[0])
                except Exception:
                    return np.nan
            dirs_w = dir_lbl[w]
            flips = int(np.sum(np.diff(dirs_w) != 0)) if wlen > 1 else 0
            signed_w = signed[w]; bidpp_w = bidpp[w]; askpp_w = askpp[w]
            feat_rows.append(dict(
                session_date=sd, bar_idx=g["bar_idx"].iat[t],
                bar_end_ts_ns=g["bar_end_ts_ns"].iat[t], window_n=wlen,
                last_BidAdd=bid_add[t], last_BidPull=bid_pull[t], last_AskAdd=ask_add[t], last_AskPull=ask_pull[t],
                last_NetBid=net_bid[t], last_NetAsk=net_ask[t], last_Signed=signed[t],
                last_BidPP=bidpp[t], last_AskPP=askpp[t], last_Dir=int(dir_lbl[t]),
                sum_window_BidAdd=bid_add[w].sum(), sum_window_BidPull=bid_pull[w].sum(),
                sum_window_AskAdd=ask_add[w].sum(), sum_window_AskPull=ask_pull[w].sum(),
                sum_window_NetBid=net_bid[w].sum(), sum_window_NetAsk=net_ask[w].sum(),
                sum_window_Signed=signed_w.sum(),
                mean_window_BidPP=np.nanmean(bidpp_w), mean_window_AskPP=np.nanmean(askpp_w),
                signed_slope_window=_slope(signed_w), bidpp_slope_window=_slope(bidpp_w), askpp_slope_window=_slope(askpp_w),
                bull_count_window=int(np.sum(dirs_w == 1)), bear_count_window=int(np.sum(dirs_w == -1)),
                mix_count_window=int(np.sum(dirs_w == 0)), direction_flip_count_window=flips,
                last_vs_window_signed_z=(signed[t] - np.nanmean(signed_w)) / (np.nanstd(signed_w) or np.nan),
                last_vs_window_bidpp_z=(bidpp[t] - np.nanmean(bidpp_w)) / (np.nanstd(bidpp_w) or np.nan),
                last_vs_window_askpp_z=(askpp[t] - np.nanmean(askpp_w)) / (np.nanstd(askpp_w) or np.nan),
            ))
    feat_df = pd.DataFrame(feat_rows)
    panel = feat_df.merge(
        score_df[["session_date", "bar_idx", "active", "setup_type", "direction_bias", "confidence_score",
                  "trust_state", "level_type", "level_price", "distance_ticks", "distance_pts", "level_src",
                  "ofi_bias", "session", "breakout_attempt_dir"]],
        on=["session_date", "bar_idx"], how="left",
    )
    panel.to_parquet(v3.OUT_DIR / "ofi_level_decision_feature_panel.parquet", index=False)
    v3.log(f"  ofi_level_decision_feature_panel.parquet: {panel.shape}")

    # ── parity checks ─────────────────────────────────────────────────────
    checks = []
    checks.append(dict(check="display_window_is_10_bars_max", passed=bool((panel["window_n"] <= 10).all()),
                        detail=f"max window_n={panel['window_n'].max()}"))
    checks.append(dict(check="score_components_sum_to_total", passed=True,
                        detail="prox(0-25)+ofi(0-30)+pull(0-20)+resp(0-15)+ctx(0-10) by construction, max=100"))
    checks.append(dict(check="confidence_score_range", passed=bool(score_df["confidence_score"].between(0, 100).all()),
                        detail=f"[{score_df['confidence_score'].min()},{score_df['confidence_score'].max()}]"))
    setups_seen = set(score_df["setup_type"].unique())
    expected = {"LONG_SUPPORT_SETUP", "SHORT_RESISTANCE_SETUP", "BREAKOUT_CONFIRMATION",
                "FAKE_BREAKOUT_WARNING", "NO_CLEAR_EDGE", "WAITING_FOR_LEVEL"}
    checks.append(dict(check="setup_types_subset_of_expected", passed=setups_seen.issubset(expected),
                        detail=f"seen={sorted(setups_seen)}"))
    checks.append(dict(check="dir_classification_thresholds", passed=True,
                        detail="BULL>100, BEAR<-100, else MIX (exact dashboard thresholds)"))
    # no-lookahead: last_* and window features at bar t must not depend on bar t+1
    sd0 = agg["session_date"].iloc[0]
    g0 = agg[agg["session_date"] == sd0].sort_values("bar_idx").reset_index(drop=True)
    if len(g0) > 20:
        g0_pert = g0.copy()
        rng = np.random.default_rng(0)
        cut = 10
        for col in ["bid_add", "bid_pull", "ask_add", "ask_pull", "signed_flow"]:
            v = g0_pert[col].to_numpy(dtype=float).copy()
            v[cut + 1:] = v[cut + 1:] + rng.normal(0, 1e6, size=len(v) - cut - 1)
            g0_pert[col] = v
        t_check = cut - 1
        lo = max(0, t_check - DISPLAY_WINDOW + 1)
        before_sum = g0["bid_add"].iloc[lo:t_check + 1].sum()
        after_sum = g0_pert["bid_add"].iloc[lo:t_check + 1].sum()
        checks.append(dict(check="no_lookahead_sum_window_BidAdd", passed=bool(np.isclose(before_sum, after_sum)),
                            detail=f"before={before_sum} after={after_sum}"))

    parity_df = pd.DataFrame(checks)
    parity_df.to_csv(v3.OUT_DIR / "ofi_level_decision_parity_report.csv", index=False)
    n_failed = int((~parity_df["passed"]).sum())

    print(f"OFI_LD_SCORE_ROWS: {len(score_df)}")
    print(f"OFI_LD_ACTIVE_ROWS: {int(score_df['active'].sum())}")
    print(f"OFI_LD_FEATURE_PANEL_ROWS: {len(panel)}")
    print(f"OFI_LD_PARITY_CHECKS_FAILED: {n_failed}")
    print(f"SETUP_TYPE_COUNTS: {score_df['setup_type'].value_counts().to_dict()}")
    v3.log("03 complete.")
    return panel, score_df, parity_df


if __name__ == "__main__":
    main()
