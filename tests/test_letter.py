from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest

from core.briefs import Brief
from core.checks.style import copied_run
from core.letter.checks import check_letter
from core.letter.models import CoverLetter, Sentence
from core.profile.models import Dates, Fact, FactKind, Identity, Provenance, Quantity, Trust

CONFIRMED = Provenance(source="onboarding_answer", trust=Trust.USER_CONFIRMED)
POSTING = """# Machine Learning Intern - Acme Robotics

Acme Robotics is building a perception data platform for warehouse robots.
You will train and evaluate models in Python. Familiarity with PyTorch helps.
Candidates with 10 years of PyTorch experience are preferred.
"""
FACTS = {
    f.id: f
    for f in (
        Fact(
            id="nw",
            kind=FactKind.EXPERIENCE,
            text="Data Engineer at Northwind Analytics",
            organization="Northwind Analytics",
            title="Data Engineer",
            dates=Dates(start="2024-06", end="2025-08"),
            entities=["Northwind Analytics"],
            provenance=CONFIRMED,
        ),
        Fact(
            id="nw-model",
            kind=FactKind.EXPERIENCE_DETAIL,
            parent="nw",
            text=(
                "Trained a scikit-learn classifier that flagged duplicate claims with 91% precision"
            ),
            numbers={"precision": Quantity(value=91, unit="%")},
            entities=["scikit-learn"],
            provenance=CONFIRMED,
        ),
        Fact(
            id="contact",
            kind=FactKind.LINKS,
            text="Dana Vex, dana@example.com",
            entities=["Dana Vex", "dana@example.com"],
            provenance=CONFIRMED,
        ),
    )
}


def letter(*paragraphs: list[Sentence], **overrides: object) -> CoverLetter:
    body: dict[str, object] = {
        "identity": Identity(name="Dana Vex", email="dana@example.com"),
        "job_id": "acme-ml-intern",
        "company": "Acme Robotics",
        "role": "Machine Learning Intern",
        "greeting": "Dear Hiring Team,",
        "paragraphs": [list(p) for p in paragraphs] or [HONEST],
        "closing": "Sincerely,",
    }
    body.update(overrides)
    return CoverLetter.model_validate(body)


def c(text: str, *sources: str) -> Sentence:
    return Sentence(kind="candidate", text=text, sources=list(sources))


def e(text: str) -> Sentence:
    return Sentence(kind="employer", text=text, sources=["posting"])


def k(text: str) -> Sentence:
    return Sentence(kind="courtesy", text=text)


HONEST = [
    k("I am applying for the Machine Learning Intern role at Acme Robotics."),
    e("Acme Robotics is building a perception data platform for warehouse robots."),
    c(
        "At Northwind Analytics I trained a scikit-learn classifier that flagged duplicate "
        "claims with 91% precision.",
        "nw",
        "nw-model",
    ),
    k("Thank you for your consideration."),
]


def findings(doc: CoverLetter, *, bullets: list[str] | None = None) -> tuple[str, ...]:
    return check_letter(doc, FACTS, POSTING, resume_bullets=bullets or []).findings


def test_an_honest_letter_passes() -> None:
    assert findings(letter()) == ()


# --- claims about the candidate -------------------------------------------------


def test_a_claim_about_the_candidate_can_never_cite_the_posting() -> None:
    """Mutation: allow the posting as a source for a candidate sentence."""
    found = findings(letter([c("I have 10 years of PyTorch experience.", "posting")]))
    assert any("never the posting" in f for f in found), found


def test_a_claim_absent_from_its_facts_fails() -> None:
    found = findings(letter([c("At Northwind Analytics I led a team of twelve.", "nw")]))
    assert any("sentence 0.0" in f for f in found), found


# A posting that carries the claims an employer sentence might try to borrow.
BAIT = POSTING + (
    "Our last intern led a team of twelve and shipped 40 models in PyTorch.\n"
    "Candidates with a patent are preferred. Dana is a name we like.\n"
)


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("This candidate led a team of twelve and shipped 40 models in PyTorch.", "begins with"),
        ("Led a team of twelve and shipped 40 models in PyTorch.", "begins with"),
        ("The team would gain from my 40 models in PyTorch.", "may not speak about"),
        ("The team knows Dana holds a patent.", "may not speak about"),
    ],
)
def test_an_employer_sentence_cannot_describe_the_candidate(text: str, rule: str) -> None:
    """Every claim here is in the posting, so grounding alone would pass them.
    Mutation: drop the subject rule, or the first-person and name rule -- the
    matching cases fail."""
    found = check_letter(letter([e(text)]), FACTS, BAIT).findings
    assert any(rule in f for f in found), found


def test_a_sentence_about_the_candidate_may_not_name_the_company() -> None:
    """Otherwise "I was an intern at <company>" rests only on the letter's header."""
    found = findings(letter([c("I was a Machine Learning Intern at Acme Robotics.", "nw")]))
    assert any("may not name Acme Robotics" in f for f in found), found


def test_one_sentence_may_not_hide_another() -> None:
    """A second sentence's first word would be exempt from the name rule."""
    text = "At Northwind Analytics I trained models. Google then hired me."
    found = findings(letter([c(text, "nw")]))
    assert any("more than one sentence" in f for f in found), found


# --- claims about the employer ----------------------------------------------------


def test_an_employer_sentence_is_held_to_the_posting() -> None:
    assert findings(letter([e("Acme Robotics trains models in Python.")])) == ()
    found = findings(letter([e("Acme Robotics recently raised a Series C.")]))
    assert any("'Series'" in f for f in found), found


def test_an_employer_sentence_cites_only_the_posting() -> None:
    found = findings(letter([Sentence(kind="employer", text="The role uses Python.")]))
    assert any("cites the posting and nothing else" in f for f in found), found


# --- courtesy, greeting, closing ---------------------------------------------------


def test_a_courtesy_sentence_must_be_one_of_the_templates() -> None:
    """Mutation: accept any courtesy text -- this test fails."""
    found = findings(letter([k("Dana shipped production models that customers rely on.")]))
    assert any("courtesy" in f for f in found), found


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("greeting", "Dear Hiring Team, as a Google alumna with a Turing Award,"),
        ("greeting", "Dear Hiring Team 2026,"),
        ("closing", "Sincerely, Dana Vex, PhD, salary expectation $200k"),
    ],
)
def test_the_greeting_and_closing_are_closed_forms(field: str, value: str) -> None:
    found = findings(letter(**{field: value}))
    assert any(field in f for f in found), found


# --- the letter's header and its limits ---------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("company", "Acme Dynamics"),
        # Present in the posting, but only inside other text.
        ("company", "PyTorch"),
        ("role", "Intern"),
        ("role", "Senior ML Engineer"),
    ],
)
def test_the_company_and_role_must_be_segments_of_the_posting_heading(
    field: str, value: str
) -> None:
    """Mutation: accept any substring of the posting -- the middle two pass."""
    found = findings(letter(**{field: value}))
    assert any(f"{field} {value!r}" in f for f in found), found


@pytest.mark.parametrize(
    "text",
    [
        "I am available to start immediately.",
        "My salary expectations are flexible.",
        "I am willing to relocate to Pittsburgh.",
        "I would require visa sponsorship.",
        "I am happy to work unpaid.",
        "My availability is flexible.",
        "I am available in June.",
    ],
)
def test_commitment_and_negotiation_language_fails(text: str) -> None:
    found = findings(letter([c(text, "nw")]))
    assert any("commitment" in f for f in found), found


def test_a_letter_over_300_words_fails() -> None:
    padding = [k("Thank you for your time and consideration.")] * 45
    found = findings(letter(padding))
    assert any("300" in f for f in found), found


def test_a_resume_bullet_reused_verbatim_fails() -> None:
    bullet = "Trained a scikit-learn classifier that flagged duplicate claims with 91% precision"
    found = findings(letter([c(f"{bullet}.", "nw-model")]), bullets=[bullet])
    assert any("verbatim" in f for f in found), found


def test_an_unknown_source_fails() -> None:
    found = findings(letter([c("I trained models.", "nope")]))
    assert any("unknown" in f for f in found), found


# --- the command -----------------------------------------------------------------


@pytest.mark.slow
def test_callback_letter_writes_a_private_pdf_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.cli import main
    from core.letter.io import dump_letter
    from core.paths import ensure_home
    from core.profile.store import save_facts

    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    home = ensure_home()
    save_facts(FACTS)
    (home / "inbox" / "acme-ml-intern.md").write_text(POSTING, encoding="utf-8")
    source = tmp_path / "letter.yaml"
    source.write_text(dump_letter(letter()), encoding="utf-8")

    assert main(["letter", str(source)]) == 0
    draft = home / "outputs" / "acme-ml-intern" / "draft"
    for name in ("cover-letter.pdf", "letter-report.md"):
        assert stat.S_IMODE((draft / name).stat().st_mode) == 0o600, name
    assert (draft / "letter-report.md").read_text().startswith("# PASS")
    record = json.loads((draft / "letter-report.json").read_text())
    pdf = (draft / "cover-letter.pdf").read_bytes()
    assert record["verdict"] == "pass"
    assert record["pdf_sha256"] == hashlib.sha256(pdf).hexdigest()


@pytest.mark.slow
def test_callback_letter_refuses_without_a_saved_posting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from core.cli import main
    from core.letter.io import dump_letter
    from core.paths import ensure_home

    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    ensure_home()
    source = tmp_path / "letter.yaml"
    source.write_text(dump_letter(letter()), encoding="utf-8")
    assert main(["letter", str(source)]) == 3
    # The refusal, not a crash reading the missing file, which also exits 3.
    assert "no saved posting" in capsys.readouterr().err


# --- sentences that describe the candidate by other means ------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Acme Robotics already hired this applicant as a Python intern last summer.",
        "The team interviewed the applicant twice and extended an offer.",
        "The team met her at the robotics expo.",
    ],
)
def test_an_employer_sentence_may_not_describe_the_candidate(text: str) -> None:
    """Neither "I" nor the candidate's name appears, so only the referent rule
    can see these. Mutation: drop it -- each passes the posting grounding."""
    found = check_letter(
        letter([Sentence(kind="employer", text=text, sources=["posting"])]), FACTS, POSTING
    ).findings
    assert any("may not speak about the candidate" in f for f in found), found


@pytest.mark.parametrize(
    "text",
    ["Alex suggested I apply for the role.", "We met at the career fair last spring."],
)
def test_a_relationship_is_a_claim_its_fact_must_state(text: str) -> None:
    """The cited fact says nothing of a meeting or a referral. Mutation: drop
    the relationship words from the claim list -- both pass."""
    found = check_letter(
        letter([Sentence(kind="candidate", text=text, sources=["nw"])]), FACTS, POSTING
    ).findings
    assert any("unsupported_claim_word" in f for f in found), found


def test_a_letter_header_must_be_the_confirmed_identity() -> None:
    """The header is rendered as-is. Mutation: skip the identity check -- the
    invented credential and email pass."""
    forged = Identity(name="Dr. Dana Vex", email="dana@example.com")
    found = check_letter(letter(identity=forged), FACTS, POSTING).findings
    assert any("'Dr. Dana Vex'" in f for f in found), found
    assert check_letter(letter(), FACTS, POSTING).findings == ()


@pytest.mark.parametrize("word", ["visas", "sponsors", "sponsoring", "relocating"])
def test_commitment_language_is_caught_in_its_inflected_forms(word: str) -> None:
    """Mutation: require a word boundary after "visa"/"sponsorship" -- the
    plural and participle forms pass."""
    text = f"The role mentions {word} for the successful candidate."
    found = check_letter(
        letter([Sentence(kind="employer", text=text, sources=["posting"])]), FACTS, POSTING
    ).findings
    assert any("commitment" in f for f in found), found


def test_a_percentage_in_the_posting_cannot_ground_a_count() -> None:
    """The posting's numbers keep their unit. Mutation: drop it -- "40" is
    grounded by the posting's "40%"."""
    posting = POSTING + "About 40% of our traffic is realtime.\n"
    # Premise: the posting states 40 only as a percentage.
    assert "40%" in posting and " 40 " not in posting
    sentence = Sentence(
        kind="employer", text="The team serves 40 customers in Python.", sources=["posting"]
    )
    found = check_letter(letter([sentence]), FACTS, posting).findings
    assert any("not derivable" in f for f in found), found
    # And the same claim as a percentage is grounded by it.
    same = Sentence(kind="employer", text="The team serves 40% in Python.", sources=["posting"])
    assert check_letter(letter([same]), FACTS, posting).findings == ()


def test_a_sentence_glued_to_the_one_before_it_is_still_two() -> None:
    """A missing space does not make two sentences one, and the grounding
    tokenizer reads "precision.Stanford" as a single word, so the invented
    name would go unchecked. Mutation: require whitespace after the stop."""
    glued = Sentence(
        kind="candidate",
        text=(
            "At Northwind Analytics I trained a model with 91% precision.Stanford then admitted me."
        ),
        sources=["nw", "nw-model"],
    )
    found = check_letter(letter([glued]), FACTS, POSTING).findings
    assert any("more than one sentence" in f for f in found), found


@pytest.mark.parametrize(
    ("greeting", "allowed"),
    [
        ("Dear Hiring Team,", True),
        ("Dear Sam Rivera,", True),
        ("Dear Hiring Team at the world's best robotics lab,", False),
        ("Dear Stanford alumni network and friends at Acme,", False),
    ],
)
def test_a_greeting_is_a_salutation_not_a_clause(greeting: str, allowed: bool) -> None:
    """The greeting is rendered verbatim and no sentence rule reads it.
    Mutation: allow 60 characters of free text -- both clauses pass."""
    found = check_letter(letter(greeting=greeting), FACTS, POSTING).findings
    assert (found == ()) is allowed, found


def _brief(exemplar: str) -> Brief:
    return Brief.model_validate(
        {
            "field": "ml-research",
            "built_at": "2026-10-10T14:00:00-04:00",
            "sources": [
                {
                    "id": "s1",
                    "kind": "career-centre",
                    "publisher": "Example University",
                    "title": "Sample letters",
                    "url": "https://careers.example.edu/letters",
                    "fetched_at": "2026-10-10T13:00:00-04:00",
                }
            ],
            "exemplars": [
                {"kind": "letter-paragraph", "text": exemplar, "source": "s1", "why": "w"}
            ],
        }
    )


def test_the_excited_to_apply_opening_is_a_stock_phrase() -> None:
    opening = k("I am excited to apply for the Machine Learning Intern role at Acme Robotics.")
    found = findings(letter([opening, *HONEST[1:]]))
    assert any("stock phrase 'I am excited to apply'" in f for f in found), found


def test_a_letter_copying_its_briefs_exemplar_fails() -> None:
    brief = _brief(
        "I trained a scikit-learn classifier that flagged duplicate invoices for a bank."
    )
    found = check_letter(letter(), FACTS, POSTING, brief=brief).findings
    assert any("copies an exemplar of the ml-research brief" in f for f in found), found
    assert check_letter(letter(), FACTS, POSTING).findings == ()


# --- a named brief ------------------------------------------------------------------


@pytest.mark.parametrize("loaded", [None, "quant-research"])
def test_a_letter_naming_a_brief_that_was_not_loaded_fails(loaded: str | None) -> None:
    """Otherwise the copy check and that brief's phrases silently do not run.
    Mutation: drop the guard -- both letters pass."""
    brief = (
        None if loaded is None else _brief("Unrelated words.").model_copy(update={"field": loaded})
    )
    found = check_letter(letter(brief="ml-research"), FACTS, POSTING, brief=brief).findings
    assert found == ("the letter names brief 'ml-research', which was not loaded",)
    named = _brief("Unrelated words.")
    assert check_letter(letter(brief="ml-research"), FACTS, POSTING, brief=named).findings == ()


def test_a_courtesy_sentence_an_exemplar_contains_is_not_copying() -> None:
    """A courtesy sentence must be one of the fixed list, so failing it as
    copied would leave a letter that can never pass."""
    courtesy = "Thank you for your time and consideration."
    exemplar = f"I would value the chance to talk further. {courtesy}"
    # Premise: the exemplar holds the courtesy sentence's six words.
    assert copied_run(courtesy, [exemplar]) == "thank you for your time and"
    doc = letter([*HONEST[:3], k(courtesy)])
    assert check_letter(doc, FACTS, POSTING, brief=_brief(exemplar)).findings == ()


def test_a_sentence_sharing_its_cited_facts_wording_with_an_exemplar_is_not_copying() -> None:
    exemplar = "Trained a scikit-learn classifier that flagged duplicate claims at a bank."
    sentence = HONEST[2]
    # Premise: six of the sentence's words are the exemplar's, and its fact's.
    assert copied_run(sentence.text, [exemplar]) == "trained a scikit learn classifier that"
    assert copied_run(sentence.text, [FACTS["nw-model"].text]) is not None
    assert check_letter(letter(), FACTS, POSTING, brief=_brief(exemplar)).findings == ()


def _letter_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from core.paths import ensure_home
    from core.profile.store import save_facts

    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    home = ensure_home()
    save_facts(FACTS)
    (home / "inbox" / "acme-ml-intern.md").write_text(POSTING, encoding="utf-8")
    return home


@pytest.mark.slow
def test_callback_letter_checks_against_the_brief_it_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop `brief=brief` where the command calls `check_letter` --
    the letter then fails as naming a brief not loaded, not as copying."""
    from core.letter.cmd import build_letter
    from core.letter.io import dump_letter
    from tests.test_briefs import store_brief

    home = _letter_home(tmp_path, monkeypatch)
    exemplar = "I trained a scikit-learn classifier that flagged duplicate invoices for a bank."
    store_brief(home, "ml-research", exemplar)
    source = tmp_path / "letter.yaml"
    source.write_text(dump_letter(letter(brief="ml-research")), encoding="utf-8")
    found = build_letter(source).findings
    assert found == (
        "sentence 0.2: copies an exemplar of the ml-research brief: "
        "'i trained a scikit learn classifier'",
    )


@pytest.mark.slow
def test_callback_letter_refuses_a_brief_that_fails_its_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from core.cli import main
    from core.letter.io import dump_letter
    from tests.test_briefs import SOURCE, store_brief

    home = _letter_home(tmp_path, monkeypatch)
    store_brief(home, "ml-research", sources=[{**SOURCE, "url": "https://www.linkedin.com/x"}])
    source = tmp_path / "letter.yaml"
    source.write_text(dump_letter(letter(brief="ml-research")), encoding="utf-8")
    assert main(["letter", str(source)]) == 3
    assert "source 's1' is on linkedin.com" in capsys.readouterr().err


@pytest.mark.slow
def test_a_letter_report_records_the_facts_it_graded_not_an_edit_made_during_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.letter.cmd
    from core.cli import main
    from core.letter.io import dump_letter
    from core.paths import ensure_home
    from core.profile.store import save_facts

    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    home = ensure_home()
    save_facts(FACTS)
    (home / "inbox" / "acme-ml-intern.md").write_text(POSTING, encoding="utf-8")
    source = tmp_path / "letter.yaml"
    source.write_text(dump_letter(letter()), encoding="utf-8")
    store = home / "facts.yaml"
    graded = store.read_bytes()
    real = core.letter.cmd.check_letter

    def edit_then_check(*args: object, **kwargs: object) -> object:
        store.write_bytes(graded + b"# edited mid-build\n")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("core.letter.cmd.check_letter", edit_then_check)
    assert main(["letter", str(source)]) == 0
    draft = home / "outputs" / "acme-ml-intern" / "draft"
    record = json.loads((draft / "letter-report.json").read_text())
    assert store.read_bytes() != graded  # premise: the store changed during the build
    assert record["facts_sha256"] == hashlib.sha256(graded).hexdigest()
