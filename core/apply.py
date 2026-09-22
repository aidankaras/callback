"""Hand an application to the candidate, and keep the record of it.

callback never submits an application. Every major platform either requires
the employer's own credentials to post one or forbids automation outright, so
the supported path is a *packet*: the gated resume, the gated cover letter,
the resume as plain text for forms that want it pasted, and a field map for
the form's contact questions. The candidate submits it themselves.

`apply` refuses to build a packet from a draft that did not pass its gates, or
whose PDF has changed since it was gated. It also enforces three volume rules,
in code rather than advice: no second application for the same job, none to
the same employer, title and location within 90 days, and at most 10 in any
24 hours. Each application is recorded with the SHA-256 of exactly what was
sent, and outcomes (an interview, a rejection) are recorded against it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, cast

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from core.export.text import to_text
from core.paths import private_dir, resolve_inside, write_private, write_private_bytes
from core.resume.io import DocumentError, load_doc

APPLICATIONS_FILE = "applications.yaml"
MAX_PER_DAY = 10
REAPPLY_WINDOW = timedelta(days=90)

Source = Literal[
    "greenhouse",
    "lever",
    "ashby",
    "workday",
    "linkedin",
    "handshake",
    "indeed",
    "company-site",
    "email",
    "referral",
    "other",
]
OutcomeKind = Literal["viewed", "callback", "interview", "offer", "rejected", "ghosted", "withdrew"]


class ApplyError(RuntimeError):
    """An application that cannot go ahead; nothing was written."""


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must carry a timezone")
    return value


class Target(BaseModel):
    """The posting being applied to, as the candidate describes it."""

    model_config = {"extra": "forbid"}

    job_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    company: str = Field(min_length=1)
    title: str = Field(min_length=1)
    location: str = ""
    url: str = ""
    source: Source

    def key(self) -> tuple[str, str, str]:
        """What makes two postings the same requisition, for duplicate detection."""

        def norm(value: str) -> str:
            return " ".join(value.casefold().split())

        return norm(self.company), norm(self.title), norm(self.location)


class Outcome(BaseModel):
    model_config = {"extra": "forbid"}

    kind: OutcomeKind
    occurred_at: datetime

    _aware = field_validator("occurred_at")(_aware)


class Application(Target):
    """One application: the target, when, and the hashes of what was sent."""

    submitted_at: datetime
    resume_sha256: str
    letter_sha256: str | None = None
    outcomes: list[Outcome] = Field(default_factory=list[Outcome])

    _aware = field_validator("submitted_at")(_aware)


def load_applications() -> list[Application]:
    path = resolve_inside(APPLICATIONS_FILE)
    if not path.exists():
        return []
    try:
        parsed: object = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        if not isinstance(parsed, list):
            raise ValueError(f"expected a list, found {type(parsed).__name__}")
        # The boundary where this starts trusting the file's shape;
        # `model_validate` is what checks each entry.
        return [Application.model_validate(item) for item in cast("list[object]", parsed)]
    except (yaml.YAMLError, ValueError, ValidationError) as exc:
        raise ApplyError(f"{path} is not a readable application record: {exc}") from exc


def _save(applications: list[Application]) -> None:
    body = yaml.safe_dump([a.model_dump(mode="json") for a in applications], sort_keys=False)
    write_private(resolve_inside(APPLICATIONS_FILE), body)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_record(path: Path, what: str) -> dict[str, object]:
    """A gate report's JSON record, refusing one that is missing or unreadable."""
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplyError(f"cannot read the {what}'s gate record {path.name}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ApplyError(f"the {what}'s gate record {path.name} is not a JSON object")
    return cast("dict[str, object]", parsed)


def _check_draft(draft: Path, *, accept_warnings: bool) -> tuple[Path, Path | None]:
    """The resume and letter PDFs to send, once their reports say they may be."""
    report_path = draft / "report.json"
    resume = draft / "resume.pdf"
    if not report_path.is_file() or not resume.is_file():
        raise ApplyError(f"no built resume in {draft}: run `callback build` first")
    report = _read_record(report_path, "resume")
    verdict = report.get("verdict")
    if verdict == "warn" and not accept_warnings:
        raise ApplyError("the resume's gates returned warn; read report.md, then accept warnings")
    if verdict not in {"pass", "warn"}:
        raise ApplyError(f"the resume did not pass its gates (verdict {verdict!r})")
    if _sha256(resume) != report.get("pdf_sha256"):
        raise ApplyError("resume.pdf has changed since it was gated: run `callback build` again")

    letter = draft / "cover-letter.pdf"
    if not letter.is_file():
        return resume, None
    letter_record = _read_record(draft / "letter-report.json", "cover letter")
    if letter_record.get("verdict") != "pass":
        raise ApplyError("the cover letter did not pass its checks: run `callback letter` again")
    if _sha256(letter) != letter_record.get("pdf_sha256"):
        raise ApplyError(
            "cover-letter.pdf has changed since it was checked: run `callback letter` again"
        )
    return resume, letter


def _check_volume(target: Target, existing: list[Application], now: datetime) -> None:
    for prior in existing:
        if prior.job_id == target.job_id:
            raise ApplyError(
                f"already applied to {target.job_id!r} on {prior.submitted_at:%Y-%m-%d}"
            )
        if prior.key() == target.key() and now - prior.submitted_at < REAPPLY_WINDOW:
            raise ApplyError(
                f"applied to {prior.company} / {prior.title} on {prior.submitted_at:%Y-%m-%d} "
                f"as {prior.job_id!r}; the same posting may not be re-applied to within 90 days"
            )
    recent = [a for a in existing if now - a.submitted_at < timedelta(days=1)]
    if len(recent) >= MAX_PER_DAY:
        raise ApplyError(
            f"{len(recent)} applications in the last 24 hours; the limit is {MAX_PER_DAY}"
        )


def apply(target: Target, *, now: datetime, accept_warnings: bool = False) -> Path:
    """Build the application packet for `target` and record the application.

    Returns the packet directory. Raises `ApplyError`, having written
    nothing, when the draft is not ready or a volume rule refuses it.
    """
    _aware(now)
    draft = resolve_inside(f"outputs/{target.job_id}/draft")
    resume, letter = _check_draft(draft, accept_warnings=accept_warnings)
    existing = load_applications()
    _check_volume(target, existing, now)
    try:
        doc = load_doc(draft / "resume.yaml")
    except (DocumentError, OSError) as exc:
        raise ApplyError(f"cannot read the job's resume document: {exc}") from exc

    packet = private_dir(f"outputs/{target.job_id}/packet")
    for stale in packet.iterdir():
        stale.unlink()
    write_private_bytes(packet / "resume.pdf", resume.read_bytes())
    if letter is not None:
        write_private_bytes(packet / "cover-letter.pdf", letter.read_bytes())
    write_private(packet / "resume.txt", to_text(doc))
    identity = doc.identity
    fields = {
        "name": identity.name,
        "email": identity.email,
        "phone": identity.phone,
        "links": identity.links,
    }
    write_private(packet / "fields.json", json.dumps(fields, indent=2) + "\n")

    record = Application(
        **target.model_dump(),
        submitted_at=now,
        resume_sha256=_sha256(resume),
        letter_sha256=_sha256(letter) if letter is not None else None,
    )
    _save([*existing, record])
    return packet


def record_outcome(job_id: str, kind: OutcomeKind, *, now: datetime) -> None:
    """Record what happened to the application for `job_id`."""
    applications = load_applications()
    for application in applications:
        if application.job_id == job_id:
            application.outcomes.append(Outcome(kind=kind, occurred_at=now))
            _save(applications)
            return
    raise ApplyError(f"no application recorded for {job_id!r}")
