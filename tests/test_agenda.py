from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.agenda import SHORTLIST, agenda, render
from core.apply import Application, Outcome, Target
from core.discovery.store import Found, parse_deadline
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


def _use_zone(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv("TZ", name)
    time.tzset()


@pytest.fixture(autouse=True)
def _berlin(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Deadline days are counted in this machine's timezone; pin one, so that a
    run in UTC and one in another zone read the same dates."""
    _use_zone(monkeypatch, "Europe/Berlin")
    yield
    monkeypatch.undo()
    time.tzset()


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
    """Days are calendar days in this machine's zone, Berlin here: 08:00 on 12
    October is eleven days from 1 October there, though under eleven days of
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
    """Nine days closed, no longer listed as a deadline, so only the shortlist's
    own check keeps it out. Mutation: drop that check -- it is suggested."""
    shut = _programme("shut", "Shut Lab", NOW - timedelta(days=9))
    assert _apply_rows([shut.model_copy(update={"deadline": None})], []) != []  # premise
    assert _deadline_lines([shut]) == []  # premise: past the closed week
    assert agenda([shut], [], PROFILE, FACTS, now=NOW).items == ()


def test_a_posting_listed_by_its_deadline_is_not_also_shortlisted(home: Path) -> None:
    """It is listed once, and the shortlist still offers `SHORTLIST` others.
    Mutation: drop the shortlist's skip of a listed deadline -- it appears
    twice and pushes one of the others out."""
    others = [_posting(f"p{n}", f"Company{n}") for n in range(SHORTLIST)]
    dated = _programme("dated", "Dated Lab", NOW + timedelta(days=26))
    undated = dated.model_copy(update={"deadline": None})
    take = "callback postings take dated --job <job-id>"
    assert take in _apply_rows([undated, *others], [])  # premise: it ranks in the shortlist
    result = agenda([dated, *others], [], PROFILE, FACTS, now=NOW)
    assert [i.command for i in result.items if i.command == take] == [take]
    assert [i.kind for i in result.items] == ["deadline"] + ["apply"] * SHORTLIST


def _season_pair(
    applied_title: str, posting_title: str, applied_url: str = "", url: str | None = None
) -> tuple[list[Found], list[Application]]:
    posting = _programme("p", "Lab", NOW + timedelta(days=5)).model_copy(
        update={"title": posting_title, **({"url": url} if url else {})}
    )
    applied = _application("lab-app", "Lab", days_ago=1).model_copy(
        update={"title": applied_title, "url": applied_url}
    )
    target = Target(job_id="x", company="Lab", title=posting.title, url=posting.url, source="other")
    assert applied.same_posting(target)  # premise: `apply` would call these one posting
    return [posting], [applied]


def test_another_term_of_a_programme_applied_to_keeps_its_deadline(home: Path) -> None:
    """Mutation: make `_is_applied` return `same_posting` alone -- no line."""
    postings, applied = _season_pair(
        "Summer Research (Summer 2027)", "Summer Research (Winter 2027)"
    )
    assert len(_deadline_lines(postings, applied)) == 1


def test_the_same_term_or_a_missing_term_still_hides_the_deadline(home: Path) -> None:
    for a, b in [
        ("Summer Research (Summer 2027)", "Summer Research ( summer  2027 )"),
        ("Summer Research (Summer 2027)", "Summer Research"),
        ("Summer Research", "Summer Research (Winter 2027)"),
    ]:
        postings, applied = _season_pair(a, b)
        assert _deadline_lines(postings, applied) == [], (a, b)


def test_the_same_url_hides_the_deadline_despite_different_terms(home: Path) -> None:
    url = "https://www.example.org/p"
    postings, applied = _season_pair(
        "Summer Research (Summer 2027)", "Summer Research (Winter 2027)", applied_url=url, url=url
    )
    assert _deadline_lines(postings, applied) == []


def test_a_fragment_or_a_trailing_slash_is_the_same_url_and_a_query_is_not(
    home: Path,
) -> None:
    """Mutations: keep the fragment -- "#apply" reopens the posting; drop the
    query -- one Greenhouse job hides another."""
    page = "https://x.org/fellowship"
    for applied_url in (f"{page}#apply", f"{page}/", "HTTPS://X.org/fellowship"):
        postings, applied = _season_pair("Fellowship", "Fellowship", applied_url, page)
        assert _deadline_lines(postings) != []  # premise: listed before the application
        assert _deadline_lines(postings, applied) == [], applied_url
    job = "https://boards.greenhouse.io/embed/job_app?gh_jid="
    postings, applied = _season_pair("Fellowship", "Fellowship", f"{job}1", f"{job}2")
    assert len(_deadline_lines(postings, applied)) == 1


def test_days_count_from_today_in_this_zone_across_a_clock_change(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At 00:30 on 11 October in New York the clocks are at -04:00, and a
    deadline on 5 November is stored at -05:00; read at -05:00, today is still
    10 October. A deadline typed in another zone shows its date in this one.
    Mutations: count from today at the deadline's offset -- 26 days; show the
    date at its own offset -- 2026-10-20."""
    _use_zone(monkeypatch, "America/New_York")
    now = datetime(2026, 10, 11, 4, 30, tzinfo=UTC)
    november = parse_deadline("2026-11-05")
    pacific = datetime.fromisoformat("2026-10-20T23:30-07:00")
    assert november is not None
    assert now.astimezone().isoformat() == "2026-10-11T00:30:00-04:00"  # premise
    assert november.isoformat() == "2026-11-05T23:59:00-05:00"  # premise
    postings = [_programme("nov", "Nov Lab", november), _programme("pac", "Pac Lab", pacific)]
    result = agenda(postings, [], PROFILE, FACTS, now=now)
    assert [i.what for i in result.items if i.kind == "deadline"] == [
        "Pac Lab Summer Research: closes in 10 days (2026-10-21)",
        "Nov Lab Summer Research: closes in 25 days (2026-11-05)",
    ]


def test_a_posting_with_no_company_does_not_stop_the_agenda(home: Path) -> None:
    """A posting can be stored with an empty company; it matches an
    application by URL alone. Mutation: compare it by name -- `Target`
    refuses the empty company and the whole agenda raises."""
    nameless = _programme("anon", "Anon", NOW + timedelta(days=5)).model_copy(
        update={"company": ""}
    )
    with pytest.raises(ValueError, match="company"):
        Target(job_id="x", company="", title="T", source="other")  # premise
    quiet = _application("acme-ml", "Acme", 30)
    result = agenda([nameless, _posting("g", "Globex")], [quiet], PROFILE, FACTS, now=NOW)
    assert [i.kind for i in result.items] == ["deadline", "apply", "silent"]
    by_url = quiet.model_copy(update={"url": nameless.url})
    assert _deadline_lines([nameless], [by_url]) == []


def test_the_window_edges_are_inclusive_at_a_week_and_a_month(home: Path) -> None:
    """Mutations: `<=` to `<` on the closed bound drops the first; `>` to `>=`
    on the 30-day bound drops the third."""
    week, month = timedelta(days=7), timedelta(days=30)
    edges = {
        "a": NOW - week,
        "b": NOW - week - timedelta(minutes=1),
        "c": NOW + month,
        "d": NOW + month + timedelta(hours=1),
        "e": NOW + month - timedelta(hours=1),
    }
    postings = [_programme(k, f"Lab {k}", v) for k, v in edges.items()]
    shown = {line.split()[1] for line in _deadline_lines(postings)}
    assert shown == {"a", "c", "e"}


def _apply_rows(postings: list[Found], applications: list[Application]) -> list[str]:
    result = agenda(postings, applications, PROFILE, FACTS, now=NOW)
    return [item.command for item in result.items if item.kind == "apply"]


def test_a_programme_applied_to_under_a_longer_company_name_is_not_suggested(home: Path) -> None:
    """The deadline line was suppressed while the shortlist still offered the
    posting, which it does once the deadline is past the window. Mutation:
    drop the shortlist's `_is_applied` skip -- the row shows."""
    cern = _programme("ssp", "CERN", NOW + timedelta(days=10)).model_copy(
        update={"title": "Summer Student", "url": "https://cern.ch/ssp"}
    )
    later = cern.model_copy(update={"deadline": NOW + timedelta(days=60)})
    applied = _application(
        "cern-ssp", "CERN (European Organization for Nuclear Research)", days_ago=1
    ).model_copy(update={"title": "Summer Student", "url": "https://cern.ch/ssp/"})
    assert applied.company.casefold() != cern.company.casefold()  # premise: names differ
    assert _deadline_lines([cern]) != [] and _apply_rows([later], []) != []  # premise: offered
    assert _deadline_lines([cern], [applied]) == []
    assert _apply_rows([later], [applied]) == []


def test_a_last_cycle_application_does_not_hide_this_cycle_s_posting(home: Path) -> None:
    """Different URLs are different postings, whatever the titles; the same
    URL in another spelling is the same one. Mutation: let only a shared URL
    decide, falling back to names otherwise -- the 2027 posting is hidden."""
    posting = _programme("lab", "Lab", NOW + timedelta(days=10)).model_copy(
        update={"title": "Lab / Summer Research Intern", "url": "https://lab.org/ssr/2027"}
    )
    last = _application("lab-2026", "Lab", days_ago=300).model_copy(
        update={"title": "Lab / Summer Research Intern", "url": "https://lab.org/ssr/2026"}
    )
    same = last.model_copy(update={"url": "HTTPS://Lab.org/ssr/2027/"})
    target = Target(job_id="x", company="Lab", title=posting.title, source="other")
    assert last.same_posting(target)  # premise: the names alone match
    assert len(_deadline_lines([posting], [last])) == 1
    undated = posting.model_copy(update={"deadline": None})  # a dated one is not shortlisted
    assert len(_apply_rows([undated], [last])) == 0  # the same company stays skipped
    renamed = last.model_copy(update={"company": "Lab Inc."})
    assert renamed.same_posting(target)  # premise: the names alone still match
    assert len(_apply_rows([undated], [renamed])) == 1
    assert _deadline_lines([posting], [same]) == []
