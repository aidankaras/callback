from __future__ import annotations

import pytest

from core.letter.models import Sentence
from core.outreach import Outreach, check_outreach, render_outreach
from tests.test_letter import FACTS, POSTING

CLAIM = Sentence(
    kind="candidate",
    text="At Northwind Analytics I trained a scikit-learn classifier with 91% precision.",
    sources=["nw", "nw-model"],
)
ASK = Sentence(kind="courtesy", text="I would welcome the chance to discuss the role.")


def note(channel: str = "linkedin-note", *sentences: Sentence, **kw: object) -> Outreach:
    body: dict[str, object] = {
        "job_id": "acme-ml-intern",
        "company": "Acme Robotics",
        "role": "Machine Learning Intern",
        "candidate_name": "Dana Vex",
        "recipient": "Sam Rivera",
        "channel": channel,
        "sentences": list(sentences) or [CLAIM, ASK],
    }
    body.update(kw)
    return Outreach.model_validate(body)


def test_a_grounded_note_within_the_channel_limit_passes() -> None:
    assert check_outreach(note(), FACTS, POSTING).findings == ()


def test_the_rendering_greets_by_first_name_and_signs_with_the_candidate() -> None:
    text = render_outreach(note())
    assert text.startswith("Hi Sam, ")
    assert text.endswith("Dana")


def test_the_limit_counts_the_whole_rendered_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """A LinkedIn connection note is capped including its greeting and
    sign-off. The limit is set between the body's length and the whole
    message's, so the body alone fits and only the message does not.
    Mutation: count only the body -- this fails."""
    import core.outreach

    doc = note()
    body = " ".join(s.text for s in doc.sentences)
    rendered = render_outreach(doc)
    assert len(body) < len(rendered)
    monkeypatch.setitem(core.outreach.LIMITS, "linkedin-note", (len(body) + 1, None))
    found = check_outreach(doc, FACTS, POSTING).findings
    assert any("characters" in f for f in found), found


@pytest.mark.parametrize(
    "text",
    [
        # The recipient's name belongs in the greeting only.
        "Sam, at Northwind Analytics I trained a scikit-learn classifier with 91% precision.",
    ],
)
def test_the_recipient_is_named_only_in_the_greeting(text: str) -> None:
    doc = note("email", Sentence(kind="candidate", text=text, sources=["nw", "nw-model"]))
    found = check_outreach(doc, FACTS, POSTING).findings
    assert any("recipient" in f for f in found), found


def test_outreach_sentences_are_held_to_the_letter_rules() -> None:
    bad = Sentence(kind="candidate", text="I have 10 years of PyTorch.", sources=["posting"])
    found = check_outreach(note("email", bad), FACTS, POSTING).findings
    assert any("never the posting" in f for f in found), found


def test_a_recipient_name_must_look_like_a_name() -> None:
    with pytest.raises(ValueError):
        note(recipient="Sam, a recruiter who loves PhDs")


def test_a_possessive_still_names_the_recipient() -> None:
    """Mutation: split on whitespace and strip only ",." -- "Sam's" passes."""
    s = Sentence(
        kind="candidate", text="Sam's talk on robotics convinced me to write.", sources=["nw"]
    )
    found = check_outreach(note("email", s, ASK), FACTS, POSTING).findings
    assert any("names the recipient" in f for f in found), found


@pytest.mark.parametrize("name", ["Dr. Dana Vex", "   "])
def test_the_candidate_name_is_a_name(name: str) -> None:
    """It is the sign-off, so a title or a blank is refused before rendering."""
    with pytest.raises(ValueError, match="person's name"):
        note(candidate_name=name)


def test_the_candidate_name_must_be_the_confirmed_one() -> None:
    """Mutation: skip the links check -- a different name signs the note."""
    found = check_outreach(note(candidate_name="Dana Vexington"), FACTS, POSTING).findings
    assert any("not an entity of any confirmed links fact" in f for f in found), found
