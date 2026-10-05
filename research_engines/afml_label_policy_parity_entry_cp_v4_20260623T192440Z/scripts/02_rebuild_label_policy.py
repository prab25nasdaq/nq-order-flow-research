"""
02_rebuild_label_policy.py - Part B: rebuild the existing model's labeling
method READ-ONLY from the v4 engine's own fresh master snapshot (NOT from
predictions.csv - that file is never used as ground truth here).

Reproduces, line-for-line, pipeline_continuous.py's:
  Stage 2 - volume_profile_levels() + build_level_stream() (per-day POC/VAH/
            VAL/HVN/LVN, continuous price scale)
  Stage 3 - classify_bar_reaction() + build_event_stream() (causal
            level-reaction event detection)
  Stage 5 - build_event_labels() (label_h40 forward-direction, MFE/MAE)

Then compares the rebuild against the existing model's OWN frozen training
artifacts (raw_snapshot/active_release_policy/level_reaction_events.parquet
+ labels_level_reaction.parquet) on the OVERLAPPING date range (the 12 dates
the existing model actually trained on) - this is the legitimate "existing
training labels" comparison the build spec calls for; predictions.csv is
never read in this script.

The rebuild's master snapshot extends 3 days BEYOND the existing model's
training dates (2026-06-21/22/23, post training-cutoff) - this is the
larger candidate/label universe v4 is built to produce.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v4_common as v4

cfg = v4.load_config()


def build_level_stream(master_df: pd.DataFrame, dates: List[str]) -> Dict[str, Dict]:
    per_day_levels: Dict[str, Dict] = {}
    for d in dates:
        df = master_df[master_df["rithmic_date_str"] == d].reset_index(drop=True)
        try:
            vp = v4.volume_profile_levels(df)
        except RuntimeError as e:
            v4.log(f"  {d}: SKIP level stream - {e}")
            continue
        per_day_levels[d] = {
            "poc_px": vp["poc_px"], "vah_px": vp["vah_px"], "val_px": vp["val_px"],
            "hvn_px": vp["hvn_px"], "lvn_px": vp["lvn_px"],
            "n_hvn": len(vp["hvn_px"]), "n_lvn": len(vp["lvn_px"]),
            "value_area_pct": vp["value_area_pct"], "n_bars": int(len(df)),
            "contract_symbol": str(df["contract_symbol"].iloc[0]) if len(df) else None,
        }
    return per_day_levels


def build_event_stream(master_df: pd.DataFrame, dates: List[str],
                        per_day_levels: Dict[str, Dict]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    skipped_low_sample = []
    event_id_counter = 0
    for d in dates:
        if d not in per_day_levels:
            continue
        df = master_df[master_df["rithmic_date_str"] == d].reset_index(drop=True)
        if len(df) < v4.MIN_BARS_PER_DAY_FOR_EVENTS:
            sym = str(df["contract_symbol"].iloc[0]) if len(df) else "?"
            skipped_low_sample.append({"rithmic_date_str": d, "contract_symbol": sym, "n_bars": int(len(df))})
            continue
        lv = per_day_levels[d]
        vt = pd.to_numeric(df["vol_total"], errors="coerce").to_numpy()
        h = pd.to_numeric(df["continuous_high"], errors="coerce").to_numpy()
        l = pd.to_numeric(df["continuous_low"], errors="coerce").to_numpy()
        c = pd.to_numeric(df["continuous_close"], errors="coerce").to_numpy()
        o = pd.to_numeric(df["continuous_open"], errors="coerce").to_numpy()
        raw_c = pd.to_numeric(df["raw_close"], errors="coerce").to_numpy()
        prx_range = np.maximum(h - l, v4.TICK_SIZE)
        absorb_strength = vt / prx_range
        absorb_s = pd.Series(absorb_strength)
        absorb_z = ((absorb_s - absorb_s.rolling(50, min_periods=10).mean())
                    / absorb_s.rolling(50, min_periods=10).std()).to_numpy()
        delta = pd.to_numeric(df["delta_norm"], errors="coerce").to_numpy()
        mid_z = pd.to_numeric(df["mid_resid_z"], errors="coerce").to_numpy()
        prior_close = np.r_[np.nan, c[:-1]]
        contract_symbol = str(df["contract_symbol"].iloc[0])
        source_contract = str(df["source_contract"].iloc[0])
        is_backadj = bool(df["is_backadjusted_history"].iloc[0])
        cum_roll_adj = float(df["cumulative_roll_adjustment_points"].iloc[0])
        roll_quality = str(df["roll_quality_flag"].iloc[0])
        flat_levels: List[Tuple[float, str]] = [
            (lv["poc_px"], "POC"), (lv["vah_px"], "VAH"), (lv["val_px"], "VAL"),
        ] + [(p, "HVN") for p in lv["hvn_px"]] + [(p, "LVN") for p in lv["lvn_px"]]
        touch_counter: Dict[Tuple[float, str], int] = defaultdict(int)
        last_touch_bar: Dict[Tuple[float, str], int] = {}
        for i in range(len(df)):
            for lp, ln in flat_levels:
                rxn = v4.classify_bar_reaction(
                    h[i], l[i], c[i], o[i], lp, ln, delta[i], mid_z[i], absorb_z[i],
                    prior_close[i], touch_counter[(lp, ln)],
                )
                if rxn is None:
                    continue
                tc_past = touch_counter[(lp, ln)]
                bars_since_prior = (i - last_touch_bar[(lp, ln)]
                                     if (lp, ln) in last_touch_bar else -1)
                touch_counter[(lp, ln)] += 1
                last_touch_bar[(lp, ln)] = i
                rows.append({
                    "event_id": event_id_counter,
                    "event_time_ns": int(df["bar_end_ts_ns"].iloc[i]),
                    "bar_end_ts_ns": int(df["bar_end_ts_ns"].iloc[i]),
                    "rithmic_date_str": d, "bar_idx_in_day": i,
                    "session": v4.session_label(int(df["minute_of_day"].iloc[i])),
                    "level_type": ln, "level_price": float(lp), "level_source": "native",
                    "reaction_type": rxn,
                    "distance_ticks_at_event": float((c[i] - lp) / v4.TICK_SIZE),
                    "delta_norm_at_event": float(delta[i]) if np.isfinite(delta[i]) else None,
                    "mid_resid_z_at_event": float(mid_z[i]) if np.isfinite(mid_z[i]) else None,
                    "absorb_z_at_event": float(absorb_z[i]) if np.isfinite(absorb_z[i]) else None,
                    "touch_count_past_only": int(tc_past),
                    "bars_since_prior_touch": int(bars_since_prior),
                    "contract_symbol": contract_symbol, "source_contract": source_contract,
                    "is_backadjusted_history": is_backadj,
                    "cumulative_roll_adjustment_points": cum_roll_adj,
                    "roll_quality_flag": roll_quality,
                    "continuous_close_at_event": float(c[i]) if np.isfinite(c[i]) else None,
                    "raw_close_at_event": float(raw_c[i]) if np.isfinite(raw_c[i]) else None,
                })
                event_id_counter += 1
    if not rows:
        raise RuntimeError("no events generated")
    ev = pd.DataFrame(rows)
    return ev, skipped_low_sample


def build_event_labels(master_df: pd.DataFrame, ev: pd.DataFrame, dates: List[str]) -> pd.DataFrame:
    rows = []
    for d in dates:
        ev_d = ev[ev["rithmic_date_str"] == d]
        if not len(ev_d):
            continue
        src = master_df[master_df["rithmic_date_str"] == d].reset_index(drop=True)
        c = pd.to_numeric(src["continuous_close"], errors="coerce").to_numpy(dtype=float)
        h_ = pd.to_numeric(src["continuous_high"], errors="coerce").to_numpy(dtype=float)
        l_ = pd.to_numeric(src["continuous_low"], errors="coerce").to_numpy(dtype=float)
        lpx = np.log(np.where(np.isfinite(c) & (c > 0), c, np.nan))
        for _, e in ev_d.iterrows():
            i = int(e["bar_idx_in_day"])
            rec = {"event_id": int(e["event_id"]), "rithmic_date_str": d, "bar_idx_in_day": i}
            for h in v4.H_FORWARD:
                j = i + h
                if j < len(lpx) and np.isfinite(lpx[i]) and np.isfinite(lpx[j]):
                    rec[f"fwd_logret_h{h}"] = float(lpx[j] - lpx[i])
                    rec[f"fwd_return_ticks_h{h}"] = float((c[j] - c[i]) / v4.TICK_SIZE)
                else:
                    rec[f"fwd_logret_h{h}"] = np.nan
                    rec[f"fwd_return_ticks_h{h}"] = np.nan
            jmax = min(i + v4.PRIMARY_H + 1, len(c))
            if jmax > i + 1 and np.isfinite(c[i]):
                seg_h = h_[i + 1:jmax]; seg_l = l_[i + 1:jmax]
                if np.isfinite(seg_h).any() and np.isfinite(seg_l).any():
                    rec["MFE_h40"] = float(np.nanmax(seg_h) - c[i])
                    rec["MAE_h40"] = float(np.nanmin(seg_l) - c[i])
                else:
                    rec["MFE_h40"] = np.nan; rec["MAE_h40"] = np.nan
            else:
                rec["MFE_h40"] = np.nan; rec["MAE_h40"] = np.nan
            r40 = rec["fwd_logret_h40"]
            rec["label_h40"] = 1 if (np.isfinite(r40) and r40 > 0) else (0 if (np.isfinite(r40) and r40 < 0) else np.nan)
            rec["hit_up_h40"] = int(np.isfinite(r40) and r40 > 0)
            rec["hit_down_h40"] = int(np.isfinite(r40) and r40 < 0)
            rec["label_end_bar_idx"] = i + v4.PRIMARY_H
            rows.append(rec)
    return pd.DataFrame(rows)


def main():
    v4.log("02: rebuilding existing model's label policy from masters (read-only, NOT predictions.csv)...")
    master = v4.load_continuous_master()
    dates = sorted(master["rithmic_date_str"].unique())
    v4.log(f"  master snapshot covers {len(dates)} dates: {dates}")

    per_day_levels = build_level_stream(master, dates)
    v4.log(f"  level stream built for {len(per_day_levels)} / {len(dates)} dates")

    ev, skipped = build_event_stream(master, dates, per_day_levels)
    v4.log(f"  rebuilt events: {len(ev):,}  (skipped low-sample days: {skipped})")
    ev.to_parquet(v4.OUT_DIR / "rebuilt_label_policy_events.parquet", index=False)

    lbl = build_event_labels(master, ev, dates)
    v4.log(f"  rebuilt labels: {len(lbl):,}  "
           f"LONG={int((lbl['label_h40']==1).sum())}  SHORT={int((lbl['label_h40']==0).sum())}  "
           f"NEUTRAL={int(lbl['label_h40'].isna().sum())}")
    lbl.to_parquet(v4.OUT_DIR / "rebuilt_label_policy_labels.parquet", index=False)

    # ── Parity comparison against the existing model's OWN frozen training
    #    artifacts, restricted to the OVERLAPPING (original training) dates ──
    existing_ev = v4.load_existing_training_events()
    existing_lbl = v4.load_existing_training_labels()
    training_dates = set(v4.load_training_config()["dates_used"])
    v4.log(f"  comparing against existing training artifacts on overlapping dates: {sorted(training_dates)}")

    ev_overlap = ev[ev["rithmic_date_str"].isin(training_dates)].copy()
    existing_ev_overlap = existing_ev[existing_ev["rithmic_date_str"].isin(training_dates)].copy()

    key_cols = ["rithmic_date_str", "bar_idx_in_day", "level_type", "level_price"]
    ev_overlap["_key"] = list(zip(*[ev_overlap[c] for c in key_cols]))
    existing_ev_overlap["_key"] = list(zip(*[existing_ev_overlap[c] for c in key_cols]))

    merged = ev_overlap.merge(
        existing_ev_overlap[["_key", "reaction_type", "event_time_ns", "distance_ticks_at_event"]],
        on="_key", how="inner", suffixes=("_rebuilt", "_existing"),
    )
    n_rebuilt_overlap = len(ev_overlap)
    n_existing_overlap = len(existing_ev_overlap)
    n_matched_keys = len(merged)
    reaction_match = (merged["reaction_type_rebuilt"] == merged["reaction_type_existing"])
    ts_match = (merged["event_time_ns_rebuilt"] == merged["event_time_ns_existing"])

    lbl_overlap = lbl[lbl["event_id"].isin(ev_overlap["event_id"])]
    lbl_merged = lbl_overlap.merge(existing_lbl[["event_id", "label_h40"]],
                                    on="event_id", how="inner", suffixes=("_rebuilt", "_existing"))
    label_match = (lbl_merged["label_h40_rebuilt"] == lbl_merged["label_h40_existing"]) | \
                  (lbl_merged["label_h40_rebuilt"].isna() & lbl_merged["label_h40_existing"].isna())
    side_rebuilt = lbl_merged["label_h40_rebuilt"].map({1: 1, 0: -1})
    side_existing = lbl_merged["label_h40_existing"].map({1: 1, 0: -1})
    side_match = (side_rebuilt == side_existing) | (side_rebuilt.isna() & side_existing.isna())

    mismatch_examples = merged.loc[~reaction_match, key_cols + ["reaction_type_rebuilt", "reaction_type_existing"]].head(10)

    parity_rows = [
        dict(check="row_count_rebuilt_on_training_dates", value=n_rebuilt_overlap),
        dict(check="row_count_existing_on_training_dates", value=n_existing_overlap),
        dict(check="row_count_delta", value=n_rebuilt_overlap - n_existing_overlap),
        dict(check="n_matched_on_key_(date,bar_idx,level_type,level_price)", value=n_matched_keys),
        dict(check="pct_matched_keys_vs_rebuilt", value=round(100 * n_matched_keys / max(n_rebuilt_overlap, 1), 3)),
        dict(check="reaction_type_match_rate_among_matched_keys", value=round(float(reaction_match.mean()), 6) if len(merged) else np.nan),
        dict(check="event_timestamp_match_rate_among_matched_keys", value=round(float(ts_match.mean()), 6) if len(merged) else np.nan),
        dict(check="n_label_rows_matched_(date,bar_idx)", value=len(lbl_merged)),
        dict(check="label_h40_match_rate", value=round(float(label_match.mean()), 6) if len(lbl_merged) else np.nan),
        dict(check="side_match_rate", value=round(float(side_match.mean()), 6) if len(lbl_merged) else np.nan),
        dict(check="n_mismatch_examples_captured", value=len(mismatch_examples)),
        dict(check="rebuilt_total_events_all_dates_incl_post_cutoff", value=len(ev)),
        dict(check="rebuilt_dates_beyond_existing_training_window",
             value=str(sorted(set(dates) - training_dates))),
    ]
    parity_df = pd.DataFrame(parity_rows)
    parity_df.to_csv(v4.OUT_DIR / "label_policy_parity_report.csv", index=False)
    if len(mismatch_examples):
        mismatch_examples.to_csv(v4.OUT_DIR / "_label_policy_parity_mismatch_examples.csv", index=False)

    reaction_ok = (len(merged) == 0) or (float(reaction_match.mean()) >= 0.995)
    label_ok = (len(lbl_merged) == 0) or (float(label_match.mean()) >= 0.995)
    coverage_ok = n_matched_keys >= 0.95 * n_rebuilt_overlap
    parity_pass = bool(reaction_ok and label_ok and coverage_ok)

    print(parity_df.to_string(index=False))
    print(f"\nLABEL_POLICY_PARITY_PASS: {parity_pass}")
    if not parity_pass:
        print("BLOCKED: label policy rebuild does not match existing training artifacts closely enough.")
    v4.log(f"02 complete. PARITY_PASS={parity_pass}")
    return ev, lbl, parity_df, parity_pass


if __name__ == "__main__":
    main()
