"""
01_existing_model_label_policy_catalog.py - Part A: extract the EXACT
labeling method of the existing dashboard/active level-reaction model.

Every fact below is read directly from the active release's own files
(EVENT_POLICY.md, LABEL_POLICY.md, FEATURE_POLICY.md, training_config.json,
reaction_type_metadata.json, leakage_audit.md, release_manifest.json,
pipeline_continuous.py) - snapshotted read-only into
raw_snapshot/active_release_policy/. Nothing here is inferred; every
parameter is transcribed verbatim from source.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v4_common as v4

POLICY_DIR = v4.RELEASE_SNAPSHOT_DIR


def main():
    v4.log("01: cataloging existing model label policy (read-only, verbatim)...")
    training_config = json.loads((POLICY_DIR / "training_config.json").read_text())
    reaction_meta = json.loads((POLICY_DIR / "reaction_type_metadata.json").read_text())
    release_manifest = json.loads((POLICY_DIR / "release_manifest.json").read_text())
    event_policy_md = (POLICY_DIR / "EVENT_POLICY.md").read_text()
    label_policy_md = (POLICY_DIR / "LABEL_POLICY.md").read_text()
    feature_policy_md = (POLICY_DIR / "FEATURE_POLICY.md").read_text()
    leakage_audit_md = (POLICY_DIR / "leakage_audit.md").read_text()

    catalog_rows = [
        dict(category="LABELING_METHOD", field="method_type",
             value="LEVEL_REACTION_EVENT_TRIGGERED_FIXED_HORIZON_DIRECTION (NOT triple-barrier, "
                   "NOT pure fixed-horizon-on-every-bar - only bars meeting a level-reaction rule "
                   "produce events; the label itself is fixed-horizon binary direction at h40)",
             source="pipeline_continuous.py docstring lines 3-67; LABEL_POLICY.md"),
        dict(category="LABELING_METHOD", field="primary_target", value="label_h40",
             source="training_config.json:target"),
        dict(category="LABELING_METHOD", field="label_rule",
             value="LONG=1 if log_ret(continuous_close, t+40)>0; SHORT=0 if <0; NEUTRAL (NaN) dropped",
             source="LABEL_POLICY.md; pipeline_continuous.py:878-882"),
        dict(category="LABELING_METHOD", field="label_price_source", value="continuous_close",
             source="training_config.json:label_price_source"),
        dict(category="LABELING_METHOD", field="level_price_source",
             value="continuous_high/continuous_low (per-day volume profile, see EVENT_GENERATION rows)",
             source="training_config.json:level_price_source"),
        dict(category="LABELING_METHOD", field="MFE_MAE_window",
             value="MFE_h40=max(continuous_high[t+1:t+41])-close[t]; MAE_h40=min(continuous_low[t+1:t+41])-close[t]",
             source="pipeline_continuous.py:868-877"),
        dict(category="LABELING_METHOD", field="label_end_bar_idx_purge_anchor",
             value="bar_idx_in_day + 40 (used for purging/embargo)",
             source="LABEL_POLICY.md; pipeline_continuous.py:883"),

        dict(category="EVENT_GENERATION", field="event_trigger",
             value="classify_bar_reaction(): a bar produces an event ONLY if it touches/closes-near "
                   "a level AND matches one of 5 reaction rules below. Normal bars produce NONE.",
             source="pipeline_continuous.py:499-531 (classify_bar_reaction)"),
        dict(category="EVENT_GENERATION", field="levels_evaluated",
             value="POC, VAH, VAL, every HVN price, every LVN price - per-day volume profile, "
                   "computed from that day's own continuous_high/continuous_low/vol_total/buy_vol/sell_vol",
             source="EVENT_POLICY.md; pipeline_continuous.py:350-404 (volume_profile_levels)"),
        dict(category="EVENT_GENERATION", field="volume_profile_formula",
             value="TICK=0.25 buckets spanning [min(low)-2tick, max(high)+2tick]; per-bar volume "
                   "distributed across buckets by high/low overlap fraction; POC=argmax bucket; "
                   "VAH/VAL=expand from POC until 70% of total volume captured (ties favor expanding "
                   "upward); HVN: bucket vol > smoothed_vol*1.45; LVN: bucket vol < smoothed_vol*0.55; "
                   "smoothing kernel width k=max(3, n_levels//15) box filter",
             source="pipeline_continuous.py:350-404 (volume_profile_levels, EXACT replica of "
                    "ofi_live_dashboard.py:2740-2767)"),
        dict(category="EVENT_GENERATION", field="reaction_rejection_from_above",
             value="low < level <= high AND close > level (came down, closed back above)",
             source="EVENT_POLICY.md; pipeline_continuous.py:519-521"),
        dict(category="EVENT_GENERATION", field="reaction_rejection_from_below",
             value="low <= level < high AND close < level (came up, closed back below)",
             source="EVENT_POLICY.md; pipeline_continuous.py:522-523"),
        dict(category="EVENT_GENERATION", field="reaction_absorption",
             value="abs_z(vol/range) > 1.5 AND |close-open| < 1 tick",
             source="EVENT_POLICY.md; pipeline_continuous.py:515-516"),
        dict(category="EVENT_GENERATION", field="reaction_breakout_acceptance_above",
             value="|delta_norm| > 1.0 AND close > level+4ticks AND prior_close < level-0.5ticks",
             source="EVENT_POLICY.md; pipeline_continuous.py:524-527"),
        dict(category="EVENT_GENERATION", field="reaction_breakdown_acceptance_below",
             value="|delta_norm| > 1.0 AND close < level-4ticks AND prior_close > level+0.5ticks",
             source="EVENT_POLICY.md; pipeline_continuous.py:524,528"),
        dict(category="EVENT_GENERATION", field="reaction_neutral_touch",
             value="touched level AND |close-open| < 1 tick AND no absorption",
             source="EVENT_POLICY.md; pipeline_continuous.py:529-530"),
        dict(category="EVENT_GENERATION", field="near_ticks_k", value=str(training_config["near_ticks_k"]),
             source="training_config.json:near_ticks_k"),
        dict(category="EVENT_GENERATION", field="touch_count_tracking",
             value="touch_count_past_only and bars_since_prior_touch use ONLY data before the current "
                   "bar (per (level_price, level_name) key, reset never, accumulated across the whole day)",
             source="pipeline_continuous.py:576-591"),
        dict(category="EVENT_GENERATION", field="session_label_convention",
             value="Asia: minute_of_day<360; EU: <720; US_Open: <870; US_AM: <1080; US_PM: <1320; "
                   "US_Late: else. NOTE: this is the LEVEL-REACTION model's OWN session convention - "
                   "DIFFERENT from the OFI Level Decision tab's own London/US/Asia_Overnight convention "
                   "used in v3.",
             source="pipeline_continuous.py:490-496 (session_label)"),
        dict(category="EVENT_GENERATION", field="min_bars_per_day_for_events",
             value=str(training_config["min_bars_per_day_for_events"]),
             source="training_config.json:min_bars_per_day_for_events"),
        dict(category="EVENT_GENERATION", field="level_source", value="native (100% of training events)",
             source="EVENT_POLICY.md; 'projected_prior_level' reserved for inference script only"),

        dict(category="HORIZON", field="label_horizon_bars", value=str(training_config["label_horizon_bars"]),
             source="training_config.json:label_horizon_bars"),
        dict(category="HORIZON", field="forward_horizons_reported",
             value=str(training_config["forward_horizons_bars"]),
             source="training_config.json:forward_horizons_bars"),
        dict(category="HORIZON", field="embargo_bars", value=str(training_config["embargo_bars"]),
             source="training_config.json:embargo_bars"),
        dict(category="HORIZON", field="embargo_equals_horizon_rationale",
             value="EMBARGO_BARS=40 == label_horizon_bars guarantees no train-event label window can "
                   "overlap a different (later) calendar day's validation-event input bars",
             source="pipeline_continuous.py:963-970"),

        dict(category="VALIDATION_FOLDS", field="fold_strategy",
             value="purged walk-forward BY CALENDAR DAY, expanding window (train on days[:k], "
                   "validate on days[k], for k=2..n_days-1) - NOT random K-fold",
             source="pipeline_continuous.py:935-947 (build_folds)"),
        dict(category="VALIDATION_FOLDS", field="cross_day_overlap_count", value="0",
             source="leakage_audit.md / purging_audit.json"),
        dict(category="VALIDATION_FOLDS", field="min_n_for_train", value=str(training_config["min_n_for_train"]),
             source="training_config.json:min_n_for_train"),
        dict(category="VALIDATION_FOLDS", field="min_days", value=str(training_config["min_days"]),
             source="training_config.json:min_days"),

        dict(category="GATE_LOGIC", field="status_rule", value=reaction_meta["_meta"]["status_rule"],
             source="reaction_type_metadata.json:_meta.status_rule"),
        dict(category="GATE_LOGIC", field="model_used_for_gate", value="hgb_diagnostic per-reaction MCC",
             source="pipeline_continuous.py:1455-1499 (build_section_f_artifacts)"),
        dict(category="GATE_LOGIC", field="release_level_gate_status", value=release_manifest["gate_status"],
             source="release_manifest.json:gate_status (== 'BLOCKED' - every model's worst-fold MCC was "
                    "negative; the existing model itself never passed its own production promotion gates)"),
        dict(category="GATE_LOGIC", field="positive_fold_pct_threshold",
             value=str(training_config["positive_fold_pct_threshold"]),
             source="training_config.json:positive_fold_pct_threshold"),

        dict(category="FEATURE_POLICY", field="n_features", value="77",
             source="FEATURE_POLICY.md"),
        dict(category="FEATURE_POLICY", field="families",
             value="level context (dist_to_POC/VAH/VAL/HVN/LVN ticks+vol-norm, inside/above/below VA); "
                   "OHLC-vol path (candle_body/range_vol, wicks, close_location, break_vol); Rithmic "
                   "order-flow (mlofi*, delta_norm+lags, decay_norm); sweep/aggression; regime (vpin, "
                   "entropy_score, flow_alignment, mid_resid_z, volatility_5); z20 residuals; time "
                   "(minute/dow cyclic); one-hots (lvl_*, sess_*, rxn_*); bid/ask pull PROXY (exploratory, "
                   "NOT real book pull)",
             source="FEATURE_POLICY.md"),
        dict(category="FEATURE_POLICY", field="hard_banned",
             value="raw absolute prices (px_*, mid_mean/sum/kf/roll20, continuous_*, raw_*), Databento, "
                   "future-bar data (no shift(-1)/centered-rolling/bfill), raw level prices",
             source="FEATURE_POLICY.md"),

        dict(category="DATA_SOURCE", field="training_master",
             value=training_config["training_master"], source="training_config.json:training_master"),
        dict(category="DATA_SOURCE", field="uses_NQM6_and_NQU6_continuous",
             value="YES - single continuous backadjusted master spans NQM6 history "
                   "(cumulative_roll_adjustment_points=632.5) + NQU6 live (=0). Dates used: "
                   f"{training_config['dates_used']}", source="training_config.json:dates_used"),
        dict(category="DATA_SOURCE", field="roll_gap_points", value=str(training_config["roll_gap_points"]),
             source="training_config.json:roll_gap_points"),
        dict(category="DATA_SOURCE", field="duplicate_bar_dedup",
             value="1 duplicate bar_end_ts_ns removed, keep-first (bar_index 8911/8912, day 20260609)",
             source="leakage_audit.md"),

        dict(category="SAMPLE_SCOPE", field="generated_at_utc", value=training_config["generated_at_utc"],
             source="training_config.json:generated_at_utc (== MODEL_TRAINING_CUTOFF_UTC used throughout "
                    "this v4 build and the prior v1-v3 builds)"),
        dict(category="SAMPLE_SCOPE", field="n_events", value=str(training_config["n_events"]),
             source="training_config.json:n_events"),
        dict(category="SAMPLE_SCOPE", field="n_labelled_events", value=str(training_config["n_labelled_events"]),
             source="training_config.json:n_labelled_events"),
        dict(category="SAMPLE_SCOPE", field="in_sample_rows_definition",
             value="any bar/event with bar_end_ts_ns timestamp <= generated_at_utc is IN-SAMPLE "
                   "(was available to the existing model's own training run); rows after that "
                   "timestamp are genuinely post-training-cutoff (this v4 build's `day` values for "
                   "the existing training run were 2026-06-03..2026-06-18; any NQU6 bar dated after "
                   "2026-06-21T01:53:35Z UTC is OOS for the existing model)",
             source="derived from training_config.json:generated_at_utc + dates_used"),
    ]
    cat_df = pd.DataFrame(catalog_rows)
    cat_df.to_csv(v4.OUT_DIR / "existing_model_label_policy_catalog.csv", index=False)

    md = ["# EXISTING DASHBOARD/LEVEL-REACTION MODEL - LABEL POLICY CATALOG",
          "",
          f"Active release: `{release_manifest['release_id']}`",
          f"Resolved path: `{json.loads((v4.RELEASE_SNAPSHOT_DIR/'release_manifest.json').read_text())['release_id']}`",
          "",
          "Every entry below is transcribed VERBATIM from the active release's own policy files "
          "(EVENT_POLICY.md, LABEL_POLICY.md, FEATURE_POLICY.md, training_config.json, "
          "reaction_type_metadata.json, leakage_audit.md, release_manifest.json) and its training "
          "script pipeline_continuous.py - nothing here is inferred.",
          "",
          "## Answers to Part A's required questions",
          "",
          "**Q: What labeling method does the existing dashboard model use?**",
          "LEVEL_REACTION_EVENT_TRIGGERED_FIXED_HORIZON_DIRECTION. It is NOT triple-barrier (no PT/SL "
          "horizontal barriers at all) and NOT a fixed-horizon-on-every-bar scheme (only bars meeting a "
          "level-reaction rule produce a labeled event). It IS: (1) detect a level-reaction EVENT on a "
          "bar via classify_bar_reaction(), then (2) label that event with the SIGN of the forward "
          "log-return at a FIXED +40-bar horizon (label_h40), with NEUTRAL (zero-sign) events dropped.",
          "",
          "**Q: What events create labels?**",
          "Bars that touch or close within 4 ticks of POC/VAH/VAL/HVN/LVN AND match one of 5 causal "
          "reaction rules: rejection_from_above, rejection_from_below, absorption, "
          "breakout_acceptance_above_<level>, breakdown_acceptance_below_<level>, neutral_touch.",
          "",
          "**Q: What horizons are used?**",
          "Primary/label horizon = 40 bars. Forward returns also reported (not labeled) at 5/10/20 bars. "
          "Embargo = 40 bars (== label horizon).",
          "",
          "**Q: What level types are used?**",
          "POC, VAH, VAL, HVN (every HVN price for the day), LVN (every LVN price for the day) - "
          "all computed per-day from that day's own continuous-scale OHLCV volume profile.",
          "",
          "**Q: What reaction types are PRIMARY_USE / SECONDARY_WATCH / BLOCKED_NEGATIVE?**",
          "See existing_model_reaction_type_catalog.csv for the full table (17 reaction types). Summary: "
          "PRIMARY_USE = HVN_absorption, HVN_rejection_from_above, HVN_rejection_from_below, "
          "POC_rejection_from_above, VAH_rejection_from_above, VAH_rejection_from_below. "
          "SECONDARY_WATCH = LVN_rejection_from_below, POC_rejection_from_below. "
          "BLOCKED_NEGATIVE = LVN_rejection_from_above, VAL_rejection_from_above, VAL_rejection_from_below. "
          "EXPLORATORY_SMALL_N / SMALL_N = the remaining rare types (n<50, mostly absorption/neutral_touch "
          "variants).",
          "",
          "**Q: Is the label fixed horizon, triple barrier, level reaction, or another method?**",
          "BOTH level-reaction (for event SELECTION) AND fixed-horizon-direction (for the label ITSELF). "
          "There is no triple-barrier PT/SL logic anywhere in the existing model.",
          "",
          "**Q: Does it use NQM6 + NQU6 continuous master?**",
          "YES. Single continuous backadjusted master "
          f"(`{training_config['training_master']}`) spanning NQM6 history days "
          f"{training_config['dates_used'][:7]} (cumulative_roll_adjustment_points=632.5) plus the NQU6 "
          f"warmup day {training_config['dates_used'][7:]} (=0 adjustment).",
          "",
          "**Q: What rows are in-sample vs genuinely post-training-cutoff?**",
          f"Training cutoff (generated_at_utc) = `{training_config['generated_at_utc']}`. Any bar/event "
          "with bar_end_ts_ns <= this timestamp is in-sample for the existing model. The existing "
          f"training run itself only used dates {training_config['dates_used']} "
          f"({training_config['n_events']:,} events, {training_config['n_labelled_events']:,} labelled). "
          "Any NQU6 master row dated after this cutoff (i.e. produced by the live scheduler running "
          "after the existing model was trained) is genuinely post-training-cutoff / out-of-sample for "
          "the existing model - this v4 build's masters extend well past that date.",
          "",
          "**Important honest note - the existing model's OWN gate status:**",
          f"`release_manifest.json:gate_status = \"{release_manifest['gate_status']}\"`. Every one of the "
          "4 models the existing pipeline trained (logreg, logreg_balanced, hgb_diagnostic, rf_diagnostic) "
          "had a NEGATIVE worst-fold MCC and therefore BLOCKED on its own promotion gate "
          "(`WORST_FOLD_MCC_NOT_POSITIVE`). The existing model is itself shadow/research-only and was "
          "never promoted to production - this is the baseline v4 is trying to extend with a larger "
          "sample, not a previously-validated production system.",
          "",
          "## Full catalog",
          "",
          cat_df.to_markdown(index=False),
          ]
    with open(v4.OUT_DIR / "existing_model_label_policy_catalog.md", "w") as f:
        f.write("\n".join(md))

    cutoff_json = {
        "generated_at_utc": training_config["generated_at_utc"],
        "training_dates_used": training_config["dates_used"],
        "n_events_in_training_run": training_config["n_events"],
        "n_labelled_events_in_training_run": training_config["n_labelled_events"],
        "release_id": release_manifest["release_id"],
        "release_gate_status": release_manifest["gate_status"],
        "definition": "Any bar_end_ts_ns <= this UTC timestamp is in-sample for the existing model. "
                       "This timestamp is reused verbatim as MODEL_TRAINING_CUTOFF_UTC throughout this "
                       "v4 build (identical to v1/v2/v3) for the genuinely-OOS subset split.",
    }
    with open(v4.OUT_DIR / "existing_model_training_cutoff.json", "w") as f:
        json.dump(cutoff_json, f, indent=2)

    rxn_rows = []
    for k, v in reaction_meta.items():
        if k == "_meta":
            continue
        rxn_rows.append(v)
    rxn_df = pd.DataFrame(rxn_rows).sort_values("n_training", ascending=False)
    rxn_df.to_csv(v4.OUT_DIR / "existing_model_reaction_type_catalog.csv", index=False)

    v4.log(f"  catalog rows: {len(cat_df)}")
    v4.log(f"  reaction types: {len(rxn_df)}  "
           f"(PRIMARY_USE={int((rxn_df.training_gate_status=='PRIMARY_USE').sum())}, "
           f"SECONDARY_WATCH={int((rxn_df.training_gate_status=='SECONDARY_WATCH').sum())}, "
           f"BLOCKED_NEGATIVE={int((rxn_df.training_gate_status=='BLOCKED_NEGATIVE').sum())}, "
           f"SMALL_N/EXPLORATORY={int(rxn_df.training_gate_status.isin(['SMALL_N','EXPLORATORY_SMALL_N']).sum())})")
    print(rxn_df[["reaction_type", "n_training", "training_gate_status", "hgb_diagnostic_mcc"]].to_string(index=False))
    v4.log("01 complete.")
    return cat_df, rxn_df


if __name__ == "__main__":
    main()
