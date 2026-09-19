from pathlib import Path

import pytest

from core.checks.naive_extract import naive_segments, normalize
from core.checks.roundtrip import check_roundtrip
from core.profile.models import Identity
from core.render.typst import FONT_DIR, render
from core.resume.models import Bullet, Entry, ResumeDoc, Section
from tests.fixtures.make_two_column import build_two_column_resume

DOC = ResumeDoc(
    identity=Identity(name="Dana Vex", email="dana@example.com", phone="+1 555 0100"),
    sections=[
        Section(
            title="Experience",
            entries=[
                Entry(
                    heading="Northwind Analytics",
                    subheading="Data Engineer",
                    location="Boston, MA",
                    start="Jun 2024",
                    end="Aug 2025",
                    bullets=[Bullet(text="Cut the queue to 300 items", sources=["a"])],
                )
            ],
        )
    ],
)


@pytest.mark.slow
def test_clean_document_recovers_every_critical_field(tmp_path: Path) -> None:
    out = tmp_path / "clean.pdf"
    render(DOC, out)
    result = check_roundtrip(DOC, out)
    assert result.verdict == "pass", [f.field for f in result.critical if not f.recovered]
    assert all(f.recovered for f in result.critical)


@pytest.mark.slow
def test_date_span_is_recovered_as_written(tmp_path: Path) -> None:
    """Checked against this project's own render: a single '-' in a date span
    ("Jun 2024 - Aug 2025") comes back from `extract_words` as a literal
    hyphen-minus, not an en dash -- Typst's typographic substitution only
    fires on '--'/'---', which this template never emits. So the character
    that must be absorbed here isn't produced by this specific render, and
    `core.checks.naive_extract.normalize`'s dash-folding table is exercised
    directly (with an actual en dash) by `test_naive_extract.py` instead.
    What this test guards is the integration path: that `check_roundtrip`
    recovers a dates field at all through whatever glyph a date range
    renders as, not the specific glyph substitution.
    """
    out = tmp_path / "dash.pdf"
    render(DOC, out)
    dates = [f for f in check_roundtrip(DOC, out).critical if f.field.startswith("dates")]
    assert dates and all(f.recovered for f in dates)


@pytest.mark.slow
def test_employer_and_location_land_in_different_segments(tmp_path: Path) -> None:
    out = tmp_path / "seg.pdf"
    render(DOC, out)
    result = check_roundtrip(DOC, out)
    employer = next(f for f in result.critical if f.field == "employer[0]")
    location = next(f for f in result.critical if f.field == "location[0]")
    assert employer.segment != location.segment, "fields share a segment; parser cannot split them"


@pytest.mark.slow
def test_clean_multi_entry_document_passes_in_order(tmp_path: Path) -> None:
    """Five entries deliberately sharing one date span and one location, which
    is ordinary in a real resume and the case that breaks a naive order check:
    looking each field up independently resolves every repeated date to the
    first entry's segment and reports inversions in a correctly ordered
    document. Measured entry-heading positions in the flattened segment
    sequence here are [3, 7, 11, 15, 19] -- each a clean four-row stride
    (heading, location+dates, subheading, bullet) after the two-row header.
    """
    doc = ResumeDoc(
        identity=Identity(name="Dana Vex", email="dana@example.com", phone="+1 555 0100"),
        sections=[
            Section(
                title="Experience",
                entries=[
                    Entry(
                        heading=f"Company {i}",
                        subheading=f"Engineer {i}",
                        location="Boston, MA",
                        start="Jun 2024",
                        end="Aug 2025",
                        bullets=[Bullet(text=f"Shipped milestone {i} on time", sources=["a"])],
                    )
                    for i in range(1, 6)
                ],
            )
        ],
    )
    out = tmp_path / "multi.pdf"
    render(doc, out)
    result = check_roundtrip(doc, out)
    assert result.order_ok, f"clean document reported out of order: {result.out_of_order}"
    assert result.out_of_order == []
    assert result.verdict == "pass", result.findings


@pytest.mark.slow
def test_char_ratio_is_exactly_one_on_a_clean_document(tmp_path: Path) -> None:
    """Guards the casefold rule. The template renders section titles through
    `upper()`, so comparing "Experience" case-sensitively against a rendered
    "EXPERIENCE" scores a clean document at 0.9242 and fails it.
    """
    out = tmp_path / "ratio.pdf"
    render(DOC, out)
    assert check_roundtrip(DOC, out).char_ratio == 1.0


@pytest.mark.slow
def test_char_ratio_falls_when_declared_content_is_absent(tmp_path: Path) -> None:
    """The ratio must be crossable, which the original formula (bullet
    characters over whole-document characters, clamped at 1.0) was not: on
    this module's own `DOC` it computed a raw ratio of 5.46 and clamped to
    1.00 regardless of what was actually lost. Comparing declared characters
    recovered against declared characters total instead, declaring one
    bullet the rendered PDF does not contain measures 0.7765.
    """
    out = tmp_path / "lossy.pdf"
    render(DOC, out)
    doc = DOC.model_copy(deep=True)
    doc.sections[0].entries[0].bullets.append(
        Bullet(text="Rebuilt the ingestion layer end to end", sources=["b"])
    )
    result = check_roundtrip(doc, out)
    assert result.char_ratio < 0.98, result.char_ratio
    assert result.verdict == "fail"


@pytest.mark.slow
def test_two_column_document_fails_on_order_not_recovery(tmp_path: Path) -> None:
    """The document this gate exists to reject. Every declared entry heading is
    recoverable by substring search, so a presence-only gate passes it; 9 of
    20 are unreachable in declared order, so this one must not. The final
    assertion is the point of the test: it proves the weaker check would have
    passed, so a future change back to presence-only fails here rather than
    quietly widening what the gate accepts.

    `entries=20`, not a rounder or larger number, is load-bearing: past
    roughly 22 entries on this fixture, per-entry recovery scoping (see
    `core.checks.roundtrip`) picks up a secondary, genuine recovery miss
    from one entry's rows interleaving into a different entry's own span --
    a real consequence of the fix, not a bug, but not what this test is
    about either. `build_two_column_resume`'s own docstring has the measured
    numbers for that case; this test stays at the largest count that still
    isolates order cleanly.
    """
    doc, out = build_two_column_resume(tmp_path / "scrambled.pdf", FONT_DIR, entries=20)
    result = check_roundtrip(doc, out)

    assert not result.order_ok
    assert result.out_of_order, (
        "no anchors reported out of order; is the fixture really two-column?"
    )
    assert result.verdict == "fail"

    segments = [seg for line in naive_segments(out) for seg in line]
    assert all(
        any(normalize(anchor).casefold() in seg.casefold() for seg in segments)
        for anchor in result.out_of_order
    ), "an out-of-order anchor was also missing outright; this fixture should fail on order alone"

    # The docstring above claims this document fails on order *alone*, so pin
    # the other two paths shut rather than trusting them to stay open, using
    # targeted checks rather than a finding count: a count couples this test
    # to the total number of finding *types* the gate can ever produce, so an
    # unrelated future finding would break it even though order isolation
    # would be untouched. Measured here: every critical field recovers, and
    # the character ratio is exactly 1.0 (the fixture renders every string
    # it declares, including its section title), so neither path is anywhere
    # near its own threshold.
    assert [f.field for f in result.critical if not f.recovered] == [], (
        "a critical field was unrecoverable, so this no longer isolates order"
    )
    assert result.char_ratio >= 0.98, (
        f"character ratio {result.char_ratio:.4f} is itself failing the document, "
        "so this no longer isolates order"
    )
    assert any(
        finding.startswith("entry headings present but not reachable in declared reading order")
        for finding in result.findings
    ), result.findings


@pytest.mark.slow
def test_colliding_heading_reports_out_of_order(tmp_path: Path) -> None:
    """A short heading that is itself a substring of a different entry's
    heading can match that other entry's segment under bare containment,
    pinning the order cursor there and letting a genuinely inverted
    document read as perfectly ordered.

    The document below declares "Analytics" (Boston) first and "Northwind
    Analytics Group" (NYC) second, but is physically rendered in the
    opposite order -- "Northwind Analytics Group" first, "Analytics"
    second -- by rendering a copy with the entries reversed. Under bare
    substring containment, looking up "Analytics" matches wherever it
    first appears in reading order, which is *inside* "Northwind Analytics
    Group"'s own segment (since "analytics" is a substring of "northwind
    analytics group"); the cursor then pins there, and the following
    lookup for "Northwind Analytics Group" matches the same segment again
    (a segment always contains itself), so the whole document reads as
    ascending. No existing fixture in this module exercised this: every
    heading in every other document here is unique and not a substring of
    any other declared value, so a matcher that finds *a* segment
    containing the right text, without checking whether a longer declared
    value owns that segment instead, was never exercised against a
    colliding pair -- which is how this shipped.

    This test's own collision is heading-against-heading, so it would still
    pass under a narrower fix that only ever blocked one heading with
    another and left every other field type unprotected -- it never
    exercises a non-heading blocker. `test_coincidental_substring_is_not_recovered`
    is the test that holds that line: its blocker is a subheading, not a
    heading, so a fix narrowed to headings only would pass this test while
    failing that one.
    """
    physical = ResumeDoc(
        identity=Identity(name="Dana Vex", email="dana@example.com", phone="+1 555 0100"),
        sections=[
            Section(
                title="Experience",
                entries=[
                    Entry(
                        heading="Northwind Analytics Group",
                        location="NYC, NY",
                        start="Jan 2020",
                        end="May 2023",
                        bullets=[Bullet(text="Led the migration effort", sources=["a"])],
                    ),
                    Entry(
                        heading="Analytics",
                        location="Boston, MA",
                        start="Jun 2024",
                        end="Aug 2025",
                        bullets=[Bullet(text="Owned the reporting pipeline", sources=["b"])],
                    ),
                ],
            )
        ],
    )
    declared = physical.model_copy(deep=True)
    declared.sections[0].entries.reverse()

    out = tmp_path / "colliding.pdf"
    render(physical, out)
    result = check_roundtrip(declared, out)

    assert not result.order_ok, "a fully inverted document reported as reading in order"
    assert result.out_of_order == ["Northwind Analytics Group"]


@pytest.mark.slow
def test_coincidental_substring_is_not_recovered(tmp_path: Path) -> None:
    """A fabricated field value that happens to be spelled inside a
    genuinely different field's text must not be reported as recovered:
    appearing inside someone else's field is not the same as being on the
    page in its own right.

    The rendered document below has `location="Remote"` and a subheading
    "NY Ops Lead"; the document checked against it claims `location="NY"`
    instead -- a value that never appears anywhere as a location, but
    which is a coincidental substring of the genuine "NY Ops Lead" text.
    """
    rendered = ResumeDoc(
        identity=Identity(name="Dana Vex", email="dana@example.com", phone="+1 555 0100"),
        sections=[
            Section(
                title="Experience",
                entries=[
                    Entry(
                        heading="Northwind Analytics",
                        subheading="NY Ops Lead",
                        location="Remote",
                        start="Jun 2024",
                        end="Aug 2025",
                        bullets=[Bullet(text="Cut the queue to 300 items", sources=["a"])],
                    )
                ],
            )
        ],
    )
    out = tmp_path / "coincidence.pdf"
    render(rendered, out)

    claimed = rendered.model_copy(deep=True)
    claimed.sections[0].entries[0].location = "NY"
    result = check_roundtrip(claimed, out)

    location = next(f for f in result.critical if f.field == "location[0]")
    assert not location.recovered, (
        f"'NY' was reported recovered from segment {location.segment!r}, "
        "which is a different field's text, not a location"
    )


@pytest.mark.slow
def test_dropped_location_shared_with_another_entry_is_not_recovered(tmp_path: Path) -> None:
    """Recovery must be scoped to the entry that declares a field, not the
    whole document. Two jobs in the same city is ordinary; a location
    genuinely missing from one of them must not be covered for by the
    other entry's identical value landing somewhere else on the page.

    Entry 0 ("Northwind") is rendered with no location at all -- the
    content loss this gate exists to detect. Entry 1 ("Contoso") genuinely
    has "Boston, MA". The document checked here fabricates entry 0's
    location as "Boston, MA" too: a value that is real, just not real
    *there*. Before per-entry scoping, a document-wide search for "Boston,
    MA" found entry 1's genuine segment and reported entry 0's fabricated
    claim as recovered, passing the whole document clean.

    This test exercises `location` specifically and would not notice
    `entry_index` going missing from a different field's declaration --
    `dates`, say. `test_identical_headings_swapped_fields_are_not_recovered`
    is the test that holds that line: it swaps title, location *and* dates
    between two entries at once, so losing scoping on any one of those
    three field kinds leaves at least one of its six expected misses absent
    and fails it.
    """
    rendered = ResumeDoc(
        identity=Identity(name="Dana Vex", email="dana@example.com", phone="+1 555 0100"),
        sections=[
            Section(
                title="Experience",
                entries=[
                    Entry(
                        heading="Northwind",
                        start="Jun 2024",
                        end="Aug 2025",
                        bullets=[Bullet(text="Owned the pipeline", sources=["a"])],
                    ),
                    Entry(
                        heading="Contoso",
                        location="Boston, MA",
                        start="Jan 2020",
                        end="May 2023",
                        bullets=[Bullet(text="Led the migration", sources=["b"])],
                    ),
                ],
            )
        ],
    )
    out = tmp_path / "dropped_location.pdf"
    render(rendered, out)

    claimed = rendered.model_copy(deep=True)
    claimed.sections[0].entries[0].location = "Boston, MA"
    result = check_roundtrip(claimed, out)

    location = next(f for f in result.critical if f.field == "location[0]")
    assert not location.recovered, (
        f"'Boston, MA' was reported recovered from segment {location.segment!r}, "
        "which belongs to a different entry"
    )
    assert result.verdict == "fail"


@pytest.mark.slow
def test_identical_headings_swapped_fields_are_not_recovered(tmp_path: Path) -> None:
    """Two entries can share one heading exactly (a title held twice, at two
    different companies, is ordinary), which erases the order check's only
    signal: heading-only order-checking cannot tell two identical headings
    apart, so a document with their *other* fields physically swapped --
    entry 0's real subheading, location and dates sitting where entry 1's
    are declared, and the reverse -- still reads as perfectly in order.
    `order_ok` is asserted `True` below specifically to make that visible,
    not just skipped over: identical headings genuinely carry no order
    signal, and the order check alone cannot be expected to catch a swap
    like this one. What catches it instead is recovery, once fields are
    scoped per entry: entry 0's own span runs from its own heading to entry
    1's, and entry 1's real subheading/location/dates are not inside it.
    """
    physical = ResumeDoc(
        identity=Identity(name="Dana Vex", email="dana@example.com", phone="+1 555 0100"),
        sections=[
            Section(
                title="Experience",
                entries=[
                    Entry(
                        heading="Regional Sales Lead",
                        subheading="West Region",
                        location="Denver, CO",
                        start="Jan 2022",
                        end="Dec 2023",
                        bullets=[Bullet(text="Grew west territory revenue", sources=["a"])],
                    ),
                    Entry(
                        heading="Regional Sales Lead",
                        subheading="East Region",
                        location="Miami, FL",
                        start="Jan 2020",
                        end="Dec 2021",
                        bullets=[Bullet(text="Grew east territory revenue", sources=["b"])],
                    ),
                ],
            )
        ],
    )
    declared = physical.model_copy(deep=True)
    e0, e1 = declared.sections[0].entries
    e0.subheading, e1.subheading = e1.subheading, e0.subheading
    e0.location, e1.location = e1.location, e0.location
    e0.start, e1.start = e1.start, e0.start
    e0.end, e1.end = e1.end, e0.end
    e0.bullets, e1.bullets = e1.bullets, e0.bullets

    out = tmp_path / "identical_headings.pdf"
    render(physical, out)
    result = check_roundtrip(declared, out)

    assert result.order_ok, "identical headings carry no order signal either way"
    assert result.verdict == "fail"
    assert [f.field for f in result.critical if not f.recovered] == [
        "title[0]",
        "location[0]",
        "dates[0]",
        "title[1]",
        "location[1]",
        "dates[1]",
    ]


@pytest.mark.slow
def test_entry_span_end_is_exclusive_of_the_next_anchor(tmp_path: Path) -> None:
    """An entry's region runs up to, and does not include, the segment
    where the next entry's own heading was found -- nothing in the suite
    otherwise distinguishes that half-open `[start, end)` from an inclusive
    `[start, end]` one segment wider, and a boundary with zero coverage is
    not actually pinned down.

    Entry 0 ("Northwind") has no location declared for rendering. Entry 1's
    heading is "Contoso Analytics". The document checked here fabricates
    entry 0's location as "Contoso Analytics" too -- equal to entry 1's
    *heading*, specifically, because that heading's own segment is exactly
    the one segment an off-by-one on the exclusive end would pull into
    entry 0's region. A correct, exclusive end excludes it; entry 0's
    region stops one segment short of entry 1's heading.
    """
    rendered = ResumeDoc(
        identity=Identity(name="Dana Vex", email="dana@example.com", phone="+1 555 0100"),
        sections=[
            Section(
                title="Experience",
                entries=[
                    Entry(
                        heading="Northwind",
                        start="Jun 2024",
                        end="Aug 2025",
                        bullets=[Bullet(text="Owned the pipeline", sources=["a"])],
                    ),
                    Entry(
                        heading="Contoso Analytics",
                        location="Boston, MA",
                        start="Jan 2020",
                        end="May 2023",
                        bullets=[Bullet(text="Led the migration", sources=["b"])],
                    ),
                ],
            )
        ],
    )
    out = tmp_path / "span_boundary.pdf"
    render(rendered, out)

    claimed = rendered.model_copy(deep=True)
    claimed.sections[0].entries[0].location = "Contoso Analytics"
    result = check_roundtrip(claimed, out)

    location = next(f for f in result.critical if f.field == "location[0]")
    assert not location.recovered, (
        f"'Contoso Analytics' was reported recovered from segment {location.segment!r}, "
        "which is entry 1's heading, one segment past where entry 0's region should end"
    )
    assert result.verdict == "fail"


@pytest.mark.slow
def test_fabricated_name_matching_an_unrelated_employer_is_not_recovered(tmp_path: Path) -> None:
    """Identity must be scoped to the header block for the same reason an
    entry's fields are scoped to that entry's region: a document-wide
    search for a fabricated name that happens to equal an unrelated entry's
    employer name finds that entry's genuine segment and reports the
    fabrication as recovered, passing a document whose declared identity is
    entirely made up.

    The rendered document's real name is "Dana Vex"; its one entry's
    employer is "Foo Bar Ventures". The document checked against it claims
    the candidate's name is "Foo Bar Ventures" -- a value that never
    appears anywhere as a name, but is the genuine text of an unrelated
    entry's heading.
    """
    rendered = ResumeDoc(
        identity=Identity(name="Dana Vex", email="dana@example.com", phone="+1 555 0100"),
        sections=[
            Section(
                title="Experience",
                entries=[
                    Entry(
                        heading="Foo Bar Ventures",
                        location="Boston, MA",
                        start="Jun 2024",
                        end="Aug 2025",
                        bullets=[Bullet(text="Owned the pipeline", sources=["a"])],
                    )
                ],
            )
        ],
    )
    out = tmp_path / "fabricated_name.pdf"
    render(rendered, out)

    claimed = rendered.model_copy(deep=True)
    claimed.identity.name = "Foo Bar Ventures"
    result = check_roundtrip(claimed, out)

    name = next(f for f in result.critical if f.field == "name")
    assert not name.recovered, (
        f"'Foo Bar Ventures' was reported recovered from segment {name.segment!r}, "
        "which is the entry's heading, not the header"
    )
    assert result.verdict == "fail"


@pytest.mark.slow
def test_name_whose_surname_appears_inside_an_employer_name_still_passes(
    tmp_path: Path,
) -> None:
    """Scoping identity to the header block must not break the ordinary
    case it could plausibly interfere with: a candidate's surname
    legitimately reappearing inside an employer's name (a common surname,
    or simple coincidence) is not itself suspicious, and the header block
    is defined widely enough -- everything before the first entry anchor --
    that the genuine name in its own header segment is found there directly
    rather than needing to compete with the later, unrelated occurrence.
    """
    doc = ResumeDoc(
        identity=Identity(name="Jordan Reed", email="jordan@example.com", phone="+1 555 0100"),
        sections=[
            Section(
                title="Experience",
                entries=[
                    Entry(
                        heading="Reed Analytics",
                        location="Boston, MA",
                        start="Jun 2024",
                        end="Aug 2025",
                        bullets=[Bullet(text="Owned the pipeline", sources=["a"])],
                    )
                ],
            )
        ],
    )
    out = tmp_path / "legit_surname.pdf"
    render(doc, out)
    result = check_roundtrip(doc, out)

    name = next(f for f in result.critical if f.field == "name")
    assert name.recovered, "a name legitimately echoed in an employer name was not recovered"
    assert result.verdict == "pass", result.findings


@pytest.mark.slow
def test_fabricated_section_title_matching_an_employer_is_not_recovered(
    tmp_path: Path,
) -> None:
    """A section's title must be scoped to its own section's region for the
    same reason identity is scoped to the header: an unscoped search for a
    fabricated title that happens to equal an entry's employer name finds
    that entry's genuine segment, and reports a heading that was dropped
    from the page as fully recovered. Section titles are `critical=False`,
    so this shows up as a `char_ratio` drop rather than a critical miss --
    but the realistic form is a renderer losing a "Skills" heading while
    the word "skills" survives somewhere in a bullet, and the ratio must
    not report full recovery of a heading that is not on the page.

    The rendered document's real section title is "Experience"; its one
    entry's employer is "Skills Matrix Group". The document checked
    against it declares the section title as "Skills Matrix Group" too --
    a value that never appears anywhere as a title, but is the genuine text
    of that entry's own heading.
    """
    rendered = ResumeDoc(
        identity=Identity(name="Dana Vex", email="dana@example.com", phone="+1 555 0100"),
        sections=[
            Section(
                title="Experience",
                entries=[
                    Entry(
                        heading="Skills Matrix Group",
                        location="Boston, MA",
                        start="Jun 2024",
                        end="Aug 2025",
                        bullets=[Bullet(text="Owned the pipeline", sources=["a"])],
                    )
                ],
            )
        ],
    )
    out = tmp_path / "fabricated_title.pdf"
    render(rendered, out)

    claimed = rendered.model_copy(deep=True)
    claimed.sections[0].title = "Skills Matrix Group"
    result = check_roundtrip(claimed, out)

    assert result.char_ratio < 1.0, "the fabricated title was scored as fully recovered characters"
    assert result.verdict == "fail", result.findings


@pytest.mark.slow
def test_two_sections_with_honest_titles_are_both_recovered(tmp_path: Path) -> None:
    """The regression section-region scoping could most easily cause: two
    real sections, each with its own honest title, both still recovered.
    Section 0's region ends at its own first found entry anchor, and
    section 1's region starts just past it -- get either boundary wrong by
    one segment and one of the two titles lands outside the region it
    should have been found within.
    """
    doc = ResumeDoc(
        identity=Identity(name="Dana Vex", email="dana@example.com", phone="+1 555 0100"),
        sections=[
            Section(
                title="Experience",
                entries=[
                    Entry(
                        heading="Northwind Analytics",
                        location="Boston, MA",
                        start="Jun 2024",
                        end="Aug 2025",
                        bullets=[Bullet(text="Owned the pipeline", sources=["a"])],
                    )
                ],
            ),
            Section(
                title="Education",
                entries=[
                    Entry(
                        heading="State University",
                        location="Amherst, MA",
                        start="Sep 2018",
                        end="May 2022",
                        bullets=[Bullet(text="Studied computer science", sources=["b"])],
                    )
                ],
            ),
        ],
    )
    out = tmp_path / "two_sections.pdf"
    render(doc, out)
    result = check_roundtrip(doc, out)

    assert result.char_ratio == 1.0, result.char_ratio
    assert result.verdict == "pass", result.findings


@pytest.mark.slow
def test_missing_phone_is_a_failure(tmp_path: Path) -> None:
    doc = DOC.model_copy(deep=True)
    out = tmp_path / "nophone.pdf"
    render(doc, out)
    # Claim a phone number the document does not contain.
    doc.identity.phone = "+1 555 9999"
    result = check_roundtrip(doc, out)
    assert result.verdict == "fail"
    assert any(f.field == "phone" and not f.recovered for f in result.critical)


@pytest.mark.slow
def test_every_font_carries_a_tounicode_map(tmp_path: Path) -> None:
    """Every embedded font must carry a `/ToUnicode` CMap, or copy-pasting
    text out of the PDF -- which is what a naive parser does, and what
    anyone pasting a resume's contents into a form does too -- can silently
    produce the wrong characters even though the glyphs render correctly.

    Probed on this project's own render: every font here is Type0 with
    Identity-H encoding (`/BaseFont` ending "-Identity-H"), and `/ToUnicode`
    lives on the Type0 dictionary itself, not on its `/DescendantFonts`
    entry -- the descendant carries `/Subtype /CIDFontType0` and no
    `/ToUnicode` key at all. So the top-level dictionary is checked first,
    and a descendant is only consulted as a fallback for a font structure
    this project's own renderer does not currently produce. Likewise,
    `/Resources` is set directly on every page produced here (confirmed by
    checking the page's own dictionary against its `/Parent`), never
    inherited from the page tree -- but a page lacking it directly is not
    provably impossible in a PDF in general, so the walk up `/Parent` is
    kept as a fallback rather than assumed away.
    """
    from pypdf import PdfReader
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

    def resolve(obj: object) -> DictionaryObject:
        resolved = obj.get_object() if isinstance(obj, IndirectObject) else obj
        assert isinstance(resolved, DictionaryObject), f"expected a dictionary, got {resolved!r}"
        return resolved

    def resources_of(page: DictionaryObject) -> DictionaryObject | None:
        node: DictionaryObject | None = page
        while node is not None:
            found = node.get("/Resources")
            if found is not None:
                return resolve(found)
            parent = node.get("/Parent")
            node = resolve(parent) if parent is not None else None
        return None

    out = tmp_path / "uni.pdf"
    render(DOC, out)
    reader = PdfReader(str(out))
    checked = 0
    for page in reader.pages:
        resources = resources_of(page)
        fonts = resolve(resources.get("/Font")) if resources is not None else None
        if fonts is None:
            continue
        for ref in fonts.values():
            font = resolve(ref)
            checked += 1
            if "/ToUnicode" in font:
                continue
            descendants = font.get("/DescendantFonts")
            found_on_descendant = False
            if isinstance(descendants, ArrayObject):
                for descendant in descendants:
                    if "/ToUnicode" in resolve(descendant):
                        found_on_descendant = True
                        break
            assert found_on_descendant, f"font without ToUnicode: {font.get('/BaseFont')}"
    assert checked > 0, "no embedded fonts found; the assertion above never ran"
