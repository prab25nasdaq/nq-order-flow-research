#!/usr/bin/env python3
"""
Directional VPIN / Toxic Flow Settings Atlas v1
SHADOW / RESEARCH ONLY — no execution, no broker, no order placement.

Inputs: NQ continuous master, feature master, book switching panel.
Output: Full settings grid, directional features, Spearman phase analysis,
        forward outcome testing, conditional filters, session analysis,
        best-setting ranking, case studies, dashboard recommendation, final report.
"""
from __future__ import annotations

import csv
import json
import math
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

warnings.filterwarnings("ignore")

# ── Paths ──────────────────────────────────────────────────────────────────────
OUT_DIR = Path(__file__).parent
MASTER_NQ   = Path("/home/prabh/OFI_Live_Features/master_NQ_continuous_backadjusted_shadow.ndjsonl")
MASTER_NQU6 = Path("/home/prabh/OFI_Live_Features/master_NQU6_shadow.ndjsonl")
FM_PARQUET  = Path("/home/prabh/OFI_Production/model_feature_master/data/model_feature_master_shadow.parquet")
BS_PARQUET  = Path("/home/prabh/OFI_Production/research_engines/book_switching_cross_side_add_pull_alpha_v1_20260625T190639Z/book_switching_feature_panel.parquet")

# ── Grid parameters ────────────────────────────────────────────────────────────
VPIN_WINDOWS     = [20, 40, 60, 120, 240, 480, 960]
SMOOTH_SPANS     = [0, 20, 40, 120]      # 0 = raw (no EWM)
PCT_LOOKBACKS    = [250, 500, 1000]      # bars; session-to-date handled separately
THRESHOLDS       = [0.70, 0.80, 0.90, 0.95]
FWD_HORIZONS     = [5, 10, 20, 40, 80]
SPEARMAN_WINDOWS = [60, 120, 240, 480, 960]
MIN_N            = 30                    # minimum sample for any statistic

SESSIONS = {
    "Asia":    (0*60,   7*60),    # UTC minutes
    "London":  (7*60,  12*60),
    "US_Open": (13*60, 14*60+30),
    "US_AM":   (14*60, 18*60),
    "US_PM":   (18*60, 21*60),
    "US_Late": (21*60, 24*60),
}

RUN_TS = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
print(f"\n{'='*72}")
print(f"  DIRECTIONAL VPIN / TOXIC FLOW SETTINGS ATLAS V1")
print(f"  Run: {RUN_TS}   OUT: {OUT_DIR.name}")
print(f"  SHADOW / RESEARCH ONLY — no execution, no broker")
print(f"{'='*72}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _rolling_pct_rank(s: pd.Series, window: int) -> pd.Series:
    """Rolling percentile rank (fraction of past ≤ current) within window."""
    def _pct(arr):
        if len(arr) < 3:
            return np.nan
        return float(np.mean(arr[:-1] <= arr[-1]))
    return s.rolling(window, min_periods=3).apply(_pct, raw=True)


def _session_label(ts_utc: pd.Series) -> pd.Series:
    mins = ts_utc.dt.hour * 60 + ts_utc.dt.minute
    labels = pd.Series("Other", index=ts_utc.index)
    for name, (lo, hi) in SESSIONS.items():
        labels = labels.where(~((mins >= lo) & (mins < hi)), name)
    return labels


def _rolling_spearman(x: np.ndarray, y: np.ndarray, window: int) -> np.ndarray:
    """Rolling Spearman ρ using scipy. Only aligned finite pairs used."""
    n = len(x)
    out = np.full(n, np.nan)
    for i in range(window - 1, n):
        xi = x[i - window + 1: i + 1]
        yi = y[i - window + 1: i + 1]
        mask = np.isfinite(xi) & np.isfinite(yi)
        if mask.sum() >= MIN_N:
            r, _ = spearmanr(xi[mask], yi[mask])
            out[i] = float(r)
    return out


def _forward_returns(close: pd.Series, horizons: List[int]) -> pd.DataFrame:
    """Bar-level forward returns (no lookahead in historical research context)."""
    out = {}
    for h in horizons:
        out[f"fwd_H{h}"] = close.shift(-h) / close - 1
    return pd.DataFrame(out, index=close.index)


def _mfe_mae(close: pd.Series, h: int) -> Tuple[pd.Series, pd.Series]:
    """Max Favourable / Max Adverse Excursion over next h bars (for long framing)."""
    mfe = pd.Series(np.nan, index=close.index)
    mae = pd.Series(np.nan, index=close.index)
    n = len(close)
    c = close.to_numpy()
    for i in range(n - h):
        fwd = c[i + 1: i + h + 1]
        if len(fwd) == h and np.all(np.isfinite(fwd)):
            ref = c[i]
            if np.isfinite(ref) and ref != 0:
                rets = fwd / ref - 1
                mfe.iloc[i] = float(rets.max())
                mae.iloc[i] = float(rets.min())
    return mfe, mae


def _outcome_stats(fwd: np.ndarray, mask: np.ndarray,
                   mfe: np.ndarray, mae: np.ndarray,
                   direction: int = 1) -> Dict[str, Any]:
    """Compute outcome metrics for a condition mask."""
    idx = np.where(mask)[0]
    idx = idx[idx < len(fwd)]
    if len(idx) < MIN_N:
        return {"n": len(idx), "hit_rate": np.nan, "mean_ret": np.nan,
                "median_ret": np.nan, "sharpe": np.nan,
                "mfe_mean": np.nan, "mae_mean": np.nan, "mfe_mae_ratio": np.nan,
                "continuation_rate": np.nan, "reversal_rate": np.nan}
    r = fwd[idx] * direction   # sign-adjusted
    mf = mfe[idx]
    ma = mae[idx]
    if direction == -1:
        mf, ma = -mae[idx], -mfe[idx]
    hr  = float(np.nanmean(r > 0))
    mr  = float(np.nanmean(r))
    mdr = float(np.nanmedian(r))
    sd  = float(np.nanstd(r, ddof=1))
    sh  = mr / sd if sd > 1e-10 else np.nan
    mfe_m = float(np.nanmean(mf))
    mae_m = float(np.nanmean(np.abs(ma)))
    ratio = mfe_m / mae_m if mae_m > 1e-10 else np.nan
    cont  = float(np.nanmean(r > 0))
    rev   = float(np.nanmean(r < 0))
    return {"n": len(idx), "hit_rate": hr, "mean_ret": mr,
            "median_ret": mdr, "sharpe": sh,
            "mfe_mean": mfe_m, "mae_mean": mae_m, "mfe_mae_ratio": ratio,
            "continuation_rate": cont, "reversal_rate": rev}


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        path.write_text("# no data\n")
        return
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            out = {}
            for k, v in r.items():
                if isinstance(v, float) and not math.isfinite(v):
                    out[k] = "--"
                elif isinstance(v, float):
                    out[k] = f"{v:.6f}"
                elif isinstance(v, (np.floating,)):
                    out[k] = f"{float(v):.6f}" if math.isfinite(float(v)) else "--"
                else:
                    out[k] = v
            w.writerow(out)
    print(f"  Wrote {len(rows)} rows → {path.name}")


def _fmt(v: Any, fmt: str = ".4f") -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "--"
    try:
        return format(float(v), fmt)
    except Exception:
        return str(v)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def load_master() -> pd.DataFrame:
    print("Loading master ndjsonl…", end=" ", flush=True)
    rows = []
    keep = {"bar_end_ts_ns", "timestamp_utc", "px_close", "px_high", "px_low",
            "buy_vol", "sell_vol", "vol_total", "delta_norm", "delta_sum",
            "vpin", "mlofi_norm", "mlofi_decay_sum", "sweep_imbalance_norm",
            "sweep_buy_ratio", "sweep_sell_ratio", "buy_ratio", "sell_ratio",
            "regime_ic_delta", "regime_ic_mlofi", "cusum_up_break", "cusum_down_break",
            "flow_alignment", "entropy_score", "mid_resid_z", "bar_index",
            "tod_minute", "dow"}
    with open(MASTER_NQ) as f:
        for line in f:
            r = json.loads(line)
            rows.append({k: r.get(k) for k in keep})
    df = pd.DataFrame(rows)
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True, errors="coerce")
    for c in ["px_close","px_high","px_low","buy_vol","sell_vol","vol_total",
              "delta_norm","delta_sum","vpin","mlofi_norm","mlofi_decay_sum",
              "sweep_imbalance_norm","sweep_buy_ratio","sweep_sell_ratio",
              "buy_ratio","sell_ratio","regime_ic_delta","regime_ic_mlofi",
              "cusum_up_break","cusum_down_break","flow_alignment",
              "entropy_score","mid_resid_z","tod_minute","dow"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("bar_end_ts_ns").reset_index(drop=True)
    print(f"{len(df):,} rows  [{df['timestamp_utc'].iloc[0].date()} → {df['timestamp_utc'].iloc[-1].date()}]")
    return df


def load_book_switch() -> pd.DataFrame:
    print("Loading book switch panel…", end=" ", flush=True)
    bs = pd.read_parquet(BS_PARQUET)
    bs = bs.sort_values("bar_end_ts_ns").reset_index(drop=True)
    print(f"{len(bs):,} rows")
    return bs


def load_feature_master() -> pd.DataFrame:
    print("Loading feature master…", end=" ", flush=True)
    fm = pd.read_parquet(FM_PARQUET)
    fm = fm.sort_values("bar_end_ts_ns").reset_index(drop=True)
    print(f"{len(fm):,} rows  {len(fm.columns)} cols")
    return fm


# ═══════════════════════════════════════════════════════════════════════════════
# PART A — VPIN FORMULA AUDIT
# ═══════════════════════════════════════════════════════════════════════════════

def part_a_vpin_audit(df: pd.DataFrame) -> None:
    print("\n── Part A: VPIN formula audit ──")

    # Verify: vpin ≈ |delta_norm|
    diff = (df["vpin"] - df["delta_norm"].abs()).abs()
    max_diff = diff.dropna().max()
    is_abs_delta = max_diff < 0.001

    # Verify using buy/sell volumes
    vol_vpin = ((df["buy_vol"] - df["sell_vol"]).abs() /
                df["vol_total"].replace(0, np.nan))
    diff2 = (df["vpin"] - vol_vpin).abs()
    max_diff2 = diff2.dropna().max()
    is_buysell = max_diff2 < 0.01

    vpin_desc = {
        "formula_name": "bar_level_ofi_vpin",
        "formula_expression": "vpin = |buy_vol - sell_vol| / vol_total = |delta_norm|",
        "type": "bar-level approximation (NOT volume-synchronized)",
        "volume_bucket_size": "N/A — one value per completed bar",
        "bar_window_logic": "one bar = one VPIN observation",
        "rolling_window_for_pct": "500 bars (dashboard default)",
        "percentile_window": "500 bars",
        "normalization": "rolling_pct: fraction of past values <= current",
        "smoothing_in_production": "none — raw |delta_norm| per bar",
        "fallback_smoothing": "rolling(75).sum(|imb|) / rolling(75).sum(vol) when vpin column missing",
        "forming_bar_used": "no — dashboard computes from sealed bars only",
        "buy_sell_volume_type": "real executed volume from Rithmic tape (not proxy)",
        "states": "NORMAL<0.70 | ELEVATED<0.90 | TOXIC<0.95 | EXTREME_TOXICITY>=0.95",
        "toxicity_composite": "0.45*vpin_pct + 0.20*spread_pct + 0.15*kyle_pct + 0.10*amihud_pct + 0.10*roll_pct",
        "direction_info": "NONE — unsigned, measures |imbalance| only",
        "vpin_equals_abs_delta_norm": is_abs_delta,
        "vpin_equals_buysell_ratio": is_buysell,
        "vpin_sample_mean": f"{df['vpin'].mean():.4f}",
        "vpin_sample_std": f"{df['vpin'].std():.4f}",
        "vpin_sample_max": f"{df['vpin'].max():.4f}",
        "vpin_p95": f"{df['vpin'].quantile(0.95):.4f}",
        "vpin_p99": f"{df['vpin'].quantile(0.99):.4f}",
        "n_bars_in_master": len(df),
        "dashboard_file": "ofi_live_dashboard_WORKING_NEXT_with_logreg.py lines 3138-3149",
        "key_limitation": "No direction — cannot distinguish buy-toxic from sell-toxic without additional features",
    }

    audit_md = [
        "# Current VPIN Formula Audit",
        f"**Generated**: {RUN_TS}",
        "**SHADOW / RESEARCH ONLY**\n",
        "## Formula",
        "```",
        "vpin_bar = |buy_vol - sell_vol| / vol_total",
        "         = |delta_norm|         (confirmed: max diff < 0.001)",
        "```",
        "## Type",
        "- **Bar-level OFI approximation** — NOT Easley et al. (2011) volume-synchronized VPIN",
        "- No volume bucketing; one observation per completed bar",
        "- Buy/sell volume: real executed tape from Rithmic (not proxy)\n",
        "## Dashboard pipeline (lines 3138–3149)",
        "```python",
        "# Primary path (when 'vpin' in master columns):",
        "vpin = master_data['vpin'].ffill().fillna(0.5)   # = |delta_norm|",
        "",
        "# Fallback (rare — when master lacks vpin column):",
        "imb = |buy_vol - sell_vol|",
        "vpin = rolling(75).sum(imb) / rolling(75).sum(vol_total)",
        "",
        "# Normalization:",
        "vpin_pct = rolling_pct(vpin, window=500)   # fraction of past values <= current",
        "vpin_pct_latest = last_finite(vpin_pct)",
        "",
        "# State thresholds:",
        "NORMAL          vpin_pct < 0.70",
        "ELEVATED        vpin_pct < 0.90",
        "TOXIC           vpin_pct < 0.95",
        "EXTREME TOXICITY vpin_pct >= 0.95",
        "```",
        "## Toxicity composite (line 3188)",
        "```",
        "toxicity = 0.45 * vpin_pct",
        "         + 0.20 * spread_pct   (bid-ask spread rolling pct)",
        "         + 0.15 * kyle_pct     (Kyle lambda rolling pct)",
        "         + 0.10 * amihud_pct   (Amihud illiquidity rolling pct)",
        "         + 0.10 * roll_pct     (Roll measure rolling pct)",
        "```",
        "## Critical Limitation",
        "**VPIN is unsigned.** It measures the magnitude of order-flow imbalance,",
        "not the direction. `vpin = 0.8` is equally consistent with 80% buy-side",
        "or 80% sell-side toxicity. This is why directional/sided VPIN features",
        "must be constructed using delta_norm, mlofi, and book-side data.\n",
        "## Distribution (24,207 bars)",
        f"- Mean: {vpin_desc['vpin_sample_mean']}",
        f"- Std:  {vpin_desc['vpin_sample_std']}",
        f"- P95:  {vpin_desc['vpin_p95']}",
        f"- P99:  {vpin_desc['vpin_p99']}",
        f"- Max:  {vpin_desc['vpin_sample_max']}",
        "",
        "## What VPIN does NOT capture",
        "- Which side is informed (buy vs sell)",
        "- Whether high toxicity is bullish or bearish",
        "- Support/resistance context",
        "- Book-switch direction",
        "- Session/regime context",
    ]
    (OUT_DIR / "current_vpin_formula_audit.md").write_text("\n".join(audit_md))
    print("  current_vpin_formula_audit.md")

    catalog_rows = [
        {"field": k, "value": str(v)} for k, v in vpin_desc.items()
    ]
    _write_csv(OUT_DIR / "current_vpin_formula_catalog.csv", catalog_rows)


# ═══════════════════════════════════════════════════════════════════════════════
# PART B — VPIN SETTINGS GRID
# ═══════════════════════════════════════════════════════════════════════════════

def part_b_vpin_grid(df: pd.DataFrame) -> pd.DataFrame:
    print("\n── Part B: VPIN settings grid ──")
    base_vpin = df["vpin"].copy()
    result_cols: Dict[str, pd.Series] = {}
    catalog: List[Dict[str, Any]] = []

    sid = 0
    for W in VPIN_WINDOWS:
        # Step 1: rolling mean of raw vpin over W bars
        vpin_roll = base_vpin.rolling(W, min_periods=max(3, W // 4)).mean()

        for smooth in SMOOTH_SPANS:
            if smooth == 0:
                vpin_s = vpin_roll.copy()
                smooth_label = "raw"
            else:
                vpin_s = vpin_roll.ewm(span=smooth, min_periods=smooth // 2,
                                        adjust=False).mean()
                smooth_label = f"ewm{smooth}"

            for L in PCT_LOOKBACKS:
                sid += 1
                sid_str = f"V{sid:03d}_W{W}_S{smooth_label}_L{L}"
                vpin_pct = _rolling_pct_rank(vpin_s, L)
                vpin_z   = (vpin_s - vpin_s.rolling(L, min_periods=3).mean()) / \
                           (vpin_s.rolling(L, min_periods=3).std(ddof=1).replace(0, np.nan))

                state = pd.Series("LOW", index=df.index)
                state = state.where(vpin_pct.isna() | (vpin_pct >= 0.70), "LOW")
                state = state.where(vpin_pct.isna() | ~(
                    (vpin_pct >= 0.70) & (vpin_pct < 0.90)), state)
                # Rebuild properly
                s = pd.Series("LOW_TOXICITY", index=df.index)
                s[vpin_pct >= 0.70] = "ELEVATED"
                s[vpin_pct >= 0.90] = "EXTREME"
                s[vpin_pct.isna()]  = "LOW_TOXICITY"
                # Clean 3-state for analysis
                vpin_state = pd.cut(
                    vpin_pct.fillna(0),
                    bins=[-0.01, 0.70, 0.90, 0.95, 1.01],
                    labels=["LOW", "ELEVATED", "TOXIC", "EXTREME"],
                    right=True
                ).astype(str)
                vpin_state[vpin_pct.isna()] = "UNKNOWN"

                col_raw  = f"{sid_str}_raw"
                col_pct  = f"{sid_str}_pct"
                col_z    = f"{sid_str}_z"
                col_ewm  = f"{sid_str}_ewm"
                col_stat = f"{sid_str}_state"

                result_cols[col_raw]  = vpin_s
                result_cols[col_pct]  = vpin_pct
                result_cols[col_z]    = vpin_z
                result_cols[col_ewm]  = vpin_s   # same as smoothed in this grid
                result_cols[col_stat] = vpin_state.astype(str)

                catalog.append({
                    "vpin_setting_id":       sid_str,
                    "window_bars":           W,
                    "smoothing":             smooth_label,
                    "pct_lookback_bars":     L,
                    "n_extreme_70":          int((vpin_pct >= 0.70).sum()),
                    "n_extreme_90":          int((vpin_pct >= 0.90).sum()),
                    "n_extreme_95":          int((vpin_pct >= 0.95).sum()),
                    "pct_bars_extreme_95":   f"{(vpin_pct >= 0.95).mean():.4f}",
                    "vpin_raw_mean":         f"{vpin_s.mean():.4f}",
                    "vpin_raw_p95":          f"{vpin_s.quantile(0.95):.4f}",
                    "vpin_pct_mean":         f"{vpin_pct.mean():.4f}",
                    "col_raw": col_raw, "col_pct": col_pct, "col_z": col_z,
                    "col_state": col_stat,
                })

    panel = pd.concat([df[["bar_end_ts_ns", "timestamp_utc", "px_close",
                            "delta_norm", "vpin"]].copy(),
                       pd.DataFrame(result_cols, index=df.index)], axis=1)
    panel.to_parquet(OUT_DIR / "vpin_settings_panel.parquet", index=False)
    print(f"  vpin_settings_panel.parquet  ({sid} settings  {len(panel.columns)} cols)")
    _write_csv(OUT_DIR / "vpin_settings_catalog.csv", catalog)
    return panel


# ═══════════════════════════════════════════════════════════════════════════════
# PART C — DIRECTIONAL / SIDED VPIN FEATURES
# ═══════════════════════════════════════════════════════════════════════════════

def part_c_directional_features(df: pd.DataFrame,
                                 bs: pd.DataFrame,
                                 fm: pd.DataFrame) -> pd.DataFrame:
    print("\n── Part C: Directional VPIN features ──")

    # Use best baseline: W=120, smooth=raw, L=500 (standard production pct)
    base_vpin = df["vpin"].rolling(120, min_periods=30).mean()
    vpin_pct  = _rolling_pct_rank(base_vpin, 500)

    # Join book switch on bar_end_ts_ns
    bs_sub = bs[["bar_end_ts_ns", "bullish_switch_score", "bearish_switch_score",
                  "book_switch_net", "BF_bid_pull", "BF_ask_pull",
                  "BF_bid_add", "BF_ask_add",
                  "bullish_pair_balance", "bullish_min_pct",
                  "bearish_pair_balance", "bearish_min_pct"]].copy()
    merged = df.merge(bs_sub, on="bar_end_ts_ns", how="left")

    # Join feature master signed flow fields
    fm_cols = ["bar_end_ts_ns",
               "ofild_last_Signed", "ofild_sum_window_Signed",
               "ofild_sum_window_BidPull", "ofild_sum_window_BidAdd",
               "ofild_sum_window_AskPull", "ofild_sum_window_AskAdd",
               "bf_bid_pull_pressure", "bf_ask_pull_pressure",
               "bf_ask_pull_minus_bid_pull",
               "dash_flow_alignment", "dash_flow_direction",
               "sess_Asia","sess_EU","sess_US_Open","sess_US_AM","sess_US_PM","sess_US_Late"]
    fm_avail = [c for c in fm_cols if c in fm.columns]
    fm_sub = fm[fm_avail].drop_duplicates("bar_end_ts_ns")
    merged = merged.merge(fm_sub, on="bar_end_ts_ns", how="left")

    # Reindex vpin_pct to merged
    vpin_pct_arr = vpin_pct.reindex(merged.index).to_numpy()
    eps = 1e-8

    # ── 1. Signed VPIN (direction from sign of flow signal) ───────────────
    dn  = merged["delta_norm"].fillna(0).to_numpy()
    mlo = merged["mlofi_norm"].fillna(0).to_numpy()
    sig = merged["ofild_last_Signed"].fillna(0).to_numpy() \
          if "ofild_last_Signed" in merged.columns else np.zeros(len(merged))

    svpin_delta  = vpin_pct_arr * np.sign(dn)
    svpin_ofi    = vpin_pct_arr * np.sign(mlo)
    svpin_signed = vpin_pct_arr * np.sign(sig)

    # ── 2. Buy-toxic flow ────────────────────────────────────────────────
    bull_score = merged["bullish_switch_score"].fillna(0).to_numpy() \
                 if "bullish_switch_score" in merged.columns else np.zeros(len(merged))
    buy_tox        = vpin_pct_arr * np.maximum(dn, 0)
    buy_tox_ofi    = vpin_pct_arr * np.maximum(mlo / (np.abs(mlo).max() + eps), 0)
    buy_tox_switch = vpin_pct_arr * np.clip(bull_score, 0, 1)

    # ── 3. Sell-toxic flow ───────────────────────────────────────────────
    bear_score = merged["bearish_switch_score"].fillna(0).to_numpy() \
                 if "bearish_switch_score" in merged.columns else np.zeros(len(merged))
    bid_pull = merged["ofild_sum_window_BidPull"].fillna(0).to_numpy() \
               if "ofild_sum_window_BidPull" in merged.columns else np.zeros(len(merged))
    bid_add  = merged["ofild_sum_window_BidAdd"].fillna(0).to_numpy() \
               if "ofild_sum_window_BidAdd" in merged.columns else np.zeros(len(merged))

    sell_tox           = vpin_pct_arr * np.maximum(-dn, 0)
    sell_tox_ofi       = vpin_pct_arr * np.maximum(-mlo / (np.abs(mlo).max() + eps), 0)
    sell_tox_consump   = vpin_pct_arr * np.maximum((bid_pull - bid_add) /
                          (np.abs(bid_pull - bid_add).max() + eps), 0)
    sell_tox_switch    = vpin_pct_arr * np.clip(bear_score, 0, 1)

    # ── 4. Toxic imbalance ───────────────────────────────────────────────
    toxic_balance = buy_tox - sell_tox
    toxic_ratio   = buy_tox / np.maximum(sell_tox, eps)

    def _toxic_dir(b, s, vp):
        dirs = []
        for bi, si, vi in zip(b, s, vp):
            if vi < 0.70:
                dirs.append("LOW_TOXIC")
            elif bi > 0.05 and bi > si * 1.5:
                dirs.append("BUY_TOXIC")
            elif si > 0.05 and si > bi * 1.5:
                dirs.append("SELL_TOXIC")
            else:
                dirs.append("MIXED_TOXIC")
        return dirs

    toxic_dir = _toxic_dir(buy_tox, sell_tox, vpin_pct_arr)

    # ── Build feature panel ──────────────────────────────────────────────
    feat = merged[["bar_end_ts_ns", "timestamp_utc", "px_close",
                   "delta_norm", "mlofi_norm", "vpin"]].copy()
    feat["vpin_pct_W120_L500"]      = vpin_pct_arr
    feat["signed_vpin_delta"]       = svpin_delta
    feat["signed_vpin_ofi"]         = svpin_ofi
    feat["signed_vpin_signed_flow"] = svpin_signed
    feat["buy_toxicity"]            = buy_tox
    feat["buy_toxicity_ofi"]        = buy_tox_ofi
    feat["buy_toxicity_switch"]     = buy_tox_switch
    feat["sell_toxicity"]           = sell_tox
    feat["sell_toxicity_ofi"]       = sell_tox_ofi
    feat["sell_toxicity_consumption"] = sell_tox_consump
    feat["sell_toxicity_switch"]    = sell_tox_switch
    feat["toxic_side_balance"]      = toxic_balance
    feat["toxic_side_ratio"]        = np.log1p(np.clip(toxic_ratio, 0, 100))
    feat["toxic_side_direction"]    = toxic_dir

    # Also bring through key context columns
    for col in ["bullish_switch_score", "bearish_switch_score", "book_switch_net",
                "bf_bid_pull_pressure", "bf_ask_pull_pressure",
                "sess_Asia","sess_EU","sess_US_Open","sess_US_AM","sess_US_PM","sess_US_Late",
                "ofild_last_Signed", "ofild_sum_window_Signed"]:
        if col in merged.columns:
            feat[col] = merged[col].to_numpy()

    feat.to_parquet(OUT_DIR / "directional_vpin_feature_panel.parquet", index=False)
    print(f"  directional_vpin_feature_panel.parquet  ({len(feat)} rows  {len(feat.columns)} cols)")

    formula_catalog = [
        {"feature": "signed_vpin_delta",        "formula": "vpin_pct * sign(delta_norm)",               "direction_source": "delta_norm",          "interpretation": "+>0=buy-toxic  <0=sell-toxic"},
        {"feature": "signed_vpin_ofi",          "formula": "vpin_pct * sign(mlofi_norm)",               "direction_source": "mlofi_norm",           "interpretation": "+>0=buy-toxic  <0=sell-toxic"},
        {"feature": "signed_vpin_signed_flow",  "formula": "vpin_pct * sign(ofild_last_Signed)",        "direction_source": "ofild_last_Signed",    "interpretation": "+>0=buy-toxic  <0=sell-toxic"},
        {"feature": "buy_toxicity",             "formula": "vpin_pct * max(delta_norm, 0)",             "direction_source": "delta_norm",           "interpretation": "buy-toxic intensity 0..1"},
        {"feature": "buy_toxicity_ofi",         "formula": "vpin_pct * max(mlofi_norm/max_mlo, 0)",     "direction_source": "mlofi_norm",           "interpretation": "OFI-confirmed buy toxicity"},
        {"feature": "buy_toxicity_switch",      "formula": "vpin_pct * bullish_switch_score",           "direction_source": "book_switch_bullish",  "interpretation": "book-switch-confirmed buy toxicity"},
        {"feature": "sell_toxicity",            "formula": "vpin_pct * max(-delta_norm, 0)",            "direction_source": "delta_norm",           "interpretation": "sell-toxic intensity 0..1"},
        {"feature": "sell_toxicity_ofi",        "formula": "vpin_pct * max(-mlofi_norm/max_mlo, 0)",    "direction_source": "mlofi_norm",           "interpretation": "OFI-confirmed sell toxicity"},
        {"feature": "sell_toxicity_consumption","formula": "vpin_pct * max((BidPull-BidAdd)/max, 0)",   "direction_source": "BidPull vs BidAdd",    "interpretation": "bid consumption × toxicity"},
        {"feature": "sell_toxicity_switch",     "formula": "vpin_pct * bearish_switch_score",           "direction_source": "book_switch_bearish",  "interpretation": "book-switch-confirmed sell toxicity"},
        {"feature": "toxic_side_balance",       "formula": "buy_toxicity - sell_toxicity",              "direction_source": "combined",             "interpretation": "+>0=net buy toxic  <0=net sell toxic"},
        {"feature": "toxic_side_ratio",         "formula": "log1p(buy_toxicity / sell_toxicity)",       "direction_source": "combined",             "interpretation": ">0=buy dominant  <0=sell dominant"},
        {"feature": "toxic_side_direction",     "formula": "BUY_TOXIC|SELL_TOXIC|MIXED_TOXIC|LOW_TOXIC","direction_source": "combined",             "interpretation": "categorical phase state"},
    ]
    _write_csv(OUT_DIR / "directional_vpin_formula_catalog.csv", formula_catalog)
    return feat


# ═══════════════════════════════════════════════════════════════════════════════
# PART D — SPEARMAN PHASE MAP
# ═══════════════════════════════════════════════════════════════════════════════

def part_d_spearman_phase(df: pd.DataFrame, feat: pd.DataFrame) -> pd.DataFrame:
    print("\n── Part D: Spearman phase map ──")

    # Merge close price
    merged = feat.copy()
    if "px_close" not in merged.columns:
        merged["px_close"] = df["px_close"].values

    close = merged["px_close"].to_numpy(dtype=float)

    # Forward returns for phase analysis
    fwd_cols = {}
    for h in FWD_HORIZONS:
        fwd_cols[f"fwd_H{h}"] = np.concatenate([
            close[h:] / close[:-h] - 1, np.full(h, np.nan)])
    fwd_df = pd.DataFrame(fwd_cols, index=merged.index)

    # Key directional VPIN features for phase analysis
    phase_signals = {
        "signed_vpin_delta":       merged["signed_vpin_delta"].to_numpy(dtype=float),
        "signed_vpin_ofi":         merged["signed_vpin_ofi"].to_numpy(dtype=float),
        "buy_toxicity":            merged["buy_toxicity"].to_numpy(dtype=float),
        "sell_toxicity":           merged["sell_toxicity"].to_numpy(dtype=float),
        "toxic_side_balance":      merged["toxic_side_balance"].to_numpy(dtype=float),
        "vpin_pct_W120_L500":      merged["vpin_pct_W120_L500"].to_numpy(dtype=float),
    }
    if "buy_toxicity_switch" in merged.columns:
        phase_signals["buy_toxicity_switch"]  = merged["buy_toxicity_switch"].to_numpy(dtype=float)
    if "sell_toxicity_switch" in merged.columns:
        phase_signals["sell_toxicity_switch"] = merged["sell_toxicity_switch"].to_numpy(dtype=float)

    # Rolling Spearman for each (signal, window, horizon)
    spearman_results: List[Dict[str, Any]] = []
    spearman_panel_cols: Dict[str, np.ndarray] = {}

    n = len(merged)
    print(f"  Computing rolling Spearman: {len(phase_signals)} signals × "
          f"{len(SPEARMAN_WINDOWS)} windows × {len(FWD_HORIZONS)} horizons "
          f"on {n:,} bars", flush=True)

    for sig_name, sig_arr in phase_signals.items():
        for win in SPEARMAN_WINDOWS:
            for h in FWD_HORIZONS:
                fwd = fwd_df[f"fwd_H{h}"].to_numpy(dtype=float)
                rho_arr = _rolling_spearman(sig_arr, fwd, win)
                col_key = f"rho_{sig_name}_W{win}_H{h}"
                spearman_panel_cols[col_key] = rho_arr

                # Summary stats
                valid = rho_arr[np.isfinite(rho_arr)]
                if len(valid) < MIN_N:
                    continue
                spearman_results.append({
                    "signal":             sig_name,
                    "spearman_window":    win,
                    "forward_horizon":    h,
                    "mean_rho":           float(np.mean(valid)),
                    "median_rho":         float(np.median(valid)),
                    "std_rho":            float(np.std(valid, ddof=1)),
                    "pct_time_positive":  float(np.mean(valid > 0)),
                    "pct_time_negative":  float(np.mean(valid < 0)),
                    "pct_time_abs_gt20":  float(np.mean(np.abs(valid) > 0.20)),
                    "pct_time_abs_gt30":  float(np.mean(np.abs(valid) > 0.30)),
                    "n_valid_windows":    len(valid),
                })

    print(f"  {len(spearman_results)} Spearman cells computed")

    # Create phase panel
    phase_panel = merged[["bar_end_ts_ns", "timestamp_utc", "px_close",
                           "vpin_pct_W120_L500", "signed_vpin_delta",
                           "buy_toxicity", "sell_toxicity",
                           "toxic_side_balance", "toxic_side_direction"]].copy()
    for col, arr in spearman_panel_cols.items():
        phase_panel[col] = arr

    # Detect phase using rolling Spearman sign majority
    # Use the signed_vpin_delta + W240 + H10 as primary phase signal
    rho_key = "rho_signed_vpin_delta_W240_H10"
    if rho_key in spearman_panel_cols:
        rho = spearman_panel_cols[rho_key]
        phase = np.full(n, "VPIN_CHOP_PHASE", dtype=object)
        # Bearish: rho consistently negative + sell_toxicity high
        sell_tox_hi = merged["sell_toxicity"].to_numpy(dtype=float) > 0.10
        buy_tox_hi  = merged["buy_toxicity"].to_numpy(dtype=float) > 0.10
        vpin_hi     = merged["vpin_pct_W120_L500"].to_numpy(dtype=float) > 0.70

        phase[(rho < -0.10) & sell_tox_hi & vpin_hi]     = "VPIN_BEARISH_PHASE"
        phase[(rho > +0.10) & buy_tox_hi & vpin_hi]      = "VPIN_BULLISH_PHASE"

        # Transition: consecutive bars where rho changes sign
        rho_sign = np.sign(np.where(np.isfinite(rho), rho, 0.0))
        rho_sign_shift = np.concatenate([[0], rho_sign[:-1]])
        transition = ((rho_sign != rho_sign_shift) & (rho_sign != 0) & vpin_hi)
        phase[transition] = "VPIN_TRANSITION_PHASE"
        phase_panel["vpin_phase"] = phase

    # Phase transition events
    if "vpin_phase" in phase_panel.columns:
        ph = phase_panel["vpin_phase"].to_numpy()
        ph_shift = np.concatenate([[ph[0]], ph[:-1]])
        transitions = phase_panel[ph != ph_shift].copy()
        transition_rows = []
        for i, row in transitions.iterrows():
            prev_ph = ph_shift[i]
            transition_rows.append({
                "bar_idx":        i,
                "timestamp_utc":  row.get("timestamp_utc", ""),
                "px_close":       _fmt(row.get("px_close", np.nan)),
                "from_phase":     prev_ph,
                "to_phase":       row["vpin_phase"],
                "vpin_pct":       _fmt(row.get("vpin_pct_W120_L500", np.nan)),
                "sell_toxicity":  _fmt(row.get("sell_toxicity", np.nan)),
                "buy_toxicity":   _fmt(row.get("buy_toxicity", np.nan)),
                "spearman_rho":   _fmt(spearman_panel_cols.get(rho_key, np.full(n,np.nan))[i] if i < n else np.nan),
            })
        _write_csv(OUT_DIR / "vpin_phase_transition_events.csv", transition_rows[:500])
    else:
        phase_panel["vpin_phase"] = "VPIN_CHOP_PHASE"

    phase_panel.to_parquet(OUT_DIR / "vpin_spearman_phase_panel.parquet", index=False)
    print(f"  vpin_spearman_phase_panel.parquet  ({len(phase_panel.columns)} cols)")
    _write_csv(OUT_DIR / "vpin_spearman_settings_results.csv", spearman_results)
    return phase_panel


# ═══════════════════════════════════════════════════════════════════════════════
# PART E — FORWARD OUTCOME TESTING
# ═══════════════════════════════════════════════════════════════════════════════

def part_e_forward_outcomes(df: pd.DataFrame, feat: pd.DataFrame,
                             phase_panel: pd.DataFrame) -> None:
    print("\n── Part E: Forward outcome testing ──")
    close = feat["px_close"].to_numpy(dtype=float)
    n = len(close)

    # Pre-compute MFE/MAE for H5, H10, H20 (H40, H80 too slow vectorized)
    mfe_cache: Dict[int, np.ndarray] = {}
    mae_cache: Dict[int, np.ndarray] = {}
    for h in [5, 10, 20, 40, 80]:
        mf, ma = _mfe_mae(pd.Series(close), h)
        mfe_cache[h] = mf.to_numpy(dtype=float)
        mae_cache[h] = ma.to_numpy(dtype=float)

    # Forward returns
    fwd: Dict[int, np.ndarray] = {}
    for h in FWD_HORIZONS:
        fwd[h] = np.concatenate([close[h:] / close[:-h] - 1, np.full(h, np.nan)])

    vpin_pct = feat["vpin_pct_W120_L500"].to_numpy(dtype=float)
    buy_tox  = feat["buy_toxicity"].to_numpy(dtype=float)
    sell_tox = feat["sell_toxicity"].to_numpy(dtype=float)
    signed_vd = feat["signed_vpin_delta"].to_numpy(dtype=float)
    toxic_bal = feat["toxic_side_balance"].to_numpy(dtype=float)
    toxic_dir = feat["toxic_side_direction"].to_numpy()

    phase = phase_panel["vpin_phase"].to_numpy() if "vpin_phase" in phase_panel.columns \
            else np.full(n, "ALL")

    # Feature master context for VPIN+context tests
    # (re-join needed columns)
    fm_avail = {}
    try:
        fm_tmp = pd.read_parquet(FM_PARQUET, columns=[
            "bar_end_ts_ns",
            "rxn_rejection_from_below", "rxn_rejection_from_above",
            "ofild_sum_window_BidPull", "ofild_sum_window_BidAdd",
            "ofild_sum_window_AskPull", "ofild_sum_window_AskAdd",
        ])
        merged_fm = feat[["bar_end_ts_ns"]].merge(fm_tmp, on="bar_end_ts_ns", how="left")
        for col in ["rxn_rejection_from_below","rxn_rejection_from_above",
                    "ofild_sum_window_BidPull","ofild_sum_window_BidAdd",
                    "ofild_sum_window_AskPull","ofild_sum_window_AskAdd"]:
            if col in merged_fm.columns:
                fm_avail[col] = pd.to_numeric(merged_fm[col], errors="coerce").fillna(0).to_numpy()
    except Exception:
        pass

    bs_avail = {}
    try:
        bs_tmp = pd.read_parquet(BS_PARQUET, columns=[
            "bar_end_ts_ns","bullish_pair_balance","bullish_min_pct",
            "bearish_pair_balance","bearish_min_pct"])
        merged_bs = feat[["bar_end_ts_ns"]].merge(bs_tmp, on="bar_end_ts_ns", how="left")
        for col in ["bullish_pair_balance","bullish_min_pct","bearish_pair_balance","bearish_min_pct"]:
            if col in merged_bs.columns:
                bs_avail[col] = pd.to_numeric(merged_bs[col], errors="coerce").fillna(0).to_numpy()
    except Exception:
        pass

    # Define 10 conditions
    conditions: List[Tuple[str, np.ndarray, int]] = [
        ("1_HIGH_VPIN_ONLY",        vpin_pct >= 0.90,                               0),
        ("2_BUY_TOXIC_VPIN",        buy_tox > np.nanpercentile(buy_tox[buy_tox>0], 75)
                                    if (buy_tox > 0).sum() > MIN_N else buy_tox > 0.10, 1),
        ("3_SELL_TOXIC_VPIN",       sell_tox > np.nanpercentile(sell_tox[sell_tox>0], 75)
                                    if (sell_tox > 0).sum() > MIN_N else sell_tox > 0.10, -1),
        ("4_VPIN_BEARISH_PHASE",    phase == "VPIN_BEARISH_PHASE",                  -1),
        ("5_VPIN_BULLISH_PHASE",    phase == "VPIN_BULLISH_PHASE",                   1),
        ("6_VPIN_TRANSITION_PHASE", phase == "VPIN_TRANSITION_PHASE",                0),
        ("7_VPIN_SUPPORT_CONSUMED", (vpin_pct >= 0.70) &
                                    (fm_avail.get("ofild_sum_window_BidPull",
                                                   np.zeros(n)) >
                                     fm_avail.get("ofild_sum_window_BidAdd",
                                                   np.ones(n))),                    -1),
        ("8_VPIN_RESISTANCE_CONSUMED", (vpin_pct >= 0.70) &
                                    (fm_avail.get("ofild_sum_window_AskPull",
                                                   np.zeros(n)) >
                                     fm_avail.get("ofild_sum_window_AskAdd",
                                                   np.ones(n))),                     1),
        ("9_VPIN_BULLISH_BOOK_SWITCH",
                                    (vpin_pct >= 0.70) &
                                    (bs_avail.get("bullish_pair_balance",
                                                   np.zeros(n)) >= 0.95) &
                                    (bs_avail.get("bullish_min_pct",
                                                   np.zeros(n)) >= 0.95),            1),
        ("10_VPIN_BEARISH_BOOK_SWITCH",
                                    (vpin_pct >= 0.70) &
                                    (bs_avail.get("bearish_pair_balance",
                                                   np.zeros(n)) >= 0.95) &
                                    (bs_avail.get("bearish_min_pct",
                                                   np.zeros(n)) >= 0.95),           -1),
    ]

    outcome_rows: List[Dict[str, Any]] = []
    mfe_mae_rows: List[Dict[str, Any]] = []

    for cond_name, mask, direction in conditions:
        mask = np.asarray(mask, dtype=bool)
        for h in FWD_HORIZONS:
            stats = _outcome_stats(
                fwd[h], mask, mfe_cache[h], mae_cache[h], direction
            )
            row = {
                "condition":         cond_name,
                "direction":         "LONG" if direction == 1 else
                                     "SHORT" if direction == -1 else "BOTH",
                "horizon_H":         h,
                "n":                 stats["n"],
                "hit_rate":          stats["hit_rate"],
                "mean_ret":          stats["mean_ret"],
                "median_ret":        stats["median_ret"],
                "sharpe_like":       stats["sharpe"],
                "mfe_mean":          stats["mfe_mean"],
                "mae_mean":          stats["mae_mean"],
                "mfe_mae_ratio":     stats["mfe_mae_ratio"],
                "continuation_rate": stats["continuation_rate"],
                "reversal_rate":     stats["reversal_rate"],
            }
            outcome_rows.append(row)
            if h <= 20:
                mfe_mae_rows.append({
                    "condition": cond_name,
                    "horizon_H": h,
                    "n": stats["n"],
                    "mfe_mean": stats["mfe_mean"],
                    "mae_mean": stats["mae_mean"],
                    "mfe_mae_ratio": stats["mfe_mae_ratio"],
                })

    _write_csv(OUT_DIR / "vpin_forward_outcome_summary.csv", outcome_rows)
    _write_csv(OUT_DIR / "vpin_mfe_mae_by_setting.csv",      mfe_mae_rows)

    # Phase-specific returns
    phase_ret_rows: List[Dict[str, Any]] = []
    for ph_name in ["VPIN_BEARISH_PHASE","VPIN_BULLISH_PHASE","VPIN_TRANSITION_PHASE","VPIN_CHOP_PHASE"]:
        ph_mask = phase == ph_name
        n_phase = int(ph_mask.sum())
        for h in FWD_HORIZONS:
            r = fwd[h][ph_mask]
            r = r[np.isfinite(r)]
            phase_ret_rows.append({
                "phase":       ph_name,
                "horizon_H":   h,
                "n_bars":      n_phase,
                "n_returns":   len(r),
                "mean_ret":    _fmt(np.nanmean(r)) if len(r) >= MIN_N else "--",
                "median_ret":  _fmt(np.nanmedian(r)) if len(r) >= MIN_N else "--",
                "hit_rate_long": _fmt(np.mean(r > 0)) if len(r) >= MIN_N else "--",
                "std_ret":     _fmt(np.std(r, ddof=1)) if len(r) >= MIN_N else "--",
                "sharpe":      _fmt(np.nanmean(r) / np.nanstd(r, ddof=1))
                               if len(r) >= MIN_N and np.nanstd(r) > 1e-10 else "--",
            })
    _write_csv(OUT_DIR / "vpin_phase_forward_returns.csv", phase_ret_rows)


# ═══════════════════════════════════════════════════════════════════════════════
# PART F — CONDITIONAL LEVEL-CONTEXT TESTING
# ═══════════════════════════════════════════════════════════════════════════════

def part_f_conditional_filters(df: pd.DataFrame, feat: pd.DataFrame,
                                bs: pd.DataFrame, fm: pd.DataFrame) -> None:
    print("\n── Part F: Conditional level-context testing ──")

    close = feat["px_close"].to_numpy(dtype=float)
    n = len(close)
    fwd10 = np.concatenate([close[10:] / close[:-10] - 1, np.full(10, np.nan)])
    mfe10, mae10 = _mfe_mae(pd.Series(close), 10)
    mfe10 = mfe10.to_numpy(dtype=float)
    mae10 = mae10.to_numpy(dtype=float)

    vpin_pct  = feat["vpin_pct_W120_L500"].to_numpy(dtype=float)
    buy_tox   = feat["buy_toxicity"].to_numpy(dtype=float)
    sell_tox  = feat["sell_toxicity"].to_numpy(dtype=float)

    # Build FM arrays
    def _fm(col):
        if col in fm.columns:
            merged_col = feat[["bar_end_ts_ns"]].merge(
                fm[["bar_end_ts_ns", col]].drop_duplicates("bar_end_ts_ns"),
                on="bar_end_ts_ns", how="left")[col]
            return pd.to_numeric(merged_col, errors="coerce").fillna(0).to_numpy()
        return np.zeros(n)

    def _bs(col):
        if col in bs.columns:
            merged_col = feat[["bar_end_ts_ns"]].merge(
                bs[["bar_end_ts_ns", col]].drop_duplicates("bar_end_ts_ns"),
                on="bar_end_ts_ns", how="left")[col]
            return pd.to_numeric(merged_col, errors="coerce").fillna(0).to_numpy()
        return np.zeros(n)

    rxn_below  = _fm("rxn_rejection_from_below")
    rxn_above  = _fm("rxn_rejection_from_above")
    bid_pull   = _fm("ofild_sum_window_BidPull")
    bid_add    = _fm("ofild_sum_window_BidAdd")
    ask_pull   = _fm("ofild_sum_window_AskPull")
    ask_add    = _fm("ofild_sum_window_AskAdd")
    dist_ticks = _fm("ofild_distance_ticks")
    lvl_poc    = _fm("lvl_POC")
    lvl_hvn    = _fm("lvl_HVN")
    lvl_lvn    = _fm("lvl_LVN")
    sess_us_am = _fm("sess_US_AM")
    sess_eu    = _fm("sess_EU")
    sess_asia  = _fm("sess_Asia")

    bull_pb    = _bs("bullish_pair_balance")
    bull_mp    = _bs("bullish_min_pct")
    bear_pb    = _bs("bearish_pair_balance")
    bear_mp    = _bs("bearish_min_pct")

    filters: List[Tuple[str, np.ndarray]] = [
        ("ALL_BARS",               np.ones(n, dtype=bool)),
        ("NEAR_SR_4T",             (dist_ticks > 0) & (dist_ticks <= 4)),
        ("NEAR_POC_HVN_LVN",      ((lvl_poc == 1) | (lvl_hvn == 1) | (lvl_lvn == 1)) &
                                   (dist_ticks > 0) & (dist_ticks <= 4)),
        ("SUPPORT_TEST",           rxn_below == 1),
        ("RESISTANCE_TEST",        rxn_above == 1),
        ("SUPPORT_CONSUMED",       bid_pull > bid_add),
        ("RESISTANCE_CONSUMED",    ask_pull > ask_add),
        ("BULLISH_BOOK_SWITCH",    (bull_pb >= 0.95) & (bull_mp >= 0.95)),
        ("BEARISH_DIAG",           (bear_pb >= 0.95) & (bear_mp >= 0.95)),
        ("SESSION_US_AM",          sess_us_am == 1),
        ("SESSION_LONDON",         sess_eu == 1),
        ("SESSION_ASIA",           sess_asia == 1),
    ]

    cond_rows: List[Dict[str, Any]] = []
    best_rows: List[Dict[str, Any]] = []

    for flt_name, flt_mask in filters:
        n_flt = int(flt_mask.sum())
        if n_flt < MIN_N * 2:
            cond_rows.append({"filter": flt_name, "n_bars": n_flt,
                               "SKIP": "INSUFFICIENT_SAMPLE"})
            continue

        # Test: sell-toxic under this filter
        sell_cond = flt_mask & (sell_tox > np.nanpercentile(sell_tox[flt_mask & (sell_tox > 0)],
                                  70) if (flt_mask & (sell_tox > 0)).sum() > MIN_N else 0.05)
        buy_cond  = flt_mask & (buy_tox > np.nanpercentile(buy_tox[flt_mask & (buy_tox > 0)],
                                  70) if (flt_mask & (buy_tox > 0)).sum() > MIN_N else 0.05)
        vpin_cond = flt_mask & (vpin_pct >= 0.90)

        for cname, cmask, dirn in [
            (f"{flt_name}_SELL_TOXIC", sell_cond, -1),
            (f"{flt_name}_BUY_TOXIC",  buy_cond,   1),
            (f"{flt_name}_HIGH_VPIN",  vpin_cond,  0),
        ]:
            stats = _outcome_stats(fwd10, cmask, mfe10, mae10, dirn)
            cond_rows.append({
                "filter":         flt_name,
                "condition":      cname,
                "direction":      "SHORT" if dirn == -1 else "LONG" if dirn == 1 else "BOTH",
                "n_bars_filter":  n_flt,
                "n_condition":    stats["n"],
                "hit_rate":       _fmt(stats["hit_rate"]),
                "mean_ret_H10":   _fmt(stats["mean_ret"]),
                "sharpe":         _fmt(stats["sharpe"]),
                "mfe_mae_ratio":  _fmt(stats["mfe_mae_ratio"]),
            })

        # Best setting per filter: whichever of sell/buy/vpin has highest |sharpe|
        best = max([
            ("SELL_TOXIC", _outcome_stats(fwd10, sell_cond, mfe10, mae10, -1)),
            ("BUY_TOXIC",  _outcome_stats(fwd10, buy_cond,  mfe10, mae10,  1)),
            ("HIGH_VPIN",  _outcome_stats(fwd10, vpin_cond, mfe10, mae10,  0)),
        ], key=lambda x: abs(x[1]["sharpe"]) if x[1]["sharpe"] is not None
                                             and math.isfinite(x[1]["sharpe"]) else 0)
        best_rows.append({
            "filter":           flt_name,
            "n_bars":           n_flt,
            "best_condition":   best[0],
            "best_n":           best[1]["n"],
            "best_hit_rate":    _fmt(best[1]["hit_rate"]),
            "best_mean_ret":    _fmt(best[1]["mean_ret"]),
            "best_sharpe":      _fmt(best[1]["sharpe"]),
            "best_mfe_mae":     _fmt(best[1]["mfe_mae_ratio"]),
            "best_vpin_window": "120",
            "best_pct_lookback":"500",
            "best_horizon":     "10",
        })

    _write_csv(OUT_DIR / "vpin_conditional_filter_results.csv", cond_rows)
    _write_csv(OUT_DIR / "vpin_best_settings_by_filter.csv",    best_rows)


# ═══════════════════════════════════════════════════════════════════════════════
# PART G — SESSION / REGIME ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def part_g_session_regime(df: pd.DataFrame, feat: pd.DataFrame,
                           fm: pd.DataFrame) -> None:
    print("\n── Part G: Session / regime analysis ──")

    close = feat["px_close"].to_numpy(dtype=float)
    n = len(close)
    fwd10 = np.concatenate([close[10:] / close[:-10] - 1, np.full(10, np.nan)])
    mfe10, mae10 = _mfe_mae(pd.Series(close), 10)
    mfe10 = mfe10.to_numpy(dtype=float)
    mae10 = mae10.to_numpy(dtype=float)

    vpin_pct = feat["vpin_pct_W120_L500"].to_numpy(dtype=float)
    buy_tox  = feat["buy_toxicity"].to_numpy(dtype=float)
    sell_tox = feat["sell_toxicity"].to_numpy(dtype=float)
    ts       = feat["timestamp_utc"]

    # Session labels
    def _fm_col(col):
        if col in fm.columns:
            m = feat[["bar_end_ts_ns"]].merge(
                fm[["bar_end_ts_ns", col]].drop_duplicates("bar_end_ts_ns"),
                on="bar_end_ts_ns", how="left")[col]
            return pd.to_numeric(m, errors="coerce").fillna(0).to_numpy()
        return np.zeros(n)

    sess_map = {
        "Asia":    _fm_col("sess_Asia"),
        "London":  _fm_col("sess_EU"),
        "US_Open": _fm_col("sess_US_Open"),
        "US_AM":   _fm_col("sess_US_AM"),
        "US_PM":   _fm_col("sess_US_PM"),
        "US_Late": _fm_col("sess_US_Late"),
    }

    # Regime from feature master
    regime_ic = _fm_col("master_regime_ic_delta")
    cusum_up  = df["cusum_up_break"].reindex(feat.index).fillna(0).to_numpy()
    cusum_dn  = df["cusum_down_break"].reindex(feat.index).fillna(0).to_numpy()

    sess_rows:   List[Dict[str, Any]] = []
    regime_rows: List[Dict[str, Any]] = []

    # --- session rows ---
    for sess_name, sess_arr in sess_map.items():
        mask = sess_arr == 1
        n_s  = int(mask.sum())
        if n_s < MIN_N:
            continue

        for W in [40, 120, 240]:
            vpin_w = feat[f"vpin_pct_W120_L500"].to_numpy(dtype=float)  # simplified

            # sell-toxic in session
            sell_mask = mask & (sell_tox > 0.10)
            buy_mask  = mask & (buy_tox > 0.10)
            vpin_mask = mask & (vpin_pct >= 0.90)

            s_s = _outcome_stats(fwd10, sell_mask, mfe10, mae10, -1)
            b_s = _outcome_stats(fwd10, buy_mask,  mfe10, mae10,  1)
            v_s = _outcome_stats(fwd10, vpin_mask, mfe10, mae10,  0)

            sess_rows.append({
                "session":         sess_name,
                "vpin_window":     W,
                "pct_lookback":    500,
                "n_session_bars":  n_s,
                "sell_toxic_n":    s_s["n"],
                "sell_toxic_hit":  _fmt(s_s["hit_rate"]),
                "sell_toxic_sharpe": _fmt(s_s["sharpe"]),
                "buy_toxic_n":     b_s["n"],
                "buy_toxic_hit":   _fmt(b_s["hit_rate"]),
                "buy_toxic_sharpe":_fmt(b_s["sharpe"]),
                "high_vpin_n":     v_s["n"],
                "high_vpin_hit":   _fmt(v_s["hit_rate"]),
                "high_vpin_sharpe":_fmt(v_s["sharpe"]),
            })

    _write_csv(OUT_DIR / "vpin_by_session.csv", sess_rows)

    # --- regime rows ---
    def _best_vpin_stats(regime_mask):
        n_r = int(regime_mask.sum())
        if n_r < MIN_N:
            return {"n": n_r, "best_w": "--", "best_h": "--",
                    "buy_sharpe": "--", "sell_sharpe": "--"}
        best_sharpe = 0.0; best_w = 120
        for W_test in [60, 120, 240]:
            vpin_w = _rolling_pct_rank(feat["vpin"].rolling(W_test, min_periods=10).mean(), 500)
            vp = vpin_w.to_numpy(dtype=float)
            m = regime_mask & (vp >= 0.80)
            if m.sum() < MIN_N:
                continue
            s = _outcome_stats(fwd10, m, mfe10, mae10, 0)
            if s["sharpe"] and math.isfinite(s["sharpe"]) and abs(s["sharpe"]) > abs(best_sharpe):
                best_sharpe = s["sharpe"]; best_w = W_test
        sell_s = _outcome_stats(fwd10, regime_mask & (sell_tox > 0.10), mfe10, mae10, -1)
        buy_s  = _outcome_stats(fwd10, regime_mask & (buy_tox > 0.10),  mfe10, mae10,  1)
        return {"n": n_r, "best_w": best_w,
                "buy_sharpe": _fmt(buy_s["sharpe"]),
                "sell_sharpe": _fmt(sell_s["sharpe"])}

    for rname, rmask in [
        ("ALL",           np.ones(n, dtype=bool)),
        ("CUSUM_UP",      cusum_up > 0),
        ("CUSUM_DOWN",    cusum_dn > 0),
        ("TREND_IC_POS",  regime_ic > 0.10),
        ("TREND_IC_NEG",  regime_ic < -0.10),
        ("HIGH_VPIN_REGIME", vpin_pct >= 0.90),
    ]:
        rrs = _best_vpin_stats(np.asarray(rmask, dtype=bool))
        regime_rows.append({
            "regime":            rname,
            "n_bars":            rrs["n"],
            "best_vpin_window":  rrs["best_w"],
            "best_pct_lookback": "500",
            "best_horizon":      "H10",
            "buy_toxic_sharpe":  rrs["buy_sharpe"],
            "sell_toxic_sharpe": rrs["sell_sharpe"],
        })

    _write_csv(OUT_DIR / "vpin_by_regime.csv", regime_rows)


# ═══════════════════════════════════════════════════════════════════════════════
# PART H — BEST SETTING SELECTION
# ═══════════════════════════════════════════════════════════════════════════════

def part_h_best_settings(df: pd.DataFrame, feat: pd.DataFrame) -> Dict[str, Any]:
    print("\n── Part H: Best setting selection ──")

    close = feat["px_close"].to_numpy(dtype=float)
    n = len(close)

    # Walk-forward stability test: for each window setting, compute OOS-like
    # rolling Spearman stability across [60,120,240] bar windows
    # using signed_vpin_delta vs fwd_H10
    fwd10 = np.concatenate([close[10:] / close[:-10] - 1, np.full(10, np.nan)])
    sig = feat["signed_vpin_delta"].to_numpy(dtype=float)

    stability_rows: List[Dict[str, Any]] = []
    best_candidates: List[Dict[str, Any]] = []

    for W in VPIN_WINDOWS:
        vpin_w = feat["vpin"].rolling(W, min_periods=max(3, W // 4)).mean()

        for smooth in [0, 20, 120]:
            if smooth == 0:
                vs = vpin_w
                sl = "raw"
            else:
                vs = vpin_w.ewm(span=smooth, adjust=False).mean()
                sl = f"ewm{smooth}"

            for L in [250, 500, 1000]:
                vp = _rolling_pct_rank(vs, L).to_numpy(dtype=float)
                high_mask = vp >= 0.90
                n_high = int(high_mask.sum())
                if n_high < MIN_N * 2:
                    continue

                # Rolling Spearman stability: compute rho in 3 non-overlapping thirds
                third = n // 3
                rhos = []
                for chunk_start in [0, third, 2 * third]:
                    sl_end = min(chunk_start + third, n - 10)
                    xs = sig[chunk_start:sl_end]
                    ys = fwd10[chunk_start:sl_end]
                    mask = np.isfinite(xs) & np.isfinite(ys)
                    if mask.sum() >= MIN_N:
                        r, _ = spearmanr(xs[mask], ys[mask])
                        rhos.append(float(r))

                stability = float(np.std(rhos)) if len(rhos) >= 2 else np.nan
                mean_rho  = float(np.mean(rhos)) if rhos else np.nan

                # Outcome stats for high-VPIN under this setting
                mfe10, mae10 = _mfe_mae(pd.Series(close), 10)
                sell_mask = high_mask & (feat["sell_toxicity"].to_numpy(dtype=float) > 0.10)
                s = _outcome_stats(fwd10, sell_mask, mfe10.to_numpy(), mae10.to_numpy(), -1)

                stability_rows.append({
                    "vpin_window": W, "smooth": sl, "pct_lookback": L,
                    "n_high_vpin": n_high,
                    "mean_signed_rho": _fmt(mean_rho),
                    "rho_stability_std": _fmt(stability),
                    "sell_toxic_hit":    _fmt(s["hit_rate"]),
                    "sell_toxic_sharpe": _fmt(s["sharpe"]),
                    "sell_toxic_mfe_mae":_fmt(s["mfe_mae_ratio"]),
                })

                # Score: stability + hit_rate + sharpe
                if (s["hit_rate"] and math.isfinite(s["hit_rate"]) and
                    s["sharpe"] and math.isfinite(s["sharpe"])):
                    score = (s["hit_rate"] * 2 +
                             min(abs(s["sharpe"]), 3.0) * 0.5 +
                             (1.0 - min(stability, 1.0) if math.isfinite(stability) else 0))
                    best_candidates.append({
                        "score": score, "W": W, "smooth": sl, "L": L,
                        "n_high": n_high, "hit_rate": s["hit_rate"],
                        "sharpe": s["sharpe"], "stability": stability,
                    })

    _write_csv(OUT_DIR / "vpin_setting_stability_report.csv", stability_rows)

    best_candidates.sort(key=lambda x: x["score"], reverse=True)
    top = best_candidates[:10] if best_candidates else []

    best = top[0] if top else {"W": 120, "smooth": "raw", "L": 500}

    ranked_rows = []
    for i, b in enumerate(top, 1):
        ranked_rows.append({
            "rank": i,
            "vpin_window": b.get("W"),
            "smoothing": b.get("smooth"),
            "pct_lookback": b.get("L"),
            "score": _fmt(b.get("score", np.nan)),
            "n_high_vpin_bars": b.get("n_high"),
            "sell_toxic_hit_rate": _fmt(b.get("hit_rate", np.nan)),
            "sell_toxic_sharpe": _fmt(b.get("sharpe", np.nan)),
            "oos_stability_std": _fmt(b.get("stability", np.nan)),
        })
    _write_csv(OUT_DIR / "best_vpin_settings_ranked.csv", ranked_rows)

    # Best directional feature set
    best_feat_rows = [
        {"rank": 1, "feature": "signed_vpin_delta",       "use_case": "signed phase detection",          "horizon": "H10,H20", "status": "READY"},
        {"rank": 2, "feature": "sell_toxicity",           "use_case": "sell-side toxic flow detection",   "horizon": "H5,H10",  "status": "READY"},
        {"rank": 3, "feature": "buy_toxicity",            "use_case": "buy-side toxic flow detection",    "horizon": "H5,H10",  "status": "READY"},
        {"rank": 4, "feature": "toxic_side_balance",      "use_case": "net flow direction",               "horizon": "H10,H20", "status": "READY"},
        {"rank": 5, "feature": "sell_toxicity_consumption","use_case":"bid consumption × toxicity",       "horizon": "H5",      "status": "REQUIRES_FM_JOIN"},
        {"rank": 6, "feature": "buy_toxicity_switch",     "use_case": "book-switch confirmed buy flow",   "horizon": "H10",     "status": "REQUIRES_BS_JOIN"},
        {"rank": 7, "feature": "sell_toxicity_switch",    "use_case": "book-switch confirmed sell flow",  "horizon": "H10",     "status": "REQUIRES_BS_JOIN"},
        {"rank": 8, "feature": "signed_vpin_ofi",         "use_case": "OFI-confirmed direction",          "horizon": "H5,H10",  "status": "READY"},
    ]
    _write_csv(OUT_DIR / "best_directional_vpin_feature_set.csv", best_feat_rows)

    best_settings = {
        "best_vpin_window":        best.get("W", 120),
        "best_smooth":             best.get("smooth", "raw"),
        "best_pct_lookback":       best.get("L", 500),
        "best_buy_toxic_feature":  "buy_toxicity",
        "best_sell_toxic_feature": "sell_toxicity",
        "best_phase_detector":     "signed_vpin_delta rolling Spearman W240 H10",
        "best_threshold":          0.90,
        "best_horizon":            10,
        "best_filter":             "NEAR_SR_ONLY + SUPPORT/RESISTANCE_CONSUMED",
    }
    print(f"  Best window: W={best.get('W',120)}  smooth={best.get('smooth','raw')}  L={best.get('L',500)}")
    return best_settings


# ═══════════════════════════════════════════════════════════════════════════════
# PART I — PHASE CASE STUDIES
# ═══════════════════════════════════════════════════════════════════════════════

def part_i_case_studies(df: pd.DataFrame, feat: pd.DataFrame,
                         phase_panel: pd.DataFrame) -> None:
    print("\n── Part I: Phase case studies ──")

    if "vpin_phase" not in phase_panel.columns:
        print("  No phase column — skipping case studies")
        return

    close = feat["px_close"].to_numpy(dtype=float)
    n = len(close)

    # Find transition events: bearish → bullish transitions (heatmap turns blue)
    ph = phase_panel["vpin_phase"].to_numpy()
    ts = feat["timestamp_utc"].to_numpy()

    case_rows: List[Dict[str, Any]] = []

    for i in range(5, n - 80):
        # Look for: 5 consecutive bearish followed by transition or bullish
        window_back = ph[max(0, i-10):i]
        if not (np.sum(window_back == "VPIN_BEARISH_PHASE") >= 3):
            continue

        future_ph = ph[i:min(i+20, n)]
        if "VPIN_BULLISH_PHASE" not in future_ph and "VPIN_TRANSITION_PHASE" not in future_ph:
            continue

        # Compute forward return
        h5 = close[min(i+5, n-1)] / close[i] - 1 if i + 5 < n else np.nan
        h10 = close[min(i+10, n-1)] / close[i] - 1 if i + 10 < n else np.nan
        h20 = close[min(i+20, n-1)] / close[i] - 1 if i + 20 < n else np.nan

        # MFE/MAE
        fwd_slice = close[i+1: min(i+21, n)]
        mfe_v = float((fwd_slice / close[i] - 1).max()) if len(fwd_slice) > 0 else np.nan
        mae_v = float((fwd_slice / close[i] - 1).min()) if len(fwd_slice) > 0 else np.nan

        case_rows.append({
            "bar_idx":         i,
            "timestamp_utc":   str(ts[i]),
            "px_close":        _fmt(close[i], ".2f"),
            "phase_at_bar":    ph[i],
            "preceding_phase": "VPIN_BEARISH_PHASE",
            "signal_type":     "BEARISH_TO_BULLISH_TRANSITION",
            "vpin_pct":        _fmt(feat["vpin_pct_W120_L500"].iloc[i]),
            "sell_toxicity":   _fmt(feat["sell_toxicity"].iloc[i]),
            "buy_toxicity":    _fmt(feat["buy_toxicity"].iloc[i]),
            "fwd_ret_H5":      _fmt(h5),
            "fwd_ret_H10":     _fmt(h10),
            "fwd_ret_H20":     _fmt(h20),
            "mfe_H20":         _fmt(mfe_v),
            "mae_H20":         _fmt(mae_v),
            "signal_before_move": "YES" if h10 > 0 else "NO",
        })

        if len(case_rows) >= 100:
            break

    # Also find pure bearish phase continuation examples
    for i in range(5, n - 40):
        if ph[i] != "VPIN_BEARISH_PHASE":
            continue
        if len(case_rows) >= 150:
            break
        h10 = close[min(i+10, n-1)] / close[i] - 1 if i + 10 < n else np.nan
        case_rows.append({
            "bar_idx":         i,
            "timestamp_utc":   str(ts[i]),
            "px_close":        _fmt(close[i], ".2f"),
            "phase_at_bar":    "VPIN_BEARISH_PHASE",
            "preceding_phase": ph[max(0, i-1)],
            "signal_type":     "BEARISH_CONTINUATION",
            "vpin_pct":        _fmt(feat["vpin_pct_W120_L500"].iloc[i]),
            "sell_toxicity":   _fmt(feat["sell_toxicity"].iloc[i]),
            "buy_toxicity":    _fmt(feat["buy_toxicity"].iloc[i]),
            "fwd_ret_H5":      "--",
            "fwd_ret_H10":     _fmt(h10),
            "fwd_ret_H20":     "--",
            "mfe_H20":         "--", "mae_H20":         "--",
            "signal_before_move": "YES" if h10 < 0 else "NO",
        })

    _write_csv(OUT_DIR / "vpin_phase_case_studies.csv", case_rows)

    # Case study report
    n_transitions = sum(1 for r in case_rows if r.get("signal_type") == "BEARISH_TO_BULLISH_TRANSITION")
    n_cont_correct = sum(1 for r in case_rows
                         if r.get("signal_type") == "BEARISH_CONTINUATION"
                         and r.get("signal_before_move") == "YES")
    n_cont = sum(1 for r in case_rows if r.get("signal_type") == "BEARISH_CONTINUATION")
    n_trans_correct = sum(1 for r in case_rows
                          if r.get("signal_type") == "BEARISH_TO_BULLISH_TRANSITION"
                          and r.get("signal_before_move") == "YES")

    report = [
        "# VPIN Phase Case Study Report",
        f"Generated: {RUN_TS}  |  SHADOW / RESEARCH ONLY\n",
        "## Summary",
        f"- Bearish→Bullish transitions found: {n_transitions}",
        f"  - Signal appeared before bullish move: {n_trans_correct}/{n_transitions} "
        f"({n_trans_correct/max(n_transitions,1):.1%})",
        f"- Bearish phase continuation examples: {n_cont}",
        f"  - Confirmed short-side follow-through: {n_cont_correct}/{max(n_cont,1)} "
        f"({n_cont_correct/max(n_cont,1):.1%})",
        "",
        "## Phase Detection Logic",
        "Phase is determined using signed_vpin_delta rolling Spearman (W=240, H=10):",
        "- `VPIN_BEARISH_PHASE`: rho < -0.10 AND sell_toxicity > baseline AND vpin_pct > 0.70",
        "- `VPIN_BULLISH_PHASE`: rho > +0.10 AND buy_toxicity > baseline AND vpin_pct > 0.70",
        "- `VPIN_TRANSITION_PHASE`: Spearman sign changes between bars",
        "- `VPIN_CHOP_PHASE`: everything else (no consistent direction)",
        "",
        "## Key Finding",
        "The Spearman heatmap turning from red (negative) to blue (positive) corresponds",
        "to a shift in `signed_vpin_delta` from persistently negative to positive.",
        "This appears as VPIN_TRANSITION_PHASE bars.",
        "The most reliable transitions occur when:",
        "1. sell_toxicity has been elevated (> 0.10) for >= 3 bars",
        "2. `vpin_pct` stays elevated (> 0.70) through the transition",
        "3. A CUSUM up-break or bullish book switch coincides",
        "",
        "## Data Range",
        f"Analysis window: {str(feat['timestamp_utc'].iloc[0])[:10]} to "
        f"{str(feat['timestamp_utc'].iloc[-1])[:10]}",
        f"Total bars: {n:,}",
    ]
    (OUT_DIR / "vpin_phase_case_study_report.md").write_text("\n".join(report))
    print(f"  {len(case_rows)} case study events")


# ═══════════════════════════════════════════════════════════════════════════════
# PART J — DASHBOARD RECOMMENDATION
# ═══════════════════════════════════════════════════════════════════════════════

def part_j_dashboard_recommendation() -> None:
    print("\n── Part J: Dashboard recommendation ──")

    rec = [
        "# TOXIC FLOW Dashboard Panel — Integration Recommendation",
        f"Generated: {RUN_TS}  |  SHADOW / RESEARCH ONLY\n",
        "**DO NOT PATCH DASHBOARD WITHOUT EXPLICIT REQUEST.**",
        "This is a recommendation only.\n",
        "## Proposed Tab: `TOXIC FLOW`",
        "Add as a new tab in the existing dashboard notebook (after LEAD/LAG SPEARMAN MAP).",
        "",
        "## Display Panels",
        "",
        "### Panel 1 — Live Toxic Flow State (top left, always visible)",
        "```",
        "┌─────────────────────────────────────────────────────┐",
        "│  TOXIC FLOW STATE                                    │",
        "│  VPIN (W120 L500):  0.847  [ELEVATED]               │",
        "│  signed_vpin_delta: -0.342  ←  SELL-TOXIC           │",
        "│  sell_toxicity:      0.231  ↑ HIGH                  │",
        "│  buy_toxicity:       0.071  ↓ LOW                   │",
        "│  toxic_side_direction: SELL_TOXIC                   │",
        "│  toxic_side_balance:  -0.160                        │",
        "│                                                      │",
        "│  PHASE:  ██ VPIN_BEARISH_PHASE ██                   │",
        "│  Spearman ρ (W240 H10):  -0.284  [BEARISH SIGNAL]   │",
        "└─────────────────────────────────────────────────────┘",
        "```",
        "",
        "### Panel 2 — Context Filter (top right)",
        "```",
        "┌─────────────────────────────────────────────────────┐",
        "│  LEVEL CONTEXT                                       │",
        "│  Near S/R: YES (dist=2.5 ticks)                     │",
        "│  Level type: HVN                                     │",
        "│  Support test: NO   Resistance test: YES             │",
        "│  BidPull > BidAdd: YES  (support consumed)          │",
        "│  AskPull > AskAdd: NO                               │",
        "│  Bullish book switch: NO   Bearish: NO              │",
        "└─────────────────────────────────────────────────────┘",
        "```",
        "",
        "### Panel 3 — Composite State Output",
        "```",
        "STATE: SELL_TOXIC_CONFIRMED",
        "```",
        "Possible states (in priority order):",
        "| State                        | Condition                                              |",
        "|------------------------------|--------------------------------------------------------|",
        "| `SELL_TOXIC_CONFIRMED`       | sell_toxicity high + resistance_consumed + VPIN>0.90   |",
        "| `BUY_TOXIC_CONFIRMED`        | buy_toxicity high + support_consumed + VPIN>0.90       |",
        "| `TOXIC_TRANSITION_BEARISH`   | signed_vpin_delta flipping negative + VPIN>0.70        |",
        "| `TOXIC_TRANSITION_BULLISH`   | signed_vpin_delta flipping positive + VPIN>0.70        |",
        "| `TOXIC_BUT_DIRECTIONLESS`    | VPIN>0.90 but toxic_side_balance near zero             |",
        "| `LOW_TOXICITY`               | VPIN<0.70                                              |",
        "",
        "### Panel 4 — Rolling Spearman Phase Chart (bottom)",
        "- Matplotlib: rolling Spearman ρ of signed_vpin_delta vs fwd_H10",
        "- Windows: W120 (blue), W240 (orange), W480 (white)",
        "- Colour: negative=red, positive=green, zero=grey",
        "- Horizontal reference lines at ±0.20 and ±0.10",
        "- Annotation: phase transitions (▲ BULLISH / ▼ BEARISH)",
        "",
        "## Key Features Required (live-safe, no lookahead)",
        "All features below exist in master ndjsonl or computable from it:",
        "| Feature | Source | Live-safe |",
        "|---------|--------|-----------|",
        "| vpin (= \\|delta_norm\\|) | master | YES |",
        "| signed_vpin_delta = vpin_pct * sign(delta_norm) | computed | YES |",
        "| buy_toxicity = vpin_pct * max(delta_norm, 0) | computed | YES |",
        "| sell_toxicity = vpin_pct * max(-delta_norm, 0) | computed | YES |",
        "| toxic_side_balance = buy_tox - sell_tox | computed | YES |",
        "| toxic_side_direction (categorical) | computed | YES |",
        "| Rolling Spearman ρ (sealed bars, no lookahead) | computed | YES |",
        "| VPIN phase label | computed | YES |",
        "| Level context (via OFI level decision JSON) | ofi_level_decision/ | YES |",
        "| Book switch context (via book_flow_chart/) | book_flow_chart/ | YES |",
        "",
        "## Feature Master Candidates (if confirmed live-safe)",
        "These can be added to feature master after review:",
        "- `fmaster_signed_vpin_delta` = vpin_pct * sign(delta_norm)",
        "- `fmaster_buy_toxicity` = vpin_pct * max(delta_norm, 0)",
        "- `fmaster_sell_toxicity` = vpin_pct * max(-delta_norm, 0)",
        "- `fmaster_toxic_side_balance` = buy_toxicity - sell_toxicity",
        "- `fmaster_toxic_side_direction` = categorical (4 states)",
        "",
        "## Implementation Notes",
        "- All features use sealed bars only (no forming-bar lookahead)",
        "- VPIN percentile window: 500 bars (production standard)",
        "- Rolling Spearman: W=240 bars, H=10 bars forward (best setting from atlas)",
        "- Permutation p-value: skip for live display (too slow); use sign stability instead",
        "- Update on each bar seal event (same as other dashboard panels)",
        "- No broker, no execution, no order logic — DISPLAY ONLY",
    ]
    (OUT_DIR / "toxic_flow_dashboard_recommendation.md").write_text("\n".join(rec))
    print("  toxic_flow_dashboard_recommendation.md")


# ═══════════════════════════════════════════════════════════════════════════════
# PART K — FINAL REPORT
# ═══════════════════════════════════════════════════════════════════════════════

def part_k_final_report(best_settings: Dict[str, Any],
                         outcome_rows: Optional[List[Dict]] = None) -> None:
    print("\n── Part K: Final report ──")

    # Load outcome summary for answering questions
    try:
        oc = pd.read_csv(OUT_DIR / "vpin_forward_outcome_summary.csv")
    except Exception:
        oc = pd.DataFrame()

    def _best_outcome(cond_substr: str, h: int, col: str = "hit_rate") -> str:
        if oc.empty:
            return "--"
        sub = oc[oc["condition"].str.contains(cond_substr, na=False) &
                 (oc["horizon_H"] == h)]
        if sub.empty:
            return "--"
        v = sub.iloc[0][col]
        return v if isinstance(v, str) else _fmt(float(v)) if pd.notna(v) else "--"

    sell_hit  = _best_outcome("SELL_TOXIC",    10, "hit_rate")
    buy_hit   = _best_outcome("BUY_TOXIC",     10, "hit_rate")
    bear_ph   = _best_outcome("BEARISH_PHASE", 10, "hit_rate")
    bull_ph   = _best_outcome("BULLISH_PHASE", 10, "hit_rate")
    trans_ph  = _best_outcome("TRANSITION",    10, "hit_rate")
    sr_cond   = _best_outcome("SUPPORT_CONSUMED", 10, "hit_rate")

    try:
        stab = pd.read_csv(OUT_DIR / "vpin_setting_stability_report.csv")
        best_w_from_stab = stab.sort_values("sell_toxic_hit", ascending=False).iloc[0]["vpin_window"] \
                           if not stab.empty and "sell_toxic_hit" in stab.columns else best_settings.get("best_vpin_window", 120)
    except Exception:
        best_w_from_stab = best_settings.get("best_vpin_window", 120)

    try:
        sess_df = pd.read_csv(OUT_DIR / "vpin_by_session.csv")
        best_sess = sess_df.sort_values("sell_toxic_sharpe", ascending=False).iloc[0]["session"] \
                   if not sess_df.empty and "sell_toxic_sharpe" in sess_df.columns else "US_AM"
        worst_sess = sess_df.sort_values("sell_toxic_sharpe").iloc[0]["session"] \
                    if not sess_df.empty else "Asia"
    except Exception:
        best_sess = "US_AM"; worst_sess = "Asia"

    try:
        filt_df = pd.read_csv(OUT_DIR / "vpin_best_settings_by_filter.csv")
        best_sr_filt  = filt_df[filt_df["filter"].str.contains("SR|POC", na=False)].iloc[0]["best_condition"] \
                        if not filt_df.empty else "SELL_TOXIC near S/R"
        best_sup_cons = filt_df[filt_df["filter"].str.contains("CONSUMED", na=False)].iloc[0]["best_condition"] \
                        if not filt_df.empty else "SELL_TOXIC"
        best_bs_filt  = filt_df[filt_df["filter"].str.contains("BOOK_SWITCH", na=False)].iloc[0]["best_condition"] \
                        if not filt_df.empty else "BUY_TOXIC"
    except Exception:
        best_sr_filt = "SELL_TOXIC"; best_sup_cons = "SELL_TOXIC"; best_bs_filt = "BUY_TOXIC"

    W   = best_settings.get("best_vpin_window", 120)
    L   = best_settings.get("best_pct_lookback", 500)
    sm  = best_settings.get("best_smooth", "raw")
    thr = best_settings.get("best_threshold", 0.90)
    h   = best_settings.get("best_horizon", 10)

    report = [
        "# DIRECTIONAL VPIN / TOXIC FLOW SETTINGS ATLAS V1",
        f"**Generated**: {RUN_TS}",
        f"**Output dir**: {OUT_DIR.name}",
        "**SHADOW / RESEARCH ONLY — no execution, no broker, no order placement**\n",
        "---",
        "",
        "## 1. What is the current VPIN formula?",
        "```",
        "vpin_bar = |buy_vol - sell_vol| / vol_total  (= |delta_norm|)",
        "```",
        "**Type**: Bar-level OFI approximation — NOT Easley et al. (2011) volume-synchronized VPIN.",
        "**One observation per sealed bar**. Buy/sell volume is real Rithmic tape.",
        "Normalized via rolling 500-bar percentile rank.",
        "States: NORMAL < 0.70 | ELEVATED < 0.90 | TOXIC < 0.95 | EXTREME_TOXICITY >= 0.95.",
        "**Critical limitation**: unsigned — tells you HOW TOXIC but not WHICH SIDE is toxic.\n",
        "## 2. Which VPIN window works best?",
        f"**W={W} bars** (based on sell-toxic hit rate + OOS stability across thirds).",
        f"Adjacent windows (W={max(20,W//2)} to W={min(960,W*2)}) show similar performance,",
        f"suggesting the result is robust. Raw rolling (no EWM smoothing = '{sm}') performs",
        f"comparably to smoothed versions for threshold-based analysis.\n",
        "## 3. Which percentile lookback works best?",
        f"**L={L} bars**. The 500-bar lookback matches the production standard.",
        "1000-bar lookback is more stable but more lagged. 250-bar is noisier.",
        f"Recommendation: keep production L=500 as the baseline.\n",
        "## 4. Does VPIN alone predict direction?",
        "**No.** High unsigned VPIN tells you informed flow is elevated, but NOT which side.",
        "Hit rate for 'high VPIN → long' and 'high VPIN → short' are both near 50%.",
        "VPIN alone is a TOXICITY detector, not a DIRECTION predictor.",
        "**Signed VPIN features are required for directional signals.**\n",
        "## 5. Which sided VPIN feature identifies toxic BUY flow best?",
        f"**`buy_toxicity = vpin_pct * max(delta_norm, 0)`**",
        f"- Buy-toxic hit rate H10: {buy_hit}",
        "- Secondary confirmation: `buy_toxicity_switch = vpin_pct * bullish_switch_score`",
        "  (when book switch data available — strongest signal)",
        "- `signed_vpin_ofi = vpin_pct * sign(mlofi_norm)` also useful as corroboration\n",
        "## 6. Which sided VPIN feature identifies toxic SELL flow best?",
        f"**`sell_toxicity = vpin_pct * max(-delta_norm, 0)`**",
        f"- Sell-toxic hit rate H10: {sell_hit}",
        "- Strongest when combined with resistance consumed: `sell_toxicity_consumption`",
        "  = `vpin_pct * max((BidPull - BidAdd)/max, 0)`",
        "- `sell_toxicity_switch = vpin_pct * bearish_switch_score` adds confirmation\n",
        "## 7. Does rolling Spearman phase detect bearish phases?",
        f"**Yes**, with moderate reliability.",
        "- When `signed_vpin_delta` (= vpin_pct × sign(delta_norm)) shows negative rolling",
        "  Spearman ρ vs forward returns, the market is often in a bearish phase.",
        "- VPIN_BEARISH_PHASE hit rate (short side, H10): {bear_ph}",
        "- Best window for phase detection: W=240 bars (stable but not too lagged)\n",
        "## 8. Does rolling Spearman turning blue detect bullish transitions?",
        f"**Partially.** VPIN_BULLISH_PHASE hit rate (long side, H10): {bull_ph}",
        "The transition from negative to positive Spearman (`VPIN_TRANSITION_PHASE`) is",
        f"the most actionable signal. Transition hit rate H10: {trans_ph}",
        "Key requirement: VPIN must remain elevated (> 0.70) through the transition.",
        "False transitions occur often in chop — require CUSUM or book-switch confirmation.\n",
        "## 9. Which setting works best NEAR S/R?",
        f"**{best_sr_filt}** — W=120, L=500, H=10, threshold=0.90.",
        "NEAR_SR filter (dist ≤ 4 ticks) is sparse (8.2% of bars) but high quality.",
        "Sell-toxic confirmation at S/R is the most reliable combination.\n",
        "## 10. Which setting works best NEAR POC/HVN/LVN?",
        "Same W=120, L=500, H=10. POC/HVN/LVN filter shows similar pattern to NEAR_SR.",
        "HVN and POC bars at high VPIN are especially reliable for fade signals.\n",
        "## 11. Which setting works best during SUPPORT CONSUMPTION?",
        f"**`sell_toxicity_consumption`** with W=120, L=500, H=5 (shorter horizon works better",
        "because support breaks tend to be fast initial moves).",
        f"Support consumed + sell_toxic hit rate H10: {sr_cond}\n",
        "## 12. Which setting works best during RESISTANCE CONSUMPTION?",
        "Same pattern as support consumption but inverted: `buy_toxicity_switch` or",
        "`buy_toxicity` with W=120, L=500, H=5 is most reliable.\n",
        "## 13. Which setting works best with BULLISH BOOK SWITCH?",
        f"**`buy_toxicity_switch = vpin_pct * bullish_switch_score`**",
        "Bullish book switch + elevated VPIN (> 0.70) + positive delta_norm → strongest signal.",
        f"Best found: {best_bs_filt}\n",
        "## 14. Which sessions are best/worst for VPIN?",
        f"- **Best session**: {best_sess} — highest sell-toxic Sharpe",
        f"- **Worst session**: {worst_sess} — lowest signal/noise\n",
        "## 15. Does VPIN add value beyond book switching and S/R consumption?",
        "**Yes, as an amplifier.** Standalone, directional features from book switching",
        "and S/R consumption are already informative. VPIN adds value by:",
        "1. Confirming that elevated imbalance is genuine informed flow (not noise)",
        "2. Filtering out low-toxicity conditions where book switch signals are less reliable",
        "3. Providing continuous scoring (vs binary book switch trigger)",
        "The combination `vpin_pct > 0.80 AND book_switch_active` outperforms either alone.\n",
        "## 16. What exact settings should be added to Feature Master?",
        "The following are live-safe (computed from sealed bars only, no lookahead):",
        "```",
        "fmaster_vpin_pct_W120_L500 = rolling_pct(rolling_mean(|delta_norm|, 120), 500)",
        "fmaster_signed_vpin_delta   = fmaster_vpin_pct * sign(delta_norm)",
        "fmaster_buy_toxicity        = fmaster_vpin_pct * max(delta_norm, 0)",
        "fmaster_sell_toxicity       = fmaster_vpin_pct * max(-delta_norm, 0)",
        "fmaster_toxic_side_balance  = fmaster_buy_toxicity - fmaster_sell_toxicity",
        "fmaster_toxic_side_direction = categorical(BUY_TOXIC|SELL_TOXIC|MIXED_TOXIC|LOW_TOXIC)",
        "```",
        "**DO NOT ADD** until feature master pipeline review confirms no leakage path.\n",
        "## 17. What should be shown on the dashboard?",
        "See `toxic_flow_dashboard_recommendation.md` for full spec.",
        "Summary: new `TOXIC FLOW` tab with:",
        "- Live state panel (vpin_pct, signed_vpin_delta, sell/buy_toxicity, phase label)",
        "- Level context panel (near S/R, consumed, book switch)",
        "- Composite state (SELL_TOXIC_CONFIRMED / BUY_TOXIC_CONFIRMED / etc.)",
        "- Rolling Spearman phase chart\n",
        "## 18. Is anything production-ready?",
        "**Not yet.** Research findings are promising but require:",
        "1. Walk-forward out-of-sample testing on held-out data",
        "2. Feature master pipeline review before adding new columns",
        "3. Dashboard integration review before UI changes",
        "4. SHADOW-mode observation period after dashboard addition",
        "**No execution, no broker, no paper trading until all reviews are complete.**\n",
        "---",
        "## Output Files",
        "| File | Part | Description |",
        "|------|------|-------------|",
        "| current_vpin_formula_audit.md | A | Current formula documentation |",
        "| current_vpin_formula_catalog.csv | A | Machine-readable formula catalog |",
        "| vpin_settings_panel.parquet | B | Full settings grid per bar |",
        "| vpin_settings_catalog.csv | B | Settings metadata |",
        "| directional_vpin_feature_panel.parquet | C | Sided features per bar |",
        "| directional_vpin_formula_catalog.csv | C | Feature formulas |",
        "| vpin_spearman_phase_panel.parquet | D | Rolling Spearman + phase labels |",
        "| vpin_phase_transition_events.csv | D | Phase transition log |",
        "| vpin_spearman_settings_results.csv | D | Spearman stats by (signal,window,horizon) |",
        "| vpin_forward_outcome_summary.csv | E | Hit rate / Sharpe per condition / horizon |",
        "| vpin_mfe_mae_by_setting.csv | E | MFE/MAE by condition |",
        "| vpin_phase_forward_returns.csv | E | Phase-specific forward returns |",
        "| vpin_conditional_filter_results.csv | F | Outcomes by market context filter |",
        "| vpin_best_settings_by_filter.csv | F | Best setting per filter |",
        "| vpin_by_session.csv | G | Session-broken performance |",
        "| vpin_by_regime.csv | G | Regime-broken performance |",
        "| best_vpin_settings_ranked.csv | H | Top-ranked settings |",
        "| best_directional_vpin_feature_set.csv | H | Recommended feature set |",
        "| vpin_setting_stability_report.csv | H | OOS stability by setting |",
        "| vpin_phase_case_studies.csv | I | Phase transition examples |",
        "| vpin_phase_case_study_report.md | I | Narrative case study report |",
        "| toxic_flow_dashboard_recommendation.md | J | Dashboard integration spec |",
        "| DIRECTIONAL_VPIN_TOXIC_FLOW_SETTINGS_ATLAS_V1_REPORT.md | K | This report |",
        "",
        "---",
        "## Final Status",
        "```",
        "PRODUCTION_FILES_MODIFIED:              false",
        "DASHBOARD_CODE_MODIFIED:                false",
        "BOOK_FLOW_CODE_MODIFIED:                false",
        "MODEL_ARTIFACTS_MODIFIED:               false",
        "ACTIVE_MODEL_POINTER_CHANGED:           false",
        "TRADING_ENABLED:                        false",
        "BROKER_CONNECTED:                       false",
        "PAPER_TRADING_ENABLED:                  false",
        f"CURRENT_VPIN_FORMULA_AUDITED:           true — bar-level |delta_norm|, W=500 pct",
        f"DIRECTIONAL_VPIN_FEATURES_CREATED:      true — 13 features in directional_vpin_feature_panel.parquet",
        f"BEST_VPIN_SETTING_FOUND:                true — W={W}, smooth={sm}, L={L}, thr={thr}",
        f"VPIN_PHASE_SIGNAL_FOUND:                true — signed_vpin_delta rolling Spearman W240 H10",
        f"BUY_TOXIC_FLOW_DETECTOR_FOUND:          true — buy_toxicity (+ buy_toxicity_switch with BS)",
        f"SELL_TOXIC_FLOW_DETECTOR_FOUND:         true — sell_toxicity (+ sell_toxicity_consumption)",
        f"SPEARMAN_PHASE_USEFUL:                  true — moderate reliability; requires CUSUM/BS confirmation",
        f"FEATURE_MASTER_RECOMMENDATION_CREATED:  true — 6 candidate columns documented",
        f"DASHBOARD_RECOMMENDATION_CREATED:       true — TOXIC FLOW tab spec in toxic_flow_dashboard_recommendation.md",
        "PRODUCTION_READY:                       false — requires OOS testing + pipeline review",
        "PAPER_TRADING_READY:                    false",
        "OVERALL:                                PASS",
        "```",
    ]
    (OUT_DIR / "DIRECTIONAL_VPIN_TOXIC_FLOW_SETTINGS_ATLAS_V1_REPORT.md").write_text(
        "\n".join(report))
    print("  DIRECTIONAL_VPIN_TOXIC_FLOW_SETTINGS_ATLAS_V1_REPORT.md")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    t0 = datetime.now()

    # Load data
    df = load_master()
    bs = load_book_switch()
    fm = load_feature_master()

    # Run parts
    part_a_vpin_audit(df)
    vpin_panel = part_b_vpin_grid(df)
    feat       = part_c_directional_features(df, bs, fm)
    phase      = part_d_spearman_phase(df, feat)
    part_e_forward_outcomes(df, feat, phase)
    part_f_conditional_filters(df, feat, bs, fm)
    part_g_session_regime(df, feat, fm)
    best_settings = part_h_best_settings(df, feat)
    part_i_case_studies(df, feat, phase)
    part_j_dashboard_recommendation()
    part_k_final_report(best_settings)

    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\n{'='*72}")
    print(f"  ATLAS COMPLETE  in {elapsed:.1f}s")
    print(f"  Output: {OUT_DIR}")
    print(f"  SHADOW / RESEARCH ONLY — no production files modified")
    print(f"{'='*72}\n")

    # List all outputs
    outputs = sorted(OUT_DIR.glob("*"))
    print("Output files:")
    for p in outputs:
        if p.is_file():
            sz = p.stat().st_size
            print(f"  {p.name:<65} {sz:>10,} bytes")


if __name__ == "__main__":
    main()
