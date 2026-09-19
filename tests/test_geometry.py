"""PDF geometry: line clustering, bullet-block segmentation, page fill.

Every test here renders the real fixture document once (module-scoped) and
measures its actual PDF, rather than asserting against a hand-built
expectation of what Typst *should* produce — the whole point of this module
is that Typst's own introspection cannot be trusted for line counts (see
`core.render.geometry`), so its tests can't trust a hand-rolled substitute
either.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.profile.models import Identity
from core.render.geometry import Line, Word, bullet_blocks, extract_lines, page_fill
from core.render.typst import render
from core.resume.models import Bullet, Entry, ResumeDoc, Section

LONG = (
    "Owned the migration of the nightly reporting pipeline to a partitioned store, "
    "coordinating the cutover across three separate teams and their release calendars"
)


def _doc() -> ResumeDoc:
    return ResumeDoc(
        identity=Identity(name="Dana Vex", email="dana@example.com"),
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
                        bullets=[
                            Bullet(text="Cut the queue to 300 items", sources=["a"]),
                            Bullet(text=LONG, sources=["b"]),
                        ],
                    ),
                ],
            )
        ],
    )


@pytest.fixture(scope="module")
def pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("geom") / "r.pdf"
    render(_doc(), out)
    return out


@pytest.mark.slow
def test_mixed_style_runs_are_one_line(pdf: Path) -> None:
    """The heading (bold, default size) and its right-aligned date/location
    run (9pt) share a baseline but land at slightly different `top`
    values because of font-metric differences between sizes. Rounding
    those tops to one decimal is exactly the failure mode this module's
    docstring warns about — it would split this single visual line into
    two. Mutation: setting `tolerance_ratio=0.0` on this call reproduces
    that split and fails the second assertion below.
    """
    lines = extract_lines(pdf)
    heading = [ln for ln in lines if "Northwind" in ln.text]
    assert len(heading) == 1, f"employer/location split across lines: {[h.text for h in heading]}"
    assert "Boston, MA" in heading[0].text


@pytest.mark.slow
def test_bullet_blocks_count_their_own_lines(pdf: Path) -> None:
    """Mutation: swapping the continuation-line test in `bullet_blocks`
    from `abs(line.x0 - body_x0) < 1.0` to an always-false condition
    (never attach continuations) would still find both bullet starts but
    collapse the long bullet to a single line, failing
    `blocks[1].line_count >= 2`.
    """
    blocks = bullet_blocks(extract_lines(pdf))
    assert len(blocks) == 2
    assert blocks[0].line_count == 1
    assert blocks[1].line_count >= 2, "the long bullet should wrap"


@pytest.mark.slow
def test_last_line_words_support_widow_detection(pdf: Path) -> None:
    """The long bullet's wrapped second line reads "teams and their
    release calendars" against the current render — five words — so the
    exact count is asserted rather than a `>= 1` bound: a bound that
    loose would pass just as well if `last_line_words` indexed the
    wrong line. Mutation: indexing `self.lines[0]` (the 17-word first
    line) instead of `self.lines[-1]` fails this exact-value assertion,
    where it would not have failed a `>= 1` check.

    This 5 is a measured golden, not an invariant. Re-baseline it only after
    inspecting the rendered PDF and confirming the new wrap point is
    correct — never by loosening it back toward a bound, which would restore
    exactly the unfalsifiable check this replaced.
    """
    blocks = bullet_blocks(extract_lines(pdf))
    assert blocks[1].last_line_words == 5


@pytest.mark.slow
def test_page_fill_is_a_fraction(pdf: Path) -> None:
    """A loose `0 < fill < 1` bound would pass even if `usable` were
    computed from the full page height instead of the margin-trimmed
    one: on this fixture that wrong denominator gives 0.1948, still
    inside `(0, 1)`. Measuring the real render puts the correct fraction
    at 0.2142 (`(bottom - top) / (792 - 72)` in Typst points for a
    US-letter page with the template's 0.5in y-margins), so the interval
    below is tight enough to separate the two. Mutation: dropping the
    `2 * 36.0` margin subtraction in `page_fill` reliably lands outside
    this range.

    This interval is a measured golden, not an invariant. Re-baseline it
    only after inspecting the rendered PDF and confirming the new fill is
    correct — never by widening it back toward `(0, 1)`, which would restore
    exactly the unfalsifiable check this replaced.
    """
    fills = page_fill(pdf)
    assert len(fills) == 1
    assert 0.20 < fills[0] < 0.23


@pytest.mark.slow
def test_bullet_marker_is_literal_text(pdf: Path) -> None:
    """`bullet_blocks` finds a bullet's start by looking for the marker
    glyph as an extracted word. That only works because the template
    emits it as real text in its own grid cell, not a drawn path — if a
    future template change switched to drawing the marker (a `circle()`
    or a vector shape), `extract_words` would simply stop reporting it
    and `bullet_blocks` would silently return zero blocks with no error.
    This test checks the assumption directly, independent of
    `bullet_blocks`'s own logic, so that failure mode is caught here
    rather than as a mysteriously empty block list downstream.

    Mutation: matching on the wrong glyph (e.g. `"◦"`) reliably fails
    this test against the real fixture, since the template's marker is
    `•`.
    """
    lines = extract_lines(pdf)
    markers = [w.text for ln in lines for w in ln.words if w.text == "•"]
    assert len(markers) == 2, "bullet marker glyph not found as extractable text"


@pytest.mark.slow
def test_geometry_snapshot_is_stable(pdf: Path) -> None:
    """A Typst upgrade, a font substitution, or a template layout change
    that alters line breaking must fail here, loudly, rather than
    silently changing what every downstream layout verdict counts as a
    bullet's length.

    These numbers are measured against the exact fixture defined above
    (`LONG`, ending "...their release calendars"), not assumed from an
    earlier draft: rendering it and inspecting the extracted lines shows
    the first bullet holds at one line and the long bullet wraps to
    exactly two — "...across three separate" is the wrap point, with
    "teams and their release calendars" as the second line. Re-baseline
    only after inspecting the rendered PDF again, never to make this
    assertion merely pass.

    Mutation: perturbing `tolerance_ratio` enough to merge two real lines
    (or split one) changes `observed` and fails this exact-list
    comparison — unlike the `>= 2` bound in
    `test_bullet_blocks_count_their_own_lines`, an off-by-one anywhere in
    the block has nowhere to hide.
    """
    blocks = bullet_blocks(extract_lines(pdf))
    observed = [b.line_count for b in blocks]
    assert observed == [1, 2], (
        f"line counts changed to {observed}; re-baseline only after inspecting the PDF"
    )


def _synthetic_bullet_lines(*continuation_x0: float) -> list[Line]:
    """A marker line at x=36 with body text at x=46, plus continuations.

    Built by hand rather than rendered so the horizontal offsets under test
    are exact. The body column at 46 is 10pt right of the marker at 36,
    matching the grid column width the real template uses.
    """
    marker = Line(
        page=0,
        top=100.0,
        words=[
            Word(text="\N{BULLET}", x0=36.0, x1=40.0, top=100.0),
            Word(text="Shipped", x0=46.0, x1=80.0, top=100.0),
        ],
    )
    rest = [
        Line(
            page=0,
            top=110.0 + i * 10,
            words=[Word(text="more", x0=x, x1=x + 30, top=110.0 + i * 10)],
        )
        for i, x in enumerate(continuation_x0)
    ]
    return [marker, *rest]


def test_continuation_slack_bounds_attachment_in_both_directions() -> None:
    """A wrapped line's left edge can sit a fraction of a point off the first
    body line's, so the column match is a tolerance rather than an equality.
    `continuation_slack` is the width of that tolerance, and both of its
    directions matter: too narrow drops real continuations and undercounts a
    bullet's length, too wide swallows the next heading and overcounts it.

    A line 0.5pt off the body column attaches at the 1.0pt default; one 2.0pt
    off does not, and closes the block. Raising the slack to 3.0pt attaches
    both. The 2.0pt case stands in for a heading at a nearby indent, which is
    what the default's margin exists to exclude.

    Mutation: hardcoding `1.0` in place of `continuation_slack` in the
    comparison leaves the default cases passing and fails the 3.0pt case,
    which reports 2 lines instead of 3.
    """
    lines = _synthetic_bullet_lines(46.5, 48.0)

    default = bullet_blocks(lines)
    assert default[0].line_count == 2, "a 0.5pt offset continuation should attach"

    loosened = bullet_blocks(lines, continuation_slack=3.0)
    assert loosened[0].line_count == 3, "a 2.0pt offset should attach once slack exceeds it"
