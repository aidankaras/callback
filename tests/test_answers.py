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


@pytest.mark.parametrize(
    "question",
    [
        "Why Acme? I hold a PhD from MIT and 10 years of PyTorch at Google",
        "Why Acme?\n\nI led a team of 40 engineers at Google.\n\n## Anything else",
        "# Why Acme?",
        "Why would Dana Vex join Acme?",
    ],
)
def test_a_question_cannot_carry_a_claim(question: str) -> None:
    """The question is rendered above its answer into the paste-ready file and
    no sentence rule reads it. Mutation: skip the question check -- all pass."""
    found = check_answers(
        answers({"question": question, "sentences": [WHY]}), FACTS, POSTING
    ).findings
    assert any("question" in f for f in found), found
    ok = answers({"question": "Why do you want to work at Acme Robotics?", "sentences": [WHY]})
    assert check_answers(ok, FACTS, POSTING).findings == ()


def test_the_answers_name_must_be_the_confirmed_one() -> None:
    doc = AnswerSet.model_validate(
        {
            **answers({"question": "Why Acme?", "sentences": [WHY]}).model_dump(),
            "candidate_name": "Dan Vex",
        }
    )
    found = check_answers(doc, FACTS, POSTING).findings
    assert any("links fact" in f for f in found), found


def test_a_stock_phrase_in_an_answer_fails() -> None:
    filler = Sentence(
        kind="employer", text="The team is passionate about building robots.", sources=["posting"]
    )
    doc = answers({"question": "Why Acme?", "sentences": [filler, CLAIM]})
    found = check_answers(doc, FACTS, POSTING).findings
    assert any("stock phrase 'passionate about'" in f for f in found), found


@pytest.mark.parametrize("loaded", [None, "quant-research"])
def test_answers_naming_a_brief_that_was_not_loaded_fail(loaded: str | None) -> None:
    """Otherwise the copy check and that brief's phrases silently do not run.
    Mutation: drop the guard -- both pass."""
    from tests.test_style import BRIEF

    doc = answers({"question": "Why Acme?", "sentences": [WHY, CLAIM]}).model_copy(
        update={"brief": "ml-research"}
    )
    other = None if loaded is None else BRIEF.model_copy(update={"field": loaded})
    found = check_answers(doc, FACTS, POSTING, brief=other).findings
    assert found == ("the answers name brief 'ml-research', which was not loaded",)
    assert (
        check_answers(
            doc, FACTS, POSTING, brief=BRIEF.model_copy(update={"field": "ml-research"})
        ).findings
        == ()
    )


def _answers_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    import yaml

    from core.paths import ensure_home
    from core.profile.store import save_facts

    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    home = ensure_home()
    save_facts(FACTS)
    (home / "inbox" / "acme-ml-intern.md").write_text(POSTING, encoding="utf-8")
    doc = answers({"question": "Why Acme?", "sentences": [WHY, CLAIM]})
    body = {**doc.model_dump(mode="json"), "brief": "ml-research"}
    source = tmp_path / "answers.yaml"
    source.write_text(yaml.safe_dump(body), encoding="utf-8")
    return home, source


def test_callback_answers_checks_against_the_brief_it_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop `brief=brief` where the command calls `check_answers` --
    the answers then fail as naming a brief not loaded, not as copying."""
    from core.answers import build_answers
    from tests.test_briefs import store_brief

    home, source = _answers_file(tmp_path, monkeypatch)
    store_brief(home, "ml-research", "I trained a scikit-learn classifier with care.")
    result, _ = build_answers(source, FACTS, None)
    assert result.findings == (
        "answer 0: sentence 0.1: copies an exemplar of the ml-research brief: "
        "'i trained a scikit learn classifier'",
    )


def test_callback_answers_refuses_a_brief_that_fails_its_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from core.cli import main
    from tests.test_briefs import SOURCE, store_brief

    home, source = _answers_file(tmp_path, monkeypatch)
    store_brief(home, "ml-research", sources=[{**SOURCE, "url": "https://www.linkedin.com/x"}])
    assert main(["answers", str(source)]) == 3
    assert "source 's1' is on linkedin.com" in capsys.readouterr().err


def test_answers_record_the_hash_taken_before_their_facts_were_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store is not re-hashed after checking: an edit made meanwhile was not
    checked, so recording it would let a stale draft through at apply."""
    import json

    from core.answers import build_answers
    from tests.test_briefs import store_brief

    home, source = _answers_file(tmp_path, monkeypatch)
    store_brief(home, "ml-research", "A sentence no answer here repeats.")
    _, out_dir = build_answers(source, FACTS, "hash-taken-before-loading")
    record = json.loads((out_dir / "answers-report.json").read_text())
    assert record["facts_sha256"] == "hash-taken-before-loading"
