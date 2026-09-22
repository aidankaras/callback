from __future__ import annotations

import pytest

from core.checks.claims import check_claims
from core.profile.models import Dates, Fact, FactKind, Identity, Provenance, Trust
from core.resume.models import Bullet, Entry, ResumeDoc, Section

CONFIRMED = Provenance(source="onboarding_answer", trust=Trust.USER_CONFIRMED)
IDENTITY = Identity(name="Dana Vex", email="dana@example.com")


def fact(fid: str, text: str, **kw: object) -> Fact:
    body: dict[str, object] = {
        "id": fid,
        "kind": FactKind.EXPERIENCE,
        "text": text,
        "provenance": CONFIRMED,
    }
    body.update(kw)
    return Fact.model_validate(body)


ROLE = fact(
    "role",
    "Data Engineer at Northwind Analytics in Boston, MA",
    entities=["Northwind Analytics"],
    dates=Dates(start="2024-06", end="2025-08"),
)
CURRENT = fact(
    "current",
    "Research Assistant at Harbor Lab",
    entities=["Harbor Lab"],
    dates=Dates(start="2025-09"),
)
SKILLS = fact("skills", "Kubernetes", kind=FactKind.SKILL)
DETAIL = fact(
    "detail",
    "Moved the event bus to Kafka",
    kind=FactKind.EXPERIENCE_DETAIL,
    entities=["Kafka"],
)
FACTS = {f.id: f for f in (ROLE, CURRENT, SKILLS, DETAIL)}


def doc(*, entry: dict[str, object] | None = None, skills: list[str] | None = None) -> ResumeDoc:
    body: dict[str, object] = {
        "heading": "Northwind Analytics",
        "subheading": "Data Engineer",
        "location": "Boston, MA",
        "start": "Jun 2024",
        "end": "Aug 2025",
        "sources": ["role"],
        "bullets": [Bullet(text="Moved the event bus to Kafka", sources=["detail"])],
    }
    body.update(entry or {})
    sections = [Section(title="Experience", entries=[Entry.model_validate(body)])]
    if skills is not None:
        sections.append(Section(title="Skills", skills=skills))
    return ResumeDoc(identity=IDENTITY, sections=sections)


def test_an_honest_header_passes() -> None:
    assert check_claims(doc(skills=["Kubernetes", "Kafka"]), FACTS).verdict == "pass"


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        # The invented employer shares a word with the real one, so a
        # token-overlap or substring rule would let it through.
        ({"heading": "Northwind Capital"}, "employer"),
        ({"heading": "Analytics"}, "employer"),
        ({"subheading": "Senior Data Engineer"}, "title"),
        ({"location": "New York, NY"}, "location"),
        ({"start": "Jun 2023"}, "start date"),
        ({"end": "Present"}, "end date"),
        ({"start": "Summer 2024"}, "start date"),
        ({"sources": []}, "cites no fact"),
        ({"sources": ["nope"]}, "unknown fact"),
    ],
)
def test_a_fabricated_header_field_fails(change: dict[str, object], expected: str) -> None:
    result = check_claims(doc(entry=change), FACTS)
    assert result.verdict == "fail"
    assert any(expected in f for f in result.findings), result.findings


def test_a_shortened_title_is_not_inflation() -> None:
    assert check_claims(doc(entry={"subheading": "Engineer"}), FACTS).verdict == "pass"


def test_present_is_allowed_only_for_a_role_that_has_not_ended() -> None:
    current: dict[str, object] = {
        "heading": "Harbor Lab",
        "subheading": "Research Assistant",
        "location": None,
        "start": "Sep 2025",
        "end": "Present",
        "sources": ["current"],
    }
    assert check_claims(doc(entry=current), FACTS).verdict == "pass"


def test_a_bare_year_is_compared_to_the_fact_year() -> None:
    assert check_claims(doc(entry={"start": "2024", "end": "2025"}), FACTS).verdict == "pass"
    assert check_claims(doc(entry={"start": "2023"}), FACTS).verdict == "fail"


def test_an_unconfirmed_fact_cannot_back_a_header() -> None:
    imported = ROLE.model_copy(
        update={
            "provenance": Provenance(source="import:resume.pdf", trust=Trust.IMPORT_UNCONFIRMED)
        }
    )
    result = check_claims(doc(), {**FACTS, "role": imported})
    assert result.verdict == "fail"
    assert any("not confirmed" in f for f in result.findings), result.findings


def test_a_skill_with_no_confirmed_fact_fails() -> None:
    result = check_claims(doc(skills=["Kubernetes", "Terraform"]), FACTS)
    assert result.verdict == "fail"
    assert result.findings == ["skill 'Terraform' is backed by no confirmed fact"]


def test_a_skill_matches_through_its_alias() -> None:
    assert check_claims(doc(skills=["k8s"]), FACTS).verdict == "pass"


def test_a_skill_fact_stored_as_an_alias_backs_the_canonical_name() -> None:
    """The fact is the side written in the candidate's own words, so it is
    the side most likely to hold an alias. Mutation: compare skill facts
    without canonicalising them -- this test fails."""
    torch = fact("torch", "torch", kind=FactKind.SKILL)
    assert check_claims(doc(skills=["PyTorch"]), {**FACTS, "torch": torch}).verdict == "pass"


def test_a_skill_evidenced_by_a_cited_fact_passes_without_a_skill_fact() -> None:
    assert check_claims(doc(skills=["Kafka"]), FACTS).verdict == "pass"


def test_a_skill_fact_that_is_unconfirmed_does_not_back_a_skill() -> None:
    imported = SKILLS.model_copy(
        update={
            "provenance": Provenance(source="import:resume.pdf", trust=Trust.IMPORT_UNCONFIRMED)
        }
    )
    assert check_claims(doc(skills=["Kubernetes"]), {**FACTS, "skills": imported}).verdict == "fail"
