import re
from pathlib import Path
from typing import Any

import pdfplumber
import pytest

# `_space_advance` is private, and reaching across that boundary is normally
# not worth it. It is worth it here: the choice of a median over a mean is a
# documented design decision that no document this project renders can
# exercise -- the margins between real gaps are far too wide for the statistic
# to change any segmentation -- so the only instrument that can hold the
# decision to its claim is a direct unit test.
from core.checks.naive_extract import (
    _space_advance,  # pyright: ignore[reportPrivateUsage]
    naive_segments,
    naive_text,
    normalize,
)
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
def test_gap_threshold_holds_in_both_directions(tmp_path: Path) -> None:
    """The gap threshold must split a real field boundary while leaving an
    ordinary word gap alone, and one rendered row exercises both at once:
    "Northwind Analytics" is a two-word heading immediately followed on the
    same visual row by its right-aligned location, so the row contains a
    within-heading word gap (measured 2.5pt) and a templated field gap
    (measured 308.9pt) side by side.

    Asserted through `naive_segments` rather than by recomputing the
    threshold, deliberately. An arithmetic assertion would have to
    reproduce `_space_advance`'s formula, and a copy of that formula in a
    test cannot notice when the real one changes — it would keep passing
    while the production threshold moved underneath it. Segmentation is the
    behaviour that actually matters, so a change to how the threshold is
    computed fails here exactly when it changes what gets split, and stays
    silent when it does not.

    Mutations: raising `gap_factor`'s default to 70.0 puts the threshold
    above the 308.9pt field gap, merging the row into one segment and
    failing the two-segment assertion; lowering it to 0.3 puts the
    threshold below the 2.5pt word gap, splitting the heading into
    "Northwind" and "Analytics" and failing the exact-heading assertion. A
    template change that shrank body text far enough would break it the
    same way, which is the realistic trigger.
    """
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
    out = tmp_path / "gap.pdf"
    render(doc, out)

    row = next(r for r in naive_segments(out) if any("Northwind" in s for s in r))

    # The two assertions catch different drifts, and the first does not catch
    # every one on its own. A threshold below the 2.5pt heading gap splits the
    # heading and one above the 308.9pt field gap merges the whole row: both
    # show up here, so the message names both causes rather than guessing which
    # fired. But a threshold landing between the 4.5pt location/date join and
    # the field gap leaves the heading intact and splits the row into three, so
    # only the count assertion below sees it.
    assert row[0] == "Northwind Analytics", (
        f"the heading did not come back as exactly one segment: {row}. Either "
        "an ordinary word gap was split (threshold too low, e.g. body text "
        "shrank) or the field gap was merged (threshold too high)"
    )
    assert len(row) == 2, f"expected heading and location as two segments: {row}"
    assert "Boston" in row[1]


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


def test_space_advance_resists_a_single_outlier_token() -> None:
    """One pathologically wide token must not move the threshold for every
    other row on the page, which is the whole reason this is a median rather
    than a mean. Nine ordinary words at 5.0pt/char alongside one token at
    60.0pt/char must still report 5.0: the mean of the same list is 10.5,
    which would more than double the segment threshold and start merging
    fields that ought to split.

    This cannot be observed through `naive_segments` on any document this
    project renders -- the gap it would have to misclassify sits between
    4.5pt and 308.9pt, so even a doubled threshold changes nothing -- which
    is exactly why the statistic needs a unit test rather than a
    behavioural one. Without it, switching to a mean passes the entire
    suite.

    Mutation: replacing the sort-and-index with `sum(widths) / len(widths)`
    returns 10.5 and fails the first assertion.
    """
    ordinary = [{"text": "aaaa", "x0": 0.0, "x1": 20.0} for _ in range(9)]
    outlier = {"text": "W", "x0": 0.0, "x1": 60.0}
    words: list[dict[str, Any]] = [*ordinary, outlier]

    assert _space_advance(words) == 5.0

    advances = [(float(w["x1"]) - float(w["x0"])) / len(str(w["text"])) for w in words]
    assert sum(advances) / len(advances) == 10.5, (
        "the mean this median exists to avoid should differ sharply; if it no "
        "longer does, this fixture has stopped exercising the decision"
    )
