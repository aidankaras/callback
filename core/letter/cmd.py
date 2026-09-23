"""`callback letter`: render a cover letter and check it against facts and posting.

The posting is read from `inbox/<job>.md`, where the tailor skill saves it; a
letter for a job whose posting was never saved is refused, since its company,
role and every sentence about the job are checked against that text. Output
goes to the job's draft directory, beside the resume, at 0600; the JSON
record carries the SHA-256 of the letter PDF it judged.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.buildcmd import BuildError
from core.checks.hidden import check_hidden
from core.draft import LETTER_ARTIFACTS
from core.letter.checks import check_letter
from core.letter.io import parse_letter
from core.letter.render import render_letter
from core.paths import private_dir, resolve_inside, write_private, write_private_bytes
from core.profile.store import load_facts
from core.render.typst import RenderError
from core.resume.io import DocumentError, load_doc


@dataclass(frozen=True)
class LetterBuild:
    findings: tuple[str, ...]
    out_dir: Path

    @property
    def exit_code(self) -> int:
        return 2 if self.findings else 0


def build_letter(path: Path) -> LetterBuild:
    """Render the letter at `path`, check it, and write the PDF and a report."""
    letter = parse_letter(path.read_bytes(), origin=path)
    posting_path = resolve_inside(f"inbox/{letter.job_id}.md")
    if not posting_path.is_file():
        raise BuildError(
            f"no saved posting for {letter.job_id!r}: save it to {posting_path} first; "
            "the letter's company, role and claims about the job are checked against it"
        )
    posting = posting_path.read_text(encoding="utf-8")
    facts = load_facts()

    out_dir = private_dir(f"outputs/{letter.job_id}/draft")
    resume_path = out_dir / "resume.yaml"
    bullets: list[str] = []
    if resume_path.is_file():
        try:
            resume = load_doc(resume_path)
        except DocumentError as exc:
            raise BuildError(f"cannot read the job's resume to check for reuse: {exc}") from exc
        bullets = [b.text for s in resume.sections for e in s.entries for b in e.bullets]
    for name in LETTER_ARTIFACTS:
        (out_dir / name).unlink(missing_ok=True)

    result = check_letter(letter, facts, posting, resume_bullets=bullets)
    staging = Path(tempfile.mkdtemp(dir=out_dir, prefix=".letter."))
    try:
        staged = staging / "cover-letter.pdf"
        try:
            render_letter(letter, staged)
        except RenderError as exc:
            raise BuildError(f"could not render {path.name}: {exc}") from exc
        _, hidden = check_hidden(staged)
        pdf_bytes = staged.read_bytes()
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    findings = result.findings + tuple(f"hidden: {f.kind}: {f.detail}" for f in hidden)
    write_private_bytes(out_dir / "cover-letter.pdf", pdf_bytes)
    report = ["# FAIL" if findings else "# PASS", ""]
    report += [f"- {f}" for f in findings] or ["- no findings"]
    write_private(out_dir / "letter-report.md", "\n".join(report) + "\n")
    record = {
        "verdict": "fail" if findings else "pass",
        "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "findings": list(findings),
    }
    write_private(out_dir / "letter-report.json", json.dumps(record, indent=2) + "\n")
    return LetterBuild(findings=findings, out_dir=out_dir)
