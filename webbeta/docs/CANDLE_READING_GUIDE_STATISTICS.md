# Candle Reading Guide — Statistical Companion

SHADOW/RESEARCH ONLY. Companion to [`CANDLE_READING_GUIDE.md`](CANDLE_READING_GUIDE.md), which
establishes the ground-truth color semantics (Part 0 there) and walks through one real illustrative
example of each pattern. This document does not re-derive that ground truth — it takes the same
four patterns and asks a harder question of the same real dataset: **across every real bar that
actually matches each pattern, what genuinely happened, statistically, not anecdotally?**

**See also:** [`CANDLE_READING_GUIDE_STATES.md`](CANDLE_READING_GUIDE_STATES.md) — takes the 5
market states defined below in Part 3 and makes them the primary frame instead of the patterns,
adding recognition/trading-implication/exits per state (including a real out-of-sample correction
to the "reversing" state that mirrors this document's own mirror-pattern correction).

**Scope.** Same 53-session, 59,605-real-closed-bar dataset (`NQU6`/`NQM6`, 2026-06-03 through
2026-08-17) as the first guide. Parts 1 and 3 additionally need the feature-store's per-bar OHLC,
duration, and volume fields (`book_flow_data_service._load_bars`), which are only available for
**46 of the 53 sessions, 47,920 of 59,605 bars (80.4%)** — the 7 excluded sessions
(2026-06-03/04/07/08/09/10/11) predate the front-month roll to `NQU6` and have no feature-store
export under that symbol. This is stated once here and holds for every table below that needs
geometry; Part 0 and Part 2's core sweep use the full 59,605-bar set.

## Part 0 — Full-dataset sweep per pattern

**Match definitions** (chosen to give each pattern a workable real sample — see the sensitivity
table below for how these counts move with the threshold):

| Pattern | Definition | Real matches | Rate |
|---|---|---|---|
| 1. Green above / red below | `up_score > +0.02` and `low_score < −0.02` | 126 | 0.211% |
| 2. Mirror | `up_score < −0.02` and `low_score > +0.02` | 26 | 0.044% |
| 3. Mixed/alternating | `flip_rate > 0.45` and `\|overall_score\| < 0.015` | 2,662 | 4.47% |
| 4. Tight range | 4+ consecutive bars, close range < 2.5 points | 157 episodes | — |

**Outcome convention**, applied uniformly: for each match, `next1_norm` / `next3_norm` = the next
1-bar / 3-bar close change divided by the pattern bar's own price range (so a 2-point move on a
tight bar and a 20-point move on a wide bar can be compared on the same scale). `BAND = 0.25`.

- Patterns 1 & 2 have a real polarity (`sign(up_score − low_score)`, fixed by their own
  definition): **continued** = next move follows that polarity beyond the band, **reversed** =
  opposite, **sideways** = within the band.
- Patterns 3 & 4 have ~zero net polarity by construction — forcing continued/reversed labels on
  them would misrepresent what the pattern even is. They get **contained** vs. **broke out**
  instead (magnitude only, either direction).

### Pattern 1 — green above / red below (n=126)

| Horizon | continued | reversed | sideways |
|---|---|---|---|
| next 1 bar | 27.0% (34) | 36.5% (46) | 35.7% (45) |
| next 3 bars | 40.5% (51) | 35.7% (45) | 23.0% (29) |

Mean next-1 close change: **−0.71** (median −0.25); mean next-3: −0.27 (median 0.0). Roughly a
coin flip at both horizons, with a mild lean toward reversal at 1 bar that inverts toward
continuation by 3 bars. **This pattern does not show a clean, reliable directional edge in the raw
sweep** — the single illustrative example in the first guide happened to whipsaw hard in both
directions, which turns out to be broadly representative of the noise, not an exception.

### Pattern 2 — mirror (n=26 — small sample, read percentages as rough)

| Horizon | continued | reversed | sideways |
|---|---|---|---|
| next 1 bar | 30.8% (8) | 30.8% (8) | 38.5% (10) |
| next 3 bars | 42.3% (11) | 34.6% (9) | 19.2% (5, +1 no-data) |

Mean next-1 close change: +0.21 (median −0.75); mean next-3: +1.05 (median −3.0) — mean and
median disagree in sign, a direct symptom of n=26 being too small for the mean to be stable
(a couple of large-magnitude bars swing it). Directionally inconclusive; **the honest statement is
"not enough real occurrences to say,"** not a forced verdict.

**Threshold sensitivity** (patterns 1 & 2), for context on how threshold-dependent "rare" is:

| threshold | pattern 1 matches | pattern 2 matches |
|---|---|---|
| 0.010 | 665 (1.12%) | 150 (0.252%) |
| 0.015 | 260 (0.44%) | 51 (0.086%) |
| 0.020 | 126 (0.21%) | 26 (0.044%) |
| 0.030 | 42 (0.07%) | 12 (0.020%) |
| 0.040 | 16 (0.03%) | 5 (0.008%) |
| 0.050 | 8 (0.01%) | 3 (0.005%) |

**Correction to the first guide:** it described the mirror pattern's single illustrative bar
(42099) as "the only qualifying real instance across 59,605 bars, even after threshold
relaxation." That framing came from an ad hoc search during example-hunting, not this systematic
sweep. The real picture is a *rarity gradient*, not a singular count — anywhere from 3 to 150 real
mirror-shaped bars exist depending where the threshold is drawn, always roughly 4–6x rarer than
pattern 1 at the same threshold, never literally unique. The threshold used for this document's
headline count (0.02 → 26 matches) is a reasonable middle point, not the "true" definition — there
isn't one.

### Pattern 3 — mixed/alternating (n=2,662)

| Horizon | contained | broke out |
|---|---|---|
| next 1 bar | 27.3% (726) | 72.6% (1,933) |
| next 3 bars | 15.7% (418) | 84.0% (2,235) |

Baseline (all 59,605 bars, same normalized-move/band test): **34.4%** contained / 65.6% broke out
at 1 bar. So pattern-3 bars break out **more** often than a random bar (72.6% vs. 65.6% at 1 bar,
a real gap, not just noise around the baseline) — heavy, evenly-matched two-sided churn with no net
winner is, in this real dataset, associated with an *elevated* chance of a subsequent breakout, not
a dampened one. Signed direction of that breakout is not predicted by this pattern (mean next-1
change −0.48, essentially flat) — only that *something* moves.

### Pattern 4 — tight range, then resolution (157 real episodes)

Every episode (by construction — see below) resolves as either **broke_up (70, 44.6%)** or
**broke_down (87, 55.4%)**; zero resolve as "still contained." That's not a substantive finding so
much as a structural fact worth being upfront about: an "episode" is defined as a maximal run of
consecutive bars satisfying the tight-range test, so the bar immediately following it is, by
definition, the first bar where the range condition broke — "still contained" would just mean the
episode hadn't ended yet. 55.4% vs. 44.6% is a mild down-skew, but with n=157 that's roughly
1.4 standard errors from even — real, but not strong enough to call a reliable bias.

## Part 1 — Does bar geometry change any of this?

Dataset-wide bar duration: median **33.7s**, IQR 17.3s–77.4s (these are 500-contract volume bars,
so duration is entirely a function of how fast that volume traded, not a clock). "Fast" = below
the dataset median; "shape" from real OHLC, `close_pos = (close−low)/(high−low)`.

**Pattern 1** (n=104 with geometry data): fast bars (n=94) split continued 44.7% / reversed 29.8% /
sideways 25.5%; slow bars (n=10, too few to trust) skewed reversed. By shape, close-near-high
(n=26) and close-near-low (n=28) both still land close to the pooled 27/36/36 split — no material
shape effect at this sample size.

**Pattern 2** (n=21 with geometry): the per-cell counts here get down to 0–6 bars per bucket
(e.g., "close near high" is 2 bars total). **Not a usable sample for a geometry claim** — shown for
completeness, not conclusions.

**Pattern 3** (n=2,363 with geometry): this is the one real, robust negative result in Part 1 — the
broke-out rate is **83.0%–84.1% in every single slice** (fast/slow, close-near-high/mid/low). Bar
speed and shape make essentially no difference to this pattern's outcome. Whatever is driving
pattern 3's elevated breakout rate, it isn't bar geometry.

**Pattern 4 episodes** (n=131 with duration data): episode wall-clock duration median 177s (up to
2,295s at the extreme). Fast episodes (<median) resolve broke_down 60.0%/broke_up 40.0%; slow
episodes resolve broke_down 51.5%/broke_up 48.5% — a mild difference, small sample, not strong
enough to lean on.

## Part 2 — Trade-level drill-down, made standard

The first guide's mirror example noted that its aggregated picture was really one 386-lot resting
bid add, not broad participation — a check that, at the time, was done only for that one bar. Here
it's applied as a standard method: **5 real, diverse bars per pattern** (different sessions/dates
each), with two lenses:

1. **Raw trade prints** — every individual execution inside the bar's exact
   `[bar_start_ts_ns, bar_end_ts_ns]` window, pulled directly from `trades.ndjson` (not the
   aggregated cache).
2. **Price-level dominance** — since `signed_flow`/`abs_flow` are *book/quote* events, not trades
   (Part 0 of the first guide), the real analog of "one dominant order" is one price level's
   `abs_flow` share of the bar's total, not one trade's share of volume. Both are reported because
   they answer genuinely different questions.

**Trade-print concentration** — top-1 and top-3 trade's share of the bar's total traded volume,
across all 25 sampled bars (5 per pattern, plus the two `p4` rows are the range's last bar and its
resolution bar):

| pattern | top1_share range | top3_share range | n_trades range |
|---|---|---|---|
| 1 | 0.8%–9.2% | 1.8%–19.3% | 335–477 |
| 2 | 1.2%–5.4% | 3.0%–11.7% | 419–473 |
| 3 | 1.0%–4.0% | 2.6%–5.8% | 408–471 |
| 4 (range/resolve) | 0.7%–2.4% | 1.7%–5.4% | 392–465 |

Every single sampled bar, across every pattern, is broadly distributed at the trade-print level —
no bar's volume is dominated by one or two large executions. Buy/sell trade-volume splits also sit
close to 50/50 in every sample (e.g. 375/125 was the widest split observed, still not a
one-sided-print story). **Genuine broad participation in executed volume is the norm across all
four patterns**, not an exception.

**Price-level (book) dominance** — the metric that actually matches the mirror case's real
mechanism:

| pattern | samples | mean max-level share of bar's total abs_flow |
|---|---|---|
| 1 | 5 | 2.3% |
| 2 (mirror) | 5 | 4.2% |
| 3 | 5 | 1.5% |

The mirror pattern's fresh samples ranged 3.3%–5.3%. **Bar 42099 — the first guide's own
illustrative mirror example — was at ~36%, an order of magnitude above every other real mirror bar
sampled here.** That's a direct, honest correction: the original example was a genuine outlier
even within an already-rare pattern, not a representative case of "how mirror bars usually form."
Most real mirror bars, like most real bars of any pattern, reflect broad, distributed order-book
activity across dozens of price levels — single dominant orders are the exception, not the
mechanism.

## Part 3 — Market-state classification

States are assigned mechanically from data already in hand — no new subjective category — using
each bar's own preceding/following 10-bar window (`N=10`):

- **trending**: `|prior 10-bar close change|` in the top tercile (≥ 36.75 points)
- **ranging**: in the bottom tercile (≤ 16.0 points) and prior 10-bar total `abs_flow` NOT
  elevated
- **absorption**: bottom-tercile price movement but prior 10-bar total `abs_flow` in the top
  tercile (≥ 202,364) — lots of book activity, little net price progress
- **reversing**: prior and following 10-bar moves have opposite sign, both exceeding the
  dataset's median magnitude
- **transitional**: none of the above (the largest single bucket)

Baseline distribution, all bars: transitional 28.6%, trending 23.9%, ranging 23.3%, reversing
13.1%, absorption 9.4%, unknown 1.8% (edge-of-session bars without a full 10-bar window either
side).

| | Pattern 1 (n=126) | Pattern 2 (n=26) | Pattern 3 (n=2,662) | baseline |
|---|---|---|---|---|
| trending | 15.1% | 7.7% (2) | 23.1% | 23.9% |
| ranging | 42.1% | 38.5% (10) | 20.8% | 23.3% |
| reversing | 7.1% | 19.2% (5) | 13.4% | 13.1% |
| absorption | 0.8% (1) | 0.0% | 10.8% | 9.4% |
| transitional | 34.1% | 23.1% (6) | 29.5% | 28.6% |

**Patterns 1 and 2 both cluster disproportionately in "ranging" context** — 42.1% and 38.5%
respectively vs. a 23.3% baseline — meaning a clean directional split within one bar's cells is
more likely to show up when the broader 10-bar window was already quiet, not when the market was
trending. (Pattern 2's per-state counts are small — 2 to 10 bars per bucket — so treat its state
split as directional, not precise.)

**Pattern 3 is close to state-agnostic**: its distribution across states tracks the baseline
closely (23.1% vs 23.9% trending, 20.8% vs 23.3% ranging, etc.), and critically, its elevated
broke-out rate holds regardless of state — 83.0%–87.4% in every one of the five states. Whatever
this pattern is picking up on, it isn't specific to trending, ranging, reversing, or absorption
conditions; it shows up everywhere at a similar rate and means the same thing (elevated breakout
odds) everywhere.

## Synthesis

Putting Parts 0–3 together, the two clean, genuinely robust findings across this dataset are:

1. **Pattern 3 (mixed/alternating) is the most reliable of the four** in the narrow sense that its
   elevated breakout rate (vs. baseline) survives every cut in this document — geometry, market
   state, sample-diversity trade drilldown. It never predicts *direction*, only that price is
   statistically more likely to move than to sit still.
2. **Patterns 1 and 2 (the two directional-cell shapes) do not show a reliable directional edge** in
   the raw sweep, but do show a real context signature — they show up more often in already-quiet
   ("ranging") stretches. Pattern 2 in particular remains too rare (26 real matches at this
   threshold) for firm conclusions of any kind; every claim about it in this document should be
   read as directional, not decisive.

None of this is a validated trading signal — it's what this real dataset actually contains, with
sample sizes stated at every step so the reader can judge which numbers are load-bearing and which
aren't.
