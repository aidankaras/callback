"""Persistence and admission control for candidate facts.

Two invariants are enforced here rather than by convention:
facts that would record a legally sensitive attribute are refused, and an
identifier is never silently overwritten.
"""

from __future__ import annotations

import re

import yaml

from core.paths import resolve_inside, write_private
from core.profile.models import Fact

FACTS_FILE = "facts.yaml"

SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("date of birth", re.compile(r"\b(date of birth|d\.?o\.?b\.?)\b", re.I)),
    ("age", re.compile(r"\b(?:i am|aged)\s+\d{2}\s+years old\b", re.I)),
    ("ssn", re.compile(r"\b(ssn|social security)\b|\b\d{3}-\d{2}-\d{4}\b", re.I)),
    ("salary history", re.compile(r"\b(current|previous|prior)\s+salary\b", re.I)),
    ("marital status", re.compile(r"\b(marital status|married|divorced|single parent)\b", re.I)),
    ("religion", re.compile(r"\b(religion|religious affiliation)\b", re.I)),
    ("disability", re.compile(r"\b(disability status|disabled)\b", re.I)),
    ("race or ethnicity", re.compile(r"\b(race|ethnicity|ethnic origin)\b", re.I)),
    ("gender", re.compile(r"\b(gender identity|sex assigned)\b", re.I)),
    (
        "street address",
        re.compile(r"\b\d{1,5}\s+\w+\s+(street|st|avenue|ave|road|rd|drive|dr)\b", re.I),
    ),
)


class SensitiveFieldError(ValueError):
    """Raised when a fact would record an attribute callback refuses to store."""


class DuplicateFactError(ValueError):
    """Raised when a fact identifier already exists."""


def screen_sensitive(text: str) -> None:
    """Raise `SensitiveFieldError` if `text` matches a denylisted attribute."""
    for label, pattern in SENSITIVE_PATTERNS:
        if pattern.search(text):
            raise SensitiveFieldError(
                f"refusing to store {label}: callback does not record this attribute"
            )


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
    """Admit `fact` into the store after screening its text and checking for a duplicate id."""
    screen_sensitive(fact.text)
    facts = load_facts()
    if fact.id in facts:
        raise DuplicateFactError(f"fact {fact.id!r} already exists")
    facts[fact.id] = fact
    save_facts(facts)
