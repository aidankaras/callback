from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from core.factscmd import ConfirmError, add_from_file, confirm
from core.paths import ensure_home
from core.profile.models import Trust
from core.profile.store import load_facts


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    return ensure_home()


def _facts_file(tmp_path: Path, facts: list[dict[str, object]]) -> Path:
    path = tmp_path / "facts.yaml"
    path.write_text(yaml.safe_dump(facts), encoding="utf-8")
    return path


def _imported(fid: str, text: str) -> dict[str, object]:
    return {
        "id": fid,
        "kind": "experience_detail",
        "text": text,
        "provenance": {"source": "import:resume.pdf", "trust": "import_unconfirmed"},
    }


def test_adding_facts_records_every_rejection_and_keeps_the_rest(tmp_path: Path) -> None:
    bad_trust = _imported("bad", "x") | {
        "provenance": {"source": "import:resume.pdf", "trust": "user_confirmed"}
    }
    added, rejected = add_from_file(
        _facts_file(tmp_path, [_imported("a", "Built the pipeline"), bad_trust, {"id": "c"}])
    )
    assert added == ["a"]
    assert [fid for fid, _ in rejected] == ["bad", "c"]
    assert "import_unconfirmed" in rejected[0][1]
    assert set(load_facts()) == {"a"}


def test_adding_a_duplicate_id_is_rejected_not_overwritten(tmp_path: Path) -> None:
    add_from_file(_facts_file(tmp_path, [_imported("a", "first")]))
    added, rejected = add_from_file(_facts_file(tmp_path, [_imported("a", "second")]))
    assert (added, [fid for fid, _ in rejected]) == ([], ["a"])
    assert load_facts()["a"].text == "first"


def test_confirmation_without_a_terminal_changes_nothing(tmp_path: Path, home: Path) -> None:
    add_from_file(_facts_file(tmp_path, [_imported("a", "Built the pipeline")]))
    store = home / "facts.yaml"
    before = store.read_bytes()

    def no_tty() -> io.StringIO:
        raise OSError(6, "No such device or address")

    with pytest.raises(ConfirmError, match="typed by the candidate"):
        confirm([], open_tty=no_tty)
    assert store.read_bytes() == before


def test_confirmation_flips_exactly_the_facts_answered_yes(tmp_path: Path) -> None:
    add_from_file(
        _facts_file(
            tmp_path, [_imported("a", "one"), _imported("b", "two"), _imported("c", "three")]
        )
    )
    answers = io.StringIO("y\nn\ny\n")
    shown = io.StringIO()

    class _Tty(io.TextIOBase):
        def readline(self, size: int = -1, /) -> str:
            return answers.readline()

        def write(self, s: str, /) -> int:
            return shown.write(s)

    confirmed = confirm([], open_tty=_Tty)
    facts = load_facts()
    assert confirmed == ["a", "c"]
    assert [fid for fid, f in sorted(facts.items()) if f.selectable()] == ["a", "c"]
    assert facts["a"].provenance.source == "user:confirmed:import:resume.pdf"
    assert facts["b"].provenance.trust is Trust.IMPORT_UNCONFIRMED
    assert "three" in shown.getvalue()


def test_quitting_confirmation_keeps_the_answers_already_given(tmp_path: Path) -> None:
    add_from_file(
        _facts_file(
            tmp_path, [_imported("a", "one"), _imported("b", "two"), _imported("c", "three")]
        )
    )
    # A "y" after the "q": if quitting only skipped one fact, "c" would be confirmed.
    answers = io.StringIO("y\nq\ny\n")

    class _Tty(io.TextIOBase):
        def readline(self, size: int = -1, /) -> str:
            return answers.readline()

        def write(self, s: str, /) -> int:
            return len(s)

    assert confirm([], open_tty=_Tty) == ["a"]
    facts = load_facts()
    assert [fid for fid, f in sorted(facts.items()) if f.selectable()] == ["a"]


def test_a_web_sourced_fact_can_never_be_confirmed(tmp_path: Path) -> None:
    web = _imported("w", "Company raised a Series B") | {
        "provenance": {"source": "web:example.com", "trust": "web"}
    }
    add_from_file(_facts_file(tmp_path, [web]))

    class _Tty(io.TextIOBase):
        def readline(self, size: int = -1, /) -> str:
            return "y\n"

        def write(self, s: str, /) -> int:
            return len(s)

    with pytest.raises(ConfirmError, match="web"):
        confirm(["w"], open_tty=_Tty)
    assert not load_facts()["w"].selectable()


# --- the command line ------------------------------------------------------------


def test_facts_add_exits_nonzero_when_anything_was_refused(tmp_path: Path) -> None:
    from core.cli import main

    good = _facts_file(tmp_path, [_imported("a", "one")])
    assert main(["facts", "add", str(good)]) == 0
    assert main(["facts", "add", str(good)]) == 1


def test_facts_confirm_without_a_terminal_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Patched rather than left to the environment: on a developer's machine
    `/dev/tty` exists, and an unpatched test would wait for input."""
    from core.cli import main

    def no_tty() -> io.StringIO:
        # A distinctive message: without it, this test cannot tell the patch
        # taking effect from a machine that genuinely has no terminal.
        raise OSError(6, "patched-no-terminal")

    monkeypatch.setattr("core.factscmd._open_terminal", no_tty)
    add_from_file(_facts_file(tmp_path, [_imported("a", "one")]))
    assert main(["facts", "confirm"]) == 3
    assert "patched-no-terminal" in capsys.readouterr().err
    assert not load_facts()["a"].selectable()


@pytest.mark.slow
def test_import_writes_private_text_and_exits_4_on_hidden_content(
    tmp_path: Path, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import stat

    from core.cli import main
    from core.render.typst import FONT_DIR
    from tests.fixtures.make_hidden import WHITE, build

    pdf = tmp_path / "Old Resume.pdf"
    build(WHITE, pdf, FONT_DIR)
    assert main(["import", str(pdf)]) == 4
    out = capsys.readouterr().out
    assert out.startswith("HIDDEN CONTENT FOUND")
    text = home / "inbox" / "old-resume.txt"
    assert stat.S_IMODE(text.stat().st_mode) == 0o600
    assert "kubernetes" in text.read_text()


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("script") is None, reason="needs util-linux `script` for a pty")
def test_confirm_works_on_a_real_terminal(tmp_path: Path, home: Path) -> None:
    """No injected terminal: `script` runs the real command on a pseudo-terminal
    and feeds it "y". Every other confirm test injects `open_tty`, which is how
    a terminal that could not be opened at all once went unnoticed."""
    add_from_file(_facts_file(tmp_path, [_imported("a", "Built the pipeline")]))
    env = {**os.environ, "CALLBACK_HOME": str(home)}
    command = f"{sys.executable} -m core.cli facts confirm"
    done = subprocess.run(
        ["script", "-qec", command, "/dev/null"],
        input="y\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert "confirmed 1 fact(s)" in done.stdout, (done.stdout, done.stderr)
    assert load_facts()["a"].selectable()


def test_an_unexpected_terminal_error_is_raised_not_reported_as_no_terminal(
    tmp_path: Path,
) -> None:
    add_from_file(_facts_file(tmp_path, [_imported("a", "one")]))

    def broken() -> io.StringIO:
        raise io.UnsupportedOperation("File or stream is not seekable.")

    with pytest.raises(io.UnsupportedOperation):
        confirm([], open_tty=broken)


def test_a_fact_added_while_confirming_is_not_lost(tmp_path: Path) -> None:
    add_from_file(_facts_file(tmp_path, [_imported("a", "one")]))
    later = _facts_file(tmp_path / "..", [_imported("meanwhile", "added from another shell")])

    class _Tty(io.TextIOBase):
        def readline(self, size: int = -1, /) -> str:
            add_from_file(later)
            return "y\n"

        def write(self, s: str, /) -> int:
            return len(s)

    assert confirm([], open_tty=_Tty) == ["a"]
    assert set(load_facts()) == {"a", "meanwhile"}


def test_an_unrecognised_answer_is_asked_again_and_end_of_input_stops(tmp_path: Path) -> None:
    """Mutation: treat an unrecognised answer as "no" -- "a" is left unconfirmed."""
    add_from_file(_facts_file(tmp_path, [_imported("a", "one"), _imported("b", "two")]))
    answers = io.StringIO("yes please\ny\n")  # then end of input

    class _Tty(io.TextIOBase):
        def readline(self, size: int = -1, /) -> str:
            return answers.readline()

        def write(self, s: str, /) -> int:
            return len(s)

    assert confirm([], open_tty=_Tty) == ["a"]
    assert not load_facts()["b"].selectable()


@pytest.mark.parametrize(
    "source", ["assistant:skills", "onboarding_answer", "user:confirmed:import:x.pdf"]
)
def test_a_fact_cannot_arrive_already_confirmed(tmp_path: Path, source: str) -> None:
    """Only `confirm` makes a fact usable. A file declaring `user_confirmed`
    would let whoever wrote it skip the candidate entirely. Mutation: drop
    the refusal -- the fact is stored confirmed and this test fails."""
    body = _imported("self", "Holds a patent") | {
        "provenance": {"source": source, "trust": "user_confirmed"}
    }
    added, rejected = add_from_file(_facts_file(tmp_path, [body]))
    assert added == []
    assert [fid for fid, _ in rejected] == ["self"]
    assert "facts confirm" in rejected[0][1]
    assert "self" not in load_facts()


def test_malformed_yaml_is_a_named_error_not_a_crash(tmp_path: Path) -> None:
    from core.cli import main

    bad = tmp_path / "facts.yaml"
    bad.write_text("not: [valid", encoding="utf-8")
    assert main(["facts", "add", str(bad)]) == 3
