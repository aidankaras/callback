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
override, a byte-order mark, a combining grapheme joiner, and a few
ordinary combining accents) are built from their integer code points with
`chr(...)` rather than typed or pasted as literal characters, so nothing in
this file's source is invisible or ambiguous in a diff.
"""

from __future__ import annotations

from pathlib import Path

import typst

ZERO_WIDTH_SPACE = chr(0x200B)
SOFT_HYPHEN_CHAR = chr(0x00AD)
VARIATION_SELECTOR_16 = chr(0xFE0F)
RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)
COMBINING_GRAPHEME_JOINER_CHAR = chr(0x034F)
BYTE_ORDER_MARK_CHAR = chr(0xFEFF)
COMBINING_ACUTE_ACCENT = chr(0x0301)
COMBINING_DIAERESIS = chr(0x0308)
COMBINING_CARON = chr(0x030C)

# Fill drawn at the page's own white, over ordinary visible text -- the
# plainest form of colour-matching keyword stuffing.
WHITE = """
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt)
Visible line of the document.
#text(fill: white)[machine learning kubernetes terraform distributed systems]
"""

# A grey too light to read against a white page but well above the old,
# incorrect `>= 0.95` component floor. Measured: 90% grey quantizes to
# 230/255 = 0.90196 and contrasts at 1.25:1 against white -- comfortably
# under this module's 1.5:1 floor, and comfortably below anything WCAG
# would call legible, but `>= 0.95` on that same value is False, so the
# pre-fix code let this straight through.
GREY_INVISIBLE = """
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt)
Visible line of the document.
#text(fill: rgb(90%, 90%, 90%))[kubernetes terraform hidden in light grey]
"""

# A grey pale enough to be a deliberate low-emphasis design choice, not an
# attempt to hide anything. Measured: 80% grey contrasts at 1.61:1 against
# white, just clear of this module's 1.5:1 floor -- this fixture's role is
# proving the floor does not reach this far, not that anything here should
# be flagged.
GREY_LEGIBLE = """
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt)
Visible line of the document.
#text(fill: rgb(80%, 80%, 80%))[a faintly styled secondary line]
"""

# A page with a painted, non-white background, and text drawn in that same
# colour -- invisible for a reason the old `>= 0.95`-from-white test could
# never see, because the text is nowhere near white.
NAVY_ON_NAVY = """
#set page(paper: "us-letter", margin: 0.5in, fill: rgb(20%, 20%, 40%))
#set text(font: "Libertinus Serif", size: 10pt, fill: white)
Visible line of the document.
#text(fill: rgb(20%, 20%, 40%))[kubernetes terraform hidden on a navy page]
"""

# The false-positive case the old test made unavoidable: a painted
# non-white background with fully legible, high-contrast text drawn on it.
# Measured: white text against this page's rgb(20%, 20%, 40%) background
# contrasts at 11.63:1, far above the 1.5:1 floor -- clearly legible, and
# the point of this fixture is that it must never be flagged.
NAVY_BACKGROUND_WHITE_TEXT = """
#set page(paper: "us-letter", margin: 0.5in, fill: rgb(20%, 20%, 40%))
#set text(font: "Libertinus Serif", size: 10pt, fill: white)
Visible line of the document on a navy background.
"""

# The false positive a fill-only check cannot avoid: white fill (matching
# the white page) traced with a thin black stroke, the classic look of an
# outlined heading. The fill alone contrasts at 1.0:1 against the page --
# indistinguishable from genuinely invisible white-on-white text -- but the
# stroke that actually delineates every letter contrasts at roughly 21:1,
# and the heading is entirely legible.
OUTLINE_HEADING = """
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt)
Visible line of the document.
#text(fill: white, stroke: 0.5pt + black)[OUTLINED HEADING]
"""

# A payload this module still misses, documented rather than silently left
# unmentioned: a fill (black) that contrasts perfectly well on its own, and
# an explicit stroke (white) that does not -- but the stroke is 3pt wide on
# 10pt text, six times the width of the glyphs' own strokes, so painted on
# top of the fill it covers nearly all of it. Rendered directly to an
# image, the payload text is entirely blank but for a single stray mark.
# `check_hidden` still reports this document as clean, because
# `_invisible_fill` takes the better of the fill's and the stroke's own
# contrast, and the fill's contrast alone is fine -- see the module
# docstring's "Documented, not implemented" section for why the fix in
# this round could not close this specific shape without also reopening a
# different one.
THICK_WHITE_STROKE = """
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt)
Visible line of the document.
#text(fill: black, stroke: 3pt + white)[kubernetes terraform hidden by stroke]
"""

# `gradient.linear(...)` as a page fill still produces the single,
# page-covering rect `page_background` looks for, but that rect's
# `non_stroking_color` is the pattern name `"p0"` -- a string, not a
# colour `to_rgb` can parse. `page_background` reports this as
# unrecoverable rather than defaulting to white.
GRADIENT_BACKGROUND = """
#set page(paper: "us-letter", margin: 0.5in, fill: gradient.linear(rgb(20%, 20%, 40%), white))
#set text(font: "Libertinus Serif", size: 10pt)
Visible line of the document.
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

# A soft hyphen (U+00AD): survives rendering, and is deliberately *not* in
# this module's default-ignorable table (see the note in `core.checks.hidden`
# beside `_DEFAULT_IGNORABLE_RANGES`) -- it carries no letters of its own,
# so it cannot smuggle a keyword, and it is a routine artefact of ordinary
# hyphenated, justified typesetting rather than a hiding technique.
SOFT_HYPHEN = f"""
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt, hyphenate: false)
Infra{SOFT_HYPHEN_CHAR}structure engineering role.
"""

# A realistic version of the same thing: an ordinarily hyphenated,
# justified two-inch column -- the shape a genuine Word or LaTeX resume
# section, or this project's own template with `hyphenate: true` instead
# of the `false` it actually sets, would produce. Measured: this specific
# paragraph, at this width, produces four soft hyphens in the compiled
# PDF's extracted text -- a real typesetting artefact, not a contrived
# single character.
JUSTIFIED_HYPHENATED = """
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt, hyphenate: true)
#set par(justify: true)
#box(width: 2in)[
Distinguished accomplishments encompassing organizational transformation \
initiatives, comprehensive stakeholder communications, and \
interdisciplinary collaboration across multinational engineering \
departments throughout the entire organization.
]
"""

# A text variation selector (U+FE0F): survives rendering and is inside the
# `Default_Ignorable_Code_Point` range table's U+FE00-U+FE0F entry.
VARIATION_SELECTOR = f"""
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt)
Alpha1{VARIATION_SELECTOR_16} Beta
"""

# A byte-order mark (U+FEFF): survives rendering and is inside the
# `Default_Ignorable_Code_Point` range table's own entry for it.
BYTE_ORDER_MARK = f"""
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt)
Alpha{BYTE_ORDER_MARK_CHAR}Beta
"""

# A combining grapheme joiner (U+034F): survives rendering, is category
# `Mn` (a combining mark, like an ordinary accent) rather than `Cf`, and
# falls outside every hand-picked range the previous, narrower regex
# checked -- the code point neither of that regex's two mechanisms caught,
# and the reason this module now checks the full
# `Default_Ignorable_Code_Point` set instead of an ad hoc approximation
# of it.
COMBINING_GRAPHEME_JOINER = f"""
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt)
Alpha{COMBINING_GRAPHEME_JOINER_CHAR}Beta
"""

# Three ordinary names, each spelled with a *decomposed* combining accent
# (a base letter followed by a separate combining mark) rather than a
# single precomposed character -- the same category (`Mn`) as the
# combining grapheme joiner above, but not itself default-ignorable.
# Proves the default-ignorable set is the right property to check rather
# than "is this character a combining mark," which would also catch every
# one of these and reject ordinary accented names.
ACCENTED_NAMES = f"""
#set page(paper: "us-letter", margin: 0.5in)
#set text(font: "Libertinus Serif", size: 10pt)
Jose{COMBINING_ACUTE_ACCENT} Mu{COMBINING_DIAERESIS}ller S{COMBINING_CARON}koda
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
