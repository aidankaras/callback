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

- `invisible_text`: a glyph whose fill fails to contrast against the fill
  actually behind it. Closes colour-matching keyword stuffing, including
  against a non-white page background (a coloured banner or panel), and
  does not flag legible text set against such a background purely for
  visual contrast. Misses a fill that is contrast-adequate but drawn behind
  a *different*, opaque shape occupying the same region (that is `pdfplumber`'s
  reported fill colour disagreeing with what a viewer actually sees, a
  distinct mechanism from anything checked here) and misses opacity
  entirely -- see "Documented, not implemented" below.
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
  as "specified to have no visible glyph." Closes content smuggled through
  characters a compliant renderer draws with no ink even though the glyph
  run "contains" them. A right-to-left or left-to-right override is also
  flagged separately as `bidi_override`; see `check_hidden`'s docstring for
  why.

None of these five is a substitute for another; a document that clears all
five still is not proven free of every conceivable hidden channel (XMP
metadata and embedded-file streams are two channels no check here reads at
all), only free of the ones a resume actually has reason to carry.

**Documented, not implemented.** Three further channels were found by
direct measurement against real renders and are not checked here, because
closing any of them needs content-stream analysis this module's
colour/size/position approach cannot reach -- reading the operators that
built the page, not just the geometry `pdfplumber` derives from them. A
half-implementation of any of these would be worse than stating the gap:

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
# covers: code points explicitly specified to render with no visible glyph,
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
    (0x00AD, 0x00AD),  # SOFT HYPHEN
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
    """True if `text` contains any code point in `Default_Ignorable_Code_Point`."""
    return _DEFAULT_IGNORABLE.search(text) is not None


class HiddenKind(StrEnum):
    """Every reason `check_hidden` can attach to a `HiddenFinding`."""

    INVISIBLE_TEXT = "invisible_text"
    TINY_TEXT = "tiny_text"
    OFFPAGE_TEXT = "offpage_text"
    METADATA_LEAK = "metadata_leak"
    UNICODE_CHANNEL = "unicode_channel"
    BIDI_OVERRIDE = "bidi_override"


@dataclass(frozen=True)
class HiddenFinding:
    """A single reason a document failed the hidden-channel gate."""

    kind: str
    detail: str


def to_rgb(colour: object) -> tuple[float, float, float] | None:
    """Normalize a `pdfplumber` fill colour to `(r, g, b)` in `[0, 1]`.

    `non_stroking_color` arrives as a greyscale `(g,)` tuple, an RGB
    `(r, g, b)` tuple, a CMYK 4-tuple, or a bare number for a greyscale fill
    applied outside a colour-space wrapper. CMYK is converted with the
    naive, uncalibrated formula PDF viewers use when no ICC profile is
    attached (`r = (1-c)(1-k)`, and so on) -- exact colour management is not
    the point here, only recovering enough of the colour to judge whether a
    human could tell it apart from what's behind it.
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


def page_background(page: Any) -> tuple[float, float, float]:
    """The fill colour behind ordinary text on `page`.

    A page with no explicit background is implicitly white. A page whose
    background was painted (`#set page(fill: ...)`) renders as a single
    filled rectangle covering the page -- measured directly: a page painted
    `rgb(20%, 20%, 40%)` produces exactly one rect spanning `(0, 0)` to
    `(page.width, page.height)` with `non_stroking_color == (0.2, 0.2,
    0.4)`, while an unpainted page produces no rect at all. This looks for
    a filled rect covering at least 90% of the page area -- "substantially
    the whole page," not a decorative panel confined to one region -- and
    takes the last one found if more than one qualifies, since later
    drawing paints over earlier drawing at the same location.
    """
    area = float(page.width) * float(page.height)
    background: tuple[float, float, float] | None = None
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
    return background if background is not None else (1.0, 1.0, 1.0)


def _invisible_fill(
    page: Any, page_number: int, background: tuple[float, float, float]
) -> list[HiddenFinding]:
    for char in page.chars:
        text = str(char.get("text", ""))
        if not text.strip():
            continue
        fg = to_rgb(char.get("non_stroking_color"))
        if fg is None:
            continue
        if contrast_ratio(fg, background) < MIN_CONTRAST_RATIO:
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
            findings.extend(_invisible_fill(page, page_number, background))
            findings.extend(_tiny_text(page, page_number))
            findings.extend(_offpage_text(page, page_number))
            findings.extend(_unicode_channel(page, page_number))
    findings.extend(_metadata_leak(pdf_path))
    return ("fail" if findings else "pass"), findings
