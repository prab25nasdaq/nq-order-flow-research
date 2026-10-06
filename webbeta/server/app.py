#!/usr/bin/env python3
"""
webbeta W1 Part B: FastAPI replay backend.

SHADOW / RESEARCH ONLY. LOCALHOST / LAN ONLY -- binds 127.0.0.1 by default; a non-loopback host
requires the explicit --lan flag (see main()). No TLS, no public exposure, no live data: every
byte this server sends comes from webbeta/beta_data/ (written once, offline, by
export_sessions.py) -- this process never opens a production cache path.

Routes:
  GET  /login              -- unauthenticated: branded login page (background + username/password)
  POST /login              -- unauthenticated, rate-limited: checks credentials, sets session cookie
  POST /logout             -- clears the session cookie, redirects to /login
  GET  /login-assets/logo.png -- unauthenticated: the one image asset the login page needs
  GET  /landing-assets/chart-screenshot-1.png -- unauthenticated: landing page's product screenshot
  POST /request-access     -- unauthenticated, rate-limited: persists a lead to leads.jsonl, then
                               best-effort emails notify_config.json's recipient (never blocks the
                               submission on email failure -- see notify.py)
  GET  /                   -- no session cookie -> marketing landing page (200). Valid cookie -> chart.
  GET  /app.js             -- session-cookie only (require_session).
  GET  /sessions           -- authenticated: list of exported sessions (name/kind/description/duration)
  GET  /status             -- authenticated: live-hub + build status
  WS   /ws?token=&session=  -- authenticated: streams ChartFrameWire (see WIRE_SCHEMA.md)

Auth: two ways in for /sessions, /status, and /ws (require_auth/require_auth_ws) -- the original
invite-token (query param) OR a signed session cookie set by POST /login; kept for non-browser
test/verification tooling. The browser-facing chart routes / and /app.js (webbeta-retire-token-urls
mission) accept ONLY the session cookie (require_session for /app.js; / does the equivalent check
inline so it can render the marketing landing page instead of a bare 401/redirect for a human
browser without a session -- see webbeta-landing-page mission). A bare ?token= can no longer reach
the chart at all. The session cookie itself (webbeta-logout-and-browser-session mission) is
a browser-session cookie (no Max-Age/Expires -- cleared when the browser fully closes) with a signed
7-day max_age backstop enforced on every request regardless (session_auth.SESSION_MAX_AGE_S).

MISSION multi-user-credentials: credentials and sessions are now individually issued/tracked in
auth_db.py's SQLite store (server/.auth.db) instead of one shared .credentials.json + a purely
stateless signed cookie -- multiple simultaneous credentials, one active session enforced per
credential (a new login revokes that credential's prior session), and a session can be force-ended
on demand (POST /logout now genuinely revokes server-side, or via the separate local-only admin
tool, server/admin_app.py -- 127.0.0.1-only, never reachable through the tunnel). An open /ws
connection re-checks its own session's liveness every few seconds (see the revocation watchdog in
the /ws handler below) so a revoked session's live connection is force-closed, not just left to
fail on its next unrelated request. Verified (see webbeta/tests/) that
an unauthenticated WebSocket handshake is correctly rejected with an HTTP 401 for both a missing and
an incorrect token, before any upgrade/accept happens -- that gate is unchanged, just widened.
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import logging
import logging.handlers
import os
import subprocess
from collections import OrderedDict, defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, RedirectResponse
import uvicorn

from google.auth.transport import requests as google_auth_requests
from google.oauth2 import id_token as google_id_token

from . import tokens as tokens_mod
from . import credentials as credentials_mod
from . import session_auth
from . import auth_db
from . import leads as leads_mod
from . import notify as notify_mod
from . import google_signin_config as google_signin_cfg_mod
from . import llm_chat
from . import raw_research_chat
from . import raw_research_shared
from . import general_chat
from .replay import load_all_sessions, ReplayPlayer
from . import live_bridge
from . import zscores as zscores_mod

# W2.4 Part D: without this, log.info() below produces NO output at all -- found directly: nothing
# in this module or its imports ever called logging.basicConfig or attached a handler, so the
# "webbeta" logger fell through to the root logger's lastResort handler (WARNING+ only, stderr),
# silently swallowing every connect/close/evict INFO line this mission's gates depend on.
#
# W2.9 Part 0: run.sh's terminal-only output has twice now cost an investigation its traceback
# when the terminal it ran in was closed. logging.basicConfig's `handlers=` here attaches a
# RotatingFileHandler to the ROOT logger (not just "webbeta") alongside the existing StreamHandler,
# so every logger in the process -- including uvicorn's own error/access loggers, which is where an
# unhandled startup exception would land -- is captured to disk by default, with no separate opt-in
# and no reliance on shell-level redirection alone. run.sh ALSO tees combined stdout+stderr to its
# own per-run log (belt-and-suspenders: catches anything that bypasses the `logging` module
# entirely, e.g. a raw print() or a crash before this line even runs).
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_file_handler = logging.handlers.RotatingFileHandler(
    _LOG_DIR / "webbeta.log", maxBytes=20 * 1024 * 1024, backupCount=5,
)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), _file_handler],
)
log = logging.getLogger("webbeta")

# W2.13 Part 0: "eliminate 'is it running?' forever" -- the last three missions each spent real
# time re-discovering, via indirect evidence, which commit a running server process actually had
# loaded (Python doesn't hot-reload; a server started before an edit keeps running the OLD code
# with no visible sign of it). Computed ONCE at import time (server startup) via a real `git`
# call, never re-derived from anything that could itself be stale -- exposed on /status
# unconditionally (even when live mode is disabled) so one curl always answers "which commit is
# this process actually running" with certainty instead of an investigation.
def _compute_build_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=str(Path(__file__).resolve().parent.parent.parent),
            capture_output=True, text=True, timeout=5, check=True,
        )
        sha = out.stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(Path(__file__).resolve().parent.parent.parent),
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except Exception as exc:
        return f"unknown ({exc})"


BUILD_SHA = _compute_build_sha()
log.info("[app] build stamp: %s", BUILD_SHA)

# W2.4 Part D: /status is polled every 5s by every connected client (see static/app.js
# startStatusPolling) -- at uvicorn's default access-log level that drowns out the events that
# actually matter (connects, closes, evictions). Demoted here rather than disabling access logging
# entirely, so a real problem on any OTHER route is still visible.
class _SuppressStatusAccessLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/status" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_SuppressStatusAccessLog())

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

VALID_TOKENS: set[str] = tokens_mod.load_or_create_tokens()
_LOGIN_RATE_LIMITER = session_auth.LoginRateLimiter()
# MISSION webbeta-landing-page: LoginRateLimiter is a generic per-IP sliding-window limiter despite
# the name (nothing in it is credential-specific) -- reused as-is for the public Request Access form,
# which needs the same basic spam/abuse protection a login form does, via its own separate instance
# (so a burst of form submissions can never count against, or be limited by, real login attempts).
_REQUEST_ACCESS_RATE_LIMITER = session_auth.LoginRateLimiter()
# MISSION google-signin-trial: same generic limiter, own instance again -- a burst of bad/expired
# Google ID tokens hitting /login/google must never count against (or be limited by) real
# username/password attempts on /login, and vice versa.
_GOOGLE_SIGNIN_RATE_LIMITER = session_auth.LoginRateLimiter()
# Real deployment (cliffviewcapital.com) is HTTPS via the cloudflared tunnel -- Secure=True is
# correct and required there. Defaults to secure; only ever overridden to "0" for local plain-HTTP
# testing against 127.0.0.1 (a browser will never send a Secure cookie back over plain HTTP, so
# leaving this at its default while testing over http:// would make every session look like it
# silently failed to persist -- this is a same-machine test knob, not a production weakening).
COOKIE_SECURE = os.environ.get("WEBBETA_COOKIE_SECURE", "1") != "0"
SESSIONS = load_all_sessions()
if not SESSIONS:
    log.warning("No sessions found under webbeta/beta_data/ -- run export_sessions.py first.")

LIVE_ENABLED = os.environ.get("WEBBETA_LIVE_ENABLED", "1") != "0"
LIVE_HUB: Optional[live_bridge.LiveBroadcastHub] = live_bridge.LiveBroadcastHub() if LIVE_ENABLED else None


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    if LIVE_HUB is not None:
        LIVE_HUB.start()
    try:
        yield
    finally:
        if LIVE_HUB is not None:
            LIVE_HUB.stop()


app = FastAPI(title="Book Flow Chart -- Web Beta W1", docs_url=None, redoc_url=None, lifespan=_lifespan)


# W2.13 Part 0: "serve static assets with cache-busting so a stale app.js is impossible." Two
# investigations already burned real time on ambiguity about which SERVER PROCESS was running --
# a stale-cached app.js in the BROWSER is the identical failure mode one layer up (the server can
# be perfectly up to date while a client's own cached copy of app.js is not), and StaticFiles's
# default response has no cache-control headers at all, leaving the browser's own default heuristic
# caching free to serve a stale copy indefinitely. Unconditional no-store on every response is the
# only way to make this impossible rather than merely unlikely.
@app.middleware("http")
async def _no_cache_static(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


# MISSION webbeta-login: the invite-token check is kept exactly as it was (still a valid,
# independent way in) -- this widens it to ALSO accept a valid signed session cookie from the new
# login gate, rather than replacing it. Returns a "principal key" used everywhere the old code used
# the raw token string (the per-connection socket-cap bookkeeping below) -- a session-authenticated
# connection gets the SAME cap protection a token-holder already had, just keyed by its own
# per-login session_id instead of a token, so one browser's connections can't starve another's.
#
# Takes plain `cookies` (a dict) rather than a Request/WebSocket object: FastAPI's Depends()
# resolution needs a dependency's parameter types to match what the ROUTE TYPE actually injects --
# an HTTP route hands a Request in, a WebSocket route hands a WebSocket in, and those are NOT
# interchangeable for a dependency that declares one concretely (confirmed live: type-hinting this
# as Request made every /ws connection -- even a fully valid token -- fail with an unhandled 500,
# caught by tests/test_auth.py's real-websocket-client check, not by curl's fake upgrade headers).
# require_auth (HTTP) and require_auth_ws (WebSocket) below each read cookies from their own
# connection object's .cookies and pass down to this one shared, connection-type-agnostic check.
def _authenticated_principal(cookies: dict, token: Optional[str]) -> Optional[str]:
    if tokens_mod.is_valid(token, VALID_TOKENS):
        return token
    payload = session_auth.verify_session_cookie(cookies.get(session_auth.SESSION_COOKIE_NAME))
    if payload is not None:
        return f"session:{payload.get('sid')}"
    return None


def require_auth(request: Request, token: Optional[str] = Query(None)) -> str:
    principal = _authenticated_principal(request.cookies, token)
    if principal is None:
        raise HTTPException(status_code=401, detail="missing or invalid token")
    return principal


def require_auth_ws(websocket: WebSocket, token: Optional[str] = Query(None)) -> str:
    principal = _authenticated_principal(websocket.cookies, token)
    if principal is None:
        raise HTTPException(status_code=401, detail="missing or invalid token")
    return principal


# MISSION webbeta-retire-token-urls: browser-facing PAGE routes (/ and /app.js) no longer accept a
# bare ?token= at all -- only a valid signed session cookie from the /login gate. /ws, /status, and
# /sessions are explicitly left on require_auth/require_auth_ws above (token OR session), per that
# mission's own Part 0 carve-out for non-browser test/verification tooling.
def require_session(request: Request) -> str:
    payload = session_auth.verify_session_cookie(request.cookies.get(session_auth.SESSION_COOKIE_NAME))
    if payload is None:
        raise HTTPException(status_code=401, detail="no valid session")
    return f"session:{payload.get('sid')}"


# MISSION ai-strategy-lab: llm_chat.py needs the actual username (a Google email or an issued
# credential's username -- either way, the same "u" claim create_session_cookie_value() already
# signs into every session cookie) for per-user rate limiting/daily-token/one-in-flight tracking
# and log lines, not require_session()'s opaque "session:<sid>" principal string. Same 401
# contract, just returns the payload instead of a formatted string.
def require_session_payload(request: Request) -> dict:
    payload = session_auth.verify_session_cookie(request.cookies.get(session_auth.SESSION_COOKIE_NAME))
    if payload is None:
        raise HTTPException(status_code=401, detail="no valid session")
    return payload


LIVE_SESSION_NAME = "live"

# W2.4 Part D: per-token concurrent-socket cap, evicting the oldest connection to make room.
# OrderedDict preserves insertion order per token, so "oldest" is always its first item.
MAX_SOCKETS_PER_TOKEN = 8
_conn_ids = itertools.count()
_active_by_token: dict[str, "OrderedDict[int, WebSocket]"] = defaultdict(OrderedDict)


async def _enforce_socket_cap(token: str) -> None:
    conns = _active_by_token[token]
    while len(conns) >= MAX_SOCKETS_PER_TOKEN:
        oldest_id, oldest_ws = next(iter(conns.items()))
        log.info("ws evict conn_id=%s token=%s reason=per-token-cap-exceeded (max=%d, current=%d)",
                  oldest_id, token, MAX_SOCKETS_PER_TOKEN, len(conns))
        conns.pop(oldest_id, None)
        try:
            await oldest_ws.close(code=1008, reason="connection cap exceeded")
        except Exception:
            pass  # already closing/closed -- the evicted connection's own finally block also pops


@app.get("/sessions")
def list_sessions(_token: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse([
        {"name": s.name, "kind": s.meta.get("kind"), "description": s.meta.get("description"),
         "symbol": s.meta.get("symbol"), "date": s.meta.get("date"),
         "n_bars_total": s.meta.get("n_bars_total"), "duration_s": s.meta.get("duration_s"),
         "book_data": s.meta.get("book_data")}
        for s in SESSIONS.values()
    ])


@app.get("/status")
def live_status(token: str = Depends(require_auth)) -> JSONResponse:
    # W2.4 Part D gate: exposes THIS token's own open-socket count so the connection-lifecycle
    # hammer test can assert "exactly 1 open socket" server-side, not just infer it client-side.
    n_sockets = len(_active_by_token.get(token, {}))
    if LIVE_HUB is None:
        return JSONResponse({
            "live_enabled": False, "active_sockets_for_token": n_sockets, "build_sha": BUILD_SHA,
        })
    status = LIVE_HUB.status()
    status["live_enabled"] = True
    status["book_stale_secs"] = live_bridge.BOOK_STALE_SECS
    status["active_sockets_for_token"] = n_sockets
    status["build_sha"] = BUILD_SHA
    return JSONResponse(status)


@app.get("/zscores")
def zscores_endpoint(
    lo: Optional[int] = Query(None), hi: Optional[int] = Query(None),
    metric: Optional[str] = Query(None),
    _token: str = Depends(require_auth),
) -> JSONResponse:
    """MISSION zscores-panel-viewport-sync: bars for the 5 dashboard Z-Score Board metrics,
    read-only, from the same shared master feature file the dashboard itself reads by default.
    lo/hi are bar_index (same numbering as webbeta's own Data.bars -- verified real, not assumed,
    see server/zscores.py's own module docstring) -- when given, returns exactly that historical
    range via the module's byte-offset index (not a tail read), so scrolling the main chart back in
    history shows the z-scores for the SAME bars, not always "the most recent N". Omitted -> falls
    back to the most recent N_BARS_DEFAULT bars.

    MISSION tester-feedback issue 6: `metric` is optional -- omitted returns all 5 (the original,
    still-supported shape), but app.js's panel only ever renders one at a time and always passes
    it, cutting the measured ~5.3KB/~225KB payloads roughly 5x."""
    return JSONResponse(zscores_mod.build_zscores_payload(lo_bar=lo, hi_bar=hi, metric_key=metric))


@app.get("/app.js")
def app_js(_principal: str = Depends(require_session)) -> Response:
    """W2.13 Part 0: registered as an explicit route specifically to inject BUILD_SHA into the ONE
    static asset most likely to go stale in a browser's cache (webbeta-login mission: now also
    gated by require_auth, see that route's own comment). Reads the real file fresh from disk on
    every request (never
    cached server-side) and substitutes a literal placeholder -- the client then compares ITS OWN
    embedded stamp (captured whenever this specific response was fetched) against /status's
    ALWAYS-fresh, never-cached BUILD_SHA on every poll: if they ever differ, the browser is
    running on an app.js from a server that has since restarted with different code, not the
    current one -- a stale-cache-detector layered on top of the no-store middleware above, in
    case some other caching layer (a proxy, a service worker, a browser bug) ever slips past it."""
    src = (STATIC_DIR / "app.js").read_text()
    src = src.replace("__WEBBETA_BUILD_SHA__", BUILD_SHA)
    return Response(content=src, media_type="application/javascript")


# MISSION webbeta-login: the ONLY unauthenticated GET routes in this entire app are this one, its
# one image asset below, and the POST that checks credentials. Every other route (including "/"
# itself) requires require_auth to return successfully first -- see the bottom of this file for
# why the old blanket StaticFiles mount was removed rather than left in place alongside this.
@app.get("/login")
def login_page() -> Response:
    src = (STATIC_DIR / "login.html").read_text()
    # MISSION google-signin-trial: client_id is a public value (see google_signin_config.py's own
    # docstring), safe to inject into this server-rendered HTML same as BUILD_SHA is injected into
    # app.js above -- read fresh from disk/config on every request, never cached, so
    # set_google_signin_config.py's --enable/--disable takes effect on the very next page load with
    # no restart. Empty string when inactive: login.html's own JS treats that as "don't render the
    # button" (see its own comment), so an unconfigured/disabled trial shows nothing extra at all.
    client_id = ""
    if google_signin_cfg_mod.is_active():
        cfg = google_signin_cfg_mod.load_google_signin_config()
        client_id = cfg["client_id"]
    src = src.replace("__GOOGLE_SIGNIN_CLIENT_ID__", client_id)
    return Response(content=src, media_type="text/html")


@app.get("/login-assets/logo.png")
def login_logo() -> Response:
    data = (STATIC_DIR / "logo.png").read_bytes()
    return Response(content=data, media_type="image/png")


# MISSION webbeta-sitemap-fix: a prior Search Console submission pointed at a URL that served HTML
# (not a real sitemap), so it was rejected -- these two routes are the actual fix. robots.txt was
# previously served only by Cloudflare's edge-level Content Signals injection (confirmed by hitting
# the origin directly: 404, no app-level route existed at all) -- static/robots.txt below is that
# same Cloudflare-served text verbatim, plus one added `Sitemap:` line, so switching to serving it
# from the origin doesn't drop the existing AI-crawl content-signal policy.
@app.get("/robots.txt")
def robots_txt() -> Response:
    src = (STATIC_DIR / "robots.txt").read_text()
    return Response(content=src, media_type="text/plain")


@app.get("/sitemap.xml")
def sitemap_xml() -> Response:
    src = (STATIC_DIR / "sitemap.xml").read_text()
    return Response(content=src, media_type="application/xml")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _redirect_with_session(username: str, ip: str, next_url: str) -> Response:
    # MISSION multi-user-credentials / google-signin-trial: shared by both login paths --
    # create_session_cookie_value() atomically revokes any existing active session for this SAME
    # username first (one active session per account, credential or Google email alike; see
    # auth_db.create_session()), so this one function is the single place either login path needs
    # to reach to become a real, admin-visible, force-logout-able session.
    cookie_value = session_auth.create_session_cookie_value(username, ip)
    response = RedirectResponse(url=next_url, status_code=303)
    # MISSION webbeta-logout-and-browser-session: no max_age/expires -- a true browser-session
    # cookie, cleared when the browser itself fully closes (not just the tab). The signed 7-day
    # max_age check in session_auth.verify_session_cookie() is UNCHANGED and still enforced on every
    # request regardless of cookie type, so a browser that's never fully closed still forces
    # re-login after 7 days -- this only shortens the common case, never lengthens the backstop.
    response.set_cookie(
        key=session_auth.SESSION_COOKIE_NAME, value=cookie_value,
        httponly=True, secure=COOKIE_SECURE, samesite="strict", path="/",
    )
    return response


@app.post("/login")
async def login_submit(request: Request) -> Response:
    ip = _client_ip(request)
    if _LOGIN_RATE_LIMITER.is_rate_limited(ip):
        log.info("login rate-limited ip=%s", ip)
        return RedirectResponse(url="/login?error=rate_limited", status_code=303)

    # request.form() needs python-multipart, which isn't installed (and doesn't need to be --
    # this form is plain application/x-www-form-urlencoded, never multipart/file uploads).
    # Parsed with stdlib urllib.parse instead of adding a dependency for one line of parsing.
    body = (await request.body()).decode("utf-8", errors="replace")
    form = dict(parse_qsl(body))
    username = form.get("username", "")
    password = form.get("password", "")
    next_url = form.get("next", "") or "/"
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"  # never redirect off-site -- an open-redirect guard, not just a UX default

    _LOGIN_RATE_LIMITER.record_attempt(ip)
    if not credentials_mod.verify_login(username, password):
        log.info("login failed ip=%s", ip)  # never logs the username or password itself
        return RedirectResponse(url="/login?error=1", status_code=303)

    log.info("login success ip=%s", ip)
    # Kicked-out-by-a-new-login is logged inside _redirect_with_session's own call chain via
    # auth_db.create_session() -- worth being able to find in the log later.
    return _redirect_with_session(username, ip, next_url)


@app.post("/login/google")
async def login_google_submit(request: Request) -> Response:
    # MISSION google-signin-trial: reversible on/off switch, checked fresh on every request (not
    # cached at import time) -- set_google_signin_config.py's --disable takes effect immediately,
    # no restart needed. Fails CLOSED: unconfigured or switched-off both reject, same as a
    # nonexistent route would, rather than leaking whether it's "off" vs "never set up".
    if not google_signin_cfg_mod.is_active():
        raise HTTPException(status_code=404, detail="not found")
    cfg = google_signin_cfg_mod.load_google_signin_config()

    ip = _client_ip(request)
    if _GOOGLE_SIGNIN_RATE_LIMITER.is_rate_limited(ip):
        log.info("google sign-in rate-limited ip=%s", ip)
        return RedirectResponse(url="/login?error=rate_limited", status_code=303)
    _GOOGLE_SIGNIN_RATE_LIMITER.record_attempt(ip)

    body = (await request.body()).decode("utf-8", errors="replace")
    form = dict(parse_qsl(body))
    credential = form.get("credential", "")
    next_url = form.get("next", "") or "/"
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"  # never redirect off-site -- same open-redirect guard as /login

    try:
        # Verifies the JWT's signature against Google's own current public keys (fetched over
        # HTTPS, cached by the library), its expiry, and that `aud` matches our own Client ID --
        # and, per the google-auth library's own implementation, that `iss` is a genuine Google
        # issuer. A ValueError here means "this token is not a thing Google actually issued to
        # THIS app," not just "malformed" -- reject uniformly, don't try to distinguish why.
        idinfo = google_id_token.verify_oauth2_token(
            credential, google_auth_requests.Request(), cfg["client_id"]
        )
    except ValueError as e:
        log.info("google sign-in token rejected ip=%s reason=%s", ip, e)
        return RedirectResponse(url="/login?error=google_failed", status_code=303)

    if not idinfo.get("email_verified"):
        # A real Google account whose email Google itself has not confirmed -- reject. This is a
        # security-hygiene gate, not a business restriction: every real Gmail address is already
        # email_verified=true by construction, so this only ever excludes the unusual case, never
        # a normal Gmail sign-in.
        log.info("google sign-in email not verified ip=%s", ip)
        return RedirectResponse(url="/login?error=google_failed", status_code=303)

    email = idinfo["email"]
    log.info("google sign-in success ip=%s email=%s", ip, email)
    return _redirect_with_session(email, ip, next_url)


@app.post("/logout")
def logout_submit(request: Request) -> Response:
    # MISSION multi-user-credentials: sessions are now tracked server-side (auth_db.py), so logout
    # is a REAL revoke, not just clearing this browser's own cookie -- a raw copy of the cookie
    # value taken before logout stops working too, not just this browser. (Rotating the signing
    # secret remains the tool for "something may have leaked" at the credential/secret level;
    # this is the tool for "I'm done on this device," now with real teeth.)
    session_auth.revoke_session_from_cookie(request.cookies.get(session_auth.SESSION_COOKIE_NAME))
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(
        key=session_auth.SESSION_COOKIE_NAME, path="/", secure=COOKIE_SECURE, samesite="strict",
    )
    return response


# MISSION webbeta-landing-page: purely additive marketing/lead-capture surface, unauthenticated by
# design (that's the point -- it's what a not-yet-invited visitor sees). Explicit named asset routes,
# same pattern as /login-assets/logo.png above, deliberately NOT a generic static-file mount (this
# codebase already removed its old blanket StaticFiles mount once, see the bottom of this file).
@app.get("/landing-assets/chart-screenshot-1.png")
def landing_screenshot() -> Response:
    data = (STATIC_DIR / "landing-assets" / "chart-screenshot-1.png").read_bytes()
    return Response(content=data, media_type="image/png")


@app.post("/request-access")
async def request_access_submit(request: Request) -> Response:
    ip = _client_ip(request)
    if _REQUEST_ACCESS_RATE_LIMITER.is_rate_limited(ip):
        log.info("request-access rate-limited ip=%s", ip)
        return RedirectResponse(url="/?requested=rate_limited#request-access", status_code=303)
    _REQUEST_ACCESS_RATE_LIMITER.record_attempt(ip)

    body = (await request.body()).decode("utf-8", errors="replace")
    form = dict(parse_qsl(body))
    name = form.get("name", "").strip()
    email = form.get("email", "").strip()
    firm = form.get("firm", "").strip()
    message = form.get("message", "").strip()

    if not name or not leads_mod.is_valid_email(email):
        log.info("request-access invalid submission ip=%s", ip)  # never logs name/email/message
        return RedirectResponse(url="/?requested=0#request-access", status_code=303)

    record = leads_mod.save_lead(name, email, firm, message, ip)
    log.info("request-access saved ip=%s", ip)  # never logs the submitted fields themselves

    # MISSION diagnose-email-notification: best-effort ONLY -- save_lead() above is the durable
    # record; a notify failure (bad credentials, Gmail unreachable, not yet configured) must never
    # fail the user-facing submission. Run off the event loop thread (smtplib is blocking, and this
    # server is shared with live WS/poll traffic) with an explicit success/failure log line either
    # way, so a future "notifications aren't arriving" report has real evidence in the log instead
    # of silence -- exactly the gap the last diagnostic mission found.
    try:
        sent = await asyncio.to_thread(
            notify_mod.send_lead_notification_email,
            name, email, firm, message, ip, record["submitted_at_utc"],
        )
        log.info("request-access notify %s ip=%s", "sent" if sent else "skipped (not configured)", ip)
    except Exception as e:
        log.error("request-access notify FAILED ip=%s error=%s: %s", ip, type(e).__name__, e)

    return RedirectResponse(url="/?requested=1#request-access", status_code=303)


# MISSION ai-strategy-lab: session-gated like everything else behind sign-in -- no separate auth
# mechanism. The browser posts here only; it never learns the model server's address or API key
# (both live in llm_chat.py, read from WEBBETA_LLM_BASE_URL/WEBBETA_LLM_API_KEY at request time).
@app.post("/api/chat")
async def api_chat(request: Request, session: dict = Depends(require_session_payload)):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "bad_request", "message": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "bad_request", "message": "invalid JSON body"}, status_code=400)
    return await llm_chat.stream_chat(request, session.get("u", ""), _client_ip(request), body)


# MISSION raw-data-research Phase 2: second mode of the same chat infrastructure -- same
# session-gating, same StreamingResponse/SSE shape, different system prompt and tool set (raw
# market data + search_literature + web_search + sandbox + a raw-only backtest harness +
# spawn_subagent), routed to raw_research_chat.py instead of llm_chat.py.
@app.post("/api/raw-research/chat")
async def api_raw_research_chat(request: Request, session: dict = Depends(require_session_payload)):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "bad_request", "message": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "bad_request", "message": "invalid JSON body"}, status_code=400)
    return await raw_research_chat.stream_chat(request, session.get("u", ""), _client_ip(request), body)


# MISSION raw-data-research Phase 2: read-only view of the shared journal/derivations/findings so
# the UI can show them without the user needing direct filesystem access -- "read along" as a real
# in-product feature, not only via `cat`ing the files on disk.
@app.get("/api/raw-research/state")
async def api_raw_research_state(session: dict = Depends(require_session_payload)):
    return JSONResponse(raw_research_shared.read_shared_state())


# Third mode of the same chat infrastructure -- general-purpose assistant (unrestricted web_search,
# real local file access, no trading-specific tools/framing), routed to general_chat.py. Same
# session-gating as the other two modes: the browser posts here only, it never learns which local
# model server backs this mode or its API key (both live in general_chat.py, read from
# GENERAL_CHAT_LLM_BASE_URL/GENERAL_CHAT_LLM_API_KEY at request time, independent of the other two
# modes' model server so swapping this mode's backend model never affects them).
@app.post("/api/general-chat")
async def api_general_chat(request: Request, session: dict = Depends(require_session_payload)):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "bad_request", "message": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "bad_request", "message": "invalid JSON body"}, status_code=400)
    return await general_chat.stream_chat(request, session.get("u", ""), _client_ip(request), body)


def _available_session_names() -> list[str]:
    names = ([LIVE_SESSION_NAME] if LIVE_HUB is not None else []) + list(SESSIONS.keys())
    return names


@app.websocket("/ws")
async def ws_endpoint(
    websocket: WebSocket,
    session: Optional[str] = Query(None),
    token: str = Depends(require_auth_ws),
) -> None:
    available = _available_session_names()
    if not available:
        raise HTTPException(status_code=503, detail="no sessions or live mode available")
    # Landing page defaults to LIVE (mission Part B) -- an omitted/unrecognized `session` query
    # param falls back to live if enabled, else the first replay session.
    session_name = session if session in available else available[0]
    await websocket.accept()

    conn_id = next(_conn_ids)
    await _enforce_socket_cap(token)  # evict oldest for this token BEFORE registering the new one
    _active_by_token[token][conn_id] = websocket
    log.info("ws connect conn_id=%s token=%s session=%s (token now has %d open)",
              conn_id, token, session_name, len(_active_by_token[token]))

    await websocket.send_json({
        "type": "hello", "protocol_version": 1,
        "session": session_name, "available_sessions": available,
    })

    # Shared, per-CONNECTION state only -- never shared across connections/clients (see
    # replay.py's module docstring on multi-client safety). `current` holds the live ReplayPlayer
    # (replay mode) so the receiver coroutine can call set_speed()/stop() on whichever player is
    # actually running right now; speed control has no effect in live mode (always real-time).
    state = {
        "session_name": session_name, "speed": 1.0, "closing": False,
        "n_sessions": live_bridge.DEFAULT_N_SESSIONS,
    }
    current: dict[str, Optional[ReplayPlayer]] = {"player": None}

    async def send(msg: dict) -> None:
        await websocket.send_text(json.dumps(msg))

    async def run_replay(name: str) -> None:
        player = ReplayPlayer(SESSIONS[name])
        player.speed = state["speed"]
        current["player"] = player
        await player.run(send)  # returns when player.stop() is called (switch or close)
        current["player"] = None

    async def run_live() -> None:
        assert LIVE_HUB is not None
        cid, q = LIVE_HUB.register(n_sessions=state["n_sessions"])
        try:
            while not state["closing"] and state["session_name"] == LIVE_SESSION_NAME:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue  # just re-check the exit condition; live hub may be quiet briefly
                await send(msg)
        finally:
            LIVE_HUB.unregister(cid)

    async def sender_loop() -> None:
        while not state["closing"]:
            name = state["session_name"]
            if name == LIVE_SESSION_NAME:
                await run_live()
            else:
                await run_replay(name)

    async def receiver_loop() -> None:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")
            if mtype == "speed":
                state["speed"] = float(msg.get("value", 1.0))
                if current["player"] is not None:
                    current["player"].set_speed(state["speed"])
            elif mtype == "session":
                new_name = msg.get("name")
                if new_name in available and new_name != state["session_name"]:
                    state["session_name"] = new_name
                    await send({
                        "type": "hello", "protocol_version": 1,
                        "session": new_name, "available_sessions": available,
                    })
                    if current["player"] is not None:
                        current["player"].stop()  # sender_loop's run_replay() returns, then
                                                    # re-enters the while loop and picks up the
                                                    # new state["session_name"] (live or replay);
                                                    # switching AWAY from live is picked up by
                                                    # run_live()'s own loop condition within its
                                                    # <=1s poll timeout, no explicit signal needed.
            elif mtype == "resync":
                # Part C: the client noticed a version gap (its bounded live queue coalesced past
                # a message) and is asking for a fresh, complete snapshot rather than trying to
                # apply a delta on top of state it doesn't fully have.
                if state["session_name"] == LIVE_SESSION_NAME and LIVE_HUB is not None:
                    snap = LIVE_HUB.snapshot_now(n_sessions=state["n_sessions"])
                    if snap is not None:
                        await send(snap)
                elif current["player"] is not None:
                    current["player"].stop()  # replay: restart from its own snapshot
            elif mtype == "lookback":
                # MISSION lookback-sessions-and-timestamps: same pattern as "resync" -- request a
                # fresh, complete snapshot rather than trying to reconcile a changed window against
                # whatever bars the client already holds. Live-only (replay sessions have no
                # session-count concept of their own; the dropdown is disabled client-side while a
                # replay session is active).
                try:
                    n = int(msg.get("n_sessions"))
                except (TypeError, ValueError):
                    n = None
                if n is not None and 1 <= n <= 5:
                    state["n_sessions"] = n
                    if state["session_name"] == LIVE_SESSION_NAME and LIVE_HUB is not None:
                        snap = LIVE_HUB.snapshot_now(n_sessions=n)
                        if snap is not None:
                            await send(snap)

    # MISSION multi-user-credentials: a session revoked mid-connection (a new login under the same
    # credential, or the local admin tool -- a SEPARATE process, coordinating only through the
    # shared auth_db.py SQLite file, not any direct call into this one) must not just fail on its
    # NEXT unrelated request -- it needs to actually get force-closed. This is the one place that
    # needs teaching: auth is otherwise checked once, at connect time, never again for the life of
    # the connection. Raw-token connections (tokens.py, not part of the DB-backed credential/
    # session model at all) are deliberately exempt -- only "session:<sid>" principals apply here.
    SESSION_REVOCATION_CHECK_S = 5.0

    async def revocation_watchdog() -> None:
        if not token.startswith("session:"):
            return  # not part of the DB-backed model; nothing to watch
        sid = token.split(":", 1)[1]
        while not state["closing"]:
            await asyncio.sleep(SESSION_REVOCATION_CHECK_S)
            if not auth_db.is_session_live(sid):
                log.info("ws force-closing conn_id=%s token=%s (session revoked)", conn_id, token)
                await websocket.close(code=4001, reason="session revoked")
                return

    close_reason = "normal"
    sender_task = asyncio.ensure_future(sender_loop())
    receiver_task = asyncio.ensure_future(receiver_loop())
    watchdog_task = asyncio.ensure_future(revocation_watchdog())
    try:
        done, pending = await asyncio.wait(
            {sender_task, receiver_task, watchdog_task}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            if t is watchdog_task:
                close_reason = "session_revoked"
                continue
            exc = t.exception() if not t.cancelled() else None
            if isinstance(exc, WebSocketDisconnect):
                close_reason = f"client_disconnect code={exc.code}"
            elif exc is not None:
                close_reason = f"error={exc!r}"
    except WebSocketDisconnect as e:
        close_reason = f"client_disconnect code={e.code}"
    except Exception as e:  # noqa: BLE001 -- logged below, then re-raised is unnecessary here
        close_reason = f"error={e!r}"
    finally:
        state["closing"] = True
        if current["player"] is not None:
            current["player"].stop()
        sender_task.cancel()
        receiver_task.cancel()
        watchdog_task.cancel()
        _active_by_token[token].pop(conn_id, None)
        # W2.4 Part D gate: "every open matched by a close in the log" -- conn_id ties this line
        # back to its own "ws connect" line above, for exactly this assertion.
        log.info("ws close conn_id=%s token=%s session=%s reason=%s (token now has %d open)",
                  conn_id, token, state["session_name"], close_reason, len(_active_by_token[token]))


# MISSION webbeta-login: replaces the old blanket `app.mount("/", StaticFiles(...))` -- StaticFiles
# has no per-mount auth hook, and that mount was the biggest of the pre-existing gaps this mission
# closes (it served index.html, and would have served any other file later dropped into static/,
# to anyone, unauthenticated). An unauthenticated GET here gets redirected straight to /login --
# nothing about the chart (markup, data, or otherwise) is present in that response.
@app.get("/")
def index(request: Request) -> Response:
    # MISSION webbeta-retire-token-urls: session cookie ONLY -- a bare ?token= (old or new, valid or
    # not) is no longer sufficient on its own to reach the chart. /login is the sole front door for
    # actually signing in. Deliberately ignores any `token`/`session` query params entirely rather
    # than reading them, so a screenshot-shared or bookmarked ?token=...&session=... URL renders
    # exactly like a bare "/" would -- there is no code path here that ever grants chart access from
    # the query string.
    #
    # MISSION webbeta-landing-page: the unauthenticated case no longer redirects to /login -- it
    # serves the marketing landing page directly (this IS the bare-domain page a not-yet-invited
    # visitor sees). /login remains directly reachable, both by URL and via the landing page's own
    # "Sign In" link; nothing about the auth check itself changes, only what renders when it fails.
    # The landing page contains no <script src="app.js">, no WS-connect code, and no /status polling
    # -- "no chart data reachable without login" holds by construction, not by an extra gate here.
    payload = session_auth.verify_session_cookie(request.cookies.get(session_auth.SESSION_COOKIE_NAME))
    if payload is None:
        src = (STATIC_DIR / "landing.html").read_text()
        return Response(content=src, media_type="text/html")
    src = (STATIC_DIR / "index.html").read_text()
    return Response(content=src, media_type="text/html")


# MISSION ai-strategy-lab: same session-cookie-only gate as "/" above (no bare ?token= bypass,
# consistent with webbeta-retire-token-urls) -- unauthenticated visitors go through /login, which
# already reads ?next= and redirects back here on success, so no change to login.html was needed.
@app.get("/strategy-lab")
def strategy_lab_page(request: Request) -> Response:
    payload = session_auth.verify_session_cookie(request.cookies.get(session_auth.SESSION_COOKIE_NAME))
    if payload is None:
        return RedirectResponse(url="/login?next=/strategy-lab", status_code=302)
    src = (STATIC_DIR / "strategy-lab.html").read_text()
    return Response(content=src, media_type="text/html")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--lan", action="store_true",
                     help="Allow binding a non-loopback host. Without this flag, any --host "
                          "other than 127.0.0.1/localhost is refused.")
    args = ap.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.lan:
        raise SystemExit(
            f"Refusing to bind {args.host!r} without --lan (localhost/LAN-only per mission scope). "
            f"Pass --lan to allow a LAN-visible bind explicitly."
        )
    log.info("Binding %s:%s (lan=%s)", args.host, args.port, args.lan)
    # MISSION lookback-sessions-and-timestamps: previously unset, relying on uvicorn's implicit
    # 16 MB default -- the real measured 5-session raw payload (8.64 MB) had only ~1.85x headroom
    # under that, undocumented/implicit rather than an intentional margin. Made explicit at 32 MB.
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", ws_max_size=32 * 1024 * 1024)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
