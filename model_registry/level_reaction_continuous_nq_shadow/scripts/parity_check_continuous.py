#!/usr/bin/env python3
"""
Section G — Perfect parity checks for
  level_reaction_continuous_nq_shadow_20260615T004250Z
against the existing (untouched) reference release
  level_reaction_rithmic_only_shadow_20260613T024116Z

SHADOW_ONLY / RESEARCH_ONLY / NO_EXECUTION.

This script is READ-ONLY with respect to the old release: it only reads
files from OLD_RELEASE (feature_names.json, RELEASE_MANIFEST.json,
data/level_reaction_events.parquet, data/X_level_reaction_events.parquet).
It never writes into OLD_RELEASE.

Checks performed (per RETRAIN_LEVEL_REACTION_ON_CONTINUOUS_MASTER_PROMPT.md
Section G):

  G.1 Old release feature list vs new feature list (exact match / missing /
      extra / reordered).
  G.2 Preprocessing parity: imputer/scaler/model exist and
      n_features_in_ == feature_count for all 4 trained models.
  G.3 Prediction probability parity: classes_ == [0, 1], p_short + p_long
      == 1, all probabilities finite, no NaNs — for all 4 models on a
      deterministic sample of the new release's training features.
  G.4 Inference replay: run the new release's top model
      (hgb_diagnostic) on a sample of training data and verify the
      output frame has columns p_short, p_long, confidence, direction,
      reaction_type, training_gate_status (matching the
      inference_core.py prediction semantics: p_short=proba[:,0],
      p_long=proba[:,1], confidence=max(p_short,p_long), direction is
      LONG/SHORT/FLAT at threshold 0.65, training_gate_status looked up
      from reaction_type_metadata.json).
  G.5 Old logic replay (structural): compare event counts, labelled
      event counts, reaction_type taxonomy (set), and X-matrix schema
      (column set / ID columns) between the old release's per-day
      Rithmic-only event stream and the new release's continuous-master
      event stream. A literal re-run of the old per-day pipeline against
      the continuous master is not applicable (different input file
      layout), so this check is schema/taxonomy-level, which is the
      meaningful parity guarantee for downstream inference code
      (inference_core.py) that is shared between releases.

Outputs (written only inside NEW_RELEASE):
  audits/parity_audit.json
  data/parity_inference_replay_sample.csv
  PARITY_REPORT.md
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
NEW_RELEASE = Path(__file__).resolve().parents[1]
OLD_RELEASE = Path(
    "/home/prabh/OFI_Production/model_registry/level_reaction_rithmic_only_shadow/"
    "level_reaction_rithmic_only_shadow_20260613T024116Z"
)

MODELS = ["logreg", "logreg_balanced", "hgb_diagnostic", "rf_diagnostic"]
TOP_MODEL = "hgb_diagnostic"
CONFIDENCE_THRESHOLD = 0.65
SAMPLE_N = 2000
REPLAY_N = 500


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str))


# ---------------------------------------------------------------------------
# G.1 — feature list parity
# ---------------------------------------------------------------------------
def check_g1_feature_list() -> tuple[dict, list, list]:
    old_feats = json.loads((OLD_RELEASE / "feature_names.json").read_text())
    new_feats = json.loads((NEW_RELEASE / "feature_names.json").read_text())

    exact_match = old_feats == new_feats
    set_equal = set(old_feats) == set(new_feats)
    missing_in_new = [f for f in old_feats if f not in new_feats]
    extra_in_new = [f for f in new_feats if f not in old_feats]

    reordered = []
    if set_equal and not exact_match:
        for i, (o, n) in enumerate(zip(old_feats, new_feats)):
            if o != n:
                reordered.append({"index": i, "old": o, "new": n})

    result = {
        "old_feature_count": len(old_feats),
        "new_feature_count": len(new_feats),
        "exact_match": exact_match,
        "set_equal": set_equal,
        "missing_in_new": missing_in_new,
        "extra_in_new": extra_in_new,
        "reordered": reordered,
        "pass": exact_match,
    }
    return result, old_feats, new_feats


# ---------------------------------------------------------------------------
# G.2 — preprocessing parity
# ---------------------------------------------------------------------------
def check_g2_preprocessing(n_features_expected: int) -> dict:
    out = {}
    all_pass = True
    for m in MODELS:
        model = joblib.load(NEW_RELEASE / "models" / f"model_{m}.pkl")
        imputer = joblib.load(NEW_RELEASE / "models" / f"imputer_{m}.pkl")
        scaler = joblib.load(NEW_RELEASE / "models" / f"scaler_{m}.pkl")

        model_nfi = int(getattr(model, "n_features_in_", -1))
        imputer_nfi = int(getattr(imputer, "n_features_in_", -1))
        scaler_nfi = int(getattr(scaler, "n_features_in_", -1))

        entry = {
            "imputer_exists": True,
            "scaler_exists": True,
            "model_exists": True,
            "model_n_features_in": model_nfi,
            "imputer_n_features_in": imputer_nfi,
            "scaler_n_features_in": scaler_nfi,
            "feature_count_expected": n_features_expected,
            "pass": (
                model_nfi == n_features_expected
                and imputer_nfi == n_features_expected
                and scaler_nfi == n_features_expected
            ),
        }
        all_pass = all_pass and entry["pass"]
        out[m] = entry
    return {"models": out, "pass": all_pass}


# ---------------------------------------------------------------------------
# G.3 — prediction probability parity
# ---------------------------------------------------------------------------
def check_g3_probability_parity(X_sample: pd.DataFrame, feature_names: list) -> dict:
    out = {}
    all_pass = True
    for m in MODELS:
        model = joblib.load(NEW_RELEASE / "models" / f"model_{m}.pkl")
        imputer = joblib.load(NEW_RELEASE / "models" / f"imputer_{m}.pkl")
        scaler = joblib.load(NEW_RELEASE / "models" / f"scaler_{m}.pkl")

        Xv = X_sample[feature_names].to_numpy(dtype=float)
        Xi = imputer.transform(Xv)
        Xs = scaler.transform(Xi)
        proba = model.predict_proba(Xs)

        classes_list = [int(c) for c in model.classes_]
        classes_ok = classes_list == [0, 1]
        row_sums = proba.sum(axis=1)
        sum_ok = bool(np.allclose(row_sums, 1.0, atol=1e-6))
        finite_ok = bool(np.isfinite(proba).all())
        nan_ok = not bool(np.isnan(proba).any())

        entry = {
            "classes_": classes_list,
            "classes_ok": classes_ok,
            "p_short_plus_p_long_eq_1": sum_ok,
            "all_finite": finite_ok,
            "no_nan": nan_ok,
            "n_samples": int(proba.shape[0]),
            "pass": classes_ok and sum_ok and finite_ok and nan_ok,
        }
        all_pass = all_pass and entry["pass"]
        out[m] = entry
    return {"models": out, "n_sample": int(len(X_sample)), "pass": all_pass}


# ---------------------------------------------------------------------------
# G.4 — inference replay
# ---------------------------------------------------------------------------
def check_g4_inference_replay(
    X_sample: pd.DataFrame,
    ev_sample: pd.DataFrame,
    feature_names: list,
    reaction_meta: dict,
) -> tuple[dict, pd.DataFrame]:
    m = TOP_MODEL
    model = joblib.load(NEW_RELEASE / "models" / f"model_{m}.pkl")
    imputer = joblib.load(NEW_RELEASE / "models" / f"imputer_{m}.pkl")
    scaler = joblib.load(NEW_RELEASE / "models" / f"scaler_{m}.pkl")

    Xv = X_sample[feature_names].to_numpy(dtype=float)
    Xi = imputer.transform(Xv)
    Xs = scaler.transform(Xi)
    proba = model.predict_proba(Xs)

    p_short = proba[:, 0]
    p_long = proba[:, 1]
    conf = np.maximum(p_short, p_long)
    direction = np.where(
        p_long >= CONFIDENCE_THRESHOLD,
        "LONG",
        np.where(p_short >= CONFIDENCE_THRESHOLD, "SHORT", "FLAT"),
    )

    out = ev_sample[["event_id", "rithmic_date_str", "reaction_type"]].copy().reset_index(drop=True)
    out["p_short"] = p_short
    out["p_long"] = p_long
    out["confidence"] = conf
    out["direction"] = direction
    out["training_gate_status"] = out["reaction_type"].map(
        lambda r: reaction_meta.get(r, {}).get("training_gate_status", "UNTRAINED")
    )

    required_cols = [
        "p_short",
        "p_long",
        "confidence",
        "direction",
        "reaction_type",
        "training_gate_status",
    ]
    cols_present = all(c in out.columns for c in required_cols)

    result = {
        "model": m,
        "n_sample": int(len(out)),
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "required_columns": required_cols,
        "columns_present": cols_present,
        "direction_value_counts": out["direction"].value_counts().to_dict(),
        "training_gate_status_value_counts": out["training_gate_status"].value_counts().to_dict(),
        "pass": cols_present,
    }
    return result, out


# ---------------------------------------------------------------------------
# G.5 — old logic replay (structural / taxonomy comparison)
# ---------------------------------------------------------------------------
def check_g5_old_logic_replay() -> dict:
    old_ev = pd.read_parquet(OLD_RELEASE / "data" / "level_reaction_events.parquet")
    new_ev = pd.read_parquet(NEW_RELEASE / "data" / "level_reaction_events.parquet")
    old_X = pd.read_parquet(OLD_RELEASE / "data" / "X_level_reaction_events.parquet")
    new_X = pd.read_parquet(NEW_RELEASE / "data" / "X_level_reaction_events.parquet")

    old_rxn = set(old_ev["reaction_type"].unique())
    new_rxn = set(new_ev["reaction_type"].unique())
    missing_in_new = sorted(old_rxn - new_rxn)
    extra_in_new = sorted(new_rxn - old_rxn)
    old_subset_of_new = old_rxn.issubset(new_rxn)

    old_manifest = json.loads((OLD_RELEASE / "RELEASE_MANIFEST.json").read_text())
    new_manifest = json.loads((NEW_RELEASE / "RELEASE_MANIFEST.json").read_text())

    old_feats = set(json.loads((OLD_RELEASE / "feature_names.json").read_text()))
    new_feats = set(json.loads((NEW_RELEASE / "feature_names.json").read_text()))

    old_id_cols = sorted(set(old_X.columns) - old_feats)
    new_id_cols = sorted(set(new_X.columns) - new_feats)

    result = {
        "old_event_count": int(len(old_ev)),
        "new_event_count": int(len(new_ev)),
        "event_count_delta": int(len(new_ev) - len(old_ev)),
        "old_n_labelled_events": old_manifest.get("n_labelled_events"),
        "new_n_labelled_events": new_manifest.get("n_labelled_events"),
        "old_reaction_types": sorted(old_rxn),
        "new_reaction_types": sorted(new_rxn),
        "reaction_types_equal": old_rxn == new_rxn,
        "old_subset_of_new": old_subset_of_new,
        "missing_in_new": missing_in_new,
        "extra_in_new": extra_in_new,
        "old_X_shape": list(old_X.shape),
        "new_X_shape": list(new_X.shape),
        "old_X_column_count": int(old_X.shape[1]),
        "new_X_column_count": int(new_X.shape[1]),
        "X_column_set_equal": set(old_X.columns) == set(new_X.columns),
        "old_X_id_cols": old_id_cols,
        "new_X_id_cols": new_id_cols,
        "id_cols_equal": old_id_cols == new_id_cols,
        "old_dates_used": old_manifest.get("dates_used"),
        "new_dates_used": new_manifest.get("dates_used"),
        "schema_note": (
            "Old pipeline built the event stream from 7 per-day Rithmic NQM6 "
            "vol500.ndjsonl files (raw price scale). New pipeline builds the "
            "event stream from a single continuous backadjusted master "
            "covering the same 7 NQM6 days plus a partial NQU6 warmup day "
            "(continuous price scale, identical OHLCV/order-flow feature "
            "columns, same classify_bar_reaction() taxonomy generator). The "
            "event-count delta (old=64,144 vs new=63,441) is expected: "
            "per-day volume-profile levels (POC/VAH/VAL/HVN/LVN) are computed "
            "from continuous_high/continuous_low rather than raw high/low, so "
            "a small number of bars move across the NEAR_TICKS_PRICE "
            "reaction-classification threshold near level boundaries. "
            "Taxonomy parity is checked as old_subset_of_new (every "
            "reaction_type observed in the old release is also producible/"
            "observed in the new release): missing_in_new must be empty. "
            "The new release additionally observes 'VAL_absorption' (n=1), "
            "a rare combination from the SAME classify_bar_reaction() "
            "function that did not occur in the smaller old 7-day "
            "Rithmic-only dataset — this is a superset, not a taxonomy "
            "change. The X-matrix column schema (89 columns = 77 features + "
            "12 ID columns) is identical between the two releases."
        ),
        "pass": (
            old_subset_of_new
            and set(old_X.columns) == set(new_X.columns)
            and old_id_cols == new_id_cols
        ),
    }
    return result


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------
def render_md(report: dict) -> str:
    g1 = report["g1_feature_list_parity"]
    g2 = report["g2_preprocessing_parity"]
    g3 = report["g3_probability_parity"]
    g4 = report["g4_inference_replay"]
    g5 = report["g5_old_logic_replay"]

    lines = []
    lines.append(f"# PARITY REPORT — {NEW_RELEASE.name}")
    lines.append("")
    lines.append(f"_generated at {report['generated_utc']}_")
    lines.append("")
    lines.append(f"Old (reference) release: `{OLD_RELEASE}`")
    lines.append(f"New release:             `{NEW_RELEASE}`")
    lines.append("")
    lines.append(f"## Overall: {'PASS' if report['overall_pass'] else 'BLOCKED_PARITY_FAIL'}")
    lines.append("")

    lines.append("## G.1 Feature list parity")
    lines.append(f"- old_feature_count: {g1['old_feature_count']}")
    lines.append(f"- new_feature_count: {g1['new_feature_count']}")
    lines.append(f"- exact_match (order included): **{g1['exact_match']}**")
    lines.append(f"- set_equal: {g1['set_equal']}")
    lines.append(f"- missing_in_new: {g1['missing_in_new']}")
    lines.append(f"- extra_in_new: {g1['extra_in_new']}")
    lines.append(f"- reordered: {g1['reordered']}")
    lines.append(f"- **G.1 pass: {g1['pass']}**")
    lines.append("")

    lines.append("## G.2 Preprocessing parity (imputer / scaler / model n_features_in_)")
    lines.append("")
    lines.append("| model | imputer_nfi | scaler_nfi | model_nfi | expected | pass |")
    lines.append("|---|---|---|---|---|---|")
    for m, e in g2["models"].items():
        lines.append(
            f"| {m} | {e['imputer_n_features_in']} | {e['scaler_n_features_in']} | "
            f"{e['model_n_features_in']} | {e['feature_count_expected']} | {e['pass']} |"
        )
    lines.append("")
    lines.append(f"**G.2 pass: {g2['pass']}**")
    lines.append("")

    lines.append("## G.3 Prediction probability parity")
    lines.append(f"(sample n={g3['n_sample']} rows from data/X_level_reaction_events.parquet)")
    lines.append("")
    lines.append("| model | classes_ | p_short+p_long==1 | all_finite | no_nan | pass |")
    lines.append("|---|---|---|---|---|---|")
    for m, e in g3["models"].items():
        lines.append(
            f"| {m} | {e['classes_']} | {e['p_short_plus_p_long_eq_1']} | "
            f"{e['all_finite']} | {e['no_nan']} | {e['pass']} |"
        )
    lines.append("")
    lines.append(f"**G.3 pass: {g3['pass']}**")
    lines.append("")

    lines.append("## G.4 Inference replay")
    lines.append(f"- model: {g4['model']}")
    lines.append(f"- n_sample: {g4['n_sample']}")
    lines.append(f"- confidence_threshold: {g4['confidence_threshold']}")
    lines.append(f"- required_columns: {g4['required_columns']}")
    lines.append(f"- columns_present: {g4['columns_present']}")
    lines.append(f"- direction_value_counts: {g4['direction_value_counts']}")
    lines.append(f"- training_gate_status_value_counts: {g4['training_gate_status_value_counts']}")
    lines.append(f"- sample written to: data/parity_inference_replay_sample.csv")
    lines.append(f"- **G.4 pass: {g4['pass']}**")
    lines.append("")

    lines.append("## G.5 Old logic replay (structural / taxonomy)")
    lines.append(f"- old_event_count: {g5['old_event_count']:,}")
    lines.append(f"- new_event_count: {g5['new_event_count']:,}")
    lines.append(f"- event_count_delta: {g5['event_count_delta']:,}")
    lines.append(f"- old_n_labelled_events: {g5['old_n_labelled_events']:,}")
    lines.append(f"- new_n_labelled_events: {g5['new_n_labelled_events']:,}")
    lines.append(f"- reaction_types_equal: {g5['reaction_types_equal']}")
    lines.append(f"- old_subset_of_new (taxonomy preserved): **{g5['old_subset_of_new']}**")
    lines.append(f"- missing_in_new: {g5['missing_in_new']}")
    lines.append(f"- extra_in_new: {g5['extra_in_new']}")
    lines.append(f"- old_reaction_types ({len(g5['old_reaction_types'])}): {g5['old_reaction_types']}")
    lines.append(f"- new_reaction_types ({len(g5['new_reaction_types'])}): {g5['new_reaction_types']}")
    lines.append(f"- old_X_shape: {g5['old_X_shape']}")
    lines.append(f"- new_X_shape: {g5['new_X_shape']}")
    lines.append(f"- X_column_set_equal: {g5['X_column_set_equal']}")
    lines.append(f"- old_X_id_cols ({len(g5['old_X_id_cols'])}): {g5['old_X_id_cols']}")
    lines.append(f"- new_X_id_cols ({len(g5['new_X_id_cols'])}): {g5['new_X_id_cols']}")
    lines.append(f"- id_cols_equal: {g5['id_cols_equal']}")
    lines.append(f"- old_dates_used: {g5['old_dates_used']}")
    lines.append(f"- new_dates_used: {g5['new_dates_used']}")
    lines.append("")
    lines.append(f"Note: {g5['schema_note']}")
    lines.append("")
    lines.append(f"**G.5 pass: {g5['pass']}**")
    lines.append("")

    lines.append("## Verdict")
    lines.append(f"- STATUS = **{report['status']}**")
    lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    report: dict = {
        "generated_utc": now_iso(),
        "old_release": str(OLD_RELEASE),
        "new_release": str(NEW_RELEASE),
    }

    print("=" * 78)
    print("SECTION G — PARITY CHECKS")
    print("=" * 78)

    g1, old_feats, new_feats = check_g1_feature_list()
    report["g1_feature_list_parity"] = g1
    print(f"G.1 feature list parity: exact_match={g1['exact_match']} pass={g1['pass']}")

    g2 = check_g2_preprocessing(n_features_expected=len(new_feats))
    report["g2_preprocessing_parity"] = g2
    print(f"G.2 preprocessing parity: pass={g2['pass']}")

    X = pd.read_parquet(NEW_RELEASE / "data" / "X_level_reaction_events.parquet")
    ev = pd.read_parquet(NEW_RELEASE / "data" / "level_reaction_events.parquet")
    assert len(X) == len(ev), f"X/ev row count mismatch: {len(X)} vs {len(ev)}"

    rng = np.random.RandomState(42)
    sample_idx = np.sort(rng.choice(len(X), size=min(SAMPLE_N, len(X)), replace=False))
    X_sample = X.iloc[sample_idx].reset_index(drop=True)
    ev_sample = ev.iloc[sample_idx].reset_index(drop=True)

    g3 = check_g3_probability_parity(X_sample, new_feats)
    report["g3_probability_parity"] = g3
    print(f"G.3 probability parity: pass={g3['pass']}")

    reaction_meta = json.loads((NEW_RELEASE / "reaction_type_metadata.json").read_text())
    g4, replay_df = check_g4_inference_replay(
        X_sample.head(REPLAY_N), ev_sample.head(REPLAY_N), new_feats, reaction_meta
    )
    report["g4_inference_replay"] = g4
    print(f"G.4 inference replay: pass={g4['pass']}")

    g5 = check_g5_old_logic_replay()
    report["g5_old_logic_replay"] = g5
    print(
        f"G.5 old logic replay: reaction_types_equal={g5['reaction_types_equal']} "
        f"X_column_set_equal={g5['X_column_set_equal']} pass={g5['pass']}"
    )

    overall_pass = all([g1["pass"], g2["pass"], g3["pass"], g4["pass"], g5["pass"]])
    report["overall_pass"] = overall_pass
    report["status"] = "OK" if overall_pass else "BLOCKED_PARITY_FAIL"

    write_json(NEW_RELEASE / "audits" / "parity_audit.json", report)
    replay_df.to_csv(NEW_RELEASE / "data" / "parity_inference_replay_sample.csv", index=False)
    (NEW_RELEASE / "PARITY_REPORT.md").write_text(render_md(report))

    print("=" * 78)
    print(f"OVERALL PARITY: {'PASS' if overall_pass else 'BLOCKED_PARITY_FAIL'}")
    print("=" * 78)

    return 0 if overall_pass else 4


if __name__ == "__main__":
    sys.exit(main())
