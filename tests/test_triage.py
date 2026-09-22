from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.discovery.store import Found
from core.profile.models import Fact, FactKind, Provenance, Trust
from core.triage import Profile, check_eligibility, rank

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
US_JUNIOR = Profile(work_authorization="US citizen", graduation="May 2028", degree="bachelor")


def kinds(text: str, profile: Profile = US_JUNIOR) -> list[tuple[str, str]]:
    return [(f.rule, f.level) for f in check_eligibility(text, profile).findings]


# --- work authorization -------------------------------------------------------------


def test_a_sponsorship_offer_is_not_a_knockout() -> None:
    """Measured on a live board: the only sponsorship sentences present said
    they *do* sponsor, but not for every role. For someone who needs
    sponsorship that is a question, never a refusal. Mutation: ignore the
    offer -- this reads as a knockout."""
    text = "Visa sponsorship: We do sponsor visas! However, we aren't able to sponsor every role."
    candidate = Profile(work_authorization="F-1 student, will need sponsorship")
    assert kinds(text, candidate) == [("sponsorship", "unknown")]
    assert kinds("We do sponsor visas, but are unable to sponsor every role.", candidate) == []


@pytest.mark.parametrize(
    "text",
    [
        "We are unable to sponsor visas for this role.",
        "This position is not eligible for visa sponsorship.",
        "We will not provide sponsorship now or in the future.",
        "Candidates must be authorized to work in the US without sponsorship.",
    ],
)
def test_a_refusal_to_sponsor_is_a_knockout_only_for_someone_who_needs_it(text: str) -> None:
    needs = Profile(work_authorization="F-1, will need H-1B sponsorship")
    assert kinds(text, needs) == [("sponsorship", "knockout")]
    assert check_eligibility(text, US_JUNIOR).verdict == "eligible"


@pytest.mark.parametrize(
    "text",
    [
        "Must be a U.S. citizen.",
        "U.S. citizenship is required.",
        # A capital after "U.S." would split the sentence without folding it to "US".
        "Applicants must be a U.S. Citizen at the time of hire.",
    ],
)
def test_a_citizenship_requirement_is_checked(text: str) -> None:
    assert kinds(text) == []
    assert kinds(text, Profile(work_authorization="Canadian citizen")) == [
        ("citizenship", "knockout")
    ]


def test_a_clearance_requirement_is_a_question_for_a_citizen_and_a_knockout_otherwise() -> None:
    """The profile records no clearance, so for a citizen it is asked, not assumed."""
    text = "An active Secret security clearance is required."
    assert kinds(text) == [("clearance", "unknown")]
    assert kinds(text, Profile(work_authorization="Canadian citizen")) == [
        ("clearance", "knockout")
    ]


def test_an_unknown_authorization_is_a_question_not_a_guess() -> None:
    assert kinds("We are unable to sponsor visas.", Profile()) == [("sponsorship", "unknown")]


# --- graduation window --------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "level"),
    [
        ("Open to students graduating between December 2026 and June 2027.", "knockout"),
        ("Must be graduating between December 2027 and June 2028.", None),
        ("Expected graduation in 2028.", None),
        ("Class of 2027 only.", "knockout"),
    ],
)
def test_a_graduation_window_is_compared_with_the_candidates(text: str, level: str | None) -> None:
    """Mutation: skip the window comparison -- the knockout cases pass."""
    expected = [("graduation", level)] if level else []
    assert kinds(text) == expected


def test_a_graduation_window_with_no_graduation_on_file_is_a_question() -> None:
    text = "Open to students graduating between December 2026 and June 2027."
    assert kinds(text, Profile(work_authorization="US citizen")) == [("graduation", "unknown")]


# --- degree and experience (stretches, never knockouts) ---------------------------------


def test_a_graduate_degree_requirement_is_a_stretch_for_an_undergraduate() -> None:
    text = "Master's/PhD degree in Computer Science, Machine Learning or a related field."
    assert kinds(text) == [("degree", "stretch")]
    assert kinds(text, US_JUNIOR.model_copy(update={"degree": "phd"})) == []


def test_several_years_of_experience_is_a_stretch() -> None:
    assert kinds("8+ years of hands-on data science experience.") == [("experience", "stretch")]
    assert kinds("1+ year of experience with SQL.") == []


def test_every_finding_quotes_the_posting() -> None:
    (finding,) = check_eligibility("We are unable to sponsor visas.", Profile()).findings
    assert "unable to sponsor" in finding.quote


# --- ranking --------------------------------------------------------------------------


def _posting(pid: str, title: str, text: str, days_old: int) -> Found:
    return Found(
        id=pid,
        ats="greenhouse",
        board="acme",
        company="Acme",
        title=title,
        url=f"https://job-boards.greenhouse.io/acme/jobs/{pid}",
        text=text,
        first_seen=NOW - timedelta(days=days_old),
    )


FACTS = {
    "s": Fact(
        id="s",
        kind=FactKind.SKILL,
        text="Python, SQL",
        entities=["Python", "SQL"],
        provenance=Provenance(source="x", trust=Trust.USER_CONFIRMED),
    )
}


def test_ranking_puts_knockouts_last_and_prefers_fresh_postings_you_can_evidence() -> None:
    postings = [
        _posting("old", "Data Science Intern", "Python and SQL.", days_old=20),
        _posting("fresh", "Data Science Intern", "Python and SQL.", days_old=1),
        _posting("ko", "ML Intern", "Python. Class of 2027 only.", days_old=0),
        _posting("gap", "ML Intern", "PyTorch and Kubernetes.", days_old=1),
    ]
    ranked = rank(postings, US_JUNIOR, FACTS, now=NOW)
    assert [r.posting.id for r in ranked] == ["fresh", "old", "gap", "ko"]
    fresh = ranked[0]
    assert fresh.coverage == (["Python", "SQL"], [])
    # Only technologies the alias table recognises count; it has no "Rust".
    assert ranked[2].coverage == ([], ["Kubernetes", "PyTorch"])


def test_the_triage_commands(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pathlib import Path

    from core.cli import main
    from core.paths import ensure_home

    assert isinstance(tmp_path, Path)
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    home = ensure_home()
    assert main(["profile", "--work-authorization", "US citizen", "--graduation", "May 2028"]) == 0
    assert "May 2028" in capsys.readouterr().out
    (home / "inbox" / "ko.md").write_text("# Intern - Acme\n\nClass of 2027 only.\n")
    assert main(["eligibility", "ko"]) == 2
    out = capsys.readouterr().out
    assert out.startswith("KNOCKOUT") and "Class of 2027 only." in out
    (home / "inbox" / "ok.md").write_text("# Intern - Acme\n\nPython and SQL.\n")
    assert main(["eligibility", "ok"]) == 0
    assert main(["triage"]) == 0


def test_gaps_count_the_technologies_eligible_postings_ask_for_that_facts_do_not_back() -> None:
    """Knockouts are excluded: a skill only an ineligible posting wants is not a gap
    worth closing. Mutation: count knockout postings too -- Kubernetes rises to 2."""
    from core.triage import gaps

    postings = [
        _posting("a", "ML Intern", "Python, PyTorch and Kubernetes.", days_old=1),
        _posting("b", "ML Intern", "PyTorch and SQL.", days_old=2),
        _posting("c", "ML Intern", "Kubernetes. Class of 2027 only.", days_old=0),
    ]
    assert gaps(postings, US_JUNIOR, FACTS) == [("PyTorch", 2), ("Kubernetes", 1)]
