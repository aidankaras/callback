"""Pydantic models for the candidate's identity and facts."""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator


class Identity(BaseModel):
    """Contact details for the candidate: name, email, and other reachable-by info.

    This model only validates shape; whether these values actually survive
    rendering into a readable PDF is `core.checks.roundtrip.check_roundtrip`'s
    job, not this class's.
    """

    model_config = {"extra": "forbid"}

    name: str
    email: str
    phone: str | None = None
    links: list[str] = Field(default_factory=list)
    work_authorization: str | None = None
    graduation: str | None = None

    @field_validator("links")
    @classmethod
    def _links_are_addresses(cls, links: list[str]) -> list[str]:
        """A link is an address (`github.com/dv`, a URL, a handle), never prose.

        The contact line renders links verbatim beside the email, so a "link"
        reading "US Citizen, TS/SCI clearance" would print a claim no gate
        compares to anything. An address never contains whitespace.
        """
        for link in links:
            if not link or any(c.isspace() for c in link):
                raise ValueError(f"link {link!r} must be an address with no spaces")
        return links


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
    """Where a fact's content came from, and the trust level that follows from it.

    Frozen: `Fact.selectable()` trusts `provenance.trust` to reflect how the
    content was actually established, and a plain attribute assignment
    (`fact.provenance.trust = Trust.USER_CONFIRMED`) would otherwise bypass
    `trust_matches_source` entirely, letting an unconfirmed import or a
    web-sourced fact back generated text with no validation in the way.
    """

    model_config = {"extra": "forbid", "frozen": True}

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

    Frozen: freezing `Provenance` alone stops `fact.provenance.trust = ...`,
    but leaves `fact.provenance = Provenance(...)` free to replace the whole
    field with a differently-trusted one, which flips `selectable()` just as
    completely. Freezing `Fact` closes that path too. Callers that need a
    changed fact use `model_copy(update=...)`, which still works on a frozen
    model and is how `core.profile.store.add_fact` produces its
    canonicalised copy.
    """

    model_config = {"extra": "forbid", "populate_by_name": True, "frozen": True}

    id: str
    kind: FactKind
    parent: str | None = None
    text: str
    # Structured header fields for an experience, education or project fact.
    # A resume entry's employer, title and location are checked for equality
    # against these, not for a match somewhere in `text`: free text that says
    # "reported to the Chief Technology Officer" or "consulting for Goldman
    # Sachs" must not let either become the candidate's own title or employer.
    organization: str | None = None
    title: str | None = None
    location: str | None = None
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
