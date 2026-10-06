#!/usr/bin/env python3
"""
webbeta local admin tool -- MISSION multi-user-credentials Part B.

Live session list (force-logout per session) + credential management (create/revoke), reading and
writing the SAME auth_db.py SQLite file (webbeta/server/.auth.db) the main webbeta server process
uses. Coordination between the two processes is entirely through that one shared, WAL-mode file --
no RPC, no shared memory, no direct call from one process into the other.

HARD REQUIREMENT, defense-in-depth, not just a default: binds to 127.0.0.1 ONLY. There is no flag,
env var, or code path in this file that can bind it anywhere else -- unlike server/app.py (which
supports an explicit --lan opt-in for the main chart server), this tool has no such option at all.
Runs on its own port (9001), distinct from webbeta's own (8800) and every ephemeral test port used
elsewhere in this repo. Never listed in the Cloudflare Tunnel's ingress config (~/.cloudflared/
config.yml) -- confirmed separately, not assumed, in the mission's own gate report.

A second, independent login layer sits in front of every route here (HTTP Basic, a single shared
admin credential stored separately from the tester-facing credentials in .auth.db) -- explicitly
defense in depth on top of the loopback-only bind, not a replacement for it: this tool is only ever
reachable at all if you're already on this machine.

Run via ../run_admin.sh (a plain script, not a systemd service -- see that file's own comment for
why). Set the admin credential first with set_admin_credentials.py.
"""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import auth_db

# WEBBETA_ADMIN_CREDENTIALS_FILE: same test-isolation escape hatch as WEBBETA_AUTH_DB_FILE and
# every other WEBBETA_*_FILE override in this codebase -- lets a test instance use an isolated
# admin credential without ever touching the real one.
ADMIN_CREDENTIALS_FILE = Path(
    os.environ.get("WEBBETA_ADMIN_CREDENTIALS_FILE") or (Path(__file__).resolve().parent / ".admin_credentials.json")
)

app = FastAPI(title="webbeta admin (local only)")
security = HTTPBasic()


# ------------------------------------------------------------------------------ admin's own login --
def _load_admin_credentials() -> Optional[dict]:
    if not ADMIN_CREDENTIALS_FILE.exists():
        return None
    try:
        data = json.loads(ADMIN_CREDENTIALS_FILE.read_text())
        if isinstance(data, dict) and data.get("username") and data.get("password_hash"):
            return data
    except Exception:
        pass
    return None


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    stored = _load_admin_credentials()
    if stored is None:
        # Fails CLOSED: no admin credential configured means no access, not open access.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin credential not configured -- run set_admin_credentials.py",
            headers={"WWW-Authenticate": "Basic"},
        )
    import hmac
    username_ok = hmac.compare_digest(credentials.username.encode("utf-8"), stored["username"].encode("utf-8"))
    password_ok = auth_db.verify_password(credentials.password, stored["password_hash"])
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# ------------------------------------------------------------------------------------------- HTML --
PAGE_CSS = """
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; font-family: "Segoe UI", sans-serif; background: #050508; color: #ddd; }
  .wrap { max-width: 1040px; margin: 0 auto; padding: 28px 24px 60px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .subtitle { color: #7d859c; font-size: 12.5px; margin: 0 0 24px; }
  nav a { color: #8fd; text-decoration: none; margin-right: 18px; font-size: 13px; }
  nav { margin-bottom: 22px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 28px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #222; }
  th { color: #8892a8; font-weight: 600; font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.04em; }
  tr:hover td { background: rgba(120, 140, 200, 0.05); }
  .pill { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 700; }
  .pill.active { background: rgba(79, 209, 197, 0.15); color: #7fe9dd; }
  .pill.revoked { background: rgba(255, 77, 109, 0.12); color: #ff8fa3; }
  button { background: rgba(255, 77, 109, 0.12); border: 1px solid rgba(255, 77, 109, 0.4); color: #ff8fa3;
           border-radius: 5px; padding: 5px 12px; font-size: 12px; cursor: pointer; }
  button:hover { background: rgba(255, 77, 109, 0.2); }
  .card { background: rgba(12, 13, 20, 0.72); border: 1px solid rgba(120, 140, 200, 0.22);
          border-radius: 8px; padding: 20px 22px; max-width: 420px; margin-top: 10px; }
  .card h2 { font-size: 14px; margin: 0 0 14px; }
  label { display: block; font-size: 12px; color: #9aa4bb; margin: 0 0 5px; }
  input { width: 100%; padding: 8px 10px; margin-bottom: 12px; background: #14151d; border: 1px solid #2c2f3d;
          border-radius: 5px; color: #eee; font-size: 13px; }
  .submit { background: linear-gradient(90deg, #4fd1c5, #8b7ff0); color: #050508; border: none;
            font-weight: 600; padding: 8px 16px; border-radius: 5px; cursor: pointer; }
  .empty { color: #566; font-size: 13px; padding: 16px 0; }
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8"><title>webbeta admin -- {html.escape(title)}</title>
<style>{PAGE_CSS}</style></head>
<body><div class="wrap">
<h1>webbeta admin</h1>
<p class="subtitle">Local only -- 127.0.0.1:9001. Not reachable through the tunnel.</p>
<nav><a href="/">Sessions</a><a href="/credentials">Credentials</a></nav>
{body}
</div></body></html>""")


# ---------------------------------------------------------------------------------------- routes --
@app.get("/", response_class=HTMLResponse)
def sessions_page(_admin: str = Depends(require_admin)) -> HTMLResponse:
    rows = auth_db.list_sessions()
    if not rows:
        table = '<p class="empty">No sessions yet.</p>'
    else:
        trs = []
        for r in rows:
            status_pill = '<span class="pill revoked">revoked</span>' if r["revoked"] else '<span class="pill active">active</span>'
            action = (
                f'<form method="post" action="/sessions/{html.escape(r["session_id"])}/revoke" style="margin:0;">'
                f'<button type="submit">Force Logout</button></form>'
                if not r["revoked"] else "&mdash;"
            )
            trs.append(f"""<tr>
                <td>{html.escape(r["display_name"] or r["username"])}</td>
                <td>{html.escape(r["username"])}</td>
                <td>{html.escape(r["created_at"])}</td>
                <td>{html.escape(r["last_seen_at"])}</td>
                <td>{html.escape(r["login_ip"] or "")}</td>
                <td>{status_pill}</td>
                <td>{action}</td>
            </tr>""")
        table = f"""<table>
            <tr><th>Name</th><th>Username</th><th>Login time</th><th>Last seen</th><th>IP</th><th>Status</th><th></th></tr>
            {''.join(trs)}
        </table>"""
    return _page("Sessions", f"<h2 style='font-size:15px'>Live sessions</h2>{table}")


@app.post("/sessions/{session_id}/revoke")
def revoke_session_route(session_id: str, _admin: str = Depends(require_admin)) -> RedirectResponse:
    auth_db.revoke_session(session_id)
    return RedirectResponse(url="/", status_code=303)


@app.get("/credentials", response_class=HTMLResponse)
def credentials_page(_admin: str = Depends(require_admin)) -> HTMLResponse:
    rows = auth_db.list_credentials()
    if not rows:
        table = '<p class="empty">No credentials yet.</p>'
    else:
        trs = []
        for r in rows:
            status_pill = '<span class="pill active">active</span>' if r["active"] else '<span class="pill revoked">revoked</span>'
            action = (
                f'<form method="post" action="/credentials/{html.escape(r["username"])}/revoke" style="margin:0;">'
                f'<button type="submit">Revoke</button></form>'
                if r["active"] else "&mdash;"
            )
            trs.append(f"""<tr>
                <td>{html.escape(r["username"])}</td>
                <td>{html.escape(r["display_name"])}</td>
                <td>{html.escape(r["created_at"])}</td>
                <td>{status_pill}</td>
                <td>{action}</td>
            </tr>""")
        table = f"""<table>
            <tr><th>Username</th><th>Display name</th><th>Created</th><th>Status</th><th></th></tr>
            {''.join(trs)}
        </table>"""
    form = """
      <div class="card">
        <h2>Create new credential</h2>
        <form method="post" action="/credentials/create">
          <label for="username">Username</label>
          <input type="text" id="username" name="username" required>
          <label for="display_name">Display name</label>
          <input type="text" id="display_name" name="display_name" required>
          <label for="password">Password</label>
          <input type="password" id="password" name="password" required minlength="8">
          <button type="submit" class="submit">Create</button>
        </form>
      </div>
    """
    return _page("Credentials", f"<h2 style='font-size:15px'>Credentials</h2>{table}{form}")


@app.post("/credentials/create")
async def create_credential_route(request: Request, _admin: str = Depends(require_admin)) -> RedirectResponse:
    # Manually parsed, not FastAPI's Form(...) -- that needs python-multipart under the hood for
    # ANY form content type (not just multipart uploads, despite the name), which isn't installed
    # here; same fix already applied to app.py's own POST /login and POST /request-access.
    body = (await request.body()).decode("utf-8", errors="replace")
    form = dict(parse_qsl(body))
    username = form.get("username", "").strip()
    display_name = form.get("display_name", "").strip() or username
    password = form.get("password", "")
    if not username or len(password) < 8 or auth_db.credential_exists(username):
        return RedirectResponse(url="/credentials?error=1", status_code=303)
    auth_db.create_credential(username, password, display_name)
    return RedirectResponse(url="/credentials", status_code=303)


@app.post("/credentials/{username}/revoke")
def revoke_credential_route(username: str, _admin: str = Depends(require_admin)) -> RedirectResponse:
    auth_db.revoke_credential(username)  # also force-revokes any of its currently-open sessions
    return RedirectResponse(url="/credentials", status_code=303)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9001)
    args = parser.parse_args()
    # host is intentionally NOT a CLI option -- 127.0.0.1, always, no override path.
    uvicorn.run(app, host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
