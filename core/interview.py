"""An interview prep pack built from exactly what was sent.

An interviewer reads the resume, letter and answers the candidate submitted,
not the draft they have been editing since. So the pack starts from the
application record: the packet's files must still have the SHA-256 recorded
when the application was made, and the draft `resume.yaml` is used only
while its build report still ties it to that sent PDF. From those it
assembles the posting, what was sent, and -- for every claim the resume
makes -- the fuller facts behind it, which are the stories worth preparing.

The questions to expect and how to tell each story are judgement, and belong
to the skill; everything here is deterministic.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pdfplumber

from core.apply import Application, ApplyError, load_applications
from core.paths import private_dir, resolve_inside, write_private
from core.profile.store import StoreError, load_facts
from core.resume.io import DocumentError, parse_doc


class InterviewError(RuntimeError):
    """No pack can be built: no application, or its files have changed."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _application(job_id: str) -> Application:
    try:
        applications = load_applications()
    except ApplyError as exc:
        raise InterviewError(str(exc)) from exc
    for application in applications:
        if application.job_id == job_id:
            return application
    raise InterviewError(f"no application recorded for {job_id!r}")


def _pdf_text(path: Path) -> str:
    try:
        with pdfplumber.open(str(path)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as exc:  # pdfplumber raises its own and pdfminer's types
        raise InterviewError(f"cannot read the text of {path.name}: {exc}") from exc


def prepare(job_id: str) -> Path:
    """Write `outputs/<job>/interview/prep.md` and return its path."""
    application = _application(job_id)
    job = resolve_inside(f"outputs/{job_id}")
    packet, draft = job / "packet", job / "draft"

    resume_pdf = packet / "resume.pdf"
    if not resume_pdf.is_file() or _sha256(resume_pdf) != application.resume_sha256:
        raise InterviewError(
            "the packet's resume.pdf has changed since you applied, or is missing; "
            "the pack must describe what the interviewer has"
        )
    # Every other file the pack quotes must match the hash recorded when the
    # application was made; a file with no recorded hash was never sent.
    sent: dict[str, Path] = {}
    for name, recorded in (
        ("cover-letter.pdf", application.letter_sha256),
        ("answers.md", application.answers_sha256),
        ("resume.txt", application.resume_txt_sha256),
    ):
        path = packet / name
        if recorded is None:
            if path.exists():
                raise InterviewError(
                    f"the packet holds {name}, but the application never recorded sending it"
                )
            continue
        if not path.is_file() or _sha256(path) != recorded:
            raise InterviewError(
                f"the packet's {name} has changed since you applied, or is missing"
            )
        sent[name] = path

    try:
        report = json.loads((draft / "report.json").read_text(encoding="utf-8"))
        doc_bytes = (draft / "resume.yaml").read_bytes()
    except (OSError, json.JSONDecodeError) as exc:
        raise InterviewError(f"cannot read the job's draft: {exc}") from exc
    if report.get("pdf_sha256") != application.resume_sha256 or hashlib.sha256(
        doc_bytes
    ).hexdigest() != report.get("doc_sha256"):
        raise InterviewError(
            "resume.yaml no longer matches the resume you sent; the stories below would "
            "describe a different document"
        )
    try:
        doc = parse_doc(doc_bytes, origin=draft / "resume.yaml")
        facts = load_facts()
    except (DocumentError, StoreError) as exc:
        raise InterviewError(str(exc)) from exc

    posting_path = resolve_inside(f"inbox/{job_id}.md")
    posting = posting_path.read_text(encoding="utf-8") if posting_path.is_file() else ""

    lines = [
        f"# Interview prep: {application.title} at {application.company}",
        "",
        f"Applied {application.submitted_at:%Y-%m-%d} via {application.source}.",
    ]
    if application.outcomes:
        latest = application.outcomes[-1]
        lines.append(f"Latest: {latest.kind} on {latest.occurred_at:%Y-%m-%d}.")
    lines += ["", "## The posting", "", posting.strip() or "(no saved posting)", ""]
    lines += ["## Stories behind each claim", ""]
    for section in doc.sections:
        for entry in section.entries:
            for bullet in entry.bullets:
                lines.append(f"- **{bullet.text}** ({entry.heading})")
                for fid in bullet.sources:
                    fact = facts.get(fid)
                    if fact is None:
                        lines.append(f"  - (fact {fid!r} is no longer stored)")
                    elif not fact.selectable():
                        lines.append(f"  - (fact {fid!r} is no longer confirmed; not shown)")
                    else:
                        lines.append(f"  - {fact.text}")
    # The packet's plain-text resume is the gated document's own text, so it is
    # used rather than a re-extraction of the PDF.
    lines += ["", "## What you sent", "", "### Resume", ""]
    if "resume.txt" in sent:
        lines.append(sent["resume.txt"].read_text(encoding="utf-8").strip())
    else:
        lines.append(_pdf_text(resume_pdf).strip())
    lines.append("")
    if "cover-letter.pdf" in sent:
        lines += ["### Cover letter", "", _pdf_text(sent["cover-letter.pdf"]).strip(), ""]
    if "answers.md" in sent:
        lines += [
            "### Form answers",
            "",
            sent["answers.md"].read_text(encoding="utf-8").strip(),
            "",
        ]
    out = private_dir(f"outputs/{job_id}/interview") / "prep.md"
    write_private(out, "\n".join(lines) + "\n")
    return out
