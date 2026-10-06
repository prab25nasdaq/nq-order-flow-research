#!/usr/bin/env python3
"""
MISSION agent-replay: PositionTracker's execution semantics are HAND-MIRRORED from
strategy_lab._simulate_trades, not shared code -- that function assumes the whole signal is known
in advance and simulates the entire window in one vectorized pass, which a genuinely sequential,
one-decision-at-a-time agent cannot use directly. Hand-mirrored code can silently drift from what
it was mirrored from. If PositionTracker's fills/stops/targets/P&L ever disagree with
_simulate_trades given the IDENTICAL signal and exit rules, the agent's results and the
benchmarks stop being comparable -- and nothing else would catch that; this is the only thing
that checks it.

Gate: run a fixed, real (not synthetic) signal through both paths on a clean (duplicate-free,
bar_index >= 11960 -- see CLAUDE.md's data-integrity note) slice of the real dataset, across
three spec shapes (long with both stop+target, short with only a stop, a pure hold-bars exit with
neither), and assert every resulting trade is IDENTICAL: same entry/exit bar, same entry/exit
price, same pnl_points, same hold_bars, same exit_reason. Raw pnl_points only (before
apply_trading_costs) -- that's a separate, already-independently-tested post-processing step, not
part of what this gate is checking.
"""
import sys

sys.path.insert(0, "..")
from server import agent_replay as ar
from server import strategy_lab as sl


def _mirror_via_position_tracker(df, signal, entry_side, hold_bars, stop_points, target_points):
    """Replicates _simulate_trades's own scan (entry_idx = i+1, exit_idx = min(entry_idx+hold_bars,
    n-1), non-overlapping via i = exit_idx+1) but executes fills/stops/targets through
    PositionTracker instead of _simulate_trades's inline arithmetic."""
    n = len(df)
    opens = df["continuous_open"].to_numpy(dtype=float)
    highs = df["continuous_high"].to_numpy(dtype=float)
    lows = df["continuous_low"].to_numpy(dtype=float)
    closes = df["continuous_close"].to_numpy(dtype=float)
    tracker = ar.PositionTracker()

    i = 0
    while i < n - 1:
        if not signal[i]:
            i += 1
            continue
        entry_idx = i + 1
        if entry_idx >= n:
            break
        entry_px = opens[entry_idx]
        exit_idx = min(entry_idx + hold_bars, n - 1)

        tracker.open(entry_side, entry_bar=entry_idx, entry_px=entry_px,
                     stop_points=stop_points, target_points=target_points)
        closed = None
        for j in range(entry_idx, exit_idx + 1):
            closed = tracker.check_bar(j, highs[j], lows[j])
            if closed is not None:
                break
        if closed is None:
            closed = tracker.close_at_market(exit_idx, closes[exit_idx], reason="hold_bars")

        i = closed.exit_bar + 1

    return [
        {"entry_bar": t.entry_bar, "exit_bar": t.exit_bar, "entry_px": t.entry_px,
         "exit_px": t.exit_px, "pnl_points": t.pnl_points, "hold_bars": t.hold_bars,
         "exit_reason": t.exit_reason}
        for t in tracker.trades
    ]


def _via_simulate_trades(df, signal, entry_side, hold_bars, stop_points, target_points):
    spec = {"entry_side": entry_side, "exit_hold_bars": hold_bars,
            "stop_points": stop_points, "target_points": target_points}
    trades = sl._simulate_trades(df, spec, signal_override=signal)
    return [
        {"entry_bar": t["entry_bar"], "exit_bar": t["exit_bar"], "entry_px": t["entry_px"],
         "exit_px": t["exit_px"], "pnl_points": t["pnl_points"], "hold_bars": t["hold_bars"],
         "exit_reason": t["exit_reason"]}
        for t in trades
    ]


def _bar_index_to_offset(trades, df):
    """_simulate_trades' entry_bar/exit_bar are the real bar_index column values; the
    PositionTracker mirror's are 0-based offsets into the (already reset_index'd) slice. Both
    slices here are built the same way (reset_index(drop=True) on the same df), so bar_index ==
    offset for this test -- convert explicitly rather than assume, so a future change to how the
    window is sliced can't silently make this comparison meaningless."""
    bar_index_col = df["bar_index"].to_numpy()
    out = []
    for t in trades:
        t = dict(t)
        t["entry_bar"] = int(bar_index_col[t["entry_bar"]]) if t["entry_bar"] < len(bar_index_col) else t["entry_bar"]
        t["exit_bar"] = int(bar_index_col[t["exit_bar"]]) if t["exit_bar"] < len(bar_index_col) else t["exit_bar"]
        out.append(t)
    return out


CASES = [
    dict(name="long, stop+target", entry_side="long", hold_bars=10, stop_points=15, target_points=30),
    dict(name="short, stop only", entry_side="short", hold_bars=15, stop_points=10, target_points=None),
    dict(name="long, pure hold-bars exit", entry_side="long", hold_bars=5, stop_points=None, target_points=None),
]


def main() -> int:
    df_all = sl._load_df()
    # Clean, duplicate-free slice -- see CLAUDE.md's "duplicated rows from overlapping contracts"
    # note. Using a contaminated slice here would make a genuine mismatch indistinguishable from
    # a data artifact, defeating the point of this gate.
    clean = df_all[df_all["bar_index"] >= 11960].reset_index(drop=True)
    assert not clean["bar_index"].duplicated().any(), "test fixture itself is contaminated -- fix the slice bounds"
    df = clean.iloc[:3000].reset_index(drop=True)

    signal = (df["mlofi_norm"] > 0.3).to_numpy()
    fired = int(signal.sum())
    print(f"fixture: {len(df)} bars (bar_index {df['bar_index'].iloc[0]}-{df['bar_index'].iloc[-1]}), "
          f"signal fires on {fired} bars\n")

    all_ok = True
    for case in CASES:
        ref = _via_simulate_trades(df, signal, case["entry_side"], case["hold_bars"],
                                    case["stop_points"], case["target_points"])
        mirror = _mirror_via_position_tracker(df, signal, case["entry_side"], case["hold_bars"],
                                               case["stop_points"], case["target_points"])
        mirror = _bar_index_to_offset(mirror, df)

        ok = ref == mirror
        all_ok &= ok
        print(f"[{'PASS' if ok else 'FAIL'}] {case['name']}: "
              f"_simulate_trades={len(ref)} trades, PositionTracker={len(mirror)} trades")
        if not ok:
            print("  MISMATCH -- first 5 of each:")
            for a, b in list(zip(ref, mirror))[:5]:
                marker = "  " if a == b else ">>"
                print(f"  {marker} ref={a}")
                print(f"  {marker} mir={b}")
            if len(ref) != len(mirror):
                print(f"  trade COUNT differs: ref={len(ref)} mirror={len(mirror)}")

    print()
    print("ALL CASES PASS -- PositionTracker and _simulate_trades agree exactly" if all_ok
          else "FAILED -- PositionTracker has diverged from _simulate_trades; agent results and "
               "benchmarks are NOT currently comparable")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
