from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from core.apply import load_applications
from core.gates import FactFinding, run_gates
from core.insights import rejected_postings
from core.mcp_server import server
from core.paths import ensure_home
from core.profile.models import Fact, FactKind, Identity, Provenance, Trust
from core.recurring import MIN_JOBS, Report, recurring, render
from core.resume.models import Bullet, Entry, ResumeDoc, Section
from core.triage import named_by

ETL = ("unknown_entity", ["etl"], "'ETL' (canonical form 'ETL') appears in no cited fact")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    return ensure_home()


def _record(home: Path, job: str, *findings: tuple[str, list[str], str]) -> None:
    draft = home / "outputs" / job / "draft"
    draft.mkdir(parents=True, exist_ok=True)
    verdict = "fail" if findings else "pass"
    body: dict[str, object] = {
        "verdict": verdict,
        "outcomes": [{"gate": "grounding", "verdict": verdict, "findings": []}],
        "fact_findings": [{"kind": k, "facts": f, "detail": d} for k, f, d in findings],
    }
    (draft / "report.json").write_text(json.dumps(body), encoding="utf-8")


def test_grounding_names_the_facts_behind_each_failure(tmp_path: Path) -> None:
    """The per-fact record `recurring` reads, produced by the real gate."""
    fact = Fact(
        id="etl",
        kind=FactKind.EXPERIENCE_DETAIL,
        text="Built the data pipeline",
        provenance=Provenance(source="user", trust=Trust.USER_CONFIRMED),
    )
    doc = ResumeDoc(
        identity=Identity(name="Dana Vex", email="d@example.com"),
        sections=[
            Section(
                title="Experience",
                entries=[
                    Entry(
                        heading="Northwind",
                        bullets=[
                            Bullet(text="Built the ETL pipeline", sources=["etl", "etl"]),
                            Bullet(text="Built the data pipeline", sources=["etl"]),
                        ],
                    )
                ],
            )
        ],
    )
    # No PDF: every gate after grounding crashes into a recorded failure,
    # which leaves the grounding outcome, computed first, to inspect.
    grounding = run_gates(doc, {"etl": fact}, tmp_path / "absent.pdf").outcomes[0]
    assert grounding.gate == "grounding"
    assert grounding.fact_findings == (FactFinding(ETL[0], ("etl",), ETL[2]),)


def test_a_failure_in_two_jobs_recurs_and_in_one_does_not(home: Path) -> None:
    """Mutation: set MIN_JOBS to 1 -- the one-job failure is reported too."""
    assert MIN_JOBS == 2
    _record(home, "acme", ETL)
    _record(home, "globex", ETL, ("unresolved_number", ["scale"], "'40' is not derivable"))
    _record(home, "initech")
    report = recurring()
    assert [(r.facts, r.jobs) for r in report.recurring] == [(("etl",), ("acme", "globex"))]
    assert report.unreadable == ()
    assert "fix the fact once" in render(report)


def test_a_job_counts_once_however_often_it_was_rebuilt(home: Path) -> None:
    """Only the latest build per job is on disk, and one job's repeated findings are one."""
    _record(home, "acme", ETL, ETL)
    assert recurring().recurring == ()


def test_an_unreadable_record_is_named_not_skipped(home: Path) -> None:
    """Premise first: without it this job would be absent, not unreadable."""
    _record(home, "acme", ETL)
    _record(home, "globex", ETL)
    (home / "outputs" / "globex" / "draft" / "report.json").write_text("{not json")
    assert (home / "outputs" / "globex" / "draft" / "report.json").exists()
    report = recurring()
    assert report.recurring == () and report.unreadable == ("globex",)
    assert "could not read the build record for: globex" in render(report)


def _grounding_only(home: Path, job: str, verdict: str, **extra: object) -> None:
    draft = home / "outputs" / job / "draft"
    draft.mkdir(parents=True)
    outcomes = [{"gate": "grounding", "verdict": verdict, "findings": ["bullet 0: ..."]}]
    (draft / "report.json").write_text(json.dumps({"outcomes": outcomes, **extra}))


def test_a_passing_record_from_before_per_fact_findings_is_neither_unreadable_nor_untraced(
    home: Path,
) -> None:
    _grounding_only(home, "old", "pass")
    assert recurring() == Report((), (), ())


def test_a_grounding_failure_that_names_no_fact_is_listed_not_read_as_clean(home: Path) -> None:
    """A record from before per-fact findings, and one whose grounding failed
    naming no fact (a cited fact that does not exist, a crashed gate). Either
    could hide a recurring failure. Mutation: drop the untraced list -- the
    report reads "no grounding failure recurs"."""
    _grounding_only(home, "old", "fail")
    _grounding_only(home, "crashed", "fail", fact_findings=[])
    report = recurring()
    assert report.untraced == ("crashed", "old") and report.unreadable == ()
    assert "grounding failed without naming a fact in: crashed, old" in render(report)


def test_a_record_with_no_grounding_outcome_is_unreadable(home: Path) -> None:
    draft = home / "outputs" / "odd" / "draft"
    draft.mkdir(parents=True)
    (draft / "report.json").write_text("{}")
    assert recurring().unreadable == ("odd",)


def test_nothing_built_yet_is_nothing_recurring(home: Path) -> None:
    assert render(recurring()) == "no grounding failure recurs across jobs"


def _application(job: str, *kinds: str) -> dict[str, object]:
    at = datetime(2026, 10, 1, tzinfo=UTC).isoformat()
    return {
        "job_id": job,
        "company": "Acme",
        "title": "ML Intern",
        "location": "",
        "url": "",
        "source": "lever",
        "submitted_at": at,
        "resume_sha256": "0" * 64,
        "outcomes": [{"kind": k, "occurred_at": at} for k in kinds],
    }


def test_gaps_count_the_rejected_applications_whose_posting_asked(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three rejections: one posting asks for Kubernetes, one was never saved and
    one was saved empty; those two are named rather than counted as asking for
    nothing. The offer's posting
    also asks, and must not count. Mutation: count every application -- 2."""
    apps = [_application("k8s", "rejected"), _application("lost", "rejected")]
    apps.append(_application("blank", "rejected"))
    apps.append(_application("hired", "offer"))
    (home / "applications.yaml").write_text(yaml.safe_dump(apps), encoding="utf-8")
    (home / "inbox" / "k8s.md").write_text("We run Kubernetes and PyTorch.", encoding="utf-8")
    (home / "inbox" / "hired.md").write_text("Kubernetes everywhere.", encoding="utf-8")
    (home / "inbox" / "blank.md").write_text("  \n", encoding="utf-8")
    assert not (home / "inbox" / "lost.md").exists()  # premise: one posting is missing

    texts, missing = rejected_postings(load_applications())
    # An empty saved posting names nothing: listed with the missing, not counted as zero.
    assert missing == ["lost", "blank"] and len(texts) == 1
    assert named_by(["Kubernetes", "SQL"], texts) == {"Kubernetes": 1, "SQL": 0}

    # The tool joins the two; the posting-side count is `gaps`' own, tested in test_triage.
    def found(*_: object) -> list[tuple[str, int]]:
        return [("Kubernetes", 3), ("SQL", 1)]

    monkeypatch.setattr("core.triage.gaps", found)
    called = asyncio.run(server.call_tool("gaps", {}))
    assert cast("dict[str, Any]", called.structured_content) == {  # type: ignore[attr-defined]
        "gaps": [
            {"technology": "Kubernetes", "postings": 3, "rejections": 1},
            {"technology": "SQL", "postings": 1, "rejections": 0},
        ],
        "rejected_without_saved_posting": ["lost", "blank"],
    }


def test_a_bullet_citing_a_missing_fact_is_traced_even_beside_a_traced_failure(
    tmp_path: Path,
) -> None:
    """One bullet cites a fact id that does not exist, another fails normally.
    The first must still name its (missing) fact: otherwise the second makes the
    record look fully traced and the first's failure can recur unseen."""
    fact = Fact(
        id="etl",
        kind=FactKind.EXPERIENCE_DETAIL,
        text="Built the data pipeline",
        provenance=Provenance(source="user", trust=Trust.USER_CONFIRMED),
    )
    doc = ResumeDoc(
        identity=Identity(name="Dana Vex", email="d@example.com"),
        sections=[
            Section(
                title="Experience",
                entries=[
                    Entry(
                        heading="Northwind",
                        bullets=[
                            Bullet(text="Built the ETL pipeline", sources=["etl"]),
                            Bullet(text="Shipped the loader", sources=["loader", "ghost"]),
                        ],
                    )
                ],
            )
        ],
    )
    grounding = run_gates(doc, {"etl": fact}, tmp_path / "absent.pdf").outcomes[0]
    assert {(f.kind, f.facts) for f in grounding.fact_findings} == {
        ("unknown_entity", ("etl",)),
        ("unknown_fact", ("ghost", "loader")),
    }
