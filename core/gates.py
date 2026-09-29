"""Run every deterministic gate on one rendered document and fold the results.

Each gate predates this module and returns its own shape -- a
`(Verdict, findings)` tuple, a result dataclass, a `(str, findings)` pair.
`run_gates` adapts each into one `GateOutcome`: a verdict and its findings
as text.

Three properties matter more than any single gate:

- **Every gate always runs.** A failure in grounding does not skip the
  hidden-channel screen; the report shows everything wrong with a document
  at once, rather than one defect per rebuild.
- **A gate that raises is a failed outcome, not a crash.** The exception's
  type and message become its finding, and every other gate's outcome is
  still reported. A gate that could not run must never read as one that
  ran and found nothing.
- **A report always carries every gate, once, in `GATES` order, and its
  verdict is derived from them rather than stored beside them.** A report
  missing a gate, or claiming a verdict its outcomes do not support, cannot
  be constructed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from core.checks.claims import check_claims
from core.checks.grounding import Verdict, check_bullet
from core.checks.hidden import check_hidden
from core.checks.lints import check_lints
from core.checks.roundtrip import check_roundtrip
from core.checks.skills import check_skills
from core.profile.models import Fact
from core.resume.models import ResumeDoc

Outcome = Literal["pass", "warn", "fail"]
GateName = Literal["grounding", "claims", "roundtrip", "hidden", "skills", "lints"]

GATES: tuple[GateName, ...] = ("grounding", "claims", "roundtrip", "hidden", "skills", "lints")

_KNOWN: dict[str, Outcome] = {"pass": "pass", "warn": "warn", "fail": "fail"}


@dataclass(frozen=True)
class FactFinding:
    """Why a bullet failed grounding, with the facts it cited.

    The text findings say which bullet failed; this says which facts, so the
    same fault recurring across jobs can be traced to one fact and fixed there.
    """

    kind: str
    facts: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class GateOutcome:
    """One gate's verdict and its findings as text; grounding adds them per fact."""

    gate: GateName
    verdict: Outcome
    findings: tuple[str, ...]
    fact_findings: tuple[FactFinding, ...] = ()


def aggregate(outcomes: Sequence[GateOutcome]) -> Outcome:
    """The document's verdict, from every gate's outcome.

    This is the policy line that decides what "this resume passes" means:
    only a document every gate passed is a pass; one whose gates all passed
    or warned is a warning -- deliverable, but only once someone has read
    why. Anything else fails, including no outcomes at all (nothing was
    checked) and any verdict this function does not recognise: it is
    written as an allowlist, so a verdict string it has never seen can only
    ever fail a document, never pass one.
    """
    verdicts = {o.verdict for o in outcomes}
    if verdicts == {"pass"}:
        return "pass"
    if verdicts and verdicts <= {"pass", "warn"}:
        return "warn"
    return "fail"


@dataclass(frozen=True)
class GateReport:
    """Every gate's outcome, exactly once and in `GATES` order."""

    outcomes: tuple[GateOutcome, ...]

    def __post_init__(self) -> None:
        names = tuple(o.gate for o in self.outcomes)
        if names != GATES:
            raise ValueError(f"a report needs every gate once, in order {GATES}; got {names}")

    @property
    def verdict(self) -> Outcome:
        return aggregate(self.outcomes)


def _grounding(doc: ResumeDoc, facts: Mapping[str, Fact]) -> tuple[GateOutcome, list[str]]:
    """Ground each bullet on its own, returning the outcome and the texts of
    the bullets that passed -- the only bullets that count as evidence for a
    listed skill."""
    findings: list[str] = []
    by_fact: list[FactFinding] = []
    grounded: list[str] = []
    failed = False
    bullets = [b for s in doc.sections for e in s.entries for b in e.bullets]
    for i, bullet in enumerate(bullets):
        cited = tuple(sorted(set(bullet.sources)))
        unknown = [s for s in bullet.sources if s not in facts]
        if unknown:
            findings.append(f"bullet {i}: cites unknown fact(s) {unknown}")
            # Named per fact too, so a missing fact cited in job after job is
            # reported as recurring rather than lost beside a traced failure.
            by_fact.append(FactFinding("unknown_fact", cited, f"cites unknown fact(s) {unknown}"))
            failed = True
            continue
        verdict, bullet_findings = check_bullet(
            bullet.text, [facts[s] for s in bullet.sources], index=i
        )
        findings.extend(f"bullet {i}: {f.kind}: {f.detail}" for f in bullet_findings)
        if verdict is Verdict.FAIL:
            failed = True
            by_fact.extend(FactFinding(str(f.kind), cited, f.detail) for f in bullet_findings)
        else:
            grounded.append(bullet.text)
    if not bullets:
        findings.append("document has no bullets to ground")
        failed = True
    outcome = GateOutcome(
        "grounding", "fail" if failed else "pass", tuple(findings), tuple(by_fact)
    )
    return outcome, grounded


def _crashed(gate: GateName, error: Exception) -> GateOutcome:
    return GateOutcome(gate, "fail", (f"gate raised {type(error).__name__}: {error}",))


def _guarded(gate: GateName, run: Callable[[], tuple[Outcome, tuple[str, ...]]]) -> GateOutcome:
    """Run one gate, naming its outcome here so the name is written once."""
    try:
        verdict, findings = run()
    except Exception as error:
        return _crashed(gate, error)
    return GateOutcome(gate, verdict, findings)


def run_gates(
    doc: ResumeDoc, facts: Mapping[str, Fact], pdf_path: Path, *, max_pages: int = 1
) -> GateReport:
    """Run every gate in `GATES` on `doc` and the PDF rendered from it."""
    try:
        grounding, grounded = _grounding(doc, facts)
    except Exception as error:
        grounding, grounded = _crashed("grounding", error), []

    def claims() -> tuple[Outcome, tuple[str, ...]]:
        result = check_claims(doc, facts)
        return result.verdict, result.findings

    def roundtrip() -> tuple[Outcome, tuple[str, ...]]:
        result = check_roundtrip(doc, pdf_path)
        # An unrecognised verdict string is treated as a failure, never a pass.
        return _KNOWN.get(result.verdict, "fail"), tuple(result.findings)

    def hidden() -> tuple[Outcome, tuple[str, ...]]:
        verdict, found = check_hidden(pdf_path)
        texts = tuple(f"{f.kind}: {f.detail}" for f in found)
        return ("pass" if verdict == "pass" and not found else "fail"), texts

    def skills() -> tuple[Outcome, tuple[str, ...]]:
        result = check_skills(doc, grounded)
        texts = tuple(result.findings) + tuple(
            f"skill {s!r} is listed but no grounded bullet shows it" for s in result.listed_only
        )
        return ("warn" if result.listed_only else "pass"), texts

    def lints() -> tuple[Outcome, tuple[str, ...]]:
        # Layout is advice: a lint can warn, never fail a document.
        found = check_lints(doc, pdf_path, max_pages=max_pages)
        return ("warn" if found else "pass"), tuple(f"{x.rule}: {x.detail}" for x in found)

    return GateReport(
        outcomes=(
            grounding,
            _guarded("claims", claims),
            _guarded("roundtrip", roundtrip),
            _guarded("hidden", hidden),
            _guarded("skills", skills),
            _guarded("lints", lints),
        )
    )
