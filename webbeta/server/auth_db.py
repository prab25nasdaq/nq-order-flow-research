"""
Shared SQLite-backed store for webbeta's multi-user credentials + live session tracking.
SHADOW/RESEARCH/LOCALHOST-LAN ONLY.

Replaces the single-shared-credential / stateless-signed-cookie-only design: credentials are now
individually issued and individually revocable, and every session is tracked server-side (session
id, issuing credential, created/last-seen time, login IP, revoked flag) so a session can be forced
to end on demand -- either by a new login under the same credential (automatic "one active session
per credential" kick-out) or by the local admin tool (server/admin_app.py) revoking it directly.

Storage: webbeta/server/.auth.db (gitignored, 0600), WAL journal mode so the webbeta server process
and the separate, local-only admin tool can both read/write it concurrently without any RPC between
them -- coordination is entirely through this one shared file.

Password hashing is unchanged from the single-credential design: stdlib hashlib.pbkdf2_hmac,
260,000 iterations, same "pbkdf2_sha256$iter$salt$digest" storage format.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Optional

# WEBBETA_AUTH_DB_FILE: same test-isolation escape hatch as WEBBETA_LEADS_FILE / the other
# WEBBETA_* env vars -- lets a spawned test-sandbox server process use an isolated (or nonexistent)
# DB instead of the real one, so a test run's fake credentials/sessions never touch real data.
DB_FILE = Path(os.environ.get("WEBBETA_AUTH_DB_FILE") or (Path(__file__).resolve().parent / ".auth.db"))

PBKDF2_ITERATIONS = 260_000
_ALGO = "pbkdf2_sha256"

# How often touch_last_seen() actually issues a write, per session_id -- avoids a DB write on
# every single authenticated request (which happens often: /status polling, every WS revocation
# check, every page nav) when "last seen a few seconds ago" vs "last seen right now" carries no
# real information for this tool's purpose.
LAST_SEEN_TOUCH_THROTTLE_S = 60.0


# ---------------------------------------------------------------------------- password hashing --
def hash_password(password: str, *, salt: Optional[bytes] = None) -> str:
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{_ALGO}${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo, iterations_s, salt_hex, digest_hex = stored_hash.split("$")
        if algo != _ALGO:
            return False
        iterations = int(iterations_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


# ------------------------------------------------------------------------------ connection/schema --
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS credentials (
                username      TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                display_name  TEXT NOT NULL,
                created_at    TEXT NOT NULL,
                active        INTEGER NOT NULL DEFAULT 1
            )
        """)
        # username is deliberately a plain column, NOT a FOREIGN KEY REFERENCES credentials(...):
        # the WEBBETA_LOGIN_USERNAME/PASSWORD test-isolation escape hatch (credentials.py) verifies
        # against an env var, never touching the credentials table at all -- a hard FK here would
        # reject every test-suite session creation with a constraint violation. list_sessions()'s
        # LEFT JOIN already tolerates a username with no matching credentials row (display_name
        # just comes back NULL), so nothing downstream assumes the FK held anyway.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id   TEXT PRIMARY KEY,
                username     TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                login_ip     TEXT,
                revoked      INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_username ON sessions(username)")
        conn.commit()
    finally:
        conn.close()
    try:
        DB_FILE.chmod(0o600)
    except FileNotFoundError:
        pass  # WEBBETA_AUTH_DB_FILE pointed at ':memory:' or similar in some test context


init_db()  # module import time, mirrors tokens.py's/credentials.py's own load-or-create pattern


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ------------------------------------------------------------------------------------ credentials --
def verify_credential(username: str, password: str) -> bool:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT username, password_hash, active FROM credentials WHERE username = ?", (username,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        # Still do a real PBKDF2 computation even for a nonexistent username, so "no such user"
        # and "wrong password" take the same amount of time -- same timing-side-channel care the
        # single-credential design already had, extended to "does this username exist at all".
        verify_password(password, hash_password(secrets.token_hex(8)))
        return False
    if not row["active"]:
        return False
    username_ok = hmac.compare_digest(username.encode("utf-8"), row["username"].encode("utf-8"))
    password_ok = verify_password(password, row["password_hash"])
    return username_ok and password_ok


def create_credential(username: str, password: str, display_name: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO credentials (username, password_hash, display_name, created_at, active) "
            "VALUES (?, ?, ?, ?, 1)",
            (username, hash_password(password), display_name, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def import_credential_hash(username: str, password_hash: str, display_name: str) -> None:
    """Migration-only: inserts a credential from an ALREADY-HASHED password (the exact PBKDF2
    string already on disk from the old single-shared-credential .credentials.json) -- never sees
    or needs the real plaintext password, so the existing credential keeps working through the
    migration with no forced re-entry and no exposure of the real password to this tooling."""
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO credentials (username, password_hash, display_name, created_at, active) "
            "VALUES (?, ?, ?, ?, 1)",
            (username, password_hash, display_name, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def credential_exists(username: str) -> bool:
    conn = _connect()
    try:
        row = conn.execute("SELECT 1 FROM credentials WHERE username = ?", (username,)).fetchone()
    finally:
        conn.close()
    return row is not None


def revoke_credential(username: str) -> None:
    """Deactivates the credential (active=0, so it can no longer log in) AND force-revokes every
    currently-active session issued under it (so an already-logged-in holder doesn't keep working
    just because their existing session predates the revoke)."""
    conn = _connect()
    try:
        conn.execute("BEGIN")
        conn.execute("UPDATE credentials SET active = 0 WHERE username = ?", (username,))
        conn.execute("UPDATE sessions SET revoked = 1 WHERE username = ? AND revoked = 0", (username,))
        conn.commit()
    finally:
        conn.close()


def list_credentials() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT username, display_name, created_at, active FROM credentials ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------------------- sessions --
def create_session(username: str, ip: str) -> str:
    """Kicks out (revokes) any existing active session for this username, then creates and returns
    a new session_id -- one atomic transaction, no race window between "revoke the old one" and
    "the old one makes one more request" being visible."""
    session_id = secrets.token_urlsafe(16)
    conn = _connect()
    try:
        conn.execute("BEGIN")
        conn.execute("UPDATE sessions SET revoked = 1 WHERE username = ? AND revoked = 0", (username,))
        now = _now_iso()
        conn.execute(
            "INSERT INTO sessions (session_id, username, created_at, last_seen_at, login_ip, revoked) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (session_id, username, now, now, ip),
        )
        conn.commit()
    finally:
        conn.close()
    return session_id


_last_touch: dict[str, float] = {}


def touch_last_seen(session_id: str) -> None:
    now_mono = time.monotonic()
    if now_mono - _last_touch.get(session_id, 0.0) < LAST_SEEN_TOUCH_THROTTLE_S:
        return
    _last_touch[session_id] = now_mono
    conn = _connect()
    try:
        conn.execute("UPDATE sessions SET last_seen_at = ? WHERE session_id = ?", (_now_iso(), session_id))
        conn.commit()
    finally:
        conn.close()


def is_session_live(session_id: Optional[str]) -> bool:
    """Fails CLOSED: a missing row, a revoked row, or (implicitly, via the exception propagating up
    to the caller in the extremely unlikely event of a DB error) is never treated as valid."""
    if not session_id:
        return False
    conn = _connect()
    try:
        row = conn.execute("SELECT revoked FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    finally:
        conn.close()
    return row is not None and row["revoked"] == 0


def revoke_session(session_id: str) -> bool:
    """Returns True if a row actually existed and was active (so the caller -- e.g. the admin
    tool's Force Logout button -- can tell "revoked" from "nothing to revoke")."""
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE sessions SET revoked = 1 WHERE session_id = ? AND revoked = 0", (session_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_sessions() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute("""
            SELECT s.session_id, s.username, c.display_name, s.created_at, s.last_seen_at,
                   s.login_ip, s.revoked
            FROM sessions s
            LEFT JOIN credentials c ON c.username = s.username
            ORDER BY s.created_at DESC
        """).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
