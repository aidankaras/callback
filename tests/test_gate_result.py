"""The shared gate-result protocol, and the grounding gate's adapter onto it."""

from __future__ import annotations

from core.checks.grounding import Verdict, check_document, check_document_gate
from core.checks.result import GateResult
from core.profile.models import Derivation, Fact, FactKind, Provenance, Quantity, Trust

PROV = Provenance(source="onboarding_answer", trust=Trust.USER_CONFIRMED)


def fact(fid: str, **kw: object) -> Fact:
    body: dict[str, object] = {
        "id": fid,
        "kind": FactKind.EXPERIENCE_DETAIL,
        "text": "x",
        "provenance": PROV,
    }
    body.update(kw)
    return Fact.model_validate(body)


def test_passing_document_is_ok_and_not_hard_failed() -> None:
    f = fact("a", numbers={"queue": Quantity(value=300)})
    result = check_document_gate([("Cut the review queue to 300 items", [f])])
    assert isinstance(result, GateResult)
    assert result.gate == "grounding"
    assert result.verdict is Verdict.PASS
    assert result.findings == []
    assert result.ok
    assert not result.hard_failed


def test_failing_document_is_hard_failed_and_carries_its_findings() -> None:
    f = fact("b", numbers={})
    result = check_document_gate([("Lifted retention 22%", [f])])
    assert result.verdict is Verdict.FAIL
    assert result.findings != []
    assert not result.ok
    assert result.hard_failed


def test_derived_verdict_is_ok_like_a_plain_pass() -> None:
    """A non-FAIL verdict is `ok` regardless of which passing flavour it is —
    `DERIVED_VERIFIED` and `ALIAS_MATCH` both mean the document passed, just
    with a note on how, and neither should read as a hard failure."""
    f = fact(
        "c",
        numbers={"before": Quantity(value=500), "after": Quantity(value=300)},
        derived=[
            Derivation.model_validate(
                {"label": "cut", "op": "pct_change", "from": ["before", "after"]}
            )
        ],
    )
    result = check_document_gate([("Cut review volume 40%", [f])])
    assert result.verdict is Verdict.DERIVED_VERIFIED
    assert result.ok
    assert not result.hard_failed


def test_gate_result_wraps_check_document_exactly() -> None:
    """`check_document_gate` must not diverge from `check_document`'s own
    verdict and findings — it is a thin wrapper, not a second implementation
    that could drift from the first."""
    ok = fact("d", numbers={"queue": Quantity(value=300)})
    bad = fact("e", numbers={})
    bullets = [
        ("Cut the review queue to 300 items", [ok]),
        ("Lifted retention 22%", [bad]),
    ]
    tuple_verdict, tuple_findings = check_document(bullets)
    result = check_document_gate(bullets)
    assert result.verdict is tuple_verdict
    assert result.findings == tuple_findings
