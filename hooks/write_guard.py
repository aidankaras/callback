#!/usr/bin/env python3
"""PreToolUse guard: an assistant may not write the fact store or a sealed document.

Claude Code runs this before every Write, Edit, MultiEdit, NotebookEdit and
Bash call, passing the call as JSON on stdin. Exit 2 blocks the call and
shows stderr to the model; exit 0 lets it through.

What is protected, under `$CALLBACK_HOME` (default `~/.callback`):

- `facts.yaml`, the fact store. Facts enter it only through
  `callback facts add`, which marks them unconfirmed, and become usable only
  through `callback facts confirm`, which only the candidate can answer. A
  tool that could write the store directly could skip both.
- anything under an `outputs/<job>/final/` or `released/` directory.
- key, ledger and database files.
- and, wherever the plugin lives, this guard and its `hooks.json`.

Everything else is allowed, including files outside the data home: the hook
runs on every write in the session, and confining all writes to the data
home would stop the assistant editing anything else at all.

Every path is resolved before comparison, so `..` and symlinks cannot reach a
protected file. For Bash, each path-like token is resolved against the
session's working directory and against every `cd` target in the command,
and a command naming a protected path is refused whether it reads or writes
it: telling the two apart in an arbitrary shell command is not reliable
(`cat x | tee x`, `sed -n` against `sed -i`), and nothing needs the raw store,
which `callback facts list` reads. `callback facts confirm` is refused
outright. Any payload this script cannot read is denied: a guard that fails
open would imply a guarantee it does not give.

Bash guarding is defence in depth, not containment. A shell is a general
tool, and a command that builds a path at run time -- from a variable other
than `CALLBACK_HOME`, from `find`, from an encoded string -- is not caught.
It stops an assistant *accidentally* writing a protected file, which is the
failure that matters in normal use; it does not stop one determined to.

Standard library only, so it starts quickly on every tool call and does not
depend on the package being installed.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
PROTECTED_NAMES = {"facts.yaml", "ledger.log", ".key", "callback.db", "callback.db-wal"}
SEALED_DIRS = {"final", "released"}
GUARD_FILES = {Path(__file__).resolve(), Path(__file__).resolve().with_name("hooks.json")}

_CONFIRM = re.compile(r"\bcallback\s+facts\s+confirm\b")
_CD = re.compile(r"(?:^|[;&|(]\s*)cd\s+(\S+)")


def deny(reason: str) -> None:
    print(f"blocked by callback write guard: {reason}", file=sys.stderr)
    raise SystemExit(2)


def data_home() -> Path:
    raw = os.environ.get("CALLBACK_HOME")
    return (Path(raw) if raw else Path.home() / ".callback").expanduser().resolve()


def _expand(raw: str) -> str:
    home = str(data_home())
    for var in ("${CALLBACK_HOME}", "$CALLBACK_HOME"):
        raw = raw.replace(var, home)
    return str(Path(raw).expanduser())


def protected(path: Path) -> str | None:
    """Why `path` may not be written, or `None` if it may."""
    target = path.resolve()
    if target in GUARD_FILES:
        return "the write guard is not writable by a tool"
    home = data_home()
    if target != home and home not in target.parents:
        return None
    inside = target.relative_to(home).parts
    if inside == ("facts.yaml",):
        return (
            "the fact store changes only through `callback facts add` and `callback facts confirm`"
        )
    if target.name in PROTECTED_NAMES:
        return f"{target.name} is written only by callback itself"
    if SEALED_DIRS & set(inside):
        return "a finalised document is never rewritten"
    return None


def check_bash(command: str, cwd: Path) -> None:
    if _CONFIRM.search(command):
        deny("`callback facts confirm` must be typed by the candidate in their own terminal")
    expanded = _expand(command)
    bases = [cwd, *((cwd / _expand(d)) for d in _CD.findall(expanded))]
    try:
        tokens = shlex.split(expanded, comments=False, posix=True)
    except ValueError:
        tokens = expanded.split()
    for token in tokens:
        # A token may carry a path inside quotes or code, e.g. open('<path>','w').
        for candidate in re.findall(r"[\w~./@+-]*[/.][\w~./@+-]*", token):
            for base in bases:
                reason = protected(base / candidate)
                if reason:
                    deny(
                        f"command names a protected path ({candidate}): {reason}; "
                        "read facts with `callback facts list`"
                    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        tool = str(payload["tool_name"])
        tool_input = payload.get("tool_input") or {}
        cwd = Path(str(payload.get("cwd") or Path.cwd()))
    except Exception as exc:
        deny(f"unreadable hook payload ({type(exc).__name__})")
        return 2
    if tool in WRITE_TOOLS:
        raw = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not raw:
            deny(f"{tool} call with no path")
        reason = protected(cwd / _expand(str(raw)))
        if reason:
            deny(reason)
    elif tool == "Bash":
        check_bash(str(tool_input.get("command", "")), cwd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
