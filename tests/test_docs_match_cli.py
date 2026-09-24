"""Every `callback …` command the documentation names must exist.

Prose describing a command that was renamed, or never built, is the fastest
way to lose a reader's trust in the parts that are real -- and it has happened
here: `callback export` was documented before it existed. This test reads the
command names out of the documents themselves, so a rename cannot pass by
being spelled consistently in one file.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import cast

from core.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent
SKILL_REFERENCES = sorted(
    str(p.relative_to(ROOT)) for p in (ROOT / "skills/tailor/references").glob("*.md")
)
DOCS = (
    "README.md",
    "docs/using-callback.md",
    "docs/limitations.md",
    "skills/tailor/SKILL.md",
    *SKILL_REFERENCES,
)
MENTION = re.compile(r"`(?:uv run --project <repo root> )?callback ([a-z-]+)(?: ([a-z-]+))?")


def test_the_skill_reference_files_are_scanned() -> None:
    """The skill loads these on demand; a command named only there must still exist."""
    assert len(SKILL_REFERENCES) >= 5, SKILL_REFERENCES


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:  # type: ignore[reportPrivateUsage]
        if isinstance(action, argparse._SubParsersAction):  # type: ignore[reportPrivateUsage]
            subparsers = cast("argparse._SubParsersAction[argparse.ArgumentParser]", action)  # type: ignore[reportPrivateUsage]
            return dict(subparsers.choices)
    return {}


def _mentions() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for name in DOCS:
        for match in MENTION.finditer((ROOT / name).read_text(encoding="utf-8")):
            found.add((match.group(1), match.group(2) or ""))
    return found


def test_the_documents_name_commands_that_exist() -> None:
    """Mutation: document a `callback publish` command -- this test fails."""
    commands = _subcommands(build_parser())
    mentions = _mentions()
    assert len(mentions) > 20, "the command mentions are no longer being found in the docs"
    unknown = sorted({c for c, _ in mentions} - set(commands))
    assert unknown == [], f"documented but not a command: {unknown}"


def test_the_documents_name_subcommands_that_exist() -> None:
    commands = _subcommands(build_parser())
    wrong: list[str] = []
    for command, second in sorted(_mentions()):
        children = _subcommands(commands[command])
        # A second word is a subcommand only where the command has any; on the
        # others it is an argument, e.g. `callback letter letter.yaml`.
        if children and second and second not in children:
            wrong.append(f"{command} {second}")
    assert wrong == [], f"documented but not a subcommand: {wrong}"


def test_every_command_is_documented_somewhere() -> None:
    """The union of the documents must cover the CLI: a command nobody can
    find is as good as unbuilt. Mutation: add a command to the parser without
    documenting it -- this fails."""
    documented = {c for c, _ in _mentions()}
    missing = sorted(set(_subcommands(build_parser())) - documented)
    assert missing == [], f"a command no document names: {missing}"
