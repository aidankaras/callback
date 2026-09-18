"""The grounding gate.

A bullet is admissible only when every number, entity and date it states is
*derivable* from the facts it cites: present literally (after the numeric
normalisation `core.checks.numbers` already does), recomputable from a
declared derivation, a known alias of a cited entity, or inside the span of
the cited dates. Containment — "does this string appear in a fact?" — is not
enough: a resume routinely restates a stored figure in rounded or aggregated
form ("1.2M records" for a stored `1_200_000`), computes a percentage or a
headcount the facts only imply, or refers to a technology by its common
alias. A containment check rejects all of those, and a gate that refuses
truthful text teaches the person using it that the tool is broken — which is
worse than the gate occasionally missing a genuine fabrication.

Entity checking is deliberately narrow, and this is a real limitation, not
an oversight: a token in the text is only checked against the cited facts'
entities when it is either a known technology (matched against the alias
table case-insensitively) or "identifier-shaped" — it contains a digit, `+`,
`#` or `.`, or is 2-6 letters that are all capitals — *and* starts with a
capital letter. Every other capitalised word (a company name, a project
title, an ordinary noun) is ignored outright, because a looser rule that
also flags plain capitalised words produces false positives on ordinary
prose (a percentile like "p99" would be one, if the capital-letter
requirement above were dropped) that this gate cannot tell apart from a
fabricated one. An invented tool name that happens to be neither a known
alias nor identifier-shaped — a plausible-looking but nonexistent product
name in title case, say — passes through unchecked. Catching that class of
fabrication is left to later review, not to this gate.

A hyphenated compound ("S3-backed") is checked one hyphen-split component
at a time when the whole token isn't itself a table entry, because `-` has
to stay part of a token for entries like "scikit-learn" to match as a
whole. Every component that is itself checkable must be cited — accepting
on any single component's match would let a hyphen hide an uncited
technology sitting next to a cited one ("S3-H100" passing on "S3" alone
even though "H100" is not cited, when the space-separated form of the same
claim would correctly fail). A component that is not itself checkable
("backed", "Redshift") is ignored, exactly as it would be as a standalone
word — this is the same entity-detection limitation above, not a separate
hole that hyphens introduce.

Number matching is by value, against the union of every cited fact's
numbers and derivations — not by which occurrence in the text a value came
from. A bullet with two independent claims that happen to share a value
("cut errors 42% while onboarding 42 new accounts") grounds both from a
single stored `42`, even if only one of the two was ever meant to cite it.
Per-occurrence linkage between a specific span of text and a specific fact
is out of scope here.

Entities are canonicalised on this module's side of the comparison too
(`canonicalize(e) for e in f.entities`), even though `Fact.entities` is
documented as already canonical at write time — a defensive redundancy,
not a second source of truth, so a `Fact` built without going through the
normal write path still compares correctly.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from core.aliases import ALIAS_TABLE, canonicalize
from core.checks.derive import DerivationError, compute, date_hull
from core.checks.numbers import parse_numbers, values_match

if TYPE_CHECKING:
    # Only for the `check_document_gate` return annotation below. Not a
    # module-level runtime import: `core.checks.result` imports `Verdict`
    # and `Finding` from this module, so importing it back at load time
    # here would be a cycle. `check_document_gate` does the real import
    # locally, at call time, once both modules have already finished
    # loading.
    from core.checks.result import GateResult
from core.profile.models import Fact

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}
_YEAR_MONTH = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{4})\b", re.IGNORECASE
)

# Candidate entity tokens: any letter-led run of word characters plus the
# punctuation an identifier can legitimately contain ("C++", "3.12").
_TOKEN = re.compile(r"[A-Za-z][\w+#.-]*")

# A token is "identifier-shaped" when it contains a digit, '+', '#' or '.'
# and starts with a capital letter ("S3", "C++"), or is 2-6 letters that are
# all capitals ("GREET"). The capital-letter requirement on the first branch
# is load-bearing: without it, a lowercase percentile like "p99" would match
# (it contains a digit) and get flagged as an unknown entity, failing a
# truthful latency bullet that never claimed p99 was a tool.
_SHAPED = re.compile(r"^(?=.*[\d+#.])[A-Z][\w+#.-]*$|^[A-Z]{2,6}$")

# A bare year is treated as plausibly date-shaped only in this range; outside
# it, digits are almost certainly a quantity rather than a calendar year.
_YEAR_MIN, _YEAR_MAX = 1900.0, 2100.0

# Falling in [_YEAR_MIN, _YEAR_MAX] is necessary but not sufficient for a bare
# number to be read as a year: "Cut the $2020 licensing cost", "Reduced spend
# by 2020 dollars" and "Handled 2020 tickets" are all ordinary quantities
# that happen to fall in that range, not years, and must still be sourced
# like any other number. What decides it is the text immediately around the
# digits, checked in plain Python rather than folded into one regex.
#
# A single combined pattern was tried and rejected three times before this
# one: matching the surrounding words in the same regex as the digits
# *consumes* those words, so "from 2018 to 2024" only ever yields a match for
# "2018" — the engine can't also start a second match at "2024" once "to" has
# been eaten by the first. And a bare `to` preposition is not safe to add to
# fix that, because "to" is also the ordinary preposition in "improved
# throughput to 1999 requests per second", where treating "to <year-shaped
# number>" as a range would wrongly exempt the 1999 from being sourced. `to`
# is therefore only ever recognised as part of an explicit range already
# anchored by a year on the other side (`_RANGE_BEFORE`/`_RANGE_AFTER`), never
# as a standalone preposition (contrast `_PREP_BEFORE`, which excludes it).
_PREP_BEFORE = re.compile(r"(?:from|since|until|through|thru|in|during)\s+$", re.IGNORECASE)
_MONTH_BEFORE = re.compile(
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+$", re.IGNORECASE
)
# The en dash literal in each pattern is a real range separator a resume may
# use (e.g. two years written with a spaced en dash), not a stand-in for a
# hyphen ruff mistook it for.
_RANGE_AFTER_RE = r"^\s*(?:-|–|—|to|through)\s*(?:19|20)\d{2}\b"  # noqa: RUF001
_RANGE_BEFORE_RE = r"(?:19|20)\d{2}\s*(?:-|–|—|to|through)\s*$"  # noqa: RUF001
_RANGE_AFTER = re.compile(_RANGE_AFTER_RE, re.IGNORECASE)
_RANGE_BEFORE = re.compile(_RANGE_BEFORE_RE, re.IGNORECASE)


class Verdict(StrEnum):
    """The outcome of grounding a bullet (or a whole document) against its facts."""

    PASS = "pass"
    DERIVED_VERIFIED = "derived_verified"
    ALIAS_MATCH = "alias_match"
    FAIL = "fail"


class FindingKind(StrEnum):
    """Every reason `check_bullet` can attach to a `Finding`.

    A `StrEnum` member compares equal to its string form, so existing call
    sites and tests that compare `finding.kind == "unresolved_number"`
    continue to work unchanged against a member of this enum.
    """

    MISSING_SOURCE = "missing_source"
    UNSELECTABLE_FACT = "unselectable_fact"
    INVALID_DERIVATION = "invalid_derivation"
    UNRESOLVED_NUMBER = "unresolved_number"
    UNKNOWN_ENTITY = "unknown_entity"
    UNRESOLVED_DATE = "unresolved_date"


@dataclass(frozen=True)
class Finding:
    """A single reason a bullet failed to ground, or a note on how it passed."""

    bullet_index: int
    kind: FindingKind
    detail: str


def _iso_month_dates(text: str) -> list[str]:
    """Extract "Mon YYYY" style mentions as ISO month strings ("2024-06")."""
    return [f"{year}-{_MONTHS[month.lower()[:3]]:02d}" for month, year in _YEAR_MONTH.findall(text)]


def _looks_like_year(value: float) -> bool:
    return _YEAR_MIN <= value <= _YEAR_MAX and value.is_integer()


def _year_context(text: str, span: tuple[int, int]) -> bool:
    """True when the text surrounding `span` marks the number there as a year.

    Checked against the text before the number (a date preposition or month
    name immediately preceding it, or a year-dash/`to`/`through` immediately
    preceding it) or the text after it (a dash/`to`/`through` immediately
    followed by another year). See the module-level comment above
    `_PREP_BEFORE` for why this can't be one regex over the whole span.
    """
    before, after = text[: span[0]], text[span[1] :]
    return bool(
        _PREP_BEFORE.search(before)
        or _MONTH_BEFORE.search(before)
        or _RANGE_BEFORE.search(before)
        or _RANGE_AFTER.match(after)
    )


def _year_within_hull(value: float, start: str | None, end: str | None) -> bool:
    """Compare a bare year against the hull as integers, not ISO strings.

    `date_hull` compares ISO strings lexicographically, which makes a
    year-only date unable to win a max comparison against a same-year
    month-precision date. Comparing the bare year as an integer against the
    year components of the hull sidesteps that precision mismatch entirely.
    """
    if start is None or end is None:
        return False
    year = int(value)
    return int(start[:4]) <= year <= int(end[:4])


def _checkable_entity_tokens(text: str) -> list[str]:
    """Every token worth checking against the cited entities — see module docstring."""
    return [t for t in _TOKEN.findall(text) if t.lower() in ALIAS_TABLE or _SHAPED.match(t)]


def check_bullet(
    text: str, sources: Sequence[Fact], *, index: int = 0
) -> tuple[Verdict, list[Finding]]:
    """Ground a single bullet's numbers, entities and dates against `sources`.

    Returns `Verdict.FAIL` with at least one `Finding` for any unresolved
    claim. Otherwise returns the strongest verdict that explains how the
    bullet passed: `DERIVED_VERIFIED` if any number needed a recomputed
    derivation, else `ALIAS_MATCH` if any entity needed alias resolution,
    else plain `PASS`.
    """
    if not sources:
        return Verdict.FAIL, [Finding(index, FindingKind.MISSING_SOURCE, "bullet cites no fact")]

    unselectable = [f.id for f in sources if not f.selectable()]
    if unselectable:
        return Verdict.FAIL, [
            Finding(
                index,
                FindingKind.UNSELECTABLE_FACT,
                f"facts not confirmed by the user: {unselectable}",
            )
        ]

    findings: list[Finding] = []
    used_derivation = False
    used_alias = False

    literal_values = {q.value for f in sources for q in f.numbers.values()}

    derived_values: set[float] = set()
    for f in sources:
        for derivation in f.derived:
            try:
                derived_values.add(compute(derivation, sources))
            except DerivationError as exc:
                # A broken derivation names a defect in the profile's own
                # data, not an unresolved number in the bullet's text, so it
                # gets a finding kind of its own rather than being folded
                # into `unresolved_number`.
                findings.append(
                    Finding(
                        index,
                        FindingKind.INVALID_DERIVATION,
                        f"derivation {derivation.label!r} could not be recomputed: {exc}",
                    )
                )

    start, end = date_hull(sources)

    for number in parse_numbers(text):
        if any(values_match(number.value, source) for source in literal_values):
            continue
        # A percentage is compared against the *magnitude* of a derived
        # value: the sign of a `pct_change` derivation only reflects the
        # order its two inputs were cited in, not whether the underlying
        # change was an increase or a reduction, so comparing signed values
        # would reject a truthful "increased X 50%" bullet whenever the
        # derivation happened to be phrased as before/after.
        derived_pool = (
            (abs(v) for v in derived_values) if number.is_percent else iter(derived_values)
        )
        if any(values_match(number.value, source) for source in derived_pool):
            used_derivation = True
            continue
        if (
            _looks_like_year(number.value)
            and _year_context(text, number.span)
            and _year_within_hull(number.value, start, end)
        ):
            continue
        findings.append(
            Finding(
                index,
                FindingKind.UNRESOLVED_NUMBER,
                f"{number.raw!r} is not derivable from the cited facts",
            )
        )

    cited_entities = {canonicalize(e) for f in sources for e in f.entities}
    for token in _checkable_entity_tokens(text):
        canonical = canonicalize(token)
        if canonical in cited_entities:
            if canonical != token:
                used_alias = True
            continue
        # The token as a whole doesn't match, but it may be a hyphenated
        # compound built around a cited entity ("S3-backed"). `_TOKEN`
        # deliberately keeps '-' as part of a token (needed intact for
        # table entries like "scikit-learn", tried above as a whole), so a
        # compound is only recovered here by splitting on '-'.
        #
        # Every *checkable* component must match — not just one of them.
        # Accepting on any single component match would let a hyphen hide
        # an uncited technology sitting next to a cited one: "S3-H100"
        # would pass on "S3" alone even though "H100" (identifier-shaped
        # in its own right) is not cited, while the same claim written with
        # a space ("S3 H100") is correctly rejected. A component that is
        # not itself checkable ("backed", "Redshift" — no digit, no
        # all-caps shape, no alias-table entry) is ignored, exactly as it
        # would be as a standalone word: "S3-Redshift" passes on "S3" alone
        # for the same reason "S3 Redshift" does, which is the documented
        # entity-detection limitation, not a hole specific to hyphens.
        if "-" in token:
            parts = [p for p in token.split("-") if p]
            checkable_parts = [p for p in parts if p.lower() in ALIAS_TABLE or _SHAPED.match(p)]
            if checkable_parts:
                missing = [p for p in checkable_parts if canonicalize(p) not in cited_entities]
                if not missing:
                    if any(canonicalize(p) != p for p in checkable_parts):
                        used_alias = True
                    continue
                for part in missing:
                    findings.append(
                        Finding(
                            index,
                            FindingKind.UNKNOWN_ENTITY,
                            f"{part!r} (component of {token!r}) appears in no cited fact",
                        )
                    )
                continue
            # No component of the hyphenated token is itself checkable
            # (e.g. "scikit-learn" split into "scikit" and "learn", neither
            # a known technology nor identifier-shaped on its own) — there
            # is no compound to recover, so this falls through to the
            # whole-token miss below.

        findings.append(
            Finding(
                index,
                FindingKind.UNKNOWN_ENTITY,
                f"{token!r} (canonical form {canonical!r}) appears in no cited fact",
            )
        )

    for iso in _iso_month_dates(text):
        if (start is not None and iso < start) or (end is not None and iso > end):
            findings.append(
                Finding(index, FindingKind.UNRESOLVED_DATE, f"{iso} falls outside {start}..{end}")
            )

    if findings:
        return Verdict.FAIL, findings
    if used_derivation:
        return Verdict.DERIVED_VERIFIED, []
    if used_alias:
        return Verdict.ALIAS_MATCH, []
    return Verdict.PASS, []


def check_document(
    bullets: Sequence[tuple[str, Sequence[Fact]]],
) -> tuple[Verdict, list[Finding]]:
    """Ground every bullet in a document, indexing each finding to its bullet.

    Fails the whole document if any bullet fails; otherwise reports the
    strongest verdict seen across all bullets (derived, then alias, then
    plain pass), so a document composed entirely of literal restatements is
    distinguished from one that leaned on recomputed derivations or aliases.
    """
    all_findings: list[Finding] = []
    verdicts: list[Verdict] = []
    for i, (text, sources) in enumerate(bullets):
        verdict, findings = check_bullet(text, sources, index=i)
        verdicts.append(verdict)
        all_findings.extend(findings)

    if Verdict.FAIL in verdicts:
        return Verdict.FAIL, all_findings
    if Verdict.DERIVED_VERIFIED in verdicts:
        return Verdict.DERIVED_VERIFIED, []
    if Verdict.ALIAS_MATCH in verdicts:
        return Verdict.ALIAS_MATCH, []
    return Verdict.PASS, []


def check_document_gate(bullets: Sequence[tuple[str, Sequence[Fact]]]) -> GateResult:
    """Ground a whole document and return the result in the shared gate shape.

    `check_document`'s tuple return predates `GateResult` and is load-bearing
    for tests already written against it, so it stays exactly as it is and
    this wraps it, rather than the reverse — adapting its call sites would
    have meant touching every existing test for no behavioural change.

    The import is local rather than module-level to avoid a load-time cycle:
    `core.checks.result` imports `Verdict` and `Finding` from this module, so
    this module cannot also import `GateResult` from `core.checks.result` at
    the top level. By the time this function is actually called, whichever
    of the two modules loads first will have already finished loading the
    other, so the import here always succeeds.
    """
    from core.checks.result import GateResult

    verdict, findings = check_document(bullets)
    return GateResult(gate="grounding", verdict=verdict, findings=findings)
