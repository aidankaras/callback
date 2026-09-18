"""Persistence and admission control for candidate facts.

Two invariants are enforced here rather than by convention:
facts that would record a legally sensitive attribute are refused, and an
identifier is never silently overwritten.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import yaml

from core.paths import resolve_inside, write_private
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


class SensitiveFieldError(ValueError):
    """Raised when a fact would record an attribute callback refuses to store."""


class DuplicateFactError(ValueError):
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
    """Load every stored fact, keyed by id. Returns an empty dict if none exist yet."""
    path = resolve_inside(FACTS_FILE)
    if not path.exists():
        return {}
    raw: dict[str, dict[str, object]] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {fid: Fact.model_validate(body | {"id": fid}) for fid, body in raw.items()}


def save_facts(facts: dict[str, Fact]) -> None:
    """Write every fact to disk, keyed by id, in a stable (sorted) order."""
    path = resolve_inside(FACTS_FILE)
    body = {
        fid: fact.model_dump(mode="json", by_alias=True, exclude={"id"})
        for fid, fact in sorted(facts.items())
    }
    write_private(path, yaml.safe_dump(body, sort_keys=False, allow_unicode=True))


def add_fact(fact: Fact) -> None:
    """Admit `fact` into the store after screening its free text and checking for a duplicate id."""
    for field, value in _free_text_fields(fact):
        screen_sensitive(value, field)
    facts = load_facts()
    if fact.id in facts:
        raise DuplicateFactError(f"fact {fact.id!r} already exists")
    facts[fact.id] = fact
    save_facts(facts)
