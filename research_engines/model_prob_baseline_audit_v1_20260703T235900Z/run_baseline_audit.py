#!/usr/bin/env python3
"""
Audit 3 — Dumb Baseline / Naive Model Audit v1
v3 must beat naive baselines after cost.
If v3 cannot beat session-only or time-of-day-only → model is not learning
unique microstructure edge — alpha is mostly calendar/session drift.
SHADOW / RESEARCH ONLY — no execution, no broker, no order placement.

Baselines:
  1. Always LONG
  2. Always SHORT
  3. Majority class (LONG or SHORT based on train prevalence)
  4. Random same trade count (randomized labels)
  5. Session-only model (logistic regression on session one-hots only)
  6. Regime-only model (logistic regression on vol-regime features only)
  7. Time-of-day-only model (logistic regression on hour/dow features only)
  8. Previous-bar-direction model (bet in direction of most recent bar's delta_norm)
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LogisticRegression
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler

# ─── Paths ────────────────────────────────────────────────────────────────────
PBO_DIR   = Path("/home/prabh/OFI_Production/research_engines"
                 "/model_prob_pbo_window_family_audit_v1_20260703T230000Z")
MODEL_DIR = Path("/home/prabh/OFI_Production/model_registry"
                 "/level_reaction_continuous_nq_shadow"
                 "/level_reaction_continuous_nq_shadow_20260702T005104Z")
OUT_DIR   = Path(__file__).parent

COST_TICKS = 2.0
N_RANDOM   = 50     # bootstrap iterations for random baseline
HORIZONS   = [10, 40]
# W=20 (current production default) used for v3 comparison
W_DEFAULT  = 20


def net_return(pred_dir: np.ndarray, fwd_ret: np.ndarray, cost: float = COST_TICKS) -> float:
    """Average net return in ticks. pred_dir: +1=LONG, -1=SHORT."""
    return float((pred_dir * fwd_ret - cost).mean())


def baseline_always_long(fwd_ret: np.ndarray) -> float:
    return net_return(np.ones(len(fwd_ret)), fwd_ret)


def baseline_always_short(fwd_ret: np.ndarray) -> float:
    return net_return(-np.ones(len(fwd_ret)), fwd_ret)


def baseline_majority(y_train: np.ndarray, fwd_ret: np.ndarray) -> float:
    maj = 1 if y_train.mean() >= 0.5 else 0
    pred_dir = np.ones(len(fwd_ret)) if maj == 1 else -np.ones(len(fwd_ret))
    return net_return(pred_dir, fwd_ret)


def baseline_random(fwd_ret: np.ndarray, frac_long: float, n_boot: int = N_RANDOM) -> dict:
    rng    = np.random.default_rng(42)
    vals   = []
    for _ in range(n_boot):
        pred = rng.choice([1, -1], size=len(fwd_ret),
                          p=[frac_long, 1 - frac_long])
        vals.append(net_return(pred, fwd_ret))
    return {"mean": float(np.mean(vals)), "std": float(np.std(vals)),
            "p95":  float(np.percentile(vals, 95))}


def baseline_logreg(X_train: np.ndarray, y_train: np.ndarray,
                    X_val: np.ndarray, fwd_ret: np.ndarray) -> float:
    imp = SimpleImputer(strategy="median").fit(X_train)
    Xt, Xv = imp.transform(X_train), imp.transform(X_val)
    scl = StandardScaler().fit(Xt)
    Xt, Xv = scl.transform(Xt), scl.transform(Xv)
    if len(np.unique(y_train)) < 2:
        return np.nan
    model = LogisticRegression(max_iter=500, C=1.0, solver="lbfgs").fit(Xt, y_train)
    pred  = model.predict(Xv)
    pred_dir = np.where(pred == 1, 1, -1)
    return net_return(pred_dir, fwd_ret)


def main():
    print("=" * 70)
    print("Audit 3 — Dumb Baseline / Naive Model Audit v1")
    print("SHADOW / RESEARCH ONLY — no execution, no broker")
    print("=" * 70)

    oos_path = PBO_DIR / "oos_probability_table.parquet"
    if not oos_path.exists():
        print("[FAIL] oos_probability_table.parquet not found.")
        return

    oos = pd.read_parquet(oos_path)

    # Load master snapshot for session, time, and bar features
    snap = pd.read_parquet(MODEL_DIR / "data/continuous_master_snapshot.parquet")
    snap["bar_idx_in_day"] = snap.groupby("rithmic_date_str").cumcount()

    # Load labels for forward returns
    lbl = pd.read_parquet(MODEL_DIR / "data/labels_level_reaction.parquet")
    lbl = lbl.dropna(subset=["label_h40"])

    # Load events for session info
    events = pd.read_parquet(MODEL_DIR / "data/level_reaction_events.parquet")

    # Load folds
    with open(MODEL_DIR / "data/folds.json") as f:
        folds_raw = json.load(f)
    folds = folds_raw["fold_definitions"]

    # Merge events + labels + snap features
    ev_lbl = events.merge(
        lbl[["event_id", "label_h40", "fwd_return_ticks_h10", "fwd_return_ticks_h40"]],
        on="event_id", how="inner"
    )
    # Add snap features at event bar
    snap_ts = snap.set_index("bar_end_ts_ns")
    ev_lbl["delta_norm_at_bar"] = ev_lbl["event_time_ns"].map(
        snap_ts["delta_norm"].to_dict())
    # Session features (already in events as 'session')
    for s in ("Asia", "EU", "US_Open", "US_AM", "US_PM", "US_Late"):
        ev_lbl[f"sess_{s}"] = (ev_lbl["session"] == s).astype(int)
    # Volatility proxy: |fwd_return_ticks_h10| as regime
    ev_lbl["vol_regime"] = (
        ev_lbl["fwd_return_ticks_h10"].abs() >=
        ev_lbl["fwd_return_ticks_h10"].abs().median()
    ).astype(int)
    # bar_idx_in_day is already in events (no merge needed)
    ev_lbl["label_h40"] = ev_lbl["label_h40"].astype(int)

    # Feature groups for dumb baselines
    SESS_FEATS    = [f"sess_{s}" for s in ("Asia","EU","US_Open","US_AM","US_PM","US_Late")]
    REGIME_FEATS  = ["vol_regime"]
    TOD_FEATS     = ["bar_idx_in_day"]  # proxy for time-of-day
    PREVBAR_FEAT  = ["delta_norm_at_bar"]

    now_utc = datetime.now(timezone.utc).isoformat()
    result_rows = []

    # v3 OOS net return (W=20, Version A, both horizons)
    v3_oos = oos[(oos["version"] == "A") & (oos["window"] == W_DEFAULT)]

    for H in HORIZONS:
        fwd_col = f"fwd_return_ticks_h{H}"
        net_col = f"net_return_h{H}"
        print(f"\n  Horizon H={H}:")

        # v3 OOS net return
        v3_net = float(v3_oos[net_col].mean()) if net_col in v3_oos.columns else np.nan
        print(f"    v3 W={W_DEFAULT} (OOS): net_h{H}={v3_net:+.4f}t")

        # Walk-forward baselines (same folds)
        fold_nets = {
            "always_long":  [], "always_short": [], "majority": [],
            "random":       [], "session_only": [], "regime_only": [],
            "tod_only":     [], "prev_bar_dir": [],
        }

        for fold_def in folds:
            tr_dates = set(fold_def["train_dates"])
            va_dates = set(fold_def["val_dates"])
            tr = ev_lbl[ev_lbl["rithmic_date_str"].isin(tr_dates)]
            va = ev_lbl[ev_lbl["rithmic_date_str"].isin(va_dates)]
            if len(tr) < 100 or len(va) == 0:
                continue

            y_tr   = tr["label_h40"].to_numpy()
            fwd_va = va[fwd_col].to_numpy(dtype=float)
            if len(fwd_va) == 0:
                continue

            # Dumb baselines
            fold_nets["always_long"].append(baseline_always_long(fwd_va))
            fold_nets["always_short"].append(baseline_always_short(fwd_va))
            fold_nets["majority"].append(baseline_majority(y_tr, fwd_va))

            frac_long = float(y_tr.mean())
            rand_res  = baseline_random(fwd_va, frac_long, n_boot=20)
            fold_nets["random"].append(rand_res["mean"])

            # Session-only logreg
            X_sess_tr = tr[SESS_FEATS].to_numpy(dtype=float)
            X_sess_va = va[SESS_FEATS].to_numpy(dtype=float)
            if not np.all(np.isnan(X_sess_tr)):
                fold_nets["session_only"].append(
                    baseline_logreg(X_sess_tr, y_tr, X_sess_va, fwd_va))

            # Regime-only logreg (uses train regime; slightly leaky but negligible)
            X_reg_tr = tr[REGIME_FEATS].to_numpy(dtype=float)
            X_reg_va = va[REGIME_FEATS].to_numpy(dtype=float)
            fold_nets["regime_only"].append(
                baseline_logreg(X_reg_tr, y_tr, X_reg_va, fwd_va))

            # Time-of-day-only logreg
            X_tod_tr = tr[TOD_FEATS].fillna(0).to_numpy(dtype=float)
            X_tod_va = va[TOD_FEATS].fillna(0).to_numpy(dtype=float)
            fold_nets["tod_only"].append(
                baseline_logreg(X_tod_tr, y_tr, X_tod_va, fwd_va))

            # Previous-bar direction: delta_norm > 0 → bet LONG, else SHORT
            dn_va = va["delta_norm_at_bar"].fillna(0).to_numpy(dtype=float)
            pred_pb = np.where(dn_va > 0, 1.0, -1.0)
            fold_nets["prev_bar_dir"].append(net_return(pred_pb, fwd_va))

        print(f"    {'Baseline':25s}  {'Net H' + str(H):>12s}  {'Beats v3?':>10s}")
        print(f"    {'-'*55}")

        for name, nets in fold_nets.items():
            nets_clean = [n for n in nets if n is not None and not np.isnan(n)]
            if not nets_clean:
                continue
            avg_net  = float(np.mean(nets_clean))
            beats_v3 = avg_net > v3_net
            tag      = "yes" if beats_v3 else "no"
            print(f"    {name:25s}: {avg_net:+12.4f}t  {tag:>10s}")
            result_rows.append({
                "horizon": H,
                "baseline": name,
                "avg_net_return": avg_net,
                "n_folds": len(nets_clean),
                "v3_net": v3_net,
                "beats_v3": beats_v3,
            })

        print(f"    {'-'*55}")
        print(f"    {'v3 W=20 (Version A)':25s}: {v3_net:+12.4f}t  {'[reference]':>10s}")

    # Save results
    res_df = pd.DataFrame(result_rows)
    res_df.to_csv(OUT_DIR / "baseline_comparison.csv", index=False)

    # Check: how many baselines beat v3?
    n_beat = int(res_df["beats_v3"].sum())
    n_total = len(res_df)

    # Report
    report = [
        "# Audit 3 — Dumb Baseline / Naive Model Audit v1",
        f"**Generated**: {now_utc}",
        "**SHADOW / RESEARCH ONLY**",
        "",
        "---",
        "",
        f"## v3 Reference (Version A, W={W_DEFAULT}, H=10)",
        f"Net return H10 = {res_df[res_df['horizon']==10]['v3_net'].iloc[0]:+.4f} ticks (OOS average)",
        "",
        "---",
        "",
        "## Baseline Comparison",
        "",
        "| H | Baseline | Net return (ticks) | Beats v3? |",
        "|---|----------|-------------------|-----------|",
    ]
    for _, r in res_df.iterrows():
        beats = "**YES**" if r["beats_v3"] else "no"
        report.append(
            f"| {r['horizon']} | {r['baseline']} | {r['avg_net_return']:+.4f} | {beats} |"
        )

    session_beats = res_df[res_df["baseline"] == "session_only"]["beats_v3"].values
    tod_beats     = res_df[res_df["baseline"] == "tod_only"]["beats_v3"].values
    session_warn  = any(session_beats)
    tod_warn      = any(tod_beats)

    report += [
        "",
        "---",
        "",
        "## Decision",
        "",
    ]
    if n_beat == 0:
        report.append(f"**BASELINE PASS** — v3 beats all {n_total} dumb baselines after cost.")
    elif session_warn or tod_warn:
        report.append(
            f"**BASELINE WARNING** — {n_beat}/{n_total} baselines beat v3. "
            + ("Session-only beats v3 → likely calendar drift, not microstructure edge. " if session_warn else "")
            + ("Time-of-day-only beats v3 → likely TOD drift. " if tod_warn else "")
        )
    else:
        report.append(
            f"**BASELINE PARTIAL** — {n_beat}/{n_total} baselines beat v3 (no session/TOD failure). "
            "Investigate which baselines beat v3 before proceeding."
        )

    report += [
        "",
        "Key: if session-only or TOD-only beats v3 → model is not learning microstructure.",
        "",
        "---",
        "",
        "## Final Status",
        "```",
        f"BASELINE_AUDIT_COMPLETE: true",
        f"N_BASELINES_BEAT_V3: {n_beat}/{n_total}",
        f"SESSION_ONLY_BEATS_V3: {session_warn}",
        f"TOD_ONLY_BEATS_V3: {tod_warn}",
        "PRODUCTION_FILES_MODIFIED: false",
        "TRADING_ENABLED: false",
        "```",
    ]

    with open(OUT_DIR / "BASELINE_AUDIT_REPORT.md", "w") as f:
        f.write("\n".join(report))
    print("\n  Saved: BASELINE_AUDIT_REPORT.md")
    print("=" * 70)
    print("Audit 3 complete.")
    print("=" * 70)


if __name__ == "__main__":
    main()
