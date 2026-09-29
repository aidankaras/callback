"""Structural grounding for what bullet grounding cannot see: headers, identity, skills.

Bullet grounding checks a bullet's own claims against its cited facts. This
module checks everything else a resume asserts, and holds it to a stricter
rule than prose can be held to, because none of it is prose.

**An entry header must be one cited fact, field for field.** Exactly one
cited fact of kind experience, education or project must have an
`organization` equal to the heading, a `title` equal to the subheading, a
`location` equal to the location, and dates equal to the entry's dates.
Every field is compared against that same fact. Checking each field against
*any* cited fact let one entry take its employer from one fact and its dates
from another -- an education fact's start date spliced onto a job inflated
its tenure -- and matching against free text let "reported to the Chief
Technology Officer" become the candidate's own title. A title must be equal,
not contained: dropping "Intern" from "Data Engineering Intern" is
inflation, not shortening.

**A bullet cites only its own entry's facts**: the entry's `sources`, or a
fact whose `parent` is one of them. Otherwise a percentage from one job can
back a bullet under another.

**The identity block** -- name, email, phone, each link -- must equal an
entity of a confirmed `links` fact. It is otherwise free text rendered at the
top of the page, where a credential appended to a name or a clearance listed
as a "link" is the first thing a reader sees.

**Section titles** come from a fixed list (`SECTION_TITLES`). A title is
rendered in bold capitals, and "Experience - Forbes 30 Under 30" is a claim.

**Skills**: every listed skill must be backed by a confirmed fact -- a
`skill` fact naming it, or an entity of a fact a bullet cites -- compared
through the alias table, so "k8s" and "Kubernetes" are one skill.

Every rule fails closed. Only confirmed facts count, matching `Fact.selectable`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from core.aliases import canonicalize
from core.checks.grounding import iso_month_dates
from core.profile.models import Fact, FactKind, Identity
from core.resume.models import Entry, ResumeDoc

_YEAR = re.compile(r"^\d{4}$")
_HEADER_KINDS = frozenset({FactKind.EXPERIENCE, FactKind.EDUCATION, FactKind.PROJECT})

SECTION_TITLES = frozenset(
    {
        "experience",
        "work experience",
        "professional experience",
        "research experience",
        "relevant experience",
        "teaching experience",
        "leadership experience",
        "volunteer experience",
        "education",
        "projects",
        "selected projects",
        "research",
        "skills",
        "technical skills",
        "languages",
        "tools",
        "certifications",
        "publications",
        "leadership",
        "activities",
        "awards",
        "honors",
        "honors and awards",
        "coursework",
        "relevant coursework",
    }
)


@dataclass(frozen=True)
class ClaimsResult:
    """One finding per unsupported claim; the verdict follows from them."""

    findings: tuple[str, ...] = ()

    @property
    def verdict(self) -> Literal["pass", "fail"]:
        return "fail" if self.findings else "pass"


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _canon(term: str) -> str:
    return _norm(canonicalize(term))


def _date_ok(value: str, fact_date: str | None, *, is_end: bool) -> bool:
    """Whether a header date names the same month (or year) as `fact_date`."""
    if _norm(value) == "present":
        return is_end and fact_date is None
    if fact_date is None:
        return False
    if _YEAR.match(value.strip()):
        return value.strip() == fact_date[:4]
    parsed = iso_month_dates(value)
    return len(parsed) == 1 and parsed[0] == fact_date[:7]


def _mismatches(entry: Entry, fact: Fact) -> list[str]:
    """Every header field of `entry` that `fact` does not state identically."""
    wrong: list[str] = []
    for name, claimed, stated in (
        ("title", entry.subheading, fact.title),
        ("location", entry.location, fact.location),
    ):
        if claimed is not None and (stated is None or _norm(claimed) != _norm(stated)):
            wrong.append(f"{name} {claimed!r} (fact says {stated!r})")
    start = fact.dates.start if fact.dates else None
    end = fact.dates.end if fact.dates else None
    if entry.start and (fact.dates is None or not _date_ok(entry.start, start, is_end=False)):
        wrong.append(f"start date {entry.start!r} (fact says {start!r})")
    if entry.end and (fact.dates is None or not _date_ok(entry.end, end, is_end=True)):
        wrong.append(f"end date {entry.end!r} (fact says {end or 'no end'!r})")
    return wrong


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
    anchors = [
        f
        for f in cited
        if f.kind in _HEADER_KINDS
        and f.organization is not None
        and _norm(f.organization) == _norm(entry.heading)
    ]
    if not anchors:
        findings.append(
            f"{label}: employer {entry.heading!r} is not the organization of any cited "
            "experience, education or project fact"
        )
    elif all(_mismatches(entry, f) for f in anchors):
        findings.append(
            f"{label}: header does not match its fact: " + "; ".join(_mismatches(entry, anchors[0]))
        )

    own = set(entry.sources)
    for n, bullet in enumerate(entry.bullets):
        foreign = [
            s
            for s in bullet.sources
            if s not in own and (s not in facts or facts[s].parent not in own)
        ]
        if foreign:
            findings.append(
                f"{label}: bullet {n} cites fact(s) that belong to no fact of this entry {foreign}"
            )
    return findings


def _stated_identity(facts: Mapping[str, Fact]) -> set[str]:
    return {
        _norm(e)
        for f in facts.values()
        if f.selectable() and f.kind is FactKind.LINKS
        for e in f.entities
    }


def name_findings(name: str, facts: Mapping[str, Fact]) -> list[str]:
    """Whether `name` is the candidate's name as a confirmed links fact states it."""
    if _norm(name) in _stated_identity(facts):
        return []
    return [f"candidate name {name!r} is not an entity of any confirmed links fact"]


def identity_findings(identity: Identity, facts: Mapping[str, Fact]) -> list[str]:
    """Every identity value -- name, email, phone, link -- that no confirmed
    links fact states. The header is rendered as-is, so it is a claim too."""
    stated = _stated_identity(facts)
    values = [("name", identity.name), ("email", identity.email)]
    if identity.phone:
        values.append(("phone", identity.phone))
    values += [("link", link) for link in identity.links]
    return [
        f"identity {name} {value!r} is not an entity of any confirmed links fact"
        for name, value in values
        if _norm(value) not in stated
    ]


def _skill_backing(doc: ResumeDoc, facts: Mapping[str, Fact]) -> set[str]:
    """Canonical forms of every skill a confirmed fact supports."""
    confirmed = {fid: f for fid, f in facts.items() if f.selectable()}
    backed: set[str] = set()
    for f in confirmed.values():
        if f.kind is FactKind.SKILL:
            backed.add(_canon(f.text))
            backed |= {_canon(e) for e in f.entities}
    for section in doc.sections:
        for entry in section.entries:
            for bullet in entry.bullets:
                for fid in bullet.sources:
                    if fid in confirmed:
                        backed |= {_canon(e) for e in confirmed[fid].entities}
    return backed


def skills_shown_by_entries(
    skills: Iterable[str], doc: ResumeDoc, facts: Mapping[str, Fact]
) -> set[str]:
    """Those of `skills` a printed entry stands behind: the ones a confirmed
    fact belonging to an entry on the page lists in its entities.

    A fact belongs to an entry when the entry's header or one of its bullets
    cites it, or when it shares an ancestor with a fact they cite: a bullet
    that cites one detail of a role puts that role on the page, and the role's
    other details are facts about the same entry. A role's stack is usually
    recorded once, in a fact no bullet quotes, and a bullet written to name
    every tool reads as a keyword list.

    Entities only, never a fact's text: entities are where a fact records what
    was used, and its text may name a tool it moved away from. A fact kept on
    its own, under no entry, shows a skill nowhere on the page however true it
    is, so it backs a listed skill (`_skill_backing`) without appearing here.
    """
    confirmed = {fid: f for fid, f in facts.items() if f.selectable()}

    def lineage(fid: str) -> list[str]:
        """`fid` and its ancestors, nearest first, among confirmed facts."""
        seen: list[str] = []
        current: str | None = fid
        while current is not None and current in confirmed and current not in seen:
            seen.append(current)
            current = confirmed[current].parent
        return seen

    cited = {
        fid
        for section in doc.sections
        for entry in section.entries
        for fid in (*entry.sources, *(s for b in entry.bullets for s in b.sources))
    }
    on_page = {ancestor for fid in cited for ancestor in lineage(fid)}
    listed = {
        _canon(entity)
        for fid, fact in confirmed.items()
        if on_page.intersection(lineage(fid))
        for entity in fact.entities
    }
    return {skill for skill in skills if _canon(skill) in listed}


def check_claims(doc: ResumeDoc, facts: Mapping[str, Fact]) -> ClaimsResult:
    """Check identity, section titles, every entry header and every listed skill."""
    findings = identity_findings(doc.identity, facts)
    index = 0
    for section in doc.sections:
        if _norm(section.title) not in SECTION_TITLES:
            findings.append(f"section title {section.title!r} is not a recognised section name")
        for entry in section.entries:
            findings.extend(_check_entry(f"entry {index} ({entry.heading})", entry, facts))
            index += 1
    backed = _skill_backing(doc, facts)
    for section in doc.sections:
        for skill in section.skills:
            if _canon(skill) not in backed:
                findings.append(f"skill {skill!r} is backed by no confirmed fact")
    return ClaimsResult(findings=tuple(findings))
