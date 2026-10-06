# Book Flow Chart — Step 2.5 Part B: Slow-Frame Forensics (measurement only, no fix applied here)

SHADOW / RESEARCH ONLY. Read-only against the live cache daemon (PID 2527) throughout; nothing
written to any cache/data file. All measurements post-date the Step 2/2.5 Part A sig fixes
(both the GUI-thread `_current_data_sig()` fix and the `CacheFileMonitor` legacy-path backport).

## Methodology note (important, found while building the classifier)

The first attempt at this capture used the same window-construction pattern
`perf_harness.py`'s `run_live()` uses throughout Step 2: construct with a literal date string,
then set `win.live_latest_mode = True` afterward. **This is wrong** — `show_forming_bar`,
`context_mode`, and `previous_context_sessions` are derived from `live_latest_mode` *during*
`__init__` (`self.show_forming_bar = self.live_latest_mode`, etc.), so setting the attribute
after construction does not retroactively fix them. Every measurement using that pattern was
silently testing `show_forming_bar=False` + single-date context — **not** the real default
live-latest UX (`show_forming_bar=True`, +1 previous session). This was caught because
`forming-update` never appeared at all in the first capture, which made no sense given the
forming cache updates every ~350ms by design.

**This retroactively caveats Step 2's own soak/RSS numbers too** (same bug in
`perf_harness.py`'s `run_live()`, used for all of Step 2's Part B/C/D measurements) — those
numbers describe a lighter configuration than what a default-configured user actually sees. Step
2 is already merged and out of scope to redo here; noting it for the record. `frame_classifier.py`
fixes this by constructing with `date="latest"` and asserting the expected defaults hold.

A second methodology bug was found and fixed while building the classifier: `_reload()` can fire
more than once per outer test-loop iteration (a cross-thread signal can deliver and fire a second
`_reload()` during one `app.processEvents()` call), and timing the outer loop's own call would
silently miscount which frame the cost belongs to. Fixed by wrapping `_reload` directly. A third
bug (classifier priority order): checking `pan-zoom` before `bar-roll`/`forming-update` swallowed
every roll/update into "pan-zoom" in live/follow mode, since the view auto-scrolls on every real
content change — fixed by reordering (see `frame_classifier.py`'s docstring for the exact rule).

## Headline numbers (30-min live, true default UX — 5187 frames)

| class | count | share | p50 ms | p95 ms | max ms | >50ms n | >50ms % |
|---|---|---|---|---|---|---|---|
| forming-update | 2052 | 39.6% | 422.7 | 682.0 | 907.9 | 2052 | 100.0% |
| frame-consume | 1179 | 22.7% | 287.3 | 447.2 | 552.9 | 1179 | 100.0% |
| other (slow-path, no content driver) | 414 | 8.0% | 495.6 | 690.5 | 960.1 | 414 | 100.0% |
| bar-roll | 63 | 1.2% | 451.5 | 643.3 | 742.4 | 63 | 100.0% |
| idle-blit | 1479 | 28.5% | 2.0 | 52.3 | 128.8 | 81 | 5.5% |

**Overall: p50=283.1ms, p95=612.4ms, max=960.1ms. 73.0% of ALL frames exceed 50ms.**

>50ms frame share by class: forming-update 54.2%, frame-consume 31.1%, other 10.9%, idle-blit
2.1%, bar-roll 1.7%.

Cross-tabulation against which code path fired (`fast_a` = background-preloaded snapshot
consumed; `slow` = GUI-thread direct blocking read; `fast_b` = data unchanged, cheap):

```
idle-blit       fast_b    1479   (all cheap, as expected)
frame-consume   fast_a    1179
forming-update  slow      1030   <- roughly half of forming-update goes through EITHER path
forming-update  fast_a    1022   <-
other           slow       414
bar-roll        slow        42
bar-roll        fast_a      21
```

`forming-update` splits almost exactly 50/50 between the two paths — the forming cache updates so
frequently (~350ms daemon cadence) that the background worker pipeline can't always keep up,
causing frequent slow-path fallbacks alongside the fast-path deliveries. Both paths converge on
the same expensive downstream work (see below), which is why the class-level cost is
similar regardless of path.

## 15-minute replay (interactive pan sweep, unaffected by the live-mode construction bug)

100% pan-zoom by design (continuous simulated pan): p50=55.3ms, p95=106.3ms, max=300.9ms, 82.7%
of frames exceed 50ms. Materially better than live mode's tail, but still above the 50ms target —
consistent with Step 1's own replay numbers (p95 93.5-100ms in every measurement across both
steps) and not the focus of this investigation (live mode's tail is far worse: 612ms vs 106ms p95).

## py-spy stack evidence (both a 90s post-sig-fix probe and a 90s true-default-UX probe)

`py-spy record --format raw`, own-child mode (ptrace_scope=1 requires this — attaching to an
already-running unrelated PID needs sudo, not used). Top leaf frames by sample count, true-default
probe (4854 samples):

```
 490  raw_decode (json/decoder.py:361)              <- JSON parsing, see call chain below
 443  copy (pandas/core/internals/blocks.py:822)
 213  convert (pandas/core/internals/construction.py:1030)
 202  _merge_blocks (pandas/core/internals/managers.py:2320)
 197  comp_method_OBJECT_ARRAY (pandas/core/ops/array_ops.py:129)
 174  _list_of_dict_to_arrays (pandas/core/internals/construction.py:924)
 131  _load_bars (book_flow_chart_v3.py:217)
 125  vstack (numpy/_core/shape_base.py:292)
 112  concatenate_managers (pandas/core/internals/concat.py:177)
 106  _take_nd_ndarray (pandas/core/array_algos/take.py:162)
  94  factorize_array (pandas/core/algorithms.py:595)
  ...
  53  set_data (book_flow_chart_v3.py:633)           <- the actual QPicture-rebuild render step
  48  _apply_snapshot (book_flow_chart_v3.py:1488)
  33  set_data (book_flow_chart_v2.py:269)
```

Call chain for the dominant `raw_decode` leaf (488 of 490 samples):

```
raw_decode <- decode <- loads <- load_vol500_bars (book_flow_lib.py:305)
  <- _load_bars (book_flow_chart_v3.py:217)
      <- _bg_load_snapshot (book_flow_chart_v3.py:754)      [324 samples, background thread]
      <- _load_context_frames (book_flow_chart_v3.py:1349)  [164 samples, GUI-thread slow path]
```

**`load_vol500_bars()` re-parses the ENTIRE session's vol500 bars ndjsonl file, line-by-line via
`json.loads()`, from scratch, on every single reload that triggers a background reparse OR a
GUI-thread slow-path load** — not incrementally. This file grows by one line per sealed bar over
the session (tens of thousands of lines by mid/late session), so every trigger pays an O(total
bars) cost instead of the O(1)-ish cost of just reading what's new. This single call chain
accounts for ~10% of all sampled time by itself, and the pandas DataFrame-construction machinery
immediately downstream of it (`convert`, `_merge_blocks`, `_list_of_dict_to_arrays`, `vstack`,
`concatenate_managers`, `factorize_array`, etc. — several hundred more samples) is overwhelmingly
attributable to building a DataFrame from those freshly-parsed rows, not to anything render-side.

The actual render step (`set_data`'s QPicture rebuild) is real but small by comparison: **53 + 33
= 86 samples out of 4854 (1.8%)**.

## Verdict (named, before any fix)

**The mission's hypothesis ("post-sig-fix, the tail is render-side, not I/O") is confirmed at the
category level — `forming-update` is unambiguously the dominant slow-frame class — but NOT at the
mechanism level.** The cost inside forming-update (and frame-consume, and bar-roll, and the
residual slow-path "other") frames is overwhelmingly **I/O and data-prep, not QPicture rendering**:
every trigger that reaches `_load_bars()` (whether via the background worker's `_bg_load_snapshot`
or the GUI-thread's `_load_context_frames`) re-parses the *entire* vol500 bars ndjsonl file from
scratch via line-by-line `json.loads()`, and the pandas DataFrame construction immediately
downstream of that parse dominates the sampled time. The actual `set_data()` QPicture-rebuild
render step is present but small (~1.8% of sampled time) by comparison. This is a *different*
I/O mechanism than the one Step 2 fixed (that was about *how often* a reparse gets triggered; this
is about *how expensive* each triggered reparse is, once triggered, regardless of path) —
consistent with, not contradicting, Step 2's own finding that repeated full re-reads are the
recurring theme in this codebase's performance problems.

## Implication for Part C

The mission's three suggested candidates (incremental forming-bar repaint / off-UI-thread
rasterization / spread sealed-layer rebuilds) are all specifically about the render/QPicture step
— which this evidence shows is a *minor* contributor to forming-update's cost, not the dominant
one. Per the mission's explicit "chosen from the evidence" framing (candidates listed "in likely
order," not mandatory), Part C targets the mechanism the evidence actually names: **the full-file,
from-scratch reparse of the vol500 bars ndjsonl file, shared across every expensive frame class**.
A caching/incremental-read fix here has a chance to move all four expensive classes at once
(forming-update, frame-consume, other, bar-roll), unlike a render-only fix which the evidence
suggests would leave ~98% of forming-update's cost untouched.
