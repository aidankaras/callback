"""What an application records about what was sent, so outcomes can be attributed."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from core import notes
from core.apply import ApplyError, Target, apply, load_applications, record_outcome
from core.paths import ensure_home
from core.templates import use

NOW = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
DOC = (
    "identity: {name: Dana Vex, email: dana@example.com, links: [github.com/dv]}\n"
    "sections:\n"
    "- title: Experience\n"
    "  entries:\n"
    "  - heading: Northwind Analytics\n"
    "    sources: [nw]\n"
    "    bullets:\n"
    "    - {text: Built the ETL pipeline, sources: [etl]}\n"
    "    - {text: Cut report latency, sources: [latency, etl]}\n"
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    return ensure_home()


def _gate(draft: Path, pdf: bytes) -> None:
    """Write a passing report for the draft's current bytes, as `callback build` would."""
    (draft / "resume.pdf").write_bytes(pdf)
    record = {
        "verdict": "pass",
        "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        "doc_sha256": hashlib.sha256((draft / "resume.yaml").read_bytes()).hexdigest(),
    }
    (draft / "report.json").write_text(json.dumps(record), encoding="utf-8")


def _built(home: Path, job: str, doc: str = DOC) -> Path:
    draft = home / "outputs" / job / "draft"
    draft.mkdir(parents=True, exist_ok=True)
    (draft / "resume.yaml").write_text(doc, encoding="utf-8")
    _gate(draft, b"%PDF-1.7 " + job.encode())
    return draft


def _target(job: str = "acme-ml") -> Target:
    return Target.model_validate(
        {"job_id": job, "company": "Acme", "title": "ML Intern", "source": "greenhouse"}
    )


def test_a_send_records_the_facts_the_resume_cites(home: Path) -> None:
    _built(home, "acme-ml")
    apply(_target(), now=NOW)
    (application,) = load_applications()
    (sent,) = application.sent
    assert sent.cited_fact_ids == ("etl", "latency", "nw")
    assert sent.based_on is None and sent.role_family is None
    assert sent.resume_sha256 == application.resume_sha256
    assert sent.at == NOW


def test_template_use_stamps_the_draft_and_apply_records_it(home: Path) -> None:
    _built(home, "first")
    templates = home / "templates"
    templates.mkdir()
    (templates / "ml-base.yaml").write_text(DOC, encoding="utf-8")
    draft = use("ml-base", "acme-ml").parent
    _gate(draft, b"%PDF-1.7 from template")
    apply(_target(), now=NOW)
    (application,) = load_applications()
    assert application.sent[0].based_on == "ml-base"


def test_a_role_family_must_be_one_the_candidate_keeps(home: Path) -> None:
    _built(home, "acme-ml")
    with pytest.raises(ApplyError, match="not one of your role families"):
        apply(_target(), now=NOW, role_family="quant")
    assert load_applications() == []
    notes.set_role_families(["quant"])
    apply(_target(), now=NOW, role_family="quant")
    assert load_applications()[0].sent[0].role_family == "quant"


def test_a_refresh_appends_a_send_and_leaves_earlier_attribution_alone(home: Path) -> None:
    """Mutation: let refresh replace `sent` instead of appending -- this fails."""
    notes.set_role_families(["ml-infra"])
    draft = _built(home, "acme-ml")
    apply(_target(), now=NOW, role_family="ml-infra")
    record_outcome("acme-ml", "viewed", now=NOW + timedelta(days=1))

    (draft / "resume.yaml").write_text(DOC.replace("[latency, etl]", "[latency]"), encoding="utf-8")
    _gate(draft, b"%PDF-1.7 improved")
    apply(_target(), now=NOW + timedelta(days=2), refresh=True)
    record_outcome("acme-ml", "interview", now=NOW + timedelta(days=9))

    (application,) = load_applications()
    first, second = application.sent
    assert first.resume_sha256 != second.resume_sha256
    assert first.cited_fact_ids == ("etl", "latency", "nw")  # untouched by the refresh
    assert second.role_family == "ml-infra"  # carried over when not given
    assert [(o.kind, o.sent_index) for o in application.outcomes] == [
        ("viewed", 0),
        ("interview", 1),
    ]
    assert application.resume_sha256 == second.resume_sha256  # interview reads this


def test_an_application_recorded_before_sends_were_kept_still_loads(home: Path) -> None:
    old = {
        "job_id": "old-job",
        "company": "Acme",
        "title": "ML Intern",
        "location": "",
        "url": "",
        "source": "greenhouse",
        "submitted_at": NOW.isoformat(),
        "resume_sha256": "a" * 64,
        "outcomes": [{"kind": "rejected", "occurred_at": NOW.isoformat()}],
    }
    (home / "applications.yaml").write_text(yaml.safe_dump([old]), encoding="utf-8")
    (application,) = load_applications()
    assert application.sent == [] and application.outcomes[0].sent_index is None
    record_outcome("old-job", "withdrew", now=NOW)
    assert load_applications()[0].outcomes[-1].sent_index is None


def test_an_outcome_cannot_name_a_send_that_does_not_exist(home: Path) -> None:
    _built(home, "acme-ml")
    apply(_target(), now=NOW)
    path = home / "applications.yaml"
    body = yaml.safe_load(path.read_text())
    body[0]["outcomes"] = [{"kind": "offer", "occurred_at": NOW.isoformat(), "sent_index": 3}]
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    with pytest.raises(ApplyError, match="not recorded"):
        load_applications()


def test_the_cli_passes_the_role_family_through(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from core.cli import main

    _built(home, "acme-ml")
    base = ["apply", "acme-ml", "--company", "Acme", "--title", "ML Intern", "--source", "lever"]
    assert main([*base, "--role-family", "quant"]) == 3
    assert "not one of your role families" in capsys.readouterr().err
    notes.set_role_families(["quant"])
    assert main([*base, "--role-family", "quant"]) == 0
    assert load_applications()[0].sent[0].role_family == "quant"
