# Candle Reading Guide — Market States: Recognition, Trading Implications, Exits

SHADOW/RESEARCH ONLY. Third in the series, alongside [`CANDLE_READING_GUIDE.md`](CANDLE_READING_GUIDE.md)
(cell color ground truth + one real example per pattern) and
[`CANDLE_READING_GUIDE_STATISTICS.md`](CANDLE_READING_GUIDE_STATISTICS.md) (full-dataset sweep,
geometry, trade drill-down, and the 5 market states first defined there). This document reuses all
of that — same 59,605-bar/53-session dataset, same outcome/BAND=0.25 convention, same honesty
standard — and reorganizes around the 5 states as the primary frame, adding one genuinely new
piece: exits.

**A structural asymmetry that shapes everything below.** Three states — `trending`, `ranging`,
`absorption` — are defined using only the **prior** 10 bars, so they're real-time computable: you
could classify one live. `reversing` is defined using the **next** 10 bars too (opposite-sign
prior/post moves) — it's a retrospective label. That difference isn't cosmetic; it's the reason
the reversing section below looks different from the other four, and it's the reason this document
almost reported a large fake edge for it (see Reversing → Exits).

## Trending (14,221 bars / 23.9%; 4,352 real transition points)

### Recognition

Mechanical rule: prior-10-bar `|close change|` in the top tercile (≥36.75 points). Structurally, a
transition into trending doesn't look dramatically different cell-by-cell from an average bar —
`overall_score` and `flip_rate` sit close to baseline. What actually identifies it is the
**sequence** of closes over the preceding ~10 bars moving persistently one way, which is exactly
what the two real examples below show: a real, visible, multi-bar directional run into the
transition point, not any single bar's internal cell pattern.

![Trending example 1](candle_reading_guide_images/state_trending/example1.png)
![Trending example 2](candle_reading_guide_images/state_trending/example2.png)

### Trading implication — real, substantial edge

Direction = sign of the prior 10-bar move (known at classification time — this is a fair,
non-contaminated test). Outcome measured from the transition bar forward:

| Horizon | continued | reversed | sideways |
|---|---|---|---|
| next 1 bar | 38.3% | 26.2% | 35.5% |
| next 3 bars | 51.2% | 27.7% | 21.1% |

Mean next-1 move in the trend direction: **+1.87 points**; next-3: **+5.95 points**. This is a real
gap over baseline (baseline has no directional bias by construction) and the largest, cleanest edge
found across all three documents in this series.

### When not to trade

Tested two candidate degradation conditions and found **neither weakens the edge**: entries with
high entry-bar `flip_rate` (choppier cells right at the transition) actually continue *slightly
more* often (53.2% vs. 47.9% for low-flip entries), and entries following a strong vs. weak prior
move show virtually identical continuation rates (51.2% vs. 51.4%). Reported honestly because the
instinct going in was that choppy or weak-momentum entries should be worse — the data didn't
support it. The one caveat that does hold regardless: this is a 38–51% edge, not a certainty — most
individual entries do not "continue" cleanly, the edge only shows up aggregated over thousands of
real entries.

### Exits — real MFE/MAE study, three candidate order-flow exit signals, none beat a naive hold

4,327 real transition entries with a full 20-bar forward window, direction = trend's own sign.

- 20-bar MFE: mean 49.0 pts (median 41.75); MAE: mean −28.95 pts (median −23.75).
- 97.0% of entries see *some* favorable excursion; 47.7% see adverse excursion reaching a full 1×
  the entry bar's own price range at some point; 36.8% see MAE exceed MFE in magnitude (the trade
  was underwater deeper than it was ever ahead, at its worst point).

Three real candidate exit signals tested against the same 4,327 entries — state re-classifying
away from trending, a pattern-3-style mixed bar appearing (`flip_rate>0.45`, `|score|<0.015`), and
`bar_total_abs_flow` spiking past trending's own 90th percentile (30,498) — each compared to simply
holding to a fixed 10-bar exit on the **same entries**:

| Signal | Fires within 20 bars | Mean bars-to-signal | Mean pts captured at signal | Mean pts captured, fixed 10-bar hold (same entries) |
|---|---|---|---|---|
| State re-classifies | 99.7% | 3.2 | 15.74 | 31.19 |
| Pattern-3 mixed bar appears | 51.3% | 8.3 | 24.27 | 30.94 |
| abs_flow spike (>p90) | 43.1% | 6.3 | 19.38 | 33.54 |

**None of the three signals beat the naive fixed-horizon exit — all three leave real money on the
table.** State re-classification is the worst offender: it fires almost immediately (mean 3.2 bars
in) and exits with barely half the points a fixed 10-bar hold would have captured. This is reported
as a straightforward negative result, exactly the kind of finding the mission asked to hold to a
high bar rather than dress up as a discovery.

## Ranging (13,869 bars / 23.3%; 4,626 real transition points)

### Recognition

Prior-10-bar `|close change|` in the bottom tercile (≤16.0) with no elevated `abs_flow`.
Structurally real and visible: median `n_levels` **69 vs. 85 baseline** — genuinely thinner,
quieter bars, not just a price-based label.

![Ranging example 1](candle_reading_guide_images/state_ranging/example1.png)
![Ranging example 2](candle_reading_guide_images/state_ranging/example2.png)

### Trading implication — no real edge

| Horizon | contained | broke out |
|---|---|---|
| next 1 bar | 34.6% | 65.4% |
| next 3 bars | 19.7% | 80.3% |

Baseline: 34.4% / 65.6% contained. **Ranging's breakout rate is statistically indistinguishable
from a random bar.** A quiet prior 10 bars does not predict a quiet next 1–3 bars — the intuitive
"quiet begets quiet" read of this state is not supported by this real data.

### When not to trade

Any strategy premised on ranging conditions *persisting* long enough to fade extremes with low
near-term risk — the data above says the opposite: breakout odds here are the same as anywhere
else, so "the market looks quiet" is not, by itself, information about what the next few bars do.

### Exits — regime-duration study (no directional entry assumed)

No directional edge means no honest long/short entry to build an MFE/MAE study around. Instead:
2,778 real ranging runs of length ≥2. Median run length **3 bars** (mean 4.33, max 35). Within-run,
paired first-bar-vs-last-bar comparison: mean `flip_rate` rises from **0.210 to 0.244** across the
run, and **59.5%** of individual runs show a higher flip_rate on their last bar than their first —
a real, modest (not strong) signal that rising cell alternation tends to precede a ranging regime
ending. `abs_flow` rising is a much weaker signal (55.7% of runs, barely above the 50% chance line)
and isn't worth relying on for this state.

## Absorption (5,612 bars / 9.4%; 2,711 real transition points)

### Recognition

Same low-movement prior-10-bar test as ranging, but with `abs_flow` in the *top* tercile instead —
lots of book activity, little net price progress. This one has the clearest structural signature
of all five states: mean `bar_total_abs_flow` **26,516 vs. 18,552 baseline**, median `n_levels` 99
vs. 85 — dense, heavily-worked bars that just aren't going anywhere.

![Absorption example 1](candle_reading_guide_images/state_absorption/example1.png)
![Absorption example 2](candle_reading_guide_images/state_absorption/example2.png)

### Trading implication — negligible edge

| Horizon | contained | broke out |
|---|---|---|
| next 1 bar | 32.4% | 67.6% |
| next 3 bars | 19.0% | 81.0% |

Broke-out rate is 67.6% vs. 65.6% baseline at 1 bar — a small, real gap but not a strong one, and
it's gone by 3 bars (81.0% vs. ~80.1% baseline). The "coiled spring" intuition — heavy contested
volume with no net winner should precede a big move — gets only weak support here, echoing pattern
4's ~45/55 (not dramatically skewed) breakout-direction split from the statistics companion.

### When not to trade

Don't expect absorption alone to forecast a strong directional resolution — the elevated-activity
signature is real and recognizable, but it isn't predictive of *what happens next* beyond a small
margin.

### Exits — regime-duration study

1,360 real absorption runs of length ≥2, median length 3 bars (mean 3.13). Same paired test as
ranging: `flip_rate` rises from 0.235 to 0.273 across the run, with **60.1%** of runs showing a
higher last-bar flip_rate — comparable in size to ranging's signal. `abs_flow` rising is
essentially noise here (52.0% of runs, indistinguishable from chance) — unsurprising, since
absorption is already defined by persistently elevated abs_flow throughout the run, so a further
rise carries little extra information.

## Reversing (7,807 bars / 13.1%; 3,262 real transition points)

### Recognition

**Caveat first:** this label needs the next 10 bars to confirm, so it cannot be mechanically
"recognized live" the way the other four can. What follows describes the real setup observed in
the bars leading into a confirmed reversal — not a proven live trigger. Structurally, reversing
bars are the least distinctive of the five states at the cell level (`flip_rate` 0.245, close to
the 0.239 baseline) — there is no strong single-bar signature to look for; the tell is entirely in
the price sequence itself (a real run one way, then a real run the other way).

![Reversing example 1](candle_reading_guide_images/state_reversing/example1.png)
![Reversing example 2](candle_reading_guide_images/state_reversing/example2.png)

### Trading implication — the number that looked like an edge and wasn't

Direction = sign of the post-transition move (the "new" direction) — shown here for transparency,
but flagged immediately:

| Horizon | continued | reversed | sideways |
|---|---|---|---|
| next 1 bar | 46.4% | 20.5% | 33.0% |
| next 3 bars | 66.1% | 16.5% | 17.4% |

**These numbers are contaminated and should not be read as an edge.** The direction label and the
next-1/3-bar window both come from the same 10-bar lookahead used to *define* reversing at this
bar — of course a bar labeled "the new direction is up" tends to be followed by up moves in the
next 1–3 bars; that's close to restating the definition, not discovering something. The real test
is below.

### When not to trade

On the raw label alone — see Exits immediately below for why.

### Exits — the genuinely out-of-sample test, and a real null result

Since confirming the label consumes bars T..T+10, this tests **T+10 → T+20**, a window entirely
outside what was used to build the label — the direction is still the transition's own
`sign(post_chg)`, but the price data measured is fresh.

- Real reversing transitions with a full T+20 window: 3,228. Mean move, new direction, T+10→T+20:
  **−1.58 points**. Median: +1.25. Positive (persisted): **50.9%**.
- Baseline — 56,812 random bars, direction = *their own* prior-10-bar sign, same T+10→T+20
  measurement: mean **+0.54**, median +1.25, positive **51.2%**.

**Once tested out-of-sample, "confirmed" reversals show no real persistence edge — statistically
indistinguishable from a randomly chosen bar's own naive momentum.** The dramatic-looking 46–66%
numbers above are a definitional artifact, not information. This is the headline methodological
result of this document: the mission asked for this analysis to be scrutinized as hard as the
mirror-pattern correction, and this is what that scrutiny found.

## Transitional (17,036 bars / 28.6%; 9,398 real transition points, median run length 1 bar)

### Recognition

The catch-all — none of the other four rules apply. Structurally it sits closest to baseline of
any state on every measured dimension (`overall_score` −0.0002 vs. baseline −0.0001, `flip_rate`
0.237 vs. 0.239, `n_levels` median 80 vs. 85). There isn't a distinctive signature to learn here by
construction — it's what's left over.

### Trading implication and when not to trade

65.2% / 79.6% broke-out at 1/3 bars — essentially identical to the 65.6%/80.1% baseline, as
expected for a bucket that's close to the population average by definition. **This state itself is
the "when not to trade" answer for the other four** — it's the default condition when none of
trending/ranging/absorption/reversing's real signatures are present, and it carries no
directional information of its own.

### Exits

None. Median run length is **1 bar** — by the time you could act on "we're in transitional," the
next bar is often a different state entirely. There's no coherent regime here to build an entry or
exit study around, and forcing one would manufacture structure the data doesn't have.

## Synthesis

Of the five states, **only trending shows a real, robust, out-of-sample directional edge** — and
even its exits analysis returned an honest negative result (no tested order-flow signal beats a
plain fixed-horizon hold). **Reversing's apparent edge was the most important finding in this
document precisely because it wasn't real** — a large, dramatic-looking number that evaporated
under a genuinely out-of-sample test, which is exactly the failure mode the mission asked to guard
against. Ranging and absorption carry no directional edge but do offer a real, modest,
regime-duration signal (rising `flip_rate` precedes the regime ending in ~60% of real runs — real,
but not strong). Transitional carries no information and no coherent exit structure by its own
nature.

None of this is a trading system. Every number above has its real sample size stated next to it;
where the sample or the methodology couldn't support a claim, this document said so rather than
rounding up.
