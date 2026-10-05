"""AI Strategy Lab: a deterministic, server-side backtest engine over the continuous NQ bar
history. The model NEVER executes code or computes numbers itself -- it proposes a StrategySpec
(validated against a strict whitelist below) via tool-calling, this module runs the actual
walk-forward simulation over real historical bars, and the model only narrates the real result it
gets back. No eval/exec anywhere in this file.

Data: OFI_Live_Features/master_NQ_continuous_backadjusted_shadow.ndjsonl only (~69k vol500 bars,
2026-06-03..2026-08-31, continuous/roll-adjusted). The raw Rithmic_Raw tick archive (950GB, single
symbol) is deliberately out of scope -- not practical or safe to expose to a browser-facing tool.
"""
from __future__ import annotations

import itertools
import os
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import norm

from . import signal_sandbox

DATA_FILE = Path(
    os.environ.get("WEBBETA_STRATEGY_LAB_DATA_FILE")
    or "/home/prabh/OFI_Live_Features/master_NQ_continuous_backadjusted_shadow.ndjsonl"
)

# Curated subset of the ~129 available columns -- order-flow/regime features a strategy can
# condition on, plus the continuous OHLC used for P&L (not exposed as condition targets since
# conditioning "price > X" isn't a meaningful order-flow strategy input for this tool; price
# entry/exit is handled internally by the backtest loop itself, not by entry_conditions).
CONDITION_COLUMNS: dict[str, str] = {
    "mlofi_norm": "Normalized multi-level order-flow imbalance for this bar",
    "mlofi_sum": "Raw summed order-flow imbalance for this bar",
    "mlofi_decay_sum": "Decay-weighted order-flow imbalance",
    "mlofi_rolling_5": "5-bar rolling mean of mlofi_norm",
    "delta_norm": "Normalized trade delta (buy volume - sell volume)",
    "delta_sum": "Raw trade delta for this bar",
    "delta_rolling_5": "5-bar rolling mean of delta_norm",
    "vpin": "Volume-synchronized probability of informed trading (0-1)",
    "sweep_imbalance_norm": "Normalized buy-sweep minus sell-sweep imbalance",
    "sweep_signed_vol": "Signed sweep volume (positive = buy sweeps dominant)",
    "buy_ratio": "Fraction of this bar's volume that was buy-initiated",
    "sell_ratio": "Fraction of this bar's volume that was sell-initiated",
    "volatility_5": "5-bar rolling volatility of mid price",
    "mid_resid_z": "Z-score of mid price residual vs its rolling model",
    "vpin_resid_z20": "20-bar z-score of VPIN residual",
    "delta_norm_resid_z20": "20-bar z-score of delta_norm residual",
    "cusum_up_break": "1 if a CUSUM upward regime break fired on this bar, else 0",
    "cusum_down_break": "1 if a CUSUM downward regime break fired on this bar, else 0",
    "minute_of_day": "Minutes since UTC midnight (0-1439)",
    "dow": "Day of week (0=Monday..6=Sunday)",
}

OPERATORS = ("<", "<=", ">", ">=", "crosses_above", "crosses_below")
MAX_CONDITIONS = 5
MAX_HOLD_BARS = 200
MAX_STOP_TARGET_POINTS = 500.0

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "run_backtest",
        "description": (
            "Run a deterministic backtest of an order-flow strategy against real historical NQ "
            "vol500 bars (see the system prompt's dataset facts for the exact bar count and date "
            "range -- don't estimate or round it here). Returns real computed metrics (win rate, "
            "trade count, avg points per trade, max drawdown, profit factor) -- never invent or "
            "estimate these numbers yourself, only report exactly what this tool returns."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entry_side": {"type": "string", "enum": ["long", "short"]},
                "entry_conditions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_CONDITIONS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "column": {"type": "string", "enum": list(CONDITION_COLUMNS)},
                            "operator": {"type": "string", "enum": list(OPERATORS)},
                            "value": {"type": "number"},
                        },
                        "required": ["column", "operator", "value"],
                    },
                    "description": "All conditions are ANDed together.",
                },
                "exit_hold_bars": {
                    "type": "integer",
                    "description": f"Exit after this many bars if no stop/target hit. 1-{MAX_HOLD_BARS}.",
                },
                "stop_points": {
                    "type": "number",
                    "description": "Optional stop-loss distance in points from entry.",
                },
                "target_points": {
                    "type": "number",
                    "description": "Optional profit-target distance in points from entry.",
                },
            },
            "required": ["entry_side", "entry_conditions", "exit_hold_bars"],
        },
    },
}


class StrategySpecError(ValueError):
    """Raised with a message specific enough for the model to self-correct on the next turn."""


# MISSION strategy-lab-sweep Phase 1: additive-only statistical fields, shared with the Phase 2
# sweep tool so both compute expectancy/t-stat/Sharpe identically. None (never a fabricated number)
# whenever a figure genuinely isn't computable -- same convention already used for profit_factor.
YEAR_SECONDS = 365.25 * 24 * 3600.0


def _elapsed_years(df: pd.DataFrame) -> float:
    span_ns = float(df["bar_start_ts_ns"].iloc[-1] - df["bar_start_ts_ns"].iloc[0])
    return max(span_ns / 1e9 / YEAR_SECONDS, 1e-9)  # guard a degenerate zero-span window


def _trade_stats(pnls: np.ndarray, elapsed_years: float) -> dict:
    """expectancy (+ its standard error), a one-sample t-stat of mean PnL vs. 0, and an annualized
    Sharpe. Sharpe treats one TRADE as one period (there's no fixed bar-interval return series for
    a signal-driven strategy) and annualizes by this strategy's OWN realized trade frequency over
    the tested window -- trades_per_year is always reported alongside it so the assumption is
    never hidden. A high-frequency condition will show a large annualized Sharpe purely from the
    sqrt(trades_per_year) scaling; that's this convention's known behavior, not a bug."""
    n = len(pnls)
    mean = float(pnls.mean())
    std = float(pnls.std(ddof=1)) if n >= 2 else 0.0
    se = (std / float(np.sqrt(n))) if (n >= 2 and std > 0) else None
    t_stat = (mean / se) if se else None
    trades_per_year = n / elapsed_years
    sharpe_annualized = (mean / std) * float(np.sqrt(trades_per_year)) if std > 0 else None
    return {
        "expectancy_points": round(mean, 3),
        "expectancy_se_points": round(se, 4) if se is not None else None,
        "t_stat": round(t_stat, 3) if t_stat is not None else None,
        "sharpe_annualized": round(sharpe_annualized, 3) if sharpe_annualized is not None else None,
        "sharpe_trades_per_year_assumed": round(trades_per_year, 1),
    }


_df_lock = threading.Lock()
_df_cache: Optional[pd.DataFrame] = None
_column_ranges_cache: Optional[dict[str, tuple[float, float, float, float, float]]] = None


# MISSION strategy-lab-data-integrity: confirmed live this file's own dataframe contained 11,960
# duplicate `bar_index` VALUES (bar_index 0 through 11,959, exactly the NQM6 row count, no gaps,
# nowhere else in the 69,137-row file) -- but NOT duplicate ROWS. Every "paired" row is a distinct,
# real bar: different timestamps (weeks apart), different contract_symbol, different prices,
# different order-flow features. `bar_index` is a PER-CONTRACT vol500 bar counter that restarts
# near 0 when a new contract (NQU6) begins accumulating its own volume-bars, independently of the
# outgoing contract (NQM6) -- confirmed zero real calendar-time overlap between the two contracts
# (NQM6's last bar: 2026-06-12 20:51 UTC; NQU6's first bar: 2026-06-14 22:30 UTC). This is
# independently corroborated by zscores.py's own docstring, written earlier and unrelated to this
# investigation: "The file's bar_index column restarts at 0 at the NQM6->NQU6 contract roll" --
# that module already works around it (indexes NQU6 rows only); this one never did.
# `sort_values("bar_index")` was therefore never a chronological sort for a multi-contract file --
# it happened to mostly work for NQU6-only rows (bar_index IS monotonic within one contract) but
# silently interleaved NQM6's real 2026-06-03..06-12 history with NQU6's real 2026-06-14 onward
# history wherever their bar_index counters collided (0..11,959). Every backtest whose entry or
# holding period touched a row in that range was walking bars in the WRONG chronological order --
# "entry_idx = i+1" landing on a bar from the OTHER contract, weeks away in real time, not the
# genuine next bar. Fixed by sorting on real time (bar_start_ts_ns, a raw ns epoch -- unambiguous,
# already this module's own chronological source of truth via _dataset_stats) instead of the
# per-contract bar_index. bar_index itself is STILL not a globally unique key after this fix (two
# real, correctly-time-ordered rows can share the same bar_index number, one per contract) -- never
# use it as a lookup/join key expecting one row back; strategy_lab.py doesn't, but any NEW code
# reading this file must not assume it either (see agent_replay.py's own TODO on this point once
# that work resumes).
def _load_df() -> pd.DataFrame:
    global _df_cache
    with _df_lock:
        if _df_cache is None:
            df = pd.read_json(DATA_FILE, lines=True)
            df = df.sort_values("bar_start_ts_ns").reset_index(drop=True)
            assert df["bar_start_ts_ns"].is_monotonic_increasing, (
                "loaded dataframe is not in chronological order -- re-check the sort key against "
                "the raw file's actual timestamp column before trusting any backtest result"
            )
            # NOT `df["bar_index"].is_unique` -- that is FALSE by design, permanently: bar_index is
            # a PER-CONTRACT vol500 counter (confirmed: restarts near 0 for every new contract,
            # e.g. NQU6 after the NQM6 roll), so two rows sharing a bar_index number across
            # different contracts is expected and will recur at every future roll -- asserting
            # global uniqueness would fail immediately on entirely correct data. The invariant that
            # SHOULD always hold, roll or no roll, is that bar_index is unique WITHIN one contract's
            # own tape -- if this ever fails, something is genuinely wrong (a contract's own bar
            # counter repeated or skipped in a way that breaks its internal ordering), which a
            # global check would never distinguish from an ordinary roll and would either miss or
            # false-alarm on. This is what would have actually caught THIS investigation's root
            # cause faster: not "bar_index repeats" (true and harmless every roll) but "does it
            # repeat WITHIN a contract" (should never be true).
            dupe_within_contract = df.duplicated(subset=["source_contract", "bar_index"])
            assert not dupe_within_contract.any(), (
                f"{int(dupe_within_contract.sum())} row(s) share the same (source_contract, "
                f"bar_index) pair -- a genuine data anomaly, not an ordinary contract roll (which "
                f"only ever produces bar_index collisions ACROSS DIFFERENT contracts, never within "
                f"one). Do not silently proceed; this file needs to be re-examined before trusting "
                f"any backtest result computed from it."
            )
            _df_cache = df
        return _df_cache


# MISSION strategy-lab-narration-verify: confirmed live the model proposed `vpin > 0.5` for a
# Sunday-Asia-reopen strategy -- vpin's actual max in this ENTIRE dataset is 0.42, so that
# condition can never fire anywhere, not just in that session. The model had no way to know this;
# describe_columns() gave it a name and a one-line meaning, never the column's actual shape. This
# computes real min/p5/median/p95/max per condition column, once, from the same cached dataframe
# strategy_lab already loads for every backtest -- not a second dataset, not a guess.
def _column_ranges() -> dict[str, tuple[float, float, float, float, float]]:
    global _column_ranges_cache
    if _column_ranges_cache is None:
        df = _load_df()
        _column_ranges_cache = {
            name: (
                float(df[name].min()), float(df[name].quantile(0.05)), float(df[name].median()),
                float(df[name].quantile(0.95)), float(df[name].max()),
            )
            for name in CONDITION_COLUMNS
        }
    return _column_ranges_cache


def _format_stat(x: float) -> str:
    return f"{x:.0f}" if abs(x) >= 100 else f"{x:.4g}"


def describe_columns() -> str:
    ranges = _column_ranges()
    lines = []
    for name, desc in CONDITION_COLUMNS.items():
        lo, p5, med, p95, hi = ranges[name]
        lines.append(
            f"- {name}: {desc} [observed range: {_format_stat(lo)} to {_format_stat(hi)}, "
            f"typical (5th-95th pct): {_format_stat(p5)} to {_format_stat(p95)}, "
            f"median {_format_stat(med)}]"
        )
    return "\n".join(lines)


_dataset_stats_cache: Optional[dict] = None


# MISSION strategy-lab-narration-verify Phase 2: a pure dataset-metadata question ("how many bars
# do we have") had no direct answer available -- not a column value, not a backtest, not
# web-searchable (this data is local and fixed). The model's only recourse was attempting a
# backtest tool call, which the structural gate correctly blocks, but blocking isn't the same as
# answering. This computes the real facts ONCE from the same cached dataframe every backtest
# already uses, so the model can just state them directly, the same way it already does for
# describe_columns()'s ranges -- no tool call needed for a question a fixed dataset already
# answers by existing.
def _dataset_stats() -> dict:
    global _dataset_stats_cache
    if _dataset_stats_cache is None:
        df = _load_df()
        ts = pd.to_datetime(df["bar_start_ts_ns"], unit="ns", utc=True)
        _dataset_stats_cache = {
            "n_bars": int(len(df)),
            "start": ts.min(),
            "end": ts.max(),
            "span_days": (ts.max() - ts.min()).days,
        }
    return _dataset_stats_cache


def describe_dataset() -> str:
    s = _dataset_stats()
    return (
        f"{s['n_bars']:,} bars, spanning {s['start'].strftime('%Y-%m-%d')} to "
        f"{s['end'].strftime('%Y-%m-%d')} ({s['span_days']} days). Continuous, roll-adjusted NQ "
        f"vol500 bars (each bar closes after a fixed traded volume, not a fixed time interval)."
    )


def _validate_spec(args: dict) -> dict:
    if not isinstance(args, dict):
        raise StrategySpecError("Arguments must be a JSON object.")

    conditions = args.get("entry_conditions")
    if not isinstance(conditions, list) or not conditions:
        raise StrategySpecError("entry_conditions must be a non-empty array.")
    if len(conditions) > MAX_CONDITIONS:
        raise StrategySpecError(f"entry_conditions may have at most {MAX_CONDITIONS} items.")

    parsed_conditions = []
    for i, c in enumerate(conditions):
        if not isinstance(c, dict):
            raise StrategySpecError(f"entry_conditions[{i}] must be an object.")
        extra = set(c.keys()) - {"column", "operator", "value"}
        if extra:
            raise StrategySpecError(
                f"entry_conditions[{i}] has unknown key(s) {sorted(extra)}. "
                f"Valid keys are exactly: column, operator, value."
            )
        col = c.get("column")
        if col not in CONDITION_COLUMNS:
            raise StrategySpecError(
                f"entry_conditions[{i}].column {col!r} is not available. "
                f"Valid columns: {', '.join(CONDITION_COLUMNS)}."
            )
        op = c.get("operator")
        if op not in OPERATORS:
            raise StrategySpecError(
                f"entry_conditions[{i}].operator {op!r} is invalid. Valid operators: {', '.join(OPERATORS)}."
            )
        try:
            val = float(c.get("value"))
        except (TypeError, ValueError):
            raise StrategySpecError(f"entry_conditions[{i}].value must be a number.")
        parsed_conditions.append({"column": col, "operator": op, "value": val})

    exit_params = _validate_exit_params(args)  # also validates entry_side, exit_hold_bars, stop/target
    return {"entry_conditions": parsed_conditions, **exit_params}


# MISSION strategy-lab-generated-signals Phase 3: factored out of _validate_spec so
# run_generated_backtest can share the exact same exit-rule validation ("the usual exit rule and
# parameters") without duplicating it -- entry_side is validated here too since a generated
# signal() has no notion of side (it only says WHEN, not long/short), so the tool call must supply
# it as a sibling parameter, same as entry_conditions-based run_backtest.
def _validate_exit_params(args: dict) -> dict:
    side = args.get("entry_side")
    if side not in ("long", "short"):
        raise StrategySpecError('entry_side must be exactly "long" or "short".')

    hold_bars = args.get("exit_hold_bars")
    try:
        hold_bars = int(hold_bars)
    except (TypeError, ValueError):
        raise StrategySpecError("exit_hold_bars must be an integer.")
    if not (1 <= hold_bars <= MAX_HOLD_BARS):
        raise StrategySpecError(f"exit_hold_bars must be between 1 and {MAX_HOLD_BARS}.")

    stop_points = args.get("stop_points")
    if stop_points is not None:
        try:
            stop_points = float(stop_points)
        except (TypeError, ValueError):
            raise StrategySpecError("stop_points must be a number.")
        if not (0 < stop_points <= MAX_STOP_TARGET_POINTS):
            raise StrategySpecError(f"stop_points must be between 0 and {MAX_STOP_TARGET_POINTS}.")

    target_points = args.get("target_points")
    if target_points is not None:
        try:
            target_points = float(target_points)
        except (TypeError, ValueError):
            raise StrategySpecError("target_points must be a number.")
        if not (0 < target_points <= MAX_STOP_TARGET_POINTS):
            raise StrategySpecError(f"target_points must be between 0 and {MAX_STOP_TARGET_POINTS}.")

    return {
        "entry_side": side,
        "exit_hold_bars": hold_bars,
        "stop_points": stop_points,
        "target_points": target_points,
    }


def _entry_mask(df: pd.DataFrame, conditions: list[dict]) -> np.ndarray:
    mask = np.ones(len(df), dtype=bool)
    for c in conditions:
        col, op, val = c["column"], c["operator"], c["value"]
        series = df[col].to_numpy(dtype=float)
        if op == "<":
            cond = series < val
        elif op == "<=":
            cond = series <= val
        elif op == ">":
            cond = series > val
        elif op == ">=":
            cond = series >= val
        elif op == "crosses_above":
            prev = np.roll(series, 1)
            prev[0] = np.nan
            cond = (prev <= val) & (series > val)
        elif op == "crosses_below":
            prev = np.roll(series, 1)
            prev[0] = np.nan
            cond = (prev >= val) & (series < val)
        else:  # pragma: no cover -- already validated
            raise StrategySpecError(f"unknown operator {op!r}")
        cond = np.nan_to_num(cond, nan=False).astype(bool)
        mask &= cond
    return mask


# MISSION strategy-lab-sweep Phase 2: extracted verbatim from the old run_backtest so the sweep
# tool can run the exact same simulation on windowed (in-sample/out-of-sample) slices of df without
# duplicating this logic. Takes any (possibly windowed) dataframe -- callers are responsible for
# handing it a chronologically-contiguous slice.
#
# MISSION strategy-lab-generated-signals Phase 3: `signal_override` lets a generated signal() run
# through this EXACT SAME execution path -- same one-bar shift (entry_idx = i + 1 below), same
# exit/PnL logic -- as entry_conditions-based strategies, rather than duplicating any of it. When
# given, spec["entry_conditions"] is ignored entirely (spec still supplies entry_side/exit rule).
def _simulate_trades(df: pd.DataFrame, spec: dict, signal_override: Optional[np.ndarray] = None) -> list[dict]:
    signal = signal_override if signal_override is not None else _entry_mask(df, spec["entry_conditions"])
    is_long = spec["entry_side"] == "long"
    hold_bars = spec["exit_hold_bars"]
    stop_pts = spec["stop_points"]
    target_pts = spec["target_points"]

    opens = df["continuous_open"].to_numpy(dtype=float)
    highs = df["continuous_high"].to_numpy(dtype=float)
    lows = df["continuous_low"].to_numpy(dtype=float)
    closes = df["continuous_close"].to_numpy(dtype=float)
    bar_indices = df["bar_index"].to_numpy()
    n = len(df)

    trades = []
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
        exit_px = closes[exit_idx]
        exit_reason = "hold_bars"

        if stop_pts is not None or target_pts is not None:
            for j in range(entry_idx, exit_idx + 1):
                if is_long:
                    hit_stop = stop_pts is not None and lows[j] <= entry_px - stop_pts
                    hit_target = target_pts is not None and highs[j] >= entry_px + target_pts
                else:
                    hit_stop = stop_pts is not None and highs[j] >= entry_px + stop_pts
                    hit_target = target_pts is not None and lows[j] <= entry_px - target_pts
                if hit_stop and hit_target:
                    # ambiguous same-bar hit -- conservatively assume the worse outcome (stop)
                    exit_px = entry_px - stop_pts if is_long else entry_px + stop_pts
                    exit_idx, exit_reason = j, "stop"
                    break
                if hit_stop:
                    exit_px = entry_px - stop_pts if is_long else entry_px + stop_pts
                    exit_idx, exit_reason = j, "stop"
                    break
                if hit_target:
                    exit_px = entry_px + target_pts if is_long else entry_px - target_pts
                    exit_idx, exit_reason = j, "target"
                    break

        pnl = (exit_px - entry_px) if is_long else (entry_px - exit_px)
        trades.append({
            "entry_bar": int(bar_indices[entry_idx]),
            "exit_bar": int(bar_indices[exit_idx]),
            "entry_px": round(float(entry_px), 2),
            "exit_px": round(float(exit_px), 2),
            "pnl_points": round(float(pnl), 2),
            "hold_bars": int(exit_idx - entry_idx),
            "exit_reason": exit_reason,
        })
        i = exit_idx + 1  # non-overlapping: skip signals while a position would still be open
    return trades


# MISSION strategy-lab-generated-signals Phase 3: extracted from run_backtest so
# run_generated_backtest can produce the exact same metric shape -- "returns the same metrics as
# the existing backtest" only holds if this is genuinely shared code, not a second implementation
# that could quietly drift from the first. data_span/disclosure are NOT included here (callers
# assemble those since they differ by context -- full series vs. an in-sample/out-of-sample slice).
def _full_backtest_metrics(trades: list[dict], elapsed_years: float) -> dict:
    if not trades:
        return {
            "trade_count": 0,
            "message": "No trades: these conditions never fired (or never fired with room to "
                       "complete a trade) across the available history.",
        }

    pnls = np.array([t["pnl_points"] for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    equity = np.cumsum(pnls)
    running_max = np.maximum.accumulate(equity)
    drawdown = running_max - equity
    profit_factor = (
        float(wins.sum() / abs(losses.sum())) if losses.sum() != 0 else (float("inf") if wins.sum() > 0 else 0.0)
    )
    stats = _trade_stats(pnls, elapsed_years)

    return {
        "trade_count": len(trades),
        "win_rate": round(float(len(wins) / len(trades)), 4),
        "avg_pnl_points": round(float(pnls.mean()), 3),
        "total_pnl_points": round(float(pnls.sum()), 2),
        "max_drawdown_points": round(float(drawdown.max()), 2),
        "profit_factor": round(profit_factor, 3) if np.isfinite(profit_factor) else None,
        "avg_hold_bars": round(float(np.mean([t["hold_bars"] for t in trades])), 2),
        **stats,
        "equity_curve_points": [round(float(x), 2) for x in equity],
        "sample_trades": trades[:10],
    }


def run_backtest(args: dict) -> dict:
    """Validates args (raises StrategySpecError with a self-correctable message on failure),
    then runs a real, deterministic, non-overlapping walk-forward backtest and returns real
    computed metrics. Enters at the NEXT bar's open after a signal bar (never the signal bar's
    own close) to avoid lookahead bias."""
    spec = _validate_spec(args)
    df = _load_df()
    trades = _simulate_trades(df, spec)
    metrics = _full_backtest_metrics(trades, _elapsed_years(df))
    return {
        **metrics,
        "data_span": {
            "bars": len(df),
            "from_bar_index": int(df["bar_index"].iloc[0]),
            "to_bar_index": int(df["bar_index"].iloc[-1]),
        },
        "disclosure": "SHADOW/RESEARCH ONLY -- historical simulation, not investment advice, no "
                       "guarantee of future results.",
    }


# =============================================================================================
# MISSION strategy-lab-sweep Phase 2 (original JSON-grid design, RETIRED -- see
# strategy-lab-codegen-primary below): shared statistical machinery kept here since
# sweep_generated_backtest (the code-generation replacement) still needs the same deflation math,
# top-N/out-of-sample reporting shape, and grid-size discipline. The JSON-condition-grid tool
# itself (sweep_backtest, _validate_grid_spec) is removed: it hit the same schema-expressiveness
# ceiling as run_backtest -- a session+day+threshold-sweep request needed 4 of 5 condition slots
# just for scaffolding and no equality operator exists for exact day-of-week matches, which is
# what actually caused the "delta" follow-up bug (root-caused via journalctl-correlated repro:
# the model correctly reasoned through dow>=6 AND minute_of_day<=180 AND a delta_norm sweep, then
# ran out of budget writing the JSON for it). One line of pandas expresses the same logic for
# free; see SWEEP_GENERATED_TOOL_DEF below.
# =============================================================================================

MAX_GRID_VALUES_PER_FIELD = 12
TOP_N_FOR_OOS = 5
IN_SAMPLE_FRACTION = 0.7
_EULER_MASCHERONI = 0.5772156649015329


def _expected_max_sharpe_under_null(n_trials: int, representative_n_trades: int,
                                     trades_per_year: float) -> Optional[float]:
    """Bailey, Borwein, Lopez de Prado & Zhu (2014), 'The Probability of Backtest Overfitting':
    the expected value of the BEST Sharpe ratio across n_trials independent trials even if the
    true Sharpe were zero for every one of them (iid Gaussian returns assumed under the null).
    Lets an observed 'best' Sharpe be judged against what pure random search over this many trials
    would produce by chance alone -- this is a baseline to compare against, not proof the winner
    is real or fake."""
    if n_trials < 2 or representative_n_trades < 2:
        return None
    sigma_sr = 1.0 / np.sqrt(representative_n_trades - 1)  # per-trade Sharpe SE under the null
    z1 = float(norm.ppf(1.0 - 1.0 / n_trials))
    z2 = float(norm.ppf(1.0 - 1.0 / (n_trials * np.e)))
    expected_max_per_trade = sigma_sr * ((1 - _EULER_MASCHERONI) * z1 + _EULER_MASCHERONI * z2)
    return expected_max_per_trade * float(np.sqrt(trades_per_year))


def _quick_stats(df_window: pd.DataFrame, spec: dict, signal_override: Optional[np.ndarray] = None) -> Optional[dict]:
    """Per-grid-cell stats: trade_count/win_rate/total_pnl_points plus the full expectancy/t-stat/
    Sharpe bundle. None (not an empty dict) on zero trades -- callers count these separately as
    combinations_with_zero_trades rather than silently including them at rank 0."""
    trades = _simulate_trades(df_window, spec, signal_override=signal_override)
    if not trades:
        return None
    pnls = np.array([t["pnl_points"] for t in trades])
    wins = pnls[pnls > 0]
    return {
        "trade_count": len(trades),
        "win_rate": round(float(len(wins) / len(trades)), 4),
        "total_pnl_points": round(float(pnls.sum()), 2),
        **_trade_stats(pnls, _elapsed_years(df_window)),
    }


# =============================================================================================
# MISSION strategy-lab-generated-signals Phase 3: lets the model express a strategy that ISN'T
# expressible as entry_conditions over the whitelisted columns (e.g. "test a Fibonacci retracement
# strategy") by writing exactly one pure function, signal(df) -> pd.Series[bool]. The model NEVER
# writes a backtest loop -- signal_sandbox.py validates and safely executes the function (Phase 1)
# and enforces causality + a degenerate-signal cap (Phase 2); this module only ever consumes the
# already-checked boolean array through the SAME _simulate_trades/_full_backtest_metrics path
# every other strategy type uses. If the sandbox rejects/fails, this raises StrategySpecError
# exactly like an invalid entry_conditions spec would -- llm_chat.py's existing retry plumbing
# already knows how to feed that back to the model, capped at one auto-revise (see llm_chat.py).
# =============================================================================================

# Curated "raw bar data" columns exposed to generated code -- continuous OHLC (what P&L is based
# on) + raw (un-adjusted) OHLC + volume/trade counts + every column already whitelisted for
# entry_conditions, so a generated function is never MORE restricted than the condition-based
# path, only broader. Deliberately excludes pipeline/administrative columns (contract_symbol,
# roll_pair, roll_gap_method, is_backadjusted_history, ...) -- no legitimate signal use, only
# attack surface and confusion.
GENERATED_SIGNAL_COLUMNS = [
    "bar_index", "bar_start_ts_ns",
    "continuous_open", "continuous_high", "continuous_low", "continuous_close",
    "raw_open", "raw_high", "raw_low", "raw_close",
    "vol_total", "buy_vol", "sell_vol", "trade_count",
] + list(CONDITION_COLUMNS)

# MISSION raw-data-research: the whitelist for the raw-data-research chat mode's own backtest tool
# (run_raw_generated_backtest) -- deliberately excludes every column in CONDITION_COLUMNS (mlofi_*,
# vpin*, sweep_*, delta_*, cusum_*, mid_resid*, volatility_5*, decay_norm*, regime_ic_*,
# entropy_score, flow_alignment, and every _lag_N/_resid_z20 variant of any of those) -- these are
# ALL "someone else's conclusions" per that mission's explicit design: a specific formula,
# normalization, or model choice already baked in, exactly what that research mode exists to get
# away from. What remains is bar timing/index, continuous OHLC (a mechanical roll-adjustment, not
# an interpretive order-flow feature -- kept because a tradable reference price series is still
# needed for PnL), raw (un-adjusted) OHLC, and plain volume/trade counts -- see
# CLAUDE.md/raw-data-research for the full raw-vs-derived classification this was drawn from.
RAW_ONLY_SIGNAL_COLUMNS = [
    "bar_index", "bar_start_ts_ns",
    "continuous_open", "continuous_high", "continuous_low", "continuous_close",
    "raw_open", "raw_high", "raw_low", "raw_close",
    "vol_total", "buy_vol", "sell_vol", "trade_count",
    "minute_of_day", "dow",
]

# MISSION strategy-lab-codegen-primary: shared between GENERATED_TOOL_DEF and
# SWEEP_GENERATED_TOOL_DEF so the four session names and their (documented, arguable) ET-hours
# boundaries are described identically everywhere the model sees them.
SESSION_HELP_TEXT = (
    "You may call session_mask(df, name) for a ready-made, DST-correct session filter instead of "
    "hand-rolling day/time-of-day logic -- name is one of: rth (Mon-Fri 09:30-16:00 ET), overnight "
    "(everything outside rth), london (Mon-Fri 03:00-08:00 ET), asia_sunday_reopen (Sun 18:00 ET "
    "through Mon 03:00 ET). These are stated conventions for NQ futures, not a universal standard "
    "-- say which one you used when you use one."
)

GENERATED_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "run_generated_backtest",
        "description": (
            "Test a strategy that can't be expressed as a simple column/operator/value condition "
            "(e.g. a Fibonacci retracement, a session/time-of-day filter, a custom pattern, a "
            "multi-step derived indicator). You supply ONE pure Python function -- def signal(df): "
            "-- that returns a boolean Series, True on bars where a position should be entered; "
            "the harness handles everything else (loading data, shifting your signal by one bar so "
            "it can never look at its own future, the train/test split, exits, PnL, and metrics). "
            "Your function is checked for look-ahead bias by re-running it on truncated history and "
            "comparing outputs -- if it uses ANY future data (a negative shift, a mean/std over the "
            "whole series instead of a rolling window, .iloc[-1] as 'now') it will be rejected with "
            "an explanation. " + SESSION_HELP_TEXT + " Available columns: " +
            ", ".join(GENERATED_SIGNAL_COLUMNS) + ". Only pandas (pd), numpy (np), and math are "
            "available -- no other imports, no file or network access. Returns the same metrics as "
            "run_backtest, for both an in-sample and an out-of-sample window, so results are "
            "directly comparable to the other tools."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Exactly one function: 'def signal(df):\\n    ...\\n    return "
                                   "<boolean Series>'. No imports besides pandas/numpy/math, no "
                                   "other top-level statements.",
                },
                "entry_side": {"type": "string", "enum": ["long", "short"]},
                "exit_hold_bars": {"type": "integer", "description": f"1-{MAX_HOLD_BARS}."},
                "stop_points": {"type": "number", "description": "Optional stop-loss distance in points."},
                "target_points": {"type": "number", "description": "Optional profit-target distance in points."},
            },
            "required": ["code", "entry_side", "exit_hold_bars"],
        },
    },
}


def _run_generated_backtest_impl(args: dict, signal_columns: list[str]) -> dict:
    """Shared by run_generated_backtest (GENERATED_SIGNAL_COLUMNS) and run_raw_generated_backtest
    (RAW_ONLY_SIGNAL_COLUMNS) -- identical causality-guarded/train-test-split/cost-model logic
    either way; the only difference is which columns are sliced into the sandboxed dataframe
    before the model's signal(df) ever runs, i.e. what it's even ABLE to condition on."""
    if not isinstance(args, dict):
        raise StrategySpecError("Arguments must be a JSON object.")
    code = args.get("code")
    if not isinstance(code, str) or not code.strip():
        raise StrategySpecError("code must be a non-empty string containing exactly one function, signal(df).")
    exit_params = _validate_exit_params(args)

    full_df = _load_df()
    sandbox_df = full_df[signal_columns].copy()

    check = signal_sandbox.run_signal_with_leakage_guard(code, sandbox_df)
    if check.status != "ok":
        # Distinct, specific reason per status -- this is exactly what gets fed back to the model
        # for its one auto-revise (llm_chat.py) and what a human reads if both attempts fail.
        raise StrategySpecError(f"[{check.status}] {check.error_message}")

    signal_array = np.asarray(check.signal, dtype=bool)
    if len(signal_array) != len(full_df):
        raise StrategySpecError("internal error: signal length did not match the data length.")

    spec = {"entry_conditions": [], **exit_params}
    split_idx = int(len(full_df) * IN_SAMPLE_FRACTION)

    in_sample_df = full_df.iloc[:split_idx].reset_index(drop=True)
    in_sample_signal = signal_array[:split_idx]
    out_sample_df = full_df.iloc[split_idx:].reset_index(drop=True)
    out_sample_signal = signal_array[split_idx:]

    in_sample_trades = _simulate_trades(in_sample_df, spec, signal_override=in_sample_signal)
    out_sample_trades = _simulate_trades(out_sample_df, spec, signal_override=out_sample_signal)

    return {
        "code": code,
        "in_sample": _full_backtest_metrics(in_sample_trades, _elapsed_years(in_sample_df)),
        "out_of_sample": _full_backtest_metrics(out_sample_trades, _elapsed_years(out_sample_df)),
        "split": {
            "in_sample_bars": len(in_sample_df),
            "out_of_sample_bars": len(out_sample_df),
            "in_sample_fraction": IN_SAMPLE_FRACTION,
        },
        "disclosure": "SHADOW/RESEARCH ONLY -- historical simulation, not investment advice, no "
                       "guarantee of future results. This is ONE hand-written idea tested ONCE -- "
                       "far weaker evidence than a systematic sweep, and out-of-sample is the "
                       "honest test of whether it generalizes at all.",
    }


def run_generated_backtest(args: dict) -> dict:
    return _run_generated_backtest_impl(args, GENERATED_SIGNAL_COLUMNS)


# MISSION raw-data-research: same harness, same causality guard, same train/test split, same cost
# model -- the ONLY thing different from run_generated_backtest is the column whitelist
# (RAW_ONLY_SIGNAL_COLUMNS, no precomputed order-flow features). Deliberately not a new backtest
# engine -- reusing the exact machinery every other strategy in this app already goes through and
# that Phase 1/the literature-research task already validated.
def run_raw_generated_backtest(args: dict) -> dict:
    return _run_generated_backtest_impl(args, RAW_ONLY_SIGNAL_COLUMNS)


RAW_GENERATED_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "run_raw_generated_backtest",
        "description": (
            "Backtest a trading signal derived ONLY from raw/near-raw bar data -- no precomputed "
            "order-flow features (no mlofi/vpin/sweep/delta/cusum/etc.). You supply ONE pure "
            "Python function -- def signal(df): -- returning a boolean Series, True on bars where "
            "a position should be entered. If your signal depends on a quantity you derived from "
            "raw depth/quote/trade data (via read_trades_window/read_bbo_window/"
            "reconstruct_book_at/run_raw_analysis), compute that quantity's logic INSIDE this same "
            "signal(df) function using only the columns listed below -- this tool has no separate "
            "column-merging step. The harness handles everything else (the one-bar shift so your "
            "signal can never act on its own bar, the train/test split, exits, PnL, costs, "
            "metrics). Checked for look-ahead bias the same way as every other generated-signal "
            "tool in this app: re-run on truncated history, outputs compared. Available columns: " +
            ", ".join(RAW_ONLY_SIGNAL_COLUMNS) + ". Only pandas (pd), numpy (np), and math are "
            "available -- no other imports, no file or network access. Returns in-sample and "
            "out-of-sample metrics together, always -- report both, never in-sample alone."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Exactly one function: 'def signal(df):\\n    ...\\n    return "
                                   "<boolean Series>'. No imports besides pandas/numpy/math, no "
                                   "other top-level statements.",
                },
                "entry_side": {"type": "string", "enum": ["long", "short"]},
                "exit_hold_bars": {"type": "integer", "description": f"1-{MAX_HOLD_BARS}."},
                "stop_points": {"type": "number", "description": "Optional stop-loss distance in points."},
                "target_points": {"type": "number", "description": "Optional profit-target distance in points."},
            },
            "required": ["code", "entry_side", "exit_hold_bars"],
        },
    },
}


# =============================================================================================
# MISSION strategy-lab-codegen-primary: the code-generation replacement for the retired JSON-grid
# sweep_backtest. The model writes ONE parameterised function -- def signal(df, <param names>): --
# and a param_grid of candidate values per parameter; the harness (not the model) computes the
# Cartesian product and calls signal() once per combination, batched into a single sandboxed
# subprocess (see signal_sandbox.run_param_grid_with_leakage_guard). Causality is checked once
# against a representative combo, not once per combo -- it's a property of the code, not the
# numbers plugged into it. Reuses run_sweep's old reporting shape (top-N in/out-of-sample,
# distribution, expected-max-Sharpe-under-null deflation) via the same _quick_stats/
# _expected_max_sharpe_under_null helpers above, just scored on a param combo's boolean array
# instead of an entry_conditions mask.
# =============================================================================================

GENERATED_SWEEP_MAX_COMBINATIONS = 200
GENERATED_SWEEP_SANDBOX_TIMEOUT_S = 30.0

SWEEP_GENERATED_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "sweep_generated_backtest",
        "description": (
            "Search a GRID of a model-authored signal function against real historical NQ bars -- "
            "use this instead of run_generated_backtest whenever you want to search/optimize/tune "
            "a threshold or parameter in a strategy that isn't a simple column/operator/value "
            "condition (a derived level, a session filter, a multi-step pattern), the same way "
            "you'd use a sweep for a simple condition. You write ONE function -- "
            "def signal(df, <param names>): -- taking df plus EXACTLY the parameter names declared "
            "in param_grid, returning a boolean Series; the harness calls it once per combination "
            "of your declared parameter values -- you never write the loop over parameters "
            "yourself, only the single-combination logic. The harness handles the rest: shifting "
            "your signal by one bar, the train/test split, exits, PnL, and metrics. Your function "
            "is checked for look-ahead bias ONCE (causality is a property of the code, not of "
            "which parameter values are used). " + SESSION_HELP_TEXT + " Available columns: " +
            ", ".join(GENERATED_SIGNAL_COLUMNS) + ". Only pandas (pd), numpy (np), and math are "
            "available -- no other imports. Returns the same shape as a sweep: top candidates in "
            "both windows, the full distribution, combinations tested, and the expected best "
            "Sharpe pure random search would produce by chance alone. Never invent or estimate any "
            "of these numbers yourself -- only report exactly what this tool returns."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Exactly one function: 'def signal(df, <param names>):\\n    ...\\n"
                                   "    return <boolean Series>'. Parameter names (besides df) must "
                                   "exactly match param_grid's keys.",
                },
                "param_grid": {
                    "type": "object",
                    "description": "Maps each parameter name (must match a signal() argument name "
                                    "exactly, and must not be 'df') to a list of candidate numeric "
                                    "values to sweep.",
                    "additionalProperties": {
                        "type": "array", "items": {"type": "number"},
                        "minItems": 1, "maxItems": MAX_GRID_VALUES_PER_FIELD,
                    },
                },
                "entry_side": {"type": "string", "enum": ["long", "short"]},
                "exit_hold_bars": {"type": "integer", "description": f"1-{MAX_HOLD_BARS}."},
                "stop_points": {"type": "number", "description": "Optional stop-loss distance in points."},
                "target_points": {"type": "number", "description": "Optional profit-target distance in points."},
            },
            "required": ["code", "param_grid", "entry_side", "exit_hold_bars"],
        },
    },
}


def _validate_param_grid(raw_grid) -> list[dict]:
    """Returns the full Cartesian product as a list of concrete {param_name: value} combo dicts.
    Raises StrategySpecError -- with the actual combination count -- BEFORE running anything if
    the grid is too large or malformed."""
    if not isinstance(raw_grid, dict) or not raw_grid:
        raise StrategySpecError("param_grid must be a non-empty object mapping parameter names to lists of numbers.")

    names = sorted(raw_grid.keys())
    for name in names:
        if not name.isidentifier() or name == "df":
            raise StrategySpecError(
                f"param_grid key {name!r} is not a valid Python parameter name, or is 'df' (reserved "
                f"for the dataframe argument)."
            )
        vals = raw_grid[name]
        if not isinstance(vals, list) or not vals:
            raise StrategySpecError(f"param_grid[{name!r}] must be a non-empty list of numbers.")
        if len(vals) > MAX_GRID_VALUES_PER_FIELD:
            raise StrategySpecError(f"param_grid[{name!r}] may have at most {MAX_GRID_VALUES_PER_FIELD} candidates.")
        try:
            [float(v) for v in vals]
        except (TypeError, ValueError):
            raise StrategySpecError(f"param_grid[{name!r}] values must all be numbers.")

    value_lists = [[float(v) for v in raw_grid[name]] for name in names]
    combos = [dict(zip(names, combo)) for combo in itertools.product(*value_lists)]
    if len(combos) > GENERATED_SWEEP_MAX_COMBINATIONS:
        raise StrategySpecError(
            f"This grid has {len(combos)} combinations, over the {GENERATED_SWEEP_MAX_COMBINATIONS}-"
            f"combination cap for a generated-code sweep. Use fewer candidate values per parameter."
        )
    return combos


def run_generated_sweep(args: dict) -> dict:
    if not isinstance(args, dict):
        raise StrategySpecError("Arguments must be a JSON object.")
    code = args.get("code")
    if not isinstance(code, str) or not code.strip():
        raise StrategySpecError("code must be a non-empty string containing exactly one function, signal(df, ...).")
    combos = _validate_param_grid(args.get("param_grid"))
    exit_params = _validate_exit_params(args)

    full_df = _load_df()
    sandbox_df = full_df[GENERATED_SIGNAL_COLUMNS].copy()

    check = signal_sandbox.run_param_grid_with_leakage_guard(
        code, sandbox_df, combos, timeout_s=GENERATED_SWEEP_SANDBOX_TIMEOUT_S,
    )
    if check.status != "ok":
        raise StrategySpecError(f"[{check.status}] {check.error_message}")

    split_idx = int(len(full_df) * IN_SAMPLE_FRACTION)
    in_sample_df = full_df.iloc[:split_idx].reset_index(drop=True)
    out_sample_df = full_df.iloc[split_idx:].reset_index(drop=True)
    spec = {"entry_conditions": [], **exit_params}

    scored = []
    zero_or_failed = 0
    for combo, signal_list in zip(combos, check.combo_signals):
        if signal_list is None:
            zero_or_failed += 1
            continue
        signal_array = np.asarray(signal_list, dtype=bool)
        # Degeneracy checked over the FULL series (same convention as the single-signal guard in
        # signal_sandbox.py, reusing its threshold) BEFORE splitting -- a combo that never/almost-
        # always fires is excluded from ranking, not treated as a hard error for the whole grid.
        fraction_true = float(signal_array.mean()) if len(signal_array) else 0.0
        if fraction_true == 0.0 or fraction_true > signal_sandbox.MAX_SIGNAL_FRACTION:
            zero_or_failed += 1
            continue
        in_signal = signal_array[:split_idx]
        stats = _quick_stats(in_sample_df, spec, signal_override=in_signal)
        if stats is None or stats["sharpe_annualized"] is None:
            zero_or_failed += 1
            continue
        scored.append((combo, signal_array, stats))

    if not scored:
        return {
            "combinations_tested": len(combos),
            "combinations_with_zero_trades": zero_or_failed,
            "message": "No parameter combination in this grid produced enough in-sample trades to "
                       "compute a Sharpe ratio and rank. Try wider parameter ranges.",
            "disclosure": "SHADOW/RESEARCH ONLY -- historical simulation, not investment advice, no "
                           "guarantee of future results.",
        }

    scored.sort(key=lambda triple: triple[2]["sharpe_annualized"], reverse=True)
    top = scored[:TOP_N_FOR_OOS]

    top_results = []
    for combo, signal_array, in_stats in top:
        out_signal = signal_array[split_idx:]
        oos_stats = _quick_stats(out_sample_df, spec, signal_override=out_signal)
        top_results.append({
            "params": combo,
            "in_sample": in_stats,
            "out_of_sample": oos_stats if oos_stats is not None else {
                "trade_count": 0, "message": "No trades out-of-sample.",
            },
        })

    all_sharpes = np.array([s["sharpe_annualized"] for _, _, s in scored])
    profitable = sum(1 for _, _, s in scored if s["total_pnl_points"] > 0)
    median_n_trades = int(np.median([s["trade_count"] for _, _, s in scored]))
    median_trades_per_year = float(np.median([s["sharpe_trades_per_year_assumed"] for _, _, s in scored]))
    expected_max_sharpe = _expected_max_sharpe_under_null(len(scored), median_n_trades, median_trades_per_year)

    return {
        "code": code,
        "combinations_tested": len(combos),
        "combinations_scored": len(scored),
        "combinations_with_zero_trades": zero_or_failed,
        "top_results": top_results,
        "distribution": {
            "median_sharpe_annualized": round(float(np.median(all_sharpes)), 3),
            "q1_sharpe_annualized": round(float(np.percentile(all_sharpes, 25)), 3),
            "q3_sharpe_annualized": round(float(np.percentile(all_sharpes, 75)), 3),
            "profitable_combinations": profitable,
            "profitable_fraction": round(profitable / len(scored), 3),
        },
        "expected_max_sharpe_under_null": round(expected_max_sharpe, 3) if expected_max_sharpe is not None else None,
        "deflation_method": (
            "Expected Maximum Sharpe Ratio under the null of zero true skill across N independent "
            "trials (Bailey, Borwein, Lopez de Prado & Zhu, 'The Probability of Backtest "
            "Overfitting', 2014). Assumes iid Gaussian per-trade returns under the null; the "
            f"per-trial standard error uses the median in-sample trade count ({median_n_trades}) "
            f"across the {len(scored)} scored combinations as a representative sample size, "
            f"annualized at their median realized trade frequency ({median_trades_per_year:.0f}/yr)."
        ),
        "split": {
            "in_sample_bars": len(in_sample_df),
            "out_of_sample_bars": len(out_sample_df),
            "in_sample_fraction": IN_SAMPLE_FRACTION,
        },
        "causality_check": "Verified once against a representative parameter combination -- "
                            "causality is a property of the code's logic, not of which parameter "
                            "values are plugged in.",
        "disclosure": "SHADOW/RESEARCH ONLY -- historical simulation, not investment advice, no "
                       "guarantee of future results. Ranked on IN-SAMPLE data only -- the "
                       "out-of-sample figures are the honest test of whether a result generalizes.",
    }
