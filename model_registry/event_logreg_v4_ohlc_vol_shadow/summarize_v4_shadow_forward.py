#!/usr/bin/env python3
"""
Daily V3-active vs V4-shadow forward monitor summary.

Reads live_forward_shadow_log.csv and writes summary CSV/MD files in the same
shadow release folder. This script does not train, modify model artifacts, or
enable paper/production execution.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    f1_score,
    matthews_corrcoef,
    precision_recall_fscore_support,
    roc_auc_score,
)


BASE = Path("/home/prabh/OFI_Production/model_registry/event_logreg_v4_ohlc_vol_shadow")
LOG = BASE / "live_forward_shadow_log.csv"
THRESHOLDS = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_col(df: pd.DataFrame) -> pd.Series:
    ts = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    return ts.dt.date.astype(str)


def _binary(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["actual_label"].isin(["SHORT", "LONG"])].copy()


def _metrics(df: pd.DataFrame, prefix: str) -> Dict[str, Any]:
    pred_col = f"{prefix}_pred_side"
    prob_col = f"{prefix}_proba_long"
    drift_col = f"{prefix}_drift_state"
    out: Dict[str, Any] = {
        "model": prefix.upper(),
        "n_rows": int(len(df)),
        "n_binary": 0,
        "drift_block_pct": np.nan,
        "accuracy": np.nan,
        "balanced_accuracy": np.nan,
        "mcc": np.nan,
        "auc": np.nan,
    }
    if pred_col not in df.columns:
        return out
    out["drift_block_pct"] = round(
        100.0 * float((df[drift_col].astype(str) != "PASS").mean()), 2
    ) if drift_col in df.columns and len(df) else np.nan
    sub = _binary(df)
    sub = sub[sub[drift_col].astype(str) == "PASS"] if drift_col in sub.columns else sub
    sub = sub[sub[pred_col].isin(["SHORT", "LONG"])]
    out["n_binary"] = int(len(sub))
    if len(sub) == 0:
        return out
    y = (sub["actual_label"] == "LONG").astype(int).to_numpy()
    yp = (sub[pred_col] == "LONG").astype(int).to_numpy()
    p = pd.to_numeric(sub[prob_col], errors="coerce").to_numpy(float)
    out["accuracy"] = round(float(accuracy_score(y, yp)), 4)
    if len(np.unique(y)) > 1:
        out["balanced_accuracy"] = round(float(balanced_accuracy_score(y, yp)), 4)
        out["mcc"] = round(float(matthews_corrcoef(y, yp)), 4)
        try:
            out["auc"] = round(float(roc_auc_score(y, p)), 4)
        except Exception:
            pass
    return out


def _side_rows(df: pd.DataFrame, prefix: str) -> List[Dict[str, Any]]:
    pred_col = f"{prefix}_pred_side"
    drift_col = f"{prefix}_drift_state"
    sub = _binary(df)
    if drift_col in sub.columns:
        sub = sub[sub[drift_col].astype(str) == "PASS"]
    sub = sub[sub[pred_col].isin(["SHORT", "LONG"])]
    rows = []
    if len(sub):
        y = (sub["actual_label"] == "LONG").astype(int).to_numpy()
        yp = (sub[pred_col] == "LONG").astype(int).to_numpy()
        p, r, f, sup = precision_recall_fscore_support(
            y, yp, labels=[0, 1], zero_division=0
        )
    else:
        p = r = f = [np.nan, np.nan]
        sup = [0, 0]
        y = yp = np.array([])
    for i, side in enumerate(["SHORT", "LONG"]):
        rows.append({
            "model": prefix.upper(),
            "side": side,
            "precision": round(float(p[i]), 4) if np.isfinite(p[i]) else np.nan,
            "recall": round(float(r[i]), 4) if np.isfinite(r[i]) else np.nan,
            "f1": round(float(f[i]), 4) if np.isfinite(f[i]) else np.nan,
            "support": int(sup[i]),
            "pred_support": int((yp == i).sum()) if len(yp) else 0,
        })
    return rows


def _threshold_rows(df: pd.DataFrame, prefix: str) -> List[Dict[str, Any]]:
    pred_col = f"{prefix}_pred_side"
    conf_col = f"{prefix}_confidence"
    drift_col = f"{prefix}_drift_state"
    sub = _binary(df)
    if drift_col in sub.columns:
        sub = sub[sub[drift_col].astype(str) == "PASS"]
    sub = sub[sub[pred_col].isin(["SHORT", "LONG"])]
    rows = []
    denom = len(sub)
    for thr in THRESHOLDS:
        kept = sub[pd.to_numeric(sub[conf_col], errors="coerce") >= thr]
        if len(kept):
            y = (kept["actual_label"] == "LONG").astype(int).to_numpy()
            yp = (kept[pred_col] == "LONG").astype(int).to_numpy()
            acc = float(accuracy_score(y, yp))
            bacc = float(balanced_accuracy_score(y, yp)) if len(np.unique(y)) > 1 else np.nan
            mcc = float(matthews_corrcoef(y, yp)) if len(np.unique(y)) > 1 else np.nan
        else:
            acc = bacc = mcc = np.nan
        rows.append({
            "model": prefix.upper(),
            "threshold": thr,
            "n_kept": int(len(kept)),
            "coverage": round(float(len(kept) / denom), 4) if denom else np.nan,
            "accuracy": round(acc, 4) if np.isfinite(acc) else np.nan,
            "balanced_accuracy": round(bacc, 4) if np.isfinite(bacc) else np.nan,
            "mcc": round(mcc, 4) if np.isfinite(mcc) else np.nan,
        })
    return rows


def _md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    cols = list(df.columns)
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for _, row in df.iterrows():
        vals = []
        for c in cols:
            v = row[c]
            if pd.isna(v):
                vals.append("")
            elif isinstance(v, float):
                vals.append(f"{v:.4f}".rstrip("0").rstrip("."))
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", type=Path, default=LOG)
    ap.add_argument("--date", type=str, default=None,
                    help="UTC date YYYY-MM-DD. Default summarizes all rows.")
    ap.add_argument("--out-dir", type=Path, default=BASE)
    args = ap.parse_args()

    if not args.log.exists():
        print(f"[WARN] log not found: {args.log}")
        return 0
    df = pd.read_csv(args.log)
    if df.empty:
        print("[WARN] log is empty")
        return 0
    df["utc_date"] = _date_col(df)
    if args.date:
        df = df[df["utc_date"] == args.date].copy()
    if df.empty:
        print("[WARN] no rows for requested date")
        return 0

    metrics = pd.DataFrame([_metrics(df, "v3"), _metrics(df, "v4")])
    side = pd.DataFrame(_side_rows(df, "v3") + _side_rows(df, "v4"))
    conf = pd.DataFrame(_threshold_rows(df, "v3") + _threshold_rows(df, "v4"))

    bin_df = _binary(df)
    v3_ok = bin_df["v3_pred_side"].eq(bin_df["actual_label"])
    v4_ok = bin_df["v4_pred_side"].eq(bin_df["actual_label"])
    agreement = pd.DataFrame([{
        "n_binary": int(len(bin_df)),
        "agree_long": int((bin_df["agreement_state"] == "AGREE_LONG").sum()),
        "agree_short": int((bin_df["agreement_state"] == "AGREE_SHORT").sum()),
        "disagree": int((bin_df["agreement_state"] == "DISAGREE").sum()),
        "v3_only_pass": int((bin_df["agreement_state"] == "V3_ONLY_PASS").sum()),
        "v4_only_pass": int((bin_df["agreement_state"] == "V4_ONLY_PASS").sum()),
        "both_blocked": int((bin_df["agreement_state"] == "BOTH_BLOCKED").sum()),
        "v4_only_wins": int((~v3_ok & v4_ok).sum()),
        "v4_only_losses": int((v3_ok & ~v4_ok).sum()),
        "both_correct": int((v3_ok & v4_ok).sum()),
        "both_wrong": int((~v3_ok & ~v4_ok).sum()),
    }])

    stem = f"shadow_forward_{args.date}" if args.date else "shadow_forward_all"
    metrics.to_csv(args.out_dir / f"{stem}_daily_metrics.csv", index=False)
    side.to_csv(args.out_dir / f"{stem}_per_side.csv", index=False)
    conf.to_csv(args.out_dir / f"{stem}_confidence_thresholds.csv", index=False)
    agreement.to_csv(args.out_dir / f"{stem}_agreement.csv", index=False)

    md = [
        "# V4 Shadow Forward Daily Report",
        "",
        f"- generated_utc: `{_now()}`",
        f"- date_filter: `{args.date or 'ALL'}`",
        "- V4 OHLC-VOL = SHADOW ONLY",
        "- paper_trading_allowed=false",
        "- production_execution_allowed=false",
        "",
        "## Daily Metrics",
        _md_table(metrics),
        "",
        "## Agreement",
        _md_table(agreement),
        "",
        "## Per Side",
        _md_table(side),
        "",
        "## Confidence Thresholds",
        _md_table(conf),
    ]
    report = args.out_dir / f"{stem}_REPORT.md"
    report.write_text("\n".join(md))
    print(f"[OUT] {report}")
    print(metrics.to_string(index=False))
    print(agreement.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
