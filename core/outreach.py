"""A short note to someone at the company: a recruiter, an engineer, an alum.

A personal note is one of the most effective ways to be noticed for a role,
and one of the easiest places for an assistant to overreach. So its body is
a list of declared sentences held to the cover-letter rules
(`core.letter.checks`), and the greeting and sign-off are generated rather
than written: the recipient's name appears in the greeting and nowhere
else, and the message is measured whole against its channel's limit -- a
LinkedIn connection note is capped at 300 characters, greeting included.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from core.checks.claims import name_findings
from core.letter.checks import check_sentence, context_for, heading_findings
from core.letter.models import Sentence
from core.profile.models import Fact

Channel = Literal["linkedin-note", "linkedin-message", "email"]
# (max characters, max words) for each channel's whole message.
LIMITS: dict[str, tuple[int | None, int | None]] = {
    "linkedin-note": (300, None),
    "linkedin-message": (1900, 200),
    "email": (None, 150),
}
_NAME = re.compile(r"[A-Z][A-Za-z'\-]+(?: [A-Z][A-Za-z'\-]+){0,3}")
_WORD = re.compile(r"[A-Za-z]+")


class Outreach(BaseModel):
    model_config = {"extra": "forbid"}

    job_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    company: str
    role: str
    candidate_name: str
    recipient: str
    channel: Channel
    sentences: list[Sentence] = Field(min_length=1)

    @field_validator("recipient", "candidate_name")
    @classmethod
    def _is_a_name(cls, value: str) -> str:
        if not _NAME.fullmatch(value.strip()):
            raise ValueError("must be a person's name, e.g. 'Sam Rivera'")
        return value.strip()


@dataclass(frozen=True)
class OutreachResult:
    findings: tuple[str, ...] = ()

    @property
    def verdict(self) -> Literal["pass", "fail"]:
        return "fail" if self.findings else "pass"


def render_outreach(doc: Outreach) -> str:
    """The message exactly as it is sent: greeting, body, sign-off."""
    body = " ".join(s.text.strip() for s in doc.sentences)
    first = doc.recipient.split()[0]
    return f"Hi {first}, {body}\n\n{doc.candidate_name.split()[0]}"


def check_outreach(doc: Outreach, facts: Mapping[str, Fact], posting: str) -> OutreachResult:
    findings = heading_findings(doc.company, doc.role, posting)
    ctx = context_for(doc.company, doc.role, doc.candidate_name, facts, posting)
    findings.extend(name_findings(doc.candidate_name, facts))
    recipient_words = {w.casefold() for w in _WORD.findall(doc.recipient) if len(w) > 1}
    for n, sentence in enumerate(doc.sentences):
        findings.extend(check_sentence(f"0.{n}", sentence, ctx))
        # Word tokens, so a possessive ("Sam's") is still the name.
        if recipient_words & {w.casefold() for w in _WORD.findall(sentence.text)}:
            findings.append(f"sentence 0.{n}: names the recipient; the greeting does that")
    text = render_outreach(doc)
    max_chars, max_words = LIMITS[doc.channel]
    if max_chars is not None and len(text) > max_chars:
        findings.append(f"{len(text)} characters; a {doc.channel} allows {max_chars}")
    if max_words is not None and len(text.split()) > max_words:
        findings.append(f"{len(text.split())} words; a {doc.channel} allows {max_words}")
    return OutreachResult(findings=tuple(findings))
