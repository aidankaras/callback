"""`check_skills`: whether a listed skill is named in a bullet that already
passed grounding, or listed with nothing behind it.

Every test here builds its own tiny `ResumeDoc` rather than importing a
shared fixture, because the point under test each time is a specific
relationship between a document's skills and its bullets -- sharing one
fixture across cases would obscure exactly the thing each test exists to
isolate.
"""

from __future__ import annotations

from core.checks.skills import check_skills
from core.profile.models import Identity
from core.resume.models import Bullet, Entry, ResumeDoc, Section


def _doc(bullets: list[str], skills: list[str]) -> ResumeDoc:
    return ResumeDoc(
        identity=Identity(name="Dana Vex", email="d@example.com"),
        sections=[
            Section(
                title="Experience",
                entries=[
                    Entry(
                        heading="Northwind Analytics",
                        bullets=[Bullet(text=b, sources=["a"]) for b in bullets],
                    )
                ],
            ),
            Section(title="Skills", skills=skills),
        ],
    )


def test_a_skill_named_in_a_grounded_bullet_is_evidenced() -> None:
    bullets = ["Built the ingestion path in Python against PostgreSQL"]
    result = check_skills(_doc(bullets, ["Python", "PostgreSQL", "Kubernetes"]), bullets)
    assert set(result.evidenced) == {"Python", "PostgreSQL"}
    assert result.listed_only == ["Kubernetes"]
    assert result.evidenced_ratio == 2 / 3


def test_an_alias_in_the_bullet_evidences_the_canonical_skill() -> None:
    """The document may say "Postgres" in prose and "PostgreSQL" in its skills
    list. Both name the same thing, and reporting the skill as unevidenced
    would be a false finding about the document's own honesty.
    """
    bullets = ["Tuned Postgres query plans"]
    result = check_skills(_doc(bullets, ["PostgreSQL"]), bullets)
    assert result.evidenced == ["PostgreSQL"]


def test_a_skill_named_only_in_an_ungrounded_bullet_is_listed_only() -> None:
    """The point of the check: a bullet that did not pass grounding is not
    evidence, so a skill mentioned only there stays listed-only.
    """
    bullets = ["Shipped a Kubernetes migration"]
    result = check_skills(_doc(bullets, ["Kubernetes"]), grounded_bullets=[])
    assert result.listed_only == ["Kubernetes"]
    assert result.evidenced_ratio == 0.0


def test_a_skill_named_only_in_an_ungrounded_bullet_gets_its_own_finding() -> None:
    """Distinct from a skill that appears nowhere at all: this one has a
    specific bullet a reader could point at and ground, so it gets a
    finding naming that, rather than disappearing into `listed_only` with
    no more to say than a skill absent from the whole document would have.
    """
    bullets = ["Shipped a Kubernetes migration"]
    result = check_skills(_doc(bullets, ["Kubernetes"]), grounded_bullets=[])
    assert any("did not pass grounding" in finding for finding in result.findings)


def test_a_skill_named_nowhere_gets_no_such_finding() -> None:
    bullets = ["Owned the reporting pipeline end to end"]
    result = check_skills(_doc(bullets, ["Kubernetes"]), grounded_bullets=bullets)
    assert result.listed_only == ["Kubernetes"]
    assert result.findings == []


def test_a_substring_of_a_longer_word_does_not_evidence() -> None:
    """ "Go" must not be evidenced by "Going", and this is the defect class
    this repository has hit most often -- an unanchored substring match.
    """
    bullets = ["Going through the migration backlog"]
    result = check_skills(_doc(bullets, ["Go"]), bullets)
    assert result.listed_only == ["Go"]


def test_a_skill_ending_in_punctuation_is_still_matched_as_a_whole_word() -> None:
    """Guards the lookaround-vs-`\\b` distinction the module docstring
    explains: a plain `\\bC\\+\\+\\b` fails to match "C++" immediately
    followed by a space, because `+` is itself a non-word character and
    `\\b` requires a word/non-word transition that isn't there.
    """
    bullets = ["Rewrote the renderer in C++ for latency"]
    result = check_skills(_doc(bullets, ["C++"]), bullets)
    assert result.evidenced == ["C++"]


def test_no_skills_section_reports_an_empty_result_not_a_failure() -> None:
    doc = ResumeDoc(
        identity=Identity(name="Dana Vex", email="d@example.com"),
        sections=[Section(title="Experience", entries=[Entry(heading="Northwind")])],
    )
    result = check_skills(doc, [])
    assert result.listed == [] and result.findings == [] and result.evidenced_ratio == 1.0


def test_a_skill_repeated_under_two_spellings_is_counted_once() -> None:
    """ "Postgres" and "PostgreSQL" name the same skill; listing both should
    not let the document claim two skills' worth of evidence from one.
    """
    bullets = ["Tuned PostgreSQL query plans"]
    doc = ResumeDoc(
        identity=Identity(name="Dana Vex", email="d@example.com"),
        sections=[
            Section(
                title="Experience",
                entries=[
                    Entry(
                        heading="Northwind Analytics",
                        bullets=[Bullet(text=b, sources=["a"]) for b in bullets],
                    )
                ],
            ),
            Section(title="Skills", skills=["Postgres", "PostgreSQL"]),
        ],
    )
    result = check_skills(doc, bullets)
    assert result.listed == ["Postgres"]
    assert result.evidenced == ["Postgres"]
    assert result.evidenced_ratio == 1.0
