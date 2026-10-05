#!/usr/bin/env python3
"""
promote_v11_candidate.py

SHADOW / RESEARCH ONLY.

Manual-only promotion of a v11 reversal training candidate into the live model directory. Never
invoked automatically by anything in this codebase (not the dashboard, not the scheduler) -- this
is a deliberate human action.

By default this only PRINTS the copy plan. Nothing is written unless --confirm is passed.

Run:
    python3 promote_v11_candidate.py --candidate-dir /path/to/candidates/<ts>          # dry preview
    python3 promote_v11_candidate.py --candidate-dir /path/to/candidates/<ts> --confirm
"""

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path

LIVE_MODEL_DIR = Path(
    "/mnt/wd_work/workspace/Work Place/Data/Project OFI/Models/DOM_vol500_v11/Reversal_XGB"
)
TP_VARIANTS = ["1.5TP", "2.5TP"]
SUBDIRS = ["models_reversal", "models_reversal_short"]


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-dir", required=True, help="A candidate dir produced by retrain_v11_reversal_shadow.py")
    parser.add_argument("--confirm", action="store_true", help="Actually perform the copy (default: preview only)")
    parser.add_argument("--backup-dir", default=None,
                         help="Where to back up the current live files before overwriting "
                              "(default: <LIVE_MODEL_DIR>_backup_<UTCts> next to the live dir)")
    args = parser.parse_args()

    candidate_dir = Path(args.candidate_dir)
    if not candidate_dir.exists():
        raise SystemExit(f"Candidate dir does not exist: {candidate_dir}")

    plan = []
    for tp in TP_VARIANTS:
        for sub in SUBDIRS:
            src = candidate_dir / tp / sub
            dst = LIVE_MODEL_DIR / tp / sub
            if not src.exists():
                log(f"  SKIP (no candidate output): {src}")
                continue
            n_files = len(list(src.glob("*")))
            plan.append((src, dst, n_files))

    if not plan:
        raise SystemExit("Nothing to promote -- no matching TP/side output found under the candidate dir.")

    log(f"Promotion plan ({'WILL EXECUTE' if args.confirm else 'PREVIEW ONLY -- pass --confirm to apply'}):")
    for src, dst, n_files in plan:
        log(f"  {src}  ({n_files} files)  ->  {dst}")

    if not args.confirm:
        log("No changes made. Re-run with --confirm to apply.")
        return

    run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    backup_dir = Path(args.backup_dir) if args.backup_dir else LIVE_MODEL_DIR.parent / f"Reversal_XGB_backup_{run_ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    log(f"Backing up current live model dir to: {backup_dir}")
    for tp in TP_VARIANTS:
        src_live = LIVE_MODEL_DIR / tp
        if src_live.exists():
            shutil.copytree(src_live, backup_dir / tp)

    for src, dst, n_files in plan:
        dst.mkdir(parents=True, exist_ok=True)
        for f in src.glob("*"):
            shutil.copy2(f, dst / f.name)
        log(f"  promoted {n_files} files -> {dst}")

    log(f"DONE. Previous live models backed up at: {backup_dir}")


if __name__ == "__main__":
    main()
