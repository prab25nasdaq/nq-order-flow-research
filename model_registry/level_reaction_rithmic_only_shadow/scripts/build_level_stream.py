#!/usr/bin/env python3
"""build_level_stream.py — thin wrapper around pipeline.build_level_stream.

Per spec: scripts/build_level_stream.py.
Builds per-day POC/VAH/VAL/HVN/LVN context for all Rithmic-available days,
using the EXACT volume-profile formula from ofi_live_dashboard.py:2740-2767.

This script does NOT touch the live master, daemon, parser, or any model.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pipeline import (   # type: ignore
    precheck, build_level_stream, RITHMIC_FEAT_ROOT,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run_dir", type=Path, required=True,
                     help="Output run dir (timestamped)")
    args = ap.parse_args()
    args.run_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("audits","data","reports"):
        (args.run_dir / sub).mkdir(parents=True, exist_ok=True)
    pc, blocks = precheck(args.run_dir)
    if blocks:
        print(f"BLOCKED at precheck: {blocks}"); return 2
    dates = pc["checks"]["rithmic_features_root"]["dates_with_vol500"]
    build_level_stream(args.run_dir, dates)
    return 0


if __name__ == "__main__":
    sys.exit(main())
