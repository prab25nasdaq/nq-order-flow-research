#!/usr/bin/env python3
"""
Interactively set the LOCAL ADMIN TOOL's own login credential (server/admin_app.py) -- separate
from any tester-facing credential in .auth.db. Run this yourself, locally:

    cd /home/prabh/OFI_Production/webbeta
    python3 -m server.set_admin_credentials

Uses getpass for the password (not echoed to the terminal, never in shell history). Written to
webbeta/server/.admin_credentials.json (gitignored, 0600) as a salted PBKDF2 hash -- the plaintext
password is never written anywhere.

This is defense in depth on top of the admin tool's own 127.0.0.1-only bind, not a replacement for
it -- the real security boundary is that the tool is never reachable from anywhere but this
machine in the first place.
"""
from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path

from . import auth_db

ADMIN_CREDENTIALS_FILE = Path(
    os.environ.get("WEBBETA_ADMIN_CREDENTIALS_FILE") or (Path(__file__).resolve().parent / ".admin_credentials.json")
)


def main() -> int:
    if ADMIN_CREDENTIALS_FILE.exists():
        try:
            existing_username = json.loads(ADMIN_CREDENTIALS_FILE.read_text()).get("username")
        except Exception:
            existing_username = "?"
        print(f"An admin credential already exists (username: {existing_username}).")
        confirm = input("Overwrite it? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Aborted -- no changes made.")
            return 0

    username = input("Admin username: ").strip()
    if not username:
        print("Username cannot be empty.", file=sys.stderr)
        return 1

    password = getpass.getpass("Admin password: ")
    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        return 1
    password_confirm = getpass.getpass("Confirm password: ")
    if password != password_confirm:
        print("Passwords did not match -- no changes made.", file=sys.stderr)
        return 1

    ADMIN_CREDENTIALS_FILE.write_text(json.dumps({
        "username": username,
        "password_hash": auth_db.hash_password(password),
    }))
    ADMIN_CREDENTIALS_FILE.chmod(0o600)
    print(f"Saved. Admin tool login set for username '{username}'.")
    print(f"(Stored as a salted hash only, at {ADMIN_CREDENTIALS_FILE} -- the password itself "
          f"was never written anywhere.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
