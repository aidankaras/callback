"""Deterministic checks on a cover letter.

A letter is prose in the candidate's own voice, so it is checked sentence by
sentence with the same rules as a resume bullet -- every number, date,
technology, mid-sentence name and claim word must be stated by what the
sentence cites. What a sentence may cite depends on who it is about:

- **A sentence in the first person** ("I", "my", "me") is a claim about the
  candidate. It is grounded against its cited facts only, plus the company
  and role the letter names. It may never rest on the posting: the posting
  is someone else's untrusted text, and a line in it such as "10 years of
  PyTorch" must not become "I have 10 years of PyTorch" by citing it.
- **Any other sentence** is about the job or the company. It cites
  `"posting"` and is grounded against the saved posting text.
- **A sentence that cites nothing** passes only if it makes no checkable
  claim at all ("Thank you for your consideration.").

The company and role the letter names must appear in the posting. The letter
fails outright on more than `MAX_WORDS` words, on commitment or negotiation
language -- salary, availability, relocation, sponsorship -- which a signed
letter must not concede and an injected posting would try to insert, and on
reusing a resume bullet verbatim.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from core.aliases import canonicalize
from core.checks.grounding import check_bullet, checkable_entity_tokens
from core.checks.numbers import parse_numbers
from core.letter.models import POSTING, CoverLetter
from core.profile.models import Fact, FactKind, Provenance, Quantity, Trust

MAX_WORDS = 300
_MIN_REUSED_WORDS = 6

_FIRST_PERSON = re.compile(r"\b(?:i|i'm|i've|i'd|i'll|me|my|mine|myself)\b", re.IGNORECASE)
_COMMITMENT = re.compile(
    r"\b(?:salary|salaries|compensation|stipend|unpaid|pay\s+rate|relocat\w*|visa|"
    r"sponsor(?:ship)?|start\s+date|available\s+(?:to\s+start|immediately|from)|"
    r"notice\s+period|willing\s+to|negotiab\w*)\b",
    re.IGNORECASE,
)
# Standing in for the posting and the letter's own header in `check_bullet`,
# which grounds text against facts. Neither is a fact about the candidate,
# and neither is ever stored.
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


def _target(letter: CoverLetter) -> Fact:
    """The company and role, as a stand-in fact every sentence may name."""
    return Fact(
        id="__target__",
        kind=FactKind.LINKS,
        text=f"{letter.company} {letter.role}",
        entities=[letter.company],
        provenance=_SYNTHETIC,
    )


def _posting_fact(posting: str) -> Fact:
    """The posting as a stand-in fact, for sentences about the job."""
    numbers = {f"n{i}": Quantity(value=n.value) for i, n in enumerate(parse_numbers(posting))}
    entities = sorted({canonicalize(t) for t in checkable_entity_tokens(posting)})
    return Fact(
        id="__posting__",
        kind=FactKind.LINKS,
        text=posting,
        numbers=numbers,
        entities=entities,
        provenance=_SYNTHETIC,
    )


def check_letter(
    letter: CoverLetter,
    facts: Mapping[str, Fact],
    posting: str,
    *,
    resume_bullets: Sequence[str] = (),
) -> LetterResult:
    """Every way `letter` fails, given the candidate's facts and the posting text."""
    findings: list[str] = []
    for name, value in (("company", letter.company), ("role", letter.role)):
        if _norm(value) not in _norm(posting):
            findings.append(f"{name} {value!r} does not appear in the posting")

    target = _target(letter)
    posting_fact = _posting_fact(posting)
    reused = [b for b in resume_bullets if len(_norm(b).split()) >= _MIN_REUSED_WORDS]
    words = 0
    for label, sentence in letter.sentences:
        words += len(sentence.text.split())
        unknown = [s for s in sentence.sources if s != POSTING and s not in facts]
        if unknown:
            findings.append(f"sentence {label}: cites unknown fact(s) {unknown}")
            continue
        cited = [facts[s] for s in sentence.sources if s != POSTING]
        if _FIRST_PERSON.search(sentence.text):
            corpus = [*cited, target]
        elif POSTING in sentence.sources:
            corpus = [*cited, target, posting_fact]
        else:
            corpus = [*cited, target]
        _, bullet_findings = check_bullet(sentence.text, corpus)
        findings.extend(f"sentence {label}: {f.kind}: {f.detail}" for f in bullet_findings)
        if _COMMITMENT.search(sentence.text):
            findings.append(
                f"sentence {label}: commitment or negotiation language: {sentence.text!r}"
            )
        for bullet in reused:
            if _norm(bullet) in _norm(sentence.text):
                findings.append(f"sentence {label}: reuses a resume bullet verbatim")

    words += len(letter.greeting.split()) + len(letter.closing.split())
    if words > MAX_WORDS:
        findings.append(f"the letter is {words} words; at most {MAX_WORDS}")
    return LetterResult(findings=tuple(findings))
