"""
Login credential verification for the webbeta login gate. SHADOW/RESEARCH/LOCALHOST-LAN ONLY.

MISSION multi-user-credentials: thin wrapper over auth_db.py's SQLite-backed `credentials` table
(individually issued, individually revocable credentials, replacing the old single shared
.credentials.json). app.py's POST /login handler calls verify_login(username, password) exactly as
it always has -- only the storage backend changed. See migrate_credentials_to_db.py for the
one-time migration from the old shared credential, and add_login_credential.py for issuing new
individual credentials going forward.
"""
from __future__ import annotations

import hmac
import os

from . import auth_db


def verify_login(username: str, password: str) -> bool:
    # WEBBETA_LOGIN_USERNAME/PASSWORD: test-isolation escape hatch, mirroring tokens.py's own
    # WEBBETA_TOKENS env var exactly -- lets a spawned test-sandbox server process authenticate
    # with a per-run test credential WITHOUT ever touching the real .auth.db file. Only takes
    # effect when BOTH env vars are set; the real deployment never sets these, so this path is
    # simply absent there -- not a bypass reachable via the URL/browser.
    env_username = os.environ.get("WEBBETA_LOGIN_USERNAME")
    env_password = os.environ.get("WEBBETA_LOGIN_PASSWORD")
    if env_username is not None and env_password is not None:
        username_ok = hmac.compare_digest(username.encode("utf-8"), env_username.encode("utf-8"))
        password_ok = hmac.compare_digest(password.encode("utf-8"), env_password.encode("utf-8"))
        return username_ok and password_ok

    return auth_db.verify_credential(username, password)
