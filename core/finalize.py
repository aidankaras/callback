"""Sealing a gated document, and proving later that it has not changed.

`finalize` is the only writer of `outputs/<job>/final/`. It accepts only a
draft whose build passed (or whose warnings were accepted) and whose PDF and
YAML still match the report that judged them, copies them there read-only,
and writes a manifest: every file's SHA-256, the gates' verdicts, a hash of
the fact store they were checked against, and the time. The manifest is
signed with an HMAC key kept at `$CALLBACK_HOME/.key` (0600), so a file
changed after sealing is caught by its hash, and a manifest rewritten to
match is caught by its signature.

Each seal is also appended to `ledger.log`, where every entry carries the
hash of the one before it: an entry edited, removed or reordered breaks the
chain from that point on, and `verify_ledger` says where.

This is tamper-evidence, not tamper-proofing: anyone who can read the key
can forge a signature. It proves that nothing callback did not write has
changed a sealed document since, which is what a candidate needs to trust
that what they are about to send is what the gates judged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import cast

from core.paths import resolve_inside, write_private, write_private_bytes

KEY_FILE = ".key"
LEDGER_FILE = "ledger.log"
SEALED_FILES = ("resume.pdf", "resume.yaml", "report.json")
_GENESIS = "0" * 64


class FinalizeError(RuntimeError):
    """A document that cannot be sealed; nothing was written."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _key() -> bytes:
    path = resolve_inside(KEY_FILE)
    if not path.exists():
        write_private_bytes(path, secrets.token_bytes(32))
    return path.read_bytes()


def _sign(body: dict[str, object]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(_key(), canonical, hashlib.sha256).hexdigest()


def record_event(event: dict[str, object]) -> None:
    """Append `event` to the hash-chained ledger."""
    _append_ledger(event)


def _append_ledger(event: dict[str, object]) -> None:
    path = resolve_inside(LEDGER_FILE)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    previous = json.loads(lines[-1])["entry_sha256"] if lines else _GENESIS
    entry: dict[str, object] = {**event, "previous_sha256": previous}
    entry["entry_sha256"] = _sha256(json.dumps(entry, sort_keys=True).encode())
    if path.exists():
        path.chmod(0o600)
    write_private(path, "\n".join([*lines, json.dumps(entry, sort_keys=True)]) + "\n")
    path.chmod(0o400)


def _verdicts(report: object) -> dict[str, object]:
    """Each gate's verdict from a build report's outcomes."""
    outcomes = (
        cast("dict[str, object]", report).get("outcomes") if isinstance(report, dict) else None
    )
    verdicts: dict[str, object] = {}
    for outcome in cast("list[object]", outcomes or []):
        if isinstance(outcome, dict):
            record = cast("dict[str, object]", outcome)
            verdicts[str(record.get("gate"))] = record.get("verdict")
    return verdicts


def finalize(job_id: str, *, now: datetime, accept_warnings: bool = False) -> Path:
    """Seal the job's gated draft into `final/` and record it in the ledger."""
    draft = resolve_inside(f"outputs/{job_id}/draft")
    final = resolve_inside(f"outputs/{job_id}/final")
    if final.exists():
        raise FinalizeError(f"{job_id!r} is already sealed; a sealed document is never rewritten")
    try:
        data = {name: (draft / name).read_bytes() for name in SEALED_FILES}
        report = json.loads(data["report.json"])
    except (OSError, json.JSONDecodeError) as exc:
        raise FinalizeError(f"no gated draft for {job_id!r}: {exc}") from exc
    verdict = report.get("verdict")
    if verdict == "warn" and not accept_warnings:
        raise FinalizeError("the gates returned warn; read report.md, then accept warnings")
    if verdict not in {"pass", "warn"}:
        raise FinalizeError(f"the draft did not pass its gates (verdict {verdict!r})")
    if _sha256(data["resume.pdf"]) != report.get("pdf_sha256") or _sha256(
        data["resume.yaml"]
    ) != report.get("doc_sha256"):
        raise FinalizeError("the draft has changed since it was gated: run `callback build` again")
    facts = resolve_inside("facts.yaml")
    body: dict[str, object] = {
        "job_id": job_id,
        "sealed_at": now.isoformat(),
        "files": {name: _sha256(b) for name, b in data.items()},
        "facts_sha256": _sha256(facts.read_bytes()) if facts.exists() else None,
        "verdicts": _verdicts(report),
    }
    manifest = {**body, "signature": _sign(body)}

    final.mkdir(mode=0o700)
    for name, content in data.items():
        write_private_bytes(final / name, content)
    write_private(final / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    for item in final.iterdir():
        item.chmod(0o444)
    _append_ledger(
        {
            "event": "finalize",
            "job_id": job_id,
            "at": now.isoformat(),
            "manifest": manifest["signature"],
        }
    )
    return final


def verify(job_id: str) -> list[str]:
    """Every way the sealed document for `job_id` differs from what was sealed."""
    final = resolve_inside(f"outputs/{job_id}/final")
    try:
        manifest = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"no readable manifest: {exc}"]
    signature = manifest.pop("signature", "")
    if not hmac.compare_digest(str(signature), _sign(manifest)):
        return ["the manifest's signature does not match; it was altered"]
    problems: list[str] = []
    for name, expected in dict(manifest.get("files", {})).items():
        path = final / str(name)
        if not path.is_file() or _sha256(path.read_bytes()) != expected:
            problems.append(f"{name} does not match its sealed hash")
    return problems


def verify_ledger() -> list[str]:
    """Where, if anywhere, the ledger's hash chain breaks."""
    path = resolve_inside(LEDGER_FILE)
    if not path.exists():
        return []
    previous = _GENESIS
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            return [f"entry {n} is not readable"]
        claimed = entry.pop("entry_sha256", None)
        if entry.get("previous_sha256") != previous:
            return [
                f"entry {n} does not follow the entry before it: an entry was removed or reordered"
            ]
        if _sha256(json.dumps(entry, sort_keys=True).encode()) != claimed:
            return [f"entry {n} was altered after it was written"]
        previous = str(claimed)
    return []
