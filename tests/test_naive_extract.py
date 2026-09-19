import inspect
import re
from pathlib import Path

import pdfplumber
import pytest

from core.checks.naive_extract import naive_segments, naive_text, normalize
from core.render.typst import FONT_DIR
from tests.fixtures.make_two_column import build


def test_normalize_folds_dash_and_space_classes() -> None:
    assert normalize("Jun 2024 – Aug 2024") == normalize("Jun 2024 - Aug 2024")  # noqa: RUF001
    assert normalize("a\u00a0b") == "a b"
    # Zero-width characters are the ones that need their own table: unlike the
    # space classes, NFKC leaves them and `\s+` does not match them.
    assert normalize("infra\u200bstructure") == "infrastructure"
    assert normalize("soft\u00adhyphen") == "softhyphen"
    assert normalize("“quoted”") == '"quoted"'
    assert normalize("bullet • here") == "bullet here"


@pytest.mark.slow
def test_wide_gap_becomes_a_segment_boundary(tmp_path: Path) -> None:
    from core.profile.models import Identity
    from core.render.typst import render
    from core.resume.models import Entry, ResumeDoc, Section

    doc = ResumeDoc(
        identity=Identity(name="Dana Vex", email="d@example.com"),
        sections=[
            Section(
                title="Experience",
                entries=[
                    Entry(
                        heading="Northwind Analytics",
                        location="Boston, MA",
                        start="Jun 2024",
                        end="Aug 2025",
                    )
                ],
            )
        ],
    )
    out = tmp_path / "seg.pdf"
    render(doc, out)
    lines = naive_segments(out)
    employer_line = next(ln for ln in lines if any("Northwind" in s for s in ln))
    assert len(employer_line) >= 2, f"employer and location merged: {employer_line}"
    assert any("Boston" in s for s in employer_line[1:])


@pytest.mark.slow
def test_gap_threshold_separates_word_gap_from_field_gap(tmp_path: Path) -> None:
    """The `gap_factor * space` threshold `naive_segments` depends on must
    sit strictly between an ordinary word gap and a real field gap on a
    real render, not just in the module docstring's measured numbers. On
    this fixture's rendered PDF, "Northwind Analytics" is one heading with
    an ordinary within-heading word gap, immediately followed on the same
    visual row by "Boston, MA" — a real field boundary — so both a
    below-threshold and an above-threshold gap exist side by side to pin
    the threshold between.

    Mutation: moving `gap_factor`'s default in
    `core.checks.naive_extract.naive_segments` far enough in either
    direction breaks one of the two assertions below — raising it past
    roughly 65 pushes the threshold above the measured 308.9pt
    employer/location gap, and lowering it below roughly 0.5 pushes it
    below the measured 2.5pt heading word gap. A future template change
    that shrank body text far enough would move the measured gaps
    themselves and could break this the same way.
    """
    from core.profile.models import Identity
    from core.render.typst import render
    from core.resume.models import Entry, ResumeDoc, Section

    gap_factor = inspect.signature(naive_segments).parameters["gap_factor"].default

    doc = ResumeDoc(
        identity=Identity(name="Dana Vex", email="d@example.com"),
        sections=[
            Section(
                title="Experience",
                entries=[
                    Entry(
                        heading="Northwind Analytics",
                        location="Boston, MA",
                        start="Jun 2024",
                        end="Aug 2025",
                    )
                ],
            )
        ],
    )
    out = tmp_path / "gap.pdf"
    render(doc, out)

    with pdfplumber.open(str(out)) as pdf:
        page = pdf.pages[0]
        words = page.extract_words(use_text_flow=False)
        by_text = {str(w["text"]): w for w in words}
        # Recomputes the same median-per-character-advance formula
        # `naive_extract._space_advance` uses, rather than importing that
        # leading-underscore helper across the module boundary (pyright
        # strict flags that as `reportPrivateUsage`, and no other test in
        # this project reaches into a private symbol). Keep this in step
        # with `_space_advance` if that formula ever changes.
        char_widths = sorted(
            (float(w["x1"]) - float(w["x0"])) / max(len(str(w["text"])), 1)
            for w in words
            if str(w["text"]).strip()
        )
        space = char_widths[len(char_widths) // 2]
        threshold = gap_factor * space
        word_gap = float(by_text["Analytics"]["x0"]) - float(by_text["Northwind"]["x1"])
        field_gap = float(by_text["Boston,"]["x0"]) - float(by_text["Analytics"]["x1"])

    assert word_gap < threshold, (
        f"ordinary heading word gap {word_gap:.2f}pt is not below the "
        f"{threshold:.2f}pt threshold; body text may have shrunk"
    )
    assert field_gap > threshold, (
        f"employer/location field gap {field_gap:.2f}pt is not above the "
        f"{threshold:.2f}pt threshold"
    )


@pytest.mark.slow
def test_two_column_layout_scrambles_under_the_naive_extractor(tmp_path: Path) -> None:
    """The negative fixture must fail. A flow-order parser would pass it."""
    out = tmp_path / "two.pdf"
    build(out, FONT_DIR)

    # Guard the fixture before trusting the assertion it exists to support.
    # At count=39 -- exactly one column's capacity on this page -- the
    # "two-column" body renders entirely in column 1, every word lands in the
    # left half, and naive reading order comes out perfectly ascending. The
    # scramble assertion below would then pass a document that is not
    # scrambled and could never fail. Measured at count=70: page 0 carries
    # 490 words in total, and 217 of them sit in the right half.
    with pdfplumber.open(str(out)) as pdf:
        page = pdf.pages[0]
        right = [
            w for w in page.extract_words(use_text_flow=False) if float(w["x0"]) >= page.width / 2
        ]
    assert len(right) > 100, (
        f"fixture is not genuinely two-column: only {len(right)} words in the "
        "right half, so the scramble assertion below cannot fail"
    )

    order = [int(m) for m in re.findall(r"Alpha(\d+)", naive_text(out))]
    assert order, "no markers recovered"
    assert order != sorted(order), "two-column reading order was recovered; extractor is too smart"
    assert order[:4] == [1, 40, 2, 41], (
        f"expected column-interleaved order, got {order[:8]}; column 1 holds "
        "39 entries on this page, so a naive band sort reads 1, 40, 2, 41, ..."
    )
