"""Filesystem boundary for user data.

All user data lives under a single private directory so that a path check is
sufficient to prove that a write cannot reach the repository. Symlinks are
resolved before comparison: a link inside the home that points outside it is
an escape.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

DEFAULT_HOME = Path.home() / ".callback"
DIR_MODE = 0o700
FILE_MODE = 0o600


class OutsideHomeError(ValueError):
    """Raised when a requested path resolves outside the data home."""


def callback_home() -> Path:
    """Return the configured data home, honouring the `CALLBACK_HOME` override."""
    override = os.environ.get("CALLBACK_HOME")
    return Path(override).expanduser() if override else DEFAULT_HOME


def ensure_home() -> Path:
    """Create the data home and its standard subdirectories with mode 0700.

    Any missing parent directories of the data home are also created at 0700 —
    `Path.mkdir(parents=True)` alone would leave those at the umask default, which
    would contradict the private-by-default requirement for anything under the
    home. Parents that already existed are left untouched.
    """
    home = callback_home()
    _create_missing_parents(home)
    home.mkdir(mode=DIR_MODE, exist_ok=True)
    home.chmod(DIR_MODE)
    for name in ("inbox", "outputs", "targets", "briefs", "base"):
        sub = home / name
        sub.mkdir(mode=DIR_MODE, exist_ok=True)
        sub.chmod(DIR_MODE)
    return home


def _create_missing_parents(path: Path) -> None:
    """Create any missing ancestor directories of `path` at mode 0700.

    Only chmods directories this call actually creates. An ancestor that already
    exists is left exactly as it was — it belongs to whoever put it there, not to
    this tool.
    """
    missing: list[Path] = []
    ancestor = path.parent
    while not ancestor.exists():
        missing.append(ancestor)
        ancestor = ancestor.parent
    for directory in reversed(missing):
        directory.mkdir(mode=DIR_MODE)
        directory.chmod(DIR_MODE)


def resolve_inside(relative: str) -> Path:
    """Resolve `relative` against the data home and reject any escape.

    Resolves symlinks before comparing, so a link inside the home that points
    outside it is treated the same as a literal `../` traversal.
    """
    home = callback_home().resolve()
    candidate = (home / relative).resolve()
    if candidate != home and home not in candidate.parents:
        raise OutsideHomeError(f"{relative!r} resolves outside {home}")
    return candidate


def write_private(path: Path, text: str) -> None:
    """Write `text` to `path` atomically, at mode 0600, without ever truncating it in place.

    Writes to a hidden temporary file in the same directory as `path` — never a
    system temp directory, since a rename across filesystems is not atomic —
    chmods the temp file before the rename so its contents are never briefly
    world-readable, then swaps it into place with `Path.replace`, a single
    atomic operation on POSIX. A failure during the write only ever touches the
    temp file: whatever was previously at `path` is left exactly as it was, and
    the temp file is removed before the exception propagates.
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.chmod(FILE_MODE)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def delete_all() -> Iterator[Path]:
    """Remove every file and directory under the data home, yielding each path
    as it is removed rather than only after the whole walk finishes.

    This is a command whose entire job is deleting personal data, so a
    failure partway through must not leave the caller unable to tell what
    already vanished — yielding lazily lets a caller (see `core.cli`) report
    each removal as it happens, instead of only learning about any of them
    once every removal has already succeeded.

    Only descends into the resolved home and never follows a symlink out of
    it: a symlink is unlinked directly rather than resolved and traversed,
    whether it points at a file or a directory. Every other non-directory
    entry — a FIFO, a socket, a device node, anything `iterdir` can return
    that isn't a directory — is unlinked the same way, rather than silently
    left behind because it matched none of a fixed list of expected kinds.
    The home directory itself is left in place, empty, rather than removed
    outright, so a subsequent `ensure_home()` has nothing to recreate.

    Only ever touches what's under the data home; anything stored elsewhere
    is not this function's concern.
    """
    home = callback_home().resolve()
    if not home.is_dir():
        return

    def _walk(directory: Path) -> Iterator[Path]:
        for entry in sorted(directory.iterdir()):
            if entry.is_dir() and not entry.is_symlink():
                yield from _walk(entry)
                entry.rmdir()
                yield entry
            else:
                entry.unlink()
                yield entry

    yield from _walk(home)
