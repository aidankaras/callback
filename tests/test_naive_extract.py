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
