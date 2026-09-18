"""Recomputation of derived quantities.

A resume routinely states a figure that is implied by the underlying facts
rather than recorded in them: a percentage change, a headcount, a date span.
Treating those as unsourced would reject correct bullets, so each derivation
names an operation which is recomputed here from the cited facts. The model
supplies the operation and its inputs; the value itself is never taken on
faith, and every way the inputs can be malformed — a missing label, an
ambiguous one defined by more than one cited fact, an operation with the
wrong number of arguments, a zero divisor — surfaces as `DerivationError`
so a caller can catch one exception type and know it has seen every way
recomputation can fail.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from core.profile.models import Derivation, Fact


class DerivationError(ValueError):
    """Raised when a derivation cannot be recomputed from the cited facts."""


def _sum(args: Sequence[float]) -> float:
    return float(sum(args))


def _require_pair(op: str, args: Sequence[float]) -> tuple[float, float]:
    """Validate that a binary op received exactly two values.

    Plain tuple unpacking (`a, b = args`) raises a bare `ValueError` on the
    wrong count, which is indistinguishable from any other `ValueError` a
    caller might be catching. Checking the length explicitly first lets the
    failure carry the offending op name and be raised as `DerivationError`.
    """
    if len(args) != 2:
        raise DerivationError(f"{op!r} requires exactly two values, got {len(args)}")
    return args[0], args[1]


def _pct_change(args: Sequence[float]) -> float:
    before, after = _require_pair("pct_change", args)
    if before == 0:
        raise DerivationError("pct_change with a zero baseline is undefined")
    return (before - after) / before * 100.0


def _difference(args: Sequence[float]) -> float:
    minuend, subtrahend = _require_pair("difference", args)
    return minuend - subtrahend


def _ratio(args: Sequence[float]) -> float:
    numerator, denominator = _require_pair("ratio", args)
    if denominator == 0:
        raise DerivationError("ratio with a zero divisor is undefined")
    return numerator / denominator


OPS: dict[str, Callable[[Sequence[float]], float]] = {
    "pct_change": _pct_change,
    "difference": _difference,
    "sum": _sum,
    "ratio": _ratio,
}


def available_values(facts: Sequence[Fact]) -> dict[str, float]:
    """Flatten every cited fact's `numbers` into one label -> value map.

    Facts are cited by the derivation's own `from` list, not by this
    function, so it has no way to tell which fact a label was meant to come
    from. If two *different* facts both carry the same label (e.g. two
    facts each using `before`), a derivation could silently compute from
    whichever one happens to iterate last — a value the author never
    intended, indistinguishable downstream from one they did. This is
    rejected outright, as `DerivationError` naming the label and both fact
    ids, rather than resolved by iteration order. The check applies to
    every label on every fact passed in, not only the ones some particular
    derivation's `from` list references: limiting it to referenced labels
    would make whether an error fires depend on which derivation happens to
    run first over the same fact set, which is the same kind of
    order-dependence this is meant to remove. A label repeating within a
    single fact's `numbers` cannot happen — `numbers` is itself a dict — so
    only cross-fact collisions are possible here.
    """
    values: dict[str, float] = {}
    owning_fact: dict[str, str] = {}
    for fact in facts:
        for label, quantity in fact.numbers.items():
            owner = owning_fact.get(label)
            if owner is not None and owner != fact.id:
                raise DerivationError(
                    f"label {label!r} is ambiguous: both fact {owner!r} and "
                    f"fact {fact.id!r} define it"
                )
            values[label] = quantity.value
            owning_fact[label] = fact.id
    return values


def compute(derivation: Derivation, facts: Sequence[Fact]) -> float:
    """Recompute `derivation`'s value from the numbers in `facts`.

    Raises `DerivationError` for every failure mode: an unsupported op, a
    label absent from every cited fact, a label defined ambiguously by more
    than one cited fact, the wrong number of source values for a
    fixed-arity op, or a zero divisor. `DerivationError` is itself a
    `ValueError` subclass, but no *other* exception type — a bare
    `ValueError` from tuple unpacking, a `ZeroDivisionError`, a `KeyError`
    — ever escapes; a caller catching `DerivationError` sees every failure.
    """
    if derivation.op == "count":
        if len(derivation.from_) != 1:
            raise DerivationError(
                f"derivation {derivation.label!r}: count takes exactly one source, "
                f"got {len(derivation.from_)}"
            )
        (source,) = derivation.from_
        if source != "entities":
            raise DerivationError(
                f"derivation {derivation.label!r}: count derives only from the entities list"
            )
        return float(sum(len(fact.entities) for fact in facts))

    operation = OPS.get(derivation.op)
    if operation is None:
        raise DerivationError(
            f"derivation {derivation.label!r} has unsupported op {derivation.op!r}"
        )

    try:
        values = available_values(facts)
    except DerivationError as exc:
        raise DerivationError(f"derivation {derivation.label!r}: {exc}") from exc

    try:
        args = [values[label] for label in derivation.from_]
    except KeyError as exc:
        raise DerivationError(
            f"derivation {derivation.label!r} references unknown label {exc.args[0]!r}"
        ) from exc

    try:
        return operation(args)
    except DerivationError as exc:
        raise DerivationError(f"derivation {derivation.label!r}: {exc}") from exc


def date_hull(facts: Sequence[Fact]) -> tuple[str | None, str | None]:
    """Return the earliest start and latest end among the cited facts' dates.

    Dates are compared as plain ISO strings (`"2024-06"`, `"2024"`), which
    is only sound because ISO 8601's field order makes lexicographic and
    chronological order agree for two dates at the *same* precision. Mixed
    precisions still compare, but not always the way a reader would expect
    — see the task report for a documented case.
    """
    starts = [fact.dates.start for fact in facts if fact.dates and fact.dates.start]
    ends = [fact.dates.end for fact in facts if fact.dates and fact.dates.end]
    return (min(starts) if starts else None, max(ends) if ends else None)
