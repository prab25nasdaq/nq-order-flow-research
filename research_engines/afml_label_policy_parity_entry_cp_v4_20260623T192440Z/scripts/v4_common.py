"""
v4_common.py - shared READ-ONLY library for the AFML Label-Policy-Parity
Entry + Close-Position Model v4 engine.

Reads exclusively from:
  - this engine's own raw_snapshot/ (frozen at build time by 00_snapshot_inputs.py)
  - the v3 engine's frozen outputs (read-only reuse of already-validated
    dashboard-parity / OFI Level Decision feature builders)
Writes only inside this engine's own outputs/.

Two families of formulas are reproduced here (not imported/executed):

  1. DASHBOARD formulas (residual_z, causal_z, cusum_breaks, kyle/roll/CS,
     entropy, etc.) - copied verbatim from v3_common.py, which itself cites
     exact source line ranges in ofi_live_dashboard_WORKING_NEXT_with_logreg.py.

  2. EXISTING LEVEL-REACTION MODEL's label-policy formulas
     (volume_profile_levels, classify_bar_reaction, session_label,
     build_event_labels) - reproduced LINE-FOR-LINE from the active model
     release's own training script:
     /home/prabh/OFI_Production/model_registry/level_reaction_continuous_nq_shadow/
       ACTIVE_SHADOW_RELEASE/scripts/pipeline_continuous.py
     (snapshotted read-only into raw_snapshot/active_release_policy/).
     This is the "label-policy parity" half of v4 - Part B rebuilds these
     exactly, never inferring parameters that already exist in source.

SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING / NO DATABENTO.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml
from scipy.stats import norm

ENGINE_DIR = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = ENGINE_DIR / "raw_snapshot"
OUT_DIR = ENGINE_DIR / "outputs"
OUT_DIR.mkdir(exist_ok=True)
REPORTS_DIR = ENGINE_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
CONFIG_PATH = ENGINE_DIR / "configs" / "v4_config.yaml"
RELEASE_SNAPSHOT_DIR = SNAPSHOT_DIR / "active_release_policy"

V1_DIR = Path("/home/prabh/OFI_Production/research_engines/afml_trade_lifecycle_shadow_v1_20260623T062435Z")
DIAG_DIR = Path("/home/prabh/OFI_Production/research_engines/afml_trade_lifecycle_v1_failure_diagnostic_20260623T065452Z")
V2_DIR = Path("/home/prabh/OFI_Production/research_engines/afml_trade_lifecycle_v2_ic_regime_20260623T180015Z")
V3_DIR = Path("/home/prabh/OFI_Production/research_engines/afml_dashboard_parity_cusum_sr_entry_cp_v3_20260623T184026Z")

DASHBOARD_SRC = Path(
    "/mnt/wd_work/workspace/Work Place/Data/Project OFI/_codex_work/"
    "ofi_live_dashboard_WORKING_NEXT_with_logreg.py"
)
OFI_LEVEL_DECISION_SRC = Path("/home/prabh/OFI_Production/ofi_level_decision_tab.py")
MODEL_PROBS_TRUST_SRC = Path("/home/prabh/OFI_Production/model_probs_trust_tab.py")
PIPELINE_CONTINUOUS_SRC = RELEASE_SNAPSHOT_DIR / "pipeline_continuous.py"

TICK_SIZE = 0.25
MODEL_TRAINING_CUTOFF_UTC = pd.Timestamp("2026-06-21T01:53:35.580300+00:00")

# existing model's own label-policy constants (verbatim from training_config.json)
NEAR_TICKS_K = 4
NEAR_TICKS_PRICE = TICK_SIZE * NEAR_TICKS_K
H_FORWARD = [5, 10, 20, 40]
PRIMARY_H = 40
EMBARGO_BARS = 40
MIN_BARS_PER_DAY_FOR_EVENTS = 50


def log(msg: str) -> None:
    import time
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ── Read-only loaders ────────────────────────────────────────────────────────

def _read_ndjsonl(path: Path) -> pd.DataFrame:
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


def load_continuous_master(dedup: bool = True) -> pd.DataFrame:
    """Full continuous master (NQM6 backadjusted history + NQU6 live) - the
    SAME source the existing model trains on. De-duplicated on bar_end_ts_ns
    keep-first, identical to pipeline_continuous.py::load_continuous_master."""
    df = _read_ndjsonl(SNAPSHOT_DIR / "master_NQ_continuous_backadjusted_shadow.ndjsonl")
    df["bar_end_ts_ns"] = pd.to_numeric(df["bar_end_ts_ns"], errors="coerce")
    if dedup:
        df = df.drop_duplicates(subset=["bar_end_ts_ns"], keep="first")
    df = df.sort_values("bar_end_ts_ns", kind="stable").reset_index(drop=True)
    day_str = df["day"].astype(int).astype(str).str.zfill(8)
    df["rithmic_date_str"] = day_str.str[:4] + "-" + day_str.str[4:6] + "-" + day_str.str[6:8]
    return df


def load_nqu6_master(dedup: bool = True) -> pd.DataFrame:
    df = _read_ndjsonl(SNAPSHOT_DIR / "master_NQU6_shadow.ndjsonl")
    df = df.sort_values("bar_end_ts_ns", kind="stable").reset_index(drop=True)
    if dedup:
        df = df.drop_duplicates(subset=["bar_end_ts_ns"], keep="first").reset_index(drop=True)
    return df


def load_predictions() -> pd.DataFrame:
    """Loaded for SCHEMA/CONTEXT reference only - never used as label/ground
    truth (per build spec). See Part B parity report for the only legitimate
    use: comparing rebuilt events against the existing TRAINING data
    (level_reaction_events.parquet), not predictions.csv."""
    df = pd.read_csv(SNAPSHOT_DIR / "latest_continuous_nq_predictions.csv")
    df["_orig_idx"] = np.arange(len(df))
    return df


def load_existing_training_events() -> pd.DataFrame:
    """The existing model's ACTUAL frozen training events (event/reaction/
    level metadata) - data/level_reaction_events.parquet, NOT predictions.csv.
    This is the legitimate "existing training labels" comparison target for
    Part B parity."""
    return pd.read_parquet(RELEASE_SNAPSHOT_DIR / "level_reaction_events.parquet")


def load_existing_training_labels() -> pd.DataFrame:
    """The existing model's ACTUAL frozen labels (label_h40, fwd returns,
    MFE/MAE) - data/labels_level_reaction.parquet."""
    return pd.read_parquet(RELEASE_SNAPSHOT_DIR / "labels_level_reaction.parquet")


def load_existing_training_features() -> pd.DataFrame:
    """The existing model's ACTUAL frozen feature matrix (89 cols = 77
    features + 12 ID cols) - data/X_level_reaction_events.parquet."""
    return pd.read_parquet(RELEASE_SNAPSHOT_DIR / "X_level_reaction_events.parquet")


def load_reaction_type_metadata() -> dict:
    return json.loads((RELEASE_SNAPSHOT_DIR / "reaction_type_metadata.json").read_text())


def load_training_config() -> dict:
    return json.loads((RELEASE_SNAPSHOT_DIR / "training_config.json").read_text())


def load_projected_levels() -> pd.DataFrame:
    return pd.read_csv(SNAPSHOT_DIR / "projected_levels_NQM6_to_NQU6.csv")


def list_level_candle_dates(depth: int) -> List[str]:
    import glob
    pattern = str(SNAPSHOT_DIR / "cache" / f"book_flow_level_candles_NQU6_*_top{depth}.parquet")
    return sorted({Path(f).name.split("_")[5] for f in glob.glob(pattern)})


def load_level_candles(depth: int) -> pd.DataFrame:
    frames = []
    for d in list_level_candle_dates(depth):
        path = SNAPSHOT_DIR / "cache" / f"book_flow_level_candles_NQU6_{d}_top{depth}.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        df["session_date"] = d
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def aggregate_book_flow_bars(level_df: pd.DataFrame) -> pd.DataFrame:
    """Identical to v3_common.aggregate_book_flow_bars - bar-level true-book-
    flow OFI add/pull features + native nearest-level extraction, CLOSED
    bars only."""
    df = level_df[level_df["bar_state"] == "CLOSED"]
    if df.empty:
        return pd.DataFrame()
    agg = (
        df.groupby(["session_date", "bar_idx"])
        .agg(
            bid_add=("bid_add", "sum"), bid_pull=("bid_pull", "sum"),
            ask_add=("ask_add", "sum"), ask_pull=("ask_pull", "sum"),
            signed_flow=("signed_flow", "sum"), abs_flow=("abs_flow", "sum"),
            net_bid_flow=("net_bid_flow", "sum"), net_ask_flow=("net_ask_flow", "sum"),
            close_price=("close_price", "last"), bar_end_ts_ns=("bar_end_ts_ns", "last"),
            timestamp_utc=("timestamp_utc", "last"),
        )
        .reset_index()
        .sort_values(["session_date", "bar_idx"])
        .reset_index(drop=True)
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
            if len(uniq) > 1:
                nearest_price = uniq[np.argmin(np.abs(uniq - close))]
                rec[f"native_{ltype}"] = float(nearest_price)
            else:
                rec[f"native_{ltype}"] = float(uniq[0])
        native_rows.append(rec)
    native_df = pd.DataFrame(native_rows)
    agg = agg.merge(native_df, on=["session_date", "bar_idx"], how="left")
    return agg


# ═════════════════════════════════════════════════════════════════════════
# DASHBOARD FORMULAS - copied verbatim from v3_common.py (already validated
# there with 15 parity checks). See v3_common.py / dashboard_formula_catalog
# for exact source line citations.
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
    breach_magnitude = np.full(len(z), np.nan)
    for i in range(1, len(z)):
        sp[i] = max(0.0, sp[i - 1] + z[i] - k)
        sm[i] = min(0.0, sm[i - 1] + z[i] + k)
        if sp[i] > h:
            up[i] = True; breach_magnitude[i] = sp[i]; sp[i] = 0.0
        if sm[i] < -h:
            dn[i] = True; breach_magnitude[i] = sm[i]; sm[i] = 0.0
    return {"z": z, "s_plus": sp, "s_minus": sm, "up": up, "down": dn, "h": h, "k": k,
            "halflife": halflife, "breach_magnitude": breach_magnitude, "ewm_vol": sd.to_numpy()}


def rolling_kyle(mid: np.ndarray, q: np.ndarray, h: int = 1, window: int = 160) -> np.ndarray:
    n = len(mid)
    dp = np.full(n, np.nan); q_lag = np.full(n, np.nan)
    if n > 1:
        dp[1:] = mid[1:] - mid[:-1]
        q_lag[1:] = q[:-1]
    s_dp, s_q = pd.Series(dp), pd.Series(q_lag)
    cov = s_dp.rolling(window, min_periods=max(20, window // 4)).cov(s_q)
    var = s_q.rolling(window, min_periods=max(20, window // 4)).var()
    return (cov / var.replace(0, np.nan)).to_numpy()


def roll_measure(dp: np.ndarray, window: int = 160) -> np.ndarray:
    s = pd.Series(dp)
    cov = s.rolling(window, min_periods=max(20, window // 4)).cov(s.shift(1))
    return 2.0 * np.sqrt(np.maximum(0.0, -cov.to_numpy()))


def corwin_schulz(hi: np.ndarray, lo: np.ndarray) -> np.ndarray:
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


def rolling_pct(values: np.ndarray, window: int = 250) -> np.ndarray:
    vals = np.asarray(values, dtype=float)
    out = np.full(len(vals), np.nan)
    for i in range(len(vals)):
        ref = vals[max(0, i - window + 1): i + 1]
        ref = ref[np.isfinite(ref)]
        if len(ref) >= 3 and np.isfinite(vals[i]):
            out[i] = np.mean(ref <= vals[i])
    return out


def quantile_codes(values: np.ndarray, k: int = 5, window: int = 256, min_ref: int = 40) -> np.ndarray:
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


def norm_entropy(codes: np.ndarray, k: int) -> float:
    c = np.asarray(codes)
    c = c[(c >= 0) & np.isfinite(c)]
    if len(c) < 3 or k <= 1:
        return np.nan
    counts = np.bincount(c.astype(int), minlength=k).astype(float)
    p = counts[counts > 0] / counts.sum()
    h = -float(np.sum(p * np.log(p)))
    return float(np.clip(h / np.log(k), 0.0, 1.0))


def rolling_entropy(codes: np.ndarray, k: int, window: int = 128) -> np.ndarray:
    codes = np.asarray(codes, dtype=float)
    out = np.full(len(codes), np.nan)
    min_n = max(8, min(window // 4, 32))
    for i in range(len(codes)):
        start = max(0, i - window + 1)
        sl = codes[start:i + 1]
        if np.sum((sl >= 0) & np.isfinite(sl)) >= min_n:
            out[i] = norm_entropy(sl, k)
    return out


def ema_clean(values: np.ndarray, span: int = 5, fill: float = 0.0) -> np.ndarray:
    return pd.Series(values).replace([np.inf, -np.inf], np.nan).fillna(fill).ewm(
        span=span, adjust=False).mean().to_numpy()


# ═════════════════════════════════════════════════════════════════════════
# EXISTING LEVEL-REACTION MODEL's label policy - reproduced LINE-FOR-LINE
# from pipeline_continuous.py (raw_snapshot/active_release_policy/). This is
# the authoritative source for Part B's parity rebuild.
# ═════════════════════════════════════════════════════════════════════════

def volume_profile_levels(df_day: pd.DataFrame) -> Dict[str, Any]:
    """EXACT replica of pipeline_continuous.py::volume_profile_levels
    (== ofi_live_dashboard.py:2740-2767), fed continuous_high/continuous_low.
    TICK=0.25, VA target=70% from POC, HVN: tot_v > smooth*1.45, LVN:
    tot_v < smooth*0.55, smoothing kernel width k=max(3, n_levels//15)."""
    h = pd.to_numeric(df_day["continuous_high"], errors="coerce").to_numpy()
    l = pd.to_numeric(df_day["continuous_low"], errors="coerce").to_numpy()
    vt = pd.to_numeric(df_day["vol_total"], errors="coerce").to_numpy()
    bv = pd.to_numeric(df_day["buy_vol"], errors="coerce").to_numpy()
    sv = pd.to_numeric(df_day["sell_vol"], errors="coerce").to_numpy()

    valid = np.isfinite(h) & np.isfinite(l)
    pmin = float(np.nanmin(l[valid])); pmax = float(np.nanmax(h[valid]))
    lo_t = int(round(pmin / TICK_SIZE)) - 2
    hi_t = int(round(pmax / TICK_SIZE)) + 2
    n_lev = hi_t - lo_t + 1
    levels = np.array([round(j * TICK_SIZE, 2) for j in range(lo_t, hi_t + 1)])
    lev_lo = levels - TICK_SIZE * 0.5; lev_hi = levels + TICK_SIZE * 0.5
    tot_v = np.zeros(n_lev); buy_v = np.zeros(n_lev); sel_v = np.zeros(n_lev)
    for i in range(len(df_day)):
        if not (np.isfinite(h[i]) and np.isfinite(l[i])):
            continue
        br = max(h[i] - l[i], TICK_SIZE)
        ov = np.minimum(h[i], lev_hi) - np.maximum(l[i], lev_lo)
        m = ov > 0
        if not m.any():
            continue
        w = np.where(m, ov / br, 0.0)
        tot_v += vt[i] * w
        if np.isfinite(bv[i]) and np.isfinite(sv[i]):
            buy_v += bv[i] * w; sel_v += sv[i] * w
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
    kernel = np.ones(k) / k
    smooth = np.convolve(tot_v, kernel, mode="same")
    is_hvn = (tot_v > 0) & (tot_v > smooth * 1.45)
    is_lvn = (tot_v > 0) & (tot_v < smooth * 0.55)
    return {
        "poc_px": poc_px, "vah_px": vah_px, "val_px": val_px,
        "hvn_px": levels[is_hvn].tolist(), "lvn_px": levels[is_lvn].tolist(),
        "levels": levels, "tot_v": tot_v,
        "value_area_pct": round(100.0 * va_vol / tot_sum, 2),
    }


def session_label(minute_of_day: int) -> str:
    """EXACT replica of pipeline_continuous.py::session_label - this is the
    EXISTING LEVEL-REACTION MODEL's OWN session convention, distinct from
    the OFI Level Decision tab's own (London/US/Asia_Overnight) convention
    used in v3's assign_session_old_tab."""
    if minute_of_day < 360: return "Asia"
    if minute_of_day < 720: return "EU"
    if minute_of_day < 870: return "US_Open"
    if minute_of_day < 1080: return "US_AM"
    if minute_of_day < 1320: return "US_PM"
    return "US_Late"


def classify_bar_reaction(
        h: float, l: float, c: float, o: float,
        level_px: float, level_name: str,
        delta: float, mid_z: float, abs_z: float,
        prior_close: float, touch_count_past: int,
) -> Optional[str]:
    """EXACT replica of pipeline_continuous.py::classify_bar_reaction.
    Causal: current bar OHLC (continuous scale) + prior close + past touch
    count only. No future, no centered, no shift(-1)."""
    if not (np.isfinite(h) and np.isfinite(l) and np.isfinite(c)):
        return None
    touched = (l - TICK_SIZE * 0.5 <= level_px <= h + TICK_SIZE * 0.5)
    close_to = abs(c - level_px) <= NEAR_TICKS_PRICE
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
    if np.isfinite(delta) and abs(delta) > 1.0 and abs(c - level_px) > NEAR_TICKS_PRICE:
        if c > level_px and came_from_below:
            return f"breakout_acceptance_above_{level_name}"
        if c < level_px and came_from_above:
            return f"breakdown_acceptance_below_{level_name}"
    if touched and abs(c - o) < TICK_SIZE and not (np.isfinite(abs_z) and abs_z > 1.5):
        return f"{level_name}_neutral_touch"
    return None


# ═════════════════════════════════════════════════════════════════════════
# AFML methodology - re-used VERBATIM from v1/diagnostic/v2/v3.
# ═════════════════════════════════════════════════════════════════════════

def apply_triple_barrier(
    t0_idx: np.ndarray, side: np.ndarray, close: np.ndarray, high: np.ndarray,
    low: np.ndarray, vol: np.ndarray, day: np.ndarray, pt_multiple: float,
    sl_multiple: float, vertical_barrier_bars: int, tie_break: str = "SL_FIRST",
) -> Dict[str, np.ndarray]:
    n = len(t0_idx)
    t1_idx = np.full(n, -1, dtype=np.int64)
    label_primary = np.full(n, np.nan)
    first_touch = np.array([""] * n, dtype=object)
    realized_ret = np.full(n, np.nan)
    realized_points = np.full(n, np.nan)
    mfe_points = np.full(n, np.nan)
    mae_points = np.full(n, np.nan)
    holding_bars = np.full(n, np.nan)
    pt_price_arr = np.full(n, np.nan)
    sl_price_arr = np.full(n, np.nan)
    n_bars_total = len(close)

    for i in range(n):
        t0 = t0_idx[i]
        s = side[i]
        v = vol[i]
        if s == 0 or not np.isfinite(v) or v <= 0:
            continue
        c0 = close[t0]
        d0 = day[t0]
        pt_price = c0 * np.exp(s * pt_multiple * v)
        sl_price = c0 * np.exp(-s * sl_multiple * v)
        pt_price_arr[i] = pt_price
        sl_price_arr[i] = sl_price

        vb_idx = min(t0 + vertical_barrier_bars, n_bars_total - 1)
        while vb_idx > t0 and day[vb_idx] != d0:
            vb_idx -= 1
        if vb_idx <= t0:
            continue

        touch_idx = None
        touch_kind = None
        best_fav = 0.0
        worst_adv = 0.0
        for j in range(t0 + 1, vb_idx + 1):
            if day[j] != d0:
                break
            hi, lo = high[j], low[j]
            if s > 0:
                fav_excursion = hi - c0
                adv_excursion = c0 - lo
                hit_pt = hi >= pt_price
                hit_sl = lo <= sl_price
            else:
                fav_excursion = c0 - lo
                adv_excursion = hi - c0
                hit_pt = lo <= pt_price
                hit_sl = hi >= sl_price
            best_fav = max(best_fav, fav_excursion)
            worst_adv = max(worst_adv, adv_excursion)
            if hit_pt and hit_sl:
                touch_idx, touch_kind = j, ("SL" if tie_break == "SL_FIRST" else "PT")
                break
            elif hit_pt:
                touch_idx, touch_kind = j, "PT"
                break
            elif hit_sl:
                touch_idx, touch_kind = j, "SL"
                break

        if touch_idx is None:
            touch_idx, touch_kind = vb_idx, "VB"

        exit_price = close[touch_idx]
        ret_signed = s * np.log(exit_price / c0)
        pts_signed = s * (exit_price - c0)
        if touch_kind == "PT":
            lbl = 1.0
        elif touch_kind == "SL":
            lbl = -1.0
        else:
            lbl = float(np.sign(ret_signed))

        t1_idx[i] = touch_idx
        label_primary[i] = lbl
        first_touch[i] = touch_kind
        realized_ret[i] = ret_signed
        realized_points[i] = pts_signed
        mfe_points[i] = best_fav
        mae_points[i] = worst_adv
        holding_bars[i] = touch_idx - t0

    return dict(
        t1_idx=t1_idx, label_primary=label_primary, first_touch=first_touch,
        realized_ret=realized_ret, realized_points=realized_points,
        mfe_points=mfe_points, mae_points=mae_points, holding_bars=holding_bars,
        pt_price=pt_price_arr, sl_price=sl_price_arr,
    )


def ewm_log_return_vol(close: pd.Series, span_bars: int, min_periods: int) -> pd.Series:
    log_ret = np.log(close).diff()
    return log_ret.ewm(span=span_bars, min_periods=min_periods).std()


def num_co_events(t0_idx: np.ndarray, t1_idx: np.ndarray, n_bars: int) -> np.ndarray:
    valid = (t0_idx >= 0) & (t1_idx >= 0)
    c = np.zeros(n_bars, dtype=np.int64)
    for t0, t1 in zip(t0_idx[valid], t1_idx[valid]):
        lo, hi = max(0, int(t0)), min(n_bars - 1, int(t1))
        if lo <= hi:
            c[lo:hi + 1] += 1
    return c


def average_uniqueness(t0_idx: np.ndarray, t1_idx: np.ndarray, c_t: np.ndarray) -> np.ndarray:
    out = np.full(len(t0_idx), np.nan)
    for i, (t0, t1) in enumerate(zip(t0_idx, t1_idx)):
        if t0 < 0 or t1 < 0:
            continue
        lo, hi = max(0, int(t0)), min(len(c_t) - 1, int(t1))
        if lo > hi:
            continue
        out[i] = float(np.mean(1.0 / c_t[lo:hi + 1]))
    return out


def cluster_by_overlap(day: np.ndarray, t0_idx: np.ndarray, t1_idx: np.ndarray) -> np.ndarray:
    n = len(t0_idx)
    cluster_id = np.full(n, -1, dtype=np.int64)
    valid = np.where((t0_idx >= 0) & (t1_idx >= 0))[0]
    if len(valid) == 0:
        return cluster_id
    df = pd.DataFrame({"idx": valid, "day": day[valid], "t0": t0_idx[valid], "t1": t1_idx[valid]})
    df = df.sort_values(["day", "t0", "t1"], kind="stable")
    next_cluster = 0
    current_day = None
    running_hi = -1
    current_cluster = -1
    for row in df.itertuples(index=False):
        if row.day != current_day or row.t0 > running_hi:
            current_cluster = next_cluster
            next_cluster += 1
            running_hi = row.t1
            current_day = row.day
        else:
            running_hi = max(running_hi, row.t1)
            current_day = row.day
        cluster_id[row.idx] = current_cluster
    return cluster_id


def purged_embargo_day_splits(events: pd.DataFrame, day_col: str, t0_col: str, t1_col: str,
                               embargo_bars: int) -> List[Dict]:
    days = sorted(events[day_col].unique())
    folds = []
    embargoed_idx = set()
    for i, test_day in enumerate(days):
        test_mask = events[day_col] == test_day
        test_idx = events.index[test_mask].to_numpy()
        if test_idx.size == 0:
            continue
        test_lo = events.loc[test_idx, t0_col].min()
        test_hi = events.loc[test_idx, t1_col].max()
        prior_days = days[:i]
        if not prior_days:
            continue
        train_mask = events[day_col].isin(prior_days)
        train_idx_all = events.index[train_mask].to_numpy()
        overlap_mask = ~(
            (events.loc[train_idx_all, t1_col] < test_lo) |
            (events.loc[train_idx_all, t0_col] > test_hi)
        )
        purged = set(train_idx_all[overlap_mask.to_numpy()])
        embargoed_now = set(train_idx_all) & embargoed_idx
        train_idx = np.array(sorted(set(train_idx_all) - purged - embargoed_now))
        folds.append(dict(
            test_day=test_day, train_idx=train_idx, test_idx=test_idx,
            n_purged=len(purged), n_embargoed=len(embargoed_now),
            n_train=len(train_idx), n_test=len(test_idx),
        ))
        embargo_hi = test_hi + embargo_bars
        newly_embargoed = events.index[
            (events[t0_col] > test_hi) & (events[t0_col] <= embargo_hi)
        ].to_numpy()
        embargoed_idx.update(newly_embargoed)
    return folds


def meta_prob_to_size(p_meta: np.ndarray, side: np.ndarray, num_classes: int = 2) -> np.ndarray:
    p = np.clip(p_meta, 1e-6, 1 - 1e-6)
    z = (p - 1.0 / num_classes) / np.sqrt(p * (1 - p))
    magnitude = np.clip(2 * norm.cdf(z) - 1, 0, 1)
    return side * magnitude


def discretize_signal(signal: np.ndarray, step_size: float) -> np.ndarray:
    out = np.round(signal / step_size) * step_size
    return np.clip(out, -1.0, 1.0)


# ── Metrics (identical to v3_common.py) ─────────────────────────────────

def mcc_binary(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    y_true, y_pred = y_true[mask], y_pred[mask]
    if len(y_true) < 5:
        return np.nan
    tp = np.sum((y_pred == 1) & (y_true == 1)); tn = np.sum((y_pred == 0) & (y_true == 0))
    fp = np.sum((y_pred == 1) & (y_true == 0)); fn = np.sum((y_pred == 0) & (y_true == 1))
    denom = np.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    return float((tp * tn - fp * fn) / denom) if denom > 0 else 0.0


def precision_recall_f1(y_true: np.ndarray, y_pred: np.ndarray) -> Tuple[float, float, float]:
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    y_true, y_pred = y_true[mask], y_pred[mask]
    tp = np.sum((y_pred == 1) & (y_true == 1)); fp = np.sum((y_pred == 1) & (y_true == 0))
    fn = np.sum((y_pred == 0) & (y_true == 1))
    precision = tp / (tp + fp) if (tp + fp) > 0 else np.nan
    recall = tp / (tp + fn) if (tp + fn) > 0 else np.nan
    f1 = 2 * precision * recall / (precision + recall) if precision and recall and (precision + recall) > 0 else np.nan
    return float(precision), float(recall), float(f1)


def balanced_accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    y_true, y_pred = y_true[mask], y_pred[mask]
    if len(y_true) < 5:
        return np.nan
    tp = np.sum((y_pred == 1) & (y_true == 1)); fn = np.sum((y_pred == 0) & (y_true == 1))
    tn = np.sum((y_pred == 0) & (y_true == 0)); fp = np.sum((y_pred == 1) & (y_true == 0))
    sens = tp / (tp + fn) if (tp + fn) > 0 else np.nan
    spec = tn / (tn + fp) if (tn + fp) > 0 else np.nan
    return float(np.nanmean([sens, spec]))


def safe_auc(y_true: np.ndarray, score: np.ndarray) -> float:
    try:
        from sklearn.metrics import roc_auc_score
        mask = np.isfinite(y_true) & np.isfinite(score)
        y_true, score = np.asarray(y_true)[mask], np.asarray(score)[mask]
        if len(np.unique(y_true)) < 2 or len(y_true) < 10:
            return np.nan
        return float(roc_auc_score(y_true, score))
    except Exception:
        return np.nan


def brier_score(y_true: np.ndarray, p: np.ndarray) -> float:
    mask = np.isfinite(y_true) & np.isfinite(p)
    y_true, p = np.asarray(y_true)[mask], np.asarray(p)[mask]
    if len(y_true) < 5:
        return np.nan
    return float(np.mean((p - y_true) ** 2))


def log_loss_safe(y_true: np.ndarray, p: np.ndarray) -> float:
    mask = np.isfinite(y_true) & np.isfinite(p)
    y_true, p = np.asarray(y_true)[mask], np.asarray(p)[mask]
    if len(y_true) < 5:
        return np.nan
    pc = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y_true * np.log(pc) + (1 - y_true) * np.log(1 - pc)))


def wilson_ci(n_success: float, n_total: float, z: float = 1.96) -> Tuple[float, float]:
    if n_total <= 0:
        return (np.nan, np.nan)
    p = n_success / n_total
    denom = 1 + z**2 / n_total
    centre = p + z**2 / (2 * n_total)
    half = z * np.sqrt(max(p * (1 - p) / n_total, 0) + z**2 / (4 * n_total**2))
    lo = (centre - half) / denom
    hi = (centre + half) / denom
    return (float(lo), float(hi))
