"""MISSION literature-research Phase 2: the study loop.

An LLM reads the three-book corpus via search_literature (Phase 1) and the dataset's own facts
(describe_dataset()/describe_columns(), already computed by strategy_lab.py for every backtest --
not a second source of truth), then pre-registers falsifiable hypotheses about NQ order-flow
prediction BEFORE any of them is tested. Each hypothesis is recorded via record_hypothesis() the
moment the model commits to it -- not batched at the end -- so a hypothesis can never be quietly
reworded after Phase 3 sees its result. Ends only when the model calls finish_studying(), or when
MAX_TOOL_ROUNDTRIPS is exhausted (a safety bound on an unattended run, not a target to hit).

Each recorded hypothesis's `code`/`entry_side`/`exit_hold_bars`/`stop_points`/`target_points` are
in EXACTLY strategy_lab.run_generated_backtest()'s own argument shape -- Phase 3 feeds each
hypothesis into that function directly, unmodified. This is deliberate: the same causality-guarded,
train/test-split harness every other strategy in this app goes through, not a second bespoke one
built for this task.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

from . import literature_search
from . import strategy_lab

log = logging.getLogger("webbeta.literature_study")

LLM_BASE_URL = os.environ.get("WEBBETA_LLM_BASE_URL", "http://localhost:8080/v1").rstrip("/")
LLM_API_KEY = os.environ.get("WEBBETA_LLM_API_KEY", "")
MAX_TOKENS_CAP = 2048
MAX_TOOL_ROUNDTRIPS = 150  # generous -- "study as long as it wants" -- but an unattended run still
                            # needs a hard ceiling so a stuck loop can't run forever unsupervised.
OUT_DIR = Path(__file__).resolve().parent / "literature"

# MISSION literature-research Phase 2: first version of this budget (50,000 chars, derived from a
# ~4.3 chars/token guess applied to the WHOLE transcript) was wrong, caught live on the very first
# smoke test, not in production -- a single model turn firing three PARALLEL search_literature
# calls (k=8,6,6) produced a combined tool-result payload measuring 17,703 tokens, blowing through
# the model's 16,384-token context in ONE turn, before this module's own trim-between-groups logic
# ever got a chance to run (it only trims OLDER groups; the turn that just blew the budget IS the
# newest group, and trimming never touches that one). Measured directly via /tokenize: a single
# k=6 search_literature result is ~20,160 chars / ~4,256 tokens (~4.74 chars/token for this
# JSON-heavy tool-result content, not meaningfully different from prose -- the earlier guess's
# RATIO was fine; the bug was letting an unbounded number of full-size parallel calls into one
# turn with no per-turn ceiling at all). Two real fixes, not just a smaller constant:
#   1. literature_search.search_literature() now hard-caps k at MAX_K=6 regardless of what's
#      requested, so no single call can be larger than the measured ~4,256-token case.
#   2. MAX_SEARCHES_PER_TURN below bounds how many search_literature calls THIS module will
#      actually execute in one model turn, so a turn's total tool-result size has a known worst
#      case (MAX_SEARCHES_PER_TURN * ~4,256 tokens) instead of being open-ended.
# MAX_TOTAL_CHARS is now the between-turn trim threshold, sized so trimming kicks in comfortably
# before a few turns' worth of accumulated (now individually bounded) results could threaten the
# 16,384-token ceiling: 2 searches/turn * ~20,160 chars = ~40,320 chars is one turn's worst case;
# trimming at 40,000 total means at most one bounded turn is ever left untrimmed.
MAX_SEARCHES_PER_TURN = 2
MAX_TOTAL_CHARS = 40_000  # only used before any real measurement exists (round 1) -- see below

# MISSION literature-research Phase 2: a real production run crashed at round 61 after 60 good
# rounds and 2 recorded hypotheses -- lost entirely, because nothing caught the failure. Root cause
# (confirmed in the llama-server systemd journal, not guessed): "stop processing: n_tokens = 16383,
# truncated = 1" immediately followed by "Failed to parse tool call arguments as JSON: ... unexpected
# end of input" -- the request's real prompt size, which MAX_TOTAL_CHARS never accounted for (it
# only measured system_prompt + message groups, never the ~900-1200 token cost of the 4 tool JSON
# schemas sent on EVERY turn, nor real chat-template overhead), left so little headroom that the
# model's own tool-call JSON got physically cut off mid-argument when prompt+completion hit the
# 16,384-token ceiling -- llama-server enforces that ceiling as a hard physical limit regardless of
# the requested max_tokens, so it can truncate mid-generation rather than stopping cleanly.
# Real fix, not a smaller guessed constant: the chat-completions response already reports the EXACT
# real prompt size for every call (`usage.prompt_tokens`) -- use that measured truth directly
# instead of a char-based estimate for every round after the first. TARGET_PROMPT_TOKENS leaves
# real headroom: 16,384 - MAX_TOKENS_CAP(2048) - a 2,336-token safety margin (covers the parts of
# this MAX_TOKENS_CAP window generation can still legitimately use, plus any template/tokenizer
# rounding) = 12,000.
TARGET_PROMPT_TOKENS = 12_000
FALLBACK_CHARS_PER_TOKEN = 4.0  # only used for round 1, before any real usage.prompt_tokens exists
                                 # -- deliberately conservative (assumes MORE tokens per char than
                                 # every ratio measured this session, 4.24-5.89) so trimming errs
                                 # toward cutting more, not less, before real data is available.

# A single bad turn (a truncated-JSON 500, a transient connection error) must NEVER be allowed to
# kill an unattended run and discard everything recorded so far -- this is exactly what just
# happened. MAX_CALL_RETRIES bounds how many times one round retries (with progressively harder
# trimming each time) before the session ends gracefully -- with whatever was recorded still
# written to disk -- instead of raising past run_study_session and losing it all.
MAX_CALL_RETRIES = 3

# Hard cap on what update_notes will accept -- see the truncation logic in run_study_session's
# tool dispatch for the crash this is a direct response to (a notes argument long enough that
# GENERATING it hit the context ceiling mid-string, producing invalid truncated JSON).
NOTES_MAX_CHARS = 4000

# MISSION literature-research Phase 2: discovered live on the second real multi-round test (30
# rounds, logged in full) -- without this, the model searched near-duplicate queries (VPIN alone
# was re-searched with minor rewordings at least 8 times) for 18 STRAIGHT rounds after its last
# update_notes call and never recorded a single hypothesis, despite having already surfaced a
# concrete, quotable, testable finding early on (Cartea: price upticks followed by a downtick
# ~57% of the time). Not a plumbing bug -- the loop and tools worked correctly the whole time; the
# model itself just kept deferring commitment. NUDGE_INTERVAL rounds of no new record_hypothesis
# call triggers one explicit directive to commit to a hypothesis from what's already in its notes,
# the same "one corrective nudge for a stuck pattern" precedent used elsewhere in this codebase
# (llm_chat.py's dead-end retry, agent_replay.py's no-tool-call nudge) -- repeats every
# NUDGE_INTERVAL rounds for as long as the model keeps not-recording, rather than firing once.
NUDGE_INTERVAL = 8


STUDY_SYSTEM_PROMPT_TEMPLATE = (
    "You are a trading research analyst. Your job in this session is ONLY to read and think, not "
    "to trade or to judge outcomes -- you will pre-register falsifiable hypotheses about what "
    "should predict short-horizon NQ futures order-flow returns, and every one of them (including "
    "weak or speculative ones) will be tested afterward through a separate, deterministic backtest "
    "harness. You will not see any backtest result in this session -- that is the point of "
    "pre-registration: commit to a claim and a prediction BEFORE seeing whether it holds, so the "
    "hypothesis can't be quietly reworded to fit a result you already know.\n\n"
    "Dataset you are hypothesizing about:\n{dataset_desc}\n\n"
    "Columns available to condition a strategy on:\n{column_desc}\n\n"
    "IMPORTANT -- your context is finite: raw search results get trimmed out of this conversation "
    "as it grows, to make room for further searching. Once trimmed, that raw text is gone and "
    "cannot be re-read verbatim. Your `notes` (below, via update_notes) are the ONLY thing that "
    "survives trimming -- if you find something worth remembering, write it into your notes before "
    "moving on, or you will lose it. Do not just keep searching indefinitely without ever writing "
    "anything down; a few focused searches on a topic, a note capturing what you concluded, then "
    "either move to the next topic or record a hypothesis, is the intended rhythm.\n\n"
    "Your notes so far: {notes}\n\n"
    "You have four tools:\n"
    "- search_literature(query, k): searches a three-book corpus (Harris' Trading and Exchanges, "
    "Cartea/Jaimungal/Penalva's Algorithmic and High-Frequency Trading, and Hull's Options, "
    "Futures and Other Derivatives) and returns real passages with page numbers. Use it as much as "
    "you want -- read broadly before committing to anything -- but write down what you learn (see "
    "update_notes) before the raw text ages out of context.\n"
    "- update_notes(notes): REPLACES your notes entirely with the new text -- carry forward "
    "anything from the old notes you still want to keep, plus whatever you just learned. Use this "
    "to accumulate findings, page references, and half-formed ideas as you go, not just at the end. "
    "KEEP IT SHORT -- a few compact bullet points per finding (claim + book/page + one-line "
    "mechanism), not full paragraphs or long quoted passages. Notes over 4,000 characters get "
    "truncated, and a single very long response risks being cut off mid-generation before it's "
    "even valid JSON -- terse notes are safer AND more useful than exhaustive ones.\n"
    "- record_hypothesis(...): pre-registers ONE falsifiable hypothesis. Required fields: `claim` "
    "(plain language, one or two sentences), `source_book` and `source_pages` (exactly where this "
    "came from -- you must have actually retrieved this passage via search_literature, not recalled "
    "it from memory), `mechanism` (why this should apply to THIS data specifically -- name the "
    "actual column(s) involved and the causal story, not just 'the book says so'), "
    "`predicted_direction` and `predicted_magnitude` (state what you expect to see BEFORE it is "
    "tested -- e.g. direction='positive expectancy on the long side', magnitude='small, under 0.5 "
    "points/trade average' -- vague predictions ('it will work') are not acceptable), and a "
    "`code`/`entry_side`/`exit_hold_bars` (plus optional `stop_points`/`target_points`) block that "
    "is EXACTLY the same shape as this app's run_generated_backtest tool: `code` is one pure "
    "function `def signal(df): ...` returning a boolean Series, True where a position should be "
    "entered, using ONLY pandas (pd)/numpy (np)/math and ONLY these columns: " +
    ", ".join(strategy_lab.GENERATED_SIGNAL_COLUMNS) + ". " + strategy_lab.SESSION_HELP_TEXT + " "
    "Your signal will be checked for look-ahead bias exactly like every other strategy in this app "
    "-- it must not use any future bar's data. IMPORTANT: entry_side is ONE value (long OR short) "
    "applied to EVERY bar your signal returns True on -- there is no per-bar direction. If your "
    "hypothesis predicts a long entry under one condition AND a short entry under a different "
    "condition (e.g. 'buy-side imbalance predicts up, sell-side predicts down'), that is TWO "
    "separate hypotheses, each with its own single-direction signal(df) and its own entry_side -- "
    "do not OR a long-condition and a short-condition together into one signal function, since the "
    "harness would then apply only ONE entry_side to bars you meant to trade in opposite "
    "directions, silently testing something other than what you claimed. The same problem hides "
    "in a magnitude-only condition like abs(mlofi_norm) > 2 -- that fires on BOTH a strongly "
    "positive AND a strongly negative bar, but entry_side can still only apply one direction to "
    "all of them. If your mechanism implies opposite behavior on the two tails, split it into two "
    "hypotheses using the signed column directly (e.g. mlofi_norm > 2 for one, mlofi_norm < -2 for "
    "the other), not an abs() test paired with a single entry_side.\n"
    "- finish_studying(summary): ends this session. Call it once you've recorded as many "
    "hypotheses as you think are worth testing -- there is no minimum or maximum count, and "
    "recording a hypothesis you expect to fail is explicitly encouraged if the literature suggests "
    "a real, testable mechanism, even a weak one. Breadth is valuable here: the goal of Phase 3 is "
    "to test every hypothesis honestly, including the ones you don't expect to survive.\n\n"
    "Work at your own pace. Search as much as you want before recording anything."
)


def _build_system_prompt(notes: str) -> str:
    return STUDY_SYSTEM_PROMPT_TEMPLATE.format(
        dataset_desc=strategy_lab.describe_dataset(),
        column_desc=strategy_lab.describe_columns(),
        notes=notes or "(empty -- nothing recorded yet)",
    )


UPDATE_NOTES_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "update_notes",
        "description": (
            "Replace your running notes entirely with new text. This is the ONLY state that "
            "survives context trimming -- write down anything from a search you want to still "
            "have access to later, including page references. Carry forward whatever from your "
            "old notes you still want; this call replaces the whole thing, it doesn't append. "
            "KEEP IT SHORT: compact bullet points, not full paragraphs or long quotes -- max 4,000 "
            "characters (longer gets truncated, and risks the response itself being cut off "
            "mid-generation before it's valid)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "notes": {"type": "string", "description": "Your complete updated notes, replacing the previous version entirely."},
            },
            "required": ["notes"],
        },
    },
}

RECORD_HYPOTHESIS_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "record_hypothesis",
        "description": (
            "Pre-register ONE falsifiable hypothesis before it is tested. All fields required "
            "except stop_points/target_points. This is a commitment -- state your prediction "
            "before you (or anyone) knows the backtest result."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "claim": {"type": "string", "description": "Plain-language claim, 1-2 sentences."},
                "source_book": {"type": "string", "enum": ["harris", "cartea", "hull"]},
                "source_pages": {"type": "string", "description": "e.g. '142-145'. Must be a passage you actually retrieved via search_literature."},
                "mechanism": {"type": "string", "description": "Why this should apply to THIS data -- name the actual column(s) and the causal story."},
                "predicted_direction": {"type": "string", "description": "What you expect BEFORE testing, e.g. 'positive expectancy on the long side'."},
                "predicted_magnitude": {"type": "string", "description": "Roughly how large an effect, e.g. 'small, under 0.5 pts/trade average'."},
                "code": {"type": "string", "description": "Exactly one function: 'def signal(df):\\n    ...\\n    return <boolean Series>'."},
                "entry_side": {"type": "string", "enum": ["long", "short"]},
                "exit_hold_bars": {"type": "integer", "description": f"1-{strategy_lab.MAX_HOLD_BARS}."},
                "stop_points": {"type": "number", "description": "Optional stop-loss distance in points."},
                "target_points": {"type": "number", "description": "Optional profit-target distance in points."},
            },
            "required": [
                "claim", "source_book", "source_pages", "mechanism",
                "predicted_direction", "predicted_magnitude",
                "code", "entry_side", "exit_hold_bars",
            ],
        },
    },
}

FINISH_STUDYING_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "finish_studying",
        "description": "End this study session. Call this once, when you are done recording hypotheses.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Brief closing note: what you read, what you found, why you stopped here."},
            },
            "required": ["summary"],
        },
    },
}

_REQUIRED_HYPOTHESIS_FIELDS = RECORD_HYPOTHESIS_TOOL_DEF["function"]["parameters"]["required"]


@dataclass
class StudySessionResult:
    hypotheses: list[dict]
    transcript: list[dict]  # full, unedited message log -- system + every turn, verbatim
    tool_roundtrips: int
    forced_stop: bool
    forced_stop_reason: Optional[str]
    finish_summary: Optional[str]
    wall_seconds: float


async def _call_model(client: httpx.AsyncClient, messages: list[dict], tools: list[dict]) -> tuple[dict, dict]:
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    payload = {
        "model": "local", "messages": messages, "tools": tools, "tool_choice": "auto",
        "temperature": 0.5, "max_tokens": MAX_TOKENS_CAP, "stream": False,
    }
    resp = await client.post(f"{LLM_BASE_URL}/chat/completions", headers=headers, json=payload,
                              timeout=httpx.Timeout(180.0, connect=10.0))
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"], data.get("usage") or {}


def _group_char_len(group: list[dict]) -> int:
    return sum(len(json.dumps(m, default=str)) for m in group)


def _is_placeholder_group(group: list[dict]) -> bool:
    return len(group) == 1 and group[0].get("role") == "user" and group[0].get("_trim_placeholder")


def _trim_to_budget(system_prompt: str, groups: list[list[dict]], trimmed_so_far: int,
                     char_budget: float) -> tuple[list[list[dict]], int]:
    """Drops the OLDEST search_literature-result groups (never record_hypothesis/finish_studying
    groups, and never the most recent group) once the running transcript exceeds char_budget --
    same trim-not-reject principle as llm_chat.py's own context-budget fix, applied here because a
    single search_literature call can return several thousand characters of real book text and a
    long study session will otherwise run the transcript straight into the model's context ceiling.

    Bug fixed here (caught on the first real multi-round smoke test, not shipped): the original
    version inserted a NEW placeholder message every time it trimmed, without ever removing the
    previous one -- after ~18 rounds the transcript was mostly a pile of near-identical placeholder
    notes, one per trim pass. This version keeps at most ONE placeholder group (always the first
    group in the list), holding a running total, and replaces it in place on every call instead of
    inserting a new one.

    Raw search-result text lost to trimming is NOT otherwise recoverable within a session -- see
    update_notes()/the `notes` scratchpad in run_study_session for how the model is expected to
    carry forward what it learned before the raw passages age out (discovered necessary on the
    same smoke test: without a persistent scratchpad, the model kept re-searching the same handful
    of topics for 20+ rounds because each trim silently erased its only record of what it had
    already found, never converging on recording a hypothesis)."""
    groups = [g for g in groups if not _is_placeholder_group(g)]
    total = len(system_prompt) + sum(_group_char_len(g) for g in groups)
    trimmed_count = 0
    i = 0
    while total > char_budget and i < len(groups) - 1:  # never touch the last (most recent) group
        group = groups[i]
        names = {tc["function"]["name"] for m in group if m.get("role") == "assistant"
                  for tc in (m.get("tool_calls") or [])}
        if "search_literature" in names and "record_hypothesis" not in names and "finish_studying" not in names:
            total -= _group_char_len(group)
            groups.pop(i)
            trimmed_count += 1
            continue
        i += 1
    total_trimmed = trimmed_so_far + trimmed_count
    if total_trimmed:
        placeholder = {"role": "user", "_trim_placeholder": True, "content": (
            f"(Note: {total_trimmed} earlier search_literature call(s) and their raw results have "
            f"been trimmed from this transcript to fit context -- that raw text is now gone. Any "
            f"hypotheses already recorded via record_hypothesis are unaffected. Rely on your own "
            f"notes (via update_notes) for anything you found in a trimmed search that you still "
            f"need -- don't assume you can re-read it verbatim.)"
        )}
        groups = [[placeholder]] + groups
    return groups, total_trimmed


def _sanitize_for_api(messages: list[dict]) -> list[dict]:
    """Strips internal bookkeeping keys (e.g. _trim_placeholder) before a message list goes to the
    model -- those keys exist only for this module's own trimming logic and are never part of the
    OpenAI-compatible chat message schema."""
    return [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]


async def run_study_session() -> StudySessionResult:
    t0 = time.time()
    notes = ""
    initial_user = {"role": "user", "content": (
        "Begin. Search the literature, read as broadly as you want, and record falsifiable "
        "hypotheses as you form them. Call finish_studying() when you're done."
    )}
    groups: list[list[dict]] = [[initial_user]]
    tools = [
        literature_search.SEARCH_LITERATURE_TOOL_DEF, UPDATE_NOTES_TOOL_DEF,
        RECORD_HYPOTHESIS_TOOL_DEF, FINISH_STUDYING_TOOL_DEF,
    ]

    hypotheses: list[dict] = []
    finish_summary: Optional[str] = None
    forced_stop = False
    forced_stop_reason: Optional[str] = None
    roundtrip = 0
    trimmed_count = 0
    rounds_since_progress = 0  # resets whenever a hypothesis is recorded -- see NUDGE_INTERVAL below

    # Real-measured budget state -- see TARGET_PROMPT_TOKENS above for why this replaced a purely
    # char-based guess. None on round 1 (no real measurement exists yet); after that, every round
    # uses the PREVIOUS round's actual usage.prompt_tokens vs. the chars actually sent to derive a
    # live chars/token ratio, rather than trusting any fixed assumed ratio.
    last_prompt_tokens: Optional[int] = None
    last_sent_chars: Optional[int] = None

    async with httpx.AsyncClient() as client:
        for roundtrip in range(1, MAX_TOOL_ROUNDTRIPS + 1):
            system_prompt = _build_system_prompt(notes)
            if last_prompt_tokens and last_sent_chars:
                measured_ratio = last_sent_chars / last_prompt_tokens
                char_budget = TARGET_PROMPT_TOKENS * measured_ratio
            else:
                char_budget = MAX_TOTAL_CHARS

            # MISSION literature-research Phase 2: a real production run crashed here at round 61
            # with an unhandled httpx.HTTPStatusError (500 -- the model's tool-call JSON got
            # physically truncated when the request hit the context ceiling) and lost 60 rounds and
            # 2 recorded hypotheses because nothing caught it. This retry loop is the fix: on
            # failure, trim MUCH harder (halve the budget) and try again, up to MAX_CALL_RETRIES,
            # before giving up on this round gracefully rather than letting the whole session die.
            message = None
            usage: dict = {}
            for attempt in range(1, MAX_CALL_RETRIES + 1):
                groups, trimmed_count = _trim_to_budget(system_prompt, groups, trimmed_count, char_budget)
                messages = _sanitize_for_api(
                    [{"role": "system", "content": system_prompt}] + [m for g in groups for m in g]
                )
                sent_chars = len(json.dumps(messages, default=str)) + len(json.dumps(tools, default=str))
                try:
                    message, usage = await _call_model(client, messages, tools)
                    last_prompt_tokens = usage.get("prompt_tokens") or last_prompt_tokens
                    last_sent_chars = sent_chars
                    break
                except (httpx.HTTPStatusError, httpx.HTTPError) as exc:
                    # MISSION literature-research Phase 2: a SECOND real crash (after the first,
                    # input-size one, was fixed) showed this same generic except clause catching a
                    # DIFFERENT failure class -- an OUTPUT that ran too long (a giant update_notes
                    # string) and got cut off mid-generation. All 3 retries there failed with
                    # byte-identical error text because halving char_budget only shrinks INPUT; it
                    # does nothing for a too-long OUTPUT. Detect that specific signature and respond
                    # to it correctly: nudge for a SHORTER response instead of just re-trimming input.
                    body = getattr(getattr(exc, "response", None), "text", "") or ""
                    is_output_too_long = "Failed to parse tool call arguments as JSON" in body
                    log.warning("study roundtrip %d attempt %d/%d failed (%s)%s", roundtrip, attempt,
                                MAX_CALL_RETRIES, exc,
                                " -- looks like a too-long OUTPUT, nudging for brevity" if is_output_too_long
                                else " -- retrying with a harder input trim")
                    if is_output_too_long:
                        groups.append([{"role": "user", "content": (
                            "(Your previous response was too long and got cut off mid-generation, "
                            "before it was valid -- nothing from it was recorded. If you were "
                            "calling update_notes, write much shorter notes this time (a few compact "
                            "bullet points, well under 4,000 characters, no long quotes). Try again "
                            "now with a shorter response.)"
                        )}])
                    else:
                        char_budget = char_budget / 2
                        # A hard failure means our token-per-char estimate for THIS content was
                        # optimistic -- treat it as if the request had been much larger than
                        # measured, so the ratio-based budget for later rounds doesn't repeat this.
                        last_prompt_tokens = 16_384
                    last_sent_chars = sent_chars
                    # MISSION literature-research Phase 2: this exact failure signature (all
                    # MAX_CALL_RETRIES attempts failing within milliseconds of each other, with
                    # byte-IDENTICAL error text each time) recurred across two separate production
                    # runs. Real inference takes several seconds on this model/hardware (observed
                    # throughout this session); failing that fast on every retry means the server
                    # was NOT actually re-running inference on the retried (differently-nudged or
                    # differently-trimmed) request -- something in its internal state for that slot
                    # was stuck replaying the first failure. A short real delay before retrying is
                    # the direct, cheap response: give the server's slot state an actual chance to
                    # settle before asking it to try again, rather than hammering it instantly.
                    await asyncio.sleep(2.0)
            else:
                forced_stop = True
                forced_stop_reason = (
                    f"gave up after {MAX_CALL_RETRIES} failed attempts at roundtrip {roundtrip} -- "
                    f"see log for the underlying errors. Stopping with whatever was recorded rather "
                    f"than losing it to a crash."
                )
                log.error("study session: %s", forced_stop_reason)
                break

            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                groups.append([
                    {"role": "assistant", "content": message.get("content") or ""},
                    {"role": "user", "content": "(Call one of your tools now -- search_literature, update_notes, record_hypothesis, or finish_studying.)"},
                ])
                continue

            assistant_tool_calls = []
            tool_result_messages = []
            done = False
            searches_this_turn = 0
            for i, tc in enumerate(tool_calls):
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                if name == "search_literature":
                    searches_this_turn += 1
                    if searches_this_turn > MAX_SEARCHES_PER_TURN:
                        result = {"error": (
                            f"not executed -- at most {MAX_SEARCHES_PER_TURN} search_literature "
                            f"calls run per turn, to keep one turn's results from overflowing "
                            f"context. Call this again on your next turn if you still need it."
                        )}
                    else:
                        result = literature_search.search_literature(args.get("query", ""), args.get("k", 6))
                elif name == "update_notes":
                    notes = args.get("notes", "")
                    if len(notes) > NOTES_MAX_CHARS:
                        # MISSION literature-research Phase 2: a real production run's SECOND crash
                        # (after the first was fixed) traced to the model generating an update_notes
                        # call whose `notes` argument was so long that generation itself hit the
                        # 16,384-token context ceiling mid-string, producing invalid truncated JSON
                        # llama-server's own parser then 500'd on -- confirmed in the systemd journal
                        # ("stop processing: n_tokens = 16383, truncated = 1" immediately followed by
                        # "Failed to parse tool call arguments as JSON ... missing closing quote").
                        # Retrying with a harder INPUT trim (the fix for the FIRST crash) did nothing
                        # here -- all 3 retries failed with byte-identical error text, because the
                        # problem was OUTPUT length, not input size; trimming older search results
                        # never touches that. Capping accepted notes length is the direct fix for
                        # this specific failure mode.
                        notes = notes[:NOTES_MAX_CHARS]
                        result = {
                            "status": "ok", "notes_length_chars": len(notes),
                            "warning": (
                                f"your notes were truncated to {NOTES_MAX_CHARS} characters -- keep "
                                f"future notes more concise (a few short bullet points, not full "
                                f"paragraphs quoting long passages) so a single update_notes call "
                                f"can't run long enough to risk being cut off mid-generation."
                            ),
                        }
                    else:
                        result = {"status": "ok", "notes_length_chars": len(notes)}
                elif name == "record_hypothesis":
                    missing = [f for f in _REQUIRED_HYPOTHESIS_FIELDS if f not in args or args[f] in (None, "")]
                    if missing:
                        result = {"error": f"missing required field(s): {missing}. Hypothesis NOT recorded -- call record_hypothesis again with all required fields."}
                    else:
                        args["recorded_at_roundtrip"] = roundtrip
                        hypotheses.append(args)
                        result = {"status": "recorded", "hypothesis_count": len(hypotheses)}
                        if "abs(" in args.get("code", ""):
                            # Caught live twice on smoke tests: abs(col) > x paired with a single
                            # entry_side fires on both tails but only tests one direction against
                            # them -- a soft nudge, not a hard reject, since abs() isn't ALWAYS
                            # this bug (e.g. a same-direction-both-ways magnitude filter combined
                            # with a separately correct directional condition is legitimate).
                            result["warning"] = (
                                "this code uses abs() -- if that creates a both-tails condition "
                                "paired with a single entry_side, split it into two hypotheses "
                                "using the signed column directly (see system prompt). Recorded "
                                "as-is; you may record a corrected replacement instead if this "
                                "was the bug."
                            )
                        rounds_since_progress = -1  # incremented back to 0 just below, before the next round
                elif name == "finish_studying":
                    finish_summary = args.get("summary", "")
                    result = {"status": "ok"}
                    done = True
                else:
                    result = {"error": f"unknown tool {name!r}"}
                call_id = tc.get("id") or f"call_{i}"
                assistant_tool_calls.append({
                    "id": call_id, "type": "function",
                    "function": {"name": name, "arguments": tc["function"].get("arguments") or "{}"},
                })
                tool_result_messages.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, default=str)})

            groups.append(
                [{"role": "assistant", "content": message.get("content"), "tool_calls": assistant_tool_calls}]
                + tool_result_messages
            )
            log.info("study roundtrip %d: tools=%s hypothesis_count=%d notes_chars=%d", roundtrip,
                      [tc["function"]["name"] for tc in assistant_tool_calls], len(hypotheses), len(notes))

            if done:
                break

            rounds_since_progress += 1
            if rounds_since_progress >= NUDGE_INTERVAL and rounds_since_progress % NUDGE_INTERVAL == 0:
                groups.append([{"role": "user", "content": (
                    f"({rounds_since_progress} rounds since your last recorded hypothesis (or the "
                    f"start of the session, if none yet). You almost certainly already have enough "
                    f"in your notes to pre-register at least one falsifiable hypothesis now -- do "
                    f"that with record_hypothesis before searching further. It's fine to keep "
                    f"reading afterward and record more later; it is NOT fine to keep searching "
                    f"indefinitely without ever committing to one.)"
                )}])
        else:
            forced_stop = True
            forced_stop_reason = f"hit MAX_TOOL_ROUNDTRIPS ({MAX_TOOL_ROUNDTRIPS}) without finish_studying"
            log.warning("study session %s -- stopping with whatever hypotheses were recorded (%d)",
                        forced_stop_reason, len(hypotheses))

    final_system_prompt = _build_system_prompt(notes)
    full_transcript = _sanitize_for_api(
        [{"role": "system", "content": final_system_prompt}] + [m for g in groups for m in g]
    )
    return StudySessionResult(
        hypotheses=hypotheses, transcript=full_transcript, tool_roundtrips=roundtrip,
        forced_stop=forced_stop, forced_stop_reason=forced_stop_reason,
        finish_summary=finish_summary, wall_seconds=time.time() - t0,
    )


def _write_outputs(result: StudySessionResult) -> tuple[Path, Path]:
    hyp_path = OUT_DIR / "hypotheses.json"
    hyp_path.write_text(json.dumps(result.hypotheses, indent=2))
    transcript_path = OUT_DIR / "study_transcript.json"
    transcript_path.write_text(json.dumps(result.transcript, indent=2, default=str))
    return hyp_path, transcript_path


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        result = await run_study_session()
    except Exception:
        # Last-resort safety net: run_study_session already retries/degrades gracefully for the
        # specific failure mode that once crashed a real production run (a model-server error mid
        # tool-call), but this catches anything else genuinely unanticipated so main() at least
        # fails loudly and visibly instead of the process just silently dying with no diagnosis.
        # It cannot recover hypotheses that were only held in run_study_session's local state at
        # the moment of the crash -- that's why the specific, demonstrated failure mode is handled
        # INSIDE run_study_session instead, where the accumulated state is still reachable.
        log.exception("study session raised an unhandled exception -- nothing was written")
        raise
    hyp_path, transcript_path = _write_outputs(result)
    print(f"\n{'='*70}")
    print(f"Study session done in {result.wall_seconds:.1f}s, {result.tool_roundtrips} tool roundtrips"
          f"{f' (FORCED STOP: {result.forced_stop_reason})' if result.forced_stop else ''}")
    print(f"{len(result.hypotheses)} hypotheses recorded")
    print(f"Finish summary: {result.finish_summary!r}")
    print(f"Written to: {hyp_path}")
    print(f"Full transcript written to: {transcript_path}")
    print(f"{'='*70}")


if __name__ == "__main__":
    asyncio.run(main())
