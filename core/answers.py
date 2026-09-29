"""Free-text answers to an application form's questions.

Many applications ask more than a resume and a letter can carry -- "why
this company", "describe a project", a pitch under a hard character limit
-- and the answers are typed straight into the form. Each answer here is a
list of declared sentences, held to exactly the rules a cover letter's
sentences are (see `core.letter.checks`), plus the form's own limit, counted
on the exact text that will be pasted.

`callback answers` writes `answers.md`, the paste-ready text with each
answer's count beside its limit, and a JSON record with its SHA-256, so a
packet can carry only answers that passed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from core.checks.claims import name_findings
from core.letter.checks import check_sentence, context_for, heading_findings, question_findings
from core.letter.models import Sentence
from core.paths import private_dir, resolve_inside, write_private
from core.profile.models import Fact
from core.profile.store import facts_sha256
from core.resume.io import DocumentError, load_doc


class Answer(BaseModel):
    """One form question and the answer to paste into it."""

    model_config = {"extra": "forbid"}

    question: str = Field(min_length=1)
    max_chars: int | None = Field(default=None, gt=0)
    max_words: int | None = Field(default=None, gt=0)
    sentences: list[Sentence] = Field(min_length=1)

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.sentences)


class AnswerSet(BaseModel):
    """Every free-text answer for one job's application form."""

    model_config = {"extra": "forbid"}

    job_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    company: str
    role: str
    candidate_name: str
    answers: list[Answer] = Field(min_length=1)


@dataclass(frozen=True)
class AnswersResult:
    findings: tuple[str, ...] = ()

    @property
    def verdict(self) -> Literal["pass", "fail"]:
        return "fail" if self.findings else "pass"


def check_answers(
    doc: AnswerSet,
    facts: Mapping[str, Fact],
    posting: str,
    *,
    resume_bullets: Sequence[str] = (),
) -> AnswersResult:
    """Every way `doc` fails: its sentences' rules, and each answer's limit."""
    findings = heading_findings(doc.company, doc.role, posting)
    findings.extend(name_findings(doc.candidate_name, facts))
    ctx = context_for(doc.company, doc.role, doc.candidate_name, facts, posting, resume_bullets)
    for n, answer in enumerate(doc.answers):
        findings.extend(f"answer {n}: {f}" for f in question_findings(answer.question, ctx))
        for s, sentence in enumerate(answer.sentences):
            findings.extend(f"answer {n}: {f}" for f in check_sentence(f"{n}.{s}", sentence, ctx))
        text = answer.text
        if answer.max_chars is not None and len(text) > answer.max_chars:
            findings.append(
                f"answer {n}: {len(text)} characters; the form allows {answer.max_chars}"
            )
        words = len(text.split())
        if answer.max_words is not None and words > answer.max_words:
            findings.append(f"answer {n}: {words} words; the form allows {answer.max_words}")
    return AnswersResult(findings=tuple(findings))


def render_answers(doc: AnswerSet) -> str:
    """Paste-ready Markdown: each question, its answer, and its count against the limit."""
    lines = [f"# {doc.role} - {doc.company}: application answers", ""]
    for answer in doc.answers:
        text = answer.text
        counts = [f"{len(text)} characters", f"{len(text.split())} words"]
        if answer.max_chars is not None:
            counts[0] = f"{len(text)} / {answer.max_chars} characters"
        if answer.max_words is not None:
            counts[1] = f"{len(text.split())} / {answer.max_words} words"
        lines += [f"## {answer.question}", "", text, "", f"_{', '.join(counts)}_", ""]
    return "\n".join(lines)


class AnswersError(ValueError):
    """Answers that could not be checked: an unreadable file or no saved posting."""


def build_answers(path: Path, facts: Mapping[str, Fact]) -> tuple[AnswersResult, Path]:
    """Check the answers at `path` and write them, paste-ready, to the job's draft."""
    try:
        doc = AnswerSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise AnswersError(f"cannot read {path}: {exc}") from exc
    posting_path = resolve_inside(f"inbox/{doc.job_id}.md")
    if not posting_path.is_file():
        raise AnswersError(f"no saved posting for {doc.job_id!r} at {posting_path}")
    posting = posting_path.read_text(encoding="utf-8")
    out_dir = private_dir(f"outputs/{doc.job_id}/draft")
    bullets: list[str] = []
    resume_path = out_dir / "resume.yaml"
    if resume_path.is_file():
        try:
            resume = load_doc(resume_path)
        except DocumentError as exc:
            raise AnswersError(f"cannot read the job's resume to check for reuse: {exc}") from exc
        bullets = [b.text for sec in resume.sections for e in sec.entries for b in e.bullets]
    result = check_answers(doc, facts, posting, resume_bullets=bullets)
    rendered = render_answers(doc)
    write_private(out_dir / "answers.md", rendered)
    record = {
        "verdict": result.verdict,
        "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "facts_sha256": facts_sha256(),
        "findings": list(result.findings),
    }
    write_private(out_dir / "answers-report.json", json.dumps(record, indent=2) + "\n")
    return result, out_dir
