#!/usr/bin/env python3
"""
Continuous NQ Level-Reaction Shadow Inference (Section H).

SHADOW_ONLY / RESEARCH_ONLY / NO_EXECUTION.

Reads the live continuous backadjusted NQ master:
  /home/prabh/OFI_Live_Features/master_NQ_continuous_backadjusted_shadow.ndjsonl

and scores every detected level-reaction event using the
level_reaction_continuous_nq_shadow_20260615T004250Z release
(model_hgb_diagnostic.pkl + imputer + scaler + feature_names.json,
77 features, EXACT same order/logic as training).

ABSOLUTE RULES (enforced):
  * READ-ONLY w.r.t. the continuous master, the old protected master
    (/home/prabh/OFI_Live_Features/master.ndjsonl), the model release, and
    the parser/daemon. This script never writes outside its own outputs/.
  * Only imputer.transform / scaler.transform / model.predict_proba are
    called — never fit() / fit_transform().
  * shadow_only=true, trading_enabled=false in every output.
  * Projected prior NQM6->NQU6 levels (used only while the NQU6 day has
    fewer than MIN_BARS_PER_DAY_FOR_EVENTS bars) are tagged
    level_source="projected_prior_level" / data_source="PROJECTED_NQM6_TO_NQU6"
    and are NEVER presented as native NQU6 levels.

Per-bar feature construction (levels, OHLC-vol path, order-flow snapshot,
one-hots) is a verbatim port of
  level_reaction_continuous_nq_shadow_20260615T004250Z/scripts/pipeline_continuous.py
  (volume_profile_levels, classify_bar_reaction, session_label,
   build_event_stream, build_event_features), operating on
  continuous_open/high/low/close (NOT raw_*), matching the training pipeline
  exactly.

Outputs (overwritten each run) in outputs/:
  latest_continuous_nq_predictions.csv
  latest_continuous_nq_prediction_summary.csv
  latest_continuous_nq_inference_summary.json

Modes:
  --mode once   (default) run a single inference pass and exit.
  --mode loop   run forever; reload the master only when its mtime changes,
                cache model artifacts across cycles, sleep --interval-sec
                between mtime checks. Intended for the optional systemd
                service ofi-level-reaction-continuous-nq-inference.service.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import importlib.util
import json
import os
import sys
import time
import uuid
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_MASTER = Path("/home/prabh/OFI_Live_Features/master_NQ_continuous_backadjusted_shadow.ndjsonl")
RELEASE_REGISTRY_DIR = Path(
    "/home/prabh/OFI_Production/model_registry/level_reaction_continuous_nq_shadow"
)
ACTIVE_RELEASE_POINTER = Path(os.environ.get(
    "OFI_ACTIVE_MODEL_RELEASE",
    str(RELEASE_REGISTRY_DIR / "ACTIVE_SHADOW_RELEASE"),
))
FALLBACK_RELEASE = Path(
    "/home/prabh/OFI_Production/model_registry/level_reaction_continuous_nq_shadow/"
    "level_reaction_continuous_nq_shadow_20260615T004250Z"
)
DEFAULT_RELEASE = ACTIVE_RELEASE_POINTER if ACTIVE_RELEASE_POINTER.exists() else FALLBACK_RELEASE
PROJECTED_LEVELS_CSV = Path("/home/prabh/OFI_Live_Features/projected_levels_NQM6_to_NQU6.csv")
OUTDIR = Path(__file__).resolve().parent / "outputs"

MODEL_NAME = "hgb_diagnostic"
DEFAULT_CONFIDENCE = 0.65
ROLL_QUALITY_FLAG = "ROLLOVER_WARMUP_LOW_SAMPLE"

OF_COLS = [
    "delta_norm", "delta_norm_lag_1", "delta_norm_lag_2", "delta_norm_lag_3",
    "delta_rolling_5",
    "mlofi_sum", "mlofi_decay_sum", "mlofi_norm", "mlofi_rolling_5",
    "mlofi_accel", "decay_norm",
    "mlofi_norm_lag_1", "mlofi_norm_lag_2", "mlofi_norm_lag_3",
    "decay_norm_lag_1", "decay_norm_lag_2", "decay_norm_lag_3",
    "sweep_imbalance_norm", "sweep_norm", "sweep_buy_ratio", "sweep_sell_ratio",
    "buy_ratio", "sell_ratio",
    "vpin", "vpin_lag_1", "vpin_lag_2", "vpin_lag_3",
    "entropy_score", "flow_alignment",
    "mid_resid_z", "mid_ret1",
    "volatility_5", "bar_duration_s",
    "minute_of_day", "tod_minute", "dow",
    "delta_norm_resid_z20", "volatility_5_resid_z20",
    "sweep_imbalance_norm_resid_z20", "vpin_resid_z20",
]
OHLC_VOL_COLS = [
    "candle_body_vol", "candle_range_vol", "upper_wick_vol", "lower_wick_vol",
    "close_location", "open_to_close_sign", "close_vs_prev_close_vol",
    "close_vs_roll_mean_vol", "high_break_vol", "low_break_vol",
]
PROXY_COLS = ["bid_pull_PROXY", "ask_pull_PROXY"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def block(reason: str, code: int = 2) -> None:
    print(f"\nBLOCKED: {reason}", flush=True)
    sys.exit(code)


def resolve_release_path(ref: Path) -> Path:
    """Resolve ACTIVE_SHADOW_RELEASE whether it is a symlink or text pointer."""
    ref = Path(ref)
    if ref.is_symlink():
        return ref.resolve(strict=True)
    if ref.is_file():
        txt = ref.read_text().strip()
        if txt:
            return Path(txt).expanduser().resolve(strict=True)
    return ref.expanduser().resolve(strict=True)


def load_release_manifest(release: Path) -> Dict[str, Any]:
    for name in ("release_manifest.json", "RELEASE_MANIFEST.json"):
        path = release / name
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                return {}
    return {}


def master_signature(path: Path) -> Tuple[int, int, int]:
    """mtime, size, and non-empty line count so loop mode notices row changes."""
    st = path.stat()
    n_rows = 0
    with open(path, "rb") as fh:
        for line in fh:
            if line.strip():
                n_rows += 1
    return int(st.st_mtime_ns), int(st.st_size), int(n_rows)


def atomic_write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    try:
        df.to_csv(tmp, index=False)
        tmp.replace(path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def atomic_write_json(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    try:
        tmp.write_text(json.dumps(obj, indent=2, default=str))
        tmp.replace(path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


@contextlib.contextmanager
def output_write_lock(out_dir: Path, timeout_sec: float = 30.0):
    """fcntl.flock exclusive lock protecting concurrent inference output writes."""
    lock_path = out_dir / ".inference_write.lock"
    out_dir.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a")
    try:
        deadline = time.monotonic() + timeout_sec
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    fh.close()
                    raise RuntimeError(
                        f"LOCK_TIMEOUT: could not acquire output write lock after {timeout_sec}s"
                    )
                time.sleep(0.1)
        yield
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        except Exception:
            pass
        fh.close()


# ---------------------------------------------------------------------------
# Master loader (full file — continuous master is small; per-day volume
# profile requires complete days, so no mid-day tail truncation)
# ---------------------------------------------------------------------------
def load_master(path: Path) -> Tuple[pd.DataFrame, int]:
    """Load master ndjsonl. Returns (deduped/sorted df, raw_row_count).

    raw_row_count is the number of parseable lines before the
    bar_end_ts_ns dedup below, so it matches the dashboard's live
    "ROWS: N" banner (which reads the file without deduping).
    """
    rows: List[Dict[str, Any]] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    if not rows:
        block(f"master file at {path} produced 0 parseable rows")
    raw_row_count = len(rows)
    df = pd.DataFrame(rows)
    df["bar_end_ts_ns"] = pd.to_numeric(df["bar_end_ts_ns"], errors="coerce")
    df = df.dropna(subset=["bar_end_ts_ns"]).copy()
    df["bar_end_ts_ns"] = df["bar_end_ts_ns"].astype("int64")
    df = df.drop_duplicates(subset=["bar_end_ts_ns"], keep="first")
    df = df.sort_values("bar_end_ts_ns").reset_index(drop=True)
    if "rithmic_date_str" not in df.columns:
        if "day" in df.columns:
            df["rithmic_date_str"] = df["day"].astype(str)
        else:
            df["rithmic_date_str"] = (
                pd.to_datetime(df["bar_end_ts_ns"], unit="ns", utc=True).dt.date.astype(str)
            )
    return df, raw_row_count


# ---------------------------------------------------------------------------
# Cached artifacts (model + preprocessing + training-pipeline functions)
# ---------------------------------------------------------------------------
class Artifacts:
    def __init__(self, release: Path, model_name: str = MODEL_NAME):
        self.release_ref = Path(release)
        self.release = resolve_release_path(self.release_ref)
        self.model_name = model_name
        self.release_manifest = load_release_manifest(self.release)
        self.release_id = str(self.release_manifest.get("release_id") or self.release.name)
        self.gate_status = str(self.release_manifest.get("gate_status", "UNKNOWN"))
        self.active_dashboard_model = bool(self.release_manifest.get("active_dashboard_model", False))
        self.paper_trading_allowed = bool(self.release_manifest.get("paper_trading_allowed", False))
        self.production_execution_allowed = bool(self.release_manifest.get("production_execution_allowed", False))

        model_path = self.release / "models" / f"model_{model_name}.pkl"
        imputer_path = self.release / "models" / f"imputer_{model_name}.pkl"
        scaler_path = self.release / "models" / f"scaler_{model_name}.pkl"
        feat_path = self.release / "feature_names.json"
        meta_path = self.release / "reaction_type_metadata.json"
        pipeline_dir = self.release / "scripts"

        for label, p in [
            ("model", model_path), ("imputer", imputer_path), ("scaler", scaler_path),
            ("feature_names.json", feat_path), ("reaction_type_metadata.json", meta_path),
            ("pipeline_continuous.py dir", pipeline_dir),
        ]:
            if not p.exists():
                block(f"required {label} not found at {p}")

        self.model = joblib.load(model_path)
        self.imputer = joblib.load(imputer_path)
        # sklearn 1.8.0 renamed SimpleImputer._fill_dtype → _fit_dtype; patch for load-time compat
        if not hasattr(self.imputer, "_fill_dtype") and hasattr(self.imputer, "_fit_dtype"):
            self.imputer._fill_dtype = self.imputer._fit_dtype
        self.scaler = joblib.load(scaler_path)
        self.feature_names: List[str] = json.loads(feat_path.read_text())
        self.reaction_meta: Dict[str, Any] = json.loads(meta_path.read_text())

        n_feat = len(self.feature_names)
        if list(self.model.classes_) != [0, 1]:
            block(f"model.classes_={list(self.model.classes_)}; expected [0, 1]")
        for nm, obj in (("model", self.model), ("imputer", self.imputer), ("scaler", self.scaler)):
            if getattr(obj, "n_features_in_", None) != n_feat:
                block(f"{nm}.n_features_in_={getattr(obj, 'n_features_in_', None)}; expected {n_feat}")

        spec = importlib.util.spec_from_file_location(
            f"pipeline_continuous_{self.release.name}",
            pipeline_dir / "pipeline_continuous.py",
        )
        if spec is None or spec.loader is None:
            block(f"could not import pipeline_continuous.py from {pipeline_dir}")
        PC = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(PC)  # training-time pipeline, READ-ONLY import

        self.PC = PC
        self.TICK: float = PC.TICK
        self.NEAR_TICKS_PRICE: float = PC.NEAR_TICKS_PRICE
        self.MIN_BARS: int = PC.MIN_BARS_PER_DAY_FOR_EVENTS
        self.volume_profile_levels = PC.volume_profile_levels
        self.classify_bar_reaction = PC.classify_bar_reaction
        self.session_label = PC.session_label

        self.model_class = f"{type(self.model).__module__}.{type(self.model).__name__}"
        self.model_classes = [int(c) for c in self.model.classes_]


# ---------------------------------------------------------------------------
# Per-day feature engineering (verbatim port of pipeline_continuous logic,
# operating on continuous_* columns)
# ---------------------------------------------------------------------------
def add_ohlc_vol(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    h = pd.to_numeric(out["continuous_high"], errors="coerce")
    l = pd.to_numeric(out["continuous_low"], errors="coerce")
    c = pd.to_numeric(out["continuous_close"], errors="coerce")
    o = pd.to_numeric(out["continuous_open"], errors="coerce")
    vol = pd.to_numeric(out["volatility_5"], errors="coerce").replace(0, np.nan)
    rng = (h - l).abs()
    out["candle_body_vol"] = (c - o).abs() / vol
    out["candle_range_vol"] = rng / vol
    out["upper_wick_vol"] = (h - np.maximum(c, o)) / vol
    out["lower_wick_vol"] = (np.minimum(c, o) - l) / vol
    out["close_location"] = (c - l) / rng.replace(0, np.nan)
    out["open_to_close_sign"] = np.sign(c - o)
    prev_c = c.shift(1)
    out["close_vs_prev_close_vol"] = (c - prev_c) / vol
    out["close_vs_roll_mean_vol"] = (c - c.rolling(20, min_periods=5).mean()) / vol
    roll_max20 = c.rolling(20, min_periods=5).max().shift(1)
    roll_min20 = c.rolling(20, min_periods=5).min().shift(1)
    out["high_break_vol"] = (h - roll_max20) / vol
    out["low_break_vol"] = (roll_min20 - l) / vol
    return out


def add_proxy_cols(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    mn = pd.to_numeric(out["mlofi_norm"], errors="coerce")
    mz = pd.to_numeric(out["mid_resid_z"], errors="coerce")
    dn = pd.to_numeric(out["delta_norm"], errors="coerce")
    out["bid_pull_PROXY"] = ((mz < -1.5) & (mn < -0.6) & (dn > -0.4)).astype(int)
    out["ask_pull_PROXY"] = ((mz > 1.5) & (mn > 0.6) & (dn < 0.4)).astype(int)
    return out


def compute_absorb_z(df: pd.DataFrame, TICK: float) -> np.ndarray:
    h = pd.to_numeric(df["continuous_high"], errors="coerce").to_numpy()
    l = pd.to_numeric(df["continuous_low"], errors="coerce").to_numpy()
    vt = pd.to_numeric(df["vol_total"], errors="coerce").to_numpy()
    prx_range = np.maximum(h - l, TICK)
    absorb_strength = vt / prx_range
    s = pd.Series(absorb_strength)
    z = ((s - s.rolling(50, min_periods=10).mean()) / s.rolling(50, min_periods=10).std()).to_numpy()
    return z


def flat_levels_from_vp(vp: Dict[str, Any]) -> List[Tuple[float, str]]:
    return (
        [(vp["poc_px"], "POC"), (vp["vah_px"], "VAH"), (vp["val_px"], "VAL")]
        + [(p, "HVN") for p in vp["hvn_px"]]
        + [(p, "LVN") for p in vp["lvn_px"]]
    )


def projected_vp_from_csv(proj_df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """Build a vp-shaped dict {poc_px, vah_px, val_px, hvn_px[], lvn_px[]} from
    projected_levels_NQM6_to_NQU6.csv's projected_level_price column (already
    on the continuous price scale: old_level_price + roll_gap_points)."""
    sub = proj_df[proj_df["new_contract"] == "NQU6"]
    if sub.empty:
        return None

    def _one(level_type: str) -> Optional[float]:
        rows = sub[sub["level_type"] == level_type]
        if rows.empty:
            return None
        return float(rows["projected_level_price"].iloc[0])

    poc = _one("POC")
    vah = _one("VAH")
    val = _one("VAL")
    if poc is None or vah is None or val is None:
        return None
    hvn = sub[sub["level_type"] == "HVN"]["projected_level_price"].astype(float).tolist()
    lvn = sub[sub["level_type"] == "LVN"]["projected_level_price"].astype(float).tolist()
    return {"poc_px": poc, "vah_px": vah, "val_px": val, "hvn_px": hvn, "lvn_px": lvn}


def process_day(
    df: pd.DataFrame,
    vp: Dict[str, Any],
    level_source: str,
    data_source: str,
    art: Artifacts,
    event_id_start: int,
) -> Tuple[List[Dict[str, Any]], int]:
    """Detect events + build the 77-feature snapshot for one (date, contract)
    slice. Mirrors pipeline_continuous.build_event_stream +
    build_event_features (per-bar loop, fused for inference)."""
    TICK = art.TICK
    df = df.reset_index(drop=True)
    df = add_ohlc_vol(df)
    df = add_proxy_cols(df)

    h = pd.to_numeric(df["continuous_high"], errors="coerce").to_numpy()
    l = pd.to_numeric(df["continuous_low"], errors="coerce").to_numpy()
    c = pd.to_numeric(df["continuous_close"], errors="coerce").to_numpy()
    o = pd.to_numeric(df["continuous_open"], errors="coerce").to_numpy()
    raw_c = pd.to_numeric(df["raw_close"], errors="coerce").to_numpy()
    vol5 = pd.to_numeric(df["volatility_5"], errors="coerce").to_numpy()
    delta = pd.to_numeric(df["delta_norm"], errors="coerce").to_numpy()
    mid_z = pd.to_numeric(df["mid_resid_z"], errors="coerce").to_numpy()
    prior_close = np.r_[np.nan, c[:-1]]
    absorb_z = compute_absorb_z(df, TICK)

    hvn_arr = np.array(vp["hvn_px"], dtype=float) if vp["hvn_px"] else np.array([], dtype=float)
    lvn_arr = np.array(vp["lvn_px"], dtype=float) if vp["lvn_px"] else np.array([], dtype=float)
    flat_levels = flat_levels_from_vp(vp)

    touch_counter: Dict[Tuple[float, str], int] = defaultdict(int)
    last_touch_bar: Dict[Tuple[float, str], int] = {}

    contract_symbol = str(df["contract_symbol"].iloc[0]) if "contract_symbol" in df.columns else None
    rithmic_date = str(df["rithmic_date_str"].iloc[0])

    rows: List[Dict[str, Any]] = []
    event_id = event_id_start

    for i in range(len(df)):
        ci = c[i]
        if not np.isfinite(ci):
            continue
        vi = vol5[i] if (np.isfinite(vol5[i]) and vol5[i] > 1e-9) else float("nan")
        inside_va = int(vp["val_px"] <= ci <= vp["vah_px"])
        above_vah = int(ci > vp["vah_px"])
        below_val = int(ci < vp["val_px"])
        nearest_hvn = float(hvn_arr[int(np.argmin(np.abs(hvn_arr - ci)))]) if hvn_arr.size else float("nan")
        nearest_lvn = float(lvn_arr[int(np.argmin(np.abs(lvn_arr - ci)))]) if lvn_arr.size else float("nan")

        def _dn(lv: float) -> float:
            return float((ci - lv) / TICK) if np.isfinite(lv) else float("nan")

        def _dv(lv: float) -> float:
            return float((ci - lv) / vi) if (np.isfinite(lv) and np.isfinite(vi)) else float("nan")

        for lp, ln in flat_levels:
            rxn = art.classify_bar_reaction(
                h[i], l[i], c[i], o[i],
                lp, ln,
                delta[i], mid_z[i], absorb_z[i],
                prior_close[i], touch_counter[(lp, ln)],
            )
            if rxn is None:
                continue
            tc_past = touch_counter[(lp, ln)]
            bars_since = (i - last_touch_bar[(lp, ln)]) if (lp, ln) in last_touch_bar else -1
            touch_counter[(lp, ln)] += 1
            last_touch_bar[(lp, ln)] = i

            bar = df.iloc[i]
            session = art.session_label(int(bar["minute_of_day"]))

            rec: Dict[str, Any] = {
                "dist_to_poc_ticks": _dn(vp["poc_px"]),
                "dist_to_vah_ticks": _dn(vp["vah_px"]),
                "dist_to_val_ticks": _dn(vp["val_px"]),
                "dist_to_hvn_ticks": _dn(nearest_hvn),
                "dist_to_lvn_ticks": _dn(nearest_lvn),
                "dist_to_poc_vol": _dv(vp["poc_px"]),
                "dist_to_vah_vol": _dv(vp["vah_px"]),
                "dist_to_val_vol": _dv(vp["val_px"]),
                "dist_to_hvn_vol": _dv(nearest_hvn),
                "dist_to_lvn_vol": _dv(nearest_lvn),
                "inside_value_area": inside_va,
                "above_vah": above_vah,
                "below_val": below_val,
                "touch_count_past_only": int(tc_past),
                "bars_since_prior_touch": int(bars_since),
            }
            for col in OF_COLS + OHLC_VOL_COLS + PROXY_COLS:
                if col in bar.index:
                    v = bar[col]
                    rec[col] = float(v) if pd.notna(v) and isinstance(v, (int, float, np.floating)) else np.nan
                else:
                    rec[col] = np.nan
            for lt in ("POC", "VAH", "VAL", "HVN", "LVN"):
                rec[f"lvl_{lt}"] = int(ln == lt)
            for s in ("Asia", "EU", "US_Open", "US_AM", "US_PM", "US_Late"):
                rec[f"sess_{s}"] = int(session == s)
            rec["rxn_rejection_from_above"] = int("rejection_from_above" in rxn)
            rec["rxn_rejection_from_below"] = int("rejection_from_below" in rxn)
            rec["rxn_absorption"] = int("absorption" in rxn)
            rec["rxn_acceptance"] = int("acceptance" in rxn)
            rec["rxn_neutral_touch"] = int("neutral_touch" in rxn)

            meta = art.reaction_meta.get(rxn, {"n_training": 0, "training_gate_status": "UNTRAINED"})

            rows.append({
                "event_id": event_id,
                "rithmic_date_str": rithmic_date,
                "bar_idx_in_day": i,
                "bar_end_ts_ns": int(bar["bar_end_ts_ns"]),
                "timestamp_utc": str(bar.get("timestamp_utc", "")),
                "session": session,
                "level_type": ln,
                "level_price_continuous": float(lp),
                "level_source": level_source,
                "reaction_type": rxn,
                "training_gate_status": meta.get("training_gate_status", "UNTRAINED"),
                "n_training_for_reaction": meta.get("n_training", 0),
                "contract_symbol": contract_symbol,
                "data_source": data_source,
                "continuous_close": float(ci),
                "raw_close": float(raw_c[i]) if np.isfinite(raw_c[i]) else None,
                "dist_to_level_ticks": float((ci - lp) / TICK),
                "__feat": rec,
            })
            event_id += 1

    return rows, event_id


# ---------------------------------------------------------------------------
# Per-day prediction cache (loop mode) — completed days never change once
# their bar count stops growing, so their feature build + predict_proba
# result is cached and reused. Only the live (still-growing) day is
# recomputed each cycle. Keyed by rithmic_date_str; invalidated on n_bars or
# confidence change. This is the fix for predictions lagging the live bar
# count: reprocessing all ~64k historical events every cycle (~25s) meant the
# output was always 1-3 bars stale by the time it was written.
# ---------------------------------------------------------------------------
_DAY_PRED_CACHE: Dict[str, Dict[str, Any]] = {}

PRED_COLUMNS = [
    "event_id", "rithmic_date_str", "bar_idx_in_day", "bar_end_ts_ns", "timestamp_utc",
    "contract_symbol", "data_source", "continuous_close", "raw_close",
    "level_type", "level_price_continuous", "level_source", "dist_to_level_ticks",
    "session", "reaction_type", "training_gate_status", "n_training_for_reaction",
    "p_short", "p_long", "confidence", "direction", "model_name",
    "shadow_only", "trading_enabled", "roll_quality_flag",
]


def predict_event_rows(rows: List[Dict[str, Any]], art: Artifacts, confidence: float
                        ) -> Tuple[pd.DataFrame, bool, float, bool]:
    """Build the feature matrix for `rows` and run predict_proba.

    Returns (pred_df without event_id, all_finite, sum_max_dev, feature_order_exact),
    in the same row order as `rows` (caller assigns event_id + sorts globally).
    """
    if not rows:
        return pd.DataFrame(columns=PRED_COLUMNS[1:]), True, 0.0, True

    X = pd.DataFrame([r["__feat"] for r in rows])
    missing = [c for c in art.feature_names if c not in X.columns]
    if missing:
        block(f"required features missing from inference X: {missing}")
    X = X[art.feature_names].copy()
    X.replace([np.inf, -np.inf], np.nan, inplace=True)
    feature_order_exact = list(X.columns) == art.feature_names

    X_arr = X.to_numpy(dtype=float)
    X_imp = art.imputer.transform(X_arr)
    X_scl = art.scaler.transform(X_imp)
    proba = art.model.predict_proba(X_scl)
    p_short = proba[:, 0]
    p_long = proba[:, 1]
    all_finite = bool(np.isfinite(proba).all())
    sum_max_dev = float(np.abs(p_short + p_long - 1.0).max())
    conf = np.maximum(p_short, p_long)
    direction = np.where(
        p_long >= confidence, "LONG",
        np.where(p_short >= confidence, "SHORT", "FLAT"),
    )

    pred_rows = []
    for i, r in enumerate(rows):
        pred_rows.append({
            "rithmic_date_str": r["rithmic_date_str"],
            "bar_idx_in_day": r["bar_idx_in_day"],
            "bar_end_ts_ns": r["bar_end_ts_ns"],
            "timestamp_utc": r["timestamp_utc"],
            "contract_symbol": r["contract_symbol"],
            "data_source": r["data_source"],
            "continuous_close": r["continuous_close"],
            "raw_close": r["raw_close"],
            "level_type": r["level_type"],
            "level_price_continuous": r["level_price_continuous"],
            "level_source": r["level_source"],
            "dist_to_level_ticks": r["dist_to_level_ticks"],
            "session": r["session"],
            "reaction_type": r["reaction_type"],
            "training_gate_status": r["training_gate_status"],
            "n_training_for_reaction": r["n_training_for_reaction"],
            "p_short": float(p_short[i]),
            "p_long": float(p_long[i]),
            "confidence": float(conf[i]),
            "direction": str(direction[i]),
            "model_name": art.model_name,
            "shadow_only": True,
            "trading_enabled": False,
            "roll_quality_flag": ROLL_QUALITY_FLAG,
        })
    return pd.DataFrame(pred_rows), all_finite, sum_max_dev, feature_order_exact


# ---------------------------------------------------------------------------
# One inference cycle
# ---------------------------------------------------------------------------
def run_once(art: Artifacts, master_path: Path, confidence: float, out_dir: Path, service_mode: str = "once") -> Dict[str, Any]:
    t0 = time.time()
    run_utc = utc_now_iso()
    output_json_path = out_dir / "latest_continuous_nq_inference_summary.json"
    prev_output_mtime_utc: Optional[str] = None
    if output_json_path.exists():
        prev_output_mtime_utc = datetime.fromtimestamp(
            output_json_path.stat().st_mtime, tz=timezone.utc
        ).isoformat()
    master_mtime = master_path.stat().st_mtime
    df, master_raw_rows = load_master(master_path)
    dates = sorted(df["rithmic_date_str"].unique())
    master_last = df.iloc[-1]
    master_last_bar_end_ts_ns = int(master_last["bar_end_ts_ns"])
    master_last_timestamp_utc = str(
        master_last.get("timestamp_utc")
        or master_last.get("timestamp")
        or pd.to_datetime(master_last_bar_end_ts_ns, unit="ns", utc=True)
    )

    proj_vp: Optional[Dict[str, Any]] = None
    proj_csv_exists = PROJECTED_LEVELS_CSV.exists()
    if proj_csv_exists:
        proj_vp = projected_vp_from_csv(pd.read_csv(PROJECTED_LEVELS_CSV))

    day_notes: Dict[str, str] = {}
    day_pred_dfs: List[pd.DataFrame] = []
    day_finite: List[bool] = []
    day_dev: List[float] = []
    day_feat_exact: List[bool] = []

    for d in dates:
        day_df = df[df["rithmic_date_str"] == d]
        n_bars = len(day_df)
        contract = str(day_df["contract_symbol"].iloc[0]) if "contract_symbol" in day_df.columns else None

        cached = _DAY_PRED_CACHE.get(d)
        if cached is not None and cached["n_bars"] == n_bars and cached["confidence"] == confidence:
            day_notes[d] = cached["note"]
            day_pred_dfs.append(cached["pred_df"])
            day_finite.append(cached["all_finite"])
            day_dev.append(cached["sum_max_dev"])
            day_feat_exact.append(cached["feature_order_exact"])
            continue

        if n_bars >= art.MIN_BARS:
            try:
                vp = art.volume_profile_levels(day_df)
            except RuntimeError as e:
                day_notes[d] = f"SKIP — volume_profile_levels failed: {e}"
                continue
            rows, _ = process_day(
                day_df, vp, level_source="native", data_source="CONTINUOUS_ADJUSTED_NQ",
                art=art, event_id_start=0,
            )
            note = f"native levels, n_bars={n_bars}, contract={contract}, events={len(rows)}"
        elif contract == "NQU6":
            if proj_vp is None:
                day_notes[d] = (
                    f"SKIP — NQU6 warmup day (n_bars={n_bars} < {art.MIN_BARS}) and "
                    f"projected_levels_csv unavailable at {PROJECTED_LEVELS_CSV}"
                )
                continue
            rows, _ = process_day(
                day_df, proj_vp, level_source="projected_prior_level",
                data_source="PROJECTED_NQM6_TO_NQU6", art=art, event_id_start=0,
            )
            note = (
                f"PROJECTED prior NQM6->NQU6 levels (NOT native NQU6), "
                f"n_bars={n_bars} < {art.MIN_BARS}, events={len(rows)}, "
                f"roll_quality_flag={ROLL_QUALITY_FLAG}"
            )
        else:
            day_notes[d] = f"SKIP — n_bars={n_bars} < {art.MIN_BARS} (insufficient for event generation)"
            continue

        day_pred_df, d_finite, d_dev, d_feat_exact = predict_event_rows(rows, art, confidence)
        day_notes[d] = note
        day_pred_dfs.append(day_pred_df)
        day_finite.append(d_finite)
        day_dev.append(d_dev)
        day_feat_exact.append(d_feat_exact)
        _DAY_PRED_CACHE[d] = {
            "n_bars": n_bars, "confidence": confidence, "pred_df": day_pred_df,
            "note": note, "all_finite": d_finite, "sum_max_dev": d_dev,
            "feature_order_exact": d_feat_exact,
        }

    # --- Assign event_id (cumulative, date order) then sort globally ------ #
    n_total_events = sum(len(d) for d in day_pred_dfs)
    if n_total_events:
        event_id_cursor = 0
        ided_dfs: List[pd.DataFrame] = []
        for day_pred_df in day_pred_dfs:
            n_d = len(day_pred_df)
            if n_d:
                day_pred_df = day_pred_df.copy()
                day_pred_df.insert(0, "event_id", range(event_id_cursor, event_id_cursor + n_d))
                ided_dfs.append(day_pred_df)
            event_id_cursor += n_d
        pred_df = pd.concat(ided_dfs, ignore_index=True)
        # stable sort: same-bar_end_ts_ns ties (multiple levels per bar) keep
        # their (date, emission-order) relative order, so cached days' rows
        # and event_id stay byte-identical across cycles regardless of how
        # many events the live day produces this cycle.
        pred_df = pred_df.sort_values("bar_end_ts_ns", kind="stable").reset_index(drop=True)
        feature_order_exact = all(day_feat_exact)
        all_finite = all(day_finite)
        sum_max_dev = max(day_dev) if day_dev else 0.0
    else:
        pred_df = pd.DataFrame(columns=PRED_COLUMNS)
        feature_order_exact = None
        all_finite = None
        sum_max_dev = None

    # --- Per-reaction-type summary --------------------------------------- #
    if len(pred_df):
        summ_rows = []
        for rxn, sub in pred_df.groupby("reaction_type"):
            latest = sub.sort_values("bar_end_ts_ns").iloc[-1]
            summ_rows.append({
                "reaction_type": rxn,
                "level_source": ",".join(sorted(sub["level_source"].unique())),
                "n": int(len(sub)),
                "training_gate_status": str(sub["training_gate_status"].iloc[0]),
                "n_training_for_reaction": int(sub["n_training_for_reaction"].iloc[0]),
                "mean_p_long": round(float(sub["p_long"].mean()), 4),
                "mean_p_short": round(float(sub["p_short"].mean()), 4),
                "mean_confidence": round(float(sub["confidence"].mean()), 4),
                "pct_conf_ge_threshold": round(100.0 * float((sub["confidence"] >= confidence).mean()), 2),
                "latest_bar_end_ts_ns": int(latest["bar_end_ts_ns"]),
                "latest_timestamp_utc": str(latest["timestamp_utc"]),
                "latest_direction": str(latest["direction"]),
                "latest_confidence": round(float(latest["confidence"]), 4),
                "latest_continuous_close": float(latest["continuous_close"]),
            })
        summary_df = pd.DataFrame(summ_rows).sort_values("n", ascending=False).reset_index(drop=True)
    else:
        summary_df = pd.DataFrame(columns=[
            "reaction_type", "level_source", "n", "training_gate_status", "n_training_for_reaction",
            "mean_p_long", "mean_p_short", "mean_confidence", "pct_conf_ge_threshold",
            "latest_bar_end_ts_ns", "latest_timestamp_utc", "latest_direction",
            "latest_confidence", "latest_continuous_close",
        ])

    # --- Run summary JSON -------------------------------------------------- #
    n_native = int((pred_df["level_source"] == "native").sum()) if len(pred_df) else 0
    n_projected = int((pred_df["level_source"] == "projected_prior_level").sum()) if len(pred_df) else 0
    if len(pred_df):
        pred_last = pred_df.sort_values("bar_end_ts_ns", kind="stable").iloc[-1]
        prediction_last_bar_end_ts_ns: Optional[int] = int(pred_last["bar_end_ts_ns"])
        prediction_last_timestamp_utc: Optional[str] = str(pred_last["timestamp_utc"])
        bar_end_gap_to_master: Optional[int] = int(master_last_bar_end_ts_ns - prediction_last_bar_end_ts_ns)
        master_pos_by_bar = {
            int(ts): int(pos)
            for pos, ts in enumerate(pd.to_numeric(df["bar_end_ts_ns"], errors="coerce").dropna().astype("int64"))
        }
        latest_scored_pos = master_pos_by_bar.get(prediction_last_bar_end_ts_ns)
        latest_scored_event_lag_bars: Optional[int] = (
            int((len(df) - 1) - latest_scored_pos)
            if latest_scored_pos is not None else None
        )
        latest_scored_event_lag_seconds: Optional[float] = (
            float(bar_end_gap_to_master / 1_000_000_000.0)
            if bar_end_gap_to_master is not None else None
        )
        prediction_gap_reason = (
            "matched_latest_master_bar"
            if bar_end_gap_to_master == 0
            else "latest_master_bar_did_not_generate_a_level_reaction_event"
        )
        latest_scored_event_reaction_type: Optional[str] = str(pred_last.get("reaction_type", ""))
        latest_scored_event_confidence_val: Optional[float] = round(float(pred_last.get("confidence", float("nan"))), 4)
        latest_scored_event_direction_str: Optional[str] = str(pred_last.get("direction", ""))
        latest_scored_event_p_long_val: Optional[float] = round(float(pred_last.get("p_long", float("nan"))), 4)
        latest_scored_event_p_short_val: Optional[float] = round(float(pred_last.get("p_short", float("nan"))), 4)
    else:
        prediction_last_bar_end_ts_ns = None
        prediction_last_timestamp_utc = None
        bar_end_gap_to_master = None
        latest_scored_event_lag_bars = None
        latest_scored_event_lag_seconds = None
        prediction_gap_reason = "no_level_reaction_events_detected"
        latest_scored_event_reaction_type = None
        latest_scored_event_confidence_val = None
        latest_scored_event_direction_str = None
        latest_scored_event_p_long_val = None
        latest_scored_event_p_short_val = None
    master_alignment_ok = True
    inference_output_fresh = True
    no_recent_level_reaction_event = bool(
        prediction_last_bar_end_ts_ns is not None
        and bar_end_gap_to_master not in (None, 0)
        and master_alignment_ok
        and inference_output_fresh
    )
    if prediction_last_bar_end_ts_ns is None:
        asof_probability_source = "NO_LEVEL_REACTION_EVENTS"
        asof_probability_sequence_status = "NO_EVENT_PROBABILITY"
    elif no_recent_level_reaction_event:
        asof_probability_source = "HELD_LAST_SCORED_EVENT"
        asof_probability_sequence_status = "IN_SEQUENCE_HELD_NO_NEW_LEVEL_REACTION_EVENT"
    else:
        asof_probability_source = "LIVE_EVENT_ON_DASHBOARD_BAR"
        asof_probability_sequence_status = "IN_SEQUENCE_LIVE_EVENT"

    nqu6_bar_count = int((df.get("contract_symbol", pd.Series(dtype=object)) == "NQU6").sum())
    nqu6_uses_projected = n_projected > 0
    if nqu6_uses_projected:
        low_sample_detail = (
            f"NQU6 has {nqu6_bar_count} bars (< {art.MIN_BARS} required for "
            f"native levels); {n_projected} events this run used projected_prior_level "
            "(old NQM6 levels shifted by the locked roll_gap_points) and are NOT native NQU6 levels."
        )
    else:
        low_sample_detail = (
            f"NQU6 has {nqu6_bar_count} bars. Native NQU6 levels are now "
            f"computable (>= {art.MIN_BARS} bars) but the sample remains small relative to the "
            f"NQM6 history; roll_quality_flag={ROLL_QUALITY_FLAG} remains in effect for this release."
        )

    warning_details = {
        "SHADOW ONLY": "Research monitoring only — not connected to execution.",
        "ROLLOVER WARMUP": "NQU6 contract is in its rollover warmup period "
                           f"(roll_quality_flag={ROLL_QUALITY_FLAG}).",
        "LOW NQU6 SAMPLE": low_sample_detail,
        "NO EXECUTION": "paper_trading_allowed=false, production_execution_allowed=false.",
        "PROJECTED PRIOR LEVELS NOT NATIVE NQU6": (
            "See level_source column (native vs projected_prior_level) and data_source column "
            "(CONTINUOUS_ADJUSTED_NQ vs PROJECTED_NQM6_TO_NQU6). "
            + (f"{n_projected} events in this run used projected prior NQM6 levels."
               if nqu6_uses_projected else
               "No events in this run used projected prior levels.")
        ),
    }

    # --- Current bar event-detector analysis --------------------------------- #
    _cb_ts_ns = master_last_bar_end_ts_ns
    if len(pred_df):
        _cb_mask = pred_df["bar_end_ts_ns"] == _cb_ts_ns
        _cb_df = pred_df[_cb_mask]
        current_bar_event_detected: bool = bool(len(_cb_df) > 0)
        current_bar_candidate_reactions_count: int = int(len(_cb_df))
        current_bar_candidate_reactions: List[str] = sorted(_cb_df["reaction_type"].tolist())
    else:
        current_bar_event_detected = False
        current_bar_candidate_reactions_count = 0
        current_bar_candidate_reactions = []

    current_bar_nearest_level: Optional[str] = None
    current_bar_distance_to_level: Optional[float] = None
    if dates:
        _last_date = dates[-1]
        _last_date_df = df[df["rithmic_date_str"] == _last_date]
        _n_last = len(_last_date_df)
        _last_close: Optional[float] = None
        try:
            _cv = pd.to_numeric(_last_date_df.iloc[-1].get("continuous_close"), errors="coerce")
            if _cv is not None and np.isfinite(float(_cv)):
                _last_close = float(_cv)
        except Exception:
            pass
        _cur_vp: Optional[Dict[str, Any]] = None
        if _n_last >= art.MIN_BARS:
            try:
                _cur_vp = art.volume_profile_levels(_last_date_df)
            except Exception:
                pass
        elif proj_vp is not None:
            _cur_vp = proj_vp
        if _cur_vp is not None and _last_close is not None:
            _all_levels = flat_levels_from_vp(_cur_vp)
            if _all_levels:
                _dists = [(abs(_last_close - lp), lp, lt) for lp, lt in _all_levels]
                _, _near_lp, _near_lt = min(_dists, key=lambda x: x[0])
                current_bar_nearest_level = _near_lt
                current_bar_distance_to_level = round(float((_last_close - _near_lp) / art.TICK), 2)

    if current_bar_event_detected:
        current_bar_event_status = "NEW_LEVEL_REACTION_EVENT_SCORED"
        no_current_event_reason: Optional[str] = None
    elif len(pred_df) == 0:
        current_bar_event_status = "WAITING_FOR_NEXT_EVENT"
        no_current_event_reason = "NO_LEVEL_REACTION_EVENTS_DETECTED_ANY_BAR"
    else:
        current_bar_event_status = "NO_LEVEL_REACTION_EVENT_ON_CURRENT_BAR"
        no_current_event_reason = "CURRENT_BAR_DID_NOT_MEET_LEVEL_REACTION_CRITERIA"

    _prob_source_map = {
        "LIVE_EVENT_ON_DASHBOARD_BAR": "CURRENT_EVENT",
        "HELD_LAST_SCORED_EVENT": "HELD_LAST_SCORED_EVENT",
        "NO_LEVEL_REACTION_EVENTS": "NONE_NO_EVENT",
    }
    probability_source = _prob_source_map.get(asof_probability_source, asof_probability_source)
    current_probability_is_fresh: bool = probability_source == "CURRENT_EVENT"

    summary_json: Dict[str, Any] = {
        "run_utc": run_utc,
        "inference_run_utc": run_utc,
        "shadow_only": True,
        "research_only": True,
        "trading_enabled": False,
        "paper_trading_allowed": False,
        "production_execution_allowed": False,
        "active_dashboard_model": False,
        "paper_trading_enabled": False,
        "production_execution_enabled": False,
        "roll_quality_flag": ROLL_QUALITY_FLAG,
        "master_path": str(master_path),
        "master_mtime_utc": datetime.fromtimestamp(master_mtime, tz=timezone.utc).isoformat(),
        "master_rows_raw": int(master_raw_rows),
        "master_rows": int(master_raw_rows),
        "master_rows_deduped": int(len(df)),
        "master_last_timestamp_utc": master_last_timestamp_utc,
        "master_last_bar_end_ts_ns": master_last_bar_end_ts_ns,
        "dashboard_sequence_ok": True,
        "dashboard_sequence_timestamp_utc": master_last_timestamp_utc,
        "dashboard_sequence_bar_end_ts_ns": master_last_bar_end_ts_ns,
        "master_dates": dates,
        "release_path": str(art.release),
        "release_id": art.release_id,
        "gate_status": art.gate_status,
        "model_name": art.model_name,
        "model_class": art.model_class,
        "model_classes": art.model_classes,
        "feature_count": len(art.feature_names),
        "feature_order_exact": feature_order_exact,
        "predict_proba_all_finite": all_finite,
        "p_short_plus_p_long_max_dev_from_1": sum_max_dev,
        "confidence_threshold": confidence,
        "prediction_rows": int(len(pred_df)),
        "prediction_last_timestamp_utc": prediction_last_timestamp_utc,
        "prediction_last_bar_end_ts_ns": prediction_last_bar_end_ts_ns,
        "latest_scored_event_timestamp_utc": prediction_last_timestamp_utc,
        "latest_scored_event_bar_end_ts_ns": prediction_last_bar_end_ts_ns,
        "latest_scored_event_lag_bars": latest_scored_event_lag_bars,
        "latest_scored_event_lag_seconds": latest_scored_event_lag_seconds,
        "probability_timestamp_utc": master_last_timestamp_utc,
        "probability_bar_end_ts_ns": master_last_bar_end_ts_ns,
        "source_event_timestamp_utc": prediction_last_timestamp_utc,
        "source_event_bar_end_ts_ns": prediction_last_bar_end_ts_ns,
        "asof_probability_timestamp_utc": master_last_timestamp_utc,
        "asof_probability_bar_end_ts_ns": master_last_bar_end_ts_ns,
        "asof_probability_source": asof_probability_source,
        "asof_probability_sequence_status": asof_probability_sequence_status,
        "asof_probability_event_timestamp_utc": prediction_last_timestamp_utc,
        "asof_probability_event_bar_end_ts_ns": prediction_last_bar_end_ts_ns,
        "no_recent_level_reaction_event": no_recent_level_reaction_event,
        "inference_output_fresh": inference_output_fresh,
        "master_alignment_ok": master_alignment_ok,
        "row_gap_to_master": 0,
        "row_gap_to_master_reason": "inference loaded all parseable rows from master_path",
        "bar_end_gap_to_master": bar_end_gap_to_master,
        "bar_end_gap_to_master_reason": prediction_gap_reason,
        "event_count": int(len(pred_df)),
        "event_count_native": n_native,
        "event_count_projected_prior_level": n_projected,
        "projected_levels_csv": str(PROJECTED_LEVELS_CSV),
        "projected_levels_csv_exists": proj_csv_exists,
        "day_notes": day_notes,
        "nqu6_bar_count": nqu6_bar_count,
        "warnings": [
            "SHADOW ONLY",
            "ROLLOVER WARMUP",
            "LOW NQU6 SAMPLE",
            "NO EXECUTION",
            "PROJECTED PRIOR LEVELS NOT NATIVE NQU6",
        ],
        "warning_details": warning_details,
        "outputs": {
            "predictions_csv": str(out_dir / "latest_continuous_nq_predictions.csv"),
            "prediction_summary_csv": str(out_dir / "latest_continuous_nq_prediction_summary.csv"),
            "inference_summary_json": str(out_dir / "latest_continuous_nq_inference_summary.json"),
        },
        "inference_latency_sec": round(float(time.time() - t0), 6),
        "service_mode": service_mode,
        "inference_output_mtime_utc": prev_output_mtime_utc,
        "current_bar_timestamp_utc": master_last_timestamp_utc,
        "current_bar_bar_end_ts_ns": _cb_ts_ns,
        "current_bar_event_detected": current_bar_event_detected,
        "current_bar_candidate_reactions_count": current_bar_candidate_reactions_count,
        "current_bar_candidate_reactions": current_bar_candidate_reactions,
        "current_bar_nearest_level": current_bar_nearest_level,
        "current_bar_distance_to_level": current_bar_distance_to_level,
        "current_bar_event_status": current_bar_event_status,
        "no_current_event_reason": no_current_event_reason,
        "probability_source": probability_source,
        "current_probability_is_fresh": current_probability_is_fresh,
        "latest_scored_event_reaction_type": latest_scored_event_reaction_type,
        "latest_scored_event_confidence": latest_scored_event_confidence_val,
        "latest_scored_event_direction": latest_scored_event_direction_str,
        "latest_scored_event_p_long": latest_scored_event_p_long_val,
        "latest_scored_event_p_short": latest_scored_event_p_short_val,
        "verdict": "PASS" if (all_finite is not False) else "FAIL",
    }

    # --- Write outputs (atomic, flock-protected against concurrent once/loop) #
    out_dir.mkdir(parents=True, exist_ok=True)
    with output_write_lock(out_dir):
        atomic_write_csv(pred_df, out_dir / "latest_continuous_nq_predictions.csv")
        atomic_write_csv(summary_df, out_dir / "latest_continuous_nq_prediction_summary.csv")
        atomic_write_json(summary_json, out_dir / "latest_continuous_nq_inference_summary.json")

    return summary_json


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", type=Path, default=DEFAULT_MASTER)
    ap.add_argument("--release", type=Path, default=DEFAULT_RELEASE)
    ap.add_argument("--model-name", default=MODEL_NAME)
    ap.add_argument("--confidence", type=float, default=DEFAULT_CONFIDENCE)
    ap.add_argument("--out-dir", type=Path, default=OUTDIR)
    ap.add_argument("--mode", choices=["once", "loop"], default="once")
    ap.add_argument("--interval-sec", type=float, default=60.0)
    args = ap.parse_args()

    if not args.master.exists():
        block(f"master not found at {args.master}")

    release_path = resolve_release_path(args.release)
    print(f"[LOAD] release_ref: {args.release}", flush=True)
    print(f"[LOAD] release:     {release_path}", flush=True)
    art = Artifacts(release_path, args.model_name)
    print(f"[LOAD] model:   {art.model_class}  classes_={art.model_classes}  "
          f"n_features={len(art.feature_names)}", flush=True)
    print(f"[OUT]  {args.out_dir}", flush=True)

    if args.mode == "once":
        summary = run_once(art, args.master, args.confidence, args.out_dir, service_mode="once")
        print(json.dumps({k: v for k, v in summary.items() if k not in ("day_notes", "warnings")}, indent=2, default=str))
        print(f"EVENT_COUNT: {summary['event_count']}  "
              f"(native={summary['event_count_native']}, "
              f"projected_prior_level={summary['event_count_projected_prior_level']})")
        print(f"VERDICT: {summary['verdict']}")
        return 0 if summary["verdict"] == "PASS" else 4

    # loop mode
    last_master_sig: Optional[Tuple[int, int, int]] = None
    last_release_path: Optional[Path] = None
    print(f"[LOOP] watching {args.master} every {args.interval_sec}s "
          f"(reload on mtime/row/release-pointer change)", flush=True)
    while True:
        try:
            master_sig = master_signature(args.master)
            release_path = resolve_release_path(args.release)
            if release_path != last_release_path:
                print(f"[{utc_now_iso()}] active release target changed: {release_path}", flush=True)
                art = Artifacts(release_path, args.model_name)
                last_release_path = release_path
                last_master_sig = None
                print(f"[LOAD] model: {art.model_class} release_id={art.release_id} "
                      f"gate_status={art.gate_status}", flush=True)
            if master_sig != last_master_sig:
                summary = run_once(art, args.master, args.confidence, args.out_dir, service_mode="loop")
                last_master_sig = master_sig
                print(f"[{utc_now_iso()}] reprocessed master (mtime changed) — "
                      f"events={summary['event_count']} "
                      f"(native={summary['event_count_native']}, "
                      f"projected={summary['event_count_projected_prior_level']}) "
                      f"rows={summary['master_rows_raw']} "
                      f"bar_gap={summary['bar_end_gap_to_master']} "
                      f"release={summary['release_id']} "
                      f"verdict={summary['verdict']}", flush=True)
        except Exception as e:
            print(f"[{utc_now_iso()}] ERROR during inference cycle: {e}", flush=True)
        time.sleep(args.interval_sec)


if __name__ == "__main__":
    sys.exit(main())
