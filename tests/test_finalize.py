from __future__ import annotations

import hashlib
import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.finalize import FinalizeError, finalize, verify, verify_ledger
from core.paths import ensure_home

NOW = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
RESUME = "identity: {name: Dana Vex, email: d@example.com}\nsections: []\n"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    home = ensure_home()
    (home / "facts.yaml").write_text("{}\n")
    return home


def _built(home: Path, job: str, verdict: str = "pass") -> Path:
    draft = home / "outputs" / job / "draft"
    draft.mkdir(parents=True)
    pdf = b"%PDF-1.7 gated " + job.encode()
    (draft / "resume.pdf").write_bytes(pdf)
    (draft / "resume.yaml").write_text(RESUME, encoding="utf-8")
    report: dict[str, object] = {
        "verdict": verdict,
        "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        "doc_sha256": hashlib.sha256(RESUME.encode()).hexdigest(),
        "outcomes": [{"gate": "grounding", "verdict": verdict, "findings": []}],
    }
    (draft / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return draft


def test_a_passing_draft_is_sealed_read_only_with_a_signed_manifest(home: Path) -> None:
    _built(home, "acme")
    final = finalize("acme", now=NOW)
    assert sorted(p.name for p in final.iterdir()) == [
        "manifest.json",
        "report.json",
        "resume.pdf",
        "resume.yaml",
    ]
    for p in final.iterdir():
        assert stat.S_IMODE(p.stat().st_mode) == 0o444, p.name
    manifest = json.loads((final / "manifest.json").read_text())
    assert (
        manifest["files"]["resume.pdf"]
        == hashlib.sha256((final / "resume.pdf").read_bytes()).hexdigest()
    )
    assert manifest["facts_sha256"] == hashlib.sha256(b"{}\n").hexdigest()
    assert stat.S_IMODE((home / ".key").stat().st_mode) == 0o600
    assert verify("acme") == []


def test_a_draft_that_did_not_pass_is_not_sealed(home: Path) -> None:
    _built(home, "acme", verdict="fail")
    with pytest.raises(FinalizeError, match="did not pass"):
        finalize("acme", now=NOW)
    assert not (home / "outputs" / "acme" / "final").exists()


def test_a_sealed_document_is_never_resealed(home: Path) -> None:
    _built(home, "acme")
    finalize("acme", now=NOW)
    with pytest.raises(FinalizeError, match="already sealed"):
        finalize("acme", now=NOW)


def test_tampering_with_a_sealed_file_is_detected(home: Path) -> None:
    """Mutation: skip the file hashes in verify -- this test fails."""
    _built(home, "acme")
    final = finalize("acme", now=NOW)
    pdf = final / "resume.pdf"
    pdf.chmod(0o644)
    pdf.write_bytes(b"%PDF edited")
    assert verify("acme") == ["resume.pdf does not match its sealed hash"]


def test_a_rewritten_manifest_fails_its_signature(home: Path) -> None:
    """Re-hashing a tampered file into the manifest is caught by the HMAC.
    Mutation: skip the signature check -- this test fails."""
    _built(home, "acme")
    final = finalize("acme", now=NOW)
    for name in ("resume.pdf", "manifest.json"):
        (final / name).chmod(0o644)
    (final / "resume.pdf").write_bytes(b"%PDF edited")
    manifest = json.loads((final / "manifest.json").read_text())
    manifest["files"]["resume.pdf"] = hashlib.sha256(b"%PDF edited").hexdigest()
    (final / "manifest.json").write_text(json.dumps(manifest))
    assert verify("acme") == ["the manifest's signature does not match; it was altered"]


def test_the_ledger_chains_every_seal_and_detects_a_rewritten_entry(home: Path) -> None:
    for job in ("a", "b", "c"):
        _built(home, job)
        finalize(job, now=NOW)
    assert verify_ledger() == []
    ledger = home / "ledger.log"
    lines = ledger.read_text().splitlines()
    assert len(lines) == 3
    entry = json.loads(lines[1])
    entry["job_id"] = "forged"
    lines[1] = json.dumps(entry, sort_keys=True)
    ledger.chmod(0o644)
    ledger.write_text("\n".join(lines) + "\n")
    problems = verify_ledger()
    assert problems and "entry 1" in problems[0], problems


def test_deleting_a_ledger_entry_breaks_the_chain(home: Path) -> None:
    """Mutation: chain each entry to nothing -- a removed middle entry passes."""
    for job in ("a", "b", "c"):
        _built(home, job)
        finalize(job, now=NOW)
    ledger = home / "ledger.log"
    lines = ledger.read_text().splitlines()
    ledger.chmod(0o644)
    ledger.write_text(lines[0] + "\n" + lines[2] + "\n")
    assert verify_ledger() != []
