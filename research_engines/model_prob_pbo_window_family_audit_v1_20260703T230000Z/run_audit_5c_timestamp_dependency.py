#!/usr/bin/env python3
"""
Audit 5C — Timestamp Dependency / PnL Concentration Audit
Candidate: Version B, W=20, threshold=0.65, H10 primary, H40 secondary, cost=2.0t
SHADOW / RESEARCH ONLY — no execution, no broker, no order placement.
DO NOT optimize, change threshold, or alter model/window.
"""
# ─────────────────────────────────────────────────────────────────────────────
# INVARIANT CONSTRAINTS — DO NOT MODIFY
# SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING
# ─────────────────────────────────────────────────────────────────────────────

import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

# ─── Paths ────────────────────────────────────────────────────────────────────
PBO_DIR   = Path(__file__).parent
MODEL_DIR = Path("/home/prabh/OFI_Production/model_registry"
                 "/level_reaction_continuous_nq_shadow"
                 "/level_reaction_continuous_nq_shadow_20260702T005104Z")
OOS_TABLE = PBO_DIR / "oos_probability_table.parquet"
AUDIT_ROOT = PBO_DIR / "audits"

TS      = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
OUT_DIR = AUDIT_ROOT / f"audit_5c_timestamp_dependency_{TS}"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ─── Constants ────────────────────────────────────────────────────────────────
CANDIDATE_VERSION = "B"
CANDIDATE_WINDOW  = 20
THRESHOLD         = 0.65
COST_TICKS        = 2.0
HORIZON_PRIMARY   = 10
HORIZON_SECONDARY = 40
KEY_DAYS          = ["2026-06-23", "2026-06-08", "2026-06-28"]
TZ_LOCAL          = ZoneInfo("America/Edmonton")   # MDT = UTC-6 during June
MEDIAN_BAR_S      = 28.28   # from snapshot: median bar duration in seconds
H10_COOLDOWN_NS   = int(10 * MEDIAN_BAR_S * 1e9)  # ~283s
H40_COOLDOWN_NS   = int(40 * MEDIAN_BAR_S * 1e9)  # ~1131s


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _hdr(title: str) -> None:
    print(); print("=" * 70); print(title); print("=" * 70)


def add_time_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Add UTC datetime, local datetime, and all time bucket columns."""
    df = df.copy()
    df["dt_utc"]   = pd.to_datetime(df["timestamp_ns"], unit="ns", utc=True)
    df["dt_local"] = df["dt_utc"].dt.tz_convert(TZ_LOCAL)
    df["min1_utc"]  = df["dt_utc"].dt.floor("1min")
    df["min5_utc"]  = df["dt_utc"].dt.floor("5min")
    df["min15_utc"] = df["dt_utc"].dt.floor("15min")
    df["min60_utc"] = df["dt_utc"].dt.floor("60min")
    df["min1_local"]  = df["dt_local"].dt.floor("1min")
    df["min5_local"]  = df["dt_local"].dt.floor("5min")
    df["min15_local"] = df["dt_local"].dt.floor("15min")
    df["min60_local"] = df["dt_local"].dt.floor("60min")
    return df


def bucket_stats(grp: pd.DataFrame, bucket_col: str,
                 day_net: float, total_net: float) -> pd.DataFrame:
    """Aggregate per-bucket stats for one day's signals."""
    agg = grp.groupby(bucket_col).agg(
        n_signals      = ("event_id",           "count"),
        n_long         = ("signal",              lambda x: (x == "LONG").sum()),
        n_short        = ("signal",              lambda x: (x == "SHORT").sum()),
        net_h10        = ("net_return_h10",      "sum"),
        net_h40        = ("net_return_h40",      "sum"),
        win_rate_h10   = ("net_return_h10",      lambda x: (x > 0).mean()),
        avg_prob       = ("prob_max",            "mean"),
        avg_prob_edge  = ("prob_edge",           "mean"),
        avg_fwd_h10    = ("fwd_return_ticks_h10","mean"),
        avg_fwd_h40    = ("fwd_return_ticks_h40","mean"),
    ).reset_index()
    agg.columns.name = None

    # Add local time (floor to same bucket)
    local_map = (grp.groupby(bucket_col)["dt_local"]
                 .first().dt.floor(bucket_col.split("_")[0][-1] + "min"
                                   if "min" in bucket_col else "60min"))
    try:
        agg["bucket_utc"] = agg[bucket_col]
    except Exception:
        pass

    agg["pct_of_day_pnl"]   = agg["net_h10"] / day_net  if day_net != 0 else np.nan
    agg["pct_of_total_pnl"] = agg["net_h10"] / total_net if total_net != 0 else np.nan
    # Attempt session from mode
    sess_mode = grp.groupby(bucket_col)["session"].agg(lambda x: x.mode().iloc[0])
    agg = agg.merge(sess_mode.rename("session"), left_on=bucket_col,
                    right_index=True, how="left")
    return agg.sort_values("net_h10", ascending=False)


def thinning_stats(df: pd.DataFrame, label: str,
                   total_net_raw: float) -> dict:
    """Compute performance metrics for a thinned signal set."""
    if len(df) == 0:
        return {"rule": label, "n_trades": 0, "avg_net_h10": np.nan,
                "total_net_h10": 0.0, "win_rate_h10": np.nan,
                "avg_net_h40": np.nan, "total_net_h40": 0.0,
                "win_rate_h40": np.nan, "positive_days": 0,
                "positive_day_pct": 0.0, "best_day_pct_of_total": np.nan,
                "top2_days_pct_of_total": np.nan, "max_drawdown": np.nan,
                "decision": "KILL"}

    daily = df.groupby("rithmic_date_str").agg(
        net_h10=("net_return_h10", "sum"),
        net_h40=("net_return_h40", "sum"),
    )
    total_h10 = float(df["net_return_h10"].sum())
    total_h40 = float(df["net_return_h40"].sum())
    n_days     = len(daily)
    pos_days   = int((daily["net_h10"] > 0).sum())

    cum = daily["net_h10"].cumsum()
    dd  = float((cum - cum.cummax()).min())

    best_day   = float(daily["net_h10"].max())
    top2_sum   = float(daily["net_h10"].nlargest(2).sum())
    best_pct   = best_day  / total_h10 if total_h10 != 0 else np.nan
    top2_pct   = top2_sum  / total_h10 if total_h10 != 0 else np.nan

    cond_pos     = total_h10 > 0
    cond_wr      = float((df["net_return_h10"] > 0).mean()) > 0.50
    cond_posday  = pos_days / n_days >= 0.60 if n_days > 0 else False
    cond_best    = (best_pct <= 0.25) if not np.isnan(best_pct) else False
    cond_top2    = (top2_pct <= 0.40) if not np.isnan(top2_pct) else False

    if cond_pos and cond_wr and cond_posday and cond_best and cond_top2:
        decision = "PASS"
    elif cond_pos and cond_wr and not (cond_best and cond_top2):
        decision = "DEFER__concentration_remains_high"
    elif not cond_pos:
        decision = "KILL"
    else:
        decision = "DEFER"

    return {
        "rule":                   label,
        "n_trades":               len(df),
        "avg_net_h10":            float(df["net_return_h10"].mean()),
        "total_net_h10":          total_h10,
        "win_rate_h10":           float((df["net_return_h10"] > 0).mean()),
        "avg_net_h40":            float(df["net_return_h40"].mean()),
        "total_net_h40":          total_h40,
        "win_rate_h40":           float((df["net_return_h40"] > 0).mean()),
        "positive_days":          pos_days,
        "positive_day_pct":       round(pos_days / n_days, 4) if n_days > 0 else np.nan,
        "best_day_pct_of_total":  round(best_pct, 4) if not np.isnan(best_pct) else np.nan,
        "top2_days_pct_of_total": round(top2_pct, 4) if not np.isnan(top2_pct) else np.nan,
        "max_drawdown":           dd,
        "decision":               decision,
    }


def apply_cooldown_per_dir(df: pd.DataFrame, cooldown_ns: int) -> pd.DataFrame:
    """Keep first signal per direction after cooldown_ns gap from last kept signal."""
    df_s = df.sort_values("timestamp_ns").copy()
    last_ts = {"LONG": -10**18, "SHORT": -10**18}
    keep = []
    for row in df_s.itertuples(index=False):
        d = row.signal
        if row.timestamp_ns - last_ts[d] >= cooldown_ns:
            keep.append(True)
            last_ts[d] = row.timestamp_ns
        else:
            keep.append(False)
    return df_s[keep]


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    t_start  = time.time()
    now_utc  = datetime.now(timezone.utc).isoformat()
    print("=" * 70)
    print("Audit 5C — Timestamp Dependency / PnL Concentration Audit")
    print("SHADOW / RESEARCH ONLY — no execution, no broker")
    print(f"Output: {OUT_DIR}")
    print("=" * 70)

    # ── Load & filter candidate signals ──────────────────────────────────────
    _hdr("PART 0: Load and filter candidate signals")
    oos = pd.read_parquet(OOS_TABLE)
    cand = oos[(oos["version"] == CANDIDATE_VERSION) &
               (oos["window"]  == CANDIDATE_WINDOW)].copy()
    cand["signal"] = np.where(
        cand["prob_long"] >= THRESHOLD, "LONG",
        np.where(cand["prob_long"] <= (1 - THRESHOLD), "SHORT", "NONE")
    )
    sig = cand[cand["signal"] != "NONE"].copy()
    sig["prob_max"]   = sig[["prob_long", "prob_short"]].max(axis=1)
    sig["bet_dir"]    = sig["signal"].map({"LONG": 1, "SHORT": -1})
    sig["vol_regime"] = np.where(
        sig["fwd_return_ticks_h10"].abs() >= sig["fwd_return_ticks_h10"].abs().median(),
        "HIGH_VOL", "LOW_VOL"
    )

    # Add time columns
    sig = add_time_cols(sig)

    total_net_h10 = float(sig["net_return_h10"].sum())
    total_net_h40 = float(sig["net_return_h40"].sum())
    n_days = sig["rithmic_date_str"].nunique()

    print(f"  V=B W=20 total events: {len(cand):,}")
    print(f"  Signals ≥{THRESHOLD}: {len(sig):,} "
          f"({len(sig)/len(cand)*100:.1f}%)  "
          f"[{(sig['signal']=='LONG').sum():,} LONG, "
          f"{(sig['signal']=='SHORT').sum():,} SHORT]")
    print(f"  Trading days: {n_days}")
    print(f"  Total net H10: {total_net_h10:+,.0f} ticks")
    print(f"  Total net H40: {total_net_h40:+,.0f} ticks")
    print(f"  Columns available: {list(sig.columns)}")

    # ─────────────────────────────────────────────────────────────────────────
    # PART 1 — Daily concentration
    # ─────────────────────────────────────────────────────────────────────────
    _hdr("PART 1: Daily concentration")

    daily = sig.groupby("rithmic_date_str").agg(
        n_signals    = ("event_id",            "count"),
        n_long       = ("signal",              lambda x: (x == "LONG").sum()),
        n_short      = ("signal",              lambda x: (x == "SHORT").sum()),
        net_h10      = ("net_return_h10",      "sum"),
        net_h40      = ("net_return_h40",      "sum"),
        win_rate_h10 = ("net_return_h10",      lambda x: (x > 0).mean()),
    ).reset_index()

    daily["pct_total_h10"]    = daily["net_h10"] / total_net_h10
    daily["pct_total_h40"]    = daily["net_h40"] / total_net_h40
    daily["rank_by_net_h10"]  = daily["net_h10"].rank(ascending=False).astype(int)
    daily["rank_by_abs"]      = daily["net_h10"].abs().rank(ascending=False).astype(int)

    # Cumulative % of absolute PnL (Lorenz-style concentration)
    daily_sorted = daily.sort_values("net_h10", ascending=False).copy()
    daily_sorted["cum_pct_abs_h10"] = (
        daily_sorted["net_h10"].abs().cumsum()
        / daily["net_h10"].abs().sum()
    )
    daily = daily.merge(
        daily_sorted[["rithmic_date_str", "cum_pct_abs_h10"]],
        on="rithmic_date_str", how="left"
    )

    daily.to_csv(OUT_DIR / "audit_5c_daily_concentration.csv", index=False)

    # Print summary
    print(f"  {'Date':12s} {'N':>6s} {'Net H10':>12s} {'%Total':>8s} {'WinR':>6s}")
    for _, r in daily.sort_values("net_h10", ascending=False).iterrows():
        print(f"  {r['rithmic_date_str']:12s} {r['n_signals']:>6,} "
              f"{r['net_h10']:>+12,.0f} {r['pct_total_h10']:>8.1%} "
              f"{r['win_rate_h10']:>6.3f}")

    top2_sum = daily.nlargest(2, "net_h10")["net_h10"].sum()
    print(f"\n  Top-2 days pct of total H10: {top2_sum/total_net_h10:.1%}")
    print(f"  Saved: audit_5c_daily_concentration.csv")

    # ─────────────────────────────────────────────────────────────────────────
    # PART 2 — Exact timestamp contribution for key days
    # ─────────────────────────────────────────────────────────────────────────
    _hdr("PART 2: Exact timestamp contribution for key days")

    day_nets = daily.set_index("rithmic_date_str")["net_h10"].to_dict()

    all_raw = []
    all_1min = []; all_5min = []; all_15min = []; all_60min = []

    for kd in KEY_DAYS:
        day_df  = sig[sig["rithmic_date_str"] == kd].copy()
        day_net = day_nets.get(kd, 0)
        if len(day_df) == 0:
            print(f"  {kd}: no signals found")
            continue

        print(f"\n  {kd}  ({len(day_df):,} signals, net H10={day_net:+,.0f}t)")

        # Raw events
        raw_ev = day_df[[
            "event_id", "timestamp_ns", "dt_utc", "dt_local", "signal",
            "prob_long", "prob_max", "prob_edge",
            "fwd_return_ticks_h10", "fwd_return_ticks_h40",
            "net_return_h10", "net_return_h40",
            "session", "vol_regime",
        ]].copy()
        raw_ev["pct_of_day_pnl"]   = raw_ev["net_return_h10"] / day_net if day_net != 0 else np.nan
        raw_ev["pct_of_total_pnl"] = raw_ev["net_return_h10"] / total_net_h10
        raw_ev["key_date"] = kd
        all_raw.append(raw_ev.nlargest(200, "net_return_h10"))

        for bucket_col, bucket_label, out_list in [
            ("min1_utc",  "1min",  all_1min),
            ("min5_utc",  "5min",  all_5min),
            ("min15_utc", "15min", all_15min),
            ("min60_utc", "60min", all_60min),
        ]:
            b = bucket_stats(day_df, bucket_col, day_net, total_net_h10)
            b["key_date"] = kd
            b["bucket_size"] = bucket_label
            # Show top/bottom
            top3 = b.nlargest(3, "net_h10")
            bot3 = b.nsmallest(3, "net_h10")
            print(f"    [{bucket_label}] top-3 buckets by net H10:")
            for _, r in top3.iterrows():
                print(f"      {r[bucket_col]}  "
                      f"n={int(r['n_signals']):4d}  "
                      f"net={r['net_h10']:+10,.0f}t  "
                      f"wr={r['win_rate_h10']:.3f}  "
                      f"{r['pct_of_day_pnl']:+.1%} of day")
            out_list.append(b)

    # Save
    if all_raw:
        pd.concat(all_raw, ignore_index=True).to_csv(
            OUT_DIR / "audit_5c_top_raw_events_key_days.csv", index=False)
    if all_1min:
        pd.concat(all_1min, ignore_index=True).to_csv(
            OUT_DIR / "audit_5c_top_1min_buckets_key_days.csv", index=False)
    if all_5min:
        pd.concat(all_5min, ignore_index=True).to_csv(
            OUT_DIR / "audit_5c_top_5min_buckets_key_days.csv", index=False)
    if all_15min:
        pd.concat(all_15min, ignore_index=True).to_csv(
            OUT_DIR / "audit_5c_top_15min_buckets_key_days.csv", index=False)
    if all_60min:
        pd.concat(all_60min, ignore_index=True).to_csv(
            OUT_DIR / "audit_5c_top_60min_buckets_key_days.csv", index=False)

    print("\n  Saved all key-day bucket CSVs")

    # ─────────────────────────────────────────────────────────────────────────
    # PART 3 — PnL burst concentration (all days)
    # ─────────────────────────────────────────────────────────────────────────
    _hdr("PART 3: PnL burst concentration by day")

    burst_rows = []

    for date in sorted(sig["rithmic_date_str"].unique()):
        day_df  = sig[sig["rithmic_date_str"] == date]
        day_net = float(day_df["net_return_h10"].sum())
        if len(day_df) == 0:
            continue

        row = {"date": date, "n_signals": len(day_df), "day_net_h10": day_net}

        for bucket_col, bsize in [
            ("min1_utc",  "1min"),
            ("min5_utc",  "5min"),
            ("min15_utc", "15min"),
            ("min60_utc", "60min"),
        ]:
            b = day_df.groupby(bucket_col)["net_return_h10"].sum()
            best  = float(b.max())
            worst = float(b.min())
            top3_sum = float(b.nlargest(3).sum())
            n_pos = int((b > 0).sum())
            n_neg = int((b < 0).sum())

            pct_best  = best  / day_net if day_net != 0 else np.nan
            pct_top3  = top3_sum / day_net if day_net != 0 else np.nan

            row[f"best_{bsize}_net"]      = best
            row[f"worst_{bsize}_net"]     = worst
            row[f"best_{bsize}_pct_day"]  = pct_best
            row[f"top3_{bsize}_pct_day"]  = pct_top3
            row[f"n_pos_{bsize}"]         = n_pos
            row[f"n_neg_{bsize}"]         = n_neg

        # Burst flags (applied only where day_net > 0 is meaningful)
        top3_15_pct = row.get("top3_15min_pct_day", np.nan)
        best_15_pct = row.get("best_15min_pct_day", np.nan)

        if not np.isnan(top3_15_pct):
            if best_15_pct > 0.40:
                row["burst_flag"] = "SINGLE_BURST_DEPENDENT"
            elif top3_15_pct > 0.60:
                row["burst_flag"] = "BURST_DEPENDENT"
            elif top3_15_pct < 0.40 and day_net > 0:
                row["burst_flag"] = "HEALTHY_SPREAD"
            else:
                row["burst_flag"] = "MIXED"
        else:
            row["burst_flag"] = "INSUFFICIENT_DATA"

        burst_rows.append(row)

    burst_df = pd.DataFrame(burst_rows)
    burst_df.to_csv(OUT_DIR / "audit_5c_burst_concentration_by_day.csv", index=False)

    print(f"  {'Date':12s} {'DayNet':>10s} {'Best5m%':>9s} {'Top3-15m%':>11s} "
          f"{'Flag':30s}")
    for _, r in burst_df.iterrows():
        print(f"  {r['date']:12s} {r['day_net_h10']:>+10,.0f} "
              f"{r.get('best_5min_pct_day', np.nan):>9.1%} "
              f"{r.get('top3_15min_pct_day', np.nan):>11.1%} "
              f"{r.get('burst_flag','?'):30s}")

    print(f"\n  Flag distribution:")
    print(burst_df["burst_flag"].value_counts().to_string())
    print(f"  Saved: audit_5c_burst_concentration_by_day.csv")

    # ─────────────────────────────────────────────────────────────────────────
    # PART 4 — Overlap / repeated-signal dependency (thinning)
    # ─────────────────────────────────────────────────────────────────────────
    _hdr("PART 4: Signal thinning — overlap/repeated-signal analysis")

    thinning_rows = []

    # Rule A: Raw
    thinning_rows.append(thinning_stats(sig, "A__raw_all_signals", total_net_h10))
    print(f"  A raw:  {len(sig):,} signals")

    # Rule B: One per 1-min per direction (first by time)
    sig["_bucket_1min"] = (sig["timestamp_ns"] // int(60e9)).astype(int)
    thin_B = (sig.sort_values("timestamp_ns")
                 .drop_duplicates(subset=["_bucket_1min", "signal"], keep="first"))
    thinning_rows.append(thinning_stats(thin_B, "B__1min_per_dir", total_net_h10))
    print(f"  B 1min_per_dir: {len(thin_B):,} signals")

    # Rule C: One per 5-min per direction
    sig["_bucket_5min"] = (sig["timestamp_ns"] // int(5 * 60e9)).astype(int)
    thin_C = (sig.sort_values("timestamp_ns")
                 .drop_duplicates(subset=["_bucket_5min", "signal"], keep="first"))
    thinning_rows.append(thinning_stats(thin_C, "C__5min_per_dir", total_net_h10))
    print(f"  C 5min_per_dir: {len(thin_C):,} signals")

    # Rule D: One per 10-min per direction
    sig["_bucket_10min"] = (sig["timestamp_ns"] // int(10 * 60e9)).astype(int)
    thin_D = (sig.sort_values("timestamp_ns")
                 .drop_duplicates(subset=["_bucket_10min", "signal"], keep="first"))
    thinning_rows.append(thinning_stats(thin_D, "D__10min_per_dir", total_net_h10))
    print(f"  D 10min_per_dir: {len(thin_D):,} signals")

    # Rule E: One per 15-min per direction
    sig["_bucket_15min"] = (sig["timestamp_ns"] // int(15 * 60e9)).astype(int)
    thin_E = (sig.sort_values("timestamp_ns")
                 .drop_duplicates(subset=["_bucket_15min", "signal"], keep="first"))
    thinning_rows.append(thinning_stats(thin_E, "E__15min_per_dir", total_net_h10))
    print(f"  E 15min_per_dir: {len(thin_E):,} signals")

    # Rule F: Strongest prob per 10-min block (across both directions)
    thin_F = sig.loc[sig.groupby("_bucket_10min")["prob_edge"].idxmax()]
    thinning_rows.append(thinning_stats(thin_F, "F__best_prob_10min", total_net_h10))
    print(f"  F best_prob_10min: {len(thin_F):,} signals")

    # Rule G: First signal after H10-bar cooldown per direction (~283s)
    thin_G = apply_cooldown_per_dir(sig, H10_COOLDOWN_NS)
    thinning_rows.append(thinning_stats(thin_G, f"G__cooldown_H10bars_{H10_COOLDOWN_NS//int(1e9)}s",
                                        total_net_h10))
    print(f"  G cooldown H10bars ({H10_COOLDOWN_NS//int(1e9)}s): {len(thin_G):,} signals")

    # Rule H: First signal after H40-bar cooldown per direction (~1131s)
    thin_H = apply_cooldown_per_dir(sig, H40_COOLDOWN_NS)
    thinning_rows.append(thinning_stats(thin_H, f"H__cooldown_H40bars_{H40_COOLDOWN_NS//int(1e9)}s",
                                        total_net_h10))
    print(f"  H cooldown H40bars ({H40_COOLDOWN_NS//int(1e9)}s): {len(thin_H):,} signals")

    thin_df = pd.DataFrame(thinning_rows)
    thin_df.to_csv(OUT_DIR / "audit_5c_thinning_results.csv", index=False)

    print(f"\n  Thinning summary:")
    print(f"  {'Rule':40s} {'N':>7s} {'AvgNet':>8s} {'TotalNet':>12s} "
          f"{'WR':>6s} {'BestDay%':>9s} {'Top2%':>7s} {'Decision':30s}")
    for _, r in thin_df.iterrows():
        print(f"  {r['rule']:40s} {r['n_trades']:>7,} "
              f"{r['avg_net_h10']:>+8.2f} {r['total_net_h10']:>+12,.0f} "
              f"{r['win_rate_h10']:>6.3f} "
              f"{r.get('best_day_pct_of_total', np.nan):>9.1%} "
              f"{r.get('top2_days_pct_of_total', np.nan):>7.1%} "
              f"{r['decision']:30s}")
    print(f"  Saved: audit_5c_thinning_results.csv")

    # ─────────────────────────────────────────────────────────────────────────
    # PART 5 — Big days vs normal days comparison
    # ─────────────────────────────────────────────────────────────────────────
    _hdr("PART 5: Big days vs normal days comparison")

    top2_dates   = set(daily.nlargest(2, "net_h10")["rithmic_date_str"])
    bot2_dates   = set(daily.nsmallest(2, "net_h10")["rithmic_date_str"])
    normal_dates = set(daily["rithmic_date_str"]) - top2_dates - bot2_dates

    compare_rows = []
    for group_name, dates in [
        ("big_win_days",  top2_dates),
        ("bad_days",      bot2_dates),
        ("normal_days",   normal_dates),
    ]:
        g = sig[sig["rithmic_date_str"].isin(dates)]
        if len(g) == 0:
            compare_rows.append({"group": group_name})
            continue

        top2_pct_day = (
            g.groupby("rithmic_date_str")["net_return_h10"].sum()
             .apply(lambda d_net: g[g["rithmic_date_str"] == _]
                    .sort_values("net_return_h10", ascending=False)
                    .head(3)["net_return_h10"].sum() / d_net
                    if d_net != 0 else np.nan)
            if False else np.nan  # computed in burst_df instead
        )

        # Burst concentration from burst_df
        burst_sub = burst_df[burst_df["date"].isin(dates)]

        row = {
            "group":             group_name,
            "n_dates":           len(dates),
            "dates":             ",".join(sorted(dates)),
            "total_signals":     len(g),
            "avg_signals_day":   len(g) / len(dates) if dates else 0,
            "total_net_h10":     float(g["net_return_h10"].sum()),
            "avg_net_h10_day":   float(g["net_return_h10"].sum()) / len(dates) if dates else 0,
            "avg_net_h10_signal":float(g["net_return_h10"].mean()),
            "avg_prob":          float(g["prob_max"].mean()),
            "avg_prob_edge":     float(g["prob_edge"].mean()),
            "long_pct":          float((g["signal"] == "LONG").mean()),
            "short_pct":         float((g["signal"] == "SHORT").mean()),
            "win_rate_h10":      float((g["net_return_h10"] > 0).mean()),
            "win_rate_h40":      float((g["net_return_h40"] > 0).mean()),
            "avg_fwd_h10":       float(g["fwd_return_ticks_h10"].mean()),
            "avg_fwd_h40":       float(g["fwd_return_ticks_h40"].mean()),
            "high_vol_pct":      float((g["vol_regime"] == "HIGH_VOL").mean()),
            # Burst metrics (avg across dates in group)
            "avg_best_5min_pct_day":   float(burst_sub["best_5min_pct_day"].mean())
                                        if "best_5min_pct_day" in burst_sub.columns else np.nan,
            "avg_top3_15min_pct_day":  float(burst_sub["top3_15min_pct_day"].mean())
                                        if "top3_15min_pct_day" in burst_sub.columns else np.nan,
        }
        # Session mix
        for s in ["Asia", "EU", "US_Open", "US_AM", "US_PM", "US_Late"]:
            row[f"sess_{s}_pct"] = float((g["session"] == s).mean())

        compare_rows.append(row)
        print(f"\n  {group_name} ({len(dates)} days):")
        print(f"    Avg signals/day:   {row['avg_signals_day']:,.0f}")
        print(f"    Avg net H10/day:   {row['avg_net_h10_day']:+,.0f}t")
        print(f"    Avg net H10/sig:   {row['avg_net_h10_signal']:+.2f}t")
        print(f"    Win rate H10:      {row['win_rate_h10']:.3f}")
        print(f"    Avg prob:          {row['avg_prob']:.4f}")
        print(f"    Long/Short:        {row['long_pct']:.1%}/{row['short_pct']:.1%}")
        print(f"    High vol pct:      {row['high_vol_pct']:.1%}")
        print(f"    Avg top3-15min% of day: {row['avg_top3_15min_pct_day']:.1%}")

    compare_df = pd.DataFrame(compare_rows)
    compare_df.to_csv(OUT_DIR / "audit_5c_big_vs_normal_days.csv", index=False)
    print(f"\n  Saved: audit_5c_big_vs_normal_days.csv")

    # ─────────────────────────────────────────────────────────────────────────
    # PART 6 — Failure analysis for 2026-06-28
    # ─────────────────────────────────────────────────────────────────────────
    _hdr("PART 6: Failure analysis for 2026-06-28")

    fail_date = "2026-06-28"
    fail_df   = sig[sig["rithmic_date_str"] == fail_date].copy()
    fail_net  = float(fail_df["net_return_h10"].sum())
    print(f"  {fail_date}: {len(fail_df):,} signals, net H10={fail_net:+,.0f}t")

    failure_rows = []

    if len(fail_df) > 0:
        # 5-min clusters
        for bucket_col, bsize in [("min1_utc", "1min"), ("min5_utc", "5min"),
                                   ("min15_utc", "15min")]:
            b = fail_df.groupby(bucket_col).agg(
                n_signals     = ("event_id",            "count"),
                n_long        = ("signal",              lambda x: (x == "LONG").sum()),
                n_short       = ("signal",              lambda x: (x == "SHORT").sum()),
                net_h10       = ("net_return_h10",      "sum"),
                net_h40       = ("net_return_h40",      "sum"),
                win_rate      = ("net_return_h10",      lambda x: (x > 0).mean()),
                avg_prob      = ("prob_max",            "mean"),
                avg_fwd_h10   = ("fwd_return_ticks_h10","mean"),
            ).reset_index()
            b.columns.name = None
            b["pct_of_day"]   = b["net_h10"] / fail_net if fail_net != 0 else np.nan
            b["bucket_size"]  = bsize

            worst3 = b.nsmallest(3, "net_h10")
            print(f"    Worst {bsize} clusters:")
            for _, r in worst3.iterrows():
                print(f"      {r[bucket_col]}  "
                      f"n={int(r['n_signals']):4d}  "
                      f"net={r['net_h10']:+8,.0f}t  "
                      f"wr={r['win_rate']:.3f}  "
                      f"{r['pct_of_day']:.1%} of day  "
                      f"L/S={int(r['n_long'])}/{int(r['n_short'])}")
            failure_rows.append(b)

        # Long vs short contribution
        ls_breakdown = fail_df.groupby("signal").agg(
            n_signals    = ("event_id",       "count"),
            net_h10      = ("net_return_h10", "sum"),
            win_rate     = ("net_return_h10", lambda x: (x > 0).mean()),
            avg_prob     = ("prob_max",       "mean"),
            avg_fwd_h10  = ("fwd_return_ticks_h10", "mean"),
        ).reset_index()
        print(f"\n    Long vs Short on {fail_date}:")
        print(ls_breakdown.to_string(index=False))

        # Session contribution
        sess_breakdown = fail_df.groupby("session").agg(
            n_signals = ("event_id",       "count"),
            net_h10   = ("net_return_h10", "sum"),
            win_rate  = ("net_return_h10", lambda x: (x > 0).mean()),
        ).reset_index()
        print(f"\n    Session contribution on {fail_date}:")
        print(sess_breakdown.sort_values("net_h10").to_string(index=False))

        # Probability bucket contribution
        PROB_BINS   = [0.65, 0.70, 0.75, 0.80, 0.85, 1.01]
        PROB_LABELS = ["0.65-0.70", "0.70-0.75", "0.75-0.80", "0.80-0.85", "0.85+"]
        prob_rows = []
        for i, lab in enumerate(PROB_LABELS):
            lo, hi = PROB_BINS[i], PROB_BINS[i + 1]
            sub = fail_df[fail_df["prob_max"] >= lo][fail_df["prob_max"] < hi] \
                  if i < len(PROB_LABELS) - 1 else fail_df[fail_df["prob_max"] >= lo]
            if len(sub) == 0:
                continue
            prob_rows.append({
                "bucket":    lab,
                "n":         len(sub),
                "net_h10":   float(sub["net_return_h10"].sum()),
                "net_h40":   float(sub["net_return_h40"].sum()),
                "win_rate":  float((sub["net_return_h10"] > 0).mean()),
                "avg_prob":  float(sub["prob_max"].mean()),
            })
        prob_df = pd.DataFrame(prob_rows)
        print(f"\n    Prob bucket breakdown on {fail_date}:")
        print(prob_df.to_string(index=False))

        # Was model confidently wrong?
        confidently_wrong = fail_df[
            (fail_df["prob_max"] >= 0.80) & (fail_df["net_return_h10"] < 0)
        ]
        n_conf_wrong = len(confidently_wrong)
        pct_conf_wrong = n_conf_wrong / len(fail_df)
        print(f"\n    Confidently wrong signals (prob≥0.80, net<0): "
              f"{n_conf_wrong:,} / {len(fail_df):,} = {pct_conf_wrong:.1%}")
        print(f"    H40 also failed? H40 net on {fail_date}: "
              f"{fail_df['net_return_h40'].sum():+,.0f}t")
        print(f"    H40 win rate: {(fail_df['net_return_h40']>0).mean():.3f}")

        # Save failure clusters
        if failure_rows:
            pd.concat(failure_rows, ignore_index=True).to_csv(
                OUT_DIR / "audit_5c_jun28_failure_clusters.csv", index=False)
            print(f"  Saved: audit_5c_jun28_failure_clusters.csv")

    # ─────────────────────────────────────────────────────────────────────────
    # PART 7 — Report
    # ─────────────────────────────────────────────────────────────────────────
    _hdr("PART 7: Write report")

    _write_report(
        now_utc, sig, daily, burst_df, thin_df, compare_df,
        fail_df, fail_date, total_net_h10, total_net_h40, n_days,
        top2_dates, bot2_dates, thinning_rows
    )

    # ─────────────────────────────────────────────────────────────────────────
    # TERMINAL SUMMARY PRINTS
    # ─────────────────────────────────────────────────────────────────────────
    _hdr("TERMINAL SUMMARY")

    # Build global 15-min bucket table
    all_15 = sig.groupby("min15_utc").agg(
        n_signals  = ("event_id",       "count"),
        net_h10    = ("net_return_h10", "sum"),
        win_rate   = ("net_return_h10", lambda x: (x > 0).mean()),
        avg_prob   = ("prob_max",       "mean"),
        date       = ("rithmic_date_str","first"),
    ).reset_index()
    all_15.columns.name = None

    print("\n  TOP 10 POSITIVE 15-MIN WINDOWS (global):")
    print(f"  {'UTC Time':22s} {'Date':12s} {'N':>5s} {'Net H10':>10s} "
          f"{'WinR':>6s} {'AvgProb':>8s}")
    for _, r in all_15.nlargest(10, "net_h10").iterrows():
        print(f"  {str(r['min15_utc']):22s} {r['date']:12s} "
              f"{r['n_signals']:>5,} {r['net_h10']:>+10,.0f} "
              f"{r['win_rate']:>6.3f} {r['avg_prob']:>8.4f}")

    print("\n  TOP 10 NEGATIVE 15-MIN WINDOWS (global):")
    for _, r in all_15.nsmallest(10, "net_h10").iterrows():
        print(f"  {str(r['min15_utc']):22s} {r['date']:12s} "
              f"{r['n_signals']:>5,} {r['net_h10']:>+10,.0f} "
              f"{r['win_rate']:>6.3f} {r['avg_prob']:>8.4f}")

    print("\n  THINNING RESULT SUMMARY:")
    print(f"  {'Rule':40s} {'N':>7s} {'AvgNet':>8s} {'TotalNet':>12s} "
          f"{'Decision':30s}")
    for r in thinning_rows:
        print(f"  {r['rule']:40s} {r['n_trades']:>7,} "
              f"{r['avg_net_h10']:>+8.2f} {r['total_net_h10']:>+12,.0f} "
              f"  {r['decision']:30s}")

    # Final decision
    thin_pass  = [r for r in thinning_rows if r["decision"] == "PASS"]
    thin_defer = [r for r in thinning_rows if "DEFER" in r["decision"]]
    thin_kill  = [r for r in thinning_rows if r["decision"] == "KILL"]
    burst_flags = burst_df["burst_flag"].value_counts()

    n_healthy  = burst_flags.get("HEALTHY_SPREAD", 0)
    n_burst    = burst_flags.get("BURST_DEPENDENT", 0) + burst_flags.get("SINGLE_BURST_DEPENDENT", 0)

    if thin_kill:
        final_dec = "FAIL_AFTER_THINNING"
    elif len(thin_pass) >= 2 and n_healthy >= n_burst:
        final_dec = "HEALTHY_SPREAD"
    elif thin_pass and n_burst > n_healthy:
        final_dec = "BURST_DEPENDENT_DEFER"
    elif not thin_pass and thin_defer:
        final_dec = "OVERLAP_DEPENDENT_DEFER"
    else:
        final_dec = "BURST_DEPENDENT_DEFER"

    print(f"\n  FINAL DECISION: {final_dec}")
    print(f"  Output dir: {OUT_DIR}")

    import subprocess
    print("\n  GIT STATUS:")
    r = subprocess.run(["git", "status"], capture_output=True, text=True)
    print(r.stdout[:800])
    print("\n  GIT DIFF --STAT:")
    r2 = subprocess.run(["git", "diff", "--stat"], capture_output=True, text=True)
    print(r2.stdout[:400] if r2.stdout else "  (no staged diff)")

    print(f"\n  Audit 5C COMPLETE in {time.time()-t_start:.1f}s")
    print("=" * 70)


# ─────────────────────────────────────────────────────────────────────────────
# REPORT WRITER
# ─────────────────────────────────────────────────────────────────────────────

def _write_report(
    now_utc, sig, daily, burst_df, thin_df, compare_df,
    fail_df, fail_date, total_net_h10, total_net_h40, n_days,
    top2_dates, bot2_dates, thinning_rows
):
    thin_pass  = [r for r in thinning_rows if r["decision"] == "PASS"]
    thin_kill  = [r for r in thinning_rows if r["decision"] == "KILL"]
    burst_flags = burst_df["burst_flag"].value_counts()
    n_healthy  = burst_flags.get("HEALTHY_SPREAD", 0)
    n_burst    = burst_flags.get("BURST_DEPENDENT", 0) + \
                 burst_flags.get("SINGLE_BURST_DEPENDENT", 0)

    if thin_kill:
        exec_dec = "FAIL_AFTER_THINNING"
        proceed  = False
    elif len(thin_pass) >= 2 and n_healthy >= n_burst:
        exec_dec = "HEALTHY_SPREAD"
        proceed  = True
    elif thin_pass and n_burst > n_healthy:
        exec_dec = "BURST_DEPENDENT_DEFER"
        proceed  = False
    elif not thin_pass:
        exec_dec = "OVERLAP_DEPENDENT_DEFER"
        proceed  = False
    else:
        exec_dec = "BURST_DEPENDENT_DEFER"
        proceed  = False

    top2_sum = daily.nlargest(2, "net_h10")["net_h10"].sum()
    top2_pct = top2_sum / total_net_h10

    # Jun 23 and Jun 8 analysis
    jun23 = daily[daily["rithmic_date_str"] == "2026-06-23"].iloc[0] \
            if "2026-06-23" in daily["rithmic_date_str"].values else None
    jun08 = daily[daily["rithmic_date_str"] == "2026-06-08"].iloc[0] \
            if "2026-06-08" in daily["rithmic_date_str"].values else None
    jun28 = daily[daily["rithmic_date_str"] == "2026-06-28"].iloc[0] \
            if "2026-06-28" in daily["rithmic_date_str"].values else None

    # Jun 28 failure stats
    fail_h40_net = float(fail_df["net_return_h40"].sum()) if len(fail_df) > 0 else np.nan
    fail_wr_h40  = float((fail_df["net_return_h40"] > 0).mean()) if len(fail_df) > 0 else np.nan
    fail_conf_wrong = int(
        ((fail_df["prob_max"] >= 0.80) & (fail_df["net_return_h10"] < 0)).sum()
    ) if len(fail_df) > 0 else 0

    report = [
        "# Audit 5C — Timestamp Dependency / PnL Concentration Audit",
        f"**Generated**: {now_utc}",
        f"**Candidate**: Version B, W=20, threshold=0.65, H10 primary, H40 secondary",
        "**SHADOW / RESEARCH ONLY — no execution, no broker, no order placement**",
        "",
        "---",
        "",
        "## 1. Executive Decision",
        "",
        f"### **{exec_dec}**",
        "",
        "| Criterion | Value |",
        "|-----------|-------|",
        f"| Total OOS net H10 | {total_net_h10:+,.0f} ticks |",
        f"| Total OOS net H40 | {total_net_h40:+,.0f} ticks |",
        f"| Top-2 days % of total H10 | {top2_pct:.1%} |",
        f"| Thinning rules with PASS | {len(thin_pass)}/8 |",
        f"| Days: HEALTHY_SPREAD | {n_healthy}/{n_days} |",
        f"| Days: BURST_DEPENDENT | {n_burst}/{n_days} |",
        f"| Proceed to Audit 6? | {'**YES**' if proceed else '**NO — resolve concentration first**'} |",
        "",
        "---",
        "",
        "## 2. Daily Concentration",
        "",
        "| Date | N signals | Net H10 | % of Total | Win rate |",
        "|------|-----------|---------|-----------|---------|",
    ]
    for _, r in daily.sort_values("net_h10", ascending=False).iterrows():
        report.append(
            f"| {r['rithmic_date_str']} | {r['n_signals']:,} | "
            f"{r['net_h10']:+,.0f} | {r['pct_total_h10']:.1%} | "
            f"{r['win_rate_h10']:.3f} |"
        )

    report += [
        "",
        f"**Top-2 days (Jun-23 + Jun-08) combined**: {top2_sum:+,.0f}t = "
        f"{top2_pct:.1%} of total H10.",
        "",
        "---",
        "",
        "## 3. Jun-23 and Jun-08: Broad Edge or Burst Only?",
        "",
    ]

    for label, row, date in [
        ("Jun-23", jun23, "2026-06-23"),
        ("Jun-08", jun08, "2026-06-08"),
    ]:
        if row is not None:
            burst_row = burst_df[burst_df["date"] == date]
            top3_15 = float(burst_row["top3_15min_pct_day"].iloc[0]) \
                      if len(burst_row) > 0 else np.nan
            flag = str(burst_row["burst_flag"].iloc[0]) if len(burst_row) > 0 else "?"
            report += [
                f"### {label}",
                f"- Signals: {int(row['n_signals']):,}  Net H10: {row['net_h10']:+,.0f}t  "
                f"Win rate: {row['win_rate_h10']:.3f}",
                f"- Top-3 15-min buckets % of day: {top3_15:.1%}",
                f"- Burst flag: **{flag}**",
                f"- Verdict: {'Burst-concentrated — a few 15-min windows drove the day' if flag != 'HEALTHY_SPREAD' else 'Broadly distributed — not burst-dependent'}",
                "",
            ]

    report += [
        "---",
        "",
        "## 4. Jun-28: One Bad Burst or Whole-Day Failure?",
        "",
    ]
    if jun28 is not None:
        burst_row = burst_df[burst_df["date"] == "2026-06-28"]
        flag28 = str(burst_row["burst_flag"].iloc[0]) if len(burst_row) > 0 else "?"
        report += [
            f"- Signals: {int(jun28['n_signals']):,}  Net H10: {jun28['net_h10']:+,.0f}t  "
            f"Win rate: {jun28['win_rate_h10']:.3f}",
            f"- H40 net on Jun-28: {fail_h40_net:+,.0f}t  H40 win rate: {fail_wr_h40:.3f}",
            f"- Confidently wrong signals (prob≥0.80, net<0): {fail_conf_wrong:,}",
            f"- Burst flag: **{flag28}**",
            f"- H40 confirms failure: {'YES — both H10 and H40 negative' if fail_h40_net < 0 else 'NO — H40 recovered'}",
            "",
            "**Diagnosis**: See `audit_5c_jun28_failure_clusters.csv` for cluster details.",
        ]

    report += [
        "",
        "---",
        "",
        "## 5. Signal Thinning Results",
        "",
        "| Rule | N trades | Avg net H10 | Total H10 | Win rate | "
        "Best-day% | Top-2% | Decision |",
        "|------|----------|------------|---------|---------|---------|------|---------|",
    ]
    for _, r in thin_df.iterrows():
        report.append(
            f"| {r['rule']} | {r['n_trades']:,} | {r['avg_net_h10']:+.2f} | "
            f"{r['total_net_h10']:+,.0f} | {r['win_rate_h10']:.3f} | "
            f"{r.get('best_day_pct_of_total', np.nan):.1%} | "
            f"{r.get('top2_days_pct_of_total', np.nan):.1%} | "
            f"{r['decision']} |"
        )

    report += [
        "",
        "---",
        "",
        "## 6. Burst Concentration Flags",
        "",
        "| Date | Day Net H10 | Best-5min% | Top3-15min% | Flag |",
        "|------|------------|-----------|------------|------|",
    ]
    for _, r in burst_df.iterrows():
        report.append(
            f"| {r['date']} | {r['day_net_h10']:+,.0f} | "
            f"{r.get('best_5min_pct_day', np.nan):.1%} | "
            f"{r.get('top3_15min_pct_day', np.nan):.1%} | {r.get('burst_flag','?')} |"
        )

    report += [
        "",
        "---",
        "",
        "## 7. Required Next Actions",
        "",
        f"- **Overall decision**: {exec_dec}",
        f"- {'Proceed to Audit 6 (execution/fill simulation).' if proceed else 'Do NOT proceed to Audit 6 until concentration is resolved.'}",
        "",
        "**Specific actions:**",
        "",
    ]
    if exec_dec == "HEALTHY_SPREAD":
        report += [
            "1. Edge is broadly distributed. Proceed to Audit 6 execution simulation.",
            "2. Flag Jun-28 for regime-failure analysis before live deployment.",
            "3. Confirm that thinned signal sets (D/E/F) retain positive expectancy.",
        ]
    elif exec_dec == "BURST_DEPENDENT_DEFER":
        report += [
            "1. Identify the specific 15-min windows driving Jun-23 and Jun-08 gains.",
            "2. Determine whether those windows are model-driven or event-driven.",
            "3. Test whether edge survives removing the top-3 burst windows per day.",
            "4. If edge survives → proceed to Audit 6 with thinning rules E or G.",
            "5. If edge does not survive → KILL candidate.",
        ]
    else:
        report += [
            "1. Edge collapses under overlap thinning. Do not proceed.",
            "2. Investigate alternative horizons (H5, H20) or signal selection.",
        ]

    report += [
        "",
        "---",
        "",
        "## Output Files",
        "",
        "| File | Description |",
        "|------|-------------|",
        "| `audit_5c_daily_concentration.csv` | Daily P&L breakdown |",
        "| `audit_5c_top_raw_events_key_days.csv` | Top raw events on key days |",
        "| `audit_5c_top_1min_buckets_key_days.csv` | 1-min buckets on key days |",
        "| `audit_5c_top_5min_buckets_key_days.csv` | 5-min buckets on key days |",
        "| `audit_5c_top_15min_buckets_key_days.csv` | 15-min buckets on key days |",
        "| `audit_5c_top_60min_buckets_key_days.csv` | 60-min buckets on key days |",
        "| `audit_5c_burst_concentration_by_day.csv` | Burst flags per day |",
        "| `audit_5c_thinning_results.csv` | 8-rule thinning results |",
        "| `audit_5c_big_vs_normal_days.csv` | Big/bad/normal day comparison |",
        "| `audit_5c_jun28_failure_clusters.csv` | Jun-28 failure cluster detail |",
        "| `AUDIT_5C_TIMESTAMP_DEPENDENCY_REPORT.md` | This report |",
        "",
        "---",
        "",
        "## Final Status",
        "```",
        f"AUDIT_5C_COMPLETE:             true",
        f"EXECUTIVE_DECISION:            {exec_dec}",
        f"PROCEED_TO_AUDIT_6:            {proceed}",
        "PRODUCTION_FILES_MODIFIED:     false",
        "TRADING_ENABLED:               false",
        "```",
    ]

    with open(OUT_DIR / "AUDIT_5C_TIMESTAMP_DEPENDENCY_REPORT.md", "w") as f:
        f.write("\n".join(report))
    print(f"  Saved: AUDIT_5C_TIMESTAMP_DEPENDENCY_REPORT.md")
    print(f"  Output dir: {OUT_DIR}")


if __name__ == "__main__":
    main()
