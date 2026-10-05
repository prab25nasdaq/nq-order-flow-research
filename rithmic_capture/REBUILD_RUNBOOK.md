# SampleMD Rebuild Runbook

Written 2026-08-16 after the `wd_work` drive was accidentally reformatted, destroying the
previously-deployed `SampleMD` binary and its exact source. This directory (a git repo) is now
the canonical, versioned copy of everything needed to rebuild it — source, vendor SDK, and the
exact build command. If the compiled binary is ever lost again but this repo survives, rebuilding
is the two commands in "Build" below, not hours of forensic reconstruction.

## Canonical locations (as of this rebuild)

- **Source + this runbook:** `/home/prabh/OFI_Production/rithmic_build/` (git repo, this directory)
- **Compiled binary (what `rithmic_scheduler.py` actually runs):**
  `/home/prabh/OFI_Production/rithmic_build/13.6.0.0/samples/SampleMD`
- **Vendor SDK working copy:** `/home/prabh/vendor/rithmic/13.6.0.0/` (also mirrored into
  `vendor_sdk/` in this repo, so this repo alone is sufficient to rebuild even if `~/vendor` is
  ever lost too)
- **rithmic_scheduler.py's `SAMPLES_DIR`/`BINARY`/`LIB_DIR` constants** point at the first two
  paths above. **Do not point them back at `/mnt/wd_work` or any other removable/external drive.**

## What's in this repo

- `13.6.0.0/samples/SampleMD.cpp` — the patched source (see "Fixes applied" below)
- `vendor_sdk/include/RApiPlus.h` — Rithmic API header
- `vendor_sdk/linux-gnu-4.18-x86_64/lib/*.a` — prebuilt static libraries for this exact deploy
  target (RedHat Enterprise Linux 8.5-equivalent, 64-bit; matches this machine)
- `vendor_sdk/etc/rithmic_ssl_cert_auth_params` — SSL cert bundle SampleMD needs at runtime
  (generic vendor-provided CA bundle, not account-specific)

## Build

```bash
cd /home/prabh/OFI_Production/rithmic_build/13.6.0.0/samples
g++ -O3 -DLINUX -D_REENTRANT -Wall -Wno-sign-compare -Wno-write-strings -Wpointer-arith -Winline \
    -Wno-deprecated -fno-strict-aliasing \
    -I/home/prabh/vendor/rithmic/13.6.0.0/include \
    -o SampleMD SampleMD.cpp \
    -L/home/prabh/vendor/rithmic/13.6.0.0/linux-gnu-4.18-x86_64/lib \
    -lRApiPlus-optimize -lOmneStreamEngine-optimize -lOmneChannel-optimize -lOmneEngine-optimize \
    -l_api-optimize -l_apipoll-stubs-optimize -l_kit-optimize -lssl -lcrypto \
    -L/usr/lib64 -lz -lpthread -lrt -ldl
```

This is the vendor-documented "64-bit linux (4.18 kernel)" command from the copyright-header
comment at the top of the original `SampleMD.cpp` (present in every source variant, vendor and
custom alike) — unmodified except for pointing `-I`/`-L` at wherever the SDK actually lives. If
`~/vendor/rithmic` is ever gone too, restore it from this repo's own `vendor_sdk/` first:

```bash
mkdir -p /home/prabh/vendor/rithmic/13.6.0.0/{include,linux-gnu-4.18-x86_64/lib,etc}
cp vendor_sdk/include/RApiPlus.h                       /home/prabh/vendor/rithmic/13.6.0.0/include/
cp vendor_sdk/linux-gnu-4.18-x86_64/lib/*.a             /home/prabh/vendor/rithmic/13.6.0.0/linux-gnu-4.18-x86_64/lib/
cp vendor_sdk/etc/rithmic_ssl_cert_auth_params          /home/prabh/vendor/rithmic/13.6.0.0/etc/
```

After building, restart the service so it picks up the new binary (it's the same path every time,
so a plain restart is enough — no scheduler edit needed unless the binary path itself changes):

```bash
systemctl --user restart ofi-rithmic.service
journalctl --user -u ofi-rithmic.service -f   # confirm real [XGOFI STATS] lines, no crash-loop
```

## Fixes applied to this source (relative to the last saved dev snapshot,
`SampleMD_xgofi_capture_production_final_candidate_v3.cpp`, 2026-03-02)

The deployed June-2026 binary (lost in the format, confirmed via a June 14 diagnostic report that
inspected its actual source) was one revision ahead of this saved snapshot. Two gaps were found by
the binary failing outright, one was found by diffing output against intact pre-incident data:

1. **SSL cert path** — hardcoded to the now-gone `wd_work` path
   (`fake_envp[6] = "MML_SSL_CLNT_AUTH_FILE=/mnt/wd_work/.../etc/rithmic_ssl_cert_auth_params"`).
   Not fixed in the source itself (deliberately left as-is) — instead, that exact file was restored
   at that exact path on `wd_work` from the vendor's own copy. **This means the `etc/` file at that
   wd_work path is a real runtime dependency of this exact binary — don't delete it.** A cleaner
   future fix would be reading this from `XGOFI_OUTPUT_DIR`-style env var like the other two fixes
   below; not done yet because the binary already works and touching more hardcoded strings than
   necessary adds risk without a live problem to justify it.

2. **Data output directory** (`ensure_capture_dirs_and_files()`) — hardcoded to a *different*
   now-gone `wd_work` path, completely ignoring the `XGOFI_OUTPUT_DIR` env var that
   `rithmic_scheduler.py` already sets (and that this same file already reads for
   `XGOFI_VERBOSE_DUMPS`/`XGOFI_FLUSH_EVERY_N`/`XGOFI_TRUNCATE_ON_START`/`XGOFI_FAIL_IF_EXISTS`).
   Fixed: `base_root` now reads `XGOFI_OUTPUT_DIR` if set, falling back to the old hardcoded string
   only if it isn't.

3. **`session_meta.json` placeholder values** — `system`/`gateway` were hardcoded to the literal
   string `"UNVERIFIED_RUNTIME"` instead of the real connection identifiers, found by diffing a
   fresh capture against an intact 2026-08-13 (pre-incident) sample field-by-field. Fixed: hardcoded
   to the same real, fixed values already used elsewhere in this file for the actual connection
   (`"rithmic_paper_prod_domain"` / `"login_agent_tp_paperc"`) — these are static for this
   deployment, not something that varies per run.

4. **Graceful shutdown (self-pipe)** — this snapshot used a plain `fgetc(stdin)` to wait for
   shutdown, with no path for a signal to unblock it. `rithmic_scheduler.py`'s own `stop_feed()`
   already assumed a self-pipe mechanism existed (see its comment referencing "SIGTERM self-pipe"),
   matching what the actual June-built binary did per the June 14 report's source inspection.
   Without this, every `systemctl restart` was hitting the full 15s SIGTERM-timeout-then-SIGKILL
   path, never running `flush_all_outputs()`. Implemented: `g_shutdown_pipe[2]`, written from the
   signal handler (`write()`, async-signal-safe), `select()`s on both the pipe and stdin before the
   final flush/cleanup. Verified live via a real `systemctl restart` (see incident report): clean
   shutdown in ~360ms, no SIGKILL fallback.

## Data-parity verification (2026-08-16)

Field-by-field diff of a live sample against an intact pre-incident sample
(`2026-08-13/NQU6/*.ndjson`, captured by the original, now-lost binary): identical file set,
directory layout, and naming; identical JSON keys, value types, and `schema` version strings
across all 5 event streams (`trades`, `bbo`, `depth`, `bid_quote_updates`, `ask_quote_updates`)
over a 200-line sample each; identical `capture_status.json` structure; identical nanosecond
timestamp format; identical enum label sets (`update_type_label`, `callback_type_label`). The one
real divergence found (`session_meta.json` placeholders) is fix #3 above.

## If you need to know which binary is actually running

`rithmic_scheduler.py` logs its `BINARY` path on every start (`Binary: ...`) — check
`journalctl --user -u ofi-rithmic.service | grep Binary` or the current
`pipeline_health/pipeline_health_status.json`.
