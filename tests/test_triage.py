from __future__ import annotations

import re
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


# --- degree and experience ------------------------------------------------------------


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
        "Open to rising juniors, seniors or PhD candidates.",
        "Candidates must be a rising junior or senior, or a current PhD student.",
        "Must be enrolled as a junior or senior in college or a PhD student.",
        "Must be a current sophomore, junior or senior, or a master's or PhD student.",
        "Rising Junior or Senior (Class of 2028 or 2029), or Master's/PhD student.",
        "Students entering their junior or senior year, or PhD students, are eligible.",
        "Open only to rising juniors and PhD students.",
        "Open to junior or senior engineering students, or PhD students.",
        "Junior or senior data science majors or PhD candidates are eligible.",
        "Must be a junior or senior software engineering major or a PhD student.",
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
    "colleagues",
    [
        "You will work with junior or senior engineers and PhD researchers.",
        "You will work with junior and senior engineers and PhD researchers.",
        "Collaborate with junior, senior and staff engineers and PhD researchers.",
        "Senior or staff engineers and PhD researchers mentor every intern.",
    ],
)
def test_year_words_describing_colleagues_admit_no_one(colleagues: str) -> None:
    """A singular year word followed by a colleague noun is an adjective.
    Mutation: drop the colleague-noun lookahead -- the sentence admits the
    candidate and clears the knockout."""
    assert kinds(colleagues) == [("degree", "stretch")]  # premise: names no level of ours
    assert kinds(f"Open to current PhD students only. {colleagues}") == [("degree", "knockout")]


def test_a_knockout_after_a_stretch_for_the_same_rule_is_kept() -> None:
    """Sentences are read in document order; the most severe finding per rule
    wins. Mutation: keep the first finding per rule -- the stretch hides the
    knockout."""
    stretch = "We are seeking exceptional PhD candidates to join our Quant Resource team."
    knockout = "Open to current PhD students only."
    assert kinds(stretch) == [("degree", "stretch")]  # premise
    assert kinds(knockout) == [("degree", "knockout")]  # premise
    assert kinds(f"{stretch} {knockout}") == [("degree", "knockout")]
    assert kinds(f"{knockout} {stretch}") == [("degree", "knockout")]


def test_a_replaced_finding_keeps_findings_in_document_order() -> None:
    """Mutation: replace the stretch in its own slot -- the degree finding,
    quoting the last sentence, is listed before the experience one."""
    text = (
        "We are seeking exceptional PhD candidates. Requires 3+ years of experience"
        " in Python. Open to current PhD students only."
    )
    assert kinds(text) == [("experience", "stretch"), ("degree", "knockout")]


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


# --- season and duration --------------------------------------------------------------

SUMMER_ONLY = US_JUNIOR.model_copy(update={"seasons": ("summer",), "max_weeks": 14})


def season(text: str, title: str) -> list[tuple[str, str]]:
    return kinds_titled(text, title, SUMMER_ONLY)


def kinds_titled(text: str, title: str, profile: Profile) -> list[tuple[str, str]]:
    findings = check_eligibility(text, profile, title=title).findings
    return [(f.rule, f.level) for f in findings if f.rule in {"season", "duration"}]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Data Science Intern (Winter 2027)", [("season", "knockout")]),
        ("Data Science Intern (Fall 2027)", [("season", "knockout")]),
        ("Data Science Intern (Winter)", [("season", "knockout")]),
        ("Quant Researcher (Off-Cycle - Winter/Spring 2027 Intern)", [("season", "knockout")]),
        ("Co-op, Data Engineering", [("season", "knockout")]),
        ("Autumn 2027 Analyst", [("season", "knockout")]),
        ("Data Science Intern - Winter '27", [("season", "knockout")]),
        ("Analyst Intern (Summer/Fall 2027)", [("season", "stretch")]),
        ("Software Engineer, Intern (Summer or Winter)", [("season", "stretch")]),
        ("Data Science Intern (Summer 2027)", []),
    ],
)
def test_the_title_states_the_term(title: str, expected: list[tuple[str, str]]) -> None:
    """Only the title can knock a posting out on its term: it is the employer's
    own statement of which term the role is. Mutation: drop the allowed-season
    check in the title rule -- the "Summer/Fall" and "Summer or Winter" titles
    become knockouts."""
    assert season("Python and SQL.", title) == expected


@pytest.mark.parametrize(
    ("title", "text", "word"),
    [
        # A team name, not a term: no year, term word or bracket beside it.
        ("Fall Risk Analytics Intern", "Join the Fall Risk Analytics team.", "fall"),
        # An address.
        ("Data Intern", "Our office is on Spring Street, New York.", "spring"),
        # A verb and a noun.
        ("Data Intern", "Tasks that fall under the team. A spring in your step.", "fall"),
        # A company named in the body.
        ("Summer Analyst Intern", "Spring Health is our client.", "spring"),
    ],
)
def test_a_season_word_that_names_no_term_is_not_one(title: str, text: str, word: str) -> None:
    """Mutation: drop `_TERM_AFTER`/`_TERM_BEFORE` and count every season word --
    each of these becomes a knockout or a stretch."""
    assert word in f"{title} {text}".casefold()  # premise: the word is there
    assert season(text, title) == []


def test_a_term_mentioned_in_the_body_is_only_a_stretch() -> None:
    text = "Summer 2027 Analyst; we also run winter and spring cohorts."
    assert "winter" in text and "spring" in text  # premise
    assert season(text, "Summer 2027 Analyst") == [("season", "stretch")]


def test_a_graduation_window_naming_seasons_is_not_a_term() -> None:
    """Measured on a real board: "a graduation date in Fall 2027 or Spring 2028"
    sat in summer postings, and reading it as a term made 13 of them a stretch.
    Mutation: drop the `_GRADUATION` guard -- this becomes a stretch."""
    text = "Students graduating in Fall 2027 or Spring 2028 are eligible."
    between = "Expecting to graduate between Winter 2027 and Summer 2028."
    assert "Fall 2027" in text and "Winter 2027" in between  # premise
    assert season(text, "Data Intern (Summer 2027)") == []
    assert season(between, "Data Intern (Summer 2027)") == []


def test_a_term_before_a_graduation_word_is_still_a_term() -> None:
    """A list joined by tags reads as one sentence, so the term and a degree
    requirement share it. Mutation: skip every season in a sentence naming
    graduation -- this loses its stretch."""
    text = "<ul><li>Fall 2027 internship</li><li>Pursuing a bachelor or graduate degree</li></ul>"
    assert text.index("Fall 2027") < text.index("graduate")  # premise
    assert season(text, "Data Intern") == [("season", "stretch")]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("This is a 24-week internship based in Berlin.", [("duration", "knockout")]),
        ("The programme runs six months from January.", [("duration", "knockout")]),
        ("The internship runs 4 months.", [("duration", "knockout")]),
        ("The internship runs 3 months.", []),
        ("Interns typically spend 12 weeks with us.", []),
        ("Internship program: 10-26 weeks, full-time.", [("duration", "stretch")]),
        ("Internship program: 12 - 24 weeks, full-time.", [("duration", "stretch")]),
        ("The internship lasts up to 24 weeks.", [("duration", "stretch")]),
        ("This internship runs 6 months.", [("duration", "knockout")]),
        ("The minimum internship period is 6 months.", [("duration", "knockout")]),
        # 2.5 months is 10.8 weeks. Read as "5-month internship", the second is
        # a knockout; the first would only go unread.
        ("The internship lasts 2.5 months.", []),
        ("This is a 2.5-month internship.", []),
        # The lower option fits.
        ("We offer internships of 3 or 6 months.", [("duration", "stretch")]),
        ("The internship lasts from 6 to 12 months.", [("duration", "knockout")]),
        # Measured on a real board: read as nothing, this posting lost its stretch.
        ("The internship will be 3-6 months, including the summer.", [("duration", "stretch")]),
    ],
)
def test_the_role_s_stated_length(text: str, expected: list[tuple[str, str]]) -> None:
    """A range counts from its low end, and a hedge is a stretch: either way the
    candidate may fit. Mutation: use the high end of a range -- "10-26 weeks"
    becomes a knockout."""
    assert season(text, "ML Intern") == expected


def test_a_length_that_is_not_the_role_s_is_not_counted() -> None:
    """Mutation: count every length, dropping the check that the sentence
    attributes it to the role -- the onboarding sentence knocks the internship
    out. The semicolon case is a length of something else in the same sentence."""
    onboarding = "Our 6-month onboarding for full-time hires starts in March."
    semicolon = "This is a 10-week internship; full-time hires complete a 6-month rotation."
    graduation = "Apply as a 3rd year internship, or within 12 months of graduation."
    assert "6-month" in onboarding and "6-month" in semicolon and "12 months" in graduation
    assert season(onboarding, "ML Intern") == []
    assert season(semicolon, "ML Intern") == []
    assert season(graduation, "ML Intern") == []


def test_a_posting_stating_a_length_that_fits_is_never_a_duration_knockout() -> None:
    """Measured on a real board: one posting described several internships, most
    "10-week", one "minimum 6 months". A knockout would hide every role in it.
    Mutation: drop the `fitting_length` downgrade -- this is a knockout."""
    text = (
        "Over the 10-week internship program, you will learn. "
        "The minimum internship period is 6 months."
    )
    assert season(text, "Quant Intern") == [("duration", "stretch")]
    [finding] = check_eligibility(text, SUMMER_ONLY, title="Quant Intern").findings
    assert finding.reason == (
        'longer than 14 weeks; it also states "Over the 10-week internship program, you will learn"'
    )


@pytest.mark.parametrize(
    "text",
    [
        "Summer 2027. Offers go out 4 months before the internship starts.",
        "Internship candidates should have 6 months of Python experience.",
        "Data Science Intern. Applicants must be enrolled in a 24-month master's program.",
        "Summer 2027. Past interns have joined our 18-month rotational program.",
        "Return offers are made within 6 months of internship completion.",
        "Candidates must have 6 months of internship experience.",
        "Requirements: 6 months internship experience",
        "Apply 6 months before internship start.",
        "Our rotational program runs 18 months.",
        "Our graduate program is 24 months.",
        "Applicants should have worked as interns for 6 months.",
        "Candidates with prior internships of 6 months are preferred.",
        "Our interns are 6 months from graduation.",
        "Return offers remain open to interns for 6 months.",
        "Within 6 months, most internship hires convert to full-time.",
        "After 6 months, internship participants receive a review.",
        "Every 6 months, internship cohorts present.",
        "Duration: 12 months after graduation",
        "Duration: 18 months graduate programme follows",
    ],
)
def test_a_length_near_a_role_word_is_not_the_role_s(text: str) -> None:
    """Each length here exceeds 14 weeks and sits beside a role word, but is
    the length of something else. Mutation: count every length -- each becomes
    a duration knockout."""
    assert re.search(r"\b(?:4|6|12|18|24)[ -]months?\b", text)  # premise: a length too long
    assert ("duration", "knockout") not in season(text, "")


@pytest.mark.parametrize(
    "other",
    [
        "Interns receive 2 weeks of paid time off.",
        "Interns begin with a 1-week orientation program.",
        "Offers go out 2 weeks before internship start.",
        "Expect a decision within 2 weeks of internship interviews.",
        "It opens with a 1-week intern bootcamp.",
        "The notice period for interns is 2 weeks.",
        "Paid time off for interns: 2 weeks.",
        "In the first 2 weeks, internship hosts run orientation.",
        "Duration: 2 weeks onboarding",
        "Duration: 2 weeks per rotation",
        "The internship takes 2 weeks to set up.",
    ],
)
def test_a_short_span_beside_a_role_word_softens_a_knockout_and_is_quoted(other: str) -> None:
    """None of these spans is the role's length, but reading one as fitting only
    makes the knockout a stretch whose reason quotes it, so the posting stays
    visible; a knockout would hide it. Mutation: make the downgrade use the
    strict `_role_lengths` reader -- each stays a knockout."""
    knockout = "Data Science Intern. This internship runs 6 months."
    assert season(knockout, "") == [("duration", "knockout")]  # premise
    assert season(f"{knockout} {other}", "") == [("duration", "stretch")]
    [finding] = check_eligibility(f"{knockout} {other}", SUMMER_ONLY, title="").findings
    assert finding.reason.endswith(f'; it also states "{other.rstrip(".")}"')


@pytest.mark.parametrize(
    "other",
    [
        "No prior experience is required for this 10-week internship.",
        "Duration: 10 weeks starting June 2027",
        "Duration: 10 weeks full-time",
        "Duration: 10-12 weeks during the summer",
        "Duration: 10 weeks | Location: Boston",
        "The internship takes 10 weeks to complete.",
        "The internship runs 10 weeks to the end of August.",
        "Once you have completed the 10-week internship, you join a team.",
        "A 10-week all-expenses-paid internship.",
    ],
)
def test_a_fitting_length_the_strict_reader_misses_still_softens_a_knockout(other: str) -> None:
    """Each is this role's own fitting length in a phrasing the knockout reader
    declines (a "prior" or "completed" before it, a "Duration:" value that runs
    on, a "to" after it, a long modifier chain). Mutation: make the downgrade use
    the strict `_role_lengths` reader -- each stays a knockout."""
    knockout = "Data Science Intern. This internship runs 6 months."
    assert season(knockout, "") == [("duration", "knockout")]  # premise
    assert season(f"{knockout} {other}", "") == [("duration", "stretch")]
    [finding] = check_eligibility(f"{knockout} {other}", SUMMER_ONLY, title="").findings
    assert finding.reason.endswith(f'; it also states "{other.rstrip(".")}"')


_RUN_ON_FILLER = (
    " and the team works across data platforms, analytics tooling, reporting pipelines,"
    " stakeholder dashboards, model monitoring, documentation and weekly demos with partners"
    " from finance, operations, marketing and product"
)


def test_a_role_word_far_from_a_short_length_in_a_run_on_body_does_not_soften_a_knockout() -> None:
    """Tag-stripped HTML with no sentence break reads as one sentence. Mutation:
    look for the role word anywhere in the sentence instead of in the quoted
    clause -- "internship" at the start vouches for "2 weeks" at the end."""
    body = f"This internship runs 6 months{_RUN_ON_FILLER}{_RUN_ON_FILLER}"
    text = f"<li>{body}, with 2 weeks of paid leave</li>"
    plain = re.sub(r"<[^>]+>", " ", text)
    assert len(plain) > 300 and not re.search(r"[.!?;\n]", plain)  # premise: one long sentence
    assert "internship" in plain and plain.index("2 weeks") - plain.index("internship") > 200
    assert season(text, "") == [("duration", "knockout")]


def test_a_run_on_clause_is_quoted_only_around_its_fitting_length() -> None:
    """Mutation: drop the cut to `_QUOTE_REACH` -- the whole run-on is quoted."""
    other = f"Each cohort{_RUN_ON_FILLER} meets for 2 weeks of intern onboarding{_RUN_ON_FILLER}"
    knockout = "Data Science Intern. This internship runs 6 months."
    assert len(other) > 300  # premise: the clause runs on
    [finding] = check_eligibility(f"{knockout} {other}", SUMMER_ONLY, title="").findings
    assert finding.level == "stretch"
    quote = finding.reason.split("it also states ", 1)[1].strip('"')
    assert "2 weeks of intern onboarding" in quote and len(quote) <= 110


@pytest.mark.parametrize(
    "fitting",
    [
        "No prior experience is required for this 10-week internship.",
        "Duration: 10 weeks starting June 2027",
    ],
)
def test_a_summer_posting_naming_another_role_s_long_minimum_is_not_hidden(fitting: str) -> None:
    """A summer posting that also describes a longer role elsewhere in the firm.
    Mutation: make the downgrade use the strict `_role_lengths` reader -- each
    is a knockout."""
    other_role = "For our off-cycle roles, the minimum internship period is 6 months."
    assert season(other_role, "") == [("duration", "knockout")]  # premise
    text = f"Summer 2027 Analyst.\n{fitting}\n{other_role}"
    assert season(text, "Summer Analyst Intern") == [("duration", "stretch")]
    verdict = check_eligibility(text, SUMMER_ONLY, title="Summer Analyst Intern").verdict
    assert verdict != "knockout"


@pytest.mark.parametrize(
    ("text", "role_clause"),
    [
        (
            "You have completed an internship of at least 6 months.",
            "An internship of at least 6 months.",
        ),
        (
            "You have already completed a first internship of at least 6 months.",
            "A first internship of at least 6 months.",
        ),
        (
            "Ideally you have done an internship lasting 6 months.",
            "An internship lasting 6 months.",
        ),
        (
            "Prior experience: an internship lasting at least 6 months.",
            "An internship lasting at least 6 months.",
        ),
    ],
)
def test_the_candidate_s_past_internship_is_not_this_role_s_length(
    text: str, role_clause: str
) -> None:
    """A requirement about an internship the candidate has done states its
    length too. Mutation: drop the `_CANDIDATE_HISTORY` check -- each is a
    knockout."""
    assert role_clause.rstrip(".").casefold() in text.casefold()  # premise: the same clause
    assert season(role_clause, "") == [("duration", "knockout")]  # ... which alone is read
    assert season(text, "") == []


@pytest.mark.parametrize(
    ("template", "long", "short", "level"),
    [
        ("The internship will be {}.", "6 months", "10 weeks", "knockout"),
        ("Our internships run for {}.", "6 months", "10 weeks", "knockout"),
        ("An internship lasting {}.", "6 months", "10 weeks", "knockout"),
        ("Internship ({}) in Paris.", "6 months", "10 weeks", "knockout"),
        ("The internship will run for {}.", "6 months", "10 weeks", "knockout"),
        ("The internship can last up to {}.", "6 months", "10 weeks", "stretch"),
        ("The internship is scheduled for {}.", "6 months", "10 weeks", "knockout"),
        ("Internships are offered for {}.", "6 months", "10 weeks", "knockout"),
        ("The internship takes {}.", "6 months", "10 weeks", "knockout"),
        ("The internship will be for {}.", "6 months", "10 weeks", "knockout"),
        ("The internship duration will be {}.", "6 months", "10 weeks", "knockout"),
        ("The internship lasts at least {}.", "6 months", "10 weeks", "knockout"),
        ("The internship lasts a minimum of {}.", "6 months", "10 weeks", "knockout"),
        ("The internship runs for a period of {}.", "6 months", "10 weeks", "knockout"),
        ("The internship lasts a full {}.", "6 months", "10 weeks", "knockout"),
        ("The internship runs over {}.", "24 weeks", "10 weeks", "knockout"),
        ("A {}-long internship.", "six-month", "10-week", "knockout"),
        ("A {}-long internship.", "24-week", "10-week", "knockout"),
        ("A {} full-time paid internship.", "6-month", "10-week", "knockout"),
        ("A {}, full-time internship.", "6 month", "10 week", "knockout"),
        ("Internship length - {}", "6 months", "10 weeks", "knockout"),
        ("Duration of internship - {}", "24 weeks", "10 weeks", "knockout"),
        ("Contract type: Internship ({})", "6 months", "10 weeks", "knockout"),
        ("Contract: Internship, {}", "6 months", "10 weeks", "knockout"),
        ("Duration: {}", "6 months", "10 weeks", "knockout"),
    ],
)
def test_a_phrasing_that_states_the_role_s_length_is_read_at_either_length(
    template: str, long: str, short: str, level: str
) -> None:
    """Each phrasing is pinned twice: too long, it is a finding; fitting, beside
    another sentence's knockout, it softens that knockout. Mutation: drop "will"
    from `_LINK_WORDS` -- every "will" phrasing loses its knockout."""
    knockout = "Data Science Intern. This internship runs 6 months."
    assert season(knockout, "") == [("duration", "knockout")]  # premise
    assert season(template.format(long), "") == [("duration", level)]
    assert season(f"{knockout} {template.format(short)}", "") == [("duration", "stretch")]


@pytest.mark.parametrize(
    ("text", "hedge"),
    [
        ("The internship lasts up to 24 weeks.", "up to "),
        ("The internship typically lasts 24 weeks.", "typically "),
        ("The internship usually lasts 24 weeks.", "usually "),
        ("The internship lasts approximately 24 weeks.", "approximately "),
        ("The internship lasts about 24 weeks.", "about "),
        ("The internship lasts around 24 weeks.", "around "),
        ("The internship lasts roughly 24 weeks.", "roughly "),
        ("The internship lasts between 20 and 24 weeks.", "between "),
    ],
)
def test_each_hedge_makes_a_length_a_stretch(text: str, hedge: str) -> None:
    """Mutation: drop any one word from `_HEDGE_WORDS` -- its case fails."""
    assert season(text.replace(hedge, ""), "") == [("duration", "knockout")]  # premise
    assert season(text, "") == [("duration", "stretch")]


@pytest.mark.parametrize(
    ("title", "text"),
    [
        ("Backend Engineering Intern (Spring Boot)", "This is a 12-week Summer 2027 internship."),
        ("Software Engineer Intern (Java/Spring)", "Summer 2027 internship in Boston."),
        ("Data Science Intern (Spring Health)", "Join us for Summer 2027."),
    ],
)
def test_a_season_word_in_a_bracket_with_other_words_is_not_a_term(title: str, text: str) -> None:
    """Mutation: count an opening bracket alone before a season word as a term --
    each title becomes a knockout."""
    assert "(" in title and "Spring" in title  # premise
    assert check_eligibility(text, SUMMER_ONLY, title=title).verdict == "eligible"


def test_a_saved_posting_s_heading_names_the_company_last() -> None:
    """Mutation: read the whole heading line as the title -- REI's "Co-op"
    knocks the posting out."""
    body = "Join our summer 2027 internship."
    # premise: the company's "Co-op", read as part of the title, is a knockout
    assert season(body, "Data Analyst Intern - REI Co-op") == [("season", "knockout")]
    assert (
        check_eligibility(f"# Data Analyst Intern - REI Co-op\n\n{body}\n", SUMMER_ONLY).findings
        == ()
    )
    # Only the last " - " is the company's; the title keeps its own.
    assert season("Python.", "Intern") == []  # premise: cutting at the first loses the term
    saved = "# Intern - Winter 2027 - Acme\n\nPython.\n"
    assert [(f.rule, f.level) for f in check_eligibility(saved, SUMMER_ONLY).findings] == [
        ("season", "knockout")
    ]


def test_the_season_rules_run_only_when_the_profile_asks() -> None:
    text = "This is a 24-week internship."
    assert kinds_titled(text, "Data Intern (Winter 2027)", US_JUNIOR) == []


def test_a_saved_posting_s_heading_is_its_title() -> None:
    """`take` writes `# <title> - <company>`; `eligibility` and `fit` pass no title."""
    saved = "# Data Science Intern (Winter 2027) - Acme\n\nPython and SQL.\n"
    assert [(f.rule, f.level) for f in check_eligibility(saved, SUMMER_ONLY).findings] == [
        ("season", "knockout")
    ]


def test_seasons_round_trip_through_the_profile_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing profile file gains the fields without losing what it held."""
    from core.triage import load_profile, save_profile

    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path))
    save_profile(SUMMER_ONLY)
    assert load_profile() == SUMMER_ONLY


def test_the_profile_command_sets_seasons_and_a_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from core.cli import main

    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    assert main(["profile", "--seasons", "summer, fall", "--max-weeks", "14"]) == 0
    out = capsys.readouterr().out
    assert "seasons              summer, fall" in out and "max_weeks            14" in out
    assert main(["profile", "--seasons", "monsoon"]) == 3
    capsys.readouterr()
    assert main(["profile", "--seasons", ""]) == 0
    assert "seasons              (not set)" in capsys.readouterr().out
