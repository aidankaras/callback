# 01 — Adversarial extraction

**Decision.** The text extractor behind the round-trip gate is deliberately bad
at reading PDFs, and must stay bad. Improving it weakens the guarantee it
supports.

## The problem

The round-trip gate answers one question: if an automated reader parses this
rendered document, does it recover what the document says? A resume that looks
correct to a person and parses into scrambled fields is worse than one that
looks plain, because the failure is invisible to the person it affects.

The obvious way to answer that question is to extract the text with the best
parser available and compare. That measurement is worthless, and the reason is
specific rather than philosophical: Typst writes its content stream in logical
order, so any extractor that honours stream order — including `pdfplumber`'s own
default text-flow mode — reconstructs even a two-column layout perfectly.
Measuring with a good parser therefore measures agreement between the renderer
and the reader, not whether a careless reader can recover the text at all.

## What this does instead

`core/checks/naive_extract.py` models the careless reader. It ignores stream
order entirely, groups words into rows by vertical position, reads each row left
to right, and treats a wide horizontal gap inside a row as a field boundary
rather than a space.

Two consequences look like defects and are not:

- **It bands rows by `round(top / y_tolerance)`**, while its sibling
  `core/render/geometry.py` argues at length that rounding is the wrong way to
  group lines and clusters by absolute distance instead. Both are correct.
  `geometry.py` reports what the renderer really produced, and a miscount there
  is a wrong layout verdict. This module approximates what a parser with no
  care would do, and crude banding is part of the pessimism.
- **`use_text_flow=False` is set at every call site** even though, with the
  current algorithm, both settings return the same words: the banding and the
  per-row sort discard the ordering difference. It is kept as insurance, because
  the moment any path preserves input order the flag is the only thing standing
  between this module and stream-order recovery.

## The failure this guards against

Making the extractor smarter does not break a test. It silently widens what the
gate accepts. A scrambled two-column layout that currently fails would re-cohere
into readable order under a better extractor, and the gate would pass it while
reporting the same green verdict it reports for a genuinely clean document. That
is why the reasoning lives here and in the module docstring rather than in a
review comment: the change that destroys this guarantee looks like an
improvement.

## The question a reviewer asks

*Isn't failing a document that a real applicant-tracking system would parse fine
just a false alarm?*

Sometimes, and the project accepts that asymmetry deliberately. A concrete case:
an employer name long enough to wrap, rendered beside a wide right-aligned date
column, interleaves its own fragments with the date text. Measured on a real
render, the heading's three rows come back as
`'Company 12 Extended International <location> <dates>'`, `'Holdings Group
Corporation'`, `'Worldwide Services Division'`, and the document fails with 60%
of its declared characters recoverable. No parser reading by position can
reassemble that heading, so the verdict is correct even though a person reading
the page has no difficulty.

The remedy for that document is a layout change, not a looser gate. What the
gate must never do is pass it quietly.

## Implemented by

- `core/checks/naive_extract.py` — the extractor, with the rounding and
  `use_text_flow` decisions documented at the point a reader meets them.
- `core/checks/roundtrip.py` — the gate built on it.
- `tests/fixtures/make_two_column.py` — the adversarial fixture, which records
  both bounds of the entry count that keeps it adversarial and names the
  assertion that guards against it degenerating.
