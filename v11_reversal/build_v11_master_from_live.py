#!/usr/bin/env python3
"""
build_v11_master_from_live.py

SHADOW / RESEARCH ONLY.

Builds a fresh v11-reversal training master CSV from the live continuous-backadjusted
feature master, WITHOUT ever modifying or moving the live source file.

Source (READ-ONLY, never written to): /home/prabh/OFI_Live_Features/master_NQ_continuous_backadjusted_shadow.ndjsonl
Training script this feeds:          /mnt/wd_work/workspace/Work Place/Data/Project OFI/tools/Training/misc/train_v11_reversal_models_vol500.py

Known caveat (reported, not silently resolved): the live ndjsonl only goes back to ~2026-06-03
(~8 weeks). The previously-used training master (OFI_vol500_MASTER.csv) covered 2025-08-20 to
2026-02-17. There is a gap between 2026-02-17 and 2026-06-03 that this script does NOT attempt to
fill (no source for that window was found). Some training sessions' lookback windows (90-120
session-days) will therefore see less history than the original run; the training script itself
degrades gracefully (uses everything available rather than failing) -- see provenance_report.json
for exactly how many session-days ended up available.

Run:
    python3 build_v11_master_from_live.py --dry-run
    python3 build_v11_master_from_live.py
"""

import os
import sys
import json
import shutil
import argparse
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

LIVE_SOURCE = Path("/home/prabh/OFI_Live_Features/master_NQ_continuous_backadjusted_shadow.ndjsonl")
TRAIN_SCRIPT_PATH = Path(
    "/mnt/wd_work/workspace/Work Place/Data/Project OFI/tools/Training/misc/train_v11_reversal_models_vol500.py"
)
OUTPUT_ROOT = Path("/home/prabh/OFI_Production/v11_reversal/masters")

# Columns that exist in the live master but are out of scope for v11 training -- dropped, not
# renamed or repurposed. The first group is continuous-contract roll-adjustment bookkeeping; the
# second group belongs to an unrelated project (TFT regime model) that happens to share this master.
DROP_COLUMNS = [
    "is_backadjusted_history", "roll_pair", "cumulative_roll_adjustment_points",
    "is_current_contract", "is_roll_boundary", "source_contract", "contract_symbol",
    "prev_wk_h40_ic", "prev_wk_us_ic", "prev_wk_val_loss",
    "regime_ic_delta", "regime_ic_mlofi", "regime_ic_mlofi20",
]

# Columns the training script's _adapt_vol500_to_canonical() derives automatically if absent --
# a gap here is expected-safe, not an error.
AUTO_DERIVABLE = {"rvol", "mlofi_rolling_5", "delta_rolling_5", "mlofi_accel", "mid_resid_std", "mid_kf"}


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_raw_vol500_features():
    """Import RAW_VOL500_FEATURES directly from the training script so this list can never
    drift out of sync with what the trainer actually expects."""
    spec = importlib.util.spec_from_file_location("train_v11_reversal_models_vol500", TRAIN_SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # required before exec_module: the script's @dataclass decorators
                                   # resolve their own module via sys.modules during class creation
    spec.loader.exec_module(mod)
    return list(mod.RAW_VOL500_FEATURES)


def copy_source_ndjsonl(dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "master_NQ_continuous_backadjusted_shadow.ndjsonl"
    log(f"Copying live source (read-only) {LIVE_SOURCE} -> {dest} ...")
    shutil.copy2(LIVE_SOURCE, dest)
    return dest


def ndjsonl_to_dataframe(path: Path) -> pd.DataFrame:
    log(f"Parsing {path} ...")
    rows = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    df = pd.DataFrame(rows)
    present_drop = [c for c in DROP_COLUMNS if c in df.columns]
    df = df.drop(columns=present_drop)
    log(f"  parsed {len(df)} rows, {len(df.columns)} columns (dropped {len(present_drop)} out-of-scope columns)")
    return df


def check_feature_parity(df: pd.DataFrame, raw_features: list) -> dict:
    present = [c for c in raw_features if c in df.columns]
    missing = [c for c in raw_features if c not in df.columns]
    missing_auto = [c for c in missing if c in AUTO_DERIVABLE]
    missing_unexpected = [c for c in missing if c not in AUTO_DERIVABLE]
    report = {
        "n_raw_features_expected": len(raw_features),
        "n_present": len(present),
        "missing_auto_derivable": missing_auto,
        "missing_unexpected": missing_unexpected,
    }
    if missing_unexpected:
        log(f"  WARNING: {len(missing_unexpected)} RAW_VOL500_FEATURES columns are missing and NOT "
            f"auto-derivable by the training script: {missing_unexpected}")
    else:
        log(f"  feature parity OK: {len(present)}/{len(raw_features)} present, "
            f"{len(missing_auto)} missing-but-auto-derivable ({missing_auto or 'none'})")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Report only, write no CSV")
    parser.add_argument("--out-dir", default=None, help="Override output dir (default: timestamped)")
    args = parser.parse_args()

    if not LIVE_SOURCE.exists():
        raise SystemExit(f"Live source not found: {LIVE_SOURCE}")

    run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    out_dir = Path(args.out_dir) if args.out_dir else OUTPUT_ROOT / run_ts
    out_dir.mkdir(parents=True, exist_ok=True)

    src_before_mtime = LIVE_SOURCE.stat().st_mtime
    dest = copy_source_ndjsonl(out_dir)
    src_after_mtime = LIVE_SOURCE.stat().st_mtime
    if src_before_mtime != src_after_mtime:
        raise SystemExit("REFUSING TO CONTINUE: live source mtime changed during copy -- integrity check failed.")

    df = ndjsonl_to_dataframe(dest)
    for c in ("timestamp", "timestamp_utc"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")
    df = df.sort_values("timestamp_utc" if "timestamp_utc" in df.columns else "timestamp").reset_index(drop=True)

    raw_features = load_raw_vol500_features()
    parity = check_feature_parity(df, raw_features)

    ts_col = df["timestamp_utc"] if "timestamp_utc" in df.columns else df["timestamp"]
    date_min, date_max = str(ts_col.min()), str(ts_col.max())
    n_session_days = int(ts_col.dt.date.nunique())

    provenance = {
        "generated_at": utc_now_iso(),
        "source_ndjsonl": str(LIVE_SOURCE),
        "source_ndjsonl_mtime": src_after_mtime,
        "n_rows": int(len(df)),
        "date_min_utc": date_min,
        "date_max_utc": date_max,
        "n_distinct_session_days": n_session_days,
        "feature_parity": parity,
        "known_gap_note": (
            "Live ndjsonl history starts ~2026-06-03. The previously-used training master "
            "(OFI_vol500_MASTER.csv) covered 2025-08-20..2026-02-17. The window 2026-02-17..2026-06-03 "
            "is NOT covered by any source found; sessions with 90-120 session-day lookback windows "
            "will train on fewer days than the original run."
        ),
        "dry_run": bool(args.dry_run),
    }

    report_path = out_dir / "provenance_report.json"
    with open(report_path, "w") as f:
        json.dump(provenance, f, indent=2, default=str)
    log(f"Provenance report: {report_path}")
    log(f"  rows={provenance['n_rows']} date_range=[{date_min} .. {date_max}] session_days={n_session_days}")

    if args.dry_run:
        log("DRY RUN: skipping CSV write (kept the copied ndjsonl for inspection).")
        return

    csv_path = out_dir / "master_v11_fresh.csv"
    df.to_csv(csv_path, index=False)
    log(f"Master CSV written: {csv_path} ({csv_path.stat().st_size / 1e6:.1f} MB)")
    log(f"Copied source ndjsonl retained at: {dest}")
    log("DONE.")


if __name__ == "__main__":
    main()
