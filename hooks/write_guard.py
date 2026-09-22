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
- `applications.yaml`, the application record the volume limits read.
- key, ledger and database files.
- and, wherever the plugin lives, this guard and its `hooks.json`.

Everything else is allowed, including files outside the data home: the hook
runs on every write in the session, and confining all writes to the data
home would stop the assistant editing anything else at all.

Every path is resolved before comparison, so `..` and symlinks cannot reach a
protected file, and a directory holding a protected file counts as protected:
removing or moving it rewrites what is inside. For Bash, the command is split
into simple commands at shell operators and line breaks; `~`, `$HOME` and
`$CALLBACK_HOME` are expanded; and every word is resolved against the
session's working directory, every `cd` or `pushd` target, and any `-C` or
`--directory` argument. A command naming a protected path is refused whether
it reads or writes it -- telling the two apart in an arbitrary command is not
reliable (`cat x | tee x`, `sed -n` against `sed -i`) and nothing needs the
raw store, which `callback facts list` reads -- except `ls`, `stat` and `du`,
which only look, and the argument of a `-m` commit message. `callback facts
confirm` is refused in command position. Any payload this script cannot read
is denied: a guard that fails open would imply a guarantee it does not give.

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
PROTECTED_NAMES = {
    "facts.yaml",
    "applications.yaml",
    "ledger.log",
    ".key",
    "callback.db",
    "callback.db-wal",
}
SEALED_DIRS = {"final", "released"}
GUARD_FILES = {Path(__file__).resolve(), Path(__file__).resolve().with_name("hooks.json")}

_OPERATORS = {";", "&&", "||", "|", "&", "(", ")"}
# Commands that only look: naming a protected path to one of these is not a write.
_LOOK_ONLY = {"ls", "stat", "du"}
_MESSAGE_FLAGS = {"-m", "--message"}


def deny(reason: str) -> None:
    print(f"blocked by callback write guard: {reason}", file=sys.stderr)
    raise SystemExit(2)


def data_home() -> Path:
    raw = os.environ.get("CALLBACK_HOME")
    return (Path(raw) if raw else Path.home() / ".callback").expanduser().resolve()


def _expand(raw: str) -> str:
    """`raw` with `~`, `$HOME` and `$CALLBACK_HOME` spelled out, as a shell would."""
    home = str(data_home())
    user_home = str(Path.home())
    for var, value in (
        ("${CALLBACK_HOME}", home),
        ("$CALLBACK_HOME", home),
        ("${HOME}", user_home),
        ("$HOME", user_home),
    ):
        raw = raw.replace(var, value)
    if raw == "~" or raw.startswith("~/"):
        raw = user_home + raw[1:]
    return raw


def protected(path: Path) -> str | None:
    """Why `path` may not be written, or `None` if it may.

    A directory counts as protected when anything protected lies inside it:
    deleting or moving `outputs/<job>` rewrites the `final/` beneath it.
    """
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
    if target.is_dir() and any(
        p.name in PROTECTED_NAMES or p.name in SEALED_DIRS for p in target.rglob("*")
    ):
        return f"{target} holds protected files; use `callback delete-all` to remove your data"
    return None


def _segments(command: str) -> list[list[str]]:
    """`command` as simple commands, each a list of words, split at shell operators."""
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        words = list(lexer)
    except ValueError:
        words = command.split()
    segments: list[list[str]] = [[]]
    for word in words:
        if word in _OPERATORS or set(word) <= set(";&|()"):
            segments.append([])
        else:
            segments[-1].append(word)
    return [s for s in segments if s]


def _command_words(segment: list[str]) -> list[str]:
    """`segment` without leading `VAR=value` assignments and a `uv run [--flag value]` prefix."""
    words = list(segment)
    while words and re.fullmatch(r"[A-Za-z_]\w*=.*", words[0]):
        words.pop(0)
    if words[:2] == ["uv", "run"]:
        words = words[2:]
        while words and words[0].startswith("-"):
            flag = words.pop(0)
            if "=" not in flag and words:
                words.pop(0)
    return words


def check_bash(command: str, cwd: Path) -> None:
    bases = [cwd]
    for segment in _segments(command):
        words = _command_words(segment)
        if not words:
            continue
        if words[:3] == ["callback", "facts", "confirm"] or words[1:5] == [
            "-m",
            "core.cli",
            "facts",
            "confirm",
        ]:
            deny("`callback facts confirm` must be typed by the candidate in their own terminal")
        # A redirect turns any command into a write, so a segment with one is
        # never exempt: `ls > facts.yaml` truncates the file as surely as `echo`.
        redirects = any(">" in w for w in words)
        if words[0] in {"cd", "pushd"} and len(words) > 1:
            bases += [base / _expand(words[1]) for base in bases]
            if not redirects:
                continue
        if words[0] in _LOOK_ONLY and not redirects:
            continue
        local = list(bases)
        checked: list[str] = []
        skip_next = False
        for n, word in enumerate(words):
            if skip_next:
                skip_next = False
                continue
            if word in _MESSAGE_FLAGS:
                skip_next = True
                continue
            if word in {"-C", "--directory"} and n + 1 < len(words):
                local += [base / _expand(words[n + 1]) for base in bases]
            elif word.startswith("--directory="):
                local += [base / _expand(word.split("=", 1)[1]) for base in bases]
            checked.append(word)
        for word in checked:
            # A word may carry a path inside quotes or code, e.g. open('<path>','w').
            candidates = {word, *re.findall(r"[\w~$./@+{}-]+", word)}
            for candidate in candidates:
                for base in local:
                    reason = protected(base / _expand(candidate))
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
