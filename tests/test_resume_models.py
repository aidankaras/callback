"""The resume document models, in particular `Entry.sources`.

Without `sources` on `Entry`, an entry's employer, title and date range are
free text no gate reads, so a fabricated employer would be invisible to
grounding even though a fabricated bullet is not. These tests only check
that the field exists with the right shape — the grounding gate itself
doesn't read it yet; that's separate work.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.profile.models import Identity
from core.resume.models import Bullet, Entry, ResumeDoc, Section

IDENTITY = Identity(name="Dana Vex", email="dana@example.com")


def test_entry_sources_defaults_to_empty() -> None:
    entry = Entry(heading="Northwind Analytics")
    assert entry.sources == []


def test_entry_accepts_explicit_sources() -> None:
    entry = Entry(heading="Northwind Analytics", sources=["fact-employer-1"])
    assert entry.sources == ["fact-employer-1"]


def test_entry_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Entry.model_validate({"heading": "x", "nickname": "y"})


def test_bullet_requires_at_least_one_source() -> None:
    with pytest.raises(ValidationError):
        Bullet(text="Did a thing", sources=[])


def test_resume_doc_round_trips_sources_through_sections() -> None:
    doc = ResumeDoc(
        identity=IDENTITY,
        sections=[
            Section(
                title="Experience",
                entries=[
                    Entry(
                        heading="Northwind Analytics",
                        sources=["fact-employer-1"],
                        bullets=[Bullet(text="Did a thing", sources=["fact-bullet-1"])],
                    )
                ],
            )
        ],
    )
    entry = doc.sections[0].entries[0]
    assert entry.sources == ["fact-employer-1"]
    assert entry.bullets[0].sources == ["fact-bullet-1"]
