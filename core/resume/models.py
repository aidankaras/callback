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

import unicodedata

from pydantic import BaseModel, Field, field_validator, model_validator

from core.profile.models import Identity


def carries_content(text: str) -> bool:
    """True if `text` contains at least one letter or digit.

    `str.strip()` is not enough: it leaves zero-width and soft-hyphen format
    characters in place, and says nothing about a string of only dashes,
    dots or bullet glyphs. None of those makes a claim, so none can be
    grounded, and each would otherwise pass every per-token check by giving
    it nothing to reject.
    """
    return any(unicodedata.category(c)[0] in "LN" for c in text)


class Bullet(BaseModel):
    """A single grounded claim: text plus the facts it must be derivable from."""

    model_config = {"extra": "forbid"}

    text: str
    sources: list[str] = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def _text_is_not_blank(cls, text: str) -> str:
        """Refuse a bullet with no text: it makes no claim, so no grounding
        check has anything to reject, and it would otherwise pass at the
        strongest verdict its sources allow."""
        if not carries_content(text):
            raise ValueError("a bullet's text cannot be blank: it has no letter or digit")
        return text


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


def _check_skill_names(skills: list[str]) -> list[str]:
    """Refuse a blank skill, and a skill containing a comma or a colon.

    The reasons are `Section._skills_are_single_names`'s. The colon joins
    the comma because a grouped skills row renders as `Label: a, b, c`, and
    the round-trip gate (`core.checks.roundtrip._row_items`) strips
    everything up to an item's last colon to separate a label from the
    first skill after it. That strip is sound only while no skill's own
    name can contain a colon, on a flat run as well as in a group, since
    the matcher cannot tell which kind of row it is reading.
    """
    for skill in skills:
        if not skill.strip():
            raise ValueError("a skill cannot be blank")
        if "," in skill:
            raise ValueError(
                f"skill {skill!r} contains a comma; split it into separate skills "
                "-- a skills section renders as one comma-delimited line, so a comma "
                "inside a single skill's own name cannot be told apart from the "
                "boundary between two skills"
            )
        if ":" in skill:
            raise ValueError(
                f"skill {skill!r} contains a colon; a colon in a skills row is what "
                "separates a group's label from its first skill"
            )
    return skills


class SkillGroup(BaseModel):
    """One labelled row of a skills section: `Languages & Tools: Python, SQL`.

    The label is a heading for the reader, not a claim: only the skills are
    checked against facts. It may contain neither a comma nor a colon, for
    the reason `_check_skill_names` gives for skills.
    """

    model_config = {"extra": "forbid"}

    label: str
    skills: list[str] = Field(min_length=1)

    @field_validator("label")
    @classmethod
    def _label_is_plain(cls, label: str) -> str:
        if not label.strip():
            raise ValueError("a skill group's label cannot be blank")
        if "," in label or ":" in label:
            raise ValueError(f"skill group label {label!r} cannot contain a comma or a colon")
        return label

    @field_validator("skills")
    @classmethod
    def _skills_are_single_names(cls, skills: list[str]) -> list[str]:
        return _check_skill_names(skills)


class Section(BaseModel):
    """A titled group of entries (e.g. "Experience", "Education"), or a
    titled list of skills, flat (`skills`) or in labelled rows
    (`skill_groups`) — only ever one of the three.

    The two are mutually exclusive because they render differently and are
    checked differently. An entry has a heading a parser can anchor on; a
    skills list is a run of short comma-separated names with no heading of
    its own, and treating one as the other would make a skills list look
    like an employer name to anything that anchors on entry headings (the
    round-trip gate's ordering check, in particular).
    """

    model_config = {"extra": "forbid"}

    title: str
    entries: list[Entry] = Field(default_factory=list[Entry])
    skills: list[str] = Field(default_factory=list[str])
    skill_groups: list[SkillGroup] = Field(default_factory=list[SkillGroup])

    @property
    def all_skills(self) -> list[str]:
        """Every skill the section lists, flat or grouped, in reading order.
        Every check reads skills through this, so a grouped skill is held to
        exactly the rules a flat one is."""
        return [*self.skills, *(skill for group in self.skill_groups for skill in group.skills)]

    @field_validator("skills")
    @classmethod
    def _skills_are_single_names(cls, skills: list[str]) -> list[str]:
        """Refuse a blank skill and a skill containing its own comma.

        Both are rejected here, at the point the value enters the document,
        rather than left for something downstream to work around, because
        neither has a recoverable meaning once accepted. A blank skill
        contributes nothing and only ever signals a data-entry slip; keeping
        it produces a real defect one step later, not a cosmetic one -- see
        `core.checks.skills`'s `_mentions`, whose word-boundary lookaround
        degenerates to a zero-width match for an empty pattern, so a blank
        skill is reported "evidenced" by the first stray `", "` or double
        space in any grounded bullet, while the real skill sharing its ratio
        is pushed toward "unevidenced" in its place.

        A comma-containing skill is worse than blank: it is ambiguous by
        construction, not merely wrong. `templates/resume.typ` renders a
        section's skills as one comma-joined run
        (`section.skills.join(", ")`), and the round-trip gate's own skill
        matcher (`core.checks.roundtrip._row_items`) recovers a skill by
        splitting that run on every comma and comparing items exactly --
        which makes "Tableau, Looker" as a *single* declared skill
        structurally indistinguishable, once rendered, from the two
        adjacent skills "Tableau" and "Looker". No amount of care in that
        matcher can recover which one the author meant; only rejecting the
        value before it is ever rendered can.
        """
        return _check_skill_names(skills)

    @model_validator(mode="after")
    def _entries_or_skills(self) -> Section:
        used = [name for name in ("entries", "skills", "skill_groups") if getattr(self, name)]
        if len(used) > 1:
            raise ValueError(
                f"section {self.title!r} has {' and '.join(used)}; a section holds only one"
            )
        return self


class ResumeDoc(BaseModel):
    """The whole document: an identity plus its sections, ready to render."""

    model_config = {"extra": "forbid"}

    identity: Identity
    sections: list[Section] = Field(default_factory=list[Section])
