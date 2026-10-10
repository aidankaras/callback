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

A degree sentence that names the candidate's own level among others
("pursuing a BS, MS or PhD", "juniors, seniors or PhD students") admits
them, and clears any degree finding from another sentence: postings also
mention degrees in passing ("join our team of PhD students"). One naming
only higher levels is a stretch, with its own reason when it is about
current students of them ("PhD candidates"). It is a knockout only when it
also states a requirement ("must", "required", "only") with no negation and
no hedge ("Open to current PhD students only"): a knockout hides a posting,
so a mention, a refusal ("not open to PhD students") or a preference must
never be read as one.

The rules read negation: "we do sponsor visas" is not a refusal, and on a
live board it was the only sponsorship sentence present. This is advice.
Nothing here gates a document.

**Ranking** orders stored postings by how much of what they ask for the
candidate can evidence, and by how fresh they are, with knockouts last.
Coverage counts only technologies the alias table recognises in the
posting, backed by the candidate's confirmed facts, so it is a signal, not a
judgement of fit; the skill weighs the rest. For ranking it is shrunk
toward zero by `COVERAGE_PRIOR` phantom technologies the candidate lacks:
most postings name zero to two recognised technologies, so a plain ratio put
every posting that named one the candidate has at a perfect score, and on a
real board most of the list tied at the top. Shrunk, "4 of 4" (0.67)
outranks "1 of 1" (0.33).
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from core.aliases import ALIAS_TABLE, canonicalize
from core.checks.grounding import checkable_entity_tokens, iso_month_dates
from core.discovery.store import Found
from core.paths import resolve_inside, write_private
from core.profile.models import Fact

PROFILE_FILE = "profile.yaml"
Level = Literal["knockout", "unknown", "stretch"]
_SEVERITY: dict[Level, int] = {"knockout": 0, "unknown": 1, "stretch": 2}
DegreeLevel = Literal["high school", "associate", "bachelor", "master", "phd"]
Season = Literal["summer", "fall", "winter", "spring"]
FRESH_DAYS = 30
STRETCH_YEARS = 2
COVERAGE_PRIOR = 2

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
# Year-of-study and college words count as undergraduate only in a list
# ("juniors, seniors or PhD students", "a four-year university or PhD
# program"): an admitting match clears every degree finding in the posting,
# so "a PhD program at an accredited university" or "senior engineers" must
# not count. A singular year word is often an adjective ("junior or senior
# engineers"), so a list of them followed by a colleague noun does not count.
# The test is that denylist rather than an allowlist ending in "student"
# because the two errors cost differently: a false admission leaves a posting
# on the list, while a missed one can make a knockout of "a rising junior or
# senior, or a current PhD student" and hide a posting the candidate qualifies
# for. Several colleague words are also adjectives ("software engineering
# majors"), so one followed by a student noun in the same clause does not count.
_LIST_SEP = r"\s*(?:,\s*(?:or\b|and\b)?|/|or\b|and\b)\s*"
_RANK_WORD = r"(?:juniors?|seniors?|sophomores?|staff|principal|lead|mid-level)"
_COLLEAGUE = (
    r"(?:engineer|engineering|researcher|scientist|developer|analyst|staff|member|colleague"
    r"|leader|leadership|manager|management|role|position|level|team|people|professional"
    r"|mentor|trader|quant|designer|associate|partner|executive|hire|talent|software|data"
    r"|technical|technologist|employee|consultant|investor|banker|portfolio|stakeholder)\w*"
)
_NOT_A_STUDENT_NOUN = r"(?![^.,;]{0,40}?\b(?:students?|majors?|undergrad\w*|candidates?)\b)"
_YEAR_OF_STUDY = (
    r"(?:juniors|seniors)(?=\s*(?:,|/|or\b|and\b))"
    rf"|(?:junior|senior|sophomore)(?={_LIST_SEP})"
    rf"(?!(?:{_LIST_SEP}{_RANK_WORD})*\s+{_COLLEAGUE}\b{_NOT_A_STUDENT_NOUN})"
)
_DEGREE_LEVELS: dict[DegreeLevel, re.Pattern[str]] = {
    "bachelor": re.compile(
        rf"\b(?:bachelor|undergrad|b\.?\s?sc?\b|{_YEAR_OF_STUDY}"
        r"|four-year (?:college|university)|of college\b|college (?:students?|juniors?|seniors?))",
        re.I,
    ),
    "master": re.compile(
        r"\bmaster[\u2019']?s\b|\bm\.?\s?sc\b|\bm\.?s\.?(?=/|,|\s+or|\s+students?)"
        r"|\bgraduate degree\b",
        re.I,
    ),
    "phd": re.compile(r"\bph\.?\s?d\b", re.I),
}
# A current student of a degree, not a holder of one: "enrolled in a PhD
# program", "PhD candidates", each adjacent to the degree.
_STUDENT_OF = re.compile(
    r"\b(?:enrolled in|pursuing)\s+(?:an?\s+)?(?:ph\.?\s?d|master)"
    r"|\b(?:ph\.?\s?d\.?|master[\u2019']?s)\s+(?:candidates?|students?|programs?)",
    re.I,
)
# Not "eligible": "Masters students are also eligible" widens who may apply.
_REQUIRED = re.compile(r"\b(?:must|required|only)\b", re.I)
_NEGATED = re.compile(r"\b(?:not|no|never|ineligible)\b", re.I)
_HEDGED = re.compile(
    r"\b(?:typical(?:ly)?|usually|often|preferred|preferably|ideal(?:ly)?|a plus"
    r"|nice to have|encouraged|welcome)\b",
    re.I,
)
# A range counts from its low end: "0-2 years" admits someone with none.
_YEARS = re.compile(
    r"\b(\d{1,2})(?:\s*(?:[-\u2013\u2014]|to|and)\s*\d{1,2})?\+?\s*(?:or more\s+)?years?\b"
    r"[^.!?]{0,40}\bexperience\b",
    re.I,
)
_NEEDS_SPONSORSHIP = re.compile(r"\b(?:f-?1|opt|h-?1b|visa|sponsor\w*|work permit)\b", re.I)
_DEGREE_RANK = {"high school": 0, "associate": 1, "bachelor": 2, "master": 3, "phd": 4}
_SEASON_WORD = r"(?:summer|fall|autumn|winter|spring)"
_SEASON_GROUP = re.compile(
    rf"\b{_SEASON_WORD}(?:\s*(?:/|,|&|\bor\b|\band\b|[-\u2013])\s*{_SEASON_WORD})*\b", re.I
)
# A season word names a term only beside something that makes it one -- a
# year, a term word, or a bracket holding nothing else -- so "Fall Risk
# Analytics", "Spring Street", "(Spring Boot)" and "(Java/Spring)" do not,
# while "Winter/Spring 2027" and "(Summer or Winter)" do.
_TERM_AFTER = re.compile(
    r"\s*(?:20\d\d\b|'\d\d\b|semesters?\b|terms?\b|sessions?\b|cycles?\b|cohorts?\b"
    r"|intern\w*|co-?ops?\b|placements?\b|analysts?\b)",
    re.I,
)
_TERM_BEFORE = re.compile(r"\b20\d\d\s*$")
_OFF_CYCLE = re.compile(r"\boff[\s-]?cycle\b|\bco-?op\b", re.I)
# A decimal is read whole: without it, `\b` finds "5 months" in "2.5 months".
_NUMBER = r"\d{1,3}(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
_NUMBER_WORDS = {w: n for n, w in enumerate(_NUMBER.split("|")[1:], start=1)}
_DURATION = re.compile(
    rf"\b(?P<lo>{_NUMBER})(?:\s*(?:-|\u2013|\u2014|to|and|or)\s*(?P<hi>{_NUMBER}))?"
    r"[\s-]*(?P<unit>weeks?|months?)\b",
    re.I,
)
# A length is the role's only when the sentence attributes it to the role, not
# when it merely sits near a role word: "a 24-week internship" and "the
# internship runs 6 months" state one; "6 months of Python experience", "a
# 1-week orientation program" and "a 24-month master's program" do not. The
# same reading decides a knockout and the downgrade a fitting length gives, so
# a phrasing left unread can admit a posting or leave a knockout standing.
_ROLE_NOUN = r"(?:internships?|co-?ops?|placements?|fellowships?)"
_PROGRAM = r"(?:programmes?|programs?)"
# The length names the role: "a 24-week internship", "a six-month-long
# internship", "a 6-month full-time paid internship". The words between are
# modifiers, so a preposition, article or pronoun there ends it ("6 months of
# internship experience", "2 weeks before internship start"). A role word
# followed by one of `_ADJUNCT` modifies that noun instead ("6 months
# internship experience"). "intern" counts only in "intern program", so "a
# 1-week intern bootcamp" is not the role. "program" alone counts only straight
# after the length: after a modifier it is often a degree or an orientation.
_NOT_A_MODIFIER = (
    r"of|before|after|within|prior|post|from|for|in|at|to|into|since|until|ago|by|on|per"
    r"|than|with|without|as|the|a|an|and|or|is|are|was|will|you|we|our|your|their|its"
    r"|which|that|who|where|most|many|all|some|every|each|any|no|several|few|these|those|this"
)
_ADJUNCT = (
    r"experience|completion|interviews?|applications?|offers?|orientation|onboarding"
    r"|training|bootcamp|alumni|credits?"
)
_MODIFIER = rf"\s+(?!(?:{_NOT_A_MODIFIER})\b)[\w'-]+"
# A comma after the length lists a modifier ("a 6 month, full-time
# internship"); with none after it, it ends a clause ("After 6 months,
# internship participants receive a review").
_LENGTH_NAMES_ROLE = re.compile(
    rf"(?:[\s-]long)?(?:,(?:{_MODIFIER}){{1,3}}|(?:{_MODIFIER}){{0,3}})"
    rf"\s+(?:{_ROLE_NOUN}|intern\s+{_PROGRAM})\b(?!\s+(?:{_ADJUNCT})\b)"
    rf"|\s+{_PROGRAM}\b",
    re.I,
)
_HEDGE_WORDS = r"up to|typically|usually|approximately|about|around|roughly|between"
# The role is the subject and the length its predicate: "the internship will
# be 10 weeks", "internships are offered for 6 months", "the internship lasts a
# minimum of 6 months", "Internship (6 months)", "Duration of internship - 24
# weeks", or a line opening "Duration:". Only the words in `_LINK_WORDS` may
# come between, and a hedge among them makes the length a stretch. "interns"
# is never the subject: "the notice period for interns is 2 weeks" and "our
# interns are 6 months from graduation" are lengths of something else.
_LINK_WORDS = (
    rf"{_HEDGE_WORDS}|at least|will|would|can|may|be|is|are|runs?|running|lasts?|lasting"
    r"|spans?|spanning|takes?|place|scheduled|offered|for|over|of|from|a|the|full|minimum"
    r"|period"
)
_ROLE_RUNS = re.compile(
    r"(?:(?P<prev>[\w'-]+)\s+)??\b"
    r"(?:(?:(?:duration|length)\s+of\s+(?:the\s+|this\s+|your\s+|an?\s+)?)?"
    rf"(?:{_ROLE_NOUN}(?:\s+{_PROGRAM})?|intern\s+{_PROGRAM})"
    rf"|(?P<bare>{_PROGRAM})|(?P<label>^\s*(?:duration|length)))"
    r"(?:\s+(?:period|length|duration|term|contract))?"
    rf"(?P<tail>(?:\s*[:(,]|\s+[-\u2013\u2014])?(?:\s+(?:{_LINK_WORDS})\b)*)\s*$",
    re.I,
)
# A role word after one of these is not the subject: "prior internships of 6
# months", "the notice period for internships is 2 weeks".
_NOT_THE_SUBJECT = (
    r"for|to|as|of|with|from|in|during|after|before|than|about|prior|previous|past|former"
)
# A bare "program" is the role only after an article or a season: "the
# programme runs six months", "our summer program", but not "our graduate
# program is 24 months" or "our rotational program runs 18 months".
_PROGRAM_OWNERS = r"the|this|our|a|an|each|your|summer|winter|fall|autumn|spring"
# "You have completed an internship of at least 6 months", "Prior experience:
# an internship lasting 6 months": the candidate's past role, not this one.
_CANDIDATE_HISTORY = re.compile(
    r"\b(?:(?:have|has|had|having)\s+(?:\w+\s+)?(?:completed|done|undertaken|finished)"
    r"|prior|previous)\b\W+(?:[\w'-]+\W+){0,6}$",
    re.I,
)
# A "Duration:" line's length must end its value: "Duration: 12 months after
# graduation" and "Duration: 2 weeks per rotation" measure something else.
_VALUE_ENDS = re.compile(r"\s*(?:[,;().\u2013\u2014-]|$)")
# "The internship takes 2 weeks to set up": a length of a task, not the role.
_LENGTH_FOR_A_TASK = re.compile(r"\s+to\b", re.I)
_LENGTH_HEDGE = re.compile(rf"\b(?:{_HEDGE_WORDS})\b", re.I)
_WEEKS_PER_MONTH = 52 / 12


class Profile(BaseModel):
    """What is known about the candidate apart from their facts: what
    eligibility is judged against, each field of which may be unknown, and how
    many pages their resume may run to.

    One page is the default because it is the convention for a student or a
    candidate early in a career, who are most of the people writing a resume
    for the first time; someone with more roles than a page holds sets it
    themselves."""

    model_config = {"extra": "forbid", "frozen": True}

    work_authorization: str | None = None
    graduation: str | None = None
    degree: DegreeLevel | None = None
    resume_pages: int = Field(default=1, ge=1, le=10)
    seasons: tuple[Season, ...] = ()
    max_weeks: int | None = Field(default=None, ge=1, le=104)


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


class ProfileError(ValueError):
    """The eligibility profile cannot be read; a `ValueError`, as callers expect."""


def load_profile() -> Profile:
    path = resolve_inside(PROFILE_FILE)
    if not path.exists():
        return Profile()
    try:
        return Profile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ProfileError(f"{path} is not a readable profile: {exc}") from exc


def save_profile(profile: Profile) -> None:
    body = yaml.safe_dump(profile.model_dump(exclude_none=True), sort_keys=False)
    write_private(resolve_inside(PROFILE_FILE), body)


def _is_citizen(auth: str) -> bool:
    us = r"(?:us|u\.s\.|usa|u\.s\.a\.|united states|american)"
    found = re.search(rf"\b{us}\s+citizen\b|\bcitizen of the {us}(?!\w)", auth, re.I)
    return found is not None


def _unsure_citizen(auth: str) -> bool:
    """A bare "citizen" says whose citizenship nowhere, so it is a question."""
    return " ".join(auth.casefold().strip(" .").split()) in {"citizen", "citizenship"}


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


def _season_terms(text: str) -> list[tuple[int, frozenset[str]]]:
    """Each run of seasons `text` names as a term, as its offset and the seasons in it."""
    terms: list[tuple[int, frozenset[str]]] = []
    for match in _SEASON_GROUP.finditer(text):
        before, after = text[: match.start()], text[match.end() :]
        bracketed = before.rstrip().endswith("(") and after.lstrip().startswith(")")
        if bracketed or _TERM_AFTER.match(after) or _TERM_BEFORE.search(before):
            words = {w.casefold() for w in re.findall(_SEASON_WORD, match.group(0), re.I)}
            terms.append((match.start(), frozenset("fall" if w == "autumn" else w for w in words)))
    return terms


def _weeks(number: str, unit: str) -> float:
    n = float(number) if number[0].isdigit() else _NUMBER_WORDS[number.casefold()]
    return n * _WEEKS_PER_MONTH if unit.casefold().startswith("month") else n


def _role_link(before: str, after: str) -> str | None:
    """The words linking a role subject to a length between `before` and
    `after`, or None when `before` does not end in a role subject and a link."""
    runs = _ROLE_RUNS.search(before)
    if runs is None or not runs.group("tail").strip() or _LENGTH_FOR_A_TASK.match(after):
        return None
    if runs.group("label") and not _VALUE_ENDS.match(after):
        return None
    prev = runs.group("prev") or ""
    if runs.group("bare"):
        owned = not prev or re.fullmatch(_PROGRAM_OWNERS, prev, re.I)
        return runs.group("tail") if owned else None
    return None if re.fullmatch(_NOT_THE_SUBJECT, prev, re.I) else runs.group("tail")


def _role_lengths(sentence: str) -> list[tuple[float, float, bool, str]]:
    """Each length `sentence` attributes to the role, as `(low, high, hedged,
    text)`, the bounds in weeks."""
    lengths: list[tuple[float, float, bool, str]] = []
    for m in _DURATION.finditer(sentence):
        before = sentence[: m.start()]
        if _CANDIDATE_HISTORY.search(before):
            continue
        link = _role_link(before, sentence[m.end() :])
        if link is None and not _LENGTH_NAMES_ROLE.match(sentence, m.end()):
            continue
        low = _weeks(m.group("lo"), m.group("unit"))
        high = _weeks(m.group("hi"), m.group("unit")) if m.group("hi") else low
        hedged = link is not None and _LENGTH_HEDGE.search(link) is not None
        lengths.append((low, high, hedged, m.group(0)))
    return lengths


def _title_of(text: str) -> str:
    """A saved posting's title: `take` heads the file `# <title> - <company>`,
    and a title may itself hold " - ", so only the last one is cut."""
    first = text.lstrip().split("\n", 1)[0]
    return first[2:].strip().rsplit(" - ", 1)[0] if first.startswith("# ") else ""


def check_eligibility(text: str, profile: Profile, *, title: str | None = None) -> Eligibility:
    """Every eligibility requirement `text` states, judged against `profile`.

    `title` is the posting's own; omitted, it is read from a `# ` heading on
    the first line, which is how a posting saved with `take` begins."""
    findings: list[Finding] = []
    auth = profile.work_authorization

    def add(rule: str, level: Level, quote: str, reason: str) -> None:
        # One finding per rule, the most severe any sentence gives it: sentences
        # are read in document order, and a stretch early in a posting must not
        # hide a knockout later in it.
        # The replacement moves to the end, so findings stay in the order of
        # the sentences they quote.
        for i, found in enumerate(findings):
            if found.rule == rule:
                if _SEVERITY[level] < _SEVERITY[found.level]:
                    del findings[i]
                    break
                return
        findings.append(Finding(rule, level, quote.strip(), reason))

    # Board content arrives as HTML more often than not: "&nbsp;" and tags would
    # otherwise sit between the words every rule below expects to be adjacent.
    plain = re.sub(r"<[^>]+>", " ", html.unescape(text))
    normalised = re.sub(r"\bU\.S\.(?=\s|$)", "US", plain)
    # A posting that says it sponsors and also that it cannot always is a
    # question for someone who needs sponsorship, not a refusal.
    offers = _OFFERS_SPONSORSHIP.search(normalised) is not None
    admitted = False
    allowed = set(profile.seasons)
    title = _title_of(text) if title is None else title
    # The title is the employer's own statement of the term, so it alone can
    # knock a posting out; the body only raises a stretch. A knockout hides a
    # posting, so it needs a season stated as one, not a word that might be one.
    if allowed:
        named = frozenset[str]().union(*(term for _, term in _season_terms(title)))
        if named - allowed or _OFF_CYCLE.search(title):
            level: Level = "stretch" if named & allowed else "knockout"
            add("season", level, title, f"term outside {', '.join(sorted(allowed))}")
    fitting_length: str | None = None
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
            if auth is None or _unsure_citizen(auth):
                add("citizenship", "unknown", sentence, "are you a US citizen?")
            elif not _is_citizen(auth):
                add(
                    "citizenship", "knockout", sentence, f"requires US citizenship; profile: {auth}"
                )
        if _CLEARANCE.search(sentence):
            if auth is not None and not _is_citizen(auth) and not _unsure_citizen(auth):
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
        named = [level for level, pattern in _DEGREE_LEVELS.items() if pattern.search(sentence)]
        my_rank = _DEGREE_RANK[profile.degree] if profile.degree is not None else None
        above = [level for level in named if my_rank is None or _DEGREE_RANK[level] > my_rank]
        if my_rank is not None and above and len(above) < len(named):
            admitted = True
        elif above and above == named and above != ["bachelor"]:
            students = _STUDENT_OF.search(sentence) is not None
            required = (
                _REQUIRED.search(sentence) is not None
                and not _NEGATED.search(sentence)
                and not _HEDGED.search(sentence)
            )
            who = f"for current {' or '.join(above)} students"
            if my_rank is not None and students and required:
                add("degree", "knockout", sentence, who)
            else:
                add(
                    "degree", "stretch", sentence, who if students else "asks for a graduate degree"
                )
        # A graduation window names seasons too ("graduating Fall 2027 or
        # Spring 2028"); those after a graduation word are the graduation
        # rule's to judge, not a term. Only those after it: a list joined by
        # tags reads as one sentence, so "Fall 2027 internship" can precede
        # "graduate degree" in it.
        graduation = _GRADUATION.search(sentence)
        cut = graduation.start() if graduation else len(sentence)
        if allowed and any(term - allowed for at, term in _season_terms(sentence) if at < cut):
            add("season", "stretch", sentence, "mentions a term you are not available for")
        if profile.max_weeks is not None:
            for low, high, hedged, stated in _role_lengths(sentence):
                if high <= profile.max_weeks:
                    fitting_length = fitting_length or stated
                elif low > profile.max_weeks and not hedged:
                    add("duration", "knockout", sentence, f"longer than {profile.max_weeks} weeks")
                else:
                    add("duration", "stretch", sentence, f"may run past {profile.max_weeks} weeks")
        years = [int(y) for y in _YEARS.findall(sentence)]
        if years and max(years) >= STRETCH_YEARS:
            add("experience", "stretch", sentence, f"asks for {max(years)}+ years of experience")
    if admitted:
        findings = [f for f in findings if f.rule != "degree"]
    # A posting that also states a length that fits describes more than one
    # role, or more than one option; which applies is a question, not a knockout.
    if fitting_length:
        findings = [
            replace(f, level="stretch", reason=f"{f.reason}; it also states {fitting_length!r}")
            if f.rule == "duration" and f.level == "knockout"
            else f
            for f in findings
        ]
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
        eligibility = check_eligibility(posting.text, profile, title=posting.title)
        asked = _technologies(posting.text)
        have = sorted(asked & evidenced)
        missing = sorted(asked - evidenced)
        coverage = len(have) / (len(asked) + COVERAGE_PRIOR)
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
        if check_eligibility(posting.text, profile, title=posting.title).verdict == "knockout":
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
