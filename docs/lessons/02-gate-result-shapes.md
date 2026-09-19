# 02 — Why the gates do not share a result type yet

**Decision.** `GateResult` exists and only the grounding gate returns one. The
round-trip and hidden-channel gates deliberately return their own shapes, and
unifying them is left to the code that first has to aggregate several gates.

## The state a reader will find

Three gates, three return types:

| Gate | Returns |
|---|---|
| `checks/grounding.py` | `GateResult(gate, verdict, findings: list[Finding])` |
| `checks/roundtrip.py` | `RoundTripResult(critical, char_ratio, order_ok, out_of_order, verdict, findings)` |
| `checks/hidden.py` | `tuple[str, list[HiddenFinding]]` |

That looks like drift, and `checks/result.py`'s own docstring argues against it:
a caller folding every gate's outcome into one document-level ruling wants one
shape, "rather than retrofitted once there are several call sites to update in
lockstep." The reasoning is sound and the conclusion was still wrong to act on
early.

## Why the shared type does not fit

`GateResult.findings` is typed `list[Finding]`, and `Finding` is
`(bullet_index: int, kind: FindingKind, detail: str)`. Both fields are specific
to grounding: `FindingKind`'s members are all reasons a *bullet* failed to
ground, and `bullet_index` addresses a bullet.

Neither the round-trip gate nor the hidden-channel gate has a bullet. Their
findings are document-level — a field a parser could not recover, a reading
order that does not survive, a glyph drawn in the page's own colour. Conforming
them to `GateResult` today would mean either a sentinel `bullet_index` of `-1`
on every document-level finding, or reordering `Finding`'s fields across its
seven construction sites, and in both cases inventing `FindingKind` members for
two gates while five more are unwritten.

There is a structural tell that the shared types are in the wrong place
already: `checks/result.py` imports `Finding` and `Verdict` **from**
`checks/grounding.py`, so `grounding.py` cannot import `GateResult` at module
level without a cycle. It imports it inside two function bodies instead, with a
comment explaining the workaround. A shared vocabulary that has to be imported
out of one of its consumers is not yet shared; it is one gate's vocabulary with
a second name.

## What was considered and rejected

- **Widen `Finding` now.** Rejected: it commits the vocabulary to a second
  guess. The first guess was made from one example and is already wrong for the
  second gate; a version made from two examples has no better claim on the
  remaining five.
- **Give each gate an adapter to `GateResult`.** Rejected for now: an adapter
  must decide what a document-level finding's `bullet_index` and `kind` are,
  which is the same decision, taken in a less visible place.
- **Move `Verdict`, `FindingKind` and `Finding` into `result.py`.** This is
  probably right eventually and is what removes the cycle, but it is a
  refactor across four test files and `grounding.py`, and folding it into a
  feature task would have made that task's review surface two unrelated things
  at once.

## When to revisit

At the point something aggregates gates into one document ruling. That code is
the first consumer with all the gates' real needs visible at once, and it is
where the cost of the current arrangement actually lands — until then, the
divergence costs nothing but looks untidy.

Two things to do then, not before: move the shared types out of
`grounding.py` so the cycle and its two local imports disappear, and decide
how a finding addresses its subject when the subject is a document rather than
a bullet.

## The question a reviewer asks

*Isn't a gate that ignores the project's own gate protocol just an
inconsistency?*

It would be, if the protocol fit and were being skipped for convenience. What
is actually being avoided is a false conformance: a `GateResult` whose
`bullet_index` is a sentinel and whose `kind` is a member invented to satisfy a
signature carries less information than the shape the gate already returns, and
it reads as conforming while telling a reader less.

## Implemented by

- `core/checks/result.py` — `GateResult`, and the docstring arguing for early
  introduction.
- `core/checks/grounding.py` — the only producer, plus the two local imports
  that mark the cycle.
- `core/checks/roundtrip.py`, `core/checks/hidden.py` — the two gates that
  return their own shapes.
