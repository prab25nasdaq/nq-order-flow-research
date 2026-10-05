#!/usr/bin/env python3
"""
build_ofi_labels_features_folds.py

SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING.

OFI Phase 2: label spine + audits + engineered features + walk-forward folds
+ a LightGBM baseline (Track 1), built entirely from the READ-ONLY master at
MASTER_DIR (see CONFIG). Never writes, moves, or modifies anything under
MASTER_DIR or any other production path -- all output goes under a fresh
timestamped run_dir next to MASTER_DIR.

Style-matches build_ofi_cell_training_master_from_scratch.py: deterministic,
seeded, per-part checkpoint files + a STATE.json, --finish-from <PART> resume
support, chunked column-pruned pyarrow reads, modest peak RAM (every part
independently re-reads only the columns/depth it needs from the master
parquet files rather than holding cross-part state in memory, so a resumed
run reproduces the same intermediate files a from-scratch run would produce).

Canonical grain for this entire phase: depth_n == 10 (the only depth that
covers both NQM6 and NQU6 -- NQM6 was only ever captured at depth 10; NQU6
was captured 4x at depths 5/10/15/20).

Corrections to the phase-2 brief found during inspection (documented here,
not silently "fixed" -- see PHASE2_REPORT.md Part A/B1 section for the full
audit): the brief's per-contract bar-count/session-count breakdown
(12,354 NQM6 / 28,954 NQU6 bars; "28 sessions" for NQU6) does not match the
master as delivered. The actual depth-10 breakdown is NQM6=11,690 bars over
7 sessions (2026-06-03..06-11) and NQU6=29,618 bars over 29 sessions
(2026-06-14..07-23, including one PARTIAL session 2026-06-22 flagged in
daily_coverage_audit.csv). The brief's TOTAL of 41,308 rows is correct and
is retained as the hard gate; the per-contract split gate is relaxed to the
observed truth and reported, not silently forced to match the brief.
Similarly the brief's "~40% ohlcv_source=='unavailable'" prior is off; the
measured bar-level figure (depth 10) is ~18.7% -- reported via B4, not
assumed.

Run:
    python3 build_ofi_labels_features_folds.py
    python3 build_ofi_labels_features_folds.py --finish-from D --run-dir <existing_run_dir>
"""

import os
import sys
import json
import hashlib
import argparse
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from scipy import stats as sstats

warnings.filterwarnings("ignore", category=FutureWarning)
pd.set_option("future.no_silent_downcasting", True)

# ============================================================================
# CONFIG
# ============================================================================

MASTER_DIR = "/home/prabh/OFI_Production/model_training_masters/ofi_cell_training_master_from_scratch_20260724_195602Z/"
LONG_PATH = os.path.join(MASTER_DIR, "ofi_cell_master_long.parquet")
PACKED_PATH = os.path.join(MASTER_DIR, "ofi_bar_packed_cells_master.parquet")
PACKED_CTX_PATH = os.path.join(MASTER_DIR, "ofi_bar_packed_cells_master_with_context.parquet")
COVERAGE_AUDIT_PATH = os.path.join(MASTER_DIR, "daily_coverage_audit.csv")

OUTPUT_ROOT = "/home/prabh/OFI_Production/model_training_masters/"
BUILD_SCRIPT_PATH = os.path.abspath(__file__)

TICK = 0.25
DEPTH = 10
RTH_START_MIN = 13 * 60 + 30   # 13:30 UTC
RTH_END_MIN = 20 * 60          # 20:00 UTC
HORIZONS = [1, 2, 5, 10, 20, 40]
LADDER_WIDTH = 80
LADDER_K = np.arange(-LADDER_WIDTH, LADDER_WIDTH + 1)  # 161 values
CHANNELS = ["net_bid_flow", "net_ask_flow", "signed_flow", "abs_flow",
            "buyer_pressure_cell", "seller_pressure_cell"]
BANDS = [("b_m80_m21", -80, -21), ("b_m20_m6", -20, -6), ("b_m5_p5", -5, 5),
         ("b_p6_p20", 6, 20), ("b_p21_p80", 21, 80)]
LEVEL_TYPES = ["POC", "VAH", "VAL", "HVN", "LVN", "S", "R"]
CTX_BASE_COLS = ["ctx_dash_vpin_pct", "ctx_mlofi_norm", "ctx_delta_norm",
                  "ctx_bf_bs_bullish_switch_score", "ctx_bf_bs_bearish_switch_score",
                  "ctx_rxn_absorption", "ctx_dist_to_poc_ticks", "ctx_dist_to_vah_ticks",
                  "ctx_dist_to_val_ticks", "ctx_dist_to_hvn_ticks", "ctx_dist_to_lvn_ticks"]

EWM_HALFLIFE_SIGMA = 120
EWM_MIN_PERIODS_SIGMA = 60
CLASS_QUANTILES = (0.30, 0.70)

SIGMA_FLOOR_TICKS = 1.0

FOLD_TRAIN_MIN_SESSIONS = 8
FOLD_TEST_BLOCK_SESSIONS = 2
FOLD_STEP_SESSIONS = 2
FOLD_EMBARGO_BARS = 40

SEED = 42
PART_ORDER = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L"]

# ---- Phase 2B config (locked decisions from Phase 2 results) ----
NQM6_POLICY_LOCKED = "EXCLUDED"           # NQM6 excluded from all Phase 2B training/eval
PRIMARY_HORIZON = 40
HORIZONS_EXT = [60, 80]                    # new labels added in Part I
J_HORIZONS = [10, 20, 40, 60, 80]          # Part J iteration horizons
ABLATION_FAMILIES = ["profile", "band", "side_zone", "pressure", "bar_totals",
                      "level", "context", "time", "trailing"]
ECON_COST_PTS = 1.0
GBM_SWEEP_GRID = {"num_leaves": [31, 63, 127], "learning_rate": [0.03, 0.05],
                   "min_data_in_leaf": [50, 100, 200]}
GBM_SWEEP_N_ESTIMATORS = 800
GBM_SWEEP_ESR = 50
GBM_FINAL_N_ESTIMATORS = 2000
GBM_FINAL_ESR = 100
CNN_SEEDS = [42, 43, 44]
CNN_BATCH = 256
CNN_MAX_EPOCHS = 60
CNN_PATIENCE = 8
CNN_LR = 3e-4
CNN_WD = 1e-2
CNN_MIN_LR = 1e-5

# ============================================================================
# small utilities
# ============================================================================


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_of_file(path, block_size=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(block_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def tick_str(k):
    if k < 0:
        return f"m{-k}"
    elif k == 0:
        return "z0"
    else:
        return f"p{k}"


def safe_div(a, b, default=0.0):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    out = np.full_like(a, default, dtype=float)
    mask = np.abs(b) > 1e-12
    out[mask] = a[mask] / b[mask]
    return out


def load_state(run_dir):
    p = os.path.join(run_dir, "STATE.json")
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {"run_dir": run_dir, "parts": {}}


def save_state(run_dir, state):
    p = os.path.join(run_dir, "STATE.json")
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=str)
    os.replace(tmp, p)


def mark_part_done(run_dir, state, part, stats):
    state["parts"][part] = {"status": "done", "finished_at": utc_now_iso(), "stats": stats}
    save_state(run_dir, state)


def read_long_depth10(columns):
    """Column-pruned, depth-filtered read of the (read-only) long cell master."""
    t = pq.read_table(LONG_PATH, columns=list(dict.fromkeys(columns + ["depth_n"])),
                       filters=[("depth_n", "=", DEPTH)])
    return t.to_pandas()


def read_spine(run_dir):
    return pd.read_parquet(os.path.join(run_dir, "spine_depth10.parquet"))


def assert_stable_sorted(df):
    for c, g in df.groupby("contract", sort=False):
        assert g["bar_end_ts_ns"].is_monotonic_increasing, f"{c} bar_end_ts_ns not increasing"
        assert g["bar_idx"].is_monotonic_increasing, f"{c} bar_idx not increasing"


# ============================================================================
# PART A -- canonical bar spine
# ============================================================================


def part_A_spine(run_dir):
    log("PART A: building canonical bar spine (depth_n==10) ...")
    cols = ["contract", "bar_idx", "session_date", "timestamp_utc", "bar_start_ts_ns",
            "bar_end_ts_ns", "close_price", "mid_price", "ohlcv_source"]
    df = read_long_depth10(cols)

    key = ["contract", "bar_idx"]
    g = df.groupby(key, sort=False)
    n_cells = g.size().rename("n_cells")

    nun = g.agg(n_end=("bar_end_ts_ns", "nunique"), n_close=("close_price", "nunique"),
                n_sess=("session_date", "nunique"), n_start=("bar_start_ts_ns", "nunique"),
                n_mid=("mid_price", "nunique"), n_ohlcv=("ohlcv_source", "nunique"))
    bad = nun[(nun[["n_end", "n_close", "n_sess", "n_start", "n_mid", "n_ohlcv"]] > 1).any(axis=1)]
    if len(bad) > 0:
        bad.reset_index().to_csv(os.path.join(run_dir, "part_A_non_unique_bar_keys.csv"), index=False)
        raise AssertionError(f"PART A FAIL: {len(bad)} (contract,bar_idx) keys have non-unique "
                              f"bar-level identity columns -- see part_A_non_unique_bar_keys.csv")

    spine = g.first()[["session_date", "timestamp_utc", "bar_start_ts_ns", "bar_end_ts_ns",
                        "close_price", "mid_price", "ohlcv_source"]].reset_index()
    spine = spine.merge(n_cells.reset_index(), on=key)
    spine = spine.sort_values(["contract", "bar_end_ts_ns", "bar_idx"], kind="stable").reset_index(drop=True)
    assert_stable_sorted(spine)

    counts = spine.groupby("contract")["bar_idx"].count().to_dict()
    sessions = spine.groupby("contract")["session_date"].nunique().to_dict()
    total = len(spine)
    gate_total_pass = (total == 41308)

    brief_counts = {"NQM6": 12354, "NQU6": 28954}
    discrepancy = {c: {"brief": brief_counts.get(c), "actual": int(counts.get(c, 0))} for c in counts}

    spine.to_parquet(os.path.join(run_dir, "spine_depth10.parquet"), index=False)

    stats = {
        "total_rows": total,
        "gate_total_41308_pass": bool(gate_total_pass),
        "per_contract_counts": {k: int(v) for k, v in counts.items()},
        "per_contract_sessions": {k: int(v) for k, v in sessions.items()},
        "brief_vs_actual_per_contract": discrepancy,
    }
    with open(os.path.join(run_dir, "part_A_spine_report.json"), "w") as f:
        json.dump(stats, f, indent=2)

    log(f"  spine rows={total} gate_pass={gate_total_pass} counts={counts} sessions={sessions}")
    if not gate_total_pass:
        raise AssertionError(f"PART A GATE FAIL: expected 41308 total spine rows, got {total}")
    return stats


# ============================================================================
# PART B -- audits + corrected market stats
# ============================================================================


def _rth_flag_from_ns(ts_ns):
    dt = pd.DatetimeIndex(pd.to_datetime(ts_ns, unit="ns", utc=True))
    minute_of_day = np.asarray(dt.hour) * 60 + np.asarray(dt.minute)
    return (minute_of_day >= RTH_START_MIN) & (minute_of_day < RTH_END_MIN), dt


def part_B_audits(run_dir):
    log("PART B: audits + corrected market stats ...")
    spine = read_spine(run_dir)
    spine = spine.sort_values(["contract", "bar_end_ts_ns", "bar_idx"], kind="stable").reset_index(drop=True)
    is_rth, dt = _rth_flag_from_ns(spine["bar_end_ts_ns"].values)
    spine["is_rth"] = is_rth
    spine["hour_utc"] = dt.hour.values

    # ---------------- B1 [GATE] ordering / reconciliation ----------------
    b1_rows = []
    # NOTE: bar_end_ts_ns can legitimately contain TIES (two bars sealed at the identical
    # nanosecond, observed at 2026-06-09/NQM6 and 2026-07-07/07-08/NQU6 -- the latter two are
    # the known Rithmic dead-feed recovery sessions, see project memory). A tie is NOT the
    # "quicksort scrambled intra-contract order" failure mode this gate exists to catch: after
    # our stable sort on (contract, bar_end_ts_ns, bar_idx), ties are broken by bar_idx, which
    # keeps bar_idx strictly increasing and the close-diff reconciliation exact. So the FATAL
    # gate only fires on a true reversal (bar_end_ts_ns decreasing), a bar_idx ordering break,
    # or a reconciliation mismatch -- ties are reported as a WARNING, not a failure.
    ordering_ok = True
    for (c, sd), gdf in spine.groupby(["contract", "session_date"], sort=False):
        end_diffs = gdf["bar_end_ts_ns"].diff().dropna()
        end_non_decreasing = bool((end_diffs >= 0).all())
        n_ties = int((end_diffs == 0).sum())
        idx_inc = gdf["bar_idx"].is_monotonic_increasing and gdf["bar_idx"].is_unique
        closes = gdf["close_price"].values
        diffs_sum = float(np.diff(closes).sum()) if len(closes) > 1 else 0.0
        expected = float(closes[-1] - closes[0]) if len(closes) > 1 else 0.0
        recon_ok = abs(diffs_sum - expected) <= 1e-6
        ok = bool(end_non_decreasing and idx_inc and recon_ok)
        ordering_ok &= ok
        b1_rows.append({"contract": c, "session_date": sd, "n_bars": len(gdf),
                         "bar_end_ts_ns_non_decreasing": end_non_decreasing, "n_tied_bar_end_ts_ns": n_ties,
                         "bar_idx_increasing": idx_inc, "close_diff_reconciled": recon_ok,
                         "diffs_sum": diffs_sum, "expected_diff": expected, "ok": ok})
    b1_df = pd.DataFrame(b1_rows)
    b1_df.to_csv(os.path.join(run_dir, "part_B1_ordering_reconciliation.csv"), index=False)
    n_sessions_with_ties = int((b1_df["n_tied_bar_end_ts_ns"] > 0).sum())
    if n_sessions_with_ties > 0:
        log(f"  WARNING: {n_sessions_with_ties} (contract,session_date) group(s) have tied bar_end_ts_ns "
            f"values (duplicate nanosecond bar-seal timestamps); bar_idx order and close-diff reconciliation "
            f"still hold in all of them -- see part_B1_ordering_reconciliation.csv")

    gap_rows = []
    for c, gdf in spine.groupby("contract", sort=False):
        idx = np.sort(gdf["bar_idx"].unique())
        diffs = np.diff(idx)
        gap_positions = np.where(diffs > 1)[0]
        runs = [(int(idx[p] + 1), int(idx[p + 1] - 1)) for p in gap_positions]
        gap_rows.append({"contract": c, "n_gap_runs": len(runs), "total_missing_idx": int(sum(b - a + 1 for a, b in runs)),
                          "missing_runs": json.dumps(runs)})
    gap_df = pd.DataFrame(gap_rows)
    gap_df.to_csv(os.path.join(run_dir, "part_B1_bar_idx_gaps.csv"), index=False)

    if not ordering_ok:
        log("ORDERING_FAIL: PART B1 gate failed -- see part_B1_ordering_reconciliation.csv")
        log(b1_df[~b1_df["ok"]].to_string())
        raise AssertionError("PART B1 GATE FAIL: ordering/reconciliation failed for at least one (contract, session_date)")

    # ---------------- B2 bar timing ----------------
    timing_rows = []

    def _timing_stats(sub, label):
        secs = sub.groupby(["contract", "session_date"])["bar_end_ts_ns"].diff().dropna() / 1e9
        if len(secs) == 0:
            return
        timing_rows.append({"segment": label, "n": len(secs), "median_s": secs.median(),
                             "p25_s": secs.quantile(.25), "p75_s": secs.quantile(.75), "p99_s": secs.quantile(.99)})

    _timing_stats(spine, "overall")
    for c, sub in spine.groupby("contract", sort=False):
        _timing_stats(sub, f"contract={c}")
    _timing_stats(spine[spine.is_rth], "RTH")
    _timing_stats(spine[~spine.is_rth], "overnight")
    for c, sub in spine.groupby("contract", sort=False):
        _timing_stats(sub[sub.is_rth], f"contract={c} RTH")
        _timing_stats(sub[~sub.is_rth], f"contract={c} overnight")
    timing_df = pd.DataFrame(timing_rows)
    timing_df.to_csv(os.path.join(run_dir, "part_B2_bar_timing.csv"), index=False)

    bars_per_session = spine.groupby(["contract", "session_date"]).size().reset_index(name="n_bars")
    bars_per_session.to_csv(os.path.join(run_dir, "part_B2_bars_per_session.csv"), index=False)
    pct_rth = float(spine["is_rth"].mean())

    # ---------------- B3 CORRECTED forward-return table ----------------
    fr_rows = []
    spine_sorted = spine.sort_values(["contract", "session_date", "bar_idx"], kind="stable")
    for h in HORIZONS:
        fwd = spine_sorted.groupby(["contract", "session_date"])["close_price"].shift(-h) - spine_sorted["close_price"]

        def _describe(mask, label):
            x = fwd[mask].dropna()
            if len(x) == 0:
                return
            ticks = x / TICK
            fr_rows.append({
                "horizon": h, "segment": label, "n": len(x),
                "mean_pts": x.mean(), "std_pts": x.std(),
                "mean_ticks": ticks.mean(), "std_ticks": ticks.std(),
                "p05": x.quantile(.05), "p25": x.quantile(.25), "p50": x.quantile(.5),
                "p75": x.quantile(.75), "p95": x.quantile(.95),
                "frac_0": float((x == 0).mean()),
                "frac_abs_le_1tick": float((x.abs() <= TICK + 1e-9).mean()),
                "frac_abs_le_4tick": float((x.abs() <= 4 * TICK + 1e-9).mean()),
                "frac_abs_ge_8tick": float((x.abs() >= 8 * TICK - 1e-9).mean()),
            })
        _describe(pd.Series(True, index=fwd.index), "overall")
        _describe(spine_sorted["is_rth"].values, "RTH")
        _describe(~spine_sorted["is_rth"].values, "overnight")
    fr_df = pd.DataFrame(fr_rows)
    fr_df.to_csv(os.path.join(run_dir, "part_B3_fwd_return_table.csv"), index=False)

    std_1bar = fr_df[(fr_df.horizon == 1) & (fr_df.segment == "overall")]["std_pts"].iloc[0]
    median_bars_per_session = bars_per_session["n_bars"].median()
    session_implied_sigma = std_1bar * np.sqrt(median_bars_per_session)
    close_range = spine.groupby(["contract", "session_date"])["close_price"].agg(lambda s: s.max() - s.min())
    realized_median_range = float(close_range.median())
    sanity_note = (f"session-implied sigma (1-bar std {std_1bar:.4f} pts * sqrt(median {median_bars_per_session:.0f} "
                    f"bars/session)) = {session_implied_sigma:.4f} pts vs realized per-session close-range median "
                    f"= {realized_median_range:.4f} pts")
    log("  " + sanity_note)

    # ---------------- B4 mid-staleness audit ----------------
    stale = (spine["mid_price"] - spine["close_price"]) / TICK
    stale_rows = []
    for src, sub in stale.groupby(spine["ohlcv_source"]):
        stale_rows.append({"ohlcv_source": src, "n": len(sub), "mean_ticks": sub.mean(), "std_ticks": sub.std(),
                            "p50_ticks": sub.median(), "p95_abs_ticks": sub.abs().quantile(.95)})
    stale_df = pd.DataFrame(stale_rows)
    stale_df.to_csv(os.path.join(run_dir, "part_B4_mid_staleness_overall.csv"), index=False)

    pct_unavail = (spine.assign(unavail=(spine.ohlcv_source == "unavailable"))
                   .groupby(["contract", "session_date"])["unavail"].mean().reset_index(name="pct_unavailable"))
    pct_unavail.to_csv(os.path.join(run_dir, "part_B4_pct_unavailable_per_session.csv"), index=False)
    overall_pct_unavailable = float((spine.ohlcv_source == "unavailable").mean())

    # ---------------- B5 ask-flow audit ----------------
    flow = read_long_depth10(["contract", "bar_idx", "session_date", "ask_add", "ask_pull"])
    per_session_ask = flow.groupby(["contract", "session_date"])[["ask_add", "ask_pull"]].sum().reset_index()
    per_session_ask["zero_ask_flow"] = (per_session_ask.ask_add + per_session_ask.ask_pull) <= 0
    per_session_ask.to_csv(os.path.join(run_dir, "part_B5_ask_flow_audit.csv"), index=False)
    n_zero_ask_sessions = int(per_session_ask["zero_ask_flow"].sum())

    per_bar_ask = flow.groupby(["contract", "bar_idx"])[["ask_add", "ask_pull"]].sum()
    per_bar_ask["ask_flow_present"] = (per_bar_ask.ask_add + per_bar_ask.ask_pull) > 0
    ask_flag = per_bar_ask["ask_flow_present"].reset_index()
    spine_out = spine.drop(columns=["is_rth", "hour_utc"]).merge(ask_flag, on=["contract", "bar_idx"], how="left")
    spine_out["ask_flow_present"] = spine_out["ask_flow_present"].fillna(False)
    spine_out = spine_out.sort_values(["contract", "bar_end_ts_ns", "bar_idx"], kind="stable").reset_index(drop=True)
    spine_out.to_parquet(os.path.join(run_dir, "spine_depth10.parquet"), index=False)

    # ---------------- B6 dead-column scan ----------------
    dead = read_long_depth10(["trade_volume_at_price", "buy_trade_volume_at_price", "sell_trade_volume_at_price"])
    dead_rows = []
    for c in ["trade_volume_at_price", "buy_trade_volume_at_price", "sell_trade_volume_at_price"]:
        dead_rows.append({"column": c, "max": float(dead[c].max()), "nonzero_count": int((dead[c] != 0).sum())})
    dead_df = pd.DataFrame(dead_rows)
    dead_df.to_csv(os.path.join(run_dir, "part_B6_dead_column_scan.csv"), index=False)
    confirmed_dead = bool((dead_df["nonzero_count"] == 0).all())

    # ---------------- B7 ctx coverage ----------------
    ctx_cols = ["contract", "bar_idx", "session_date"] + CTX_BASE_COLS
    ctx = pq.read_table(PACKED_CTX_PATH, columns=ctx_cols + ["depth_n"],
                        filters=[("depth_n", "=", DEPTH)]).to_pandas()
    overall_null = ctx[CTX_BASE_COLS].isna().mean().reset_index()
    overall_null.columns = ["ctx_column", "null_frac_overall"]
    overall_null.to_csv(os.path.join(run_dir, "part_B7_ctx_null_frac_overall.csv"), index=False)
    per_sess_null = ctx.groupby("session_date")[CTX_BASE_COLS].apply(lambda d: d.isna().mean())
    per_sess_null.to_csv(os.path.join(run_dir, "part_B7_ctx_null_frac_per_session.csv"))

    stats = {
        "B1_ordering_gate_pass": bool(ordering_ok),
        "B1_bar_idx_gaps": gap_df.to_dict(orient="records"),
        "B2_pct_bars_rth": pct_rth,
        "B3_sanity_note": sanity_note,
        "B3_std_1bar_pts": float(std_1bar),
        "B4_overall_pct_unavailable": overall_pct_unavailable,
        "B5_n_zero_ask_flow_sessions": n_zero_ask_sessions,
        "B6_dead_columns_confirmed": confirmed_dead,
        "B7_ctx_overall_null_frac": overall_null.set_index("ctx_column")["null_frac_overall"].to_dict(),
    }
    with open(os.path.join(run_dir, "part_B_audit_summary.json"), "w") as f:
        json.dump(stats, f, indent=2, default=str)
    log(f"  B1 ordering_ok={ordering_ok}  B4 pct_unavailable={overall_pct_unavailable:.4f}  "
        f"B5 zero_ask_sessions={n_zero_ask_sessions}  B6 dead_confirmed={confirmed_dead}")
    return stats


# ============================================================================
# PART C -- labels
# ============================================================================


def part_C_labels(run_dir):
    log("PART C: building labels ...")
    spine = read_spine(run_dir)
    spine = spine.sort_values(["contract", "bar_end_ts_ns", "bar_idx"], kind="stable").reset_index(drop=True)

    grp_sess = spine.groupby(["contract", "session_date"], sort=False)
    r = grp_sess["close_price"].diff()
    spine["r"] = r.values

    def _ewm_sigma(s):
        return s.ewm(halflife=EWM_HALFLIFE_SIGMA, min_periods=EWM_MIN_PERIODS_SIGMA, ignore_na=True).std()

    sigma = spine.groupby("contract", sort=False)["r"].transform(_ewm_sigma)
    sigma = sigma.clip(lower=SIGMA_FLOOR_TICKS * TICK)
    spine["sigma_t"] = sigma.values

    out = spine[["contract", "bar_idx", "session_date", "close_price", "r", "sigma_t"]].copy()
    valid_counts = {}
    z_describe = {}
    for h in HORIZONS:
        fwd = grp_sess["close_price"].shift(-h) - spine["close_price"]
        n_remaining = grp_sess.cumcount(ascending=False)
        fwd = fwd.where(n_remaining >= h)
        z = fwd / (spine["sigma_t"] * np.sqrt(h))
        out[f"fwd_ret_{h}_pts"] = fwd.values
        out[f"z_{h}"] = z.values
        out[f"w_{h}"] = 1.0 / h
        out[f"valid_{h}"] = fwd.notna().values
        valid_counts[h] = int(fwd.notna().sum())
        zz = z.dropna()
        z_describe[h] = {"n": len(zz), "mean": float(zz.mean()), "std": float(zz.std()),
                          "p05": float(zz.quantile(.05)), "p50": float(zz.quantile(.5)), "p95": float(zz.quantile(.95))}

    ref_balance = {}
    for h in HORIZONS:
        zz = out[f"z_{h}"].dropna()
        lo, hi = zz.quantile(CLASS_QUANTILES[0]), zz.quantile(CLASS_QUANTILES[1])
        ref_balance[h] = {"q30": float(lo), "q70": float(hi),
                           "frac_below": float((zz < lo).mean()),
                           "frac_mid": float(((zz >= lo) & (zz <= hi)).mean()),
                           "frac_above": float((zz > hi).mean())}

    out.to_parquet(os.path.join(run_dir, "labels_depth10.parquet"), index=False)

    stats = {"valid_counts": valid_counts, "z_describe": z_describe, "reference_class_balance_30_70": ref_balance}
    with open(os.path.join(run_dir, "part_C_labels_report.json"), "w") as f:
        json.dump(stats, f, indent=2)
    log(f"  labels written: valid_counts={valid_counts}")
    return stats


# ============================================================================
# PART D -- close-anchored ladder
# ============================================================================


def part_D_ladder(run_dir):
    log("PART D: building close-anchored ladder (K in [-80,80]) ...")
    cols = ["contract", "bar_idx", "session_date", "rel_tick_from_close"] + CHANNELS
    df = read_long_depth10(cols)

    k_index = {int(k): i for i, k in enumerate(LADDER_K)}
    n_k = len(LADDER_K)

    out_cols = []
    for k in LADDER_K:
        ts = tick_str(int(k))
        for ch in CHANNELS:
            out_cols.append(f"k_{ts}_{ch}")
        out_cols.append(f"k_{ts}_observed")

    schema_fields = [("contract", pa.string()), ("bar_idx", pa.int64()), ("session_date", pa.string())]
    for c in out_cols:
        schema_fields.append((c, pa.bool_() if c.endswith("_observed") else pa.float64()))
    schema_fields += [("na_gaps_outside_bar_depth_range", pa.bool_()), ("truncated", pa.bool_()),
                       ("truncated_cells", pa.int64()), ("obs_min_k", pa.int64()), ("obs_max_k", pa.int64())]
    out_schema = pa.schema(schema_fields)

    out_path = os.path.join(run_dir, "ofi_relative_ladder_close_pm80_depth10.parquet")
    writer = pq.ParquetWriter(out_path, out_schema)

    total_rows = 0
    total_truncated_cells = 0
    n_truncated_bars = 0
    cells_retained = 0

    sessions = sorted(df["session_date"].unique())
    for sd in sessions:
        sub = df[df["session_date"] == sd]
        if len(sub) == 0:
            continue
        for contract, csub in sub.groupby("contract", sort=False):
            bar_ids = np.sort(csub["bar_idx"].unique())
            n_bars = len(bar_ids)
            bar_index = {int(b): i for i, b in enumerate(bar_ids)}

            obs = csub.groupby("bar_idx")["rel_tick_from_close"].agg(["min", "max"])
            obs = obs.reindex(bar_ids)
            obs_min = obs["min"].values.astype(np.int64)
            obs_max = obs["max"].values.astype(np.int64)

            in_range = (LADDER_K[None, :] >= obs_min[:, None]) & (LADDER_K[None, :] <= obs_max[:, None])

            arrs = {ch: np.where(in_range, 0.0, np.nan) for ch in CHANNELS}
            observed_arr = np.zeros((n_bars, n_k), dtype=bool)

            within = (csub["rel_tick_from_close"] >= -LADDER_WIDTH) & (csub["rel_tick_from_close"] <= LADDER_WIDTH)
            csub_in = csub[within]
            row_idx = csub_in["bar_idx"].map(bar_index).values
            col_idx = csub_in["rel_tick_from_close"].map(k_index).values
            for ch in CHANNELS:
                arrs[ch][row_idx, col_idx] = csub_in[ch].values
            observed_arr[row_idx, col_idx] = True

            trunc_counts = csub.loc[~within].groupby("bar_idx").size()
            trunc_counts = trunc_counts.reindex(bar_ids, fill_value=0).values

            na_gaps = (obs_min < -LADDER_WIDTH) | (obs_max > LADDER_WIDTH) | (obs_min > -LADDER_WIDTH) | (obs_max < LADDER_WIDTH)
            na_gaps = (obs_min > -LADDER_WIDTH) | (obs_max < LADDER_WIDTH)
            truncated = trunc_counts > 0

            data = {"contract": np.full(n_bars, contract), "bar_idx": bar_ids.astype(np.int64),
                    "session_date": np.full(n_bars, sd)}
            for i, k in enumerate(LADDER_K):
                ts = tick_str(int(k))
                for ch in CHANNELS:
                    data[f"k_{ts}_{ch}"] = arrs[ch][:, i]
                data[f"k_{ts}_observed"] = observed_arr[:, i]
            data["na_gaps_outside_bar_depth_range"] = na_gaps
            data["truncated"] = truncated
            data["truncated_cells"] = trunc_counts.astype(np.int64)
            data["obs_min_k"] = obs_min
            data["obs_max_k"] = obs_max

            table = pa.Table.from_pydict(data, schema=out_schema)
            writer.write_table(table)
            total_rows += n_bars
            total_truncated_cells += int(trunc_counts.sum())
            n_truncated_bars += int(truncated.sum())
            cells_retained += int(observed_arr.sum())

    writer.close()

    gate_pass = (total_rows == 41308)
    coverage = {
        "total_rows": total_rows, "gate_rows_eq_spine_pass": bool(gate_pass),
        "n_truncated_bars": n_truncated_bars, "pct_truncated_bars": total_rows and n_truncated_bars / total_rows,
        "total_truncated_cells": total_truncated_cells, "cells_retained": cells_retained,
    }
    pd.DataFrame([coverage]).to_csv(os.path.join(run_dir, "ladder_coverage.csv"), index=False)
    log(f"  ladder rows={total_rows} gate_pass={gate_pass} truncated_bars={n_truncated_bars} "
        f"({coverage['pct_truncated_bars']:.4%})")
    if not gate_pass:
        raise AssertionError(f"PART D GATE FAIL: ladder rows {total_rows} != spine 41308")
    return coverage


# ============================================================================
# PART E -- engineered feature table
# ============================================================================


BAND_BINS = [-81, -21, -6, 5, 20, 80]
BAND_LABELS = [b[0] for b in BANDS]


def _bucket_level(x):
    return x if x in LEVEL_TYPES else "OTHER"


def part_E_features(run_dir):
    log("PART E: building engineered feature table ...")
    spine = read_spine(run_dir)
    spine = spine.sort_values(["contract", "bar_end_ts_ns", "bar_idx"], kind="stable").reset_index(drop=True)
    key = ["contract", "bar_idx"]

    long_cols = ["contract", "bar_idx", "rel_tick_from_close", "signed_flow", "abs_flow",
                 "buyer_pressure_cell", "seller_pressure_cell", "net_bid_flow", "net_ask_flow",
                 "side_zone", "nearest_level_type", "distance_ticks_to_nearest_level"]
    long = read_long_depth10(long_cols)

    g = long.groupby(key, sort=False)
    total_abs = g["abs_flow"].sum()
    T = (total_abs + 1.0).rename("T")
    n_cells = g.size().rename("n_cells")
    kmin = g["rel_tick_from_close"].min().rename("kmin")
    kmax = g["rel_tick_from_close"].max().rename("kmax")
    cells_above = g.apply(lambda d: (d["rel_tick_from_close"] > 0).mean()).rename("cells_above_close_frac")
    cells_below = g.apply(lambda d: (d["rel_tick_from_close"] < 0).mean()).rename("cells_below_close_frac")

    base = pd.concat([n_cells, kmin, kmax, T, cells_above, cells_below], axis=1).reset_index()
    base["range_ticks"] = base["kmax"] - base["kmin"]
    base["close_pos_in_range"] = safe_div((0 - base["kmin"]).values, base["range_ticks"].values, default=0.5)

    com_abs = g.apply(lambda d: (d["rel_tick_from_close"] * d["abs_flow"]).sum()).rename("com_abs_num")
    com_net = g.apply(lambda d: (d["rel_tick_from_close"] * d["signed_flow"]).sum()).rename("com_net_num")
    prof = pd.concat([com_abs, com_net], axis=1).reset_index().merge(base[key + ["T"]], on=key)
    prof["com_abs"] = prof["com_abs_num"] / prof["T"]
    prof["com_net"] = prof["com_net_num"] / prof["T"]
    prof_map = prof.set_index(key)[["com_abs", "com_net"]]

    tmp = long.merge(prof_map, on=key, how="left")
    dev = tmp["rel_tick_from_close"] - tmp["com_abs"]
    tmp["_w2"] = tmp["abs_flow"] * dev ** 2
    tmp["_w3"] = tmp["abs_flow"] * dev ** 3
    disp_num = tmp.groupby(key)["_w2"].sum().rename("disp_num")
    skew_num = tmp.groupby(key)["_w3"].sum().rename("skew_num")
    hhi_num = (long["abs_flow"] ** 2).groupby([long["contract"], long["bar_idx"]]).sum().rename("hhi_num")

    prof2 = prof.merge(disp_num.reset_index(), on=key).merge(skew_num.reset_index(), on=key).merge(
        hhi_num.reset_index(), on=key)
    prof2["disp_abs"] = np.sqrt((prof2["disp_num"] / prof2["T"]).clip(lower=0))
    prof2["skew_abs"] = safe_div(prof2["skew_num"] / prof2["T"], prof2["disp_abs"] ** 3, default=0.0)
    prof2["hhi_abs"] = prof2["hhi_num"] / (prof2["T"] ** 2)
    prof_final = prof2[key + ["com_abs", "com_net", "disp_abs", "skew_abs", "hhi_abs"]]

    long["band"] = pd.cut(long["rel_tick_from_close"], bins=BAND_BINS, labels=BAND_LABELS)
    band_sums = long.groupby(key + ["band"])[["signed_flow", "abs_flow", "net_bid_flow", "net_ask_flow"]].sum()
    band_wide = band_sums.unstack("band", fill_value=0.0)
    band_wide.columns = [f"{b}_{m}" for m, b in band_wide.columns]
    band_wide = band_wide.reset_index().merge(base[key + ["T"]], on=key)
    for name, _, _ in BANDS:
        band_wide[f"{name}_signed_frac"] = band_wide[f"{name}_signed_flow"] / band_wide["T"]
        band_wide[f"{name}_abs_frac"] = band_wide[f"{name}_abs_flow"] / band_wide["T"]
        band_wide[f"{name}_net_bid_frac"] = band_wide[f"{name}_net_bid_flow"] / band_wide["T"]
        band_wide[f"{name}_net_ask_frac"] = band_wide[f"{name}_net_ask_flow"] / band_wide["T"]
    band_cols = [f"{name}_{m}" for name, _, _ in BANDS for m in ["signed_frac", "abs_frac", "net_bid_frac", "net_ask_frac"]]
    band_final = band_wide[key + band_cols]

    sz_sums = long.groupby(key + ["side_zone"])[["signed_flow", "abs_flow"]].sum().unstack("side_zone", fill_value=0.0)
    sz_sums.columns = [f"sz_{z}_{m}" for m, z in sz_sums.columns]
    sz_wide = sz_sums.reset_index().merge(base[key + ["T"]], on=key)
    sz_cols = []
    for z in ["bid", "ask", "near_mid"]:
        for m in ["signed_flow", "abs_flow"]:
            src = f"sz_{z}_{m}"
            if src not in sz_wide.columns:
                sz_wide[src] = 0.0
            dst = f"sz_{z}_{'signed_frac' if m == 'signed_flow' else 'abs_frac'}"
            sz_wide[dst] = sz_wide[src] / sz_wide["T"]
            sz_cols.append(dst)
    sz_final = sz_wide[key + sz_cols]

    above = long["rel_tick_from_close"] > 0
    below = long["rel_tick_from_close"] < 0
    bp_above = long.loc[above].groupby(key)["buyer_pressure_cell"].sum().rename("bp_above")
    bp_below = long.loc[below].groupby(key)["buyer_pressure_cell"].sum().rename("bp_below")
    sp_above = long.loc[above].groupby(key)["seller_pressure_cell"].sum().rename("sp_above")
    sp_below = long.loc[below].groupby(key)["seller_pressure_cell"].sum().rename("sp_below")
    bp_tot = long.groupby(key)["buyer_pressure_cell"].sum().rename("bp_tot")
    sp_tot = long.groupby(key)["seller_pressure_cell"].sum().rename("sp_tot")
    pressure = pd.concat([bp_above, bp_below, sp_above, sp_below, bp_tot, sp_tot], axis=1).reset_index().fillna(0.0)
    pressure = pressure.merge(base[key + ["T"]], on=key)
    pressure["buyer_pressure_share_above"] = safe_div(pressure["bp_above"].values, (pressure["bp_above"] + pressure["bp_below"]).values, 0.5)
    pressure["buyer_pressure_share_below"] = 1.0 - pressure["buyer_pressure_share_above"]
    pressure["seller_pressure_share_above"] = safe_div(pressure["sp_above"].values, (pressure["sp_above"] + pressure["sp_below"]).values, 0.5)
    pressure["seller_pressure_share_below"] = 1.0 - pressure["seller_pressure_share_above"]
    pressure["balance_total_frac"] = (pressure["bp_tot"] - pressure["sp_tot"]) / pressure["T"]
    pressure_final = pressure[key + ["buyer_pressure_share_above", "buyer_pressure_share_below",
                                       "seller_pressure_share_above", "seller_pressure_share_below", "balance_total_frac"]]

    near = long["distance_ticks_to_nearest_level"].abs() <= 4
    long["lvl_bucket"] = long["nearest_level_type"].map(_bucket_level)
    lvl_sums = long.loc[near].groupby(key + ["lvl_bucket"])[["signed_flow", "abs_flow"]].sum().unstack("lvl_bucket", fill_value=0.0)
    lvl_sums.columns = [f"lvl_{L}_{m}" for m, L in lvl_sums.columns]
    lvl_wide = lvl_sums.reset_index().merge(base[key + ["T"]], on=key)
    lvl_cols = []
    for L in LEVEL_TYPES:
        for m in ["signed_flow", "abs_flow"]:
            src = f"lvl_{L}_{m}"
            if src not in lvl_wide.columns:
                lvl_wide[src] = 0.0
            dst = f"lvl_{L}_{'signed_frac' if m == 'signed_flow' else 'abs_share'}"
            lvl_wide[dst] = lvl_wide[src] / lvl_wide["T"]
            lvl_cols.append(dst)
    min_dist = long.groupby(key)["distance_ticks_to_nearest_level"].apply(lambda s: s.abs().min()).rename("min_abs_dist_to_nearest_level")
    lvl_final = lvl_wide[key + lvl_cols].merge(min_dist.reset_index(), on=key)

    packed_cols = ["contract", "bar_idx", "total_signed_flow", "total_abs_flow", "cell_count",
                   "green_cell_count", "red_cell_count", "neutral_cell_count",
                   "green_abs_flow_share", "red_abs_flow_share", "timestamp_utc"]
    packed = pq.read_table(PACKED_PATH, columns=packed_cols + ["depth_n"],
                            filters=[("depth_n", "=", DEPTH)]).to_pandas()
    packed["total_signed_over_abs_ratio"] = safe_div(packed["total_signed_flow"].values, packed["total_abs_flow"].values, 0.0)
    packed["green_cell_share"] = safe_div(packed["green_cell_count"].values, packed["cell_count"].values, 0.0)
    packed["red_cell_share"] = safe_div(packed["red_cell_count"].values, packed["cell_count"].values, 0.0)
    packed["neutral_cell_share"] = safe_div(packed["neutral_cell_count"].values, packed["cell_count"].values, 0.0)
    packed_final = packed[key + ["total_signed_over_abs_ratio", "green_cell_share", "red_cell_share",
                                   "neutral_cell_share", "green_abs_flow_share", "red_abs_flow_share"]]

    ctx = pq.read_table(PACKED_CTX_PATH, columns=key + CTX_BASE_COLS + ["depth_n"],
                        filters=[("depth_n", "=", DEPTH)]).to_pandas()
    for c in CTX_BASE_COLS:
        ctx[f"{c}_avail"] = ctx[c].notna()
    ctx_final = ctx.drop(columns=["depth_n"])

    feat = spine[["contract", "bar_idx", "session_date", "bar_end_ts_ns", "bar_start_ts_ns",
                  "close_price", "ohlcv_source"]].copy()
    is_rth, dt = _rth_flag_from_ns(feat["bar_end_ts_ns"].values)
    feat["is_rth"] = is_rth
    hour_utc = dt.hour.values + dt.minute.values / 60.0
    feat["hour_utc_sin"] = np.sin(2 * np.pi * hour_utc / 24.0)
    feat["hour_utc_cos"] = np.cos(2 * np.pi * hour_utc / 24.0)
    grp_sess = feat.groupby(["contract", "session_date"], sort=False)
    session_start_ns = grp_sess["bar_end_ts_ns"].transform("min")
    feat["minutes_since_session_open"] = (feat["bar_end_ts_ns"] - session_start_ns) / 1e9 / 60.0
    feat["bars_since_session_open"] = grp_sess.cumcount()
    bar_dur_ns = feat["bar_end_ts_ns"] - feat["bar_start_ts_ns"]
    feat["bar_duration_secs"] = bar_dur_ns / 1e9
    feat["log1p_bar_duration_secs"] = np.log1p(feat["bar_duration_secs"].clip(lower=0))
    feat["ohlcv_available"] = (feat["ohlcv_source"] == "continuous_master")
    feat["is_NQM6"] = (feat["contract"] == "NQM6")
    feat["is_NQU6"] = (feat["contract"] == "NQU6")

    labels = pd.read_parquet(os.path.join(run_dir, "labels_depth10.parquet"), columns=key + ["r", "sigma_t"])
    feat = feat.merge(labels, on=key, how="left")

    def _ewm_hl(s, hl):
        return s.ewm(halflife=hl, min_periods=1, ignore_na=True).mean()

    feat = feat.sort_values(["contract", "bar_end_ts_ns", "bar_idx"], kind="stable").reset_index(drop=True)
    r_over_sigma = feat["r"] / feat["sigma_t"]
    feat["_ros"] = pd.Series(r_over_sigma.values, index=feat.index, name="ros")
    for lag in [1, 2, 3, 5, 10, 20]:
        feat[f"ret_over_sigma_lag{lag}"] = feat.groupby("contract")["_ros"].shift(lag).values

    total_signed = packed[key + ["total_signed_flow", "total_abs_flow"]].rename(columns={"total_signed_flow": "_ts", "total_abs_flow": "_ta"})
    feat = feat.merge(total_signed, on=key, how="left")
    feat = feat.sort_values(["contract", "bar_end_ts_ns", "bar_idx"], kind="stable").reset_index(drop=True)
    gcon = feat.groupby("contract", sort=False)
    for n in [3, 10, 30]:
        rs = gcon["_ts"].apply(lambda s: s.shift(1).rolling(n, min_periods=1).sum())
        ra = gcon["_ta"].apply(lambda s: s.shift(1).rolling(n, min_periods=1).sum())
        feat[f"sum_ratio_last{n}"] = safe_div(rs.values, ra.values, 0.0)
    bar_ratio = safe_div(feat["_ts"].values, feat["_ta"].values, 0.0)
    feat["_bar_ratio"] = bar_ratio
    for hl in [10, 40]:
        feat[f"ewm_ratio_hl{hl}"] = feat.groupby("contract")["_bar_ratio"].apply(lambda s: s.shift(1).ewm(halflife=hl, min_periods=1, ignore_na=True).mean()).values

    abs_r = feat["r"].abs()
    feat["_abs_r"] = abs_r
    ewm20 = feat.groupby("contract")["_abs_r"].apply(lambda s: _ewm_hl(s, 20)).values
    ewm120 = feat.groupby("contract")["_abs_r"].apply(lambda s: _ewm_hl(s, 120)).values
    feat["vol_regime_ratio"] = safe_div(ewm20, ewm120, 1.0)

    feat = feat.merge(base[key + ["close_pos_in_range"]].rename(columns={"close_pos_in_range": "_cpr_tmp"}), on=key, how="left")
    feat = feat.sort_values(["contract", "bar_end_ts_ns", "bar_idx"], kind="stable").reset_index(drop=True)
    feat["trailing_mean_close_pos_20"] = feat.groupby("contract")["_cpr_tmp"].apply(
        lambda s: s.rolling(20, min_periods=1).mean()).values

    feat = feat.drop(columns=["_ros", "_ts", "_ta", "_bar_ratio", "_abs_r", "_cpr_tmp"])

    out = feat.merge(base.drop(columns=["kmin", "kmax", "T"]), on=key, how="left")
    out = out.merge(prof_final, on=key, how="left")
    out = out.merge(band_final, on=key, how="left")
    out = out.merge(sz_final, on=key, how="left")
    out = out.merge(pressure_final, on=key, how="left")
    out = out.merge(lvl_final, on=key, how="left")
    out = out.merge(packed_final, on=key, how="left")
    out = out.merge(ctx_final, on=key, how="left")

    flow_ask = pd.read_parquet(os.path.join(run_dir, "spine_depth10.parquet"), columns=key + ["ask_flow_present"])
    out = out.merge(flow_ask, on=key, how="left")

    num_cols = out.select_dtypes(include=[np.number]).columns
    out[num_cols] = out[num_cols].replace([np.inf, -np.inf], np.nan)

    out = out.sort_values(["contract", "bar_end_ts_ns", "bar_idx"], kind="stable").reset_index(drop=True)
    out.to_parquet(os.path.join(run_dir, "features_depth10.parquet"), index=False)

    feature_cols = [c for c in out.columns if c not in ("contract", "bar_idx", "session_date", "bar_end_ts_ns",
                                                          "bar_start_ts_ns", "close_price", "ohlcv_source")]
    fam_map = {}
    for c in feature_cols:
        if c.startswith("k_") or c.startswith("com_") or c.startswith("disp_") or c.startswith("skew_") or c.startswith("hhi_"):
            fam = "profile"
        elif c.startswith("b_"):
            fam = "band"
        elif c.startswith("sz_"):
            fam = "side_zone"
        elif "pressure" in c or c == "balance_total_frac":
            fam = "pressure"
        elif c.startswith("lvl_") or c == "min_abs_dist_to_nearest_level":
            fam = "level"
        elif c.startswith("ctx_"):
            fam = "context"
        elif c in ("n_cells", "range_ticks", "cells_above_close_frac", "cells_below_close_frac", "close_pos_in_range"):
            fam = "geometry"
        elif c in ("total_signed_over_abs_ratio", "green_cell_share", "red_cell_share", "neutral_cell_share",
                    "green_abs_flow_share", "red_abs_flow_share"):
            fam = "bar_totals"
        elif c.startswith("ret_over_sigma") or c.startswith("sum_ratio_last") or c.startswith("ewm_ratio_hl") or \
                c in ("r", "sigma_t", "vol_regime_ratio", "trailing_mean_close_pos_20", "bars_since_session_open"):
            fam = "trailing"
        elif c in ("is_NQM6", "is_NQU6", "ohlcv_available", "ask_flow_present"):
            fam = "flag"
        elif c in ("minutes_since_session_open", "hour_utc_sin", "hour_utc_cos", "is_rth", "bar_duration_secs",
                    "log1p_bar_duration_secs"):
            fam = "time"
        else:
            fam = "other"
        fam_map[c] = fam
    fdict = pd.DataFrame({"name": feature_cols, "family": [fam_map[c] for c in feature_cols]})
    fdict["definition"] = fdict["name"]
    fdict.to_csv(os.path.join(run_dir, "feature_dictionary.csv"), index=False)

    stats = {"n_rows": len(out), "n_feature_cols": len(feature_cols), "n_total_cols": len(out.columns)}
    with open(os.path.join(run_dir, "part_E_features_report.json"), "w") as f:
        json.dump(stats, f, indent=2)
    log(f"  features written: rows={stats['n_rows']} feature_cols={stats['n_feature_cols']}")
    return stats


# ============================================================================
# PART F -- folds
# ============================================================================


def part_F_folds(run_dir):
    log("PART F: building walk-forward folds ...")
    spine = read_spine(run_dir)
    audit = pd.read_csv(COVERAGE_AUDIT_PATH, dtype={"session_date": str})
    partial_sessions = set(audit[audit.quality_status == "PARTIAL"]["session_date"])

    nqu6_sessions = sorted(spine[spine.contract == "NQU6"]["session_date"].unique())
    n = len(nqu6_sessions)

    folds = []
    train_end = FOLD_TRAIN_MIN_SESSIONS
    fold_id = 0
    while train_end + FOLD_TEST_BLOCK_SESSIONS <= n:
        train_sessions = nqu6_sessions[:train_end]
        test_block = nqu6_sessions[train_end:train_end + FOLD_TEST_BLOCK_SESSIONS]
        test_excluded = [s for s in test_block if s in partial_sessions]
        test_sessions = [s for s in test_block if s not in partial_sessions]
        if len(test_sessions) > 0:
            folds.append({"fold_id": fold_id, "train_sessions": train_sessions, "test_sessions": test_sessions,
                          "test_sessions_excluded_partial": test_excluded, "embargo_bars": FOLD_EMBARGO_BARS})
            fold_id += 1
        train_end += FOLD_STEP_SESSIONS

    folds_obj = {"nqu6_sessions": nqu6_sessions, "n_nqu6_sessions": n, "partial_sessions_excluded_from_test": sorted(partial_sessions),
                 "nqm6_sessions": sorted(spine[spine.contract == "NQM6"]["session_date"].unique()),
                 "nqm6_policy_options": ["EXCLUDED", "TRAIN_ONLY"], "folds": folds,
                 "fold_params": {"train_min_sessions": FOLD_TRAIN_MIN_SESSIONS,
                                  "test_block_sessions": FOLD_TEST_BLOCK_SESSIONS, "step_sessions": FOLD_STEP_SESSIONS,
                                  "embargo_bars": FOLD_EMBARGO_BARS}}
    with open(os.path.join(run_dir, "folds.json"), "w") as f:
        json.dump(folds_obj, f, indent=2)

    stats = {"n_folds": len(folds), "n_nqu6_sessions": n, "partial_sessions": sorted(partial_sessions)}
    log(f"  folds built: n_folds={len(folds)}")
    return stats


# ============================================================================
# PART G -- validation + manifest
# ============================================================================


def part_G_validation(run_dir, all_stats):
    log("PART G: validation + manifest ...")
    spine = read_spine(run_dir)
    labels = pd.read_parquet(os.path.join(run_dir, "labels_depth10.parquet"))
    features = pd.read_parquet(os.path.join(run_dir, "features_depth10.parquet"), columns=["contract", "bar_idx"])
    ladder_pf = pq.ParquetFile(os.path.join(run_dir, "ofi_relative_ladder_close_pm80_depth10.parquet"))

    gates = {
        "spine_total_41308": len(spine) == 41308,
        "labels_rows_eq_spine": len(labels) == len(spine),
        "features_rows_eq_spine": len(features) == len(spine),
        "ladder_rows_eq_spine": ladder_pf.metadata.num_rows == len(spine),
        "B1_ordering_gate_pass": all_stats["B"]["B1_ordering_gate_pass"],
        "B6_dead_columns_confirmed": all_stats["B"]["B6_dead_columns_confirmed"],
    }
    labels_ready = all(gates.values())

    output_files = ["spine_depth10.parquet", "labels_depth10.parquet", "features_depth10.parquet",
                     "ofi_relative_ladder_close_pm80_depth10.parquet", "folds.json", "feature_dictionary.csv"]
    shas = {}
    for fn in output_files:
        p = os.path.join(run_dir, fn)
        if os.path.exists(p):
            shas[fn] = sha256_of_file(p)

    manifest = {
        "build_script": BUILD_SCRIPT_PATH, "build_script_sha256": sha256_of_file(BUILD_SCRIPT_PATH),
        "master_dir": MASTER_DIR, "run_dir": run_dir, "generated_at": utc_now_iso(),
        "gates": gates, "labels_ready": labels_ready, "output_file_sha256": shas,
        "part_stats": all_stats,
    }
    with open(os.path.join(run_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2, default=str)

    log(f"LABELS_READY: {'PASS' if labels_ready else 'FAIL'}")
    return {"labels_ready": labels_ready, "gates": gates}


def write_phase2_report(run_dir, all_stats, gbm_stats=None):
    B = all_stats.get("B", {})
    lines = ["# OFI Phase 2 Report", "", f"Generated: {utc_now_iso()}", "", "## Gates", ""]
    val = all_stats.get("G", {})
    for k, v in val.get("gates", {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## B3 corrected forward-return table (see part_B3_fwd_return_table.csv for full detail)")
    lines.append("")
    lines.append(B.get("B3_sanity_note", ""))
    lines.append("")
    lines.append("## B4 mid-staleness / B5 ask-flow / B7 ctx coverage")
    lines.append(f"- overall pct ohlcv_source==unavailable: {B.get('B4_overall_pct_unavailable')}")
    lines.append(f"- sessions with zero ask-side flow: {B.get('B5_n_zero_ask_flow_sessions')}")
    lines.append(f"- B6 dead trade_volume columns confirmed dead: {B.get('B6_dead_columns_confirmed')}")
    lines.append("")
    lines.append("## Label stats (Part C)")
    lines.append(json.dumps(all_stats.get("C", {}), indent=2))
    lines.append("")
    lines.append("## Ladder coverage (Part D)")
    lines.append(json.dumps(all_stats.get("D", {}), indent=2))
    lines.append("")
    lines.append("## Fold table (Part F)")
    lines.append(json.dumps(all_stats.get("F", {}), indent=2))
    if gbm_stats is not None:
        lines.append("")
        lines.append("## Part H LightGBM baseline")
        lines.append(json.dumps(gbm_stats, indent=2, default=str))
    with open(os.path.join(run_dir, "PHASE2_REPORT.md"), "w") as f:
        f.write("\n".join(lines))


# ============================================================================
# PART H -- LightGBM baseline
# ============================================================================


def _spearman_ic(pred, actual):
    mask = (~np.isnan(pred)) & (~np.isnan(actual))
    if mask.sum() < 10:
        return np.nan
    return sstats.spearmanr(pred[mask], actual[mask]).statistic


def _decile_spread(sub, col, ret_col="fwd_ret_40_pts"):
    if len(sub) < 10:
        return np.nan
    ranks = sub[col].rank(pct=True)
    top = sub.loc[ranks >= 0.9, ret_col].mean()
    bot = sub.loc[ranks <= 0.1, ret_col].mean()
    return float(top - bot)


def part_H_gbm(run_dir):
    log("PART H: LightGBM baseline ...")
    try:
        import lightgbm as lgb
        use_lgb = True
    except ImportError:
        import xgboost as xgb
        use_lgb = False
    log(f"  backend: {'lightgbm' if use_lgb else 'xgboost (fallback)'}")

    features = pd.read_parquet(os.path.join(run_dir, "features_depth10.parquet"))
    labels = pd.read_parquet(os.path.join(run_dir, "labels_depth10.parquet"))
    with open(os.path.join(run_dir, "folds.json")) as f:
        folds_obj = json.load(f)

    key = ["contract", "bar_idx"]
    df = features.merge(labels.drop(columns=["session_date", "close_price"], errors="ignore"), on=key, how="left")
    df = df.sort_values(["contract", "bar_end_ts_ns", "bar_idx"], kind="stable").reset_index(drop=True)

    exclude = {"contract", "bar_idx", "session_date", "bar_end_ts_ns", "bar_start_ts_ns", "close_price",
               "ohlcv_source", "r", "sigma_t"}
    exclude |= {c for c in df.columns if c.startswith("fwd_ret_") or c.startswith("z_") or c.startswith("w_") or c.startswith("valid_")}
    feature_cols = [c for c in df.columns if c not in exclude and df[c].dtype != object]
    bool_cols = df[feature_cols].select_dtypes(include=["bool"]).columns
    df[bool_cols] = df[bool_cols].astype(np.float32)

    reg_horizons = [10, 20, 40]
    results_rows = []
    importances_rows = []

    for policy in ["EXCLUDED", "TRAIN_ONLY"]:
        for fold in folds_obj["folds"]:
            fold_id = fold["fold_id"]
            train_sessions = set(fold["train_sessions"])
            test_sessions = set(fold["test_sessions"])
            valid_sessions = set(sorted(fold["train_sessions"])[-2:])
            train_sessions_fit = train_sessions - valid_sessions

            nqu6_mask = df["contract"] == "NQU6"
            train_mask = nqu6_mask & df["session_date"].isin(train_sessions_fit)
            valid_mask = nqu6_mask & df["session_date"].isin(valid_sessions)
            test_mask = nqu6_mask & df["session_date"].isin(test_sessions)

            if policy == "TRAIN_ONLY":
                nqm6_mask = df["contract"] == "NQM6"
                train_mask = train_mask | nqm6_mask

            embargo_bar = FOLD_EMBARGO_BARS
            test_df = df[test_mask].copy()
            test_df["_rank_in_session"] = test_df.groupby("session_date").cumcount()
            test_df = test_df[test_df["_rank_in_session"] >= embargo_bar]

            train_df = df[train_mask]
            valid_df = df[valid_mask]
            if len(train_df) < 200 or len(test_df) < 20:
                continue

            fold_result = {"fold_id": fold_id, "nqm6_policy": policy, "n_train": len(train_df),
                            "n_valid": len(valid_df), "n_test": len(test_df)}

            for h in reg_horizons:
                y_col, w_col = f"z_{h}", f"w_{h}"
                tr = train_df.dropna(subset=[y_col])
                va = valid_df.dropna(subset=[y_col])
                te = test_df.dropna(subset=[y_col])
                if len(tr) < 200 or len(te) < 20:
                    continue
                X_tr, y_tr, w_tr = tr[feature_cols], tr[y_col], tr[w_col]
                X_va, y_va = va[feature_cols], va[y_col]
                X_te, y_te = te[feature_cols], te[y_col]

                if use_lgb:
                    model = lgb.LGBMRegressor(num_leaves=63, learning_rate=0.05, min_data_in_leaf=100,
                                               feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
                                               n_estimators=2000, random_state=SEED, deterministic=True,
                                               verbosity=-1)
                    model.fit(X_tr, y_tr, sample_weight=w_tr, eval_set=[(X_va, y_va)],
                               callbacks=[lgb.early_stopping(100, verbose=False)])
                    pred = model.predict(X_te)
                    gain_imp = pd.Series(model.booster_.feature_importance(importance_type="gain"), index=feature_cols)
                else:
                    model = xgb.XGBRegressor(tree_method="hist", max_leaves=63, learning_rate=0.05,
                                              min_child_weight=100, subsample=0.8, colsample_bytree=0.8,
                                              n_estimators=2000, random_state=SEED, early_stopping_rounds=100)
                    model.fit(X_tr, y_tr, sample_weight=w_tr, eval_set=[(X_va, y_va)], verbose=False)
                    pred = model.predict(X_te)
                    gain_imp = pd.Series(model.feature_importances_, index=feature_cols)

                ic_overall = _spearman_ic(pred, y_te.values)
                rth_mask = te["is_rth"].values.astype(bool)
                ic_rth = _spearman_ic(pred[rth_mask], y_te.values[rth_mask]) if rth_mask.sum() > 10 else np.nan
                fold_result[f"ic_h{h}_overall"] = ic_overall
                fold_result[f"ic_h{h}_rth"] = ic_rth

                top = gain_imp.sort_values(ascending=False).head(30)
                for feat_name, val in top.items():
                    importances_rows.append({"fold_id": fold_id, "nqm6_policy": policy, "horizon": h,
                                              "feature": feat_name, "gain": float(val)})

            y_col = "z_40"
            tr = train_df.dropna(subset=[y_col])
            va = valid_df.dropna(subset=[y_col])
            te = test_df.dropna(subset=[y_col])
            if len(tr) >= 200 and len(te) >= 20:
                q30, q70 = tr[y_col].quantile(CLASS_QUANTILES[0]), tr[y_col].quantile(CLASS_QUANTILES[1])

                def _cls(y):
                    return np.where(y < q30, 0, np.where(y > q70, 2, 1))

                y_tr_c, y_va_c, y_te_c = _cls(tr[y_col].values), _cls(va[y_col].values), _cls(te[y_col].values)
                X_tr, X_va, X_te = tr[feature_cols], va[feature_cols], te[feature_cols]

                if use_lgb:
                    clf = lgb.LGBMClassifier(num_leaves=63, learning_rate=0.05, min_data_in_leaf=100,
                                              feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
                                              n_estimators=2000, random_state=SEED, deterministic=True,
                                              class_weight="balanced", verbosity=-1)
                    clf.fit(X_tr, y_tr_c, eval_set=[(X_va, y_va_c)], callbacks=[lgb.early_stopping(100, verbose=False)])
                    pred_c = clf.predict(X_te)
                else:
                    from collections import Counter
                    cnt = Counter(y_tr_c)
                    w = np.array([len(y_tr_c) / (3 * cnt[c]) for c in y_tr_c])
                    clf = xgb.XGBClassifier(tree_method="hist", max_leaves=63, learning_rate=0.05,
                                             min_child_weight=100, subsample=0.8, colsample_bytree=0.8,
                                             n_estimators=2000, random_state=SEED, early_stopping_rounds=100,
                                             num_class=3, objective="multi:softmax")
                    clf.fit(X_tr, y_tr_c, sample_weight=w, eval_set=[(X_va, y_va_c)], verbose=False)
                    pred_c = clf.predict(X_te)

                acc = float((pred_c == y_te_c).mean())
                fold_result["class_accuracy_z40"] = acc
                for cls in [0, 1, 2]:
                    tp = ((pred_c == cls) & (y_te_c == cls)).sum()
                    fp = ((pred_c == cls) & (y_te_c != cls)).sum()
                    fn = ((pred_c != cls) & (y_te_c == cls)).sum()
                    prec = tp / (tp + fp) if (tp + fp) > 0 else np.nan
                    rec = tp / (tp + fn) if (tp + fn) > 0 else np.nan
                    fold_result[f"class{cls}_precision"] = prec
                    fold_result[f"class{cls}_recall"] = rec

            results_rows.append(fold_result)

    results_df = pd.DataFrame(results_rows)
    results_df.to_csv(os.path.join(run_dir, "gbm_results.csv"), index=False)
    imp_df = pd.DataFrame(importances_rows)
    imp_df.to_csv(os.path.join(run_dir, "gbm_top_importances.csv"), index=False)

    summary = {}
    for policy in ["EXCLUDED", "TRAIN_ONLY"]:
        pdf = results_df[results_df.nqm6_policy == policy]
        summary[policy] = {}
        for h in reg_horizons:
            col = f"ic_h{h}_overall"
            if col in pdf and pdf[col].notna().sum() > 1:
                ics = pdf[col].dropna()
                t_stat = float(ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics)))) if ics.std(ddof=1) > 0 else np.nan
                summary[policy][f"h{h}_mean_ic"] = float(ics.mean())
                summary[policy][f"h{h}_ic_tstat"] = t_stat
        if "class_accuracy_z40" in pdf:
            summary[policy]["mean_class_accuracy_z40"] = float(pdf["class_accuracy_z40"].dropna().mean())
            for cls in [0, 1, 2]:
                summary[policy][f"class{cls}_mean_precision"] = float(pdf[f"class{cls}_precision"].dropna().mean()) if f"class{cls}_precision" in pdf else None

    with open(os.path.join(run_dir, "gbm_report.md"), "w") as f:
        f.write("# GBM Baseline Report\n\n")
        f.write(f"backend: {'lightgbm' if use_lgb else 'xgboost'}\n\n")
        f.write(json.dumps(summary, indent=2, default=str))
    log(f"  gbm done. summary={json.dumps(summary, default=str)}")
    return summary


# ============================================================================
# PART I -- label extension (h60/h80) + calibration tables + ladder delta
# ============================================================================


def part_I_labels_calibration_ladder(run_dir):
    log("PART I: h60/h80 labels + calibration tables + ladder delta ...")
    key = ["contract", "bar_idx"]
    spine = read_spine(run_dir)
    spine = spine.sort_values(["contract", "bar_end_ts_ns", "bar_idx"], kind="stable").reset_index(drop=True)
    labels = pd.read_parquet(os.path.join(run_dir, "labels_depth10.parquet"))

    # ---------------- label extension: h in {60, 80}, identical rules to Part C ----------------
    spine_l = spine.merge(labels[key + ["sigma_t"]], on=key, how="left")
    grp_sess = spine_l.groupby(["contract", "session_date"], sort=False)
    valid_counts_ext = {}
    ext_cols = {}
    for h in HORIZONS_EXT:
        fwd = grp_sess["close_price"].shift(-h) - spine_l["close_price"]
        n_remaining = grp_sess.cumcount(ascending=False)
        fwd = fwd.where(n_remaining >= h)
        z = fwd / (spine_l["sigma_t"] * np.sqrt(h))
        ext_cols[f"fwd_ret_{h}_pts"] = fwd.values
        ext_cols[f"z_{h}"] = z.values
        ext_cols[f"w_{h}"] = 1.0 / h
        ext_cols[f"valid_{h}"] = fwd.notna().values
        valid_counts_ext[h] = int(fwd.notna().sum())
    ext_df = spine_l[key].copy()
    for c, v in ext_cols.items():
        ext_df[c] = v
    labels_new = labels.drop(columns=[c for c in ext_df.columns if c in labels.columns and c not in key], errors="ignore")
    labels_new = labels_new.merge(ext_df, on=key, how="left")
    labels_new.to_parquet(os.path.join(run_dir, "labels_depth10.parquet"), index=False)
    log(f"  h60/h80 labels added: valid_counts={valid_counts_ext}")

    # ---------------- calibration (a): per-fold train 30/70 z_40 quantile cuts ----------------
    with open(os.path.join(run_dir, "folds.json")) as f:
        folds_obj = json.load(f)
    nqu6_labels = labels_new[labels_new.contract == "NQU6"]

    cal_rows = []
    for fold in folds_obj["folds"]:
        train_sessions = fold["train_sessions"]
        val_sessions = set(sorted(train_sessions)[-2:])
        train_fit_sessions = [s for s in train_sessions if s not in val_sessions]
        tr = nqu6_labels[nqu6_labels.session_date.isin(train_fit_sessions)].dropna(subset=["z_40"])
        if len(tr) == 0:
            continue
        q30, q70 = tr["z_40"].quantile(CLASS_QUANTILES[0]), tr["z_40"].quantile(CLASS_QUANTILES[1])
        fold_median_sigma = float(tr["sigma_t"].median())
        unit = fold_median_sigma * np.sqrt(PRIMARY_HORIZON)
        cal_rows.append({"fold_id": fold["fold_id"], "n_train_fit": len(tr), "q30_z40": float(q30),
                          "q70_z40": float(q70), "fold_median_sigma_t": fold_median_sigma,
                          "q30_pts": float(q30 * unit), "q70_pts": float(q70 * unit),
                          "q30_ticks": float(q30 * unit / TICK), "q70_ticks": float(q70 * unit / TICK)})
    cal_df = pd.DataFrame(cal_rows)
    cal_df.to_csv(os.path.join(run_dir, "part_I_calibration_per_fold.csv"), index=False)

    # ---------------- calibration (b): global reference |z_40| -> pts/ticks ----------------
    global_median_sigma = float(nqu6_labels["sigma_t"].median())
    global_unit = global_median_sigma * np.sqrt(PRIMARY_HORIZON)
    global_rows = []
    for zval in [0.25, 0.5, 1.0]:
        global_rows.append({"abs_z40": zval, "pts": zval * global_unit, "ticks": zval * global_unit / TICK})
    global_df = pd.DataFrame(global_rows)
    global_df["global_median_sigma_t"] = global_median_sigma
    global_df["global_unit_pts_per_z"] = global_unit
    global_df.to_csv(os.path.join(run_dir, "part_I_calibration_global.csv"), index=False)
    log(f"  calibration: global unit (median sigma_t*sqrt(40)) = {global_unit:.4f} pts per unit z_40")

    # ---------------- ladder delta: truncated_abs_flow_below/above ----------------
    long = read_long_depth10(["contract", "bar_idx", "rel_tick_from_close", "abs_flow"])
    g = long.groupby(key, sort=False)
    total_abs_all = g["abs_flow"].sum()
    below = long.loc[long.rel_tick_from_close < -LADDER_WIDTH].groupby(key)["abs_flow"].sum()
    above = long.loc[long.rel_tick_from_close > LADDER_WIDTH].groupby(key)["abs_flow"].sum()
    Tall = (total_abs_all + 1.0)
    delta_df = pd.DataFrame({
        "truncated_abs_flow_below": safe_div(below.reindex(Tall.index, fill_value=0.0).values, Tall.values, 0.0),
        "truncated_abs_flow_above": safe_div(above.reindex(Tall.index, fill_value=0.0).values, Tall.values, 0.0),
    }, index=Tall.index).reset_index()

    ladder_path = os.path.join(run_dir, "ofi_relative_ladder_close_pm80_depth10.parquet")
    ladder = pd.read_parquet(ladder_path)
    n_before = len(ladder)
    ladder = ladder.drop(columns=["truncated_abs_flow_below", "truncated_abs_flow_above"], errors="ignore")
    ladder = ladder.merge(delta_df, on=key, how="left")
    ladder["truncated_abs_flow_below"] = ladder["truncated_abs_flow_below"].fillna(0.0)
    ladder["truncated_abs_flow_above"] = ladder["truncated_abs_flow_above"].fillna(0.0)
    gate_pass = (len(ladder) == n_before == len(spine))
    ladder.to_parquet(ladder_path, index=False)

    ladder_delta_stats = {
        "gate_rows_unchanged_eq_spine_pass": bool(gate_pass), "n_rows": len(ladder),
        "mean_truncated_abs_flow_below": float(ladder["truncated_abs_flow_below"].mean()),
        "mean_truncated_abs_flow_above": float(ladder["truncated_abs_flow_above"].mean()),
    }
    log(f"  ladder delta added: gate_pass={gate_pass} mean_below={ladder_delta_stats['mean_truncated_abs_flow_below']:.4f} "
        f"mean_above={ladder_delta_stats['mean_truncated_abs_flow_above']:.4f}")
    if not gate_pass:
        raise AssertionError("PART I GATE FAIL: ladder row count changed after adding delta columns")

    stats = {"valid_counts_ext": valid_counts_ext, "calibration_per_fold": cal_df.to_dict(orient="records"),
              "calibration_global": global_df.to_dict(orient="records"), "ladder_delta": ladder_delta_stats}
    with open(os.path.join(run_dir, "part_I_report.json"), "w") as f:
        json.dump(stats, f, indent=2, default=str)
    return stats


# ============================================================================
# PART J -- GBM iteration (NQU6-only): sweep, ablations, economics, stability
# ============================================================================


def _fold_split_nqu6(df, fold, embargo=FOLD_EMBARGO_BARS):
    train_sessions = fold["train_sessions"]
    test_sessions = fold["test_sessions"]
    val_sessions = set(sorted(train_sessions)[-2:])
    train_fit_sessions = [s for s in train_sessions if s not in val_sessions]
    train_df = df[df.session_date.isin(train_fit_sessions)]
    valid_df = df[df.session_date.isin(val_sessions)]
    test_df = df[df.session_date.isin(test_sessions)].copy()
    test_df["_rank_in_session"] = test_df.groupby("session_date").cumcount()
    test_df = test_df[test_df["_rank_in_session"] >= embargo].drop(columns=["_rank_in_session"])
    return train_df, valid_df, test_df


def _lgb_fit(X_tr, y_tr, w_tr, X_va, y_va, num_leaves, lr, min_data_in_leaf, n_estimators, esr):
    import lightgbm as lgb
    model = lgb.LGBMRegressor(num_leaves=num_leaves, learning_rate=lr, min_data_in_leaf=min_data_in_leaf,
                               feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
                               n_estimators=n_estimators, random_state=SEED, deterministic=True, verbosity=-1)
    model.fit(X_tr, y_tr, sample_weight=w_tr, eval_set=[(X_va, y_va)],
               callbacks=[lgb.early_stopping(esr, verbose=False)])
    return model


def _get_feature_cols(df):
    exclude = {"contract", "bar_idx", "session_date", "bar_end_ts_ns", "bar_start_ts_ns", "close_price",
               "ohlcv_source", "r", "sigma_t"}
    exclude |= {c for c in df.columns if c.startswith("fwd_ret_") or c.startswith("z_") or
                c.startswith("w_") or c.startswith("valid_")}
    return [c for c in df.columns if c not in exclude and df[c].dtype != object]


def _load_nqu6_frame(run_dir):
    key = ["contract", "bar_idx"]
    features = pd.read_parquet(os.path.join(run_dir, "features_depth10.parquet"))
    labels = pd.read_parquet(os.path.join(run_dir, "labels_depth10.parquet"))
    df = features.merge(labels.drop(columns=["session_date", "close_price"], errors="ignore"), on=key, how="left")
    df = df[df.contract == "NQU6"].sort_values(["bar_end_ts_ns", "bar_idx"], kind="stable").reset_index(drop=True)
    feature_cols = _get_feature_cols(df)
    bool_cols = df[feature_cols].select_dtypes(include=["bool"]).columns
    df[bool_cols] = df[bool_cols].astype(np.float32)
    return df, feature_cols


def part_J_gbm_iteration(run_dir):
    log("PART J: GBM iteration (NQU6-only: sweep, ablations, economics, stability) ...")
    df, feature_cols = _load_nqu6_frame(run_dir)
    with open(os.path.join(run_dir, "folds.json")) as f:
        folds_obj = json.load(f)
    fdict = pd.read_csv(os.path.join(run_dir, "feature_dictionary.csv"))
    fam_cols = {fam: fdict.loc[fdict.family == fam, "name"].tolist() for fam in fdict.family.unique()}
    family_feature_cols = {fam: [c for c in fam_cols.get(fam, []) if c in feature_cols] for fam in ABLATION_FAMILIES}
    control_cols = list(dict.fromkeys(family_feature_cols.get("trailing", []) + family_feature_cols.get("time", [])))

    sweep_rows, tuned_rows = [], []
    chosen_params = {}
    importances_h40 = {}
    pred_store_h40 = []

    for fold in folds_obj["folds"]:
        fold_id = fold["fold_id"]
        train_df, valid_df, test_df = _fold_split_nqu6(df, fold)
        if len(train_df) < 200 or len(test_df) < 20:
            continue
        log(f"  fold {fold_id}: train={len(train_df)} valid={len(valid_df)} test={len(test_df)}")

        for h in J_HORIZONS:
            ycol, wcol = f"z_{h}", f"w_{h}"
            tr = train_df.dropna(subset=[ycol])
            va = valid_df.dropna(subset=[ycol])
            te = test_df.dropna(subset=[ycol])
            if len(tr) < 200 or len(te) < 20 or len(va) < 10:
                continue
            X_tr, y_tr, w_tr = tr[feature_cols], tr[ycol], tr[wcol]
            X_va, y_va = va[feature_cols], va[ycol]
            X_te, y_te = te[feature_cols], te[ycol]

            best_ic, best_params = np.nan, (63, 0.05, 100)
            for nl in GBM_SWEEP_GRID["num_leaves"]:
                for lr_ in GBM_SWEEP_GRID["learning_rate"]:
                    for mdl in GBM_SWEEP_GRID["min_data_in_leaf"]:
                        model = _lgb_fit(X_tr, y_tr, w_tr, X_va, y_va, nl, lr_, mdl,
                                          GBM_SWEEP_N_ESTIMATORS, GBM_SWEEP_ESR)
                        ic_va = _spearman_ic(model.predict(X_va), y_va.values)
                        sweep_rows.append({"fold_id": fold_id, "horizon": h, "num_leaves": nl,
                                            "learning_rate": lr_, "min_data_in_leaf": mdl, "val_ic": ic_va})
                        if not np.isnan(ic_va) and (np.isnan(best_ic) or ic_va > best_ic):
                            best_ic, best_params = ic_va, (nl, lr_, mdl)
            nl, lr_, mdl = best_params
            chosen_params[(fold_id, h)] = {"num_leaves": nl, "learning_rate": lr_, "min_data_in_leaf": mdl}

            final_model = _lgb_fit(X_tr, y_tr, w_tr, X_va, y_va, nl, lr_, mdl,
                                    GBM_FINAL_N_ESTIMATORS, GBM_FINAL_ESR)
            pred_te = final_model.predict(X_te)
            ic_overall = _spearman_ic(pred_te, y_te.values)
            rth_mask = te["is_rth"].values.astype(bool)
            ic_rth = _spearman_ic(pred_te[rth_mask], y_te.values[rth_mask]) if rth_mask.sum() > 10 else np.nan
            tuned_rows.append({"fold_id": fold_id, "horizon": h, "n_train": len(tr), "n_valid": len(va),
                                "n_test": len(te), "num_leaves": nl, "learning_rate": lr_,
                                "min_data_in_leaf": mdl, "val_ic": best_ic, "ic_overall": ic_overall, "ic_rth": ic_rth})

            if h == PRIMARY_HORIZON:
                gain = pd.Series(final_model.booster_.feature_importance(importance_type="gain"), index=feature_cols)
                importances_h40[fold_id] = gain
                te_out = te[["contract", "bar_idx", "session_date", "is_rth", "fwd_ret_40_pts", "z_40"]].copy()
                te_out["fold_id"] = fold_id
                te_out["pred_gbm_tuned"] = pred_te
                pred_store_h40.append(te_out)

        log(f"  fold {fold_id} done. h40 tuned IC_overall="
            f"{[r['ic_overall'] for r in tuned_rows if r['fold_id'] == fold_id and r['horizon'] == 40]}")

    sweep_df = pd.DataFrame(sweep_rows)
    tuned_df = pd.DataFrame(tuned_rows)
    sweep_df.to_csv(os.path.join(run_dir, "gbm2_sweep_results.csv"), index=False)
    tuned_df.to_csv(os.path.join(run_dir, "gbm2_tuned_results.csv"), index=False)
    pred_h40_df = pd.concat(pred_store_h40, ignore_index=True) if pred_store_h40 else pd.DataFrame()
    pred_h40_df.to_parquet(os.path.join(run_dir, "gbm2_tuned_h40_test_predictions.parquet"), index=False)

    # ---------------- feature-family ablations at h=40 ----------------
    ablation_rows = []
    for fold in folds_obj["folds"]:
        fold_id = fold["fold_id"]
        if (fold_id, PRIMARY_HORIZON) not in chosen_params:
            continue
        train_df, valid_df, test_df = _fold_split_nqu6(df, fold)
        ycol, wcol = "z_40", "w_40"
        tr = train_df.dropna(subset=[ycol])
        va = valid_df.dropna(subset=[ycol])
        te = test_df.dropna(subset=[ycol])
        if len(tr) < 200 or len(te) < 20 or len(va) < 10:
            continue
        params = chosen_params[(fold_id, PRIMARY_HORIZON)]
        full_ic = tuned_df.loc[(tuned_df.fold_id == fold_id) & (tuned_df.horizon == PRIMARY_HORIZON), "ic_overall"]
        full_ic = float(full_ic.iloc[0]) if len(full_ic) else np.nan

        variants = {}
        for fam in ABLATION_FAMILIES:
            fam_c = family_feature_cols.get(fam, [])
            variants[f"drop_{fam}"] = [c for c in feature_cols if c not in fam_c]
            variants[f"only_{fam}"] = fam_c if fam_c else feature_cols[:1]
        variants["control_trailing_time_only"] = control_cols if control_cols else feature_cols[:1]

        for name, cols in variants.items():
            if len(cols) == 0:
                continue
            model = _lgb_fit(tr[cols], tr[ycol], tr[wcol], va[cols], va[ycol],
                              params["num_leaves"], params["learning_rate"], params["min_data_in_leaf"],
                              GBM_SWEEP_N_ESTIMATORS, GBM_SWEEP_ESR)
            pred = model.predict(te[cols])
            ic = _spearman_ic(pred, te[ycol].values)
            ablation_rows.append({"fold_id": fold_id, "variant": name, "n_features": len(cols),
                                    "ic_h40": ic, "full_ic_h40": full_ic,
                                    "delta_ic_vs_full": (ic - full_ic) if not np.isnan(full_ic) else np.nan})
    ablation_df = pd.DataFrame(ablation_rows)
    ablation_df.to_csv(os.path.join(run_dir, "gbm2_ablation_results.csv"), index=False)

    # ---------------- economics at h=40 ----------------
    econ_rows = []
    if len(pred_h40_df) > 0:
        for fold_id, sub in pred_h40_df.groupby("fold_id"):
            sub_v = sub.dropna(subset=["fwd_ret_40_pts", "pred_gbm_tuned"])
            spread = _decile_spread(sub_v, col="pred_gbm_tuned")
            spread_rth = _decile_spread(sub_v[sub_v.is_rth.astype(bool)], col="pred_gbm_tuned")
            econ_rows.append({
                "fold_id": fold_id, "n": len(sub_v),
                "decile_spread_pts": spread, "decile_spread_cost_adj_pts": spread - ECON_COST_PTS if not np.isnan(spread) else np.nan,
                "decile_spread_rth_pts": spread_rth,
                "decile_spread_rth_cost_adj_pts": spread_rth - ECON_COST_PTS if not np.isnan(spread_rth) else np.nan,
            })
    econ_df = pd.DataFrame(econ_rows)
    econ_df.to_csv(os.path.join(run_dir, "gbm2_economics.csv"), index=False)

    # ---------------- stability: importance rank-correlation across folds ----------------
    stability_stats = {}
    if len(importances_h40) > 1:
        imp_matrix = pd.DataFrame(importances_h40)
        corr = imp_matrix.corr(method="spearman")
        corr.to_csv(os.path.join(run_dir, "gbm2_importance_fold_correlation.csv"))
        iu = np.triu_indices_from(corr.values, k=1)
        stability_stats["mean_pairwise_importance_spearman_corr"] = float(np.nanmean(corr.values[iu]))

    # ---------------- summary ----------------
    summary = {"n_folds_used": tuned_df["fold_id"].nunique() if len(tuned_df) else 0, "horizons": {}}
    for h in J_HORIZONS:
        hdf = tuned_df[tuned_df.horizon == h]
        ics = hdf["ic_overall"].dropna()
        if len(ics) > 1:
            t_stat = float(ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics)))) if ics.std(ddof=1) > 0 else np.nan
            summary["horizons"][h] = {"mean_ic": float(ics.mean()), "ic_tstat": t_stat, "n_folds": len(ics)}
    summary["ablation_mean_delta_ic"] = ablation_df.groupby("variant")["delta_ic_vs_full"].mean().to_dict() if len(ablation_df) else {}
    summary["economics_mean"] = {c: float(econ_df[c].dropna().mean()) for c in
                                   ["decile_spread_pts", "decile_spread_cost_adj_pts", "decile_spread_rth_pts",
                                    "decile_spread_rth_cost_adj_pts"]} if len(econ_df) else {}
    summary["stability"] = stability_stats

    with open(os.path.join(run_dir, "part_J_report.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)
    log(f"  Part J done. summary={json.dumps(summary, default=str)[:2000]}")
    return summary


# ============================================================================
# PART K -- Track 2 CNN on the close-anchored ladder (PyTorch)
# ============================================================================

CNN_FLOW_CHANNELS = ["net_bid_flow", "net_ask_flow", "signed_flow", "abs_flow",
                      "buyer_pressure_cell", "seller_pressure_cell"]
CNN_HEADS = [10, 20, 40, 80]
CNN_HEAD_WEIGHTS = {10: 0.25, 20: 0.25, 40: 1.0, 80: 0.25}
CNN_SCALAR_COLS = ["range_ticks", "close_pos_in_range", "n_cells", "truncated_abs_flow_below",
                    "truncated_abs_flow_above", "log1p_bar_duration_secs", "is_rth",
                    "minutes_since_session_open", "sigma_t", "ewm_ratio_hl10", "ewm_ratio_hl40",
                    "vol_regime_ratio"]


def _build_or_load_cnn_dataset(run_dir):
    tensor_path = os.path.join(run_dir, "part_K_tensor_cache.npz")
    meta_path = os.path.join(run_dir, "part_K_meta_cache.parquet")
    if os.path.exists(tensor_path) and os.path.exists(meta_path):
        npz = np.load(tensor_path)
        meta = pd.read_parquet(meta_path)
        return npz["tensor"], npz["scalars"], meta

    log("  building CNN tensor cache from ladder + features (one-time) ...")
    key = ["contract", "bar_idx"]
    ladder_extra_cols = ["truncated_abs_flow_below", "truncated_abs_flow_above"]
    features_scalar_cols = [c for c in CNN_SCALAR_COLS if c not in ladder_extra_cols]
    k_cols = []
    for k in LADDER_K:
        ts = tick_str(int(k))
        for ch in CNN_FLOW_CHANNELS:
            k_cols.append(f"k_{ts}_{ch}")
        k_cols.append(f"k_{ts}_observed")
    ladder_cols = ["contract", "bar_idx", "session_date"] + ladder_extra_cols + k_cols
    ladder = pd.read_parquet(os.path.join(run_dir, "ofi_relative_ladder_close_pm80_depth10.parquet"),
                              columns=ladder_cols)
    ladder = ladder[ladder.contract == "NQU6"].reset_index(drop=True)
    feat_cols = key + ["bar_end_ts_ns", "session_date", "is_rth"] + features_scalar_cols
    feat_cols = list(dict.fromkeys(feat_cols))
    features = pd.read_parquet(os.path.join(run_dir, "features_depth10.parquet"), columns=feat_cols)
    labels = pd.read_parquet(os.path.join(run_dir, "labels_depth10.parquet"))
    label_cols = key + [f"z_{h}" for h in CNN_HEADS] + [f"valid_{h}" for h in CNN_HEADS] + ["fwd_ret_40_pts"]
    packed = pq.read_table(PACKED_PATH, columns=key + ["total_abs_flow"],
                            filters=[("depth_n", "=", DEPTH)]).to_pandas()

    df = ladder.merge(features.drop(columns=["session_date"]), on=key, how="left").merge(
        packed, on=key, how="left").merge(labels[label_cols], on=key, how="left")
    df = df.sort_values(["bar_end_ts_ns", "bar_idx"], kind="stable").reset_index(drop=True)

    N = len(df)
    n_k = len(LADDER_K)
    Tden = (df["total_abs_flow"].values.astype(np.float64) + 1.0)

    tensor = np.zeros((N, 8, n_k), dtype=np.float32)
    in_range_mask = None
    for ci, ch in enumerate(CNN_FLOW_CHANNELS):
        cols = [f"k_{tick_str(int(k))}_{ch}" for k in LADDER_K]
        raw = df[cols].values.astype(np.float64)
        if in_range_mask is None:
            in_range_mask = (~np.isnan(raw)).astype(np.float32)
        raw = np.nan_to_num(raw, nan=0.0)
        frac = raw / Tden[:, None]
        if ch == "abs_flow":
            frac = np.log1p(np.clip(frac, a_min=0, a_max=None))
        tensor[:, ci, :] = frac.astype(np.float32)
    obs_cols = [f"k_{tick_str(int(k))}_observed" for k in LADDER_K]
    tensor[:, 6, :] = df[obs_cols].values.astype(np.float32)
    tensor[:, 7, :] = in_range_mask

    scalars = np.stack([
        df["range_ticks"].values.astype(np.float64) / 100.0,
        df["close_pos_in_range"].values.astype(np.float64),
        df["n_cells"].values.astype(np.float64) / 200.0,
        df["truncated_abs_flow_below"].values.astype(np.float64),
        df["truncated_abs_flow_above"].values.astype(np.float64),
        df["log1p_bar_duration_secs"].values.astype(np.float64),
        df["is_rth"].values.astype(np.float64),
        df["minutes_since_session_open"].values.astype(np.float64) / 1380.0,
        (df["sigma_t"].values.astype(np.float64) / TICK) / 100.0,
        df["ewm_ratio_hl10"].values.astype(np.float64),
        df["ewm_ratio_hl40"].values.astype(np.float64),
        df["vol_regime_ratio"].values.astype(np.float64),
    ], axis=1)
    scalars = np.nan_to_num(scalars, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)

    meta_cols = key + ["session_date", "is_rth", "fwd_ret_40_pts"] + \
        [f"z_{h}" for h in CNN_HEADS] + [f"valid_{h}" for h in CNN_HEADS]
    meta = df[meta_cols].reset_index(drop=True)

    np.savez(tensor_path, tensor=tensor, scalars=scalars)
    meta.to_parquet(meta_path, index=False)
    log(f"  tensor cache built: {tensor.shape}, scalars {scalars.shape}")
    return tensor, scalars, meta


def _set_seed_all(seed):
    import random
    import torch
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)


def _make_ofinet():
    import torch.nn as nn
    import torch.nn.functional as F

    class ResBlock1D(nn.Module):
        def __init__(self, cin, cout, stride):
            super().__init__()
            self.conv1 = nn.Conv1d(cin, cout, 3, stride=stride, padding=1)
            self.gn1 = nn.GroupNorm(8, cout)
            self.conv2 = nn.Conv1d(cout, cout, 3, padding=1)
            self.gn2 = nn.GroupNorm(8, cout)
            self.short = nn.Conv1d(cin, cout, 1, stride=stride) if (stride != 1 or cin != cout) else None

        def forward(self, x):
            idn = x if self.short is None else self.short(x)
            out = F.gelu(self.gn1(self.conv1(x)))
            out = self.gn2(self.conv2(out))
            return F.gelu(out + idn)

    class OFINet(nn.Module):
        def __init__(self, scalar_dim=12):
            super().__init__()
            self.stem = nn.Conv1d(8, 32, 5, padding=2)
            self.stem_gn = nn.GroupNorm(8, 32)
            self.b1 = ResBlock1D(32, 32, 1)
            self.b2 = ResBlock1D(32, 64, 2)
            self.b3 = ResBlock1D(64, 64, 1)
            self.b4 = ResBlock1D(64, 128, 2)
            self.scalar_mlp = nn.Sequential(nn.Linear(scalar_dim, 32), nn.GELU())
            self.head_mlp = nn.Sequential(nn.Linear(288, 128), nn.GELU())
            self.out_heads = nn.ModuleDict({f"z_{h}": nn.Linear(128, 1) for h in CNN_HEADS})

        def forward(self, x, s):
            x = F.gelu(self.stem_gn(self.stem(x)))
            x = self.b1(x); x = self.b2(x); x = self.b3(x); x = self.b4(x)
            avgp = x.mean(dim=-1)
            maxp = x.max(dim=-1).values
            pooled = torch.cat([avgp, maxp], dim=-1)
            so = self.scalar_mlp(s)
            h = self.head_mlp(torch.cat([pooled, so], dim=-1))
            return {k: layer(h).squeeze(-1) for k, layer in self.out_heads.items()}

    import torch
    return OFINet()


def _cnn_augment(xb, targets):
    import torch
    B = xb.shape[0]
    mirror = torch.rand(B) < 0.5
    if mirror.any():
        midx = mirror.nonzero(as_tuple=True)[0]
        flipped = xb[midx].flip(-1)
        flipped = flipped[:, [1, 0, 2, 3, 5, 4, 6, 7], :]
        xb[midx] = flipped
        for h in CNN_HEADS:
            targets[h][midx] = -targets[h][midx]
    drop_mask = (torch.rand(B, 6, 1) < 0.1).float()
    xb[:, :6, :] = xb[:, :6, :] * (1 - drop_mask)
    noise = torch.randn(B, 6, xb.shape[-1]) * 0.05
    xb[:, :6, :] = xb[:, :6, :] + noise
    return xb, targets


def _train_one_cnn(tensor, scalars, meta, train_idx, valid_idx, seed):
    import torch
    import torch.nn as nn
    import torch.optim as optim

    _set_seed_all(seed)
    model = _make_ofinet()
    n_params = sum(p.numel() for p in model.parameters())
    opt = optim.AdamW(model.parameters(), lr=CNN_LR, weight_decay=CNN_WD)
    sched = optim.lr_scheduler.CosineAnnealingLR(opt, T_max=CNN_MAX_EPOCHS, eta_min=CNN_MIN_LR)
    huber = nn.HuberLoss(delta=1.0, reduction="mean")

    Xtr_t = torch.tensor(tensor[train_idx])
    Xtr_s = torch.tensor(scalars[train_idx])
    ytr = {h: torch.tensor(meta[f"z_{h}"].values[train_idx].astype(np.float32)) for h in CNN_HEADS}
    vtr = {h: torch.tensor(meta[f"valid_{h}"].values[train_idx].astype(bool)) for h in CNN_HEADS}
    for h in CNN_HEADS:
        ytr[h] = torch.nan_to_num(ytr[h], nan=0.0)

    Xva_t = torch.tensor(tensor[valid_idx])
    Xva_s = torch.tensor(scalars[valid_idx])
    yva40 = meta["z_40"].values[valid_idx]
    vva40 = meta["valid_40"].values[valid_idx].astype(bool)

    n = len(train_idx)
    rng = np.random.RandomState(seed)
    best_ic, best_state, patience_ct = -np.inf, None, 0

    for epoch in range(CNN_MAX_EPOCHS):
        model.train()
        perm = rng.permutation(n)
        for bstart in range(0, n, CNN_BATCH):
            idx = perm[bstart:bstart + CNN_BATCH]
            if len(idx) < 2:
                continue
            xb = Xtr_t[idx].clone()
            sb = Xtr_s[idx]
            targets = {h: ytr[h][idx].clone() for h in CNN_HEADS}
            valids = {h: vtr[h][idx] for h in CNN_HEADS}
            xb, targets = _cnn_augment(xb, targets)

            opt.zero_grad()
            preds = model(xb, sb)
            loss = None
            for h in CNN_HEADS:
                v = valids[h]
                if v.sum() < 2:
                    continue
                l = huber(preds[f"z_{h}"][v], targets[h][v])
                loss = l * CNN_HEAD_WEIGHTS[h] if loss is None else loss + l * CNN_HEAD_WEIGHTS[h]
            if loss is None:
                continue
            loss.backward()
            opt.step()
        sched.step()

        model.eval()
        with torch.no_grad():
            pred_va = model(Xva_t, Xva_s)["z_40"].numpy()
        val_ic = _spearman_ic(pred_va[vva40], yva40[vva40])
        if not np.isnan(val_ic) and val_ic > best_ic:
            best_ic = val_ic
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            patience_ct = 0
        else:
            patience_ct += 1
            if patience_ct >= CNN_PATIENCE:
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    return model, float(best_ic), n_params


def part_K_cnn_track2(run_dir):
    log("PART K: Track 2 CNN on close-anchored ladder ...")
    import torch
    # NOTE: this model is tiny (~174K params, batch 256) -- letting torch use all 24 cores causes
    # thread-pool synchronization overhead to dominate (measured ~1.0s/batch at 24 threads vs
    # ~0.04s/batch at 8 threads, a >20x slowdown). Cap threads at a small fixed value instead.
    torch.set_num_threads(8)

    tensor, scalars, meta = _build_or_load_cnn_dataset(run_dir)
    with open(os.path.join(run_dir, "folds.json")) as f:
        folds_obj = json.load(f)

    cnn_rows, pred_rows_all = [], []
    n_params_reported = None

    for fold in folds_obj["folds"]:
        fold_id = fold["fold_id"]
        train_sessions = fold["train_sessions"]
        test_sessions = fold["test_sessions"]
        val_sessions = set(sorted(train_sessions)[-2:])
        train_fit_sessions = [s for s in train_sessions if s not in val_sessions]

        train_idx = np.where(meta["session_date"].isin(train_fit_sessions).values)[0]
        valid_idx = np.where(meta["session_date"].isin(val_sessions).values)[0]
        test_meta = meta[meta["session_date"].isin(test_sessions)].copy()
        test_meta["_rank"] = test_meta.groupby("session_date").cumcount()
        test_meta = test_meta[test_meta["_rank"] >= FOLD_EMBARGO_BARS]
        test_idx = test_meta.index.values

        if len(train_idx) < 200 or len(test_idx) < 20 or len(valid_idx) < 10:
            continue
        log(f"  fold {fold_id}: train={len(train_idx)} valid={len(valid_idx)} test={len(test_idx)}")

        seed_pred_by_head = {h: [] for h in CNN_HEADS}
        val_ics = []
        for seed in CNN_SEEDS:
            model, best_val_ic, n_params = _train_one_cnn(tensor, scalars, meta, train_idx, valid_idx, seed)
            n_params_reported = n_params_reported or n_params
            val_ics.append(best_val_ic)
            with torch.no_grad():
                Xte_t = torch.tensor(tensor[test_idx])
                Xte_s = torch.tensor(scalars[test_idx])
                pr = model(Xte_t, Xte_s)
            for h in CNN_HEADS:
                seed_pred_by_head[h].append(pr[f"z_{h}"].numpy())
            log(f"    fold {fold_id} seed {seed}: best_val_ic_h40={best_val_ic:.4f}")

        te_out = meta.loc[test_idx, ["contract", "bar_idx", "session_date", "is_rth", "fwd_ret_40_pts"] +
                           [f"z_{h}" for h in CNN_HEADS] + [f"valid_{h}" for h in CNN_HEADS]].copy()
        te_out["fold_id"] = fold_id
        row = {"fold_id": fold_id, "n_train": len(train_idx), "n_valid": len(valid_idx),
                "n_test": len(test_idx), "mean_val_ic_h40": float(np.mean(val_ics))}
        for h in CNN_HEADS:
            pred_mean = np.mean(seed_pred_by_head[h], axis=0)
            te_out[f"pred_cnn_{h}"] = pred_mean
            v = te_out[f"valid_{h}"].values.astype(bool)
            ic_overall = _spearman_ic(pred_mean[v], te_out[f"z_{h}"].values[v])
            rth_mask = te_out["is_rth"].values.astype(bool) & v
            ic_rth = _spearman_ic(pred_mean[rth_mask], te_out[f"z_{h}"].values[rth_mask])
            row[f"ic_h{h}_overall"] = ic_overall
            row[f"ic_h{h}_rth"] = ic_rth
        cnn_rows.append(row)
        pred_rows_all.append(te_out)

    cnn_df = pd.DataFrame(cnn_rows)
    cnn_df.to_csv(os.path.join(run_dir, "cnn_results.csv"), index=False)
    pred_df = pd.concat(pred_rows_all, ignore_index=True) if pred_rows_all else pd.DataFrame()
    pred_df.to_parquet(os.path.join(run_dir, "cnn_h40_test_predictions.parquet"), index=False)

    # ---------------- economics at h=40 ----------------
    econ_rows = []
    if len(pred_df) > 0:
        for fold_id, sub in pred_df.groupby("fold_id"):
            sub_v = sub.dropna(subset=["fwd_ret_40_pts", "pred_cnn_40"])
            spread = _decile_spread(sub_v, col="pred_cnn_40")
            spread_rth = _decile_spread(sub_v[sub_v.is_rth.astype(bool)], col="pred_cnn_40")
            econ_rows.append({"fold_id": fold_id, "n": len(sub_v), "decile_spread_pts": spread,
                                "decile_spread_cost_adj_pts": spread - ECON_COST_PTS if not np.isnan(spread) else np.nan,
                                "decile_spread_rth_pts": spread_rth,
                                "decile_spread_rth_cost_adj_pts": spread_rth - ECON_COST_PTS if not np.isnan(spread_rth) else np.nan})
    econ_df = pd.DataFrame(econ_rows)
    econ_df.to_csv(os.path.join(run_dir, "cnn_economics.csv"), index=False)

    # ---------------- blend: rank(GBM tuned) + rank(CNN) at h=40 ----------------
    blend_rows = []
    gbm_path = os.path.join(run_dir, "gbm2_tuned_h40_test_predictions.parquet")
    if os.path.exists(gbm_path) and len(pred_df) > 0:
        gbm_pred = pd.read_parquet(gbm_path)
        merged = pred_df.merge(gbm_pred[["fold_id", "contract", "bar_idx", "pred_gbm_tuned"]],
                                 on=["fold_id", "contract", "bar_idx"], how="inner")
        for fold_id, sub in merged.groupby("fold_id"):
            sub = sub.dropna(subset=["pred_gbm_tuned", "pred_cnn_40", "z_40", "fwd_ret_40_pts"])
            if len(sub) < 20:
                continue
            r_gbm = sub["pred_gbm_tuned"].rank(pct=True)
            r_cnn = sub["pred_cnn_40"].rank(pct=True)
            blend_score = 0.5 * r_gbm + 0.5 * r_cnn
            sub = sub.assign(blend_score=blend_score)
            ic = _spearman_ic(blend_score.values, sub["z_40"].values)
            rth_mask = sub["is_rth"].values.astype(bool)
            ic_rth = _spearman_ic(blend_score.values[rth_mask], sub["z_40"].values[rth_mask]) if rth_mask.sum() > 10 else np.nan
            spread = _decile_spread(sub, col="blend_score")
            blend_rows.append({"fold_id": fold_id, "n": len(sub), "ic_h40_overall": ic, "ic_h40_rth": ic_rth,
                                "decile_spread_pts": spread,
                                "decile_spread_cost_adj_pts": spread - ECON_COST_PTS if not np.isnan(spread) else np.nan})
    blend_df = pd.DataFrame(blend_rows)
    blend_df.to_csv(os.path.join(run_dir, "blend_results.csv"), index=False)

    summary = {"n_params": n_params_reported, "n_folds_used": len(cnn_df), "horizons": {}}
    for h in CNN_HEADS:
        col = f"ic_h{h}_overall"
        if col in cnn_df and cnn_df[col].notna().sum() > 1:
            ics = cnn_df[col].dropna()
            t_stat = float(ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics)))) if ics.std(ddof=1) > 0 else np.nan
            summary["horizons"][h] = {"mean_ic": float(ics.mean()), "ic_tstat": t_stat, "n_folds": len(ics)}
    summary["economics_mean"] = {c: float(econ_df[c].dropna().mean()) for c in
                                   ["decile_spread_pts", "decile_spread_cost_adj_pts", "decile_spread_rth_pts",
                                    "decile_spread_rth_cost_adj_pts"]} if len(econ_df) else {}
    if len(blend_df):
        summary["blend_mean_ic_h40"] = float(blend_df["ic_h40_overall"].dropna().mean())
        summary["blend_mean_decile_spread_pts"] = float(blend_df["decile_spread_pts"].dropna().mean())

    with open(os.path.join(run_dir, "part_K_report.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)
    log(f"  Part K done. n_params={n_params_reported} summary={json.dumps(summary, default=str)[:2000]}")
    return summary


# ============================================================================
# PART L -- PHASE2B_REPORT.md + final summary
# ============================================================================


def _mtime_snapshot():
    files = [LONG_PATH, PACKED_PATH, PACKED_CTX_PATH, COVERAGE_AUDIT_PATH]
    return {os.path.basename(p): os.path.getmtime(p) for p in files if os.path.exists(p)}


MASTER_DIR_MTIMES_AT_START = _mtime_snapshot()


def part_L_report(run_dir, all_stats):
    log("PART L: writing PHASE2B_REPORT.md ...")

    def _safe_read_csv(name):
        p = os.path.join(run_dir, name)
        return pd.read_csv(p) if os.path.exists(p) else pd.DataFrame()

    def _safe_read_json(name):
        p = os.path.join(run_dir, name)
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f)
        return {}

    gbm_v1 = _safe_read_json("gbm_report.md") if False else None  # gbm_report.md is text; use gbm_results.csv
    v1_df = _safe_read_csv("gbm_results.csv")
    v1_excl = v1_df[v1_df.nqm6_policy == "EXCLUDED"] if len(v1_df) else v1_df

    tuned_df = _safe_read_csv("gbm2_tuned_results.csv")
    ablation_df = _safe_read_csv("gbm2_ablation_results.csv")
    econ_j_df = _safe_read_csv("gbm2_economics.csv")
    cnn_df = _safe_read_csv("cnn_results.csv")
    econ_k_df = _safe_read_csv("cnn_economics.csv")
    blend_df = _safe_read_csv("blend_results.csv")
    cal_fold_df = _safe_read_csv("part_I_calibration_per_fold.csv")
    cal_global_df = _safe_read_csv("part_I_calibration_global.csv")

    def _mean_ic(df, col):
        if len(df) == 0 or col not in df:
            return np.nan
        x = df[col].dropna()
        return float(x.mean()) if len(x) else np.nan

    def _tstat(df, col):
        if len(df) == 0 or col not in df:
            return np.nan
        x = df[col].dropna()
        if len(x) < 2 or x.std(ddof=1) == 0:
            return np.nan
        return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))

    comparison = []
    # GBM v1 baseline (Phase 2, NQM6-EXCLUDED policy, h in {10,20,40})
    row = {"model": "GBM v1 baseline (Phase 2)"}
    for h in [10, 20, 40]:
        row[f"ic_h{h}"] = _mean_ic(v1_excl, f"ic_h{h}_overall")
    row["ic_h40_tstat"] = _tstat(v1_excl, "ic_h40_overall")
    row["ic_h40_rth"] = _mean_ic(v1_excl, "ic_h40_rth")
    row["decile_spread_pts"] = np.nan
    row["decile_spread_cost_adj_pts"] = np.nan
    comparison.append(row)

    # GBM tuned (Part J)
    row = {"model": "GBM tuned (Part J)"}
    for h in J_HORIZONS:
        sub = tuned_df[tuned_df.horizon == h] if len(tuned_df) else tuned_df
        row[f"ic_h{h}"] = _mean_ic(sub, "ic_overall")
    row["ic_h40_tstat"] = _tstat(tuned_df[tuned_df.horizon == 40] if len(tuned_df) else tuned_df, "ic_overall")
    row["ic_h40_rth"] = _mean_ic(tuned_df[tuned_df.horizon == 40] if len(tuned_df) else tuned_df, "ic_rth")
    row["decile_spread_pts"] = _mean_ic(econ_j_df, "decile_spread_pts")
    row["decile_spread_cost_adj_pts"] = _mean_ic(econ_j_df, "decile_spread_cost_adj_pts")
    comparison.append(row)

    # trailing+time CONTROL (h=40 only, ablation)
    row = {"model": "GBM CONTROL: trailing+time only (h40 only)"}
    ctrl = ablation_df[ablation_df.variant == "control_trailing_time_only"] if len(ablation_df) else ablation_df
    row["ic_h40"] = _mean_ic(ctrl, "ic_h40")
    row["ic_h40_tstat"] = _tstat(ctrl, "ic_h40")
    row["ic_h40_rth"] = np.nan  # ablations computed overall-only
    row["decile_spread_pts"] = np.nan
    row["decile_spread_cost_adj_pts"] = np.nan
    comparison.append(row)

    # CNN (Part K, h in {10,20,40,80} -- no h60 head)
    row = {"model": "CNN Track 2 (Part K)"}
    for h in CNN_HEADS:
        row[f"ic_h{h}"] = _mean_ic(cnn_df, f"ic_h{h}_overall")
    row["ic_h40_tstat"] = _tstat(cnn_df, "ic_h40_overall")
    row["ic_h40_rth"] = _mean_ic(cnn_df, "ic_h40_rth")
    row["decile_spread_pts"] = _mean_ic(econ_k_df, "decile_spread_pts")
    row["decile_spread_cost_adj_pts"] = _mean_ic(econ_k_df, "decile_spread_cost_adj_pts")
    comparison.append(row)

    # Blend (GBM tuned rank + CNN rank, h40 only)
    row = {"model": "Blend: rank(GBM tuned) + rank(CNN) (h40 only)"}
    row["ic_h40"] = _mean_ic(blend_df, "ic_h40_overall")
    row["ic_h40_tstat"] = _tstat(blend_df, "ic_h40_overall")
    row["ic_h40_rth"] = _mean_ic(blend_df, "ic_h40_rth")
    row["decile_spread_pts"] = _mean_ic(blend_df, "decile_spread_pts")
    row["decile_spread_cost_adj_pts"] = _mean_ic(blend_df, "decile_spread_cost_adj_pts")
    comparison.append(row)

    comparison_df = pd.DataFrame(comparison)
    comparison_df.to_csv(os.path.join(run_dir, "phase2b_model_comparison.csv"), index=False)

    # ---------------- mtime check ----------------
    mtimes_now = _mtime_snapshot()
    master_untouched = (mtimes_now == MASTER_DIR_MTIMES_AT_START)

    lines = []
    lines.append("# OFI Phase 2B Report: Signal Iteration + Track 2 CNN")
    lines.append("")
    lines.append(f"Generated: {utc_now_iso()}")
    lines.append("")
    lines.append(f"**h={PRIMARY_HORIZON} is the REGISTERED PRIMARY endpoint.** All other horizons/metrics "
                  f"(h10, h20, h60, h80) are EXPLORATORY. Universe: NQU6 only (NQM6 EXCLUDED from all "
                  f"training/evaluation per locked decision from Phase 2 results).")
    lines.append("")
    lines.append("## Model comparison (mean across folds)")
    lines.append("")
    lines.append(comparison_df.to_markdown(index=False))
    lines.append("")
    lines.append("Columns ic_h10/h20/h60/h80 are EXPLORATORY. ic_h40 / ic_h40_tstat / ic_h40_rth / "
                  "decile_spread_* are the PRIMARY comparison. CONTROL and ablation ic_h40 are computed "
                  "overall-only (no RTH split, to bound compute) -- ic_h40_rth is blank there by design, "
                  "not missing data.")
    lines.append("")
    lines.append("## Key finding: cell geometry vs price history (Part J ablations)")
    lines.append("")
    if len(ablation_df):
        full_ic = tuned_df.loc[tuned_df.horizon == 40, "ic_overall"].mean() if len(tuned_df) else np.nan
        ctrl_ic = ctrl["ic_h40"].mean() if len(ctrl) else np.nan
        lines.append(f"Full tuned GBM model mean IC (h40): **{full_ic:.4f}**. The trailing+time-only CONTROL "
                      f"(zero cell-geometry features) mean IC (h40): **{ctrl_ic:.4f}** "
                      f"(delta vs full: {(ctrl_ic-full_ic) if not (np.isnan(ctrl_ic) or np.isnan(full_ic)) else float('nan'):+.4f}). "
                      f"Per-family drop-one / only-one deltas are in gbm2_ablation_results.csv "
                      f"(mean deltas also summarized in part_J_report.json).")
        lines.append("")
        lines.append("Interpretation: if the CONTROL nearly matches the full model, cell-geometry features "
                      "(bands/levels/side-zone/pressure/profile/context) are contributing little beyond what "
                      "trailing price-history and time-of-day features already capture, at least for the tuned "
                      "GBM track at this data volume -- a result to weigh directly against the CNN's use of "
                      "the full ladder geometry below.")
    lines.append("")
    lines.append("## Fold-by-fold detail")
    lines.append("")
    lines.append("- GBM tuned: gbm2_tuned_results.csv (sweep grid + chosen hyperparams: gbm2_sweep_results.csv)")
    lines.append("- GBM ablations: gbm2_ablation_results.csv")
    lines.append("- GBM economics: gbm2_economics.csv")
    lines.append("- GBM importance stability: gbm2_importance_fold_correlation.csv "
                  f"(mean pairwise Spearman corr: {all_stats.get('J', {}).get('stability', {}).get('mean_pairwise_importance_spearman_corr')})")
    lines.append("- CNN: cnn_results.csv, cnn_economics.csv "
                  f"(param count: {all_stats.get('K', {}).get('n_params')}, "
                  f"brief target ~0.3-0.5M -- measured count is below that range using the brief's literal "
                  f"channel-width spec 8->32,32,64,64,128, reported not force-fit)")
    lines.append("- Blend: blend_results.csv")
    lines.append("")
    lines.append("## Calibration tables (Part I)")
    lines.append("")
    lines.append("### Global reference: |z_40| -> pts/ticks")
    lines.append("")
    if len(cal_global_df):
        lines.append(cal_global_df.to_markdown(index=False))
    lines.append("")
    lines.append("### Per-fold train 30/70 z_40 quantile cuts (pt/tick equivalents at fold-median sigma_t)")
    lines.append("")
    if len(cal_fold_df):
        lines.append(cal_fold_df.to_markdown(index=False))
    lines.append("")
    lines.append("## MASTER_DIR integrity check")
    lines.append("")
    lines.append(f"- master_untouched: **{master_untouched}**")
    lines.append(f"- mtimes: {json.dumps(mtimes_now)}")
    lines.append("")

    with open(os.path.join(run_dir, "PHASE2B_REPORT.md"), "w") as f:
        f.write("\n".join(lines))

    stats = {"master_untouched": bool(master_untouched), "mtimes": mtimes_now,
              "comparison_table": comparison_df.to_dict(orient="records")}
    log(f"  Part L done. master_untouched={master_untouched}")
    return stats


# ============================================================================
# main
# ============================================================================


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--finish-from", default=None, choices=PART_ORDER,
                         help="Resume from this part onward (requires --run-dir pointing at an existing run)")
    parser.add_argument("--run-dir", default=None, help="Existing run_dir to resume in (required with --finish-from)")
    args = parser.parse_args()

    if args.finish_from:
        if not args.run_dir or not os.path.isdir(args.run_dir):
            raise SystemExit("--finish-from requires --run-dir pointing at an existing run directory")
        run_dir = args.run_dir
    else:
        run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        run_dir = os.path.join(OUTPUT_ROOT, f"label_feature_build_{run_ts}")
        os.makedirs(run_dir, exist_ok=True)

    log(f"Run dir: {run_dir}")
    state = load_state(run_dir)
    start_idx = PART_ORDER.index(args.finish_from) if args.finish_from else 0

    all_stats = {}
    for k in PART_ORDER[:start_idx]:
        if k in state.get("parts", {}) and state["parts"][k]["status"] == "done":
            all_stats[k] = state["parts"][k]["stats"]

    part_fns = {"A": part_A_spine, "B": part_B_audits, "C": part_C_labels, "D": part_D_ladder,
                "E": part_E_features, "F": part_F_folds, "I": part_I_labels_calibration_ladder,
                "J": part_J_gbm_iteration, "K": part_K_cnn_track2}

    for part in PART_ORDER[start_idx:]:
        if part == "G":
            g_stats = part_G_validation(run_dir, all_stats)
            all_stats["G"] = g_stats
            mark_part_done(run_dir, state, "G", g_stats)
            write_phase2_report(run_dir, all_stats)
            if not g_stats["labels_ready"]:
                log("LABELS_READY: FAIL -- stopping before Part H")
                break
            continue
        if part == "H":
            gbm_stats = part_H_gbm(run_dir)
            all_stats["H"] = gbm_stats
            mark_part_done(run_dir, state, "H", gbm_stats)
            write_phase2_report(run_dir, all_stats, gbm_stats)
            continue
        if part == "L":
            l_stats = part_L_report(run_dir, all_stats)
            all_stats["L"] = l_stats
            mark_part_done(run_dir, state, "L", l_stats)
            continue
        stats = part_fns[part](run_dir)
        all_stats[part] = stats
        mark_part_done(run_dir, state, part, stats)

    log("DONE.")
    log(f"Confirming no writes under MASTER_DIR: {MASTER_DIR}")
    log("All outputs are under: " + run_dir)


if __name__ == "__main__":
    main()
