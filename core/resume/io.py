"""Reading and writing a `ResumeDoc` as YAML, the form a person or a playbook edits."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from core.resume.models import ResumeDoc


class DocumentError(ValueError):
    """A resume document file that cannot be read as a `ResumeDoc`."""


def load_doc(path: Path) -> ResumeDoc:
    """Parse and validate the resume document at `path`."""
    return parse_doc(path.read_bytes(), origin=path)


def parse_doc(data: bytes, *, origin: Path) -> ResumeDoc:
    """Parse and validate a resume document from `data`; `origin` names it in errors.

    Taking bytes lets a caller hash exactly what it parsed: reading the file
    once to hash it and again to parse it could describe two different files.
    """
    try:
        parsed: object = yaml.safe_load(data.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise DocumentError(f"{origin} is not valid YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise DocumentError(
            f"{origin} must contain a mapping with `identity` and `sections`, "
            f"found {type(parsed).__name__}"
        )
    try:
        return ResumeDoc.model_validate(parsed)
    except ValidationError as exc:
        raise DocumentError(f"{origin} is not a valid resume document: {exc}") from exc


def dump_doc(doc: ResumeDoc) -> str:
    """Serialise `doc` to YAML that `load_doc` reads back to an equal document."""
    return yaml.safe_dump(
        doc.model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=100
    )
