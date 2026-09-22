"""Whether a posting is worth applying to, before any tailoring is done.

**Eligibility** reads the posting's own words for the requirements that
silently end an application -- a refusal to sponsor, a citizenship or
clearance requirement, a graduation window -- and compares each with the
candidate's `Profile`. Every finding quotes the sentence it came from, and
has one of three levels:

- `knockout` -- the posting excludes this candidate as stated.
- `unknown` -- it states a requirement the profile says nothing about, so
  it is a question for the candidate, never a guess.
- `stretch` -- a graduate degree or several years of experience asked for;
  reachable, and worth knowing before spending effort.

The rules read negation: "we do sponsor visas" is not a refusal, and on a
live board it was the only sponsorship sentence present. This is advice.
Nothing here gates a document.

**Ranking** orders stored postings by how much of what they ask for the
candidate can evidence, and by how fresh they are, with knockouts last.
Coverage counts only technologies the alias table recognises in the
posting, backed by the candidate's confirmed facts, so it is a signal, not a
judgement of fit; the skill weighs the rest.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError

from core.aliases import ALIAS_TABLE, canonicalize
from core.checks.grounding import checkable_entity_tokens, iso_month_dates
from core.discovery.store import Found
from core.paths import resolve_inside, write_private
from core.profile.models import Fact

PROFILE_FILE = "profile.yaml"
Level = Literal["knockout", "unknown", "stretch"]
DegreeLevel = Literal["high school", "associate", "bachelor", "master", "phd"]
FRESH_DAYS = 30
STRETCH_YEARS = 2

# A sentence ends at a stop followed by a capital, or at a line break, so an
# abbreviation such as "U.S." does not split one; "U.S." is also folded to
# "US" first so the rules below need not spell both forms.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z])|\n+")
_REFUSES_SPONSORSHIP = re.compile(
    r"\b(?:unable|not able|aren't able|are not able|isn't able|cannot|can't|will not|won't"
    r"|do not|does not|don't|not)\b"
    r"[^.!?]{0,40}\b(?:sponsor\w*|eligible for (?:visa )?sponsorship)"
    r"|\bwithout (?:the need for )?(?:visa )?sponsorship\b",
    re.IGNORECASE,
)
# A positive statement, with no negation between the subject and the verb.
_OFFERS_SPONSORSHIP = re.compile(
    r"\bwe\s+(?:do|can|will|may|are able to|are happy to)?\s*sponsor", re.IGNORECASE
)
_CITIZENSHIP = re.compile(
    r"\bu\.?s\.? citizen(?:ship)?\b[^.!?]{0,40}\b(?:required|must|only)\b"
    r"|\bmust be (?:a )?u\.?s\.? citizen\b",
    re.IGNORECASE,
)
_CLEARANCE = re.compile(
    r"\b(?:security )?clearance\b[^.!?]{0,40}\brequired\b"
    r"|\b(?:active|current)\b[^.!?]{0,30}\bclearance\b",
    re.IGNORECASE,
)
_GRADUATION = re.compile(r"\bgraduat\w*\b|\bclass of 20\d\d\b", re.IGNORECASE)
_YEAR = re.compile(r"\b(20\d\d)\b")
_GRAD_DEGREE = re.compile(r"\b(?:ph\.?d|master'?s|m\.?s\.?(?=/|\s+or)|graduate degree)\b", re.I)
_YEARS = re.compile(r"\b(\d{1,2})\+?\s*(?:or more\s+)?years?\b[^.!?]{0,40}\bexperience\b", re.I)
_NEEDS_SPONSORSHIP = re.compile(r"\b(?:f-?1|opt|h-?1b|visa|sponsor\w*|work permit)\b", re.I)
_DEGREE_RANK = {"high school": 0, "associate": 1, "bachelor": 2, "master": 3, "phd": 4}


class Profile(BaseModel):
    """What eligibility is judged against. Every field may be unknown."""

    model_config = {"extra": "forbid", "frozen": True}

    work_authorization: str | None = None
    graduation: str | None = None
    degree: DegreeLevel | None = None


@dataclass(frozen=True)
class Finding:
    rule: str
    level: Level
    quote: str
    reason: str


@dataclass(frozen=True)
class Eligibility:
    findings: tuple[Finding, ...] = ()

    @property
    def verdict(self) -> Literal["eligible", "knockout", "unknown", "stretch"]:
        levels = {f.level for f in self.findings}
        for level in ("knockout", "unknown", "stretch"):
            if level in levels:
                return level  # type: ignore[return-value]
        return "eligible"


def load_profile() -> Profile:
    path = resolve_inside(PROFILE_FILE)
    if not path.exists():
        return Profile()
    try:
        return Profile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ValueError(f"{path} is not a readable profile: {exc}") from exc


def save_profile(profile: Profile) -> None:
    body = yaml.safe_dump(profile.model_dump(exclude_none=True), sort_keys=False)
    write_private(resolve_inside(PROFILE_FILE), body)


def _is_citizen(auth: str) -> bool:
    return re.search(r"\b(?:us|u\.s\.|united states)\s+citizen\b", auth, re.I) is not None


def _grad_bounds(sentence: str) -> tuple[str, str] | None:
    """The earliest and latest graduation month a sentence allows, as YYYY-MM."""
    months = iso_month_dates(sentence)
    if months:
        return min(months), max(months)
    years = _YEAR.findall(sentence)
    if years:
        return f"{min(years)}-01", f"{max(years)}-12"
    return None


def _candidate_grad(graduation: str) -> str | None:
    months = iso_month_dates(graduation)
    if months:
        return months[0]
    year = _YEAR.search(graduation)
    return f"{year.group(1)}-06" if year else None


def check_eligibility(text: str, profile: Profile) -> Eligibility:
    """Every eligibility requirement `text` states, judged against `profile`."""
    findings: list[Finding] = []
    auth = profile.work_authorization

    def add(rule: str, level: Level, quote: str, reason: str) -> None:
        if not any(f.rule == rule for f in findings):
            findings.append(Finding(rule, level, quote.strip(), reason))

    normalised = re.sub(r"\bU\.S\.(?=\s|$)", "US", text)
    # A posting that says it sponsors and also that it cannot always is a
    # question for someone who needs sponsorship, not a refusal.
    offers = _OFFERS_SPONSORSHIP.search(normalised) is not None
    for sentence in (s for s in _SENTENCE_BREAK.split(normalised) if s.strip()):
        if _REFUSES_SPONSORSHIP.search(sentence) and not _OFFERS_SPONSORSHIP.search(sentence):
            needs = auth is not None and _NEEDS_SPONSORSHIP.search(auth) and not _is_citizen(auth)
            if offers and needs:
                add("sponsorship", "unknown", sentence, "sponsors some roles; ask whether this one")
            elif auth is None:
                add(
                    "sponsorship",
                    "unknown",
                    sentence,
                    "does your work authorization need sponsorship?",
                )
            elif _NEEDS_SPONSORSHIP.search(auth) and not _is_citizen(auth):
                add("sponsorship", "knockout", sentence, f"will not sponsor; profile: {auth}")
        if _CITIZENSHIP.search(sentence):
            if auth is None:
                add("citizenship", "unknown", sentence, "are you a US citizen?")
            elif not _is_citizen(auth):
                add(
                    "citizenship", "knockout", sentence, f"requires US citizenship; profile: {auth}"
                )
        if _CLEARANCE.search(sentence):
            if auth is not None and not _is_citizen(auth):
                add("clearance", "knockout", sentence, "a US clearance requires US citizenship")
            else:
                add(
                    "clearance",
                    "unknown",
                    sentence,
                    "do you hold, or can you obtain, this clearance?",
                )
        if _GRADUATION.search(sentence) and (bounds := _grad_bounds(sentence)):
            mine = _candidate_grad(profile.graduation) if profile.graduation else None
            if mine is None:
                add("graduation", "unknown", sentence, "when do you expect to graduate?")
            elif not bounds[0] <= mine <= bounds[1]:
                add(
                    "graduation",
                    "knockout",
                    sentence,
                    f"graduation {bounds[0]}..{bounds[1]}; profile: {profile.graduation}",
                )
        if _GRAD_DEGREE.search(sentence) and (
            profile.degree is None or _DEGREE_RANK[profile.degree] < _DEGREE_RANK["master"]
        ):
            add("degree", "stretch", sentence, "asks for a graduate degree")
        years = [int(y) for y in _YEARS.findall(sentence)]
        if years and max(years) >= STRETCH_YEARS:
            add("experience", "stretch", sentence, f"asks for {max(years)}+ years of experience")
    return Eligibility(findings=tuple(findings))


def _technologies(text: str) -> set[str]:
    return {canonicalize(t) for t in checkable_entity_tokens(text) if t.lower() in ALIAS_TABLE}


@dataclass(frozen=True)
class Ranked:
    posting: Found
    score: float
    eligibility: Eligibility
    coverage: tuple[list[str], list[str]] = field(default=([], []))
    age_days: int = 0


def rank(
    postings: Sequence[Found], profile: Profile, facts: Mapping[str, Fact], *, now: datetime
) -> list[Ranked]:
    """`postings`, best first: knockouts last, then by evidenced coverage and freshness."""
    evidenced = {canonicalize(e) for f in facts.values() if f.selectable() for e in f.entities}
    ranked: list[Ranked] = []
    for posting in postings:
        eligibility = check_eligibility(posting.text, profile)
        asked = _technologies(posting.text)
        have = sorted(asked & evidenced)
        missing = sorted(asked - evidenced)
        coverage = len(have) / len(asked) if asked else 0.0
        age = max(0, (now - posting.first_seen).days)
        freshness = max(0.0, 1 - age / FRESH_DAYS)
        score = 0.6 * coverage + 0.4 * freshness
        if eligibility.verdict == "knockout":
            score -= 10
        elif eligibility.verdict == "unknown":
            score -= 0.1
        ranked.append(Ranked(posting, round(score, 3), eligibility, (have, missing), age))
    return sorted(ranked, key=lambda r: r.score, reverse=True)


def gaps(
    postings: Sequence[Found], profile: Profile, facts: Mapping[str, Fact]
) -> list[tuple[str, int]]:
    """Technologies the eligible postings name that confirmed facts do not back,
    most frequent first. A posting the candidate is excluded from is left out:
    what only it asks for is not worth learning for this search."""
    evidenced = {canonicalize(e) for f in facts.values() if f.selectable() for e in f.entities}
    counts: dict[str, int] = {}
    for posting in postings:
        if check_eligibility(posting.text, profile).verdict == "knockout":
            continue
        for tech in _technologies(posting.text) - evidenced:
            counts[tech] = counts.get(tech, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
