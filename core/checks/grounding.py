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
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from core.aliases import ALIAS_TABLE, canonicalize
from core.checks.derive import DerivationError, compute, date_hull
from core.checks.numbers import parse_numbers, values_match
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


class Verdict(StrEnum):
    """The outcome of grounding a bullet (or a whole document) against its facts."""

    PASS = "pass"
    DERIVED_VERIFIED = "derived_verified"
    ALIAS_MATCH = "alias_match"
    FAIL = "fail"


@dataclass(frozen=True)
class Finding:
    """A single reason a bullet failed to ground, or a note on how it passed."""

    bullet_index: int
    kind: str
    detail: str


def _iso_month_dates(text: str) -> list[str]:
    """Extract "Mon YYYY" style mentions as ISO month strings ("2024-06")."""
    return [f"{year}-{_MONTHS[month.lower()[:3]]:02d}" for month, year in _YEAR_MONTH.findall(text)]


def _looks_like_year(value: float) -> bool:
    return _YEAR_MIN <= value <= _YEAR_MAX and value.is_integer()


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
        return Verdict.FAIL, [Finding(index, "missing_source", "bullet cites no fact")]

    unselectable = [f.id for f in sources if not f.selectable()]
    if unselectable:
        return Verdict.FAIL, [
            Finding(index, "unselectable_fact", f"facts not confirmed by the user: {unselectable}")
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
                findings.append(
                    Finding(
                        index,
                        "unresolved_number",
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
        if _looks_like_year(number.value) and _year_within_hull(number.value, start, end):
            continue
        findings.append(
            Finding(
                index, "unresolved_number", f"{number.raw!r} is not derivable from the cited facts"
            )
        )

    cited_entities = {e for f in sources for e in f.entities}
    for token in _checkable_entity_tokens(text):
        canonical = canonicalize(token)
        if canonical not in cited_entities:
            findings.append(
                Finding(
                    index,
                    "unknown_entity",
                    f"{token!r} (canonical form {canonical!r}) appears in no cited fact",
                )
            )
            continue
        if canonical != token:
            used_alias = True

    for iso in _iso_month_dates(text):
        if (start is not None and iso < start) or (end is not None and iso > end):
            findings.append(
                Finding(index, "unresolved_date", f"{iso} falls outside {start}..{end}")
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
