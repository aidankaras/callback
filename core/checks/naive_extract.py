"""A deliberately pessimistic text extractor.

Typst writes its content stream in logical order, so any extractor that
honours that order — including `pdfplumber`'s own default text-flow mode —
reconstructs even a two-column layout perfectly. Measuring a rendered
document with the best available parser therefore measures self-consistency
between the renderer and the reader, not whether a hostile or careless
reader can recover the intended text at all. This module models that
careless reader instead: it ignores the content stream's order entirely,
re-derives reading order purely from where ink landed on the page — banding
words into rows by vertical position, then reading each row left to right —
and treats a large horizontal gap within a row as a field boundary rather
than a space. A round-trip check built on top of this module is checking
something real: that a document also reads correctly under an extractor with
no privileged knowledge of how it was produced.

**Why this module rounds `top` when `core.render.geometry` deliberately does
not.** `geometry.py`'s module docstring explains at length why *rounding* a
word's `top` to form a row key is the wrong way to group a rendered PDF's
lines: two words on the same visual line can differ in `top` by a few
hundredths of a point purely from font-metric noise, and rounding can just as
easily split that one line as merge two genuinely different ones, depending
on which side of a rounding boundary each word's noise happens to land.
`geometry.py` clusters by absolute distance instead, because its job is to
report what the renderer actually produced, and getting that count right
matters to every layout check downstream of it.

This module's job is the opposite: to model what a parser with *no* such
care would do. A real naive reader — the kind a round-trip gate needs to
survive being read by — does not carry a font-metrics-aware clustering
tolerance; something as crude as `round(top / y_tolerance)` is a realistic
stand-in for "whatever the cheapest correct-looking grouping is," and using
it here is the pessimism, not an oversight. Giving this module `geometry.py`'s
tolerance clustering instead would make it a *better* reader than the naive
baseline it exists to approximate, which would let a genuinely scrambled
two-column layout re-cohere into readable order under this extractor too —
silently weakening the round-trip check that depends on this module actually
failing to read documents like that.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import pdfplumber

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")  # noqa: RUF001
_QUOTES = {ord("“"): '"', ord("”"): '"', ord("‘"): "'", ord("’"): "'"}  # noqa: RUF001
_DROP = dict.fromkeys(map(ord, "•­​‌‍﻿"), None)


def normalize(text: str) -> str:
    """Fold `text` down to a form that ignores rendering-level noise.

    Space-class coverage comes from `\\s` in the final `re.sub`, not from
    `NFKC`: Python's `\\s` matches every Unicode space-separator codepoint
    directly, including ones `NFKC` leaves alone — OGHAM SPACE MARK
    (U+1680) is untouched by `NFKC` but still matches `\\s`. So `\\s+`
    collapsing already covers the whole space class on its own; no explicit
    space table is needed, and `NFKC` folding some of those same codepoints
    is redundant with it rather than load-bearing. `NFKC`'s real job here is
    compatibility-form folding that neither `\\s` nor the tables below can
    do — a ligature such as `ﬁ` decomposes to `fi`, and a fullwidth Latin
    punctuation form used by some CJK input methods collapses to its
    ordinary ASCII form — not the dash or quote folding: `NFKC` leaves
    curly quotes and en/em dashes untouched, which is exactly why `_DASHES`
    and `_QUOTES` exist as explicit tables rather than relying on it.

    What survives both `NFKC` and `\\s` is the handful of zero-width and
    format characters below (`_DROP`): a soft hyphen, a zero-width space,
    and the bidi/BOM control characters. This pipeline's own rendering never
    produces the soft hyphen case — both `templates/resume.typ` and the
    two-column test fixture set `hyphenate: false`, so nothing this project
    renders ever emits one — but candidate-supplied source text pasted in
    from elsewhere can carry one already embedded, e.g. from a document
    that auto-hyphenated before the text was copied. These characters are
    invisible to `\\s`, so a naive parser reading such a line literally
    would see `infra\\u00adstructure` as one unbroken token unless something
    removes the soft hyphen explicitly.
    """
    folded = unicodedata.normalize("NFKC", text)
    folded = folded.translate(_DASHES | _QUOTES | _DROP)
    return re.sub(r"\s+", " ", folded).strip()


def naive_segments(
    pdf_path: Path, *, y_tolerance: float = 3.0, gap_factor: float = 2.0
) -> list[list[str]]:
    """Read `pdf_path` the way a careless parser would: by position, not by
    the order the content stream declares.

    Words are grouped into rows by `round(top / y_tolerance)` — see the
    module docstring for why rounding, not clustering, is deliberate here —
    and each row is then read left to right by `x0`. Within a row, a
    horizontal gap wider than `gap_factor` times the page's median
    per-character advance (`_space_advance`) is treated as a field boundary
    and starts a new segment; anything narrower is treated as an ordinary
    word space and folded into the current segment's text. This is what
    turns "Northwind Analytics␣␣␣␣␣␣␣␣␣Boston, MA" — one row, two fields
    separated by a wide templated gap — into two segments instead of one
    run-on string, while leaving an ordinary "in Boston" two-word phrase as
    a single segment.

    The default `gap_factor=2.0` was checked against this project's own
    fixtures rather than assumed: on the header line of a rendered entry,
    ordinary inter-word gaps measure 2.25-4.5pt, comfortably under the
    ~9.6pt threshold that `gap_factor=2.0` sets against that page's ~4.8pt
    median advance, while the templated gap between an employer name and
    its right-aligned location is 308.9pt — 32x over the threshold. On the
    two-column adversarial fixture, the column gutter measures 111.8pt —
    12x over its page's ~8.8pt threshold. Both real field boundaries clear
    the threshold by more than an order of magnitude while ordinary spacing
    stays well under it, so `gap_factor=2.0` is not a fragile default here.

    Returns one list of segments per row, in row order top to bottom, with
    empty segments (a row that produced no non-blank text) filtered out.
    """
    out: list[list[str]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            # No sort needed on the raw word list: `bands` below groups by
            # row regardless of input order, and each row is sorted by `x0`
            # on its own line just after. Sorting here first would be
            # thrown away by that later sort and never observed in the
            # output — do not add it back "for clarity."
            #
            # `use_text_flow=False` is currently unobservable for the same
            # reason: both settings return the same multiset of words, and
            # the banding plus per-row `x0` sort below discard whatever
            # order `extract_words` handed back. It costs nothing to keep
            # and is deliberate insurance, not a live safeguard — it is
            # exactly what would matter if this function ever grew a code
            # path that relied on input order without re-sorting. Remove it
            # only alongside proof that no such path exists.
            words = page.extract_words(use_text_flow=False)
            space = _space_advance(words)
            bands: dict[int, list[dict[str, Any]]] = {}
            for word in words:
                bands.setdefault(round(float(word["top"]) / y_tolerance), []).append(word)
            for key in sorted(bands):
                row = sorted(bands[key], key=lambda w: float(w["x0"]))
                segments: list[str] = []
                buffer: list[str] = []
                previous_x1: float | None = None
                for word in row:
                    x0, x1 = float(word["x0"]), float(word["x1"])
                    if previous_x1 is not None and (x0 - previous_x1) > gap_factor * space:
                        segments.append(normalize(" ".join(buffer)))
                        buffer = []
                    buffer.append(str(word["text"]))
                    previous_x1 = x1
                if buffer:
                    segments.append(normalize(" ".join(buffer)))
                out.append([s for s in segments if s])
    return out


def _space_advance(words: list[dict[str, Any]]) -> float:
    """The median per-character glyph width across every word on the page.

    Used as the unit for `gap_factor`: a gap is judged wide relative to how
    wide this page's glyphs typically are, not against a fixed point value,
    so the same `gap_factor` works across a resume set in a slightly larger
    or smaller body size. It is one value for the whole page rather than
    recomputed per row — see `naive_segments` — and the median, not the
    mean, is deliberate: a single very wide or very narrow token would
    otherwise pull the mean toward it and change the effective threshold for
    every other row on the page.

    **What actually moves this value, and what does not.** Per-character
    advance is close to a pure font-metrics property, essentially
    independent of how long a token is: at 10pt on this project's vendored
    font, short words ("the cat sat on a mat") measure 4.45pt/char, a
    66-character URL measures 4.34pt/char, and a 48-character hex hash
    measures 4.56pt/char — all within a few percent of each other. A page
    that happens to contain one long unbroken token (a link, a hash, an ID)
    does not depress this median. Only text made of genuinely narrow glyphs
    would (an artificial `"illlliiiillll..."` string measures 2.67pt/char),
    and no real resume content is written in glyphs that narrow.

    Font *size* is what actually moves it, because this is measured across
    the whole page while `naive_segments` compares it against individual
    rows that may be set at a different size. At the same text and font,
    per-char advance — and the `gap_factor=2.0` threshold it sets — scales
    roughly linearly with size: 7pt measures 3.12pt/char (6.23pt
    threshold), 9pt measures 4.01pt/char (8.02pt), 10pt measures 4.45pt/char
    (8.91pt), 11pt measures 4.90pt/char (9.80pt), and 16pt measures
    7.13pt/char (14.25pt). On this project's real renders, the resume
    page's median is 4.79pt (9.57pt threshold) against ordinary
    within-field gaps of 2.25-4.50pt and an employer/location field gap of
    308.9pt; the two-column fixture's threshold is 8.83pt against ordinary
    gaps of 2.50pt and column gutters of 102.5-111.8pt.

    The composition that could actually break this function's assumption is
    therefore a page whose *dominant* text is small — pulling the page
    median, and so the threshold, down — while one row is set much larger —
    pulling that row's own ordinary word gaps up — not a page of long
    tokens. This project's own template sizes span 9pt to 16pt (the 16pt
    name row's "Dana"/"Vex" gap measures 4.0pt), which puts the worst
    realistic composition at roughly an 8.0pt threshold against a 4.0pt gap
    it must not split: about 2x margin, not the order-of-magnitude margin
    the real field-boundary gaps enjoy above. A template that pushed either
    extreme further — a smaller body size, or a still-larger heading —
    would need this re-measured rather than re-guessed.
    """
    widths = [
        (float(w["x1"]) - float(w["x0"])) / max(len(str(w["text"])), 1)
        for w in words
        if str(w["text"]).strip()
    ]
    if not widths:
        return 3.0
    widths.sort()
    return widths[len(widths) // 2]


def naive_text(pdf_path: Path) -> str:
    """`naive_segments`, flattened to a single string: segments joined by
    tabs, rows joined by newlines. A tab is used rather than a space because
    the whole point of segmenting is to keep a field boundary visible; a
    downstream check that re-splits on whitespace would otherwise have no
    way to tell "one field with an internal space" from "two fields this
    extractor separated."
    """
    return "\n".join("\t".join(line) for line in naive_segments(pdf_path))
