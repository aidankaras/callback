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

**Every declared value belongs to a region of the page, and recovery is
checked only within that region.** This is the load-bearing idea the rest
of the module exists to implement, arrived at the hard way: three separate
categories of declared value were each found, in turn, to be searched
against the whole document with no region at all, and each time the same
failure shape followed -- a value missing from where it belongs is
satisfied by an *equal*, genuine value that belongs somewhere else. An
entry's location dropped from entry 0 was satisfied by entry 1's identical
city; a fabricated candidate name was satisfied by an unrelated entry's
employer name; a fabricated section title was satisfied by an entry's
heading. `_Scope` (below) is the fourth category found this way -- a
section's own title -- closed the same way as the first three: give it a
region. There is exactly one declared value that does *not* get a region of
its own, and it is a deliberate, named exception rather than a leftover
default: an entry's own heading, which cannot be scoped to the region it is
what defines. See `_Scope`'s own docstring for the shape this takes in
code, and `_region` for how a value's scope resolves to an actual
`[start, end)` range.

An entry's own region runs from its own heading's position (found by a
cursor that only ever moves forward, advanced strictly past that position
so two entries sharing one heading still claim two different regions) to
the next entry's heading position, or the end of the document for the last
entry. An entry whose own heading could not be found forward of the cursor
has no region to scope into; its other fields fall back to an unscoped
search rather than being reported as a pile of unrelated recovery failures
on top of the one real ordering problem.

Identity (name, email, phone) gets the header region: everything before the
first entry anchor that was actually found. The header's end is defined by
the first *found* anchor, not a fixed row count, so a template that adds a
summary line above the first entry still gets a correctly sized header
region rather than one hard-coded to today's layout. When no entry anchor
was found at all (a document with no entries, or one where every entry's
own heading search failed), identity falls back to an unscoped search too,
since there is then no entry region for an identity value to be mistaken
for.

A section's title gets the region between sections: it starts just past the
last entry anchor found in any section before it (or the start of the
document, for the first section) and ends at the first anchor found among
its *own* section's entries, unbounded if none of them have one. This
shares `_entry_spans`'s accepted imprecision on purpose -- see that
function's docstring for what that imprecision is and why it is accepted
rather than chased down.

A section's skills get the same region as its title, one `_Declared` per
skill: a skills run has no heading of its own to anchor on (`Section`
forbids a section from carrying both entries and skills -- see
`core.resume.models.Section`), so there is no narrower region to give it
than the one already computed for the section it belongs to. Matching a
skill is not, however, the same operation as matching a title or a heading:
`_matches`'s ownership rule -- refuse a value when a *longer* declared
value contains it in the same segment -- is right for values competing to
own a segment, but a skills run renders every skill into *one* comma-joined
segment, so its own siblings are routinely proper substrings of each other
("SQL" inside "PostgreSQL", "Go" inside "Golang", "Java" inside
"JavaScript") without anything being wrong. Applying the ownership rule
there would refuse a majority of ordinary skill pairings on a completely
honest document. `_find_skill` and `_row_items` are the second, narrower
matching rule this calls for: a skill matches only when it equals one of a
segment's comma-delimited items exactly, never merely when it is contained
somewhere in the segment's raw text -- see `_find_skill`'s own docstring
for how a skill split across two rows by line-wrap is still recovered
without that rule ever reasoning about ownership at all.

`char_ratio` compares like with like: the summed length of every string the
document declares that a naive parser actually recovered, over the summed
length of everything it declares (every critical field, plus bullet text,
which is not gated individually but still counts toward what "recoverable"
means for the document as a whole). Comparisons are casefolded throughout,
because the template renders section titles through `upper()` -- that is a
rendering decision the gate should not punish as content loss.

A value long enough to wrap across several rows is reported unrecovered,
and that is correct, not a bug to chase away: matching only ever looks
inside one segment at a time (see `_matches`), and a naive, position-based
reader has no more ability to reassemble a heading split across rows than
this gate does. A skill is the one deliberate exception, and only across
one row boundary at a time -- see `_find_skill`'s docstring for why a
comma-delimited run tolerates being reassembled where a heading must not:
a skills run's own structure (each item bounded by a comma the renderer
put there) is what a naive reader can lean on that free prose has no
equivalent of. Checked against its own render, a heading that wraps across
three rows with the location column interleaved between its fragments
(`'Company 12 Extended International A Truly Extremely Long City Name
Metropolitan Area, Massachusetts March 2021 - April 2022'` /
`'Holdings Group Corporation'` / `'Worldwide Services Division'`) fails
with `char_ratio` measuring 0.5982 and the heading itself unrecovered.
Loosening matching to reassemble that heading across segments would defeat
the entire premise this gate checks -- a naive parser reading the same PDF
cannot do it either -- so a future reader who sees this as a failing case
and reaches for a "fix" should reread this paragraph first. The same
outcome is reachable from a second direction that touches no code in this
module at all: raising `y_tolerance` or `gap_factor` in
`core.checks.naive_extract.naive_segments` would re-merge the wrapped rows
into fewer, wider bands, at which point this module's own matching would
find the whole heading intact and pass it. That change would not be a fix
either -- it would make the pessimistic extractor a better reader than the
naive baseline it exists to approximate, which is the same regression its
own module docstring already warns against for the two-column fixture.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
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


class _Scope(Enum):
    """Which region of the page a declared value is allowed to be found in.

    Every declared value gets one of these, chosen explicitly at the point
    it is declared (`_Declared.scope` has no default) -- there is no
    "otherwise" case a kind of value could fall into without a reader
    having to decide where it belongs. `DOCUMENT` is not that fallback: it
    is a fourth, equally deliberate choice, made for exactly one reason and
    used for exactly one kind of value. Three other kinds of declared value
    were each, in turn, given `DOCUMENT` scope by omission before this type
    existed, and each time an unscoped search let a value missing from
    where it belonged be satisfied by an unrelated, genuine value somewhere
    else on the page (see the module docstring). `DOCUMENT` survives only
    for the one case where scoping is actually impossible, not merely
    inconvenient: an entry's own heading defines that entry's region, so
    searching for it *within* that region would be circular. A fifth
    category -- a section's declared skills -- was added after this type
    existed, which is the case this type was built for: it does not get a
    fifth *member*, because unlike the first four it has a real region
    already on offer (its own section's), so it is placed on `SECTION`
    deliberately rather than by omission -- see `SECTION`'s own docstring.
    """

    HEADER = "header"
    """Identity (name, email, phone): the region before the first entry."""

    SECTION = "section"
    """A section's own title, and every skill declared on it: the region
    between the previous section's last found entry and this section's own
    first found entry. A skill shares its section's region rather than
    getting a scope of its own, because it has no narrower anchor to be
    scoped to -- a skills run carries no heading, by construction (see
    `core.resume.models.Section`), so there is nothing under it to define
    an `ENTRY`-sized region the way an entry's own fields get one. This is
    the fifth category of declared value this module has had to place (see
    the class docstring), and it is placed here rather than given a scope
    of its own because the fix for the first four categories was "give the
    value its own boundary" -- for a skill, that boundary already exists
    and is exactly its section's."""

    ENTRY = "entry"
    """Everything an entry owns except its own heading (subheading,
    location, dates, bullets): that entry's own region."""

    DOCUMENT = "document"
    """An entry's own heading. The one deliberate exception -- see the
    class docstring."""


@dataclass(frozen=True)
class _Declared:
    """One string the rendered document is expected to contain.

    `critical` marks the fields whose absence fails the document outright:
    identity, every entry header field, and a section's own title -- a
    parser that segments a resume by recognising section headings can
    strand every entry beneath one it fails to recognise, which is why a
    title sits with the header fields here rather than with bullet text.
    Bullets are not individually gated -- a missing bullet is a
    `char_ratio` concern, not a hard failure on its own -- but still count
    toward what `char_ratio` measures, since they are still text the
    document declared and a naive parser was asked to recover.

    `scope` and `region_index` together say where this value is allowed to
    be found -- see `_region` for how they resolve to an actual range.
    `region_index` is `None` for `HEADER` and `DOCUMENT` scope (there is
    only one header region, and `DOCUMENT` scope names no region at all);
    for `SECTION` scope it is that section's index, and for `ENTRY` scope
    it is that entry's index.

    `is_skill` selects which of the module's two matching rules applies
    when this value's region is searched: `False` (every field above) uses
    `_matches`'s ownership rule, `True` uses `_find_skill`'s delimited-item
    rule instead. It defaults to `False` rather than joining `scope` as a
    third thing every call site must choose, because only one caller in
    `_declared_fields` ever needs it -- unlike `scope`, there is no fifth
    kind of value that could silently default into the wrong choice here,
    since every other field in this module is still `False` and always
    will be.
    """

    field: str
    value: str
    critical: bool
    scope: _Scope
    region_index: int | None = None
    is_skill: bool = False


def _declared_fields(doc: ResumeDoc) -> list[_Declared]:
    out: list[_Declared] = [
        _Declared("name", doc.identity.name, critical=True, scope=_Scope.HEADER),
        _Declared("email", doc.identity.email, critical=True, scope=_Scope.HEADER),
    ]
    if doc.identity.phone:
        out.append(_Declared("phone", doc.identity.phone, critical=True, scope=_Scope.HEADER))
    index = 0
    for section_index, section in enumerate(doc.sections):
        out.append(
            _Declared(
                f"section_title[{section_index}]",
                section.title,
                critical=True,
                scope=_Scope.SECTION,
                region_index=section_index,
            )
        )
        for skill_index, skill in enumerate(section.skills):
            out.append(
                _Declared(
                    f"skill[{section_index}][{skill_index}]",
                    skill,
                    critical=False,
                    scope=_Scope.SECTION,
                    region_index=section_index,
                    is_skill=True,
                )
            )
        for entry in section.entries:
            out.append(
                _Declared(f"employer[{index}]", entry.heading, critical=True, scope=_Scope.DOCUMENT)
            )
            if entry.subheading:
                out.append(
                    _Declared(
                        f"title[{index}]",
                        entry.subheading,
                        critical=True,
                        scope=_Scope.ENTRY,
                        region_index=index,
                    )
                )
            if entry.location:
                out.append(
                    _Declared(
                        f"location[{index}]",
                        entry.location,
                        critical=True,
                        scope=_Scope.ENTRY,
                        region_index=index,
                    )
                )
            if entry.start:
                span = f"{entry.start} - {entry.end}" if entry.end else entry.start
                out.append(
                    _Declared(
                        f"dates[{index}]",
                        span,
                        critical=True,
                        scope=_Scope.ENTRY,
                        region_index=index,
                    )
                )
            for bullet_index, bullet in enumerate(entry.bullets):
                out.append(
                    _Declared(
                        f"bullet[{index}][{bullet_index}]",
                        bullet.text,
                        critical=False,
                        scope=_Scope.ENTRY,
                        region_index=index,
                    )
                )
            index += 1
    return out


def _entries(doc: ResumeDoc) -> list[Entry]:
    """Every entry, in declared order, across all sections.

    This flattened order is what `region_index` counts against for
    `_Scope.ENTRY`-scoped fields in `_declared_fields`, and what the order
    check and `_entry_spans` both walk. `_section_regions` walks the same
    flattened `anchors` list in slices, one per section, rather than
    re-deriving entry order itself.

    A section carrying skills instead of entries contributes nothing here,
    with no extra check needed to make that true: `Section` forbids a
    section from declaring both (`core.resume.models.Section`), so
    `section.entries` is always empty on a skills section and the
    comprehension below already skips it for free. This matters because a
    skills section contributing a phantom entry anchor would collapse its
    own title's region -- `_section_regions` ends a section's region at the
    first anchor found among its *own* entries, so a spurious anchor there
    would cut the region off before the title it needs to contain.
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
    exemption means two *equal* values that the document declares in two
    logically distinct places pass straight through this function
    unblocked. That has shown up several times, in the same shape: a
    location genuinely missing from entry 0, satisfied by an identical city
    that legitimately belongs to entry 1; a fabricated name equal to an
    unrelated entry's employer name; a fabricated section title equal to an
    entry's heading. Neither this function nor any of its callers fixes
    that by comparing values more cleverly -- `check_roundtrip` closes it
    for every declared value by deciding, before it ever calls this
    function, which segments that value is even allowed to be searched
    against (see `_Scope` and `_region`); this function has no notion of
    "region" at all and reasons about the segment it is handed on its own
    terms. Second, this function only ever inspects one segment at a time,
    so a value that spans several segments (a heading long enough to wrap
    across two or three rows) never appears whole in any single segment and
    so never reaches the point where this rule would even apply -- that
    case fails for an unrelated reason (see the module docstring's closing
    paragraphs), not because this rule caught or missed anything.

    Within what it does check -- whether `value` is a legitimate reading of
    one specific `segment`, given competing declared values that might also
    be present in that same segment -- this rule can refuse a legitimate
    match, but it cannot grant an illegitimate one: a segment reading `"NY
    Ops Lead in NY"` contains both the longer declared value `"NY Ops
    Lead"` and a genuinely separate, standalone `"NY"` later in the same
    segment, and this function refuses the shorter value's match there
    anyway, because it reasons about whole-segment containment and cannot
    tell those two occurrences of "NY" apart. A missed recovery is the safe
    direction for a gate whose job is to reject documents it cannot vouch
    for, so that spurious failure is accepted here rather than chased with
    position tracking this module does not otherwise need.
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
    every caller that scopes a search to a region narrower than the whole
    document (`_region`) passes an explicit boundary instead.
    """
    if not normalize(value):
        return None
    limit = len(flat) if end is None else end
    for i in range(start, limit):
        if _matches(value, flat[i], declared_values):
            return i
    return None


def _row_items(segment: str) -> list[str]:
    """A skills segment's comma-delimited items, normalised and casefolded.

    Only `_find_skill` calls this. `_matches`'s ownership rule reasons about
    which of several declared values gets to own a whole segment, which is
    the wrong question for a skills run: every skill in it shares one
    segment by construction (see the module docstring's skills paragraph),
    so "SQL" sitting inside "PostgreSQL" there is two honest, adjacent
    skills, not one value stealing another's segment. Splitting on the
    comma the renderer itself put there and comparing an item exactly is
    what makes that distinction, in place of ownership, correctly reject
    `"Go"` against `"Going through the migration backlog"` (no comma
    delimits "Go" out of "Going" at all, so it never becomes an item) while
    accepting `"Go"` against `"SQL, PostgreSQL, Go, Golang"` (a standalone
    item, `casefold()`-equal to the target, that `"Golang"` sitting next to
    it does not disturb).
    """
    return [normalize(item).casefold() for item in segment.split(",")]


def _find_skill(
    value: str, flat: list[str], *, start: int = 0, end: int | None = None
) -> int | None:
    """The position of the first segment in `[start, end)` where `value` is
    itself one of a skills run's comma-delimited items -- see `_row_items`
    for why item-equality, not containment, is the right test for a skill.

    A skills run long enough to wrap is split across *consecutive
    segments*, one per physical row (`naive_segments` gives each wrapped
    row its own segment; see the module docstring), and Typst only ever
    breaks that wrap between words -- never inside a word -- so at most one
    item is ever split by a row boundary, and the row receiving the split
    item's tail always begins with that tail as its own first
    comma-delimited item. Rejoining the last item of row `i` with the first
    item of row `i + 1`, and testing that joined string as one further
    candidate alongside each row's own items, recovers exactly that case.
    Measured on this project's own render: a 20-skill run wraps across
    three rows, and the boundary reads `'... Google Cloud'` then
    `'Platform, Machine Learning ...'` -- the joined candidate
    `'Google Cloud Platform'` is what a listed skill of that name is
    actually compared against, and no other row in that render is touched.

    The join is deliberately pairwise -- exactly one adjacent row on each
    side of a boundary, never the whole region -- rather than joining every
    segment `[start, end)` covers. A skills-only section (skills and
    entries are mutually exclusive; see `core.resume.models.Section`)
    always resolves an unbounded region (`_Scope.SECTION`'s own region ends
    at its section's first found *entry* anchor, and a skills section has
    none), so joining the whole region would, whenever anything follows
    it, absorb a later section's own content into the last skill's text.
    Checked directly: a five-skill run immediately followed by an
    entry-less "Education" section, joined whole, turns the genuinely
    listed `"Kubernetes"` into an unmatchable `"Kubernetes EDUCATION"`. The
    pairwise join cannot do that: the extra candidate it offers at an
    ordinary (non-wrap) row boundary is a nonsense concatenation no real
    skill is ever named after, while each row's own, unjoined items --
    including the genuine `"Kubernetes"` -- are still offered exactly as
    they would be without this rule at all.
    """
    target = normalize(value).casefold()
    if not target:
        return None
    limit = len(flat) if end is None else end
    for i in range(start, limit):
        items = _row_items(flat[i])
        if target in items:
            return i
        if (
            items
            and i + 1 < len(flat)
            and (following := _row_items(flat[i + 1]))
            and f"{items[-1]} {following[0]}" == target
        ):
            return i
    return None


def _entry_spans(anchors: list[int | None], flat_len: int) -> list[tuple[int, int] | None]:
    """The `[start, end)` region each entry's non-heading fields must be
    found within, derived from `anchors` (that entry's heading position, or
    `None` if it couldn't be found forward of the reading cursor -- see
    `check_roundtrip`).

    An entry's region starts at its own anchor and runs to the *next*
    anchor that was actually found, skipping over any entry in between
    whose own heading search failed. That skip is a real, accepted
    imprecision, not an oversight: an entry with no anchor of its own
    contributes no boundary of its own, but it does not shrink to nothing
    either -- the *previous found* entry's region silently widens to reach
    past it, absorbing whatever segments the skipped entry's own rows
    occupy. Checked against a real 22-entry two-column render where this
    happens (see `tests.fixtures.make_two_column`): the widened region
    absorbs a *different* entry's genuine subheading and bullet, so a
    fabricated value equal to that neighbour's real content would be
    reported recovered even though it does not belong to the entry whose
    region grew to include it.

    This cannot flip the document's aggregate verdict: whichever entry's
    anchor search failed still independently trips something, on the
    heading itself -- either `out_of_order` (present, just not reachable
    forward of the cursor) or a `critical` recovery miss on its
    `employer[i]` entry (not present anywhere at all). One of those two
    always fires when an anchor is `None`, so the document does not pass
    clean. What is not reliable in that situation is *per-field*
    attribution: a `FieldResult.recovered=True` next to the wrong entry, or
    a `char_ratio` nudged upward by content that was never really that
    entry's, are both possible, and a non-critical field (a bullet) that
    gets silently absorbed this way produces no signal at all, critical or
    otherwise. Closing this would mean tracking which segments an earlier
    entry's search has already consumed so a later one can't be credited
    with them -- position-tracking machinery this module does not
    otherwise need, for a case that already cannot pass a document clean.
    The verdict is sound; do not read a passing `FieldResult` for a
    specific field as proof that field's own value was what was actually
    found there.

    `_section_regions` shares this same imprecision, for the same reason,
    one level up: a section whose every entry fails its own anchor search
    contributes no boundary either, and the previous section's title region
    widens past it in exactly the same way.
    """
    spans: list[tuple[int, int] | None] = []
    for i, position in enumerate(anchors):
        if position is None:
            spans.append(None)
            continue
        following = next((q for q in anchors[i + 1 :] if q is not None), flat_len)
        spans.append((position, following))
    return spans


def _section_regions(doc: ResumeDoc, anchors: list[int | None]) -> list[tuple[int, int | None]]:
    """The `[start, end)` region each section's own title must be found
    within, one entry per section, in declared order.

    A section's region starts just past the last entry anchor found in any
    section before it (or `0`, for the first section) and ends at the first
    anchor found among its *own* entries, or is left unbounded (`None`) if
    none of them have one -- `_find`'s own `end=None` already means "search
    to the end of `flat`", so leaving the end as `None` here gives that
    fallback for free, the same trick `check_roundtrip` uses for the header
    region. `anchors` is walked in slices matching each section's entry
    count, rather than re-searching anything: it is the same list, built
    once, that the order check and `_entry_spans` also consume. See
    `_entry_spans` for the imprecision this shares with entry regions when
    a whole section's worth of anchors fails to be found.
    """
    regions: list[tuple[int, int | None]] = []
    start = 0
    index = 0
    for section in doc.sections:
        count = len(section.entries)
        section_anchors = anchors[index : index + count]
        end = next((a for a in section_anchors if a is not None), None)
        regions.append((start, end))
        found = [a for a in section_anchors if a is not None]
        if found:
            start = max(found) + 1
        index += count
    return regions


def _region(
    d: _Declared,
    *,
    header_end: int | None,
    section_regions: list[tuple[int, int | None]],
    entry_spans: list[tuple[int, int] | None],
) -> tuple[int, int | None]:
    """The `[start, end)` range `d.value` is allowed to be found within,
    resolved from `d.scope` -- the one place this module maps "which kind
    of declared value is this" to "which segments may it come from."

    Every `_Scope` member is handled by name, including `DOCUMENT`, so
    that an unscoped search stays a visible, deliberate branch rather than
    something a value falls into by not matching an earlier case.
    """
    if d.scope is _Scope.HEADER:
        return (0, header_end)
    if d.scope is _Scope.SECTION:
        assert d.region_index is not None, "a section-scoped field always names its section"
        return section_regions[d.region_index]
    if d.scope is _Scope.ENTRY:
        assert d.region_index is not None, "an entry-scoped field always names its entry"
        span = entry_spans[d.region_index]
        if span is not None:
            return span
        # This entry's own heading couldn't be found forward of the
        # cursor, so it has no region to scope into. Falling back to an
        # unscoped search here means one ordering problem is reported
        # once, not amplified into a recovery failure on every other field
        # this entry declares.
        return (0, None)
    # `_Scope.DOCUMENT`: an entry's own heading, which cannot be scoped to
    # the region it is what defines -- see `_Scope`'s own docstring.
    return (0, None)


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

    entry_spans = _entry_spans(anchors, len(flat))
    section_regions = _section_regions(doc, anchors)
    # The header region: everything before the first entry anchor found
    # anywhere in the document. `None` (no entry anchor found at all -- no
    # entries, or every entry's own heading search failed) means there is
    # no entry region for an identity value to be confused with, and
    # `_region` passes that straight through as `_find`'s `end`, which
    # already means "search to the end of `flat`" when it is `None`.
    header_end = next((a for a in anchors if a is not None), None)

    positions: dict[str, int | None] = {}
    for d in declared:
        start, end = _region(
            d, header_end=header_end, section_regions=section_regions, entry_spans=entry_spans
        )
        # A skill uses `_find_skill`'s delimited-item rule instead of
        # `_matches`'s ownership rule -- see `_row_items`'s docstring for
        # why the two must differ, and the module docstring's skills
        # paragraph for where a reader meets this split for the first time.
        positions[d.field] = (
            _find_skill(d.value, flat, start=start, end=end)
            if d.is_skill
            else _find(d.value, flat, declared_values, start=start, end=end)
        )

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
