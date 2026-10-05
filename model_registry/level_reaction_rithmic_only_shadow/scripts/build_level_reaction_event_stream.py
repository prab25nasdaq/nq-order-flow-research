#!/usr/bin/env python3
"""build_level_reaction_event_stream.py — wrapper around pipeline.build_event_stream.

Per spec: scripts/build_level_reaction_event_stream.py.

Generates events ONLY when a valid level reaction occurs at POC / VAH / VAL /
HVN / LVN. Causal definitions only (current OHLC + prior_close + past touch
count). No future data, no shift(-1), no centered rolling, no bfill.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pipeline import (   # type: ignore
    precheck, build_level_stream, build_event_stream,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run_dir", type=Path, required=True)
    args = ap.parse_args()
    args.run_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("audits","data","reports"):
        (args.run_dir / sub).mkdir(parents=True, exist_ok=True)
    pc, blocks = precheck(args.run_dir)
    if blocks:
        print(f"BLOCKED at precheck: {blocks}"); return 2
    dates = pc["checks"]["rithmic_features_root"]["dates_with_vol500"]
    _, per_day_levels = build_level_stream(args.run_dir, dates)
    build_event_stream(args.run_dir, dates, per_day_levels)
    return 0


if __name__ == "__main__":
    sys.exit(main())
