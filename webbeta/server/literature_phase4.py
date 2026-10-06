"""MISSION literature-research Phase 4: report honestly.

Consumes server/literature/backtest_results.json (Phase 3's real, already-computed numbers) and
produces the comparisons the mission asked for: survival vs. chance-alone expectation at this
trial count, a matched-trade-frequency random-signal benchmark per hypothesis, book distribution,
and a plain verdict. Does not re-run or reinterpret any hypothesis's own backtest -- only adds
comparison baselines around numbers Phase 3 already computed.
"""
from __future__ import annotations

import json
from pathlib import Path
from scipy.stats import norm

from . import strategy_lab

OUT_DIR = Path(__file__).resolve().parent / "literature"
RESULTS_PATH = OUT_DIR / "backtest_results.json"
REPORT_PATH = OUT_DIR / "phase4_report.json"

# Two-sided 95% significance threshold -- "held" means out-of-sample shows an edge distinguishable
# from zero at this level AND in the same direction in-sample already pointed, not merely "positive
# expectancy" (positive-by-luck is expected for roughly half of pure-noise hypotheses).
T_STAT_THRESHOLD = 1.96


def _held(backtest: dict) -> bool:
    """A hypothesis "held" if the strategy AS SPECIFIED -- entry_side already encodes which
    direction it predicts will be profitable, e.g. entry_side="long" for "delta_norm > 0.1
    predicts positive returns" -- shows a statistically significant POSITIVE out-of-sample
    expectancy. Bug caught before this shipped: the first version checked only "does
    out-of-sample agree in SIGN with in-sample," which does not mean the hypothesis's own
    prediction was confirmed -- it flagged hypothesis #2/#3 (delta_norm > 0.1, entry_side=long,
    predicting POSITIVE returns) as "held" purely because both in-sample (-0.255) and
    out-of-sample (-0.467) were consistently NEGATIVE and statistically significant -- i.e. the
    data was significant evidence AGAINST what the hypothesis actually claimed, not for it. A
    significant result in the wrong direction is a rejected hypothesis, not a held one."""
    oos = backtest["out_of_sample"]
    oos_t, oos_exp = oos.get("t_stat"), oos.get("expectancy_points")
    if oos_t is None or oos_exp is None:
        return False
    return oos_exp > 0 and oos_t >= T_STAT_THRESHOLD


def _random_benchmark(hypothesis: dict, backtest: dict, seed: int) -> dict:
    """A random signal firing at approximately the SAME per-bar rate as this hypothesis's own
    in-sample signal (recovered from its reported in-sample trade_count and bar count), run through
    the identical entry_side/exit_hold_bars/stop/target -- the same harness, same causality guard,
    same everything except WHICH bars it enters on. A fixed seed keyed to the hypothesis index
    makes this reproducible. Deliberately an approximate frequency match (trade_count is measured
    AFTER the exit_hold_bars spacing/no-overlap logic thins out raw signal fires, not before), not
    an exact one -- that approximation is stated here rather than silently assumed exact."""
    split = backtest["split"]
    ins_trades = backtest["in_sample"].get("trade_count", 0) or 0
    p = min(max(ins_trades / max(split["in_sample_bars"], 1), 0.001), 0.5)
    code = (
        "def signal(df):\n"
        f"    return np.random.RandomState({seed}).random(len(df)) < {p}\n"
    )
    args = {
        "code": code, "entry_side": hypothesis["entry_side"],
        "exit_hold_bars": hypothesis["exit_hold_bars"],
        "stop_points": hypothesis.get("stop_points"), "target_points": hypothesis.get("target_points"),
    }
    return strategy_lab.run_generated_backtest(args)


def build_report() -> dict:
    results = json.loads(RESULTS_PATH.read_text())
    tested = [r for r in results if r["status"] == "tested"]
    rejected = [r for r in results if r["status"] == "rejected"]

    per_hypothesis = []
    for r in tested:
        h, bt = r["hypothesis"], r["backtest"]
        held = _held(bt)
        try:
            rb = _random_benchmark(h, bt, seed=r["index"])
            random_benchmark = {
                "in_sample": {k: rb["in_sample"].get(k) for k in
                              ("trade_count", "expectancy_points", "t_stat")},
                "out_of_sample": {k: rb["out_of_sample"].get(k) for k in
                                 ("trade_count", "expectancy_points", "t_stat")},
            }
        except strategy_lab.StrategySpecError as exc:
            random_benchmark = {"error": str(exc)}
        per_hypothesis.append({
            "index": r["index"], "claim": h["claim"], "source_book": h["source_book"],
            "source_pages": h["source_pages"], "predicted_direction": h["predicted_direction"],
            "predicted_magnitude": h["predicted_magnitude"], "entry_side": h["entry_side"],
            "in_sample": {k: bt["in_sample"].get(k) for k in
                         ("trade_count", "win_rate", "expectancy_points", "t_stat")},
            "out_of_sample": {k: bt["out_of_sample"].get(k) for k in
                             ("trade_count", "win_rate", "expectancy_points", "t_stat")},
            "held_at_95pct_oos": held,
            "random_benchmark_matched_frequency": random_benchmark,
        })

    n_valid = len(tested)
    held_count = sum(1 for p in per_hypothesis if p["held_at_95pct_oos"])
    # Expected false positives from PURE CHANCE alone at this trial count. One-sided, matching
    # _held()'s definition (a significant POSITIVE expectancy specifically, not significant in
    # either direction) -- P(t >= 1.96) under the null is ~2.5%, not the ~5% a two-sided test
    # would give, since a significant NEGATIVE result no longer counts as "held" (see _held()).
    alpha_one_sided = 1 - norm.cdf(T_STAT_THRESHOLD)
    expected_by_chance = n_valid * alpha_one_sided

    oos_sharpes = [r["backtest"]["out_of_sample"].get("sharpe_annualized") for r in tested]
    oos_sharpes = [s for s in oos_sharpes if s is not None]
    oos_trade_counts = [r["backtest"]["out_of_sample"].get("trade_count", 0) for r in tested]
    oos_tpy = [r["backtest"]["out_of_sample"].get("sharpe_trades_per_year_assumed") for r in tested
               if r["backtest"]["out_of_sample"].get("sharpe_trades_per_year_assumed")]
    best_oos_sharpe = max(oos_sharpes) if oos_sharpes else None
    deflated_expected_max_sharpe = None
    if oos_sharpes and oos_trade_counts and oos_tpy:
        median_n_trades = sorted(oos_trade_counts)[len(oos_trade_counts) // 2]
        median_tpy = sorted(oos_tpy)[len(oos_tpy) // 2]
        deflated_expected_max_sharpe = strategy_lab._expected_max_sharpe_under_null(
            n_trials=n_valid, representative_n_trades=median_n_trades, trades_per_year=median_tpy,
        )

    book_counts: dict[str, int] = {}
    for r in results:  # ALL 17, including the rejected one -- it was still pre-registered
        book_counts[r["hypothesis"]["source_book"]] = book_counts.get(r["hypothesis"]["source_book"], 0) + 1

    return {
        "trial_count_preregistered": len(results),
        "trial_count_tested": n_valid,
        "trial_count_rejected_by_harness": len(rejected),
        "rejected": [{"index": r["index"], "claim": r["hypothesis"]["claim"], "error": r["error"]}
                     for r in rejected],
        "held_at_95pct_out_of_sample": held_count,
        "expected_held_by_chance_alone": round(expected_by_chance, 3),
        "best_out_of_sample_sharpe_annualized": best_oos_sharpe,
        "deflated_expected_max_sharpe_under_null": (
            round(deflated_expected_max_sharpe, 3) if deflated_expected_max_sharpe is not None else None
        ),
        "book_distribution_of_all_preregistered_hypotheses": book_counts,
        "per_hypothesis": per_hypothesis,
    }


def main() -> None:
    report = build_report()
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str))
    print(f"\n{'='*70}")
    print(f"Phase 4 report")
    print(f"Pre-registered: {report['trial_count_preregistered']}  "
          f"Tested: {report['trial_count_tested']}  "
          f"Rejected by harness: {report['trial_count_rejected_by_harness']}")
    print(f"Held (positive out-of-sample expectancy, t>=1.96): {report['held_at_95pct_out_of_sample']} "
          f"of {report['trial_count_tested']}")
    print(f"Expected held by chance alone at this trial count: {report['expected_held_by_chance_alone']}")
    print(f"Best out-of-sample annualized Sharpe observed: {report['best_out_of_sample_sharpe_annualized']}")
    print(f"Expected max Sharpe under the null across {report['trial_count_tested']} trials "
          f"(Bailey et al. deflation): {report['deflated_expected_max_sharpe_under_null']}")
    print(f"Book distribution: {report['book_distribution_of_all_preregistered_hypotheses']}")
    print(f"\nWritten to: {REPORT_PATH}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
