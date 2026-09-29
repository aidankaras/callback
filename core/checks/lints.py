"""Layout lints: advice about how a rendered resume reads, never a verdict.

These rules constrain one underlying quantity, how much text the document
contains, so they are coupled. Shortening a bullet can drop a role below the
bullet floor, and padding a widow can push a bullet onto a third line.
`FIX_ORDER` exists so anything that resolves them works in one direction
rather than oscillating between two rules that disagree.

Line-level rules are measured from the rendered page's geometry. Bullets per
role are counted from the document, because the page does not know which
bullets belong to which role. An entry with no bullets at all (an education
line, say) is a deliberate header-only entry, not a role short of bullets, so
the floor skips it.

There is deliberately no page-fill floor. Combined with the bullet caps it
admits no solution for a candidate with two roles, and the only ways to
satisfy it are to invent content or shrink the type below a legible size.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from core.render.geometry import BulletBlock, bullet_blocks, extract_lines, page_fill
from core.resume.models import ResumeDoc

FIX_ORDER: tuple[str, ...] = (
    "bullet_too_long",
    "too_many_bullets",
    "too_few_bullets",
    "page_count",
    "widow_line",
    "orphan_tail_page",
    "heading_only_page",
)

ORPHAN_TAIL_FILL = 0.5
HEADING_ONLY_FILL = 0.15


@dataclass(frozen=True)
class Lint:
    rule: str
    detail: str

    @property
    def priority(self) -> int:
        return FIX_ORDER.index(self.rule)


def carries_bullet(blocks: Sequence[BulletBlock], page: int) -> bool:
    """Whether any bullet puts text on `page`.

    A block's own `page` is where its marker line starts, not where it ends,
    so a bullet that wraps across the boundary would leave the page it spills
    onto looking empty of content. Each line carries its own page, and those
    are what that page actually holds.
    """
    return any(line.page == page for block in blocks for line in block.lines)


def check_lints(
    doc: ResumeDoc,
    pdf_path: Path,
    *,
    max_bullet_lines: int = 2,
    min_bullets: int = 2,
    max_bullets: int = 6,
    max_pages: int = 1,
) -> list[Lint]:
    """Every layout lint for `doc` as rendered at `pdf_path`, in `FIX_ORDER`."""
    blocks = bullet_blocks(extract_lines(pdf_path))
    fills = page_fill(pdf_path)
    lints: list[Lint] = []

    for index, block in enumerate(blocks):
        if block.line_count > max_bullet_lines:
            lints.append(
                Lint(
                    "bullet_too_long",
                    f"bullet {index} occupies {block.line_count} lines: {block.text[:60]!r}",
                )
            )
        if block.line_count > 1 and block.last_line_words <= 1:
            lints.append(
                Lint("widow_line", f"bullet {index} ends with one word on a line of its own")
            )

    for section in doc.sections:
        for entry in section.entries:
            count = len(entry.bullets)
            if count > max_bullets:
                lints.append(
                    Lint(
                        "too_many_bullets",
                        f"{entry.heading}: {count} bullets; at most {max_bullets} per role",
                    )
                )
            elif 0 < count < min_bullets:
                lints.append(
                    Lint(
                        "too_few_bullets",
                        f"{entry.heading}: {count} bullet(s); at least {min_bullets} per role",
                    )
                )

    if len(fills) > max_pages:
        lints.append(Lint("page_count", f"{len(fills)} pages; at most {max_pages}"))
    if len(fills) > 1:
        tail_fill, last_page = fills[-1], len(fills) - 1
        if tail_fill < ORPHAN_TAIL_FILL:
            lints.append(Lint("orphan_tail_page", f"the last page is only {tail_fill:.0%} full"))
        if tail_fill < HEADING_ONLY_FILL and not carries_bullet(blocks, last_page):
            lints.append(Lint("heading_only_page", "the last page carries no bullet content"))

    return sorted(lints, key=lambda lint: lint.priority)
