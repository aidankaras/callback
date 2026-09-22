from __future__ import annotations

import hashlib
import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.apply import ApplyError, Target, apply, load_applications, record_outcome
from core.paths import ensure_home

NOW = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    return ensure_home()


def _built(home: Path, job: str, *, verdict: str = "pass", letter: str | None = None) -> None:
    """A draft as `callback build` (and optionally `callback letter`) leaves it."""
    draft = home / "outputs" / job / "draft"
    draft.mkdir(parents=True)
    pdf = b"%PDF-1.7 resume for " + job.encode()
    (draft / "resume.pdf").write_bytes(pdf)
    (draft / "resume.yaml").write_text(
        "identity: {name: Dana Vex, email: dana@example.com, links: [github.com/dv]}\n"
        "sections:\n- title: Skills\n  skills: [Python]\n",
        encoding="utf-8",
    )
    record = {"verdict": verdict, "pdf_sha256": hashlib.sha256(pdf).hexdigest()}
    (draft / "report.json").write_text(json.dumps(record), encoding="utf-8")
    if letter is not None:
        letter_pdf = b"%PDF-1.7 letter"
        (draft / "cover-letter.pdf").write_bytes(letter_pdf)
        letter_record = {
            "verdict": letter.lower(),
            "pdf_sha256": hashlib.sha256(letter_pdf).hexdigest(),
        }
        (draft / "letter-report.json").write_text(json.dumps(letter_record), encoding="utf-8")


def _target(job: str = "acme-ml", **kw: str) -> Target:
    body = {
        "job_id": job,
        "company": "Acme Robotics",
        "title": "Machine Learning Intern",
        "location": "Boston, MA",
        "url": "https://boards.greenhouse.io/acme/jobs/1",
        "source": "greenhouse",
    }
    body.update(kw)
    return Target.model_validate(body)


def test_a_passing_draft_becomes_a_packet_and_a_record(home: Path) -> None:
    _built(home, "acme-ml", letter="PASS")
    packet = apply(_target(), now=NOW)

    assert sorted(p.name for p in packet.iterdir()) == [
        "cover-letter.pdf",
        "fields.json",
        "resume.pdf",
        "resume.txt",
    ]
    for p in packet.iterdir():
        assert stat.S_IMODE(p.stat().st_mode) == 0o600, p.name
    fields = json.loads((packet / "fields.json").read_text())
    assert fields["name"] == "Dana Vex" and fields["links"] == ["github.com/dv"]

    (record,) = load_applications()
    assert record.job_id == "acme-ml" and record.source == "greenhouse"
    pdf = (home / "outputs" / "acme-ml" / "draft" / "resume.pdf").read_bytes()
    assert record.resume_sha256 == hashlib.sha256(pdf).hexdigest()
    assert record.letter_sha256 is not None
    assert record.submitted_at == NOW


def test_a_draft_that_failed_its_gates_is_refused(home: Path) -> None:
    _built(home, "acme-ml", verdict="fail")
    with pytest.raises(ApplyError, match="did not pass"):
        apply(_target(), now=NOW)
    assert load_applications() == []


def test_a_warning_needs_an_explicit_acceptance(home: Path) -> None:
    _built(home, "acme-ml", verdict="warn")
    with pytest.raises(ApplyError, match="warn"):
        apply(_target(), now=NOW)
    apply(_target(), now=NOW, accept_warnings=True)
    assert len(load_applications()) == 1


def test_a_failed_cover_letter_is_refused(home: Path) -> None:
    _built(home, "acme-ml", letter="FAIL")
    with pytest.raises(ApplyError, match="cover letter"):
        apply(_target(), now=NOW)


def test_a_pdf_changed_since_it_was_gated_is_refused(home: Path) -> None:
    """The report names the bytes it judged. Mutation: skip the hash check."""
    _built(home, "acme-ml")
    (home / "outputs" / "acme-ml" / "draft" / "resume.pdf").write_bytes(b"%PDF-1.7 edited")
    with pytest.raises(ApplyError, match="changed since"):
        apply(_target(), now=NOW)


def test_the_same_job_is_never_applied_to_twice(home: Path) -> None:
    _built(home, "acme-ml")
    apply(_target(), now=NOW)
    with pytest.raises(ApplyError, match="already applied"):
        apply(_target(), now=NOW + timedelta(days=200))


def test_the_same_posting_under_another_job_id_is_refused_within_90_days(home: Path) -> None:
    _built(home, "acme-ml")
    _built(home, "acme-ml-2")
    apply(_target(), now=NOW)
    with pytest.raises(ApplyError, match="90 days"):
        apply(_target("acme-ml-2", title="machine learning intern"), now=NOW + timedelta(days=89))
    apply(_target("acme-ml-2"), now=NOW + timedelta(days=91))


def test_more_than_ten_applications_in_a_day_is_refused(home: Path) -> None:
    for n in range(11):
        _built(home, f"job-{n}")
    for n in range(10):
        apply(_target(f"job-{n}", company=f"Company {n}"), now=NOW + timedelta(minutes=n))
    with pytest.raises(ApplyError, match="10 applications"):
        apply(_target("job-10", company="Company 10"), now=NOW + timedelta(hours=2))
    # A day later the window has moved.
    apply(_target("job-10", company="Company 10"), now=NOW + timedelta(days=1, minutes=1))


def test_an_outcome_is_recorded_against_its_application(home: Path) -> None:
    _built(home, "acme-ml")
    apply(_target(), now=NOW)
    record_outcome("acme-ml", "interview", now=NOW + timedelta(days=7))
    (record,) = load_applications()
    assert [(o.kind, o.occurred_at) for o in record.outcomes] == [
        ("interview", NOW + timedelta(days=7))
    ]
    with pytest.raises(ApplyError, match="no application"):
        record_outcome("never-applied", "offer", now=NOW)


def test_a_source_must_be_one_of_the_known_channels() -> None:
    with pytest.raises(ValueError, match="source"):
        _target(source="scraper")


def test_the_cli_builds_a_packet_and_says_it_does_not_submit(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from core.cli import main

    _built(home, "acme-ml")
    args = ["apply", "acme-ml", "--company", "Acme Robotics", "--title", "ML Intern"]
    assert main([*args, "--source", "greenhouse", "--url", "https://x.test/1"]) == 0
    out = capsys.readouterr().out
    assert "does not submit" in out and "https://x.test/1" in out
    assert main([*args, "--source", "greenhouse"]) == 3
    assert "already applied" in capsys.readouterr().err
    assert main(["outcome", "acme-ml", "interview"]) == 0
    assert main(["applications"]) == 0
    assert "interview" in capsys.readouterr().out


def test_a_cover_letter_changed_since_it_was_checked_is_refused(home: Path) -> None:
    """Mutation: skip the letter's hash check -- this test fails."""
    _built(home, "acme-ml", letter="PASS")
    (home / "outputs" / "acme-ml" / "draft" / "cover-letter.pdf").write_bytes(b"%PDF edited")
    with pytest.raises(ApplyError, match=r"cover-letter\.pdf has changed"):
        apply(_target(), now=NOW)


@pytest.mark.parametrize("body", ["", "not json", "[]"])
def test_an_unreadable_gate_record_is_refused(home: Path, body: str) -> None:
    _built(home, "acme-ml")
    (home / "outputs" / "acme-ml" / "draft" / "report.json").write_text(body, encoding="utf-8")
    with pytest.raises(ApplyError, match="gate record"):
        apply(_target(), now=NOW)
