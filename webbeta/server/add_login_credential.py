#!/usr/bin/env python3
"""
Interactively create a new, individually-issued webbeta login credential. Run this yourself,
locally:

    cd /home/prabh/OFI_Production/webbeta
    python3 -m server.add_login_credential

Uses getpass for the password (not echoed to the terminal, never in shell history). The plaintext
password is held in memory only for the duration of this process; only its salted PBKDF2 hash is
persisted, to webbeta/server/.auth.db (gitignored).

Revoking a credential (or force-logging-out a live session) is done through the local admin tool
(server/admin_app.py -- 127.0.0.1-only, run via ./run_admin.sh), not this script.
"""
from __future__ import annotations

import getpass
import sys

from . import auth_db


def main() -> int:
    username = input("Username: ").strip()
    if not username:
        print("Username cannot be empty.", file=sys.stderr)
        return 1
    if auth_db.credential_exists(username):
        print(f"A credential for '{username}' already exists.", file=sys.stderr)
        return 1

    display_name = input("Display name (e.g. 'Moeen (evaluation)'): ").strip()
    if not display_name:
        display_name = username

    password = getpass.getpass("Password: ")
    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        return 1
    password_confirm = getpass.getpass("Confirm password: ")
    if password != password_confirm:
        print("Passwords did not match -- no changes made.", file=sys.stderr)
        return 1

    auth_db.create_credential(username, password, display_name)
    print(f"Created credential '{username}' (display name: '{display_name}').")
    print(f"(Stored as a salted hash only, at {auth_db.DB_FILE} -- the password itself was never "
          f"written anywhere.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
