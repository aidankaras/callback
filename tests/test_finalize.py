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
        "facts_sha256": hashlib.sha256((home / "facts.yaml").read_bytes()).hexdigest(),
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


# --- the ledger must be anchored, not merely self-consistent ----------------------------


@pytest.mark.parametrize("damage", ["delete", "empty", "truncate", "replace", "garbage"])
def test_a_damaged_ledger_is_reported_while_sealed_documents_exist(home: Path, damage: str) -> None:
    """Mutations: drop the per-entry HMAC, or treat a missing ledger as intact
    -- the replace and delete cases pass."""
    for job in ("a1", "a2", "a3"):
        _built(home, job)
        finalize(job, now=NOW)
    ledger = home / "ledger.log"
    lines = ledger.read_text().splitlines()
    ledger.chmod(0o644)
    if damage == "delete":
        ledger.unlink()
    elif damage == "empty":
        ledger.write_text("")
    elif damage == "truncate":
        ledger.write_text(lines[0] + "\n")
    elif damage == "garbage":
        ledger.write_text("5\n")
    else:
        # A fresh chain written without the key: every entry is self-consistent.
        import hashlib as h

        previous = "0" * 64
        out: list[str] = []
        # It names the real sealed jobs, so only the keyed MAC can tell it apart.
        for job in ("a1", "a2", "a3"):
            entry: dict[str, object] = {
                "event": "finalize",
                "job_id": job,
                "previous_sha256": previous,
            }
            entry["entry_sha256"] = h.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()
            previous = str(entry["entry_sha256"])
            out.append(json.dumps(entry, sort_keys=True))
        ledger.write_text("\n".join(out) + "\n")
    expected = {
        "delete": "missing or empty",
        "empty": "missing or empty",
        "truncate": "has no ledger entry",
        "garbage": "not readable",
        "replace": "not written with this data home's key",
    }[damage]
    problems = verify_ledger()
    assert problems and expected in problems[0], (damage, problems)


def test_verify_requires_the_seal_to_be_in_the_ledger(home: Path) -> None:
    _built(home, "a1")
    finalize("a1", now=NOW)
    (home / "ledger.log").chmod(0o644)
    (home / "ledger.log").unlink()
    assert verify("a1") != []


def test_a_file_added_to_a_sealed_document_is_reported(home: Path) -> None:
    _built(home, "a1")
    final = finalize("a1", now=NOW)
    final.chmod(0o755)
    (final / "cover-letter.pdf").write_bytes(b"%PDF slipped in")
    assert verify("a1") == ["cover-letter.pdf is in final/ but was not sealed"]


def test_verify_never_creates_a_key(home: Path) -> None:
    """Mutation: let verify generate the key -- the old one is lost and every
    seal reads as altered."""
    _built(home, "a1")
    finalize("a1", now=NOW)
    (home / ".key").unlink()
    assert verify("a1") == ["the signing key is missing; nothing can be verified"]
    assert not (home / ".key").exists()


def test_the_seal_records_the_facts_the_gates_judged_not_the_facts_now(home: Path) -> None:
    draft = _built(home, "a1")
    report = json.loads((draft / "report.json").read_text())
    report["facts_sha256"] = hashlib.sha256(b"{}\n").hexdigest()
    (draft / "report.json").write_text(json.dumps(report))
    (home / "facts.yaml").write_text("changed: {}\n")
    with pytest.raises(FinalizeError, match="facts have changed"):
        finalize("a1", now=NOW)
