from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest

from core.buildcmd import BuildError, build
from core.paths import ensure_home
from core.profile.models import Dates, Fact, FactKind, Identity, Provenance, Quantity, Trust
from core.profile.store import save_facts
from core.resume.io import dump_doc
from core.resume.models import Bullet, Entry, ResumeDoc, Section

CONFIRMED = Provenance(source="onboarding_answer", trust=Trust.USER_CONFIRMED)
FACTS = {
    "role": Fact(
        id="role",
        kind=FactKind.EXPERIENCE,
        text="Data Engineer at Northwind Analytics in Boston, MA",
        organization="Northwind Analytics",
        title="Data Engineer",
        location="Boston, MA",
        entities=["Northwind Analytics"],
        dates=Dates(start="2024-06", end="2025-08"),
        provenance=CONFIRMED,
    ),
    "queue": Fact(
        id="queue",
        kind=FactKind.EXPERIENCE_DETAIL,
        parent="role",
        text="Cut the review queue to 300 items using Python",
        numbers={"queue": Quantity(value=300)},
        entities=["Python"],
        provenance=CONFIRMED,
    ),
    **{
        fid: Fact(
            id=fid,
            kind=FactKind.EXPERIENCE_DETAIL,
            parent="role",
            text=text,
            provenance=CONFIRMED,
        )
        for fid, text in (("loader", "Shipped the loader"), ("runbook", "Wrote the runbook"))
    },
    "contact": Fact(
        id="contact",
        kind=FactKind.LINKS,
        text="Dana Vex, d@example.com",
        entities=["Dana Vex", "d@example.com"],
        provenance=CONFIRMED,
    ),
}


def _doc(bullet: str = "Cut the review queue to 300 items using Python") -> ResumeDoc:
    return ResumeDoc(
        identity=Identity(name="Dana Vex", email="d@example.com"),
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
                        sources=["role"],
                        bullets=[
                            Bullet(text=bullet, sources=["queue"]),
                            Bullet(text="Shipped the loader", sources=["loader"]),
                            Bullet(text="Wrote the runbook", sources=["runbook"]),
                        ],
                    )
                ],
            ),
            Section(title="Skills", skills=["Python"]),
        ],
    )


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    home = ensure_home()
    save_facts(FACTS)
    return home


def _write(tmp_path: Path, doc: ResumeDoc) -> Path:
    path = tmp_path / "resume.yaml"
    path.write_text(dump_doc(doc), encoding="utf-8")
    return path


@pytest.mark.slow
def test_a_clean_build_passes_and_writes_private_artifacts(tmp_path: Path, home: Path) -> None:
    result = build(_write(tmp_path, _doc()), "northwind-de")
    assert result.report.verdict == "pass"
    assert result.exit_code == 0
    draft = home / "outputs" / "northwind-de" / "draft"
    assert result.out_dir == draft.resolve()
    for name in ("resume.pdf", "report.md", "report.json"):
        assert stat.S_IMODE((draft / name).stat().st_mode) == 0o600, name
    for directory in (draft, draft.parent):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700, directory
    assert not (home / "outputs" / "northwind-de" / "final").exists()


@pytest.mark.slow
def test_the_report_names_the_exact_pdf_and_document_it_judged(tmp_path: Path) -> None:
    source = _write(tmp_path, _doc())
    result = build(source, "northwind-de")
    record = json.loads((result.out_dir / "report.json").read_text())
    pdf = (result.out_dir / "resume.pdf").read_bytes()
    assert record["pdf_sha256"] == hashlib.sha256(pdf).hexdigest()
    assert record["doc_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert [o["gate"] for o in record["outcomes"]] == [
        "grounding",
        "claims",
        "roundtrip",
        "hidden",
        "skills",
        "lints",
    ]


@pytest.mark.slow
def test_a_failing_build_exits_2_and_leads_with_what_to_fix(tmp_path: Path) -> None:
    result = build(_write(tmp_path, _doc("Cut the review queue to 999 items")), "northwind-de")
    assert result.report.verdict == "fail"
    assert result.exit_code == 2
    report = (result.out_dir / "report.md").read_text()
    first_lines = report.splitlines()[:8]
    assert first_lines[0] == "# FAIL"
    assert any("999" in line for line in first_lines), first_lines


@pytest.mark.parametrize("job", ["", "../escape", "Upper", "a/b", "-lead", "x" * 65])
def test_an_unsafe_job_id_is_refused_before_anything_is_written(
    tmp_path: Path, home: Path, job: str
) -> None:
    with pytest.raises(BuildError, match="job id"):
        build(_write(tmp_path, _doc()), job)
    assert list((home / "outputs").iterdir()) == []


@pytest.mark.slow
def test_the_cli_exit_code_is_the_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from core.cli import main

    assert main(["build", str(_write(tmp_path, _doc())), "--job", "ok"]) == 0
    bad = tmp_path / "bad.yaml"
    bad.write_text(dump_doc(_doc("Cut the review queue to 999 items")), encoding="utf-8")
    assert main(["build", str(bad), "--job", "bad"]) == 2
    assert main(["build", str(tmp_path / "missing.yaml"), "--job", "x"]) == 3
    assert "PASS" in capsys.readouterr().out


@pytest.mark.slow
def test_a_planted_symlink_at_the_pdf_path_is_replaced_not_followed(
    tmp_path: Path, home: Path
) -> None:
    victim = tmp_path / "outside.txt"
    victim.write_text("SECRET", encoding="utf-8")
    victim.chmod(0o644)
    draft = home / "outputs" / "sym" / "draft"
    draft.mkdir(parents=True)
    (draft / "resume.pdf").symlink_to(victim)

    build(_write(tmp_path, _doc()), "sym")
    assert victim.read_text(encoding="utf-8") == "SECRET"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644
    assert not (draft / "resume.pdf").is_symlink()


@pytest.mark.slow
def test_a_render_failure_leaves_no_stale_report_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.render.typst import RenderError

    source = _write(tmp_path, _doc())
    first = build(source, "stale")
    assert (first.out_dir / "report.md").read_text().startswith("# PASS")

    def broken(*_args: object, **_kwargs: object) -> None:
        raise RenderError("requested font is not embedded")

    monkeypatch.setattr("core.buildcmd.render", broken)
    with pytest.raises(BuildError, match="not embedded"):
        build(source, "stale")
    assert sorted(p.name for p in first.out_dir.iterdir()) == []


def test_a_job_id_with_a_trailing_newline_is_refused(tmp_path: Path, home: Path) -> None:
    with pytest.raises(BuildError, match="job id"):
        build(_write(tmp_path, _doc()), "nl\n")
    assert list((home / "outputs").iterdir()) == []
