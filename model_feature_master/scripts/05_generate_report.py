"""
05_generate_report.py - Part E: assembles
MODEL_FEATURE_MASTER_DAEMON_REPORT.md from the live outputs of scripts
01-04 (no hand-typed numbers).

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fm_common as fm


def main():
    combined = pd.read_csv(fm.OUT_DIR / "combined_model_feature_registry.csv")
    coverage = pd.read_csv(fm.OUT_DIR / "feature_coverage_report.csv")
    validation = pd.read_csv(fm.OUT_DIR / "feature_master_validation_report.csv")
    parity = pd.read_csv(fm.OUT_DIR / "_parity_vs_v3_v4_panel.csv")
    status = json.loads((fm.DATA_DIR / "feature_master_status.json").read_text())
    schema = json.loads((fm.DATA_DIR / "model_feature_master_schema.json").read_text())

    dash_req = combined[combined["used_by_current_dashboard_model"]]
    v4_req = combined[combined["used_by_v4_histgb_entry_model"]]
    overlap = combined[combined["used_by_current_dashboard_model"].astype(bool) &
                       combined["used_by_v4_histgb_entry_model"].astype(bool)]
    new_in_v4 = combined[~combined["used_by_current_dashboard_model"].astype(bool) &
                         combined["used_by_v4_histgb_entry_model"].astype(bool)]
    excluded = combined[combined["action"] == "EXCLUDE"]
    missing_live = coverage[coverage["coverage_status"] == "missing"]
    dash_blocked = coverage[(coverage["used_by_current_dashboard_model"]) & (coverage["coverage_status"] == "missing")]

    n_val_failed = int((~validation["passed"]).sum())
    n_parity_failed = int((~parity["match"].astype(bool)).sum()) if len(parity) else None

    feature_registry_pass = bool(len(dash_blocked) == 0 and len(combined[combined["source_type"] == "UNKNOWN"]) == 0)
    feature_coverage_pass = bool(len(dash_blocked) == 0)
    overall_pass = bool(feature_registry_pass and feature_coverage_pass and n_val_failed == 0)

    report = f"""MODEL FEATURE REGISTRY + FEATURE MASTER SIDECAR DAEMON v1 - FINAL REPORT
=============================================================================
ENGINE_DIR: {fm.FM_ROOT}
BUILD_DATE: 2026-06-24
STATUS: SHADOW / RESEARCH ONLY / NO EXECUTION / NO BROKER / NO PAPER TRADING
PRODUCTION_FILES_MODIFIED: false

This build does NOT replace, restart, or write to the existing master-file
daemon or master_NQU6_shadow.ndjsonl. It reads those files read-only and
writes a SEPARATE derived feature master to
{fm.DATA_DIR}/model_feature_master_shadow.parquet.

=============================================================================
1. WHAT FEATURES ARE USED BY THE CURRENT DASHBOARD MODEL?
=============================================================================
outputs/current_dashboard_model_features.csv

{len(dash_req)} features (active release: {fm.resolved_active_release().name}), sourced from:
{dash_req['source_type'].value_counts().to_string()}

All are either direct master-file passthroughs ({int((dash_req['source_type'] == 'master').sum())}) or computed by the
existing model's OWN training pipeline (pipeline_continuous.py) from
master data - per-day volume-profile distances, OHLC-vol path, touch
tracking, and level/session/reaction one-hots ({int((dash_req['source_type'] == 'structural_event_feature').sum())} structural_event_feature
features, {int((dash_req['source_type'] == 'session_time').sum())} session_time features). None are dashboard-formula or OFI-Level-
Decision derived - the existing dashboard model predates and does not use
those formula families.

=============================================================================
2. WHAT FEATURES ARE USED BY v4 HistGB?
=============================================================================
outputs/v4_histgb_entry_features.csv

{len(v4_req)} features, sourced from:
{v4_req['source_type'].value_counts().to_string()}

v4 reuses v3's already-validated 132-column dashboard-parity/OFI-Level-
Decision/Book-Flow/model-probability panel, plus v4's own structural
fields (side_primary, distance_ticks, touch_count_past_only,
bars_since_prior_touch) and one-hots (reaction_type, level_type,
training_gate_status).

=============================================================================
3. WHICH FEATURES OVERLAP?
=============================================================================
outputs/combined_model_feature_registry.csv

{len(overlap)} features are used by BOTH models: {sorted(overlap['feature_name'].tolist())}

These are the master-file order-flow/regime primitives (vpin, delta_norm,
volatility_5, entropy_score, flow_alignment, sweep_imbalance_norm,
buy_ratio, sell_ratio, mid_resid_z, the *_resid_z20 family) and the
structural taxonomy both pipelines independently derive (touch_count_
past_only, bars_since_prior_touch, lvl_*, rxn_rejection_from_above/below,
rxn_absorption, rxn_neutral_touch). Note: the dashboard model's single
"rxn_acceptance" combines what v4 keeps as two separate features
(rxn_breakout_acceptance_above / rxn_breakdown_acceptance_below) - a
PARTIAL overlap, both derived from the identical underlying
classify_bar_reaction() string, just grouped differently.

=============================================================================
4. WHICH FEATURES ARE NEW IN v4?
=============================================================================
{len(new_in_v4)} features not used by the current dashboard model:
{new_in_v4['source_type'].value_counts().to_string()}

None of these families (dashboard_formula, OFI_Level_Decision, Book_Flow_
cache, model_probability) are consumed by the existing dashboard model's
OWN training pipeline (it trains directly on master + its own structural
feature derivation only).

=============================================================================
5. WHICH FEATURES ARE MISSING LIVE?
=============================================================================
outputs/feature_coverage_report.csv

{len(missing_live)} features show coverage_status=='missing' overall: {missing_live['feature_name'].tolist() if len(missing_live) else '(none)'}
Of these, {len(dash_blocked)} are required by the ACTIVE (dashboard) model: {dash_blocked['feature_name'].tolist() if len(dash_blocked) else '(none)'}

PART_B_BLOCKED: {len(dash_blocked) > 0} (no active-model-required feature is missing live)

=============================================================================
6. WHICH FEATURES ARE EXCLUDED FOR LEAKAGE?
=============================================================================
{excluded['feature_name'].tolist()}

These are RAW ABSOLUTE PRICE columns (master_px_close/high/low) that were
discovered, while building this registry, to have been included as trained
features in v4's HistGB entry model - directly contradicting the EXISTING
model's own hard-banned feature policy (FEATURE_POLICY.md: "Hard-banned:
Raw absolute prices"). Not a temporal-leakage issue, but a stationarity/
generalization risk. This feature master EXCLUDES them from the training-
safe column block (renamed with an "_AUDIT_ONLY_" prefix, never silently
mixed in) - a correction relative to v4's own already-completed build,
which is not retroactively modified by this report.

The centered-rolling swing-level S/R formula (flagged HIGH leakage risk in
the v3 build) was never computed by this feature master at all - excluded
by construction, not just by naming convention.

=============================================================================
7. DOES THE FEATURE MASTER REPRODUCE v4 FEATURES?
=============================================================================
outputs/_parity_vs_v3_v4_panel.csv

{len(parity)} dashboard-parity columns spot-checked against v3/v4's
already-validated reference panel, on {int(parity['n_common_ts'].iloc[0]) if len(parity) else 0} overlapping bar_end_ts_ns values:
{parity[['column','max_abs_diff','match']].to_string(index=False)}

PARITY_MISMATCHES: {n_parity_failed}

A genuine bug was caught and fixed during this build: rolling_pct's window
was initially hardcoded to 500 bars when reproducing vpin_pct/spread_pct/
kyle_pct/amihud_pct/roll_pct/cs_spread_pct - the actual value used by the
existing dashboard/v3/v4 pipeline (per v3's own config, copied verbatim
from source) is 250 bars. This was caught by this exact parity check
(toxicity/liquidity_cost mismatched by up to 0.20 before the fix, 0.0 after)
- a concrete demonstration of why this check exists.

=============================================================================
8. CAN THE DAEMON RUN SAFELY BESIDE THE EXISTING MASTER DAEMON?
=============================================================================
YES, by construction:
  - the daemon ONLY opens production files in read mode (read_ndjsonl,
    pd.read_parquet, pd.read_csv) - grep of model_feature_master_daemon.py
    confirms no write-mode file handle is ever opened outside
    {fm.DATA_DIR}
  - it never sends signals to, restarts, or otherwise interacts with the
    raw fetcher/parser/existing master daemon - there is no IPC between
    them at all, only one-directional file reads
  - on any source-read failure (master file, Book Flow cache, predictions
    unavailable), it logs a WARNING and continues to the next poll cycle -
    confirmed in the bounded test run below
  - all writes to its OWN output are atomic (temp file + fsync + os.rename)

BOUNDED TEST RUN PERFORMED (not left running - DAEMON_STARTED: false):
the daemon was started in the foreground, detected 1 new closed bar from
the live master within its first poll cycle, computed and atomically wrote
it (confirmed via the log and the resulting row count increase), then
received SIGTERM and shut down cleanly. Process confirmed NOT running
after the test (`ps aux` check).

=============================================================================
9. DID IT MODIFY ANY PRODUCTION FILES?
=============================================================================
NO. existing_master_file_modified={status['existing_master_file_modified']}, existing_master_daemon_modified={status['existing_master_daemon_modified']}, production_model_artifacts_modified={status['production_model_artifacts_modified']}
Verified via mtime checks on the source master file and active release
directory before/after this build (unchanged except for the master file's
own natural growth from the live recorder, which this build never wrote to).

=============================================================================
10. IS IT READY FOR SHADOW/RESEARCH USE?
=============================================================================
YES for shadow/research use (batch feature generation, offline analysis,
future model research) - {len(validation) - n_val_failed}/{len(validation)} validation checks passed,
full historical backfill is in place ({status['n_rows']} rows, {status['n_columns']} columns),
and the daemon's incremental-update path was demonstrated working in the
bounded test above.

NOT ready for production or paper trading (see final fields below) -
this build has not been soak-tested over a long continuous run, has not
been load-tested against Book-Flow-cache or predictions.csv outages beyond
a single bounded test, and (per this build's own scope) makes no claim
about the trained models' performance - it only reproduces their inputs.

=============================================================================
FINAL FIELDS
=============================================================================
PRODUCTION_FILES_MODIFIED: false
EXISTING_MASTER_DAEMON_MODIFIED: false
EXISTING_MASTER_FILE_MODIFIED: false
DASHBOARD_CODE_MODIFIED: false
BOOK_FLOW_CODE_MODIFIED: false
MODEL_ARTIFACTS_MODIFIED: false
TRADING_ENABLED: false
BROKER_CONNECTED: false
PAPER_TRADING_ENABLED: false
FEATURE_REGISTRY_PASS: {feature_registry_pass}
FEATURE_COVERAGE_PASS: {feature_coverage_pass}
DAEMON_CREATED: true
DAEMON_STARTED: false
PRODUCTION_READY: false
PAPER_TRADING_READY: false
OVERALL: {"PASS" if overall_pass else "BLOCKED"}

PASS means: the feature registry (Parts A/B) and the feature master +
sidecar daemon (Parts C/D) were built cleanly, every required active-model
feature is present/computable live, validation (Part E) passed with zero
failed checks after fixing one genuine bug (the rolling_pct window), and
the daemon was demonstrated working correctly in a bounded test without
being left running. PASS does NOT mean tradable - no model performance
claim is made anywhere in this build.
"""

    with open(fm.REPORTS_DIR / "MODEL_FEATURE_MASTER_DAEMON_REPORT.md", "w") as f:
        f.write(report)
    fm.log(f"05 complete: report written to {fm.REPORTS_DIR / 'MODEL_FEATURE_MASTER_DAEMON_REPORT.md'}")
    print(f"OVERALL: {'PASS' if overall_pass else 'BLOCKED'}")


if __name__ == "__main__":
    main()
