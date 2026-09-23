"""Tests for the callback CLI entry point.

Two invocation paths are exercised deliberately, and neither is redundant with the other:
``python -m core.cli`` runs straight against the source tree, but ``-m`` implicitly prepends
the current directory to ``sys.path``, so it can pass even against an installed copy that
cannot actually resolve ``core`` on its own. The installed ``callback`` console script has no
such crutch: it only works if the environment's installed copy genuinely exposes ``core``,
which is exactly the case that broke silently once in this project's history (a stale build
artifact that predated the ``core`` package's existence left an installed copy with no way
to import it, while the ``-m`` form kept passing throughout).
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def test_cli_reports_version() -> None:
    """`python -m core.cli` resolves against the source tree via the injected cwd."""
    out = subprocess.run(
        [sys.executable, "-m", "core.cli", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip().startswith("callback ")


def test_installed_console_script_reports_version() -> None:
    """The installed `callback` console script resolves and runs on its own.

    Unlike `test_cli_reports_version`, this never runs against the source tree via an
    injected `sys.path` entry: it invokes the entry point that `[project.scripts]` installs,
    the same one a user gets from `uv run callback` or a plain `pip install`. It is the only
    test in this file that would have caught the packaging regression this project actually
    hit once (see the module docstring).
    """
    found = shutil.which("callback")
    executable = Path(found) if found else Path(sys.executable).parent / "callback"
    if not executable.is_file():
        pytest.fail(
            f"could not find an installed 'callback' console script: looked on PATH via "
            f"shutil.which('callback') and at {executable}"
        )
    out = subprocess.run(
        [str(executable), "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip().startswith("callback ")


def test_cli_init_creates_private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`callback init` must create the data home reported by `core.paths.callback_home`."""
    home = tmp_path / "cb"
    monkeypatch.setenv("CALLBACK_HOME", str(home))
    out = subprocess.run(
        [sys.executable, "-m", "core.cli", "init"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert home.is_dir()
    assert str(home) in out.stdout


def test_cli_delete_all_removes_everything_under_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`callback delete-all` must remove every file under the home and report each one."""
    home = tmp_path / "cb"
    monkeypatch.setenv("CALLBACK_HOME", str(home))
    nested_dir = home / "outputs" / "job" / "draft"
    nested_dir.mkdir(parents=True)
    nested_file = nested_dir / "resume.pdf"
    nested_file.write_text("draft pdf")
    top_file = home / "notes.yaml"
    top_file.write_text("notes")

    out = subprocess.run(
        [sys.executable, "-m", "core.cli", "delete-all"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert home.is_dir()
    assert list(home.iterdir()) == []
    expected_removed = {
        str(nested_file),
        str(nested_dir),
        str(nested_dir.parent),  # outputs/job
        str(nested_dir.parent.parent),  # outputs
        str(top_file),
    }
    printed = set(out.stdout.splitlines())
    assert printed == expected_removed


def test_fit_reports_coverage_and_exits_on_the_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: exit 0 regardless of the verdict -- a knockout reads as fine."""
    from core.cli import main
    from core.paths import ensure_home
    from core.profile.models import Fact, FactKind, Provenance, Trust
    from core.profile.store import save_facts
    from core.triage import Profile, save_profile

    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    home = ensure_home()
    save_profile(Profile(work_authorization="F-1 student, will need sponsorship"))
    save_facts(
        {
            "py": Fact(
                id="py",
                kind=FactKind.SKILL,
                text="Wrote Python for the loader",
                entities=["Python"],
                provenance=Provenance(source="x", trust=Trust.USER_CONFIRMED),
            )
        }
    )
    (home / "inbox" / "acme.md").write_text(
        "# ML Intern - Acme\n\nWe use Python and Kubernetes. We cannot sponsor visas.\n",
        encoding="utf-8",
    )
    assert main(["fit", "acme"]) == 2
    out = capsys.readouterr().out
    # Three technologies are named: Python, Kubernetes, and "ML" in the title,
    # which the alias table knows as Machine Learning.
    assert "KNOCKOUT" in out and "33% of what it names" in out
    assert "have     Python  (py)" in out
    assert "missing  Kubernetes" in out and "missing  Machine Learning" in out


def test_a_damaged_fact_store_is_reported_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store that fails to load exits 3 with one line, not a traceback that echoes every fact."""
    home = tmp_path / "cb"
    home.mkdir(mode=0o700)
    (home / "facts.yaml").write_text("- id: secret-fact\n  text: Private detail\n")
    monkeypatch.setenv("CALLBACK_HOME", str(home))
    out = subprocess.run(
        [sys.executable, "-m", "core.cli", "facts", "list"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 3
    assert "Traceback" not in out.stderr
    assert "callback: " in out.stderr and "must contain a mapping" in out.stderr
    assert "Private detail" not in out.stderr
