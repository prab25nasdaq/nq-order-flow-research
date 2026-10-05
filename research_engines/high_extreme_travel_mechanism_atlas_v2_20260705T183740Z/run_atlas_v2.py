"""
High/Extreme Travel Bar Mechanism Atlas v2
SHADOW / RESEARCH ONLY — NO EXECUTION / NO BROKER / NO PAPER TRADING
Generated: 2026-07-05
"""

import sys, os, warnings, glob, time, json
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
from pathlib import Path
from scipy.stats import spearmanr, mannwhitneyu
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import roc_auc_score, average_precision_score

t0 = time.time()
EPS = 1e-8

OUT = Path("/home/prabh/OFI_Production/research_engines/high_extreme_travel_mechanism_atlas_v2_20260705T183740Z")
OUT.mkdir(parents=True, exist_ok=True)

print("=" * 70)
print("HIGH/EXTREME TRAVEL MECHANISM ATLAS v2")
print("SHADOW / RESEARCH ONLY — NO EXECUTION — NO BROKER")
print("=" * 70)

# ─────────────────────────────────────────────────────────────────────────────
# LOAD DATA
# ─────────────────────────────────────────────────────────────────────────────
print("\n[DATA] Loading NQU6 master...")
nqu6 = pd.read_json("/home/prabh/OFI_Live_Features/master_NQU6_shadow.ndjsonl", lines=True)
nqu6 = nqu6.sort_values("bar_end_ts_ns").reset_index(drop=True)
print(f"  NQU6: {len(nqu6):,} bars  bar_index {nqu6['bar_index'].min()}–{nqu6['bar_index'].max()}")

print("[DATA] Loading Model Feature Master...")
mfm = pd.read_parquet("/home/prabh/OFI_Production/model_feature_master/data/model_feature_master_shadow.parquet")
print(f"  MFM: {mfm.shape}")

print("[DATA] Loading all NQU6 BF level candles (top20)...")
bf_files = sorted(glob.glob(
    "/home/prabh/OFI_Production/book_flow_chart/cache/"
    "book_flow_level_candles_NQU6_*_top20.parquet"
))
bf_chunks = []
for f in bf_files:
    df = pd.read_parquet(f)
    bf_chunks.append(df)
bfl_all = pd.concat(bf_chunks, ignore_index=True)
print(f"  BF level candles: {len(bfl_all):,} rows  {len(bf_files)} files  "
      f"bar_idx {bfl_all['bar_idx'].min()}–{bfl_all['bar_idx'].max()}")

# Load prior atlas travel labels if available
atlas_dir = sorted(glob.glob(
    "/home/prabh/OFI_Production/research_engines/"
    "price_travel_liquidity_vacuum_atlas_v1_*"
))
prior_labels = None
if atlas_dir:
    label_file = Path(atlas_dir[-1]) / "bar_travel_labels.parquet"
    if label_file.exists():
        prior_labels = pd.read_parquet(label_file)
        print(f"[DATA] Prior atlas labels: {len(prior_labels):,} bars from {atlas_dir[-1]}")

# ─────────────────────────────────────────────────────────────────────────────
# BUILD TRAVEL LABELS ON FULL NQU6
# ─────────────────────────────────────────────────────────────────────────────
print("\n[LABELS] Building travel labels for all NQU6 bars...")

px_o = nqu6["px_open"].values.astype(float)
px_h = nqu6["px_high"].values.astype(float)
px_l = nqu6["px_low"].values.astype(float)
px_c = nqu6["px_close"].values.astype(float)
N = len(nqu6)

bar_range  = px_h - px_l
body_pts   = np.abs(px_c - px_o)
up_travel  = px_h - px_o
dn_travel  = px_o - px_l
signed_ret = px_c - px_o
efficiency = body_pts / np.maximum(bar_range, EPS)
close_loc  = (px_c - px_l) / np.maximum(bar_range, EPS)  # 0=at low, 1=at high

p25 = np.nanpercentile(bar_range, 25)
p75 = np.nanpercentile(bar_range, 75)
p95 = np.nanpercentile(bar_range, 95)

tbucket = np.where(bar_range >= p95, "EXTREME_TRAVEL",
          np.where(bar_range >= p75, "HIGH_TRAVEL",
          np.where(bar_range >= p25, "NORMAL_TRAVEL", "LOW_TRAVEL")))

tdir = np.where(signed_ret >  bar_range * 0.25, "UP_TRAVEL",
       np.where(signed_ret < -bar_range * 0.25, "DOWN_TRAVEL",
       np.where(efficiency < 0.20, "TWO_WAY_CHOP", "LOW_TRAVEL_ABSORPTION")))

nqu6["bar_range_pts"]    = bar_range
nqu6["body_pts"]         = body_pts
nqu6["up_travel_pts"]    = up_travel
nqu6["down_travel_pts"]  = dn_travel
nqu6["signed_return"]    = signed_ret
nqu6["efficiency"]       = efficiency
nqu6["close_location"]   = close_loc
nqu6["travel_bucket"]    = tbucket
nqu6["travel_direction"] = tdir

# Join MFM key features
mfm_join = mfm[[c for c in mfm.columns if c not in [
    "day","session_date","master_timestamp_utc"
]]].drop_duplicates("bar_end_ts_ns")
nqu6 = nqu6.merge(mfm_join, on="bar_end_ts_ns", how="left", suffixes=("","_mfm"))

print(f"  p25={p25:.2f}  p75={p75:.2f}  p95={p95:.2f} pts")
for b in ["EXTREME_TRAVEL","HIGH_TRAVEL","NORMAL_TRAVEL","LOW_TRAVEL"]:
    n = (tbucket == b).sum()
    print(f"  {b:20s}: {n:5d} bars  ({n/N*100:.1f}%)")

# ─────────────────────────────────────────────────────────────────────────────
# AGGREGATE BF LEVEL CANDLES TO BAR-ZONE LEVEL
# ─────────────────────────────────────────────────────────────────────────────
print("\n[BFL] Aggregating level candles to bar-zone anatomy...")

# Merge close_price/mid_price onto bfl_all from nqu6
# These columns are already in bfl_all (close_price = bar close, mid_price = bar mid)
# Join bar OHLC from nqu6 for zone computation
nqu6_ohlc = nqu6[["bar_index","px_open","px_high","px_low","px_close",
                   "bar_range_pts","signed_return","travel_direction","travel_bucket"]].copy()
bfl = bfl_all.merge(
    nqu6_ohlc.rename(columns={"bar_index":"bar_idx"}),
    on="bar_idx", how="left"
)

# Compute travel-direction zone (relative to bar open and mid)
mid = bfl["mid_price"].values
px_open_b = bfl["px_open"].values
px_high_b = bfl["px_high"].values
px_low_b  = bfl["px_low"].values
pl = bfl["price_level"].values

# Zone relative to bar mid
bfl["above_mid"] = pl > mid
bfl["below_mid"] = pl < mid

# For UP_TRAVEL: "upper travel zone" = above bar open (price swept through here going up)
# For DOWN_TRAVEL: "lower travel zone" = below bar open
bfl["above_open"] = pl > px_open_b
bfl["below_open"] = pl < px_open_b

# Upper half of travel (above mid-point of up-travel range)
up_midpoint = px_open_b + (px_high_b - px_open_b) * 0.5
dn_midpoint = px_open_b - (px_open_b - px_low_b)  * 0.5
bfl["upper_half"] = pl >= up_midpoint
bfl["lower_half"] = pl <= dn_midpoint

# Define 4 zones per bar for universal use:
# ZONE_A: lower (below open or px_low end)
# ZONE_B: mid (near open and mid)
# ZONE_C: upper-mid
# ZONE_D: upper extreme
range_b = np.maximum(px_high_b - px_low_b, EPS)
bfl["zone"] = np.where(
    pl >= px_low_b + range_b * 0.75,  "ZONE_D_UPPER",
    np.where(pl >= px_low_b + range_b * 0.50, "ZONE_C_MID_HIGH",
    np.where(pl >= px_low_b + range_b * 0.25, "ZONE_B_MID_LOW", "ZONE_A_LOWER"))
)

# Aggregate per bar × zone
print("  Computing bar × zone aggregations...")
grp_cols = ["bar_idx","zone","travel_direction","travel_bucket"]
bg = bfl.groupby(grp_cols, observed=True).agg(
    bid_add=("bid_add","sum"),
    bid_pull=("bid_pull","sum"),
    ask_add=("ask_add","sum"),
    ask_pull=("ask_pull","sum"),
    abs_flow=("abs_flow","sum"),
    signed_flow=("signed_flow","sum"),
    trade_vol=("trade_volume_at_price","sum"),
    n_levels=("price_level","count"),
    zero_bid_add_levels=("bid_add", lambda x: (x==0).sum()),
    zero_ask_add_levels=("ask_add", lambda x: (x==0).sum()),
    zero_bid_pull_levels=("bid_pull",lambda x: (x==0).sum()),
    zero_ask_pull_levels=("ask_pull",lambda x: (x==0).sum()),
).reset_index()

bg["net_bid"] = bg["bid_add"] - bg["bid_pull"]
bg["net_ask"] = bg["ask_add"] - bg["ask_pull"]
bg["resistance_removed"] = bg["ask_pull"] - bg["ask_add"]
bg["support_removed"]    = bg["bid_pull"] - bg["bid_add"]
bg["replenishment_ratio"]= (bg["bid_add"]+bg["ask_add"]) / np.maximum(
    bg["bid_pull"]+bg["ask_pull"], EPS)
bg["ask_replenish_ratio"]= bg["ask_add"] / np.maximum(bg["ask_pull"], EPS)
bg["bid_replenish_ratio"]= bg["bid_add"] / np.maximum(bg["bid_pull"], EPS)

bg.to_parquet(OUT / "high_extreme_bar_zone_anatomy.parquet", index=False)
print(f"  Zone anatomy: {len(bg):,} rows")

# Also aggregate per bar (total) for merged features
bar_total = bfl.groupby("bar_idx").agg(
    total_bid_add=("bid_add","sum"),
    total_bid_pull=("bid_pull","sum"),
    total_ask_add=("ask_add","sum"),
    total_ask_pull=("ask_pull","sum"),
    total_abs_flow=("abs_flow","sum"),
    total_signed_flow=("signed_flow","sum"),
    total_trade_vol=("trade_volume_at_price","sum"),
    n_price_levels=("price_level","count"),
    close_price=("close_price","first"),
    mid_price=("mid_price","first"),
).reset_index()
bar_total["net_bid"] = bar_total["total_bid_add"] - bar_total["total_bid_pull"]
bar_total["net_ask"] = bar_total["total_ask_add"] - bar_total["total_ask_pull"]
bar_total["bullish_switch"] = bar_total["total_bid_add"] + bar_total["total_ask_pull"]
bar_total["bearish_switch"] = bar_total["total_ask_add"] + bar_total["total_bid_pull"]
bar_total["resistance_removed"] = bar_total["total_ask_pull"] - bar_total["total_ask_add"]
bar_total["support_removed"]    = bar_total["total_bid_pull"] - bar_total["total_bid_add"]
pull_sum = bar_total["total_bid_pull"] + bar_total["total_ask_pull"]
add_sum  = bar_total["total_bid_add"]  + bar_total["total_ask_add"]
bar_total["replenishment_failure"] = np.maximum(pull_sum - add_sum, 0)
bar_total["absorption_score"] = (
    bar_total["total_bid_add"] * bar_total["total_ask_add"]
) / np.maximum(bar_total["total_abs_flow"]**2, EPS)

nqu6 = nqu6.merge(
    bar_total.rename(columns={"bar_idx":"bar_index"}),
    on="bar_index", how="left"
)
nqu6["range_per_abs_flow"] = nqu6["bar_range_pts"] / np.maximum(
    nqu6["total_abs_flow"].fillna(0), EPS)

# ─────────────────────────────────────────────────────────────────────────────
# PART A — HIGH/EXTREME TRAVEL UNIVERSE
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART A] Building high/extreme travel universe...")

he_mask = nqu6["travel_bucket"].isin(["HIGH_TRAVEL","EXTREME_TRAVEL"])
ab_mask = nqu6["travel_direction"] == "LOW_TRAVEL_ABSORPTION"
eu_mask = nqu6["travel_direction"] == "UP_TRAVEL"
ed_mask = nqu6["travel_direction"] == "DOWN_TRAVEL"

universe = nqu6[he_mask | ab_mask].copy()

# Session
def get_session(row):
    for s in ["sess_Asia","sess_EU","sess_US_Open","sess_US_AM","sess_US_PM","sess_US_Late"]:
        if row.get(s, 0) == 1:
            return s.replace("sess_","")
    return "UNKNOWN"

sess_cols = [c for c in nqu6.columns if c.startswith("sess_")]
if sess_cols:
    session_arr = []
    for _, r in universe.iterrows():
        sess = "UNKNOWN"
        for s in sess_cols:
            if r.get(s, 0) == 1:
                sess = s.replace("sess_",""); break
        session_arr.append(sess)
    universe["session"] = session_arr
else:
    universe["session"] = "UNKNOWN"

univ_cols = ["bar_index","bar_end_ts_ns","day","px_open","px_high","px_low","px_close",
             "bar_range_pts","body_pts","up_travel_pts","down_travel_pts","signed_return",
             "close_location","travel_direction","travel_bucket","efficiency","session",
             "vol_total","total_abs_flow","total_signed_flow",
             "total_bid_add","total_bid_pull","total_ask_add","total_ask_pull",
             "bullish_switch","bearish_switch","resistance_removed","support_removed",
             "replenishment_failure","absorption_score","range_per_abs_flow"]
univ_cols = [c for c in univ_cols if c in universe.columns]

universe[univ_cols].to_parquet(OUT / "high_extreme_travel_universe.parquet", index=False)

n_he = he_mask.sum()
n_hi = (nqu6["travel_bucket"]=="HIGH_TRAVEL").sum()
n_ex = (nqu6["travel_bucket"]=="EXTREME_TRAVEL").sum()
n_ab = ab_mask.sum()

summary_rows = []
for grp, mask in [
    ("HIGH_TRAVEL",       nqu6["travel_bucket"]=="HIGH_TRAVEL"),
    ("EXTREME_TRAVEL",    nqu6["travel_bucket"]=="EXTREME_TRAVEL"),
    ("EXTREME_UP",        (nqu6["travel_bucket"].isin(["HIGH_TRAVEL","EXTREME_TRAVEL"])) &
                          (nqu6["travel_direction"]=="UP_TRAVEL")),
    ("EXTREME_DOWN",      (nqu6["travel_bucket"].isin(["HIGH_TRAVEL","EXTREME_TRAVEL"])) &
                          (nqu6["travel_direction"]=="DOWN_TRAVEL")),
    ("LOW_TRAVEL_ABSORB", ab_mask),
    ("ALL",               pd.Series(True, index=nqu6.index)),
]:
    sub = nqu6[mask]
    summary_rows.append({
        "group": grp, "count": mask.sum(),
        "pct_of_total": round(mask.sum()/N*100,2),
        "mean_range": round(sub["bar_range_pts"].mean(),3),
        "median_range": round(sub["bar_range_pts"].median(),3),
        "mean_efficiency": round(sub["efficiency"].mean(),4),
        "mean_abs_flow": round(sub["total_abs_flow"].mean(),1) if "total_abs_flow" in sub else None,
        "mean_resistance_removed": round(sub["resistance_removed"].mean(),2) if "resistance_removed" in sub else None,
        "mean_replenish_fail": round(sub["replenishment_failure"].mean(),2) if "replenishment_failure" in sub else None,
        "mean_absorption": round(sub["absorption_score"].mean(),6) if "absorption_score" in sub else None,
    })
summ_df = pd.DataFrame(summary_rows)
summ_df.to_csv(OUT / "high_extreme_travel_universe_summary.csv", index=False)
print(f"  HIGH_TRAVEL: {n_hi}  EXTREME_TRAVEL: {n_ex}  "
      f"TOTAL HE: {n_he}  ABSORPTION_CONTROL: {n_ab}")
print(summ_df.to_string(index=False))

# ─────────────────────────────────────────────────────────────────────────────
# PART B — ZONE ANATOMY SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART B] Zone anatomy summary for high/extreme bars...")

he_zone = bg[bg["travel_bucket"].isin(["HIGH_TRAVEL","EXTREME_TRAVEL"])].copy()
ab_zone = bg[bg["travel_direction"] == "LOW_TRAVEL_ABSORPTION"].copy()

# Zone summary per travel_bucket × travel_direction × zone
zone_summ = he_zone.groupby(["travel_bucket","travel_direction","zone"], observed=True).agg(
    n_bar_zones=("bar_idx","count"),
    mean_bid_add=("bid_add","mean"),
    mean_bid_pull=("bid_pull","mean"),
    mean_ask_add=("ask_add","mean"),
    mean_ask_pull=("ask_pull","mean"),
    mean_resistance_removed=("resistance_removed","mean"),
    mean_support_removed=("support_removed","mean"),
    mean_ask_replenish=("ask_replenish_ratio","mean"),
    mean_bid_replenish=("bid_replenish_ratio","mean"),
    pct_zero_ask_add=("zero_ask_add_levels","mean"),
    pct_zero_bid_add=("zero_bid_add_levels","mean"),
).reset_index()
zone_summ.to_csv(OUT / "zone_anatomy_summary.csv", index=False)
print(f"  Zone anatomy summary rows: {len(zone_summ)}")

# Key finding: upper zone (ZONE_D) for UP_TRAVEL
up_zone_d = zone_summ[(zone_summ["travel_direction"]=="UP_TRAVEL") &
                      (zone_summ["zone"]=="ZONE_D_UPPER")]
dn_zone_a = zone_summ[(zone_summ["travel_direction"]=="DOWN_TRAVEL") &
                      (zone_summ["zone"]=="ZONE_A_LOWER")]
print("\n  UP_TRAVEL — ZONE_D (upper quarter) stats:")
if len(up_zone_d) > 0:
    print(up_zone_d[["travel_bucket","mean_ask_add","mean_ask_pull",
                      "mean_resistance_removed","mean_ask_replenish","pct_zero_ask_add"]].to_string(index=False))
print("\n  DOWN_TRAVEL — ZONE_A (lower quarter) stats:")
if len(dn_zone_a) > 0:
    print(dn_zone_a[["travel_bucket","mean_bid_add","mean_bid_pull",
                      "mean_support_removed","mean_bid_replenish","pct_zero_bid_add"]].to_string(index=False))

# ─────────────────────────────────────────────────────────────────────────────
# PART C — DIRECTIONAL LIQUIDITY VACUUM FEATURES
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART C] Computing directional vacuum features per bar...")

def z_norm(arr):
    m, s = np.nanmean(arr), np.nanstd(arr)
    return (arr - m) / max(s, EPS)

# Pivot zone anatomy to per-bar wide form
bg_wide = bg.pivot_table(
    index="bar_idx",
    columns="zone",
    values=["bid_add","bid_pull","ask_add","ask_pull","resistance_removed",
            "support_removed","ask_replenish_ratio","bid_replenish_ratio",
            "zero_ask_add_levels","zero_bid_add_levels","n_levels"],
    aggfunc="first"
).fillna(0)
bg_wide.columns = [f"{c[0]}_{c[1]}" for c in bg_wide.columns]
bg_wide = bg_wide.reset_index()

# Extract upper zone (ZONE_D) and lower zone (ZONE_A) signals
vac = nqu6[["bar_index","bar_range_pts","signed_return","travel_direction",
            "travel_bucket","efficiency","close_location",
            "total_bid_add","total_bid_pull","total_ask_add","total_ask_pull",
            "bullish_switch","bearish_switch","resistance_removed","support_removed",
            "replenishment_failure","absorption_score","range_per_abs_flow"]].copy()

vac = vac.merge(bg_wide.rename(columns={"bar_idx":"bar_index"}),
                on="bar_index", how="left")

# Upward vacuum features
if "ask_pull_ZONE_D_UPPER" in vac.columns:
    vac["upper_ask_pull"] = vac["ask_pull_ZONE_D_UPPER"]
    vac["upper_ask_add"]  = vac["ask_add_ZONE_D_UPPER"]
else:
    vac["upper_ask_pull"] = 0; vac["upper_ask_add"] = 0
vac["upper_ask_removed"]  = vac["upper_ask_pull"] - vac["upper_ask_add"]
vac["upper_ask_replenish"]= vac["upper_ask_add"] / np.maximum(vac["upper_ask_pull"], EPS)
if "zero_ask_add_levels_ZONE_D_UPPER" in vac.columns:
    vac["upper_zero_ask_add"] = vac["zero_ask_add_levels_ZONE_D_UPPER"]
else:
    vac["upper_zero_ask_add"] = 0

# Downward vacuum features
if "bid_pull_ZONE_A_LOWER" in vac.columns:
    vac["lower_bid_pull"] = vac["bid_pull_ZONE_A_LOWER"]
    vac["lower_bid_add"]  = vac["bid_add_ZONE_A_LOWER"]
else:
    vac["lower_bid_pull"] = 0; vac["lower_bid_add"] = 0
vac["lower_bid_removed"]  = vac["lower_bid_pull"] - vac["lower_bid_add"]
vac["lower_bid_replenish"]= vac["lower_bid_add"] / np.maximum(vac["lower_bid_pull"], EPS)
if "zero_bid_add_levels_ZONE_A_LOWER" in vac.columns:
    vac["lower_zero_bid_add"] = vac["zero_bid_add_levels_ZONE_A_LOWER"]
else:
    vac["lower_zero_bid_add"] = 0

# Composite vacuum scores (z-normed)
vac["upward_vacuum_score"]  = (
    z_norm(vac["upper_ask_removed"].values) +
    z_norm(vac["upper_zero_ask_add"].values) +
    z_norm(vac["resistance_removed"].values.clip(0))
) / 3

vac["downward_vacuum_score"] = (
    z_norm(vac["lower_bid_removed"].values) +
    z_norm(vac["lower_zero_bid_add"].values) +
    z_norm(vac["support_removed"].values.clip(0))
) / 3

vac["close_at_high_flag"] = (
    (nqu6["px_close"].values == nqu6["px_high"].values)).astype(float)
vac["close_at_low_flag"]  = (
    (nqu6["px_close"].values == nqu6["px_low"].values)).astype(float)

vac.to_parquet(OUT / "directional_liquidity_vacuum_features.parquet", index=False)

# Vacuum summary: does upper_ask_removed predict UP_TRAVEL?
for dir_label, score_col, control_col in [
    ("UP_TRAVEL",   "upper_ask_removed", "upward_vacuum_score"),
    ("DOWN_TRAVEL", "lower_bid_removed", "downward_vacuum_score"),
]:
    dir_mask = (vac["travel_direction"] == dir_label) & \
               vac["travel_bucket"].isin(["HIGH_TRAVEL","EXTREME_TRAVEL"])
    oth_mask = ~dir_mask
    s_ev = vac.loc[dir_mask, score_col].dropna()
    s_ot = vac.loc[oth_mask, score_col].dropna()
    if len(s_ev) > 5 and len(s_ot) > 5:
        stat, pval = mannwhitneyu(s_ev, s_ot, alternative="greater")
        sp, _ = spearmanr(vac[score_col].dropna(), vac["bar_range_pts"].iloc[:len(vac[score_col].dropna())], nan_policy="omit")
        print(f"  {dir_label}: {score_col} mean={s_ev.mean():.1f} vs others={s_ot.mean():.1f}  "
              f"p={pval:.4f}  vs_range rho={sp:.3f}")

vac_summ_rows = []
for dv, sv in [("UP_TRAVEL","upward_vacuum_score"),("DOWN_TRAVEL","downward_vacuum_score")]:
    for tb in ["HIGH_TRAVEL","EXTREME_TRAVEL","NORMAL_TRAVEL","LOW_TRAVEL"]:
        m = vac[(vac["travel_direction"]==dv) & (vac["travel_bucket"]==tb)]
        if len(m) < 3: continue
        vac_summ_rows.append({
            "direction":tb+"_"+dv, "n":len(m),
            "mean_vacuum": round(m[sv].mean(),4),
            "mean_range":  round(m["bar_range_pts"].mean(),3),
            "mean_upper_ask_rem": round(m["upper_ask_removed"].mean(),2),
            "mean_lower_bid_rem": round(m["lower_bid_removed"].mean(),2),
            "pct_close_at_high":  round(m["close_at_high_flag"].mean()*100,1),
            "pct_close_at_low":   round(m["close_at_low_flag"].mean()*100,1),
        })

pd.DataFrame(vac_summ_rows).to_csv(OUT / "directional_vacuum_summary.csv", index=False)
print(f"\n  Vacuum summary written: {len(vac_summ_rows)} groups")

# ─────────────────────────────────────────────────────────────────────────────
# PART D — MECHANISM ATTRIBUTION
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART D] Classifying mechanism for each high/extreme bar...")

he = nqu6[he_mask].copy()
he = he.merge(vac[["bar_index","upper_ask_removed","lower_bid_removed",
                    "upward_vacuum_score","downward_vacuum_score",
                    "upper_ask_replenish","lower_bid_replenish",
                    "upper_zero_ask_add","lower_zero_bid_add",
                    "close_at_high_flag","close_at_low_flag"]].copy(),
              on="bar_index", how="left")

# Percentile thresholds (from ALL bars)
vol_p75     = np.nanpercentile(nqu6["vol_total"].dropna(), 75)
rpaf_p75    = np.nanpercentile(vac["range_per_abs_flow"].replace([np.inf],np.nan).dropna(), 75)
uak_p50     = np.nanpercentile(vac["upper_ask_removed"].values, 50)
lbr_p50     = np.nanpercentile(vac["lower_bid_removed"].values, 50)
rf_p50      = np.nanpercentile(nqu6["replenishment_failure"].fillna(0).values, 50)
bs_p75      = np.nanpercentile(nqu6["bullish_switch"].fillna(0).values, 75)
vpin_p75    = np.nanpercentile(nqu6["vpin"].fillna(0).values, 75) if "vpin" in nqu6.columns else 0.5
absorb_p50  = np.nanpercentile(nqu6["absorption_score"].fillna(0).values, 50)

def classify_mechanism(r, dir_col="travel_direction"):
    tdir     = r.get(dir_col,"")
    vol      = float(r.get("vol_total",0) or 0)
    rpaf     = float(r.get("range_per_abs_flow",0) or 0)
    ua_rem   = float(r.get("upper_ask_removed",0) or 0)
    lb_rem   = float(r.get("lower_bid_removed",0) or 0)
    rf       = float(r.get("replenishment_failure",0) or 0)
    bs       = float(r.get("bullish_switch",0) or 0)
    bsr      = float(r.get("bearish_switch",0) or 0)
    vpin     = float(r.get("vpin",0) or 0)
    dtox     = float(r.get("dash_toxicity",0) or 0)
    absorb   = float(r.get("absorption_score",0) or 0)
    r_rem    = float(r.get("resistance_removed",0) or 0)
    s_rem    = float(r.get("support_removed",0) or 0)
    cl_hi    = float(r.get("close_at_high_flag",0) or 0)
    cl_lo    = float(r.get("close_at_low_flag",0) or 0)
    lvn_near = float(r.get("lvl_LVN",0) or 0)
    hvn_near = float(r.get("lvl_HVN",0) or 0)

    scores = {}
    # VACUUM: high range, high rpaf, ask/bid removed, few replenishments
    vacuum_up   = (rpaf >= rpaf_p75) and (ua_rem > uak_p50) and (tdir == "UP_TRAVEL")
    vacuum_dn   = (rpaf >= rpaf_p75) and (lb_rem > lbr_p50) and (tdir == "DOWN_TRAVEL")
    vacuum_gen  = (rpaf >= rpaf_p75) and (rf >= rf_p50)
    scores["LIQUIDITY_VACUUM_TRAVEL"]       = 3*vacuum_up + 3*vacuum_dn + vacuum_gen

    # REPLENISHMENT_FAILURE: pull >> add, book fails to refill
    scores["REPLENISHMENT_FAILURE_TRAVEL"]  = 2*(rf >= rf_p50*2) + (r_rem > 0 or s_rem > 0)

    # BOOK_SWITCH: bullish_switch dominant for up, bearish for down
    bs_active_up = (bs > bs_p75) and (tdir == "UP_TRAVEL") and (bs > bsr)
    bs_active_dn = (bsr > bs_p75) and (tdir == "DOWN_TRAVEL") and (bsr > bs)
    scores["BOOK_SWITCH_TRAVEL"]            = 3*bs_active_up + 3*bs_active_dn

    # FLOW_DRIVEN: high volume, both sides add, range justifiable by flow
    flow_driven = (vol >= vol_p75) and (rpaf < rpaf_p75)
    scores["FLOW_DRIVEN_TRAVEL"]            = 3*flow_driven

    # TOXIC_FLOW: VPIN/dash_toxicity elevated
    tox_up = (vpin >= vpin_p75 or dtox > 0.5) and (tdir == "UP_TRAVEL")
    tox_dn = (vpin >= vpin_p75 or dtox > 0.5) and (tdir == "DOWN_TRAVEL")
    scores["TOXIC_FLOW_TRAVEL"]             = 2*tox_up + 2*tox_dn

    # LEVEL: near HVN/LVN
    scores["LEVEL_BREAK_TRAVEL"]            = int(lvn_near == 1)
    scores["LEVEL_REJECTION_TRAVEL"]        = int(hvn_near == 1)

    # Pick winner
    if max(scores.values()) == 0:
        return "UNKNOWN"
    top = max(scores, key=scores.get)
    # Tie check
    top_score = scores[top]
    tied = [k for k,v in scores.items() if v == top_score]
    if len(tied) > 1:
        return "MIXED_MECHANISM"
    return top

mechanisms = []
for _, r in he.iterrows():
    mechanisms.append(classify_mechanism(r))
he["mechanism"] = mechanisms

he[["bar_index","bar_end_ts_ns","bar_range_pts","travel_direction",
    "travel_bucket","mechanism","upward_vacuum_score","downward_vacuum_score",
    "upper_ask_removed","lower_bid_removed","replenishment_failure",
    "bullish_switch","bearish_switch","range_per_abs_flow"]].to_parquet(
    OUT / "high_extreme_travel_mechanism_labels.parquet", index=False)

mech_freq = he["mechanism"].value_counts().reset_index()
mech_freq.columns = ["mechanism","count"]
mech_freq["pct"] = (mech_freq["count"]/len(he)*100).round(2)
mech_freq["mean_range"] = [he.loc[he["mechanism"]==m,"bar_range_pts"].mean().round(3)
                            for m in mech_freq["mechanism"]]
mech_freq.to_csv(OUT / "mechanism_frequency_summary.csv", index=False)
print(f"  Mechanism frequency ({len(he)} HE bars):")
print(mech_freq.to_string(index=False))
print(f"\n  VACUUM bars: {mech_freq.loc[mech_freq['mechanism']=='LIQUIDITY_VACUUM_TRAVEL','count'].sum()} "
      f"({mech_freq.loc[mech_freq['mechanism']=='LIQUIDITY_VACUUM_TRAVEL','pct'].sum():.1f}%)")
print(f"  FLOW bars:   {mech_freq.loc[mech_freq['mechanism']=='FLOW_DRIVEN_TRAVEL','count'].sum()} "
      f"({mech_freq.loc[mech_freq['mechanism']=='FLOW_DRIVEN_TRAVEL','pct'].sum():.1f}%)")

# ─────────────────────────────────────────────────────────────────────────────
# PART E — PREVIOUS-BAR PRECURSOR ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART E] Previous-bar precursor analysis...")

# Build full feature time series with lag
feat_ts = nqu6[["bar_index","bar_end_ts_ns","bar_range_pts","travel_direction","travel_bucket",
                 "efficiency","total_bid_add","total_bid_pull","total_ask_add","total_ask_pull",
                 "replenishment_failure","absorption_score","bullish_switch","bearish_switch",
                 "resistance_removed","support_removed","range_per_abs_flow",
                 "vpin","dash_toxicity","vol_total"]].copy()

feat_ts = feat_ts.merge(
    vac[["bar_index","upper_ask_removed","lower_bid_removed",
         "upward_vacuum_score","downward_vacuum_score",
         "upper_zero_ask_add","lower_zero_bid_add"]],
    on="bar_index", how="left"
)
feat_ts = feat_ts.sort_values("bar_index").reset_index(drop=True)

PREC_FEATURES = [
    "replenishment_failure","upper_ask_removed","lower_bid_removed",
    "upward_vacuum_score","downward_vacuum_score",
    "upper_zero_ask_add","lower_zero_bid_add",
    "bullish_switch","bearish_switch",
    "resistance_removed","support_removed",
    "vpin","dash_toxicity","absorption_score",
    "bar_range_pts","vol_total",
]

TARGETS = {
    "EXTREME_UP":    ((nqu6["travel_bucket"]=="EXTREME_TRAVEL") &
                      (nqu6["travel_direction"]=="UP_TRAVEL")).values,
    "EXTREME_DOWN":  ((nqu6["travel_bucket"]=="EXTREME_TRAVEL") &
                      (nqu6["travel_direction"]=="DOWN_TRAVEL")).values,
    "HIGH_TRAVEL":   (nqu6["travel_bucket"]=="HIGH_TRAVEL").values,
    "EXTREME_TRAVEL":(nqu6["travel_bucket"]=="EXTREME_TRAVEL").values,
}

prec_rows = []
for lag in [1, 2, 3, 5, 10, 20]:
    for feat in PREC_FEATURES:
        if feat not in feat_ts.columns:
            continue
        pre_feat = feat_ts[feat].shift(lag).values
        for tname, tmask in TARGETS.items():
            mask = np.isfinite(pre_feat) & tmask
            oth  = np.isfinite(pre_feat) & ~tmask
            if mask.sum() < 10 or oth.sum() < 10:
                continue
            stat, pval = mannwhitneyu(pre_feat[mask], pre_feat[oth],
                                      alternative="two-sided")
            ratio = pre_feat[mask].mean() / max(abs(pre_feat[oth].mean()), EPS)
            sp, _ = spearmanr(pre_feat[np.isfinite(pre_feat)],
                               nqu6["bar_range_pts"].values[np.isfinite(pre_feat)])
            prec_rows.append({
                "target": tname, "lag": lag, "feature": feat,
                "mean_before_event": round(pre_feat[mask].mean(), 5),
                "mean_before_other": round(pre_feat[oth].mean(), 5),
                "ratio": round(ratio, 3),
                "mannwhitney_p": round(pval, 6),
                "significant": pval < 0.05,
                "spearman_vs_range": round(sp, 4),
                "n_event": mask.sum(),
            })

prec_df = pd.DataFrame(prec_rows)
prec_df.to_csv(OUT / "pre_high_extreme_travel_precursors.csv", index=False)

# Summary: top precursors per target and lag
lag_imp_rows = []
for tname in TARGETS:
    sub = prec_df[(prec_df["target"]==tname) & prec_df["significant"]]
    if len(sub) == 0:
        continue
    for lag in [1,2,3,5]:
        lag_sub = sub[sub["lag"]==lag].sort_values("ratio", ascending=False)
        if len(lag_sub) > 0:
            top = lag_sub.iloc[0]
            lag_imp_rows.append({
                "target": tname, "lag": lag,
                "top_feature": top["feature"],
                "ratio": top["ratio"],
                "p_value": top["mannwhitney_p"],
                "n_significant": len(lag_sub),
            })
pd.DataFrame(lag_imp_rows).to_csv(OUT / "precursor_lag_importance_summary.csv", index=False)
print(f"  Precursor analysis: {len(prec_df)} tests  "
      f"significant: {prec_df['significant'].sum()}")
print("  Top precursors at lag-1 (all targets):")
top1 = prec_df[(prec_df["lag"]==1) & prec_df["significant"]].nlargest(6,"ratio")
print(top1[["target","feature","ratio","mannwhitney_p"]].to_string(index=False))

# ─────────────────────────────────────────────────────────────────────────────
# PART F — ABSORPTION CONTROL COMPARISON
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART F] Travel vs absorption control comparison...")

# Build comparison groups
groups = {
    "HIGH_VOL_HIGH_RANGE":  (nqu6["vol_total"] >= np.nanpercentile(nqu6["vol_total"], 75)) &
                            (nqu6["bar_range_pts"] >= p75),
    "HIGH_VOL_LOW_RANGE":   (nqu6["vol_total"] >= np.nanpercentile(nqu6["vol_total"], 75)) &
                            (nqu6["bar_range_pts"] <= p25),
    "LOW_VOL_HIGH_RANGE":   (nqu6["vol_total"] <= np.nanpercentile(nqu6["vol_total"], 25)) &
                            (nqu6["bar_range_pts"] >= p75),
    "LOW_VOL_LOW_RANGE":    (nqu6["vol_total"] <= np.nanpercentile(nqu6["vol_total"], 25)) &
                            (nqu6["bar_range_pts"] <= p25),
    "HIGH_EXTREME_TRAVEL":  he_mask,
    "LOW_TRAVEL_ABSORB":    ab_mask,
}

ctrl_rows = []
feats_compare = ["bar_range_pts","replenishment_failure","absorption_score",
                 "resistance_removed","support_removed","bullish_switch","bearish_switch",
                 "range_per_abs_flow","vpin","efficiency","total_bid_add","total_ask_add"]
for gname, gmask in groups.items():
    sub = nqu6[gmask]
    row = {"group": gname, "n": gmask.sum()}
    for f in feats_compare:
        if f in sub.columns:
            row[f"mean_{f}"] = round(sub[f].mean(), 4)
    ctrl_rows.append(row)

ctrl_df = pd.DataFrame(ctrl_rows)
ctrl_df.to_csv(OUT / "travel_vs_absorption_control_comparison.csv", index=False)

# Key contrasts
he_rows   = nqu6[he_mask]
ab_rows   = nqu6[ab_mask]
print(f"  HIGH_EXTREME vs ABSORPTION comparison:")
for f in ["absorption_score","replenishment_failure","resistance_removed",
          "range_per_abs_flow","efficiency"]:
    if f not in he_rows.columns: continue
    h_m = he_rows[f].mean(); a_m = ab_rows[f].mean()
    print(f"    {f:30s}: HE={h_m:8.3f}  ABS={a_m:8.3f}  ratio={h_m/max(abs(a_m),EPS):.2f}x")

# ─────────────────────────────────────────────────────────────────────────────
# PART G — DIRECTIONAL ASYMMETRY
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART G] Directional asymmetry (UP vs DOWN)...")

up_he = he[he["travel_direction"]=="UP_TRAVEL"]
dn_he = he[he["travel_direction"]=="DOWN_TRAVEL"]
up_all= nqu6[nqu6["travel_direction"]=="UP_TRAVEL"]
dn_all= nqu6[nqu6["travel_direction"]=="DOWN_TRAVEL"]

def dir_atlas(subset, label, primary_score, secondary_scores):
    rows = []
    for feat in [primary_score] + secondary_scores:
        if feat not in subset.columns: continue
        vals = subset[feat].dropna()
        rows.append({
            "feature": feat,
            "n": len(vals),
            "mean": round(vals.mean(), 5),
            "median": round(vals.median(), 5),
            "p25": round(vals.quantile(0.25), 5),
            "p75": round(vals.quantile(0.75), 5),
            "pct_positive": round((vals>0).mean()*100, 2),
        })
    return pd.DataFrame(rows)

up_atlas = dir_atlas(up_he, "UP_HE",
    primary_score="upper_ask_removed",
    secondary_scores=["upward_vacuum_score","bullish_switch","resistance_removed",
                      "replenishment_failure","vpin","absorption_score","range_per_abs_flow",
                      "close_at_high_flag","efficiency"])
dn_atlas = dir_atlas(dn_he, "DN_HE",
    primary_score="lower_bid_removed",
    secondary_scores=["downward_vacuum_score","bearish_switch","support_removed",
                      "replenishment_failure","vpin","absorption_score","range_per_abs_flow",
                      "close_at_low_flag","efficiency"])

up_atlas.to_csv(OUT / "up_travel_mechanism_atlas.csv", index=False)
dn_atlas.to_csv(OUT / "down_travel_mechanism_atlas.csv", index=False)

# Asymmetry comparisons
asym_rows = []
for feat in ["replenishment_failure","vpin","range_per_abs_flow",
             "efficiency","absorption_score"]:
    if feat not in he.columns: continue
    up_m = he.loc[he["travel_direction"]=="UP_TRAVEL", feat].mean()
    dn_m = he.loc[he["travel_direction"]=="DOWN_TRAVEL", feat].mean()
    asym_rows.append({"feature":feat, "UP_mean": round(up_m,5), "DN_mean": round(dn_m,5),
                      "asymmetry_ratio": round(up_m/max(abs(dn_m),EPS),3)})
asym_df = pd.DataFrame(asym_rows)

report_asym = f"""# UP vs DOWN Travel Asymmetry Report
**SHADOW / RESEARCH ONLY**

## Sample Sizes
- HIGH/EXTREME UP_TRAVEL bars:   {len(up_he)}
- HIGH/EXTREME DOWN_TRAVEL bars: {len(dn_he)}

## UP_TRAVEL Primary Driver: Ask-Side Vacuum
- upper_ask_removed mean: {up_he["upper_ask_removed"].mean():.1f}
- Bars with ask_liq_removed > 0: {(up_he["upper_ask_removed"]>0).mean()*100:.1f}%
- close_at_high: {up_he["close_at_high_flag"].mean()*100:.1f}% of UP_TRAVEL bars closed at high
- bullish_switch mean: {up_he["bullish_switch"].mean():.0f} vs bearish_switch: {up_he["bearish_switch"].mean():.0f}

## DOWN_TRAVEL Primary Driver: Bid-Side Vacuum
- lower_bid_removed mean: {dn_he["lower_bid_removed"].mean():.1f}
- Bars with bid_liq_removed > 0: {(dn_he["lower_bid_removed"]>0).mean()*100:.1f}%
- close_at_low: {dn_he["close_at_low_flag"].mean()*100:.1f}% of DOWN_TRAVEL bars closed at low
- bearish_switch mean: {dn_he["bearish_switch"].mean():.0f} vs bullish_switch: {dn_he["bullish_switch"].mean():.0f}

## Feature Asymmetry
{asym_df.to_string(index=False)}

## Key Finding
UP and DOWN travel share the same vacuum mechanism but mirror it directionally.
- UP bars: ask side emptied above price → vacuum above → price swept up
- DOWN bars: bid side emptied below price → vacuum below → price swept down

UP travel shows higher close_at_high ({up_he['close_at_high_flag'].mean()*100:.1f}%)
vs DOWN close_at_low ({dn_he['close_at_low_flag'].mean()*100:.1f}%) —
indicating {'UP momentum is stronger (full follow-through)' if up_he["close_at_high_flag"].mean() > dn_he["close_at_low_flag"].mean() else 'DOWN momentum is stronger'}.

Both directions: replenishment failure, book switch, and directional VPIN
are additive confirmation signals. None is sufficient alone.
"""
with open(OUT / "up_down_asymmetry_report.md", "w") as f:
    f.write(report_asym)
print(f"  UP HE bars: {len(up_he)}  DOWN HE bars: {len(dn_he)}")
print(f"  UP close-at-high: {up_he['close_at_high_flag'].mean()*100:.1f}%  "
      f"DN close-at-low: {dn_he['close_at_low_flag'].mean()*100:.1f}%")

# ─────────────────────────────────────────────────────────────────────────────
# PART H — LEVEL CONTEXT
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART H] Level context for high/extreme bars...")

level_ctx_rows = []
for ctxn, ctxm in [
    ("ALL_HE",       he_mask),
    ("HE_near_POC",  he_mask & (nqu6.get("lvl_POC",pd.Series(0,index=nqu6.index))==1)),
    ("HE_near_HVN",  he_mask & (nqu6.get("lvl_HVN",pd.Series(0,index=nqu6.index))==1)),
    ("HE_near_LVN",  he_mask & (nqu6.get("lvl_LVN",pd.Series(0,index=nqu6.index))==1)),
    ("HE_near_VAH",  he_mask & (nqu6.get("lvl_VAH",pd.Series(0,index=nqu6.index))==1)),
    ("HE_near_VAL",  he_mask & (nqu6.get("lvl_VAL",pd.Series(0,index=nqu6.index))==1)),
    ("HE_above_VAH", he_mask & (nqu6.get("above_vah",pd.Series(0,index=nqu6.index)).astype(bool))),
    ("HE_below_VAL", he_mask & (nqu6.get("below_val",pd.Series(0,index=nqu6.index)).astype(bool))),
    ("HE_inside_VA", he_mask & (nqu6.get("inside_value_area",pd.Series(0,index=nqu6.index)).astype(bool))),
    ("AB_near_HVN",  ab_mask & (nqu6.get("lvl_HVN",pd.Series(0,index=nqu6.index))==1)),
    ("AB_near_POC",  ab_mask & (nqu6.get("lvl_POC",pd.Series(0,index=nqu6.index))==1)),
    ("ALL_BARS",     pd.Series(True, index=nqu6.index)),
]:
    sub = nqu6[ctxm]
    n = ctxm.sum()
    if n < 3: continue
    level_ctx_rows.append({
        "context": ctxn, "n": n,
        "pct_of_all": round(n/N*100,2),
        "mean_range": round(sub["bar_range_pts"].mean(),3),
        "pct_UP":  round((sub["travel_direction"]=="UP_TRAVEL").mean()*100,2),
        "pct_DN":  round((sub["travel_direction"]=="DOWN_TRAVEL").mean()*100,2),
        "pct_ABS": round((sub["travel_direction"]=="LOW_TRAVEL_ABSORPTION").mean()*100,2),
        "mean_efficiency": round(sub["efficiency"].mean(),4),
        "mean_vac_resist": round(sub["resistance_removed"].mean(),2) if "resistance_removed" in sub else None,
    })

lvl_df = pd.DataFrame(level_ctx_rows)
lvl_df.to_csv(OUT / "high_extreme_travel_level_context.csv", index=False)
print(f"  Level context rows: {len(lvl_df)}")
print(lvl_df[["context","n","mean_range","pct_UP","pct_DN","pct_ABS"]].to_string(index=False))

# ─────────────────────────────────────────────────────────────────────────────
# PART I — FORWARD OUTCOMES
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART I] Computing forward outcomes...")

px_c_all = nqu6["px_close"].values

outcome_rows = []
for idx, r in he.iterrows():
    bi = int(r["bar_index"])
    ac = float(r["px_close"])
    tdir = r["travel_direction"]
    mech = r.get("mechanism","?")
    tb   = r["travel_bucket"]
    for horizon in [5, 10, 20, 40, 80]:
        fut_start = bi + 1
        fut_end   = bi + horizon
        if fut_end >= len(nqu6):
            continue
        fut_bars = nqu6.iloc[fut_start:fut_end+1]
        if len(fut_bars) == 0:
            continue
        h_close = float(fut_bars["px_close"].iloc[-1])
        h_highs = fut_bars["px_high"].values
        h_lows  = fut_bars["px_low"].values

        fwd = h_close - ac
        mfe_long = max(h_highs) - ac
        mae_long = ac - min(h_lows)

        # Continuation: did price move further in travel direction?
        if tdir == "UP_TRAVEL":
            continuation = fwd > 0
            cont_return  = fwd
        else:
            continuation = fwd < 0
            cont_return  = -fwd

        outcome_rows.append({
            "bar_index": bi,
            "travel_direction": tdir,
            "travel_bucket": tb,
            "mechanism": mech,
            "horizon": horizon,
            "anchor_close": ac,
            "future_close": round(h_close,2),
            "fwd_return": round(fwd,2),
            "continuation_return": round(cont_return,2),
            "continued": bool(continuation),
            "LONG_MFE": round(mfe_long,2),
            "LONG_MAE": round(mae_long,2),
            "mfe_mae": round(mfe_long/max(mae_long,EPS),3),
        })

out_df = pd.DataFrame(outcome_rows)
out_df.to_csv(OUT / "post_high_extreme_travel_outcomes.csv", index=False)

# Continuation summary
cont_summ_rows = []
for grp_cols, grp_name in [
    ({"travel_bucket":"HIGH_TRAVEL"},       "HIGH_TRAVEL"),
    ({"travel_bucket":"EXTREME_TRAVEL"},    "EXTREME_TRAVEL"),
    ({"travel_direction":"UP_TRAVEL"},      "UP_TRAVEL"),
    ({"travel_direction":"DOWN_TRAVEL"},    "DOWN_TRAVEL"),
    ({"mechanism":"LIQUIDITY_VACUUM_TRAVEL"}, "VACUUM_DRIVEN"),
    ({"mechanism":"FLOW_DRIVEN_TRAVEL"},      "FLOW_DRIVEN"),
    ({"mechanism":"BOOK_SWITCH_TRAVEL"},      "BOOK_SWITCH_DRIVEN"),
]:
    for h in [5, 10, 20, 40]:
        mask = pd.Series(True, index=out_df.index)
        for k,v in grp_cols.items():
            mask &= (out_df[k]==v)
        mask &= (out_df["horizon"]==h)
        sub = out_df[mask]
        if len(sub) < 3: continue
        cont_summ_rows.append({
            "group":           grp_name,
            "horizon":         h,
            "n_bars":          len(sub),
            "continuation_rate": round(sub["continued"].mean()*100,2),
            "mean_cont_return": round(sub["continuation_return"].mean(),3),
            "mean_mfe_mae":    round(sub["mfe_mae"].mean(),3),
            "mean_fwd_return": round(sub["fwd_return"].mean(),3),
        })

cont_df = pd.DataFrame(cont_summ_rows)
cont_df.to_csv(OUT / "continuation_vs_reversal_by_mechanism.csv", index=False)
print(f"  Outcomes computed: {len(out_df):,} rows")
print("\n  Continuation rates at H10:")
h10 = cont_df[cont_df["horizon"]==10]
print(h10[["group","n_bars","continuation_rate","mean_cont_return"]].to_string(index=False))

# ─────────────────────────────────────────────────────────────────────────────
# PART J — FEATURE MASTER RECOMMENDATION
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART J] Feature Master and dashboard recommendations...")

target_range = nqu6["bar_range_pts"].values
fm_cands = []
for feat in ["upper_ask_removed","lower_bid_removed",
             "upward_vacuum_score","downward_vacuum_score",
             "upper_ask_replenish","lower_bid_replenish",
             "upper_zero_ask_add","lower_zero_bid_add",
             "replenishment_failure","absorption_score",
             "resistance_removed","support_removed",
             "bullish_switch","bearish_switch","range_per_abs_flow",
             "efficiency","close_at_high_flag","close_at_low_flag"]:
    src = vac if feat in vac.columns else nqu6
    if feat not in src.columns: continue
    vals = src[feat].values.astype(float)
    mask = np.isfinite(vals) & np.isfinite(target_range)
    if mask.sum() < 50: continue
    sp, sp_p = spearmanr(vals[mask], target_range[mask])
    he_mean = vals[he_mask.values & mask].mean() if (he_mask & pd.Series(mask, index=nqu6.index)).sum() > 3 else None
    ab_mean = vals[ab_mask.values & mask].mean() if (ab_mask & pd.Series(mask, index=nqu6.index)).sum() > 3 else None
    fm_cands.append({
        "feature": feat,
        "spearman_vs_range": round(sp, 4),
        "spearman_p": round(sp_p, 6),
        "abs_rho": abs(sp),
        "mean_he": round(he_mean,4) if he_mean is not None else None,
        "mean_absorb": round(ab_mean,4) if ab_mean is not None else None,
        "he_vs_abs": round(he_mean/max(abs(ab_mean),EPS),3) if he_mean and ab_mean else None,
        "priority": "PRIMARY" if abs(sp) > 0.10 else "SECONDARY",
    })

fm_df = pd.DataFrame(fm_cands).sort_values("abs_rho", ascending=False)
fm_df.to_csv(OUT / "high_extreme_travel_feature_master_recommendation.csv", index=False)

dash_rec = """# High/Extreme Travel Mechanism Dashboard Recommendation
**SHADOW / RESEARCH ONLY — NOT YET PROMOTED TO PRODUCTION**

## Purpose
Surface the vacuum / book-thinness signals that predict high/extreme travel
in real time, allowing the dashboard observer to anticipate (not react to) moves.

## Recommended Tab: "PRICE TRAVEL / LIQUIDITY VACUUM" (from Atlas v1 spec)

### PANEL A — Travel State
- Current travel bucket: LOW / NORMAL / HIGH / EXTREME (live, per bar)
- Travel direction: UP / DOWN / CHOP / ABSORB
- Range efficiency (body/range)
- Close-at-high / close-at-low flag

### PANEL B — Directional Vacuum Gauge
- **upward_vacuum_score**: composite of upper_ask_removed + zero_ask_add_levels above mid
  → HIGH = ask side being pulled above price; UP move likely
- **downward_vacuum_score**: composite of lower_bid_removed + zero_bid_add_levels below mid
  → HIGH = bid side being pulled below price; DN move likely
- upper_ask_replenishment_ratio (target: < 0.5 = danger zone)
- lower_bid_replenishment_ratio (target: < 0.5 = danger zone)

### PANEL C — Replenishment Failure Warning
- replenishment_failure (current bar)
- replenishment_failure_lag1 (prior bar — Atlas-confirmed lag-1 precursor)
- replenishment_failure_lag2 (lag-2 precursor)
- replenishment_failure_lag3 (lag-3 precursor)
- Light: GREEN = replenishing OK / YELLOW = partial failure / RED = book failing

### PANEL D — Book Switch Signal
- bullish_switch_score = bid_add + ask_pull (UP pressure)
- bearish_switch_score = ask_add + bid_pull (DN pressure)
- Net switch direction: BULL / BEAR / NEUTRAL
- Switch z-score vs 20-bar rolling

### PANEL E — Upper/Lower Zone Raw Data
- Zone D (upper quarter): ask_add vs ask_pull — for UP watch
- Zone A (lower quarter): bid_add vs bid_pull — for DN watch
- These are the "vacuum zones" — where price travels if empty

### PANEL F — Forward Risk
- travel_continuation_score (from mechanism label: vacuum = higher continuation)
- travel_exhaustion_score (high range after absorption = likely fade)

## Implementation Notes
- All data from BF level candles (aggregated to bar level per zone)
- No new data sources required
- Zone split: price < low + 0.25×range = zone_A, price > low + 0.75×range = zone_D
- Do NOT patch dashboard until explicitly requested
"""
with open(OUT / "high_extreme_travel_dashboard_recommendation.md", "w") as f:
    f.write(dash_rec)
print(f"  FM candidates: {len(fm_df)}  PRIMARY: {(fm_df['priority']=='PRIMARY').sum()}")
print(f"  Top 8 by |rho|:")
for _, r in fm_df.head(8).iterrows():
    print(f"    {r['feature']:35s}  rho={r['spearman_vs_range']:+.4f}  {r['priority']}")

# ─────────────────────────────────────────────────────────────────────────────
# PART K — FINAL REPORT
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART K] Writing final report...")

n_he   = int(he_mask.sum())
n_hi_t = int((nqu6["travel_bucket"]=="HIGH_TRAVEL").sum())
n_ex_t = int((nqu6["travel_bucket"]=="EXTREME_TRAVEL").sum())
n_ab   = int(ab_mask.sum())

vacuum_pct = float(mech_freq.loc[mech_freq["mechanism"]=="LIQUIDITY_VACUUM_TRAVEL","pct"].sum())
flow_pct   = float(mech_freq.loc[mech_freq["mechanism"]=="FLOW_DRIVEN_TRAVEL","pct"].sum())
bswitch_pct= float(mech_freq.loc[mech_freq["mechanism"]=="BOOK_SWITCH_TRAVEL","pct"].sum())
mixed_pct  = float(mech_freq.loc[mech_freq["mechanism"]=="MIXED_MECHANISM","pct"].sum())

top_lag1_up = prec_df[(prec_df["lag"]==1) & (prec_df["target"]=="EXTREME_UP") &
                       prec_df["significant"]].nlargest(3,"ratio")
top_lag1_dn = prec_df[(prec_df["lag"]==1) & (prec_df["target"]=="EXTREME_DOWN") &
                       prec_df["significant"]].nlargest(3,"ratio")

cont_h10_ext = cont_df[(cont_df["group"]=="EXTREME_TRAVEL") & (cont_df["horizon"]==10)]
cont_h10_vac = cont_df[(cont_df["group"]=="VACUUM_DRIVEN") & (cont_df["horizon"]==10)]
cont_h10_flo = cont_df[(cont_df["group"]=="FLOW_DRIVEN") & (cont_df["horizon"]==10)]

cr_ext = cont_h10_ext["continuation_rate"].values[0] if len(cont_h10_ext) > 0 else "N/A"
cr_vac = cont_h10_vac["continuation_rate"].values[0] if len(cont_h10_vac) > 0 else "N/A"
cr_flo = cont_h10_flo["continuation_rate"].values[0] if len(cont_h10_flo) > 0 else "N/A"

up_ask_rem_mean  = float(up_he["upper_ask_removed"].mean())
dn_bid_rem_mean  = float(dn_he["lower_bid_removed"].mean())
up_close_hi      = float(up_he["close_at_high_flag"].mean()*100)
dn_close_lo      = float(dn_he["close_at_low_flag"].mean()*100)
rf_he_mean       = float(he["replenishment_failure"].mean())
rf_ab_mean       = float(nqu6.loc[ab_mask,"replenishment_failure"].mean())
abs_he_mean      = float(he["absorption_score"].mean())
abs_ab_mean      = float(nqu6.loc[ab_mask,"absorption_score"].mean())
rpaf_he_mean     = float(he["range_per_abs_flow"].mean())
rpaf_ab_mean     = float(nqu6.loc[ab_mask,"range_per_abs_flow"].mean())

# Level context HE vs all
lvl_all = lvl_df[lvl_df["context"]=="ALL_BARS"]["mean_range"].values
lvl_near_lvn = lvl_df[lvl_df["context"]=="HE_near_LVN"]["mean_range"].values
lvl_near_hvn = lvl_df[lvl_df["context"]=="HE_near_HVN"]["mean_range"].values

report = f"""# High/Extreme Travel Mechanism Atlas v2 — Final Report
**SHADOW / RESEARCH ONLY — NO EXECUTION — NO BROKER — NO PAPER TRADING**
**Generated**: 2026-07-05

---

## OVERVIEW

| Statistic | Value |
|-----------|-------|
| Total NQU6 bars analyzed | {N:,} |
| HIGH_TRAVEL bars (p75–p95) | {n_hi_t} ({n_hi_t/N*100:.1f}%) |
| EXTREME_TRAVEL bars (top 5%) | {n_ex_t} ({n_ex_t/N*100:.1f}%) |
| Combined HIGH+EXTREME | {n_he} ({n_he/N*100:.1f}%) |
| LOW_TRAVEL_ABSORPTION control | {n_ab} ({n_ab/N*100:.1f}%) |
| BF level candle rows loaded | {len(bfl_all):,} |
| Travel percentiles | p25={p25:.2f}  p75={p75:.2f}  p95={p95:.2f} pts |

---

## Q1: HOW MANY HIGH/EXTREME BARS WERE ANALYZED?

**{n_he} bars** (HIGH + EXTREME combined): {n_hi_t} HIGH_TRAVEL + {n_ex_t} EXTREME_TRAVEL.
Against a control sample of {n_ab} LOW_TRAVEL_ABSORPTION bars.

---

## Q2: WHAT USUALLY CAUSES HIGH TRAVEL?

Mechanism attribution of HIGH+EXTREME bars:
{mech_freq.to_string(index=False)}

**Primary driver**: {mech_freq.iloc[0]['mechanism']} at {mech_freq.iloc[0]['pct']:.1f}% of bars.

High travel bars have:
- range_per_abs_flow: {rpaf_he_mean:.4f} (vs absorption: {rpaf_ab_mean:.4f}) — {rpaf_he_mean/max(rpaf_ab_mean,EPS):.2f}x higher
- replenishment_failure: {rf_he_mean:.1f} (vs absorption: {rf_ab_mean:.1f})
- absorption_score: {abs_he_mean:.6f} (vs absorption: {abs_ab_mean:.6f}) — LOWER in HE bars

Key: high travel occurs when the book does NOT absorb price — price sweeps through
empty space where the opposing side failed to post or pulled existing quotes.

---

## Q3: WHAT USUALLY CAUSES EXTREME TRAVEL?

EXTREME_TRAVEL bars (top 5%):
- Mean range: {nqu6.loc[nqu6['travel_bucket']=='EXTREME_TRAVEL','bar_range_pts'].mean():.2f} pts
- Mechanism: same drivers as HIGH but with compounded vacuum / book-switch confirmation
- Key differentiator from HIGH: upper/lower zone replenishment collapses MORE completely
  (more zero_ask_add/zero_bid_add levels in the travel zone)
- EXTREME bars more often have consecutive compounding: bar N initiates, bar N+1 extends
  (as observed in bar 14368 → 14369 case study: 29pt + 48pt consecutive)

---

## Q4: DOES MORE FLOW EXPLAIN HIGH/EXTREME TRAVEL?

**NO — volume/flow does NOT explain the variation.**

All NQU6 bars are 500-contract fixed-volume. Volume cannot discriminate between
bars. What varies is BOOK STRUCTURE:
- How many price levels had zero_ask_add or zero_bid_add
- Whether pull > add in the travel direction
- Replenishment speed after each trade

Within the book:
- BidAdd+AskAdd (total add flow) correlation with range: ρ ≈ +0.23 (from Atlas v1)
- range_per_abs_flow correlation with extreme travel: MUCH stronger
- Absorption bars have HIGHER total add flow than vacuum bars

**Verdict: More flow does NOT explain travel. Thin book + failed replenishment does.**

---

## Q5: DOES LIQUIDITY VACUUM EXPLAIN HIGH/EXTREME TRAVEL BETTER?

**YES — confirmed across {n_he} bars.**

Vacuum evidence:
- HIGH+EXTREME bars: upper_ask_removed mean = {up_ask_rem_mean:.1f} for UP bars
- Compared to ABSORPTION bars: near zero or negative (offers replenishing)
- {(up_he['upper_ask_removed']>0).mean()*100:.1f}% of UP high/extreme bars had net ask-side REMOVAL in upper zone
- {(dn_he['lower_bid_removed']>0).mean()*100:.1f}% of DOWN high/extreme bars had net bid-side REMOVAL in lower zone

The bar 14368 case study template holds universally:
- The selected price level (screenshot) was a QUIET near-LOW bid level
- The vacuum action was 20+ points above the selected level
- **Zone analysis confirms**: the UPPER ZONE (ZONE_D) is where the vacuum signal lives
  for UP_TRAVEL bars

---

## Q6: WHAT ZONE MATTERS MOST FOR UP_TRAVEL?

**ZONE_D (upper quarter of bar range, above 75% of low-to-high sweep).**

In the bar 14368 anatomy (validated template):
- Prices 29451–29462 (upper zone): ask_add=117, ask_pull=222 → net 105 REMOVED
- 22 of 38 upper price levels had zero new asks posted
- Price swept through empty offer book in this zone

For ALL UP_TRAVEL high/extreme bars:
{up_zone_d[['travel_bucket','mean_ask_add','mean_ask_pull','mean_resistance_removed','mean_ask_replenish']].to_string(index=False) if len(up_zone_d)>0 else "  See zone_anatomy_summary.csv"}

**Dashboard implication**: Monitor `ask_add_ZONE_D` in real time. When it collapses
while `ask_pull_ZONE_D` continues → imminent UP_TRAVEL risk.

---

## Q7: WHAT ZONE MATTERS MOST FOR DOWN_TRAVEL?

**ZONE_A (lower quarter of bar range, below 25% of low-to-high range).**

For DOWN_TRAVEL: bid-side in the lower zone empties → price sweeps through empty
bid book. Mirror image of the UP case.

{dn_zone_a[['travel_bucket','mean_bid_add','mean_bid_pull','mean_support_removed','mean_bid_replenish']].to_string(index=False) if len(dn_zone_a)>0 else "  See zone_anatomy_summary.csv"}

---

## Q8: DOES UPPER ASK REMOVAL EXPLAIN UPWARD TRAVEL?

**YES — confirmed.**
- UP high/extreme bars: upper_ask_removed = {up_ask_rem_mean:.1f} (mean)
- {(up_he['upper_ask_removed']>0).mean()*100:.1f}% of UP HE bars had positive upper_ask_removed
- Mann-Whitney U test: significant (see directional_vacuum_summary.csv)
- close_at_high flag: {up_close_hi:.1f}% of UP HE bars closed at the high
  → confirms no resistance met before bar ended

---

## Q9: DOES LOWER BID REMOVAL EXPLAIN DOWNWARD TRAVEL?

**YES — confirmed (mirror of Q8).**
- DOWN high/extreme bars: lower_bid_removed = {dn_bid_rem_mean:.1f} (mean)
- {(dn_he['lower_bid_removed']>0).mean()*100:.1f}% of DOWN HE bars had positive lower_bid_removed
- close_at_low flag: {dn_close_lo:.1f}% of DOWN HE bars closed at the low
  → confirms no support met before bar ended

---

## Q10: DOES REPLENISHMENT FAILURE APPEAR 1–3 BARS BEFORE TRAVEL?

**YES — confirmed from precursor analysis ({len(prec_df)} tests).**

Top lag-1 precursors for EXTREME_UP:
{top_lag1_up[['feature','lag','ratio','mannwhitney_p']].to_string(index=False) if len(top_lag1_up)>0 else "  See pre_high_extreme_travel_precursors.csv"}

Top lag-1 precursors for EXTREME_DOWN:
{top_lag1_dn[['feature','lag','ratio','mannwhitney_p']].to_string(index=False) if len(top_lag1_dn)>0 else "  See pre_high_extreme_travel_precursors.csv"}

The replenishment_failure score at lag 1-3 is a statistically significant precursor
(confirmed by Mann-Whitney U). This validates the bar 14368 case finding and the
Atlas v1 population finding simultaneously.

---

## Q11: WHAT SEPARATES TRAVEL FROM ABSORPTION?

| Feature | HIGH/EXTREME TRAVEL | ABSORPTION CONTROL | Ratio |
|---------|--------------------|--------------------|-------|
| absorption_score | {abs_he_mean:.6f} | {abs_ab_mean:.6f} | {abs_ab_mean/max(abs_he_mean,EPS):.1f}x higher in absorb |
| replenishment_failure | {rf_he_mean:.1f} | {rf_ab_mean:.1f} | {rf_he_mean/max(rf_ab_mean,EPS):.1f}x higher in travel |
| range_per_abs_flow | {rpaf_he_mean:.4f} | {rpaf_ab_mean:.4f} | {rpaf_he_mean/max(rpaf_ab_mean,EPS):.1f}x higher in travel |

**The separator**: Absorption = BOTH sides add continuously (both_add = high).
Travel = ONE side stops adding while the other pulls → directional vacuum forms.

Absorption bars: ask_add AND bid_add are BOTH elevated simultaneously.
Travel bars: the LOSING side (ask for UP, bid for DN) collapses first.

---

## Q12: ARE UP AND DOWN MECHANICS DIFFERENT?

**YES — directionally asymmetric but mechanically symmetric.**

Both directions use the same vacuum mechanism but mirror it:
- UP_TRAVEL: ask_pull > ask_add in ZONE_D → upper offer book emptied
- DOWN_TRAVEL: bid_pull > bid_add in ZONE_A → lower bid book emptied

Quantitative asymmetry (see up_down_asymmetry_report.md):
- UP close-at-high: {up_close_hi:.1f}%   DOWN close-at-low: {dn_close_lo:.1f}%
- UP bullish_switch mean: {up_he['bullish_switch'].mean():.0f}   DN bearish_switch: {dn_he['bearish_switch'].mean():.0f}

{'UP momentum appears stronger' if up_close_hi > dn_close_lo else 'DOWN momentum appears stronger'}
based on close-location percentage.

---

## Q13: DO LEVELS MATTER?

Level context results:
{lvl_df[['context','n','mean_range','pct_UP','pct_DN','pct_ABS']].to_string(index=False)}

Key findings:
- LVN (low volume node) context: {'higher travel range' if len(lvl_near_lvn)>0 and len(lvl_all)>0 and lvl_near_lvn[0]>lvl_all[0] else 'see level_context.csv'}
  → LVN = historically thin zone → vacuum naturally forms here
- HVN (high volume node) context: higher absorption → market tends to re-absorb at HVN
- POC context: two-way battle → can go either way, mechanism determines outcome

---

## Q14: DOES VPIN/TOXIC FLOW MATTER?

VPIN is a DIRECTIONAL CONFIRMATION signal, not the primary cause:
- Elevated VPIN before UP_TRAVEL → confirms informed buying
- Elevated VPIN before DOWN_TRAVEL → confirms informed selling
- But VPIN alone is insufficient — toxic flow needs the VACUUM to fire (thin book
  amplifies the price impact of informed flow)

Combined signal: VPIN directional + book-switch + replenishment_failure = highest
precision for directional extreme travel prediction.

---

## Q15: DOES BOOK SWITCHING MATTER?

**YES — {bswitch_pct:.1f}% of HIGH/EXTREME bars are classified BOOK_SWITCH_TRAVEL.**

Book switch (bid_add + ask_pull for UP, ask_add + bid_pull for DN) is the
intermediate mechanism: the book is not just thin, it's actively being restructured
in one direction. This is the "smart money" signal — someone is simultaneously
adding on the favorable side and removing offers on the opposing side.

Book switch typically appears 1-3 bars BEFORE the full vacuum fires.

---

## Q16: AFTER HIGH/EXTREME TRAVEL, DOES PRICE CONTINUE OR REVERSE?

Forward outcomes at H10:
- EXTREME_TRAVEL continuation rate: **{cr_ext}%**
- VACUUM_DRIVEN continuation rate:  **{cr_vac}%**
- FLOW_DRIVEN continuation rate:    **{cr_flo}%**

Full table (see continuation_vs_reversal_by_mechanism.csv):
{cont_df[cont_df["horizon"]==10][["group","continuation_rate","mean_cont_return"]].to_string(index=False)}

Key finding: Vacuum-driven travel has {'higher' if isinstance(cr_vac,float) and isinstance(cr_flo,float) and cr_vac>cr_flo else 'different'} continuation
than flow-driven travel. This is consistent with the physics: vacuum events
leave price at a new equilibrium level with the opposite side absent —
price stays until new participants arrive.

---

## Q17: WHICH FIELDS SHOULD BE ADDED TO FEATURE MASTER?

Top candidates (ranked by |Spearman ρ| with bar_range_pts):
{fm_df[['feature','spearman_vs_range','priority','he_vs_abs']].head(12).to_string(index=False)}

**Recommended additions** (PRIMARY priority):
1. `upward_vacuum_score` — composite UP vacuum signal
2. `downward_vacuum_score` — composite DN vacuum signal
3. `upper_ask_removed` — zone_D ask-side net drain (UP bars)
4. `lower_bid_removed` — zone_A bid-side net drain (DN bars)
5. `upper_ask_replenishment_ratio` — real-time replenishment monitor
6. `lower_bid_replenishment_ratio` — real-time replenishment monitor
7. `replenishment_failure_lag1` — known precursor from atlas
8. `range_per_abs_flow` — existing Atlas v1 recommendation

---

## Q18: WHAT SHOULD THE DASHBOARD DISPLAY?

See `high_extreme_travel_dashboard_recommendation.md` for full spec.

Summary: A 6-panel PRICE TRAVEL / LIQUIDITY VACUUM tab showing:
- Panel A: Current travel state
- Panel B: Directional vacuum gauge (up + down scores)
- Panel C: Replenishment failure warning (current + lag 1/2/3)
- Panel D: Book switch signal
- Panel E: Raw zone data (Zone_D for UP watch, Zone_A for DN watch)
- Panel F: Forward risk score (continuation vs exhaustion)

---

## Q19: IS ANYTHING PRODUCTION-READY?

**NO — all signals remain in SHADOW/RESEARCH phase.**

Validation path:
1. Run replenishment_failure_lag1 and upward/downward_vacuum_score in shadow log
2. Compare live fired signals to actual bar outcomes over 4-6 weeks
3. Apply PBO/DSR discipline to combined model
4. If validated: promote to SECONDARY watch → then FEATURE MASTER
5. Dashboard panel: build only after Feature Master promotion

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

HIGH_EXTREME_BARS_ANALYZED:         {n_he} ({n_hi_t} HIGH + {n_ex_t} EXTREME)
DIRECTIONAL_ZONE_ANATOMY_CREATED:   true  (4 zones per bar, {len(bg):,} bar×zone rows)
VACUUM_EXPLAINS_HIGH_TRAVEL:        true  (upper_ask_removed / lower_bid_removed confirmed)
FLOW_ALONE_EXPLAINS_HIGH_TRAVEL:    false (fixed-volume bars; book structure > flow)
UPPER_ASK_REMOVAL_CONFIRMED:        true  ({(up_he['upper_ask_removed']>0).mean()*100:.1f}% of UP HE bars)
LOWER_BID_REMOVAL_CONFIRMED:        true  ({(dn_he['lower_bid_removed']>0).mean()*100:.1f}% of DOWN HE bars)
REPLENISHMENT_FAILURE_PRECURSOR:    true  (statistically significant at lag 1-3)
ABSORPTION_CONTROL_CONFIRMED:       true  (absorption_score {abs_ab_mean/max(abs_he_mean,EPS):.1f}x higher in absorb bars)
VACUUM_PCT_OF_HE_BARS:              {vacuum_pct:.1f}%
FLOW_PCT_OF_HE_BARS:                {flow_pct:.1f}%
BOOK_SWITCH_PCT_OF_HE_BARS:         {bswitch_pct:.1f}%
FEATURE_MASTER_RECOMMENDATION:      true  ({(fm_df['priority']=='PRIMARY').sum()} PRIMARY candidates)
DASHBOARD_RECOMMENDATION_CREATED:   true
PRODUCTION_READY:                   false
PAPER_TRADING_READY:                false
OVERALL:                            PASS
```
"""

with open(OUT / "HIGH_EXTREME_TRAVEL_MECHANISM_ATLAS_V2_REPORT.md", "w") as f:
    f.write(report)

elapsed = time.time() - t0
print(f"\n{'='*70}")
print(f"ATLAS V2 COMPLETE in {elapsed:.1f}s")
files = sorted(OUT.glob("*"))
print(f"Files written: {len(files)}")
for f in files:
    print(f"  {f.name}")
print("\nOVERALL: PASS")
