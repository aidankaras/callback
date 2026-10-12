"""Adding facts from a file, checking them, and the two decisions only the
candidate can make about one: that it is true, and that it no longer is.

A fact becomes usable as resume material only once its provenance says the
candidate confirmed it (`Fact.selectable`). Everything else -- a fact drafted
from an imported resume, or by an assistant from an answer -- starts
unconfirmed, and `confirm` is the only way across that line. `retire` is the
way back: a confirmed fact is never edited or deleted, because documents
already sent cite it, so a fact that stopped being true is taken out of use and
its correction added under a new id.

A draft is cheaper to fix than a confirmed fact, so two things happen before
confirmation. `check_facts` grounds a fact's own text against the fact, which
is what a bullet quoting it would face, and `add_from_file` can replace a
draft that failed.

`confirm` and `retire` read their answers from the candidate's own terminal;
see `core.terminal` for what that does and does not guarantee.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import yaml
from pydantic import ValidationError

from core.checks.grounding import Verdict, check_bullet
from core.errors import yaml_problem
from core.profile.models import Fact, FactKind, Provenance, Trust
from core.profile.store import (
    StoreError,
    add_fact,
    fact_validation_problems,
    load_facts,
    save_facts,
)
from core.terminal import NoTerminalError, Terminal, ask, connect

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")


class FactsError(RuntimeError):
    """A command about stored facts could not proceed; nothing was changed."""


class ConfirmError(FactsError):
    """A decision only the candidate can make could not be asked for; nothing was changed."""


def add_from_file(path: Path, *, replace: bool = False) -> tuple[list[str], list[tuple[str, str]]]:
    """Add every fact listed in the YAML file at `path`.

    Returns the ids added and, for each fact refused, its id (or its position
    when it has none) and the reason. One bad fact never costs the others.
    With `replace`, a fact whose id is already stored replaces it while the
    stored one is still a draft (`core.profile.store.add_fact`).
    """
    try:
        parsed: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise StoreError(f"{path} is {yaml_problem(exc)}") from exc
    if not isinstance(parsed, list):
        raise StoreError(f"{path} must contain a list of facts")
    # The boundary where this starts trusting the file's shape;
    # `Fact.model_validate` below is what checks each entry.
    bodies = cast("list[object]", parsed)
    added: list[str] = []
    rejected: list[tuple[str, str]] = []
    for position, body in enumerate(bodies):
        fid = cast("dict[str, object]", body).get("id") if isinstance(body, dict) else None
        # An entry's own id only when it looks like one: the file may be any
        # file on the machine, and its "id" any value in it.
        label = fid if isinstance(fid, str) and _ID.fullmatch(fid) else f"#{position}"
        try:
            fact = Fact.model_validate(body)
            if fact.was_confirmed():
                # Only `confirm` makes a fact usable. Accepting one that arrives
                # confirmed would let whoever wrote the file skip the candidate.
                # Asked of the provenance, not of `selectable`: a fact arriving
                # confirmed and retired backs nothing, and would still enter the
                # store recording a confirmation nobody gave.
                rejected.append(
                    (
                        label,
                        "a fact cannot arrive confirmed; add it unconfirmed and run "
                        "`callback facts confirm`",
                    )
                )
                continue
            if fact.declined_on is not None or fact.retired_on is not None:
                rejected.append(
                    (
                        label,
                        "a fact cannot arrive declined or retired; those are the "
                        "candidate's answers, given at `callback facts confirm` and "
                        "`callback facts retire`",
                    )
                )
                continue
            add_fact(fact, replace=replace)
        except ValidationError as exc:
            # The reason, not the entry: the file may be any file on the machine.
            rejected.append((label, fact_validation_problems(exc)))
            continue
        except StoreError as exc:
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
            ),
            "declined_on": None,
        }
    )


def state(fact: Fact) -> str:
    """The one word that says where `fact` stands: whether it is in use, and if
    not, which decision put it there."""
    if fact.retired_on is not None:
        return "retired"
    if fact.selectable():
        return "confirmed"
    return "declined" if fact.declined_on is not None else "unconfirmed"


def _today() -> date:
    return datetime.now(tz=UTC).date()


def _unknown(ids: list[str], facts: dict[str, Fact]) -> list[str]:
    return [i for i in ids if i not in facts]


# The kinds a bullet quotes: a detail of a role, and a project, whose text is
# its one-line description. A role or a degree reaches the page through its
# header fields, which the claims check compares field by field; its text
# restates them ("Boston, MA"), and would fail as prose on the state's initials.
_QUOTED_BY_BULLETS = frozenset({FactKind.EXPERIENCE_DETAIL, FactKind.PROJECT})


@dataclass(frozen=True)
class FactsChecked:
    """What `check_facts` looked at, and what it found.

    `problems` being empty says nothing by itself: it is what a check that
    looked at no fact returns too. `checked` is what was looked at, and
    `not_checked` gives the reason for each fact that was asked about by name
    and is never checked, so "no findings" can be told from "not looked at".
    """

    problems: dict[str, list[str]]
    checked: list[str]
    not_checked: dict[str, str]


def _never_checked(fact: Fact) -> str | None:
    """Why `fact` is not checked against its own text, or None if it is."""
    if fact.provenance.trust is Trust.WEB:
        return "research from the web never backs a bullet"
    if fact.kind not in _QUOTED_BY_BULLETS:
        return f"a {fact.kind.value} fact is never quoted by a bullet"
    return None


def check_facts(ids: list[str]) -> FactsChecked:
    """What a bullet quoting each fact word for word would fail on, by fact id.

    A fact's text is grounded against the fact itself and its parent, exactly
    as `build` grounds a bullet that cites them, so a token the text states
    and `entities` omits is found while the fact is still a draft.

    With no `ids`, every detail and project fact in use or still waiting for
    an answer is checked, and the rest are passed over without comment:
    nobody asked about them. A fact named in `ids` that is never checked is
    reported in `not_checked` with the reason.

    Facts are treated as confirmed for the comparison only. Nothing is saved.
    """
    facts = load_facts()
    unknown = _unknown(ids, facts)
    if unknown:
        raise FactsError(f"no such fact(s): {unknown}")
    named = [facts[i] for i in dict.fromkeys(ids)]
    targets = named or [f for f in facts.values() if f.retired_on is None and f.declined_on is None]
    problems: dict[str, list[str]] = {}
    checked: list[str] = []
    not_checked: dict[str, str] = {}
    for fact in targets:
        reason = _never_checked(fact)
        if reason is not None:
            if named:
                not_checked[fact.id] = reason
            continue
        parent = facts.get(fact.parent) if fact.parent else None
        sources = [fact] if parent is None else [fact, parent]
        as_confirmed = [_confirmed(s).model_copy(update={"retired_on": None}) for s in sources]
        verdict, findings = check_bullet(fact.text, as_confirmed)
        checked.append(fact.id)
        if verdict is Verdict.FAIL:
            problems[fact.id] = [f.detail for f in findings]
    return FactsChecked(problems=problems, checked=checked, not_checked=not_checked)


def confirm(
    ids: list[str],
    *,
    include_declined: bool = False,
    open_tty: Callable[[], Terminal] | None = None,
) -> list[str]:
    """Ask the candidate, fact by fact, whether each is true; return the ids confirmed.

    With no `ids`, every unconfirmed fact not yet answered about is offered;
    `include_declined` adds the ones answered `n` before, and naming a fact
    offers it whatever was answered. Answers are `y` (true as written), `n`
    (not as written: recorded, and not offered again) or `q` (stop; answers
    already given are kept, and what was not reached is offered next time). A
    web-sourced fact is research about someone else and can never become a
    claim about the candidate, and a retired fact is out of use for good.
    `open_tty` defaults to the controlling terminal.
    """
    facts = load_facts()
    unknown = _unknown(ids, facts)
    if unknown:
        raise ConfirmError(f"no such fact(s): {unknown}")
    if ids:
        targets = [facts[i] for i in ids]
        web = [f.id for f in targets if f.provenance.trust is Trust.WEB]
        if web:
            raise ConfirmError(
                f"web-sourced facts cannot be confirmed as the candidate's own: {web}"
            )
        retired = [f.id for f in targets if f.retired_on is not None]
        if retired:
            raise ConfirmError(
                f"retired facts stay retired: {retired}; add the fact again under a new id"
            )
    else:
        # Offering everything unconfirmed skips web facts rather than refusing:
        # one stored piece of research must not block confirming everything else.
        targets = [
            f
            for f in facts.values()
            if f.provenance.trust is Trust.IMPORT_UNCONFIRMED
            and f.retired_on is None
            and (include_declined or f.declined_on is None)
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
    declined: dict[str, Fact] = {}
    try:
        for n, fact in enumerate(targets, 1):
            tty.write(f"\n[{n}/{len(targets)}] {fact.id} ({fact.kind})\n  {fact.text}\n")
            if fact.numbers:
                tty.write(f"  numbers: {fact.model_dump(mode='json')['numbers']}\n")
            if fact.dates:
                tty.write(f"  dates: {fact.dates.start} .. {fact.dates.end or 'present'}\n")
            if fact.entities:
                # What a bullet may name as used rests on this list, not on
                # the text, so it is part of what is being confirmed.
                tty.write(f"  names as used: {', '.join(fact.entities)}\n")
            if fact.parent:
                # Which entry a fact may back, and which skills that entry
                # shows, follow from where the fact is filed.
                above = facts.get(fact.parent)
                about = f" ({above.text})" if above is not None else " (no such fact)"
                tty.write(f"  filed under: {fact.parent}{about}\n")
            answer = ask(tty, "True as written?")
            if answer == "q":
                break
            (shown if answer == "y" else declined)[fact.id] = fact
    finally:
        tty.close()
    if not shown and not declined:
        return []
    # Re-read the store rather than saving the snapshot taken before the
    # prompts, which would silently drop anything added meanwhile. A fact
    # that changed or vanished since it was shown keeps no answer, yes or no:
    # the candidate answered about the text they saw.
    current = load_facts()
    confirmed = [fid for fid, fact in shown.items() if current.get(fid) == fact]
    for fid in confirmed:
        current[fid] = _confirmed(current[fid])
    for fid, fact in declined.items():
        if current.get(fid) == fact:
            current[fid] = fact.model_copy(update={"declined_on": _today()})
    save_facts(current)
    return confirmed


def retire(ids: list[str], *, open_tty: Callable[[], Terminal] | None = None) -> list[str]:
    """Ask the candidate whether each named fact should go out of use; return the ids retired.

    A retired fact backs nothing from then on and is never offered, confirmed
    or replaced. It stays in the store with its provenance, because a
    document sent while it was in use still cites it. Facts must be named:
    retiring is never the default for anything.
    """
    if not ids:
        raise ConfirmError("name the fact(s) to retire")
    facts = load_facts()
    unknown = _unknown(ids, facts)
    if unknown:
        raise ConfirmError(f"no such fact(s): {unknown}")
    targets = [facts[i] for i in dict.fromkeys(ids) if facts[i].retired_on is None]
    if not targets:
        return []
    try:
        tty = connect(open_tty)
    except NoTerminalError as exc:
        raise ConfirmError(
            f"retiring a fact must be typed by the candidate in a terminal ({exc})"
        ) from exc

    chosen: dict[str, Fact] = {}
    try:
        for n, fact in enumerate(targets, 1):
            state = "confirmed" if fact.selectable() else "unconfirmed"
            tty.write(f"\n[{n}/{len(targets)}] {fact.id} ({fact.kind}, {state})\n  {fact.text}\n")
            answer = ask(tty, "Retire it? It will back no document from now on.")
            if answer == "q":
                break
            if answer == "y":
                chosen[fact.id] = fact
    finally:
        tty.close()
    if not chosen:
        return []
    current = load_facts()
    retired = [fid for fid, fact in chosen.items() if current.get(fid) == fact]
    for fid in retired:
        current[fid] = current[fid].model_copy(update={"retired_on": _today()})
    save_facts(current)
    return retired
