"""Adding facts from a file, and confirming them -- which only the candidate can do.

A fact becomes usable as resume material only once its provenance says the
candidate confirmed it (`Fact.selectable`). Everything else -- a fact drafted
from an imported resume, or by an assistant from an answer -- starts
unconfirmed, and `confirm` is the only way across that line.

`confirm` reads its answers from the controlling terminal, `/dev/tty`, not
from standard input. A process with no terminal -- an agent's shell, a pipe,
a scheduled job -- cannot open it, so it cannot confirm a fact on the
candidate's behalf, even by piping "y" into the command. That is a guard
against *accidental* confirmation. It is not a security boundary against a
process that can edit the store's file directly.
"""

from __future__ import annotations

import errno
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

import yaml
from pydantic import ValidationError

from core.profile.models import Fact, Provenance, Trust
from core.profile.store import StoreError, add_fact, load_facts, save_facts


class Terminal(Protocol):
    """What confirmation needs from a terminal."""

    def write(self, s: str, /) -> int: ...
    def readline(self, size: int = -1, /) -> str: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...


class ConfirmError(RuntimeError):
    """Confirmation could not proceed; nothing was changed."""


def add_from_file(path: Path) -> tuple[list[str], list[tuple[str, str]]]:
    """Add every fact listed in the YAML file at `path`.

    Returns the ids added and, for each fact refused, its id (or its position
    when it has none) and the reason. One bad fact never costs the others.
    """
    parsed: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, list):
        raise StoreError(f"{path} must contain a list of facts")
    # The boundary where this starts trusting the file's shape;
    # `Fact.model_validate` below is what checks each entry.
    bodies = cast("list[object]", parsed)
    added: list[str] = []
    rejected: list[tuple[str, str]] = []
    for position, body in enumerate(bodies):
        fid = cast("dict[str, object]", body).get("id") if isinstance(body, dict) else None
        label = str(fid) if fid is not None else f"#{position}"
        try:
            fact = Fact.model_validate(body)
            add_fact(fact)
        except (ValidationError, StoreError) as exc:
            rejected.append((label, str(exc)))
            continue
        added.append(fact.id)
    return added, rejected


class _Tty:
    """The controlling terminal, as separate read and write handles.

    A single `"r+"` text handle on `/dev/tty` cannot be opened at all: text
    mode in read-write requires a seekable stream, and a terminal is not one,
    so `open` raises `io.UnsupportedOperation` -- which is an `OSError`, and
    would read as "no terminal" to anything catching that.
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


def _open_terminal() -> Terminal:
    # Closed by `confirm` in its own `finally`.
    return _Tty()


def _confirmed(fact: Fact) -> Fact:
    source = fact.provenance.source
    return fact.model_copy(
        update={
            "provenance": Provenance(
                source=source if source.startswith("user:") else f"user:confirmed:{source}",
                trust=Trust.USER_CONFIRMED,
            )
        }
    )


def _ask(tty: Terminal) -> str:
    """One of "y", "n" or "q". An unrecognised answer is asked again rather
    than read as "no"; end of input is "q", so a closed terminal stops the
    loop instead of spinning on empty reads."""
    while True:
        tty.write("True as written? [y]es / [n]o / [q]uit: ")
        tty.flush()
        line = tty.readline()
        if not line:
            return "q"
        answer = line.strip().casefold()
        if answer in {"y", "n", "q"}:
            return answer
        tty.write("Please answer y, n or q.\n")


def confirm(ids: list[str], *, open_tty: Callable[[], Terminal] = _open_terminal) -> list[str]:
    """Ask the candidate, fact by fact, whether each is true; return the ids confirmed.

    With no `ids`, every unconfirmed fact is offered. Answers are `y` (true
    as written), `n` (leave unconfirmed) or `q` (stop; answers already given
    are kept). A web-sourced fact is research about someone else and can
    never become a claim about the candidate.
    """
    facts = load_facts()
    unknown = [i for i in ids if i not in facts]
    if unknown:
        raise ConfirmError(f"no such fact(s): {unknown}")
    targets = [facts[i] for i in ids] if ids else [f for f in facts.values() if not f.selectable()]
    web = [f.id for f in targets if f.provenance.trust is Trust.WEB]
    if web:
        raise ConfirmError(f"web-sourced facts cannot be confirmed as the candidate's own: {web}")
    targets = [f for f in targets if not f.selectable()]
    if not targets:
        return []

    try:
        tty = open_tty()
    except OSError as exc:
        if exc.errno not in _NO_TERMINAL:
            raise
        raise ConfirmError(
            "confirmation must be typed by the candidate in a terminal "
            f"(could not open /dev/tty: {exc.strerror or exc})"
        ) from exc

    confirmed: list[str] = []
    shown: dict[str, Fact] = {}
    try:
        for n, fact in enumerate(targets, 1):
            tty.write(f"\n[{n}/{len(targets)}] {fact.id} ({fact.kind})\n  {fact.text}\n")
            if fact.numbers:
                tty.write(f"  numbers: {fact.model_dump(mode='json')['numbers']}\n")
            if fact.dates:
                tty.write(f"  dates: {fact.dates.start} .. {fact.dates.end or 'present'}\n")
            answer = _ask(tty)
            if answer == "q":
                break
            if answer == "y":
                shown[fact.id] = fact
                confirmed.append(fact.id)
    finally:
        tty.close()
    if confirmed:
        # Re-read the store rather than saving the snapshot taken before the
        # prompts, which would silently drop anything added meanwhile. A fact
        # that changed or vanished since it was shown is not confirmed: the
        # candidate answered about the text they saw.
        current = load_facts()
        confirmed = [fid for fid in confirmed if current.get(fid) == shown[fid]]
        for fid in confirmed:
            current[fid] = _confirmed(current[fid])
        save_facts(current)
    return confirmed
