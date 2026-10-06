"""AI Strategy Lab: sandboxed execution of a model-authored signal(df) function.

MISSION strategy-lab-generated-signals Phase 1 -- the execution sandbox. This module's ONLY job
is "run this one function against this one dataframe, safely, and hand back a clean result or a
clean error" -- it does not know about causality checking, degenerate-signal rejection, or the
one-bar shift (that's Phase 2, and lives in strategy_lab.py's execution path since shifting is
about how the returned signal gets CONSUMED, not about running the code itself).

Threat model, stated plainly: the author of the submitted code is untrusted, and this path is
reachable from the internet through the tunnel (anyone who can prompt the model can submit code
here). Defense is layered, not single-point:
  1. AST-based static rejection BEFORE any process is spawned -- import allowlist (pandas, numpy,
     math only), a name/attribute denylist covering the standard Python sandbox-escape surface
     (dunder attribute access, eval/exec/open/__import__ and friends, pandas/numpy I/O methods),
     and a strict "exactly one function named signal(df), nothing else at module level" shape
     check. Cheap, and gives the model an immediate, specific reason to revise.
  2. A hardened exec() namespace inside the sandbox process itself (only pd/np/math and a small
     safe-builtins subset are visible at runtime) -- a second, independent layer in case anything
     ever slips past #1.
  3. Real OS-level isolation via bubblewrap (bwrap), confirmed live on this machine (see the
     mission's own Phase 0 verification): --unshare-net genuinely makes the network unreachable
     (not a Python-level trick), a read-only bind mount genuinely blocks writes, and only the
     specific paths needed (the venv, the base Python install, one throwaway temp dir) are ever
     visible inside the sandbox -- not the whole filesystem, so even a hypothetical bypass of #1
     and #2 has nothing sensitive to read (no .auth.db, no .env.production, nothing under
     OFI_Production at all) and nowhere durable to write to (tmpfs only, wiped on exit).
  4. A wall-clock timeout and a hard cgroup memory limit (systemd-run --user --scope -p
     MemoryMax=... -p MemorySwapMax=0 -- confirmed live that RLIMIT_AS doesn't work for this: see
     _bwrap_cmd's own comment), enforced by the harness around the sandboxed process, not
     requested of it.
This is a defensible, layered mitigation for "LLM-generated code that's usually naive, not
necessarily sophisticated" -- not a claim of bulletproof security against a determined attacker
with kernel exploits. Never widen what's exposed to the sandbox without re-examining this file.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

SANDBOX_TIMEOUT_S = float(os.environ.get("WEBBETA_SIGNAL_SANDBOX_TIMEOUT_S", "20"))
SANDBOX_MEMORY_BYTES = int(os.environ.get("WEBBETA_SIGNAL_SANDBOX_MEMORY_MB", "512")) * 1024 * 1024

ALLOWED_IMPORT_ROOTS = {"pandas", "numpy", "math"}

# Names with no legitimate use in a pandas/numpy signal function, covering the standard Python
# sandbox-escape surface: code execution (eval/exec/compile), module loading (__import__), file
# I/O (open), and introspection that can be chained into a code-execution primitive even without
# a bare import (getattr/globals/locals/vars/dir combined with dunder traversal).
FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "__import__", "open", "input",
    "globals", "locals", "vars", "getattr", "setattr", "delattr", "hasattr", "dir",
    "help", "exit", "quit", "breakpoint", "memoryview",
    "classmethod", "staticmethod", "super", "type", "property", "object",
}

# Any dunder attribute (.__class__, .__globals__, .__subclasses__, .__bases__, .__mro__,
# .__code__, .__builtins__, .__reduce__, ...) is rejected wholesale below, regardless of name --
# this is the single rule that closes off the classic "reach os/subprocess via object
# introspection" escape even with zero import statements. FORBIDDEN_ATTRS below is the second,
# named layer: pandas/numpy I/O and string-expression-evaluation methods that could write to disk
# or reopen a code-injection surface even though the sandbox's own filesystem is read-only anyway.
FORBIDDEN_ATTRS = {
    "to_csv", "to_pickle", "to_parquet", "to_excel", "to_sql", "to_hdf", "to_feather",
    "to_json", "to_clipboard", "to_markdown", "to_latex", "to_stata", "to_gbq", "to_orc",
    "read_csv", "read_json", "read_pickle", "read_excel", "read_sql", "read_parquet",
    "read_html", "read_hdf", "read_feather", "read_table", "read_fwf", "read_clipboard",
    "read_gbq", "read_orc", "read_sas", "read_spss", "read_stata",
    "eval", "query",  # DataFrame.eval/.query run a STRING as an expression -- a second eval path
}


class SignalRejected(ValueError):
    """Static (AST) rejection -- raised before any process is spawned. Message is specific enough
    for the model to revise and retry."""


@dataclass
class SandboxResult:
    status: str  # "ok" | "timeout" | "crashed" | "error"
    signal: Optional[list[bool]] = None
    # Per-row: was the raw value (before fillna(False)) actually NaN? Used only by the leakage
    # guard, which compares this pattern between a full and a truncated run -- see that function's
    # own comment for why boolean-only comparison misses look-ahead on a sparse signal.
    nan_mask: Optional[list[bool]] = None
    error_message: Optional[str] = None


def validate_signal_code(code: str, extra_params: frozenset[str] = frozenset(),
                          fn_name: str = "signal") -> None:
    """Raises SignalRejected with a specific, self-correctable reason. Never runs the code.

    extra_params: for a parameterised sweep signal (MISSION strategy-lab-codegen-primary), the
    exact set of additional argument names signal() must declare beyond df -- e.g.
    {"threshold", "lookback"} when param_grid has those keys. The harness always calls
    signal(df, **one_combo_from_param_grid), so the declared names must match param_grid's keys
    exactly, df first, no defaults/*args/**kwargs. Empty for the plain, unparameterised
    run_generated_backtest path, where signal() must take only df.

    fn_name: MISSION agent-replay Phase 1 -- the required function name and shape check are
    identical for signal(df)->bool-per-row and analyze(df)->arbitrary-JSON-value; only the NAME
    differs (and analyze() never takes extra_params). Parameterised rather than duplicated so the
    two validators can never quietly drift apart on the security-relevant checks below.
    """
    if not isinstance(code, str) or not code.strip():
        raise SignalRejected("code must be a non-empty string.")
    if len(code) > 8_000:
        raise SignalRejected("code is too long (max 8000 characters) -- simplify the function.")

    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise SignalRejected(f"code does not parse: {exc}") from exc

    # Zero or more leading imports (each still checked against ALLOWED_IMPORT_ROOTS below),
    # followed by EXACTLY one function -- signal(df)/analyze(df). Nothing else at module level:
    # no bare assignments, no other functions/classes, no top-level expressions.
    *leading, last = tree.body if tree.body else [None]
    if (not isinstance(last, ast.FunctionDef)
            or any(not isinstance(n, (ast.Import, ast.ImportFrom)) for n in leading)):
        raise SignalRejected(
            "code must contain only (optionally) import statements for pandas/numpy/math, "
            f"followed by EXACTLY one function: '{fn_name}(df):'. No module-level assignments, "
            "no other functions or classes, no top-level expressions."
        )
    fn = last
    if fn.name != fn_name:
        raise SignalRejected(f"the function must be named {fn_name!r}, not {fn.name!r}.")
    if fn.decorator_list:
        raise SignalRejected(f"{fn_name}() may not have decorators.")

    args = fn.args
    actual_names = [a.arg for a in args.args]
    expected_names = ["df"] + sorted(extra_params)
    names_ok = (
        bool(actual_names) and actual_names[0] == "df"
        and set(actual_names[1:]) == set(extra_params)
        and len(actual_names) == len(expected_names)
    )
    if (not names_ok or args.vararg or args.kwarg or args.kwonlyargs or args.defaults
            or args.kw_defaults or args.posonlyargs):
        if extra_params:
            raise SignalRejected(
                f"{fn_name}() must take exactly these arguments: df, {', '.join(sorted(extra_params))} "
                f"-- df first, no defaults, no *args/**kwargs. The names must match param_grid's "
                f"keys exactly."
            )
        raise SignalRejected(f"{fn_name}() must take exactly one argument named 'df', nothing else.")

    # Walk the WHOLE module, not just fn -- the leading top-level imports are siblings of fn, not
    # descendants, and would otherwise never be visited (confirmed live: this was a real bug in an
    # earlier version of this check -- `import os` at module level was silently ACCEPTED because
    # ast.walk(fn) never sees anything outside the function body).
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [node.module] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
            for name in names:
                root = (name or "").split(".")[0]
                if root not in ALLOWED_IMPORT_ROOTS:
                    raise SignalRejected(
                        f"import of {name!r} is not allowed. Only pandas, numpy, and math may be "
                        f"imported (and they're already available as pd/np/math, so you likely "
                        f"don't need to import anything at all)."
                    )
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise SignalRejected(f"use of {node.id!r} is not allowed in a {fn_name} function.")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                raise SignalRejected(
                    f"attribute access to {node.attr!r} is not allowed (no dunder attribute "
                    f"access) -- this is exactly the pattern used to reach outside a sandbox."
                )
            if node.attr in FORBIDDEN_ATTRS:
                raise SignalRejected(f"method .{node.attr}(...) is not allowed in a {fn_name} function.")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node is not fn:
            raise SignalRejected(f"no nested function or class definitions -- exactly one function, {fn_name}(df).")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            raise SignalRejected("global/nonlocal declarations are not allowed.")


# Trusted driver, written by us -- never model-authored. Reads the (already AST-validated) code,
# a parquet data file, and an optional params.json (a JSON list of parameter-combo dicts; absent
# means the single unparameterised signal(df) case) from the sandboxed temp dir. Execs the code
# into a hardened namespace (pd/np/math/session_mask plus a small safe-builtins subset -- NOT the
# real __builtins__), calls signal(df, **combo) once per combo -- ALL combos in this one process,
# not one subprocess spawn per combo, since MAX_GENERATED_GRID_COMBINATIONS-many bwrap+systemd-run
# spawns per sweep would be far too slow -- and prints one JSON line to stdout. A failure in ONE
# combo (e.g. a threshold that causes a divide-by-zero) is caught per-combo and doesn't abort the
# rest of the batch. Every exception, including one raised BY the submitted code, is caught here
# and turned into a clean {"status":"error",...} line -- never a raw traceback, per the mission's
# own "never a stack trace to the browser" requirement.
#
# session_mask(df, name) -- MISSION strategy-lab-codegen-primary: a harness-provided helper so
# session/time-of-day filtering doesn't have to be hand-rolled (or worse, get DST wrong) inside
# model-authored code. Boundaries are ET (Eastern Time) wall-clock, since futures session
# convention is defined in ET, not UTC. DST is computed via the actual US federal rule in force
# since 2007 (2nd Sunday of March 2:00am local -> 1st Sunday of November 2:00am local) using plain
# calendar arithmetic -- no zoneinfo/pytz dependency, correct for any year 2007-present. Session
# boundary CHOICES (which ET hours count as "asia_sunday_reopen" etc) are documented, arguable
# conventions for NQ (Nasdaq-100 futures), not a universal standard -- the same wording is in
# SWEEP_GENERATED_TOOL_DEF's description in strategy_lab.py so the model states which one it used.
_DRIVER_SCRIPT = textwrap.dedent(r"""
    import json, math, sys
    import datetime as _dt
    import numpy as np
    import pandas as pd

    def _second_sunday(year, month):
        d = _dt.date(year, month, 1)
        first_sunday = d + _dt.timedelta(days=(6 - d.weekday()) % 7)
        return first_sunday + _dt.timedelta(days=7)

    def _first_sunday(year, month):
        d = _dt.date(year, month, 1)
        return d + _dt.timedelta(days=(6 - d.weekday()) % 7)

    def _et_offset_hours_array(ts_ns):
        dt_utc = pd.to_datetime(ts_ns, unit="ns", utc=True)
        years = dt_utc.year.to_numpy()
        offsets = np.full(len(dt_utc), -5, dtype=np.int64)
        for y in np.unique(years):
            dst_start = pd.Timestamp(_second_sunday(int(y), 3), tz="UTC") + pd.Timedelta(hours=7)
            dst_end = pd.Timestamp(_first_sunday(int(y), 11), tz="UTC") + pd.Timedelta(hours=6)
            in_year = years == y
            # DatetimeIndex >= Timestamp returns a plain numpy ndarray (not an Index/Series), so no
            # further .to_numpy() here -- confirmed live this AttributeErrors otherwise.
            is_dst = in_year & (dt_utc >= dst_start) & (dt_utc < dst_end)
            offsets[is_dst] = -4
        return offsets

    # Each session: list of (ET weekday [Mon=0..Sun=6], start ET minute-of-day, end ET minute-of-day
    # [exclusive]) windows, each within one ET calendar day (a window spanning ET midnight is split
    # into two entries, e.g. asia_sunday_reopen below).
    _SESSION_WINDOWS = {
        "rth": [(d, 9 * 60 + 30, 16 * 60) for d in range(5)],  # Mon-Fri 09:30-16:00 ET
        "london": [(d, 3 * 60, 8 * 60) for d in range(5)],  # Mon-Fri 03:00-08:00 ET
        "asia_sunday_reopen": [(6, 18 * 60, 24 * 60), (0, 0, 3 * 60)],  # Sun 18:00 ET - Mon 03:00 ET
    }

    def session_mask(df, name):
        if name == "overnight":
            return ~session_mask(df, "rth")
        if name not in _SESSION_WINDOWS:
            raise ValueError(
                "unknown session " + repr(name) + " -- available: rth, overnight, london, asia_sunday_reopen"
            )
        ts_ns = df["bar_start_ts_ns"].to_numpy()
        offsets = _et_offset_hours_array(ts_ns)
        dt_utc = pd.to_datetime(ts_ns, unit="ns", utc=True)
        et_dt = dt_utc + pd.to_timedelta(offsets, unit="h")
        et_weekday = et_dt.weekday
        et_minute = et_dt.hour * 60 + et_dt.minute
        mask = np.zeros(len(df), dtype=bool)
        for wd, start, end in _SESSION_WINDOWS[name]:
            mask |= (et_weekday == wd) & (et_minute >= start) & (et_minute < end)
        return pd.Series(mask, index=df.index)

    def run_one(df_slice, combo, signal_fn):
        try:
            result = signal_fn(df_slice, **combo)
            if not isinstance(result, (pd.Series, np.ndarray, list)):
                raise TypeError(
                    "signal(df) must return a pandas Series (or array-like) of booleans, "
                    "got " + type(result).__name__
                )
            result = pd.Series(result)
            if len(result) != len(df_slice):
                raise ValueError(
                    "signal(df) returned " + str(len(result)) + " values for a " + str(len(df_slice)) +
                    "-row dataframe -- it must return exactly one boolean per row, same order, "
                    "no dropped rows."
                )
            # nan_mask captured BEFORE fillna, alongside the final boolean signal -- an ADDITIONAL
            # (not sufficient alone) causality signal: it catches a raw NaN surviving all the way to
            # the returned Series (e.g. a bare .shift(-1) reference with no comparison), but NOT the
            # far more common case where a comparison operator (.shift(-1) > threshold) converts
            # NaN>threshold to a definite False before it ever reaches us -- for THAT case, the only
            # thing that works is comparing the boolean outcome across MANY truncation lengths (see
            # CAUSALITY_CHECK_FRACTIONS and its own comment in the trusted module).
            nan_mask = result.isna().tolist()
            result = result.fillna(False).astype(bool)
            return {"status": "ok", "signal": result.tolist(), "nan_mask": nan_mask}
        except Exception as e:
            return {"status": "error", "error_message": f"{type(e).__name__}: {e}"}

    def main():
        with open("/sandbox/code.py") as f:
            code = f.read()
        df = pd.read_parquet("/sandbox/data.parquet")
        try:
            with open("/sandbox/params.json") as f:
                param_combos = json.load(f)
        except FileNotFoundError:
            param_combos = [{}]
        try:
            with open("/sandbox/lengths.json") as f:
                lengths = json.load(f)
        except FileNotFoundError:
            lengths = None

        # MISSION agent-replay-pilot-followup / literature-research "fix first": two real, live
        # bugs, confirmed directly in a real agent trace (not hypothetical) -- (1) all()/any()
        # were simply missing from this dict, so `NameError: name 'all' is not defined` on any
        # code using either, no matter how safe or obviously-needed; (2) __import__ was ALSO
        # missing, so ANY explicit `import` statement (even of an allowed module -- pandas, numpy,
        # math are all in ALLOWED_IMPORT_ROOTS and already bound below as pd/np/math) failed at
        # RUNTIME with "ImportError: __import__ not found", even though the AST validator
        # (validate_signal_code) explicitly permits leading import statements. A model that
        # correctly tried `import numpy as np` to work around the missing all()/any() (attempting
        # np.all(...) instead) burned its entire retry budget hitting this SECOND bug on the way
        # to fixing the first. Kept in sync manually with the other copy of this exact block in
        # this file (the analyze(df) driver, MISSION agent-replay Phase 1) -- if one changes, so
        # must the other; they are deliberately NOT shared code because these driver scripts must
        # each stay self-contained text (they run in a separate, isolated subprocess with no
        # access back into this module).
        def _safe_import(name, *args, **kwargs):
            root = name.split(".")[0]
            if root == "pandas":
                return pd
            if root == "numpy":
                return np
            if root == "math":
                return math
            raise ImportError(f"import of {name!r} is not allowed here")

        safe_builtins = {
            "len": len, "range": range, "abs": abs, "min": min, "max": max, "sum": sum,
            "round": round, "sorted": sorted, "enumerate": enumerate, "zip": zip,
            "map": map, "filter": filter, "list": list, "tuple": tuple, "dict": dict,
            "set": set, "bool": bool, "int": int, "float": float, "str": str,
            "all": all, "any": any, "divmod": divmod, "pow": pow, "frozenset": frozenset,
            "reversed": reversed, "format": format, "repr": repr,
            "True": True, "False": False, "None": None, "isinstance": isinstance,
            "ValueError": ValueError, "TypeError": TypeError, "KeyError": KeyError,
            "IndexError": IndexError, "ZeroDivisionError": ZeroDivisionError,
            "__import__": _safe_import,
        }
        ns = {"pd": pd, "np": np, "math": math, "session_mask": session_mask, "__builtins__": safe_builtins}
        exec(compile(code, "<signal>", "exec"), ns)
        signal_fn = ns["signal"]

        if lengths is not None:
            # Causality-batch mode: ONE combo (param_combos[0]), run against MANY prefix lengths of
            # df, all in this single process -- avoids one bwrap+systemd-run spawn per truncation
            # point, which is what makes checking dozens of lengths (for statistical power on a
            # sparse signal) affordable instead of prohibitively slow.
            combo = param_combos[0] if param_combos else {}
            results = [run_one(df.iloc[:L] if L < len(df) else df, combo, signal_fn) for L in lengths]
        else:
            results = [run_one(df, combo, signal_fn) for combo in param_combos]
        print(json.dumps({"status": "ok", "results": results}))

    try:
        main()
    except Exception as e:
        print(json.dumps({"status": "error", "error_message": f"{type(e).__name__}: {e}"}))
        sys.exit(0)
""")


def _bwrap_cmd(sandbox_dir: Path) -> list[str]:
    # MISSION strategy-lab-generated-signals Phase 1: the memory cap is applied via a wrapping
    # `systemd-run --user --scope` cgroup (see run_signal_in_sandbox), NOT via RLIMIT_AS/preexec_fn
    # on this bwrap invocation -- confirmed live that RLIMIT_AS is the wrong tool here: a totally
    # normal pandas/numpy process measured VmSize ~2.6GB (vs. only ~128MB actual RSS), so any
    # RLIMIT_AS cap tight enough to catch a real memory bomb (512MB tested) makes bwrap+python's
    # own startup hang rather than run at all -- RLIMIT_AS counts virtual address space, not real
    # usage, and scientific-Python's virtual footprint is enormous relative to what it actually
    # touches.
    venv_root = sys.prefix
    base_python_root = sys.base_prefix
    cmd = [
        "bwrap",
        "--unshare-net", "--unshare-pid", "--unshare-uts", "--unshare-ipc",
        "--die-with-parent", "--new-session",
        "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib", "/lib64",
        "--symlink", "usr/bin", "/bin",
        "--symlink", "usr/bin", "/sbin",
        "--ro-bind", venv_root, venv_root,
        "--tmpfs", "/tmp",
        "--ro-bind", str(sandbox_dir), "/sandbox",
        "--chdir", "/sandbox",
    ]
    if base_python_root != venv_root:
        cmd += ["--ro-bind", base_python_root, base_python_root]
    cmd += ["--", sys.executable, "/sandbox/_driver.py"]
    return cmd


def _cgroup_memory_wrap(cmd: list[str], memory_bytes: int) -> list[str]:
    # Confirmed live: memory.max alone is NOT enough -- with swap available (30GB on this
    # machine), a process over the cgroup's memory.max just gets slowly swapped instead of killed
    # (a real bomb "succeeded" under a 200MB cap without this). MemorySwapMax=0 removes that escape
    # valve; only then does exceeding MemoryMax produce a genuine SIGKILL (confirmed: exit 137).
    return [
        "systemd-run", "--user", "--scope", "--quiet",
        "-p", f"MemoryMax={memory_bytes}",
        "-p", "MemorySwapMax=0",
        "--",
    ] + cmd


def _write_sandbox_dir(sandbox_dir: Path, code: str, df: pd.DataFrame, param_combos: list[dict],
                        lengths: Optional[list[int]] = None) -> None:
    sandbox_dir.chmod(0o755)
    (sandbox_dir / "code.py").write_text(code)
    (sandbox_dir / "_driver.py").write_text(_DRIVER_SCRIPT)
    (sandbox_dir / "params.json").write_text(json.dumps(param_combos))
    if lengths is not None:
        (sandbox_dir / "lengths.json").write_text(json.dumps(lengths))
    df.to_parquet(sandbox_dir / "data.parquet")


def _run_driver(sandbox_dir: Path, timeout_s: float) -> tuple[Optional[dict], Optional[SandboxResult]]:
    """Runs the sandboxed driver and returns (payload, None) on a clean run, or (None, an
    already-classified SandboxResult) for a timeout/crash/no-output failure the caller should
    return directly without inspecting further."""
    cmd = _cgroup_memory_wrap(_bwrap_cmd(sandbox_dir), SANDBOX_MEMORY_BYTES)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return None, SandboxResult(status="timeout", error_message=f"exceeded {timeout_s:.0f}s wall-clock limit")

    if proc.returncode != 0:
        # bwrap itself failed, or the interpreter was killed (OOM/rlimit/signal) before the
        # driver's own try/except ever got a chance to print clean JSON -- never surface
        # proc.stderr to the browser, it can contain raw tracebacks/paths.
        return None, SandboxResult(status="crashed", error_message=(
            f"the sandboxed process exited abnormally (code {proc.returncode}) -- likely hit "
            f"the {SANDBOX_MEMORY_BYTES // (1024*1024)}MB memory limit or was killed."
        ))

    try:
        last_line = proc.stdout.strip().splitlines()[-1]
        payload = json.loads(last_line)
    except (IndexError, json.JSONDecodeError):
        return None, SandboxResult(status="crashed", error_message="the sandbox produced no valid result.")
    return payload, None


def run_signal_in_sandbox(code: str, df: pd.DataFrame, params: Optional[dict] = None,
                           timeout_s: float = SANDBOX_TIMEOUT_S) -> SandboxResult:
    """Validates (raises SignalRejected, no process spawned, if invalid), then runs
    signal(df, **params) exactly once inside the bwrap sandbox and returns a SandboxResult.
    params=None/{} for the plain signal(df) case. Never raises for a runtime failure
    (timeout/crash/error) -- those come back as a SandboxResult with a clean message."""
    params = params or {}
    validate_signal_code(code, extra_params=frozenset(params.keys()))  # raises SignalRejected -- let it propagate

    with tempfile.TemporaryDirectory(prefix="webbeta_signal_sandbox_") as tmpdir:
        sandbox_dir = Path(tmpdir)
        _write_sandbox_dir(sandbox_dir, code, df, [params])
        payload, failure = _run_driver(sandbox_dir, timeout_s)
        if failure is not None:
            return failure

        if payload.get("status") != "ok":
            return SandboxResult(status="error", error_message=payload.get("error_message", "unknown error"))
        one = payload["results"][0]
        if one.get("status") == "ok":
            return SandboxResult(status="ok", signal=one["signal"], nan_mask=one.get("nan_mask"))
        return SandboxResult(status="error", error_message=one.get("error_message", "unknown error"))


@dataclass
class GridSandboxResult:
    status: str  # "ok" | "timeout" | "crashed" | "error" -- top-level (whole batch failed to run)
    combo_results: Optional[list[SandboxResult]] = None  # aligned 1:1 with the submitted param_combos
    error_message: Optional[str] = None


def run_param_grid_in_sandbox(code: str, df: pd.DataFrame, param_combos: list[dict],
                               timeout_s: float = SANDBOX_TIMEOUT_S) -> GridSandboxResult:
    """Runs signal(df, **combo) once per combo in param_combos, ALL inside ONE sandboxed subprocess
    invocation -- the trusted driver loops internally (see _DRIVER_SCRIPT), not one bwrap+
    systemd-run spawn per combo, since that would be far too slow for a grid of any real size. A
    per-combo runtime failure (e.g. a threshold that causes a divide-by-zero) doesn't abort the
    batch -- it's recorded as that combo's own SandboxResult so one bad candidate doesn't lose
    every other one."""
    if not param_combos:
        raise SignalRejected("param_grid produced zero combinations.")
    validate_signal_code(code, extra_params=frozenset(param_combos[0].keys()))

    with tempfile.TemporaryDirectory(prefix="webbeta_signal_sandbox_") as tmpdir:
        sandbox_dir = Path(tmpdir)
        _write_sandbox_dir(sandbox_dir, code, df, param_combos)
        payload, failure = _run_driver(sandbox_dir, timeout_s)
        if failure is not None:
            return GridSandboxResult(status=failure.status, error_message=failure.error_message)

        if payload.get("status") != "ok":
            return GridSandboxResult(status="error", error_message=payload.get("error_message", "unknown error"))

        combo_results = [
            SandboxResult(status="ok", signal=r["signal"], nan_mask=r.get("nan_mask")) if r.get("status") == "ok"
            else SandboxResult(status="error", error_message=r.get("error_message", "unknown error"))
            for r in payload["results"]
        ]
        return GridSandboxResult(status="ok", combo_results=combo_results)


@dataclass
class CausalityBatchResult:
    status: str  # "ok" | "timeout" | "crashed" | "error" -- top-level (whole batch failed to run)
    per_length: Optional[list[SandboxResult]] = None  # aligned 1:1 with the submitted lengths
    error_message: Optional[str] = None


def run_causality_batch_in_sandbox(code: str, df: pd.DataFrame, params: dict, lengths: list[int],
                                    timeout_s: float = SANDBOX_TIMEOUT_S) -> CausalityBatchResult:
    """Runs signal(df.iloc[:L], **params) once per length in `lengths`, ALL inside ONE sandboxed
    subprocess -- the same batching principle as run_param_grid_in_sandbox, applied to truncation
    LENGTH instead of parameter VALUE, so the causality guard can afford to check many more
    truncation points than one-spawn-per-point would allow (see CAUSALITY_CHECK_FRACTIONS's own
    comment for why checking many points matters for a sparse signal)."""
    validate_signal_code(code, extra_params=frozenset(params.keys()))

    with tempfile.TemporaryDirectory(prefix="webbeta_signal_sandbox_") as tmpdir:
        sandbox_dir = Path(tmpdir)
        _write_sandbox_dir(sandbox_dir, code, df, [params], lengths=lengths)
        payload, failure = _run_driver(sandbox_dir, timeout_s)
        if failure is not None:
            return CausalityBatchResult(status=failure.status, error_message=failure.error_message)

        if payload.get("status") != "ok":
            return CausalityBatchResult(status="error", error_message=payload.get("error_message", "unknown error"))

        per_length = [
            SandboxResult(status="ok", signal=r["signal"], nan_mask=r.get("nan_mask")) if r.get("status") == "ok"
            else SandboxResult(status="error", error_message=r.get("error_message", "unknown error"))
            for r in payload["results"]
        ]
        return CausalityBatchResult(status="ok", per_length=per_length)


# =============================================================================================
# MISSION strategy-lab-generated-signals Phase 2: the leakage guard. This is the most important
# check in the whole feature -- an LLM asked to "compute a Fibonacci level" will, without
# noticing, reach for exactly the patterns that leak the future: .shift(-1), a mean/std computed
# over the whole passed-in series instead of a rolling window, .iloc[-1] as a reference price. A
# genuinely causal function's output for row i cannot depend on anything after row i -- which
# means it MUST produce identical output for row i whether the dataframe handed to it ends at row
# i, row i+1000, or the full series. That invariant is exactly what gets tested below: run
# signal() on the full series, run it again on several truncated prefixes, and compare the
# overlapping region. Any disagreement is definitional proof of look-ahead, not a heuristic.
#
# The ONE-BAR SHIFT ("a signal computed on bar t enters at bar t+1's open") is intentionally NOT
# implemented here -- it already lives in strategy_lab._simulate_trades (entry_idx = i + 1), which
# every signal source shares, generated or condition-based. Shifting is about how a signal gets
# CONSUMED at execution time, not about validating the signal itself.
# =============================================================================================

# MISSION strategy-lab-codegen-primary: widened from 5 points to 101. Verified empirically before
# shipping (see the mission's own verification script) that comparing only the final BOOLEAN at a
# handful of truncation points has LOW power against a SPARSE signal: a comparison operator (e.g.
# `.shift(-1) > threshold`) converts a boundary NaN straight to False, indistinguishable from a
# real value that legitimately doesn't cross the threshold -- so whether a leak is even visible at
# a given boundary row is a coin flip weighted by the signal's firing rate, not a certainty. At a
# realistic 3%-firing threshold, 5 points caught a planted .shift(-1) only ~14% of the time
# (measured directly), and even 19 points only ~44% (0.97^19 miss odds -- an earlier estimate of
# "under 2%" in this comment was arithmetic error, corrected after direct measurement kept failing
# the mission's own Phase 5 gate). 101 points -- still batched into ONE sandboxed subprocess (see
# run_causality_batch_in_sandbox, one spawn regardless of point count) -- brings the miss rate for
# that same 3%-firing signal down to ~4.7% (0.97^101); for a more typically-selective signal (10%+
# firing, which most real entry conditions are, since 3% is unusually rare) the miss rate is
# effectively zero (0.9^101 =~ 2e-5). This is STILL a probabilistic check, not a mathematical
# guarantee, for the sparsest signals -- stated honestly rather than claimed as absolute, since a
# black-box behavioral test fundamentally cannot rule out every possible leak with finitely many
# sample points. MAX_SIGNAL_FRACTION below still rejects 0%-firing and near-100%-firing signals as
# degenerate on their own separate grounds.
CAUSALITY_CHECK_FRACTIONS = tuple(round(0.02 + i * (0.98 - 0.02) / 100, 4) for i in range(101))
MAX_SIGNAL_FRACTION = 0.30


@dataclass
class SignalCheckResult:
    status: str  # "ok" | "rejected" | "timeout" | "crashed" | "error" | "non_causal" | "degenerate"
    signal: Optional[list[bool]] = None
    error_message: Optional[str] = None


def run_signal_with_leakage_guard(code: str, df: pd.DataFrame, params: Optional[dict] = None,
                                   timeout_s: float = SANDBOX_TIMEOUT_S) -> SignalCheckResult:
    """The full Phase 1 + Phase 2 pipeline: static rejection, sandboxed execution, the causality
    check (several truncation points), and the degenerate-signal check -- in that order, cheapest
    first. params=None/{} for the plain signal(df) case; a non-empty dict runs signal(df, **params)
    throughout (used both directly by run_generated_backtest and as the representative-combo check
    inside run_param_grid_with_leakage_guard below). Never raises; every failure mode comes back as
    a SignalCheckResult with a specific, honest status and message."""
    params = params or {}
    try:
        validate_signal_code(code, extra_params=frozenset(params.keys()))
    except SignalRejected as exc:
        return SignalCheckResult(status="rejected", error_message=str(exc))

    full = run_signal_in_sandbox(code, df, params=params, timeout_s=timeout_s)
    if full.status != "ok":
        return SignalCheckResult(status=full.status, error_message=full.error_message)
    full_signal = full.signal
    n = len(df)

    lengths = sorted({k for k in (int(n * frac) for frac in CAUSALITY_CHECK_FRACTIONS) if 10 <= k < n})
    # All 19 truncation lengths run inside ONE sandboxed subprocess (see
    # run_causality_batch_in_sandbox) -- not one spawn per length, which is what makes checking this
    # many points affordable.
    batch = run_causality_batch_in_sandbox(code, df, params, lengths, timeout_s=timeout_s)
    if batch.status != "ok":
        # A genuinely causal function works the same way regardless of how much MORE data exists
        # beyond the row it's looking at -- if the batch itself failed (crash/timeout on some
        # truncation), that's still evidence something depends on data only present in a longer
        # series, so this counts as a causality failure too, not a separate "it just broke" case.
        return SignalCheckResult(status="non_causal", error_message=(
            f"signal(df) could not be verified across shorter histories: {batch.error_message or batch.status}. "
            f"A causal function must work identically regardless of how much data comes after the "
            f"row it's evaluating -- it may only use rows up to and including the current one."
        ))

    for k, truncated in zip(lengths, batch.per_length):
        if truncated.status != "ok":
            return SignalCheckResult(status="non_causal", error_message=(
                f"signal(df) behaves differently on a shorter history (first {k} of {n} bars) than "
                f"on the full series: {truncated.error_message or truncated.status}. A causal "
                f"function must work identically regardless of how much data comes after the row "
                f"it's evaluating -- it may only use rows up to and including the current one."
            ))
        mismatches = [i for i in range(k) if bool(full_signal[i]) != bool(truncated.signal[i])]
        if mismatches:
            return SignalCheckResult(status="non_causal", error_message=(
                f"signal(df) is NOT causal: run on the first {k} of {n} bars and compared against "
                f"the full-series computation for those same bars, {len(mismatches)} of {k} disagree "
                f"(first disagreement at row {mismatches[0]}). This means the function uses data "
                f"from AFTER the row it's predicting -- common causes: .shift(-1) or a negative "
                f"shift, a mean/std/min/max computed over the WHOLE series instead of a rolling "
                f"window, or referencing .iloc[-1] as if it were 'now'. Fix the function so row i's "
                f"value depends only on rows <= i, then resubmit."
            ))

        # An ADDITIONAL, not sufficient-alone, signal -- catches a raw NaN that survives all the way
        # to the returned Series (e.g. a bare .shift(-1) with no comparison wrapped around it). It
        # will NOT catch the more common `.shift(-1) > threshold` pattern, since the comparison
        # itself converts a boundary NaN straight to False before the function ever returns -- that
        # case relies on the boolean-mismatch check above, run over enough truncation points (see
        # CAUSALITY_CHECK_FRACTIONS's own comment) that a false negative becomes unlikely rather than
        # impossible.
        if full.nan_mask is not None and truncated.nan_mask is not None:
            nan_mismatches = [i for i in range(k) if bool(full.nan_mask[i]) != bool(truncated.nan_mask[i])]
            if nan_mismatches:
                return SignalCheckResult(status="non_causal", error_message=(
                    f"signal(df) is NOT causal: on the first {k} of {n} bars, {len(nan_mismatches)} "
                    f"row(s) came out as missing/NaN internally that aren't missing on the full "
                    f"series (first at row {nan_mismatches[0]}) -- the function needs data that "
                    f"isn't available yet at that row when only a shorter history is given, which is "
                    f"exactly what a causal function must never require. Common causes: .shift(-1) "
                    f"or a negative shift, a mean/std/min/max computed over the WHOLE series instead "
                    f"of a rolling window, or referencing .iloc[-1] as if it were 'now'. Fix the "
                    f"function so row i's value depends only on rows <= i."
                ))

    fraction_true = (sum(full_signal) / len(full_signal)) if full_signal else 0.0
    if fraction_true == 0.0:
        return SignalCheckResult(status="degenerate",
                                  error_message="signal(df) is never True -- it would never generate a trade. Loosen the condition.")
    if fraction_true == 1.0:
        return SignalCheckResult(status="degenerate",
                                  error_message="signal(df) is always True -- that isn't a selective entry signal, it fires on every bar. Add a real condition.")
    if fraction_true > MAX_SIGNAL_FRACTION:
        return SignalCheckResult(status="degenerate", error_message=(
            f"signal(df) fires on {fraction_true:.0%} of bars, above the {MAX_SIGNAL_FRACTION:.0%} "
            f"cap this tool enforces -- too broad to be a meaningful, selective entry signal. Tighten it."
        ))

    return SignalCheckResult(status="ok", signal=full_signal)


# =============================================================================================
# MISSION strategy-lab-codegen-primary: causality is a property of the CODE's logic (does it read
# rows after the one it's predicting), not of which numeric parameter values are plugged into it --
# a comparison like `df[col] > threshold` doesn't become look-ahead for a different threshold. So
# the expensive multi-truncation causality check (5 extra sandbox spawns) runs ONCE, against one
# representative combo, rather than once per combo in the grid -- re-running it per combo would
# multiply the sandbox cost by the grid size for no additional safety. If that representative check
# passes, every combo in the grid is executed once each, batched into a single sandboxed subprocess
# (run_param_grid_in_sandbox above). Per-combo degeneracy (a threshold so loose or tight the signal
# never/always fires) is deliberately NOT checked here -- that's evaluated per-combo by
# strategy_lab.py's run_generated_sweep, since a grid can legitimately have some degenerate combos
# alongside good ones; this guard only proves the CODE itself doesn't leak the future.
# =============================================================================================

@dataclass
class GridSignalCheckResult:
    status: str  # "ok" | "rejected" | "timeout" | "crashed" | "error" | "non_causal"
    combo_signals: Optional[list[Optional[list[bool]]]] = None  # None entry = that combo failed
    combo_errors: Optional[list[Optional[str]]] = None  # parallel to combo_signals
    error_message: Optional[str] = None


def run_param_grid_with_leakage_guard(code: str, df: pd.DataFrame, param_combos: list[dict],
                                       timeout_s: float = SANDBOX_TIMEOUT_S) -> GridSignalCheckResult:
    """Runs the causality guard once (against param_combos[0] as a representative sample), then --
    only if that passes -- executes the full grid in one batched sandbox call. Never raises."""
    if not param_combos:
        return GridSignalCheckResult(status="error", error_message="param_grid produced zero combinations.")

    representative = run_signal_with_leakage_guard(code, df, params=param_combos[0], timeout_s=timeout_s)
    if representative.status != "ok":
        return GridSignalCheckResult(status=representative.status, error_message=representative.error_message)

    grid = run_param_grid_in_sandbox(code, df, param_combos, timeout_s=timeout_s)
    if grid.status != "ok":
        return GridSignalCheckResult(status=grid.status, error_message=grid.error_message)

    combo_signals, combo_errors = [], []
    for r in grid.combo_results:
        if r.status == "ok":
            combo_signals.append(r.signal)
            combo_errors.append(None)
        else:
            combo_signals.append(None)
            combo_errors.append(r.error_message)
    return GridSignalCheckResult(status="ok", combo_signals=combo_signals, combo_errors=combo_errors)


# =============================================================================================
# MISSION agent-replay Phase 1: the analyze(df) contract -- a free-form counterpart to signal(df)
# for an agent that wants to explore truncated history (compute a rolling stat, check a
# correlation, summarize recent bars) rather than produce a boolean entry condition. Same layered
# defenses as everywhere else in this file (AST allowlist/denylist, bwrap, cgroup memory cap,
# timeout) -- ONLY the function name/shape and the return-value handling differ. df passed in here
# MUST already be truncated at the caller's cursor; this module has no concept of a cursor, same
# as run_signal_in_sandbox has no concept of causality -- that's the CALLER's responsibility.
# =============================================================================================

_ANALYSIS_RESULT_MAX_CHARS = 4_000  # keep any single analyze() result small enough not to blow context

_ANALYSIS_DRIVER_SCRIPT = textwrap.dedent(r"""
    import json, math, sys
    import numpy as np
    import pandas as pd

    def _to_jsonable(x, depth=0):
        if depth > 4:
            raise TypeError("nested too deeply to serialize (max depth 4)")
        if isinstance(x, (bool, int, float, str)) or x is None:
            return x
        if isinstance(x, np.integer):
            return int(x)
        if isinstance(x, np.floating):
            return float(x)
        if isinstance(x, np.bool_):
            return bool(x)
        if isinstance(x, pd.Series):
            return [_to_jsonable(v, depth + 1) for v in x.tolist()]
        if isinstance(x, np.ndarray):
            return [_to_jsonable(v, depth + 1) for v in x.tolist()]
        if isinstance(x, dict):
            return {str(k): _to_jsonable(v, depth + 1) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [_to_jsonable(v, depth + 1) for v in x]
        raise TypeError("analyze(df) returned a non-JSON-serializable value of type " + type(x).__name__)

    def main():
        with open("/sandbox/code.py") as f:
            code = f.read()
        df = pd.read_parquet("/sandbox/data.parquet")

        # Kept in sync manually with the other copy of this exact block in this file (the
        # signal(df) driver, above) -- see that copy's comment for why these two fixes exist and
        # why this isn't refactored into shared code (each driver script must stay self-contained
        # text; it runs in a separate, isolated subprocess with no access back into this module).
        def _safe_import(name, *args, **kwargs):
            root = name.split(".")[0]
            if root == "pandas":
                return pd
            if root == "numpy":
                return np
            if root == "math":
                return math
            raise ImportError(f"import of {name!r} is not allowed here")

        safe_builtins = {
            "len": len, "range": range, "abs": abs, "min": min, "max": max, "sum": sum,
            "round": round, "sorted": sorted, "enumerate": enumerate, "zip": zip,
            "map": map, "filter": filter, "list": list, "tuple": tuple, "dict": dict,
            "set": set, "bool": bool, "int": int, "float": float, "str": str,
            "all": all, "any": any, "divmod": divmod, "pow": pow, "frozenset": frozenset,
            "reversed": reversed, "format": format, "repr": repr,
            "True": True, "False": False, "None": None, "isinstance": isinstance,
            "ValueError": ValueError, "TypeError": TypeError, "KeyError": KeyError,
            "IndexError": IndexError, "ZeroDivisionError": ZeroDivisionError,
            "__import__": _safe_import,
        }
        ns = {"pd": pd, "np": np, "math": math, "__builtins__": safe_builtins}
        exec(compile(code, "<analyze>", "exec"), ns)
        analyze_fn = ns["analyze"]
        result = analyze_fn(df)
        jsonable = _to_jsonable(result)
        print(json.dumps({"status": "ok", "result": jsonable}))

    try:
        main()
    except Exception as e:
        print(json.dumps({"status": "error", "error_message": f"{type(e).__name__}: {e}"}))
        sys.exit(0)
""")


def _write_analysis_sandbox_dir(sandbox_dir: Path, code: str, df: pd.DataFrame) -> None:
    sandbox_dir.chmod(0o755)
    (sandbox_dir / "code.py").write_text(code)
    (sandbox_dir / "_driver.py").write_text(_ANALYSIS_DRIVER_SCRIPT)
    df.to_parquet(sandbox_dir / "data.parquet")


@dataclass
class AnalysisResult:
    status: str  # "ok" | "rejected" | "timeout" | "crashed" | "error"
    result: Optional[object] = None
    error_message: Optional[str] = None


def run_analysis_in_sandbox(code: str, df: pd.DataFrame, timeout_s: float = SANDBOX_TIMEOUT_S) -> AnalysisResult:
    """The analyze(df)-shaped counterpart to run_signal_in_sandbox. df MUST already be truncated
    at the harness's cursor by the CALLER -- this function has no concept of a cursor, exactly
    like run_signal_in_sandbox has no concept of causality. Never raises for a runtime failure."""
    try:
        validate_signal_code(code, fn_name="analyze")
    except SignalRejected as exc:
        return AnalysisResult(status="rejected", error_message=str(exc))

    with tempfile.TemporaryDirectory(prefix="webbeta_analysis_sandbox_") as tmpdir:
        sandbox_dir = Path(tmpdir)
        _write_analysis_sandbox_dir(sandbox_dir, code, df)
        payload, failure = _run_driver(sandbox_dir, timeout_s)
        if failure is not None:
            return AnalysisResult(status=failure.status, error_message=failure.error_message)
        if payload.get("status") != "ok":
            return AnalysisResult(status="error", error_message=payload.get("error_message", "unknown error"))

        result = payload.get("result")
        serialized = json.dumps(result)
        if len(serialized) > _ANALYSIS_RESULT_MAX_CHARS:
            return AnalysisResult(status="error", error_message=(
                f"analyze(df) returned {len(serialized)} chars of JSON, over the "
                f"{_ANALYSIS_RESULT_MAX_CHARS}-char limit -- return a smaller summary (a few "
                f"numbers or short strings), not raw per-row data."
            ))
        return AnalysisResult(status="ok", result=result)
