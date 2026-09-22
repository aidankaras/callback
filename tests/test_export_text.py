from __future__ import annotations

from pathlib import Path

import pytest

from core.export.text import check_text_export, to_text
from core.profile.models import Identity
from core.resume.models import Bullet, Entry, ResumeDoc, Section

LONG = (
    "Owned the migration of the nightly reporting pipeline to a partitioned columnar store, "
    "coordinating the cutover across three separate teams and their release calendars"
)
DOC = ResumeDoc(
    identity=Identity(
        name="Dana Vex", email="dana@example.com", phone="+1 555 0100", links=["github.com/dv"]
    ),
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
                        Bullet(text="Cut the nightly queue to 300 items", sources=["a"]),
                        Bullet(text=LONG, sources=["a"]),
                    ],
                )
            ],
        ),
        Section(title="Skills", skills=["Python", "SQL", "scikit-learn"]),
    ],
)


def test_every_critical_field_appears() -> None:
    text = to_text(DOC)
    for value in [
        "Dana Vex",
        "dana@example.com",
        "+1 555 0100",
        "github.com/dv",
        "EXPERIENCE",
        "Northwind Analytics",
        "Data Engineer",
        "Boston, MA",
        "Jun 2024",
        "Aug 2025",
        "Python, SQL, scikit-learn",
    ]:
        assert value in text, value
    assert check_text_export(DOC, text) == ("pass", [])


@pytest.mark.parametrize("width", [40, 78])
def test_lines_respect_the_width_and_wrapped_bullets_hang(width: int) -> None:
    lines = to_text(DOC, width=width).splitlines()
    assert all(len(line) <= width for line in lines), [x for x in lines if len(x) > width]
    start = next(i for i, line in enumerate(lines) if line.startswith("- Owned"))
    assert lines[start + 1].startswith("  ") and not lines[start + 1].startswith("  -")


def test_a_bullet_survives_wrapping_word_for_word() -> None:
    joined = " ".join(to_text(DOC, width=40).split())
    assert LONG in joined


def test_a_missing_field_is_named() -> None:
    """Mutation: skip the subheading in to_text -- this test fails."""
    text = to_text(DOC).replace("Data Engineer", "")
    verdict, missing = check_text_export(DOC, text)
    assert verdict == "fail"
    assert missing == ["title[0]: 'Data Engineer'"]


def test_an_empty_export_fails() -> None:
    assert check_text_export(DOC, "")[0] == "fail"


def test_wrapping_never_splits_a_hyphenated_token() -> None:
    """Mutation: let textwrap break on hyphens -- some width in the sweep fails."""
    hyphenated = (
        "Ran state-of-the-art multi-tenant data-quality checks re-run nightly by "
        "cross-team on-call rotations for real-time pay-per-use customers"
    )
    doc = DOC.model_copy(
        update={
            "sections": [
                Section(
                    title="Experience",
                    entries=[
                        Entry(heading="Northwind", bullets=[Bullet(text=hyphenated, sources=["a"])])
                    ],
                )
            ]
        }
    )
    assert hyphenated.count("-") > 10
    for width in range(20, 61):
        lines = to_text(doc, width=width).splitlines()
        assert not any(line.endswith("-") for line in lines), (width, lines)


def test_the_cli_prints_the_text_and_exits_on_its_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from core.cli import main
    from core.resume.io import dump_doc

    path = tmp_path / "resume.yaml"
    path.write_text(dump_doc(DOC), encoding="utf-8")
    assert main(["export", str(path)]) == 0
    assert capsys.readouterr().out == to_text(DOC)
    assert main(["export", str(tmp_path / "missing.yaml")]) == 3


def test_the_cli_exits_2_and_names_a_field_the_export_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The export includes every field by construction, so a lost field means
    a regression in `to_text`; simulate one. Mutation: always exit 0 -- fails."""
    import core.export.text
    from core.cli import main
    from core.resume.io import dump_doc

    real = core.export.text.to_text

    def lossy(doc: ResumeDoc, *, width: int = 78) -> str:
        return real(doc, width=width).replace("Data Engineer", "")

    monkeypatch.setattr(core.export.text, "to_text", lossy)
    path = tmp_path / "resume.yaml"
    path.write_text(dump_doc(DOC), encoding="utf-8")
    assert main(["export", str(path)]) == 2
    assert "title[0]: 'Data Engineer'" in capsys.readouterr().err
