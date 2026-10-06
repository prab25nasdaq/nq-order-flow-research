"""MISSION literature-research Phase 3: test every pre-registered hypothesis, honestly.

Reads server/literature/hypotheses.json (Phase 2's output, written before any of these numbers
existed) and feeds each hypothesis's code/entry_side/exit_hold_bars/stop_points/target_points into
strategy_lab.run_generated_backtest() UNMODIFIED -- the same causality-guarded, train/test-split
harness every other strategy in this app goes through. No hypothesis is skipped, reworded, or
excluded for looking weak; every one gets a real result, including the ones expected to fail.

Deliberately does NOT try to auto-grade a hypothesis's free-text predicted_direction/
predicted_magnitude against the real numbers -- that's a judgment call for the Phase 4 report to
make plainly, in prose, next to the real table, not a fuzzy string-matching heuristic pretending to
be objective.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from . import strategy_lab

log = logging.getLogger("webbeta.literature_backtest")

OUT_DIR = Path(__file__).resolve().parent / "literature"
HYPOTHESES_PATH = OUT_DIR / "hypotheses.json"
RESULTS_PATH = OUT_DIR / "backtest_results.json"


def _extract_backtest_args(hypothesis: dict) -> dict:
    """Exactly the subset of a hypothesis record run_generated_backtest() accepts -- everything
    else (claim, source_book, mechanism, predicted_direction, predicted_magnitude,
    recorded_at_roundtrip) is pre-registration metadata this function deliberately ignores."""
    return {
        "code": hypothesis["code"],
        "entry_side": hypothesis["entry_side"],
        "exit_hold_bars": hypothesis["exit_hold_bars"],
        "stop_points": hypothesis.get("stop_points"),
        "target_points": hypothesis.get("target_points"),
    }


def run_all_hypotheses(hypotheses_path: Path = HYPOTHESES_PATH) -> list[dict]:
    """Runs EVERY hypothesis in the file through run_generated_backtest(), in order, and returns
    one result record per hypothesis -- {"index", "hypothesis" (verbatim, unmodified),
    "status" ("tested"|"rejected"), "error" (None unless rejected), "backtest" (None unless
    tested)}. A hypothesis whose code fails the causality/leakage guard or the AST safety check is
    recorded as "rejected" with the real StrategySpecError message, not silently dropped -- that is
    itself a reportable fact about that hypothesis, not a bug in this runner."""
    hypotheses = json.loads(hypotheses_path.read_text())
    results = []
    for i, h in enumerate(hypotheses):
        args = _extract_backtest_args(h)
        try:
            backtest = strategy_lab.run_generated_backtest(args)
            status, error = "tested", None
            log.info("hypothesis %d/%d tested: in-sample trades=%s out-of-sample trades=%s",
                      i + 1, len(hypotheses), backtest["in_sample"].get("trade_count"),
                      backtest["out_of_sample"].get("trade_count"))
        except strategy_lab.StrategySpecError as exc:
            backtest, status, error = None, "rejected", str(exc)
            log.warning("hypothesis %d/%d REJECTED: %s", i + 1, len(hypotheses), exc)
        results.append({
            "index": i, "hypothesis": h, "status": status, "error": error, "backtest": backtest,
        })
    return results


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    results = run_all_hypotheses()
    RESULTS_PATH.write_text(json.dumps(results, indent=2, default=str))
    tested = sum(1 for r in results if r["status"] == "tested")
    rejected = sum(1 for r in results if r["status"] == "rejected")
    print(f"\n{'='*70}")
    print(f"Phase 3: {len(results)} hypotheses total -- {tested} tested, {rejected} rejected")
    for r in results:
        h = r["hypothesis"]
        tag = f"#{r['index']+1}"
        if r["status"] == "rejected":
            print(f"{tag} REJECTED: {r['error']}")
            continue
        ins = r["backtest"]["in_sample"]
        oos = r["backtest"]["out_of_sample"]
        print(f"{tag} [{h['entry_side']}] {h['claim'][:70]}")
        print(f"     in-sample:  n={ins.get('trade_count')} win_rate={ins.get('win_rate')} "
              f"expectancy={ins.get('expectancy_points')} t={ins.get('t_stat')}")
        print(f"     out-of-sample: n={oos.get('trade_count')} win_rate={oos.get('win_rate')} "
              f"expectancy={oos.get('expectancy_points')} t={oos.get('t_stat')}")
    print(f"\nWritten to: {RESULTS_PATH}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
