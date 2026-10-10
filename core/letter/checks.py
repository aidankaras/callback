"""Deterministic checks on a cover letter.

Every sentence declares what it is about, and each kind is held to its own rule:

- **`candidate`** -- a claim about the candidate. It must cite at least one
  confirmed fact and may never cite the posting, and it is grounded against
  those facts with the same rules as a resume bullet: every number, date,
  technology, mid-sentence name and claim word must be stated by them. It may
  not name the company either, since "I was an intern at <company>" would
  otherwise rest on nothing but the letter's own header.
- **`employer`** -- a statement about the job or the company. It must cite
  the posting and nothing else, begin with the company's name or one of
  `EMPLOYER_SUBJECTS`, contain no first-person word and no part of the
  candidate's name, and is grounded against the posting text. The subject
  rule is what stops a claim about the candidate ("Led a team of twelve…",
  "This candidate holds a patent") borrowing the posting's words.
- **`courtesy`** -- must be one of `COURTESY` exactly, with the company and
  role substituted, and cites nothing.

Each sentence must be one sentence: an internal full stop would let a second
sentence start with a capitalised word that the name rule exempts. The
greeting must be a short salutation and the closing one of `CLOSINGS`, since
both are printed. The company and role must each equal a segment of the
posting's first line, not merely occur somewhere in it -- an injected line can
put any name somewhere in a posting. The letter also fails on more than
`MAX_WORDS` words, on commitment or negotiation language (which a signed
letter must not concede and an injected posting would try to insert), on
reusing a resume bullet verbatim. Every sentence but a courtesy one on the
fixed list also fails on a stock phrase and, when the letter names a field
brief, on that brief's own phrases and on copying one of its exemplars
(`core.checks.style`). A letter that names a brief fails unless that brief
was passed in, so the copy check cannot be skipped by not loading it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from core.aliases import canonicalize
from core.briefs import Brief
from core.checks.claims import identity_findings
from core.checks.grounding import check_bullet, checkable_entity_tokens
from core.checks.numbers import parse_numbers
from core.checks.style import text_findings
from core.letter.models import POSTING, CoverLetter, Sentence
from core.profile.models import Fact, FactKind, Provenance, Quantity, Trust

MAX_WORDS = 300
_MIN_REUSED_WORDS = 6

EMPLOYER_SUBJECTS = (
    "the role",
    "this role",
    "the position",
    "this position",
    "the internship",
    "this internship",
    "the team",
    "your team",
)
COURTESY = (
    "i am applying for the {role} role at {company}",
    "i am writing to apply for the {role} position at {company}",
    "thank you for your consideration",
    "thank you for your time and consideration",
    "i look forward to hearing from you",
    "i would welcome the chance to discuss the role",
)
CLOSINGS = frozenset(
    {"sincerely,", "best regards,", "kind regards,", "regards,", "thank you,", "with thanks,"}
)
# At most four capitalised words after the salutation: the greeting is
# rendered verbatim and no sentence rule reads it, so anything long enough to
# carry a claim ("Dear Stanford alumni network and friends at Acme,") is
# refused rather than printed unchecked.
_GREETING = re.compile(r"(?:Dear|Hello|To) [A-Z][A-Za-z.'&-]*(?: [A-Z][A-Za-z.'&-]*){0,3},")
# A terminator followed by whitespace, or glued straight to a capital: a
# missing space does not make two sentences one, and the grounding tokenizer
# reads "precision.Stanford" as a single word, so the name would go unchecked.
_INTERNAL_BREAK = re.compile(r"[.!?;:](?:\s+\S|[A-Z])")
# Apostrophes a letter may use: the ASCII one and the typographic U+2019.
_APOSTROPHE = "['\u2019]"
_FIRST_PERSON = re.compile(
    rf"\b(?:i|i{_APOSTROPHE}(?:m|ve|d|ll)|me|my|mine|myself|we|we{_APOSTROPHE}(?:re|ve)|us|our|ours)\b",
    re.IGNORECASE,
)
# An employer sentence is what the posting says about the company or the role.
# These words make it a statement about the candidate instead -- "the team
# interviewed this applicant" -- which the posting cannot ground.
_ABOUT_CANDIDATE = re.compile(
    r"\b(?:applicants?|candidates?|this (?:person|student)|him|her|he|she|hired|interviewed|"
    r"met|referred|recommended|introduced|contacted|emailed|invited|reached out|"
    r"(?:extended|made) (?:an|the) offer|offered (?:him|her|them|the|this))\b",
    re.IGNORECASE,
)
_COMMITMENT = re.compile(
    r"\b(?:salary|salaries|compensation|stipend|unpaid|pay\s+rate|relocat\w*|visas?|"
    r"sponsor\w*|start\s+date|availability|available\s+(?:to\s+start|immediately|from|in)|"
    r"notice\s+period|willing\s+to|negotiab\w*)\b",
    re.IGNORECASE,
)
# Separators between a heading's role and company: hyphen, en dash, em dash, bar, "at", "@".
_HEADING_SPLIT = re.compile("\\s+(?:-|\u2013|\u2014|\\||at|@)\\s+|,\\s*")
# Stands in for the posting in `check_bullet`, which grounds text against facts.
# It is not a fact about the candidate and is never stored.
_SYNTHETIC = Provenance(source="letter:synthetic", trust=Trust.USER_CONFIRMED)


@dataclass(frozen=True)
class LetterResult:
    """One finding per failed check; the verdict follows from them."""

    findings: tuple[str, ...] = ()

    @property
    def verdict(self) -> Literal["pass", "fail"]:
        return "fail" if self.findings else "pass"


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w%]+", " ", text.casefold()).split())


def _heading_segments(posting: str) -> set[str]:
    first = next((line for line in posting.splitlines() if line.strip()), "")
    return {_norm(part) for part in _HEADING_SPLIT.split(first.lstrip("# ").strip()) if part}


def _posting_fact(posting: str) -> Fact:
    # The unit travels with the value: a unit-less quantity backs either a
    # percentage or a count, so dropping it would let "40% of our traffic" in
    # the posting ground a sentence claiming 40 of something.
    numbers = {
        f"n{i}": Quantity(value=n.value, unit="%" if n.is_percent else n.unit)
        for i, n in enumerate(parse_numbers(posting))
    }
    entities = sorted({canonicalize(t) for t in checkable_entity_tokens(posting)})
    return Fact(
        id="__posting__",
        kind=FactKind.LINKS,
        text=posting,
        numbers=numbers,
        entities=entities,
        provenance=_SYNTHETIC,
    )


def _grounding(label: str, text: str, corpus: list[Fact]) -> list[str]:
    _, found = check_bullet(text, corpus)
    return [f"sentence {label}: {f.kind}: {f.detail}" for f in found]


@dataclass(frozen=True)
class SentenceContext:
    """What every sentence in one application document is checked against."""

    company: str
    role: str
    candidate_name: str
    facts: Mapping[str, Fact]
    posting: Fact
    resume_bullets: Sequence[str] = ()
    brief: Brief | None = None


def context_for(
    company: str,
    role: str,
    candidate_name: str,
    facts: Mapping[str, Fact],
    posting: str,
    resume_bullets: Sequence[str] = (),
    brief: Brief | None = None,
) -> SentenceContext:
    reused = [b for b in resume_bullets if len(_norm(b).split()) >= _MIN_REUSED_WORDS]
    return SentenceContext(
        company, role, candidate_name, facts, _posting_fact(posting), reused, brief
    )


def heading_findings(company: str, role: str, posting: str) -> list[str]:
    """Whether the company and role are segments of the posting's first line."""
    segments = _heading_segments(posting)
    return [
        f"{name} {value!r} is not a segment of the posting's first line; "
        "start the saved posting with '# <role> - <company>'"
        for name, value in (("company", company), ("role", role))
        if _norm(value) not in segments
    ]


def brief_findings(subject: str, named: str | None, brief: Brief | None) -> list[str]:
    """A document that names a brief fails unless that brief was loaded: the
    copy check and a brief's own phrases run only against a loaded one, so
    checking without it would read as having passed them."""
    if named is not None and (brief is None or brief.field != named):
        return [f"{subject} brief {named!r}, which was not loaded"]
    return []


def question_findings(question: str, ctx: SentenceContext) -> list[str]:
    """A form question is copied from the form, never written, and it is
    rendered above its answer: so it may not carry a claim of its own."""
    findings: list[str] = []
    if "\n" in question.strip() or question.lstrip().startswith("#"):
        findings.append("the question must be one line, as the form shows it, not a heading")
    name_parts = {w for w in _norm(ctx.candidate_name).split() if len(w) > 1}
    if _FIRST_PERSON.search(question) or name_parts & set(_norm(question).split()):
        findings.append("the question speaks about the candidate; copy it from the form")
    return findings


def check_sentence(label: str, sentence: Sentence, ctx: SentenceContext) -> list[str]:
    """Every rule `sentence` breaks, for its declared kind -- see the module docstring."""
    findings = _kind_findings(label, sentence, ctx)
    # A courtesy sentence that passed is one of `COURTESY`, so its wording is
    # fixed, not written: the style check could only fail a sentence the
    # letter is allowed to use, as when an exemplar happens to contain it.
    fixed = sentence.kind == "courtesy" and not findings
    if _COMMITMENT.search(sentence.text):
        findings.append(f"sentence {label}: commitment or negotiation language: {sentence.text!r}")
    if any(_norm(b) in _norm(sentence.text) for b in ctx.resume_bullets):
        findings.append(f"sentence {label}: reuses a resume bullet verbatim")
    if not fixed:
        own = [ctx.facts[s].text for s in sentence.sources if s != POSTING and s in ctx.facts]
        findings += text_findings(f"sentence {label}", sentence.text, ctx.brief, own=own)
    return findings


def _kind_findings(label: str, sentence: Sentence, ctx: SentenceContext) -> list[str]:
    text = sentence.text
    if _INTERNAL_BREAK.search(text.rstrip()):
        return [f"sentence {label}: holds more than one sentence; split it"]
    if sentence.kind == "courtesy":
        allowed = {_norm(t.format(role=ctx.role, company=ctx.company)) for t in COURTESY}
        if sentence.sources or _norm(text) not in allowed:
            return [f"sentence {label}: is not one of the courtesy sentences, so it must cite"]
        return []
    if sentence.kind == "employer":
        if sentence.sources != [POSTING]:
            return [f"sentence {label}: an employer sentence cites the posting and nothing else"]
        subject = _norm(text)
        subjects = (_norm(ctx.company), *EMPLOYER_SUBJECTS)
        if not any(subject.startswith(s) for s in subjects):
            return [
                f"sentence {label}: an employer sentence begins with the company's name or "
                "the role, position, internship or team"
            ]
        name_parts = {w for w in _norm(ctx.candidate_name).split() if len(w) > 1}
        if (
            _FIRST_PERSON.search(text)
            or name_parts & set(subject.split())
            or _ABOUT_CANDIDATE.search(text)
        ):
            return [f"sentence {label}: an employer sentence may not speak about the candidate"]
        return _grounding(label, text, [ctx.posting])
    # candidate
    if not sentence.sources or POSTING in sentence.sources:
        return [f"sentence {label}: a sentence about you cites your facts, never the posting"]
    unknown = [s for s in sentence.sources if s not in ctx.facts]
    if unknown:
        return [f"sentence {label}: cites unknown fact(s) {unknown}"]
    if _norm(ctx.company) in _norm(text):
        return [f"sentence {label}: a sentence about you may not name {ctx.company}"]
    return _grounding(label, text, [ctx.facts[s] for s in sentence.sources])


def check_letter(
    letter: CoverLetter,
    facts: Mapping[str, Fact],
    posting: str,
    *,
    resume_bullets: Sequence[str] = (),
    brief: Brief | None = None,
) -> LetterResult:
    """Every way `letter` fails, given the candidate's facts and the posting text."""
    findings = heading_findings(letter.company, letter.role, posting)
    if not _GREETING.fullmatch(letter.greeting.strip()):
        findings.append(
            f"greeting {letter.greeting!r} must be a salutation such as 'Dear Hiring Team,'"
        )
    if _norm(letter.greeting) and any(c.isdigit() for c in letter.greeting):
        findings.append("the greeting may not contain a number")
    if letter.closing.strip().casefold() not in CLOSINGS:
        findings.append(f"closing {letter.closing!r} must be one of {sorted(CLOSINGS)}")

    findings.extend(identity_findings(letter.identity, facts))
    findings.extend(brief_findings("the letter names", letter.brief, brief))
    ctx = context_for(
        letter.company, letter.role, letter.identity.name, facts, posting, resume_bullets, brief
    )
    words = len(letter.greeting.split()) + len(letter.closing.split())
    for label, sentence in letter.sentences:
        words += len(sentence.text.split())
        findings.extend(check_sentence(label, sentence, ctx))
    if words > MAX_WORDS:
        findings.append(f"the letter is {words} words; at most {MAX_WORDS}")
    return LetterResult(findings=tuple(findings))
