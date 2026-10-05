#!/usr/bin/env python3
"""
Audit 8A — Flow Toxicity Side/Session Attribution
SHADOW / RESEARCH ONLY — no execution, no broker, no order placement
Candidate: Version B, W=20, threshold >= 0.65, Rule G H10 cooldown
"""
import time
import warnings
import datetime
import numpy as np
import pandas as pd
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)
t0 = time.time()

# ─── Paths ─────────────────────────────────────────────────────────────────
WORK_DIR   = Path(__file__).parent
OOS_PATH   = WORK_DIR / "oos_probability_table.parquet"
MODEL_DIR  = Path("/home/prabh/OFI_Production/model_registry"
                  "/level_reaction_continuous_nq_shadow"
                  "/level_reaction_continuous_nq_shadow_20260702T005104Z")
DATA_DIR   = MODEL_DIR / "data"
ATLAS1_PATH = Path("/home/prabh/OFI_Production/research_engines"
                   "/directional_vpin_toxic_flow_settings_atlas_v1_20260701T025021Z"
                   "/directional_vpin_feature_panel.parquet")
Q16_PATH    = Path("/home/prabh/OFI_Production/research_engines"
                   "/q16_true_vpin_feature_master_recommendation"
                   "_20260603_20260701_20260702T061819Z"
                   "/q16_true_vpin_feature_master_aligned.parquet")

# ─── Constants ──────────────────────────────────────────────────────────────
THRESHOLD   = 0.65
COST_TICKS  = 2.0
H10_CD_NS   = int(282 * 1e9)
SESSIONS    = ["Asia", "EU", "US_Open", "US_AM", "US_PM", "US_Late"]
NY_SESSIONS = ["US_Open", "US_AM", "US_PM", "US_Late"]
TOX_COLS    = ["buy_toxicity", "sell_toxicity", "signed_vpin_delta", "cur_vpin_pct_L500_signed"]
TOTAL_OOS_DAYS = 18

ts_str = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
OUT_DIR = WORK_DIR / "audits" / f"audit_8a_flow_toxicity_side_session_{ts_str}"
OUT_DIR.mkdir(parents=True, exist_ok=True)

print("=" * 70)
print("Audit 8A — Flow Toxicity Side/Session Attribution")
print("SHADOW / RESEARCH ONLY — no execution, no broker")
print(f"Output: {OUT_DIR}")
print("=" * 70)

# ═══════════════════════════════════════════════════════════════════════════
# PART 0: Load and join data
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 0: Load and join data")
print("=" * 70)
t_p0 = time.time()

oos = pd.read_parquet(OOS_PATH)
print(f"  OOS table: {len(oos):,} rows × {len(oos.columns)} cols")
oos["max_prob"]     = oos[["prob_long", "prob_short"]].max(axis=1)
oos["prob_edge_abs"] = (oos["prob_long"] - oos["prob_short"]).abs()

# V=B candidate signals
vb_all = oos[(oos["version"] == "B") & (oos["window"] == 20) & (oos["is_oos"])].copy()
vb = vb_all[vb_all["max_prob"] >= THRESHOLD].copy()
print(f"  V=B W=20 all OOS: {len(vb_all):,}  | at threshold >= {THRESHOLD}: {len(vb):,}")

# V=A on same event_ids (causal comparison only — no threshold applied to A)
va_eids = set(vb["event_id"])
va = oos[(oos["version"] == "A") & (oos["window"] == 20) & (oos["is_oos"]) &
         (oos["event_id"].isin(va_eids))].copy()
va["max_prob"]      = va[["prob_long", "prob_short"]].max(axis=1)
va["prob_edge_abs"] = (va["prob_long"] - va["prob_short"]).abs()
va["side"]          = va["pred_side"]
print(f"  V=A W=20 same {len(va_eids):,} events: {len(va):,} rows")

# ─── Deferred toxicity panel (with INV7 dedup fix) ──────────────────────────
print("\n  Building deferred toxicity panel (INV7 dedup fix applied)...")

atlas1 = pd.read_parquet(ATLAS1_PATH,
    columns=["bar_end_ts_ns", "buy_toxicity", "sell_toxicity", "signed_vpin_delta"]
).rename(columns={"bar_end_ts_ns": "ts_ns"})
dup_a = atlas1["ts_ns"].duplicated().sum()
atlas1 = atlas1.drop_duplicates("ts_ns")
print(f"  Atlas1: {len(atlas1)+dup_a:,} → {len(atlas1):,} rows ({dup_a} dups removed)")

q16 = pd.read_parquet(Q16_PATH, columns=["bar_end_ts_ns", "cur_vpin_pct_L500"]
).rename(columns={"bar_end_ts_ns": "ts_ns"})
dup_q = q16["ts_ns"].duplicated().sum()
q16 = q16.drop_duplicates("ts_ns")
print(f"  Q16: {len(q16)+dup_q:,} → {len(q16):,} rows ({dup_q} dups removed)")

# Compute cur_vpin_pct_L500_signed = cur_vpin_pct_L500 × sign(delta_norm from snapshot)
snap = pd.read_parquet(DATA_DIR / "continuous_master_snapshot.parquet",
                       columns=["bar_end_ts_ns", "delta_norm", "volatility_5"])
snap_delta = snap[["bar_end_ts_ns", "delta_norm"]].rename(columns={"bar_end_ts_ns": "ts_ns"})
q16 = q16.merge(snap_delta, on="ts_ns", how="left")
q16["cur_vpin_pct_L500_signed"] = q16["cur_vpin_pct_L500"] * np.sign(q16["delta_norm"])
q16 = q16.drop(columns=["cur_vpin_pct_L500", "delta_norm"])

deferred = atlas1.merge(q16, on="ts_ns", how="outer").rename(
    columns={"ts_ns": "event_time_ns"})
deferred["event_time_ns"] = deferred["event_time_ns"].astype("int64")
dup_def = deferred["event_time_ns"].duplicated().sum()
if dup_def > 0:
    deferred = deferred.drop_duplicates("event_time_ns")
print(f"  Deferred panel: {len(deferred):,} rows | dups after outer-merge: {dup_def}")

# Verify: no duplicate (event_id, version, window) in joined result
vb = vb.merge(deferred[["event_time_ns"] + TOX_COLS],
              left_on="timestamp_ns", right_on="event_time_ns",
              how="left").drop(columns=["event_time_ns"], errors="ignore")
dup_evw = vb.duplicated(["event_id", "version", "window"]).sum()
print(f"  Duplicate (event_id, version, window) after join: {dup_evw}  "
      f"{'[OK]' if dup_evw == 0 else '[WARN]'}")

n_null_any = vb[TOX_COLS].isna().any(axis=1).sum()
print(f"  Signals with all 4 toxicity cols: {len(vb)-n_null_any:,}/{len(vb):,} "
      f"({(len(vb)-n_null_any)/len(vb):.1%})")

# Vol regime from snapshot volatility_5 (OOS-only quartiles on matched bars)
snap_vol = snap[["bar_end_ts_ns", "volatility_5"]].rename(
    columns={"bar_end_ts_ns": "ts_snap"})
vb = vb.merge(snap_vol, left_on="timestamp_ns", right_on="ts_snap",
              how="left").drop(columns=["ts_snap"], errors="ignore")
q33 = vb["volatility_5"].quantile(0.33)
q67 = vb["volatility_5"].quantile(0.67)
vb["vol_regime"] = pd.cut(
    vb["volatility_5"],
    bins=[-np.inf, q33, q67, np.inf],
    labels=["LOW_VOL", "MED_VOL", "HIGH_VOL"]
)

# Define LONG / SHORT side
vb["side"] = vb["pred_side"]

# ─── Toxicity support score (OOS-only descriptive z-scores) ────────────────
print("\n  Computing toxicity support scores (OOS-only z-scores)...")

def _zscore_oos(col):
    filled = col.fillna(col.median())
    mu, sd = filled.mean(), filled.std(ddof=1)
    return (filled - mu) / sd if sd > 0 else pd.Series(0.0, index=col.index)

for tc in TOX_COLS:
    vb[f"z_{tc}"] = _zscore_oos(vb[tc])

lm = vb["side"] == "LONG"
sm = vb["side"] == "SHORT"
vb["tox_support"] = np.nan
vb.loc[lm, "tox_support"] = (
      vb.loc[lm, "z_buy_toxicity"]
    - vb.loc[lm, "z_sell_toxicity"]
    + vb.loc[lm, "z_signed_vpin_delta"]
    + vb.loc[lm, "z_cur_vpin_pct_L500_signed"]
)
vb.loc[sm, "tox_support"] = (
      vb.loc[sm, "z_sell_toxicity"]
    - vb.loc[sm, "z_buy_toxicity"]
    - vb.loc[sm, "z_signed_vpin_delta"]
    - vb.loc[sm, "z_cur_vpin_pct_L500_signed"]
)

q25 = vb["tox_support"].quantile(0.25)
q75 = vb["tox_support"].quantile(0.75)
vb["tox_bucket"] = pd.cut(
    vb["tox_support"],
    bins=[-np.inf, q25, q75, np.inf],
    labels=["TOX_AGAINST", "TOX_WEAK_SUPPORT", "TOX_STRONG_SUPPORT"]
)
tox_dist = vb["tox_bucket"].value_counts().sort_index().to_dict()
print(f"  Tox quartile bounds: q25={q25:.3f}, q75={q75:.3f}")
print(f"  Tox bucket dist: {tox_dist}")

# ─── Rule G: H10 cooldown 282s per direction ────────────────────────────────
def apply_rule_g(df, cooldown_ns=H10_CD_NS):
    arr_ts   = df["timestamp_ns"].values
    arr_side = df["side"].values
    keep = []
    last_ts = {"LONG": -10**18, "SHORT": -10**18}
    for i in range(len(arr_ts)):
        s, ts = arr_side[i], arr_ts[i]
        if ts - last_ts[s] >= cooldown_ns:
            keep.append(i)
            last_ts[s] = ts
    return df.iloc[keep].copy()

print("\n  Applying Rule G (H10 cooldown 282s per direction, globally)...")
vb_sorted = vb.sort_values("timestamp_ns").reset_index(drop=True)
rg = apply_rule_g(vb_sorted)
print(f"  Rule G trades: {len(rg):,}  "
      f"(LONG={( rg['side']=='LONG').sum()}, SHORT={(rg['side']=='SHORT').sum()})")
print(f"  Part 0 done in {time.time()-t_p0:.1f}s")

# ═══════════════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════════════
def _stats(grp):
    n = len(grp)
    if n == 0:
        return {}
    net10 = grp["net_return_h10"].sum()
    wr10  = (grp["net_return_h10"] > 0).mean()
    net40 = grp["net_return_h40"].sum()
    wr40  = (grp["net_return_h40"] > 0).mean()
    day_nets = grp.groupby("rithmic_date_str")["net_return_h10"].sum()
    pos_d    = (day_nets > 0).sum()
    best_d   = day_nets.max() / max(abs(net10), 1)
    ln = (grp["side"] == "LONG").sum()
    sn = (grp["side"] == "SHORT").sum()
    lg = grp[grp["side"] == "LONG"]
    sg = grp[grp["side"] == "SHORT"]
    return dict(
        n=n, total_net_h10=net10, avg_net_h10=grp["net_return_h10"].mean(),
        win_rate_h10=wr10, total_net_h40=net40, avg_net_h40=grp["net_return_h40"].mean(),
        win_rate_h40=wr40, avg_prob=grp["max_prob"].mean(),
        avg_prob_edge=grp["prob_edge_abs"].mean(),
        positive_days=int(pos_d), positive_day_pct=pos_d/len(day_nets),
        best_day_pct_of_total=best_d,
        long_n=int(ln), short_n=int(sn),
        long_net_h10=lg["net_return_h10"].sum() if ln else 0.0,
        short_net_h10=sg["net_return_h10"].sum() if sn else 0.0,
        long_win_rate=(lg["net_return_h10"] > 0).mean() if ln else np.nan,
        short_win_rate=(sg["net_return_h10"] > 0).mean() if sn else np.nan,
        tox_support_mean=grp["tox_support"].mean(),
        tox_support_hit_rate=(grp["tox_support"] > 0).mean(),
    )

def _side_decision(r):
    if r["total_net_h10"] <= 0 or r["win_rate_h10"] < 0.50:
        return "SIDE_KILL"
    if r["positive_day_pct"] >= 0.60 and r["best_day_pct_of_total"] < 0.50:
        return "SIDE_PASS"
    return "SIDE_DEFER"

def _sess_decision(r):
    if r["total_net_h10"] <= 0 or r["win_rate_h10"] < 0.50:
        return "SESSION_KILL"
    if r["positive_day_pct"] >= 0.60:
        return "SESSION_PASS"
    return "SESSION_DEFER"

# ═══════════════════════════════════════════════════════════════════════════
# PART 1 — Version B side survival
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 1: Side survival (raw signals + Rule G)")
print("=" * 70)
t_p1 = time.time()

def side_survival(df, label="raw"):
    rows = []
    for side, grp in df.groupby("side"):
        s = _stats(grp)
        s["side"] = side
        s["dataset"] = label
        s["decision"] = _side_decision(s)
        rows.append(s)
    return pd.DataFrame(rows)

ss_raw = side_survival(vb, "raw")
ss_rg  = side_survival(rg, "rule_g")

for ds, df_s in [("RAW", ss_raw), ("Rule G", ss_rg)]:
    print(f"\n  [{ds}] Side survival:")
    for _, r in df_s.iterrows():
        print(f"    {r['side']:6s}: n={r['n']:>6,}  avg={r['avg_net_h10']:>8.2f}t  "
              f"wr={r['win_rate_h10']:.3f}  pos_days={r['positive_day_pct']:.1%}  "
              f"→ {r['decision']}")

ss_raw.to_csv(OUT_DIR / "audit_8a_side_survival_raw.csv", index=False)
ss_rg.to_csv(OUT_DIR / "audit_8a_side_survival_ruleG.csv", index=False)
print(f"  Part 1 done in {time.time()-t_p1:.1f}s")

# ═══════════════════════════════════════════════════════════════════════════
# PART 2 — Session survival
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 2: Session survival (raw + Rule G)")
print("=" * 70)
t_p2 = time.time()

def session_survival(df, label="raw"):
    rows = []
    for sess_val, grp in df.groupby("session", observed=True):
        s = _stats(grp)
        s["session"] = sess_val
        s["dataset"] = label
        best_side = "LONG" if s["long_net_h10"] >= s["short_net_h10"] else "SHORT"
        s["best_side"] = best_side
        s["decision"] = _sess_decision(s)
        rows.append(s)

    # NY aggregate
    ny = df[df["session"].isin(NY_SESSIONS)]
    if len(ny) > 0:
        s = _stats(ny)
        s["session"] = "NY"
        s["dataset"] = label
        s["best_side"] = "LONG" if s["long_net_h10"] >= s["short_net_h10"] else "SHORT"
        s["decision"] = _sess_decision(s)
        rows.append(s)

    return pd.DataFrame(rows)

sr_raw = session_survival(vb, "raw")
sr_rg  = session_survival(rg, "rule_g")

print(f"\n  [RAW] Session survival:")
for _, r in sr_raw.iterrows():
    print(f"    {r['session']:10s}: n={r['n']:>6,}  avg={r['avg_net_h10']:>8.2f}t  "
          f"wr={r['win_rate_h10']:.3f}  best={r['best_side']:5s}  → {r['decision']}")
print(f"\n  [Rule G] Session survival:")
for _, r in sr_rg.iterrows():
    print(f"    {r['session']:10s}: n={r['n']:>5,}  avg={r['avg_net_h10']:>8.2f}t  "
          f"wr={r['win_rate_h10']:.3f}  best={r['best_side']:5s}  → {r['decision']}")

sr_raw.to_csv(OUT_DIR / "audit_8a_session_survival_raw.csv", index=False)
sr_rg.to_csv(OUT_DIR / "audit_8a_session_survival_ruleG.csv", index=False)
print(f"  Part 2 done in {time.time()-t_p2:.1f}s")

# ═══════════════════════════════════════════════════════════════════════════
# PART 3 — Side × session matrix
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 3: Side × session matrix (Rule G)")
print("=" * 70)
t_p3 = time.time()

matrix_rows = []
for sess in SESSIONS + ["NY"]:
    grp_all = rg[rg["session"].isin(NY_SESSIONS)] if sess == "NY" else rg[rg["session"] == sess]
    for side in ["LONG", "SHORT"]:
        grp = grp_all[grp_all["side"] == side]
        if len(grp) == 0:
            continue
        s = _stats(grp)
        cell_n = s["n"]
        cell_net = s["total_net_h10"]
        cell_avg = s["avg_net_h10"]
        cell_wr  = s["win_rate_h10"]
        cell_avg40 = s["avg_net_h40"]
        cell_tox_mean = s["tox_support_mean"]
        cell_tox_hit  = s["tox_support_hit_rate"]

        if cell_net <= 0 or cell_wr < 0.48:
            dec = "BLOCK"
        elif cell_wr >= 0.55 and s["positive_day_pct"] >= 0.55:
            dec = "PASS"
        elif cell_net > 0 and cell_wr >= 0.50:
            dec = "DEFER"
        else:
            dec = "BLOCK"

        matrix_rows.append(dict(
            session=sess, side=side,
            n=cell_n, total_net_h10=cell_net, avg_net_h10=cell_avg,
            win_rate_h10=cell_wr, avg_net_h40=cell_avg40,
            positive_day_pct=s["positive_day_pct"],
            tox_support_mean=cell_tox_mean,
            tox_support_hit_rate=cell_tox_hit,
            decision=dec,
        ))

mat = pd.DataFrame(matrix_rows)

print(f"\n  Side × Session matrix (Rule G):")
print(f"  {'Session':10s} {'Side':6s} {'N':>5} {'AvgH10':>8} {'WR':>6} {'ToxHit':>7} Decision")
print(f"  {'-'*60}")
for _, r in mat.iterrows():
    print(f"  {r['session']:10s} {r['side']:6s} {r['n']:>5} {r['avg_net_h10']:>8.2f} "
          f"{r['win_rate_h10']:>6.3f} {r['tox_support_hit_rate']:>7.3f} {r['decision']}")

mat.to_csv(OUT_DIR / "audit_8a_side_session_matrix_ruleG.csv", index=False)
print(f"  Part 3 done in {time.time()-t_p3:.1f}s")

# ═══════════════════════════════════════════════════════════════════════════
# PART 4 — Flow toxicity support attribution (by side × session)
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 4: Flow toxicity support attribution (raw signals)")
print("=" * 70)
t_p4 = time.time()

tox_rows = []
for sess in SESSIONS + ["ALL"]:
    grp_sess = vb if sess == "ALL" else vb[vb["session"] == sess]
    for side in ["LONG", "SHORT", "ALL"]:
        grp = grp_sess if side == "ALL" else grp_sess[grp_sess["side"] == side]
        if len(grp) < 20:
            continue
        for bkt in ["TOX_AGAINST", "TOX_WEAK_SUPPORT", "TOX_STRONG_SUPPORT"]:
            bg = grp[grp["tox_bucket"] == bkt]
            if len(bg) < 5:
                continue
            tox_rows.append(dict(
                session=sess, side=side, tox_bucket=bkt,
                n=len(bg),
                avg_net_h10=bg["net_return_h10"].mean(),
                total_net_h10=bg["net_return_h10"].sum(),
                win_rate_h10=(bg["net_return_h10"] > 0).mean(),
                avg_net_h40=bg["net_return_h40"].mean(),
                avg_prob=bg["max_prob"].mean(),
                avg_prob_edge=bg["prob_edge_abs"].mean(),
                tox_support_mean=bg["tox_support"].mean(),
            ))

tox_df = pd.DataFrame(tox_rows)

# Print top-level (ALL sessions, ALL sides) tox attribution
print(f"\n  Toxicity attribution — ALL sessions, ALL sides:")
top = tox_df[(tox_df["session"] == "ALL") & (tox_df["side"] == "ALL")]
for _, r in top.iterrows():
    print(f"    {r['tox_bucket']:22s}: n={r['n']:>6,}  avg={r['avg_net_h10']:>8.2f}t  "
          f"wr={r['win_rate_h10']:.3f}  avg_prob={r['avg_prob']:.3f}")

# Decision: does TOX_STRONG beat TOX_AGAINST?
for sess in ["ALL", "US_Open", "US_AM", "US_PM"]:
    for side in ["LONG", "SHORT"]:
        sub = tox_df[(tox_df["session"] == sess) & (tox_df["side"] == side)]
        if sub.empty:
            continue
        strong = sub[sub["tox_bucket"] == "TOX_STRONG_SUPPORT"]["avg_net_h10"].values
        against = sub[sub["tox_bucket"] == "TOX_AGAINST"]["avg_net_h10"].values
        if len(strong) and len(against):
            lift = strong[0] - against[0]
            dec = "TOX_SUPPORT_PASS" if strong[0] > against[0] else "TOX_SUPPORT_FAIL"
            if abs(lift) < 2.0:
                dec = "TOX_SUPPORT_DEFER"
            tox_rows_dec = tox_df[(tox_df["session"] == sess) & (tox_df["side"] == side)].copy()
            tox_df.loc[tox_rows_dec.index, "tox_decision"] = dec

tox_df.to_csv(OUT_DIR / "audit_8a_toxicity_support_by_side_session.csv", index=False)
print(f"  Part 4 done in {time.time()-t_p4:.1f}s")

# ═══════════════════════════════════════════════════════════════════════════
# PART 5 — Version A vs Version B incremental attribution
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 5: Version A vs B incremental attribution")
print("=" * 70)
t_p5 = time.time()

# Merge A and B on event_id to compute deltas
vb_slim = vb[["event_id", "rithmic_date_str", "session", "side",
              "prob_long", "prob_short", "max_prob", "prob_edge_abs",
              "net_return_h10", "net_return_h40", "win_rate_h10" if False else "net_return_h10",
              "tox_support", "tox_bucket"]].copy()
vb_slim.columns = [c if c not in ["prob_long","prob_short","max_prob","prob_edge_abs",
                                   "net_return_h10","net_return_h40","side"]
                   else f"B_{c}" for c in vb_slim.columns]
# rebuild properly
vb_comp = vb[["event_id", "rithmic_date_str", "session",
              "prob_long", "prob_short", "max_prob", "prob_edge_abs",
              "net_return_h10", "net_return_h40", "side", "tox_support", "tox_bucket"]].rename(
    columns={c: f"B_{c}" for c in ["prob_long","prob_short","max_prob","prob_edge_abs",
                                    "net_return_h10","net_return_h40","side"]})

va_comp = va[["event_id", "prob_long", "prob_short", "max_prob", "prob_edge_abs",
              "net_return_h10", "net_return_h40", "side"]].rename(
    columns={c: f"A_{c}" for c in ["prob_long","prob_short","max_prob","prob_edge_abs",
                                    "net_return_h10","net_return_h40","side"]})

comp = vb_comp.merge(va_comp, on="event_id", how="inner")
comp["delta_prob_long"]  = comp["B_prob_long"]  - comp["A_prob_long"]
comp["delta_prob_short"] = comp["B_prob_short"] - comp["A_prob_short"]
comp["delta_prob_edge"]  = comp["B_prob_edge_abs"] - comp["A_prob_edge_abs"]
comp["delta_net_h10"]    = comp["B_net_return_h10"] - comp["A_net_return_h10"]
comp["side_changed"]     = comp["B_side"] != comp["A_side"]
comp["confidence_up"]    = comp["B_max_prob"] > comp["A_max_prob"]

print(f"\n  Comparison set: {len(comp):,} events (V=A and V=B both present)")
print(f"  Side changed: {comp['side_changed'].sum():,} ({comp['side_changed'].mean():.1%})")
print(f"  Confidence up (B > A): {comp['confidence_up'].sum():,} ({comp['confidence_up'].mean():.1%})")

def ab_group_stats(grp_df, grp_name):
    return dict(
        group=grp_name,
        n=len(grp_df),
        A_avg_net_h10=grp_df["A_net_return_h10"].mean(),
        B_avg_net_h10=grp_df["B_net_return_h10"].mean(),
        B_minus_A=grp_df["B_net_return_h10"].mean() - grp_df["A_net_return_h10"].mean(),
        A_win_rate=(grp_df["A_net_return_h10"] > 0).mean(),
        B_win_rate=(grp_df["B_net_return_h10"] > 0).mean(),
        delta_win_rate=((grp_df["B_net_return_h10"]>0).mean()
                        - (grp_df["A_net_return_h10"]>0).mean()),
        n_confidence_up=grp_df["confidence_up"].sum(),
        conf_up_avg_net_B=grp_df.loc[grp_df["confidence_up"], "B_net_return_h10"].mean(),
        conf_dn_avg_net_B=grp_df.loc[~grp_df["confidence_up"], "B_net_return_h10"].mean(),
        side_changed_n=grp_df["side_changed"].sum(),
        side_changed_avg_net_B=grp_df.loc[grp_df["side_changed"], "B_net_return_h10"].mean()
            if grp_df["side_changed"].any() else np.nan,
        delta_prob_edge_mean=grp_df["delta_prob_edge"].mean(),
    )

ab_rows = []
# Overall
ab_rows.append(ab_group_stats(comp, "OVERALL"))
# By side (B side)
for s, g in comp.groupby("B_side"):
    ab_rows.append(ab_group_stats(g, f"side_{s}"))
# By session
for s, g in comp.groupby("session"):
    ab_rows.append(ab_group_stats(g, f"sess_{s}"))
# By tox bucket
for tb, g in comp.groupby("tox_bucket"):
    if pd.notna(tb):
        ab_rows.append(ab_group_stats(g, f"tox_{tb}"))

ab_df = pd.DataFrame(ab_rows)

print(f"\n  A vs B incremental — key groups:")
for _, r in ab_df[ab_df["group"].isin(["OVERALL","side_LONG","side_SHORT"])].iterrows():
    print(f"    {r['group']:18s}: A={r['A_avg_net_h10']:>8.2f}t  B={r['B_avg_net_h10']:>8.2f}t  "
          f"Δ={r['B_minus_A']:>+8.2f}t  ΔWR={r['delta_win_rate']:>+.3f}  "
          f"conf_up={r['n_confidence_up']:>5,}")

ab_df.to_csv(OUT_DIR / "audit_8a_versionA_vs_B_incremental.csv", index=False)
print(f"  Part 5 done in {time.time()-t_p5:.1f}s")

# ═══════════════════════════════════════════════════════════════════════════
# PART 6 — Best and worst Rule G trades
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 6: Best and worst Rule G trades (top/bottom 20)")
print("=" * 70)
t_p6 = time.time()

trade_cols = ["timestamp_ns", "rithmic_date_str", "session", "side",
              "prob_long", "prob_short", "max_prob", "prob_edge_abs",
              "buy_toxicity", "sell_toxicity", "signed_vpin_delta",
              "cur_vpin_pct_L500_signed", "tox_support",
              "fwd_return_ticks_h10", "net_return_h10",
              "fwd_return_ticks_h40", "net_return_h40",
              "vol_regime"]
trade_cols_present = [c for c in trade_cols if c in rg.columns]

best20 = rg.nlargest(20, "net_return_h10")[trade_cols_present].copy()
worst20 = rg.nsmallest(20, "net_return_h10")[trade_cols_present].copy()
best20["rank_label"] = "BEST"
worst20["rank_label"] = "WORST"

print("\n  Top-5 best Rule G trades:")
for _, r in best20.head(5).iterrows():
    print(f"    {r['rithmic_date_str']} {r['session']:8s} {r['side']:5s} "
          f"max_prob={r['max_prob']:.3f}  tox={r['tox_support']:.3f}  "
          f"net_h10={r['net_return_h10']:>8.1f}t")
print("\n  Top-5 worst Rule G trades:")
for _, r in worst20.head(5).iterrows():
    print(f"    {r['rithmic_date_str']} {r['session']:8s} {r['side']:5s} "
          f"max_prob={r['max_prob']:.3f}  tox={r['tox_support']:.3f}  "
          f"net_h10={r['net_return_h10']:>8.1f}t")

# Toxicity profile summary
for label, df_sw in [("BEST 20", best20), ("WORST 20", worst20)]:
    tox_avg = df_sw[["buy_toxicity","sell_toxicity","signed_vpin_delta",
                      "cur_vpin_pct_L500_signed","tox_support"]].mean()
    print(f"\n  {label} toxicity profile:")
    for c, v in tox_avg.items():
        print(f"    {c:30s}: {v:>+.4f}")
    if "tox_bucket" in df_sw.columns:
        print(f"    tox_bucket dist: {df_sw['tox_bucket'].value_counts().to_dict()}")

best20.to_csv(OUT_DIR / "audit_8a_top_best_ruleG_trades.csv", index=False)
worst20.to_csv(OUT_DIR / "audit_8a_top_worst_ruleG_trades.csv", index=False)
print(f"\n  Part 6 done in {time.time()-t_p6:.1f}s")

# ═══════════════════════════════════════════════════════════════════════════
# PART 7 — Daily side/session attribution + key day diagnosis
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 7: Daily side/session attribution")
print("=" * 70)
t_p7 = time.time()

# Per-day raw-signal attribution
day_rows = []
for date, grp in vb.groupby("rithmic_date_str"):
    long_g  = grp[grp["side"] == "LONG"]
    short_g = grp[grp["side"] == "SHORT"]
    sess_nets = grp.groupby("session")["net_return_h10"].sum()
    best_sess = sess_nets.idxmax() if len(sess_nets) else ""
    worst_sess = sess_nets.idxmin() if len(sess_nets) else ""
    day_tox_mean = grp["tox_support"].mean()
    against_n = (grp["tox_bucket"] == "TOX_AGAINST").sum()

    long_net  = long_g["net_return_h10"].sum()
    short_net = short_g["net_return_h10"].sum()
    total_net = grp["net_return_h10"].sum()

    # Best side/session combo
    ss_nets = grp.groupby(["session","side"])["net_return_h10"].sum()
    best_ss = str(ss_nets.idxmax()) if len(ss_nets) else ""
    best_ss_pct = ss_nets.max() / max(abs(total_net), 1) if len(ss_nets) else np.nan

    day_rows.append(dict(
        rithmic_date_str=date,
        n_signals=len(grp),
        long_n=len(long_g), short_n=len(short_g),
        long_net_h10=long_net, short_net_h10=short_net,
        total_net_h10=total_net,
        win_rate_h10=(grp["net_return_h10"] > 0).mean(),
        best_session=best_sess, worst_session=worst_sess,
        tox_support_mean=day_tox_mean,
        tox_against_n=int(against_n),
        pct_tox_against=against_n/len(grp),
        best_side_session_combo=best_ss,
        pct_pnl_from_best_side_session=best_ss_pct,
    ))

daily_df = pd.DataFrame(day_rows).sort_values("rithmic_date_str")
daily_df.to_csv(OUT_DIR / "audit_8a_daily_side_session.csv", index=False)
print(f"  Daily attribution: {len(daily_df)} dates saved")

# ─── Key day diagnosis: Jun 23, Jun 08, Jun 28 ──────────────────────────────
KEY_DATES = ["2026-06-23", "2026-06-08", "2026-06-28"]
diag_rows = []

print("\n  Key day diagnosis (raw signals):")
for date in KEY_DATES:
    grp = vb[vb["rithmic_date_str"] == date]
    print(f"\n  ── {date} (n={len(grp)}, total_h10={grp['net_return_h10'].sum():+,.0f}t) ──")

    # By side
    for side in ["LONG","SHORT"]:
        sg = grp[grp["side"] == side]
        if len(sg) == 0:
            continue
        print(f"    {side:5s}: n={len(sg):>4,}  net={sg['net_return_h10'].sum():>+10,.0f}t  "
              f"wr={( sg['net_return_h10']>0).mean():.3f}  "
              f"avg_tox={sg['tox_support'].mean():>+.3f}")

    # By session
    print(f"    Session breakdown:")
    for sess in SESSIONS:
        sg = grp[grp["session"] == sess]
        if len(sg) == 0:
            continue
        long_net = sg[sg["side"]=="LONG"]["net_return_h10"].sum()
        short_net = sg[sg["side"]=="SHORT"]["net_return_h10"].sum()
        print(f"      {sess:10s}: n={len(sg):>4,}  net={sg['net_return_h10'].sum():>+10,.0f}t  "
              f"LONG={long_net:>+9,.0f}t  SHORT={short_net:>+9,.0f}t  "
              f"tox={sg['tox_support'].mean():>+.3f}")

    # Tox bucket breakdown
    print(f"    Toxicity bucket:")
    for bkt in ["TOX_AGAINST","TOX_WEAK_SUPPORT","TOX_STRONG_SUPPORT"]:
        bg = grp[grp["tox_bucket"] == bkt]
        if len(bg) == 0:
            continue
        print(f"      {bkt:22s}: n={len(bg):>4,}  net={bg['net_return_h10'].sum():>+10,.0f}t  "
              f"wr={(bg['net_return_h10']>0).mean():.3f}")

    # Save per-date rows
    for side in ["LONG","SHORT"]:
        for sess in SESSIONS:
            sg = grp[(grp["side"]==side) & (grp["session"]==sess)]
            if len(sg) == 0:
                continue
            for bkt in ["TOX_AGAINST","TOX_WEAK_SUPPORT","TOX_STRONG_SUPPORT"]:
                bg = sg[sg["tox_bucket"]==bkt]
                if len(bg) == 0:
                    continue
                diag_rows.append(dict(
                    date=date, side=side, session=sess, tox_bucket=bkt,
                    n=len(bg),
                    net_h10=bg["net_return_h10"].sum(),
                    avg_net_h10=bg["net_return_h10"].mean(),
                    win_rate_h10=(bg["net_return_h10"]>0).mean(),
                    net_h40=bg["net_return_h40"].sum(),
                    avg_prob=bg["max_prob"].mean(),
                    tox_support_mean=bg["tox_support"].mean(),
                ))

diag_df = pd.DataFrame(diag_rows)
diag_df.to_csv(OUT_DIR / "audit_8a_jun23_jun08_jun28_diagnosis.csv", index=False)
print(f"\n  Part 7 done in {time.time()-t_p7:.1f}s")

# ═══════════════════════════════════════════════════════════════════════════
# PART 8 — Rules recommendation
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 8: Forward shadow recommendation")
print("=" * 70)

# Gather key evidence
rg_long_wr  = rg[rg["side"]=="LONG"]["net_return_h10"].pipe(lambda x: (x>0).mean())
rg_short_wr = rg[rg["side"]=="SHORT"]["net_return_h10"].pipe(lambda x: (x>0).mean())
rg_long_net  = rg[rg["side"]=="LONG"]["net_return_h10"].sum()
rg_short_net = rg[rg["side"]=="SHORT"]["net_return_h10"].sum()

# Tox strong vs against
tox_all = tox_df[(tox_df["session"]=="ALL") & (tox_df["side"]=="ALL")]
strong_avg = tox_all.loc[tox_all["tox_bucket"]=="TOX_STRONG_SUPPORT","avg_net_h10"]
against_avg = tox_all.loc[tox_all["tox_bucket"]=="TOX_AGAINST","avg_net_h10"]
tox_lift = (strong_avg.values[0] - against_avg.values[0]
            if len(strong_avg) and len(against_avg) else np.nan)

# Session kill check
sess_kills = sr_rg[sr_rg["decision"] == "SESSION_KILL"]["session"].tolist()

# Recommendation logic
long_pass  = rg_long_wr >= 0.50 and rg_long_net > 0
short_pass = rg_short_wr >= 0.50 and rg_short_net > 0
tox_helps  = not np.isnan(tox_lift) and tox_lift > 2.0

if long_pass and short_pass and not sess_kills:
    recommendation = "BOTH_SIDES_ALL_SESSIONS_FORWARD_SHADOW"
elif long_pass and short_pass and sess_kills:
    recommendation = "BOTH_SIDES_WITH_SESSION_BLOCK"
elif long_pass and not short_pass:
    recommendation = "LONG_ONLY_DEFER_SHORT"
elif short_pass and not long_pass:
    recommendation = "SHORT_ONLY_DEFER_LONG"
else:
    recommendation = "STOP_AND_RESEARCH"

if tox_helps and recommendation not in ("STOP_AND_RESEARCH",):
    recommendation_tox = f"{recommendation}__TOX_SUPPORT_FILTER_OPTIONAL"
else:
    recommendation_tox = recommendation

print(f"\n  LONG  side: net={rg_long_net:>+12,.0f}t  wr={rg_long_wr:.3f}  "
      f"→ {'PASS' if long_pass else 'FAIL'}")
print(f"  SHORT side: net={rg_short_net:>+12,.0f}t  wr={rg_short_wr:.3f}  "
      f"→ {'PASS' if short_pass else 'FAIL'}")
print(f"  Tox lift (strong vs against): {tox_lift:>+.3f}t  "
      f"→ {'HELPS' if tox_helps else 'MARGINAL'}")
print(f"  SESSION_KILL sessions: {sess_kills if sess_kills else 'none'}")
print(f"\n  *** RECOMMENDATION: {recommendation_tox} ***")

# ═══════════════════════════════════════════════════════════════════════════
# Write main report
# ═══════════════════════════════════════════════════════════════════════════
report_path = OUT_DIR / "AUDIT_8A_FLOW_TOXICITY_SIDE_SESSION_REPORT.md"

# Compute summary stats for report
rg_side_summary = ss_rg.set_index("side") if len(ss_rg) else pd.DataFrame()
rg_sess_summary = sr_rg.set_index("session") if len(sr_rg) else pd.DataFrame()

best_side  = ss_rg.loc[ss_rg["total_net_h10"].idxmax(), "side"] if len(ss_rg) else "N/A"
best_sess  = sr_rg[sr_rg["session"]!="NY"].loc[
    sr_rg[sr_rg["session"]!="NY"]["total_net_h10"].idxmax(), "session"] if len(sr_rg) else "N/A"
best_combo = mat.loc[mat["total_net_h10"].idxmax(), ["session","side"]] if len(mat) else pd.Series()
worst_combo = mat.loc[mat["total_net_h10"].idxmin(), ["session","side"]] if len(mat) else pd.Series()

ab_overall = ab_df[ab_df["group"]=="OVERALL"].iloc[0] if len(ab_df) else pd.Series()

with open(report_path, "w") as f:
    f.write("# Audit 8A — Flow Toxicity Side/Session Attribution\n")
    f.write(f"**Generated**: {datetime.datetime.utcnow().isoformat()}+00:00\n")
    f.write("**Candidate**: Version B, W=20, threshold=0.65, Rule G H10 cooldown\n")
    f.write("**SHADOW / RESEARCH ONLY — no execution, no broker, no order placement**\n\n")
    f.write("---\n\n")

    f.write("## 1. Executive Summary\n\n")
    f.write(f"| Item | Value |\n|------|-------|\n")
    f.write(f"| Total OOS raw signals (V=B W=20 ≥0.65) | {len(vb):,} |\n")
    f.write(f"| Rule G trades (H10 cd 282s) | {len(rg):,} |\n")
    f.write(f"| LONG trades (Rule G) | {(rg['side']=='LONG').sum():,} |\n")
    f.write(f"| SHORT trades (Rule G) | {(rg['side']=='SHORT').sum():,} |\n")
    f.write(f"| Total net H10 (Rule G) | {rg['net_return_h10'].sum():+,.0f} ticks |\n")
    f.write(f"| Best side overall | {best_side} |\n")
    f.write(f"| Best session overall | {best_sess} |\n")
    f.write(f"| Best side×session combo (Rule G) | {best_combo.get('side','?')}×{best_combo.get('session','?')} |\n")
    f.write(f"| Worst side×session combo (Rule G) | {worst_combo.get('side','?')}×{worst_combo.get('session','?')} |\n")
    f.write(f"| Toxicity lift (strong vs against) | {tox_lift:+.2f}t/signal |\n")
    f.write(f"| V=B vs V=A avg net improvement | {ab_overall.get('B_minus_A',np.nan):+.2f}t/signal |\n")
    f.write(f"| SESSION_KILL sessions | {sess_kills if sess_kills else 'none'} |\n")
    f.write(f"| **Recommendation** | **{recommendation_tox}** |\n\n")

    f.write("---\n\n")
    f.write("## 2. Side Survival (Rule G)\n\n")
    f.write("| Side | N | Avg H10 | WR H10 | Total H10 | Pos-day% | Decision |\n")
    f.write("|------|---|---------|--------|-----------|----------|----------|\n")
    for _, r in ss_rg.iterrows():
        f.write(f"| {r['side']} | {r['n']:,} | {r['avg_net_h10']:+.2f}t | {r['win_rate_h10']:.3f} | "
                f"{r['total_net_h10']:+,.0f}t | {r['positive_day_pct']:.1%} | {r['decision']} |\n")

    f.write("\n---\n\n")
    f.write("## 3. Session Survival (Rule G)\n\n")
    f.write("| Session | N | Avg H10 | WR | LONG net | SHORT net | Best side | Decision |\n")
    f.write("|---------|---|---------|-----|----------|-----------|-----------|----------|\n")
    for _, r in sr_rg.iterrows():
        f.write(f"| {r['session']} | {r['n']:,} | {r['avg_net_h10']:+.2f}t | {r['win_rate_h10']:.3f} | "
                f"{r['long_net_h10']:+,.0f}t | {r['short_net_h10']:+,.0f}t | "
                f"{r['best_side']} | {r['decision']} |\n")

    f.write("\n---\n\n")
    f.write("## 4. Side × Session Matrix (Rule G)\n\n")
    f.write("| Session | Side | N | Avg H10 | WR | Tox Hit% | Decision |\n")
    f.write("|---------|------|---|---------|-----|----------|----------|\n")
    for _, r in mat.iterrows():
        f.write(f"| {r['session']} | {r['side']} | {r['n']:,} | {r['avg_net_h10']:+.2f}t | "
                f"{r['win_rate_h10']:.3f} | {r['tox_support_hit_rate']:.1%} | {r['decision']} |\n")

    f.write("\n---\n\n")
    f.write("## 5. Toxicity Support Attribution (ALL sessions, ALL sides — raw signals)\n\n")
    f.write("| Tox Bucket | N | Avg H10 | WR | Avg Prob |\n")
    f.write("|------------|---|---------|----|----------|\n")
    top_all = tox_df[(tox_df["session"]=="ALL") & (tox_df["side"]=="ALL")]
    for _, r in top_all.iterrows():
        f.write(f"| {r['tox_bucket']} | {r['n']:,} | {r['avg_net_h10']:+.2f}t | "
                f"{r['win_rate_h10']:.3f} | {r['avg_prob']:.3f} |\n")
    f.write(f"\n**Toxicity lift (STRONG vs AGAINST): {tox_lift:+.2f}t/signal**\n\n")

    f.write("---\n\n")
    f.write("## 6. Version A vs B Incremental (Rule G event set)\n\n")
    f.write("| Group | N | A avg H10 | B avg H10 | B−A | Δ WR | Conf-up N |\n")
    f.write("|-------|---|-----------|-----------|-----|------|----------|\n")
    for _, r in ab_df[ab_df["group"].isin(["OVERALL","side_LONG","side_SHORT"])].iterrows():
        f.write(f"| {r['group']} | {r['n']:,} | {r['A_avg_net_h10']:+.2f}t | "
                f"{r['B_avg_net_h10']:+.2f}t | {r['B_minus_A']:+.2f}t | "
                f"{r['delta_win_rate']:+.3f} | {r['n_confidence_up']:,} |\n")

    f.write("\n---\n\n")
    f.write("## 7. Key Day Diagnosis\n\n")
    for date in KEY_DATES:
        dg = daily_df[daily_df["rithmic_date_str"] == date]
        if len(dg) == 0:
            continue
        r = dg.iloc[0]
        f.write(f"### {date}\n")
        f.write(f"- Total H10: {r['total_net_h10']:+,.0f}t  "
                f"(LONG: {r['long_net_h10']:+,.0f}t, SHORT: {r['short_net_h10']:+,.0f}t)\n")
        f.write(f"- Best session: {r['best_session']}  Worst: {r['worst_session']}\n")
        f.write(f"- Mean tox support: {r['tox_support_mean']:+.3f}  "
                f"Tox-against signals: {r['tox_against_n']} ({r['pct_tox_against']:.1%})\n")
        f.write(f"- Best side×session: {r['best_side_session_combo']} "
                f"({r['pct_pnl_from_best_side_session']:.1%} of day P&L)\n\n")

    f.write("---\n\n")
    f.write("## 8. Recommendation\n\n")
    f.write(f"**{recommendation_tox}**\n\n")
    f.write("Evidence:\n")
    f.write(f"- LONG: net={rg_long_net:+,.0f}t, wr={rg_long_wr:.3f} → {'PASS' if long_pass else 'FAIL'}\n")
    f.write(f"- SHORT: net={rg_short_net:+,.0f}t, wr={rg_short_wr:.3f} → {'PASS' if short_pass else 'FAIL'}\n")
    f.write(f"- Toxicity lift: {tox_lift:+.2f}t → {'supports filtering' if tox_helps else 'marginal'}\n")
    f.write(f"- Session kills: {sess_kills if sess_kills else 'none'}\n\n")
    f.write("---\n\n")
    f.write("## Output Files\n\n")
    files = [
        "AUDIT_8A_FLOW_TOXICITY_SIDE_SESSION_REPORT.md",
        "audit_8a_side_survival_raw.csv",
        "audit_8a_side_survival_ruleG.csv",
        "audit_8a_session_survival_raw.csv",
        "audit_8a_session_survival_ruleG.csv",
        "audit_8a_side_session_matrix_ruleG.csv",
        "audit_8a_toxicity_support_by_side_session.csv",
        "audit_8a_versionA_vs_B_incremental.csv",
        "audit_8a_top_best_ruleG_trades.csv",
        "audit_8a_top_worst_ruleG_trades.csv",
        "audit_8a_daily_side_session.csv",
        "audit_8a_jun23_jun08_jun28_diagnosis.csv",
    ]
    for fn in files:
        f.write(f"- `{fn}`\n")

    f.write("\n---\n\n")
    f.write("## Final Status\n```\n")
    f.write(f"AUDIT_8A_COMPLETE:             true\n")
    f.write(f"RECOMMENDATION:               {recommendation_tox}\n")
    f.write(f"PRODUCTION_FILES_MODIFIED:     false\n")
    f.write(f"TRADING_ENABLED:               false\n")
    f.write("```\n")

print(f"\n  Saved: AUDIT_8A_FLOW_TOXICITY_SIDE_SESSION_REPORT.md")

# ═══════════════════════════════════════════════════════════════════════════
# Terminal summary
# ═══════════════════════════════════════════════════════════════════════════
elapsed = time.time() - t0
print("\n" + "=" * 70)
print("AUDIT 8A COMPLETE — TERMINAL SUMMARY")
print("=" * 70)
print(f"\n  1. Best side overall:       {best_side}")
print(f"  2. Best session overall:    {best_sess}")
print(f"  3. Best side×session:       "
      f"{best_combo.get('side','?')} × {best_combo.get('session','?')}")
print(f"  4. Worst side×session:      "
      f"{worst_combo.get('side','?')} × {worst_combo.get('session','?')}")
print(f"  5. Tox improves expectancy: {'YES' if tox_helps else 'MARGINAL'} "
      f"(lift={tox_lift:+.3f}t)")
print(f"  6. V=B improves over V=A:   {ab_overall.get('B_minus_A',np.nan):+.3f}t/signal "
      f"(conf_up: {int(ab_overall.get('n_confidence_up',0)):,})")
print(f"  7. Recommendation:          {recommendation_tox}")
print(f"  8. Output dir:              {OUT_DIR}")

import subprocess
print("\n  9. git status:")
try:
    gs = subprocess.run(["git", "status", "--short"], capture_output=True, text=True,
                        cwd=str(WORK_DIR))
    print(gs.stdout if gs.stdout.strip() else "    (not a git repo or no changes)")
except Exception:
    print("    (not a git repo)")

print("\n  10. git diff --stat:")
try:
    gd = subprocess.run(["git", "diff", "--stat"], capture_output=True, text=True,
                        cwd=str(WORK_DIR))
    print(gd.stdout if gd.stdout.strip() else "    (no staged diff)")
except Exception:
    print("    (not a git repo)")

print(f"\n  Total elapsed: {elapsed:.1f}s")
print("  PRODUCTION_FILES_MODIFIED: false")
print("  TRADING_ENABLED: false")
print("=" * 70)
