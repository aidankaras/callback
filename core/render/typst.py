"""Typst rendering with the determinism guarantees the gates depend on.

Every layout gate planned after this one measures the produced PDF, so the
same input must produce the same bytes on any machine that runs it. Three
things are pinned here because each one, left unpinned, was observed to
break that guarantee in practice rather than merely in theory:

- System fonts are ignored (`ignore_system_fonts=True`) and the vendored
  family in `templates/fonts/` is passed explicitly via `font_paths`. A
  requested font that isn't found otherwise falls back to whatever the
  compiling machine happens to have installed under a similar name, which
  changes glyph widths and therefore line breaks with no error raised at
  all — this shifted content 6.7pt in one observed case.
- Every compiler warning is treated as a hard failure (`compile_with_warnings`,
  not `compile`). A font substitution is reported as a warning, not an
  error, so treating warnings as advisory would let the exact failure mode
  above back in silently. There is no warning this renderer considers safe
  to ignore, because every one of them means the compiler did something
  other than what the template asked for.
- The document's embedded timestamp is fixed (`timestamp=0`) rather than
  left to wall-clock time. Typst's default embeds `CreationDate` at
  one-second resolution, so two renders of byte-identical input made a
  second apart otherwise hash differently — a guarantee this module exists
  to provide, not an incidental nice-to-have.

`hyphenate: false` is set in `templates/resume.typ` itself, not here: a
justified, hyphenated line break ("infra-" / "structure") can silently split
a keyword a downstream text match depends on, so `render` also verifies no
extracted line ends in a hyphen is left to the caller's tests rather than
enforced unconditionally here, since a legitimate compound word ending a
line at the page margin is not itself a defect in the renderer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import typst
from pypdf import PdfReader
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

from core.resume.models import ResumeDoc

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "templates"
FONT_DIR = TEMPLATE_DIR / "fonts"
DEFAULT_FONT = "Libertinus Serif"


class RenderError(RuntimeError):
    """Raised when rendering is not provably deterministic.

    Covers both a fatal compiler warning (most often an unresolved font,
    hence callers matching on `"font"`) and a compiled PDF whose embedded
    font doesn't actually match what was requested.
    """


@dataclass(frozen=True)
class RenderResult:
    """A successful render: the bytes, the source that produced them, and
    the font families actually embedded — the last of which is how a
    silent font substitution gets caught even if the compiler emitted no
    warning for it."""

    pdf_bytes: bytes
    typst_source: str
    font_families: list[str]


def _contact_line(doc: ResumeDoc) -> str:
    parts = [
        doc.identity.email,
        *([doc.identity.phone] if doc.identity.phone else []),
        *doc.identity.links,
    ]
    return "  |  ".join(parts)


def _dates(start: str | None, end: str | None) -> str:
    if start and end:
        return f"{start} - {end}"
    return start or end or ""


def _payload(doc: ResumeDoc) -> dict[str, object]:
    return {
        "identity": {"name": doc.identity.name, "contact_line": _contact_line(doc)},
        "sections": [
            {
                "title": section.title,
                "entries": [
                    {
                        "heading": entry.heading,
                        "subheading": entry.subheading,
                        "right_line": "  ".join(
                            p for p in [entry.location, _dates(entry.start, entry.end)] if p
                        ),
                        "bullets": [b.text for b in entry.bullets],
                    }
                    for entry in section.entries
                ],
            }
            for section in doc.sections
        ],
    }


def build_source(doc: ResumeDoc, *, font_family: str = DEFAULT_FONT) -> str:
    """Render the document's data into a standalone Typst source file.

    The template is imported by a root-relative path (`/templates/...`) —
    Typst's own path syntax, where a leading `/` means "from the project
    root" and is unrelated to an OS-absolute path — so the source compiles
    correctly regardless of where its bytes are handed to the compiler from.
    `render` passes this source directly as bytes rather than writing it to
    a file first, precisely so the caller's output path (which may be
    anywhere, e.g. a pytest temp directory) never has to sit inside that
    project root itself; only the source and the vendored fonts do. The
    data is passed through as a JSON literal decoded at compile time rather
    than interpolated field-by-field, so a value containing a quote or a
    Typst markup character can't break the generated source.
    """
    data = json.dumps(_payload(doc), ensure_ascii=False)
    return (
        '#import "/templates/resume.typ": resume\n'
        f"#let data = json(bytes({json.dumps(data)}))\n"
        f'#resume(identity: data.identity, sections: data.sections, font: "{font_family}")\n'
    )


def render(doc: ResumeDoc, out_pdf: Path, *, font_family: str = DEFAULT_FONT) -> RenderResult:
    """Render `doc` to `out_pdf` and return the bytes, source, and embedded fonts.

    Raises `RenderError` if the compiler reported any warning, or if the
    embedded font family doesn't match `font_family` — see the module
    docstring for why both checks exist independently of each other.
    """
    source = build_source(doc, font_family=font_family)
    # A `Compiler` instance, not the free `typst.compile_with_warnings`
    # function, because the free function's stub binds `root` to the same
    # type variable as `input`: passing `input` as bytes (the source text)
    # would then require `root` to also be bytes rather than the filesystem
    # path it actually is. The `Compiler` constructor types `root`
    # independently, so fonts and root are set here and the source is
    # compiled below.
    compiler = typst.Compiler(font_paths=[str(FONT_DIR)], ignore_system_fonts=True)
    compiled, warnings = compiler.compile_with_warnings(
        source.encode("utf-8"), root=str(REPO_ROOT), timestamp=0
    )
    if warnings:
        raise RenderError(f"typst reported warnings, refusing output: {warnings}")
    if not isinstance(compiled, bytes):
        # Only possible if `input` were a glob or `output` were set to a
        # multi-page format, neither of which this call does; a single
        # `.typ` source with no `output` always compiles to one PDF's bytes.
        raise RenderError(f"expected a single PDF, got {type(compiled).__name__}")
    pdf_bytes = compiled
    out_pdf.write_bytes(pdf_bytes)
    families = _embedded_families(out_pdf)
    if not any(font_family.split()[0] in family for family in families):
        raise RenderError(f"requested font {font_family!r} is not embedded; PDF reports {families}")
    return RenderResult(pdf_bytes=pdf_bytes, typst_source=source, font_families=families)


def _resolve(obj: DictionaryObject | IndirectObject) -> DictionaryObject:
    resolved = obj.get_object() if isinstance(obj, IndirectObject) else obj
    if not isinstance(resolved, DictionaryObject):
        raise RenderError(f"expected a font dictionary, got {type(resolved).__name__}")
    return resolved


def _embedded_families(pdf_path: Path) -> list[str]:
    """Every `/BaseFont` name embedded in `pdf_path`, with subset tags stripped.

    A subsetted font's `/BaseFont` is prefixed with a 6-letter uppercase tag
    plus `+` (e.g. `XWPWRT+LibertinusSerif-Regular`); the tag is an
    arbitrary per-subset identifier, not part of the family name, so
    comparing it against the requested family would never match.
    """
    reader = PdfReader(str(pdf_path))
    names: set[str] = set()
    for page in reader.pages:
        resources = page.get("/Resources")
        if resources is None:
            continue
        fonts = _resolve(resources).get("/Font")
        if fonts is None:
            continue
        for ref in _resolve(fonts).values():
            font = _resolve(ref)
            base = font.get("/BaseFont")
            if base is not None:
                names.add(str(base).lstrip("/").split("+")[-1])
            descendants = font.get("/DescendantFonts")
            if isinstance(descendants, ArrayObject):
                for descendant in descendants:
                    sub_base = _resolve(descendant).get("/BaseFont")
                    if sub_base is not None:
                        names.add(str(sub_base).lstrip("/").split("+")[-1])
    return sorted(names)
