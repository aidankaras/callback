"""The hidden-channel gate.

A rendered PDF can say one thing to a human reader and another to whatever
reads it next -- an ATS parser, a copy-paste into a text box, an LLM fed the
extracted text. Nothing about producing a *visually* correct document rules
that out: PDF text is drawn independently of whether it is visible, sized
legibly, placed on the page, or free of characters that carry no glyph at
all. This module looks for the five channels that let a document diverge
that way, each closing a distinct attack and each blind to the others:

- `invisible_text`: a glyph filled at or near the page background colour.
  Closes plain colour-matching keyword stuffing ("white text on a white
  page"). Misses text tinted just short of the `>= 0.95` floor (`(0.94,)`
  reads as a visible near-white grey, not background), and misses a glyph
  drawn in a colour that matches a *local*, non-white background -- see
  `_is_background`.
- `tiny_text`: a glyph below `MIN_FONT_SIZE`. Closes text shrunk small
  enough that a reader skims past it without ever consciously reading it.
  Misses anything at or above the floor, however densely packed or
  low-contrast; size is the only signal checked.
- `offpage_text`: a glyph whose box falls outside the page on any of the
  four sides. Closes text pushed past a margin with `#place` so it never
  paints inside the visible page area at all. Misses text placed *inside*
  the page box but behind or under other content (an overlapping shape
  drawn on top of it): "off the page" and "obscured on the page" are
  different geometries, and only the first is checked here.
- `metadata_leak`: a document-info dictionary key outside `ALLOWED_METADATA`.
  Closes keyword stuffing in a field no resume viewer ever renders (a
  `/Keywords` entry is real Typst behaviour, produced by
  `#set document(keywords: ...)`, not a hypothetical). Misses content
  hidden inside an *allowed* key's own value, or in XMP metadata, embedded
  files, or any other part of the PDF structure this check never opens.
- `unicode_channel`: a code point in the extracted text that carries no
  reliably visible mark -- zero-width spacing, bidirectional controls,
  variation selectors, or the Unicode tag block. Closes content smuggled
  through characters a renderer draws with zero or near-zero ink even
  though the glyph run "contains" them. See the two-detector split below
  for what each half of this check catches that the other does not.

None of these five is a substitute for another; a document that clears all
five still is not proven free of every conceivable hidden channel (XMP
metadata and embedded-file streams are two channels no check here reads at
all), only free of the ones a resume actually has reason to carry.
"""

from __future__ import annotations

import re
import unicodedata
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

# Every contiguous range of code points that carries no reliably visible
# mark under this project's vendored font: zero-width spacing through the
# right-to-left mark, the explicit bidi embedding/override controls, the
# word-joining and invisible-operator controls, the emoji/text variation
# selectors, and the Unicode tag block used for language/flag tagging.
# Written as integer code points rather than escape sequences or literal
# characters, so the table stays plain ASCII and reviewable at a glance --
# an invisible character sitting directly in this file's source would be
# exactly the kind of thing this module exists to catch a document for
# doing, and would be unreadable in a diff besides.
_INVISIBLE_RANGES: list[tuple[int, int]] = [
    (0x200B, 0x200F),  # ZERO WIDTH SPACE .. RIGHT-TO-LEFT MARK
    (0x202A, 0x202E),  # LRE, RLE, PDF, LRO, RLO (bidi embedding/override)
    (0x2060, 0x2064),  # WORD JOINER .. INVISIBLE PLUS
    (0xFE00, 0xFE0F),  # VARIATION SELECTOR-1 .. VARIATION SELECTOR-16
    (0xE0000, 0xE007F),  # Unicode tag block
]

# This regex and the `category in {"Cf", "Cn"}` test below are
# complementary, not redundant, and both must stay. Checked against real
# code points: this regex matches U+200B, U+202E, U+FE00 and U+FE0F, but
# misses U+FEFF (ZERO WIDTH NO-BREAK SPACE / BOM) and U+00AD (SOFT HYPHEN) --
# neither falls inside any range in `_INVISIBLE_RANGES`. The category test
# catches exactly those two (both are `Cf`), but misses the variation
# selectors, whose Unicode category is `Mn` (a combining mark), not `Cf`.
# Deleting either check as "the same thing" reopens whatever it alone was
# catching.
_INVISIBLE = re.compile("[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _INVISIBLE_RANGES) + "]")

# The bidirectional-control subset of `_INVISIBLE_RANGES` plus the modern
# isolate controls (U+2066-U+2069), checked on its own because this
# specific family of characters causes a harm `unicode_channel` does not
# name: they don't hide a payload the way a zero-width space does, they
# make the *visible* glyphs paint in a different order than the extracted
# text reports them in. A right-to-left override still shows every one of
# its visible letters -- nothing about it is invisible -- but a naive
# reader extracts them out of order: content and reading order have
# diverged, which is a distinct failure from content being concealed. See
# `check_hidden`'s docstring for why this earns a finding kind of its own
# rather than being folded into `unicode_channel`.
_BIDI_RANGES: list[tuple[int, int]] = [
    (0x202A, 0x202E),  # LRE, RLE, PDF, LRO, RLO
    (0x2066, 0x2069),  # LRI, RLI, FSI, PDI
]
_BIDI_CONTROL = re.compile("[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _BIDI_RANGES) + "]")


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


def _is_background(colour: object) -> bool:
    """True when `colour` is close enough to white to read as invisible ink.

    `pdfplumber`'s `non_stroking_color` is a greyscale `(g,)` tuple, an RGB
    `(r, g, b)` tuple, a CMYK 4-tuple, or occasionally a bare number for a
    greyscale fill applied outside a `(...)`-wrapped colour space -- all
    three shapes are handled the same way here, since "every component is
    near its channel's maximum" means near-white regardless of how many
    components there are. `>= 0.95` rather than `== 1.0` catches a fill
    picked to *read* as invisible against a white page without literally
    being pure white (a printer-safe "off-white" of `(0.97, 0.97, 0.97)`,
    say), while still passing genuinely visible near-white greys used as
    deliberate, legible design choices -- this project's own clean render
    never produces anything above `(0.0,)` (pure black) for real text, so
    there is no observed legitimate near-white body text to false-positive
    against today.

    What this function actually computes is "is this fill near-white," used
    as a proxy for "does this fill match the page background" -- a proxy
    that is exactly correct only because every page this project renders has
    a plain white background and no coloured blocks behind text. If a
    template ever painted a coloured banner or shaded panel behind text,
    this proxy inverts in both directions at once: legitimate white text set
    against that panel for contrast would be flagged as invisible (a false
    positive), while genuinely hidden text drawn to match that panel's own
    colour -- navy-on-navy, say -- would score low on every channel and pass
    straight through undetected (a false negative). Checking against each
    glyph's *local* background (the fill of whatever shape sits directly
    beneath it, from `page.rects`/`page.curves`) would close both gaps, but
    is unneeded while nothing this project renders puts a fill behind text
    at all.
    """
    if colour is None:
        return False
    if isinstance(colour, int | float):
        return float(colour) >= 0.95
    if isinstance(colour, list | tuple):
        components = cast("list[object] | tuple[object, ...]", colour)
        values = [float(v) for v in components if isinstance(v, int | float)]
        return bool(values) and all(v >= 0.95 for v in values)
    return False


def _invisible_fill(page: Any, page_number: int) -> list[HiddenFinding]:
    for char in page.chars:
        text = str(char.get("text", ""))
        if text.strip() and _is_background(char.get("non_stroking_color")):
            return [
                HiddenFinding(
                    HiddenKind.INVISIBLE_TEXT,
                    f"page {page_number}: {text!r} drawn in the background colour",
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
    if _INVISIBLE.search(text) or any(unicodedata.category(ch) in {"Cf", "Cn"} for ch in text):
        findings.append(
            HiddenFinding(
                HiddenKind.UNICODE_CHANNEL,
                f"page {page_number}: formatting or unassigned code points in extracted text",
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
    also caught by the general scan (it falls in `_INVISIBLE_RANGES` and is
    category `Cf`); the split adds a specific label rather than replacing
    the general one.
    """
    findings: list[HiddenFinding] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_number, page in enumerate(pdf.pages):
            findings.extend(_invisible_fill(page, page_number))
            findings.extend(_tiny_text(page, page_number))
            findings.extend(_offpage_text(page, page_number))
            findings.extend(_unicode_channel(page, page_number))
    findings.extend(_metadata_leak(pdf_path))
    return ("fail" if findings else "pass"), findings
