"""Persistence and admission control for candidate facts.

Two invariants are enforced here rather than by convention:
facts that would record a legally sensitive attribute are refused, and an
identifier is never silently overwritten. Every way `add_fact` and
`load_facts` can fail -- an escape outside the data home, a missing or
unreadable file, invalid YAML, a malformed fact, a duplicate id, a sensitive
field -- surfaces as `StoreError`, so a caller can catch one type and know it
has seen every failure; see `StoreError` for the full list this closes.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import cast

import yaml
from pydantic import ValidationError

from core.aliases import canonicalize
from core.paths import OutsideHomeError, resolve_inside, write_private
from core.profile.models import Fact

FACTS_FILE = "facts.yaml"

# Every pattern here targets self-attribution or an explicit field label (e.g.
# "date of birth:", "I am married"), never a bare topic word. callback is not a
# classifier that infers intent from a topic; it is a refusal of a candidate
# recording their own protected attributes. A bare word like "race", "married",
# "disabled" or "religious" collides constantly with ordinary technical and
# academic vocabulary ("race condition", "married two datasets", "disabled
# legacy flow", "Religious Studies") — refusing those teaches the user the tool
# is broken, which is worse than not screening at all. Equally, a label must
# not promise more than its pattern delivers: "salary history" previously
# matched only "current/previous/prior salary" and let the literal phrase
# "salary history" straight through.
SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("date of birth", re.compile(r"\b(date of birth|d\.?o\.?b\.?)\b", re.I)),
    (
        "age",
        re.compile(r"\b(?:i am|i'm)\s+\d{1,2}\b|\bage\s*:\s*\d{1,2}\b|\bborn\s+(?:in|on)\s", re.I),
    ),
    ("ssn", re.compile(r"\b(ssn|social security (?:number|no))\b|\b\d{3}-\d{2}-\d{4}\b", re.I)),
    (
        "salary history",
        re.compile(
            r"\b(salary history|desired salary|(?:current|previous|prior|expected)\s+salary)\b",
            re.I,
        ),
    ),
    (
        "marital status",
        re.compile(r"\bmarital status\b|\bi am (?:married|divorced|single|widowed)\b", re.I),
    ),
    ("religion", re.compile(r"\b(my religion|religious affiliation)\b|\breligion\s*:", re.I)),
    (
        "disability",
        re.compile(r"\b(disability status|disabled veteran|i have a disability)\b", re.I),
    ),
    (
        "race or ethnicity",
        re.compile(
            r"\b(racial background|ethnic background|race or ethnicity)\b|\b(?:race|ethnicity)\s*:",
            re.I,
        ),
    ),
    ("gender", re.compile(r"\b(gender identity|sex assigned)\b|\bgender\s*:", re.I)),
    (
        "street address",
        re.compile(
            r"\b\d{1,5}\s+\w+\s+"
            r"(street|st|avenue|ave|road|rd|drive|dr|boulevard|blvd|lane|ln|way|"
            r"court|ct|circle|cir|place|pl)\b",
            re.I,
        ),
    ),
)


class StoreError(Exception):
    """The only exception type `add_fact` and `load_facts` let escape.

    Both functions cross a filesystem and a parsing boundary: `resolve_inside`
    can raise `OutsideHomeError`, reading and parsing the facts file can raise
    `OSError` or `yaml.YAMLError`, a malformed fact can raise pydantic's
    `ValidationError`, and writing can raise `OSError` again. None of those
    types is declared by this module, so every one of those paths is caught
    and re-raised as `StoreError`, with the original preserved as `__cause__`
    rather than swallowed. `core.checks.derive.DerivationError` is the model
    this follows.
    """


class SensitiveFieldError(StoreError):
    """Raised when a fact would record an attribute callback refuses to store."""


class DuplicateFactError(StoreError):
    """Raised when a fact identifier already exists."""


def screen_sensitive(text: str, field: str) -> None:
    """Raise `SensitiveFieldError` if `text` matches a denylisted attribute.

    `field` is the dotted/indexed path the text came from (e.g. `"entities[1]"`),
    named in the error so a caller knows which part of the fact to fix.
    """
    for label, pattern in SENSITIVE_PATTERNS:
        if pattern.search(text):
            raise SensitiveFieldError(
                f"refusing to store {label} in {field}: callback does not record this attribute"
            )


def _free_text_fields(fact: Fact) -> Iterator[tuple[str, str]]:
    """Yield every free-text string on `fact` that must be screened, with its field path.

    `Provenance.source` is deliberately excluded: it names where a fact came from
    (a filename or URL), not content the candidate wrote, so screening it would
    reject an import over its own filename rather than over anything sensitive.
    `Dates` is excluded because it never carries free text.
    """
    yield "text", fact.text
    for index, entity in enumerate(fact.entities):
        yield f"entities[{index}]", entity
    for index, derivation in enumerate(fact.derived):
        yield f"derived[{index}].label", derivation.label


def load_facts() -> dict[str, Fact]:
    """Load every stored fact, keyed by id. Returns an empty dict if none exist yet.

    Raises `StoreError` -- and only `StoreError` -- for every way this can
    fail: the data home cannot be resolved, the file cannot be read, its
    content is not valid YAML, its top level is not a mapping of id to fact
    body (a plausible-looking value, such as an empty list, is a corrupt
    store, not an empty one, so it is rejected rather than coerced into
    `{}`), or a stored body no longer validates as a `Fact`.
    """
    try:
        path = resolve_inside(FACTS_FILE)
    except OutsideHomeError as exc:
        raise StoreError(f"cannot resolve {FACTS_FILE!r} inside the data home: {exc}") from exc
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StoreError(f"could not read {path}: {exc}") from exc
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise StoreError(f"{path} is not valid YAML: {exc}") from exc
    if parsed is None:
        parsed = {}
    elif not isinstance(parsed, dict):
        raise StoreError(
            f"{path} must contain a mapping of fact id to fact body, "
            f"found {type(parsed).__name__}: {parsed!r}"
        )
    # `yaml.safe_load` returns `Any`, and `isinstance(parsed, dict)` above only
    # narrows pyright to the parameterless `dict[Unknown, Unknown]` -- the cast
    # is the documented boundary where this module starts trusting the file's
    # shape; `Fact.model_validate` is what actually checks it, just below.
    raw = cast("dict[str, dict[str, object]]", parsed)
    try:
        return {fid: Fact.model_validate(body | {"id": fid}) for fid, body in raw.items()}
    except ValidationError as exc:
        raise StoreError(f"{path} contains a fact that no longer validates: {exc}") from exc


def save_facts(facts: dict[str, Fact]) -> None:
    """Write every fact to disk, keyed by id, in a stable (sorted) order.

    Raises `StoreError` -- and only `StoreError` -- if the data home cannot
    be resolved or the write itself fails.
    """
    try:
        path = resolve_inside(FACTS_FILE)
    except OutsideHomeError as exc:
        raise StoreError(f"cannot resolve {FACTS_FILE!r} inside the data home: {exc}") from exc
    body = {
        fid: fact.model_dump(mode="json", by_alias=True, exclude={"id"})
        for fid, fact in sorted(facts.items())
    }
    try:
        write_private(path, yaml.safe_dump(body, sort_keys=False, allow_unicode=True))
    except OSError as exc:
        raise StoreError(f"could not write {path}: {exc}") from exc


def add_fact(fact: Fact) -> None:
    """Admit `fact` into the store after screening its free text and checking for a duplicate id."""
    for field, value in _free_text_fields(fact):
        screen_sensitive(value, field)
    fact = fact.model_copy(update={"entities": [canonicalize(e) for e in fact.entities]})
    facts = load_facts()
    if fact.id in facts:
        raise DuplicateFactError(f"fact {fact.id!r} already exists")
    facts[fact.id] = fact
    save_facts(facts)
