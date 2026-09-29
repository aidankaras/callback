#!/usr/bin/env python3
"""PreToolUse guard: an assistant may not write the fact store or a sealed document.

Claude Code runs this before every Write, Edit, MultiEdit, NotebookEdit and
Bash call, passing the call as JSON on stdin. Exit 2 blocks the call and
shows stderr to the model; exit 0 lets it through.

What is protected, under `$CALLBACK_HOME` (default `~/.callback`):

- `facts.yaml`, the fact store. Facts enter it only through
  `callback facts add`, which marks them unconfirmed, and become usable only
  through `callback facts confirm`, which only the candidate can answer, and
  go out of use only through `callback facts retire`, likewise theirs. A tool
  that could write the store directly could skip all three.
- `notes.yaml`, the candidate's standing preferences. An assistant may
  propose or retire a note through `callback`; only the candidate activates
  one, with `callback notes activate` at their own terminal.
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
which only look, unless the command substitutes another (`$(...)`, a
backtick, `<(...)`) or redirects into a file -- and the argument of a `-m`
commit message. The commands only the candidate may run, `callback facts
confirm`, `callback facts retire` and `callback notes activate`,
are refused in command position, and anywhere else in a command except as the
text a search or `git` is given. Any payload this script cannot read
is denied: a guard that fails open would imply a guarantee it does not give.

Bash guarding is defence in depth, not containment. For the candidate-only
commands in particular, a phrase split across words (`conf''irm`, a variable
holding only `activate`) or a script written to a file and then run is not
caught, and neither is Python that imports the modules behind them under
another name; the guard stops an assistant steered into running them, not one
set on it. A shell is a general tool, and a command that builds a path at run
time -- from a variable other than `CALLBACK_HOME`, from `find`, from an
encoded string -- is not caught.
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
from itertools import pairwise
from pathlib import Path

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
PROTECTED_NAMES = {
    "facts.yaml",
    "notes.yaml",
    "applications.yaml",
    "ledger.log",
    ".key",
    "callback.db",
    "callback.db-wal",
}
SEALED_DIRS = {"final", "released"}
GUARD_FILES = {Path(__file__).resolve(), Path(__file__).resolve().with_name("hooks.json")}

_OPERATORS = {";", "&&", "||", "|", "&", "(", ")"}
# Commands that only look: naming a protected path to one of these is not a
# write. `echo` and `printf` are not among them: what they print can be piped
# into `xargs rm`.
_LOOK_ONLY = {"ls", "stat", "du"}
# Text that runs a command wherever it appears, quoted or not: `$(...)`, a
# backtick, and process substitution `<(...)` and `>(...)`.
_SUBSTITUTION = re.compile(r"`|[$<>]\(")
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


_GLOB = re.compile(r"[*?\[]")


def protected(path: Path) -> str | None:
    """Why `path` may not be written, or `None` if it may.

    A directory counts as protected when anything protected lies inside it:
    deleting or moving `outputs/<job>` rewrites the `final/` beneath it.
    """
    if _GLOB.search(path.name):
        # A wildcard is not resolvable to one file here, and expanding it
        # would only see the files that exist at this moment. Inside the data
        # home it may name any of them, `rm ~/.callback/facts.*` included.
        directory = path.parent.resolve()
        home = data_home()
        if directory == home or home in directory.parents:
            return "a wildcard inside the data home may name a protected file"
        return None
    target = path.resolve()
    if target in GUARD_FILES:
        return "the write guard is not writable by a tool"
    home = data_home()
    if target != home and home not in target.parents:
        return None
    inside = target.relative_to(home).parts
    if inside == ("facts.yaml",):
        return (
            "the fact store changes only through `callback facts add`, and through "
            "`callback facts confirm` and `callback facts retire`, which the candidate runs"
        )
    if inside == ("notes.yaml",):
        return "notes change only through `callback notes`, and only the candidate activates one"
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
    # Newlines became ";" above, so the whole command is one logical line and
    # shlex's default comment character would swallow everything after the
    # first unquoted "#" -- including the command on the next line. A guard
    # that reads less than the shell will run fails open, so nothing is
    # treated as a comment: a protected path named in one is checked too.
    lexer.commenters = ""
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
    """`segment` without leading `VAR=value` assignments and a leading `uv run`.

    `uv run`'s own flags are kept rather than skipped. Which of them take a
    value cannot be known here, and guessing cost the guard twice: consuming
    the word after every flag hid `uv run --directory <dir> rm ../facts.yaml`
    from the directory tracking below, and let a boolean flag such as
    `--locked` swallow the word that named `callback facts confirm`. Left in
    place, a flag is an unrecognised word, which every check below is safe
    against."""
    words = list(segment)
    while words and re.fullmatch(r"[A-Za-z_]\w*=.*", words[0]):
        words.pop(0)
    return words[2:] if words[:2] == ["uv", "run"] else words


def _runs(words: list[str], wanted: list[str]) -> bool:
    """Whether `wanted` appears in `words` as consecutive words, anywhere: a
    wrapper's flags may precede the command, and `uv run --locked callback ...`
    names the same command as `callback ...`."""
    return any(words[i : i + len(wanted)] == wanted for i in range(len(words) - len(wanted) + 1))


# The commands only the candidate may run, as the words after `callback`. Each
# reads its answer from `/dev/tty`. Either can also run as text another
# command is handed, as `callback facts confirm` can:
# `script -qc "..."` gives it a pseudo-terminal, under which `/dev/tty` opens
# and confirmation succeeds with no one at the keyboard, and `bash -c`,
# `watch`, `flock`, `find -exec` or `awk`'s `system()` run it the same way.
# Listing the runners cannot be complete, so the rule is inverted: a word
# naming `facts confirm` is allowed only in a command that merely mentions
# text, and refused everywhere else. That also refuses a harmless
# `timeout 10 grep "facts confirm"`; the guard does not model what a runner
# runs.
_MENTION_ONLY = frozenset({"grep", "egrep", "fgrep", "rg", "git"})
_CANDIDATE_ONLY = (("facts", "confirm"), ("facts", "retire"), ("notes", "activate"))
# Any run of separators: the shell reads the same command however many spaces
# or tabs stand between its words, and a bound on them is a gap to step through.
_CANDIDATE_TEXT = re.compile(r"facts\W+(?:confirm|retire)|notes\W+activate", re.IGNORECASE)


# The modules that can change what only the candidate decides, named as Python
# imports them: `python -c "from core.notes import activate; ..."` can hand
# `activate` a fake terminal, and `core.profile.store` can save a confirmed
# fact, without the words the pattern above looks for. Refused on the same
# terms. Paths (`core/notes.py`) are not matched: naming the file runs nothing.
_CANDIDATE_CODE = re.compile(
    r"\bcore\.(?:notes|factscmd|profile\.store)\b"
    r"|\bfrom\s+core(?:\.profile)?\s+import\b[^;]*\b(?:notes|factscmd|store)\b"
)


def _named(text: str) -> str:
    """The candidate-only command `text` names, as its words after `callback`."""
    found = _CANDIDATE_TEXT.search(text)
    return " ".join(re.findall(r"[a-z]+", found.group(0).casefold())) if found else "facts confirm"


def _names_candidate_only(text: str) -> bool:
    """Whether `text` names a candidate-only command or module."""
    return bool(_CANDIDATE_TEXT.search(text) or _CANDIDATE_CODE.search(text))


def _git_config_runs(words: list[str]) -> bool:
    """Whether a `git` command sets config that names a candidate-only command
    or module (`-c alias.x=!...`)."""
    return Path(words[0]).name == "git" and any(
        w in {"-c", "--config-env"} and _names_candidate_only(nxt) for w, nxt in pairwise(words)
    )


def _writes(words: list[str]) -> bool:
    """Whether `words` redirect output into a file. Any word holding `>` counts
    -- `;>` and `>(` included -- except a descriptor duplication (`2>&1`) and a
    redirect into /dev/null, which write nothing."""
    for n, word in enumerate(words):
        if ">" not in word:
            continue
        target = words[n + 1] if n + 1 < len(words) else ""
        if word == ">&" and (target.isdigit() or target == "-"):
            continue
        if target == "/dev/null":
            continue
        return True
    return False


def _check_paths(words: list[str], bases: list[Path]) -> None:
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
        candidates = {word, *re.findall(r"[\w~$./@+{}*?-]+", word)}
        for candidate in candidates:
            for base in local:
                reason = protected(base / _expand(candidate))
                if reason:
                    deny(
                        f"command names a protected path ({candidate}): {reason}; "
                        "read facts with `callback facts list`"
                    )


def check_bash(command: str, cwd: Path) -> None:
    bases = [cwd]
    for segment in _segments(command):
        words = _command_words(segment)
        # The raw segment, assignments included: `C='callback facts confirm';
        # script -qc "$C"` carries the command in a variable, and a leading
        # `GIT_EDITOR=...` runs it. `git` is exempt as a place to mention the
        # text (a commit message, a log search) but not through `-c`, whose
        # aliases, editor and pager run commands.
        leading = segment[: len(segment) - len(words)] if words else segment
        # Searched across the words as well as in each: an unquoted backtick
        # splits `` `callback facts confirm` `` into three.
        joined = " ".join(segment)
        named = joined if _CANDIDATE_TEXT.search(joined) else None
        code = joined if _CANDIDATE_CODE.search(joined) else None
        # A search mentions text; a substitution inside its arguments runs it.
        mention_only = (
            bool(words)
            and Path(words[0]).name in _MENTION_ONLY
            and not _git_config_runs(words)
            and not any(_names_candidate_only(w) for w in leading)
            and not any(_SUBSTITUTION.search(w) for w in segment)
        )
        if named is not None and not mention_only:
            deny(
                f"`callback {_named(named)}` must be typed by the candidate in their own "
                "terminal, not run through another command"
            )
        if code is not None and not mention_only:
            deny(
                "code that confirms facts or activates notes runs only as `callback`, "
                "typed by the candidate; read with `callback facts list` or `notes list`"
            )
        # An assignment runs what it substitutes: `A="$(rm facts.yaml)"`. One
        # that only names a path, `CALLBACK_HOME=~/.callback`, writes nothing.
        _check_paths([w for w in leading if _SUBSTITUTION.search(w)], bases)
        if not words:
            continue
        # By basename: `.venv/bin/callback` is the same command as `callback`.
        names = [Path(w).name for w in words]
        for sub in _CANDIDATE_ONLY:
            if _runs(names, ["callback", *sub]) or _runs(names, ["-m", "core.cli", *sub]):
                deny(
                    f"`callback {' '.join(sub)}` must be typed by the candidate "
                    "in their own terminal"
                )
        # A redirect turns any command into a write: `ls > facts.yaml` truncates
        # the file as surely as `echo`.
        redirects = _writes(words)
        # A substitution runs a command inside the argument: `ls "$(rm x)"` is
        # an `rm`, whatever runs first.
        substitutes = any(_SUBSTITUTION.search(w) for w in segment)
        if words[0] in {"cd", "pushd"} and len(words) > 1:
            bases += [base / _expand(words[1]) for base in bases]
            if not redirects and not substitutes:
                continue
        if words[0] in _LOOK_ONLY and not redirects and not substitutes:
            continue
        _check_paths(words, bases)


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
