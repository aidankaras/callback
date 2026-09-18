"""Determinism guarantees for `core.render.typst`.

Every layout gate planned after this one measures the PDF bytes this module
produces, so these tests exist to prove — not merely assume — that the same
`ResumeDoc` always renders to the same bytes, that a substituted font is a
hard failure rather than a silent fallback, and that no line ends in a
hyphen (a justified, hyphenated break can silently zero a keyword match a
later gate depends on).
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber
import pytest

from core.profile.models import Identity
from core.render.typst import RenderError, render
from core.resume.models import Bullet, Entry, ResumeDoc, Section

IDENTITY = Identity(
    name="Dana Vex", email="dana@example.com", phone="+1 555 0100", links=["github.com/danavex"]
)

DOC = ResumeDoc(
    identity=IDENTITY,
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
                        Bullet(text="Cut the nightly review queue to 300 items", sources=["a"]),
                        Bullet(text="Processed 1.2M records per run", sources=["b"]),
                    ],
                ),
            ],
        ),
        Section(
            title="Education",
            entries=[
                Entry(
                    heading="Boston University",
                    subheading="BA Data Science",
                    location="Boston, MA",
                    start="2023",
                    end="2027",
                    bullets=[],
                ),
            ],
        ),
    ],
)


@pytest.mark.slow
def test_render_produces_a_pdf(tmp_path: Path) -> None:
    result = render(DOC, tmp_path / "out.pdf")
    assert result.pdf_bytes.startswith(b"%PDF-")
    assert (tmp_path / "out.pdf").exists()


@pytest.mark.slow
def test_render_is_byte_reproducible(tmp_path: Path) -> None:
    first = render(DOC, tmp_path / "a.pdf").pdf_bytes
    second = render(DOC, tmp_path / "b.pdf").pdf_bytes
    assert first == second, "renders differ; check timestamp=0 and font pinning"


@pytest.mark.slow
def test_only_vendored_fonts_are_used(tmp_path: Path) -> None:
    result = render(DOC, tmp_path / "c.pdf")
    assert result.font_families, "no font families reported"
    assert all("Libertinus" in family for family in result.font_families), result.font_families


@pytest.mark.slow
def test_unknown_font_is_a_hard_error(tmp_path: Path) -> None:
    with pytest.raises(RenderError, match="font"):
        render(DOC, tmp_path / "d.pdf", font_family="Nonexistent Sans")


@pytest.mark.slow
def test_no_line_ends_in_a_hyphen(tmp_path: Path) -> None:
    render(DOC, tmp_path / "e.pdf")
    with pdfplumber.open(tmp_path / "e.pdf") as pdf:
        for line in (pdf.pages[0].extract_text() or "").splitlines():
            assert not line.rstrip().endswith("-"), f"hyphenated break in {line!r}"
