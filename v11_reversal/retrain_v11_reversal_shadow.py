#!/usr/bin/env python3
"""
retrain_v11_reversal_shadow.py

SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING.

Retrains the v11 reversal XGBoost models (long/short x 1.5TP/2.5TP, 3 sessions each) on a fresh
master built from the live feature master. Writes ONLY to a new timestamped candidate directory --
never touches the currently-live model artifacts under
"/mnt/wd_work/workspace/Work Place/Data/Project OFI/Models/DOM_vol500_v11/Reversal_XGB/". Promotion
into that live path is a separate, explicit, human-run step (see promote_v11_candidate.py).

This is the single entry point used by both the dashboard's "Retrain V11 Models" button (manual)
and the (currently disabled) CME-break scheduler hook (--auto) -- one script, one status file, so
the dashboard shows the same thing regardless of who triggered the run.

Run:
    python3 retrain_v11_reversal_shadow.py --dry-run
    python3 retrain_v11_reversal_shadow.py
    python3 retrain_v11_reversal_shadow.py --reuse-master /path/to/master_v11_fresh.csv
    python3 retrain_v11_reversal_shadow.py --auto   (used by the scheduler hook)
"""

import os
import sys
import json
import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD_MASTER_SCRIPT = HERE / "build_v11_master_from_live.py"
TRAIN_SCRIPT = Path(
    "/mnt/wd_work/workspace/Work Place/Data/Project OFI/tools/Training/misc/train_v11_reversal_models_vol500.py"
)
EXPECTED_VOLUME_PROFILE = Path(
    "/mnt/wd_work/workspace/Work Place/Data/Project OFI/data/volume_profile.json"
)
LIVE_MODEL_DIR = Path(
    "/mnt/wd_work/workspace/Work Place/Data/Project OFI/Models/DOM_vol500_v11/Reversal_XGB"
)

CANDIDATES_ROOT = HERE / "candidates"
STATUS_PATH = HERE / "v11_retrain_status.json"

SESSIONS = "London_Open,US_Overlap,Midday"
TP_VARIANTS = [("1.5TP", 1.5, 1.0), ("2.5TP", 2.5, 1.5)]
SIDES = ["long", "short"]
TRAIN_TIMEOUT_S = 28800  # 8h per (side, tp_variant) run covering all 3 sessions -- empirically,
                          # the smallest session (London_Open, ~2k train rows) alone took ~55min
                          # at n_estimators=5000; the 1-hour timeout previously here silently
                          # killed runs mid-way through the larger sessions (US_Overlap/Midday).


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_status(d: dict):
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2, default=str)
    os.replace(tmp, STATUS_PATH)


def read_status() -> dict:
    if STATUS_PATH.exists():
        try:
            with open(STATUS_PATH) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def build_fresh_master(candidate_dir: Path, dry_run: bool) -> Path:
    out_dir = candidate_dir / "master"
    cmd = [sys.executable, str(BUILD_MASTER_SCRIPT), "--out-dir", str(out_dir)]
    if dry_run:
        cmd.append("--dry-run")
    log(f"Building fresh master: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(f"build_v11_master_from_live.py failed (exit {result.returncode})")
    return out_dir / "master_v11_fresh.csv"


def run_one_training(side: str, tp_mult: float, sl_mult: float, master_csv: Path,
                      tp_dir: Path, log_dir: Path, dry_run: bool) -> dict:
    cmd = [
        sys.executable, str(TRAIN_SCRIPT),
        "--side", side,
        "--input", str(master_csv),
        "--out_dir", str(tp_dir),
        "--sessions", SESSIONS,
        "--feature_mode", "vol500",
        "--tp_mult", str(tp_mult),
        "--sl_mult", str(sl_mult),
        "--expected_volume_profile", str(EXPECTED_VOLUME_PROFILE),
    ]
    run_info = {"side": side, "tp_mult": tp_mult, "sl_mult": sl_mult, "cmd": cmd, "status": "PENDING"}
    if dry_run:
        log(f"  [dry-run] would run: {' '.join(cmd)}")
        run_info["status"] = "DRY_RUN"
        return run_info

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{side}_train.log"
    log(f"  training side={side} tp_mult={tp_mult} sl_mult={sl_mult} -> {tp_dir} (log: {log_path})")
    with open(log_path, "w") as logf:
        proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)
        try:
            ret = proc.wait(timeout=TRAIN_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            run_info["status"] = "TIMEOUT"
            run_info["log_path"] = str(log_path)
            return run_info

    run_info["status"] = "OK" if ret == 0 else "ERROR"
    run_info["returncode"] = ret
    run_info["log_path"] = str(log_path)

    subdir = "models_reversal" if side == "long" else "models_reversal_short"
    meta_dir = tp_dir / subdir
    metas = sorted(str(p) for p in meta_dir.glob("*_meta.json")) if meta_dir.exists() else []
    run_info["meta_paths"] = metas
    return run_info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print planned commands, run nothing")
    parser.add_argument("--auto", action="store_true", help="Set by the CME-break scheduler hook")
    parser.add_argument("--reuse-master", default=None, help="Path to an existing master_v11_fresh.csv (skip rebuild)")
    args = parser.parse_args()

    prev = read_status()
    if prev.get("retrain_running"):
        raise SystemExit("A v11 retrain is already marked running in v11_retrain_status.json -- refusing to start a second one.")

    trigger_mode = "auto" if args.auto else "manual"
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    candidate_dir = CANDIDATES_ROOT / run_ts

    status = {
        "retrain_running": True,
        "retrain_start_time_utc": utc_now_iso(),
        "retrain_end_time_utc": None,
        "trigger_mode": trigger_mode,
        "dry_run": bool(args.dry_run),
        "candidate_dir": str(candidate_dir),
        "master_csv_path": None,
        "runs": [],
        "validation_status": "RUNNING",
        "error": None,
    }
    write_status(status)
    log(f"v11 retrain starting (trigger_mode={trigger_mode}, dry_run={args.dry_run}) -> {candidate_dir}")

    try:
        if args.reuse_master:
            master_csv = Path(args.reuse_master)
            if not master_csv.exists():
                raise FileNotFoundError(f"--reuse-master path does not exist: {master_csv}")
            log(f"Reusing existing master: {master_csv}")
        else:
            master_csv = build_fresh_master(candidate_dir, args.dry_run)

        status["master_csv_path"] = str(master_csv)

        if not args.dry_run and not master_csv.exists():
            raise RuntimeError(f"Expected master CSV not found after build: {master_csv}")

        runs = []
        for tp_name, tp_mult, sl_mult in TP_VARIANTS:
            tp_dir = candidate_dir / tp_name
            log_dir = candidate_dir / "logs" / tp_name
            for side in SIDES:
                run_info = run_one_training(side, tp_mult, sl_mult, master_csv, tp_dir, log_dir, args.dry_run)
                run_info["tp_name"] = tp_name
                runs.append(run_info)
                status["runs"] = runs
                write_status(status)  # incremental progress visible to the dashboard while running

        n_error = sum(1 for r in runs if r["status"] in ("ERROR", "TIMEOUT"))
        status["validation_status"] = "DRY_RUN" if args.dry_run else ("PASS" if n_error == 0 else "PARTIAL_FAILURE")

    except Exception as e:
        status["validation_status"] = "ERROR"
        status["error"] = str(e)
        log(f"ERROR: {e}")
    finally:
        status["retrain_running"] = False
        status["retrain_end_time_utc"] = utc_now_iso()
        write_status(status)

    log(f"v11 retrain finished: validation_status={status['validation_status']}")
    log(f"NOTE: live model dir NOT touched: {LIVE_MODEL_DIR}. Use promote_v11_candidate.py to promote manually.")


if __name__ == "__main__":
    main()
