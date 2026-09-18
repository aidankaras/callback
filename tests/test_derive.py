import pytest

from core.checks.derive import DerivationError, compute, date_hull
from core.profile.models import Dates, Derivation, Fact, FactKind, Provenance, Quantity, Trust

PROV = Provenance(source="onboarding_answer", trust=Trust.USER_CONFIRMED)


def _fact(fid: str, **kw: object) -> Fact:
    body: dict[str, object] = {
        "id": fid,
        "kind": FactKind.EXPERIENCE_DETAIL,
        "text": "x",
        "provenance": PROV,
    }
    body.update(kw)
    return Fact.model_validate(body)


def test_pct_change_is_recomputed() -> None:
    fact = _fact(
        "a",
        numbers={"before": Quantity(value=500), "after": Quantity(value=300)},
        derived=[
            Derivation.model_validate(
                {"label": "cut", "op": "pct_change", "from": ["before", "after"]}
            )
        ],
    )
    assert compute(fact.derived[0], [fact]) == pytest.approx(40.0)


def test_count_uses_enumerated_entities() -> None:
    fact = _fact(
        "b",
        entities=["Ana", "Ben", "Cleo"],
        derived=[Derivation.model_validate({"label": "team", "op": "count", "from": ["entities"]})],
    )
    assert compute(fact.derived[0], [fact]) == 3.0


def test_values_may_come_from_two_cited_facts() -> None:
    one = _fact("c", numbers={"before": Quantity(value=8.0)})
    two = _fact("d", numbers={"after": Quantity(value=2.0)})
    derivation = Derivation.model_validate(
        {"label": "cut", "op": "pct_change", "from": ["before", "after"]}
    )
    assert compute(derivation, [one, two]) == pytest.approx(75.0)


def test_unknown_label_raises() -> None:
    fact = _fact(
        "e", derived=[Derivation.model_validate({"label": "z", "op": "sum", "from": ["nope"]})]
    )
    with pytest.raises(DerivationError):
        compute(fact.derived[0], [fact])


def test_unknown_op_raises() -> None:
    derivation = Derivation.model_validate({"label": "z", "op": "sqrt", "from": ["a"]})
    with pytest.raises(DerivationError):
        compute(derivation, [_fact("f", numbers={"a": Quantity(value=4)})])


def test_date_hull_spans_cited_facts() -> None:
    one = _fact("g", dates=Dates(start="2024-06", end="2024-08"))
    two = _fact("h", dates=Dates(start="2025-01", end="2025-05"))
    assert date_hull([one, two]) == ("2024-06", "2025-05")


# --- Arity and divisor validation: every fixed-arity op must reject a bad
# argument count with DerivationError, never a bare ValueError from tuple
# unpacking (`before, after = args` raises ValueError on the wrong count). ---


def test_pct_change_wrong_arity_raises_derivation_error() -> None:
    fact = _fact(
        "arity-pct",
        numbers={"before": Quantity(value=500)},
        derived=[
            Derivation.model_validate({"label": "cut", "op": "pct_change", "from": ["before"]})
        ],
    )
    with pytest.raises(DerivationError):
        compute(fact.derived[0], [fact])


def test_difference_wrong_arity_raises_derivation_error() -> None:
    fact = _fact(
        "arity-diff",
        numbers={"a": Quantity(value=1), "b": Quantity(value=2), "c": Quantity(value=3)},
        derived=[
            Derivation.model_validate({"label": "d", "op": "difference", "from": ["a", "b", "c"]})
        ],
    )
    with pytest.raises(DerivationError):
        compute(fact.derived[0], [fact])


def test_ratio_wrong_arity_raises_derivation_error() -> None:
    fact = _fact(
        "arity-ratio",
        numbers={"a": Quantity(value=1)},
        derived=[Derivation.model_validate({"label": "r", "op": "ratio", "from": ["a"]})],
    )
    with pytest.raises(DerivationError):
        compute(fact.derived[0], [fact])


def test_count_wrong_arity_raises_derivation_error() -> None:
    fact = _fact(
        "arity-count",
        entities=["Ana"],
        derived=[
            Derivation.model_validate(
                {"label": "team", "op": "count", "from": ["entities", "extra"]}
            )
        ],
    )
    with pytest.raises(DerivationError):
        compute(fact.derived[0], [fact])


def test_ratio_zero_divisor_raises_derivation_error() -> None:
    fact = _fact(
        "zero-div",
        numbers={"a": Quantity(value=10), "b": Quantity(value=0)},
        derived=[Derivation.model_validate({"label": "r", "op": "ratio", "from": ["a", "b"]})],
    )
    with pytest.raises(DerivationError):
        compute(fact.derived[0], [fact])


def test_pct_change_increase_is_negative() -> None:
    # Pins the sign convention: a decrease (before > after) is positive
    # (see test_pct_change_is_recomputed, 500 -> 300 gives +40.0); an
    # increase (before < after) is therefore negative.
    fact = _fact(
        "increase",
        numbers={"before": Quantity(value=100), "after": Quantity(value=150)},
        derived=[
            Derivation.model_validate(
                {"label": "growth", "op": "pct_change", "from": ["before", "after"]}
            )
        ],
    )
    assert compute(fact.derived[0], [fact]) == pytest.approx(-50.0)


def test_sum_with_no_sources_raises_derivation_error() -> None:
    derivation = Derivation.model_validate({"label": "total", "op": "sum", "from": []})
    with pytest.raises(DerivationError):
        compute(derivation, [_fact("empty-sum")])


def test_count_with_no_sources_raises_derivation_error() -> None:
    derivation = Derivation.model_validate({"label": "team", "op": "count", "from": []})
    with pytest.raises(DerivationError):
        compute(derivation, [_fact("empty-count")])


def test_count_sums_entities_across_multiple_cited_facts() -> None:
    one = _fact("team-one", entities=["Ana", "Ben"])
    two = _fact("team-two", entities=["Cleo"])
    derivation = Derivation.model_validate({"label": "team", "op": "count", "from": ["entities"]})
    assert compute(derivation, [one, two]) == 3.0


def test_pct_change_zero_baseline_raises_derivation_error() -> None:
    fact = _fact(
        "zero-base",
        numbers={"before": Quantity(value=0), "after": Quantity(value=5)},
        derived=[
            Derivation.model_validate(
                {"label": "cut", "op": "pct_change", "from": ["before", "after"]}
            )
        ],
    )
    with pytest.raises(DerivationError):
        compute(fact.derived[0], [fact])


# --- Cross-fact label collisions: two cited facts must not silently define
# the same number label, even when only one of the two is disjoint-safe. ---


def test_compute_rejects_cross_fact_label_collision() -> None:
    one = _fact("fact-one", numbers={"before": Quantity(value=500)})
    two = _fact("fact-two", numbers={"before": Quantity(value=100), "after": Quantity(value=50)})
    derivation = Derivation.model_validate(
        {"label": "cut", "op": "pct_change", "from": ["before", "after"]}
    )
    with pytest.raises(DerivationError) as exc_info:
        compute(derivation, [one, two])
    message = str(exc_info.value)
    assert "before" in message
    assert "fact-one" in message
    assert "fact-two" in message


def test_compute_rejects_collision_even_on_a_label_the_derivation_ignores() -> None:
    # `cut` only references `before`/`after`, but the two cited facts also
    # each define an unrelated `shared` label. That collision must still be
    # rejected: whether it fires cannot depend on which derivation happens
    # to run first over this same pair of facts.
    one = _fact("fact-a", numbers={"before": Quantity(value=1.0), "shared": Quantity(value=10.0)})
    two = _fact("fact-b", numbers={"after": Quantity(value=2.0), "shared": Quantity(value=20.0)})
    derivation = Derivation.model_validate(
        {"label": "cut", "op": "pct_change", "from": ["before", "after"]}
    )
    with pytest.raises(DerivationError) as exc_info:
        compute(derivation, [one, two])
    message = str(exc_info.value)
    assert "shared" in message
    assert "fact-a" in message
    assert "fact-b" in message


# --- date_hull: mixed precision and partial ranges ---


def test_date_hull_handles_fact_with_start_but_no_end() -> None:
    fact = _fact("only-start", dates=Dates(start="2024-01"))
    assert date_hull([fact]) == ("2024-01", None)


def test_date_hull_mixed_precision_orders_lexicographically() -> None:
    # A year-precision date and a month-precision date within the same year
    # compare as plain strings. "2024" is a string-prefix of "2024-06", so
    # it sorts BEFORE it — that makes the coarse date win the `start` (min)
    # comparison, which is a reasonable conservative choice, but it can
    # never win the `end` (max) comparison against any same-year
    # month-precision date, even if the true (unknown) month is later than
    # June. This test documents that lexicographic ordering, not asserts it
    # is the "right" answer.
    year_only = _fact("year", dates=Dates(start="2024", end="2024"))
    month = _fact("month", dates=Dates(start="2024-06", end="2024-06"))
    assert date_hull([year_only, month]) == ("2024", "2024-06")


def test_date_hull_with_no_facts_returns_none_pair() -> None:
    assert date_hull([]) == (None, None)


def test_date_hull_with_undated_facts_returns_none_pair() -> None:
    fact = _fact("no-dates")
    assert date_hull([fact]) == (None, None)


def test_date_hull_does_not_validate_a_facts_own_start_after_end() -> None:
    # Per-fact date validity (an end before its own start) is not enforced
    # anywhere in this codebase, `Dates` included — this test pins the
    # actual behavior (min of starts, max of ends, taken independently)
    # rather than asserting it is "correct": a caller must not assume a
    # fact's own dates arrive in order.
    fact = _fact("backwards", dates=Dates(start="2024-08", end="2024-06"))
    assert date_hull([fact]) == ("2024-08", "2024-06")
