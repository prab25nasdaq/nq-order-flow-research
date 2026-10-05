"""MISSION agent-replay: let the model trade the historical NQ dataset on its own -- no
pre-written strategy, no condition supplied. It observes bars up to a moving cursor, writes and
runs its own analysis code, decides its own entries/exits/sizing, and revises as it goes. Pure
historical research; nothing here touches a broker or any live order path.

Phase 1 scope: the make-or-break safety mechanism (the cursor + truncated-data sandbox) and the
minimal end-to-end decision loop, sized to time 10-20 real decisions before committing to a full
multi-window run. NOT yet built: the multi-window/multi-run experiment orchestration, the
benchmark suite, held-out evaluation -- those are later phases, once the core loop's timing and
correctness are confirmed live.

THE ONE INVARIANT THIS FILE EXISTS TO PROTECT: the agent must be PHYSICALLY unable to see data
beyond the cursor, not merely instructed not to. Every path that hands the model (or the sandbox
running the model's code) a dataframe goes through ReplayWindow.visible_df(), which always slices
to `cursor + 1` -- nothing upstream of that call ever holds a reference to the unsliced window. Do
not add a code path that hands out `self.df` directly; that would be a leak of the same class this
whole design exists to close (see signal_sandbox.py's own leakage-guard for the analogous concern
on the backtesting side, which this module deliberately does NOT reuse -- that guard proves a
function COULD have leaked; this module structurally prevents the data from being reachable at
all, a stronger and simpler guarantee for a genuinely sequential process).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
import pandas as pd

from . import signal_sandbox
from . import strategy_lab

log = logging.getLogger("webbeta.agent_replay")

LLM_BASE_URL = os.environ.get("WEBBETA_LLM_BASE_URL", "http://localhost:8080/v1").rstrip("/")
LLM_API_KEY = os.environ.get("WEBBETA_LLM_API_KEY", "")
MAX_TOKENS_CAP = 4096
MAX_TOOL_ROUNDTRIPS_PER_DECISION = 6  # bounds worst-case latency/cost of one decision cycle

# --------------------------------------------------------------------------------------- economics
# Real CME E-mini NQ contract specs (confirmed against this dataset's own contract_symbol column:
# NQM6/NQU6 -- full-size E-mini, not Micro). strategy_lab.py's existing _simulate_trades/
# run_backtest have NEVER modeled slippage or commission (grepped: zero matches for either term in
# that file) -- applying real costs ONLY to the agent's own trades here would make the four-way
# benchmark comparison unfair. apply_trading_costs() below is applied identically to whatever
# trade list ANY of the four benchmarks (baseline/random/buy-hold/agent) produces, as a shared
# post-processing step, never inside strategy_lab.py itself.
TICK_SIZE_POINTS = 0.25
TICK_VALUE_USD = 5.00
POINT_VALUE_USD = TICK_VALUE_USD / TICK_SIZE_POINTS  # 20.00
SLIPPAGE_TICKS_PER_SIDE = 1  # one tick against the trader, both entry and exit
SLIPPAGE_POINTS_PER_SIDE = TICK_SIZE_POINTS * SLIPPAGE_TICKS_PER_SIDE  # 0.25
COMMISSION_USD_PER_SIDE = 2.50  # a commonly-cited approximate NQ round-turn commission (~$5 RT)
COMMISSION_POINTS_PER_SIDE = COMMISSION_USD_PER_SIDE / POINT_VALUE_USD  # 0.125
ROUND_TURN_COST_POINTS = 2 * (SLIPPAGE_POINTS_PER_SIDE + COMMISSION_POINTS_PER_SIDE)  # 0.75 pts


def apply_trading_costs(trades: list[dict]) -> list[dict]:
    """Deducts ROUND_TURN_COST_POINTS from every trade's pnl_points. Applied identically
    regardless of which benchmark produced the trade -- see the economics comment above."""
    out = []
    for t in trades:
        t = dict(t)
        t["pnl_points_gross"] = t["pnl_points"]
        t["pnl_points"] = round(t["pnl_points"] - ROUND_TURN_COST_POINTS, 4)
        out.append(t)
    return out


# ----------------------------------------------------------------------------------- agent's view
# Deliberately a small, curated subset -- OHLC (what PnL is based on) plus a handful of order-flow
# features, not all ~129 raw columns. read_bars() is meant to be a cheap, frequent glance; anything
# needing more history/columns is what run_analysis() (against the SAME cursor-truncated frame) is
# for.
AGENT_VIEW_COLUMNS = [
    "bar_index", "continuous_open", "continuous_high", "continuous_low", "continuous_close",
    "mlofi_norm", "delta_norm", "vpin", "buy_ratio",
]


@dataclass
class AuditEntry:
    t_wall: float
    cursor: int
    tool: str
    detail: str


@dataclass
class ReplayWindow:
    """One contiguous slice of the historical dataframe, with a cursor the harness alone advances.
    `df` is the FULL window slice and is harness-internal ONLY -- nothing outside this class's own
    methods may ever read `self.df` directly. Every public method here returns (or hands the
    sandbox) `visible_df()`, never `df`."""
    df: pd.DataFrame  # index-reset, 0..len(df)-1 -- cursor is an offset into THIS, not bar_index
    cursor: int
    audit_log: list[AuditEntry] = field(default_factory=list)

    def _log(self, tool: str, detail: str = "") -> None:
        self.audit_log.append(AuditEntry(t_wall=time.time(), cursor=self.cursor, tool=tool, detail=detail))

    def visible_df(self) -> pd.DataFrame:
        """The ONLY way a dataframe leaves this class. Always truncated at the cursor, inclusive."""
        return self.df.iloc[: self.cursor + 1]

    def read_bars(self, n: int) -> list[dict]:
        n = max(1, min(int(n), 200))
        self._log("read_bars", f"n={n}")
        view = self.visible_df()[AGENT_VIEW_COLUMNS].tail(n)
        return view.round(4).to_dict("records")

    def run_analysis(self, code: str) -> signal_sandbox.AnalysisResult:
        self._log("run_analysis", f"code_len={len(code)}")
        return signal_sandbox.run_analysis_in_sandbox(code, self.visible_df())

    def bar_at(self, offset: int) -> pd.Series:
        """Harness-internal only (execution/fills) -- may look past the PUBLIC cursor by a few
        bars during cursor advancement, since advancing the cursor and filling/checking orders
        against the newly-revealed bars is the harness's own job, not the agent's. The agent never
        calls this; it has no tool that does."""
        return self.df.iloc[offset]

    def advance_cursor(self, new_cursor: int) -> None:
        if new_cursor < self.cursor:
            raise ValueError("cursor must never move backward")
        if new_cursor >= len(self.df):
            raise ValueError("cursor cannot exceed the window")
        self.cursor = new_cursor
        self._log("advance_cursor")


# --------------------------------------------------------------------------------------- position
@dataclass
class Trade:
    entry_bar: int
    exit_bar: int
    entry_px: float
    exit_px: float
    pnl_points: float
    hold_bars: int
    exit_reason: str
    direction: str


@dataclass
class OpenPosition:
    direction: str  # "long" | "short"
    entry_bar: int
    entry_px: float
    stop_points: Optional[float]
    target_points: Optional[float]


@dataclass
class PositionTracker:
    """Sequential (not vectorized) position/PnL accounting. strategy_lab._simulate_trades cannot
    be reused directly here -- it assumes the entire signal is known in advance and simulates the
    whole window in one vectorized pass, the opposite of "one order arrives at a time as decisions
    happen." Execution semantics deliberately MIRROR _simulate_trades's own logic exactly (fill at
    the NEXT bar's open, stop/target checked via each subsequent bar's high/low, worse-outcome-wins
    on a same-bar stop+target ambiguity) so the agent's trades are computed the same way the
    benchmarks are -- a different-looking simulator that happened to agree wouldn't be good enough
    for a fair comparison."""
    position: Optional[OpenPosition] = None
    trades: list[Trade] = field(default_factory=list)

    def is_flat(self) -> bool:
        return self.position is None

    def open(self, direction: str, entry_bar: int, entry_px: float,
             stop_points: Optional[float], target_points: Optional[float]) -> None:
        if self.position is not None:
            raise ValueError("cannot open a new position while one is already open")
        self.position = OpenPosition(direction, entry_bar, entry_px, stop_points, target_points)

    def check_bar(self, bar_idx: int, high: float, low: float) -> Optional[Trade]:
        """Called once per bar as the cursor advances while a position is open. Returns the
        closed Trade if this bar's high/low triggered the stop or target, else None."""
        pos = self.position
        if pos is None:
            return None
        is_long = pos.direction == "long"
        hit_stop = pos.stop_points is not None and (
            (low <= pos.entry_px - pos.stop_points) if is_long else (high >= pos.entry_px + pos.stop_points)
        )
        hit_target = pos.target_points is not None and (
            (high >= pos.entry_px + pos.target_points) if is_long else (low <= pos.entry_px - pos.target_points)
        )
        if not (hit_stop or hit_target):
            return None
        if hit_stop:  # stop wins on a same-bar ambiguity, matching _simulate_trades's own choice
            exit_px = pos.entry_px - pos.stop_points if is_long else pos.entry_px + pos.stop_points
            reason = "stop"
        else:
            exit_px = pos.entry_px + pos.target_points if is_long else pos.entry_px - pos.target_points
            reason = "target"
        return self._close(bar_idx, exit_px, reason)

    def close_at_market(self, bar_idx: int, price: float, reason: str) -> Trade:
        return self._close(bar_idx, price, reason)

    def _close(self, bar_idx: int, exit_px: float, reason: str) -> Trade:
        pos = self.position
        pnl = (exit_px - pos.entry_px) if pos.direction == "long" else (pos.entry_px - exit_px)
        trade = Trade(
            entry_bar=pos.entry_bar, exit_bar=bar_idx, entry_px=round(pos.entry_px, 2),
            exit_px=round(exit_px, 2), pnl_points=round(pnl, 2),
            hold_bars=bar_idx - pos.entry_bar, exit_reason=reason, direction=pos.direction,
        )
        self.trades.append(trade)
        self.position = None
        return trade


# ------------------------------------------------------------------------------------- agent I/O
# MISSION agent-replay: no anonymization (dropped per review -- the data is 2026-06..08, after
# this model's training cutoff, so there is nothing real to recall; offsetting prices would only
# risk corrupting the agent's own legitimate analysis for no benefit). Real timestamps and real
# prices, stated explicitly to the agent so it knows this and isn't left to guess.
AGENT_SYSTEM_PROMPT = (
    "You are an autonomous trading agent in a research experiment on Cliff View Capital's "
    "historical NQ futures dataset. SHADOW/RESEARCH ONLY -- nothing here touches a broker or any "
    "live order path; this is pure historical replay.\n\n"
    "You observe bars as they arrive, up to a moving cursor that only the harness advances. You "
    "have never seen, and structurally cannot see, anything past the cursor -- this is enforced, "
    "not a rule you are simply asked to follow. Prices and timestamps are REAL, not anonymized -- "
    "this is the actual NQ futures market from June-August 2026. Your training predates this "
    "period, so you have no memory of these specific price moves to draw on; treat this as "
    "genuinely new data, not something to pattern-match against a memorized outcome.\n\n"
    "You have three tools:\n"
    "- read_bars(n): the last n bars up to the cursor -- OHLC plus a few order-flow features "
    "(mlofi_norm, delta_norm, vpin, buy_ratio). A cheap, frequent glance.\n"
    "- run_analysis(code): write exactly one function, def analyze(df):, and it runs against the "
    "FULL history up to the cursor (not just the recent window read_bars shows), inside a "
    "genuinely sandboxed, isolated process -- no network, no file access, only pandas (pd), "
    "numpy (np), and math. Return a JSON-serializable value: a number, a short dict, a short "
    "list -- a summary, not raw per-row data. Nothing persists between run_analysis calls; it is "
    "stateless compute, not a place to store anything.\n"
    "- decide(...): end this decision cycle -- the ONLY way to end it. Choose action "
    "(long/short/flat/hold), an optional stop/target in points, when to be woken next (after N "
    "bars, or immediately on a price move of M points, whichever comes first), your updated "
    "scratchpad (replaces the previous one entirely -- carry forward whatever you want to "
    "remember), and a note explaining your reasoning. The note is recorded verbatim and matters "
    "more than the trade itself -- this experiment is about whether your reasoning holds up, not "
    "just whether you got lucky.\n\n"
    "The harness owns everything else: your order fills at the NEXT bar's open, never the "
    "current bar's close; realistic slippage and commission are applied; your position and P&L "
    "are tracked for you. You can only be in one position at a time -- if you're already in one, "
    "a long/short action is ignored (say 'flat' first, or 'hold' to stay in it).\n\n"
    "Losing is an expected, legitimate outcome of an honest experiment. Do not force a trade to "
    "have something to show -- 'hold' with a long wake_after_bars is a completely legitimate "
    "decision when nothing looks worth acting on."
)


def _read_bars_tool_def() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "read_bars",
            "description": (
                "Read the most recent bars up to the current cursor (never beyond it). Returns "
                "OHLC and a few order-flow features per bar, oldest first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "description": "How many recent bars to read (max 200)."},
                },
                "required": ["n"],
            },
        },
    }


def _run_analysis_tool_def() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "run_analysis",
            "description": (
                "Run your own analysis against the FULL history up to the current cursor (not "
                "just what read_bars showed). Write exactly one function -- def analyze(df): -- "
                "returning a JSON-serializable value (a number, a short dict, or a short list, "
                "not raw per-row data). Only pandas (pd), numpy (np), and math are available; no "
                "other imports, no network, no file access. df has the same columns as "
                "read_bars for every bar up to the cursor: bar_index, continuous_open/high/low/"
                "close, mlofi_norm, delta_norm, vpin, buy_ratio."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "The analyze(df) function source, nothing else."},
                },
                "required": ["code"],
            },
        },
    }


DECIDE_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "decide",
        "description": (
            "End this decision cycle -- the ONLY way to end it; read_bars/run_analysis do not. "
            "Choose an action, an optional stop/target, and when to be woken for the next cycle."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string", "enum": ["long", "short", "flat", "hold"],
                    "description": (
                        "long/short: open a new position (ignored if you're already in one). "
                        "flat: close your current open position now (ignored if already flat). "
                        "hold: no change."
                    ),
                },
                "stop_points": {"type": "number", "description": "Stop-loss distance in points from entry (long/short only)."},
                "target_points": {"type": "number", "description": "Profit-target distance in points from entry (long/short only)."},
                "wake_after_bars": {"type": "integer", "description": "Wake me again after this many bars, whichever comes first with wake_on_price_move_points."},
                "wake_on_price_move_points": {"type": "number", "description": "Wake me immediately if price moves this many points from here, whichever comes first with wake_after_bars."},
                "scratchpad": {"type": "string", "description": "Your updated notes, replacing the previous scratchpad entirely."},
                "note": {"type": "string", "description": "Your reasoning for this decision, in your own words -- recorded verbatim."},
            },
            "required": ["action", "wake_after_bars", "note"],
        },
    },
}


@dataclass
class ToolCallRecord:
    name: str
    arguments: dict
    result: object


@dataclass
class DecisionRecord:
    decision_index: int
    cursor_at_start: int
    wall_seconds: float
    tool_calls: list[ToolCallRecord]
    decide_args: dict
    total_tokens: int


async def _call_model(client: httpx.AsyncClient, messages: list[dict], tools: list[dict],
                       temperature: float = 0.4) -> tuple[dict, dict]:
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    payload = {
        "model": "local", "messages": messages, "tools": tools, "tool_choice": "auto",
        "temperature": temperature, "max_tokens": MAX_TOKENS_CAP, "stream": False,
    }
    resp = await client.post(f"{LLM_BASE_URL}/chat/completions", headers=headers, json=payload,
                              timeout=httpx.Timeout(120.0, connect=10.0))
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"], data.get("usage") or {}


async def run_one_decision(client: httpx.AsyncClient, window: ReplayWindow, tracker: PositionTracker,
                            scratchpad: str, decision_index: int, current_offset: int) -> DecisionRecord:
    """Runs ONE decision cycle: a FRESH, short prompt (system + current state only -- no growing
    transcript across decisions, per the deliberately-forgetful context strategy), looping tool
    calls until the model calls decide(). Bounded by MAX_TOOL_ROUNDTRIPS_PER_DECISION so one stuck
    decision can't hang the whole experiment."""
    pos = tracker.position
    position_desc = "FLAT" if pos is None else (
        f"{pos.direction.upper()} from {pos.entry_px} "
        f"(stop={pos.stop_points}, target={pos.target_points})"
    )
    context_msg = (
        f"Decision #{decision_index}. Cursor is at window-offset {current_offset} "
        f"(bar_index {int(window.df['bar_index'].iloc[current_offset])}).\n"
        f"Position: {position_desc}\n"
        f"Closed trades so far: {len(tracker.trades)}, total pnl_points so far: "
        f"{round(sum(t.pnl_points for t in tracker.trades), 2)}\n"
        f"Your scratchpad: {scratchpad or '(empty)'}\n\n"
        "Use read_bars/run_analysis as needed, then call decide() to end this cycle."
    )
    messages = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": context_msg},
    ]
    tools = [_read_bars_tool_def(), _run_analysis_tool_def(), DECIDE_TOOL_DEF]
    tool_call_records: list[ToolCallRecord] = []
    total_tokens = 0
    t0 = time.time()

    for _attempt in range(MAX_TOOL_ROUNDTRIPS_PER_DECISION):
        message, usage = await _call_model(client, messages, tools)
        total_tokens += usage.get("total_tokens", 0) or 0
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            # Dead end (reasoning-only, no tool call) -- one corrective nudge, same precedent as
            # llm_chat.py's own MAX_DEAD_END_RETRIES handling of this exact failure mode.
            messages.append({"role": "assistant", "content": message.get("content") or ""})
            messages.append({"role": "user", "content": (
                "(Call one of your tools now -- read_bars, run_analysis, or decide.)"
            )})
            continue

        assistant_tool_calls = []
        tool_result_messages = []
        decide_args: Optional[dict] = None
        for i, tc in enumerate(tool_calls):
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if name == "read_bars":
                result = window.read_bars(args.get("n", 20))
            elif name == "run_analysis":
                r = window.run_analysis(args.get("code", ""))
                result = {"status": r.status, "result": r.result, "error_message": r.error_message}
            elif name == "decide":
                result = {"status": "ok"}
                decide_args = args
            else:
                result = {"error": f"unknown tool {name!r}"}
            tool_call_records.append(ToolCallRecord(name=name, arguments=args, result=result))
            call_id = tc.get("id") or f"call_{i}"
            assistant_tool_calls.append({
                "id": call_id, "type": "function",
                "function": {"name": name, "arguments": tc["function"].get("arguments") or "{}"},
            })
            tool_result_messages.append({
                "role": "tool", "tool_call_id": call_id, "content": json.dumps(result, default=str),
            })
        messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": assistant_tool_calls})
        messages.extend(tool_result_messages)

        if decide_args is not None:
            return DecisionRecord(decision_index, current_offset, time.time() - t0,
                                   tool_call_records, decide_args, total_tokens)

    # Exhausted retries without a decide() call -- force a safe default rather than hang the
    # experiment on one stuck decision. Recorded plainly as forced, not silently.
    forced = {"action": "hold", "wake_after_bars": 5,
              "note": "(forced: model never called decide() within the round-trip budget)"}
    return DecisionRecord(decision_index, current_offset, time.time() - t0,
                           tool_call_records, forced, total_tokens)


def apply_decision_and_advance(window: ReplayWindow, tracker: PositionTracker,
                                decide_args: dict, current_offset: int) -> tuple[list[dict], int]:
    """Applies the agent's decide() call, then advances the cursor bar-by-bar (in-process, no
    model calls) until a wake condition is met, an early stop/target fill occurs (itself treated
    as a wake condition -- being flat again is a new circumstance worth a fresh decision), or the
    window ends. Returns (events, new_offset)."""
    action = decide_args.get("action", "hold")
    stop_points = decide_args.get("stop_points")
    target_points = decide_args.get("target_points")
    wake_after_bars = max(1, int(decide_args.get("wake_after_bars") or 10))
    wake_on_move = decide_args.get("wake_on_price_move_points")

    events: list[dict] = []
    fill_offset = current_offset + 1
    if fill_offset >= len(window.df):
        return events, current_offset  # window exhausted

    fill_bar = window.bar_at(fill_offset)
    fill_open = float(fill_bar["continuous_open"])

    if action in ("long", "short") and tracker.is_flat():
        tracker.open(action, entry_bar=fill_offset, entry_px=fill_open,
                     stop_points=stop_points, target_points=target_points)
        events.append({"type": "entry", "bar": fill_offset, "px": fill_open, "direction": action})
    elif action == "flat" and not tracker.is_flat():
        trade = tracker.close_at_market(fill_offset, fill_open, reason="agent_flat")
        events.append({"type": "exit", "bar": fill_offset, "px": fill_open,
                        "reason": "agent_flat", "pnl_points": trade.pnl_points})
    # "hold", or long/short while already positioned, or flat while already flat: no order change.

    ref_price = fill_open
    offset = fill_offset
    while True:
        bar = window.bar_at(offset)
        if not tracker.is_flat():
            trade = tracker.check_bar(offset, float(bar["continuous_high"]), float(bar["continuous_low"]))
            if trade is not None:
                events.append({"type": "exit", "bar": offset, "px": trade.exit_px,
                                "reason": trade.exit_reason, "pnl_points": trade.pnl_points})
                window.advance_cursor(offset)
                return events, offset  # early wake: the stop/target fill itself is new information

        bars_elapsed = offset - fill_offset + 1
        price_moved = abs(float(bar["continuous_close"]) - ref_price)
        woke_on_bars = bars_elapsed >= wake_after_bars
        woke_on_move = wake_on_move is not None and price_moved >= float(wake_on_move)
        if woke_on_bars or woke_on_move or offset >= len(window.df) - 1:
            window.advance_cursor(offset)
            return events, offset
        offset += 1


# --------------------------------------------------------------------------------------- pilot run
@dataclass
class PilotResult:
    decisions: list[DecisionRecord]
    events: list[dict]
    trades: list[dict]  # after apply_trading_costs
    wall_seconds_total: float


async def run_pilot(window_start_bar_index: int, window_bars: int, max_decisions: int) -> PilotResult:
    """MISSION agent-replay Phase 1: runs up to max_decisions real decisions (or until the window
    is exhausted) and returns full timing + records. This is the calibration pilot the mission's
    own Phase 0 asked for BEFORE committing to a full run size -- not the full experiment."""
    df = strategy_lab._load_df()
    train_end = int(len(df) * 0.7)
    start_pos = df.index[df["bar_index"] == window_start_bar_index]
    if len(start_pos) == 0:
        raise ValueError(f"bar_index {window_start_bar_index} not found in dataset")
    start_pos = int(start_pos[0])
    end_pos = min(start_pos + window_bars, train_end, len(df))
    if end_pos <= start_pos:
        raise ValueError("window is empty after clamping to the training split")

    window_df = df.iloc[start_pos:end_pos].reset_index(drop=True)
    window = ReplayWindow(df=window_df, cursor=0)
    tracker = PositionTracker()
    scratchpad = ""

    decisions: list[DecisionRecord] = []
    all_events: list[dict] = []
    t_start = time.time()

    async with httpx.AsyncClient() as client:
        offset = 0
        for i in range(max_decisions):
            if offset >= len(window_df) - 2:
                log.info("pilot stopping early: window exhausted at decision %d", i)
                break
            record = await run_one_decision(client, window, tracker, scratchpad, i, offset)
            decisions.append(record)
            scratchpad = record.decide_args.get("scratchpad", scratchpad) or scratchpad
            events, offset = apply_decision_and_advance(window, tracker, record.decide_args, offset)
            all_events.extend(events)
            log.info(
                "decision %d: action=%s wall=%.1fs tokens=%d tool_calls=%d events=%s",
                i, record.decide_args.get("action"), record.wall_seconds, record.total_tokens,
                len(record.tool_calls), [e["type"] for e in events],
            )

        # close out any still-open position at the window's last bar for clean final accounting
        if not tracker.is_flat():
            last = window.bar_at(len(window_df) - 1)
            trade = tracker.close_at_market(len(window_df) - 1, float(last["continuous_close"]), "window_end")
            all_events.append({"type": "exit", "bar": len(window_df) - 1, "px": trade.exit_px,
                                "reason": "window_end", "pnl_points": trade.pnl_points})

    trades_raw = [
        {"entry_bar": t.entry_bar, "exit_bar": t.exit_bar, "entry_px": t.entry_px, "exit_px": t.exit_px,
         "pnl_points": t.pnl_points, "hold_bars": t.hold_bars, "exit_reason": t.exit_reason,
         "direction": t.direction}
        for t in tracker.trades
    ]
    trades = apply_trading_costs(trades_raw)
    return PilotResult(decisions=decisions, events=all_events, trades=trades,
                        wall_seconds_total=time.time() - t_start)


def _pilot_result_to_dict(result: PilotResult) -> dict:
    """Full-detail serialization -- every tool call's actual arguments (including the real
    analyze(df) code submitted) and actual results, every decide() call's full note/scratchpad
    text, untruncated. The summary printer below only ever showed truncated previews of this same
    already-collected data; this is the first time it's written out in full."""
    return {
        "wall_seconds_total": result.wall_seconds_total,
        "decisions": [
            {
                "decision_index": d.decision_index,
                "cursor_at_start": d.cursor_at_start,
                "wall_seconds": d.wall_seconds,
                "total_tokens": d.total_tokens,
                "tool_calls": [
                    {"name": tc.name, "arguments": tc.arguments, "result": tc.result}
                    for tc in d.tool_calls
                ],
                "decide_args": d.decide_args,
            }
            for d in result.decisions
        ],
        "events": result.events,
        "trades": result.trades,
    }


def _print_pilot_summary(result: PilotResult) -> None:
    print(f"\n{'=' * 70}\nPILOT SUMMARY\n{'=' * 70}")
    print(f"Decisions completed: {len(result.decisions)}")
    print(f"Total wall time: {result.wall_seconds_total:.1f}s")
    if result.decisions:
        wall_times = [d.wall_seconds for d in result.decisions]
        tokens = [d.total_tokens for d in result.decisions]
        print(f"Per-decision wall time: min={min(wall_times):.1f}s max={max(wall_times):.1f}s "
              f"mean={sum(wall_times) / len(wall_times):.1f}s")
        print(f"Per-decision tokens: min={min(tokens)} max={max(tokens)} "
              f"mean={sum(tokens) / len(tokens):.0f}")
        actions = [d.decide_args.get("action") for d in result.decisions]
        print(f"Actions taken: {dict((a, actions.count(a)) for a in set(actions))}")
        tool_call_counts = [len(d.tool_calls) for d in result.decisions]
        print(f"Tool calls per decision: min={min(tool_call_counts)} max={max(tool_call_counts)} "
              f"mean={sum(tool_call_counts) / len(tool_call_counts):.1f}")
    print(f"\nTrades: {len(result.trades)}")
    for t in result.trades:
        print(f"  {t['direction']:5s} bar {t['entry_bar']:>6d}->{t['exit_bar']:<6d} "
              f"{t['entry_px']:>10.2f}->{t['exit_px']:<10.2f} "
              f"pnl={t['pnl_points']:+.2f} (gross {t['pnl_points_gross']:+.2f}) "
              f"[{t['exit_reason']}]")
    total_pnl = sum(t["pnl_points"] for t in result.trades)
    print(f"\nTotal net pnl_points (after costs): {total_pnl:+.2f}")
    print(f"\nAudit log entries: {len(result.decisions[0].tool_calls) if result.decisions else 0} "
          f"(first decision) -- see window.audit_log for the full cursor-position trail")
    print("\nDecision notes:")
    for d in result.decisions:
        print(f"  #{d.decision_index}: {d.decide_args.get('note', '')[:200]}")


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    parser = argparse.ArgumentParser(description="agent-replay Phase 1 timing pilot")
    parser.add_argument("--start-bar-index", type=int, default=5000,
                         help="bar_index to start the pilot window at (must be within the 70%% training split)")
    parser.add_argument("--window-bars", type=int, default=2000)
    parser.add_argument("--max-decisions", type=int, default=15)
    parser.add_argument("--out-json", type=str, default=None,
                         help="write full-detail decision/tool-call/trade records to this path")
    args = parser.parse_args()

    result = asyncio.run(run_pilot(args.start_bar_index, args.window_bars, args.max_decisions))
    _print_pilot_summary(result)
    if args.out_json:
        with open(args.out_json, "w") as f:
            json.dump(_pilot_result_to_dict(result), f, indent=2, default=str)
        print(f"\nFull detail written to {args.out_json}")
