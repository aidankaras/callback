"""Filesystem boundary for user data.

All user data lives under a single private directory so that a path check is
sufficient to prove that a write cannot reach the repository or the plugin tree.
Symlinks are resolved before comparison: a link inside the home that points
outside it is an escape.
"""

from __future__ import annotations

import os
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
    """Create the data home and its standard subdirectories with mode 0700."""
    home = callback_home()
    home.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    home.chmod(DIR_MODE)
    for name in ("inbox", "outputs", "targets", "briefs", "base"):
        sub = home / name
        sub.mkdir(mode=DIR_MODE, exist_ok=True)
        sub.chmod(DIR_MODE)
    return home


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
    """Write `text` to `path` and restrict it to mode 0600."""
    path.write_text(text, encoding="utf-8")
    path.chmod(FILE_MODE)
