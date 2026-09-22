from __future__ import annotations

from pathlib import Path

import pytest

from core.answers import AnswerSet, check_answers, render_answers
from core.letter.models import Sentence
from tests.test_letter import FACTS, POSTING

# A form answer is held to the cover letter's sentence rules; these tests
# cover what is new: limits, and the paste-ready rendering.


def answers(*items: dict[str, object]) -> AnswerSet:
    return AnswerSet.model_validate(
        {
            "job_id": "acme-ml-intern",
            "company": "Acme Robotics",
            "role": "Machine Learning Intern",
            "candidate_name": "Dana Vex",
            "answers": list(items),
        }
    )


CLAIM = Sentence(
    kind="candidate",
    text="At Northwind Analytics I trained a scikit-learn classifier with 91% precision.",
    sources=["nw", "nw-model"],
)
WHY = Sentence(
    kind="employer",
    text="Acme Robotics is building a perception data platform for warehouse robots.",
    sources=["posting"],
)


def test_a_grounded_answer_within_its_limit_passes() -> None:
    doc = answers({"question": "Why Acme?", "max_chars": 300, "sentences": [WHY, CLAIM]})
    assert check_answers(doc, FACTS, POSTING).findings == ()


@pytest.mark.parametrize(
    ("limits", "expected"),
    [({"max_chars": 100}, "characters"), ({"max_words": 12}, "words")],
)
def test_an_answer_over_its_limit_fails(limits: dict[str, int], expected: str) -> None:
    """The limit counts the exact text that will be pasted. Mutation: count
    only the first sentence -- this fails."""
    doc = answers({"question": "Why Acme?", **limits, "sentences": [WHY, CLAIM]})
    found = check_answers(doc, FACTS, POSTING).findings
    assert any(expected in f and "answer 0" in f for f in found), found


def test_answer_sentences_are_held_to_the_letter_rules() -> None:
    laundered = Sentence(kind="candidate", text="I have 10 years of PyTorch.", sources=["posting"])
    salary = Sentence(kind="candidate", text="My salary expectations are flexible.", sources=["nw"])
    doc = answers(
        {"question": "Experience?", "sentences": [laundered]},
        {"question": "Anything else?", "sentences": [salary]},
    )
    found = check_answers(doc, FACTS, POSTING).findings
    assert any(f.startswith("answer 0") and "never the posting" in f for f in found), found
    assert any(f.startswith("answer 1") and "commitment" in f for f in found), found


def test_the_company_and_role_are_checked_against_the_posting() -> None:
    doc = answers({"question": "Why us?", "sentences": [WHY]}).model_copy(
        update={"company": "Acme Dynamics"}
    )
    assert any("company 'Acme Dynamics'" in f for f in check_answers(doc, FACTS, POSTING).findings)


def test_the_rendering_is_paste_ready_and_states_each_count() -> None:
    doc = answers({"question": "Why Acme?", "max_chars": 300, "sentences": [WHY, CLAIM]})
    text = render_answers(doc)
    assert "## Why Acme?" in text
    body = f"{WHY.text} {CLAIM.text}"
    assert body in text
    assert f"{len(body)} / 300 characters" in text


def test_an_answer_needs_at_least_one_sentence() -> None:
    with pytest.raises(ValueError):
        answers({"question": "Why?", "sentences": []})


@pytest.mark.slow
def test_callback_answers_writes_a_private_report_and_paste_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stat

    import yaml

    from core.cli import main
    from core.paths import ensure_home
    from core.profile.store import save_facts

    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    home = ensure_home()
    save_facts(FACTS)
    (home / "inbox" / "acme-ml-intern.md").write_text(POSTING, encoding="utf-8")
    source = tmp_path / "answers.yaml"
    doc = answers({"question": "Why Acme?", "max_chars": 300, "sentences": [WHY, CLAIM]})
    source.write_text(yaml.safe_dump(doc.model_dump(mode="json")), encoding="utf-8")
    assert main(["answers", str(source)]) == 0
    draft = home / "outputs" / "acme-ml-intern" / "draft"
    for name in ("answers.md", "answers-report.json"):
        assert stat.S_IMODE((draft / name).stat().st_mode) == 0o600, name
    assert main(["answers", str(tmp_path / "missing.yaml")]) == 3
