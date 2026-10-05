#!/usr/bin/env python3
"""
inspect_current_session_performance.py

SHADOW / RESEARCH ONLY. Read-only inspection -- makes no predictions available anywhere else,
issues no orders, changes no live state.

Scores the freshly-trained v11 reversal candidate models against TODAY's live session bars,
pulled fresh from the live feature master (copied, never modified in place). Because the
candidate models were trained on data through a fixed cutoff, any bars after that cutoff are
genuine out-of-sample data -- this reports realized precision-at-threshold (identical
methodology to the training script's own validation printout) for whichever of today's named
sessions (London_Open / US_Overlap / Midday) have enough bars for the 3-bar-forward TBM outcome
to have resolved, plus the current live probability for the most recent (not-yet-resolved) bars.

Run:
    python3 inspect_current_session_performance.py
    python3 inspect_current_session_performance.py --candidate-dir /path/to/candidates/<ts>
"""

import sys
import json
import argparse
import importlib.util
import shutil
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

LIVE_SOURCE = Path("/home/prabh/OFI_Live_Features/master_NQ_continuous_backadjusted_shadow.ndjsonl")
TRAIN_SCRIPT_PATH = Path(
    "/mnt/wd_work/workspace/Work Place/Data/Project OFI/tools/Training/misc/train_v11_reversal_models_vol500.py"
)
EXPECTED_VOLUME_PROFILE = Path(
    "/mnt/wd_work/workspace/Work Place/Data/Project OFI/data/volume_profile.json"
)
CANDIDATES_ROOT = Path("/home/prabh/OFI_Production/v11_reversal/candidates")
TP_VARIANTS = [("1.5TP", 1.5, 1.0), ("2.5TP", 2.5, 1.5)]
SIDES = ["long", "short"]
PROB_THRESHOLDS = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80]


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_training_module():
    spec = importlib.util.spec_from_file_location("train_v11_reversal_models_vol500", TRAIN_SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def latest_candidate_dir() -> Path:
    dirs = sorted([d for d in CANDIDATES_ROOT.iterdir() if d.is_dir()], key=lambda d: d.name)
    if not dirs:
        raise SystemExit(f"No candidate dirs found under {CANDIDATES_ROOT}")
    return dirs[-1]


def load_today_bars() -> pd.DataFrame:
    """Fresh, read-only pull of today's live bars. Copies the ndjsonl before parsing -- never
    opens the live source for writing."""
    tmp_dir = Path("/home/prabh/OFI_Production/v11_reversal/_inspect_scratch")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_copy = tmp_dir / "live_snapshot.ndjsonl"
    log(f"Copying live source (read-only) for a fresh snapshot ...")
    shutil.copy2(LIVE_SOURCE, tmp_copy)

    rows = []
    with open(tmp_copy) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    df = pd.DataFrame(rows)
    tmp_copy.unlink()

    df["timestamp"] = pd.to_datetime(df["timestamp_utc"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    today = df["timestamp"].max().date()
    df_today = df[df["timestamp"].dt.date == today].reset_index(drop=True)
    log(f"  loaded {len(df)} total bars, {len(df_today)} bars for today ({today})")
    return df_today


def score_session_side_variant(mod, df_today, session_name, side, tp_mult, sl_mult, model_path, feature_names):
    session = mod.SESSIONS[session_name]
    mask = mod._session_mask(df_today["timestamp"], session)
    df_sess = df_today.loc[mask].copy()
    if df_sess.empty:
        return {"session": session_name, "side": side, "status": "NO_BARS_YET", "n_bars": 0}

    if "bar_index" in df_sess.columns or "bar_duration_s" in df_sess.columns:
        df_sess = mod._adapt_vol500_to_canonical(df_sess)
    df_sess["is_dead"] = mod._compute_dead_minutes(df_sess)
    df_sess = mod._ensure_time_features(df_sess)

    if EXPECTED_VOLUME_PROFILE.exists():
        curve = mod._load_expected_volume_curve(EXPECTED_VOLUME_PROFILE)
        df_sess = mod._add_expected_volume_features(df_sess, curve)
    else:
        df_sess["expected_vol_total"] = 0.0
        df_sess["rvol_expected"] = 0.0

    tbm = mod.TBMConfig(atr_window=30, horizon_bars=3, tp_mult=tp_mult, sl_mult=sl_mult,
                        time_breakeven_frac=0.30, time_win_frac=0.85)
    df_labeled = mod.compute_labels_session(df_sess, side=side, tbm=tbm, use_expected_vol=session.use_expected_vol)
    df_labeled = df_labeled[df_labeled.get("is_dead", 0) == 0].copy() if "is_dead" in df_labeled.columns else df_labeled

    missing = [f for f in feature_names if f not in df_labeled.columns]
    for f in missing:
        df_labeled[f] = 0.0

    if len(df_labeled) <= tbm.horizon_bars:
        return {"session": session_name, "side": side, "status": "TOO_FEW_BARS", "n_bars": len(df_labeled)}

    X = df_labeled[feature_names].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    model = joblib.load(model_path)
    probs = model.predict_proba(X)
    p_profit = probs[:, 1]

    # last `horizon_bars` rows have a label of 0 by construction (compute_labels_session never
    # labels the tail where the forward window doesn't exist) -- treat those as "pending" (no
    # realized outcome yet), everything else as "resolved" out-of-sample data.
    n = len(df_labeled)
    resolved_mask = np.arange(n) < (n - tbm.horizon_bars)

    result = {"session": session_name, "side": side, "status": "OK", "n_bars": n,
              "n_resolved": int(resolved_mask.sum()), "n_pending": int((~resolved_mask).sum())}

    y = df_labeled["label"].to_numpy()
    thresholds_out = []
    for th in PROB_THRESHOLDS:
        sel = resolved_mask & (p_profit >= th)
        n_sel = int(sel.sum())
        if n_sel == 0:
            continue
        precision = float((y[sel] == 1).mean())
        loss_rate = float((y[sel] == 2).mean())
        thresholds_out.append({"threshold": th, "n_trades": n_sel, "precision": precision, "loss_rate": loss_rate})
    result["thresholds"] = thresholds_out

    if (~resolved_mask).any():
        last_idx = n - 1
        result["latest_bar_timestamp"] = str(df_labeled.iloc[last_idx].get("timestamp"))
        result["latest_bar_p_profit"] = float(p_profit[last_idx])

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-dir", default=None, help="Defaults to the most recent candidate dir")
    args = parser.parse_args()

    candidate_dir = Path(args.candidate_dir) if args.candidate_dir else latest_candidate_dir()
    log(f"Using candidate: {candidate_dir}")

    mod = load_training_module()
    feature_names = list(mod.RAW_VOL500_FEATURES)
    df_today = load_today_bars()

    all_results = []
    for tp_name, tp_mult, sl_mult in TP_VARIANTS:
        for side in SIDES:
            subdir = "models_reversal" if side == "long" else "models_reversal_short"
            suffix = "_short" if side == "short" else ""
            for session_name in mod.SESSIONS.keys():
                if session_name == "Asia":
                    continue
                model_path = candidate_dir / tp_name / subdir / f"XGB_{session_name}{suffix}.pkl"
                if not model_path.exists():
                    continue
                res = score_session_side_variant(mod, df_today, session_name, side, tp_mult, sl_mult,
                                                  model_path, feature_names)
                res["tp_name"] = tp_name
                all_results.append(res)

    print("\n" + "=" * 100)
    print(f"V11 REVERSAL -- CURRENT SESSION ({df_today['timestamp'].max().date() if len(df_today) else '?'}) OUT-OF-SAMPLE INSPECTION")
    print("=" * 100)
    for r in all_results:
        header = f"\n{r['tp_name']} / {r['side']} / {r['session']}  --  status={r['status']}  n_bars={r.get('n_bars', 0)}"
        print(header)
        if r["status"] != "OK":
            continue
        print(f"  resolved={r['n_resolved']}  pending(no outcome yet)={r['n_pending']}")
        if r.get("thresholds"):
            print(f"  {'thresh':<8}{'n_trades':<10}{'precision':<12}{'loss_rate':<10}")
            for t in r["thresholds"]:
                prec_s = f"{t['precision']:.1%}"
                loss_s = f"{t['loss_rate']:.1%}"
                print(f"  {t['threshold']:<8}{t['n_trades']:<10}{prec_s:<12}{loss_s:<10}")
        else:
            print("  (no resolved trades cleared any probability threshold yet)")
        if "latest_bar_p_profit" in r:
            print(f"  LIVE (unresolved) latest bar {r['latest_bar_timestamp']}: p_profit={r['latest_bar_p_profit']:.3f}")

    out_path = candidate_dir / "current_session_inspection.json"
    with open(out_path, "w") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(), "candidate_dir": str(candidate_dir),
                   "results": all_results}, f, indent=2, default=str)
    log(f"Report written: {out_path}")


if __name__ == "__main__":
    main()
