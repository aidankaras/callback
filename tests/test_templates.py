from __future__ import annotations

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


def _draft(home: Path, job: str, verdict: str = "pass") -> Path:
    draft = home / "outputs" / job / "draft"
    draft.mkdir(parents=True)
    (draft / "resume.yaml").write_text(DOC, encoding="utf-8")
    (draft / "report.json").write_text(json.dumps({"verdict": verdict}), encoding="utf-8")
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
