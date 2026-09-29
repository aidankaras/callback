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


_NOT_SHOWN = (
    "is listed but no grounded bullet names it and no confirmed fact under an entry on the "
    "page lists it"
)

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
    assert skills.findings == (f"skill 'SQL' {_NOT_SHOWN}",)


@pytest.mark.slow
def test_a_bullet_that_failed_grounding_is_not_evidence_for_a_skill(tmp_path: Path) -> None:
    """The skill is backed by a fact of its own and named only in a bullet
    that failed; no fact under the entry lists it. Mutation: pass every bullet
    to the skills gate -- this test fails."""
    doc = _doc(skills=["SQL"])
    entry = doc.sections[0].entries[0]
    wrong = entry.model_copy(
        update={
            "bullets": [
                Bullet(text="Cut the review queue to 999 items using SQL", sources=["queue"])
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
    assert f"skill 'SQL' {_NOT_SHOWN}" in by_gate["skills"].findings


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


def _detail(
    fid: str, parent: str, entities: list[str], *, text: str = "Kept the warehouse"
) -> Fact:
    return Fact(
        id=fid,
        kind=FactKind.EXPERIENCE_DETAIL,
        parent=parent,
        text=text,
        entities=entities,
        provenance=CONFIRMED,
    )


def _skills_outcome(doc: ResumeDoc, facts: dict[str, Fact], tmp_path: Path) -> GateOutcome:
    pdf = tmp_path / "resume.pdf"
    render(doc, pdf)
    assert not any("SQL" in b.text for s in doc.sections for e in s.entries for b in e.bullets)
    return next(o for o in run_gates(doc, facts, pdf).outcomes if o.gate == "skills")


@pytest.mark.slow
def test_a_skill_a_fact_under_a_printed_entry_lists_is_shown_by_that_entry(
    tmp_path: Path,
) -> None:
    """A bullet need not name every tool its role used. The role is on the
    page, and a confirmed fact about that role lists the skill."""
    facts = {**FACTS, "stack": _detail("stack", "role", ["SQL"])}

    skills = _skills_outcome(_doc(skills=["Python", "SQL"]), facts, tmp_path)

    assert (skills.verdict, skills.findings) == ("pass", ())


@pytest.mark.slow
def test_a_fact_beside_a_cited_one_belongs_to_the_same_entry(tmp_path: Path) -> None:
    """The entry cites no header fact here; its bullets cite details of the
    role, and another detail of that same role lists the skill."""
    facts = {**FACTS, "stack": _detail("stack", "role", ["SQL"])}
    doc = _doc(skills=["Python", "SQL"])
    bare = doc.sections[0].entries[0].model_copy(update={"sources": []})
    doc = doc.model_copy(
        update={
            "sections": [doc.sections[0].model_copy(update={"entries": [bare]}), doc.sections[1]]
        }
    )

    skills = _skills_outcome(doc, facts, tmp_path)

    assert (skills.verdict, skills.findings) == ("pass", ())


@pytest.mark.slow
@pytest.mark.parametrize(
    "extra",
    [
        pytest.param(
            {
                "other": Fact(
                    id="other",
                    kind=FactKind.EXPERIENCE,
                    text="Analyst at Harbor Lab",
                    organization="Harbor Lab",
                    title="Analyst",
                    provenance=CONFIRMED,
                ),
                "stack": _detail("stack", "other", ["SQL"]),
            },
            id="a-role-that-is-not-on-the-page",
        ),
        pytest.param(
            {
                "stack": _detail("stack", "role", ["SQL"]).model_copy(
                    update={
                        "provenance": Provenance(
                            source="resume.pdf", trust=Trust.IMPORT_UNCONFIRMED
                        )
                    }
                )
            },
            id="a-fact-nobody-confirmed",
        ),
        pytest.param(
            {"stack": _detail("stack", "role", [], text="Migrated the warehouse off SQL")},
            id="a-mention-in-text-that-entities-do-not-list",
        ),
    ],
)
def test_a_skill_no_printed_entry_shows_still_warns(tmp_path: Path, extra: dict[str, Fact]) -> None:
    sql = Fact(id="sql", kind=FactKind.SKILL, text="SQL", provenance=CONFIRMED)

    skills = _skills_outcome(
        _doc(skills=["Python", "SQL"]), {**FACTS, "sql": sql, **extra}, tmp_path
    )

    assert (skills.verdict, skills.findings) == ("warn", (f"skill 'SQL' {_NOT_SHOWN}",))


@pytest.mark.slow
def test_a_skill_the_entry_shows_is_not_reported_for_a_bullet_that_failed(
    tmp_path: Path,
) -> None:
    """The bullet that names Python fails grounding on its number, and the
    grounding gate says so. The skill itself is shown by the role's own fact,
    so the skills gate has nothing to add."""
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

    by_gate = {o.gate: o for o in _run(doc, tmp_path).outcomes}

    assert by_gate["grounding"].verdict == "fail"
    assert (by_gate["skills"].verdict, by_gate["skills"].findings) == ("pass", ())
