from hypothesis import given
from hypothesis import strategies as st

from core.checks.grounding import Verdict, check_bullet
from core.profile.models import Fact, FactKind, Provenance, Quantity, Trust

PROV = Provenance(source="onboarding_answer", trust=Trust.USER_CONFIRMED)


def _fact(numbers: dict[str, Quantity]) -> Fact:
    return Fact.model_validate(
        {
            "id": "f",
            "kind": FactKind.EXPERIENCE_DETAIL,
            "text": "x",
            "numbers": numbers,
            "provenance": PROV,
        }
    )


@given(st.integers(min_value=1, max_value=99_999), st.integers(min_value=1, max_value=99_999))
def test_any_number_absent_from_the_facts_is_rejected(claimed: int, sourced: int) -> None:
    """Negative direction: a figure not present in (or derivable from) the facts never passes."""
    if abs(claimed - sourced) / sourced <= 0.005:
        return
    verdict, findings = check_bullet(
        f"Improved throughput to {claimed} per hour",
        [_fact({"throughput": Quantity(value=float(sourced))})],
    )
    assert verdict is Verdict.FAIL
    assert any(f.kind == "unresolved_number" for f in findings)


@given(st.integers(min_value=1, max_value=99_999))
def test_any_sourced_number_is_accepted(sourced: int) -> None:
    """Positive direction: a figure that is in the facts always passes."""
    verdict, findings = check_bullet(
        f"Handled {sourced} requests per minute",
        [_fact({"rps": Quantity(value=float(sourced))})],
    )
    assert verdict is Verdict.PASS, findings
