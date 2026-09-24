"""Adding facts from a file, and confirming them -- which only the candidate can do.

A fact becomes usable as resume material only once its provenance says the
candidate confirmed it (`Fact.selectable`). Everything else -- a fact drafted
from an imported resume, or by an assistant from an answer -- starts
unconfirmed, and `confirm` is the only way across that line.

`confirm` reads its answers from the candidate's own terminal; see
`core.terminal` for what that does and does not guarantee.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import yaml
from pydantic import ValidationError

from core.profile.models import Fact, Provenance, Trust
from core.profile.store import StoreError, add_fact, load_facts, save_facts
from core.terminal import NoTerminalError, Terminal, ask, connect


class ConfirmError(RuntimeError):
    """Confirmation could not proceed; nothing was changed."""


def add_from_file(path: Path) -> tuple[list[str], list[tuple[str, str]]]:
    """Add every fact listed in the YAML file at `path`.

    Returns the ids added and, for each fact refused, its id (or its position
    when it has none) and the reason. One bad fact never costs the others.
    """
    try:
        parsed: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise StoreError(f"{path} is not valid YAML: {exc}") from exc
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
            if fact.selectable():
                # Only `confirm` makes a fact usable. Accepting one that arrives
                # confirmed would let whoever wrote the file skip the candidate.
                rejected.append(
                    (
                        label,
                        "a fact cannot arrive confirmed; add it unconfirmed and run "
                        "`callback facts confirm`",
                    )
                )
                continue
            add_fact(fact)
        except (ValidationError, StoreError) as exc:
            rejected.append((label, str(exc)))
            continue
        added.append(fact.id)
    return added, rejected


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


def confirm(ids: list[str], *, open_tty: Callable[[], Terminal] | None = None) -> list[str]:
    """Ask the candidate, fact by fact, whether each is true; return the ids confirmed.

    With no `ids`, every unconfirmed fact is offered. Answers are `y` (true
    as written), `n` (leave unconfirmed) or `q` (stop; answers already given
    are kept). A web-sourced fact is research about someone else and can
    never become a claim about the candidate. `open_tty` defaults to the
    controlling terminal.
    """
    facts = load_facts()
    unknown = [i for i in ids if i not in facts]
    if unknown:
        raise ConfirmError(f"no such fact(s): {unknown}")
    if ids:
        targets = [facts[i] for i in ids]
        web = [f.id for f in targets if f.provenance.trust is Trust.WEB]
        if web:
            raise ConfirmError(
                f"web-sourced facts cannot be confirmed as the candidate's own: {web}"
            )
    else:
        # Offering everything unconfirmed skips web facts rather than refusing:
        # one stored piece of research must not block confirming everything else.
        targets = [
            f for f in facts.values() if not f.selectable() and f.provenance.trust is not Trust.WEB
        ]
    targets = [f for f in targets if not f.selectable()]
    if not targets:
        return []

    try:
        tty = connect(open_tty)
    except NoTerminalError as exc:
        raise ConfirmError(
            f"confirmation must be typed by the candidate in a terminal ({exc})"
        ) from exc

    shown: dict[str, Fact] = {}
    try:
        for n, fact in enumerate(targets, 1):
            tty.write(f"\n[{n}/{len(targets)}] {fact.id} ({fact.kind})\n  {fact.text}\n")
            if fact.numbers:
                tty.write(f"  numbers: {fact.model_dump(mode='json')['numbers']}\n")
            if fact.dates:
                tty.write(f"  dates: {fact.dates.start} .. {fact.dates.end or 'present'}\n")
            answer = ask(tty, "True as written?")
            if answer == "q":
                break
            if answer == "y":
                shown[fact.id] = fact
    finally:
        tty.close()
    if not shown:
        return []
    # Re-read the store rather than saving the snapshot taken before the
    # prompts, which would silently drop anything added meanwhile. A fact
    # that changed or vanished since it was shown is not confirmed: the
    # candidate answered about the text they saw.
    current = load_facts()
    confirmed = [fid for fid, fact in shown.items() if current.get(fid) == fact]
    for fid in confirmed:
        current[fid] = _confirmed(current[fid])
    save_facts(current)
    return confirmed
