"""A cover letter as data: sentences, each citing what it rests on."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from core.profile.models import Identity
from core.resume.models import carries_content

POSTING = "posting"


class Sentence(BaseModel):
    """One sentence and its sources: candidate fact ids, or `"posting"`.

    A sentence may cite nothing, but then it may make no checkable claim.
    """

    model_config = {"extra": "forbid"}

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

    @property
    def sentences(self) -> list[tuple[str, Sentence]]:
        """Every sentence with its `paragraph.sentence` label."""
        return [
            (f"{p}.{s}", sentence)
            for p, paragraph in enumerate(self.paragraphs)
            for s, sentence in enumerate(paragraph)
        ]
