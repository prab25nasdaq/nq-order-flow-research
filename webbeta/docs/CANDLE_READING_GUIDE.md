# Reading Book-Flow Level Candles — A Data-Grounded Guide

SHADOW/RESEARCH ONLY. Every example below is a real bar pulled from actual `NQU6` production
cache history (`book_flow_chart/cache/*_top10.parquet`), and every screenshot is the real
production chart (webbeta) rendering that real data — not a mockup. Nothing here is synthetic.

**See also:** [`CANDLE_READING_GUIDE_STATISTICS.md`](CANDLE_READING_GUIDE_STATISTICS.md) — a full
dataset-wide statistical sweep (all real matches per pattern, not one example each), bar-geometry
effects, a raw trade-print drill-down, and market-state cross-tabs. It also revises one claim made
below (the mirror pattern's rarity) once measured systematically rather than found ad hoc.
[`CANDLE_READING_GUIDE_STATES.md`](CANDLE_READING_GUIDE_STATES.md) — the same 5 market states,
reorganized as the primary frame, with recognition/trading-implication/exits per state.

## Part 0 — What a cell's color actually means

Read directly from `static/app.js` (`colorGroupForCell`) and `build_book_flow_level_cache.py`.

Each row/cell in a bar is one price level. Its two numbers:

- **`signed_flow`** — net directional change in *resting order-book depth* at that price level
  during the bar, computed exclusively from raw bid/ask quote-update events (never from trades):
  - Bid-side size increases → **positive** (green). Bid-side size decreases (pulled) → **negative** (red).
  - Ask-side size increases (offers added) → **negative** (red). Ask-side size decreases (pulled) → **positive** (green).
- **`abs_flow`** — `abs(signed_flow)`, the magnitude of that change regardless of direction. This
  sets the cell's brightness: `alpha = 45 + 210 * clamp(abs_flow / p95_visible, 0, 1)`, where
  `p95_visible` is the 95th percentile of `abs_flow` across every cell currently on screen — so
  brightness is relative to what's visible, not an absolute scale.
- **Gray** — `signed_flow == 0` exactly, a genuine tie between add/pull pressure at that level.

**The critical point: this is a liquidity/order-book signal, not a trade/aggressor-volume
signal.** Green means "the book got more bid-supportive or less offer-supportive here." It does
not mean "buyers traded here." A price level can be green while zero contracts actually traded
at it, if resting size simply grew there.

This was cross-checked against `book-flow-level-candles-explained.md`, the system's own design
doc, which independently confirms the same definition.

## Part 1 — Four real patterns

Each 500-contract volume bar packs many price levels into one candle. What you're reading is the
**vertical arrangement of green/red within a single bar**, and how that arrangement relates to what
price does next. Four shapes recur in real history:

---

### 1. Green concentrated above, red concentrated below

**Bar 34050, NQU6 2026-07-29 session, sealed 2026-07-30 13:30:00.43 UTC. Close 27871.25.**

113 price levels, 27863.50–27892.50. Split at the midpoint (27878.00):

| Half | Price range | Σ signed_flow | Σ abs_flow |
|---|---|---|---|
| Lower | 27863.50–27878.00 | **−153** | 1571 |
| Upper | 27878.25–27892.50 | **+115** | 1885 |

Almost every level below 27878 pulled bids or added offers (red); almost every level above 27878
added bids or pulled offers (green) — a clean, broad-based split, not one outlier event.

![Pattern 1](candle_reading_guide_images/pattern1_green_above_red_below/example1_bar34050_2026-07-29.png)

Five more real examples of this pattern (bars 10149, 18857, 27118, 7614, 11549) are in
[`candle_reading_guide_images/pattern1_green_above_red_below/`](candle_reading_guide_images/pattern1_green_above_red_below/) — the same sample used for the statistical sweep in the companion doc.

**What price actually did next:** bar 34051 closed 27844.00 (**−27.25**, a sharp drop straight
through the bar's own lower half), then bar 34052 reversed hard to 27888.50 (**+44.50**), then bar
34053 settled to 27881.25 (**−7.25**). In this real instance the pattern did **not** predict clean
continuation — it was followed by a sharp fake-down/reversal-up whipsaw. Two other real instances
of this shape (bar 15505, 2026-07-05; bar 47392, 2026-08-17) are smaller/thinner bars with the same
sign arrangement but far less volume behind it — the shape recurs at multiple scales, but a strong,
broad-based version like 34050 is what makes it legible at a glance.

---

### 2. The mirror: red concentrated above, green concentrated below

*[Revised by the systematic sweep in the companion doc — see its Part 0 and Part 2. The "only
qualifying instance" framing below came from ad hoc example-hunting, not a full sweep; a real
rarity gradient exists (3–150 matches depending on threshold), and this bar's own single-order
dominance turns out to be an outlier even among other real mirror bars, not their typical mechanism.]*

This is the rarest real shape in the entire dataset. Across all **59,605 real closed bars** in the
53-session sample (2026-06-03 through 2026-08-17), only **one bar** cleanly qualified, even after
relaxing the selection threshold.

**Bar 42099, NQU6 2026-08-09 session, sealed 2026-08-10 13:34:26.83 UTC. Close 29746.00.**

70 price levels, 29743.25–29765.25. Split at the midpoint (29751.75):

| Half | Price range | Σ signed_flow | Σ abs_flow |
|---|---|---|---|
| Lower | 29743.25–29751.75 | **+314** | 1060 |
| Upper | 29752.00–29765.25 | **−67** | 143 |

![Pattern 2](candle_reading_guide_images/pattern2_mirror/example1_bar42099_2026-08-09.png)

Five more real examples (bars 29339, 7232, 45602, 22208, 18382) are in
[`candle_reading_guide_images/pattern2_mirror/`](candle_reading_guide_images/pattern2_mirror/) — none of them show anywhere near this bar's single-order dominance (see the companion doc's Part 2).

**Why this one is instructive, not just rare:** the lower half's +314 is almost entirely one
event — a single **386-lot bid add at 29749.00** (`abs_flow=434` at that level alone). Strip that
one level out and the lower half's own sum flips to **−72** — i.e. without that one large resting
order, this bar would look like ordinary two-sided noise, not a mirror at all. The upper half, by
contrast, is a broad, unremarkable drizzle of small red (−67 across 35+ levels, no single
dominant print). **The honest takeaway: a clean mirror shape in real data is far more likely to be
one large resting order distorting an otherwise incoherent half than a genuine broad two-sided
reversal in flow.** That scarcity (1 in 59,605) is itself the finding — treat this shape as a flag
for "one big order sat here," not as a reliable, repeatable setup.

**What price actually did next:** bar 42100 closed 29748.75 (+2.75), bar 42101 closed 29750.75
(+2.00, back up near the big bid), then bar 42102 dropped to 29736.50 (**−14.25**). A brief hold
near the large resting bid, then it gave way.

---

### 3. Genuinely mixed / alternating

**Bar 36679, NQU6 2026-08-02 session, sealed 2026-08-03 13:34:27.09 UTC. Close 28389.50.**

73 price levels. Sign flips almost every level (flip_rate 0.667 — two out of three adjacent levels
disagree in sign) while `abs_flow` is large throughout, climbing as high as 582 at a single level.
Net bar score (Σsigned/Σabs) is **0.0015** — essentially zero. This is not a quiet bar; it's a bar
with heavy, genuinely two-sided contested activity that cancels out to no net winner.

![Pattern 3](candle_reading_guide_images/pattern3_mixed_alternating/example1_bar36679_2026-08-02.png)

Five more real examples (bars 27125, 20523, 16617, 33600, 14475) are in
[`candle_reading_guide_images/pattern3_mixed_alternating/`](candle_reading_guide_images/pattern3_mixed_alternating/).

**What price actually did next:** context close 28370.75 → 28392.00 → 28388.25 → **28389.50 (this
bar)** → 28394.50 (+5.00, a brief continuation attempt) → 28381.50 (**−13.00**, a real reversal) →
28380.00 → 28379.50, finishing the sequence roughly 10 points below where the mixed bar closed. In
this real instance, heavy two-sided churn with no net winner preceded a false push and then a
genuine reversal — not continuation. One real instance, not a rule.

---

### 4. A short real range, and how it resolved

**Bars 41974–41977, NQU6 2026-08-09 session, 2026-08-10 05:13:08–05:38:37 UTC.**

Four consecutive bars, closes pinned within **0.75 points** (29911.00 / 29911.50 / 29910.75 /
29911.25), each with a near-zero net score despite heavy volume:

| Bar | Close | Σ signed_flow | Σ abs_flow | net score |
|---|---|---|---|---|
| 41974 | 29911.00 | −18 | 25,200 | −0.0007 |
| 41975 | 29911.50 | −63 | 15,377 | −0.0041 |
| 41976 | 29910.75 | −66 | 27,244 | −0.0024 |
| 41977 | 29911.25 | +15 | 32,017 | +0.0005 |

Tens of thousands of `abs_flow` per bar, essentially zero net direction each time — real two-sided
balance, not a quiet market.

![Pattern 4](candle_reading_guide_images/pattern4_range_then_breakout/example1_bars41974-41980_2026-08-09.png)

Five more real range episodes (2 breaking up, 3 breaking down) are in
[`candle_reading_guide_images/pattern4_range_then_breakout/`](candle_reading_guide_images/pattern4_range_then_breakout/).

**What price actually did next:** bar 41978 dipped slightly to 29908.25 (score −0.0033, still
balanced), then bar 41979 broke out with a clearly positive score (**+0.029**, the highest of the
whole window) and closed 29929.25 — **+21.00** from the prior bar, continuing to 29935.00 the bar
after. In this real instance, the range held for one more quiet bar past the visible pin, then
resolved with a bar whose *score*, not just its price move, was visibly different from the four
balanced bars before it.

## How these screenshots were made

Each is the real webbeta chart, driven by a temporary local replay session built from the exact
bar-index range shown, sourced read-only from the real production parquet cache via the same
loader the desktop app uses (`book_flow_data_service`). No chart code, coloring logic, or data was
modified to produce these — this is the production renderer showing real history.

## A pattern to take away, stated carefully

Across all four examples, the **score of a bar's flow** (not just where price closed) seemed to
carry information about what came next: the mixed bar (score ≈ 0) preceded a reversal; the balanced
range bars (score ≈ 0) held until a bar with a real non-zero score appeared, which is when the
range actually broke. The clean directional bar (34050) did *not* produce a clean continuation.
These are four verified real instances, not a backtested rule — treat this guide as literacy in
what the colors mean and how to read one bar's internal structure, not as a trading signal.
