from __future__ import annotations

import stat
from pathlib import Path

import pytest

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


HONEST = [
    Sentence(
        text="I am applying for the Machine Learning Intern role at Acme Robotics.", sources=[]
    ),
    Sentence(
        text="Acme Robotics is building a perception data platform for warehouse robots.",
        sources=["posting"],
    ),
    Sentence(
        text="At Northwind Analytics I trained a scikit-learn classifier that flagged "
        "duplicate claims with 91% precision.",
        sources=["nw", "nw-model"],
    ),
    Sentence(text="Thank you for your consideration.", sources=[]),
]


def findings(doc: CoverLetter, *, bullets: list[str] | None = None) -> tuple[str, ...]:
    return check_letter(doc, FACTS, POSTING, resume_bullets=bullets or []).findings


def test_an_honest_letter_passes() -> None:
    assert findings(letter()) == ()


def test_a_first_person_claim_cannot_be_grounded_in_the_posting() -> None:
    """The posting is untrusted text. Mutation: let a first-person sentence
    use the posting as a source -- this test fails."""
    laundered = Sentence(text="I have 10 years of PyTorch experience.", sources=["posting"])
    found = findings(letter([laundered]))
    assert any("sentence 0.0" in f for f in found), found


def test_a_first_person_claim_absent_from_its_facts_fails() -> None:
    inflated = Sentence(text="At Northwind Analytics I led a team of twelve.", sources=["nw"])
    found = findings(letter([inflated]))
    assert any("sentence 0.0" in f for f in found), found


def test_a_sentence_about_the_company_is_held_to_the_posting() -> None:
    stated = Sentence(text="Acme Robotics trains models in Python.", sources=["posting"])
    invented = Sentence(text="Acme Robotics recently raised a Series C.", sources=["posting"])
    assert findings(letter([stated])) == ()
    found = findings(letter([invented]))
    assert any("'Series'" in f for f in found), found


@pytest.mark.parametrize(
    ("field", "value"), [("company", "Acme Dynamics"), ("role", "Senior ML Engineer")]
)
def test_the_company_and_role_must_appear_in_the_posting(field: str, value: str) -> None:
    found = findings(letter(**{field: value}))
    assert any(f"{field} {value!r}" in f for f in found), found


def test_an_unsourced_sentence_may_carry_no_claim() -> None:
    assert findings(letter([Sentence(text="I would welcome a conversation.", sources=[])])) == ()
    found = findings(letter([Sentence(text="I shipped 40 models.", sources=[])]))
    assert found, "an unsourced number passed"
    # Not in the first person, but not citing the posting either: the posting
    # may not back it. Mutation: ground unsourced sentences in the posting.
    echoed = Sentence(text="Candidates with 10 years are preferred.", sources=[])
    assert findings(letter([echoed])), "an unsourced sentence borrowed the posting's number"


@pytest.mark.parametrize(
    "text",
    [
        "I am available to start immediately.",
        "My salary expectations are flexible.",
        "I am willing to relocate to Pittsburgh.",
        "I would require visa sponsorship.",
        "I am happy to work unpaid.",
    ],
)
def test_commitment_and_negotiation_language_fails(text: str) -> None:
    """A signed letter must not commit the candidate to terms, which is what
    an injected posting would try to insert."""
    found = findings(letter([Sentence(text=text, sources=[])]))
    assert any("commitment" in f for f in found), found


def test_a_letter_over_300_words_fails() -> None:
    padding = [Sentence(text="I am keen to learn more about the role.", sources=[])] * 40
    found = findings(letter(padding))
    assert any("300" in f for f in found), found


def test_a_resume_bullet_reused_verbatim_fails() -> None:
    bullet = "Trained a scikit-learn classifier that flagged duplicate claims with 91% precision"
    reused = Sentence(text=f"{bullet}.", sources=["nw-model"])
    found = findings(letter([reused]), bullets=[bullet])
    assert any("verbatim" in f for f in found), found


def test_an_unknown_or_unconfirmed_source_fails() -> None:
    found = findings(letter([Sentence(text="I trained models.", sources=["nope"])]))
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
