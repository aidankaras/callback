"""Generate a genuinely two-column resume whose reading order is ambiguous.

The round-trip gate exists to reject documents like this one. A parser that
follows the PDF content stream reconstructs it perfectly, which is why the gate
must not use one.
"""

from __future__ import annotations

import json
from pathlib import Path

import typst

from core.profile.models import Identity
from core.resume.models import Bullet, Entry, ResumeDoc, Section

TEMPLATE = """
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt, hyphenate: false)
#columns(2, gutter: 24pt)[
{body}
]
"""


def build(out_pdf: Path, font_dir: Path, count: int = 70) -> None:
    body = "\n\n".join(
        f"Alpha{i} entry line describing work item {i}." for i in range(1, count + 1)
    )
    source = out_pdf.with_suffix(".typ")
    source.write_text(TEMPLATE.format(body=body), encoding="utf-8")
    # No `output` argument is passed, so the stub resolves to the overload
    # that always returns `bytes` (never `None` or `list[bytes]`) — no
    # isinstance narrowing needed, and pyright strict flags one as dead code
    # (reportUnnecessaryIsInstance) if added anyway.
    pdf, warnings = typst.compile_with_warnings(
        str(source), font_paths=[str(font_dir)], ignore_system_fonts=True, timestamp=0
    )
    if warnings:
        raise RuntimeError(f"fixture render warned: {warnings}")
    out_pdf.write_bytes(pdf)


# `#columns(2)` fills column one top to bottom, then continues at the top of
# column two -- so on a page tall enough to hold roughly half the entries per
# column, an entry near the end of column one sits at the same height as an
# entry near the start of column two. `naive_segments` bands purely by
# height, so a row at that height picks up fragments of both entries at
# once, sorted left to right: the reading order it reconstructs interleaves
# the tail of column one with the head of column two instead of finishing
# one column before starting the other.
_RESUME_TEMPLATE = """#let data = json(bytes({json_data}))
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt, hyphenate: false)
#set par(justify: false, leading: 0.55em)
#align(center)[
  #text(size: 16pt, weight: "bold")[#data.identity.name]
  #linebreak()
  #text(size: 9pt)[#data.identity.contact_line]
]
#v(6pt)
#text(size: 11pt, weight: "bold")[#upper(data.section_title)]
#line(length: 100%, stroke: 0.5pt)
#v(2pt)
#columns(2, gutter: 24pt)[
#for entry in data.entries [
  #grid(
    columns: (1fr, auto),
    text(weight: "bold")[#entry.heading],
    text(size: 9pt)[#entry.right_line],
  )
  #text(style: "italic", size: 9.5pt)[#entry.subheading]
  #grid(columns: (10pt, 1fr), [•], [#entry.bullet])
  #v(4pt)
]
]
"""


def _two_column_payload(doc: ResumeDoc) -> dict[str, object]:
    contact = "  |  ".join(
        [doc.identity.email] + ([doc.identity.phone] if doc.identity.phone else [])
    )
    entries: list[dict[str, str]] = []
    for entry in doc.sections[0].entries:
        if entry.start and entry.end:
            dates = f"{entry.start} - {entry.end}"
        else:
            dates = entry.start or entry.end or ""
        entries.append(
            {
                "heading": entry.heading,
                "right_line": "  ".join(p for p in [entry.location, dates] if p),
                "subheading": entry.subheading or "",
                "bullet": entry.bullets[0].text if entry.bullets else "",
            }
        )
    return {
        "identity": {"name": doc.identity.name, "contact_line": contact},
        "section_title": doc.sections[0].title,
        "entries": entries,
    }


def build_two_column_resume(
    out_pdf: Path, font_dir: Path, entries: int = 24
) -> tuple[ResumeDoc, Path]:
    """Render a `ResumeDoc` with `entries` entries in a two-column layout and
    write it to `out_pdf`, returning the document alongside the path so the
    same scrambled PDF can be checked with `check_roundtrip`.

    Every entry's fields (heading, subheading, location, dates, one bullet),
    the section title, and every identity field are individually
    substring-recoverable somewhere on the page -- a presence-only check
    would pass this document, and its character ratio is exactly 1.0
    because nothing declared is actually absent. What the layout breaks is
    declared reading *order*: measured at `entries=24`, every heading is
    recoverable, and 10 of the 24 are unreachable by a cursor that only
    moves forward through the page in the order a naive parser reads it
    (`core.checks.naive_extract.naive_segments`)
    -- see the constant `_RESUME_TEMPLATE` above for why a two-column layout
    produces that specific failure. At `entries=10` the body fits inside a
    single column and nothing overflows into column two at all, so the
    reading order comes out perfectly ascending and the fixture stops being
    adversarial; the caller comparing against this fixture's own output
    (`result.out_of_order`) is the guard against that, in the same spirit as
    `build`'s caller checking the word count in the page's right half.

    The document's fields are injected as a JSON literal decoded inside the
    Typst source, exactly as `core.render.typst.build_source` does, rather
    than interpolated into the markup as raw text: an email address such as
    "dana@example.com" interpolated directly would have Typst parse
    `@example.com` as a label reference and fail to compile, since `@`
    introduces reference syntax outside of code context. Passing it through
    `data.identity.contact_line` instead inserts it as a string value, never
    as markup to be parsed.
    """
    doc = ResumeDoc(
        identity=Identity(name="Dana Vex", email="dana@example.com", phone="+1 555 0100"),
        sections=[
            Section(
                title="Experience",
                entries=[
                    Entry(
                        heading=f"Company {i}",
                        subheading=f"Engineer {i}",
                        location="Boston, MA",
                        start="Jun 2024",
                        end="Aug 2025",
                        bullets=[Bullet(text=f"Shipped milestone {i} on time", sources=["a"])],
                    )
                    for i in range(1, entries + 1)
                ],
            )
        ],
    )
    data = json.dumps(_two_column_payload(doc), ensure_ascii=False)
    source = _RESUME_TEMPLATE.format(json_data=json.dumps(data))
    source_path = out_pdf.with_suffix(".typ")
    source_path.write_text(source, encoding="utf-8")
    pdf, warnings = typst.compile_with_warnings(
        str(source_path), font_paths=[str(font_dir)], ignore_system_fonts=True, timestamp=0
    )
    if warnings:
        raise RuntimeError(f"fixture render warned: {warnings}")
    out_pdf.write_bytes(pdf)
    return doc, out_pdf
