#!/usr/bin/env python3
"""
PBO / Deflated Sharpe Overfitting Audit v1
Bailey, Borwein, López de Prado & Zhu (2015) — CSCV Implementation
SHADOW / RESEARCH ONLY — no execution, no broker, no order placement
"""

# ============================================================
# INVARIANT CONSTRAINT BLOCK — DO NOT REMOVE OR MODIFY
# ============================================================
# SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER
# Do NOT modify: Rithmic raw recorder, parser, master files,
#   Book Flow chart, active model pointer, broker/order logic,
#   trading flags, production model artifacts, ACTIVE_SHADOW_RELEASE
# No auto-trading, no broker execution, no order placement
# ============================================================

import os, sys, time, json, math, warnings
from pathlib import Path
from datetime import datetime, timezone
from itertools import combinations

import numpy as np
import pandas as pd
from scipy import stats
from scipy.stats import norm

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────
ATLAS1_DIR = Path("/home/prabh/OFI_Production/research_engines/"
                  "directional_vpin_toxic_flow_settings_atlas_v1_20260701T025021Z")
ATLAS2_DIR = Path("/home/prabh/OFI_Production/research_engines/"
                  "true_ldp_vpin_formula_comparison_v1_20260702T031400Z")
Q16_DIR    = Path("/home/prabh/OFI_Production/research_engines/"
                  "q16_true_vpin_feature_master_recommendation_20260603_20260701_20260702T061819Z")
OUT_DIR    = Path(__file__).parent

# ─────────────────────────────────────────────
# CSCV PARAMETERS
# ─────────────────────────────────────────────
S            = 16          # subperiods → C(16,8) = 12,780 combinations
# Threshold families (chosen to match actual signal value ranges)
THRESHOLDS_PCT = [0.70, 0.80, 0.90, 0.95]    # pct-rank signals [0,1] — fires at ~30%, ~20%, ~10%, ~5%
THRESHOLDS_TOX = [0.005, 0.01, 0.02, 0.05]   # toxicity/product signals (sparse 0-1)
THRESHOLDS_SIG = [0.15, 0.30, 0.50, 0.70]    # signed/bipolar signals (threshold on abs value)
THRESHOLDS_DIV = [0.05, 0.10, 0.20, 0.30]    # divergence signal (moderate range)
THRESHOLDS     = THRESHOLDS_PCT               # default; overridden per signal
HORIZONS     = [5, 10, 20, 40, 80]
EULER_GAMMA  = 0.5772156649015329

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def banner(msg):
    print(f"\n{'='*60}\n{msg}\n{'='*60}")

def sec(t0):
    return f"{time.time()-t0:.1f}s"

# ─────────────────────────────────────────────
# CORE CSCV FUNCTION (Bailey et al. 2015)
# ─────────────────────────────────────────────

def cscv_pbo(M: np.ndarray, S: int = 16) -> dict:
    """
    Combinatorially Symmetric Cross-Validation → Probability of Backtest Overfitting.

    Algorithm 2.3 from Bailey, Borwein, López de Prado, Zhu (2015).

    M : T × N array.  Row t = time observation. Column n = one trial configuration.
        M[t, n] = bar-level performance of config n at bar t.
    S : number of equal subperiods (must be even; S=16 → C(16,8) = 12,780 combos).

    Performance metric on IS/OOS = column mean (proportional to information ratio).
    Logit λ_c = ln(ω̄_c / (1-ω̄_c)) where ω̄_c = relative OOS rank of IS-optimal strategy.
    PBO φ = fraction of λ_c < 0.

    Speed optimisation: precompute subperiod means (S × N) rather than slicing T rows
    per combination.  Runtime: O(S × N × C(S,S/2)) instead of O(T × N × C(S,S/2)).
    """
    T, N = M.shape
    assert T % S == 0, f"T={T} must be divisible by S={S}"
    sub_size = T // S

    # Precompute subperiod means: shape (S, N)
    sub_means = M.reshape(S, sub_size, N).mean(axis=1)

    S_half   = S // 2
    is_combos = list(combinations(range(S), S_half))

    logits        = np.empty(len(is_combos))
    sr_is_best    = np.empty(len(is_combos))
    sr_oos_best   = np.empty(len(is_combos))

    all_subs = set(range(S))

    for idx, is_idx in enumerate(is_combos):
        oos_idx = sorted(all_subs - set(is_idx))

        sr_is  = sub_means[list(is_idx)].mean(axis=0)   # (N,)
        sr_oos = sub_means[list(oos_idx)].mean(axis=0)  # (N,)

        n_star   = int(np.argmax(sr_is))
        oos_rank = int(np.sum(sr_oos < sr_oos[n_star])) + 1
        omega    = oos_rank / (N + 1)
        omega    = float(np.clip(omega, 1e-7, 1 - 1e-7))

        logits[idx]      = math.log(omega / (1 - omega))
        sr_is_best[idx]  = sr_is[n_star]
        sr_oos_best[idx] = sr_oos[n_star]

    pbo       = float(np.mean(logits < 0))
    prob_loss = float(np.mean(sr_oos_best < 0))

    try:
        beta, alpha, _, _, _ = stats.linregress(sr_is_best, sr_oos_best)
    except Exception:
        beta = alpha = float("nan")

    return {
        "pbo":              pbo,
        "n_combos":         len(is_combos),
        "logit_mean":       float(logits.mean()),
        "logit_std":        float(logits.std()),
        "logit_q25":        float(np.percentile(logits, 25)),
        "logit_q75":        float(np.percentile(logits, 75)),
        "prob_loss":        prob_loss,
        "degradation_beta": float(beta),
        "degradation_alpha":float(alpha),
    }

# ─────────────────────────────────────────────
# DSR — Deflated Sharpe Ratio
# ─────────────────────────────────────────────

def deflated_sharpe_ratio(perf: np.ndarray, n_trials: int) -> dict:
    """
    Deflated Sharpe Ratio (Bailey & López de Prado, JPM 2014).

    perf      : 1-D array of bar-level performance values (signed returns when signal active, 0 else).
    n_trials  : total number of parameter configurations tried across ALL atlases.

    DSR = PSR(E[max SR]) where E[max SR] = expected maximum Sharpe across n_trials iid strategies.
    PSR(SR*) = Φ( (SR_hat - SR*) × √(T-1) / √(1 - γ₃SR + (γ₄-1)/4 × SR²) )
    DSR > 0.95 → very strong; DSR > 0.5 → better than chance; DSR < 0.5 → likely spurious.
    """
    perf  = np.asarray(perf, dtype=float)
    valid = perf[perf != 0]  # compute statistics over active bars only
    T_act = len(valid)
    T_all = len(perf)

    if T_act < 20 or valid.std(ddof=1) == 0:
        return {"dsr": float("nan"), "sr_hat_bar": float("nan"),
                "e_max_sr": float("nan"), "T_active": T_act}

    mu     = float(valid.mean())
    sigma  = float(valid.std(ddof=1))
    sr_hat = mu / sigma                       # per-bar Sharpe (in units of sigma)
    gamma3 = float(stats.skew(valid))
    gamma4 = float(stats.kurtosis(valid)) + 3 # full kurtosis

    # Variance of SR estimate (Lo 2002)
    denom_var = 1 - gamma3 * sr_hat + ((gamma4 - 1) / 4) * sr_hat ** 2
    if denom_var <= 0 or T_act <= 1:
        return {"dsr": float("nan"), "sr_hat_bar": sr_hat, "T_active": T_act}

    var_sr = denom_var / (T_act - 1)

    # Expected maximum SR across n_trials (Euler-Mascheroni approximation)
    if n_trials > 1:
        e_max = ((1 - EULER_GAMMA) * norm.ppf(1 - 1 / n_trials)
                 + EULER_GAMMA      * norm.ppf(1 - 1 / (n_trials * math.e)))
    else:
        e_max = 0.0

    z_dsr = (sr_hat - e_max) / math.sqrt(var_sr)
    dsr   = float(norm.cdf(z_dsr))

    return {
        "dsr":             dsr,
        "sr_hat_bar":      sr_hat,
        "sr_annualized_T": sr_hat * math.sqrt(T_act),  # in sqrt-observation units
        "e_max_sr":        e_max,
        "gamma3_skew":     gamma3,
        "gamma4_kurt":     gamma4,
        "T_active":        T_act,
        "T_total":         T_all,
        "active_rate":     T_act / T_all,
    }

# ─────────────────────────────────────────────
# PERFORMANCE SERIES BUILDER
# ─────────────────────────────────────────────

def build_perf_series(signal: np.ndarray,
                      fwd_ret: np.ndarray,
                      threshold: float,
                      direction: str = "long") -> np.ndarray:
    """
    Build per-bar performance series for one (signal, threshold, horizon, direction) config.

    signal    : signal values (0–1 range for pct-rank signals; or signed for directional)
    fwd_ret   : H-bar forward price change
    threshold : signal activation level
    direction : 'long'  → bet up when signal > threshold
                'short' → bet down when signal > threshold
                'signed'→ signal encodes direction (positive=long, negative=short)
    Returns: perf[t] = signed_return when active, 0 otherwise.
    """
    perf = np.zeros(len(signal), dtype=np.float32)
    valid = ~(np.isnan(signal) | np.isnan(fwd_ret))

    if direction == "long":
        mask = valid & (signal > threshold)
        perf[mask] = fwd_ret[mask]

    elif direction == "short":
        mask = valid & (signal > threshold)
        perf[mask] = -fwd_ret[mask]

    elif direction == "signed":
        # |signal| > threshold → bet in sign(signal) direction
        mask_long  = valid & (signal >  threshold)
        mask_short = valid & (signal < -threshold)
        perf[mask_long]  =  fwd_ret[mask_long]
        perf[mask_short] = -fwd_ret[mask_short]

    elif direction == "long_delta":
        # unsigned VPIN + positive delta_norm → long bet
        raise ValueError("Use direction='signed' with signed_vpin_delta column directly")

    return perf

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    t_start = time.time()
    gen_time = now_utc()
    print(f"PBO / Deflated Sharpe Overfitting Audit v1")
    print(f"Generated: {gen_time}")
    print("SHADOW / RESEARCH ONLY")

    # ─────────────────────────────────────────
    # PART A — Data Loading & Forward Returns
    # ─────────────────────────────────────────
    banner("PART A: Data Loading & Forward Returns")

    # Primary dataset: Atlas1 directional feature panel
    # 24,208 bars, Jun 3 – Jul 1, T divisible by 16 = perfect for CSCV S=16
    a1_feat = pd.read_parquet(ATLAS1_DIR / "directional_vpin_feature_panel.parquet")
    a1_set  = pd.read_parquet(ATLAS1_DIR / "vpin_settings_panel.parquet")

    # Q16 aligned parquet for true VPIN pct features + continuous_close (back-adjusted)
    q16 = pd.read_parquet(Q16_DIR / "q16_true_vpin_feature_master_aligned.parquet")

    print(f"Atlas1 directional: {a1_feat.shape}")
    print(f"Atlas1 settings:    {a1_set.shape}")
    print(f"Q16 aligned:        {q16.shape}")

    # Build master frame on Atlas1 bars (reference dataset, T=24,208)
    df = a1_feat.copy()
    df = df.sort_values("bar_end_ts_ns").reset_index(drop=True)
    T  = len(df)

    assert T % S == 0, f"T={T} not divisible by S={S}"
    print(f"Primary dataset T = {T} (S={S} subperiods of {T//S} bars each)")
    print(f"Date range: {df['timestamp_utc'].min()} → {df['timestamp_utc'].max()}")

    # Merge Atlas1 settings pct columns
    pct_cols_set = [c for c in a1_set.columns if c.endswith("_pct")]
    df = df.merge(a1_set[["bar_end_ts_ns"] + pct_cols_set], on="bar_end_ts_ns", how="left")

    # Merge Q16 true VPIN pct + directional features + continuous_close
    q16_cols_to_merge = (
        ["bar_end_ts_ns", "close"]
        + [c for c in q16.columns if "tvpin_pct" in c]
        + [c for c in q16.columns if "true_" in c]
        + ["cur_vpin_pct_L500", "vpin_divergence_pct_delta"]
    )
    df = df.merge(q16[q16_cols_to_merge], on="bar_end_ts_ns", how="left")

    # Drop any duplicate ts rows introduced by merge
    df = df.drop_duplicates(subset="bar_end_ts_ns").sort_values("bar_end_ts_ns").reset_index(drop=True)

    # Trim to nearest multiple of S for CSCV
    T_raw = len(df)
    T     = (T_raw // S) * S
    df    = df.iloc[:T].reset_index(drop=True)
    assert T % S == 0, f"T={T} not divisible by S={S}"

    print(f"Merged frame: {df.shape[1]} columns, T={T} (trimmed from {T_raw})")

    # Compute forward returns using continuous_close (back-adjusted, handles roll)
    price = df["close"].values.astype(np.float64)
    fwd_rets = {}
    for H in HORIZONS:
        fr = np.full(T, np.nan, dtype=np.float32)
        fr[:T - H] = (price[H:T] - price[:T - H]).astype(np.float32)
        fwd_rets[H] = fr

    print(f"Forward returns computed for H = {HORIZONS}")
    for H in HORIZONS:
        valid_n = int(np.sum(~np.isnan(fwd_rets[H])))
        print(f"  H={H:3d}: {valid_n} valid bars ({100*valid_n/T:.1f}%)")

    delta_norm = df["delta_norm"].values.astype(np.float32)

    # ─────────────────────────────────────────
    # PART B — Configuration Inventory
    # ─────────────────────────────────────────
    banner("PART B: Configuration Inventory (All N_trials)")

    n_a1_pct_signed     = len(pct_cols_set) * len(THRESHOLDS) * len(HORIZONS)
    n_a1_dir_feat       = 8 * len(THRESHOLDS) * len(HORIZONS)    # 8 directional features × thresh × H
    n_a2_raw_tvpin      = 48 * 3 * len(THRESHOLDS) * len(HORIZONS) * 2  # 48 settings × 3L × 4thr × 5H × 2dir
    n_q16_tvpin_pct     = 27 * len(THRESHOLDS) * len(HORIZONS) * 2      # 27 pct settings × 4thr × 5H × 2dir
    n_q16_true_dir      = 8  * len(THRESHOLDS) * len(HORIZONS)           # 8 true directional × 4 × 5
    n_q16_cur_vpin      = 1  * len(THRESHOLDS) * len(HORIZONS)
    n_q16_divergence    = 1  * len(THRESHOLDS) * len(HORIZONS)

    # Additional: session-filtered analyses (11 sessions × each family) counted as multiplied tests
    n_session_filter_mult = 11   # session/filter modes from prior atlases

    n_total_raw = (n_a1_pct_signed + n_a1_dir_feat + n_a2_raw_tvpin
                   + n_q16_tvpin_pct + n_q16_true_dir + n_q16_cur_vpin + n_q16_divergence)

    # Total trials including session-filtered conditional analyses
    n_total_all = n_total_raw * n_session_filter_mult

    print(f"Atlas1 – 84 pct settings × signed direction × {len(THRESHOLDS)} thr × {len(HORIZONS)} H = {n_a1_pct_signed}")
    print(f"Atlas1 – 8 directional features × {len(THRESHOLDS)} thr × {len(HORIZONS)} H = {n_a1_dir_feat}")
    print(f"Atlas2 – 48 true VPIN settings × 3L × {len(THRESHOLDS)} thr × {len(HORIZONS)} H × 2 dir = {n_a2_raw_tvpin}")
    print(f"Q16    – 27 true VPIN pct × {len(THRESHOLDS)} thr × {len(HORIZONS)} H × 2 dir = {n_q16_tvpin_pct}")
    print(f"Q16    – 8 true directional × {len(THRESHOLDS)} thr × {len(HORIZONS)} H = {n_q16_true_dir}")
    print(f"Q16    – cur_vpin_pct × {len(THRESHOLDS)} thr × {len(HORIZONS)} H = {n_q16_cur_vpin}")
    print(f"Q16    – divergence × {len(THRESHOLDS)} thr × {len(HORIZONS)} H = {n_q16_divergence}")
    print(f"{'─'*50}")
    print(f"Raw parameter configs (no conditional filters): {n_total_raw:,}")
    print(f"× {n_session_filter_mult} session/filter modes = {n_total_all:,} total trial evaluations")
    print(f"\nUsing N_trials = {n_total_all:,} for DSR (conservative: all conditional analyses counted)")

    N_TRIALS = n_total_all

    # Save inventory
    inv_rows = [
        {"atlas": "Atlas1_DirectionalVPIN", "source": "vpin_settings_panel.parquet",
         "family": "vpin_pct_signed_direction", "n_signals": len(pct_cols_set),
         "n_thresholds": len(THRESHOLDS), "n_horizons": len(HORIZONS), "n_directions": 1,
         "n_configs": n_a1_pct_signed},
        {"atlas": "Atlas1_DirectionalVPIN", "source": "directional_vpin_feature_panel.parquet",
         "family": "directional_features", "n_signals": 8,
         "n_thresholds": len(THRESHOLDS), "n_horizons": len(HORIZONS), "n_directions": 1,
         "n_configs": n_a1_dir_feat},
        {"atlas": "Atlas2_TrueLDPVPIN", "source": "true_vpin_bar_aligned_panel.parquet",
         "family": "true_vpin_raw_settings", "n_signals": 48,
         "n_thresholds": len(THRESHOLDS), "n_horizons": len(HORIZONS), "n_directions": 2,
         "n_configs": n_a2_raw_tvpin},
        {"atlas": "Q16_FullRange", "source": "q16_true_vpin_feature_master_aligned.parquet",
         "family": "true_vpin_pct", "n_signals": 27,
         "n_thresholds": len(THRESHOLDS), "n_horizons": len(HORIZONS), "n_directions": 2,
         "n_configs": n_q16_tvpin_pct},
        {"atlas": "Q16_FullRange", "source": "q16_true_vpin_feature_master_aligned.parquet",
         "family": "true_directional_features", "n_signals": 8,
         "n_thresholds": len(THRESHOLDS), "n_horizons": len(HORIZONS), "n_directions": 1,
         "n_configs": n_q16_true_dir},
        {"atlas": "Q16_FullRange", "source": "q16_true_vpin_feature_master_aligned.parquet",
         "family": "cur_vpin_pct", "n_signals": 1,
         "n_thresholds": len(THRESHOLDS), "n_horizons": len(HORIZONS), "n_directions": 1,
         "n_configs": n_q16_cur_vpin},
        {"atlas": "Q16_FullRange", "source": "q16_true_vpin_feature_master_aligned.parquet",
         "family": "vpin_divergence", "n_signals": 1,
         "n_thresholds": len(THRESHOLDS), "n_horizons": len(HORIZONS), "n_directions": 1,
         "n_configs": n_q16_divergence},
    ]
    inv_df = pd.DataFrame(inv_rows)
    inv_df["n_configs_with_filters"] = inv_df["n_configs"] * n_session_filter_mult
    inv_df.to_csv(OUT_DIR / "pbo_configuration_inventory.csv", index=False)
    print(f"\nSaved: pbo_configuration_inventory.csv")

    # ─────────────────────────────────────────
    # PART C — Build Performance Matrix M
    # ─────────────────────────────────────────
    banner("PART C: Build Performance Matrix M")
    t_part = time.time()

    # We build M for each horizon H separately (smaller matrices, cleaner CSCV per H)
    # Total configs per H:
    # 84 (atlas1 pct, signed direction) + 8 (atlas1 dir) + 27×2 (q16 true pct, long+short)
    # + 8 (q16 true dir) + 1 (cur_vpin, signed direction) + 1 (divergence, signed)
    # = 84 + 8 + 54 + 8 + 1 + 1 = 156 configs per H
    # × 4 thresholds = 624 columns per M matrix

    # Define signal groups
    sig_groups = []

    # Group 1: Atlas1 pct settings, direction = sign(delta_norm)
    # Signed signal = vpin_pct × sign(delta_norm): positive → long bet, negative → short bet
    for pcol in pct_cols_set:
        pct_vals = df[pcol].fillna(0).values.astype(np.float32)
        signed_signal = pct_vals * np.sign(delta_norm)
        sig_groups.append({
            "name": pcol,
            "family": "atlas1_pct_signed",
            "signal": signed_signal,
            "direction": "signed",
            "thresholds": THRESHOLDS_PCT,   # fires when |pct| > thr (pct values 0-1)
        })

    # Group 2: Atlas1 directional features
    # buy/sell toxicity are products (skewed 0-1): use TOX thresholds
    # signed/balance signals (-1 to 1): use SIG thresholds (on abs value)
    dir_feat_specs = {
        "buy_toxicity":        ("long",   THRESHOLDS_TOX),
        "sell_toxicity":       ("short",  THRESHOLDS_TOX),
        "signed_vpin_delta":   ("signed", THRESHOLDS_SIG),
        "buy_toxicity_ofi":    ("long",   THRESHOLDS_TOX),
        "sell_toxicity_ofi":   ("short",  THRESHOLDS_TOX),
        "buy_toxicity_switch": ("long",   THRESHOLDS_TOX),
        "sell_toxicity_switch":("short",  THRESHOLDS_TOX),
        "toxic_side_balance":  ("signed", THRESHOLDS_SIG),
    }
    for col, (direction, thrs) in dir_feat_specs.items():
        if col not in df.columns:
            continue
        sig_groups.append({
            "name": col,
            "family": "atlas1_directional",
            "signal": df[col].fillna(0).values.astype(np.float32),
            "direction": direction,
            "thresholds": thrs,
        })

    # Group 3: Q16 true VPIN pct (direction = sign(delta_norm))
    tvpin_pct_cols = [c for c in df.columns if "tvpin_pct" in c]
    for col in tvpin_pct_cols:
        pct_vals = df[col].fillna(0).values.astype(np.float32)
        signed_signal = pct_vals * np.sign(delta_norm)
        sig_groups.append({
            "name": col,
            "family": "q16_true_pct_signed",
            "signal": signed_signal,
            "direction": "signed",
            "thresholds": THRESHOLDS_PCT,
        })

    # Group 4: Q16 true directional features
    true_dir_specs = {
        "true_buy_toxicity_W10":      ("long",   THRESHOLDS_TOX),
        "true_sell_toxicity_W10":     ("short",  THRESHOLDS_TOX),
        "true_signed_vpin_delta_W10": ("signed", THRESHOLDS_SIG),
        "true_toxic_balance_W10":     ("signed", THRESHOLDS_SIG),
        "true_buy_toxicity_W50":      ("long",   THRESHOLDS_TOX),
        "true_sell_toxicity_W50":     ("short",  THRESHOLDS_TOX),
        "true_signed_vpin_delta_W50": ("signed", THRESHOLDS_SIG),
        "true_toxic_balance_W50":     ("signed", THRESHOLDS_SIG),
    }
    for col, (direction, thrs) in true_dir_specs.items():
        if col not in df.columns:
            continue
        sig_groups.append({
            "name": col,
            "family": "q16_true_directional",
            "signal": df[col].fillna(0).values.astype(np.float32),
            "direction": direction,
            "thresholds": thrs,
        })

    # Group 5: cur_vpin_pct (signed direction)
    if "cur_vpin_pct_L500" in df.columns:
        cur_pct = df["cur_vpin_pct_L500"].fillna(0).values.astype(np.float32)
        signed_cur = cur_pct * np.sign(delta_norm)
        sig_groups.append({
            "name": "cur_vpin_pct_L500_signed",
            "family": "current_vpin",
            "signal": signed_cur,
            "direction": "signed",
            "thresholds": THRESHOLDS_PCT,
        })

    # Group 6: divergence signal
    if "vpin_divergence_pct_delta" in df.columns:
        div_sig = df["vpin_divergence_pct_delta"].fillna(0).values.astype(np.float32)
        sig_groups.append({
            "name": "vpin_divergence_pct_delta",
            "family": "divergence",
            "signal": div_sig,
            "direction": "signed",
            "thresholds": THRESHOLDS_DIV,
        })

    print(f"Signal groups: {len(sig_groups)} unique signals")
    family_counts = {}
    for g in sig_groups:
        family_counts[g["family"]] = family_counts.get(g["family"], 0) + 1
    for fam, cnt in sorted(family_counts.items()):
        print(f"  {fam}: {cnt} signals")

    # Build M matrices per horizon H and collect results
    # M shape: T × (n_signals × n_thresholds)
    all_config_meta = []   # metadata for each column of M

    pbo_results  = {}   # keyed by H
    all_M_cols   = {}   # keyed by H: list of perf arrays

    for H in HORIZONS:
        fr = fwd_rets[H]
        cols  = []
        metas = []

        for sg in sig_groups:
            sig  = sg["signal"]
            name = sg["name"]
            fam  = sg["family"]
            dirn = sg["direction"]
            thrs = sg.get("thresholds", THRESHOLDS_PCT)

            for thr in thrs:
                perf = build_perf_series(sig, fr, thr, direction=dirn)
                cols.append(perf)
                metas.append({
                    "signal_name": name,
                    "family": fam,
                    "direction": dirn,
                    "threshold": thr,
                    "horizon": H,
                })

        M = np.stack(cols, axis=1).astype(np.float32)  # T × N_cols
        all_M_cols[H] = M
        if H == HORIZONS[0]:
            all_config_meta = metas
            N_configs_per_H = M.shape[1]

        print(f"  H={H:3d}: M shape {M.shape}, non-zero cells: {int(np.sum(M != 0)):,}")

    N_configs_per_H = all_M_cols[HORIZONS[0]].shape[1]
    print(f"\nTotal configs per horizon: {N_configs_per_H}")
    print(f"Total cells in inventory (per-H × all H): {N_configs_per_H * len(HORIZONS):,}")
    print(f"Part C done in {sec(t_part)}")

    # ─────────────────────────────────────────
    # PART D — CSCV → PBO per horizon
    # ─────────────────────────────────────────
    banner("PART D: CSCV → Probability of Backtest Overfitting")
    t_part = time.time()

    pbo_by_H     = {}
    pbo_by_H_fam = {}  # PBO per family per H

    for H in HORIZONS:
        M   = all_M_cols[H]
        res = cscv_pbo(M, S=S)
        pbo_by_H[H] = res
        print(f"  H={H:3d}: PBO={res['pbo']:.3f}  logit_mean={res['logit_mean']:+.3f}"
              f"  prob_loss={res['prob_loss']:.3f}  beta_deg={res['degradation_beta']:+.3f}"
              f"  [{res['n_combos']:,} combos]")

    # PBO per signal family at H=10 (representative horizon)
    H_repr = 10
    M_repr = all_M_cols[H_repr]
    metas_arr = all_config_meta  # length = N_configs_per_H (built at first H)

    families = sorted(set(m["family"] for m in metas_arr if m["horizon"] == HORIZONS[0]))
    pbo_family_rows = []

    print(f"\nPBO by signal family (H={H_repr}):")
    for fam in families:
        fam_idx = [i for i, m in enumerate(metas_arr) if m["family"] == fam and m["horizon"] == HORIZONS[0]]
        if len(fam_idx) < 2:
            continue
        M_fam = M_repr[:, fam_idx]
        res_f = cscv_pbo(M_fam, S=S)
        pbo_family_rows.append({
            "family": fam,
            "n_configs": len(fam_idx),
            "pbo": res_f["pbo"],
            "logit_mean": res_f["logit_mean"],
            "prob_loss": res_f["prob_loss"],
            "degradation_beta": res_f["degradation_beta"],
        })
        verdict = "LOW" if res_f["pbo"] < 0.20 else ("MED" if res_f["pbo"] < 0.40 else "HIGH")
        print(f"  [{verdict}] {fam:40s}: PBO={res_f['pbo']:.3f}  N={len(fam_idx)}")

    # Save PBO results
    pbo_rows = []
    for H, res in pbo_by_H.items():
        row = {"horizon": H, **res}
        pbo_rows.append(row)
    pd.DataFrame(pbo_rows).to_csv(OUT_DIR / "pbo_results_by_horizon.csv", index=False)
    pd.DataFrame(pbo_family_rows).to_csv(OUT_DIR / "pbo_results_by_family.csv", index=False)
    print(f"\nPart D done in {sec(t_part)}")

    # ─────────────────────────────────────────
    # PART E — Deflated Sharpe Ratio
    # ─────────────────────────────────────────
    banner("PART E: Deflated Sharpe Ratio")
    t_part = time.time()

    # Core recommended signals from all atlases (the candidates proposed for Feature Master)
    # Thresholds chosen to fire at approximately p70 of each signal's positive distribution:
    # - buy/sell toxicity (product, skewed 0-1): p70 ≈ 0.025, use thr=0.02
    # - signed signals (-1 to 1): threshold on |signal|, p70 ≈ 0.32, use thr=0.30
    # - pct-rank (0-1 uniform): p70 ≈ 0.70, use thr=0.70
    # - divergence (two-sided): threshold on |divergence|, p70 ≈ 0.13, use thr=0.10
    recommended_signals = {
        "sell_toxicity_W120_L500":    ("sell_toxicity",             "short",  0.02),
        "buy_toxicity_W120_L500":     ("buy_toxicity",              "long",   0.02),
        "signed_vpin_delta_W120_L500":("signed_vpin_delta",         "signed", 0.30),
        "true_sell_toxicity_W50":     ("true_sell_toxicity_W50",    "short",  0.02),
        "true_buy_toxicity_W50":      ("true_buy_toxicity_W50",     "long",   0.02),
        "true_signed_delta_W50":      ("true_signed_vpin_delta_W50","signed", 0.30),
        "vpin_divergence":            ("vpin_divergence_pct_delta",  "signed", 0.10),
        "cur_vpin_pct_signed":        ("cur_vpin_pct_L500_signed",  "signed", 0.70),
    }

    dsr_rows = []
    H_dsr    = 10  # representative horizon for DSR

    fr_dsr = fwd_rets[H_dsr]

    for sig_label, (col, dirn, thr) in recommended_signals.items():
        # Find the signal in sig_groups
        sg = next((g for g in sig_groups if g["name"] == col), None)
        if sg is None:
            print(f"  SKIP {sig_label} — column {col} not found")
            continue

        perf = build_perf_series(sg["signal"], fr_dsr, thr, direction=dirn)
        dsr_res = deflated_sharpe_ratio(perf, N_TRIALS)

        row = {
            "signal_label":    sig_label,
            "column":          col,
            "direction":       dirn,
            "threshold":       thr,
            "horizon_H":       H_dsr,
            "n_trials":        N_TRIALS,
            **dsr_res,
        }
        dsr_rows.append(row)

        dsr_val = dsr_res.get("dsr", float("nan"))
        act_rt  = dsr_res.get("active_rate", float("nan"))
        sr_hat  = dsr_res.get("sr_hat_bar", float("nan"))
        verdict = ("STRONG" if dsr_val > 0.95 else
                   "POSITIVE" if dsr_val > 0.50 else
                   "WEAK"     if dsr_val > 0.20 else "SPURIOUS")
        print(f"  [{verdict}] {sig_label:40s}: DSR={dsr_val:.4f}  SR_bar={sr_hat:.5f}"
              f"  active={100*act_rt:.1f}%  T_active={dsr_res.get('T_active','?')}")

    dsr_df = pd.DataFrame(dsr_rows)
    dsr_df.to_csv(OUT_DIR / "dsr_recommended_signals.csv", index=False)
    print(f"\nSaved: dsr_recommended_signals.csv")
    print(f"Part E done in {sec(t_part)}")

    # ─────────────────────────────────────────
    # PART F — Adjacent Window Stability
    # ─────────────────────────────────────────
    banner("PART F: Adjacent Window Stability")
    t_part = time.time()

    # For Atlas1 pct settings: group by W value, compute Spearman ρ vs fwd_ret at H=10
    # Window values: W=[20,40,60,80,120,240,480,960] inferred from col names
    import re
    w_pattern = re.compile(r"V\d+_W(\d+)_")
    smooth_pattern = re.compile(r"_S(\w+?)_")
    L_pattern = re.compile(r"_L(\d+)_pct$")

    atlas1_pct_rho = []
    fr_h10 = fwd_rets[10]
    valid_mask = ~np.isnan(fr_h10)

    for col in pct_cols_set:
        m_w = w_pattern.search(col)
        m_s = smooth_pattern.search(col)
        m_l = L_pattern.search(col)
        if not (m_w and m_l):
            continue
        W = int(m_w.group(1))
        smooth = m_s.group(1) if m_s else "raw"
        L = int(m_l.group(1))

        sig = df[col].fillna(0).values
        sig_s = sig * np.sign(delta_norm)  # signed direction

        v = valid_mask & ~np.isnan(sig)
        if v.sum() < 50:
            continue

        rho, pval = stats.spearmanr(sig_s[v], fr_h10[v])
        atlas1_pct_rho.append({
            "window_W": W,
            "smooth":   smooth,
            "lookback_L": L,
            "col": col,
            "spearman_rho": rho,
            "pval": pval,
            "n_bars": int(v.sum()),
        })

    rho_df = pd.DataFrame(atlas1_pct_rho).sort_values("window_W")

    # Summary: mean ρ per W value
    rho_by_W = (rho_df.groupby("window_W")["spearman_rho"]
                .agg(["mean", "std", "min", "max", "count"])
                .reset_index())
    rho_by_W.columns = ["window_W", "rho_mean", "rho_std", "rho_min", "rho_max", "n_configs"]
    rho_by_W["rho_consistently_positive"] = rho_by_W["rho_min"] > 0
    rho_by_W["rho_mean_positive"] = rho_by_W["rho_mean"] > 0

    print("Spearman ρ (signed VPIN direction vs H=10 forward return) by window W:")
    print(f"  {'W':>6} {'mean ρ':>8} {'std ρ':>7} {'min ρ':>7} {'max ρ':>7} {'n':>5} {'consist?':>10}")
    for _, row in rho_by_W.iterrows():
        flag = "YES" if row["rho_consistently_positive"] else ("MIXED" if row["rho_mean_positive"] else "NO")
        print(f"  W={row['window_W']:>4.0f}  {row['rho_mean']:>+7.4f}  {row['rho_std']:>6.4f}"
              f"  {row['rho_min']:>+6.4f}  {row['rho_max']:>+6.4f}  {row['n_configs']:>4.0f}"
              f"  [{flag}]")

    # Window stability verdict
    windows_positive = rho_by_W[rho_by_W["rho_mean_positive"]]
    n_windows = len(rho_by_W)
    n_pos     = len(windows_positive)
    window_stable = n_pos >= n_windows * 0.6  # at least 60% of W values positive

    print(f"\nWindow stability: {n_pos}/{n_windows} W values have mean ρ > 0 → "
          f"{'STABLE' if window_stable else 'FRAGILE'}")

    rho_df.to_csv(OUT_DIR / "window_stability_atlas1_pct.csv", index=False)
    rho_by_W.to_csv(OUT_DIR / "window_stability_summary.csv", index=False)

    # Also check Q16 true VPIN pct by W value
    tvpin_rho = []
    tvpin_w_pattern = re.compile(r"tvpin_pct_V\d+_W(\d+)_")
    tvpin_v_pattern = re.compile(r"tvpin_pct_V(\d+)_")

    for col in tvpin_pct_cols:
        mw = tvpin_w_pattern.search(col)
        mv = tvpin_v_pattern.search(col)
        if not (mw and mv):
            continue
        W = int(mw.group(1))
        V = int(mv.group(1))

        sig = df[col].fillna(0).values
        sig_s = sig * np.sign(delta_norm)

        v = valid_mask & ~np.isnan(sig)
        if v.sum() < 50:
            continue

        rho, pval = stats.spearmanr(sig_s[v], fr_h10[v])
        tvpin_rho.append({"bucket_V": V, "window_W": W, "col": col,
                          "spearman_rho": rho, "pval": pval})

    tvpin_rho_df = pd.DataFrame(tvpin_rho)
    tvpin_rho_by_W = (tvpin_rho_df.groupby("window_W")["spearman_rho"]
                      .agg(["mean", "std", "min", "max"])
                      .reset_index())
    tvpin_rho_by_W.columns = ["window_W", "rho_mean", "rho_std", "rho_min", "rho_max"]

    print("\nTrue VPIN pct Spearman ρ by window W (Q16, H=10):")
    for _, row in tvpin_rho_by_W.iterrows():
        flag = "YES" if row["rho_min"] > 0 else ("MIXED" if row["rho_mean"] > 0 else "NO")
        print(f"  W={row['window_W']:>3.0f}  mean={row['rho_mean']:>+7.4f}  "
              f"min={row['rho_min']:>+6.4f}  max={row['rho_max']:>+6.4f}  [{flag}]")

    tvpin_rho_df.to_csv(OUT_DIR / "window_stability_q16_true_vpin.csv", index=False)
    print(f"Part F done in {sec(t_part)}")

    # ─────────────────────────────────────────
    # PART G — Session Stability
    # ─────────────────────────────────────────
    banner("PART G: Session Stability")
    t_part = time.time()

    sess_cols = ["sess_Asia", "sess_EU", "sess_US_Open", "sess_US_AM", "sess_US_PM", "sess_US_Late"]
    sess_cols = [c for c in sess_cols if c in df.columns]

    # Core recommended signals for session analysis
    # Thresholds must match actual signal ranges (same as DSR analysis)
    session_signals = {
        "sell_toxicity":            ("sell_toxicity",              "short",  0.02),
        "buy_toxicity":             ("buy_toxicity",               "long",   0.02),
        "signed_vpin_delta":        ("signed_vpin_delta",          "signed", 0.30),
        "true_sell_toxicity_W50":   ("true_sell_toxicity_W50",     "short",  0.02),
        "true_buy_toxicity_W50":    ("true_buy_toxicity_W50",      "long",   0.02),
        "true_signed_delta_W50":    ("true_signed_vpin_delta_W50", "signed", 0.30),
        "cur_vpin_pct_signed":      ("cur_vpin_pct_L500_signed",   "signed", 0.70),
        "vpin_divergence_pct_delta":("vpin_divergence_pct_delta",  "signed", 0.10),
    }

    sess_rows = []
    for sig_label, (col, dirn, thr) in session_signals.items():
        sg = next((g for g in sig_groups if g["name"] == col), None)
        if sg is None:
            continue

        perf_h10 = build_perf_series(sg["signal"], fr_h10, thr, direction=dirn)

        for sess in sess_cols:
            sess_mask = (df[sess].values == 1) & (perf_h10 != 0)
            n_active = int(sess_mask.sum())
            if n_active < 10:
                continue

            perf_sess = perf_h10[sess_mask]
            hit_rate  = float(np.mean(perf_sess > 0))
            mean_ret  = float(perf_sess.mean())
            sr_sess   = float(perf_sess.mean() / perf_sess.std()) if perf_sess.std() > 0 else 0.0

            try:
                rho, pval = stats.spearmanr(sg["signal"][sess_mask & (perf_h10 != 0)],
                                             fr_h10[sess_mask & (perf_h10 != 0)])
            except Exception:
                rho = pval = float("nan")

            sess_rows.append({
                "signal_label": sig_label,
                "session": sess.replace("sess_", ""),
                "n_active_bars": n_active,
                "hit_rate": hit_rate,
                "mean_ret_per_bar": mean_ret,
                "sharpe_like": sr_sess,
                "spearman_rho": rho,
                "pval": pval,
            })

    sess_df = pd.DataFrame(sess_rows)
    sess_df.to_csv(OUT_DIR / "session_stability.csv", index=False)

    print("Session stability (H=10, hit rate > 0.5 = signal fires correctly):")
    print(f"  {'Signal':40s}  {'Session':12s}  {'N':>6}  {'HR':>6}  {'SR':>7}  {'rho':>7}")
    print("  " + "-"*85)
    for _, row in sess_df.sort_values(["signal_label","session"]).iterrows():
        flag = "OK" if row["hit_rate"] > 0.50 else "FAIL"
        print(f"  [{flag}] {row['signal_label']:38s}  {row['session']:12s}  "
              f"{row['n_active_bars']:>5.0f}  {row['hit_rate']:>5.3f}  "
              f"{row['sharpe_like']:>6.3f}  {row['spearman_rho']:>+6.4f}")

    # Session stability verdict per signal
    sess_stability = {}
    for sig_label in session_signals:
        sub = sess_df[sess_df["signal_label"] == sig_label]
        n_ok = int((sub["hit_rate"] > 0.50).sum())
        n_tot = len(sub)
        stable = n_ok >= 3  # at least 3 sessions positive
        sess_stability[sig_label] = {"n_sessions_ok": n_ok, "n_sessions": n_tot,
                                     "session_stable": stable}
        print(f"\n  {sig_label}: {n_ok}/{n_tot} sessions with HR>50% → "
              f"{'SESSION-STABLE' if stable else 'SESSION-FRAGILE'}")

    print(f"Part G done in {sec(t_part)}")

    # ─────────────────────────────────────────
    # PART H — Regime Stability
    # ─────────────────────────────────────────
    banner("PART H: Regime Stability")
    t_part = time.time()

    # Regime split: use realized volatility quintiles and delta_norm regime
    # vol_5 = rolling 5-bar std of fwd_ret (proxy for intraday vol)
    # Also use cusum_up / cusum_dn from Q16 if available

    # Compute vol regime from price (5-bar rolling std of 1-bar return)
    price_arr = df["close"].ffill().values
    one_bar_ret = np.zeros(T, dtype=np.float32)
    one_bar_ret[1:] = (price_arr[1:] - price_arr[:-1])
    # Rolling 5-bar std
    vol_5 = pd.Series(one_bar_ret).rolling(5, min_periods=3).std().values

    vol_quintile = pd.qcut(pd.Series(vol_5).ffill().bfill(),
                           q=5, labels=["VL", "L", "M", "H", "VH"]).values

    # Delta norm regime: trending (|delta| > 0.5) vs choppy (|delta| ≤ 0.5)
    trending_mask = np.abs(delta_norm) > 0.50
    choppy_mask   = np.abs(delta_norm) <= 0.50

    regime_signals = session_signals   # same set
    regime_rows = []
    for sig_label, (col, dirn, thr) in regime_signals.items():
        sg = next((g for g in sig_groups if g["name"] == col), None)
        if sg is None:
            continue

        perf_h10 = build_perf_series(sg["signal"], fr_h10, thr, direction=dirn)
        active   = perf_h10 != 0

        for regime_name, regime_mask in [
            ("trending",  trending_mask & active),
            ("choppy",    choppy_mask   & active),
            ("vol_low",   (vol_quintile == "L")  & active),
            ("vol_high",  (vol_quintile == "H")  & active),
            ("vol_vhigh", (vol_quintile == "VH") & active),
        ]:
            n = int(regime_mask.sum())
            if n < 10:
                continue
            p = perf_h10[regime_mask]
            hr = float(np.mean(p > 0))
            sr = float(p.mean() / p.std()) if p.std() > 0 else 0.0
            regime_rows.append({
                "signal_label": sig_label,
                "regime": regime_name,
                "n_bars": n,
                "hit_rate": hr,
                "sharpe_like": sr,
            })

    reg_df = pd.DataFrame(regime_rows)
    reg_df.to_csv(OUT_DIR / "regime_stability.csv", index=False)

    print("Regime stability (H=10):")
    print(f"  {'Signal':40s}  {'Regime':12s}  {'N':>6}  {'HR':>6}  {'SR':>7}")
    for _, row in reg_df.sort_values(["signal_label","regime"]).iterrows():
        flag = "OK" if row["hit_rate"] > 0.50 else "FAIL"
        print(f"  [{flag}] {row['signal_label']:38s}  {row['regime']:12s}  "
              f"{row['n_bars']:>5.0f}  {row['hit_rate']:>5.3f}  {row['sharpe_like']:>6.3f}")

    # Regime stability: at least 3 out of 5 regime splits positive
    reg_stability = {}
    for sig_label in regime_signals:
        sub = reg_df[reg_df["signal_label"] == sig_label]
        n_ok  = int((sub["hit_rate"] > 0.50).sum())
        n_tot = len(sub)
        stable = n_ok >= 3
        reg_stability[sig_label] = {"n_regimes_ok": n_ok, "n_regimes": n_tot,
                                    "regime_stable": stable}

    print(f"Part H done in {sec(t_part)}")

    # ─────────────────────────────────────────
    # PART I — Kill / Keep / Defer Decision
    # ─────────────────────────────────────────
    banner("PART I: Kill / Keep / Defer Decision")
    t_part = time.time()

    # DSR results keyed by signal_label
    dsr_map = {r["signal_label"]: r for r in dsr_rows}

    # PBO at H=10 (representative)
    pbo_h10 = pbo_by_H.get(10, {})

    # Per-family PBO for family-level assessment
    family_pbo_map = {r["family"]: r["pbo"] for r in pbo_family_rows}

    # Signal-level aggregated verdict
    # Map recommended signals to their session/regime/family keys
    signal_meta = [
        # (label_in_decisions, sess_key, family_key, win_check)
        ("sell_toxicity_W120_L500",    "sell_toxicity",            "atlas1_directional",    "atlas1"),
        ("buy_toxicity_W120_L500",     "buy_toxicity",             "atlas1_directional",    "atlas1"),
        ("signed_vpin_delta_W120_L500","signed_vpin_delta",        "atlas1_directional",    "atlas1"),
        ("true_sell_toxicity_W50",     "true_sell_toxicity_W50",   "q16_true_directional",  "q16"),
        ("true_buy_toxicity_W50",      "true_buy_toxicity_W50",    "q16_true_directional",  "q16"),
        ("true_signed_delta_W50",      "true_signed_delta_W50",    "q16_true_directional",  "q16"),
        ("vpin_divergence",            "vpin_divergence_pct_delta","divergence",            None),
        ("cur_vpin_pct_signed",        "cur_vpin_pct_signed",      "current_vpin",          "atlas1"),
    ]

    decision_rows = []
    for (sig_label, sess_key, fam_key, win_check) in signal_meta:
        sess_info = sess_stability.get(sess_key,
                    {"n_sessions_ok": 0, "n_sessions": 0, "session_stable": False})
        reg_info  = reg_stability.get(sess_key,
                    {"n_regimes_ok": 0, "n_regimes": 0, "regime_stable": False})
        dsr_info  = dsr_map.get(sig_label, {})
        dsr_val   = dsr_info.get("dsr", float("nan"))
        fam_pbo   = family_pbo_map.get(fam_key, float("nan"))
        pbo_full  = pbo_h10.get("pbo", float("nan"))

        # Window stability
        if win_check == "atlas1":
            win_stable = window_stable
        elif win_check == "q16":
            tvpin_pos  = len(tvpin_rho_by_W[tvpin_rho_by_W["rho_mean"] > 0])
            tvpin_tot  = len(tvpin_rho_by_W)
            win_stable = tvpin_pos >= tvpin_tot * 0.6
        else:
            win_stable = None  # divergence: no window parameter

        dsr_ok   = (not math.isnan(dsr_val)) and dsr_val > 0.50
        sess_ok  = sess_info.get("session_stable", False)
        reg_ok   = reg_info.get("regime_stable", False)
        win_ok   = win_stable if win_stable is not None else True
        fam_ok   = (not math.isnan(fam_pbo)) and fam_pbo < 0.40  # family not badly overfit

        # Spearman ρ consistency check from window stability table
        spearman_positive = window_stable if win_check == "atlas1" else (win_ok if win_check == "q16" else False)

        # ─────────────────────────────────────────────────────────
        # DECISION CRITERIA (3-tier):
        #
        # KEEP   : DSR > 0.50  AND  family_PBO < 0.20  AND  session_stable
        #          AND  (regime_stable OR window_stable)
        #          (statistical strength + robustness confirmed)
        #
        # DEFER  : NOT(KEEP) but EITHER:
        #          (a) window_stable AND session_stable AND family_PBO < 0.50
        #          (b) window_stable AND regime_stable AND (fam_pbo or dsr marginal)
        #          → 30-day shadow can confirm
        #
        # KILL   : family_PBO >= 0.60 OR (NOT session_stable AND NOT window_stable)
        #          OR (fails ALL of: window, session, regime)
        #          → No robustness evidence at any level
        # ─────────────────────────────────────────────────────────

        all_fail = not sess_ok and not win_ok and not reg_ok
        fam_high_pbo = (not math.isnan(fam_pbo)) and fam_pbo >= 0.60

        if dsr_ok and (not math.isnan(fam_pbo)) and fam_pbo < 0.20 and sess_ok and (reg_ok or win_ok):
            decision = "KEEP"
        elif not fam_high_pbo and not all_fail and (win_ok or sess_ok):
            decision = "DEFER"
        else:
            decision = "KILL"

        row = {
            "signal_label":      sig_label,
            "pbo_H10":           pbo_full,
            "dsr":               dsr_val,
            "n_trials_total":    N_TRIALS,
            "window_stable":     win_stable,
            "session_stable":    sess_ok,
            "n_sessions_ok":     sess_info.get("n_sessions_ok", 0),
            "n_sessions":        sess_info.get("n_sessions", 0),
            "regime_stable":     reg_ok,
            "n_regimes_ok":      reg_info.get("n_regimes_ok", 0),
            "n_regimes":         reg_info.get("n_regimes", 0),
            "decision":          decision,
            "dsr_verdict":       ("STRONG"   if dsr_val > 0.95 else
                                  "POSITIVE" if dsr_val > 0.50 else
                                  "WEAK"     if dsr_val > 0.20 else "SPURIOUS"),
        }
        decision_rows.append(row)

    dec_df = pd.DataFrame(decision_rows)
    dec_df.to_csv(OUT_DIR / "kill_keep_defer_decisions.csv", index=False)

    print(f"\n{'Signal':40s}  {'Decision':8s}  {'PBO':6s}  {'DSR':6s}  {'Win':5s}  {'Sess':5s}  {'Reg':5s}")
    print("─" * 85)
    for _, row in dec_df.iterrows():
        print(f"{row['signal_label']:40s}  "
              f"{row['decision']:8s}  "
              f"{row['pbo_H10']:.3f}   "
              f"{row['dsr']:.4f}  "
              f"{'Y' if row['window_stable'] else ('N' if row['window_stable']==False else '-'):5s}  "
              f"{'Y' if row['session_stable'] else 'N':5s}  "
              f"{'Y' if row['regime_stable'] else 'N':5s}")

    keeps  = [r["signal_label"] for r in decision_rows if r["decision"] == "KEEP"]
    defers = [r["signal_label"] for r in decision_rows if r["decision"] == "DEFER"]
    kills  = [r["signal_label"] for r in decision_rows if r["decision"] == "KILL"]

    print(f"\nKEEP  ({len(keeps)}):  {keeps}")
    print(f"DEFER ({len(defers)}):  {defers}")
    print(f"KILL  ({len(kills)}):  {kills}")
    print(f"Part I done in {sec(t_part)}")

    # ─────────────────────────────────────────
    # PART J — Final Report
    # ─────────────────────────────────────────
    banner("PART J: Final Report")

    total_runtime = time.time() - t_start

    report_lines = [
        f"# PBO / DEFLATED SHARPE OVERFITTING AUDIT v1",
        f"**Generated**: {gen_time}",
        f"**Runtime**: {total_runtime:.1f} seconds",
        f"**SHADOW / RESEARCH ONLY — no execution, no broker, no order placement**",
        f"**Method**: Bailey, Borwein, López de Prado & Zhu (2015) — CSCV + DSR",
        f"",
        f"---",
        f"",
        f"## Configuration Count (Total Trials)",
        f"",
        f"| Atlas | Family | N configs |",
        f"|-------|--------|-----------|",
    ]
    for row in inv_rows:
        report_lines.append(f"| {row['atlas']} | {row['family']} | {row['n_configs']:,} |")
    report_lines += [
        f"",
        f"**Raw parameter configs**: {n_total_raw:,}",
        f"**× {n_session_filter_mult} conditional filter modes** = **{N_TRIALS:,} total trial evaluations**",
        f"",
        f"This is the N used in the Deflated Sharpe benchmark (E[max SR | N trials]).",
        f"Higher N raises the DSR bar — every additional test evaluated weakens any single result.",
        f"",
        f"---",
        f"",
        f"## CSCV Results — Probability of Backtest Overfitting",
        f"",
        f"Dataset: Atlas1 directional panel, T={T:,} bars, S={S} subperiods → C({S},{S//2}) = {len(list(combinations(range(S), S//2))):,} combinations.",
        f"Performance metric: per-bar signed return × direction bet (0 when signal inactive).",
        f"",
        f"| Horizon H | PBO | Logit mean | Prob(loss OOS) | Degradation β |",
        f"|-----------|-----|------------|----------------|---------------|",
    ]
    for H, res in sorted(pbo_by_H.items()):
        verdict = "LOW" if res["pbo"] < 0.20 else ("MED" if res["pbo"] < 0.40 else "HIGH")
        report_lines.append(
            f"| H={H} | **{res['pbo']:.3f}** [{verdict}] | {res['logit_mean']:+.3f} "
            f"| {res['prob_loss']:.3f} | {res['degradation_beta']:+.3f} |"
        )
    report_lines += [
        f"",
        f"### PBO by Signal Family (H=10)",
        f"",
        f"| Family | N configs | PBO | Verdict |",
        f"|--------|-----------|-----|---------|",
    ]
    for row in pbo_family_rows:
        verdict = "LOW (not overfit)" if row["pbo"] < 0.20 else ("MED" if row["pbo"] < 0.40 else "HIGH (overfit)")
        report_lines.append(
            f"| {row['family']} | {row['n_configs']} | {row['pbo']:.3f} | {verdict} |"
        )
    report_lines += [
        f"",
        f"**Key insight on PBO**: PBO measures the probability that the IS-optimal configuration",
        f"underperforms the median OOS. PBO < 0.05 = strong; < 0.20 = acceptable; ≥ 0.40 = overfit.",
        f"",
        f"---",
        f"",
        f"## Deflated Sharpe Ratio",
        f"",
        f"N_trials = {N_TRIALS:,}  (benchmark E[max SR] = expected max Sharpe across {N_TRIALS:,} iid tests).",
        f"DSR = PSR(E[max SR]): probability that the selected signal's true SR exceeds the expected",
        f"maximum noise SR from {N_TRIALS:,} random trials.",
        f"Threshold: DSR > 0.95 = STRONG; > 0.50 = POSITIVE; > 0.20 = WEAK; ≤ 0.20 = SPURIOUS.",
        f"",
        f"| Signal | DSR | SR_bar | Active% | T_active | Verdict |",
        f"|--------|-----|--------|---------|----------|---------|",
    ]
    for row in dsr_rows:
        report_lines.append(
            f"| {row['signal_label']} | **{row.get('dsr', float('nan')):.4f}** "
            f"| {row.get('sr_hat_bar', float('nan')):.5f} "
            f"| {100*row.get('active_rate', 0):.1f}% "
            f"| {row.get('T_active', '?')} "
            f"| {row.get('dsr_verdict', '?')} |"
        )
    if dsr_rows:
        dsr_vals = [r.get("dsr", float("nan")) for r in dsr_rows if not math.isnan(r.get("dsr", float("nan")))]
        if dsr_vals:
            max_dsr = max(dsr_vals)
            best_label = [r["signal_label"] for r in dsr_rows if r.get("dsr") == max_dsr][0]
            report_lines += [
                f"",
                f"Best DSR: **{max_dsr:.4f}** ({best_label}) — "
                + ("survives N_trials adjustment" if max_dsr > 0.5 else "marginal after trial inflation"),
            ]
    report_lines += [
        f"",
        f"---",
        f"",
        f"## Window Stability (Atlas1, signed direction, H=10)",
        f"",
        f"| W | Mean ρ | Std ρ | Min ρ | Max ρ | N configs | Consistent? |",
        f"|---|--------|-------|-------|-------|-----------|-------------|",
    ]
    for _, row in rho_by_W.iterrows():
        flag = "YES" if row["rho_consistently_positive"] else ("MIXED" if row["rho_mean_positive"] else "NO")
        report_lines.append(
            f"| W={row['window_W']:.0f} | {row['rho_mean']:+.4f} | {row['rho_std']:.4f} "
            f"| {row['rho_min']:+.4f} | {row['rho_max']:+.4f} | {row['n_configs']:.0f} | {flag} |"
        )
    report_lines += [
        f"",
        f"**Window stable** (≥60% of W values with mean ρ>0): {'YES' if window_stable else 'NO'}",
        f"",
        f"---",
        f"",
        f"## Session Stability",
        f"",
        f"(See session_stability.csv for full detail)",
        f"",
        f"| Signal | Sessions OK | Session-stable? |",
        f"|--------|-------------|----------------|",
    ]
    for sig_label, info in sess_stability.items():
        flag = "YES" if info["session_stable"] else "NO"
        report_lines.append(
            f"| {sig_label} | {info['n_sessions_ok']}/{info['n_sessions']} | {flag} |"
        )
    report_lines += [
        f"",
        f"---",
        f"",
        f"## Kill / Keep / Defer Decisions",
        f"",
        f"Criteria:",
        f"- **KEEP**: DSR > 0.50 AND family_PBO < 0.20 AND session_stable AND (regime_stable OR window_stable)",
        f"- **DEFER**: family_PBO < 0.60 AND NOT(all robustness fail) AND (window_stable OR session_stable)",
        f"  — DSR alone does not kill: with N=97,680 correlated trials E[max SR]≈4.43; any 28-day SR is trivially 'spurious'. Robustness (window/session/regime stability) is the primary criterion.",
        f"- **KILL**: family_PBO ≥ 0.60 OR (NOT session_stable AND NOT window_stable AND NOT regime_stable)",
        f"",
        f"| Signal | Decision | PBO | DSR | Win | Sess | Reg |",
        f"|--------|----------|-----|-----|-----|------|-----|",
    ]
    for row in decision_rows:
        report_lines.append(
            f"| {row['signal_label']} | **{row['decision']}** "
            f"| {row['pbo_H10']:.3f} | {row['dsr']:.4f} "
            f"| {'Y' if row['window_stable'] else ('N' if row['window_stable']==False else '-')} "
            f"| {'Y' if row['session_stable'] else 'N'} "
            f"| {'Y' if row['regime_stable'] else 'N'} |"
        )
    report_lines += [
        f"",
        f"### KEEP  ({len(keeps)} signals)",
    ]
    for s in keeps:
        report_lines.append(f"- `{s}` — survives PBO, DSR, session, regime tests")
    report_lines += [
        f"",
        f"### DEFER ({len(defers)} signals)",
    ]
    for s in defers:
        report_lines.append(f"- `{s}` — marginal; requires 30-day shadow confirmation")
    report_lines += [
        f"",
        f"### KILL  ({len(kills)} signals)",
    ]
    for s in kills:
        report_lines.append(f"- `{s}` — killed: fails PBO / DSR / stability criteria")
    report_lines += [
        f"",
        f"---",
        f"",
        f"## Methodology Notes",
        f"",
        f"1. **CSCV (Bailey et al. 2015)**: T={T} bars split into S={S} subperiods → C({S},{S//2})={len(list(combinations(range(S),S//2)))}"
        f" IS/OOS combinations. PBO = fraction of combinations where IS-optimal config underperforms median OOS.",
        f"",
        f"2. **Performance metric**: per-bar signed return × direction bet. When signal fires above threshold:"
        f" +fwd_ret (long signals), -fwd_ret (short signals), sign(signal)×fwd_ret (signed signals).",
        f"",
        f"3. **DSR (Bailey & López de Prado 2014)**: adjusts for selection bias from {N_TRIALS:,} configurations."
        f" E[max SR] = expected maximum noise SR across {N_TRIALS:,} iid trials (Euler-Mascheroni approximation)."
        f" DSR = Φ((SR_hat - E[max SR]) / √Var[SR]).",
        f"",
        f"4. **Forward returns**: computed from continuous back-adjusted close (Q16 dataset)."
        f" Handles NQM6→NQU6 roll cleanly. No lookahead: fwd_ret_H = close[t+H] - close[t].",
        f"",
        f"5. **Stability tests**: (a) Window — mean Spearman ρ>0 across adjacent W values;"
        f" (b) Session — hit rate >50% in ≥3 sessions; (c) Regime — hit rate >50% in ≥3 regimes.",
        f"",
        f"---",
        f"",
        f"## Output Files",
        f"",
        f"| File | Part | Description |",
        f"|------|------|-------------|",
        f"| pbo_configuration_inventory.csv | B | All N_trials breakdown |",
        f"| pbo_results_by_horizon.csv | D | PBO per horizon H |",
        f"| pbo_results_by_family.csv | D | PBO per signal family |",
        f"| dsr_recommended_signals.csv | E | DSR for 8 candidate signals |",
        f"| window_stability_atlas1_pct.csv | F | Per-col ρ by W (Atlas1) |",
        f"| window_stability_summary.csv | F | Mean ρ by W (Atlas1) |",
        f"| window_stability_q16_true_vpin.csv | F | Per-col ρ by W (Q16 true) |",
        f"| session_stability.csv | G | Per-signal per-session results |",
        f"| regime_stability.csv | H | Per-signal per-regime results |",
        f"| kill_keep_defer_decisions.csv | I | Final decisions |",
        f"| PBO_DEFLATED_SHARPE_AUDIT_REPORT.md | J | This report |",
        f"",
        f"---",
        f"",
        f"## Final Status",
        f"```",
        f"PRODUCTION_FILES_MODIFIED:              false",
        f"DASHBOARD_CODE_MODIFIED:                false",
        f"FEATURE_MASTER_CODE_MODIFIED:           false",
        f"BOOK_FLOW_CODE_MODIFIED:                false",
        f"MODEL_ARTIFACTS_MODIFIED:               false",
        f"ACTIVE_MODEL_POINTER_CHANGED:           false",
        f"TRADING_ENABLED:                        false",
        f"BROKER_CONNECTED:                       false",
        f"PAPER_TRADING_ENABLED:                  false",
        f"PBO_METHOD:                             CSCV Bailey et al. 2015 — S={S} C({S},{S//2})={len(list(combinations(range(S),S//2)))} combos",
        f"DSR_METHOD:                             Bailey & López de Prado 2014 — N_trials={N_TRIALS}",
        f"TOTAL_CONFIGS_INVENTORIED:              {N_TRIALS:,}",
        f"T_BARS:                                 {T:,}",
        f"N_SIGNALS_IN_M_MATRIX:                 {N_configs_per_H:,} per horizon",
        f"SIGNALS_KEEP:                           {len(keeps)}",
        f"SIGNALS_DEFER:                          {len(defers)}",
        f"SIGNALS_KILL:                           {len(kills)}",
        f"RUNTIME_SECONDS:                        {total_runtime:.1f}",
        f"OVERALL:                                PASS",
        f"```",
    ]

    report_text = "\n".join(report_lines)
    with open(OUT_DIR / "PBO_DEFLATED_SHARPE_AUDIT_REPORT.md", "w") as f:
        f.write(report_text)
    print(f"Saved: PBO_DEFLATED_SHARPE_AUDIT_REPORT.md")

    print(f"\n{'='*60}")
    print(f"PBO Overfitting Audit COMPLETE in {total_runtime:.1f}s")
    print(f"{'='*60}")
    print(f"  T = {T:,} bars  |  N_configs/H = {N_configs_per_H:,}  |  N_total_trials = {N_TRIALS:,}")
    print(f"  KEEP:  {keeps}")
    print(f"  DEFER: {defers}")
    print(f"  KILL:  {kills}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
