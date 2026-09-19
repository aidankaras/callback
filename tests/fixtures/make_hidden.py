"""Documents that carry a channel a human reader cannot see.

Every source string below is a real, compiling Typst document -- not a
notional example -- and every claim its name makes (a colour matches the
background, a size falls under the floor, a glyph lands outside the page,
a code point carries no visible mark) is checked directly against the
compiled PDF in `tests/test_hidden.py` before that test ever calls the gate
under test. A fixture whose own claim doesn't hold would make the gate test
that follows it meaningless, whichever way it came out.

The handful of invisible or hard-to-distinguish code points used below
(a zero-width space, a soft hyphen, a variation selector, a right-to-left
override) are built from their integer code points with `chr(...)` rather
than typed or pasted as literal characters, so nothing in this file's
source is invisible or ambiguous in a diff.
"""

from __future__ import annotations

from pathlib import Path

import typst

ZERO_WIDTH_SPACE = chr(0x200B)
SOFT_HYPHEN_CHAR = chr(0x00AD)
VARIATION_SELECTOR_16 = chr(0xFE0F)
RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)

# Fill drawn at the page's own white, over ordinary visible text -- the
# plainest form of colour-matching keyword stuffing.
WHITE = """
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt)
Visible line of the document.
#text(fill: white)[machine learning kubernetes terraform distributed systems]
"""

# Text sized well under the 6.0pt floor, next to an ordinary 10pt line.
TINY = """
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt)
Visible line of the document.
#text(size: 1pt)[kubernetes terraform]
"""

# A zero-width space placed between two word characters, not next to a
# space. Typst drops U+200B outright when it sits adjacent to a space (a
# space is already a break opportunity, so an explicit zero-width one right
# next to it is redundant and gets discarded during shaping) -- placing it
# mid-word is what survives into the extracted text. Measured: this source
# produces four U+200B occurrences in the compiled PDF's extracted text, not
# one, because the same shaping pass that would have dropped an
# adjacent-to-space occurrence instead duplicates this one across a nearby
# word boundary. The fixture is exercised at that measured count, not at an
# assumed count of one.
ZERO_WIDTH = f"""
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt)
Visible{ZERO_WIDTH_SPACE}line with an invisible separator.
"""

# A soft hyphen (U+00AD): survives rendering, is category `Cf`, but falls
# outside every range `_INVISIBLE_RANGES` covers -- this is one of the two
# code points that regex is documented to miss and the category test must
# catch on its own.
SOFT_HYPHEN = f"""
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt, hyphenate: false)
Infra{SOFT_HYPHEN_CHAR}structure engineering role.
"""

# A text variation selector (U+FE0F): survives rendering, is category `Mn`
# (a combining mark, not `Cf`/`Cn`), but falls inside the U+FE00-U+FE0F
# range the regex covers -- the code point the category test is documented
# to miss and the regex must catch on its own.
VARIATION_SELECTOR = f"""
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt)
Alpha1{VARIATION_SELECTOR_16} Beta
"""

# A right-to-left override: renders as nine ordinary, fully visible letters
# ("AlphaBeta"), but a reader that trusts the extracted text sees the same
# nine letters in a different order -- the visible order and the extracted
# order diverge, rather than any content being concealed.
BIDI_OVERRIDE = f"""
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt)
Alpha{RIGHT_TO_LEFT_OVERRIDE}Beta
"""

# Two `#place`d blocks pushed past the left and right page edges. `dx` shifts
# the placed block's anchor from its normal flow position, so how far a
# glyph's `x0`/`x1` ends up past the edge depends on the block's own text
# length as well as `dx` -- only the leftward block's `min x0` is
# text-length-independent (it is set entirely by `dx` and the page margin),
# which is why the test asserting an exact value uses that one and not the
# rightward block's.
OFFPAGE = """
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt)
Visible line of the document.
#place(dx: -200pt, dy: 100pt)[Hidden keyword payload text left of page]
#place(dx: 700pt, dy: 130pt)[Hidden keyword payload text right of page]
"""

# The same attack on the vertical axis: a block placed far enough below the
# page that its glyphs' `bottom` exceeds the page height, invisible to a
# gate that only ever compared `x0`/`x1` against the page width.
OFFPAGE_VERTICAL = """
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt)
Visible line of the document.
#place(dx: 100pt, dy: 900pt)[Hidden keyword payload text below page]
"""

# `#set document(keywords: ...)` is real, supported Typst syntax that writes
# a `/Keywords` entry into the PDF's document-information dictionary -- a
# field no resume viewer displays, and a real place a candidate could stuff
# ATS-targeted terms invisibly to a human reader.
METADATA_LEAK = """
#set document(title: "Resume", author: "Dana Vex", keywords: ("kubernetes", "terraform"))
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt)
Visible line of the document.
"""


def build(source: str, out_pdf: Path, font_dir: Path) -> None:
    """Compile `source` to `out_pdf`, raising if the compiler warned.

    Matches every other fixture builder in this project: a warning during
    fixture construction (most often an unresolved font) means the fixture
    is not what its name claims, and discarding it silently would make
    whatever test runs against `out_pdf` check the wrong document.
    """
    typ = out_pdf.with_suffix(".typ")
    typ.write_text(source, encoding="utf-8")
    pdf, warnings = typst.compile_with_warnings(
        str(typ), font_paths=[str(font_dir)], ignore_system_fonts=True, timestamp=0
    )
    if warnings:
        raise RuntimeError(f"fixture render warned: {warnings}")
    out_pdf.write_bytes(pdf)
