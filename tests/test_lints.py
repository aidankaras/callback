from __future__ import annotations

from pathlib import Path

import pytest

from core.checks.lints import FIX_ORDER, Lint, check_lints
from core.profile.models import Identity
from core.render.geometry import bullet_blocks, extract_lines
from core.render.typst import render
from core.resume.models import Bullet, Entry, ResumeDoc, Section

LONG = (
    "Owned the migration of the nightly reporting pipeline to a partitioned columnar store, "
    "coordinating the cutover across three separate teams, their release calendars, and the "
    "downstream consumers who depended on the previous schema remaining"
)
SHORT = ["Cut the queue to 300 items", "Shipped the loader", "Wrote the runbook"]


def _entry(heading: str, bullets: list[str]) -> Entry:
    return Entry(heading=heading, bullets=[Bullet(text=b, sources=["a"]) for b in bullets])


def _doc(*entries: Entry) -> ResumeDoc:
    return ResumeDoc(
        identity=Identity(name="Dana Vex", email="d@example.com"),
        sections=[Section(title="Experience", entries=list(entries))],
    )


def _lints(doc: ResumeDoc, tmp_path: Path) -> list[Lint]:
    out = tmp_path / "resume.pdf"
    render(doc, out)
    return check_lints(doc, out)


@pytest.mark.slow
def test_a_clean_single_page_resume_has_no_lints(tmp_path: Path) -> None:
    assert _lints(_doc(_entry("Northwind", SHORT)), tmp_path) == []


@pytest.mark.slow
def test_a_three_line_bullet_is_flagged(tmp_path: Path) -> None:
    lints = _lints(_doc(_entry("Northwind", [LONG, *SHORT[:2]])), tmp_path)
    # LONG is sized so its third line holds one word, a real widow: one word
    # shorter and it fits on two lines. What matters most is that line count
    # is flagged and sorts first.
    (block, *_) = bullet_blocks(extract_lines(tmp_path / "resume.pdf"))
    assert (block.line_count, block.last_line_words) == (3, 1), "fixture no longer widows"
    assert lints[0].rule == "bullet_too_long", lints
    # Mutation: disable the widow rule -- this fails.
    assert "widow_line" in [lint.rule for lint in lints], lints


@pytest.mark.slow
def test_bullet_counts_are_per_role_not_per_document(tmp_path: Path) -> None:
    """Three roles of three bullets is an ordinary resume. Counting across the
    document would call it nine bullets and warn. Mutation: count bullets over
    the whole document -- this test fails."""
    doc = _doc(*(_entry(f"Employer {n}", SHORT) for n in range(3)))
    assert _lints(doc, tmp_path) == []


@pytest.mark.slow
def test_a_role_with_too_few_or_too_many_bullets_is_named(tmp_path: Path) -> None:
    many = [f"Delivered outcome number {i} for the platform team" for i in range(7)]
    lints = _lints(_doc(_entry("Northwind", SHORT[:1]), _entry("Harbor Lab", many)), tmp_path)
    assert [
        (lint.rule, "Northwind" in lint.detail or "Harbor Lab" in lint.detail) for lint in lints
    ] == [
        ("too_many_bullets", True),
        ("too_few_bullets", True),
    ], lints


@pytest.mark.slow
def test_an_entry_with_no_bullets_is_not_a_role_short_of_bullets(tmp_path: Path) -> None:
    doc = ResumeDoc(
        identity=Identity(name="Dana Vex", email="d@example.com"),
        sections=[
            Section(title="Experience", entries=[_entry("Northwind", SHORT)]),
            Section(title="Education", entries=[Entry(heading="Lakeshore State University")]),
        ],
    )
    assert _lints(doc, tmp_path) == []


@pytest.mark.parametrize(("employers", "orphan"), [(6, False), (9, True)])
@pytest.mark.slow
def test_page_count_and_an_orphan_tail_page(tmp_path: Path, employers: int, orphan: bool) -> None:
    """Measured: six employers fill 99% and 57% of two pages; nine fill 99%,
    99% and 32% of three. Both tails sit near the 0.5 threshold, one on each
    side. Mutation: disable the orphan rule -- (9, True) fails."""
    from core.render.geometry import page_fill

    doc = _doc(*(_entry(f"Employer {n}", [LONG[:150]] * 6) for n in range(employers)))
    out = tmp_path / "resume.pdf"
    render(doc, out)
    fills = page_fill(out)
    assert len(fills) > 1 and (fills[-1] < 0.5) is orphan, fills

    rules = [lint.rule for lint in check_lints(doc, out)]
    assert "page_count" in rules, rules
    assert ("orphan_tail_page" in rules) is orphan, rules


def test_fix_order_is_total_and_starts_with_line_count() -> None:
    assert FIX_ORDER[0] == "bullet_too_long"
    assert set(FIX_ORDER) == {
        "bullet_too_long",
        "too_many_bullets",
        "too_few_bullets",
        "page_count",
        "widow_line",
        "orphan_tail_page",
        "heading_only_page",
    }


def test_a_bullet_that_wraps_onto_the_last_page_is_content_on_it() -> None:
    """`BulletBlock.page` is where the bullet starts, so a bullet wrapping
    across the boundary leaves the page it spills onto looking empty.
    Mutation: read the block's page instead of its lines' -- this fails."""
    from core.checks.lints import carries_bullet
    from core.render.geometry import BulletBlock, Line, Word

    def line(page: int, text: str) -> Line:
        return Line(page=page, top=100.0, words=[Word(text=text, x0=0.0, x1=10.0, top=100.0)])

    wrapped = BulletBlock(page=0, lines=[line(0, "Cut the review queue"), line(1, "to 300 items")])
    assert wrapped.page == 0  # premise: the block reports the page it started on
    assert carries_bullet([wrapped], 1)
    assert not carries_bullet([BulletBlock(page=0, lines=[line(0, "all on one page")])], 1)


@pytest.mark.slow
def test_two_bullets_are_enough_for_a_role(tmp_path: Path) -> None:
    """A project or a short internship often has two things worth saying;
    padding it to three costs the page a line that says nothing."""
    assert _lints(_doc(_entry("Northwind", SHORT[:2])), tmp_path) == []

    (lint,) = _lints(_doc(_entry("Northwind", SHORT[:1])), tmp_path)

    assert lint.rule == "too_few_bullets"
    assert "1 bullet(s); at least 2 per role" in lint.detail


@pytest.mark.parametrize(
    ("employers", "pages", "max_pages", "flagged"),
    [(6, 2, 1, True), (6, 2, 2, False), (9, 3, 2, True)],
)
@pytest.mark.slow
def test_the_page_limit_is_the_callers(
    tmp_path: Path, employers: int, pages: int, max_pages: int, flagged: bool
) -> None:
    """How long a resume may run depends on whose it is: one page for a
    student, two for someone with a decade of roles."""
    from core.render.geometry import page_fill

    doc = _doc(*(_entry(f"Employer {n}", [LONG[:150]] * 6) for n in range(employers)))
    out = tmp_path / "resume.pdf"
    render(doc, out)
    assert len(page_fill(out)) == pages

    lints = check_lints(doc, out, max_pages=max_pages)
    found = [lint for lint in lints if lint.rule == "page_count"]

    assert bool(found) is flagged
    if flagged:
        assert found[0].detail == f"{pages} pages; at most {max_pages}"
