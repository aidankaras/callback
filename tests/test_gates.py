from __future__ import annotations

from pathlib import Path

import pytest

import core.gates
from core.gates import (
    GATES,
    GateOutcome,
    GateReport,
    aggregate,
    run_gates,
)
from core.profile.models import Dates, Fact, FactKind, Identity, Provenance, Quantity, Trust
from core.render.typst import render
from core.resume.models import Bullet, Entry, ResumeDoc, Section

CONFIRMED = Provenance(source="onboarding_answer", trust=Trust.USER_CONFIRMED)
FACTS = {
    f.id: f
    for f in (
        Fact(
            id="role",
            kind=FactKind.EXPERIENCE,
            text="Data Engineer at Northwind Analytics in Boston, MA",
            organization="Northwind Analytics",
            title="Data Engineer",
            location="Boston, MA",
            entities=["Northwind Analytics"],
            dates=Dates(start="2024-06", end="2025-08"),
            provenance=CONFIRMED,
        ),
        Fact(
            id="queue",
            kind=FactKind.EXPERIENCE_DETAIL,
            parent="role",
            text="Cut the review queue to 300 items using Python",
            numbers={"queue": Quantity(value=300)},
            entities=["Python"],
            provenance=CONFIRMED,
        ),
        *(
            Fact(
                id=fid,
                kind=FactKind.EXPERIENCE_DETAIL,
                parent="role",
                text=text,
                provenance=CONFIRMED,
            )
            for fid, text in (("loader", "Shipped the loader"), ("runbook", "Wrote the runbook"))
        ),
        Fact(
            id="contact",
            kind=FactKind.LINKS,
            text="Dana Vex, d@example.com",
            entities=["Dana Vex", "d@example.com"],
            provenance=CONFIRMED,
        ),
    )
}


# Two more grounded bullets, so a clean document meets the three-per-role floor.
EXTRA_BULLETS = [
    Bullet(text="Shipped the loader", sources=["loader"]),
    Bullet(text="Wrote the runbook", sources=["runbook"]),
]


def _doc(*, bullet_sources: list[str] | None = None, skills: list[str] | None = None) -> ResumeDoc:
    entry = Entry(
        heading="Northwind Analytics",
        subheading="Data Engineer",
        location="Boston, MA",
        start="Jun 2024",
        end="Aug 2025",
        sources=["role"],
        bullets=[
            Bullet(
                text="Cut the review queue to 300 items using Python",
                sources=bullet_sources or ["queue"],
            ),
            *EXTRA_BULLETS,
        ],
    )
    sections = [Section(title="Experience", entries=[entry])]
    sections.append(Section(title="Skills", skills=skills or ["Python"]))
    return ResumeDoc(identity=Identity(name="Dana Vex", email="d@example.com"), sections=sections)


def _run(doc: ResumeDoc, tmp_path: Path) -> core.gates.GateReport:
    pdf = tmp_path / "resume.pdf"
    render(doc, pdf)
    return run_gates(doc, FACTS, pdf)


def _outcome(verdict: str) -> GateOutcome:
    return GateOutcome("claims", verdict, ())  # type: ignore[arg-type]


# --- composition ---------------------------------------------------------------


@pytest.mark.slow
def test_a_clean_document_passes_every_gate(tmp_path: Path) -> None:
    report = _run(_doc(), tmp_path)
    assert [(o.gate, o.verdict) for o in report.outcomes] == [(g, "pass") for g in GATES], [
        (o.gate, o.findings) for o in report.outcomes
    ]
    assert report.verdict == "pass"


@pytest.mark.slow
def test_one_failing_gate_does_not_hide_the_others(tmp_path: Path) -> None:
    report = _run(_doc(bullet_sources=["nope"]), tmp_path)
    by_gate = {o.gate: o for o in report.outcomes}
    assert tuple(by_gate) == GATES
    assert by_gate["grounding"].verdict == "fail"
    assert by_gate["grounding"].findings == ("bullet 0: cites unknown fact(s) ['nope']",)
    assert report.verdict == "fail"


@pytest.mark.slow
def test_a_gate_that_raises_is_a_failed_outcome_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_: Path) -> tuple[str, list[object]]:
        raise RuntimeError("pdf unreadable")

    monkeypatch.setattr(core.gates, "check_hidden", boom)
    report = _run(_doc(), tmp_path)
    hidden = next(o for o in report.outcomes if o.gate == "hidden")
    assert hidden.verdict == "fail"
    assert hidden.findings == ("gate raised RuntimeError: pdf unreadable",)
    assert tuple(o.gate for o in report.outcomes) == GATES
    assert report.verdict == "fail"


@pytest.mark.slow
def test_a_skill_only_listed_is_a_skills_warning(tmp_path: Path) -> None:
    facts_with_sql = {
        **FACTS,
        "sql": Fact(id="sql", kind=FactKind.SKILL, text="SQL", provenance=CONFIRMED),
    }
    doc = _doc(skills=["Python", "SQL"])
    pdf = tmp_path / "resume.pdf"
    render(doc, pdf)
    report = run_gates(doc, facts_with_sql, pdf)
    skills = next(o for o in report.outcomes if o.gate == "skills")
    assert skills.verdict == "warn"
    assert "skill 'SQL' is listed but no grounded bullet shows it" in skills.findings


@pytest.mark.slow
def test_a_bullet_that_failed_grounding_is_not_evidence_for_a_skill(tmp_path: Path) -> None:
    """Mutation: pass every bullet to the skills gate -- this test fails."""
    doc = _doc()
    entry = doc.sections[0].entries[0]
    wrong = entry.model_copy(
        update={
            "bullets": [
                Bullet(text="Cut the review queue to 999 items using Python", sources=["queue"])
            ]
        }
    )
    doc = doc.model_copy(
        update={
            "sections": [doc.sections[0].model_copy(update={"entries": [wrong]}), doc.sections[1]]
        }
    )
    report = _run(doc, tmp_path)
    by_gate = {o.gate: o for o in report.outcomes}
    assert by_gate["grounding"].verdict == "fail"
    assert by_gate["skills"].verdict == "warn"
    assert "skill 'Python' is listed but no grounded bullet shows it" in by_gate["skills"].findings


@pytest.mark.slow
def test_a_document_with_no_bullets_fails_grounding(tmp_path: Path) -> None:
    """Mutation: drop the no-bullets condition -- this test fails."""
    doc = _doc()
    bare = doc.sections[0].entries[0].model_copy(update={"bullets": []})
    doc = doc.model_copy(
        update={
            "sections": [doc.sections[0].model_copy(update={"entries": [bare]}), doc.sections[1]]
        }
    )
    grounding = next(o for o in _run(doc, tmp_path).outcomes if o.gate == "grounding")
    assert grounding.verdict == "fail"
    assert grounding.findings == ("document has no bullets to ground",)


# --- the verdict rule ------------------------------------------------------------


def test_any_failed_gate_fails_the_document() -> None:
    assert aggregate([_outcome("pass"), _outcome("fail"), _outcome("warn")]) == "fail"


def test_all_passing_gates_pass_the_document() -> None:
    assert aggregate([_outcome("pass")] * 5) == "pass"


def test_no_outcomes_at_all_is_not_a_pass() -> None:
    assert aggregate([]) == "fail"


def test_a_warning_without_a_failure_is_a_warning() -> None:
    assert aggregate([_outcome("pass"), _outcome("warn"), _outcome("pass")]) == "warn"


@pytest.mark.parametrize("unknown", ["error", "PASS", ""])
def test_an_unrecognised_verdict_fails_rather_than_passes(unknown: str) -> None:
    """Mutation: aggregate by denylist ("fail" in verdicts) -- this test fails."""
    assert aggregate([_outcome("pass"), _outcome(unknown)]) == "fail"
    assert aggregate([_outcome("warn"), _outcome(unknown)]) == "fail"


def test_a_report_missing_or_reordering_a_gate_cannot_be_built() -> None:
    outcomes = [GateOutcome(g, "pass", ()) for g in GATES]
    with pytest.raises(ValueError, match="every gate once"):
        GateReport(tuple(outcomes[:-1]))
    with pytest.raises(ValueError, match="every gate once"):
        GateReport((outcomes[1], outcomes[0], *outcomes[2:]))
    assert GateReport(tuple(outcomes)).verdict == "pass"


def test_a_report_verdict_is_derived_from_its_outcomes() -> None:
    outcomes = [GateOutcome(g, "pass", ()) for g in GATES]
    outcomes[3] = GateOutcome("hidden", "fail", ("invisible_text: x",))
    assert GateReport(tuple(outcomes)).verdict == "fail"


@pytest.mark.slow
def test_a_layout_lint_makes_an_otherwise_clean_document_a_warning(tmp_path: Path) -> None:
    """One bullet in a role is below the three-per-role floor. Mutation: make
    the lints gate always pass -- this test fails."""
    doc = _doc()
    entry = doc.sections[0].entries[0]
    single = entry.model_copy(update={"bullets": entry.bullets[:1]})
    doc = doc.model_copy(
        update={
            "sections": [doc.sections[0].model_copy(update={"entries": [single]}), doc.sections[1]]
        }
    )
    report = _run(doc, tmp_path)
    verdicts = {o.gate: o.verdict for o in report.outcomes}
    assert verdicts == {g: ("warn" if g == "lints" else "pass") for g in GATES}, report.outcomes
    assert report.verdict == "warn"
