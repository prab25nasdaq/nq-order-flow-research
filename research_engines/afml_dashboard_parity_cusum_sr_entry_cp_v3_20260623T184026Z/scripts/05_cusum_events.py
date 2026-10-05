"""
05_cusum_events.py - Part D: CUSUM event-based sampling (AFML-style).

Builds CUSUM events from the NQU6 continuous bar-close series using
v3_common.cusum_breaks - the EXACT dashboard formula (halflife=64, k=0.35,
h=5.0), unchanged, not reset at session/day boundaries (matches the
dashboard's own continuous accumulation - see Part A catalog). The EWM
standard deviation in the z-score denominator IS the "dynamic volatility
threshold": it adapts every bar, never a fixed point number.

This model does NOT train on every bar - only these event bars become
candidate entries downstream (Part F).

READ-ONLY. SHADOW / RESEARCH ONLY / NO EXECUTION.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v3_common as v3

cfg = v3.load_config()
DF = cfg["dashboard_formulas"]


def main():
    v3.log("05: building CUSUM events on NQU6 close-price log returns (dashboard formula, verbatim)...")
    nqu6 = v3.load_nqu6_master().sort_values("bar_index").reset_index(drop=True)
    close = nqu6["px_close"].to_numpy(dtype=float)
    n = len(close)
    r = np.full(n, np.nan); r[1:] = np.log(close[1:]) - np.log(close[:-1])
    r = np.nan_to_num(r, nan=0.0)

    cb = v3.cusum_breaks(r, halflife=DF["cusum_halflife"], k=DF["cusum_k"], h=DF["cusum_h"])
    is_event = cb["up"] | cb["down"]
    event_idx = np.where(is_event)[0]
    v3.log(f"  {len(event_idx)} CUSUM events out of {n} bars ({100*len(event_idx)/n:.2f}% of bars - confirms NOT training on every bar)")

    events = pd.DataFrame({
        "event_id": np.arange(len(event_idx)),
        "bar_idx": nqu6["bar_index"].to_numpy()[event_idx],
        "bar_end_ts_ns": nqu6["bar_end_ts_ns"].to_numpy()[event_idx],
        "event_time": pd.to_datetime(nqu6["bar_end_ts_ns"].to_numpy()[event_idx], unit="ns", utc=True),
        "day": nqu6["day"].to_numpy()[event_idx],
        "cusum_side": np.where(cb["up"][event_idx], 1, -1),
        "event_magnitude": np.abs(cb["breach_magnitude"][event_idx]),
        "volatility_at_event": cb["ewm_vol"][event_idx],
        "close_at_event": close[event_idx],
        "z_at_event": cb["z"][event_idx],
    })
    events.to_parquet(v3.OUT_DIR / "cusum_events.parquet", index=False)

    # ── diagnostics ──────────────────────────────────────────────────────
    diag_rows = []
    for day, g in events.groupby("day"):
        diag_rows.append(dict(day=int(day), n_events=len(g), n_up=int((g["cusum_side"] == 1).sum()),
                              n_down=int((g["cusum_side"] == -1).sum()),
                              mean_magnitude=float(g["event_magnitude"].mean()),
                              mean_vol_at_event=float(g["volatility_at_event"].mean())))
    diag_rows.append(dict(day="ALL", n_events=len(events), n_up=int((events["cusum_side"] == 1).sum()),
                          n_down=int((events["cusum_side"] == -1).sum()),
                          mean_magnitude=float(events["event_magnitude"].mean()),
                          mean_vol_at_event=float(events["volatility_at_event"].mean())))
    diag_df = pd.DataFrame(diag_rows)
    diag_df.to_csv(v3.OUT_DIR / "cusum_event_diagnostics.csv", index=False)

    # spacing between events (bars) - sanity that this is genuinely event-based, not every-bar
    spacing = np.diff(event_idx)
    v3.log(f"  median bars between events: {np.median(spacing):.1f}  min: {spacing.min()}  max: {spacing.max()}")

    print(f"CUSUM_EVENTS_TOTAL: {len(events)}")
    print(f"CUSUM_EVENTS_UP: {int((events['cusum_side']==1).sum())}")
    print(f"CUSUM_EVENTS_DOWN: {int((events['cusum_side']==-1).sum())}")
    print(f"CUSUM_EVENTS_PCT_OF_BARS: {100*len(event_idx)/n:.4f}")
    print(f"MEDIAN_BARS_BETWEEN_EVENTS: {np.median(spacing):.2f}")
    v3.log("05 complete.")
    return events, diag_df


if __name__ == "__main__":
    main()
