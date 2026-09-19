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
def test_en_dash_date_range_still_matches(tmp_path: Path) -> None:
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
    recoverable by substring search, so a presence-only gate passes it; 11 of
    24 are unreachable in declared order, so this one must not. The final
    assertion is the point of the test: it proves the weaker check would have
    passed, so a future change back to presence-only fails here rather than
    quietly widening what the gate accepts.
    """
    doc, out = build_two_column_resume(tmp_path / "scrambled.pdf", FONT_DIR, entries=24)
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
