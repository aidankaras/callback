"""Keeping a tailored document to start the next similar one from.

Applying at any volume means writing the same resume repeatedly with small
differences, and starting each one from nothing invites a different mistake
every time. A template is simply a document that already passed its gates,
saved under a name, and copied into a new job's draft to be edited down.

It is a starting point and nothing more: the copy is a draft like any other,
every gate runs against it again, and its bullets still cite fact ids that
must still exist and still be confirmed. A template cannot smuggle anything
past a check, because nothing about being a template is trusted --
`callback build` does not know the draft came from one.

Only a document whose own build passed (or whose warnings the candidate
accepted) can be saved, so a template is never a shape that failed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from core.paths import private_dir, resolve_inside, write_private

TEMPLATE_DIR = "templates"
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


class TemplateError(RuntimeError):
    """A template that cannot be saved or used; nothing was written."""


@dataclass(frozen=True)
class Template:
    name: str
    path: Path
    bytes_: int


def _named(name: str) -> str:
    if not NAME.fullmatch(name):
        raise TemplateError(f"template name {name!r} must be lowercase letters, digits and hyphens")
    return name


def save(job_id: str, name: str) -> Path:
    """Save the job's gated draft document as the template `name`."""
    draft = resolve_inside(f"outputs/{_named(job_id)}/draft")
    try:
        report = json.loads((draft / "report.json").read_text(encoding="utf-8"))
        document = (draft / "resume.yaml").read_bytes()
    except (OSError, json.JSONDecodeError) as exc:
        raise TemplateError(f"no gated draft for {job_id!r}: {exc}") from exc
    if report.get("verdict") not in {"pass", "warn"}:
        raise TemplateError(
            f"the draft for {job_id!r} did not pass its gates, so it is not a shape to reuse"
        )
    out = private_dir(TEMPLATE_DIR) / f"{_named(name)}.yaml"
    write_private(out, document.decode("utf-8"))
    return out


def listing() -> list[Template]:
    """Every saved template, by name."""
    directory = resolve_inside(TEMPLATE_DIR)
    if not directory.is_dir():
        return []
    return [
        Template(path.stem, path, path.stat().st_size) for path in sorted(directory.glob("*.yaml"))
    ]


def use(name: str, job_id: str, *, overwrite: bool = False) -> Path:
    """Copy the template into the job's draft, ready to edit.

    The copy is a draft, not a result: `callback build` must still be run, and
    every gate judges it as though it had been written by hand.
    """
    source = resolve_inside(f"{TEMPLATE_DIR}/{_named(name)}.yaml")
    if not source.is_file():
        raise TemplateError(f"no template {name!r}: run `callback template list`")
    target = private_dir(f"outputs/{_named(job_id)}/draft") / "resume.yaml"
    if target.exists() and not overwrite:
        raise TemplateError(
            f"{job_id!r} already has a draft document; pass --overwrite to replace it"
        )
    write_private(target, source.read_text(encoding="utf-8"))
    return target
