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
  20-entry two-column fixture (`tests.fixtures.make_two_column`): every
  declared entry heading is recoverable by substring search, while 9 of
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

Every other entry-owned field (subheading, location, dates, bullets) is
recovered only from *its own entry's region* of the page, not from anywhere
in the document. Searching the whole document for each field independently
means a value missing from entry 0 can be satisfied by an identical value
that legitimately belongs to entry 1 -- two jobs in the same city is
ordinary, and a location genuinely dropped from one of them must not be
covered for by the other one's. An entry's region runs from its own
heading's position (found by the same forward-moving cursor the order
check uses, advanced strictly past that position so two entries sharing one
heading still claim two different regions) to the next entry's heading
position, or the end of the document for the last entry. An entry whose own
heading could not be found forward of the cursor has no region to scope
into; its other fields fall back to a document-wide search rather than
being reported as a pile of unrelated recovery failures on top of the one
real ordering problem. Identity fields (name, email, phone) and a section's
title belong to no entry and stay document-scoped, since nothing in the
model claims otherwise.

`char_ratio` compares like with like: the summed length of every string the
document declares that a naive parser actually recovered, over the summed
length of everything it declares (every critical field, plus section titles
and bullet text, which are not gated individually but still count toward
what "recoverable" means for the document as a whole). Comparisons are
casefolded throughout, because the template renders section titles through
`upper()` -- that is a rendering decision the gate should not punish as
content loss.

A value long enough to wrap across several rows is reported unrecovered,
and that is correct, not a bug to chase away: matching only ever looks
inside one segment at a time (see `_matches`), and a naive, position-based
reader has no more ability to reassemble a heading split across rows than
this gate does. Checked against its own render, a heading that wraps across
three rows with the location column interleaved between its fragments
(`'Company 12 Extended International A Truly Extremely Long City Name
Metropolitan Area, Massachusetts March 2021 - April 2022'` /
`'Holdings Group Corporation'` / `'Worldwide Services Division'`) fails
with `char_ratio` measuring 0.5982 and the heading itself unrecovered.
Loosening matching to reassemble that heading across segments would defeat
the entire premise this gate checks -- a naive parser reading the same PDF
cannot do it either -- so a future reader who sees this as a failing case
and reaches for a "fix" should reread this paragraph first.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from core.checks.naive_extract import naive_segments, normalize
from core.resume.models import Entry, ResumeDoc

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

    `entry_index` is `None` for a field that belongs to no entry (identity,
    a section's title) and is searched document-wide; otherwise it names
    which entry's region (see `check_roundtrip`) the field must be found
    within. `is_anchor` marks the one field per entry -- its heading -- that
    defines that region rather than being searched inside it: an entry's
    own heading is always searched document-wide too, because scoping it to
    "its own region" would be circular, and because whether a naive parser
    can find a heading *anywhere* is a question independent of whether it
    can find it in the declared reading position (see the module docstring
    on why recovery and order are checked separately).
    """

    field: str
    value: str
    critical: bool
    entry_index: int | None = None
    is_anchor: bool = False


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
            out.append(
                _Declared(
                    f"employer[{index}]",
                    entry.heading,
                    critical=True,
                    entry_index=index,
                    is_anchor=True,
                )
            )
            if entry.subheading:
                out.append(
                    _Declared(f"title[{index}]", entry.subheading, critical=True, entry_index=index)
                )
            if entry.location:
                out.append(
                    _Declared(
                        f"location[{index}]", entry.location, critical=True, entry_index=index
                    )
                )
            if entry.start:
                span = f"{entry.start} - {entry.end}" if entry.end else entry.start
                out.append(_Declared(f"dates[{index}]", span, critical=True, entry_index=index))
            for bullet_index, bullet in enumerate(entry.bullets):
                out.append(
                    _Declared(
                        f"bullet[{index}][{bullet_index}]",
                        bullet.text,
                        critical=False,
                        entry_index=index,
                    )
                )
            index += 1
    return out


def _entries(doc: ResumeDoc) -> list[Entry]:
    """Every entry, in declared order, across all sections.

    This flattened order is what `entry_index` in `_declared_fields`
    counts against, and what the order check and the per-entry regions in
    `check_roundtrip` both walk.
    """
    return [entry for section in doc.sections for entry in section.entries]


def _flatten(rows: list[list[str]]) -> list[str]:
    """`naive_segments`'s per-row output, read in the order a naive parser
    reads it: row by row, top to bottom, each row's segments already left
    to right."""
    return [segment for row in rows for segment in row]


def _matches(value: str, segment: str, declared_values: Sequence[str]) -> bool:
    """Whether `value` can be read out of `segment`, given every other
    string the document declares (`declared_values`).

    Bare substring containment lets a short declared value "win" by
    matching inside a *longer declared value's own text* -- an entry
    heading that happens to be a substring of another entry's heading
    (`"Analytics"` sitting inside a different entry's `"Northwind Analytics
    Group"`), or a fabricated field that happens to be spelled inside an
    unrelated field's genuine text (a wrong `location="NY"` matching inside
    a real subheading `"NY Ops Lead"`). Blocking specifically on the
    *proper containment* relation between two declared values -- not on
    "any other declared value is also present in this segment" -- is what
    keeps two genuinely distinct fields sharing one segment (a location and
    a date span both landing in `"Boston, MA Jun 2024 - Aug 2025"`) from
    blocking each other: neither is a substring of the other, so neither
    blocks the other's match there.

    This rule closes the proper-containment case specifically, and it is
    not a general guarantee against every false match -- two gaps are real,
    and each is closed elsewhere rather than here. First, it exempts equal
    values (`w != v`) so that legitimately repeated content (several
    entries sharing one date span) doesn't block itself, but that same
    exemption means two *equal* values belonging to two different entries
    -- a location genuinely missing from entry 0, satisfied by an identical
    city that legitimately belongs to entry 1 -- pass straight through this
    function unblocked; `check_roundtrip` closes that gap by scoping each
    entry's fields to that entry's own region of the page (see the module
    docstring), not by anything this function does. Second, this function
    only ever inspects one segment at a time, so a value that spans several
    segments (a heading long enough to wrap across two or three rows) never
    appears whole in any single segment and so never reaches the point
    where this rule would even apply -- that case fails for an unrelated
    reason (see the module docstring's closing paragraph), not because this
    rule caught or missed anything.

    Within what it does check, this rule can refuse a legitimate match, but
    it cannot grant an illegitimate one: a segment reading `"NY Ops Lead in
    NY"` contains both the longer declared value `"NY Ops Lead"` and a
    genuinely separate, standalone `"NY"` later in the same segment, and
    this function refuses the shorter value's match there anyway, because
    it reasons about whole-segment containment and cannot tell those two
    occurrences of "NY" apart. A missed recovery is the safe direction for
    a gate whose job is to reject documents it cannot vouch for, so that
    spurious failure is accepted here rather than chased with position
    tracking this module does not otherwise need.
    """
    v = normalize(value).casefold()
    s = segment.casefold()
    if not v or v not in s:
        return False
    return not any(
        (w := normalize(other).casefold()) != v and v in w and w in s for other in declared_values
    )


def _find(
    value: str,
    flat: list[str],
    declared_values: Sequence[str],
    *,
    start: int = 0,
    end: int | None = None,
) -> int | None:
    """The position of the first segment in `[start, end)` that `value` can
    be read out of -- see `_matches` for what that excludes and why.

    `start` is inclusive rather than exclusive of a previous match's
    position: two anchors can legitimately share one segment (a merged
    field), and requiring strictly-increasing positions would report that
    as an order failure it isn't. `end` defaults to the whole of `flat`;
    callers scoping a search to one entry's region (`check_roundtrip`) pass
    the next entry's anchor position instead.
    """
    if not normalize(value):
        return None
    limit = len(flat) if end is None else end
    for i in range(start, limit):
        if _matches(value, flat[i], declared_values):
            return i
    return None


def _entry_spans(anchors: list[int | None], flat_len: int) -> list[tuple[int, int] | None]:
    """The `[start, end)` region each entry's non-heading fields must be
    found within, derived from `anchors` (that entry's heading position, or
    `None` if it couldn't be found forward of the reading cursor -- see
    `check_roundtrip`).

    An entry's region starts at its own anchor and runs to the *next*
    anchor that was actually found, skipping over any entry in between
    whose own heading search failed -- an entry with no anchor of its own
    contributes no boundary, but does not widen its neighbours' regions
    either; it simply has no region (`None`), and `check_roundtrip` falls
    that entry's other fields back to a document-wide search instead of
    scoping them to a region no anchor supports.
    """
    spans: list[tuple[int, int] | None] = []
    for i, position in enumerate(anchors):
        if position is None:
            spans.append(None)
            continue
        following = next((q for q in anchors[i + 1 :] if q is not None), flat_len)
        spans.append((position, following))
    return spans


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
    declared_values = [d.value for d in declared]
    entries = _entries(doc)

    # One anchor per entry: the position of its own heading, found by a
    # cursor that only ever moves forward -- this is what makes "declared
    # order" a property of the *document*, not of each heading looked up in
    # isolation (see the module docstring). The advance is strict
    # (`position + 1`, not `position`) specifically so that two entries
    # sharing one heading still land on two different anchors; an inclusive
    # advance would let the second lookup re-match the first entry's own
    # segment, collapsing two entries into one region below.
    anchors: list[int | None] = []
    cursor = 0
    for entry in entries:
        position = _find(entry.heading, flat, declared_values, start=cursor)
        anchors.append(position)
        if position is not None:
            cursor = position + 1
    spans = _entry_spans(anchors, len(flat))

    positions: dict[str, int | None] = {}
    for d in declared:
        if d.entry_index is None or d.is_anchor:
            # Document-scoped: identity, a section's title, or an entry's
            # own heading (which cannot be scoped to a region it is what
            # defines).
            positions[d.field] = _find(d.value, flat, declared_values)
            continue
        span = spans[d.entry_index]
        if span is None:
            # This entry's own heading couldn't be found forward of the
            # cursor, so it has no region to scope into. Falling back to a
            # document-wide search here means one ordering problem is
            # reported once, not amplified into a recovery failure on
            # every other field this entry declares.
            positions[d.field] = _find(d.value, flat, declared_values)
        else:
            positions[d.field] = _find(d.value, flat, declared_values, start=span[0], end=span[1])

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

    # An anchor search failing to find a heading forward of the cursor means
    # one of two things, and only a *second*, unconstrained search tells
    # them apart: a heading that is on the page nowhere at all is already
    # reported above as a `critical` recovery failure on that heading's
    # `employer[i]` entry, not an ordering problem, so it must not also
    # appear here; a heading that is on the page but only *earlier* than the
    # cursor left it is a genuine ordering problem, and does belong here.
    out_of_order = [
        entries[i].heading
        for i, position in enumerate(anchors)
        if position is None and _find(entries[i].heading, flat, declared_values) is not None
    ]
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
