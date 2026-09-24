"""The candidate's own terminal, for the decisions only the candidate may make.

Confirming a fact and activating a note both change what an assistant is
allowed to rely on, so both read their answer from the controlling terminal,
`/dev/tty`, rather than from standard input. A process with no terminal -- an
agent's shell, a pipe, a scheduled job -- cannot open it, so it cannot answer
on the candidate's behalf, even by piping "y" into the command.

A process can obtain one, though: `script` or `unbuffer` hands a child a
pseudo-terminal, under which `/dev/tty` opens. The plugin's write guard
refuses these commands run through such a wrapper. Together that is a guard
against *accidental* or steered answers. It is not a security boundary against
a process built to get round it.
"""

from __future__ import annotations

import errno
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

Answer = Literal["y", "n", "q"]


class Terminal(Protocol):
    """What asking the candidate needs from a terminal."""

    def write(self, s: str, /) -> int: ...
    def readline(self, size: int = -1, /) -> str: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...


class NoTerminalError(RuntimeError):
    """This process has no controlling terminal, so no one can be asked."""


class _Tty:
    """The controlling terminal, as separate read and write handles.

    A single `"r+"` handle on `/dev/tty` cannot be opened at all: a buffered
    read-write handle, text (`"r+"`) or binary (`"r+b"`), needs a seekable
    stream, and a terminal is not one, so `open` raises
    `io.UnsupportedOperation` -- which is an `OSError`, and would read as "no
    terminal" to anything catching that.
    """

    def __init__(self) -> None:
        self._read = Path("/dev/tty").open(encoding="utf-8")  # noqa: SIM115 -- closed in close()
        try:
            self._write = Path("/dev/tty").open("w", encoding="utf-8")  # noqa: SIM115
        except BaseException:
            self._read.close()
            raise

    def write(self, s: str, /) -> int:
        return self._write.write(s)

    def readline(self, size: int = -1, /) -> str:
        return self._read.readline(size)

    def flush(self) -> None:
        self._write.flush()

    def close(self) -> None:
        self._write.close()
        self._read.close()


# The errors that mean "this process has no controlling terminal". Anything
# else opening /dev/tty is a real failure and is raised as itself.
_NO_TERMINAL = {errno.ENXIO, errno.ENOENT}


def _open_tty() -> Terminal:
    return _Tty()


def connect(open_tty: Callable[[], Terminal] | None = None) -> Terminal:
    """Open the candidate's terminal; the caller closes it.

    `open_tty` defaults to the controlling terminal. Looked up at call time,
    not bound as a default argument, so replacing `_open_tty` replaces every
    caller's. Raises `NoTerminalError` when there is no terminal to open.
    """
    try:
        return (open_tty or _open_tty)()
    except OSError as exc:
        if exc.errno not in _NO_TERMINAL:
            raise
        raise NoTerminalError(f"could not open /dev/tty: {exc.strerror or exc}") from exc


def ask(tty: Terminal, question: str) -> Answer:
    """One of "y", "n" or "q". An unrecognised answer is asked again rather
    than read as "no"; end of input is "q", so a closed terminal stops the
    loop instead of spinning on empty reads."""
    while True:
        tty.write(f"{question} [y]es / [n]o / [q]uit: ")
        tty.flush()
        line = tty.readline()
        if not line:
            return "q"
        answer = line.strip().casefold()
        if answer == "y" or answer == "n" or answer == "q":
            return answer
        tty.write("Please answer y, n or q.\n")
