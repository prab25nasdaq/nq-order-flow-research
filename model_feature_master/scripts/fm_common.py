"""
fm_common.py - shared READ-ONLY library for the Model Feature Registry +
Feature Master Sidecar Daemon v1 build.

Every formula below is REPRODUCED (never imported/executed) from direct
line-by-line reading of:
  - ofi_live_dashboard_WORKING_NEXT_with_logreg.py (dashboard-parity formulas)
  - ofi_level_decision_tab.py (OFI Level Decision score_setup + display table)
  - the active model release's scripts/pipeline_continuous.py (existing
    model's own volume_profile_levels / classify_bar_reaction / session_label
    / OHLC-vol-path structural features)
All of this was ALREADY validated (parity-checked, no-lookahead-perturbation
tested) in the prior v3/v4 research engines this session - copied here
verbatim for self-containment (consistent with this whole project's own
established convention: each engine vendors its own copy rather than
cross-importing across folders that may not remain stable).

READ-ONLY against all production paths. Writes only inside
/home/prabh/OFI_Production/model_feature_master/.
SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

FM_ROOT = Path("/home/prabh/OFI_Production/model_feature_master")
OUT_DIR = FM_ROOT / "outputs"
REPORTS_DIR = FM_ROOT / "reports"
DATA_DIR = FM_ROOT / "data"
LOGS_DIR = FM_ROOT / "logs"
for d in (OUT_DIR, REPORTS_DIR, DATA_DIR, LOGS_DIR):
    d.mkdir(exist_ok=True, parents=True)

ACTIVE_RELEASE_LINK = Path(
    "/home/prabh/OFI_Production/model_registry/level_reaction_continuous_nq_shadow/ACTIVE_SHADOW_RELEASE")
MODEL_REGISTRY_DIR = Path(
    "/home/prabh/OFI_Production/model_registry/level_reaction_continuous_nq_shadow")
DASHBOARD_SRC = Path(
    "/mnt/wd_work/workspace/Work Place/Data/Project OFI/_codex_work/"
    "ofi_live_dashboard_WORKING_NEXT_with_logreg.py")
OFI_LEVEL_DECISION_SRC = Path("/home/prabh/OFI_Production/ofi_level_decision_tab.py")
LIVE_FEATURES_DIR = Path("/home/prabh/OFI_Live_Features")
MASTER_CONTINUOUS = LIVE_FEATURES_DIR / "master_NQ_continuous_backadjusted_shadow.ndjsonl"
MASTER_NQU6 = LIVE_FEATURES_DIR / "master_NQU6_shadow.ndjsonl"
BOOK_FLOW_CACHE_DIR = Path("/home/prabh/OFI_Production/book_flow_chart/cache")
PRED_DIR = Path("/home/prabh/OFI_Production/inference_scripts/level_reaction_continuous_nq_shadow/outputs")
V4_DIR = Path("/home/prabh/OFI_Production/research_engines/afml_label_policy_parity_entry_cp_v4_20260623T192440Z")
V3_DIR = Path("/home/prabh/OFI_Production/research_engines/afml_dashboard_parity_cusum_sr_entry_cp_v3_20260623T184026Z")

TICK_SIZE = 0.25
NEAR_TICKS_K = 4
NEAR_TICKS_PRICE = TICK_SIZE * NEAR_TICKS_K

# raw absolute price + HIGH-leakage columns this feature master must NEVER
# emit as training-safe (Part A/B finding: discovered in v4, corrected here)
EXCLUDED_HIGH_LEAKAGE = {"master_px_close", "master_px_high", "master_px_low",
                         "swing_levels", "compute_sr_levels"}


def log(msg: str) -> None:
    import time
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def resolved_active_release() -> Path:
    return ACTIVE_RELEASE_LINK.resolve()


def read_ndjsonl(path: Path) -> pd.DataFrame:
    rows = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    return pd.DataFrame(rows)


def read_ndjsonl_last_n_lines(path: Path, n_lines: int) -> pd.DataFrame:
    """Tail-read for the daemon's incremental poll - never loads the full
    multi-GB master file when only the newest rows are needed."""
    from collections import deque
    tail = deque(maxlen=n_lines)
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                tail.append(line)
    rows = []
    for line in tail:
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return pd.DataFrame(rows)


def load_nqu6_master(dedup: bool = True) -> pd.DataFrame:
    df = read_ndjsonl(MASTER_NQU6)
    df = df.sort_values("bar_end_ts_ns", kind="stable").reset_index(drop=True)
    if dedup:
        df = df.drop_duplicates(subset=["bar_end_ts_ns"], keep="first").reset_index(drop=True)
    return df


def load_continuous_master(dedup: bool = True) -> pd.DataFrame:
    df = read_ndjsonl(MASTER_CONTINUOUS)
    df["bar_end_ts_ns"] = pd.to_numeric(df["bar_end_ts_ns"], errors="coerce")
    if dedup:
        df = df.drop_duplicates(subset=["bar_end_ts_ns"], keep="first")
    return df.sort_values("bar_end_ts_ns", kind="stable").reset_index(drop=True)


def load_predictions() -> Optional[pd.DataFrame]:
    path = PRED_DIR / "latest_continuous_nq_predictions.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df["_orig_idx"] = np.arange(len(df))
    return df


def list_level_candle_dates(depth: int = 10) -> List[str]:
    import glob
    pattern = str(BOOK_FLOW_CACHE_DIR / f"book_flow_level_candles_NQU6_*_top{depth}.parquet")
    return sorted({Path(f).name.split("_")[5] for f in glob.glob(pattern)})


def load_level_candles(depth: int = 10, dates: List[str] = None) -> pd.DataFrame:
    frames = []
    dates = dates if dates is not None else list_level_candle_dates(depth)
    for d in dates:
        path = BOOK_FLOW_CACHE_DIR / f"book_flow_level_candles_NQU6_{d}_top{depth}.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        df["session_date"] = d
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def aggregate_book_flow_bars(level_df: pd.DataFrame) -> pd.DataFrame:
    df = level_df[level_df["bar_state"] == "CLOSED"]
    if df.empty:
        return pd.DataFrame()
    agg = (
        df.groupby(["session_date", "bar_idx"])
        .agg(bid_add=("bid_add", "sum"), bid_pull=("bid_pull", "sum"),
            ask_add=("ask_add", "sum"), ask_pull=("ask_pull", "sum"),
            signed_flow=("signed_flow", "sum"), abs_flow=("abs_flow", "sum"),
            net_bid_flow=("net_bid_flow", "sum"), net_ask_flow=("net_ask_flow", "sum"),
            close_price=("close_price", "last"), bar_end_ts_ns=("bar_end_ts_ns", "last"),
            timestamp_utc=("timestamp_utc", "last"))
        .reset_index().sort_values(["session_date", "bar_idx"]).reset_index(drop=True)
    )
    agg["bid_pull_pressure"] = agg["bid_pull"] / agg["abs_flow"].replace(0, np.nan)
    agg["ask_pull_pressure"] = agg["ask_pull"] / agg["abs_flow"].replace(0, np.nan)
    agg["ask_pull_minus_bid_pull"] = agg["ask_pull_pressure"] - agg["bid_pull_pressure"]
    agg["bid_add_minus_ask_add"] = agg["bid_add"] - agg["ask_add"]
    eps = 1e-9
    agg["tab_bid_pull_pressure"] = agg["bid_pull"] / (agg["bid_add"] + agg["bid_pull"] + eps).clip(lower=eps)
    agg["tab_ask_pull_pressure"] = agg["ask_pull"] / (agg["ask_add"] + agg["ask_pull"] + eps).clip(lower=eps)

    native_rows = []
    for (sd, bidx), grp in df.groupby(["session_date", "bar_idx"]):
        close = float(grp["close_price"].iloc[-1])
        rec = {"session_date": sd, "bar_idx": bidx}
        for ltype in ["POC", "VAH", "VAL", "HVN", "LVN"]:
            rows = grp[grp["nearest_level"] == ltype]
            if rows.empty:
                rec[f"native_{ltype}"] = np.nan
                continue
            prices = (rows["price_level"] - rows["distance_to_nearest_level"]).round(2)
            uniq = prices.unique()
            rec[f"native_{ltype}"] = float(uniq[np.argmin(np.abs(uniq - close))]) if len(uniq) > 1 else float(uniq[0])
        native_rows.append(rec)
    native_df = pd.DataFrame(native_rows)
    return agg.merge(native_df, on=["session_date", "bar_idx"], how="left")


# ═════════════════════════════════════════════════════════════════════════
# DASHBOARD-PARITY FORMULAS (verbatim from v3_common.py / v4_common.py,
# already validated: 15 parity checks passed in the v3 build)
# ═════════════════════════════════════════════════════════════════════════
def residual_z(s: pd.Series, window: int = 20) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce").astype("float64")
    roll = x.rolling(window, min_periods=window).mean()
    res = x - roll
    std = res.rolling(window, min_periods=window).std(ddof=1)
    return res / std.replace(0.0, np.nan)


def causal_z(values: np.ndarray, window: int = 128, min_ref: int = 30) -> np.ndarray:
    s = pd.Series(np.asarray(values, dtype=float)).replace([np.inf, -np.inf], np.nan)
    mu = s.rolling(window, min_periods=min_ref).mean().shift(1)
    sd = s.rolling(window, min_periods=min_ref).std().shift(1).replace(0, np.nan)
    return ((s - mu) / sd).replace([np.inf, -np.inf], np.nan).to_numpy()


def cusum_breaks(r: np.ndarray, halflife: int = 64, k: float = 0.35, h: float = 5.0) -> dict:
    rr = pd.Series(r).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    mu = rr.ewm(halflife=halflife, adjust=False).mean()
    sd = rr.ewm(halflife=halflife, adjust=False).std().replace(0, np.nan)
    z = ((rr - mu) / sd).replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy()
    sp = np.zeros(len(z)); sm = np.zeros(len(z))
    up = np.zeros(len(z), dtype=bool); dn = np.zeros(len(z), dtype=bool)
    for i in range(1, len(z)):
        sp[i] = max(0.0, sp[i - 1] + z[i] - k)
        sm[i] = min(0.0, sm[i - 1] + z[i] + k)
        if sp[i] > h:
            up[i] = True; sp[i] = 0.0
        if sm[i] < -h:
            dn[i] = True; sm[i] = 0.0
    return {"z": z, "up": up, "down": dn}


def rolling_kyle(mid, q, h: int = 1, window: int = 160) -> np.ndarray:
    n = len(mid)
    dp = np.full(n, np.nan); q_lag = np.full(n, np.nan)
    if n > 1:
        dp[1:] = mid[1:] - mid[:-1]; q_lag[1:] = q[:-1]
    s_dp, s_q = pd.Series(dp), pd.Series(q_lag)
    cov = s_dp.rolling(window, min_periods=max(20, window // 4)).cov(s_q)
    var = s_q.rolling(window, min_periods=max(20, window // 4)).var()
    return (cov / var.replace(0, np.nan)).to_numpy()


def roll_measure(dp, window: int = 160) -> np.ndarray:
    s = pd.Series(dp)
    cov = s.rolling(window, min_periods=max(20, window // 4)).cov(s.shift(1))
    return 2.0 * np.sqrt(np.maximum(0.0, -cov.to_numpy()))


def corwin_schulz(hi, lo) -> np.ndarray:
    hi = np.asarray(hi, dtype=float); lo = np.asarray(lo, dtype=float)
    out = np.full(len(hi), np.nan)
    if len(hi) < 2:
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        hl = np.log(np.maximum(hi, 1e-12) / np.maximum(lo, 1e-12))
        beta = hl ** 2 + np.r_[np.nan, hl[:-1] ** 2]
        h2 = np.maximum(hi, np.r_[np.nan, hi[:-1]])
        l2 = np.minimum(lo, np.r_[np.nan, lo[:-1]])
        gamma = np.log(np.maximum(h2, 1e-12) / np.maximum(l2, 1e-12)) ** 2
        den = 3.0 - 2.0 * np.sqrt(2.0)
        alpha = (np.sqrt(2.0 * beta) - np.sqrt(beta)) / den - np.sqrt(gamma / den)
        sp = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))
    out[:] = np.maximum(0.0, sp)
    return out


def rolling_pct(values, window: int = 250) -> np.ndarray:
    vals = np.asarray(values, dtype=float)
    out = np.full(len(vals), np.nan)
    for i in range(len(vals)):
        ref = vals[max(0, i - window + 1):i + 1]
        ref = ref[np.isfinite(ref)]
        if len(ref) >= 3 and np.isfinite(vals[i]):
            out[i] = np.mean(ref <= vals[i])
    return out


def quantile_codes(values, k: int = 5, window: int = 256, min_ref: int = 40) -> np.ndarray:
    vals = np.asarray(values, dtype=float)
    out = np.full(len(vals), -1, dtype=int)
    for i, v in enumerate(vals):
        if not np.isfinite(v):
            continue
        ref = vals[max(0, i - window):i]
        ref = ref[np.isfinite(ref)]
        if len(ref) < max(k, min_ref):
            continue
        qs = np.quantile(ref, np.linspace(0, 1, k + 1)[1:-1])
        out[i] = int(np.digitize(v, qs))
    return out


def rolling_abs_quantile(values, q: float = 0.60, window: int = 256, min_ref: int = 40, default: float = 0.10) -> np.ndarray:
    vals = np.asarray(values, dtype=float)
    out = np.full(len(vals), default, dtype=float)
    av = np.abs(vals)
    for i in range(len(vals)):
        ref = av[max(0, i - window):i]
        ref = ref[np.isfinite(ref)]
        if len(ref) >= min_ref:
            out[i] = float(np.nanquantile(ref, q))
    return out


def norm_entropy(codes, k: int) -> float:
    c = np.asarray(codes)
    c = c[(c >= 0) & np.isfinite(c)]
    if len(c) < 3 or k <= 1:
        return np.nan
    counts = np.bincount(c.astype(int), minlength=k).astype(float)
    p = counts[counts > 0] / counts.sum()
    return float(np.clip(-float(np.sum(p * np.log(p))) / np.log(k), 0.0, 1.0))


def rolling_entropy(codes, k: int, window: int = 128) -> np.ndarray:
    codes = np.asarray(codes, dtype=float)
    out = np.full(len(codes), np.nan)
    min_n = max(8, min(window // 4, 32))
    for i in range(len(codes)):
        sl = codes[max(0, i - window + 1):i + 1]
        if np.sum((sl >= 0) & np.isfinite(sl)) >= min_n:
            out[i] = norm_entropy(sl, k)
    return out


def ema_clean(values, span: int = 5, fill: float = 0.0) -> np.ndarray:
    return pd.Series(values).replace([np.inf, -np.inf], np.nan).fillna(fill).ewm(span=span, adjust=False).mean().to_numpy()


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
            score[t] = float(np.nanmax(vals)); crit[t] = float(4.6 + np.log(max(1, gaps[valid].max())))
    return score, crit


def _adx(h, l, c, period=14):
    n = len(c)
    if n < period + 2:
        z = np.full(n, np.nan); return z, z, z
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


def build_dashboard_panel(nqu6: pd.DataFrame) -> pd.DataFrame:
    """Verbatim reproduction of v3's 02_dashboard_feature_builder.py::build_panel
    (already validated: 15/15 parity checks passed in the v3 build)."""
    n = len(nqu6)
    close = nqu6["px_close"].to_numpy(dtype=float)
    high = nqu6["px_high"].to_numpy(dtype=float)
    low = nqu6["px_low"].to_numpy(dtype=float)
    mid = nqu6["mid_mean"].to_numpy(dtype=float) if "mid_mean" in nqu6.columns else close
    mid = np.where(np.isfinite(mid), mid, close)
    bv = nqu6["buy_vol"].to_numpy(dtype=float); sv = nqu6["sell_vol"].to_numpy(dtype=float)
    vol = bv + sv; q = bv - sv
    dollar_vol = mid * np.maximum(vol, 0.0)
    delta_norm = nqu6["delta_norm"].to_numpy(dtype=float)
    sweep = nqu6["sweep_imbalance_norm"].to_numpy(dtype=float)
    ofi = nqu6["mlofi_norm"].to_numpy(dtype=float) if "mlofi_norm" in nqu6.columns else delta_norm

    y = np.log(np.maximum(mid, 1e-12))
    r = np.full(n, np.nan); r[1:] = y[1:] - y[:-1]
    dp = np.full(n, np.nan); dp[1:] = mid[1:] - mid[:-1]

    panel = pd.DataFrame({"bar_idx": nqu6["bar_index"].to_numpy(), "day": nqu6["day"].to_numpy(),
                          "bar_end_ts_ns": nqu6["bar_end_ts_ns"].to_numpy()})

    panel["mid_resid_z"] = residual_z(pd.Series(mid), window=20).to_numpy()
    for col in ["delta_norm", "volatility_5", "sweep_imbalance_norm", "vpin"]:
        if col in nqu6.columns:
            panel[f"{col}_resid_z20"] = residual_z(pd.to_numeric(nqu6[col], errors="coerce"), window=20).to_numpy()

    cb = cusum_breaks(r, halflife=64, k=0.35, h=5.0)
    panel["dash_cusum_up_break"] = cb["up"]; panel["dash_cusum_down_break"] = cb["down"]
    panel["dash_cusum_z"] = cb["z"]

    ret_z = causal_z(r, window=128, min_ref=30)
    ret_codes = np.full(n, -1, dtype=int)
    vmask = np.isfinite(ret_z)
    ret_codes[vmask & (ret_z <= -1.5)] = 0
    ret_codes[vmask & (ret_z > -1.5) & (ret_z <= -0.5)] = 1
    ret_codes[vmask & (ret_z > -0.5) & (ret_z < 0.5)] = 2
    ret_codes[vmask & (ret_z >= 0.5) & (ret_z < 1.5)] = 3
    ret_codes[vmask & (ret_z >= 1.5)] = 4
    panel["dash_ret_z"] = ret_z

    delta_codes = quantile_codes(delta_norm, 5, 256, 40)
    ofi_codes = quantile_codes(ofi, 5, 256, 40)
    delta_thr = np.maximum(rolling_abs_quantile(delta_norm, 0.60, 256, 40), 0.05)
    ofi_thr = np.maximum(rolling_abs_quantile(ofi, 0.60, 256, 40), 0.05)
    sw_thr = np.maximum(rolling_abs_quantile(sweep, 0.60, 256, 40), 0.05)
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

    ent_return = rolling_entropy(ret_codes, 5, 128)
    ent_delta = rolling_entropy(delta_codes, 5, 128)
    ent_ofi = rolling_entropy(ofi_codes, 5, 128)
    ent_sweep = rolling_entropy(sweep_codes, 3, 128)
    ent_spread = rolling_entropy(spread_codes, 4, 128)
    ent_flow = rolling_entropy(flow_joint_codes, 27, 128)
    panel["entropy_return"] = ent_return; panel["entropy_delta"] = ent_delta; panel["entropy_ofi"] = ent_ofi
    panel["entropy_sweep"] = ent_sweep; panel["entropy_spread"] = ent_spread; panel["entropy_flow"] = ent_flow
    panel["entropy_score"] = (
        0.35 * pd.Series(ent_return).ffill().fillna(0.5).to_numpy()
        + 0.35 * pd.Series(ent_flow).ffill().fillna(0.5).to_numpy()
        + 0.15 * pd.Series(ent_spread).ffill().fillna(0.5).to_numpy()
        + 0.15 * (pd.Series(ent_delta).ffill().fillna(0.5).to_numpy()
                  + pd.Series(ent_ofi).ffill().fillna(0.5).to_numpy()
                  + pd.Series(ent_sweep).ffill().fillna(0.5).to_numpy()) / 3.0
    )

    delta_z = causal_z(delta_norm, 128, 30); ofi_z = causal_z(ofi, 128, 30); sweep_z = causal_z(sweep, 128, 30)
    flow_score_raw = (0.40 * np.tanh(np.nan_to_num(delta_z, nan=0.0) / 2.0)
                      + 0.35 * np.tanh(np.nan_to_num(ofi_z, nan=0.0) / 2.0)
                      + 0.25 * np.tanh(np.nan_to_num(sweep_z, nan=0.0) / 2.0))
    flow_score = ema_clean(flow_score_raw, span=5, fill=0.0)
    flow_slope = np.r_[np.nan, np.diff(flow_score)]
    price_slope_z = causal_z(r, 64, 20); flow_slope_z = causal_z(flow_slope, 64, 20)
    panel["flow_score"] = flow_score
    panel["flow_divergence"] = price_slope_z - flow_slope_z
    panel["flow_alignment"] = np.clip(np.abs(flow_score), 0.0, 1.0)
    panel["flow_direction"] = np.where(flow_score > 0.05, 1, np.where(flow_score < -0.05, -1, 0))

    if "vpin" in nqu6.columns:
        vpin = pd.to_numeric(nqu6["vpin"], errors="coerce").ffill().fillna(0.5).to_numpy()
    else:
        imb = np.abs(bv - sv)
        vpin = (pd.Series(imb).rolling(75, min_periods=10).sum() / pd.Series(vol).rolling(75, min_periods=10).sum()).fillna(0.5).to_numpy()
    vpin_pct = rolling_pct(vpin, 250)
    panel["vpin_pct"] = vpin_pct
    panel["vpin_state"] = np.select([vpin_pct < 0.70, vpin_pct < 0.90, vpin_pct < 0.95],
                                    ["NORMAL", "ELEVATED", "TOXIC"], default="EXTREME_TOXICITY")

    kyle = rolling_kyle(mid, q, 1, 160)
    with np.errstate(divide="ignore", invalid="ignore"):
        amihud_raw = np.where(dollar_vol > 0, np.abs(dp) / dollar_vol, np.nan)
    amihud = pd.Series(amihud_raw).rolling(160, min_periods=30).median().to_numpy()
    roll = roll_measure(dp, 160)
    roll_impact = roll / pd.Series(dollar_vol).rolling(160, min_periods=30).mean().to_numpy()
    spread_raw = high - low
    cs_spread = corwin_schulz(high, low)
    spread_pct = rolling_pct(spread_raw, 250); kyle_pct = rolling_pct(np.abs(kyle), 250)
    amihud_pct = rolling_pct(amihud, 250); roll_pct = rolling_pct(roll_impact, 250); cs_pct = rolling_pct(cs_spread, 250)
    panel["kyle"] = kyle; panel["amihud"] = amihud; panel["roll_impact"] = roll_impact; panel["cs_spread"] = cs_spread
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
    panel["toxicity"] = toxicity; panel["liquidity_cost"] = liq_cost

    csw_score, csw_crit = _csw(y)
    csw_pct = rolling_pct(csw_score, 250)
    panel["csw_score"] = csw_score
    break_score = np.maximum.reduce([cb["up"].astype(float), cb["down"].astype(float), pd.Series(csw_pct).fillna(0).to_numpy()])
    panel["break_score"] = break_score
    break_event = (cb["up"] | cb["down"] | (np.isfinite(csw_score) & np.isfinite(csw_crit) & (csw_score > csw_crit)))
    break_age = np.zeros(n, dtype=int); last_break = -1
    for i in range(n):
        if break_event[i]:
            last_break = i
        break_age[i] = i - last_break if last_break >= 0 else i
    panel["break_age"] = break_age

    e_latest = pd.Series(panel["entropy_score"]).fillna(0.5).to_numpy()
    flow_align = panel["flow_alignment"].to_numpy(); tox = panel["toxicity"].to_numpy()
    sp_pct = pd.Series(panel["spread_pct"]).fillna(0.5).to_numpy()
    market_state = np.full(n, "NO_EDGE", dtype=object)
    market_state[(tox > 0.90) & (sp_pct > 0.80)] = "TOXIC_FLOW"
    market_state[(break_score > 0.85) & (break_age <= 20)] = "REGIME_BREAK"
    market_state[(e_latest > 0.65) & (flow_align < 0.67)] = "CHOP_RANDOM"
    market_state[(e_latest < 0.35) & (flow_align >= 0.67) & (tox < 0.90)] = "CLEAN_TREND"
    panel["market_state"] = market_state

    adx, pdi, ndi = _adx(high, low, close, 14)
    panel["adx"] = adx; panel["plus_di"] = pdi; panel["minus_di"] = ndi

    bull_buy_delta = _pctrank(np.maximum(delta_norm, 0), 80)
    bear_sell_delta = _pctrank(np.maximum(-delta_norm, 0), 80)
    bull_buy_sweep = _pctrank(np.maximum(sweep, 0), 80)
    bear_sell_sweep = _pctrank(np.maximum(-sweep, 0), 80)
    with np.errstate(divide="ignore", invalid="ignore"):
        bf = np.where(vol > 0, bv / vol, 0.5)
    bull_bid_liq = _pctrank(bf, 80); bear_ask_liq = _pctrank(1.0 - bf, 80)
    bull_vpin_inv = _pctrank(1.0 - vpin, 80); bear_vpin = _pctrank(vpin, 80)
    bull_composite = np.vstack([bull_buy_delta, bull_buy_sweep, bull_bid_liq, bull_vpin_inv]).mean(axis=0)
    bear_composite = np.vstack([bear_sell_delta, bear_sell_sweep, bear_ask_liq, bear_vpin]).mean(axis=0)
    panel["bull_pressure"] = pd.Series(bull_composite).ewm(span=8, adjust=False).mean().to_numpy()
    panel["bear_pressure"] = pd.Series(bear_composite).ewm(span=8, adjust=False).mean().to_numpy()

    panel["r"] = r; panel["dp"] = dp; panel["px_close"] = close
    return panel


# ═════════════════════════════════════════════════════════════════════════
# OFI LEVEL DECISION (verbatim from v3's 03_ofi_level_decision_parity.py,
# already validated: 6/6 parity checks passed)
# ═════════════════════════════════════════════════════════════════════════
LEVEL_TYPES_OFILD = ["POC", "VAH", "VAL", "HVN", "LVN"]
NEAR_THRESH_TK_OFILD = 8
N_RECENT_SCORE = 5
DISPLAY_WINDOW_OFILD = 10


def find_nearest_level_ofild(current_price, level_dict, tick_size=TICK_SIZE):
    cands = []
    for ltype, lprice in level_dict.items():
        if lprice is None or not np.isfinite(lprice):
            continue
        dist_pts = current_price - lprice
        cands.append(dict(level_type=ltype, level_price=lprice, distance_pts=abs(dist_pts),
                          distance_ticks=abs(dist_pts) / tick_size, signed_dist_pts=dist_pts, source="native"))
    if not cands:
        return None
    cands.sort(key=lambda x: x["distance_ticks"])
    return cands[0]


def score_setup_ofild(closes, ba, bp, aa, ap, signed, abs_, t, level_dict, near_thresh_ticks, n_recent, session):
    if t < 1:
        return None
    current_price = float(closes[t])
    nearest = find_nearest_level_ofild(current_price, level_dict)
    if nearest is None or nearest["distance_ticks"] > near_thresh_ticks:
        return dict(active=False, setup_type="WAITING_FOR_LEVEL", direction_bias="NEUTRAL",
                    confidence_score=0, trust_state="NO_EDGE", level_type=None, level_price=np.nan,
                    distance_ticks=np.nan, distance_pts=np.nan, ofi_bias="NEUTRAL", session=session,
                    current_price=current_price, breakout_attempt_dir=0)
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
    pull_score = int(max(0, min(20, 10 + ask_minus_bid_pp * 40))) if is_near_support else int(max(0, min(20, 10 - ask_minus_bid_pp * 40)))
    price_resp_score = 7; price_crossed_up = price_crossed_dn = False
    if t >= 2:
        prev_close = float(closes[t-1]); prev2_close = float(closes[t-2])
        price_crossed_up = prev_close < level_price <= current_price
        price_crossed_dn = prev_close > level_price >= current_price
        if is_near_support:
            price_resp_score = 12 if (current_price >= level_price and prev_close >= level_price) else 9 if current_price >= level_price - TICK_SIZE * 2 else 4
        else:
            price_resp_score = 12 if (current_price <= level_price and prev_close <= level_price) else 9 if current_price <= level_price + TICK_SIZE * 2 else 4
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
    return dict(active=True, setup_type=setup_type, direction_bias=direction_bias, confidence_score=total_score,
               trust_state=trust_state, level_type=nearest["level_type"], level_price=level_price,
               distance_ticks=dt, distance_pts=nearest["distance_pts"], ofi_bias=ofi_bias, session=session,
               current_price=current_price, breakout_attempt_dir=breakout_attempt_dir)


def assign_session_old_tab(hour_utc: int) -> str:
    if 7 <= hour_utc < 13:
        return "London"
    elif 13 <= hour_utc < 21:
        return "US"
    return "Asia_Overnight"


def build_ofild_panel(agg: pd.DataFrame) -> pd.DataFrame:
    agg = agg.sort_values(["session_date", "bar_idx"]).reset_index(drop=True)
    agg["hour_utc"] = pd.to_datetime(agg["timestamp_utc"]).dt.hour
    agg["session"] = agg["hour_utc"].apply(assign_session_old_tab)

    score_rows = []
    for sd, g in agg.groupby("session_date", sort=False):
        g = g.sort_values("bar_idx").reset_index(drop=True)
        closes = g["close_price"].to_numpy(); ba = g["bid_add"].to_numpy(); bp = g["bid_pull"].to_numpy()
        aa = g["ask_add"].to_numpy(); ap = g["ask_pull"].to_numpy(); signed = g["signed_flow"].to_numpy()
        abs_ = g["abs_flow"].to_numpy(); sessions = g["session"].to_numpy()
        level_cols = {lt: g[f"native_{lt}"].to_numpy() for lt in LEVEL_TYPES_OFILD}
        for t in range(len(g)):
            level_dict = {lt: level_cols[lt][t] for lt in LEVEL_TYPES_OFILD}
            res = score_setup_ofild(closes, ba, bp, aa, ap, signed, abs_, t, level_dict,
                                    NEAR_THRESH_TK_OFILD, N_RECENT_SCORE, sessions[t])
            if res is None:
                continue
            res["bar_idx"] = g["bar_idx"].iat[t]; res["session_date"] = sd
            score_rows.append(res)
    score_df = pd.DataFrame(score_rows)

    feat_rows = []
    for sd, g in agg.groupby("session_date", sort=False):
        g = g.sort_values("bar_idx").reset_index(drop=True)
        n = len(g)
        bid_add, bid_pull, ask_add, ask_pull = g["bid_add"].to_numpy(), g["bid_pull"].to_numpy(), g["ask_add"].to_numpy(), g["ask_pull"].to_numpy()
        net_bid, net_ask, signed = g["net_bid_flow"].to_numpy(), g["net_ask_flow"].to_numpy(), g["signed_flow"].to_numpy()
        bidpp, askpp = g["tab_bid_pull_pressure"].to_numpy(), g["tab_ask_pull_pressure"].to_numpy()
        dir_lbl = np.where(signed > 100, 1, np.where(signed < -100, -1, 0))
        for t in range(n):
            lo = max(0, t - DISPLAY_WINDOW_OFILD + 1)
            w = slice(lo, t + 1); wlen = t - lo + 1
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
                session_date=sd, bar_idx=g["bar_idx"].iat[t], bar_end_ts_ns=g["bar_end_ts_ns"].iat[t], window_n=wlen,
                last_BidAdd=bid_add[t], last_BidPull=bid_pull[t], last_AskAdd=ask_add[t], last_AskPull=ask_pull[t],
                last_NetBid=net_bid[t], last_NetAsk=net_ask[t], last_Signed=signed[t], last_BidPP=bidpp[t],
                last_AskPP=askpp[t], last_Dir=int(dir_lbl[t]),
                sum_window_BidAdd=bid_add[w].sum(), sum_window_BidPull=bid_pull[w].sum(),
                sum_window_AskAdd=ask_add[w].sum(), sum_window_AskPull=ask_pull[w].sum(),
                sum_window_NetBid=net_bid[w].sum(), sum_window_NetAsk=net_ask[w].sum(), sum_window_Signed=signed_w.sum(),
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
                  "trust_state", "level_type", "level_price", "distance_ticks", "distance_pts",
                  "ofi_bias", "session", "breakout_attempt_dir"]],
        on=["session_date", "bar_idx"], how="left")
    return panel


# ═════════════════════════════════════════════════════════════════════════
# EXISTING MODEL's own structural features (verbatim from
# pipeline_continuous.py - the 77-feature dashboard model's own pipeline)
# ═════════════════════════════════════════════════════════════════════════
def volume_profile_levels(df_day: pd.DataFrame) -> Dict[str, Any]:
    h = pd.to_numeric(df_day["continuous_high"], errors="coerce").to_numpy()
    l = pd.to_numeric(df_day["continuous_low"], errors="coerce").to_numpy()
    vt = pd.to_numeric(df_day["vol_total"], errors="coerce").to_numpy()
    valid = np.isfinite(h) & np.isfinite(l)
    pmin = float(np.nanmin(l[valid])); pmax = float(np.nanmax(h[valid]))
    lo_t = int(round(pmin / TICK_SIZE)) - 2; hi_t = int(round(pmax / TICK_SIZE)) + 2
    n_lev = hi_t - lo_t + 1
    levels = np.array([round(j * TICK_SIZE, 2) for j in range(lo_t, hi_t + 1)])
    lev_lo = levels - TICK_SIZE * 0.5; lev_hi = levels + TICK_SIZE * 0.5
    tot_v = np.zeros(n_lev)
    for i in range(len(df_day)):
        if not (np.isfinite(h[i]) and np.isfinite(l[i])):
            continue
        br = max(h[i] - l[i], TICK_SIZE)
        ov = np.minimum(h[i], lev_hi) - np.maximum(l[i], lev_lo)
        m = ov > 0
        if not m.any():
            continue
        tot_v += vt[i] * np.where(m, ov / br, 0.0)
    if tot_v.max() == 0:
        raise RuntimeError("zero-volume window")
    poc_idx = int(np.argmax(tot_v))
    tot_sum = tot_v.sum(); va_target = tot_sum * 0.70
    va_vol = tot_v[poc_idx]; vah_i = poc_idx; val_i = poc_idx
    while va_vol < va_target and (val_i > 0 or vah_i < n_lev - 1):
        up = tot_v[vah_i + 1] if vah_i < n_lev - 1 else 0
        down = tot_v[val_i - 1] if val_i > 0 else 0
        if up >= down:
            vah_i += 1; va_vol += up
        else:
            val_i -= 1; va_vol += down
    poc_px = float(levels[poc_idx]); vah_px = float(levels[vah_i]); val_px = float(levels[val_i])
    k = max(3, n_lev // 15)
    smooth = np.convolve(tot_v, np.ones(k) / k, mode="same")
    is_hvn = (tot_v > 0) & (tot_v > smooth * 1.45)
    is_lvn = (tot_v > 0) & (tot_v < smooth * 0.55)
    return {"poc_px": poc_px, "vah_px": vah_px, "val_px": val_px,
           "hvn_px": levels[is_hvn].tolist(), "lvn_px": levels[is_lvn].tolist()}


def session_label(minute_of_day: int) -> str:
    if minute_of_day < 360: return "Asia"
    if minute_of_day < 720: return "EU"
    if minute_of_day < 870: return "US_Open"
    if minute_of_day < 1080: return "US_AM"
    if minute_of_day < 1320: return "US_PM"
    return "US_Late"


def classify_bar_reaction(h, l, c, o, level_px, level_name, delta, mid_z, abs_z, prior_close, near_ticks_k=NEAR_TICKS_K):
    near_ticks_price = TICK_SIZE * near_ticks_k
    if not (np.isfinite(h) and np.isfinite(l) and np.isfinite(c)):
        return None
    touched = (l - TICK_SIZE * 0.5 <= level_px <= h + TICK_SIZE * 0.5)
    close_to = abs(c - level_px) <= near_ticks_price
    if not (touched or close_to):
        return None
    if np.isfinite(abs_z) and abs_z > 1.5 and abs(c - o) < TICK_SIZE:
        return f"{level_name}_absorption"
    came_from_above = np.isfinite(prior_close) and (prior_close > level_px + TICK_SIZE * 0.5)
    came_from_below = np.isfinite(prior_close) and (prior_close < level_px - TICK_SIZE * 0.5)
    if touched:
        if c > level_px and l < level_px:
            return f"{level_name}_rejection_from_above"
        if c < level_px and h > level_px:
            return f"{level_name}_rejection_from_below"
    if np.isfinite(delta) and abs(delta) > 1.0 and abs(c - level_px) > near_ticks_price:
        if c > level_px and came_from_below:
            return f"breakout_acceptance_above_{level_name}"
        if c < level_px and came_from_above:
            return f"breakdown_acceptance_below_{level_name}"
    if touched and abs(c - o) < TICK_SIZE and not (np.isfinite(abs_z) and abs_z > 1.5):
        return f"{level_name}_neutral_touch"
    return None


def build_structural_features(nqu6_day: pd.DataFrame) -> pd.DataFrame:
    """The existing model's own 44 structural features (dist_to_*, OHLC-vol
    path, touch tracking, lvl_*/sess_*/rxn_* one-hots) for ONE day's bars.
    Verbatim from pipeline_continuous.py's build_level_stream / build_event_
    features OHLC-vol helper / session_label / classify_bar_reaction."""
    g = nqu6_day.reset_index(drop=True).copy()
    g["continuous_high"] = g["px_high"]; g["continuous_low"] = g["px_low"]; g["continuous_close"] = g["px_close"]
    g["continuous_open"] = g["px_open"]
    n = len(g)
    out = pd.DataFrame({"bar_end_ts_ns": g["bar_end_ts_ns"]})
    try:
        lv = volume_profile_levels(g)
    except RuntimeError:
        for col in ["dist_to_poc_ticks", "dist_to_vah_ticks", "dist_to_val_ticks", "dist_to_hvn_ticks",
                   "dist_to_lvn_ticks", "dist_to_poc_vol", "dist_to_vah_vol", "dist_to_val_vol",
                   "dist_to_hvn_vol", "dist_to_lvn_vol", "inside_value_area", "above_vah", "below_val"]:
            out[col] = np.nan
        lv = None

    c = pd.to_numeric(g["px_close"], errors="coerce").to_numpy()
    h = pd.to_numeric(g["px_high"], errors="coerce").to_numpy()
    l = pd.to_numeric(g["px_low"], errors="coerce").to_numpy()
    o_ = pd.to_numeric(g["px_open"], errors="coerce").to_numpy()
    vol_ = pd.to_numeric(g["volatility_5"], errors="coerce").replace(0, np.nan) if "volatility_5" in g.columns else pd.Series(np.nan, index=g.index)
    vol = vol_.to_numpy() if hasattr(vol_, "to_numpy") else vol_

    if lv is not None:
        hvn_arr = np.array(lv["hvn_px"]) if lv["hvn_px"] else np.array([np.nan])
        lvn_arr = np.array(lv["lvn_px"]) if lv["lvn_px"] else np.array([np.nan])
        dist_to_poc_ticks = np.full(n, np.nan); dist_to_vah_ticks = np.full(n, np.nan); dist_to_val_ticks = np.full(n, np.nan)
        dist_to_hvn_ticks = np.full(n, np.nan); dist_to_lvn_ticks = np.full(n, np.nan)
        dist_to_poc_vol = np.full(n, np.nan); dist_to_vah_vol = np.full(n, np.nan); dist_to_val_vol = np.full(n, np.nan)
        dist_to_hvn_vol = np.full(n, np.nan); dist_to_lvn_vol = np.full(n, np.nan)
        inside_va = np.zeros(n, dtype=int); above_vah = np.zeros(n, dtype=int); below_val = np.zeros(n, dtype=int)
        for i in range(n):
            ci = c[i]
            if not np.isfinite(ci):
                continue
            vi = vol[i] if (np.isfinite(vol[i]) and vol[i] > 1e-9) else np.nan
            nh_px = float(hvn_arr[np.argmin(np.abs(hvn_arr - ci))]) if hvn_arr.size else np.nan
            nl_px = float(lvn_arr[np.argmin(np.abs(lvn_arr - ci))]) if lvn_arr.size else np.nan
            dist_to_poc_ticks[i] = (ci - lv["poc_px"]) / TICK_SIZE
            dist_to_vah_ticks[i] = (ci - lv["vah_px"]) / TICK_SIZE
            dist_to_val_ticks[i] = (ci - lv["val_px"]) / TICK_SIZE
            dist_to_hvn_ticks[i] = (ci - nh_px) / TICK_SIZE if np.isfinite(nh_px) else np.nan
            dist_to_lvn_ticks[i] = (ci - nl_px) / TICK_SIZE if np.isfinite(nl_px) else np.nan
            if np.isfinite(vi):
                dist_to_poc_vol[i] = (ci - lv["poc_px"]) / vi
                dist_to_vah_vol[i] = (ci - lv["vah_px"]) / vi
                dist_to_val_vol[i] = (ci - lv["val_px"]) / vi
                if np.isfinite(nh_px): dist_to_hvn_vol[i] = (ci - nh_px) / vi
                if np.isfinite(nl_px): dist_to_lvn_vol[i] = (ci - nl_px) / vi
            inside_va[i] = int(lv["val_px"] <= ci <= lv["vah_px"])
            above_vah[i] = int(ci > lv["vah_px"]); below_val[i] = int(ci < lv["val_px"])
        out["dist_to_poc_ticks"] = dist_to_poc_ticks; out["dist_to_vah_ticks"] = dist_to_vah_ticks
        out["dist_to_val_ticks"] = dist_to_val_ticks; out["dist_to_hvn_ticks"] = dist_to_hvn_ticks
        out["dist_to_lvn_ticks"] = dist_to_lvn_ticks; out["dist_to_poc_vol"] = dist_to_poc_vol
        out["dist_to_vah_vol"] = dist_to_vah_vol; out["dist_to_val_vol"] = dist_to_val_vol
        out["dist_to_hvn_vol"] = dist_to_hvn_vol; out["dist_to_lvn_vol"] = dist_to_lvn_vol
        out["inside_value_area"] = inside_va; out["above_vah"] = above_vah; out["below_val"] = below_val

    rng = np.maximum(h - l, 1e-9)
    out["candle_body_vol"] = np.abs(c - o_) / vol
    out["candle_range_vol"] = (h - l) / vol
    out["upper_wick_vol"] = (h - np.maximum(c, o_)) / vol
    out["lower_wick_vol"] = (np.minimum(c, o_) - l) / vol
    out["close_location"] = (c - l) / np.where(rng > 0, rng, np.nan)
    out["open_to_close_sign"] = np.sign(c - o_)
    prev_c = pd.Series(c).shift(1)
    out["close_vs_prev_close_vol"] = (c - prev_c.to_numpy()) / vol
    out["close_vs_roll_mean_vol"] = (c - pd.Series(c).rolling(20, min_periods=5).mean().to_numpy()) / vol
    roll_max20 = pd.Series(c).rolling(20, min_periods=5).max().shift(1).to_numpy()
    roll_min20 = pd.Series(c).rolling(20, min_periods=5).min().shift(1).to_numpy()
    out["high_break_vol"] = (h - roll_max20) / vol
    out["low_break_vol"] = (roll_min20 - l) / vol

    minute_of_day = pd.to_numeric(g["minute_of_day"], errors="coerce") if "minute_of_day" in g.columns else pd.Series(np.nan, index=g.index)
    sessions = minute_of_day.apply(lambda m: session_label(int(m)) if pd.notna(m) else "UNKNOWN")
    for s in ("Asia", "EU", "US_Open", "US_AM", "US_PM", "US_Late"):
        out[f"sess_{s}"] = (sessions == s).astype(int)

    delta = pd.to_numeric(g["delta_norm"], errors="coerce").to_numpy() if "delta_norm" in g.columns else np.full(n, np.nan)
    mid_z = pd.to_numeric(g["mid_resid_z"], errors="coerce").to_numpy() if "mid_resid_z" in g.columns else np.full(n, np.nan)
    prx_range = np.maximum(h - l, TICK_SIZE)
    vt = pd.to_numeric(g["vol_total"], errors="coerce").to_numpy() if "vol_total" in g.columns else np.full(n, np.nan)
    absorb_strength = vt / prx_range
    absorb_s = pd.Series(absorb_strength)
    absorb_z = ((absorb_s - absorb_s.rolling(50, min_periods=10).mean()) / absorb_s.rolling(50, min_periods=10).std()).to_numpy()
    prior_close = np.r_[np.nan, c[:-1]]
    touch_counter = defaultdict(int); last_touch_bar = {}
    lvl_onehot = {lt: np.zeros(n, dtype=int) for lt in ("POC", "VAH", "VAL", "HVN", "LVN")}
    # dashboard model's own 5-category convention (combined "acceptance") AND
    # v4's split 2-category convention (breakout_acceptance_above /
    # breakdown_acceptance_below) are BOTH derived from the SAME underlying
    # classify_bar_reaction() string - emitted side-by-side, no extra
    # computation, just two different one-hot groupings of one classification.
    rxn_onehot = {r: np.zeros(n, dtype=int) for r in
                 ("rejection_from_above", "rejection_from_below", "absorption", "acceptance", "neutral_touch",
                  "breakout_acceptance_above", "breakdown_acceptance_below")}
    reaction_type_str = np.full(n, "", dtype=object)
    touch_count_past_only = np.full(n, np.nan); bars_since_prior_touch = np.full(n, np.nan)
    if lv is not None:
        flat_levels = [(lv["poc_px"], "POC"), (lv["vah_px"], "VAH"), (lv["val_px"], "VAL")] + \
                      [(p, "HVN") for p in lv["hvn_px"]] + [(p, "LVN") for p in lv["lvn_px"]]
        for i in range(n):
            best = None
            for lp, ln in flat_levels:
                rxn = classify_bar_reaction(h[i], l[i], c[i], o_[i], lp, ln, delta[i], mid_z[i], absorb_z[i], prior_close[i])
                if rxn is None:
                    continue
                key = (lp, ln)
                tc_past = touch_counter[key]
                bars_since = (i - last_touch_bar[key]) if key in last_touch_bar else -1
                touch_counter[key] += 1; last_touch_bar[key] = i
                if best is None:
                    best = (rxn, ln, tc_past, bars_since)
            if best is not None:
                rxn, ln, tc_past, bars_since = best
                lvl_onehot[ln][i] = 1
                reaction_type_str[i] = rxn
                if "rejection_from_above" in rxn: rxn_onehot["rejection_from_above"][i] = 1
                elif "rejection_from_below" in rxn: rxn_onehot["rejection_from_below"][i] = 1
                elif "absorption" in rxn: rxn_onehot["absorption"][i] = 1
                elif rxn.startswith("breakout_acceptance_above"):
                    rxn_onehot["acceptance"][i] = 1; rxn_onehot["breakout_acceptance_above"][i] = 1
                elif rxn.startswith("breakdown_acceptance_below"):
                    rxn_onehot["acceptance"][i] = 1; rxn_onehot["breakdown_acceptance_below"][i] = 1
                elif "neutral_touch" in rxn: rxn_onehot["neutral_touch"][i] = 1
                touch_count_past_only[i] = tc_past; bars_since_prior_touch[i] = bars_since
    for lt in ("POC", "VAH", "VAL", "HVN", "LVN"):
        out[f"lvl_{lt}"] = lvl_onehot[lt]
    for r in ("rejection_from_above", "rejection_from_below", "absorption", "acceptance", "neutral_touch",
             "breakout_acceptance_above", "breakdown_acceptance_below"):
        out[f"rxn_{r}"] = rxn_onehot[r]
    out["touch_count_past_only"] = touch_count_past_only
    out["bars_since_prior_touch"] = bars_since_prior_touch
    out["reaction_type_full_string"] = reaction_type_str  # internal - mapped to gate_* by the build script
    return out
