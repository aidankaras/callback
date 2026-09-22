from __future__ import annotations

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
    draft = home / "outputs" / "acme-ml" / "draft"
    draft.mkdir(parents=True)
    (draft / "resume.pdf").write_bytes(b"%PDF")
    result = agenda([], [], PROFILE, FACTS, now=NOW)
    assert [i.kind for i in result.items] == ["unsent"]
    assert "acme-ml" in result.items[0].what


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
