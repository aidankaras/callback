"""The shape every verification gate returns.

Grounding is the first gate; six more (parseability, hidden channels, and
others not yet written) are planned. A caller that aggregates every gate's
outcome into one document-level ruling needs one uniform shape to fold
over, not a different tuple signature per gate — so this is introduced now,
before those gates exist, rather than retrofitted once there are several
call sites to update in lockstep.

`ok` and `hard_failed` are kept as two separate properties rather than
collapsed into one boolean. Today they're exact opposites, because
`Verdict` has exactly one failing member (`FAIL`); every other member is
some flavour of pass. But a gate that produces a finding worth surfacing
without blocking the document — a softer verdict than today's binary
pass/fail — is exactly the kind of thing a future gate is likely to add,
and when that happens `ok` (safe to present as passing) and `hard_failed`
(must block aggregation) will actually diverge. Keeping them distinct now
means neither this class nor any of its callers has to change shape later.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.checks.grounding import Finding, Verdict


@dataclass(frozen=True)
class GateResult:
    """One gate's outcome: which gate, its verdict, and why (if it failed)."""

    gate: str
    verdict: Verdict
    findings: list[Finding]

    @property
    def ok(self) -> bool:
        """True unless the verdict is a hard failure.

        Currently the exact complement of `hard_failed` — see the module
        docstring for why the two are kept separate regardless.
        """
        return self.verdict is not Verdict.FAIL

    @property
    def hard_failed(self) -> bool:
        """True when this result must block the document, not just be noted."""
        return self.verdict is Verdict.FAIL
