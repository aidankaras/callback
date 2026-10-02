"""Grouped skills: `Label: a, b, c` rows in a skills section.

Every check reads skills through `Section.all_skills`, so a grouped skill is
held to exactly the rules a flat one is. The round-trip tests collide on
value, not layout: a declared skill the render never printed, sitting at the
end of a labelled row, where a label-stripping bug would hide it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from pypdf import PdfReader

from core.checks.claims import check_claims
from core.checks.roundtrip import check_roundtrip
from core.export.text import to_text
from core.profile.models import Fact, FactKind, Identity, Provenance, Trust
from core.render.typst import render
from core.resume.models import ResumeDoc, Section, SkillGroup

IDENTITY = Identity(name="Dana Vex", email="dana@example.com")
CONFIRMED = Provenance(source="user:test", trust=Trust.USER_CONFIRMED)


def grouped(*groups: tuple[str, list[str]]) -> ResumeDoc:
    return ResumeDoc(
        identity=IDENTITY,
        sections=[
            Section(
                title="Technical Skills",
                skill_groups=[SkillGroup(label=label, skills=skills) for label, skills in groups],
            )
        ],
    )


# --- the model ------------------------------------------------------------------


@pytest.mark.parametrize("skills", [["C: the language"], ["Python", "SQL:2016"]])
def test_a_colon_in_a_skill_is_refused_flat_or_grouped(skills: list[str]) -> None:
    with pytest.raises(ValidationError, match="colon"):
        Section(title="Skills", skills=skills)
    with pytest.raises(ValidationError, match="colon"):
        SkillGroup(label="Languages", skills=skills)


@pytest.mark.parametrize("label", ["Languages: core", "Languages, Tools", " "])
def test_a_label_with_a_separator_or_nothing_is_refused(label: str) -> None:
    with pytest.raises(ValidationError):
        SkillGroup(label=label, skills=["Python"])


def test_an_empty_group_is_refused() -> None:
    with pytest.raises(ValidationError):
        SkillGroup(label="Languages", skills=[])


def test_a_section_holds_flat_skills_or_groups_not_both() -> None:
    with pytest.raises(ValidationError, match="skills and skill_groups"):
        Section(
            title="Skills",
            skills=["Python"],
            skill_groups=[SkillGroup(label="Tools", skills=["Git"])],
        )


def test_all_skills_reads_flat_then_grouped_in_order() -> None:
    section = grouped(("Languages", ["Python", "SQL"]), ("Tools", ["Git"])).sections[0]
    assert section.all_skills == ["Python", "SQL", "Git"]


# --- the claims gate --------------------------------------------------------------


def test_an_unbacked_skill_inside_a_group_fails_claims() -> None:
    facts = {
        "skills": Fact.model_validate(
            {"id": "skills", "kind": FactKind.SKILL, "text": "Python", "provenance": CONFIRMED}
        ),
        "contact": Fact.model_validate(
            {
                "id": "contact",
                "kind": FactKind.LINKS,
                "text": "Dana Vex, dana@example.com",
                "entities": ["Dana Vex", "dana@example.com"],
                "provenance": CONFIRMED,
            }
        ),
    }
    findings = check_claims(grouped(("Languages", ["Python", "Kubernetes"])), facts).findings
    assert "skill 'Kubernetes' is backed by no confirmed fact" in findings
    assert not any("'Python'" in f for f in findings)


@pytest.mark.parametrize("label", ["Languages & Tools", "languages and tools", "AI Engineering"])
def test_a_category_label_passes_claims_in_either_conjunction(label: str) -> None:
    findings = check_claims(grouped((label, ["Python"])), _python_facts()).findings
    assert not any("label" in f for f in findings)


@pytest.mark.parametrize(
    "label", ["Certified Cloud Architect", "Expert Languages", "Languages & Kubernetes"]
)
def test_a_label_outside_the_category_list_fails_claims(label: str) -> None:
    """A bold label reads as a claim and no fact can back it. Mutation: skip
    the label check -- each of these renders as an unbacked bold claim."""
    findings = check_claims(grouped((label, ["Python"])), _python_facts()).findings
    assert findings == (f"skill group label {label!r} is not a recognised category name",)


def _python_facts() -> dict[str, Fact]:
    """Facts backing the identity and the skill Python, so a label is the only
    thing left to fail."""
    return {
        "skills": Fact.model_validate(
            {"id": "skills", "kind": FactKind.SKILL, "text": "Python", "provenance": CONFIRMED}
        ),
        "contact": Fact.model_validate(
            {
                "id": "contact",
                "kind": FactKind.LINKS,
                "text": "Dana Vex, dana@example.com",
                "entities": ["Dana Vex", "dana@example.com"],
                "provenance": CONFIRMED,
            }
        ),
    }


# --- the text export ----------------------------------------------------------------


def test_the_text_export_keeps_each_group_on_its_own_labelled_line() -> None:
    text = to_text(grouped(("Languages", ["Python", "SQL"]), ("Tools", ["Git"])))
    assert "Languages: Python, SQL\n" in text
    assert "Tools: Git\n" in text


# --- the round-trip gate --------------------------------------------------------------


ROWS = (
    ("Languages & Tools", ["Python", "SQL", "Bash", "Git"]),
    ("Libraries", ["NumPy", "pandas", "scikit-learn", "PyTorch"]),
    ("Statistics", ["hypothesis testing", "bootstrap resampling", "Monte Carlo methods"]),
)


@pytest.mark.slow
def test_every_grouped_skill_is_read_back_from_its_labelled_row(tmp_path: Path) -> None:
    doc = grouped(*ROWS)
    out = tmp_path / "resume.pdf"
    render(doc, out)
    result = check_roundtrip(doc, out)
    assert result.skills_unrecovered == []


@pytest.mark.slow
def test_a_grouped_skill_the_render_never_printed_is_reported(tmp_path: Path) -> None:
    # Declared with a last item on every row that the rendered PDF lacks: the
    # position a label-stripping or row-joining bug would cover up.
    printed = grouped(*ROWS)
    declared = grouped(*((label, [*skills, "Terraform"]) for label, skills in ROWS))
    out = tmp_path / "resume.pdf"
    render(printed, out)
    page_text = "".join(page.extract_text() for page in PdfReader(out).pages)
    assert "Terraform" not in page_text  # the premise: the PDF really lacks it
    assert "Monte Carlo methods" in page_text
    result = check_roundtrip(declared, out)
    assert result.skills_unrecovered == ["Terraform", "Terraform", "Terraform"]
