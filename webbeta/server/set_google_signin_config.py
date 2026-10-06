#!/usr/bin/env python3
"""
Set or toggle the webbeta Google Sign-In trial config. Run this yourself, locally:

    cd /home/prabh/OFI_Production/webbeta
    python3 -m server.set_google_signin_config --client-id 123-abc.apps.googleusercontent.com
    python3 -m server.set_google_signin_config --enable
    python3 -m server.set_google_signin_config --disable

--client-id sets (or updates) the Google Cloud OAuth 2.0 Web application Client ID (from
console.cloud.google.com -- APIs & Services > Credentials; Authorized JavaScript origin must be
https://cliffviewcapital.com). This is a public identifier, not a secret -- safe to pass as a plain
argument, unlike a password.

--enable / --disable is the reversible trial on/off switch -- flip it any time, no code change, no
redeploy. Passing --client-id alone does NOT turn the feature on by itself if it was already off;
combine with --enable on first setup, e.g.:

    python3 -m server.set_google_signin_config --client-id 123-abc.apps.googleusercontent.com --enable
"""
from __future__ import annotations

import argparse
import sys

from . import google_signin_config as cfg_mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", default=None, help="Google OAuth Client ID (public value)")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--enable", action="store_true", help="turn Google Sign-In on")
    group.add_argument("--disable", action="store_true", help="turn Google Sign-In off (reversible)")
    args = ap.parse_args()

    existing = cfg_mod.load_google_signin_config()

    if args.client_id is None and not args.enable and not args.disable:
        if existing is None:
            print("No Google Sign-In config set yet. Pass --client-id to set one.")
        else:
            print(f"client_id: {existing['client_id']}")
            print(f"enabled:   {existing['enabled']}")
        return 0

    if args.client_id is not None:
        client_id = args.client_id.strip()
        if not client_id.endswith(".apps.googleusercontent.com"):
            print("That doesn't look like a Google OAuth Client ID "
                  "(expected it to end in .apps.googleusercontent.com).", file=sys.stderr)
            return 1
        enabled = args.enable or (existing["enabled"] if existing and not args.disable else False)
        cfg_mod.save_google_signin_config(client_id, enabled)
        print(f"Saved client_id. enabled={enabled}")
        return 0

    # --enable / --disable only, no --client-id given this run
    if not cfg_mod.set_enabled(args.enable):
        print("No client_id configured yet -- pass --client-id first.", file=sys.stderr)
        return 1
    print(f"Google Sign-In {'enabled' if args.enable else 'disabled'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
