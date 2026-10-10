"""A cover letter as data: sentences, each citing what it rests on."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from core.profile.models import Identity
from core.resume.models import carries_content

POSTING = "posting"


SentenceKind = Literal["candidate", "employer", "courtesy"]


class Sentence(BaseModel):
    """One sentence, what it is about, and what it cites.

    `kind` is declared, not inferred: deciding who a sentence is about from
    its wording is exactly what an unusual phrasing defeats. Each kind is
    then held to its own rule -- see `core.letter.checks`.
    """

    model_config = {"extra": "forbid"}

    kind: SentenceKind
    text: str
    sources: list[str] = Field(default_factory=list)

    @field_validator("text")
    @classmethod
    def _text_is_not_blank(cls, text: str) -> str:
        if not carries_content(text):
            raise ValueError("a sentence's text cannot be blank: it has no letter or digit")
        return text


class CoverLetter(BaseModel):
    """The letter for one job: who it is from, who it is to, and what it says."""

    model_config = {"extra": "forbid"}

    identity: Identity
    job_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    company: str
    role: str
    greeting: str
    paragraphs: list[list[Sentence]] = Field(min_length=1)
    closing: str
    brief: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")

    @property
    def sentences(self) -> list[tuple[str, Sentence]]:
        """Every sentence with its `paragraph.sentence` label."""
        return [
            (f"{p}.{s}", sentence)
            for p, paragraph in enumerate(self.paragraphs)
            for s, sentence in enumerate(paragraph)
        ]
