"""`callback build`: render a resume document and run every gate on the result.

The output goes to `outputs/<job>/draft/` and nowhere else. A clean build is
still a draft: promoting a document to a final, sealed copy is a separate step
this command never takes.

The report records the SHA-256 of the exact document file and PDF it judged,
so a report can always be tied to the bytes it describes -- a PDF edited or
re-rendered after the build no longer matches its report.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from core.gates import GateOutcome, GateReport, run_gates
from core.paths import FILE_MODE, private_dir, write_private
from core.profile.store import load_facts
from core.render.typst import render
from core.resume.io import parse_doc

_JOB_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
EXIT_CODES = {"pass": 0, "warn": 1, "fail": 2}
_MAX_ACTIONS = 3


class BuildError(ValueError):
    """A build that could not start: a bad job id or an unreadable document."""


@dataclass(frozen=True)
class BuildResult:
    report: GateReport
    out_dir: Path

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.report.verdict]


def _actions(outcomes: tuple[GateOutcome, ...]) -> list[str]:
    """The first findings to act on: failures before warnings, in gate order."""
    ranked = [o for o in outcomes if o.verdict == "fail"] + [
        o for o in outcomes if o.verdict == "warn"
    ]
    return [f"{o.gate}: {f}" for o in ranked for f in o.findings][:_MAX_ACTIONS]


def render_report(report: GateReport) -> str:
    """Markdown: the verdict, at most three things to fix, then every gate in full."""
    lines = [f"# {report.verdict.upper()}", ""]
    actions = _actions(report.outcomes)
    if actions:
        lines += ["## Fix first", "", *(f"- {a}" for a in actions), ""]
    lines += ["## Gates", ""]
    for o in report.outcomes:
        lines.append(f"### {o.gate}: {o.verdict}")
        lines += [f"- {f}" for f in o.findings] or ["- no findings"]
        lines.append("")
    return "\n".join(lines)


def build(doc_path: Path, job_id: str) -> BuildResult:
    """Render `doc_path`, run every gate, and write the PDF and reports to the job's draft."""
    if not _JOB_ID.match(job_id):
        raise BuildError(
            f"job id {job_id!r} must be 1-64 lowercase letters, digits or hyphens, "
            "starting with a letter or digit"
        )
    doc_bytes = doc_path.read_bytes()
    doc = parse_doc(doc_bytes, origin=doc_path)
    facts = load_facts()

    out_dir = private_dir(f"outputs/{job_id}/draft")
    pdf = out_dir / "resume.pdf"
    render(doc, pdf)
    pdf.chmod(FILE_MODE)
    report = run_gates(doc, facts, pdf)

    record = {
        "verdict": report.verdict,
        "doc": str(doc_path),
        "doc_sha256": hashlib.sha256(doc_bytes).hexdigest(),
        "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "outcomes": [
            {"gate": o.gate, "verdict": o.verdict, "findings": list(o.findings)}
            for o in report.outcomes
        ],
    }
    write_private(out_dir / "report.json", json.dumps(record, indent=2) + "\n")
    write_private(out_dir / "report.md", render_report(report))
    return BuildResult(report=report, out_dir=out_dir)
