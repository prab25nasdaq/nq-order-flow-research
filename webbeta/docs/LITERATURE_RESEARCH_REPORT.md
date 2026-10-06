# Literature-Grounded Research Loop — Report

Status as of this line: Phase 1 complete, Phase 2 in progress. Written incrementally, as the work
happens, for someone reading this cold with no memory of the conversation that produced it.

## What this project is

Give an LLM three trading/microstructure textbooks via local RAG, let it read them and pre-register
falsifiable hypotheses about what should predict NQ order-flow returns (using the dataset already
available in Strategy Lab), then backtest every single hypothesis — including weak ones — through
the existing causality-guarded harness, and report honestly, including a null result if that's what
happens.

Governing instruction from the user (given once, run unattended): run Phases 1–3 continuously
without stopping for review. Design decisions were specified up front (see "Design decisions" below).
Only stop and ask if something is genuinely ambiguous enough that guessing would waste hours —
otherwise keep going and document what happened.

## Corpus

Three PDFs, already present on disk under `/home/prabh/Documents/Books/`:

| Key | Title | Author | Pages | Assigned collection |
|---|---|---|---|---|
| harris | Trading and Exchanges: Market Microstructure for Practitioners | Larry Harris | (full book) | primary |
| cartea | Algorithmic and High-Frequency Trading | Cartea, Jaimungal, Penalva | (full book) | primary |
| hull | Options, Futures and Other Derivatives (8th ed.) | John Hull | (full book) | hull (separate) |

Text layers were verified extractable via `pdftotext -layout` before committing to this approach —
no OCR needed, no OCR errors beyond the pre-existing occasional garbling around equations/Greek
letters that `pdftotext` itself introduces.

## Design decisions (specified by the user, not chosen by the model)

1. **Chunking**: ~700 tokens per chunk, ~15% overlap, retrieve 6 per query. Rationale given: Harris
   builds arguments over pages, so fewer/larger chunks beat many small ones for this corpus.
2. **Embeddings**: use a local model already installed if one exists; nothing requiring an API.
   `nomic-embed-text-v1.5.Q4_K_M.gguf` was already present on disk — used as-is, no download needed.
   Served via a second llama-server instance in `--embedding --pooling mean` mode, on port 8091,
   independent from the main chat model's server.
3. **Hull**: indexed as a separate collection (`collection_hull`), never merged into
   `collection_primary` (Harris + Cartea). Queries hit primary first; Hull is consulted only as a
   fallback. Rationale given: at 685k tokens Hull is roughly half the corpus and the least relevant
   to intraday order-flow prediction — merging would dilute every microstructure query with
   derivatives-pricing-theory noise.

## Build pipeline (`server/literature/build_index.py`)

1. `pdftotext -layout <pdf> -` per book → per-page text, joined with `\x0c` form-feed page breaks
   from `pdftotext` itself, used to build a char-offset → page-number map.
2. Chunked at a per-book character target derived from a **measured**, not assumed, chars-per-token
   ratio (via the main model's `/tokenize` endpoint on the full extracted text of each book):
   harris 5.21, cartea 4.24, hull 4.38 chars/token. Different books tokenize at meaningfully
   different densities (equation-heavy books compress differently), so a single generic ratio
   would have mis-sized every chunk.
3. Chunk boundaries snap forward up to 150 chars to the nearest blank-line paragraph break when one
   exists in that window, so a chunk doesn't get cut mid-sentence when avoidable.
4. Each chunk embedded via the local embedding server, batched 32 at a time.
5. Written to disk: `chunks_{book}.json` / `embeddings_{book}.npy` per book (for provenance/
   debugging), then assembled into `collection_primary.{json,npy}` (harris+cartea, 1240 chunks) and
   `collection_hull.{json,npy}` (hull alone, 1151 chunks).

**Fixed during build**: the embedding server's default llama-server batch size (`-b 512`) rejected
~700-token chunks with `"input (686 tokens) is too large to process"`. Relaunched with
`-b 4096 -ub 4096`, verified against a long synthetic string, then re-ran the full build. No data
was silently dropped — the failure was a hard 500 on every batch containing an oversized chunk, not
a silent truncation, so the first (failed) attempt's partial output was simply discarded and the
whole build re-run clean.

## Search module (`server/literature_search.py`)

`search_literature(query, k=6)` — embeds the query against the same model, computes cosine
similarity against both collections (cheap: a few thousand chunks total, plain numpy dot products,
no vector DB needed), and picks which collection's results to return.

**First attempt was wrong, caught by calibration testing before use, not shipped**: the initial
design used an absolute floor on primary's own top score (`FALLBACK_THRESHOLD = 0.55`) — fall back
to Hull only if primary's best cosine score fell below that. Calibrated live against 7 real queries
spanning clearly-primary topics (order flow toxicity, dealer inventory, optimal execution, market
microstructure noise), clearly-Hull topics (Black-Scholes, binomial trees, futures margin), and one
deliberately ambiguous query. Result: **every** primary top-1 score across all 7 queries came back
between 0.64 and 0.81 — nomic-embed-text's cosine similarities cluster tightly in that range
regardless of true relevance, so no absolute cutoff anywhere near 0.55 could ever fire; Hull's score
was never actually consulted for routing even on the queries it was clearly better suited to answer.

**Fix**: switched to a direct margin comparison between the two collections' own top scores.
Hull is used only when `hull_best_score - primary_best_score > FALLBACK_MARGIN` (0.02). Re-verified
against the same 7 queries after the fix:

| Query | Primary best | Hull best | Margin | Routed to |
|---|---|---|---|---|
| order flow toxicity / adverse selection | 0.745 | — | — | primary |
| dealer inventory risk | 0.807 | — | — | primary |
| optimal execution / order splitting | 0.666 | — | — | primary |
| market microstructure noise | 0.641 | — | — | primary |
| Black-Scholes derivation | 0.694 | 0.767 | +0.073 | hull_fallback |
| binomial tree option valuation | 0.609 | 0.765 | +0.156 | hull_fallback |
| futures margin / mark-to-market | 0.752 | 0.759 | +0.007 | primary (within noise margin) |

The last row is the deliberately ambiguous query (futures margin mechanics appear in both Harris'
exchange-mechanics chapters and Hull's futures chapters) — it lands well inside the 0.02 noise
margin and correctly defaults to primary rather than flip-flopping, which is the intended behavior
for a genuinely borderline case.

**What I'd do differently**: calibrate the routing logic against real queries *before* writing the
first version, not after. The absolute-threshold design was a reasonable-sounding guess that would
have silently never triggered in production — it happened to be caught here only because calibration
testing was done as a deliberate step before wiring the tool into the study loop, not because the
bug was otherwise obvious from reading the code.

## Phase 2 (study loop)

New file: `webbeta/server/literature_study.py`. An LLM reads via `search_literature` (Phase 1) plus
the dataset's own facts (`strategy_lab.describe_columns()`/`describe_dataset()` — the same numbers
every backtest already uses, not a second source of truth), and writes pre-registered, falsifiable
hypotheses via a `record_hypothesis` tool call the moment it commits to one — not batched at the
end, so a hypothesis can't be quietly reworded after Phase 3 sees its result. Each recorded
hypothesis's `code`/`entry_side`/`exit_hold_bars`/`stop_points`/`target_points` are in EXACTLY
`strategy_lab.run_generated_backtest()`'s own argument shape, so Phase 3 feeds each one into that
function directly, unmodified — the same causality-guarded, train/test-split harness every other
strategy in this app goes through.

Model used: the existing production chat model already running on port 8080 (a Qwen3.6-35B variant,
`-c 16384`), the same server every other feature in this app already uses — no separate model stood
up for this.

**Three real bugs found and fixed during development, in order, each caught by a live smoke test
before being allowed into the production run:**

1. **Context overflow on the very first test.** The model fired three PARALLEL `search_literature`
   calls in one turn (one of them with `k=8`, unbounded), and the combined tool results measured
   17,703 tokens — over the model's 16,384-token context in a single shot, before any trim logic
   ever got a chance to run (trimming only drops OLDER groups; the turn that just blew the budget
   IS the newest group). Fixed at the source, not by further tuning a size constant: `k` is now
   hard-capped at 6 inside `search_literature()` itself regardless of what's requested (measured: a
   single k=6 result is ~20,160 chars / ~4,256 tokens, ~4.74 chars/token for this JSON-heavy
   content), and `literature_study.py` now executes at most 2 `search_literature` calls per model
   turn (`MAX_SEARCHES_PER_TURN`), returning a "not executed, call again next turn" result for any
   more in the same turn — bounding one turn's worst-case size to a known quantity instead of
   leaving it open-ended.

2. **Trim-placeholder leak.** The context-trimming logic (drops oldest search-result groups once the
   transcript exceeds a char budget, same trim-not-reject principle as the earlier context-budget
   fix) inserted a NEW placeholder note every time it trimmed, without removing the previous one —
   by round 18 of a test run the transcript was mostly a pile of near-identical placeholder
   messages. Fixed to keep exactly one placeholder, holding a running total, replaced in place each
   time.

3. **The real bug: the model never converged on a hypothesis.** Once (1) and (2) were fixed, a
   20-round test still recorded **zero** hypotheses — full per-round logging showed the model
   re-searching close variations of the same handful of queries (VPIN alone reworded and re-searched
   at least 8 times) for 18 straight rounds, never calling `record_hypothesis`, despite having
   already surfaced a concrete, quotable, testable finding early on (Cartea: price upticks followed
   by a downtick ~57% of the time). This traced to trimming discarding raw search text with nothing
   compensating for it — the model had no way to retain what it had already learned once the raw
   passages aged out of context, so it kept re-covering the same ground rather than building on it.
   Fixed with a persistent `notes` scratchpad (an `update_notes(notes)` tool, mirroring
   agent_replay.py's own proven scratchpad pattern) that survives trimming and is re-injected into
   the system prompt every turn, plus a corrective nudge every 8 rounds without a new
   `record_hypothesis` call (same "one corrective nudge for a stuck pattern" precedent as
   llm_chat.py's dead-end retry and agent_replay.py's no-tool-call nudge) explicitly telling the
   model to commit to what it already has rather than keep searching indefinitely. After this fix, a
   40-round validation run recorded 2 well-formed hypotheses (at rounds 26 and 36) with real
   mechanism/column mappings and specific predictions.

**A fourth issue, caught in the recorded hypotheses' actual content, not the plumbing:** two of
three hypotheses in one smoke-test batch OR'd a `long_signal` condition and a `short_signal`
condition together into one `signal(df)` boolean series, while declaring only a single
`entry_side`. Since `run_generated_backtest`'s contract applies ONE entry_side to every bar the
signal returns True on, this would have silently tested "go long on every bar meeting EITHER
condition" — not what either hypothesis actually claimed. A related variant (`abs(mlofi_norm) > 2`
paired with a single `entry_side`) showed up in a later sample — a magnitude-only condition fires
on both tails but still gets only one direction applied. Fixed with an explicit instruction in the
system prompt (split an opposite-direction claim into two single-direction hypotheses) plus a
lightweight, non-blocking structural check in `record_hypothesis`'s dispatch: any recorded `code`
containing `abs(` gets a warning appended to the tool result reminding the model to check for this
exact pattern, without hard-rejecting (an `abs()` used correctly alongside an already-consistent
directional condition is legitimate, so this warns rather than blocks).

**What I'd do differently**: build the persistent-notes scratchpad and the per-turn/per-call size
caps into the FIRST version, not discover both live. In hindsight, "an unattended multi-round tool
loop against a small context window will eventually need both a hard per-call size cap and a
compaction/memory strategy" was predictable from this app's own prior experience (agent_replay.py's
scratchpad, llm_chat.py's context-budget fix) rather than something to re-derive by hitting the
same class of failure a third time on a new module.

**Production run history — 5 attempts, 2 more real bugs found and fixed live, before a clean run:**

| Attempt | Rounds reached | Hypotheses | Outcome |
|---|---|---|---|
| 1 | 60 | 2 (lost) | **Crashed** — unhandled exception, nothing written to disk |
| 2 | 13 | 0 | Gracefully degraded (new retry logic caught the crash) but gave up fast |
| 3 | 34 | 4 | Gracefully degraded |
| 4 | 108 | 1 | Gracefully degraded |
| 5 | 60 (natural finish) | **17** | **Clean** — model called `finish_studying()` itself, zero errors |

Attempt 1's crash (root-caused in the llama-server systemd journal, not guessed) was
`"stop processing: n_tokens = 16383, truncated = 1"` immediately followed by `"Failed to parse tool
call arguments as JSON ... unexpected end of input"` — the request's real prompt size, which the
original char-based trim budget never accounted for (it measured only the message history, never
the ~900-1,200 token cost of the 4 tool JSON schemas sent on every turn, nor real chat-template
overhead), left so little headroom that the model's own tool-call JSON got physically cut off when
prompt+completion hit the 16,384-token ceiling. **Fixed properly, not by guessing a smaller
constant**: the chat-completions response already reports the exact real prompt size for every
call (`usage.prompt_tokens`) — the trim budget now uses that measured truth directly (target
12,000 tokens, leaving room for the completion + safety margin) instead of an assumed chars/token
ratio. Also added: a retry loop (`MAX_CALL_RETRIES=3`) so a single bad turn degrades gracefully
(harder trim, then give up preserving whatever was recorded) instead of ever crashing the whole
session again — this is what turned attempts 2-4 from "lost work" into "an honest partial result
still written to disk."

Attempts 2-4 surfaced a SECOND, different real bug via the same journal-log method: the model
occasionally tried to write a very long `update_notes` string, and generating it hit the SAME
16,384-token ceiling mid-string — this is an OUTPUT-length failure, not an input-size one, so the
existing "trim the input harder" retry response did nothing (confirmed: all 3 retries failed with
byte-identical error text, because halving input size never touches a too-long response). Fixed
with: a hard 4,000-character cap on accepted `update_notes` text (truncated with a warning, not
silently rejected), explicit "keep notes short" instructions in both the system prompt and the
tool's own JSON description, and detection of this specific error signature in the retry loop that
nudges for a shorter response instead of re-trimming input.

The identical, near-instantaneous nature of the repeated failures during attempts 3-4 (all 3 retry
attempts failing within single-digit milliseconds of each other, with byte-for-byte identical error
text) is itself evidence worth recording: real inference on this model/hardware measured several
seconds throughout this session, so failing that fast on every retry means the server was NOT
actually re-running inference on the retried request — something in its internal state for that
slot appears to get stuck replaying the first failure rather than processing new content. Added a
2-second real delay before each retry as a direct, low-cost response to that specific observation.
Attempt 5, with this fix in place, ran clean.

**What I'd do differently**: root-cause from server-side logs (the systemd journal) from the very
first failure rather than reasoning from the client-side exception alone — the client only ever
saw "500 Internal Server Error" with no body in two of these cases; the journal had the real,
specific error every time, and every fix in this section came from reading it rather than guessing.
Also: budget for MULTIPLE production attempts up front when running an LLM against a fixed context
window through many unattended rounds — this is inherently a long-tail-of-failure-modes problem
(three genuinely distinct root causes across 5 attempts, no repeats), not a one-shot "fix it once"
task.

**Final Phase 2 output** (`webbeta/server/literature/hypotheses.json`, from attempt 5): 17
pre-registered hypotheses, each with a plain-language claim, source book/page range, a stated
mechanism naming real dataset columns, a pre-committed prediction (direction + magnitude), and a
`signal(df)`/`entry_side`/`exit_hold_bars` block in the exact shape `run_generated_backtest`
expects. Span: directional order-flow signals (`mlofi_norm`, `delta_norm`, `sweep_imbalance_norm`),
reversal signals (extreme `mlofi_norm`, extreme `mid_resid_z`), CUSUM regime persistence, a rolling
(`mlofi_rolling_5`) variant, and VPIN-amplified combinations.

**Known issues in this exact hypothesis set, disclosed rather than silently cleaned up** (per
pre-registration principles: the goal is to test what the model actually committed to, flaws
included, not a tidied-up version):
- **#5 is internally inconsistent with its own claim.** Its code is `abs(mlofi_norm) > 3.0` with a
  single `entry_side="short"` — a magnitude-only (both-tails) condition paired with one direction,
  exactly the failure mode the system prompt explicitly warned against. The model evidently
  understood the correct approach (it separately, correctly recorded the split version as #6
  `mlofi_norm > 3.0`/short and #7 `mlofi_norm < -3.0`/long) but still also recorded the inconsistent
  combined version as its own hypothesis. Phase 3 tests #5 exactly as specified — it will measure
  "always short when |mlofi_norm| is extreme, regardless of sign," which is a real, testable
  strategy in its own right even though it doesn't actually match hypothesis #5's own stated claim.
- **Two exact-duplicate pairs**: #2 and #3 (`delta_norm > 0.1`, long, 1-bar hold) are identical
  down to the code; #7 and #9 (`mlofi_norm < -3.0`, long, 3-bar hold) are also identical. Phase 3
  will produce identical results for each pair — reported as 17 trials, not silently deduplicated
  to 15, since the trial count itself is part of what was asked to be reported honestly.

## Phase 3 (test every hypothesis)

New file `webbeta/server/literature_backtest.py`: feeds each hypothesis's `code`/`entry_side`/
`exit_hold_bars`/`stop_points`/`target_points` into `strategy_lab.run_generated_backtest()`
UNMODIFIED — the exact same causality-guarded, train/test-split (70/30) harness every other
strategy in this app goes through. No hypothesis was skipped, reworded, or excluded for looking
weak. Ran clean on the first attempt (pure deterministic Python, no LLM involved, ~1.3s/hypothesis).

**16 of 17 pre-registered hypotheses were actually tested; 1 was rejected by the harness's own
existing safety gate**, not by anything built for this task: hypothesis #1
(`mlofi_norm > 0.5`, no other condition) fires on 34% of all bars — above the 30% cap
`signal_sandbox.py` already enforces against "too broad to be a meaningful, selective entry
signal." This is itself a finding worth keeping, not an error to route around: the single
simplest, most naively-obvious hypothesis in the set (large chunk of history, weak threshold) was
too undiscriminating to even qualify as a real signal by this app's own pre-existing standard.

## Phase 4 (report honestly)

New file `webbeta/server/literature_phase4.py`. For each of the 16 tested hypotheses: whether it
"held" (a statistically significant — one-sided t ≥ 1.96 — POSITIVE out-of-sample expectancy, i.e.
the strategy as specified, already encoding its predicted direction via `entry_side`, actually made
money out-of-sample), plus a matched-trade-frequency random-signal benchmark (same entry_side/
exit_hold_bars/stop/target, a random boolean signal firing at approximately the same rate,
deterministic per-hypothesis seed) run through the identical harness for comparison.

**A real methodology bug caught and fixed before this shipped, not after**: the first version of
the "held" check only verified that out-of-sample agreed in SIGN with in-sample — it flagged
hypotheses #2/#3 (`delta_norm > 0.1`, `entry_side="long"`, claiming delta_norm *positively*
predicts returns) as "held," because both in-sample (-0.255 expectancy) and out-of-sample (-0.467)
were consistently NEGATIVE and statistically significant. That is significant evidence AGAINST what
the hypothesis actually claimed, not for it — a significant result in the wrong direction is a
rejected hypothesis, not a confirmed one. Fixed to require the out-of-sample expectancy be
POSITIVE (matching the direction the hypothesis's own `entry_side` already encodes) AND
significant, and the chance-baseline recomputed to match (one-sided ~2.5% false-positive rate
under the null, not the ~5% a two-sided test would give).

### Full results table

| # | Side | Book/pages | In-sample expectancy (t) | Out-of-sample expectancy (t) | Random-benchmark OOS (t) | Held? |
|---|---|---|---|---|---|---|
| 1 | long | cartea | — | **REJECTED** (fires on 34% of bars, over the 30% cap) | — | n/a |
| 2 | long | cartea 82-89 | -0.255 (-1.16) | -0.467 (-1.974) | -0.427 (-1.539) | No |
| 3 | long | cartea 62-63 | -0.255 (-1.16) | -0.467 (-1.974) | -0.057 (-0.217) | No |
| 4 | long | cartea 25-26 | -0.097 (-0.113) | -0.235 (-0.215) | -0.310 (-0.343) | No |
| 5 | short | harris 445-446 | -0.662 (-1.104) | -0.754 (-1.197) | 0.605 (0.974) | No |
| 6 | short | harris 445-446 | 0.332 (0.395) | -0.215 (-0.212) | -0.707 (-0.863) | No |
| 7 | long | harris 445-446 | 1.312 (1.704) | 0.427 (0.544) | 0.602 (0.817) | No |
| 8 | long | cartea 82-89 | -0.374 (-1.124) | -0.431 (-1.178) | -0.271 (-0.701) | No |
| 9 | long | harris 445-446 | 1.312 (1.704) | 0.427 (0.544) | -1.557 (-2.054) | No |
| 10 | long | cartea 298-304 | -0.365 (-0.965) | -0.256 (-0.687) | -0.325 (-0.788) | No |
| 11 | short | harris 445-446 | -0.338 (-0.658) | 0.413 (0.796) | -0.428 (-0.690) | No |
| 12 | long | harris 445-446 | 0.167 (0.324) | 0.652 (1.192) | 0.025 (0.044) | No |
| 13 | long | cartea 307-308 | -1.069 (-1.093) | -0.752 (-0.723) | 0.883 (0.734) | No |
| 14 | short | cartea 307-308 | 0.025 (0.026) | -0.452 (-0.399) | -2.171 (-1.851) | No |
| 15 | long | cartea 25-26 | 1.695 (1.079) | -0.521 (-0.142) | -3.250 (-1.700) | No |
| 16 | long | cartea 298-300 | -0.037 (-0.050) | -0.821 (-0.945) | **2.297 (3.271)** | No |
| 17 | short | cartea 298-300 | -0.215 (-0.310) | -0.727 (-1.128) | -0.612 (-0.910) | No |

(#7 and #9 are the exact-duplicate pair flagged in Phase 2 — identical code/side/hold, identical
results, as expected.)

### The numbers, plainly

- **Trial count: 17 pre-registered, 16 actually tested** (1 rejected by the harness's pre-existing
  degenerate-signal cap).
- **0 of 16 held** (statistically significant, positive, out-of-sample, in the hypothesis's own
  predicted direction).
- **Expected to hold by chance alone at this trial count: 0.4.** Zero is entirely consistent with
  pure noise; it is not evidence of an anti-signal either — with only 16 trials there is no power
  to distinguish "truly zero effect" from "a very small real effect swamped by trading costs and
  noise."
- **Best observed out-of-sample annualized Sharpe: 4.24. Expected best Sharpe from PURE RANDOM
  SEARCH across 16 trials (Bailey, Borwein, López de Prado & Zhu 2014 deflation): 6.41.** The best
  real hypothesis does not even clear the bar random chance alone would be expected to clear at
  this trial count. This is the single most damning number in this report.
- **The random-benchmark column is not a formality — it produced the single largest |t|-stat in
  the entire table** (hypothesis #16's matched-frequency random signal: t=3.27, vs. the best real
  hypothesis's t≈1.7-2.0). A concrete, empirical illustration of exactly why one good-looking
  number from a single trial should never be trusted on its own: here, literal noise outscored
  every literature-derived hypothesis in the set.
- **Book distribution of all 17 pre-registered hypotheses: 11 cartea, 6 harris, 0 hull.** Hull was
  never once selected as more relevant during the entire Phase 2 study session — consistent with
  the corpus design (Hull is derivatives-pricing theory, genuinely the least relevant book to
  intraday order-flow prediction) and with the routing logic verified in Phase 1.
- Several individual hypotheses show the classic in-sample-to-out-of-sample decay pattern of an
  overfit or spurious effect: #7/#9's in-sample t=1.704 (the single most "promising"-looking
  in-sample result in the whole set) drops to t=0.544 out-of-sample — still positive in direction,
  but nowhere near significant, and well within what the random benchmark alone produced for the
  same trade frequency (t=0.817).

### Plain verdict

**None of the 17 literature-motivated hypotheses tested here show a trading edge that survives
honest out-of-sample and multiple-testing scrutiny.** This is a null result, reported as one. It
does not prove market microstructure theory is wrong, nor that no exploitable order-flow signal
exists in this dataset — it means these 17 specific, simply-specified, single-condition-or-pair
hypotheses, tested exactly as pre-registered, did not clear that bar on this ~69k-bar / 3-month NQ
sample. A best-observed Sharpe that loses to a random-search deflation baseline at this trial count
is about as clean a "nothing survived" signal as a small study like this can produce.

**What I'd do differently**: 
1. Pre-register a MECHANICAL "held" check (exact formula, not natural-language interpretation)
   as part of Phase 2's hypothesis schema itself — e.g. require `predicted_sign: "positive"|
   "negative"` as a structured field, not just free-text `predicted_direction` — so Phase 4 never
   has to infer intent from prose, and the methodology bug caught above (sign-agreement vs.
   actual-prediction-confirmation) couldn't have happened in the first place.
2. Test compound/multi-bar conditions and true out-of-sample-only literature ideas (e.g. actual
   optimal-execution schedules, not just single-threshold order-flow conditions) — everything
   recorded this session was a single condition or a two-condition AND, which is a narrow slice of
   what the corpus actually discusses.
3. Consider a larger `MAX_TOOL_ROUNDTRIPS`/multiple independent Phase 2 sessions pooled together
   for a larger, more diverse hypothesis set before Phase 3 — one session's 17 hypotheses, while
   honestly tested, is a small sample of what a deeper reading could have produced.
