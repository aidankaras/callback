"""Run every deterministic gate on one rendered document and fold the results.

Each gate predates this module and returns its own shape -- a
`(Verdict, findings)` tuple, a result dataclass, a `(str, findings)` pair.
`run_gates` adapts each into one `GateOutcome` without discarding the
original, which stays available as `detail` for anything that needs more
than a verdict and a list of strings.

Two properties matter more than any single gate:

- **Every gate always runs.** A failure in grounding does not skip the
  hidden-channel screen; the report shows everything wrong with a document
  at once, rather than one defect per rebuild.
- **A gate that raises is a failed outcome, not a crash.** The exception's
  type and message become its finding, and every other gate's outcome is
  still reported. A gate that could not run must never read as one that
  ran and found nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from core.checks.claims import check_claims
from core.checks.grounding import Verdict, check_bullet
from core.checks.hidden import check_hidden
from core.checks.roundtrip import check_roundtrip
from core.checks.skills import check_skills
from core.profile.models import Fact
from core.resume.models import ResumeDoc

Outcome = Literal["pass", "warn", "fail"]

GATES = ("grounding", "claims", "roundtrip", "hidden", "skills")

_KNOWN: dict[str, Outcome] = {"pass": "pass", "warn": "warn", "fail": "fail"}


@dataclass(frozen=True)
class GateOutcome:
    """One gate's verdict, its findings as text, and its own full result."""

    gate: str
    verdict: Outcome
    findings: tuple[str, ...]
    detail: object


@dataclass(frozen=True)
class GateReport:
    """Every gate's outcome, in `GATES` order, and the document's verdict."""

    outcomes: tuple[GateOutcome, ...]
    verdict: Outcome


def aggregate(outcomes: Sequence[GateOutcome]) -> Outcome:
    """The document's verdict, from every gate's outcome.

    This is the policy line that decides what "this resume passes" means:
    any failed gate fails the document; otherwise any warning makes it a
    warning -- deliverable, but only once someone has read why -- and only a
    document every gate passed is a pass. No outcomes at all is a failure,
    not a vacuous pass: it means nothing was checked.
    """
    verdicts = {o.verdict for o in outcomes}
    if not verdicts or "fail" in verdicts:
        return "fail"
    return "warn" if "warn" in verdicts else "pass"


def _grounding(doc: ResumeDoc, facts: Mapping[str, Fact]) -> tuple[GateOutcome, list[str]]:
    """Ground each bullet on its own, returning the outcome and the texts of
    the bullets that passed -- the only bullets that count as evidence for a
    listed skill."""
    findings: list[str] = []
    grounded: list[str] = []
    verdicts: list[Verdict] = []
    bullets = [b for s in doc.sections for e in s.entries for b in e.bullets]
    for i, bullet in enumerate(bullets):
        unknown = [s for s in bullet.sources if s not in facts]
        if unknown:
            findings.append(f"bullet {i}: cites unknown fact(s) {unknown}")
            verdicts.append(Verdict.FAIL)
            continue
        verdict, bullet_findings = check_bullet(
            bullet.text, [facts[s] for s in bullet.sources], index=i
        )
        verdicts.append(verdict)
        findings.extend(f"bullet {i}: {f.kind}: {f.detail}" for f in bullet_findings)
        if verdict is not Verdict.FAIL:
            grounded.append(bullet.text)
    if not bullets:
        findings.append("document has no bullets to ground")
    failed = not bullets or Verdict.FAIL in verdicts
    outcome = GateOutcome(
        "grounding", "fail" if failed else "pass", tuple(findings), tuple(verdicts)
    )
    return outcome, grounded


def _crashed(gate: str, error: Exception) -> GateOutcome:
    return GateOutcome(gate, "fail", (f"gate raised {type(error).__name__}: {error}",), error)


def _guarded(gate: str, run: Callable[[], GateOutcome]) -> GateOutcome:
    try:
        return run()
    except Exception as error:
        return _crashed(gate, error)


def run_gates(doc: ResumeDoc, facts: Mapping[str, Fact], pdf_path: Path) -> GateReport:
    """Run every gate in `GATES` on `doc` and the PDF rendered from it."""
    try:
        grounding, grounded = _grounding(doc, facts)
    except Exception as error:
        grounding, grounded = _crashed("grounding", error), []

    def claims() -> GateOutcome:
        result = check_claims(doc, facts)
        return GateOutcome("claims", result.verdict, tuple(result.findings), result)

    def roundtrip() -> GateOutcome:
        result = check_roundtrip(doc, pdf_path)
        # An unrecognised verdict string is treated as a failure, never a pass.
        verdict = _KNOWN.get(result.verdict, "fail")
        return GateOutcome("roundtrip", verdict, tuple(result.findings), result)

    def hidden() -> GateOutcome:
        verdict, found = check_hidden(pdf_path)
        texts = tuple(f"{f.kind}: {f.detail}" for f in found)
        return GateOutcome("hidden", "fail" if verdict != "pass" else "pass", texts, found)

    def skills() -> GateOutcome:
        result = check_skills(doc, grounded)
        texts = tuple(result.findings) + tuple(
            f"skill {s!r} is listed but no grounded bullet shows it" for s in result.listed_only
        )
        return GateOutcome("skills", "warn" if result.listed_only else "pass", texts, result)

    outcomes = (
        grounding,
        _guarded("claims", claims),
        _guarded("roundtrip", roundtrip),
        _guarded("hidden", hidden),
        _guarded("skills", skills),
    )
    return GateReport(outcomes=outcomes, verdict=aggregate(outcomes))
