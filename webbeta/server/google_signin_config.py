"""
Google Sign-In config for the self-serve trial. SHADOW/RESEARCH ONLY.

Storage: webbeta/server/.google_signin_config.json (gitignored, 0600) -- client_id, enabled.
`client_id` is a public value (Google's own docs: safe to embed in client-side HTML, it identifies
the app, not a secret -- this flow verifies the signed ID token server-side, no client secret is
ever involved). `enabled` is the REVERSIBLE on/off switch for the trial: flip it via
set_google_signin_config.py at any time, no redeploy, no code change, no need to touch or forget
the Client ID itself when the trial ends.

Set via set_google_signin_config.py.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

# WEBBETA_GOOGLE_SIGNIN_CONFIG_FILE: same test-isolation escape hatch as every other
# WEBBETA_*_FILE override in this codebase -- a test process gets an isolated (or nonexistent)
# config, never the real one.
GOOGLE_SIGNIN_CONFIG_FILE = Path(
    os.environ.get("WEBBETA_GOOGLE_SIGNIN_CONFIG_FILE")
    or (Path(__file__).resolve().parent / ".google_signin_config.json")
)


def save_google_signin_config(client_id: str, enabled: bool) -> None:
    GOOGLE_SIGNIN_CONFIG_FILE.write_text(json.dumps({
        "client_id": client_id,
        "enabled": enabled,
    }))
    GOOGLE_SIGNIN_CONFIG_FILE.chmod(0o600)


def load_google_signin_config() -> Optional[dict]:
    if not GOOGLE_SIGNIN_CONFIG_FILE.exists():
        return None
    try:
        data = json.loads(GOOGLE_SIGNIN_CONFIG_FILE.read_text())
        if isinstance(data, dict) and data.get("client_id"):
            data.setdefault("enabled", False)
            return data
    except Exception:
        pass
    return None


def set_enabled(enabled: bool) -> bool:
    """Flips just the on/off switch, keeping the already-stored client_id. Returns False (no-op)
    if no client_id has ever been configured -- there's nothing to enable yet."""
    existing = load_google_signin_config()
    if existing is None:
        return False
    save_google_signin_config(existing["client_id"], enabled)
    return True


def is_active() -> bool:
    """What the login page and the verification route both actually check: configured AND
    currently switched on."""
    cfg = load_google_signin_config()
    return cfg is not None and bool(cfg.get("enabled"))
