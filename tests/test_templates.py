from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest

from core.paths import ensure_home
from core.templates import TemplateError, listing, save, use

DOC = "identity: {name: Dana Vex, email: d@example.com}\nsections: []\n"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    return ensure_home()


def _draft(home: Path, job: str, verdict: str = "pass", doc: str = DOC) -> Path:
    """A draft as `callback build` leaves it: the document, and a report whose
    hash names the bytes it judged."""
    draft = home / "outputs" / job / "draft"
    draft.mkdir(parents=True)
    (draft / "resume.yaml").write_text(doc, encoding="utf-8")
    (draft / "resume.pdf").write_bytes(b"%PDF gated")
    report = {"verdict": verdict, "doc_sha256": hashlib.sha256(doc.encode()).hexdigest()}
    (draft / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return draft


def test_a_gated_draft_is_saved_and_reused_as_a_draft(home: Path) -> None:
    saved = save("acme-ml", "ml-intern") if _draft(home, "acme-ml") else None
    assert saved is not None
    assert [t.name for t in listing()] == ["ml-intern"]
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600

    target = use("ml-intern", "globex-ml")
    assert target == home / "outputs" / "globex-ml" / "draft" / "resume.yaml"
    assert target.read_text(encoding="utf-8") == DOC
    # It is a draft, not a result: nothing claims it passed anything.
    assert not (target.parent / "report.json").exists()


@pytest.mark.parametrize("verdict", ["fail", "unknown"])
def test_a_draft_that_did_not_pass_is_not_a_shape_to_reuse(home: Path, verdict: str) -> None:
    """Mutation: save any draft -- a failing document becomes the start of the
    next five applications."""
    _draft(home, "acme-ml", verdict=verdict)
    with pytest.raises(TemplateError, match="did not pass"):
        save("acme-ml", "ml-intern")
    assert listing() == []


def test_an_existing_draft_is_not_replaced_without_asking(home: Path) -> None:
    _draft(home, "acme-ml")
    save("acme-ml", "ml-intern")
    other = _draft(home, "globex-ml")
    (other / "resume.yaml").write_text("identity: {name: Someone Else}\n", encoding="utf-8")
    with pytest.raises(TemplateError, match="already has a draft"):
        use("ml-intern", "globex-ml")
    assert "Someone Else" in (other / "resume.yaml").read_text(encoding="utf-8")
    use("ml-intern", "globex-ml", overwrite=True)
    assert (other / "resume.yaml").read_text(encoding="utf-8") == DOC


@pytest.mark.parametrize("name", ["../escape", "Name", "x" * 65, ""])
def test_a_template_name_that_is_not_one_is_refused(home: Path, name: str) -> None:
    _draft(home, "acme-ml")
    with pytest.raises(TemplateError, match="must be lowercase"):
        save("acme-ml", name)


def test_an_unknown_template_says_how_to_list_them(home: Path) -> None:
    with pytest.raises(TemplateError, match="callback template list"):
        use("missing", "acme-ml")


def test_a_document_edited_since_its_build_is_not_a_template(home: Path) -> None:
    """The verdict describes the bytes the build judged. Mutation: trust the
    verdict without comparing the hash -- an ungated document is saved."""
    draft = _draft(home, "acme-ml")
    (draft / "resume.yaml").write_text(DOC + "sections: []\n", encoding="utf-8")
    with pytest.raises(TemplateError, match="changed since it was gated"):
        save("acme-ml", "ml-intern")
    assert listing() == []


def test_using_a_template_clears_the_previous_build(home: Path) -> None:
    """A report describes one document; left beside another it is a judgement
    of something else, and `apply` reads it. Mutation: keep the artifacts --
    the old report.json and resume.pdf survive beside the new document."""
    _draft(home, "acme-ml")
    save("acme-ml", "ml-intern")
    target = _draft(home, "globex-ml", doc="identity: {name: Someone Else}\n")
    (target / "report.md").write_text("# PASS", encoding="utf-8")
    use("ml-intern", "globex-ml", overwrite=True)
    assert sorted(p.name for p in target.iterdir()) == ["origin.json", "resume.yaml"]


def test_using_a_template_clears_the_letter_and_answers_records_too(home: Path) -> None:
    """The letter and the form answers are checked against resume.yaml as well,
    and `apply` trusts their verdicts. Mutation: clear only the build's own
    artifacts -- a letter checked against the previous document survives."""
    from core.draft import GATE_RECORDS

    _draft(home, "acme-ml")
    save("acme-ml", "ml-intern")
    target = _draft(home, "globex-ml", doc="identity: {name: Someone Else}\n")
    for record in GATE_RECORDS:
        (target / record).write_bytes(b"old")
    assert len(list(target.iterdir())) > 3  # premise: the draft holds every record
    use("ml-intern", "globex-ml", overwrite=True)
    assert sorted(p.name for p in target.iterdir()) == ["origin.json", "resume.yaml"]


def test_a_report_without_a_document_hash_says_so(home: Path) -> None:
    """An older report is not an edited document, and telling the candidate to
    look for an edit that never happened wastes their time. Mutation: treat a
    missing hash as a mismatch -- the message says "has changed"."""
    draft = _draft(home, "acme-ml")
    (draft / "report.json").write_text(json.dumps({"verdict": "pass"}), encoding="utf-8")
    with pytest.raises(TemplateError, match="records no document hash"):
        save("acme-ml", "ml-intern")


def test_a_report_that_is_not_an_object_is_refused(home: Path) -> None:
    draft = _draft(home, "acme-ml")
    (draft / "report.json").write_text("null", encoding="utf-8")
    with pytest.raises(TemplateError, match="not readable"):
        save("acme-ml", "ml-intern")
