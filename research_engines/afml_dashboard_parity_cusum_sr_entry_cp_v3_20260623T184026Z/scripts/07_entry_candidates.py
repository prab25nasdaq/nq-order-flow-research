"""
07_entry_candidates.py - Part F: primary side + entry candidate labeling.

Candidate side is generated PURELY from CUSUM event + S/R structural context
(Parts D/E) - never from model probabilities, which are attached only as
FEATURES (per build spec: stale HELD_LAST probabilities must not create a
candidate by themselves; this script never even looks at them when deciding
side/candidacy):

  support_or_resistance == "support"    -> LONG candidate structurally allowed
  support_or_resistance == "resistance" -> SHORT candidate structurally allowed
  not is_near_valid_sr                  -> NO_CANDIDATE

This mirrors EXACTLY ofi_level_decision_tab.py's own is_near_support/
is_near_resistance convention (Part B) - not a new rule, the same structural
definition already used elsewhere in this codebase.

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v3_common as v3

cfg = v3.load_config()


def main():
    v3.log("07: building entry candidate labels (CUSUM + S/R structural side)...")
    cand = pd.read_parquet(v3.OUT_DIR / "sr_gated_cusum_candidates.parquet")
    master_panel = pd.read_parquet(v3.OUT_DIR / "master_dashboard_ofi_feature_panel.parquet")

    side = np.where(
        ~cand["is_near_valid_sr"], 0,
        np.where(cand["support_or_resistance"] == "support", 1,
                np.where(cand["support_or_resistance"] == "resistance", -1, 0)),
    )
    cand["side_primary"] = side
    cand["side_source"] = "cusum_sr_structural"
    cand["candidate_label"] = np.where(side == 1, "LONG", np.where(side == -1, "SHORT", "NO_CANDIDATE"))

    # attach ALL t0-or-earlier features from the master panel (model probs,
    # dashboard-parity, OFI Level Decision, book-flow) by bar_end_ts_ns
    feature_cols = [c for c in master_panel.columns if c not in
                    ("bar_index", "bar_end_ts_ns", "day", "timestamp_utc")]
    candidates = cand.merge(master_panel[["bar_end_ts_ns"] + feature_cols], on="bar_end_ts_ns", how="left")

    # stale-HELD guard check (documentation/verification, not a filter - candidacy
    # was already decided above without reference to modelprob_* at all)
    stale_held_only = (
        (candidates["modelprob_probability_source"] == "HELD_LAST_SCORED_EVENT")
        & (candidates["modelprob_bars_since_last_event"] > 5)
    )
    n_candidates_with_stale_held = int((stale_held_only & (candidates["side_primary"] != 0)).sum())

    candidates.to_parquet(v3.OUT_DIR / "candidate_events_entry_v3.parquet", index=False)

    n_total = len(candidates)
    n_long = int((candidates["side_primary"] == 1).sum())
    n_short = int((candidates["side_primary"] == -1).sum())
    n_none = int((candidates["side_primary"] == 0).sum())

    v3.log(f"  {n_total} CUSUM events -> LONG={n_long} SHORT={n_short} NO_CANDIDATE={n_none}")
    v3.log(f"  candidates whose modelprob feature happens to be stale-HELD (informational only, "
          f"never used to create/deny candidacy): {n_candidates_with_stale_held}")

    print(f"ENTRY_CANDIDATES_TOTAL: {n_total}")
    print(f"ENTRY_CANDIDATES_LONG: {n_long}")
    print(f"ENTRY_CANDIDATES_SHORT: {n_short}")
    print(f"ENTRY_CANDIDATES_NO_CANDIDATE: {n_none}")
    print(f"CANDIDATES_WITH_STALE_HELD_MODELPROB_FEATURE: {n_candidates_with_stale_held}")
    v3.log("07 complete.")
    return candidates


if __name__ == "__main__":
    main()
