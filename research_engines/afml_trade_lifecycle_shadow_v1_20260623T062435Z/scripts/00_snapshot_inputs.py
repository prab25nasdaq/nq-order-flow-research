"""
00_snapshot_inputs.py - freeze a READ-ONLY snapshot of all live production
inputs into raw_snapshot/ before any analysis runs.

WHY: master_NQ_continuous_backadjusted_shadow.ndjsonl, master_NQU6_shadow.ndjsonl,
the book-flow cache, and predictions.csv are LIVE files continuously appended
by the production scheduler. Reading them directly mid-build would make
different scripts see different row counts. This script ONLY READS from the
live production paths and ONLY WRITES inside this engine's raw_snapshot/ dir.
It never opens any production path for writing.

SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import glob
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = ENGINE_DIR / "raw_snapshot"
SNAPSHOT_DIR.mkdir(exist_ok=True)
(SNAPSHOT_DIR / "cache").mkdir(exist_ok=True)

LIVE_FEATURES_DIR = Path("/home/prabh/OFI_Live_Features")
BOOK_FLOW_CACHE_DIR = Path("/home/prabh/OFI_Production/book_flow_chart/cache")
PRED_DIR = Path(
    "/home/prabh/OFI_Production/inference_scripts/level_reaction_continuous_nq_shadow/outputs"
)

FILES_TO_COPY = [
    (LIVE_FEATURES_DIR / "master_NQ_continuous_backadjusted_shadow.ndjsonl", SNAPSHOT_DIR),
    (LIVE_FEATURES_DIR / "master_NQU6_shadow.ndjsonl", SNAPSHOT_DIR),
    (LIVE_FEATURES_DIR / "projected_levels_NQM6_to_NQU6.csv", SNAPSHOT_DIR),
    (PRED_DIR / "latest_continuous_nq_predictions.csv", SNAPSHOT_DIR),
    (PRED_DIR / "latest_continuous_nq_prediction_summary.csv", SNAPSHOT_DIR),
    (PRED_DIR / "latest_continuous_nq_inference_summary.json", SNAPSHOT_DIR),
]


def main():
    copied = []
    for src, dst_dir in FILES_TO_COPY:
        if not src.exists():
            print(f"WARNING: missing {src}, skipping")
            continue
        dst = dst_dir / src.name
        shutil.copy2(src, dst)
        copied.append(str(dst))
        print(f"copied {src} -> {dst} ({dst.stat().st_size} bytes)")

    # book-flow level-candle cache: all NQU6 dates, all depths (top5/10/15/20)
    n_cache = 0
    for depth in [5, 10, 15, 20]:
        pattern = str(BOOK_FLOW_CACHE_DIR / f"book_flow_level_candles_NQU6_*_top{depth}.parquet")
        for fpath in sorted(glob.glob(pattern)):
            dst = SNAPSHOT_DIR / "cache" / Path(fpath).name
            shutil.copy2(fpath, dst)
            n_cache += 1
    print(f"copied {n_cache} book-flow level-candle parquet files")

    heartbeat_src = BOOK_FLOW_CACHE_DIR / "book_flow_cache_heartbeat.json"
    if heartbeat_src.exists():
        shutil.copy2(heartbeat_src, SNAPSHOT_DIR / "cache" / heartbeat_src.name)

    manifest = {
        "snapshot_taken_at_utc": datetime.now(timezone.utc).isoformat(),
        "files_copied": copied,
        "book_flow_cache_files": n_cache,
        "note": "READ-ONLY snapshot. Source production files were opened for "
                "reading only; never modified. All downstream scripts in this "
                "engine read exclusively from this raw_snapshot/ directory.",
    }
    with open(SNAPSHOT_DIR / "SNAPSHOT_MANIFEST.json", "w") as f:
        json.dump(manifest, f, indent=2)
    print("\nSNAPSHOT_MANIFEST.json written. Snapshot complete.")


if __name__ == "__main__":
    main()
