"""The round-trip gate.

The claim this gate supports is narrow and deliberately so: a naive parser
that bands words into rows by vertical position and reads each row left to
right (`core.checks.naive_extract`) can still recover every critical field
from the rendered document, and can recover the document's entries in the
order they were declared. It is not a claim of compatibility with any named
applicant-tracking system, whose internals are unpublished.

Two properties are checked, and they are kept apart deliberately because a
document can fail either one without failing the other:

- **Recovery**: is each critical field's text present *somewhere* on the
  page? A field absent from every segment is a fabrication risk as much as
  a parsing risk -- if it isn't on the page at all, no parser, careless or
  careful, will ever produce it.
- **Order**: does a careless parser encounter the document's entries in the
  order it declared them? A two-column layout can make every field
  substring-recoverable while still destroying this -- the content stream
  and a position-based reader disagree about which text comes first, and a
  presence-only check cannot tell the two cases apart. Measured on a
  24-entry two-column fixture (`tests.fixtures.make_two_column`): every
  declared entry heading is recoverable by substring search, while 11 of
  them are unreachable in declared order.

Order is checked across entries, by heading only, not within an entry: the
template lays an entry's heading and dates on one grid row with its
subheading on the line below, so a field-by-field sequence taken from the
model's declaration order does not match any order the template produces --
that mismatch is a layout detail, not evidence of scrambled reading order.
Headings are looked up from a moving cursor rather than independently,
because a resume legitimately repeats values (several entries sharing one
date span and one location is ordinary); independent first-match lookup
would resolve every repeated value back to the first entry that used it and
manufacture inversions in a document that reads perfectly in order.

`char_ratio` compares like with like: the summed length of every string the
document declares that a naive parser actually recovered, over the summed
length of everything it declares (every critical field, plus section titles
and bullet text, which are not gated individually but still count toward
what "recoverable" means for the document as a whole). Comparisons are
casefolded throughout, because the template renders section titles through
`upper()` -- that is a rendering decision the gate should not punish as
content loss.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from core.checks.naive_extract import naive_segments, normalize
from core.resume.models import ResumeDoc

# Below this fraction of declared characters recovered, the document is
# failing to survive being read by a naive parser even where individual
# critical fields still happen to be present -- see the module docstring's
# `char_ratio` paragraph for what "declared characters" counts.
_CHAR_RATIO_THRESHOLD = 0.98


@dataclass(frozen=True)
class FieldResult:
    """Whether one critical field's declared text was found anywhere on the
    page, and the first segment it was found in."""

    field: str
    value: str
    recovered: bool
    segment: str | None


@dataclass
class RoundTripResult:
    """The gate's verdict, plus everything a reader needs to find the cause.

    `out_of_order` and the "critical fields not recoverable" finding are
    kept as separate signals rather than folded together, because they name
    different defects: a field in `out_of_order` was found somewhere on the
    page (it is not a `critical` failure) but not from the reading position
    its predecessor left behind, while a `critical` entry with
    `recovered=False` was not found at all. Sending a reader to "the layout
    scrambles reading order" when the real defect is "this text never made
    it onto the page" (or the reverse) is exactly the failure mode the two
    signals exist to prevent.
    """

    # `default_factory=list[FieldResult]`, not bare `list`: pyright strict
    # cannot bind a bare `list` factory against a `list[SomeType]` target
    # without widening the field to `list[Unknown]` -- the same reasoning as
    # `Fact.derived` in `core.profile.models`.
    critical: list[FieldResult] = field(default_factory=list[FieldResult])
    char_ratio: float = 1.0
    order_ok: bool = True
    out_of_order: list[str] = field(default_factory=list[str])
    verdict: str = "pass"
    findings: list[str] = field(default_factory=list[str])


@dataclass(frozen=True)
class _Declared:
    """One string the rendered document is expected to contain.

    `critical` marks the fields whose absence fails the document outright
    (identity plus every entry header field); section titles and bullet
    text are not individually gated -- a missing bullet is a `char_ratio`
    concern, not a hard failure on its own -- but still count toward what
    `char_ratio` measures, since they are still text the document declared
    and a naive parser was asked to recover.
    """

    field: str
    value: str
    critical: bool


def _declared_fields(doc: ResumeDoc) -> list[_Declared]:
    out: list[_Declared] = [
        _Declared("name", doc.identity.name, critical=True),
        _Declared("email", doc.identity.email, critical=True),
    ]
    if doc.identity.phone:
        out.append(_Declared("phone", doc.identity.phone, critical=True))
    index = 0
    for section_index, section in enumerate(doc.sections):
        out.append(_Declared(f"section_title[{section_index}]", section.title, critical=False))
        for entry in section.entries:
            out.append(_Declared(f"employer[{index}]", entry.heading, critical=True))
            if entry.subheading:
                out.append(_Declared(f"title[{index}]", entry.subheading, critical=True))
            if entry.location:
                out.append(_Declared(f"location[{index}]", entry.location, critical=True))
            if entry.start:
                span = f"{entry.start} - {entry.end}" if entry.end else entry.start
                out.append(_Declared(f"dates[{index}]", span, critical=True))
            for bullet_index, bullet in enumerate(entry.bullets):
                out.append(
                    _Declared(f"bullet[{index}][{bullet_index}]", bullet.text, critical=False)
                )
            index += 1
    return out


def _entry_headings(doc: ResumeDoc) -> list[str]:
    """Every entry's heading, in declared order, across all sections.

    This is the anchor sequence the order check walks a cursor through --
    see the module docstring for why headings specifically, and not every
    critical field, are what "declared order" means here.
    """
    return [entry.heading for section in doc.sections for entry in section.entries]


def _flatten(rows: list[list[str]]) -> list[str]:
    """`naive_segments`'s per-row output, read in the order a naive parser
    reads it: row by row, top to bottom, each row's segments already left
    to right."""
    return [segment for row in rows for segment in row]


def _find(value: str, flat: list[str], *, start: int = 0) -> int | None:
    """The position of the first segment at or after `start` that contains
    `value`, both sides normalized and casefolded, or `None`.

    `start` is inclusive rather than exclusive of a previous match's
    position: two anchors can legitimately share one segment (a merged
    field, or a heading that is itself a substring of the next one), and
    requiring strictly-increasing positions would report that as an order
    failure it isn't. `naive_segments` already normalizes its output, so
    only `value` needs it here, but both sides are casefolded regardless of
    which one produced the case difference.
    """
    target = normalize(value).casefold()
    if not target:
        return None
    for i in range(start, len(flat)):
        if target in flat[i].casefold():
            return i
    return None


def _field_result(d: _Declared, position: int | None, flat: list[str]) -> FieldResult:
    # Narrowing `position` here, rather than indexing `positions[d.field]`
    # twice inline at the call site, is load-bearing for pyright: two
    # separate dict lookups on the same key are two separate expressions to
    # the type checker, so it cannot carry the `is not None` check on one
    # into the `flat[...]` index on the other.
    return FieldResult(
        field=d.field,
        value=d.value,
        recovered=position is not None,
        segment=flat[position] if position is not None else None,
    )


def check_roundtrip(doc: ResumeDoc, pdf_path: Path) -> RoundTripResult:
    """Check whether a naive, position-based parser reading `pdf_path` can
    recover `doc`'s critical fields, in the order `doc` declares its
    entries, with the character volume `doc` declares.

    See the module docstring for what each of the three checks means and
    why they are reported separately.
    """
    flat = _flatten(naive_segments(pdf_path))
    findings: list[str] = []

    declared = _declared_fields(doc)
    positions = {d.field: _find(d.value, flat) for d in declared}

    critical = [_field_result(d, positions[d.field], flat) for d in declared if d.critical]
    missing = [f.field for f in critical if not f.recovered]
    if missing:
        findings.append(f"critical fields not recoverable by a naive parser: {missing}")

    recovered_chars = sum(
        len(normalize(d.value)) for d in declared if positions[d.field] is not None
    )
    total_chars = sum(len(normalize(d.value)) for d in declared)
    char_ratio = 1.0 if total_chars == 0 else recovered_chars / total_chars
    if total_chars and char_ratio < _CHAR_RATIO_THRESHOLD:
        findings.append(
            f"only {char_ratio:.0%} of declared characters recoverable by a naive parser"
        )

    out_of_order: list[str] = []
    cursor = 0
    for heading in _entry_headings(doc):
        forward = _find(heading, flat, start=cursor)
        if forward is not None:
            cursor = forward
            continue
        # Not reachable from the current reading position. Distinguish a
        # heading a naive parser will never reach in order (present, just
        # earlier on the page) from one that isn't on the page at all --
        # the latter is already reported above as a `critical` recovery
        # failure on this heading's `employer[i]` entry, not an ordering
        # problem.
        if _find(heading, flat) is not None:
            out_of_order.append(heading)
    order_ok = not out_of_order
    if out_of_order:
        findings.append(
            f"entry headings present but not reachable in declared reading order: {out_of_order}"
        )

    with pdfplumber.open(str(pdf_path)) as pdf:
        images = sum(len(page.images) for page in pdf.pages)
    if images:
        findings.append(f"document contains {images} image(s); text must be text")

    verdict = "fail" if findings else "pass"
    return RoundTripResult(
        critical=critical,
        char_ratio=char_ratio,
        order_ok=order_ok,
        out_of_order=out_of_order,
        verdict=verdict,
        findings=findings,
    )
