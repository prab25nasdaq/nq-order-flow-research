#!/usr/bin/env python3
"""
One-time migration: imports the old single shared credential (webbeta/server/.credentials.json,
from before the multi-user-credentials mission) into auth_db.py's SQLite `credentials` table as
the first individually-tracked credential. Run this yourself, once:

    cd /home/prabh/OFI_Production/webbeta
    python3 -m server.migrate_credentials_to_db

Imports the EXISTING password hash as-is (same PBKDF2 format both before and after this
migration) -- never needs, sees, or asks for the real plaintext password, so whoever already has
that credential keeps working with it unchanged; only the storage backend moves. After a
successful import, the old file is renamed to .credentials.json.migrated so credentials.py's new
SQLite-backed verify_login() (which never reads it) can't be confused with a live source of truth.

If .credentials.json doesn't exist (a fresh install, or already migrated), this is a no-op --
create the first real credential with add_login_credential.py instead.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import auth_db

OLD_CREDENTIALS_FILE = Path(__file__).resolve().parent / ".credentials.json"


def main() -> int:
    if not OLD_CREDENTIALS_FILE.exists():
        print(f"{OLD_CREDENTIALS_FILE} not found -- nothing to migrate.")
        print("To create the first credential directly, run: python3 -m server.add_login_credential")
        return 0

    try:
        data = json.loads(OLD_CREDENTIALS_FILE.read_text())
        username = data["username"]
        password_hash = data["password_hash"]
    except Exception as exc:
        print(f"Could not read/parse {OLD_CREDENTIALS_FILE}: {exc}", file=sys.stderr)
        return 1

    if auth_db.credential_exists(username):
        print(f"A credential for username '{username}' already exists in the database -- "
              "nothing to do. If you want to re-migrate, remove it first.")
        return 0

    display_name = input(f"Display name for '{username}' (e.g. 'Prabh (primary)'): ").strip()
    if not display_name:
        display_name = username

    auth_db.import_credential_hash(username, password_hash, display_name)
    print(f"Imported '{username}' (display name: '{display_name}') into {auth_db.DB_FILE}.")

    migrated_path = OLD_CREDENTIALS_FILE.with_suffix(".json.migrated")
    OLD_CREDENTIALS_FILE.rename(migrated_path)
    print(f"Renamed {OLD_CREDENTIALS_FILE.name} -> {migrated_path.name} (no longer read by "
          "credentials.py -- kept only as a record, safe to delete later).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
