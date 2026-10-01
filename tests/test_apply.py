from __future__ import annotations

import hashlib
import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.apply import (
    ApplyError,
    Target,
    apply,
    load_applications,
    record_outcome,
    void_application,
)
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
    doc = (draft / "resume.yaml").read_bytes()
    record = {
        "verdict": verdict,
        "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        "doc_sha256": hashlib.sha256(doc).hexdigest(),
    }
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
        apply(
            _target(f"job-{n}", company=f"Company {n}", url=f"https://x.test/{n}"),
            now=NOW + timedelta(minutes=n),
        )
    with pytest.raises(ApplyError, match="10 applications"):
        apply(
            _target("job-10", company="Company 10", url="https://x.test/10"),
            now=NOW + timedelta(hours=2),
        )
    # A day later the window has moved.
    apply(
        _target("job-10", company="Company 10", url="https://x.test/10"),
        now=NOW + timedelta(days=1, minutes=1),
    )


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


def test_an_application_never_submitted_can_be_voided(home: Path) -> None:
    """Voided, it leaves the record and stops counting toward the volume rules,
    and the ledger says so. Mutation: skip the save -- the record stays."""
    _built(home, "acme-ml")
    apply(_target(), now=NOW)
    assert [a.job_id for a in load_applications()] == ["acme-ml"]

    void_application("acme-ml", now=NOW + timedelta(hours=1))

    assert load_applications() == []
    ledger = (home / "ledger.log").read_text().splitlines()
    assert [json.loads(line)["event"] for line in ledger][-2:] == ["apply", "void"]
    apply(_target(), now=NOW + timedelta(hours=2))  # the same job is no longer a duplicate


def test_an_application_marked_withdrew_can_still_be_voided(home: Path) -> None:
    """`withdrew` was the only way to mark an unsent application before `void`.
    Mutation: refuse on any outcome -- this fails."""
    _built(home, "acme-ml")
    apply(_target(), now=NOW)
    record_outcome("acme-ml", "withdrew", now=NOW + timedelta(hours=1))
    void_application("acme-ml", now=NOW + timedelta(hours=2))
    assert load_applications() == []


def test_an_application_with_an_outcome_cannot_be_voided(home: Path) -> None:
    """Mutation: drop the outcome check -- a replied-to application vanishes."""
    _built(home, "acme-ml")
    apply(_target(), now=NOW)
    record_outcome("acme-ml", "viewed", now=NOW + timedelta(days=1))
    with pytest.raises(ApplyError, match="was submitted"):
        void_application("acme-ml", now=NOW + timedelta(days=2))
    assert [a.job_id for a in load_applications()] == ["acme-ml"]
    with pytest.raises(ApplyError, match="no application"):
        void_application("never-applied", now=NOW)
    from core.cli import main

    assert main(["void", "acme-ml"]) == 3


def test_a_void_whose_save_fails_says_so(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: let the OSError escape -- the CLI exits with a traceback."""
    import core.apply

    _built(home, "acme-ml")
    apply(_target(), now=NOW)

    def full(_applications: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(core.apply, "_save", full)
    with pytest.raises(ApplyError, match="ledger records the void"):
        void_application("acme-ml", now=NOW + timedelta(hours=1))
    with pytest.raises(ValueError, match="timezone"):
        void_application("acme-ml", now=datetime(2026, 10, 1, 18, 0))  # noqa: DTZ001


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


def test_a_resume_document_edited_since_it_was_gated_is_refused(home: Path) -> None:
    """The packet's plain text and form fields come from resume.yaml, so it
    must be the document that was gated. Mutation: skip the check -- fails."""
    _built(home, "acme-ml")
    doc = home / "outputs" / "acme-ml" / "draft" / "resume.yaml"
    doc.write_text(doc.read_text().replace("dana@example.com", "other@evil.test"))
    with pytest.raises(ApplyError, match=r"resume\.yaml has changed"):
        apply(_target(), now=NOW)


@pytest.mark.parametrize(
    "variant",
    [
        # Each carries its own URL, so only the name rule under test can match it.
        {"company": "Acme Robotics, Inc.", "url": "https://x.test/a"},
        {"company": "Acme Robotics.", "url": "https://x.test/b"},
        {"location": "", "url": "https://x.test/c"},
        {"title": "Machine Learning Intern (Summer 2027)", "url": "https://x.test/d"},
        {"company": "Different Name", "url": "https://boards.greenhouse.io/acme/jobs/1"},
    ],
)
def test_a_near_duplicate_posting_is_refused_within_90_days(
    home: Path, variant: dict[str, str]
) -> None:
    _built(home, "acme-ml")
    _built(home, "acme-ml-2")
    apply(_target(), now=NOW)
    with pytest.raises(ApplyError, match="90 days"):
        apply(_target("acme-ml-2", **variant), now=NOW + timedelta(days=1))


def test_a_failed_record_save_leaves_no_packet_behind(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A packet with no record is one the volume rules never hear about."""
    import core.apply

    _built(home, "acme-ml")

    def full(_applications: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(core.apply, "_save", full)
    with pytest.raises(OSError, match="disk full"):
        apply(_target(), now=NOW)
    job = home / "outputs" / "acme-ml"
    assert sorted(p.name for p in job.iterdir()) == ["draft"]


def test_a_stale_packet_is_replaced_whole(home: Path) -> None:
    _built(home, "acme-ml")
    stale = home / "outputs" / "acme-ml" / "packet" / "old" / "leftover.pdf"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    packet = apply(_target(), now=NOW)
    assert "old" not in {p.name for p in packet.iterdir()}


@pytest.mark.parametrize("body", ["{}", "~", "0", "false", "''", "{a: 1}"])
def test_an_application_record_that_is_not_a_list_is_refused(home: Path, body: str) -> None:
    (home / "applications.yaml").write_text(body, encoding="utf-8")
    with pytest.raises(ApplyError, match="not a readable application record"):
        load_applications()


@pytest.mark.parametrize(
    "record",
    [{"verdict": "pass"}, {"verdict": ["pass"], "pdf_sha256": "x", "doc_sha256": "y"}],
)
def test_a_gate_record_missing_or_mistyping_a_field_is_named(
    home: Path, record: dict[str, object]
) -> None:
    _built(home, "acme-ml")
    (home / "outputs" / "acme-ml" / "draft" / "report.json").write_text(json.dumps(record))
    with pytest.raises(ApplyError, match="gate record"):
        apply(_target(), now=NOW)


def test_refreshing_a_packet_rebuilds_it_without_counting_a_new_application(home: Path) -> None:
    """Mutation: let refresh append a second record -- this test fails."""
    _built(home, "acme-ml")
    apply(_target(), now=NOW)
    draft = home / "outputs" / "acme-ml" / "draft"
    improved = b"%PDF-1.7 improved resume"
    (draft / "resume.pdf").write_bytes(improved)
    record = json.loads((draft / "report.json").read_text())
    record["pdf_sha256"] = hashlib.sha256(improved).hexdigest()
    (draft / "report.json").write_text(json.dumps(record))

    packet = apply(_target(), now=NOW + timedelta(hours=1), refresh=True)
    assert (packet / "resume.pdf").read_bytes() == improved
    (only,) = load_applications()
    assert only.submitted_at == NOW
    assert only.resume_sha256 == hashlib.sha256(improved).hexdigest()


def test_refresh_still_requires_a_gated_draft_and_a_prior_record(home: Path) -> None:
    _built(home, "acme-ml")
    with pytest.raises(ApplyError, match="to refresh"):
        apply(_target(), now=NOW, refresh=True)
    apply(_target(), now=NOW)
    (home / "outputs" / "acme-ml" / "draft" / "resume.pdf").write_bytes(b"%PDF tampered")
    with pytest.raises(ApplyError, match="changed since"):
        apply(_target(), now=NOW, refresh=True)


@pytest.mark.parametrize("state", ["pass", "fail", "edited"])
def test_form_answers_join_the_packet_only_when_checked_and_unchanged(
    home: Path, state: str
) -> None:
    """Mutation: skip the answers' hash check -- the edited case is packed."""
    _built(home, "acme-ml")
    draft = home / "outputs" / "acme-ml" / "draft"
    text = "# answers\n\nWhy us? Because.\n"
    (draft / "answers.md").write_text(text, encoding="utf-8")
    record = {
        "verdict": "fail" if state == "fail" else "pass",
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    (draft / "answers-report.json").write_text(json.dumps(record), encoding="utf-8")
    if state == "edited":
        (draft / "answers.md").write_text(text + "Also I founded a company.\n", encoding="utf-8")
    if state == "pass":
        packet = apply(_target(), now=NOW)
        assert (packet / "answers.md").read_text() == text
    else:
        with pytest.raises(ApplyError, match="answers"):
            apply(_target(), now=NOW)


def test_an_application_the_ledger_cannot_record_is_not_saved(home: Path) -> None:
    """The ledger is written before the record, so a saved application always
    has its entry. Mutation: save first -- the record survives the failure."""
    _built(home, "acme-ml")
    (home / "ledger.log").write_text("not json\n", encoding="utf-8")
    with pytest.raises(ApplyError, match="ledger"):
        apply(_target(), now=NOW)
    assert load_applications() == []
    assert not (home / "outputs" / "acme-ml" / "packet").exists()


def _judged_against(home: Path, record: Path, store: bytes | None) -> None:
    """Rewrite `record` as one judged against a fact store holding `store`."""
    body = json.loads(record.read_text(encoding="utf-8"))
    body["facts_sha256"] = None if store is None else hashlib.sha256(store).hexdigest()
    record.write_text(json.dumps(body), encoding="utf-8")


@pytest.mark.parametrize("which", ["resume", "cover letter", "form answers"])
def test_a_draft_judged_against_facts_that_have_since_changed_is_refused(
    home: Path, which: str
) -> None:
    """A fact retired after the build backs nothing, and the report that
    passed the document was written while it still did."""
    _built(home, "acme-ml", letter="PASS")
    draft = home / "outputs" / "acme-ml" / "draft"
    text = "# answers\n\nWhy us? Because.\n"
    (draft / "answers.md").write_text(text, encoding="utf-8")
    answers = {"verdict": "pass", "sha256": hashlib.sha256(text.encode()).hexdigest()}
    (draft / "answers-report.json").write_text(json.dumps(answers), encoding="utf-8")
    records = {
        "resume": draft / "report.json",
        "cover letter": draft / "letter-report.json",
        "form answers": draft / "answers-report.json",
    }
    then, now = b"queue: {text: in use}\n", b"queue: {text: in use, retired_on: 2026-09-29}\n"
    for record in records.values():
        _judged_against(home, record, then)
    (home / "facts.yaml").write_bytes(then)
    packet = apply(_target(), now=NOW)
    assert (packet / "answers.md").is_file(), "premise: unchanged facts are accepted"

    (home / "facts.yaml").write_bytes(now)
    for name, record in records.items():
        _judged_against(home, record, then if name == which else now)

    with pytest.raises(ApplyError, match=f"the facts have changed since the {which} was"):
        apply(_target(), now=NOW, refresh=True)


def test_a_record_that_names_no_facts_is_refused_once_there_are_facts(home: Path) -> None:
    _built(home, "acme-ml")
    (home / "facts.yaml").write_bytes(b"queue: {text: in use}\n")

    with pytest.raises(ApplyError, match="the facts have changed since the resume was"):
        apply(_target(), now=NOW)
