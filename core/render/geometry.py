"""Line and bullet geometry read from the rendered PDF.

Every layout check downstream of this module — bullet length, widow lines,
page fill — reads its answer from here, so a miscount here is a wrong
verdict on every document that follows, not just a cosmetic bug in this
module.

**Why tolerance clustering, not rounding.** Two words on the same visual
line can be reported by the PDF at slightly different `top` values when
they carry different font sizes or styles — an italic run and a regular
run sharing a baseline can differ by as little as 0.03pt because of font
metric differences, not because either digit is imprecise. Rounding `top`
to one decimal place does not fix this: it merges lines that happen to
round to the same tenth and splits lines that straddle a rounding boundary,
in either direction, with no relationship to which lines are actually
adjacent on the page. Measured directly on this project's own resume
fixture, naive rounding counted a heading and its right-aligned
date/location run — one visual line — as two. Clustering instead merges
words whose `top` values are close in absolute points.

The tolerance is **one value for the whole call**, computed as
`tolerance_ratio * font_size` from the caller's declared body size — it is
not re-derived per line from each line's own rendered size. That is
deliberate rather than an omission. Scaling per line would give this
template's 20pt name a 6.0pt tolerance, making a false *merge* of two
genuinely adjacent lines more likely in order to fix a false split that no
measurement exhibits. Measured with `extract_lines` on renders of
`templates/resume.typ` (10.5pt body, `leading: 0.63em`): words on one visual
line share a `top` exactly (0.000pt spread), because every right-hand cell is
set at its own row's size; the smallest distance between two adjacent lines'
tops is 11.34pt on a full one-page, 49-line resume and 11.55pt on
`tests/test_geometry.py`'s fixture; and the line count is unchanged at every
tolerance from 0.5pt to 9pt.

So the number that matters to a future reader is the margin, not the ratio:
the default 3.0pt tolerance sits against an 11.34pt smallest line-to-line
distance, roughly 3.8x. Note that this distance is the line pitch, not the
`leading` value, which is only the space between lines. A template with much
tighter line spacing, a row mixing sizes whose tops differ by more than the
tolerance, or a caller passing a `font_size` well above the body size is what
breaks this: the first and last by collapsing two real lines into one, the
mixed row by splitting one line in two. Re-measure before changing any of them.

**Why PDF geometry, not Typst introspection.** Two alternatives were tried
before this one and both measurably fail on this project's own bullets:
`typst.query` reports identical start and end positions for adjacent list
items, so a height-based line count reads zero lines for most bullets; and
`context measure()` returns the *ink* height of a text block, not its line
pitch, so `height / ink` produced 3 and 5 for bullets that render as 2 and
3 lines. Clustering words extracted directly from the compiled PDF is the
first approach that gave correct counts against a real render, and it
stays correct across a Typst upgrade because it never depends on Typst's
own account of its layout — only on where the ink actually landed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber


@dataclass(frozen=True)
class Word:
    """A single extracted token and the box it occupies on its page."""

    text: str
    x0: float
    x1: float
    top: float


@dataclass
class Line:
    """One visual line: a cluster of words judged to share a baseline.

    `top` is the position of the word that started the cluster, not a
    running average of every word later merged into it — see
    `extract_lines` for why an anchor rather than an average is correct
    here.
    """

    page: int
    top: float
    # `default_factory=list[Word]`, not bare `list` — see the same note on
    # `Entry.bullets` in `core.resume.models`: pyright strict can't bind a
    # bare `list` factory against a `list[SomeDataclass]` target without
    # widening the field to `list[Unknown]`.
    words: list[Word] = field(default_factory=list[Word])

    @property
    def text(self) -> str:
        return " ".join(w.text for w in sorted(self.words, key=lambda w: w.x0))

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words)


@dataclass
class BulletBlock:
    """One bullet's marker line plus every continuation line that wraps it.

    `page` is the page the bullet *starts* on — the marker line's page — and
    is not updated when a continuation line wraps onto the next page. The
    measurements are unaffected: `line_count`, `last_line_words` and `text`
    all span the boundary correctly, because continuations attach by
    x-position without consulting the page. Only this single field is
    ambiguous for a cross-page bullet, so a caller doing per-page
    attribution should read each line's own `page` rather than the block's.
    """

    page: int
    lines: list[Line]

    @property
    def line_count(self) -> int:
        return len(self.lines)

    @property
    def last_line_words(self) -> int:
        return len(self.lines[-1].words) if self.lines else 0

    @property
    def text(self) -> str:
        return " ".join(line.text for line in self.lines)


def extract_lines(
    pdf_path: Path, *, tolerance_ratio: float = 0.3, font_size: float = 10.0
) -> list[Line]:
    """Cluster every word in `pdf_path` into visual lines, per page.

    A word joins the first existing line on its page whose anchor `top`
    is within `tolerance_ratio * font_size` points of the word's own
    `top`; otherwise it starts a new line. The anchor is fixed at the
    line's first word and never updated as later words join, so a long
    line can't drift its effective position one small step at a time —
    every word on it is compared against the same reference the line
    started with.
    """
    tolerance = tolerance_ratio * font_size
    lines: list[Line] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_number, page in enumerate(pdf.pages):
            current: list[Line] = []
            words = sorted(
                page.extract_words(use_text_flow=False),
                key=lambda w: (w["top"], w["x0"]),
            )
            for raw in words:
                word = Word(
                    text=str(raw["text"]),
                    x0=float(raw["x0"]),
                    x1=float(raw["x1"]),
                    top=float(raw["top"]),
                )
                for line in current:
                    if abs(line.top - word.top) <= tolerance:
                        line.words.append(word)
                        break
                else:
                    current.append(Line(page=page_number, top=word.top, words=[word]))
            lines.extend(sorted(current, key=lambda ln: ln.top))
    return lines


def bullet_blocks(
    lines: list[Line],
    *,
    marker: str = "\N{BULLET}",
    continuation_slack: float = 1.0,
) -> list[BulletBlock]:
    """Group `lines` into bullet blocks by finding the marker glyph.

    A line starts a new block when its first word (by x-position) is
    exactly the marker glyph; the marker word itself is dropped from the
    block's own lines, since it carries no text a length or wrap check
    should count. A later line joins the open block as a continuation
    when its own leftmost x-position matches the block's body
    x-position — the column the marker's template grid cell wraps
    text into — within `continuation_slack` points. Any other line (the
    next entry's heading, a subheading) closes the open block by resetting
    the body x-position tracked below, so it can't be mistaken for a
    further continuation of a bullet that has already ended.

    `continuation_slack` is horizontal and absolute, unlike
    `extract_lines`'s vertical tolerance, which is proportional — the two
    are separate mechanisms and neither default is derived from the other.
    Its 1.0pt default is safe here only because of a specific template
    fact: `templates/resume.typ` pads each bullet 4pt and puts the marker in
    its own 10pt grid column (`grid(columns: (10pt, 1fr), [•], ...)`), so
    bullet bodies begin 14pt right of the flush-left x-position that section
    headings and subheadings are typeset at (39.6pt against 53.6pt, measured
    on a render). That is a 14x margin against the nearest
    thing this test could confuse a continuation with. A template that
    indented headings to near the bullet body column, or narrowed that grid
    column, would need this re-measured rather than re-guessed — the
    failure mode is a heading silently absorbed into the preceding bullet,
    inflating its `line_count`.

    This depends on the marker being real, extractable text at a
    distinct x-position rather than a drawn shape — see the module
    docstring's "PDF geometry, not Typst introspection" note, and
    `tests/test_geometry.py::test_bullet_marker_is_literal_text`, which
    checks that assumption directly against the current template.
    """
    blocks: list[BulletBlock] = []
    body_x0: float | None = None
    for line in lines:
        words = sorted(line.words, key=lambda w: w.x0)
        if words and words[0].text == marker:
            body = Line(page=line.page, top=line.top, words=words[1:])
            body_x0 = body.x0 if body.words else body_x0
            blocks.append(BulletBlock(page=line.page, lines=[body]))
        elif blocks and body_x0 is not None and abs(line.x0 - body_x0) < continuation_slack:
            blocks[-1].lines.append(line)
        else:
            body_x0 = None
    return blocks


# The template's top and bottom page margin, in points (0.45in).
TEMPLATE_Y_MARGIN = 0.45 * 72


def page_fill(pdf_path: Path) -> list[float]:
    """The fraction of each page's usable vertical space that carries text.

    "Usable" excludes `TEMPLATE_Y_MARGIN` top and bottom, the template's own
    `set page(margin: (..., y: 0.45in))`. A PDF does not record its margins,
    so this is a copy, kept in step by
    `tests/test_geometry.py::test_page_fill_margin_matches_the_template`:
    when the copy went stale once, a full page measured 1.003. Pages
    with no extracted words fill 0.0 rather than raising, since an empty
    page is a legitimate (if unusual) input, not an error.
    """
    fills: list[float] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            if not words:
                fills.append(0.0)
                continue
            top = min(float(w["top"]) for w in words)
            bottom = max(float(w["bottom"]) for w in words)
            usable = float(page.height) - 2 * TEMPLATE_Y_MARGIN
            fills.append((bottom - top) / usable)
    return fills
