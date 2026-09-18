"""Number grammar for the grounding gate.

A regular expression over bare digits is not sufficient: `1,200,000`,
`1.2M` and `1.2 million` all denote the same quantity and must parse to
the same float, and a sentence-final period ("Raised AUC to 0.83.")
must not be misread as the start of a new decimal. Everything downstream
compares parsed values, never the original strings, so this module is
the only place either of those distinctions gets made.

`values_match` compares two already-parsed values with a relative
tolerance rather than exact equality, because a generated claim is
usually a rounded restatement of a stored figure (`0.8312` reported as
"0.83"), not a copy of it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAGNITUDES: dict[str, float] = {
    "k": 1e3,
    "thousand": 1e3,
    "m": 1e6,
    "million": 1e6,
    "mm": 1e6,
    "b": 1e9,
    "billion": 1e9,
}
CURRENCY: dict[str, str] = {"$": "USD", "£": "GBP", "€": "EUR"}

# Alternation is tried left to right, so a word must appear before any
# shorter alternative that is also a prefix of it: "million" and "mm" before
# bare "m", "billion" before bare "b". With the reverse order "1.2 million"
# would match only the leading "m" of "million", leaving "illion" dangling
# outside the match (the resolved value is unaffected here because "m" and
# "million" share a magnitude, but `raw` and `span` would be wrong).
_NUMBER = re.compile(
    r"(?P<currency>[$£€])?"
    r"(?P<digits>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
    r"\s*"
    r"(?P<suffix>thousand|million|billion|mm|k|m|b)?"
    r"(?P<percent>\s*%)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedNumber:
    """A single quantity found in text, with its resolved value and provenance."""

    raw: str
    value: float
    unit: str | None
    is_percent: bool
    span: tuple[int, int]


def parse_numbers(text: str) -> list[ParsedNumber]:
    """Find every quantity in `text` and resolve it to a float.

    Handles thousands separators (`1,200,000`), magnitude suffixes
    (`1.2M`, `1.2 million`), currency symbols (`$12k`) and percent signs.
    A number with no suffix is used as literal digits — this is what
    keeps a year like `2024` from being read as a magnitude it never
    carries, rather than any year-specific detection.
    """
    out: list[ParsedNumber] = []
    for match in _NUMBER.finditer(text):
        digits = match.group("digits").replace(",", "")
        value = float(digits)
        suffix = (match.group("suffix") or "").lower()
        if suffix:
            value *= MAGNITUDES[suffix]
        currency = match.group("currency")
        unit = CURRENCY.get(currency) if currency else None
        out.append(
            ParsedNumber(
                raw=match.group(0).strip(),
                value=value,
                unit=unit,
                is_percent=bool(match.group("percent")),
                span=match.span(),
            )
        )
    return out


def values_match(claimed: float, source: float, *, tolerance: float = 0.005) -> bool:
    """True when `claimed` is within a relative tolerance of `source`.

    The default (0.5%) admits ordinary rounding — `0.8312` reported as
    "0.83" — and rejects anything that overstates the underlying figure
    by more than that, so "40%" against a stored 44% still fails.
    Tolerance is symmetric: it also rejects understatement, not only
    inflation.
    """
    if source == 0:
        return abs(claimed) <= tolerance
    return abs(claimed - source) / abs(source) <= tolerance
