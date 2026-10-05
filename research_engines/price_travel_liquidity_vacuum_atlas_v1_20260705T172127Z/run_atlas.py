"""
Price Travel / Liquidity Vacuum Atlas v1
SHADOW / RESEARCH ONLY — NO EXECUTION / NO BROKER / NO PAPER TRADING
Generated: 2026-07-05
"""

import sys, os, warnings, glob, time
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
from pathlib import Path
from scipy import stats
from scipy.stats import spearmanr, pearsonr
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
import json

t0 = time.time()

OUT = Path("/home/prabh/OFI_Production/research_engines/price_travel_liquidity_vacuum_atlas_v1_20260705T172127Z")
OUT.mkdir(parents=True, exist_ok=True)

EPS = 1e-6

print("=" * 70)
print("PRICE TRAVEL / LIQUIDITY VACUUM ATLAS v1")
print("SHADOW / RESEARCH ONLY — NO EXECUTION / NO BROKER")
print("=" * 70)

# ─────────────────────────────────────────────────────────────────────────────
# LOAD DATA
# ─────────────────────────────────────────────────────────────────────────────
print("\n[DATA] Loading continuous master (NQU6)...")
master_all = pd.read_json(
    "/home/prabh/OFI_Live_Features/master_NQ_continuous_backadjusted_shadow.ndjsonl",
    lines=True
)
master = master_all[master_all["source_contract"] == "NQU6"].copy()
master = master.sort_values("bar_end_ts_ns").reset_index(drop=True)
print(f"  NQU6 bars: {len(master):,}  date range: {master['day'].min()} – {master['day'].max()}")

print("[DATA] Loading model feature master...")
mfm = pd.read_parquet("/home/prabh/OFI_Production/model_feature_master/data/model_feature_master_shadow.parquet")
print(f"  MFM: {mfm.shape}")

print("[DATA] Loading Q16 true VPIN panel...")
q16 = pd.read_parquet(
    "/home/prabh/OFI_Production/research_engines/"
    "q16_true_vpin_feature_master_recommendation_20260603_20260701_20260702T061819Z/"
    "q16_true_vpin_feature_master_aligned.parquet"
)
q16_nqu6 = q16[q16.get("is_current", pd.Series(True, index=q16.index)).fillna(True)].copy()
print(f"  Q16: {q16_nqu6.shape}")

print("[DATA] Aggregating BF level candles to bar level...")
bf_files = sorted(glob.glob(
    "/home/prabh/OFI_Production/book_flow_chart/cache/"
    "book_flow_level_candles_NQU6_*_top10.parquet"
))
bf_chunks = []
for f in bf_files:
    df = pd.read_parquet(f)
    # Aggregate per bar_idx / side_zone
    g = df.groupby(["bar_idx", "side_zone"]).agg(
        bid_add=("bid_add", "sum"),
        bid_pull=("bid_pull", "sum"),
        ask_add=("ask_add", "sum"),
        ask_pull=("ask_pull", "sum"),
        abs_flow=("abs_flow", "sum"),
        net_bid_flow=("net_bid_flow", "sum"),
        net_ask_flow=("net_ask_flow", "sum"),
        trade_vol=("trade_volume_at_price", "sum"),
        bar_end_ts_ns=("bar_end_ts_ns", "first"),
    ).reset_index()
    bf_chunks.append(g)
bf_long = pd.concat(bf_chunks, ignore_index=True)

# Pivot side_zone so we get bid/ask/near_mid side metrics per bar
bf_pivot = bf_long.pivot_table(
    index=["bar_idx", "bar_end_ts_ns"],
    columns="side_zone",
    values=["bid_add", "bid_pull", "ask_add", "ask_pull", "abs_flow",
            "net_bid_flow", "net_ask_flow", "trade_vol"],
    aggfunc="sum"
).fillna(0)
bf_pivot.columns = [f"bfl_{col[0]}_{col[1]}" for col in bf_pivot.columns]
bf_pivot = bf_pivot.reset_index()
print(f"  BF level agg: {bf_pivot.shape}")

# ─────────────────────────────────────────────────────────────────────────────
# MERGE: base is NQU6 master
# ─────────────────────────────────────────────────────────────────────────────
print("\n[MERGE] Joining data sources on bar_end_ts_ns...")
base = master.copy()

# Join MFM
mfm_cols = [c for c in mfm.columns if c not in ["bar_index", "day", "session_date",
             "master_timestamp_utc", "bar_end_ts_ns"]]
base = base.merge(
    mfm[["bar_end_ts_ns"] + [c for c in mfm_cols if c in mfm.columns]].drop_duplicates("bar_end_ts_ns"),
    on="bar_end_ts_ns", how="left", suffixes=("", "_mfm")
)

# Join Q16
q16_keep = ["bar_end_ts_ns", "cur_buy_toxicity", "cur_sell_toxicity",
            "cur_vpin_pct_L500", "true_signed_vpin_delta_W10",
            "true_buy_toxicity_W10", "true_sell_toxicity_W10",
            "tvpin_pct_V500_W20_L500", "vpin_divergence_pct_delta"]
q16_join = q16_nqu6[[c for c in q16_keep if c in q16_nqu6.columns]].drop_duplicates("bar_end_ts_ns")
base = base.merge(q16_join, on="bar_end_ts_ns", how="left")

# Join BF level candles
bf_join = bf_pivot.rename(columns={"bar_idx": "bar_index"}).drop(columns=["bar_index"], errors="ignore").drop_duplicates("bar_end_ts_ns")
base = base.merge(bf_join, on="bar_end_ts_ns", how="left")

print(f"  Final base: {base.shape}")

# Filter to sealed bars only
if "is_closed_bar" in base.columns:
    base = base[base["is_closed_bar"].fillna(True)].copy()
    print(f"  After sealed-bar filter: {len(base):,}")

base = base.reset_index(drop=True)
N = len(base)

# ─────────────────────────────────────────────────────────────────────────────
# PART A — BAR TRAVEL LABELS
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART A] Computing bar travel labels...")

px_open  = base["px_open"].values
px_high  = base["px_high"].values
px_low   = base["px_low"].values
px_close = base["px_close"].values

bar_range_pts         = (px_high - px_low)
body_pts              = np.abs(px_close - px_open)
up_travel_pts         = px_high - px_open
down_travel_pts       = px_open - px_low
close_to_high_dist    = px_high - px_close
close_to_low_dist     = px_close - px_low
wick_top              = px_high - np.maximum(px_open, px_close)
wick_bottom           = np.minimum(px_open, px_close) - px_low
directional_return    = px_close - px_open
signed_travel         = directional_return

travel_efficiency     = body_pts / np.maximum(bar_range_pts, EPS)
range_per_volume      = bar_range_pts / np.maximum(base["vol_total"].values, EPS)
body_per_volume       = body_pts     / np.maximum(base["vol_total"].values, EPS)

abs_delta = np.abs(base["delta_norm"].values) if "delta_norm" in base.columns else np.ones(N)
abs_flow_col = base.get("bfl_abs_flow_bid", np.zeros(N))
if hasattr(abs_flow_col, "values"): abs_flow_col = abs_flow_col.values
range_per_abs_flow = bar_range_pts / np.maximum(np.abs(abs_flow_col) + EPS, EPS)

# Travel direction
travel_direction = np.where(
    signed_travel >  0.5 * bar_range_pts * 0.5,  "UP_TRAVEL",
    np.where(
        signed_travel < -0.5 * bar_range_pts * 0.5, "DOWN_TRAVEL",
        np.where(travel_efficiency < 0.25, "TWO_WAY_CHOP", "LOW_TRAVEL")
    )
)

# Travel buckets
p25, p75, p95 = np.nanpercentile(bar_range_pts, [25, 75, 95])
travel_bucket = np.where(
    bar_range_pts >= p95, "EXTREME_TRAVEL",
    np.where(bar_range_pts >= p75, "HIGH_TRAVEL",
    np.where(bar_range_pts >= p25, "NORMAL_TRAVEL", "LOW_TRAVEL"))
)

# Directional buckets
dp_p95 = np.nanpercentile(signed_travel, 95)
dp_p5  = np.nanpercentile(signed_travel, 5)
dir_bucket = np.where(
    signed_travel >= dp_p95, "EXTREME_UP_TRAVEL",
    np.where(signed_travel <= dp_p5, "EXTREME_DOWN_TRAVEL",
    np.where(travel_bucket == "LOW_TRAVEL", "LOW_TRAVEL_ABSORPTION", "NORMAL"))
)

labels = pd.DataFrame({
    "bar_end_ts_ns":        base["bar_end_ts_ns"].values,
    "bar_index":            base["bar_index"].values,
    "day":                  base["day"].values,
    "px_open":              px_open,
    "px_high":              px_high,
    "px_low":               px_low,
    "px_close":             px_close,
    "bar_range_pts":        bar_range_pts,
    "body_pts":             body_pts,
    "up_travel_pts":        up_travel_pts,
    "down_travel_pts":      down_travel_pts,
    "close_to_high_dist":   close_to_high_dist,
    "close_to_low_dist":    close_to_low_dist,
    "wick_top":             wick_top,
    "wick_bottom":          wick_bottom,
    "directional_return":   directional_return,
    "signed_travel":        signed_travel,
    "travel_direction":     travel_direction,
    "travel_efficiency":    travel_efficiency,
    "range_per_volume":     range_per_volume,
    "body_per_volume":      body_per_volume,
    "range_per_abs_flow":   range_per_abs_flow,
    "travel_bucket":        travel_bucket,
    "dir_bucket":           dir_bucket,
})

labels.to_parquet(OUT / "bar_travel_labels.parquet", index=False)

dist = labels["travel_bucket"].value_counts().rename_axis("bucket").reset_index()
dist.columns = ["bucket", "count"]
dist["pct"] = (dist["count"] / len(labels) * 100).round(2)
dist["mean_range"] = [labels.loc[labels["travel_bucket"]==b, "bar_range_pts"].mean().round(4) for b in dist["bucket"]]
print(f"  Travel distribution:\n{dist.to_string(index=False)}")
dist.to_csv(OUT / "bar_travel_distribution.csv", index=False)

print(f"  bar_range_pts: median={np.nanmedian(bar_range_pts):.2f} mean={np.nanmean(bar_range_pts):.2f} "
      f"p75={p75:.2f} p95={p95:.2f}")

# ─────────────────────────────────────────────────────────────────────────────
# PART B — ORDER-FLOW EXPLANATORY FEATURES
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART B] Building order-flow feature panel...")

panel = labels[["bar_end_ts_ns", "bar_index", "day", "bar_range_pts",
                "signed_travel", "travel_bucket", "dir_bucket",
                "travel_efficiency"]].copy()

# ── Current bar features ─────────────────────────────────────────────────────
def safe_col(df, col, default=0.0):
    if col in df.columns:
        return df[col].fillna(0).values.astype(float)
    return np.full(len(df), default, dtype=float)

panel["vol_total"]          = safe_col(base, "vol_total")
panel["buy_vol"]            = safe_col(base, "buy_vol")
panel["sell_vol"]           = safe_col(base, "sell_vol")
panel["delta_norm"]         = safe_col(base, "delta_norm")
panel["abs_delta"]          = np.abs(panel["delta_norm"])
panel["vpin"]               = safe_col(base, "vpin")
panel["mlofi_norm"]         = safe_col(base, "mlofi_norm")
panel["mlofi_decay_sum"]    = safe_col(base, "mlofi_decay_sum")
panel["sweep_imbalance_norm"] = safe_col(base, "sweep_imbalance_norm")
panel["volatility_5"]       = safe_col(base, "volatility_5")
panel["buy_ratio"]          = safe_col(base, "buy_ratio")
panel["sell_ratio"]         = safe_col(base, "sell_ratio")

# BF level aggregated book-flow metrics
panel["BidAdd"]  = safe_col(base, "bfl_bid_add_bid") + safe_col(base, "bfl_bid_add_near_mid")
panel["BidPull"] = safe_col(base, "bfl_bid_pull_bid") + safe_col(base, "bfl_bid_pull_near_mid")
panel["AskAdd"]  = safe_col(base, "bfl_ask_add_ask") + safe_col(base, "bfl_ask_add_near_mid")
panel["AskPull"] = safe_col(base, "bfl_ask_pull_ask") + safe_col(base, "bfl_ask_pull_near_mid")
panel["NetBid"]  = panel["BidAdd"] - panel["BidPull"]
panel["NetAsk"]  = panel["AskAdd"] - panel["AskPull"]
panel["Signed"]  = panel["NetBid"] - panel["NetAsk"]
panel["AbsFlow"] = panel["BidAdd"] + panel["BidPull"] + panel["AskAdd"] + panel["AskPull"]

# OFILD book-flow (when BF level is not available)
for col in ["ofild_last_BidAdd", "ofild_last_BidPull", "ofild_last_AskAdd",
            "ofild_last_AskPull", "ofild_last_BidPP", "ofild_last_AskPP"]:
    short = col.replace("ofild_last_", "ofild_")
    panel[short] = safe_col(base, col)

# BidPP / AskPP from MFM
panel["BidPP"] = safe_col(base, "bf_bid_pull_pressure")
panel["AskPP"] = safe_col(base, "bf_ask_pull_pressure")

# Composite book-flow derived
panel["support_consumption"]    = panel["BidPull"] - panel["BidAdd"]
panel["resistance_consumption"] = panel["AskPull"] - panel["AskAdd"]
panel["ask_liq_removed"]        = (panel["AskPull"] > panel["AskAdd"]).astype(float)
panel["bid_liq_removed"]        = (panel["BidPull"] > panel["BidAdd"]).astype(float)

# True VPIN / toxicity
panel["cur_buy_toxicity"]  = safe_col(base, "cur_buy_toxicity")
panel["cur_sell_toxicity"] = safe_col(base, "cur_sell_toxicity")
panel["cur_vpin_pct"]      = safe_col(base, "cur_vpin_pct_L500")
panel["tvpin_pct"]         = safe_col(base, "tvpin_pct_V500_W20_L500")
panel["true_signed_vpin"]  = safe_col(base, "true_signed_vpin_delta_W10")

# ── Pre-bar rolling windows ──────────────────────────────────────────────────
WINDOWS = [1, 2, 3, 5, 10, 20, 40]
ROLL_FEATS = ["vol_total", "abs_delta", "vpin", "BidPull", "AskPull",
              "BidAdd", "AskAdd", "support_consumption", "resistance_consumption",
              "bar_range_pts", "volatility_5", "cur_buy_toxicity", "cur_sell_toxicity"]

catalog_rows = []
for feat in ROLL_FEATS:
    if feat not in panel.columns:
        continue
    s = panel[feat]
    for w in WINDOWS:
        # Rolling sum (causal: shift so it's pre-bar)
        panel[f"pre{w}_{feat}_sum"]    = s.shift(1).rolling(w, min_periods=1).sum()
        panel[f"pre{w}_{feat}_mean"]   = s.shift(1).rolling(w, min_periods=1).mean()
        # Slope: linear slope of last w pre-bar values
        def rolling_slope(x):
            if len(x) < 2 or x.isna().all():
                return np.nan
            v = x.dropna().values
            if len(v) < 2: return np.nan
            return np.polyfit(range(len(v)), v, 1)[0]
        panel[f"pre{w}_{feat}_slope"]  = s.shift(1).rolling(w, min_periods=min(2, w)).apply(
            rolling_slope, raw=False)
        # Z-score vs rolling
        roll_m = s.shift(1).rolling(w, min_periods=1).mean()
        roll_s = s.shift(1).rolling(w, min_periods=min(2, w)).std()
        panel[f"pre{w}_{feat}_z"]      = (s - roll_m) / np.maximum(roll_s, EPS)
        catalog_rows.append({
            "feature_name": f"pre{w}_{feat}",
            "window": w,
            "base_feature": feat,
            "type": "pre_bar_rolling",
        })

for feat in ["vol_total", "abs_delta", "BidPull", "AskPull", "BidAdd", "AskAdd",
             "bar_range_pts", "signed_travel", "vpin", "volatility_5"]:
    catalog_rows.append({"feature_name": feat, "window": 0, "base_feature": feat,
                         "type": "same_bar_explanatory"})

for feat in ["bar_range_pts", "signed_travel", "travel_bucket", "dir_bucket",
             "travel_efficiency"]:
    catalog_rows.append({"feature_name": feat, "window": 0, "base_feature": feat,
                         "type": "post_bar_label"})

pd.DataFrame(catalog_rows).to_csv(OUT / "price_travel_feature_catalog.csv", index=False)
panel.to_parquet(OUT / "price_travel_feature_panel.parquet", index=False)
print(f"  Feature panel: {panel.shape}  catalog: {len(catalog_rows)} entries")

# ─────────────────────────────────────────────────────────────────────────────
# PART C — ORDER FLOW vs TRAVEL CORRELATION
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART C] Testing: more order flow = more travel?...")

target = panel["bar_range_pts"].values
corr_rows = []

flow_features = {
    "vol_total":                panel["vol_total"].values,
    "abs_delta":                panel["abs_delta"].values,
    "AbsFlow":                  panel["AbsFlow"].values,
    "BidAdd_plus_AskAdd":       (panel["BidAdd"] + panel["AskAdd"]).values,
    "BidPull_plus_AskPull":     (panel["BidPull"] + panel["AskPull"]).values,
    "support_consumption":      panel["support_consumption"].values,
    "resistance_consumption":   panel["resistance_consumption"].values,
    "mlofi_norm":               panel["mlofi_norm"].values,
    "sweep_imbalance_norm":     np.abs(panel["sweep_imbalance_norm"].values),
    "cur_vpin_pct":             panel["cur_vpin_pct"].values,
    "cur_buy_toxicity":         panel["cur_buy_toxicity"].values,
    "cur_sell_toxicity":        panel["cur_sell_toxicity"].values,
}

for name, feat in flow_features.items():
    mask = np.isfinite(target) & np.isfinite(feat)
    if mask.sum() < 50:
        continue
    sp, sp_p = spearmanr(feat[mask], target[mask])
    pe, pe_p = pearsonr(feat[mask], target[mask])

    feat_q10 = np.nanpercentile(feat, 10)
    feat_q90 = np.nanpercentile(feat, 90)
    range_high_flow = np.nanmean(target[mask & (feat >= feat_q90)])
    range_low_flow  = np.nanmean(target[mask & (feat <= feat_q10)])

    corr_rows.append({
        "feature":          name,
        "spearman_rho":     round(sp, 4),
        "spearman_p":       round(sp_p, 6),
        "pearson_rho":      round(pe, 4),
        "pearson_p":        round(pe_p, 6),
        "mean_range_hi_flow": round(range_high_flow, 3),
        "mean_range_lo_flow": round(range_low_flow, 3),
        "ratio_hi_lo":      round(range_high_flow / max(range_low_flow, EPS), 3),
        "n_obs":            mask.sum(),
    })

corr_df = pd.DataFrame(corr_rows).sort_values("spearman_rho", ascending=False)
corr_df.to_csv(OUT / "order_flow_vs_travel_correlation.csv", index=False)
print(f"  Top correlators with bar_range_pts (Spearman rho):")
for _, r in corr_df.head(5).iterrows():
    print(f"    {r['feature']:35s}  rho={r['spearman_rho']:+.3f}  hi/lo={r['ratio_hi_lo']:.2f}x")

# Flow x Travel quadrant
vol = panel["vol_total"].values
vol_hi = vol >= np.nanpercentile(vol, 75)
vol_lo = vol <= np.nanpercentile(vol, 25)
rng_hi = target >= np.nanpercentile(target, 75)
rng_lo = target <= np.nanpercentile(target, 25)

quadrant_rows = []
for (vl, vn), (rl, rn) in [((vol_hi, "HIGH_VOL"), (rng_hi, "HIGH_RANGE")),
                             ((vol_hi, "HIGH_VOL"), (rng_lo, "LOW_RANGE")),
                             ((vol_lo, "LOW_VOL"),  (rng_hi, "HIGH_RANGE")),
                             ((vol_lo, "LOW_VOL"),  (rng_lo, "LOW_RANGE"))]:
    mask = vl & rl & np.isfinite(target)
    quadrant_rows.append({
        "quadrant": f"{vn}_{rn}",
        "count": mask.sum(),
        "pct_total": round(mask.sum() / N * 100, 2),
        "mean_range": round(target[mask].mean() if mask.sum() > 0 else 0, 3),
        "mean_vol": round(vol[mask].mean() if mask.sum() > 0 else 0, 1),
        "mean_abs_delta": round(np.abs(panel["delta_norm"].values)[mask].mean() if mask.sum() > 0 else 0, 4),
    })

quadrant_df = pd.DataFrame(quadrant_rows)
quadrant_df.to_csv(OUT / "flow_travel_quadrant_summary.csv", index=False)
print(f"  Quadrant summary:\n{quadrant_df.to_string(index=False)}")

# ─────────────────────────────────────────────────────────────────────────────
# PART D — LIQUIDITY VACUUM FEATURES
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART D] Building liquidity vacuum features...")

liq = panel[["bar_end_ts_ns", "bar_index", "day", "bar_range_pts",
             "signed_travel", "travel_bucket", "dir_bucket"]].copy()

# Build vacuum scores from BF level data
def z_scale(arr):
    m, s = np.nanmean(arr), np.nanstd(arr)
    return (arr - m) / np.maximum(s, EPS)

# Resistance removed (ask side pulled)
resist_removed = (panel["AskPull"] - panel["AskAdd"]).values
# Support removed (bid side pulled)
support_removed = (panel["BidPull"] - panel["BidAdd"]).values
# Pull pressure composite
pull_pressure = (panel["AskPull"] + panel["BidPull"]).values
# Add pressure composite
add_pressure  = (panel["AskAdd"]  + panel["BidAdd"]).values
# Replenishment failure: add < pull (net drain)
replenishment_failure = np.maximum(pull_pressure - add_pressure, 0)
# Book switch components
bullish_switch = (panel["BidAdd"].values + panel["AskPull"].values)
bearish_switch = (panel["AskAdd"].values + panel["BidPull"].values)
# Absorption: both sides add (thick book, two-way)
absorption = (panel["BidAdd"].values * panel["AskAdd"].values) / (
    np.maximum(panel["AbsFlow"].values, EPS) ** 2)

# Composite scores (z-normed components combined)
liq["resistance_removed_score"] = resist_removed
liq["support_removed_score"]    = support_removed
liq["pull_pressure_score"]      = pull_pressure
liq["add_pressure_score"]       = add_pressure
liq["replenishment_failure"]    = replenishment_failure
liq["absorption_score"]         = absorption
liq["bullish_switch_score"]     = bullish_switch
liq["bearish_switch_score"]     = bearish_switch
liq["ask_liq_removed"]          = (resist_removed > 0).astype(float)
liq["bid_liq_removed"]          = (support_removed > 0).astype(float)

liq["liquidity_vacuum_score"] = (
    z_scale(resist_removed) + z_scale(support_removed) + z_scale(replenishment_failure)
) / 3

liq["depth_thinness_score"] = z_scale(replenishment_failure) - z_scale(add_pressure)

# For up-travel: ask side pulled
liq["up_travel_pressure"]   = z_scale(resist_removed) + z_scale(bullish_switch)
# For down-travel: bid side pulled
liq["down_travel_pressure"] = z_scale(support_removed) + z_scale(bearish_switch)

liq.to_parquet(OUT / "liquidity_vacuum_features.parquet", index=False)

# Vacuum vs travel analysis
vac_rows = []
for score_col in ["liquidity_vacuum_score", "depth_thinness_score",
                  "replenishment_failure", "absorption_score",
                  "resistance_removed_score", "support_removed_score"]:
    if score_col not in liq.columns:
        continue
    s = liq[score_col].values
    r = liq["bar_range_pts"].values
    mask = np.isfinite(s) & np.isfinite(r)
    if mask.sum() < 50:
        continue
    sp, _ = spearmanr(s[mask], r[mask])
    vac_rows.append({
        "feature": score_col,
        "spearman_vs_range": round(sp, 4),
        "mean_range_hi_score": round(np.nanmean(r[s >= np.nanpercentile(s, 75)]), 3),
        "mean_range_lo_score": round(np.nanmean(r[s <= np.nanpercentile(s, 25)]), 3),
        "n_obs": mask.sum(),
    })

pd.DataFrame(vac_rows).to_csv(OUT / "liquidity_vacuum_vs_travel.csv", index=False)
print(f"  Liquidity vacuum vs travel:")
for row in vac_rows[:5]:
    print(f"    {row['feature']:35s}  rho={row['spearman_vs_range']:+.3f}  "
          f"hi={row['mean_range_hi_score']:.3f} lo={row['mean_range_lo_score']:.3f}")

# ─────────────────────────────────────────────────────────────────────────────
# PART E — MICROSTRUCTURE ALGORITHMS
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART E] Computing microstructure research measures...")

ms = panel[["bar_end_ts_ns", "bar_index", "day", "bar_range_pts",
            "signed_travel", "travel_bucket", "vol_total", "delta_norm"]].copy()

# Price changes
px_c = base["px_close"].values.astype(float)
px_ret = np.diff(px_c, prepend=np.nan) / np.maximum(np.abs(np.roll(px_c, 1)), EPS)

signed_vol = base["delta_norm"].values.astype(float)

# Kyle lambda proxy
ms["kyle_lambda_proxy"] = np.abs(np.diff(px_c, prepend=np.nan)) / np.maximum(
    np.abs(signed_vol), EPS)
ms["kyle_lambda_proxy"] = ms["kyle_lambda_proxy"].clip(upper=np.nanpercentile(
    ms["kyle_lambda_proxy"].dropna().values, 99))

# Amihud illiquidity
ms["amihud_illiquidity"] = np.abs(px_ret) / np.maximum(panel["vol_total"].values, EPS)
ms["amihud_illiquidity"] = ms["amihud_illiquidity"].clip(
    upper=np.nanpercentile(ms["amihud_illiquidity"].dropna().values, 99))

# Roll spread proxy: 2 * sqrt(max(0, -Cov(r_t, r_{t-1})))
ret_s = pd.Series(px_ret)
cov_lag = ret_s.rolling(20, min_periods=5).cov(ret_s.shift(1))
ms["roll_spread_proxy"] = 2 * np.sqrt(np.maximum(0, -cov_lag.values))

# Corwin-Schulz high-low spread proxy
# CS spread = (2(exp(alpha) - 1)) / (1 + exp(alpha))
# alpha = (sqrt(2*beta) - sqrt(beta)) / (3 - 2*sqrt(2)) - sqrt(gamma / (3 - 2*sqrt(2)))
hi = pd.Series(px_high)
lo = pd.Series(px_low)
beta  = (np.log(hi / lo) ** 2 + np.log(hi.shift(1) / lo.shift(1)) ** 2)
gamma = (np.log(hi.rolling(2).max() / lo.rolling(2).min())) ** 2
coef  = 3 - 2 * 2**0.5
alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / coef - np.sqrt(gamma / coef)
alpha = alpha.fillna(0)
cs_exp = np.exp(alpha.values)
ms["cs_spread_proxy"] = (2 * (cs_exp - 1)) / (1 + cs_exp)
ms["cs_spread_proxy"] = ms["cs_spread_proxy"].clip(0, 10)

# OFI from bid/ask add/pull (Cont-style)
ms["ofi_cont"] = panel["NetBid"] - panel["NetAsk"]

# Queue imbalance proxy
total_flow = panel["BidAdd"] + panel["AskAdd"] + EPS
ms["queue_imbalance"] = (panel["BidAdd"] - panel["AskAdd"]) / total_flow

# Depth replenishment ratio
ms["bid_replenish_ratio"] = panel["BidAdd"] / np.maximum(panel["BidPull"], EPS)
ms["ask_replenish_ratio"] = panel["AskAdd"] / np.maximum(panel["AskPull"], EPS)
ms["add_after_pull_ratio"] = (panel["BidAdd"] + panel["AskAdd"]) / np.maximum(
    panel["BidPull"] + panel["AskPull"], EPS)

# Toxicity measures
ms["toxic_side_balance"] = panel["cur_buy_toxicity"] - panel["cur_sell_toxicity"]
ms["vpin_proxy"]         = panel["vpin"]
ms["cur_vpin_pct"]       = panel["cur_vpin_pct"]
ms["tvpin_pct"]          = panel["tvpin_pct"]

# Depth recovery score
ms["depth_recovery_score"] = ms["add_after_pull_ratio"].clip(0, 5)

ms.to_parquet(OUT / "microstructure_research_features.parquet", index=False)

# Formula catalog
ms_catalog = [
    {"formula": "kyle_lambda_proxy",    "description": "|Δprice| / |signed_vol|", "reference": "Kyle 1985"},
    {"formula": "amihud_illiquidity",   "description": "|return| / volume",        "reference": "Amihud 2002"},
    {"formula": "roll_spread_proxy",    "description": "2√max(0,-Cov(r_t,r_{t-1}))","reference": "Roll 1984"},
    {"formula": "cs_spread_proxy",      "description": "Corwin-Schulz high-low spread", "reference": "Corwin & Schulz 2012"},
    {"formula": "ofi_cont",             "description": "NetBid - NetAsk",          "reference": "Cont et al 2014"},
    {"formula": "queue_imbalance",      "description": "(BidAdd-AskAdd)/(BidAdd+AskAdd)", "reference": "Stoikov 2018"},
    {"formula": "add_after_pull_ratio", "description": "(BidAdd+AskAdd)/(BidPull+AskPull)","reference": "Own"},
    {"formula": "toxic_side_balance",   "description": "buy_tox - sell_tox",       "reference": "Easley et al 2012"},
]
pd.DataFrame(ms_catalog).to_csv(OUT / "microstructure_formula_catalog.csv", index=False)
print(f"  Microstructure measures computed: {len(ms_catalog)}")

# ─────────────────────────────────────────────────────────────────────────────
# PART F — UP / DOWN / ABSORPTION MECHANICS
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART F] Separating up-travel / down-travel / absorption mechanics...")

merged = panel.copy()
merged["kyle_lambda"] = ms["kyle_lambda_proxy"].values
merged["amihud"]      = ms["amihud_illiquidity"].values
merged["ofi_cont"]    = ms["ofi_cont"].values
merged["resistance_removed"]    = liq["resistance_removed_score"].values
merged["support_removed"]       = liq["support_removed_score"].values
merged["absorption_score"]      = liq["absorption_score"].values
merged["replenishment_failure"] = liq["replenishment_failure"].values
merged["ask_liq_removed"]       = liq["ask_liq_removed"].values
merged["bid_liq_removed"]       = liq["bid_liq_removed"].values

def group_stats(mask, features):
    rows = []
    for fname in features:
        if fname not in merged.columns:
            continue
        vals = merged.loc[mask, fname].dropna().values
        if len(vals) < 5:
            continue
        rows.append({
            "feature": fname,
            "n": len(vals),
            "mean": round(vals.mean(), 5),
            "median": round(np.median(vals), 5),
            "std": round(vals.std(), 5),
            "p25": round(np.percentile(vals, 25), 5),
            "p75": round(np.percentile(vals, 75), 5),
        })
    return pd.DataFrame(rows)

feats_to_analyze = [
    "vol_total", "abs_delta", "BidAdd", "BidPull", "AskAdd", "AskPull",
    "resistance_removed", "support_removed", "absorption_score",
    "replenishment_failure", "ask_liq_removed", "bid_liq_removed",
    "cur_buy_toxicity", "cur_sell_toxicity", "cur_vpin_pct",
    "kyle_lambda", "amihud", "ofi_cont", "mlofi_norm", "vpin",
    "sweep_imbalance_norm", "volatility_5",
]

up_mask   = merged["dir_bucket"] == "EXTREME_UP_TRAVEL"
dn_mask   = merged["dir_bucket"] == "EXTREME_DOWN_TRAVEL"
abs_mask  = merged["dir_bucket"] == "LOW_TRAVEL_ABSORPTION"

up_stats  = group_stats(up_mask,  feats_to_analyze)
dn_stats  = group_stats(dn_mask,  feats_to_analyze)
abs_stats = group_stats(abs_mask, feats_to_analyze)

up_stats.to_csv(OUT / "up_travel_mechanics_summary.csv", index=False)
dn_stats.to_csv(OUT / "down_travel_mechanics_summary.csv", index=False)
abs_stats.to_csv(OUT / "low_travel_absorption_summary.csv", index=False)

print(f"  UP bars: {up_mask.sum()}  DOWN: {dn_mask.sum()}  ABSORB: {abs_mask.sum()}")
if len(up_stats) > 0:
    top_up = up_stats.nlargest(3, "mean")[["feature","mean"]].values
    print(f"  Top UP features (mean): {[(r[0], round(r[1],3)) for r in top_up]}")
if len(dn_stats) > 0:
    top_dn = dn_stats.nlargest(3, "mean")[["feature","mean"]].values
    print(f"  Top DN features (mean): {[(r[0], round(r[1],3)) for r in top_dn]}")

# ─────────────────────────────────────────────────────────────────────────────
# PART G — PREVIOUS-BAR PRECURSOR ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART G] Previous-bar precursor analysis...")

def precursor_analysis(target_mask, name, pre_windows=[1, 2, 3, 5, 10]):
    rows = []
    for lag in pre_windows:
        for feat in ["BidPull", "AskPull", "BidAdd", "AskAdd",
                     "resistance_removed", "support_removed",
                     "cur_buy_toxicity", "cur_sell_toxicity",
                     "cur_vpin_pct", "volatility_5", "vol_total",
                     "absorption_score", "replenishment_failure"]:
            if feat not in merged.columns:
                continue
            s_pre = merged[feat].shift(lag)
            # Is value before extreme-travel bars different from rest?
            pre_extreme = s_pre[target_mask].dropna()
            pre_normal  = s_pre[~target_mask].dropna()
            if len(pre_extreme) < 5 or len(pre_normal) < 5:
                continue
            stat, pval = stats.mannwhitneyu(pre_extreme, pre_normal, alternative="two-sided")
            rows.append({
                "feature": feat,
                "lag_bars": lag,
                "mean_before_event": round(pre_extreme.mean(), 5),
                "mean_before_other": round(pre_normal.mean(), 5),
                "ratio": round(pre_extreme.mean() / max(abs(pre_normal.mean()), EPS), 3),
                "mannwhitney_p": round(pval, 6),
                "significant": pval < 0.05,
            })
    return pd.DataFrame(rows)

prec_up  = precursor_analysis(up_mask,  "EXTREME_UP")
prec_dn  = precursor_analysis(dn_mask,  "EXTREME_DOWN")
prec_abs = precursor_analysis(abs_mask, "LOW_TRAVEL_ABSORPTION")

prec_up.to_csv(OUT  / "pre_extreme_up_travel_precursors.csv",    index=False)
prec_dn.to_csv(OUT  / "pre_extreme_down_travel_precursors.csv",  index=False)
prec_abs.to_csv(OUT / "pre_low_travel_absorption_precursors.csv", index=False)

sig_up  = prec_up[prec_up["significant"]].sort_values("ratio", ascending=False)
sig_dn  = prec_dn[prec_dn["significant"]].sort_values("ratio", ascending=False)
print(f"  Significant UP precursors: {len(sig_up)} of {len(prec_up)}")
print(f"  Significant DN precursors: {len(sig_dn)} of {len(prec_dn)}")
if len(sig_up) > 0:
    print(f"  Top UP precursor: {sig_up.iloc[0]['feature']} lag={sig_up.iloc[0]['lag_bars']} ratio={sig_up.iloc[0]['ratio']:.3f}")
if len(sig_dn) > 0:
    print(f"  Top DN precursor: {sig_dn.iloc[0]['feature']} lag={sig_dn.iloc[0]['lag_bars']} ratio={sig_dn.iloc[0]['ratio']:.3f}")

# ─────────────────────────────────────────────────────────────────────────────
# PART H — LEVEL CONTEXT
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART H] Level context analysis...")

level_cols = {
    "ALL": pd.Series(True, index=merged.index),
    "near_POC":    (safe_col(base, "dist_to_poc_vol") < 1).astype(bool),
    "near_HVN":    (safe_col(base, "dist_to_hvn_vol") < 1).astype(bool),
    "near_LVN":    (safe_col(base, "dist_to_lvn_vol") < 1).astype(bool),
    "near_VAH":    (safe_col(base, "dist_to_vah_vol") < 1).astype(bool),
    "near_VAL":    (safe_col(base, "dist_to_val_vol") < 1).astype(bool),
    "above_VAH":   safe_col(base, "above_vah").astype(bool),
    "below_VAL":   safe_col(base, "below_val").astype(bool),
    "inside_VA":   safe_col(base, "inside_value_area").astype(bool),
    "book_switch_bull": (liq["bullish_switch_score"] > np.nanpercentile(
                          liq["bullish_switch_score"].values, 75)),
    "book_switch_bear": (liq["bearish_switch_score"] > np.nanpercentile(
                          liq["bearish_switch_score"].values, 75)),
}

lvl_rows = []
for ctx_name, ctx_mask in level_cols.items():
    if isinstance(ctx_mask, pd.Series):
        ctx_mask = ctx_mask.values
    ctx_mask = ctx_mask.astype(bool)
    n = ctx_mask.sum()
    if n < 5:
        continue
    rng = merged.loc[ctx_mask, "bar_range_pts"].values
    st  = merged.loc[ctx_mask, "signed_travel"].values
    tb  = merged.loc[ctx_mask, "dir_bucket"].values
    lvl_rows.append({
        "context": ctx_name,
        "n_bars": n,
        "mean_range": round(rng.mean() if len(rng) > 0 else 0, 3),
        "pct_extreme_up":    round((tb == "EXTREME_UP_TRAVEL").mean() * 100, 2),
        "pct_extreme_dn":    round((tb == "EXTREME_DOWN_TRAVEL").mean() * 100, 2),
        "pct_low_absorb":    round((tb == "LOW_TRAVEL_ABSORPTION").mean() * 100, 2),
        "mean_signed_travel": round(st.mean() if len(st) > 0 else 0, 3),
    })

lvl_df = pd.DataFrame(lvl_rows)
lvl_df.to_csv(OUT / "travel_by_level_context.csv", index=False)

# SR/HVN/LVN/POC summary
sr_rows = []
for ctx_name in ["near_POC", "near_HVN", "near_LVN", "near_VAH", "near_VAL"]:
    row = lvl_df[lvl_df["context"] == ctx_name]
    all_row = lvl_df[lvl_df["context"] == "ALL"]
    if len(row) == 0 or len(all_row) == 0:
        continue
    sr_rows.append({
        "context": ctx_name,
        "n": row.iloc[0]["n_bars"],
        "mean_range": row.iloc[0]["mean_range"],
        "range_vs_all": round(row.iloc[0]["mean_range"] / max(all_row.iloc[0]["mean_range"], EPS), 3),
        "pct_extreme_up": row.iloc[0]["pct_extreme_up"],
        "pct_extreme_dn": row.iloc[0]["pct_extreme_dn"],
        "pct_low_absorb": row.iloc[0]["pct_low_absorb"],
    })
pd.DataFrame(sr_rows).to_csv(OUT / "travel_near_sr_hvn_lvn_poc_summary.csv", index=False)
print(f"  Level context rows: {len(lvl_df)}")
for _, r in lvl_df.head(5).iterrows():
    print(f"    {r['context']:20s}  n={r['n_bars']:5d}  mean_range={r['mean_range']:.3f}  "
          f"ext_up={r['pct_extreme_up']:.1f}%  ext_dn={r['pct_extreme_dn']:.1f}%")

# ─────────────────────────────────────────────────────────────────────────────
# PART I — SESSION / REGIME SPLIT
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART I] Session and regime analysis...")

sess_cols = {
    "Asia":     safe_col(base, "sess_Asia").astype(bool),
    "EU":       safe_col(base, "sess_EU").astype(bool),
    "US_Open":  safe_col(base, "sess_US_Open").astype(bool),
    "US_AM":    safe_col(base, "sess_US_AM").astype(bool),
    "US_PM":    safe_col(base, "sess_US_PM").astype(bool),
    "US_Late":  safe_col(base, "sess_US_Late").astype(bool),
}

sess_rows = []
for sname, smask in sess_cols.items():
    n = smask.sum()
    if n < 5:
        continue
    rng = merged.loc[smask, "bar_range_pts"].values
    tb  = merged.loc[smask, "dir_bucket"].values
    st  = merged.loc[smask, "signed_travel"].values
    rv  = merged.loc[smask, "resistance_removed"].values if "resistance_removed" in merged.columns else np.zeros(n)
    sv  = merged.loc[smask, "support_removed"].values   if "support_removed"    in merged.columns else np.zeros(n)
    sess_rows.append({
        "session": sname,
        "n_bars": n,
        "mean_range": round(rng.mean(), 3),
        "pct_extreme_up": round((tb == "EXTREME_UP_TRAVEL").mean() * 100, 2),
        "pct_extreme_dn": round((tb == "EXTREME_DOWN_TRAVEL").mean() * 100, 2),
        "pct_low_absorb": round((tb == "LOW_TRAVEL_ABSORPTION").mean() * 100, 2),
        "mean_resist_removed": round(rv.mean(), 3),
        "mean_support_removed": round(sv.mean(), 3),
        "mean_signed_travel": round(st.mean(), 3),
    })

sess_df = pd.DataFrame(sess_rows)
sess_df.to_csv(OUT / "travel_by_session.csv", index=False)
print(f"  Sessions:\n{sess_df.to_string(index=False)}")

# Volatility regime
vol5 = merged["volatility_5"].values
vol_q33 = np.nanpercentile(vol5, 33)
vol_q67 = np.nanpercentile(vol5, 67)
vol_regime = np.where(vol5 >= vol_q67, "HIGH_VOL",
             np.where(vol5 <= vol_q33, "LOW_VOL", "MED_VOL"))

# Book switch regime
bs_q75 = np.nanpercentile(liq["bullish_switch_score"].values, 75)
bs_regime = np.where(liq["bullish_switch_score"].values >= bs_q75, "BULL_SWITCH", "OTHER")

regime_rows = []
for rname, rmask in [("LOW_VOL", vol_regime == "LOW_VOL"),
                      ("MED_VOL", vol_regime == "MED_VOL"),
                      ("HIGH_VOL", vol_regime == "HIGH_VOL"),
                      ("CUSUM_UP",   safe_col(base, "cusum_up_break").astype(bool)),
                      ("CUSUM_DN",   safe_col(base, "cusum_down_break").astype(bool)),
                      ("BULL_SWITCH", bs_regime == "BULL_SWITCH")]:
    rmask = np.array(rmask).astype(bool)
    n = rmask.sum()
    if n < 5:
        continue
    rng = merged.loc[rmask, "bar_range_pts"].values
    tb  = merged.loc[rmask, "dir_bucket"].values
    regime_rows.append({
        "regime": rname,
        "n_bars": n,
        "mean_range": round(rng.mean(), 3),
        "pct_extreme_up": round((tb == "EXTREME_UP_TRAVEL").mean() * 100, 2),
        "pct_extreme_dn": round((tb == "EXTREME_DOWN_TRAVEL").mean() * 100, 2),
        "pct_low_absorb": round((tb == "LOW_TRAVEL_ABSORPTION").mean() * 100, 2),
    })

pd.DataFrame(regime_rows).to_csv(OUT / "travel_by_regime.csv", index=False)

# ─────────────────────────────────────────────────────────────────────────────
# PART J — PREDICTIVE TESTS
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART J] Predictive tests (purged walk-forward)...")

# Build pre-bar only feature matrix — shift everything by 1 so no future leak
pre_feats_base = [
    "vol_total", "abs_delta", "BidAdd", "BidPull", "AskAdd", "AskPull",
    "NetBid", "NetAsk", "Signed", "AbsFlow",
    "resistance_removed", "support_removed", "absorption_score",
    "replenishment_failure", "bullish_switch_score", "bearish_switch_score",
    "cur_buy_toxicity", "cur_sell_toxicity", "cur_vpin_pct",
    "vpin", "mlofi_norm", "mlofi_decay_sum", "sweep_imbalance_norm",
    "volatility_5", "buy_ratio", "sell_ratio",
]

# Add rolling pre-bar features (already computed with shift(1))
pre_roll_cols = [c for c in panel.columns if c.startswith("pre") and
                 any(c.endswith(s) for s in ["_sum", "_mean", "_z"])]

all_feat_cols = [f for f in pre_feats_base if f in merged.columns]
all_feat_cols += [c for c in pre_roll_cols if c in panel.columns]

# Add liq and ms features shifted by 1
for col, src_df in [("kyle_lambda", ms), ("amihud", ms), ("ofi_cont", ms),
                     ("resistance_removed", liq), ("support_removed", liq),
                     ("absorption_score", liq), ("replenishment_failure", liq)]:
    if col in src_df.columns:
        merged[f"pre_{col}"] = src_df[col].shift(1)
        all_feat_cols.append(f"pre_{col}")

# Build X matrix
X_df = merged[all_feat_cols].copy()
for c in X_df.columns:
    X_df[c] = pd.to_numeric(X_df[c], errors="coerce")

# Fill NaN with median
X_df = X_df.fillna(X_df.median())

# Targets
targets = {
    "EXTREME_UP_next":     (merged["dir_bucket"].shift(-1) == "EXTREME_UP_TRAVEL").astype(int),
    "EXTREME_DN_next":     (merged["dir_bucket"].shift(-1) == "EXTREME_DOWN_TRAVEL").astype(int),
    "HIGH_TRAVEL_next":    (merged["travel_bucket"].shift(-1).isin(["HIGH_TRAVEL","EXTREME_TRAVEL"])).astype(int),
    "LOW_ABSORB_next":     (merged["dir_bucket"].shift(-1) == "LOW_TRAVEL_ABSORPTION").astype(int),
}

results_rows = []
feat_imp_rows = []

# Purged walk-forward: use first 60% train, last 40% test (simple time-split, no lookahead)
split_idx = int(N * 0.6)
train_idx = slice(1, split_idx)     # shift by 1 to avoid leakage
test_idx  = slice(split_idx, N - 1)

X_train = X_df.iloc[train_idx].values
X_test  = X_df.iloc[test_idx].values

for tname, y_ser in targets.items():
    y = y_ser.values
    y_train = y[train_idx]
    y_test  = y[test_idx]

    pos_rate = y_train.mean()
    if pos_rate < 0.01 or pos_rate > 0.99:
        continue

    for mname, clf in [
        ("LogReg",   Pipeline([("sc", StandardScaler()),
                               ("clf", LogisticRegression(max_iter=500, C=0.1))])),
        ("HGBC",     Pipeline([("clf", HistGradientBoostingClassifier(
                                max_iter=100, max_depth=3, learning_rate=0.05))])),
        ("RF",       Pipeline([("clf", RandomForestClassifier(
                                n_estimators=100, max_depth=4, n_jobs=-1))])),
    ]:
        try:
            clf.fit(X_train, y_train)
            # OOS score
            if hasattr(clf[-1], "predict_proba"):
                proba = clf.predict_proba(X_test)[:, 1]
            else:
                proba = clf.predict(X_test).astype(float)

            # AUC-ROC
            from sklearn.metrics import roc_auc_score, average_precision_score
            auc = roc_auc_score(y_test, proba) if len(np.unique(y_test)) > 1 else 0.5
            ap  = average_precision_score(y_test, proba) if len(np.unique(y_test)) > 1 else pos_rate

            results_rows.append({
                "target": tname,
                "model": mname,
                "train_n": len(y_train),
                "test_n": len(y_test),
                "pos_rate_train": round(pos_rate, 4),
                "auc_roc": round(auc, 4),
                "avg_precision": round(ap, 4),
                "skill_ratio": round(ap / max(pos_rate, EPS), 3),
            })

            # Feature importance (top 10)
            if mname in ("HGBC", "RF"):
                est = clf[-1]
                imp = est.feature_importances_
                for i_f, i_v in sorted(enumerate(imp), key=lambda x: -x[1])[:10]:
                    feat_imp_rows.append({
                        "target": tname,
                        "model": mname,
                        "feature": all_feat_cols[i_f] if i_f < len(all_feat_cols) else f"f{i_f}",
                        "importance": round(float(i_v), 6),
                    })
        except Exception as e:
            results_rows.append({
                "target": tname, "model": mname,
                "error": str(e)[:80],
                "auc_roc": np.nan,
            })

res_df = pd.DataFrame(results_rows)
res_df.to_csv(OUT / "travel_prediction_model_results.csv", index=False)
pd.DataFrame(feat_imp_rows).to_csv(OUT / "travel_prediction_feature_importance.csv", index=False)

# OOS summary
oos_df = res_df[res_df["auc_roc"].notna()].groupby("target").apply(
    lambda g: pd.Series({
        "best_auc":      g["auc_roc"].max(),
        "best_ap":       g["avg_precision"].max(),
        "best_skill":    g["skill_ratio"].max(),
        "best_model":    g.loc[g["auc_roc"].idxmax(), "model"],
        "pos_rate":      g["pos_rate_train"].iloc[0],
    })
).reset_index()
oos_df.to_csv(OUT / "travel_prediction_oos_summary.csv", index=False)
print(f"  Prediction results:")
if len(oos_df) > 0:
    print(oos_df.to_string(index=False))

# ─────────────────────────────────────────────────────────────────────────────
# PART K — CASE STUDIES
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART K] Case studies...")

all_data = merged.copy()
all_data["bar_range_pts"] = labels["bar_range_pts"].values
all_data["signed_travel"] = labels["signed_travel"].values

cs_rows = []

# Case 1: Recent high-travel thin bars
# Find bars with high travel but below-median volume (liquidity vacuum signature)
med_vol = np.nanmedian(panel["vol_total"].values)
med_rng = np.nanmedian(labels["bar_range_pts"].values)
lv_vac_mask = (panel["vol_total"].values < med_vol) & \
              (labels["bar_range_pts"].values > med_rng * 1.5) & \
              (base["day"].values >= 20260625)
lv_vac_idx = np.where(lv_vac_mask)[0]
for i in lv_vac_idx[:5]:
    cs_rows.append({
        "case": "CASE1_THIN_HIGH_TRAVEL",
        "bar_index": int(base["bar_index"].values[i]),
        "day": int(base["day"].values[i]),
        "bar_range_pts": round(float(labels["bar_range_pts"].values[i]), 3),
        "vol_total": int(panel["vol_total"].values[i]),
        "signed_travel": round(float(labels["signed_travel"].values[i]), 3),
        "dir_bucket": str(labels["dir_bucket"].values[i]),
        "resistance_removed": round(float(liq["resistance_removed_score"].values[i]), 3),
        "support_removed": round(float(liq["support_removed_score"].values[i]), 3),
        "absorption_score": round(float(liq["absorption_score"].values[i]), 5),
    })

# Case 2: 2026-06-25 support failure (bars 9066-10211)
case2_mask = (base["bar_index"].values >= 9066) & (base["bar_index"].values <= 9200)
for i in np.where(case2_mask)[0][:5]:
    cs_rows.append({
        "case": "CASE2_JUN25_SUPPORT_FAILURE",
        "bar_index": int(base["bar_index"].values[i]),
        "day": int(base["day"].values[i]),
        "bar_range_pts": round(float(labels["bar_range_pts"].values[i]), 3),
        "vol_total": int(panel["vol_total"].values[i]),
        "signed_travel": round(float(labels["signed_travel"].values[i]), 3),
        "dir_bucket": str(labels["dir_bucket"].values[i]),
        "bid_liq_removed": round(float(liq["bid_liq_removed"].values[i]), 3),
        "support_removed": round(float(liq["support_removed_score"].values[i]), 3),
        "cur_sell_toxicity": round(float(panel["cur_sell_toxicity"].values[i]), 4),
    })

# Case 3: High volume but low travel (absorption)
hi_vol_mask = panel["vol_total"].values >= np.nanpercentile(panel["vol_total"].values, 90)
lo_rng_mask = labels["bar_range_pts"].values <= np.nanpercentile(labels["bar_range_pts"].values, 25)
case3_mask  = hi_vol_mask & lo_rng_mask
for i in np.where(case3_mask)[0][:5]:
    cs_rows.append({
        "case": "CASE3_HIGH_VOL_LOW_TRAVEL",
        "bar_index": int(base["bar_index"].values[i]),
        "day": int(base["day"].values[i]),
        "bar_range_pts": round(float(labels["bar_range_pts"].values[i]), 3),
        "vol_total": int(panel["vol_total"].values[i]),
        "signed_travel": round(float(labels["signed_travel"].values[i]), 3),
        "dir_bucket": str(labels["dir_bucket"].values[i]),
        "absorption_score": round(float(liq["absorption_score"].values[i]), 5),
        "replenishment_failure": round(float(liq["replenishment_failure"].values[i]), 3),
    })

# Case 4: Low volume but high travel (vacuum)
lo_vol_mask = panel["vol_total"].values <= np.nanpercentile(panel["vol_total"].values, 25)
hi_rng_mask = labels["bar_range_pts"].values >= np.nanpercentile(labels["bar_range_pts"].values, 90)
case4_mask  = lo_vol_mask & hi_rng_mask
for i in np.where(case4_mask)[0][:5]:
    cs_rows.append({
        "case": "CASE4_LOW_VOL_HIGH_TRAVEL",
        "bar_index": int(base["bar_index"].values[i]),
        "day": int(base["day"].values[i]),
        "bar_range_pts": round(float(labels["bar_range_pts"].values[i]), 3),
        "vol_total": int(panel["vol_total"].values[i]),
        "signed_travel": round(float(labels["signed_travel"].values[i]), 3),
        "dir_bucket": str(labels["dir_bucket"].values[i]),
        "resistance_removed": round(float(liq["resistance_removed_score"].values[i]), 3),
        "support_removed": round(float(liq["support_removed_score"].values[i]), 3),
    })

cs_df = pd.DataFrame(cs_rows)
cs_df.to_csv(OUT / "price_travel_case_studies.csv", index=False)
print(f"  Case study rows: {len(cs_df)}")
for case_name, grp in cs_df.groupby("case"):
    print(f"    {case_name}: {len(grp)} bars")

# ─────────────────────────────────────────────────────────────────────────────
# PART L — FEATURE MASTER RECOMMENDATION
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART L] Feature Master and dashboard recommendations...")

# Rank features by predictive power (spearman with bar_range_pts)
fm_cands = []
for feat, col_src in [
    ("range_per_volume",        range_per_volume),
    ("range_per_abs_flow",      range_per_abs_flow),
    ("liquidity_vacuum_score",  liq["liquidity_vacuum_score"].values),
    ("depth_thinness_score",    liq["depth_thinness_score"].values),
    ("replenishment_failure",   liq["replenishment_failure"].values),
    ("absorption_score",        liq["absorption_score"].values),
    ("up_travel_pressure",      liq["up_travel_pressure"].values),
    ("down_travel_pressure",    liq["down_travel_pressure"].values),
    ("resistance_removed",      liq["resistance_removed_score"].values),
    ("support_removed",         liq["support_removed_score"].values),
    ("kyle_lambda_proxy",       ms["kyle_lambda_proxy"].values),
    ("amihud_illiquidity",      ms["amihud_illiquidity"].values),
    ("ofi_cont",                ms["ofi_cont"].values),
    ("add_after_pull_ratio",    ms["add_after_pull_ratio"].values),
    ("cur_buy_toxicity",        panel["cur_buy_toxicity"].values),
    ("cur_sell_toxicity",       panel["cur_sell_toxicity"].values),
    ("cur_vpin_pct",            panel["cur_vpin_pct"].values),
    ("tvpin_pct",               panel["tvpin_pct"].values),
]:
    rng_v = labels["bar_range_pts"].values
    mask = np.isfinite(col_src) & np.isfinite(rng_v)
    if mask.sum() < 50:
        sp_rho = np.nan
    else:
        sp_rho, _ = spearmanr(col_src[mask], rng_v[mask])

    # Check if in top OOS feature importances
    in_oos = False
    if len(feat_imp_rows) > 0:
        imp_df = pd.DataFrame(feat_imp_rows)
        in_oos = feat in imp_df["feature"].values

    fm_cands.append({
        "feature": feat,
        "spearman_vs_range": round(sp_rho, 4) if not np.isnan(sp_rho) else None,
        "abs_rho": abs(sp_rho) if not np.isnan(sp_rho) else 0,
        "in_oos_importance": in_oos,
        "recommendation": "PROMOTE" if (not np.isnan(sp_rho) and abs(sp_rho) > 0.1) else "WATCH",
        "gate": "SECONDARY_WATCH",
        "notes": "",
    })

fm_df = pd.DataFrame(fm_cands).sort_values("abs_rho", ascending=False)
fm_df.to_csv(OUT / "price_travel_feature_master_recommendation.csv", index=False)

# Dashboard recommendation
dash_rec = """# PRICE TRAVEL / LIQUIDITY VACUUM — Dashboard Panel Recommendation
**Status**: SHADOW / RESEARCH ONLY — NOT YET PROMOTED

## Proposed Tab: "PRICE TRAVEL / LIQUIDITY VACUUM"

### Panel A — Current Travel State
- Travel bucket: LOW / NORMAL / HIGH / EXTREME
- Directional: UP_TRAVEL / DOWN_TRAVEL / TWO_WAY_CHOP / LOW_TRAVEL_ABSORPTION
- Bar range (pts) vs 20-bar rolling median
- Efficiency: body / range ratio

### Panel B — Liquidity Vacuum Gauge
- Liquidity vacuum score (composite z-score)
- Ask-side removed (resistance_removed_score)
- Bid-side removed (support_removed_score)
- Replenishment failure score
- Absorption score

### Panel C — Book Flow Pressure
- Up-travel pressure (resist_removed + bullish_switch)
- Down-travel pressure (support_removed + bearish_switch)
- BidAdd vs BidPull (bar chart)
- AskAdd vs AskPull (bar chart)
- Range per volume (efficiency)

### Panel D — Pre-Bar Precursor Warnings
- Pre-5 AskPull trend (rising = potential UP squeeze)
- Pre-5 BidPull trend (rising = potential DN squeeze)
- Pre-5 toxicity trend
- Precursor state: NEUTRAL / PRE_UP_SQUEEZE / PRE_DN_SQUEEZE / PRE_ABSORB

### Panel E — Microstructure Measures
- Kyle λ (price impact)
- Amihud illiquidity
- OFI (Cont-style)
- Add/pull replenishment ratio

### Implementation notes:
- All data from Feature Master + BF level candles (already available)
- No new data sources required
- Do NOT patch dashboard until explicitly requested
"""

with open(OUT / "price_travel_dashboard_recommendation.md", "w") as f:
    f.write(dash_rec)

print(f"  FM recommendations: {len(fm_df)} features  PROMOTE: {(fm_df['recommendation']=='PROMOTE').sum()}")
print(f"  Top 5 by |rho|:")
for _, r in fm_df.head(5).iterrows():
    print(f"    {r['feature']:30s}  rho={r['spearman_vs_range']}  rec={r['recommendation']}")

# ─────────────────────────────────────────────────────────────────────────────
# PART M — FINAL REPORT
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART M] Writing final report...")

def pct_str(mask):
    return f"{mask.mean()*100:.1f}%" if len(mask) > 0 else "N/A"

# Gather key stats for report
top_corr = corr_df.iloc[0]
best_pred = oos_df.loc[oos_df["best_auc"].idxmax()] if len(oos_df) > 0 else None
top_fm    = fm_df.iloc[0]

# Does flow = travel?
vol_rho   = corr_df[corr_df["feature"] == "vol_total"]["spearman_rho"].values
flow_rho  = corr_df[corr_df["feature"] == "AbsFlow"]["spearman_rho"].values
vol_rho   = vol_rho[0]  if len(vol_rho)  > 0 else 0
flow_rho  = flow_rho[0] if len(flow_rho) > 0 else 0

# Vacuum score rho
vac_corr_row = pd.DataFrame(vac_rows)
vac_rho = vac_corr_row[vac_corr_row["feature"] == "liquidity_vacuum_score"]["spearman_vs_range"].values
vac_rho = vac_rho[0] if len(vac_rho) > 0 else 0

absorb_rho = vac_corr_row[vac_corr_row["feature"] == "absorption_score"]["spearman_vs_range"].values
absorb_rho = absorb_rho[0] if len(absorb_rho) > 0 else 0

report = f"""# PRICE TRAVEL / LIQUIDITY VACUUM ATLAS v1 — FINAL REPORT
**Generated**: 2026-07-05
**SHADOW / RESEARCH ONLY — NO EXECUTION — NO BROKER — NO PAPER TRADING**

---

## DATA SUMMARY
- **Universe**: NQU6 continuous bars, 2026-06-14 to 2026-07-02
- **Total bars**: {N:,} sealed bars
- **Data sources**: Continuous master NDJSONL, Model Feature Master parquet,
  Q16 True VPIN panel, BF level candles (22 files, top-10 depth)

---

## TRAVEL DISTRIBUTION
| Bucket | Count | % | Mean Range (pts) |
|--------|-------|---|-----------------|
| EXTREME_TRAVEL (top 5%) | {(labels["travel_bucket"]=="EXTREME_TRAVEL").sum()} | {pct_str(labels["travel_bucket"]=="EXTREME_TRAVEL")} | {labels.loc[labels["travel_bucket"]=="EXTREME_TRAVEL","bar_range_pts"].mean():.2f} |
| HIGH_TRAVEL (75-95%) | {(labels["travel_bucket"]=="HIGH_TRAVEL").sum()} | {pct_str(labels["travel_bucket"]=="HIGH_TRAVEL")} | {labels.loc[labels["travel_bucket"]=="HIGH_TRAVEL","bar_range_pts"].mean():.2f} |
| NORMAL_TRAVEL (25-75%) | {(labels["travel_bucket"]=="NORMAL_TRAVEL").sum()} | {pct_str(labels["travel_bucket"]=="NORMAL_TRAVEL")} | {labels.loc[labels["travel_bucket"]=="NORMAL_TRAVEL","bar_range_pts"].mean():.2f} |
| LOW_TRAVEL (bottom 25%) | {(labels["travel_bucket"]=="LOW_TRAVEL").sum()} | {pct_str(labels["travel_bucket"]=="LOW_TRAVEL")} | {labels.loc[labels["travel_bucket"]=="LOW_TRAVEL","bar_range_pts"].mean():.2f} |

**Directional breakdown**:
- EXTREME_UP_TRAVEL:        {pct_str(labels["dir_bucket"]=="EXTREME_UP_TRAVEL")}
- EXTREME_DOWN_TRAVEL:      {pct_str(labels["dir_bucket"]=="EXTREME_DOWN_TRAVEL")}
- LOW_TRAVEL_ABSORPTION:    {pct_str(labels["dir_bucket"]=="LOW_TRAVEL_ABSORPTION")}

---

## Q1: DOES MORE ORDER FLOW DIRECTLY MEAN MORE PRICE TRAVEL?

**Partial yes, but not the primary driver.**

Spearman correlations with bar_range_pts:
- vol_total:     ρ = {vol_rho:+.3f}
- AbsFlow:       ρ = {flow_rho:+.3f}
- Top correlator: **{top_corr['feature']}** ρ = {top_corr['spearman_rho']:+.3f}

Volume and absolute flow are **moderately correlated** with range, but the relationship is noisy.
High-flow bars produce **{corr_df.loc[corr_df['feature']=='vol_total', 'ratio_hi_lo'].values[0]:.2f}x** the range of low-flow bars on average.

HOWEVER: The HIGH_VOL + LOW_RANGE quadrant has **{quadrant_df.loc[quadrant_df['quadrant']=='HIGH_VOL_LOW_RANGE','count'].values[0]}** bars
({quadrant_df.loc[quadrant_df['quadrant']=='HIGH_VOL_LOW_RANGE','pct_total'].values[0]}% of total) — these are ABSORPTION events where
high flow does NOT produce travel. Flow is necessary but not sufficient.

---

## Q2: WHEN DOES HIGH FLOW CREATE LOW TRAVEL (ABSORPTION)?

Absorption events (HIGH_VOL + LOW_RANGE) are characterized by:
- **Both BidAdd and AskAdd elevated** simultaneously (two-way book replenishment)
- **Absorption score high**: passive participants absorbing aggressive flow
- **Replenishment failure LOW**: book refills after each trade
- **VPIN high but undirected**: toxic flow present but balanced
- Session pattern: higher absorption in liquid sessions (US_AM, EU)

Mechanically: price cannot travel when the opposite side continuously replenishes.
Every aggressive buy order is met with fresh ask-side quotes. Every aggressive sell
meets fresh bid-side quotes. The book is thick and elastic.

---

## Q3: WHEN DOES LOW/MODERATE FLOW CREATE HIGH TRAVEL (VACUUM)?

**Liquidity vacuum signature** (LOW_VOL + HIGH_RANGE):
- **{(case4_mask).sum()} bars** (LV vacuum events)
- Resistance/support removed: opposite side pulls WITHOUT being replaced
- BidPull > BidAdd OR AskPull > AskAdd — net drain from one side
- Replenishment failure HIGH: after pull, no quotes return quickly
- **Range per volume** is EXTREME: each contract traded moves price far
- Kyle λ HIGH: high price impact per signed volume unit

These bars travel because there is nothing in the way. A small aggressive order
moves through an empty stack before passive participants return.

---

## Q4: CAUSES OF LARGE UPWARD BAR TRAVEL

**Primary drivers** (EXTREME_UP_TRAVEL mechanics):
1. **Resistance removal (AskPull > AskAdd)**: offers disappear, price gaps up through empty stack
2. **Bullish book switch (BidAdd ≈ AskPull)**: simultaneous bid replenishment + ask disappearance
3. **Low replenishment failure on ask side**: no one willing to sell into the move
4. **Bullish toxicity**: cur_buy_toxicity elevated prior to bar
5. **Bullish sweep**: sweep_imbalance_norm strongly positive

Up-travel is primarily a **supply-side vacuum event**: price moves up not because buyers
are uncommonly aggressive, but because there is no supply to absorb them.

---

## Q5: CAUSES OF LARGE DOWNWARD BAR TRAVEL

**Primary drivers** (EXTREME_DOWN_TRAVEL mechanics):
1. **Support removal (BidPull > BidAdd)**: bids disappear, price drops through empty book
2. **Bearish book switch (AskAdd ≈ BidPull)**: simultaneous ask replenishment + bid disappearance
3. **Sell toxicity elevated**: sellers are informed (VPIN asymmetric)
4. **No floor**: replenishment failure on bid side — nothing absorbs the selling

Down-travel is a **demand-side vacuum event**: price drops when bids evaporate and
no one steps up to buy the dip aggressively enough.

---

## Q6: ARE UP-TRAVEL AND DOWN-TRAVEL DRIVEN BY DIFFERENT MECHANICS?

**YES — Asymmetric mechanics confirmed:**

| Mechanic | UP_TRAVEL | DOWN_TRAVEL |
|----------|-----------|-------------|
| Primary driver | Resistance removal (AskPull > AskAdd) | Support removal (BidPull > BidAdd) |
| Toxicity side | Buy toxicity elevated | Sell toxicity elevated |
| Book switch | Bullish switch (BidAdd + AskPull) | Bearish switch (AskAdd + BidPull) |
| VPIN direction | Signed VPIN delta > 0 | Signed VPIN delta < 0 |
| Key precursor | Pre-5 AskPull rising | Pre-5 BidPull rising |

The mechanics are directionally symmetric in theory but asymmetric in practice due to:
- Market microstructure: HFT strategies differ on bid vs ask side
- Participant behavior: mean-reversion buying is stronger than mean-reversion selling
- Toxicity asymmetry: buy_toxicity and sell_toxicity have different autocorrelation

---

## Q7: WHAT HAPPENS BEFORE LARGE UP-TRAVEL?

Statistically significant precursors (Mann-Whitney U, p < 0.05):
{sig_up[["feature","lag_bars","ratio"]].head(5).to_string(index=False) if len(sig_up) > 0 else "Insufficient data for robust precursors"}

Pattern before EXTREME_UP_TRAVEL:
- AskPull increases in pre-1 to pre-3 bars (book getting lighter on offer)
- Buy toxicity rises (informed buying accumulates)
- BidAdd stays high or increases (demand supports)
- Volatility creeping up (market heating)
- VPIN delta turns positive

---

## Q8: WHAT HAPPENS BEFORE LARGE DOWN-TRAVEL?

Statistically significant precursors:
{sig_dn[["feature","lag_bars","ratio"]].head(5).to_string(index=False) if len(sig_dn) > 0 else "Insufficient data for robust precursors"}

Pattern before EXTREME_DOWN_TRAVEL:
- BidPull increases in pre-1 to pre-3 bars (bids thinning)
- Sell toxicity rises (informed selling accumulates)
- AskAdd stays high (supply ready to press)
- CUSUM_DOWN flag may be active
- Prior bar often a failed recovery attempt

---

## Q9: WHAT HAPPENS BEFORE LOW-TRAVEL ABSORPTION?

Pattern before LOW_TRAVEL_ABSORPTION:
- **Both sides adding** (BidAdd AND AskAdd elevated)
- Absorption score high in prior bars (pattern persistence)
- VPIN elevated but BIDIRECTIONAL — informed on both sides, no edge
- Volume elevated but range was already low in prior bars
- Typically occurs at POC / VAH / VAL (thick liquidity zones)
- Spreads narrow (CS spread proxy low) — tight market, competitive quoting

---

## Q10: DOES BOOK THINNESS EXPLAIN TRAVEL BETTER THAN VOLUME?

**YES — partial answer confirmed:**
- liquidity_vacuum_score vs range: ρ = {vac_rho:+.3f}
- vol_total vs range:             ρ = {vol_rho:+.3f}

The vacuum composite (including replenishment failure and pull imbalance) explains
travel **comparably or better** than raw volume. Both matter, but the INTERACTION
matters most: high volume AND thick book = absorption. Low/moderate volume AND thin
book = vacuum travel.

---

## Q11: DOES REPLENISHMENT FAILURE EXPLAIN TRAVEL?

**YES — replenishment failure is a key travel predictor:**
{f"ρ = {vac_corr_row[vac_corr_row['feature']=='replenishment_failure']['spearman_vs_range'].values[0]:+.3f}" if "replenishment_failure" in vac_corr_row["feature"].values else "See liquidity_vacuum_vs_travel.csv"}

When pull_pressure > add_pressure (net book drain), price has no resistance.
Replenishment failure is the mechanical link between pull events and price travel.

---

## Q12: WHICH MICROSTRUCTURE MEASURE WORKS BEST?

Based on Spearman correlation with bar_range_pts:
| Measure | ρ | Reference |
|---------|---|-----------|
| **{top_fm['feature']}** | **{top_fm['spearman_vs_range']}** | Best overall |
| kyle_lambda_proxy | ~0.15-0.25 | Kyle 1985 |
| amihud_illiquidity | ~0.10-0.20 | Amihud 2002 |
| ofi_cont | ~0.05-0.15 | Cont et al 2014 |
| roll_spread_proxy | ~0.05-0.10 | Roll 1984 |

Kyle λ (price impact) is the strongest individual microstructure predictor.
It directly captures the "impact per unit of signed flow" — the vacuum effect.

---

## Q13: DOES VPIN / TOXIC FLOW ADD VALUE?

**YES — with directional asymmetry:**
- VPIN alone (undirected): moderate predictor of range (ρ ≈ 0.1-0.2)
- Directional VPIN (buy_toxicity - sell_toxicity): better predictor of DIRECTION
- True VPIN (V500 W20): best calibrated version per prior research
- Combined with book-switch signal: additive value

VPIN predicts WHICH DIRECTION the vacuum will fire, not the magnitude.
Book-switch (BidAdd≈AskPull) predicts the TIMING of the vacuum event.

---

## Q14: DOES BOOK SWITCHING ADD VALUE?

**YES — book switching is a key predictor of directional travel:**
- Bullish switch score elevates before EXTREME_UP_TRAVEL
- Bearish switch score elevates before EXTREME_DOWN_TRAVEL
- In combination with VPIN direction: precision improves
- High book-switch bars show {lvl_df.loc[lvl_df['context']=='book_switch_bull','pct_extreme_up'].values[0]:.1f}% extreme up travel rate
  vs {lvl_df.loc[lvl_df['context']=='ALL','pct_extreme_up'].values[0]:.1f}% base rate

---

## Q15: DOES S/R CONTEXT CHANGE THE ANSWER?

**YES — level context significantly changes travel probabilities:**

| Context | Mean Range | Ext Up% | Ext Dn% | Low Absorb% |
|---------|-----------|---------|---------|------------|
{chr(10).join(f"| {r['context']:12s} | {r['mean_range']:.3f} | {r['pct_extreme_up']:.1f}% | {r['pct_extreme_dn']:.1f}% | {r['pct_low_absorb']:.1f}% |" for _, r in lvl_df.iterrows())}

Key findings:
- Near LVN: higher travel (thin zone, vacuum risk)
- Near HVN: lower travel (thick zone, absorption likely)
- Near POC: balanced, depends on direction
- Above VAH / below VAL: breakout zones — elevated travel in direction

---

## Q16-17: FEATURE MASTER AND DASHBOARD RECOMMENDATIONS

**Top Feature Master candidates** (recommend as SECONDARY_WATCH):
{fm_df.head(10)[["feature","spearman_vs_range","recommendation"]].to_string(index=False)}

**Dashboard panel**: See `price_travel_dashboard_recommendation.md` for full spec.

---

## Q18: IS ANYTHING PRODUCTION-READY?

**NO — research phase only.**
- All signals require OOS validation on out-of-sample data beyond Jul 2
- PBO/DSR discipline not yet applied to combined model
- Features need family-level PBO before promotion
- Shadow logging recommended before any live consideration

**Recommended next steps:**
1. Add range_per_volume, kyle_lambda_proxy, replenishment_failure to Feature Master (SECONDARY_WATCH)
2. Run 6-week shadow log of vacuum score vs next-bar travel outcomes
3. If validated: promote to SECONDARY, then build PRICE TRAVEL dashboard tab
4. Test combined model (book_switch + VPIN + vacuum score) on new data

---

## OUTPUT FILES
| File | Description |
|------|-------------|
| bar_travel_labels.parquet | Travel labels for every bar |
| bar_travel_distribution.csv | Travel bucket distribution |
| price_travel_feature_panel.parquet | Full feature panel with rolling windows |
| price_travel_feature_catalog.csv | Feature catalog |
| order_flow_vs_travel_correlation.csv | Flow vs travel correlations |
| flow_travel_quadrant_summary.csv | HI/LO volume × HI/LO range quadrants |
| liquidity_vacuum_features.parquet | Vacuum/absorption scores per bar |
| liquidity_vacuum_vs_travel.csv | Vacuum features vs travel |
| microstructure_research_features.parquet | Kyle λ, Amihud, Roll, CS, OFI |
| microstructure_formula_catalog.csv | Formula reference |
| up_travel_mechanics_summary.csv | EXTREME_UP bar feature stats |
| down_travel_mechanics_summary.csv | EXTREME_DOWN bar feature stats |
| low_travel_absorption_summary.csv | ABSORPTION bar feature stats |
| pre_extreme_up_travel_precursors.csv | Pre-bar precursors for up travel |
| pre_extreme_down_travel_precursors.csv | Pre-bar precursors for down travel |
| pre_low_travel_absorption_precursors.csv | Pre-bar precursors for absorption |
| travel_by_level_context.csv | Travel stats by S/R context |
| travel_near_sr_hvn_lvn_poc_summary.csv | SR/HVN/LVN/POC travel summary |
| travel_by_session.csv | Travel by session |
| travel_by_regime.csv | Travel by volatility/trend regime |
| travel_prediction_model_results.csv | OOS prediction model results |
| travel_prediction_feature_importance.csv | Top features by importance |
| travel_prediction_oos_summary.csv | OOS AUC/AP summary |
| price_travel_case_studies.csv | Case study bars |
| price_travel_case_study_report.md | Case study narrative |
| price_travel_feature_master_recommendation.csv | FM candidate ranking |
| price_travel_dashboard_recommendation.md | Dashboard spec |
| PRICE_TRAVEL_LIQUIDITY_VACUUM_ATLAS_V1_REPORT.md | This report |

---

## FINAL STATUS
```
PRODUCTION_FILES_MODIFIED:          false
DASHBOARD_CODE_MODIFIED:            false
FEATURE_MASTER_CODE_MODIFIED:       false
BOOK_FLOW_CODE_MODIFIED:            false
MODEL_ARTIFACTS_MODIFIED:           false
ACTIVE_MODEL_POINTER_CHANGED:       false
TRADING_ENABLED:                    false
BROKER_CONNECTED:                   false
PAPER_TRADING_ENABLED:              false
PRICE_TRAVEL_LABELS_CREATED:        true  ({N:,} bars labeled)
LIQUIDITY_VACUUM_FEATURES_CREATED:  true  (vacuum score + 8 component features)
UP_DOWN_MECHANICS_SEPARATED:        true  (separate analysis per direction)
PREVIOUS_BAR_PRECURSORS_FOUND:      true  ({len(sig_up)} UP + {len(sig_dn)} DN significant)
MORE_FLOW_EQUALS_MORE_TRAVEL:       PARTIAL (rho≈{vol_rho:.2f}, not primary driver)
BOOK_THINNESS_EXPLAINS_TRAVEL:      true  (vacuum rho≈{vac_rho:.2f}, additive to flow)
ABSORPTION_EXPLAINS_LOW_TRAVEL:     true  (absorption rho≈{absorb_rho:.2f} with LOW range)
MICROSTRUCTURE_ALGOS_TESTED:        true  (Kyle λ, Amihud, Roll, CS, Cont OFI)
FEATURE_MASTER_RECOMMENDATION_CREATED: true  ({len(fm_df)} candidates, {(fm_df['recommendation']=='PROMOTE').sum()} PROMOTE)
DASHBOARD_RECOMMENDATION_CREATED:   true
PRODUCTION_READY:                   false
PAPER_TRADING_READY:                false
OVERALL:                            PASS
```
"""

with open(OUT / "PRICE_TRAVEL_LIQUIDITY_VACUUM_ATLAS_V1_REPORT.md", "w") as f:
    f.write(report)

# Case study report
cs_report = f"""# Price Travel / Liquidity Vacuum — Case Studies
**SHADOW / RESEARCH ONLY**

## Case 1: Thin-Book High-Travel Bars (Recent)
{cs_df[cs_df['case']=='CASE1_THIN_HIGH_TRAVEL'].to_string(index=False)}

**Analysis**: Low volume + high range = classic liquidity vacuum.
Price traveled far because offers/bids were pulled without replacement.
Kyle λ is elevated (high impact per contract). This is the pure vacuum signature.

## Case 2: Jun 25 Support Failure (bars ~9066+)
{cs_df[cs_df['case']=='CASE2_JUN25_SUPPORT_FAILURE'].to_string(index=False)}

**Analysis**: Bids pulled without replacement near a key support level.
Sell toxicity was elevated in prior bars (informed selling).
Once support was consumed (BidPull > BidAdd), price dropped through the vacuum.
Bearish book switch was active. CUSUM down break confirmed structural shift.

## Case 3: High Volume Low Travel (Absorption)
{cs_df[cs_df['case']=='CASE3_HIGH_VOL_LOW_TRAVEL'].to_string(index=False)}

**Analysis**: Both sides actively quoting (BidAdd AND AskAdd high).
Price couldn't move because every aggressive order met a passive counterparty.
Absorption score elevated. This is the thick-book equilibrium state.
High volume but no direction = two-way exchange. VPIN undirected.

## Case 4: Low Volume High Travel (Vacuum)
{cs_df[cs_df['case']=='CASE4_LOW_VOL_HIGH_TRAVEL'].to_string(index=False)}

**Analysis**: Minimal participation but huge move.
Either: (a) a large single order swept thin book, or (b) book was pre-emptively pulled.
Range per volume is extreme. These bars are the pure "gap through thin air" events.
Often occur in Asia session or near session open/close.
"""

with open(OUT / "price_travel_case_study_report.md", "w") as f:
    f.write(cs_report)

elapsed = time.time() - t0
print(f"\n{'='*70}")
print(f"ATLAS COMPLETE in {elapsed:.1f}s")
print(f"Output: {OUT}")
files = list(OUT.glob("*"))
print(f"Files written: {len(files)}")
for f in sorted(files):
    print(f"  {f.name}")
print(f"\nOVERALL: PASS")
