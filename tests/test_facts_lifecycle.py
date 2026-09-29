"""What happens to a fact after it is drafted: declined, replaced while still
a draft, checked against its own text, and retired once it is no longer true."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
import yaml

from core.checks.grounding import FindingKind, check_bullet
from core.cli import main
from core.factscmd import (
    ConfirmError,
    FactsError,
    add_from_file,
    check_facts,
    confirm,
    retire,
)
from core.paths import ensure_home
from core.profile.models import Trust
from core.profile.store import load_facts


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    return ensure_home()


def _file(tmp_path: Path, facts: list[dict[str, object]], name: str = "facts.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(facts), encoding="utf-8")
    return path


def _draft(fid: str, text: str, **fields: object) -> dict[str, object]:
    return {
        "id": fid,
        "kind": "experience_detail",
        "text": text,
        "provenance": {"source": "import:resume.pdf", "trust": "import_unconfirmed"},
        **fields,
    }


class _Asked:
    """A terminal that answers from a script and records what it was shown."""

    def __init__(self, answers: str) -> None:
        self.answers = io.StringIO(answers)
        self.shown = io.StringIO()
        self.opened = 0

    def __call__(self) -> _Asked:
        self.opened += 1
        return self

    def readline(self, size: int = -1, /) -> str:
        return self.answers.readline()

    def write(self, s: str, /) -> int:
        return self.shown.write(s)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


def _three(tmp_path: Path) -> None:
    drafts = [_draft("a", "one"), _draft("b", "two"), _draft("c", "three")]
    assert add_from_file(_file(tmp_path, drafts)) == (["a", "b", "c"], [])


def test_a_declined_fact_is_not_offered_again(tmp_path: Path) -> None:
    _three(tmp_path)
    assert confirm([], open_tty=_Asked("y\nn\ny\n")) == ["a", "c"]
    assert load_facts()["b"].declined_on is not None

    again = _Asked("y\n")
    assert confirm([], open_tty=again) == []

    assert again.opened == 0, "nothing was left to ask about"
    assert not load_facts()["b"].selectable()


def test_a_declined_fact_can_be_revisited_by_name_or_on_request(tmp_path: Path) -> None:
    _three(tmp_path)
    confirm([], open_tty=_Asked("n\nn\nn\n"))
    assert [f.declined_on is not None for f in load_facts().values()] == [True, True, True]

    by_name = _Asked("y\n")
    assert confirm(["b"], open_tty=by_name) == ["b"]
    assert "two" in by_name.shown.getvalue()

    on_request = _Asked("y\nn\n")
    assert confirm([], include_declined=True, open_tty=on_request) == ["a"]

    facts = load_facts()
    assert [fid for fid, f in facts.items() if f.selectable()] == ["a", "b"]
    assert facts["a"].declined_on is None and facts["b"].declined_on is None
    assert facts["c"].declined_on is not None


def test_a_fact_not_yet_answered_about_is_not_declined(tmp_path: Path) -> None:
    """Stopping is not an answer: what came after it is offered next time."""
    _three(tmp_path)
    assert confirm([], open_tty=_Asked("n\nq\n")) == []

    facts = load_facts()
    assert facts["a"].declined_on is not None
    assert facts["b"].declined_on is None and facts["c"].declined_on is None

    later = _Asked("y\ny\n")
    assert confirm([], open_tty=later) == ["b", "c"]


def test_a_draft_can_be_replaced_until_it_is_confirmed(tmp_path: Path) -> None:
    _three(tmp_path)
    confirm([], open_tty=_Asked("y\nn\nq\n"))
    revised = [_draft("a", "one, revised"), _draft("b", "two, revised"), _draft("d", "four")]

    plain = add_from_file(_file(tmp_path, revised, "again.yaml"))
    assert plain[0] == ["d"] and [fid for fid, _ in plain[1]] == ["a", "b"]

    added, refused = add_from_file(_file(tmp_path, revised, "again.yaml"), replace=True)

    assert added == ["b", "d"]
    assert [fid for fid, _ in refused] == ["a"]
    assert "retire" in refused[0][1]
    facts = load_facts()
    assert facts["a"].text == "one" and facts["a"].selectable()
    assert facts["b"].text == "two, revised"
    assert facts["b"].declined_on is None, "new text is a new question"
    assert not facts["b"].selectable()


def test_a_retired_fact_backs_nothing(tmp_path: Path) -> None:
    _three(tmp_path)
    confirm([], open_tty=_Asked("y\ny\ny\n"))
    assert check_bullet("one", [load_facts()["a"]])[0].value == "pass"

    asked = _Asked("y\nn\n")
    assert retire(["a", "b"], open_tty=asked) == ["a"]

    facts = load_facts()
    assert "one" in asked.shown.getvalue() and "two" in asked.shown.getvalue()
    assert facts["a"].retired_on is not None and not facts["a"].selectable()
    assert facts["a"].provenance.trust is Trust.USER_CONFIRMED, "what was confirmed stays on record"
    assert facts["b"].retired_on is None and facts["b"].selectable()
    verdict, findings = check_bullet("one", [facts["a"]])
    assert verdict.value == "fail"
    assert findings[0].kind is FindingKind.UNSELECTABLE_FACT


def test_retiring_needs_the_candidate_and_a_fact_that_exists(tmp_path: Path) -> None:
    _three(tmp_path)

    def no_terminal() -> _Asked:
        raise OSError(6, "No such device or address")

    with pytest.raises(ConfirmError, match="typed by the candidate"):
        retire(["a"], open_tty=no_terminal)
    with pytest.raises(ConfirmError, match="no such fact"):
        retire(["zzz"], open_tty=_Asked("y\n"))
    with pytest.raises(ConfirmError, match="name the fact"):
        retire([], open_tty=_Asked("y\n"))

    assert all(f.retired_on is None for f in load_facts().values())


def test_a_retired_fact_is_not_offered_for_confirmation(tmp_path: Path) -> None:
    _three(tmp_path)
    retire(["b"], open_tty=_Asked("y\n"))

    asked = _Asked("y\ny\n")
    assert confirm([], open_tty=asked) == ["a", "c"]

    assert "two" not in asked.shown.getvalue()
    with pytest.raises(ConfirmError, match="retired"):
        confirm(["b"], open_tty=_Asked("y\n"))


GPA = _draft(
    "gpa",
    "GPA: 3.9/4.0",
    parent="school",
    numbers={"gpa": {"value": 3.9}, "scale": {"value": 4.0}},
)
SCHOOL: dict[str, object] = {
    "id": "school",
    "kind": "education",
    "text": "B.S. at Example University",
    "organization": "Example University",
    "title": "B.S.",
    "provenance": {"source": "import:resume.pdf", "trust": "import_unconfirmed"},
}


def test_a_fact_is_checked_against_its_own_text_before_anyone_confirms_it(
    tmp_path: Path,
) -> None:
    """The text names "GPA" and the entities do not list it, so a bullet
    quoting this fact word for word would fail. Better known while it is a
    draft than after it is confirmed."""
    add_from_file(_file(tmp_path, [SCHOOL, GPA]))

    problems = check_facts([])

    assert list(problems) == ["gpa"]
    assert "'GPA'" in problems["gpa"][0] and "entities" in problems["gpa"][0]
    assert not any(f.selectable() for f in load_facts().values()), "checking confirms nothing"

    fixed = {**GPA, "entities": ["GPA"]}
    add_from_file(_file(tmp_path, [fixed], "fixed.yaml"), replace=True)
    assert check_facts([]) == {}


def test_a_fact_that_is_never_a_bullet_is_not_checked_as_one(tmp_path: Path) -> None:
    """Each of these names something its entities do not list, and none is
    text a bullet quotes: the same words in a detail fact are found."""
    contact = {**_draft("contact", "Dana Vex, GPA Consulting LLC"), "kind": "links"}
    skill = {**_draft("skill", "AWS, GCP and SQL"), "kind": "skill"}
    role = {**_draft("role", "Analyst at Harbor Lab in Boston, MA"), "kind": "experience"}
    detail = _draft("detail", "Analyst at Harbor Lab in Boston, MA")
    add_from_file(_file(tmp_path, [contact, skill, role, SCHOOL, detail]))

    assert list(check_facts([])) == ["detail"]
    assert check_facts(["contact", "skill", "role", "school"]) == {}
    with pytest.raises(FactsError, match="no such fact"):
        check_facts(["zzz"])


def test_the_commands_report_what_the_functions_found(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["facts", "add", str(_file(tmp_path, [SCHOOL, GPA]))]) == 0
    added = capsys.readouterr()
    assert "added 2 fact(s)" in added.out
    assert "gpa" in added.err and "'GPA'" in added.err and "--replace" in added.err

    assert main(["facts", "check"]) == 1
    assert "'GPA'" in capsys.readouterr().out
    assert main(["facts", "check", "school"]) == 0
    assert main(["facts", "check", "zzz"]) == 3
    assert "no such fact" in capsys.readouterr().err

    fixed = _file(tmp_path, [{**GPA, "entities": ["GPA"]}], "fixed.yaml")
    assert main(["facts", "add", "--replace", str(fixed)]) == 0
    assert capsys.readouterr().err == ""
    assert main(["facts", "check"]) == 0

    assert main(["facts", "retire", "gpa"]) == 3, "no terminal under test"
    capsys.readouterr()
    assert main(["facts", "list"]) == 0
    listed = capsys.readouterr().out
    assert listed.count("UNCONFIRMED") == 2


def test_the_list_says_what_state_each_fact_is_in(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _three(tmp_path)
    confirm([], open_tty=_Asked("y\nn\ny\n"))
    retire(["c"], open_tty=_Asked("y\n"))

    assert main(["facts", "list"]) == 0

    marks = [line.split()[0] for line in capsys.readouterr().out.splitlines()]
    assert marks == ["confirmed", "declined", "retired"]


def test_the_candidate_is_shown_the_names_a_fact_lists(tmp_path: Path) -> None:
    """A bullet may name a tool only if a cited fact lists it, so the list is
    part of what the candidate is asked to call true."""
    listed = _draft("hash", "Recorded each file with its SHA-256 hash", entities=["SHA-256"])
    add_from_file(_file(tmp_path, [listed, _draft("plain", "Wrote the runbook")]))
    asked = _Asked("n\nn\n")

    confirm([], open_tty=asked)

    shown = asked.shown.getvalue()
    assert shown.count("names as used:") == 1
    assert "names as used: SHA-256" in shown


def test_unconfirmed_means_still_waiting_for_an_answer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A declined or retired fact has had its answer. Listing it as unconfirmed
    would leave a session waiting for the list to empty, which it never would."""
    drafts = [_draft(fid, f"text {fid}") for fid in ("a", "b", "c", "d")]
    add_from_file(_file(tmp_path, drafts))
    confirm(["a", "b"], open_tty=_Asked("y\nn\n"))
    retire(["c"], open_tty=_Asked("y\n"))

    assert main(["facts", "list", "--unconfirmed"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert [line.split()[:2] for line in lines] == [["UNCONFIRMED", "d"]]
