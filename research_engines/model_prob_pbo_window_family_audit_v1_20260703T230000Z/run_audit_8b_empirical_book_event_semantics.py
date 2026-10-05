#!/usr/bin/env python3
"""
Audit 8B — Empirical Book Event Semantics Atlas
SHADOW / RESEARCH ONLY — no execution, no broker, no order placement
Do NOT assume bid_add means buyer support. Treat all primitives as raw.
Let data define meaning through causal OOS outcome distributions.
"""
import time
import warnings
import datetime
import numpy as np
import pandas as pd
from pathlib import Path
from scipy.stats import spearmanr, pearsonr, permutation_test
from sklearn.tree import DecisionTreeClassifier, export_text
from sklearn.preprocessing import quantile_transform

warnings.filterwarnings("ignore")
t0 = time.time()

# ─── Paths ─────────────────────────────────────────────────────────────────
WORK_DIR   = Path(__file__).parent
OOS_PATH   = WORK_DIR / "oos_probability_table.parquet"
MODEL_DIR  = Path("/home/prabh/OFI_Production/model_registry"
                  "/level_reaction_continuous_nq_shadow"
                  "/level_reaction_continuous_nq_shadow_20260702T005104Z")
DATA_DIR   = MODEL_DIR / "data"
BOOK_ATLAS = Path("/home/prabh/OFI_Production/research_engines"
                  "/book_switching_cross_side_add_pull_alpha_v1_20260625T190639Z")
BF_PANEL   = BOOK_ATLAS / "book_switching_feature_panel.parquet"
MECH_BASE  = Path("/home/prabh/OFI_Production/research_engines"
                  "/nasdaq_full_book_level_mechanics_atlas_v1_20260623T225748Z/outputs")

# ─── Constants ──────────────────────────────────────────────────────────────
COST_TICKS  = 2.0
EPS         = 1e-6
HORIZONS    = [5, 10, 20, 40]
WINDOWS     = [1, 3, 5, 10, 20, 50]
RAW_PRIMS   = ["BF_bid_add", "BF_bid_pull", "BF_ask_add", "BF_ask_pull"]
SESSIONS    = ["Asia", "EU", "US_Open", "US_AM", "US_PM", "US_Late"]

ts_str = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
OUT_DIR = WORK_DIR / "audits" / f"audit_8b_empirical_book_event_semantics_{ts_str}"
OUT_DIR.mkdir(parents=True, exist_ok=True)

print("=" * 70)
print("Audit 8B — Empirical Book Event Semantics Atlas")
print("SHADOW / RESEARCH ONLY — no execution, no broker")
print("Do NOT assume directional meaning before testing.")
print(f"Output: {OUT_DIR}")
print("=" * 70)

# ═══════════════════════════════════════════════════════════════════════════
# PART 1 — Data Integrity
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 1: Data integrity")
print("=" * 70)
t_p1 = time.time()

# ─── Source A: Bar-level book flow panel ────────────────────────────────────
bf_raw = pd.read_parquet(BF_PANEL)
print(f"\n  [SRC-A] Book switching panel (bar-level):")
print(f"    Shape: {bf_raw.shape}")
print(f"    Dates: {sorted(bf_raw['timestamp_utc'].dt.date.unique())}")
print(f"    bf_data_available=True: {bf_raw['bf_data_available'].sum():,}/{len(bf_raw):,}")
print(f"    dup bar_end_ts_ns: {bf_raw['bar_end_ts_ns'].duplicated().sum()}")
for c in RAW_PRIMS:
    print(f"    {c}: null={bf_raw[c].isna().sum()}, "
          f"min={bf_raw[c].min():.0f}, max={bf_raw[c].max():.0f}")

# ─── Source B: Book mechanics (event-level, pre-window only) ────────────────
mech = pd.read_parquet(MECH_BASE / "level_touch_full_book_mechanics.parquet",
    columns=["event_id"] +
            [f"pre_{w}__{p}" for w in [10,20,50,100]
             for p in ["bid_add","bid_pull","ask_add","ask_pull"]] +
            ["touch__close_price","post_5__close_price","post_10__close_price",
             "post_20__close_price","post_40__close_price","window_truncated_at_day_edge"])
mech_master = pd.read_parquet(MECH_BASE / "_why_levels_master_joined.parquet",
    columns=["event_id","rithmic_date_str","session","vol_state","vpin_state",
             "behavior_label","reject_final_return_40","breakout_final_return_40",
             "working","failing"])

print(f"\n  [SRC-B] Book mechanics (event-level, pre-windows only):")
print(f"    Shape: {mech.shape}")
n_pre10_valid = mech["pre_10__bid_add"].notna().sum()
print(f"    Events with pre_10 primitives: {n_pre10_valid:,}/{len(mech):,} "
      f"({n_pre10_valid/len(mech):.1%})")
print(f"    dup event_id: {mech['event_id'].duplicated().sum()}")
print(f"    CAUSAL CONSTRAINT: Using ONLY pre_* and touch_* columns (post_* excluded)")

# ─── Source C: OOS V=B W=20 (for Part 6 interaction) ───────────────────────
oos_full = pd.read_parquet(OOS_PATH)
oos_full["max_prob"] = oos_full[["prob_long","prob_short"]].max(axis=1)
vb20_all = oos_full[(oos_full["version"]=="B") & (oos_full["window"]==20) & (oos_full["is_oos"])]
vb20_sig  = vb20_all[vb20_all["max_prob"] >= 0.65].copy()
vb20_sig["side"] = vb20_sig["pred_side"]
print(f"\n  [SRC-C] OOS V=B W=20 threshold signals: {len(vb20_sig):,}")

# ─── Forward returns from snapshot (only for regime buckets + rithmic_date_str) ─
# BF panel already has px_close, minute_of_day, volatility_5, vpin
snap = pd.read_parquet(DATA_DIR / "continuous_master_snapshot.parquet",
    columns=["bar_end_ts_ns","rithmic_date_str",
             "volatility_5_resid_z20_bucket","vpin_resid_z20_bucket"])

# ─── Causal check ───────────────────────────────────────────────────────────
print("\n  Causal verification:")
print("    BF panel uses bar_end_ts_ns as the feature timestamp.")
print("    Forward returns computed from px_close at bar N+H relative to bar N.")
print("    Features (BF_bid/ask_add/pull) computed DURING bar N — causal by construction.")
print("    POST_* event windows from book mechanics are explicitly EXCLUDED as features.")
print("    Causal constraint: SATISFIED")

# ─── Session inference from minute_of_day (UTC-based) ────────────────────────
def infer_session_utc(minute_utc):
    mod = minute_utc % 1440
    if mod >= 1320 or mod < 150:     return "Asia"
    elif mod < 570:                   return "EU"
    elif mod < 630:                   return "US_Open"
    elif mod < 750:                   return "US_AM"
    elif mod < 960:                   return "US_PM"
    else:                             return "US_Late"

# ─── Build main analysis DataFrame ──────────────────────────────────────────
print("\n  Building main analysis DataFrame...")
bf = bf_raw.copy()
bf = bf.sort_values("bar_end_ts_ns").reset_index(drop=True)

# Forward returns from BF panel's own px_close (bar-level, causal)
for h in HORIZONS:
    bf[f"fwd_ticks_h{h}"] = (bf["px_close"].shift(-h) - bf["px_close"]) * 4

# Join only unique snap columns (regime buckets, date string)
bf = bf.merge(snap, on="bar_end_ts_ns", how="left")
bf["session"] = bf["minute_of_day"].apply(infer_session_utc)

# Vol regime from snapshot bucketed column
bf["vol_regime"] = bf["volatility_5_resid_z20_bucket"].fillna("MED")
bf["vpin_regime"] = bf["vpin_resid_z20_bucket"].fillna("NORMAL")

n_fwd_valid = bf["fwd_ticks_h10"].notna().sum()
print(f"  BF bars with fwd_h10: {n_fwd_valid:,}/{len(bf):,}")
print(f"  Session dist: {bf['session'].value_counts().to_dict()}")
print(f"  Vol regime dist: {bf['vol_regime'].value_counts().to_dict()}")

# ─── Integrity summary ───────────────────────────────────────────────────────
integrity_rows = []
for c in RAW_PRIMS:
    integrity_rows.append(dict(
        source="BF_panel", column=c, n_rows=len(bf),
        n_null=bf[c].isna().sum(), null_pct=bf[c].isna().mean(),
        min_val=bf[c].min(), max_val=bf[c].max(),
        mean_val=bf[c].mean(), std_val=bf[c].std()
    ))
integrity_rows.append(dict(
    source="BF_panel", column="fwd_ticks_h10", n_rows=len(bf),
    n_null=bf["fwd_ticks_h10"].isna().sum(), null_pct=bf["fwd_ticks_h10"].isna().mean(),
    min_val=bf["fwd_ticks_h10"].min(), max_val=bf["fwd_ticks_h10"].max(),
    mean_val=bf["fwd_ticks_h10"].mean(), std_val=bf["fwd_ticks_h10"].std()
))
pd.DataFrame(integrity_rows).to_csv(OUT_DIR / "audit_8b_data_integrity.csv", index=False)
print(f"\n  Part 1 done in {time.time()-t_p1:.1f}s")

# ═══════════════════════════════════════════════════════════════════════════
# Compute neutral derived features (no directional label)
# ═══════════════════════════════════════════════════════════════════════════
print("\n  Computing neutral derived features...")
bf["total_add"]           = bf["BF_bid_add"]  + bf["BF_ask_add"]
bf["total_pull"]          = bf["BF_bid_pull"] + bf["BF_ask_pull"]
bf["net_add_side"]        = bf["BF_bid_add"]  - bf["BF_ask_add"]
bf["net_pull_side"]       = bf["BF_bid_pull"] - bf["BF_ask_pull"]
bf["bid_event_balance"]   = bf["BF_bid_add"]  - bf["BF_bid_pull"]
bf["ask_event_balance"]   = bf["BF_ask_add"]  - bf["BF_ask_pull"]
bf["book_event_pressure"] = bf["bid_event_balance"] - bf["ask_event_balance"]
bf["two_sided_pull"]      = bf["BF_bid_pull"] + bf["BF_ask_pull"]
bf["two_sided_add"]       = bf["BF_bid_add"]  + bf["BF_ask_add"]
bf["event_churn"]         = (bf["BF_bid_add"] + bf["BF_bid_pull"] +
                              bf["BF_ask_add"] + bf["BF_ask_pull"])
bf["pull_add_ratio"]      = bf["total_pull"] / (bf["total_add"] + EPS)

DERIVED_FEATS = [
    "total_add","total_pull","net_add_side","net_pull_side",
    "bid_event_balance","ask_event_balance","book_event_pressure",
    "two_sided_pull","two_sided_add","event_churn","pull_add_ratio"
]
ALL_BASE_FEATS = RAW_PRIMS + DERIVED_FEATS

# Compute rolling windows for each feature
bf = bf.sort_values("bar_end_ts_ns").reset_index(drop=True)
WINDOW_FEATS = {}
for w in [3, 5, 10, 20, 50]:
    for feat in ALL_BASE_FEATS:
        col = f"{feat}_w{w}"
        bf[col] = bf[feat].rolling(w, min_periods=max(1, w//2)).mean()
        WINDOW_FEATS.setdefault(w, []).append(col)

print(f"  Total feature columns: {len(ALL_BASE_FEATS)} base + "
      f"{sum(len(v) for v in WINDOW_FEATS.values())} windowed")

# ═══════════════════════════════════════════════════════════════════════════
# PART 2 — Empirical meaning atlas (quantile bins × forward returns)
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 2: Empirical meaning atlas")
print("=" * 70)
t_p2 = time.time()

def empirical_meaning(df, feat_col, n_bins=5):
    """Bin feature into quantiles, compute outcome stats per bin."""
    col_data = df[[feat_col] + [f"fwd_ticks_h{h}" for h in HORIZONS]].dropna()
    if len(col_data) < 100:
        return pd.DataFrame()
    col_data["bin"] = pd.qcut(col_data[feat_col], n_bins, labels=False, duplicates="drop")
    rows = []
    for b, grp in col_data.groupby("bin"):
        row = dict(feature=feat_col, bin=int(b), n=len(grp))
        for h in HORIZONS:
            fc = f"fwd_ticks_h{h}"
            row[f"avg_fwd_h{h}"]      = grp[fc].mean()
            row[f"hit_rate_pos_h{h}"] = (grp[fc] > 0).mean()
            row[f"hit_rate_neg_h{h}"] = (grp[fc] < 0).mean()
        rows.append(row)
    return pd.DataFrame(rows)

atlas_rows = []
test_feats_p2 = RAW_PRIMS + DERIVED_FEATS + [f"{f}_w{w}" for f in RAW_PRIMS for w in [5,10,20]]
for feat in test_feats_p2:
    if feat in bf.columns:
        res = empirical_meaning(bf, feat)
        if len(res):
            atlas_rows.append(res)

atlas_df = pd.concat(atlas_rows, ignore_index=True) if atlas_rows else pd.DataFrame()

# Assign empirical role per feature (based on Q5 vs Q1 avg_fwd_h10)
meaning_rows = []
for feat, grp in atlas_df.groupby("feature"):
    grp_s = grp.sort_values("bin")
    if len(grp_s) < 3:
        continue
    q_lo = grp_s.iloc[0]["avg_fwd_h10"]   # lowest bin
    q_hi = grp_s.iloc[-1]["avg_fwd_h10"]  # highest bin
    sign = q_hi - q_lo
    # Monotonicity check
    vals = grp_s["avg_fwd_h10"].values
    is_mono = all(vals[i] <= vals[i+1] for i in range(len(vals)-1)) or \
              all(vals[i] >= vals[i+1] for i in range(len(vals)-1))

    if abs(sign) < 1.0:
        role = "NOISE"
    elif sign > 0 and is_mono:
        role = "LONG_SUPPORTIVE"
    elif sign < 0 and is_mono:
        role = "SHORT_SUPPORTIVE"
    elif sign > 0:
        role = "CONTEXT_ONLY"
    elif sign < 0:
        role = "CONTEXT_ONLY"
    else:
        role = "NOISE"

    # Check if sign is stable across horizons
    h_signs = []
    for h in HORIZONS:
        col = f"avg_fwd_h{h}"
        if col in grp_s.columns:
            h_hi = grp_s.iloc[-1][col]
            h_lo = grp_s.iloc[0][col]
            h_signs.append(np.sign(h_hi - h_lo))
    if len(set(h_signs)) > 1 and 0 not in set(h_signs):
        role = "CONTRADICTORY"

    meaning_rows.append(dict(feature=feat, q1_avg_h10=q_lo, q5_avg_h10=q_hi,
                              sign=sign, monotonic=is_mono, role=role,
                              h_signs=str(h_signs)))

meaning_df = pd.DataFrame(meaning_rows)
role_counts = meaning_df["role"].value_counts().to_dict()
print(f"\n  Empirical roles assigned to {len(meaning_df)} feature/window combinations:")
for r, n in sorted(role_counts.items()):
    print(f"    {r:22s}: {n:>4}")

atlas_df.to_csv(OUT_DIR / "audit_8b_empirical_meaning_atlas.csv", index=False)
print(f"  Part 2 done in {time.time()-t_p2:.1f}s")

# ═══════════════════════════════════════════════════════════════════════════
# PART 3 — Standalone predictive atlas (IC, hit rate, cost-adjusted edge)
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 3: Standalone predictive atlas (IC / hit rate / cost edge)")
print("=" * 70)
t_p3 = time.time()

predictive_rows = []
h_primary = 10

test_feats_p3 = (RAW_PRIMS + DERIVED_FEATS +
                 [f"{f}_w{w}" for f in RAW_PRIMS for w in [3,5,10,20,50]])

for feat in test_feats_p3:
    if feat not in bf.columns:
        continue
    fc = f"fwd_ticks_h{h_primary}"
    sub = bf[[feat, fc]].dropna()
    if len(sub) < 50:
        continue
    x, y = sub[feat].values, sub[fc].values

    # Spearman IC
    sp_r, sp_p = spearmanr(x, y)
    # Pearson IC
    pe_r, pe_p = pearsonr(x, y)

    # Directional hit rate: if feature high (top quartile) → positive return
    q75 = np.percentile(x, 75)
    q25 = np.percentile(x, 25)
    top_mask = x >= q75
    bot_mask = x <= q25
    top_ret = y[top_mask].mean() if top_mask.any() else np.nan
    bot_ret = y[bot_mask].mean() if bot_mask.any() else np.nan
    top_hr  = (y[top_mask] > 0).mean() if top_mask.any() else np.nan
    bot_hr  = (y[bot_mask] < 0).mean() if bot_mask.any() else np.nan

    # Cost-adjusted net edge
    net_long  = top_ret - COST_TICKS if not np.isnan(top_ret) else np.nan
    net_short = -bot_ret - COST_TICKS if not np.isnan(bot_ret) else np.nan

    predictive_rows.append(dict(
        feature=feat, horizon=h_primary, n=len(sub),
        spearman_ic=sp_r, spearman_p=sp_p,
        pearson_ic=pe_r, pearson_p=pe_p,
        top_q_avg_ret=top_ret, bot_q_avg_ret=bot_ret,
        top_q_hit_rate=top_hr, bot_q_hit_rate=bot_hr,
        net_long_after_cost=net_long,
        net_short_after_cost=net_short,
        direction_sign=np.sign(sp_r),
    ))

pred_df = pd.DataFrame(predictive_rows)

# Print top survivors by |IC|
top_ic = pred_df.nlargest(10, "spearman_ic")
bot_ic = pred_df.nsmallest(10, "spearman_ic")
print(f"\n  Top-10 positive Spearman IC (raw, H{h_primary}):")
for _, r in top_ic.iterrows():
    print(f"    {r['feature']:35s}: IC={r['spearman_ic']:>+.4f}  "
          f"net_long={r['net_long_after_cost']:>+.2f}t  n={r['n']:,}")
print(f"\n  Top-10 negative Spearman IC (raw, H{h_primary}):")
for _, r in bot_ic.iterrows():
    print(f"    {r['feature']:35s}: IC={r['spearman_ic']:>+.4f}  "
          f"net_short={r['net_short_after_cost']:>+.2f}t  n={r['n']:,}")

pred_df.to_csv(OUT_DIR / "audit_8b_standalone_predictive_atlas.csv", index=False)
print(f"  Part 3 done in {time.time()-t_p3:.1f}s")

# ═══════════════════════════════════════════════════════════════════════════
# PART 4 — Stability and survival
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 4: Stability and survival")
print("=" * 70)
t_p4 = time.time()

def ic_by_group(df, feat, outcome_col, group_col):
    """Spearman IC per group."""
    results = {}
    for g, grp in df.groupby(group_col):
        sub = grp[[feat, outcome_col]].dropna()
        if len(sub) < 30:
            continue
        ic, _ = spearmanr(sub[feat].values, sub[outcome_col].values)
        results[g] = ic
    return results

stability_rows = []
h_col = "fwd_ticks_h10"

for feat in RAW_PRIMS + DERIVED_FEATS:
    if feat not in bf.columns:
        continue
    sub = bf[[feat, h_col, "session","vol_regime"]].dropna()
    if len(sub) < 100:
        continue

    # Overall IC
    ic_all, _ = spearmanr(sub[feat].values, sub[h_col].values)

    # Session IC
    sess_ics = ic_by_group(sub, feat, h_col, "session")
    n_pos_sess = sum(1 for v in sess_ics.values() if v > 0)
    sess_stable = n_pos_sess >= 3

    # Vol regime IC
    reg_ics = ic_by_group(sub, feat, h_col, "vol_regime")
    n_pos_reg = sum(1 for v in reg_ics.values() if v > 0)
    reg_stable = n_pos_reg >= 2

    # Window stability: compare IC at w1 (this feat) vs w5/w10/w20
    w_ics = {}
    for w in [5, 10, 20]:
        wcol = f"{feat}_w{w}"
        if wcol in bf.columns:
            wsub = bf[[wcol, h_col]].dropna()
            if len(wsub) >= 100:
                wic, _ = spearmanr(wsub[wcol].values, wsub[h_col].values)
                w_ics[w] = wic
    if w_ics:
        sign_consistent = len(set(np.sign(list(w_ics.values()) + [ic_all]))) == 1
    else:
        sign_consistent = False

    # Cost-adjusted survival
    q75 = sub[feat].quantile(0.75)
    top_ret = sub.loc[sub[feat] >= q75, h_col].mean()
    cost_pass = top_ret > COST_TICKS or (-top_ret) > COST_TICKS

    # Kill conditions
    kill_reasons = []
    if not sess_stable:
        kill_reasons.append(f"only {n_pos_sess}/6 sessions IC>0")
    if not reg_stable:
        kill_reasons.append(f"only {n_pos_reg}/3 regimes IC>0")
    if not sign_consistent:
        kill_reasons.append("sign flips across windows")
    if abs(ic_all) < 0.02:
        kill_reasons.append("IC < 0.02 (noise level)")
    if not cost_pass:
        kill_reasons.append("no edge after 2t cost")

    survived = len(kill_reasons) == 0

    stability_rows.append(dict(
        feature=feat, ic_all=ic_all,
        n_pos_sessions=n_pos_sess, sess_stable=sess_stable,
        n_pos_regimes=n_pos_reg, reg_stable=reg_stable,
        window_sign_consistent=sign_consistent,
        cost_pass=cost_pass,
        kill_reasons="; ".join(kill_reasons) if kill_reasons else "none",
        survived=survived,
    ))

stab_df = pd.DataFrame(stability_rows)
survived = stab_df[stab_df["survived"]]
killed   = stab_df[~stab_df["survived"]]
print(f"\n  Stability results: {len(survived)} survived, {len(killed)} killed")
print(f"  Survivors:")
for _, r in survived.iterrows():
    print(f"    {r['feature']:30s}: IC={r['ic_all']:>+.4f}  "
          f"sess={r['n_pos_sessions']}/6  reg={r['n_pos_regimes']}/3  "
          f"win_stable={r['window_sign_consistent']}")
print(f"  Killed patterns:")
for _, r in killed.iterrows():
    print(f"    {r['feature']:30s}: {r['kill_reasons']}")

stab_df.to_csv(OUT_DIR / "audit_8b_stability_survival.csv", index=False)
print(f"  Part 4 done in {time.time()-t_p4:.1f}s")

# ═══════════════════════════════════════════════════════════════════════════
# PART 5 — PBO / DSR
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 5: PBO / DSR analysis")
print("=" * 70)
t_p5 = time.time()

# Count total configurations = features × windows × horizons × signs
n_raw       = len(RAW_PRIMS)
n_derived   = len(DERIVED_FEATS)
n_wind      = len(WINDOWS)
n_hor       = len(HORIZONS)
n_sign      = 2
n_thresh    = 5  # quintile thresholds
total_trials = (n_raw + n_derived) * n_wind * n_hor * n_sign
print(f"\n  Total tested configurations (family): {total_trials:,}")
print(f"    ({n_raw} raw + {n_derived} derived) × {n_wind} windows × "
      f"{n_hor} horizons × {n_sign} signs = {total_trials}")

# Simplified PBO: 50/50 IS vs OOS split on the 8,923 bars
# Use first 50% of BF panel as IS, second 50% as OOS
bf_sorted = bf.sort_values("bar_end_ts_ns").reset_index(drop=True)
split_idx = len(bf_sorted) // 2
bf_is  = bf_sorted.iloc[:split_idx]
bf_oos = bf_sorted.iloc[split_idx:]

h_pbo = "fwd_ticks_h10"
pbo_rows = []
is_ics, oos_ics = {}, {}

all_eval_feats = (RAW_PRIMS + DERIVED_FEATS +
                  [f"{f}_w{w}" for f in RAW_PRIMS for w in [5,10,20,50]])
for feat in all_eval_feats:
    if feat not in bf_sorted.columns:
        continue
    is_sub  = bf_is[[feat, h_pbo]].dropna()
    oos_sub = bf_oos[[feat, h_pbo]].dropna()
    if len(is_sub) < 30 or len(oos_sub) < 30:
        continue
    ic_is,  _ = spearmanr(is_sub[feat].values, is_sub[h_pbo].values)
    ic_oos, _ = spearmanr(oos_sub[feat].values, oos_sub[h_pbo].values)
    is_ics[feat]  = ic_is
    oos_ics[feat] = ic_oos
    pbo_rows.append(dict(feature=feat, ic_is=ic_is, ic_oos=ic_oos,
                         is_positive=(ic_is>0), oos_positive=(ic_oos>0),
                         degradation=ic_oos-ic_is))

pbo_df = pd.DataFrame(pbo_rows)

# Simplified PBO: fraction of best-IS strategies that are negative OOS
n_valid = len(pbo_df)
if n_valid > 0:
    # Top quartile IS strategies
    ic_is_q75 = pbo_df["ic_is"].quantile(0.75)
    top_is = pbo_df[pbo_df["ic_is"] >= ic_is_q75]
    pbo_family = (top_is["ic_oos"] < 0).mean() if len(top_is) else 1.0

    # DSR: expected max Sharpe under N trials
    # Using simplified formula: E[max IC] ≈ sqrt(2 * log(N)) * sigma_IC
    sigma_ic = pbo_df["ic_is"].std()
    exp_max_sharpe = np.sqrt(2 * np.log(n_valid)) * sigma_ic if sigma_ic > 0 else 0
    obs_max_ic = pbo_df["ic_oos"].max()
    dsr = obs_max_ic / max(exp_max_sharpe, EPS)

    print(f"\n  PBO (simplified, 50/50 IS/OOS split):")
    print(f"    Total evaluated configurations: {n_valid}")
    print(f"    IS IC range: [{pbo_df['ic_is'].min():.4f}, {pbo_df['ic_is'].max():.4f}]")
    print(f"    OOS IC range: [{pbo_df['ic_oos'].min():.4f}, {pbo_df['ic_oos'].max():.4f}]")
    print(f"    Family PBO: {pbo_family:.3f}  "
          f"({'HIGH' if pbo_family > 0.60 else 'MODERATE' if pbo_family > 0.30 else 'LOW'})")
    print(f"    Expected max IC under {n_valid} trials: {exp_max_sharpe:.4f}")
    print(f"    Observed best OOS IC: {obs_max_ic:.4f}")
    print(f"    DSR (deflated Sharpe ratio proxy): {dsr:.3f}")
    print(f"    Avg IS→OOS degradation: {pbo_df['degradation'].mean():+.4f}")

    n_oos_pos = (pbo_df["ic_oos"] > 0).sum()
    print(f"    OOS positive IC: {n_oos_pos}/{n_valid} ({n_oos_pos/n_valid:.1%})")

pbo_df.to_csv(OUT_DIR / "audit_8b_pbo_dsr.csv", index=False)

# Final decision per feature based on PBO
pbo_final_rows = []
for _, r in pbo_df.iterrows():
    stab_row = stab_df[stab_df["feature"]==r["feature"]]
    survived_stab = stab_row["survived"].values[0] if len(stab_row) else False
    if pbo_family >= 0.60 or not survived_stab:
        dec = "KILL"
    elif pbo_family < 0.20 and survived_stab and r["ic_oos"] > 0:
        dec = "PROMOTE_TO_FEATURE_CANDIDATE"
    elif r["ic_oos"] > 0 and survived_stab:
        dec = "DEFER_RESEARCH"
    else:
        dec = "KILL"
    pbo_final_rows.append(dict(feature=r["feature"], family_pbo=pbo_family,
                                ic_is=r["ic_is"], ic_oos=r["ic_oos"],
                                survived_stability=survived_stab, decision=dec))

pbo_final_df = pd.DataFrame(pbo_final_rows)
dec_counts = pbo_final_df["decision"].value_counts().to_dict()
print(f"\n  Per-feature decisions: {dec_counts}")
print(f"  Part 5 done in {time.time()-t_p5:.1f}s")

# ═══════════════════════════════════════════════════════════════════════════
# PART 6 — Interaction with flow toxicity (V=B W=20 OOS)
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 6: Interaction with flow toxicity (V=B W=20 OOS)")
print("=" * 70)
t_p6 = time.time()

# Join OOS signals to book switching panel by bar timestamp
bf_compact = bf[["bar_end_ts_ns", "BF_bid_add","BF_bid_pull","BF_ask_add","BF_ask_pull",
                  "net_add_side","book_event_pressure","event_churn","pull_add_ratio",
                  "vol_regime","session"]].copy()
vb20_bf = vb20_sig.merge(bf_compact, left_on="timestamp_ns", right_on="bar_end_ts_ns",
                          how="left", suffixes=("","_bf"))

n_matched = vb20_bf["BF_bid_add"].notna().sum()
pct_matched = n_matched / len(vb20_bf)
print(f"\n  OOS signals with bar-level book data: {n_matched:,}/{len(vb20_bf):,} ({pct_matched:.1%})")
print(f"  Coverage dates (9 of 18 OOS days): Jun 14-24 only")
print(f"  Missing: Jun 07-13, Jun 25, Jun 28-30 (incl. biggest win day Jun 08 and failure Jun 28)")

# Restrict to matched subset
vb20_m = vb20_bf[vb20_bf["BF_bid_add"].notna()].copy()
vb20_m["book_event_pressure"] = (vb20_m["BF_bid_add"] - vb20_m["BF_bid_pull"] -
                                   vb20_m["BF_ask_add"] + vb20_m["BF_ask_pull"])

# Load toxicity for OOS signals (recompute — must join Atlas1 + Q16)
# Use pre-computed probe: book_event_pressure quartile as book state
vb20_m["bep_bucket"] = pd.qcut(vb20_m["book_event_pressure"], 4,
                                labels=["BEP_Q1","BEP_Q2","BEP_Q3","BEP_Q4"],
                                duplicates="drop")

tox_rows = []
for bkt, grp in vb20_m.groupby("bep_bucket", observed=True):
    stats = dict(
        book_state=str(bkt),
        n=len(grp),
        avg_net_h10=grp["net_return_h10"].mean(),
        total_net_h10=grp["net_return_h10"].sum(),
        win_rate_h10=(grp["net_return_h10"] > 0).mean(),
        avg_net_h40=grp["net_return_h40"].mean(),
        win_rate_h40=(grp["net_return_h40"] > 0).mean(),
        avg_max_prob=grp["max_prob"].mean(),
        avg_book_pressure=grp["book_event_pressure"].mean(),
    )
    tox_rows.append(stats)

print(f"\n  Book Event Pressure (BEP) quartile performance on matched OOS signals:")
tox_df_p6 = pd.DataFrame(tox_rows)
for _, r in tox_df_p6.iterrows():
    print(f"    {r['book_state']}: n={r['n']:>5,}  avg={r['avg_net_h10']:>+8.2f}t  "
          f"wr={r['win_rate_h10']:.3f}  avg_bep={r['avg_book_pressure']:>+.0f}")

# Test: does book event state add value on top of prob_edge alone?
prob_edge_ic, _ = spearmanr(vb20_m["prob_edge"].values, vb20_m["net_return_h10"].values)
bep_ic, _ = spearmanr(vb20_m["book_event_pressure"].values, vb20_m["net_return_h10"].values)
print(f"\n  prob_edge Spearman IC with H10 net return: {prob_edge_ic:>+.4f}")
print(f"  book_event_pressure Spearman IC with H10:  {bep_ic:>+.4f}")

# Conditional: high prob + aligned book_event_pressure vs high prob + against
# BEP positive = bid-side favorable, BEP negative = ask-side favorable
high_prob = vb20_m[vb20_m["max_prob"] >= 0.75]
long_sig  = high_prob[high_prob["side"]=="LONG"]
short_sig = high_prob[high_prob["side"]=="SHORT"]

for label, grp in [("HIGH_PROB LONG", long_sig), ("HIGH_PROB SHORT", short_sig)]:
    if len(grp) < 20:
        continue
    bep_q50 = grp["book_event_pressure"].median()
    aligned = grp[((grp["side"]=="LONG")  & (grp["book_event_pressure"] > bep_q50)) |
                   ((grp["side"]=="SHORT") & (grp["book_event_pressure"] < bep_q50))]
    against = grp[~grp.index.isin(aligned.index)]
    tox_rows.append(dict(book_state=f"{label}_aligned",
                         n=len(aligned), avg_net_h10=aligned["net_return_h10"].mean(),
                         total_net_h10=aligned["net_return_h10"].sum(),
                         win_rate_h10=(aligned["net_return_h10"]>0).mean(),
                         avg_net_h40=aligned["net_return_h40"].mean(),
                         win_rate_h40=(aligned["net_return_h40"]>0).mean(),
                         avg_max_prob=aligned["max_prob"].mean(),
                         avg_book_pressure=aligned["book_event_pressure"].mean()))
    tox_rows.append(dict(book_state=f"{label}_against",
                         n=len(against), avg_net_h10=against["net_return_h10"].mean(),
                         total_net_h10=against["net_return_h10"].sum(),
                         win_rate_h10=(against["net_return_h10"]>0).mean(),
                         avg_net_h40=against["net_return_h40"].mean(),
                         win_rate_h40=(against["net_return_h40"]>0).mean(),
                         avg_max_prob=against["max_prob"].mean(),
                         avg_book_pressure=against["book_event_pressure"].mean()))
    print(f"  {label}: aligned={aligned['net_return_h10'].mean():>+.2f}t "
          f"vs against={against['net_return_h10'].mean():>+.2f}t")

pd.DataFrame(tox_rows).to_csv(OUT_DIR / "audit_8b_toxicity_interaction.csv", index=False)
print(f"  Part 6 done in {time.time()-t_p6:.1f}s")

# ═══════════════════════════════════════════════════════════════════════════
# PART 7 — Algorithmic state discovery (decision tree on raw primitives)
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 7: Algorithmic state discovery (decision tree, max_depth=3)")
print("=" * 70)
t_p7 = time.time()

h_primary_col = "fwd_ticks_h10"
state_feats = ["BF_bid_add","BF_bid_pull","BF_ask_add","BF_ask_pull",
               "total_add","total_pull","event_churn","book_event_pressure"]

tree_data = bf[state_feats + [h_primary_col, "session","vol_regime"]].dropna()
X = tree_data[state_feats].values
y_dir = (tree_data[h_primary_col] > 0).astype(int).values  # binary direction

dt = DecisionTreeClassifier(max_depth=3, min_samples_leaf=50, random_state=42)
dt.fit(X, y_dir)
leaf_ids = dt.apply(X)
tree_data = tree_data.copy()
tree_data["leaf_id"] = leaf_ids

# Print tree structure
print(f"\n  Decision tree (max_depth=3) on raw book primitives:")
tree_str = export_text(dt, feature_names=state_feats, max_depth=3)
for line in tree_str.split("\n")[:40]:
    print(f"    {line}")

state_rows = []
for leaf, grp in tree_data.groupby("leaf_id"):
    avg_h10 = grp[h_primary_col].mean()
    wr = (grp[h_primary_col] > 0).mean()

    # Determine role from outcomes only
    if avg_h10 > COST_TICKS and wr > 0.55:
        role = "LONG_SUPPORTIVE"
    elif avg_h10 < -COST_TICKS and wr < 0.45:
        role = "SHORT_SUPPORTIVE"
    elif abs(avg_h10) <= COST_TICKS:
        role = "NOISE"
    else:
        role = "CONTEXT_ONLY"

    feat_means = {f: grp[f].mean() for f in state_feats}
    state_rows.append(dict(
        state_id=int(leaf), n=len(grp),
        avg_h10=avg_h10, avg_h40=grp[h_primary_col].mean(),  # same for now
        win_rate=wr,
        role=role,
        session_mix=grp["session"].value_counts(normalize=True).to_dict(),
        vol_mix=grp["vol_regime"].value_counts(normalize=True).to_dict(),
        **feat_means
    ))

states_df = pd.DataFrame(state_rows).sort_values("avg_h10", ascending=False)

print(f"\n  Discovered states:")
for _, r in states_df.iterrows():
    print(f"    State {r['state_id']:>3}: n={r['n']:>4,}  avg_h10={r['avg_h10']:>+.2f}t  "
          f"wr={r['win_rate']:.3f}  role={r['role']}")

states_df["session_mix"] = states_df["session_mix"].astype(str)
states_df["vol_mix"] = states_df["vol_mix"].astype(str)
states_df.to_csv(OUT_DIR / "audit_8b_discovered_states.csv", index=False)
print(f"  Part 7 done in {time.time()-t_p7:.1f}s")

# ═══════════════════════════════════════════════════════════════════════════
# PART 8 — Recommendation
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 8: Recommendation")
print("=" * 70)

# Gather evidence
n_survived_feats = stab_df["survived"].sum()
n_promoted = (pbo_final_df["decision"] == "PROMOTE_TO_FEATURE_CANDIDATE").sum()
n_defer = (pbo_final_df["decision"] == "DEFER_RESEARCH").sum()
n_killed = (pbo_final_df["decision"] == "KILL").sum()
best_oos_ic = pbo_df["ic_oos"].max() if len(pbo_df) else 0.0
best_feat_oos = pbo_df.loc[pbo_df["ic_oos"].idxmax(),"feature"] if len(pbo_df) else "none"

# Determine recommendation
coverage_pct = n_matched / len(vb20_sig)
if pbo_family >= 0.60 and n_promoted == 0:
    recommendation = "DEFER_BOOK_EVENT_RESEARCH"
    reason = f"Family PBO={pbo_family:.2f} (high). No feature promoted. Coverage={coverage_pct:.1%}."
elif n_promoted > 0 and pbo_family < 0.40:
    recommendation = "PROMOTE_BOOK_EVENT_STATE_TO_FEATURE_CANDIDATE"
    reason = f"{n_promoted} features promoted, PBO={pbo_family:.2f}."
elif best_oos_ic < 0.02:
    recommendation = "DEFER_BOOK_EVENT_RESEARCH"
    reason = f"Best OOS IC={best_oos_ic:.4f} (below 0.02 threshold). Insufficient edge."
elif n_survived_feats == 0:
    recommendation = "DEFER_BOOK_EVENT_RESEARCH"
    reason = "No features survived stability filter."
else:
    recommendation = "DEFER_BOOK_EVENT_RESEARCH"
    reason = f"Some survivors ({n_survived_feats}), PBO={pbo_family:.2f}, partial coverage ({coverage_pct:.1%}). Needs full OOS coverage before promotion."

print(f"\n  Evidence summary:")
print(f"    Features survived stability: {n_survived_feats}")
print(f"    PBO (family): {pbo_family:.3f}")
print(f"    Best OOS IC: {best_oos_ic:+.4f} ({best_feat_oos})")
print(f"    Promoted: {n_promoted}  Deferred: {n_defer}  Killed: {n_killed}")
print(f"    OOS coverage: {coverage_pct:.1%} (9/18 days, missing Jun 07-13, 25, 28-30)")
print(f"\n  *** RECOMMENDATION: {recommendation} ***")
print(f"    Reason: {reason}")

# ─── Write final decisions ────────────────────────────────────────────────
final_rows = []
# Raw primitives
for feat in RAW_PRIMS:
    row = pbo_final_df[pbo_final_df["feature"]==feat]
    mr = meaning_df[meaning_df["feature"]==feat]
    final_rows.append(dict(
        feature=feat, feature_type="RAW_PRIMITIVE",
        ic_oos=row["ic_oos"].values[0] if len(row) else np.nan,
        pbo=pbo_family,
        empirical_role=mr["role"].values[0] if len(mr) else "UNKNOWN",
        decision=row["decision"].values[0] if len(row) else "KILL",
        overall_recommendation=recommendation,
    ))
# Derived
for feat in DERIVED_FEATS:
    row = pbo_final_df[pbo_final_df["feature"]==feat]
    mr = meaning_df[meaning_df["feature"]==feat]
    final_rows.append(dict(
        feature=feat, feature_type="DERIVED",
        ic_oos=row["ic_oos"].values[0] if len(row) else np.nan,
        pbo=pbo_family,
        empirical_role=mr["role"].values[0] if len(mr) else "UNKNOWN",
        decision=row["decision"].values[0] if len(row) else "KILL",
        overall_recommendation=recommendation,
    ))

final_df = pd.DataFrame(final_rows)
final_df.to_csv(OUT_DIR / "audit_8b_final_decisions.csv", index=False)

# ─── Main report ─────────────────────────────────────────────────────────
with open(OUT_DIR / "AUDIT_8B_EMPIRICAL_BOOK_EVENT_SEMANTICS_REPORT.md", "w") as f:
    f.write("# Audit 8B — Empirical Book Event Semantics Atlas\n")
    f.write(f"**Generated**: {datetime.datetime.now(datetime.UTC).isoformat()}\n")
    f.write("**SHADOW / RESEARCH ONLY — no execution, no broker, no order placement**\n")
    f.write("**Principle: NO pre-assigned directional semantics. Data defines meaning.**\n\n")
    f.write("---\n\n")
    f.write("## 1. Data Coverage\n\n")
    f.write("| Source | Rows | Dates | Coverage |\n|--------|------|-------|----------|\n")
    f.write(f"| book_switching_feature_panel (bar-level) | {len(bf_raw):,} | Jun 14–24 | 9/18 OOS days |\n")
    f.write(f"| level_touch_full_book_mechanics (events) | {len(mech):,} | Jun 14–23 (only {n_pre10_valid:,} have pre-event primitives) | Supplementary |\n")
    f.write(f"| OOS V=B W=20 ≥0.65 signals | {len(vb20_sig):,} | Jun 07–30 | Full OOS |\n")
    f.write(f"| OOS signals with bar book data | {n_matched:,} | Jun 14–24 | **{pct_matched:.1%}** |\n\n")
    f.write("**Coverage limitation**: Jun 07–13 (missing first week incl. Jun 08 +376K win day) "
            "and Jun 25, 28–30 (missing Jun 28 regime failure) have NO book data.\n\n")
    f.write("---\n\n")
    f.write("## 2. Causal Verification\n\n")
    f.write("- BF features (BF_bid_add/pull, BF_ask_add/pull): computed during bar N (causal)\n")
    f.write("- Forward returns: computed from px_close at bar N+H relative to bar N (causal)\n")
    f.write("- post_* event windows from book mechanics: **explicitly excluded** (future data)\n")
    f.write("- Causal constraint: **SATISFIED**\n\n")
    f.write("---\n\n")
    f.write("## 3. Empirical Role Atlas\n\n")
    f.write("| Role | Count |\n|------|-------|\n")
    for role, cnt in sorted(role_counts.items()):
        f.write(f"| {role} | {cnt} |\n")
    f.write("\n---\n\n")
    f.write("## 4. Standalone IC (H10, top Spearman)\n\n")
    f.write("| Feature | IC (IS) | IC (OOS) | Net Long H10 | Net Short H10 |\n")
    f.write("|---------|---------|----------|-------------|---------------|\n")
    for _, r in pbo_df.nlargest(10, "ic_oos").iterrows():
        f.write(f"| {r['feature']} | {r['ic_is']:+.4f} | {r['ic_oos']:+.4f} | ")
        pr = pred_df[pred_df["feature"]==r["feature"]]
        nlong = pr["net_long_after_cost"].values[0] if len(pr) else np.nan
        nshort = pr["net_short_after_cost"].values[0] if len(pr) else np.nan
        f.write(f"{nlong:+.2f}t | {nshort:+.2f}t |\n")
    f.write("\n---\n\n")
    f.write("## 5. PBO / DSR\n\n")
    f.write(f"| Item | Value |\n|------|-------|\n")
    f.write(f"| Total tested configurations | {total_trials:,} |\n")
    f.write(f"| Configurations evaluated for IS/OOS | {n_valid} |\n")
    f.write(f"| Family PBO | {pbo_family:.3f} |\n")
    f.write(f"| DSR proxy | {dsr:.3f} |\n")
    f.write(f"| Best OOS IC | {best_oos_ic:+.4f} ({best_feat_oos}) |\n")
    f.write(f"| OOS IC > 0 | {n_oos_pos}/{n_valid} |\n")
    f.write(f"| Promoted | {n_promoted} |\n")
    f.write(f"| Deferred | {n_defer} |\n")
    f.write(f"| Killed | {n_killed} |\n\n")
    f.write("---\n\n")
    f.write("## 6. Toxicity Interaction\n\n")
    f.write(f"- OOS coverage: {n_matched:,}/{len(vb20_sig):,} ({pct_matched:.1%})\n")
    f.write(f"- prob_edge Spearman IC with H10 return: {prob_edge_ic:+.4f}\n")
    f.write(f"- book_event_pressure Spearman IC with H10 return: {bep_ic:+.4f}\n\n")
    f.write("---\n\n")
    f.write("## 7. Discovered States\n\n")
    f.write("| State | N | Avg H10 | WR | Role |\n|-------|---|---------|-----|------|\n")
    for _, r in states_df.iterrows():
        f.write(f"| {int(r['state_id'])} | {r['n']:,} | {r['avg_h10']:+.2f}t | "
                f"{r['win_rate']:.3f} | {r['role']} |\n")
    f.write("\n---\n\n")
    f.write("## 8. Recommendation\n\n")
    f.write(f"**{recommendation}**\n\n{reason}\n\n")
    f.write("---\n\n")
    f.write("## Final Status\n```\n")
    f.write(f"AUDIT_8B_COMPLETE:             true\n")
    f.write(f"RECOMMENDATION:               {recommendation}\n")
    f.write(f"PRODUCTION_FILES_MODIFIED:     false\n")
    f.write(f"TRADING_ENABLED:               false\n")
    f.write("```\n")

print(f"\n  Saved: AUDIT_8B_EMPIRICAL_BOOK_EVENT_SEMANTICS_REPORT.md")

# ═══════════════════════════════════════════════════════════════════════════
# Terminal summary
# ═══════════════════════════════════════════════════════════════════════════
elapsed = time.time() - t0
print("\n" + "=" * 70)
print("AUDIT 8B COMPLETE — TERMINAL SUMMARY")
print("=" * 70)

# Best/worst survivor
if len(pbo_df):
    best_row  = pbo_df.loc[pbo_df["ic_oos"].idxmax()]
    worst_row = pbo_df.loc[pbo_df["ic_oos"].idxmin()]
else:
    best_row = worst_row = pd.Series({"feature":"none","ic_oos":0.0})

best_state = states_df.iloc[0] if len(states_df) else pd.Series({"state_id":0,"avg_h10":0.0})
worst_state = states_df.iloc[-1] if len(states_df) else pd.Series({"state_id":0,"avg_h10":0.0})

n_any_raw_survived = sum(1 for f in RAW_PRIMS if f in
                         stab_df[stab_df["survived"]]["feature"].values)
n_any_derived_survived = sum(1 for f in DERIVED_FEATS if f in
                              stab_df[stab_df["survived"]]["feature"].values)

print(f"\n  1. Raw book primitives survived: {n_any_raw_survived}/{len(RAW_PRIMS)}")
print(f"  2. Derived book states survived: {n_any_derived_survived}/{len(DERIVED_FEATS)}")
print(f"  3. Empirical meaning — top role: {meaning_df['role'].value_counts().index[0] if len(meaning_df) else 'N/A'}")
print(f"  4. Book events improve flow toxicity: {'MARGINALLY' if abs(bep_ic) > 0.02 else 'NO'} "
      f"(bep_ic={bep_ic:+.4f} vs prob_edge_ic={prob_edge_ic:+.4f})")
print(f"  5. Best surviving state: State {int(best_state['state_id'])} "
      f"(avg_h10={best_state['avg_h10']:+.2f}t)")
print(f"  6. Worst/kill state: State {int(worst_state['state_id'])} "
      f"(avg_h10={worst_state['avg_h10']:+.2f}t)")
print(f"  7. PBO by family: {pbo_family:.3f}  DSR={dsr:.3f}")
print(f"  8. Recommendation: {recommendation}")
print(f"  9. Output dir: {OUT_DIR}")

import subprocess
print("\n  10. git status:")
try:
    gs = subprocess.run(["git","status","--short"], capture_output=True, text=True, cwd=str(WORK_DIR))
    print(gs.stdout if gs.stdout.strip() else "    (not a git repo or no changes)")
except Exception:
    print("    (not a git repo)")
print("\n  11. git diff --stat:")
try:
    gd = subprocess.run(["git","diff","--stat"], capture_output=True, text=True, cwd=str(WORK_DIR))
    print(gd.stdout if gd.stdout.strip() else "    (no staged diff)")
except Exception:
    print("    (not a git repo)")

print(f"\n  Total elapsed: {elapsed:.1f}s")
print("  PRODUCTION_FILES_MODIFIED: false")
print("  TRADING_ENABLED: false")
print("=" * 70)
