"""
Signed session cookies + login-attempt rate limiting for the webbeta login gate.
SHADOW/RESEARCH/LOCALHOST-LAN ONLY.

MISSION multi-user-credentials: sessions are no longer purely stateless. The cookie itself is
still an itsdangerous-signed token carrying {username, session_id}, HMAC-signed with SECRET_KEY
(generated once, persisted like tokens.py's own token, gitignored) with a max_age check at read
time for the 7-day backstop -- but the signature+expiry check is now ALSO backed by auth_db.py's
`sessions` table: create_session_cookie_value() creates the row (and atomically kicks out any
prior active session for that username), verify_session_cookie() additionally checks the row is
still revoked=0, and a session can now be force-ended on demand (a new login under the same
credential, or the local admin tool) -- something a purely stateless signed token could never do
short of rotating the signing secret for everyone at once.

Rate limiting: in-memory per-IP sliding window, same lightweight style as app.py's own
_active_by_token dict -- no Redis, no external dependency. Resets naturally as old timestamps
age out of the window; not persisted across a restart (acceptable for this project's scale).
"""
from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Optional

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import auth_db

SECRET_KEY_FILE = Path(__file__).resolve().parent / ".session_secret.json"
SESSION_COOKIE_NAME = "webbeta_session"
SESSION_MAX_AGE_S = 7 * 24 * 3600  # ~7 days, per mission's own proposed default

LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 5
LOGIN_RATE_LIMIT_WINDOW_S = 60.0


def _load_or_create_secret_key() -> str:
    import json
    if SECRET_KEY_FILE.exists():
        try:
            data = json.loads(SECRET_KEY_FILE.read_text())
            if isinstance(data, dict) and data.get("key"):
                return data["key"]
        except Exception:
            pass
    key = secrets.token_urlsafe(32)
    SECRET_KEY_FILE.write_text(json.dumps({"key": key}))
    SECRET_KEY_FILE.chmod(0o600)
    return key


_SECRET_KEY = _load_or_create_secret_key()
_serializer = URLSafeTimedSerializer(_SECRET_KEY, salt="webbeta-session")


def create_session_cookie_value(username: str, ip: str) -> str:
    # auth_db.create_session() does the atomic "revoke any existing active session for this
    # username, then create the new row" kick-out transaction -- the returned session_id is what
    # gets embedded in the signed cookie, so the DB row and the cookie's own 'sid' always match.
    session_id = auth_db.create_session(username, ip)
    return _serializer.dumps({"u": username, "sid": session_id})


def verify_session_cookie(cookie_value: Optional[str]) -> Optional[dict]:
    """Returns the decoded payload (dict with 'u'/'sid') if the signature is valid, unexpired, AND
    the session hasn't been revoked (a new login under the same credential, or the local admin
    tool) -- else None. Every existing caller (require_auth, require_auth_ws, require_session, and
    "/"'s own inline check) already just calls this one function, so the added liveness check
    reaches every authenticated route with no other code changed."""
    if not cookie_value:
        return None
    try:
        payload = _serializer.loads(cookie_value, max_age=SESSION_MAX_AGE_S)
    except (BadSignature, SignatureExpired, Exception):
        return None
    sid = payload.get("sid")
    if not auth_db.is_session_live(sid):
        return None
    auth_db.touch_last_seen(sid)
    return payload


def revoke_session_from_cookie(cookie_value: Optional[str]) -> None:
    """Used by POST /logout: unlike the old stateless design (which could only clear the
    browser's own cookie), this now genuinely revokes the session server-side too -- a raw copy of
    the cookie value taken before logout is no longer usable afterward, not just absent from this
    one browser."""
    payload = verify_session_cookie(cookie_value)
    if payload is not None:
        auth_db.revoke_session(payload.get("sid"))


class LoginRateLimiter:
    """Sliding-window limiter: max_attempts per window_s seconds per key (an IP for the login
    gate; llm_chat.py reuses this same class per-username for chat/backtest/search limits).
    In-memory, matches app.py's existing _active_by_token dict style -- deliberately not
    persisted across restarts.

    Defaults to LOGIN_RATE_LIMIT_MAX_ATTEMPTS/LOGIN_RATE_LIMIT_WINDOW_S so every zero-arg call
    site (this module's own login gate) is unchanged. MISSION strategy-lab-web-search: added
    explicit max_attempts/window_s params because the search rate limit genuinely needs to be
    its own number (search costs real money per call) -- discovered along the way that
    llm_chat.py's CHAT_RATE_LIMITER/BACKTEST_RATE_LIMITER were passing no args at all, so both
    silently ran at the login default (5/60s) instead of their documented 20/min and 10/min.
    MISSION strategy-lab-narration-verify: that mismatch turned out to be a real, live cause of
    an apparent narration failure (rapid manual re-testing tripped the actual 5/60s ceiling,
    and the resulting upfront JSONResponse rejection is indistinguishable, client-side, from an
    empty/instant response) -- llm_chat.py now passes both their real thresholds through these
    same params."""

    def __init__(self, max_attempts: int = LOGIN_RATE_LIMIT_MAX_ATTEMPTS,
                 window_s: float = LOGIN_RATE_LIMIT_WINDOW_S) -> None:
        self._max_attempts = max_attempts
        self._window_s = window_s
        self._attempts: dict[str, list[float]] = {}

    def _prune(self, ip: str, now: float) -> list[float]:
        cutoff = now - self._window_s
        attempts = [t for t in self._attempts.get(ip, []) if t > cutoff]
        self._attempts[ip] = attempts
        return attempts

    def is_rate_limited(self, ip: str) -> bool:
        now = time.time()
        attempts = self._prune(ip, now)
        return len(attempts) >= self._max_attempts

    def record_attempt(self, ip: str) -> None:
        now = time.time()
        attempts = self._prune(ip, now)
        attempts.append(now)
        self._attempts[ip] = attempts
