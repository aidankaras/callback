"""Entity canonicalisation.

Canonicalisation happens once, when a fact is written, so every later
comparison is between canonical forms. Resolving aliases at check time would
make a gate's verdict depend on a lookup the caller could influence.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml

_TABLE_PATH = Path(__file__).with_name("table.yaml")


@cache
def _load() -> tuple[dict[str, str], dict[str, set[str]]]:
    raw: dict[str, list[str]] = yaml.safe_load(_TABLE_PATH.read_text(encoding="utf-8"))
    alias_to_canonical: dict[str, str] = {}
    canonical_to_aliases: dict[str, set[str]] = {}
    for canonical, aliases in raw.items():
        forms = {canonical, *aliases}
        canonical_to_aliases[canonical] = forms
        for form in forms:
            alias_to_canonical[form.lower()] = canonical
    return alias_to_canonical, canonical_to_aliases


# The lookup table sits above the functions that use it, per convention. It is
# built once from `_load`'s cached result, so every caller (this module's own
# `canonicalize`/`synonyms`, and `core.checks.grounding`, which imports it
# directly) shares the same dict rather than re-parsing the table file.
ALIAS_TABLE: dict[str, str] = _load()[0]


def canonicalize(entity: str) -> str:
    """Return the canonical form of an entity, or the entity unchanged if not aliased.

    Performs case-insensitive lookup and strips leading/trailing whitespace.
    """
    alias_to_canonical, _ = _load()
    return alias_to_canonical.get(entity.strip().lower(), entity.strip())


def synonyms(canonical: str) -> set[str]:
    """Return all known forms (canonical + aliases) of a canonical entity name.

    Returns a set containing only the canonical form if it is not in the table.
    Always a fresh copy: the cached table backs every caller, so returning the
    stored set itself would let one caller's mutation corrupt what every later
    call sees.
    """
    _, canonical_to_aliases = _load()
    return set(canonical_to_aliases.get(canonical, {canonical}))
