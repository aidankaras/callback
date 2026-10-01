from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

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
    # The same two statements in one sentence read the same way.
    one_sentence = "We do sponsor visas, but are unable to sponsor every role."
    assert kinds(one_sentence, candidate) == [("sponsorship", "unknown")]
    assert kinds("We do sponsor visas.", candidate) == []


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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("At least 0-2 years experience in trading or trade support role", []),
        ("1\u20133 years of experience with SQL.", []),
        ("2-3 years of experience in research.", [("experience", "stretch")]),
        ("0 to 2 years of experience.", []),
        ("Between 1 and 3 years of experience.", []),
        ("0\u20142 years of experience.", []),
    ],
)
def test_a_range_of_years_counts_from_its_low_end(
    text: str, expected: list[tuple[str, str]]
) -> None:
    """Mutation: read the range's high end -- "0-2 years" becomes a stretch."""
    assert kinds(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Currently pursuing a bachelor\u2019s, master\u2019s, or PhD in a quantitative field.",
        "Are pursuing a Bachelor\u2019s, Master\u2019s or PhD in a technical discipline.",
        "Currently pursuing a BS, MS, or PhD in Computer Science",
        "Currently pursuing a BSc, MSc or PhD in Computer Science",
        "Must be currently enrolled as a junior, senior, or PhD student",
        "Current students (BS/MS/PhD) welcome",
        "Currently enrolled in a four-year university or PhD program",
        "Currently in your third year of college or in a PhD program",
        "We welcome PhD students and undergraduates.",
    ],
)
def test_a_degree_sentence_naming_the_candidates_level_admits_them(text: str) -> None:
    """Mutation: drop the year-of-study and BS/BSc forms -- several of these
    become findings."""
    assert kinds(text) == []


def test_a_sentence_that_admits_the_candidate_clears_one_that_mentions_phds() -> None:
    """Mutation: keep the first degree finding -- the team sentence wins."""
    text = (
        "You'll join our team of PhD students and engineers. "
        "Requirements: Currently pursuing a Bachelor's, Master's or PhD in CS."
    )
    assert kinds(text) == []


def test_a_year_of_study_word_alone_admits_no_one() -> None:
    """ "senior" in a sentence about colleagues is not a degree list.
    Mutation: let a sentence naming only the candidate's level admit --
    the PhD finding is cleared."""
    text = "Open to current PhD students only. You will work with senior researchers."
    assert kinds(text) == [("degree", "knockout")]


@pytest.mark.parametrize(
    "text",
    [
        "Open to current PhD students only.",
        "Must be enrolled in a PhD program.",
        "Only Ph.D. students in Computer Science are eligible.",
        # A college word outside a list of levels admits no one.
        "Must be enrolled in a PhD program at an accredited university.",
        "Must be a PhD student working with senior engineers.",
        "Candidates must be currently enrolled in a Master\u2019s or PhD program.",
    ],
)
def test_a_stated_requirement_for_current_graduate_students_is_a_knockout(text: str) -> None:
    """Mutation: drop the requirement check -- the mentions below become knockouts."""
    assert kinds(text) == [("degree", "knockout")]
    assert kinds(text, US_JUNIOR.model_copy(update={"degree": "phd"})) == []


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        # Mentions, refusals and preferences: a knockout would hide the posting.
        ("Join our team of PhD students and researchers working on LLMs", "students"),
        ("This role is not open to current PhD students", "students"),
        ("We are not looking for PhD candidates for this role", "students"),
        ("Currently enrolled; PhD students not eligible.", "students"),
        ("Masters program students are also eligible.", "master"),
        ("PhD students are encouraged to apply.", "students"),
        ("Ideal for students currently pursuing a PhD only", "students"),
        ("We are seeking exceptional PhD candidates to join our Quant Resource team.", "students"),
        ("Students enrolled in a PhD program in machine learning or statistics.", "students"),
        ("Our typical intern candidates come from quantitative PhD programs.", "students"),
        (
            "Master's degree or equivalent experience preferred; candidates should be curious.",
            "degree",
        ),
        ("PhD preferred.", "degree"),
        ("M.S. or PhD in Statistics.", "degree"),
    ],
)
def test_a_graduate_degree_that_is_not_a_stated_requirement_is_a_stretch(
    text: str, reason: str
) -> None:
    """Mutation: drop the negation or the hedge check -- a refusal or a
    preference that also says "only" or "eligible" becomes a knockout."""
    found = check_eligibility(text, US_JUNIOR).findings
    assert [(f.rule, f.level) for f in found] == [("degree", "stretch")]
    expected = {
        "students": "for current phd students",
        "master": "for current master students",
        "degree": "asks for a graduate degree",
    }[reason]
    assert found[0].reason == expected


@pytest.mark.parametrize(
    "auth", ["American citizen", "USA citizen", "Citizen of the United States", "U.S. citizen"]
)
def test_a_us_citizen_written_any_common_way_is_a_citizen(auth: str) -> None:
    """Mutation: drop the American/USA/"citizen of" forms -- these knock out."""
    assert kinds("Applicants must be US citizens.", Profile(work_authorization=auth)) == []


def test_a_bare_citizen_profile_is_asked_not_knocked_out() -> None:
    """ "citizen" names no country. Mutation: drop the bare-citizen check --
    a citizenship requirement knocks the candidate out."""
    assert kinds("Must be a US citizen.", Profile(work_authorization="citizen.")) == [
        ("citizenship", "unknown")
    ]
    bare = Profile(work_authorization="citizen")
    assert kinds("Applicants must be US citizens.", bare) == [("citizenship", "unknown")]
    assert kinds("Must hold an active TS/SCI clearance.", bare) == [("clearance", "unknown")]


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


def test_ranking_prefers_more_evidence_over_a_perfect_ratio_on_one() -> None:
    """Both postings are fully evidenced; one names two technologies, one names
    one. Mutation: set `COVERAGE_PRIOR` to 0 -- they tie, and input order wins."""
    postings = [
        _posting("one", "Data Science Intern", "Python.", days_old=1),
        _posting("two", "Data Science Intern", "Python and SQL.", days_old=1),
    ]
    ranked = rank(postings, US_JUNIOR, FACTS, now=NOW)
    assert [r.posting.id for r in ranked] == ["two", "one"]
    assert ranked[0].score > ranked[1].score


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


@pytest.mark.parametrize(
    "text",
    ["U.S. Citizens only.", "Only US citizens are eligible.", "Must be a U.S.&nbsp;citizen."],
)
def test_plural_and_html_citizenship_requirements_are_read(text: str) -> None:
    assert kinds(text, Profile(work_authorization="F-1 student")) == [("citizenship", "knockout")]


def test_a_posting_without_sentence_breaks_is_read_in_windows() -> None:
    """With no breaks the whole posting is one sentence: an offer elsewhere
    must not erase a refusal, and unrelated years must not widen a window."""
    needs = Profile(work_authorization="F-1, will need sponsorship", graduation="May 2028")
    run_on = "we sponsor some roles but we cannot sponsor this one and we care about growth"
    assert kinds(run_on, needs) == [("sponsorship", "unknown")]
    window = (
        "open to students graduating between December 2026 and June 2027 founded in 2031 at scale"
    )
    assert kinds(window, needs) == [("graduation", "knockout")]


@pytest.mark.parametrize(
    ("text", "graduation", "flagged"),
    [
        # "by" and "no later than" bound only the late side: finishing sooner qualifies.
        ("Open to students graduating by June 2027.", "May 2026", False),
        ("Open to students graduating by June 2027.", "May 2028", True),
        ("Candidates must graduate no later than December 2026.", "May 2026", False),
        # "after" bounds only the early side.
        ("For students graduating after May 2027.", "May 2028", False),
        ("For students graduating after May 2027.", "May 2026", True),
        # Two dates, or a year named as the graduation's own, is a closed window.
        ("Graduating between December 2026 and June 2027.", "May 2028", True),
        ("Class of 2027 only.", "May 2028", True),
        # A year that is not the graduation's is no requirement at all.
        ("We have hired new graduates every year since 2015.", "May 2028", False),
        ("Our graduate programme has run since 2015.", "May 2026", False),
        # No direction word either: a closed window at 2015 would knock out everyone.
        ("Our team includes 2015 alumni of the graduate programme.", "May 2028", False),
    ],
)
def test_a_graduation_window_is_read_in_the_direction_it_is_written(
    text: str, graduation: str, flagged: bool
) -> None:
    """Mutations: treat every window as closed -- the "by"/"after" rows fail;
    take any year in a graduation sentence -- the "since 2015" rows fail."""
    profile = Profile(work_authorization="US citizen", graduation=graduation)
    found = [f for f in check_eligibility(text, profile).findings if f.rule == "graduation"]
    assert bool(found) is flagged, found


# --- one posting, judged against the facts ----------------------------------------------


def test_fit_names_the_facts_that_back_each_technology() -> None:
    """The point of the report is knowing what to cite. Mutation: return the
    technologies without their fact ids -- this fails."""
    from core.triage import fit

    facts = {
        "nw": Fact(
            id="nw",
            kind=FactKind.EXPERIENCE_DETAIL,
            text="Trained a classifier in Python with PyTorch",
            entities=["Python", "PyTorch"],
            provenance=Provenance(source="x", trust=Trust.USER_CONFIRMED),
        ),
        "old": Fact(
            id="old",
            kind=FactKind.SKILL,
            text="Wrote SQL for the reporting stack",
            entities=["SQL"],
            provenance=Provenance(source="x", trust=Trust.IMPORT_UNCONFIRMED),
        ),
        "db": Fact(
            id="db",
            kind=FactKind.SKILL,
            text="Used Python for data loading",
            entities=["Python"],
            provenance=Provenance(source="x", trust=Trust.USER_CONFIRMED),
        ),
    }
    posting = "We use Python, PyTorch and SQL. Kubernetes experience helps."
    result = fit(posting, Profile(work_authorization="US citizen"), facts)
    assert result.evidenced == (("PyTorch", ("nw",)), ("Python", ("db", "nw")))
    # SQL is named by an unconfirmed fact only, so it is missing, not evidenced.
    assert result.missing == ("Kubernetes", "SQL")
    assert result.coverage == 0.5


def test_fit_carries_the_postings_knockouts() -> None:
    from core.triage import fit

    posting = "We cannot sponsor visas. You will write Python."
    profile = Profile(work_authorization="F-1 student, will need sponsorship")
    result = fit(posting, profile, {})
    assert result.eligibility.verdict == "knockout"
    assert [f.rule for f in result.eligibility.findings] == ["sponsorship"]


def test_the_page_limit_is_part_of_the_profile(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from core.cli import main
    from core.paths import ensure_home
    from core.triage import load_profile

    assert isinstance(tmp_path, Path)
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    home = ensure_home()
    (home / "profile.yaml").write_text("graduation: May 2028\n", encoding="utf-8")
    assert load_profile().resume_pages == 1, "a profile written before the field existed"

    assert main(["profile", "--pages", "2"]) == 0
    assert load_profile().resume_pages == 2
    assert load_profile().graduation == "May 2028"

    capsys.readouterr()
    assert main(["profile", "--pages", "0"]) == 3
    assert "resume_pages" in capsys.readouterr().err
    assert load_profile().resume_pages == 2
