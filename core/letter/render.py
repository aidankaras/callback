"""Rendering a `CoverLetter` through `templates/letter.typ`."""

from __future__ import annotations

import json
from pathlib import Path

from core.letter.models import CoverLetter
from core.render.typst import DEFAULT_FONT, RenderResult, compile_source


def _contact_line(letter: CoverLetter) -> str:
    identity = letter.identity
    return "  |  ".join([identity.email, *filter(None, [identity.phone]), *identity.links])


def build_letter_source(letter: CoverLetter, *, font_family: str = DEFAULT_FONT) -> str:
    """The letter's data as a Typst source, passed as a JSON literal so no value
    can be read as markup -- the same construction as the resume's."""
    data = {
        "identity": {"name": letter.identity.name, "contact_line": _contact_line(letter)},
        "greeting": letter.greeting,
        "paragraphs": [" ".join(s.text for s in paragraph) for paragraph in letter.paragraphs],
        "closing": letter.closing,
    }
    encoded = json.dumps(json.dumps(data, ensure_ascii=False))
    return (
        '#import "/templates/letter.typ": letter\n'
        f"#let data = json(bytes({encoded}))\n"
        "#letter(identity: data.identity, greeting: data.greeting, "
        f'paragraphs: data.paragraphs, closing: data.closing, font: "{font_family}")\n'
    )


def render_letter(letter: CoverLetter, out_pdf: Path) -> RenderResult:
    return compile_source(build_letter_source(letter), out_pdf)
