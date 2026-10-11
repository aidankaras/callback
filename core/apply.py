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
import re
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, cast

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from core.draft import ORIGIN
from core.export.text import to_text
from core.finalize import record_event
from core.paths import private_dir, resolve_inside, write_private, write_private_bytes
from core.profile.store import facts_sha256
from core.resume.io import DocumentError, parse_doc
from core.resume.models import ResumeDoc

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


_LEGAL_SUFFIXES = {"inc", "llc", "ltd", "corp", "corporation", "co", "plc", "gmbh"}


def _norm_name(value: str) -> str:
    without_parens = re.sub(r"\([^)]*\)", " ", value.casefold())
    words = re.sub(r"[^\w]+", " ", without_parens).split()
    while words and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def _norm_url(url: str) -> str:
    return url.strip().casefold().rstrip("/").removeprefix("https://").removeprefix("http://")


def same_url(a: str, b: str) -> bool:
    """Whether two URLs name one page, as `Target.same_posting` compares them."""
    return _norm_url(a) == _norm_url(b)


class Target(BaseModel):
    """The posting being applied to, as the candidate describes it."""

    model_config = {"extra": "forbid"}

    job_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    company: str = Field(min_length=1)
    title: str = Field(min_length=1)
    location: str = ""
    url: str = ""
    source: Source

    def same_posting(self, other: Target) -> bool:
        """Whether `other` is, as far as can be told, the same requisition.

        The same non-empty URL is decisive. Otherwise company and title are
        compared after dropping punctuation, a trailing legal suffix ("Inc.")
        and a parenthetical ("(Summer 2027)"), and a blank location matches
        any: a variant spelling must not reopen a posting already applied to.
        """
        if self.url and other.url and same_url(self.url, other.url):
            return True
        same_location = (
            not self.location
            or not other.location
            or _norm_name(self.location) == _norm_name(other.location)
        )
        return (
            _norm_name(self.company) == _norm_name(other.company)
            and _norm_name(self.title) == _norm_name(other.title)
            and same_location
        )


class Outcome(BaseModel):
    model_config = {"extra": "forbid"}

    kind: OutcomeKind
    occurred_at: datetime
    # The `Application.sent` entry current when this was recorded, so a later
    # refresh cannot change what an outcome is attributed to. `None` for an
    # application recorded before sends were kept.
    sent_index: int | None = Field(default=None, ge=0)

    _aware = field_validator("occurred_at")(_aware)


class Sent(BaseModel):
    """One packet handed to the candidate: its hashes, and what it was built from."""

    model_config = {"extra": "forbid", "frozen": True}

    at: datetime
    resume_sha256: str
    letter_sha256: str | None = None
    answers_sha256: str | None = None
    resume_txt_sha256: str | None = None
    # The template the draft was seeded from, if any.
    based_on: str | None = None
    # Every fact the resume cites, sorted.
    cited_fact_ids: tuple[str, ...] = ()
    role_family: str | None = None

    _aware = field_validator("at")(_aware)


class Application(Target):
    """One application: the target, when, and the hashes of what was sent.

    The top-level hashes name the current packet, which `core.interview`
    checks against. `sent` keeps every packet in order, the first from `apply`
    and one more for each refresh; it is empty for an application recorded
    before it existed, whose sends are unknown rather than guessed.
    """

    submitted_at: datetime
    resume_sha256: str
    letter_sha256: str | None = None
    answers_sha256: str | None = None
    resume_txt_sha256: str | None = None
    sent: list[Sent] = Field(default_factory=list[Sent])
    outcomes: list[Outcome] = Field(default_factory=list[Outcome])

    _aware = field_validator("submitted_at")(_aware)

    @model_validator(mode="after")
    def _outcomes_name_a_send(self) -> Application:
        bad = [
            o for o in self.outcomes if o.sent_index is not None and o.sent_index >= len(self.sent)
        ]
        if bad:
            raise ValueError(f"an outcome is attributed to a send that is not recorded: {bad}")
        times = [s.at for s in self.sent]
        if times != sorted(times) or (times and times[0] < self.submitted_at):
            raise ValueError("sends must be in order, none before the application")
        # When the first send is recorded, every outcome came after it, so
        # each names its send; an unattributed one would vanish from insights.
        if (
            times
            and times[0] == self.submitted_at
            and any(o.sent_index is None for o in self.outcomes)
        ):
            raise ValueError("an outcome of an application with recorded sends names none")
        return self


def load_applications() -> list[Application]:
    """Every recorded application. A missing or empty file is none; anything
    else that is not a list is refused, not read as "no applications" -- the
    volume rules would otherwise be lifted by a corrupted record."""
    path = resolve_inside(APPLICATIONS_FILE)
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
        parsed: object = yaml.safe_load(text)
        if parsed is None and not text.strip():
            return []
        if not isinstance(parsed, list):
            raise ValueError(f"expected a list, found {type(parsed).__name__}")
        # The boundary where this starts trusting the file's shape;
        # `model_validate` is what checks each entry.
        return [Application.model_validate(item) for item in cast("list[object]", parsed)]
    except (OSError, yaml.YAMLError, ValueError, ValidationError) as exc:
        raise ApplyError(f"{path} is not a readable application record: {exc}") from exc


def _save(applications: list[Application]) -> None:
    body = yaml.safe_dump([a.model_dump(mode="json") for a in applications], sort_keys=False)
    write_private(resolve_inside(APPLICATIONS_FILE), body)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_record(path: Path, what: str, fields: tuple[str, ...]) -> dict[str, str]:
    """A gate report's JSON record, with each of `fields` present as a string.

    A missing, unreadable or mistyped record is refused by name, rather than
    surfacing as a raw exception or being misread as a changed PDF.
    """
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplyError(f"cannot read the {what}'s gate record {path.name}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ApplyError(f"the {what}'s gate record {path.name} is not a JSON object")
    record = cast("dict[str, object]", parsed)
    bad = [f for f in fields if not isinstance(record.get(f), str)]
    if bad:
        raise ApplyError(f"the {what}'s gate record {path.name} lacks a text value for {bad}")
    return {f: cast("str", record[f]) for f in fields}


def _check_facts(record: Path, what: str, command: str) -> None:
    """Refuse a record judged against a fact store other than today's.

    A passed document is known to pass only against the facts it was checked
    with. One retired since backs nothing, and the packet would carry its
    claim all the same. A record that names no store matches only the absence
    of one.
    """
    judged = cast("dict[str, object]", json.loads(record.read_text(encoding="utf-8"))).get(
        "facts_sha256"
    )
    if judged != facts_sha256():
        raise ApplyError(
            f"the facts have changed since the {what} was checked: run `callback {command}` again"
        )


def _check_draft(draft: Path, *, accept_warnings: bool) -> tuple[Path, Path | None]:
    """The resume and letter PDFs to send, once their reports say they may be."""
    report_path = draft / "report.json"
    resume = draft / "resume.pdf"
    if not report_path.is_file() or not resume.is_file():
        raise ApplyError(f"no built resume in {draft}: run `callback build` first")
    report = _read_record(report_path, "resume", ("verdict", "pdf_sha256", "doc_sha256"))
    verdict = report["verdict"]
    if verdict == "warn" and not accept_warnings:
        raise ApplyError("the resume's gates returned warn; read report.md, then accept warnings")
    if verdict not in {"pass", "warn"}:
        raise ApplyError(f"the resume did not pass its gates (verdict {verdict!r})")
    if _sha256(resume) != report["pdf_sha256"]:
        raise ApplyError("resume.pdf has changed since it was gated: run `callback build` again")
    document = draft / "resume.yaml"
    if not document.is_file() or _sha256(document) != report["doc_sha256"]:
        # The packet's plain text and form fields come from this file.
        raise ApplyError("resume.yaml has changed since it was gated: run `callback build` again")
    _check_facts(report_path, "resume", "build")

    letter = draft / "cover-letter.pdf"
    if not letter.is_file():
        return resume, None
    letter_record = _read_record(
        draft / "letter-report.json", "cover letter", ("verdict", "pdf_sha256")
    )
    if letter_record["verdict"] != "pass":
        raise ApplyError("the cover letter did not pass its checks: run `callback letter` again")
    if _sha256(letter) != letter_record["pdf_sha256"]:
        raise ApplyError(
            "cover-letter.pdf has changed since it was checked: run `callback letter` again"
        )
    _check_facts(draft / "letter-report.json", "cover letter", "letter")
    return resume, letter


def _check_answers(draft: Path) -> str | None:
    """The form answers to include, if there are any, once their record says they may be."""
    path = draft / "answers.md"
    if not path.is_file():
        return None
    record = _read_record(draft / "answers-report.json", "form answers", ("verdict", "sha256"))
    if record["verdict"] != "pass":
        raise ApplyError("the form answers did not pass their checks: run `callback answers` again")
    text = path.read_text(encoding="utf-8")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != record["sha256"]:
        raise ApplyError(
            "answers.md has changed since it was checked: run `callback answers` again"
        )
    _check_facts(draft / "answers-report.json", "form answers", "answers")
    return text


def _check_volume(target: Target, existing: list[Application], now: datetime) -> None:
    for prior in existing:
        if prior.job_id == target.job_id:
            raise ApplyError(
                f"already applied to {target.job_id!r} on {prior.submitted_at:%Y-%m-%d}"
            )
        if prior.same_posting(target) and now - prior.submitted_at < REAPPLY_WINDOW:
            raise ApplyError(
                f"applied to {prior.company} / {prior.title} on {prior.submitted_at:%Y-%m-%d} "
                f"as {prior.job_id!r}; the same posting may not be re-applied to within 90 days"
            )
    recent = [a for a in existing if now - a.submitted_at < timedelta(days=1)]
    if len(recent) >= MAX_PER_DAY:
        raise ApplyError(
            f"{len(recent)} applications in the last 24 hours; the limit is {MAX_PER_DAY}"
        )


def _based_on(draft: Path) -> str | None:
    """The template the draft was seeded from, or `None` when there is no stamp."""
    path = draft / ORIGIN
    if not path.is_file():
        return None
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplyError(f"cannot read the draft's {ORIGIN}: {exc}") from exc
    name = cast("dict[str, object]", parsed).get("template") if isinstance(parsed, dict) else None
    if not isinstance(name, str):
        raise ApplyError(f"the draft's {ORIGIN} names no template")
    return name


def _cited(doc: ResumeDoc) -> tuple[str, ...]:
    ids = {
        fid
        for section in doc.sections
        for entry in section.entries
        for fid in (*entry.sources, *(s for b in entry.bullets for s in b.sources))
    }
    return tuple(sorted(ids))


def _role_family(requested: str | None, prior: Application | None) -> str | None:
    """The role family to record: the one asked for, checked against the
    candidate's list, or on a refresh the one the application already had."""
    from core.notes import NoteError, role_families

    if requested is None:
        return prior.sent[-1].role_family if prior is not None and prior.sent else None
    try:
        known = role_families()
    except NoteError as exc:
        raise ApplyError(str(exc)) from exc
    if requested not in known:
        listed = ", ".join(known) or "none yet"
        raise ApplyError(
            f"{requested!r} is not one of your role families ({listed}); "
            "add it with `callback notes families --set`"
        )
    return requested


def apply(
    target: Target,
    *,
    now: datetime,
    accept_warnings: bool = False,
    refresh: bool = False,
    role_family: str | None = None,
) -> Path:
    """Build the application packet for `target` and record the application.

    With `refresh`, rebuild the packet for a job already recorded -- after the
    draft was improved, say -- under the same gate and hash checks. The
    record keeps its original date and is not counted again. Its top-level
    hashes change to name the new packet, and the new packet is appended to
    `sent`, leaving earlier sends, and the outcomes attributed to them, as
    they were.

    `role_family` must be one of the candidate's role families in
    `notes.yaml`; on a refresh, leaving it out keeps the one already recorded.

    Returns the packet directory. Raises `ApplyError`, having written
    nothing, when the draft is not ready or a volume rule refuses it.
    """
    _aware(now)
    draft = resolve_inside(f"outputs/{target.job_id}/draft")
    resume, letter = _check_draft(draft, accept_warnings=accept_warnings)
    answers = _check_answers(draft)
    existing = load_applications()
    prior = next((a for a in existing if a.job_id == target.job_id), None)
    if refresh:
        if prior is None:
            raise ApplyError(f"no application recorded for {target.job_id!r} to refresh")
        existing = [a for a in existing if a.job_id != target.job_id]
    else:
        _check_volume(target, existing, now)
    try:
        doc = parse_doc((draft / "resume.yaml").read_bytes(), origin=draft / "resume.yaml")
    except (DocumentError, OSError) as exc:
        raise ApplyError(f"cannot read the job's resume document: {exc}") from exc
    family = _role_family(role_family, prior)
    based_on = _based_on(draft)

    # Built in a staging directory and moved into place only once the record
    # is saved: a packet with no record is one the volume rules never hear of.
    job_dir = private_dir(f"outputs/{target.job_id}")
    staging = Path(tempfile.mkdtemp(dir=job_dir, prefix=".packet."))
    try:
        write_private_bytes(staging / "resume.pdf", resume.read_bytes())
        if letter is not None:
            write_private_bytes(staging / "cover-letter.pdf", letter.read_bytes())
        if answers is not None:
            write_private(staging / "answers.md", answers)
        write_private(staging / "resume.txt", to_text(doc))
        identity = doc.identity
        fields = {
            "name": identity.name,
            "email": identity.email,
            "phone": identity.phone,
            "links": identity.links,
        }
        write_private(staging / "fields.json", json.dumps(fields, indent=2) + "\n")
        hashes: dict[str, str | None] = {
            "resume_sha256": _sha256(resume),
            "letter_sha256": _sha256(letter) if letter is not None else None,
            "answers_sha256": _sha256(staging / "answers.md") if answers is not None else None,
            "resume_txt_sha256": _sha256(staging / "resume.txt"),
        }
        sent = Sent.model_validate(
            {
                "at": now,
                **hashes,
                "based_on": based_on,
                "cited_fact_ids": _cited(doc),
                "role_family": family,
            }
        )
        if refresh and prior is not None:
            record = prior.model_copy(update={**hashes, "sent": [*prior.sent, sent]})
        else:
            record = Application.model_validate(
                {**target.model_dump(), "submitted_at": now, **hashes, "sent": [sent]}
            )
        # The ledger first: an entry for an application whose record then failed
        # to save is visible, where a saved record with no entry would not be.
        try:
            record_event(
                {
                    "event": "refresh" if refresh else "apply",
                    "job_id": target.job_id,
                    "at": now.isoformat(),
                    **hashes,
                }
            )
        except (OSError, ValueError, KeyError) as exc:
            raise ApplyError(f"cannot record the application in the ledger: {exc}") from exc
        _save([*existing, record])
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    packet = job_dir / "packet"
    shutil.rmtree(packet, ignore_errors=True)
    staging.rename(packet)
    return packet


def void_application(job_id: str, *, now: datetime) -> None:
    """Remove the record of an application that was never submitted.

    `apply` records the packet when it is handed over, before the candidate
    submits it; a posting that closes in between leaves a record of an
    application nobody received, which `withdrew` would still count in the
    funnel and the volume rules. Removing the record is what keeps every
    reader of it correct without each one learning to skip it. The ledger
    keeps the `apply` entry and gains a `void` one, so the history survives.

    Refused once any outcome but `withdrew` is recorded: a reply, or
    `ghosted`, means it was submitted. `withdrew` is allowed because it was
    the only way to mark an unsent application before this existed. Nothing
    else can tell a silent submitted application from an unsent one,
    so the candidate's word decides, and a voided application stops counting
    toward the volume rules.
    """
    _aware(now)
    applications = load_applications()
    match = [a for a in applications if a.job_id == job_id]
    if not match:
        raise ApplyError(f"no application recorded for {job_id!r}")
    if any(o.kind != "withdrew" for o in match[0].outcomes):
        raise ApplyError(
            f"{job_id!r} has a recorded outcome, so it was submitted; use `outcome withdrew`"
        )
    try:
        record_event({"event": "void", "job_id": job_id, "at": now.isoformat()})
    except (OSError, ValueError, KeyError) as exc:
        raise ApplyError(f"cannot record the void in the ledger: {exc}") from exc
    try:
        _save([a for a in applications if a.job_id != job_id])
    except OSError as exc:
        raise ApplyError(
            f"the ledger records the void but the record could not be removed: {exc}; "
            "run `callback void` again"
        ) from exc


def record_outcome(job_id: str, kind: OutcomeKind, *, now: datetime) -> None:
    """Record what happened to the application for `job_id`."""
    applications = load_applications()
    for application in applications:
        if application.job_id == job_id:
            index = len(application.sent) - 1 if application.sent else None
            application.outcomes.append(Outcome(kind=kind, occurred_at=now, sent_index=index))
            _save(applications)
            return
    raise ApplyError(f"no application recorded for {job_id!r}")
