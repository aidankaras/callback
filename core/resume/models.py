"""The resume document: the canonical input to rendering.

This is deliberately a separate model tree from `core.profile`, not a view
onto it: a `Fact` is candidate-controlled source material, while a
`ResumeDoc` is the arrangement of that material into sections, entries and
bullets that actually gets typeset. Keeping them distinct means a rendering
change can never accidentally mutate stored facts, and a document can
reference the same fact from multiple places (a number cited in a bullet and
again in a derived summary) without the profile store needing to know
anything about documents at all.

Every bullet and every entry header carries `sources`: the fact IDs the
grounding gate should check its text against. Without that on `Entry`, a
header's employer, title and date range would be free text no gate ever
reads — a fabricated employer would be just as invisible to grounding as an
unsourced bullet, which defeats the point of grounding as a whole.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.profile.models import Identity


class Bullet(BaseModel):
    """A single grounded claim: text plus the facts it must be derivable from."""

    model_config = {"extra": "forbid"}

    text: str
    sources: list[str] = Field(min_length=1)


class Entry(BaseModel):
    """One employer, role, or program within a section.

    `sources` cites the facts that back the header fields (`heading`,
    `subheading`, `location`, `start`, `end`) — separately from each
    bullet's own `sources`, since a bullet can restate or derive a claim its
    entry's header never makes. It defaults to empty because no gate reads
    it yet; a header with no cited facts simply isn't checked, the same as
    an old document that predates this field.
    """

    model_config = {"extra": "forbid"}

    heading: str
    subheading: str | None = None
    location: str | None = None
    start: str | None = None
    end: str | None = None
    # `default_factory=list[Bullet]`, not bare `list` — see the same note on
    # `Fact.derived` in `core.profile.models`: pyright strict can't bind a
    # bare `list` factory against a `list[SomeBaseModel]` target without
    # widening the field to `list[Unknown]`.
    bullets: list[Bullet] = Field(default_factory=list[Bullet])
    sources: list[str] = Field(default_factory=list)


class Section(BaseModel):
    """A titled group of entries (e.g. "Experience", "Education")."""

    model_config = {"extra": "forbid"}

    title: str
    entries: list[Entry] = Field(default_factory=list[Entry])


class ResumeDoc(BaseModel):
    """The whole document: an identity plus its sections, ready to render."""

    model_config = {"extra": "forbid"}

    identity: Identity
    sections: list[Section] = Field(default_factory=list[Section])
