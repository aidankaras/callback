"""Generate a genuinely two-column resume whose reading order is ambiguous.

The round-trip gate exists to reject documents like this one. A parser that
follows the PDF content stream reconstructs it perfectly, which is why the gate
must not use one.
"""

from __future__ import annotations

from pathlib import Path

import typst

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
