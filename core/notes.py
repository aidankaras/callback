"""Working memory that is not a fact: what the candidate prefers, wants and must avoid.

A fact is a claim a resume may make. A note is everything else a session
should know before it starts -- "one page always", "never use 'spearheaded'",
"targeting ML infrastructure", "offer deadline Nov 14" -- so that no session
has to be briefed again.

A note cannot make an ungrounded claim pass the gates: grounding reads facts
and nothing else. It can change everything the gates do not check -- which
facts are chosen, what is left out, the wording, and the letters, answers and
outreach. So a note is written in two steps. An assistant may *propose* one;
it becomes *active*, and is shown to future sessions, only when the candidate
activates it on a surface the assistant does not author: their own terminal,
through `callback notes activate`, which shows the full text and where it came
from. A tool-permission prompt does not count: it shows an id rather than the
text, and an auto-approving session approves it with no one looking.

Retiring needs no terminal. It can only set aside a note the candidate
activated, the note stays in the file, and activating it again asks them.

`notes.yaml` also holds the role families the candidate sorts applications
into, so that `insights` can group by them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from core.paths import ensure_home, resolve_inside, write_private
from core.profile.store import StoreError, screen_sensitive
from core.terminal import NoTerminalError, Terminal, ask, connect

NOTES_FILE = "notes.yaml"
MAX_TEXT = 200
FAMILY = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")

NoteKind = Literal["preference", "goal", "constraint", "context"]
NoteStatus = Literal["proposed", "active", "retired"]


class NoteError(RuntimeError):
    """A note that cannot be stored or changed; nothing was written."""


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must carry a timezone")
    return value


class Note(BaseModel):
    """One note. Frozen: a status changes only through this module's functions."""

    model_config = {"extra": "forbid", "frozen": True}

    id: str = Field(pattern=r"^n[1-9][0-9]{0,5}$")
    kind: NoteKind
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    added: datetime
    # "assistant" for a proposal; "user" for a note the candidate wrote into
    # the file by hand, which is theirs to do.
    source: Literal["user", "assistant"]
    # The job or posting in view when an assistant proposed it, so the
    # candidate can see what prompted it before activating.
    source_context: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9:_-]{0,127}$")
    status: NoteStatus

    _aware = field_validator("added")(_aware)


class Notebook(BaseModel):
    model_config = {"extra": "forbid"}

    role_families: list[str] = Field(default_factory=list[str])
    notes: list[Note] = Field(default_factory=list[Note])

    @field_validator("role_families")
    @classmethod
    def _families(cls, value: list[str]) -> list[str]:
        bad = [f for f in value if not FAMILY.fullmatch(f)]
        if bad:
            raise ValueError(f"role families must be lowercase letters, digits, hyphens: {bad}")
        if len(set(value)) != len(value):
            raise ValueError("role families must not repeat")
        return value


def load() -> Notebook:
    """The notebook. A missing or empty file is an empty one; anything else
    that does not validate is refused, never read as "no notes"."""
    path = resolve_inside(NOTES_FILE)
    if not path.exists():
        return Notebook()
    try:
        text = path.read_text(encoding="utf-8")
        parsed: object = yaml.safe_load(text)
        if parsed is None and not text.strip():
            return Notebook()
        return Notebook.model_validate(parsed)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise NoteError(f"{path} is not a readable notebook: {exc}") from exc


def _save(book: Notebook) -> None:
    body = yaml.safe_dump(book.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    ensure_home()
    write_private(resolve_inside(NOTES_FILE), body)


def active() -> list[Note]:
    """The notes a session may act on. A proposed or retired note is never among them."""
    return [n for n in load().notes if n.status == "active"]


def _next_id(book: Notebook) -> str:
    return f"n{max((int(n.id[1:]) for n in book.notes), default=0) + 1}"


def _text(text: str) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        raise NoteError("a note needs some text")
    if len(cleaned) > MAX_TEXT:
        raise NoteError(f"a note is at most {MAX_TEXT} characters; this one is {len(cleaned)}")
    try:
        screen_sensitive(cleaned, "note")
    except StoreError as exc:
        raise NoteError(str(exc)) from exc
    return cleaned


def propose(kind: NoteKind, text: str, *, source_context: str | None, now: datetime) -> Note:
    """Record an assistant's suggested note. It stays inert until the candidate activates it."""
    book = load()
    try:
        note = Note(
            id=_next_id(book),
            kind=kind,
            text=_text(text),
            added=now,
            source="assistant",
            source_context=source_context,
            status="proposed",
        )
    except ValidationError as exc:
        raise NoteError(str(exc)) from exc
    book.notes.append(note)
    _save(book)
    return note


def _find(book: Notebook, note_id: str) -> int:
    for i, note in enumerate(book.notes):
        if note.id == note_id:
            return i
    raise NoteError(f"no note {note_id!r}: run `callback notes list --all`")


def activate(note_id: str, *, open_tty: Callable[[], Terminal] | None = None) -> bool:
    """Show the candidate the note in full, with where it came from, and activate it on a yes.

    Returns whether the note is now active. A retired note may be activated
    again: retiring sets a note aside, it does not destroy it.
    """
    book = load()
    note = book.notes[_find(book, note_id)]
    if note.status == "active":
        return True
    origin = note.source + (
        f", while working on {note.source_context}" if note.source_context else ""
    )
    try:
        tty = connect(open_tty)
    except NoTerminalError as exc:
        raise NoteError(
            f"only the candidate can activate a note, in their own terminal ({exc})"
        ) from exc
    try:
        tty.write(f"\n{note.id} ({note.kind}, {note.status}, from {origin})\n  {note.text}\n")
        if ask(tty, "Should future sessions follow this?") != "y":
            return False
    finally:
        tty.close()
    # Re-read, as `facts confirm` does: a note changed while it was on screen
    # is not the one the candidate answered about.
    current = load()
    i = _find(current, note_id)
    if current.notes[i] != note:
        raise NoteError(f"note {note_id} changed while it was being shown; nothing was activated")
    current.notes[i] = note.model_copy(update={"status": "active"})
    _save(current)
    return True


def retire(note_id: str) -> Note:
    """Set a note aside, proposed or active. It stays in the file, and
    activating it again asks the candidate."""
    book = load()
    i = _find(book, note_id)
    book.notes[i] = book.notes[i].model_copy(update={"status": "retired"})
    _save(book)
    return book.notes[i]


def set_role_families(families: list[str]) -> list[str]:
    """Replace the list of role families applications may be sorted into."""
    book = load()
    try:
        updated = Notebook(role_families=families, notes=book.notes)
    except ValidationError as exc:
        raise NoteError(str(exc)) from exc
    _save(updated)
    return updated.role_families


def role_families() -> list[str]:
    return list(load().role_families)
