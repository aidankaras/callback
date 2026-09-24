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
        # A boolean uv flag must not swallow the word that names the command,
        # and `uv run --directory` changes directory exactly as `cd` does.
        "uv run --locked callback facts confirm",
        "uv run --locked --project /repo callback facts confirm",
        "uv run --no-project python -m core.cli facts confirm",
        "uv run --directory {home}/inbox rm ../facts.yaml",
        "uv run --frozen --directory {home}/outputs/job cp /tmp/x.pdf final/resume.pdf",
        # A "#" must not hide the rest of the command from the guard.
        "# tidy up\nrm {home}/facts.yaml",
        "echo hi # note\nrm {home}/facts.yaml",
        "# a comment\ncallback facts confirm",
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


@pytest.mark.parametrize(
    "command",
    [
        "rm {home}/facts.*",
        "rm -rf {home}/outputs/*/final",
        "cp /tmp/x.pdf {home}/*.yaml",
        # The glob inside a quoted argument is a path too.
        "python3 -c \"import glob; [open(p,'w') for p in glob.glob('{home}/facts.*')]\"",
    ],
)
def test_a_wildcard_inside_the_data_home_is_refused(home: Path, command: str) -> None:
    """A glob cannot be resolved to one file here, and expanding it would see
    only what exists at this moment. Mutation: leave `*` out of the candidate
    pattern -- `facts.*` is read as `facts.` and slips past."""
    assert denied(run_guard(_bash(command.format(home=home)), home)), command


def test_a_wildcard_outside_the_data_home_is_allowed(home: Path, tmp_path: Path) -> None:
    result = run_guard(_bash(f"rm {tmp_path}/scratch/*.pdf"), home)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "command",
    [
        'script -qec "uv run callback facts confirm probe" /dev/null',
        "timeout 30 script -qc 'callback facts confirm' /dev/null",
        'bash -c "callback facts confirm"',
        "python3 -c \"import pty; pty.spawn(['callback', 'facts', 'confirm'])\"",
        'tmux send-keys "callback facts confirm" Enter',
        'unbuffer sh -c "uv run --project /repo callback facts confirm"',
        '/bin/bash -c "callback facts confirm"',
        '/usr/bin/env script -qc "callback facts confirm" /dev/null',
        './script -qc "callback facts confirm" /dev/null',
        'watch "callback facts confirm"',
        'flock /tmp/l bash -c "callback facts confirm"',
        'find . -exec bash -c "callback facts confirm" \\;',
        "awk 'BEGIN{system(\"callback facts confirm\")}'",
        'uv run --locked bash -c "callback facts confirm"',
        "C='callback facts confirm'; script -qc \"$C\" /dev/null",
        "C='callback facts confirm' script -qc '$C' /dev/null",
        "GIT_EDITOR='script -qc \"callback facts confirm\" /dev/null' git commit",
        "git -c core.editor='script -qc \"callback facts confirm\" /dev/null' commit",
        "git -c alias.x='!callback facts confirm' x",
    ],
)
def test_confirm_run_through_a_pseudo_terminal_wrapper_is_denied(home: Path, command: str) -> None:
    """`script` gives a child a terminal, so `/dev/tty` opens and confirm succeeds without the user.

    Each command hides `facts confirm` inside one quoted word, where the
    command-position check cannot see it.
    """
    result = run_guard(_bash(command), home, cwd=home)
    assert denied(result), (command, result.stderr)
    assert "not run through another command" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        'rg -n "facts confirm" docs/',
        'git log --grep "facts confirm"',
        'git commit -m "docs: explain facts confirm"',
    ],
)
def test_a_search_or_commit_may_mention_confirm(home: Path, command: str) -> None:
    result = run_guard(_bash(command), home, cwd=home)
    assert result.returncode == 0, (command, result.stderr)


def test_a_mention_passed_through_a_runner_is_refused_by_design(home: Path) -> None:
    """The guard does not model what a runner runs, so it refuses the mention too."""
    result = run_guard(_bash('timeout 10 grep -rn "facts confirm" docs/'), home, cwd=home)
    assert denied(result)


@pytest.mark.parametrize(
    "command",
    [
        "callback notes activate n1",
        "uv run --locked callback notes activate n1",
        "uv run --no-project python -m core.cli notes activate n1",
        'script -qec "callback notes activate n1" /dev/null',
        '/bin/bash -c "callback notes activate n1"',
        'watch -n 1 "callback notes activate n1"',
        "C='callback notes activate n1'; script -qc \"$C\" /dev/null",
        "git -c alias.x='!callback notes activate n1' x",
    ],
)
def test_only_the_candidate_activates_a_note(home: Path, command: str) -> None:
    """Refused wherever `facts confirm` is. Mutation: drop `notes activate`
    from `_CANDIDATE_TEXT` -- every case but the command-position ones fails."""
    result = run_guard(_bash(command), home, cwd=home)
    assert denied(result), (command, result.stderr)
    assert "notes activate" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "sed -i s/proposed/active/ {home}/notes.yaml",
        "cd {home} && python3 -c \"open('notes.yaml','a').write('x')\"",
    ],
)
def test_the_notebook_is_not_edited_by_a_tool(home: Path, command: str) -> None:
    (home / "notes.yaml").write_text("notes: []\n")
    assert denied(run_guard(_bash(command.format(home=home)), home, cwd=home)), command


def test_a_write_to_the_notebook_is_denied(home: Path) -> None:
    (home / "notes.yaml").write_text("notes: []\n")
    result = run_guard(_write(str(home / "notes.yaml")), home)
    assert denied(result)
    assert "only the candidate activates" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "uv run callback notes retire n1",
        "uv run callback notes list --all",
        'grep -rn "notes activate" docs/',
        'git commit -m "docs: explain notes activate"',
    ],
)
def test_retiring_listing_and_mentioning_notes_is_allowed(home: Path, command: str) -> None:
    result = run_guard(_bash(command), home, cwd=home)
    assert result.returncode == 0, (command, result.stderr)


@pytest.mark.parametrize(
    "command",
    [
        "uv run python -c \"from core.notes import activate; activate('n1', open_tty=T)\"",
        "uv run python -c 'import core.notes as m; m._save(m.load())'",
        'uv run python -c "from core.factscmd import confirm"',
        'uv run python -c "from core.profile import store; store.save_facts({})"',
        'uv run python -c "from core import cli, notes"',
    ],
)
def test_code_that_decides_for_the_candidate_is_not_run_by_a_tool(home: Path, command: str) -> None:
    """`activate` accepts any terminal it is handed, and the stores save whatever
    they are given: importing them is running `notes activate` or `facts confirm`
    without the words. Mutation: drop `_CANDIDATE_CODE`'s check -- all fail."""
    result = run_guard(_bash(command), home, cwd=home)
    assert denied(result), (command, result.stderr)


@pytest.mark.parametrize(
    "command",
    [".venv/bin/callback notes activate n1", "/usr/local/bin/callback facts confirm f1"],
)
def test_a_candidate_only_command_is_refused_by_any_path(home: Path, command: str) -> None:
    assert denied(run_guard(_bash(command), home, cwd=home)), command


@pytest.mark.parametrize(
    "command",
    [
        "uv run pytest -q tests/test_notes.py",
        "uv run ruff check core/notes.py",
        'grep -rn "from core.notes import" core',
        'uv run python -c "from core.insights import render"',
    ],
)
def test_naming_the_files_or_other_modules_is_allowed(home: Path, command: str) -> None:
    result = run_guard(_bash(command), home, cwd=home)
    assert result.returncode == 0, (command, result.stderr)


@pytest.mark.parametrize(
    "command",
    [
        "ls `rm {home}/facts.yaml`",
        "echo `rm {home}/facts.yaml`",
        "echo hi > {home}/facts.yaml",
    ],
)
def test_a_look_only_command_that_runs_or_writes_something_is_refused(
    home: Path, command: str
) -> None:
    """A backtick in an argument runs a command, and a redirect writes.
    Mutation: drop the backtick check -- the first two are allowed."""
    assert denied(run_guard(_bash(command.format(home=home)), home, cwd=home)), command


@pytest.mark.parametrize(
    "command",
    [
        "ls ~/.callback 2>&1 | head",
        "ls {home} 2>/dev/null",
        "stat {home}/facts.yaml 2>&1",
    ],
)
def test_looking_with_a_harmless_redirect_is_allowed(home: Path, command: str) -> None:
    """`2>&1` duplicates a descriptor and a redirect into /dev/null writes
    nothing, so neither ends the look-only exemption. Mutation: count a
    redirect into /dev/null as a write -- the /dev/null case is refused."""
    env_home = home.parent
    command = command.format(home=home).replace("~/.callback", str(home))
    result = run_guard(_bash(command), home, cwd=env_home)
    assert result.returncode == 0, (command, result.stderr)


@pytest.mark.parametrize(
    "command",
    [
        "ls x > {home}/facts.yaml",
        "ls x >> {home}/facts.yaml",
        "echo x &> {home}/facts.yaml",
        "echo x >& {home}/facts.yaml",
        "rm {home}/facts.yaml 2>&1",
    ],
)
def test_a_redirect_into_a_protected_file_is_still_refused(home: Path, command: str) -> None:
    assert denied(run_guard(_bash(command.format(home=home)), home, cwd=home)), command


def test_changing_into_the_data_home_with_a_descriptor_duplication_is_allowed(
    home: Path,
) -> None:
    """`cd` is exempt unless the segment redirects into a file, and `2>&1`
    duplicates a descriptor rather than naming one. Mutation: count `>&` plus
    a digit as a file -- this is refused."""
    result = run_guard(_bash(f"cd {home} 2>&1"), home, cwd=home.parent)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "command",
    [
        'echo "$(rm {home}/facts.yaml)"',
        "printf '%s' \"$(rm {home}/facts.yaml)\"",
        "printf -v v '%s' \"$(rm {home}/facts.yaml)\"",
        'echo x <<< "$(rm {home}/facts.yaml)"',
        'echo "$(mv {home}/facts.yaml /tmp/zz)"',
        'echo "$(rm -r {home}/outputs/job/final)"',
        'echo "$(: > {home}/notes.yaml)"',
        'cat <<EOF\necho "$(rm {home}/facts.yaml)"\nEOF',
        "ls;>{home}/facts.yaml",
        "echo x;>{home}/facts.yaml",
        "echo x ;> {home}/facts.yaml",
        "echo x;>>{home}/facts.yaml",
        "printf x;>{home}/notes.yaml",
        "echo >(tee {home}/facts.yaml)",
        "echo x > >(tee {home}/facts.yaml)",
        "ls >(rm {home}/facts.yaml)",
        "echo <(rm {home}/facts.yaml)",
        "ls <(rm {home}/facts.yaml)",
        'ls "$(rm {home}/facts.yaml)"',
        'stat "$(rm {home}/facts.yaml)"',
        "echo {home}/facts.yaml | xargs rm",
        "printf '%s\\0' {home}/facts.yaml | xargs -0 truncate -s0",
        "printf -v p '%s/facts.yaml' {home}; : > \"$p\"",
        'A="$(rm {home}/facts.yaml)"',
        "echo {home} | xargs rm -rf",
    ],
)
def test_a_command_that_looks_but_carries_a_write_is_refused(home: Path, command: str) -> None:
    """Every one of these deletes, moves or truncates a protected file when run.
    A command that only looks is exempt only when nothing in it can run
    another command or write: no substitution (`$(`, backtick, `<(`, `>(`), no
    redirect into a file, and no argument handed down a pipe. Assignments are
    checked for paths like any other word."""
    (home / "notes.yaml").write_text("notes: []\n")
    result = run_guard(_bash(command.format(home=home)), home, cwd=home.parent)
    assert denied(result), (command, result.stderr)
