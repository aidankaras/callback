from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.agenda import SHORTLIST, agenda, render
from core.apply import Application, Outcome
from core.discovery.store import Found
from core.paths import ensure_home
from core.profile.models import Fact, FactKind, Provenance, Trust
from core.triage import Profile

NOW = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
PROFILE = Profile(work_authorization="US citizen")
FACTS = {
    "py": Fact(
        id="py",
        kind=FactKind.SKILL,
        text="Wrote Python for the loader",
        entities=["Python"],
        provenance=Provenance(source="x", trust=Trust.USER_CONFIRMED),
    )
}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    return ensure_home()


DOC = "identity: {name: Dana Vex, email: d@example.com}\nsections: []\n"


def _built(home: Path, job: str, verdict: str = "pass", doc: str = DOC) -> Path:
    """A draft as `callback build` leaves it: the document, its PDF, and a
    report naming the verdict and the bytes that were judged."""
    draft = home / "outputs" / job / "draft"
    draft.mkdir(parents=True)
    (draft / "resume.yaml").write_text(doc, encoding="utf-8")
    (draft / "resume.pdf").write_bytes(b"%PDF")
    report = {"verdict": verdict, "doc_sha256": hashlib.sha256(doc.encode()).hexdigest()}
    (draft / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return draft


def _posting(pid: str, company: str, days_old: int = 1, text: str = "We use Python.") -> Found:
    return Found(
        id=pid,
        ats="greenhouse",
        board=company.lower(),
        company=company,
        title="ML Intern",
        url=f"https://boards.greenhouse.io/{company.lower()}/jobs/1",
        text=text,
        first_seen=NOW - timedelta(days=days_old),
    )


def _application(job: str, company: str, days_ago: float, *outcomes: str) -> Application:
    sent = NOW - timedelta(days=days_ago)
    return Application.model_validate(
        {
            "job_id": job,
            "company": company,
            "title": "ML Intern",
            "source": "greenhouse",
            "submitted_at": sent,
            "resume_sha256": "0" * 64,
            "outcomes": [Outcome(kind=k, occurred_at=sent + timedelta(days=1)) for k in outcomes],  # type: ignore[arg-type]
        }
    )


def test_a_booked_interview_comes_first(home: Path) -> None:
    """Mutation: drop the interview item -- the agenda leads with a posting."""
    result = agenda(
        [_posting("g:acme:1", "Acme")],
        [_application("globex-ml", "Globex", 9, "interview")],
        PROFILE,
        FACTS,
        now=NOW,
    )
    assert result.items[0].kind == "interview"
    assert result.items[0].command == "callback interview globex-ml"


def test_an_interview_already_prepared_is_not_suggested_again(home: Path) -> None:
    prep = home / "outputs" / "globex-ml" / "interview"
    prep.mkdir(parents=True)
    (prep / "prep.md").write_text("pack", encoding="utf-8")
    result = agenda(
        [], [_application("globex-ml", "Globex", 9, "interview")], PROFILE, FACTS, now=NOW
    )
    assert [i.kind for i in result.items] == []


def test_a_built_draft_that_was_never_sent_is_named(home: Path) -> None:
    """Work already done is worth more than a new posting. Mutation: ignore
    drafts -- this fails."""
    _built(home, "acme-ml")
    result = agenda([], [], PROFILE, FACTS, now=NOW)
    assert [i.kind for i in result.items] == ["unsent"]
    assert "acme-ml" in result.items[0].what


def test_a_draft_that_failed_its_gates_is_not_work_ready_to_send(home: Path) -> None:
    """`build` writes a PDF whatever the gates said. Mutation: look only for
    resume.pdf -- a failed draft is offered as work already done."""
    _built(home, "bad-ml", verdict="fail")
    assert agenda([], [], PROFILE, FACTS, now=NOW).items == ()


def test_an_application_the_candidate_closed_is_not_waiting_on_them(home: Path) -> None:
    """Mutation: treat only a reply as closing it -- a withdrawn application is
    reported as silent, and the agenda suggests marking it ghosted."""
    for kind in ("withdrew", "ghosted"):
        closed = agenda([], [_application("w", "Acme", 30, kind)], PROFILE, FACTS, now=NOW)
        assert closed.items == (), kind


def test_postings_are_shortlisted_skipping_companies_already_applied_to(home: Path) -> None:
    postings = [_posting(f"g:c{n}:1", f"Company{n}", days_old=n) for n in range(SHORTLIST + 3)]
    postings.append(_posting("g:globex:1", "Globex"))
    result = agenda(postings, [_application("globex-ml", "Globex", 2)], PROFILE, FACTS, now=NOW)
    suggested = [i for i in result.items if i.kind == "apply"]
    assert len(suggested) == SHORTLIST
    assert not any("Globex" in i.what for i in suggested)


def test_a_knockout_posting_is_never_suggested(home: Path) -> None:
    """Mutation: keep knockouts -- the agenda suggests a role the candidate
    cannot hold."""
    posting = _posting("g:acme:1", "Acme", text="US citizenship is required. We use Python.")
    profile = Profile(work_authorization="F-1 student")
    assert agenda([posting], [], profile, FACTS, now=NOW).items == ()


def test_an_application_gone_quiet_is_offered_as_ghosted(home: Path) -> None:
    result = agenda([], [_application("acme-ml", "Acme", 30)], PROFILE, FACTS, now=NOW)
    (item,) = result.items
    assert item.kind == "silent" and item.command == "callback outcome acme-ml ghosted"
    # One sent three days ago is not yet quiet, so it is not listed.
    recent = agenda([], [_application("new-ml", "New", 3)], PROFILE, FACTS, now=NOW)
    assert recent.items == ()
    # One that replied is not listed, however old.
    replied = agenda([], [_application("acme-ml", "Acme", 30, "rejected")], PROFILE, FACTS, now=NOW)
    assert replied.items == ()


def test_the_daily_headroom_counts_only_the_last_day(home: Path) -> None:
    applications = [_application(f"j{n}", f"C{n}", 0.5) for n in range(3)]
    applications.append(_application("old", "Old", 5))
    result = agenda([], applications, PROFILE, FACTS, now=NOW)
    assert (result.applied_today, result.headroom) == (3, 7)
    assert "7 of 10 applications left" in render(result)


def test_an_empty_agenda_says_what_to_run(home: Path) -> None:
    assert "callback watch" in render(agenda([], [], PROFILE, FACTS, now=NOW))


@pytest.mark.parametrize("damage", ["not-json", "a list", "no-hash", "edited", "unreadable"])
def test_a_draft_whose_gates_cannot_be_read_is_reported_not_forgotten(
    home: Path, damage: str
) -> None:
    """This command's job is not to forget work already done, so a report that
    exists and cannot be trusted is an item, never a silent skip. Mutation:
    `continue` on any unreadable report -- the agenda says "nothing waiting"."""
    draft = _built(home, "acme-ml")
    report = draft / "report.json"
    if damage == "not-json":
        report.write_text("{oops", encoding="utf-8")
    elif damage == "a list":
        report.write_text("[]", encoding="utf-8")
    elif damage == "no-hash":
        report.write_text(json.dumps({"verdict": "pass"}), encoding="utf-8")
    elif damage == "edited":
        (draft / "resume.yaml").write_text(DOC + "# edited\n", encoding="utf-8")
    else:
        report.chmod(0o000)
    try:
        items = agenda([], [], PROFILE, FACTS, now=NOW).items
        assert [i.kind for i in items] == ["rebuild"], damage
        assert "callback build" in items[0].command
    finally:
        report.chmod(0o600)


def test_a_job_that_was_never_built_is_not_an_item(home: Path) -> None:
    """A missing report is a job never built, not a damaged one."""
    (home / "outputs" / "acme-ml" / "draft").mkdir(parents=True)
    assert agenda([], [], PROFILE, FACTS, now=NOW).items == ()


# --- deadlines ------------------------------------------------------------------------


def _programme(pid: str, company: str, deadline: datetime | None) -> Found:
    return _posting(pid, company).model_copy(
        update={
            "ats": "manual",
            "title": "Summer Research",
            "url": f"https://www.example.org/{pid}",
            "deadline": deadline,
        }
    )


def _deadline_lines(
    postings: list[Found], applications: list[Application] | None = None
) -> list[str]:
    result = agenda(postings, applications or [], PROFILE, FACTS, now=NOW)
    return [item.what for item in result.items if item.kind == "deadline"]


def test_deadlines_in_the_next_thirty_days_are_listed_soonest_first(home: Path) -> None:
    """Days are calendar days in the deadline's own zone: 08:00 on 12 October
    in Berlin is eleven days from 1 October there, though under eleven days of
    elapsed time from 15:00 UTC. Mutation: count elapsed days -- this reads 10."""
    from zoneinfo import ZoneInfo

    berlin = ZoneInfo("Europe/Berlin")
    postings = [
        _programme("late", "Late Lab", datetime(2026, 10, 25, 23, 59, tzinfo=berlin)),
        _programme("soon", "Soon Lab", datetime(2026, 10, 12, 8, 0, tzinfo=berlin)),
        _programme("far", "Far Lab", datetime(2026, 11, 15, 23, 59, tzinfo=berlin)),
        _programme("none", "No Date Lab", None),
    ]
    assert _deadline_lines(postings) == [
        "Soon Lab Summer Research: closes in 11 days (2026-10-12)",
        "Late Lab Summer Research: closes in 24 days (2026-10-25)",
    ]


def test_a_passed_deadline_shows_as_closed_for_a_week_then_drops(home: Path) -> None:
    """Mutation: drop the `CLOSED_DAYS` bound -- the nine-day-old one is listed."""
    postings = [
        _programme("recent", "Recent Lab", NOW - timedelta(days=3)),
        _programme("old", "Old Lab", NOW - timedelta(days=9)),
    ]
    assert _deadline_lines(postings) == ["Recent Lab Summer Research: closed (2026-09-28)"]


def test_a_programme_already_applied_to_has_no_deadline_line(home: Path) -> None:
    applied = _application("lab-app", "Soon Lab", days_ago=1).model_copy(
        update={"title": "Summer Research"}
    )
    postings = [_programme("soon", "Soon Lab", NOW + timedelta(days=5))]
    assert _deadline_lines(postings) != []  # premise: listed before the application
    assert _deadline_lines(postings, [applied]) == []


def test_a_closed_posting_is_not_suggested_for_applying(home: Path) -> None:
    postings = [_programme("shut", "Shut Lab", NOW - timedelta(days=2))]
    result = agenda(postings, [], PROFILE, FACTS, now=NOW)
    assert [i.kind for i in result.items] == ["deadline"]
