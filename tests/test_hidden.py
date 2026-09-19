from __future__ import annotations

import unicodedata
from pathlib import Path

import pdfplumber
import pytest
from pypdf import PdfReader

from core.checks.hidden import MIN_FONT_SIZE, HiddenKind, check_hidden
from core.render.typst import FONT_DIR
from tests.fixtures.make_hidden import (
    BIDI_OVERRIDE,
    METADATA_LEAK,
    OFFPAGE,
    OFFPAGE_VERTICAL,
    RIGHT_TO_LEFT_OVERRIDE,
    SOFT_HYPHEN,
    SOFT_HYPHEN_CHAR,
    TINY,
    VARIATION_SELECTOR,
    VARIATION_SELECTOR_16,
    WHITE,
    ZERO_WIDTH,
    ZERO_WIDTH_SPACE,
    build,
)


@pytest.mark.slow
def test_white_on_white_text_is_detected(tmp_path: Path) -> None:
    out = tmp_path / "white.pdf"
    build(WHITE, out, FONT_DIR)

    # The fixture's own premise: at least one non-blank glyph is filled
    # near-white. Mutation: change WHITE's "fill: white" to "fill: black"
    # -- this assertion fails, proving it actually inspects the fixture's
    # colour rather than assuming the source text implies it.
    with pdfplumber.open(str(out)) as pdf:
        near_white = [
            c
            for c in pdf.pages[0].chars
            if str(c.get("text", "")).strip()
            and all(float(v) >= 0.95 for v in c.get("non_stroking_color") or ())
        ]
    assert near_white, "fixture did not render any near-white glyph"

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert any(f.kind == HiddenKind.INVISIBLE_TEXT for f in findings), findings


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
def test_soft_hyphen_is_caught_by_category_not_regex(tmp_path: Path) -> None:
    """A soft hyphen is category Cf but outside every _INVISIBLE_RANGES entry.

    Mutation: delete the unicodedata.category(...) in {"Cf", "Cn"} branch
    from core.checks.hidden._unicode_channel, leaving only the regex --
    this test fails, because the regex alone never matches U+00AD.
    """
    out = tmp_path / "soft_hyphen.pdf"
    build(SOFT_HYPHEN, out, FONT_DIR)

    with pdfplumber.open(str(out)) as pdf:
        raw = pdf.pages[0].extract_text() or ""
    assert SOFT_HYPHEN_CHAR in raw, repr(raw)
    assert unicodedata.category(SOFT_HYPHEN_CHAR) == "Cf"

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert any(f.kind == HiddenKind.UNICODE_CHANNEL for f in findings), findings


@pytest.mark.slow
def test_variation_selector_is_caught_by_regex_not_category(tmp_path: Path) -> None:
    """A variation selector is category Mn, not Cf/Cn, but is inside the
    U+FE00-U+FE0F range the regex covers.

    Mutation: delete the _INVISIBLE.search(text) branch from
    core.checks.hidden._unicode_channel, leaving only the category test --
    this test fails, because U+FE0F's category never matches {"Cf", "Cn"}.
    """
    out = tmp_path / "vs16.pdf"
    build(VARIATION_SELECTOR, out, FONT_DIR)

    with pdfplumber.open(str(out)) as pdf:
        raw = pdf.pages[0].extract_text() or ""
    assert VARIATION_SELECTOR_16 in raw, repr(raw)
    assert unicodedata.category(VARIATION_SELECTOR_16) == "Mn"

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert any(f.kind == HiddenKind.UNICODE_CHANNEL for f in findings), findings


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
    # -- the last assertion below fails, because the template's own 16pt
    # name heading would then fall under the floor.
    with pdfplumber.open(str(out)) as pdf:
        page = pdf.pages[0]
        sizes = {round(float(c["size"]), 2) for c in page.chars}
        colours = {c.get("non_stroking_color") for c in page.chars}
        raw = page.extract_text() or ""
    reader = PdfReader(str(out))
    metadata_keys = set(dict(reader.metadata or {}).keys())

    assert sizes == {9.0, 10.0, 11.0, 16.0}, sizes
    assert colours == {(0.0,)}, colours
    assert not any(unicodedata.category(ch) in {"Cf", "Cn"} for ch in raw)
    allowed = {"/Title", "/Author", "/Producer", "/Creator", "/CreationDate", "/ModDate"}
    assert metadata_keys <= allowed
    assert min(sizes) > MIN_FONT_SIZE

    verdict, findings = check_hidden(out)
    assert verdict == "pass", findings
