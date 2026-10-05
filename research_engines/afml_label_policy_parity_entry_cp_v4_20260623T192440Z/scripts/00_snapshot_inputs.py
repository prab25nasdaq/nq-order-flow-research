"""
00_snapshot_inputs.py - freeze a READ-ONLY snapshot of live production inputs
for the AFML Label-Policy-Parity Entry + CP v4 engine.

master_NQ_continuous_backadjusted_shadow.ndjsonl, master_NQU6_shadow.ndjsonl,
the book-flow cache, and predictions.csv are LIVE files continuously
appended by the production scheduler. This script ONLY READS from the live
production paths (and the active model release, also read-only) and ONLY
WRITES inside this engine's own raw_snapshot/.

The active model release's policy/event/label artifacts
(EVENT_POLICY.md, LABEL_POLICY.md, FEATURE_POLICY.md, training_config.json,
reaction_type_metadata.json, leakage_audit.md, release_manifest.json,
pipeline_continuous.py, data/level_reaction_events.parquet,
data/labels_level_reaction.parquet, data/X_level_reaction_events.parquet,
data/folds.json) were already copied into raw_snapshot/active_release_policy/
in a prior step of this build - this script does not touch the model
registry again.

Dashboard / OFI Level Decision / model-probs-trust source files are read
directly from their live paths for formula extraction ONLY (never executed,
never imported, never modified) - not snapshotted here, consistent with v3.

SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import glob
import json
from datetime import datetime, timezone
from pathlib import Path
import shutil

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

    n_cache = 0
    for depth in [5, 10, 15, 20]:
        pattern = str(BOOK_FLOW_CACHE_DIR / f"book_flow_level_candles_NQU6_*_top{depth}.parquet")
        for fpath in sorted(glob.glob(pattern)):
            dst = SNAPSHOT_DIR / "cache" / Path(fpath).name
            shutil.copy2(fpath, dst)
            n_cache += 1
    print(f"copied {n_cache} book-flow level-candle parquet files")

    active_release_files = sorted(
        str(p) for p in (SNAPSHOT_DIR / "active_release_policy").glob("*")
    ) if (SNAPSHOT_DIR / "active_release_policy").exists() else []

    manifest = {
        "snapshot_taken_at_utc": datetime.now(timezone.utc).isoformat(),
        "files_copied": copied,
        "book_flow_cache_files": n_cache,
        "active_release_policy_files_pre_copied": active_release_files,
        "active_shadow_release_resolved_path": str(
            Path("/home/prabh/OFI_Production/model_registry/level_reaction_continuous_nq_shadow/"
                 "ACTIVE_SHADOW_RELEASE").resolve()
        ),
        "dashboard_source_files_read_only_not_snapshotted": [
            "/mnt/wd_work/workspace/Work Place/Data/Project OFI/_codex_work/ofi_live_dashboard_WORKING_NEXT_with_logreg.py",
            "/home/prabh/OFI_Production/ofi_level_decision_tab.py",
            "/mnt/wd_work/workspace/Work Place/Data/Project OFI/_codex_work/level_reaction_dashboard_tab.py",
            "/home/prabh/OFI_Production/model_probs_trust_tab.py",
        ],
        "note": "READ-ONLY snapshot. Source production files (masters, book-flow "
                "cache, predictions.csv, model registry policy/data artifacts) "
                "were opened for reading only; never modified. predictions.csv "
                "is snapshotted for SCHEMA/CONTEXT reference only - it is never "
                "used as ground truth for labels (see Part B). All downstream "
                "scripts in this engine read exclusively from this raw_snapshot/ "
                "directory for DATA.",
    }
    with open(SNAPSHOT_DIR / "SNAPSHOT_MANIFEST.json", "w") as f:
        json.dump(manifest, f, indent=2)
    print("\nSNAPSHOT_MANIFEST.json written. Snapshot complete.")


if __name__ == "__main__":
    main()
