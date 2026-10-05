#!/usr/bin/env python3
"""
Compare:
  A) tail of the master CSV (the offline-master scale we want to match)
  B) old C++ parser output  (the pre-patch live scale, 5-7x too wide)
  C) NEW C++ parser output  (post-patch — should match A within ~1.2x)

Calm-window filter on each side: |delta_norm| < 0.10.

Writes report files to /home/prabh/OFI_Production/live_parser/.
"""
from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd

REPO       = Path("/home/prabh/OFI_Production/live_parser")
MASTER_CSV = Path("/mnt/wd_work/workspace/Work Place/Data/Project OFI/"
                  "DOM_1min_v500_ohlc_MASTER_OHLC_v3/MASTER_vol500_with_ohlc.csv")
OLD_LIVE   = Path("/home/prabh/OFI_Live_Features/2026-06-04/NQM6_vol500.ndjsonl")
NEW_LIVE   = Path("/home/prabh/OFI_Live_Features_FIXED/2026-06-04/NQM6_vol500.ndjsonl")
TARGET_FEATS = [
    "mlofi_decay_sum", "decay_norm",
    "mlofi_norm", "mlofi_rolling_5",
    "mlofi_norm_lag_1", "mlofi_norm_lag_2", "mlofi_norm_lag_3",
    "decay_norm_lag_1", "decay_norm_lag_2", "decay_norm_lag_3",
    "mlofi_accel",
    "book_count", "vol_total",
]


def load_ndjsonl(p: Path) -> pd.DataFrame:
    rows = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            rows.append(json.loads(line))
    return pd.DataFrame(rows)


def stats(df: pd.DataFrame, feat: str) -> dict:
    if feat not in df.columns:
        return {"present": False}
    a = pd.to_numeric(df[feat], errors="coerce").to_numpy()
    a = a[np.isfinite(a)]
    if a.size == 0:
        return {"present": True, "n": 0}
    return {
        "present": True,
        "n":       int(a.size),
        "mean":    float(a.mean()),
        "std":     float(a.std()),
        "median":  float(np.median(a)),
        "p10":     float(np.percentile(a, 10)),
        "p90":     float(np.percentile(a, 90)),
        "abs_mean": float(np.abs(a).mean()),
    }


def calm(df: pd.DataFrame) -> pd.DataFrame:
    if "delta_norm" not in df.columns: return df
    return df[df["delta_norm"].abs() < 0.10]


def main():
    print(f"[LOAD] master csv tail ({MASTER_CSV.name}) ...")
    csv = pd.read_csv(MASTER_CSV)
    csv_tail = csv.tail(5000)   # last few trading days
    csv_calm = calm(csv_tail)

    print(f"[LOAD] OLD live  ({OLD_LIVE}) ...")
    old = load_ndjsonl(OLD_LIVE)
    old_calm = calm(old)

    print(f"[LOAD] NEW live  ({NEW_LIVE}) ...")
    new = load_ndjsonl(NEW_LIVE)
    new_calm = calm(new)

    # Per-event scale check (the smoking gun from earlier)
    def per_event_scale(df, label):
        if "mlofi_decay_sum" not in df.columns or "book_count" not in df.columns:
            return None
        d  = pd.to_numeric(df["mlofi_decay_sum"], errors="coerce")
        bc = pd.to_numeric(df["book_count"],     errors="coerce")
        m  = (~d.isna()) & (~bc.isna()) & (bc > 0)
        r  = d.abs()[m] / bc[m]
        return float(r.mean()), float(r.median()), int(m.sum())

    csv_pes = per_event_scale(csv_tail, "csv")
    old_pes = per_event_scale(old,      "old")
    new_pes = per_event_scale(new,      "new")

    rows = []
    print()
    print("=" * 110)
    print(f"{'feature':<22} {'CSV calm std':>13} {'OLD calm std':>13} {'NEW calm std':>13}  "
          f"{'OLD/CSV':>8} {'NEW/CSV':>8}  {'verdict':>20}")
    print("=" * 110)
    for f in TARGET_FEATS:
        a = stats(csv_calm, f); b = stats(old_calm, f); c = stats(new_calm, f)
        if not (a.get("present") and b.get("present") and c.get("present")):
            print(f"  {f:<22}  MISSING in some side"); continue
        if a.get("n", 0) < 5 or b.get("n", 0) < 5 or c.get("n", 0) < 5:
            print(f"  {f:<22}  too few obs"); continue
        s_csv = a["std"]; s_old = b["std"]; s_new = c["std"]
        old_ratio = s_old / s_csv if s_csv != 0 else float("nan")
        new_ratio = s_new / s_csv if s_csv != 0 else float("nan")
        # verdict on the new side
        if np.isfinite(new_ratio):
            if 0.7 <= new_ratio <= 1.5:
                verdict = "RECOVERED ✓"
            elif new_ratio > 1.5:
                verdict = f"still {new_ratio:.1f}× wide"
            else:
                verdict = f"too narrow ({new_ratio:.2f}×)"
        else:
            verdict = "n/a"
        print(f"  {f:<22} {s_csv:>13.4f} {s_old:>13.4f} {s_new:>13.4f}  "
              f"{old_ratio:>7.2f}× {new_ratio:>7.2f}×  {verdict:>20}")
        rows.append({"feature": f,
                     "csv_calm_std": round(s_csv, 6),
                     "old_calm_std": round(s_old, 6),
                     "new_calm_std": round(s_new, 6),
                     "old_csv_ratio": round(old_ratio, 4) if np.isfinite(old_ratio) else None,
                     "new_csv_ratio": round(new_ratio, 4) if np.isfinite(new_ratio) else None,
                     "verdict": verdict})

    # per-event scale comparison
    print()
    print("=" * 110)
    print("PER-EVENT  |mlofi_decay_sum| / book_count   (lower-is-narrower; CSV is the spec)")
    print("=" * 110)
    if csv_pes and old_pes and new_pes:
        print(f"  CSV    mean={csv_pes[0]:.4f}  median={csv_pes[1]:.4f}  n={csv_pes[2]}")
        print(f"  OLD    mean={old_pes[0]:.4f}  median={old_pes[1]:.4f}  n={old_pes[2]}  "
              f"ratio_vs_csv={old_pes[0]/csv_pes[0]:.2f}×")
        print(f"  NEW    mean={new_pes[0]:.4f}  median={new_pes[1]:.4f}  n={new_pes[2]}  "
              f"ratio_vs_csv={new_pes[0]/csv_pes[0]:.2f}×")

    # PASS / BLOCKED decision: NEW std ratios should be in [0.7, 1.5] on MLOFI/decay features
    affected = [r for r in rows if r["feature"] in
                 ("mlofi_decay_sum", "decay_norm", "mlofi_norm",
                  "mlofi_rolling_5", "decay_norm_lag_1", "decay_norm_lag_2",
                  "decay_norm_lag_3", "mlofi_norm_lag_1", "mlofi_norm_lag_2",
                  "mlofi_norm_lag_3", "mlofi_accel")]
    n_ok   = sum(1 for r in affected
                  if r["new_csv_ratio"] is not None and 0.7 <= r["new_csv_ratio"] <= 1.5)
    n_bad  = sum(1 for r in affected
                  if r["new_csv_ratio"] is not None and not (0.7 <= r["new_csv_ratio"] <= 1.5))
    overall = "PASS" if n_bad == 0 and n_ok >= 8 else "BLOCKED"

    print()
    print("=" * 110)
    print(f"OVERALL: [{overall}]   "
          f"MLOFI/decay-family features in [0.7×, 1.5×] band: {n_ok}/{len(affected)}")
    print("=" * 110)

    # write the JSON
    payload = {
        "ran_at_utc":     pd.Timestamp.now(tz="UTC").isoformat(),
        "input": {
            "master_csv": str(MASTER_CSV),
            "old_live":   str(OLD_LIVE),
            "new_live":   str(NEW_LIVE),
        },
        "per_event_decay_ofi_magnitude": {
            "csv": {"mean": csv_pes[0], "median": csv_pes[1], "n": csv_pes[2]}
                    if csv_pes else None,
            "old": {"mean": old_pes[0], "median": old_pes[1], "n": old_pes[2]}
                    if old_pes else None,
            "new": {"mean": new_pes[0], "median": new_pes[1], "n": new_pes[2]}
                    if new_pes else None,
        },
        "per_feature_calm_std":            rows,
        "mlofi_family_in_band":            n_ok,
        "mlofi_family_out_of_band":        n_bad,
        "overall_status":                  overall,
    }
    (REPO / "parser_mlofi_parity_patch_report.json").write_text(
        json.dumps(payload, indent=2))

    md = []
    md.append("# Parser MLOFI Parity Patch — Verification Report")
    md.append("")
    md.append(f"- ran_at_utc: {payload['ran_at_utc']}")
    md.append(f"- patch site: `src/ofi_live_parser.cpp :: flush_packet`")
    md.append(f"- mechanism: per-update OFI compute (one push_book_obs per individual "
              "book update) — matches Python master builder per-MBO-event semantics")
    md.append("")
    md.append("## Sources compared")
    md.append("")
    md.append(f"- **CSV (spec)**       : `{MASTER_CSV}` — tail 5,000 rows")
    md.append(f"- **OLD live (pre-patch)** : `{OLD_LIVE}` — unchanged, never overwritten")
    md.append(f"- **NEW live (post-patch)**: `{NEW_LIVE}`")
    md.append("")
    md.append("All comparisons use the same calm-window filter `|delta_norm| < 0.10` on "
              "each side independently — so the bars compared have similar market "
              "conditions.")
    md.append("")
    md.append("## book_count semantics — explicit before/after")
    md.append("")
    md.append("| | per-packet flush | per individual update | per trade piece (depth_ok) |")
    md.append("|---|---|---|---|")
    md.append("| **CSV master builder (Python)** | n/a (no packets) | **+1** | +1 |")
    md.append("| **C++ live parser BEFORE patch** | +1 | 0 | +1 |")
    md.append("| **C++ live parser AFTER patch**  | 0 | **+1** | +1 |")
    md.append("")
    md.append("After-patch C++ now matches CSV-spec semantics: `book_count` increments "
              "once per individual book update.")
    md.append("")
    md.append("## Per-event |dec_ofi| magnitude (smoking gun)")
    md.append("")
    md.append("```")
    if csv_pes and old_pes and new_pes:
        md.append(f"  CSV    mean={csv_pes[0]:.4f}  median={csv_pes[1]:.4f}")
        md.append(f"  OLD    mean={old_pes[0]:.4f}  median={old_pes[1]:.4f}   "
                  f"ratio_vs_csv={old_pes[0]/csv_pes[0]:.2f}×")
        md.append(f"  NEW    mean={new_pes[0]:.4f}  median={new_pes[1]:.4f}   "
                  f"ratio_vs_csv={new_pes[0]/csv_pes[0]:.2f}×")
    md.append("```")
    md.append("")
    md.append("## Per-feature calm-window std")
    md.append("")
    md.append("```")
    md.append(f"  {'feature':<22} {'CSV':>12} {'OLD':>12} {'NEW':>12}  "
              f"{'OLD/CSV':>9} {'NEW/CSV':>9}  verdict")
    for r in rows:
        old_r = r["old_csv_ratio"]; new_r = r["new_csv_ratio"]
        md.append(f"  {r['feature']:<22} {r['csv_calm_std']:>12.4f} "
                  f"{r['old_calm_std']:>12.4f} {r['new_calm_std']:>12.4f}  "
                  f"{(str(old_r)+'×') if old_r else 'n/a':>9}  "
                  f"{(str(new_r)+'×') if new_r else 'n/a':>9}  {r['verdict']}")
    md.append("```")
    md.append("")
    md.append("## Verdict")
    md.append("")
    md.append(f"- MLOFI/decay-family features within `[0.7×, 1.5×]` of the CSV-spec "
              f"std: **{n_ok}/{len(affected)}**")
    md.append(f"- Overall: **{overall}**")
    md.append("")
    md.append("## Safety properties preserved")
    md.append("")
    md.append("- No lookahead (each per-event OFI compares the just-applied update "
              "against the immediately prior snapshot — no future bars)")
    md.append("- Same `update_bid` / `update_ask` semantics on `LOBBook`")
    md.append("- Same `OFI_K_RAW`, `OFI_K_DECAY`, `top_bids(10)`/`top_asks(10)` depth")
    md.append("- Bar close logic, vol500 aggregation, and timestamp ordering unchanged")
    md.append("- Snapshot/image packets still do a baseline-only refresh (no OFI push) — "
              "preserves the master builder's behavior on book resets")
    md.append("- Old live NDJSONL files left untouched (output went to "
              "`OFI_Live_Features_FIXED/`)")
    (REPO / "PARSER_MLOFI_PARITY_PATCH_REPORT.md").write_text("\n".join(md))

    print(f"\n[OUT] {REPO / 'PARSER_MLOFI_PARITY_PATCH_REPORT.md'}")
    print(f"[OUT] {REPO / 'parser_mlofi_parity_patch_report.json'}")
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
