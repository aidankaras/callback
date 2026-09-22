"""Reading and writing a `CoverLetter` as YAML."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from core.letter.models import CoverLetter
from core.resume.io import DocumentError


def parse_letter(data: bytes, *, origin: Path) -> CoverLetter:
    """Parse and validate a cover letter from `data`; `origin` names it in errors."""
    try:
        parsed: object = yaml.safe_load(data.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise DocumentError(f"{origin} is not valid YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise DocumentError(f"{origin} must contain a mapping, found {type(parsed).__name__}")
    try:
        return CoverLetter.model_validate(parsed)
    except ValidationError as exc:
        raise DocumentError(f"{origin} is not a valid cover letter: {exc}") from exc


def dump_letter(letter: CoverLetter) -> str:
    """Serialise `letter` to YAML that `parse_letter` reads back to an equal letter."""
    return yaml.safe_dump(
        letter.model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=100
    )
