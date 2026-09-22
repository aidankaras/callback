"""The guard runs as its own process, exactly as Claude Code invokes a hook."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / "hooks" / "write_guard.py"


def run_guard(
    payload: Mapping[str, object] | str, home: Path, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    if isinstance(payload, dict) and cwd is not None:
        payload = {**payload, "cwd": str(cwd)}
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        env={"CALLBACK_HOME": str(home), "PATH": "/usr/bin:/bin", "HOME": str(home.parent)},
        check=False,
    )


def denied(result: subprocess.CompletedProcess[str]) -> bool:
    """Exit 2 *and* the guard's own message. Python also exits 2 when the
    script does not exist, so exit status alone would count a missing or
    crashing guard as a denial."""
    return result.returncode == 2 and "blocked by callback write guard" in result.stderr


def _write(path: str) -> dict[str, object]:
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": "x"}}


def _bash(command: str) -> dict[str, object]:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "cb"
    (home / "outputs" / "job" / "draft").mkdir(parents=True)
    (home / "outputs" / "job" / "final").mkdir(parents=True)
    (home / "inbox").mkdir()
    (home / "facts.yaml").write_text("{}\n")
    return home


def test_ordinary_writes_are_allowed(home: Path, tmp_path: Path) -> None:
    for path in (
        home / "outputs" / "job" / "draft" / "resume.yaml",
        home / "inbox" / "acme.md",
        tmp_path / "project" / "notes.md",
    ):
        assert run_guard(_write(str(path)), home).returncode == 0, path


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
def test_the_fact_store_is_not_writable_by_a_tool(home: Path, tool: str) -> None:
    payload = {"tool_name": tool, "tool_input": {"file_path": str(home / "facts.yaml")}}
    result = run_guard(payload, home)
    assert denied(result)
    assert "callback facts" in result.stderr


def test_traversal_and_symlinks_resolve_before_comparison(home: Path, tmp_path: Path) -> None:
    assert denied(run_guard(_write(str(home / "inbox" / ".." / "facts.yaml")), home))
    link = tmp_path / "innocent.yaml"
    link.symlink_to(home / "facts.yaml")
    assert denied(run_guard(_write(str(link)), home))
    final_link = home / "outputs" / "job" / "draft" / "sealed"
    final_link.symlink_to(home / "outputs" / "job" / "final")
    assert denied(run_guard(_write(str(final_link / "resume.pdf")), home))


def test_a_finalised_document_is_not_writable(home: Path) -> None:
    for sub in ("final", "released"):
        path = home / "outputs" / "job" / sub / "resume.pdf"
        assert denied(run_guard(_write(str(path)), home)), sub


@pytest.mark.parametrize(
    "command",
    [
        "sed -i s/Engineer/Director/ {home}/facts.yaml",
        "echo '- id: x' >> {home}/facts.yaml",
        "cp /tmp/x.pdf {home}/outputs/job/final/resume.pdf",
        "python3 -c \"open('{home}/facts.yaml','w')\"",
        "cd {home} && sed -i s/a/b/ facts.yaml",
        "cd {home}/outputs/job && cp x.pdf final/resume.pdf",
        "sed -i s/a/b/ $CALLBACK_HOME/facts.yaml",
        "sed -i s/a/b/ ${{CALLBACK_HOME}}/facts.yaml",
        "uv run --project /repo callback facts confirm",
    ],
)
def test_bash_forms_that_touch_a_protected_path_are_denied(home: Path, command: str) -> None:
    assert denied(run_guard(_bash(command.format(home=home)), home)), command


def test_a_relative_path_is_resolved_against_the_session_cwd(home: Path) -> None:
    assert denied(run_guard(_bash("sed -i s/a/b/ facts.yaml"), home, cwd=home))


@pytest.mark.parametrize(
    "command",
    [
        "uv run --project /repo callback facts add facts.yaml",
        "uv run --project /repo callback facts list",
        "uv run --project /repo callback build {home}/outputs/job/draft/resume.yaml --job job",
        "uv run pytest -q",
    ],
)
def test_ordinary_commands_are_allowed(home: Path, tmp_path: Path, command: str) -> None:
    """A draft named facts.yaml outside the home is the candidate's own file."""
    result = run_guard(_bash(command.format(home=home)), home, cwd=tmp_path)
    assert result.returncode == 0, (command, result.stderr)


def test_the_guard_protects_itself() -> None:
    home = Path("/nonexistent-callback-home")
    assert denied(run_guard(_write(str(GUARD)), home))
    assert denied(run_guard(_write(str(GUARD.parent / "hooks.json")), home))


@pytest.mark.parametrize("payload", ["not json", "[]", '{"tool_input": {}}'])
def test_an_unreadable_payload_is_denied(home: Path, payload: str) -> None:
    assert denied(run_guard(payload, home))


def test_reading_the_store_through_the_shell_is_refused_with_the_alternative(home: Path) -> None:
    """Reads and writes cannot be told apart reliably in a shell command."""
    result = run_guard(_bash(f"cat {home}/facts.yaml | tee {home}/facts.yaml"), home)
    assert denied(result)
    assert "callback facts list" in result.stderr


def test_the_application_record_is_not_writable_by_a_tool(home: Path) -> None:
    """The daily and re-apply limits read it; editing it would lift them."""
    assert denied(run_guard(_write(str(home / "applications.yaml")), home))
    assert denied(run_guard(_bash(f"echo '[]' > {home}/applications.yaml"), home))


@pytest.mark.parametrize(
    "command",
    [
        "rm ~/.callback/facts.yaml",
        "echo x > ~/.callback/facts.yaml",
        "cp a.pdf ~/.callback/outputs/job/final/resume.pdf",
        "rm $HOME/.callback/facts.yaml",
        'rm "${HOME}/.callback/facts.yaml"',
        "rm -rf ~/.callback/outputs/job",
        "mv ~/.callback/outputs/job ~/.callback/outputs/moved",
        "rm -rf ~/.callback",
        "cd ~/.callback/outputs/job && rm -rf final",
        # Each of these names only inbox/, which holds nothing protected; only
        # tracking the directory change reveals that ../facts.yaml is the store.
        "git status\ncd ~/.callback/inbox\nrm ../facts.yaml",
        "pushd ~/.callback/inbox && rm ../facts.yaml",
        "git -C ~/.callback/inbox checkout -- ../facts.yaml",
        "tar --directory=$HOME/.callback/inbox -xf backup.tar ../facts.yaml",
        # A look-only command, or a cd, still writes through a redirect.
        "ls > ~/.callback/facts.yaml",
        "stat x >>~/.callback/.key",
        "du 2>~/.callback/ledger.log",
        "cd /tmp > ~/.callback/facts.yaml",
    ],
)
def test_the_default_home_and_its_ordinary_spellings_are_guarded(
    tmp_path: Path, command: str
) -> None:
    """With CALLBACK_HOME unset the home is ~/.callback, and `~`, `$HOME`,
    later-line cds, pushd and -C are how an assistant would ordinarily name
    it. Deleting a directory that holds a protected file is a write to it."""
    home = tmp_path / ".callback"
    (home / "outputs" / "job" / "final").mkdir(parents=True)
    (home / "inbox").mkdir()
    (home / "facts.yaml").write_text("{}\n")
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(_bash(command) | {"cwd": str(tmp_path / "repo")}),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        check=False,
    )
    assert denied(result), command


@pytest.mark.parametrize(
    "command",
    [
        'git commit -m "docs: explain callback facts confirm"',
        'grep -rn "callback facts confirm" docs/',
        'git commit -m "validate facts.yaml schema"',
        "ls {home}/outputs/job/final",
    ],
)
def test_mentioning_a_protected_name_is_not_writing_it(home: Path, command: str) -> None:
    result = run_guard(_bash(command.format(home=home)), home, cwd=home)
    assert result.returncode == 0, (command, result.stderr)
