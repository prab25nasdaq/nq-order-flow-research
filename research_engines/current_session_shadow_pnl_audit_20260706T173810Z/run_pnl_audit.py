"""
Current Session Shadow PnL Audit — Model / Bar-Level / Dashboard Signals
SHADOW / RESEARCH ONLY — NO EXECUTION / NO BROKER / NO PAPER TRADING
Generated: 2026-07-06
"""

import sys, os, warnings, glob, json, time
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
from pathlib import Path

t0 = time.time()
EPS = 1e-8

OUT = Path("/home/prabh/OFI_Production/research_engines/current_session_shadow_pnl_audit_20260706T173810Z")
OUT.mkdir(parents=True, exist_ok=True)

print("=" * 70)
print("CURRENT SESSION SHADOW PnL AUDIT")
print("SHADOW / RESEARCH ONLY — NO EXECUTION — NO BROKER — NO PAPER TRADING")
print("=" * 70)

# ─────────────────────────────────────────────────────────────────────────────
# PART A — CONTRACT SETTINGS
# ─────────────────────────────────────────────────────────────────────────────
NQ_POINT_VALUE   = 20.0
NQ_TICK_SIZE     = 0.25
NQ_TICK_VALUE    = 5.0
MNQ_POINT_VALUE  = 2.0
MNQ_TICK_VALUE   = 0.50
CONTRACT_COUNTS  = [1, 2, 5, 10]
SLIPPAGE_TICKS   = [0, 1, 2, 4]   # round-trip slippage ticks
COMMISSION_RT    = 0.0             # per round turn USD

CONFIDENCE_THRESHOLDS = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80]
DEFAULT_THRESHOLD = 0.65

HORIZONS = [10, 20, 40]
MAIN_HORIZON = 40

print(f"\n[SETTINGS] NQ: ${NQ_POINT_VALUE}/pt  ${NQ_TICK_VALUE}/tick  tick={NQ_TICK_SIZE}")
print(f"  MNQ: ${MNQ_POINT_VALUE}/pt  ${MNQ_TICK_VALUE}/tick")
print(f"  Confidence threshold: {DEFAULT_THRESHOLD}  Horizons: {HORIZONS}")

# ─────────────────────────────────────────────────────────────────────────────
# PART B — LOAD DATA
# ─────────────────────────────────────────────────────────────────────────────
print("\n[DATA] Loading inputs...")

# NQU6 price series
nqu6 = pd.read_json("/home/prabh/OFI_Live_Features/master_NQU6_shadow.ndjsonl", lines=True)
nqu6 = nqu6.sort_values("bar_end_ts_ns").reset_index(drop=True)
nqu6_idx = nqu6.set_index("bar_end_ts_ns")
print(f"  NQU6: {len(nqu6):,} bars  latest bar_index={nqu6['bar_index'].iloc[-1]}")

# Predictions
pred_all = pd.read_csv("/home/prabh/OFI_Production/inference_scripts/"
                       "level_reaction_continuous_nq_shadow/outputs/"
                       "latest_continuous_nq_predictions.csv")
print(f"  Predictions: {len(pred_all):,} rows  dates: {sorted(pred_all['rithmic_date_str'].unique())[-3:]}")

# Load inference summary
with open("/home/prabh/OFI_Production/inference_scripts/"
          "level_reaction_continuous_nq_shadow/outputs/"
          "latest_continuous_nq_inference_summary.json") as f:
    inf_summ = json.load(f)

# OFI Level Decision
ld = pd.read_csv("/home/prabh/OFI_Production/ofi_level_decision/outputs/"
                 "ofi_level_decision_latest_table.csv")
print(f"  OFI Level Decision: {len(ld):,} rows  bar_idx {ld['bar_idx'].min()}–{ld['bar_idx'].max()}")

# Model Feature Master (for VPIN/toxicity/book-switch context)
mfm = pd.read_parquet("/home/prabh/OFI_Production/model_feature_master/data/"
                      "model_feature_master_shadow.parquet")
print(f"  MFM: {mfm.shape}")

# Atlas v2 mechanism labels if available
atlas_dir = sorted(glob.glob(
    "/home/prabh/OFI_Production/research_engines/"
    "high_extreme_travel_mechanism_atlas_v2_*"))
atlas_labels = None
if atlas_dir:
    mlf = Path(atlas_dir[-1]) / "high_extreme_travel_mechanism_labels.parquet"
    if mlf.exists():
        atlas_labels = pd.read_parquet(mlf)
        print(f"  Atlas v2 labels: {len(atlas_labels)} bars")

# ─────────────────────────────────────────────────────────────────────────────
# PART C — DEFINE CURRENT SESSION
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART C] Defining current session...")

# Current session = latest rithmic_date_str in predictions
session_date = int(pred_all["rithmic_date_str"].max())
pred = pred_all[pred_all["rithmic_date_str"] == session_date].copy()

# Find session bars in NQU6 master
# Join via bar_end_ts_ns to get global bar_index
nqu6_ts_map = nqu6.set_index("bar_end_ts_ns")[["bar_index","day","px_open","px_high","px_low","px_close"]].copy()
pred = pred.merge(
    nqu6_ts_map.reset_index(),
    on="bar_end_ts_ns", how="left"
)

# Session start/end
session_bars_global = sorted(pred["bar_index"].dropna().unique().astype(int))
session_start_bar = int(min(session_bars_global)) if session_bars_global else None
session_end_bar   = int(max(session_bars_global)) if session_bars_global else None

# Latest master bar (global)
master_latest_bar   = int(nqu6["bar_index"].max())
master_latest_close = float(nqu6["px_close"].iloc[-1])
master_latest_ts    = str(inf_summ.get("master_last_timestamp_utc","unknown"))
run_ts              = str(inf_summ.get("run_utc","unknown"))

session_def = {
    "session_date":             session_date,
    "session_label":            f"20260705 (NQU6 session, calendar date 2026-07-06 UTC)",
    "master_latest_bar_index":  master_latest_bar,
    "master_latest_close":      master_latest_close,
    "master_latest_timestamp":  master_latest_ts,
    "inference_run_utc":        run_ts,
    "shadow_only":              True,
    "trading_enabled":          False,
    "roll_quality_flag":        inf_summ.get("roll_quality_flag","?"),
    "session_start_bar":        session_start_bar,
    "session_end_bar":          session_end_bar,
    "session_bars_with_signal": len(session_bars_global),
    "total_event_rows":         len(pred),
    "unique_scored_bars":       pred["bar_idx_in_day"].nunique(),
    "unique_bars_global":       pred["bar_index"].nunique(),
    "gate_status_counts":       pred["training_gate_status"].value_counts().to_dict(),
    "direction_counts":         pred["direction"].value_counts().to_dict(),
    "level_type_counts":        pred["level_type"].value_counts().to_dict(),
    "reaction_type_counts":     pred["reaction_type"].value_counts().to_dict(),
}
with open(OUT / "current_session_definition.json", "w") as f:
    json.dump(session_def, f, indent=2, default=str)

print(f"  Session date: {session_date}")
print(f"  Event rows: {len(pred)}  Unique bars (day-idx): {pred['bar_idx_in_day'].nunique()}")
print(f"  Global bar range: {session_start_bar}–{session_end_bar}")
print(f"  Master latest: bar {master_latest_bar}  close={master_latest_close}  ts={master_latest_ts}")
print(f"  Roll quality: {inf_summ.get('roll_quality_flag','?')}")
print(f"  Gate status: {pred['training_gate_status'].value_counts().to_dict()}")
print(f"  Direction dist: {pred['direction'].value_counts().to_dict()}")

# Data coverage
cov_rows = []
for s, g in pred.groupby("training_gate_status"):
    cov_rows.append({"gate_status":s,"event_rows":len(g),
                     "unique_bars":g["bar_idx_in_day"].nunique(),
                     "pct_long":round((g["direction"]=="LONG").mean()*100,1),
                     "pct_short":round((g["direction"]=="SHORT").mean()*100,1),
                     "pct_flat":round((g["direction"]=="FLAT").mean()*100,1),
                     "mean_confidence":round(g["confidence"].mean(),4)})
pd.DataFrame(cov_rows).to_csv(OUT / "current_session_data_coverage.csv", index=False)

# ─────────────────────────────────────────────────────────────────────────────
# PART D — BAR-LEVEL DECISION LEDGER
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART D] Building bar-level decision ledger...")

# For each bar_idx_in_day: aggregate across all event rows
def build_bar_ledger(pred_df, threshold=DEFAULT_THRESHOLD):
    rows = []
    for bar_day_idx, grp in pred_df.groupby("bar_idx_in_day"):
        # Core price info
        ts_ns   = grp["bar_end_ts_ns"].iloc[0]
        ts_utc  = grp["timestamp_utc"].iloc[0]
        bi_glob = grp["bar_index"].iloc[0] if "bar_index" in grp.columns and pd.notna(grp["bar_index"].iloc[0]) else None
        close   = float(grp["continuous_close"].iloc[0])
        raw_c   = float(grp["raw_close"].iloc[0])
        px_o    = float(grp["px_open"].iloc[0]) if "px_open" in grp.columns and pd.notna(grp["px_open"].iloc[0]) else close
        px_h    = float(grp["px_high"].iloc[0]) if "px_high" in grp.columns and pd.notna(grp["px_high"].iloc[0]) else close
        px_l    = float(grp["px_low"].iloc[0]) if "px_low" in grp.columns and pd.notna(grp["px_low"].iloc[0]) else close
        sess    = grp["session"].iloc[0]

        # Aggregate signal
        n_rows    = len(grp)
        gate_use  = grp[grp["training_gate_status"]=="PRIMARY_USE"]
        gate_sw   = grp[grp["training_gate_status"]=="SECONDARY_WATCH"]
        gate_blk  = grp[grp["training_gate_status"]=="BLOCKED_NEGATIVE"]

        # Use PRIMARY_USE rows for signal if available, else SECONDARY_WATCH
        sig_rows = gate_use if len(gate_use) > 0 else gate_sw if len(gate_sw) > 0 else grp
        n_blocked = len(gate_blk)
        n_primary = len(gate_use)
        n_watch   = len(gate_sw)

        avg_p_long  = float(sig_rows["p_long"].mean())
        avg_p_short = float(sig_rows["p_short"].mean())
        max_conf    = float(grp["confidence"].max())
        avg_conf    = float(sig_rows["confidence"].mean())
        level_types = list(grp["level_type"].unique())
        rxn_types   = list(grp["reaction_type"].unique())

        # Direction agreement
        long_votes  = (sig_rows["direction"]=="LONG").sum()
        short_votes = (sig_rows["direction"]=="SHORT").sum()
        flat_votes  = (sig_rows["direction"]=="FLAT").sum()
        total_votes = len(sig_rows)
        dom_dir = "LONG" if long_votes >= short_votes and long_votes >= flat_votes else \
                  "SHORT" if short_votes > long_votes and short_votes >= flat_votes else "FLAT"
        agree_pct = max(long_votes, short_votes, flat_votes) / max(total_votes, 1)

        # Gate status for bar
        if n_blocked > 0 and n_primary == 0 and n_watch == 0:
            gate = "ALL_BLOCKED"
        elif n_blocked > 0:
            gate = "MIXED_GATE"
        elif n_primary > 0:
            gate = "PRIMARY_USE"
        else:
            gate = "SECONDARY_WATCH"

        # Decision rule
        if gate == "ALL_BLOCKED":
            model_dir = "BLOCKED"
        elif avg_p_long >= threshold:
            model_dir = "LONG"
        elif avg_p_short >= threshold:
            model_dir = "SHORT"
        else:
            model_dir = "FLAT"

        rows.append({
            "bar_idx_in_day":       bar_day_idx,
            "bar_index_global":     bi_glob,
            "bar_end_ts_ns":        ts_ns,
            "timestamp_utc":        ts_utc,
            "px_open":              px_o,
            "px_high":              px_h,
            "px_low":               px_l,
            "continuous_close":     close,
            "raw_close":            raw_c,
            "session":              sess,
            "n_event_rows":         n_rows,
            "n_primary_rows":       n_primary,
            "n_secondary_rows":     n_watch,
            "n_blocked_rows":       n_blocked,
            "level_types":          "|".join(level_types),
            "reaction_types":       "|".join(rxn_types),
            "avg_p_long":           round(avg_p_long, 4),
            "avg_p_short":          round(avg_p_short, 4),
            "max_confidence":       round(max_conf, 4),
            "avg_confidence":       round(avg_conf, 4),
            "direction_votes_long": long_votes,
            "direction_votes_short":short_votes,
            "direction_votes_flat": flat_votes,
            "dominant_signal_dir":  dom_dir,
            "direction_agreement_pct": round(agree_pct*100,1),
            "gate_status":          gate,
            "model_direction":      model_dir,
            "threshold_used":       threshold,
        })
    return pd.DataFrame(rows).sort_values("bar_idx_in_day").reset_index(drop=True)

ledger = build_bar_ledger(pred)
ledger.to_csv(OUT / "bar_level_decision_ledger.csv", index=False)

n_long_decisions = (ledger["model_direction"]=="LONG").sum()
n_short_decisions= (ledger["model_direction"]=="SHORT").sum()
n_flat_decisions = (ledger["model_direction"]=="FLAT").sum()
n_blocked_dec    = (ledger["model_direction"]=="BLOCKED").sum()

print(f"  Bar decisions: {len(ledger)} total")
print(f"    LONG={n_long_decisions}  SHORT={n_short_decisions}  FLAT={n_flat_decisions}  BLOCKED={n_blocked_dec}")
print(f"  Written: bar_level_decision_ledger.csv")

# ─────────────────────────────────────────────────────────────────────────────
# HELPER: forward price lookup from NQU6 master
# ─────────────────────────────────────────────────────────────────────────────
# Build global-bar-index-keyed price arrays for fast lookups
nqu6_sorted = nqu6.sort_values("bar_index").reset_index(drop=True)
max_bar_idx  = int(nqu6_sorted["bar_index"].max())

bar_close_arr = np.full(max_bar_idx + 200, np.nan)
bar_high_arr  = np.full(max_bar_idx + 200, np.nan)
bar_low_arr   = np.full(max_bar_idx + 200, np.nan)
for _, r in nqu6_sorted.iterrows():
    bi = int(r["bar_index"])
    bar_close_arr[bi] = r["px_close"]
    bar_high_arr[bi]  = r["px_high"]
    bar_low_arr[bi]   = r["px_low"]

def get_exit_close(entry_bar, horizon):
    exit_bar = entry_bar + horizon
    if exit_bar >= len(bar_close_arr): return np.nan, exit_bar
    return bar_close_arr[exit_bar], exit_bar

def get_mfe_mae_long(entry_bar, horizon, entry_price):
    bars = range(entry_bar+1, min(entry_bar+horizon+1, len(bar_high_arr)))
    highs = [bar_high_arr[b] for b in bars if not np.isnan(bar_high_arr[b])]
    lows  = [bar_low_arr[b]  for b in bars if not np.isnan(bar_low_arr[b])]
    if not highs: return np.nan, np.nan
    mfe = max(highs) - entry_price   # max favorable
    mae = entry_price - min(lows)    # max adverse
    return mfe, mae

def get_mfe_mae_short(entry_bar, horizon, entry_price):
    bars = range(entry_bar+1, min(entry_bar+horizon+1, len(bar_high_arr)))
    highs = [bar_high_arr[b] for b in bars if not np.isnan(bar_high_arr[b])]
    lows  = [bar_low_arr[b]  for b in bars if not np.isnan(bar_low_arr[b])]
    if not highs: return np.nan, np.nan
    mfe = entry_price - min(lows)   # max favorable for short
    mae = max(highs) - entry_price  # max adverse for short
    return mfe, mae

def trade_usd(pts, n_contracts, point_value, slip_ticks, tick_value, commission):
    slip_pts = slip_ticks * NQ_TICK_SIZE
    net_pts  = pts - slip_pts
    gross    = pts * n_contracts * point_value
    net      = net_pts * n_contracts * point_value - commission * n_contracts
    return gross, net, net_pts

# ─────────────────────────────────────────────────────────────────────────────
# PART E — PnL SIMULATION VARIANTS
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART E] Running PnL simulation variants...")

def run_fixed_horizon(ledger_df, horizon, threshold=DEFAULT_THRESHOLD, slip_ticks=0,
                      n_contracts=1, label=""):
    trades = []
    active_longs = (ledger_df["model_direction"]=="LONG") & \
                   (ledger_df["gate_status"] != "ALL_BLOCKED")
    active_shorts= (ledger_df["model_direction"]=="SHORT") & \
                   (ledger_df["gate_status"] != "ALL_BLOCKED")

    for _, r in ledger_df.iterrows():
        mdir = r["model_direction"]
        if mdir not in ("LONG","SHORT"):
            continue
        bi = r["bar_index_global"]
        if pd.isna(bi): continue
        bi = int(bi)
        entry_price = float(r["raw_close"])   # raw contract price — same series as master px_close/px_high/px_low
        exit_price, exit_bar = get_exit_close(bi, horizon)
        if np.isnan(exit_price): continue  # not enough future bars

        if mdir == "LONG":
            raw_pts = exit_price - entry_price
            mfe, mae = get_mfe_mae_long(bi, horizon, entry_price)
        else:
            raw_pts = entry_price - exit_price
            mfe, mae = get_mfe_mae_short(bi, horizon, entry_price)

        slip_pts = slip_ticks * NQ_TICK_SIZE
        net_pts  = raw_pts - slip_pts
        gross_nq = raw_pts * n_contracts * NQ_POINT_VALUE
        net_nq   = net_pts * n_contracts * NQ_POINT_VALUE - COMMISSION_RT * n_contracts
        gross_mnq= raw_pts * n_contracts * MNQ_POINT_VALUE
        net_mnq  = net_pts * n_contracts * MNQ_POINT_VALUE - COMMISSION_RT * n_contracts

        trades.append({
            "variant": label or f"H{horizon}",
            "bar_idx_in_day":   r["bar_idx_in_day"],
            "bar_index_global": bi,
            "exit_bar":         exit_bar,
            "entry_time":       r["timestamp_utc"],
            "side":             mdir,
            "entry_price":      entry_price,
            "exit_price":       round(exit_price,2),
            "raw_points":       round(raw_pts,2),
            "net_points":       round(net_pts,2),
            "MFE":              round(mfe,2) if mfe is not None and not np.isnan(mfe) else None,
            "MAE":              round(mae,2) if mae is not None and not np.isnan(mae) else None,
            "hold_bars":        horizon,
            "slip_ticks":       slip_ticks,
            "n_contracts":      n_contracts,
            "confidence":       r["max_confidence"],
            "gate_status":      r["gate_status"],
            "level_types":      r["level_types"],
            "reaction_types":   r["reaction_types"],
            "session":          r["session"],
            "gross_usd_NQ":     round(gross_nq,2),
            "net_usd_NQ":       round(net_nq,2),
            "gross_usd_MNQ":    round(gross_mnq,2),
            "net_usd_MNQ":      round(net_mnq,2),
            "outcome":          "WIN" if net_pts > 0 else "LOSS" if net_pts < 0 else "FLAT",
        })
    return pd.DataFrame(trades)

def run_one_at_a_time(ledger_df, horizon, slip_ticks=0, n_contracts=1, label="V5"):
    trades = []
    open_until = -1
    for _, r in ledger_df.iterrows():
        mdir = r["model_direction"]
        if mdir not in ("LONG","SHORT"): continue
        bi = r["bar_index_global"]
        if pd.isna(bi): continue
        bi = int(bi)
        if bi <= open_until: continue  # position still open
        entry_price = float(r["raw_close"])   # raw contract price
        exit_price, exit_bar = get_exit_close(bi, horizon)
        if np.isnan(exit_price): continue
        open_until = exit_bar
        raw_pts = (exit_price - entry_price) if mdir=="LONG" else (entry_price - exit_price)
        net_pts = raw_pts - slip_ticks * NQ_TICK_SIZE
        mfe, mae = (get_mfe_mae_long(bi, horizon, entry_price) if mdir=="LONG"
                    else get_mfe_mae_short(bi, horizon, entry_price))
        trades.append({
            "variant":label, "bar_idx_in_day":r["bar_idx_in_day"],
            "bar_index_global":bi, "exit_bar":exit_bar,
            "entry_time":r["timestamp_utc"], "side":mdir,
            "entry_price":entry_price, "exit_price":round(exit_price,2),
            "raw_points":round(raw_pts,2), "net_points":round(net_pts,2),
            "MFE":round(mfe,2) if mfe and not np.isnan(mfe) else None,
            "MAE":round(mae,2) if mae and not np.isnan(mae) else None,
            "hold_bars":horizon, "slip_ticks":slip_ticks,
            "n_contracts":n_contracts, "confidence":r["max_confidence"],
            "gate_status":r["gate_status"], "level_types":r["level_types"],
            "reaction_types":r["reaction_types"], "session":r["session"],
            "gross_usd_NQ":round(raw_pts*n_contracts*NQ_POINT_VALUE,2),
            "net_usd_NQ":round(net_pts*n_contracts*NQ_POINT_VALUE,2),
            "gross_usd_MNQ":round(raw_pts*n_contracts*MNQ_POINT_VALUE,2),
            "net_usd_MNQ":round(net_pts*n_contracts*MNQ_POINT_VALUE,2),
            "outcome":"WIN" if net_pts>0 else "LOSS" if net_pts<0 else "FLAT",
        })
    return pd.DataFrame(trades)

def run_flip_model(ledger_df, slip_ticks=0, n_contracts=1, label="V6"):
    trades = []
    open_pos = None  # (side, entry_bar, entry_price)
    for _, r in ledger_df.iterrows():
        mdir = r["model_direction"]
        bi   = r["bar_index_global"]
        if pd.isna(bi): continue
        bi   = int(bi)
        cprice = float(r["raw_close"])   # raw contract price
        # Close if flip or FLAT
        if open_pos is not None:
            prev_side, entry_bar, entry_price = open_pos
            if mdir != prev_side:
                exit_price = cprice
                raw_pts = (exit_price - entry_price) if prev_side=="LONG" else (entry_price - exit_price)
                net_pts = raw_pts - slip_ticks * NQ_TICK_SIZE
                hold = bi - entry_bar
                mfe, mae = (get_mfe_mae_long(entry_bar, hold, entry_price) if prev_side=="LONG"
                            else get_mfe_mae_short(entry_bar, hold, entry_price))
                trades.append({
                    "variant":label, "bar_idx_in_day":r["bar_idx_in_day"],
                    "bar_index_global":entry_bar, "exit_bar":bi,
                    "entry_time":r["timestamp_utc"], "side":prev_side,
                    "entry_price":entry_price, "exit_price":round(exit_price,2),
                    "raw_points":round(raw_pts,2), "net_points":round(net_pts,2),
                    "MFE":round(mfe,2) if mfe and not np.isnan(mfe) else None,
                    "MAE":round(mae,2) if mae and not np.isnan(mae) else None,
                    "hold_bars":hold, "slip_ticks":slip_ticks,
                    "n_contracts":n_contracts, "confidence":r["max_confidence"],
                    "gate_status":r["gate_status"], "level_types":r["level_types"],
                    "reaction_types":r["reaction_types"], "session":r["session"],
                    "gross_usd_NQ":round(raw_pts*n_contracts*NQ_POINT_VALUE,2),
                    "net_usd_NQ":round(net_pts*n_contracts*NQ_POINT_VALUE,2),
                    "gross_usd_MNQ":round(raw_pts*n_contracts*MNQ_POINT_VALUE,2),
                    "net_usd_MNQ":round(net_pts*n_contracts*MNQ_POINT_VALUE,2),
                    "outcome":"WIN" if net_pts>0 else "LOSS" if net_pts<0 else "FLAT",
                })
                open_pos = None
        if mdir in ("LONG","SHORT"):
            open_pos = (mdir, bi, cprice)
    return pd.DataFrame(trades)

def run_conf_scaled(ledger_df, horizon, slip_ticks=0, label="V7"):
    def conf_scale(c):
        if c >= 0.90: return 1.00
        if c >= 0.80: return 0.75
        if c >= 0.70: return 0.50
        return 0.25
    trades = []
    for _, r in ledger_df.iterrows():
        mdir = r["model_direction"]
        if mdir not in ("LONG","SHORT"): continue
        bi = r["bar_index_global"]
        if pd.isna(bi): continue
        bi = int(bi)
        scale = conf_scale(float(r["max_confidence"]))
        entry_price = float(r["raw_close"])   # raw contract price
        exit_price, exit_bar = get_exit_close(bi, horizon)
        if np.isnan(exit_price): continue
        raw_pts = (exit_price - entry_price) if mdir=="LONG" else (entry_price - exit_price)
        net_pts = raw_pts - slip_ticks * NQ_TICK_SIZE
        trades.append({
            "variant":label, "bar_idx_in_day":r["bar_idx_in_day"],
            "bar_index_global":bi, "exit_bar":exit_bar,
            "entry_time":r["timestamp_utc"], "side":mdir,
            "entry_price":entry_price, "exit_price":round(exit_price,2),
            "raw_points":round(raw_pts,2), "net_points":round(net_pts,2),
            "conf_scale":scale, "scaled_pts":round(raw_pts*scale,2),
            "hold_bars":horizon, "slip_ticks":slip_ticks,
            "confidence":r["max_confidence"],
            "gate_status":r["gate_status"], "level_types":r["level_types"],
            "reaction_types":r["reaction_types"], "session":r["session"],
            "gross_usd_NQ":round(raw_pts*scale*NQ_POINT_VALUE,2),
            "net_usd_NQ":round(net_pts*scale*NQ_POINT_VALUE,2),
            "gross_usd_MNQ":round(raw_pts*scale*MNQ_POINT_VALUE,2),
            "net_usd_MNQ":round(net_pts*scale*MNQ_POINT_VALUE,2),
            "outcome":"WIN" if net_pts>0 else "LOSS" if net_pts<0 else "FLAT",
        })
    return pd.DataFrame(trades)

# Run all variants
v1 = run_fixed_horizon(ledger, 40, label="V1_H40")
v2 = run_fixed_horizon(ledger, 20, label="V2_H20")
v3 = run_fixed_horizon(ledger, 10, label="V3_H10")
v4 = run_fixed_horizon(ledger,  1, label="V4_NEXT_BAR")
v5 = run_one_at_a_time(ledger, 40, label="V5_ONE_AT_A_TIME_H40")
v6 = run_flip_model(ledger, label="V6_FLIP")
v7 = run_conf_scaled(ledger, 40, label="V7_CONF_SCALED_H40")

all_trades = pd.concat([v1,v2,v3,v4,v5,v6,v7], ignore_index=True)
all_trades.to_csv(OUT / "shadow_trade_ledger_all_variants.csv", index=False)
v1.to_csv(OUT / "shadow_trade_ledger_main_bar_level_h40.csv", index=False)

def summarize_trades(df, label):
    if len(df) == 0:
        return {"variant":label,"n_trades":0,"total_pts":0,"hit_rate":0,
                "avg_win":0,"avg_loss":0,"gross_usd_NQ":0,"net_usd_NQ":0}
    wins = df[df["net_points"]>0]
    loss = df[df["net_points"]<0]
    # Max drawdown (cumulative)
    cum_pts = df["net_points"].cumsum().values
    max_dd   = float(np.min(cum_pts - np.maximum.accumulate(cum_pts)))
    return {
        "variant":   label,
        "n_trades":  len(df),
        "n_long":    (df["side"]=="LONG").sum(),
        "n_short":   (df["side"]=="SHORT").sum(),
        "total_raw_pts": round(df["raw_points"].sum(),2),
        "total_net_pts": round(df["net_points"].sum(),2),
        "hit_rate":      round(len(wins)/len(df)*100,1),
        "avg_win_pts":   round(wins["net_points"].mean(),2) if len(wins)>0 else 0,
        "avg_loss_pts":  round(loss["net_points"].mean(),2) if len(loss)>0 else 0,
        "max_win_pts":   round(df["net_points"].max(),2),
        "max_loss_pts":  round(df["net_points"].min(),2),
        "avg_MFE":       round(df["MFE"].mean(),2) if "MFE" in df and df["MFE"].notna().any() else None,
        "avg_MAE":       round(df["MAE"].mean(),2) if "MAE" in df and df["MAE"].notna().any() else None,
        "profit_factor": round(wins["net_points"].sum()/max(abs(loss["net_points"].sum()),EPS),3),
        "max_drawdown_pts": round(max_dd,2),
        "gross_usd_NQ":  round(df["gross_usd_NQ"].sum(),2),
        "net_usd_NQ":    round(df["net_usd_NQ"].sum(),2),
        "gross_usd_MNQ": round(df["gross_usd_MNQ"].sum(),2),
        "net_usd_MNQ":   round(df["net_usd_MNQ"].sum(),2),
    }

variant_summ = pd.DataFrame([
    summarize_trades(v1,"V1_H40"),
    summarize_trades(v2,"V2_H20"),
    summarize_trades(v3,"V3_H10"),
    summarize_trades(v4,"V4_NEXT_BAR"),
    summarize_trades(v5,"V5_ONE_AT_A_TIME_H40"),
    summarize_trades(v6,"V6_FLIP"),
    summarize_trades(v7,"V7_CONF_SCALED_H40"),
])
variant_summ.to_csv(OUT / "shadow_pnl_variant_summary.csv", index=False)

print(f"\n  Variant results (1 NQ, zero slippage):")
for _, r in variant_summ.iterrows():
    print(f"    {r['variant']:30s}: {r['n_trades']:3d} trades  "
          f"pts={r['total_net_pts']:+7.2f}  hit={r['hit_rate']}%  "
          f"NQ=${r['net_usd_NQ']:+8.2f}  DD={r['max_drawdown_pts']:.2f}")

# Slippage sensitivity on main variant
slip_rows = []
for slip in SLIPPAGE_TICKS:
    for nc in CONTRACT_COUNTS:
        t = run_fixed_horizon(ledger, MAIN_HORIZON, slip_ticks=slip, n_contracts=nc)
        s = summarize_trades(t, f"H40_slip{slip}tick_nc{nc}")
        s["slip_ticks"] = slip
        s["n_contracts"] = nc
        slip_rows.append(s)
pd.DataFrame(slip_rows).to_csv(OUT / "shadow_pnl_slippage_sensitivity.csv", index=False)
print(f"\n  Written: shadow_pnl_slippage_sensitivity.csv ({len(slip_rows)} rows)")

# Confidence threshold sensitivity
thresh_rows = []
for thr in CONFIDENCE_THRESHOLDS:
    l_t = build_bar_ledger(pred, threshold=thr)
    t   = run_fixed_horizon(l_t, MAIN_HORIZON, threshold=thr, label=f"H40_thr{thr}")
    s   = summarize_trades(t, f"H40_thr{thr}")
    s["threshold"] = thr
    thresh_rows.append(s)
pd.DataFrame(thresh_rows).to_csv(OUT / "shadow_pnl_threshold_sensitivity.csv", index=False)
print(f"  Written: shadow_pnl_threshold_sensitivity.csv ({len(thresh_rows)} rows)")

# ─────────────────────────────────────────────────────────────────────────────
# PART F — DASHBOARD BLOCKER / FILTER COMPARISON
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART F] Filter / blocker comparison...")

# Join OFI level decision to ledger for filter data
# OFI LD uses global bar_idx; ledger has bar_index_global
ld_join = ld.rename(columns={"bar_idx":"bar_index_global"}).copy()
ld_join = ld_join[["bar_index_global","support_consumed","resistance_consumed","dir",
                   "bid_pull_pressure","ask_pull_pressure","row_state"]].copy()

ledger_f = ledger.merge(ld_join, on="bar_index_global", how="left")

# Join MFM for VPIN/toxicity/book-switch context
mfm_sub = mfm[["bar_end_ts_ns","vpin","mlofi_norm","sweep_imbalance_norm",
                "dash_toxicity","buy_ratio","sell_ratio"]].drop_duplicates("bar_end_ts_ns")
ledger_f = ledger_f.merge(mfm_sub, on="bar_end_ts_ns", how="left")

def apply_filter(ledger_df, filter_fn, label):
    filtered = ledger_df.copy()
    filtered = filtered[filter_fn(filtered)]
    trades = run_fixed_horizon(filtered, MAIN_HORIZON, label=label)
    s = summarize_trades(trades, label)
    s["filter"] = label
    n_blocked_by_filter = len(ledger_df[(ledger_df["model_direction"].isin(["LONG","SHORT"])) &
                                         ~ledger_df.index.isin(filtered.index)])
    s["n_signals_blocked_by_filter"] = n_blocked_by_filter
    return s, trades

# Filter functions
def f_model_only(df):
    return df["model_direction"].isin(["LONG","SHORT"])

def f_no_gate_block(df):
    return df["model_direction"].isin(["LONG","SHORT"]) & (df["gate_status"] != "ALL_BLOCKED")

def f_ofi_dir_agree(df):
    mask = f_model_only(df)
    # OFI level decision dir agrees with model
    ofi_agrees = (
        ((df["model_direction"]=="LONG")  & (df["dir"].fillna("") == "BULL")) |
        ((df["model_direction"]=="SHORT") & (df["dir"].fillna("") == "BEAR"))
    )
    return mask & ofi_agrees

def f_no_sr_conflict(df):
    mask = f_model_only(df)
    # Model LONG but support consumed → conflict; Model SHORT but resistance consumed → conflict
    no_conflict = ~(
        ((df["model_direction"]=="LONG")  & df["support_consumed"].fillna(False).astype(bool)) |
        ((df["model_direction"]=="SHORT") & df["resistance_consumed"].fillna(False).astype(bool))
    )
    return mask & no_conflict

def f_book_switch_confirm(df):
    mask = f_model_only(df)
    # Bid pull pressure low for LONG (offers being pulled), ask pull pressure low for SHORT
    bp = df["bid_pull_pressure"].fillna(0.5)
    ap = df["ask_pull_pressure"].fillna(0.5)
    confirm = (
        ((df["model_direction"]=="LONG")  & (ap > bp))  |  # ask side under more pull pressure
        ((df["model_direction"]=="SHORT") & (bp > ap))
    )
    return mask & confirm

def f_toxic_flow_confirm(df):
    mask = f_model_only(df)
    dtox = df["dash_toxicity"].fillna(0)
    vpin = df["vpin"].fillna(0)
    confirm = (
        ((df["model_direction"]=="LONG")  & (dtox > 0)) |
        ((df["model_direction"]=="SHORT") & (dtox < 0)) |
        (vpin.abs() > 0.3)
    )
    return mask & confirm

def f_primary_gate_only(df):
    return (df["model_direction"].isin(["LONG","SHORT"])) & (df["gate_status"] == "PRIMARY_USE")

def f_all_filters(df):
    return (f_no_gate_block(df) & f_no_sr_conflict(df) & f_book_switch_confirm(df) & f_ofi_dir_agree(df))

filter_results = []
filter_trades_map = {}
for fname, ffn in [
    ("F1_MODEL_ONLY",           f_model_only),
    ("F2_NO_GATE_BLOCK",        f_no_gate_block),
    ("F3_OFI_DIR_AGREE",        f_ofi_dir_agree),
    ("F4_NO_SR_CONFLICT",       f_no_sr_conflict),
    ("F5_BOOK_SWITCH_CONFIRM",  f_book_switch_confirm),
    ("F6_TOXIC_FLOW_CONFIRM",   f_toxic_flow_confirm),
    ("F7_PRIMARY_GATE_ONLY",    f_primary_gate_only),
    ("F8_ALL_FILTERS_COMBINED", f_all_filters),
]:
    try:
        s, ft = apply_filter(ledger_f, ffn, fname)
        filter_results.append(s)
        filter_trades_map[fname] = ft
    except Exception as e:
        filter_results.append({"filter":fname,"error":str(e)[:80]})
        filter_trades_map[fname] = pd.DataFrame()

filt_df = pd.DataFrame(filter_results)
filt_df.to_csv(OUT / "shadow_pnl_by_filter.csv", index=False)

# Opportunity cost of blockers
opp_rows = []
base_f1 = filter_results[0]
for s in filter_results[1:]:
    opp_rows.append({
        "filter": s.get("filter","?"),
        "n_trades_base": base_f1.get("n_trades",0),
        "n_trades_filtered": s.get("n_trades",0),
        "trades_removed": base_f1.get("n_trades",0) - s.get("n_trades",0),
        "pts_base":       base_f1.get("total_net_pts",0),
        "pts_filtered":   s.get("total_net_pts",0),
        "pts_delta":      round(s.get("total_net_pts",0) - base_f1.get("total_net_pts",0),2),
        "usd_delta_NQ":   round(s.get("net_usd_NQ",0) - base_f1.get("net_usd_NQ",0),2),
        "filter_helped":  s.get("total_net_pts",0) > base_f1.get("total_net_pts",0),
    })
pd.DataFrame(opp_rows).to_csv(OUT / "blocked_trade_opportunity_cost.csv", index=False)

print(f"  Filter results:")
for s in filter_results:
    n  = s.get("n_trades",0)
    pt = s.get("total_net_pts",0)
    hr = s.get("hit_rate",0)
    ud = s.get("net_usd_NQ",0)
    print(f"    {s.get('filter','?'):30s}: {n:3d} trades  pts={pt:+7.2f}  "
          f"hit={hr}%  NQ=${ud:+8.2f}")

# ─────────────────────────────────────────────────────────────────────────────
# PART G — LONG vs SHORT BREAKDOWN
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART G] Long vs short breakdown...")

main_trades = v1.copy()

ls_rows = []
for side in ["LONG","SHORT"]:
    sub = main_trades[main_trades["side"]==side]
    if len(sub) == 0:
        ls_rows.append({"side":side,"n":0}); continue
    wins = sub[sub["net_points"]>0]
    loss = sub[sub["net_points"]<0]

    # Best/worst reaction type
    rxn_pts = sub.groupby("reaction_types")["net_points"].sum().sort_values(ascending=False)
    lvl_pts = sub.groupby("level_types")["net_points"].sum().sort_values(ascending=False)

    ls_rows.append({
        "side": side,
        "n": len(sub),
        "hit_rate": round(len(wins)/len(sub)*100,1),
        "total_net_pts": round(sub["net_points"].sum(),2),
        "avg_pts": round(sub["net_points"].mean(),2),
        "avg_win_pts": round(wins["net_points"].mean(),2) if len(wins)>0 else 0,
        "avg_loss_pts":round(loss["net_points"].mean(),2) if len(loss)>0 else 0,
        "best_win_pts": round(sub["net_points"].max(),2),
        "worst_loss_pts":round(sub["net_points"].min(),2),
        "avg_MFE": round(sub["MFE"].mean(),2) if sub["MFE"].notna().any() else None,
        "avg_MAE": round(sub["MAE"].mean(),2) if sub["MAE"].notna().any() else None,
        "total_NQ_USD": round(sub["net_usd_NQ"].sum(),2),
        "total_MNQ_USD": round(sub["net_usd_MNQ"].sum(),2),
        "best_reaction_type": rxn_pts.index[0] if len(rxn_pts)>0 else "?",
        "best_rxn_pts": round(rxn_pts.iloc[0],2) if len(rxn_pts)>0 else 0,
        "worst_reaction_type": rxn_pts.index[-1] if len(rxn_pts)>1 else "?",
        "worst_rxn_pts": round(rxn_pts.iloc[-1],2) if len(rxn_pts)>1 else 0,
        "best_level_type": lvl_pts.index[0] if len(lvl_pts)>0 else "?",
        "worst_level_type": lvl_pts.index[-1] if len(lvl_pts)>1 else "?",
    })

pd.DataFrame(ls_rows).to_csv(OUT / "long_short_pnl_breakdown.csv", index=False)
for r in ls_rows:
    print(f"  {r['side']:6s}: n={r['n']}  pts={r['total_net_pts']:+7.2f}  "
          f"hit={r['hit_rate']}%  NQ=${r['total_NQ_USD']:+8.2f}")

# ─────────────────────────────────────────────────────────────────────────────
# PART H — SESSION PHASE / TIME BREAKDOWN
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART H] Session phase breakdown...")

if len(main_trades) > 0:
    sess_rows = []
    for sess in ["Asia","EU","US_Open","US_AM","US_PM","US_Late","UNKNOWN"]:
        sub = main_trades[main_trades["session"]==sess]
        if len(sub) == 0: continue
        wins = sub[sub["net_points"]>0]
        sess_rows.append({
            "session":sess, "n":len(sub),
            "total_pts":round(sub["net_points"].sum(),2),
            "hit_rate":round(len(wins)/len(sub)*100,1) if len(sub)>0 else 0,
            "avg_pts":round(sub["net_points"].mean(),2),
            "net_NQ_USD":round(sub["net_usd_NQ"].sum(),2),
        })
    pd.DataFrame(sess_rows).to_csv(OUT / "pnl_by_session_phase.csv", index=False)

    # By hour (extract from bar_idx_in_day — approximate)
    if "bar_idx_in_day" in main_trades.columns:
        main_trades["hour_approx"] = ((main_trades["bar_idx_in_day"] // 60) % 24).astype(int)
        hour_rows = []
        for h, sub in main_trades.groupby("hour_approx"):
            wins = sub[sub["net_points"]>0]
            hour_rows.append({
                "hour_approx":h, "n":len(sub),
                "total_pts":round(sub["net_points"].sum(),2),
                "hit_rate":round(len(wins)/len(sub)*100,1) if len(sub)>0 else 0,
                "net_NQ_USD":round(sub["net_usd_NQ"].sum(),2),
            })
        pd.DataFrame(hour_rows).to_csv(OUT / "pnl_by_hour.csv", index=False)

    # By gate status and level type (market state proxy)
    state_rows = []
    for key_col in ["gate_status","level_types","reaction_types"]:
        if key_col not in main_trades.columns: continue
        for val, sub in main_trades.groupby(key_col):
            wins = sub[sub["net_points"]>0]
            state_rows.append({
                "state_type":key_col, "state_value":str(val), "n":len(sub),
                "total_pts":round(sub["net_points"].sum(),2),
                "hit_rate":round(len(wins)/len(sub)*100,1) if len(sub)>0 else 0,
                "avg_pts":round(sub["net_points"].mean(),2),
                "net_NQ_USD":round(sub["net_usd_NQ"].sum(),2),
            })
    pd.DataFrame(state_rows).to_csv(OUT / "pnl_by_market_state.csv", index=False)
    print(f"  Written: pnl_by_session_phase.csv, pnl_by_hour.csv, pnl_by_market_state.csv")

# ─────────────────────────────────────────────────────────────────────────────
# PART I — MFE/MAE and money left on table
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART I] MFE/MAE — money left on table...")

if len(main_trades) > 0 and "MFE" in main_trades.columns:
    wins = main_trades[main_trades["net_points"] > 0]
    loss = main_trades[main_trades["net_points"] <= 0]

    realized_pts      = main_trades["net_points"].sum()
    realized_wins_pts = wins["net_points"].sum()
    realized_loss_pts = loss["net_points"].sum()

    # MFE/MAE totals and averages
    mfe_total    = main_trades["MFE"].dropna().sum()
    mae_total    = main_trades["MAE"].dropna().sum()
    mfe_mean     = main_trades["MFE"].mean()
    mae_mean     = main_trades["MAE"].mean()

    # ── WINNERS: how much more could you have captured by exiting at peak?
    mfe_wins     = wins["MFE"].dropna().sum()
    # left_on_table_wins = peak_possible - what_you_got (winners only)
    left_on_tbl_wins  = mfe_wins - realized_wins_pts

    # ── LOSERS: how much was available before the reversal?
    mfe_loss     = loss["MFE"].dropna().sum()
    # saved_if_exited_at_mfe = MFE_loser - net_pts (net_pts is negative, so this = MFE + abs(loss))
    # = the full swing from the favorable peak to the actual exit
    saved_if_peak_exit = mfe_loss - realized_loss_pts

    # ── MFE capture: only meaningful on winners (apples-to-apples)
    mfe_capture_wins  = realized_wins_pts / max(mfe_wins, EPS)

    # ── Total potential from better exits (winners: less greed; losers: better stop)
    total_exit_improvement = left_on_tbl_wins + saved_if_peak_exit

    # ── Did H40 give back vs H10?
    h10_summ  = summarize_trades(v3, "H10")
    h40_summ  = summarize_trades(v1, "H40")
    gave_back = h40_summ["total_net_pts"] < h10_summ["total_net_pts"]

    # ── Optimal theoretical: exit each trade at its MFE bar (upper bound)
    mfe_theoretical_total = mfe_total  # if every trade exited at exact MFE tick

    mfe_rows = [
        {"metric": "n_winning_trades",               "value": len(wins)},
        {"metric": "n_losing_trades",                "value": len(loss)},
        {"metric": "total_realized_pts",             "value": round(realized_pts,2)},
        {"metric": "realized_wins_pts",              "value": round(realized_wins_pts,2)},
        {"metric": "realized_loss_pts",              "value": round(realized_loss_pts,2)},
        # MFE decomposed by outcome
        {"metric": "mfe_winners_pts",                "value": round(mfe_wins,2)},
        {"metric": "mfe_losers_pts",                 "value": round(mfe_loss,2)},
        {"metric": "mfe_total_all_trades_pts",       "value": round(mfe_total,2)},
        {"metric": "avg_MFE_per_trade",              "value": round(mfe_mean,2)},
        {"metric": "avg_MFE_winners",                "value": round(wins["MFE"].mean(),2) if len(wins)>0 else 0},
        {"metric": "avg_MFE_losers",                 "value": round(loss["MFE"].mean(),2) if len(loss)>0 else 0},
        {"metric": "avg_MAE_per_trade",              "value": round(mae_mean,2)},
        # Capture — winners only (correct formula)
        {"metric": "mfe_capture_winners_only",       "value": round(mfe_capture_wins,4)},
        # Left on table — split by outcome type
        {"metric": "left_on_table_winners_pts",      "value": round(left_on_tbl_wins,2)},
        {"metric": "left_on_table_winners_NQ_USD",   "value": round(left_on_tbl_wins*NQ_POINT_VALUE,2)},
        {"metric": "saved_if_exited_at_mfe_losers_pts",     "value": round(saved_if_peak_exit,2)},
        {"metric": "saved_if_exited_at_mfe_losers_NQ_USD",  "value": round(saved_if_peak_exit*NQ_POINT_VALUE,2)},
        {"metric": "total_exit_improvement_pts",     "value": round(total_exit_improvement,2)},
        {"metric": "total_exit_improvement_NQ_USD",  "value": round(total_exit_improvement*NQ_POINT_VALUE,2)},
        # Horizon comparison
        {"metric": "H10_total_pts",                  "value": h10_summ["total_net_pts"]},
        {"metric": "H20_total_pts",                  "value": summarize_trades(v2,"H20")["total_net_pts"]},
        {"metric": "H40_total_pts",                  "value": h40_summ["total_net_pts"]},
        {"metric": "H40_gave_back_vs_H10",           "value": gave_back},
        {"metric": "worst_MAE_pts",                  "value": round(main_trades["MAE"].max(),2)},
        {"metric": "worst_adverse_trade_pts",        "value": round(main_trades["net_points"].min(),2)},
    ]
    pd.DataFrame(mfe_rows).to_csv(OUT / "mfe_mae_money_left_on_table.csv", index=False)
    print(f"  Realized: {realized_pts:+.2f}pts  (wins={realized_wins_pts:+.2f}  loss={realized_loss_pts:+.2f})")
    print(f"  MFE winners: {mfe_wins:.2f}pts  capture: {mfe_capture_wins:.1%}  "
          f"left on table (winners): {left_on_tbl_wins:.2f}pts")
    print(f"  MFE losers:  {mfe_loss:.2f}pts  saved if exited at peak: {saved_if_peak_exit:.2f}pts")
    print(f"  Total exit improvement potential: {total_exit_improvement:.2f}pts  "
          f"(${total_exit_improvement*NQ_POINT_VALUE:,.0f} NQ shadow)")
    print(f"  H10={h10_summ['total_net_pts']:+.2f}pts  H40={h40_summ['total_net_pts']:+.2f}pts  "
          f"gave_back={gave_back}")
else:
    realized_pts = 0; mfe_total = 0; mae_total = 0
    mfe_wins = 0; mfe_loss = 0; mfe_capture_wins = 0
    left_on_tbl_wins = 0; saved_if_peak_exit = 0; total_exit_improvement = 0
    gave_back = False; mfe_mean = 0; mae_mean = 0
    realized_wins_pts = 0; realized_loss_pts = 0

# ─────────────────────────────────────────────────────────────────────────────
# PART J — FINAL REPORT
# ─────────────────────────────────────────────────────────────────────────────
print("\n[PART J] Writing final report...")

# Gather key stats
v1_s  = summarize_trades(v1, "H40")
v3_s  = summarize_trades(v3, "H10")
f1_s  = filter_results[0]
best_filt = max(filter_results, key=lambda x: x.get("total_net_pts",0))
long_s  = ls_rows[0] if ls_rows and ls_rows[0]["side"]=="LONG"  else {}
short_s = ls_rows[1] if len(ls_rows)>1 and ls_rows[1]["side"]=="SHORT" else {}

# Reaction type PnL
rxn_pnl = {}
if len(main_trades) > 0:
    rxn_pnl = main_trades.groupby("reaction_types")["net_points"].agg(
        ["sum","mean","count"]).sort_values("sum",ascending=False).to_dict("index")

# Level type PnL
lvl_pnl = {}
if len(main_trades) > 0:
    lvl_pnl = main_trades.groupby("level_types")["net_points"].agg(
        ["sum","mean","count"]).sort_values("sum",ascending=False).to_dict("index")

# Threshold sensitivity table
thresh_df = pd.read_csv(OUT / "shadow_pnl_threshold_sensitivity.csv")
slip_df   = pd.read_csv(OUT / "shadow_pnl_slippage_sensitivity.csv")

main_pts      = v1_s["total_net_pts"]
main_nq_usd   = v1_s["net_usd_NQ"]
main_mnq_usd  = v1_s["net_usd_MNQ"]
main_hit      = v1_s["hit_rate"]
main_dd       = v1_s["max_drawdown_pts"]
main_pf       = v1_s["profit_factor"]

report = f"""# Current Session Shadow PnL Audit
**SHADOW_THEORETICAL_PNL ONLY — NO EXECUTION — NO BROKER — NO PAPER TRADING**
**Audit run**: {run_ts}
**Session**: {session_date}

---

## CRITICAL DISCLAIMERS
- All figures are SHADOW_THEORETICAL_PNL — no real trades executed
- trading_enabled = False
- shadow_only = True
- roll_quality_flag = {inf_summ.get('roll_quality_flag','?')}
- ROLLOVER_WARMUP_LOW_SAMPLE: model may have reduced calibration during roll

---

## Q1: CURRENT SESSION DATE AND TIMESTAMP RANGE

| Field | Value |
|-------|-------|
| session_date | {session_date} |
| Calendar date (UTC) | 2026-07-06 (NQU6 session labeled as Jul 5 internally) |
| Session start bar (global) | {session_start_bar} |
| Session end bar (global) | {session_end_bar} |
| Master latest bar | {master_latest_bar} |
| Master latest close | {master_latest_close} |
| Master latest timestamp | {master_latest_ts} |
| Inference run UTC | {run_ts} |

---

## Q2: HOW MANY MODEL EVENT ROWS OCCURRED?

**{len(pred)} event rows** in the current session.

| Gate Status | Rows |
|-------------|------|
{chr(10).join(f"| {k} | {v} |" for k,v in session_def['gate_status_counts'].items())}

Reaction types:
{chr(10).join(f"| {k} | {v} |" for k,v in list(session_def['reaction_type_counts'].items())[:8])}

---

## Q3: HOW MANY INDEPENDENT BAR DECISIONS OCCURRED?

**{len(ledger)} unique bar decisions** (deduplicated from {len(pred)} event rows)
- LONG decisions: {n_long_decisions}
- SHORT decisions: {n_short_decisions}
- FLAT (below threshold {DEFAULT_THRESHOLD}): {n_flat_decisions}
- BLOCKED (all rows blocked): {n_blocked_dec}

Bar-level dedup is the **honest metric** — multiple level events on the same bar count as ONE trade signal.

---

## Q4: HOW MUCH DID MODEL-ONLY MAKE/LOSE IN POINTS? (H40)

**SHADOW_THEORETICAL_PNL — Model Only (V1_H40, threshold={DEFAULT_THRESHOLD}):**
- Trades matured: {v1_s['n_trades']}
- Total raw points: {v1_s['total_raw_pts']:+.2f}
- Total net points: **{v1_s['total_net_pts']:+.2f}**
- Hit rate: {v1_s['hit_rate']}%
- Avg win: +{v1_s['avg_win_pts']:.2f} pts
- Avg loss: {v1_s['avg_loss_pts']:.2f} pts
- Profit factor: {v1_s['profit_factor']:.3f}
- Max drawdown: {v1_s['max_drawdown_pts']:.2f} pts

---

## Q5: USD PnL FOR 1 NQ CONTRACT (SHADOW ONLY)

**SHADOW_THEORETICAL_PNL — 1 NQ, zero slippage, H40:**
- Gross USD: ${v1_s['gross_usd_NQ']:+,.2f}
- Net USD:   **${v1_s['net_usd_NQ']:+,.2f}**

---

## Q6: USD PnL FOR 1 MNQ CONTRACT (SHADOW ONLY)

**SHADOW_THEORETICAL_PNL — 1 MNQ, zero slippage, H40:**
- Gross USD: ${v1_s['gross_usd_MNQ']:+,.2f}
- Net USD:   **${v1_s['net_usd_MNQ']:+,.2f}**

---

## Q7: COST/SLIPPAGE SENSITIVITY (1 NQ, H40)

{slip_df[slip_df['n_contracts']==1][['slip_ticks','n_contracts','total_net_pts','hit_rate','net_usd_NQ']].to_string(index=False)}

---

## THRESHOLD SENSITIVITY (1 NQ, H40)

{thresh_df[['threshold','n_trades','total_net_pts','hit_rate','profit_factor','net_usd_NQ']].to_string(index=False)}

---

## Q8: HOW DID LONG TRADES PERFORM?

{pd.DataFrame(ls_rows).to_string(index=False) if ls_rows else "No trades"}

---

## Q9: HOW DID SHORT TRADES PERFORM?

See Q8 table above (SHORT row).

---

## Q10: WHICH REACTION TYPES MADE MONEY?

Reaction type PnL (V1_H40 main variant):
{pd.DataFrame([{"reaction":k,"total_pts":round(v['sum'],2),"avg_pts":round(v['mean'],2),"n":v['count']} for k,v in rxn_pnl.items()]).to_string(index=False) if rxn_pnl else "Insufficient data"}

---

## Q11: WHICH REACTION TYPES LOST MONEY?

See table above — bottom rows.

---

## Q12: WHICH LEVEL TYPES MADE MONEY?

Level type PnL (V1_H40):
{pd.DataFrame([{"level":k,"total_pts":round(v['sum'],2),"avg_pts":round(v['mean'],2),"n":v['count']} for k,v in lvl_pnl.items()]).to_string(index=False) if lvl_pnl else "Insufficient data"}

---

## Q13: DID OFI/BOOKSWITCH/TOXICFLOW/TRAVEL FILTERS IMPROVE PnL?

Filter comparison (V1_H40 baseline):
{filt_df[['filter','n_trades','total_net_pts','hit_rate','profit_factor','net_usd_NQ']].to_string(index=False) if 'total_net_pts' in filt_df.columns else filt_df.to_string(index=False)}

**Best filter**: {best_filt.get('filter','?')} with {best_filt.get('total_net_pts',0):+.2f} pts

---

## Q14: HOW MUCH DID BLOCKERS SAVE OR COST?

Opportunity cost analysis (delta vs model-only):
{pd.read_csv(OUT / "blocked_trade_opportunity_cost.csv").to_string(index=False)}

---

## Q15: HOW MUCH MFE WAS LEFT ON TABLE?

MFE is decomposed by outcome — mixing winners and losers into one number is misleading
because losers need a better STOP rule, not a better target.

**Winners ({len(wins) if len(main_trades)>0 else 0} trades) — exit-too-early problem:**

| Metric | Value |
|--------|-------|
| Realized (wins) | {realized_wins_pts:+.2f} pts |
| MFE available (wins) | {mfe_wins:.2f} pts |
| MFE capture ratio (wins only) | **{mfe_capture_wins:.1%}** |
| Left on table — winners | **{left_on_tbl_wins:.2f} pts  (${left_on_tbl_wins*NQ_POINT_VALUE:+,.0f} NQ shadow)** |

**Losers ({len(loss) if len(main_trades)>0 else 0} trades) — stop placement problem:**

| Metric | Value |
|--------|-------|
| Realized (losses) | {realized_loss_pts:+.2f} pts |
| MFE before reversal (losers) | {mfe_loss:.2f} pts |
| Saved if stopped at MFE peak | **{saved_if_peak_exit:.2f} pts  (${saved_if_peak_exit*NQ_POINT_VALUE:+,.0f} NQ shadow)** |

**Combined exit improvement potential: {total_exit_improvement:.2f} pts  (${total_exit_improvement*NQ_POINT_VALUE:+,.0f} NQ shadow)**

| Horizon | Pts |
|---------|-----|
| H10 | {v3_s['total_net_pts']:+.2f} |
| H40 | {main_pts:+.2f} |
| H40 gave back vs H10 | {gave_back} |

---

## Q16: DID MODEL FAIL IN ANY SPECIFIC PHASE?

See pnl_by_session_phase.csv and pnl_by_hour.csv for breakdown.

Key concern: **ROLLOVER_WARMUP_LOW_SAMPLE** flag active.
During roll warmup, the model has fewer training samples for the new contract,
potentially reducing calibration accuracy. All signals should be treated with
additional skepticism during this period.

---

## Q17: IS THE RESULT PRODUCTION-READY?

**NO — shadow only. Results are theoretical and subject to:**
1. ROLLOVER_WARMUP_LOW_SAMPLE — reduced calibration
2. SECONDARY_WATCH gate status on many signals
3. Fixed 500-contract volume bars (actual fills would depend on book depth)
4. No slippage, no commissions assumed in baseline
5. Back-adjusted continuous prices — roll offsets affect raw USD values
6. Single session (current session only) — insufficient OOS validation window

Minimum required before any paper trading consideration:
- Validated over 20+ sessions
- PBO/DSR applied
- PRIMARY_USE gate status only
- Signal confirmed by OFI Level Decision dir agreement
- No ROLLOVER_WARMUP period

---

## ALL VARIANTS SUMMARY

{variant_summ[['variant','n_trades','total_net_pts','hit_rate','profit_factor','net_usd_NQ','max_drawdown_pts']].to_string(index=False)}

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

CURRENT_SESSION_DATE:               {session_date}
CURRENT_SESSION_START:              bar_global={session_start_bar}
CURRENT_SESSION_END:                bar_global={session_end_bar}
EVENT_ROWS:                         {len(pred)}
BAR_LEVEL_DECISIONS:                {len(ledger)} ({n_long_decisions}L / {n_short_decisions}S / {n_flat_decisions}F / {n_blocked_dec}BLK)
MODEL_ONLY_POINTS_H40:              {main_pts:+.2f}
MODEL_ONLY_USD_NQ_1_CONTRACT:       ${main_nq_usd:+,.2f} SHADOW_THEORETICAL_PNL
MODEL_ONLY_USD_MNQ_1_CONTRACT:      ${main_mnq_usd:+,.2f} SHADOW_THEORETICAL_PNL
BEST_FILTER:                        {best_filt.get('filter','?')}
BEST_FILTER_POINTS:                 {best_filt.get('total_net_pts',0):+.2f}
BEST_FILTER_USD_NQ_1_CONTRACT:      ${best_filt.get('net_usd_NQ',0):+,.2f} SHADOW_THEORETICAL_PNL
LONG_POINTS_H40:                    {long_s.get('total_net_pts','N/A')}
SHORT_POINTS_H40:                   {short_s.get('total_net_pts','N/A')}
MAX_DRAWDOWN_POINTS:                {main_dd:.2f}
MFE_LEFT_ON_TABLE_WINNERS_POINTS:   {left_on_tbl_wins:.2f}
SAVED_IF_STOPPED_AT_MFE_LOSERS:    {saved_if_peak_exit:.2f}
TOTAL_EXIT_IMPROVEMENT_POINTS:     {total_exit_improvement:.2f}
ROLL_QUALITY_WARNING:               {inf_summ.get('roll_quality_flag','?')}
PRODUCTION_READY:                   false
PAPER_TRADING_READY:                false
OVERALL:                            PASS
```
"""

with open(OUT / "CURRENT_SESSION_SHADOW_PNL_REPORT.md", "w") as f:
    f.write(report)

elapsed = time.time() - t0
print(f"\n{'='*70}")
print(f"SHADOW PnL AUDIT COMPLETE in {elapsed:.1f}s")
files = sorted(OUT.glob("*"))
print(f"Files written: {len(files)}")
for f in files:
    print(f"  {f.name}")
print(f"\nSHADOW_THEORETICAL_PNL — NO EXECUTION — NO BROKER — PASS")
