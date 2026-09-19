"""The hidden-channel gate.

A rendered PDF can say one thing to a human reader and another to whatever
reads it next -- an ATS parser, a copy-paste into a text box, an LLM fed the
extracted text. Nothing about producing a *visually* correct document rules
that out: PDF text is drawn independently of whether it is visible, sized
legibly, placed on the page, or free of characters that carry no glyph at
all.

**Where each channel is actually reachable.** On this project's own render
path, none of the five checks below can currently be tripped by anything a
candidate's resume content says: `core.render.typst.render` passes every
string field through as a JSON-encoded *value*, decoded by the fixed
template at compile time, never as Typst markup the template re-parses.
Feeding `#text(fill: white)[...]`, `#box(clip: true)[...]`, `#text(size:
1pt)[...]` or `#place(dx: -300pt)[...]` through a bullet's text renders as
that literal, fully visible string, with the template's own sizes and
colours undisturbed -- there is no markup-injection path from resume
content into the compiled document. On that path, this gate is a regression
check: it exists to catch the template itself drifting into one of these
five shapes, not to catch a candidate's text doing so, because a candidate's
text cannot reach any of them.

That is not true of a PDF this project did not produce. A document imported
from elsewhere -- exported from Word, run through a third-party "ATS
optimizer," or handed over with no known provenance at all -- was not built
through the fixed template, so every one of these five channels is live
against it: nothing about how that file was constructed is controlled here.
This module's checks are written for that document, even though today's
only caller renders documents of the safer kind.

This module looks for the five channels that let a document diverge that
way, each closing a distinct attack and each blind to the others:

- `invisible_text`: a glyph whose fill and, if the document set one, stroke
  both fail to contrast against the fill actually behind it. Closes
  colour-matching keyword stuffing, including against a non-white page
  background (a coloured banner or panel), and reading both channels closes
  a false positive the fill-only version of this check could not avoid:
  stroke-only outlined text (a white-filled, black-stroked heading) is
  legible and must not be flagged just because its *fill* happens to match
  the page. See `_invisible_fill` for what reading both channels does and
  does not close.
- `tiny_text`: a glyph below `MIN_FONT_SIZE`. Closes text shrunk small
  enough that a reader skims past it without ever consciously reading it.
  Misses anything at or above the floor, however densely packed or
  low-contrast; size is the only signal checked, and misses a horizontal-only
  squash that leaves the reported size untouched -- see "Documented, not
  implemented" below.
- `offpage_text`: a glyph whose box falls outside the page on any of the
  four sides. Closes text pushed past a margin with `#place` so it never
  paints inside the visible page area at all. Misses text placed *inside*
  the page box but clipped to nothing or drawn fully transparent -- see
  "Documented, not implemented" below.
- `metadata_leak`: a document-info dictionary key outside `ALLOWED_METADATA`.
  Closes keyword stuffing in a field no resume viewer ever renders (a
  `/Keywords` entry is real Typst behaviour, produced by
  `#set document(keywords: ...)`, not a hypothetical). Misses content
  hidden inside an *allowed* key's own value, or in XMP metadata, embedded
  files, or any other part of the PDF structure this check never opens.
- `unicode_channel`: a code point in the extracted text with Unicode's own
  `Default_Ignorable_Code_Point` property -- the set Unicode itself defines
  as "specified to have no visible glyph," with one deliberate exclusion
  (the soft hyphen; see the note beside `_DEFAULT_IGNORABLE_RANGES`).
  Closes content smuggled through characters a compliant renderer draws
  with no ink even though the glyph run "contains" them. A right-to-left
  or left-to-right override is also flagged separately as `bidi_override`;
  see `check_hidden`'s docstring for why.

A sixth finding, `background_unknown`, is not a channel of its own but a
signal that `invisible_text` could not be checked at all on a page: see
`page_background`.

None of these six is a substitute for another; a document that clears all
of them still is not proven free of every conceivable hidden channel (XMP
metadata and embedded-file streams are two channels no check here reads at
all), only free of the ones a resume actually has reason to carry.

**Documented, not implemented.** Every case below shares one root cause:
visibility is a property of what gets *painted*, and every check in this
module infers it instead from partial content-stream metadata -- a colour
value, a reported size, a bounding box. That inference is an unbounded
chase: closing one gap this way just reveals the next piece of metadata a
document could vary instead. A real content-stream walker (tracking the
graphics-state stack across every `q`/`Q`, every `gs`, every `w`) would
close some of these; a genuinely structural answer closes all of them at
once by comparing rendered *pixels* rather than any content-stream metadata
-- what a page looks like does not care which operator produced it.

A first attempt at that structural answer was tried and rejected: counting
the number of distinct colours inside a glyph's rendered bounding box
(rendered with `pypdfium2`, already available transitively through
`pdfplumber`, and BSD-3 licensed like it) measures 1 distinct colour for a
genuinely invisible glyph (the thick-stroke case below) and 129 for an
ordinary, clearly visible letter -- but it also measures 12 for the
opaque-box case and 110 for the gradient-background case below, both
reading as "visible" by that count, because the box's edge and the
gradient's own variation inside the glyph's box are what get counted, not
the text. A real pixel-based check needs an inset margin that avoids
whatever is drawn at the very edge of a glyph's box, a local-contrast
measure rather than a raw colour count, and a calibrated threshold -- a
design in its own right, not a drop-in replacement for what this module
does today.

- *Zero opacity.* `#text(fill: rgb(0%, 0%, 0%, 0%))` paints nothing, yet
  `pdfplumber`'s char dict reports a fully opaque `(0.0, 0.0, 0.0)` for it
  (measured directly) -- alpha is set through the ExtGState `ca`/`CA`
  operators, which `pdfplumber` does not expose on a char at all, not
  through the colour operators this module reads. Closing this needs a
  content-stream walker that tracks the graphics-state stack (`q`/`Q`,
  `gs` referencing an ExtGState dictionary's `ca`/`CA`) alongside each
  glyph-drawing operator.
- *Clipping.* `#box(width: 0pt, height: 0pt, clip: true)[...]` paints
  nothing, and the glyphs it contains report entirely ordinary colour,
  size and position (measured directly: black, 10pt, positioned among the
  visible text) -- this is a different mechanism from an opaque shape
  drawn on top of visible text, and is invisible to every check in this
  module. Closing this needs tracking the active clipping path's bounding
  box (`W n` clip operators, scoped by the same `q`/`Q` stack) and
  comparing each glyph's box against the *effective* clip region, not just
  the page box.
- *Horizontal-only squash.* `#scale(x: 2%, y: 100%)` leaves `pdfplumber`'s
  reported `size` at the original value, because that field is derived from
  the text matrix's vertical scale -- a squash confined to the horizontal
  axis collapses each glyph's actual width toward zero (measured: ~4-5pt
  down to ~0.07-0.1pt) while `tiny_text` keeps reading the unaffected
  vertical size and sees nothing wrong. Closing this needs reading the full
  text rendering matrix to recover the horizontal scale independently of
  the vertical one. Weaker than the other two even as a gap: a visible
  smudge of compressed ink remains on the page, so this is not fully
  invisible the way the other two are, only unreadable.
- *An opaque shape drawn behind matching text.* `#box(fill: rgb(20%, 20%,
  40%))[#text(fill: rgb(20%, 20%, 40%))[...]]` paints the text in the exact
  colour of a shape sitting directly behind it, which `_invisible_fill`
  cannot see: it only ever compares a glyph's own fill against
  `page_background`, and `page_background` only recognises a rect covering
  at least 90% of the page, not a small box sized to one line of text --
  measured: a box around one payload line covers 172.61pt x 6.58pt, about
  0.23% of a 612x792 page. `pdfplumber`'s reported fill colour is correct
  here (it does match the true background at that point on the page); the
  gap is that this module never looks at anything smaller than the whole
  page when asking what a glyph sits on.
- *A non-solid page background.* `#set page(fill: gradient.linear(...))`
  still produces a single rect covering the whole page, so `page_background`
  finds it -- but its `non_stroking_color` is the string `"p0"`, a pattern
  name, not a colour tuple, because a gradient or tiling pattern fill is a
  separate PDF resource referenced by name rather than a colour value in
  the content stream. `to_rgb` correctly returns `None` for it, and
  `page_background` reports `background_unknown` rather than guessing --
  see `page_background` -- but that only makes the *uncertainty* visible;
  it does not let `invisible_text` actually check a page like this at all,
  since there is no single background colour to check contrast against in
  the first place.
- *The CMYK-to-RGB conversion this module uses is not the only one, and
  they disagree.* `to_rgb` converts CMYK multiplicatively
  (`r = (1-c)(1-k)`, and so on), while the PDF specification's own
  fallback conversion for a device that cannot render CMYK directly is
  additive (`r = 1 - min(1, c+k)`). The two formulas model different
  physical assumptions about how ink layers combine, and they diverge
  measurably: `cmyk(50%, 30%, 20%, 40%)` computes a contrast of 5.67:1
  against white under the multiplicative formula this module uses versus
  9.20:1 under the additive one -- both clear `MIN_CONTRAST_RATIO` here,
  but a random search over CMYK values found real combinations where they
  do not agree which side of the floor a colour falls on (for example,
  `cmyk(23.4%, 8.5%, 18.7%, 5.7%)` measures 1.491:1 under this module's
  formula -- just under the floor, flagged invisible -- and 1.516:1 under
  the additive one -- just over it, correctly visible). Neither formula is
  what a real print RIP or an on-screen viewer's colour management
  actually produces, which depends on device profiles this module has no
  access to; a CMYK colour's true rendered appearance is not reliably
  recoverable from its four numbers at all, only from the pixels a
  specific renderer actually produced.
- *A stroke wide enough to paint over its own fill.* `#text(fill: black,
  stroke: 3pt + white)[...]` reports a fill (black) that contrasts well and
  an explicit stroke (white) that does not; `_invisible_fill` takes the
  *better* of the two (see its docstring), which reports this glyph as
  visible because its fill does contrast -- but rendered at 3pt on 10pt
  text, the stroke's width exceeds the glyph's own strokes and paints over
  almost the entire fill, leaving the page visually blank (confirmed by
  rendering it: nothing survives but a single stray mark). The
  distinguishing fact is stroke *width* relative to the glyph's own size,
  and `pdfplumber` does not expose a per-character line width at all --
  confirmed against its full documented attribute set. A rule that instead
  trusted the stroke whenever one was explicitly set (rather than the
  better of the two) would close this specific case, but was rejected here
  because it opens a different one: a decorative, low-contrast stroke
  meant only as a subtle trim on already-legible, high-contrast fill text
  would then be flagged as invisible on the stroke's contrast alone, even
  though the fill alone is perfectly readable. Both rules are wrong for
  some real document; only the glyph's actual rendered width -- pixels, or
  the line-width operand this module cannot currently read -- resolves it
  correctly in both directions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import pdfplumber
from pypdf import PdfReader

# Measured against this project's own template (see `test_hidden.py`): real
# body text renders at 9pt, 10pt, 11pt and 16pt, so 6.0 leaves 3pt of
# headroom below the smallest size the template ever legitimately produces.
# A genuine reason to render below 9pt -- a footnote, a copyright line, a
# citation -- would still typically sit at 7-8pt, comfortably inside that
# headroom; nothing on a printed page is commonly set below roughly 6pt and
# still expected to be read, which is why 6.0 is a floor rather than a
# guess split between the template's own sizes. It is not tied to the
# template's specific 9pt body size, though: raising the template's body
# size would not by itself justify raising this floor, since the floor's
# job is bounding what a human can read at all, not tracking one template's
# choice of body size.
MIN_FONT_SIZE = 6.0

# An allowlist, not a denylist, because the failure modes are asymmetric.
# Denylisting specific keys (/Keywords, /Subject, ...) requires knowing
# every key a hiding place could use in advance, including ones a future
# Typst release adds; missing one means a new channel opens silently and
# this gate says nothing. Allowlisting instead means an unrecognised key --
# whether it is a genuine new hazard or just a harmless addition from a
# compiler upgrade -- fails loudly and visibly, and the fix (extending this
# set once the key is inspected and judged harmless) is a one-line, visible
# change rather than a channel nobody noticed. The cost is a document that
# legitimately needs a currently-unlisted key failing until this set is
# updated; for a gate whose job is exactly to be suspicious of anything it
# does not recognise, that cost is the point, not a defect.
ALLOWED_METADATA = {"/Title", "/Author", "/Producer", "/Creator", "/CreationDate", "/ModDate"}

# WCAG's contrast-ratio floor for large text (3:1) or normal text (4.5:1)
# both assume the text is *meant* to be read comfortably; this gate asks a
# narrower question -- can the text be perceived as text at all -- so it
# sits below both. Measured directly against a white page (quantized to
# Typst's 8-bit colour resolution, which is why the numbers below don't
# land on round percentages): 98% grey measures 1.04:1, 96% grey 1.09:1,
# 95% grey 1.12:1 (this is the case that mattered: 95% grey quantizes to
# 242/255 = 0.94902, just under the old code's literal `>= 0.95` component
# test, so the effective old floor was 96%, not 95%, and every one of these
# greys passed as "not background" even though none is legible), 94% grey
# 1.14:1, 90% grey 1.25:1, and 80% grey 1.61:1. Set at 1.5:1, every one of
# 98/96/95/94/90% grey is flagged and 80% grey is not -- the floor sits
# between a grey nothing could call legible and the palest grey a
# deliberate low-emphasis design (a faint section divider, a de-emphasized
# caption) could plausibly still intend to be read. A design that wants a
# fainter divider than that is choosing something close to genuinely
# unreadable, which this floor treats as indistinguishable from hidden --
# that is the one class of legitimate design this floor forecloses.
MIN_CONTRAST_RATIO = 1.5

# The ranges Unicode's own `Default_Ignorable_Code_Point` derived property
# covers, minus one deliberate exclusion -- see the note below the table --
# rather than an ad hoc guess at which formatting/joining characters happen
# to be invisible. Verified against this project's vendored font: a
# combining grapheme joiner (U+034F) survives compilation and extraction
# with no compiler warning, and is caught here even though it is neither
# in the old code's hand-picked ranges nor in the `Cf`/`Cn` category test
# this replaces -- it is category `Mn` (a combining mark) and fell outside
# every range the old regex checked, so the old two-check split missed it
# by both mechanisms at once. An ordinary accented name written with a
# decomposed combining accent (an "e" followed by U+0301 COMBINING ACUTE
# ACCENT, as in a "Jose" typed with separate base and accent rather than a
# single precomposed "e"-acute) is not in this set -- confirmed directly:
# U+0301 is category `Mn` like the joiner above, but combining diacritics
# in general are not default-ignorable, only the specific joining/format
# characters below are, so ordinary accented names are never flagged.
#
# Written as integer code points rather than escape sequences or literal
# characters, so the table stays plain ASCII and reviewable at a glance --
# an invisible character sitting directly in this file's source would be
# exactly the kind of thing this module exists to catch a document for
# doing, and would be unreadable in a diff besides.
_DEFAULT_IGNORABLE_RANGES: list[tuple[int, int]] = [
    (0x034F, 0x034F),  # COMBINING GRAPHEME JOINER
    (0x061C, 0x061C),  # ARABIC LETTER MARK
    (0x115F, 0x1160),  # HANGUL CHOSEONG/JUNGSEONG FILLER
    (0x17B4, 0x17B5),  # KHMER VOWEL INHERENT AQ/AA
    (0x180B, 0x180E),  # Mongolian free variation selectors + vowel separator
    (0x200B, 0x200F),  # ZERO WIDTH SPACE .. RIGHT-TO-LEFT MARK
    (0x202A, 0x202E),  # LRE, RLE, PDF, LRO, RLO (bidi embedding/override)
    (0x2060, 0x206F),  # WORD JOINER .. deprecated invisible operators/isolates
    (0x3164, 0x3164),  # HANGUL FILLER
    (0xFE00, 0xFE0F),  # VARIATION SELECTOR-1 .. VARIATION SELECTOR-16
    (0xFEFF, 0xFEFF),  # ZERO WIDTH NO-BREAK SPACE / BOM
    (0xFFA0, 0xFFA0),  # HALFWIDTH HANGUL FILLER
    (0xFFF0, 0xFFF8),  # unassigned specials reserved as default-ignorable
    (0x1BCA0, 0x1BCA3),  # Shorthand format controls
    (0x1D173, 0x1D17A),  # musical symbol format controls
    (0xE0000, 0xE0FFF),  # Unicode tag block + variation selectors supplement
]

# U+00AD SOFT HYPHEN is deliberately absent from the table above, even
# though it is one of Unicode's own default-ignorable code points. It is
# not a hidden-payload vector: it carries no letters of its own, so it
# cannot smuggle a keyword the way a zero-width space between two real
# letters can, and inserting one into a word only ever makes that word
# *harder* to match against a naive extractor, never easier -- the
# opposite of what hiding a keyword would want. It is instead a routine
# typesetting artefact: any hyphenated, justified column -- produced by
# Word, LibreOffice, InDesign, LaTeX, or this project's own template with
# `hyphenate: true` instead of the `false` it actually sets -- inserts one
# at every soft break point a line-breaking algorithm considered, whether
# or not the break was taken there. Flagging it here would fail exactly
# the ordinarily-typeset imported PDFs this gate exists to check, on a
# character whose only real effect is on parseability, not concealment --
# and parseability is already `core.checks.roundtrip`'s job, whose
# `normalize` step strips this exact character before comparing text.
# (A resume author trying to defeat a *repetition* check by breaking up a
# repeated word with soft hyphens is a real technique, but the corpus-wide
# repetition analysis that would need to see through is out of scope for
# this module -- it belongs with whatever gate implements that check, not
# with the hidden-payload detector.)
_DEFAULT_IGNORABLE = re.compile(
    "[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _DEFAULT_IGNORABLE_RANGES) + "]"
)

# `Default_Ignorable_Code_Point` also flags U+200D ZERO WIDTH JOINER, which
# is not a hidden channel when it appears inside an emoji ZWJ sequence (the
# character that fuses, say, a person and a briefcase glyph into one
# rendered emoji) -- there the visible glyph is exactly what a reader sees,
# and the joiner is structural, not concealment. This module flags it
# anyway rather than special-casing emoji sequences: distinguishing "ZWJ
# joining two emoji" from "ZWJ smuggling a payload" needs tracking which
# neighbouring code points are emoji, which is more context-sensitive logic
# than the rest of this module carries, and a resume containing a decorative
# compound emoji is rare enough that treating it the same as any other
# default-ignorable hit -- a finding worth a second look, not a silent pass
# -- is the safer default until that turns out to be wrong in practice.
_BIDI_RANGES: list[tuple[int, int]] = [
    (0x202A, 0x202E),  # LRE, RLE, PDF, LRO, RLO
    (0x2066, 0x2069),  # LRI, RLI, FSI, PDI
]
_BIDI_CONTROL = re.compile("[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _BIDI_RANGES) + "]")


def is_default_ignorable(text: str) -> bool:
    """True if `text` contains any code point in `Default_Ignorable_Code_Point`
    (as narrowed by this module -- see the note beside `_DEFAULT_IGNORABLE_RANGES`).
    """
    return _DEFAULT_IGNORABLE.search(text) is not None


class HiddenKind(StrEnum):
    """Every reason `check_hidden` can attach to a `HiddenFinding`."""

    INVISIBLE_TEXT = "invisible_text"
    TINY_TEXT = "tiny_text"
    OFFPAGE_TEXT = "offpage_text"
    METADATA_LEAK = "metadata_leak"
    UNICODE_CHANNEL = "unicode_channel"
    BIDI_OVERRIDE = "bidi_override"
    BACKGROUND_UNKNOWN = "background_unknown"


@dataclass(frozen=True)
class HiddenFinding:
    """A single reason a document failed the hidden-channel gate."""

    kind: str
    detail: str


def to_rgb(colour: object) -> tuple[float, float, float] | None:
    """Normalize a `pdfplumber` fill colour to `(r, g, b)` in `[0, 1]`.

    `non_stroking_color` arrives as a greyscale `(g,)` tuple, an RGB
    `(r, g, b)` tuple, a CMYK 4-tuple, or a bare number for a greyscale fill
    applied outside a colour-space wrapper -- or, for a gradient or tiling
    pattern fill, a `str` pattern name rather than a colour at all, which
    this function correctly reports as unrecoverable (`None`) rather than
    guessing. CMYK is converted with a naive, uncalibrated formula -- exact
    colour management is not the point here, only recovering enough of the
    colour to judge whether a human could tell it apart from what's behind
    it -- and it is not the *only* naive formula in use for this purpose;
    see the CMYK entry under the module docstring's "Documented, not
    implemented" section for how much that choice can matter.
    """
    if colour is None:
        return None
    if isinstance(colour, int | float):
        g = float(colour)
        return (g, g, g)
    if isinstance(colour, list | tuple):
        components = cast("list[object] | tuple[object, ...]", colour)
        values = [float(v) for v in components if isinstance(v, int | float)]
        if len(values) == 1:
            g = values[0]
            return (g, g, g)
        if len(values) == 3:
            return (values[0], values[1], values[2])
        if len(values) == 4:
            c, m, y, k = values
            return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return None


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    """WCAG relative luminance: linearize each channel, then weight them."""

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    """The WCAG contrast ratio between two colours, always `>= 1.0`."""
    la, lb = _relative_luminance(a), _relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def is_explicit_colour(colour: object) -> bool:
    """True when `colour` was set by a real content-stream colour operator,
    not left at `pdfminer`'s internal *never-set* default.

    `pdfminer.pdfinterp.PDFGraphicState` initializes both its stroke and
    fill colour attributes to the literal Python `int` `0` and only ever
    replaces that sentinel with a `float`-valued tuple, built from an
    actual `g`/`rg`/`k`/`scn` operator's parsed operands -- confirmed by
    reading that class directly. A colour that survives untouched from
    that default is therefore always an `int` (or a tuple of them), while
    a colour a document's own content stream chose is always built from
    `float`s, *including* when that choice happens to equal the default
    numeric value. This module depends on that type distinction, not the
    value, because `pdfplumber` reports a stroke colour for every
    character regardless of whether that character's text-rendering mode
    ever paints with it, and does not expose the rendering mode itself --
    so a colour this function calls unset is one this module has no way to
    know is actually meaningful, and one it calls explicit is one a real
    operator in the page's own content stream produced.
    """
    if isinstance(colour, tuple | list):
        values = cast("tuple[object, ...] | list[object]", colour)
        return bool(values) and all(isinstance(v, float) for v in values)
    return isinstance(colour, float)


def page_background(page: Any) -> tuple[float, float, float] | None:
    """The fill colour behind ordinary text on `page`, or `None` if this
    module cannot determine one.

    A page with no explicit background is implicitly white. A page whose
    background was painted (`#set page(fill: ...)`) renders as a single
    filled rectangle covering the page -- measured directly: a page painted
    `rgb(20%, 20%, 40%)` produces exactly one rect spanning `(0, 0)` to
    `(page.width, page.height)` with `non_stroking_color == (0.2, 0.2,
    0.4)`, while an unpainted page produces no rect at all. This looks for
    a filled rect covering at least 90% of the page area -- "substantially
    the whole page," not a decorative panel confined to one region -- and
    uses the last one found if more than one qualifies, since later
    drawing paints over earlier drawing at the same location.

    Finding no such rect and finding one this module cannot read are
    different situations, and this function is written to keep them
    different rather than collapsing both into a default. A gradient or
    tiling-pattern background produces exactly the qualifying rect this
    function looks for, but its `non_stroking_color` is a pattern *name*
    (a `str`), not a colour `to_rgb` can parse -- confirmed directly:
    `#set page(fill: gradient.linear(...))` produces a page-covering rect
    whose colour is the string `"p0"`. Silently falling back to white
    there would mean answering a contrast question about a page this
    module never actually looked at, on a page that is almost certainly
    not white at all. Returning `None` instead means "this page has a
    background, but not one I can read" is reported as its own outcome
    (`background_unknown` in `check_hidden`) rather than folded into either
    "white" or "invisible" -- consistent with this project's rule that a
    gate should say it cannot answer rather than guess and possibly answer
    wrongly. The cost is a real one: an ordinary resume with a legitimate
    gradient header would also report `background_unknown`, not a clean
    pass, until this module (or its future pixel-based replacement) can
    read that kind of fill.
    """
    area = float(page.width) * float(page.height)
    background: tuple[float, float, float] | None = None
    found_unreadable = False
    if area > 0:
        for rect in page.rects:
            if not rect.get("fill"):
                continue
            covered = float(rect.get("width", 0)) * float(rect.get("height", 0))
            if covered / area < 0.9:
                continue
            colour = to_rgb(rect.get("non_stroking_color"))
            if colour is not None:
                background = colour
                found_unreadable = False
            else:
                background = None
                found_unreadable = True
    if found_unreadable:
        return None
    return background if background is not None else (1.0, 1.0, 1.0)


def _invisible_fill(
    page: Any, page_number: int, background: tuple[float, float, float]
) -> list[HiddenFinding]:
    """Flag a glyph whose fill, and whose stroke if one was actually set,
    both fail to contrast against `background`.

    A glyph's visible ink can come from its fill, its stroke, or both --
    PDF's "fill then stroke" text-rendering mode paints the fill first and
    the stroke on top of it -- so a check that only ever reads the fill
    (as this module did before) flags a legitimate stroke-only-visible
    design (`fill: white, stroke: 0.5pt + black`, a classic outlined
    heading) as invisible purely because its *fill* matches the page,
    even though the black outline it actually paints is entirely legible.
    Taking the *better* of the two contrasts -- not requiring both to pass,
    which would flag that same outlined heading on its non-contrasting
    fill; not requiring only one, which would flag ordinary text through
    an unrelated stroke colour, see below -- fixes exactly that false
    positive.

    The stroke is read only when `is_explicit_colour` says it was actually
    set: `pdfplumber` reports *some* stroke colour for every character
    regardless of whether its rendering mode paints with one at all, and
    the unset default happens to be plain black, which would otherwise
    "rescue" the contrast of any invisible white-fill glyph for free,
    reopening the exact channel this check exists to close. See
    `is_explicit_colour` for how that default is told apart from a real
    one.

    Taking the better of the two contrasts does not close every stroke-based
    trick: a stroke wide enough to overpaint most of an otherwise-visible
    fill can still render a glyph blank even though its fill alone would
    contrast fine, and this function has no way to read how wide a stroke
    actually is. See the module docstring's "Documented, not implemented"
    section for the measured evidence and why the alternative (trusting an
    explicit stroke outright) was rejected instead of adopted.
    """
    for char in page.chars:
        text = str(char.get("text", ""))
        if not text.strip():
            continue
        contrasts: list[float] = []
        fill = to_rgb(char.get("non_stroking_color"))
        if fill is not None:
            contrasts.append(contrast_ratio(fill, background))
        raw_stroke = char.get("stroking_color")
        if is_explicit_colour(raw_stroke):
            stroke = to_rgb(raw_stroke)
            if stroke is not None:
                contrasts.append(contrast_ratio(stroke, background))
        if contrasts and max(contrasts) < MIN_CONTRAST_RATIO:
            return [
                HiddenFinding(
                    HiddenKind.INVISIBLE_TEXT,
                    f"page {page_number}: {text!r} does not contrast against its background",
                )
            ]
    return []


def _tiny_text(page: Any, page_number: int) -> list[HiddenFinding]:
    for char in page.chars:
        size = float(char.get("size", 12.0))
        if size < MIN_FONT_SIZE and str(char.get("text", "")).strip():
            return [
                HiddenFinding(HiddenKind.TINY_TEXT, f"page {page_number}: text at {size:.1f}pt")
            ]
    return []


def _offpage_text(page: Any, page_number: int) -> list[HiddenFinding]:
    width, height = float(page.width), float(page.height)
    for char in page.chars:
        if not str(char.get("text", "")).strip():
            continue
        x0, x1 = float(char.get("x0", 0)), float(char.get("x1", 0))
        top, bottom = float(char.get("top", 0)), float(char.get("bottom", 0))
        if x0 < 0 or x1 > width or top < 0 or bottom > height:
            return [
                HiddenFinding(
                    HiddenKind.OFFPAGE_TEXT, f"page {page_number}: glyph outside the page box"
                )
            ]
    return []


def _unicode_channel(page: Any, page_number: int) -> list[HiddenFinding]:
    text = page.extract_text() or ""
    findings: list[HiddenFinding] = []
    if is_default_ignorable(text):
        findings.append(
            HiddenFinding(
                HiddenKind.UNICODE_CHANNEL,
                f"page {page_number}: default-ignorable code points in extracted text",
            )
        )
    if _BIDI_CONTROL.search(text):
        findings.append(
            HiddenFinding(
                HiddenKind.BIDI_OVERRIDE,
                f"page {page_number}: a bidirectional control character reorders extracted text "
                "relative to what a reader sees",
            )
        )
    return findings


def _metadata_leak(pdf_path: Path) -> list[HiddenFinding]:
    reader = PdfReader(str(pdf_path))
    metadata = reader.metadata
    present: set[str] = {str(key) for key in metadata} if metadata is not None else set()
    extra = present - ALLOWED_METADATA
    if not extra:
        return []
    return [HiddenFinding(HiddenKind.METADATA_LEAK, f"unexpected metadata keys: {sorted(extra)}")]


def check_hidden(pdf_path: Path) -> tuple[str, list[HiddenFinding]]:
    """Check `pdf_path` for every channel documented in this module.

    Returns `("fail", findings)` if any channel produced at least one
    finding, else `("pass", [])`. At most one `invisible_text`, `tiny_text`
    or `offpage_text` finding is recorded per page -- each of those checks
    stops at the first glyph it finds, since the gate's job is deciding
    whether the document is trustworthy at all, not enumerating every
    offending glyph on it. `unicode_channel` and `bidi_override` are
    independent of each other and of the per-glyph checks, so a page can
    contribute findings of several kinds at once; `metadata_leak` is
    checked once for the whole document, since PDF document-info metadata
    is not a per-page concept.

    When `page_background` cannot determine a page's background, this
    reports `background_unknown` for that page instead of running
    `invisible_text` against a guessed background -- a page whose
    background this module cannot read is a page it cannot answer the
    contrast question on, and `background_unknown` says so explicitly
    rather than the page silently passing `invisible_text` by skipping it
    with no signal at all.

    `bidi_override` earns a finding kind separate from `unicode_channel`
    because the two name different harms. Every other channel in this
    module hides a payload a reader never sees at all; a bidirectional
    override does not hide anything -- text run through a right-to-left
    override renders as fully visible letters in their original glyphs --
    it instead makes the *order* those visible letters extract in diverge
    from the order they're read in. A caller that reacted to every
    `unicode_channel` finding by assuming "there is invisible content here,
    strip it" would draw the wrong conclusion about a bidi override, where
    stripping the control character fixes the *extraction*, but there was
    never any hidden content to begin with -- what's wrong is that the
    visible text and the machine-read text disagree about order. Both
    findings still fire together on the same input, since a bidi control is
    also default-ignorable; the split adds a specific label rather than
    replacing the general one.
    """
    findings: list[HiddenFinding] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_number, page in enumerate(pdf.pages):
            background = page_background(page)
            if background is None:
                findings.append(
                    HiddenFinding(
                        HiddenKind.BACKGROUND_UNKNOWN,
                        f"page {page_number}: background fill could not be determined; "
                        "invisible-text check skipped for this page",
                    )
                )
            else:
                findings.extend(_invisible_fill(page, page_number, background))
            findings.extend(_tiny_text(page, page_number))
            findings.extend(_offpage_text(page, page_number))
            findings.extend(_unicode_channel(page, page_number))
    findings.extend(_metadata_leak(pdf_path))
    return ("fail" if findings else "pass"), findings
