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
    """(before - after) / before * 100, as a signed percentage.

    Sign convention: the result is *positive* for a reduction (after <
    before) and *negative* for an increase (after > before) — the formula
    itself carries no notion of "good" or "bad" direction, only whether
    the second value is smaller or larger than the first. A generated
    bullet's verb carries that direction in natural language ("cut costs
    40%" vs. "grew revenue 40%"); this function only ever returns a
    magnitude with a sign tied to before/after order, not to the verb. A
    caller comparing a claimed percentage against this derived value must
    therefore compare magnitudes (e.g. the absolute value of each), never
    signed values — otherwise a truthful "grew revenue 50%" bullet is
    compared against a derived -50.0 and is wrongly rejected.
    """
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
    fixed-arity op (including none at all), or a zero divisor.
    `DerivationError` is itself a `ValueError` subclass, but no *other*
    exception type — a bare `ValueError` from tuple unpacking, a
    `ZeroDivisionError`, a `KeyError` — ever escapes; a caller catching
    `DerivationError` sees every failure.

    `count` sums `len(entities)` over *every* cited fact, not just one —
    citing two facts adds their entity counts together rather than picking
    one fact's list. That is a deliberate reading of "how many people are
    named across what I'm citing," not an accident of the loop.
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

    if not args:
        # `sum` has no fixed arity, so nothing above catches an empty
        # `from` list — and `sum([])` is 0.0, the additive identity, which
        # would otherwise flow into a comparison looking like a real
        # figure. An author who genuinely wants zero should write it as a
        # number, not derive it from nothing.
        raise DerivationError(f"derivation {derivation.label!r} has no source values")

    try:
        return operation(args)
    except DerivationError as exc:
        raise DerivationError(f"derivation {derivation.label!r}: {exc}") from exc


def date_hull(facts: Sequence[Fact]) -> tuple[str | None, str | None]:
    """Return the earliest start and latest end among the cited facts' dates.

    Dates are compared as plain ISO strings (`"2024-06"`, `"2024"`), which
    is only sound because ISO 8601's field order makes lexicographic and
    chronological order agree for two dates at the *same* precision. Mixed
    precisions still compare, but not always the way a reader would
    expect: a year-only date (`"2024"`) is a string-prefix of any
    same-year month-precision date (`"2024-06"`), so it always sorts
    *before* it. That makes a coarse year-only `start` win the minimum
    comparison — a conservative, defensible outcome — but it means a
    coarse year-only `end` can *never* win the maximum comparison against
    a same-year month-precision `end`, even when the true, unknown month
    behind that year-only date is actually later.

    A fact whose own `end` precedes its own `start` is not detected or
    corrected here, nor anywhere else in this codebase — `Dates` does not
    enforce that invariant either. This function only takes the min of
    every start and the max of every end it is handed, whatever those
    values are; a caller must not assume a fact's own dates arrive in order.
    """
    starts = [fact.dates.start for fact in facts if fact.dates and fact.dates.start]
    ends = [fact.dates.end for fact in facts if fact.dates and fact.dates.end]
    return (min(starts) if starts else None, max(ends) if ends else None)
