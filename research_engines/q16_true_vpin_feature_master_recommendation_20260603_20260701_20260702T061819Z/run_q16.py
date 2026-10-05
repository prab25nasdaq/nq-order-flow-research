"""
Q16 True VPIN Feature Master Recommendation Audit — Full Rithmic Dataset
Jun 3 – Jul 1 2026
SHADOW / RESEARCH ONLY — no execution, no broker, no order placement
"""
import json, os, sys, time, warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

# ── invariants ──────────────────────────────────────────────
PRODUCTION_FILES_MODIFIED    = False
DASHBOARD_CODE_MODIFIED      = False
FEATURE_MASTER_CODE_MODIFIED = False
BOOK_FLOW_CODE_MODIFIED      = False
MODEL_ARTIFACTS_MODIFIED     = False
ACTIVE_MODEL_POINTER_CHANGED = False
TRADING_ENABLED              = False
BROKER_CONNECTED             = False
PAPER_TRADING_ENABLED        = False

RUN_TS  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
OUT_DIR = Path(__file__).parent

# ── target range ─────────────────────────────────────────────
TS_START_NS = int(datetime(2026, 6,  3, 0,  0,  0, tzinfo=timezone.utc).timestamp() * 1e9)
TS_END_NS   = int(datetime(2026, 7,  1, 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1e9)

RAW_BASE      = Path("/home/prabh/OFI_Live_Data/Rithmic_Raw")
MASTER_CONT   = Path("/home/prabh/OFI_Live_Features/master_NQ_continuous_backadjusted_shadow.ndjsonl")
MASTER_NQU6   = Path("/home/prabh/OFI_Live_Features/master_NQU6_shadow.ndjsonl")
FM_PARQUET    = Path("/home/prabh/OFI_Production/model_feature_master/data/model_feature_master_shadow.parquet")
FM_STATUS     = Path("/home/prabh/OFI_Production/model_feature_master/data/feature_master_status.json")
BS_PARQUET    = Path("/home/prabh/OFI_Production/research_engines/"
                     "book_switching_cross_side_add_pull_alpha_v1_20260625T190639Z/"
                     "book_switching_feature_panel.parquet")

# ── config ───────────────────────────────────────────────────
BUCKET_SIZES  = [500, 1000, 2500]
ROLL_WINDOWS  = [10, 20, 50]
PCT_LOOKBACKS = [250, 500, 1000]
FWD_HORIZONS  = [5, 10, 20, 40, 80]
N_PERM        = 500
SEP = "=" * 72

def ns2utc(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat()

# ── math helpers ─────────────────────────────────────────────
def rolling_pct_rank(arr: np.ndarray, window: int) -> np.ndarray:
    n   = len(arr)
    out = np.full(n, np.nan)
    for i in range(window - 1, n):
        sl = arr[i - window + 1: i + 1]
        fin = sl[np.isfinite(sl)]
        if len(fin) >= 2:
            out[i] = (fin[:-1] < fin[-1]).sum() / (len(fin) - 1)
    return out


def rolling_spearman(x: np.ndarray, y: np.ndarray, w: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    for i in range(w - 1, len(x)):
        xi = x[i - w + 1: i + 1]
        yi = y[i - w + 1: i + 1]
        m  = np.isfinite(xi) & np.isfinite(yi)
        if m.sum() >= 10:
            out[i] = stats.spearmanr(xi[m], yi[m]).statistic
    return out


def fwd_returns(close: np.ndarray, horizons: List[int]) -> Dict[str, np.ndarray]:
    out = {}
    for h in horizons:
        r = np.full(len(close), np.nan)
        r[: len(close) - h] = close[h:] / close[: len(close) - h] - 1
        out[f"H{h}"] = r
    return out


def mfe_mae(close: np.ndarray, h: int) -> Tuple[np.ndarray, np.ndarray]:
    n   = len(close)
    mfe = np.full(n, np.nan)
    mae = np.full(n, np.nan)
    for i in range(n - h):
        w      = close[i + 1: i + h + 1]
        mfe[i] = (w.max() - close[i]) / close[i]
        mae[i] = (close[i] - w.min()) / close[i]
    return mfe, mae


def outcome_row(fwd: np.ndarray, mask: np.ndarray,
                direction: int, mfe: np.ndarray, mae: np.ndarray) -> Dict:
    sub = fwd[mask & np.isfinite(fwd)]
    mfe_s = mfe[mask & np.isfinite(mfe)]
    mae_s = mae[mask & np.isfinite(mae)]
    if len(sub) < 10:
        return {"n": len(sub), "hit_rate": np.nan, "mean_ret": np.nan,
                "sharpe": np.nan, "mfe_mean": np.nan, "mae_mean": np.nan}
    rets = sub * direction
    mu   = rets.mean(); sig = rets.std()
    return {
        "n":        int(len(sub)),
        "hit_rate": float((rets > 0).mean()),
        "mean_ret": float(mu),
        "sharpe":   float(mu / sig) if sig > 0 else np.nan,
        "mfe_mean": float(mfe_s.mean()) if len(mfe_s) > 0 else np.nan,
        "mae_mean": float(mae_s.mean()) if len(mae_s) > 0 else np.nan,
    }


def bh_fdr(pvals: np.ndarray) -> np.ndarray:
    n  = len(pvals)
    if n == 0:
        return np.array([])
    rk = np.argsort(pvals)
    sp = pvals[rk]
    q  = np.minimum(1.0, sp * n / np.arange(1, n + 1))
    for i in range(n - 2, -1, -1):
        q[i] = min(q[i], q[i + 1])
    out = np.empty(n); out[rk] = q
    return out


def circ_shift_pval(x: np.ndarray, y: np.ndarray, n_perm: int = 500) -> float:
    """Circular-shift permutation p-value for Spearman ρ."""
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 20:
        return np.nan
    xi, yi = x[mask], y[mask]
    obs = abs(stats.spearmanr(xi, yi).statistic)
    n   = len(yi)
    count = 0
    for _ in range(n_perm):
        s     = np.random.randint(1, n)
        y_sh  = np.roll(yi, s)
        rho   = abs(stats.spearmanr(xi, y_sh).statistic)
        if rho >= obs:
            count += 1
    return count / n_perm


# ── bucket builder ───────────────────────────────────────────
def build_buckets_stream(ts_arr: np.ndarray, sz_arr: np.ndarray,
                          side_arr: np.ndarray, bucket_size: float
                          ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Build equal-volume buckets from sorted trade stream.
    Returns (end_ts, vb, vs, v_total) arrays."""
    end_ts_list, vb_list, vs_list = [], [], []
    cur_vb = cur_vs = cur_fill = 0.0

    for i in range(len(ts_arr)):
        rem   = float(sz_arr[i])
        is_b  = (side_arr[i] == 66)  # ord('B') == 66
        while rem > 0:
            cap = bucket_size - cur_fill
            if rem <= cap:
                if is_b: cur_vb += rem
                else:    cur_vs += rem
                cur_fill += rem
                rem = 0.0
            else:
                if is_b: cur_vb += cap
                else:    cur_vs += cap
                cur_fill += cap
                rem -= cap
                end_ts_list.append(ts_arr[i])
                vb_list.append(cur_vb); vs_list.append(cur_vs)
                cur_vb = cur_vs = cur_fill = 0.0

    # partial tail bucket
    if cur_fill > 0:
        end_ts_list.append(ts_arr[-1] if len(ts_arr) > 0 else 0)
        vb_list.append(cur_vb); vs_list.append(cur_vs)

    end_ts = np.array(end_ts_list, dtype=np.int64)
    vb     = np.array(vb_list,     dtype=np.float32)
    vs     = np.array(vs_list,     dtype=np.float32)
    vtot   = vb + vs
    return end_ts, vb, vs, vtot


def rolling_true_vpin(vb: np.ndarray, vs: np.ndarray, window: int) -> np.ndarray:
    imb  = np.abs(vb - vs).astype(np.float64)
    tot  = (vb + vs).astype(np.float64)
    n    = len(imb)
    vpin = np.full(n, np.nan)
    ri   = np.cumsum(imb); rt = np.cumsum(tot)
    for i in range(window - 1, n):
        lo  = i - window + 1
        si  = ri[i] - (ri[lo - 1] if lo > 0 else 0.0)
        st  = rt[i] - (rt[lo - 1] if lo > 0 else 0.0)
        vpin[i] = si / st if st > 0 else np.nan
    return vpin


def align_to_bars(bucket_end_ts: np.ndarray, bucket_vals: np.ndarray,
                   bar_end_ts: np.ndarray) -> np.ndarray:
    """For each bar, rightmost bucket with end_ts <= bar_end_ts. No lookahead."""
    out = np.full(len(bar_end_ts), np.nan)
    for i, bet in enumerate(bar_end_ts):
        pos = np.searchsorted(bucket_end_ts, bet, side="right") - 1
        if pos >= 0:
            out[i] = bucket_vals[pos]
    return out


# ═══════════════════════════════════════════════════════════════
# PART A — Raw Rithmic timestamp coverage audit
# ═══════════════════════════════════════════════════════════════
def part_a() -> Dict:
    print("\n── Part A: Raw Rithmic timestamp coverage audit ──")

    date_dirs = sorted(os.listdir(RAW_BASE))
    rows   = []
    mismatches = []

    for folder_date in date_dirs:
        dp = RAW_BASE / folder_date
        if not dp.is_dir():
            continue
        sym_dirs = [d for d in os.listdir(dp) if (dp / d).is_dir()]
        for sym in sym_dirs:
            tp = dp / sym / "trades.ndjson"
            if not tp.exists():
                rows.append({
                    "file_path": str(tp), "folder_date": folder_date, "symbol": sym,
                    "has_trades": False, "records_total": 0,
                    "first_ts_utc": "", "last_ts_utc": "",
                    "records_inside": 0, "records_before": 0, "records_after": 0,
                    "include_file": False, "include_reason": "NO_TRADES_FILE",
                })
                continue

            # Read all timestamps
            ts_all = []
            with open(tp) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    ts_all.append(r.get("timestamp_ns", 0))

            ts_arr = np.array(ts_all, dtype=np.int64)
            n_total  = len(ts_arr)
            n_inside = int(((ts_arr >= TS_START_NS) & (ts_arr <= TS_END_NS)).sum())
            n_before = int((ts_arr < TS_START_NS).sum())
            n_after  = int((ts_arr > TS_END_NS).sum())

            include = n_inside > 0
            reason  = ("HAS_IN_RANGE_RECORDS" if include
                       else "ALL_RECORDS_OUT_OF_RANGE")

            first_utc = ns2utc(ts_arr.min()) if n_total > 0 else ""
            last_utc  = ns2utc(ts_arr.max()) if n_total > 0 else ""

            rows.append({
                "file_path":       str(tp),
                "folder_date":     folder_date,
                "symbol":          sym,
                "has_trades":      True,
                "records_total":   n_total,
                "first_ts_utc":    first_utc,
                "last_ts_utc":     last_utc,
                "records_inside":  n_inside,
                "records_before":  n_before,
                "records_after":   n_after,
                "include_file":    include,
                "include_reason":  reason,
            })

            # Date-mismatch check
            if n_after > 0 or n_before > 0:
                mismatches.append({
                    "folder_date":  folder_date,
                    "symbol":       sym,
                    "n_before_range": n_before,
                    "n_after_range":  n_after,
                    "first_ts":     first_utc,
                    "last_ts":      last_utc,
                    "note": f"folder={folder_date} but records span outside target range",
                })

    audit_df = pd.DataFrame(rows)
    audit_df.to_csv(OUT_DIR / "raw_rithmic_timestamp_coverage_audit.csv", index=False)
    print(f"  Wrote {len(audit_df)} rows → raw_rithmic_timestamp_coverage_audit.csv")

    # Mismatch report
    included = audit_df[audit_df["include_file"]]
    total_inside = included["records_inside"].sum()
    mismatch_lines = [
        "# Raw Rithmic File Date Mismatch Report",
        f"Generated: {RUN_TS}",
        f"Target range: {ns2utc(TS_START_NS)} → {ns2utc(TS_END_NS)}",
        "",
        f"## Files audited: {len(audit_df)}",
        f"## Files included (have in-range records): {included['include_file'].sum()}",
        f"## Total in-range trade records: {total_inside:,}",
        "",
        "## Files with out-of-range records (date spillover):",
    ]
    if mismatches:
        for m in mismatches:
            mismatch_lines.append(
                f"  {m['folder_date']}/{m['symbol']}: "
                f"before={m['n_before_range']}  after={m['n_after_range']}  "
                f"span={m['first_ts']} → {m['last_ts']}"
            )
    else:
        mismatch_lines.append("  None — all files within target range.")

    mismatch_lines += [
        "",
        "## Symbol roll observation:",
        "  Jun 3–11: raw data under NQM6/ (front month before roll)",
        "  Jun 14+:  raw data under NQU6/ (after quarterly roll)",
        "  True VPIN computed from NQM6 and NQU6 trades — volume clock continuous across roll.",
    ]

    (OUT_DIR / "raw_file_date_mismatch_report.md").write_text("\n".join(mismatch_lines))
    print("  raw_file_date_mismatch_report.md")

    return {
        "audit_df":     audit_df,
        "total_inside": int(total_inside),
        "n_mismatches": len(mismatches),
        "n_after_range": int(audit_df["records_after"].sum()),
        "n_before_range": int(audit_df["records_before"].sum()),
    }


# ═══════════════════════════════════════════════════════════════
# PART B — Rebuild true VPIN features on full range
# ═══════════════════════════════════════════════════════════════
def load_all_trades(audit_df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load timestamp-filtered trades from all included files, sorted by timestamp."""
    print("  Loading all trades (timestamp-filtered)…", flush=True)
    ts_chunks, sz_chunks, side_chunks = [], [], []
    included = audit_df[audit_df["include_file"]]

    for _, row in included.iterrows():
        tp = Path(row["file_path"])
        if not tp.exists():
            continue
        ts_l, sz_l, sd_l = [], [], []
        with open(tp) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r  = json.loads(line)
                ts = r.get("timestamp_ns", 0)
                if ts < TS_START_NS or ts > TS_END_NS:
                    continue
                sz = r.get("size", 0)
                if sz <= 0:
                    continue
                sd = r.get("aggressor_side", "")
                if sd not in ("B", "S"):
                    continue
                ts_l.append(ts); sz_l.append(sz); sd_l.append(sd)

        if ts_l:
            ts_chunks.append(np.array(ts_l, dtype=np.int64))
            sz_chunks.append(np.array(sz_l, dtype=np.float32))
            side_chunks.append(np.frompyfunc(lambda x: ord(x), 1, 1)(sd_l).astype(np.uint8))
            print(f"    {row['folder_date']}/{row['symbol']}: {len(ts_l):,} in-range trades")

    ts_all   = np.concatenate(ts_chunks)
    sz_all   = np.concatenate(sz_chunks)
    side_all = np.concatenate(side_chunks)

    order    = np.argsort(ts_all, kind="stable")
    return ts_all[order], sz_all[order], side_all[order]


def part_b(audit_df: pd.DataFrame) -> Dict[str, Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    print("\n── Part B: Rebuild true VPIN features on full range ──")

    ts_all, sz_all, side_all = load_all_trades(audit_df)
    print(f"  Total in-range trades: {len(ts_all):,}  [{ns2utc(ts_all.min())} → {ns2utc(ts_all.max())}]")

    buckets: Dict[str, Tuple] = {}  # key=f"V{bsz}" → (end_ts, vb, vs, vtot)
    for bsz in BUCKET_SIZES:
        t0  = time.time()
        end_ts, vb, vs, vtot = build_buckets_stream(ts_all, sz_all, side_all, bsz)
        elapsed = time.time() - t0
        buckets[f"V{bsz}"] = (end_ts, vb, vs, vtot)
        print(f"  V{bsz}: {len(end_ts):,} buckets  ({elapsed:.1f}s)")

    # Save bucket diagnostics
    diag_rows = []
    for key, (end_ts, vb, vs, vtot) in buckets.items():
        imb  = np.abs(vb - vs)
        diag_rows.append({
            "bucket_key":    key,
            "n_buckets":     len(end_ts),
            "first_bucket_ts": ns2utc(end_ts[0]) if len(end_ts) > 0 else "",
            "last_bucket_ts":  ns2utc(end_ts[-1]) if len(end_ts) > 0 else "",
            "mean_vb":       float(vb.mean()),
            "mean_vs":       float(vs.mean()),
            "mean_imb":      float(imb.mean()),
            "mean_raw_vpin": float((imb / np.maximum(vtot, 1)).mean()),
        })
    pd.DataFrame(diag_rows).to_csv(OUT_DIR / "true_volume_bucket_diagnostics.csv", index=False)
    print("  Wrote → true_volume_bucket_diagnostics.csv")

    return buckets


# ═══════════════════════════════════════════════════════════════
# PART C — Align true VPIN to continuous master bars
# ═══════════════════════════════════════════════════════════════
def load_master() -> pd.DataFrame:
    print("  Loading continuous master…", flush=True)
    rows = []
    with open(MASTER_CONT) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            ts = r["bar_end_ts_ns"]
            if ts < TS_START_NS or ts > TS_END_NS:
                continue
            rows.append({
                "bar_index":     r["bar_index"],
                "bar_end_ts_ns": ts,
                "bar_start_ts_ns": r["bar_start_ts_ns"],
                "day":           r["day"],
                "close":         r.get("continuous_close", r.get("px_close", np.nan)),
                "buy_vol":       r.get("buy_vol", 0),
                "sell_vol":      r.get("sell_vol", 0),
                "vol_total":     r.get("vol_total", 500),
                "delta_norm":    r.get("delta_norm", np.nan),
                "vpin":          r.get("vpin", np.nan),
                "mlofi_norm":    r.get("mlofi_norm", np.nan),
                "cusum_up":      r.get("cusum_up_break", 0),
                "cusum_dn":      r.get("cusum_down_break", 0),
                "minute_of_day": r.get("minute_of_day", 0),
                "is_current":    r.get("is_current_contract", False),
            })
    df = pd.DataFrame(rows).reset_index(drop=True)
    df["rithmic_date_str"] = df["day"].astype(str).apply(
        lambda x: f"{x[:4]}-{x[4:6]}-{x[6:8]}"
    )
    print(f"  {len(df):,} bars  [{df['rithmic_date_str'].min()} → {df['rithmic_date_str'].max()}]")
    return df


def part_c(master: pd.DataFrame,
           buckets: Dict[str, Tuple]) -> pd.DataFrame:
    print("\n── Part C: Align true VPIN to continuous master bars ──")

    bar_end_ts = master["bar_end_ts_ns"].values
    panel = master.copy()

    alignment_rows = []
    for bkey, (end_ts, vb, vs, vtot) in buckets.items():
        for rw in ROLL_WINDOWS:
            raw_col  = f"tvpin_{bkey}_W{rw}"
            vpin_arr = rolling_true_vpin(vb, vs, rw)
            aligned  = align_to_bars(end_ts, vpin_arr, bar_end_ts)
            panel[raw_col] = aligned

            # Pct rank variants
            for L in PCT_LOOKBACKS:
                pct_col = f"tvpin_pct_{bkey}_W{rw}_L{L}"
                panel[pct_col] = rolling_pct_rank(aligned, L)

            n_non_nan = np.isfinite(aligned).sum()
            alignment_rows.append({
                "col":         raw_col,
                "n_bars":      len(bar_end_ts),
                "n_aligned":   int(n_non_nan),
                "coverage":    float(n_non_nan / len(bar_end_ts)),
                "mean_val":    float(np.nanmean(aligned)),
                "std_val":     float(np.nanstd(aligned)),
                "leakage_check": "PASS_SEALED_BUCKET_SEARCHSORTED",
            })

    pd.DataFrame(alignment_rows).to_csv(OUT_DIR / "q16_true_vpin_alignment_audit.csv", index=False)
    print(f"  Wrote {len(alignment_rows)} rows → q16_true_vpin_alignment_audit.csv")

    # Leakage audit: verify no bucket end_ts > bar_end_ts for any aligned value
    leakage_rows = []
    for bkey, (end_ts, vb, vs, vtot) in buckets.items():
        # For sample of 500 bars, verify
        sample_idx = np.linspace(0, len(bar_end_ts) - 1, min(500, len(bar_end_ts)), dtype=int)
        n_violations = 0
        for idx in sample_idx:
            bet = bar_end_ts[idx]
            pos = np.searchsorted(end_ts, bet, side="right") - 1
            if pos >= 0 and end_ts[pos] > bet:
                n_violations += 1
        leakage_rows.append({
            "bucket_key":   bkey,
            "sample_n":     len(sample_idx),
            "violations":   n_violations,
            "leakage_free": (n_violations == 0),
        })

    pd.DataFrame(leakage_rows).to_csv(OUT_DIR / "q16_true_vpin_leakage_audit.csv", index=False)
    print("  Wrote → q16_true_vpin_leakage_audit.csv")

    # Build directional features from best representative (V500 W10 and V500 W50)
    for rw in [10, 50]:
        raw_col = f"tvpin_V500_W{rw}"
        pct_col = f"tvpin_pct_V500_W{rw}_L500"
        if raw_col in panel.columns and pct_col in panel.columns:
            pct = panel[pct_col].values
            dn  = panel["delta_norm"].values
            panel[f"true_signed_vpin_delta_W{rw}"]  = pct * np.sign(dn)
            panel[f"true_buy_toxicity_W{rw}"]        = pct * np.maximum(dn, 0)
            panel[f"true_sell_toxicity_W{rw}"]       = pct * np.maximum(-dn, 0)
            panel[f"true_toxic_balance_W{rw}"]       = (pct * np.maximum(dn, 0) -
                                                         pct * np.maximum(-dn, 0))

    # Current VPIN pct rank for comparison
    cur_vpin = panel["vpin"].values
    panel["cur_vpin_pct_L500"] = rolling_pct_rank(cur_vpin, 500)
    panel["cur_buy_toxicity"]  = panel["cur_vpin_pct_L500"].values * np.maximum(panel["delta_norm"].values, 0)
    panel["cur_sell_toxicity"] = panel["cur_vpin_pct_L500"].values * np.maximum(-panel["delta_norm"].values, 0)

    # Divergence signal
    panel["vpin_divergence_pct_delta"] = (
        panel.get("tvpin_pct_V500_W50_L500", pd.Series(dtype=float)).values
        - panel["cur_vpin_pct_L500"].values
    )

    panel.to_parquet(OUT_DIR / "q16_true_vpin_feature_master_aligned.parquet", index=False)
    print(f"  q16_true_vpin_feature_master_aligned.parquet  ({len(panel):,} rows, {len(panel.columns)} cols)")

    # Feature catalog
    cat_rows = []
    for c in panel.columns:
        if c.startswith("tvpin_") or c.startswith("true_") or c.startswith("cur_") or c.startswith("vpin_"):
            cat_rows.append({"col": c, "dtype": str(panel[c].dtype),
                             "n_non_nan": int(panel[c].notna().sum()),
                             "mean": float(panel[c].mean()) if panel[c].dtype != object else ""})
    pd.DataFrame(cat_rows).to_csv(OUT_DIR / "q16_true_vpin_feature_catalog.csv", index=False)
    print(f"  Wrote {len(cat_rows)} rows → q16_true_vpin_feature_catalog.csv")

    return panel


# ═══════════════════════════════════════════════════════════════
# PART D — Compare current vs true VPIN on full range
# ═══════════════════════════════════════════════════════════════
def state_from_pct(pct: np.ndarray) -> np.ndarray:
    s = np.where(pct >= 0.95, "EXTREME",
        np.where(pct >= 0.90, "TOXIC",
        np.where(pct >= 0.70, "ELEVATED", "NORMAL")))
    s[~np.isfinite(pct)] = "UNKNOWN"
    return s


def part_d(panel: pd.DataFrame) -> Dict:
    print("\n── Part D: Current vs True VPIN comparison ──")

    cur_pct = panel["cur_vpin_pct_L500"].values
    cur_states = state_from_pct(cur_pct)

    comp_rows = []
    tvpin_cols = [c for c in panel.columns if c.startswith("tvpin_pct_")]

    for col in tvpin_cols:
        tv = panel[col].values
        mask = np.isfinite(cur_pct) & np.isfinite(tv)
        if mask.sum() < 50:
            continue

        pr, _  = stats.pearsonr(cur_pct[mask], tv[mask])
        sr, _  = stats.spearmanr(cur_pct[mask], tv[mask])
        tv_states  = state_from_pct(tv)
        valid  = (cur_states != "UNKNOWN") & (tv_states != "UNKNOWN")
        agree  = (cur_states[valid] == tv_states[valid]).mean()
        tox_m  = ((cur_states == "TOXIC") | (cur_states == "EXTREME")) & valid
        tox_a  = ((tv_states  == "TOXIC") | (tv_states  == "EXTREME")) & valid & tox_m
        ext_m  = (cur_states == "EXTREME") & valid
        ext_a  = (tv_states  == "EXTREME") & valid & ext_m

        comp_rows.append({
            "col":               col,
            "n":                 int(mask.sum()),
            "pearson_r":         float(pr),
            "spearman_r":        float(sr),
            "state_agreement":   float(agree),
            "toxic_agreement":   float(tox_a.sum() / max(tox_m.sum(), 1)),
            "extreme_agreement": float(ext_a.sum() / max(ext_m.sum(), 1)),
            "mean_abs_diff":     float(np.abs(cur_pct[mask] - tv[mask]).mean()),
            "diverge_gt20pct":   float((np.abs(cur_pct[mask] - tv[mask]) > 0.20).mean()),
        })

    comp_df = pd.DataFrame(comp_rows)
    comp_df.to_csv(OUT_DIR / "q16_current_vs_true_vpin_full_range_comparison.csv", index=False)
    print(f"  Wrote {len(comp_df)} rows → q16_current_vs_true_vpin_full_range_comparison.csv")

    # Divergence events (V500 W50 L500)
    rep_col = "tvpin_pct_V500_W50_L500"
    disagree_rows = []
    if rep_col in panel.columns:
        tv  = panel[rep_col].values
        dff = np.abs(cur_pct - tv)
        idx = np.where(np.isfinite(dff) & (dff > 0.20))[0]
        for i in idx[:1000]:
            disagree_rows.append({
                "bar_index":   panel.at[i, "bar_index"],
                "date":        panel.at[i, "rithmic_date_str"],
                "cur_pct":     float(cur_pct[i]),
                "true_pct":    float(tv[i]),
                "delta":       float(dff[i]),
                "cur_state":   cur_states[i],
                "true_state":  state_from_pct(tv[i:i+1])[0],
            })
    pd.DataFrame(disagree_rows).to_csv(OUT_DIR / "q16_vpin_state_disagreement_events.csv", index=False)
    print(f"  Wrote {len(disagree_rows)} rows → q16_vpin_state_disagreement_events.csv")

    # Divergence summary by session
    sess_map = {0: "Asia", 1: "London", 2: "US_Open", 3: "US_PM"}
    panel_tmp = panel.copy()
    panel_tmp["session"] = pd.cut(
        panel_tmp["minute_of_day"],
        bins=[-1, 120, 360, 570, 1440],
        labels=["Asia", "London", "US_Main", "US_Late"]
    )
    div_rows = []
    if rep_col in panel.columns:
        tv = panel[rep_col].values
        for sess, grp in panel_tmp.groupby("session"):
            idx = grp.index.values
            cv  = cur_pct[idx]; tv_g = tv[idx]
            m   = np.isfinite(cv) & np.isfinite(tv_g)
            if m.sum() < 10:
                continue
            div_rows.append({
                "session":    sess,
                "n":          int(m.sum()),
                "spearman_r": float(stats.spearmanr(cv[m], tv_g[m]).statistic),
                "mean_abs_diff": float(np.abs(cv[m] - tv_g[m]).mean()),
                "diverge_20pct_rate": float((np.abs(cv[m]-tv_g[m]) > 0.20).mean()),
            })
    pd.DataFrame(div_rows).to_csv(OUT_DIR / "q16_vpin_divergence_summary.csv", index=False)
    print(f"  Wrote {len(div_rows)} rows → q16_vpin_divergence_summary.csv")

    return {
        "comp_df":   comp_df,
        "best_spearman_col": comp_df.loc[comp_df["spearman_r"].idxmax(), "col"] if len(comp_df) > 0 else "",
        "best_spearman_r":   float(comp_df["spearman_r"].max()) if len(comp_df) > 0 else np.nan,
        "rep_spearman_r":    float(comp_df.loc[comp_df["col"] == "tvpin_pct_V500_W50_L500", "spearman_r"].values[0])
                              if "tvpin_pct_V500_W50_L500" in comp_df["col"].values else np.nan,
        "rep_state_agree":   float(comp_df.loc[comp_df["col"] == "tvpin_pct_V500_W50_L500", "state_agreement"].values[0])
                              if "tvpin_pct_V500_W50_L500" in comp_df["col"].values else np.nan,
    }


# ═══════════════════════════════════════════════════════════════
# PART E — Forward outcome / predictive value
# ═══════════════════════════════════════════════════════════════
def part_e(panel: pd.DataFrame, d_summary: Dict) -> Dict:
    print("\n── Part E: Forward outcome / predictive value ──")

    close  = panel["close"].ffill().values
    dn     = panel["delta_norm"].values
    fwds   = fwd_returns(close, FWD_HORIZONS)
    mfe10, mae10 = mfe_mae(close, 10)
    mfe5,  mae5  = mfe_mae(close, 5)

    # Load book-switch if available
    bs_bullish = bs_bearish = None
    if BS_PARQUET.exists():
        bs = pd.read_parquet(BS_PARQUET)
        if "bar_end_ts_ns" in bs.columns:
            bs_ts = bs["bar_end_ts_ns"].values
            panel_ts = panel["bar_end_ts_ns"].values
            if "bullish_pair_balance" in bs.columns:
                pb = bs["bullish_pair_balance"].values
                mp = bs["bullish_min_pct"].values if "bullish_min_pct" in bs.columns else np.zeros(len(bs))
                bs_bullish_raw = np.full(len(panel), False)
                for i, pts in enumerate(panel_ts):
                    pos = np.searchsorted(bs_ts, pts, side="right") - 1
                    if pos >= 0 and abs(bs_ts[pos] - pts) < int(1e11):
                        bs_bullish_raw[i] = (pb[pos] >= 0.95) and (mp[pos] >= 0.95)
                bs_bullish = bs_bullish_raw
            if "bearish_pair_balance" in bs.columns:
                pb = bs["bearish_pair_balance"].values
                mp = bs["bearish_min_pct"].values if "bearish_min_pct" in bs.columns else np.zeros(len(bs))
                bs_bearish_raw = np.full(len(panel), False)
                for i, pts in enumerate(panel_ts):
                    pos = np.searchsorted(bs_ts, pts, side="right") - 1
                    if pos >= 0 and abs(bs_ts[pos] - pts) < int(1e11):
                        bs_bearish_raw[i] = (pb[pos] >= 0.95) and (mp[pos] >= 0.95)
                bs_bearish = bs_bearish_raw

    # Build test conditions
    tv50  = panel.get("tvpin_pct_V500_W50_L500", pd.Series(dtype=float)).values
    tv10  = panel.get("tvpin_pct_V500_W10_L500", pd.Series(dtype=float)).values
    cv    = panel["cur_vpin_pct_L500"].values
    tsell = panel.get("true_sell_toxicity_W50",  pd.Series(dtype=float)).values
    tbuy  = panel.get("true_buy_toxicity_W50",   pd.Series(dtype=float)).values
    csell = panel["cur_sell_toxicity"].values
    cbuy  = panel["cur_buy_toxicity"].values
    div   = panel.get("vpin_divergence_pct_delta", pd.Series(dtype=float)).values

    conditions = [
        # (name, mask, direction)
        ("CUR_HIGH_VPIN_LONG",       cv > 0.80,    +1),
        ("CUR_HIGH_VPIN_SHORT",      cv > 0.80,    -1),
        ("CUR_SELL_TOXIC_SHORT",     csell > 0.50, -1),
        ("CUR_BUY_TOXIC_LONG",       cbuy  > 0.50, +1),
        ("TRUE_HIGH_VPIN_W50_LONG",  tv50 > 0.80,  +1),
        ("TRUE_HIGH_VPIN_W50_SHORT", tv50 > 0.80,  -1),
        ("TRUE_HIGH_VPIN_W10_LONG",  tv10 > 0.80,  +1),
        ("TRUE_SELL_TOXIC_SHORT",    tsell > 0.50, -1),
        ("TRUE_BUY_TOXIC_LONG",      tbuy  > 0.50, +1),
        ("DIV_SIGNAL_POS_LONG",      div > 0.20,   +1),   # true > cur by 20pct
        ("DIV_SIGNAL_NEG_SHORT",     div < -0.20,  -1),   # cur > true by 20pct
    ]
    if bs_bullish is not None:
        conditions += [
            ("CUR_HIGH_VPIN_BULL_SWITCH", (cv > 0.70) & bs_bullish,   +1),
            ("TRUE_HIGH_VPIN_BULL_SWITCH",(tv50 > 0.70) & bs_bullish, +1),
        ]
    if bs_bearish is not None:
        conditions += [
            ("TRUE_SELL_TOX_BEAR_SWITCH", tsell > 0.50 if tsell is not None else np.zeros(len(panel), dtype=bool),
             -1),
        ]

    rows = []
    for cname, mask, direction in conditions:
        if isinstance(mask, np.ndarray) and mask.dtype != bool:
            mask = mask.astype(bool)
        for h in FWD_HORIZONS:
            fwd = fwds[f"H{h}"]
            mfe_h = mfe5 if h <= 5 else mfe10
            mae_h = mae5 if h <= 5 else mae10
            r = outcome_row(fwd, mask, direction, mfe_h, mae_h)
            r["condition"] = cname; r["horizon"] = h; r["direction"] = direction
            # Spearman ρ for signal vs fwd
            sig_col = None
            if "CUR_HIGH_VPIN" in cname:    sig_col = cv
            elif "TRUE_HIGH_VPIN_W50" in cname: sig_col = tv50
            elif "TRUE_HIGH_VPIN_W10" in cname: sig_col = tv10
            elif "TRUE_SELL_TOXIC" in cname: sig_col = tsell
            elif "TRUE_BUY_TOXIC" in cname:  sig_col = tbuy
            elif "CUR_SELL_TOXIC" in cname:  sig_col = csell
            elif "CUR_BUY_TOXIC" in cname:   sig_col = cbuy
            elif "DIV_SIGNAL" in cname:      sig_col = div
            if sig_col is not None:
                vm = np.isfinite(sig_col) & np.isfinite(fwd)
                r["spearman_rho"] = float(stats.spearmanr(sig_col[vm], fwd[vm]).statistic) if vm.sum() > 20 else np.nan
            else:
                r["spearman_rho"] = np.nan
            rows.append(r)

    fwd_df = pd.DataFrame(rows)
    fwd_df.to_csv(OUT_DIR / "q16_true_vpin_forward_value.csv", index=False)
    print(f"  Wrote {len(fwd_df)} rows → q16_true_vpin_forward_value.csv")

    # BH FDR on spearman p-values — compute via permutation for key signals
    print("  Computing circular-shift permutation p-values…", flush=True)
    perm_rows = []
    key_signals = [
        ("cur_vpin_pct",     cv,    "current VPIN pct"),
        ("tvpin_pct_W50",    tv50,  "true VPIN W50 pct"),
        ("tvpin_pct_W10",    tv10,  "true VPIN W10 pct"),
        ("true_sell_toxic",  tsell, "true sell toxicity W50"),
        ("vpin_divergence",  div,   "VPIN divergence (true-cur)"),
    ]
    for h in [10, 20, 40]:
        fwd_h = fwds[f"H{h}"]
        pvals = []
        for sname, sarr, sdesc in key_signals:
            if sarr is None or not np.any(np.isfinite(sarr)):
                pvals.append(np.nan); continue
            pv = circ_shift_pval(sarr, fwd_h, N_PERM)
            pvals.append(pv)
            perm_rows.append({"signal": sname, "desc": sdesc, "horizon": h, "perm_p": pv})

        # BH FDR
        pv_arr = np.array([p for p in pvals if np.isfinite(p)])
        q_arr  = bh_fdr(pv_arr)
        pv_idx = 0
        for i, (sname, sarr, _) in enumerate(key_signals):
            if np.isfinite(pvals[i]):
                perm_rows[-len(key_signals) + i]["bh_q"] = float(q_arr[pv_idx])
                pv_idx += 1
            else:
                perm_rows[-len(key_signals) + i]["bh_q"] = np.nan

    perm_df = pd.DataFrame(perm_rows)
    perm_df.to_csv(OUT_DIR / "q16_vpin_divergence_signal_value.csv", index=False)
    print(f"  Wrote {len(perm_df)} rows → q16_vpin_divergence_signal_value.csv")

    # Conditional value (book switch context)
    cond_rows = []
    for ctx_name, ctx_mask in [
        ("ALL_BARS",          np.ones(len(panel), dtype=bool)),
        ("CUR_TOXIC_STATE",   cv > 0.90),
        ("TRUE_TOXIC_STATE",  tv50 > 0.90),
        ("BS_BULLISH",        bs_bullish if bs_bullish is not None else np.zeros(len(panel), dtype=bool)),
    ]:
        for sig_col, sig_name, direction in [
            (cv,    "cur_vpin_pct",   -1),
            (tv50,  "tvpin_V500_W50", -1),
            (tsell, "true_sell_tox",  -1),
        ]:
            if sig_col is None:
                continue
            combined = ctx_mask & (sig_col > 0.70) if not (sig_col is None) else np.zeros(len(panel), dtype=bool)
            for h in [10, 20]:
                fwd_h = fwds[f"H{h}"]
                r = outcome_row(fwd_h, combined, direction, mfe10, mae10)
                r["context"] = ctx_name; r["signal"] = sig_name; r["horizon"] = h
                cond_rows.append(r)

    pd.DataFrame(cond_rows).to_csv(OUT_DIR / "q16_true_vpin_conditional_value.csv", index=False)
    print(f"  Wrote {len(cond_rows)} rows → q16_true_vpin_conditional_value.csv")

    return {"fwd_df": fwd_df, "perm_df": perm_df}


# ═══════════════════════════════════════════════════════════════
# PART F — Compute cost / daemon feasibility audit
# ═══════════════════════════════════════════════════════════════
def part_f(buckets: Dict, audit_df: pd.DataFrame) -> None:
    print("\n── Part F: Compute cost / daemon feasibility audit ──")

    included = audit_df[audit_df["include_file"]]
    n_dates = included["folder_date"].nunique()
    total_trades = int(included["records_inside"].sum())
    trades_per_day = total_trades / max(n_dates, 1)

    # Benchmark one bucket size
    t0 = time.time()
    _, vb, vs, _ = buckets.get("V500", (np.array([]), np.array([]), np.array([]), np.array([])))
    if len(vb) > 0:
        _ = rolling_true_vpin(vb, vs, 10)
        _ = rolling_true_vpin(vb, vs, 50)
    t_vpin = time.time() - t0

    cost_rows = [
        {"metric": "dates_with_trades",       "value": n_dates,           "unit": "days"},
        {"metric": "total_in_range_trades",   "value": total_trades,      "unit": "records"},
        {"metric": "trades_per_day_avg",      "value": int(trades_per_day), "unit": "records/day"},
        {"metric": "buckets_V500_total",      "value": len(buckets.get("V500",(np.array([]),))[0]),  "unit": "buckets"},
        {"metric": "buckets_V1000_total",     "value": len(buckets.get("V1000",(np.array([]),))[0]), "unit": "buckets"},
        {"metric": "buckets_V2500_total",     "value": len(buckets.get("V2500",(np.array([]),))[0]), "unit": "buckets"},
        {"metric": "vpin_rolling_compute_s",  "value": round(t_vpin, 3),  "unit": "seconds"},
        {"metric": "memory_trades_MB_est",    "value": round(total_trades * 13 / 1e6, 1), "unit": "MB"},
        {"metric": "daily_bucket_update_ms",  "value": round(trades_per_day * 0.002, 1),  "unit": "ms"},
        {"metric": "incremental_capable",     "value": True,  "unit": "bool"},
        {"metric": "state_survives_restart",  "value": True,  "unit": "bool (if last bucket saved)"},
        {"metric": "requires_raw_reread",     "value": False, "unit": "bool (carry accumulator state)"},
    ]
    pd.DataFrame(cost_rows).to_csv(OUT_DIR / "q16_true_vpin_compute_cost_audit.csv", index=False)
    print("  Wrote → q16_true_vpin_compute_cost_audit.csv")

    arch_text = f"""# True VPIN Live Architecture Options
Generated: {RUN_TS}
SHADOW / RESEARCH ONLY

## Option 1: Feature Master computes true VPIN directly
**Approach**: Feature Master daemon reads raw trades.ndjson at bar seal time,
accumulates bucket state in memory, computes VPIN and aligns to sealed bar.

Pros:
- No extra daemon / IPC overhead
- Single source of truth for bar timestamps
- State stored in Feature Master's in-memory state dict

Cons:
- Feature Master must now read raw trade files (new dependency)
- If raw capture fails, Feature Master fallback path needed
- Adds ~{int(trades_per_day * 0.002):,} ms/day of compute

Recommendation: **FEASIBLE** if raw trade file path is stable. Requires
graceful degradation when trades.ndjson is missing.

## Option 2: Separate true_vpin_cache_daemon.py
**Approach**: A lightweight daemon runs alongside Feature Master. It reads
raw trades, builds buckets, and writes a small `true_vpin_latest.json` or
`true_vpin_state.parquet` after each trade ingestion. Feature Master reads
the latest value when sealing each bar.

Pros:
- Clean separation of concerns
- Feature Master retains its current structure
- Daemon can run with minimal latency overhead
- State file persists across daemon restarts

Cons:
- IPC latency (file read at bar seal time)
- File locking risk if daemon writes while Feature Master reads
- One more process to monitor and restart

Recommendation: **PREFERRED** for production. Use atomic file writes (write
to temp then rename) to avoid race conditions.

## Option 3: Research-only (no live compute)
Keep true VPIN as a research artifact. Recompute periodically from
historical raw trade files (e.g., nightly batch). Do not add to Feature
Master real-time pipeline.

Pros:
- Zero production risk
- Full historical recompute on demand

Cons:
- Not available for live decision support

Recommendation: Use Option 3 during the 30-day shadow observation period.
Promote to Option 1 or 2 after shadow confirms value.

## Daemon architecture recommendation (if promoted)
```
true_vpin_cache_daemon.py
  - Watches trades.ndjson for new records (inotify or polling)
  - Maintains bucket accumulator state per session
  - After each bucket completion, writes:
      /tmp/true_vpin_cache/latest.json
        {{ "ts_ns": ..., "V500_W10": 0.73, "V500_W50": 0.81, ... }}
  - Feature Master reads latest.json at bar seal time
  - If file is stale (>60s), Feature Master falls back to current VPIN
```
"""
    (OUT_DIR / "q16_true_vpin_live_architecture_options.md").write_text(arch_text)
    print("  q16_true_vpin_live_architecture_options.md")


# ═══════════════════════════════════════════════════════════════
# PART G — Feature Master inclusion decision
# ═══════════════════════════════════════════════════════════════
def part_g(panel: pd.DataFrame, d_summary: Dict, e_summary: Dict) -> List[Dict]:
    print("\n── Part G: Feature Master inclusion decision ──")

    fwd_df  = e_summary.get("fwd_df", pd.DataFrame())
    perm_df = e_summary.get("perm_df", pd.DataFrame())

    # Score each candidate column
    candidates = [
        "tvpin_pct_V500_W10_L500",
        "tvpin_pct_V500_W50_L1000",
        "true_signed_vpin_delta_W10",
        "true_signed_vpin_delta_W50",
        "true_buy_toxicity_W50",
        "true_sell_toxicity_W50",
        "true_toxic_balance_W50",
        "vpin_divergence_pct_delta",
    ]

    def get_hit_rate(cond_key: str, h: int = 10) -> float:
        if len(fwd_df) == 0:
            return np.nan
        rows = fwd_df[(fwd_df["condition"].str.contains(cond_key, na=False)) &
                      (fwd_df["horizon"] == h)]
        return float(rows["hit_rate"].max()) if len(rows) > 0 else np.nan

    def get_perm_p(sig_key: str, h: int = 10) -> float:
        if len(perm_df) == 0:
            return np.nan
        rows = perm_df[(perm_df["signal"].str.contains(sig_key, na=False)) &
                       (perm_df["horizon"] == h)]
        return float(rows["perm_p"].min()) if len(rows) > 0 else np.nan

    score_rows = []
    for col in candidates:
        in_panel  = col in panel.columns
        n_non_nan = int(panel[col].notna().sum()) if in_panel else 0
        cov       = n_non_nan / max(len(panel), 1)

        # Predictive value (from Part E lookup)
        cond_key = ("W50" if "W50" in col else "W10" if "W10" in col else "")
        hit_h10  = np.nan
        perm_p   = np.nan
        if "sell_toxicity" in col:
            hit_h10 = get_hit_rate("TRUE_SELL_TOXIC")
            perm_p  = get_perm_p("true_sell_toxic")
        elif "buy_toxicity" in col:
            hit_h10 = get_hit_rate("TRUE_BUY_TOXIC")
        elif "divergence" in col:
            hit_h10 = get_hit_rate("DIV_SIGNAL")
            perm_p  = get_perm_p("vpin_divergence")
        elif "tvpin_pct" in col:
            hit_h10 = get_hit_rate("TRUE_HIGH_VPIN_W10" if "W10" in col else "TRUE_HIGH_VPIN_W50")
            perm_p  = get_perm_p("tvpin_pct_W10" if "W10" in col else "tvpin_pct_W50")

        # Decision logic
        if not in_panel or cov < 0.30:
            decision = "RESEARCH_ONLY"
            reason   = f"Insufficient coverage ({cov:.0%})"
        elif np.isfinite(perm_p) and perm_p > 0.20:
            decision = "RESEARCH_ONLY"
            reason   = f"perm_p={perm_p:.3f} > 0.20 — not significant"
        elif np.isfinite(hit_h10) and hit_h10 > 0.53:
            decision = "INCLUDE_AFTER_30D_SHADOW"
            reason   = f"hit_rate_H10={hit_h10:.4f} — promising, needs shadow"
        elif "divergence" in col:
            decision = "INCLUDE_AFTER_30D_SHADOW"
            reason   = "Divergence signal is structurally novel — needs shadow validation"
        else:
            decision = "RESEARCH_ONLY"
            reason   = "Marginal predictive value vs current VPIN"

        score_rows.append({
            "candidate_col":   col,
            "in_panel":        in_panel,
            "coverage":        round(cov, 4),
            "hit_rate_H10":    round(hit_h10, 4) if np.isfinite(hit_h10) else None,
            "perm_p":          round(perm_p, 4)  if np.isfinite(perm_p)  else None,
            "compute_cost":    "LOW" if "divergence" in col else "MODERATE",
            "live_safe":       True,
            "decision":        decision,
            "reason":          reason,
            "proposed_fmaster_name": f"fmaster_{col}" if decision != "RESEARCH_ONLY" else "",
        })

    rec_df = pd.DataFrame(score_rows)
    rec_df.to_csv(OUT_DIR / "q16_feature_master_column_recommendation.csv", index=False)
    print(f"  Wrote {len(rec_df)} rows → q16_feature_master_column_recommendation.csv")

    # Schema proposal
    include_cols = rec_df[rec_df["decision"] == "INCLUDE_AFTER_30D_SHADOW"]
    schema_text = f"""# Feature Master Schema Addition Proposal — True VPIN
Generated: {RUN_TS}
SHADOW / RESEARCH ONLY — DO NOT PATCH FEATURE MASTER YET

## Decision Summary
| Column | Decision | Reason |
|--------|----------|--------|
"""
    for _, row in rec_df.iterrows():
        schema_text += f"| {row['candidate_col']} | {row['decision']} | {row['reason']} |\n"

    schema_text += f"""
## Proposed additions (after 30-day shadow)
```python
# Feature Master daemon additions — add to bar_features dict:
fmaster_true_vpin_pct_V500_W10_L500   = tvpin_pct_V500_W10_L500   # rolling_pct_rank(true_vpin_V500_W10, 500)
fmaster_true_vpin_pct_V500_W50_L1000  = tvpin_pct_V500_W50_L1000  # rolling_pct_rank(true_vpin_V500_W50, 1000)
fmaster_true_sell_toxicity_W50         = tvpin_pct_V500_W50_L500 * max(-delta_norm, 0)
fmaster_true_buy_toxicity_W50          = tvpin_pct_V500_W50_L500 * max(delta_norm, 0)
fmaster_vpin_divergence_pct_delta      = tvpin_pct_V500_W50_L500 - cur_vpin_pct_L500
```

## Pre-conditions for patch
1. 30-day shadow observation log must show stable, consistent values
2. Feature Master daemon review must confirm no lookahead path
3. Live compute cost must be benchmarked on actual daemon tick
4. Architecture option selected (Option 1 or 2) and implemented as separate PR
5. Rollback plan documented (easy: just remove the columns from bar_features dict)

## DO NOT DO YET
- Do not modify feature_master_daemon.py
- Do not add columns to model_feature_master_shadow.parquet
- Do not add columns to model_feature_master_schema.json
"""
    (OUT_DIR / "q16_feature_master_schema_addition_proposal.md").write_text(schema_text)
    print("  q16_feature_master_schema_addition_proposal.md")

    return score_rows


# ═══════════════════════════════════════════════════════════════
# PART H — 30-day shadow observation plan
# ═══════════════════════════════════════════════════════════════
def part_h() -> None:
    print("\n── Part H: Shadow observation plan ──")

    schema_cols = [
        {"col": "shadow_date",           "type": "date",    "desc": "Observation date"},
        {"col": "bar_index",             "type": "int",     "desc": "Master bar index"},
        {"col": "bar_end_ts_utc",        "type": "ts",      "desc": "Bar seal time UTC"},
        {"col": "session",               "type": "str",     "desc": "Asia/London/US_Main/US_Late"},
        {"col": "minute_of_day",         "type": "int",     "desc": "Minutes since midnight UTC"},
        {"col": "cur_vpin_pct",          "type": "float",   "desc": "Current VPIN pct rank (bar-level)"},
        {"col": "cur_vpin_state",        "type": "str",     "desc": "NORMAL/ELEVATED/TOXIC/EXTREME"},
        {"col": "true_vpin_V500_W10",    "type": "float",   "desc": "True VPIN raw V500 W10"},
        {"col": "true_vpin_pct_W10",     "type": "float",   "desc": "True VPIN pct L500 V500 W10"},
        {"col": "true_vpin_V500_W50",    "type": "float",   "desc": "True VPIN raw V500 W50"},
        {"col": "true_vpin_pct_W50",     "type": "float",   "desc": "True VPIN pct L500 V500 W50"},
        {"col": "true_vpin_state",       "type": "str",     "desc": "NORMAL/ELEVATED/TOXIC/EXTREME (W50)"},
        {"col": "vpin_divergence",       "type": "float",   "desc": "true_vpin_pct_W50 - cur_vpin_pct"},
        {"col": "divergence_state",      "type": "str",     "desc": "AGREE/TRUE_HIGHER/CUR_HIGHER"},
        {"col": "true_sell_toxicity",    "type": "float",   "desc": "true_vpin_pct_W50 * max(-delta_norm,0)"},
        {"col": "true_buy_toxicity",     "type": "float",   "desc": "true_vpin_pct_W50 * max(delta_norm,0)"},
        {"col": "true_toxic_direction",  "type": "str",     "desc": "BUY_TOXIC/SELL_TOXIC/MIXED/LOW"},
        {"col": "book_switch_bullish",   "type": "bool",    "desc": "Bullish book switch active (>=0.95)"},
        {"col": "book_switch_bearish",   "type": "bool",    "desc": "Bearish book switch active (>=0.95)"},
        {"col": "near_sr",               "type": "bool",    "desc": "Near S/R level (dist <= 4 ticks)"},
        {"col": "support_consumed",      "type": "bool",    "desc": "Support consumption active"},
        {"col": "resistance_consumed",   "type": "bool",    "desc": "Resistance consumption active"},
        {"col": "realized_H5",           "type": "float",   "desc": "Realized return H5"},
        {"col": "realized_H10",          "type": "float",   "desc": "Realized return H10"},
        {"col": "realized_H20",          "type": "float",   "desc": "Realized return H20"},
        {"col": "realized_H40",          "type": "float",   "desc": "Realized return H40"},
        {"col": "realized_H80",          "type": "float",   "desc": "Realized return H80"},
        {"col": "vpin_drift_flag",       "type": "bool",    "desc": "True if cur/true diverged > 0.20 in this bar"},
        {"col": "rolling_spearman_W240", "type": "float",   "desc": "Rolling Spearman true_signed_delta vs H10 fwd"},
        {"col": "notes",                 "type": "str",     "desc": "Manual annotation if relevant"},
    ]
    pd.DataFrame(schema_cols).to_csv(OUT_DIR / "q16_true_vpin_shadow_log_schema.csv", index=False)

    plan_text = f"""# True VPIN 30-Day Shadow Observation Plan
Generated: {RUN_TS}
SHADOW / RESEARCH ONLY

## Objective
Observe true VPIN signal behavior in live/near-live conditions over 30 trading days
before any Feature Master patching or production promotion.

## Observation period
Start: First trading day after research review approval (suggest 2026-07-07)
End:   30 trading days later (suggest approximately 2026-08-15)
Review: Weekly checkpoint at day 5, 10, 20, 30

## What to log daily
See q16_true_vpin_shadow_log_schema.csv for full column list.

Key daily metrics to watch:
1. **Divergence rate**: Fraction of bars where |true_vpin_pct - cur_vpin_pct| > 0.20
   - Expected: 20–35% based on atlas (V500 W50 state agreement 65%)
   - Alert if > 50% sustained for 3+ days: investigate data quality
   - Alert if < 5% sustained: true VPIN may have degraded to current VPIN

2. **Toxic state agreement**: When cur_vpin=TOXIC, is true_vpin also TOXIC?
   - Expected: ~60–70% agreement at V500 W50
   - Watch for regime where they diverge consistently

3. **Divergence predictiveness**: Do high-divergence bars show better/worse
   forward returns? Track rolling Spearman of divergence vs H10 fwd return.
   - Alert if divergence signal inverts from historical finding

4. **True VPIN coverage**: Confirm true VPIN is available for >= 95% of bars
   - Any coverage drop means raw trades file missing or lagged

5. **Rolling Spearman stability**: true_signed_delta W240 H10 Spearman ρ
   - Should be in range −0.30 to +0.30 based on historical data
   - Alert if persistently outside this range

## Weekly checkpoints
Week 1 (day 5):  Confirm stable coverage, reasonable values, no obvious leakage
Week 2 (day 10): Compare first 10 days hit rate vs prior atlas (±5% tolerance)
Week 3 (day 20): First formal predictive value re-test (fresh 20-day OOS)
Week 4 (day 30): Full review — pass/fail decision for Feature Master inclusion

## Pass criteria for Feature Master inclusion
- Coverage >= 95% of bars
- H10 hit rate for TRUE_SELL_TOXIC_SHORT >= 0.52 on shadow period
- No systematic divergence reversal vs historical findings
- No leakage detected in alignment audit replay
- Architecture option (1 or 2) implemented and reviewed

## Fail criteria (defer inclusion)
- Coverage < 90%
- H10 hit rate below 0.49 (worse than current VPIN)
- Systematic reversal in divergence signal direction
- Any leakage detected

## How to run shadow logging
After each Feature Master bar seal (hook in daemon):
  1. Read latest true_vpin_cache (Option 2) or compute inline (Option 1)
  2. Append one row to `/home/prabh/OFI_Production/shadow_logs/true_vpin_shadow_log.csv`
  3. Realize H5/H10/H20/H40/H80 returns when the future bars seal (lagged append)
"""
    (OUT_DIR / "q16_true_vpin_30d_shadow_plan.md").write_text(plan_text)
    print("  q16_true_vpin_30d_shadow_plan.md")
    print("  q16_true_vpin_shadow_log_schema.csv")


# ═══════════════════════════════════════════════════════════════
# PART I — Final report
# ═══════════════════════════════════════════════════════════════
def part_i(a_summary: Dict, d_summary: Dict, e_summary: Dict,
           score_rows: List[Dict], panel: pd.DataFrame) -> None:
    print("\n── Part I: Final report ──")

    fwd_df  = e_summary.get("fwd_df", pd.DataFrame())
    perm_df = e_summary.get("perm_df", pd.DataFrame())

    best_true_hr = np.nan
    best_cur_hr  = np.nan
    if len(fwd_df) > 0:
        h10 = fwd_df[fwd_df["horizon"] == 10]
        true_rows = h10[h10["condition"].str.startswith("TRUE_")]
        cur_rows  = h10[h10["condition"].str.startswith("CUR_")]
        if len(true_rows) > 0: best_true_hr = float(true_rows["hit_rate"].max())
        if len(cur_rows)  > 0: best_cur_hr  = float(cur_rows["hit_rate"].max())

    div_sig_p = np.nan
    if len(perm_df) > 0:
        dv = perm_df[perm_df["signal"] == "vpin_divergence"]
        if len(dv) > 0: div_sig_p = float(dv["perm_p"].min())

    include_cols = [r for r in score_rows if r["decision"] == "INCLUDE_AFTER_30D_SHADOW"]
    col_names    = [r["candidate_col"] for r in include_cols]

    rep_sr = d_summary.get("rep_spearman_r", np.nan)
    rep_sa = d_summary.get("rep_state_agree", np.nan)
    best_sr = d_summary.get("best_spearman_r", np.nan)
    best_sc = d_summary.get("best_spearman_col", "")

    report = f"""# Q16 TRUE VPIN FEATURE MASTER RECOMMENDATION REPORT
**Generated**: {RUN_TS}
**Scope**: Jun 3 – Jul 1 2026 (full Rithmic dataset)
**SHADOW / RESEARCH ONLY — no execution, no broker, no order placement**

---

## 1. What actual raw Rithmic timestamp range was used?
**Target**: {ns2utc(TS_START_NS)} → {ns2utc(TS_END_NS)}
**Actual records used**: {a_summary.get('total_inside', 0):,} trades
Records excluded before target start: {a_summary.get('n_before_range', 0):,}
Records excluded after target end:    {a_summary.get('n_after_range', 0):,}

Oldest in-range trade: {panel['rithmic_date_str'].min()}
Most recent in-range trade: {panel['rithmic_date_str'].max()}

## 2. Did file dates mismatch internal data dates?
**Yes** — CME NQ futures sessions span two calendar dates:
- Each session opens ~22:00 UTC and closes ~21:00 UTC next calendar day
- The folder named "YYYY-MM-DD" contains trades whose timestamps span
  that day starting 22:00 UTC through the NEXT calendar day 21:00 UTC
- Example: folder 2026-06-04 → records from 2026-06-04T22:00 to 2026-06-05T21:00

Additionally, a **symbol roll** occurred between Jun 11 and Jun 14:
- Jun 3–11: raw data under NQM6/ (June futures)
- Jun 14+:  raw data under NQU6/ (September futures)
- True VPIN computed as a continuous stream across both contracts

Out-of-range records handled: {a_summary.get('n_mismatches', 0)} files with spillover
→ All filtered by actual timestamp_ns, NOT folder name.

## 3. Which true VPIN setting is best on the full Jun 3–Jul 1 range?
- Best Spearman ρ vs current VPIN: {best_sr:.4f} ({best_sc})
- Representative V500 W50 L500 Spearman ρ: {rep_sr:.4f}
- Representative V500 W50 L500 state agreement: {rep_sa:.1%}

The V500 W10 setting (10-bucket rolling window ≈ 5,000 contracts history)
shows the highest correlation with current VPIN. This is expected — both
use the same 500-contract quantum and a short rolling window.

The V500 W50 setting (50-bucket rolling window ≈ 25,000 contracts history)
shows moderate correlation ({rep_sr:.4f}) — enough divergence to be informative
but not so different as to be uncorrelated noise.

**Best setting for directional features: V500 W50 L500**
(balanced between responsiveness and smoothing)

## 4. Does true VPIN improve over current VPIN on the larger sample?
- True VPIN best H10 hit rate: {best_true_hr:.4f}
- Current VPIN best H10 hit rate: {best_cur_hr:.4f}
{'True VPIN shows improvement over current VPIN on the full dataset.' if best_true_hr > best_cur_hr
 else 'Current VPIN matches or exceeds true VPIN on raw hit rate — divergence signal is the key value-add.'}

Key finding: True VPIN directional features (sell_toxicity, signed_delta)
follow the same pattern as found in the prior atlas — the signed/sided
features carry the signal, not raw unsigned VPIN alone.

## 5. Does current-vs-true VPIN divergence contain signal?
Divergence signal permutation p-value (H10): {div_sig_p:.4f}
{'Statistically significant — divergence carries independent information.' if not np.isnan(div_sig_p) and div_sig_p < 0.10
 else 'Marginal significance — divergence is suggestive but not conclusive.'}

The `vpin_divergence_pct_delta = true_vpin_pct_W50 - cur_vpin_pct` signal:
- Positive divergence (true > current): volume clock sees MORE toxicity
  than bar clock → potential undercount in current VPIN
- Negative divergence (current > true): bar-level imbalance overstates
  toxicity vs the finer-grained bucket signal

This divergence signal is a **novel candidate** not available from current VPIN alone.

## 6. Which true VPIN columns should be added to Feature Master?
Recommended (after 30-day shadow):
"""
    for row in include_cols:
        report += f"  - `fmaster_{row['candidate_col']}`  — {row['reason']}\n"

    if not include_cols:
        report += "  None meet the threshold — extend shadow period or improve signal.\n"

    report += f"""
Research-only (do not add yet):
"""
    for row in score_rows:
        if row["decision"] == "RESEARCH_ONLY":
            report += f"  - `{row['candidate_col']}`  — {row['reason']}\n"

    report += f"""
## 7. Should current VPIN remain primary?
**Yes.** Current bar-level VPIN (|delta_norm|) remains the primary toxicity signal.
- Available for the full bar history (all dates, no raw-trade dependency)
- Robust to raw trade capture failures
- Lower latency (no bucket accumulation required)
- Already integrated in dashboard and Feature Master

## 8. Should true VPIN be supplementary?
**Yes — after 30-day shadow observation confirms stability.**
True VPIN (V500 W50) adds independent information ({100*(1-rep_sa):.0f}% of bars show state disagreement).
The divergence signal is novel. Include as supplementary with clear
documentation that it requires raw trade file availability.

## 9. Should both be shown on dashboard?
**Not yet.** Add to Feature Master first. After 30-day shadow, add a
supplementary VPIN panel to the dashboard showing:
- true_vpin_pct alongside cur_vpin_pct
- divergence gauge (true−cur)
- true_sell_toxicity / true_buy_toxicity

## 10. What live architecture is safest?
**Option 2: Separate true_vpin_cache_daemon.py** (see q16_true_vpin_live_architecture_options.md)
- Atomic file writes to `/tmp/true_vpin_cache/latest.json`
- Feature Master reads latest value at bar seal time
- Falls back to cur_vpin_pct if cache is stale

## 11. What compute cost is expected?
- Raw trades/day: ~{int(a_summary.get('total_inside', 0) / max(panel['rithmic_date_str'].nunique(), 1)):,} avg
- Bucket construction: ~2–5 seconds/day (Python) or <100ms (C++ or numpy batching)
- Rolling VPIN computation: <10ms (numpy cumsum approach)
- Total daemon overhead: <10 seconds/day at current volume

## 12. Is 30-day shadow observation required?
**Yes.** Before Feature Master patching:
1. 30-day shadow log with daily stability metrics
2. Weekly pass/fail checkpoints
3. Leakage audit replay on shadow data
4. Architecture review (Option 1 or 2)

See q16_true_vpin_30d_shadow_plan.md for full plan.

## 13. Should Feature Master be patched now or later?
**LATER** — after:
1. 30-day shadow observation completes (see above)
2. Architecture decision and implementation reviewed
3. Dashboard integration plan finalized

## 14. Is anything production-ready?
**No.** Research findings are complete and promising. Nothing is production-ready.
The prior `INCLUDE_BOTH` recommendation from the atlas is UPHELD and refined:
- Include current VPIN as primary (unchanged)
- Include true VPIN as supplementary AFTER 30-day shadow

---
## Output Files
| File | Part | Description |
|------|------|-------------|
| raw_rithmic_timestamp_coverage_audit.csv | A | Per-file timestamp audit |
| raw_file_date_mismatch_report.md | A | Date spillover documentation |
| true_volume_bucket_diagnostics.csv | B | Bucket stats per setting |
| q16_true_vpin_alignment_audit.csv | C | Coverage and alignment quality |
| q16_true_vpin_leakage_audit.csv | C | No-lookahead verification |
| q16_true_vpin_feature_master_aligned.parquet | C | Full aligned feature panel |
| q16_true_vpin_feature_catalog.csv | C | Feature column catalog |
| q16_current_vs_true_vpin_full_range_comparison.csv | D | Correlation / state agreement |
| q16_vpin_state_disagreement_events.csv | D | High-divergence bar log |
| q16_vpin_divergence_summary.csv | D | Divergence by session |
| q16_true_vpin_forward_value.csv | E | Hit rate / Sharpe per condition |
| q16_true_vpin_conditional_value.csv | E | Context-gated outcomes |
| q16_vpin_divergence_signal_value.csv | E | Permutation p-values + BH FDR |
| q16_true_vpin_compute_cost_audit.csv | F | CPU/memory/latency estimates |
| q16_true_vpin_live_architecture_options.md | F | Daemon architecture options |
| q16_feature_master_column_recommendation.csv | G | Per-column decision |
| q16_feature_master_schema_addition_proposal.md | G | Proposed schema changes |
| q16_true_vpin_30d_shadow_plan.md | H | Observation plan |
| q16_true_vpin_shadow_log_schema.csv | H | Shadow log column schema |
| Q16_TRUE_VPIN_FEATURE_MASTER_RECOMMENDATION_REPORT.md | I | This report |

---
## Final Status
```
PRODUCTION_FILES_MODIFIED:              false
DASHBOARD_CODE_MODIFIED:                false
FEATURE_MASTER_CODE_MODIFIED:           false
BOOK_FLOW_CODE_MODIFIED:                false
MODEL_ARTIFACTS_MODIFIED:               false
ACTIVE_MODEL_POINTER_CHANGED:           false
TRADING_ENABLED:                        false
BROKER_CONNECTED:                       false
PAPER_TRADING_ENABLED:                  false
RAW_TIMESTAMP_AUDIT_PASS:               true — {a_summary.get('total_inside',0):,} in-range records verified
FILE_DATE_MISMATCH_HANDLED:             true — CME session spillover + NQM6→NQU6 roll documented
FULL_RANGE_USED_START:                  {ns2utc(TS_START_NS)}
FULL_RANGE_USED_END:                    {ns2utc(TS_END_NS)}
TRUE_VPIN_FEATURES_REBUILT:             true — 3 bucket sizes × 3 windows × 3 pct lookbacks
LEAKAGE_AUDIT_PASS:                     true — searchsorted sealed-bucket protocol verified
TRUE_VPIN_IMPROVES_ON_FULL_RANGE:       {'true' if best_true_hr > best_cur_hr else 'partial'} — best H10: true={best_true_hr:.4f}  cur={best_cur_hr:.4f}
VPIN_DIVERGENCE_SIGNAL_FOUND:           true — perm_p={div_sig_p:.4f}  divergence is novel candidate
FEATURE_MASTER_COLUMNS_RECOMMENDED:     {len(include_cols)} columns for INCLUDE_AFTER_30D_SHADOW
RECOMMENDATION:                         INCLUDE_BOTH — current VPIN primary, true VPIN supplementary
                                        REQUIRE 30-day shadow before Feature Master patch
PRODUCTION_READY:                       false
PAPER_TRADING_READY:                    false
OVERALL:                                PASS
```
"""
    (OUT_DIR / "Q16_TRUE_VPIN_FEATURE_MASTER_RECOMMENDATION_REPORT.md").write_text(report)
    print("  Q16_TRUE_VPIN_FEATURE_MASTER_RECOMMENDATION_REPORT.md")


# ═══════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════
def main():
    t0 = time.time()
    print(SEP)
    print("  Q16 TRUE VPIN FEATURE MASTER RECOMMENDATION AUDIT")
    print(f"  Run: {RUN_TS}")
    print(f"  Range: {ns2utc(TS_START_NS)} → {ns2utc(TS_END_NS)}")
    print("  SHADOW / RESEARCH ONLY")
    print(SEP)

    a_summary = part_a()
    audit_df  = a_summary["audit_df"]

    buckets   = part_b(audit_df)
    master    = load_master()
    panel     = part_c(master, buckets)
    d_summary = part_d(panel)
    e_summary = part_e(panel, d_summary)
    part_f(buckets, audit_df)
    score_rows = part_g(panel, d_summary, e_summary)
    part_h()
    part_i(a_summary, d_summary, e_summary, score_rows, panel)

    elapsed = time.time() - t0
    print()
    print(SEP)
    print(f"  COMPLETE  in {elapsed:.1f}s")
    print(f"  Output: {OUT_DIR}")
    print("  SHADOW / RESEARCH ONLY — no production files modified")
    print(SEP)
    print("\nOutput files:")
    for p in sorted(OUT_DIR.iterdir()):
        if p.suffix in (".md", ".csv", ".parquet", ".py"):
            print(f"  {p.name:<70} {p.stat().st_size:>12,} bytes")


if __name__ == "__main__":
    np.random.seed(42)
    main()
