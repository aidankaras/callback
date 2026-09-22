from __future__ import annotations

from pathlib import Path

import pytest

from core.profile.models import Identity
from core.resume.io import DocumentError, dump_doc, load_doc
from core.resume.models import Bullet, Entry, ResumeDoc, Section

DOC = ResumeDoc(
    identity=Identity(name="Dana Vex", email="d@example.com", links=["github.com/dv"]),
    sections=[
        Section(
            title="Experience",
            entries=[
                Entry(
                    heading="Northwind",
                    start="Jun 2024",
                    sources=["role"],
                    bullets=[Bullet(text="Cut the queue to 300", sources=["q"])],
                )
            ],
        ),
        Section(title="Skills", skills=["Python", "C++"]),
    ],
)


def test_a_document_survives_a_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "resume.yaml"
    path.write_text(dump_doc(DOC), encoding="utf-8")
    assert load_doc(path) == DOC


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", "must contain a mapping"),
        ("- a\n- b\n", "must contain a mapping"),
        ("identity: [\n", "not valid YAML"),
        ("identity: {name: x, email: y}\nsurprise: 1\n", "surprise"),
    ],
)
def test_a_malformed_document_names_its_problem(tmp_path: Path, text: str, expected: str) -> None:
    path = tmp_path / "resume.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(DocumentError, match=expected):
        load_doc(path)
