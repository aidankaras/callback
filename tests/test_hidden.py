from __future__ import annotations

from pathlib import Path

import pdfplumber
import pytest
from pypdf import PdfReader

from core.checks.hidden import (
    CANNOT_JUDGE,
    MIN_CONTRAST_RATIO,
    MIN_FONT_SIZE,
    HiddenKind,
    _invisible_fill,  # pyright: ignore[reportPrivateUsage]
    check_hidden,
    contrast_ratio,
    is_default_ignorable,
    is_explicit_colour,
    page_background,
    to_rgb,
)
from core.render.typst import FONT_DIR
from tests.fixtures.make_hidden import (
    ACCENTED_NAMES,
    BIDI_OVERRIDE,
    BYTE_ORDER_MARK,
    BYTE_ORDER_MARK_CHAR,
    COMBINING_GRAPHEME_JOINER,
    COMBINING_GRAPHEME_JOINER_CHAR,
    GRADIENT_BACKGROUND,
    GREY_INVISIBLE,
    GREY_LEGIBLE,
    JUSTIFIED_HYPHENATED,
    METADATA_LEAK,
    NAVY_BACKGROUND_WHITE_TEXT,
    NAVY_ON_NAVY,
    NO_TEXT_LAYER,
    OFFPAGE,
    OFFPAGE_VERTICAL,
    OUTLINE_HEADING,
    PATTERN_FILL,
    RIGHT_TO_LEFT_OVERRIDE,
    SOFT_HYPHEN,
    SOFT_HYPHEN_CHAR,
    THICK_WHITE_STROKE,
    TINY,
    VARIATION_SELECTOR,
    VARIATION_SELECTOR_16,
    WHITE,
    ZERO_WIDTH,
    ZERO_WIDTH_SPACE,
    build,
)
from tests.fixtures.make_raw_pdf import make


@pytest.mark.slow
def test_white_on_white_text_is_detected(tmp_path: Path) -> None:
    out = tmp_path / "white.pdf"
    build(WHITE, out, FONT_DIR)

    # The fixture's own premise: at least one non-blank glyph contrasts
    # below the floor against the page's actual (here, implicit white)
    # background. Mutation: change WHITE's "fill: white" to "fill: black"
    # -- this assertion fails, proving it actually inspects the fixture's
    # colour rather than assuming the source text implies it.
    with pdfplumber.open(str(out)) as pdf:
        page = pdf.pages[0]
        background = page_background(page)
        assert background is not None
        below_floor = [
            c
            for c in page.chars
            if str(c.get("text", "")).strip()
            and (fg := to_rgb(c.get("non_stroking_color"))) is not None
            and contrast_ratio(fg, background) < MIN_CONTRAST_RATIO
        ]
    assert below_floor, "fixture did not render any low-contrast glyph"

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert any(f.kind == HiddenKind.INVISIBLE_TEXT for f in findings), findings


@pytest.mark.slow
def test_grey_below_the_old_component_floor_is_detected(tmp_path: Path) -> None:
    """90% grey quantizes to 0.90196 and contrasts at 1.25:1 against white --
    below this module's 1.5:1 floor, but the pre-fix `>= 0.95` per-component
    test let it straight through (0.90196 < 0.95 on every component, so the
    old `_is_background` returned False -- "not background" -- for exactly
    the fill this fixture uses).

    Mutation: revert core.checks.hidden._invisible_fill to call the pre-fix
    `_is_background(fill) -> all components >= 0.95` test instead of a
    contrast check -- this test fails, since 0.90196 clears neither `>=
    0.95` component threshold and the old code would report this document
    as clean.
    """
    out = tmp_path / "grey_invisible.pdf"
    build(GREY_INVISIBLE, out, FONT_DIR)

    with pdfplumber.open(str(out)) as pdf:
        page = pdf.pages[0]
        background = page_background(page)
        assert background is not None
        greys = [
            c
            for c in page.chars
            if str(c.get("text", "")).strip()
            and (fg := to_rgb(c.get("non_stroking_color"))) is not None
            and 0.85 < fg[0] < 0.95
        ]
    assert greys, "fixture did not render any glyph under the old 0.95 floor"
    contrast = contrast_ratio(to_rgb(greys[0].get("non_stroking_color")) or (0, 0, 0), background)
    assert contrast == pytest.approx(1.248, abs=0.01), contrast
    assert contrast < MIN_CONTRAST_RATIO

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert any(f.kind == HiddenKind.INVISIBLE_TEXT for f in findings), findings


@pytest.mark.slow
def test_pale_grey_is_not_a_false_positive(tmp_path: Path) -> None:
    """80% grey contrasts at 1.61:1 against white -- just clear of the
    1.5:1 floor, and this fixture's whole point is that a legitimate
    faint design choice at that contrast must not be flagged.

    Mutation: lower MIN_CONTRAST_RATIO from 1.5 to 1.7 -- this test fails,
    since 1.61 < 1.7 would then flag this fixture's text as invisible.
    """
    out = tmp_path / "grey_legible.pdf"
    build(GREY_LEGIBLE, out, FONT_DIR)

    with pdfplumber.open(str(out)) as pdf:
        page = pdf.pages[0]
        background = page_background(page)
        assert background is not None
        styled = [
            c
            for c in page.chars
            if str(c.get("text", "")).strip()
            and (fg := to_rgb(c.get("non_stroking_color"))) is not None
            and 0.75 < fg[0] < 0.85
        ]
    assert styled, "fixture did not render any styled glyph"
    contrast = contrast_ratio(to_rgb(styled[0].get("non_stroking_color")) or (0, 0, 0), background)
    assert contrast == pytest.approx(1.606, abs=0.01), contrast
    assert contrast >= MIN_CONTRAST_RATIO

    verdict, findings = check_hidden(out)
    assert verdict == "pass", findings


@pytest.mark.slow
def test_navy_on_navy_text_is_detected(tmp_path: Path) -> None:
    """Text drawn in the same colour as a painted, non-white page
    background -- invisible for a reason the old white-only test could
    never see.

    Mutation: revert core.checks.hidden.page_background to always return
    white regardless of any painted rect -- this test fails, since the
    navy text here computed against an assumed white background contrasts
    at 11.63:1 (measured), comfortably above the 1.5:1 floor, so nothing
    would be flagged.
    """
    out = tmp_path / "navy_on_navy.pdf"
    build(NAVY_ON_NAVY, out, FONT_DIR)

    with pdfplumber.open(str(out)) as pdf:
        page = pdf.pages[0]
        background = page_background(page)
        assert background is not None
        assert background == pytest.approx((0.2, 0.2, 0.4), abs=0.01), background
        hidden_candidates = [
            c
            for c in page.chars
            if str(c.get("text", "")).strip()
            and (fg := to_rgb(c.get("non_stroking_color"))) is not None
            and contrast_ratio(fg, background) < MIN_CONTRAST_RATIO
        ]
    assert hidden_candidates, "fixture did not render any glyph invisible against its background"

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert any(f.kind == HiddenKind.INVISIBLE_TEXT for f in findings), findings


@pytest.mark.slow
def test_white_text_on_navy_background_is_not_a_false_positive(tmp_path: Path) -> None:
    """The false positive the old `>= 0.95`-from-white test made
    unavoidable: legible white text on a painted navy background, which
    the old test flagged as invisible purely because the text's own
    colour happened to be near white.

    Mutation: revert core.checks.hidden._invisible_fill to the pre-fix
    `_is_background(fill) -> fill near white` test -- this test then fails,
    since the white text here is near-white and would be flagged as
    invisible despite being the only legible colour choice against this
    background.
    """
    out = tmp_path / "navy_bg_white.pdf"
    build(NAVY_BACKGROUND_WHITE_TEXT, out, FONT_DIR)

    with pdfplumber.open(str(out)) as pdf:
        page = pdf.pages[0]
        background = page_background(page)
        assert background is not None
        assert background == pytest.approx((0.2, 0.2, 0.4), abs=0.01), background
        white_chars = [
            c
            for c in page.chars
            if str(c.get("text", "")).strip()
            and (fg := to_rgb(c.get("non_stroking_color"))) is not None
            and fg[0] > 0.9
        ]
    assert white_chars, "fixture did not render any near-white glyph"
    contrast = contrast_ratio((1.0, 1.0, 1.0), background)
    assert contrast > 10, contrast

    verdict, findings = check_hidden(out)
    assert verdict == "pass", findings


@pytest.mark.slow
def test_default_stroke_does_not_rescue_invisible_fill(tmp_path: Path) -> None:
    """`pdfplumber` reports a stroke colour for every character, including
    ordinary text that never set one -- confirmed directly: an untouched
    stroke colour is `(0,)`, a plain Python `int`, because `pdfminer`
    initializes it to the literal `int` `0` and only ever replaces it with
    a `float`-valued tuple built from a real colour operator. Reading the
    stroke naively, without `is_explicit_colour`'s guard, would let that
    default -- black, always high-contrast against a light page -- rescue
    the contrast of any invisible white-fill glyph for free.

    Mutation: remove the `is_explicit_colour(raw_stroke)` guard from
    core.checks.hidden._invisible_fill, treating every reported stroke
    colour as real -- this test fails, since WHITE's fill-only glyphs
    would then read as visible through their untouched default black
    stroke.
    """
    out = tmp_path / "white_for_stroke_guard.pdf"
    build(WHITE, out, FONT_DIR)

    with pdfplumber.open(str(out)) as pdf:
        page = pdf.pages[0]
        payload_chars = [
            c
            for c in page.chars
            if str(c.get("text", "")).strip() and c["text"] not in "Visible line of the document."
        ]
    assert payload_chars, "fixture did not render any payload glyph"
    # Fixture premise: this fixture never sets a stroke, so pdfplumber's
    # reported stroke colour for its payload text is the untouched
    # pdfminer default, not a real, explicit colour.
    assert not is_explicit_colour(payload_chars[0]["stroking_color"]), payload_chars[0][
        "stroking_color"
    ]

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert any(f.kind == HiddenKind.INVISIBLE_TEXT for f in findings), findings


@pytest.mark.slow
def test_outline_heading_is_not_a_false_positive(tmp_path: Path) -> None:
    """The false positive a fill-only check cannot avoid: white fill
    (matching the page) traced with an explicit, high-contrast black
    stroke -- a legible outlined heading, not hidden text. The fill alone
    contrasts at 1.0:1, indistinguishable from genuine white-on-white
    text, but the stroke that actually delineates every letter contrasts
    at roughly 21:1.

    Mutation: remove the stroke-reading branch from
    core.checks.hidden._invisible_fill, leaving only the fill's own
    contrast -- this test fails, since the fill alone reads as invisible.
    """
    out = tmp_path / "outline.pdf"
    build(OUTLINE_HEADING, out, FONT_DIR)

    with pdfplumber.open(str(out)) as pdf:
        page = pdf.pages[0]
        background = page_background(page)
        assert background is not None
        heading_chars = [
            c
            for c in page.chars
            if str(c.get("text", "")).strip() and c["text"] not in "Visible line of the document."
        ]
    assert heading_chars, "fixture did not render any heading glyph"
    char = heading_chars[0]
    fill = to_rgb(char["non_stroking_color"])
    stroke_raw = char["stroking_color"]
    assert fill is not None
    # Fixture premise: the fill alone would fail contrast, and the stroke
    # really is an explicit, high-contrast colour -- both are load-bearing
    # for this test to mean anything.
    assert contrast_ratio(fill, background) < MIN_CONTRAST_RATIO
    assert is_explicit_colour(stroke_raw)
    stroke = to_rgb(stroke_raw)
    assert stroke is not None
    assert contrast_ratio(stroke, background) >= MIN_CONTRAST_RATIO

    verdict, findings = check_hidden(out)
    assert verdict == "pass", findings


@pytest.mark.slow
def test_thick_occluding_stroke_is_a_documented_blind_spot(tmp_path: Path) -> None:
    """A fill (black) that contrasts perfectly well on its own, paired with
    an explicit, low-contrast stroke (white) wide enough (3pt on 10pt text)
    to paint over nearly all of that fill -- rendered directly to an image,
    the payload is blank but for a single stray mark. `_invisible_fill`
    takes the *better* of the fill's and the stroke's own contrast against
    the background (see its docstring for why), so the fill's good contrast
    alone reports this glyph as visible. Distinguishing this from a
    thin, genuinely decorative stroke needs the stroke's actual width,
    which `pdfplumber` does not expose per character -- see the module
    docstring's "Documented, not implemented" section.

    This fixture is not a regression test in the usual sense: no mutation
    of `_invisible_fill`'s combining rule closes this gap without breaking
    `test_outline_heading_is_not_a_false_positive` above (verified: trusting
    an explicit stroke outright, instead of the better of the two, flags a
    decorative low-contrast stroke on already-legible fill text as
    invisible). It exists to keep this known gap under a real, running
    fixture rather than only as prose.
    """
    out = tmp_path / "thick_stroke.pdf"
    build(THICK_WHITE_STROKE, out, FONT_DIR)

    with pdfplumber.open(str(out)) as pdf:
        page = pdf.pages[0]
        background = page_background(page)
        assert background is not None
        payload_chars = [
            c
            for c in page.chars
            if str(c.get("text", "")).strip() and c["text"] not in "Visible line of the document."
        ]
    assert payload_chars, "fixture did not render any payload glyph"
    char = payload_chars[0]
    fill = to_rgb(char["non_stroking_color"])
    stroke_raw = char["stroking_color"]
    assert fill is not None
    # Fixture premise: the fill contrasts fine on its own, and the stroke
    # really is an explicit, low-contrast colour -- exactly the shape that
    # defeats the "better of the two" rule.
    assert contrast_ratio(fill, background) >= MIN_CONTRAST_RATIO
    assert is_explicit_colour(stroke_raw)
    stroke = to_rgb(stroke_raw)
    assert stroke is not None
    assert contrast_ratio(stroke, background) < MIN_CONTRAST_RATIO

    verdict, findings = check_hidden(out)
    assert verdict == "pass", findings


@pytest.mark.slow
def test_gradient_background_reports_unknown(tmp_path: Path) -> None:
    """A gradient page fill still produces the page-covering rect
    `page_background` looks for, but that rect's colour is a pattern name
    (`"p0"`), not a colour `to_rgb` can parse.

    Mutation: revert core.checks.hidden.page_background to fall back to
    white whenever it found *any* colour on a qualifying rect, dropping
    the `found_unreadable` tracking -- this test fails, since the page
    would then silently default to a white background instead of
    reporting `background_unknown`.
    """
    out = tmp_path / "gradient.pdf"
    build(GRADIENT_BACKGROUND, out, FONT_DIR)

    with pdfplumber.open(str(out)) as pdf:
        page = pdf.pages[0]
        covering_rects = [
            r
            for r in page.rects
            if r.get("fill")
            and float(r.get("width", 0)) * float(r.get("height", 0))
            >= 0.9 * float(page.width) * float(page.height)
        ]
        assert covering_rects, "fixture did not render a page-covering rect"
        colour = covering_rects[0]["non_stroking_color"]
        assert isinstance(colour, str), colour
        assert to_rgb(colour) is None
        assert page_background(page) is None

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert any(f.kind == HiddenKind.BACKGROUND_UNKNOWN for f in findings), findings


@pytest.mark.slow
def test_one_point_text_is_detected(tmp_path: Path) -> None:
    out = tmp_path / "tiny.pdf"
    build(TINY, out, FONT_DIR)

    # Fixture premise: a glyph really is sized under the floor. Mutation:
    # change TINY's "size: 1pt" to "size: 8pt" -- this assertion fails.
    with pdfplumber.open(str(out)) as pdf:
        under_floor = [c for c in pdf.pages[0].chars if float(c.get("size", 99)) < MIN_FONT_SIZE]
    assert under_floor, "fixture did not render any glyph under the size floor"

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert any(f.kind == HiddenKind.TINY_TEXT for f in findings), findings


@pytest.mark.slow
def test_zero_width_character_is_detected(tmp_path: Path) -> None:
    out = tmp_path / "zw.pdf"
    build(ZERO_WIDTH, out, FONT_DIR)

    # Fixture premise: the compiled PDF's extracted text really carries
    # U+200B. Mutation: change ZERO_WIDTH to place the zero-width space
    # immediately before a space instead of mid-word -- Typst drops it
    # there, this count goes to 0, and this assertion fails before the
    # gate is even consulted.
    with pdfplumber.open(str(out)) as pdf:
        raw = pdf.pages[0].extract_text() or ""
    assert raw.count(ZERO_WIDTH_SPACE) == 4, repr(raw)

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert any(f.kind == HiddenKind.UNICODE_CHANNEL for f in findings), findings


@pytest.mark.slow
def test_soft_hyphen_is_not_flagged(tmp_path: Path) -> None:
    """A soft hyphen (U+00AD) survives rendering but is deliberately absent
    from `_DEFAULT_IGNORABLE_RANGES` -- see the note beside that table for
    why: it carries no letters, so it cannot smuggle a keyword, and it is a
    routine artefact of ordinary hyphenated, justified typesetting.

    Mutation: add (0x00AD, 0x00AD) back into
    core.checks.hidden._DEFAULT_IGNORABLE_RANGES -- this test fails, since
    the character this fixture's own premise confirms is present would
    then be flagged.
    """
    out = tmp_path / "soft_hyphen.pdf"
    build(SOFT_HYPHEN, out, FONT_DIR)

    with pdfplumber.open(str(out)) as pdf:
        raw = pdf.pages[0].extract_text() or ""
    assert SOFT_HYPHEN_CHAR in raw, repr(raw)
    assert not is_default_ignorable(raw)

    verdict, findings = check_hidden(out)
    assert verdict == "pass", findings


@pytest.mark.slow
def test_justified_hyphenation_is_not_flagged(tmp_path: Path) -> None:
    """A realistic ordinarily-hyphenated, justified column -- the shape a
    genuine imported resume would actually produce, not a single contrived
    character. Measured: this fixture's own paragraph, at this column
    width, produces four soft hyphens.

    Mutation: add (0x00AD, 0x00AD) back into
    core.checks.hidden._DEFAULT_IGNORABLE_RANGES -- this test fails, since
    a document that legitimately contains four of them would then fail the
    gate entirely.
    """
    out = tmp_path / "justified.pdf"
    build(JUSTIFIED_HYPHENATED, out, FONT_DIR)

    with pdfplumber.open(str(out)) as pdf:
        raw = pdf.pages[0].extract_text() or ""
    assert raw.count(SOFT_HYPHEN_CHAR) == 4, repr(raw)

    verdict, findings = check_hidden(out)
    assert verdict == "pass", findings


@pytest.mark.slow
def test_variation_selector_is_detected(tmp_path: Path) -> None:
    """A text variation selector (U+FE0F) is inside the
    Default_Ignorable_Code_Point range table's U+FE00-U+FE0F entry.

    Mutation: delete the (0xFE00, 0xFE0F) tuple from
    core.checks.hidden._DEFAULT_IGNORABLE_RANGES -- this test fails, since
    no other entry in the table covers U+FE0F.
    """
    out = tmp_path / "vs16.pdf"
    build(VARIATION_SELECTOR, out, FONT_DIR)

    with pdfplumber.open(str(out)) as pdf:
        raw = pdf.pages[0].extract_text() or ""
    assert VARIATION_SELECTOR_16 in raw, repr(raw)

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert any(f.kind == HiddenKind.UNICODE_CHANNEL for f in findings), findings


@pytest.mark.slow
def test_byte_order_mark_is_detected(tmp_path: Path) -> None:
    """A byte-order mark (U+FEFF) is inside the Default_Ignorable_Code_Point
    range table's own entry for it.

    Mutation: delete the (0xFEFF, 0xFEFF) tuple from
    core.checks.hidden._DEFAULT_IGNORABLE_RANGES -- this test fails, since
    no other entry in the table covers U+FEFF.
    """
    out = tmp_path / "bom.pdf"
    build(BYTE_ORDER_MARK, out, FONT_DIR)

    with pdfplumber.open(str(out)) as pdf:
        raw = pdf.pages[0].extract_text() or ""
    assert BYTE_ORDER_MARK_CHAR in raw, repr(raw)

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert any(f.kind == HiddenKind.UNICODE_CHANNEL for f in findings), findings


@pytest.mark.slow
def test_combining_grapheme_joiner_is_detected(tmp_path: Path) -> None:
    """A combining grapheme joiner (U+034F) is category Mn, not Cf/Cn, and
    falls outside every range the pre-fix regex checked -- the code point
    that regex-or-category design missed by both mechanisms simultaneously,
    and the reason this module now checks the full
    Default_Ignorable_Code_Point set instead of an ad hoc approximation.

    Mutation: delete the (0x034F, 0x034F) tuple from
    core.checks.hidden._DEFAULT_IGNORABLE_RANGES -- this test fails, since
    no other entry in the table covers U+034F.
    """
    out = tmp_path / "cgj.pdf"
    build(COMBINING_GRAPHEME_JOINER, out, FONT_DIR)

    with pdfplumber.open(str(out)) as pdf:
        raw = pdf.pages[0].extract_text() or ""
    assert COMBINING_GRAPHEME_JOINER_CHAR in raw, repr(raw)

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert any(f.kind == HiddenKind.UNICODE_CHANNEL for f in findings), findings


@pytest.mark.slow
def test_accented_names_are_not_flagged(tmp_path: Path) -> None:
    """Three ordinary names, each spelled with a decomposed combining
    accent -- the same Unicode category (Mn) as the combining grapheme
    joiner above, but not itself default-ignorable. Proves the property
    checked is "is this specified to render with no glyph," not "is this a
    combining mark," which would also reject every one of these.

    Mutation: widen core.checks.hidden._unicode_channel to flag any
    character whose unicodedata.category is "Mn" (in addition to the
    default-ignorable regex) -- this test fails, since every accent below
    is category Mn and would then be flagged.
    """
    out = tmp_path / "accented.pdf"
    build(ACCENTED_NAMES, out, FONT_DIR)

    with pdfplumber.open(str(out)) as pdf:
        raw = pdf.pages[0].extract_text() or ""
    assert "Jos" in raw and "ller" in raw and "koda" in raw, repr(raw)
    assert not is_default_ignorable(raw), repr(raw)

    verdict, findings = check_hidden(out)
    assert verdict == "pass", findings


@pytest.mark.slow
def test_bidi_override_gets_its_own_finding_kind(tmp_path: Path) -> None:
    """A right-to-left override reverses extracted order without hiding
    anything: every letter in BIDI_OVERRIDE remains fully visible.

    Mutation: delete the _BIDI_CONTROL.search(text) branch from
    core.checks.hidden._unicode_channel -- this test fails, since only the
    generic unicode_channel finding (still produced by the shared scan)
    would remain, never bidi_override.
    """
    out = tmp_path / "bidi.pdf"
    build(BIDI_OVERRIDE, out, FONT_DIR)

    # Fixture premise: the override really did reverse extraction order
    # relative to the eight letters typed in BIDI_OVERRIDE.
    with pdfplumber.open(str(out)) as pdf:
        raw = pdf.pages[0].extract_text() or ""
    assert RIGHT_TO_LEFT_OVERRIDE in raw
    assert raw.strip() != "AlphaBeta"

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert any(f.kind == HiddenKind.BIDI_OVERRIDE for f in findings), findings
    assert any(f.kind == HiddenKind.UNICODE_CHANNEL for f in findings), findings


@pytest.mark.slow
def test_offpage_text_horizontal_is_detected(tmp_path: Path) -> None:
    out = tmp_path / "offpage.pdf"
    build(OFFPAGE, out, FONT_DIR)

    # Fixture premise: at least one glyph really lands past each horizontal
    # edge. Mutation: change both #place calls' dx to 0pt -- both of these
    # assertions fail.
    with pdfplumber.open(str(out)) as pdf:
        page = pdf.pages[0]
        min_x0 = min(float(c["x0"]) for c in page.chars)
        max_x1 = max(float(c["x1"]) for c in page.chars)
    assert min_x0 < 0, min_x0
    assert max_x1 > 612, max_x1
    # The leftward block's minimum x0 is set by dx and the page margin
    # alone, independent of its own text length -- see the fixture module's
    # comment on OFFPAGE -- so this exact value is a stable oracle.
    assert min_x0 == pytest.approx(-164.0)

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert any(f.kind == HiddenKind.OFFPAGE_TEXT for f in findings), findings


@pytest.mark.slow
def test_offpage_text_vertical_is_detected(tmp_path: Path) -> None:
    out = tmp_path / "offpage_vertical.pdf"
    build(OFFPAGE_VERTICAL, out, FONT_DIR)

    # Fixture premise: a glyph really lands below the page bottom.
    # Mutation: change OFFPAGE_VERTICAL's dy from 900pt to 100pt (back on
    # the page) -- this assertion fails.
    with pdfplumber.open(str(out)) as pdf:
        page = pdf.pages[0]
        max_bottom = max(float(c["bottom"]) for c in page.chars)
        min_x0 = min(float(c["x0"]) for c in page.chars)
        max_x1 = max(float(c["x1"]) for c in page.chars)
    assert max_bottom > 792, max_bottom
    # And this fixture stays on the page horizontally -- it isolates the
    # vertical axis, so a detector that only ever compared x0/x1 against
    # page width would find nothing here and this next check would fail if
    # _offpage_text regressed to that horizontal-only shape.
    assert min_x0 >= 0
    assert max_x1 <= 612

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert any(f.kind == HiddenKind.OFFPAGE_TEXT for f in findings), findings


@pytest.mark.slow
def test_metadata_leak_is_detected(tmp_path: Path) -> None:
    out = tmp_path / "metadata.pdf"
    build(METADATA_LEAK, out, FONT_DIR)

    # Fixture premise: /Keywords really is present with real content.
    # Mutation: drop keywords: (...) from METADATA_LEAK's
    # #set document(...) call -- this assertion fails.
    reader = PdfReader(str(out))
    metadata = dict(reader.metadata or {})
    assert metadata.get("/Keywords"), metadata

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert any(f.kind == HiddenKind.METADATA_LEAK for f in findings), findings


@pytest.mark.slow
def test_clean_render_passes(tmp_path: Path) -> None:
    from core.profile.models import Identity
    from core.render.typst import render
    from core.resume.models import Entry, ResumeDoc, Section

    doc = ResumeDoc(
        identity=Identity(name="Dana Vex", email="d@example.com"),
        sections=[Section(title="Experience", entries=[Entry(heading="Northwind")])],
    )
    out = tmp_path / "clean.pdf"
    render(doc, out)

    # The premise this test depends on, enforced rather than assumed: a
    # real render of this template stays comfortably clear of every
    # threshold this gate checks. Mutation: lower MIN_FONT_SIZE past 16.0
    # -- the size assertion below fails, because the template's own 16pt
    # name heading would then fall under the floor.
    with pdfplumber.open(str(out)) as pdf:
        page = pdf.pages[0]
        sizes = {round(float(c["size"]), 2) for c in page.chars}
        colours = {c.get("non_stroking_color") for c in page.chars}
        raw = page.extract_text() or ""
        background = page_background(page)
    reader = PdfReader(str(out))
    metadata_keys = set(dict(reader.metadata or {}).keys())

    assert sizes == {9.0, 10.0, 11.0, 16.0}, sizes
    assert colours == {(0.0,)}, colours
    assert background is not None
    assert background == (1.0, 1.0, 1.0), background
    assert contrast_ratio((0.0, 0.0, 0.0), background) > MIN_CONTRAST_RATIO
    assert not is_default_ignorable(raw)
    allowed = {"/Title", "/Author", "/Producer", "/Creator", "/CreationDate", "/ModDate"}
    assert metadata_keys <= allowed
    assert min(sizes) > MIN_FONT_SIZE

    verdict, findings = check_hidden(out)
    assert verdict == "pass", findings


@pytest.mark.slow
def test_pattern_filled_text_is_reported_rather_than_read_as_visible(tmp_path: Path) -> None:
    """Mutation: restore the old guard in `_invisible_fill`, which only
    judged a glyph when at least one contrast could be computed -- this
    test fails, because an unreadable fill then counts as visible."""
    out = tmp_path / "pattern.pdf"
    build(PATTERN_FILL, out, FONT_DIR)

    with pdfplumber.open(str(out)) as pdf:
        page = pdf.pages[0]
        payload = [c for c in page.chars if c["text"] == "K"]
        assert payload, "fixture did not render the payload"
        assert to_rgb(payload[0]["non_stroking_color"]) is None
        assert "Kubernetes" in (page.extract_text() or "")
        assert page_background(page) == (1.0, 1.0, 1.0)

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert [f.kind for f in findings] == [HiddenKind.FILL_UNKNOWN], findings


@pytest.mark.slow
def test_a_page_with_no_text_layer_is_reported(tmp_path: Path) -> None:
    """Mutation: drop the empty-page branch in `check_hidden` -- this test
    fails, because every per-character check iterates nothing and the page
    passes exactly like a legible one."""
    out = tmp_path / "textless.pdf"
    build(NO_TEXT_LAYER, out, FONT_DIR)

    with pdfplumber.open(str(out)) as pdf:
        page = pdf.pages[0]
        assert page.chars == []
        assert page.rects, "fixture did not render any ink"

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert [f.kind for f in findings] == [HiddenKind.NO_TEXT_LAYER], findings


class _DegeneratePage:
    def __init__(self) -> None:
        self.width = 612.0
        self.height = 0.0
        self.rects: list[dict[str, object]] = []


def test_a_zero_area_page_has_no_known_background() -> None:
    """Mutation: fall back to white when the page has no area -- this test
    fails. Typst never emits such a page; an imported PDF can."""
    assert page_background(_DegeneratePage()) is None


def test_a_pdf_with_no_pages_is_reported(tmp_path: Path) -> None:
    out = tmp_path / "empty.pdf"
    make(out, b"", npages=0)
    with pdfplumber.open(str(out)) as pdf:
        assert pdf.pages == []

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert [f.kind for f in findings] == [HiddenKind.NO_PAGES], findings


def test_a_page_whose_only_text_is_whitespace_has_no_text_layer(tmp_path: Path) -> None:
    out = tmp_path / "spaces.pdf"
    make(out, b"BT /F1 12 Tf 72 700 Td (     ) Tj ET")
    with pdfplumber.open(str(out)) as pdf:
        chars = pdf.pages[0].chars
        assert chars and all(not str(c["text"]).strip() for c in chars)

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert [f.kind for f in findings] == [HiddenKind.NO_TEXT_LAYER], findings


def test_an_unreadable_fill_does_not_hide_invisible_text_after_it(tmp_path: Path) -> None:
    """Mutation: return on the first `fill_unknown` glyph again -- this test
    fails, because the white payload later on the page is never reached."""
    out = tmp_path / "both.pdf"
    make(
        out,
        b"/Pattern cs /P0 scn BT /F1 12 Tf 72 720 Td (Dana Vex) Tj ET "
        b"1 g BT /F1 12 Tf 72 700 Td (Python Kubernetes) Tj ET",
    )
    with pdfplumber.open(str(out)) as pdf:
        fills = {str(c["text"]): c["non_stroking_color"] for c in pdf.pages[0].chars}
        assert to_rgb(fills["D"]) is None and to_rgb(fills["P"]) == (1.0, 1.0, 1.0)

    _, findings = check_hidden(out)
    assert sorted(f.kind for f in findings) == sorted(
        [HiddenKind.FILL_UNKNOWN, HiddenKind.INVISIBLE_TEXT]
    ), findings


def test_an_unset_fill_is_judged_as_the_pdf_default_black() -> None:
    """PDF's initial fill colour is black. Mutation: skip a `None` fill
    again -- this test fails, since black-on-black then counts as visible."""

    class _Page:
        def __init__(self) -> None:
            self.chars = [{"text": "K", "non_stroking_color": None, "stroking_color": None}]

    findings = _invisible_fill(_Page(), 0, (0.0, 0.0, 0.0))
    assert [f.kind for f in findings] == [HiddenKind.INVISIBLE_TEXT]


def test_text_in_an_unpainted_rendering_mode_is_reported(tmp_path: Path) -> None:
    """Rendering mode 3 draws no ink but leaves the text extractable.
    Mutation: skip the rendering-mode scan -- this test fails."""
    out = tmp_path / "tr3.pdf"
    make(
        out,
        b"BT /F1 12 Tf 72 700 Td (Visible line) Tj ET "
        b"BT 3 Tr /F1 12 Tf 72 600 Td (Kubernetes TensorFlow) Tj ET",
    )
    with pdfplumber.open(str(out)) as pdf:
        assert "Kubernetes" in (pdf.pages[0].extract_text() or "")

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert [f.kind for f in findings] == [HiddenKind.UNPAINTED_TEXT], findings


@pytest.mark.parametrize(
    "stream",
    [
        # The mode is restored by Q before any text is shown.
        b"q 3 Tr Q BT /F1 12 Tf 72 700 Td (Visible line) Tj ET",
        # The mode is set but nothing is ever shown in it.
        b"BT /F1 12 Tf 72 700 Td (Visible line) Tj 3 Tr ET",
    ],
)
def test_an_unpainted_mode_with_no_text_shown_in_it_is_not_flagged(
    tmp_path: Path, stream: bytes
) -> None:
    """Mutation: flag any `3 Tr` operator regardless of state -- both fail."""
    out = tmp_path / "tr.pdf"
    make(out, stream)
    assert check_hidden(out) == ("pass", [])


@pytest.mark.parametrize(
    ("page", "form"),
    [
        # The form sets the unpainted mode itself.
        (b"/X1 Do", b"BT 3 Tr /F1 12 Tf 72 600 Td (Kubernetes TensorFlow) Tj ET"),
        # The page sets it and the form inherits it: a form XObject runs in
        # the graphics state in force at `Do`.
        (b"3 Tr /X1 Do", b"BT /F1 12 Tf 72 600 Td (Kubernetes TensorFlow) Tj ET"),
    ],
)
def test_unpainted_text_inside_a_form_xobject_is_reported(
    tmp_path: Path, page: bytes, form: bytes
) -> None:
    """Mutation: stop recursing into form XObjects -- both cases fail."""
    out = tmp_path / "form.pdf"
    make(out, b"BT /F1 12 Tf 72 700 Td (Visible line) Tj ET " + page, form=form)
    with pdfplumber.open(str(out)) as pdf:
        assert "Kubernetes" in (pdf.pages[0].extract_text() or "")

    _, findings = check_hidden(out)
    assert [f.kind for f in findings] == [HiddenKind.UNPAINTED_TEXT], findings


def test_a_form_restores_the_mode_it_changed(tmp_path: Path) -> None:
    """A form's own mode change ends with the form. Mutation: let it leak out."""
    out = tmp_path / "form.pdf"
    make(
        out,
        b"/X1 Do BT /F1 12 Tf 72 700 Td (Visible line) Tj ET",
        form=b"3 Tr",
    )
    assert check_hidden(out) == ("pass", [])


def test_forms_nested_past_the_limit_are_reported_not_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lowering the limit sends an ordinary one-level form past it, which is
    the same path a self-drawing form takes. Mutation: return None instead of
    raising at the limit -- this test fails."""
    monkeypatch.setattr("core.checks.hidden._MAX_FORM_DEPTH", 0)
    out = tmp_path / "deep.pdf"
    make(out, b"/X1 Do", form=b"BT /F1 12 Tf 72 600 Td (Visible line) Tj ET")
    _, findings = check_hidden(out)
    assert [f.kind for f in findings] == [HiddenKind.FORM_TOO_DEEP], findings
    assert HiddenKind.FORM_TOO_DEEP in CANNOT_JUDGE
