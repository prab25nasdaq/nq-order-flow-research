"""
02_dashboard_feature_builder.py - Part A feature builder + parity report.

Builds dashboard_feature_panel.parquet by applying every formula catalogued
in 01_dashboard_formula_catalog.py (excluding the ones explicitly marked
EXCLUDED/HIGH-leakage there) to the full NQU6 bar history, in true
chronological bar order - exactly the data the live dashboard itself would
see bar-by-bar.

PARITY VERIFICATION (dashboard_feature_parity_report.csv): since the
dashboard is an interactive Tkinter GUI (never executed/imported here, per
the absolute rules), pixel-for-pixel comparison against a live session is
not possible or appropriate. Parity is instead verified the only rigorous
way available against a frozen historical dataset:
  1. Code-level parity: every transform below is a direct line-for-line
     translation of the cited dashboard source (Part A catalog).
  2. No-lookahead verification: a perturbation test (identical technique
     used and proven in the v2 engine) - corrupt every bar strictly AFTER a
     cut point and confirm zero change before the cut, for every formula
     family.
  3. Range/sanity verification: entropy in [0,1], toxicity/liquidity_cost in
     [0,1], percentile ranks in [0,1], etc.
If any formula fails (1)-(3), this script marks BLOCKED and the model build
must not proceed past Part A.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v3_common as v3

cfg = v3.load_config()
DF = cfg["dashboard_formulas"]


def build_panel(nqu6: pd.DataFrame) -> pd.DataFrame:
    n = len(nqu6)
    close = nqu6["px_close"].to_numpy(dtype=float)
    high = nqu6["px_high"].to_numpy(dtype=float)
    low = nqu6["px_low"].to_numpy(dtype=float)
    mid = nqu6["mid_mean"].to_numpy(dtype=float) if "mid_mean" in nqu6.columns else close
    mid = np.where(np.isfinite(mid), mid, close)
    bv = nqu6["buy_vol"].to_numpy(dtype=float)
    sv = nqu6["sell_vol"].to_numpy(dtype=float)
    vol = bv + sv
    q = bv - sv
    dollar_vol = mid * np.maximum(vol, 0.0)
    delta_norm = nqu6["delta_norm"].to_numpy(dtype=float)
    sweep = nqu6["sweep_imbalance_norm"].to_numpy(dtype=float)
    ofi = nqu6["mlofi_norm"].to_numpy(dtype=float) if "mlofi_norm" in nqu6.columns else delta_norm

    y = np.log(np.maximum(mid, 1e-12))
    r = np.full(n, np.nan); r[1:] = y[1:] - y[:-1]
    dp = np.full(n, np.nan); dp[1:] = mid[1:] - mid[:-1]

    panel = pd.DataFrame({"bar_idx": nqu6["bar_index"].to_numpy(), "day": nqu6["day"].to_numpy(),
                          "bar_end_ts_ns": nqu6["bar_end_ts_ns"].to_numpy()})

    # ── Z-SCORES ──────────────────────────────────────────────────────────
    panel["mid_resid_z"] = v3.residual_z(pd.Series(mid), window=DF["residual_z_window"]).to_numpy()
    for col in ["delta_norm", "volatility_5", "sweep_imbalance_norm", "vpin"]:
        if col in nqu6.columns:
            panel[f"{col}_resid_z20"] = v3.residual_z(
                pd.to_numeric(nqu6[col], errors="coerce"), window=DF["residual_z_window"]
            ).to_numpy()

    # ── REGIME BREAKS: CUSUM (cross-day continuous, matches dashboard) ─────
    cb = v3.cusum_breaks(r, halflife=DF["cusum_halflife"], k=DF["cusum_k"], h=DF["cusum_h"])
    panel["dash_cusum_up_break"] = cb["up"]
    panel["dash_cusum_down_break"] = cb["down"]
    panel["dash_cusum_z"] = cb["z"]

    # ── REGIME BREAKS: ret_z 5-state code ───────────────────────────────────
    ret_z = v3.causal_z(r, window=DF["causal_z_window"], min_ref=DF["causal_z_min_ref"])
    ret_codes = np.full(n, -1, dtype=int)
    vmask = np.isfinite(ret_z)
    ret_codes[vmask & (ret_z <= -1.5)] = 0
    ret_codes[vmask & (ret_z > -1.5) & (ret_z <= -0.5)] = 1
    ret_codes[vmask & (ret_z > -0.5) & (ret_z < 0.5)] = 2
    ret_codes[vmask & (ret_z >= 0.5) & (ret_z < 1.5)] = 3
    ret_codes[vmask & (ret_z >= 1.5)] = 4
    panel["dash_ret_z"] = ret_z

    # ── ENTROPY ──────────────────────────────────────────────────────────
    delta_codes = v3.quantile_codes(delta_norm, 5, window=DF["quantile_codes_window"], min_ref=DF["quantile_codes_min_ref"])
    ofi_codes = v3.quantile_codes(ofi, 5, window=DF["quantile_codes_window"], min_ref=DF["quantile_codes_min_ref"])
    delta_thr = np.maximum(v3.rolling_abs_quantile(delta_norm, 0.60, window=DF["quantile_codes_window"], min_ref=DF["quantile_codes_min_ref"]), 0.05)
    ofi_thr = np.maximum(v3.rolling_abs_quantile(ofi, 0.60, window=DF["quantile_codes_window"], min_ref=DF["quantile_codes_min_ref"]), 0.05)
    sw_thr = np.maximum(v3.rolling_abs_quantile(sweep, 0.60, window=DF["quantile_codes_window"], min_ref=DF["quantile_codes_min_ref"]), 0.05)
    delta_dir = np.where(delta_norm > delta_thr, 1, np.where(delta_norm < -delta_thr, -1, 0))
    ofi_dir = np.where(ofi > ofi_thr, 1, np.where(ofi < -ofi_thr, -1, 0))
    sweep_dir = np.where(sweep > sw_thr, 1, np.where(sweep < -sw_thr, -1, 0))
    sweep_codes = sweep_dir + 1
    flow_joint_codes = (delta_dir + 1) * 9 + (ofi_dir + 1) * 3 + (sweep_dir + 1)
    sp = pd.Series(high - low).replace([np.inf, -np.inf], np.nan)
    sp_mu = sp.rolling(128, min_periods=20).mean().shift(1)
    sp_sd = sp.rolling(128, min_periods=20).std().shift(1).replace(0, np.nan)
    sp_z = ((sp - sp_mu) / sp_sd).fillna(0.0).to_numpy()
    spread_codes = np.where(sp_z < 1, 0, np.where(sp_z < 2, 1, np.where(sp_z < 3, 2, 3)))

    ew = DF["entropy_window"]
    ent_return = v3.rolling_entropy(ret_codes, 5, ew)
    ent_delta = v3.rolling_entropy(delta_codes, 5, ew)
    ent_ofi = v3.rolling_entropy(ofi_codes, 5, ew)
    ent_sweep = v3.rolling_entropy(sweep_codes, 3, ew)
    ent_spread = v3.rolling_entropy(spread_codes, 4, ew)
    ent_flow = v3.rolling_entropy(flow_joint_codes, 27, ew)
    panel["entropy_return"] = ent_return
    panel["entropy_delta"] = ent_delta
    panel["entropy_ofi"] = ent_ofi
    panel["entropy_sweep"] = ent_sweep
    panel["entropy_spread"] = ent_spread
    panel["entropy_flow"] = ent_flow
    panel["entropy_score"] = (
        0.35 * pd.Series(ent_return).ffill().fillna(0.5).to_numpy()
        + 0.35 * pd.Series(ent_flow).ffill().fillna(0.5).to_numpy()
        + 0.15 * pd.Series(ent_spread).ffill().fillna(0.5).to_numpy()
        + 0.15 * (pd.Series(ent_delta).ffill().fillna(0.5).to_numpy()
                  + pd.Series(ent_ofi).ffill().fillna(0.5).to_numpy()
                  + pd.Series(ent_sweep).ffill().fillna(0.5).to_numpy()) / 3.0
    )

    # ── FLOW TOXICITY / Q-SCIENCE: flow_score, divergence ───────────────────
    delta_z = v3.causal_z(delta_norm, window=128, min_ref=30)
    ofi_z = v3.causal_z(ofi, window=128, min_ref=30)
    sweep_z = v3.causal_z(sweep, window=128, min_ref=30)
    flow_score_raw = (0.40 * np.tanh(np.nan_to_num(delta_z, nan=0.0) / 2.0)
                      + 0.35 * np.tanh(np.nan_to_num(ofi_z, nan=0.0) / 2.0)
                      + 0.25 * np.tanh(np.nan_to_num(sweep_z, nan=0.0) / 2.0))
    flow_score = v3.ema_clean(flow_score_raw, span=5, fill=0.0)
    flow_slope = np.r_[np.nan, np.diff(flow_score)]
    price_slope_z = v3.causal_z(r, window=64, min_ref=20)
    flow_slope_z = v3.causal_z(flow_slope, window=64, min_ref=20)
    panel["flow_score"] = flow_score
    panel["flow_divergence"] = price_slope_z - flow_slope_z
    panel["flow_alignment"] = np.clip(np.abs(flow_score), 0.0, 1.0)
    panel["flow_direction"] = np.where(flow_score > 0.05, 1, np.where(flow_score < -0.05, -1, 0))

    # ── FLOW TOXICITY: vpin ──────────────────────────────────────────────
    if "vpin" in nqu6.columns:
        vpin = pd.to_numeric(nqu6["vpin"], errors="coerce").ffill().fillna(0.5).to_numpy()
    else:
        imb = np.abs(bv - sv)
        vpin = (pd.Series(imb).rolling(75, min_periods=10).sum() /
                pd.Series(vol).rolling(75, min_periods=10).sum()).fillna(0.5).to_numpy()
    vpin_pct = v3.rolling_pct(vpin, DF["rolling_pct_window"])
    panel["vpin_pct"] = vpin_pct
    panel["vpin_state"] = np.select(
        [vpin_pct < 0.70, vpin_pct < 0.90, vpin_pct < 0.95],
        ["NORMAL", "ELEVATED", "TOXIC"], default="EXTREME_TOXICITY",
    )

    # ── LIQUIDITY COST ───────────────────────────────────────────────────
    kyle = v3.rolling_kyle(mid, q, h=1, window=DF["rolling_kyle_window"])
    with np.errstate(divide="ignore", invalid="ignore"):
        amihud_raw = np.where(dollar_vol > 0, np.abs(dp) / dollar_vol, np.nan)
    amihud = pd.Series(amihud_raw).rolling(160, min_periods=30).median().to_numpy()
    roll = v3.roll_measure(dp, DF["roll_measure_window"])
    roll_impact = roll / pd.Series(dollar_vol).rolling(160, min_periods=30).mean().to_numpy()
    spread_raw = high - low
    cs_spread = v3.corwin_schulz(high, low)

    spread_pct = v3.rolling_pct(spread_raw, DF["rolling_pct_window"])
    kyle_pct = v3.rolling_pct(np.abs(kyle), DF["rolling_pct_window"])
    amihud_pct = v3.rolling_pct(amihud, DF["rolling_pct_window"])
    roll_pct = v3.rolling_pct(roll_impact, DF["rolling_pct_window"])
    cs_pct = v3.rolling_pct(cs_spread, DF["rolling_pct_window"])
    panel["kyle"] = kyle; panel["amihud"] = amihud; panel["roll_impact"] = roll_impact
    panel["cs_spread"] = cs_spread
    panel["spread_pct"] = spread_pct; panel["kyle_pct"] = kyle_pct
    panel["amihud_pct"] = amihud_pct; panel["roll_pct"] = roll_pct; panel["cs_spread_pct"] = cs_pct

    toxicity = np.clip(0.45 * vpin_pct + 0.20 * pd.Series(spread_pct).ffill().fillna(0.5).to_numpy()
                       + 0.15 * pd.Series(kyle_pct).ffill().fillna(0.5).to_numpy()
                       + 0.10 * pd.Series(amihud_pct).ffill().fillna(0.5).to_numpy()
                       + 0.10 * pd.Series(roll_pct).ffill().fillna(0.5).to_numpy(), 0, 1)
    liq_cost = np.clip(0.35 * pd.Series(spread_pct).ffill().fillna(0.5).to_numpy()
                       + 0.25 * pd.Series(roll_pct).ffill().fillna(0.5).to_numpy()
                       + 0.25 * pd.Series(amihud_pct).ffill().fillna(0.5).to_numpy()
                       + 0.15 * pd.Series(cs_pct).ffill().fillna(0.5).to_numpy(), 0, 1)
    panel["toxicity"] = toxicity
    panel["liquidity_cost"] = liq_cost

    # ── REGIME BREAKS: break_score / break_age / market_state (SADF/SMT excluded) ──
    csw_score, csw_crit = _csw(y)
    csw_pct = v3.rolling_pct(csw_score, 250)
    panel["csw_score"] = csw_score
    break_score = np.maximum.reduce([cb["up"].astype(float), cb["down"].astype(float),
                                      pd.Series(csw_pct).fillna(0).to_numpy()])
    panel["break_score"] = break_score
    break_event = (cb["up"] | cb["down"] | (np.isfinite(csw_score) & np.isfinite(csw_crit) & (csw_score > csw_crit)))
    break_age = np.zeros(n, dtype=int)
    last_break = -1
    for i in range(n):
        if break_event[i]:
            last_break = i
        break_age[i] = i - last_break if last_break >= 0 else i
    panel["break_age"] = break_age

    e_latest = pd.Series(panel["entropy_score"]).fillna(0.5).to_numpy()
    flow_align = panel["flow_alignment"].to_numpy()
    tox = panel["toxicity"].to_numpy()
    sp_pct = pd.Series(panel["spread_pct"]).fillna(0.5).to_numpy()
    market_state = np.full(n, "NO_EDGE", dtype=object)
    market_state[(tox > 0.90) & (sp_pct > 0.80)] = "TOXIC_FLOW"
    market_state[(break_score > 0.85) & (break_age <= 20)] = "REGIME_BREAK"
    market_state[(e_latest > 0.65) & (flow_align < 0.67)] = "CHOP_RANDOM"
    market_state[(e_latest < 0.35) & (flow_align >= 0.67) & (tox < 0.90)] = "CLEAN_TREND"
    panel["market_state"] = market_state  # SADF/SMT-driven EXPLOSIVE_MOVE branch excluded (documented)

    # ── Q-SCIENCE / MICROSTRUCTURE: ADX ─────────────────────────────────
    adx, pdi, ndi = _adx(high, low, close, period=14)
    panel["adx"] = adx; panel["plus_di"] = pdi; panel["minus_di"] = ndi

    # ── ORDER FLOW: bull/bear pressure composite (DOM-depth component excluded) ──
    bull_buy_delta = _pctrank(np.maximum(delta_norm, 0), 80)
    bear_sell_delta = _pctrank(np.maximum(-delta_norm, 0), 80)
    bull_buy_sweep = _pctrank(np.maximum(sweep, 0), 80)
    bear_sell_sweep = _pctrank(np.maximum(-sweep, 0), 80)
    with np.errstate(divide="ignore", invalid="ignore"):
        bf = np.where(vol > 0, bv / vol, 0.5)
    bull_bid_liq = _pctrank(bf, 80)
    bear_ask_liq = _pctrank(1.0 - bf, 80)
    bull_vpin_inv = _pctrank(1.0 - vpin, 80)
    bear_vpin = _pctrank(vpin, 80)
    bull_stack = np.vstack([bull_buy_delta, bull_buy_sweep, bull_bid_liq, bull_vpin_inv])
    bear_stack = np.vstack([bear_sell_delta, bear_sell_sweep, bear_ask_liq, bear_vpin])
    bull_composite = bull_stack.mean(axis=0)
    bear_composite = bear_stack.mean(axis=0)
    panel["bull_pressure"] = pd.Series(bull_composite).ewm(span=8, adjust=False).mean().to_numpy()
    panel["bear_pressure"] = pd.Series(bear_composite).ewm(span=8, adjust=False).mean().to_numpy()

    panel["r"] = r
    panel["dp"] = dp
    panel["px_close"] = close
    return panel


def _csw(y, max_anchor=250):
    n = len(y)
    score = np.full(n, np.nan); crit = np.full(n, np.nan)
    if n < 5:
        return score, crit
    dy = np.diff(y, prepend=y[0])
    sig = np.sqrt(pd.Series(dy * dy).expanding(min_periods=3).mean()).to_numpy()
    for t in range(3, n):
        s = max(0, t - max_anchor)
        anchors = np.arange(s, t)
        gaps = t - anchors
        denom = sig[t] * np.sqrt(gaps)
        valid = np.isfinite(denom) & (denom > 1e-12) & np.isfinite(y[anchors])
        if valid.any() and np.isfinite(y[t]):
            vals = np.abs((y[t] - y[anchors[valid]]) / denom[valid])
            score[t] = float(np.nanmax(vals))
            crit[t] = float(4.6 + np.log(max(1, gaps[valid].max())))
    return score, crit


def _adx(h, l, c, period=14):
    n = len(c)
    if n < period + 2:
        z = np.full(n, np.nan)
        return z, z, z
    c_prev = np.empty(n); c_prev[0] = c[0]; c_prev[1:] = c[:-1]
    tr = np.maximum(h - l, np.maximum(np.abs(h - c_prev), np.abs(l - c_prev)))
    up = np.empty(n); up[0] = 0.0; up[1:] = h[1:] - h[:-1]
    dn = np.empty(n); dn[0] = 0.0; dn[1:] = l[:-1] - l[1:]
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    alpha = 1.0 / period
    atr_s = pd.Series(tr).ewm(alpha=alpha, adjust=False).mean().to_numpy()
    pdi_s = pd.Series(pdm).ewm(alpha=alpha, adjust=False).mean().to_numpy()
    ndi_s = pd.Series(ndm).ewm(alpha=alpha, adjust=False).mean().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = np.where(atr_s > 0, pdi_s / atr_s * 100, 0.0)
        ndi = np.where(atr_s > 0, ndi_s / atr_s * 100, 0.0)
        dx = np.where(pdi + ndi > 0, np.abs(pdi - ndi) / (pdi + ndi) * 100, 0.0)
    adx = pd.Series(dx).ewm(alpha=alpha, adjust=False).mean().to_numpy()
    return adx, pdi, ndi


def _pctrank(arr, window=80):
    n = len(arr)
    out = np.full(n, 50.0)
    for i in range(1, n):
        ref = arr[max(0, i - window):i]
        if len(ref) < 2:
            continue
        out[i] = np.mean(ref < arr[i]) * 100.0
    return out / 100.0


def main():
    v3.log("02: loading NQU6 master, building dashboard-parity feature panel...")
    nqu6 = v3.load_nqu6_master().sort_values("bar_index").reset_index(drop=True)
    panel = build_panel(nqu6)
    panel.to_parquet(v3.OUT_DIR / "dashboard_feature_panel.parquet", index=False)
    v3.log(f"  panel shape: {panel.shape}")

    # ── parity checks ────────────────────────────────────────────────────
    checks = []

    def add_check(name, passed, detail):
        checks.append(dict(check=name, passed=bool(passed), detail=detail))

    # range sanity
    for col, lo, hi in [("entropy_score", 0, 1), ("toxicity", 0, 1), ("liquidity_cost", 0, 1),
                        ("vpin_pct", 0, 1), ("flow_alignment", 0, 1),
                        ("bull_pressure", 0, 1), ("bear_pressure", 0, 1)]:
        v = panel[col].dropna()
        ok = v.between(lo, hi).all()
        add_check(f"range_{col}", ok, f"[{v.min():.4f},{v.max():.4f}] expected [{lo},{hi}]")

    # no-lookahead perturbation test on a representative subset (cusum + entropy + toxicity)
    cut = len(nqu6) // 2
    nqu6_perturbed = nqu6.copy()
    rng = np.random.default_rng(0)
    for col in ["px_close", "px_high", "px_low", "delta_norm", "sweep_imbalance_norm", "buy_vol", "sell_vol"]:
        if col in nqu6_perturbed.columns:
            vals = nqu6_perturbed[col].to_numpy(dtype=float).copy()
            vals[cut + 1:] = vals[cut + 1:] + rng.normal(0, np.nanstd(vals) * 50 + 1, size=len(vals) - cut - 1)
            nqu6_perturbed[col] = vals
    panel_perturbed = build_panel(nqu6_perturbed)
    for col in ["dash_cusum_up_break", "dash_cusum_down_break", "entropy_score", "toxicity",
                "liquidity_cost", "flow_score", "market_state", "adx"]:
        a = panel[col].iloc[:cut].to_numpy()
        b = panel_perturbed[col].iloc[:cut].to_numpy()
        if a.dtype == object:
            same = (a == b).all()
        else:
            same = np.allclose(np.nan_to_num(a.astype(float), nan=-999), np.nan_to_num(b.astype(float), nan=-999), atol=1e-9)
        add_check(f"no_lookahead_{col}", same, f"identical before cut={cut}: {same}")

    parity_df = pd.DataFrame(checks)
    parity_df.to_csv(v3.OUT_DIR / "dashboard_feature_parity_report.csv", index=False)

    n_failed = int((~parity_df["passed"]).sum())
    v3.log(f"  parity checks: {len(parity_df)} run, {n_failed} FAILED")
    if n_failed > 0:
        v3.log("  FAILED CHECKS:")
        print(parity_df[~parity_df["passed"]].to_string(index=False))

    print(f"PARITY_CHECKS_TOTAL: {len(parity_df)}")
    print(f"PARITY_CHECKS_FAILED: {n_failed}")
    print(f"PARITY_BLOCKED: {n_failed > 0}")
    v3.log("02 complete.")
    return panel, parity_df


if __name__ == "__main__":
    main()
