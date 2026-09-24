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

import html
import re
from collections.abc import Iterable, Mapping, Sequence
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
    r"\bu\.?s\.?\s+citizen(?:s|ship)?\b[^.!?]{0,40}\b(?:required|must|only)\b"
    r"|\bmust\s+be\s+(?:a\s+)?u\.?s\.?\s+citizens?\b"
    r"|\bonly\s+u\.?s\.?\s+citizens?\b",
    re.IGNORECASE,
)
_CLEARANCE = re.compile(
    r"\b(?:security )?clearance\b[^.!?]{0,40}\brequired\b"
    r"|\b(?:active|current)\b[^.!?]{0,30}\bclearance\b",
    re.IGNORECASE,
)
_GRADUATION = re.compile(r"\bgraduat\w*\b|\bclass of 20\d\d\b", re.IGNORECASE)
# A year is the graduation's own only when it is named as one.
_GRAD_YEAR = re.compile(
    r"\bclass of\s+(20\d\d)\b"
    r"|\bgraduat\w+\s+(?:in|by|before|after|during|no later than)?\s*(20\d\d)\b",
    re.IGNORECASE,
)
_BETWEEN = re.compile(r"\bbetween\b", re.IGNORECASE)
_LATEST = re.compile(r"\b(?:by|before|no later than|prior to|on or before)\b", re.IGNORECASE)
_EARLIEST = re.compile(r"\b(?:after|on or after|from|starting|since)\b", re.IGNORECASE)
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


def _grad_bounds(sentence: str) -> tuple[str | None, str | None] | None:
    r"""The graduation window a sentence allows, as `(earliest, latest)` months,
    either of which is None when that side is open.

    Direction decides the shape. "graduating by June 2027" and "must graduate
    no later than December 2026" bound only the late side: a candidate who
    finishes sooner qualifies, and reading those as a closed window at the
    stated month knocked them out. "after May 2026" bounds only the early
    side. Two dates, or none of these words, is a closed window.

    A bare year counts only where it belongs to the graduation itself --
    "class of 2027", "graduating in 2027" -- because any `20\d\d` in a
    sentence that merely mentions graduates is not a requirement: "we have
    hired new graduates every year since 2015" bounded everyone out of a role.
    """
    months = iso_month_dates(sentence)
    if months:
        low, high = min(months), max(months)
    else:
        found = _GRAD_YEAR.search(sentence)
        if not found:
            return None
        year = found.group(1) or found.group(2)
        low, high = f"{year}-01", f"{year}-12"
    if len(months) > 1 or _BETWEEN.search(sentence):
        return low, high
    if _LATEST.search(sentence):
        return None, high
    if _EARLIEST.search(sentence):
        return low, None
    return low, high


def _candidate_grad(graduation: str) -> str | None:
    months = iso_month_dates(graduation)
    if months:
        return months[0]
    year = _YEAR.search(graduation)
    return f"{year.group(1)}-06" if year else None


def _refuses(sentence: str) -> bool:
    """Whether `sentence` refuses sponsorship somewhere an offer does not cover.
    Judged by span, not by sentence: in a posting with no sentence breaks the
    whole text is one sentence, and "we sponsor some roles" early on must not
    erase "we cannot sponsor this one" later."""
    offers = [m.span() for m in _OFFERS_SPONSORSHIP.finditer(sentence)]
    return any(
        not any(a < end and start < b for a, b in offers)
        for start, end in (m.span() for m in _REFUSES_SPONSORSHIP.finditer(sentence))
    )


def check_eligibility(text: str, profile: Profile) -> Eligibility:
    """Every eligibility requirement `text` states, judged against `profile`."""
    findings: list[Finding] = []
    auth = profile.work_authorization

    def add(rule: str, level: Level, quote: str, reason: str) -> None:
        if not any(f.rule == rule for f in findings):
            findings.append(Finding(rule, level, quote.strip(), reason))

    # Board content arrives as HTML more often than not: "&nbsp;" and tags would
    # otherwise sit between the words every rule below expects to be adjacent.
    plain = re.sub(r"<[^>]+>", " ", html.unescape(text))
    normalised = re.sub(r"\bU\.S\.(?=\s|$)", "US", plain)
    # A posting that says it sponsors and also that it cannot always is a
    # question for someone who needs sponsorship, not a refusal.
    offers = _OFFERS_SPONSORSHIP.search(normalised) is not None
    for sentence in (s for s in _SENTENCE_BREAK.split(normalised) if s.strip()):
        if _refuses(sentence):
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
            elif (bounds[0] is not None and mine < bounds[0]) or (
                bounds[1] is not None and mine > bounds[1]
            ):
                window = f"{bounds[0] or 'any'}..{bounds[1] or 'any'}"
                add(
                    "graduation",
                    "knockout",
                    sentence,
                    f"graduation {window}; profile: {profile.graduation}",
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


@dataclass(frozen=True)
class Fit:
    """What one posting asks for, against what the candidate can evidence."""

    eligibility: Eligibility
    evidenced: tuple[tuple[str, tuple[str, ...]], ...]
    missing: tuple[str, ...]

    @property
    def coverage(self) -> float:
        asked = len(self.evidenced) + len(self.missing)
        return len(self.evidenced) / asked if asked else 0.0


def fit(text: str, profile: Profile, facts: Mapping[str, Fact]) -> Fit:
    """Judge one posting's text: its knockouts, and which technologies it names
    that confirmed facts back.

    Each evidenced technology carries the ids of the facts that state it, which
    are the facts a bullet about it may cite. A technology the alias table does
    not know is not counted either way: the same rule the ranking uses, so a
    posting's prose cannot inflate or dilute its own coverage.
    """
    backing: dict[str, list[str]] = {}
    for fid, entry in sorted(facts.items()):
        if not entry.selectable():
            continue
        for entity in entry.entities:
            backing.setdefault(canonicalize(entity), []).append(fid)
    asked = _technologies(text)
    evidenced = tuple((tech, tuple(backing[tech])) for tech in sorted(asked & backing.keys()))
    return Fit(
        eligibility=check_eligibility(text, profile),
        evidenced=evidenced,
        missing=tuple(sorted(asked - backing.keys())),
    )


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


def named_by(technologies: Iterable[str], texts: Iterable[str]) -> dict[str, int]:
    """For each of `technologies`, how many of `texts` name it, read the way `gaps` reads."""
    counts = dict.fromkeys(technologies, 0)
    for text in texts:
        for tech in _technologies(text) & counts.keys():
            counts[tech] += 1
    return counts
