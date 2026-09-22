"""Structural grounding for what bullet grounding cannot see: entry headers and skills.

Bullet grounding checks a bullet's numbers, dates and recognised technical
terms against its cited facts. It deliberately does not check ordinary
words, because a bullet is rephrased prose. That leaves a gap exactly where
a tailored resume is most tempting to embellish: an entry header. Measured
against the bullet rule, a header reading "Senior Data Engineer" at
"Acme Quant Capital" grounds cleanly against a fact that says "Data
Engineer at Northwind Analytics", because neither "Senior" nor "Acme" is a
token that rule inspects.

Headers are not prose, so they can be held to a much stricter rule than
bullets can:

- the employer (`Entry.heading`) must *equal* an entity of a cited fact --
  not merely appear in its text, where "partnered with Acme" would let a
  swapped employer through;
- the title and location must appear in a cited fact as a whole-word run,
  so a title can be shortened ("Engineer") but not inflated;
- each date must equal the cited fact's own date, "Present" is accepted
  only for a role whose fact has no end, and a date in any form this module
  cannot parse fails rather than being skipped.

A skills list carries no sources of its own, so every listed skill must be
backed by a confirmed fact: either a `skill` fact naming it, or an entity of
a fact cited by one of the document's bullets. Both are compared through
the alias table, so "k8s" and "Kubernetes" are the same skill. Bullets are
not required to have passed grounding here; a bullet that fails grounding
fails the document through that gate on its own.

Every rule fails closed. Only confirmed facts count, matching
`Fact.selectable`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from core.aliases import canonicalize
from core.checks.grounding import iso_month_dates
from core.profile.models import Fact, FactKind
from core.resume.models import Entry, ResumeDoc

_YEAR = re.compile(r"^\d{4}$")


@dataclass(frozen=True)
class ClaimsResult:
    """The gate's verdict and one finding per unsupported claim."""

    verdict: Literal["pass", "fail"]
    findings: list[str] = field(default_factory=list[str])


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _contains_words(value: str, text: str) -> bool:
    """True if `value` occurs in `text` as a whole-word run, ignoring case
    and whitespace differences. "Intern" does not occur in "internal"."""
    pattern = r"(?<!\w)" + re.escape(_norm(value)) + r"(?!\w)"
    return re.search(pattern, _norm(text)) is not None


def _canon(term: str) -> str:
    return _norm(canonicalize(term))


def _date_ok(value: str, fact: Fact, *, is_end: bool) -> bool:
    """Whether a header date names the same month (or year) as `fact`'s."""
    if fact.dates is None:
        return False
    fact_date = fact.dates.end if is_end else fact.dates.start
    if _norm(value) == "present":
        return is_end and fact_date is None
    if fact_date is None:
        return False
    if _YEAR.match(value.strip()):
        return value.strip() == fact_date[:4]
    parsed = iso_month_dates(value)
    return len(parsed) == 1 and parsed[0] == fact_date[:7]


def _check_entry(label: str, entry: Entry, facts: Mapping[str, Fact]) -> list[str]:
    if not entry.sources:
        return [f"{label}: header cites no fact"]
    unknown = [s for s in entry.sources if s not in facts]
    if unknown:
        return [f"{label}: header cites unknown fact(s) {unknown}"]
    cited = [facts[s] for s in entry.sources]
    unconfirmed = [f.id for f in cited if not f.selectable()]
    if unconfirmed:
        return [f"{label}: header cites fact(s) not confirmed by the candidate {unconfirmed}"]

    findings: list[str] = []
    entities = {_norm(e) for f in cited for e in f.entities}
    if _norm(entry.heading) not in entities:
        findings.append(f"{label}: employer {entry.heading!r} is not an entity of any cited fact")
    for name, value in (("title", entry.subheading), ("location", entry.location)):
        if value and not any(
            _contains_words(value, f.text) or _norm(value) in {_norm(e) for e in f.entities}
            for f in cited
        ):
            findings.append(f"{label}: {name} {value!r} appears in no cited fact")

    for name, value, is_end in (("start date", entry.start, False), ("end date", entry.end, True)):
        if value and not any(_date_ok(value, f, is_end=is_end) for f in cited):
            findings.append(f"{label}: {name} {value!r} does not match any cited fact's dates")
    return findings


def _skill_backing(doc: ResumeDoc, facts: Mapping[str, Fact]) -> set[str]:
    """Canonical forms of every skill a confirmed fact supports."""
    confirmed = [f for f in facts.values() if f.selectable()]
    backed = {_canon(f.text) for f in confirmed if f.kind is FactKind.SKILL}
    backed |= {_canon(e) for f in confirmed if f.kind is FactKind.SKILL for e in f.entities}
    cited: Iterable[str] = (
        s
        for section in doc.sections
        for entry in section.entries
        for bullet in entry.bullets
        for s in bullet.sources
    )
    for fid in cited:
        f = facts.get(fid)
        if f is not None and f.selectable():
            backed |= {_canon(e) for e in f.entities}
    return backed


def check_claims(doc: ResumeDoc, facts: Mapping[str, Fact]) -> ClaimsResult:
    """Check every entry header and every listed skill against confirmed facts."""
    findings: list[str] = []
    index = 0
    for section in doc.sections:
        for entry in section.entries:
            findings.extend(_check_entry(f"entry {index} ({entry.heading})", entry, facts))
            index += 1
    backed = _skill_backing(doc, facts)
    for section in doc.sections:
        for skill in section.skills:
            if _canon(skill) not in backed:
                findings.append(f"skill {skill!r} is backed by no confirmed fact")
    return ClaimsResult(verdict="fail" if findings else "pass", findings=findings)
