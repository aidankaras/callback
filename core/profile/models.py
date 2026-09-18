"""Pydantic models for the candidate's identity and facts."""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class Identity(BaseModel):
    """Contact details the round-trip gate must be able to recover from a PDF."""

    model_config = {"extra": "forbid"}

    name: str
    email: str
    phone: str | None = None
    links: list[str] = Field(default_factory=list)
    work_authorization: str | None = None
    graduation: str | None = None


class Trust(StrEnum):
    """How a fact's content was established.

    Only `USER_CONFIRMED` facts may back generated text; the other two levels
    exist to hold candidate content that has not yet cleared that bar.
    """

    USER_CONFIRMED = "user_confirmed"
    IMPORT_UNCONFIRMED = "import_unconfirmed"
    WEB = "web"


class FactKind(StrEnum):
    """What a fact describes, including the two kinds that record a gap."""

    EXPERIENCE = "experience"
    EXPERIENCE_DETAIL = "experience_detail"
    EDUCATION = "education"
    PROJECT = "project"
    SKILL = "skill"
    LINKS = "links"
    ABSENT = "absent"
    DECLINED = "declined"


class Quantity(BaseModel):
    """A single measured value, e.g. a headcount or a percentage."""

    model_config = {"extra": "forbid"}

    value: float
    unit: str | None = None


class Dates(BaseModel):
    """A date range for a fact, with the precision it was actually known at."""

    model_config = {"extra": "forbid"}

    start: str | None = None
    end: str | None = None
    precision: str = "month"


class Derivation(BaseModel):
    """A value computed from other numbers in the cited facts.

    `op` is recomputed by core; the model never supplies the result.
    """

    model_config = {"extra": "forbid"}

    label: str
    op: str
    from_: list[str] = Field(alias="from")


class Provenance(BaseModel):
    """Where a fact's content came from, and the trust level that follows from it."""

    model_config = {"extra": "forbid"}

    source: str
    trust: Trust

    @model_validator(mode="after")
    def trust_matches_source(self) -> Provenance:
        if self.source.startswith("web:") and self.trust is not Trust.WEB:
            raise ValueError("web-sourced facts must carry trust=web")
        if self.source.startswith("import:") and self.trust is Trust.USER_CONFIRMED:
            raise ValueError("imported facts start as import_unconfirmed")
        return self


class Fact(BaseModel):
    """A single candidate-controlled unit of content, with its own provenance.

    Facts are stored keyed by `id`; the identifier is not repeated in the
    stored body (see `core.profile.store`).
    """

    model_config = {"extra": "forbid", "populate_by_name": True}

    id: str
    kind: FactKind
    parent: str | None = None
    text: str
    numbers: dict[str, Quantity] = Field(default_factory=dict)
    dates: Dates | None = None
    # `default_factory=list[Derivation]`, not bare `list`: pyright strict cannot
    # bind a bare `list` factory against a `list[SomeBaseModel]` target and
    # widens the field to `list[Unknown]` (reportUnknownVariableType). Bare
    # `list` works for `list[str]` and `dict[str, ...]` fields below; only the
    # user-model-typed list needs the explicit factory spelling.
    derived: list[Derivation] = Field(default_factory=list[Derivation])
    entities: list[str] = Field(default_factory=list)
    provenance: Provenance
    captured: date = Field(default_factory=lambda: datetime.now(tz=UTC).date())

    def selectable(self) -> bool:
        """Only confirmed facts may back generated text."""
        return self.provenance.trust is Trust.USER_CONFIRMED
