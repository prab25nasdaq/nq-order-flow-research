"use strict";
/*
 * webbeta canvas frontend. No framework, no build step.
 *
 * W2.2 Part B: there is exactly ONE cell renderer, at every zoom level -- no aggregated/blocky
 * fallback (RENDER_SPEC.md section 8 documents why: the desktop's own WIDE/OVERVIEW LOD does
 * change representation at wide zoom, and this browser deliberately does not replicate that).
 * Performance at wide zoom comes ONLY from bitmap caching: sealed-bar cells are rendered once into
 * an offscreen canvas and reused (drawImage) until the view transform or the sealed-bar set
 * changes; the forming bar is always redrawn live, every frame, on top. Caching never changes what
 * is computed -- computeVisibleCellGeometry() is the single source of truth for both the cache-fill
 * pass and the live pass, and for the geometry-parity test hook -- so the picture is identical with
 * caching on or off (toggle via window.__setCachingEnabled(false) or ?nocache=1).
 *
 * Mirrors book_flow_chart_v3.py's rendering philosophy where it matters for the gates:
 *   - cell candles drawn as batched rects (grouped by color so ctx.fillStyle changes are
 *     minimized, not per-cell).
 *   - order-book side panel sharing the SAME price (y) axis as the main chart -- here that's
 *     exact by construction (both draw functions take the same View.yMin/yMax and canvas
 *     height), not an approximation.
 *   - full-width current-price line + a persistent current-cell highlight, both repositioned in
 *     place every frame, never recreated.
 *   - stale badge: greys out the book panel when now - bookTs > BOOK_STALE_SECS, matching
 *     book_flow_data_service.BOOK_STALE_SECS exactly.
 *   - render loop draws ONLY on new data or interaction (a `dirty` flag gates
 *     requestAnimationFrame) -- no busy loop.
 *
 * Mouse dynamics (RENDER_SPEC.md section 9, cited from pyqtgraph's ViewBox/AxisItem defaults --
 * book_flow_chart_v3.py has no override):
 *   - wheel over the plot body: both axes scale together, anchored at the cursor's data point.
 *   - wheel over the x-axis strip (bottom) / y-axis strip (right): that ONE axis scales, anchored
 *     at the cursor's coordinate on that axis.
 *   - left-drag over the plot body: pan, both axes.
 *   - left-drag over an axis strip: pan, that axis only.
 *   - right-drag over the plot body: scale, both axes, anchored at the button-down point.
 *   - right-drag over an axis strip: scale, that axis only ("change candle length with the mouse").
 *   - Reset View / follow-live: View.userSet is this browser's analog of the desktop's
 *     user_view_override -- ANY manual wheel/drag sets it, and only Reset View clears it, exactly
 *     matching book_flow_chart_v3.py's _on_user_interaction()/_reset_view() precedence.
 */

const TICK = 0.25;
const BOOK_STALE_SECS = 10.0;
const UP_COLOR = "#00d27a";
const DOWN_COLOR = "#ff4d6d";
const PRICE_COLOR = "#55aaff";

// MISSION toolbar-light-theme: same persistence mechanism as Depth (setDepthSilhouetteEnabled
// below) -- a URL query param round-tripped via history.replaceState, not a new localStorage
// mechanism (this codebase has none; Depth's own toggle is the existing precedent to match).
let lightTheme = new URLSearchParams(location.search).get("theme") === "light";

// Canvas colors can't be styled via CSS. Every substitution here was measured directly (real
// WCAG contrast ratios against #ffffff, not guessed) and confirmed via real before/after
// screenshots before this was built -- see the MISSION toolbar-light-theme proposal. tc() is a
// no-op in dark theme (unchanged default); in light theme it substitutes exactly these colors and
// passes anything else through untouched, so a call site either IS one of these exact strings or
// isn't affected at all -- no silent partial substitution.
const LIGHT_COLOR_MAP = {
  "#050505": "#ffffff",
  "#55aaff": "#0d4d8f",                          // PRICE_COLOR
  "#3388ee": "#0a3d80",                          // drawing: horizontal line
  "#00d27a": "#007a3d",                          // UP_COLOR (market line, up-tick)
  "#ff4d6d": "#a10f38",                          // DOWN_COLOR (market line, down-tick)
  "#ffa94d": "#cc6a00",                          // MARKET_COLOR
  "#ffd700": "#b8860b",                          // zscore latest-bar threshold line
  "#00ff00": "#0a8a3a",                          // zscore z<=-4
  "#66ff66": "#2fa855",                          // zscore -4<z<=-2 / reference line -2
  "#99ff99": "#5cb87a",                          // zscore -2<z<0 (pale)
  "#cccccc": "#8a8a8a",                          // zscore ==0 (pale)
  "#ff9999": "#d97a7a",                          // zscore 0<z<=2 (pale)
  "#777777": "#888888",                          // zscore null/NaN
  "#ffd166": "#7a5000",                          // drawing: trend line / signInAgainLink
  "#c792ea": "#6b2f8f",                          // drawing: fib line
  "#ffffff": "#333333",                          // crosshair/highlight stroke, zscore zero-line
  "#dddddd": "#333333",                          // depth hover text
  "#88ffaa": "#4fae72",                          // zscore reference line -1.5
  "#33dd33": "#1f8f3d",                          // zscore reference line -3
  "#ff8888": "#c85c5c",                          // zscore reference line +2
  "#ffaa88": "#b97a52",                          // zscore reference line +1.5
  "#00ccff": "#0a7fa8",                          // zscore metric color: delta
  "#ffcc00": "#a87c00",                          // zscore metric color: volatility
  "#ff66ff": "#a83fa8",                          // zscore metric color: sweep_imb
  "#ff8800": "#b85f00",                          // zscore metric color: vpin
  "rgba(255,255,255,0.24)": "rgba(0,0,0,0.24)",  // crosshair/highlight fill
  "rgba(255,255,255,0.9)": "rgba(0,0,0,0.85)",   // order-book best-row outline
  "rgba(255,255,255,0.12)": "rgba(0,0,0,0.12)",  // gridlines
  "rgba(255,255,255,0.07)": "rgba(0,0,0,0.07)",  // depth silhouette
  "rgba(180,180,180,0.55)": "rgba(100,100,100,0.7)", // crosshair line
};
function tc(color) {
  if (!lightTheme) return color;
  return LIGHT_COLOR_MAP[color] !== undefined ? LIGHT_COLOR_MAP[color] : color;
}

function setLightTheme(v) {
  lightTheme = v;
  const params = new URLSearchParams(location.search);
  params.set("theme", v ? "light" : "dark");
  history.replaceState(null, "", `${location.pathname}?${params.toString()}`);
  document.getElementById("themeToggleBtn").classList.toggle("active", v);
  document.body.classList.toggle("lightTheme", v);
  markDirty();
  zDirty = true;  // z-score panel has its own dirty flag, separate from the main/book canvases
}
window.__setLightTheme = setLightTheme; // test hook, matching __setDepthSilhouetteEnabled's convention
const AXIS_STRIP_X_PX = 22;  // bottom strip height: x-axis wheel/drag hit region
const AXIS_STRIP_Y_PX = 54;  // right strip width: y-axis wheel/drag hit region + price labels
// MISSION lookback-sessions-and-timestamps: #lookbackSelect now selects a number of whole trading
// SESSIONS (1-5), not a bar count -- the server (server/session_boundaries.py) decides which bars
// ship, keyed by real timestamps, not a fixed count. currentNSessions/Data.sessionBoundaries are
// populated from each snapshot's own n_sessions/session_boundaries fields (server/live_bridge.py's
// build_snapshot). FOLLOW_SPAN_BARS no longer exists -- view width after a lookback change is set
// by fitViewToAllBars() (below) to the actual fetched extent, since different session counts
// legitimately produce very different bar counts depending on how active those sessions were.
let currentNSessions = 2; // must match server/live_bridge.py's DEFAULT_N_SESSIONS
let pendingLookbackFit = false; // set when a {type:"lookback"} request is in flight

// A defensive bar-count ceiling only -- retention is actually governed by
// trimToSessionWindow() below (real time-based session cutoff, mirroring
// server/session_boundaries.py), matching how the server's own SNAPSHOT_MAX_BARS is now just a
// defensive ceiling on top of the real session-based filter, not the mechanism itself. Set above
// the server's own ceiling (8000) so this client-side backstop is never the tighter of the two.
const MAX_RETAINED_BARS = 10000;
function trimRetainedBars() {
  const excess = Data.bars.length - MAX_RETAINED_BARS;
  if (excess <= 0) return;
  const dropped = Data.bars.splice(0, excess);
  for (const bar of dropped) Data.cellsByPos.delete(bar.bar_pos);
}

// ---------------------------------------------------------------- client-side session cutoff ---
// Mirrors server/session_boundaries.py's exact rule (22:00 UTC session start, Sun/Mon/Tue/Wed/Thu
// only, Fri 21:00 UTC close -> Sun 22:00 UTC reopen) -- see that module's own docstring for why
// this is deliberately duplicated rather than round-tripped per tick: the rule is simple/static,
// and the INITIAL bar set for any lookback selection always comes authoritatively from the server
// regardless. This copy is used ONLY for continuous local trimming while a live session runs, so
// the "last N sessions" window keeps advancing between snapshots instead of going stale/growing
// unbounded until the next server round-trip.
const ONE_DAY_MS = 86400000;
function utcDateStartMs(ms) {
  const d = new Date(ms);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
}
function currentOrMostRecentSessionStartMs(nowMs) {
  const hour = new Date(nowMs).getUTCHours();
  let candidateMs = (hour >= 22 ? utcDateStartMs(nowMs) : utcDateStartMs(nowMs) - ONE_DAY_MS) + 22 * 3600 * 1000;
  let wd = new Date(candidateMs).getUTCDay(); // JS: 0=Sun..6=Sat
  while (wd === 5 || wd === 6) { // Fri, Sat -- no session ever starts on these days
    candidateMs -= ONE_DAY_MS;
    wd = new Date(candidateMs).getUTCDay();
  }
  return candidateMs;
}
function priorSessionStartMs(startMs) {
  const wd = new Date(startMs).getUTCDay();
  return startMs - (wd === 0 ? 3 : 1) * ONE_DAY_MS; // Sunday reopen -> prior started that Thursday
}
function lastNSessionsCutoffMs(n, nowMs) {
  let s = currentOrMostRecentSessionStartMs(nowMs);
  for (let i = 1; i < n; i++) s = priorSessionStartMs(s);
  return s;
}
function trimToSessionWindow() {
  if (!Data.bars.length) return;
  // "Now" is anchored to the DATA's own latest timestamp, never wall-clock Date.now() directly --
  // matching server/live_bridge.py's own _reference_now() and for the identical reason: in real
  // live mode the two agree (modulo normal poll latency), but a replay/simulated session dated in
  // the past would otherwise have every one of its bars look "expired" by a real-time cutoff and
  // get trimmed away entirely the first time this runs, regardless of currentNSessions.
  const latestTsNs = Data.frame.formingBar ? Data.frame.formingBar.bar_start_ts_ns
                                            : Data.bars[Data.bars.length - 1].bar_start_ts_ns;
  const cutoffTsNs = lastNSessionsCutoffMs(currentNSessions, latestTsNs / 1e6) * 1e6;
  let dropCount = 0;
  while (dropCount < Data.bars.length && Data.bars[dropCount].bar_start_ts_ns < cutoffTsNs) dropCount++;
  if (dropCount === 0) return;
  const dropped = Data.bars.splice(0, dropCount);
  for (const bar of dropped) Data.cellsByPos.delete(bar.bar_pos);
}
// Debug overlay (symbol/date/bar_idx/version) is hidden by default -- shown only via ?debug=1.
// Safe to touch the DOM directly here (no DOMContentLoaded wait needed): this script tag sits at
// the end of <body>, so #banner is already parsed by the time this line runs.
const DEBUG_OVERLAY = new URLSearchParams(location.search).get("debug") === "1";
if (DEBUG_OVERLAY) document.getElementById("banner").classList.add("debugVisible");

// W2.13 Part 0: substituted server-side by server/app.py's dedicated /app.js route (registered
// ahead of the StaticFiles mount specifically so it's never bypassed) -- this literal string is
// what actually ships in the raw static/app.js file on disk; a running server always replaces it
// with its own current BUILD_SHA before serving. Captured ONCE per page load (whatever this
// SPECIFIC fetched response contained), then compared on every /status poll (always fresh, never
// cached -- see app.py's no-store middleware) against the server's LIVE current value: if they
// ever differ, this browser tab is running on an app.js from a server build that has since been
// replaced, not the one actually live right now -- exactly the ambiguity that cost real
// investigation time in W2.11/W2.12/W2.13 itself.
const CLIENT_BUILD_SHA = "__WEBBETA_BUILD_SHA__";
if (DEBUG_OVERLAY) console.log(`[webbeta] client build_sha=${CLIENT_BUILD_SHA}`);
window.__CLIENT_BUILD_SHA__ = CLIENT_BUILD_SHA;

// ---------------------------------------------------------------- data model (from the wire) --
const EMPTY_FRAME = {
  version: 0, lastPrice: null, formingBar: null, formingCells: [],
  book: null, bookTs: null, bookStatus: "ok", bookStatusReason: null,
};

const Data = {
  symbol: null, date: null, depth: null,
  bars: [],                 // [{bar_index, bar_pos, px_close, bar_start_ts_ns}], sorted by bar_pos
  cellsByPos: new Map(),    // bar_pos -> [{price_level, signed_flow, abs_flow}]
  // W2.5 Part A / W2.6 Part A (RENDER_SPEC.md sections 13/14/19): the price line, forming cells,
  // current-cell highlight, AND the order book all render from this ONE object, sampled at one
  // instant server-side and replaced atomically here -- never from independently-mutated
  // top-level fields. setFrame() below is the ONLY place this is ever assigned; see
  // tests/test_atomic_frame.py for the code-level assert. book/bookTs/bookStatus arrive on their
  // own, slower cadence (book_update deltas) -- setFrame's patch merges onto the previous frame,
  // so a forming_update/bar_roll that doesn't touch the book carries the book fields forward
  // unchanged, exactly like it already does for lastPrice at the seal boundary.
  frame: EMPTY_FRAME,
  lastClosedBarIdx: null,
  replayEnded: false,       // W2.5 Part B: set by the server's explicit `replay_ended` message,
                            // cleared by the next snapshot/delta (the loop restarting).
  sessionBoundaries: [],    // [{start_ts_ns, end_ts_ns}, ...] from the last snapshot -- x-axis
                            // session-crossing markers, see drawAxisStrips().
};

// W2.5 Part B (RENDER_SPEC.md section 15): replay staleness must key off STREAM LIVENESS (are
// messages still arriving, on wall-clock time) -- never the recorded payload's own historical
// timestamp, which is always "old" by definition and would otherwise make the book panel show
// STALE for the entire duration of every replay session. Updated at the top of handleMessage() for
// every message kind, live or replay. Live mode's own staleness (book_ts vs BOOK_STALE_SECS) is
// untouched -- see isBookStale() below.
let lastMessageWallMs = Date.now();
const REPLAY_STALL_MS = 8000;

// W2.8 Part B (mission Part C gate (b): "...or their own age indicator explains why"): last_price
// is sourced from book_flow_data_service._last_price_from_cells's close_price field, which -- for
// a live, still-forming bar in the real production write pipeline -- is written once per bar and
// does not update again until the bar rolls (verified directly against the real, unmodified
// desktop code path: close_price stayed constant across a whole bar's forming lifetime while
// mid_price and the cell count both varied continuously). This is correct, current-desktop-
// matching behavior, not staleness -- but a price that hasn't moved in a while should say so
// rather than look ambiguous, so the order book's price chip (renderBook) shows an age once it's
// old enough (PRICE_AGE_SHOW_MS).
let lastPriceChangeWallMs = Date.now();
// MISSION market-line-direction: 'up' | 'down' | null -- last observed tick direction of
// book.mid, updated exactly once per real change (setFrame, mirroring lastPriceChangeWallMs's own
// pattern just above), not recomputed per render frame. drawMarketLine reads this to color the
// market line green/red instantly on the tick that moved it, rather than a fixed amber regardless
// of direction.
let marketDirection = null;
// Kept comfortably UNDER the mission's own "zero intervals >10s where cells move and price does
// not" gate threshold, so a genuinely stale price is always ALREADY visibly flagged by the time
// that gate would otherwise fire on it -- a real live run found the gap between a 30s threshold
// and the gate's 10s bar produced several "unexplained" samples that were actually this same,
// already-diagnosed close_price characteristic, just not yet flagged as stale on screen.
const PRICE_AGE_SHOW_MS = 8000;

// MISSION diagnose-bar-disappears-at-seal: real, live-confirmed evidence (this mission's own Part
// 1) showed Data.frame.formingBar going null for 1202ms during a real production bar-roll -- the
// upstream forming-cache meta file genuinely oscillates at the exact moment of every roll
// (confirmed directly: 46838<->46839, then 46840<->46841, both within ~3s of the actual roll).
// book_flow_data_service/webbeta faithfully report whatever the source currently says, including
// that transient null -- correct in principle, but the newest (currently forming) candle and its
// current-cell highlight (drawCellHighlight is gated on `!f.formingBar`) both vanish for that
// whole window as a direct result. This holds the last REAL forming bar/cells across a transient
// null for a short grace window, so a genuine ~1-2s upstream blip never reaches the screen, while
// still giving up (showing the honest null) if the gap turns out to be real and prolonged.
const FORMING_HOLD_GRACE_MS = 4000;
let heldFormingBar = null;   // {bar, cells} snapshot of the last known-real forming bar
let heldFormingSince = null; // performance.now() when the hold started

function applyFormingHold(patch) {
  const newForming = patch.formingBar;
  if (newForming) {
    // A real forming bar was reported (same bar continuing, or genuinely the next one) --
    // whichever it is, it supersedes anything held. No need to distinguish the two cases here.
    heldFormingBar = null;
    heldFormingSince = null;
    return patch;
  }
  if (!heldFormingBar) {
    const prevForming = Data.frame.formingBar;
    if (!prevForming) return patch; // nothing real to hold (e.g. the very first message ever)
    heldFormingBar = { bar: prevForming, cells: Data.frame.formingCells };
    heldFormingSince = performance.now();
  }
  if (performance.now() - heldFormingSince < FORMING_HOLD_GRACE_MS) {
    return { ...patch, formingBar: heldFormingBar.bar, formingCells: heldFormingBar.cells };
  }
  // Grace expired with nothing superseding it -- give up and apply the real null rather than
  // hold a genuinely stale/abandoned bar forever.
  heldFormingBar = null;
  heldFormingSince = null;
  return patch;
}

function setFrame(version, patch) {
  // Object.freeze: a genuine runtime guarantee, not just convention -- this file is "use strict",
  // so any code that tried to mutate a field of an already-published frame (rather than calling
  // setFrame() again) would throw a TypeError, not silently drift. tests/test_atomic_frame.py
  // asserts against this directly. Spreading the PREVIOUS frame first means any field `patch`
  // doesn't mention (e.g. book/bookTs on a forming_update) carries forward unchanged.
  const prevPrice = Data.frame.lastPrice;
  const prevMid = Data.frame.book ? Data.frame.book.mid : null;
  const effectivePatch = ("formingBar" in patch) ? applyFormingHold(patch) : patch;
  Data.frame = Object.freeze({ ...Data.frame, ...effectivePatch, version });
  if (Data.frame.lastPrice !== prevPrice) {
    lastPriceChangeWallMs = Date.now();
  }
  const newMid = Data.frame.book ? Data.frame.book.mid : null;
  if (prevMid != null && newMid != null && newMid !== prevMid) {
    marketDirection = newMid > prevMid ? "up" : "down";
  }
}

function resetData() {
  Data.bars = [];
  Data.cellsByPos = new Map();
  Data.frame = EMPTY_FRAME;
  Data.lastClosedBarIdx = null;
  Data.replayEnded = false;
  heldFormingBar = null;
  heldFormingSince = null;
  marketDirection = null;
}

function cellsFromWire(w) {
  const n = w.bar_idx.length;
  const out = new Array(n);
  for (let i = 0; i < n; i++) {
    out[i] = { price_level: w.price_level[i], signed_flow: w.signed_flow[i], abs_flow: w.abs_flow[i] };
  }
  return out;
}

// W4.1 Part 2: bestBid/bestAsk/mid back the "market" indicator -- the continuously-updating price
// line (see MISSION market-line-only). Confirmed via direct source grep (rithmic_live_features.py's
// Vol500BarBuilder): last_price/close_price is fed EXCLUSIVELY by apply_trade() (trade prints),
// never by apply_book_obs() (quote/book updates) -- so "mid" is what answers "where is the market
// right now" on every book refresh, independent of whether a trade has printed. undefined-safe:
// older replay recordings (pre-W4.1) never carried best_bid/best_ask at all, so mid is simply null
// for those -- drawMarketLine below already no-ops when null.
function bookFromWire(w) {
  if (!w) return null;
  return {
    prices: w.prices, bidSizes: w.bid_sizes, askSizes: w.ask_sizes,
    bestBid: w.best_bid ?? null, bestAsk: w.best_ask ?? null, mid: w.mid ?? null,
  };
}

// -------------------------------------------------------------------------- viewport (pan/zoom) --
const View = { xMin: 0, xMax: 50, yMin: 0, yMax: 100, userSet: false };
let dirty = true;
function markDirty() { dirty = true; }

// W2.8 Part A: tracks whether autoFitView() has ever computed a real view for the CURRENT session
// context. RENDER_SPEC.md section 9 (`_update_view_ranges()`): the desktop only does its full
// default-span-plus-y-fit on the FIRST render (`not self._initialized`) or while
// `follow_live_requested` is explicitly engaged -- and even then it re-derives a FIXED-width
// window (`FOLLOW_WINDOW=180`) every time, not a translate of whatever width the view currently
// has. Reset via resetViewBtn / Connect / session-switch (a genuinely fresh view context);
// NEVER touched by data arriving (snapshot/delta), which is the whole point of this mission.
let viewInitialized = false;

// MISSION lookback-sessions-and-timestamps: replaces the old FOLLOW_SPAN_BARS-windowed first fit.
// Each lookback option now maps to a genuinely different fetched bar count depending on how active
// those sessions were (not a fixed span) -- so "fit the view" means "show everything the server
// just sent for this selection," both on first connect and on every lookback change. Used from
// autoFitView() (first-ever fit) and directly from handleMessage's snapshot branch (a fresh fit
// after the user changes #lookbackSelect, since viewInitialized is already true by then and
// autoFitView()'s own steady-state branch would otherwise just translate the OLD span/width).
function fitViewToAllBars() {
  const bars = Data.bars;
  if (!bars.length) return;
  const lastPos = (Data.frame.formingBar ? Data.frame.formingBar.bar_pos : bars[bars.length - 1].bar_pos);
  View.xMax = lastPos + 0.8;
  View.xMin = Math.max(-1, bars[0].bar_pos - 0.8);
  let lo = Infinity, hi = -Infinity;
  for (const b of bars) {
    if (b.px_close < lo) lo = b.px_close;
    if (b.px_close > hi) hi = b.px_close;
  }
  if (Data.frame.lastPrice != null) { lo = Math.min(lo, Data.frame.lastPrice); hi = Math.max(hi, Data.frame.lastPrice); }
  if (!isFinite(lo) || !isFinite(hi)) { lo = 0; hi = 100; }
  const pad = Math.max((hi - lo) * 0.15, 5);
  View.yMin = lo - pad;
  View.yMax = hi + pad;
  viewInitialized = true;
}

function autoFitView() {
  if (View.userSet) return;
  const bars = Data.bars;
  if (!bars.length) return;
  const lastPos = (Data.frame.formingBar ? Data.frame.formingBar.bar_pos : bars[bars.length - 1].bar_pos);
  if (!viewInitialized) {
    fitViewToAllBars();
    return;
  }
  // Already initialized and still following (not userSet): translate x ONLY, preserving
  // whatever width/zoom is currently in effect -- this mission's explicit requirement ("MUST NOT
  // alter zoom level, bar width, or y-range" while follow-live is engaged). This is a deliberate,
  // documented divergence from the desktop's own re-fit-every-time formula above (cited exactly
  // where it's actually used -- the one-time initial fit); a continuous width-refit on every
  // delta is what produced this mission's reported jitter or made a stable follow view impossible
  // to reason about.
  const span = View.xMax - View.xMin;
  View.xMax = lastPos + 0.8;
  View.xMin = View.xMax - span;
}

// ------------------------------------------------------------------------------- WebSocket -----
let ws = null;
let currentSessionName = null;
let currentSpeed = 1;
let lastSeq = null; // gap-detection cursor for live-mode deltas, see handleMessage()

function wsUrl(token, session) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const host = location.host || "127.0.0.1:8800";
  return `${proto}//${host}/ws?token=${encodeURIComponent(token)}&session=${encodeURIComponent(session || "")}`;
}

function setStatus(text, isStale) {
  const el = document.getElementById("status");
  el.textContent = text;
  el.classList.toggle("stale", !!isStale);
}

const LIVE_SESSION_NAME = "live";
let lastStatusPoll = null;
let statusPollTimer = null;

function setModeBadge(cls, text) {
  const el = document.getElementById("modeBadge");
  el.hidden = false;
  el.className = cls;
  el.textContent = text;
}

// User request: hide the top-bar badge entirely during a plain stale/no-data period (not
// CELLS STALLED -- that stays a distinct, deliberately-visible state per W2.11 Part B's own
// "never a green LIVE badge over a frozen chart" rule, a different bug class this wasn't asked
// to touch). Badge reappears automatically via setModeBadge's el.hidden = false once any other
// state (LIVE, CELLS STALLED, REPLAY, REPLAY ENDED) is next set.
function hideModeBadge() {
  document.getElementById("modeBadge").hidden = true;
}

window.__buildShaMismatchWarned = false;
async function pollStatus(token) {
  try {
    const resp = await fetch(`/status?token=${encodeURIComponent(token)}`);
    if (resp.status === 401) { handleSessionRevoked(); return; }
    if (resp.ok) lastStatusPoll = await resp.json();
  } catch (e) { /* transient -- next poll retries */ }
  // W2.13 Part 0: active regardless of ?debug=1 -- a stale-cached app.js is exactly the kind of
  // thing nobody thinks to check for until it's already cost an investigation. Warned once (not
  // every 5s poll) since the mismatch, once true, stays true for the rest of this page load.
  if (lastStatusPoll && lastStatusPoll.build_sha && lastStatusPoll.build_sha !== CLIENT_BUILD_SHA
      && !window.__buildShaMismatchWarned) {
    window.__buildShaMismatchWarned = true;
    console.warn(
      `[webbeta] BUILD SHA MISMATCH: this page's own app.js was fetched from build ` +
      `${CLIENT_BUILD_SHA}, but the server is now running ${lastStatusPoll.build_sha} -- ` +
      `reload the page to pick up the current code.`
    );
  }
  updateModeBadge();
}

function updateModeBadge() {
  // MISSION lookback-sessions-and-timestamps: session-count lookback is a live-only concept (a
  // replay session is fixed, pre-recorded data with no "current session" to count back from) --
  // server/app.py's receiver_loop only actions {type:"lookback"} while session_name === "live".
  // Disabled (not hidden) during replay so its purpose stays visible even when inapplicable.
  document.getElementById("lookbackSelect").disabled = currentSessionName !== LIVE_SESSION_NAME;
  if (currentSessionName !== LIVE_SESSION_NAME) {
    // W2.5 Part B: end-of-session is a distinct, expected/benign state -- never the market-
    // staleness "stale" badge (that concept doesn't apply to replay at all; see isBookStale()).
    if (Data.replayEnded) {
      setModeBadge("replayEnded", "REPLAY ENDED");
      return;
    }
    setModeBadge("replay", `REPLAY: ${currentSessionName || "--"}`);
    return;
  }
  if (!lastStatusPoll || !lastStatusPoll.has_frame) {
    hideModeBadge();
    return;
  }
  const age = lastStatusPoll.market_age_s;
  const threshold = lastStatusPoll.market_stale_secs;
  if (age == null || age >= threshold) {
    hideModeBadge();
    return;
  }
  // W2.11 Part B: "never a green LIVE badge over a frozen chart" -- market_age_s alone (sealed-bar
  // recency) can look perfectly fresh while the forming-cell stream specifically is stuck (this
  // mission's exact bug: price/book healthy, cells frozen). Overrides LIVE with a distinct,
  // equally-visible state sourced directly from the server's own cells_stalled signal.
  if (lastStatusPoll.cells_stalled) {
    setModeBadge("stale", "CELLS STALLED");
    return;
  }
  setModeBadge("live", "LIVE");
}

function startStatusPolling(token) {
  stopStatusPolling();
  pollStatus(token);
  statusPollTimer = setInterval(() => pollStatus(token), 5000);
}

function stopStatusPolling() {
  if (statusPollTimer) { clearInterval(statusPollTimer); statusPollTimer = null; }
}

// --------------------------------------------------------------------------- z-scores panel ---
// MISSION zscores-panel: independent REST poll (GET /zscores), same pattern as pollStatus above --
// deliberately NOT folded into the WS wire protocol, so this feature can't affect the book-flow
// stream at all. Z_BINS/Z_LABELS/zColor are copied verbatim from server/zscores.py (itself copied
// verbatim from the OFI Dashboard's own Z_BINS/Z_LABELS/z_color) so all three stay provably the
// same scale, not three independent reimplementations that could quietly drift apart.
const Z_BINS = [-Infinity, -4, -3, -2, -1.5, 0, 1.5, 2, 3, 4, Infinity];
const Z_LABELS = ["z≤-4", "-4<z≤-3", "-3<z≤-2", "-2<z≤-1.5", "-1.5<z≤0",
                   "0<z≤1.5", "1.5<z≤2", "2<z≤3", "3<z≤4", "z>4"];

function zColor(v) {
  if (v === null || v === undefined || !Number.isFinite(v)) return tc("#777777");
  if (v >= 4) return "#ff0000";
  if (v >= 2) return "#ff5555";
  if (v > 0)  return tc("#ff9999");
  if (v <= -4) return tc("#00ff00");
  if (v <= -2) return tc("#66ff66");
  if (v < 0)  return tc("#99ff99");
  return tc("#cccccc");
}

let zscorePollTimer = null;
let zData = null;       // last /zscores payload
let zDirty = false;
let zSelected = "off";  // mirrors #zscoreSelect's current value
// MISSION zscores-panel-viewport-sync bugfix: this must be undefined (not "") until polling has
// actually been started -- a real cookie-authenticated page load (the retire-token-urls mission's
// normal case) passes token="" into connect()/openSocket() throughout this file, which is a VALID,
// working token for require_auth (it falls back to the session cookie), just an empty string. `if
// (!zToken) return` treated that falsy-but-valid "" the same as "never started", silently skipping
// every fetch forever -- exactly the reported "stuck on loading z-scores" with zero requests ever
// reaching the server. Checked with `=== undefined` below instead, so "" is correctly treated as a
// real, already-started token.
let zToken = undefined;
let zLastRange = null;  // [lo, hi] last actually requested, so an unchanged viewport doesn't refetch every tick
let zLastFetchAt = 0;

// MISSION zscores-panel-viewport-sync: the panel must show the SAME bars currently visible in the
// main chart -- including scrolled-back history -- not just "the latest 60". bar_pos is what View
// is expressed in; bar_index (real, stable, matches the z-score master file 1:1 -- verified, see
// server/zscores.py) is what /zscores is keyed by. This walks Data.bars once to convert one to the
// other; deliberately NOT called every animation frame (see the 500ms check below), since it's an
// O(bars-on-screen) scan.
function visibleBarIndexRange() {
  const bars = Data.bars;
  if (!bars.length) return null;
  let lo = null, hi = null;
  for (const b of bars) {
    if (b.bar_pos < View.xMin - 1 || b.bar_pos > View.xMax + 1) continue;
    if (lo === null || b.bar_index < lo) lo = b.bar_index;
    if (hi === null || b.bar_index > hi) hi = b.bar_index;
  }
  if (lo === null) { lo = bars[0].bar_index; hi = bars[bars.length - 1].bar_index; }
  return [lo, hi];
}

async function fetchZScoreRange(token, lo, hi, metric) {
  try {
    // MISSION tester-feedback issue 6: measured directly -- omitting `metric` shipped all 5
    // metrics' full value arrays every request (~225KB for a full-history range) even though the
    // panel only ever renders ONE at a time. Requesting just the selected one cut that ~5x.
    const resp = await fetch(`/zscores?token=${encodeURIComponent(token)}&lo=${lo}&hi=${hi}&metric=${encodeURIComponent(metric)}`);
    if (resp.status === 401) { handleSessionRevoked(); return; }
    if (resp.ok) { zData = await resp.json(); zDirty = true; }
  } catch (e) { /* transient -- next tick retries */ }
}

// MISSION tester-feedback issue 6: measured directly -- a few bars of harmless viewport jitter from
// autoFitView() (called on nearly every incoming live message) used to count as a "range changed"
// every time, forcing a full refetch on almost every 500ms tick (real capture: 0.4-3.5s effective
// interval, avg ~1.9s). REFETCH_SLACK_BARS absorbs that jitter without changing what's shown (the
// panel only has room for so many bars regardless); a real pan/zoom still crosses this immediately.
const REFETCH_SLACK_BARS = 5;

// Runs every 500ms regardless of whether the panel is visible (cheap: one array scan + at most one
// fetch), so switching a metric ON shows the CURRENT viewport immediately rather than waiting for
// the old "latest 60" default to load first.
function zscoreViewportTick() {
  if (zToken === undefined) return;  // polling not started yet -- see the "" vs undefined note above
  if (zSelected === "off") return;   // nothing renders while off -- no metric to fetch for either
  const range = visibleBarIndexRange();
  if (!range) return;
  const [lo, hi] = range;
  // zLastRange is forced to null by setZPanelVisible() on every metric-select change (including
  // switching from one metric to another) -- so !zLastRange still catches "need a fresh fetch for
  // the newly-selected metric" even when the bar range itself hasn't moved at all.
  const rangeChanged = !zLastRange
    || Math.abs(zLastRange[0] - lo) > REFETCH_SLACK_BARS
    || Math.abs(zLastRange[1] - hi) > REFETCH_SLACK_BARS;
  const staleTimer = Date.now() - zLastFetchAt > 3000;  // still refresh a held-still range periodically (new live bars)
  if (rangeChanged || staleTimer) {
    zLastRange = [lo, hi];
    zLastFetchAt = Date.now();
    fetchZScoreRange(zToken, lo, hi, zSelected);
  }
}

function startZScorePolling(token) {
  stopZScorePolling();
  zToken = token;
  zLastRange = null;
  zscoreViewportTick();
  zscorePollTimer = setInterval(zscoreViewportTick, 500);
}

function stopZScorePolling() {
  if (zscorePollTimer) { clearInterval(zscorePollTimer); zscorePollTimer = null; }
}

function setZPanelVisible(visible) {
  document.getElementById("zscorePanel").classList.toggle("visible", visible);
  document.getElementById("zscoreResizeHandle").classList.toggle("visible", visible);
  resizeCanvases();  // #zscorePanel's own box just changed size outside any window-resize event
  zLastRange = null;  // force an immediate fetch for the current viewport on next tick, not a stale one
  zDirty = true;
}

document.getElementById("zscoreSelect").addEventListener("change", (ev) => {
  zSelected = ev.target.value;
  setZPanelVisible(zSelected !== "off");
});

// MISSION zscores-panel-viewport-sync: vertical drag-resize. min/max are generous but bounded --
// unbounded would let the handle drag the panel to consume (or invert) the candle area entirely.
(function setupZPanelResize() {
  const handle = document.getElementById("zscoreResizeHandle");
  const MIN_H = 50, MAX_H = 480;
  let dragging = false, startY = 0, startH = 112;

  handle.addEventListener("mousedown", (ev) => {
    dragging = true;
    startY = ev.clientY;
    const cur = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--zpanel-h")) || 112;
    startH = cur;
    handle.classList.add("dragging");
    document.body.style.userSelect = "none";
    ev.preventDefault();
  });
  window.addEventListener("mousemove", (ev) => {
    if (!dragging) return;
    // dragging the handle UP should grow the panel (handle sits above it) -- so invert the delta.
    const newH = Math.min(MAX_H, Math.max(MIN_H, startH - (ev.clientY - startY)));
    document.documentElement.style.setProperty("--zpanel-h", `${newH}px`);
    resizeCanvases();
    zDirty = true;
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove("dragging");
    document.body.style.userSelect = "";
  });
})();

function renderZScorePanel() {
  const cssW = zscoreCanvas.clientWidth, cssH = zscoreCanvas.clientHeight;
  zscoreCtx.clearRect(0, 0, cssW, cssH);
  if (zSelected === "off" || cssW <= 0 || cssH <= 0) return;

  zscoreCtx.fillStyle = tc("#050505");
  zscoreCtx.fillRect(0, 0, cssW, cssH);

  if (!zData || !zData.available || !zData.metrics || !zData.metrics[zSelected]) {
    zscoreCtx.fillStyle = "#ff6666";
    zscoreCtx.font = "11px 'Segoe UI', sans-serif";
    zscoreCtx.fillText(
      zData && !zData.available ? `z-scores unavailable: ${zData.reason || "no data"}` : "loading z-scores...",
      8, cssH / 2);
    return;
  }

  const m = zData.metrics[zSelected];
  const values = m.values.filter((v) => v !== null && Number.isFinite(v));
  const padL = 6, padR = 6, padT = 4, padB = 16;
  const plotW = Math.max(1, cssW - padL - padR);
  const plotH = Math.max(1, cssH - padT - padB);

  let lo = -2.5, hi = 2.5;
  if (values.length) {
    const fmin = Math.min(...values), fmax = Math.max(...values);
    const span = Math.max(fmax - fmin, 0.75);
    const pad = Math.max(0.35, span * 0.18);
    lo = Math.min(fmin - pad, -0.25);
    hi = Math.max(fmax + pad, 0.25);
    for (const lvl of [-4, -3, -2, -1.5, 1.5, 2, 3, 4]) {
      if (lo - 0.4 <= lvl && lvl <= hi + 0.4) { lo = Math.min(lo, lvl - 0.15); hi = Math.max(hi, lvl + 0.15); }
    }
    lo = Math.max(lo, -8.0); hi = Math.min(hi, 8.0);
    if (hi - lo < 1.0) { const mid = (hi + lo) / 2; lo = mid - 0.5; hi = mid + 0.5; }
  }
  const toY = (v) => padT + plotH - ((v - lo) / (hi - lo)) * plotH;

  // reference lines, same levels as the dashboard's own _draw_z
  for (const [lvl, c] of [[4, "#ff0000"], [3, "#ff5555"], [2, tc("#ff8888")], [1.5, tc("#ffaa88")],
                            [0, tc("#ffffff")], [-1.5, tc("#88ffaa")], [-2, tc("#66ff66")], [-3, tc("#33dd33")], [-4, tc("#00ff00")]]) {
    if (lvl < lo || lvl > hi) continue;
    const y = toY(lvl);
    zscoreCtx.strokeStyle = c; zscoreCtx.globalAlpha = 0.35; zscoreCtx.lineWidth = lvl === 0 ? 1 : 0.75;
    zscoreCtx.setLineDash(lvl === 0 ? [] : [3, 3]);
    zscoreCtx.beginPath(); zscoreCtx.moveTo(padL, y); zscoreCtx.lineTo(padL + plotW, y); zscoreCtx.stroke();
  }
  zscoreCtx.setLineDash([]); zscoreCtx.globalAlpha = 1.0;

  const n = m.values.length;
  const barW = Math.max(1, (plotW / n) * 0.82);
  const step = plotW / n;

  zscoreCtx.beginPath();
  for (let i = 0; i < n; i++) {
    const v = m.values[i];
    const vv = (v === null || !Number.isFinite(v)) ? 0 : v;
    const x = padL + i * step + step / 2;
    const y0 = toY(0), y1 = toY(vv);
    zscoreCtx.fillStyle = zColor(v);
    zscoreCtx.globalAlpha = 0.90;
    zscoreCtx.fillRect(x - barW / 2, Math.min(y0, y1), barW, Math.max(1, Math.abs(y1 - y0)));
    if (i === 0) zscoreCtx.moveTo(x, toY(vv)); else zscoreCtx.lineTo(x, toY(vv));
  }
  zscoreCtx.globalAlpha = 0.78;
  zscoreCtx.strokeStyle = tc(m.color);
  zscoreCtx.lineWidth = 1.05;
  zscoreCtx.stroke();
  zscoreCtx.globalAlpha = 1.0;

  // latest-bar highlight, matching the dashboard's gold-outline "current bar" marker
  if (n > 0) {
    const i = n - 1;
    const v = m.values[i]; const vv = (v === null || !Number.isFinite(v)) ? 0 : v;
    const x = padL + i * step + step / 2;
    zscoreCtx.strokeStyle = tc("#ffd700"); zscoreCtx.lineWidth = 1.2; zscoreCtx.setLineDash([4, 3]);
    zscoreCtx.beginPath(); zscoreCtx.moveTo(x, padT); zscoreCtx.lineTo(x, padT + plotH); zscoreCtx.stroke();
    zscoreCtx.setLineDash([]);
    zscoreCtx.fillStyle = tc("#ffd700");
    zscoreCtx.beginPath(); zscoreCtx.arc(x, toY(vv), 2.6, 0, 2 * Math.PI); zscoreCtx.fill();
  }

  // label + NOW/Δ/bucket readout, same fields as the dashboard's own text box
  zscoreCtx.fillStyle = tc(m.color);
  zscoreCtx.font = "bold 10px 'Segoe UI', sans-serif";
  zscoreCtx.fillText(m.label, padL, 12);

  const latestTxt = m.latest === null ? "--" : (m.latest >= 0 ? "+" : "") + m.latest.toFixed(2);
  const deltaTxt = m.delta === null ? "--" : (m.delta >= 0 ? "+" : "") + m.delta.toFixed(2);
  const readout = `now ${latestTxt}   Δ ${deltaTxt}   ${m.bucket}`;
  zscoreCtx.font = "10px 'Segoe UI', sans-serif";
  const tw = zscoreCtx.measureText(readout).width;
  // Self-contained dark chip (dark bg + white text) -- legible regardless of the page's own
  // background, so this one deliberately does NOT change with the theme (same reasoning as the
  // crosshair's own price/time label chips below).
  zscoreCtx.fillStyle = "rgba(16,16,16,0.88)";
  zscoreCtx.fillRect(cssW - tw - 14, 2, tw + 10, 14);
  zscoreCtx.strokeStyle = tc(m.color); zscoreCtx.lineWidth = 1; zscoreCtx.strokeRect(cssW - tw - 14, 2, tw + 10, 14);
  zscoreCtx.fillStyle = "#fff";
  zscoreCtx.fillText(readout, cssW - tw - 9, 12);

  // MISSION zscores-panel-viewport-sync: this now shows whatever bar_index range the main chart
  // is currently scrolled to -- not always "the latest" -- so the real range is the honest label.
  zscoreCtx.fillStyle = tc("#777777");
  zscoreCtx.font = "9px 'Segoe UI', sans-serif";
  const rangeTxt = (zData.lo_bar != null && zData.hi_bar != null)
    ? `bar ${zData.lo_bar} → ${zData.hi_bar}` : "";
  zscoreCtx.fillText(rangeTxt, padL, cssH - 4);
}

// W2.4 Part D: single-connection state machine. `connGeneration` is the source of truth for
// "which connect() call is current" -- every socket captures the generation it was opened under,
// and checks it on every event (open/close/message) before acting. This replaces a shared
// `manualDisconnect` boolean that was set true then immediately reset to false synchronously
// within connect() itself, before the OLD socket's close event could ever fire (WebSocket close is
// always asynchronous) -- found directly: that flag could never actually be observed as true by
// the handler it was meant to guard, so an old socket's close from a rapid re-connect/session-
// switch was treated as an unexpected drop and could trigger a redundant extra reconnect. Per-
// socket generation comparison has no such timing window: it's a plain integer comparison, correct
// regardless of when the event fires.
let lastConnectArgs = null;
let connGeneration = 0;
let reconnectAttempts = 0;
let reconnectTimer = null;
const MAX_RECONNECT_ATTEMPTS = 8;
const BACKOFF_BASE_MS = 500;
const BACKOFF_MAX_MS = 8000;
window.__wsDropCount = 0;              // unexpected closes (test/soak observability)
window.__wsReconnectCount = 0;         // successful auto-reconnects after a drop
window.__wsSuppressedCloseCount = 0;   // intentional/superseded closes correctly NOT reconnected

function setConnState(state) {
  const el = document.getElementById("connState");
  if (el) { el.textContent = state; el.className = state.split(" ")[0]; }
}

// MISSION tester-feedback issue 5: a session revoked mid-connection (new login under the same
// credential, or the local admin tool's Force Logout) used to fall into the generic "unexpected
// drop" branch below -- confirmed live: the client burned through all 8 reconnect attempts against
// a cookie that will never re-authenticate, ending on a meaningless "disconnected (code 1006)" with
// the server's own 4001/"session revoked" close reason never shown anywhere. This is a genuinely
// distinct, terminal state (server/app.py's revocation_watchdog force-closes with code 4001; the
// same underlying cause also surfaces as a 401 on /status or /zscores if an HTTP poll happens to
// land first) -- reconnecting can never fix it, only signing in again can. Idempotent: the WS close
// and an in-flight HTTP poll's 401 can both fire close together; only the first call does anything.
let sessionRevoked = false;
function handleSessionRevoked() {
  if (sessionRevoked) return;
  sessionRevoked = true;
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  connGeneration++; // any in-flight/stale socket's further events are now ignored
  stopStatusPolling();
  stopZScorePolling();
  setConnState("signedOut - session ended elsewhere");
  setStatus("signed out -- this session was ended (signed in again elsewhere, or by an admin)", true);
  const link = document.getElementById("signInAgainLink");
  if (link) link.style.display = "inline";
}

// W2.8 Part A: "Any user pan/zoom disengages follow-live until Reset View is pressed; show that
// state in the header" -- matches the desktop's own _on_user_interaction()/_reset_view()
// precedence (RENDER_SPEC.md section 9): View.userSet IS this browser's user_view_override, so
// this is a direct readout of that flag, not a separate tracked state that could drift from it.
function updateFollowIndicator() {
  const el = document.getElementById("followIndicator");
  if (!el) return;
  el.textContent = View.userSet ? "manual view" : "following live";
  el.className = View.userSet ? "manual" : "following";
}

function closeExistingSocket() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  if (!ws) return;
  const old = ws;
  ws = null;
  old._intentional = true;
  try { old.close(); } catch (e) {}
}

// Connect button AND session-switch both call this -- BOOK_SPEC.md-style "one code path": close
// whatever socket currently exists (suppressed via the per-socket _intentional flag/generation
// bump above, never via a racy shared variable), then open exactly one new one.
function connect(token, session) {
  closeExistingSocket();
  reconnectAttempts = 0;
  lastConnectArgs = { token, session };
  connGeneration++;
  openSocket(token, session, connGeneration);
}

function openSocket(token, session, gen) {
  setConnState("connecting");
  setStatus("connecting...");
  const sock = new WebSocket(wsUrl(token, session));
  ws = sock;
  startStatusPolling(token);
  startZScorePolling(token);

  sock.onopen = () => {
    if (gen !== connGeneration) return; // superseded before it even finished connecting
    setConnState("connected");
    setStatus("connected");
    reconnectAttempts = 0;
  };
  sock.onclose = (ev) => {
    if (ev.code === 4001) {
      handleSessionRevoked();
      return;
    }
    if (gen !== connGeneration || sock._intentional) {
      window.__wsSuppressedCloseCount++;
      if (gen === connGeneration) setConnState("disconnected");
      return;
    }
    // Unexpected drop (server restart, network blip, evicted by the per-token cap, etc.) --
    // auto-reconnect with exponential backoff + jitter + a max attempt cap. "Zero unrecovered
    // drops" means every drop must eventually reconnect (within the cap), not that drops never
    // happen; a real soak over a real network/process can see transient blips.
    setStatus(`disconnected (code ${ev.code})`, true);
    window.__wsDropCount++;
    if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      setConnState("disconnected (gave up)");
      return;
    }
    reconnectAttempts++;
    setConnState(`reconnecting (attempt ${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})`);
    const backoff = Math.min(BACKOFF_BASE_MS * (2 ** (reconnectAttempts - 1)), BACKOFF_MAX_MS);
    const jittered = backoff * (0.5 + Math.random() * 0.5); // 50-100% of the nominal backoff
    reconnectTimer = setTimeout(() => {
      if (gen !== connGeneration) return; // a newer connect() superseded us during the wait
      window.__wsReconnectCount++;
      openSocket(lastConnectArgs.token, currentSessionName || lastConnectArgs.session, gen);
    }, jittered);
  };
  sock.onerror = () => { if (gen === connGeneration) setStatus("error", true); };
  sock.onmessage = (ev) => {
    if (gen !== connGeneration) return; // stale/superseded socket -- ignore its messages entirely
    handleMessage(JSON.parse(ev.data));
  };
}

function handleMessage(msg) {
  lastMessageWallMs = Date.now(); // stream-liveness clock -- see the note on isBookStale()
  // W2.8 Part 0: gap detection must see EVERY message that carries a seq -- including keepalives
  // (W2.7 Part B). ClientQueue.push() (server/live_bridge.py) tags ALL pushed items with a
  // strictly-incrementing seq, keepalive or not; this check used to live only inside the "delta"
  // branch below, so a keepalive landing between two real deltas silently consumed a seq value
  // the client never accounted for -- the very next delta then looked exactly like a dropped
  // message, triggering a spurious resync (a fresh snapshot request). Found directly: this fired
  // on a fixed ~KEEPALIVE_INTERVAL_S=5s cycle, forever -- window.__wsGapCount incremented by
  // exactly 1 every 5s in a live capture with zero user interaction and zero real drops. Each one
  // replaced Data.frame AND (before Part A's fix) the user's current pan/zoom, since every
  // snapshot unconditionally reset the view.
  if (typeof msg.seq === "number") {
    if (lastSeq !== null && msg.seq !== lastSeq + 1) {
      window.__wsGapCount = (window.__wsGapCount || 0) + 1;
      lastSeq = msg.seq;
      sendControl({ type: "resync" });
      return; // don't apply a delta on top of state we know is now incomplete; the fresh
              // snapshot the resync request triggers will reset everything cleanly.
    }
    lastSeq = msg.seq;
  }
  if (msg.type === "keepalive") {
    // No state to update -- lastMessageWallMs above is the whole point of this message existing
    // (see live_bridge.KEEPALIVE_INTERVAL_S and checkLiveFeedFreshness() below).
    return;
  }
  if (msg.type === "hello") {
    currentSessionName = msg.session;
    populateSessionSelect(msg.available_sessions, msg.session);
    updateModeBadge();
    return;
  }
  if (msg.type === "replay_ended") {
    Data.replayEnded = true;
    updateModeBadge();
    markDirty();
    return;
  }
  if (msg.type === "snapshot") {
    resetData(); // also clears Data.replayEnded -- a fresh snapshot means the loop restarted
    updateModeBadge();
    Data.symbol = msg.symbol; Data.date = msg.date; Data.depth = msg.depth;
    Data.lastClosedBarIdx = msg.last_closed_bar_idx;
    if (typeof msg.n_sessions === "number") currentNSessions = msg.n_sessions;
    Data.sessionBoundaries = msg.session_boundaries || [];
    Data.bars = msg.bars.bar_index.map((bi, i) => ({
      bar_index: bi, bar_pos: msg.bars.bar_pos[i], px_close: msg.bars.px_close[i],
      bar_start_ts_ns: msg.bars.bar_start_ts_ns[i],
    }));
    const cells = cellsFromWire(msg.cells);
    for (const bar of Data.bars) {
      // cells for this bar are a contiguous slice tagged with bar_idx -- filter once here since
      // the snapshot only arrives once per session start.
    }
    groupCellsByBar(cells, msg.cells.bar_idx, Data.bars);
    setFrame(msg.version, {
      lastPrice: msg.last_price, formingBar: msg.forming_bar || null,
      formingCells: msg.forming_cells ? cellsFromWire(msg.forming_cells) : [],
      book: bookFromWire(msg.book),
      bookTs: msg.book_ts, bookStatus: msg.book_status || "ok", bookStatusReason: msg.book_status_reason || null,
    });
    // W2.8 Part A: the view transform belongs to the USER, not the data. A snapshot arrives on
    // initial connect AND on every reconnect/resync -- initializing the view (the desktop's own
    // default-fit-then-follow behavior, RENDER_SPEC.md section 9's _update_view_ranges()) is only
    // correct the FIRST time, when no view state exists yet. Any later snapshot (reconnect,
    // resync) must leave View untouched -- previously this unconditionally wiped whatever the
    // user was looking at on EVERY snapshot, which (combined with the keepalive/seq bug just
    // fixed above) was resetting the view roughly every 5 seconds even with zero user interaction.
    if (pendingLookbackFit || !viewInitialized) {
      View.userSet = false;
      fitViewToAllBars();
      updateFollowIndicator();
      pendingLookbackFit = false;
    }
    markDirty();
    // W3 Part D gate (e) finding: a fresh LIVE snapshot's own bars list (server/live_bridge.py's
    // _trim_bars, the last N sorted by bar_index) should always be internally contiguous by
    // construction -- unlike a live "seal" stream (where a trimmed-window boundary is normal), an
    // internal gap WITHIN one snapshot means the read-only service's own sealed_bars metadata
    // hadn't caught up with a just-sealed bar yet at the exact instant this snapshot was built
    // (the same class of forming/compact handoff lag book_flow_data_service.py's own
    // _bridge_gap_if_needed documents for cells -- this is the sealed_bars/metadata side of that
    // same real, transient, self-healing lag, not a webbeta-introduced bug). A live session only:
    // replay's own pre-recorded snapshots are already-validated static data, never subject to it.
    if (currentSessionName === LIVE_SESSION_NAME) {
      let gapAt = -1;
      for (let i = 1; i < Data.bars.length; i++) {
        if (Data.bars[i].bar_index !== Data.bars[i - 1].bar_index + 1) { gapAt = i; break; }
      }
      if (gapAt >= 0) {
        window.__snapshotGapResyncCount = (window.__snapshotGapResyncCount || 0) + 1;
        if (window.__snapshotGapResyncCount <= 5) {
          console.warn(`[webbeta] snapshot had an internal bar_idx gap at position ${gapAt} `
            + `(${Data.bars[gapAt - 1].bar_index} -> ${Data.bars[gapAt].bar_index}) -- requesting `
            + `another snapshot (attempt ${window.__snapshotGapResyncCount})`);
          sendControl({ type: "resync" });
        } else {
          console.error(`[webbeta] snapshot bar_idx gap persisted through 5 resync attempts -- `
            + `giving up to avoid a resync storm; report this`);
        }
      } else {
        window.__snapshotGapResyncCount = 0;
      }
    }
    return;
  }
  // W3: replay sessions (server/replay.py) replay PRE-RECORDED "delta"/{bar_roll,forming_update,
  // book_update} messages captured under the old wire protocol -- those static files are out of
  // scope for this mission and are not being regenerated, so this branch stays exactly as it was
  // for replay compatibility. LIVE mode never sends "delta" messages anymore (see the new
  // seal/update/book_update handling below); this is reachable only from a replay session.
  if (msg.type === "delta") {
    if (msg.kind === "bar_roll") {
      const bar = msg.sealed_bar;
      Data.bars.push(bar);
      trimRetainedBars();
      Data.cellsByPos.set(bar.bar_pos, cellsFromWire(msg.sealed_cells));
      Data.lastClosedBarIdx = msg.last_closed_bar_idx;
      const lp = (msg.last_price != null) ? msg.last_price : Data.frame.lastPrice;
      setFrame(msg.version, {
        lastPrice: lp, formingBar: msg.forming_bar || null,
        formingCells: msg.forming_cells ? cellsFromWire(msg.forming_cells) : [],
      });
    } else if (msg.kind === "forming_update") {
      setFrame(msg.version, {
        lastPrice: msg.last_price, formingBar: msg.forming_bar || null,
        formingCells: msg.forming_cells ? cellsFromWire(msg.forming_cells) : [],
      });
    } else if (msg.kind === "book_update") {
      setFrame(msg.version, {
        book: bookFromWire(msg.book),
        bookTs: msg.book_ts, bookStatus: msg.book_status || "ok", bookStatusReason: msg.book_status_reason || null,
      });
    }
    autoFitView();
    markDirty();
    return;
  }
  // W3 Part B: live mode's entity-complete protocol. Every message here is a COMPLETE statement
  // about the entity it names -- a seal's sealed_cells is that bar's entire final cell set, an
  // update's forming_cells is the forming bar's entire current cell set -- never a merge/patch.
  // Applying one is always "replace whatever I have for this key," never "add to it."
  const bookPatch = (m) => (
    m.book !== undefined
      ? {
          book: bookFromWire(m.book),
          bookTs: m.book_ts, bookStatus: m.book_status || "ok", bookStatusReason: m.book_status_reason || null,
        }
      : {}
  );
  if (msg.type === "seal") {
    const bar = msg.sealed_bar;
    // W3 Part D gate (e) finding: the hub broadcasts each bar's seal exactly ONCE, hub-wide, not
    // per-client (server/live_bridge.py's _last_closed_bar_idx_broadcast) -- a client that is
    // disconnected at that exact instant, and whose reconnect snapshot lands one poll before the
    // hub's own frame catches up, gets no second chance at that one bar; nothing else ever
    // re-reports it. Caught by test_live_reconnect.py's repeated-kill gate as a real internal gap
    // (not a trimmed-window boundary, which this check is deliberately narrow enough to never
    // false-positive on -- it only ever looks at the single newest bar just before appending).
    // Detect it here, at the one point it can actually be detected cheaply, and self-heal via the
    // same resync control message ClientQueue's own seq-gap detection already uses -- the user
    // must never have to click Connect to repair this.
    const curMax = Data.bars.length ? Data.bars[Data.bars.length - 1].bar_index : null;
    // Only a bar_index strictly AHEAD of curMax+1 is a gap -- one <= curMax is a legitimate
    // duplicate/replace of an already-known bar (a batched-poll re-send), not a skip.
    if (curMax !== null && bar.bar_index > curMax + 1) {
      console.warn(`[webbeta] seal gap: newest known bar is ${curMax}, `
        + `next seal is ${bar.bar_index} -- requesting a fresh snapshot`);
      sendControl({ type: "resync" });
      return;
    }
    // Replace-by-key: bars are keyed by bar_index. Normal operation always appends (seals arrive
    // in increasing bar_index order); the lookup only matters for the rare case of the same bar
    // being reported twice (a batched-poll edge, a coalesced-then-resent seal).
    const existingIdx = Data.bars.findIndex(b => b.bar_index === bar.bar_index);
    if (existingIdx >= 0) Data.bars[existingIdx] = bar;
    else { Data.bars.push(bar); trimRetainedBars(); }
    Data.cellsByPos.set(bar.bar_pos, cellsFromWire(msg.sealed_cells));
    Data.lastClosedBarIdx = msg.last_closed_bar_idx;
    // MISSION diagnose-bar-disappears-at-seal: the real seal for this bar_pos just landed --
    // if applyFormingHold() was holding it (server had transiently reported no forming bar), drop
    // the hold now. Data.bars/cellsByPos above is the authoritative representation from here on;
    // continuing to also substitute it as "forming" would double-render the same bar_pos.
    if (heldFormingBar && heldFormingBar.bar.bar_pos === bar.bar_pos) {
      heldFormingBar = null;
      heldFormingSince = null;
    }
    const lp = (msg.last_price != null) ? msg.last_price : Data.frame.lastPrice;
    // forming_bar/forming_cells keys are OMITTED (not null) on every seal message in a batch
    // except the last one (server/live_bridge.py's _build_messages) -- "in msg" distinguishes
    // "this message says nothing about forming state, leave it alone" from a real null ("there
    // is genuinely no forming bar right now"), which msg.forming_bar || null could not tell apart.
    const framePatch = { lastPrice: lp, ...bookPatch(msg) };
    if ("forming_bar" in msg) {
      framePatch.formingBar = msg.forming_bar || null;
      framePatch.formingCells = msg.forming_cells ? cellsFromWire(msg.forming_cells) : [];
    }
    setFrame(msg.version, framePatch);
    autoFitView();
    markDirty();
    return;
  }
  if (msg.type === "update") {
    setFrame(msg.version, {
      lastPrice: msg.last_price,
      formingBar: msg.forming_bar || null,
      formingCells: msg.forming_cells ? cellsFromWire(msg.forming_cells) : [],
      ...bookPatch(msg),
    });
    autoFitView();
    markDirty();
    return;
  }
  if (msg.type === "book_update") {
    setFrame(msg.version, bookPatch(msg));
    markDirty();
    return;
  }
}

// W3 Part A: the old checkSealedContinuity/checkSnapshotContinuity/findSealedGap machinery
// (W2.12/W2.13) scanned Data.bars for internal bar_index gaps and treated any gap as corruption
// needing an automatic resync. That assumption no longer holds under the entity-complete protocol:
// a gap between the trimmed snapshot window and whatever seals arrive after it is NORMAL (bars are
// keyed by bar_index and replaced/appended independently, not required to form a contiguous run),
// so the same check would now false-positive constantly. No desktop equivalent exists either --
// removed rather than adapted, per this mission's own rule.

function groupCellsByBar(cells, barIdxArr, bars) {
  const byBarIdx = new Map();
  for (let i = 0; i < cells.length; i++) {
    const bi = barIdxArr[i];
    if (!byBarIdx.has(bi)) byBarIdx.set(bi, []);
    byBarIdx.get(bi).push(cells[i]);
  }
  for (const bar of bars) {
    Data.cellsByPos.set(bar.bar_pos, byBarIdx.get(bar.bar_index) || []);
  }
}

function sendControl(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

// ------------------------------------------------------------------------------ price<->pixel --
function makeYMapper(canvasHeight) {
  const { yMin, yMax } = View;
  const span = Math.max(yMax - yMin, 1e-6);
  return (price) => canvasHeight - ((price - yMin) / span) * canvasHeight;
}
function makeXMapper(canvasWidth) {
  const { xMin, xMax } = View;
  const span = Math.max(xMax - xMin, 1e-6);
  return (barPos) => ((barPos - xMin) / span) * canvasWidth;
}

// --------------------------------------------------------------------------- rendering: main ---
const mainCanvas = document.getElementById("mainCanvas");
const mainCtx = mainCanvas.getContext("2d");
const bookCanvas = document.getElementById("bookCanvas");
const bookCtx = bookCanvas.getContext("2d");
const zscoreCanvas = document.getElementById("zscoreCanvas");
const zscoreCtx = zscoreCanvas.getContext("2d");

function resizeCanvases() {
  for (const c of [mainCanvas, bookCanvas, zscoreCanvas]) {
    const rect = c.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    c.width = Math.max(1, Math.round(rect.width * dpr));
    c.height = Math.max(1, Math.round(rect.height * dpr));
    c.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  markDirty();
}

// RENDER_SPEC.md section 1: numpy nanpercentile's default linear-interpolation method, over
// whatever cells are currently visible (this function's caller's own analog of the desktop's
// viewport-relative window -- book_flow_chart_v3.py:413, :1991-2023).
function percentile95(values) {
  const n = values.length;
  if (!n) return 0;
  const sorted = values.slice().sort((a, b) => a - b);
  const idx = 0.95 * (n - 1);
  const lo = Math.floor(idx), hi = Math.ceil(idx);
  if (lo === hi) return sorted[lo];
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

// MISSION toolbar-light-theme (follow-up): UP_COLOR/DOWN_COLOR are light, saturated hues tuned to
// pop against near-black -- even at FULL alpha they only reached ~2.0x/3.2x contrast against white,
// so no amount of alpha-floor raising alone could get thin-flow cells to a real "OK" ratio. Fixed
// properly this time: darker green/pink/gray hues for light theme (measured directly, real WCAG
// numbers, not guessed) PLUS a higher alpha floor (100->170) -- together the floor now reads
// ~2.9-4.1x and full alpha ~5.4-8.5x, real parity with the rest of the reviewed palette instead of
// a partial mitigation.
const CELL_ALPHA_FLOOR_DARK = 45, CELL_ALPHA_FLOOR_LIGHT = 170;
function colorGroupForCell(cell, denom) {
  const norm = denom > 0 ? Math.min(Math.max(cell.abs_flow / denom, 0), 1.0) : 0.0;
  const floor = lightTheme ? CELL_ALPHA_FLOOR_LIGHT : CELL_ALPHA_FLOOR_DARK;
  const alpha = Math.round(floor + (255 - floor) * norm);
  const sign = cell.signed_flow > 0 ? 0 : (cell.signed_flow < 0 ? 1 : 2);
  const band = Math.min(Math.max(Math.floor((alpha - CELL_ALPHA_FLOOR_DARK) / 14), 0), 15);
  return { key: sign * 16 + band, sign, alpha: sign === 2 ? Math.max(35, alpha >> 1) : alpha, norm };
}

// RENDER_SPEC.md section 1/2, Part D gate (a): the exact per-cell geometry the renderer paints --
// (bar_pos, bar_idx, price_level, width_fraction, rgba) -- computed once here and used both for
// drawing (drawCellCandles/paintCellGeometry below) and as the geometry-parity test hook
// (window.__computeCellGeometry), so the two can never drift apart.
function computeVisibleCellGeometry() {
  const loBar = Math.floor(View.xMin) - 1, hiBar = Math.ceil(View.xMax) + 1;

  // Gather visible cells (sealed + forming) -- same pool the desktop's set_data() receives for
  // its own viewport (book_flow_chart_v3.py:1991-2023): the 95th-percentile denominator below is
  // computed over exactly this set, not a running/session-wide max. No bar-count/zoom-dependent
  // branch here at all -- RENDER_SPEC.md section 8: this browser always uses the true per-cell
  // encoding, at every zoom, unlike the desktop's own WIDE/OVERVIEW LOD.
  const visible = [];
  for (const bar of Data.bars) {
    if (bar.bar_pos < loBar || bar.bar_pos > hiBar) continue;
    const cells = Data.cellsByPos.get(bar.bar_pos) || [];
    for (const c of cells) visible.push({ barPos: bar.bar_pos, barIdx: bar.bar_index, isForming: false, ...c });
  }
  const { formingBar, formingCells } = Data.frame;
  if (formingBar && formingBar.bar_pos >= loBar && formingBar.bar_pos <= hiBar) {
    for (const c of formingCells) {
      visible.push({ barPos: formingBar.bar_pos, barIdx: formingBar.bar_index, isForming: true, ...c });
    }
  }
  if (!visible.length) return [];

  // RENDER_SPEC.md section 1 / book_flow_chart_v3.py:413: denom = nanpercentile(abs_flow, 95).
  const denom = percentile95(visible.map((c) => c.abs_flow));

  const out = [];
  for (const c of visible) {
    const g = colorGroupForCell(c, denom);
    const rgb = g.sign === 0 ? themeUpRgb() : (g.sign === 1 ? themeDownRgb() : themeCellGrayRgb());
    out.push({
      barPos: c.barPos, bar_idx: c.barIdx, price_level: c.price_level, isForming: c.isForming,
      width_fraction: 0.14 + 0.80 * g.norm, rgba: [rgb[0], rgb[1], rgb[2], g.alpha],
    });
  }
  return out;
}
window.__computeCellGeometry = computeVisibleCellGeometry;
window.__priceAgeMs = () => Date.now() - lastPriceChangeWallMs; // test hook, W2.8 Part B/C

function paintCellGeometry(ctx, cssW, cssH, geometry) {
  if (!geometry.length) return;
  const toX = makeXMapper(cssW), toY = makeYMapper(cssH);
  // Batch by color so fillStyle changes are minimized -- the same batching principle
  // book_flow_chart_v3.py's BookFlowLevelCellItem uses via QPainter groups (its own grouping
  // additionally quantizes alpha into 16 bands for painting, a QPainter-call-count optimization
  // this browser path does not replicate -- see RENDER_SPEC.md section 2 addendum).
  const groups = new Map();
  for (const g of geometry) {
    const key = g.rgba.join(",");
    if (!groups.has(key)) groups.set(key, { rgba: g.rgba, items: [] });
    groups.get(key).items.push(g);
  }
  const tickPx = Math.abs(toY(0) - toY(TICK));
  const cellH = Math.max(tickPx * 0.92, 1);
  const barPx = cssW / Math.max(View.xMax - View.xMin, 1);
  for (const { rgba, items } of groups.values()) {
    ctx.fillStyle = `rgba(${rgba[0]},${rgba[1]},${rgba[2]},${(rgba[3] / 255).toFixed(3)})`;
    for (const g of items) {
      const w = Math.max(g.width_fraction * barPx, 1);
      const x = toX(g.barPos) - w / 2;
      const y = toY(g.price_level) - cellH / 2;
      ctx.fillRect(x, y, w, cellH);
    }
  }
}

// W2.2 Part B: bitmap cache for SEALED-bar cells only. The forming bar is never cached (it changes
// every forming_update tick, ~350ms cadence at real production rates) -- it is always painted live,
// every frame, on top of whatever the cache (or the direct path, if caching is disabled) produced.
// Invalidated whenever the view transform, canvas size, or the sealed-bar set (lastClosedBarIdx)
// changes -- i.e. on any zoom/pan or bar-roll. This changes ONLY how pixels get drawn, never what
// computeVisibleCellGeometry() computes, so gate (a) can assert the picture is identical with
// caching on or off.
let cachingEnabled = new URLSearchParams(location.search).get("nocache") !== "1";
window.__setCachingEnabled = (v) => { cachingEnabled = !!v; sealedCacheKey = null; markDirty(); };
window.__isCachingEnabled = () => cachingEnabled;

const sealedCache = document.createElement("canvas");
const sealedCacheCtx = sealedCache.getContext("2d");
let sealedCacheKey = null;

function sealedCacheKeyFor(cssW, cssH) {
  return [View.xMin, View.xMax, View.yMin, View.yMax, cssW, cssH, Data.lastClosedBarIdx].join(",");
}

function drawCellCandles(ctx, cssW, cssH) {
  const geometry = computeVisibleCellGeometry();
  const sealed = geometry.filter((g) => !g.isForming);
  const forming = geometry.filter((g) => g.isForming);

  if (!cachingEnabled) {
    paintCellGeometry(ctx, cssW, cssH, sealed);
  } else {
    const key = sealedCacheKeyFor(cssW, cssH);
    if (key !== sealedCacheKey) {
      sealedCache.width = mainCanvas.width;
      sealedCache.height = mainCanvas.height;
      const dpr = window.devicePixelRatio || 1;
      sealedCacheCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
      sealedCacheCtx.clearRect(0, 0, cssW, cssH);
      paintCellGeometry(sealedCacheCtx, cssW, cssH, sealed);
      sealedCacheKey = key;
    }
    // Blit at native device-pixel resolution -- avoid double-applying the ctx's own dpr transform.
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.drawImage(sealedCache, 0, 0);
    ctx.restore();
  }
  paintCellGeometry(ctx, cssW, cssH, forming);
}

// RENDER_SPEC.md section 4a/4c: the desktop's price_curve (a polyline connecting every visible
// bar's close price, toggle-controlled, book_flow_chart_v3.py:1320-1321/2344-2350) is deliberately
// NOT replicated here -- a bar-to-bar connecting line is explicitly out of scope for this
// renderer.
//
// MISSION market-line-only: the static "last trade" price line (drawCurrentPriceLine, moved only
// on an actual trade print) was removed -- the book-anchored market line below is now the sole
// price line on both canvases; the order book's own price+age chip (renderBook) still shows the
// exact last-trade number and still uses PRICE_COLOR, unaffected by this removal.

// W4.1 Part 2: a continuously-updating price indicator, sourced from the book (best bid/ask
// midpoint) -- moves on every book refresh regardless of whether a trade has printed, answering
// "where is the market right now". Confirmed via direct source grep (rithmic_live_features.py):
// apply_book_obs() (quote/book updates) never touches last_price/close_price at all, so this
// needed its OWN signal, not a derivation of last-trade price. Now the only price LINE on either
// canvas (see MISSION market-line-only above) -- the order book's own price+age chip still shows
// the exact last-trade number separately.
const MARKET_COLOR = "#ffa94d";
// MISSION market-line-direction: green/red instantly on the tick that actually moved book.mid
// (marketDirection, set in setFrame() the moment it changes -- never recomputed per render frame,
// so it can't flicker between draws of the same unchanged frame). MARKET_COLOR (amber) is only the
// fallback before any direction has been observed yet (a fresh connect/reconnect, see resetData()).
function marketLineColor() {
  if (marketDirection === "up") return tc(UP_COLOR);
  if (marketDirection === "down") return tc(DOWN_COLOR);
  return tc(MARKET_COLOR);
}
function drawMarketLine(ctx, cssW, cssH, extendFullWidth, f = Data.frame) {
  const mid = f.book ? f.book.mid : null;
  if (mid == null) return;
  const toY = makeYMapper(cssH);
  const y = toY(mid);
  const color = marketLineColor();
  ctx.save();
  ctx.strokeStyle = color;
  ctx.setLineDash([1, 3]);
  ctx.lineWidth = 1.6;
  ctx.beginPath();
  ctx.moveTo(0, y);
  ctx.lineTo(cssW, y);
  ctx.stroke();
  if (extendFullWidth) {
    ctx.fillStyle = color;
    ctx.font = "10px sans-serif";
    ctx.textAlign = "right";
    ctx.fillText("market", cssW - 4, y - 3);
    ctx.textAlign = "left";
  }
  ctx.restore();
}

function drawCellHighlight(ctx, cssW, cssH, f = Data.frame) {
  // W4.1 Part 6: main-chart-only, like the market line below it in renderMain -- gated on
  // View.userSet at the CALL SITE (renderMain), not here, so the same guard logic lives in one
  // place. See renderMain's own comment for why.
  if (f.lastPrice == null || !f.formingBar) return;
  const toX = makeXMapper(cssW), toY = makeYMapper(cssH);
  const cellPrice = Math.round(f.lastPrice / TICK) * TICK;
  const tickPx = Math.abs(toY(0) - toY(TICK));
  const cellH = Math.max(tickPx * 0.92, 2);
  const barPx = cssW / Math.max(View.xMax - View.xMin, 1);
  const w = Math.max(barPx * 0.94, 2);
  const x = toX(f.formingBar.bar_pos) - w / 2;
  const y = toY(cellPrice) - cellH / 2;
  ctx.save();
  ctx.fillStyle = tc("rgba(255,255,255,0.24)");
  ctx.strokeStyle = tc("#ffffff");
  ctx.lineWidth = 1;
  ctx.fillRect(x, y, w, cellH);
  ctx.strokeRect(x, y, w, cellH);
  ctx.restore();
}

// MISSION tester-feedback issue 2: crosshair + price/time readout on hover. Deliberately a distinct
// neutral gray from drawCellHighlight's white forming-cell box above, so the two overlays are never
// visually confused with each other. Price/time conversion reuses the exact same helpers the y-axis
// and x-axis labels already use (makeYMapper, tsNsForBarPos/formatUtcHM) -- guaranteed to read the
// same value the axis itself shows, not a second, potentially-drifting implementation.
function drawCrosshair(ctx, cssW, cssH) {
  if (!hoverPos) return;
  const { px, py } = hoverPos;
  const plotRight = cssW - AXIS_STRIP_Y_PX, plotBottom = cssH - AXIS_STRIP_X_PX;
  if (px < 0 || px > plotRight || py < 0 || py > plotBottom) return;
  const toY = makeYMapper(cssH);
  const price = View.yMax - (py / cssH) * (View.yMax - View.yMin);
  const tsNs = tsNsForBarPos(View.xMin + (px / cssW) * (View.xMax - View.xMin));

  ctx.save();
  ctx.strokeStyle = tc("rgba(180,180,180,0.55)");
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]);
  ctx.beginPath(); ctx.moveTo(px, 0); ctx.lineTo(px, plotBottom); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(0, py); ctx.lineTo(plotRight, py); ctx.stroke();
  ctx.setLineDash([]);

  ctx.font = "11px 'Segoe UI', sans-serif";
  const priceTxt = price.toFixed(2);
  const priceTw = ctx.measureText(priceTxt).width;
  ctx.fillStyle = "#333";
  ctx.fillRect(plotRight, py - 8, AXIS_STRIP_Y_PX, 16);
  ctx.fillStyle = "#eee";
  ctx.fillText(priceTxt, plotRight + (AXIS_STRIP_Y_PX - priceTw) / 2, py + 4);

  if (tsNs != null) {
    const timeTxt = formatUtcHM(tsNs);
    const timeTw = ctx.measureText(timeTxt).width;
    ctx.fillStyle = "#333";
    ctx.fillRect(px - timeTw / 2 - 5, plotBottom, timeTw + 10, AXIS_STRIP_X_PX - 1);
    ctx.fillStyle = "#eee";
    ctx.fillText(timeTxt, px - timeTw / 2, plotBottom + 15);
  }
  ctx.restore();
}

// ------------------------------------------------------------------- bar_pos <-> timestamp -----
// MISSION lookback-sessions-and-timestamps: bar_pos is a plain sequential integer (see live_bridge
// _bar_wire's own comment -- "no separately-tracked position counter... a globally monotonic
// integer sequence"), so it carries no wall-clock meaning of its own; every bar's real timestamp
// lives in bar_start_ts_ns. Data.bars is sorted by bar_pos (== chronological order), so a plain
// binary search finds the nearest bar to a given x-axis position or session-boundary timestamp
// cheaply -- only ~8 label lookups plus a handful of boundary lookups per draw call.
function barIndexAtOrBeforePos(pos) {
  const bars = Data.bars;
  let lo = 0, hi = bars.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (bars[mid].bar_pos <= pos) { ans = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  return ans;
}
function barIndexAtOrAfterTsNs(tsNs) {
  const bars = Data.bars;
  let lo = 0, hi = bars.length - 1, ans = bars.length ? bars.length - 1 : -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (bars[mid].bar_start_ts_ns >= tsNs) { ans = mid; hi = mid - 1; } else { lo = mid + 1; }
  }
  return ans;
}
function tsNsForBarPos(pos) {
  const bars = Data.bars;
  if (!bars.length) return null;
  const i = barIndexAtOrBeforePos(pos);
  if (i < 0) return bars[0].bar_start_ts_ns;
  return bars[i].bar_start_ts_ns;
}
function formatUtcHM(tsNs) {
  const d = new Date(tsNs / 1e6);
  return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
}
const UTC_DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
function formatUtcDayDate(tsNs) {
  const d = new Date(tsNs / 1e6);
  return `${UTC_DAY_NAMES[d.getUTCDay()]} ${String(d.getUTCMonth() + 1).padStart(2, "0")}/${String(d.getUTCDate()).padStart(2, "0")}`;
}

// W2.2 Part B: thin label strips along the bottom (x-axis) and right (y-axis) edges, drawn on top
// of the chart content. These are also the interaction hit-regions for axis-only wheel/drag
// (RENDER_SPEC.md section 9) -- see hitRegion() below, which uses the same AXIS_STRIP_*_PX consts.
//
// MISSION lookback-sessions-and-timestamps: x-axis labels are now real UTC HH:MM timestamps (read
// off the nearest bar's bar_start_ts_ns) instead of a bare, context-free bar-index number -- a
// timestamp on its own is still ambiguous across a session boundary (same HH:MM, different day), so
// every session boundary actually in view (Data.sessionBoundaries, from the snapshot that fetched
// this data -- session_boundaries.py's own 22:00 UTC session-start convention) additionally draws a
// distinct vertical marker line plus a "Day MM/DD" label, unmistakably distinguishing it from the
// routine time-only labels.
function drawAxisStrips(ctx, cssW, cssH) {
  const toX = makeXMapper(cssW), toY = makeYMapper(cssH);
  ctx.save();
  ctx.font = "10px sans-serif";
  ctx.fillStyle = "rgba(8,8,8,0.72)";
  ctx.fillRect(0, cssH - AXIS_STRIP_X_PX, cssW, AXIS_STRIP_X_PX);
  ctx.fillRect(cssW - AXIS_STRIP_Y_PX, 0, AXIS_STRIP_Y_PX, cssH - AXIS_STRIP_X_PX);
  ctx.fillStyle = "#999";
  ctx.textAlign = "center";
  const barStep = Math.max(1, Math.round((View.xMax - View.xMin) / 8));
  for (let b = Math.ceil(View.xMin / barStep) * barStep; b <= View.xMax; b += barStep) {
    const tsNs = tsNsForBarPos(b);
    ctx.fillText(tsNs != null ? formatUtcHM(tsNs) : String(Math.round(b)), toX(b), cssH - 7);
  }
  // Session-boundary markers: a distinct vertical line spanning the plot body + a day/date label,
  // drawn AFTER the routine time labels so it's never occluded by one landing at nearly the same x.
  ctx.strokeStyle = "rgba(255,204,102,0.55)";
  ctx.fillStyle = "#ffcc66";
  ctx.lineWidth = 1;
  const plotBottom = cssH - AXIS_STRIP_X_PX;
  for (const b of Data.sessionBoundaries || []) {
    const i = barIndexAtOrAfterTsNs(b.start_ts_ns);
    if (i < 0) continue;
    const pos = Data.bars[i].bar_pos;
    if (pos < View.xMin || pos > View.xMax) continue;
    const x = toX(pos);
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, plotBottom);
    ctx.stroke();
    ctx.fillText(formatUtcDayDate(b.start_ts_ns), x, cssH - 7);
  }
  ctx.textAlign = "right";
  const priceStep = Math.max(TICK, niceStep((View.yMax - View.yMin) / 6));
  for (let p = Math.ceil(View.yMin / priceStep) * priceStep; p <= View.yMax; p += priceStep) {
    const y = toY(p);
    if (y < 10 || y > cssH - AXIS_STRIP_X_PX - 4) continue;
    ctx.fillText(p.toFixed(2), cssW - 6, y + 3);
  }
  ctx.restore();
}

function niceStep(raw) {
  if (raw <= 0 || !isFinite(raw)) return TICK;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = norm < 1.5 ? 1 : (norm < 3.5 ? 2.5 : (norm < 7.5 ? 5 : 10));
  return step * mag;
}

// BOOK_SPEC.md v2 "merged-with-chart": faint horizontal gridlines at the same "nice" tick positions
// drawAxisStrips already computes for its y-axis labels, at GRIDLINE_ALPHA=0.12 (cited exactly from
// book_flow_chart_v3.py:1288's main_plot.showGrid(alpha=0.12)). Drawn on BOTH canvases at the same
// shared-y-axis pixel positions (same View.yMin/yMax, same toY) -- a line at price P looks like one
// continuous line crossing straight from the main chart through the 1px separator into the book
// panel, because it IS the same y-mapping evaluated twice, not two independently-styled elements.
function drawPriceGridlines(ctx, cssW, cssH) {
  const toY = makeYMapper(cssH);
  const step = Math.max(TICK, niceStep((View.yMax - View.yMin) / 6));
  ctx.save();
  ctx.strokeStyle = tc(`rgba(255,255,255,${GRIDLINE_ALPHA})`);
  ctx.lineWidth = 1;
  for (let p = Math.ceil(View.yMin / step) * step; p <= View.yMax; p += step) {
    const y = Math.round(toY(p)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(cssW, y);
    ctx.stroke();
  }
  ctx.restore();
}

function renderMain() {
  const cssW = mainCanvas.clientWidth, cssH = mainCanvas.clientHeight;
  mainCtx.clearRect(0, 0, cssW, cssH);
  mainCtx.fillStyle = tc("#050505");
  mainCtx.fillRect(0, 0, cssW, cssH);

  const curFrame = Data.frame; // sampled once -- see the note on drawMarketLine/drawCellHighlight
  drawPriceGridlines(mainCtx, cssW, cssH);
  drawCellCandles(mainCtx, cssW, cssH);
  // W4.1 Part 8 (reverses Part 6): the market line is not suppressed in manual view -- it renders
  // continuously in every view state, same as the book panel's own copy always has. Only the cell
  // highlight (x-positioned, so it really does go visually stray once scrolled off the live edge)
  // stays gated on View.userSet.
  if (!View.userSet) {
    drawCellHighlight(mainCtx, cssW, cssH, curFrame);
  }
  drawMarketLine(mainCtx, cssW, cssH, true, curFrame);
  drawAxisStrips(mainCtx, cssW, cssH);
  drawUserDrawings(mainCtx, cssW, cssH);
  drawCrosshair(mainCtx, cssW, cssH);

  // Debug overlay (symbol/date/bar_idx/version): hidden by default, shown only via ?debug=1.
  if (DEBUG_OVERLAY) {
    // W2.13 Part 0: build_sha shown alongside everything else the overlay already surfaces, and
    // flagged distinctly if it's ever stale relative to the server's own live-polled value (see
    // pollStatus's own mismatch check) -- "is it running?" answered on screen, not by guessing.
    const serverSha = lastStatusPoll ? lastStatusPoll.build_sha : null;
    const shaText = serverSha && serverSha !== CLIENT_BUILD_SHA
      ? `client_sha=${CLIENT_BUILD_SHA} SERVER_SHA_MISMATCH=${serverSha}`
      : `sha=${CLIENT_BUILD_SHA}`;
    document.getElementById("banner").textContent =
      `${Data.symbol || ""} ${Data.date || ""} depth=${Data.depth || ""} last_closed_bar_idx=${Data.lastClosedBarIdx ?? ""} v${curFrame.version} ${shaText}`;
  }
}

// --------------------------------------------------------------------------- rendering: book ---
function isBookStale() {
  if (currentSessionName !== LIVE_SESSION_NAME) {
    // W2.5 Part B: replay's book_ts (when present at all) is the RECORDED session's own
    // historical timestamp -- comparing it to Date.now() would read as permanently stale
    // regardless of how healthy the stream actually is. Key off stream liveness instead: are
    // messages still arriving on the replay clock. REPLAY ENDED gets its own distinct banner
    // (see renderBook()), so it is deliberately not reported as stale here.
    if (Data.replayEnded) return false;
    return (Date.now() - lastMessageWallMs) > REPLAY_STALL_MS;
  }
  // W2.6 Part A: a book flagged "mismatch" (server-side identity/distance guard,
  // LIVEFIX_DIAGNOSIS.md) is grayed out exactly like a stale one -- renderBook() draws it with a
  // distinct amber "BOOK MISMATCH" banner instead of the red "STALE" one, but the ladder/inset
  // gray-out treatment is the same either way: never render a book that doesn't belong here.
  if (Data.frame.bookStatus === "mismatch") return true;
  if (!Data.frame.book || Data.frame.bookTs == null) return true;
  return (Date.now() / 1000 - Data.frame.bookTs) > BOOK_STALE_SECS;
}

// BOOK_SPEC.md Part A/B: order-book panel v3. Recon (RENDER_SPEC.md section 10, unchanged): the v3
// level-state checkpoint carries ONLY bid_sizes/ask_sizes per tick -- no order-count field anywhere
// in the pipeline. Every label below is SIZE ONLY -- never a fabricated count.
//
// v3 replaces BOTH of W2.2's render paths (drawLevel's price-true-but-no-minimum-height "legacy
// thin-line", and drawFixedRow's always-legible-but-not-price-aligned "near-touch zone") with
// exactly ONE ladder code path (drawLadder, price-true everywhere, row height follows price pitch
// capped at LADDER_MAX_ROW_PX) plus a separate DOM inset for guaranteed legibility -- the two
// concerns (price truth, always-on legibility near the touch) no longer share one canvas layer.
const LADDER_MAX_ROW_PX = 20;          // row height cap -- height follows price pitch below this
const LADDER_GAP_THRESHOLD_PX = 3;     // BOOK_SPEC.md v2: pitch below this -> contiguous rows
const LADDER_MIN_STUB_PX = 6;          // BOOK_SPEC.md v2: every nonzero level is a clean, visible mark
const LADDER_MAX_BAR_FRAC = 0.82;      // sub-linear width scale still gets a hard cap
const LADDER_LABEL_MIN_PITCH_PX = 12;  // BOOK_SPEC.md v2: bumped from v3's 11 to match spec exactly
const LADDER_ALPHA_MIN = 0.55;         // BOOK_SPEC.md v2: brightness floor (0th percentile)
const LADDER_ALPHA_MAX = 1.00;         // BOOK_SPEC.md v2: brightness ceiling (100th percentile)
const GRIDLINE_ALPHA = 0.12;           // cited exactly: book_flow_chart_v3.py:1288 main_plot.showGrid
const DEPTH_SILHOUETTE_ALPHA = 0.07;   // BOOK_SPEC.md v2: faint cumulative-depth shape behind bars

// BOOK_SPEC.md v2: height follows price pitch, minus a 1px gap once the pitch is wide enough to
// show one -- below LADDER_GAP_THRESHOLD_PX, rows go contiguous (floored at 1px) rather than
// showing an increasingly-meaningless "gap". Both branches are the SAME function of tickPx, not a
// different drawing routine -- they agree (to within 1px) at the threshold itself.
function ladderRowHeight(tickPx) {
  if (tickPx >= LADDER_GAP_THRESHOLD_PX) return Math.min(tickPx - 1, LADDER_MAX_ROW_PX);
  return Math.max(tickPx, 1);
}

function ladderWidth(size, maxVisibleSize, cssW) {
  // Sub-linear (sqrt) width scale, shared by every ladder level, computed once per frame over
  // VISIBLE levels only (see ladderMaxVisibleSize) -- BOOK_SPEC.md Part A's correction to W2.2's
  // scope (that code's shared maxSize spanned the full +-400-tick loaded array, not the viewport).
  // BOOK_SPEC.md v2: a real minimum stub (not just a 1px floor) so a lone small order is still a
  // clean, visible mark.
  if (maxVisibleSize <= 0 || size <= 0) return 0;
  return Math.max(
    Math.min(Math.sqrt(size) / Math.sqrt(maxVisibleSize), 1.0) * cssW * LADDER_MAX_BAR_FRAC,
    LADDER_MIN_STUB_PX,
  );
}

// BOOK_SPEC.md v2: percentile rank of `size` among this frame's visible sizes, mapped to
// [LADDER_ALPHA_MIN, LADDER_ALPHA_MAX] -- so magnitude still reads via brightness once several
// levels are bunched at (or near) the width cap. `sortedSizes` is sorted once per frame by the
// caller, not per level (O(n log n) once, O(log n) per lookup via binary search).
function percentileAlpha(size, sortedSizes) {
  if (!sortedSizes.length) return LADDER_ALPHA_MAX;
  let lo = 0, hi = sortedSizes.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (sortedSizes[mid] <= size) lo = mid + 1; else hi = mid;
  }
  const rank = lo / sortedSizes.length;
  return LADDER_ALPHA_MIN + (LADDER_ALPHA_MAX - LADDER_ALPHA_MIN) * rank;
}

function hexToRgb(hex) {
  return [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];
}
const UP_RGB = hexToRgb(UP_COLOR), DOWN_RGB = hexToRgb(DOWN_COLOR), GRAY_RGB = [102, 102, 102];
// MISSION toolbar-light-theme (follow-up): darker hue identity for light theme, shared by both the
// main-chart candle cells (computeVisibleCellGeometry) and the order-book ladder (drawLadder) so
// the two stay the same color language they already are in dark theme -- measured directly (real
// WCAG contrast against #ffffff): #007a3d/#a10f38/#4d4d4d reach 5.4x/8.0x/8.5x at full alpha,
// vs. 2.0x/3.2x/4.6x for the dark-theme hues at the SAME full alpha.
const UP_RGB_LIGHT = [0, 122, 61], DOWN_RGB_LIGHT = [161, 15, 56], GRAY_RGB_LIGHT = [77, 77, 77];
function themeUpRgb() { return lightTheme ? UP_RGB_LIGHT : UP_RGB; }
function themeDownRgb() { return lightTheme ? DOWN_RGB_LIGHT : DOWN_RGB; }
function themeGrayRgb() { return lightTheme ? GRAY_RGB_LIGHT : GRAY_RGB; }
function themeCellGrayRgb() { return lightTheme ? GRAY_RGB_LIGHT : [119, 119, 119]; }

function ladderMaxVisibleSize(book) {
  let max = 1;
  for (let i = 0; i < book.prices.length; i++) {
    const p = book.prices[i];
    if (p < View.yMin || p > View.yMax) continue;
    if (book.bidSizes[i] > max) max = book.bidSizes[i];
    if (book.askSizes[i] > max) max = book.askSizes[i];
  }
  return max;
}

function findBestBidAsk(book) {
  let bestBidIdx = -1, bestAskIdx = -1;
  for (let i = 0; i < book.prices.length; i++) if (book.bidSizes[i] > 0) bestBidIdx = i;
  for (let i = 0; i < book.prices.length; i++) { if (book.askSizes[i] > 0) { bestAskIdx = i; break; } }
  return { bestBidIdx, bestAskIdx };
}

// Part C gate (a): runtime instrumentation. Every level drawLadder paints increments this counter
// -- compared, in tests, against an independent count of non-zero visible levels to demonstrate
// (at runtime, not just by code-structure argument) that 100% of drawn levels route through this
// one path. Reset at the start of every computeLadderGeometry() call.
window.__ladderDrawCount = 0;

// Single source of truth for what the ladder draws -- computed once here, consumed both by
// drawLadder (paint) and by window.__computeLadderGeometry (Part C gates b/c/e), so they can never
// drift apart, mirroring computeVisibleCellGeometry()'s established pattern for the main chart.
function computeLadderGeometry(book, cssW, cssH) {
  window.__ladderDrawCount = 0;
  if (!book) return [];
  const toY = makeYMapper(cssH);
  const tickPx = Math.abs(toY(0) - toY(TICK));
  const rowH = ladderRowHeight(tickPx);
  const maxVisible = ladderMaxVisibleSize(book);
  const { bestBidIdx, bestAskIdx } = findBestBidAsk(book);

  // Percentile-alpha needs one sort of this frame's visible sizes -- done once here, not per level.
  const visibleSizes = [];
  for (let i = 0; i < book.prices.length; i++) {
    const price = book.prices[i];
    if (price < View.yMin - TICK || price > View.yMax + TICK) continue;
    if (book.bidSizes[i] > 0) visibleSizes.push(book.bidSizes[i]);
    if (book.askSizes[i] > 0) visibleSizes.push(book.askSizes[i]);
  }
  visibleSizes.sort((a, b) => a - b);

  const out = [];
  for (let i = 0; i < book.prices.length; i++) {
    const price = book.prices[i];
    if (price < View.yMin - TICK || price > View.yMax + TICK) continue;
    const y = toY(price);
    const bs = book.bidSizes[i], as_ = book.askSizes[i];
    if (bs > 0) {
      out.push({
        idx: i, price, side: "bid", size: bs, y, rowH, isBest: i === bestBidIdx,
        width: ladderWidth(bs, maxVisible, cssW), alpha: percentileAlpha(bs, visibleSizes),
      });
      window.__ladderDrawCount++;
    }
    if (as_ > 0) {
      out.push({
        idx: i, price, side: "ask", size: as_, y, rowH, isBest: i === bestAskIdx,
        width: ladderWidth(as_, maxVisible, cssW), alpha: percentileAlpha(as_, visibleSizes),
      });
      window.__ladderDrawCount++;
    }
  }
  return out;
}
window.__computeLadderGeometry = () => computeLadderGeometry(Data.frame.book, bookCanvas.clientWidth, bookCanvas.clientHeight);

// BOOK_SPEC.md v2: faint cumulative-depth silhouette, drawn BEHIND the per-level bars, tracing the
// running size total from the touch outward on each side. Toggleable, default on, persisted the
// same way the inset's own toggle is.
let depthSilhouetteEnabled = new URLSearchParams(location.search).get("depth") !== "0";
function setDepthSilhouetteEnabled(v) {
  depthSilhouetteEnabled = v;
  const params = new URLSearchParams(location.search);
  params.set("depth", v ? "1" : "0");
  history.replaceState(null, "", `${location.pathname}?${params.toString()}`);
  document.getElementById("depthToggleBtn").classList.toggle("active", v);
  markDirty();
}
window.__setDepthSilhouetteEnabled = setDepthSilhouetteEnabled; // test hook

function drawDepthSilhouette(ctx, cssW, cssH, book) {
  const toY = makeYMapper(cssH);
  const rowH = ladderRowHeight(Math.abs(toY(0) - toY(TICK)));
  const maxVisible = ladderMaxVisibleSize(book);
  const { bestBidIdx, bestAskIdx } = findBestBidAsk(book);
  ctx.fillStyle = tc(`rgba(255,255,255,${DEPTH_SILHOUETTE_ALPHA})`);

  let cum = 0;
  for (let i = bestBidIdx; i >= 0; i--) {
    if (book.bidSizes[i] <= 0) continue;
    cum += book.bidSizes[i];
    const price = book.prices[i];
    if (price < View.yMin - TICK || price > View.yMax + TICK) continue;
    const w = ladderWidth(cum, maxVisible * 3, cssW);
    ctx.fillRect(0, toY(price) - rowH / 2, w, rowH);
  }
  cum = 0;
  for (let i = bestAskIdx; i >= 0 && i < book.prices.length; i++) {
    if (book.askSizes[i] <= 0) continue;
    cum += book.askSizes[i];
    const price = book.prices[i];
    if (price < View.yMin - TICK || price > View.yMax + TICK) continue;
    const w = ladderWidth(cum, maxVisible * 3, cssW);
    ctx.fillRect(0, toY(price) - rowH / 2, w, rowH);
  }
}

// The ONE ladder code path -- every visible level, bid or ask, painted identically from
// computeLadderGeometry()'s list. No fixed-pitch exception, no separate near-touch treatment: that
// job belongs entirely to the DOM inset now.
function drawLadder(ctx, cssW, cssH, book, stale) {
  if (depthSilhouetteEnabled && !stale) drawDepthSilhouette(ctx, cssW, cssH, book);

  const geometry = computeLadderGeometry(book, cssW, cssH);
  const tickPx = Math.abs(makeYMapper(cssH)(0) - makeYMapper(cssH)(TICK));
  const showLabels = !stale && tickPx >= LADDER_LABEL_MIN_PITCH_PX;
  if (showLabels) {
    ctx.font = "10px ui-monospace, 'Courier New', monospace";
    ctx.textAlign = "right";
  }

  for (const g of geometry) {
    const rgb = stale ? themeGrayRgb() : (g.side === "bid" ? themeUpRgb() : themeDownRgb());
    const alpha = stale ? 1.0 : g.alpha;
    ctx.fillStyle = `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${alpha.toFixed(3)})`;
    ctx.fillRect(0, g.y - g.rowH / 2, g.width, g.rowH);
    if (g.isBest) {
      ctx.strokeStyle = tc("rgba(255,255,255,0.9)");
      ctx.lineWidth = 1;
      ctx.strokeRect(0.5, g.y - g.rowH / 2 + 0.5, g.width - 1, Math.max(g.rowH - 1, 1));
    }
    if (showLabels) {
      ctx.fillStyle = tc("#dddddd");
      ctx.fillText(String(Math.round(g.size)), cssW - 4, g.y + 3);
    }
  }
  if (showLabels) ctx.textAlign = "left";
}

// BOOK_SPEC.md Part A: hover anywhere on the ladder shows a price+size readout via a DOM tooltip
// (positioned at the cursor) -- registered once at load, reads live Data/View state each move.
function initBookHover() {
  const tooltip = document.getElementById("bookHoverTooltip");
  bookCanvas.addEventListener("mousemove", (ev) => {
    const book = Data.frame.book;
    if (!book) { tooltip.style.display = "none"; return; }
    const rect = bookCanvas.getBoundingClientRect();
    const py = ev.clientY - rect.top;
    if (py < 0 || py > rect.height) { tooltip.style.display = "none"; return; }
    const price = View.yMax - (py / rect.height) * (View.yMax - View.yMin);
    const tick = Math.round(price / TICK) * TICK;
    let idx = -1, bestDiff = Infinity;
    for (let i = 0; i < book.prices.length; i++) {
      const d = Math.abs(book.prices[i] - tick);
      if (d < bestDiff) { bestDiff = d; idx = i; }
    }
    if (idx < 0 || bestDiff > TICK * 0.5 + 1e-6) { tooltip.style.display = "none"; return; }
    const bs = book.bidSizes[idx], as_ = book.askSizes[idx];
    if (!bs && !as_) { tooltip.style.display = "none"; return; }
    const side = bs > 0 ? "bid" : "ask";
    const size = bs > 0 ? bs : as_;
    tooltip.textContent = `${book.prices[idx].toFixed(2)}  ${side} ${Math.round(size)}`;
    tooltip.dataset.price = book.prices[idx].toFixed(2);
    tooltip.dataset.size = String(Math.round(size));
    tooltip.dataset.side = side;
    tooltip.style.left = `${ev.clientX - rect.left + 10}px`;
    tooltip.style.top = `${py - 8}px`;
    tooltip.style.display = "block";
  });
  bookCanvas.addEventListener("mouseleave", () => { tooltip.style.display = "none"; });
}

function renderBook() {
  const cssW = bookCanvas.clientWidth, cssH = bookCanvas.clientHeight;
  bookCtx.clearRect(0, 0, cssW, cssH);
  bookCtx.fillStyle = tc("#050505");
  bookCtx.fillRect(0, 0, cssW, cssH);

  const toY = makeYMapper(cssH);
  const curFrame = Data.frame; // sampled once -- same frame the market line below is drawn from
  const stale = isBookStale();
  const book = curFrame.book;

  drawPriceGridlines(bookCtx, cssW, cssH);
  if (book) {
    drawLadder(bookCtx, cssW, cssH, book, stale);
  }

  drawMarketLine(bookCtx, cssW, cssH, false, curFrame);
  if (curFrame.lastPrice != null) {
    const y = toY(curFrame.lastPrice);
    const priceAgeMs = Date.now() - lastPriceChangeWallMs;
    const label = curFrame.lastPrice.toFixed(2)
      + (priceAgeMs > PRICE_AGE_SHOW_MS ? ` (${Math.round(priceAgeMs / 1000)}s)` : "");
    bookCtx.font = "bold 12px sans-serif";
    const tw = bookCtx.measureText(label).width + 10;
    bookCtx.fillStyle = tc(PRICE_COLOR);
    bookCtx.fillRect(0, y - 9, tw, 18);
    // Dark text ON TOP of the (possibly light-theme-adjusted) price chip above -- deliberately
    // NOT tc()-wrapped: this needs to stay dark against that chip's own color in BOTH themes, not
    // flip to white the way the page background does (same literal string, unrelated meaning).
    bookCtx.fillStyle = "#050505";
    bookCtx.fillText(label, 5, y + 4);
  }
  const isMismatch = curFrame.bookStatus === "mismatch";
  if (stale || Data.replayEnded) {
    // W2.5 Part B / W2.6 Part A: REPLAY ENDED and BOOK MISMATCH are distinct, non-red banners --
    // REPLAY ENDED is benign/expected (amber, matching #modeBadge.replayEnded); BOOK MISMATCH
    // means the server's sanity guard rejected a book that doesn't belong to this instrument
    // (LIVEFIX_DIAGNOSIS.md) -- also amber (a real, logged condition, but the client correctly
    // refused to render wrong data rather than silently showing it) -- never the red STALE banner,
    // which means "something the client can't fix on its own has simply gone quiet."
    bookCtx.font = "bold 12px sans-serif";
    const label = Data.replayEnded ? "REPLAY ENDED" : (isMismatch ? "BOOK MISMATCH" : "STALE");
    const amber = Data.replayEnded || isMismatch;
    const tw = bookCtx.measureText(label).width + 16;
    bookCtx.fillStyle = amber ? "rgba(255,209,102,0.9)" : "rgba(170,34,34,0.85)";
    bookCtx.fillRect(cssW / 2 - tw / 2, 8, tw, 20);
    bookCtx.fillStyle = amber ? "#332600" : "#ffffff";
    bookCtx.textAlign = "center";
    bookCtx.fillText(label, cssW / 2, 22);
    bookCtx.textAlign = "left";
    if (isMismatch && curFrame.bookStatusReason) {
      console.warn("BOOK MISMATCH:", curFrame.bookStatusReason);
    }
  }
}

// ------------------------------------------------------------------------------- render loop ---
function frame() {
  if (dirty) {
    dirty = false;
    renderMain();
    renderBook();
  }
  if (zDirty) {
    zDirty = false;
    renderZScorePanel();
  }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

// Book staleness is time-driven, not just message-driven -- tick a periodic dirty check so the
// STALE badge appears/clears even if no new message ever arrives (mirrors the desktop's
// _poll_data_service fallback fix in book_flow_chart_v3.py for the identical reason).
setInterval(markDirty, 1000);

// W2.7 Part B: distinguish "connection healthy, market just quiet" from "connection dead" in LIVE
// mode. The server sends a keepalive every KEEPALIVE_INTERVAL_S=5s regardless of market activity
// (server/live_bridge.py), so on a genuinely healthy pipe SOMETHING always arrives well within
// LIVE_FEED_WARN_MS. Silence alone must never itself trigger a reconnect below that -- this is
// purely a visible warning until LIVE_FEED_DEAD_MS, at which point the pipe is being treated as
// actually dead: we proactively close it (once per idle episode, not every tick) and let the
// EXISTING onclose handler's exponential-backoff reconnect take over -- no separate reconnect path
// duplicated here, and a single, generous bound replaces whatever produced the reported ~10s
// reconnect-loop symptom.
const LIVE_FEED_WARN_MS = 8000;
const LIVE_FEED_DEAD_MS = 20000;
let liveFeedDeadTriggered = false;

function checkLiveFeedFreshness() {
  if (currentSessionName !== LIVE_SESSION_NAME || !ws || ws.readyState !== WebSocket.OPEN) {
    liveFeedDeadTriggered = false;
    return;
  }
  const idleMs = Date.now() - lastMessageWallMs;
  if (idleMs > LIVE_FEED_DEAD_MS) {
    if (!liveFeedDeadTriggered) {
      liveFeedDeadTriggered = true;
      setConnState("reconnecting (feed frozen)");
      try { ws.close(); } catch (e) { /* onclose handles the rest either way */ }
    }
    return;
  }
  liveFeedDeadTriggered = false;
  // W2.9 Part 2/3: the server's own /status is the authoritative "is the poll loop actually
  // iterating" signal (last_poll_ts/degraded, server/live_bridge.py) -- distinct from, and an
  // earlier/more specific warning than, this client's own idle-time guess below, which can't tell
  // "poll loop stalled" apart from "market is just quiet" until LIVE_FEED_WARN_MS has passed.
  // Checked first so a confirmed server-side stall is never masked by the generic "stale" text.
  if (lastStatusPoll && lastStatusPoll.degraded) {
    const s = lastStatusPoll.seconds_since_last_poll;
    setConnState(`degraded (server poll stalled ${s != null ? Math.round(s) + "s" : "?"})`);
    return;
  }
  // W2.11 Part B: the poll loop can be perfectly healthy (degraded=false, frames_published still
  // climbing from sealed bars/price/book) while the CELL stream specifically is stuck -- a
  // narrower, more specific failure than a dead poll loop, and the exact bug this mission is
  // about. Checked before the generic "stale" text so it's never masked by it, and the mode badge
  // below also treats this as an override so a frozen chart can never show a green LIVE badge.
  if (lastStatusPoll && lastStatusPoll.cells_stalled) {
    const s = lastStatusPoll.seconds_since_cell_advance;
    setConnState(`cells stalled (${s != null ? Math.round(s) + "s" : "?"})`);
    return;
  }
  if (idleMs > LIVE_FEED_WARN_MS) {
    setConnState(`stale (no data ${Math.round(idleMs / 1000)}s)`);
  } else {
    setConnState("connected");
  }
}
setInterval(checkLiveFeedFreshness, 1000);

// MISSION lookback-sessions-and-timestamps: "the last N sessions counting back from right now"
// must keep advancing on its own, not just at the moment a snapshot happened to arrive -- e.g. if
// a viewer's browser stays open across a session boundary, the oldest retained session should drop
// off client-side without waiting for the user to touch the dropdown or reconnect. 30s cadence is
// generous relative to how often a session boundary can actually occur (~hours apart at the
// tightest, the daily break); this is not a hot path.
setInterval(() => { trimToSessionWindow(); markDirty(); }, 30000);

// ------------------------------------------------------------------------------ interaction ----
// RENDER_SPEC.md section 9: pyqtgraph ViewBox/AxisItem default dynamics, reproduced exactly --
// wheel/drag over the plot body affects both axes; over an axis strip, only that one axis.
function hitRegion(px, py, cssW, cssH) {
  if (px >= cssW - AXIS_STRIP_Y_PX && py < cssH - AXIS_STRIP_X_PX) return "y-axis";
  if (py >= cssH - AXIS_STRIP_X_PX) return "x-axis";
  return "plot";
}

function canvasPos(ev) {
  const rect = mainCanvas.getBoundingClientRect();
  return { px: ev.clientX - rect.left, py: ev.clientY - rect.top, cssW: rect.width, cssH: rect.height };
}

// MISSION tester-feedback issue 2: main chart had zero hover feedback -- confirmed directly (a
// mousemove listener existed on bookCanvas for its tooltip, but none at all on mainCanvas; the
// canvas bitmap was byte-identical before/after a hover-only mouse move). This listener ONLY
// records position and calls markDirty() -- it never calls preventDefault(), never touches
// `dragging`/`armedTool`/`pendingPoint`, so it cannot interfere with the existing mousedown (pan /
// place-drawing-point), contextmenu (drawing menu), or wheel (zoom) handlers on this same element,
// which all remain completely untouched.
let hoverPos = null; // {px, py, cssW, cssH} in mainCanvas CSS pixels, or null when not hovering
mainCanvas.addEventListener("mousemove", (ev) => {
  hoverPos = canvasPos(ev);
  markDirty();
});
mainCanvas.addEventListener("mouseleave", () => {
  hoverPos = null;
  markDirty();
});

mainCanvas.addEventListener("contextmenu", (ev) => {
  ev.preventDefault();
  // MISSION drawing-tools: right-drag already means "scale" (see the wheel/drag handlers just
  // below) -- only show the menu for a genuine click, never after a real drag, so the existing
  // zoom gesture is untouched.
  //
  // MISSION rightclick-menu-drag-fix: reads rightMouseDownPos, NOT dragButton/dragStartX -- those
  // are nulled by the window-level mouseup listener, and on SOME browsers mouseup (and this
  // handler's OWN fallback) fires before contextmenu for a right-click, which would already make
  // dragButton unusable here. 4px matches this app's existing click-vs-drag convention (also used
  // by the left-click-vs-drag paths) -- distance, not time, since this gesture only does anything
  // once the cursor actually moves.
  const downPos = rightMouseDownPos;
  rightMouseDownPos = null;
  if (downPos && Math.hypot(ev.clientX - downPos.x, ev.clientY - downPos.y) > 4) return;
  // MISSION rightclick-menu-drag-fix Part 2: real-world testing found `contextmenu` firing on
  // SOME browsers/platforms immediately at right-mousedown time, before any drag has happened --
  // the check above can't catch that (zero movement has occurred yet to measure), so the menu
  // opened immediately and then stayed open through the whole subsequent drag. Fix: never show
  // immediately -- hold the request for a short window and let a real drag (movement past the
  // same 4px threshold, checked continuously in the mousemove handler below) cancel it. A genuine
  // click still opens the menu, just ~180ms after release rather than instantly; a drag never
  // shows it at all, regardless of which event order this particular browser uses.
  if (pendingMenuShow) clearTimeout(pendingMenuShow.timer);
  // MISSION drawing-tools-delete: hit-test FIRST -- a right-click that lands on an existing
  // drawing gets a menu specific to that hit (delete this one, plus erase-all), never mixed with
  // the add-new-drawing options; empty space still gets exactly the original menu, untouched.
  const { px, py, cssW, cssH } = canvasPos(ev);
  const hit = hitTestDrawings(px, py, cssW, cssH);
  pendingMenuShow = {
    timer: setTimeout(() => {
      pendingMenuShow = null;
      showDrawingMenu(ev.clientX, ev.clientY, hit);
    }, 180),
  };
});

mainCanvas.addEventListener("wheel", (ev) => {
  ev.preventDefault();
  // W4.1 Part 7: some trackpads/OS scroll stacks fire a genuine "wheel" event with
  // deltaX===deltaY===0 (an inertial-scroll tail end, or a spurious event on initial focus/layout)
  // -- confirmed as the only code path capable of entering manual view with zero deliberate
  // interaction (three independent reports of a fresh, untouched load landing in "manual view";
  // live instrumentation of a real connect sequence showed userSet correctly starting/staying
  // False throughout snapshot/update/book_update, so the bug isn't in initialization, it's here:
  // this handler used to set View.userSet=true and even apply a small zoom for ANY wheel event,
  // including a zero-magnitude one). A no-op event must be a true no-op.
  if (ev.deltaX === 0 && ev.deltaY === 0) return;
  View.userSet = true;
  updateFollowIndicator();
  const { px, py, cssW, cssH } = canvasPos(ev);
  const region = hitRegion(px, py, cssW, cssH);
  // pyqtgraph ViewBox.wheelEvent: s = 1.02 ** (delta * wheelScaleFactor); this browser's wheel
  // delta units differ from Qt's, so the exponent base is tuned for an equivalent per-notch feel,
  // not a literal port -- the SEMANTICS (cursor-anchored, exponential, per-axis when over an axis
  // strip) are what RENDER_SPEC.md section 9 specifies and what this reproduces.
  const factor = ev.deltaY > 0 ? 1.12 : 1 / 1.12;

  if (region !== "y-axis") {
    const frac = px / cssW;
    const cursorBarPos = View.xMin + frac * (View.xMax - View.xMin);
    const newSpan = Math.max(3, Math.min(5000, (View.xMax - View.xMin) * factor));
    View.xMin = cursorBarPos - frac * newSpan;
    View.xMax = View.xMin + newSpan;
  }
  if (region !== "x-axis") {
    const fracY = py / cssH;
    const cursorPrice = View.yMax - fracY * (View.yMax - View.yMin);
    const newYSpan = Math.max(TICK * 4, (View.yMax - View.yMin) * factor);
    View.yMax = cursorPrice + fracY * newYSpan;
    View.yMin = View.yMax - newYSpan;
  }
  markDirty();
}, { passive: false });

let dragging = false, dragButton = null, dragRegion = "plot";
let dragStartX = 0, dragStartY = 0, dragView0 = null;
// MISSION rightclick-menu-drag-fix: separate from dragButton/dragging on purpose -- those are
// nulled by the window-level mouseup listener below, which on some browsers fires before
// contextmenu does for a right-click, which would make dragButton unusable by the time the
// contextmenu handler runs. This is its own state, set only on a real right-mousedown, read and
// cleared only by contextmenu itself -- no other code path touches it.
let rightMouseDownPos = null;
// MISSION rightclick-menu-drag-fix Part 2: real-world testing found that on some browsers/
// platforms, `contextmenu` fires immediately at right-mousedown time -- before any drag has had a
// chance to happen -- so rightMouseDownPos's own movement check always sees zero movement and
// can't reject it. This holds the pending "show the menu" request in that window; the mousemove
// handler below cancels it the moment real right-drag movement (past the same 4px threshold)
// is detected, so a hold-and-drag never leaves the menu open through the drag.
let pendingMenuShow = null; // { timer } or null

mainCanvas.addEventListener("mousedown", (ev) => {
  // MISSION drawing-tools: a left click while a tool is armed places a drawing point instead of
  // starting the normal pan drag -- everything else (right-drag zoom, plain left-drag pan) is
  // completely untouched when no tool is armed.
  if (ev.button === 0 && armedTool) {
    placeDrawingPoint(ev);
    return;
  }
  if (ev.button === 2) {
    rightMouseDownPos = { x: ev.clientX, y: ev.clientY };
  }
  dragging = true;
  dragButton = ev.button; // 0 = left (pan), 2 = right (scale)
  const { px, py, cssW, cssH } = canvasPos(ev);
  dragRegion = hitRegion(px, py, cssW, cssH);
  dragStartX = ev.clientX; dragStartY = ev.clientY;
  dragView0 = { ...View };
});

window.addEventListener("mousemove", (ev) => {
  if (!dragging) return;
  View.userSet = true;
  updateFollowIndicator();
  const rect = mainCanvas.getBoundingClientRect();
  const affectX = dragRegion !== "y-axis";
  const affectY = dragRegion !== "x-axis";

  if (dragButton === 2) {
    // MISSION rightclick-menu-drag-fix Part 3: found a real remaining gap -- if the button is held
    // just long enough for the Part 2 pending-show timer to actually fire (menu becomes visible,
    // correctly matching what looked like a completed click at that moment) and the SAME
    // right-button hold then continues into a drag, pendingMenuShow is already null by then, so
    // the Part 2 cancel check above never runs and the menu stayed open through the whole drag.
    // Fixed generally: any real right-drag movement past the threshold both cancels a pending show
    // AND hides an already-open menu -- a drawing menu has no business staying on screen once the
    // user is actively resizing/zooming underneath it, regardless of which instant that happened.
    if (Math.hypot(ev.clientX - dragStartX, ev.clientY - dragStartY) > 4) {
      if (pendingMenuShow) {
        clearTimeout(pendingMenuShow.timer);
        pendingMenuShow = null;
      }
      if (drawingMenu.classList.contains("visible")) hideDrawingMenu();
    }
    // Right-drag: scale (pyqtgraph ViewBox.mouseDragEvent's RightButton branch), anchored at the
    // button-down point, driven by cumulative screen-pixel delta -- "change candle length with the
    // mouse" when dragged over the x-axis strip; analogous y-axis price-scale drag over the y-axis
    // strip; both axes together over the plot body.
    const anchor = dragView0;
    const dxPx = ev.clientX - dragStartX, dyPx = ev.clientY - dragStartY;
    if (affectX) {
      const sx = Math.pow(1.006, -dxPx);
      const anchorFrac = (dragStartX - rect.left) / rect.width;
      const anchorBar = anchor.xMin + anchorFrac * (anchor.xMax - anchor.xMin);
      const span = Math.max(3, Math.min(5000, (anchor.xMax - anchor.xMin) * sx));
      View.xMin = anchorBar - anchorFrac * span;
      View.xMax = View.xMin + span;
    }
    if (affectY) {
      const sy = Math.pow(1.006, dyPx);
      const anchorFracY = (dragStartY - rect.top) / rect.height;
      const anchorPrice = anchor.yMax - anchorFracY * (anchor.yMax - anchor.yMin);
      const span = Math.max(TICK * 4, (anchor.yMax - anchor.yMin) * sy);
      View.yMax = anchorPrice + anchorFracY * span;
      View.yMin = View.yMax - span;
    }
  } else {
    // Left-drag: pan (pyqtgraph ViewBox.mouseDragEvent's Left/MiddleButton branch, PanMode).
    const dxBars = affectX ? -(ev.clientX - dragStartX) / rect.width * (dragView0.xMax - dragView0.xMin) : 0;
    const dyPrice = affectY ? (ev.clientY - dragStartY) / rect.height * (dragView0.yMax - dragView0.yMin) : 0;
    if (affectX) { View.xMin = dragView0.xMin + dxBars; View.xMax = dragView0.xMax + dxBars; }
    if (affectY) { View.yMin = dragView0.yMin + dyPrice; View.yMax = dragView0.yMax + dyPrice; }
  }
  markDirty();
});
window.addEventListener("mouseup", () => { dragging = false; dragButton = null; });

// --------------------------------------------------------------------------- drawing tools ---
// MISSION drawing-tools. drawings is a plain top-level array, deliberately never touched by
// connect()/openSocket()/reconnect -- same lifecycle as View itself, so drawings survive both
// automatic reconnects and a manual Connect click, and are lost only via Erase All or an actual
// page reload (no backend/localStorage persistence -- ephemeral by design, stated explicitly
// rather than decided silently).
let drawings = [];
let armedTool = null;       // "trend" | "horizontal" | "fib" | null
let pendingPoint = null;    // first click of a 2-point tool, awaiting the second

// Anchors are {anchorBarIndex, frac, price} -- anchorBarIndex is the real, stable bar_index (NOT
// bar_pos: confirmed by reading the wire code that the two only coincide in live mode; replay
// re-bases bar_pos to a 0..N sequence while bar_index stays the true bar number, the same
// distinction the z-score panel already had to get right). frac is the click's offset in
// bar-width units from that bar's own position, so a click between two bars keeps its precision
// instead of snapping to the nearest whole bar.
function pixelToAnchor(px, py, cssW, cssH) {
  const barPosClicked = View.xMin + (px / cssW) * (View.xMax - View.xMin);
  const price = View.yMin + (1 - py / cssH) * (View.yMax - View.yMin);
  const bars = Data.bars;
  if (!bars.length) return null;
  let nearest = bars[0], bestDist = Infinity;
  for (const b of bars) {
    const d = Math.abs(b.bar_pos - barPosClicked);
    if (d < bestDist) { bestDist = d; nearest = b; }
  }
  return { anchorBarIndex: nearest.bar_index, frac: barPosClicked - nearest.bar_pos, price };
}

// Reverse of pixelToAnchor's x-component -- null if the anchor bar isn't currently loaded (e.g.
// trimmed out, or not yet loaded at a short lookback). Callers must skip the drawing when this
// returns null rather than guess at a position -- it reappears once that bar_index is back in range.
function anchorToPixelX(anchor, cssW) {
  const bar = Data.bars.find((b) => b.bar_index === anchor.anchorBarIndex);
  if (!bar) return null;
  const barPos = bar.bar_pos + anchor.frac;
  return ((barPos - View.xMin) / Math.max(View.xMax - View.xMin, 1e-6)) * cssW;
}

function priceToPixelY(price, cssH) {
  return cssH - ((price - View.yMin) / Math.max(View.yMax - View.yMin, 1e-6)) * cssH;
}

function setDrawingModeIndicator(text) {
  const el = document.getElementById("drawingModeIndicator");
  el.textContent = text;
  el.classList.toggle("visible", !!text);
}

function disarmTool() {
  armedTool = null;
  pendingPoint = null;
  setDrawingModeIndicator("");
}

const TOOL_LABELS = { trend: "Trend Line", horizontal: "Horizontal Line", fib: "Fibonacci Retracement" };

function armTool(tool) {
  armedTool = tool;
  pendingPoint = null;
  const clicksNeeded = tool === "horizontal" ? 1 : 2;
  setDrawingModeIndicator(`Drawing: ${TOOL_LABELS[tool]} — click ${clicksNeeded} point${clicksNeeded > 1 ? "s" : ""} (Esc to cancel)`);
}

function placeDrawingPoint(ev) {
  const { px, py, cssW, cssH } = canvasPos(ev);
  if (armedTool === "horizontal") {
    const price = View.yMin + (1 - py / cssH) * (View.yMax - View.yMin);
    drawings.push({ id: Date.now() + Math.random(), type: "horizontal", price });
    disarmTool();
    markDirty();
    return;
  }
  const anchor = pixelToAnchor(px, py, cssW, cssH);
  if (!anchor) return; // no bars loaded yet -- nothing sensible to anchor to
  if (!pendingPoint) {
    pendingPoint = anchor;
    setDrawingModeIndicator(`Drawing: ${TOOL_LABELS[armedTool]} — click 1 more point (Esc to cancel)`);
    return;
  }
  drawings.push({ id: Date.now() + Math.random(), type: armedTool, p1: pendingPoint, p2: anchor });
  disarmTool();
  markDirty();
}

// --- hit-testing (MISSION drawing-tools-delete) ---
// Generic point-to-SEGMENT distance (not an infinite line) -- matches how trend lines and each
// Fibonacci level are actually drawn: bounded between their own two x-endpoints.
function distToSegment(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1;
  const lenSq = dx * dx + dy * dy;
  if (lenSq < 1e-9) return Math.hypot(px - x1, py - y1);
  let t = ((px - x1) * dx + (py - y1) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

const DRAWING_HIT_TOLERANCE_PX = 6;

// Returns Infinity if the drawing can't currently be resolved to pixels at all (e.g. an anchor
// bar_index isn't loaded right now) -- same "skip, don't guess" rule as rendering uses.
function distToDrawing(d, px, py, cssW, cssH) {
  if (d.type === "horizontal") {
    return Math.abs(py - priceToPixelY(d.price, cssH));
  }
  const x1 = anchorToPixelX(d.p1, cssW), x2 = anchorToPixelX(d.p2, cssW);
  if (x1 == null || x2 == null) return Infinity;
  const y1 = priceToPixelY(d.p1.price, cssH), y2 = priceToPixelY(d.p2.price, cssH);
  if (d.type === "trend") {
    return distToSegment(px, py, x1, y1, x2, y2);
  }
  // fib: treated as ONE object -- the whole retracement's hit distance is the closest of its own
  // 7 level segments, but a hit on any of them means "delete this Fibonacci", never one level.
  const xLo = Math.min(x1, x2), xHi = Math.max(x1, x2);
  let best = Infinity;
  for (const lvl of FIB_LEVELS) {
    const y = priceToPixelY(d.p1.price + (d.p2.price - d.p1.price) * lvl, cssH);
    best = Math.min(best, distToSegment(px, py, xLo, y, xHi, y));
  }
  return best;
}

// Closest-match: among every drawing within tolerance, the smallest actual distance wins --
// simple, predictable, no extra UI for the ambiguous case (two drawings near the same click).
function hitTestDrawings(px, py, cssW, cssH) {
  let best = null, bestDist = DRAWING_HIT_TOLERANCE_PX;
  for (const d of drawings) {
    const dist = distToDrawing(d, px, py, cssW, cssH);
    if (dist < bestDist) { bestDist = dist; best = d; }
  }
  return best;
}

// --- context menu ---
const drawingMenu = document.getElementById("drawingContextMenu");
let contextMenuTarget = null; // the hit drawing when the menu was opened, or null (empty-space menu)

function showDrawingMenu(x, y, hitDrawing) {
  contextMenuTarget = hitDrawing || null;
  if (contextMenuTarget) {
    drawingMenu.innerHTML = `
      <div class="item danger" data-action="delete-one">Delete this ${TOOL_LABELS[contextMenuTarget.type]}</div>
      <div class="sep"></div>
      <div class="item danger" data-action="erase-all">Erase All Drawings</div>`;
  } else {
    drawingMenu.innerHTML = `
      <div class="item" data-tool="trend">Trend Line</div>
      <div class="item" data-tool="horizontal">Horizontal Line</div>
      <div class="item" data-tool="fib">Fibonacci Retracement</div>
      <div class="sep"></div>
      <div class="item danger" data-action="erase-all">Erase All Drawings</div>`;
  }
  drawingMenu.style.left = `${x}px`;
  drawingMenu.style.top = `${y}px`;
  drawingMenu.classList.add("visible");
}
function hideDrawingMenu() { drawingMenu.classList.remove("visible"); }

drawingMenu.addEventListener("click", (ev) => {
  const item = ev.target.closest(".item");
  if (!item) return;
  if (item.dataset.tool) armTool(item.dataset.tool);
  if (item.dataset.action === "erase-all") { drawings = []; markDirty(); }
  if (item.dataset.action === "delete-one" && contextMenuTarget) {
    drawings = drawings.filter((d) => d.id !== contextMenuTarget.id);
    markDirty();
  }
  hideDrawingMenu();
});
document.addEventListener("mousedown", (ev) => {
  if (drawingMenu.classList.contains("visible") && !drawingMenu.contains(ev.target)) hideDrawingMenu();
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") { hideDrawingMenu(); if (armedTool) disarmTool(); markDirty(); }
});

// --- rendering (called from renderMain, same canvas/coordinate space as the candles) ---
const FIB_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];

function drawUserDrawings(ctx, cssW, cssH) {
  ctx.save();
  ctx.lineWidth = 2.0;
  ctx.font = "10px 'Segoe UI', sans-serif";
  for (const d of drawings) {
    if (d.type === "horizontal") {
      const y = priceToPixelY(d.price, cssH);
      if (y < -20 || y > cssH + 20) continue;
      ctx.strokeStyle = tc("#3388ee");
      ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(cssW, y); ctx.stroke();
      ctx.fillStyle = tc("#3388ee");
      ctx.fillText(d.price.toFixed(2), 4, y - 3);
    } else if (d.type === "trend") {
      const x1 = anchorToPixelX(d.p1, cssW), x2 = anchorToPixelX(d.p2, cssW);
      if (x1 == null || x2 == null) continue; // an anchor bar isn't currently loaded -- skip, not guess
      const y1 = priceToPixelY(d.p1.price, cssH), y2 = priceToPixelY(d.p2.price, cssH);
      ctx.strokeStyle = tc("#ffd166");
      ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
      ctx.fillStyle = tc("#ffd166");
      ctx.beginPath(); ctx.arc(x1, y1, 3, 0, 2 * Math.PI); ctx.fill();
      ctx.beginPath(); ctx.arc(x2, y2, 3, 0, 2 * Math.PI); ctx.fill();
    } else if (d.type === "fib") {
      const x1 = anchorToPixelX(d.p1, cssW), x2 = anchorToPixelX(d.p2, cssW);
      if (x1 == null || x2 == null) continue;
      const xLo = Math.min(x1, x2), xHi = Math.max(x1, x2);
      const p1 = d.p1.price, p2 = d.p2.price;
      ctx.strokeStyle = tc("#c792ea");
      for (const lvl of FIB_LEVELS) {
        const price = p1 + (p2 - p1) * lvl;
        const y = priceToPixelY(price, cssH);
        if (y < -20 || y > cssH + 20) continue;
        ctx.setLineDash(lvl === 0 || lvl === 1 ? [] : [4, 3]);
        ctx.beginPath(); ctx.moveTo(xLo, y); ctx.lineTo(xHi, y); ctx.stroke();
        ctx.fillStyle = tc("#c792ea");
        ctx.fillText(`${(lvl * 100).toFixed(1)}%  ${price.toFixed(2)}`, xHi + 4, y - 3);
      }
      ctx.setLineDash([]);
    }
  }
  ctx.restore();
}

document.getElementById("resetViewBtn").addEventListener("click", () => {
  // W2.8 Part A: "Reset View is the only thing that restores defaults" -- forces a fresh full fit
  // (not just a translate), matching the desktop's own _reset_view() (RENDER_SPEC.md section 9),
  // which clears user_view_override/_initialized and re-derives the default range from scratch.
  View.userSet = false;
  viewInitialized = false;
  autoFitView();
  updateFollowIndicator();
  markDirty();
});

// MISSION lookback-sessions-and-timestamps: #lookbackSelect now selects a SESSION count (1-5),
// requested from the server (which alone knows real session boundaries and holds the underlying
// data) via a {type:"lookback"} control message -- the same request/fresh-snapshot pattern already
// established for "resync". The view is deliberately NOT touched here: the fresh snapshot the
// server sends back is what actually updates Data.bars, and pendingLookbackFit tells the snapshot
// handler to fit the view to that new extent once it lands (handleMessage's "snapshot" branch).
document.getElementById("lookbackSelect").addEventListener("change", (ev) => {
  const n = parseInt(ev.target.value, 10);
  if (!Number.isFinite(n) || n < 1 || n > 5) return;
  currentNSessions = n;
  pendingLookbackFit = true;
  sendControl({ type: "lookback", n_sessions: n });
});

document.getElementById("depthToggleBtn").classList.toggle("active", depthSilhouetteEnabled);
document.getElementById("depthToggleBtn").addEventListener("click", () => setDepthSilhouetteEnabled(!depthSilhouetteEnabled));
document.getElementById("themeToggleBtn").classList.toggle("active", lightTheme);
document.body.classList.toggle("lightTheme", lightTheme);
document.getElementById("themeToggleBtn").addEventListener("click", () => setLightTheme(!lightTheme));
initBookHover();

window.addEventListener("resize", resizeCanvases);
resizeCanvases();

// ------------------------------------------------------------------------------------- UI ------
function populateSessionSelect(names, current) {
  const sel = document.getElementById("sessionSelect");
  sel.innerHTML = "";
  for (const n of names) {
    const opt = document.createElement("option");
    opt.value = n; opt.textContent = n;
    if (n === current) opt.selected = true;
    sel.appendChild(opt);
  }
}

// MISSION toolbar-cleanup: the token input field was removed from the toolbar (and from the DOM
// entirely) -- Connect now reconnects using whatever token the current session already
// authenticated with (lastConnectArgs, set inside connect() itself), matching the fact that a real
// user reaches this page only via a logged-in session cookie, never a manually-typed token. The
// session dropdown itself stays in the DOM (hidden, not on the toolbar) purely because
// tests/test_connection_lifecycle.py still drives it directly to exercise session-switching.
document.getElementById("connectBtn").addEventListener("click", () => {
  const token = lastConnectArgs ? lastConnectArgs.token : "";
  const sel = document.getElementById("sessionSelect");
  const session = sel.value && sel.value !== "(connect to load)" ? sel.value : "";
  View.userSet = false;
  viewInitialized = false; // a genuinely new view context, unlike a reconnect/resync snapshot
  updateFollowIndicator();
  connect(token, session);
});

document.getElementById("sessionSelect").addEventListener("change", (ev) => {
  // W2.4 Part D: session-switch now goes through the same close-then-open state machine as
  // Connect, instead of sending a {type:"session"} control message over the existing socket --
  // one code path for every "start a fresh session" action, and the same reconnect-suppression
  // guarantees apply here too.
  const token = lastConnectArgs ? lastConnectArgs.token : "";
  View.userSet = false;
  viewInitialized = false; // a genuinely new view context, unlike a reconnect/resync snapshot
  updateFollowIndicator();
  connect(token, ev.target.value);
});

function setSpeedUI(active) {
  for (const id of ["speed1x", "speed8x", "speedPause"]) {
    document.getElementById(id).classList.toggle("active", id === active);
  }
}
document.getElementById("speed1x").addEventListener("click", () => { currentSpeed = 1; sendControl({ type: "speed", value: 1 }); setSpeedUI("speed1x"); });
document.getElementById("speed8x").addEventListener("click", () => { currentSpeed = 8; sendControl({ type: "speed", value: 8 }); setSpeedUI("speed8x"); });
document.getElementById("speedPause").addEventListener("click", () => { sendControl({ type: "speed", value: 0 }); setSpeedUI("speedPause"); });
setSpeedUI("speed1x");

// MISSION webbeta-retire-token-urls: / no longer accepts a token at all, so a real login lands here
// with an EMPTY query string -- auto-connect must not depend on a ?token= being present anymore.
// The WS/status endpoints already accept a session cookie in place of a token (require_auth_ws /
// require_auth both fall back to the cookie), so connecting with an empty token still authenticates
// fine on a real, logged-in page load. ?session=<name> (used by tests to pick a specific replay
// session, cookie-authenticated, no token needed) still overrides the default; a lingering ?token=
// query param (e.g. from an old bookmark) is still honored if present, purely as a convenience --
// note it can no longer get a browser PAST /login in the first place, since that's checked before
// this script ever loads.
// MISSION toolbar-cleanup: no more #tokenInput element to mirror the value into -- the token still
// flows into connect()/lastConnectArgs exactly as before, just without a visible field.
(function autoConnectFromQuery() {
  const params = new URLSearchParams(location.search);
  const token = params.get("token") || "";
  const session = params.get("session") || LIVE_SESSION_NAME;
  connect(token, session);
})();
